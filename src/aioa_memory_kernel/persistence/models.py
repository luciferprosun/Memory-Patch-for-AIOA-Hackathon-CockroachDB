"""Immutable values shared by retry, idempotency, and CockroachDB adapters."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping

from aioa_memory_kernel.contracts.serialization import canonical_sha256

from .errors import PersistenceConfigurationError


_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_SQLSTATE = re.compile(r"^[0-9A-Z]{5}$")
_ERROR_CODE = re.compile(r"^[A-Z0-9][A-Z0-9_:-]{0,63}$")


def _text(value: object, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise PersistenceConfigurationError(
            f"{field} must be a canonical non-empty string",
            sanitized_code="INVALID_PERSISTENCE_VALUE",
        )
    if len(value) > maximum:
        raise PersistenceConfigurationError(
            f"{field} exceeds its bounded length",
            sanitized_code="INVALID_PERSISTENCE_VALUE",
        )
    return value


def _optional_text(
    value: object | None,
    field: str,
    maximum: int,
) -> str | None:
    if value is None:
        return None
    return _text(value, field, maximum)


def _digest(value: object, field: str) -> str:
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        raise PersistenceConfigurationError(
            f"{field} must be a lowercase SHA-256 digest",
            sanitized_code="INVALID_DIGEST",
        )
    return value


def _optional_digest(value: object | None, field: str) -> str | None:
    if value is None:
        return None
    return _digest(value, field)


def _utc(value: object, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise PersistenceConfigurationError(
            f"{field} must be timezone-aware",
            sanitized_code="INVALID_TIMESTAMP",
        )
    return value.astimezone(UTC)


def _optional_utc(value: object | None, field: str) -> datetime | None:
    if value is None:
        return None
    return _utc(value, field)


def _mapping(value: Mapping[str, Any], field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or not all(
        isinstance(key, str) for key in value
    ):
        raise PersistenceConfigurationError(
            f"{field} must be a string-keyed mapping",
            sanitized_code="INVALID_JSON_VALUE",
        )
    return MappingProxyType(dict(value))


class AccessMode(str, Enum):
    """Exact Step 5 request-context mode."""

    TENANT_SHARED = "TENANT_SHARED"
    USER_PRIVATE = "USER_PRIVATE"


class OperationStatus(str, Enum):
    """Persistence workflow status; never an authority lifecycle."""

    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    INTERRUPTED = "INTERRUPTED"
    FAILED_FINAL = "FAILED_FINAL"


@dataclass(frozen=True, slots=True)
class RequestContext:
    tenant_id: str
    user_id: str | None
    access_mode: AccessMode

    def __post_init__(self) -> None:
        object.__setattr__(self, "tenant_id", _text(self.tenant_id, "tenant_id", 255))
        if not isinstance(self.access_mode, AccessMode):
            raise PersistenceConfigurationError(
                "access_mode must be an AccessMode",
                sanitized_code="INVALID_REQUEST_CONTEXT",
            )
        if self.access_mode is AccessMode.TENANT_SHARED:
            if self.user_id is not None:
                raise PersistenceConfigurationError(
                    "tenant-shared context cannot carry a user",
                    sanitized_code="INVALID_REQUEST_CONTEXT",
                )
        else:
            object.__setattr__(
                self,
                "user_id",
                _text(self.user_id, "user_id", 255),
            )


@dataclass(frozen=True, slots=True)
class ExternalReferenceIdentity:
    origin_kind: str
    origin_system: str
    origin_version: str
    adapter_version: str
    artifact_kind: str
    external_ref: str

    def __post_init__(self) -> None:
        for field, maximum in (
            ("origin_kind", 128),
            ("origin_system", 255),
            ("origin_version", 128),
            ("adapter_version", 128),
            ("artifact_kind", 128),
            ("external_ref", 1024),
        ):
            object.__setattr__(
                self,
                field,
                _text(getattr(self, field), field, maximum),
            )


@dataclass(frozen=True, slots=True)
class BeginOperation:
    operation_id: str
    tenant_id: str
    owner_user_id: str | None
    operation_kind: str
    idempotency_key: str
    request_digest: str
    scope_digest: str
    created_at: datetime
    external_identity: ExternalReferenceIdentity | None = None
    schema_version: str = "1.0.0"

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "operation_id", _text(self.operation_id, "operation_id", 255)
        )
        object.__setattr__(self, "tenant_id", _text(self.tenant_id, "tenant_id", 255))
        object.__setattr__(
            self,
            "owner_user_id",
            _optional_text(self.owner_user_id, "owner_user_id", 255),
        )
        object.__setattr__(
            self,
            "operation_kind",
            _text(self.operation_kind, "operation_kind", 128),
        )
        object.__setattr__(
            self,
            "idempotency_key",
            _text(self.idempotency_key, "idempotency_key", 512),
        )
        object.__setattr__(
            self, "request_digest", _digest(self.request_digest, "request_digest")
        )
        object.__setattr__(
            self, "scope_digest", _digest(self.scope_digest, "scope_digest")
        )
        object.__setattr__(self, "created_at", _utc(self.created_at, "created_at"))
        if self.schema_version != "1.0.0":
            raise PersistenceConfigurationError(
                "unsupported persistence operation schema version",
                sanitized_code="INVALID_SCHEMA_VERSION",
            )
        if self.external_identity is not None and not isinstance(
            self.external_identity, ExternalReferenceIdentity
        ):
            raise PersistenceConfigurationError(
                "external identity must be complete or absent",
                sanitized_code="PARTIAL_EXTERNAL_IDENTITY",
            )


@dataclass(frozen=True, slots=True)
class PersistenceOperation:
    operation_id: str
    tenant_id: str
    owner_user_id: str | None
    operation_kind: str
    idempotency_key: str
    request_digest: str
    scope_digest: str
    status: OperationStatus
    attempt_count: int
    result_ref: str | None
    result_digest: str | None
    last_sqlstate: str | None
    sanitized_error_code: str | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None
    external_identity: ExternalReferenceIdentity | None
    schema_version: str = "1.0.0"

    def __post_init__(self) -> None:
        BeginOperation(
            operation_id=self.operation_id,
            tenant_id=self.tenant_id,
            owner_user_id=self.owner_user_id,
            operation_kind=self.operation_kind,
            idempotency_key=self.idempotency_key,
            request_digest=self.request_digest,
            scope_digest=self.scope_digest,
            created_at=self.created_at,
            external_identity=self.external_identity,
            schema_version=self.schema_version,
        )
        if not isinstance(self.status, OperationStatus):
            raise PersistenceConfigurationError(
                "status must be an OperationStatus",
                sanitized_code="INVALID_OPERATION_STATUS",
            )
        if not isinstance(self.attempt_count, int) or self.attempt_count < 0:
            raise PersistenceConfigurationError(
                "attempt_count must be non-negative",
                sanitized_code="INVALID_ATTEMPT_COUNT",
            )
        object.__setattr__(
            self, "result_ref", _optional_text(self.result_ref, "result_ref", 1024)
        )
        object.__setattr__(
            self,
            "result_digest",
            _optional_digest(self.result_digest, "result_digest"),
        )
        if self.last_sqlstate is not None and not _SQLSTATE.fullmatch(
            self.last_sqlstate
        ):
            raise PersistenceConfigurationError(
                "last_sqlstate must be exactly five bounded characters",
                sanitized_code="INVALID_SQLSTATE",
            )
        if self.sanitized_error_code is not None and not _ERROR_CODE.fullmatch(
            self.sanitized_error_code
        ):
            raise PersistenceConfigurationError(
                "sanitized_error_code is invalid",
                sanitized_code="INVALID_ERROR_CODE",
            )
        object.__setattr__(self, "created_at", _utc(self.created_at, "created_at"))
        object.__setattr__(self, "updated_at", _utc(self.updated_at, "updated_at"))
        object.__setattr__(
            self,
            "completed_at",
            _optional_utc(self.completed_at, "completed_at"),
        )
        if self.updated_at < self.created_at:
            raise PersistenceConfigurationError(
                "updated_at cannot precede created_at",
                sanitized_code="INVALID_TIMESTAMP_ORDER",
            )
        if self.completed_at is not None and (
            self.completed_at < self.created_at
            or self.completed_at > self.updated_at
        ):
            raise PersistenceConfigurationError(
                "completed_at must be within the operation time bounds",
                sanitized_code="INVALID_TIMESTAMP_ORDER",
            )
        if self.status is OperationStatus.COMPLETED:
            if self.result_digest is None or self.completed_at is None:
                raise PersistenceConfigurationError(
                    "completed operation requires a result digest and timestamp",
                    sanitized_code="INVALID_COMPLETION",
                )
            if (
                self.last_sqlstate is not None
                or self.sanitized_error_code is not None
            ):
                raise PersistenceConfigurationError(
                    "completed operation cannot retain failure metadata",
                    sanitized_code="INVALID_COMPLETION",
                )
        elif (
            self.result_ref is not None
            or self.result_digest is not None
            or self.completed_at is not None
        ):
            raise PersistenceConfigurationError(
                "non-completed operation cannot claim a result",
                sanitized_code="INVALID_COMPLETION",
            )


@dataclass(frozen=True, slots=True)
class OperationClaim:
    operation: PersistenceOperation
    may_proceed: bool
    resumed: bool


@dataclass(frozen=True, slots=True)
class KernelRunRecord:
    tenant_id: str
    kernel_run_id: str
    user_id: str
    personal_memory_space_id: str | None
    model_binding_id: str
    request_sha256: str
    created_at: datetime
    completed_at: datetime | None = None

    def __post_init__(self) -> None:
        for field in ("tenant_id", "kernel_run_id", "user_id", "model_binding_id"):
            object.__setattr__(
                self, field, _text(getattr(self, field), field, 255)
            )
        object.__setattr__(
            self,
            "personal_memory_space_id",
            _optional_text(
                self.personal_memory_space_id,
                "personal_memory_space_id",
                255,
            ),
        )
        object.__setattr__(
            self, "request_sha256", _digest(self.request_sha256, "request_sha256")
        )
        object.__setattr__(self, "created_at", _utc(self.created_at, "created_at"))
        object.__setattr__(
            self,
            "completed_at",
            _optional_utc(self.completed_at, "completed_at"),
        )


@dataclass(frozen=True, slots=True)
class DraftRecord:
    """Immutable row contract for the existing Step 4 drafts table."""

    tenant_id: str
    draft_id: str
    kernel_run_id: str
    draft_stage: int
    content_sha256: str
    immutable_content_reference: str
    created_at: datetime

    def __post_init__(self) -> None:
        for field in ("tenant_id", "draft_id", "kernel_run_id"):
            object.__setattr__(
                self,
                field,
                _text(getattr(self, field), field, 255),
            )
        if self.draft_stage != 1:
            raise PersistenceConfigurationError(
                "Step 22 persistence accepts Draft V1 only",
                sanitized_code="INVALID_DRAFT_STAGE",
            )
        object.__setattr__(
            self,
            "content_sha256",
            _digest(self.content_sha256, "content_sha256"),
        )
        object.__setattr__(
            self,
            "immutable_content_reference",
            _text(
                self.immutable_content_reference,
                "immutable_content_reference",
                140_000,
            ),
        )
        object.__setattr__(self, "created_at", _utc(self.created_at, "created_at"))


@dataclass(frozen=True, slots=True)
class SourceSnapshotRecord:
    tenant_id: str
    snapshot_id: str
    source_id: str
    hat_scope_id: str
    content_sha256: str
    byte_length: int
    storage_class: str
    immutable_object_reference: str
    captured_at: datetime
    source_observed_at: datetime | None
    provenance: Mapping[str, Any]

    def __post_init__(self) -> None:
        for field, maximum in (
            ("tenant_id", 255),
            ("snapshot_id", 255),
            ("source_id", 255),
            ("hat_scope_id", 255),
            ("storage_class", 128),
            ("immutable_object_reference", 1024),
        ):
            object.__setattr__(
                self, field, _text(getattr(self, field), field, maximum)
            )
        object.__setattr__(
            self, "content_sha256", _digest(self.content_sha256, "content_sha256")
        )
        if not isinstance(self.byte_length, int) or self.byte_length < 0:
            raise PersistenceConfigurationError(
                "byte_length must be non-negative",
                sanitized_code="INVALID_LENGTH",
            )
        object.__setattr__(self, "captured_at", _utc(self.captured_at, "captured_at"))
        object.__setattr__(
            self,
            "source_observed_at",
            _optional_utc(self.source_observed_at, "source_observed_at"),
        )
        object.__setattr__(self, "provenance", _mapping(self.provenance, "provenance"))


@dataclass(frozen=True, slots=True)
class EvidenceItemRecord:
    tenant_id: str
    evidence_id: str
    source_id: str
    knowledge_version_id: str
    hat_scope_id: str
    citation_reference: str
    content_sha256: str
    trust_class: str
    authority_rank: int
    scope_dimensions: tuple[Any, ...]
    metadata: Mapping[str, Any]
    retrieved_at: datetime
    valid_from: datetime | None = None
    valid_until: datetime | None = None

    def __post_init__(self) -> None:
        for field, maximum in (
            ("tenant_id", 255),
            ("evidence_id", 255),
            ("source_id", 255),
            ("knowledge_version_id", 255),
            ("hat_scope_id", 255),
            ("citation_reference", 1024),
            ("trust_class", 128),
        ):
            object.__setattr__(
                self, field, _text(getattr(self, field), field, maximum)
            )
        object.__setattr__(
            self, "content_sha256", _digest(self.content_sha256, "content_sha256")
        )
        if not isinstance(self.authority_rank, int) or self.authority_rank < 0:
            raise PersistenceConfigurationError(
                "authority_rank must be non-negative",
                sanitized_code="INVALID_AUTHORITY_RANK",
            )
        if not isinstance(self.scope_dimensions, tuple):
            raise PersistenceConfigurationError(
                "scope_dimensions must be an immutable tuple",
                sanitized_code="INVALID_JSON_VALUE",
            )
        object.__setattr__(self, "metadata", _mapping(self.metadata, "metadata"))
        object.__setattr__(
            self, "retrieved_at", _utc(self.retrieved_at, "retrieved_at")
        )
        object.__setattr__(
            self, "valid_from", _optional_utc(self.valid_from, "valid_from")
        )
        object.__setattr__(
            self, "valid_until", _optional_utc(self.valid_until, "valid_until")
        )


@dataclass(frozen=True, slots=True)
class AuditEventRecord:
    tenant_id: str
    event_id: str
    event_type: str
    actor_type: str
    actor_id: str
    payload_hash: str
    event_hash: str
    metadata: Mapping[str, Any]
    occurred_at: datetime
    kernel_run_id: str | None = None
    user_id: str | None = None
    personal_memory_space_id: str | None = None
    previous_event_hash: str | None = None
    schema_version: str = "1.0.0"

    def __post_init__(self) -> None:
        for field, maximum in (
            ("tenant_id", 255),
            ("event_id", 255),
            ("event_type", 128),
            ("actor_type", 128),
            ("actor_id", 255),
        ):
            object.__setattr__(
                self, field, _text(getattr(self, field), field, maximum)
            )
        for field in (
            "kernel_run_id",
            "user_id",
            "personal_memory_space_id",
        ):
            object.__setattr__(
                self,
                field,
                _optional_text(getattr(self, field), field, 255),
            )
        if (self.user_id is None) != (self.personal_memory_space_id is None):
            raise PersistenceConfigurationError(
                "audit personal scope must be complete or absent",
                sanitized_code="INVALID_AUDIT_SCOPE",
            )
        object.__setattr__(
            self, "payload_hash", _digest(self.payload_hash, "payload_hash")
        )
        object.__setattr__(
            self, "event_hash", _digest(self.event_hash, "event_hash")
        )
        object.__setattr__(
            self,
            "previous_event_hash",
            _optional_digest(self.previous_event_hash, "previous_event_hash"),
        )
        object.__setattr__(self, "metadata", _mapping(self.metadata, "metadata"))
        object.__setattr__(self, "occurred_at", _utc(self.occurred_at, "occurred_at"))
        if self.schema_version != "1.0.0":
            raise PersistenceConfigurationError(
                "unsupported audit schema version",
                sanitized_code="INVALID_SCHEMA_VERSION",
            )


def digest_canonical_request(value: Any) -> str:
    """Use the Kernel's established canonical serialization and SHA-256."""

    return canonical_sha256(value)
