"""Adversarial coverage for authority, isolation, replay, and copy safety."""

from __future__ import annotations

import unittest
from dataclasses import FrozenInstanceError, fields, replace
from datetime import datetime, timedelta

from tests._support import (
    LATER,
    NOW,
    SPACE_A,
    TENANT_A,
    TENANT_B,
    USER_A,
    USER_B,
    make_approval,
    make_claim,
    make_commit,
    make_manifest,
    make_personal_proposal,
    make_shared_promotion,
)
import aioa_memory_kernel as kernel
from aioa_memory_kernel.contracts import (
    ActorType,
    ApprovalDecision,
    ApprovalRequirement,
    AuditEvent,
    AuthorityViolation,
    CONTRACT_SCHEMA_VERSION,
    ContractValidationError,
    CorrectionCandidate,
    CorrectionCandidateState,
    IntegrityError,
    InvalidTransition,
    MemoryContentKind,
    MemoryItem,
    MemoryPatchApproval,
    MemoryPatchCommit,
    MemoryPatchProposal,
    MemoryOwnership,
    MemoryTargetScope,
    MemoryTrustClass,
    MemoryVisibility,
    ModelExperienceEvent,
    ModelExperienceOutcome,
    OwnershipViolation,
    PatchState,
    PersonalHatQuotaPolicy,
    PersonalMemoryPool,
    PersonalMemorySpace,
    PersonalMemorySpaceState,
    ProposalOrigin,
    SharedPromotionState,
    StorageClass,
    canonical_json,
    require_tenant_context,
    verify_memory_patch_proposal_hash,
    verify_approval_binding,
    verify_commit_binding,
)
from aioa_memory_kernel.state_machines import (
    PatchTransitionRecord,
    SharedPromotionTransitionRecord,
    activate_personal_memory_space,
    allocate_personal_memory_space,
    configure_personal_memory_space,
    restore_personal_memory_space,
    suspend_personal_memory_space,
    transition_memory_patch,
    transition_shared_promotion,
)


def _active_space(
    *,
    tenant_id: str = TENANT_A,
    user_id: str = USER_A,
    space_id: str = SPACE_A,
) -> PersonalMemorySpace:
    return PersonalMemorySpace(
        schema_version=CONTRACT_SCHEMA_VERSION,
        personal_memory_space_id=space_id,
        tenant_id=tenant_id,
        user_id=user_id,
        state=PersonalMemorySpaceState.ACTIVE,
        display_name="Synthetic private memory",
        created_at=NOW,
        updated_at=LATER,
    )


def _session_proposal() -> MemoryPatchProposal:
    proposal = MemoryPatchProposal(
        schema_version=CONTRACT_SCHEMA_VERSION,
        proposal_id="proposal-session-1",
        tenant_id=TENANT_A,
        owner_user_id=USER_A,
        target_scope=MemoryTargetScope.SESSION,
        target_hat_id=None,
        target_personal_memory_space_id=SPACE_A,
        origin=ProposalOrigin.CRITIC_PROMPT_LOOP,
        proposed_content={"note": "Advisory session candidate."},
        evidence_references=(),
        scope_dimensions=(),
        valid_from=NOW,
        valid_until=None,
        requested_trust_class=MemoryTrustClass.SESSION_MEMORY,
        approval_requirement=ApprovalRequirement.OWNER,
        lifecycle_state=PatchState.DETECTED,
        content_kind=MemoryContentKind.SESSION,
        created_at=NOW,
    )
    for state in (
        PatchState.PROPOSED,
        PatchState.EVIDENCE_BOUND,
        PatchState.VALIDATED,
        PatchState.AWAITING_APPROVAL,
    ):
        proposal, _ = transition_memory_patch(
            proposal,
            target_state=state,
            actor_type=ActorType.SYSTEM,
            actor_id="kernel-contract",
            transitioned_at=NOW,
        )
    return proposal


def _direct_patch_with_state(state: PatchState) -> MemoryPatchProposal:
    base = make_personal_proposal()
    values = {
        field.name: getattr(base, field.name)
        for field in fields(base)
        if field.init
    }
    values["lifecycle_state"] = state
    return MemoryPatchProposal(**values)


def _scoped_proposal(
    *,
    proposal_id: str,
    tenant_id: str,
    user_id: str,
    space_id: str,
    content: object | None = None,
    created_at=NOW,
    target_state: PatchState = PatchState.AWAITING_APPROVAL,
) -> MemoryPatchProposal:
    proposal = MemoryPatchProposal(
        schema_version=CONTRACT_SCHEMA_VERSION,
        proposal_id=proposal_id,
        tenant_id=tenant_id,
        owner_user_id=user_id,
        target_scope=MemoryTargetScope.USER_PERSONAL_HAT,
        target_hat_id=None,
        target_personal_memory_space_id=space_id,
        origin=ProposalOrigin.CRITIC_PROMPT_LOOP,
        proposed_content=(
            {"statement": "Synthetic correction."}
            if content is None
            else content
        ),
        evidence_references=("evidence-1",),
        scope_dimensions=(),
        valid_from=created_at,
        valid_until=None,
        requested_trust_class=MemoryTrustClass.PERSONAL_VERIFIED_PATCH,
        approval_requirement=ApprovalRequirement.OWNER,
        lifecycle_state=PatchState.DETECTED,
        content_kind=MemoryContentKind.FACTUAL,
        created_at=created_at,
    )
    if target_state is PatchState.DETECTED:
        return proposal
    for state in (
        PatchState.PROPOSED,
        PatchState.EVIDENCE_BOUND,
        PatchState.VALIDATED,
        PatchState.AWAITING_APPROVAL,
    ):
        proposal, _ = transition_memory_patch(
            proposal,
            target_state=state,
            actor_type=ActorType.SYSTEM,
            actor_id="kernel-contract",
            transitioned_at=created_at,
        )
        if state is target_state:
            return proposal
    raise ValueError(f"unsupported helper target state {target_state.value}")


def _scoped_approval(
    proposal: MemoryPatchProposal,
    *,
    approval_id: str,
    decision: ApprovalDecision = ApprovalDecision.APPROVE,
) -> MemoryPatchApproval:
    return MemoryPatchApproval(
        schema_version=CONTRACT_SCHEMA_VERSION,
        approval_id=approval_id,
        proposal_id=proposal.proposal_id,
        proposal_content_hash=proposal.content_hash,
        tenant_id=proposal.tenant_id,
        owner_user_id=proposal.owner_user_id,
        personal_memory_space_id=proposal.target_personal_memory_space_id,
        decision=decision,
        approver_type=ActorType.USER,
        approver_id=proposal.owner_user_id or "missing-owner",
        reason_code="OWNER_REVIEW",
        decided_at=LATER,
    )


class PrivilegedConstructionTests(unittest.TestCase):
    def test_verified_memory_item_defaults_inert_and_cannot_self_activate(
        self,
    ) -> None:
        constructor = {
            "schema_version": CONTRACT_SCHEMA_VERSION,
            "memory_item_id": "verified-memory-direct",
            "visibility": MemoryVisibility.PERSONAL,
            "trust_class": MemoryTrustClass.PERSONAL_VERIFIED_PATCH,
            "content_kind": MemoryContentKind.FACTUAL,
            "content": {"statement": "Uncommitted content."},
            "scope_dimensions": (),
            "evidence_references": ("evidence-1",),
            "created_at": NOW,
            "ownership": MemoryOwnership(
                tenant_id=TENANT_A,
                user_id=USER_A,
                personal_memory_space_id=SPACE_A,
            ),
            "source_patch_id": "proposal-uncommitted",
        }
        item = MemoryItem(**constructor)
        self.assertFalse(item.active)
        with self.assertRaises(ContractValidationError):
            replace(item, active=True)

    def test_direct_active_patch_construction_is_rejected(self) -> None:
        with self.assertRaises(ContractValidationError):
            _direct_patch_with_state(PatchState.ACTIVE)

    def test_copy_cannot_jump_from_detected_to_active(self) -> None:
        with self.assertRaises(ContractValidationError):
            replace(
                make_personal_proposal(),
                lifecycle_state=PatchState.ACTIVE,
            )

    def test_direct_active_personal_space_construction_is_rejected(self) -> None:
        with self.assertRaises(ContractValidationError):
            _active_space()

    def test_direct_committed_shared_promotion_is_rejected(self) -> None:
        proposal = make_shared_promotion()
        with self.assertRaises(ContractValidationError):
            replace(
                proposal,
                independent_evidence_references=("independent-evidence-1",),
                independent_evidence_validated=True,
                domain_approval_id="domain-approval-forged",
                shared_commit_id="shared-commit-forged",
                state=SharedPromotionState.SHARED_PATCH_COMMITTED,
            )

    def test_advisory_shared_promotion_rejects_authority_references(
        self,
    ) -> None:
        for changes in (
            {"domain_approval_id": "forged-domain-approval"},
            {"shared_commit_id": "forged-shared-commit"},
        ):
            with self.subTest(changes=changes):
                with self.assertRaises(ContractValidationError):
                    replace(make_shared_promotion(), **changes)

class BindingAndReplayTests(unittest.TestCase):
    def test_source_list_mutation_cannot_change_approved_proposal(self) -> None:
        references = ["evidence-1"]
        proposal = make_personal_proposal(
            state=PatchState.AWAITING_APPROVAL,
            evidence_references=references,  # type: ignore[arg-type]
        )
        approval = make_approval(proposal)
        references.append("attacker-evidence")
        self.assertEqual(proposal.evidence_references, ("evidence-1",))
        verify_approval_binding(proposal, approval)
        commit = make_commit(proposal, approval)
        verify_commit_binding(proposal, approval, commit)

    def test_forced_post_validation_mutation_invalidates_approval(self) -> None:
        proposal = make_personal_proposal(
            state=PatchState.AWAITING_APPROVAL
        )
        approval = make_approval(proposal)
        object.__setattr__(
            proposal,
            "evidence_references",
            ("attacker-evidence",),
        )
        with self.assertRaises(IntegrityError):
            verify_approval_binding(proposal, approval)

    def test_approval_proof_binds_approval_identifier(self) -> None:
        proposal = make_personal_proposal(state=PatchState.AWAITING_APPROVAL)
        original = make_approval(proposal)
        replay = replace(original, approval_id="approval-replayed-id")
        self.assertNotEqual(original.approval_proof, replay.approval_proof)

    def test_session_approval_rejects_cross_user_scope(self) -> None:
        proposal = _session_proposal()
        approval = MemoryPatchApproval(
            schema_version=CONTRACT_SCHEMA_VERSION,
            approval_id="approval-session-1",
            proposal_id=proposal.proposal_id,
            proposal_content_hash=proposal.content_hash,
            tenant_id=proposal.tenant_id,
            owner_user_id=USER_B,
            personal_memory_space_id="space-user-b",
            decision=ApprovalDecision.APPROVE,
            approver_type=ActorType.USER,
            approver_id=USER_A,
            reason_code="OWNER_REVIEW",
            decided_at=LATER,
        )
        with self.assertRaises(ContractValidationError):
            verify_approval_binding(proposal, approval)

    def test_critic_event_is_rejected_as_approval_deterministically(self) -> None:
        proposal = make_personal_proposal(state=PatchState.AWAITING_APPROVAL)
        critic_event = CorrectionCandidate(
            event_id="critic-event-1",
            tenant_id=TENANT_A,
            user_id=USER_A,
            personal_memory_space_id=SPACE_A,
            source_component=ActorType.CRITIC_PROMPT_LOOP,
            run_id="run-1",
            model_binding_id="model-1",
            draft_v1_reference="draft-1",
            detected_claims=(
                # A real claim is supplied so rejection exercises approval typing.
                make_claim(),
            ),
            proposed_correction="Candidate only.",
            available_evidence_references=("evidence-1",),
            uncertainty=0.1,
            created_at=NOW,
            state=CorrectionCandidateState.PROPOSED,
        )
        with self.assertRaisesRegex(
            ContractValidationError, "MemoryPatchApproval"
        ):
            verify_approval_binding(
                proposal,
                critic_event,  # type: ignore[arg-type]
            )

    def test_approval_transition_cannot_predate_decision(self) -> None:
        proposal = make_personal_proposal(state=PatchState.AWAITING_APPROVAL)
        approval = make_approval(proposal)
        with self.assertRaises(ContractValidationError):
            transition_memory_patch(
                proposal,
                target_state=PatchState.APPROVED,
                actor_type=ActorType.USER,
                actor_id=USER_A,
                transitioned_at=NOW,
                approval=approval,
            )

    def test_commit_transition_cannot_predate_commit_receipt(self) -> None:
        proposal = make_personal_proposal(state=PatchState.APPROVED)
        approval = make_approval(proposal)
        commit = make_commit(proposal, approval)
        with self.assertRaises(ContractValidationError):
            transition_memory_patch(
                proposal,
                target_state=PatchState.COMMITTED,
                actor_type=ActorType.COMMIT_SERVICE,
                actor_id=commit.actor_id,
                transitioned_at=NOW,
                approval=approval,
                commit=commit,
            )


class NestedMutationAndTypeSafetyTests(unittest.TestCase):
    def test_pool_copies_space_sequence_before_validation(self) -> None:
        spaces: list[PersonalMemorySpace] = []
        pool = PersonalMemoryPool(
            tenant_id=TENANT_A,
            user_id=USER_A,
            quota_policy=PersonalHatQuotaPolicy(),
            spaces=spaces,  # type: ignore[arg-type]
        )
        spaces.append(
            PersonalMemorySpace(
                schema_version=CONTRACT_SCHEMA_VERSION,
                personal_memory_space_id="space-foreign",
                tenant_id=TENANT_B,
                user_id=USER_B,
                state=PersonalMemorySpaceState.EMPTY,
                display_name=None,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        self.assertEqual(pool.spaces, ())

    def test_manifest_copies_capabilities_before_authority_validation(self) -> None:
        capabilities = ["REQUEST_NORMALIZATION"]
        manifest = replace(
            make_manifest(),
            capabilities=capabilities,  # type: ignore[arg-type]
        )
        capabilities.append("SHELL_EXECUTION")
        self.assertEqual(manifest.capabilities, ("REQUEST_NORMALIZATION",))

    def test_model_experience_rejects_empty_scoped_identity(self) -> None:
        with self.assertRaises(ContractValidationError):
            ModelExperienceEvent(
                model_experience_event_id="experience-empty-owner",
                tenant_id="",
                user_id="",
                personal_memory_space_id=None,
                provider="synthetic-provider",
                model_family="synthetic-family",
                exact_model_version="synthetic-1",
                failure_category="synthetic-failure",
                kernel_run_id="run-1",
                claim_category="synthetic-claim",
                correction_outcome=ModelExperienceOutcome.CORRECTED,
                verifier_outcome="CORRECTED",
                created_at=NOW,
                expires_at=LATER,
                retention_policy="bounded",
            )

    def test_restore_rejects_identifier_type_coercion(self) -> None:
        pool = PersonalMemoryPool(
            tenant_id=TENANT_A,
            user_id=USER_A,
            quota_policy=PersonalHatQuotaPolicy(),
        )
        pool, _ = allocate_personal_memory_space(
            pool,
            personal_memory_space_id="123",
            created_at=NOW,
            tenant_id=TENANT_A,
            user_id=USER_A,
        )
        pool = configure_personal_memory_space(
            pool,
            personal_memory_space_id="123",
            display_name="Synthetic private memory",
            changed_at=NOW,
            tenant_id=TENANT_A,
            user_id=USER_A,
        )
        pool = activate_personal_memory_space(
            pool,
            personal_memory_space_id="123",
            changed_at=NOW,
            tenant_id=TENANT_A,
            user_id=USER_A,
        )
        pool = suspend_personal_memory_space(
            pool,
            personal_memory_space_id="123",
            changed_at=NOW,
            tenant_id=TENANT_A,
            user_id=USER_A,
        )
        with self.assertRaises(ContractValidationError):
            restore_personal_memory_space(
                pool,
                personal_memory_space_id=123,
                changed_at=LATER,
                tenant_id=TENANT_A,
                user_id=USER_A,
            )

    def test_non_string_evidence_reference_has_defined_error(self) -> None:
        with self.assertRaises(ContractValidationError):
            make_personal_proposal(
                evidence_references=(
                    ["not-a-string"],  # type: ignore[list-item]
                )
            )

    def test_normal_attribute_assignment_is_frozen(self) -> None:
        proposal = make_personal_proposal()
        with self.assertRaises(FrozenInstanceError):
            proposal.owner_user_id = USER_B  # type: ignore[misc]
        approval = make_approval(
            make_personal_proposal(
                state=PatchState.AWAITING_APPROVAL
            )
        )
        with self.assertRaises(FrozenInstanceError):
            approval.tenant_id = TENANT_B  # type: ignore[misc]

    def test_shared_evidence_and_scope_sequences_are_copied(self) -> None:
        evidence = []
        proposal = replace(
            make_shared_promotion(),
            independent_evidence_references=evidence,
        )
        evidence.append("late-evidence")
        self.assertEqual(proposal.independent_evidence_references, ())

    def test_shared_evidence_flag_requires_exact_boolean(self) -> None:
        with self.assertRaises(ContractValidationError):
            replace(
                make_shared_promotion(),
                independent_evidence_validated="true",  # type: ignore[arg-type]
            )

    def test_approval_and_commit_optional_owner_cannot_be_blank(self) -> None:
        proposal = make_personal_proposal(
            state=PatchState.AWAITING_APPROVAL
        )
        with self.assertRaises(ContractValidationError):
            MemoryPatchApproval(
                schema_version=CONTRACT_SCHEMA_VERSION,
                approval_id="approval-blank-owner",
                proposal_id=proposal.proposal_id,
                proposal_content_hash=proposal.content_hash,
                tenant_id=proposal.tenant_id,
                owner_user_id=" ",
                personal_memory_space_id=SPACE_A,
                decision=ApprovalDecision.APPROVE,
                approver_type=ActorType.USER,
                approver_id=USER_A,
                reason_code="OWNER_REVIEW",
                decided_at=LATER,
            )
        approval = make_approval(proposal)
        for changes in (
            {"owner_user_id": None},
            {"personal_memory_space_id": None},
        ):
            with self.subTest(approval_scope=changes):
                with self.assertRaises(ContractValidationError):
                    replace(approval, **changes)
        with self.assertRaises(ContractValidationError):
            MemoryPatchCommit(
                schema_version=CONTRACT_SCHEMA_VERSION,
                commit_id="commit-blank-owner",
                proposal_id=proposal.proposal_id,
                proposal_content_hash=proposal.content_hash,
                approval_id=approval.approval_id,
                approval_proof=approval.approval_proof,
                committed_patch_id="patch-blank-owner",
                tenant_id=proposal.tenant_id,
                owner_user_id=" ",
                personal_memory_space_id=SPACE_A,
                actor_type=ActorType.COMMIT_SERVICE,
                actor_id="bounded-commit-service",
                storage_class=StorageClass.CRDB_TRANSACTIONAL,
                committed_at=LATER,
            )
        approved = make_personal_proposal(state=PatchState.APPROVED)
        commit = make_commit(approved, make_approval(approved))
        for changes in (
            {"owner_user_id": None},
            {"personal_memory_space_id": None},
        ):
            with self.subTest(commit_scope=changes):
                with self.assertRaises(ContractValidationError):
                    replace(commit, **changes)


class CrossScopeIsolationMatrixTests(unittest.TestCase):
    def setUp(self) -> None:
        self.proposal_a = _scoped_proposal(
            proposal_id="proposal-a",
            tenant_id=TENANT_A,
            user_id=USER_A,
            space_id="space-a",
        )
        self.proposal_b = _scoped_proposal(
            proposal_id="proposal-b",
            tenant_id=TENANT_B,
            user_id=USER_B,
            space_id="space-b",
        )
        self.approval_a = _scoped_approval(
            self.proposal_a,
            approval_id="approval-a",
        )
        self.approval_b = _scoped_approval(
            self.proposal_b,
            approval_id="approval-b",
        )

    def test_user_a_approval_cannot_authorize_user_b_proposal(self) -> None:
        with self.assertRaises(ContractValidationError):
            verify_approval_binding(self.proposal_b, self.approval_a)

    def test_personal_patch_cannot_delegate_owner_approval_to_reviewer(
        self,
    ) -> None:
        base = make_personal_proposal()
        values = {
            field.name: getattr(base, field.name)
            for field in fields(base)
            if field.init
        }
        values["approval_requirement"] = ApprovalRequirement.DOMAIN_REVIEWER
        with self.assertRaises(ContractValidationError):
            MemoryPatchProposal(**values)

    def test_tenant_a_approval_cannot_authorize_tenant_b_proposal(self) -> None:
        copied = replace(
            self.approval_a,
            proposal_id=self.proposal_b.proposal_id,
            proposal_content_hash=self.proposal_b.content_hash,
        )
        with self.assertRaises(ContractValidationError):
            verify_approval_binding(self.proposal_b, copied)

    def test_personal_hat_identifier_is_scoped_by_owner_and_tenant(self) -> None:
        ownership_a = MemoryOwnership(
            tenant_id=TENANT_A,
            user_id=USER_A,
            personal_memory_space_id="same-visible-id",
        )
        ownership_b = MemoryOwnership(
            tenant_id=TENANT_B,
            user_id=USER_B,
            personal_memory_space_id="same-visible-id",
        )
        self.assertNotEqual(ownership_a, ownership_b)
        self.assertNotEqual(
            (
                ownership_a.tenant_id,
                ownership_a.user_id,
                ownership_a.personal_memory_space_id,
            ),
            (
                ownership_b.tenant_id,
                ownership_b.user_id,
                ownership_b.personal_memory_space_id,
            ),
        )

    def test_copying_owner_invalidates_existing_approval(self) -> None:
        object.__setattr__(self.proposal_a, "owner_user_id", USER_B)
        with self.assertRaises(IntegrityError):
            verify_approval_binding(self.proposal_a, self.approval_a)

    def test_copying_tenant_invalidates_existing_commit(self) -> None:
        approved = transition_memory_patch(
            self.proposal_a,
            target_state=PatchState.APPROVED,
            actor_type=ActorType.USER,
            actor_id=USER_A,
            transitioned_at=LATER,
            approval=self.approval_a,
        )[0]
        commit = make_commit(approved, self.approval_a)
        object.__setattr__(approved, "tenant_id", TENANT_B)
        with self.assertRaises(IntegrityError):
            verify_commit_binding(approved, self.approval_a, commit)

    def test_serialization_keeps_owner_and_tenant_scope(self) -> None:
        raw = canonical_json(self.proposal_a)
        self.assertIn(f'"tenant_id":"{TENANT_A}"', raw)
        self.assertIn(f'"owner_user_id":"{USER_A}"', raw)
        self.assertIn('"target_personal_memory_space_id":"space-a"', raw)

    def test_empty_personal_hat_does_not_create_first_writer_ownership(self) -> None:
        pool, empty = allocate_personal_memory_space(
            PersonalMemoryPool(
                tenant_id=TENANT_A,
                user_id=USER_A,
                quota_policy=PersonalHatQuotaPolicy(),
            ),
            personal_memory_space_id="empty-space-a",
            created_at=NOW,
            tenant_id=TENANT_A,
            user_id=USER_A,
        )
        del pool
        attacker_proposal = _scoped_proposal(
            proposal_id="attacker-proposal",
            tenant_id=TENANT_A,
            user_id=USER_B,
            space_id=empty.personal_memory_space_id,
        )
        self.assertNotEqual(attacker_proposal.ownership, empty.ownership)
        owner_proposal = _scoped_proposal(
            proposal_id="owner-proposal",
            tenant_id=TENANT_A,
            user_id=USER_A,
            space_id=empty.personal_memory_space_id,
        )
        owner_approval = _scoped_approval(
            owner_proposal,
            approval_id="owner-approval",
        )
        with self.assertRaises(ContractValidationError):
            verify_approval_binding(attacker_proposal, owner_approval)


class ReplayBindingMatrixTests(unittest.TestCase):
    def setUp(self) -> None:
        self.proposal = _scoped_proposal(
            proposal_id="proposal-replay",
            tenant_id=TENANT_A,
            user_id=USER_A,
            space_id=SPACE_A,
        )
        self.approval = _scoped_approval(
            self.proposal,
            approval_id="approval-replay",
        )

    def test_same_approval_rejects_different_content(self) -> None:
        changed = _scoped_proposal(
            proposal_id=self.proposal.proposal_id,
            tenant_id=TENANT_A,
            user_id=USER_A,
            space_id=SPACE_A,
            content={"statement": "Changed content."},
        )
        with self.assertRaises(ContractValidationError):
            verify_approval_binding(changed, self.approval)

    def test_same_approval_rejects_later_reconstructed_revision(self) -> None:
        later = _scoped_proposal(
            proposal_id=self.proposal.proposal_id,
            tenant_id=TENANT_A,
            user_id=USER_A,
            space_id=SPACE_A,
            created_at=NOW + timedelta(minutes=1),
        )
        with self.assertRaises(ContractValidationError):
            verify_approval_binding(later, self.approval)

    def test_same_approval_rejects_another_personal_hat(self) -> None:
        other_hat = _scoped_proposal(
            proposal_id=self.proposal.proposal_id,
            tenant_id=TENANT_A,
            user_id=USER_A,
            space_id="space-other",
        )
        with self.assertRaises(ContractValidationError):
            verify_approval_binding(other_hat, self.approval)

    def test_approval_decision_cannot_be_replayed_for_rejection(self) -> None:
        with self.assertRaises(ContractValidationError):
            transition_memory_patch(
                self.proposal,
                target_state=PatchState.REJECTED,
                actor_type=ActorType.USER,
                actor_id=USER_A,
                transitioned_at=LATER,
                approval=self.approval,
            )

    def test_commit_cannot_be_used_before_approved_state(self) -> None:
        commit = make_commit(self.proposal, self.approval)
        with self.assertRaises(InvalidTransition):
            transition_memory_patch(
                self.proposal,
                target_state=PatchState.COMMITTED,
                actor_type=ActorType.COMMIT_SERVICE,
                actor_id=commit.actor_id,
                transitioned_at=LATER,
                approval=self.approval,
                commit=commit,
            )

    def test_commit_rejects_different_proposal(self) -> None:
        approved = transition_memory_patch(
            self.proposal,
            target_state=PatchState.APPROVED,
            actor_type=ActorType.USER,
            actor_id=USER_A,
            transitioned_at=LATER,
            approval=self.approval,
        )[0]
        commit = make_commit(approved, self.approval)
        other = _scoped_proposal(
            proposal_id="proposal-other",
            tenant_id=TENANT_A,
            user_id=USER_A,
            space_id=SPACE_A,
        )
        with self.assertRaises(ContractValidationError):
            verify_commit_binding(other, self.approval, commit)

    def test_reconstructed_same_binding_is_not_false_single_use_protection(
        self,
    ) -> None:
        replay = replace(self.approval)
        self.assertEqual(replay, self.approval)
        verify_approval_binding(self.proposal, replay)
        self.assertEqual(replay.approval_proof, self.approval.approval_proof)

    def test_same_commit_id_with_conflicting_payload_has_different_hash(self) -> None:
        approved = transition_memory_patch(
            self.proposal,
            target_state=PatchState.APPROVED,
            actor_type=ActorType.USER,
            actor_id=USER_A,
            transitioned_at=LATER,
            approval=self.approval,
        )[0]
        commit = make_commit(approved, self.approval)
        conflicting = replace(
            commit,
            committed_patch_id="different-committed-patch",
        )
        self.assertNotEqual(commit.commit_hash, conflicting.commit_hash)
        with self.assertRaises(ContractValidationError):
            verify_commit_binding(
                approved,
                self.approval,
                replace(conflicting, proposal_id="different-proposal"),
            )

    def test_shared_domain_approval_id_is_not_treated_as_authority(self) -> None:
        for index in (1, 2):
            proposal = replace(
                make_shared_promotion(),
                shared_promotion_proposal_id=f"promotion-{index}",
            )
            proposal, _ = transition_shared_promotion(
                proposal,
                target_state=SharedPromotionState.EVIDENCE_REVALIDATED,
                actor_type=ActorType.HUMAN_REVIEWER,
                actor_id="evidence-reviewer",
                transitioned_at=LATER,
                independent_evidence_references=(
                    f"independent-evidence-{index}",
                ),
                independent_evidence_validated=True,
            )
            proposal, _ = transition_shared_promotion(
                proposal,
                target_state=SharedPromotionState.DOMAIN_REVIEW_REQUIRED,
                actor_type=ActorType.SYSTEM,
                actor_id="review-router",
                transitioned_at=LATER,
            )
            with self.subTest(proposal=index):
                with self.assertRaisesRegex(
                    AuthorityViolation, "future reference"
                ):
                    transition_shared_promotion(
                        proposal,
                        target_state=(
                            SharedPromotionState.APPROVED_FOR_SHARED
                        ),
                        actor_type=ActorType.HUMAN_REVIEWER,
                        actor_id="domain-reviewer",
                        transitioned_at=LATER,
                        domain_approval_id="reused-domain-approval",
                    )


class AdvisoryEntityNonAuthorityTests(unittest.TestCase):
    def test_all_advisory_actor_types_are_rejected_as_approvers(self) -> None:
        proposal = make_personal_proposal(
            state=PatchState.AWAITING_APPROVAL
        )
        for actor_type in (
            ActorType.MODEL,
            ActorType.MODEL_VERIFIER,
            ActorType.KNOWLEDGE_HAT,
            ActorType.KNOWLEDGE_KERNEL,
            ActorType.KNOWLEDGE_HUB,
            ActorType.CRITIC_PROMPT_LOOP,
        ):
            with self.subTest(actor=actor_type.value):
                with self.assertRaises(AuthorityViolation):
                    MemoryPatchApproval(
                        schema_version=CONTRACT_SCHEMA_VERSION,
                        approval_id=f"approval-{actor_type.value.lower()}",
                        proposal_id=proposal.proposal_id,
                        proposal_content_hash=proposal.content_hash,
                        tenant_id=proposal.tenant_id,
                        owner_user_id=proposal.owner_user_id,
                        personal_memory_space_id=(
                            proposal.target_personal_memory_space_id
                        ),
                        decision=ApprovalDecision.APPROVE,
                        approver_type=actor_type,
                        approver_id=f"{actor_type.value.lower()}-1",
                        reason_code="FORBIDDEN",
                        decided_at=LATER,
                    )

    def test_knowledge_hat_manifest_cannot_be_used_as_approval(self) -> None:
        proposal = make_personal_proposal(
            state=PatchState.AWAITING_APPROVAL
        )
        with self.assertRaisesRegex(
            ContractValidationError, "MemoryPatchApproval"
        ):
            verify_approval_binding(
                proposal,
                make_manifest(),  # type: ignore[arg-type]
            )

    def test_personal_patch_content_cannot_change_owner_or_tenant(self) -> None:
        proposal = make_personal_proposal(
            content={
                "owner_user_id": USER_B,
                "tenant_id": TENANT_B,
                "human_verified": True,
            }
        )
        self.assertEqual(proposal.owner_user_id, USER_A)
        self.assertEqual(proposal.tenant_id, TENANT_A)
        self.assertIs(proposal.lifecycle_state, PatchState.DETECTED)

    def test_critic_event_cannot_be_used_as_commit(self) -> None:
        proposal = make_personal_proposal(state=PatchState.APPROVED)
        approval = make_approval(proposal)
        critic = CorrectionCandidate(
            event_id="critic-commit-1",
            tenant_id=TENANT_A,
            user_id=USER_A,
            personal_memory_space_id=SPACE_A,
            source_component=ActorType.CRITIC_PROMPT_LOOP,
            run_id="run-1",
            model_binding_id="model-1",
            draft_v1_reference="draft-1",
            detected_claims=(make_claim(),),
            proposed_correction="Candidate only.",
            available_evidence_references=("evidence-1",),
            uncertainty=0.1,
            created_at=NOW,
            state=CorrectionCandidateState.PROPOSED,
        )
        with self.assertRaisesRegex(
            ContractValidationError, "MemoryPatchCommit"
        ):
            verify_commit_binding(
                proposal,
                approval,
                critic,  # type: ignore[arg-type]
            )


class MalformedContractMatrixTests(unittest.TestCase):
    def test_required_proposal_identity_fields_fail_closed(self) -> None:
        base = make_personal_proposal()
        values = {
            field.name: getattr(base, field.name)
            for field in fields(base)
            if field.init
        }
        for field_name in (
            "proposal_id",
            "tenant_id",
            "owner_user_id",
            "target_personal_memory_space_id",
        ):
            for invalid in (None, "", " ", [], {}):
                mutated = dict(values)
                mutated[field_name] = invalid
                with self.subTest(field=field_name, invalid=repr(invalid)):
                    with self.assertRaises(ContractValidationError):
                        MemoryPatchProposal(**mutated)

    def test_required_approval_identifiers_fail_closed(self) -> None:
        proposal = make_personal_proposal(
            state=PatchState.AWAITING_APPROVAL
        )
        valid = {
            "schema_version": CONTRACT_SCHEMA_VERSION,
            "approval_id": "approval-valid",
            "proposal_id": proposal.proposal_id,
            "proposal_content_hash": proposal.content_hash,
            "tenant_id": proposal.tenant_id,
            "owner_user_id": proposal.owner_user_id,
            "personal_memory_space_id": (
                proposal.target_personal_memory_space_id
            ),
            "decision": ApprovalDecision.APPROVE,
            "approver_type": ActorType.USER,
            "approver_id": USER_A,
            "reason_code": "OWNER_REVIEW",
            "decided_at": LATER,
        }
        for field_name in (
            "approval_id",
            "proposal_id",
            "tenant_id",
            "approver_id",
        ):
            for invalid in (None, "", " ", [], {}):
                mutated = dict(valid)
                mutated[field_name] = invalid
                with self.subTest(field=field_name, invalid=repr(invalid)):
                    with self.assertRaises(ContractValidationError):
                        MemoryPatchApproval(**mutated)

    def test_required_commit_identifiers_fail_closed(self) -> None:
        proposal = make_personal_proposal(state=PatchState.APPROVED)
        approval = make_approval(proposal)
        valid = {
            "schema_version": CONTRACT_SCHEMA_VERSION,
            "commit_id": "commit-valid",
            "proposal_id": proposal.proposal_id,
            "proposal_content_hash": proposal.content_hash,
            "approval_id": approval.approval_id,
            "approval_proof": approval.approval_proof,
            "committed_patch_id": "committed-valid",
            "tenant_id": proposal.tenant_id,
            "owner_user_id": proposal.owner_user_id,
            "personal_memory_space_id": (
                proposal.target_personal_memory_space_id
            ),
            "actor_type": ActorType.COMMIT_SERVICE,
            "actor_id": "bounded-commit-service",
            "storage_class": StorageClass.CRDB_TRANSACTIONAL,
            "committed_at": LATER,
        }
        for field_name in (
            "commit_id",
            "proposal_id",
            "approval_id",
            "committed_patch_id",
            "tenant_id",
            "actor_id",
        ):
            for invalid in (None, "", " ", [], {}):
                mutated = dict(valid)
                mutated[field_name] = invalid
                with self.subTest(field=field_name, invalid=repr(invalid)):
                    with self.assertRaises(ContractValidationError):
                        MemoryPatchCommit(**mutated)

    def test_invalid_digest_shapes_fail_closed(self) -> None:
        proposal = make_personal_proposal(
            state=PatchState.AWAITING_APPROVAL
        )
        for invalid in (None, "", "a" * 63, "A" * 64, "g" * 64, []):
            with self.subTest(digest=repr(invalid)):
                with self.assertRaises(ContractValidationError):
                    MemoryPatchApproval(
                        schema_version=CONTRACT_SCHEMA_VERSION,
                        approval_id="approval-invalid-digest",
                        proposal_id=proposal.proposal_id,
                        proposal_content_hash=invalid,  # type: ignore[arg-type]
                        tenant_id=proposal.tenant_id,
                        owner_user_id=proposal.owner_user_id,
                        personal_memory_space_id=(
                            proposal.target_personal_memory_space_id
                        ),
                        decision=ApprovalDecision.APPROVE,
                        approver_type=ActorType.USER,
                        approver_id=USER_A,
                        reason_code="OWNER_REVIEW",
                        decided_at=LATER,
                    )

    def test_tenant_context_type_confusion_has_ownership_error(self) -> None:
        for tenant_id, user_id in (
            ({}, USER_A),
            (TENANT_A, {}),
            ([], USER_A),
            (TENANT_A, []),
            (" ", USER_A),
            (TENANT_A, " "),
        ):
            with self.subTest(tenant=tenant_id, user=user_id):
                with self.assertRaises(OwnershipViolation):
                    require_tenant_context(
                        tenant_id,  # type: ignore[arg-type]
                        user_id,  # type: ignore[arg-type]
                    )

    def test_audit_optional_identity_and_payload_types_fail_closed(self) -> None:
        valid = {
            "schema_version": CONTRACT_SCHEMA_VERSION,
            "audit_event_id": "audit-malformed-1",
            "tenant_id": TENANT_A,
            "user_id": USER_A,
            "kernel_run_id": "run-1",
            "event_type": "SYNTHETIC",
            "sequence_number": 0,
            "previous_event_hash": None,
            "resource_type": "Synthetic",
            "resource_id": "resource-1",
            "state_before": None,
            "state_after": None,
            "actor_type": ActorType.SYSTEM,
            "actor_id": "validator",
            "content_hashes": {"record": "a" * 64},
            "created_at": NOW,
            "personal_memory_space_id": SPACE_A,
        }
        for field_name, invalid in (
            ("user_id", ""),
            ("kernel_run_id", " "),
            ("content_hashes", []),
            ("content_hashes", {"record": "not-a-digest"}),
        ):
            mutated = dict(valid)
            mutated[field_name] = invalid
            with self.subTest(field=field_name, invalid=repr(invalid)):
                with self.assertRaises(ContractValidationError):
                    AuditEvent(**mutated)
        missing_user = dict(valid)
        missing_user["user_id"] = None
        with self.assertRaises(ContractValidationError):
            AuditEvent(**missing_user)

    def test_transition_records_reject_invalid_edges_and_digests(self) -> None:
        with self.assertRaises(InvalidTransition):
            PatchTransitionRecord(
                proposal_id="proposal-1",
                proposal_content_hash="a" * 64,
                state_before=PatchState.DETECTED,
                state_after=PatchState.ACTIVE,
                actor_type=ActorType.SYSTEM,
                actor_id="system",
                transitioned_at=NOW,
            )
        with self.assertRaises(ContractValidationError):
            SharedPromotionTransitionRecord(
                shared_promotion_proposal_id="promotion-1",
                proposal_hash_before="not-a-digest",
                proposal_hash_after="b" * 64,
                state_before=(
                    SharedPromotionState.SHARED_PROMOTION_PROPOSED
                ),
                state_after=SharedPromotionState.EVIDENCE_REVALIDATED,
                actor_type=ActorType.SYSTEM,
                actor_id="system",
                transitioned_at=NOW,
            )

    def test_hash_canonicalization_covers_all_identity_dimensions(self) -> None:
        base = make_personal_proposal()
        mutations = (
            {"proposal_id": "proposal-other"},
            {"tenant_id": TENANT_B},
            {"owner_user_id": USER_B},
            {"target_personal_memory_space_id": "space-other"},
            {"origin": ProposalOrigin.USER_ENTRY},
            {"proposed_content": {"statement": "Different."}},
            {"created_at": NOW + timedelta(seconds=1)},
        )
        for changes in mutations:
            with self.subTest(changes=changes):
                changed = replace(base, **changes)
                self.assertNotEqual(changed.content_hash, base.content_hash)

    def test_forced_schema_version_mutation_invalidates_hash(self) -> None:
        proposal = make_personal_proposal()
        object.__setattr__(proposal, "schema_version", "2.0.0")
        with self.assertRaises(IntegrityError):
            verify_memory_patch_proposal_hash(proposal)

    def test_temporal_ordering_fails_closed(self) -> None:
        awaiting = make_personal_proposal(
            state=PatchState.AWAITING_APPROVAL
        )
        early_approval = replace(
            make_approval(awaiting),
            decided_at=NOW - timedelta(seconds=1),
        )
        with self.assertRaises(ContractValidationError):
            verify_approval_binding(awaiting, early_approval)

        approved = make_personal_proposal(state=PatchState.APPROVED)
        approval = make_approval(approved)
        early_commit = replace(
            make_commit(approved, approval),
            committed_at=NOW,
        )
        with self.assertRaises(ContractValidationError):
            verify_commit_binding(approved, approval, early_commit)

        with self.assertRaises(ContractValidationError):
            replace(
                make_personal_proposal(),
                valid_from=LATER,
                valid_until=NOW,
            )

        with self.assertRaises(ContractValidationError):
            MemoryItem(
                schema_version=CONTRACT_SCHEMA_VERSION,
                memory_item_id="expired-before-creation",
                visibility=MemoryVisibility.PERSONAL,
                trust_class=MemoryTrustClass.USER_ASSERTED_MEMORY,
                content_kind=MemoryContentKind.FACTUAL,
                content={"statement": "Malformed time."},
                scope_dimensions=(),
                evidence_references=(),
                created_at=NOW,
                ownership=MemoryOwnership(
                    tenant_id=TENANT_A,
                    user_id=USER_A,
                    personal_memory_space_id=SPACE_A,
                ),
                expires_at=NOW - timedelta(seconds=1),
            )


class PublicApiSafetyTests(unittest.TestCase):
    def test_private_transition_permits_are_not_public_exports(self) -> None:
        for name in (
            "_replace_memory_patch_lifecycle",
            "_replace_personal_memory_space",
            "_replace_shared_promotion_lifecycle",
            "_MEMORY_PATCH_LIFECYCLE_PERMIT",
            "_PERSONAL_MEMORY_LIFECYCLE_PERMIT",
            "_SHARED_PROMOTION_LIFECYCLE_PERMIT",
        ):
            with self.subTest(name=name):
                self.assertFalse(hasattr(kernel, name))

    def test_public_contract_aliases_are_identical_not_duplicates(self) -> None:
        from aioa_memory_kernel import MemoryPatchProposal as RootProposal
        from aioa_memory_kernel.contracts import (
            MemoryPatchProposal as ContractProposal,
        )

        self.assertIs(RootProposal, ContractProposal)

    def test_unknown_and_wrong_case_enum_values_fail(self) -> None:
        for value in ("active", "ACTIVE_UNKNOWN", 1, True, {}):
            with self.subTest(value=value):
                with self.assertRaises((ContractValidationError, ValueError)):
                    if isinstance(value, str):
                        PatchState(value)
                    else:
                        replace(
                            make_personal_proposal(),
                            lifecycle_state=value,  # type: ignore[arg-type]
                        )

    def test_public_transition_rejects_plain_string_actor(self) -> None:
        proposal = make_personal_proposal()
        with self.assertRaises(ContractValidationError):
            transition_memory_patch(
                proposal,
                target_state=PatchState.PROPOSED,
                actor_type="SYSTEM",  # type: ignore[arg-type]
                actor_id="system",
                transitioned_at=NOW,
            )

    def test_naive_transition_timestamp_is_rejected(self) -> None:
        with self.assertRaises(ContractValidationError):
            transition_memory_patch(
                make_personal_proposal(),
                target_state=PatchState.PROPOSED,
                actor_type=ActorType.SYSTEM,
                actor_id="system",
                transitioned_at=datetime(2030, 1, 2),
            )


class ExistingBindingControlsTests(unittest.TestCase):
    """Positive controls prove failures above are not unrelated setup errors."""

    def test_exact_personal_approval_and_commit_bindings_pass(self) -> None:
        proposal = make_personal_proposal(state=PatchState.APPROVED)
        approval = make_approval(proposal)
        commit = MemoryPatchCommit(
            schema_version=CONTRACT_SCHEMA_VERSION,
            commit_id="commit-positive-control",
            proposal_id=proposal.proposal_id,
            proposal_content_hash=proposal.content_hash,
            approval_id=approval.approval_id,
            approval_proof=approval.approval_proof,
            committed_patch_id="committed-positive-control",
            tenant_id=proposal.tenant_id,
            owner_user_id=proposal.owner_user_id,
            personal_memory_space_id=proposal.target_personal_memory_space_id,
            actor_type=ActorType.COMMIT_SERVICE,
            actor_id="bounded-commit-service",
            storage_class=StorageClass.CRDB_TRANSACTIONAL,
            committed_at=LATER,
        )
        verify_approval_binding(proposal, approval)
        verify_commit_binding(proposal, approval, commit)

    def test_cross_tenant_commit_is_rejected(self) -> None:
        proposal = make_personal_proposal(state=PatchState.APPROVED)
        approval = make_approval(proposal)
        commit = replace(make_commit(proposal, approval), tenant_id=TENANT_B)
        with self.assertRaises(ContractValidationError):
            verify_commit_binding(proposal, approval, commit)


if __name__ == "__main__":
    unittest.main()
