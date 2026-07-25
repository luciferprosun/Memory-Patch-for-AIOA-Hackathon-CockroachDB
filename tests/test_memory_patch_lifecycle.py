from __future__ import annotations

import unittest

from _support import (
    LATER,
    NOW,
    USER_A,
    make_approval,
    make_commit,
    make_personal_proposal,
)
from aioa_memory_kernel.contracts import (
    ActorType,
    ApprovalDecision,
    AuthorityViolation,
    ContractValidationError,
    MemoryContentKind,
    MemoryPatchApproval,
    MemoryPatchProposal,
    MemoryTargetScope,
    MemoryTrustClass,
    PatchState,
    ProposalOrigin,
)
from aioa_memory_kernel.state_machines import (
    memory_patch_is_retrieval_eligible,
    memory_patch_transition_allowed,
    transition_memory_patch,
)


class MemoryPatchLifecycleTests(unittest.TestCase):
    def _transition(
        self,
        proposal: MemoryPatchProposal,
        target: PatchState,
        *,
        actor_type: ActorType = ActorType.SYSTEM,
        actor_id: str = "kernel-contract",
        approval=None,
        commit=None,
    ) -> MemoryPatchProposal:
        updated, _ = transition_memory_patch(
            proposal,
            target_state=target,
            actor_type=actor_type,
            actor_id=actor_id,
            transitioned_at=LATER,
            approval=approval,
            commit=commit,
        )
        return updated

    def _awaiting_approval(self) -> MemoryPatchProposal:
        proposal = make_personal_proposal()
        for state in (
            PatchState.PROPOSED,
            PatchState.EVIDENCE_BOUND,
            PatchState.VALIDATED,
            PatchState.AWAITING_APPROVAL,
        ):
            proposal = self._transition(proposal, state)
        return proposal

    def _active(self) -> tuple[MemoryPatchProposal, object, object]:
        proposal = self._awaiting_approval()
        approval = make_approval(proposal)
        proposal = self._transition(
            proposal,
            PatchState.APPROVED,
            actor_type=ActorType.USER,
            actor_id=USER_A,
            approval=approval,
        )
        commit = make_commit(proposal, approval)
        proposal = self._transition(
            proposal,
            PatchState.COMMITTED,
            actor_type=ActorType.COMMIT_SERVICE,
            actor_id="bounded-commit-service",
            approval=approval,
            commit=commit,
        )
        proposal = self._transition(
            proposal,
            PatchState.ACTIVE,
            actor_type=ActorType.COMMIT_SERVICE,
            actor_id="bounded-commit-service",
            approval=approval,
            commit=commit,
        )
        return proposal, approval, commit

    def test_complete_successful_path_passes(self) -> None:
        proposal, _, _ = self._active()
        self.assertIs(proposal.lifecycle_state, PatchState.ACTIVE)
        self.assertTrue(memory_patch_is_retrieval_eligible(proposal))

    def test_proposed_to_active_fails(self) -> None:
        proposal = make_personal_proposal(state=PatchState.PROPOSED)
        with self.assertRaises(InvalidTransition):
            self._transition(proposal, PatchState.ACTIVE)

    def test_detected_to_approved_fails(self) -> None:
        proposal = make_personal_proposal()
        with self.assertRaises(InvalidTransition):
            self._transition(proposal, PatchState.APPROVED)

    def test_validated_to_committed_fails(self) -> None:
        proposal = make_personal_proposal(state=PatchState.VALIDATED)
        with self.assertRaises(InvalidTransition):
            self._transition(proposal, PatchState.COMMITTED)

    def test_factual_validation_without_evidence_fails(self) -> None:
        with self.assertRaisesRegex(ContractValidationError, "requires evidence"):
            make_personal_proposal(
                state=PatchState.EVIDENCE_BOUND,
                evidence_references=(),
            )

    def test_activation_without_approval_fails(self) -> None:
        proposal = make_personal_proposal(state=PatchState.COMMITTED)
        approval = make_approval(proposal)
        commit = make_commit(proposal, approval)
        with self.assertRaises(AuthorityViolation):
            self._transition(
                proposal,
                PatchState.ACTIVE,
                actor_type=ActorType.COMMIT_SERVICE,
                actor_id="bounded-commit-service",
                commit=commit,
            )

    def test_commit_transition_requires_bound_approval_record(self) -> None:
        proposal = self._awaiting_approval()
        approval = make_approval(proposal)
        proposal = self._transition(
            proposal,
            PatchState.APPROVED,
            actor_type=ActorType.USER,
            actor_id=USER_A,
            approval=approval,
        )
        commit = make_commit(proposal, approval)
        with self.assertRaises(AuthorityViolation):
            self._transition(
                proposal,
                PatchState.COMMITTED,
                actor_type=ActorType.COMMIT_SERVICE,
                actor_id="bounded-commit-service",
                commit=commit,
            )

    def test_model_cannot_create_approval_record(self) -> None:
        proposal = self._awaiting_approval()
        with self.assertRaises(AuthorityViolation):
            MemoryPatchApproval(
                approval_id="invalid-approval",
                proposal_id=proposal.proposal_id,
                proposal_content_hash=proposal.content_hash,
                tenant_id=proposal.tenant_id,
                owner_user_id=proposal.owner_user_id,
                personal_memory_space_id=(
                    proposal.target_personal_memory_space_id
                ),
                decision=ApprovalDecision.APPROVE,
                approver_type=ActorType.MODEL,
                approver_id="synthetic-model",
                reason_code="FORBIDDEN",
                decided_at=LATER,
            )

    def test_critic_cannot_apply_approval_transition(self) -> None:
        proposal = self._awaiting_approval()
        approval = make_approval(proposal)
        with self.assertRaises(AuthorityViolation):
            self._transition(
                proposal,
                PatchState.APPROVED,
                actor_type=ActorType.CRITIC_PROMPT_LOOP,
                actor_id="critic-runner",
                approval=approval,
            )

    def test_preference_requires_exact_owner_approval(self) -> None:
        proposal = make_personal_proposal(
            state=PatchState.AWAITING_APPROVAL,
            content={"language": "pl"},
            evidence_references=(),
            content_kind=MemoryContentKind.PREFERENCE,
        )
        reviewer_approval = make_approval(
            proposal,
            approver_type=ActorType.HUMAN_REVIEWER,
            approver_id="reviewer-1",
        )
        with self.assertRaises(AuthorityViolation):
            self._transition(
                proposal,
                PatchState.APPROVED,
                actor_type=ActorType.HUMAN_REVIEWER,
                actor_id="reviewer-1",
                approval=reviewer_approval,
            )

    def test_rejected_proposal_cannot_commit_or_activate(self) -> None:
        proposal = self._awaiting_approval()
        rejection = make_approval(
            proposal, decision=ApprovalDecision.REJECT
        )
        proposal = self._transition(
            proposal,
            PatchState.REJECTED,
            actor_type=ActorType.USER,
            actor_id=USER_A,
            approval=rejection,
        )
        self.assertFalse(memory_patch_is_retrieval_eligible(proposal))
        self.assertFalse(
            memory_patch_transition_allowed(
                PatchState.REJECTED, PatchState.COMMITTED
            )
        )
        with self.assertRaises(InvalidTransition):
            self._transition(proposal, PatchState.COMMITTED)

    def test_revoked_patch_is_not_retrieval_eligible(self) -> None:
        proposal, _, _ = self._active()
        proposal = self._transition(
            proposal,
            PatchState.REVOKED,
            actor_type=ActorType.HUMAN_REVIEWER,
            actor_id="reviewer-1",
        )
        self.assertFalse(memory_patch_is_retrieval_eligible(proposal))

    def test_model_cannot_revoke_active_memory(self) -> None:
        proposal, _, _ = self._active()
        with self.assertRaises(AuthorityViolation):
            self._transition(
                proposal,
                PatchState.REVOKED,
                actor_type=ActorType.MODEL,
                actor_id="synthetic-model",
            )

    def test_supersession_preserves_content_identity_and_history(self) -> None:
        proposal, _, _ = self._active()
        updated, transition = transition_memory_patch(
            proposal,
            target_state=PatchState.SUPERSEDED,
            actor_type=ActorType.HUMAN_REVIEWER,
            actor_id="reviewer-1",
            transitioned_at=LATER,
        )
        self.assertEqual(updated.content_hash, proposal.content_hash)
        self.assertIs(transition.state_before, PatchState.ACTIVE)
        self.assertIs(transition.state_after, PatchState.SUPERSEDED)

    def test_canonical_evidence_is_not_a_patch_target(self) -> None:
        with self.assertRaisesRegex(
            ContractValidationError, "not a valid Memory Patch target"
        ):
            MemoryPatchProposal(
                proposal_id="invalid-canonical-target",
                tenant_id="tenant-alpha",
                owner_user_id=USER_A,
                target_scope=MemoryTargetScope.USER_PERSONAL_HAT,
                target_hat_id=None,
                target_personal_memory_space_id="space-alpha-1",
                origin=ProposalOrigin.USER_ENTRY,
                proposed_content={"statement": "synthetic"},
                evidence_references=("evidence-1",),
                scope_dimensions=(),
                valid_from=NOW,
                valid_until=None,
                requested_trust_class=(
                    MemoryTrustClass.CANONICAL_SOURCE_EVIDENCE
                ),
                approval_requirement=(
                    make_personal_proposal().approval_requirement
                ),
                lifecycle_state=PatchState.DETECTED,
                content_kind=MemoryContentKind.FACTUAL,
                created_at=NOW,
            )

    def test_origin_never_grants_approval_authority(self) -> None:
        proposal = self._awaiting_approval()
        self.assertIs(proposal.origin, ProposalOrigin.CRITIC_PROMPT_LOOP)
        with self.assertRaises(AuthorityViolation):
            self._transition(
                proposal,
                PatchState.APPROVED,
                actor_type=ActorType.CRITIC_PROMPT_LOOP,
                actor_id="same-origin",
            )


# Imported here to keep the test failures focused on state-machine behavior.
from aioa_memory_kernel.contracts import InvalidTransition  # noqa: E402


if __name__ == "__main__":
    unittest.main()
