"""Immutable identities, receipts, and evidence for the Step 10 saga."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any, Mapping

from aioa_memory_kernel.contracts.enums import StableStringEnum
from aioa_memory_kernel.contracts.serialization import (
    canonical_json_bytes,
    canonical_sha256,
    ensure_utc,
    freeze_json,
    require_sha256_hex,
)
from aioa_memory_kernel.sources import PUBLICATION_GENESIS_DIGEST

from .errors import IngestionValidationError


INGESTION_SCHEMA_VERSION = "1.0.0"
INGESTION_CONTRACT_VERSION = "ingestion-saga-1a"
INGESTION_GENESIS_DIGEST = canonical_sha256(
    {
        "contract_type": "IngestionSagaGenesis",
        "contract_version": INGESTION_SCHEMA_VERSION,
        "marker": "INGESTION_SAGA_GENESIS_1A",
    }
)
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,254}$")
_CODE = re.compile(r"^[A-Z0-9][A-Z0-9_:-]{0,127}$")
_SAGA_ID = re.compile(r"^ingsaga-[0-9a-f]{64}$")
_EVENT_ID = re.compile(r"^ingevent-[0-9a-f]{64}$")
_EFFECT_ID = re.compile(r"^ingeffect-[0-9a-f]{64}$")
_ORPHAN_ID = re.compile(r"^ingorphan-[0-9a-f]{64}$")
_MEDIA_TYPE = re.compile(
    r"^[a-z0-9][a-z0-9!#$&^_.+-]{0,126}/"
    r"[a-z0-9][a-z0-9!#$&^_.+-]{0,126}$"
)


class SagaMilestone(StableStringEnum):
    REGISTERED = "REGISTERED"
    ACQUIRED_LOCAL = "ACQUIRED_LOCAL"
    HASH_VERIFIED = "HASH_VERIFIED"
    SNAPSHOT_UPLOAD_PENDING = "SNAPSHOT_UPLOAD_PENDING"
    SNAPSHOT_UPLOADED = "SNAPSHOT_UPLOADED"
    SNAPSHOT_LOCK_VERIFIED = "SNAPSHOT_LOCK_VERIFIED"
    PARSED = "PARSED"
    VALIDATED = "VALIDATED"
    PUBLISHED = "PUBLISHED"


MILESTONE_ORDER = (
    SagaMilestone.REGISTERED,
    SagaMilestone.ACQUIRED_LOCAL,
    SagaMilestone.HASH_VERIFIED,
    SagaMilestone.SNAPSHOT_UPLOAD_PENDING,
    SagaMilestone.SNAPSHOT_UPLOADED,
    SagaMilestone.SNAPSHOT_LOCK_VERIFIED,
    SagaMilestone.PARSED,
    SagaMilestone.VALIDATED,
    SagaMilestone.PUBLISHED,
)


class SagaExecutionDisposition(StableStringEnum):
    READY = "READY"
    CLAIMED = "CLAIMED"
    RETRY_WAIT = "RETRY_WAIT"
    OPERATOR_REVIEW = "OPERATOR_REVIEW"
    QUARANTINED = "QUARANTINED"
    COMPLETED = "COMPLETED"


class ExternalEffectKind(StableStringEnum):
    ACQUISITION = "ACQUISITION"
    HASH_VERIFICATION = "HASH_VERIFICATION"
    S3_UPLOAD = "S3_UPLOAD"
    S3_LOCK_VERIFICATION = "S3_LOCK_VERIFICATION"
    PARSE = "PARSE"
    VALIDATION = "VALIDATION"
    PUBLICATION = "PUBLICATION"


class ExternalEffectStatus(StableStringEnum):
    INTENT_RECORDED = "INTENT_RECORDED"
    RECEIPT_RECORDED = "RECEIPT_RECORDED"


class OrphanBackend(StableStringEnum):
    EXTERNAL_VOLUME = "EXTERNAL_VOLUME"
    S3 = "S3"


class OrphanClassification(StableStringEnum):
    EXACT_EVIDENCE = "EXACT_EVIDENCE"
    EXACT_DUPLICATE = "EXACT_DUPLICATE"
    EXPECTED_ABSENT = "EXPECTED_ABSENT"
    DATABASE_RECEIPT_EXTERNAL_MISSING = "DATABASE_RECEIPT_EXTERNAL_MISSING"
    CONFLICTING_EXTERNAL_EVIDENCE = "CONFLICTING_EXTERNAL_EVIDENCE"
    AMBIGUOUS_EXTERNAL_EVIDENCE = "AMBIGUOUS_EXTERNAL_EVIDENCE"
    RETENTION_BLOCKED = "RETENTION_BLOCKED"


class OrphanResolution(StableStringEnum):
    UNRESOLVED = "UNRESOLVED"
    ATTACHED = "ATTACHED"
    DUPLICATE_RECORDED = "DUPLICATE_RECORDED"
    QUARANTINED = "QUARANTINED"
    OPERATOR_REVIEW = "OPERATOR_REVIEW"
    CLEANUP_ELIGIBLE_AFTER_POLICY = "CLEANUP_ELIGIBLE_AFTER_POLICY"


def _text(value: object, field_name: str, maximum: int = 255) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > maximum
        or any(ord(character) < 32 for character in value)
    ):
        raise IngestionValidationError(
            f"{field_name} must be a bounded canonical string",
            sanitized_code="INVALID_INGESTION_VALUE",
        )
    return value


def _identifier(value: object, field_name: str) -> str:
    text = _text(value, field_name)
    if _ID.fullmatch(text) is None:
        raise IngestionValidationError(
            f"{field_name} is not a canonical identifier",
            sanitized_code="INVALID_INGESTION_IDENTIFIER",
        )
    return text


def _optional_identifier(value: object | None, field_name: str) -> str | None:
    if value is None:
        return None
    return _identifier(value, field_name)


def _digest(value: object, field_name: str) -> str:
    try:
        return require_sha256_hex(value, field_name)  # type: ignore[arg-type]
    except Exception as exc:
        raise IngestionValidationError(
            f"{field_name} must be a lowercase SHA-256 digest",
            sanitized_code="INVALID_INGESTION_DIGEST",
        ) from exc


def _optional_digest(value: object | None, field_name: str) -> str | None:
    if value is None:
        return None
    return _digest(value, field_name)


def _timestamp(value: object, field_name: str) -> datetime:
    try:
        return ensure_utc(value, field_name)  # type: ignore[arg-type]
    except Exception as exc:
        raise IngestionValidationError(
            f"{field_name} must be timezone-aware",
            sanitized_code="INVALID_INGESTION_TIMESTAMP",
        ) from exc


def _optional_timestamp(
    value: object | None,
    field_name: str,
) -> datetime | None:
    if value is None:
        return None
    return _timestamp(value, field_name)


def _code(value: object, field_name: str) -> str:
    text = _text(value, field_name, 128)
    if _CODE.fullmatch(text) is None:
        raise IngestionValidationError(
            f"{field_name} is not a bounded reason code",
            sanitized_code="INVALID_INGESTION_REASON",
        )
    return text


def _codes(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, (tuple, list, set, frozenset)):
        raise IngestionValidationError(
            f"{field_name} must be a bounded collection",
            sanitized_code="INVALID_INGESTION_REASON",
        )
    ordered = tuple(sorted({_code(item, field_name) for item in value}))
    if len(ordered) > 32:
        raise IngestionValidationError(
            f"{field_name} contains too many values",
            sanitized_code="INVALID_INGESTION_REASON",
        )
    return ordered


def _mapping(value: object, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or not all(
        isinstance(key, str) for key in value
    ):
        raise IngestionValidationError(
            f"{field_name} must be a string-keyed mapping",
            sanitized_code="INVALID_INGESTION_METADATA",
        )
    try:
        frozen = freeze_json(value)
        encoded = canonical_json_bytes(frozen)
    except Exception as exc:
        raise IngestionValidationError(
            f"{field_name} must be canonical JSON",
            sanitized_code="INVALID_INGESTION_METADATA",
        ) from exc
    if len(encoded) > 32 * 1024:
        raise IngestionValidationError(
            f"{field_name} exceeds its bounded size",
            sanitized_code="UNBOUNDED_INGESTION_METADATA",
        )
    assert isinstance(frozen, Mapping)
    return frozen


def _relative_path(value: object) -> str:
    text = _text(value, "local_relative_path", 512)
    path = PurePosixPath(text)
    if (
        path.is_absolute()
        or str(path) != text
        or "\\" in text
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise IngestionValidationError(
            "local relative path is unsafe",
            sanitized_code="UNSAFE_INGESTION_RELATIVE_PATH",
        )
    return text


def _versioned_identity(
    name: object,
    version: object,
    contract_version: object,
    *,
    prefix: str,
) -> tuple[str, str, str]:
    normalized_name = _text(name, f"{prefix}_name", 128)
    normalized_version = _text(version, f"{prefix}_version", 128)
    normalized_contract = _text(
        contract_version,
        f"{prefix}_contract_version",
        128,
    )
    if (
        normalized_version.casefold() == "latest"
        or normalized_contract.casefold() == "latest"
    ):
        raise IngestionValidationError(
            "mutable component version aliases are forbidden",
            sanitized_code="MUTABLE_INGESTION_VERSION_ALIAS",
        )
    return normalized_name, normalized_version, normalized_contract


@dataclass(frozen=True, slots=True)
class IngestionSaga:
    tenant_id: str
    saga_id: str
    source_id: str
    hat_scope_id: str
    owner_user_id: str | None
    knowledge_version_id: str
    idempotency_key: str
    request_digest: str
    scope_digest: str
    source_registry_digest: str
    content_sha256: str
    content_length: int
    media_type: str
    local_relative_path: str
    snapshot_id: str
    captured_at: datetime
    retain_until: datetime
    current_milestone: SagaMilestone
    execution_disposition: SagaExecutionDisposition
    state_version: int
    attempt_count: int
    event_sequence: int
    current_event_digest: str
    next_retry_at: datetime | None
    claim_token_digest: str | None
    claimed_at: datetime | None
    claim_expires_at: datetime | None
    quarantine_reason: str | None
    created_at: datetime
    updated_at: datetime
    terminal_at: datetime | None
    run_digest: str = ""
    schema_version: str = INGESTION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for field_name in (
            "tenant_id",
            "source_id",
            "hat_scope_id",
            "knowledge_version_id",
        ):
            object.__setattr__(
                self,
                field_name,
                _identifier(getattr(self, field_name), field_name),
            )
        object.__setattr__(
            self,
            "owner_user_id",
            _optional_identifier(self.owner_user_id, "owner_user_id"),
        )
        object.__setattr__(
            self,
            "idempotency_key",
            _text(self.idempotency_key, "idempotency_key", 512),
        )
        for field_name in (
            "request_digest",
            "scope_digest",
            "source_registry_digest",
            "content_sha256",
            "current_event_digest",
        ):
            object.__setattr__(
                self,
                field_name,
                _digest(getattr(self, field_name), field_name),
            )
        if (
            not isinstance(self.content_length, int)
            or isinstance(self.content_length, bool)
            or not 0 <= self.content_length <= 64 * 1024 * 1024
        ):
            raise IngestionValidationError(
                "content_length is outside the bounded adapter size",
                sanitized_code="INVALID_INGESTION_LENGTH",
            )
        if (
            not isinstance(self.media_type, str)
            or _MEDIA_TYPE.fullmatch(self.media_type) is None
        ):
            raise IngestionValidationError(
                "media_type must be canonical",
                sanitized_code="INVALID_INGESTION_MEDIA_TYPE",
            )
        object.__setattr__(
            self,
            "local_relative_path",
            _relative_path(self.local_relative_path),
        )
        object.__setattr__(
            self,
            "snapshot_id",
            _text(self.snapshot_id, "snapshot_id", 255),
        )
        object.__setattr__(
            self,
            "captured_at",
            _timestamp(self.captured_at, "captured_at"),
        )
        object.__setattr__(
            self,
            "retain_until",
            _timestamp(self.retain_until, "retain_until"),
        )
        if self.retain_until <= self.captured_at:
            raise IngestionValidationError(
                "retain_until must follow captured_at",
                sanitized_code="INVALID_INGESTION_RETENTION",
            )
        if not isinstance(self.current_milestone, SagaMilestone):
            raise IngestionValidationError(
                "current_milestone has the wrong type",
                sanitized_code="INVALID_INGESTION_MILESTONE",
            )
        if not isinstance(
            self.execution_disposition,
            SagaExecutionDisposition,
        ):
            raise IngestionValidationError(
                "execution_disposition has the wrong type",
                sanitized_code="INVALID_INGESTION_DISPOSITION",
            )
        for field_name, minimum in (
            ("state_version", 0),
            ("attempt_count", 0),
            ("event_sequence", 0),
        ):
            value = getattr(self, field_name)
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value < minimum
            ):
                raise IngestionValidationError(
                    f"{field_name} must be non-negative",
                    sanitized_code="INVALID_INGESTION_COUNTER",
                )
        object.__setattr__(
            self,
            "next_retry_at",
            _optional_timestamp(self.next_retry_at, "next_retry_at"),
        )
        object.__setattr__(
            self,
            "claim_token_digest",
            _optional_digest(self.claim_token_digest, "claim_token_digest"),
        )
        object.__setattr__(
            self,
            "claimed_at",
            _optional_timestamp(self.claimed_at, "claimed_at"),
        )
        object.__setattr__(
            self,
            "claim_expires_at",
            _optional_timestamp(self.claim_expires_at, "claim_expires_at"),
        )
        claim_values = (
            self.claim_token_digest,
            self.claimed_at,
            self.claim_expires_at,
        )
        if any(value is None for value in claim_values) != all(
            value is None for value in claim_values
        ):
            raise IngestionValidationError(
                "worker claim must be complete or absent",
                sanitized_code="PARTIAL_INGESTION_CLAIM",
            )
        if self.claimed_at is not None and (
            self.claim_expires_at is None
            or self.claim_expires_at <= self.claimed_at
        ):
            raise IngestionValidationError(
                "worker claim expiry must follow claim time",
                sanitized_code="INVALID_INGESTION_CLAIM",
            )
        quarantine_reason = (
            None
            if self.quarantine_reason is None
            else _code(self.quarantine_reason, "quarantine_reason")
        )
        object.__setattr__(self, "quarantine_reason", quarantine_reason)
        if (
            self.execution_disposition
            is SagaExecutionDisposition.QUARANTINED
        ) != (self.quarantine_reason is not None):
            raise IngestionValidationError(
                "quarantine disposition and reason must agree",
                sanitized_code="INVALID_INGESTION_QUARANTINE",
            )
        object.__setattr__(
            self,
            "created_at",
            _timestamp(self.created_at, "created_at"),
        )
        object.__setattr__(
            self,
            "updated_at",
            _timestamp(self.updated_at, "updated_at"),
        )
        object.__setattr__(
            self,
            "terminal_at",
            _optional_timestamp(self.terminal_at, "terminal_at"),
        )
        if self.updated_at < self.created_at:
            raise IngestionValidationError(
                "updated_at cannot precede created_at",
                sanitized_code="INVALID_INGESTION_TIME_ORDER",
            )
        terminal = self.current_milestone is SagaMilestone.PUBLISHED
        if terminal != (
            self.execution_disposition
            is SagaExecutionDisposition.COMPLETED
        ) or terminal != (self.terminal_at is not None):
            raise IngestionValidationError(
                "published milestone requires exact terminal disposition",
                sanitized_code="INVALID_INGESTION_TERMINAL_STATE",
            )
        if self.schema_version != INGESTION_SCHEMA_VERSION:
            raise IngestionValidationError(
                "unsupported ingestion schema version",
                sanitized_code="INVALID_INGESTION_SCHEMA_VERSION",
            )
        immutable = {
            "contract_type": "IngestionSagaRun",
            "contract_version": INGESTION_CONTRACT_VERSION,
            "tenant_id": self.tenant_id,
            "source_id": self.source_id,
            "hat_scope_id": self.hat_scope_id,
            "owner_user_id": self.owner_user_id,
            "knowledge_version_id": self.knowledge_version_id,
            "idempotency_key": self.idempotency_key,
            "request_digest": self.request_digest,
            "scope_digest": self.scope_digest,
            "source_registry_digest": self.source_registry_digest,
            "content_sha256": self.content_sha256,
            "content_length": self.content_length,
            "media_type": self.media_type,
            "local_relative_path": self.local_relative_path,
            "snapshot_id": self.snapshot_id,
            "captured_at": self.captured_at,
            "retain_until": self.retain_until,
        }
        expected_run_digest = canonical_sha256(immutable)
        if self.run_digest:
            if _digest(self.run_digest, "run_digest") != expected_run_digest:
                raise IngestionValidationError(
                    "run_digest differs from immutable saga facts",
                    sanitized_code="INGESTION_RUN_DIGEST_MISMATCH",
                )
        else:
            object.__setattr__(self, "run_digest", expected_run_digest)
        expected_saga_id = f"ingsaga-{expected_run_digest}"
        if (
            not isinstance(self.saga_id, str)
            or _SAGA_ID.fullmatch(self.saga_id) is None
            or self.saga_id != expected_saga_id
        ):
            raise IngestionValidationError(
                "saga_id differs from the deterministic run identity",
                sanitized_code="INGESTION_SAGA_ID_MISMATCH",
            )


@dataclass(frozen=True, slots=True)
class SagaTransitionEvent:
    tenant_id: str
    saga_id: str
    event_id: str
    sequence_number: int
    from_milestone: SagaMilestone
    to_milestone: SagaMilestone
    reason_code: str
    actor_boundary: str
    idempotency_reference: str
    prerequisite_receipt_digests: tuple[str, ...]
    previous_event_digest: str
    created_at: datetime
    event_digest: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "tenant_id",
            _identifier(self.tenant_id, "tenant_id"),
        )
        if _SAGA_ID.fullmatch(self.saga_id) is None:
            raise IngestionValidationError(
                "event saga_id is invalid",
                sanitized_code="INVALID_INGESTION_SAGA_ID",
            )
        if _EVENT_ID.fullmatch(self.event_id) is None:
            raise IngestionValidationError(
                "event_id is invalid",
                sanitized_code="INVALID_INGESTION_EVENT_ID",
            )
        if (
            not isinstance(self.sequence_number, int)
            or isinstance(self.sequence_number, bool)
            or self.sequence_number < 1
        ):
            raise IngestionValidationError(
                "event sequence must be positive",
                sanitized_code="INVALID_INGESTION_EVENT_SEQUENCE",
            )
        if not isinstance(
            self.from_milestone,
            SagaMilestone,
        ) or not isinstance(self.to_milestone, SagaMilestone):
            raise IngestionValidationError(
                "event milestones have the wrong type",
                sanitized_code="INVALID_INGESTION_MILESTONE",
            )
        object.__setattr__(
            self,
            "reason_code",
            _code(self.reason_code, "reason_code"),
        )
        object.__setattr__(
            self,
            "actor_boundary",
            _text(self.actor_boundary, "actor_boundary", 128),
        )
        object.__setattr__(
            self,
            "idempotency_reference",
            _text(
                self.idempotency_reference,
                "idempotency_reference",
                512,
            ),
        )
        if not isinstance(self.prerequisite_receipt_digests, tuple):
            raise IngestionValidationError(
                "prerequisite receipt digests must be an immutable tuple",
                sanitized_code="INVALID_INGESTION_PREREQUISITES",
            )
        ordered = tuple(
            sorted(
                {
                    _digest(value, "prerequisite_receipt_digest")
                    for value in self.prerequisite_receipt_digests
                }
            )
        )
        if ordered != self.prerequisite_receipt_digests:
            raise IngestionValidationError(
                "prerequisite digests must be unique and sorted",
                sanitized_code="INVALID_INGESTION_PREREQUISITES",
            )
        object.__setattr__(
            self,
            "previous_event_digest",
            _digest(self.previous_event_digest, "previous_event_digest"),
        )
        object.__setattr__(
            self,
            "created_at",
            _timestamp(self.created_at, "created_at"),
        )
        expected = canonical_sha256(self, exclude_fields=("event_digest",))
        if self.event_digest:
            if _digest(self.event_digest, "event_digest") != expected:
                raise IngestionValidationError(
                    "event_digest differs from canonical event facts",
                    sanitized_code="INGESTION_EVENT_DIGEST_MISMATCH",
                )
        else:
            object.__setattr__(self, "event_digest", expected)


@dataclass(frozen=True, slots=True)
class ExternalEffectIntent:
    tenant_id: str
    saga_id: str
    effect_kind: ExternalEffectKind
    deterministic_locator: str
    expected_snapshot_id: str
    expected_sha256: str
    expected_length: int
    created_at: datetime
    effect_id: str = ""
    intent_digest: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "tenant_id",
            _identifier(self.tenant_id, "tenant_id"),
        )
        if _SAGA_ID.fullmatch(self.saga_id) is None:
            raise IngestionValidationError(
                "effect saga_id is invalid",
                sanitized_code="INVALID_INGESTION_SAGA_ID",
            )
        if not isinstance(self.effect_kind, ExternalEffectKind):
            raise IngestionValidationError(
                "effect_kind has the wrong type",
                sanitized_code="INVALID_EXTERNAL_EFFECT_KIND",
            )
        locator = _text(
            self.deterministic_locator,
            "deterministic_locator",
            1024,
        )
        if locator.startswith("/") or "\\" in locator:
            raise IngestionValidationError(
                "external locator cannot expose a machine path",
                sanitized_code="UNSAFE_EXTERNAL_LOCATOR",
            )
        object.__setattr__(self, "deterministic_locator", locator)
        object.__setattr__(
            self,
            "expected_snapshot_id",
            _text(self.expected_snapshot_id, "expected_snapshot_id"),
        )
        object.__setattr__(
            self,
            "expected_sha256",
            _digest(self.expected_sha256, "expected_sha256"),
        )
        if (
            not isinstance(self.expected_length, int)
            or isinstance(self.expected_length, bool)
            or not 0 <= self.expected_length <= 64 * 1024 * 1024
        ):
            raise IngestionValidationError(
                "expected_length is invalid",
                sanitized_code="INVALID_INGESTION_LENGTH",
            )
        object.__setattr__(
            self,
            "created_at",
            _timestamp(self.created_at, "created_at"),
        )
        expected_digest = canonical_sha256(
            self,
            exclude_fields=("effect_id", "intent_digest"),
        )
        if self.intent_digest:
            if _digest(self.intent_digest, "intent_digest") != expected_digest:
                raise IngestionValidationError(
                    "intent digest mismatch",
                    sanitized_code="EXTERNAL_INTENT_DIGEST_MISMATCH",
                )
        else:
            object.__setattr__(self, "intent_digest", expected_digest)
        expected_id = f"ingeffect-{expected_digest}"
        if self.effect_id:
            if self.effect_id != expected_id:
                raise IngestionValidationError(
                    "effect_id differs from intent identity",
                    sanitized_code="EXTERNAL_EFFECT_ID_MISMATCH",
                )
        else:
            object.__setattr__(self, "effect_id", expected_id)


@dataclass(frozen=True, slots=True)
class ExternalEffectReceipt:
    tenant_id: str
    saga_id: str
    effect_id: str
    effect_kind: ExternalEffectKind
    intent_digest: str
    evidence_digest: str
    evidence: Mapping[str, Any]
    completed_at: datetime
    receipt_digest: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "tenant_id",
            _identifier(self.tenant_id, "tenant_id"),
        )
        if _SAGA_ID.fullmatch(self.saga_id) is None:
            raise IngestionValidationError(
                "receipt saga_id is invalid",
                sanitized_code="INVALID_INGESTION_SAGA_ID",
            )
        if _EFFECT_ID.fullmatch(self.effect_id) is None:
            raise IngestionValidationError(
                "receipt effect_id is invalid",
                sanitized_code="INVALID_EXTERNAL_EFFECT_ID",
            )
        if not isinstance(self.effect_kind, ExternalEffectKind):
            raise IngestionValidationError(
                "receipt effect kind has the wrong type",
                sanitized_code="INVALID_EXTERNAL_EFFECT_KIND",
            )
        for field_name in ("intent_digest", "evidence_digest"):
            object.__setattr__(
                self,
                field_name,
                _digest(getattr(self, field_name), field_name),
            )
        object.__setattr__(
            self,
            "evidence",
            _mapping(self.evidence, "receipt_evidence"),
        )
        if self.evidence_digest != canonical_sha256(self.evidence):
            raise IngestionValidationError(
                "evidence digest differs from canonical receipt evidence",
                sanitized_code="EXTERNAL_EVIDENCE_DIGEST_MISMATCH",
            )
        object.__setattr__(
            self,
            "completed_at",
            _timestamp(self.completed_at, "completed_at"),
        )
        expected = canonical_sha256(self, exclude_fields=("receipt_digest",))
        if self.receipt_digest:
            if _digest(self.receipt_digest, "receipt_digest") != expected:
                raise IngestionValidationError(
                    "receipt digest mismatch",
                    sanitized_code="EXTERNAL_RECEIPT_DIGEST_MISMATCH",
                )
        else:
            object.__setattr__(self, "receipt_digest", expected)


@dataclass(frozen=True, slots=True)
class ExternalEffectRecord:
    intent: ExternalEffectIntent
    status: ExternalEffectStatus
    receipt: ExternalEffectReceipt | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.intent, ExternalEffectIntent):
            raise IngestionValidationError(
                "external effect requires a typed intent",
                sanitized_code="INVALID_EXTERNAL_EFFECT",
            )
        if not isinstance(self.status, ExternalEffectStatus):
            raise IngestionValidationError(
                "external effect status has the wrong type",
                sanitized_code="INVALID_EXTERNAL_EFFECT",
            )
        if self.status is ExternalEffectStatus.INTENT_RECORDED:
            if self.receipt is not None:
                raise IngestionValidationError(
                    "intent-only effect cannot carry a receipt",
                    sanitized_code="INVALID_EXTERNAL_EFFECT",
                )
        elif self.receipt is None or (
            self.receipt.tenant_id,
            self.receipt.saga_id,
            self.receipt.effect_id,
            self.receipt.effect_kind,
            self.receipt.intent_digest,
        ) != (
            self.intent.tenant_id,
            self.intent.saga_id,
            self.intent.effect_id,
            self.intent.effect_kind,
            self.intent.intent_digest,
        ):
            raise IngestionValidationError(
                "external receipt differs from its durable intent",
                sanitized_code="EXTERNAL_RECEIPT_BINDING_MISMATCH",
            )


@dataclass(frozen=True, slots=True)
class ParseReceipt:
    tenant_id: str
    saga_id: str
    source_id: str
    snapshot_id: str
    s3_version_id: str
    input_sha256: str
    parser_name: str
    parser_version: str
    parser_contract_version: str
    output_artifact_digest: str
    completed_at: datetime
    synthetic_validation_boundary: bool = False
    receipt_digest: str = ""

    def __post_init__(self) -> None:
        for field_name in ("tenant_id", "source_id"):
            object.__setattr__(
                self,
                field_name,
                _identifier(getattr(self, field_name), field_name),
            )
        if _SAGA_ID.fullmatch(self.saga_id) is None:
            raise IngestionValidationError(
                "parse receipt saga_id is invalid",
                sanitized_code="INVALID_INGESTION_SAGA_ID",
            )
        object.__setattr__(
            self,
            "snapshot_id",
            _text(self.snapshot_id, "snapshot_id"),
        )
        object.__setattr__(
            self,
            "s3_version_id",
            _text(self.s3_version_id, "s3_version_id", 1024),
        )
        for field_name in ("input_sha256", "output_artifact_digest"):
            object.__setattr__(
                self,
                field_name,
                _digest(getattr(self, field_name), field_name),
            )
        name, version, contract = _versioned_identity(
            self.parser_name,
            self.parser_version,
            self.parser_contract_version,
            prefix="parser",
        )
        object.__setattr__(self, "parser_name", name)
        object.__setattr__(self, "parser_version", version)
        object.__setattr__(self, "parser_contract_version", contract)
        object.__setattr__(
            self,
            "completed_at",
            _timestamp(self.completed_at, "completed_at"),
        )
        if not isinstance(self.synthetic_validation_boundary, bool):
            raise IngestionValidationError(
                "synthetic validation marker must be a boolean",
                sanitized_code="INVALID_PARSE_RECEIPT",
            )
        expected = canonical_sha256(self, exclude_fields=("receipt_digest",))
        if self.receipt_digest:
            if _digest(self.receipt_digest, "receipt_digest") != expected:
                raise IngestionValidationError(
                    "parse receipt digest mismatch",
                    sanitized_code="PARSE_RECEIPT_DIGEST_MISMATCH",
                )
        else:
            object.__setattr__(self, "receipt_digest", expected)


@dataclass(frozen=True, slots=True)
class ValidationReceipt:
    tenant_id: str
    saga_id: str
    source_id: str
    snapshot_id: str
    parse_output_digest: str
    validator_name: str
    validator_version: str
    validator_contract_version: str
    accepted: bool
    reason_codes: tuple[str, ...]
    output_artifact_digest: str
    completed_at: datetime
    synthetic_validation_boundary: bool = False
    receipt_digest: str = ""

    def __post_init__(self) -> None:
        for field_name in ("tenant_id", "source_id"):
            object.__setattr__(
                self,
                field_name,
                _identifier(getattr(self, field_name), field_name),
            )
        if _SAGA_ID.fullmatch(self.saga_id) is None:
            raise IngestionValidationError(
                "validation receipt saga_id is invalid",
                sanitized_code="INVALID_INGESTION_SAGA_ID",
            )
        object.__setattr__(
            self,
            "snapshot_id",
            _text(self.snapshot_id, "snapshot_id"),
        )
        for field_name in (
            "parse_output_digest",
            "output_artifact_digest",
        ):
            object.__setattr__(
                self,
                field_name,
                _digest(getattr(self, field_name), field_name),
            )
        name, version, contract = _versioned_identity(
            self.validator_name,
            self.validator_version,
            self.validator_contract_version,
            prefix="validator",
        )
        object.__setattr__(self, "validator_name", name)
        object.__setattr__(self, "validator_version", version)
        object.__setattr__(self, "validator_contract_version", contract)
        if not isinstance(self.accepted, bool):
            raise IngestionValidationError(
                "validation accepted must be a boolean",
                sanitized_code="INVALID_VALIDATION_RECEIPT",
            )
        object.__setattr__(
            self,
            "reason_codes",
            _codes(self.reason_codes, "reason_codes"),
        )
        if self.accepted and self.reason_codes:
            raise IngestionValidationError(
                "accepted validation cannot carry blocking reasons",
                sanitized_code="INVALID_VALIDATION_RECEIPT",
            )
        if not self.accepted and not self.reason_codes:
            raise IngestionValidationError(
                "rejected validation requires a reason",
                sanitized_code="INVALID_VALIDATION_RECEIPT",
            )
        object.__setattr__(
            self,
            "completed_at",
            _timestamp(self.completed_at, "completed_at"),
        )
        if not isinstance(self.synthetic_validation_boundary, bool):
            raise IngestionValidationError(
                "synthetic validation marker must be a boolean",
                sanitized_code="INVALID_VALIDATION_RECEIPT",
            )
        expected = canonical_sha256(self, exclude_fields=("receipt_digest",))
        if self.receipt_digest:
            if _digest(self.receipt_digest, "receipt_digest") != expected:
                raise IngestionValidationError(
                    "validation receipt digest mismatch",
                    sanitized_code="VALIDATION_RECEIPT_DIGEST_MISMATCH",
                )
        else:
            object.__setattr__(self, "receipt_digest", expected)


@dataclass(frozen=True, slots=True)
class PublicationReceipt:
    tenant_id: str
    saga_id: str
    source_id: str
    snapshot_id: str
    source_registry_digest: str
    publication_event_id: str
    publication_event_digest: str
    publication_sequence: int
    completed_at: datetime
    receipt_digest: str = ""

    def __post_init__(self) -> None:
        for field_name in ("tenant_id", "source_id"):
            object.__setattr__(
                self,
                field_name,
                _identifier(getattr(self, field_name), field_name),
            )
        if _SAGA_ID.fullmatch(self.saga_id) is None:
            raise IngestionValidationError(
                "publication receipt saga_id is invalid",
                sanitized_code="INVALID_INGESTION_SAGA_ID",
            )
        for field_name in ("snapshot_id", "publication_event_id"):
            object.__setattr__(
                self,
                field_name,
                _text(getattr(self, field_name), field_name),
            )
        for field_name in (
            "source_registry_digest",
            "publication_event_digest",
        ):
            object.__setattr__(
                self,
                field_name,
                _digest(getattr(self, field_name), field_name),
            )
        if (
            not isinstance(self.publication_sequence, int)
            or isinstance(self.publication_sequence, bool)
            or self.publication_sequence < 1
        ):
            raise IngestionValidationError(
                "publication sequence must be positive",
                sanitized_code="INVALID_PUBLICATION_RECEIPT",
            )
        object.__setattr__(
            self,
            "completed_at",
            _timestamp(self.completed_at, "completed_at"),
        )
        expected = canonical_sha256(self, exclude_fields=("receipt_digest",))
        if self.receipt_digest:
            if _digest(self.receipt_digest, "receipt_digest") != expected:
                raise IngestionValidationError(
                    "publication receipt digest mismatch",
                    sanitized_code="PUBLICATION_RECEIPT_DIGEST_MISMATCH",
                )
        else:
            object.__setattr__(self, "receipt_digest", expected)


@dataclass(frozen=True, slots=True)
class OrphanRecord:
    tenant_id: str
    orphan_id: str
    saga_id: str | None
    backend: OrphanBackend
    deterministic_locator: str
    expected_snapshot_id: str
    observed_evidence_digest: str
    classification: OrphanClassification
    resolution: OrphanResolution
    reason_code: str
    retention_constraint: str
    cleanup_performed: bool
    created_at: datetime
    record_digest: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "tenant_id",
            _identifier(self.tenant_id, "tenant_id"),
        )
        if self.saga_id is not None and _SAGA_ID.fullmatch(self.saga_id) is None:
            raise IngestionValidationError(
                "orphan saga_id is invalid",
                sanitized_code="INVALID_INGESTION_SAGA_ID",
            )
        if not isinstance(self.backend, OrphanBackend):
            raise IngestionValidationError(
                "orphan backend has the wrong type",
                sanitized_code="INVALID_ORPHAN_RECORD",
            )
        locator = _text(
            self.deterministic_locator,
            "deterministic_locator",
            1024,
        )
        if locator.startswith("/") or "\\" in locator:
            raise IngestionValidationError(
                "orphan locator cannot expose a machine path",
                sanitized_code="UNSAFE_EXTERNAL_LOCATOR",
            )
        object.__setattr__(self, "deterministic_locator", locator)
        object.__setattr__(
            self,
            "expected_snapshot_id",
            _text(self.expected_snapshot_id, "expected_snapshot_id"),
        )
        object.__setattr__(
            self,
            "observed_evidence_digest",
            _digest(
                self.observed_evidence_digest,
                "observed_evidence_digest",
            ),
        )
        if not isinstance(
            self.classification,
            OrphanClassification,
        ) or not isinstance(self.resolution, OrphanResolution):
            raise IngestionValidationError(
                "orphan classification or resolution has the wrong type",
                sanitized_code="INVALID_ORPHAN_RECORD",
            )
        object.__setattr__(
            self,
            "reason_code",
            _code(self.reason_code, "reason_code"),
        )
        object.__setattr__(
            self,
            "retention_constraint",
            _code(self.retention_constraint, "retention_constraint"),
        )
        if self.cleanup_performed is not False:
            raise IngestionValidationError(
                "Step 10 orphan records cannot claim destructive cleanup",
                sanitized_code="DESTRUCTIVE_ORPHAN_CLEANUP_FORBIDDEN",
            )
        object.__setattr__(
            self,
            "created_at",
            _timestamp(self.created_at, "created_at"),
        )
        expected = canonical_sha256(
            self,
            exclude_fields=("orphan_id", "record_digest"),
        )
        if self.record_digest:
            if _digest(self.record_digest, "record_digest") != expected:
                raise IngestionValidationError(
                    "orphan record digest mismatch",
                    sanitized_code="ORPHAN_RECORD_DIGEST_MISMATCH",
                )
        else:
            object.__setattr__(self, "record_digest", expected)
        expected_id = f"ingorphan-{expected}"
        if self.orphan_id:
            if self.orphan_id != expected_id:
                raise IngestionValidationError(
                    "orphan_id differs from canonical evidence",
                    sanitized_code="ORPHAN_ID_MISMATCH",
                )
        else:
            object.__setattr__(self, "orphan_id", expected_id)


def build_initial_saga(
    *,
    tenant_id: str,
    source_id: str,
    hat_scope_id: str,
    owner_user_id: str | None,
    knowledge_version_id: str,
    idempotency_key: str,
    scope_digest: str,
    source_registry_digest: str,
    content_sha256: str,
    content_length: int,
    media_type: str,
    local_relative_path: str,
    snapshot_id: str,
    captured_at: datetime,
    retain_until: datetime,
    created_at: datetime,
) -> IngestionSaga:
    """Build exact genesis without permitting caller-selected random IDs."""

    request_digest = canonical_sha256(
        {
            "contract_type": "IngestionSagaRequest",
            "contract_version": INGESTION_CONTRACT_VERSION,
            "tenant_id": tenant_id,
            "source_id": source_id,
            "hat_scope_id": hat_scope_id,
            "owner_user_id": owner_user_id,
            "knowledge_version_id": knowledge_version_id,
            "idempotency_key": idempotency_key,
            "scope_digest": scope_digest,
            "source_registry_digest": source_registry_digest,
            "content_sha256": content_sha256,
            "content_length": content_length,
            "media_type": media_type,
            "local_relative_path": local_relative_path,
            "snapshot_id": snapshot_id,
            "captured_at": captured_at,
            "retain_until": retain_until,
            "source_publication_genesis_digest": PUBLICATION_GENESIS_DIGEST,
        }
    )
    provisional = {
        "contract_type": "IngestionSagaRun",
        "contract_version": INGESTION_CONTRACT_VERSION,
        "tenant_id": tenant_id,
        "source_id": source_id,
        "hat_scope_id": hat_scope_id,
        "owner_user_id": owner_user_id,
        "knowledge_version_id": knowledge_version_id,
        "idempotency_key": idempotency_key,
        "request_digest": request_digest,
        "scope_digest": scope_digest,
        "source_registry_digest": source_registry_digest,
        "content_sha256": content_sha256,
        "content_length": content_length,
        "media_type": media_type,
        "local_relative_path": local_relative_path,
        "snapshot_id": snapshot_id,
        "captured_at": captured_at,
        "retain_until": retain_until,
    }
    run_digest = canonical_sha256(provisional)
    return IngestionSaga(
        tenant_id=tenant_id,
        saga_id=f"ingsaga-{run_digest}",
        source_id=source_id,
        hat_scope_id=hat_scope_id,
        owner_user_id=owner_user_id,
        knowledge_version_id=knowledge_version_id,
        idempotency_key=idempotency_key,
        request_digest=request_digest,
        scope_digest=scope_digest,
        source_registry_digest=source_registry_digest,
        content_sha256=content_sha256,
        content_length=content_length,
        media_type=media_type,
        local_relative_path=local_relative_path,
        snapshot_id=snapshot_id,
        captured_at=captured_at,
        retain_until=retain_until,
        current_milestone=SagaMilestone.REGISTERED,
        execution_disposition=SagaExecutionDisposition.READY,
        state_version=0,
        attempt_count=0,
        event_sequence=0,
        current_event_digest=INGESTION_GENESIS_DIGEST,
        next_retry_at=None,
        claim_token_digest=None,
        claimed_at=None,
        claim_expires_at=None,
        quarantine_reason=None,
        created_at=created_at,
        updated_at=created_at,
        terminal_at=None,
        run_digest=run_digest,
    )
