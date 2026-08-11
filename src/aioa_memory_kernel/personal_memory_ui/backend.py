"""Thin Step 35 adapter over the existing Step 27-33 services.

All reads are parameterized and execute under USER_PRIVATE request context.
All writes call the existing typed business services.  This module has no
Commit Helper, activation, source-publication, or direct state-setting API.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from aioa_memory_kernel.contracts.enums import PatchState, PersonalMemorySpaceState
from aioa_memory_kernel.persistence import (
    AccessMode,
    RequestContext,
    SerializableTransactionRunner,
)
from aioa_memory_kernel.persistence.errors import PersistenceConfigurationError
from aioa_memory_kernel.persistence.protocols import TransactionProtocol
from aioa_memory_kernel.personal_memory import (
    STEP27_SCHEMA_VERSION,
    ConfigureSlotCommand,
    ModelBindingCommand,
    PersonalMemoryApprovalService,
    PersonalMemoryBindingAction,
    PersonalMemoryBindingMode,
    PersonalMemoryHatSlot,
    PersonalMemoryLifecycle32Service,
    PersonalMemoryModelBinding,
    PersonalMemoryMutationActor,
    PersonalMemoryPersistenceError,
    PersonalMemoryPatchLifecycleCockroachRepository,
    PersonalMemoryPatchLifecycleState,
    PersonalMemoryPatchProposalState,
    PersonalMemoryService,
    Step32ActorType,
    Step32ReasonCode,
    TransitionSlotCommand,
    build_deletion_request,
    build_lifecycle_export_request,
    build_personal_memory_approval_request,
    build_revocation_request,
)
from aioa_memory_kernel.personal_memory.lifecycle_repository import (
    lifecycle_state_from_row,
)
from aioa_memory_kernel.personal_memory.repository import (
    PersonalMemoryCockroachRepository,
)

from .models import (
    AuditEventView,
    DashboardView,
    ModelBindingView,
    OwnerActionResult,
    OwnerPrincipal,
    PatchView,
    PersonalMemoryUiConflict,
    PersonalMemoryUiNotFound,
    QuotaView,
    SlotView,
    bounded_page_size,
    MAXIMUM_AUDIT_PAGE_SIZE,
    MAXIMUM_PATCH_PAGE_SIZE,
    MAXIMUM_SLOT_PAGE_SIZE,
)


_PROPOSAL_COLUMNS = """
    proposal.proposal_id, proposal.proposed_content,
    proposal.lifecycle_state, proposal.content_hash,
    proposal.step29_dedup_key, proposal.step29_state_version,
    proposal.step29_state_hash, proposal.step29_candidate_id,
    proposal.step29_candidate_hash,
    proposal.step29_candidate_envelope_hash,
    proposal.step29_target_binding_hash,
    proposal.step29_evidence_binding_hash,
    proposal.step29_validation_receipt_hash,
    proposal.step30_approval_id,
    proposal.step30_approval_receipt_hash,
    proposal.step30_commit_receipt_hash,
    proposal.step30_activation_receipt_hash,
    proposal.step30_patch_id,
    proposal.created_at,
    item.step32_terminal_kind,
    item.step32_terminal_record_hash,
    item.step32_superseded_by_patch_id,
    item.step32_effective_at
"""
_READ_NOT_FOUND = object()
_READ_CONFLICT = object()


def _database_timestamp(value: object, field_name: str) -> datetime:
    """Normalize timestamps returned by either DBAPI or HTTP SQL adapters."""

    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise PersistenceConfigurationError(
                f"{field_name} is not a timestamp",
                sanitized_code="INVALID_STEP35_UI_ROW",
            ) from error
    else:
        raise PersistenceConfigurationError(
            f"{field_name} is not a timestamp",
            sanitized_code="INVALID_STEP35_UI_ROW",
        )
    if parsed.tzinfo is None:
        raise PersistenceConfigurationError(
            f"{field_name} must be timezone-aware",
            sanitized_code="INVALID_STEP35_UI_ROW",
        )
    return parsed.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class _PatchRecord:
    state: PersonalMemoryPatchProposalState | PersonalMemoryPatchLifecycleState
    terminal_kind: str | None
    terminal_record_hash: str | None
    superseded_by_patch_id: str | None


class PersonalMemoryUiBackend(Protocol):
    def dashboard(self, principal: OwnerPrincipal) -> DashboardView:
        ...

    def slot_detail(self, principal: OwnerPrincipal, space_id: str) -> tuple[SlotView, tuple[PatchView, ...]]:
        ...

    def approve_proposal(self, principal: OwnerPrincipal, **values: object) -> OwnerActionResult:
        ...

    def configure_slot(self, principal: OwnerPrincipal, **values: object) -> OwnerActionResult:
        ...

    def transition_slot(self, principal: OwnerPrincipal, **values: object) -> OwnerActionResult:
        ...

    def update_model_binding(self, principal: OwnerPrincipal, **values: object) -> OwnerActionResult:
        ...

    def revoke_patch(self, principal: OwnerPrincipal, **values: object) -> OwnerActionResult:
        ...

    def export_slot(self, principal: OwnerPrincipal, **values: object) -> OwnerActionResult:
        ...

    def delete_patch(self, principal: OwnerPrincipal, **values: object) -> OwnerActionResult:
        ...


class PersonalMemoryUiReadRepository:
    """Owner-scoped bounded projections; there is no generic query API."""

    @staticmethod
    def list_patch_records(
        transaction: TransactionProtocol,
        *,
        tenant_id: str,
        owner_user_id: str,
        personal_memory_space_id: str | None,
        maximum_results: int,
    ) -> tuple[_PatchRecord, ...]:
        bounded_page_size(maximum_results, MAXIMUM_PATCH_PAGE_SIZE)
        sql = f"""
            SELECT {_PROPOSAL_COLUMNS}
              FROM memory_patch.memory_patch_proposals AS proposal
              LEFT JOIN memory_patch.memory_items AS item
                ON item.tenant_id = proposal.tenant_id
               AND item.step30_proposal_id = proposal.proposal_id
             WHERE proposal.tenant_id = %s
               AND proposal.owner_user_id = %s
               AND proposal.target_scope = 'USER_PERSONAL_HAT'
               AND proposal.proposed_content->>'contract_type' IN (
                 'PersonalMemoryPatchProposalState',
                 'PersonalMemoryPatchLifecycleState'
               )
        """
        parameters: list[object] = [tenant_id, owner_user_id]
        if personal_memory_space_id is not None:
            sql += " AND proposal.personal_memory_space_id = %s"
            parameters.append(personal_memory_space_id)
        sql += " ORDER BY proposal.created_at DESC, proposal.proposal_id LIMIT %s"
        parameters.append(maximum_results)
        rows = transaction.fetch_all(sql, tuple(parameters))
        result: list[_PatchRecord] = []
        for row in rows:
            result.append(
                _PatchRecord(
                    state=lifecycle_state_from_row(row),
                    terminal_kind=(
                        None
                        if row.get("step32_terminal_kind") is None
                        else str(row["step32_terminal_kind"])
                    ),
                    terminal_record_hash=(
                        None
                        if row.get("step32_terminal_record_hash") is None
                        else str(row["step32_terminal_record_hash"])
                    ),
                    superseded_by_patch_id=(
                        None
                        if row.get("step32_superseded_by_patch_id") is None
                        else str(row["step32_superseded_by_patch_id"])
                    ),
                )
            )
        return tuple(result)

    @staticmethod
    def list_audit_events(
        transaction: TransactionProtocol,
        *,
        tenant_id: str,
        owner_user_id: str,
        maximum_results: int,
    ) -> tuple[AuditEventView, ...]:
        bounded_page_size(maximum_results, MAXIMUM_AUDIT_PAGE_SIZE)
        rows = transaction.fetch_all(
            """
            SELECT event_id, event_type, subject_type, subject_id,
                   subject_hash, event_hash, sequence_number, occurred_at,
                   reason_codes
              FROM memory_patch.audit_events
             WHERE tenant_id = %s AND user_id = %s
               AND chain_id IS NOT NULL
             ORDER BY sequence_number DESC, event_id
             LIMIT %s
            """,
            (tenant_id, owner_user_id, maximum_results),
        )
        result: list[AuditEventView] = []
        for row in rows:
            reason_codes = row.get("reason_codes", ())
            if isinstance(reason_codes, str):
                reason_codes = json.loads(reason_codes)
            result.append(
                AuditEventView(
                    event_id=str(row["event_id"]),
                    event_type=str(row["event_type"]),
                    subject_type=str(row["subject_type"]),
                    subject_id=str(row["subject_id"]),
                    subject_hash=str(row["subject_hash"]),
                    event_hash=str(row["event_hash"]),
                    sequence_number=int(row["sequence_number"]),
                    occurred_at=_database_timestamp(
                        row["occurred_at"], "occurred_at"
                    ),
                    reason_codes=tuple(str(item) for item in reason_codes),
                )
            )
        return tuple(result)


class KernelPersonalMemoryUiBackend:
    """Production adapter; authenticated identity is never taken from forms."""

    def __init__(
        self,
        transaction_runner: SerializableTransactionRunner,
        *,
        personal_memory_service: PersonalMemoryService,
        approval_service: PersonalMemoryApprovalService,
        lifecycle_service: PersonalMemoryLifecycle32Service,
        slot_repository: PersonalMemoryCockroachRepository | None = None,
        lifecycle_repository: PersonalMemoryPatchLifecycleCockroachRepository | None = None,
        read_repository: PersonalMemoryUiReadRepository | None = None,
        trusted_now: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(transaction_runner, SerializableTransactionRunner):
            raise TypeError("transaction_runner must be SerializableTransactionRunner")
        self._runner = transaction_runner
        self._personal = personal_memory_service
        self._approval = approval_service
        self._lifecycle_service = lifecycle_service
        self._slots = slot_repository or PersonalMemoryCockroachRepository()
        self._lifecycle = lifecycle_repository or PersonalMemoryPatchLifecycleCockroachRepository()
        self._reads = read_repository or PersonalMemoryUiReadRepository()
        self._now = trusted_now or (lambda: datetime.now(UTC))

    @staticmethod
    def _context(principal: OwnerPrincipal) -> RequestContext:
        return RequestContext(
            tenant_id=principal.tenant_id,
            user_id=principal.owner_user_id,
            access_mode=AccessMode.USER_PRIVATE,
        )

    def _read(self, principal: OwnerPrincipal, callback, operation_kind: str):
        return self._runner.run(
            self._context(principal), callback, operation_kind=operation_kind
        )

    @staticmethod
    def _patch_view(record: _PatchRecord) -> PatchView:
        state = record.state
        base = state.step29_state if isinstance(state, PersonalMemoryPatchLifecycleState) else state
        proposal = base.proposal
        lifecycle = state if isinstance(state, PersonalMemoryPatchLifecycleState) else None
        committed = None if lifecycle is None else lifecycle.committed_patch
        displayed_state = record.terminal_kind or state.state.value
        validation = base.validation_receipt
        binding = base.evidence_binding
        scope = tuple(
            f"{dimension.name}={dimension.value}"
            for dimension in proposal.proposal_scope
        )
        return PatchView(
            proposal_id=proposal.proposal_id,
            proposal_hash=proposal.proposal_hash,
            personal_memory_space_id=proposal.personal_memory_space_id,
            statement=proposal.proposal_statement,
            statement_hash=proposal.proposal_statement_sha256,
            state=displayed_state,
            state_version=state.state_version,
            state_hash=state.state_hash,
            scope_summary=scope,
            model_binding_id=proposal.model_binding_id,
            validation_receipt_hash=(None if validation is None else validation.receipt_hash),
            evidence_binding_hash=(None if binding is None else binding.binding_hash),
            validation_summary=(
                "Validated against bound evidence"
                if validation is not None and validation.validated
                else "Not yet validated"
            ),
            limitations=() if binding is None else binding.limitations,
            patch_id=None if committed is None else committed.patch_id,
            patch_hash=None if committed is None else committed.patch_hash,
            approval_receipt_hash=(
                None if lifecycle is None else lifecycle.approval_receipt.receipt_hash
            ),
            activation_receipt_hash=(
                None
                if lifecycle is None or lifecycle.activation_receipt is None
                else lifecycle.activation_receipt.receipt_hash
            ),
            terminal_record_hash=record.terminal_record_hash,
            superseded_by_patch_id=record.superseded_by_patch_id,
            updated_at=state.updated_at,
        )

    @staticmethod
    def _quota_view(slot: PersonalMemoryHatSlot, usage, policy) -> QuotaView:
        return QuotaView(
            stored_bytes=usage.stored_bytes,
            maximum_bytes=policy.limits.maximum_bytes,
            active_patches=usage.usage.active_memory_patches,
            maximum_active_patches=policy.limits.maximum_active_memory_patches,
            model_bindings=usage.model_binding_count,
            maximum_model_bindings=policy.maximum_model_bindings_per_space,
            total_spaces=usage.usage.total_spaces,
            maximum_total_spaces=policy.limits.maximum_total_spaces,
        )

    @classmethod
    def _slot_view(cls, slot, usage, policy, patches: tuple[PatchView, ...]) -> SlotView:
        return SlotView(
            personal_memory_space_id=slot.personal_memory_space_id,
            display_name=slot.display_name or "Unnamed Personal Memory",
            state=slot.state.value,
            slot_hash=slot.slot_hash,
            state_version=slot.state_version,
            configuration_version=slot.configuration_version,
            updated_at=slot.updated_at,
            quota=cls._quota_view(slot, usage, policy),
            model_bindings=tuple(
                ModelBindingView(
                    binding_id=item.binding_id,
                    provider_id=item.provider_id,
                    model_id=item.model_id,
                    model_revision=item.model_revision_or_declared_version,
                    binding_mode=item.binding_mode.value,
                    enabled=item.enabled,
                    binding_hash=item.binding_hash,
                )
                for item in slot.model_bindings
            ),
            patch_count=len(patches),
            pending_approval_count=sum(
                item.state == PatchState.AWAITING_APPROVAL.value for item in patches
            ),
            active_patch_count=sum(item.state == PatchState.ACTIVE.value for item in patches),
        )

    def dashboard(self, principal: OwnerPrincipal) -> DashboardView:
        def work(transaction: TransactionProtocol):
            slots = self._slots.list_owner_slots(
                transaction, principal.tenant_id, principal.owner_user_id
            )[:MAXIMUM_SLOT_PAGE_SIZE]
            records = self._reads.list_patch_records(
                transaction,
                tenant_id=principal.tenant_id,
                owner_user_id=principal.owner_user_id,
                personal_memory_space_id=None,
                maximum_results=MAXIMUM_PATCH_PAGE_SIZE,
            )
            patches = tuple(self._patch_view(item) for item in records)
            per_slot = {
                slot.personal_memory_space_id: tuple(
                    patch
                    for patch in patches
                    if patch.personal_memory_space_id == slot.personal_memory_space_id
                )
                for slot in slots
            }
            views: list[SlotView] = []
            for slot in slots:
                usage = self._slots.owner_usage(
                    transaction,
                    tenant_id=principal.tenant_id,
                    owner_user_id=principal.owner_user_id,
                    personal_memory_space_id=slot.personal_memory_space_id,
                    quota_policy_digest=slot.quota_policy_digest,
                )
                policy = self._slots.get_quota_policy(
                    transaction,
                    principal.tenant_id,
                    principal.owner_user_id,
                    slot.quota_policy_id,
                )
                if policy is None:
                    return _READ_CONFLICT
                views.append(self._slot_view(slot, usage, policy, per_slot[slot.personal_memory_space_id]))
            audit = self._reads.list_audit_events(
                transaction,
                tenant_id=principal.tenant_id,
                owner_user_id=principal.owner_user_id,
                maximum_results=20,
            )
            return DashboardView(
                slots=tuple(views),
                pending_approvals=tuple(
                    item for item in patches if item.state == PatchState.AWAITING_APPROVAL.value
                ),
                recent_patches=patches,
                recent_audit_events=audit,
                slot_count=len(views),
                active_slot_count=sum(item.state == PersonalMemorySpaceState.ACTIVE.value for item in views),
                pending_approval_count=sum(item.state == PatchState.AWAITING_APPROVAL.value for item in patches),
                active_patch_count=sum(item.state == PatchState.ACTIVE.value for item in patches),
                superseded_patch_count=sum(item.state == PatchState.SUPERSEDED.value for item in patches),
                revoked_patch_count=sum(item.state == PatchState.REVOKED.value for item in patches),
            )

        result = self._read(principal, work, "STEP35_OWNER_DASHBOARD")
        if result is _READ_CONFLICT:
            raise PersonalMemoryUiConflict()
        return result

    def slot_detail(
        self, principal: OwnerPrincipal, space_id: str
    ) -> tuple[SlotView, tuple[PatchView, ...]]:
        def work(transaction: TransactionProtocol):
            slot = self._slots.get_slot(
                transaction, principal.tenant_id, principal.owner_user_id, space_id
            )
            if slot is None:
                return _READ_NOT_FOUND
            records = self._reads.list_patch_records(
                transaction,
                tenant_id=principal.tenant_id,
                owner_user_id=principal.owner_user_id,
                personal_memory_space_id=space_id,
                maximum_results=MAXIMUM_PATCH_PAGE_SIZE,
            )
            patches = tuple(self._patch_view(item) for item in records)
            usage = self._slots.owner_usage(
                transaction,
                tenant_id=principal.tenant_id,
                owner_user_id=principal.owner_user_id,
                personal_memory_space_id=space_id,
                quota_policy_digest=slot.quota_policy_digest,
            )
            policy = self._slots.get_quota_policy(
                transaction,
                principal.tenant_id,
                principal.owner_user_id,
                slot.quota_policy_id,
            )
            if policy is None:
                return _READ_CONFLICT
            return self._slot_view(slot, usage, policy, patches), patches

        result = self._read(principal, work, "STEP35_OWNER_SLOT_DETAIL")
        if result is _READ_NOT_FOUND:
            raise PersonalMemoryUiNotFound()
        if result is _READ_CONFLICT:
            raise PersonalMemoryUiConflict()
        return result

    @staticmethod
    def _exact(value: object, expected: object) -> None:
        if value != expected:
            raise PersonalMemoryUiConflict()

    def _state(self, principal: OwnerPrincipal, proposal_id: str):
        def work(transaction: TransactionProtocol):
            return self._lifecycle.get_state(
                transaction, principal.tenant_id, principal.owner_user_id, proposal_id
            )

        state = self._read(principal, work, "STEP35_OWNER_PATCH_READ")
        if state is None:
            raise PersonalMemoryUiNotFound()
        return state

    def _slot(self, principal: OwnerPrincipal, space_id: str) -> PersonalMemoryHatSlot:
        def work(transaction: TransactionProtocol):
            return self._slots.get_slot(
                transaction, principal.tenant_id, principal.owner_user_id, space_id
            )

        slot = self._read(principal, work, "STEP35_OWNER_SLOT_READ")
        if slot is None:
            raise PersonalMemoryUiNotFound()
        return slot

    def approve_proposal(self, principal: OwnerPrincipal, **values: object) -> OwnerActionResult:
        proposal_id = str(values["proposal_id"])
        current = self._state(principal, proposal_id)
        if not isinstance(current, PersonalMemoryPatchProposalState):
            raise PersonalMemoryUiConflict()
        self._exact(values.get("proposal_hash"), current.proposal.proposal_hash)
        self._exact(int(values["expected_state_version"]), current.state_version)
        self._exact(values.get("expected_state_hash"), current.state_hash)
        if current.state is not PatchState.AWAITING_APPROVAL:
            raise PersonalMemoryUiConflict()
        request = build_personal_memory_approval_request(
            current,
            approval_nonce=str(values["idempotency_key"]),
            requested_at=self._now(),
        )
        updated, receipt, replayed = self._approval.approve(
            request, authenticated_actor_user_id=principal.owner_user_id
        )
        return OwnerActionResult(
            action="APPROVE",
            subject_id=proposal_id,
            resulting_state=updated.state.value,
            receipt_hash=receipt.receipt_hash,
            replayed=replayed,
            message="Owner approval recorded. Technical commit and activation remain separate.",
        )

    def configure_slot(self, principal: OwnerPrincipal, **values: object) -> OwnerActionResult:
        space_id = str(values["space_id"])
        slot = self._slot(principal, space_id)
        self._exact(values.get("slot_hash"), slot.slot_hash)

        def policy_work(transaction: TransactionProtocol):
            return self._slots.get_quota_policy(
                transaction, principal.tenant_id, principal.owner_user_id, slot.quota_policy_id
            )

        policy = self._read(principal, policy_work, "STEP35_SLOT_POLICY_READ")
        if policy is None:
            raise PersonalMemoryUiConflict()
        command = ConfigureSlotCommand(
            schema_version=STEP27_SCHEMA_VERSION,
            tenant_id=principal.tenant_id,
            owner_user_id=principal.owner_user_id,
            personal_memory_space_id=space_id,
            display_name=str(values["display_name"]),
            quota_policy=policy,
            expected_state_version=int(values["expected_state_version"]),
            expected_configuration_version=int(values["expected_configuration_version"]),
            idempotency_key=str(values["idempotency_key"]),
            requested_at=self._now(),
            actor=PersonalMemoryMutationActor.OWNER_CONFIGURATION,
        )
        updated, receipt = self._personal.configure_slot(command)
        return OwnerActionResult(
            action="CONFIGURE_SLOT",
            subject_id=space_id,
            resulting_state=updated.state.value,
            receipt_hash=receipt.result_hash,
            replayed=receipt.replayed,
            message="Slot configuration saved.",
        )

    def transition_slot(self, principal: OwnerPrincipal, **values: object) -> OwnerActionResult:
        space_id = str(values["space_id"])
        slot = self._slot(principal, space_id)
        self._exact(values.get("slot_hash"), slot.slot_hash)
        command = TransitionSlotCommand(
            schema_version=STEP27_SCHEMA_VERSION,
            tenant_id=principal.tenant_id,
            owner_user_id=principal.owner_user_id,
            personal_memory_space_id=space_id,
            target_state=PersonalMemorySpaceState(str(values["target_state"])),
            expected_state_version=int(values["expected_state_version"]),
            expected_configuration_version=int(values["expected_configuration_version"]),
            idempotency_key=str(values["idempotency_key"]),
            requested_at=self._now(),
            actor=PersonalMemoryMutationActor.OWNER_CONFIGURATION,
        )
        updated, receipt = self._personal.transition_slot(command)
        return OwnerActionResult(
            action="TRANSITION_SLOT",
            subject_id=space_id,
            resulting_state=updated.state.value,
            receipt_hash=receipt.result_hash,
            replayed=receipt.replayed,
            message=f"Slot is now {updated.state.value}.",
        )

    def update_model_binding(
        self, principal: OwnerPrincipal, **values: object
    ) -> OwnerActionResult:
        space_id = str(values["space_id"])
        slot = self._slot(principal, space_id)
        self._exact(values.get("slot_hash"), slot.slot_hash)
        self._exact(int(values["expected_state_version"]), slot.state_version)
        self._exact(
            int(values["expected_configuration_version"]),
            slot.configuration_version,
        )
        action = PersonalMemoryBindingAction(str(values["action"]))
        if action is PersonalMemoryBindingAction.ADD:
            binding = PersonalMemoryModelBinding(
                schema_version=STEP27_SCHEMA_VERSION,
                tenant_id=principal.tenant_id,
                owner_user_id=principal.owner_user_id,
                personal_memory_space_id=space_id,
                provider_id=str(values["provider_id"]),
                model_id=str(values["model_id"]),
                model_revision_or_declared_version=str(values["model_revision"]),
                binding_mode=PersonalMemoryBindingMode.EXACT_MODEL,
                enabled=True,
                binding_version=1,
                bound_at=self._now(),
            )
        else:
            binding_id = str(values["binding_id"])
            matches = tuple(
                item for item in slot.model_bindings if item.binding_id == binding_id
            )
            if len(matches) != 1:
                raise PersonalMemoryUiConflict()
            binding = matches[0]
            self._exact(values.get("binding_hash"), binding.binding_hash)
        command = ModelBindingCommand(
            schema_version=STEP27_SCHEMA_VERSION,
            tenant_id=principal.tenant_id,
            owner_user_id=principal.owner_user_id,
            personal_memory_space_id=space_id,
            binding=binding,
            action=action,
            expected_state_version=slot.state_version,
            expected_configuration_version=slot.configuration_version,
            idempotency_key=str(values["idempotency_key"]),
            requested_at=self._now(),
            actor=PersonalMemoryMutationActor.OWNER_CONFIGURATION,
        )
        try:
            updated, receipt = self._personal.update_model_binding(command)
        except PersonalMemoryPersistenceError as error:
            # Quota, concurrent configuration and invalid binding failures are
            # safe owner-visible conflicts; never serialize database details.
            raise PersonalMemoryUiConflict() from error
        return OwnerActionResult(
            action=f"MODEL_BINDING_{action.value}",
            subject_id=binding.binding_id,
            resulting_state=updated.state.value,
            receipt_hash=receipt.result_hash,
            replayed=receipt.replayed,
            message=(
                "Model binding added."
                if action is PersonalMemoryBindingAction.ADD
                else "Model binding removed."
            ),
        )

    def revoke_patch(self, principal: OwnerPrincipal, **values: object) -> OwnerActionResult:
        proposal_id = str(values["proposal_id"])
        state = self._state(principal, proposal_id)
        if not isinstance(state, PersonalMemoryPatchLifecycleState) or state.committed_patch is None:
            raise PersonalMemoryUiConflict()
        self._exact(values.get("state_hash"), state.state_hash)
        self._exact(values.get("patch_hash"), state.committed_patch.patch_hash)
        self._exact(int(values["expected_state_version"]), state.state_version)
        request = build_revocation_request(
            state,
            reason_codes=(Step32ReasonCode.REVOCATION_CREATED,),
            effective_at=self._now(),
            idempotency_key=str(values["idempotency_key"]),
        )
        record, replayed = self._lifecycle_service.revoke(
            request,
            actor_type=Step32ActorType.HUMAN_OWNER,
            authenticated_actor_id=principal.owner_user_id,
        )
        return OwnerActionResult(
            action="REVOKE_PATCH",
            subject_id=record.patch_id,
            resulting_state=record.state.value,
            receipt_hash=record.revocation_hash,
            replayed=replayed,
            message="Patch revoked. Revocation is not deletion.",
        )

    def export_slot(self, principal: OwnerPrincipal, **values: object) -> OwnerActionResult:
        space_id = str(values["space_id"])
        slot = self._slot(principal, space_id)
        self._exact(values.get("slot_hash"), slot.slot_hash)
        self._exact(int(values["expected_state_version"]), slot.state_version)
        self._exact(int(values["expected_configuration_version"]), slot.configuration_version)
        request = build_lifecycle_export_request(
            slot, requested_at=self._now(), idempotency_key=str(values["idempotency_key"])
        )
        bundle, replayed = self._lifecycle_service.export(
            request, authenticated_owner_user_id=principal.owner_user_id
        )
        return OwnerActionResult(
            action="EXPORT_SLOT",
            subject_id=space_id,
            resulting_state="EXPORT_READY",
            receipt_hash=bundle.bundle_hash,
            replayed=replayed,
            message=f"Private canonical JSON export is ready ({len(bundle.records)} records).",
        )

    def delete_patch(self, principal: OwnerPrincipal, **values: object) -> OwnerActionResult:
        proposal_id = str(values["proposal_id"])
        state = self._state(principal, proposal_id)
        if not isinstance(state, PersonalMemoryPatchLifecycleState) or state.committed_patch is None:
            raise PersonalMemoryUiConflict()
        slot = self._slot(principal, state.proposal.personal_memory_space_id)
        self._exact(values.get("state_hash"), state.state_hash)
        self._exact(values.get("patch_hash"), state.committed_patch.patch_hash)
        self._exact(values.get("slot_hash"), slot.slot_hash)
        request = build_deletion_request(
            state,
            slot,
            requested_at=self._now(),
            idempotency_key=str(values["idempotency_key"]),
        )
        result, _, replayed = self._lifecycle_service.delete(
            request, authenticated_owner_user_id=principal.owner_user_id
        )
        return OwnerActionResult(
            action="DELETE_PATCH",
            subject_id=result.patch_id,
            resulting_state="DELETED",
            receipt_hash=result.result_hash,
            replayed=replayed,
            message="Logical deletion completed; no physical deletion is claimed.",
        )


__all__ = [
    "KernelPersonalMemoryUiBackend",
    "PersonalMemoryUiBackend",
    "PersonalMemoryUiReadRepository",
]
