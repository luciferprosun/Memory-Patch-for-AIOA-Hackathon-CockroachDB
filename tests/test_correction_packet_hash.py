from __future__ import annotations

import unittest
from dataclasses import fields, replace

from tests._support import (
    LATER,
    NOW,
    RUN_A,
    make_evidence,
    make_packet,
)
from aioa_memory_kernel.contracts import (
    ActionPolicy,
    ActionPolicyDecision,
    ActorType,
    AnswerStatus,
    ContractValidationError,
    CorrectionCandidate,
    CorrectionCandidateState,
    EvidenceStatus,
    KnowledgeRoute,
    RoutingDecision,
    assert_action_policy_unchanged,
    derive_answer_status,
    verify_correction_candidate_hash,
    verify_correction_packet_hash,
)


class CorrectionPacketHashTests(unittest.TestCase):
    def test_packet_hash_is_deterministic(self) -> None:
        self.assertEqual(make_packet().packet_hash, make_packet().packet_hash)

    def test_packet_hash_changes_after_evidence_mutation(self) -> None:
        original = make_packet(evidence=make_evidence(content_hash="a" * 64))
        changed = make_packet(evidence=make_evidence(content_hash="b" * 64))
        self.assertNotEqual(original.packet_hash, changed.packet_hash)

    def test_packet_hash_verifies(self) -> None:
        verify_correction_packet_hash(make_packet())

    def test_ordered_evidence_changes_packet_identity(self) -> None:
        first = make_evidence(
            evidence_id="evidence-a", source_version_id="version-a"
        )
        second = make_evidence(
            evidence_id="evidence-b",
            source_version_id="version-b",
            content_hash="b" * 64,
        )
        base = make_packet(evidence=first)
        reversed_packet = replace(
            base,
            ordered_evidence_items=(second, first),
            source_version_ids=("version-a", "version-b"),
        )
        forward_packet = replace(
            base,
            ordered_evidence_items=(first, second),
            source_version_ids=("version-a", "version-b"),
        )
        self.assertNotEqual(
            reversed_packet.packet_hash, forward_packet.packet_hash
        )

    def test_source_versions_must_cover_evidence_exactly(self) -> None:
        packet = make_packet()
        with self.assertRaisesRegex(
            ContractValidationError, "exactly cover ordered evidence"
        ):
            replace(packet, source_version_ids=("unrelated-version",))

    def test_sufficient_packet_requires_evidence(self) -> None:
        packet = make_packet()
        with self.assertRaises(ContractValidationError):
            replace(
                packet,
                ordered_evidence_items=(),
                source_version_ids=(),
            )

    def test_correction_candidate_is_only_detected_or_proposed(self) -> None:
        self.assertEqual(
            {state.value for state in CorrectionCandidateState},
            {"DETECTED", "PROPOSED"},
        )

    def test_correction_candidate_hash_verifies(self) -> None:
        claim = make_packet().claims_under_review[0]
        candidate = CorrectionCandidate(
            event_id="correction-1",
            tenant_id="tenant-alpha",
            user_id="user-alpha",
            personal_memory_space_id="space-alpha-1",
            source_component=ActorType.CRITIC_PROMPT_LOOP,
            run_id=RUN_A,
            model_binding_id="model-binding-alpha",
            draft_v1_reference="draft-v1",
            detected_claims=(claim,),
            proposed_correction="Use the cited synthetic source.",
            available_evidence_references=("evidence-1",),
            uncertainty=0.2,
            created_at=NOW,
            state=CorrectionCandidateState.PROPOSED,
        )
        verify_correction_candidate_hash(candidate)

    def test_knowledge_kernel_may_produce_candidate_but_not_approval(self) -> None:
        claim = make_packet().claims_under_review[0]
        candidate = CorrectionCandidate(
            event_id="kernel-correction-1",
            tenant_id="tenant-alpha",
            user_id="user-alpha",
            personal_memory_space_id="space-alpha-1",
            source_component=ActorType.KNOWLEDGE_KERNEL,
            run_id=RUN_A,
            model_binding_id="model-binding-alpha",
            draft_v1_reference="draft-v1",
            detected_claims=(claim,),
            proposed_correction="Use the synthetic evidence.",
            available_evidence_references=("evidence-1",),
            uncertainty=0.1,
            created_at=NOW,
            state=CorrectionCandidateState.PROPOSED,
        )
        self.assertEqual(candidate.state.value, "PROPOSED")

    def test_correction_candidate_uncertainty_is_bounded(self) -> None:
        claim = make_packet().claims_under_review[0]
        with self.assertRaisesRegex(ContractValidationError, "between 0 and 1"):
            CorrectionCandidate(
                event_id="correction-invalid",
                tenant_id="tenant-alpha",
                user_id="user-alpha",
                personal_memory_space_id="space-alpha-1",
                source_component=ActorType.USER,
                run_id=RUN_A,
                model_binding_id="model-binding-alpha",
                draft_v1_reference="draft-v1",
                detected_claims=(claim,),
                proposed_correction="Synthetic correction.",
                available_evidence_references=(),
                uncertainty=1.1,
                created_at=NOW,
                state=CorrectionCandidateState.DETECTED,
            )


class RoutingAndResultContractTests(unittest.TestCase):
    def _routing(self) -> RoutingDecision:
        return RoutingDecision(
            kernel_run_id=RUN_A,
            knowledge_route=KnowledgeRoute.HAT_ENFORCE,
            selected_hat_id="synthetic-software-version",
            reason_codes=("SYNTHETIC_DOMAIN_MATCH",),
            decided_at=NOW,
        )

    def _action(
        self, policy: ActionPolicy = ActionPolicy.ALLOW
    ) -> ActionPolicyDecision:
        return ActionPolicyDecision(
            kernel_run_id=RUN_A,
            action_policy=policy,
            reason_codes=("NO_EXTERNAL_ACTION",),
            decided_at=NOW,
        )

    def test_axis_a_and_axis_b_are_independent_fields(self) -> None:
        self.assertNotIn(
            "action_policy", {field.name for field in fields(RoutingDecision)}
        )
        self.assertNotIn(
            "knowledge_route",
            {field.name for field in fields(ActionPolicyDecision)},
        )

    def test_plain_string_cannot_impersonate_route_enum(self) -> None:
        with self.assertRaisesRegex(
            ContractValidationError, "KnowledgeRoute member"
        ):
            RoutingDecision(
                kernel_run_id=RUN_A,
                knowledge_route="HAT_ENFORCE",  # type: ignore[arg-type]
                selected_hat_id="synthetic-software-version",
                reason_codes=("SYNTHETIC_DOMAIN_MATCH",),
                decided_at=NOW,
            )

    def test_evidence_failure_does_not_rewrite_historical_route(self) -> None:
        routing = self._routing()
        status = derive_answer_status(
            knowledge_route=routing.knowledge_route,
            action_policy=ActionPolicy.ALLOW,
            evidence_status=EvidenceStatus.INSUFFICIENT,
        )
        self.assertIs(routing.knowledge_route, KnowledgeRoute.HAT_ENFORCE)
        self.assertIs(status, AnswerStatus.BLOCKED_NO_VERIFIED_EVIDENCE)

    def test_hat_enforce_insufficient_is_blocked(self) -> None:
        self.assertIs(
            derive_answer_status(
                knowledge_route=KnowledgeRoute.HAT_ENFORCE,
                action_policy=ActionPolicy.ALLOW,
                evidence_status=EvidenceStatus.INSUFFICIENT,
            ),
            AnswerStatus.BLOCKED_NO_VERIFIED_EVIDENCE,
        )

    def test_personal_memory_cannot_alter_action_policy(self) -> None:
        original = self._action(ActionPolicy.REQUIRE_CONFIRMATION)
        changed = replace(
            original,
            action_policy=ActionPolicy.ALLOW,
            decided_at=LATER,
        )
        with self.assertRaisesRegex(
            ContractValidationError, "cannot alter"
        ):
            assert_action_policy_unchanged(original, changed)

    def test_policy_denial_blocks_independently_of_evidence(self) -> None:
        self.assertIs(
            derive_answer_status(
                knowledge_route=KnowledgeRoute.PASS_THROUGH,
                action_policy=ActionPolicy.DENY_ACTION,
                evidence_status=EvidenceStatus.NOT_REQUIRED,
                action_required=True,
            ),
            AnswerStatus.BLOCKED_POLICY,
        )

    def test_action_denial_does_not_erase_informational_answer_status(self) -> None:
        self.assertIs(
            derive_answer_status(
                knowledge_route=KnowledgeRoute.HAT_ENFORCE,
                action_policy=ActionPolicy.DENY_ACTION,
                evidence_status=EvidenceStatus.SUFFICIENT,
                verification_passed=True,
                action_required=False,
            ),
            AnswerStatus.VERIFIED,
        )

    def test_ambiguous_route_has_distinct_blocked_status(self) -> None:
        self.assertIs(
            derive_answer_status(
                knowledge_route=KnowledgeRoute.AMBIGUOUS,
                action_policy=ActionPolicy.ALLOW,
                evidence_status=EvidenceStatus.UNAVAILABLE,
            ),
            AnswerStatus.BLOCKED_AMBIGUOUS_ROUTE,
        )

    def test_storage_unavailable_has_distinct_blocked_status(self) -> None:
        self.assertIs(
            derive_answer_status(
                knowledge_route=KnowledgeRoute.HAT_ENFORCE,
                action_policy=ActionPolicy.ALLOW,
                evidence_status=EvidenceStatus.UNAVAILABLE,
                storage_unavailable=True,
            ),
            AnswerStatus.BLOCKED_STORAGE_UNAVAILABLE,
        )


if __name__ == "__main__":
    unittest.main()
