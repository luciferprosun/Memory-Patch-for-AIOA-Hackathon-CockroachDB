"""Transactional Step 27 Personal Memory configuration service."""

from __future__ import annotations

from dataclasses import replace

from aioa_memory_kernel.contracts.enums import PersonalMemorySpaceState
from aioa_memory_kernel.contracts.exceptions import QuotaExceeded
from aioa_memory_kernel.contracts.personal_memory import PersonalHatQuotaUsage
from aioa_memory_kernel.persistence import (
    AccessMode,
    BeginOperation,
    IdempotencyService,
    RequestContext,
    SerializableTransactionRunner,
)
from aioa_memory_kernel.persistence.protocols import TransactionProtocol
from aioa_memory_kernel.state_machines.personal_memory import (
    personal_memory_transition_allowed,
)

from .export import build_personal_memory_export
from .models import (
    STEP27_SCHEMA_VERSION,
    ConfigureSlotCommand,
    CreateEmptySlotCommand,
    ExportSlotCommand,
    ModelBindingCommand,
    PersonalMemoryBindingAction,
    PersonalMemoryHatSlot,
    PersonalMemoryOperationKind,
    PersonalMemoryPersistenceError,
    PersonalMemoryQuotaPolicyRecord,
    PersonalMemoryQuotaUsageView,
    PersonalMemoryReasonCode,
    PersonalMemorySlotExport,
    SlotMutationReceipt,
    TransitionSlotCommand,
    enforce_step27_quota,
    operation_id_for_command,
    owner_scope_digest,
    verify_slot_hash,
)
from .repository import PersonalMemoryCockroachRepository


_BINDING_STATES = frozenset(
    {
        PersonalMemorySpaceState.CONFIGURED,
        PersonalMemorySpaceState.ACTIVE,
        PersonalMemorySpaceState.SUSPENDED,
    }
)


def _context(tenant_id: str, owner_user_id: str) -> RequestContext:
    return RequestContext(
        tenant_id=tenant_id,
        user_id=owner_user_id,
        access_mode=AccessMode.USER_PRIVATE,
    )


def _require_versions(
    slot: PersonalMemoryHatSlot,
    *,
    state_version: int,
    configuration_version: int,
) -> None:
    if (
        slot.state_version != state_version
        or slot.configuration_version != configuration_version
    ):
        raise PersonalMemoryPersistenceError(
            PersonalMemoryReasonCode.CONFIGURATION_CONFLICT
        )


def _receipt(
    *,
    operation_id: str,
    operation_kind: PersonalMemoryOperationKind,
    reason_code: PersonalMemoryReasonCode,
    command_hash: str,
    slot: PersonalMemoryHatSlot,
    replayed: bool,
) -> SlotMutationReceipt:
    return SlotMutationReceipt(
        schema_version=STEP27_SCHEMA_VERSION,
        operation_id=operation_id,
        operation_kind=operation_kind,
        reason_code=reason_code,
        tenant_id=slot.tenant_id,
        owner_user_id=slot.owner_user_id,
        personal_memory_space_id=slot.personal_memory_space_id,
        command_hash=command_hash,
        slot_hash=slot.slot_hash,
        state=slot.state,
        state_version=slot.state_version,
        configuration_version=slot.configuration_version,
        replayed=replayed,
    )


class PersonalMemoryService:
    """Explicit owner operations with short SERIALIZABLE transactions."""

    def __init__(
        self,
        transaction_runner: SerializableTransactionRunner,
        *,
        repository: PersonalMemoryCockroachRepository | None = None,
        idempotency: IdempotencyService | None = None,
    ) -> None:
        if not isinstance(transaction_runner, SerializableTransactionRunner):
            raise TypeError("transaction_runner must be SerializableTransactionRunner")
        self._runner = transaction_runner
        self._repository = repository or PersonalMemoryCockroachRepository()
        self._idempotency = idempotency or IdempotencyService()

    @staticmethod
    def _begin(
        idempotency: IdempotencyService,
        transaction: TransactionProtocol,
        command: object,
        operation_kind: PersonalMemoryOperationKind,
    ):
        tenant_id = getattr(command, "tenant_id")
        owner_user_id = getattr(command, "owner_user_id")
        space_id = getattr(command, "personal_memory_space_id")
        idempotency_key = getattr(command, "idempotency_key")
        command_hash = getattr(command, "command_hash")
        requested_at = getattr(command, "requested_at")
        operation_id = operation_id_for_command(
            operation_kind,
            tenant_id,
            owner_user_id,
            idempotency_key,
        )
        claim = idempotency.begin_or_resume_operation(
            transaction,
            BeginOperation(
                operation_id=operation_id,
                tenant_id=tenant_id,
                owner_user_id=owner_user_id,
                operation_kind=f"PERSONAL_MEMORY_{operation_kind.value}",
                idempotency_key=idempotency_key,
                request_digest=command_hash,
                scope_digest=owner_scope_digest(
                    tenant_id, owner_user_id, space_id
                ),
                created_at=requested_at,
            ),
        )
        return operation_id, claim

    def _completed_replay(
        self,
        transaction: TransactionProtocol,
        command: object,
        operation_id: str,
        operation_kind: PersonalMemoryOperationKind,
        reason_code: PersonalMemoryReasonCode,
    ) -> SlotMutationReceipt:
        slot = self._repository.get_slot(
            transaction,
            getattr(command, "tenant_id"),
            getattr(command, "owner_user_id"),
            getattr(command, "personal_memory_space_id"),
        )
        if slot is None:
            raise PersonalMemoryPersistenceError(
                PersonalMemoryReasonCode.SLOT_NOT_FOUND
            )
        verify_slot_hash(slot)
        return _receipt(
            operation_id=operation_id,
            operation_kind=operation_kind,
            reason_code=reason_code,
            command_hash=getattr(command, "command_hash"),
            slot=slot,
            replayed=True,
        )

    def _complete(
        self,
        transaction: TransactionProtocol,
        claim: object,
        receipt: SlotMutationReceipt,
    ) -> None:
        operation = getattr(claim, "operation")
        self._idempotency.complete_operation(
            transaction,
            tenant_id=receipt.tenant_id,
            operation_id=operation.operation_id,
            expected_attempt_count=operation.attempt_count,
            result_ref=receipt.personal_memory_space_id,
            result_digest=receipt.result_hash,
        )

    def create_empty_slot(
        self,
        command: CreateEmptySlotCommand,
    ) -> tuple[PersonalMemoryHatSlot, SlotMutationReceipt]:
        if not isinstance(command, CreateEmptySlotCommand):
            raise TypeError("command must be CreateEmptySlotCommand")

        def work(transaction: TransactionProtocol):
            operation_id, claim = self._begin(
                self._idempotency,
                transaction,
                command,
                PersonalMemoryOperationKind.CREATE_EMPTY_SLOT,
            )
            if not claim.may_proceed:
                receipt = self._completed_replay(
                    transaction,
                    command,
                    operation_id,
                    PersonalMemoryOperationKind.CREATE_EMPTY_SLOT,
                    PersonalMemoryReasonCode.SLOT_ALREADY_EXISTS_EXACT_REPLAY,
                )
                slot = self._repository.get_slot(
                    transaction,
                    command.tenant_id,
                    command.owner_user_id,
                    command.personal_memory_space_id,
                )
                assert slot is not None
                return slot, receipt
            policy = self._repository.insert_quota_policy(
                transaction, command.quota_policy, command.requested_at
            )
            usage = self._repository.owner_usage(
                transaction,
                tenant_id=command.tenant_id,
                owner_user_id=command.owner_user_id,
                personal_memory_space_id=command.personal_memory_space_id,
                quota_policy_digest=policy.policy_digest,
            )
            candidate_usage = replace(
                usage,
                usage=replace(
                    usage.usage,
                    total_spaces=usage.usage.total_spaces + 1,
                ),
            )
            try:
                enforce_step27_quota(policy, candidate_usage)
            except QuotaExceeded as error:
                raise PersonalMemoryPersistenceError(
                    PersonalMemoryReasonCode.QUOTA_EXCEEDED
                ) from error
            slot = self._repository.create_empty_slot(
                transaction,
                tenant_id=command.tenant_id,
                owner_user_id=command.owner_user_id,
                personal_memory_space_id=command.personal_memory_space_id,
                quota_policy=policy,
                created_at=command.requested_at,
            )
            receipt = _receipt(
                operation_id=operation_id,
                operation_kind=PersonalMemoryOperationKind.CREATE_EMPTY_SLOT,
                reason_code=PersonalMemoryReasonCode.SLOT_CREATED,
                command_hash=command.command_hash,
                slot=slot,
                replayed=False,
            )
            self._complete(transaction, claim, receipt)
            return slot, receipt

        return self._runner.run(
            _context(command.tenant_id, command.owner_user_id),
            work,
            operation_kind="PERSONAL_MEMORY_CREATE_EMPTY_SLOT",
        )

    def read_slot(
        self,
        *,
        tenant_id: str,
        owner_user_id: str,
        personal_memory_space_id: str,
    ) -> PersonalMemoryHatSlot:
        def work(transaction: TransactionProtocol) -> PersonalMemoryHatSlot:
            slot = self._repository.get_slot(
                transaction, tenant_id, owner_user_id, personal_memory_space_id
            )
            if slot is None:
                raise PersonalMemoryPersistenceError(
                    PersonalMemoryReasonCode.SLOT_NOT_FOUND
                )
            verify_slot_hash(slot)
            return slot

        return self._runner.run(
            _context(tenant_id, owner_user_id),
            work,
            operation_kind="PERSONAL_MEMORY_READ_SLOT",
        )

    def list_owner_slots(
        self,
        *,
        tenant_id: str,
        owner_user_id: str,
    ) -> tuple[PersonalMemoryHatSlot, ...]:
        return self._runner.run(
            _context(tenant_id, owner_user_id),
            lambda transaction: self._repository.list_owner_slots(
                transaction, tenant_id, owner_user_id
            ),
            operation_kind="PERSONAL_MEMORY_LIST_OWNER_SLOTS",
        )

    def quota_usage(
        self,
        *,
        tenant_id: str,
        owner_user_id: str,
        personal_memory_space_id: str,
    ) -> PersonalMemoryQuotaUsageView:
        def work(transaction: TransactionProtocol) -> PersonalMemoryQuotaUsageView:
            slot = self._repository.get_slot(
                transaction, tenant_id, owner_user_id, personal_memory_space_id
            )
            if slot is None:
                raise PersonalMemoryPersistenceError(
                    PersonalMemoryReasonCode.SLOT_NOT_FOUND
                )
            return self._repository.owner_usage(
                transaction,
                tenant_id=tenant_id,
                owner_user_id=owner_user_id,
                personal_memory_space_id=personal_memory_space_id,
                quota_policy_digest=slot.quota_policy_digest,
            )

        return self._runner.run(
            _context(tenant_id, owner_user_id),
            work,
            operation_kind="PERSONAL_MEMORY_QUOTA_USAGE",
        )

    def configure_slot(
        self,
        command: ConfigureSlotCommand,
    ) -> tuple[PersonalMemoryHatSlot, SlotMutationReceipt]:
        if not isinstance(command, ConfigureSlotCommand):
            raise TypeError("command must be ConfigureSlotCommand")

        def work(transaction: TransactionProtocol):
            operation_id, claim = self._begin(
                self._idempotency,
                transaction,
                command,
                PersonalMemoryOperationKind.CONFIGURE_SLOT,
            )
            if not claim.may_proceed:
                receipt = self._completed_replay(
                    transaction,
                    command,
                    operation_id,
                    PersonalMemoryOperationKind.CONFIGURE_SLOT,
                    PersonalMemoryReasonCode.SLOT_CONFIGURATION_UPDATED,
                )
                slot = self._repository.get_slot(
                    transaction,
                    command.tenant_id,
                    command.owner_user_id,
                    command.personal_memory_space_id,
                )
                assert slot is not None
                return slot, receipt
            slot = self._require_slot(transaction, command)
            _require_versions(
                slot,
                state_version=command.expected_state_version,
                configuration_version=command.expected_configuration_version,
            )
            if slot.state in {
                PersonalMemorySpaceState.DELETED_PENDING,
                PersonalMemorySpaceState.DELETED,
            }:
                raise PersonalMemoryPersistenceError(
                    PersonalMemoryReasonCode.INVALID_STATE_TRANSITION
                )
            policy = self._repository.insert_quota_policy(
                transaction, command.quota_policy, command.requested_at
            )
            usage = self._repository.owner_usage(
                transaction,
                tenant_id=slot.tenant_id,
                owner_user_id=slot.owner_user_id,
                personal_memory_space_id=slot.personal_memory_space_id,
                quota_policy_digest=policy.policy_digest,
            )
            try:
                enforce_step27_quota(policy, usage)
            except QuotaExceeded as error:
                raise PersonalMemoryPersistenceError(
                    PersonalMemoryReasonCode.QUOTA_EXCEEDED
                ) from error
            next_state = (
                PersonalMemorySpaceState.CONFIGURED
                if slot.state is PersonalMemorySpaceState.EMPTY
                else slot.state
            )
            updated = self._repository.update_slot(
                transaction,
                current=slot,
                state=next_state,
                display_name=command.display_name,
                quota_policy=policy,
                model_bindings=slot.model_bindings,
                state_version=slot.state_version
                + (1 if next_state is not slot.state else 0),
                configuration_version=slot.configuration_version + 1,
                changed_at=command.requested_at,
                export_requested_at=slot.export_requested_at,
                deletion_requested_at=slot.deletion_requested_at,
                deleted_at=slot.deleted_at,
            )
            receipt = _receipt(
                operation_id=operation_id,
                operation_kind=PersonalMemoryOperationKind.CONFIGURE_SLOT,
                reason_code=PersonalMemoryReasonCode.SLOT_CONFIGURATION_UPDATED,
                command_hash=command.command_hash,
                slot=updated,
                replayed=False,
            )
            self._complete(transaction, claim, receipt)
            return updated, receipt

        return self._runner.run(
            _context(command.tenant_id, command.owner_user_id),
            work,
            operation_kind="PERSONAL_MEMORY_CONFIGURE_SLOT",
        )

    def transition_slot(
        self,
        command: TransitionSlotCommand,
    ) -> tuple[PersonalMemoryHatSlot, SlotMutationReceipt]:
        if not isinstance(command, TransitionSlotCommand):
            raise TypeError("command must be TransitionSlotCommand")

        def work(transaction: TransactionProtocol):
            operation_id, claim = self._begin(
                self._idempotency,
                transaction,
                command,
                PersonalMemoryOperationKind.TRANSITION_SLOT,
            )
            reason = self._transition_reason(command.target_state)
            if not claim.may_proceed:
                receipt = self._completed_replay(
                    transaction,
                    command,
                    operation_id,
                    PersonalMemoryOperationKind.TRANSITION_SLOT,
                    reason,
                )
                slot = self._repository.get_slot(
                    transaction,
                    command.tenant_id,
                    command.owner_user_id,
                    command.personal_memory_space_id,
                )
                assert slot is not None
                return slot, receipt
            slot = self._require_slot(transaction, command)
            _require_versions(
                slot,
                state_version=command.expected_state_version,
                configuration_version=command.expected_configuration_version,
            )
            if not personal_memory_transition_allowed(
                slot.state, command.target_state
            ):
                raise PersonalMemoryPersistenceError(
                    PersonalMemoryReasonCode.INVALID_STATE_TRANSITION
                )
            policy = self._require_policy(transaction, slot)
            bindings = slot.model_bindings
            display_name = slot.display_name
            configuration_version = slot.configuration_version
            deletion_requested_at = slot.deletion_requested_at
            deleted_at = slot.deleted_at
            if command.target_state is PersonalMemorySpaceState.DELETED_PENDING:
                deletion_requested_at = command.requested_at
            elif command.target_state is PersonalMemorySpaceState.DELETED:
                self._repository.delete_all_bindings(transaction, slot)
                if bindings or display_name is not None:
                    configuration_version += 1
                bindings = ()
                display_name = None
                deleted_at = command.requested_at
            updated = self._repository.update_slot(
                transaction,
                current=slot,
                state=command.target_state,
                display_name=display_name,
                quota_policy=policy,
                model_bindings=bindings,
                state_version=slot.state_version + 1,
                configuration_version=configuration_version,
                changed_at=command.requested_at,
                export_requested_at=slot.export_requested_at,
                deletion_requested_at=deletion_requested_at,
                deleted_at=deleted_at,
            )
            usage = self._repository.owner_usage(
                transaction,
                tenant_id=slot.tenant_id,
                owner_user_id=slot.owner_user_id,
                personal_memory_space_id=slot.personal_memory_space_id,
                quota_policy_digest=policy.policy_digest,
            )
            try:
                enforce_step27_quota(policy, usage)
            except QuotaExceeded as error:
                raise PersonalMemoryPersistenceError(
                    PersonalMemoryReasonCode.QUOTA_EXCEEDED
                ) from error
            receipt = _receipt(
                operation_id=operation_id,
                operation_kind=PersonalMemoryOperationKind.TRANSITION_SLOT,
                reason_code=reason,
                command_hash=command.command_hash,
                slot=updated,
                replayed=False,
            )
            self._complete(transaction, claim, receipt)
            return updated, receipt

        return self._runner.run(
            _context(command.tenant_id, command.owner_user_id),
            work,
            operation_kind="PERSONAL_MEMORY_TRANSITION_SLOT",
        )

    def update_model_binding(
        self,
        command: ModelBindingCommand,
    ) -> tuple[PersonalMemoryHatSlot, SlotMutationReceipt]:
        if not isinstance(command, ModelBindingCommand):
            raise TypeError("command must be ModelBindingCommand")

        def work(transaction: TransactionProtocol):
            operation_id, claim = self._begin(
                self._idempotency,
                transaction,
                command,
                PersonalMemoryOperationKind.UPDATE_MODEL_BINDING,
            )
            reason = (
                PersonalMemoryReasonCode.MODEL_BINDING_ADDED
                if command.action is PersonalMemoryBindingAction.ADD
                else PersonalMemoryReasonCode.MODEL_BINDING_REMOVED
            )
            if not claim.may_proceed:
                receipt = self._completed_replay(
                    transaction,
                    command,
                    operation_id,
                    PersonalMemoryOperationKind.UPDATE_MODEL_BINDING,
                    reason,
                )
                slot = self._repository.get_slot(
                    transaction,
                    command.tenant_id,
                    command.owner_user_id,
                    command.personal_memory_space_id,
                )
                assert slot is not None
                return slot, receipt
            slot = self._require_slot(transaction, command)
            _require_versions(
                slot,
                state_version=command.expected_state_version,
                configuration_version=command.expected_configuration_version,
            )
            if slot.state not in _BINDING_STATES:
                raise PersonalMemoryPersistenceError(
                    PersonalMemoryReasonCode.INVALID_STATE_TRANSITION
                )
            policy = self._require_policy(transaction, slot)
            by_id = {item.binding_id: item for item in slot.model_bindings}
            if command.action is PersonalMemoryBindingAction.ADD:
                if command.binding.binding_id in by_id:
                    if (
                        by_id[command.binding.binding_id].binding_hash
                        != command.binding.binding_hash
                    ):
                        raise PersonalMemoryPersistenceError(
                            PersonalMemoryReasonCode.MODEL_BINDING_INVALID
                        )
                    next_bindings = slot.model_bindings
                else:
                    if (
                        len(slot.model_bindings) + 1
                        > policy.maximum_model_bindings_per_space
                    ):
                        raise PersonalMemoryPersistenceError(
                            PersonalMemoryReasonCode.MODEL_BINDING_LIMIT_EXCEEDED
                        )
                    self._repository.insert_binding(transaction, command.binding)
                    next_bindings = tuple(
                        sorted(
                            slot.model_bindings + (command.binding,),
                            key=lambda item: item.binding_id,
                        )
                    )
            else:
                if command.binding.binding_id not in by_id:
                    raise PersonalMemoryPersistenceError(
                        PersonalMemoryReasonCode.MODEL_BINDING_INVALID
                    )
                self._repository.delete_binding(transaction, command.binding)
                next_bindings = tuple(
                    item
                    for item in slot.model_bindings
                    if item.binding_id != command.binding.binding_id
                )
            updated = self._repository.update_slot(
                transaction,
                current=slot,
                state=slot.state,
                display_name=slot.display_name,
                quota_policy=policy,
                model_bindings=next_bindings,
                state_version=slot.state_version,
                configuration_version=slot.configuration_version + 1,
                changed_at=command.requested_at,
                export_requested_at=slot.export_requested_at,
                deletion_requested_at=slot.deletion_requested_at,
                deleted_at=slot.deleted_at,
            )
            receipt = _receipt(
                operation_id=operation_id,
                operation_kind=PersonalMemoryOperationKind.UPDATE_MODEL_BINDING,
                reason_code=reason,
                command_hash=command.command_hash,
                slot=updated,
                replayed=False,
            )
            self._complete(transaction, claim, receipt)
            return updated, receipt

        return self._runner.run(
            _context(command.tenant_id, command.owner_user_id),
            work,
            operation_kind="PERSONAL_MEMORY_UPDATE_MODEL_BINDING",
        )

    def request_export(
        self,
        command: ExportSlotCommand,
    ) -> tuple[PersonalMemorySlotExport, SlotMutationReceipt]:
        if not isinstance(command, ExportSlotCommand):
            raise TypeError("command must be ExportSlotCommand")

        def work(transaction: TransactionProtocol):
            operation_id, claim = self._begin(
                self._idempotency,
                transaction,
                command,
                PersonalMemoryOperationKind.REQUEST_EXPORT,
            )
            if not claim.may_proceed:
                receipt = self._completed_replay(
                    transaction,
                    command,
                    operation_id,
                    PersonalMemoryOperationKind.REQUEST_EXPORT,
                    PersonalMemoryReasonCode.EXPORT_READY,
                )
                slot = self._require_slot(transaction, command)
                usage = self._repository.owner_usage(
                    transaction,
                    tenant_id=slot.tenant_id,
                    owner_user_id=slot.owner_user_id,
                    personal_memory_space_id=slot.personal_memory_space_id,
                    quota_policy_digest=slot.quota_policy_digest,
                )
                return build_personal_memory_export(command, slot, usage), receipt
            slot = self._require_slot(transaction, command)
            _require_versions(
                slot,
                state_version=command.expected_state_version,
                configuration_version=command.expected_configuration_version,
            )
            if slot.state is PersonalMemorySpaceState.DELETED:
                raise PersonalMemoryPersistenceError(
                    PersonalMemoryReasonCode.DELETE_STATE_INVALID
                )
            policy = self._require_policy(transaction, slot)
            updated = self._repository.update_slot(
                transaction,
                current=slot,
                state=slot.state,
                display_name=slot.display_name,
                quota_policy=policy,
                model_bindings=slot.model_bindings,
                state_version=slot.state_version,
                configuration_version=slot.configuration_version,
                changed_at=command.requested_at,
                export_requested_at=command.requested_at,
                deletion_requested_at=slot.deletion_requested_at,
                deleted_at=slot.deleted_at,
            )
            usage = self._repository.owner_usage(
                transaction,
                tenant_id=updated.tenant_id,
                owner_user_id=updated.owner_user_id,
                personal_memory_space_id=updated.personal_memory_space_id,
                quota_policy_digest=updated.quota_policy_digest,
            )
            exported = build_personal_memory_export(command, updated, usage)
            receipt = _receipt(
                operation_id=operation_id,
                operation_kind=PersonalMemoryOperationKind.REQUEST_EXPORT,
                reason_code=PersonalMemoryReasonCode.EXPORT_READY,
                command_hash=command.command_hash,
                slot=updated,
                replayed=False,
            )
            self._complete(transaction, claim, receipt)
            return exported, receipt

        return self._runner.run(
            _context(command.tenant_id, command.owner_user_id),
            work,
            operation_kind="PERSONAL_MEMORY_REQUEST_EXPORT",
        )

    def _require_slot(
        self,
        transaction: TransactionProtocol,
        command: object,
    ) -> PersonalMemoryHatSlot:
        slot = self._repository.get_slot(
            transaction,
            getattr(command, "tenant_id"),
            getattr(command, "owner_user_id"),
            getattr(command, "personal_memory_space_id"),
        )
        if slot is None:
            raise PersonalMemoryPersistenceError(
                PersonalMemoryReasonCode.SLOT_NOT_FOUND
            )
        verify_slot_hash(slot)
        return slot

    def _require_policy(
        self,
        transaction: TransactionProtocol,
        slot: PersonalMemoryHatSlot,
    ) -> PersonalMemoryQuotaPolicyRecord:
        policy = self._repository.get_quota_policy(
            transaction,
            slot.tenant_id,
            slot.owner_user_id,
            slot.quota_policy_id,
        )
        if policy is None or policy.policy_digest != slot.quota_policy_digest:
            raise PersonalMemoryPersistenceError(
                PersonalMemoryReasonCode.QUOTA_POLICY_INVALID
            )
        return policy

    @staticmethod
    def _transition_reason(
        target: PersonalMemorySpaceState,
    ) -> PersonalMemoryReasonCode:
        return {
            PersonalMemorySpaceState.ACTIVE: PersonalMemoryReasonCode.SLOT_ACTIVATED,
            PersonalMemorySpaceState.SUSPENDED: (
                PersonalMemoryReasonCode.SLOT_DEACTIVATED
            ),
            PersonalMemorySpaceState.ARCHIVED: PersonalMemoryReasonCode.SLOT_ARCHIVED,
            PersonalMemorySpaceState.DELETED_PENDING: (
                PersonalMemoryReasonCode.DELETE_REQUESTED
            ),
            PersonalMemorySpaceState.DELETED: PersonalMemoryReasonCode.DELETE_COMPLETED,
            PersonalMemorySpaceState.CONFIGURED: (
                PersonalMemoryReasonCode.SLOT_CONFIGURATION_UPDATED
            ),
        }.get(target, PersonalMemoryReasonCode.INVALID_STATE_TRANSITION)
