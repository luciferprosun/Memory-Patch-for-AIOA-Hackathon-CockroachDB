"""Step 27 Personal Memory HAT persistence, quota, and isolation tests."""

from __future__ import annotations

import dataclasses
import inspect
import json
import re
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tests._support import REPOSITORY_ROOT

from aioa_memory_kernel.contracts.enums import PersonalMemorySpaceState
from aioa_memory_kernel.contracts.exceptions import (
    ContractValidationError,
    IntegrityError,
    QuotaExceeded,
)
from aioa_memory_kernel.contracts.personal_memory import (
    PersonalHatQuotaPolicy,
    PersonalHatQuotaUsage,
)
from aioa_memory_kernel.contracts.serialization import canonical_sha256
from aioa_memory_kernel.persistence import (
    IdempotencyConflictError,
    PersistenceError,
    SerializableTransactionRunner,
)
from aioa_memory_kernel.personal_memory import (
    STEP27_SCHEMA_VERSION,
    ConfigureSlotCommand,
    CreateEmptySlotCommand,
    ExportSlotCommand,
    ModelBindingCommand,
    PersonalMemoryBindingAction,
    PersonalMemoryBindingMode,
    PersonalMemoryHatSlot,
    PersonalMemoryModelBinding,
    PersonalMemoryMutationActor,
    PersonalMemoryPersistenceError,
    PersonalMemoryQuotaPolicyRecord,
    PersonalMemoryQuotaUsageView,
    PersonalMemoryReasonCode,
    PersonalMemoryService,
    TransitionSlotCommand,
    build_personal_memory_export,
    enforce_step27_quota,
    personal_memory_hat_scope_id,
    verify_export_hash,
    verify_model_binding_hash,
    verify_quota_policy_hash,
    verify_receipt_hash,
    verify_slot_hash,
    verify_usage_hash,
)
from aioa_memory_kernel.personal_memory.repository import (
    PersonalMemoryCockroachRepository,
    slot_from_row,
)


ROOT = REPOSITORY_ROOT
NOW = datetime(2042, 1, 2, 3, 4, 5, tzinfo=UTC)


def quota_policy(
    *,
    tenant_id: str = "tenant-a",
    owner_user_id: str = "user-a",
    total_spaces: int = 4,
    active_spaces: int = 2,
    archived_spaces: int = 3,
    bindings: int = 2,
    policy_id: str = "quota-owner-a-v1",
) -> PersonalMemoryQuotaPolicyRecord:
    return PersonalMemoryQuotaPolicyRecord(
        schema_version=STEP27_SCHEMA_VERSION,
        tenant_id=tenant_id,
        owner_user_id=owner_user_id,
        quota_policy_id=policy_id,
        quota_policy_version="1",
        limits=PersonalHatQuotaPolicy(
            maximum_total_spaces=total_spaces,
            maximum_active_spaces=active_spaces,
            maximum_archived_spaces=archived_spaces,
            maximum_bytes=4096,
            maximum_personal_sources=4,
            maximum_active_memory_patches=4,
            maximum_session_memory_bytes=4096,
            maximum_ingestion_jobs=2,
            maximum_embedding_or_index_bytes=4096,
        ),
        maximum_model_bindings_per_space=bindings,
    )


def binding(
    *,
    provider_id: str = "provider-a",
    model_id: str = "model-a",
    revision: str = "revision-1",
    tenant_id: str = "tenant-a",
    owner_user_id: str = "user-a",
    slot_id: str = "slot-a",
    at: datetime = NOW,
) -> PersonalMemoryModelBinding:
    return PersonalMemoryModelBinding(
        schema_version=STEP27_SCHEMA_VERSION,
        tenant_id=tenant_id,
        owner_user_id=owner_user_id,
        personal_memory_space_id=slot_id,
        provider_id=provider_id,
        model_id=model_id,
        model_revision_or_declared_version=revision,
        binding_mode=PersonalMemoryBindingMode.EXACT_MODEL,
        enabled=True,
        binding_version=1,
        bound_at=at,
    )


def empty_slot(
    *,
    tenant_id: str = "tenant-a",
    owner_user_id: str = "user-a",
    slot_id: str = "slot-a",
    policy: PersonalMemoryQuotaPolicyRecord | None = None,
    at: datetime = NOW,
) -> PersonalMemoryHatSlot:
    policy = policy or quota_policy(
        tenant_id=tenant_id, owner_user_id=owner_user_id
    )
    return PersonalMemoryHatSlot(
        schema_version=STEP27_SCHEMA_VERSION,
        tenant_id=tenant_id,
        owner_user_id=owner_user_id,
        personal_memory_space_id=slot_id,
        hat_scope_id=personal_memory_hat_scope_id(
            tenant_id, owner_user_id, slot_id
        ),
        state=PersonalMemorySpaceState.EMPTY,
        display_name=None,
        quota_policy_id=policy.quota_policy_id,
        quota_policy_digest=policy.policy_digest,
        model_bindings=(),
        state_version=0,
        configuration_version=0,
        created_at=at,
        updated_at=at,
    )


def persisted_slot_row(
    slot: PersonalMemoryHatSlot,
    *,
    hat_scope_id: str | None | object = ...,
    slot_hash: str | None | object = ...,
) -> dict[str, object]:
    """Render the Step 27 slot columns, including the Step 28 authority tuple."""

    return {
        "tenant_id": slot.tenant_id,
        "user_id": slot.owner_user_id,
        "personal_memory_space_id": slot.personal_memory_space_id,
        "schema_version": slot.schema_version,
        "state": slot.state.value,
        "display_name": slot.display_name,
        "created_at": slot.created_at,
        "updated_at": slot.updated_at,
        "export_requested_at": slot.export_requested_at,
        "deletion_requested_at": slot.deletion_requested_at,
        "deleted_at": slot.deleted_at,
        "state_version": slot.state_version,
        "configuration_version": slot.configuration_version,
        "quota_policy_id": slot.quota_policy_id,
        "quota_policy_digest": slot.quota_policy_digest,
        "configuration_digest": slot.configuration_digest,
        "hat_scope_id": slot.hat_scope_id if hat_scope_id is ... else hat_scope_id,
        "slot_hash": slot.slot_hash if slot_hash is ... else slot_hash,
    }


class FakeIdempotency:
    def __init__(self) -> None:
        self.operations: dict[tuple[str, str, str, str], dict[str, object]] = {}

    def begin_or_resume_operation(self, transaction: object, request: object):
        key = (
            request.tenant_id,
            request.owner_user_id,
            request.operation_kind,
            request.idempotency_key,
        )
        existing = self.operations.get(key)
        if existing is not None:
            if (
                existing["request_digest"] != request.request_digest
                or existing["scope_digest"] != request.scope_digest
            ):
                raise IdempotencyConflictError(
                    "conflicting test replay", sanitized_code="TEST_CONFLICT"
                )
            return SimpleNamespace(
                may_proceed=not existing["completed"],
                operation=SimpleNamespace(
                    operation_id=request.operation_id,
                    attempt_count=1,
                ),
            )
        self.operations[key] = {
            "completed": False,
            "request_digest": request.request_digest,
            "scope_digest": request.scope_digest,
        }
        return SimpleNamespace(
            may_proceed=True,
            operation=SimpleNamespace(
                operation_id=request.operation_id,
                attempt_count=1,
            ),
        )

    def complete_operation(self, transaction: object, **values: object):
        for operation in self.operations.values():
            if not operation["completed"]:
                operation["completed"] = True
                break
        return values


class InMemoryRepository:
    def __init__(self) -> None:
        self.slots: dict[tuple[str, str, str], PersonalMemoryHatSlot] = {}
        self.policies: dict[
            tuple[str, str, str], PersonalMemoryQuotaPolicyRecord
        ] = {}

    @staticmethod
    def key(tenant_id: str, owner: str, slot_id: str):
        return tenant_id, owner, slot_id

    def get_quota_policy(self, transaction: object, tenant: str, owner: str, policy_id: str):
        return self.policies.get((tenant, owner, policy_id))

    def insert_quota_policy(self, transaction: object, policy: PersonalMemoryQuotaPolicyRecord, created_at: datetime):
        key = (policy.tenant_id, policy.owner_user_id, policy.quota_policy_id)
        existing = self.policies.get(key)
        if existing is not None and existing.policy_digest != policy.policy_digest:
            raise RuntimeError("conflicting policy")
        self.policies[key] = policy
        return policy

    def get_slot(self, transaction: object, tenant: str, owner: str, slot_id: str):
        return self.slots.get(self.key(tenant, owner, slot_id))

    def list_owner_slots(self, transaction: object, tenant: str, owner: str):
        return tuple(
            sorted(
                (
                    value
                    for key, value in self.slots.items()
                    if key[:2] == (tenant, owner)
                ),
                key=lambda item: item.personal_memory_space_id,
            )
        )

    def create_empty_slot(self, transaction: object, *, tenant_id: str, owner_user_id: str, personal_memory_space_id: str, quota_policy: PersonalMemoryQuotaPolicyRecord, created_at: datetime):
        key = self.key(tenant_id, owner_user_id, personal_memory_space_id)
        self.slots.setdefault(
            key,
            empty_slot(
                tenant_id=tenant_id,
                owner_user_id=owner_user_id,
                slot_id=personal_memory_space_id,
                policy=quota_policy,
                at=created_at,
            ),
        )
        return self.slots[key]

    def owner_usage(self, transaction: object, *, tenant_id: str, owner_user_id: str, personal_memory_space_id: str, quota_policy_digest: str):
        spaces = [
            slot
            for key, slot in self.slots.items()
            if key[:2] == (tenant_id, owner_user_id)
            and slot.state is not PersonalMemorySpaceState.DELETED
        ]
        target = self.get_slot(
            transaction, tenant_id, owner_user_id, personal_memory_space_id
        )
        return PersonalMemoryQuotaUsageView(
            schema_version=STEP27_SCHEMA_VERSION,
            tenant_id=tenant_id,
            owner_user_id=owner_user_id,
            personal_memory_space_id=personal_memory_space_id,
            quota_policy_digest=quota_policy_digest,
            usage=PersonalHatQuotaUsage(
                total_spaces=len(spaces),
                active_spaces=sum(
                    item.state is PersonalMemorySpaceState.ACTIVE for item in spaces
                ),
                archived_spaces=sum(
                    item.state is PersonalMemorySpaceState.ARCHIVED
                    for item in spaces
                ),
            ),
            model_binding_count=(0 if target is None else len(target.model_bindings)),
            memory_item_count=0,
            patch_count=0,
            stored_bytes=0,
        )

    def update_slot(self, transaction: object, *, current: PersonalMemoryHatSlot, state: PersonalMemorySpaceState, display_name: str | None, quota_policy: PersonalMemoryQuotaPolicyRecord, model_bindings: tuple[PersonalMemoryModelBinding, ...], state_version: int, configuration_version: int, changed_at: datetime, export_requested_at: datetime | None, deletion_requested_at: datetime | None, deleted_at: datetime | None):
        key = self.key(
            current.tenant_id,
            current.owner_user_id,
            current.personal_memory_space_id,
        )
        if self.slots.get(key) != current:
            raise RuntimeError("test compare-and-set conflict")
        updated = PersonalMemoryHatSlot(
            schema_version=STEP27_SCHEMA_VERSION,
            tenant_id=current.tenant_id,
            owner_user_id=current.owner_user_id,
            personal_memory_space_id=current.personal_memory_space_id,
            hat_scope_id=current.hat_scope_id,
            state=state,
            display_name=display_name,
            quota_policy_id=quota_policy.quota_policy_id,
            quota_policy_digest=quota_policy.policy_digest,
            model_bindings=model_bindings,
            state_version=state_version,
            configuration_version=configuration_version,
            created_at=current.created_at,
            updated_at=changed_at,
            export_requested_at=export_requested_at,
            deletion_requested_at=deletion_requested_at,
            deleted_at=deleted_at,
        )
        self.slots[key] = updated
        return updated

    def insert_binding(self, transaction: object, value: PersonalMemoryModelBinding):
        return True

    def delete_binding(self, transaction: object, value: PersonalMemoryModelBinding):
        return True

    def delete_all_bindings(self, transaction: object, slot: PersonalMemoryHatSlot):
        return None


class ServiceHarness:
    def __init__(self) -> None:
        self.repository = InMemoryRepository()
        self.idempotency = FakeIdempotency()
        self.runner = SerializableTransactionRunner(lambda: None)  # patched below
        self.runner_patch = mock.patch.object(
            self.runner,
            "run",
            side_effect=lambda context, callback, **kwargs: callback(object()),
        )
        self.runner_patch.start()
        self.service = PersonalMemoryService(
            self.runner,
            repository=self.repository,  # type: ignore[arg-type]
            idempotency=self.idempotency,  # type: ignore[arg-type]
        )

    def close(self) -> None:
        self.runner_patch.stop()

    def create(self, *, slot_id: str = "slot-a", key: str = "create-a", policy: PersonalMemoryQuotaPolicyRecord | None = None, at: datetime = NOW):
        policy = policy or quota_policy()
        return self.service.create_empty_slot(
            CreateEmptySlotCommand(
                schema_version=STEP27_SCHEMA_VERSION,
                tenant_id=policy.tenant_id,
                owner_user_id=policy.owner_user_id,
                personal_memory_space_id=slot_id,
                quota_policy=policy,
                idempotency_key=key,
                requested_at=at,
                actor=PersonalMemoryMutationActor.OWNER_CONFIGURATION,
            )
        )

    def configure(self, slot: PersonalMemoryHatSlot, *, key: str, at: datetime, display: str = "Private memory", policy: PersonalMemoryQuotaPolicyRecord | None = None):
        policy = policy or self.repository.policies[
            (slot.tenant_id, slot.owner_user_id, slot.quota_policy_id)
        ]
        return self.service.configure_slot(
            ConfigureSlotCommand(
                schema_version=STEP27_SCHEMA_VERSION,
                tenant_id=slot.tenant_id,
                owner_user_id=slot.owner_user_id,
                personal_memory_space_id=slot.personal_memory_space_id,
                display_name=display,
                quota_policy=policy,
                expected_state_version=slot.state_version,
                expected_configuration_version=slot.configuration_version,
                idempotency_key=key,
                requested_at=at,
                actor=PersonalMemoryMutationActor.OWNER_CONFIGURATION,
            )
        )

    def transition(self, slot: PersonalMemoryHatSlot, target: PersonalMemorySpaceState, *, key: str, at: datetime):
        return self.service.transition_slot(
            TransitionSlotCommand(
                schema_version=STEP27_SCHEMA_VERSION,
                tenant_id=slot.tenant_id,
                owner_user_id=slot.owner_user_id,
                personal_memory_space_id=slot.personal_memory_space_id,
                target_state=target,
                expected_state_version=slot.state_version,
                expected_configuration_version=slot.configuration_version,
                idempotency_key=key,
                requested_at=at,
                actor=PersonalMemoryMutationActor.TRUSTED_APPLICATION,
            )
        )


class ContractTests(unittest.TestCase):
    def test_domain_rejection_survives_transaction_rollback(self) -> None:
        error = PersonalMemoryPersistenceError(
            PersonalMemoryReasonCode.QUOTA_EXCEEDED
        )
        self.assertIsInstance(error, PersistenceError)
        self.assertEqual(error.sanitized_code, "QUOTA_EXCEEDED")

    def test_empty_slot_is_valid_zero_content_and_non_authoritative(self) -> None:
        policy = quota_policy()
        slot = empty_slot(policy=policy)
        usage = PersonalMemoryQuotaUsageView(
            schema_version=STEP27_SCHEMA_VERSION,
            tenant_id="tenant-a",
            owner_user_id="user-a",
            personal_memory_space_id="slot-a",
            quota_policy_digest=policy.policy_digest,
            usage=PersonalHatQuotaUsage(),
            model_binding_count=0,
            memory_item_count=0,
            patch_count=0,
            stored_bytes=0,
        )
        self.assertEqual(usage.memory_item_count, 0)
        self.assertEqual(usage.patch_count, 0)
        self.assertEqual(usage.stored_bytes, 0)
        self.assertFalse(slot.executable)
        self.assertFalse(slot.canonical_evidence)
        verify_slot_hash(slot)
        verify_usage_hash(usage)

    def test_owner_and_tenant_are_required(self) -> None:
        for field_name in ("tenant_id", "owner_user_id"):
            values = {field_name: ""}
            with self.subTest(field_name=field_name):
                with self.assertRaises(ContractValidationError):
                    empty_slot(**values)

    def test_slot_and_configuration_hashes_are_deterministic(self) -> None:
        first = empty_slot()
        second = empty_slot()
        self.assertEqual(first.configuration_digest, second.configuration_digest)
        self.assertEqual(first.slot_hash, second.slot_hash)
        changed = empty_slot(slot_id="slot-b")
        self.assertNotEqual(first.slot_hash, changed.slot_hash)

    def test_contracts_are_immutable(self) -> None:
        for value in (quota_policy(), empty_slot(), binding()):
            with self.subTest(type=type(value).__name__):
                with self.assertRaises(dataclasses.FrozenInstanceError):
                    value.tenant_id = "changed"  # type: ignore[misc]

    def test_quota_policy_is_explicit_versioned_and_hash_bound(self) -> None:
        first = quota_policy()
        second = quota_policy()
        changed = quota_policy(bindings=1)
        self.assertEqual(first.policy_digest, second.policy_digest)
        self.assertNotEqual(first.policy_digest, changed.policy_digest)
        verify_quota_policy_hash(first)

    def test_negative_or_unset_quota_fails_closed(self) -> None:
        values = {
            name: 1 for name in PersonalHatQuotaPolicy.__dataclass_fields__
        }
        values["maximum_bytes"] = -1
        with self.assertRaises(ContractValidationError):
            quota_policy().limits.__class__(**values)
        values["maximum_bytes"] = None
        with self.assertRaises(ContractValidationError):
            PersonalMemoryQuotaPolicyRecord(
                schema_version=STEP27_SCHEMA_VERSION,
                tenant_id="tenant-a",
                owner_user_id="user-a",
                quota_policy_id="unset-policy",
                quota_policy_version="1",
                limits=PersonalHatQuotaPolicy(**values),
                maximum_model_bindings_per_space=1,
            )

    def test_quota_boundary_is_hard(self) -> None:
        policy = quota_policy(total_spaces=1)
        at_limit = PersonalMemoryQuotaUsageView(
            schema_version=STEP27_SCHEMA_VERSION,
            tenant_id="tenant-a",
            owner_user_id="user-a",
            personal_memory_space_id="slot-a",
            quota_policy_digest=policy.policy_digest,
            usage=PersonalHatQuotaUsage(total_spaces=1),
            model_binding_count=0,
            memory_item_count=0,
            patch_count=0,
            stored_bytes=0,
        )
        enforce_step27_quota(policy, at_limit)
        with self.assertRaises(QuotaExceeded):
            enforce_step27_quota(
                policy,
                replace(
                    at_limit,
                    usage=replace(at_limit.usage, total_spaces=2),
                ),
            )

    def test_provider_neutral_model_binding_accepts_two_models(self) -> None:
        first = binding(provider_id="provider-a", model_id="alpha")
        second = binding(provider_id="provider-b", model_id="beta")
        self.assertNotEqual(first.binding_id, second.binding_id)
        self.assertNotEqual(first.binding_hash, second.binding_hash)
        verify_model_binding_hash(first)
        self.assertFalse(hasattr(first, "api_key"))
        self.assertFalse(hasattr(first, "database_credentials"))

    def test_binding_cross_owner_cannot_enter_slot(self) -> None:
        policy = quota_policy()
        with self.assertRaises(ContractValidationError):
            PersonalMemoryHatSlot(
                schema_version=STEP27_SCHEMA_VERSION,
                tenant_id="tenant-a",
                owner_user_id="user-a",
                personal_memory_space_id="slot-a",
                hat_scope_id=personal_memory_hat_scope_id(
                    "tenant-a", "user-a", "slot-a"
                ),
                state=PersonalMemorySpaceState.CONFIGURED,
                display_name="Private",
                quota_policy_id=policy.quota_policy_id,
                quota_policy_digest=policy.policy_digest,
                model_bindings=(binding(owner_user_id="user-b"),),
                state_version=1,
                configuration_version=1,
                created_at=NOW,
                updated_at=NOW,
            )


class ServiceLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.harness = ServiceHarness()

    def tearDown(self) -> None:
        self.harness.close()

    def test_create_empty_slot_and_exact_replay(self) -> None:
        first, first_receipt = self.harness.create()
        replay, replay_receipt = self.harness.create()
        self.assertEqual(first.personal_memory_space_id, replay.personal_memory_space_id)
        self.assertEqual(len(self.harness.repository.slots), 1)
        self.assertFalse(first_receipt.replayed)
        self.assertTrue(replay_receipt.replayed)
        self.assertEqual(
            replay_receipt.reason_code,
            PersonalMemoryReasonCode.SLOT_ALREADY_EXISTS_EXACT_REPLAY,
        )
        verify_receipt_hash(first_receipt)

    def test_conflicting_idempotency_replay_is_rejected(self) -> None:
        self.harness.create()
        with self.assertRaises(IdempotencyConflictError):
            self.harness.create(policy=quota_policy(bindings=1))

    def test_total_slot_quota_is_transactional_and_fail_closed(self) -> None:
        policy = quota_policy(total_spaces=1)
        self.harness.create(policy=policy)
        with self.assertRaises(PersonalMemoryPersistenceError) as caught:
            self.harness.create(
                slot_id="slot-b", key="create-b", policy=policy, at=NOW + timedelta(seconds=1)
            )
        self.assertEqual(
            caught.exception.reason_code, PersonalMemoryReasonCode.QUOTA_EXCEEDED
        )
        self.assertEqual(len(self.harness.repository.slots), 1)

    def test_configure_activate_deactivate_archive_lifecycle(self) -> None:
        slot, _ = self.harness.create()
        slot, _ = self.harness.configure(
            slot, key="configure", at=NOW + timedelta(seconds=1)
        )
        self.assertEqual(slot.state, PersonalMemorySpaceState.CONFIGURED)
        slot, _ = self.harness.transition(
            slot,
            PersonalMemorySpaceState.ACTIVE,
            key="activate",
            at=NOW + timedelta(seconds=2),
        )
        self.assertTrue(slot.retrieval_eligible)
        self.assertFalse(slot.executable)
        slot, _ = self.harness.transition(
            slot,
            PersonalMemorySpaceState.SUSPENDED,
            key="deactivate",
            at=NOW + timedelta(seconds=3),
        )
        self.assertFalse(slot.retrieval_eligible)
        slot, _ = self.harness.transition(
            slot,
            PersonalMemorySpaceState.ARCHIVED,
            key="archive",
            at=NOW + timedelta(seconds=4),
        )
        self.assertEqual(slot.state, PersonalMemorySpaceState.ARCHIVED)
        self.assertIsNotNone(slot.display_name)

    def test_invalid_transition_and_stale_version_fail(self) -> None:
        slot, _ = self.harness.create()
        with self.assertRaises(PersonalMemoryPersistenceError):
            self.harness.transition(
                slot,
                PersonalMemorySpaceState.ACTIVE,
                key="bad-active",
                at=NOW + timedelta(seconds=1),
            )
        configured, _ = self.harness.configure(
            slot, key="configure", at=NOW + timedelta(seconds=1)
        )
        with self.assertRaises(PersonalMemoryPersistenceError) as caught:
            self.harness.configure(
                slot,
                key="stale-configure",
                at=NOW + timedelta(seconds=2),
            )
        self.assertEqual(
            caught.exception.reason_code,
            PersonalMemoryReasonCode.CONFIGURATION_CONFLICT,
        )
        self.assertEqual(configured.state_version, 1)

    def test_binding_add_remove_and_limit(self) -> None:
        policy = quota_policy(bindings=1)
        slot, _ = self.harness.create(policy=policy)
        slot, _ = self.harness.configure(
            slot, key="configure", at=NOW + timedelta(seconds=1), policy=policy
        )
        first = binding(at=NOW + timedelta(seconds=2))
        slot, receipt = self.harness.service.update_model_binding(
            ModelBindingCommand(
                schema_version=STEP27_SCHEMA_VERSION,
                tenant_id="tenant-a",
                owner_user_id="user-a",
                personal_memory_space_id="slot-a",
                binding=first,
                action=PersonalMemoryBindingAction.ADD,
                expected_state_version=slot.state_version,
                expected_configuration_version=slot.configuration_version,
                idempotency_key="bind-a",
                requested_at=NOW + timedelta(seconds=2),
                actor=PersonalMemoryMutationActor.OWNER_CONFIGURATION,
            )
        )
        self.assertEqual(len(slot.model_bindings), 1)
        self.assertEqual(
            receipt.reason_code, PersonalMemoryReasonCode.MODEL_BINDING_ADDED
        )
        second = binding(
            provider_id="provider-b",
            model_id="model-b",
            at=NOW + timedelta(seconds=3),
        )
        with self.assertRaises(PersonalMemoryPersistenceError) as caught:
            self.harness.service.update_model_binding(
                ModelBindingCommand(
                    schema_version=STEP27_SCHEMA_VERSION,
                    tenant_id="tenant-a",
                    owner_user_id="user-a",
                    personal_memory_space_id="slot-a",
                    binding=second,
                    action=PersonalMemoryBindingAction.ADD,
                    expected_state_version=slot.state_version,
                    expected_configuration_version=slot.configuration_version,
                    idempotency_key="bind-b",
                    requested_at=NOW + timedelta(seconds=3),
                    actor=PersonalMemoryMutationActor.OWNER_CONFIGURATION,
                )
            )
        self.assertEqual(
            caught.exception.reason_code,
            PersonalMemoryReasonCode.MODEL_BINDING_LIMIT_EXCEEDED,
        )
        slot, _ = self.harness.service.update_model_binding(
            ModelBindingCommand(
                schema_version=STEP27_SCHEMA_VERSION,
                tenant_id="tenant-a",
                owner_user_id="user-a",
                personal_memory_space_id="slot-a",
                binding=first,
                action=PersonalMemoryBindingAction.REMOVE,
                expected_state_version=slot.state_version,
                expected_configuration_version=slot.configuration_version,
                idempotency_key="unbind-a",
                requested_at=NOW + timedelta(seconds=4),
                actor=PersonalMemoryMutationActor.TRUSTED_APPLICATION,
            )
        )
        self.assertEqual(slot.model_bindings, ())

    def test_model_binding_does_not_grant_read_write_or_approval(self) -> None:
        value = binding()
        fields = {item.name for item in dataclasses.fields(value)}
        self.assertFalse(
            fields
            & {
                "read_authority",
                "write_authority",
                "approval_authority",
                "commit_authority",
                "execution_authority",
            }
        )

    def test_cross_owner_and_cross_tenant_reads_are_invisible(self) -> None:
        self.harness.create()
        for tenant, owner in (
            ("tenant-a", "user-b"),
            ("tenant-b", "user-c"),
        ):
            with self.subTest(tenant=tenant, owner=owner):
                with self.assertRaises(PersonalMemoryPersistenceError):
                    self.harness.service.read_slot(
                        tenant_id=tenant,
                        owner_user_id=owner,
                        personal_memory_space_id="slot-a",
                    )
        self.assertEqual(
            len(
                self.harness.service.list_owner_slots(
                    tenant_id="tenant-a", owner_user_id="user-b"
                )
            ),
            0,
        )

    def test_export_is_owner_scoped_deterministic_and_config_only(self) -> None:
        slot, _ = self.harness.create()
        slot, _ = self.harness.configure(
            slot, key="configure", at=NOW + timedelta(seconds=1)
        )
        command = ExportSlotCommand(
            schema_version=STEP27_SCHEMA_VERSION,
            tenant_id="tenant-a",
            owner_user_id="user-a",
            personal_memory_space_id="slot-a",
            expected_state_version=slot.state_version,
            expected_configuration_version=slot.configuration_version,
            idempotency_key="export-a",
            requested_at=NOW + timedelta(seconds=2),
            actor=PersonalMemoryMutationActor.OWNER_CONFIGURATION,
        )
        exported, _ = self.harness.service.request_export(command)
        replay, receipt = self.harness.service.request_export(command)
        self.assertEqual(exported.export_digest, replay.export_digest)
        self.assertTrue(receipt.replayed)
        data = json.loads(exported.canonical_text())
        self.assertNotIn("patches", data)
        self.assertNotIn("provider_credentials", data)
        self.assertNotIn("database_credentials", data)
        verify_export_hash(exported)

    def test_cross_owner_export_builder_fails(self) -> None:
        policy = quota_policy()
        slot = empty_slot(policy=policy)
        usage = PersonalMemoryQuotaUsageView(
            schema_version=STEP27_SCHEMA_VERSION,
            tenant_id="tenant-a",
            owner_user_id="user-a",
            personal_memory_space_id="slot-a",
            quota_policy_digest=policy.policy_digest,
            usage=PersonalHatQuotaUsage(total_spaces=1),
            model_binding_count=0,
            memory_item_count=0,
            patch_count=0,
            stored_bytes=0,
        )
        command = ExportSlotCommand(
            schema_version=STEP27_SCHEMA_VERSION,
            tenant_id="tenant-a",
            owner_user_id="user-b",
            personal_memory_space_id="slot-a",
            expected_state_version=0,
            expected_configuration_version=0,
            idempotency_key="cross-export",
            requested_at=NOW,
            actor=PersonalMemoryMutationActor.OWNER_CONFIGURATION,
        )
        with self.assertRaises(PersonalMemoryPersistenceError):
            build_personal_memory_export(command, slot, usage)

    def test_archive_and_delete_are_distinct_logical_states(self) -> None:
        slot, _ = self.harness.create()
        slot, _ = self.harness.configure(
            slot, key="configure", at=NOW + timedelta(seconds=1)
        )
        archived, _ = self.harness.transition(
            slot,
            PersonalMemorySpaceState.ARCHIVED,
            key="archive",
            at=NOW + timedelta(seconds=2),
        )
        self.assertIsNone(archived.deleted_at)
        pending, _ = self.harness.transition(
            archived,
            PersonalMemorySpaceState.DELETED_PENDING,
            key="delete-request",
            at=NOW + timedelta(seconds=3),
        )
        self.assertIsNotNone(pending.deletion_requested_at)
        deleted, _ = self.harness.transition(
            pending,
            PersonalMemorySpaceState.DELETED,
            key="delete-complete",
            at=NOW + timedelta(seconds=4),
        )
        self.assertEqual(deleted.state, PersonalMemorySpaceState.DELETED)
        self.assertIsNotNone(deleted.deleted_at)
        self.assertIn(
            ("tenant-a", "user-a", "slot-a"), self.harness.repository.slots
        )
        with self.assertRaises(PersonalMemoryPersistenceError):
            self.harness.transition(
                deleted,
                PersonalMemorySpaceState.ACTIVE,
                key="reactivate-deleted",
                at=NOW + timedelta(seconds=5),
            )


class PersistedSlotAuthorityTests(unittest.TestCase):
    def test_exact_persisted_scope_and_slot_hash_rehydrate(self) -> None:
        slot = empty_slot()
        self.assertEqual(slot_from_row(persisted_slot_row(slot), ()), slot)

    def test_legacy_null_authority_tuple_reads_but_remains_unsealed(self) -> None:
        slot = empty_slot()
        restored = slot_from_row(
            persisted_slot_row(slot, hat_scope_id=None, slot_hash=None),
            (),
        )
        self.assertEqual(restored.hat_scope_id, slot.hat_scope_id)
        self.assertEqual(restored.slot_hash, slot.slot_hash)

    def test_partial_or_mismatched_authority_tuple_fails_closed(self) -> None:
        slot = empty_slot()
        invalid_rows = (
            persisted_slot_row(slot, hat_scope_id=None),
            persisted_slot_row(slot, slot_hash=None),
            persisted_slot_row(slot, hat_scope_id="personal-hat-scope-" + "0" * 64),
            persisted_slot_row(slot, slot_hash="0" * 64),
        )
        for row in invalid_rows:
            with self.subTest(
                hat_scope_id=row["hat_scope_id"],
                slot_hash=row["slot_hash"],
            ):
                with self.assertRaises(IntegrityError):
                    slot_from_row(row, ())

    def test_repository_materializes_and_lazily_seals_canonical_authority(self) -> None:
        source = inspect.getsource(PersonalMemoryCockroachRepository)
        self.assertIn("configuration_digest, hat_scope_id,", source)
        self.assertIn("slot.configuration_digest", source)
        self.assertIn("slot.hat_scope_id", source)
        self.assertIn("slot.slot_hash", source)
        self.assertIn("AND (hat_scope_id = %s OR hat_scope_id IS NULL)", source)
        self.assertIn("AND (slot_hash = %s OR slot_hash IS NULL)", source)


class DatabaseAndBoundaryStaticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sql = (
            ROOT
            / "sql/cockroachdb/migrations/0011_step27_personal_memory_persistence.sql"
        ).read_text(encoding="utf-8")
        cls.package_text = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(
                (ROOT / "src/aioa_memory_kernel/personal_memory").glob("*.py")
            )
        )
        cls.step27_package_text = "\n".join(
            (
                ROOT / "src/aioa_memory_kernel/personal_memory" / filename
            ).read_text(encoding="utf-8")
            for filename in ("models.py", "repository.py", "service.py", "export.py")
        )

    def test_quota_table_uses_composite_owner_key_and_restrictive_fk(self) -> None:
        self.assertIn(
            "PRIMARY KEY (tenant_id, owner_user_id, quota_policy_id)", self.sql
        )
        self.assertIn("personal_memory_quota_policies_owner_fk", self.sql)
        self.assertIn("ON DELETE RESTRICT", self.sql)
        self.assertNotRegex(self.sql, r"(?i)ON\s+DELETE\s+CASCADE")

    def test_rls_and_force_rls_are_present(self) -> None:
        self.assertIn(
            "personal_memory_quota_policies\n  ENABLE ROW LEVEL SECURITY",
            self.sql,
        )
        self.assertIn(
            "personal_memory_quota_policies\n  FORCE ROW LEVEL SECURITY",
            self.sql,
        )
        self.assertGreaterEqual(self.sql.count("user_context_matches"), 2)
        self.assertNotRegex(self.sql, r"(?i)TO\s+PUBLIC")
        self.assertNotRegex(self.sql, r"(?i)\bBYPASSRLS\b")

    def test_model_bindings_are_provider_neutral_and_credential_free(self) -> None:
        for field in (
            "provider_id STRING",
            "model_id STRING",
            "model_revision STRING",
            "binding_mode STRING",
        ):
            self.assertIn(field, self.sql)
        self.assertNotRegex(
            self.step27_package_text.lower(),
            r"api[_-]?key|authorization:|password|secret_key|private_key",
        )

    def test_service_exposes_no_patch_write_or_execution_api(self) -> None:
        methods = {
            name
            for name in dir(PersonalMemoryService)
            if not name.startswith("_")
        }
        forbidden = {
            "approve",
            "commit",
            "execute",
            "activate_patch",
            "retrieve_active_patch",
            "promote_shared",
        }
        self.assertTrue(methods.isdisjoint(forbidden))

    def test_no_external_action_imports(self) -> None:
        self.assertNotRegex(
            self.package_text,
            r"(?m)^\s*(?:import|from)\s+(?:subprocess|boto|requests|httpx)",
        )
        self.assertNotIn("os.system", self.package_text)
        self.assertNotIn("shell=True", self.package_text)

    def test_step33_and_later_capabilities_are_absent(self) -> None:
        for forbidden in (
            "audit_ledger",
            "hash_chain",
            "review_workspace",
            "personal_memory_ui",
        ):
            self.assertNotIn(forbidden, self.package_text)

    def test_verified_answer_does_not_auto_write_memory(self) -> None:
        answer_sources = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (ROOT / "src/aioa_memory_kernel/answers").glob("*.py")
        )
        self.assertNotIn("PersonalMemoryService", answer_sources)


class DocumentationAndEvidenceTests(unittest.TestCase):
    def test_committed_controlled_validation_evidence_is_canonical(self) -> None:
        path = (
            ROOT
            / "docs/evidence/personal-memory/step27-personal-memory-persistence-validation.json"
        )
        value = json.loads(path.read_text(encoding="utf-8"))
        claimed = value.pop("validation_digest")
        self.assertEqual(claimed, canonical_sha256(value))
        self.assertEqual(value["status"], "PASS")
        self.assertEqual(
            value["start_sha"],
            "31b23f662be329a1e70440e50a50f41d2550b89c",
        )
        self.assertTrue(value["database"]["rls"]["rls_enabled"])
        self.assertTrue(value["database"]["rls"]["force_rls_enabled"])
        self.assertEqual(value["owner_isolation"]["cross_user_visible_rows"], 0)
        self.assertEqual(value["owner_isolation"]["cross_tenant_visible_rows"], 0)
        self.assertFalse(value["later_step_boundaries"]["step28_started"])
        rendered = json.dumps(value, sort_keys=True).casefold()
        for unsafe in ("/media/", "/home/", "authorization:", "aws_secret"):
            self.assertNotIn(unsafe, rendered)

    def test_closure_documents_and_current_checkpoint_close_only_step27(self) -> None:
        required = {
            "docs/architecture/PERSONAL_MEMORY_HAT_PERSISTENCE_QUOTAS_MODEL_BINDINGS_1A.md": (
                "owner-private data space",
                "Step 28 remains",
            ),
            "docs/adr/ADR-034-personal-memory-hat-persistence.md": (
                "FORCE RLS on every Step 27",
                "Step 28: NOT STARTED",
            ),
            "docs/operations/STEP_27_PERSONAL_MEMORY_VALIDATION_1A.md": (
                "run_step27_personal_memory_validation.py",
                "force_kill_used=false",
            ),
            "docs/audits/STEP_27_PERSONAL_MEMORY_HAT_PERSISTENCE_CLOSURE_1A.md": (
                "31b23f662be329a1e70440e50a50f41d2550b89c",
                "Step 28 remains NOT STARTED",
            ),
        }
        for relative, phrases in required.items():
            text = (ROOT / relative).read_text(encoding="utf-8")
            for phrase in phrases:
                self.assertIn(phrase, text, relative)
        roadmap = (ROOT / "docs/roadmap/PRODUCTION_ROADMAP.md").read_text(
            encoding="utf-8"
        )
        agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("[x] **Step 27", roadmap)
        self.assertIn("[x] **Step 28", roadmap)
        self.assertIn("Step 28: COMPLETE AND PUSHED", roadmap)
        self.assertIn("[x] **Step 29", roadmap)
        self.assertIn("Step 29: COMPLETE AND PUSHED", roadmap)
        self.assertIn("[x] **Step 30", roadmap)
        self.assertIn("[x] **Step 31", roadmap)
        self.assertIn("[x] **Step 32", roadmap)
        self.assertIn("[x] **Step 33", roadmap)
        self.assertIn("[x] **Step 34", roadmap)
        self.assertIn("[x] **Step 35", roadmap)
        self.assertIn("[x] **Step 36", roadmap)
        self.assertIn("[x] **Step 37", roadmap)
        self.assertIn("[x] **Step 38", roadmap)
        self.assertIn("[x] **Step 39", roadmap)
        self.assertIn("[x] **Step 40", roadmap)
        self.assertIn("[x] **Step 41", roadmap)
        self.assertIn("[ ] **Step 42", roadmap)
        self.assertIn("Step 39: COMPLETE AND PUSHED", roadmap)
        self.assertIn("Step 40: COMPLETE AND PUSHED", roadmap)
        self.assertIn("Step 41: COMPLETE AND PUSHED", roadmap)
        self.assertIn("Step 42: NOT STARTED", roadmap)
        self.assertIn("Step 41 completion does not authorize Step 42.", roadmap)
        self.assertIn(
            "Step 27 owner-private empty Personal Memory HAT slots",
            agents,
        )
        self.assertIn("Step 28 owner- and slot-bound Correction Candidate", agents)
        self.assertIn("Step 29: COMPLETE AND PUSHED", agents)
        self.assertIn("`Step 30: COMPLETE AND PUSHED", agents)
        self.assertIn("`Step 31: COMPLETE AND PUSHED", agents)
        self.assertIn("`Step 32: COMPLETE AND PUSHED", agents)
        self.assertIn("`Step 33: COMPLETE AND PUSHED", agents)
        self.assertIn("`Step 34: COMPLETE AND PUSHED", agents)
        self.assertIn("`Step 35: COMPLETE AND PUSHED", agents)
        self.assertIn("`Step 36: COMPLETE AND PUSHED", agents)
        self.assertIn("Step 37: COMPLETE AND PUSHED", agents)
        self.assertIn("Step 38: COMPLETE AND PUSHED", agents)
        self.assertIn("Step 39: COMPLETE AND PUSHED", agents)
        self.assertIn("Step 40: COMPLETE AND PUSHED", agents)
        self.assertIn("Step 41: COMPLETE AND PUSHED", agents)
        self.assertIn("Step 42: NOT STARTED", agents)
        self.assertIn("Step 41 completion does not authorize Step 42.", agents)


if __name__ == "__main__":
    unittest.main()
