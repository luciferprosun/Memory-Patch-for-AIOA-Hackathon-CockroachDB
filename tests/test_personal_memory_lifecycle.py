from __future__ import annotations

import unittest

from _support import (
    LATER,
    MODEL_A,
    NOW,
    SPACE_A,
    TENANT_A,
    TENANT_B,
    USER_A,
    USER_B,
    make_pool,
)
from aioa_memory_kernel.contracts import (
    InvalidTransition,
    OwnershipViolation,
    PersonalMemorySpaceState,
    QuotaExceeded,
)
from aioa_memory_kernel.state_machines import (
    activate_personal_memory_space,
    allocate_personal_memory_space,
    archive_personal_memory_space,
    bind_model_to_personal_memory_space,
    complete_personal_memory_deletion,
    configure_personal_memory_space,
    personal_memory_transition_allowed,
    request_personal_memory_deletion,
    request_personal_memory_export,
    restore_personal_memory_space,
    suspend_personal_memory_space,
)


def context(space_id: str = SPACE_A) -> dict[str, object]:
    return {
        "personal_memory_space_id": space_id,
        "changed_at": LATER,
        "tenant_id": TENANT_A,
        "user_id": USER_A,
    }


class PersonalMemoryLifecycleTests(unittest.TestCase):
    def _allocated(self):
        pool, space = allocate_personal_memory_space(
            make_pool(),
            personal_memory_space_id=SPACE_A,
            created_at=NOW,
            tenant_id=TENANT_A,
            user_id=USER_A,
        )
        return pool, space

    def _configured(self):
        pool, _ = self._allocated()
        pool = configure_personal_memory_space(
            pool,
            personal_memory_space_id=SPACE_A,
            display_name="Synthetic private memory",
            changed_at=LATER,
            tenant_id=TENANT_A,
            user_id=USER_A,
        )
        return pool

    def test_new_space_starts_empty_and_inert(self) -> None:
        _, space = self._allocated()
        self.assertIs(space.state, PersonalMemorySpaceState.EMPTY)
        self.assertIsNone(space.display_name)
        self.assertEqual(space.model_binding_ids, ())
        self.assertFalse(space.retrieval_eligible)

    def test_valid_lifecycle_transitions_pass(self) -> None:
        pool = self._configured()
        pool = activate_personal_memory_space(pool, **context())
        self.assertIs(pool.spaces[0].state, PersonalMemorySpaceState.ACTIVE)
        pool = suspend_personal_memory_space(pool, **context())
        self.assertIs(pool.spaces[0].state, PersonalMemorySpaceState.SUSPENDED)
        pool = restore_personal_memory_space(pool, **context())
        self.assertIs(pool.spaces[0].state, PersonalMemorySpaceState.ACTIVE)
        pool = archive_personal_memory_space(pool, **context())
        self.assertIs(pool.spaces[0].state, PersonalMemorySpaceState.ARCHIVED)
        pool = restore_personal_memory_space(pool, **context())
        self.assertIs(pool.spaces[0].state, PersonalMemorySpaceState.CONFIGURED)

    def test_invalid_transition_fails(self) -> None:
        pool, _ = self._allocated()
        with self.assertRaises(InvalidTransition):
            activate_personal_memory_space(pool, **context())

    def test_archived_space_is_not_active(self) -> None:
        pool = self._configured()
        pool = archive_personal_memory_space(pool, **context())
        self.assertFalse(pool.spaces[0].retrieval_eligible)

    def test_deleted_space_is_not_retrieval_eligible_and_is_terminal(self) -> None:
        pool = self._configured()
        pool = request_personal_memory_deletion(pool, **context())
        pool = complete_personal_memory_deletion(pool, **context())
        self.assertIs(pool.spaces[0].state, PersonalMemorySpaceState.DELETED)
        self.assertFalse(pool.spaces[0].retrieval_eligible)
        self.assertFalse(
            personal_memory_transition_allowed(
                PersonalMemorySpaceState.DELETED,
                PersonalMemorySpaceState.ACTIVE,
            )
        )

    def test_empty_space_can_be_deleted_without_forced_configuration(self) -> None:
        pool, _ = self._allocated()
        pool = request_personal_memory_deletion(pool, **context())
        self.assertIs(
            pool.spaces[0].state, PersonalMemorySpaceState.DELETED_PENDING
        )
        pool = complete_personal_memory_deletion(pool, **context())
        self.assertIs(pool.spaces[0].state, PersonalMemorySpaceState.DELETED)

    def test_quota_rejection_does_not_modify_pool(self) -> None:
        pool = make_pool(maximum_total_spaces=0)
        with self.assertRaises(QuotaExceeded):
            allocate_personal_memory_space(
                pool,
                personal_memory_space_id=SPACE_A,
                created_at=NOW,
                tenant_id=TENANT_A,
                user_id=USER_A,
            )
        self.assertEqual(pool.spaces, ())

    def test_active_space_quota_rejects_activation_atomically(self) -> None:
        pool = make_pool(maximum_active_spaces=0)
        pool, _ = allocate_personal_memory_space(
            pool,
            personal_memory_space_id=SPACE_A,
            created_at=NOW,
            tenant_id=TENANT_A,
            user_id=USER_A,
        )
        pool = configure_personal_memory_space(
            pool,
            personal_memory_space_id=SPACE_A,
            display_name="Synthetic private memory",
            changed_at=LATER,
            tenant_id=TENANT_A,
            user_id=USER_A,
        )
        with self.assertRaises(QuotaExceeded):
            activate_personal_memory_space(pool, **context())
        self.assertIs(
            pool.spaces[0].state, PersonalMemorySpaceState.CONFIGURED
        )

    def test_model_binding_does_not_transfer_ownership(self) -> None:
        pool = self._configured()
        before = pool.spaces[0].ownership
        pool = bind_model_to_personal_memory_space(
            pool,
            personal_memory_space_id=SPACE_A,
            model_binding_id=MODEL_A,
            changed_at=LATER,
            tenant_id=TENANT_A,
            user_id=USER_A,
        )
        self.assertEqual(pool.spaces[0].ownership, before)

    def test_same_memory_space_supports_two_model_bindings(self) -> None:
        pool = self._configured()
        for binding in ("model-binding-one", "model-binding-two"):
            pool = bind_model_to_personal_memory_space(
                pool,
                personal_memory_space_id=SPACE_A,
                model_binding_id=binding,
                changed_at=LATER,
                tenant_id=TENANT_A,
                user_id=USER_A,
            )
        self.assertEqual(
            pool.spaces[0].model_binding_ids,
            ("model-binding-one", "model-binding-two"),
        )
        self.assertEqual(pool.spaces[0].user_id, USER_A)

    def test_export_request_records_metadata_only(self) -> None:
        pool = self._configured()
        pool = request_personal_memory_export(
            pool,
            personal_memory_space_id=SPACE_A,
            requested_at=LATER,
            tenant_id=TENANT_A,
            user_id=USER_A,
        )
        self.assertEqual(pool.spaces[0].export_requested_at, LATER)

    def test_cross_user_allocation_is_rejected(self) -> None:
        with self.assertRaises(OwnershipViolation):
            allocate_personal_memory_space(
                make_pool(),
                personal_memory_space_id=SPACE_A,
                created_at=NOW,
                tenant_id=TENANT_A,
                user_id=USER_B,
            )

    def test_cross_tenant_configuration_is_rejected(self) -> None:
        pool, _ = self._allocated()
        with self.assertRaises(OwnershipViolation):
            configure_personal_memory_space(
                pool,
                personal_memory_space_id=SPACE_A,
                display_name="Forbidden rename",
                changed_at=LATER,
                tenant_id=TENANT_B,
                user_id=USER_A,
            )

    def test_pool_size_is_policy_data_not_hardcoded(self) -> None:
        pool = make_pool(maximum_total_spaces=None)
        for index in range(6):
            pool, _ = allocate_personal_memory_space(
                pool,
                personal_memory_space_id=f"dynamic-space-{index}",
                created_at=NOW,
                tenant_id=TENANT_A,
                user_id=USER_A,
            )
        self.assertEqual(len(pool.spaces), 6)


if __name__ == "__main__":
    unittest.main()
