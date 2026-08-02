"""Trusted German-law publication and corpus-verification boundary 1A.

The Step 16 boundary consumes the immutable Step 14 inventory and Step 15
normalization bundles.  It does not select a question, retrieve knowledge,
infer missing legal facts, or grant authority to a HAT or model.  For each
eligible official-consolidation version it binds two distinct immutable S3
objects: the raw GII ZIP and a deterministic, provenance-bound textual
projection.  The latter is an explicit derived representation for the Step 11
generic parser; it never claims that the parser natively understands ZIP.

The module has no import-time filesystem, database, AWS, network, HAT, or
model activity.  All I/O boundaries are injected by the runner.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
from concurrent.futures import Future, ThreadPoolExecutor
from collections import Counter, defaultdict
from contextlib import closing
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable, Iterator, Mapping, Protocol

from aioa_memory_kernel.contracts.serialization import (
    canonical_json_bytes,
    canonical_sha256,
    ensure_utc,
    require_sha256_hex,
    sha256_hex,
    to_canonical_data,
)
from aioa_memory_kernel.corpus import CorpusInventoryEngine, verify_inventory_bundle
from aioa_memory_kernel.parsing import (
    GenericParsingPipeline,
    LanguageTag,
    ParseArtifactValidator,
    ParsingRequest,
)
from aioa_memory_kernel.parsing.errors import ParsingError
from aioa_memory_kernel.storage import (
    EXACT_BYTES_SERIALIZATION_VERSION,
    S3ObjectLockMode,
    SnapshotEnvelope,
    SnapshotStorageEvidence,
)

from .corpus import STEP14_TENANT_ID
from .normalization import verify_temporal_jurisdiction_bundle


STEP16_PUBLICATION_SCHEMA_VERSION = "1.0.0"
STEP16_PUBLICATION_POLICY_VERSION = "german-law-publication-verification-1a.1"
STEP16_PROJECTION_ADAPTER_VERSION = "german-law-gii-textual-projection-1a"
STEP16_PROJECTION_TRANSFORMATION_VERSION = "german-law-gii-textual-projection-1a"
STEP16_PROVENANCE_VERSION = "german-law-publication-provenance-1a"
STEP16_BATCH_POLICY_VERSION = "german-law-publication-batch-1a"

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@+/-]{0,511}$")
_SAFE_CODE = re.compile(r"^[A-Z][A-Z0-9_:-]{0,127}$")


class Step16PublicationError(RuntimeError):
    """A sanitized trusted-publication failure."""

    def __init__(self, message: str, *, sanitized_code: str) -> None:
        super().__init__(message)
        self.sanitized_code = sanitized_code


class Step16PublicationSafetyError(Step16PublicationError):
    """A source, input, or output safety boundary failed closed."""


class Step16PublicationReplayConflictError(Step16PublicationError):
    """A checkpoint or deterministic output differs from immutable inputs."""


class Step16CandidateError(Step16PublicationError):
    """One candidate is safely classified without invalidating other inputs."""


class PublicationDisposition(str, Enum):
    ELIGIBLE = "ELIGIBLE"
    INELIGIBLE = "INELIGIBLE"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    QUARANTINED = "QUARANTINED"
    CONFLICTING = "CONFLICTING"
    UNSUPPORTED = "UNSUPPORTED"
    ALREADY_PUBLISHED_EXACT_REPLAY = "ALREADY_PUBLISHED_EXACT_REPLAY"


class PublicationBodyKind(str, Enum):
    VERIFIED_GII_TEXTUAL_PROJECTION = "VERIFIED_GII_TEXTUAL_PROJECTION"


def _text(value: object, field_name: str, maximum: int = 1024) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > maximum
        or "\x00" in value
        or any(ord(character) < 32 for character in value)
    ):
        raise Step16PublicationSafetyError(
            "publication field is not bounded canonical text",
            sanitized_code="INVALID_STEP16_TEXT",
        )
    return value


def _optional_text(value: object | None, field_name: str, maximum: int = 1024) -> str | None:
    return None if value is None else _text(value, field_name, maximum)


def _optional_declared_source_text(value: object | None, field_name: str, maximum: int = 1024) -> str | None:
    """Represent an explicitly empty structured source field as absent.

    GII-derived JSON records use empty strings for optional labels.  This is a
    source-declared absence, not whitespace normalization or source rewriting:
    the raw JSON remains independently hash-bound and the projection simply
    omits an absent label.  All non-empty values retain the strict text rules.
    """

    return None if value is None or value == "" else _text(value, field_name, maximum)


def _digest(value: object, field_name: str) -> str:
    try:
        return require_sha256_hex(value, field_name)  # type: ignore[arg-type]
    except Exception as exc:
        raise Step16PublicationSafetyError(
            "publication digest is malformed",
            sanitized_code="INVALID_STEP16_DIGEST",
        ) from exc


def _identifier(value: object, field_name: str, maximum: int = 512) -> str:
    result = _text(value, field_name, maximum)
    if _ID.fullmatch(result) is None:
        raise Step16PublicationSafetyError(
            "publication identifier is malformed",
            sanitized_code="INVALID_STEP16_IDENTIFIER",
        )
    return result


def _relative_path(value: object, field_name: str = "relative_path") -> str:
    result = _text(value, field_name, 4096)
    path = PurePosixPath(result)
    if (
        path.is_absolute()
        or str(path) != result
        or "\\" in result
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise Step16PublicationSafetyError(
            "source path escaped its approved root",
            sanitized_code="STEP16_PATH_ESCAPE",
        )
    return result


def _codes(values: Iterable[str]) -> tuple[str, ...]:
    output = tuple(sorted(set(values)))
    if any(_SAFE_CODE.fullmatch(value) is None for value in output):
        raise Step16PublicationSafetyError(
            "publication reason code is malformed",
            sanitized_code="INVALID_STEP16_REASON_CODE",
        )
    return output


def _strict_json(payload: bytes, *, field_name: str, maximum_bytes: int) -> Any:
    if not isinstance(payload, bytes) or not payload or len(payload) > maximum_bytes:
        raise Step16PublicationSafetyError(
            "JSON input violates its bounded contract",
            sanitized_code="STEP16_INPUT_JSON_SIZE_INVALID",
        )
    if b"\x00" in payload:
        raise Step16PublicationSafetyError(
            "JSON input contains a prohibited NUL",
            sanitized_code="STEP16_INPUT_JSON_NUL",
        )

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate member")
            result[key] = value
        return result

    def nonfinite(_: str) -> None:
        raise ValueError("non-finite number")

    try:
        return json.loads(
            payload.decode("utf-8", "strict"),
            object_pairs_hook=pairs,
            parse_constant=nonfinite,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise Step16PublicationSafetyError(
            f"{field_name} is not strict JSON",
            sanitized_code="STEP16_INPUT_JSON_INVALID",
        ) from exc


def _mapping(value: object, *, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise Step16PublicationSafetyError(
            "publication JSON value must be an object",
            sanitized_code="STEP16_INPUT_SCHEMA_INVALID",
        )
    return value


def _canonical_line(value: Mapping[str, Any]) -> str:
    return canonical_json_bytes(to_canonical_data(value)).decode("utf-8")


def _read_jsonl(path: Path, *, maximum_line_bytes: int = 4 * 1024 * 1024) -> Iterator[dict[str, Any]]:
    descriptor: int | None = None
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
        )
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise Step16PublicationSafetyError(
                "derived JSONL input is not a regular file",
                sanitized_code="STEP16_INPUT_ARTIFACT_UNSAFE",
            )
        with os.fdopen(descriptor, "rb", closefd=True) as handle:
            descriptor = None
            for line_number, line in enumerate(handle, start=1):
                if not line.endswith(b"\n") or len(line) > maximum_line_bytes:
                    raise Step16PublicationSafetyError(
                        "derived JSONL input is not bounded canonical JSONL",
                        sanitized_code="STEP16_INPUT_JSONL_INVALID",
                    )
                value = _mapping(
                    _strict_json(
                        line[:-1],
                        field_name=f"JSONL line {line_number}",
                        maximum_bytes=maximum_line_bytes,
                    ),
                    field_name=f"JSONL line {line_number}",
                )
                if _canonical_line(value).encode("utf-8") + b"\n" != line:
                    raise Step16PublicationSafetyError(
                        "derived JSONL input is not canonical",
                        sanitized_code="STEP16_INPUT_JSONL_NONCANONICAL",
                    )
                yield value
            after = os.fstat(handle.fileno())
    except Step16PublicationError:
        raise
    except OSError as exc:
        raise Step16PublicationSafetyError(
            "derived JSONL input is unavailable",
            sanitized_code="STEP16_INPUT_ARTIFACT_UNAVAILABLE",
        ) from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if (
        opened.st_dev,
        opened.st_ino,
        opened.st_size,
        opened.st_mtime_ns,
    ) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise Step16PublicationReplayConflictError(
            "derived JSONL changed while being read",
            sanitized_code="STEP16_INPUT_ARTIFACT_CHANGED",
        )


@dataclass(frozen=True, slots=True)
class Step16PublicationPolicy:
    """Fixed, bounded publication policy with no legal-fact inference."""

    schema_version: str = STEP16_PUBLICATION_SCHEMA_VERSION
    policy_version: str = STEP16_PUBLICATION_POLICY_VERSION
    projection_adapter_version: str = STEP16_PROJECTION_ADAPTER_VERSION
    retention_days: int = 14
    checkpoint_batch_size: int = 16
    max_snapshot_workers: int = 1
    maximum_metadata_bytes: int = 2 * 1024 * 1024
    maximum_provision_line_bytes: int = 2 * 1024 * 1024
    maximum_provisions_per_document: int = 100_000
    maximum_projection_bytes: int = 64 * 1024 * 1024
    maximum_candidates: int = 20_000

    def __post_init__(self) -> None:
        if self.schema_version != STEP16_PUBLICATION_SCHEMA_VERSION:
            raise ValueError("unsupported Step 16 publication schema")
        for name in ("policy_version", "projection_adapter_version"):
            object.__setattr__(self, name, _identifier(getattr(self, name), name, 255))
        for name, minimum, maximum in (
            ("retention_days", 7, 365),
            ("checkpoint_batch_size", 1, 512),
            ("max_snapshot_workers", 1, 4),
            ("maximum_metadata_bytes", 1024, 8 * 1024 * 1024),
            ("maximum_provision_line_bytes", 1024, 4 * 1024 * 1024),
            ("maximum_provisions_per_document", 1, 100_000),
            ("maximum_projection_bytes", 1024, 64 * 1024 * 1024),
            ("maximum_candidates", 1, 100_000),
        ):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
                raise ValueError(f"{name} is outside its fixed Step 16 bound")

    @property
    def policy_digest(self) -> str:
        # Worker count is an execution-resource bound, not a semantic
        # publication rule.  Keeping it outside the logical policy digest lets
        # a safely interrupted run resume serially or with the approved bounded
        # worker pool without changing its deterministic snapshot identities.
        return canonical_sha256(
            {
                "schema_version": self.schema_version,
                "policy_version": self.policy_version,
                "projection_adapter_version": self.projection_adapter_version,
                "retention_days": self.retention_days,
                "checkpoint_batch_size": self.checkpoint_batch_size,
                "maximum_metadata_bytes": self.maximum_metadata_bytes,
                "maximum_provision_line_bytes": self.maximum_provision_line_bytes,
                "maximum_provisions_per_document": self.maximum_provisions_per_document,
                "maximum_projection_bytes": self.maximum_projection_bytes,
                "maximum_candidates": self.maximum_candidates,
            }
        )


@dataclass(frozen=True, slots=True)
class Step16PublicationPlan:
    run_id: str
    source_root_identity_digest: str
    source_tree_digest: str
    step14_manifest_digest: str
    step15_manifest_digest: str
    step15_logical_output_digest: str
    inventory_object_count: int
    normalized_version_count: int
    candidate_count: int
    static_eligible_precheck_count: int
    eligible_precheck_count: int
    projection_preflight_reason_counts: tuple[tuple[str, int], ...]
    projection_preflight_digest: str
    estimated_output_bytes: int
    policy_digest: str
    plan_digest: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", _identifier(self.run_id, "run_id", 255))
        for name in (
            "source_root_identity_digest",
            "source_tree_digest",
            "step14_manifest_digest",
            "step15_manifest_digest",
            "step15_logical_output_digest",
            "projection_preflight_digest",
            "policy_digest",
            "plan_digest",
        ):
            object.__setattr__(self, name, _digest(getattr(self, name), name))
        for name in (
            "inventory_object_count",
            "normalized_version_count",
            "candidate_count",
            "static_eligible_precheck_count",
            "eligible_precheck_count",
            "estimated_output_bytes",
        ):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{name} must be non-negative")
        reasons = self.projection_preflight_reason_counts
        if not isinstance(reasons, tuple):
            raise ValueError("projection_preflight_reason_counts must be a tuple")
        normalized_reasons: list[tuple[str, int]] = []
        for value in reasons:
            if not isinstance(value, tuple) or len(value) != 2:
                raise ValueError("projection_preflight_reason_counts contains an invalid item")
            code, count = value
            code = _text(code, "projection preflight reason", 128)
            if _SAFE_CODE.fullmatch(code) is None:
                raise ValueError("projection_preflight_reason_counts contains an invalid code")
            if not isinstance(count, int) or isinstance(count, bool) or count <= 0:
                raise ValueError("projection_preflight_reason_counts contains an invalid count")
            normalized_reasons.append((code, count))
        if tuple(normalized_reasons) != tuple(sorted(normalized_reasons)):
            raise ValueError("projection_preflight_reason_counts must be canonically sorted")
        if len({code for code, _count in normalized_reasons}) != len(normalized_reasons):
            raise ValueError("projection_preflight_reason_counts must not repeat codes")


@dataclass(frozen=True, slots=True)
class Step16PublicationResult:
    """Verified result of one fixed-input Step 16 publication run."""

    run_id: str
    summary: Mapping[str, Any]
    manifest: Mapping[str, Any]
    verification: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", _identifier(self.run_id, "run_id", 255))
        for name in ("summary", "manifest", "verification"):
            value = getattr(self, name)
            if not isinstance(value, Mapping):
                raise ValueError(f"{name} must be a mapping")


@dataclass(frozen=True, slots=True)
class PublicationInput:
    """One Step 14/15-bound prospective body-bearing legal version."""

    inventory_record_id: str
    source_registry_candidate_id: str
    source_id: str
    hat_scope_id: str
    source_class: str
    authority_level: str
    license_status: str
    access_class: str
    redaction_state: str
    document_identity: str | None
    version_identity: str | None
    official_identifier: str | None
    version_status: str | None
    consolidation_status: str | None
    version_normalization_status: str
    jurisdiction_normalization_status: str | None
    normalized_jurisdiction: str | None
    temporal_facts_digest: str | None
    law_record_relative_path: str
    law_record_sha256: str
    provisions_relative_path: str
    provisions_sha256: str
    raw_source_relative_path: str
    raw_source_sha256: str
    raw_source_length: int
    candidate_digest: str
    alias_inventory_record_ids: tuple[str, ...] = ()
    alias_law_record_relative_paths: tuple[str, ...] = ()
    alias_provisions_relative_paths: tuple[str, ...] = ()
    alias_raw_source_relative_paths: tuple[str, ...] = ()
    alias_metadata_conflict: bool = False
    input_digest: str = field(init=False)

    def __post_init__(self) -> None:
        for name in (
            "inventory_record_id",
            "source_registry_candidate_id",
            "source_id",
            "hat_scope_id",
            "source_class",
            "authority_level",
            "license_status",
            "access_class",
            "redaction_state",
            "version_normalization_status",
        ):
            object.__setattr__(self, name, _identifier(getattr(self, name), name, 512))
        for name in ("document_identity", "version_identity", "official_identifier", "version_status", "consolidation_status", "jurisdiction_normalization_status", "normalized_jurisdiction"):
            object.__setattr__(self, name, _optional_text(getattr(self, name), name, 512))
        for name in ("law_record_relative_path", "provisions_relative_path", "raw_source_relative_path"):
            object.__setattr__(self, name, _relative_path(getattr(self, name), name))
        for name in ("law_record_sha256", "provisions_sha256", "raw_source_sha256", "candidate_digest"):
            object.__setattr__(self, name, _digest(getattr(self, name), name))
        object.__setattr__(self, "temporal_facts_digest", _optional_text(self.temporal_facts_digest, "temporal_facts_digest", 64))
        if self.temporal_facts_digest is not None:
            object.__setattr__(self, "temporal_facts_digest", _digest(self.temporal_facts_digest, "temporal_facts_digest"))
        if not isinstance(self.raw_source_length, int) or isinstance(self.raw_source_length, bool) or self.raw_source_length < 0:
            raise ValueError("raw_source_length must be non-negative")
        aliases = tuple(sorted({_identifier(value, "alias_inventory_record_id", 512) for value in (self.alias_inventory_record_ids or (self.inventory_record_id,))}))
        if self.inventory_record_id not in aliases:
            raise ValueError("publication aliases must include the canonical inventory record")
        object.__setattr__(self, "alias_inventory_record_ids", aliases)
        alias_paths = tuple(sorted({_relative_path(value, "alias_law_record_relative_path") for value in (self.alias_law_record_relative_paths or (self.law_record_relative_path,))}))
        if self.law_record_relative_path not in alias_paths:
            raise ValueError("publication alias paths must include the canonical law record")
        object.__setattr__(self, "alias_law_record_relative_paths", alias_paths)
        alias_provisions = tuple(sorted({_relative_path(value, "alias_provisions_relative_path") for value in (self.alias_provisions_relative_paths or (self.provisions_relative_path,))}))
        if self.provisions_relative_path not in alias_provisions:
            raise ValueError("publication alias paths must include the canonical provisions record")
        object.__setattr__(self, "alias_provisions_relative_paths", alias_provisions)
        alias_raw = tuple(sorted({_relative_path(value, "alias_raw_source_relative_path") for value in (self.alias_raw_source_relative_paths or (self.raw_source_relative_path,))}))
        if self.raw_source_relative_path not in alias_raw:
            raise ValueError("publication alias paths must include the canonical raw record")
        object.__setattr__(self, "alias_raw_source_relative_paths", alias_raw)
        if not isinstance(self.alias_metadata_conflict, bool):
            raise ValueError("alias_metadata_conflict must be boolean")
        object.__setattr__(self, "input_digest", canonical_sha256(self, exclude_fields=("input_digest",)))


@dataclass(frozen=True, slots=True)
class PublicationDecision:
    """One deterministic eligibility or exclusion decision."""

    version_identity: str | None
    document_identity: str | None
    inventory_record_id: str
    source_registry_candidate_id: str
    disposition: PublicationDisposition
    reason_codes: tuple[str, ...]
    input_digest: str
    raw_source_sha256: str
    decision_digest: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "version_identity", _optional_text(self.version_identity, "version_identity", 512))
        object.__setattr__(self, "document_identity", _optional_text(self.document_identity, "document_identity", 512))
        for name in ("inventory_record_id", "source_registry_candidate_id"):
            object.__setattr__(self, name, _identifier(getattr(self, name), name, 512))
        if not isinstance(self.disposition, PublicationDisposition):
            raise ValueError("publication disposition must be typed")
        object.__setattr__(self, "reason_codes", _codes(self.reason_codes))
        if self.disposition is PublicationDisposition.ELIGIBLE and self.reason_codes:
            raise ValueError("eligible decision cannot carry exclusion reasons")
        if self.disposition is not PublicationDisposition.ELIGIBLE and not self.reason_codes:
            raise ValueError("non-eligible decision must preserve a reason")
        for name in ("input_digest", "raw_source_sha256"):
            object.__setattr__(self, name, _digest(getattr(self, name), name))
        object.__setattr__(self, "decision_digest", canonical_sha256(self, exclude_fields=("decision_digest",)))


@dataclass(frozen=True, slots=True)
class SnapshotBinding:
    """Non-semantic immutable storage evidence for one representation."""

    binding_kind: str
    version_identity: str
    source_registry_candidate_id: str
    snapshot_id: str
    source_artifact_sha256: str
    content_sha256: str
    content_length: int
    serialization_version: str
    media_type: str
    scope_digest: str
    snapshot_manifest_sha256: str
    captured_at: datetime
    bucket_reference: str
    object_key: str
    version_id: str
    retention_mode: str
    retain_until: datetime
    evidence_digest: str
    idempotent_replay: bool
    binding_id: str = field(init=False)
    binding_digest: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "binding_kind", _identifier(self.binding_kind, "binding_kind", 128))
        for name in ("version_identity", "source_registry_candidate_id", "snapshot_id"):
            object.__setattr__(self, name, _identifier(getattr(self, name), name, 512))
        for name in (
            "source_artifact_sha256",
            "content_sha256",
            "scope_digest",
            "snapshot_manifest_sha256",
            "evidence_digest",
        ):
            object.__setattr__(self, name, _digest(getattr(self, name), name))
        if not isinstance(self.content_length, int) or isinstance(self.content_length, bool) or self.content_length < 0:
            raise ValueError("content_length must be non-negative")
        for name in (
            "serialization_version",
            "media_type",
            "bucket_reference",
            "object_key",
            "version_id",
            "retention_mode",
        ):
            object.__setattr__(self, name, _text(getattr(self, name), name, 1024))
        if self.serialization_version != EXACT_BYTES_SERIALIZATION_VERSION:
            raise ValueError("Step 16 snapshot serialization must be exact bytes")
        if self.media_type not in {"application/zip", "text/plain"}:
            raise ValueError("Step 16 snapshot media type is unsupported")
        object.__setattr__(self, "captured_at", ensure_utc(self.captured_at, "captured_at"))
        object.__setattr__(self, "retain_until", ensure_utc(self.retain_until, "retain_until"))
        if self.retain_until <= self.captured_at:
            raise ValueError("snapshot retention must follow capture")
        if not isinstance(self.idempotent_replay, bool):
            raise ValueError("idempotent_replay must be boolean")
        identity = canonical_sha256(
            {
                "binding_kind": self.binding_kind,
                "version_identity": self.version_identity,
                "snapshot_id": self.snapshot_id,
                "content_sha256": self.content_sha256,
            }
        )
        object.__setattr__(self, "binding_id", f"publication-snapshot-{identity}")
        object.__setattr__(self, "binding_digest", canonical_sha256(self, exclude_fields=("binding_digest",)))


class SnapshotWriter(Protocol):
    """The existing Step 7 adapter remains the only S3 writer."""

    def __call__(self, snapshot: SnapshotEnvelope) -> SnapshotStorageEvidence: ...


@dataclass(frozen=True, slots=True)
class _CandidateOutcome:
    """One candidate result computed before deterministic spool persistence.

    When a bounded worker pool is used, workers perform only source reads,
    exact snapshot calls through the injected Step 7 writer, and Step 11
    parsing.  SQLite checkpoint and output ordering stay on the owning thread.
    """

    decision: PublicationDecision
    temporal: Mapping[str, Any]
    jurisdiction: Mapping[str, Any]
    raw: Mapping[str, Any] | None
    projection: Mapping[str, Any] | None
    parser: Mapping[str, Any] | None
    chunk: Mapping[str, Any] | None
    provenance: Mapping[str, Any] | None
    publication_item: Mapping[str, Any] | None
    exclusion: Mapping[str, Any] | None
    published: bool


def _record_data(value: object) -> dict[str, Any]:
    data = to_canonical_data(value)
    if not isinstance(data, dict):
        raise TypeError("Step 16 record serialization must be an object")
    return data


class GermanLawPublicationEngine:
    """Stream a fixed inventory/normalization snapshot into Step 16 evidence."""

    _OUTPUT_JSONL = (
        "publication-eligibility.jsonl",
        "publication-items.jsonl",
        "publication-exclusions.jsonl",
        "snapshot-bindings.jsonl",
        "provenance-chains.jsonl",
        "parser-coverage.jsonl",
        "chunk-coverage.jsonl",
        "temporal-validation.jsonl",
        "jurisdiction-validation.jsonl",
        "publication-conflicts.jsonl",
    )

    def __init__(
        self,
        *,
        source_root: Path,
        step14_bundle_root: Path,
        step15_bundle_root: Path,
        bundle_parent: Path,
        device_reference: str,
        starting_head: str,
        policy: Step16PublicationPolicy | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        progress: Callable[[Mapping[str, int]], None] | None = None,
    ) -> None:
        self.source_root = source_root
        self.step14_bundle_root = step14_bundle_root
        self.step15_bundle_root = step15_bundle_root
        self.bundle_parent = bundle_parent
        self.device_reference = _text(device_reference, "device_reference", 255)
        self.starting_head = _text(starting_head, "starting_head", 128)
        self.policy = policy or Step16PublicationPolicy()
        if not callable(clock):
            raise TypeError("clock must be callable")
        self.clock = clock
        self.progress = progress or (lambda _values: None)
        self._root_stat = self._verify_roots()
        self.source_root_identity_digest = canonical_sha256(
            {
                "device_reference": self.device_reference,
                "root_device": self._root_stat.st_dev,
                "root_inode": self._root_stat.st_ino,
                "root_name_digest": sha256_hex(self.source_root.name),
            }
        )
        self._step14_manifest = self._load_step14_manifest()
        self._step15_manifest = self._load_step15_manifest()
        self.step14_manifest_digest = _digest(self._step14_manifest["manifest_digest"], "step14_manifest_digest")
        self.step15_manifest_digest = _digest(self._step15_manifest["manifest_digest"], "step15_manifest_digest")
        self._source_tree_digest: str | None = None
        self._input_cache: tuple[PublicationInput, ...] | None = None
        self._input_context: dict[str, Any] | None = None
        self.run_id: str | None = None
        self.bundle_root: Path | None = None

    def _verify_roots(self) -> os.stat_result:
        try:
            if self.source_root.resolve(strict=True) != self.source_root:
                raise Step16PublicationSafetyError("source root has a symlinked component", sanitized_code="STEP16_SOURCE_ROOT_UNSAFE")
            source = self.source_root.lstat()
            for path, code in (
                (self.step14_bundle_root, "STEP16_STEP14_BUNDLE_UNSAFE"),
                (self.step15_bundle_root, "STEP16_STEP15_BUNDLE_UNSAFE"),
            ):
                if path.resolve(strict=True) != path or path.is_symlink() or not stat.S_ISDIR(path.lstat().st_mode):
                    raise Step16PublicationSafetyError("Step 16 input bundle is unsafe", sanitized_code=code)
            destination = self.bundle_parent if os.path.lexists(self.bundle_parent) else self.bundle_parent.parent
            if destination.resolve(strict=True) != destination:
                raise Step16PublicationSafetyError("Step 16 output parent is unsafe", sanitized_code="STEP16_OUTPUT_ROOT_UNSAFE")
            output = destination.lstat()
        except Step16PublicationError:
            raise
        except OSError as exc:
            raise Step16PublicationSafetyError("Step 16 roots are unavailable", sanitized_code="STEP16_ROOT_UNAVAILABLE") from exc
        if self.source_root.is_symlink() or not stat.S_ISDIR(source.st_mode):
            raise Step16PublicationSafetyError("source root is unsafe", sanitized_code="STEP16_SOURCE_ROOT_UNSAFE")
        if destination.is_symlink() or not stat.S_ISDIR(output.st_mode):
            raise Step16PublicationSafetyError("output root is unsafe", sanitized_code="STEP16_OUTPUT_ROOT_UNSAFE")
        if source.st_dev != output.st_dev or source.st_dev != self.step14_bundle_root.lstat().st_dev or source.st_dev != self.step15_bundle_root.lstat().st_dev:
            raise Step16PublicationSafetyError("Step 16 crosses an external filesystem boundary", sanitized_code="STEP16_EXTERNAL_FILESYSTEM_BOUNDARY_MISMATCH")
        return source

    @staticmethod
    def _read_bounded_regular(path: Path, *, maximum_bytes: int) -> bytes:
        descriptor: int | None = None
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0))
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode) or opened.st_size < 0 or opened.st_size > maximum_bytes:
                raise Step16PublicationSafetyError("input artifact is unsafe", sanitized_code="STEP16_INPUT_ARTIFACT_UNSAFE")
            payload = bytearray()
            while len(payload) < opened.st_size:
                block = os.read(descriptor, min(1024 * 1024, opened.st_size - len(payload)))
                if not block:
                    break
                payload.extend(block)
            after = os.fstat(descriptor)
            if len(payload) != opened.st_size or (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
                raise Step16PublicationReplayConflictError("input artifact changed while being read", sanitized_code="STEP16_INPUT_ARTIFACT_CHANGED")
            return bytes(payload)
        except Step16PublicationError:
            raise
        except OSError as exc:
            raise Step16PublicationSafetyError("input artifact is unavailable", sanitized_code="STEP16_INPUT_ARTIFACT_UNAVAILABLE") from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def _load_step14_manifest(self) -> dict[str, Any]:
        verification = verify_inventory_bundle(self.step14_bundle_root)
        if verification.get("status") != "PASS":
            raise Step16PublicationReplayConflictError("Step 14 bundle did not verify", sanitized_code="STEP16_STEP14_BUNDLE_INVALID")
        manifest = _mapping(
            _strict_json(
                self._read_bounded_regular(self.step14_bundle_root / "inventory-manifest.json", maximum_bytes=4 * 1024 * 1024),
                field_name="Step 14 manifest",
                maximum_bytes=4 * 1024 * 1024,
            ),
            field_name="Step 14 manifest",
        )
        if manifest.get("manifest_digest") != canonical_sha256(manifest, exclude_fields=("manifest_digest",)):
            raise Step16PublicationReplayConflictError("Step 14 manifest digest mismatches its content", sanitized_code="STEP16_STEP14_DIGEST_MISMATCH")
        required = {
            "file-records.jsonl",
            "source-registration-candidates.jsonl",
            "exact-duplicate-groups.jsonl",
            "near-duplicate-candidates.jsonl",
            "quarantine-candidates.jsonl",
            "inventory-summary.json",
        }
        generated = {entry[0] for entry in manifest.get("generated_files", ()) if isinstance(entry, list) and len(entry) == 3 and isinstance(entry[0], str)}
        if not required.issubset(generated):
            raise Step16PublicationSafetyError("Step 14 bundle lacks a required input", sanitized_code="STEP16_STEP14_BUNDLE_INCOMPLETE")
        return manifest

    def _load_step15_manifest(self) -> dict[str, Any]:
        verification = verify_temporal_jurisdiction_bundle(self.step15_bundle_root)
        if verification.get("status") != "PASS":
            raise Step16PublicationReplayConflictError("Step 15 bundle did not verify", sanitized_code="STEP16_STEP15_BUNDLE_INVALID")
        manifest = _mapping(
            _strict_json(
                self._read_bounded_regular(self.step15_bundle_root / "artifact-manifest.json", maximum_bytes=4 * 1024 * 1024),
                field_name="Step 15 manifest",
                maximum_bytes=4 * 1024 * 1024,
            ),
            field_name="Step 15 manifest",
        )
        if manifest.get("manifest_digest") != canonical_sha256(manifest, exclude_fields=("manifest_digest",)):
            raise Step16PublicationReplayConflictError("Step 15 manifest digest mismatches its content", sanitized_code="STEP16_STEP15_DIGEST_MISMATCH")
        run = manifest.get("run")
        if not isinstance(run, Mapping) or run.get("step14_manifest_digest") != self._step14_manifest.get("manifest_digest"):
            raise Step16PublicationReplayConflictError("Step 15 is not bound to the accepted Step 14 manifest", sanitized_code="STEP16_MIXED_INPUT_RUNS")
        required = {
            "temporal-normalization.jsonl",
            "jurisdiction-normalization.jsonl",
            "document-versions.jsonl",
            "normalization-conflicts.jsonl",
            "source-registry-normalization-proposals.jsonl",
            "run-summary.json",
        }
        generated = {entry[0] for entry in manifest.get("generated_files", ()) if isinstance(entry, list) and len(entry) == 3 and isinstance(entry[0], str)}
        if not required.issubset(generated):
            raise Step16PublicationSafetyError("Step 15 bundle lacks a required input", sanitized_code="STEP16_STEP15_BUNDLE_INCOMPLETE")
        return manifest

    def _tree_fingerprint(self) -> tuple[str, Mapping[str, int]]:
        probe = CorpusInventoryEngine(
            source_root=self.source_root,
            bundle_parent=self.step14_bundle_root.parent,
            device_reference=self.device_reference,
            starting_head=self.starting_head,
        )
        return probe.tree_fingerprint()

    def _load_input_context(self) -> tuple[tuple[PublicationInput, ...], dict[str, Any]]:
        if self._input_cache is not None and self._input_context is not None:
            return self._input_cache, self._input_context
        files_by_id: dict[str, dict[str, Any]] = {}
        files_by_relative: dict[str, dict[str, Any]] = {}
        files_by_raw: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for record in _read_jsonl(self.step14_bundle_root / "file-records.jsonl"):
            record_id = _identifier(record.get("record_id"), "inventory record id", 512)
            relative = _relative_path(record.get("relative_path"))
            files_by_id[record_id] = record
            files_by_relative[relative] = record
            raw_sha256 = record.get("raw_sha256")
            if isinstance(raw_sha256, str) and _SHA256.fullmatch(raw_sha256):
                files_by_raw[raw_sha256].append(record)
        candidates: dict[str, dict[str, Any]] = {}
        for candidate in _read_jsonl(self.step14_bundle_root / "source-registration-candidates.jsonl"):
            candidate_id = _identifier(candidate.get("candidate_id"), "candidate_id", 512)
            if candidate_id in candidates:
                raise Step16PublicationReplayConflictError("duplicate Step 14 candidate identity", sanitized_code="STEP16_CANDIDATE_IDENTITY_CONFLICT")
            candidates[candidate_id] = candidate
        versions: dict[str, dict[str, Any]] = {}
        for version in _read_jsonl(self.step15_bundle_root / "document-versions.jsonl"):
            inventory_record_id = _identifier(version.get("inventory_record_id"), "version inventory record id", 512)
            if inventory_record_id in versions:
                raise Step16PublicationReplayConflictError("duplicate Step 15 version mapping", sanitized_code="STEP16_VERSION_IDENTITY_CONFLICT")
            versions[inventory_record_id] = version
        jurisdictions: dict[str, dict[str, Any]] = {}
        for jurisdiction in _read_jsonl(self.step15_bundle_root / "jurisdiction-normalization.jsonl"):
            inventory_record_id = _identifier(jurisdiction.get("inventory_record_id"), "jurisdiction inventory record id", 512)
            if inventory_record_id in jurisdictions:
                raise Step16PublicationReplayConflictError("duplicate Step 15 jurisdiction mapping", sanitized_code="STEP16_JURISDICTION_IDENTITY_CONFLICT")
            jurisdictions[inventory_record_id] = jurisdiction
        temporal_by_record: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for fact in _read_jsonl(self.step15_bundle_root / "temporal-normalization.jsonl"):
            temporal_by_record[_identifier(fact.get("inventory_record_id"), "temporal inventory record id", 512)].append(fact)
        conflict_records: set[str] = set()
        conflicts: list[dict[str, Any]] = []
        for conflict in _read_jsonl(self.step15_bundle_root / "normalization-conflicts.jsonl"):
            conflicts.append(conflict)
            for record_id in conflict.get("involved_inventory_record_ids", ()):
                if isinstance(record_id, str):
                    conflict_records.add(record_id)

        inputs: list[PublicationInput] = []
        for inventory_record_id, version in sorted(versions.items(), key=lambda item: str(item[1].get("version_identity") or item[0])):
            law_record = files_by_id.get(inventory_record_id)
            if law_record is None:
                raise Step16PublicationReplayConflictError("Step 15 version has no Step 14 inventory record", sanitized_code="STEP16_VERSION_INVENTORY_LINK_MISSING")
            relative = _relative_path(law_record.get("relative_path"))
            if not relative.endswith("/law_record.json"):
                raise Step16PublicationReplayConflictError("Step 15 version does not bind law metadata", sanitized_code="STEP16_VERSION_INPUT_KIND_INVALID")
            candidate_ids = version.get("source_registry_candidate_ids")
            if not isinstance(candidate_ids, list) or len(candidate_ids) != 1:
                raise Step16PublicationReplayConflictError("Step 15 version must bind one Step 14 candidate", sanitized_code="STEP16_SOURCE_CANDIDATE_LINK_INVALID")
            candidate_id = _identifier(candidate_ids[0], "source_registry_candidate_id", 512)
            candidate = candidates.get(candidate_id)
            if candidate is None:
                raise Step16PublicationReplayConflictError("Step 15 source candidate is missing", sanitized_code="STEP16_SOURCE_CANDIDATE_LINK_MISSING")
            jurisdiction = jurisdictions.get(inventory_record_id)
            if jurisdiction is None:
                raise Step16PublicationReplayConflictError("Step 15 jurisdiction mapping is missing", sanitized_code="STEP16_JURISDICTION_LINK_MISSING")
            raw_source_sha256 = _digest(candidate.get("content_sha256"), "candidate content sha256")
            candidates_for_raw = sorted(
                (
                    item
                    for item in files_by_raw.get(raw_source_sha256, ())
                    if item.get("file_kind") == "REGULAR"
                    and item.get("source_family_candidate") == "GESETZE_IM_INTERNET_OFFICIAL_CONSOLIDATED_RAW"
                ),
                key=lambda item: str(item.get("relative_path")),
            )
            if not candidates_for_raw:
                raise Step16PublicationReplayConflictError("Step 14 candidate lacks its raw GII ZIP", sanitized_code="STEP16_RAW_SOURCE_LINK_MISSING")
            raw_record = candidates_for_raw[0]
            raw_length = raw_record.get("byte_size")
            if not isinstance(raw_length, int) or isinstance(raw_length, bool) or raw_length < 0:
                raise Step16PublicationReplayConflictError("raw GII ZIP lacks a stable byte length", sanitized_code="STEP16_RAW_SOURCE_LENGTH_INVALID")
            provisions_relative = str(PurePosixPath(relative).parent / "provisions.jsonl")
            provisions_record = files_by_relative.get(provisions_relative)
            if provisions_record is None:
                raise Step16PublicationReplayConflictError("law metadata has no Step 14 provisions record", sanitized_code="STEP16_PROVISIONS_LINK_MISSING")
            inputs.append(
                PublicationInput(
                    inventory_record_id=inventory_record_id,
                    source_registry_candidate_id=candidate_id,
                    source_id=_identifier(candidate.get("logical_source_candidate_id"), "source_id", 512),
                    hat_scope_id=_identifier(candidate.get("hat_scope_id"), "hat_scope_id", 512),
                    source_class=_identifier(candidate.get("source_class"), "source_class", 512),
                    authority_level=_identifier(candidate.get("authority_level"), "authority_level", 128),
                    license_status=_identifier(candidate.get("license_status"), "license_status", 128),
                    access_class=_identifier(candidate.get("access_class"), "access_class", 128),
                    redaction_state=_identifier(candidate.get("redaction_state"), "redaction_state", 128),
                    document_identity=_optional_text(version.get("document_identity"), "document_identity", 512),
                    version_identity=_optional_text(version.get("version_identity"), "version_identity", 512),
                    official_identifier=_optional_text(version.get("official_identifier"), "official_identifier", 512),
                    version_status=_optional_text(version.get("version_status"), "version_status", 512),
                    consolidation_status=_optional_text(version.get("consolidation_status"), "consolidation_status", 512),
                    version_normalization_status=_identifier(version.get("status"), "version_normalization_status", 128),
                    jurisdiction_normalization_status=_optional_text(jurisdiction.get("normalization_status"), "jurisdiction_normalization_status", 128),
                    normalized_jurisdiction=_optional_text(jurisdiction.get("normalized_jurisdiction"), "normalized_jurisdiction", 128),
                    temporal_facts_digest=_optional_text(version.get("temporal_facts_digest"), "temporal_facts_digest", 64),
                    law_record_relative_path=relative,
                    law_record_sha256=_digest(law_record.get("raw_sha256"), "law_record_sha256"),
                    provisions_relative_path=provisions_relative,
                    provisions_sha256=_digest(provisions_record.get("raw_sha256"), "provisions_sha256"),
                    raw_source_relative_path=_relative_path(raw_record.get("relative_path"), "raw_source_relative_path"),
                    raw_source_sha256=raw_source_sha256,
                    raw_source_length=raw_length,
                    candidate_digest=_digest(candidate.get("candidate_digest"), "candidate_digest"),
                )
            )
        inputs = list(self._collapse_version_aliases(inputs))
        if not inputs or len(inputs) > self.policy.maximum_candidates:
            raise Step16PublicationSafetyError("Step 16 candidate cardinality is outside its bounded policy", sanitized_code="STEP16_CANDIDATE_COUNT_INVALID")
        inputs = sorted(inputs, key=lambda item: (item.version_identity or "", item.inventory_record_id))
        context = {
            "files_by_relative": files_by_relative,
            "temporal_by_record": temporal_by_record,
            "conflict_records": conflict_records,
            "conflicts": conflicts,
            "inventory_summary": _mapping(
                _strict_json(
                    self._read_bounded_regular(self.step14_bundle_root / "inventory-summary.json", maximum_bytes=4 * 1024 * 1024),
                    field_name="Step 14 summary",
                    maximum_bytes=4 * 1024 * 1024,
                ),
                field_name="Step 14 summary",
            ),
            "step15_version_record_count": len(versions),
            "version_alias_record_count": len(versions) - len(inputs),
        }
        self._input_cache, self._input_context = tuple(inputs), context
        return self._input_cache, context

    @staticmethod
    def _alias_material_signature(item: PublicationInput) -> tuple[object, ...]:
        """Return the fields that must agree before alias paths may share a version.

        A matching legal-version identifier is not itself permission to select a
        winner.  Only byte-identical derived metadata plus matching Step 14/15
        policy facts may be represented as provenance aliases.  Any difference
        becomes one deterministic, non-publishable version conflict.
        """

        return (
            item.source_registry_candidate_id,
            item.source_id,
            item.hat_scope_id,
            item.source_class,
            item.authority_level,
            item.license_status,
            item.access_class,
            item.redaction_state,
            item.document_identity,
            item.version_identity,
            item.official_identifier,
            item.version_status,
            item.consolidation_status,
            item.version_normalization_status,
            item.jurisdiction_normalization_status,
            item.normalized_jurisdiction,
            item.temporal_facts_digest,
            item.law_record_sha256,
            item.provisions_sha256,
            item.raw_source_sha256,
            item.raw_source_length,
            item.candidate_digest,
        )

    @classmethod
    def _collapse_version_aliases(cls, inputs: Iterable[PublicationInput]) -> tuple[PublicationInput, ...]:
        """Preserve every path while preventing duplicate version publication.

        Step 14 correctly inventories all package copies.  Step 15 can therefore
        contain more than one record for one legal version.  Step 16 keeps every
        alias in its evidence, but only collapses byte- and metadata-equivalent
        aliases.  Divergent copies are retained as a single conflicting candidate
        rather than failing the entire corpus run or silently publishing one.
        """

        grouped: dict[str, list[PublicationInput]] = defaultdict(list)
        for item in inputs:
            grouped[item.version_identity or f"inventory-{item.inventory_record_id}"].append(item)
        collapsed: list[PublicationInput] = []
        for key in sorted(grouped):
            members = sorted(grouped[key], key=lambda value: value.inventory_record_id)
            canonical = members[0]
            collapsed.append(
                replace(
                    canonical,
                    alias_inventory_record_ids=tuple(value.inventory_record_id for value in members),
                    alias_law_record_relative_paths=tuple(value.law_record_relative_path for value in members),
                    alias_provisions_relative_paths=tuple(value.provisions_relative_path for value in members),
                    alias_raw_source_relative_paths=tuple(value.raw_source_relative_path for value in members),
                    alias_metadata_conflict=len({cls._alias_material_signature(value) for value in members}) != 1,
                )
            )
        return tuple(collapsed)

    def plan(self) -> Step16PublicationPlan:
        source_tree_digest, counts = self._tree_fingerprint()
        step14_run = self._step14_manifest.get("run")
        step15_run = self._step15_manifest.get("run")
        if not isinstance(step14_run, Mapping) or not isinstance(step15_run, Mapping):
            raise Step16PublicationSafetyError("Step 14 or Step 15 run contract is invalid", sanitized_code="STEP16_INPUT_RUN_SCHEMA_INVALID")
        if source_tree_digest != step14_run.get("source_tree_after_digest") or source_tree_digest != step15_run.get("source_tree_digest"):
            raise Step16PublicationReplayConflictError("source corpus differs from the accepted Step 14/15 input snapshot", sanitized_code="STEP16_SOURCE_TREE_CHANGED")
        if self.source_root_identity_digest != step14_run.get("source_root_identity_digest") or self.source_root_identity_digest != step15_run.get("source_root_identity_digest"):
            raise Step16PublicationReplayConflictError("source root identity differs from the accepted Step 14/15 input", sanitized_code="STEP16_SOURCE_ROOT_IDENTITY_MISMATCH")
        inputs, context = self._load_input_context()
        static_eligible = tuple(item for item in inputs if not self._static_reasons(item, context))
        precheck, preflight_reasons = self._projection_preflight(static_eligible)
        preflight_reason_counts = tuple(sorted(preflight_reasons.items()))
        preflight_digest = canonical_sha256(
            {
                "eligible_candidate_count": precheck,
                "reason_counts": dict(preflight_reason_counts),
                "projection_adapter_version": STEP16_PROJECTION_ADAPTER_VERSION,
            }
        )
        logical = _digest(self._step15_manifest.get("logical_output_digest"), "step15_logical_output_digest")
        facts = {
            "source_root_identity_digest": self.source_root_identity_digest,
            "source_tree_digest": source_tree_digest,
            "step14_manifest_digest": self.step14_manifest_digest,
            "step15_manifest_digest": self.step15_manifest_digest,
            "step15_logical_output_digest": logical,
            "inventory_object_count": int(context["inventory_summary"].get("objects_observed", 0)),
            "normalized_version_count": int(context["step15_version_record_count"]),
            "candidate_count": len(inputs),
            "static_eligible_precheck_count": len(static_eligible),
            "eligible_precheck_count": precheck,
            "projection_preflight_reason_counts": preflight_reason_counts,
            "projection_preflight_digest": preflight_digest,
            "estimated_output_bytes": max(4 * 1024 * 1024, len(inputs) * 8 * 1024),
            "policy_digest": self.policy.policy_digest,
        }
        run_id = "step16-" + canonical_sha256(
            {
                "inputs": {
                    key: value
                    for key, value in facts.items()
                    if key not in {"estimated_output_bytes"}
                },
                "contract": STEP16_PUBLICATION_SCHEMA_VERSION,
            }
        )[:32]
        plan_digest = canonical_sha256({"run_id": run_id, **facts})
        self._source_tree_digest = source_tree_digest
        self.run_id = run_id
        self.bundle_root = self.bundle_parent / run_id
        return Step16PublicationPlan(run_id=run_id, plan_digest=plan_digest, **facts)

    def _projection_preflight(self, inputs: Iterable[PublicationInput]) -> tuple[int, Counter[str]]:
        """Read every statically eligible projection before the S3 write gate.

        This intentionally repeats a bounded, read-only source pass.  It
        converts only a candidate-local malformed projection into a terminal
        exclusion reason, exactly as ``_process_candidates`` does.  Any
        source-mutation, digest, path, or external-boundary failure remains a
        whole-run failure.  The resulting count gives the operator an exact
        snapshot-write upper bound instead of treating static metadata alone as
        proof that a body can be safely published.
        """

        eligible = 0
        reasons: Counter[str] = Counter()
        for ordinal, item in enumerate(inputs, start=1):
            try:
                payload, _metadata = self._projection_payload(item)
                # Do not retain projections while planning.  A plan is a
                # bounded streaming read and must not accumulate corpus text.
                del payload
            except Step16CandidateError as exc:
                reasons[_text(exc.sanitized_code, "projection preflight code", 128)] += 1
            except Step16PublicationSafetyError as exc:
                if exc.sanitized_code != "INVALID_STEP16_TEXT":
                    raise
                reasons["PROJECTION_METADATA_INVALID"] += 1
            else:
                eligible += 1
            if ordinal % self.policy.checkpoint_batch_size == 0:
                self.progress(
                    {
                        "phase": "projection_preflight",
                        "candidates_examined": ordinal,
                        "eligible": eligible,
                        "excluded": sum(reasons.values()),
                    }
                )
        return eligible, reasons

    def _static_reasons(self, item: PublicationInput, context: Mapping[str, Any]) -> tuple[str, ...]:
        reasons: list[str] = []
        if item.document_identity is None:
            reasons.append("DOCUMENT_IDENTITY_MISSING")
        if item.version_identity is None:
            reasons.append("VERSION_IDENTITY_MISSING")
        if item.version_normalization_status != "CLEAR":
            reasons.append("STEP15_VERSION_REVIEW_REQUIRED")
        if item.jurisdiction_normalization_status != "CLEAR" or item.normalized_jurisdiction != "DE_FEDERAL":
            reasons.append("JURISDICTION_UNRESOLVED_OR_CONFLICTING")
        if item.source_class != "DE_FEDERAL_OFFICIAL_CONSOLIDATED_LAW":
            reasons.append("UNSUPPORTED_GERMAN_LAW_SOURCE_CLASS")
        if item.authority_level != "AUTHORITATIVE_SECONDARY":
            reasons.append("SOURCE_AUTHORITY_POLICY_MISMATCH")
        if item.license_status != "CONFIRMED_PERMISSIVE":
            reasons.append("RIGHTS_NOT_PUBLICATION_COMPATIBLE")
        if item.access_class != "PUBLIC" or item.redaction_state != "NOT_REQUIRED":
            reasons.append("PRIVACY_OR_REDACTION_BLOCK")
        if item.temporal_facts_digest is None:
            reasons.append("TEMPORAL_FACTS_MISSING")
        if item.alias_metadata_conflict:
            reasons.append("DUPLICATE_VERSION_METADATA_CONFLICT")
        if item.inventory_record_id in context["conflict_records"]:
            reasons.append("MATERIAL_OR_REVIEW_NORMALIZATION_CONFLICT")
        return _codes(reasons)

    @staticmethod
    def _safe_open_under_root(root: Path, relative_path: str) -> tuple[int, os.stat_result]:
        root_fd: int | None = None
        current_fd: int | None = None
        file_fd: int | None = None
        try:
            root_fd = os.open(root, os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0))
            current_fd = root_fd
            parts = PurePosixPath(relative_path).parts
            for part in parts[:-1]:
                next_fd = os.open(part, os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0), dir_fd=current_fd)
                if current_fd != root_fd:
                    os.close(current_fd)
                current_fd = next_fd
            file_fd = os.open(parts[-1], os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0), dir_fd=current_fd)
            metadata = os.fstat(file_fd)
            if not stat.S_ISREG(metadata.st_mode):
                raise Step16PublicationSafetyError("source object is not a regular file", sanitized_code="STEP16_SOURCE_OBJECT_UNSAFE")
            if current_fd != root_fd:
                os.close(current_fd)
                current_fd = None
            os.close(root_fd)
            root_fd = None
            return file_fd, metadata
        except Step16PublicationError:
            if file_fd is not None:
                os.close(file_fd)
            raise
        except OSError as exc:
            if file_fd is not None:
                os.close(file_fd)
            raise Step16PublicationSafetyError("source object is unavailable", sanitized_code="STEP16_SOURCE_OBJECT_UNAVAILABLE") from exc
        finally:
            if current_fd is not None and current_fd != root_fd:
                try:
                    os.close(current_fd)
                except OSError:
                    pass
            if root_fd is not None:
                try:
                    os.close(root_fd)
                except OSError:
                    pass

    def _read_verified_source(self, relative_path: str, expected_sha256: str, expected_length: int, *, maximum_length: int) -> bytes:
        descriptor, opened = self._safe_open_under_root(self.source_root, relative_path)
        try:
            if opened.st_size != expected_length or opened.st_size > maximum_length:
                raise Step16PublicationReplayConflictError("source object differs from the accepted inventory size", sanitized_code="STEP16_SOURCE_BYTES_MISMATCH")
            digest = hashlib.sha256()
            payload = bytearray()
            while True:
                block = os.read(descriptor, min(1024 * 1024, maximum_length - len(payload) + 1))
                if not block:
                    break
                payload.extend(block)
                if len(payload) > maximum_length:
                    raise Step16PublicationSafetyError("source object exceeds the Step 16 byte limit", sanitized_code="STEP16_SOURCE_OBJECT_TOO_LARGE")
                digest.update(block)
            after = os.fstat(descriptor)
            if (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
                raise Step16PublicationReplayConflictError("source object changed during read", sanitized_code="STEP16_SOURCE_MUTATION_DURING_READ")
            if len(payload) != expected_length or digest.hexdigest() != expected_sha256:
                raise Step16PublicationReplayConflictError("source bytes differ from the accepted inventory digest", sanitized_code="STEP16_SOURCE_BYTES_MISMATCH")
            return bytes(payload)
        finally:
            os.close(descriptor)

    @staticmethod
    def _package_relative_raw_path(law_record_relative_path: str, raw_source_relative_path: str) -> str | None:
        """Resolve a package-relative raw path without accepting path hints.

        The recovered GII package records raw paths relative to its package
        root, while the Step 14 inventory correctly records corpus-root
        relative paths.  A comparison is valid only when both paths share the
        exact prefix before a known normalized-corpus segment.  This does not
        follow a path, inspect a filename as authority, or allow traversal.
        """

        law_parts = PurePosixPath(_relative_path(law_record_relative_path, "law_record_relative_path")).parts
        raw_parts = PurePosixPath(_relative_path(raw_source_relative_path, "raw_source_relative_path")).parts
        markers = {"20_BULK_NORMALIZED_CORPUS", "06_PILOT_NORMALIZED_CORPUS"}
        indexes = [index for index, part in enumerate(law_parts) if part in markers]
        if len(indexes) != 1:
            return None
        package_prefix = law_parts[: indexes[0]]
        if not package_prefix or len(raw_parts) <= len(package_prefix) or raw_parts[: len(package_prefix)] != package_prefix:
            return None
        return PurePosixPath(*raw_parts[len(package_prefix) :]).as_posix()

    def _projection_payload(self, item: PublicationInput) -> tuple[bytes, dict[str, Any]]:
        law_payload = self._read_verified_source(
            item.law_record_relative_path,
            item.law_record_sha256,
            int(self._input_context["files_by_relative"][item.law_record_relative_path]["byte_size"]),  # type: ignore[index]
            maximum_length=self.policy.maximum_metadata_bytes,
        )
        metadata = _mapping(
            _strict_json(law_payload, field_name="law_record", maximum_bytes=self.policy.maximum_metadata_bytes),
            field_name="law_record",
        )
        metadata_source_sha = _digest(metadata.get("source_sha256"), "law_record source_sha256")
        if metadata_source_sha != item.raw_source_sha256:
            raise Step16PublicationReplayConflictError("law metadata is not bound to the candidate raw source", sanitized_code="STEP16_METADATA_SOURCE_DIGEST_MISMATCH")
        declared_raw_path = _relative_path(metadata.get("raw_source_path"), "raw_source_path")
        package_relative_raw_path = self._package_relative_raw_path(item.law_record_relative_path, item.raw_source_relative_path)
        if declared_raw_path not in {item.raw_source_relative_path, package_relative_raw_path}:
            raise Step16CandidateError("law metadata raw path differs from Step 14 input", sanitized_code="METADATA_RAW_PATH_MISMATCH")
        if _optional_text(metadata.get("jurisdiction_layer"), "jurisdiction_layer", 128) != item.normalized_jurisdiction:
            raise Step16CandidateError("structured law metadata jurisdiction conflicts", sanitized_code="JURISDICTION_CONFLICT")
        official_title = _optional_declared_source_text(metadata.get("official_title"), "official_title", 4096)
        record_identifier = _optional_declared_source_text(metadata.get("record_id"), "record_id", 512)
        document_family = _optional_declared_source_text(metadata.get("document_family_id"), "document_family_id", 512)
        header = [
            "[MEMORY_PATCH_GERMAN_LAW_TEXTUAL_PROJECTION_1A]",
            f"document_identity: {item.document_identity}",
            f"version_identity: {item.version_identity}",
            f"source_raw_sha256: {item.raw_source_sha256}",
            f"source_class: {item.source_class}",
            "consolidation_status: OFFICIAL_CONSOLIDATED_REFERENCE_NOT_AUTHENTIC_PROMULGATION",
        ]
        if item.official_identifier is not None:
            header.append(f"official_identifier: {item.official_identifier}")
        if record_identifier is not None:
            header.append(f"record_id: {record_identifier}")
        if document_family is not None:
            header.append(f"document_family_id: {document_family}")
        if official_title is not None:
            header.append(f"official_title: {official_title}")
        pieces = ["\n".join(header), ""]
        descriptor, opened = self._safe_open_under_root(self.source_root, item.provisions_relative_path)
        digest = hashlib.sha256()
        line_count = 0
        text_count = 0
        try:
            with os.fdopen(descriptor, "rb", closefd=True) as handle:
                descriptor = -1
                for line_number, line in enumerate(handle, start=1):
                    digest.update(line)
                    if not line.endswith(b"\n") or len(line) > self.policy.maximum_provision_line_bytes:
                        raise Step16CandidateError("provision JSONL is malformed", sanitized_code="PROVISION_JSONL_INVALID")
                    value = _mapping(
                        _strict_json(line[:-1], field_name=f"provision {line_number}", maximum_bytes=self.policy.maximum_provision_line_bytes),
                        field_name=f"provision {line_number}",
                    )
                    if _digest(value.get("source_sha256"), "provision source_sha256") != item.raw_source_sha256:
                        raise Step16CandidateError("provision is not bound to the candidate raw source", sanitized_code="PROVISION_SOURCE_DIGEST_MISMATCH")
                    provision_id = _optional_declared_source_text(value.get("provision_identifier"), "provision_identifier", 4096) or _optional_declared_source_text(value.get("record_id"), "provision_record_id", 512)
                    text = value.get("official_text_de")
                    if not isinstance(text, str) or not text or "\x00" in text or len(text.encode("utf-8")) > self.policy.maximum_provision_line_bytes:
                        raise Step16CandidateError("provision text is unavailable or oversized", sanitized_code="PROVISION_TEXT_INVALID")
                    title = _optional_declared_source_text(value.get("provision_title"), "provision_title", 4096)
                    label = provision_id or f"provision-{line_number}"
                    label_line = f"[{label}]" if title is None else f"[{label}: {title}]"
                    pieces.extend((label_line, text, ""))
                    line_count += 1
                    text_count += len(text)
                    if line_count > self.policy.maximum_provisions_per_document:
                        raise Step16CandidateError("provision count exceeds the fixed parser bound", sanitized_code="PROVISION_COUNT_LIMIT_EXCEEDED")
                after = os.fstat(handle.fileno())
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        if (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
            raise Step16PublicationReplayConflictError("provisions changed during read", sanitized_code="STEP16_SOURCE_MUTATION_DURING_READ")
        if opened.st_size != int(self._input_context["files_by_relative"][item.provisions_relative_path]["byte_size"]) or digest.hexdigest() != item.provisions_sha256:
            raise Step16PublicationReplayConflictError("provisions differ from the Step 14 snapshot", sanitized_code="STEP16_PROVISIONS_DIGEST_MISMATCH")
        if not line_count or not text_count:
            raise Step16CandidateError("law has no publishable textual provisions", sanitized_code="EMPTY_DOCUMENT")
        payload = "\n".join(pieces).encode("utf-8")
        if len(payload) > self.policy.maximum_projection_bytes:
            raise Step16CandidateError("textual projection exceeds the Step 11 size limit", sanitized_code="PROJECTION_TOO_LARGE")
        return payload, {
            "law_record_digest": item.law_record_sha256,
            "provisions_digest": item.provisions_sha256,
            "provision_count": line_count,
            "projection_character_count": len(payload.decode("utf-8")),
            "projection_sha256": hashlib.sha256(payload).hexdigest(),
        }

    @staticmethod
    def _evidence_binding(
        kind: str,
        item: PublicationInput,
        snapshot: SnapshotEnvelope,
        evidence: SnapshotStorageEvidence,
        source_artifact_sha256: str,
    ) -> SnapshotBinding:
        if (
            snapshot.snapshot_id != evidence.snapshot_id
            or snapshot.content_sha256 != evidence.canonical_sha256
            or snapshot.content_length != evidence.content_length
        ):
            raise Step16PublicationReplayConflictError(
                "S3 receipt does not bind the exact Step 16 envelope",
                sanitized_code="STEP16_SNAPSHOT_RECEIPT_BINDING_MISMATCH",
            )
        return SnapshotBinding(
            binding_kind=kind,
            version_identity=item.version_identity or f"unresolved-{item.inventory_record_id}",
            source_registry_candidate_id=item.source_registry_candidate_id,
            snapshot_id=evidence.snapshot_id,
            source_artifact_sha256=source_artifact_sha256,
            content_sha256=evidence.canonical_sha256,
            content_length=evidence.content_length,
            serialization_version=snapshot.serialization_version,
            media_type=snapshot.media_type,
            scope_digest=snapshot.scope_digest,
            snapshot_manifest_sha256=snapshot.manifest_sha256,
            captured_at=snapshot.captured_at,
            bucket_reference=evidence.bucket_reference,
            object_key=evidence.object_key,
            version_id=evidence.version_id,
            retention_mode=evidence.retention_mode.value,
            retain_until=evidence.retain_until,
            evidence_digest=evidence.evidence_digest,
            idempotent_replay=evidence.idempotent_replay,
        )

    def _snapshot_envelopes(self, item: PublicationInput, raw_payload: bytes, projection_payload: bytes, *, captured_at: datetime, retain_until: datetime) -> tuple[SnapshotEnvelope, SnapshotEnvelope]:
        common_authority = {
            "authority_policy_version": STEP16_PUBLICATION_POLICY_VERSION,
            "source_class": item.source_class,
            "authority_level": item.authority_level,
        }
        raw = SnapshotEnvelope(
            tenant_id=STEP14_TENANT_ID,
            source_id=item.source_id,
            hat_scope_id=item.hat_scope_id,
            payload=raw_payload,
            serialization_version=EXACT_BYTES_SERIALIZATION_VERSION,
            media_type="application/zip",
            captured_at=captured_at,
            retain_until=retain_until,
            retention_mode=S3ObjectLockMode.GOVERNANCE,
            authority_metadata=common_authority,
            provenance_metadata={
                "step14_manifest_digest": self.step14_manifest_digest,
                "step15_manifest_digest": self.step15_manifest_digest,
                "source_registry_candidate_id": item.source_registry_candidate_id,
                "version_identity": item.version_identity,
                "representation": "RAW_GII_XML_ZIP",
            },
            source_artifact_digest=item.raw_source_sha256,
        )
        projection = SnapshotEnvelope(
            tenant_id=STEP14_TENANT_ID,
            source_id=item.source_id,
            hat_scope_id=item.hat_scope_id,
            payload=projection_payload,
            serialization_version=EXACT_BYTES_SERIALIZATION_VERSION,
            media_type="text/plain",
            captured_at=captured_at,
            retain_until=retain_until,
            retention_mode=S3ObjectLockMode.GOVERNANCE,
            authority_metadata={
                **common_authority,
                "representation": PublicationBodyKind.VERIFIED_GII_TEXTUAL_PROJECTION.value,
            },
            provenance_metadata={
                "step14_manifest_digest": self.step14_manifest_digest,
                "step15_manifest_digest": self.step15_manifest_digest,
                "source_registry_candidate_id": item.source_registry_candidate_id,
                "version_identity": item.version_identity,
                "raw_snapshot_parent_sha256": item.raw_source_sha256,
                "representation": PublicationBodyKind.VERIFIED_GII_TEXTUAL_PROJECTION.value,
            },
            source_artifact_digest=hashlib.sha256(projection_payload).hexdigest(),
        )
        return raw, projection

    def _parse_projection(self, item: PublicationInput, projection: SnapshotEnvelope, evidence: SnapshotStorageEvidence, *, completed_at: datetime) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        if projection.snapshot_id != evidence.snapshot_id or projection.content_sha256 != evidence.canonical_sha256 or projection.content_length != evidence.content_length:
            raise Step16PublicationReplayConflictError("S3 projection evidence does not bind parser input", sanitized_code="STEP16_PARSER_SNAPSHOT_BINDING_MISMATCH")
        artifact = GenericParsingPipeline().parse(
            ParsingRequest(
                tenant_id=STEP14_TENANT_ID,
                owner_user_id=None,
                saga_id=f"step16-{canonical_sha256({'version': item.version_identity})}",
                source_id=item.source_id,
                snapshot_id=projection.snapshot_id,
                knowledge_version_id=f"lawver-{canonical_sha256({'version': item.version_identity})}",
                knowledge_version_ordinal=1,
                hat_scope_id=item.hat_scope_id,
                s3_version_id=evidence.version_id,
                locked_storage_evidence_digest=evidence.evidence_digest,
                input_sha256=projection.content_sha256,
                input_byte_length=projection.content_length,
                media_type="text/plain",
                completed_at=completed_at,
                language_tag=LanguageTag("de"),
            ),
            projection.canonical_payload,
        )
        validation = ParseArtifactValidator().validate(artifact)
        if not validation.accepted:
            raise Step16CandidateError("Step 11 structural validation rejected the textual projection", sanitized_code=validation.reason_codes[0])
        if artifact.quarantine.required:
            raise Step16CandidateError("Step 11 security policy quarantined the textual projection", sanitized_code=artifact.quarantine.reason_codes[0])
        sections = artifact.sections
        chunks = artifact.chunks
        coverage_gaps = 0
        expected_overlaps = 0
        unexpected_overlaps = 0
        chunks_by_section: dict[str, list[Any]] = defaultdict(list)
        for chunk in chunks:
            chunks_by_section[chunk.section_id].append(chunk)
        for section in sections:
            previous_end = section.normalized_start_offset
            for chunk in sorted(chunks_by_section[section.section_id], key=lambda value: value.section_chunk_ordinal):
                if chunk.normalized_start_offset > previous_end:
                    coverage_gaps += 1
                if chunk.normalized_start_offset < previous_end:
                    expected_overlaps += 1
                previous_end = max(previous_end, chunk.normalized_end_offset)
            if previous_end != section.normalized_end_offset:
                coverage_gaps += 1
        parser_coverage = {
            "schema_version": STEP16_PUBLICATION_SCHEMA_VERSION,
            "version_identity": item.version_identity,
            "document_identity": item.document_identity,
            "source_registry_candidate_id": item.source_registry_candidate_id,
            "snapshot_id": projection.snapshot_id,
            "s3_version_id": evidence.version_id,
            "parser_name": artifact.document.parser_name,
            "parser_version": artifact.document.parser_version,
            "parser_contract_version": artifact.document.parser_contract_version,
            "normalization_profile": artifact.document.normalization_profile,
            "normalization_version": artifact.document.normalization_version,
            "input_sha256": artifact.document.input_sha256,
            "parsed_document_id": artifact.document.document_id,
            "parse_artifact_digest": artifact.document.parse_artifact_digest,
            "section_count": len(sections),
            "security_finding_count": len(artifact.findings),
            "finding_manifest_digest": artifact.document.finding_manifest_digest,
            "quarantine_required": False,
            "coverage_status": "PASS",
        }
        chunk_coverage = {
            "schema_version": STEP16_PUBLICATION_SCHEMA_VERSION,
            "version_identity": item.version_identity,
            "document_identity": item.document_identity,
            "parsed_document_id": artifact.document.document_id,
            "section_manifest_digest": artifact.document.section_manifest_digest,
            "chunk_manifest_digest": artifact.document.chunk_manifest_digest,
            "chunk_count": len(chunks),
            "offset_basis": "NORMALIZED_UNICODE_CODE_POINTS_NFC",
            "coverage_gap_count": coverage_gaps,
            "expected_overlap_count": expected_overlaps,
            "unexplained_overlap_count": unexpected_overlaps,
            "coverage_status": "PASS" if not coverage_gaps and not unexpected_overlaps else "FAILED",
        }
        projection_evidence = {
            "schema_version": STEP16_PUBLICATION_SCHEMA_VERSION,
            "version_identity": item.version_identity,
            "document_identity": item.document_identity,
            "projection_kind": PublicationBodyKind.VERIFIED_GII_TEXTUAL_PROJECTION.value,
            "projection_sha256": projection.content_sha256,
            "projection_byte_length": projection.content_length,
            "parsed_document_id": artifact.document.document_id,
            "parse_artifact_digest": artifact.document.parse_artifact_digest,
            "section_manifest_digest": artifact.document.section_manifest_digest,
            "chunk_manifest_digest": artifact.document.chunk_manifest_digest,
            "finding_manifest_digest": artifact.document.finding_manifest_digest,
        }
        return parser_coverage, chunk_coverage, projection_evidence

    @staticmethod
    def _connect(path: Path) -> sqlite3.Connection:
        """Create an owned, non-authoritative resume spool below Step 16 output."""

        connection = sqlite3.connect(path)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS metadata(
              key TEXT PRIMARY KEY,
              value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS processed(
              version_identity TEXT PRIMARY KEY,
              input_digest TEXT NOT NULL,
              decision_json TEXT NOT NULL,
              raw_binding_json TEXT,
              projection_binding_json TEXT,
              parser_json TEXT,
              chunk_json TEXT,
              provenance_json TEXT,
              temporal_json TEXT,
              jurisdiction_json TEXT,
              item_json TEXT,
              exclusion_json TEXT
            );
            CREATE TABLE IF NOT EXISTS input_conflicts(
              conflict_id TEXT PRIMARY KEY,
              conflict_json TEXT NOT NULL
            );
            """
        )
        return connection

    @staticmethod
    def _serialized(value: object) -> str:
        return canonical_json_bytes(to_canonical_data(value)).decode("utf-8")

    @staticmethod
    def _sha256_file(path: Path) -> str:
        descriptor: int | None = None
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0))
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode):
                raise Step16PublicationSafetyError("Step 16 output is not a regular file", sanitized_code="STEP16_OUTPUT_UNSAFE")
            digest = hashlib.sha256()
            while True:
                block = os.read(descriptor, 1024 * 1024)
                if not block:
                    break
                digest.update(block)
            after = os.fstat(descriptor)
            if (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
                raise Step16PublicationReplayConflictError("Step 16 output changed while being verified", sanitized_code="STEP16_OUTPUT_CHANGED")
            return digest.hexdigest()
        except Step16PublicationError:
            raise
        except OSError as exc:
            raise Step16PublicationSafetyError("Step 16 output cannot be verified", sanitized_code="STEP16_OUTPUT_UNAVAILABLE") from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)

    @staticmethod
    def _publish_no_replace(temporary: Path, target: Path) -> None:
        """Atomically publish a new owned output without replacing evidence."""

        try:
            os.link(temporary, target, follow_symlinks=False)
        except FileExistsError as exc:
            raise Step16PublicationReplayConflictError("Step 16 output already exists", sanitized_code="STEP16_OUTPUT_REPLAY_CONFLICT") from exc
        except OSError as exc:
            raise Step16PublicationSafetyError("Step 16 output could not be atomically published", sanitized_code="STEP16_OUTPUT_PUBLISH_FAILED") from exc
        try:
            temporary.unlink()
        except OSError as exc:
            raise Step16PublicationSafetyError("Step 16 temporary output could not be finalized", sanitized_code="STEP16_OUTPUT_FINALIZE_FAILED") from exc
        directory_fd = os.open(target.parent, os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)

    def _write_atomic_absent(self, target: Path, payload: bytes) -> tuple[int, str]:
        if target.parent != self.bundle_root and target.parent != (self.bundle_root / "checkpoints"):
            raise Step16PublicationSafetyError("Step 16 output escaped its approved run root", sanitized_code="STEP16_OUTPUT_PATH_ESCAPE")
        digest = hashlib.sha256(payload).hexdigest()
        if target.exists():
            if target.is_symlink() or not target.is_file() or target.stat().st_size != len(payload) or self._sha256_file(target) != digest:
                raise Step16PublicationReplayConflictError("existing Step 16 output differs from deterministic facts", sanitized_code="STEP16_OUTPUT_REPLAY_CONFLICT")
            return len(payload), digest
        temporary = target.with_name(f".{target.name}.{os.getpid()}.part")
        descriptor: int | None = None
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0), 0o640)
            offset = 0
            while offset < len(payload):
                offset += os.write(descriptor, payload[offset:])
            os.fsync(descriptor)
        except BaseException:
            if descriptor is not None:
                os.close(descriptor)
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        else:
            assert descriptor is not None
            os.close(descriptor)
        self._publish_no_replace(temporary, target)
        return len(payload), digest

    def _ensure_bundle_root(self) -> tuple[Path, Path]:
        if self.bundle_root is None or self.run_id is None:
            raise Step16PublicationSafetyError("Step 16 plan was not created", sanitized_code="STEP16_PLAN_REQUIRED")
        if not self.bundle_parent.exists():
            try:
                os.mkdir(self.bundle_parent, mode=0o750)
            except FileExistsError:
                pass
            except OSError as exc:
                raise Step16PublicationSafetyError("Step 16 namespace could not be created", sanitized_code="STEP16_OUTPUT_ROOT_CREATE_FAILED") from exc
        parent = self.bundle_parent.lstat()
        if self.bundle_parent.is_symlink() or not stat.S_ISDIR(parent.st_mode) or parent.st_dev != self._root_stat.st_dev:
            raise Step16PublicationSafetyError("Step 16 namespace is unsafe", sanitized_code="STEP16_OUTPUT_ROOT_UNSAFE")
        if not self.bundle_root.exists():
            try:
                os.mkdir(self.bundle_root, mode=0o750)
            except OSError as exc:
                raise Step16PublicationSafetyError("Step 16 run root could not be created", sanitized_code="STEP16_OUTPUT_ROOT_CREATE_FAILED") from exc
        run = self.bundle_root.lstat()
        if self.bundle_root.is_symlink() or not stat.S_ISDIR(run.st_mode) or run.st_dev != self._root_stat.st_dev:
            raise Step16PublicationReplayConflictError("Step 16 run root is unsafe", sanitized_code="STEP16_OUTPUT_REPLAY_CONFLICT")
        checkpoints = self.bundle_root / "checkpoints"
        if not checkpoints.exists():
            try:
                os.mkdir(checkpoints, mode=0o750)
            except OSError as exc:
                raise Step16PublicationSafetyError("Step 16 checkpoint root could not be created", sanitized_code="STEP16_CHECKPOINT_CREATE_FAILED") from exc
        state = checkpoints.lstat()
        if checkpoints.is_symlink() or not stat.S_ISDIR(state.st_mode) or state.st_dev != self._root_stat.st_dev:
            raise Step16PublicationSafetyError("Step 16 checkpoint root is unsafe", sanitized_code="STEP16_CHECKPOINT_UNSAFE")
        return self.bundle_root, checkpoints

    def _initialize_state(self, connection: sqlite3.Connection, plan: Step16PublicationPlan) -> tuple[datetime, datetime, int]:
        existing = dict(connection.execute("SELECT key, value FROM metadata"))
        expected = {
            "run_id": plan.run_id,
            "starting_head": self.starting_head,
            "policy_digest": self.policy.policy_digest,
            "step14_manifest_digest": self.step14_manifest_digest,
            "step15_manifest_digest": self.step15_manifest_digest,
            "source_root_identity_digest": self.source_root_identity_digest,
            "source_tree_digest": plan.source_tree_digest,
            "plan_digest": plan.plan_digest,
        }
        if existing:
            for key, value in expected.items():
                if existing.get(key) != value:
                    raise Step16PublicationReplayConflictError("Step 16 checkpoint belongs to incompatible immutable facts", sanitized_code="STEP16_INCOMPATIBLE_CHECKPOINT")
            started_at = datetime.fromisoformat(existing["started_at"].replace("Z", "+00:00"))
            captured_at = datetime.fromisoformat(existing["captured_at"].replace("Z", "+00:00"))
            if existing.get("completed") == "true":
                return started_at, captured_at, int(existing.get("resume_count", "0"))
            resume_count = int(existing.get("resume_count", "0")) + 1
            connection.execute("UPDATE metadata SET value=? WHERE key='resume_count'", (str(resume_count),))
            connection.commit()
            return started_at, captured_at, resume_count
        started_at = ensure_utc(self.clock(), "Step 16 clock")
        captured_at = started_at.replace(microsecond=0)
        values = {
            **expected,
            "started_at": started_at.isoformat(),
            "captured_at": captured_at.isoformat(),
            "resume_count": "0",
            "completed": "false",
        }
        connection.executemany("INSERT INTO metadata(key,value) VALUES(?,?)", sorted(values.items()))
        connection.commit()
        return started_at, captured_at, 0

    @staticmethod
    def _row_key(item: PublicationInput) -> str:
        return item.version_identity or f"inventory-{item.inventory_record_id}"

    @staticmethod
    def _disposition_for_reasons(reasons: Iterable[str]) -> PublicationDisposition:
        values = set(reasons)
        if any("CONFLICT" in value for value in values):
            return PublicationDisposition.CONFLICTING
        if any(value.startswith("RIGHTS_") or value.startswith("PRIVACY_") for value in values):
            return PublicationDisposition.INELIGIBLE
        if any(value.startswith("UNSUPPORTED_") for value in values):
            return PublicationDisposition.UNSUPPORTED
        if any(value.endswith("MISSING") for value in values):
            return PublicationDisposition.REVIEW_REQUIRED
        return PublicationDisposition.REVIEW_REQUIRED

    def _static_decision(self, item: PublicationInput, context: Mapping[str, Any]) -> PublicationDecision:
        reasons = self._static_reasons(item, context)
        if not reasons:
            return PublicationDecision(
                version_identity=item.version_identity,
                document_identity=item.document_identity,
                inventory_record_id=item.inventory_record_id,
                source_registry_candidate_id=item.source_registry_candidate_id,
                disposition=PublicationDisposition.ELIGIBLE,
                reason_codes=(),
                input_digest=item.input_digest,
                raw_source_sha256=item.raw_source_sha256,
            )
        return PublicationDecision(
            version_identity=item.version_identity,
            document_identity=item.document_identity,
            inventory_record_id=item.inventory_record_id,
            source_registry_candidate_id=item.source_registry_candidate_id,
            disposition=self._disposition_for_reasons(reasons),
            reason_codes=reasons,
            input_digest=item.input_digest,
            raw_source_sha256=item.raw_source_sha256,
        )

    def _failure_decision(self, item: PublicationInput, error: Step16CandidateError) -> PublicationDecision:
        code = _text(error.sanitized_code, "candidate error code", 128)
        if "PROMPT" in code or "QUARANTINE" in code:
            disposition = PublicationDisposition.QUARANTINED
        elif "CONFLICT" in code or "MISMATCH" in code:
            disposition = PublicationDisposition.CONFLICTING
        elif "UNSUPPORTED" in code or "TOO_LARGE" in code:
            disposition = PublicationDisposition.UNSUPPORTED
        elif "EMPTY" in code or "INVALID" in code or "MALFORMED" in code:
            disposition = PublicationDisposition.INELIGIBLE
        else:
            disposition = PublicationDisposition.REVIEW_REQUIRED
        return PublicationDecision(
            version_identity=item.version_identity,
            document_identity=item.document_identity,
            inventory_record_id=item.inventory_record_id,
            source_registry_candidate_id=item.source_registry_candidate_id,
            disposition=disposition,
            reason_codes=(code,),
            input_digest=item.input_digest,
            raw_source_sha256=item.raw_source_sha256,
        )

    @staticmethod
    def _alias_evidence(item: PublicationInput) -> dict[str, Any]:
        """Expose every Step 14 alias without treating it as legal equivalence."""

        return {
            "alias_inventory_record_ids": list(item.alias_inventory_record_ids),
            "alias_law_record_relative_paths": list(item.alias_law_record_relative_paths),
            "alias_provisions_relative_paths": list(item.alias_provisions_relative_paths),
            "alias_raw_source_relative_paths": list(item.alias_raw_source_relative_paths),
            "alias_metadata_conflict": item.alias_metadata_conflict,
        }

    @staticmethod
    def _temporal_validation(item: PublicationInput, *, accepted: bool, reason_codes: tuple[str, ...]) -> dict[str, Any]:
        data = {
            "schema_version": STEP16_PUBLICATION_SCHEMA_VERSION,
            "validation_kind": "STEP15_TEMPORAL_PUBLICATION_GATE",
            "inventory_record_id": item.inventory_record_id,
            "source_registry_candidate_id": item.source_registry_candidate_id,
            "document_identity": item.document_identity,
            "version_identity": item.version_identity,
            "temporal_facts_digest": item.temporal_facts_digest,
            "accepted": accepted,
            "reason_codes": list(reason_codes),
            "rule_version": STEP16_PUBLICATION_POLICY_VERSION,
            **GermanLawPublicationEngine._alias_evidence(item),
        }
        data["validation_digest"] = canonical_sha256(data)
        return data

    @staticmethod
    def _jurisdiction_validation(item: PublicationInput, *, accepted: bool, reason_codes: tuple[str, ...]) -> dict[str, Any]:
        data = {
            "schema_version": STEP16_PUBLICATION_SCHEMA_VERSION,
            "validation_kind": "STEP15_JURISDICTION_PUBLICATION_GATE",
            "inventory_record_id": item.inventory_record_id,
            "source_registry_candidate_id": item.source_registry_candidate_id,
            "document_identity": item.document_identity,
            "version_identity": item.version_identity,
            "normalized_jurisdiction": item.normalized_jurisdiction,
            "normalization_status": item.jurisdiction_normalization_status,
            "accepted": accepted,
            "reason_codes": list(reason_codes),
            "rule_version": STEP16_PUBLICATION_POLICY_VERSION,
            **GermanLawPublicationEngine._alias_evidence(item),
        }
        data["validation_digest"] = canonical_sha256(data)
        return data

    def _provenance_chain(
        self,
        item: PublicationInput,
        raw: SnapshotBinding,
        projection: SnapshotBinding,
        projection_evidence: Mapping[str, Any],
        temporal: Mapping[str, Any],
        jurisdiction: Mapping[str, Any],
        decision: PublicationDecision,
    ) -> dict[str, Any]:
        data: dict[str, Any] = {
            "schema_version": STEP16_PUBLICATION_SCHEMA_VERSION,
            "provenance_version": STEP16_PROVENANCE_VERSION,
            "inventory_record_id": item.inventory_record_id,
            "source_registry_candidate_id": item.source_registry_candidate_id,
            "document_identity": item.document_identity,
            "version_identity": item.version_identity,
            "source_root_identity_digest": self.source_root_identity_digest,
            "step14_manifest_digest": self.step14_manifest_digest,
            "step15_manifest_digest": self.step15_manifest_digest,
            "raw_source_relative_path_digest": sha256_hex(item.raw_source_relative_path),
            "raw_source_sha256": item.raw_source_sha256,
            "raw_snapshot_binding_digest": raw.binding_digest,
            "projection_snapshot_binding_digest": projection.binding_digest,
            "parser_projection_evidence_digest": canonical_sha256(projection_evidence),
            "temporal_validation_digest": _digest(temporal["validation_digest"], "temporal validation digest"),
            "jurisdiction_validation_digest": _digest(jurisdiction["validation_digest"], "jurisdiction validation digest"),
            "publication_decision_digest": decision.decision_digest,
            "source_class": item.source_class,
            "authority_level": item.authority_level,
            "authority_limitations": ["OFFICIAL_CONSOLIDATION_NOT_AUTHENTIC_PROMULGATION"],
            **self._alias_evidence(item),
        }
        identity = canonical_sha256(
            {
                "inventory_record_id": item.inventory_record_id,
                "version_identity": item.version_identity,
                "raw": raw.binding_digest,
                "projection": projection.binding_digest,
                "parser": projection_evidence["parse_artifact_digest"],
                "temporal": temporal["validation_digest"],
                "jurisdiction": jurisdiction["validation_digest"],
                "policy": STEP16_PROVENANCE_VERSION,
            }
        )
        data["provenance_chain_id"] = f"publication-provenance-{identity}"
        data["provenance_digest"] = canonical_sha256(data)
        return data

    @staticmethod
    def _publication_item(
        item: PublicationInput,
        decision: PublicationDecision,
        raw: SnapshotBinding,
        projection: SnapshotBinding,
        parser_coverage: Mapping[str, Any],
        chunk_coverage: Mapping[str, Any],
        provenance: Mapping[str, Any],
    ) -> dict[str, Any]:
        identity = canonical_sha256(
            {
                "version_identity": item.version_identity,
                "decision": decision.decision_digest,
                "raw": raw.binding_digest,
                "projection": projection.binding_digest,
                "provenance": provenance["provenance_digest"],
            }
        )
        data = {
            "schema_version": STEP16_PUBLICATION_SCHEMA_VERSION,
            "publication_policy_version": STEP16_PUBLICATION_POLICY_VERSION,
            "publication_item_id": f"publication-item-{identity}",
            "publication_event_id": f"publication-event-{identity}",
            "state": "PUBLISHED",
            "trusted_actor_type": "TRUSTED_PUBLICATION_SERVICE",
            "trusted_actor_reference": "german-law-publication-service-1a",
            "inventory_record_id": item.inventory_record_id,
            "source_registry_candidate_id": item.source_registry_candidate_id,
            "source_id": item.source_id,
            "hat_scope_id": item.hat_scope_id,
            "document_identity": item.document_identity,
            "version_identity": item.version_identity,
            "official_identifier": item.official_identifier,
            "source_class": item.source_class,
            "authority_level": item.authority_level,
            "consolidation_status": item.consolidation_status,
            "normalized_jurisdiction": item.normalized_jurisdiction,
            "raw_snapshot_binding_digest": raw.binding_digest,
            "projection_snapshot_binding_digest": projection.binding_digest,
            "parser_coverage_digest": canonical_sha256(parser_coverage),
            "chunk_coverage_digest": canonical_sha256(chunk_coverage),
            "provenance_chain_digest": _digest(provenance["provenance_digest"], "provenance digest"),
            "publication_decision_digest": decision.decision_digest,
            "publication_authority": "TRUSTED_SERVICE_ONLY",
            **GermanLawPublicationEngine._alias_evidence(item),
        }
        data["publication_item_digest"] = canonical_sha256(data)
        return data

    @staticmethod
    def _exclusion_record(item: PublicationInput, decision: PublicationDecision) -> dict[str, Any]:
        data = {
            "schema_version": STEP16_PUBLICATION_SCHEMA_VERSION,
            "inventory_record_id": item.inventory_record_id,
            "source_registry_candidate_id": item.source_registry_candidate_id,
            "document_identity": item.document_identity,
            "version_identity": item.version_identity,
            "disposition": decision.disposition.value,
            "reason_codes": list(decision.reason_codes),
            "publication_decision_digest": decision.decision_digest,
            "state": "UNPUBLISHED",
            **GermanLawPublicationEngine._alias_evidence(item),
        }
        data["exclusion_digest"] = canonical_sha256(data)
        return data

    def _store_processed(
        self,
        connection: sqlite3.Connection,
        item: PublicationInput,
        decision: PublicationDecision,
        *,
        raw: Mapping[str, Any] | None = None,
        projection: Mapping[str, Any] | None = None,
        parser: Mapping[str, Any] | None = None,
        chunk: Mapping[str, Any] | None = None,
        provenance: Mapping[str, Any] | None = None,
        temporal: Mapping[str, Any] | None = None,
        jurisdiction: Mapping[str, Any] | None = None,
        publication_item: Mapping[str, Any] | None = None,
        exclusion: Mapping[str, Any] | None = None,
    ) -> bool:
        key = self._row_key(item)
        existing = connection.execute("SELECT input_digest,decision_json FROM processed WHERE version_identity=?", (key,)).fetchone()
        decision_record = _record_data(decision)
        decision_record.update(self._alias_evidence(item))
        decision_record["decision_record_digest"] = canonical_sha256(decision_record)
        decision_json = self._serialized(decision_record)
        if existing is not None:
            if existing[0] != item.input_digest or existing[1] != decision_json:
                raise Step16PublicationReplayConflictError("Step 16 candidate replay conflicts with its checkpoint", sanitized_code="STEP16_CANDIDATE_REPLAY_CONFLICT")
            return False
        values = (
            key,
            item.input_digest,
            decision_json,
            None if raw is None else self._serialized(raw),
            None if projection is None else self._serialized(projection),
            None if parser is None else self._serialized(parser),
            None if chunk is None else self._serialized(chunk),
            None if provenance is None else self._serialized(provenance),
            None if temporal is None else self._serialized(temporal),
            None if jurisdiction is None else self._serialized(jurisdiction),
            None if publication_item is None else self._serialized(publication_item),
            None if exclusion is None else self._serialized(exclusion),
        )
        connection.execute(
            """INSERT INTO processed(
                 version_identity,input_digest,decision_json,raw_binding_json,
                 projection_binding_json,parser_json,chunk_json,provenance_json,
                 temporal_json,jurisdiction_json,item_json,exclusion_json
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            values,
        )
        return True

    def _record_step15_conflicts(self, connection: sqlite3.Connection, context: Mapping[str, Any]) -> None:
        for conflict in context["conflicts"]:
            source_digest = _digest(conflict.get("conflict_digest"), "Step 15 conflict digest")
            source_id = _identifier(conflict.get("conflict_id"), "Step 15 conflict ID", 512)
            data = {
                "schema_version": STEP16_PUBLICATION_SCHEMA_VERSION,
                "publication_conflict_id": f"publication-conflict-{canonical_sha256({'step15_conflict_id': source_id, 'digest': source_digest, 'policy': STEP16_PUBLICATION_POLICY_VERSION})}",
                "step15_conflict_id": source_id,
                "step15_conflict_digest": source_digest,
                "resolution_status": "REVIEW_REQUIRED",
                "human_review_required": True,
                "reason_codes": ["STEP15_NORMALIZATION_CONFLICT_PRESERVED"],
            }
            data["publication_conflict_digest"] = canonical_sha256(data)
            serialized = self._serialized(data)
            existing = connection.execute("SELECT conflict_json FROM input_conflicts WHERE conflict_id=?", (data["publication_conflict_id"],)).fetchone()
            if existing is not None and existing[0] != serialized:
                raise Step16PublicationReplayConflictError("Step 15 conflict is replayed with incompatible facts", sanitized_code="STEP16_CONFLICT_REPLAY_CONFLICT")
            connection.execute("INSERT OR IGNORE INTO input_conflicts(conflict_id,conflict_json) VALUES(?,?)", (data["publication_conflict_id"], serialized))
        connection.commit()

    def _candidate_outcome(
        self,
        item: PublicationInput,
        decision: PublicationDecision,
        *,
        captured_at: datetime,
        retain_until: datetime,
        snapshot_writer: SnapshotWriter,
    ) -> _CandidateOutcome:
        """Compute one candidate without touching the interruption spool."""

        temporal = self._temporal_validation(
            item,
            accepted=decision.disposition is PublicationDisposition.ELIGIBLE,
            reason_codes=decision.reason_codes,
        )
        jurisdiction = self._jurisdiction_validation(
            item,
            accepted=decision.disposition is PublicationDisposition.ELIGIBLE,
            reason_codes=decision.reason_codes,
        )
        raw_data: dict[str, Any] | None = None
        projection_data: dict[str, Any] | None = None
        parser: dict[str, Any] | None = None
        chunk: dict[str, Any] | None = None
        provenance: dict[str, Any] | None = None
        item_data: dict[str, Any] | None = None
        exclusion: dict[str, Any] | None = None
        published = False
        if decision.disposition is PublicationDisposition.ELIGIBLE:
            try:
                raw_payload = self._read_verified_source(
                    item.raw_source_relative_path,
                    item.raw_source_sha256,
                    item.raw_source_length,
                    maximum_length=64 * 1024 * 1024,
                )
                projection_payload, projection_metadata = self._projection_payload(item)
                raw_envelope, projection_envelope = self._snapshot_envelopes(
                    item,
                    raw_payload,
                    projection_payload,
                    captured_at=captured_at,
                    retain_until=retain_until,
                )
                raw_evidence = snapshot_writer(raw_envelope)
                raw_binding = self._evidence_binding(
                    "RAW_GII_XML_ZIP",
                    item,
                    raw_envelope,
                    raw_evidence,
                    item.raw_source_sha256,
                )
                raw_data = _record_data(raw_binding)
                projection_evidence = snapshot_writer(projection_envelope)
                projection_binding = self._evidence_binding(
                    PublicationBodyKind.VERIFIED_GII_TEXTUAL_PROJECTION.value,
                    item,
                    projection_envelope,
                    projection_evidence,
                    projection_envelope.content_sha256,
                )
                projection_data = _record_data(projection_binding)
                try:
                    parser, chunk, projection_parse_evidence = self._parse_projection(
                        item,
                        projection_envelope,
                        projection_evidence,
                        completed_at=captured_at,
                    )
                except ParsingError as exc:
                    raise Step16CandidateError(
                        "Step 11 parser rejected the textual projection",
                        sanitized_code=_text(
                            getattr(exc, "sanitized_code", "PARSER_REJECTED"),
                            "parser error code",
                            128,
                        ),
                    ) from exc
                if chunk["coverage_status"] != "PASS":
                    raise Step16CandidateError(
                        "Step 11 chunk coverage is incomplete",
                        sanitized_code="CHUNK_COVERAGE_INVALID",
                    )
                parser["projection_metadata_digest"] = canonical_sha256(
                    projection_metadata
                )
                temporal = self._temporal_validation(item, accepted=True, reason_codes=())
                jurisdiction = self._jurisdiction_validation(
                    item,
                    accepted=True,
                    reason_codes=(),
                )
                provenance = self._provenance_chain(
                    item,
                    raw_binding,
                    projection_binding,
                    projection_parse_evidence,
                    temporal,
                    jurisdiction,
                    decision,
                )
                item_data = self._publication_item(
                    item,
                    decision,
                    raw_binding,
                    projection_binding,
                    parser,
                    chunk,
                    provenance,
                )
                published = True
            except (Step16CandidateError, Step16PublicationSafetyError) as exc:
                if isinstance(exc, Step16PublicationSafetyError):
                    # This code is emitted only by strict conversion of a
                    # corpus-supplied scalar field.  The raw source remains
                    # immutable and hash-bound; one malformed optional
                    # metadata field must exclude that candidate, not abort an
                    # otherwise independent full-corpus run.  All other
                    # source/output/integrity safety failures remain fatal.
                    if exc.sanitized_code != "INVALID_STEP16_TEXT":
                        raise
                    candidate_error = Step16CandidateError(
                        "structured projection metadata is invalid",
                        sanitized_code="PROJECTION_METADATA_INVALID",
                    )
                else:
                    candidate_error = exc
                decision = self._failure_decision(item, candidate_error)
                temporal = self._temporal_validation(
                    item,
                    accepted=False,
                    reason_codes=decision.reason_codes,
                )
                jurisdiction = self._jurisdiction_validation(
                    item,
                    accepted=False,
                    reason_codes=decision.reason_codes,
                )
                exclusion = self._exclusion_record(item, decision)
        if decision.disposition is not PublicationDisposition.ELIGIBLE:
            exclusion = exclusion or self._exclusion_record(item, decision)
        return _CandidateOutcome(
            decision=decision,
            temporal=temporal,
            jurisdiction=jurisdiction,
            raw=raw_data,
            projection=projection_data,
            parser=parser,
            chunk=chunk,
            provenance=provenance,
            publication_item=item_data,
            exclusion=exclusion,
            published=published,
        )

    def _process_candidates(
        self,
        connection: sqlite3.Connection,
        inputs: tuple[PublicationInput, ...],
        context: Mapping[str, Any],
        *,
        captured_at: datetime,
        snapshot_writer: SnapshotWriter,
    ) -> Counter[str]:
        counters: Counter[str] = Counter()
        retain_until = captured_at + timedelta(days=self.policy.retention_days)
        executor: ThreadPoolExecutor | None = None
        if self.policy.max_snapshot_workers > 1:
            executor = ThreadPoolExecutor(
                max_workers=self.policy.max_snapshot_workers,
                thread_name_prefix="step16-snapshot",
            )
        try:
            for batch_start in range(0, len(inputs), self.policy.max_snapshot_workers):
                pending: list[tuple[int, PublicationInput, _CandidateOutcome | Future[_CandidateOutcome]]] = []
                for ordinal, item in enumerate(
                    inputs[batch_start : batch_start + self.policy.max_snapshot_workers],
                    start=batch_start + 1,
                ):
                    key = self._row_key(item)
                    prior = connection.execute(
                        "SELECT input_digest FROM processed WHERE version_identity=?",
                        (key,),
                    ).fetchone()
                    if prior is not None:
                        if prior[0] != item.input_digest:
                            raise Step16PublicationReplayConflictError(
                                "Step 16 checkpoint candidate digest changed",
                                sanitized_code="STEP16_CANDIDATE_REPLAY_CONFLICT",
                            )
                        counters["replayed_checkpoint_records"] += 1
                        continue
                    decision = self._static_decision(item, context)
                    if (
                        executor is not None
                        and decision.disposition is PublicationDisposition.ELIGIBLE
                    ):
                        outcome: _CandidateOutcome | Future[_CandidateOutcome] = executor.submit(
                            self._candidate_outcome,
                            item,
                            decision,
                            captured_at=captured_at,
                            retain_until=retain_until,
                            snapshot_writer=snapshot_writer,
                        )
                    else:
                        outcome = self._candidate_outcome(
                            item,
                            decision,
                            captured_at=captured_at,
                            retain_until=retain_until,
                            snapshot_writer=snapshot_writer,
                        )
                    pending.append((ordinal, item, outcome))
                # Persist in source order even when independent S3 calls run in
                # parallel.  The spool and all generated JSONL artifacts are
                # therefore deterministic across worker scheduling.
                for ordinal, item, outcome in pending:
                    resolved = outcome.result() if isinstance(outcome, Future) else outcome
                    if resolved.published:
                        counters["published"] += 1
                    if resolved.decision.disposition is not PublicationDisposition.ELIGIBLE:
                        counters[resolved.decision.disposition.value] += 1
                    self._store_processed(
                        connection,
                        item,
                        resolved.decision,
                        raw=resolved.raw,
                        projection=resolved.projection,
                        parser=resolved.parser,
                        chunk=resolved.chunk,
                        provenance=resolved.provenance,
                        temporal=resolved.temporal,
                        jurisdiction=resolved.jurisdiction,
                        publication_item=resolved.publication_item,
                        exclusion=resolved.exclusion,
                    )
                    counters["processed"] += 1
                    if ordinal % self.policy.checkpoint_batch_size == 0:
                        connection.commit()
                        self.progress(
                            {
                                "processed": ordinal,
                                "published": counters["published"],
                                "excluded": ordinal - counters["published"],
                            }
                        )
        finally:
            if executor is not None:
                executor.shutdown(wait=True, cancel_futures=False)
        connection.commit()
        self.progress({"processed": len(inputs), "published": counters["published"], "excluded": len(inputs) - counters["published"]})
        return counters

    def _write_query_jsonl(self, connection: sqlite3.Connection, *, name: str, query: str) -> tuple[int, str]:
        if self.bundle_root is None:
            raise Step16PublicationSafetyError("Step 16 bundle root is unavailable", sanitized_code="STEP16_PLAN_REQUIRED")
        target = self.bundle_root / name
        digest = hashlib.sha256()
        length = 0
        for (serialized,) in connection.execute(query):
            line = str(serialized).encode("utf-8") + b"\n"
            digest.update(line)
            length += len(line)
        expected = digest.hexdigest()
        if target.exists():
            if target.is_symlink() or not target.is_file() or target.stat().st_size != length or self._sha256_file(target) != expected:
                raise Step16PublicationReplayConflictError("existing Step 16 JSONL differs from checkpoint facts", sanitized_code="STEP16_OUTPUT_REPLAY_CONFLICT")
            return length, expected
        temporary = target.with_name(f".{target.name}.{os.getpid()}.part")
        descriptor: int | None = None
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0), 0o640)
            for (serialized,) in connection.execute(query):
                line = str(serialized).encode("utf-8") + b"\n"
                offset = 0
                while offset < len(line):
                    offset += os.write(descriptor, line[offset:])
            os.fsync(descriptor)
        except BaseException:
            if descriptor is not None:
                os.close(descriptor)
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        else:
            assert descriptor is not None
            os.close(descriptor)
        self._publish_no_replace(temporary, target)
        return length, expected

    @staticmethod
    def _logical_digest(generated: Iterable[tuple[str, int, str]]) -> str:
        return canonical_sha256(tuple(sorted(generated)))

    def _summary(
        self,
        connection: sqlite3.Connection,
        plan: Step16PublicationPlan,
        *,
        logical_output_digest: str,
        started_at: datetime,
        completed_at: datetime,
        resume_count: int,
    ) -> dict[str, Any]:
        dispositions = {
            str(key): int(value)
            for key, value in connection.execute("SELECT json_extract(decision_json,'$.disposition'),count(*) FROM processed GROUP BY json_extract(decision_json,'$.disposition') ORDER BY json_extract(decision_json,'$.disposition')")
        }
        raw_bindings = int(connection.execute("SELECT count(*) FROM processed WHERE raw_binding_json IS NOT NULL").fetchone()[0])
        projection_bindings = int(connection.execute("SELECT count(*) FROM processed WHERE projection_binding_json IS NOT NULL").fetchone()[0])
        replays = int(
            connection.execute(
                "SELECT count(*) FROM processed WHERE json_extract(raw_binding_json,'$.idempotent_replay')=1"
            ).fetchone()[0]
        ) + int(
            connection.execute(
                "SELECT count(*) FROM processed WHERE json_extract(projection_binding_json,'$.idempotent_replay')=1"
            ).fetchone()[0]
        )
        parser_rows = int(connection.execute("SELECT count(*) FROM processed WHERE parser_json IS NOT NULL").fetchone()[0])
        sections = int(connection.execute("SELECT coalesce(sum(json_extract(parser_json,'$.section_count')),0) FROM processed WHERE parser_json IS NOT NULL").fetchone()[0])
        chunks = int(connection.execute("SELECT coalesce(sum(json_extract(chunk_json,'$.chunk_count')),0) FROM processed WHERE chunk_json IS NOT NULL").fetchone()[0])
        findings = int(connection.execute("SELECT coalesce(sum(json_extract(parser_json,'$.security_finding_count')),0) FROM processed WHERE parser_json IS NOT NULL").fetchone()[0])
        gap_count = int(connection.execute("SELECT coalesce(sum(json_extract(chunk_json,'$.coverage_gap_count')),0) FROM processed WHERE chunk_json IS NOT NULL").fetchone()[0])
        overlap_count = int(connection.execute("SELECT coalesce(sum(json_extract(chunk_json,'$.unexplained_overlap_count')),0) FROM processed WHERE chunk_json IS NOT NULL").fetchone()[0])
        reason_counts = {
            str(key): int(value)
            for key, value in connection.execute(
                "SELECT json_each.value,count(*) FROM processed,json_each(json_extract(decision_json,'$.reason_codes')) GROUP BY json_each.value ORDER BY json_each.value"
            )
        }
        base = {
            "schema_version": STEP16_PUBLICATION_SCHEMA_VERSION,
            "run_id": plan.run_id,
            "starting_head": self.starting_head,
            "publication_policy_version": self.policy.policy_version,
            "publication_policy_digest": self.policy.policy_digest,
            "step14_manifest_digest": self.step14_manifest_digest,
            "step15_manifest_digest": self.step15_manifest_digest,
            "source_root_identity_digest": self.source_root_identity_digest,
            "source_tree_digest": plan.source_tree_digest,
            "inventory_objects": plan.inventory_object_count,
            "normalized_version_records": plan.normalized_version_count,
            "publication_candidates": plan.candidate_count,
            "version_alias_records": plan.normalized_version_count - plan.candidate_count,
            "version_alias_metadata_conflicts": int(connection.execute("SELECT count(*) FROM processed WHERE json_extract(decision_json,'$.alias_metadata_conflict')=1").fetchone()[0]),
            "publication_dispositions": dispositions,
            "published_versions": int(connection.execute("SELECT count(*) FROM processed WHERE item_json IS NOT NULL").fetchone()[0]),
            "snapshot_bindings": raw_bindings + projection_bindings,
            "raw_snapshot_bindings": raw_bindings,
            "projection_snapshot_bindings": projection_bindings,
            "snapshot_exact_replays": replays,
            "new_snapshot_uploads": raw_bindings + projection_bindings - replays,
            "parser_required_versions": parser_rows,
            "parsed_versions": parser_rows,
            "sections": sections,
            "chunks": chunks,
            "coverage_gaps": gap_count,
            "coverage_overlaps": overlap_count,
            "prompt_injection_findings": findings,
            "temporal_validations": int(connection.execute("SELECT count(*) FROM processed WHERE temporal_json IS NOT NULL").fetchone()[0]),
            "jurisdiction_validations": int(connection.execute("SELECT count(*) FROM processed WHERE jurisdiction_json IS NOT NULL").fetchone()[0]),
            "complete_provenance_chains": int(connection.execute("SELECT count(*) FROM processed WHERE provenance_json IS NOT NULL").fetchone()[0]),
            "publication_conflicts": int(connection.execute("SELECT count(*) FROM input_conflicts").fetchone()[0]),
            "exclusion_reason_counts": reason_counts,
            "resume_count": resume_count,
            "started_at": started_at.isoformat(),
            "completed_at": completed_at.isoformat(),
            "source_tree_writes": 0,
            "source_files_modified": 0,
            "source_files_deleted": 0,
            "aws_writes_outside_approved_s3_boundary": 0,
            "s3_deletes": 0,
            "public_acl_changes": 0,
            "model_calls": 0,
            "ocr_operations": 0,
            "web_corpus_downloads": 0,
            "embeddings_created": 0,
            "retrieval_indexes_created": 0,
            "final_question_selected": False,
            "forbidden_scenario_hardcoded": False,
            "step17_started": False,
            "logical_output_digest": logical_output_digest,
        }
        base["summary_digest"] = canonical_sha256(base)
        return base

    def _completed_result(self) -> Step16PublicationResult:
        if self.bundle_root is None or self.run_id is None:
            raise Step16PublicationSafetyError("Step 16 completion cannot be resolved without a plan", sanitized_code="STEP16_PLAN_REQUIRED")
        verification = verify_german_law_publication_bundle(self.bundle_root)
        manifest = _mapping(
            _strict_json(
                self._read_bounded_regular(self.bundle_root / "artifact-manifest.json", maximum_bytes=4 * 1024 * 1024),
                field_name="Step 16 manifest",
                maximum_bytes=4 * 1024 * 1024,
            ),
            field_name="Step 16 manifest",
        )
        summary = _mapping(
            _strict_json(
                self._read_bounded_regular(self.bundle_root / "run-summary.json", maximum_bytes=4 * 1024 * 1024),
                field_name="Step 16 summary",
                maximum_bytes=4 * 1024 * 1024,
            ),
            field_name="Step 16 summary",
        )
        return Step16PublicationResult(self.run_id, summary, manifest, verification)

    def execute(self, plan: Step16PublicationPlan, *, snapshot_writer: SnapshotWriter) -> Step16PublicationResult:
        """Publish one full fixed candidate set through the injected Step 7 writer.

        The SQLite file is only an interruption spool.  It grants no semantic
        authority and is removed after a verified external artifact manifest is
        finalized.  Every S3 write is delegated to the existing exact-byte,
        Object-Lock enforcing adapter.
        """

        if not callable(snapshot_writer):
            raise TypeError("snapshot_writer must be callable")
        if self.run_id != plan.run_id or self.bundle_root is None or self._source_tree_digest != plan.source_tree_digest:
            raise Step16PublicationReplayConflictError("Step 16 execution facts differ from its plan", sanitized_code="STEP16_PLAN_MISMATCH")
        tree_before, _counts_before = self._tree_fingerprint()
        if tree_before != plan.source_tree_digest:
            raise Step16PublicationReplayConflictError("source corpus changed after Step 16 planning", sanitized_code="STEP16_SOURCE_TREE_CHANGED")
        bundle_root, checkpoints = self._ensure_bundle_root()
        if (bundle_root / "artifact-manifest.json").exists():
            return self._completed_result()
        inputs, context = self._load_input_context()
        state_path = checkpoints / "publication-state.sqlite3"
        with closing(self._connect(state_path)) as connection:
            started_at, captured_at, resume_count = self._initialize_state(connection, plan)
            self._record_step15_conflicts(connection, context)
            self._process_candidates(
                connection,
                inputs,
                context,
                captured_at=captured_at,
                snapshot_writer=snapshot_writer,
            )
            queries = {
                "publication-eligibility.jsonl": "SELECT decision_json FROM processed ORDER BY version_identity",
                "publication-items.jsonl": "SELECT item_json FROM processed WHERE item_json IS NOT NULL ORDER BY version_identity",
                "publication-exclusions.jsonl": "SELECT exclusion_json FROM processed WHERE exclusion_json IS NOT NULL ORDER BY version_identity",
                "snapshot-bindings.jsonl": "SELECT raw_binding_json FROM processed WHERE raw_binding_json IS NOT NULL UNION ALL SELECT projection_binding_json FROM processed WHERE projection_binding_json IS NOT NULL ORDER BY 1",
                "provenance-chains.jsonl": "SELECT provenance_json FROM processed WHERE provenance_json IS NOT NULL ORDER BY version_identity",
                "parser-coverage.jsonl": "SELECT parser_json FROM processed WHERE parser_json IS NOT NULL ORDER BY version_identity",
                "chunk-coverage.jsonl": "SELECT chunk_json FROM processed WHERE chunk_json IS NOT NULL ORDER BY version_identity",
                "temporal-validation.jsonl": "SELECT temporal_json FROM processed WHERE temporal_json IS NOT NULL ORDER BY version_identity",
                "jurisdiction-validation.jsonl": "SELECT jurisdiction_json FROM processed WHERE jurisdiction_json IS NOT NULL ORDER BY version_identity",
                "publication-conflicts.jsonl": "SELECT conflict_json FROM input_conflicts ORDER BY conflict_id",
            }
            generated: list[tuple[str, int, str]] = []
            for name in self._OUTPUT_JSONL:
                generated.append((name, *self._write_query_jsonl(connection, name=name, query=queries[name])))
            logical_output_digest = self._logical_digest(generated)
            completed_at = ensure_utc(self.clock(), "Step 16 clock")
            summary = self._summary(
                connection,
                plan,
                logical_output_digest=logical_output_digest,
                started_at=started_at,
                completed_at=completed_at,
                resume_count=resume_count,
            )
            summary_length, summary_digest = self._write_atomic_absent(bundle_root / "run-summary.json", canonical_json_bytes(summary) + b"\n")
            generated.append(("run-summary.json", summary_length, summary_digest))
            candidate_set_digest = canonical_sha256(
                tuple((item.version_identity, item.input_digest) for item in inputs)
            )
            exclusion_set_digest = canonical_sha256(
                tuple(row[0] for row in connection.execute("SELECT exclusion_json FROM processed WHERE exclusion_json IS NOT NULL ORDER BY version_identity"))
            )
            batch = {
                "schema_version": STEP16_PUBLICATION_SCHEMA_VERSION,
                "publication_policy_version": STEP16_BATCH_POLICY_VERSION,
                "batch_id": f"publication-batch-{canonical_sha256({'run_id': plan.run_id, 'candidate_set_digest': candidate_set_digest, 'policy_digest': self.policy.policy_digest})}",
                "status": "COMPLETED",
                "german_law_hat_id": "german-law",
                "german_law_hat_version": "1.0.0",
                "step14_manifest_digest": self.step14_manifest_digest,
                "step15_manifest_digest": self.step15_manifest_digest,
                "source_root_identity_digest": self.source_root_identity_digest,
                "eligible_candidate_set_digest": candidate_set_digest,
                "excluded_candidate_set_digest": exclusion_set_digest,
                "publication_item_count": summary["published_versions"],
                "publication_decisions_digest": generated[0][2],
                "s3_snapshot_set_digest": generated[3][2],
                "parser_coverage_digest": generated[5][2],
                "chunk_coverage_digest": generated[6][2],
                "temporal_validation_digest": generated[7][2],
                "jurisdiction_validation_digest": generated[8][2],
                "conflict_report_digest": generated[9][2],
                "gap_report_digest": canonical_sha256({"dispositions": summary["publication_dispositions"], "reasons": summary["exclusion_reason_counts"]}),
                "operator_authorization_boundary": "TRUSTED_PUBLICATION_SERVICE_ONLY",
            }
            batch["batch_content_digest"] = canonical_sha256(batch)
            batch_length, batch_digest = self._write_atomic_absent(bundle_root / "publication-batch.json", canonical_json_bytes(batch) + b"\n")
            generated.append(("publication-batch.json", batch_length, batch_digest))
            completion = {
                "run_id": plan.run_id,
                "plan_digest": plan.plan_digest,
                "policy_digest": self.policy.policy_digest,
                "step14_manifest_digest": self.step14_manifest_digest,
                "step15_manifest_digest": self.step15_manifest_digest,
                "source_tree_digest": plan.source_tree_digest,
                "logical_output_digest": logical_output_digest,
                "summary_digest": summary["summary_digest"],
                "batch_content_digest": batch["batch_content_digest"],
                "completed_at": completed_at.isoformat(),
                "state_spool_authority": "NONE",
            }
            completion_length, completion_digest = self._write_atomic_absent(checkpoints / "completion.json", canonical_json_bytes(completion) + b"\n")
            generated.append(("checkpoints/completion.json", completion_length, completion_digest))
            connection.execute("UPDATE metadata SET value='true' WHERE key='completed'")
            connection.commit()
        run = {
            "run_id": plan.run_id,
            "starting_head": self.starting_head,
            "source_root_identity_digest": self.source_root_identity_digest,
            "source_tree_digest": plan.source_tree_digest,
            "step14_manifest_digest": self.step14_manifest_digest,
            "step15_manifest_digest": self.step15_manifest_digest,
            "device_reference": self.device_reference,
            "publication_policy_digest": self.policy.policy_digest,
            "started_at": started_at.isoformat(),
            "completed_at": completed_at.isoformat(),
            "resume_count": resume_count,
        }
        manifest = {
            "schema_version": STEP16_PUBLICATION_SCHEMA_VERSION,
            "run": run,
            "summary_digest": summary["summary_digest"],
            "logical_output_digest": logical_output_digest,
            "generated_files": [list(value) for value in sorted(generated)],
            "artifact_root_reference": f"corpora/manifests/step16/{plan.run_id}",
        }
        manifest["manifest_digest"] = canonical_sha256(manifest)
        self._write_atomic_absent(bundle_root / "artifact-manifest.json", canonical_json_bytes(manifest) + b"\n")
        verification = verify_german_law_publication_bundle(bundle_root)
        tree_after, _counts_after = self._tree_fingerprint()
        if tree_after != plan.source_tree_digest:
            raise Step16PublicationReplayConflictError("source tree changed during Step 16 publication", sanitized_code="STEP16_SOURCE_TREE_CHANGED")
        for suffix in ("-wal", "-shm", ""):
            target = Path(str(state_path) + suffix)
            if target.exists():
                try:
                    target.unlink()
                except OSError as exc:
                    raise Step16PublicationSafetyError("Step 16 owned checkpoint spool could not be removed", sanitized_code="STEP16_CHECKPOINT_CLEANUP_FAILED") from exc
        return Step16PublicationResult(plan.run_id, summary, manifest, verification)


def verify_german_law_publication_bundle(bundle_root: Path) -> dict[str, Any]:
    """Verify completed Step 16 evidence without trusting its own claims."""

    manifest_path = bundle_root / "artifact-manifest.json"
    if bundle_root.is_symlink() or not bundle_root.is_dir() or manifest_path.is_symlink() or not manifest_path.is_file():
        raise Step16PublicationSafetyError("Step 16 bundle is incomplete", sanitized_code="STEP16_OUTPUT_BUNDLE_INCOMPLETE")
    manifest = _mapping(
        _strict_json(
            GermanLawPublicationEngine._read_bounded_regular(manifest_path, maximum_bytes=4 * 1024 * 1024),
            field_name="Step 16 manifest",
            maximum_bytes=4 * 1024 * 1024,
        ),
        field_name="Step 16 manifest",
    )
    if manifest.get("schema_version") != STEP16_PUBLICATION_SCHEMA_VERSION or manifest.get("manifest_digest") != canonical_sha256(manifest, exclude_fields=("manifest_digest",)):
        raise Step16PublicationReplayConflictError("Step 16 manifest digest mismatches content", sanitized_code="STEP16_OUTPUT_DIGEST_MISMATCH")
    run = _mapping(manifest.get("run"), field_name="Step 16 manifest run")
    _identifier(run.get("run_id"), "Step 16 run ID", 255)
    for field_name in (
        "source_root_identity_digest",
        "source_tree_digest",
        "step14_manifest_digest",
        "step15_manifest_digest",
        "publication_policy_digest",
    ):
        _digest(run.get(field_name), field_name)
    generated_raw = manifest.get("generated_files")
    if not isinstance(generated_raw, list):
        raise Step16PublicationSafetyError("Step 16 generated file manifest is invalid", sanitized_code="STEP16_OUTPUT_SCHEMA_INVALID")
    generated: list[tuple[str, int, str]] = []
    for value in generated_raw:
        if not isinstance(value, list) or len(value) != 3:
            raise Step16PublicationSafetyError("Step 16 generated file record is invalid", sanitized_code="STEP16_OUTPUT_SCHEMA_INVALID")
        relative = _relative_path(value[0], "generated file path")
        length = value[1]
        if not isinstance(length, int) or isinstance(length, bool) or length < 0:
            raise Step16PublicationSafetyError("Step 16 generated file length is invalid", sanitized_code="STEP16_OUTPUT_SCHEMA_INVALID")
        generated.append((relative, length, _digest(value[2], "generated file digest")))
    if len({entry[0] for entry in generated}) != len(generated):
        raise Step16PublicationReplayConflictError("Step 16 generated file names are duplicated", sanitized_code="STEP16_OUTPUT_DIGEST_MISMATCH")
    verified: list[dict[str, Any]] = []
    for relative, length, digest in generated:
        target = bundle_root / relative
        if target.is_symlink() or not target.is_file() or target.stat().st_size != length:
            raise Step16PublicationReplayConflictError("Step 16 artifact length differs from the manifest", sanitized_code="STEP16_OUTPUT_DIGEST_MISMATCH")
        actual = GermanLawPublicationEngine._sha256_file(target)
        if actual != digest:
            raise Step16PublicationReplayConflictError("Step 16 artifact digest differs from the manifest", sanitized_code="STEP16_OUTPUT_DIGEST_MISMATCH")
        verified.append({"relative_path": relative, "byte_length": length, "sha256": actual})
    part_files = tuple(path.relative_to(bundle_root).as_posix() for path in bundle_root.rglob("*.part"))
    if part_files:
        raise Step16PublicationReplayConflictError("Step 16 bundle contains partial outputs", sanitized_code="STEP16_OUTPUT_PARTIAL_RESIDUE")
    required = set(GermanLawPublicationEngine._OUTPUT_JSONL) | {"run-summary.json", "publication-batch.json", "checkpoints/completion.json"}
    actual_names = {entry[0] for entry in generated}
    if not required.issubset(actual_names):
        raise Step16PublicationSafetyError("Step 16 bundle lacks a required output", sanitized_code="STEP16_OUTPUT_BUNDLE_INCOMPLETE")
    summary = _mapping(
        _strict_json(
            GermanLawPublicationEngine._read_bounded_regular(bundle_root / "run-summary.json", maximum_bytes=4 * 1024 * 1024),
            field_name="Step 16 summary",
            maximum_bytes=4 * 1024 * 1024,
        ),
        field_name="Step 16 summary",
    )
    if summary.get("summary_digest") != canonical_sha256(summary, exclude_fields=("summary_digest",)):
        raise Step16PublicationReplayConflictError("Step 16 summary digest mismatches content", sanitized_code="STEP16_OUTPUT_DIGEST_MISMATCH")
    if summary.get("run_id") != run.get("run_id") or summary.get("step14_manifest_digest") != run.get("step14_manifest_digest") or summary.get("step15_manifest_digest") != run.get("step15_manifest_digest"):
        raise Step16PublicationReplayConflictError("Step 16 summary is not bound to its manifest inputs", sanitized_code="STEP16_OUTPUT_INPUT_MISMATCH")
    jsonl_generated = tuple(entry for entry in generated if entry[0] in GermanLawPublicationEngine._OUTPUT_JSONL)
    if summary.get("logical_output_digest") != canonical_sha256(tuple(sorted(jsonl_generated))) or manifest.get("logical_output_digest") != summary.get("logical_output_digest"):
        raise Step16PublicationReplayConflictError("Step 16 logical output digest mismatches content", sanitized_code="STEP16_OUTPUT_DIGEST_MISMATCH")
    batch = _mapping(
        _strict_json(
            GermanLawPublicationEngine._read_bounded_regular(bundle_root / "publication-batch.json", maximum_bytes=4 * 1024 * 1024),
            field_name="Step 16 publication batch",
            maximum_bytes=4 * 1024 * 1024,
        ),
        field_name="Step 16 publication batch",
    )
    if batch.get("batch_content_digest") != canonical_sha256(batch, exclude_fields=("batch_content_digest",)):
        raise Step16PublicationReplayConflictError("Step 16 publication batch digest mismatches content", sanitized_code="STEP16_OUTPUT_DIGEST_MISMATCH")
    if batch.get("step14_manifest_digest") != run.get("step14_manifest_digest") or batch.get("step15_manifest_digest") != run.get("step15_manifest_digest"):
        raise Step16PublicationReplayConflictError("Step 16 publication batch is not bound to input manifests", sanitized_code="STEP16_OUTPUT_INPUT_MISMATCH")
    for name in GermanLawPublicationEngine._OUTPUT_JSONL:
        for _record in _read_jsonl(bundle_root / name):
            pass
    return {
        "status": "PASS",
        "run_id": run["run_id"],
        "manifest_digest": manifest["manifest_digest"],
        "logical_output_digest": manifest["logical_output_digest"],
        "summary_digest": summary["summary_digest"],
        "batch_content_digest": batch["batch_content_digest"],
        "verified_files": verified,
    }
