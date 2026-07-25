from __future__ import annotations

import unittest

from _support import LATER, make_personal_proposal, make_shared_promotion
from aioa_memory_kernel.contracts import (
    ActorType,
    AuthorityViolation,
    ContractValidationError,
    InvalidTransition,
    SharedPromotionState,
)
from aioa_memory_kernel.state_machines import (
    shared_promotion_is_committed,
    transition_shared_promotion,
)


class SharedPromotionLifecycleTests(unittest.TestCase):
    def test_personal_patch_cannot_become_shared_automatically(self) -> None:
        personal = make_personal_proposal()
        promotion = make_shared_promotion()
        self.assertNotEqual(
            promotion.shared_promotion_proposal_id, personal.proposal_id
        )
        self.assertIs(
            promotion.state,
            SharedPromotionState.SHARED_PROMOTION_PROPOSED,
        )
        self.assertFalse(shared_promotion_is_committed(promotion))

    def test_promotion_requires_a_new_proposal_id(self) -> None:
        with self.assertRaisesRegex(ContractValidationError, "new proposal ID"):
            make_shared_promotion(
                proposal_id="same-id", originating_patch_id="same-id"
            )

    def test_evidence_revalidation_cannot_be_skipped(self) -> None:
        proposal = make_shared_promotion()
        with self.assertRaises(ContractValidationError):
            transition_shared_promotion(
                proposal,
                target_state=SharedPromotionState.EVIDENCE_REVALIDATED,
                actor_type=ActorType.HUMAN_REVIEWER,
                actor_id="evidence-reviewer",
                transitioned_at=LATER,
            )

    def test_domain_review_cannot_be_skipped(self) -> None:
        proposal = make_shared_promotion()
        with self.assertRaises(InvalidTransition):
            transition_shared_promotion(
                proposal,
                target_state=SharedPromotionState.APPROVED_FOR_SHARED,
                actor_type=ActorType.HUMAN_REVIEWER,
                actor_id="domain-reviewer",
                transitioned_at=LATER,
                domain_approval_id="domain-approval-1",
            )

    def test_user_approval_alone_is_insufficient(self) -> None:
        proposal = make_shared_promotion()
        proposal, _ = transition_shared_promotion(
            proposal,
            target_state=SharedPromotionState.EVIDENCE_REVALIDATED,
            actor_type=ActorType.HUMAN_REVIEWER,
            actor_id="evidence-reviewer",
            transitioned_at=LATER,
            independent_evidence_references=("evidence-independent-1",),
            independent_evidence_validated=True,
        )
        proposal, _ = transition_shared_promotion(
            proposal,
            target_state=SharedPromotionState.DOMAIN_REVIEW_REQUIRED,
            actor_type=ActorType.SYSTEM,
            actor_id="review-router",
            transitioned_at=LATER,
        )
        with self.assertRaises(AuthorityViolation):
            transition_shared_promotion(
                proposal,
                target_state=SharedPromotionState.APPROVED_FOR_SHARED,
                actor_type=ActorType.USER,
                actor_id=proposal.owner_user_id,
                transitioned_at=LATER,
                domain_approval_id="user-only-approval",
            )

    def test_complete_separate_promotion_path_passes(self) -> None:
        personal = make_personal_proposal()
        original_hash = personal.content_hash
        promotion = make_shared_promotion()
        promotion, _ = transition_shared_promotion(
            promotion,
            target_state=SharedPromotionState.EVIDENCE_REVALIDATED,
            actor_type=ActorType.HUMAN_REVIEWER,
            actor_id="evidence-reviewer",
            transitioned_at=LATER,
            independent_evidence_references=("evidence-independent-1",),
            independent_evidence_validated=True,
        )
        promotion, _ = transition_shared_promotion(
            promotion,
            target_state=SharedPromotionState.DOMAIN_REVIEW_REQUIRED,
            actor_type=ActorType.SYSTEM,
            actor_id="review-router",
            transitioned_at=LATER,
        )
        promotion, _ = transition_shared_promotion(
            promotion,
            target_state=SharedPromotionState.APPROVED_FOR_SHARED,
            actor_type=ActorType.HUMAN_REVIEWER,
            actor_id="domain-reviewer",
            transitioned_at=LATER,
            domain_approval_id="domain-approval-1",
        )
        promotion, _ = transition_shared_promotion(
            promotion,
            target_state=SharedPromotionState.SHARED_PATCH_COMMITTED,
            actor_type=ActorType.COMMIT_SERVICE,
            actor_id="bounded-commit-service",
            transitioned_at=LATER,
            shared_commit_id="shared-commit-1",
        )
        self.assertTrue(shared_promotion_is_committed(promotion))
        self.assertEqual(personal.content_hash, original_hash)
        self.assertEqual(personal.lifecycle_state.value, "DETECTED")

    def test_rejected_promotion_is_terminal(self) -> None:
        proposal = make_shared_promotion()
        proposal, _ = transition_shared_promotion(
            proposal,
            target_state=SharedPromotionState.REJECTED,
            actor_type=ActorType.USER,
            actor_id=proposal.owner_user_id,
            transitioned_at=LATER,
        )
        with self.assertRaises(InvalidTransition):
            transition_shared_promotion(
                proposal,
                target_state=SharedPromotionState.EVIDENCE_REVALIDATED,
                actor_type=ActorType.HUMAN_REVIEWER,
                actor_id="reviewer",
                transitioned_at=LATER,
                independent_evidence_references=("evidence-1",),
                independent_evidence_validated=True,
            )

    def test_another_user_cannot_reject_owner_promotion(self) -> None:
        proposal = make_shared_promotion()
        with self.assertRaises(AuthorityViolation):
            transition_shared_promotion(
                proposal,
                target_state=SharedPromotionState.REJECTED,
                actor_type=ActorType.USER,
                actor_id="different-user",
                transitioned_at=LATER,
            )


if __name__ == "__main__":
    unittest.main()
