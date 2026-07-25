from __future__ import annotations

import unittest
from datetime import timedelta

from _support import NOW, SPACE_A, TENANT_A, USER_A, make_evidence, ownership_a
from aioa_memory_kernel.contracts import (
    ContractValidationError,
    EvidenceItem,
    MemoryConflictType,
    MemoryContentKind,
    MemoryItem,
    MemoryRetrievalStatus,
    MemoryTrustClass,
    MemoryVisibility,
    ModelExperienceEvent,
    ModelExperienceOutcome,
    PersonalMemorySpaceState,
    TRUST_PRECEDENCE,
    assert_model_experience_is_advisory,
    classify_memory_conflict,
    compare_memory_trust,
    memory_item_is_retrieval_eligible,
    memory_item_retrieval_status,
    trust_rank,
)


def memory_item(
    *,
    item_id: str,
    trust: MemoryTrustClass,
    content_kind: MemoryContentKind = MemoryContentKind.FACTUAL,
    content: object | None = None,
    valid_until=None,
) -> MemoryItem:
    if content is None:
        content = {"statement": f"Synthetic {item_id}"}
    return MemoryItem(
        memory_item_id=item_id,
        visibility=MemoryVisibility.PERSONAL,
        trust_class=trust,
        content_kind=content_kind,
        content=content,
        scope_dimensions=(),
        evidence_references=("evidence-1",),
        created_at=NOW,
        ownership=ownership_a(),
        source_patch_id=f"patch-{item_id}",
        valid_from=NOW - timedelta(days=1),
        valid_until=valid_until,
    )


class MemoryTrustPrecedenceTests(unittest.TestCase):
    def test_canonical_evidence_outranks_personal_patch(self) -> None:
        self.assertGreater(
            compare_memory_trust(
                MemoryTrustClass.CANONICAL_SOURCE_EVIDENCE,
                MemoryTrustClass.PERSONAL_VERIFIED_PATCH,
            ),
            0,
        )

    def test_shared_hat_memory_outranks_model_experience(self) -> None:
        self.assertGreater(
            compare_memory_trust(
                MemoryTrustClass.SHARED_HAT_VERIFIED_MEMORY,
                MemoryTrustClass.MODEL_EXPERIENCE_HINT,
            ),
            0,
        )

    def test_user_asserted_memory_is_not_canonical(self) -> None:
        self.assertIsNot(
            MemoryTrustClass.USER_ASSERTED_MEMORY,
            MemoryTrustClass.CANONICAL_SOURCE_EVIDENCE,
        )
        self.assertLess(
            trust_rank(MemoryTrustClass.USER_ASSERTED_MEMORY),
            trust_rank(MemoryTrustClass.CANONICAL_SOURCE_EVIDENCE),
        )

    def test_all_trust_classes_follow_required_order(self) -> None:
        ranks = [trust_rank(item) for item in TRUST_PRECEDENCE]
        self.assertEqual(ranks, sorted(ranks, reverse=True))
        self.assertEqual(len(ranks), len(set(ranks)))

    def test_model_experience_is_rejected_as_evidence(self) -> None:
        with self.assertRaisesRegex(ContractValidationError, "not factual evidence"):
            make_evidence(trust_class=MemoryTrustClass.MODEL_EXPERIENCE_HINT)

    def test_personal_patch_memory_is_not_reclassified_as_evidence(self) -> None:
        with self.assertRaisesRegex(ContractValidationError, "not factual evidence"):
            make_evidence(
                trust_class=MemoryTrustClass.PERSONAL_VERIFIED_PATCH
            )

    def test_same_trust_conflict_remains_explicit(self) -> None:
        left = memory_item(
            item_id="left",
            trust=MemoryTrustClass.PERSONAL_VERIFIED_PATCH,
        )
        right = memory_item(
            item_id="right",
            trust=MemoryTrustClass.PERSONAL_VERIFIED_PATCH,
        )
        conflict = classify_memory_conflict(left, right)
        self.assertIs(
            conflict.conflict_type, MemoryConflictType.SAME_TRUST_CONFLICT
        )
        self.assertFalse(conflict.resolved_by_precedence)

    def test_lower_trust_conflict_uses_precedence_without_mutation(self) -> None:
        higher = memory_item(
            item_id="higher",
            trust=MemoryTrustClass.PERSONAL_VERIFIED_PATCH,
        )
        lower = memory_item(
            item_id="lower",
            trust=MemoryTrustClass.SESSION_MEMORY,
            content_kind=MemoryContentKind.SESSION,
        )
        conflict = classify_memory_conflict(lower, higher)
        self.assertIs(
            conflict.conflict_type, MemoryConflictType.LOWER_TRUST_CONFLICT
        )
        self.assertEqual(conflict.higher_or_primary_item_id, "higher")
        self.assertTrue(conflict.resolved_by_precedence)

    def test_stale_personal_memory_is_excluded_and_flagged(self) -> None:
        stale = memory_item(
            item_id="stale",
            trust=MemoryTrustClass.PERSONAL_VERIFIED_PATCH,
            valid_until=NOW - timedelta(seconds=1),
        )
        kwargs = {
            "at_time": NOW,
            "tenant_id": TENANT_A,
            "user_id": USER_A,
            "personal_memory_space_id": SPACE_A,
            "personal_space_state": PersonalMemorySpaceState.ACTIVE,
        }
        self.assertFalse(memory_item_is_retrieval_eligible(stale, **kwargs))
        self.assertIs(
            memory_item_retrieval_status(stale, **kwargs),
            MemoryRetrievalStatus.STALE,
        )

    def test_allowed_preference_can_shape_presentation(self) -> None:
        preference = memory_item(
            item_id="preference",
            trust=MemoryTrustClass.USER_ASSERTED_MEMORY,
            content_kind=MemoryContentKind.PREFERENCE,
            content={"language": "pl", "explanation_depth": "concise"},
        )
        self.assertEqual(preference.content["language"], "pl")

    def test_preference_cannot_override_action_policy(self) -> None:
        with self.assertRaisesRegex(
            ContractValidationError, "cannot override evidence"
        ):
            memory_item(
                item_id="forbidden-preference",
                trust=MemoryTrustClass.USER_ASSERTED_MEMORY,
                content_kind=MemoryContentKind.PREFERENCE,
                content={"action_policy": "ALLOW"},
            )

    def test_canonical_evidence_cannot_be_constructed_as_memory_item(self) -> None:
        with self.assertRaisesRegex(ContractValidationError, "not a MemoryItem"):
            memory_item(
                item_id="invalid-canonical-memory",
                trust=MemoryTrustClass.CANONICAL_SOURCE_EVIDENCE,
            )

    def test_model_experience_event_is_advisory_only(self) -> None:
        event = ModelExperienceEvent(
            model_experience_event_id="experience-1",
            tenant_id=TENANT_A,
            user_id=USER_A,
            personal_memory_space_id=SPACE_A,
            provider="synthetic-provider",
            model_family="synthetic-family",
            exact_model_version="synthetic-1.0",
            failure_category="unsupported-claim",
            kernel_run_id="kernel-run-alpha-1",
            claim_category="synthetic-version",
            correction_outcome=ModelExperienceOutcome.CORRECTED,
            verifier_outcome="SUPPORTED_AFTER_CORRECTION",
            created_at=NOW,
            expires_at=NOW + timedelta(days=30),
            retention_policy="30-days",
        )
        assert_model_experience_is_advisory(event)
        with self.assertRaisesRegex(
            ContractValidationError, "never factual evidence"
        ):
            assert_model_experience_is_advisory(event, used_as_evidence=True)
        with self.assertRaisesRegex(
            ContractValidationError, "cannot approve"
        ):
            assert_model_experience_is_advisory(event, used_for_approval=True)


if __name__ == "__main__":
    unittest.main()
