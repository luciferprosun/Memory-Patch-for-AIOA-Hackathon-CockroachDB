"""Transactional Step 28 Kernel/Critic correction-candidate bridge."""

from __future__ import annotations

from aioa_memory_kernel.contracts.enums import ActorType, PersonalMemorySpaceState
from aioa_memory_kernel.contracts.serialization import canonical_sha256
from aioa_memory_kernel.persistence import (
    AccessMode,
    BeginOperation,
    IdempotencyService,
    RequestContext,
    SerializableTransactionRunner,
)
from aioa_memory_kernel.persistence.protocols import TransactionProtocol

from .candidate_repository import CorrectionCandidateCockroachRepository
from .candidates import (
    CorrectionCandidateEnvelope,
    CorrectionCandidateIntakeDisposition,
    CorrectionCandidateIntakeError,
    CorrectionCandidateIntakeReceipt,
    CorrectionCandidateReasonCode,
    build_correction_candidate_intake_receipt,
    verify_correction_candidate_envelope,
)
from .models import (
    PersonalMemoryHatSlot,
    PersonalMemoryModelBinding,
    verify_model_binding_hash,
    verify_slot_hash,
)
from .repository import PersonalMemoryCockroachRepository


_OPERATION_KIND = "PERSONAL_MEMORY_CORRECTION_CANDIDATE_INTAKE"


def _context(tenant_id: str, owner_user_id: str) -> RequestContext:
    return RequestContext(
        tenant_id=tenant_id,
        user_id=owner_user_id,
        access_mode=AccessMode.USER_PRIVATE,
    )


def _scope_digest(envelope: CorrectionCandidateEnvelope) -> str:
    target = envelope.submission.target_slot_binding
    return canonical_sha256(
        {
            "candidate_policy_digest": envelope.policy.policy_digest,
            "configuration_digest": target.configuration_digest,
            "hat_scope_id": target.hat_scope_id,
            "owner_user_id": target.owner_user_id,
            "personal_memory_space_id": target.personal_memory_space_id,
            "target_scope": "USER_PERSONAL_HAT",
            "tenant_id": target.tenant_id,
        }
    )


def _state_reason(state: PersonalMemorySpaceState) -> CorrectionCandidateReasonCode:
    if state is PersonalMemorySpaceState.ARCHIVED:
        return CorrectionCandidateReasonCode.TARGET_ARCHIVED
    if state in {
        PersonalMemorySpaceState.DELETED_PENDING,
        PersonalMemorySpaceState.DELETED,
    }:
        return CorrectionCandidateReasonCode.TARGET_DELETE_PENDING
    return CorrectionCandidateReasonCode.TARGET_INELIGIBLE


def _find_binding(
    slot: PersonalMemoryHatSlot,
    binding_id: str,
) -> PersonalMemoryModelBinding:
    matches = tuple(
        value for value in slot.model_bindings if value.binding_id == binding_id
    )
    if len(matches) != 1 or not matches[0].enabled:
        raise CorrectionCandidateIntakeError(
            CorrectionCandidateReasonCode.MODEL_BINDING_MISMATCH
        )
    verify_model_binding_hash(matches[0])
    return matches[0]


def _verify_current_target(
    envelope: CorrectionCandidateEnvelope,
    slot: PersonalMemoryHatSlot,
) -> None:
    """Reject stale configuration, cross-owner, and binding detachment."""

    verify_slot_hash(slot)
    target = envelope.submission.target_slot_binding
    if slot.tenant_id != target.tenant_id:
        raise CorrectionCandidateIntakeError(
            CorrectionCandidateReasonCode.TENANT_MISMATCH
        )
    if slot.owner_user_id != target.owner_user_id:
        raise CorrectionCandidateIntakeError(
            CorrectionCandidateReasonCode.OWNER_MISMATCH
        )
    if (
        slot.personal_memory_space_id != target.personal_memory_space_id
        or slot.hat_scope_id != target.hat_scope_id
    ):
        raise CorrectionCandidateIntakeError(
            CorrectionCandidateReasonCode.SCOPE_MISMATCH
        )
    if slot.state not in {
        PersonalMemorySpaceState.CONFIGURED,
        PersonalMemorySpaceState.ACTIVE,
    }:
        raise CorrectionCandidateIntakeError(_state_reason(slot.state))
    if (
        slot.state is not target.slot_state
        or slot.state_version != target.slot_state_version
        or slot.configuration_version != target.slot_configuration_version
        or slot.slot_hash != target.slot_hash
        or slot.configuration_digest != target.configuration_digest
        or slot.quota_policy_id != target.quota_policy_id
        or slot.quota_policy_digest != target.quota_policy_digest
    ):
        raise CorrectionCandidateIntakeError(
            CorrectionCandidateReasonCode.TARGET_INELIGIBLE
        )
    binding = _find_binding(slot, target.model_binding_id)
    if (
        binding.binding_hash != target.model_binding_hash
        or binding.binding_version != target.model_binding_version
        or binding.binding_mode is not target.binding_mode
        or binding.provider_id != target.provider_id
        or binding.model_id != target.model_id
        or binding.model_revision_or_declared_version
        != target.model_revision_or_declared_version
    ):
        raise CorrectionCandidateIntakeError(
            CorrectionCandidateReasonCode.MODEL_BINDING_MISMATCH
        )


class CorrectionCandidateBridgeService:
    """Two exact producer entry points and owner-scoped candidate reads only."""

    def __init__(
        self,
        transaction_runner: SerializableTransactionRunner,
        *,
        slot_repository: PersonalMemoryCockroachRepository | None = None,
        candidate_repository: CorrectionCandidateCockroachRepository | None = None,
        idempotency: IdempotencyService | None = None,
    ) -> None:
        if not isinstance(transaction_runner, SerializableTransactionRunner):
            raise TypeError("transaction_runner must be SerializableTransactionRunner")
        self._runner = transaction_runner
        self._slots = slot_repository or PersonalMemoryCockroachRepository()
        self._candidates = (
            candidate_repository or CorrectionCandidateCockroachRepository()
        )
        self._idempotency = idempotency or IdempotencyService()

    def submit_kernel_candidate(
        self,
        envelope: CorrectionCandidateEnvelope,
    ) -> tuple[CorrectionCandidateEnvelope, CorrectionCandidateIntakeReceipt]:
        return self._submit(envelope, ActorType.KNOWLEDGE_KERNEL)

    def submit_critic_loop_candidate(
        self,
        envelope: CorrectionCandidateEnvelope,
    ) -> tuple[CorrectionCandidateEnvelope, CorrectionCandidateIntakeReceipt]:
        return self._submit(envelope, ActorType.CRITIC_PROMPT_LOOP)

    def _submit(
        self,
        envelope: CorrectionCandidateEnvelope,
        required_source: ActorType,
    ) -> tuple[CorrectionCandidateEnvelope, CorrectionCandidateIntakeReceipt]:
        if not isinstance(envelope, CorrectionCandidateEnvelope):
            raise TypeError("envelope must be CorrectionCandidateEnvelope")
        verify_correction_candidate_envelope(envelope)
        submission = envelope.submission
        candidate = submission.candidate
        if candidate.source_component is not required_source:
            raise CorrectionCandidateIntakeError(
                CorrectionCandidateReasonCode.SOURCE_NOT_ALLOWED
            )

        def work(transaction: TransactionProtocol):
            claim = self._idempotency.begin_or_resume_operation(
                transaction,
                BeginOperation(
                    operation_id=submission.submission_id,
                    tenant_id=candidate.tenant_id,
                    owner_user_id=candidate.user_id,
                    operation_kind=_OPERATION_KIND,
                    idempotency_key=submission.idempotency_key,
                    request_digest=submission.submission_hash,
                    scope_digest=_scope_digest(envelope),
                    created_at=submission.submitted_at,
                ),
            )
            if not claim.may_proceed:
                stored = self._candidates.get_candidate(
                    transaction,
                    candidate.tenant_id,
                    candidate.user_id,
                    envelope.candidate_id,
                )
                if (
                    stored is None
                    or claim.operation.result_ref != stored.candidate_id
                    or claim.operation.result_digest != stored.envelope_hash
                    or stored.submission.semantic_deduplication_key
                    != submission.semantic_deduplication_key
                ):
                    raise CorrectionCandidateIntakeError(
                        CorrectionCandidateReasonCode.CONTENT_CONFLICT
                    )
                receipt = build_correction_candidate_intake_receipt(
                    envelope,
                    accepted_at=submission.submitted_at,
                    disposition=CorrectionCandidateIntakeDisposition.EXACT_REPLAY,
                )
                return stored, receipt

            slot = self._slots.get_slot(
                transaction,
                candidate.tenant_id,
                candidate.user_id,
                candidate.personal_memory_space_id,
            )
            if slot is None:
                raise CorrectionCandidateIntakeError(
                    CorrectionCandidateReasonCode.TARGET_NOT_FOUND
                )
            _verify_current_target(envelope, slot)

            stored = self._candidates.get_candidate(
                transaction,
                candidate.tenant_id,
                candidate.user_id,
                envelope.candidate_id,
            )
            inserted = False
            if stored is None:
                count, stored_bytes = self._candidates.candidate_usage(
                    transaction,
                    candidate.tenant_id,
                    candidate.user_id,
                    candidate.personal_memory_space_id,
                )
                envelope_bytes = len(envelope.canonical_bytes())
                if (
                    count >= envelope.policy.maximum_candidates_per_slot
                    or stored_bytes + envelope_bytes
                    > envelope.policy.maximum_candidate_bytes_per_slot
                ):
                    raise CorrectionCandidateIntakeError(
                        CorrectionCandidateReasonCode.CANDIDATE_QUOTA_EXCEEDED
                    )
                self._candidates.ensure_target_hat_scope(
                    transaction,
                    submission.target_slot_binding,
                    submission.submitted_at,
                )
                stored, inserted = self._candidates.insert_candidate(
                    transaction, envelope
                )
            elif (
                stored.submission.semantic_deduplication_key
                != submission.semantic_deduplication_key
            ):
                raise CorrectionCandidateIntakeError(
                    CorrectionCandidateReasonCode.CONTENT_CONFLICT
                )

            disposition = (
                CorrectionCandidateIntakeDisposition.ACCEPTED
                if inserted
                else CorrectionCandidateIntakeDisposition.DUPLICATE
            )
            receipt = build_correction_candidate_intake_receipt(
                envelope,
                accepted_at=submission.submitted_at,
                disposition=disposition,
            )
            self._idempotency.complete_operation(
                transaction,
                tenant_id=candidate.tenant_id,
                operation_id=claim.operation.operation_id,
                expected_attempt_count=claim.operation.attempt_count,
                result_ref=stored.candidate_id,
                result_digest=stored.envelope_hash,
            )
            return stored, receipt

        return self._runner.run(
            _context(candidate.tenant_id, candidate.user_id),
            work,
            operation_kind=_OPERATION_KIND,
        )

    def get_candidate(
        self,
        *,
        tenant_id: str,
        owner_user_id: str,
        candidate_id: str,
    ) -> CorrectionCandidateEnvelope | None:
        return self._runner.run(
            _context(tenant_id, owner_user_id),
            lambda transaction: self._candidates.get_candidate(
                transaction, tenant_id, owner_user_id, candidate_id
            ),
            operation_kind="CORRECTION_CANDIDATE_READ",
        )

    def list_owner_candidates(
        self,
        *,
        tenant_id: str,
        owner_user_id: str,
        personal_memory_space_id: str,
    ) -> tuple[CorrectionCandidateEnvelope, ...]:
        return self._runner.run(
            _context(tenant_id, owner_user_id),
            lambda transaction: self._candidates.list_owner_candidates(
                transaction,
                tenant_id,
                owner_user_id,
                personal_memory_space_id,
            ),
            operation_kind="CORRECTION_CANDIDATE_LIST",
        )


__all__ = ["CorrectionCandidateBridgeService"]
