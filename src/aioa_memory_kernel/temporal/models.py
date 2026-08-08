"""Immutable Step 21 temporal, conflict, and freshness contracts.

The contracts consume the frozen Step 20 Evidence Bundle.  They describe
evidence policy only: no value in this module grants answer, approval,
execution, publication, or external-action authority.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime

from aioa_memory_kernel.contracts.enums import (
    AnswerStatus,
    EvidenceStatus,
    KnowledgeRoute,
    StableStringEnum,
)
from aioa_memory_kernel.contracts.exceptions import (
    ContractValidationError,
    IntegrityError,
)
from aioa_memory_kernel.contracts.scope import ScopeDimension
from aioa_memory_kernel.contracts.serialization import (
    canonical_json_bytes,
    canonical_sha256,
    ensure_utc,
    freeze_json,
    require_enum_member,
    require_sha256_hex,
    verify_canonical_hash,
)
from aioa_memory_kernel.evidence import (
    FrozenEvidenceBundle,
    HybridEvidenceOutcome,
    RetrievalCoverageStatus,
    verify_evidence_bundle_hash,
    verify_outcome_hash,
)
from aioa_memory_kernel.routing import KnowledgeRouteResult, verify_route_hash


STEP21_SCHEMA_VERSION = "1.0.0"
TEMPORAL_POLICY_ID = "temporal-resolution-policy-1a"
TEMPORAL_POLICY_VERSION = "1"
FRESHNESS_POLICY_SCHEMA_VERSION = "1.0.0"
COMPLETENESS_POLICY_ID = "temporal-completeness-fallback-1a"
COMPLETENESS_POLICY_VERSION = "1"
TEMPORAL_FACTS_DIGEST_SCHEME = "step21-canonical-temporal-facts-1a"
MAX_COMPLETENESS_ATTEMPTS = 1
MAX_TEMPORAL_CANDIDATES = 80
MAX_CONFLICT_GROUPS = 40
MAX_LIMITATIONS = 32
MAX_POLICY_SOURCE_KINDS = 64
MAX_FRESHNESS_SECONDS = 10 * 365 * 24 * 60 * 60


class TemporalQueryMode(StableStringEnum):
    CURRENT = "CURRENT"
    AS_OF = "AS_OF"
    FUTURE = "FUTURE"
    UNSPECIFIED = "UNSPECIFIED"


class TemporalApplicability(StableStringEnum):
    APPLICABLE = "APPLICABLE"
    NOT_YET_APPLICABLE = "NOT_YET_APPLICABLE"
    EXPIRED = "EXPIRED"
    SUPERSEDED = "SUPERSEDED"
    UNKNOWN = "UNKNOWN"
    CONFLICTING = "CONFLICTING"


class FreshnessStatus(StableStringEnum):
    FRESH = "FRESH"
    STALE = "STALE"
    UNKNOWN = "UNKNOWN"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class SupersessionStatus(StableStringEnum):
    NOT_SUPERSEDED = "NOT_SUPERSEDED"
    SUPERSEDED = "SUPERSEDED"
    AMBIGUOUS = "AMBIGUOUS"
    CYCLIC = "CYCLIC"
    UNKNOWN = "UNKNOWN"


class EvidenceAvailability(StableStringEnum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"


class Step21ReasonCode(StableStringEnum):
    TEMPORAL_OK = "TEMPORAL_OK"
    NO_HAT_SELECTED = "NO_HAT_SELECTED"
    AMBIGUOUS_ROUTE = "AMBIGUOUS_ROUTE"
    STEP20_INPUT_HASH_INVALID = "STEP20_INPUT_HASH_INVALID"
    STEP20_INPUT_BINDING_MISMATCH = "STEP20_INPUT_BINDING_MISMATCH"
    TEMPORAL_SCOPE_MISMATCH = "TEMPORAL_SCOPE_MISMATCH"
    CURRENT_TIME_BOUND = "CURRENT_TIME_BOUND"
    EXPLICIT_AS_OF_BOUND = "EXPLICIT_AS_OF_BOUND"
    FUTURE_AS_OF_BOUND = "FUTURE_AS_OF_BOUND"
    AS_OF_UNSPECIFIED = "AS_OF_UNSPECIFIED"
    EFFECTIVE_AT_AS_OF = "EFFECTIVE_AT_AS_OF"
    NOT_YET_EFFECTIVE = "NOT_YET_EFFECTIVE"
    EFFECTIVE_PERIOD_EXPIRED = "EFFECTIVE_PERIOD_EXPIRED"
    SUPERSEDED_AT_AS_OF = "SUPERSEDED_AT_AS_OF"
    SUPERSESSION_AMBIGUOUS = "SUPERSESSION_AMBIGUOUS"
    SUPERSESSION_CYCLE = "SUPERSESSION_CYCLE"
    TEMPORAL_FACTS_MISSING = "TEMPORAL_FACTS_MISSING"
    TEMPORAL_FACTS_INVALID = "TEMPORAL_FACTS_INVALID"
    TEMPORAL_FACTS_DIGEST_INVALID = "TEMPORAL_FACTS_DIGEST_INVALID"
    TEMPORAL_FACTS_DIGEST_PRESERVED = "TEMPORAL_FACTS_DIGEST_PRESERVED"
    FRESHNESS_POLICY_MISSING = "FRESHNESS_POLICY_MISSING"
    FRESHNESS_FACT_MISSING = "FRESHNESS_FACT_MISSING"
    FRESHNESS_FACT_IN_FUTURE = "FRESHNESS_FACT_IN_FUTURE"
    EVIDENCE_FRESH = "EVIDENCE_FRESH"
    EVIDENCE_STALE = "EVIDENCE_STALE"
    MATERIAL_CONFLICT = "MATERIAL_CONFLICT"
    INDEPENDENT_SUPPORT = "INDEPENDENT_SUPPORT"
    EVIDENCE_UNAVAILABLE = "EVIDENCE_UNAVAILABLE"
    NO_APPLICABLE_EVIDENCE = "NO_APPLICABLE_EVIDENCE"
    COMPLETENESS_FALLBACK_ATTEMPTED = "COMPLETENESS_FALLBACK_ATTEMPTED"
    COMPLETENESS_FALLBACK_ADMITTED = "COMPLETENESS_FALLBACK_ADMITTED"
    COMPLETENESS_FALLBACK_EXHAUSTED = "COMPLETENESS_FALLBACK_EXHAUSTED"
    STEP20_COVERAGE_PARTIAL = "STEP20_COVERAGE_PARTIAL"
    EVIDENCE_SUFFICIENT = "EVIDENCE_SUFFICIENT"
    EVIDENCE_INSUFFICIENT = "EVIDENCE_INSUFFICIENT"
    EVIDENCE_CONFLICTING = "EVIDENCE_CONFLICTING"
    EVIDENCE_INVALID = "EVIDENCE_INVALID"


class TemporalBoundaryError(RuntimeError):
    """Sanitized fail-closed error at the Step 21 boundary."""

    def __init__(self, reason_code: Step21ReasonCode) -> None:
        if not isinstance(reason_code, Step21ReasonCode):
            raise TypeError("reason_code must be a Step21ReasonCode")
        super().__init__(f"Step 21 temporal resolution denied: {reason_code.value}")
        self.reason_code = reason_code
        self.evidence_status = EvidenceStatus.INVALID


_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_DOMAIN_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")


def _text(value: object, field_name: str, maximum_bytes: int = 1024) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ContractValidationError(f"{field_name} must be canonical text")
    if unicodedata.normalize("NFC", value) != value or _CONTROL.search(value):
        raise ContractValidationError(f"{field_name} must be NFC without controls")
    if len(value.encode("utf-8")) > maximum_bytes:
        raise ContractValidationError(f"{field_name} exceeds its byte limit")
    return value


def _optional_text(
    value: object | None,
    field_name: str,
    maximum_bytes: int = 1024,
) -> str | None:
    return None if value is None else _text(value, field_name, maximum_bytes)


def _domain_id(value: object, field_name: str) -> str:
    text = _text(value, field_name, 128)
    if _DOMAIN_ID.fullmatch(text) is None:
        raise ContractValidationError(f"{field_name} is not a logical identifier")
    return text


def _scope_tuple(value: object, field_name: str) -> tuple[ScopeDimension, ...]:
    if not isinstance(value, (tuple, list)):
        raise ContractValidationError(f"{field_name} must be an ordered scope")
    result = tuple(value)
    if any(not isinstance(item, ScopeDimension) for item in result):
        raise ContractValidationError(f"{field_name} must contain ScopeDimension")
    names = tuple(item.name for item in result)
    if names != tuple(sorted(names)) or len(names) != len(set(names)):
        raise ContractValidationError(f"{field_name} must be sorted and unique")
    return result


def _reason_tuple(value: object) -> tuple[Step21ReasonCode, ...]:
    if not isinstance(value, (tuple, list)):
        raise ContractValidationError("reason_codes must be ordered")
    if any(not isinstance(item, Step21ReasonCode) for item in value):
        raise ContractValidationError("reason_codes must use Step21ReasonCode")
    return tuple(sorted(set(value), key=lambda item: item.value))


def _string_tuple(
    value: object,
    field_name: str,
    *,
    maximum: int,
) -> tuple[str, ...]:
    if not isinstance(value, (tuple, list)):
        raise ContractValidationError(f"{field_name} must be ordered")
    result = tuple(sorted(set(value)))
    if len(result) > maximum or any(not isinstance(item, str) for item in result):
        raise ContractValidationError(f"{field_name} is invalid")
    for item in result:
        _text(item, field_name, 512)
    return result


def _hash_tuple(value: object, field_name: str, maximum: int) -> tuple[str, ...]:
    result = _string_tuple(value, field_name, maximum=maximum)
    for digest in result:
        require_sha256_hex(digest, field_name)
    return result


def _ordered_hash_tuple(
    value: object,
    field_name: str,
    maximum: int,
) -> tuple[str, ...]:
    if not isinstance(value, (tuple, list)):
        raise ContractValidationError(f"{field_name} must be ordered")
    result = tuple(value)
    if len(result) > maximum or len(result) != len(set(result)):
        raise ContractValidationError(f"{field_name} must be bounded and unique")
    for digest in result:
        require_sha256_hex(digest, field_name)
    return result


@dataclass(frozen=True, slots=True)
class TemporalPolicy:
    policy_id: str = field(init=False, default=TEMPORAL_POLICY_ID)
    policy_version: str = field(init=False, default=TEMPORAL_POLICY_VERSION)
    interval_start_inclusive: bool = field(init=False, default=True)
    interval_end_exclusive: bool = field(init=False, default=True)
    source_operational_time_is_legal_time: bool = field(init=False, default=False)
    step20_rank_overrides_temporal_policy: bool = field(init=False, default=False)
    policy_digest: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "policy_digest",
            canonical_sha256(self, exclude_fields=("policy_digest",)),
        )


@dataclass(frozen=True, slots=True)
class FreshnessPolicy:
    policy_id: str
    policy_version: str
    maximum_age_seconds_by_source_kind: Mapping[str, int]
    observation_precedence: tuple[str, ...] = (
        "verified_at",
        "retrieved_at",
        "source_observed_at",
        "snapshot_captured_at",
        "published_at",
    )
    schema_version: str = FRESHNESS_POLICY_SCHEMA_VERSION
    policy_digest: str = field(init=False)

    def __post_init__(self) -> None:
        _domain_id(self.policy_id, "freshness policy_id")
        _text(self.policy_version, "freshness policy_version", 64)
        if self.schema_version != FRESHNESS_POLICY_SCHEMA_VERSION:
            raise ContractValidationError("unsupported freshness policy schema")
        if not isinstance(self.maximum_age_seconds_by_source_kind, Mapping):
            raise ContractValidationError("freshness thresholds must be a mapping")
        if len(self.maximum_age_seconds_by_source_kind) > MAX_POLICY_SOURCE_KINDS:
            raise ContractValidationError("too many freshness source profiles")
        thresholds: dict[str, int] = {}
        for source_kind, seconds in self.maximum_age_seconds_by_source_kind.items():
            _text(source_kind, "freshness source_kind", 1024)
            if (
                isinstance(seconds, bool)
                or not isinstance(seconds, int)
                or not 1 <= seconds <= MAX_FRESHNESS_SECONDS
            ):
                raise ContractValidationError("freshness threshold is outside policy")
            thresholds[source_kind] = seconds
        object.__setattr__(
            self,
            "maximum_age_seconds_by_source_kind",
            freeze_json(dict(sorted(thresholds.items()))),
        )
        if not isinstance(self.observation_precedence, (tuple, list)):
            raise ContractValidationError("observation_precedence must be ordered")
        precedence = tuple(self.observation_precedence)
        if len(precedence) > 8 or len(precedence) != len(set(precedence)):
            raise ContractValidationError("freshness observation precedence is invalid")
        for value in precedence:
            _text(value, "observation_precedence", 64)
        allowed = {
            "verified_at",
            "retrieved_at",
            "source_observed_at",
            "snapshot_captured_at",
            "published_at",
        }
        if not set(precedence) <= allowed:
            raise ContractValidationError("freshness observation precedence is invalid")
        # Preserve the declared precedence; it is policy, not a set.
        object.__setattr__(self, "observation_precedence", precedence)
        object.__setattr__(
            self,
            "policy_digest",
            canonical_sha256(self, exclude_fields=("policy_digest",)),
        )


@dataclass(frozen=True, slots=True)
class CompletenessPolicy:
    policy_id: str = COMPLETENESS_POLICY_ID
    policy_version: str = COMPLETENESS_POLICY_VERSION
    minimum_applicable_items: int = 1
    maximum_attempts: int = MAX_COMPLETENESS_ATTEMPTS
    policy_digest: str = field(init=False)

    def __post_init__(self) -> None:
        _domain_id(self.policy_id, "completeness policy_id")
        _text(self.policy_version, "completeness policy_version", 64)
        if (
            isinstance(self.minimum_applicable_items, bool)
            or not isinstance(self.minimum_applicable_items, int)
            or not 1 <= self.minimum_applicable_items <= 40
        ):
            raise ContractValidationError("minimum_applicable_items is outside policy")
        if (
            isinstance(self.maximum_attempts, bool)
            or not isinstance(self.maximum_attempts, int)
            or not 0 <= self.maximum_attempts <= MAX_COMPLETENESS_ATTEMPTS
        ):
            raise ContractValidationError("maximum_attempts exceeds Step 21 policy")
        object.__setattr__(
            self,
            "policy_digest",
            canonical_sha256(self, exclude_fields=("policy_digest",)),
        )


def load_temporal_policy() -> TemporalPolicy:
    return TemporalPolicy()


def load_completeness_policy() -> CompletenessPolicy:
    return CompletenessPolicy()


@dataclass(frozen=True, slots=True)
class TemporalFacts:
    published_at: datetime | None = None
    promulgated_at: datetime | None = None
    adopted_at: datetime | None = None
    effective_from: datetime | None = None
    effective_to: datetime | None = None
    applicable_from: datetime | None = None
    applicable_to: datetime | None = None
    decision_date: datetime | None = None
    retrieved_at: datetime | None = None
    ingested_at: datetime | None = None
    verified_at: datetime | None = None
    source_observed_at: datetime | None = None
    snapshot_captured_at: datetime | None = None
    superseded_at: datetime | None = None
    version_status: str | None = None
    consolidation_status: str | None = None
    document_identity: str | None = None
    version_identity: str | None = None
    official_identifier: str | None = None
    provision_identifier: str | None = None
    supersedes: tuple[str, ...] = ()
    superseded_by: tuple[str, ...] = ()
    source_temporal_facts_digest: str | None = None
    source_digest_scheme: str | None = None
    applicability_facts_hash: str = field(init=False)
    facts_hash: str = field(init=False)

    def __post_init__(self) -> None:
        for name in (
            "published_at",
            "promulgated_at",
            "adopted_at",
            "effective_from",
            "effective_to",
            "applicable_from",
            "applicable_to",
            "decision_date",
            "retrieved_at",
            "ingested_at",
            "verified_at",
            "source_observed_at",
            "snapshot_captured_at",
            "superseded_at",
        ):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, ensure_utc(value, name))
        for name in (
            "version_status",
            "consolidation_status",
            "document_identity",
            "version_identity",
            "official_identifier",
            "provision_identifier",
            "source_digest_scheme",
        ):
            object.__setattr__(
                self,
                name,
                _optional_text(getattr(self, name), name, 512),
            )
        object.__setattr__(
            self,
            "supersedes",
            _string_tuple(self.supersedes, "supersedes", maximum=32),
        )
        object.__setattr__(
            self,
            "superseded_by",
            _string_tuple(self.superseded_by, "superseded_by", maximum=32),
        )
        if self.source_temporal_facts_digest is not None:
            require_sha256_hex(
                self.source_temporal_facts_digest,
                "source_temporal_facts_digest",
            )
        object.__setattr__(
            self,
            "applicability_facts_hash",
            canonical_sha256(
                {
                    "published_at": self.published_at,
                    "promulgated_at": self.promulgated_at,
                    "adopted_at": self.adopted_at,
                    "effective_from": self.effective_from,
                    "effective_to": self.effective_to,
                    "applicable_from": self.applicable_from,
                    "applicable_to": self.applicable_to,
                    "decision_date": self.decision_date,
                    "superseded_at": self.superseded_at,
                    "version_status": self.version_status,
                    "consolidation_status": self.consolidation_status,
                    "supersedes": self.supersedes,
                    "superseded_by": self.superseded_by,
                }
            ),
        )
        object.__setattr__(
            self,
            "facts_hash",
            canonical_sha256(
                self,
                exclude_fields=(
                    "facts_hash",
                    "applicability_facts_hash",
                    "source_temporal_facts_digest",
                    "source_digest_scheme",
                    "document_identity",
                    "version_identity",
                    "official_identifier",
                    "provision_identifier",
                ),
            ),
        )


@dataclass(frozen=True, slots=True)
class TemporalResolutionRequest:
    route: KnowledgeRouteResult
    step20_outcome: HybridEvidenceOutcome
    temporal_mode: TemporalQueryMode
    knowledge_as_of: datetime | None
    trusted_now: datetime
    availability: EvidenceAvailability
    freshness_policy: FreshnessPolicy
    completeness_policy: CompletenessPolicy = field(default_factory=load_completeness_policy)
    fallback_outcome: HybridEvidenceOutcome | None = None
    temporal_policy_digest: str = field(default_factory=lambda: load_temporal_policy().policy_digest)
    request_hash: str = field(init=False)
    evaluation_as_of: datetime = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.route, KnowledgeRouteResult):
            raise TemporalBoundaryError(Step21ReasonCode.STEP20_INPUT_HASH_INVALID)
        if not isinstance(self.step20_outcome, HybridEvidenceOutcome):
            raise TemporalBoundaryError(Step21ReasonCode.STEP20_INPUT_HASH_INVALID)
        try:
            verify_route_hash(self.route)
            _verify_step20_outcome(self.step20_outcome)
            if self.fallback_outcome is not None:
                _verify_step20_outcome(self.fallback_outcome)
        except (ContractValidationError, IntegrityError) as exc:
            raise TemporalBoundaryError(
                Step21ReasonCode.STEP20_INPUT_HASH_INVALID
            ) from exc
        require_enum_member(self.temporal_mode, TemporalQueryMode, "temporal_mode")
        require_enum_member(self.availability, EvidenceAvailability, "availability")
        if not isinstance(self.freshness_policy, FreshnessPolicy):
            raise ContractValidationError("freshness_policy must be typed")
        if not isinstance(self.completeness_policy, CompletenessPolicy):
            raise ContractValidationError("completeness_policy must be typed")
        trusted_now = ensure_utc(self.trusted_now, "trusted_now")
        object.__setattr__(self, "trusted_now", trusted_now)
        supplied = (
            None
            if self.knowledge_as_of is None
            else ensure_utc(self.knowledge_as_of, "knowledge_as_of")
        )
        object.__setattr__(self, "knowledge_as_of", supplied)
        if self.temporal_mode in {TemporalQueryMode.CURRENT, TemporalQueryMode.UNSPECIFIED}:
            if supplied is not None:
                raise ContractValidationError(
                    "current/unspecified mode cannot carry explicit knowledge_as_of"
                )
            evaluation = trusted_now
        elif supplied is None:
            raise ContractValidationError("AS_OF/FUTURE requires knowledge_as_of")
        elif self.temporal_mode is TemporalQueryMode.AS_OF:
            if supplied > trusted_now:
                raise ContractValidationError("historical AS_OF cannot be in the future")
            evaluation = supplied
        else:
            if supplied < trusted_now:
                raise ContractValidationError("FUTURE knowledge_as_of precedes trusted_now")
            evaluation = supplied
        object.__setattr__(self, "evaluation_as_of", evaluation)
        require_sha256_hex(self.temporal_policy_digest, "temporal_policy_digest")
        if self.temporal_policy_digest != load_temporal_policy().policy_digest:
            raise ContractValidationError("temporal policy identity is invalid")
        _verify_route_outcome_binding(self.route, self.step20_outcome)
        if self.fallback_outcome is not None:
            if self.route.knowledge_route not in {
                KnowledgeRoute.HAT_ASSIST,
                KnowledgeRoute.HAT_ENFORCE,
            }:
                raise TemporalBoundaryError(
                    Step21ReasonCode.STEP20_INPUT_BINDING_MISMATCH
                )
            _verify_route_outcome_binding(self.route, self.fallback_outcome)
            _verify_bundle_binding_equality(
                self.step20_outcome.bundle,
                self.fallback_outcome.bundle,
            )
        scope_as_of = tuple(
            item.value for item in self.route.effective_scope if item.name == "knowledge_as_of"
        )
        if scope_as_of:
            if len(scope_as_of) != 1 or not isinstance(scope_as_of[0], datetime):
                raise TemporalBoundaryError(Step21ReasonCode.TEMPORAL_SCOPE_MISMATCH)
            if ensure_utc(scope_as_of[0], "scope knowledge_as_of") != evaluation:
                raise TemporalBoundaryError(Step21ReasonCode.TEMPORAL_SCOPE_MISMATCH)
        object.__setattr__(
            self,
            "request_hash",
            canonical_sha256(self, exclude_fields=("request_hash",)),
        )


@dataclass(frozen=True, slots=True)
class TemporalCandidateAssessment:
    step20_bundle_hash: str
    step20_item_hash: str
    evidence_id: str
    candidate_identity_hash: str
    source_id: str
    knowledge_version_id: str
    chunk_id: str
    document_identity: str | None
    version_identity: str | None
    logical_subject_identity: str
    temporal_facts_digest: str | None
    temporal_facts_hash: str
    as_of: datetime
    temporal_applicability: TemporalApplicability
    freshness_status: FreshnessStatus
    supersession_status: SupersessionStatus
    conflict_group_id: str | None
    selected: bool
    reason_codes: tuple[Step21ReasonCode, ...]
    assessment_hash: str = field(init=False)

    def __post_init__(self) -> None:
        for value, name in (
            (self.step20_bundle_hash, "step20_bundle_hash"),
            (self.step20_item_hash, "step20_item_hash"),
            (self.candidate_identity_hash, "candidate_identity_hash"),
            (self.temporal_facts_hash, "temporal_facts_hash"),
        ):
            require_sha256_hex(value, name)
        for value, name in (
            (self.evidence_id, "evidence_id"),
            (self.source_id, "source_id"),
            (self.knowledge_version_id, "knowledge_version_id"),
            (self.chunk_id, "chunk_id"),
            (self.logical_subject_identity, "logical_subject_identity"),
        ):
            _text(value, name, 512)
        object.__setattr__(
            self,
            "document_identity",
            _optional_text(self.document_identity, "document_identity", 512),
        )
        object.__setattr__(
            self,
            "version_identity",
            _optional_text(self.version_identity, "version_identity", 512),
        )
        if self.temporal_facts_digest is not None:
            require_sha256_hex(self.temporal_facts_digest, "temporal_facts_digest")
        object.__setattr__(self, "as_of", ensure_utc(self.as_of, "as_of"))
        require_enum_member(
            self.temporal_applicability,
            TemporalApplicability,
            "temporal_applicability",
        )
        require_enum_member(self.freshness_status, FreshnessStatus, "freshness_status")
        require_enum_member(
            self.supersession_status,
            SupersessionStatus,
            "supersession_status",
        )
        object.__setattr__(
            self,
            "conflict_group_id",
            _optional_text(self.conflict_group_id, "conflict_group_id", 512),
        )
        if not isinstance(self.selected, bool):
            raise ContractValidationError("selected must be boolean")
        if self.selected and self.temporal_applicability is not TemporalApplicability.APPLICABLE:
            raise ContractValidationError("only applicable evidence may be selected")
        object.__setattr__(self, "reason_codes", _reason_tuple(self.reason_codes))
        object.__setattr__(
            self,
            "assessment_hash",
            canonical_sha256(self, exclude_fields=("assessment_hash",)),
        )


@dataclass(frozen=True, slots=True)
class TemporalConflictGroup:
    logical_subject_identity: str
    candidate_item_hashes: tuple[str, ...]
    reason_codes: tuple[Step21ReasonCode, ...]
    conflict_group_id: str = field(init=False)
    conflict_group_hash: str = field(init=False)

    def __post_init__(self) -> None:
        _text(self.logical_subject_identity, "logical_subject_identity", 512)
        hashes = _hash_tuple(
            self.candidate_item_hashes,
            "candidate_item_hashes",
            MAX_TEMPORAL_CANDIDATES,
        )
        if len(hashes) < 2:
            raise ContractValidationError("a conflict group requires two candidates")
        object.__setattr__(self, "candidate_item_hashes", hashes)
        object.__setattr__(self, "reason_codes", _reason_tuple(self.reason_codes))
        digest = canonical_sha256(
            {
                "logical_subject_identity": self.logical_subject_identity,
                "candidate_item_hashes": hashes,
                "reason_codes": self.reason_codes,
            }
        )
        object.__setattr__(self, "conflict_group_hash", digest)
        object.__setattr__(self, "conflict_group_id", f"temporal-conflict:{digest}")


@dataclass(frozen=True, slots=True)
class CompletenessFallbackSummary:
    attempted: bool
    maximum_attempts: int
    attempts_used: int
    primary_candidate_count: int
    additional_candidates_considered: int
    additional_candidates_admitted: int
    final_applicable_count: int
    reason_codes: tuple[Step21ReasonCode, ...]
    summary_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.attempted, bool):
            raise ContractValidationError("fallback attempted must be boolean")
        for value, name, maximum in (
            (self.maximum_attempts, "maximum_attempts", MAX_COMPLETENESS_ATTEMPTS),
            (self.attempts_used, "attempts_used", MAX_COMPLETENESS_ATTEMPTS),
            (self.primary_candidate_count, "primary_candidate_count", 40),
            (self.additional_candidates_considered, "additional_candidates_considered", 40),
            (self.additional_candidates_admitted, "additional_candidates_admitted", 40),
            (self.final_applicable_count, "final_applicable_count", MAX_TEMPORAL_CANDIDATES),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum:
                raise ContractValidationError(f"{name} is outside Step 21 bounds")
        if self.attempts_used > self.maximum_attempts:
            raise ContractValidationError("fallback attempts exceed policy")
        if self.attempted != bool(self.attempts_used):
            raise ContractValidationError("fallback attempted flag is inconsistent")
        if self.additional_candidates_admitted > self.additional_candidates_considered:
            raise ContractValidationError("fallback admitted count is invalid")
        object.__setattr__(self, "reason_codes", _reason_tuple(self.reason_codes))
        object.__setattr__(
            self,
            "summary_hash",
            canonical_sha256(self, exclude_fields=("summary_hash",)),
        )


@dataclass(frozen=True, slots=True)
class TemporalResolutionResult:
    schema_version: str
    temporal_request_hash: str
    request_id: str
    tenant_id: str
    user_id: str
    route_hash: str
    selected_hat_id: str | None
    selected_hat_version: str | None
    selected_manifest_digest: str | None
    effective_scope: tuple[ScopeDimension, ...]
    step20_outcome_hash: str
    step20_bundle_hash: str | None
    fallback_outcome_hash: str | None
    fallback_bundle_hash: str | None
    temporal_mode: TemporalQueryMode
    knowledge_as_of: datetime | None
    trusted_now: datetime
    evaluation_as_of: datetime
    temporal_policy_digest: str
    freshness_policy_id: str
    freshness_policy_version: str
    freshness_policy_digest: str
    completeness_policy_digest: str
    answer_status: AnswerStatus | None
    assessments: tuple[TemporalCandidateAssessment, ...]
    resolved_item_hashes: tuple[str, ...]
    excluded_assessment_hashes: tuple[str, ...]
    conflict_groups: tuple[TemporalConflictGroup, ...]
    freshness_summary: Mapping[str, int]
    completeness_fallback: CompletenessFallbackSummary
    evidence_status: EvidenceStatus
    reason_codes: tuple[Step21ReasonCode, ...]
    limitations: tuple[str, ...]
    result_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != STEP21_SCHEMA_VERSION:
            raise ContractValidationError("unsupported Step 21 result schema")
        require_sha256_hex(self.temporal_request_hash, "temporal_request_hash")
        require_sha256_hex(self.route_hash, "route_hash")
        require_sha256_hex(self.step20_outcome_hash, "step20_outcome_hash")
        for value, name in (
            (self.request_id, "request_id"),
            (self.tenant_id, "tenant_id"),
            (self.user_id, "user_id"),
        ):
            _text(value, name, 255)
        selected = (
            self.selected_hat_id,
            self.selected_hat_version,
            self.selected_manifest_digest,
        )
        if any(value is None for value in selected) and any(value is not None for value in selected):
            raise ContractValidationError("selected HAT identity must be complete")
        if self.selected_hat_id is not None:
            _domain_id(self.selected_hat_id, "selected_hat_id")
            _text(self.selected_hat_version, "selected_hat_version", 128)
            require_sha256_hex(self.selected_manifest_digest, "selected_manifest_digest")
        object.__setattr__(
            self,
            "effective_scope",
            _scope_tuple(self.effective_scope, "effective_scope"),
        )
        for value, name in (
            (self.step20_bundle_hash, "step20_bundle_hash"),
            (self.fallback_outcome_hash, "fallback_outcome_hash"),
            (self.fallback_bundle_hash, "fallback_bundle_hash"),
        ):
            if value is not None:
                require_sha256_hex(value, name)
        if (self.fallback_outcome_hash is None) != (self.fallback_bundle_hash is None):
            raise ContractValidationError("fallback identities must be complete")
        require_enum_member(self.temporal_mode, TemporalQueryMode, "temporal_mode")
        if self.knowledge_as_of is not None:
            object.__setattr__(
                self,
                "knowledge_as_of",
                ensure_utc(self.knowledge_as_of, "knowledge_as_of"),
            )
        object.__setattr__(self, "trusted_now", ensure_utc(self.trusted_now, "trusted_now"))
        object.__setattr__(
            self,
            "evaluation_as_of",
            ensure_utc(self.evaluation_as_of, "evaluation_as_of"),
        )
        for value, name in (
            (self.temporal_policy_digest, "temporal_policy_digest"),
            (self.freshness_policy_digest, "freshness_policy_digest"),
            (self.completeness_policy_digest, "completeness_policy_digest"),
        ):
            require_sha256_hex(value, name)
        _domain_id(self.freshness_policy_id, "freshness_policy_id")
        _text(self.freshness_policy_version, "freshness_policy_version", 64)
        if self.answer_status is not None:
            require_enum_member(self.answer_status, AnswerStatus, "answer_status")
        if not isinstance(self.assessments, (tuple, list)):
            raise ContractValidationError("assessments must be ordered")
        assessments = tuple(self.assessments)
        if len(assessments) > MAX_TEMPORAL_CANDIDATES or any(
            not isinstance(item, TemporalCandidateAssessment) for item in assessments
        ):
            raise ContractValidationError("assessments are invalid")
        if len({item.step20_item_hash for item in assessments}) != len(assessments):
            raise ContractValidationError("assessment items must be unique")
        for assessment in assessments:
            verify_temporal_assessment_hash(assessment)
        allowed_bundle_hashes = {
            value
            for value in (self.step20_bundle_hash, self.fallback_bundle_hash)
            if value is not None
        }
        if any(
            assessment.step20_bundle_hash not in allowed_bundle_hashes
            for assessment in assessments
        ):
            raise ContractValidationError(
                "assessment is detached from the verified Step 20 bundles"
            )
        object.__setattr__(self, "assessments", assessments)
        resolved = _ordered_hash_tuple(
            self.resolved_item_hashes,
            "resolved_item_hashes",
            MAX_TEMPORAL_CANDIDATES,
        )
        selected_hashes = tuple(
            item.step20_item_hash for item in assessments if item.selected
        )
        if resolved != selected_hashes:
            raise ContractValidationError("resolved item hashes differ from assessments")
        object.__setattr__(self, "resolved_item_hashes", resolved)
        excluded = _ordered_hash_tuple(
            self.excluded_assessment_hashes,
            "excluded_assessment_hashes",
            MAX_TEMPORAL_CANDIDATES,
        )
        expected_excluded = tuple(
            item.assessment_hash for item in assessments if not item.selected
        )
        if excluded != expected_excluded:
            raise ContractValidationError("excluded assessment hashes are incomplete")
        object.__setattr__(self, "excluded_assessment_hashes", excluded)
        if not isinstance(self.conflict_groups, (tuple, list)):
            raise ContractValidationError("conflict_groups must be ordered")
        groups = tuple(sorted(self.conflict_groups, key=lambda item: item.conflict_group_id))
        if len(groups) > MAX_CONFLICT_GROUPS or any(
            not isinstance(item, TemporalConflictGroup) for item in groups
        ):
            raise ContractValidationError("conflict_groups are invalid")
        if len({item.conflict_group_id for item in groups}) != len(groups):
            raise ContractValidationError("conflict groups must be unique")
        assessment_item_hashes = {item.step20_item_hash for item in assessments}
        assessment_group_ids = {
            item.conflict_group_id
            for item in assessments
            if item.conflict_group_id is not None
        }
        for group in groups:
            verify_conflict_group_hash(group)
            if not set(group.candidate_item_hashes).issubset(assessment_item_hashes):
                raise ContractValidationError(
                    "conflict group references an unknown Step 20 item"
                )
        if not assessment_group_ids.issubset(
            {item.conflict_group_id for item in groups}
        ):
            raise ContractValidationError(
                "assessment conflict identities differ from conflict groups"
            )
        object.__setattr__(self, "conflict_groups", groups)
        if not isinstance(self.freshness_summary, Mapping):
            raise ContractValidationError("freshness_summary must be a mapping")
        summary = dict(self.freshness_summary)
        if set(summary) - {item.value for item in FreshnessStatus} or any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in summary.values()
        ):
            raise ContractValidationError("freshness_summary is invalid")
        if sum(summary.values()) != len(assessments):
            raise ContractValidationError("freshness_summary count is inconsistent")
        object.__setattr__(self, "freshness_summary", freeze_json(dict(sorted(summary.items()))))
        if not isinstance(self.completeness_fallback, CompletenessFallbackSummary):
            raise ContractValidationError("completeness_fallback must be typed")
        verify_fallback_summary_hash(self.completeness_fallback)
        require_enum_member(self.evidence_status, EvidenceStatus, "evidence_status")
        object.__setattr__(self, "reason_codes", _reason_tuple(self.reason_codes))
        limitations = _string_tuple(
            self.limitations,
            "limitations",
            maximum=MAX_LIMITATIONS,
        )
        object.__setattr__(self, "limitations", limitations)
        object.__setattr__(
            self,
            "result_hash",
            canonical_sha256(self, exclude_fields=("result_hash",)),
        )


def _verify_step20_outcome(value: HybridEvidenceOutcome) -> None:
    verify_outcome_hash(value)
    if value.bundle is not None:
        verify_evidence_bundle_hash(value.bundle)


def _verify_route_outcome_binding(
    route: KnowledgeRouteResult,
    outcome: HybridEvidenceOutcome,
) -> None:
    if (
        outcome.request_id != route.request_id
        or outcome.tenant_id != route.tenant_id
        or outcome.user_id != route.user_id
        or outcome.route_hash != route.route_hash
    ):
        raise TemporalBoundaryError(Step21ReasonCode.STEP20_INPUT_BINDING_MISMATCH)
    bundle = outcome.bundle
    if route.knowledge_route is KnowledgeRoute.PASS_THROUGH:
        if bundle is not None or outcome.evidence_status is not EvidenceStatus.NOT_REQUIRED:
            raise TemporalBoundaryError(Step21ReasonCode.STEP20_INPUT_BINDING_MISMATCH)
        return
    if route.knowledge_route in {KnowledgeRoute.HAT_ASSIST, KnowledgeRoute.HAT_ENFORCE}:
        if bundle is None:
            raise TemporalBoundaryError(Step21ReasonCode.STEP20_INPUT_BINDING_MISMATCH)
        if (
            bundle.request_id != route.request_id
            or bundle.tenant_id != route.tenant_id
            or bundle.user_id != route.user_id
            or bundle.route_hash != route.route_hash
            or bundle.selected_hat_id != route.selected_hat_id
            or bundle.selected_hat_version != route.selected_hat_version
            or bundle.selected_manifest_digest != route.selected_manifest_digest
            or bundle.effective_scope != route.effective_scope
        ):
            raise TemporalBoundaryError(Step21ReasonCode.STEP20_INPUT_BINDING_MISMATCH)


def _verify_bundle_binding_equality(
    primary: FrozenEvidenceBundle | None,
    fallback: FrozenEvidenceBundle | None,
) -> None:
    if primary is None or fallback is None:
        raise TemporalBoundaryError(Step21ReasonCode.STEP20_INPUT_BINDING_MISMATCH)
    fields = (
        "request_id",
        "tenant_id",
        "user_id",
        "route_hash",
        "policy_result_hash",
        "selected_hat_id",
        "selected_hat_version",
        "selected_manifest_digest",
        "hat_scope_id",
        "effective_scope",
        "knowledge_policy_decision",
        "execution_authorization_decision",
        "answer_status",
        "embedding_model_id",
        "embedding_model_revision",
        "embedding_model_digest",
        "embedding_dimension",
        "vector_metric_policy",
    )
    if any(getattr(primary, name) != getattr(fallback, name) for name in fields):
        raise TemporalBoundaryError(Step21ReasonCode.STEP20_INPUT_BINDING_MISMATCH)


def verify_temporal_request_hash(value: TemporalResolutionRequest) -> None:
    verify_canonical_hash(value, value.request_hash, exclude_fields=("request_hash",))


def verify_temporal_assessment_hash(value: TemporalCandidateAssessment) -> None:
    verify_canonical_hash(
        value,
        value.assessment_hash,
        exclude_fields=("assessment_hash",),
    )


def verify_conflict_group_hash(value: TemporalConflictGroup) -> None:
    expected = canonical_sha256(
        {
            "logical_subject_identity": value.logical_subject_identity,
            "candidate_item_hashes": value.candidate_item_hashes,
            "reason_codes": value.reason_codes,
        }
    )
    if value.conflict_group_hash != expected or value.conflict_group_id != f"temporal-conflict:{expected}":
        raise IntegrityError("temporal conflict group hash mismatch")


def verify_fallback_summary_hash(value: CompletenessFallbackSummary) -> None:
    verify_canonical_hash(value, value.summary_hash, exclude_fields=("summary_hash",))


def verify_temporal_result_hash(value: TemporalResolutionResult) -> None:
    verify_canonical_hash(value, value.result_hash, exclude_fields=("result_hash",))


__all__ = [
    "COMPLETENESS_POLICY_ID",
    "COMPLETENESS_POLICY_VERSION",
    "CompletenessFallbackSummary",
    "CompletenessPolicy",
    "EvidenceAvailability",
    "FreshnessPolicy",
    "FreshnessStatus",
    "MAX_COMPLETENESS_ATTEMPTS",
    "STEP21_SCHEMA_VERSION",
    "Step21ReasonCode",
    "SupersessionStatus",
    "TEMPORAL_FACTS_DIGEST_SCHEME",
    "TEMPORAL_POLICY_ID",
    "TEMPORAL_POLICY_VERSION",
    "TemporalApplicability",
    "TemporalBoundaryError",
    "TemporalCandidateAssessment",
    "TemporalConflictGroup",
    "TemporalFacts",
    "TemporalPolicy",
    "TemporalQueryMode",
    "TemporalResolutionRequest",
    "TemporalResolutionResult",
    "load_completeness_policy",
    "load_temporal_policy",
    "verify_conflict_group_hash",
    "verify_fallback_summary_hash",
    "verify_temporal_assessment_hash",
    "verify_temporal_request_hash",
    "verify_temporal_result_hash",
]
