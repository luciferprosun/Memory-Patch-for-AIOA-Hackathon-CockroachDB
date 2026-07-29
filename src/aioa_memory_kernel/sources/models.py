"""Immutable source-registry, provenance, and publication values."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping

from aioa_memory_kernel.contracts.enums import (
    MemoryTargetScope,
    StableStringEnum,
)
from aioa_memory_kernel.contracts.serialization import (
    canonical_sha256,
    ensure_utc,
    freeze_json,
    require_sha256_hex,
)

from .errors import SourceRegistryValidationError


SOURCE_REGISTRY_SCHEMA_VERSION = "1.0.0"
PUBLICATION_ELIGIBILITY_POLICY_VERSION = "source-publication-eligibility-1a"
PUBLICATION_GENESIS_MARKER = "SOURCE_PUBLICATION_GENESIS_1A"
PUBLICATION_GENESIS_DIGEST = canonical_sha256(
    {
        "contract_type": "SourcePublicationGenesis",
        "contract_version": SOURCE_REGISTRY_SCHEMA_VERSION,
        "marker": PUBLICATION_GENESIS_MARKER,
    }
)


class SourceAuthorityLevel(StableStringEnum):
    OFFICIAL_PRIMARY = "OFFICIAL_PRIMARY"
    AUTHORITATIVE_SECONDARY = "AUTHORITATIVE_SECONDARY"
    INFORMATIONAL_SECONDARY = "INFORMATIONAL_SECONDARY"
    USER_SUPPLIED = "USER_SUPPLIED"
    DERIVED = "DERIVED"
    UNKNOWN = "UNKNOWN"


class SourceLicenseStatus(StableStringEnum):
    PUBLIC_DOMAIN = "PUBLIC_DOMAIN"
    CONFIRMED_PERMISSIVE = "CONFIRMED_PERMISSIVE"
    CONFIRMED_RESTRICTED = "CONFIRMED_RESTRICTED"
    PRIVATE_AUTHORIZED = "PRIVATE_AUTHORIZED"
    UNKNOWN = "UNKNOWN"
    PROHIBITED = "PROHIBITED"


class SourceAccessClass(StableStringEnum):
    PUBLIC = "PUBLIC"
    TENANT_RESTRICTED = "TENANT_RESTRICTED"
    USER_PRIVATE = "USER_PRIVATE"


class RedactionState(StableStringEnum):
    NOT_REQUIRED = "NOT_REQUIRED"
    PENDING = "PENDING"
    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"


class SourcePublicationState(StableStringEnum):
    REGISTERED = "REGISTERED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    ELIGIBLE = "ELIGIBLE"
    PUBLISHED = "PUBLISHED"
    QUARANTINED = "QUARANTINED"
    WITHDRAWN = "WITHDRAWN"
    REJECTED = "REJECTED"


class SourceRegistryActorType(StableStringEnum):
    """Actors trusted by a future authenticated application boundary.

    These values are audit classifications only. They do not authenticate a
    human and do not grant approval, commit, publication, or execution
    authority by themselves.
    """

    TRUSTED_APPLICATION = "TRUSTED_APPLICATION"
    HUMAN_REVIEWER = "HUMAN_REVIEWER"
    MIGRATION_SERVICE = "MIGRATION_SERVICE"


def _text(value: object, field: str, maximum: int = 255) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > maximum
    ):
        raise SourceRegistryValidationError(
            f"{field} must be a bounded canonical non-empty string",
            sanitized_code="INVALID_SOURCE_REGISTRY_VALUE",
        )
    return value


def _optional_text(
    value: object | None,
    field: str,
    maximum: int = 255,
) -> str | None:
    if value is None:
        return None
    return _text(value, field, maximum)


def _enum(value: object, expected: type[Enum], field: str) -> Any:
    if not isinstance(value, expected):
        raise SourceRegistryValidationError(
            f"{field} must be a {expected.__name__} member",
            sanitized_code="INVALID_SOURCE_REGISTRY_ENUM",
        )
    return value


def _digest(value: object, field: str) -> str:
    try:
        return require_sha256_hex(value, field)  # type: ignore[arg-type]
    except Exception as exc:
        raise SourceRegistryValidationError(
            f"{field} must be a lowercase SHA-256 digest",
            sanitized_code="INVALID_SOURCE_DIGEST",
        ) from exc


def _optional_digest(value: object | None, field: str) -> str | None:
    if value is None:
        return None
    return _digest(value, field)


def _timestamp(value: object, field: str) -> datetime:
    try:
        return ensure_utc(value, field)  # type: ignore[arg-type]
    except Exception as exc:
        raise SourceRegistryValidationError(
            f"{field} must be timezone-aware",
            sanitized_code="INVALID_SOURCE_TIMESTAMP",
        ) from exc


def _optional_timestamp(value: object | None, field: str) -> datetime | None:
    if value is None:
        return None
    return _timestamp(value, field)


def _bounded_json(
    value: Any,
    *,
    field: str,
    depth: int = 0,
    item_budget: list[int] | None = None,
) -> Any:
    if item_budget is None:
        item_budget = [0]
    item_budget[0] += 1
    if item_budget[0] > 128 or depth > 4:
        raise SourceRegistryValidationError(
            f"{field} exceeds the bounded metadata shape",
            sanitized_code="UNBOUNDED_SOURCE_METADATA",
        )
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return freeze_json(value)
    if isinstance(value, str):
        if len(value) > 2048:
            raise SourceRegistryValidationError(
                f"{field} contains an overlong value",
                sanitized_code="UNBOUNDED_SOURCE_METADATA",
            )
        return value
    if isinstance(value, Mapping):
        if len(value) > 32 or not all(isinstance(key, str) for key in value):
            raise SourceRegistryValidationError(
                f"{field} must use bounded string-keyed objects",
                sanitized_code="UNBOUNDED_SOURCE_METADATA",
            )
        return MappingProxyType(
            {
                _text(key, f"{field} key", 128): _bounded_json(
                    child,
                    field=field,
                    depth=depth + 1,
                    item_budget=item_budget,
                )
                for key, child in sorted(value.items())
            }
        )
    if isinstance(value, (list, tuple)):
        if len(value) > 32:
            raise SourceRegistryValidationError(
                f"{field} contains too many values",
                sanitized_code="UNBOUNDED_SOURCE_METADATA",
            )
        return tuple(
            _bounded_json(
                child,
                field=field,
                depth=depth + 1,
                item_budget=item_budget,
            )
            for child in value
        )
    raise SourceRegistryValidationError(
        f"{field} contains an unsupported value",
        sanitized_code="INVALID_SOURCE_METADATA",
    )


def _mapping(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SourceRegistryValidationError(
            f"{field} must be an object",
            sanitized_code="INVALID_SOURCE_METADATA",
        )
    bounded = _bounded_json(value, field=field)
    assert isinstance(bounded, Mapping)
    return bounded


def _ordered_codes(value: object, field: str = "reason_codes") -> tuple[str, ...]:
    if not isinstance(value, (list, tuple, set, frozenset)):
        raise SourceRegistryValidationError(
            f"{field} must be a bounded collection",
            sanitized_code="INVALID_REASON_CODES",
        )
    codes = tuple(sorted({_text(item, f"{field} item", 128) for item in value}))
    if len(codes) > 32:
        raise SourceRegistryValidationError(
            f"{field} contains too many values",
            sanitized_code="INVALID_REASON_CODES",
        )
    return codes


@dataclass(frozen=True, slots=True)
class ParserIdentity:
    parser_name: str
    parser_version: str
    parser_contract_version: str

    def __post_init__(self) -> None:
        for field in (
            "parser_name",
            "parser_version",
            "parser_contract_version",
        ):
            value = _text(getattr(self, field), field, 128)
            if value.casefold() == "latest":
                raise SourceRegistryValidationError(
                    "mutable parser aliases are forbidden",
                    sanitized_code="MUTABLE_VERSION_ALIAS",
                )
            object.__setattr__(self, field, value)


@dataclass(frozen=True, slots=True)
class TransformationIdentity:
    transformation_name: str
    transformation_version: str
    transformation_contract_version: str

    def __post_init__(self) -> None:
        for field in (
            "transformation_name",
            "transformation_version",
            "transformation_contract_version",
        ):
            value = _text(getattr(self, field), field, 128)
            if value.casefold() == "latest":
                raise SourceRegistryValidationError(
                    "mutable transformation aliases are forbidden",
                    sanitized_code="MUTABLE_VERSION_ALIAS",
                )
            object.__setattr__(self, field, value)


@dataclass(frozen=True, slots=True)
class SourceAuthorityAssessment:
    authority_level: SourceAuthorityLevel
    authority_basis: Mapping[str, Any]

    def __post_init__(self) -> None:
        _enum(self.authority_level, SourceAuthorityLevel, "authority_level")
        basis = _mapping(self.authority_basis, "authority_basis")
        if self.authority_level is not SourceAuthorityLevel.UNKNOWN and not basis:
            raise SourceRegistryValidationError(
                "known authority requires an explicit authority basis",
                sanitized_code="AUTHORITY_BASIS_REQUIRED",
            )
        object.__setattr__(self, "authority_basis", basis)


@dataclass(frozen=True, slots=True)
class SourceLicenseAssessment:
    license_status: SourceLicenseStatus
    license_identifier: str | None = None
    license_reference: str | None = None

    def __post_init__(self) -> None:
        _enum(self.license_status, SourceLicenseStatus, "license_status")
        object.__setattr__(
            self,
            "license_identifier",
            _optional_text(self.license_identifier, "license_identifier", 255),
        )
        object.__setattr__(
            self,
            "license_reference",
            _optional_text(self.license_reference, "license_reference", 1024),
        )


@dataclass(frozen=True, slots=True)
class SourceScopeDimensions:
    tenant_id: str
    hat_scope_id: str
    target_scope: MemoryTargetScope
    owner_user_id: str | None = None
    personal_memory_space_id: str | None = None
    domain: str | None = None
    jurisdiction: str | None = None
    language: str | None = None
    temporal_policy_reference: str | None = None
    source_collection: tuple[str, ...] = ()
    additional_dimensions: Mapping[str, Any] = MappingProxyType({})
    scope_digest: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "tenant_id", _text(self.tenant_id, "tenant_id"))
        object.__setattr__(
            self, "hat_scope_id", _text(self.hat_scope_id, "hat_scope_id")
        )
        _enum(self.target_scope, MemoryTargetScope, "target_scope")
        owner = _optional_text(self.owner_user_id, "owner_user_id")
        space = _optional_text(
            self.personal_memory_space_id,
            "personal_memory_space_id",
        )
        if self.target_scope is MemoryTargetScope.USER_PERSONAL_HAT:
            if owner is None or space is None:
                raise SourceRegistryValidationError(
                    "personal source scope requires exact owner and memory space",
                    sanitized_code="PERSONAL_SOURCE_OWNER_REQUIRED",
                )
        elif self.target_scope is MemoryTargetScope.SHARED_KNOWLEDGE_HAT:
            if owner is not None or space is not None:
                raise SourceRegistryValidationError(
                    "shared source scope cannot carry personal ownership",
                    sanitized_code="SHARED_SOURCE_OWNER_FORBIDDEN",
                )
        else:
            raise SourceRegistryValidationError(
                "session scope is not a durable source registry scope",
                sanitized_code="INVALID_SOURCE_TARGET_SCOPE",
            )
        object.__setattr__(self, "owner_user_id", owner)
        object.__setattr__(self, "personal_memory_space_id", space)
        for field, maximum in (
            ("domain", 255),
            ("jurisdiction", 255),
            ("language", 64),
            ("temporal_policy_reference", 512),
        ):
            object.__setattr__(
                self,
                field,
                _optional_text(getattr(self, field), field, maximum),
            )
        if not isinstance(self.source_collection, (tuple, list, set, frozenset)):
            raise SourceRegistryValidationError(
                "source_collection must be a bounded collection",
                sanitized_code="INVALID_SOURCE_COLLECTION",
            )
        collection = tuple(
            sorted(
                {
                    _text(item, "source_collection item", 255)
                    for item in self.source_collection
                }
            )
        )
        if len(collection) > 32:
            raise SourceRegistryValidationError(
                "source_collection contains too many values",
                sanitized_code="INVALID_SOURCE_COLLECTION",
            )
        object.__setattr__(self, "source_collection", collection)
        object.__setattr__(
            self,
            "additional_dimensions",
            _mapping(self.additional_dimensions, "additional_dimensions"),
        )
        expected = canonical_sha256(self, exclude_fields=("scope_digest",))
        if self.scope_digest:
            if _digest(self.scope_digest, "scope_digest") != expected:
                raise SourceRegistryValidationError(
                    "scope_digest does not match canonical scope",
                    sanitized_code="SCOPE_DIGEST_MISMATCH",
                )
        else:
            object.__setattr__(self, "scope_digest", expected)


@dataclass(frozen=True, slots=True)
class OriginMetadata:
    origin_kind: str
    origin_system: str
    origin_version: str
    adapter_version: str
    external_ref: str | None
    observed_at: datetime | None = None

    def __post_init__(self) -> None:
        for field, maximum in (
            ("origin_kind", 128),
            ("origin_system", 255),
            ("origin_version", 128),
            ("adapter_version", 128),
        ):
            value = _text(getattr(self, field), field, maximum)
            if field.endswith("version") and value.casefold() == "latest":
                raise SourceRegistryValidationError(
                    "mutable origin or adapter aliases are forbidden",
                    sanitized_code="MUTABLE_VERSION_ALIAS",
                )
            object.__setattr__(self, field, value)
        object.__setattr__(
            self,
            "external_ref",
            _optional_text(self.external_ref, "external_ref", 1024),
        )
        object.__setattr__(
            self,
            "observed_at",
            _optional_timestamp(self.observed_at, "observed_at"),
        )


@dataclass(frozen=True, slots=True)
class ProvenanceArtifactIdentity:
    artifact_kind: str
    artifact_digest: str
    byte_length: int | None
    media_type: str | None
    origin: OriginMetadata
    parser: ParserIdentity
    transformation: TransformationIdentity
    created_at: datetime
    exact_source_bytes: bool = False
    model_generated: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "artifact_kind", _text(self.artifact_kind, "artifact_kind", 128)
        )
        object.__setattr__(
            self, "artifact_digest", _digest(self.artifact_digest, "artifact_digest")
        )
        if self.byte_length is not None and (
            not isinstance(self.byte_length, int)
            or isinstance(self.byte_length, bool)
            or self.byte_length < 0
        ):
            raise SourceRegistryValidationError(
                "byte_length must be a non-negative integer when present",
                sanitized_code="INVALID_ARTIFACT_LENGTH",
            )
        object.__setattr__(
            self,
            "media_type",
            _optional_text(self.media_type, "media_type", 255),
        )
        if not isinstance(self.origin, OriginMetadata):
            raise SourceRegistryValidationError(
                "artifact origin has the wrong type",
                sanitized_code="INVALID_ARTIFACT_IDENTITY",
            )
        if not isinstance(self.parser, ParserIdentity) or not isinstance(
            self.transformation, TransformationIdentity
        ):
            raise SourceRegistryValidationError(
                "artifact parser and transformation identities are required",
                sanitized_code="INVALID_ARTIFACT_IDENTITY",
            )
        object.__setattr__(
            self, "created_at", _timestamp(self.created_at, "artifact_created_at")
        )
        if self.exact_source_bytes and self.model_generated:
            raise SourceRegistryValidationError(
                "model output cannot claim exact source-byte identity",
                sanitized_code="MODEL_OUTPUT_NOT_SOURCE_BYTES",
            )


@dataclass(frozen=True, slots=True)
class ProvenanceEdge:
    tenant_id: str
    source_id: str
    hat_scope_id: str
    edge_id: str
    parent_artifact_digest: str
    child_artifact_digest: str
    edge_kind: str
    parser: ParserIdentity
    transformation: TransformationIdentity
    metadata: Mapping[str, Any]
    created_at: datetime
    edge_digest: str = ""

    def __post_init__(self) -> None:
        for field in ("tenant_id", "source_id", "hat_scope_id", "edge_id"):
            object.__setattr__(self, field, _text(getattr(self, field), field))
        object.__setattr__(
            self,
            "parent_artifact_digest",
            _digest(self.parent_artifact_digest, "parent_artifact_digest"),
        )
        object.__setattr__(
            self,
            "child_artifact_digest",
            _digest(self.child_artifact_digest, "child_artifact_digest"),
        )
        if self.parent_artifact_digest == self.child_artifact_digest:
            raise SourceRegistryValidationError(
                "provenance self-edges are forbidden",
                sanitized_code="PROVENANCE_SELF_EDGE",
            )
        object.__setattr__(self, "edge_kind", _text(self.edge_kind, "edge_kind", 128))
        if not isinstance(self.parser, ParserIdentity) or not isinstance(
            self.transformation, TransformationIdentity
        ):
            raise SourceRegistryValidationError(
                "edge parser and transformation identities are required",
                sanitized_code="INVALID_PROVENANCE_IDENTITY",
            )
        object.__setattr__(self, "metadata", _mapping(self.metadata, "edge_metadata"))
        object.__setattr__(self, "created_at", _timestamp(self.created_at, "created_at"))
        expected = canonical_sha256(self, exclude_fields=("edge_digest",))
        if self.edge_digest:
            if _digest(self.edge_digest, "edge_digest") != expected:
                raise SourceRegistryValidationError(
                    "edge_digest does not match canonical edge",
                    sanitized_code="EDGE_DIGEST_MISMATCH",
                )
        else:
            object.__setattr__(self, "edge_digest", expected)


@dataclass(frozen=True, slots=True)
class SourceRegistryRecord:
    tenant_id: str
    source_id: str
    hat_scope_id: str
    source_kind: str
    source_reference: str
    scope: SourceScopeDimensions
    authority: SourceAuthorityAssessment
    license: SourceLicenseAssessment
    access_class: SourceAccessClass
    redaction_state: RedactionState
    parser: ParserIdentity
    transformation: TransformationIdentity
    origin: OriginMetadata
    artifact: ProvenanceArtifactIdentity
    snapshot_id: str | None
    knowledge_version_id: str | None
    current_publication_state: SourcePublicationState
    current_publication_sequence: int
    current_publication_event_digest: str
    created_at: datetime
    updated_at: datetime
    registry_digest: str = ""
    schema_version: str = SOURCE_REGISTRY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for field in ("tenant_id", "source_id", "hat_scope_id"):
            object.__setattr__(self, field, _text(getattr(self, field), field))
        object.__setattr__(
            self, "source_kind", _text(self.source_kind, "source_kind", 128)
        )
        object.__setattr__(
            self,
            "source_reference",
            _text(self.source_reference, "source_reference", 1024),
        )
        if self.schema_version != SOURCE_REGISTRY_SCHEMA_VERSION:
            raise SourceRegistryValidationError(
                "unsupported source registry schema version",
                sanitized_code="INVALID_SOURCE_SCHEMA_VERSION",
            )
        if not isinstance(self.scope, SourceScopeDimensions):
            raise SourceRegistryValidationError(
                "scope has the wrong type",
                sanitized_code="INVALID_SOURCE_SCOPE",
            )
        if (
            self.tenant_id != self.scope.tenant_id
            or self.hat_scope_id != self.scope.hat_scope_id
        ):
            raise SourceRegistryValidationError(
                "registry identity and scope coordinates differ",
                sanitized_code="SOURCE_SCOPE_MISMATCH",
            )
        if not isinstance(self.authority, SourceAuthorityAssessment) or not isinstance(
            self.license, SourceLicenseAssessment
        ):
            raise SourceRegistryValidationError(
                "authority and license assessments are required",
                sanitized_code="INVALID_SOURCE_ASSESSMENT",
            )
        _enum(self.access_class, SourceAccessClass, "access_class")
        _enum(self.redaction_state, RedactionState, "redaction_state")
        if self.access_class is SourceAccessClass.USER_PRIVATE:
            if self.scope.target_scope is not MemoryTargetScope.USER_PERSONAL_HAT:
                raise SourceRegistryValidationError(
                    "user-private access requires a personal HAT scope",
                    sanitized_code="ACCESS_SCOPE_MISMATCH",
                )
        elif self.scope.target_scope is not MemoryTargetScope.SHARED_KNOWLEDGE_HAT:
            raise SourceRegistryValidationError(
                "public or tenant-restricted access requires a shared HAT scope",
                sanitized_code="ACCESS_SCOPE_MISMATCH",
            )
        for value, expected, field in (
            (self.parser, ParserIdentity, "parser"),
            (self.transformation, TransformationIdentity, "transformation"),
            (self.origin, OriginMetadata, "origin"),
            (self.artifact, ProvenanceArtifactIdentity, "artifact"),
        ):
            if not isinstance(value, expected):
                raise SourceRegistryValidationError(
                    f"{field} has the wrong type",
                    sanitized_code="INVALID_SOURCE_IDENTITY",
                )
        if (
            self.artifact.origin != self.origin
            or self.artifact.parser != self.parser
            or self.artifact.transformation != self.transformation
        ):
            raise SourceRegistryValidationError(
                "artifact identities differ from registry identities",
                sanitized_code="SOURCE_ARTIFACT_IDENTITY_MISMATCH",
            )
        object.__setattr__(
            self, "snapshot_id", _optional_text(self.snapshot_id, "snapshot_id")
        )
        object.__setattr__(
            self,
            "knowledge_version_id",
            _optional_text(self.knowledge_version_id, "knowledge_version_id"),
        )
        _enum(
            self.current_publication_state,
            SourcePublicationState,
            "current_publication_state",
        )
        if (
            not isinstance(self.current_publication_sequence, int)
            or isinstance(self.current_publication_sequence, bool)
            or self.current_publication_sequence < 0
        ):
            raise SourceRegistryValidationError(
                "publication sequence must be a non-negative integer",
                sanitized_code="INVALID_PUBLICATION_SEQUENCE",
            )
        object.__setattr__(
            self,
            "current_publication_event_digest",
            _digest(
                self.current_publication_event_digest,
                "current_publication_event_digest",
            ),
        )
        if (
            self.current_publication_sequence == 0
            and (
                self.current_publication_state
                is not SourcePublicationState.REGISTERED
                or self.current_publication_event_digest
                != PUBLICATION_GENESIS_DIGEST
            )
        ) or (
            self.current_publication_sequence > 0
            and (
                self.current_publication_state
                is SourcePublicationState.REGISTERED
                or self.current_publication_event_digest
                == PUBLICATION_GENESIS_DIGEST
            )
        ):
            raise SourceRegistryValidationError(
                "publication pointer must be either exact genesis or a "
                "non-genesis event state",
                sanitized_code="INVALID_PUBLICATION_GENESIS",
            )
        object.__setattr__(self, "created_at", _timestamp(self.created_at, "created_at"))
        object.__setattr__(self, "updated_at", _timestamp(self.updated_at, "updated_at"))
        if self.updated_at < self.created_at:
            raise SourceRegistryValidationError(
                "updated_at cannot precede created_at",
                sanitized_code="INVALID_SOURCE_TIMESTAMP_ORDER",
            )
        expected = canonical_sha256(
            self,
            exclude_fields=(
                "registry_digest",
                "current_publication_state",
                "current_publication_sequence",
                "current_publication_event_digest",
                "updated_at",
            ),
        )
        if self.registry_digest:
            if _digest(self.registry_digest, "registry_digest") != expected:
                raise SourceRegistryValidationError(
                    "registry_digest does not match canonical registration facts",
                    sanitized_code="REGISTRY_DIGEST_MISMATCH",
                )
        else:
            object.__setattr__(self, "registry_digest", expected)


@dataclass(frozen=True, slots=True)
class PublicationEligibilityDecision:
    policy_version: str
    eligible: bool
    reason_codes: tuple[str, ...]
    registry_digest: str
    scope_digest: str
    lineage_root_digests: tuple[str, ...]
    lineage_digest: str
    lineage_terminal_digest: str
    snapshot_id: str | None
    knowledge_version_id: str | None
    evaluated_at: datetime
    decision_digest: str = ""

    def __post_init__(self) -> None:
        if self.policy_version != PUBLICATION_ELIGIBILITY_POLICY_VERSION:
            raise SourceRegistryValidationError(
                "unsupported publication eligibility policy",
                sanitized_code="INVALID_ELIGIBILITY_POLICY",
            )
        if not isinstance(self.eligible, bool):
            raise SourceRegistryValidationError(
                "eligible must be a boolean",
                sanitized_code="INVALID_ELIGIBILITY_DECISION",
            )
        object.__setattr__(
            self, "reason_codes", _ordered_codes(self.reason_codes)
        )
        if self.eligible == bool(self.reason_codes):
            raise SourceRegistryValidationError(
                "eligible decisions must have no reasons; ineligible decisions need reasons",
                sanitized_code="INVALID_ELIGIBILITY_DECISION",
            )
        if (
            not isinstance(self.lineage_root_digests, tuple)
            or not self.lineage_root_digests
        ):
            raise SourceRegistryValidationError(
                "lineage roots must be a non-empty immutable tuple",
                sanitized_code="INVALID_ELIGIBILITY_LINEAGE",
            )
        roots = tuple(
            sorted(
                {
                    _digest(value, "lineage_root_digest")
                    for value in self.lineage_root_digests
                }
            )
        )
        if roots != self.lineage_root_digests:
            raise SourceRegistryValidationError(
                "lineage roots must be unique and canonically ordered",
                sanitized_code="INVALID_ELIGIBILITY_LINEAGE",
            )
        for field in (
            "registry_digest",
            "scope_digest",
            "lineage_digest",
            "lineage_terminal_digest",
        ):
            object.__setattr__(self, field, _digest(getattr(self, field), field))
        object.__setattr__(
            self, "snapshot_id", _optional_text(self.snapshot_id, "snapshot_id")
        )
        object.__setattr__(
            self,
            "knowledge_version_id",
            _optional_text(self.knowledge_version_id, "knowledge_version_id"),
        )
        object.__setattr__(
            self, "evaluated_at", _timestamp(self.evaluated_at, "evaluated_at")
        )
        expected = canonical_sha256(self, exclude_fields=("decision_digest",))
        if self.decision_digest:
            if _digest(self.decision_digest, "decision_digest") != expected:
                raise SourceRegistryValidationError(
                    "decision_digest does not match canonical eligibility decision",
                    sanitized_code="ELIGIBILITY_DIGEST_MISMATCH",
                )
        else:
            object.__setattr__(self, "decision_digest", expected)


@dataclass(frozen=True, slots=True)
class SourceRegistryActor:
    actor_type: SourceRegistryActorType
    actor_reference: str

    def __post_init__(self) -> None:
        _enum(self.actor_type, SourceRegistryActorType, "actor_type")
        object.__setattr__(
            self,
            "actor_reference",
            _text(self.actor_reference, "actor_reference", 255),
        )


@dataclass(frozen=True, slots=True)
class PublicationStateEvent:
    tenant_id: str
    source_id: str
    hat_scope_id: str
    event_id: str
    sequence_number: int
    from_state: SourcePublicationState
    to_state: SourcePublicationState
    actor_type: SourceRegistryActorType
    actor_reference: str
    policy_version: str
    eligibility_decision_digest: str
    reason_codes: tuple[str, ...]
    reviewer_reference: str | None
    previous_event_digest: str
    created_at: datetime
    event_digest: str = ""

    def __post_init__(self) -> None:
        for field in ("tenant_id", "source_id", "hat_scope_id", "event_id"):
            object.__setattr__(self, field, _text(getattr(self, field), field))
        if (
            not isinstance(self.sequence_number, int)
            or isinstance(self.sequence_number, bool)
            or self.sequence_number < 1
        ):
            raise SourceRegistryValidationError(
                "event sequence must be a positive integer",
                sanitized_code="INVALID_PUBLICATION_SEQUENCE",
            )
        _enum(self.from_state, SourcePublicationState, "from_state")
        _enum(self.to_state, SourcePublicationState, "to_state")
        _enum(self.actor_type, SourceRegistryActorType, "actor_type")
        object.__setattr__(
            self,
            "actor_reference",
            _text(self.actor_reference, "actor_reference", 255),
        )
        object.__setattr__(
            self, "policy_version", _text(self.policy_version, "policy_version", 128)
        )
        object.__setattr__(
            self,
            "eligibility_decision_digest",
            _digest(
                self.eligibility_decision_digest,
                "eligibility_decision_digest",
            ),
        )
        object.__setattr__(
            self, "reason_codes", _ordered_codes(self.reason_codes)
        )
        object.__setattr__(
            self,
            "reviewer_reference",
            _optional_text(self.reviewer_reference, "reviewer_reference", 255),
        )
        object.__setattr__(
            self,
            "previous_event_digest",
            _digest(self.previous_event_digest, "previous_event_digest"),
        )
        object.__setattr__(self, "created_at", _timestamp(self.created_at, "created_at"))
        expected = canonical_sha256(self, exclude_fields=("event_digest",))
        if self.event_digest:
            if _digest(self.event_digest, "event_digest") != expected:
                raise SourceRegistryValidationError(
                    "event_digest does not match canonical publication event",
                    sanitized_code="PUBLICATION_EVENT_DIGEST_MISMATCH",
                )
        else:
            object.__setattr__(self, "event_digest", expected)


def utc_now() -> datetime:
    """A small injectable-default helper for production service boundaries."""

    return datetime.now(UTC)
