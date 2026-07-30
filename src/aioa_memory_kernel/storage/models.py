"""Deterministic Step 7 snapshot identities and storage-only evidence."""

from __future__ import annotations

import base64
import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from aioa_memory_kernel.contracts.enums import StableStringEnum, StorageClass
from aioa_memory_kernel.contracts.serialization import (
    canonical_json_bytes,
    canonical_sha256,
    ensure_utc,
    freeze_json,
    require_sha256_hex,
    sha256_hex,
)

from .errors import SnapshotConfigurationError


SNAPSHOT_SCHEMA_VERSION = "1.0.0"
SNAPSHOT_SERIALIZATION_VERSION = "canonical-json-1a"
EXACT_BYTES_SERIALIZATION_VERSION = "exact-bytes-1a"
MAX_SNAPSHOT_BYTES = 64 * 1024 * 1024
STORAGE_EVIDENCE_ONLY = "STORAGE_EVIDENCE_ONLY"
_MEDIA_TYPE = re.compile(
    r"^[a-z0-9][a-z0-9!#$&^_.+-]{0,126}/"
    r"[a-z0-9][a-z0-9!#$&^_.+-]{0,126}$"
)


class S3ObjectLockMode(StableStringEnum):
    """The only retention mode supported by the bounded Step 7 adapter."""

    GOVERNANCE = "GOVERNANCE"


def _text(value: object, field_name: str, maximum: int = 255) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > maximum
    ):
        raise SnapshotConfigurationError(
            f"{field_name} must be a bounded canonical non-empty string",
            sanitized_code="INVALID_SNAPSHOT_VALUE",
        )
    if any(ord(character) < 32 for character in value):
        raise SnapshotConfigurationError(
            f"{field_name} contains a control character",
            sanitized_code="INVALID_SNAPSHOT_VALUE",
        )
    return value


def _digest(value: object, field_name: str) -> str:
    try:
        return require_sha256_hex(value, field_name)  # type: ignore[arg-type]
    except Exception as exc:
        raise SnapshotConfigurationError(
            f"{field_name} must be a lowercase SHA-256 digest",
            sanitized_code="INVALID_SNAPSHOT_DIGEST",
        ) from exc


def _timestamp(
    value: object,
    field_name: str,
    *,
    whole_seconds: bool = False,
) -> datetime:
    try:
        normalized = ensure_utc(value, field_name)  # type: ignore[arg-type]
    except Exception as exc:
        raise SnapshotConfigurationError(
            f"{field_name} must be timezone-aware",
            sanitized_code="INVALID_SNAPSHOT_TIMESTAMP",
        ) from exc
    if whole_seconds and normalized.microsecond:
        raise SnapshotConfigurationError(
            f"{field_name} must use whole-second precision",
            sanitized_code="INVALID_SNAPSHOT_TIMESTAMP",
        )
    return normalized


def _metadata(value: object, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or not all(
        isinstance(key, str) for key in value
    ):
        raise SnapshotConfigurationError(
            f"{field_name} must be a string-keyed mapping",
            sanitized_code="INVALID_SNAPSHOT_METADATA",
        )
    try:
        frozen = freeze_json(value)
        encoded = canonical_json_bytes(frozen)
    except Exception as exc:
        raise SnapshotConfigurationError(
            f"{field_name} is not canonical JSON data",
            sanitized_code="INVALID_SNAPSHOT_METADATA",
        ) from exc
    if len(encoded) > 16 * 1024:
        raise SnapshotConfigurationError(
            f"{field_name} exceeds the bounded metadata size",
            sanitized_code="UNBOUNDED_SNAPSHOT_METADATA",
        )
    assert isinstance(frozen, Mapping)
    return frozen


@dataclass(frozen=True, slots=True)
class SnapshotEnvelope:
    """Canonical payload plus deterministic identity and retention intent.

    The envelope is storage input only. Constructing or persisting it does not
    approve, publish, activate, or otherwise authorize Memory Patch state.
    """

    tenant_id: str
    source_id: str
    hat_scope_id: str
    payload: Any = field(repr=False, compare=False)
    serialization_version: str
    media_type: str
    captured_at: datetime
    retain_until: datetime
    retention_mode: S3ObjectLockMode
    authority_metadata: Mapping[str, Any]
    provenance_metadata: Mapping[str, Any]
    source_artifact_digest: str | None = None
    storage_class: StorageClass = StorageClass.S3_GLOBAL_LOCKED_SNAPSHOT
    schema_version: str = SNAPSHOT_SCHEMA_VERSION
    canonical_payload: bytes = field(init=False, repr=False)
    content_sha256: str = field(init=False)
    content_length: int = field(init=False)
    scope_digest: str = field(init=False)
    manifest_sha256: str = field(init=False)
    snapshot_id: str = field(init=False)

    def __post_init__(self) -> None:
        for field_name in ("tenant_id", "source_id", "hat_scope_id"):
            object.__setattr__(
                self,
                field_name,
                _text(getattr(self, field_name), field_name),
            )
        object.__setattr__(
            self,
            "serialization_version",
            _text(self.serialization_version, "serialization_version", 128),
        )
        if self.serialization_version not in {
            SNAPSHOT_SERIALIZATION_VERSION,
            EXACT_BYTES_SERIALIZATION_VERSION,
        }:
            raise SnapshotConfigurationError(
                "unsupported snapshot serialization version",
                sanitized_code="INVALID_SNAPSHOT_SERIALIZATION_VERSION",
            )
        if (
            not isinstance(self.media_type, str)
            or _MEDIA_TYPE.fullmatch(self.media_type) is None
        ):
            raise SnapshotConfigurationError(
                "media_type must be a canonical type/subtype value",
                sanitized_code="INVALID_SNAPSHOT_MEDIA_TYPE",
            )
        if (
            self.serialization_version == SNAPSHOT_SERIALIZATION_VERSION
            and self.media_type != "application/json"
        ):
            raise SnapshotConfigurationError(
                "canonical JSON snapshots require application/json",
                sanitized_code="SNAPSHOT_MEDIA_TYPE_MISMATCH",
            )
        if self.schema_version != SNAPSHOT_SCHEMA_VERSION:
            raise SnapshotConfigurationError(
                "unsupported snapshot schema version",
                sanitized_code="INVALID_SNAPSHOT_SCHEMA_VERSION",
            )
        if self.storage_class is not StorageClass.S3_GLOBAL_LOCKED_SNAPSHOT:
            raise SnapshotConfigurationError(
                "the Object Lock adapter accepts only global locked snapshots",
                sanitized_code="PRIVATE_SNAPSHOT_REQUIRES_SEPARATE_STORAGE",
            )
        if not isinstance(self.retention_mode, S3ObjectLockMode):
            raise SnapshotConfigurationError(
                "retention_mode must be S3ObjectLockMode.GOVERNANCE",
                sanitized_code="UNSUPPORTED_RETENTION_MODE",
            )
        captured_at = _timestamp(self.captured_at, "captured_at")
        retain_until = _timestamp(
            self.retain_until,
            "retain_until",
            whole_seconds=True,
        )
        if retain_until <= captured_at:
            raise SnapshotConfigurationError(
                "retain_until must be later than captured_at",
                sanitized_code="INVALID_RETENTION_WINDOW",
            )
        object.__setattr__(self, "captured_at", captured_at)
        object.__setattr__(self, "retain_until", retain_until)
        object.__setattr__(
            self,
            "authority_metadata",
            _metadata(self.authority_metadata, "authority_metadata"),
        )
        object.__setattr__(
            self,
            "provenance_metadata",
            _metadata(self.provenance_metadata, "provenance_metadata"),
        )
        if self.source_artifact_digest is not None:
            object.__setattr__(
                self,
                "source_artifact_digest",
                _digest(self.source_artifact_digest, "source_artifact_digest"),
            )
        if self.serialization_version == SNAPSHOT_SERIALIZATION_VERSION:
            try:
                frozen_payload = freeze_json(self.payload)
                canonical_payload = canonical_json_bytes(frozen_payload)
            except Exception as exc:
                raise SnapshotConfigurationError(
                    "snapshot payload is not repository-canonical JSON data",
                    sanitized_code="INVALID_CANONICAL_SNAPSHOT_PAYLOAD",
                ) from exc
        else:
            if not isinstance(self.payload, bytes):
                raise SnapshotConfigurationError(
                    "exact-byte snapshots require immutable bytes",
                    sanitized_code="INVALID_EXACT_BYTES_SNAPSHOT",
                )
            frozen_payload = self.payload
            canonical_payload = self.payload
        if len(canonical_payload) > MAX_SNAPSHOT_BYTES:
            raise SnapshotConfigurationError(
                "snapshot exceeds the bounded single-request adapter size",
                sanitized_code="SNAPSHOT_TOO_LARGE",
            )
        object.__setattr__(self, "payload", frozen_payload)
        object.__setattr__(self, "canonical_payload", canonical_payload)
        object.__setattr__(
            self,
            "content_sha256",
            sha256_hex(canonical_payload),
        )
        object.__setattr__(self, "content_length", len(canonical_payload))
        if (
            self.serialization_version == EXACT_BYTES_SERIALIZATION_VERSION
            and self.source_artifact_digest is not None
            and self.source_artifact_digest != self.content_sha256
        ):
            raise SnapshotConfigurationError(
                "exact bytes do not match the referenced Step 9 artifact digest",
                sanitized_code="SOURCE_ARTIFACT_DIGEST_MISMATCH",
            )
        scope_digest = canonical_sha256(
            {
                "tenant_id": self.tenant_id,
                "source_id": self.source_id,
                "hat_scope_id": self.hat_scope_id,
                "storage_class": self.storage_class,
            }
        )
        object.__setattr__(self, "scope_digest", scope_digest)
        manifest_sha256 = canonical_sha256(
            {
                "contract_type": "S3SnapshotIdentity",
                "schema_version": self.schema_version,
                "serialization_version": self.serialization_version,
                "media_type": self.media_type,
                "tenant_id": self.tenant_id,
                "source_id": self.source_id,
                "hat_scope_id": self.hat_scope_id,
                "storage_class": self.storage_class,
                "content_sha256": self.content_sha256,
                "content_length": self.content_length,
                "captured_at": self.captured_at,
                "retention_mode": self.retention_mode,
                "retain_until": self.retain_until,
                "authority_metadata": self.authority_metadata,
                "provenance_metadata": self.provenance_metadata,
                "source_artifact_digest": self.source_artifact_digest,
            }
        )
        object.__setattr__(self, "manifest_sha256", manifest_sha256)
        object.__setattr__(self, "snapshot_id", f"s3snap-{manifest_sha256}")

    @property
    def checksum_sha256_base64(self) -> str:
        """Return the S3 checksum header value, not an ETag surrogate."""

        return base64.b64encode(
            hashlib.sha256(self.canonical_payload).digest()
        ).decode("ascii")

    @property
    def object_suffix(self) -> str:
        """Return the deterministic representation suffix for object keys."""

        if self.serialization_version == SNAPSHOT_SERIALIZATION_VERSION:
            return "json"
        return "bin"


@dataclass(frozen=True, slots=True)
class BucketCapabilities:
    """Read-only proof of the bucket capabilities required by Step 7."""

    bucket_reference: str
    region: str
    versioning_status: str
    object_lock_enabled: bool
    default_retention_mode: S3ObjectLockMode
    default_retention_days: int


@dataclass(frozen=True, slots=True)
class SnapshotStorageEvidence:
    """Non-secret S3 evidence that grants no semantic authority."""

    snapshot_id: str
    canonical_sha256: str
    content_length: int
    bucket_reference: str
    object_key: str
    version_id: str
    retention_mode: S3ObjectLockMode
    retain_until: datetime
    checksum_sha256_base64: str
    metadata_verified: bool
    content_verified: bool
    idempotent_replay: bool
    authority_status: str = STORAGE_EVIDENCE_ONLY
    evidence_digest: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "snapshot_id", _text(self.snapshot_id, "snapshot_id")
        )
        object.__setattr__(
            self,
            "canonical_sha256",
            _digest(self.canonical_sha256, "canonical_sha256"),
        )
        if (
            not isinstance(self.content_length, int)
            or isinstance(self.content_length, bool)
            or self.content_length < 0
        ):
            raise SnapshotConfigurationError(
                "content_length must be a non-negative integer",
                sanitized_code="INVALID_SNAPSHOT_LENGTH",
            )
        for field_name, maximum in (
            ("bucket_reference", 96),
            ("object_key", 1024),
            ("version_id", 1024),
        ):
            object.__setattr__(
                self,
                field_name,
                _text(getattr(self, field_name), field_name, maximum),
            )
        if not isinstance(self.retention_mode, S3ObjectLockMode):
            raise SnapshotConfigurationError(
                "retention evidence has an unsupported mode",
                sanitized_code="UNSUPPORTED_RETENTION_MODE",
            )
        object.__setattr__(
            self,
            "retain_until",
            _timestamp(self.retain_until, "retain_until", whole_seconds=True),
        )
        object.__setattr__(
            self,
            "checksum_sha256_base64",
            _text(
                self.checksum_sha256_base64,
                "checksum_sha256_base64",
                128,
            ),
        )
        for field_name in (
            "metadata_verified",
            "content_verified",
            "idempotent_replay",
        ):
            if not isinstance(getattr(self, field_name), bool):
                raise SnapshotConfigurationError(
                    f"{field_name} must be a boolean",
                    sanitized_code="INVALID_STORAGE_EVIDENCE",
                )
        if self.authority_status != STORAGE_EVIDENCE_ONLY:
            raise SnapshotConfigurationError(
                "S3 evidence cannot claim semantic authority",
                sanitized_code="S3_SEMANTIC_AUTHORITY_FORBIDDEN",
            )
        expected = canonical_sha256(self, exclude_fields=("evidence_digest",))
        if self.evidence_digest:
            if _digest(self.evidence_digest, "evidence_digest") != expected:
                raise SnapshotConfigurationError(
                    "storage evidence digest mismatch",
                    sanitized_code="STORAGE_EVIDENCE_DIGEST_MISMATCH",
                )
        else:
            object.__setattr__(self, "evidence_digest", expected)


@dataclass(frozen=True, slots=True)
class RetrievedSnapshot:
    """Exact retrieved bytes paired with storage-only verification evidence."""

    payload: bytes = field(repr=False)
    evidence: SnapshotStorageEvidence

    def __post_init__(self) -> None:
        if not isinstance(self.payload, bytes):
            raise SnapshotConfigurationError(
                "retrieved payload must be bytes",
                sanitized_code="INVALID_RETRIEVED_PAYLOAD",
            )
        if not isinstance(self.evidence, SnapshotStorageEvidence):
            raise SnapshotConfigurationError(
                "retrieved snapshot requires typed storage evidence",
                sanitized_code="INVALID_STORAGE_EVIDENCE",
            )
