"""Immutable Step 24 correction-packet and integrity-receipt contracts.

The packet is derived only from a verified Step 23 ``PacketInputSnapshot``.
It is correction data for a later drafting stage, never approval, execution,
publication, source-authority, or memory-write authority.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from aioa_memory_kernel.claims import (
    MAX_CLAIMS,
    ClaimEvidenceAssessment,
    ClaimEvidenceRelation,
    ClaimRecord,
    PacketInputSnapshot,
    verify_claim_assessment_hash,
    verify_claim_record_hash,
)
from aioa_memory_kernel.contracts.enums import EvidenceStatus, StableStringEnum
from aioa_memory_kernel.contracts.exceptions import ContractValidationError, IntegrityError
from aioa_memory_kernel.contracts.scope import ScopeDimension
from aioa_memory_kernel.contracts.serialization import (
    canonical_json,
    canonical_json_bytes,
    canonical_sha256,
    require_enum_member,
    require_sha256_hex,
    verify_canonical_hash,
)
from aioa_memory_kernel.sources import SourceAuthorityLevel, SourcePublicationState


STEP24_SCHEMA_VERSION = "1.0.0"
PACKET_POLICY_ID = "correction-packet-construction-integrity-1a"
PACKET_POLICY_VERSION = "1"
PACKET_HASH_ALGORITHM = "SHA-256"
PACKET_AUTHENTICITY_ALGORITHM = "HMAC-SHA-256"
PACKET_HMAC_DOMAIN_ID = "MEMORY_PATCH_CORRECTION_PACKET_V1"
MAX_REQUIRED_CORRECTIONS = MAX_CLAIMS
MAX_PROHIBITED_CLAIMS = MAX_CLAIMS * 4
MAX_CITATIONS = 4096
MAX_CONFLICTS = MAX_CLAIMS
MAX_LIMITATIONS = 256
MAX_TEXT_UTF8_BYTES = 16 * 1024
MAX_CANONICAL_PACKET_BYTES = 4 * 1024 * 1024
PERSISTENCE_DECISION = (
    "DEFERRED_EXISTING_STEP4_SCHEMA_REQUIRES_DURABLE_UPSTREAM_LINEAGE"
)


class CorrectionActionType(StableStringEnum):
    REPLACE_CLAIM = "REPLACE_CLAIM"
    REMOVE_CLAIM = "REMOVE_CLAIM"
    QUALIFY_CLAIM = "QUALIFY_CLAIM"
    ADD_MISSING_CONTEXT = "ADD_MISSING_CONTEXT"
    TEMPORAL_CORRECTION = "TEMPORAL_CORRECTION"
    SOURCE_AUTHORITY_CORRECTION = "SOURCE_AUTHORITY_CORRECTION"


class ProhibitionType(StableStringEnum):
    DO_NOT_REPEAT_EXACT = "DO_NOT_REPEAT_EXACT"
    DO_NOT_STATE_AS_FACT = "DO_NOT_STATE_AS_FACT"
    DO_NOT_USE_OUTSIDE_TEMPORAL_SCOPE = "DO_NOT_USE_OUTSIDE_TEMPORAL_SCOPE"
    DO_NOT_UPGRADE_SOURCE_AUTHORITY = "DO_NOT_UPGRADE_SOURCE_AUTHORITY"
    DO_NOT_RESOLVE_CONFLICT_AS_CERTAIN = "DO_NOT_RESOLVE_CONFLICT_AS_CERTAIN"


class ConflictHandling(StableStringEnum):
    PRESERVE_AND_QUALIFY = "PRESERVE_AND_QUALIFY"
    REQUIRE_HUMAN_REVIEW = "REQUIRE_HUMAN_REVIEW"


class Step24ReasonCode(StableStringEnum):
    PACKET_BUILT = "PACKET_BUILT"
    PACKET_INPUT_HASH_INVALID = "PACKET_INPUT_HASH_INVALID"
    CLAIM_INPUT_INVALID = "CLAIM_INPUT_INVALID"
    EVIDENCE_LINK_INVALID = "EVIDENCE_LINK_INVALID"
    CORRECTION_REQUIRED_REFUTED = "CORRECTION_REQUIRED_REFUTED"
    CORRECTION_REQUIRED_UNVERIFIED = "CORRECTION_REQUIRED_UNVERIFIED"
    CORRECTION_REQUIRED_TEMPORAL = "CORRECTION_REQUIRED_TEMPORAL"
    CORRECTION_REQUIRED_AUTHORITY = "CORRECTION_REQUIRED_AUTHORITY"
    CORRECTION_REQUIRED_CONFLICT_QUALIFICATION = (
        "CORRECTION_REQUIRED_CONFLICT_QUALIFICATION"
    )
    PROHIBITED_REFUTED_CLAIM = "PROHIBITED_REFUTED_CLAIM"
    PROHIBITED_UNSUPPORTED_ASSERTION = "PROHIBITED_UNSUPPORTED_ASSERTION"
    PROHIBITED_AUTHORITY_UPGRADE = "PROHIBITED_AUTHORITY_UPGRADE"
    PROHIBITED_CONFLICT_CERTAINTY = "PROHIBITED_CONFLICT_CERTAINTY"
    CITATION_REQUIRED = "CITATION_REQUIRED"
    CITATION_INVALID = "CITATION_INVALID"
    EVIDENCE_INSUFFICIENT = "EVIDENCE_INSUFFICIENT"
    EVIDENCE_CONFLICTING = "EVIDENCE_CONFLICTING"
    EVIDENCE_STALE = "EVIDENCE_STALE"
    EVIDENCE_UNAVAILABLE = "EVIDENCE_UNAVAILABLE"
    PACKET_HASH_INVALID = "PACKET_HASH_INVALID"
    PACKET_HMAC_INVALID = "PACKET_HMAC_INVALID"
    PACKET_REPLAY_CONFLICT = "PACKET_REPLAY_CONFLICT"


class CorrectionPacketBoundaryError(RuntimeError):
    """Sanitized fail-closed Step 24 error."""

    def __init__(self, reason_code: Step24ReasonCode) -> None:
        if not isinstance(reason_code, Step24ReasonCode):
            raise TypeError("reason_code must be Step24ReasonCode")
        super().__init__(f"Step 24 correction packet denied: {reason_code.value}")
        self.reason_code = reason_code


_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_CLAIM_ID = re.compile(r"^claim-[0-9a-f]{64}$")
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_REASON_ORDER = {value: index for index, value in enumerate(Step24ReasonCode)}
_ACTION_ORDER = {value: index for index, value in enumerate(CorrectionActionType)}
_PROHIBITION_ORDER = {value: index for index, value in enumerate(ProhibitionType)}
_RELATION_ORDER = {value: index for index, value in enumerate(ClaimEvidenceRelation)}


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
    sorted_values: bool = True,
) -> tuple[str, ...]:
    if not isinstance(value, (tuple, list)) or len(value) > maximum:
        raise ContractValidationError(f"{field_name} is outside Step 24 bounds")
    result = tuple(value)
    for item in result:
        require_sha256_hex(item, field_name)
    if len(set(result)) != len(result):
        raise ContractValidationError(f"{field_name} must be unique")
    if sorted_values and result != tuple(sorted(result)):
        raise ContractValidationError(f"{field_name} must be canonically sorted")
    return result


def _string_tuple(
    value: object,
    field_name: str,
    maximum: int,
    *,
    item_bytes: int = 1024,
) -> tuple[str, ...]:
    if not isinstance(value, (tuple, list)) or len(value) > maximum:
        raise ContractValidationError(f"{field_name} is outside Step 24 bounds")
    result = tuple(value)
    for item in result:
        _text(item, f"{field_name} item", item_bytes)
    if result != tuple(sorted(set(result))):
        raise ContractValidationError(f"{field_name} must be unique and canonical")
    return result


def _reason_tuple(value: object) -> tuple[Step24ReasonCode, ...]:
    if not isinstance(value, (tuple, list)) or any(
        not isinstance(item, Step24ReasonCode) for item in value
    ):
        raise ContractValidationError("reason_codes must contain Step24ReasonCode values")
    result = tuple(sorted(set(value), key=_REASON_ORDER.__getitem__))
    if tuple(value) != result:
        raise ContractValidationError("reason_codes must be unique and canonical")
    return result


def reason_codes(*values: Step24ReasonCode) -> tuple[Step24ReasonCode, ...]:
    return tuple(sorted(set(values), key=_REASON_ORDER.__getitem__))


def derive_packet_id(
    request_id: str,
    draft_v1_hash: str,
    step23_input_snapshot_hash: str,
    schema_version: str = STEP24_SCHEMA_VERSION,
) -> str:
    _text(request_id, "request_id", 255)
    require_sha256_hex(draft_v1_hash, "draft_v1_hash")
    require_sha256_hex(step23_input_snapshot_hash, "step23_input_snapshot_hash")
    if schema_version != STEP24_SCHEMA_VERSION:
        raise ContractValidationError("unsupported Step 24 packet schema")
    digest = canonical_sha256(
        {
            "request_id": request_id,
            "draft_v1_hash": draft_v1_hash,
            "step23_input_snapshot_hash": step23_input_snapshot_hash,
            "schema_version": schema_version,
        }
    )
    return f"correction-packet-{digest}"


@dataclass(frozen=True, slots=True)
class CorrectionPacketPolicy:
    policy_id: str = PACKET_POLICY_ID
    policy_version: str = PACKET_POLICY_VERSION
    must_preserve_supported_claims: bool = True
    must_apply_required_corrections: bool = True
    must_avoid_prohibited_claims: bool = True
    must_preserve_conflicts: bool = True
    must_not_upgrade_authority: bool = True
    must_respect_temporal_scope: bool = True
    must_use_only_packet_citations: bool = True
    packet_may_encode_non_sufficient_evidence: bool = True
    packet_grants_execution: bool = False
    policy_digest: str = field(init=False)

    def __post_init__(self) -> None:
        _text(self.policy_id, "policy_id", 255)
        _text(self.policy_version, "policy_version", 64)
        required_true = (
            self.must_preserve_supported_claims,
            self.must_apply_required_corrections,
            self.must_avoid_prohibited_claims,
            self.must_preserve_conflicts,
            self.must_not_upgrade_authority,
            self.must_respect_temporal_scope,
            self.must_use_only_packet_citations,
            self.packet_may_encode_non_sufficient_evidence,
        )
        if any(value is not True for value in required_true):
            raise ContractValidationError("Step 24 packet policy cannot be weakened")
        if self.packet_grants_execution is not False:
            raise ContractValidationError("a Correction Packet cannot grant execution")
        object.__setattr__(
            self,
            "policy_digest",
            canonical_sha256(self, exclude_fields=("policy_digest",)),
        )


def load_packet_policy() -> CorrectionPacketPolicy:
    return CorrectionPacketPolicy()


@dataclass(frozen=True, slots=True)
class KnowledgePolicyBinding:
    binding_scheme: str
    step20_bundle_ids: tuple[str, ...]
    step20_bundle_hashes: tuple[str, ...]
    step21_resolution_hash: str
    evidence_status: EvidenceStatus
    explicit_decision_values_available: bool
    binding_digest: str = field(init=False)

    def __post_init__(self) -> None:
        if self.binding_scheme != "step20-bundle-hash-step21-status-v1":
            raise ContractValidationError("knowledge policy binding scheme is invalid")
        bundle_ids = tuple(self.step20_bundle_ids)
        if len(bundle_ids) > 2:
            raise ContractValidationError("Step 20 bundle identity count is invalid")
        for value in bundle_ids:
            _text(value, "step20_bundle_id", 255)
        if len(set(bundle_ids)) != len(bundle_ids):
            raise ContractValidationError("Step 20 bundle IDs must be unique")
        object.__setattr__(self, "step20_bundle_ids", bundle_ids)
        hashes = _hash_tuple(
            self.step20_bundle_hashes,
            "step20_bundle_hashes",
            2,
            sorted_values=False,
        )
        if len(bundle_ids) != len(hashes):
            raise ContractValidationError("Step 20 policy identity count differs")
        object.__setattr__(self, "step20_bundle_hashes", hashes)
        require_sha256_hex(self.step21_resolution_hash, "step21_resolution_hash")
        require_enum_member(self.evidence_status, EvidenceStatus, "evidence_status")
        if self.explicit_decision_values_available is not False:
            raise ContractValidationError(
                "Step 23 snapshot binds Step 20 decisions by hash, not copied values"
            )
        object.__setattr__(
            self,
            "binding_digest",
            canonical_sha256(self, exclude_fields=("binding_digest",)),
        )


@dataclass(frozen=True, slots=True)
class CorrectionFactReference:
    evidence_link_hash: str
    candidate_hash: str
    content_sha256: str
    temporal_assessment_hash: str
    relation: ClaimEvidenceRelation
    citation_id: str
    fact_reference_hash: str = field(init=False)

    def __post_init__(self) -> None:
        for value, name in (
            (self.evidence_link_hash, "evidence_link_hash"),
            (self.candidate_hash, "candidate_hash"),
            (self.content_sha256, "content_sha256"),
            (self.temporal_assessment_hash, "temporal_assessment_hash"),
        ):
            require_sha256_hex(value, name)
        require_enum_member(self.relation, ClaimEvidenceRelation, "relation")
        _text(self.citation_id, "citation_id", 255)
        object.__setattr__(
            self,
            "fact_reference_hash",
            canonical_sha256(self, exclude_fields=("fact_reference_hash",)),
        )


@dataclass(frozen=True, slots=True)
class RequiredCorrection:
    claim_id: str
    original_claim_text: str
    correction_action: CorrectionActionType
    required_replacement_facts: tuple[CorrectionFactReference, ...]
    supporting_evidence_link_hashes: tuple[str, ...]
    reason_codes: tuple[Step24ReasonCode, ...]
    limitations: tuple[str, ...]
    correction_id: str = field(init=False)
    correction_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if _CLAIM_ID.fullmatch(self.claim_id) is None:
            raise ContractValidationError("claim_id is invalid")
        _text(self.original_claim_text, "original_claim_text", MAX_TEXT_UTF8_BYTES)
        require_enum_member(self.correction_action, CorrectionActionType, "correction_action")
        facts = tuple(self.required_replacement_facts)
        if len(facts) > MAX_CITATIONS or any(
            not isinstance(item, CorrectionFactReference) for item in facts
        ):
            raise ContractValidationError("required_replacement_facts are invalid")
        if facts != tuple(
            sorted(facts, key=lambda item: (item.evidence_link_hash, item.fact_reference_hash))
        ):
            raise ContractValidationError("replacement facts are not canonical")
        if len({item.evidence_link_hash for item in facts}) != len(facts):
            raise ContractValidationError("replacement facts must be unique")
        object.__setattr__(self, "required_replacement_facts", facts)
        evidence_hashes = _hash_tuple(
            self.supporting_evidence_link_hashes,
            "supporting_evidence_link_hashes",
            MAX_CITATIONS,
        )
        if {item.evidence_link_hash for item in facts} - set(evidence_hashes):
            raise ContractValidationError("replacement facts are detached from correction evidence")
        object.__setattr__(self, "supporting_evidence_link_hashes", evidence_hashes)
        object.__setattr__(self, "reason_codes", _reason_tuple(self.reason_codes))
        object.__setattr__(
            self,
            "limitations",
            _string_tuple(self.limitations, "limitations", MAX_LIMITATIONS),
        )
        semantic = canonical_sha256(
            self,
            exclude_fields=("correction_id", "correction_hash"),
        )
        object.__setattr__(self, "correction_id", f"correction-{semantic}")
        object.__setattr__(
            self,
            "correction_hash",
            canonical_sha256(self, exclude_fields=("correction_hash",)),
        )


@dataclass(frozen=True, slots=True)
class ProhibitedClaim:
    source_claim_id: str
    exact_or_normalized_prohibited_content: str
    prohibition_type: ProhibitionType
    reason_codes: tuple[Step24ReasonCode, ...]
    evidence_reference_hashes: tuple[str, ...]
    prohibited_claim_id: str = field(init=False)
    prohibition_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if _CLAIM_ID.fullmatch(self.source_claim_id) is None:
            raise ContractValidationError("source_claim_id is invalid")
        _text(
            self.exact_or_normalized_prohibited_content,
            "exact_or_normalized_prohibited_content",
            MAX_TEXT_UTF8_BYTES,
        )
        require_enum_member(self.prohibition_type, ProhibitionType, "prohibition_type")
        object.__setattr__(self, "reason_codes", _reason_tuple(self.reason_codes))
        object.__setattr__(
            self,
            "evidence_reference_hashes",
            _hash_tuple(
                self.evidence_reference_hashes,
                "evidence_reference_hashes",
                MAX_CITATIONS,
            ),
        )
        semantic = canonical_sha256(
            self,
            exclude_fields=("prohibited_claim_id", "prohibition_hash"),
        )
        object.__setattr__(self, "prohibited_claim_id", f"prohibited-{semantic}")
        object.__setattr__(
            self,
            "prohibition_hash",
            canonical_sha256(self, exclude_fields=("prohibition_hash",)),
        )


@dataclass(frozen=True, slots=True)
class CorrectionCitation:
    claim_id: str
    evidence_link_hash: str
    candidate_hash: str
    source_id: str
    knowledge_version_id: str
    chunk_id: str
    content_sha256: str
    source_reference: str
    citation_reference: str
    authority_level: SourceAuthorityLevel
    publication_state: SourcePublicationState
    temporal_assessment_hash: str
    relation: ClaimEvidenceRelation
    citation_id: str = field(init=False)
    citation_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if _CLAIM_ID.fullmatch(self.claim_id) is None:
            raise ContractValidationError("citation claim_id is invalid")
        for value, name in (
            (self.evidence_link_hash, "evidence_link_hash"),
            (self.candidate_hash, "candidate_hash"),
            (self.content_sha256, "content_sha256"),
            (self.temporal_assessment_hash, "temporal_assessment_hash"),
        ):
            require_sha256_hex(value, name)
        for value, name, maximum in (
            (self.source_id, "source_id", 255),
            (self.knowledge_version_id, "knowledge_version_id", 255),
            (self.chunk_id, "chunk_id", 255),
            (self.source_reference, "source_reference", 2048),
            (self.citation_reference, "citation_reference", 4096),
        ):
            _text(value, name, maximum)
        rendered = f"{self.source_reference}\n{self.citation_reference}".casefold()
        if any(
            marker in rendered
            for marker in (
                "x-amz-signature",
                "x-amz-credential",
                "awsaccesskeyid",
                "authorization=",
            )
        ):
            raise CorrectionPacketBoundaryError(Step24ReasonCode.CITATION_INVALID)
        require_enum_member(self.authority_level, SourceAuthorityLevel, "authority_level")
        if self.authority_level not in {
            SourceAuthorityLevel.OFFICIAL_PRIMARY,
            SourceAuthorityLevel.AUTHORITATIVE_SECONDARY,
        }:
            raise CorrectionPacketBoundaryError(Step24ReasonCode.CITATION_INVALID)
        require_enum_member(self.publication_state, SourcePublicationState, "publication_state")
        if self.publication_state is not SourcePublicationState.PUBLISHED:
            raise CorrectionPacketBoundaryError(Step24ReasonCode.CITATION_INVALID)
        require_enum_member(self.relation, ClaimEvidenceRelation, "relation")
        semantic = canonical_sha256(
            self,
            exclude_fields=("citation_id", "citation_hash"),
        )
        object.__setattr__(self, "citation_id", f"citation-{semantic}")
        object.__setattr__(
            self,
            "citation_hash",
            canonical_sha256(self, exclude_fields=("citation_hash",)),
        )


@dataclass(frozen=True, slots=True)
class CorrectionConflict:
    conflict_group_id: str
    affected_claim_ids: tuple[str, ...]
    supporting_evidence_hashes: tuple[str, ...]
    refuting_evidence_hashes: tuple[str, ...]
    temporal_assessment_hashes: tuple[str, ...]
    source_authority_levels: tuple[SourceAuthorityLevel, ...]
    required_handling: ConflictHandling
    conflict_hash: str = field(init=False)

    def __post_init__(self) -> None:
        _text(self.conflict_group_id, "conflict_group_id", 512)
        claims = tuple(self.affected_claim_ids)
        if not claims or len(claims) > MAX_CLAIMS:
            raise ContractValidationError("conflict claim count is invalid")
        if any(_CLAIM_ID.fullmatch(item) is None for item in claims):
            raise ContractValidationError("conflict claim identity is invalid")
        if claims != tuple(sorted(set(claims))):
            raise ContractValidationError("conflict claim identities must be canonical")
        object.__setattr__(self, "affected_claim_ids", claims)
        for name in (
            "supporting_evidence_hashes",
            "refuting_evidence_hashes",
            "temporal_assessment_hashes",
        ):
            object.__setattr__(
                self,
                name,
                _hash_tuple(getattr(self, name), name, MAX_CITATIONS),
            )
        authorities = tuple(self.source_authority_levels)
        if any(not isinstance(item, SourceAuthorityLevel) for item in authorities):
            raise ContractValidationError("conflict authorities must be typed")
        if authorities != tuple(sorted(set(authorities), key=lambda item: item.value)):
            raise ContractValidationError("conflict authorities must be canonical")
        object.__setattr__(self, "source_authority_levels", authorities)
        require_enum_member(self.required_handling, ConflictHandling, "required_handling")
        object.__setattr__(
            self,
            "conflict_hash",
            canonical_sha256(self, exclude_fields=("conflict_hash",)),
        )


@dataclass(frozen=True, slots=True)
class PacketIntegrityMetadata:
    public_hash_algorithm: str = PACKET_HASH_ALGORITHM
    authenticity_algorithm: str = PACKET_AUTHENTICITY_ALGORITHM
    domain_id: str = PACKET_HMAC_DOMAIN_ID
    authenticator_embedded_in_packet: bool = False
    key_material_embedded_in_packet: bool = False
    metadata_digest: str = field(init=False)

    def __post_init__(self) -> None:
        if (
            self.public_hash_algorithm != PACKET_HASH_ALGORITHM
            or self.authenticity_algorithm != PACKET_AUTHENTICITY_ALGORITHM
            or self.domain_id != PACKET_HMAC_DOMAIN_ID
            or self.authenticator_embedded_in_packet is not False
            or self.key_material_embedded_in_packet is not False
        ):
            raise ContractValidationError("Step 24 integrity boundary cannot be weakened")
        object.__setattr__(
            self,
            "metadata_digest",
            canonical_sha256(self, exclude_fields=("metadata_digest",)),
        )


@dataclass(frozen=True, slots=True)
class CorrectionPacketV1A:
    schema_version: str
    request_id: str
    tenant_id: str
    user_id: str
    route_hash: str
    selected_hat_id: str
    selected_hat_version: str
    selected_manifest_digest: str
    hat_scope_id: str
    effective_scope: tuple[ScopeDimension, ...]
    draft_id: str
    draft_v1_hash: str
    draft_text_sha256: str
    original_query_digest: str
    step20_evidence_bundle_ids: tuple[str, ...]
    step20_evidence_bundle_hashes: tuple[str, ...]
    step21_resolution_hash: str
    step23_input_snapshot_hash: str
    claim_processing_policy_digest: str
    knowledge_policy_binding: KnowledgePolicyBinding
    evidence_status: EvidenceStatus
    ordered_claims: tuple[ClaimRecord, ...]
    ordered_claim_assessments: tuple[ClaimEvidenceAssessment, ...]
    ordered_required_corrections: tuple[RequiredCorrection, ...]
    ordered_prohibited_claims: tuple[ProhibitedClaim, ...]
    ordered_conflicts: tuple[CorrectionConflict, ...]
    ordered_citations: tuple[CorrectionCitation, ...]
    temporal_freshness_limitations: tuple[str, ...]
    packet_policy: CorrectionPacketPolicy
    integrity_metadata: PacketIntegrityMetadata
    persistence_decision: str
    reason_codes: tuple[Step24ReasonCode, ...]
    packet_id: str = field(init=False)
    scope_binding_digest: str = field(init=False)
    packet_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != STEP24_SCHEMA_VERSION:
            raise ContractValidationError("unsupported Step 24 packet schema")
        for value, name in (
            (self.request_id, "request_id"),
            (self.tenant_id, "tenant_id"),
            (self.user_id, "user_id"),
            (self.selected_hat_id, "selected_hat_id"),
            (self.selected_hat_version, "selected_hat_version"),
            (self.hat_scope_id, "hat_scope_id"),
            (self.draft_id, "draft_id"),
        ):
            _text(value, name, 255)
        for value, name in (
            (self.route_hash, "route_hash"),
            (self.selected_manifest_digest, "selected_manifest_digest"),
            (self.draft_v1_hash, "draft_v1_hash"),
            (self.draft_text_sha256, "draft_text_sha256"),
            (self.original_query_digest, "original_query_digest"),
            (self.step21_resolution_hash, "step21_resolution_hash"),
            (self.step23_input_snapshot_hash, "step23_input_snapshot_hash"),
            (self.claim_processing_policy_digest, "claim_processing_policy_digest"),
        ):
            require_sha256_hex(value, name)
        scope = _scope_tuple(self.effective_scope, "effective_scope")
        object.__setattr__(self, "effective_scope", scope)
        bundle_ids = tuple(self.step20_evidence_bundle_ids)
        if len(bundle_ids) > 2 or len(set(bundle_ids)) != len(bundle_ids):
            raise ContractValidationError("Step 20 bundle IDs are invalid")
        for value in bundle_ids:
            _text(value, "step20_evidence_bundle_id", 255)
        object.__setattr__(self, "step20_evidence_bundle_ids", bundle_ids)
        bundle_hashes = _hash_tuple(
            self.step20_evidence_bundle_hashes,
            "step20_evidence_bundle_hashes",
            2,
            sorted_values=False,
        )
        if len(bundle_ids) != len(bundle_hashes):
            raise ContractValidationError("Step 20 bundle identity count differs")
        object.__setattr__(self, "step20_evidence_bundle_hashes", bundle_hashes)
        if not isinstance(self.knowledge_policy_binding, KnowledgePolicyBinding):
            raise ContractValidationError("knowledge policy binding must be typed")
        if (
            self.knowledge_policy_binding.step20_bundle_ids != bundle_ids
            or self.knowledge_policy_binding.step20_bundle_hashes != bundle_hashes
            or self.knowledge_policy_binding.step21_resolution_hash
            != self.step21_resolution_hash
            or self.knowledge_policy_binding.evidence_status is not self.evidence_status
        ):
            raise CorrectionPacketBoundaryError(Step24ReasonCode.PACKET_INPUT_HASH_INVALID)
        require_enum_member(self.evidence_status, EvidenceStatus, "evidence_status")
        if self.evidence_status is EvidenceStatus.NOT_REQUIRED:
            raise CorrectionPacketBoundaryError(Step24ReasonCode.EVIDENCE_INSUFFICIENT)
        claims = tuple(self.ordered_claims)
        if not claims or len(claims) > MAX_CLAIMS or any(
            not isinstance(item, ClaimRecord) for item in claims
        ):
            raise ContractValidationError("ordered_claims are invalid")
        if claims != tuple(
            sorted(claims, key=lambda item: (item.start_offset, item.end_offset, item.claim_id))
        ):
            raise ContractValidationError("claims are not canonically ordered")
        claim_ids = tuple(item.claim_id for item in claims)
        if len(set(claim_ids)) != len(claims):
            raise ContractValidationError("claim identities must be unique")
        for claim in claims:
            verify_claim_record_hash(claim)
            if claim.draft_id != self.draft_id or claim.draft_v1_hash != self.draft_v1_hash:
                raise CorrectionPacketBoundaryError(Step24ReasonCode.CLAIM_INPUT_INVALID)
        object.__setattr__(self, "ordered_claims", claims)
        claim_order = {claim_id: index for index, claim_id in enumerate(claim_ids)}
        assessments = tuple(self.ordered_claim_assessments)
        if len(assessments) != len(claims) or any(
            not isinstance(item, ClaimEvidenceAssessment) for item in assessments
        ):
            raise ContractValidationError("claim assessments must cover every claim")
        if assessments != tuple(sorted(assessments, key=lambda item: item.claim_id)):
            raise ContractValidationError("claim assessments are not canonically ordered")
        if tuple(item.claim_id for item in assessments) != tuple(sorted(claim_ids)):
            raise ContractValidationError("claim assessment coverage is incomplete")
        for assessment in assessments:
            verify_claim_assessment_hash(assessment)
        object.__setattr__(self, "ordered_claim_assessments", assessments)

        corrections = tuple(self.ordered_required_corrections)
        if len(corrections) > MAX_REQUIRED_CORRECTIONS or any(
            not isinstance(item, RequiredCorrection) for item in corrections
        ):
            raise ContractValidationError("required corrections are invalid")
        correction_order = lambda item: (
            claim_order.get(item.claim_id, MAX_CLAIMS + 1),
            _ACTION_ORDER[item.correction_action],
            item.correction_id,
        )
        if corrections != tuple(sorted(corrections, key=correction_order)):
            raise ContractValidationError("required corrections are not canonically ordered")
        if any(item.claim_id not in claim_order for item in corrections):
            raise ContractValidationError("required correction references an unknown claim")
        if len({item.correction_id for item in corrections}) != len(corrections):
            raise ContractValidationError("required correction identities must be unique")
        object.__setattr__(self, "ordered_required_corrections", corrections)

        prohibited = tuple(self.ordered_prohibited_claims)
        if len(prohibited) > MAX_PROHIBITED_CLAIMS or any(
            not isinstance(item, ProhibitedClaim) for item in prohibited
        ):
            raise ContractValidationError("prohibited claims are invalid")
        prohibition_order = lambda item: (
            claim_order.get(item.source_claim_id, MAX_CLAIMS + 1),
            _PROHIBITION_ORDER[item.prohibition_type],
            item.prohibited_claim_id,
        )
        if prohibited != tuple(sorted(prohibited, key=prohibition_order)):
            raise ContractValidationError("prohibited claims are not canonically ordered")
        if any(item.source_claim_id not in claim_order for item in prohibited):
            raise ContractValidationError("prohibited claim references an unknown claim")
        if len({item.prohibited_claim_id for item in prohibited}) != len(prohibited):
            raise ContractValidationError("prohibited claim identities must be unique")
        object.__setattr__(self, "ordered_prohibited_claims", prohibited)

        citations = tuple(self.ordered_citations)
        if len(citations) > MAX_CITATIONS or any(
            not isinstance(item, CorrectionCitation) for item in citations
        ):
            raise ContractValidationError("citations are invalid")
        citation_order = lambda item: (
            claim_order.get(item.claim_id, MAX_CLAIMS + 1),
            _RELATION_ORDER[item.relation],
            item.source_id,
            item.knowledge_version_id,
            item.chunk_id,
            item.candidate_hash,
            item.citation_id,
        )
        if citations != tuple(sorted(citations, key=citation_order)):
            raise ContractValidationError("citations are not canonically ordered")
        if any(item.claim_id not in claim_order for item in citations):
            raise ContractValidationError("citation references an unknown claim")
        if len({item.citation_id for item in citations}) != len(citations):
            raise ContractValidationError("citation identities must be unique")
        link_hashes = {item.evidence_link_hash for item in citations}
        citations_by_link = {
            item.evidence_link_hash: item.citation_id for item in citations
        }
        object.__setattr__(self, "ordered_citations", citations)
        assessment_link_hashes = {
            value
            for assessment in assessments
            for value in (
                assessment.supporting_link_hashes
                + assessment.refuting_link_hashes
                + assessment.related_link_hashes
                + assessment.insufficient_link_hashes
            )
        }
        if assessment_link_hashes != link_hashes:
            raise CorrectionPacketBoundaryError(Step24ReasonCode.EVIDENCE_LINK_INVALID)

        conflicts = tuple(self.ordered_conflicts)
        if len(conflicts) > MAX_CONFLICTS or any(
            not isinstance(item, CorrectionConflict) for item in conflicts
        ):
            raise ContractValidationError("conflicts are invalid")
        if conflicts != tuple(sorted(conflicts, key=lambda item: item.conflict_group_id)):
            raise ContractValidationError("conflicts are not canonically ordered")
        if len({item.conflict_group_id for item in conflicts}) != len(conflicts):
            raise ContractValidationError("conflict groups must be unique")
        if any(set(item.affected_claim_ids) - set(claim_ids) for item in conflicts):
            raise ContractValidationError("conflict references an unknown claim")
        object.__setattr__(self, "ordered_conflicts", conflicts)

        referenced = {
            value
            for item in corrections
            for value in item.supporting_evidence_link_hashes
        } | {
            value
            for item in prohibited
            for value in item.evidence_reference_hashes
        } | {
            value
            for item in conflicts
            for value in item.supporting_evidence_hashes + item.refuting_evidence_hashes
        }
        if referenced - link_hashes:
            raise CorrectionPacketBoundaryError(Step24ReasonCode.EVIDENCE_LINK_INVALID)
        if any(
            citations_by_link.get(fact.evidence_link_hash) != fact.citation_id
            for correction in corrections
            for fact in correction.required_replacement_facts
        ):
            raise CorrectionPacketBoundaryError(Step24ReasonCode.CITATION_INVALID)
        object.__setattr__(
            self,
            "temporal_freshness_limitations",
            _string_tuple(
                self.temporal_freshness_limitations,
                "temporal_freshness_limitations",
                MAX_LIMITATIONS,
            ),
        )
        if not isinstance(self.packet_policy, CorrectionPacketPolicy):
            raise ContractValidationError("packet_policy must be typed")
        if self.packet_policy != load_packet_policy():
            raise ContractValidationError("packet policy is not the approved Step 24 policy")
        if not isinstance(self.integrity_metadata, PacketIntegrityMetadata):
            raise ContractValidationError("integrity_metadata must be typed")
        if self.persistence_decision != PERSISTENCE_DECISION:
            raise ContractValidationError("Step 24 persistence decision is invalid")
        object.__setattr__(self, "reason_codes", _reason_tuple(self.reason_codes))
        if Step24ReasonCode.PACKET_BUILT not in self.reason_codes:
            raise ContractValidationError("packet build reason is missing")
        scope_digest = canonical_sha256(
            {
                "tenant_id": self.tenant_id,
                "user_id": self.user_id,
                "route_hash": self.route_hash,
                "selected_hat_id": self.selected_hat_id,
                "selected_hat_version": self.selected_hat_version,
                "selected_manifest_digest": self.selected_manifest_digest,
                "hat_scope_id": self.hat_scope_id,
                "effective_scope": scope,
            }
        )
        object.__setattr__(self, "scope_binding_digest", scope_digest)
        object.__setattr__(
            self,
            "packet_id",
            derive_packet_id(
                self.request_id,
                self.draft_v1_hash,
                self.step23_input_snapshot_hash,
                self.schema_version,
            ),
        )
        object.__setattr__(
            self,
            "packet_hash",
            canonical_sha256(self, exclude_fields=("packet_hash",)),
        )
        if len(canonical_json_bytes(self)) > MAX_CANONICAL_PACKET_BYTES:
            raise ContractValidationError("canonical Step 24 packet exceeds its byte bound")


@dataclass(frozen=True, slots=True)
class CorrectionPacketIntegrityReceipt:
    schema_version: str
    packet_hash: str
    integrity_algorithm: str
    key_id: str
    authenticator: str
    domain_id: str
    receipt_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != STEP24_SCHEMA_VERSION:
            raise ContractValidationError("unsupported integrity receipt schema")
        require_sha256_hex(self.packet_hash, "packet_hash")
        if self.integrity_algorithm != PACKET_AUTHENTICITY_ALGORITHM:
            raise ContractValidationError("unsupported packet integrity algorithm")
        _text(self.key_id, "key_id", 255)
        if not isinstance(self.authenticator, str) or _HEX_64.fullmatch(self.authenticator) is None:
            raise ContractValidationError("authenticator must be a lowercase HMAC digest")
        if self.domain_id != PACKET_HMAC_DOMAIN_ID:
            raise ContractValidationError("packet integrity domain is invalid")
        object.__setattr__(
            self,
            "receipt_hash",
            canonical_sha256(self, exclude_fields=("receipt_hash",)),
        )


def verify_fact_reference_hash(value: CorrectionFactReference) -> None:
    verify_canonical_hash(
        value,
        value.fact_reference_hash,
        exclude_fields=("fact_reference_hash",),
    )


def verify_required_correction_hash(value: RequiredCorrection) -> None:
    verify_canonical_hash(value, value.correction_hash, exclude_fields=("correction_hash",))


def verify_prohibited_claim_hash(value: ProhibitedClaim) -> None:
    verify_canonical_hash(value, value.prohibition_hash, exclude_fields=("prohibition_hash",))


def verify_citation_hash(value: CorrectionCitation) -> None:
    verify_canonical_hash(value, value.citation_hash, exclude_fields=("citation_hash",))


def verify_conflict_hash(value: CorrectionConflict) -> None:
    verify_canonical_hash(value, value.conflict_hash, exclude_fields=("conflict_hash",))


def verify_correction_packet_hash(value: CorrectionPacketV1A) -> None:
    try:
        verify_canonical_hash(value, value.packet_hash, exclude_fields=("packet_hash",))
    except IntegrityError as exc:
        raise CorrectionPacketBoundaryError(Step24ReasonCode.PACKET_HASH_INVALID) from exc
    expected_id = derive_packet_id(
        value.request_id,
        value.draft_v1_hash,
        value.step23_input_snapshot_hash,
        value.schema_version,
    )
    if value.packet_id != expected_id:
        raise CorrectionPacketBoundaryError(Step24ReasonCode.PACKET_HASH_INVALID)
    for claim in value.ordered_claims:
        verify_claim_record_hash(claim)
    for assessment in value.ordered_claim_assessments:
        verify_claim_assessment_hash(assessment)
    for correction in value.ordered_required_corrections:
        verify_required_correction_hash(correction)
        for fact in correction.required_replacement_facts:
            verify_fact_reference_hash(fact)
    for prohibited in value.ordered_prohibited_claims:
        verify_prohibited_claim_hash(prohibited)
    for citation in value.ordered_citations:
        verify_citation_hash(citation)
    for conflict in value.ordered_conflicts:
        verify_conflict_hash(conflict)


def canonical_packet_json(value: CorrectionPacketV1A) -> str:
    """Return the verified packet's exact repository-canonical JSON."""

    verify_correction_packet_hash(value)
    return canonical_json(value)


def canonical_packet_bytes(value: CorrectionPacketV1A) -> bytes:
    """Return the verified packet's exact canonical UTF-8 bytes."""

    return canonical_packet_json(value).encode("utf-8")


def verify_integrity_receipt_hash(value: CorrectionPacketIntegrityReceipt) -> None:
    verify_canonical_hash(value, value.receipt_hash, exclude_fields=("receipt_hash",))


def verify_packet_against_snapshot(
    packet: CorrectionPacketV1A,
    snapshot: PacketInputSnapshot,
) -> None:
    from aioa_memory_kernel.claims import verify_packet_input_snapshot_hash

    try:
        verify_packet_input_snapshot_hash(snapshot)
        verify_correction_packet_hash(packet)
    except (ContractValidationError, IntegrityError) as exc:
        raise CorrectionPacketBoundaryError(Step24ReasonCode.PACKET_INPUT_HASH_INVALID) from exc
    expected = (
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
        snapshot.snapshot_hash,
        snapshot.claim_processing_policy_digest,
        snapshot.step21_evidence_status,
        snapshot.ordered_claims,
        snapshot.ordered_candidate_assessments,
    )
    actual = (
        packet.request_id,
        packet.tenant_id,
        packet.user_id,
        packet.route_hash,
        packet.selected_hat_id,
        packet.selected_hat_version,
        packet.selected_manifest_digest,
        packet.hat_scope_id,
        packet.effective_scope,
        packet.draft_id,
        packet.draft_v1_hash,
        packet.draft_text_sha256,
        packet.original_query_digest,
        packet.step20_evidence_bundle_ids,
        packet.step20_evidence_bundle_hashes,
        packet.step21_resolution_hash,
        packet.step23_input_snapshot_hash,
        packet.claim_processing_policy_digest,
        packet.evidence_status,
        packet.ordered_claims,
        packet.ordered_claim_assessments,
    )
    if actual != expected:
        raise CorrectionPacketBoundaryError(Step24ReasonCode.PACKET_INPUT_HASH_INVALID)


__all__ = [
    "ConflictHandling",
    "CorrectionActionType",
    "CorrectionCitation",
    "CorrectionConflict",
    "CorrectionFactReference",
    "CorrectionPacketBoundaryError",
    "CorrectionPacketIntegrityReceipt",
    "CorrectionPacketPolicy",
    "CorrectionPacketV1A",
    "KnowledgePolicyBinding",
    "MAX_CANONICAL_PACKET_BYTES",
    "MAX_CITATIONS",
    "MAX_CONFLICTS",
    "MAX_PROHIBITED_CLAIMS",
    "MAX_REQUIRED_CORRECTIONS",
    "PACKET_AUTHENTICITY_ALGORITHM",
    "PACKET_HASH_ALGORITHM",
    "PACKET_HMAC_DOMAIN_ID",
    "PACKET_POLICY_ID",
    "PACKET_POLICY_VERSION",
    "PERSISTENCE_DECISION",
    "PacketIntegrityMetadata",
    "ProhibitedClaim",
    "ProhibitionType",
    "RequiredCorrection",
    "STEP24_SCHEMA_VERSION",
    "Step24ReasonCode",
    "canonical_packet_bytes",
    "canonical_packet_json",
    "derive_packet_id",
    "load_packet_policy",
    "reason_codes",
    "verify_citation_hash",
    "verify_conflict_hash",
    "verify_correction_packet_hash",
    "verify_fact_reference_hash",
    "verify_integrity_receipt_hash",
    "verify_packet_against_snapshot",
    "verify_prohibited_claim_hash",
    "verify_required_correction_hash",
]
