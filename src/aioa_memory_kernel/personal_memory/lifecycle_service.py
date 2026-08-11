"""Separated approval, commit-helper, and activation services for Step 30."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from aioa_memory_kernel.contracts.enums import PatchState
from aioa_memory_kernel.contracts.serialization import (
    canonical_json,
    canonical_sha256,
    ensure_utc,
)
from aioa_memory_kernel.persistence import (
    AccessMode,
    BeginOperation,
    IdempotencyService,
    RequestContext,
    SerializableTransactionRunner,
)
from aioa_memory_kernel.persistence.protocols import TransactionProtocol
from aioa_memory_kernel.security.credentials import CredentialPurpose

from .candidate_repository import CorrectionCandidateCockroachRepository
from .lifecycle import (
    MAXIMUM_REVALIDATION_AGE_SECONDS,
    PersonalMemoryActivationReceipt,
    PersonalMemoryActivationRequest,
    PersonalMemoryApprovalReceipt,
    PersonalMemoryApprovalRequest,
    PersonalMemoryCommitReceipt,
    PersonalMemoryCommitRequest,
    PersonalMemoryPatchLifecycleError,
    PersonalMemoryPatchLifecycleState,
    Step30ReasonCode,
    activate_personal_memory_patch,
    approve_personal_memory_patch,
    commit_personal_memory_patch,
    verify_personal_memory_activation_request,
    verify_personal_memory_approval_request,
    verify_personal_memory_commit_request,
    verify_personal_memory_patch_lifecycle_state,
)
from .lifecycle_repository import (
    PersonalMemoryPatchLifecycleCockroachRepository,
)
from .models import PersonalMemoryHatSlot, enforce_step27_quota
from .proposal_repository import PersonalMemoryPatchProposalCockroachRepository
from .proposal_service import _quota_and_model_results, _verify_current_target
from .proposals import (
    PersonalMemoryPatchProposalState,
    ProposalConflictResult,
    ProposalDedupResult,
    ProposalFreshnessResult,
    ProposalGateResult,
    verify_personal_memory_patch_state,
)
from .repository import PersonalMemoryCockroachRepository


_APPROVE_OPERATION = "PERSONAL_MEMORY_PATCH_OWNER_APPROVE"
_COMMIT_OPERATION = "PERSONAL_MEMORY_PATCH_TECHNICAL_COMMIT"
_ACTIVATE_OPERATION = "PERSONAL_MEMORY_PATCH_ACTIVATE"


class Step30TrustedClock(Protocol):
    """Application-owned time source; request/model timestamps are not authority."""

    def now(self) -> datetime:
        ...


def _trusted_now(clock: Step30TrustedClock) -> datetime:
    if not hasattr(clock, "now") or not callable(clock.now):
        raise TypeError("a Step 30 trusted clock is required")
    return ensure_utc(clock.now(), "Step 30 trusted clock value")


def _context(tenant_id: str, owner_user_id: str) -> RequestContext:
    return RequestContext(
        tenant_id=tenant_id,
        user_id=owner_user_id,
        access_mode=AccessMode.USER_PRIVATE,
    )


def _operation_id(kind: str, tenant_id: str, owner_user_id: str, replay: str) -> str:
    return "step30-operation-" + canonical_sha256(
        {
            "operation_kind": kind,
            "tenant_id": tenant_id,
            "owner_user_id": owner_user_id,
            "replay_identity": replay,
        }
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


def _base_state(
    value: PersonalMemoryPatchProposalState | PersonalMemoryPatchLifecycleState,
) -> PersonalMemoryPatchProposalState:
    if isinstance(value, PersonalMemoryPatchLifecycleState):
        verify_personal_memory_patch_lifecycle_state(value)
        return value.step29_state
    verify_personal_memory_patch_state(value)
    return value


def _verify_evidence_current(
    base: PersonalMemoryPatchProposalState,
    *,
    at: datetime,
) -> None:
    binding = base.evidence_binding
    receipt = base.validation_receipt
    if (
        binding is None
        or receipt is None
        or not receipt.validated
        or receipt.dedup_result is not ProposalDedupResult.PASS
        or receipt.conflict_result is not ProposalConflictResult.PASS
        or receipt.freshness_result is not ProposalFreshnessResult.FRESH
        or any(
            getattr(receipt, name) is not ProposalGateResult.PASS
            for name in (
                "temporal_result",
                "owner_scope_result",
                "slot_state_result",
                "quota_result",
                "model_binding_result",
            )
        )
        or binding.conflict_group_hashes
        or binding.prohibited_claim_hashes
    ):
        raise PersonalMemoryPatchLifecycleError(
            Step30ReasonCode.COMMIT_EVIDENCE_STALE
        )
    age = (at - receipt.validated_at).total_seconds()
    if age < 0 or age > MAXIMUM_REVALIDATION_AGE_SECONDS:
        raise PersonalMemoryPatchLifecycleError(
            Step30ReasonCode.COMMIT_VALIDATION_STALE
        )


def _verify_current_target_and_policy(
    transaction: TransactionProtocol,
    *,
    state: PersonalMemoryPatchProposalState | PersonalMemoryPatchLifecycleState,
    at: datetime,
    slot_repository: PersonalMemoryCockroachRepository,
    candidate_repository: CorrectionCandidateCockroachRepository,
    proposal_repository: PersonalMemoryPatchProposalCockroachRepository,
) -> tuple[PersonalMemoryPatchProposalState, PersonalMemoryHatSlot]:
    base = _base_state(state)
    _verify_evidence_current(base, at=at)
    try:
        slot = _verify_current_target(
            transaction,
            state=base,
            slot_repository=slot_repository,
            candidate_repository=candidate_repository,
        )
    except Exception as exc:
        raise PersonalMemoryPatchLifecycleError(
            Step30ReasonCode.COMMIT_SLOT_INVALID
        ) from exc
    quota, model = _quota_and_model_results(
        transaction,
        slot=slot,
        proposal=base.proposal,
        slot_repository=slot_repository,
        proposal_repository=proposal_repository,
    )
    if quota is not ProposalGateResult.PASS:
        raise PersonalMemoryPatchLifecycleError(
            Step30ReasonCode.COMMIT_QUOTA_EXCEEDED
        )
    if model is not ProposalGateResult.PASS:
        raise PersonalMemoryPatchLifecycleError(
            Step30ReasonCode.COMMIT_BINDING_INVALID
        )
    return base, slot


def _verify_commit_capacity(
    transaction: TransactionProtocol,
    *,
    slot: PersonalMemoryHatSlot,
    state: PersonalMemoryPatchLifecycleState,
    slot_repository: PersonalMemoryCockroachRepository,
) -> None:
    policy = slot_repository.get_quota_policy(
        transaction,
        slot.tenant_id,
        slot.owner_user_id,
        slot.quota_policy_id,
    )
    if policy is None or policy.policy_digest != slot.quota_policy_digest:
        raise PersonalMemoryPatchLifecycleError(
            Step30ReasonCode.COMMIT_QUOTA_EXCEEDED
        )
    usage = slot_repository.owner_usage(
        transaction,
        tenant_id=slot.tenant_id,
        owner_user_id=slot.owner_user_id,
        personal_memory_space_id=slot.personal_memory_space_id,
        quota_policy_digest=slot.quota_policy_digest,
    )
    enforce_step27_quota(policy, usage)
    maximum_bytes = policy.limits.maximum_bytes
    patch_bytes = len(canonical_json(state.committed_patch).encode("utf-8"))
    if maximum_bytes is not None and usage.stored_bytes + patch_bytes > maximum_bytes:
        raise PersonalMemoryPatchLifecycleError(
            Step30ReasonCode.COMMIT_QUOTA_EXCEEDED
        )


def _verify_activation_capacity(
    transaction: TransactionProtocol,
    *,
    slot: PersonalMemoryHatSlot,
    slot_repository: PersonalMemoryCockroachRepository,
) -> None:
    policy = slot_repository.get_quota_policy(
        transaction,
        slot.tenant_id,
        slot.owner_user_id,
        slot.quota_policy_id,
    )
    if policy is None or policy.policy_digest != slot.quota_policy_digest:
        raise PersonalMemoryPatchLifecycleError(
            Step30ReasonCode.ACTIVATION_QUOTA_INVALID
        )
    usage = slot_repository.owner_usage(
        transaction,
        tenant_id=slot.tenant_id,
        owner_user_id=slot.owner_user_id,
        personal_memory_space_id=slot.personal_memory_space_id,
        quota_policy_digest=slot.quota_policy_digest,
    )
    enforce_step27_quota(policy, usage)
    maximum_active = policy.limits.maximum_active_memory_patches
    if maximum_active is not None and usage.usage.active_memory_patches >= maximum_active:
        raise PersonalMemoryPatchLifecycleError(
            Step30ReasonCode.ACTIVATION_QUOTA_INVALID
        )


class PersonalMemoryApprovalService:
    """Authenticated owner approval; never technical commit or activation."""

    def __init__(
        self,
        transaction_runner: SerializableTransactionRunner,
        *,
        lifecycle_repository: PersonalMemoryPatchLifecycleCockroachRepository | None = None,
        slot_repository: PersonalMemoryCockroachRepository | None = None,
        candidate_repository: CorrectionCandidateCockroachRepository | None = None,
        proposal_repository: PersonalMemoryPatchProposalCockroachRepository | None = None,
        idempotency: IdempotencyService | None = None,
        trusted_clock: Step30TrustedClock,
    ) -> None:
        if not isinstance(transaction_runner, SerializableTransactionRunner):
            raise TypeError("transaction_runner must be SerializableTransactionRunner")
        transaction_runner.require_credential_purpose(
            CredentialPurpose.APPLICATION_DATABASE
        )
        self._runner = transaction_runner
        self._lifecycle = lifecycle_repository or PersonalMemoryPatchLifecycleCockroachRepository()
        self._slots = slot_repository or PersonalMemoryCockroachRepository()
        self._candidates = candidate_repository or CorrectionCandidateCockroachRepository()
        self._proposals = proposal_repository or PersonalMemoryPatchProposalCockroachRepository()
        self._idempotency = idempotency or IdempotencyService()
        self._clock = trusted_clock

    def approve(
        self,
        request: PersonalMemoryApprovalRequest,
        *,
        authenticated_actor_user_id: str,
    ) -> tuple[PersonalMemoryPatchLifecycleState, PersonalMemoryApprovalReceipt, bool]:
        verify_personal_memory_approval_request(request)
        trusted_now = _trusted_now(self._clock)
        if authenticated_actor_user_id != request.owner_user_id:
            raise PersonalMemoryPatchLifecycleError(
                Step30ReasonCode.APPROVAL_OWNER_MISMATCH
            )
        operation_id = _operation_id(
            _APPROVE_OPERATION,
            request.tenant_id,
            request.owner_user_id,
            request.approval_replay_identity,
        )

        def work(transaction: TransactionProtocol):
            current = self._lifecycle.get_state(
                transaction,
                request.tenant_id,
                request.owner_user_id,
                request.proposal_id,
            )
            if current is None:
                raise PersonalMemoryPatchLifecycleError(
                    Step30ReasonCode.APPROVAL_HASH_MISMATCH
                )
            claim = self._idempotency.begin_or_resume_operation(
                transaction,
                BeginOperation(
                    operation_id=operation_id,
                    tenant_id=request.tenant_id,
                    owner_user_id=request.owner_user_id,
                    operation_kind=_APPROVE_OPERATION,
                    idempotency_key=request.approval_replay_identity,
                    request_digest=request.request_hash,
                    scope_digest=_scope_digest(
                        tenant_id=request.tenant_id,
                        owner_user_id=request.owner_user_id,
                        personal_memory_space_id=request.personal_memory_space_id,
                        proposal_hash=request.proposal_hash,
                    ),
                    created_at=request.requested_at,
                ),
            )
            if not claim.may_proceed:
                if not isinstance(current, PersonalMemoryPatchLifecycleState):
                    raise PersonalMemoryPatchLifecycleError(
                        Step30ReasonCode.APPROVAL_REPLAY_CONFLICT
                    )
                row = self._lifecycle.get_approval_replay(
                    transaction,
                    tenant_id=request.tenant_id,
                    owner_user_id=request.owner_user_id,
                    replay_identity=request.approval_replay_identity,
                )
                if (
                    row is None
                    or row["proposal_id"] != request.proposal_id
                    or row["step30_request_hash"] != request.request_hash
                    or row["step30_approval_receipt_hash"]
                    != current.approval_receipt.receipt_hash
                    or claim.operation.result_digest
                    != current.approval_receipt.receipt_hash
                ):
                    raise PersonalMemoryPatchLifecycleError(
                        Step30ReasonCode.APPROVAL_REPLAY_CONFLICT
                    )
                return current, current.approval_receipt, True
            if not isinstance(current, PersonalMemoryPatchProposalState):
                raise PersonalMemoryPatchLifecycleError(
                    Step30ReasonCode.APPROVAL_STATE_MISMATCH
                )
            _verify_current_target_and_policy(
                transaction,
                state=current,
                at=trusted_now,
                slot_repository=self._slots,
                candidate_repository=self._candidates,
                proposal_repository=self._proposals,
            )
            updated = approve_personal_memory_patch(
                current,
                request,
                authenticated_actor_user_id=authenticated_actor_user_id,
                approved_at=trusted_now,
            )
            self._lifecycle.insert_approval(transaction, updated.approval_receipt)
            stored = self._lifecycle.transition_state(
                transaction, expected=current, updated=updated
            )
            self._lifecycle.insert_transition_event(
                transaction,
                state=stored,
                state_before=PatchState.AWAITING_APPROVAL,
                receipt_hash=stored.approval_receipt.receipt_hash,
                transitioned_at=trusted_now,
            )
            self._idempotency.complete_operation(
                transaction,
                tenant_id=request.tenant_id,
                operation_id=operation_id,
                expected_attempt_count=claim.operation.attempt_count,
                result_ref=request.proposal_id,
                result_digest=stored.approval_receipt.receipt_hash,
            )
            return stored, stored.approval_receipt, False

        return self._runner.run(
            _context(request.tenant_id, request.owner_user_id),
            work,
            operation_kind=_APPROVE_OPERATION,
        )


class PersonalMemoryCommitHelper:
    """Least-privileged technical commit; cannot manufacture approval."""

    def __init__(
        self,
        commit_transaction_runner: SerializableTransactionRunner,
        *,
        lifecycle_repository: PersonalMemoryPatchLifecycleCockroachRepository | None = None,
        slot_repository: PersonalMemoryCockroachRepository | None = None,
        candidate_repository: CorrectionCandidateCockroachRepository | None = None,
        proposal_repository: PersonalMemoryPatchProposalCockroachRepository | None = None,
        idempotency: IdempotencyService | None = None,
        trusted_clock: Step30TrustedClock,
    ) -> None:
        if not isinstance(commit_transaction_runner, SerializableTransactionRunner):
            raise TypeError("commit_transaction_runner must be SerializableTransactionRunner")
        commit_transaction_runner.require_credential_purpose(
            CredentialPurpose.PERSONAL_MEMORY_COMMIT_DATABASE
        )
        self._runner = commit_transaction_runner
        self._lifecycle = lifecycle_repository or PersonalMemoryPatchLifecycleCockroachRepository()
        self._slots = slot_repository or PersonalMemoryCockroachRepository()
        self._candidates = candidate_repository or CorrectionCandidateCockroachRepository()
        self._proposals = proposal_repository or PersonalMemoryPatchProposalCockroachRepository()
        self._idempotency = idempotency or IdempotencyService()
        self._clock = trusted_clock

    def commit(
        self,
        request: PersonalMemoryCommitRequest,
    ) -> tuple[PersonalMemoryPatchLifecycleState, PersonalMemoryCommitReceipt, bool]:
        verify_personal_memory_commit_request(request)
        trusted_now = _trusted_now(self._clock)
        operation_id = _operation_id(
            _COMMIT_OPERATION,
            request.tenant_id,
            request.owner_user_id,
            request.commit_replay_identity,
        )

        def work(transaction: TransactionProtocol):
            self._lifecycle.assert_commit_helper_authority(transaction)
            current = self._lifecycle.get_state(
                transaction,
                request.tenant_id,
                request.owner_user_id,
                request.proposal_id,
            )
            if current is None:
                raise PersonalMemoryPatchLifecycleError(
                    Step30ReasonCode.COMMIT_APPROVAL_INVALID
                )
            claim = self._idempotency.begin_or_resume_operation(
                transaction,
                BeginOperation(
                    operation_id=operation_id,
                    tenant_id=request.tenant_id,
                    owner_user_id=request.owner_user_id,
                    operation_kind=_COMMIT_OPERATION,
                    idempotency_key=request.commit_replay_identity,
                    request_digest=request.request_hash,
                    scope_digest=_scope_digest(
                        tenant_id=request.tenant_id,
                        owner_user_id=request.owner_user_id,
                        personal_memory_space_id=request.personal_memory_space_id,
                        proposal_hash=request.proposal_hash,
                    ),
                    created_at=request.requested_at,
                ),
            )
            if not claim.may_proceed:
                if (
                    not isinstance(current, PersonalMemoryPatchLifecycleState)
                    or current.commit_receipt is None
                ):
                    raise PersonalMemoryPatchLifecycleError(
                        Step30ReasonCode.COMMIT_REPLAY_CONFLICT
                    )
                row = self._lifecycle.get_commit_replay(
                    transaction,
                    tenant_id=request.tenant_id,
                    owner_user_id=request.owner_user_id,
                    replay_identity=request.commit_replay_identity,
                )
                if (
                    row is None
                    or row["proposal_id"] != request.proposal_id
                    or row["step30_request_hash"] != request.request_hash
                    or row["step30_commit_receipt_hash"]
                    != current.commit_receipt.receipt_hash
                    or claim.operation.result_digest
                    != current.commit_receipt.receipt_hash
                ):
                    raise PersonalMemoryPatchLifecycleError(
                        Step30ReasonCode.COMMIT_REPLAY_CONFLICT
                    )
                return current, current.commit_receipt, True
            if (
                not isinstance(current, PersonalMemoryPatchLifecycleState)
                or current.state is not PatchState.APPROVED
            ):
                raise PersonalMemoryPatchLifecycleError(
                    Step30ReasonCode.COMMIT_APPROVAL_INVALID
                )
            _, slot = _verify_current_target_and_policy(
                transaction,
                state=current,
                at=trusted_now,
                slot_repository=self._slots,
                candidate_repository=self._candidates,
                proposal_repository=self._proposals,
            )
            updated = commit_personal_memory_patch(
                current, request, committed_at=trusted_now
            )
            _verify_commit_capacity(
                transaction,
                slot=slot,
                state=updated,
                slot_repository=self._slots,
            )
            self._lifecycle.insert_commit_and_patch(transaction, updated)
            stored = self._lifecycle.transition_state(
                transaction, expected=current, updated=updated
            )
            self._lifecycle.insert_transition_event(
                transaction,
                state=stored,
                state_before=PatchState.APPROVED,
                receipt_hash=stored.commit_receipt.receipt_hash,
                transitioned_at=trusted_now,
            )
            self._idempotency.complete_operation(
                transaction,
                tenant_id=request.tenant_id,
                operation_id=operation_id,
                expected_attempt_count=claim.operation.attempt_count,
                result_ref=stored.committed_patch.patch_id,
                result_digest=stored.commit_receipt.receipt_hash,
            )
            return stored, stored.commit_receipt, False

        return self._runner.run(
            _context(request.tenant_id, request.owner_user_id),
            work,
            operation_kind=_COMMIT_OPERATION,
        )


class PersonalMemoryActivationService:
    """Receipt-gated COMMITTED to ACTIVE transition; no retrieval API."""

    def __init__(
        self,
        activation_transaction_runner: SerializableTransactionRunner,
        *,
        lifecycle_repository: PersonalMemoryPatchLifecycleCockroachRepository | None = None,
        slot_repository: PersonalMemoryCockroachRepository | None = None,
        candidate_repository: CorrectionCandidateCockroachRepository | None = None,
        proposal_repository: PersonalMemoryPatchProposalCockroachRepository | None = None,
        idempotency: IdempotencyService | None = None,
        trusted_clock: Step30TrustedClock,
    ) -> None:
        if not isinstance(activation_transaction_runner, SerializableTransactionRunner):
            raise TypeError("activation_transaction_runner must be SerializableTransactionRunner")
        activation_transaction_runner.require_credential_purpose(
            CredentialPurpose.PERSONAL_MEMORY_COMMIT_DATABASE
        )
        self._runner = activation_transaction_runner
        self._lifecycle = lifecycle_repository or PersonalMemoryPatchLifecycleCockroachRepository()
        self._slots = slot_repository or PersonalMemoryCockroachRepository()
        self._candidates = candidate_repository or CorrectionCandidateCockroachRepository()
        self._proposals = proposal_repository or PersonalMemoryPatchProposalCockroachRepository()
        self._idempotency = idempotency or IdempotencyService()
        self._clock = trusted_clock

    def activate(
        self,
        request: PersonalMemoryActivationRequest,
    ) -> tuple[PersonalMemoryPatchLifecycleState, PersonalMemoryActivationReceipt, bool]:
        verify_personal_memory_activation_request(request)
        trusted_now = _trusted_now(self._clock)
        operation_id = _operation_id(
            _ACTIVATE_OPERATION,
            request.tenant_id,
            request.owner_user_id,
            request.activation_replay_identity,
        )

        def work(transaction: TransactionProtocol):
            self._lifecycle.assert_commit_helper_authority(transaction)
            current = self._lifecycle.get_state(
                transaction,
                request.tenant_id,
                request.owner_user_id,
                # Activation request binds patch, proposal hash, and owner;
                # proposal ID is obtained through the unique patch carrier.
                self._proposal_id_for_patch(transaction, request),
            )
            if current is None:
                raise PersonalMemoryPatchLifecycleError(
                    Step30ReasonCode.ACTIVATION_COMMIT_INVALID
                )
            claim = self._idempotency.begin_or_resume_operation(
                transaction,
                BeginOperation(
                    operation_id=operation_id,
                    tenant_id=request.tenant_id,
                    owner_user_id=request.owner_user_id,
                    operation_kind=_ACTIVATE_OPERATION,
                    idempotency_key=request.activation_replay_identity,
                    request_digest=request.request_hash,
                    scope_digest=_scope_digest(
                        tenant_id=request.tenant_id,
                        owner_user_id=request.owner_user_id,
                        personal_memory_space_id=request.personal_memory_space_id,
                        proposal_hash=request.proposal_hash,
                    ),
                    created_at=request.requested_at,
                ),
            )
            if not claim.may_proceed:
                if (
                    not isinstance(current, PersonalMemoryPatchLifecycleState)
                    or current.activation_receipt is None
                ):
                    raise PersonalMemoryPatchLifecycleError(
                        Step30ReasonCode.ACTIVATION_REPLAY_CONFLICT
                    )
                row = self._lifecycle.get_activation_replay(
                    transaction,
                    tenant_id=request.tenant_id,
                    patch_id=request.patch_id,
                    replay_identity=request.activation_replay_identity,
                )
                if (
                    row is None
                    or row["step30_activation_receipt_hash"]
                    != current.activation_receipt.receipt_hash
                    or claim.operation.result_digest
                    != current.activation_receipt.receipt_hash
                ):
                    raise PersonalMemoryPatchLifecycleError(
                        Step30ReasonCode.ACTIVATION_REPLAY_CONFLICT
                    )
                return current, current.activation_receipt, True
            if (
                not isinstance(current, PersonalMemoryPatchLifecycleState)
                or current.state is not PatchState.COMMITTED
            ):
                raise PersonalMemoryPatchLifecycleError(
                    Step30ReasonCode.ACTIVATION_COMMIT_INVALID
                )
            _, slot = _verify_current_target_and_policy(
                transaction,
                state=current,
                at=trusted_now,
                slot_repository=self._slots,
                candidate_repository=self._candidates,
                proposal_repository=self._proposals,
            )
            _verify_activation_capacity(
                transaction, slot=slot, slot_repository=self._slots
            )
            updated = activate_personal_memory_patch(
                current, request, activated_at=trusted_now
            )
            self._lifecycle.activate_patch(transaction, updated)
            stored = self._lifecycle.transition_state(
                transaction, expected=current, updated=updated
            )
            self._lifecycle.insert_transition_event(
                transaction,
                state=stored,
                state_before=PatchState.COMMITTED,
                receipt_hash=stored.activation_receipt.receipt_hash,
                transitioned_at=trusted_now,
            )
            self._idempotency.complete_operation(
                transaction,
                tenant_id=request.tenant_id,
                operation_id=operation_id,
                expected_attempt_count=claim.operation.attempt_count,
                result_ref=request.patch_id,
                result_digest=stored.activation_receipt.receipt_hash,
            )
            return stored, stored.activation_receipt, False

        return self._runner.run(
            _context(request.tenant_id, request.owner_user_id),
            work,
            operation_kind=_ACTIVATE_OPERATION,
        )

    @staticmethod
    def _proposal_id_for_patch(
        transaction: TransactionProtocol,
        request: PersonalMemoryActivationRequest,
    ) -> str:
        row = transaction.fetch_one(
            """
            SELECT step30_proposal_id
              FROM memory_patch.memory_items
             WHERE tenant_id = %s AND memory_item_id = %s
               AND step30_proposal_hash = %s
            """,
            (request.tenant_id, request.patch_id, request.proposal_hash),
        )
        if row is None:
            raise PersonalMemoryPatchLifecycleError(
                Step30ReasonCode.ACTIVATION_COMMIT_INVALID
            )
        return str(row["step30_proposal_id"])


__all__ = [
    "PersonalMemoryActivationService",
    "PersonalMemoryApprovalService",
    "PersonalMemoryCommitHelper",
    "Step30TrustedClock",
]
