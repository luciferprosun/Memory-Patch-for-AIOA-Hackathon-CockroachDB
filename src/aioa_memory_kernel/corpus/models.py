"""Immutable contracts for Step 14 inventory, deduplication and registration.

The records deliberately contain root-relative paths and digests only.  They
are evidence and registration candidates, never legal authority or a request
to publish source material.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import PurePosixPath
from types import MappingProxyType
from typing import Any, Mapping

from aioa_memory_kernel.contracts.serialization import (
    canonical_sha256,
    ensure_utc,
    freeze_json,
    require_sha256_hex,
)


INVENTORY_SCHEMA_VERSION = "1.0.0"
INVENTORY_POLICY_ID = "german-law-corpus-inventory-1a"
EXACT_DUPLICATE_ALGORITHM = "sha256-length-exact-1a"
NORMALIZED_DUPLICATE_ALGORITHM = "step11-normalized-content-1a"
NEAR_DUPLICATE_ALGORITHM = "bounded-minhash-shingle-candidates-1a"
SOURCE_MAPPING_POLICY = "german-law-step9-source-registration-mapping-1a"

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@+-]{0,255}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT_OBJECT_ID = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")


class FileKind(str, Enum):
    REGULAR = "REGULAR"
    SYMLINK = "SYMLINK"
    DIRECTORY = "DIRECTORY"
    SOCKET = "SOCKET"
    FIFO = "FIFO"
    BLOCK_DEVICE = "BLOCK_DEVICE"
    CHARACTER_DEVICE = "CHARACTER_DEVICE"
    OTHER_SPECIAL = "OTHER_SPECIAL"
    UNREADABLE = "UNREADABLE"


class StabilityStatus(str, Enum):
    STABLE = "STABLE"
    UNSTABLE = "UNSTABLE_DURING_HASH"
    NOT_HASHED = "NOT_HASHED"


class ParserSupportStatus(str, Enum):
    STEP11_SUPPORTED = "STEP11_SUPPORTED"
    UNSUPPORTED_FORMAT = "UNSUPPORTED_FORMAT"
    MALFORMED_SUPPORTED_FORMAT = "MALFORMED_SUPPORTED_FORMAT"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class InventoryLicenseStatus(str, Enum):
    VERIFIED_ALLOWED = "VERIFIED_ALLOWED"
    VERIFIED_RESTRICTED = "VERIFIED_RESTRICTED"
    DECLARED = "DECLARED"
    UNKNOWN = "UNKNOWN"
    CONFLICTING = "CONFLICTING"


class InventoryPrivacyStatus(str, Enum):
    PUBLIC = "PUBLIC"
    PRIVATE = "PRIVATE"
    POTENTIALLY_SENSITIVE = "POTENTIALLY_SENSITIVE"
    UNKNOWN = "UNKNOWN"
    CONFLICTING = "CONFLICTING"


class QuarantineStatus(str, Enum):
    CLEAR = "CLEAR"
    QUARANTINED = "QUARANTINED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


class QuarantineReason(str, Enum):
    UNREADABLE_FILE = "UNREADABLE_FILE"
    UNSTABLE_DURING_HASH = "UNSTABLE_DURING_HASH"
    SYMLINK = "SYMLINK"
    SPECIAL_FILE = "SPECIAL_FILE"
    PATH_ESCAPE = "PATH_ESCAPE"
    HASH_FAILURE = "HASH_FAILURE"
    CORRUPT_OR_MALFORMED = "CORRUPT_OR_MALFORMED"
    SOURCE_IDENTITY_CONFLICT = "SOURCE_IDENTITY_CONFLICT"
    OFFICIAL_STATUS_UNVERIFIED = "OFFICIAL_STATUS_UNVERIFIED"
    LICENSE_UNKNOWN = "LICENSE_UNKNOWN"
    LICENSE_RESTRICTED = "LICENSE_RESTRICTED"
    LICENSE_PROHIBITED = "LICENSE_PROHIBITED"
    PRIVATE_OR_PERSONAL_DATA = "PRIVATE_OR_PERSONAL_DATA"
    SECRET_OR_CREDENTIAL_SIGNAL = "SECRET_OR_CREDENTIAL_SIGNAL"
    DUPLICATE_METADATA_CONFLICT = "DUPLICATE_METADATA_CONFLICT"
    NEAR_DUPLICATE_REVIEW = "NEAR_DUPLICATE_REVIEW"
    UNSUPPORTED_FORMAT = "UNSUPPORTED_FORMAT"
    UNKNOWN_PROVENANCE = "UNKNOWN_PROVENANCE"


class RegistrationDisposition(str, Enum):
    READY_FOR_REGISTRATION = "READY_FOR_REGISTRATION"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    QUARANTINED = "QUARANTINED"
    DUPLICATE_ALIAS = "DUPLICATE_ALIAS"
    UNSUPPORTED_FORMAT = "UNSUPPORTED_FORMAT"


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


def _optional_text(
    value: object | None, field_name: str, maximum: int = 1024
) -> str | None:
    return None if value is None else _text(value, field_name, maximum)


def _relative_path(value: object, field_name: str = "relative_path") -> str:
    text = _text(value, field_name, 4096)
    candidate = PurePosixPath(text)
    if (
        candidate.is_absolute()
        or str(candidate) != text
        or "\\" in text
        or any(part in {"", ".", ".."} for part in candidate.parts)
    ):
        raise ValueError(f"{field_name} must be a safe root-relative POSIX path")
    return text


def _digest(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a SHA-256 digest")
    require_sha256_hex(value, field_name)
    return value


def _optional_digest(value: object | None, field_name: str) -> str | None:
    return None if value is None else _digest(value, field_name)


def _git_object_id(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _GIT_OBJECT_ID.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a lowercase Git object ID")
    return value


def _strings(values: object, field_name: str, maximum: int = 128) -> tuple[str, ...]:
    if not isinstance(values, (tuple, list, set, frozenset)):
        raise ValueError(f"{field_name} must be a bounded collection")
    result = tuple(sorted({_text(item, f"{field_name} item", 255) for item in values}))
    if len(result) > maximum:
        raise ValueError(f"{field_name} exceeds its item limit")
    return result


def _pairs(values: Mapping[str, int] | tuple[tuple[str, int], ...]) -> tuple[tuple[str, int], ...]:
    items = values.items() if isinstance(values, Mapping) else values
    frozen: list[tuple[str, int]] = []
    for key, count in items:
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise ValueError("distribution counts must be non-negative integers")
        frozen.append((_text(key, "distribution key", 255), count))
    return tuple(sorted(frozen))


@dataclass(frozen=True, slots=True)
class CorpusInventoryPolicy:
    schema_version: str = INVENTORY_SCHEMA_VERSION
    policy_id: str = INVENTORY_POLICY_ID
    scanner_version: str = "1.0.0"
    exact_duplicate_algorithm: str = EXACT_DUPLICATE_ALGORITHM
    normalized_duplicate_algorithm: str = NORMALIZED_DUPLICATE_ALGORITHM
    near_duplicate_algorithm: str = NEAR_DUPLICATE_ALGORITHM
    source_mapping_policy: str = SOURCE_MAPPING_POLICY
    hash_chunk_bytes: int = 1024 * 1024
    checkpoint_batch_size: int = 256
    maximum_supported_parse_bytes: int = 64 * 1024 * 1024
    maximum_signal_scan_bytes: int = 2 * 1024 * 1024
    shingle_width: int = 5
    minhash_width: int = 24
    near_similarity_threshold_millionths: int = 800_000
    maximum_bucket_members: int = 64
    maximum_near_candidates: int = 50_000
    source_tree_writes_allowed: bool = False
    automatic_publication_allowed: bool = False

    def __post_init__(self) -> None:
        if self.schema_version != INVENTORY_SCHEMA_VERSION:
            raise ValueError("unsupported inventory schema version")
        for name in (
            "policy_id",
            "scanner_version",
            "exact_duplicate_algorithm",
            "normalized_duplicate_algorithm",
            "near_duplicate_algorithm",
            "source_mapping_policy",
        ):
            _text(getattr(self, name), name, 255)
        for name, minimum, maximum in (
            ("hash_chunk_bytes", 4096, 16 * 1024 * 1024),
            ("checkpoint_batch_size", 1, 10_000),
            ("maximum_supported_parse_bytes", 1, 64 * 1024 * 1024),
            ("maximum_signal_scan_bytes", 1, 16 * 1024 * 1024),
            ("shingle_width", 2, 12),
            ("minhash_width", 8, 128),
            ("near_similarity_threshold_millionths", 1, 1_000_000),
            ("maximum_bucket_members", 2, 1024),
            ("maximum_near_candidates", 1, 1_000_000),
        ):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
                raise ValueError(f"{name} is outside the bounded policy")
        if self.source_tree_writes_allowed or self.automatic_publication_allowed:
            raise ValueError("Step 14 cannot write the source tree or publish")

    @property
    def policy_digest(self) -> str:
        return canonical_sha256(self)


@dataclass(frozen=True, slots=True)
class LicenseAssessment:
    status: InventoryLicenseStatus
    rule_ids: tuple[str, ...]
    evidence_digests: tuple[str, ...] = ()
    assessment_digest: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.status, InventoryLicenseStatus):
            raise ValueError("license status must be typed")
        object.__setattr__(self, "rule_ids", _strings(self.rule_ids, "license rule_ids"))
        object.__setattr__(self, "evidence_digests", tuple(sorted(_digest(item, "license evidence digest") for item in self.evidence_digests)))
        object.__setattr__(self, "assessment_digest", canonical_sha256(self, exclude_fields=("assessment_digest",)))


@dataclass(frozen=True, slots=True)
class PrivacyAssessment:
    status: InventoryPrivacyStatus
    rule_ids: tuple[str, ...]
    signal_count: int
    location_digests: tuple[str, ...] = ()
    assessment_digest: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.status, InventoryPrivacyStatus):
            raise ValueError("privacy status must be typed")
        if not isinstance(self.signal_count, int) or isinstance(self.signal_count, bool) or self.signal_count < 0:
            raise ValueError("privacy signal_count must be non-negative")
        object.__setattr__(self, "rule_ids", _strings(self.rule_ids, "privacy rule_ids"))
        object.__setattr__(self, "location_digests", tuple(sorted(_digest(item, "privacy location digest") for item in self.location_digests)))
        object.__setattr__(self, "assessment_digest", canonical_sha256(self, exclude_fields=("assessment_digest",)))


@dataclass(frozen=True, slots=True)
class QuarantineDecision:
    status: QuarantineStatus
    reasons: tuple[QuarantineReason, ...]
    finding_digests: tuple[str, ...] = ()
    decision_digest: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.status, QuarantineStatus):
            raise ValueError("quarantine status must be typed")
        reasons = tuple(sorted(set(self.reasons), key=lambda item: item.value))
        if any(not isinstance(item, QuarantineReason) for item in reasons):
            raise ValueError("quarantine reasons must be typed")
        if self.status is QuarantineStatus.CLEAR and reasons:
            raise ValueError("clear quarantine decision cannot carry reasons")
        if self.status is not QuarantineStatus.CLEAR and not reasons:
            raise ValueError("non-clear quarantine decision needs a reason")
        object.__setattr__(self, "reasons", reasons)
        object.__setattr__(self, "finding_digests", tuple(sorted(_digest(item, "finding digest") for item in self.finding_digests)))
        object.__setattr__(self, "decision_digest", canonical_sha256(self, exclude_fields=("decision_digest",)))


@dataclass(frozen=True, slots=True)
class CorpusFileRecord:
    source_root_identity_digest: str
    relative_path: str
    path_digest: str
    file_kind: FileKind
    stability_status: StabilityStatus
    byte_size: int | None
    mtime_ns: int | None
    raw_sha256: str | None
    normalized_sha256: str | None
    normalized_character_length: int | None
    extension: str
    media_type_candidate: str | None
    parser_support_status: ParserSupportStatus
    source_family_candidate: str | None
    official_identifier_candidate: str | None
    license_assessment_digest: str
    privacy_assessment_digest: str
    quarantine_decision_digest: str
    findings: tuple[str, ...]
    record_id: str = field(init=False)
    record_digest: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_root_identity_digest", _digest(self.source_root_identity_digest, "source_root_identity_digest"))
        path = _relative_path(self.relative_path)
        object.__setattr__(self, "relative_path", path)
        object.__setattr__(self, "path_digest", _digest(self.path_digest, "path_digest"))
        if self.path_digest != canonical_sha256({"relative_path": path}):
            raise ValueError("path_digest does not bind the relative path")
        if not isinstance(self.file_kind, FileKind) or not isinstance(self.stability_status, StabilityStatus):
            raise ValueError("file kind and stability must be typed")
        for name in ("byte_size", "mtime_ns", "normalized_character_length"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, int) or isinstance(value, bool) or value < 0):
                raise ValueError(f"{name} must be a non-negative integer")
        object.__setattr__(self, "raw_sha256", _optional_digest(self.raw_sha256, "raw_sha256"))
        object.__setattr__(self, "normalized_sha256", _optional_digest(self.normalized_sha256, "normalized_sha256"))
        object.__setattr__(self, "extension", self.extension.casefold())
        object.__setattr__(self, "media_type_candidate", _optional_text(self.media_type_candidate, "media_type_candidate", 255))
        if not isinstance(self.parser_support_status, ParserSupportStatus):
            raise ValueError("parser support status must be typed")
        object.__setattr__(self, "source_family_candidate", _optional_text(self.source_family_candidate, "source_family_candidate", 255))
        object.__setattr__(self, "official_identifier_candidate", _optional_text(self.official_identifier_candidate, "official_identifier_candidate", 512))
        for name in ("license_assessment_digest", "privacy_assessment_digest", "quarantine_decision_digest"):
            object.__setattr__(self, name, _digest(getattr(self, name), name))
        object.__setattr__(self, "findings", _strings(self.findings, "findings"))
        identity = canonical_sha256({
            "source_root_identity_digest": self.source_root_identity_digest,
            "path_digest": self.path_digest,
            "raw_sha256": self.raw_sha256,
            "file_kind": self.file_kind,
        })
        object.__setattr__(self, "record_id", f"corpus-object-{identity}")
        object.__setattr__(self, "record_digest", canonical_sha256(self, exclude_fields=("record_digest",)))


@dataclass(frozen=True, slots=True)
class CorpusPathAlias:
    raw_sha256: str
    byte_size: int
    record_id: str
    relative_path: str
    path_digest: str
    alias_id: str = field(init=False)
    alias_digest: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "raw_sha256", _digest(self.raw_sha256, "raw_sha256"))
        if self.byte_size < 0:
            raise ValueError("byte_size cannot be negative")
        object.__setattr__(self, "record_id", _text(self.record_id, "record_id", 512))
        object.__setattr__(self, "relative_path", _relative_path(self.relative_path))
        object.__setattr__(self, "path_digest", _digest(self.path_digest, "path_digest"))
        identity = canonical_sha256({"raw_sha256": self.raw_sha256, "path_digest": self.path_digest})
        object.__setattr__(self, "alias_id", f"corpus-alias-{identity}")
        object.__setattr__(self, "alias_digest", canonical_sha256(self, exclude_fields=("alias_digest",)))


@dataclass(frozen=True, slots=True)
class ExactDuplicateGroup:
    raw_sha256: str
    byte_size: int
    member_record_ids: tuple[str, ...]
    member_path_digests: tuple[str, ...]
    source_family_conflict: bool
    license_conflict: bool
    privacy_conflict: bool
    group_id: str = field(init=False)
    group_digest: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "raw_sha256", _digest(self.raw_sha256, "raw_sha256"))
        members = _strings(self.member_record_ids, "member_record_ids", 1_000_000)
        paths = tuple(sorted(_digest(item, "member path digest") for item in self.member_path_digests))
        if len(members) < 2 or len(paths) != len(members):
            raise ValueError("duplicate group must bind at least two members")
        object.__setattr__(self, "member_record_ids", members)
        object.__setattr__(self, "member_path_digests", paths)
        identity = canonical_sha256({"algorithm": EXACT_DUPLICATE_ALGORITHM, "raw_sha256": self.raw_sha256, "byte_size": self.byte_size})
        object.__setattr__(self, "group_id", f"exact-duplicate-{identity}")
        object.__setattr__(self, "group_digest", canonical_sha256(self, exclude_fields=("group_digest",)))

    @property
    def informational_reclaimable_bytes(self) -> int:
        return self.byte_size * (len(self.member_record_ids) - 1)


@dataclass(frozen=True, slots=True)
class NormalizedDuplicateGroup:
    media_type: str
    normalized_sha256: str
    member_record_ids: tuple[str, ...]
    raw_sha256_values: tuple[str, ...]
    group_id: str = field(init=False)
    group_digest: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "media_type", _text(self.media_type, "media_type", 255))
        object.__setattr__(self, "normalized_sha256", _digest(self.normalized_sha256, "normalized_sha256"))
        members = _strings(self.member_record_ids, "member_record_ids", 1_000_000)
        raw = tuple(sorted(_digest(item, "raw_sha256") for item in self.raw_sha256_values))
        if len(members) < 2 or len(raw) != len(members):
            raise ValueError("normalized duplicate group must bind at least two members")
        object.__setattr__(self, "member_record_ids", members)
        object.__setattr__(self, "raw_sha256_values", raw)
        identity = canonical_sha256({"algorithm": NORMALIZED_DUPLICATE_ALGORITHM, "media_type": self.media_type, "normalized_sha256": self.normalized_sha256})
        object.__setattr__(self, "group_id", f"normalized-duplicate-{identity}")
        object.__setattr__(self, "group_digest", canonical_sha256(self, exclude_fields=("group_digest",)))


@dataclass(frozen=True, slots=True)
class NearDuplicateCandidateGroup:
    left_record_id: str
    right_record_id: str
    algorithm_version: str
    similarity_millionths: int
    reason_codes: tuple[str, ...]
    authority_or_source_class_conflict: bool
    temporal_or_version_conflict: bool
    human_review_required: bool = True
    candidate_id: str = field(init=False)
    candidate_digest: str = field(init=False)

    def __post_init__(self) -> None:
        left = _text(self.left_record_id, "left_record_id", 512)
        right = _text(self.right_record_id, "right_record_id", 512)
        if left == right:
            raise ValueError("near duplicate candidate cannot be a self-pair")
        left, right = sorted((left, right))
        object.__setattr__(self, "left_record_id", left)
        object.__setattr__(self, "right_record_id", right)
        object.__setattr__(self, "algorithm_version", _text(self.algorithm_version, "algorithm_version", 255))
        if not 0 <= self.similarity_millionths <= 1_000_000:
            raise ValueError("similarity_millionths is outside its bound")
        object.__setattr__(self, "reason_codes", _strings(self.reason_codes, "reason_codes"))
        if not self.human_review_required:
            raise ValueError("near duplicates always require human review")
        identity = canonical_sha256({"algorithm": self.algorithm_version, "left": left, "right": right})
        object.__setattr__(self, "candidate_id", f"near-duplicate-{identity}")
        object.__setattr__(self, "candidate_digest", canonical_sha256(self, exclude_fields=("candidate_digest",)))


@dataclass(frozen=True, slots=True)
class SourceRegistrationCandidate:
    inventory_run_id: str
    logical_source_candidate_id: str
    content_sha256: str
    normalized_sha256: str | None
    sanitized_source_reference: str
    hat_scope_id: str
    source_class: str
    authority_level: str
    license_status: str
    access_class: str
    redaction_state: str
    jurisdiction: str | None
    language: str | None
    official_identifier: str | None
    provenance_alias_digests: tuple[str, ...]
    parser_support_status: ParserSupportStatus
    disposition: RegistrationDisposition
    reason_codes: tuple[str, ...]
    candidate_id: str = field(init=False)
    candidate_digest: str = field(init=False)

    def __post_init__(self) -> None:
        for name in (
            "inventory_run_id",
            "logical_source_candidate_id",
            "sanitized_source_reference",
            "hat_scope_id",
            "source_class",
            "authority_level",
            "license_status",
            "access_class",
            "redaction_state",
        ):
            object.__setattr__(self, name, _text(getattr(self, name), name, 1024))
        object.__setattr__(self, "content_sha256", _digest(self.content_sha256, "content_sha256"))
        object.__setattr__(self, "normalized_sha256", _optional_digest(self.normalized_sha256, "normalized_sha256"))
        object.__setattr__(self, "jurisdiction", _optional_text(self.jurisdiction, "jurisdiction", 128))
        object.__setattr__(self, "language", _optional_text(self.language, "language", 64))
        object.__setattr__(self, "official_identifier", _optional_text(self.official_identifier, "official_identifier", 512))
        object.__setattr__(self, "provenance_alias_digests", tuple(sorted(_digest(item, "provenance alias digest") for item in self.provenance_alias_digests)))
        if not isinstance(self.parser_support_status, ParserSupportStatus) or not isinstance(self.disposition, RegistrationDisposition):
            raise ValueError("candidate parser support and disposition must be typed")
        object.__setattr__(self, "reason_codes", _strings(self.reason_codes, "reason_codes"))
        identity = canonical_sha256({"run": self.inventory_run_id, "logical_source": self.logical_source_candidate_id, "content_sha256": self.content_sha256})
        object.__setattr__(self, "candidate_id", f"source-registration-{identity}")
        object.__setattr__(self, "candidate_digest", canonical_sha256(self, exclude_fields=("candidate_digest",)))


@dataclass(frozen=True, slots=True)
class CorpusInventoryRun:
    run_id: str
    starting_head: str
    source_root_identity_digest: str
    device_reference: str
    policy_digest: str
    started_at: datetime
    completed_at: datetime | None
    resume_count: int
    source_tree_before_digest: str
    source_tree_after_digest: str | None
    run_digest: str = field(init=False)

    def __post_init__(self) -> None:
        for name in ("run_id", "device_reference"):
            object.__setattr__(self, name, _text(getattr(self, name), name, 512))
        object.__setattr__(self, "starting_head", _git_object_id(self.starting_head, "starting_head"))
        for name in ("source_root_identity_digest", "policy_digest", "source_tree_before_digest"):
            object.__setattr__(self, name, _digest(getattr(self, name), name))
        object.__setattr__(self, "source_tree_after_digest", _optional_digest(self.source_tree_after_digest, "source_tree_after_digest"))
        object.__setattr__(self, "started_at", ensure_utc(self.started_at, "started_at"))
        if self.completed_at is not None:
            object.__setattr__(self, "completed_at", ensure_utc(self.completed_at, "completed_at"))
        if self.resume_count < 0:
            raise ValueError("resume_count cannot be negative")
        object.__setattr__(self, "run_digest", canonical_sha256(self, exclude_fields=("run_digest",)))


@dataclass(frozen=True, slots=True)
class CorpusInventorySummary:
    run_id: str
    source_root_identity_digest: str
    directories_observed: int
    objects_observed: int
    stable_files: int
    bytes_observed: int
    raw_sha256_count: int
    symlink_count: int
    special_count: int
    unreadable_count: int
    unstable_count: int
    exact_duplicate_group_count: int
    exact_duplicate_member_count: int
    informational_duplicate_bytes: int
    normalized_duplicate_group_count: int
    near_duplicate_candidate_count: int
    registration_candidate_count: int
    registration_conflict_count: int
    distributions: Mapping[str, Any]
    source_tree_writes: int = 0
    source_files_modified: int = 0
    source_files_deleted: int = 0
    aws_writes: int = 0
    s3_writes: int = 0
    model_calls: int = 0
    network_acquisitions: int = 0
    ocr_operations: int = 0
    embeddings_created: int = 0
    step15_started: bool = False
    final_question_selected: bool = False
    forbidden_scenario_hardcoded: bool = False
    summary_digest: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", _text(self.run_id, "run_id", 512))
        object.__setattr__(self, "source_root_identity_digest", _digest(self.source_root_identity_digest, "source_root_identity_digest"))
        for name in (
            "directories_observed", "objects_observed", "stable_files", "bytes_observed",
            "raw_sha256_count", "symlink_count", "special_count", "unreadable_count",
            "unstable_count", "exact_duplicate_group_count", "exact_duplicate_member_count",
            "informational_duplicate_bytes", "normalized_duplicate_group_count",
            "near_duplicate_candidate_count", "registration_candidate_count",
            "registration_conflict_count", "source_tree_writes", "source_files_modified",
            "source_files_deleted", "aws_writes", "s3_writes", "model_calls",
            "network_acquisitions", "ocr_operations", "embeddings_created",
        ):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if any((self.source_tree_writes, self.source_files_modified, self.source_files_deleted, self.aws_writes, self.s3_writes, self.model_calls, self.network_acquisitions, self.ocr_operations, self.embeddings_created)):
            raise ValueError("Step 14 summary cannot claim forbidden effects")
        if self.step15_started or self.final_question_selected or self.forbidden_scenario_hardcoded:
            raise ValueError("Step 14 boundary was crossed")
        object.__setattr__(self, "distributions", freeze_json(self.distributions))
        object.__setattr__(self, "summary_digest", canonical_sha256(self, exclude_fields=("summary_digest",)))


@dataclass(frozen=True, slots=True)
class CorpusInventoryManifest:
    run: CorpusInventoryRun
    summary_digest: str
    generated_files: tuple[tuple[str, int, str], ...]
    artifact_root_reference: str
    manifest_digest: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.run, CorpusInventoryRun):
            raise ValueError("manifest run must be typed")
        object.__setattr__(self, "summary_digest", _digest(self.summary_digest, "summary_digest"))
        generated: list[tuple[str, int, str]] = []
        for relative_path, length, digest in self.generated_files:
            generated.append((_relative_path(relative_path, "generated relative_path"), int(length), _digest(digest, "generated digest")))
        object.__setattr__(self, "generated_files", tuple(sorted(generated)))
        object.__setattr__(self, "artifact_root_reference", _relative_path(self.artifact_root_reference, "artifact_root_reference"))
        object.__setattr__(self, "manifest_digest", canonical_sha256(self, exclude_fields=("manifest_digest",)))


__all__ = [
    "CorpusFileRecord", "CorpusInventoryManifest", "CorpusInventoryPolicy",
    "CorpusInventoryRun", "CorpusInventorySummary", "CorpusPathAlias",
    "ExactDuplicateGroup", "FileKind", "InventoryLicenseStatus",
    "InventoryPrivacyStatus", "LicenseAssessment", "NearDuplicateCandidateGroup",
    "NormalizedDuplicateGroup", "ParserSupportStatus", "PrivacyAssessment",
    "QuarantineDecision", "QuarantineReason", "QuarantineStatus",
    "RegistrationDisposition", "SourceRegistrationCandidate", "StabilityStatus",
]
