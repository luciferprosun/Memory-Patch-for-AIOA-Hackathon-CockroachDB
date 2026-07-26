"""Separate personal-to-shared promotion review state machine."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType

from ..contracts.enums import ActorType, SharedPromotionState
from ..contracts.exceptions import AuthorityViolation, ContractValidationError, InvalidTransition
from ..contracts.patches import (
    SharedPromotionProposal,
    _replace_shared_promotion_lifecycle,
    verify_shared_promotion_hash,
)
from ..contracts.serialization import (
    ensure_utc,
    require_enum_member,
    require_non_empty,
    require_sha256_hex,
)


SHARED_PROMOTION_TRANSITIONS = MappingProxyType({
    SharedPromotionState.SHARED_PROMOTION_PROPOSED: frozenset(
        {SharedPromotionState.EVIDENCE_REVALIDATED, SharedPromotionState.REJECTED}
    ),
    SharedPromotionState.EVIDENCE_REVALIDATED: frozenset(
        {
            SharedPromotionState.DOMAIN_REVIEW_REQUIRED,
            SharedPromotionState.REJECTED,
        }
    ),
    SharedPromotionState.DOMAIN_REVIEW_REQUIRED: frozenset(
        {SharedPromotionState.APPROVED_FOR_SHARED, SharedPromotionState.REJECTED}
    ),
    SharedPromotionState.APPROVED_FOR_SHARED: frozenset(
        {SharedPromotionState.SHARED_PATCH_COMMITTED}
    ),
    SharedPromotionState.SHARED_PATCH_COMMITTED: frozenset(),
    SharedPromotionState.REJECTED: frozenset(),
})

SHARED_PROMOTION_TERMINAL_STATES = frozenset(
    {
        SharedPromotionState.SHARED_PATCH_COMMITTED,
        SharedPromotionState.REJECTED,
    }
)


@dataclass(frozen=True, slots=True)
class SharedPromotionTransitionRecord:
    """Appendable promotion history independent of the personal patch."""

    shared_promotion_proposal_id: str
    proposal_hash_before: str
    proposal_hash_after: str
    state_before: SharedPromotionState
    state_after: SharedPromotionState
    actor_type: ActorType
    actor_id: str
    transitioned_at: datetime

    def __post_init__(self) -> None:
        require_non_empty(
            self.shared_promotion_proposal_id,
            "shared_promotion_proposal_id",
        )
        require_sha256_hex(self.proposal_hash_before, "proposal_hash_before")
        require_sha256_hex(self.proposal_hash_after, "proposal_hash_after")
        require_enum_member(
            self.state_before,
            SharedPromotionState,
            "state_before",
        )
        require_enum_member(
            self.state_after,
            SharedPromotionState,
            "state_after",
        )
        require_enum_member(self.actor_type, ActorType, "actor_type")
        if not shared_promotion_transition_allowed(
            self.state_before,
            self.state_after,
        ):
            raise InvalidTransition(
                "SharedPromotionTransitionRecord contains a forbidden edge"
            )
        require_non_empty(self.actor_id, "actor_id")
        object.__setattr__(
            self,
            "transitioned_at",
            ensure_utc(self.transitioned_at, "transitioned_at"),
        )


def shared_promotion_transition_allowed(
    current: SharedPromotionState, target: SharedPromotionState
) -> bool:
    require_enum_member(current, SharedPromotionState, "current")
    require_enum_member(target, SharedPromotionState, "target")
    return target in SHARED_PROMOTION_TRANSITIONS[current]


def transition_shared_promotion(
    proposal: SharedPromotionProposal,
    *,
    target_state: SharedPromotionState,
    actor_type: ActorType,
    actor_id: str,
    transitioned_at: datetime,
    independent_evidence_references: tuple[str, ...] | None = None,
    independent_evidence_validated: bool | None = None,
    domain_approval_id: str | None = None,
    shared_commit_id: str | None = None,
) -> tuple[SharedPromotionProposal, SharedPromotionTransitionRecord]:
    """Apply a review edge without mutating or replacing the personal patch."""

    require_enum_member(target_state, SharedPromotionState, "target_state")
    require_enum_member(actor_type, ActorType, "actor_type")
    verify_shared_promotion_hash(proposal)
    if not shared_promotion_transition_allowed(proposal.state, target_state):
        raise InvalidTransition(
            f"shared promotion transition {proposal.state.value} -> "
            f"{target_state.value} is forbidden"
        )
    require_non_empty(actor_id, "actor_id")
    transitioned_at = ensure_utc(transitioned_at, "transitioned_at")
    if transitioned_at < proposal.updated_at:
        raise ContractValidationError(
            "shared promotion transition cannot move time backwards"
        )
    if target_state is SharedPromotionState.EVIDENCE_REVALIDATED:
        if actor_type not in {ActorType.HUMAN_REVIEWER, ActorType.SYSTEM}:
            raise AuthorityViolation(
                "independent evidence revalidation requires governed review"
            )
        if not independent_evidence_validated or not independent_evidence_references:
            raise ContractValidationError(
                "promotion cannot skip independent evidence revalidation"
            )
    if target_state is SharedPromotionState.DOMAIN_REVIEW_REQUIRED:
        if actor_type not in {ActorType.HUMAN_REVIEWER, ActorType.SYSTEM}:
            raise AuthorityViolation("domain review cannot be requested by a model")
    if target_state is SharedPromotionState.APPROVED_FOR_SHARED:
        if actor_type is not ActorType.HUMAN_REVIEWER:
            raise AuthorityViolation(
                "personal user approval alone is insufficient for sharing"
            )
        require_non_empty(domain_approval_id or "", "domain_approval_id")
        raise AuthorityViolation(
            "a domain approval identifier is only a future reference; "
            "a hash-bound shared approval contract is not implemented"
        )
    if target_state is SharedPromotionState.SHARED_PATCH_COMMITTED:
        if actor_type is not ActorType.COMMIT_SERVICE:
            raise AuthorityViolation(
                "shared commitment requires the technical commit service"
            )
        require_non_empty(shared_commit_id or "", "shared_commit_id")
        raise AuthorityViolation(
            "a shared commit identifier is only a future reference; "
            "a hash-bound shared commitment contract is not implemented"
        )
    if target_state is SharedPromotionState.REJECTED and actor_type not in {
        ActorType.USER,
        ActorType.HUMAN_REVIEWER,
        ActorType.SYSTEM,
    }:
        raise AuthorityViolation("actor cannot reject a shared promotion")
    if (
        target_state is SharedPromotionState.REJECTED
        and actor_type is ActorType.USER
        and actor_id != proposal.owner_user_id
    ):
        raise AuthorityViolation(
            "only the originating owner may reject a shared promotion"
        )
    updates: dict[str, object] = {
        "state": target_state,
        "updated_at": transitioned_at,
    }
    if independent_evidence_references is not None:
        updates["independent_evidence_references"] = (
            independent_evidence_references
        )
    if independent_evidence_validated is not None:
        updates["independent_evidence_validated"] = (
            independent_evidence_validated
        )
    if domain_approval_id is not None:
        updates["domain_approval_id"] = domain_approval_id
    if shared_commit_id is not None:
        updates["shared_commit_id"] = shared_commit_id
    updated = _replace_shared_promotion_lifecycle(proposal, **updates)
    transition = SharedPromotionTransitionRecord(
        shared_promotion_proposal_id=proposal.shared_promotion_proposal_id,
        proposal_hash_before=proposal.proposal_hash,
        proposal_hash_after=updated.proposal_hash,
        state_before=proposal.state,
        state_after=target_state,
        actor_type=actor_type,
        actor_id=actor_id,
        transitioned_at=transitioned_at,
    )
    return updated, transition


def shared_promotion_is_committed(proposal: SharedPromotionProposal) -> bool:
    return proposal.state is SharedPromotionState.SHARED_PATCH_COMMITTED
