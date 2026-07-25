"""Private, user-owned, non-executable Personal Memory HAT contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .enums import (
    MemoryConflictType,
    MemoryContentKind,
    MemoryRetrievalStatus,
    MemoryTrustClass,
    MemoryVisibility,
    PersonalMemorySpaceState,
)
from .exceptions import (
    ContractValidationError,
    OwnershipViolation,
    QuotaExceeded,
)
from .identities import MemoryOwnership, verify_ownership
from .scope import ScopeDimension, scope_interval_is_valid
from .serialization import (
    ensure_utc,
    freeze_json,
    require_enum_member,
    require_non_empty,
)


TRUST_PRECEDENCE: tuple[MemoryTrustClass, ...] = (
    MemoryTrustClass.CANONICAL_SOURCE_EVIDENCE,
    MemoryTrustClass.SHARED_HAT_VERIFIED_MEMORY,
    MemoryTrustClass.PERSONAL_VERIFIED_PATCH,
    MemoryTrustClass.USER_ASSERTED_MEMORY,
    MemoryTrustClass.MODEL_EXPERIENCE_HINT,
    MemoryTrustClass.SESSION_MEMORY,
)

_TRUST_RANK = {
    trust_class: len(TRUST_PRECEDENCE) - index
    for index, trust_class in enumerate(TRUST_PRECEDENCE)
}

_ALLOWED_PREFERENCE_KEYS = frozenset(
    {
        "language",
        "format",
        "explanation_depth",
        "presentation",
        "workflow_preference",
    }
)


@dataclass(frozen=True, slots=True)
class PersonalHatQuotaPolicy:
    """Configurable limits; ``None`` means the deployment has not set a cap."""

    maximum_total_spaces: int | None = None
    maximum_active_spaces: int | None = None
    maximum_archived_spaces: int | None = None
    maximum_bytes: int | None = None
    maximum_personal_sources: int | None = None
    maximum_active_memory_patches: int | None = None
    maximum_session_memory_bytes: int | None = None
    maximum_ingestion_jobs: int | None = None
    maximum_embedding_or_index_bytes: int | None = None

    def __post_init__(self) -> None:
        for field_name in self.__dataclass_fields__:
            value = getattr(self, field_name)
            if value is not None and (
                not isinstance(value, int) or isinstance(value, bool) or value < 0
            ):
                raise ContractValidationError(
                    f"{field_name} must be a non-negative integer or None"
                )


@dataclass(frozen=True, slots=True)
class PersonalHatQuotaUsage:
    total_spaces: int = 0
    active_spaces: int = 0
    archived_spaces: int = 0
    bytes_used: int = 0
    personal_sources: int = 0
    active_memory_patches: int = 0
    session_memory_bytes: int = 0
    ingestion_jobs: int = 0
    embedding_or_index_bytes: int = 0

    def __post_init__(self) -> None:
        for field_name in self.__dataclass_fields__:
            value = getattr(self, field_name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ContractValidationError(
                    f"{field_name} must be a non-negative integer"
                )


@dataclass(frozen=True, slots=True)
class PersonalMemorySpace:
    """Data namespace marketed as a Personal Memory HAT; never executable code."""

    personal_memory_space_id: str
    tenant_id: str
    user_id: str
    state: PersonalMemorySpaceState
    display_name: str | None
    created_at: datetime
    updated_at: datetime
    model_binding_ids: tuple[str, ...] = ()
    export_requested_at: datetime | None = None
    deletion_requested_at: datetime | None = None
    deleted_at: datetime | None = None

    def __post_init__(self) -> None:
        require_non_empty(
            self.personal_memory_space_id, "personal_memory_space_id"
        )
        require_non_empty(self.tenant_id, "tenant_id")
        require_non_empty(self.user_id, "user_id")
        require_enum_member(self.state, PersonalMemorySpaceState, "state")
        object.__setattr__(
            self, "created_at", ensure_utc(self.created_at, "created_at")
        )
        object.__setattr__(
            self, "updated_at", ensure_utc(self.updated_at, "updated_at")
        )
        if self.updated_at < self.created_at:
            raise ContractValidationError("updated_at cannot precede created_at")
        if self.state is PersonalMemorySpaceState.EMPTY:
            if self.display_name is not None:
                raise ContractValidationError("an EMPTY space cannot already be named")
            if self.model_binding_ids:
                raise ContractValidationError(
                    "an EMPTY space must be inert and have no model binding"
                )
        elif self.state in {
            PersonalMemorySpaceState.CONFIGURED,
            PersonalMemorySpaceState.ACTIVE,
            PersonalMemorySpaceState.SUSPENDED,
            PersonalMemorySpaceState.ARCHIVED,
        }:
            require_non_empty(self.display_name or "", "display_name")
        if len(set(self.model_binding_ids)) != len(self.model_binding_ids):
            raise ContractValidationError("model bindings must be unique")
        for field_name in (
            "export_requested_at",
            "deletion_requested_at",
            "deleted_at",
        ):
            timestamp = getattr(self, field_name)
            if timestamp is not None:
                object.__setattr__(
                    self, field_name, ensure_utc(timestamp, field_name)
                )
        if (
            self.state is PersonalMemorySpaceState.DELETED_PENDING
            and self.deletion_requested_at is None
        ):
            raise ContractValidationError(
                "DELETED_PENDING requires deletion_requested_at"
            )
        if self.state is PersonalMemorySpaceState.DELETED and self.deleted_at is None:
            raise ContractValidationError("DELETED requires deleted_at")
        if self.state is not PersonalMemorySpaceState.DELETED and self.deleted_at:
            raise ContractValidationError("only a DELETED space may set deleted_at")

    @property
    def ownership(self) -> MemoryOwnership:
        return MemoryOwnership(
            tenant_id=self.tenant_id,
            user_id=self.user_id,
            personal_memory_space_id=self.personal_memory_space_id,
        )

    @property
    def retrieval_eligible(self) -> bool:
        return self.state is PersonalMemorySpaceState.ACTIVE


@dataclass(frozen=True, slots=True)
class PersonalMemoryPool:
    """A tenant/user pool whose size is policy data, never a hardcoded constant."""

    tenant_id: str
    user_id: str
    quota_policy: PersonalHatQuotaPolicy
    spaces: tuple[PersonalMemorySpace, ...] = ()

    def __post_init__(self) -> None:
        require_non_empty(self.tenant_id, "tenant_id")
        require_non_empty(self.user_id, "user_id")
        ids = [space.personal_memory_space_id for space in self.spaces]
        if len(ids) != len(set(ids)):
            raise ContractValidationError("personal memory space IDs must be unique")
        for space in self.spaces:
            if space.tenant_id != self.tenant_id or space.user_id != self.user_id:
                raise OwnershipViolation(
                    "a Personal Memory HAT pool cannot contain another owner"
                )
        enforce_quota(self.quota_policy, quota_usage(self.spaces))


@dataclass(frozen=True, slots=True)
class MemoryItem:
    """Governed memory record; it never carries executable authority."""

    memory_item_id: str
    visibility: MemoryVisibility
    trust_class: MemoryTrustClass
    content_kind: MemoryContentKind
    content: Any
    scope_dimensions: tuple[ScopeDimension, ...]
    evidence_references: tuple[str, ...]
    created_at: datetime
    ownership: MemoryOwnership | None = None
    source_patch_id: str | None = None
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    expires_at: datetime | None = None
    active: bool = True
    revoked: bool = False

    def __post_init__(self) -> None:
        require_non_empty(self.memory_item_id, "memory_item_id")
        require_enum_member(self.visibility, MemoryVisibility, "visibility")
        require_enum_member(
            self.trust_class, MemoryTrustClass, "trust_class"
        )
        require_enum_member(
            self.content_kind, MemoryContentKind, "content_kind"
        )
        object.__setattr__(self, "content", freeze_json(self.content))
        object.__setattr__(
            self, "created_at", ensure_utc(self.created_at, "created_at")
        )
        for field_name in ("valid_from", "valid_until", "expires_at"):
            timestamp = getattr(self, field_name)
            if timestamp is not None:
                object.__setattr__(
                    self, field_name, ensure_utc(timestamp, field_name)
                )
        if not scope_interval_is_valid(self.valid_from, self.valid_until):
            raise ContractValidationError("memory validity interval is inverted")
        scope_names = [dimension.name for dimension in self.scope_dimensions]
        if len(scope_names) != len(set(scope_names)):
            raise ContractValidationError(
                "memory scope dimension names must be unique"
            )
        if any(
            not reference.strip() for reference in self.evidence_references
        ) or len(self.evidence_references) != len(set(self.evidence_references)):
            raise ContractValidationError(
                "memory evidence references must be non-empty and unique"
            )
        if (
            self.visibility
            in {MemoryVisibility.PERSONAL, MemoryVisibility.SESSION}
            and self.ownership is None
        ):
            raise ContractValidationError(
                "personal or session memory requires explicit ownership"
            )
        if self.visibility is MemoryVisibility.SHARED and self.ownership is not None:
            raise ContractValidationError(
                "shared memory cannot carry private-space ownership"
            )
        if self.trust_class is MemoryTrustClass.CANONICAL_SOURCE_EVIDENCE:
            raise ContractValidationError(
                "canonical evidence is not a MemoryItem or Memory Patch target"
            )
        if self.content_kind is MemoryContentKind.MODEL_EXPERIENCE and (
            self.trust_class is not MemoryTrustClass.MODEL_EXPERIENCE_HINT
        ):
            raise ContractValidationError(
                "model-experience content must remain an advisory hint"
            )
        if (
            self.content_kind is MemoryContentKind.FACTUAL
            and self.trust_class is MemoryTrustClass.PERSONAL_VERIFIED_PATCH
            and not self.evidence_references
        ):
            raise ContractValidationError(
                "personal verified factual memory requires evidence"
            )
        if self.content_kind is MemoryContentKind.PREFERENCE:
            validate_preference_content(self.content)


@dataclass(frozen=True, slots=True)
class MemoryConflict:
    """Explicit conflict record; resolution never silently overwrites memory."""

    conflict_type: MemoryConflictType
    higher_or_primary_item_id: str
    lower_or_secondary_item_id: str
    explanation: str
    resolved_by_precedence: bool

    def __post_init__(self) -> None:
        require_non_empty(
            self.higher_or_primary_item_id, "higher_or_primary_item_id"
        )
        require_enum_member(
            self.conflict_type, MemoryConflictType, "conflict_type"
        )
        if self.conflict_type is not MemoryConflictType.NO_CONFLICT:
            require_non_empty(
                self.lower_or_secondary_item_id, "lower_or_secondary_item_id"
            )
            require_non_empty(self.explanation, "conflict explanation")
        if (
            self.conflict_type is MemoryConflictType.SAME_TRUST_CONFLICT
            and self.resolved_by_precedence
        ):
            raise ContractValidationError(
                "a same-trust conflict must remain explicit"
            )


def trust_rank(trust_class: MemoryTrustClass) -> int:
    """Return the deterministic factual precedence rank."""

    return _TRUST_RANK[trust_class]


def compare_memory_trust(
    left: MemoryTrustClass, right: MemoryTrustClass
) -> int:
    """Return positive when left outranks right, zero when equal."""

    return trust_rank(left) - trust_rank(right)


def classify_memory_conflict(
    primary: MemoryItem, secondary: MemoryItem
) -> MemoryConflict:
    """Classify a content conflict using trust only, never by silent mutation."""

    comparison = compare_memory_trust(primary.trust_class, secondary.trust_class)
    if comparison == 0:
        return MemoryConflict(
            conflict_type=MemoryConflictType.SAME_TRUST_CONFLICT,
            higher_or_primary_item_id=primary.memory_item_id,
            lower_or_secondary_item_id=secondary.memory_item_id,
            explanation="items at the same trust class conflict",
            resolved_by_precedence=False,
        )
    higher, lower = (
        (primary, secondary) if comparison > 0 else (secondary, primary)
    )
    return MemoryConflict(
        conflict_type=MemoryConflictType.LOWER_TRUST_CONFLICT,
        higher_or_primary_item_id=higher.memory_item_id,
        lower_or_secondary_item_id=lower.memory_item_id,
        explanation="lower-trust memory cannot override higher-trust memory",
        resolved_by_precedence=True,
    )


def validate_preference_content(content: Any) -> None:
    """Allow presentation preferences, never factual or authority overrides."""

    if not isinstance(content, dict) and not hasattr(content, "keys"):
        raise ContractValidationError("preference content must be an object")
    unsupported = set(content.keys()) - _ALLOWED_PREFERENCE_KEYS
    if unsupported:
        raise ContractValidationError(
            "preference memory cannot override evidence, scope, policy, "
            f"security, or approval: {', '.join(sorted(unsupported))}"
        )


def memory_item_is_retrieval_eligible(
    item: MemoryItem,
    *,
    at_time: datetime,
    tenant_id: str | None = None,
    user_id: str | None = None,
    personal_memory_space_id: str | None = None,
    personal_space_state: PersonalMemorySpaceState | None = None,
    shared_retrieval: bool = False,
) -> bool:
    """Apply visibility, ownership, lifecycle, revocation, and freshness guards."""

    return (
        memory_item_retrieval_status(
            item,
            at_time=at_time,
            tenant_id=tenant_id,
            user_id=user_id,
            personal_memory_space_id=personal_memory_space_id,
            personal_space_state=personal_space_state,
            shared_retrieval=shared_retrieval,
        )
        is MemoryRetrievalStatus.ELIGIBLE
    )


def memory_item_retrieval_status(
    item: MemoryItem,
    *,
    at_time: datetime,
    tenant_id: str | None = None,
    user_id: str | None = None,
    personal_memory_space_id: str | None = None,
    personal_space_state: PersonalMemorySpaceState | None = None,
    shared_retrieval: bool = False,
) -> MemoryRetrievalStatus:
    """Return an explicit exclusion reason while preserving ownership failures."""

    at_time = ensure_utc(at_time, "at_time")
    if item.revoked:
        return MemoryRetrievalStatus.REVOKED
    if not item.active:
        return MemoryRetrievalStatus.INACTIVE
    if item.valid_from is not None and at_time < item.valid_from:
        return MemoryRetrievalStatus.NOT_YET_VALID
    if item.valid_until is not None and at_time > item.valid_until:
        return MemoryRetrievalStatus.STALE
    if item.expires_at is not None and at_time >= item.expires_at:
        return MemoryRetrievalStatus.EXPIRED
    if item.visibility in {
        MemoryVisibility.PERSONAL,
        MemoryVisibility.SESSION,
    }:
        if shared_retrieval:
            raise OwnershipViolation(
                "shared HAT retrieval cannot include private or session memory"
            )
        if item.ownership is None:
            raise OwnershipViolation("personal memory has no ownership context")
        verify_ownership(
            item.ownership,
            tenant_id=tenant_id,
            user_id=user_id,
            personal_memory_space_id=personal_memory_space_id,
        )
        if (
            item.visibility is MemoryVisibility.PERSONAL
            and personal_space_state is not PersonalMemorySpaceState.ACTIVE
        ):
            return MemoryRetrievalStatus.SPACE_NOT_ACTIVE
    return MemoryRetrievalStatus.ELIGIBLE


def quota_usage(
    spaces: tuple[PersonalMemorySpace, ...],
) -> PersonalHatQuotaUsage:
    """Compute lifecycle counts represented directly by the current pool."""

    return PersonalHatQuotaUsage(
        total_spaces=sum(
            space.state is not PersonalMemorySpaceState.DELETED for space in spaces
        ),
        active_spaces=sum(
            space.state is PersonalMemorySpaceState.ACTIVE for space in spaces
        ),
        archived_spaces=sum(
            space.state is PersonalMemorySpaceState.ARCHIVED for space in spaces
        ),
    )


def enforce_quota(
    policy: PersonalHatQuotaPolicy, usage: PersonalHatQuotaUsage
) -> None:
    """Reject a usage snapshot above any configured deployment quota."""

    mappings = (
        ("maximum_total_spaces", "total_spaces"),
        ("maximum_active_spaces", "active_spaces"),
        ("maximum_archived_spaces", "archived_spaces"),
        ("maximum_bytes", "bytes_used"),
        ("maximum_personal_sources", "personal_sources"),
        ("maximum_active_memory_patches", "active_memory_patches"),
        ("maximum_session_memory_bytes", "session_memory_bytes"),
        ("maximum_ingestion_jobs", "ingestion_jobs"),
        ("maximum_embedding_or_index_bytes", "embedding_or_index_bytes"),
    )
    for limit_name, usage_name in mappings:
        limit = getattr(policy, limit_name)
        current = getattr(usage, usage_name)
        if limit is not None and current > limit:
            raise QuotaExceeded(
                f"{usage_name}={current} exceeds configured {limit_name}={limit}"
            )
