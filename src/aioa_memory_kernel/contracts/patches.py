"""Memory Patch, approval, commit, and personal-to-shared contracts."""

from __future__ import annotations

from dataclasses import InitVar, dataclass, field, replace
from datetime import datetime
from typing import Any

from .enums import (
    ActorType,
    ApprovalDecision,
    ApprovalRequirement,
    DeidentificationStatus,
    MemoryContentKind,
    MemoryTargetScope,
    MemoryTrustClass,
    PatchState,
    PrivateDataClassification,
    ProposalOrigin,
    SharedPromotionState,
    StorageClass,
)
from .exceptions import AuthorityViolation, ContractValidationError
from .identities import MemoryOwnership
from .personal_memory import validate_preference_content
from .scope import ScopeDimension, scope_interval_is_valid
from .serialization import (
    approval_proof_hash,
    canonical_sha256,
    ensure_utc,
    freeze_string_tuple,
    freeze_typed_tuple,
    freeze_json,
    require_enum_member,
    require_non_empty,
    require_schema_version,
    require_sha256_hex,
    verify_canonical_hash,
)


APPROVAL_ACTOR_TYPES = frozenset(
    {ActorType.USER, ActorType.HUMAN_REVIEWER}
)
COMMIT_ACTOR_TYPES = frozenset(
    {ActorType.COMMIT_SERVICE, ActorType.MIGRATION_SERVICE}
)
NON_AUTHORITY_PROPOSERS = frozenset(
    {
        ActorType.KNOWLEDGE_KERNEL,
        ActorType.KNOWLEDGE_HAT,
        ActorType.KNOWLEDGE_HUB,
        ActorType.CRITIC_PROMPT_LOOP,
        ActorType.MODEL,
        ActorType.MODEL_VERIFIER,
    }
)
_MEMORY_PATCH_LIFECYCLE_PERMIT = object()
_SHARED_PROMOTION_LIFECYCLE_PERMIT = object()


@dataclass(frozen=True, slots=True)
class MemoryPatchProposal:
    """Evidence-bound proposal whose origin never grants approval authority."""

    schema_version: str
    proposal_id: str
    tenant_id: str
    owner_user_id: str | None
    target_scope: MemoryTargetScope
    target_hat_id: str | None
    target_personal_memory_space_id: str | None
    origin: ProposalOrigin
    proposed_content: Any
    evidence_references: tuple[str, ...]
    scope_dimensions: tuple[ScopeDimension, ...]
    valid_from: datetime | None
    valid_until: datetime | None
    requested_trust_class: MemoryTrustClass
    approval_requirement: ApprovalRequirement
    lifecycle_state: PatchState
    content_kind: MemoryContentKind
    created_at: datetime
    _lifecycle_permit: InitVar[object | None] = None
    content_hash: str = field(init=False)

    def __post_init__(self, _lifecycle_permit: object | None) -> None:
        require_schema_version(self.schema_version)
        require_non_empty(self.proposal_id, "proposal_id")
        require_non_empty(self.tenant_id, "tenant_id")
        require_enum_member(
            self.target_scope, MemoryTargetScope, "target_scope"
        )
        require_enum_member(self.origin, ProposalOrigin, "origin")
        require_enum_member(
            self.requested_trust_class,
            MemoryTrustClass,
            "requested_trust_class",
        )
        require_enum_member(
            self.approval_requirement,
            ApprovalRequirement,
            "approval_requirement",
        )
        require_enum_member(
            self.lifecycle_state, PatchState, "lifecycle_state"
        )
        require_enum_member(
            self.content_kind, MemoryContentKind, "content_kind"
        )
        if (
            self.lifecycle_state
            not in {PatchState.DETECTED, PatchState.PROPOSED}
            and _lifecycle_permit is not _MEMORY_PATCH_LIFECYCLE_PERMIT
        ):
            raise ContractValidationError(
                "privileged Memory Patch states require the validated "
                "state-machine transition API"
            )
        object.__setattr__(
            self, "proposed_content", freeze_json(self.proposed_content)
        )
        object.__setattr__(
            self,
            "evidence_references",
            freeze_string_tuple(
                self.evidence_references,
                "evidence_references",
                unique=True,
            ),
        )
        object.__setattr__(
            self,
            "scope_dimensions",
            freeze_typed_tuple(
                self.scope_dimensions,
                ScopeDimension,
                "scope_dimensions",
            ),
        )
        object.__setattr__(
            self, "created_at", ensure_utc(self.created_at, "created_at")
        )
        for field_name in ("valid_from", "valid_until"):
            timestamp = getattr(self, field_name)
            if timestamp is not None:
                object.__setattr__(
                    self, field_name, ensure_utc(timestamp, field_name)
                )
        if not scope_interval_is_valid(self.valid_from, self.valid_until):
            raise ContractValidationError("patch validity interval is inverted")
        scope_names = [dimension.name for dimension in self.scope_dimensions]
        if len(scope_names) != len(set(scope_names)):
            raise ContractValidationError(
                "patch scope dimension names must be unique"
            )
        if self.requested_trust_class is MemoryTrustClass.CANONICAL_SOURCE_EVIDENCE:
            raise ContractValidationError(
                "canonical evidence is not a valid Memory Patch target"
            )
        if self.target_scope is MemoryTargetScope.USER_PERSONAL_HAT:
            require_non_empty(self.owner_user_id or "", "owner_user_id")
            require_non_empty(
                self.target_personal_memory_space_id or "",
                "target_personal_memory_space_id",
            )
            if self.target_hat_id is not None:
                raise ContractValidationError(
                    "personal patch cannot also target a shared Knowledge HAT"
                )
            if (
                self.requested_trust_class
                is MemoryTrustClass.SHARED_HAT_VERIFIED_MEMORY
            ):
                raise ContractValidationError(
                    "a personal patch cannot request shared-HAT trust"
                )
            if self.approval_requirement is not ApprovalRequirement.OWNER:
                raise ContractValidationError(
                    "a Personal Memory HAT patch requires exact owner approval"
                )
        elif self.target_scope is MemoryTargetScope.SHARED_KNOWLEDGE_HAT:
            require_non_empty(self.target_hat_id or "", "target_hat_id")
            if self.owner_user_id is not None:
                raise ContractValidationError(
                    "shared Knowledge HAT patch cannot carry a private owner"
                )
            if self.target_personal_memory_space_id is not None:
                raise ContractValidationError(
                    "shared patch cannot target a Personal Memory HAT"
                )
            if (
                self.requested_trust_class
                is not MemoryTrustClass.SHARED_HAT_VERIFIED_MEMORY
            ):
                raise ContractValidationError(
                    "shared patch must request shared-HAT verified trust"
                )
            if (
                self.approval_requirement
                is not ApprovalRequirement.DOMAIN_REVIEWER
            ):
                raise ContractValidationError(
                    "shared Knowledge HAT patch requires domain reviewer approval"
                )
        elif self.target_scope is MemoryTargetScope.SESSION:
            require_non_empty(self.owner_user_id or "", "owner_user_id")
            require_non_empty(
                self.target_personal_memory_space_id or "",
                "target_personal_memory_space_id",
            )
            if self.target_hat_id is not None:
                raise ContractValidationError(
                    "session patch cannot target a shared Knowledge HAT"
                )
            if (
                self.requested_trust_class
                is not MemoryTrustClass.SESSION_MEMORY
            ):
                raise ContractValidationError(
                    "session patch must request SESSION_MEMORY trust"
                )
            if self.approval_requirement is not ApprovalRequirement.OWNER:
                raise ContractValidationError(
                    "session patch requires exact owner approval"
                )
        if (
            self.content_kind is MemoryContentKind.MODEL_EXPERIENCE
            and self.requested_trust_class
            is not MemoryTrustClass.MODEL_EXPERIENCE_HINT
        ):
            raise ContractValidationError(
                "model experience can only request advisory hint trust"
            )
        if self.content_kind is MemoryContentKind.PREFERENCE:
            validate_preference_content(self.proposed_content)
        if (
            self.content_kind is MemoryContentKind.FACTUAL
            and self.lifecycle_state
            not in {PatchState.DETECTED, PatchState.PROPOSED}
            and not self.evidence_references
        ):
            raise ContractValidationError(
                "an evidence-bound factual patch requires evidence references"
            )
        object.__setattr__(
            self, "content_hash", compute_memory_patch_proposal_hash(self)
        )

    @property
    def ownership(self) -> MemoryOwnership | None:
        if (
            self.owner_user_id is None
            or self.target_personal_memory_space_id is None
        ):
            return None
        return MemoryOwnership(
            tenant_id=self.tenant_id,
            user_id=self.owner_user_id,
            personal_memory_space_id=self.target_personal_memory_space_id,
        )


@dataclass(frozen=True, slots=True)
class MemoryPatchApproval:
    """Claimed human/owner decision digest-bound to exact proposal scope.

    Construction validates the record shape and binding; it does not
    authenticate the claimed actor.
    """

    schema_version: str
    approval_id: str
    proposal_id: str
    proposal_content_hash: str
    tenant_id: str
    owner_user_id: str | None
    personal_memory_space_id: str | None
    decision: ApprovalDecision
    approver_type: ActorType
    approver_id: str
    reason_code: str
    decided_at: datetime
    approval_proof: str = field(init=False)

    def __post_init__(self) -> None:
        require_schema_version(self.schema_version)
        for field_name in (
            "approval_id",
            "proposal_id",
            "tenant_id",
            "approver_id",
            "reason_code",
        ):
            require_non_empty(getattr(self, field_name), field_name)
        require_sha256_hex(
            self.proposal_content_hash, "proposal_content_hash"
        )
        require_enum_member(self.decision, ApprovalDecision, "decision")
        require_enum_member(self.approver_type, ActorType, "approver_type")
        if self.approver_type not in APPROVAL_ACTOR_TYPES:
            raise AuthorityViolation(
                f"{self.approver_type.value} cannot approve a Memory Patch"
            )
        if (self.owner_user_id is None) != (
            self.personal_memory_space_id is None
        ):
            raise ContractValidationError(
                "approval owner and Personal Memory HAT scope must be "
                "both present or both absent"
            )
        if self.personal_memory_space_id is not None:
            require_non_empty(
                self.personal_memory_space_id, "personal_memory_space_id"
            )
        if self.owner_user_id is not None:
            require_non_empty(self.owner_user_id, "owner_user_id")
        object.__setattr__(
            self, "decided_at", ensure_utc(self.decided_at, "decided_at")
        )
        object.__setattr__(
            self,
            "approval_proof",
            approval_proof_hash(
                approval_id=self.approval_id,
                proposal_id=self.proposal_id,
                proposal_hash=self.proposal_content_hash,
                tenant_id=self.tenant_id,
                owner_user_id=self.owner_user_id,
                personal_memory_space_id=self.personal_memory_space_id,
                decision=self.decision.value,
                approver_type=self.approver_type.value,
                approver_id=self.approver_id,
                reason_code=self.reason_code,
                decided_at=self.decided_at,
            ),
        )


@dataclass(frozen=True, slots=True)
class MemoryPatchCommit:
    """Claimed technical receipt, structurally separate from human approval."""

    schema_version: str
    commit_id: str
    proposal_id: str
    proposal_content_hash: str
    approval_id: str
    approval_proof: str
    committed_patch_id: str
    tenant_id: str
    owner_user_id: str | None
    personal_memory_space_id: str | None
    actor_type: ActorType
    actor_id: str
    storage_class: StorageClass
    committed_at: datetime
    commit_hash: str = field(init=False)

    def __post_init__(self) -> None:
        require_schema_version(self.schema_version)
        for field_name in (
            "commit_id",
            "proposal_id",
            "approval_id",
            "committed_patch_id",
            "tenant_id",
            "actor_id",
        ):
            require_non_empty(getattr(self, field_name), field_name)
        require_sha256_hex(
            self.proposal_content_hash, "proposal_content_hash"
        )
        require_sha256_hex(self.approval_proof, "approval_proof")
        require_enum_member(self.actor_type, ActorType, "actor_type")
        require_enum_member(
            self.storage_class, StorageClass, "storage_class"
        )
        if self.actor_type not in COMMIT_ACTOR_TYPES:
            raise AuthorityViolation(
                f"{self.actor_type.value} has no technical commit authority"
            )
        if (self.owner_user_id is None) != (
            self.personal_memory_space_id is None
        ):
            raise ContractValidationError(
                "commit owner and Personal Memory HAT scope must be "
                "both present or both absent"
            )
        if self.personal_memory_space_id is not None:
            require_non_empty(
                self.personal_memory_space_id, "personal_memory_space_id"
            )
        if self.owner_user_id is not None:
            require_non_empty(self.owner_user_id, "owner_user_id")
        if self.storage_class is not StorageClass.CRDB_TRANSACTIONAL:
            raise ContractValidationError(
                "active patch commitment is future transactional state, "
                "not a snapshot, cache, or session object"
            )
        object.__setattr__(
            self, "committed_at", ensure_utc(self.committed_at, "committed_at")
        )
        object.__setattr__(
            self,
            "commit_hash",
            canonical_sha256(self, exclude_fields=("commit_hash",)),
        )


@dataclass(frozen=True, slots=True)
class SharedPromotionProposal:
    """Separate review object; the originating personal patch is unchanged."""

    schema_version: str
    shared_promotion_proposal_id: str
    originating_personal_patch_id: str
    originating_personal_patch_hash: str
    originating_personal_memory_space_id: str
    tenant_id: str
    owner_user_id: str
    target_hat_id: str
    private_data_classification: PrivateDataClassification
    deidentification_status: DeidentificationStatus
    independent_evidence_references: tuple[str, ...]
    independent_evidence_validated: bool
    hat_scope_dimensions: tuple[ScopeDimension, ...]
    valid_from: datetime | None
    valid_until: datetime | None
    domain_approval_id: str | None
    shared_commit_id: str | None
    state: SharedPromotionState
    created_at: datetime
    updated_at: datetime
    _lifecycle_permit: InitVar[object | None] = None
    proposal_hash: str = field(init=False)

    def __post_init__(self, _lifecycle_permit: object | None) -> None:
        require_schema_version(self.schema_version)
        for field_name in (
            "shared_promotion_proposal_id",
            "originating_personal_patch_id",
            "originating_personal_memory_space_id",
            "tenant_id",
            "owner_user_id",
            "target_hat_id",
        ):
            require_non_empty(getattr(self, field_name), field_name)
        require_sha256_hex(
            self.originating_personal_patch_hash,
            "originating_personal_patch_hash",
        )
        require_enum_member(
            self.private_data_classification,
            PrivateDataClassification,
            "private_data_classification",
        )
        require_enum_member(
            self.deidentification_status,
            DeidentificationStatus,
            "deidentification_status",
        )
        require_enum_member(self.state, SharedPromotionState, "state")
        if (
            self.state is not SharedPromotionState.SHARED_PROMOTION_PROPOSED
            and _lifecycle_permit is not _SHARED_PROMOTION_LIFECYCLE_PERMIT
        ):
            raise ContractValidationError(
                "reviewed or committed shared-promotion states require the "
                "validated state-machine transition API"
            )
        if not isinstance(self.independent_evidence_validated, bool):
            raise ContractValidationError(
                "independent_evidence_validated must be a boolean"
            )
        object.__setattr__(
            self,
            "independent_evidence_references",
            freeze_string_tuple(
                self.independent_evidence_references,
                "independent_evidence_references",
                unique=True,
            ),
        )
        object.__setattr__(
            self,
            "hat_scope_dimensions",
            freeze_typed_tuple(
                self.hat_scope_dimensions,
                ScopeDimension,
                "hat_scope_dimensions",
            ),
        )
        if (
            self.shared_promotion_proposal_id
            == self.originating_personal_patch_id
        ):
            raise ContractValidationError(
                "shared promotion must receive a new proposal ID"
            )
        object.__setattr__(
            self, "created_at", ensure_utc(self.created_at, "created_at")
        )
        object.__setattr__(
            self, "updated_at", ensure_utc(self.updated_at, "updated_at")
        )
        if self.updated_at < self.created_at:
            raise ContractValidationError("updated_at cannot precede created_at")
        for field_name in ("valid_from", "valid_until"):
            timestamp = getattr(self, field_name)
            if timestamp is not None:
                object.__setattr__(
                    self, field_name, ensure_utc(timestamp, field_name)
                )
        if not scope_interval_is_valid(self.valid_from, self.valid_until):
            raise ContractValidationError("promotion validity interval is inverted")
        if (
            self.domain_approval_id is not None
            and self.state
            not in {
                SharedPromotionState.APPROVED_FOR_SHARED,
                SharedPromotionState.SHARED_PATCH_COMMITTED,
            }
        ):
            raise ContractValidationError(
                "advisory shared-promotion states cannot carry domain approval"
            )
        if (
            self.shared_commit_id is not None
            and self.state
            is not SharedPromotionState.SHARED_PATCH_COMMITTED
        ):
            raise ContractValidationError(
                "uncommitted shared-promotion states cannot carry a commit"
            )
        if (
            self.state
            in {
                SharedPromotionState.EVIDENCE_REVALIDATED,
                SharedPromotionState.DOMAIN_REVIEW_REQUIRED,
                SharedPromotionState.APPROVED_FOR_SHARED,
                SharedPromotionState.SHARED_PATCH_COMMITTED,
            }
            and (
                not self.independent_evidence_validated
                or not self.independent_evidence_references
            )
        ):
            raise ContractValidationError(
                "shared promotion requires independent evidence revalidation"
            )
        if (
            self.state
            in {
                SharedPromotionState.APPROVED_FOR_SHARED,
                SharedPromotionState.SHARED_PATCH_COMMITTED,
            }
            and not self.domain_approval_id
        ):
            raise ContractValidationError(
                "user approval alone is insufficient for shared activation"
            )
        if (
            self.private_data_classification
            is not PrivateDataClassification.NONE
            and self.deidentification_status is not DeidentificationStatus.COMPLETE
            and self.state
            in {
                SharedPromotionState.APPROVED_FOR_SHARED,
                SharedPromotionState.SHARED_PATCH_COMMITTED,
            }
        ):
            raise ContractValidationError(
                "private personal data must be de-identified before shared approval"
            )
        if (
            self.state is SharedPromotionState.SHARED_PATCH_COMMITTED
            and not self.shared_commit_id
        ):
            raise ContractValidationError(
                "shared promotion commitment requires a separate commit ID"
            )
        if self.shared_commit_id is not None:
            require_non_empty(self.shared_commit_id, "shared_commit_id")
        if self.domain_approval_id is not None:
            require_non_empty(self.domain_approval_id, "domain_approval_id")
        object.__setattr__(
            self,
            "proposal_hash",
            compute_shared_promotion_hash(self),
        )


def compute_memory_patch_proposal_hash(proposal: MemoryPatchProposal) -> str:
    """Hash immutable proposal content, not its lifecycle cursor or own hash."""

    return canonical_sha256(
        proposal, exclude_fields=("content_hash", "lifecycle_state")
    )


def verify_memory_patch_proposal_hash(proposal: MemoryPatchProposal) -> None:
    """Verify proposal content identity across state transitions."""

    verify_canonical_hash(
        proposal,
        proposal.content_hash,
        exclude_fields=("content_hash", "lifecycle_state"),
    )


def verify_approval_binding(
    proposal: MemoryPatchProposal, approval: MemoryPatchApproval
) -> None:
    """Validate proposal identity, ownership, and proof binding."""

    if not isinstance(proposal, MemoryPatchProposal):
        raise ContractValidationError(
            "proposal must be a MemoryPatchProposal"
        )
    if not isinstance(approval, MemoryPatchApproval):
        raise ContractValidationError(
            "approval must be a MemoryPatchApproval"
        )
    verify_memory_patch_proposal_hash(proposal)
    if approval.proposal_id != proposal.proposal_id:
        raise ContractValidationError("approval references another proposal")
    if approval.proposal_content_hash != proposal.content_hash:
        raise ContractValidationError("approval is bound to different content")
    if approval.tenant_id != proposal.tenant_id:
        raise ContractValidationError("approval tenant mismatch")
    if (
        proposal.target_scope
        in {
            MemoryTargetScope.USER_PERSONAL_HAT,
            MemoryTargetScope.SESSION,
        }
        and (
            approval.owner_user_id != proposal.owner_user_id
            or approval.personal_memory_space_id
            != proposal.target_personal_memory_space_id
        )
    ):
        raise ContractValidationError("personal patch approval owner mismatch")
    if (
        proposal.target_scope is MemoryTargetScope.SHARED_KNOWLEDGE_HAT
        and (
            approval.owner_user_id is not None
            or approval.personal_memory_space_id is not None
        )
    ):
        raise ContractValidationError(
            "shared patch approval cannot carry private ownership"
        )
    if approval.decided_at < proposal.created_at:
        raise ContractValidationError("approval cannot precede proposal creation")
    expected = approval_proof_hash(
        approval_id=approval.approval_id,
        proposal_id=approval.proposal_id,
        proposal_hash=approval.proposal_content_hash,
        tenant_id=approval.tenant_id,
        owner_user_id=approval.owner_user_id,
        personal_memory_space_id=approval.personal_memory_space_id,
        decision=approval.decision.value,
        approver_type=approval.approver_type.value,
        approver_id=approval.approver_id,
        reason_code=approval.reason_code,
        decided_at=approval.decided_at,
    )
    if expected != approval.approval_proof:
        raise ContractValidationError("approval proof does not verify")


def verify_commit_binding(
    proposal: MemoryPatchProposal,
    approval: MemoryPatchApproval,
    commit: MemoryPatchCommit,
) -> None:
    """Validate technical commit receipt binding to content and approval."""

    if not isinstance(commit, MemoryPatchCommit):
        raise ContractValidationError("commit must be a MemoryPatchCommit")
    verify_approval_binding(proposal, approval)
    if approval.decision is not ApprovalDecision.APPROVE:
        raise ContractValidationError("a rejection cannot authorize commitment")
    if commit.proposal_id != proposal.proposal_id:
        raise ContractValidationError("commit references another proposal")
    if commit.proposal_content_hash != proposal.content_hash:
        raise ContractValidationError("commit is bound to different content")
    if commit.tenant_id != proposal.tenant_id:
        raise ContractValidationError("commit tenant mismatch")
    if commit.owner_user_id != proposal.owner_user_id:
        raise ContractValidationError("commit owner mismatch")
    if (
        commit.personal_memory_space_id
        != proposal.target_personal_memory_space_id
    ):
        raise ContractValidationError("commit personal memory space mismatch")
    if commit.committed_at < proposal.created_at:
        raise ContractValidationError("commit cannot precede proposal creation")
    if commit.approval_id != approval.approval_id:
        raise ContractValidationError("commit references another approval")
    if commit.approval_proof != approval.approval_proof:
        raise ContractValidationError("commit approval proof mismatch")
    if commit.committed_at < approval.decided_at:
        raise ContractValidationError(
            "technical commitment cannot precede bound approval"
        )
    verify_canonical_hash(
        commit, commit.commit_hash, exclude_fields=("commit_hash",)
    )


def compute_shared_promotion_hash(proposal: SharedPromotionProposal) -> str:
    """Hash the complete current promotion review record."""

    return canonical_sha256(proposal, exclude_fields=("proposal_hash",))


def verify_shared_promotion_hash(proposal: SharedPromotionProposal) -> None:
    """Verify a promotion record without modifying the personal source patch."""

    verify_canonical_hash(
        proposal,
        proposal.proposal_hash,
        exclude_fields=("proposal_hash",),
    )


def _replace_memory_patch_lifecycle(
    proposal: MemoryPatchProposal,
    *,
    lifecycle_state: PatchState,
) -> MemoryPatchProposal:
    """Internal constructor used only after state-machine validation."""

    return replace(
        proposal,
        lifecycle_state=lifecycle_state,
        _lifecycle_permit=_MEMORY_PATCH_LIFECYCLE_PERMIT,
    )


def _replace_shared_promotion_lifecycle(
    proposal: SharedPromotionProposal,
    **updates: object,
) -> SharedPromotionProposal:
    """Internal constructor used only after shared review validation."""

    return replace(
        proposal,
        **updates,
        _lifecycle_permit=_SHARED_PROMOTION_LIFECYCLE_PERMIT,
    )
