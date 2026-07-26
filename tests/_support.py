"""Synthetic builders shared by standard-library contract tests."""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from aioa_memory_kernel.contracts import (  # noqa: E402
    ActionPolicy,
    ActorType,
    ApprovalDecision,
    ApprovalRequirement,
    ClaimCandidate,
    CONTRACT_SCHEMA_VERSION,
    CorrectionPacket,
    CorrectionRequirement,
    DeidentificationStatus,
    EvidenceItem,
    EvidenceStatus,
    HatManifest,
    HatScopeDimensionDefinition,
    HatSecurityPolicy,
    KnowledgeRoute,
    MemoryContentKind,
    MemoryOwnership,
    MemoryPatchApproval,
    MemoryPatchCommit,
    MemoryPatchProposal,
    MemoryTargetScope,
    MemoryTrustClass,
    MissingDimensionBehavior,
    PatchState,
    PersonalHatQuotaPolicy,
    PersonalMemoryPool,
    PrivateDataClassification,
    ProposalOrigin,
    ScopeComparisonMode,
    ScopeDimension,
    ScopeValueType,
    SharedPromotionProposal,
    SharedPromotionState,
    StorageClass,
)


NOW = datetime(2030, 1, 2, 12, 0, tzinfo=UTC)
LATER = NOW + timedelta(hours=1)
TENANT_A = "tenant-alpha"
TENANT_B = "tenant-beta"
USER_A = "user-alpha"
USER_B = "user-beta"
SPACE_A = "space-alpha-1"
RUN_A = "kernel-run-alpha-1"
MODEL_A = "model-binding-alpha"


def make_scope(
    *,
    name: str = "runtime_version",
    value: str = "4.2.0",
) -> ScopeDimension:
    return ScopeDimension(
        name=name,
        value=value,
        value_type=ScopeValueType.SEMVER,
        comparison_mode=ScopeComparisonMode.SEMVER,
        source="synthetic-request",
        required=True,
    )


def make_manifest(
    *,
    hat_id: str = "synthetic-software-version",
    domain_id: str = "synthetic.software",
    dimension_name: str = "runtime_version",
) -> HatManifest:
    return HatManifest(
        schema_version="1.0.0",
        hat_id=hat_id,
        hat_version="1.0.0",
        display_name=f"Synthetic {hat_id}",
        domain_ids=(domain_id,),
        kernel_api_compatibility=">=1.0.0,<2.0.0",
        supported_languages=("en",),
        scope_dimension_definitions=(
            HatScopeDimensionDefinition(
                name=dimension_name,
                value_type=ScopeValueType.SEMVER,
                comparison_mode=ScopeComparisonMode.SEMVER,
                required=True,
                default_behavior=MissingDimensionBehavior.AMBIGUOUS,
                missing_creates_ambiguous=True,
                description="Synthetic version dimension.",
            ),
        ),
        capabilities=(
            "REQUEST_NORMALIZATION",
            "EVIDENCE_CONSTRAINTS",
            "CORRECTION_PROPOSAL",
        ),
        source_authority_policy={"policy_version": "synthetic-1"},
        retrieval_contract={"contract_version": "synthetic-1"},
        claim_contract={"contract_version": "synthetic-1"},
        conflict_contract={"contract_version": "synthetic-1"},
        memory_policy={"proposal_only": True},
        security_policy=HatSecurityPolicy(),
        extension_points={"normalizer_contract": "synthetic-v1"},
    )


def make_evidence(
    *,
    evidence_id: str = "evidence-1",
    source_version_id: str = "source-version-1",
    content_hash: str = "a" * 64,
    trust_class: MemoryTrustClass = MemoryTrustClass.CANONICAL_SOURCE_EVIDENCE,
) -> EvidenceItem:
    return EvidenceItem(
        evidence_id=evidence_id,
        source_id="synthetic-source",
        source_version_id=source_version_id,
        citation_reference=f"synthetic:{source_version_id}",
        content_hash=content_hash,
        trust_class=trust_class,
        authority_rank=10,
        scope_dimensions=(make_scope(),),
        retrieved_at=NOW,
        valid_from=NOW - timedelta(days=1),
        valid_until=NOW + timedelta(days=30),
        metadata={"fixture": "synthetic"},
    )


def make_claim(
    *,
    claim_id: str = "claim-1",
    statement: str = "Synthetic runtime claim.",
) -> ClaimCandidate:
    return ClaimCandidate(
        claim_id=claim_id,
        draft_id="draft-v1",
        statement=statement,
        claim_category="synthetic-version-compatibility",
        scope_dimensions=(make_scope(),),
    )


def make_packet(
    *,
    evidence: EvidenceItem | None = None,
    prohibited_claims: tuple[str, ...] = ("Unsupported synthetic claim",),
) -> CorrectionPacket:
    evidence = evidence or make_evidence()
    claim = make_claim()
    requirement = CorrectionRequirement(
        requirement_id="requirement-1",
        claim_id=claim.claim_id,
        instruction="Use the cited synthetic source version.",
        evidence_references=(evidence.evidence_id,),
        mandatory=True,
    )
    return CorrectionPacket(
        schema_version=CONTRACT_SCHEMA_VERSION,
        kernel_run_id=RUN_A,
        draft_v1_id="draft-v1",
        selected_hat_id="synthetic-software-version",
        knowledge_route=KnowledgeRoute.HAT_ENFORCE,
        action_policy=ActionPolicy.ALLOW,
        evidence_status=EvidenceStatus.SUFFICIENT,
        scope_dimensions=(make_scope(),),
        knowledge_as_of=NOW,
        claims_under_review=(claim,),
        ordered_evidence_items=(evidence,),
        source_version_ids=(evidence.source_version_id,),
        validity_or_version_scopes=(make_scope(),),
        conflicts=(),
        required_corrections=(requirement,),
        prohibited_claims=prohibited_claims,
        uncertainty=0.1,
        citation_requirements=("Cite the source version.",),
        retrieval_policy_version="synthetic-policy-1",
        embedding_model_version=None,
    )


def make_pool(
    *,
    maximum_total_spaces: int | None = 4,
    maximum_active_spaces: int | None = 2,
) -> PersonalMemoryPool:
    return PersonalMemoryPool(
        tenant_id=TENANT_A,
        user_id=USER_A,
        quota_policy=PersonalHatQuotaPolicy(
            maximum_total_spaces=maximum_total_spaces,
            maximum_active_spaces=maximum_active_spaces,
        ),
    )


def make_personal_proposal(
    *,
    state: PatchState = PatchState.DETECTED,
    content: object | None = None,
    evidence_references: tuple[str, ...] = ("evidence-1",),
    content_kind: MemoryContentKind = MemoryContentKind.FACTUAL,
) -> MemoryPatchProposal:
    if content is None:
        content = {"statement": "Synthetic personal correction."}
    proposal = MemoryPatchProposal(
        schema_version=CONTRACT_SCHEMA_VERSION,
        proposal_id="proposal-personal-1",
        tenant_id=TENANT_A,
        owner_user_id=USER_A,
        target_scope=MemoryTargetScope.USER_PERSONAL_HAT,
        target_hat_id=None,
        target_personal_memory_space_id=SPACE_A,
        origin=ProposalOrigin.CRITIC_PROMPT_LOOP,
        proposed_content=content,
        evidence_references=evidence_references,
        scope_dimensions=(make_scope(),),
        valid_from=NOW,
        valid_until=NOW + timedelta(days=30),
        requested_trust_class=MemoryTrustClass.PERSONAL_VERIFIED_PATCH,
        approval_requirement=ApprovalRequirement.OWNER,
        lifecycle_state=PatchState.DETECTED,
        content_kind=content_kind,
        created_at=NOW,
    )
    if state is PatchState.DETECTED:
        return proposal

    from aioa_memory_kernel.state_machines import transition_memory_patch

    preliminary_path = (
        PatchState.PROPOSED,
        PatchState.EVIDENCE_BOUND,
        PatchState.VALIDATED,
        PatchState.AWAITING_APPROVAL,
    )
    for target in preliminary_path:
        proposal, _ = transition_memory_patch(
            proposal,
            target_state=target,
            actor_type=ActorType.SYSTEM,
            actor_id="kernel-contract",
            transitioned_at=NOW,
        )
        if state is target:
            return proposal

    approval = make_approval(
        proposal,
        decision=(
            ApprovalDecision.REJECT
            if state is PatchState.REJECTED
            else ApprovalDecision.APPROVE
        ),
    )
    if state is PatchState.REJECTED:
        proposal, _ = transition_memory_patch(
            proposal,
            target_state=PatchState.REJECTED,
            actor_type=ActorType.USER,
            actor_id=USER_A,
            transitioned_at=LATER,
            approval=approval,
        )
        return proposal
    proposal, _ = transition_memory_patch(
        proposal,
        target_state=PatchState.APPROVED,
        actor_type=ActorType.USER,
        actor_id=USER_A,
        transitioned_at=LATER,
        approval=approval,
    )
    if state is PatchState.APPROVED:
        return proposal

    commit = make_commit(proposal, approval)
    proposal, _ = transition_memory_patch(
        proposal,
        target_state=PatchState.COMMITTED,
        actor_type=ActorType.COMMIT_SERVICE,
        actor_id=commit.actor_id,
        transitioned_at=LATER,
        approval=approval,
        commit=commit,
    )
    if state is PatchState.COMMITTED:
        return proposal
    proposal, _ = transition_memory_patch(
        proposal,
        target_state=PatchState.ACTIVE,
        actor_type=ActorType.COMMIT_SERVICE,
        actor_id=commit.actor_id,
        transitioned_at=LATER,
        approval=approval,
        commit=commit,
    )
    if state is PatchState.ACTIVE:
        return proposal
    if state in {PatchState.SUPERSEDED, PatchState.REVOKED}:
        proposal, _ = transition_memory_patch(
            proposal,
            target_state=state,
            actor_type=ActorType.HUMAN_REVIEWER,
            actor_id="reviewer-1",
            transitioned_at=LATER,
        )
        return proposal
    raise ValueError(f"unsupported synthetic patch state {state.value}")


def make_approval(
    proposal: MemoryPatchProposal,
    *,
    decision: ApprovalDecision = ApprovalDecision.APPROVE,
    approver_type: ActorType = ActorType.USER,
    approver_id: str = USER_A,
) -> MemoryPatchApproval:
    return MemoryPatchApproval(
        schema_version=CONTRACT_SCHEMA_VERSION,
        approval_id="approval-1",
        proposal_id=proposal.proposal_id,
        proposal_content_hash=proposal.content_hash,
        tenant_id=proposal.tenant_id,
        owner_user_id=proposal.owner_user_id,
        personal_memory_space_id=proposal.target_personal_memory_space_id,
        decision=decision,
        approver_type=approver_type,
        approver_id=approver_id,
        reason_code="SYNTHETIC_REVIEW_COMPLETE",
        decided_at=LATER,
    )


def make_commit(
    proposal: MemoryPatchProposal, approval: MemoryPatchApproval
) -> MemoryPatchCommit:
    return MemoryPatchCommit(
        schema_version=CONTRACT_SCHEMA_VERSION,
        commit_id="commit-1",
        proposal_id=proposal.proposal_id,
        proposal_content_hash=proposal.content_hash,
        approval_id=approval.approval_id,
        approval_proof=approval.approval_proof,
        committed_patch_id="committed-patch-1",
        tenant_id=proposal.tenant_id,
        owner_user_id=proposal.owner_user_id,
        personal_memory_space_id=proposal.target_personal_memory_space_id,
        actor_type=ActorType.COMMIT_SERVICE,
        actor_id="bounded-commit-service",
        storage_class=StorageClass.CRDB_TRANSACTIONAL,
        committed_at=LATER,
    )


def make_shared_promotion(
    *,
    proposal_id: str = "shared-promotion-1",
    originating_patch_id: str = "proposal-personal-1",
    state: SharedPromotionState = (
        SharedPromotionState.SHARED_PROMOTION_PROPOSED
    ),
) -> SharedPromotionProposal:
    return SharedPromotionProposal(
        schema_version=CONTRACT_SCHEMA_VERSION,
        shared_promotion_proposal_id=proposal_id,
        originating_personal_patch_id=originating_patch_id,
        originating_personal_patch_hash="b" * 64,
        originating_personal_memory_space_id=SPACE_A,
        tenant_id=TENANT_A,
        owner_user_id=USER_A,
        target_hat_id="synthetic-software-version",
        private_data_classification=PrivateDataClassification.NONE,
        deidentification_status=DeidentificationStatus.NOT_REQUIRED,
        independent_evidence_references=(),
        independent_evidence_validated=False,
        hat_scope_dimensions=(make_scope(),),
        valid_from=NOW,
        valid_until=NOW + timedelta(days=30),
        domain_approval_id=None,
        shared_commit_id=None,
        state=state,
        created_at=NOW,
        updated_at=NOW,
    )


def ownership_a() -> MemoryOwnership:
    return MemoryOwnership(
        tenant_id=TENANT_A,
        user_id=USER_A,
        personal_memory_space_id=SPACE_A,
    )
