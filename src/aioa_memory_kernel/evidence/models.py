"""Immutable Step 20 hybrid-ranking and Evidence Bundle contracts.

Step 20 consumes only verified Step 18 lexical results and Step 19 vector
results.  These values rank already-eligible evidence; they do not create
source authority, answer authority, approval, or execution capability.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from aioa_memory_kernel.contracts.enums import (
    AnswerStatus,
    EvidenceStatus,
    KnowledgeRoute,
    MemoryTargetScope,
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
    freeze_json,
    require_enum_member,
    require_sha256_hex,
    verify_canonical_hash,
)
from aioa_memory_kernel.embeddings import load_approved_model_spec
from aioa_memory_kernel.routing import (
    ExecutionAuthorizationDecision,
    KnowledgePolicyDecision,
    KnowledgeRouteResult,
    PolicyGateResult,
    verify_policy_result_hash,
    verify_route_hash,
)
from aioa_memory_kernel.sources import (
    SourceAccessClass,
    SourceAuthorityLevel,
    SourcePublicationState,
)


STEP20_SCHEMA_VERSION = "1.0.0"
STEP18_RETRIEVAL_POLICY_VERSION = "exact-full-text-retrieval-1a"
RANKING_POLICY_ID = "hybrid-retrieval-ranking-1a"
RANKING_POLICY_VERSION = "1"
RRF_K = 60
RRF_SCALE = 1_000_000_000
MAX_UPSTREAM_RESULTS_PER_MODALITY = 100
MAX_MERGED_CANDIDATES = 500
MAX_BUNDLE_ITEMS = 40
MAX_ITEMS_PER_SOURCE = 3
MAX_ITEMS_PER_KNOWLEDGE_VERSION = 4
MAX_EXACT_PRIORITY_ITEMS = 8
DEFAULT_CONTEXT_BUDGET_BYTES = 65_536
MAX_CONTEXT_BUDGET_BYTES = 262_144
MAX_EXCERPT_BYTES_PER_ITEM = 8_192
MIN_PARTIAL_EXCERPT_BYTES = 256
MAX_CANONICAL_BUNDLE_BYTES = 524_288
VECTOR_METRIC_POLICY = "unit-normalized-l2-v1"


class HybridModality(StableStringEnum):
    EXACT_IDENTIFIER = "EXACT_IDENTIFIER"
    STATUTE_SECTION = "STATUTE_SECTION"
    FULL_TEXT = "FULL_TEXT"
    KEYWORD = "KEYWORD"
    VECTOR = "VECTOR"


_MODALITY_ORDER = {
    HybridModality.STATUTE_SECTION: 0,
    HybridModality.EXACT_IDENTIFIER: 1,
    HybridModality.FULL_TEXT: 2,
    HybridModality.VECTOR: 3,
    HybridModality.KEYWORD: 4,
}
_MODALITY_WEIGHTS = {
    HybridModality.STATUTE_SECTION: 8,
    HybridModality.EXACT_IDENTIFIER: 8,
    HybridModality.FULL_TEXT: 4,
    HybridModality.VECTOR: 3,
    HybridModality.KEYWORD: 2,
}


class RetrievalCoverageStatus(StableStringEnum):
    """Completeness relative only to the bounded Step 20 request."""

    EMPTY = "EMPTY"
    PARTIAL = "PARTIAL"
    COMPLETE = "COMPLETE"
    INVALID = "INVALID"


class Step20ReasonCode(StableStringEnum):
    HYBRID_OK = "HYBRID_OK"
    NO_HAT_SELECTED = "NO_HAT_SELECTED"
    AMBIGUOUS_ROUTE = "AMBIGUOUS_ROUTE"
    HYBRID_INPUT_REQUIRED = "HYBRID_INPUT_REQUIRED"
    HYBRID_INPUT_HASH_INVALID = "HYBRID_INPUT_HASH_INVALID"
    HYBRID_INPUT_BINDING_MISMATCH = "HYBRID_INPUT_BINDING_MISMATCH"
    HYBRID_MODEL_MISMATCH = "HYBRID_MODEL_MISMATCH"
    HYBRID_SCOPE_MISMATCH = "HYBRID_SCOPE_MISMATCH"
    HYBRID_CANDIDATE_INVALID = "HYBRID_CANDIDATE_INVALID"
    HYBRID_CANDIDATE_METADATA_CONFLICT = "HYBRID_CANDIDATE_METADATA_CONFLICT"
    HYBRID_DUPLICATE_MERGED = "HYBRID_DUPLICATE_MERGED"
    HYBRID_EXACT_PRIORITY = "HYBRID_EXACT_PRIORITY"
    HYBRID_MULTI_MODAL_SUPPORT = "HYBRID_MULTI_MODAL_SUPPORT"
    HYBRID_VECTOR_ONLY = "HYBRID_VECTOR_ONLY"
    HYBRID_RANKED = "HYBRID_RANKED"
    DIVERSITY_SOURCE_CAP = "DIVERSITY_SOURCE_CAP"
    DIVERSITY_VERSION_CAP = "DIVERSITY_VERSION_CAP"
    DIVERSITY_EXACT_CAP = "DIVERSITY_EXACT_CAP"
    DIVERSITY_GLOBAL_LIMIT = "DIVERSITY_GLOBAL_LIMIT"
    CONTEXT_BUDGET_EXCLUDED = "CONTEXT_BUDGET_EXCLUDED"
    CONTEXT_EXCERPT_TRUNCATED = "CONTEXT_EXCERPT_TRUNCATED"
    BUNDLE_TRUNCATED = "BUNDLE_TRUNCATED"
    NO_ADMISSIBLE_EVIDENCE = "NO_ADMISSIBLE_EVIDENCE"
    EVIDENCE_BUNDLE_INVALID = "EVIDENCE_BUNDLE_INVALID"
    EVIDENCE_BUNDLE_PERSISTENCE_ERROR = "EVIDENCE_BUNDLE_PERSISTENCE_ERROR"
    EVIDENCE_BUNDLE_REPLAY_CONFLICT = "EVIDENCE_BUNDLE_REPLAY_CONFLICT"


class Step20BoundaryError(RuntimeError):
    """Sanitized fail-closed Step 20 error."""

    def __init__(self, reason_code: Step20ReasonCode) -> None:
        if not isinstance(reason_code, Step20ReasonCode):
            raise TypeError("reason_code must be a Step20ReasonCode")
        super().__init__(f"Step 20 evidence assembly denied: {reason_code.value}")
        self.reason_code = reason_code
        self.retrieval_coverage = RetrievalCoverageStatus.INVALID
        self.evidence_status = EvidenceStatus.INVALID


_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_DOMAIN_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_FORBIDDEN_METADATA_KEYS = (
    "api_key",
    "authorization",
    "credential",
    "password",
    "private_key",
    "secret",
    "token",
)
_SUPPORTED_AUTHORITY = frozenset(
    {
        SourceAuthorityLevel.OFFICIAL_PRIMARY,
        SourceAuthorityLevel.AUTHORITATIVE_SECONDARY,
    }
)


def modality_order(value: HybridModality) -> int:
    require_enum_member(value, HybridModality, "modality")
    return _MODALITY_ORDER[value]


def modality_weight(value: HybridModality) -> int:
    require_enum_member(value, HybridModality, "modality")
    return _MODALITY_WEIGHTS[value]


def _text(value: object, field_name: str, maximum_bytes: int = 1024) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ContractValidationError(f"{field_name} must be canonical text")
    if unicodedata.normalize("NFC", value) != value or _CONTROL.search(value):
        raise ContractValidationError(f"{field_name} must be NFC without controls")
    if len(value.encode("utf-8")) > maximum_bytes:
        raise ContractValidationError(f"{field_name} exceeds its byte limit")
    return value


def _content(value: object, field_name: str, maximum_bytes: int) -> str:
    if not isinstance(value, str) or not value:
        raise ContractValidationError(f"{field_name} must be non-empty text")
    if unicodedata.normalize("NFC", value) != value:
        raise ContractValidationError(f"{field_name} must use Unicode NFC")
    for character in value:
        if unicodedata.category(character) == "Cc" and character not in {"\t", "\n"}:
            raise ContractValidationError(f"{field_name} contains a prohibited control")
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


def _reject_float_or_secret(value: Any, path: str = "metadata") -> None:
    if isinstance(value, float):
        raise ContractValidationError(
            f"{path} must not contain native float hash material"
        )
    if isinstance(value, Mapping):
        for key, child in value.items():
            lowered = str(key).lower()
            if any(marker in lowered for marker in _FORBIDDEN_METADATA_KEYS):
                raise ContractValidationError(f"{path} contains forbidden secret metadata")
            _reject_float_or_secret(child, f"{path}.{key}")
    elif isinstance(value, (tuple, list, set, frozenset)):
        for index, child in enumerate(value):
            _reject_float_or_secret(child, f"{path}[{index}]")


def _mapping(value: object, field_name: str, maximum_bytes: int) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise ContractValidationError(f"{field_name} must be a string-keyed object")
    _reject_float_or_secret(value, field_name)
    frozen = freeze_json(value)
    if len(canonical_json_bytes(frozen)) > maximum_bytes:
        raise ContractValidationError(f"{field_name} exceeds its byte limit")
    return frozen


def _hash_tuple(value: object, field_name: str, maximum: int) -> tuple[str, ...]:
    if not isinstance(value, (tuple, list)):
        raise ContractValidationError(f"{field_name} must be an ordered tuple")
    result = tuple(sorted(value))
    if len(result) > maximum or len(result) != len(set(result)):
        raise ContractValidationError(f"{field_name} must be bounded and unique")
    for digest in result:
        require_sha256_hex(digest, field_name)
    return result


def _reason_tuple(value: object) -> tuple[Step20ReasonCode, ...]:
    if not isinstance(value, (tuple, list)):
        raise ContractValidationError("reason_codes must be ordered")
    result = tuple(value)
    if any(not isinstance(item, Step20ReasonCode) for item in result):
        raise ContractValidationError("reason_codes must use Step20ReasonCode")
    if len(result) != len(set(result)):
        raise ContractValidationError("reason_codes must be unique")
    return result


def _modality_tuple(
    value: object,
    field_name: str,
    *,
    allow_empty: bool,
) -> tuple[HybridModality, ...]:
    if not isinstance(value, (tuple, list)):
        raise ContractValidationError(f"{field_name} must be ordered")
    if any(not isinstance(item, HybridModality) for item in value):
        raise ContractValidationError(f"{field_name} contains an unknown modality")
    result = tuple(sorted(value, key=modality_order))
    if len(result) != len(set(result)) or (not allow_empty and not result):
        raise ContractValidationError(f"{field_name} must be unique and non-empty")
    return result


def _expected_match_class(
    contributions: tuple[ModalityContribution, ...],
) -> int:
    modalities = {item.modality for item in contributions}
    if HybridModality.STATUTE_SECTION in modalities:
        return 0
    if HybridModality.EXACT_IDENTIFIER in modalities:
        return 1
    if len(modalities) >= 2:
        return 2
    if modalities & {HybridModality.FULL_TEXT, HybridModality.KEYWORD}:
        return 3
    return 4


def _expected_candidate_reasons(
    contributions: tuple[ModalityContribution, ...],
    match_class: int,
) -> tuple[Step20ReasonCode, ...]:
    reasons: list[Step20ReasonCode] = []
    if len(contributions) > 1:
        reasons.append(Step20ReasonCode.HYBRID_DUPLICATE_MERGED)
    if match_class in {0, 1}:
        reasons.append(Step20ReasonCode.HYBRID_EXACT_PRIORITY)
    elif match_class == 2:
        reasons.append(Step20ReasonCode.HYBRID_MULTI_MODAL_SUPPORT)
    elif match_class == 4:
        reasons.append(Step20ReasonCode.HYBRID_VECTOR_ONLY)
    reasons.append(Step20ReasonCode.HYBRID_RANKED)
    return tuple(reasons)


@dataclass(frozen=True, slots=True)
class RankingPolicy:
    policy_id: str = field(init=False, default=RANKING_POLICY_ID)
    policy_version: str = field(init=False, default=RANKING_POLICY_VERSION)
    rrf_k: int = field(init=False, default=RRF_K)
    rrf_scale: int = field(init=False, default=RRF_SCALE)
    modality_weights: Mapping[str, int] = field(init=False)
    policy_digest: str = field(init=False)

    def __post_init__(self) -> None:
        weights = freeze_json(
            {
                modality.value: _MODALITY_WEIGHTS[modality]
                for modality in sorted(HybridModality, key=modality_order)
            }
        )
        object.__setattr__(self, "modality_weights", weights)
        object.__setattr__(
            self,
            "policy_digest",
            canonical_sha256(self, exclude_fields=("policy_digest",)),
        )


@dataclass(frozen=True, slots=True)
class DiversityPolicy:
    policy_id: str = field(init=False, default="hybrid-diversity-1a")
    policy_version: str = field(init=False, default="1")
    maximum_bundle_items: int = field(init=False, default=MAX_BUNDLE_ITEMS)
    maximum_items_per_source: int = field(init=False, default=MAX_ITEMS_PER_SOURCE)
    maximum_items_per_knowledge_version: int = field(
        init=False,
        default=MAX_ITEMS_PER_KNOWLEDGE_VERSION,
    )
    maximum_exact_priority_items: int = field(
        init=False,
        default=MAX_EXACT_PRIORITY_ITEMS,
    )
    policy_digest: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "policy_digest",
            canonical_sha256(self, exclude_fields=("policy_digest",)),
        )


def load_ranking_policy() -> RankingPolicy:
    return RankingPolicy()


def load_diversity_policy() -> DiversityPolicy:
    return DiversityPolicy()


@dataclass(frozen=True, slots=True)
class CandidateIdentity:
    tenant_id: str
    hat_scope_id: str
    source_id: str
    knowledge_version_id: str
    chunk_id: str
    content_sha256: str
    identity_hash: str = field(init=False)

    def __post_init__(self) -> None:
        for value, name in (
            (self.tenant_id, "tenant_id"),
            (self.hat_scope_id, "hat_scope_id"),
            (self.source_id, "source_id"),
            (self.knowledge_version_id, "knowledge_version_id"),
            (self.chunk_id, "chunk_id"),
        ):
            _text(value, name, 255)
        require_sha256_hex(self.content_sha256, "content_sha256")
        object.__setattr__(
            self,
            "identity_hash",
            canonical_sha256(self, exclude_fields=("identity_hash",)),
        )


@dataclass(frozen=True, slots=True)
class ModalityContribution:
    modality: HybridModality
    upstream_request_hash: str
    upstream_result_hash: str
    upstream_candidate_hash: str
    one_based_rank: int
    retrieval_local_value: str | None
    fixed_point_contribution: int
    contribution_hash: str = field(init=False)

    def __post_init__(self) -> None:
        require_enum_member(self.modality, HybridModality, "modality")
        for value, name in (
            (self.upstream_request_hash, "upstream_request_hash"),
            (self.upstream_result_hash, "upstream_result_hash"),
            (self.upstream_candidate_hash, "upstream_candidate_hash"),
        ):
            require_sha256_hex(value, name)
        if (
            isinstance(self.one_based_rank, bool)
            or not isinstance(self.one_based_rank, int)
            or not 1 <= self.one_based_rank <= MAX_UPSTREAM_RESULTS_PER_MODALITY
        ):
            raise ContractValidationError("one_based_rank is outside Step 20 bounds")
        if self.retrieval_local_value is not None:
            _text(self.retrieval_local_value, "retrieval_local_value", 128)
        expected = (RRF_SCALE * modality_weight(self.modality)) // (
            RRF_K + self.one_based_rank
        )
        if self.fixed_point_contribution != expected:
            raise ContractValidationError("fixed-point contribution differs from policy")
        object.__setattr__(
            self,
            "contribution_hash",
            canonical_sha256(self, exclude_fields=("contribution_hash",)),
        )


@dataclass(frozen=True, slots=True)
class HybridCandidate:
    identity: CandidateIdentity
    chunk_ordinal: int
    content: str
    language_tag: str | None
    authority_level: SourceAuthorityLevel
    authority_basis: Mapping[str, Any]
    source_kind: str
    source_reference: str
    publication_state: SourcePublicationState
    access_class: SourceAccessClass
    target_scope: MemoryTargetScope
    owner_user_id: str | None
    personal_memory_space_id: str | None
    scope_digest: str
    registry_digest: str
    artifact_digest: str
    snapshot_id: str
    structured_metadata: Mapping[str, Any]
    effective_scope: tuple[ScopeDimension, ...]
    vector_model_digest: str | None
    vector_embedding_bytes_sha256: str | None
    contributions: tuple[ModalityContribution, ...]
    match_class: int
    fused_score: int
    reason_codes: tuple[Step20ReasonCode, ...]
    modality_count: int = field(init=False)
    candidate_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.identity, CandidateIdentity):
            raise ContractValidationError("identity must be CandidateIdentity")
        verify_candidate_identity_hash(self.identity)
        if (
            isinstance(self.chunk_ordinal, bool)
            or not isinstance(self.chunk_ordinal, int)
            or self.chunk_ordinal < 0
        ):
            raise ContractValidationError("chunk_ordinal must be non-negative")
        content = _content(self.content, "content", 64 * 1024)
        if hashlib.sha256(content.encode("utf-8")).hexdigest() != self.identity.content_sha256:
            raise Step20BoundaryError(Step20ReasonCode.HYBRID_CANDIDATE_INVALID)
        object.__setattr__(self, "content", content)
        object.__setattr__(
            self,
            "language_tag",
            _optional_text(self.language_tag, "language_tag", 64),
        )
        require_enum_member(self.authority_level, SourceAuthorityLevel, "authority_level")
        if self.authority_level not in _SUPPORTED_AUTHORITY:
            raise Step20BoundaryError(Step20ReasonCode.HYBRID_CANDIDATE_INVALID)
        object.__setattr__(
            self,
            "authority_basis",
            _mapping(self.authority_basis, "authority_basis", 16 * 1024),
        )
        _text(self.source_kind, "source_kind", 1024)
        _text(self.source_reference, "source_reference", 2048)
        require_enum_member(self.publication_state, SourcePublicationState, "publication_state")
        if self.publication_state is not SourcePublicationState.PUBLISHED:
            raise Step20BoundaryError(Step20ReasonCode.HYBRID_CANDIDATE_INVALID)
        require_enum_member(self.access_class, SourceAccessClass, "access_class")
        require_enum_member(self.target_scope, MemoryTargetScope, "target_scope")
        owner = _optional_text(self.owner_user_id, "owner_user_id", 255)
        personal = _optional_text(
            self.personal_memory_space_id,
            "personal_memory_space_id",
            255,
        )
        if self.access_class is SourceAccessClass.USER_PRIVATE:
            if owner is None or personal is None or self.target_scope is not MemoryTargetScope.USER_PERSONAL_HAT:
                raise Step20BoundaryError(Step20ReasonCode.HYBRID_CANDIDATE_INVALID)
        elif owner is not None or personal is not None or self.target_scope is not MemoryTargetScope.SHARED_KNOWLEDGE_HAT:
            raise Step20BoundaryError(Step20ReasonCode.HYBRID_CANDIDATE_INVALID)
        object.__setattr__(self, "owner_user_id", owner)
        object.__setattr__(self, "personal_memory_space_id", personal)
        for value, name in (
            (self.scope_digest, "scope_digest"),
            (self.registry_digest, "registry_digest"),
            (self.artifact_digest, "artifact_digest"),
        ):
            require_sha256_hex(value, name)
        _text(self.snapshot_id, "snapshot_id", 255)
        metadata = _mapping(
            self.structured_metadata,
            "structured_metadata",
            32 * 1024,
        )
        redaction_state = metadata.get("redaction_state")
        if redaction_state not in {None, "NOT_REQUIRED", "VERIFIED"}:
            raise Step20BoundaryError(Step20ReasonCode.HYBRID_CANDIDATE_INVALID)
        if metadata.get("model_generated") is True:
            raise Step20BoundaryError(Step20ReasonCode.HYBRID_CANDIDATE_INVALID)
        object.__setattr__(self, "structured_metadata", metadata)
        object.__setattr__(
            self,
            "effective_scope",
            _scope_tuple(self.effective_scope, "effective_scope"),
        )
        vector_values = (
            self.vector_model_digest,
            self.vector_embedding_bytes_sha256,
        )
        if any(value is not None for value in vector_values):
            if any(value is None for value in vector_values):
                raise ContractValidationError("vector identity must be complete")
            require_sha256_hex(self.vector_model_digest, "vector_model_digest")
            require_sha256_hex(
                self.vector_embedding_bytes_sha256,
                "vector_embedding_bytes_sha256",
            )
            if self.vector_model_digest != load_approved_model_spec().model_digest:
                raise Step20BoundaryError(Step20ReasonCode.HYBRID_MODEL_MISMATCH)
        if not isinstance(self.contributions, (tuple, list)):
            raise ContractValidationError("contributions must be ordered")
        contributions = tuple(
            sorted(self.contributions, key=lambda item: modality_order(item.modality))
        )
        if not contributions or any(
            not isinstance(item, ModalityContribution) for item in contributions
        ):
            raise ContractValidationError("contributions must be typed and non-empty")
        if len({item.modality for item in contributions}) != len(contributions):
            raise ContractValidationError("candidate modalities must be unique")
        for contribution in contributions:
            verify_contribution_hash(contribution)
        has_vector = any(
            item.modality is HybridModality.VECTOR for item in contributions
        )
        if has_vector != (self.vector_model_digest is not None):
            raise ContractValidationError("vector contribution and identity must agree")
        object.__setattr__(self, "contributions", contributions)
        if (
            isinstance(self.match_class, bool)
            or not isinstance(self.match_class, int)
            or self.match_class != _expected_match_class(contributions)
        ):
            raise ContractValidationError("match_class is invalid")
        expected_score = sum(item.fixed_point_contribution for item in contributions)
        if self.fused_score != expected_score:
            raise ContractValidationError("fused_score differs from contributions")
        reasons = _reason_tuple(self.reason_codes)
        if reasons != _expected_candidate_reasons(contributions, self.match_class):
            raise ContractValidationError("candidate reason codes differ from policy")
        object.__setattr__(self, "reason_codes", reasons)
        object.__setattr__(self, "modality_count", len(contributions))
        object.__setattr__(
            self,
            "candidate_hash",
            canonical_sha256(self, exclude_fields=("candidate_hash",)),
        )


@dataclass(frozen=True, slots=True)
class EvidenceExcerpt:
    text: str
    full_content_sha256: str
    start_byte: int
    end_byte: int
    utf8_byte_length: int
    excerpt_sha256: str
    truncated: bool

    def __post_init__(self) -> None:
        text = _content(
            self.text,
            "excerpt text",
            MAX_EXCERPT_BYTES_PER_ITEM,
        )
        payload = text.encode("utf-8")
        require_sha256_hex(self.full_content_sha256, "full_content_sha256")
        for value, name in (
            (self.start_byte, "start_byte"),
            (self.end_byte, "end_byte"),
            (self.utf8_byte_length, "utf8_byte_length"),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ContractValidationError(f"{name} must be non-negative")
        if self.start_byte != 0 or self.end_byte != len(payload):
            raise ContractValidationError("excerpt offsets do not match UTF-8 bytes")
        if self.utf8_byte_length != len(payload):
            raise ContractValidationError("excerpt byte length is inconsistent")
        require_sha256_hex(self.excerpt_sha256, "excerpt_sha256")
        if hashlib.sha256(payload).hexdigest() != self.excerpt_sha256:
            raise ContractValidationError("excerpt_sha256 does not match excerpt")
        if not isinstance(self.truncated, bool):
            raise ContractValidationError("excerpt truncated must be boolean")
        if not self.truncated and self.excerpt_sha256 != self.full_content_sha256:
            raise ContractValidationError("complete excerpt must match full content hash")
        object.__setattr__(self, "text", text)


@dataclass(frozen=True, slots=True)
class EvidenceBundleItem:
    item_ordinal: int
    evidence_id: str
    identity: CandidateIdentity
    citation_reference: str
    excerpt: EvidenceExcerpt
    authority_level: SourceAuthorityLevel
    authority_basis: Mapping[str, Any]
    source_kind: str
    source_reference: str
    publication_state: SourcePublicationState
    access_class: SourceAccessClass
    target_scope: MemoryTargetScope
    owner_user_id: str | None
    personal_memory_space_id: str | None
    scope_digest: str
    registry_digest: str
    artifact_digest: str
    snapshot_id: str
    structured_metadata: Mapping[str, Any]
    effective_scope: tuple[ScopeDimension, ...]
    contributions: tuple[ModalityContribution, ...]
    match_class: int
    fused_score: int
    ranking_policy_id: str
    ranking_policy_version: str
    ranking_policy_digest: str
    item_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if (
            isinstance(self.item_ordinal, bool)
            or not isinstance(self.item_ordinal, int)
            or self.item_ordinal < 1
        ):
            raise ContractValidationError("item_ordinal must be one-based")
        if not isinstance(self.identity, CandidateIdentity):
            raise ContractValidationError("identity must be CandidateIdentity")
        verify_candidate_identity_hash(self.identity)
        expected_evidence_id = evidence_id_for(self.identity)
        if self.evidence_id != expected_evidence_id:
            raise ContractValidationError("evidence_id differs from candidate identity")
        _text(self.citation_reference, "citation_reference", 4096)
        if not isinstance(self.excerpt, EvidenceExcerpt):
            raise ContractValidationError("excerpt must be EvidenceExcerpt")
        if self.excerpt.full_content_sha256 != self.identity.content_sha256:
            raise ContractValidationError("excerpt and evidence identity differ")
        require_enum_member(self.authority_level, SourceAuthorityLevel, "authority_level")
        if self.authority_level not in _SUPPORTED_AUTHORITY:
            raise Step20BoundaryError(Step20ReasonCode.EVIDENCE_BUNDLE_INVALID)
        object.__setattr__(
            self,
            "authority_basis",
            _mapping(self.authority_basis, "authority_basis", 16 * 1024),
        )
        _text(self.source_kind, "source_kind", 1024)
        _text(self.source_reference, "source_reference", 2048)
        if self.citation_reference != citation_reference_for(
            self.identity,
            self.source_reference,
        ):
            raise ContractValidationError("citation_reference differs from lineage")
        require_enum_member(self.publication_state, SourcePublicationState, "publication_state")
        if self.publication_state is not SourcePublicationState.PUBLISHED:
            raise Step20BoundaryError(Step20ReasonCode.EVIDENCE_BUNDLE_INVALID)
        require_enum_member(self.access_class, SourceAccessClass, "access_class")
        require_enum_member(self.target_scope, MemoryTargetScope, "target_scope")
        owner = _optional_text(self.owner_user_id, "owner_user_id", 255)
        personal = _optional_text(
            self.personal_memory_space_id,
            "personal_memory_space_id",
            255,
        )
        if self.access_class is SourceAccessClass.USER_PRIVATE:
            if owner is None or personal is None or self.target_scope is not MemoryTargetScope.USER_PERSONAL_HAT:
                raise Step20BoundaryError(Step20ReasonCode.EVIDENCE_BUNDLE_INVALID)
        elif owner is not None or personal is not None or self.target_scope is not MemoryTargetScope.SHARED_KNOWLEDGE_HAT:
            raise Step20BoundaryError(Step20ReasonCode.EVIDENCE_BUNDLE_INVALID)
        object.__setattr__(self, "owner_user_id", owner)
        object.__setattr__(self, "personal_memory_space_id", personal)
        for value, name in (
            (self.scope_digest, "scope_digest"),
            (self.registry_digest, "registry_digest"),
            (self.artifact_digest, "artifact_digest"),
        ):
            require_sha256_hex(value, name)
        _text(self.snapshot_id, "snapshot_id", 255)
        metadata = _mapping(
            self.structured_metadata,
            "structured_metadata",
            32 * 1024,
        )
        if metadata.get("redaction_state") not in {None, "NOT_REQUIRED", "VERIFIED"}:
            raise Step20BoundaryError(Step20ReasonCode.EVIDENCE_BUNDLE_INVALID)
        if metadata.get("model_generated") is True:
            raise Step20BoundaryError(Step20ReasonCode.EVIDENCE_BUNDLE_INVALID)
        object.__setattr__(self, "structured_metadata", metadata)
        object.__setattr__(
            self,
            "effective_scope",
            _scope_tuple(self.effective_scope, "effective_scope"),
        )
        if not isinstance(self.contributions, (tuple, list)):
            raise ContractValidationError("contributions must be ordered")
        contributions = tuple(
            sorted(self.contributions, key=lambda item: modality_order(item.modality))
        )
        if not contributions or any(
            not isinstance(item, ModalityContribution) for item in contributions
        ):
            raise ContractValidationError("contributions must be typed and non-empty")
        if len({item.modality for item in contributions}) != len(contributions):
            raise ContractValidationError("item modalities must be unique")
        for contribution in contributions:
            verify_contribution_hash(contribution)
        object.__setattr__(self, "contributions", contributions)
        if (
            isinstance(self.match_class, bool)
            or not isinstance(self.match_class, int)
            or self.match_class != _expected_match_class(contributions)
        ):
            raise ContractValidationError("match_class is invalid")
        if self.fused_score != sum(
            item.fixed_point_contribution for item in contributions
        ):
            raise ContractValidationError("fused score is inconsistent")
        policy = load_ranking_policy()
        if (
            self.ranking_policy_id != policy.policy_id
            or self.ranking_policy_version != policy.policy_version
            or self.ranking_policy_digest != policy.policy_digest
        ):
            raise ContractValidationError("ranking policy identity is invalid")
        object.__setattr__(
            self,
            "item_hash",
            canonical_sha256(self, exclude_fields=("item_hash",)),
        )


@dataclass(frozen=True, slots=True)
class HybridRetrievalRequest:
    route: KnowledgeRouteResult
    policy_result: PolicyGateResult
    tenant_id: str
    user_id: str
    request_id: str
    route_hash: str
    policy_result_hash: str
    selected_hat_id: str | None
    selected_hat_version: str | None
    selected_manifest_digest: str | None
    hat_scope_id: str | None
    effective_scope: tuple[ScopeDimension, ...]
    personal_memory_space_id: str | None
    requested_modalities: tuple[HybridModality, ...]
    lexical_request_hashes: tuple[str, ...]
    lexical_result_hashes: tuple[str, ...]
    vector_request_hash: str | None
    vector_result_hash: str | None
    embedding_model_digest: str
    step18_retrieval_policy_version: str
    ranking_policy_id: str
    ranking_policy_version: str
    ranking_policy_digest: str
    diversity_policy_digest: str
    context_budget_bytes: int = DEFAULT_CONTEXT_BUDGET_BYTES
    maximum_bundle_items: int = MAX_BUNDLE_ITEMS
    request_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.route, KnowledgeRouteResult):
            raise Step20BoundaryError(Step20ReasonCode.HYBRID_INPUT_HASH_INVALID)
        if not isinstance(self.policy_result, PolicyGateResult):
            raise Step20BoundaryError(Step20ReasonCode.HYBRID_INPUT_HASH_INVALID)
        try:
            verify_route_hash(self.route)
            verify_policy_result_hash(self.policy_result)
        except (ContractValidationError, IntegrityError) as exc:
            raise Step20BoundaryError(
                Step20ReasonCode.HYBRID_INPUT_HASH_INVALID
            ) from exc
        for value, name in (
            (self.tenant_id, "tenant_id"),
            (self.user_id, "user_id"),
            (self.request_id, "request_id"),
        ):
            _text(value, name, 255)
        require_sha256_hex(self.route_hash, "route_hash")
        require_sha256_hex(self.policy_result_hash, "policy_result_hash")
        if self.route_hash != self.route.route_hash:
            raise Step20BoundaryError(Step20ReasonCode.HYBRID_INPUT_HASH_INVALID)
        if self.policy_result_hash != self.policy_result.policy_result_hash:
            raise Step20BoundaryError(Step20ReasonCode.HYBRID_INPUT_HASH_INVALID)
        expected_identity = (
            self.route.tenant_id,
            self.route.user_id,
            self.route.request_id,
            self.route.route_hash,
        )
        actual_identity = (
            self.tenant_id,
            self.user_id,
            self.request_id,
            self.policy_result.route_hash,
        )
        policy_identity = (
            self.policy_result.tenant_id,
            self.policy_result.user_id,
            self.policy_result.request_id,
            self.policy_result.route_hash,
        )
        if actual_identity != expected_identity or policy_identity != expected_identity:
            raise Step20BoundaryError(
                Step20ReasonCode.HYBRID_INPUT_BINDING_MISMATCH
            )
        selected = (
            self.selected_hat_id,
            self.selected_hat_version,
            self.selected_manifest_digest,
        )
        expected_selected = (
            self.route.selected_hat_id,
            self.route.selected_hat_version,
            self.route.selected_manifest_digest,
        )
        if selected != expected_selected:
            raise Step20BoundaryError(
                Step20ReasonCode.HYBRID_INPUT_BINDING_MISMATCH
            )
        if self.selected_hat_id is not None:
            _domain_id(self.selected_hat_id, "selected_hat_id")
            _text(self.selected_hat_version, "selected_hat_version", 128)
            require_sha256_hex(
                self.selected_manifest_digest,
                "selected_manifest_digest",
            )
        scope = _scope_tuple(self.effective_scope, "effective_scope")
        if scope != self.route.effective_scope:
            raise Step20BoundaryError(Step20ReasonCode.HYBRID_SCOPE_MISMATCH)
        object.__setattr__(self, "effective_scope", scope)
        personal = _optional_text(
            self.personal_memory_space_id,
            "personal_memory_space_id",
            255,
        )
        scope_values = {item.name: item.value for item in scope}
        target = scope_values.get("target_scope")
        declared_personal = scope_values.get("personal_memory_space_id")
        if personal is None:
            if declared_personal is not None or target not in {
                None,
                MemoryTargetScope.SHARED_KNOWLEDGE_HAT.value,
            }:
                raise Step20BoundaryError(Step20ReasonCode.HYBRID_SCOPE_MISMATCH)
        elif (
            target != MemoryTargetScope.USER_PERSONAL_HAT.value
            or declared_personal != personal
        ):
            raise Step20BoundaryError(Step20ReasonCode.HYBRID_SCOPE_MISMATCH)
        object.__setattr__(self, "personal_memory_space_id", personal)
        hat_route = self.route.knowledge_route in {
            KnowledgeRoute.HAT_ASSIST,
            KnowledgeRoute.HAT_ENFORCE,
        }
        if hat_route:
            object.__setattr__(
                self,
                "hat_scope_id",
                _domain_id(self.hat_scope_id, "hat_scope_id"),
            )
        elif self.hat_scope_id is not None:
            raise Step20BoundaryError(Step20ReasonCode.HYBRID_SCOPE_MISMATCH)
        modalities = _modality_tuple(
            self.requested_modalities,
            "requested_modalities",
            allow_empty=not hat_route,
        )
        object.__setattr__(self, "requested_modalities", modalities)
        lexical_requests = _hash_tuple(
            self.lexical_request_hashes,
            "lexical_request_hashes",
            4,
        )
        lexical_results = _hash_tuple(
            self.lexical_result_hashes,
            "lexical_result_hashes",
            4,
        )
        if len(lexical_requests) != len(lexical_results):
            raise Step20BoundaryError(
                Step20ReasonCode.HYBRID_INPUT_BINDING_MISMATCH
            )
        object.__setattr__(self, "lexical_request_hashes", lexical_requests)
        object.__setattr__(self, "lexical_result_hashes", lexical_results)
        vector_values = (self.vector_request_hash, self.vector_result_hash)
        if any(value is not None for value in vector_values):
            if any(value is None for value in vector_values):
                raise Step20BoundaryError(
                    Step20ReasonCode.HYBRID_INPUT_BINDING_MISMATCH
                )
            require_sha256_hex(self.vector_request_hash, "vector_request_hash")
            require_sha256_hex(self.vector_result_hash, "vector_result_hash")
            if HybridModality.VECTOR not in modalities:
                raise Step20BoundaryError(
                    Step20ReasonCode.HYBRID_INPUT_BINDING_MISMATCH
                )
        object.__setattr__(self, "vector_request_hash", self.vector_request_hash)
        object.__setattr__(self, "vector_result_hash", self.vector_result_hash)
        require_sha256_hex(self.embedding_model_digest, "embedding_model_digest")
        if self.embedding_model_digest != load_approved_model_spec().model_digest:
            raise Step20BoundaryError(Step20ReasonCode.HYBRID_MODEL_MISMATCH)
        if self.step18_retrieval_policy_version != STEP18_RETRIEVAL_POLICY_VERSION:
            raise Step20BoundaryError(
                Step20ReasonCode.HYBRID_INPUT_BINDING_MISMATCH
            )
        ranking = load_ranking_policy()
        diversity = load_diversity_policy()
        if (
            self.ranking_policy_id != ranking.policy_id
            or self.ranking_policy_version != ranking.policy_version
            or self.ranking_policy_digest != ranking.policy_digest
            or self.diversity_policy_digest != diversity.policy_digest
        ):
            raise Step20BoundaryError(
                Step20ReasonCode.HYBRID_INPUT_BINDING_MISMATCH
            )
        if (
            isinstance(self.context_budget_bytes, bool)
            or not isinstance(self.context_budget_bytes, int)
            or not 1 <= self.context_budget_bytes <= MAX_CONTEXT_BUDGET_BYTES
        ):
            raise ContractValidationError("context_budget_bytes exceeds policy")
        if (
            isinstance(self.maximum_bundle_items, bool)
            or not isinstance(self.maximum_bundle_items, int)
            or not 1 <= self.maximum_bundle_items <= MAX_BUNDLE_ITEMS
        ):
            raise ContractValidationError("maximum_bundle_items exceeds policy")
        object.__setattr__(
            self,
            "request_hash",
            canonical_sha256(self, exclude_fields=("request_hash",)),
        )


@dataclass(frozen=True, slots=True)
class FrozenEvidenceBundle:
    schema_version: str
    hybrid_request_hash: str
    request_id: str
    tenant_id: str
    user_id: str
    route_hash: str
    policy_result_hash: str
    selected_hat_id: str
    selected_hat_version: str
    selected_manifest_digest: str
    hat_scope_id: str
    effective_scope: tuple[ScopeDimension, ...]
    knowledge_policy_decision: KnowledgePolicyDecision
    execution_authorization_decision: ExecutionAuthorizationDecision
    answer_status: AnswerStatus
    step18_result_hashes: tuple[str, ...]
    step19_vector_result_hash: str | None
    embedding_model_id: str
    embedding_model_revision: str
    embedding_model_digest: str
    embedding_dimension: int
    vector_metric_policy: str
    ranking_policy_id: str
    ranking_policy_version: str
    ranking_policy_digest: str
    diversity_policy_digest: str
    context_budget_bytes: int
    maximum_bundle_items: int
    requested_modalities: tuple[HybridModality, ...]
    context_bytes_used: int
    ordered_items: tuple[EvidenceBundleItem, ...]
    candidates_before_dedup: int
    candidates_after_dedup: int
    candidates_after_diversity: int
    excluded_counts: Mapping[str, int]
    available_modalities: tuple[HybridModality, ...]
    missing_modalities: tuple[HybridModality, ...]
    truncated: bool
    retrieval_coverage: RetrievalCoverageStatus
    evidence_status: EvidenceStatus
    reason_codes: tuple[Step20ReasonCode, ...]
    input_result_hash_count: int = field(init=False)
    evidence_bundle_id: str = field(init=False)
    bundle_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != STEP20_SCHEMA_VERSION:
            raise ContractValidationError("unsupported Step 20 bundle schema")
        for value, name in (
            (self.request_id, "request_id"),
            (self.tenant_id, "tenant_id"),
            (self.user_id, "user_id"),
            (self.selected_hat_id, "selected_hat_id"),
            (self.selected_hat_version, "selected_hat_version"),
            (self.hat_scope_id, "hat_scope_id"),
        ):
            _text(value, name, 255)
        for value, name in (
            (self.hybrid_request_hash, "hybrid_request_hash"),
            (self.route_hash, "route_hash"),
            (self.policy_result_hash, "policy_result_hash"),
            (self.selected_manifest_digest, "selected_manifest_digest"),
            (self.embedding_model_digest, "embedding_model_digest"),
            (self.ranking_policy_digest, "ranking_policy_digest"),
            (self.diversity_policy_digest, "diversity_policy_digest"),
        ):
            require_sha256_hex(value, name)
        model_spec = load_approved_model_spec()
        if (
            self.embedding_model_id != model_spec.model_id
            or self.embedding_model_revision != model_spec.model_revision
            or self.embedding_model_digest != model_spec.model_digest
            or self.embedding_dimension != model_spec.embedding_dimension
            or self.vector_metric_policy != VECTOR_METRIC_POLICY
        ):
            raise ContractValidationError("bundle embedding model identity is invalid")
        object.__setattr__(
            self,
            "effective_scope",
            _scope_tuple(self.effective_scope, "effective_scope"),
        )
        require_enum_member(
            self.knowledge_policy_decision,
            KnowledgePolicyDecision,
            "knowledge_policy_decision",
        )
        require_enum_member(
            self.execution_authorization_decision,
            ExecutionAuthorizationDecision,
            "execution_authorization_decision",
        )
        require_enum_member(self.answer_status, AnswerStatus, "answer_status")
        step18_hashes = _hash_tuple(
            self.step18_result_hashes,
            "step18_result_hashes",
            4,
        )
        object.__setattr__(self, "step18_result_hashes", step18_hashes)
        if self.step19_vector_result_hash is not None:
            require_sha256_hex(
                self.step19_vector_result_hash,
                "step19_vector_result_hash",
            )
        ranking = load_ranking_policy()
        diversity = load_diversity_policy()
        if (
            self.ranking_policy_id != ranking.policy_id
            or self.ranking_policy_version != ranking.policy_version
            or self.ranking_policy_digest != ranking.policy_digest
            or self.diversity_policy_digest != diversity.policy_digest
        ):
            raise ContractValidationError("bundle policy identity is invalid")
        for value, name, maximum in (
            (self.context_budget_bytes, "context_budget_bytes", MAX_CONTEXT_BUDGET_BYTES),
            (self.maximum_bundle_items, "maximum_bundle_items", MAX_BUNDLE_ITEMS),
            (self.context_bytes_used, "context_bytes_used", MAX_CONTEXT_BUDGET_BYTES),
            (self.candidates_before_dedup, "candidates_before_dedup", MAX_MERGED_CANDIDATES),
            (self.candidates_after_dedup, "candidates_after_dedup", MAX_MERGED_CANDIDATES),
            (self.candidates_after_diversity, "candidates_after_diversity", MAX_BUNDLE_ITEMS),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum:
                raise ContractValidationError(f"{name} is outside Step 20 bounds")
        if (
            self.context_budget_bytes < 1
            or self.maximum_bundle_items < 1
            or self.context_bytes_used > self.context_budget_bytes
        ):
            raise ContractValidationError("context byte accounting is invalid")
        if not isinstance(self.ordered_items, (tuple, list)):
            raise ContractValidationError("ordered_items must be ordered")
        items = tuple(self.ordered_items)
        if len(items) > self.maximum_bundle_items or any(
            not isinstance(item, EvidenceBundleItem) for item in items
        ):
            raise ContractValidationError("ordered_items are invalid")
        if tuple(item.item_ordinal for item in items) != tuple(range(1, len(items) + 1)):
            raise ContractValidationError("item ordinals must be contiguous")
        if len({item.evidence_id for item in items}) != len(items):
            raise ContractValidationError("evidence IDs must be unique")
        if len({item.item_hash for item in items}) != len(items):
            raise ContractValidationError("item hashes must be unique")
        if sum(item.excerpt.utf8_byte_length for item in items) != self.context_bytes_used:
            raise ContractValidationError("context_bytes_used differs from excerpts")
        for item in items:
            verify_bundle_item_hash(item)
            if item.identity.tenant_id != self.tenant_id or item.identity.hat_scope_id != self.hat_scope_id:
                raise Step20BoundaryError(Step20ReasonCode.EVIDENCE_BUNDLE_INVALID)
            if item.effective_scope != self.effective_scope:
                raise Step20BoundaryError(Step20ReasonCode.EVIDENCE_BUNDLE_INVALID)
        object.__setattr__(self, "ordered_items", items)
        if not (
            self.candidates_before_dedup >= self.candidates_after_dedup
            >= self.candidates_after_diversity >= len(items)
        ):
            raise ContractValidationError("candidate counts are not monotonic")
        excluded = _mapping(self.excluded_counts, "excluded_counts", 16 * 1024)
        if any(
            not isinstance(value, int) or isinstance(value, bool) or value <= 0
            for value in excluded.values()
        ):
            raise ContractValidationError("excluded_counts must be positive integers")
        object.__setattr__(self, "excluded_counts", excluded)
        available = _modality_tuple(
            self.available_modalities,
            "available_modalities",
            allow_empty=True,
        )
        missing = _modality_tuple(
            self.missing_modalities,
            "missing_modalities",
            allow_empty=True,
        )
        if set(available) & set(missing):
            raise ContractValidationError("available and missing modalities overlap")
        requested = _modality_tuple(
            self.requested_modalities,
            "requested_modalities",
            allow_empty=False,
        )
        if set(requested) != set(available) | set(missing):
            raise ContractValidationError("requested modality accounting is incomplete")
        if (HybridModality.VECTOR in available) != (
            self.step19_vector_result_hash is not None
        ):
            raise ContractValidationError("vector result availability is inconsistent")
        if len(step18_hashes) != len(
            set(available) - {HybridModality.VECTOR}
        ):
            raise ContractValidationError("lexical result availability is inconsistent")
        object.__setattr__(self, "available_modalities", available)
        object.__setattr__(self, "missing_modalities", missing)
        object.__setattr__(self, "requested_modalities", requested)
        if not isinstance(self.truncated, bool):
            raise ContractValidationError("truncated must be boolean")
        if self.truncated != bool(missing or excluded):
            raise ContractValidationError("truncation metadata is inconsistent")
        require_enum_member(
            self.retrieval_coverage,
            RetrievalCoverageStatus,
            "retrieval_coverage",
        )
        require_enum_member(self.evidence_status, EvidenceStatus, "evidence_status")
        allowed_states = {
            RetrievalCoverageStatus.EMPTY: EvidenceStatus.INSUFFICIENT,
            RetrievalCoverageStatus.PARTIAL: EvidenceStatus.INSUFFICIENT,
            RetrievalCoverageStatus.COMPLETE: EvidenceStatus.SUFFICIENT,
            RetrievalCoverageStatus.INVALID: EvidenceStatus.INVALID,
        }
        if self.evidence_status is not allowed_states[self.retrieval_coverage]:
            raise ContractValidationError("coverage and evidence status differ")
        if self.retrieval_coverage is RetrievalCoverageStatus.COMPLETE and not items:
            raise ContractValidationError("complete retrieval requires evidence")
        if self.retrieval_coverage is RetrievalCoverageStatus.COMPLETE and self.truncated:
            raise ContractValidationError("complete retrieval cannot be truncated")
        if self.retrieval_coverage is RetrievalCoverageStatus.EMPTY and items:
            raise ContractValidationError("empty retrieval cannot carry items")
        if self.retrieval_coverage is RetrievalCoverageStatus.EMPTY and self.truncated:
            raise ContractValidationError("empty retrieval cannot be truncated")
        if self.retrieval_coverage is RetrievalCoverageStatus.PARTIAL and not self.truncated:
            raise ContractValidationError("partial retrieval must record truncation")
        object.__setattr__(self, "reason_codes", _reason_tuple(self.reason_codes))
        object.__setattr__(
            self,
            "input_result_hash_count",
            len(step18_hashes) + (1 if self.step19_vector_result_hash else 0),
        )
        digest = canonical_sha256(
            self,
            exclude_fields=("evidence_bundle_id", "bundle_hash"),
        )
        object.__setattr__(self, "bundle_hash", digest)
        object.__setattr__(self, "evidence_bundle_id", f"evidence-bundle:{digest}")
        if len(canonical_json_bytes(self)) > MAX_CANONICAL_BUNDLE_BYTES:
            raise ContractValidationError("canonical Evidence Bundle exceeds its byte limit")


@dataclass(frozen=True, slots=True)
class HybridEvidenceOutcome:
    hybrid_request_hash: str
    request_id: str
    tenant_id: str
    user_id: str
    route_hash: str
    retrieval_coverage: RetrievalCoverageStatus
    evidence_status: EvidenceStatus
    bundle: FrozenEvidenceBundle | None
    reason_codes: tuple[Step20ReasonCode, ...]
    outcome_hash: str = field(init=False)

    def __post_init__(self) -> None:
        require_sha256_hex(self.hybrid_request_hash, "hybrid_request_hash")
        for value, name in (
            (self.request_id, "request_id"),
            (self.tenant_id, "tenant_id"),
            (self.user_id, "user_id"),
        ):
            _text(value, name, 255)
        require_sha256_hex(self.route_hash, "route_hash")
        require_enum_member(
            self.retrieval_coverage,
            RetrievalCoverageStatus,
            "retrieval_coverage",
        )
        require_enum_member(self.evidence_status, EvidenceStatus, "evidence_status")
        if self.bundle is not None:
            if not isinstance(self.bundle, FrozenEvidenceBundle):
                raise ContractValidationError("bundle must be a FrozenEvidenceBundle")
            verify_evidence_bundle_hash(self.bundle)
            if (
                self.bundle.request_id != self.request_id
                or self.bundle.hybrid_request_hash != self.hybrid_request_hash
                or self.bundle.tenant_id != self.tenant_id
                or self.bundle.user_id != self.user_id
                or self.bundle.route_hash != self.route_hash
                or self.bundle.retrieval_coverage is not self.retrieval_coverage
                or self.bundle.evidence_status is not self.evidence_status
            ):
                raise Step20BoundaryError(Step20ReasonCode.EVIDENCE_BUNDLE_INVALID)
        elif not (
            self.retrieval_coverage is RetrievalCoverageStatus.EMPTY
            and self.evidence_status is EvidenceStatus.NOT_REQUIRED
        ):
            raise ContractValidationError("only a no-HAT outcome may omit a bundle")
        object.__setattr__(self, "reason_codes", _reason_tuple(self.reason_codes))
        object.__setattr__(
            self,
            "outcome_hash",
            canonical_sha256(self, exclude_fields=("outcome_hash",)),
        )


def evidence_id_for(identity: CandidateIdentity) -> str:
    if not isinstance(identity, CandidateIdentity):
        raise ContractValidationError("identity must be CandidateIdentity")
    return f"evidence:{identity.identity_hash}"


def citation_reference_for(identity: CandidateIdentity, source_reference: str) -> str:
    _text(source_reference, "source_reference", 2048)
    return (
        f"{source_reference}#source={identity.source_id};"
        f"version={identity.knowledge_version_id};chunk={identity.chunk_id}"
    )


def verify_hybrid_request_hash(value: HybridRetrievalRequest) -> None:
    verify_canonical_hash(value, value.request_hash, exclude_fields=("request_hash",))


def verify_candidate_identity_hash(value: CandidateIdentity) -> None:
    verify_canonical_hash(value, value.identity_hash, exclude_fields=("identity_hash",))


def verify_contribution_hash(value: ModalityContribution) -> None:
    verify_canonical_hash(
        value,
        value.contribution_hash,
        exclude_fields=("contribution_hash",),
    )


def verify_hybrid_candidate_hash(value: HybridCandidate) -> None:
    verify_canonical_hash(value, value.candidate_hash, exclude_fields=("candidate_hash",))


def verify_bundle_item_hash(value: EvidenceBundleItem) -> None:
    verify_canonical_hash(value, value.item_hash, exclude_fields=("item_hash",))


def verify_evidence_bundle_hash(value: FrozenEvidenceBundle) -> None:
    expected = canonical_sha256(
        value,
        exclude_fields=("evidence_bundle_id", "bundle_hash"),
    )
    require_sha256_hex(value.bundle_hash, "bundle_hash")
    if expected != value.bundle_hash:
        raise IntegrityError(
            f"canonical hash mismatch: expected {value.bundle_hash}, calculated {expected}"
        )
    if value.evidence_bundle_id != f"evidence-bundle:{value.bundle_hash}":
        raise IntegrityError("Evidence Bundle ID differs from its canonical hash")


def verify_outcome_hash(value: HybridEvidenceOutcome) -> None:
    verify_canonical_hash(value, value.outcome_hash, exclude_fields=("outcome_hash",))


__all__ = [
    "DEFAULT_CONTEXT_BUDGET_BYTES",
    "DiversityPolicy",
    "EvidenceBundleItem",
    "EvidenceExcerpt",
    "FrozenEvidenceBundle",
    "HybridCandidate",
    "HybridEvidenceOutcome",
    "HybridModality",
    "HybridRetrievalRequest",
    "MAX_BUNDLE_ITEMS",
    "MAX_CANONICAL_BUNDLE_BYTES",
    "MAX_CONTEXT_BUDGET_BYTES",
    "MAX_EXACT_PRIORITY_ITEMS",
    "MAX_EXCERPT_BYTES_PER_ITEM",
    "MAX_ITEMS_PER_KNOWLEDGE_VERSION",
    "MAX_ITEMS_PER_SOURCE",
    "MAX_MERGED_CANDIDATES",
    "MAX_UPSTREAM_RESULTS_PER_MODALITY",
    "MIN_PARTIAL_EXCERPT_BYTES",
    "ModalityContribution",
    "RANKING_POLICY_ID",
    "RANKING_POLICY_VERSION",
    "RRF_K",
    "RRF_SCALE",
    "RankingPolicy",
    "RetrievalCoverageStatus",
    "STEP18_RETRIEVAL_POLICY_VERSION",
    "STEP20_SCHEMA_VERSION",
    "Step20BoundaryError",
    "Step20ReasonCode",
    "VECTOR_METRIC_POLICY",
    "CandidateIdentity",
    "citation_reference_for",
    "evidence_id_for",
    "load_diversity_policy",
    "load_ranking_policy",
    "modality_order",
    "modality_weight",
    "verify_bundle_item_hash",
    "verify_candidate_identity_hash",
    "verify_contribution_hash",
    "verify_evidence_bundle_hash",
    "verify_hybrid_candidate_hash",
    "verify_hybrid_request_hash",
    "verify_outcome_hash",
]
