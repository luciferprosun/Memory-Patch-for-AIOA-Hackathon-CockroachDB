"""Immutable Step 23 claim, evidence-link, and packet-input contracts.

Step 23 consumes only a verified Draft V1 and the already frozen Step 20/21
evidence universe.  Candidate verdicts are correction inputs, never final
truth, answer, approval, memory, or execution authority.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field

from aioa_memory_kernel.contracts.enums import EvidenceStatus, MemoryTargetScope, StableStringEnum
from aioa_memory_kernel.contracts.evidence import ClaimCandidate as KernelClaimCandidate
from aioa_memory_kernel.contracts.exceptions import ContractValidationError, IntegrityError
from aioa_memory_kernel.contracts.scope import ScopeDimension
from aioa_memory_kernel.contracts.serialization import (
    canonical_json_bytes,
    canonical_sha256,
    require_enum_member,
    require_sha256_hex,
    verify_canonical_hash,
)
from aioa_memory_kernel.evidence import (
    EvidenceBundleItem,
    FrozenEvidenceBundle,
    verify_bundle_item_hash,
    verify_evidence_bundle_hash,
)
from aioa_memory_kernel.modeling import DraftV1, verify_draft_v1_hash
from aioa_memory_kernel.sources import (
    SourceAccessClass,
    SourceAuthorityLevel,
    SourcePublicationState,
)
from aioa_memory_kernel.temporal import (
    FreshnessStatus,
    TemporalApplicability,
    TemporalCandidateAssessment,
    TemporalResolutionResult,
    verify_temporal_assessment_hash,
    verify_temporal_result_hash,
)


STEP23_SCHEMA_VERSION = "1.0.0"
CLAIM_PROCESSING_POLICY_ID = "claim-extraction-evidence-binding-1a"
CLAIM_PROCESSING_POLICY_VERSION = "1"
CLAIM_SPAN_CONVENTION = "draft-v1-unicode-codepoints-start-inclusive-end-exclusive-v1"
EVIDENCE_SPAN_CONVENTION = "step20-excerpt-unicode-codepoints-start-inclusive-end-exclusive-v1"
MAX_CLAIMS = 256
MAX_CLAIM_UTF8_BYTES = 16 * 1024
MAX_EVIDENCE_LINKS = 4096
MAX_LIMITATIONS = 32
MAX_CANONICAL_SNAPSHOT_BYTES = 2 * 1024 * 1024
PERSISTENCE_DECISION = "NOT_REQUIRED_STEP23_FROZEN_PACKET_INPUT_ONLY"


class ClaimType(StableStringEnum):
    FACTUAL = "FACTUAL"
    TEMPORAL = "TEMPORAL"
    LEGAL_NORM = "LEGAL_NORM"
    SOURCE_ASSERTION = "SOURCE_ASSERTION"
    QUANTITATIVE = "QUANTITATIVE"
    RELATIONAL = "RELATIONAL"
    NON_FACTUAL = "NON_FACTUAL"


class ClaimAtomicity(StableStringEnum):
    ATOMIC = "ATOMIC"
    COMPOUND = "COMPOUND"
    NON_FACTUAL = "NON_FACTUAL"


class ClaimEvidenceRelation(StableStringEnum):
    SUPPORTS = "SUPPORTS"
    REFUTES = "REFUTES"
    RELATED_ONLY = "RELATED_ONLY"
    INSUFFICIENT = "INSUFFICIENT"


class ClaimEvidenceCandidateStatus(StableStringEnum):
    SUPPORTED = "SUPPORTED"
    REFUTED = "REFUTED"
    UNVERIFIED = "UNVERIFIED"


class ClaimReasonCode(StableStringEnum):
    CLAIM_EXTRACTED = "CLAIM_EXTRACTED"
    CLAIM_NON_FACTUAL = "CLAIM_NON_FACTUAL"
    CLAIM_COMPOUND = "CLAIM_COMPOUND"
    CLAIM_SPAN_INVALID = "CLAIM_SPAN_INVALID"
    CLAIM_TEXT_MISMATCH = "CLAIM_TEXT_MISMATCH"
    INPUT_HASH_INVALID = "INPUT_HASH_INVALID"
    INPUT_BINDING_MISMATCH = "INPUT_BINDING_MISMATCH"
    EVIDENCE_SUPPORTS = "EVIDENCE_SUPPORTS"
    EVIDENCE_REFUTES = "EVIDENCE_REFUTES"
    EVIDENCE_RELATED_ONLY = "EVIDENCE_RELATED_ONLY"
    EVIDENCE_INSUFFICIENT = "EVIDENCE_INSUFFICIENT"
    TEMPORAL_MISMATCH = "TEMPORAL_MISMATCH"
    TEMPORAL_CONFLICT = "TEMPORAL_CONFLICT"
    FRESHNESS_STALE = "FRESHNESS_STALE"
    SOURCE_AUTHORITY_INSUFFICIENT = "SOURCE_AUTHORITY_INSUFFICIENT"
    EVIDENCE_UNAVAILABLE = "EVIDENCE_UNAVAILABLE"
    CLAIM_SUPPORTED = "CLAIM_SUPPORTED"
    CLAIM_REFUTED = "CLAIM_REFUTED"
    CLAIM_UNVERIFIED = "CLAIM_UNVERIFIED"
    MATERIAL_CONFLICT = "MATERIAL_CONFLICT"
    PACKET_INPUT_FROZEN = "PACKET_INPUT_FROZEN"


class ClaimBoundaryError(RuntimeError):
    """Sanitized fail-closed error at the Step 23 boundary."""

    def __init__(self, reason_code: ClaimReasonCode) -> None:
        if not isinstance(reason_code, ClaimReasonCode):
            raise TypeError("reason_code must be ClaimReasonCode")
        super().__init__(f"Step 23 claim binding denied: {reason_code.value}")
        self.reason_code = reason_code


_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_CLAIM_ID = re.compile(r"^claim-[0-9a-f]{64}$")
_SUPPORTED_AUTHORITY = frozenset(
    {
        SourceAuthorityLevel.OFFICIAL_PRIMARY,
        SourceAuthorityLevel.AUTHORITATIVE_SECONDARY,
    }
)
_REASON_ORDER = {value: index for index, value in enumerate(ClaimReasonCode)}
_RELATION_ORDER = {
    ClaimEvidenceRelation.SUPPORTS: 0,
    ClaimEvidenceRelation.REFUTES: 1,
    ClaimEvidenceRelation.RELATED_ONLY: 2,
    ClaimEvidenceRelation.INSUFFICIENT: 3,
}
_AUTHORITY_ORDER = {
    SourceAuthorityLevel.OFFICIAL_PRIMARY: 0,
    SourceAuthorityLevel.AUTHORITATIVE_SECONDARY: 1,
}


def _text(value: object, field_name: str, maximum_bytes: int = 1024) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or unicodedata.normalize("NFC", value) != value
        or _CONTROL.search(value)
        or len(value.encode("utf-8")) > maximum_bytes
    ):
        raise ContractValidationError(f"{field_name} must be bounded canonical NFC text")
    return value


def _optional_text(
    value: object | None,
    field_name: str,
    maximum_bytes: int = 1024,
) -> str | None:
    return None if value is None else _text(value, field_name, maximum_bytes)


def _scope_tuple(value: object, field_name: str) -> tuple[ScopeDimension, ...]:
    if not isinstance(value, (tuple, list)) or any(
        not isinstance(item, ScopeDimension) for item in value
    ):
        raise ContractValidationError(f"{field_name} must contain ScopeDimension values")
    result = tuple(value)
    if len({item.name for item in result}) != len(result):
        raise ContractValidationError(f"{field_name} names must be unique")
    return result


def _hash_tuple(
    value: object,
    field_name: str,
    maximum: int,
    *,
    ordered: bool,
) -> tuple[str, ...]:
    if not isinstance(value, (tuple, list)) or len(value) > maximum:
        raise ContractValidationError(f"{field_name} is outside Step 23 bounds")
    result = tuple(value)
    for item in result:
        require_sha256_hex(item, field_name)
    if len(set(result)) != len(result):
        raise ContractValidationError(f"{field_name} must be unique")
    if not ordered and result != tuple(sorted(result)):
        raise ContractValidationError(f"{field_name} must be canonically sorted")
    return result


def _reason_tuple(value: object) -> tuple[ClaimReasonCode, ...]:
    if not isinstance(value, (tuple, list)) or any(
        not isinstance(item, ClaimReasonCode) for item in value
    ):
        raise ContractValidationError("reason_codes must contain ClaimReasonCode values")
    result = tuple(sorted(set(value), key=_REASON_ORDER.__getitem__))
    if tuple(value) != result:
        raise ContractValidationError("reason_codes must be unique and canonical")
    return result


def reason_codes(*values: ClaimReasonCode) -> tuple[ClaimReasonCode, ...]:
    """Return a unique, stable Step 23 reason tuple."""

    return tuple(sorted(set(values), key=_REASON_ORDER.__getitem__))


def _limitations(value: object) -> tuple[str, ...]:
    if not isinstance(value, (tuple, list)) or len(value) > MAX_LIMITATIONS:
        raise ContractValidationError("limitations are outside Step 23 bounds")
    result = tuple(value)
    for item in result:
        _text(item, "limitation", 512)
    canonical = tuple(sorted(set(result)))
    if result != canonical:
        raise ContractValidationError("limitations must be unique and canonical")
    return result


def normalize_claim_for_match(value: str) -> str:
    """Create a separate, non-authoritative form used only for exact rules."""

    text = _text(value, "claim text", MAX_CLAIM_UTF8_BYTES)
    folded = " ".join(text.casefold().split())
    return folded.strip(" .!?;:…")


def derive_claim_id(
    draft_v1_hash: str,
    start_offset: int,
    end_offset: int,
    exact_claim_text: str,
) -> str:
    require_sha256_hex(draft_v1_hash, "draft_v1_hash")
    digest = canonical_sha256(
        {
            "draft_v1_hash": draft_v1_hash,
            "start_offset": start_offset,
            "end_offset": end_offset,
            "exact_claim_text": exact_claim_text,
        }
    )
    return f"claim-{digest}"


@dataclass(frozen=True, slots=True)
class ClaimProcessingPolicy:
    policy_id: str = CLAIM_PROCESSING_POLICY_ID
    policy_version: str = CLAIM_PROCESSING_POLICY_VERSION
    span_convention: str = CLAIM_SPAN_CONVENTION
    support_rule: str = "normalized-whole-assertion-equality-v1"
    refutation_rule: str = "single-explicit-negation-counterpart-v1"
    related_rule: str = "bounded-significant-token-overlap-diagnostic-only-v1"
    maximum_claims: int = MAX_CLAIMS
    maximum_evidence_links: int = MAX_EVIDENCE_LINKS
    model_assisted_extraction: bool = False
    policy_digest: str = field(init=False)

    def __post_init__(self) -> None:
        for value, name in (
            (self.policy_id, "policy_id"),
            (self.policy_version, "policy_version"),
            (self.span_convention, "span_convention"),
            (self.support_rule, "support_rule"),
            (self.refutation_rule, "refutation_rule"),
            (self.related_rule, "related_rule"),
        ):
            _text(value, name, 256)
        if self.maximum_claims != MAX_CLAIMS or self.maximum_evidence_links != MAX_EVIDENCE_LINKS:
            raise ContractValidationError("Step 23 resource bounds are fixed")
        if self.model_assisted_extraction is not False:
            raise ContractValidationError("Step 23 V1 extraction is deterministic only")
        object.__setattr__(
            self,
            "policy_digest",
            canonical_sha256(self, exclude_fields=("policy_digest",)),
        )


def load_claim_processing_policy() -> ClaimProcessingPolicy:
    return ClaimProcessingPolicy()


@dataclass(frozen=True, slots=True)
class ClaimRecord:
    draft_id: str
    draft_v1_hash: str
    start_offset: int
    end_offset: int
    exact_claim_text: str
    normalized_match_text: str
    claim_type: ClaimType
    atomicity: ClaimAtomicity
    scope_dimensions: tuple[ScopeDimension, ...]
    reason_codes: tuple[ClaimReasonCode, ...]
    exact_claim_text_sha256: str = field(init=False)
    claim_id: str = field(init=False)
    claim_hash: str = field(init=False)

    def __post_init__(self) -> None:
        _text(self.draft_id, "draft_id", 255)
        require_sha256_hex(self.draft_v1_hash, "draft_v1_hash")
        for value, name in (
            (self.start_offset, "start_offset"),
            (self.end_offset, "end_offset"),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ContractValidationError(f"{name} must be a non-negative integer")
        if self.start_offset >= self.end_offset:
            raise ClaimBoundaryError(ClaimReasonCode.CLAIM_SPAN_INVALID)
        text = _text(self.exact_claim_text, "exact_claim_text", MAX_CLAIM_UTF8_BYTES)
        normalized = normalize_claim_for_match(text)
        if not normalized or self.normalized_match_text != normalized:
            raise ContractValidationError("normalized claim text differs from policy")
        require_enum_member(self.claim_type, ClaimType, "claim_type")
        require_enum_member(self.atomicity, ClaimAtomicity, "atomicity")
        if (self.claim_type is ClaimType.NON_FACTUAL) != (
            self.atomicity is ClaimAtomicity.NON_FACTUAL
        ):
            raise ContractValidationError("non-factual type and atomicity must agree")
        object.__setattr__(
            self,
            "scope_dimensions",
            _scope_tuple(self.scope_dimensions, "scope_dimensions"),
        )
        object.__setattr__(self, "reason_codes", _reason_tuple(self.reason_codes))
        if ClaimReasonCode.CLAIM_EXTRACTED not in self.reason_codes:
            raise ContractValidationError("claim must record deterministic extraction")
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        object.__setattr__(self, "exact_claim_text_sha256", digest)
        claim_id = derive_claim_id(
            self.draft_v1_hash,
            self.start_offset,
            self.end_offset,
            text,
        )
        object.__setattr__(self, "claim_id", claim_id)
        # Validate compatibility with the repository's original Kernel claim slot.
        KernelClaimCandidate(
            claim_id=claim_id,
            draft_id=self.draft_id,
            statement=text,
            claim_category=self.claim_type.value,
            scope_dimensions=self.scope_dimensions,
        )
        object.__setattr__(
            self,
            "claim_hash",
            canonical_sha256(self, exclude_fields=("claim_hash",)),
        )


@dataclass(frozen=True, slots=True)
class ClaimEvidenceLink:
    claim_id: str
    step20_bundle_hash: str
    step20_item_ordinal: int
    step20_item_hash: str
    evidence_id: str
    candidate_identity_hash: str
    source_id: str
    knowledge_version_id: str
    chunk_id: str
    content_sha256: str
    citation_reference: str
    source_reference: str
    authority_level: SourceAuthorityLevel
    publication_state: SourcePublicationState
    scope_digest: str
    effective_scope: tuple[ScopeDimension, ...]
    relation: ClaimEvidenceRelation
    evidence_span_convention: str | None
    evidence_start_offset: int | None
    evidence_end_offset: int | None
    evidence_span_text_sha256: str | None
    temporal_assessment_hash: str
    temporal_applicability: TemporalApplicability
    freshness_status: FreshnessStatus
    conflict_group_id: str | None
    reason_codes: tuple[ClaimReasonCode, ...]
    link_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if _CLAIM_ID.fullmatch(self.claim_id) is None:
            raise ContractValidationError("claim_id is invalid")
        for value, name in (
            (self.step20_bundle_hash, "step20_bundle_hash"),
            (self.step20_item_hash, "step20_item_hash"),
            (self.candidate_identity_hash, "candidate_identity_hash"),
            (self.content_sha256, "content_sha256"),
            (self.scope_digest, "scope_digest"),
            (self.temporal_assessment_hash, "temporal_assessment_hash"),
        ):
            require_sha256_hex(value, name)
        if (
            isinstance(self.step20_item_ordinal, bool)
            or not isinstance(self.step20_item_ordinal, int)
            or self.step20_item_ordinal < 1
        ):
            raise ContractValidationError("step20_item_ordinal must be one-based")
        for value, name, maximum in (
            (self.evidence_id, "evidence_id", 255),
            (self.source_id, "source_id", 255),
            (self.knowledge_version_id, "knowledge_version_id", 255),
            (self.chunk_id, "chunk_id", 255),
            (self.citation_reference, "citation_reference", 4096),
            (self.source_reference, "source_reference", 2048),
        ):
            _text(value, name, maximum)
        require_enum_member(self.authority_level, SourceAuthorityLevel, "authority_level")
        if self.authority_level not in _SUPPORTED_AUTHORITY:
            raise ClaimBoundaryError(ClaimReasonCode.SOURCE_AUTHORITY_INSUFFICIENT)
        require_enum_member(self.publication_state, SourcePublicationState, "publication_state")
        if self.publication_state is not SourcePublicationState.PUBLISHED:
            raise ClaimBoundaryError(ClaimReasonCode.SOURCE_AUTHORITY_INSUFFICIENT)
        object.__setattr__(
            self,
            "effective_scope",
            _scope_tuple(self.effective_scope, "effective_scope"),
        )
        require_enum_member(self.relation, ClaimEvidenceRelation, "relation")
        span_values = (
            self.evidence_span_convention,
            self.evidence_start_offset,
            self.evidence_end_offset,
            self.evidence_span_text_sha256,
        )
        if any(value is None for value in span_values) and any(
            value is not None for value in span_values
        ):
            raise ContractValidationError("evidence span identity must be complete or absent")
        if self.evidence_span_convention is not None:
            if self.evidence_span_convention != EVIDENCE_SPAN_CONVENTION:
                raise ContractValidationError("evidence span convention is invalid")
            if (
                isinstance(self.evidence_start_offset, bool)
                or not isinstance(self.evidence_start_offset, int)
                or isinstance(self.evidence_end_offset, bool)
                or not isinstance(self.evidence_end_offset, int)
                or self.evidence_start_offset < 0
                or self.evidence_start_offset >= self.evidence_end_offset
            ):
                raise ClaimBoundaryError(ClaimReasonCode.CLAIM_SPAN_INVALID)
            require_sha256_hex(
                self.evidence_span_text_sha256,
                "evidence_span_text_sha256",
            )
        require_enum_member(
            self.temporal_applicability,
            TemporalApplicability,
            "temporal_applicability",
        )
        require_enum_member(self.freshness_status, FreshnessStatus, "freshness_status")
        object.__setattr__(
            self,
            "conflict_group_id",
            _optional_text(self.conflict_group_id, "conflict_group_id", 512),
        )
        object.__setattr__(self, "reason_codes", _reason_tuple(self.reason_codes))
        object.__setattr__(
            self,
            "link_hash",
            canonical_sha256(self, exclude_fields=("link_hash",)),
        )


@dataclass(frozen=True, slots=True)
class ClaimEvidenceAssessment:
    claim_id: str
    candidate_status: ClaimEvidenceCandidateStatus
    supporting_link_hashes: tuple[str, ...]
    refuting_link_hashes: tuple[str, ...]
    related_link_hashes: tuple[str, ...]
    insufficient_link_hashes: tuple[str, ...]
    reason_codes: tuple[ClaimReasonCode, ...]
    limitations: tuple[str, ...]
    assessment_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if _CLAIM_ID.fullmatch(self.claim_id) is None:
            raise ContractValidationError("claim_id is invalid")
        require_enum_member(
            self.candidate_status,
            ClaimEvidenceCandidateStatus,
            "candidate_status",
        )
        partitions: list[tuple[str, ...]] = []
        for name in (
            "supporting_link_hashes",
            "refuting_link_hashes",
            "related_link_hashes",
            "insufficient_link_hashes",
        ):
            value = _hash_tuple(getattr(self, name), name, MAX_EVIDENCE_LINKS, ordered=True)
            object.__setattr__(self, name, value)
            partitions.append(value)
        flat = tuple(item for group in partitions for item in group)
        if len(flat) != len(set(flat)):
            raise ContractValidationError("assessment link partitions overlap")
        if self.candidate_status is ClaimEvidenceCandidateStatus.SUPPORTED:
            if not self.supporting_link_hashes or self.refuting_link_hashes:
                raise ContractValidationError("SUPPORTED candidate link partition is invalid")
        if self.candidate_status is ClaimEvidenceCandidateStatus.REFUTED:
            if not self.refuting_link_hashes or self.supporting_link_hashes:
                raise ContractValidationError("REFUTED candidate link partition is invalid")
        object.__setattr__(self, "reason_codes", _reason_tuple(self.reason_codes))
        expected_reason = {
            ClaimEvidenceCandidateStatus.SUPPORTED: ClaimReasonCode.CLAIM_SUPPORTED,
            ClaimEvidenceCandidateStatus.REFUTED: ClaimReasonCode.CLAIM_REFUTED,
            ClaimEvidenceCandidateStatus.UNVERIFIED: ClaimReasonCode.CLAIM_UNVERIFIED,
        }[self.candidate_status]
        if expected_reason not in self.reason_codes:
            raise ContractValidationError("candidate status reason is missing")
        object.__setattr__(self, "limitations", _limitations(self.limitations))
        object.__setattr__(
            self,
            "assessment_hash",
            canonical_sha256(self, exclude_fields=("assessment_hash",)),
        )


@dataclass(frozen=True, slots=True)
class ClaimBindingRequest:
    draft_v1: DraftV1
    step20_bundles: tuple[FrozenEvidenceBundle, ...]
    temporal_result: TemporalResolutionResult
    request_id: str
    tenant_id: str
    user_id: str
    route_hash: str
    selected_hat_id: str | None
    selected_hat_version: str | None
    selected_manifest_digest: str | None
    hat_scope_id: str | None
    effective_scope: tuple[ScopeDimension, ...]
    draft_v1_hash: str
    original_query_digest: str
    step20_bundle_hashes: tuple[str, ...]
    step21_result_hash: str
    policy: ClaimProcessingPolicy = field(default_factory=load_claim_processing_policy)
    request_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.draft_v1, DraftV1):
            raise ClaimBoundaryError(ClaimReasonCode.INPUT_HASH_INVALID)
        if not isinstance(self.temporal_result, TemporalResolutionResult):
            raise ClaimBoundaryError(ClaimReasonCode.INPUT_HASH_INVALID)
        try:
            verify_draft_v1_hash(self.draft_v1)
            verify_temporal_result_hash(self.temporal_result)
        except (ContractValidationError, IntegrityError) as exc:
            raise ClaimBoundaryError(ClaimReasonCode.INPUT_HASH_INVALID) from exc
        if not isinstance(self.step20_bundles, (tuple, list)):
            raise ContractValidationError("step20_bundles must be ordered")
        bundles = tuple(self.step20_bundles)
        if len(bundles) > 2 or any(
            not isinstance(bundle, FrozenEvidenceBundle) for bundle in bundles
        ):
            raise ContractValidationError("step20_bundles are outside Step 23 bounds")
        try:
            for bundle in bundles:
                verify_evidence_bundle_hash(bundle)
                for item in bundle.ordered_items:
                    verify_bundle_item_hash(item)
            for assessment in self.temporal_result.assessments:
                verify_temporal_assessment_hash(assessment)
        except (ContractValidationError, IntegrityError) as exc:
            raise ClaimBoundaryError(ClaimReasonCode.INPUT_HASH_INVALID) from exc
        expected_hashes = tuple(
            value
            for value in (
                self.temporal_result.step20_bundle_hash,
                self.temporal_result.fallback_bundle_hash,
            )
            if value is not None
        )
        by_hash = {bundle.bundle_hash: bundle for bundle in bundles}
        if len(by_hash) != len(bundles) or set(by_hash) != set(expected_hashes):
            raise ClaimBoundaryError(ClaimReasonCode.INPUT_BINDING_MISMATCH)
        ordered_bundles = tuple(by_hash[value] for value in expected_hashes)
        object.__setattr__(self, "step20_bundles", ordered_bundles)
        if tuple(self.step20_bundle_hashes) != expected_hashes:
            raise ClaimBoundaryError(ClaimReasonCode.INPUT_BINDING_MISMATCH)
        object.__setattr__(
            self,
            "step20_bundle_hashes",
            _hash_tuple(expected_hashes, "step20_bundle_hashes", 2, ordered=True),
        )
        for value, name in (
            (self.request_id, "request_id"),
            (self.tenant_id, "tenant_id"),
            (self.user_id, "user_id"),
        ):
            _text(value, name, 255)
        for value, name in (
            (self.route_hash, "route_hash"),
            (self.draft_v1_hash, "draft_v1_hash"),
            (self.original_query_digest, "original_query_digest"),
            (self.step21_result_hash, "step21_result_hash"),
        ):
            require_sha256_hex(value, name)
        selected = (
            self.selected_hat_id,
            self.selected_hat_version,
            self.selected_manifest_digest,
            self.hat_scope_id,
        )
        if any(value is None for value in selected) and any(value is not None for value in selected):
            raise ClaimBoundaryError(ClaimReasonCode.INPUT_BINDING_MISMATCH)
        if self.selected_hat_id is not None:
            _text(self.selected_hat_id, "selected_hat_id", 255)
            _text(self.selected_hat_version, "selected_hat_version", 128)
            require_sha256_hex(self.selected_manifest_digest, "selected_manifest_digest")
            _text(self.hat_scope_id, "hat_scope_id", 255)
        scope = _scope_tuple(self.effective_scope, "effective_scope")
        object.__setattr__(self, "effective_scope", scope)
        if not isinstance(self.policy, ClaimProcessingPolicy) or self.policy != load_claim_processing_policy():
            raise ContractValidationError("claim processing policy is not approved")
        expected_identity = (
            self.temporal_result.request_id,
            self.temporal_result.tenant_id,
            self.temporal_result.user_id,
            self.temporal_result.route_hash,
            self.temporal_result.selected_hat_id,
            self.temporal_result.selected_hat_version,
            self.temporal_result.selected_manifest_digest,
            self.temporal_result.effective_scope,
            self.temporal_result.result_hash,
        )
        supplied_identity = (
            self.request_id,
            self.tenant_id,
            self.user_id,
            self.route_hash,
            self.selected_hat_id,
            self.selected_hat_version,
            self.selected_manifest_digest,
            scope,
            self.step21_result_hash,
        )
        if supplied_identity != expected_identity:
            raise ClaimBoundaryError(ClaimReasonCode.INPUT_BINDING_MISMATCH)
        draft_identity = (
            self.draft_v1.request_id,
            self.draft_v1.tenant_id,
            self.draft_v1.user_id,
            self.draft_v1.route_hash,
            self.draft_v1.step21_result_hash,
            self.draft_v1.step21_evidence_status,
            self.draft_v1.draft_hash,
            self.draft_v1.original_query_digest,
        )
        expected_draft = (
            self.request_id,
            self.tenant_id,
            self.user_id,
            self.route_hash,
            self.step21_result_hash,
            self.temporal_result.evidence_status,
            self.draft_v1_hash,
            self.original_query_digest,
        )
        if draft_identity != expected_draft:
            raise ClaimBoundaryError(ClaimReasonCode.INPUT_BINDING_MISMATCH)
        item_hashes: set[str] = set()
        assessment_hashes = {
            assessment.step20_item_hash for assessment in self.temporal_result.assessments
        }
        for bundle in ordered_bundles:
            if (
                bundle.request_id != self.request_id
                or bundle.tenant_id != self.tenant_id
                or bundle.user_id != self.user_id
                or bundle.route_hash != self.route_hash
                or bundle.selected_hat_id != self.selected_hat_id
                or bundle.selected_hat_version != self.selected_hat_version
                or bundle.selected_manifest_digest != self.selected_manifest_digest
                or bundle.hat_scope_id != self.hat_scope_id
                or bundle.effective_scope != scope
            ):
                raise ClaimBoundaryError(ClaimReasonCode.INPUT_BINDING_MISMATCH)
            for item in bundle.ordered_items:
                if (
                    item.identity.tenant_id != self.tenant_id
                    or item.identity.hat_scope_id != self.hat_scope_id
                    or item.effective_scope != scope
                    or (
                        item.access_class is SourceAccessClass.USER_PRIVATE
                        and item.owner_user_id != self.user_id
                    )
                    or (
                        item.access_class is SourceAccessClass.USER_PRIVATE
                        and item.target_scope is not MemoryTargetScope.USER_PERSONAL_HAT
                    )
                ):
                    raise ClaimBoundaryError(ClaimReasonCode.INPUT_BINDING_MISMATCH)
                item_hashes.add(item.item_hash)
        if item_hashes != assessment_hashes:
            raise ClaimBoundaryError(ClaimReasonCode.INPUT_BINDING_MISMATCH)
        object.__setattr__(
            self,
            "request_hash",
            canonical_sha256(
                self,
                exclude_fields=(
                    "request_hash",
                    "draft_v1",
                    "step20_bundles",
                    "temporal_result",
                ),
            ),
        )


def prepare_claim_binding_request(
    draft_v1: DraftV1,
    step20_bundles: tuple[FrozenEvidenceBundle, ...],
    temporal_result: TemporalResolutionResult,
) -> ClaimBindingRequest:
    hashes = tuple(
        value
        for value in (
            temporal_result.step20_bundle_hash,
            temporal_result.fallback_bundle_hash,
        )
        if value is not None
    )
    by_hash = {bundle.bundle_hash: bundle for bundle in step20_bundles}
    ordered = tuple(by_hash[value] for value in hashes if value in by_hash)
    hat_scope_id = ordered[0].hat_scope_id if ordered else None
    return ClaimBindingRequest(
        draft_v1=draft_v1,
        step20_bundles=ordered,
        temporal_result=temporal_result,
        request_id=temporal_result.request_id,
        tenant_id=temporal_result.tenant_id,
        user_id=temporal_result.user_id,
        route_hash=temporal_result.route_hash,
        selected_hat_id=temporal_result.selected_hat_id,
        selected_hat_version=temporal_result.selected_hat_version,
        selected_manifest_digest=temporal_result.selected_manifest_digest,
        hat_scope_id=hat_scope_id,
        effective_scope=temporal_result.effective_scope,
        draft_v1_hash=draft_v1.draft_hash,
        original_query_digest=draft_v1.original_query_digest,
        step20_bundle_hashes=hashes,
        step21_result_hash=temporal_result.result_hash,
    )


def link_order_key(value: ClaimEvidenceLink) -> tuple[object, ...]:
    return (
        value.claim_id,
        _RELATION_ORDER[value.relation],
        _AUTHORITY_ORDER[value.authority_level],
        0 if value.temporal_applicability is TemporalApplicability.APPLICABLE else 1,
        0 if value.freshness_status is FreshnessStatus.FRESH else 1,
        value.step20_item_ordinal,
        value.candidate_identity_hash,
        value.link_hash,
    )


@dataclass(frozen=True, slots=True)
class PacketInputSnapshot:
    schema_version: str
    claim_binding_request_hash: str
    request_id: str
    tenant_id: str
    user_id: str
    route_hash: str
    selected_hat_id: str | None
    selected_hat_version: str | None
    selected_manifest_digest: str | None
    hat_scope_id: str | None
    effective_scope: tuple[ScopeDimension, ...]
    draft_id: str
    draft_v1_hash: str
    draft_text_sha256: str
    original_query_digest: str
    step20_bundle_ids: tuple[str, ...]
    step20_bundle_hashes: tuple[str, ...]
    step21_result_hash: str
    step21_evidence_status: EvidenceStatus
    claim_processing_policy_digest: str
    ordered_claims: tuple[ClaimRecord, ...]
    ordered_evidence_links: tuple[ClaimEvidenceLink, ...]
    ordered_candidate_assessments: tuple[ClaimEvidenceAssessment, ...]
    reason_codes: tuple[ClaimReasonCode, ...]
    snapshot_id: str = field(init=False)
    snapshot_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != STEP23_SCHEMA_VERSION:
            raise ContractValidationError("unsupported Step 23 snapshot schema")
        for value, name in (
            (self.request_id, "request_id"),
            (self.tenant_id, "tenant_id"),
            (self.user_id, "user_id"),
            (self.draft_id, "draft_id"),
        ):
            _text(value, name, 255)
        for value, name in (
            (self.claim_binding_request_hash, "claim_binding_request_hash"),
            (self.route_hash, "route_hash"),
            (self.draft_v1_hash, "draft_v1_hash"),
            (self.draft_text_sha256, "draft_text_sha256"),
            (self.original_query_digest, "original_query_digest"),
            (self.step21_result_hash, "step21_result_hash"),
            (self.claim_processing_policy_digest, "claim_processing_policy_digest"),
        ):
            require_sha256_hex(value, name)
        selected = (
            self.selected_hat_id,
            self.selected_hat_version,
            self.selected_manifest_digest,
            self.hat_scope_id,
        )
        if any(value is None for value in selected) and any(value is not None for value in selected):
            raise ContractValidationError("selected HAT snapshot identity must be complete")
        if self.selected_hat_id is not None:
            _text(self.selected_hat_id, "selected_hat_id", 255)
            _text(self.selected_hat_version, "selected_hat_version", 128)
            require_sha256_hex(self.selected_manifest_digest, "selected_manifest_digest")
            _text(self.hat_scope_id, "hat_scope_id", 255)
        object.__setattr__(
            self,
            "effective_scope",
            _scope_tuple(self.effective_scope, "effective_scope"),
        )
        bundle_ids = tuple(self.step20_bundle_ids)
        if len(bundle_ids) > 2:
            raise ContractValidationError("step20_bundle_ids are outside Step 23 bounds")
        for value in bundle_ids:
            _text(value, "step20_bundle_id", 255)
        if len(set(bundle_ids)) != len(bundle_ids):
            raise ContractValidationError("step20_bundle_ids must be unique")
        object.__setattr__(self, "step20_bundle_ids", bundle_ids)
        object.__setattr__(
            self,
            "step20_bundle_hashes",
            _hash_tuple(self.step20_bundle_hashes, "step20_bundle_hashes", 2, ordered=True),
        )
        if len(bundle_ids) != len(self.step20_bundle_hashes):
            raise ContractValidationError("Step 20 bundle identity count differs")
        require_enum_member(self.step21_evidence_status, EvidenceStatus, "step21_evidence_status")
        claims = tuple(self.ordered_claims)
        links = tuple(self.ordered_evidence_links)
        assessments = tuple(self.ordered_candidate_assessments)
        if len(claims) > MAX_CLAIMS or any(not isinstance(item, ClaimRecord) for item in claims):
            raise ContractValidationError("ordered_claims are outside Step 23 bounds")
        if len(links) > MAX_EVIDENCE_LINKS or any(
            not isinstance(item, ClaimEvidenceLink) for item in links
        ):
            raise ContractValidationError("ordered_evidence_links are outside Step 23 bounds")
        if len(assessments) != len(claims) or any(
            not isinstance(item, ClaimEvidenceAssessment) for item in assessments
        ):
            raise ContractValidationError("candidate assessments must cover every claim")
        if claims != tuple(sorted(claims, key=lambda item: (item.start_offset, item.end_offset, item.claim_id))):
            raise ContractValidationError("claims are not canonically ordered")
        if links != tuple(sorted(links, key=link_order_key)):
            raise ContractValidationError("evidence links are not canonically ordered")
        if assessments != tuple(sorted(assessments, key=lambda item: item.claim_id)):
            raise ContractValidationError("candidate assessments are not canonically ordered")
        claim_ids = tuple(item.claim_id for item in claims)
        if len(set(claim_ids)) != len(claim_ids):
            raise ContractValidationError("claim IDs must be unique")
        if tuple(item.claim_id for item in assessments) != tuple(sorted(claim_ids)):
            raise ContractValidationError("assessment claim coverage is incomplete")
        link_hashes = {item.link_hash for item in links}
        if len(link_hashes) != len(links):
            raise ContractValidationError("evidence link hashes must be unique")
        if any(item.claim_id not in set(claim_ids) for item in links):
            raise ContractValidationError("evidence link references an unknown claim")
        links_by_claim: dict[str, tuple[ClaimEvidenceLink, ...]] = {
            claim_id: tuple(item for item in links if item.claim_id == claim_id)
            for claim_id in claim_ids
        }
        for assessment in assessments:
            verify_claim_assessment_hash(assessment)
            expected = links_by_claim[assessment.claim_id]
            partitions = {
                ClaimEvidenceRelation.SUPPORTS: assessment.supporting_link_hashes,
                ClaimEvidenceRelation.REFUTES: assessment.refuting_link_hashes,
                ClaimEvidenceRelation.RELATED_ONLY: assessment.related_link_hashes,
                ClaimEvidenceRelation.INSUFFICIENT: assessment.insufficient_link_hashes,
            }
            for relation, hashes in partitions.items():
                if hashes != tuple(item.link_hash for item in expected if item.relation is relation):
                    raise ContractValidationError("assessment link partition differs from snapshot")
        for claim in claims:
            verify_claim_record_hash(claim)
        for link in links:
            verify_claim_evidence_link_hash(link)
        object.__setattr__(self, "ordered_claims", claims)
        object.__setattr__(self, "ordered_evidence_links", links)
        object.__setattr__(self, "ordered_candidate_assessments", assessments)
        object.__setattr__(self, "reason_codes", _reason_tuple(self.reason_codes))
        if ClaimReasonCode.PACKET_INPUT_FROZEN not in self.reason_codes:
            raise ContractValidationError("snapshot freeze reason is missing")
        digest = canonical_sha256(
            self,
            exclude_fields=("snapshot_id", "snapshot_hash"),
        )
        object.__setattr__(self, "snapshot_hash", digest)
        object.__setattr__(self, "snapshot_id", f"claim-input-snapshot:{digest}")
        if len(canonical_json_bytes(self)) > MAX_CANONICAL_SNAPSHOT_BYTES:
            raise ContractValidationError("canonical Step 23 snapshot exceeds its byte bound")


def verify_claim_record_hash(value: ClaimRecord) -> None:
    verify_canonical_hash(value, value.claim_hash, exclude_fields=("claim_hash",))
    expected_id = derive_claim_id(
        value.draft_v1_hash,
        value.start_offset,
        value.end_offset,
        value.exact_claim_text,
    )
    if value.claim_id != expected_id:
        raise IntegrityError("claim ID differs from exact Draft V1 span")
    if value.exact_claim_text_sha256 != hashlib.sha256(
        value.exact_claim_text.encode("utf-8")
    ).hexdigest():
        raise IntegrityError("claim text digest mismatch")


def verify_claim_evidence_link_hash(value: ClaimEvidenceLink) -> None:
    verify_canonical_hash(value, value.link_hash, exclude_fields=("link_hash",))


def verify_claim_assessment_hash(value: ClaimEvidenceAssessment) -> None:
    verify_canonical_hash(value, value.assessment_hash, exclude_fields=("assessment_hash",))


def verify_claim_binding_request_hash(value: ClaimBindingRequest) -> None:
    expected = canonical_sha256(
        value,
        exclude_fields=(
            "request_hash",
            "draft_v1",
            "step20_bundles",
            "temporal_result",
        ),
    )
    require_sha256_hex(value.request_hash, "request_hash")
    if expected != value.request_hash:
        raise IntegrityError("claim binding request hash mismatch")
    # Reconstructing repeats all nested hash and binding validation.
    ClaimBindingRequest(
        draft_v1=value.draft_v1,
        step20_bundles=value.step20_bundles,
        temporal_result=value.temporal_result,
        request_id=value.request_id,
        tenant_id=value.tenant_id,
        user_id=value.user_id,
        route_hash=value.route_hash,
        selected_hat_id=value.selected_hat_id,
        selected_hat_version=value.selected_hat_version,
        selected_manifest_digest=value.selected_manifest_digest,
        hat_scope_id=value.hat_scope_id,
        effective_scope=value.effective_scope,
        draft_v1_hash=value.draft_v1_hash,
        original_query_digest=value.original_query_digest,
        step20_bundle_hashes=value.step20_bundle_hashes,
        step21_result_hash=value.step21_result_hash,
        policy=value.policy,
    )


def verify_packet_input_snapshot_hash(value: PacketInputSnapshot) -> None:
    expected = canonical_sha256(
        value,
        exclude_fields=("snapshot_id", "snapshot_hash"),
    )
    require_sha256_hex(value.snapshot_hash, "snapshot_hash")
    if expected != value.snapshot_hash:
        raise IntegrityError("packet-input snapshot hash mismatch")
    if value.snapshot_id != f"claim-input-snapshot:{value.snapshot_hash}":
        raise IntegrityError("packet-input snapshot ID differs from its hash")
    for claim in value.ordered_claims:
        verify_claim_record_hash(claim)
    for link in value.ordered_evidence_links:
        verify_claim_evidence_link_hash(link)
    for assessment in value.ordered_candidate_assessments:
        verify_claim_assessment_hash(assessment)


def verify_snapshot_against_request(
    snapshot: PacketInputSnapshot,
    request: ClaimBindingRequest,
) -> None:
    verify_claim_binding_request_hash(request)
    verify_packet_input_snapshot_hash(snapshot)
    expected = (
        request.request_hash,
        request.request_id,
        request.tenant_id,
        request.user_id,
        request.route_hash,
        request.selected_hat_id,
        request.selected_hat_version,
        request.selected_manifest_digest,
        request.hat_scope_id,
        request.effective_scope,
        request.draft_v1.draft_id,
        request.draft_v1_hash,
        request.draft_v1.draft_text_sha256,
        request.original_query_digest,
        tuple(bundle.evidence_bundle_id for bundle in request.step20_bundles),
        request.step20_bundle_hashes,
        request.step21_result_hash,
        request.temporal_result.evidence_status,
        request.policy.policy_digest,
    )
    actual = (
        snapshot.claim_binding_request_hash,
        snapshot.request_id,
        snapshot.tenant_id,
        snapshot.user_id,
        snapshot.route_hash,
        snapshot.selected_hat_id,
        snapshot.selected_hat_version,
        snapshot.selected_manifest_digest,
        snapshot.hat_scope_id,
        snapshot.effective_scope,
        snapshot.draft_id,
        snapshot.draft_v1_hash,
        snapshot.draft_text_sha256,
        snapshot.original_query_digest,
        snapshot.step20_bundle_ids,
        snapshot.step20_bundle_hashes,
        snapshot.step21_result_hash,
        snapshot.step21_evidence_status,
        snapshot.claim_processing_policy_digest,
    )
    if actual != expected:
        raise ClaimBoundaryError(ClaimReasonCode.INPUT_BINDING_MISMATCH)
    draft_text = request.draft_v1.draft_text
    for claim in snapshot.ordered_claims:
        if (
            claim.draft_id != request.draft_v1.draft_id
            or claim.draft_v1_hash != request.draft_v1_hash
            or claim.end_offset > len(draft_text)
            or draft_text[claim.start_offset : claim.end_offset] != claim.exact_claim_text
        ):
            raise ClaimBoundaryError(ClaimReasonCode.CLAIM_TEXT_MISMATCH)
    items = {
        item.item_hash: item
        for bundle in request.step20_bundles
        for item in bundle.ordered_items
    }
    temporal = {
        item.assessment_hash: item for item in request.temporal_result.assessments
    }
    for link in snapshot.ordered_evidence_links:
        item = items.get(link.step20_item_hash)
        assessment = temporal.get(link.temporal_assessment_hash)
        if item is None or assessment is None:
            raise ClaimBoundaryError(ClaimReasonCode.INPUT_BINDING_MISMATCH)
        if (
            item.item_hash != link.step20_item_hash
            or item.identity.identity_hash != link.candidate_identity_hash
            or item.identity.source_id != link.source_id
            or item.identity.knowledge_version_id != link.knowledge_version_id
            or item.identity.chunk_id != link.chunk_id
            or item.identity.content_sha256 != link.content_sha256
            or item.authority_level is not link.authority_level
            or item.publication_state is not link.publication_state
            or assessment.step20_item_hash != link.step20_item_hash
            or assessment.temporal_applicability is not link.temporal_applicability
            or assessment.freshness_status is not link.freshness_status
            or assessment.conflict_group_id != link.conflict_group_id
        ):
            raise ClaimBoundaryError(ClaimReasonCode.INPUT_BINDING_MISMATCH)
        if link.evidence_start_offset is not None:
            span = item.excerpt.text[link.evidence_start_offset : link.evidence_end_offset]
            if hashlib.sha256(span.encode("utf-8")).hexdigest() != link.evidence_span_text_sha256:
                raise ClaimBoundaryError(ClaimReasonCode.CLAIM_TEXT_MISMATCH)


__all__ = [
    "CLAIM_PROCESSING_POLICY_ID",
    "CLAIM_PROCESSING_POLICY_VERSION",
    "CLAIM_SPAN_CONVENTION",
    "ClaimAtomicity",
    "ClaimBindingRequest",
    "ClaimBoundaryError",
    "ClaimEvidenceAssessment",
    "ClaimEvidenceCandidateStatus",
    "ClaimEvidenceLink",
    "ClaimEvidenceRelation",
    "ClaimProcessingPolicy",
    "ClaimReasonCode",
    "ClaimRecord",
    "ClaimType",
    "EVIDENCE_SPAN_CONVENTION",
    "MAX_CLAIMS",
    "MAX_EVIDENCE_LINKS",
    "PERSISTENCE_DECISION",
    "PacketInputSnapshot",
    "STEP23_SCHEMA_VERSION",
    "derive_claim_id",
    "link_order_key",
    "load_claim_processing_policy",
    "normalize_claim_for_match",
    "prepare_claim_binding_request",
    "reason_codes",
    "verify_claim_assessment_hash",
    "verify_claim_binding_request_hash",
    "verify_claim_evidence_link_hash",
    "verify_claim_record_hash",
    "verify_packet_input_snapshot_hash",
    "verify_snapshot_against_request",
]
