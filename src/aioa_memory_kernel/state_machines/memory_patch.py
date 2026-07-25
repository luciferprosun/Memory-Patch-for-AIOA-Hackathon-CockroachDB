"""Authority-gated Memory Patch lifecycle state machine."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from types import MappingProxyType

from ..contracts.enums import (
    ActorType,
    ApprovalDecision,
    ApprovalRequirement,
    MemoryContentKind,
    MemoryTargetScope,
    PatchState,
)
from ..contracts.exceptions import (
    AuthorityViolation,
    ContractValidationError,
    InvalidTransition,
)
from ..contracts.patches import (
    APPROVAL_ACTOR_TYPES,
    COMMIT_ACTOR_TYPES,
    MemoryPatchApproval,
    MemoryPatchCommit,
    MemoryPatchProposal,
    verify_approval_binding,
    verify_commit_binding,
    verify_memory_patch_proposal_hash,
)
from ..contracts.serialization import (
    ensure_utc,
    require_enum_member,
    require_non_empty,
)


MEMORY_PATCH_TRANSITIONS = MappingProxyType({
    PatchState.DETECTED: frozenset({PatchState.PROPOSED}),
    PatchState.PROPOSED: frozenset({PatchState.EVIDENCE_BOUND}),
    PatchState.EVIDENCE_BOUND: frozenset({PatchState.VALIDATED}),
    PatchState.VALIDATED: frozenset({PatchState.AWAITING_APPROVAL}),
    PatchState.AWAITING_APPROVAL: frozenset(
        {PatchState.APPROVED, PatchState.REJECTED}
    ),
    PatchState.APPROVED: frozenset({PatchState.COMMITTED}),
    PatchState.COMMITTED: frozenset({PatchState.ACTIVE}),
    PatchState.ACTIVE: frozenset(
        {PatchState.SUPERSEDED, PatchState.REVOKED}
    ),
    PatchState.SUPERSEDED: frozenset(),
    PatchState.REJECTED: frozenset(),
    PatchState.REVOKED: frozenset(),
})

MEMORY_PATCH_TERMINAL_STATES = frozenset(
    {PatchState.SUPERSEDED, PatchState.REJECTED, PatchState.REVOKED}
)


@dataclass(frozen=True, slots=True)
class PatchTransitionRecord:
    """Appendable transition fact preserving proposal identity and history."""

    proposal_id: str
    proposal_content_hash: str
    state_before: PatchState
    state_after: PatchState
    actor_type: ActorType
    actor_id: str
    transitioned_at: datetime

    def __post_init__(self) -> None:
        require_non_empty(self.proposal_id, "proposal_id")
        require_non_empty(self.proposal_content_hash, "proposal_content_hash")
        require_non_empty(self.actor_id, "actor_id")
        object.__setattr__(
            self,
            "transitioned_at",
            ensure_utc(self.transitioned_at, "transitioned_at"),
        )


def memory_patch_transition_allowed(
    current: PatchState, target: PatchState
) -> bool:
    """Return whether the exact successful/side-path graph contains an edge."""

    require_enum_member(current, PatchState, "current")
    require_enum_member(target, PatchState, "target")
    return target in MEMORY_PATCH_TRANSITIONS[current]


def _validate_approval_for_target(
    proposal: MemoryPatchProposal,
    approval: MemoryPatchApproval,
) -> None:
    verify_approval_binding(proposal, approval)
    if (
        proposal.approval_requirement is ApprovalRequirement.OWNER
        and (
            approval.approver_type is not ActorType.USER
            or approval.approver_id != proposal.owner_user_id
        )
    ):
        raise AuthorityViolation("owner approval must come from the exact owner")
    if (
        proposal.approval_requirement is ApprovalRequirement.DOMAIN_REVIEWER
        and approval.approver_type is not ActorType.HUMAN_REVIEWER
    ):
        raise AuthorityViolation(
            "shared Knowledge HAT patch requires domain reviewer approval"
        )
    if (
        proposal.approval_requirement
        is ApprovalRequirement.SYSTEM_MIGRATION_REVIEW
        and approval.approver_type is not ActorType.HUMAN_REVIEWER
    ):
        raise AuthorityViolation(
            "system migration patch requires independent human review"
        )
    if (
        proposal.content_kind is MemoryContentKind.PREFERENCE
        and proposal.target_scope is MemoryTargetScope.USER_PERSONAL_HAT
        and (
            approval.approver_type is not ActorType.USER
            or approval.approver_id != proposal.owner_user_id
        )
    ):
        raise AuthorityViolation(
            "a personal preference still requires exact owner approval"
        )


def transition_memory_patch(
    proposal: MemoryPatchProposal,
    *,
    target_state: PatchState,
    actor_type: ActorType,
    actor_id: str,
    transitioned_at: datetime,
    approval: MemoryPatchApproval | None = None,
    commit: MemoryPatchCommit | None = None,
) -> tuple[MemoryPatchProposal, PatchTransitionRecord]:
    """Apply one graph edge after evidence, approval, and authority invariants."""

    require_enum_member(target_state, PatchState, "target_state")
    require_enum_member(actor_type, ActorType, "actor_type")
    verify_memory_patch_proposal_hash(proposal)
    if not memory_patch_transition_allowed(
        proposal.lifecycle_state, target_state
    ):
        raise InvalidTransition(
            f"Memory Patch transition {proposal.lifecycle_state.value} -> "
            f"{target_state.value} is forbidden"
        )
    require_non_empty(actor_id, "actor_id")
    transitioned_at = ensure_utc(transitioned_at, "transitioned_at")
    if transitioned_at < proposal.created_at:
        raise ContractValidationError(
            "Memory Patch transition cannot precede proposal creation"
        )
    if (
        target_state is PatchState.EVIDENCE_BOUND
        and proposal.content_kind is MemoryContentKind.FACTUAL
        and not proposal.evidence_references
    ):
        raise ContractValidationError(
            "a factual patch cannot bind an empty evidence set"
        )
    if (
        target_state is PatchState.VALIDATED
        and proposal.content_kind is MemoryContentKind.FACTUAL
        and not proposal.evidence_references
    ):
        raise ContractValidationError(
            "a factual patch cannot reach VALIDATED without evidence"
        )
    if target_state in {PatchState.APPROVED, PatchState.REJECTED}:
        if approval is None:
            raise AuthorityViolation("approval transition requires an approval record")
        _validate_approval_for_target(proposal, approval)
        expected_decision = (
            ApprovalDecision.APPROVE
            if target_state is PatchState.APPROVED
            else ApprovalDecision.REJECT
        )
        if approval.decision is not expected_decision:
            raise ContractValidationError(
                "approval decision does not match target patch state"
            )
        if actor_type != approval.approver_type or actor_id != approval.approver_id:
            raise AuthorityViolation(
                "transition actor must be the bound approval actor"
            )
    elif actor_type in {
        ActorType.KNOWLEDGE_HAT,
        ActorType.KNOWLEDGE_KERNEL,
        ActorType.KNOWLEDGE_HUB,
        ActorType.CRITIC_PROMPT_LOOP,
        ActorType.MODEL,
        ActorType.MODEL_VERIFIER,
    } and target_state in {
        PatchState.APPROVED,
        PatchState.COMMITTED,
        PatchState.ACTIVE,
    }:
        raise AuthorityViolation(
            f"{actor_type.value} may propose but cannot approve, commit, or activate"
        )
    if target_state is PatchState.COMMITTED:
        if commit is None:
            raise AuthorityViolation("COMMITTED requires a technical commit receipt")
        if approval is None:
            raise AuthorityViolation(
                "COMMITTED requires the bound approval record"
            )
        if actor_type not in COMMIT_ACTOR_TYPES:
            raise AuthorityViolation("actor lacks technical commit authority")
        if actor_type != commit.actor_type or actor_id != commit.actor_id:
            raise AuthorityViolation("transition actor does not match commit receipt")
        _validate_approval_for_target(proposal, approval)
        verify_commit_binding(proposal, approval, commit)
    if target_state is PatchState.ACTIVE:
        if actor_type is not ActorType.COMMIT_SERVICE:
            raise AuthorityViolation(
                "only the bounded technical commit service may activate"
            )
        if approval is None or commit is None:
            raise AuthorityViolation(
                "activation requires the approval and commitment bindings"
            )
        _validate_approval_for_target(proposal, approval)
        if approval.decision is not ApprovalDecision.APPROVE:
            raise AuthorityViolation("rejected content cannot activate")
        verify_commit_binding(proposal, approval, commit)
    if target_state in {PatchState.SUPERSEDED, PatchState.REVOKED}:
        if actor_type not in {
            ActorType.USER,
            ActorType.HUMAN_REVIEWER,
            ActorType.SYSTEM,
            ActorType.COMMIT_SERVICE,
        }:
            raise AuthorityViolation(
                f"{actor_type.value} cannot revoke or supersede active memory"
            )
        if (
            actor_type is ActorType.USER
            and (
                proposal.owner_user_id is None
                or actor_id != proposal.owner_user_id
            )
        ):
            raise AuthorityViolation(
                "only the exact owner may revoke or supersede a personal patch"
            )
    updated = replace(proposal, lifecycle_state=target_state)
    if updated.content_hash != proposal.content_hash:
        raise ContractValidationError(
            "lifecycle transition changed immutable proposal identity"
        )
    transition = PatchTransitionRecord(
        proposal_id=proposal.proposal_id,
        proposal_content_hash=proposal.content_hash,
        state_before=proposal.lifecycle_state,
        state_after=target_state,
        actor_type=actor_type,
        actor_id=actor_id,
        transitioned_at=transitioned_at,
    )
    return updated, transition


def memory_patch_is_retrieval_eligible(proposal: MemoryPatchProposal) -> bool:
    """Only ACTIVE, non-revoked/superseded patches are eligible."""

    return proposal.lifecycle_state is PatchState.ACTIVE
