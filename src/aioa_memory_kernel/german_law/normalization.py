"""Deterministic Step 15 temporal and jurisdictional normalization.

The boundary consumes one verified Step 14 inventory bundle.  It deliberately
does not scan arbitrary source material, infer legal meaning, publish a
source, or resolve a question at a point in time.  The only second source read
is a bounded, hash-bound ``law_record.json`` explicitly listed by Step 14.

Large output is an external-volume manifest bundle.  SQLite is used only as a
Step-15-owned restart spool; it is removed after a completed canonical bundle
has been materialised.  It is never an authority for legal facts or a
CockroachDB replacement.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
from collections import Counter
from contextlib import closing
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable, Iterator, Mapping

from aioa_memory_kernel.contracts.serialization import (
    canonical_json_bytes,
    canonical_sha256,
    ensure_utc,
    require_sha256_hex,
    sha256_hex,
    to_canonical_data,
)
from aioa_memory_kernel.corpus import CorpusInventoryEngine, verify_inventory_bundle

from .models import GermanLegalSourceClass, LegalJurisdiction


TEMPORAL_JURISDICTION_SCHEMA_VERSION = "1.0.0"
TEMPORAL_JURISDICTION_POLICY_ID = "german-law-temporal-jurisdiction-normalization-1a"
# 1a.1 preserves valid facts when one optional descriptive version marker is
# oversized.  The marker becomes review evidence rather than discarding the
# entire hash-bound source metadata record.
TEMPORAL_NORMALIZATION_RULE_VERSION = "german-law-temporal-normalization-1a.1"
JURISDICTION_NORMALIZATION_RULE_VERSION = "german-law-jurisdiction-normalization-1a"
DOCUMENT_VERSION_NORMALIZATION_RULE_VERSION = "german-law-document-version-normalization-1a"
CONFLICT_NORMALIZATION_RULE_VERSION = "german-law-normalization-conflict-rules-1a"
SUPERSESSION_NORMALIZATION_RULE_VERSION = "german-law-supersession-candidates-1a"

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@+/-]{0,511}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_YEAR = re.compile(r"^(?P<year>[0-9]{4})$")
_MONTH = re.compile(r"^(?P<year>[0-9]{4})-(?P<month>0[1-9]|1[0-2])$")
_DATE = re.compile(r"^(?P<year>[0-9]{4})-(?P<month>0[1-9]|1[0-2])-(?P<day>0[1-9]|[12][0-9]|3[01])$")
_COMPACT_TIMESTAMP = re.compile(
    r"^(?P<year>[0-9]{4})(?P<month>0[1-9]|1[0-2])(?P<day>0[1-9]|[12][0-9]|3[01])"
    r"(?P<hour>[01][0-9]|2[0-3])(?P<minute>[0-5][0-9])(?P<second>[0-5][0-9])$"
)
_ISO_DATETIME = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?(?:Z|[+-][0-9]{2}:[0-9]{2})?$"
)
_GERMAN_DATE = re.compile(
    r"^(?P<day>[1-9]|[12][0-9]|3[01])\.\s*(?P<month>[A-Za-zäöüÄÖÜ]+)\s+(?P<year>[0-9]{4})$"
)
_GERMAN_MONTHS = {
    "januar": 1,
    "februar": 2,
    "märz": 3,
    "maerz": 3,
    "april": 4,
    "mai": 5,
    "juni": 6,
    "juli": 7,
    "august": 8,
    "september": 9,
    "oktober": 10,
    "november": 11,
    "dezember": 12,
}
_STATE_CODES = frozenset(
    {
        "DE-BW",
        "DE-BY",
        "DE-BE",
        "DE-BB",
        "DE-HB",
        "DE-HH",
        "DE-HE",
        "DE-MV",
        "DE-NI",
        "DE-NW",
        "DE-RP",
        "DE-SL",
        "DE-SN",
        "DE-ST",
        "DE-SH",
        "DE-TH",
    }
)


class TemporalJurisdictionNormalizationError(RuntimeError):
    """Base sanitized failure for the Step 15 boundary."""

    def __init__(self, message: str, *, sanitized_code: str) -> None:
        super().__init__(message)
        self.sanitized_code = sanitized_code


class TemporalJurisdictionSafetyError(TemporalJurisdictionNormalizationError):
    """A source or external-derived boundary failed closed."""


class TemporalJurisdictionReplayConflictError(TemporalJurisdictionNormalizationError):
    """A checkpoint or completed output conflicts with immutable facts."""


class TemporalFactType(str, Enum):
    PUBLISHED_AT = "PUBLISHED_AT"
    PROMULGATED_AT = "PROMULGATED_AT"
    ADOPTED_AT = "ADOPTED_AT"
    EFFECTIVE_FROM = "EFFECTIVE_FROM"
    EFFECTIVE_TO = "EFFECTIVE_TO"
    APPLICABLE_FROM = "APPLICABLE_FROM"
    APPLICABLE_TO = "APPLICABLE_TO"
    DECISION_DATE = "DECISION_DATE"
    RETRIEVED_AT = "RETRIEVED_AT"
    INGESTED_AT = "INGESTED_AT"
    VERIFIED_AT = "VERIFIED_AT"
    SUPERSEDED_AT = "SUPERSEDED_AT"
    EXECUTED_AT = "EXECUTED_AT"
    REPEAL_DATE = "REPEAL_DATE"
    SOURCE_BUILD_AT = "SOURCE_BUILD_AT"
    CURRENTNESS_CHECKED_AT = "CURRENTNESS_CHECKED_AT"


class TemporalPrecision(str, Enum):
    YEAR = "YEAR"
    MONTH = "MONTH"
    DATE = "DATE"
    DATETIME = "DATETIME"


class TimezoneStatus(str, Enum):
    NOT_APPLICABLE = "NOT_APPLICABLE"
    EXPLICIT_OFFSET = "EXPLICIT_OFFSET"
    UNKNOWN = "UNKNOWN"


class NormalizationEvidenceClass(str, Enum):
    STRUCTURED_DOCUMENT_METADATA = "STRUCTURED_DOCUMENT_METADATA"
    STEP14_SOURCE_REGISTRY_CANDIDATE = "STEP14_SOURCE_REGISTRY_CANDIDATE"
    EXPLICIT_DOCUMENT_RELATION = "EXPLICIT_DOCUMENT_RELATION"
    STEP14_EXACT_DUPLICATE_EVIDENCE = "STEP14_EXACT_DUPLICATE_EVIDENCE"
    STEP14_NEAR_DUPLICATE_EVIDENCE = "STEP14_NEAR_DUPLICATE_EVIDENCE"


class FactVerificationStatus(str, Enum):
    DECLARED = "DECLARED"
    VERIFIED = "VERIFIED"
    UNVERIFIED = "UNVERIFIED"
    CONFLICTING = "CONFLICTING"
    UNKNOWN = "UNKNOWN"


class NormalizationStatus(str, Enum):
    CLEAR = "CLEAR"
    AMBIGUOUS = "AMBIGUOUS"
    CONFLICTING = "CONFLICTING"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    QUARANTINED = "QUARANTINED"
    UNKNOWN = "UNKNOWN"


class SupersessionRelationshipType(str, Enum):
    DECLARED_SUPERSEDES = "DECLARED_SUPERSEDES"
    DECLARED_SUPERSEDED_BY = "DECLARED_SUPERSEDED_BY"
    NEAR_DUPLICATE_REVIEW_ONLY = "NEAR_DUPLICATE_REVIEW_ONLY"


class NormalizationConflictType(str, Enum):
    STEP14_INPUT_DIGEST_MISMATCH = "STEP14_INPUT_DIGEST_MISMATCH"
    STEP14_SOURCE_TREE_CHANGED = "STEP14_SOURCE_TREE_CHANGED"
    SOURCE_BYTES_MISMATCH = "SOURCE_BYTES_MISMATCH"
    METADATA_JSON_INVALID = "METADATA_JSON_INVALID"
    METADATA_FIELD_INVALID = "METADATA_FIELD_INVALID"
    INVALID_TEMPORAL_VALUE = "INVALID_TEMPORAL_VALUE"
    EFFECTIVE_INTERVAL_INVALID = "EFFECTIVE_INTERVAL_INVALID"
    APPLICABLE_INTERVAL_INVALID = "APPLICABLE_INTERVAL_INVALID"
    INTERVAL_PRECISION_OR_TIMEZONE_INCOMPARABLE = "INTERVAL_PRECISION_OR_TIMEZONE_INCOMPARABLE"
    SUPERSESSION_PRECEDES_LEGAL_TIME = "SUPERSESSION_PRECEDES_LEGAL_TIME"
    VERIFICATION_PRECEDES_RETRIEVAL = "VERIFICATION_PRECEDES_RETRIEVAL"
    JURISDICTION_VALUE_UNKNOWN = "JURISDICTION_VALUE_UNKNOWN"
    STATE_JURISDICTION_STATE_CODE_MISSING = "STATE_JURISDICTION_STATE_CODE_MISSING"
    SOURCE_REGISTRY_JURISDICTION_CONFLICT = "SOURCE_REGISTRY_JURISDICTION_CONFLICT"
    SAME_OFFICIAL_IDENTIFIER_INCOMPATIBLE_JURISDICTION = "SAME_OFFICIAL_IDENTIFIER_INCOMPATIBLE_JURISDICTION"
    SAME_VERSION_ID_DIFFERENT_RAW_CONTENT = "SAME_VERSION_ID_DIFFERENT_RAW_CONTENT"
    SAME_DOCUMENT_DIFFERENT_RAW_NO_VERSION_DISTINCTION = "SAME_DOCUMENT_DIFFERENT_RAW_NO_VERSION_DISTINCTION"
    EXACT_DUPLICATE_METADATA_CONFLICT = "EXACT_DUPLICATE_METADATA_CONFLICT"
    SUPERSESSION_TARGET_UNRESOLVED = "SUPERSESSION_TARGET_UNRESOLVED"
    SOURCE_REGISTRY_METADATA_CONFLICT = "SOURCE_REGISTRY_METADATA_CONFLICT"
    DOCUMENT_IDENTITY_UNKNOWN = "DOCUMENT_IDENTITY_UNKNOWN"
    STEP14_QUARANTINED_INPUT = "STEP14_QUARANTINED_INPUT"


class ConflictSeverity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    MATERIAL = "MATERIAL"


_TEMPORAL_SOURCE_FIELDS: tuple[tuple[str, TemporalFactType], ...] = (
    ("publication_date", TemporalFactType.PUBLISHED_AT),
    ("promulgation_date", TemporalFactType.PROMULGATED_AT),
    ("adopted_at", TemporalFactType.ADOPTED_AT),
    ("effective_from", TemporalFactType.EFFECTIVE_FROM),
    ("effective_to", TemporalFactType.EFFECTIVE_TO),
    ("applicable_from", TemporalFactType.APPLICABLE_FROM),
    ("applicable_to", TemporalFactType.APPLICABLE_TO),
    ("decision_date", TemporalFactType.DECISION_DATE),
    ("source_retrieved_at", TemporalFactType.RETRIEVED_AT),
    ("ingested_at", TemporalFactType.INGESTED_AT),
    # A repository/provider currentness check is operational evidence.  It
    # must never be silently elevated into legal/authenticity verification.
    ("currentness_checked_at", TemporalFactType.CURRENTNESS_CHECKED_AT),
    ("verified_at", TemporalFactType.VERIFIED_AT),
    ("superseded_at", TemporalFactType.SUPERSEDED_AT),
    ("ausfertigung_datum", TemporalFactType.EXECUTED_AT),
    ("repeal_date", TemporalFactType.REPEAL_DATE),
    ("gii_builddate", TemporalFactType.SOURCE_BUILD_AT),
)


def _text(value: object, field_name: str, maximum: int = 1024) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > maximum
        or "\x00" in value
        or any(ord(character) < 32 for character in value)
    ):
        raise ValueError(f"{field_name} must be bounded canonical text")
    return value


def _optional_text(value: object | None, field_name: str, maximum: int = 1024) -> str | None:
    return None if value is None else _text(value, field_name, maximum)


def _digest(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")
    require_sha256_hex(value, field_name)
    return value


def _optional_digest(value: object | None, field_name: str) -> str | None:
    return None if value is None else _digest(value, field_name)


def _relative_path(value: object, field_name: str = "relative_path") -> str:
    text = _text(value, field_name, 4096)
    parsed = PurePosixPath(text)
    if (
        parsed.is_absolute()
        or str(parsed) != text
        or "\\" in text
        or any(item in {"", ".", ".."} for item in parsed.parts)
    ):
        raise ValueError(f"{field_name} must be a safe root-relative POSIX path")
    return text


def _identifiers(values: Iterable[object], field_name: str, maximum: int = 128) -> tuple[str, ...]:
    result = tuple(sorted({_text(value, field_name, 512) for value in values}))
    if len(result) > maximum:
        raise ValueError(f"{field_name} exceeds its bounded item count")
    return result


def _enum(value: object, expected: type[Enum], field_name: str) -> Enum:
    if not isinstance(value, expected):
        raise ValueError(f"{field_name} must be {expected.__name__}")
    return value


def _strict_json(payload: bytes, *, field_name: str, maximum_bytes: int) -> Any:
    if len(payload) > maximum_bytes:
        raise TemporalJurisdictionSafetyError(
            "bounded JSON payload is too large",
            sanitized_code="STEP15_METADATA_SIZE_LIMIT",
        )
    if b"\x00" in payload:
        raise TemporalJurisdictionSafetyError(
            "JSON payload contains a NUL byte",
            sanitized_code="STEP15_METADATA_NUL",
        )

    class DuplicateMember(ValueError):
        pass

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in items:
            if key in result:
                raise DuplicateMember(key)
            result[key] = item
        return result

    def nonfinite(value: str) -> None:
        raise ValueError(value)

    try:
        return json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=pairs,
            parse_constant=nonfinite,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, DuplicateMember, ValueError) as exc:
        raise TemporalJurisdictionSafetyError(
            f"{field_name} is not strict JSON",
            sanitized_code="STEP15_METADATA_JSON_INVALID",
        ) from exc


def _bounded_mapping(value: object, *, field_name: str, maximum_items: int = 128) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or len(value) > maximum_items:
        raise TemporalJurisdictionSafetyError(
            f"{field_name} is not a bounded object",
            sanitized_code="STEP15_METADATA_FIELD_INVALID",
        )
    if any(not isinstance(key, str) or not key or len(key) > 128 for key in value):
        raise TemporalJurisdictionSafetyError(
            f"{field_name} has an invalid key",
            sanitized_code="STEP15_METADATA_FIELD_INVALID",
        )
    return value


def _validate_json_bounds(
    value: object,
    *,
    maximum_depth: int,
    maximum_items: int,
    field_name: str,
) -> None:
    """Bound a decoded metadata value without executing or coercing it.

    The Step-14 inventory is trusted only as an immutable file listing, not as
    a reason to accept arbitrarily deep or wide JSON from the source tree.
    An iterative walk avoids recursion on adversarial content.
    """

    pending: list[tuple[object, int]] = [(value, 1)]
    observed_items = 0
    while pending:
        current, depth = pending.pop()
        if depth > maximum_depth:
            raise TemporalJurisdictionSafetyError(
                f"{field_name} exceeds its nesting bound",
                sanitized_code="STEP15_METADATA_DEPTH_LIMIT",
            )
        if isinstance(current, Mapping):
            observed_items += len(current)
            for key, item in current.items():
                if not isinstance(key, str) or len(key) > 128 or "\x00" in key:
                    raise TemporalJurisdictionSafetyError(
                        f"{field_name} has an invalid key",
                        sanitized_code="STEP15_METADATA_FIELD_INVALID",
                    )
                pending.append((item, depth + 1))
        elif isinstance(current, (list, tuple)):
            observed_items += len(current)
            pending.extend((item, depth + 1) for item in current)
        elif isinstance(current, str):
            if len(current) > 4096 or "\x00" in current:
                raise TemporalJurisdictionSafetyError(
                    f"{field_name} has an oversized or unsafe scalar",
                    sanitized_code="STEP15_METADATA_FIELD_INVALID",
                )
        elif current is not None and not isinstance(current, (bool, int, float)):
            raise TemporalJurisdictionSafetyError(
                f"{field_name} has an unsupported JSON value",
                sanitized_code="STEP15_METADATA_FIELD_INVALID",
            )
        if observed_items > maximum_items:
            raise TemporalJurisdictionSafetyError(
                f"{field_name} exceeds its item bound",
                sanitized_code="STEP15_METADATA_ITEM_LIMIT",
            )


@dataclass(frozen=True, slots=True)
class TemporalJurisdictionNormalizationPolicy:
    schema_version: str = TEMPORAL_JURISDICTION_SCHEMA_VERSION
    policy_id: str = TEMPORAL_JURISDICTION_POLICY_ID
    temporal_rule_version: str = TEMPORAL_NORMALIZATION_RULE_VERSION
    jurisdiction_rule_version: str = JURISDICTION_NORMALIZATION_RULE_VERSION
    document_version_rule_version: str = DOCUMENT_VERSION_NORMALIZATION_RULE_VERSION
    conflict_rule_version: str = CONFLICT_NORMALIZATION_RULE_VERSION
    supersession_rule_version: str = SUPERSESSION_NORMALIZATION_RULE_VERSION
    maximum_metadata_bytes: int = 512 * 1024
    metadata_read_chunk_bytes: int = 64 * 1024
    checkpoint_batch_size: int = 128
    maximum_metadata_depth: int = 12
    maximum_metadata_items: int = 512
    maximum_relations_per_record: int = 32
    source_tree_writes_allowed: bool = False
    automatic_publication_allowed: bool = False
    automatic_verification_allowed: bool = False
    network_allowed: bool = False
    model_calls_allowed: bool = False

    def __post_init__(self) -> None:
        if self.schema_version != TEMPORAL_JURISDICTION_SCHEMA_VERSION:
            raise ValueError("unsupported Step 15 schema version")
        for name in (
            "policy_id",
            "temporal_rule_version",
            "jurisdiction_rule_version",
            "document_version_rule_version",
            "conflict_rule_version",
            "supersession_rule_version",
        ):
            _text(getattr(self, name), name, 255)
        for name, minimum, maximum in (
            ("maximum_metadata_bytes", 1024, 4 * 1024 * 1024),
            ("metadata_read_chunk_bytes", 4096, 1024 * 1024),
            ("checkpoint_batch_size", 1, 10_000),
            ("maximum_metadata_depth", 1, 32),
            ("maximum_metadata_items", 1, 10_000),
            ("maximum_relations_per_record", 1, 256),
        ):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
                raise ValueError(f"{name} is outside the bounded policy")
        if (
            self.source_tree_writes_allowed
            or self.automatic_publication_allowed
            or self.automatic_verification_allowed
            or self.network_allowed
            or self.model_calls_allowed
        ):
            raise ValueError("Step 15 policy cannot grant an authority-bearing action")

    @property
    def policy_digest(self) -> str:
        return canonical_sha256(self)


@dataclass(frozen=True, slots=True)
class NormalizedTemporalValue:
    raw_value: str
    normalized_value: str
    precision: TemporalPrecision
    timezone_status: TimezoneStatus
    value_digest: str = field(init=False)

    def __post_init__(self) -> None:
        raw = _text(self.raw_value, "raw_value", 128)
        normalized = _text(self.normalized_value, "normalized_value", 128)
        _enum(self.precision, TemporalPrecision, "precision")
        _enum(self.timezone_status, TimezoneStatus, "timezone_status")
        if self.precision is not TemporalPrecision.DATETIME and self.timezone_status is not TimezoneStatus.NOT_APPLICABLE:
            raise ValueError("only datetime values can have a timezone status")
        if self.precision is TemporalPrecision.DATETIME and self.timezone_status is TimezoneStatus.NOT_APPLICABLE:
            raise ValueError("datetime values need an explicit timezone status")
        object.__setattr__(self, "raw_value", raw)
        object.__setattr__(self, "normalized_value", normalized)
        object.__setattr__(self, "value_digest", canonical_sha256(self, exclude_fields=("value_digest",)))


@dataclass(frozen=True, slots=True)
class TemporalNormalizationRecord:
    inventory_record_id: str
    source_root_identity_digest: str
    step14_manifest_digest: str
    source_registry_candidate_id: str | None
    document_identity: str | None
    version_identity: str | None
    fact_type: TemporalFactType
    source_field: str
    value: NormalizedTemporalValue
    evidence_class: NormalizationEvidenceClass
    verification_status: FactVerificationStatus
    normalization_status: NormalizationStatus
    finding_codes: tuple[str, ...] = ()
    temporal_record_id: str = field(init=False)
    normalization_digest: str = field(init=False)

    def __post_init__(self) -> None:
        for name in ("inventory_record_id", "source_field"):
            object.__setattr__(self, name, _text(getattr(self, name), name, 512))
        for name in ("source_root_identity_digest", "step14_manifest_digest"):
            object.__setattr__(self, name, _digest(getattr(self, name), name))
        for name in ("source_registry_candidate_id", "document_identity", "version_identity"):
            object.__setattr__(self, name, _optional_text(getattr(self, name), name, 512))
        _enum(self.fact_type, TemporalFactType, "fact_type")
        if not isinstance(self.value, NormalizedTemporalValue):
            raise ValueError("value must be a normalized temporal value")
        _enum(self.evidence_class, NormalizationEvidenceClass, "evidence_class")
        _enum(self.verification_status, FactVerificationStatus, "verification_status")
        _enum(self.normalization_status, NormalizationStatus, "normalization_status")
        object.__setattr__(self, "finding_codes", _identifiers(self.finding_codes, "finding code", 32))
        identity = canonical_sha256(
            {
                "inventory_record_id": self.inventory_record_id,
                "fact_type": self.fact_type.value,
                "source_field": self.source_field,
                "value_digest": self.value.value_digest,
                "policy": TEMPORAL_NORMALIZATION_RULE_VERSION,
            }
        )
        object.__setattr__(self, "temporal_record_id", f"temporal-fact-{identity}")
        object.__setattr__(self, "normalization_digest", canonical_sha256(self, exclude_fields=("normalization_digest",)))


@dataclass(frozen=True, slots=True)
class JurisdictionNormalizationRecord:
    inventory_record_id: str
    source_root_identity_digest: str
    step14_manifest_digest: str
    source_registry_candidate_id: str | None
    document_identity: str | None
    version_identity: str | None
    raw_jurisdiction: str | None
    normalized_jurisdiction: LegalJurisdiction | None
    federal_state_code: str | None
    source_field: str
    evidence_class: NormalizationEvidenceClass
    verification_status: FactVerificationStatus
    normalization_status: NormalizationStatus
    finding_codes: tuple[str, ...] = ()
    jurisdiction_record_id: str = field(init=False)
    normalization_digest: str = field(init=False)

    def __post_init__(self) -> None:
        for name in ("inventory_record_id", "source_field"):
            object.__setattr__(self, name, _text(getattr(self, name), name, 512))
        for name in ("source_root_identity_digest", "step14_manifest_digest"):
            object.__setattr__(self, name, _digest(getattr(self, name), name))
        for name in ("source_registry_candidate_id", "document_identity", "version_identity", "raw_jurisdiction", "federal_state_code"):
            object.__setattr__(self, name, _optional_text(getattr(self, name), name, 512))
        if self.normalized_jurisdiction is not None:
            _enum(self.normalized_jurisdiction, LegalJurisdiction, "normalized_jurisdiction")
        if self.normalized_jurisdiction is LegalJurisdiction.DE_STATE:
            if self.federal_state_code not in _STATE_CODES:
                raise ValueError("DE_STATE jurisdiction requires one canonical state code")
        elif self.federal_state_code is not None:
            raise ValueError("federal state code is valid only for DE_STATE")
        _enum(self.evidence_class, NormalizationEvidenceClass, "evidence_class")
        _enum(self.verification_status, FactVerificationStatus, "verification_status")
        _enum(self.normalization_status, NormalizationStatus, "normalization_status")
        object.__setattr__(self, "finding_codes", _identifiers(self.finding_codes, "finding code", 32))
        identity = canonical_sha256(
            {
                "inventory_record_id": self.inventory_record_id,
                "raw_jurisdiction": self.raw_jurisdiction,
                "normalized_jurisdiction": None if self.normalized_jurisdiction is None else self.normalized_jurisdiction.value,
                "federal_state_code": self.federal_state_code,
                "source_field": self.source_field,
                "policy": JURISDICTION_NORMALIZATION_RULE_VERSION,
            }
        )
        object.__setattr__(self, "jurisdiction_record_id", f"jurisdiction-fact-{identity}")
        object.__setattr__(self, "normalization_digest", canonical_sha256(self, exclude_fields=("normalization_digest",)))


@dataclass(frozen=True, slots=True)
class DocumentVersionRecord:
    inventory_record_id: str
    source_root_identity_digest: str
    step14_manifest_digest: str
    source_registry_candidate_ids: tuple[str, ...]
    document_identity: str | None
    version_key_identity: str | None
    version_identity: str | None
    official_identifier: str | None
    source_class: str | None
    raw_content_sha256: str | None
    normalized_content_sha256: str | None
    jurisdiction_digest: str | None
    temporal_facts_digest: str | None
    version_status: str | None
    version_basis: str | None
    consolidation_status: str | None
    status: NormalizationStatus
    finding_codes: tuple[str, ...] = ()
    version_digest: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "inventory_record_id", _text(self.inventory_record_id, "inventory_record_id", 512))
        for name in ("source_root_identity_digest", "step14_manifest_digest"):
            object.__setattr__(self, name, _digest(getattr(self, name), name))
        object.__setattr__(self, "source_registry_candidate_ids", _identifiers(self.source_registry_candidate_ids, "source registry candidate ID", 128))
        for name in (
            "document_identity",
            "version_key_identity",
            "version_identity",
            "official_identifier",
            "source_class",
            "version_status",
            "version_basis",
            "consolidation_status",
        ):
            object.__setattr__(self, name, _optional_text(getattr(self, name), name, 512))
        for name in ("raw_content_sha256", "normalized_content_sha256", "jurisdiction_digest", "temporal_facts_digest"):
            object.__setattr__(self, name, _optional_digest(getattr(self, name), name))
        _enum(self.status, NormalizationStatus, "status")
        object.__setattr__(self, "finding_codes", _identifiers(self.finding_codes, "finding code", 64))
        object.__setattr__(self, "version_digest", canonical_sha256(self, exclude_fields=("version_digest",)))


@dataclass(frozen=True, slots=True)
class SupersessionCandidate:
    relationship_type: SupersessionRelationshipType
    predecessor_version_identity: str | None
    successor_version_identity: str | None
    related_identifier: str | None
    source_inventory_record_id: str
    source_field: str
    evidence_class: NormalizationEvidenceClass
    supporting_evidence_digest: str
    effective_boundary_digest: str | None
    superseded_at_digest: str | None
    verification_status: FactVerificationStatus
    conflict_markers: tuple[str, ...]
    human_review_required: bool = True
    relationship_id: str = field(init=False)
    relationship_digest: str = field(init=False)

    def __post_init__(self) -> None:
        _enum(self.relationship_type, SupersessionRelationshipType, "relationship_type")
        for name in ("predecessor_version_identity", "successor_version_identity", "related_identifier"):
            object.__setattr__(self, name, _optional_text(getattr(self, name), name, 512))
        if self.predecessor_version_identity is None and self.successor_version_identity is None:
            raise ValueError("supersession candidate needs at least one version identity")
        object.__setattr__(self, "source_inventory_record_id", _text(self.source_inventory_record_id, "source_inventory_record_id", 512))
        object.__setattr__(self, "source_field", _text(self.source_field, "source_field", 512))
        _enum(self.evidence_class, NormalizationEvidenceClass, "evidence_class")
        object.__setattr__(self, "supporting_evidence_digest", _digest(self.supporting_evidence_digest, "supporting_evidence_digest"))
        for name in ("effective_boundary_digest", "superseded_at_digest"):
            object.__setattr__(self, name, _optional_digest(getattr(self, name), name))
        _enum(self.verification_status, FactVerificationStatus, "verification_status")
        object.__setattr__(self, "conflict_markers", _identifiers(self.conflict_markers, "conflict marker", 32))
        if not self.human_review_required:
            raise ValueError("Step 15 supersession candidates always require human review")
        identity = canonical_sha256(
            {
                "relationship_type": self.relationship_type.value,
                "predecessor": self.predecessor_version_identity,
                "successor": self.successor_version_identity,
                "related_identifier": self.related_identifier,
                "source_inventory_record_id": self.source_inventory_record_id,
                "source_field": self.source_field,
                "rule": SUPERSESSION_NORMALIZATION_RULE_VERSION,
            }
        )
        object.__setattr__(self, "relationship_id", f"supersession-candidate-{identity}")
        object.__setattr__(self, "relationship_digest", canonical_sha256(self, exclude_fields=("relationship_digest",)))


@dataclass(frozen=True, slots=True)
class NormalizationConflict:
    conflict_type: NormalizationConflictType
    involved_inventory_record_ids: tuple[str, ...]
    involved_source_registry_candidate_ids: tuple[str, ...]
    involved_version_identities: tuple[str, ...]
    evidence_digests: tuple[str, ...]
    severity: ConflictSeverity
    resolution_status: NormalizationStatus
    human_review_required: bool
    reason_codes: tuple[str, ...]
    conflict_id: str = field(init=False)
    conflict_digest: str = field(init=False)

    def __post_init__(self) -> None:
        _enum(self.conflict_type, NormalizationConflictType, "conflict_type")
        object.__setattr__(self, "involved_inventory_record_ids", _identifiers(self.involved_inventory_record_ids, "inventory record ID", 1024))
        object.__setattr__(self, "involved_source_registry_candidate_ids", _identifiers(self.involved_source_registry_candidate_ids, "source registry candidate ID", 1024))
        object.__setattr__(self, "involved_version_identities", _identifiers(self.involved_version_identities, "version identity", 1024))
        object.__setattr__(self, "evidence_digests", tuple(sorted(_digest(item, "conflict evidence digest") for item in self.evidence_digests)))
        if not self.evidence_digests:
            raise ValueError("conflict requires at least one evidence digest")
        _enum(self.severity, ConflictSeverity, "severity")
        _enum(self.resolution_status, NormalizationStatus, "resolution_status")
        if self.resolution_status is NormalizationStatus.CLEAR:
            raise ValueError("a conflict cannot be silently clear")
        if not self.human_review_required:
            raise ValueError("Step 15 material conflicts retain human review")
        object.__setattr__(self, "reason_codes", _identifiers(self.reason_codes, "reason code", 64))
        identity = canonical_sha256(
            {
                "type": self.conflict_type.value,
                "inventory_records": self.involved_inventory_record_ids,
                "source_candidates": self.involved_source_registry_candidate_ids,
                "versions": self.involved_version_identities,
                "evidence": self.evidence_digests,
                "rule": CONFLICT_NORMALIZATION_RULE_VERSION,
            }
        )
        object.__setattr__(self, "conflict_id", f"normalization-conflict-{identity}")
        object.__setattr__(self, "conflict_digest", canonical_sha256(self, exclude_fields=("conflict_digest",)))


@dataclass(frozen=True, slots=True)
class SourceRegistryNormalizationProposal:
    source_registry_candidate_id: str
    inventory_record_id: str
    document_identity: str | None
    version_identity: str | None
    jurisdiction_normalization_digest: str | None
    temporal_facts_digest: str | None
    normalization_status: NormalizationStatus
    reason_codes: tuple[str, ...]
    automatic_update_allowed: bool = False
    proposal_id: str = field(init=False)
    proposal_digest: str = field(init=False)

    def __post_init__(self) -> None:
        for name in ("source_registry_candidate_id", "inventory_record_id"):
            object.__setattr__(self, name, _text(getattr(self, name), name, 512))
        for name in ("document_identity", "version_identity"):
            object.__setattr__(self, name, _optional_text(getattr(self, name), name, 512))
        for name in ("jurisdiction_normalization_digest", "temporal_facts_digest"):
            object.__setattr__(self, name, _optional_digest(getattr(self, name), name))
        _enum(self.normalization_status, NormalizationStatus, "normalization_status")
        object.__setattr__(self, "reason_codes", _identifiers(self.reason_codes, "reason code", 64))
        if self.automatic_update_allowed:
            raise ValueError("Step 15 cannot update a source registry automatically")
        identity = canonical_sha256(
            {
                "source_registry_candidate_id": self.source_registry_candidate_id,
                "inventory_record_id": self.inventory_record_id,
                "version_identity": self.version_identity,
                "jurisdiction_normalization_digest": self.jurisdiction_normalization_digest,
                "temporal_facts_digest": self.temporal_facts_digest,
            }
        )
        object.__setattr__(self, "proposal_id", f"source-normalization-proposal-{identity}")
        object.__setattr__(self, "proposal_digest", canonical_sha256(self, exclude_fields=("proposal_digest",)))


@dataclass(frozen=True, slots=True)
class TemporalJurisdictionNormalizationRun:
    run_id: str
    starting_head: str
    source_root_identity_digest: str
    source_tree_digest: str
    step14_manifest_digest: str
    step14_summary_digest: str
    device_reference: str
    policy_digest: str
    started_at: datetime
    completed_at: datetime | None
    resume_count: int
    run_digest: str = field(init=False)

    def __post_init__(self) -> None:
        for name in ("run_id", "starting_head", "device_reference"):
            object.__setattr__(self, name, _text(getattr(self, name), name, 512))
        for name in (
            "source_root_identity_digest",
            "source_tree_digest",
            "step14_manifest_digest",
            "step14_summary_digest",
            "policy_digest",
        ):
            object.__setattr__(self, name, _digest(getattr(self, name), name))
        object.__setattr__(self, "started_at", ensure_utc(self.started_at, "started_at"))
        if self.completed_at is not None:
            object.__setattr__(self, "completed_at", ensure_utc(self.completed_at, "completed_at"))
        if not isinstance(self.resume_count, int) or isinstance(self.resume_count, bool) or self.resume_count < 0:
            raise ValueError("resume_count must be non-negative")
        object.__setattr__(self, "run_digest", canonical_sha256(self, exclude_fields=("run_digest",)))


@dataclass(frozen=True, slots=True)
class TemporalJurisdictionNormalizationSummary:
    run_id: str
    step14_manifest_digest: str
    source_root_identity_digest: str
    inventory_records_considered: int
    metadata_records_eligible: int
    metadata_records_ineligible: int
    metadata_records_quarantined: int
    records_normalized: int
    records_without_temporal_metadata: int
    records_without_jurisdiction_metadata: int
    temporal_fact_counts: Mapping[str, int]
    jurisdiction_counts: Mapping[str, int]
    document_identity_count: int
    version_identity_count: int
    version_relationship_count: int
    conflict_counts: Mapping[str, int]
    source_registry_proposal_count: int
    review_required_records: int
    source_tree_writes: int = 0
    source_files_modified: int = 0
    source_files_deleted: int = 0
    aws_writes: int = 0
    s3_writes: int = 0
    network_acquisitions: int = 0
    model_calls: int = 0
    ocr_operations: int = 0
    embeddings_created: int = 0
    publication_transitions: int = 0
    approval_transitions: int = 0
    step16_started: bool = False
    final_question_selected: bool = False
    forbidden_scenario_hardcoded: bool = False
    logical_output_digest: str = ""
    summary_digest: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", _text(self.run_id, "run_id", 512))
        object.__setattr__(self, "step14_manifest_digest", _digest(self.step14_manifest_digest, "step14_manifest_digest"))
        object.__setattr__(self, "source_root_identity_digest", _digest(self.source_root_identity_digest, "source_root_identity_digest"))
        for name in (
            "inventory_records_considered",
            "metadata_records_eligible",
            "metadata_records_ineligible",
            "metadata_records_quarantined",
            "records_normalized",
            "records_without_temporal_metadata",
            "records_without_jurisdiction_metadata",
            "document_identity_count",
            "version_identity_count",
            "version_relationship_count",
            "source_registry_proposal_count",
            "review_required_records",
            "source_tree_writes",
            "source_files_modified",
            "source_files_deleted",
            "aws_writes",
            "s3_writes",
            "network_acquisitions",
            "model_calls",
            "ocr_operations",
            "embeddings_created",
            "publication_transitions",
            "approval_transitions",
        ):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        for name in ("temporal_fact_counts", "jurisdiction_counts", "conflict_counts"):
            raw = getattr(self, name)
            if not isinstance(raw, Mapping):
                raise ValueError(f"{name} must be a mapping")
            normalized: dict[str, int] = {}
            for key, value in sorted(raw.items()):
                normalized[_text(key, f"{name} key", 255)] = value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else -1
            if any(value < 0 for value in normalized.values()):
                raise ValueError(f"{name} contains an invalid count")
            object.__setattr__(self, name, normalized)
        if any(
            (
                self.source_tree_writes,
                self.source_files_modified,
                self.source_files_deleted,
                self.aws_writes,
                self.s3_writes,
                self.network_acquisitions,
                self.model_calls,
                self.ocr_operations,
                self.embeddings_created,
                self.publication_transitions,
                self.approval_transitions,
            )
        ) or self.step16_started or self.final_question_selected or self.forbidden_scenario_hardcoded:
            raise ValueError("Step 15 summary contains a forbidden boundary action")
        object.__setattr__(self, "logical_output_digest", _digest(self.logical_output_digest, "logical_output_digest"))
        object.__setattr__(self, "summary_digest", canonical_sha256(self, exclude_fields=("summary_digest",)))


@dataclass(frozen=True, slots=True)
class TemporalJurisdictionNormalizationManifest:
    run: TemporalJurisdictionNormalizationRun
    summary_digest: str
    logical_output_digest: str
    generated_files: tuple[tuple[str, int, str], ...]
    artifact_root_reference: str
    manifest_digest: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.run, TemporalJurisdictionNormalizationRun):
            raise ValueError("run must be typed")
        object.__setattr__(self, "summary_digest", _digest(self.summary_digest, "summary_digest"))
        object.__setattr__(self, "logical_output_digest", _digest(self.logical_output_digest, "logical_output_digest"))
        generated: list[tuple[str, int, str]] = []
        for item in self.generated_files:
            if not isinstance(item, tuple) or len(item) != 3:
                raise ValueError("generated_files must contain file triplets")
            relative = _relative_path(item[0], "generated file path")
            length = item[1]
            if not isinstance(length, int) or isinstance(length, bool) or length < 0:
                raise ValueError("generated file length must be non-negative")
            generated.append((relative, length, _digest(item[2], "generated file digest")))
        if len({item[0] for item in generated}) != len(generated):
            raise ValueError("generated output paths must be unique")
        object.__setattr__(self, "generated_files", tuple(sorted(generated)))
        object.__setattr__(self, "artifact_root_reference", _relative_path(self.artifact_root_reference, "artifact_root_reference"))
        object.__setattr__(self, "manifest_digest", canonical_sha256(self, exclude_fields=("manifest_digest",)))


@dataclass(frozen=True, slots=True)
class TemporalJurisdictionNormalizationPlan:
    run_id: str
    step14_manifest_digest: str
    source_root_identity_digest: str
    source_tree_digest: str
    inventory_records: int
    metadata_records: int
    estimated_output_bytes: int
    policy_digest: str
    plan_digest: str


@dataclass(frozen=True, slots=True)
class TemporalJurisdictionNormalizationResult:
    run_id: str
    summary: TemporalJurisdictionNormalizationSummary
    manifest: TemporalJurisdictionNormalizationManifest
    verification: Mapping[str, Any]


def _normalise_temporal_value(raw: object) -> NormalizedTemporalValue:
    value = _text(raw, "temporal raw value", 128)
    matched = _YEAR.fullmatch(value)
    if matched is not None:
        date(int(matched["year"]), 1, 1)
        return NormalizedTemporalValue(value, value, TemporalPrecision.YEAR, TimezoneStatus.NOT_APPLICABLE)
    matched = _MONTH.fullmatch(value)
    if matched is not None:
        date(int(matched["year"]), int(matched["month"]), 1)
        return NormalizedTemporalValue(value, value, TemporalPrecision.MONTH, TimezoneStatus.NOT_APPLICABLE)
    matched = _DATE.fullmatch(value)
    if matched is not None:
        date(int(matched["year"]), int(matched["month"]), int(matched["day"]))
        return NormalizedTemporalValue(value, value, TemporalPrecision.DATE, TimezoneStatus.NOT_APPLICABLE)
    matched = _COMPACT_TIMESTAMP.fullmatch(value)
    if matched is not None:
        parsed = datetime(
            int(matched["year"]),
            int(matched["month"]),
            int(matched["day"]),
            int(matched["hour"]),
            int(matched["minute"]),
            int(matched["second"]),
        )
        return NormalizedTemporalValue(
            value,
            parsed.isoformat(timespec="seconds"),
            TemporalPrecision.DATETIME,
            TimezoneStatus.UNKNOWN,
        )
    matched = _GERMAN_DATE.fullmatch(value)
    if matched is not None:
        month = _GERMAN_MONTHS.get(matched["month"].casefold())
        if month is None:
            raise ValueError("unsupported German month")
        parsed = date(int(matched["year"]), month, int(matched["day"]))
        return NormalizedTemporalValue(value, parsed.isoformat(), TemporalPrecision.DATE, TimezoneStatus.NOT_APPLICABLE)
    if _ISO_DATETIME.fullmatch(value) is not None:
        if value.endswith("Z") or re.search(r"[+-][0-9]{2}:[0-9]{2}$", value) is not None:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ValueError("timestamp timezone is unexpectedly absent")
            normalized = parsed.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
            normalized = normalized.replace(".000000Z", "Z")
            return NormalizedTemporalValue(value, normalized, TemporalPrecision.DATETIME, TimezoneStatus.EXPLICIT_OFFSET)
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is not None:
            raise ValueError("timestamp timezone classification is inconsistent")
        return NormalizedTemporalValue(value, parsed.isoformat(timespec="microseconds").replace(".000000", ""), TemporalPrecision.DATETIME, TimezoneStatus.UNKNOWN)
    raise ValueError("unsupported deterministic temporal form")


def _temporal_sort_key(value: NormalizedTemporalValue) -> tuple[str, int, TimezoneStatus] | None:
    """Return only safe comparison keys; partial values retain their precision."""

    if value.precision is TemporalPrecision.YEAR:
        return value.normalized_value, 1, TimezoneStatus.NOT_APPLICABLE
    if value.precision is TemporalPrecision.MONTH:
        return value.normalized_value, 2, TimezoneStatus.NOT_APPLICABLE
    if value.precision is TemporalPrecision.DATE:
        return value.normalized_value, 3, TimezoneStatus.NOT_APPLICABLE
    return value.normalized_value, 4, value.timezone_status


def _compare_temporal_values(left: NormalizedTemporalValue, right: NormalizedTemporalValue) -> int | None:
    """Compare only values with equivalent precision and timezone semantics."""

    left_key = _temporal_sort_key(left)
    right_key = _temporal_sort_key(right)
    if left_key is None or right_key is None:
        return None
    if left_key[1:] != right_key[1:]:
        return None
    if left_key[0] < right_key[0]:
        return -1
    if left_key[0] > right_key[0]:
        return 1
    return 0


def _canonical_jurisdiction(value: object | None) -> LegalJurisdiction | None:
    if value is None:
        return None
    if not isinstance(value, str) or value != value.strip() or len(value) > 64:
        return None
    try:
        return LegalJurisdiction(value)
    except ValueError:
        return None


def _strict_relations(value: object, *, maximum: int) -> tuple[str, ...]:
    if value in (None, "", (), [], {}):
        return ()
    if isinstance(value, str):
        values: Iterable[object] = (value,)
    elif isinstance(value, (tuple, list)):
        values = value
    else:
        raise ValueError("relation value must be a string or bounded sequence")
    result: list[str] = []
    for item in values:
        if not isinstance(item, str) or not item.strip() or len(item) > 512 or "\x00" in item:
            raise ValueError("relation item is invalid")
        result.append(item.strip())
    if len(result) > maximum:
        raise ValueError("relation collection exceeds its bound")
    return tuple(sorted(set(result)))


def _safe_metadata_scalar(value: object | None) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str) or value != value.strip() or len(value) > 512 or "\x00" in value:
        raise ValueError("metadata scalar is invalid")
    return value


def _metadata_identifier(metadata: Mapping[str, Any]) -> str | None:
    for field_name in ("document_family_id", "fna_identifier", "eli_identifier"):
        value = _safe_metadata_scalar(metadata.get(field_name))
        if value is not None:
            return value
    return None


def _metadata_version_marker(metadata: Mapping[str, Any]) -> tuple[str | None, str | None, str | None]:
    def optional_marker(field_name: str) -> str | None:
        try:
            return _safe_metadata_scalar(metadata.get(field_name))
        except ValueError:
            return None

    return (
        optional_marker("version_status"),
        optional_marker("version_basis"),
        optional_marker("binding_status"),
    )


def _source_class_from_candidates(candidates: Iterable[Mapping[str, Any]]) -> str | None:
    values = {item.get("source_class") for item in candidates if isinstance(item.get("source_class"), str)}
    if len(values) == 1:
        value = next(iter(values))
        try:
            GermanLegalSourceClass(value)
        except ValueError:
            return None
        return value
    return None


def _candidate_jurisdictions(candidates: Iterable[Mapping[str, Any]]) -> tuple[str, ...]:
    return tuple(sorted({item["jurisdiction"] for item in candidates if isinstance(item.get("jurisdiction"), str) and item["jurisdiction"]}))


def _json_line_iter(path: Path, *, maximum_line_bytes: int = 4 * 1024 * 1024) -> Iterator[Mapping[str, Any]]:
    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0))
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise TemporalJurisdictionSafetyError("input artifact is not a regular file", sanitized_code="STEP15_INPUT_ARTIFACT_UNSAFE")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            for raw_line in stream:
                if len(raw_line) > maximum_line_bytes:
                    raise TemporalJurisdictionSafetyError("input JSONL record exceeds bound", sanitized_code="STEP15_INPUT_LINE_LIMIT")
                if not raw_line.endswith(b"\n"):
                    raise TemporalJurisdictionSafetyError("input JSONL line is unterminated", sanitized_code="STEP15_INPUT_JSON_INVALID")
                parsed = _strict_json(raw_line[:-1], field_name="Step 14 JSONL", maximum_bytes=maximum_line_bytes)
                yield _bounded_mapping(parsed, field_name="Step 14 JSONL record")
        after = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
            raise TemporalJurisdictionReplayConflictError("input JSONL artifact changed during read", sanitized_code="STEP15_INPUT_ARTIFACT_CHANGED")
    except OSError as exc:
        raise TemporalJurisdictionSafetyError("input JSONL artifact could not be read", sanitized_code="STEP15_INPUT_ARTIFACT_UNAVAILABLE") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


class _SafeSourceMetadataReader:
    """Read one Step-14-bound source metadata file without symlink traversal."""

    def __init__(self, source_root: Path, *, maximum_bytes: int, chunk_bytes: int) -> None:
        self.source_root = source_root
        self.maximum_bytes = maximum_bytes
        self.chunk_bytes = chunk_bytes
        try:
            root = source_root.lstat()
        except OSError as exc:
            raise TemporalJurisdictionSafetyError("source root is unavailable", sanitized_code="STEP15_SOURCE_ROOT_UNAVAILABLE") from exc
        if source_root.is_symlink() or not stat.S_ISDIR(root.st_mode):
            raise TemporalJurisdictionSafetyError("source root is not a direct directory", sanitized_code="STEP15_SOURCE_ROOT_UNSAFE")
        self.root_device = root.st_dev
        self.root_inode = root.st_ino

    def read(self, relative_path: str, *, expected_sha256: str) -> bytes:
        relative = _relative_path(relative_path)
        _digest(expected_sha256, "expected_sha256")
        parts = PurePosixPath(relative).parts
        root_fd: int | None = None
        current_fd: int | None = None
        file_fd: int | None = None
        try:
            root_fd = os.open(
                self.source_root,
                os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
            )
            root_opened = os.fstat(root_fd)
            if root_opened.st_dev != self.root_device or root_opened.st_ino != self.root_inode:
                raise TemporalJurisdictionReplayConflictError("source root identity changed", sanitized_code="STEP15_SOURCE_ROOT_CHANGED")
            current_fd = root_fd
            for part in parts[:-1]:
                next_fd = os.open(
                    part,
                    os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=current_fd,
                )
                opened = os.fstat(next_fd)
                if not stat.S_ISDIR(opened.st_mode) or opened.st_dev != self.root_device:
                    os.close(next_fd)
                    raise TemporalJurisdictionSafetyError("source metadata path is unsafe", sanitized_code="STEP15_SOURCE_PATH_ESCAPE")
                if current_fd != root_fd:
                    os.close(current_fd)
                current_fd = next_fd
            file_fd = os.open(parts[-1], os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0), dir_fd=current_fd)
            opened = os.fstat(file_fd)
            if not stat.S_ISREG(opened.st_mode) or opened.st_dev != self.root_device or opened.st_size > self.maximum_bytes:
                raise TemporalJurisdictionSafetyError("source metadata object is unsafe", sanitized_code="STEP15_SOURCE_METADATA_UNSAFE")
            payload = bytearray()
            digest = hashlib.sha256()
            while True:
                block = os.read(file_fd, self.chunk_bytes)
                if not block:
                    break
                payload.extend(block)
                if len(payload) > self.maximum_bytes:
                    raise TemporalJurisdictionSafetyError("source metadata exceeds its bound", sanitized_code="STEP15_METADATA_SIZE_LIMIT")
                digest.update(block)
            after = os.fstat(file_fd)
            if (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
                raise TemporalJurisdictionReplayConflictError("source metadata changed during read", sanitized_code="STEP15_SOURCE_METADATA_CHANGED")
            if digest.hexdigest() != expected_sha256:
                raise TemporalJurisdictionReplayConflictError("source metadata differs from Step 14 digest", sanitized_code="STEP15_SOURCE_DIGEST_MISMATCH")
            return bytes(payload)
        except OSError as exc:
            raise TemporalJurisdictionSafetyError("source metadata could not be read safely", sanitized_code="STEP15_SOURCE_METADATA_UNAVAILABLE") from exc
        finally:
            if file_fd is not None:
                os.close(file_fd)
            if current_fd is not None and current_fd != root_fd:
                os.close(current_fd)
            if root_fd is not None:
                os.close(root_fd)


class TemporalJurisdictionNormalizationEngine:
    """Normalize one fixed Step 14 snapshot into an external Step 15 bundle."""

    _OUTPUT_JSONL = (
        "temporal-normalization.jsonl",
        "jurisdiction-normalization.jsonl",
        "document-versions.jsonl",
        "supersession-candidates.jsonl",
        "normalization-conflicts.jsonl",
        "source-registry-normalization-proposals.jsonl",
    )

    def __init__(
        self,
        *,
        source_root: Path,
        step14_bundle_root: Path,
        bundle_parent: Path,
        device_reference: str,
        starting_head: str,
        policy: TemporalJurisdictionNormalizationPolicy | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        progress: Callable[[Mapping[str, int]], None] | None = None,
    ) -> None:
        self.source_root = source_root
        self.step14_bundle_root = step14_bundle_root
        self.bundle_parent = bundle_parent
        self.device_reference = _text(device_reference, "device_reference", 255)
        self.starting_head = _text(starting_head, "starting_head", 128)
        self.policy = policy or TemporalJurisdictionNormalizationPolicy()
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
        self.step14_manifest_digest = _digest(self._step14_manifest["manifest_digest"], "Step 14 manifest digest")
        self.step14_summary_digest = _digest(self._step14_manifest["summary_digest"], "Step 14 summary digest")
        self._step14_run = _bounded_mapping(self._step14_manifest["run"], field_name="Step 14 run")
        if self._step14_run.get("source_root_identity_digest") != self.source_root_identity_digest:
            raise TemporalJurisdictionReplayConflictError(
                "Step 14 source root identity differs from the live source root",
                sanitized_code="STEP15_SOURCE_ROOT_IDENTITY_MISMATCH",
            )
        self._source_tree_digest: str | None = None
        self.run_id: str | None = None
        self.bundle_root: Path | None = None

    def _verify_roots(self) -> os.stat_result:
        try:
            if self.source_root.resolve(strict=True) != self.source_root:
                raise TemporalJurisdictionSafetyError("source root has a symlinked component", sanitized_code="STEP15_SOURCE_ROOT_UNSAFE")
            source = self.source_root.lstat()
            if self.step14_bundle_root.resolve(strict=True) != self.step14_bundle_root:
                raise TemporalJurisdictionSafetyError("Step 14 bundle has a symlinked component", sanitized_code="STEP15_INPUT_BUNDLE_UNSAFE")
            bundle = self.step14_bundle_root.lstat()
            destination = self.bundle_parent if os.path.lexists(self.bundle_parent) else self.bundle_parent.parent
            if destination.resolve(strict=True) != destination:
                raise TemporalJurisdictionSafetyError("Step 15 parent has a symlinked component", sanitized_code="STEP15_OUTPUT_ROOT_UNSAFE")
            output = destination.lstat()
        except OSError as exc:
            raise TemporalJurisdictionSafetyError("Step 15 source or derived root is unavailable", sanitized_code="STEP15_ROOT_UNAVAILABLE") from exc
        if self.source_root.is_symlink() or not stat.S_ISDIR(source.st_mode):
            raise TemporalJurisdictionSafetyError("source root is unsafe", sanitized_code="STEP15_SOURCE_ROOT_UNSAFE")
        if self.step14_bundle_root.is_symlink() or not stat.S_ISDIR(bundle.st_mode):
            raise TemporalJurisdictionSafetyError("Step 14 bundle is unsafe", sanitized_code="STEP15_INPUT_BUNDLE_UNSAFE")
        if destination.is_symlink() or not stat.S_ISDIR(output.st_mode):
            raise TemporalJurisdictionSafetyError("Step 15 output parent is unsafe", sanitized_code="STEP15_OUTPUT_ROOT_UNSAFE")
        if source.st_dev != bundle.st_dev or source.st_dev != output.st_dev:
            raise TemporalJurisdictionSafetyError("source and derived data cross a filesystem boundary", sanitized_code="STEP15_EXTERNAL_FILESYSTEM_BOUNDARY_MISMATCH")
        return source

    @staticmethod
    def _read_bounded_regular(path: Path, *, maximum_bytes: int) -> bytes:
        descriptor: int | None = None
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0))
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode) or opened.st_size < 0 or opened.st_size > maximum_bytes:
                raise TemporalJurisdictionSafetyError("derived artifact is unsafe", sanitized_code="STEP15_INPUT_ARTIFACT_UNSAFE")
            payload = bytearray()
            while len(payload) < opened.st_size:
                block = os.read(descriptor, min(1024 * 1024, opened.st_size - len(payload)))
                if not block:
                    break
                payload.extend(block)
            after = os.fstat(descriptor)
            if (
                len(payload) != opened.st_size
                or (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
                != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            ):
                raise TemporalJurisdictionReplayConflictError("derived artifact changed during read", sanitized_code="STEP15_INPUT_ARTIFACT_CHANGED")
            return bytes(payload)
        except OSError as exc:
            raise TemporalJurisdictionSafetyError("derived artifact could not be read", sanitized_code="STEP15_INPUT_ARTIFACT_UNAVAILABLE") from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)

    @staticmethod
    def _sha256_file(path: Path) -> str:
        descriptor: int | None = None
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0))
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode):
                raise TemporalJurisdictionSafetyError("derived artifact is unsafe", sanitized_code="STEP15_OUTPUT_ARTIFACT_UNSAFE")
            digest = hashlib.sha256()
            count = 0
            while True:
                block = os.read(descriptor, 1024 * 1024)
                if not block:
                    break
                count += len(block)
                digest.update(block)
            after = os.fstat(descriptor)
            if count != opened.st_size or (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
                raise TemporalJurisdictionReplayConflictError("derived artifact changed during hash", sanitized_code="STEP15_OUTPUT_ARTIFACT_CHANGED")
            return digest.hexdigest()
        except OSError as exc:
            raise TemporalJurisdictionSafetyError("derived artifact could not be hashed", sanitized_code="STEP15_OUTPUT_ARTIFACT_UNAVAILABLE") from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def _load_step14_manifest(self) -> Mapping[str, Any]:
        verification = verify_inventory_bundle(self.step14_bundle_root)
        if verification["status"] != "PASS":
            raise TemporalJurisdictionReplayConflictError("Step 14 bundle did not verify", sanitized_code="STEP15_INPUT_BUNDLE_INVALID")
        manifest_path = self.step14_bundle_root / "inventory-manifest.json"
        manifest = _bounded_mapping(
            _strict_json(self._read_bounded_regular(manifest_path, maximum_bytes=4 * 1024 * 1024), field_name="Step 14 manifest", maximum_bytes=4 * 1024 * 1024),
            field_name="Step 14 manifest",
        )
        if manifest.get("manifest_digest") != canonical_sha256(manifest, exclude_fields=("manifest_digest",)):
            raise TemporalJurisdictionReplayConflictError("Step 14 manifest hash does not bind its content", sanitized_code="STEP15_INPUT_DIGEST_MISMATCH")
        if not isinstance(manifest.get("run"), Mapping) or not isinstance(manifest.get("generated_files"), list):
            raise TemporalJurisdictionSafetyError("Step 14 manifest has an invalid schema", sanitized_code="STEP15_INPUT_SCHEMA_INVALID")
        required = {
            "file-records.jsonl",
            "source-registration-candidates.jsonl",
            "exact-duplicate-groups.jsonl",
            "near-duplicate-candidates.jsonl",
            "quarantine-candidates.jsonl",
            "inventory-summary.json",
        }
        generated = {item[0] for item in manifest["generated_files"] if isinstance(item, list) and len(item) == 3 and isinstance(item[0], str)}
        if not required.issubset(generated):
            raise TemporalJurisdictionSafetyError("Step 14 bundle lacks a required input", sanitized_code="STEP15_INPUT_BUNDLE_INCOMPLETE")
        return manifest

    def _tree_fingerprint(self) -> tuple[str, Mapping[str, int]]:
        # Reuse the exact Step 14 lstat/O_NOFOLLOW walker and identity formula.
        probe = CorpusInventoryEngine(
            source_root=self.source_root,
            bundle_parent=self.step14_bundle_root.parent,
            device_reference=self.device_reference,
            starting_head=self.starting_head,
        )
        return probe.tree_fingerprint()

    def plan(self) -> TemporalJurisdictionNormalizationPlan:
        tree_digest, counts = self._tree_fingerprint()
        expected_tree_digest = self._step14_run.get("source_tree_after_digest")
        if tree_digest != expected_tree_digest:
            raise TemporalJurisdictionReplayConflictError("source tree differs from Step 14 snapshot", sanitized_code="STEP15_SOURCE_TREE_CHANGED")
        summary = _bounded_mapping(
            _strict_json(self._read_bounded_regular(self.step14_bundle_root / "inventory-summary.json", maximum_bytes=4 * 1024 * 1024), field_name="Step 14 summary", maximum_bytes=4 * 1024 * 1024),
            field_name="Step 14 summary",
        )
        if summary.get("summary_digest") != self.step14_summary_digest:
            raise TemporalJurisdictionReplayConflictError("Step 14 summary digest mismatches the manifest", sanitized_code="STEP15_INPUT_DIGEST_MISMATCH")
        inventory_records = int(summary.get("objects_observed", 0))
        metadata_records = sum(1 for record in _json_line_iter(self.step14_bundle_root / "file-records.jsonl") if str(record.get("relative_path", "")).endswith("/law_record.json"))
        run_id = "step15-" + canonical_sha256(
            {
                "step14_manifest_digest": self.step14_manifest_digest,
                "source_root_identity_digest": self.source_root_identity_digest,
                "source_tree_digest": tree_digest,
                "policy_digest": self.policy.policy_digest,
                "starting_head": self.starting_head,
            }
        )[:32]
        facts = {
            "run_id": run_id,
            "step14_manifest_digest": self.step14_manifest_digest,
            "source_root_identity_digest": self.source_root_identity_digest,
            "source_tree_digest": tree_digest,
            "inventory_records": inventory_records,
            "metadata_records": metadata_records,
            "estimated_output_bytes": max(4 * 1024 * 1024, metadata_records * 4096),
            "policy_digest": self.policy.policy_digest,
        }
        self._source_tree_digest = tree_digest
        self.run_id = run_id
        self.bundle_root = self.bundle_parent / run_id
        return TemporalJurisdictionNormalizationPlan(**facts, plan_digest=canonical_sha256(facts))

    @staticmethod
    def _connect(path: Path) -> sqlite3.Connection:
        connection = sqlite3.connect(path)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS candidates(
              candidate_id TEXT PRIMARY KEY,
              content_sha256 TEXT NOT NULL,
              candidate_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS candidates_by_content ON candidates(content_sha256);
            CREATE TABLE IF NOT EXISTS exact_groups(group_id TEXT PRIMARY KEY, group_json TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS near_candidates(candidate_id TEXT PRIMARY KEY, candidate_json TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS quarantine_decisions(
              decision_digest TEXT PRIMARY KEY,
              status TEXT NOT NULL,
              decision_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS processed_records(
              inventory_record_id TEXT PRIMARY KEY,
              relative_path TEXT NOT NULL,
              raw_sha256 TEXT NOT NULL,
              input_quarantine_status TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS temporal_records(record_id TEXT PRIMARY KEY, record_json TEXT NOT NULL, inventory_record_id TEXT NOT NULL, fact_type TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS temporal_by_inventory ON temporal_records(inventory_record_id);
            CREATE TABLE IF NOT EXISTS jurisdiction_records(record_id TEXT PRIMARY KEY, record_json TEXT NOT NULL, inventory_record_id TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS jurisdiction_by_inventory ON jurisdiction_records(inventory_record_id);
            CREATE TABLE IF NOT EXISTS versions(
              inventory_record_id TEXT PRIMARY KEY,
              document_identity TEXT,
              version_key_identity TEXT,
              version_identity TEXT,
              raw_content_sha256 TEXT,
              jurisdiction_json TEXT,
              source_candidate_ids_json TEXT NOT NULL,
              version_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS versions_by_document ON versions(document_identity);
            CREATE INDEX IF NOT EXISTS versions_by_key ON versions(version_key_identity);
            CREATE TABLE IF NOT EXISTS relation_inputs(
              source_inventory_record_id TEXT NOT NULL,
              source_version_identity TEXT,
              relation_field TEXT NOT NULL,
              related_identifier TEXT NOT NULL,
              evidence_digest TEXT NOT NULL,
              PRIMARY KEY(source_inventory_record_id, relation_field, related_identifier)
            );
            CREATE TABLE IF NOT EXISTS conflicts(conflict_id TEXT PRIMARY KEY, conflict_json TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS relationships(relationship_id TEXT PRIMARY KEY, relationship_json TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS proposals(proposal_id TEXT PRIMARY KEY, proposal_json TEXT NOT NULL);
            """
        )
        return connection

    @staticmethod
    def _serialized(value: object) -> str:
        return canonical_json_bytes(value).decode("utf-8")

    def _ensure_bundle_root(self) -> tuple[Path, Path]:
        if self.bundle_root is None or self.run_id is None:
            raise TemporalJurisdictionSafetyError("Step 15 plan was not created", sanitized_code="STEP15_PLAN_REQUIRED")
        # ``bundle_parent`` is the approved Step-15 namespace below the
        # already-verified Step-14-derived parent.  Create only this direct
        # child, never a recursive or source-tree path.
        if not self.bundle_parent.exists():
            try:
                os.mkdir(self.bundle_parent, mode=0o750)
                directory_fd = os.open(self.bundle_parent.parent, os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_DIRECTORY", 0))
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except FileExistsError:
                pass
            except OSError as exc:
                raise TemporalJurisdictionSafetyError("Step 15 namespace could not be created", sanitized_code="STEP15_OUTPUT_ROOT_CREATE_FAILED") from exc
        try:
            parent_stat = self.bundle_parent.lstat()
        except OSError as exc:
            raise TemporalJurisdictionSafetyError("Step 15 namespace is unavailable", sanitized_code="STEP15_OUTPUT_ROOT_UNSAFE") from exc
        if self.bundle_parent.is_symlink() or not stat.S_ISDIR(parent_stat.st_mode) or parent_stat.st_dev != self._root_stat.st_dev:
            raise TemporalJurisdictionSafetyError("Step 15 namespace is unsafe", sanitized_code="STEP15_OUTPUT_ROOT_UNSAFE")
        if self.bundle_root.exists():
            if self.bundle_root.is_symlink() or not self.bundle_root.is_dir():
                raise TemporalJurisdictionReplayConflictError("Step 15 output root is unsafe", sanitized_code="STEP15_OUTPUT_REPLAY_CONFLICT")
        else:
            try:
                os.mkdir(self.bundle_root, mode=0o750)
                directory_fd = os.open(self.bundle_parent, os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_DIRECTORY", 0))
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except OSError as exc:
                raise TemporalJurisdictionSafetyError("Step 15 output root could not be created", sanitized_code="STEP15_OUTPUT_ROOT_CREATE_FAILED") from exc
        checkpoints = self.bundle_root / "checkpoints"
        if not checkpoints.exists():
            try:
                os.mkdir(checkpoints, mode=0o750)
            except OSError as exc:
                raise TemporalJurisdictionSafetyError("Step 15 checkpoint root could not be created", sanitized_code="STEP15_CHECKPOINT_CREATE_FAILED") from exc
        if checkpoints.is_symlink() or not checkpoints.is_dir():
            raise TemporalJurisdictionSafetyError("Step 15 checkpoint root is unsafe", sanitized_code="STEP15_CHECKPOINT_UNSAFE")
        return self.bundle_root, checkpoints

    def _initialize_state(self, connection: sqlite3.Connection, plan: TemporalJurisdictionNormalizationPlan) -> tuple[datetime, int]:
        existing = dict(connection.execute("SELECT key, value FROM metadata"))
        if existing:
            expected = {
                "policy_digest": self.policy.policy_digest,
                "step14_manifest_digest": self.step14_manifest_digest,
                "source_root_identity_digest": self.source_root_identity_digest,
                "source_tree_digest": plan.source_tree_digest,
                "run_id": plan.run_id,
                "starting_head": self.starting_head,
            }
            for key, value in expected.items():
                if existing.get(key) != value:
                    raise TemporalJurisdictionReplayConflictError("Step 15 checkpoint belongs to incompatible immutable facts", sanitized_code="STEP15_INCOMPATIBLE_CHECKPOINT")
            if existing.get("completed") == "true":
                return datetime.fromisoformat(existing["started_at"]), int(existing.get("resume_count", "0"))
            resume_count = int(existing.get("resume_count", "0")) + 1
            connection.execute("UPDATE metadata SET value=? WHERE key='resume_count'", (str(resume_count),))
            connection.commit()
            return datetime.fromisoformat(existing["started_at"]), resume_count
        started_at = ensure_utc(self.clock(), "Step 15 clock")
        values = {
            "policy_digest": self.policy.policy_digest,
            "step14_manifest_digest": self.step14_manifest_digest,
            "source_root_identity_digest": self.source_root_identity_digest,
            "source_tree_digest": plan.source_tree_digest,
            "run_id": plan.run_id,
            "starting_head": self.starting_head,
            "started_at": started_at.isoformat(),
            "resume_count": "0",
            "completed": "false",
        }
        connection.executemany("INSERT INTO metadata(key,value) VALUES(?,?)", sorted(values.items()))
        connection.commit()
        return started_at, 0

    def _load_step14_auxiliaries(self, connection: sqlite3.Connection) -> None:
        if connection.execute("SELECT count(*) FROM candidates").fetchone()[0] == 0:
            for candidate in _json_line_iter(self.step14_bundle_root / "source-registration-candidates.jsonl"):
                candidate_id = _text(candidate.get("candidate_id"), "Step 14 candidate ID", 512)
                content_sha256 = _digest(candidate.get("content_sha256"), "Step 14 candidate content SHA-256")
                connection.execute(
                    "INSERT INTO candidates(candidate_id,content_sha256,candidate_json) VALUES(?,?,?)",
                    (candidate_id, content_sha256, self._serialized(candidate)),
                )
        if connection.execute("SELECT count(*) FROM exact_groups").fetchone()[0] == 0:
            for group in _json_line_iter(self.step14_bundle_root / "exact-duplicate-groups.jsonl"):
                group_id = _text(group.get("group_id"), "Step 14 exact group ID", 512)
                connection.execute("INSERT INTO exact_groups(group_id,group_json) VALUES(?,?)", (group_id, self._serialized(group)))
        if connection.execute("SELECT count(*) FROM near_candidates").fetchone()[0] == 0:
            for candidate in _json_line_iter(self.step14_bundle_root / "near-duplicate-candidates.jsonl"):
                candidate_id = _text(candidate.get("candidate_id"), "Step 14 near candidate ID", 512)
                connection.execute("INSERT INTO near_candidates(candidate_id,candidate_json) VALUES(?,?)", (candidate_id, self._serialized(candidate)))
        if connection.execute("SELECT count(*) FROM quarantine_decisions").fetchone()[0] == 0:
            for decision in _json_line_iter(self.step14_bundle_root / "quarantine-candidates.jsonl"):
                decision_digest = _digest(decision.get("decision_digest"), "Step 14 quarantine decision digest")
                status = _text(decision.get("status"), "Step 14 quarantine status", 64)
                if status not in {"REVIEW_REQUIRED", "QUARANTINED"}:
                    raise TemporalJurisdictionSafetyError("Step 14 quarantine status is unsupported", sanitized_code="STEP15_INPUT_SCHEMA_INVALID")
                serialized = self._serialized(decision)
                existing = connection.execute(
                    "SELECT decision_json FROM quarantine_decisions WHERE decision_digest=?",
                    (decision_digest,),
                ).fetchone()
                if existing is not None and existing[0] != serialized:
                    raise TemporalJurisdictionReplayConflictError(
                        "Step 14 quarantine decision digest is reused incompatibly",
                        sanitized_code="STEP15_INPUT_DIGEST_MISMATCH",
                    )
                connection.execute(
                    "INSERT OR IGNORE INTO quarantine_decisions(decision_digest,status,decision_json) VALUES(?,?,?)",
                    (decision_digest, status, serialized),
                )
        connection.commit()

    @staticmethod
    def _candidate_rows(connection: sqlite3.Connection, content_sha256: str | None) -> tuple[Mapping[str, Any], ...]:
        if content_sha256 is None:
            return ()
        rows = connection.execute("SELECT candidate_json FROM candidates WHERE content_sha256=? ORDER BY candidate_id", (content_sha256,))
        return tuple(_bounded_mapping(_strict_json(str(row[0]).encode("utf-8"), field_name="Step 14 candidate", maximum_bytes=256 * 1024), field_name="Step 14 candidate") for row in rows)

    @staticmethod
    def _record_conflict(connection: sqlite3.Connection, conflict: NormalizationConflict) -> None:
        serialized = TemporalJurisdictionNormalizationEngine._serialized(conflict)
        existing = connection.execute("SELECT conflict_json FROM conflicts WHERE conflict_id=?", (conflict.conflict_id,)).fetchone()
        if existing is not None and existing[0] != serialized:
            raise TemporalJurisdictionReplayConflictError("normalization conflict identity was reused incompatibly", sanitized_code="STEP15_CONFLICT_REPLAY_MISMATCH")
        connection.execute("INSERT OR IGNORE INTO conflicts(conflict_id,conflict_json) VALUES(?,?)", (conflict.conflict_id, serialized))

    def _emit_invalid_temporal_conflict(
        self,
        connection: sqlite3.Connection,
        *,
        inventory_record_id: str,
        candidate_ids: tuple[str, ...],
        source_field: str,
        raw_value: str,
    ) -> None:
        self._record_conflict(
            connection,
            NormalizationConflict(
                NormalizationConflictType.INVALID_TEMPORAL_VALUE,
                (inventory_record_id,),
                candidate_ids,
                (),
                (canonical_sha256({"source_field": source_field, "raw_value": raw_value}),),
                ConflictSeverity.WARNING,
                NormalizationStatus.REVIEW_REQUIRED,
                True,
                ("UNSUPPORTED_OR_INVALID_DETERMINISTIC_TEMPORAL_FORMAT",),
            ),
        )

    def _process_metadata_record(
        self,
        connection: sqlite3.Connection,
        reader: _SafeSourceMetadataReader,
        inventory: Mapping[str, Any],
    ) -> None:
        inventory_record_id = _text(inventory.get("record_id"), "inventory record ID", 512)
        if connection.execute("SELECT 1 FROM processed_records WHERE inventory_record_id=?", (inventory_record_id,)).fetchone() is not None:
            return
        relative_path = _relative_path(inventory.get("relative_path"))
        raw_sha256 = _digest(inventory.get("raw_sha256"), "Step 14 source metadata digest")
        decision_digest = inventory.get("quarantine_decision_digest")
        input_quarantine_status = "CLEAR"
        if decision_digest is not None:
            normalized_decision_digest = _digest(decision_digest, "Step 14 quarantine decision digest")
            decision_row = connection.execute(
                "SELECT status FROM quarantine_decisions WHERE decision_digest=?",
                (normalized_decision_digest,),
            ).fetchone()
            if decision_row is not None:
                input_quarantine_status = str(decision_row[0])
        if input_quarantine_status == "QUARANTINED":
            conflict = NormalizationConflict(
                NormalizationConflictType.STEP14_QUARANTINED_INPUT,
                (inventory_record_id,),
                (),
                (),
                (_digest(decision_digest, "Step 14 quarantine decision digest"),),
                ConflictSeverity.MATERIAL,
                NormalizationStatus.QUARANTINED,
                True,
                ("STEP14_LOGICAL_QUARANTINE_PREVENTS_METADATA_NORMALIZATION",),
            )
            self._record_conflict(connection, conflict)
            version = DocumentVersionRecord(
                inventory_record_id,
                self.source_root_identity_digest,
                self.step14_manifest_digest,
                (),
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                NormalizationStatus.QUARANTINED,
                ("STEP14_LOGICAL_QUARANTINE_PREVENTS_METADATA_NORMALIZATION",),
            )
            connection.execute(
                "INSERT INTO versions(inventory_record_id,document_identity,version_key_identity,version_identity,raw_content_sha256,jurisdiction_json,source_candidate_ids_json,version_json) VALUES(?,?,?,?,?,?,?,?)",
                (
                    inventory_record_id,
                    None,
                    None,
                    None,
                    None,
                    "null",
                    self._serialized(()),
                    self._serialized(version),
                ),
            )
            connection.execute(
                "INSERT INTO processed_records(inventory_record_id,relative_path,raw_sha256,input_quarantine_status) VALUES(?,?,?,?)",
                (inventory_record_id, relative_path, raw_sha256, input_quarantine_status),
            )
            return
        payload = reader.read(relative_path, expected_sha256=raw_sha256)
        metadata = _bounded_mapping(_strict_json(payload, field_name="source metadata", maximum_bytes=self.policy.maximum_metadata_bytes), field_name="source metadata", maximum_items=self.policy.maximum_metadata_items)
        _validate_json_bounds(
            metadata,
            maximum_depth=self.policy.maximum_metadata_depth,
            maximum_items=self.policy.maximum_metadata_items,
            field_name="source metadata",
        )
        source_sha = _safe_metadata_scalar(metadata.get("source_sha256"))
        if source_sha is not None and _SHA256.fullmatch(source_sha) is None:
            raise TemporalJurisdictionSafetyError("source metadata has an invalid source hash", sanitized_code="STEP15_METADATA_FIELD_INVALID")
        candidates = self._candidate_rows(connection, source_sha)
        candidate_ids = tuple(_text(item["candidate_id"], "candidate ID", 512) for item in candidates)
        candidate_source_class = _source_class_from_candidates(candidates)
        raw_identifier = _metadata_identifier(metadata)
        document_identity = None
        if raw_identifier is not None:
            document_identity = "legal-document-" + canonical_sha256(
                {
                    "official_identifier": raw_identifier,
                    "source_class": candidate_source_class,
                    "jurisdiction": _safe_metadata_scalar(metadata.get("jurisdiction_layer")),
                    "rule": DOCUMENT_VERSION_NORMALIZATION_RULE_VERSION,
                }
            )
        else:
            self._record_conflict(
                connection,
                NormalizationConflict(
                    NormalizationConflictType.DOCUMENT_IDENTITY_UNKNOWN,
                    (inventory_record_id,),
                    candidate_ids,
                    (),
                    (canonical_sha256({"inventory_record_id": inventory_record_id, "source_metadata_sha256": raw_sha256}),),
                    ConflictSeverity.WARNING,
                    NormalizationStatus.REVIEW_REQUIRED,
                    True,
                    ("OFFICIAL_IDENTIFIER_MISSING",),
                ),
            )

        raw_jurisdiction = _safe_metadata_scalar(metadata.get("jurisdiction_layer"))
        normalized_jurisdiction = _canonical_jurisdiction(raw_jurisdiction)
        state_code = _safe_metadata_scalar(metadata.get("federal_state_code"))
        jurisdiction_findings: list[str] = []
        jurisdiction_status = NormalizationStatus.CLEAR
        if raw_jurisdiction is None:
            candidate_values = _candidate_jurisdictions(candidates)
            if len(candidate_values) == 1:
                raw_jurisdiction = candidate_values[0]
                normalized_jurisdiction = _canonical_jurisdiction(raw_jurisdiction)
                evidence_class = NormalizationEvidenceClass.STEP14_SOURCE_REGISTRY_CANDIDATE
            elif len(candidate_values) > 1:
                evidence_class = NormalizationEvidenceClass.STEP14_SOURCE_REGISTRY_CANDIDATE
                normalized_jurisdiction = None
                jurisdiction_status = NormalizationStatus.CONFLICTING
                jurisdiction_findings.append("SOURCE_REGISTRY_JURISDICTION_AMBIGUOUS")
            else:
                evidence_class = NormalizationEvidenceClass.STRUCTURED_DOCUMENT_METADATA
                jurisdiction_status = NormalizationStatus.UNKNOWN
                jurisdiction_findings.append("JURISDICTION_UNKNOWN")
        else:
            evidence_class = NormalizationEvidenceClass.STRUCTURED_DOCUMENT_METADATA
        if raw_jurisdiction is not None and normalized_jurisdiction is None:
            jurisdiction_status = NormalizationStatus.REVIEW_REQUIRED
            jurisdiction_findings.append("JURISDICTION_VALUE_UNSUPPORTED")
            self._record_conflict(
                connection,
                NormalizationConflict(
                    NormalizationConflictType.JURISDICTION_VALUE_UNKNOWN,
                    (inventory_record_id,),
                    candidate_ids,
                    (),
                    (canonical_sha256({"raw_jurisdiction": raw_jurisdiction, "inventory_record_id": inventory_record_id}),),
                    ConflictSeverity.WARNING,
                    NormalizationStatus.REVIEW_REQUIRED,
                    True,
                    ("JURISDICTION_VALUE_UNSUPPORTED",),
                ),
            )
        if normalized_jurisdiction is LegalJurisdiction.DE_STATE and state_code not in _STATE_CODES:
            jurisdiction_status = NormalizationStatus.REVIEW_REQUIRED
            jurisdiction_findings.append("FEDERAL_STATE_CODE_MISSING")
            state_code = None
            self._record_conflict(
                connection,
                NormalizationConflict(
                    NormalizationConflictType.STATE_JURISDICTION_STATE_CODE_MISSING,
                    (inventory_record_id,),
                    candidate_ids,
                    (),
                    (canonical_sha256({"raw_jurisdiction": raw_jurisdiction, "inventory_record_id": inventory_record_id}),),
                    ConflictSeverity.MATERIAL,
                    NormalizationStatus.REVIEW_REQUIRED,
                    True,
                    ("DE_STATE_REQUIRES_EXPLICIT_STATE_CODE",),
                ),
            )
        elif normalized_jurisdiction is not LegalJurisdiction.DE_STATE:
            state_code = None
        candidate_jurisdictions = _candidate_jurisdictions(candidates)
        if normalized_jurisdiction is not None and candidate_jurisdictions and normalized_jurisdiction.value not in candidate_jurisdictions:
            jurisdiction_status = NormalizationStatus.CONFLICTING
            jurisdiction_findings.append("SOURCE_REGISTRY_JURISDICTION_CONFLICT")
            self._record_conflict(
                connection,
                NormalizationConflict(
                    NormalizationConflictType.SOURCE_REGISTRY_JURISDICTION_CONFLICT,
                    (inventory_record_id,),
                    candidate_ids,
                    (),
                    (canonical_sha256({"metadata": normalized_jurisdiction.value, "candidates": candidate_jurisdictions}),),
                    ConflictSeverity.MATERIAL,
                    NormalizationStatus.CONFLICTING,
                    True,
                    ("STRUCTURED_METADATA_AND_STEP14_REGISTRY_DISAGREE",),
                ),
            )
        version_marker_findings: list[str] = []
        for marker_field in ("version_status", "version_basis", "binding_status"):
            try:
                _safe_metadata_scalar(metadata.get(marker_field))
            except ValueError:
                version_marker_findings.append(f"{marker_field.upper()}_FIELD_INVALID")
                self._record_conflict(
                    connection,
                    NormalizationConflict(
                        NormalizationConflictType.METADATA_FIELD_INVALID,
                        (inventory_record_id,),
                        candidate_ids,
                        (),
                        (canonical_sha256({"inventory_record_id": inventory_record_id, "field": marker_field}),),
                        ConflictSeverity.WARNING,
                        NormalizationStatus.REVIEW_REQUIRED,
                        True,
                        ("OPTIONAL_VERSION_MARKER_REJECTED_FACTS_RETAINED",),
                    ),
                )
        # A legal-version identity intentionally precedes construction of the
        # normalized facts.  It binds immutable source/version facts, but not
        # the facts' operational serialization.  This lets every fact point
        # at its version without making the identity circular.
        version_status, version_basis, consolidation_status = _metadata_version_marker(metadata)
        raw_content_sha256 = source_sha
        normalized_content_sha256 = _safe_metadata_scalar(metadata.get("content_sha256"))
        if normalized_content_sha256 is not None and _SHA256.fullmatch(normalized_content_sha256) is None:
            normalized_content_sha256 = None
        version_key_identity = None
        version_identity = None
        if document_identity is not None:
            version_key_identity = "legal-version-key-" + canonical_sha256(
                {
                    "document_identity": document_identity,
                    "source_class": candidate_source_class,
                    "jurisdiction": None if normalized_jurisdiction is None else normalized_jurisdiction.value,
                    "version_status": version_status,
                    "version_basis": version_basis,
                    "consolidation_status": consolidation_status,
                    "rule": DOCUMENT_VERSION_NORMALIZATION_RULE_VERSION,
                }
            )
            version_identity = "legal-version-" + canonical_sha256(
                {
                    "version_key_identity": version_key_identity,
                    "raw_content_sha256": raw_content_sha256,
                    "normalized_content_sha256": normalized_content_sha256,
                    "rule": DOCUMENT_VERSION_NORMALIZATION_RULE_VERSION,
                }
            )
        jurisdiction = JurisdictionNormalizationRecord(
            inventory_record_id,
            self.source_root_identity_digest,
            self.step14_manifest_digest,
            candidate_ids[0] if len(candidate_ids) == 1 else None,
            document_identity,
            version_identity,
            raw_jurisdiction,
            normalized_jurisdiction,
            state_code,
            "jurisdiction_layer" if metadata.get("jurisdiction_layer") not in (None, "") else "step14.source_registration_candidate.jurisdiction",
            evidence_class,
            FactVerificationStatus.DECLARED if normalized_jurisdiction is not None else FactVerificationStatus.UNKNOWN,
            jurisdiction_status,
            tuple(jurisdiction_findings),
        )
        connection.execute(
            "INSERT INTO jurisdiction_records(record_id,record_json,inventory_record_id) VALUES(?,?,?)",
            (jurisdiction.jurisdiction_record_id, self._serialized(jurisdiction), inventory_record_id),
        )

        temporal_records: list[TemporalNormalizationRecord] = []
        for source_field, fact_type in _TEMPORAL_SOURCE_FIELDS:
            raw_value = metadata.get(source_field)
            if raw_value in (None, ""):
                continue
            try:
                normalized = _normalise_temporal_value(raw_value)
            except (TypeError, ValueError):
                self._emit_invalid_temporal_conflict(
                    connection,
                    inventory_record_id=inventory_record_id,
                    candidate_ids=candidate_ids,
                    source_field=source_field,
                    raw_value=str(raw_value)[:128],
                )
                continue
            findings: list[str] = []
            if fact_type is TemporalFactType.EXECUTED_AT:
                findings.append("EXECUTION_DATE_NOT_TREATED_AS_PROMULGATION_OR_EFFECT")
            elif fact_type is TemporalFactType.REPEAL_DATE:
                findings.append("REPEAL_DATE_NOT_AUTOMATICALLY_MAPPED_TO_EFFECTIVE_TO_OR_SUPERSEDED_AT")
            elif fact_type is TemporalFactType.SOURCE_BUILD_AT:
                findings.append("SOURCE_BUILD_TIME_IS_OPERATIONAL_METADATA_NOT_LEGAL_TIME")
            elif source_field == "currentness_checked_at":
                findings.append("CURRENTNESS_CHECK_IS_NOT_AUTHENTICITY_VERIFICATION")
            record = TemporalNormalizationRecord(
                inventory_record_id,
                self.source_root_identity_digest,
                self.step14_manifest_digest,
                candidate_ids[0] if len(candidate_ids) == 1 else None,
                document_identity,
                version_identity,
                fact_type,
                source_field,
                normalized,
                NormalizationEvidenceClass.STRUCTURED_DOCUMENT_METADATA,
                FactVerificationStatus.DECLARED,
                NormalizationStatus.CLEAR,
                tuple(findings),
            )
            temporal_records.append(record)
            connection.execute(
                "INSERT INTO temporal_records(record_id,record_json,inventory_record_id,fact_type) VALUES(?,?,?,?)",
                (record.temporal_record_id, self._serialized(record), inventory_record_id, fact_type.value),
            )

        facts_by_type = {record.fact_type: record for record in temporal_records}
        self._validate_temporal_relationships(connection, inventory_record_id, candidate_ids, facts_by_type)
        temporal_digest = canonical_sha256(tuple(record.normalization_digest for record in sorted(temporal_records, key=lambda value: value.temporal_record_id)))
        version = DocumentVersionRecord(
            inventory_record_id,
            self.source_root_identity_digest,
            self.step14_manifest_digest,
            candidate_ids,
            document_identity,
            version_key_identity,
            version_identity,
            raw_identifier,
            candidate_source_class,
            raw_content_sha256,
            normalized_content_sha256,
            jurisdiction.normalization_digest,
            temporal_digest if temporal_records else None,
            version_status,
            version_basis,
            consolidation_status,
            NormalizationStatus.REVIEW_REQUIRED if input_quarantine_status == "REVIEW_REQUIRED" or document_identity is None or jurisdiction_status is not NormalizationStatus.CLEAR or version_marker_findings else NormalizationStatus.CLEAR,
            tuple(sorted(set(jurisdiction_findings + version_marker_findings + (["TEMPORAL_FACTS_ABSENT"] if not temporal_records else []) + (["STEP14_INPUT_REVIEW_REQUIRED"] if input_quarantine_status == "REVIEW_REQUIRED" else [])))),
        )
        connection.execute(
            "INSERT INTO versions(inventory_record_id,document_identity,version_key_identity,version_identity,raw_content_sha256,jurisdiction_json,source_candidate_ids_json,version_json) VALUES(?,?,?,?,?,?,?,?)",
            (
                inventory_record_id,
                version.document_identity,
                version.version_key_identity,
                version.version_identity,
                version.raw_content_sha256,
                self._serialized(jurisdiction),
                self._serialized(candidate_ids),
                self._serialized(version),
            ),
        )
        for relation_field, relationship_type in (("supersedes", SupersessionRelationshipType.DECLARED_SUPERSEDES), ("superseded_by", SupersessionRelationshipType.DECLARED_SUPERSEDED_BY)):
            try:
                values = _strict_relations(metadata.get(relation_field), maximum=self.policy.maximum_relations_per_record)
            except ValueError:
                self._record_conflict(
                    connection,
                    NormalizationConflict(
                        NormalizationConflictType.METADATA_FIELD_INVALID,
                        (inventory_record_id,),
                        candidate_ids,
                        tuple(value for value in (version_identity,) if value is not None),
                        (canonical_sha256({"field": relation_field, "record": inventory_record_id}),),
                        ConflictSeverity.WARNING,
                        NormalizationStatus.REVIEW_REQUIRED,
                        True,
                        ("SUPERSESSION_RELATION_FIELD_INVALID",),
                    ),
                )
                values = ()
            for target in values:
                connection.execute(
                    "INSERT INTO relation_inputs(source_inventory_record_id,source_version_identity,relation_field,related_identifier,evidence_digest) VALUES(?,?,?,?,?)",
                    (
                        inventory_record_id,
                        version_identity,
                        relation_field,
                        target,
                        canonical_sha256({"inventory_record_id": inventory_record_id, "field": relation_field, "related_identifier": target}),
                    ),
                )
        for candidate_id in candidate_ids:
            proposal = SourceRegistryNormalizationProposal(
                candidate_id,
                inventory_record_id,
                document_identity,
                version_identity,
                jurisdiction.normalization_digest,
                temporal_digest if temporal_records else None,
                NormalizationStatus.REVIEW_REQUIRED,
                tuple(sorted(set(("STEP15_NO_AUTOMATIC_SOURCE_REGISTRY_UPDATE",) + tuple(jurisdiction_findings) + tuple(version_marker_findings) + (("TEMPORAL_FACTS_ABSENT",) if not temporal_records else ())))),
            )
            connection.execute("INSERT INTO proposals(proposal_id,proposal_json) VALUES(?,?)", (proposal.proposal_id, self._serialized(proposal)))
        connection.execute(
            "INSERT INTO processed_records(inventory_record_id,relative_path,raw_sha256,input_quarantine_status) VALUES(?,?,?,?)",
            (inventory_record_id, relative_path, raw_sha256, input_quarantine_status),
        )

    def _validate_temporal_relationships(
        self,
        connection: sqlite3.Connection,
        inventory_record_id: str,
        candidate_ids: tuple[str, ...],
        facts: Mapping[TemporalFactType, TemporalNormalizationRecord],
    ) -> None:
        def conflict_for_pair(
            conflict_type: NormalizationConflictType,
            left: TemporalFactType,
            right: TemporalFactType,
            *,
            severity: ConflictSeverity = ConflictSeverity.MATERIAL,
        ) -> None:
            left_record = facts.get(left)
            right_record = facts.get(right)
            if left_record is None or right_record is None:
                return
            comparison = _compare_temporal_values(left_record.value, right_record.value)
            if comparison is None:
                self._record_conflict(
                    connection,
                    NormalizationConflict(
                        NormalizationConflictType.INTERVAL_PRECISION_OR_TIMEZONE_INCOMPARABLE,
                        (inventory_record_id,),
                        candidate_ids,
                        (),
                        (left_record.normalization_digest, right_record.normalization_digest),
                        ConflictSeverity.WARNING,
                        NormalizationStatus.REVIEW_REQUIRED,
                        True,
                        (f"{left.value}_AND_{right.value}_NOT_COMPARABLE",),
                    ),
                )
            elif comparison >= 0:
                self._record_conflict(
                    connection,
                    NormalizationConflict(
                        conflict_type,
                        (inventory_record_id,),
                        candidate_ids,
                        (),
                        (left_record.normalization_digest, right_record.normalization_digest),
                        severity,
                        NormalizationStatus.CONFLICTING,
                        True,
                        (f"{left.value}_MUST_PRECEDE_{right.value}",),
                    ),
                )

        conflict_for_pair(NormalizationConflictType.EFFECTIVE_INTERVAL_INVALID, TemporalFactType.EFFECTIVE_FROM, TemporalFactType.EFFECTIVE_TO)
        conflict_for_pair(NormalizationConflictType.APPLICABLE_INTERVAL_INVALID, TemporalFactType.APPLICABLE_FROM, TemporalFactType.APPLICABLE_TO)
        conflict_for_pair(NormalizationConflictType.SUPERSESSION_PRECEDES_LEGAL_TIME, TemporalFactType.PUBLISHED_AT, TemporalFactType.SUPERSEDED_AT)
        conflict_for_pair(NormalizationConflictType.SUPERSESSION_PRECEDES_LEGAL_TIME, TemporalFactType.EFFECTIVE_FROM, TemporalFactType.SUPERSEDED_AT)
        conflict_for_pair(NormalizationConflictType.VERIFICATION_PRECEDES_RETRIEVAL, TemporalFactType.RETRIEVED_AT, TemporalFactType.VERIFIED_AT, severity=ConflictSeverity.WARNING)

    def _process_inventory(self, connection: sqlite3.Connection, reader: _SafeSourceMetadataReader) -> Counter[str]:
        counters: Counter[str] = Counter()
        batch = 0
        metadata_rejection_codes = frozenset(
            {
                "STEP15_METADATA_JSON_INVALID",
                "STEP15_METADATA_NUL",
                "STEP15_METADATA_SIZE_LIMIT",
                "STEP15_METADATA_DEPTH_LIMIT",
                "STEP15_METADATA_ITEM_LIMIT",
                "STEP15_METADATA_FIELD_INVALID",
            }
        )
        for inventory in _json_line_iter(self.step14_bundle_root / "file-records.jsonl"):
            counters["inventory_records_considered"] += 1
            relative_path = inventory.get("relative_path")
            if not isinstance(relative_path, str) or not relative_path.endswith("/law_record.json"):
                continue
            counters["metadata_records_seen"] += 1
            try:
                self._process_metadata_record(connection, reader, inventory)
            except TemporalJurisdictionSafetyError as exc:
                if exc.sanitized_code not in metadata_rejection_codes:
                    raise
                inventory_record_id = _text(inventory.get("record_id"), "inventory record ID", 512)
                relative_path = _relative_path(relative_path)
                raw_sha256 = _digest(inventory.get("raw_sha256"), "raw_sha256")
                conflict_type = (
                    NormalizationConflictType.METADATA_JSON_INVALID
                    if exc.sanitized_code in {"STEP15_METADATA_JSON_INVALID", "STEP15_METADATA_NUL"}
                    else NormalizationConflictType.METADATA_FIELD_INVALID
                )
                self._record_conflict(
                    connection,
                    NormalizationConflict(
                        conflict_type,
                        (inventory_record_id,),
                        (),
                        (),
                        (canonical_sha256({"inventory_record_id": inventory_record_id, "rejection": exc.sanitized_code}),),
                        ConflictSeverity.WARNING,
                        NormalizationStatus.REVIEW_REQUIRED,
                        True,
                        ("SOURCE_METADATA_REJECTED_WITHOUT_EXECUTION",),
                    ),
                )
                connection.execute(
                    "INSERT OR IGNORE INTO processed_records(inventory_record_id,relative_path,raw_sha256,input_quarantine_status) VALUES(?,?,?,?)",
                    (inventory_record_id, relative_path, raw_sha256, "REVIEW_REQUIRED"),
                )
            except TemporalJurisdictionReplayConflictError:
                raise
            except (TypeError, ValueError) as exc:
                inventory_record_id = _text(inventory.get("record_id"), "inventory record ID", 512)
                self._record_conflict(
                    connection,
                    NormalizationConflict(
                        NormalizationConflictType.METADATA_FIELD_INVALID,
                        (inventory_record_id,),
                        (),
                        (),
                        (canonical_sha256({"inventory_record_id": inventory_record_id, "exception": type(exc).__name__}),),
                        ConflictSeverity.WARNING,
                        NormalizationStatus.REVIEW_REQUIRED,
                        True,
                        ("STRUCTURED_METADATA_CONTRACT_REJECTED",),
                    ),
                )
                connection.execute(
                    "INSERT OR IGNORE INTO processed_records(inventory_record_id,relative_path,raw_sha256,input_quarantine_status) VALUES(?,?,?,?)",
                    (inventory_record_id, _relative_path(relative_path), _digest(inventory.get("raw_sha256"), "raw_sha256"), "REVIEW_REQUIRED"),
                )
            batch += 1
            if batch >= self.policy.checkpoint_batch_size:
                connection.commit()
                self.progress({"metadata_records_processed": int(connection.execute("SELECT count(*) FROM processed_records").fetchone()[0])})
                batch = 0
        connection.commit()
        counters["metadata_records_processed"] = int(connection.execute("SELECT count(*) FROM processed_records").fetchone()[0])
        return counters

    def _materialize_cross_record_findings(self, connection: sqlite3.Connection) -> None:
        for document_identity, rows_count, jurisdictions in connection.execute(
            "SELECT document_identity,count(*),group_concat(DISTINCT json_extract(jurisdiction_json,'$.normalized_jurisdiction')) FROM versions WHERE document_identity IS NOT NULL GROUP BY document_identity HAVING count(DISTINCT json_extract(jurisdiction_json,'$.normalized_jurisdiction')) > 1"
        ):
            rows = list(connection.execute("SELECT inventory_record_id,version_identity,source_candidate_ids_json,version_json FROM versions WHERE document_identity=? ORDER BY inventory_record_id", (document_identity,)))
            candidate_ids: list[str] = []
            version_ids: list[str] = []
            evidence: list[str] = []
            for row in rows:
                candidate_ids.extend(json.loads(row[2]))
                if row[1] is not None:
                    version_ids.append(row[1])
                evidence.append(canonical_sha256(json.loads(row[3])))
            self._record_conflict(
                connection,
                NormalizationConflict(
                    NormalizationConflictType.SAME_OFFICIAL_IDENTIFIER_INCOMPATIBLE_JURISDICTION,
                    tuple(row[0] for row in rows),
                    tuple(candidate_ids),
                    tuple(version_ids),
                    tuple(evidence),
                    ConflictSeverity.MATERIAL,
                    NormalizationStatus.CONFLICTING,
                    True,
                    ("SAME_DOCUMENT_IDENTITY_HAS_MULTIPLE_JURISDICTIONS",),
                ),
            )
        for key, raw_count in connection.execute(
            "SELECT version_key_identity,count(DISTINCT raw_content_sha256) FROM versions WHERE version_key_identity IS NOT NULL AND raw_content_sha256 IS NOT NULL GROUP BY version_key_identity HAVING count(DISTINCT raw_content_sha256)>1"
        ):
            rows = list(connection.execute("SELECT inventory_record_id,version_identity,source_candidate_ids_json,version_json FROM versions WHERE version_key_identity=? ORDER BY inventory_record_id", (key,)))
            candidate_ids: list[str] = []
            version_ids: list[str] = []
            evidence: list[str] = []
            for row in rows:
                candidate_ids.extend(json.loads(row[2]))
                if row[1] is not None:
                    version_ids.append(row[1])
                evidence.append(canonical_sha256(json.loads(row[3])))
            self._record_conflict(
                connection,
                NormalizationConflict(
                    NormalizationConflictType.SAME_VERSION_ID_DIFFERENT_RAW_CONTENT,
                    tuple(row[0] for row in rows),
                    tuple(candidate_ids),
                    tuple(version_ids),
                    tuple(evidence),
                    ConflictSeverity.MATERIAL,
                    NormalizationStatus.CONFLICTING,
                    True,
                    ("VERSION_KEY_REUSED_FOR_DIFFERENT_CONTENT",),
                ),
            )
        for group_id, group_json in connection.execute("SELECT group_id,group_json FROM exact_groups ORDER BY group_id"):
            group = _bounded_mapping(_strict_json(str(group_json).encode("utf-8"), field_name="Step 14 exact group", maximum_bytes=256 * 1024), field_name="Step 14 exact group")
            member_ids = tuple(str(value) for value in group.get("member_record_ids", ()) if isinstance(value, str))
            relevant = list(
                connection.execute(
                    "SELECT inventory_record_id,version_identity,source_candidate_ids_json,version_json FROM versions WHERE inventory_record_id IN ({}) ORDER BY inventory_record_id".format(
                        ",".join("?" for _ in member_ids) if member_ids else "NULL"
                    ),
                    member_ids,
                )
            ) if member_ids else []
            if not relevant:
                continue
            if not any(bool(group.get(field)) for field in ("source_family_conflict", "license_conflict", "privacy_conflict")):
                continue
            candidate_ids: list[str] = []
            version_ids: list[str] = []
            for row in relevant:
                candidate_ids.extend(json.loads(row[2]))
                if row[1] is not None:
                    version_ids.append(row[1])
            self._record_conflict(
                connection,
                NormalizationConflict(
                    NormalizationConflictType.EXACT_DUPLICATE_METADATA_CONFLICT,
                    tuple(row[0] for row in relevant),
                    tuple(candidate_ids),
                    tuple(version_ids),
                    (canonical_sha256(group),),
                    ConflictSeverity.WARNING,
                    NormalizationStatus.REVIEW_REQUIRED,
                    True,
                    ("STEP14_EXACT_DUPLICATE_GROUP_HAS_METADATA_CONFLICT",),
                ),
            )
        self._materialize_relations(connection)
        self._materialize_near_duplicate_relationships(connection)
        connection.commit()

    def _materialize_relations(self, connection: sqlite3.Connection) -> None:
        identifiers: dict[str, tuple[str, ...]] = {}
        for official_identifier, version_identity in connection.execute("SELECT json_extract(version_json,'$.official_identifier'),version_identity FROM versions WHERE version_identity IS NOT NULL"):
            if isinstance(official_identifier, str) and official_identifier:
                identifiers.setdefault(official_identifier, tuple())
                identifiers[official_identifier] = tuple(sorted(set(identifiers[official_identifier] + (str(version_identity),))))
        for source_record_id, source_version, relation_field, related_identifier, evidence_digest in connection.execute(
            "SELECT source_inventory_record_id,source_version_identity,relation_field,related_identifier,evidence_digest FROM relation_inputs ORDER BY source_inventory_record_id,relation_field,related_identifier"
        ):
            target_versions = identifiers.get(related_identifier, ())
            if relation_field == "supersedes":
                predecessor = target_versions[0] if len(target_versions) == 1 else None
                successor = source_version
                relation_type = SupersessionRelationshipType.DECLARED_SUPERSEDES
            else:
                predecessor = source_version
                successor = target_versions[0] if len(target_versions) == 1 else None
                relation_type = SupersessionRelationshipType.DECLARED_SUPERSEDED_BY
            markers = () if len(target_versions) == 1 else ("RELATED_IDENTIFIER_UNRESOLVED_OR_AMBIGUOUS",)
            if predecessor is None and successor is None:
                self._record_conflict(
                    connection,
                    NormalizationConflict(
                        NormalizationConflictType.SUPERSESSION_TARGET_UNRESOLVED,
                        (source_record_id,),
                        (),
                        (),
                        (evidence_digest,),
                        ConflictSeverity.WARNING,
                        NormalizationStatus.REVIEW_REQUIRED,
                        True,
                        ("SOURCE_VERSION_IDENTITY_UNAVAILABLE",),
                    ),
                )
                continue
            relationship = SupersessionCandidate(
                relation_type,
                predecessor,
                successor,
                related_identifier,
                source_record_id,
                relation_field,
                NormalizationEvidenceClass.EXPLICIT_DOCUMENT_RELATION,
                evidence_digest,
                None,
                None,
                FactVerificationStatus.DECLARED,
                markers,
            )
            connection.execute("INSERT OR IGNORE INTO relationships(relationship_id,relationship_json) VALUES(?,?)", (relationship.relationship_id, self._serialized(relationship)))
            if markers:
                self._record_conflict(
                    connection,
                    NormalizationConflict(
                        NormalizationConflictType.SUPERSESSION_TARGET_UNRESOLVED,
                        (source_record_id,),
                        (),
                        tuple(value for value in (source_version,) if value is not None),
                        (relationship.relationship_digest,),
                        ConflictSeverity.WARNING,
                        NormalizationStatus.REVIEW_REQUIRED,
                        True,
                        markers,
                    ),
                )

    def _materialize_near_duplicate_relationships(self, connection: sqlite3.Connection) -> None:
        for near_id, near_json in connection.execute("SELECT candidate_id,candidate_json FROM near_candidates ORDER BY candidate_id"):
            near = _bounded_mapping(_strict_json(str(near_json).encode("utf-8"), field_name="Step 14 near candidate", maximum_bytes=256 * 1024), field_name="Step 14 near candidate")
            left = near.get("left_record_id")
            right = near.get("right_record_id")
            if not isinstance(left, str) or not isinstance(right, str):
                continue
            left_row = connection.execute("SELECT version_identity FROM versions WHERE inventory_record_id=?", (left,)).fetchone()
            right_row = connection.execute("SELECT version_identity FROM versions WHERE inventory_record_id=?", (right,)).fetchone()
            if left_row is None or right_row is None or left_row[0] is None or right_row[0] is None:
                continue
            relationship = SupersessionCandidate(
                SupersessionRelationshipType.NEAR_DUPLICATE_REVIEW_ONLY,
                str(left_row[0]),
                str(right_row[0]),
                None,
                left,
                "step14.near_duplicate_candidate",
                NormalizationEvidenceClass.STEP14_NEAR_DUPLICATE_EVIDENCE,
                _digest(near.get("candidate_digest"), "Step 14 near candidate digest"),
                None,
                None,
                FactVerificationStatus.UNVERIFIED,
                ("NEAR_DUPLICATE_DOES_NOT_PROVE_LINEAGE_OR_SUPERSESSION",),
            )
            connection.execute("INSERT OR IGNORE INTO relationships(relationship_id,relationship_json) VALUES(?,?)", (relationship.relationship_id, self._serialized(relationship)))

    @staticmethod
    def _publish_no_replace(temporary: Path, target: Path) -> None:
        try:
            os.link(temporary, target, follow_symlinks=False)
        except FileExistsError as exc:
            temporary.unlink(missing_ok=True)
            raise TemporalJurisdictionReplayConflictError("derived output appeared during atomic publication", sanitized_code="STEP15_OUTPUT_REPLAY_CONFLICT") from exc
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            raise TemporalJurisdictionSafetyError("derived output could not be atomically published", sanitized_code="STEP15_OUTPUT_PUBLISH_FAILED") from exc
        try:
            temporary.unlink()
            directory_fd = os.open(target.parent, os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError as exc:
            raise TemporalJurisdictionSafetyError("derived output directory could not be synced", sanitized_code="STEP15_OUTPUT_PUBLISH_FAILED") from exc

    def _write_atomic_absent(self, target: Path, payload: bytes) -> tuple[int, str]:
        digest = hashlib.sha256(payload).hexdigest()
        if target.exists():
            if target.is_symlink() or not target.is_file() or target.stat().st_size != len(payload) or self._sha256_file(target) != digest:
                raise TemporalJurisdictionReplayConflictError("existing Step 15 output differs", sanitized_code="STEP15_OUTPUT_REPLAY_CONFLICT")
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
            temporary.unlink(missing_ok=True)
            raise
        else:
            assert descriptor is not None
            os.close(descriptor)
        self._publish_no_replace(temporary, target)
        return len(payload), digest

    def _write_query_jsonl(self, connection: sqlite3.Connection, *, name: str, query: str) -> tuple[int, str]:
        assert self.bundle_root is not None
        target = self.bundle_root / name
        expected_digest = hashlib.sha256()
        expected_length = 0
        for (serialized,) in connection.execute(query):
            line = str(serialized).encode("utf-8") + b"\n"
            expected_digest.update(line)
            expected_length += len(line)
        digest = expected_digest.hexdigest()
        if target.exists():
            if target.is_symlink() or not target.is_file() or target.stat().st_size != expected_length or self._sha256_file(target) != digest:
                raise TemporalJurisdictionReplayConflictError("existing JSONL output differs from checkpoint", sanitized_code="STEP15_OUTPUT_REPLAY_CONFLICT")
            return expected_length, digest
        temporary = target.with_name(f".{target.name}.{os.getpid()}.part")
        descriptor: int | None = None
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o640)
            for (serialized,) in connection.execute(query):
                line = str(serialized).encode("utf-8") + b"\n"
                offset = 0
                while offset < len(line):
                    offset += os.write(descriptor, line[offset:])
            os.fsync(descriptor)
        except BaseException:
            if descriptor is not None:
                os.close(descriptor)
            temporary.unlink(missing_ok=True)
            raise
        else:
            assert descriptor is not None
            os.close(descriptor)
        self._publish_no_replace(temporary, target)
        return expected_length, digest

    @staticmethod
    def _count_by_json_field(connection: sqlite3.Connection, table: str, field: str) -> dict[str, int]:
        return {
            str(key): int(count)
            for key, count in connection.execute(
                f"SELECT json_extract(record_json,'$.{field}'),count(*) FROM {table} GROUP BY json_extract(record_json,'$.{field}') ORDER BY json_extract(record_json,'$.{field}')"
            )
        }

    def _summary(self, connection: sqlite3.Connection, plan: TemporalJurisdictionNormalizationPlan, logical_output_digest: str) -> TemporalJurisdictionNormalizationSummary:
        inventory_summary = _bounded_mapping(
            _strict_json(self._read_bounded_regular(self.step14_bundle_root / "inventory-summary.json", maximum_bytes=4 * 1024 * 1024), field_name="Step 14 summary", maximum_bytes=4 * 1024 * 1024),
            field_name="Step 14 summary",
        )
        temporal_counts = {
            str(key): int(value)
            for key, value in connection.execute("SELECT fact_type,count(*) FROM temporal_records GROUP BY fact_type ORDER BY fact_type")
        }
        jurisdiction_counts = {
            (str(key) if key is not None else "UNKNOWN"): int(value)
            for key, value in connection.execute("SELECT json_extract(record_json,'$.normalized_jurisdiction'),count(*) FROM jurisdiction_records GROUP BY json_extract(record_json,'$.normalized_jurisdiction') ORDER BY json_extract(record_json,'$.normalized_jurisdiction')")
        }
        conflict_counts = {
            str(key): int(value)
            for key, value in connection.execute("SELECT json_extract(conflict_json,'$.conflict_type'),count(*) FROM conflicts GROUP BY json_extract(conflict_json,'$.conflict_type') ORDER BY json_extract(conflict_json,'$.conflict_type')")
        }
        metadata_records = int(connection.execute("SELECT count(*) FROM processed_records").fetchone()[0])
        metadata_records_quarantined = int(
            connection.execute(
                "SELECT count(*) FROM processed_records WHERE input_quarantine_status='QUARANTINED'"
            ).fetchone()[0]
        )
        metadata_records_eligible = max(0, metadata_records - metadata_records_quarantined)
        with_temporal = int(connection.execute("SELECT count(DISTINCT inventory_record_id) FROM temporal_records").fetchone()[0])
        with_jurisdiction = int(connection.execute("SELECT count(*) FROM jurisdiction_records WHERE json_extract(record_json,'$.normalized_jurisdiction') IS NOT NULL").fetchone()[0])
        review_required = int(
            connection.execute(
                "SELECT count(DISTINCT inventory_record_id) FROM versions WHERE json_extract(version_json,'$.status') IN ('REVIEW_REQUIRED','CONFLICTING','AMBIGUOUS','QUARANTINED')"
            ).fetchone()[0]
        )
        return TemporalJurisdictionNormalizationSummary(
            plan.run_id,
            self.step14_manifest_digest,
            self.source_root_identity_digest,
            int(inventory_summary["objects_observed"]),
            metadata_records_eligible,
            max(0, plan.metadata_records - metadata_records_eligible),
            metadata_records_quarantined,
            int(connection.execute("SELECT count(*) FROM versions").fetchone()[0]),
            max(0, metadata_records - with_temporal),
            max(0, metadata_records - with_jurisdiction),
            temporal_counts,
            jurisdiction_counts,
            int(connection.execute("SELECT count(DISTINCT document_identity) FROM versions WHERE document_identity IS NOT NULL").fetchone()[0]),
            int(connection.execute("SELECT count(DISTINCT version_identity) FROM versions WHERE version_identity IS NOT NULL").fetchone()[0]),
            int(connection.execute("SELECT count(*) FROM relationships").fetchone()[0]),
            conflict_counts,
            int(connection.execute("SELECT count(*) FROM proposals").fetchone()[0]),
            review_required,
            logical_output_digest=logical_output_digest,
        )

    @staticmethod
    def _logical_digest(generated: Iterable[tuple[str, int, str]]) -> str:
        return canonical_sha256(tuple(sorted(generated)))

    def _completed_result(self) -> TemporalJurisdictionNormalizationResult:
        assert self.bundle_root is not None
        verification = verify_temporal_jurisdiction_bundle(self.bundle_root)
        manifest_payload = _strict_json(self._read_bounded_regular(self.bundle_root / "artifact-manifest.json", maximum_bytes=4 * 1024 * 1024), field_name="Step 15 manifest", maximum_bytes=4 * 1024 * 1024)
        summary_payload = _strict_json(self._read_bounded_regular(self.bundle_root / "run-summary.json", maximum_bytes=4 * 1024 * 1024), field_name="Step 15 summary", maximum_bytes=4 * 1024 * 1024)
        manifest = _manifest_from_mapping(_bounded_mapping(manifest_payload, field_name="Step 15 manifest"))
        summary = _summary_from_mapping(_bounded_mapping(summary_payload, field_name="Step 15 summary"))
        return TemporalJurisdictionNormalizationResult(self.run_id or manifest.run.run_id, summary, manifest, verification)

    def execute(self, plan: TemporalJurisdictionNormalizationPlan) -> TemporalJurisdictionNormalizationResult:
        if self.run_id != plan.run_id or self.bundle_root is None or self._source_tree_digest != plan.source_tree_digest:
            raise TemporalJurisdictionReplayConflictError("Step 15 execution facts differ from its plan", sanitized_code="STEP15_PLAN_MISMATCH")
        bundle_root, checkpoints = self._ensure_bundle_root()
        if (bundle_root / "artifact-manifest.json").exists():
            return self._completed_result()
        state_path = checkpoints / "normalization-state.sqlite3"
        with closing(self._connect(state_path)) as connection:
            started_at, resume_count = self._initialize_state(connection, plan)
            self._load_step14_auxiliaries(connection)
            reader = _SafeSourceMetadataReader(
                self.source_root,
                maximum_bytes=self.policy.maximum_metadata_bytes,
                chunk_bytes=self.policy.metadata_read_chunk_bytes,
            )
            self._process_inventory(connection, reader)
            self._materialize_cross_record_findings(connection)
            queries = {
                "temporal-normalization.jsonl": "SELECT record_json FROM temporal_records ORDER BY record_id",
                "jurisdiction-normalization.jsonl": "SELECT record_json FROM jurisdiction_records ORDER BY record_id",
                "document-versions.jsonl": "SELECT version_json FROM versions ORDER BY version_identity,inventory_record_id",
                "supersession-candidates.jsonl": "SELECT relationship_json FROM relationships ORDER BY relationship_id",
                "normalization-conflicts.jsonl": "SELECT conflict_json FROM conflicts ORDER BY conflict_id",
                "source-registry-normalization-proposals.jsonl": "SELECT proposal_json FROM proposals ORDER BY proposal_id",
            }
            generated: list[tuple[str, int, str]] = []
            for name, query in queries.items():
                generated.append((name, *self._write_query_jsonl(connection, name=name, query=query)))
            logical_output_digest = self._logical_digest(generated)
            summary = self._summary(connection, plan, logical_output_digest)
            summary_length, summary_hash = self._write_atomic_absent(bundle_root / "run-summary.json", canonical_json_bytes(summary) + b"\n")
            generated.append(("run-summary.json", summary_length, summary_hash))
            completed_at = ensure_utc(self.clock(), "Step 15 clock")
            run = TemporalJurisdictionNormalizationRun(
                plan.run_id,
                self.starting_head,
                self.source_root_identity_digest,
                plan.source_tree_digest,
                self.step14_manifest_digest,
                self.step14_summary_digest,
                self.device_reference,
                self.policy.policy_digest,
                started_at,
                completed_at,
                resume_count,
            )
            completion = {
                "run_id": plan.run_id,
                "policy_digest": self.policy.policy_digest,
                "step14_manifest_digest": self.step14_manifest_digest,
                "source_tree_digest": plan.source_tree_digest,
                "logical_output_digest": logical_output_digest,
                "summary_digest": summary.summary_digest,
                "completed_at": completed_at.isoformat(),
                "state_spool_authority": "NONE",
            }
            completion_length, completion_hash = self._write_atomic_absent(checkpoints / "completion.json", canonical_json_bytes(completion) + b"\n")
            generated.append(("checkpoints/completion.json", completion_length, completion_hash))
            connection.execute("UPDATE metadata SET value='true' WHERE key='completed'")
            connection.commit()
        for suffix in ("-wal", "-shm", ""):
            (Path(str(state_path) + suffix)).unlink(missing_ok=True)
        manifest = TemporalJurisdictionNormalizationManifest(
            run,
            summary.summary_digest,
            logical_output_digest,
            tuple(generated),
            f"corpora/manifests/step15/{plan.run_id}",
        )
        self._write_atomic_absent(bundle_root / "artifact-manifest.json", canonical_json_bytes(manifest) + b"\n")
        verification = verify_temporal_jurisdiction_bundle(bundle_root)
        tree_after, _counts_after = self._tree_fingerprint()
        if tree_after != plan.source_tree_digest:
            raise TemporalJurisdictionReplayConflictError("source tree changed during Step 15 normalization", sanitized_code="STEP15_SOURCE_TREE_CHANGED")
        return TemporalJurisdictionNormalizationResult(plan.run_id, summary, manifest, verification)


def _run_from_mapping(value: Mapping[str, Any]) -> TemporalJurisdictionNormalizationRun:
    return TemporalJurisdictionNormalizationRun(
        str(value["run_id"]),
        str(value["starting_head"]),
        str(value["source_root_identity_digest"]),
        str(value["source_tree_digest"]),
        str(value["step14_manifest_digest"]),
        str(value["step14_summary_digest"]),
        str(value["device_reference"]),
        str(value["policy_digest"]),
        datetime.fromisoformat(str(value["started_at"]).replace("Z", "+00:00")),
        None if value.get("completed_at") is None else datetime.fromisoformat(str(value["completed_at"]).replace("Z", "+00:00")),
        int(value["resume_count"]),
    )


def _summary_from_mapping(value: Mapping[str, Any]) -> TemporalJurisdictionNormalizationSummary:
    fields = {name: value[name] for name in TemporalJurisdictionNormalizationSummary.__dataclass_fields__ if name not in {"summary_digest"}}
    fields["temporal_fact_counts"] = dict(fields["temporal_fact_counts"])
    fields["jurisdiction_counts"] = dict(fields["jurisdiction_counts"])
    fields["conflict_counts"] = dict(fields["conflict_counts"])
    summary = TemporalJurisdictionNormalizationSummary(**fields)
    if value.get("summary_digest") != summary.summary_digest:
        raise TemporalJurisdictionReplayConflictError("Step 15 summary digest mismatch", sanitized_code="STEP15_OUTPUT_DIGEST_MISMATCH")
    return summary


def _manifest_from_mapping(value: Mapping[str, Any]) -> TemporalJurisdictionNormalizationManifest:
    run = _run_from_mapping(_bounded_mapping(value["run"], field_name="Step 15 manifest run"))
    generated_values = value["generated_files"]
    if not isinstance(generated_values, list):
        raise TemporalJurisdictionSafetyError("Step 15 manifest files are invalid", sanitized_code="STEP15_OUTPUT_SCHEMA_INVALID")
    generated = tuple((str(item[0]), int(item[1]), str(item[2])) for item in generated_values if isinstance(item, list) and len(item) == 3)
    if len(generated) != len(generated_values):
        raise TemporalJurisdictionSafetyError("Step 15 manifest files are invalid", sanitized_code="STEP15_OUTPUT_SCHEMA_INVALID")
    manifest = TemporalJurisdictionNormalizationManifest(
        run,
        str(value["summary_digest"]),
        str(value["logical_output_digest"]),
        generated,
        str(value["artifact_root_reference"]),
    )
    if value.get("manifest_digest") != manifest.manifest_digest:
        raise TemporalJurisdictionReplayConflictError("Step 15 manifest digest mismatch", sanitized_code="STEP15_OUTPUT_DIGEST_MISMATCH")
    return manifest


def verify_temporal_jurisdiction_bundle(bundle_root: Path) -> dict[str, Any]:
    """Verify a completed Step 15 bundle without trusting its own claims."""

    manifest_path = bundle_root / "artifact-manifest.json"
    if bundle_root.is_symlink() or not bundle_root.is_dir() or manifest_path.is_symlink() or not manifest_path.is_file():
        raise TemporalJurisdictionSafetyError("Step 15 bundle is incomplete", sanitized_code="STEP15_OUTPUT_BUNDLE_INCOMPLETE")
    payload = TemporalJurisdictionNormalizationEngine._read_bounded_regular(manifest_path, maximum_bytes=4 * 1024 * 1024)
    manifest_map = _bounded_mapping(_strict_json(payload, field_name="Step 15 manifest", maximum_bytes=4 * 1024 * 1024), field_name="Step 15 manifest")
    manifest = _manifest_from_mapping(manifest_map)
    verified: list[dict[str, Any]] = []
    for relative, length, digest in manifest.generated_files:
        target = bundle_root / relative
        if target.is_symlink() or not target.is_file() or target.stat().st_size != length:
            raise TemporalJurisdictionReplayConflictError("Step 15 artifact length changed", sanitized_code="STEP15_OUTPUT_DIGEST_MISMATCH")
        actual = TemporalJurisdictionNormalizationEngine._sha256_file(target)
        if actual != digest:
            raise TemporalJurisdictionReplayConflictError("Step 15 artifact digest changed", sanitized_code="STEP15_OUTPUT_DIGEST_MISMATCH")
        verified.append({"relative_path": relative, "byte_length": length, "sha256": actual})
    part_files = tuple(path.relative_to(bundle_root).as_posix() for path in bundle_root.rglob("*.part"))
    if part_files:
        raise TemporalJurisdictionReplayConflictError("Step 15 bundle has partial artifacts", sanitized_code="STEP15_OUTPUT_PARTIAL_RESIDUE")
    return {
        "status": "PASS",
        "manifest_digest": manifest.manifest_digest,
        "logical_output_digest": manifest.logical_output_digest,
        "verified_files": verified,
        "part_files": [],
    }


__all__ = [
    "CONFLICT_NORMALIZATION_RULE_VERSION",
    "DOCUMENT_VERSION_NORMALIZATION_RULE_VERSION",
    "JURISDICTION_NORMALIZATION_RULE_VERSION",
    "SUPERSESSION_NORMALIZATION_RULE_VERSION",
    "TEMPORAL_JURISDICTION_POLICY_ID",
    "TEMPORAL_JURISDICTION_SCHEMA_VERSION",
    "TEMPORAL_NORMALIZATION_RULE_VERSION",
    "ConflictSeverity",
    "DocumentVersionRecord",
    "FactVerificationStatus",
    "JurisdictionNormalizationRecord",
    "NormalizationConflict",
    "NormalizationConflictType",
    "NormalizationEvidenceClass",
    "NormalizationStatus",
    "NormalizedTemporalValue",
    "SourceRegistryNormalizationProposal",
    "SupersessionCandidate",
    "SupersessionRelationshipType",
    "TemporalFactType",
    "TemporalJurisdictionNormalizationEngine",
    "TemporalJurisdictionNormalizationError",
    "TemporalJurisdictionNormalizationManifest",
    "TemporalJurisdictionNormalizationPlan",
    "TemporalJurisdictionNormalizationPolicy",
    "TemporalJurisdictionNormalizationResult",
    "TemporalJurisdictionNormalizationRun",
    "TemporalJurisdictionNormalizationSummary",
    "TemporalJurisdictionReplayConflictError",
    "TemporalJurisdictionSafetyError",
    "TemporalNormalizationRecord",
    "TemporalPrecision",
    "TimezoneStatus",
    "verify_temporal_jurisdiction_bundle",
]
