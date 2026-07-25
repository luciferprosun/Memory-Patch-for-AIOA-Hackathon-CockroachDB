from __future__ import annotations

import unittest
from datetime import timedelta

from _support import (
    MODEL_A,
    NOW,
    RUN_A,
    SPACE_A,
    TENANT_A,
    TENANT_B,
    USER_A,
    USER_B,
    make_claim,
    ownership_a,
)
from aioa_memory_kernel.contracts import (
    ActorType,
    ContractValidationError,
    CorrectionCandidate,
    CorrectionCandidateState,
    KernelRunIdentity,
    MemoryContentKind,
    MemoryItem,
    MemoryTrustClass,
    MemoryVisibility,
    OwnershipViolation,
    PersonalMemorySpaceState,
    memory_item_is_retrieval_eligible,
    require_tenant_context,
    validate_correction_candidate_ownership,
    verify_ownership,
)


def personal_item(*, revoked: bool = False) -> MemoryItem:
    return MemoryItem(
        memory_item_id="personal-item-1",
        visibility=MemoryVisibility.PERSONAL,
        trust_class=MemoryTrustClass.PERSONAL_VERIFIED_PATCH,
        content_kind=MemoryContentKind.FACTUAL,
        content={"statement": "Synthetic private fact."},
        scope_dimensions=(),
        evidence_references=("evidence-1",),
        created_at=NOW,
        ownership=ownership_a(),
        valid_from=NOW - timedelta(days=1),
        valid_until=NOW + timedelta(days=1),
        revoked=revoked,
    )


def run_a() -> KernelRunIdentity:
    return KernelRunIdentity(
        kernel_run_id=RUN_A,
        tenant_id=TENANT_A,
        user_id=USER_A,
        personal_memory_space_id=SPACE_A,
        model_binding_id=MODEL_A,
        created_at=NOW,
    )


def correction_candidate(
    *, tenant_id: str = TENANT_A, user_id: str = USER_A
) -> CorrectionCandidate:
    return CorrectionCandidate(
        event_id="correction-event-1",
        tenant_id=tenant_id,
        user_id=user_id,
        personal_memory_space_id=SPACE_A,
        source_component=ActorType.CRITIC_PROMPT_LOOP,
        run_id=RUN_A,
        model_binding_id=MODEL_A,
        draft_v1_reference="draft-v1",
        detected_claims=(make_claim(),),
        proposed_correction="Use the synthetic source.",
        available_evidence_references=("evidence-1",),
        uncertainty=0.2,
        created_at=NOW,
        state=CorrectionCandidateState.PROPOSED,
    )


class TenantBoundaryContractTests(unittest.TestCase):
    def test_exact_owner_access_passes(self) -> None:
        verify_ownership(
            ownership_a(),
            tenant_id=TENANT_A,
            user_id=USER_A,
            personal_memory_space_id=SPACE_A,
        )

    def test_cross_user_target_is_rejected(self) -> None:
        with self.assertRaisesRegex(OwnershipViolation, "cross-user"):
            verify_ownership(
                ownership_a(), tenant_id=TENANT_A, user_id=USER_B
            )

    def test_cross_tenant_target_is_rejected(self) -> None:
        with self.assertRaisesRegex(OwnershipViolation, "cross-tenant"):
            verify_ownership(
                ownership_a(), tenant_id=TENANT_B, user_id=USER_A
            )

    def test_personal_access_requires_exact_space_context(self) -> None:
        with self.assertRaisesRegex(
            OwnershipViolation, "space context is required"
        ):
            verify_ownership(
                ownership_a(),
                tenant_id=TENANT_A,
                user_id=USER_A,
                personal_memory_space_id=None,
            )

    def test_missing_tenant_context_fails_closed(self) -> None:
        with self.assertRaises(OwnershipViolation):
            require_tenant_context(None, USER_A)
        with self.assertRaises(OwnershipViolation):
            require_tenant_context(TENANT_A, None)

    def test_critic_loop_correction_cannot_cross_tenants(self) -> None:
        candidate = correction_candidate(tenant_id=TENANT_B)
        with self.assertRaises(OwnershipViolation):
            validate_correction_candidate_ownership(
                candidate,
                run=run_a(),
                target_ownership=ownership_a(),
            )

    def test_critic_loop_correction_cannot_cross_users(self) -> None:
        candidate = correction_candidate(user_id=USER_B)
        with self.assertRaises(OwnershipViolation):
            validate_correction_candidate_ownership(
                candidate,
                run=run_a(),
                target_ownership=ownership_a(),
            )

    def test_kernel_run_cannot_cross_personal_memory_spaces(self) -> None:
        mismatched_run = KernelRunIdentity(
            kernel_run_id=RUN_A,
            tenant_id=TENANT_A,
            user_id=USER_A,
            personal_memory_space_id="space-alpha-other",
            model_binding_id=MODEL_A,
            created_at=NOW,
        )
        with self.assertRaisesRegex(OwnershipViolation, "memory spaces"):
            validate_correction_candidate_ownership(
                correction_candidate(),
                run=mismatched_run,
                target_ownership=ownership_a(),
            )

    def test_kernel_run_missing_space_context_fails_closed(self) -> None:
        missing_space_run = KernelRunIdentity(
            kernel_run_id=RUN_A,
            tenant_id=TENANT_A,
            user_id=USER_A,
            personal_memory_space_id=None,
            model_binding_id=MODEL_A,
            created_at=NOW,
        )
        with self.assertRaisesRegex(OwnershipViolation, "context is required"):
            validate_correction_candidate_ownership(
                correction_candidate(),
                run=missing_space_run,
                target_ownership=ownership_a(),
            )

    def test_shared_retrieval_cannot_include_private_memory(self) -> None:
        with self.assertRaisesRegex(OwnershipViolation, "shared HAT retrieval"):
            memory_item_is_retrieval_eligible(
                personal_item(),
                at_time=NOW,
                shared_retrieval=True,
            )

    def test_revoked_personal_memory_is_not_eligible(self) -> None:
        self.assertFalse(
            memory_item_is_retrieval_eligible(
                personal_item(revoked=True),
                at_time=NOW,
                tenant_id=TENANT_A,
                user_id=USER_A,
                personal_memory_space_id=SPACE_A,
                personal_space_state=PersonalMemorySpaceState.ACTIVE,
            )
        )

    def test_deleted_space_is_not_retrieval_eligible(self) -> None:
        self.assertFalse(
            memory_item_is_retrieval_eligible(
                personal_item(),
                at_time=NOW,
                tenant_id=TENANT_A,
                user_id=USER_A,
                personal_memory_space_id=SPACE_A,
                personal_space_state=PersonalMemorySpaceState.DELETED,
            )
        )

    def test_personal_memory_without_ownership_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            ContractValidationError, "requires explicit ownership"
        ):
            MemoryItem(
                memory_item_id="orphan-private-item",
                visibility=MemoryVisibility.PERSONAL,
                trust_class=MemoryTrustClass.USER_ASSERTED_MEMORY,
                content_kind=MemoryContentKind.FACTUAL,
                content={"statement": "Synthetic orphan."},
                scope_dimensions=(),
                evidence_references=(),
                created_at=NOW,
                ownership=None,
            )

    def test_shared_memory_cannot_carry_private_ownership(self) -> None:
        with self.assertRaisesRegex(
            ContractValidationError, "cannot carry private-space ownership"
        ):
            MemoryItem(
                memory_item_id="invalid-shared-item",
                visibility=MemoryVisibility.SHARED,
                trust_class=MemoryTrustClass.SHARED_HAT_VERIFIED_MEMORY,
                content_kind=MemoryContentKind.FACTUAL,
                content={"statement": "Synthetic shared fact."},
                scope_dimensions=(),
                evidence_references=("evidence-1",),
                created_at=NOW,
                ownership=ownership_a(),
            )


if __name__ == "__main__":
    unittest.main()
