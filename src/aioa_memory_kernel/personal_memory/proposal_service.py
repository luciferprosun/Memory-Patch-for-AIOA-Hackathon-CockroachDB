"""Transactional, fail-closed Step 29 proposal validation service."""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import TYPE_CHECKING, Sequence

if TYPE_CHECKING:
    from aioa_memory_kernel.german_law.e2e import EvidenceBoundCorrectionContext

from aioa_memory_kernel.claims.models import (
    ClaimEvidenceAssessment,
    ClaimEvidenceLink,
)
from aioa_memory_kernel.contracts.enums import PatchState, PersonalMemorySpaceState
from aioa_memory_kernel.contracts.serialization import canonical_json, canonical_sha256
from aioa_memory_kernel.corrections.models import CorrectionPacketV1A
from aioa_memory_kernel.evidence.models import FrozenEvidenceBundle
from aioa_memory_kernel.persistence import (
    AccessMode,
    BeginOperation,
    IdempotencyService,
    RequestContext,
    SerializableTransactionRunner,
)
from aioa_memory_kernel.persistence.protocols import TransactionProtocol
from aioa_memory_kernel.temporal.models import (
    TemporalResolutionResult,
    verify_temporal_result_hash,
)
from aioa_memory_kernel.answers.models import VerifiedAnswer
from aioa_memory_kernel.verification.models import DraftV2PipelineResult

from .candidate_repository import CorrectionCandidateCockroachRepository
from .candidate_service import verify_correction_candidate_target_against_slot
from .models import PersonalMemoryHatSlot, enforce_step27_quota
from .proposal_repository import (
    PersonalMemoryPatchProposalCockroachRepository,
    ProposalPeer,
    proposal_transition_id,
)
from .proposals import (
    MAXIMUM_PROPOSAL_BYTES_PER_SLOT,
    MAXIMUM_PROPOSALS_PER_SLOT,
    AdvancePersonalMemoryPatchToAwaitingApproval,
    BindPersonalMemoryPatchEvidence,
    CreatePersonalMemoryPatchProposal,
    PersonalMemoryPatchProposal,
    PersonalMemoryPatchProposalState,
    PersonalMemoryPatchTransitionReceipt,
    PersonalMemoryPatchValidationError,
    PersonalMemoryPatchValidationReceipt,
    ProposalConflictResult,
    ProposalDedupResult,
    ProposalGateResult,
    Step29ReasonCode,
    ValidatePersonalMemoryPatchProposal,
    advance_personal_memory_patch_to_awaiting_approval,
    bind_personal_memory_patch_evidence,
    build_personal_memory_patch_evidence_binding,
    build_personal_memory_patch_proposal,
    build_personal_memory_patch_transition_receipt,
    build_personal_memory_patch_validation_receipt,
    load_personal_memory_patch_validation_policy,
    normalize_proposal_statement,
    proposal_conflict_between,
    proposal_conflict_subject,
    validate_personal_memory_patch,
    verify_personal_memory_patch_state,
)
from .repository import PersonalMemoryCockroachRepository


_CREATE_OPERATION = "PERSONAL_MEMORY_PATCH_PROPOSAL_CREATE"
_BIND_OPERATION = "PERSONAL_MEMORY_PATCH_EVIDENCE_BIND"
_VALIDATE_OPERATION = "PERSONAL_MEMORY_PATCH_VALIDATE"
_AWAIT_OPERATION = "PERSONAL_MEMORY_PATCH_AWAITING_APPROVAL"


def _context(tenant_id: str, owner_user_id: str) -> RequestContext:
    return RequestContext(
        tenant_id=tenant_id,
        user_id=owner_user_id,
        access_mode=AccessMode.USER_PRIVATE,
    )


def _scope_digest(
    *,
    tenant_id: str,
    owner_user_id: str,
    personal_memory_space_id: str,
    proposal_hash: str,
) -> str:
    return canonical_sha256(
        {
            "tenant_id": tenant_id,
            "owner_user_id": owner_user_id,
            "personal_memory_space_id": personal_memory_space_id,
            "proposal_hash": proposal_hash,
            "target_scope": "USER_PERSONAL_HAT",
        }
    )


def _operation_id(
    operation_kind: str,
    tenant_id: str,
    owner_user_id: str,
    idempotency_key: str,
) -> str:
    return "step29-operation-" + canonical_sha256(
        {
            "operation_kind": operation_kind,
            "tenant_id": tenant_id,
            "owner_user_id": owner_user_id,
            "idempotency_key": idempotency_key,
        }
    )


def _historical_state(
    current: PersonalMemoryPatchProposalState,
    target: PatchState,
) -> PersonalMemoryPatchProposalState:
    if target is PatchState.PROPOSED:
        return PersonalMemoryPatchProposalState(
            schema_version=current.schema_version,
            contract_type=current.contract_type,
            proposal=current.proposal,
            state=target,
            state_version=1,
            evidence_binding=None,
            validation_receipt=None,
            updated_at=current.proposal.created_at,
        )
    if current.evidence_binding is None:
        raise PersonalMemoryPatchValidationError(Step29ReasonCode.CONTENT_CONFLICT)
    if target is PatchState.EVIDENCE_BOUND:
        return PersonalMemoryPatchProposalState(
            schema_version=current.schema_version,
            contract_type=current.contract_type,
            proposal=current.proposal,
            state=target,
            state_version=2,
            evidence_binding=current.evidence_binding,
            validation_receipt=None,
            updated_at=current.evidence_binding.bound_at,
        )
    if current.validation_receipt is None:
        raise PersonalMemoryPatchValidationError(Step29ReasonCode.CONTENT_CONFLICT)
    if target is PatchState.VALIDATED:
        return PersonalMemoryPatchProposalState(
            schema_version=current.schema_version,
            contract_type=current.contract_type,
            proposal=current.proposal,
            state=target,
            state_version=3,
            evidence_binding=current.evidence_binding,
            validation_receipt=current.validation_receipt,
            updated_at=current.validation_receipt.validated_at,
        )
    if target is PatchState.AWAITING_APPROVAL and current.state is target:
        return current
    raise PersonalMemoryPatchValidationError(Step29ReasonCode.CONTENT_CONFLICT)


def _verify_command_state(
    command: BindPersonalMemoryPatchEvidence
    | ValidatePersonalMemoryPatchProposal
    | AdvancePersonalMemoryPatchToAwaitingApproval,
    state: PersonalMemoryPatchProposalState,
) -> None:
    proposal = state.proposal
    if (
        command.tenant_id != proposal.tenant_id
        or command.owner_user_id != proposal.owner_user_id
        or command.personal_memory_space_id != proposal.personal_memory_space_id
        or command.proposal_id != proposal.proposal_id
        or command.proposal_hash != proposal.proposal_hash
    ):
        raise PersonalMemoryPatchValidationError(
            Step29ReasonCode.OWNER_SCOPE_MISMATCH
        )
    if (
        command.expected_state is not state.state
        or command.expected_state_version != state.state_version
        or command.expected_state_hash != state.state_hash
    ):
        raise PersonalMemoryPatchValidationError(
            Step29ReasonCode.STATE_VERSION_CONFLICT
        )


def _verify_current_target(
    transaction: TransactionProtocol,
    *,
    state: PersonalMemoryPatchProposalState,
    slot_repository: PersonalMemoryCockroachRepository,
    candidate_repository: CorrectionCandidateCockroachRepository,
) -> PersonalMemoryHatSlot:
    proposal = state.proposal
    envelope = candidate_repository.get_candidate(
        transaction,
        proposal.tenant_id,
        proposal.owner_user_id,
        proposal.candidate_id,
    )
    if (
        envelope is None
        or envelope.envelope_hash != proposal.candidate_envelope_hash
        or envelope.submission.candidate.content_hash != proposal.candidate_hash
        or envelope.submission.target_slot_binding.target_binding_hash
        != proposal.target_binding_hash
    ):
        raise PersonalMemoryPatchValidationError(Step29ReasonCode.CONTENT_CONFLICT)
    slot = slot_repository.get_slot(
        transaction,
        proposal.tenant_id,
        proposal.owner_user_id,
        proposal.personal_memory_space_id,
    )
    if slot is None:
        raise PersonalMemoryPatchValidationError(Step29ReasonCode.TARGET_SLOT_INVALID)
    try:
        verify_correction_candidate_target_against_slot(envelope, slot)
    except Exception as exc:
        reason = (
            Step29ReasonCode.TARGET_SLOT_ARCHIVED
            if slot.state is PersonalMemorySpaceState.ARCHIVED
            else Step29ReasonCode.TARGET_SLOT_DELETE_PENDING
            if slot.state
            in {PersonalMemorySpaceState.DELETED_PENDING, PersonalMemorySpaceState.DELETED}
            else Step29ReasonCode.TARGET_SLOT_INVALID
        )
        raise PersonalMemoryPatchValidationError(reason) from exc
    return slot


def _quota_and_model_results(
    transaction: TransactionProtocol,
    *,
    slot: PersonalMemoryHatSlot,
    proposal: PersonalMemoryPatchProposal,
    slot_repository: PersonalMemoryCockroachRepository,
    proposal_repository: PersonalMemoryPatchProposalCockroachRepository,
) -> tuple[ProposalGateResult, ProposalGateResult]:
    policy = slot_repository.get_quota_policy(
        transaction,
        slot.tenant_id,
        slot.owner_user_id,
        slot.quota_policy_id,
    )
    if policy is None or policy.policy_digest != slot.quota_policy_digest:
        return ProposalGateResult.FAIL, ProposalGateResult.FAIL
    usage = slot_repository.owner_usage(
        transaction,
        tenant_id=slot.tenant_id,
        owner_user_id=slot.owner_user_id,
        personal_memory_space_id=slot.personal_memory_space_id,
        quota_policy_digest=slot.quota_policy_digest,
    )
    try:
        enforce_step27_quota(policy, usage)
    except Exception:
        return ProposalGateResult.FAIL, ProposalGateResult.PASS
    maximum_active = policy.limits.maximum_active_memory_patches
    quota_pass = (
        maximum_active is None
        or usage.usage.active_memory_patches < maximum_active
    )
    proposal_usage = proposal_repository.proposal_usage(
        transaction,
        slot.tenant_id,
        slot.owner_user_id,
        slot.personal_memory_space_id,
    )
    quota_pass = quota_pass and proposal_usage.within_hard_limit
    model_pass = any(
        item.enabled
        and item.binding_id == proposal.model_binding_id
        and item.binding_hash == proposal.model_binding_hash
        for item in slot.model_bindings
    )
    return (
        ProposalGateResult.PASS if quota_pass else ProposalGateResult.FAIL,
        ProposalGateResult.PASS if model_pass else ProposalGateResult.FAIL,
    )


def _peer_results(
    proposal: PersonalMemoryPatchProposal,
    peers: Sequence[ProposalPeer],
) -> tuple[ProposalDedupResult, ProposalConflictResult]:
    for peer in peers:
        if peer.exact_dedup_key == proposal.exact_dedup_key:
            return (
                ProposalDedupResult.EXISTING_PATCH_DUPLICATE
                if peer.lifecycle_state in {PatchState.COMMITTED, PatchState.ACTIVE}
                else ProposalDedupResult.EXACT_DUPLICATE,
                ProposalConflictResult.EXISTING_PATCH_CONFLICT,
            )
    for peer in peers:
        if peer.proposal is not None:
            conflict = proposal_conflict_between(proposal, peer.proposal)
        else:
            subject, negative = proposal_conflict_subject(peer.statement)
            conflict = (
                ProposalConflictResult.DIRECT_CONTRADICTION
                if hashlib.sha256(subject.encode("utf-8")).hexdigest()
                == proposal.conflict_subject_sha256
                and negative is not proposal.negative_polarity
                else ProposalConflictResult.PASS
            )
        if conflict is not ProposalConflictResult.PASS:
            return ProposalDedupResult.PASS, conflict
    return ProposalDedupResult.PASS, ProposalConflictResult.PASS


class PersonalMemoryPatchProposalService:
    """Own only DETECTED through AWAITING_APPROVAL, never Step 30."""

    def __init__(
        self,
        transaction_runner: SerializableTransactionRunner,
        *,
        slot_repository: PersonalMemoryCockroachRepository | None = None,
        candidate_repository: CorrectionCandidateCockroachRepository | None = None,
        proposal_repository: PersonalMemoryPatchProposalCockroachRepository | None = None,
        idempotency: IdempotencyService | None = None,
    ) -> None:
        if not isinstance(transaction_runner, SerializableTransactionRunner):
            raise TypeError("transaction_runner must be SerializableTransactionRunner")
        self._runner = transaction_runner
        self._slots = slot_repository or PersonalMemoryCockroachRepository()
        self._candidates = candidate_repository or CorrectionCandidateCockroachRepository()
        self._proposals = (
            proposal_repository or PersonalMemoryPatchProposalCockroachRepository()
        )
        self._idempotency = idempotency or IdempotencyService()

    def create_proposal(
        self,
        envelope,
        command: CreatePersonalMemoryPatchProposal,
    ) -> tuple[PersonalMemoryPatchProposalState, PersonalMemoryPatchTransitionReceipt]:
        proposed = build_personal_memory_patch_proposal(envelope, command)
        proposal = proposed.proposal
        operation_id = _operation_id(
            _CREATE_OPERATION,
            proposal.tenant_id,
            proposal.owner_user_id,
            command.idempotency_key,
        )

        def work(transaction: TransactionProtocol):
            claim = self._idempotency.begin_or_resume_operation(
                transaction,
                BeginOperation(
                    operation_id=operation_id,
                    tenant_id=proposal.tenant_id,
                    owner_user_id=proposal.owner_user_id,
                    operation_kind=_CREATE_OPERATION,
                    idempotency_key=command.idempotency_key,
                    request_digest=command.command_hash,
                    scope_digest=_scope_digest(
                        tenant_id=proposal.tenant_id,
                        owner_user_id=proposal.owner_user_id,
                        personal_memory_space_id=proposal.personal_memory_space_id,
                        proposal_hash=proposal.proposal_hash,
                    ),
                    created_at=command.requested_at,
                ),
            )
            if not claim.may_proceed:
                current = self._proposals.get_proposal(
                    transaction,
                    proposal.tenant_id,
                    proposal.owner_user_id,
                    proposal.proposal_id,
                )
                if (
                    current is None
                    or current.proposal.proposal_hash != claim.operation.result_digest
                    or claim.operation.result_ref != proposal.proposal_id
                ):
                    raise PersonalMemoryPatchValidationError(
                        Step29ReasonCode.CONTENT_CONFLICT
                    )
                historical = _historical_state(current, PatchState.PROPOSED)
                transition_id = proposal_transition_id(
                    proposal, PatchState.DETECTED, PatchState.PROPOSED, 1
                )
                return current, build_personal_memory_patch_transition_receipt(
                    historical,
                    command_hash=command.command_hash,
                    state_before=PatchState.DETECTED,
                    transition_id=transition_id,
                    result_digest=proposal.proposal_hash,
                    idempotent_replay=True,
                    completed_at=command.requested_at,
                )
            stored_candidate = self._candidates.get_candidate(
                transaction,
                proposal.tenant_id,
                proposal.owner_user_id,
                proposal.candidate_id,
            )
            if stored_candidate is None or stored_candidate.envelope_hash != envelope.envelope_hash:
                raise PersonalMemoryPatchValidationError(Step29ReasonCode.CONTENT_CONFLICT)
            slot = self._slots.get_slot(
                transaction,
                proposal.tenant_id,
                proposal.owner_user_id,
                proposal.personal_memory_space_id,
            )
            if slot is None:
                raise PersonalMemoryPatchValidationError(
                    Step29ReasonCode.TARGET_SLOT_INVALID
                )
            try:
                verify_correction_candidate_target_against_slot(envelope, slot)
            except Exception as exc:
                raise PersonalMemoryPatchValidationError(
                    Step29ReasonCode.TARGET_SLOT_INVALID
                ) from exc
            usage = self._proposals.proposal_usage(
                transaction,
                proposal.tenant_id,
                proposal.owner_user_id,
                proposal.personal_memory_space_id,
            )
            proposed_bytes = len(canonical_json(proposed).encode("utf-8"))
            if (
                usage.proposal_count >= MAXIMUM_PROPOSALS_PER_SLOT
                or usage.proposal_bytes + proposed_bytes
                > MAXIMUM_PROPOSAL_BYTES_PER_SLOT
            ):
                raise PersonalMemoryPatchValidationError(Step29ReasonCode.QUOTA_FAIL)
            stored, inserted = self._proposals.insert_proposal(transaction, proposed)
            if not inserted:
                raise PersonalMemoryPatchValidationError(
                    Step29ReasonCode.PROPOSAL_DUPLICATE
                )
            transition_id = self._proposals.insert_transition_event(
                transaction,
                proposal=proposal,
                state_before=PatchState.DETECTED,
                state_after=PatchState.PROPOSED,
                state_version=1,
                transitioned_at=command.requested_at,
            )
            self._idempotency.complete_operation(
                transaction,
                tenant_id=proposal.tenant_id,
                operation_id=operation_id,
                expected_attempt_count=claim.operation.attempt_count,
                result_ref=proposal.proposal_id,
                result_digest=proposal.proposal_hash,
            )
            receipt = build_personal_memory_patch_transition_receipt(
                stored,
                command_hash=command.command_hash,
                state_before=PatchState.DETECTED,
                transition_id=transition_id,
                result_digest=proposal.proposal_hash,
                idempotent_replay=False,
                completed_at=command.requested_at,
            )
            return stored, receipt

        return self._runner.run(
            _context(proposal.tenant_id, proposal.owner_user_id),
            work,
            operation_kind=_CREATE_OPERATION,
        )

    def bind_evidence(
        self,
        command: BindPersonalMemoryPatchEvidence,
        *,
        bundles: Sequence[FrozenEvidenceBundle],
        temporal_result: TemporalResolutionResult,
        claim_links: Sequence[ClaimEvidenceLink],
        claim_assessments: Sequence[ClaimEvidenceAssessment],
        correction_packet: CorrectionPacketV1A,
        verified_answer: VerifiedAnswer,
        step25_result: DraftV2PipelineResult | None = None,
        evidence_context: EvidenceBoundCorrectionContext | None = None,
    ) -> tuple[PersonalMemoryPatchProposalState, PersonalMemoryPatchTransitionReceipt]:
        operation_id = _operation_id(
            _BIND_OPERATION,
            command.tenant_id,
            command.owner_user_id,
            command.idempotency_key,
        )

        def work(transaction: TransactionProtocol):
            current = self._proposals.get_proposal(
                transaction,
                command.tenant_id,
                command.owner_user_id,
                command.proposal_id,
            )
            if current is None:
                raise PersonalMemoryPatchValidationError(
                    Step29ReasonCode.CONTENT_CONFLICT
                )
            claim = self._idempotency.begin_or_resume_operation(
                transaction,
                BeginOperation(
                    operation_id=operation_id,
                    tenant_id=command.tenant_id,
                    owner_user_id=command.owner_user_id,
                    operation_kind=_BIND_OPERATION,
                    idempotency_key=command.idempotency_key,
                    request_digest=command.command_hash,
                    scope_digest=_scope_digest(
                        tenant_id=command.tenant_id,
                        owner_user_id=command.owner_user_id,
                        personal_memory_space_id=command.personal_memory_space_id,
                        proposal_hash=command.proposal_hash,
                    ),
                    created_at=command.requested_at,
                ),
            )
            if not claim.may_proceed:
                historical = _historical_state(current, PatchState.EVIDENCE_BOUND)
                if (
                    claim.operation.result_ref != command.proposal_id
                    or historical.evidence_binding.binding_hash
                    != claim.operation.result_digest
                ):
                    raise PersonalMemoryPatchValidationError(
                        Step29ReasonCode.CONTENT_CONFLICT
                    )
                transition_id = proposal_transition_id(
                    current.proposal,
                    PatchState.PROPOSED,
                    PatchState.EVIDENCE_BOUND,
                    2,
                )
                return current, build_personal_memory_patch_transition_receipt(
                    historical,
                    command_hash=command.command_hash,
                    state_before=PatchState.PROPOSED,
                    transition_id=transition_id,
                    result_digest=historical.evidence_binding.binding_hash,
                    idempotent_replay=True,
                    completed_at=command.requested_at,
                )
            _verify_command_state(command, current)
            _verify_current_target(
                transaction,
                state=current,
                slot_repository=self._slots,
                candidate_repository=self._candidates,
            )
            binding = build_personal_memory_patch_evidence_binding(
                current,
                bundles=bundles,
                temporal_result=temporal_result,
                claim_links=claim_links,
                claim_assessments=claim_assessments,
                correction_packet=correction_packet,
                verified_answer=verified_answer,
                step25_result=step25_result,
                evidence_context=evidence_context,
                bound_at=command.requested_at,
            )
            updated = bind_personal_memory_patch_evidence(
                current, binding, transitioned_at=command.requested_at
            )
            stored = self._proposals.transition_proposal(
                transaction, expected=current, updated=updated
            )
            transition_id = self._proposals.insert_transition_event(
                transaction,
                proposal=stored.proposal,
                state_before=PatchState.PROPOSED,
                state_after=PatchState.EVIDENCE_BOUND,
                state_version=2,
                transitioned_at=command.requested_at,
            )
            self._idempotency.complete_operation(
                transaction,
                tenant_id=command.tenant_id,
                operation_id=operation_id,
                expected_attempt_count=claim.operation.attempt_count,
                result_ref=command.proposal_id,
                result_digest=binding.binding_hash,
            )
            return stored, build_personal_memory_patch_transition_receipt(
                stored,
                command_hash=command.command_hash,
                state_before=PatchState.PROPOSED,
                transition_id=transition_id,
                result_digest=binding.binding_hash,
                idempotent_replay=False,
                completed_at=command.requested_at,
            )

        return self._runner.run(
            _context(command.tenant_id, command.owner_user_id),
            work,
            operation_kind=_BIND_OPERATION,
        )

    def validate_proposal(
        self,
        command: ValidatePersonalMemoryPatchProposal,
        *,
        bundles: Sequence[FrozenEvidenceBundle],
        temporal_result: TemporalResolutionResult,
        claim_links: Sequence[ClaimEvidenceLink],
        claim_assessments: Sequence[ClaimEvidenceAssessment],
        correction_packet: CorrectionPacketV1A,
        verified_answer: VerifiedAnswer,
        step25_result: DraftV2PipelineResult | None = None,
        evidence_context: EvidenceBoundCorrectionContext | None = None,
    ) -> tuple[
        PersonalMemoryPatchProposalState,
        PersonalMemoryPatchValidationReceipt,
        PersonalMemoryPatchTransitionReceipt | None,
    ]:
        verify_temporal_result_hash(temporal_result)
        operation_id = _operation_id(
            _VALIDATE_OPERATION,
            command.tenant_id,
            command.owner_user_id,
            command.idempotency_key,
        )

        def work(transaction: TransactionProtocol):
            current = self._proposals.get_proposal(
                transaction,
                command.tenant_id,
                command.owner_user_id,
                command.proposal_id,
            )
            if current is None:
                raise PersonalMemoryPatchValidationError(
                    Step29ReasonCode.CONTENT_CONFLICT
                )
            claim = self._idempotency.begin_or_resume_operation(
                transaction,
                BeginOperation(
                    operation_id=operation_id,
                    tenant_id=command.tenant_id,
                    owner_user_id=command.owner_user_id,
                    operation_kind=_VALIDATE_OPERATION,
                    idempotency_key=command.idempotency_key,
                    request_digest=command.command_hash,
                    scope_digest=_scope_digest(
                        tenant_id=command.tenant_id,
                        owner_user_id=command.owner_user_id,
                        personal_memory_space_id=command.personal_memory_space_id,
                        proposal_hash=command.proposal_hash,
                    ),
                    created_at=command.requested_at,
                ),
            )
            base = _historical_state(current, PatchState.EVIDENCE_BOUND)
            if claim.may_proceed:
                _verify_command_state(command, current)
            slot = _verify_current_target(
                transaction,
                state=base,
                slot_repository=self._slots,
                candidate_repository=self._candidates,
            )
            rebuilt = build_personal_memory_patch_evidence_binding(
                _historical_state(current, PatchState.PROPOSED),
                bundles=bundles,
                temporal_result=temporal_result,
                claim_links=claim_links,
                claim_assessments=claim_assessments,
                correction_packet=correction_packet,
                verified_answer=verified_answer,
                step25_result=step25_result,
                evidence_context=evidence_context,
                bound_at=base.evidence_binding.bound_at,
            )
            if rebuilt.binding_hash != base.evidence_binding.binding_hash:
                raise PersonalMemoryPatchValidationError(
                    Step29ReasonCode.EVIDENCE_INVALID
                )
            peers = self._proposals.list_peers(
                transaction,
                tenant_id=base.proposal.tenant_id,
                owner_user_id=base.proposal.owner_user_id,
                personal_memory_space_id=base.proposal.personal_memory_space_id,
                exclude_proposal_id=base.proposal.proposal_id,
            )
            dedup, conflict = _peer_results(base.proposal, peers)
            if base.evidence_binding.prohibited_claim_hashes:
                conflict = ProposalConflictResult.CANONICAL_EVIDENCE_CONFLICT
            elif base.evidence_binding.conflict_group_hashes:
                conflict = ProposalConflictResult.TEMPORAL_CONFLICT
            quota, model = _quota_and_model_results(
                transaction,
                slot=slot,
                proposal=base.proposal,
                slot_repository=self._slots,
                proposal_repository=self._proposals,
            )
            receipt = build_personal_memory_patch_validation_receipt(
                base,
                dedup_result=dedup,
                conflict_result=conflict,
                temporal_trusted_now=temporal_result.trusted_now,
                owner_scope_result=ProposalGateResult.PASS,
                slot_state_result=ProposalGateResult.PASS,
                quota_result=quota,
                model_binding_result=model,
                validated_at=command.requested_at,
            )
            if not claim.may_proceed:
                if (
                    claim.operation.result_ref != command.proposal_id
                    or claim.operation.result_digest != receipt.receipt_hash
                ):
                    raise PersonalMemoryPatchValidationError(
                        Step29ReasonCode.CONTENT_CONFLICT
                    )
                if receipt.validated:
                    historical = _historical_state(current, PatchState.VALIDATED)
                    transition_id = proposal_transition_id(
                        current.proposal,
                        PatchState.EVIDENCE_BOUND,
                        PatchState.VALIDATED,
                        3,
                    )
                    transition_receipt = build_personal_memory_patch_transition_receipt(
                        historical,
                        command_hash=command.command_hash,
                        state_before=PatchState.EVIDENCE_BOUND,
                        transition_id=transition_id,
                        result_digest=receipt.receipt_hash,
                        idempotent_replay=True,
                        completed_at=command.requested_at,
                    )
                    return current, receipt, transition_receipt
                return current, receipt, None
            transition_receipt = None
            stored = current
            if receipt.validated:
                updated = validate_personal_memory_patch(
                    current, receipt, transitioned_at=command.requested_at
                )
                stored = self._proposals.transition_proposal(
                    transaction, expected=current, updated=updated
                )
                transition_id = self._proposals.insert_transition_event(
                    transaction,
                    proposal=stored.proposal,
                    state_before=PatchState.EVIDENCE_BOUND,
                    state_after=PatchState.VALIDATED,
                    state_version=3,
                    transitioned_at=command.requested_at,
                )
                transition_receipt = build_personal_memory_patch_transition_receipt(
                    stored,
                    command_hash=command.command_hash,
                    state_before=PatchState.EVIDENCE_BOUND,
                    transition_id=transition_id,
                    result_digest=receipt.receipt_hash,
                    idempotent_replay=False,
                    completed_at=command.requested_at,
                )
            self._idempotency.complete_operation(
                transaction,
                tenant_id=command.tenant_id,
                operation_id=operation_id,
                expected_attempt_count=claim.operation.attempt_count,
                result_ref=command.proposal_id,
                result_digest=receipt.receipt_hash,
            )
            return stored, receipt, transition_receipt

        return self._runner.run(
            _context(command.tenant_id, command.owner_user_id),
            work,
            operation_kind=_VALIDATE_OPERATION,
        )

    def advance_to_awaiting_approval(
        self,
        command: AdvancePersonalMemoryPatchToAwaitingApproval,
    ) -> tuple[PersonalMemoryPatchProposalState, PersonalMemoryPatchTransitionReceipt]:
        operation_id = _operation_id(
            _AWAIT_OPERATION,
            command.tenant_id,
            command.owner_user_id,
            command.idempotency_key,
        )

        def work(transaction: TransactionProtocol):
            current = self._proposals.get_proposal(
                transaction,
                command.tenant_id,
                command.owner_user_id,
                command.proposal_id,
            )
            if current is None:
                raise PersonalMemoryPatchValidationError(
                    Step29ReasonCode.CONTENT_CONFLICT
                )
            claim = self._idempotency.begin_or_resume_operation(
                transaction,
                BeginOperation(
                    operation_id=operation_id,
                    tenant_id=command.tenant_id,
                    owner_user_id=command.owner_user_id,
                    operation_kind=_AWAIT_OPERATION,
                    idempotency_key=command.idempotency_key,
                    request_digest=command.command_hash,
                    scope_digest=_scope_digest(
                        tenant_id=command.tenant_id,
                        owner_user_id=command.owner_user_id,
                        personal_memory_space_id=command.personal_memory_space_id,
                        proposal_hash=command.proposal_hash,
                    ),
                    created_at=command.requested_at,
                ),
            )
            if not claim.may_proceed:
                historical = _historical_state(current, PatchState.AWAITING_APPROVAL)
                if (
                    claim.operation.result_ref != command.proposal_id
                    or claim.operation.result_digest != historical.state_hash
                ):
                    raise PersonalMemoryPatchValidationError(
                        Step29ReasonCode.CONTENT_CONFLICT
                    )
                transition_id = proposal_transition_id(
                    current.proposal,
                    PatchState.VALIDATED,
                    PatchState.AWAITING_APPROVAL,
                    4,
                )
                return current, build_personal_memory_patch_transition_receipt(
                    historical,
                    command_hash=command.command_hash,
                    state_before=PatchState.VALIDATED,
                    transition_id=transition_id,
                    result_digest=historical.state_hash,
                    idempotent_replay=True,
                    completed_at=command.requested_at,
                )
            _verify_command_state(command, current)
            slot = _verify_current_target(
                transaction,
                state=current,
                slot_repository=self._slots,
                candidate_repository=self._candidates,
            )
            if (
                current.validation_receipt is None
                or current.validation_receipt.receipt_hash
                != command.validation_receipt_hash
                or not current.validation_receipt.validated
            ):
                raise PersonalMemoryPatchValidationError(
                    Step29ReasonCode.PROPOSAL_VALIDATION_FAILED
                )
            policy = load_personal_memory_patch_validation_policy()
            if (
                command.requested_at - current.validation_receipt.validated_at
            ).total_seconds() > policy.maximum_temporal_revalidation_age_seconds:
                raise PersonalMemoryPatchValidationError(Step29ReasonCode.EVIDENCE_STALE)
            peers = self._proposals.list_peers(
                transaction,
                tenant_id=current.proposal.tenant_id,
                owner_user_id=current.proposal.owner_user_id,
                personal_memory_space_id=current.proposal.personal_memory_space_id,
                exclude_proposal_id=current.proposal.proposal_id,
            )
            dedup, conflict = _peer_results(current.proposal, peers)
            quota, model = _quota_and_model_results(
                transaction,
                slot=slot,
                proposal=current.proposal,
                slot_repository=self._slots,
                proposal_repository=self._proposals,
            )
            if (
                dedup is not ProposalDedupResult.PASS
                or conflict is not ProposalConflictResult.PASS
                or quota is not ProposalGateResult.PASS
                or model is not ProposalGateResult.PASS
            ):
                raise PersonalMemoryPatchValidationError(
                    Step29ReasonCode.PROPOSAL_VALIDATION_FAILED
                )
            updated = advance_personal_memory_patch_to_awaiting_approval(
                current,
                validation_receipt_hash=command.validation_receipt_hash,
                transitioned_at=command.requested_at,
            )
            stored = self._proposals.transition_proposal(
                transaction, expected=current, updated=updated
            )
            transition_id = self._proposals.insert_transition_event(
                transaction,
                proposal=stored.proposal,
                state_before=PatchState.VALIDATED,
                state_after=PatchState.AWAITING_APPROVAL,
                state_version=4,
                transitioned_at=command.requested_at,
            )
            self._idempotency.complete_operation(
                transaction,
                tenant_id=command.tenant_id,
                operation_id=operation_id,
                expected_attempt_count=claim.operation.attempt_count,
                result_ref=command.proposal_id,
                result_digest=stored.state_hash,
            )
            return stored, build_personal_memory_patch_transition_receipt(
                stored,
                command_hash=command.command_hash,
                state_before=PatchState.VALIDATED,
                transition_id=transition_id,
                result_digest=stored.state_hash,
                idempotent_replay=False,
                completed_at=command.requested_at,
            )

        return self._runner.run(
            _context(command.tenant_id, command.owner_user_id),
            work,
            operation_kind=_AWAIT_OPERATION,
        )

    def get_proposal(
        self,
        *,
        tenant_id: str,
        owner_user_id: str,
        proposal_id: str,
    ) -> PersonalMemoryPatchProposalState | None:
        return self._runner.run(
            _context(tenant_id, owner_user_id),
            lambda transaction: self._proposals.get_proposal(
                transaction, tenant_id, owner_user_id, proposal_id
            ),
            operation_kind="PERSONAL_MEMORY_PATCH_PROPOSAL_READ",
        )


__all__ = ["PersonalMemoryPatchProposalService"]
