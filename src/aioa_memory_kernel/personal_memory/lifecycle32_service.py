"""Transactional Step 32 lifecycle services.

All operations run inside the Step 6 serializable runner.  Replay identities
are unique in CockroachDB and each mutation is owner/tenant/slot scoped.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from aioa_memory_kernel.contracts.enums import PersonalMemorySpaceState
from aioa_memory_kernel.contracts.serialization import ensure_utc, to_canonical_data
from aioa_memory_kernel.persistence import (
    AccessMode,
    RequestContext,
    SerializableTransactionRunner,
)
from aioa_memory_kernel.persistence.protocols import TransactionProtocol

from .lifecycle32 import (
    PersonalMemoryDeletionRequest,
    PersonalMemoryDeletionResult,
    PersonalMemoryLifecycleExportBundle,
    PersonalMemoryLifecycleExportRequest,
    PersonalMemoryPatchRevocation,
    PersonalMemoryPatchSupersession,
    PersonalMemoryRevocationRequest,
    PersonalMemoryStep32Error,
    PersonalMemorySupersessionRequest,
    SharedMemoryPromotionProposal,
    SharedMemoryPromotionRequest,
    Step32ActorType,
    Step32ReasonCode,
    build_lifecycle_export_bundle,
    complete_logical_deletion,
    create_patch_revocation,
    create_patch_supersession,
    create_shared_promotion_proposal,
    deletion_result_for_request,
    verify_deletion_request,
    verify_lifecycle_export_request,
    verify_revocation_request,
    verify_shared_promotion_request,
    verify_supersession_request,
)
from .lifecycle32_repository import PersonalMemoryLifecycle32CockroachRepository
from .models import PersonalMemoryHatSlot, verify_slot_hash
from .repository import PersonalMemoryCockroachRepository


class Step32TrustedClock(Protocol):
    def now(self) -> datetime:
        ...


def _context(tenant_id: str, owner_user_id: str) -> RequestContext:
    return RequestContext(
        tenant_id=tenant_id,
        user_id=owner_user_id,
        access_mode=AccessMode.USER_PRIVATE,
    )


def _trusted_now(clock: Step32TrustedClock) -> datetime:
    if not hasattr(clock, "now") or not callable(clock.now):
        raise TypeError("a Step 32 trusted clock is required")
    return ensure_utc(clock.now(), "Step 32 trusted clock value")


class PersonalMemoryLifecycle32Service:
    """Owner lifecycle boundary; it cannot publish shared or canonical data."""

    def __init__(
        self,
        transaction_runner: SerializableTransactionRunner,
        *,
        lifecycle_repository: PersonalMemoryLifecycle32CockroachRepository | None = None,
        slot_repository: PersonalMemoryCockroachRepository | None = None,
        trusted_clock: Step32TrustedClock,
    ) -> None:
        if not isinstance(transaction_runner, SerializableTransactionRunner):
            raise TypeError("transaction_runner must be SerializableTransactionRunner")
        self._runner = transaction_runner
        self._lifecycle = lifecycle_repository or PersonalMemoryLifecycle32CockroachRepository()
        self._slots = slot_repository or PersonalMemoryCockroachRepository()
        self._clock = trusted_clock

    def _slot(
        self,
        transaction: TransactionProtocol,
        *,
        tenant_id: str,
        owner_user_id: str,
        personal_memory_space_id: str,
    ) -> PersonalMemoryHatSlot:
        slot = self._slots.get_slot(
            transaction, tenant_id, owner_user_id, personal_memory_space_id
        )
        if slot is None:
            raise PersonalMemoryStep32Error(Step32ReasonCode.DELETE_OWNER_MISMATCH)
        verify_slot_hash(slot)
        return slot

    @staticmethod
    def _require_time(trusted_now: datetime, request_time: datetime) -> None:
        if trusted_now < request_time:
            raise PersonalMemoryStep32Error(Step32ReasonCode.STATE_VERSION_CONFLICT)

    def supersede(
        self,
        request: PersonalMemorySupersessionRequest,
        *,
        authenticated_owner_user_id: str,
    ) -> tuple[PersonalMemoryPatchSupersession, bool]:
        verify_supersession_request(request)
        trusted_now = _trusted_now(self._clock)
        self._require_time(trusted_now, request.effective_at)
        if authenticated_owner_user_id != request.owner_user_id:
            raise PersonalMemoryStep32Error(Step32ReasonCode.SUPERSESSION_OWNER_MISMATCH)

        def work(transaction: TransactionProtocol):
            old_state = self._lifecycle.get_step30_state(
                transaction, request.tenant_id, request.owner_user_id, request.old_proposal_id
            )
            new_state = self._lifecycle.get_step30_state(
                transaction, request.tenant_id, request.owner_user_id, request.new_proposal_id
            )
            if old_state is None or new_state is None:
                raise PersonalMemoryStep32Error(Step32ReasonCode.SUPERSESSION_CONFLICT)
            record = create_patch_supersession(
                request,
                old_state,
                new_state,
                authenticated_owner_user_id=authenticated_owner_user_id,
            )
            replay = self._lifecycle.get_supersession_replay(
                transaction,
                tenant_id=request.tenant_id,
                owner_user_id=request.owner_user_id,
                replay_identity=request.replay_identity,
            )
            if replay is not None:
                if (
                    replay["request_hash"] != request.request_hash
                    or replay["supersession_hash"] != record.supersession_hash
                    or replay["old_patch_id"] != request.old_patch_id
                    or replay["new_patch_id"] != request.new_patch_id
                ):
                    raise PersonalMemoryStep32Error(Step32ReasonCode.SUPERSESSION_CONFLICT)
                return record, True
            slot = self._slot(
                transaction,
                tenant_id=request.tenant_id,
                owner_user_id=request.owner_user_id,
                personal_memory_space_id=request.personal_memory_space_id,
            )
            if slot.state not in {PersonalMemorySpaceState.CONFIGURED, PersonalMemorySpaceState.ACTIVE}:
                raise PersonalMemoryStep32Error(Step32ReasonCode.SUPERSESSION_CONFLICT)
            for patch_id, patch_hash in (
                (request.old_patch_id, request.old_patch_hash),
                (request.new_patch_id, request.new_patch_hash),
            ):
                if self._lifecycle.lock_active_patch(
                    transaction,
                    tenant_id=request.tenant_id,
                    owner_user_id=request.owner_user_id,
                    personal_memory_space_id=request.personal_memory_space_id,
                    patch_id=patch_id,
                    patch_hash=patch_hash,
                ) is None:
                    raise PersonalMemoryStep32Error(Step32ReasonCode.SUPERSESSION_CONFLICT)
            self._lifecycle.persist_supersession(transaction, record)
            return record, False

        return self._runner.run(
            _context(request.tenant_id, request.owner_user_id),
            work,
            operation_kind="PERSONAL_MEMORY_PATCH_SUPERSESSION",
        )

    def revoke(
        self,
        request: PersonalMemoryRevocationRequest,
        *,
        actor_type: Step32ActorType,
        authenticated_actor_id: str,
    ) -> tuple[PersonalMemoryPatchRevocation, bool]:
        verify_revocation_request(request)
        trusted_now = _trusted_now(self._clock)
        self._require_time(trusted_now, request.effective_at)

        def work(transaction: TransactionProtocol):
            state = self._lifecycle.get_step30_state(
                transaction, request.tenant_id, request.owner_user_id, request.proposal_id
            )
            if state is None:
                raise PersonalMemoryStep32Error(Step32ReasonCode.REVOCATION_STATE_INVALID)
            record = create_patch_revocation(
                request,
                state,
                actor_type=actor_type,
                authenticated_actor_id=authenticated_actor_id,
            )
            replay = self._lifecycle.get_revocation_replay(
                transaction,
                tenant_id=request.tenant_id,
                owner_user_id=request.owner_user_id,
                replay_identity=request.replay_identity,
            )
            if replay is not None:
                if replay["request_hash"] != request.request_hash or replay["revocation_hash"] != record.revocation_hash or replay["patch_id"] != request.patch_id:
                    raise PersonalMemoryStep32Error(Step32ReasonCode.REPLAY_CONFLICT)
                return record, True
            slot = self._slot(
                transaction,
                tenant_id=request.tenant_id,
                owner_user_id=request.owner_user_id,
                personal_memory_space_id=request.personal_memory_space_id,
            )
            if slot.state not in {PersonalMemorySpaceState.CONFIGURED, PersonalMemorySpaceState.ACTIVE}:
                raise PersonalMemoryStep32Error(Step32ReasonCode.REVOCATION_STATE_INVALID)
            if self._lifecycle.lock_active_patch(
                transaction,
                tenant_id=request.tenant_id,
                owner_user_id=request.owner_user_id,
                personal_memory_space_id=request.personal_memory_space_id,
                patch_id=request.patch_id,
                patch_hash=request.patch_hash,
            ) is None:
                raise PersonalMemoryStep32Error(Step32ReasonCode.REVOCATION_STATE_INVALID)
            self._lifecycle.persist_revocation(transaction, record)
            return record, False

        return self._runner.run(
            _context(request.tenant_id, request.owner_user_id),
            work,
            operation_kind="PERSONAL_MEMORY_PATCH_REVOCATION",
        )

    def export(
        self,
        request: PersonalMemoryLifecycleExportRequest,
        *,
        authenticated_owner_user_id: str,
    ) -> tuple[PersonalMemoryLifecycleExportBundle, bool]:
        verify_lifecycle_export_request(request)
        trusted_now = _trusted_now(self._clock)
        self._require_time(trusted_now, request.requested_at)
        if authenticated_owner_user_id != request.owner_user_id:
            raise PersonalMemoryStep32Error(
                Step32ReasonCode.EXPORT_OWNER_MISMATCH
            )

        def work(transaction: TransactionProtocol):
            replay = self._lifecycle.get_export_replay(
                transaction,
                tenant_id=request.tenant_id,
                owner_user_id=request.owner_user_id,
                replay_identity=request.replay_identity,
            )
            if replay is not None:
                if replay.request_hash != request.request_hash:
                    raise PersonalMemoryStep32Error(Step32ReasonCode.REPLAY_CONFLICT)
                return replay, True
            slot = self._slot(
                transaction,
                tenant_id=request.tenant_id,
                owner_user_id=request.owner_user_id,
                personal_memory_space_id=request.personal_memory_space_id,
            )
            records = self._lifecycle.export_records(
                transaction,
                tenant_id=request.tenant_id,
                owner_user_id=request.owner_user_id,
                personal_memory_space_id=request.personal_memory_space_id,
                slot_payload=to_canonical_data(slot),
                slot_hash=slot.slot_hash,
            )
            bundle = build_lifecycle_export_bundle(request, slot, records)
            self._lifecycle.persist_export(transaction, bundle)
            return bundle, False

        return self._runner.run(
            _context(request.tenant_id, request.owner_user_id),
            work,
            operation_kind="PERSONAL_MEMORY_OWNER_EXPORT",
        )

    def delete(
        self,
        request: PersonalMemoryDeletionRequest,
        *,
        authenticated_owner_user_id: str,
    ) -> tuple[PersonalMemoryDeletionResult, PersonalMemoryHatSlot, bool]:
        verify_deletion_request(request)
        trusted_now = _trusted_now(self._clock)
        self._require_time(trusted_now, request.requested_at)
        if authenticated_owner_user_id != request.owner_user_id:
            raise PersonalMemoryStep32Error(Step32ReasonCode.DELETE_OWNER_MISMATCH)

        def work(transaction: TransactionProtocol):
            replay = self._lifecycle.get_deletion_replay(
                transaction,
                tenant_id=request.tenant_id,
                owner_user_id=request.owner_user_id,
                replay_identity=request.replay_identity,
            )
            if replay is not None:
                result = deletion_result_for_request(request)
                if replay["request_hash"] != request.request_hash or replay["result_hash"] != result.result_hash or replay["tombstone_hash"] != result.tombstone_hash:
                    raise PersonalMemoryStep32Error(Step32ReasonCode.REPLAY_CONFLICT)
                slot = self._slot(
                    transaction,
                    tenant_id=request.tenant_id,
                    owner_user_id=request.owner_user_id,
                    personal_memory_space_id=request.personal_memory_space_id,
                )
                if slot.state is not PersonalMemorySpaceState.DELETED:
                    raise PersonalMemoryStep32Error(Step32ReasonCode.DELETE_STATE_INVALID)
                return result, slot, True
            state = self._lifecycle.get_step30_state(
                transaction, request.tenant_id, request.owner_user_id, request.proposal_id
            )
            slot = self._slot(
                transaction,
                tenant_id=request.tenant_id,
                owner_user_id=request.owner_user_id,
                personal_memory_space_id=request.personal_memory_space_id,
            )
            if state is None:
                raise PersonalMemoryStep32Error(Step32ReasonCode.DELETE_STATE_INVALID)
            result = complete_logical_deletion(
                request,
                state,
                slot,
                authenticated_owner_user_id=authenticated_owner_user_id,
            )
            if self._lifecycle.lock_active_patch(
                transaction,
                tenant_id=request.tenant_id,
                owner_user_id=request.owner_user_id,
                personal_memory_space_id=request.personal_memory_space_id,
                patch_id=request.patch_id,
                patch_hash=request.patch_hash,
            ) is None:
                raise PersonalMemoryStep32Error(Step32ReasonCode.DELETE_STATE_INVALID)
            self._lifecycle.persist_deletion(transaction, result)
            policy = self._slots.get_quota_policy(
                transaction, slot.tenant_id, slot.owner_user_id, slot.quota_policy_id
            )
            if policy is None or policy.policy_digest != slot.quota_policy_digest:
                raise PersonalMemoryStep32Error(Step32ReasonCode.DELETE_STATE_INVALID)
            self._slots.delete_all_bindings(transaction, slot)
            configuration_version = slot.configuration_version
            if slot.model_bindings or slot.display_name is not None:
                configuration_version += 1
            deleted_slot = self._slots.update_slot(
                transaction,
                current=slot,
                state=PersonalMemorySpaceState.DELETED,
                display_name=None,
                quota_policy=policy,
                model_bindings=(),
                state_version=slot.state_version + 1,
                configuration_version=configuration_version,
                changed_at=request.requested_at,
                export_requested_at=slot.export_requested_at,
                deletion_requested_at=slot.deletion_requested_at,
                deleted_at=request.requested_at,
            )
            return result, deleted_slot, False

        return self._runner.run(
            _context(request.tenant_id, request.owner_user_id),
            work,
            operation_kind="PERSONAL_MEMORY_OWNER_DELETE",
        )

    def propose_shared(
        self,
        request: SharedMemoryPromotionRequest,
        *,
        authenticated_owner_user_id: str,
    ) -> tuple[SharedMemoryPromotionProposal, bool]:
        verify_shared_promotion_request(request)
        trusted_now = _trusted_now(self._clock)
        self._require_time(trusted_now, request.requested_at)
        if authenticated_owner_user_id != request.owner_user_id:
            raise PersonalMemoryStep32Error(Step32ReasonCode.SHARED_PROMOTION_OWNER_CONSENT_REQUIRED)

        def work(transaction: TransactionProtocol):
            state = self._lifecycle.get_step30_state(
                transaction, request.tenant_id, request.owner_user_id, request.source_proposal_id
            )
            if state is None:
                raise PersonalMemoryStep32Error(Step32ReasonCode.SHARED_PROMOTION_SOURCE_REVOKED)
            proposal = create_shared_promotion_proposal(
                request,
                state,
                authenticated_owner_user_id=authenticated_owner_user_id,
            )
            replay = self._lifecycle.get_promotion_replay(
                transaction,
                tenant_id=request.tenant_id,
                owner_user_id=request.owner_user_id,
                replay_identity=request.replay_identity,
            )
            if replay is not None:
                if replay["request_hash"] != request.request_hash or replay["proposal_hash"] != proposal.proposal_hash or replay["source_patch_id"] != request.source_patch_id:
                    raise PersonalMemoryStep32Error(Step32ReasonCode.REPLAY_CONFLICT)
                return proposal, True
            slot = self._slot(
                transaction,
                tenant_id=request.tenant_id,
                owner_user_id=request.owner_user_id,
                personal_memory_space_id=request.personal_memory_space_id,
            )
            if slot.state not in {PersonalMemorySpaceState.CONFIGURED, PersonalMemorySpaceState.ACTIVE}:
                raise PersonalMemoryStep32Error(Step32ReasonCode.SHARED_PROMOTION_SOURCE_DELETED)
            if self._lifecycle.lock_active_patch(
                transaction,
                tenant_id=request.tenant_id,
                owner_user_id=request.owner_user_id,
                personal_memory_space_id=request.personal_memory_space_id,
                patch_id=request.source_patch_id,
                patch_hash=request.source_patch_hash,
            ) is None:
                raise PersonalMemoryStep32Error(Step32ReasonCode.SHARED_PROMOTION_SOURCE_REVOKED)
            self._lifecycle.persist_promotion(transaction, proposal)
            return proposal, False

        return self._runner.run(
            _context(request.tenant_id, request.owner_user_id),
            work,
            operation_kind="PERSONAL_MEMORY_SHARED_PROMOTION_PROPOSAL",
        )


__all__ = ["PersonalMemoryLifecycle32Service", "Step32TrustedClock"]
