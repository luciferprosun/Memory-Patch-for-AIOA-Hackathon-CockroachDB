"""Step 29 owner-private Personal Memory Patch proposal contracts.

The module owns only the deterministic path from one verified Step 28
``DETECTED`` candidate to ``AWAITING_APPROVAL``.  It deliberately contains no
approval, commitment, activation, retrieval, provider, network, or execution
capability.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, fields as dataclass_fields
from datetime import datetime
from typing import Any

from aioa_memory_kernel.answers.models import (
    VerifiedAnswer,
    verify_verified_answer_hash,
)
from aioa_memory_kernel.claims.models import (
    ClaimEvidenceAssessment,
    ClaimEvidenceCandidateStatus,
    ClaimEvidenceLink,
    ClaimEvidenceRelation,
    verify_claim_assessment_hash,
    verify_claim_evidence_link_hash,
)
from aioa_memory_kernel.contracts.enums import (
    ActorType,
    EvidenceStatus,
    MemoryContentKind,
    PatchState,
    PersonalMemorySpaceState,
    ProposalOrigin,
    ScopeComparisonMode,
    ScopeValueType,
    StableStringEnum,
)
from aioa_memory_kernel.contracts.exceptions import (
    ContractValidationError,
    IntegrityError,
)
from aioa_memory_kernel.contracts.scope import ScopeDimension
from aioa_memory_kernel.contracts.serialization import (
    canonical_json,
    canonical_json_bytes,
    canonical_sha256,
    ensure_utc,
    require_sha256_hex,
    to_canonical_data,
    verify_canonical_hash,
)
from aioa_memory_kernel.corrections.models import (
    CorrectionPacketV1A,
    verify_correction_packet_hash,
)
from aioa_memory_kernel.evidence.models import (
    EvidenceBundleItem,
    FrozenEvidenceBundle,
    verify_bundle_item_hash,
    verify_evidence_bundle_hash,
)
from aioa_memory_kernel.persistence.errors import PersistenceError
from aioa_memory_kernel.sources.models import (
    SourceAuthorityLevel,
    SourcePublicationState,
)
from aioa_memory_kernel.temporal.models import (
    FreshnessStatus,
    TemporalApplicability,
    TemporalResolutionResult,
    verify_temporal_result_hash,
)

from .candidates import (
    CorrectionCandidateEnvelope,
    CorrectionCandidateTargetBinding,
    parse_correction_candidate_envelope,
    parse_correction_candidate_target_binding,
    verify_correction_candidate_envelope,
    verify_correction_candidate_target_binding,
)


STEP29_SCHEMA_VERSION = "1.0.0"
PERSONAL_MEMORY_PATCH_PROPOSAL_CONTRACT_TYPE = "PersonalMemoryPatchProposal"
PERSONAL_MEMORY_PATCH_STATE_CONTRACT_TYPE = "PersonalMemoryPatchProposalState"
PERSONAL_MEMORY_PATCH_VALIDATION_POLICY_ID = (
    "personal-memory-patch-validation-1a"
)
PERSONAL_MEMORY_PATCH_VALIDATION_POLICY_VERSION = "1"
MAXIMUM_PROPOSAL_TEXT_BYTES = 16 * 1024
MAXIMUM_PROPOSAL_RECORD_BYTES = 512 * 1024
MAXIMUM_PROPOSALS_PER_SLOT = 128
MAXIMUM_PROPOSAL_BYTES_PER_SLOT = 8 * 1024 * 1024
MAXIMUM_EVIDENCE_REFERENCES = 256
MAXIMUM_REASON_CODES = 64
MAXIMUM_LIMITATIONS = 64
MAXIMUM_TEMPORAL_REVALIDATION_AGE_SECONDS = 24 * 60 * 60

STEP29_STATES = (
    PatchState.PROPOSED,
    PatchState.EVIDENCE_BOUND,
    PatchState.VALIDATED,
    PatchState.AWAITING_APPROVAL,
)
STEP29_STATE_VERSIONS = {
    PatchState.PROPOSED: 1,
    PatchState.EVIDENCE_BOUND: 2,
    PatchState.VALIDATED: 3,
    PatchState.AWAITING_APPROVAL: 4,
}
STEP29_TRANSITIONS = {
    PatchState.PROPOSED: PatchState.EVIDENCE_BOUND,
    PatchState.EVIDENCE_BOUND: PatchState.VALIDATED,
    PatchState.VALIDATED: PatchState.AWAITING_APPROVAL,
}
STEP29_ELIGIBLE_SLOT_STATES = frozenset(
    {PersonalMemorySpaceState.CONFIGURED, PersonalMemorySpaceState.ACTIVE}
)

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_LOGICAL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+-]{0,254}$")
_NEGATION = frozenset(
    {"not", "no", "never", "nicht", "kein", "keine", "keinen", "ohne"}
)
_WORD = re.compile(r"[^\w]+", re.UNICODE)


class PersonalMemoryPatchProposalKind(StableStringEnum):
    FACT_CORRECTION = "FACT_CORRECTION"


class ProposalDedupResult(StableStringEnum):
    PASS = "PASS"
    EXACT_DUPLICATE = "EXACT_DUPLICATE"
    EXISTING_PATCH_DUPLICATE = "EXISTING_PATCH_DUPLICATE"


class ProposalConflictResult(StableStringEnum):
    PASS = "PASS"
    DIRECT_CONTRADICTION = "DIRECT_CONTRADICTION"
    SCOPE_OVERLAP_CONFLICT = "SCOPE_OVERLAP_CONFLICT"
    TEMPORAL_CONFLICT = "TEMPORAL_CONFLICT"
    EXISTING_PATCH_CONFLICT = "EXISTING_PATCH_CONFLICT"
    CANONICAL_EVIDENCE_CONFLICT = "CANONICAL_EVIDENCE_CONFLICT"
    UNRESOLVED_SOURCE_CONFLICT = "UNRESOLVED_SOURCE_CONFLICT"


class ProposalFreshnessResult(StableStringEnum):
    FRESH = "FRESH"
    STALE = "STALE"
    INSUFFICIENT = "INSUFFICIENT"
    UNAVAILABLE = "UNAVAILABLE"
    CONFLICTING = "CONFLICTING"
    INVALID = "INVALID"


class ProposalGateResult(StableStringEnum):
    PASS = "PASS"
    FAIL = "FAIL"


class Step29ReasonCode(StableStringEnum):
    PROPOSAL_CREATED = "PROPOSAL_CREATED"
    PROPOSAL_EXACT_REPLAY = "PROPOSAL_EXACT_REPLAY"
    PROPOSAL_DUPLICATE = "PROPOSAL_DUPLICATE"
    PROPOSAL_CONFLICT = "PROPOSAL_CONFLICT"
    EVIDENCE_BOUND = "EVIDENCE_BOUND"
    EVIDENCE_INVALID = "EVIDENCE_INVALID"
    EVIDENCE_INSUFFICIENT = "EVIDENCE_INSUFFICIENT"
    EVIDENCE_CONFLICTING = "EVIDENCE_CONFLICTING"
    EVIDENCE_STALE = "EVIDENCE_STALE"
    EVIDENCE_UNAVAILABLE = "EVIDENCE_UNAVAILABLE"
    OWNER_SCOPE_VALID = "OWNER_SCOPE_VALID"
    OWNER_SCOPE_MISMATCH = "OWNER_SCOPE_MISMATCH"
    TARGET_SLOT_VALID = "TARGET_SLOT_VALID"
    TARGET_SLOT_INVALID = "TARGET_SLOT_INVALID"
    TARGET_SLOT_ARCHIVED = "TARGET_SLOT_ARCHIVED"
    TARGET_SLOT_DELETE_PENDING = "TARGET_SLOT_DELETE_PENDING"
    DEDUP_PASS = "DEDUP_PASS"
    DEDUP_FAIL = "DEDUP_FAIL"
    CONFLICT_CHECK_PASS = "CONFLICT_CHECK_PASS"
    CONFLICT_CHECK_FAIL = "CONFLICT_CHECK_FAIL"
    FRESHNESS_PASS = "FRESHNESS_PASS"
    FRESHNESS_FAIL = "FRESHNESS_FAIL"
    TEMPORAL_PASS = "TEMPORAL_PASS"
    TEMPORAL_FAIL = "TEMPORAL_FAIL"
    QUOTA_PASS = "QUOTA_PASS"
    QUOTA_FAIL = "QUOTA_FAIL"
    MODEL_BINDING_PASS = "MODEL_BINDING_PASS"
    MODEL_BINDING_FAIL = "MODEL_BINDING_FAIL"
    PROPOSAL_VALIDATED = "PROPOSAL_VALIDATED"
    PROPOSAL_VALIDATION_FAILED = "PROPOSAL_VALIDATION_FAILED"
    AWAITING_APPROVAL_READY = "AWAITING_APPROVAL_READY"
    APPROVAL_FORBIDDEN = "APPROVAL_FORBIDDEN"
    COMMIT_FORBIDDEN = "COMMIT_FORBIDDEN"
    ACTIVATION_FORBIDDEN = "ACTIVATION_FORBIDDEN"
    STATE_TRANSITION_INVALID = "STATE_TRANSITION_INVALID"
    STATE_VERSION_CONFLICT = "STATE_VERSION_CONFLICT"
    CONTENT_CONFLICT = "CONTENT_CONFLICT"


class PersonalMemoryPatchValidationError(PersistenceError):
    def __init__(self, reason_code: Step29ReasonCode) -> None:
        if not isinstance(reason_code, Step29ReasonCode):
            raise TypeError("reason_code must be Step29ReasonCode")
        self.reason_code = reason_code
        super().__init__(
            "Personal Memory Patch validation failed",
            sanitized_code=reason_code.value,
            operation_kind="PERSONAL_MEMORY_PATCH_VALIDATION",
        )


def _text(value: object, name: str, maximum_bytes: int = 255) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or _CONTROL.search(value)
        or len(value.encode("utf-8")) > maximum_bytes
    ):
        raise ContractValidationError(f"{name} is not bounded canonical text")
    return value


def _logical_id(value: object, name: str) -> str:
    text = _text(value, name, 255)
    if _LOGICAL_ID.fullmatch(text) is None:
        raise ContractValidationError(f"{name} is not a logical identity")
    return text


def _optional_hash(value: object | None, name: str) -> str | None:
    if value is None:
        return None
    return require_sha256_hex(value, name)  # type: ignore[arg-type]


def _hash_tuple(value: object, name: str, maximum: int) -> tuple[str, ...]:
    if not isinstance(value, (tuple, list)) or len(value) > maximum:
        raise ContractValidationError(f"{name} is outside its bound")
    result = tuple(value)
    for item in result:
        require_sha256_hex(item, f"{name} item")
    if result != tuple(sorted(result)) or len(result) != len(set(result)):
        raise ContractValidationError(f"{name} must be unique and ordered")
    return result


def _scope_tuple(value: object) -> tuple[ScopeDimension, ...]:
    if not isinstance(value, (tuple, list)) or not value or len(value) > 64:
        raise ContractValidationError("proposal_scope is invalid")
    result = tuple(value)
    if any(not isinstance(item, ScopeDimension) for item in result):
        raise ContractValidationError("proposal_scope must contain ScopeDimension")
    ordered = tuple(sorted(result, key=lambda item: item.name))
    if result != ordered or len({item.name for item in result}) != len(result):
        raise ContractValidationError("proposal_scope must be unique and ordered")
    return result


def _reason_tuple(value: object) -> tuple[Step29ReasonCode, ...]:
    if not isinstance(value, (tuple, list)) or not value or len(value) > MAXIMUM_REASON_CODES:
        raise ContractValidationError("reason_codes are invalid")
    result = tuple(value)
    if any(not isinstance(item, Step29ReasonCode) for item in result):
        raise ContractValidationError("reason_codes must be Step29ReasonCode")
    ordered = tuple(sorted(set(result), key=lambda item: item.value))
    if result != ordered:
        raise ContractValidationError("reason_codes must be unique and ordered")
    return result


def _limitations(value: object) -> tuple[str, ...]:
    if not isinstance(value, (tuple, list)) or len(value) > MAXIMUM_LIMITATIONS:
        raise ContractValidationError("limitations are invalid")
    result = tuple(_text(item, "limitation", 4096) for item in value)
    if result != tuple(sorted(set(result))):
        raise ContractValidationError("limitations must be unique and ordered")
    return result


def normalize_proposal_statement(value: str) -> str:
    value = _text(value, "proposal_statement", MAXIMUM_PROPOSAL_TEXT_BYTES)
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = " ".join(_WORD.sub(" ", normalized).split())
    if not normalized:
        raise ContractValidationError("proposal statement normalizes to empty")
    return normalized


def proposal_conflict_subject(value: str) -> tuple[str, bool]:
    tokens = normalize_proposal_statement(value).split()
    negative = any(token in _NEGATION for token in tokens)
    subject = " ".join(token for token in tokens if token not in _NEGATION)
    if not subject:
        raise ContractValidationError("proposal conflict subject is empty")
    return subject, negative


def _origin_for(source: ActorType) -> ProposalOrigin:
    if source is ActorType.KNOWLEDGE_KERNEL:
        return ProposalOrigin.KNOWLEDGE_KERNEL
    if source is ActorType.CRITIC_PROMPT_LOOP:
        return ProposalOrigin.CRITIC_PROMPT_LOOP
    raise ContractValidationError("candidate source cannot create a Step 29 proposal")


def _reconstruct(value: object, context: str) -> None:
    cls = type(value)
    init_values = {
        item.name: getattr(value, item.name)
        for item in dataclass_fields(value)
        if item.init
    }
    try:
        rebuilt = cls(**init_values)
    except Exception as exc:
        raise IntegrityError(f"{context} semantic reconstruction failed") from exc
    if rebuilt != value:
        raise IntegrityError(f"{context} differs from reconstructed semantics")


@dataclass(frozen=True, slots=True)
class PersonalMemoryPatchValidationPolicy:
    policy_id: str = PERSONAL_MEMORY_PATCH_VALIDATION_POLICY_ID
    policy_version: str = PERSONAL_MEMORY_PATCH_VALIDATION_POLICY_VERSION
    maximum_proposals_per_slot: int = MAXIMUM_PROPOSALS_PER_SLOT
    maximum_proposal_bytes_per_slot: int = MAXIMUM_PROPOSAL_BYTES_PER_SLOT
    maximum_evidence_references: int = MAXIMUM_EVIDENCE_REFERENCES
    maximum_temporal_revalidation_age_seconds: int = (
        MAXIMUM_TEMPORAL_REVALIDATION_AGE_SECONDS
    )
    policy_digest: str = field(init=False)

    def __post_init__(self) -> None:
        _logical_id(self.policy_id, "policy_id")
        _logical_id(self.policy_version, "policy_version")
        for name in (
            "maximum_proposals_per_slot",
            "maximum_proposal_bytes_per_slot",
            "maximum_evidence_references",
            "maximum_temporal_revalidation_age_seconds",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ContractValidationError(f"{name} must be positive")
        if self.maximum_proposals_per_slot > MAXIMUM_PROPOSALS_PER_SLOT:
            raise ContractValidationError("proposal count policy exceeds hard limit")
        if self.maximum_proposal_bytes_per_slot > MAXIMUM_PROPOSAL_BYTES_PER_SLOT:
            raise ContractValidationError("proposal byte policy exceeds hard limit")
        object.__setattr__(
            self,
            "policy_digest",
            canonical_sha256(self, exclude_fields=("policy_digest",)),
        )


def load_personal_memory_patch_validation_policy() -> PersonalMemoryPatchValidationPolicy:
    return PersonalMemoryPatchValidationPolicy()


@dataclass(frozen=True, slots=True)
class CreatePersonalMemoryPatchProposal:
    schema_version: str
    tenant_id: str
    owner_user_id: str
    personal_memory_space_id: str
    candidate_id: str
    candidate_envelope_hash: str
    expected_target_binding_hash: str
    idempotency_key: str
    requested_at: datetime
    command_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != STEP29_SCHEMA_VERSION:
            raise ContractValidationError("unsupported create-proposal schema")
        for value, name in (
            (self.tenant_id, "tenant_id"),
            (self.owner_user_id, "owner_user_id"),
            (self.personal_memory_space_id, "personal_memory_space_id"),
            (self.candidate_id, "candidate_id"),
            (self.idempotency_key, "idempotency_key"),
        ):
            _logical_id(value, name)
        require_sha256_hex(self.candidate_envelope_hash, "candidate_envelope_hash")
        require_sha256_hex(
            self.expected_target_binding_hash,
            "expected_target_binding_hash",
        )
        object.__setattr__(self, "requested_at", ensure_utc(self.requested_at))
        object.__setattr__(
            self,
            "command_hash",
            canonical_sha256(self, exclude_fields=("command_hash",)),
        )


@dataclass(frozen=True, slots=True)
class Step29TransitionCommand:
    schema_version: str
    tenant_id: str
    owner_user_id: str
    personal_memory_space_id: str
    proposal_id: str
    proposal_hash: str
    expected_state: PatchState
    expected_state_version: int
    expected_state_hash: str
    idempotency_key: str
    requested_at: datetime
    validation_receipt_hash: str | None = None
    command_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != STEP29_SCHEMA_VERSION:
            raise ContractValidationError("unsupported Step 29 command schema")
        for value, name in (
            (self.tenant_id, "tenant_id"),
            (self.owner_user_id, "owner_user_id"),
            (self.personal_memory_space_id, "personal_memory_space_id"),
            (self.proposal_id, "proposal_id"),
            (self.idempotency_key, "idempotency_key"),
        ):
            _logical_id(value, name)
        require_sha256_hex(self.proposal_hash, "proposal_hash")
        require_sha256_hex(self.expected_state_hash, "expected_state_hash")
        if self.expected_state not in STEP29_STATES:
            raise ContractValidationError("expected_state is outside Step 29")
        if (
            isinstance(self.expected_state_version, bool)
            or not isinstance(self.expected_state_version, int)
            or self.expected_state_version < 1
        ):
            raise ContractValidationError("expected_state_version is invalid")
        _logical_id(self.idempotency_key, "idempotency_key")
        object.__setattr__(self, "requested_at", ensure_utc(self.requested_at))
        _optional_hash(self.validation_receipt_hash, "validation_receipt_hash")
        object.__setattr__(
            self,
            "command_hash",
            canonical_sha256(self, exclude_fields=("command_hash",)),
        )


@dataclass(frozen=True, slots=True)
class BindPersonalMemoryPatchEvidence(Step29TransitionCommand):
    def __post_init__(self) -> None:
        super(BindPersonalMemoryPatchEvidence, self).__post_init__()
        if self.expected_state is not PatchState.PROPOSED:
            raise ContractValidationError("evidence binding requires PROPOSED")
        if self.validation_receipt_hash is not None:
            raise ContractValidationError("evidence binding cannot carry a receipt")


@dataclass(frozen=True, slots=True)
class ValidatePersonalMemoryPatchProposal(Step29TransitionCommand):
    def __post_init__(self) -> None:
        super(ValidatePersonalMemoryPatchProposal, self).__post_init__()
        if self.expected_state is not PatchState.EVIDENCE_BOUND:
            raise ContractValidationError("proposal validation requires EVIDENCE_BOUND")
        if self.validation_receipt_hash is not None:
            raise ContractValidationError("validation command cannot self-certify")


@dataclass(frozen=True, slots=True)
class AdvancePersonalMemoryPatchToAwaitingApproval(Step29TransitionCommand):
    def __post_init__(self) -> None:
        super(AdvancePersonalMemoryPatchToAwaitingApproval, self).__post_init__()
        if self.expected_state is not PatchState.VALIDATED:
            raise ContractValidationError("approval readiness requires VALIDATED")
        if self.validation_receipt_hash is None:
            raise ContractValidationError("approval readiness requires exact receipt hash")


@dataclass(frozen=True, slots=True)
class PersonalMemoryPatchProposal:
    schema_version: str
    contract_type: str
    tenant_id: str
    owner_user_id: str
    personal_memory_space_id: str
    hat_scope_id: str
    target_slot_binding: CorrectionCandidateTargetBinding
    target_binding_hash: str
    model_binding_id: str
    model_binding_hash: str
    candidate_id: str
    candidate_hash: str
    candidate_envelope_hash: str
    candidate_claim_ids: tuple[str, ...]
    candidate_evidence_reference_hashes: tuple[str, ...]
    proposal_statement: str
    proposal_scope: tuple[ScopeDimension, ...]
    proposal_kind: PersonalMemoryPatchProposalKind
    content_kind: MemoryContentKind
    origin: ProposalOrigin
    route_hash: str
    source_result_hash: str
    original_query_digest: str
    draft_v1_hash: str
    draft_v2_hash: str | None
    correction_packet_hash: str | None
    verification_summary_hash: str | None
    verified_answer_hash: str | None
    created_at: datetime
    reason_codes: tuple[Step29ReasonCode, ...]
    proposal_statement_sha256: str = field(init=False)
    normalized_statement: str = field(init=False)
    normalized_statement_sha256: str = field(init=False)
    conflict_subject_sha256: str = field(init=False)
    negative_polarity: bool = field(init=False)
    exact_dedup_key: str = field(init=False)
    proposal_id: str = field(init=False)
    proposal_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != STEP29_SCHEMA_VERSION:
            raise ContractValidationError("unsupported proposal schema")
        if self.contract_type != PERSONAL_MEMORY_PATCH_PROPOSAL_CONTRACT_TYPE:
            raise ContractValidationError("proposal contract type is invalid")
        for value, name in (
            (self.tenant_id, "tenant_id"),
            (self.owner_user_id, "owner_user_id"),
            (self.personal_memory_space_id, "personal_memory_space_id"),
            (self.hat_scope_id, "hat_scope_id"),
            (self.model_binding_id, "model_binding_id"),
            (self.candidate_id, "candidate_id"),
        ):
            _logical_id(value, name)
        for value, name in (
            (self.target_binding_hash, "target_binding_hash"),
            (self.model_binding_hash, "model_binding_hash"),
            (self.candidate_hash, "candidate_hash"),
            (self.candidate_envelope_hash, "candidate_envelope_hash"),
            (self.route_hash, "route_hash"),
            (self.source_result_hash, "source_result_hash"),
            (self.original_query_digest, "original_query_digest"),
            (self.draft_v1_hash, "draft_v1_hash"),
        ):
            require_sha256_hex(value, name)
        if not isinstance(self.target_slot_binding, CorrectionCandidateTargetBinding):
            raise ContractValidationError("proposal target binding must be typed")
        verify_correction_candidate_target_binding(self.target_slot_binding)
        target = self.target_slot_binding
        if (
            target.tenant_id != self.tenant_id
            or target.owner_user_id != self.owner_user_id
            or target.personal_memory_space_id != self.personal_memory_space_id
            or target.hat_scope_id != self.hat_scope_id
            or target.target_binding_hash != self.target_binding_hash
            or target.model_binding_id != self.model_binding_id
            or target.model_binding_hash != self.model_binding_hash
        ):
            raise ContractValidationError(
                "proposal target snapshot is detached from candidate lineage"
            )
        for value, name in (
            (self.draft_v2_hash, "draft_v2_hash"),
            (self.correction_packet_hash, "correction_packet_hash"),
            (self.verification_summary_hash, "verification_summary_hash"),
            (self.verified_answer_hash, "verified_answer_hash"),
        ):
            _optional_hash(value, name)
        claims = tuple(self.candidate_claim_ids)
        if not claims or claims != tuple(sorted(set(claims))):
            raise ContractValidationError("candidate_claim_ids must be unique and ordered")
        for item in claims:
            _logical_id(item, "candidate_claim_id")
        object.__setattr__(self, "candidate_claim_ids", claims)
        object.__setattr__(
            self,
            "candidate_evidence_reference_hashes",
            _hash_tuple(
                self.candidate_evidence_reference_hashes,
                "candidate_evidence_reference_hashes",
                MAXIMUM_EVIDENCE_REFERENCES,
            ),
        )
        statement = _text(
            self.proposal_statement,
            "proposal_statement",
            MAXIMUM_PROPOSAL_TEXT_BYTES,
        )
        normalized = normalize_proposal_statement(statement)
        subject, negative = proposal_conflict_subject(statement)
        object.__setattr__(
            self,
            "proposal_statement_sha256",
            hashlib.sha256(statement.encode("utf-8")).hexdigest(),
        )
        object.__setattr__(self, "normalized_statement", normalized)
        object.__setattr__(
            self,
            "normalized_statement_sha256",
            hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
        )
        object.__setattr__(
            self,
            "conflict_subject_sha256",
            hashlib.sha256(subject.encode("utf-8")).hexdigest(),
        )
        object.__setattr__(self, "negative_polarity", negative)
        scope = _scope_tuple(self.proposal_scope)
        object.__setattr__(self, "proposal_scope", scope)
        if self.proposal_kind is not PersonalMemoryPatchProposalKind.FACT_CORRECTION:
            raise ContractValidationError("Step 29 1A supports factual corrections only")
        if self.content_kind is not MemoryContentKind.FACTUAL:
            raise ContractValidationError("Step 29 proposal must remain factual")
        if self.origin not in {
            ProposalOrigin.KNOWLEDGE_KERNEL,
            ProposalOrigin.CRITIC_PROMPT_LOOP,
        }:
            raise ContractValidationError("proposal origin is outside Step 29")
        object.__setattr__(self, "created_at", ensure_utc(self.created_at))
        object.__setattr__(self, "reason_codes", _reason_tuple(self.reason_codes))
        if Step29ReasonCode.PROPOSAL_CREATED not in self.reason_codes:
            raise ContractValidationError("proposal creation reason is missing")
        dedup_digest = canonical_sha256(
            {
                "tenant_id": self.tenant_id,
                "owner_user_id": self.owner_user_id,
                "personal_memory_space_id": self.personal_memory_space_id,
                "normalized_statement_sha256": self.normalized_statement_sha256,
                "proposal_scope": scope,
                "proposal_kind": self.proposal_kind,
            }
        )
        object.__setattr__(self, "exact_dedup_key", f"personal-patch-dedup-{dedup_digest}")
        identity = canonical_sha256(
            {
                "tenant_id": self.tenant_id,
                "owner_user_id": self.owner_user_id,
                "personal_memory_space_id": self.personal_memory_space_id,
                "candidate_hash": self.candidate_hash,
                "proposal_statement_sha256": self.proposal_statement_sha256,
                "proposal_scope": scope,
                "proposal_kind": self.proposal_kind,
            }
        )
        object.__setattr__(
            self,
            "proposal_id",
            f"personal-memory-patch-proposal-{identity}",
        )
        object.__setattr__(
            self,
            "proposal_hash",
            canonical_sha256(self, exclude_fields=("proposal_hash",)),
        )

    @property
    def canonical_evidence(self) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class PersonalMemoryPatchEvidenceReference:
    claim_id: str
    evidence_link_hash: str
    step20_bundle_hash: str
    step20_item_hash: str
    candidate_identity_hash: str
    evidence_id: str
    source_id: str
    knowledge_version_id: str
    chunk_id: str
    content_sha256: str
    authority_level: SourceAuthorityLevel
    publication_state: SourcePublicationState
    temporal_assessment_hash: str
    temporal_applicability: TemporalApplicability
    freshness_status: FreshnessStatus
    conflict_group_id: str | None
    relation: ClaimEvidenceRelation
    reference_hash: str = field(init=False)

    def __post_init__(self) -> None:
        for value, name in (
            (self.claim_id, "claim_id"),
            (self.evidence_id, "evidence_id"),
            (self.source_id, "source_id"),
            (self.knowledge_version_id, "knowledge_version_id"),
            (self.chunk_id, "chunk_id"),
        ):
            _logical_id(value, name)
        for value, name in (
            (self.evidence_link_hash, "evidence_link_hash"),
            (self.step20_bundle_hash, "step20_bundle_hash"),
            (self.step20_item_hash, "step20_item_hash"),
            (self.candidate_identity_hash, "candidate_identity_hash"),
            (self.content_sha256, "content_sha256"),
            (self.temporal_assessment_hash, "temporal_assessment_hash"),
        ):
            require_sha256_hex(value, name)
        if not isinstance(self.authority_level, SourceAuthorityLevel):
            raise ContractValidationError("authority_level is invalid")
        if self.authority_level in {
            SourceAuthorityLevel.DERIVED,
            SourceAuthorityLevel.UNKNOWN,
        }:
            raise ContractValidationError("derived/unknown evidence cannot bind a patch")
        if self.publication_state is not SourcePublicationState.PUBLISHED:
            raise ContractValidationError("only published evidence may bind a patch")
        if not isinstance(self.temporal_applicability, TemporalApplicability):
            raise ContractValidationError("temporal_applicability is invalid")
        if not isinstance(self.freshness_status, FreshnessStatus):
            raise ContractValidationError("freshness_status is invalid")
        if self.conflict_group_id is not None:
            _text(self.conflict_group_id, "conflict_group_id", 512)
        if self.relation is not ClaimEvidenceRelation.SUPPORTS:
            raise ContractValidationError("bound patch evidence must support the claim")
        object.__setattr__(
            self,
            "reference_hash",
            canonical_sha256(self, exclude_fields=("reference_hash",)),
        )


@dataclass(frozen=True, slots=True)
class PersonalMemoryPatchEvidenceBinding:
    schema_version: str
    proposal_id: str
    proposal_hash: str
    candidate_hash: str
    tenant_id: str
    owner_user_id: str
    personal_memory_space_id: str
    route_hash: str
    effective_scope: tuple[ScopeDimension, ...]
    evidence_bundle_hashes: tuple[str, ...]
    temporal_resolution_hash: str
    claim_assessment_hashes: tuple[str, ...]
    correction_packet_hash: str
    verified_answer_hash: str
    ordered_evidence_references: tuple[PersonalMemoryPatchEvidenceReference, ...]
    evidence_status: EvidenceStatus
    conflict_group_hashes: tuple[str, ...]
    prohibited_claim_hashes: tuple[str, ...]
    limitations: tuple[str, ...]
    reason_codes: tuple[Step29ReasonCode, ...]
    bound_at: datetime
    binding_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != STEP29_SCHEMA_VERSION:
            raise ContractValidationError("unsupported evidence-binding schema")
        for value, name in (
            (self.proposal_id, "proposal_id"),
            (self.tenant_id, "tenant_id"),
            (self.owner_user_id, "owner_user_id"),
            (self.personal_memory_space_id, "personal_memory_space_id"),
        ):
            _logical_id(value, name)
        for value, name in (
            (self.proposal_hash, "proposal_hash"),
            (self.candidate_hash, "candidate_hash"),
            (self.route_hash, "route_hash"),
            (self.temporal_resolution_hash, "temporal_resolution_hash"),
            (self.correction_packet_hash, "correction_packet_hash"),
            (self.verified_answer_hash, "verified_answer_hash"),
        ):
            require_sha256_hex(value, name)
        object.__setattr__(self, "effective_scope", _scope_tuple(self.effective_scope))
        object.__setattr__(
            self,
            "evidence_bundle_hashes",
            _hash_tuple(self.evidence_bundle_hashes, "evidence_bundle_hashes", 2),
        )
        object.__setattr__(
            self,
            "claim_assessment_hashes",
            _hash_tuple(
                self.claim_assessment_hashes,
                "claim_assessment_hashes",
                MAXIMUM_EVIDENCE_REFERENCES,
            ),
        )
        references = tuple(self.ordered_evidence_references)
        if not references or len(references) > MAXIMUM_EVIDENCE_REFERENCES or any(
            not isinstance(item, PersonalMemoryPatchEvidenceReference)
            for item in references
        ):
            raise ContractValidationError("ordered_evidence_references are invalid")
        ordered = tuple(sorted(references, key=lambda item: item.evidence_link_hash))
        if references != ordered or len({item.evidence_link_hash for item in references}) != len(references):
            raise ContractValidationError("evidence references must be unique and ordered")
        object.__setattr__(self, "ordered_evidence_references", references)
        if not isinstance(self.evidence_status, EvidenceStatus):
            raise ContractValidationError("evidence_status is invalid")
        object.__setattr__(
            self,
            "conflict_group_hashes",
            _hash_tuple(self.conflict_group_hashes, "conflict_group_hashes", 64),
        )
        object.__setattr__(
            self,
            "prohibited_claim_hashes",
            _hash_tuple(self.prohibited_claim_hashes, "prohibited_claim_hashes", 256),
        )
        object.__setattr__(self, "limitations", _limitations(self.limitations))
        object.__setattr__(self, "reason_codes", _reason_tuple(self.reason_codes))
        if Step29ReasonCode.EVIDENCE_BOUND not in self.reason_codes:
            raise ContractValidationError("evidence-bound reason is missing")
        object.__setattr__(self, "bound_at", ensure_utc(self.bound_at))
        object.__setattr__(
            self,
            "binding_hash",
            canonical_sha256(self, exclude_fields=("binding_hash",)),
        )


@dataclass(frozen=True, slots=True)
class PersonalMemoryPatchValidationReceipt:
    schema_version: str
    proposal_id: str
    proposal_hash: str
    evidence_binding_hash: str
    dedup_result: ProposalDedupResult
    conflict_result: ProposalConflictResult
    freshness_result: ProposalFreshnessResult
    temporal_result: ProposalGateResult
    owner_scope_result: ProposalGateResult
    slot_state_result: ProposalGateResult
    quota_result: ProposalGateResult
    model_binding_result: ProposalGateResult
    validation_policy_id: str
    validation_policy_version: str
    validation_policy_digest: str
    reason_codes: tuple[Step29ReasonCode, ...]
    validated: bool
    validated_at: datetime
    receipt_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != STEP29_SCHEMA_VERSION:
            raise ContractValidationError("unsupported validation-receipt schema")
        _logical_id(self.proposal_id, "proposal_id")
        for value, name in (
            (self.proposal_hash, "proposal_hash"),
            (self.evidence_binding_hash, "evidence_binding_hash"),
            (self.validation_policy_digest, "validation_policy_digest"),
        ):
            require_sha256_hex(value, name)
        if not isinstance(self.dedup_result, ProposalDedupResult):
            raise ContractValidationError("dedup_result is invalid")
        if not isinstance(self.conflict_result, ProposalConflictResult):
            raise ContractValidationError("conflict_result is invalid")
        if not isinstance(self.freshness_result, ProposalFreshnessResult):
            raise ContractValidationError("freshness_result is invalid")
        for name in (
            "temporal_result",
            "owner_scope_result",
            "slot_state_result",
            "quota_result",
            "model_binding_result",
        ):
            if not isinstance(getattr(self, name), ProposalGateResult):
                raise ContractValidationError(f"{name} is invalid")
        _logical_id(self.validation_policy_id, "validation_policy_id")
        _logical_id(self.validation_policy_version, "validation_policy_version")
        policy = load_personal_memory_patch_validation_policy()
        if (
            self.validation_policy_id != policy.policy_id
            or self.validation_policy_version != policy.policy_version
            or self.validation_policy_digest != policy.policy_digest
        ):
            raise ContractValidationError("validation receipt policy differs")
        object.__setattr__(self, "reason_codes", _reason_tuple(self.reason_codes))
        if not isinstance(self.validated, bool):
            raise ContractValidationError("validated must be boolean")
        expected = (
            self.dedup_result is ProposalDedupResult.PASS
            and self.conflict_result is ProposalConflictResult.PASS
            and self.freshness_result is ProposalFreshnessResult.FRESH
            and all(
                getattr(self, name) is ProposalGateResult.PASS
                for name in (
                    "temporal_result",
                    "owner_scope_result",
                    "slot_state_result",
                    "quota_result",
                    "model_binding_result",
                )
            )
        )
        if self.validated is not expected:
            raise ContractValidationError("validated differs from fail-closed gates")
        required_reason = (
            Step29ReasonCode.PROPOSAL_VALIDATED
            if expected
            else Step29ReasonCode.PROPOSAL_VALIDATION_FAILED
        )
        if required_reason not in self.reason_codes:
            raise ContractValidationError("validation outcome reason is missing")
        object.__setattr__(self, "validated_at", ensure_utc(self.validated_at))
        object.__setattr__(
            self,
            "receipt_hash",
            canonical_sha256(self, exclude_fields=("receipt_hash",)),
        )


@dataclass(frozen=True, slots=True)
class PersonalMemoryPatchProposalState:
    schema_version: str
    contract_type: str
    proposal: PersonalMemoryPatchProposal
    state: PatchState
    state_version: int
    evidence_binding: PersonalMemoryPatchEvidenceBinding | None
    validation_receipt: PersonalMemoryPatchValidationReceipt | None
    updated_at: datetime
    state_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != STEP29_SCHEMA_VERSION:
            raise ContractValidationError("unsupported proposal-state schema")
        if self.contract_type != PERSONAL_MEMORY_PATCH_STATE_CONTRACT_TYPE:
            raise ContractValidationError("proposal-state contract type is invalid")
        if not isinstance(self.proposal, PersonalMemoryPatchProposal):
            raise ContractValidationError("proposal must be typed")
        if self.state not in STEP29_STATES:
            raise ContractValidationError("state is outside Step 29")
        if self.state_version != STEP29_STATE_VERSIONS[self.state]:
            raise ContractValidationError("state_version differs from Step 29 state")
        if self.state is PatchState.PROPOSED:
            if self.evidence_binding is not None or self.validation_receipt is not None:
                raise ContractValidationError("PROPOSED cannot carry validation state")
        elif self.state is PatchState.EVIDENCE_BOUND:
            if self.evidence_binding is None or self.validation_receipt is not None:
                raise ContractValidationError("EVIDENCE_BOUND payload is invalid")
        else:
            if self.evidence_binding is None or self.validation_receipt is None:
                raise ContractValidationError("validated states require exact receipts")
            if not self.validation_receipt.validated:
                raise ContractValidationError("failed validation cannot advance state")
        if self.evidence_binding is not None:
            if (
                self.evidence_binding.proposal_id != self.proposal.proposal_id
                or self.evidence_binding.proposal_hash != self.proposal.proposal_hash
                or self.evidence_binding.candidate_hash != self.proposal.candidate_hash
            ):
                raise ContractValidationError("evidence binding is detached")
        if self.validation_receipt is not None:
            if (
                self.validation_receipt.proposal_id != self.proposal.proposal_id
                or self.validation_receipt.proposal_hash != self.proposal.proposal_hash
                or self.validation_receipt.evidence_binding_hash
                != self.evidence_binding.binding_hash
            ):
                raise ContractValidationError("validation receipt is detached")
        updated = ensure_utc(self.updated_at)
        if updated < self.proposal.created_at:
            raise ContractValidationError("proposal state predates proposal")
        object.__setattr__(self, "updated_at", updated)
        object.__setattr__(
            self,
            "state_hash",
            canonical_sha256(self, exclude_fields=("state_hash",)),
        )
        if len(canonical_json_bytes(self)) > MAXIMUM_PROPOSAL_RECORD_BYTES:
            raise ContractValidationError("proposal state exceeds canonical byte bound")


@dataclass(frozen=True, slots=True)
class PersonalMemoryPatchTransitionReceipt:
    schema_version: str
    proposal_id: str
    proposal_hash: str
    command_hash: str
    state_before: PatchState
    state_after: PatchState
    state_version: int
    state_hash: str
    transition_id: str
    result_digest: str
    idempotent_replay: bool
    reason_code: Step29ReasonCode
    completed_at: datetime
    receipt_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != STEP29_SCHEMA_VERSION:
            raise ContractValidationError("unsupported transition-receipt schema")
        for value, name in (
            (self.proposal_id, "proposal_id"),
            (self.transition_id, "transition_id"),
        ):
            _logical_id(value, name)
        for value, name in (
            (self.proposal_hash, "proposal_hash"),
            (self.command_hash, "command_hash"),
            (self.state_hash, "state_hash"),
            (self.result_digest, "result_digest"),
        ):
            require_sha256_hex(value, name)
        if self.state_after not in STEP29_STATES:
            raise ContractValidationError("receipt target is outside Step 29")
        if self.state_before is PatchState.DETECTED:
            if self.state_after is not PatchState.PROPOSED:
                raise ContractValidationError("DETECTED receipt edge is invalid")
        elif STEP29_TRANSITIONS.get(self.state_before) is not self.state_after:
            raise ContractValidationError("receipt transition edge is invalid")
        if self.state_version != STEP29_STATE_VERSIONS[self.state_after]:
            raise ContractValidationError("receipt state version is invalid")
        if not isinstance(self.idempotent_replay, bool):
            raise ContractValidationError("idempotent_replay must be boolean")
        if not isinstance(self.reason_code, Step29ReasonCode):
            raise ContractValidationError("receipt reason_code is invalid")
        expected_reason = {
            PatchState.PROPOSED: Step29ReasonCode.PROPOSAL_CREATED,
            PatchState.EVIDENCE_BOUND: Step29ReasonCode.EVIDENCE_BOUND,
            PatchState.VALIDATED: Step29ReasonCode.PROPOSAL_VALIDATED,
            PatchState.AWAITING_APPROVAL: Step29ReasonCode.AWAITING_APPROVAL_READY,
        }[self.state_after]
        if self.idempotent_replay:
            expected_reason = Step29ReasonCode.PROPOSAL_EXACT_REPLAY
        if self.reason_code is not expected_reason:
            raise ContractValidationError("transition receipt reason is inconsistent")
        object.__setattr__(self, "completed_at", ensure_utc(self.completed_at))
        object.__setattr__(
            self,
            "receipt_hash",
            canonical_sha256(self, exclude_fields=("receipt_hash",)),
        )


def build_personal_memory_patch_transition_receipt(
    state: PersonalMemoryPatchProposalState,
    *,
    command_hash: str,
    state_before: PatchState,
    transition_id: str,
    result_digest: str,
    idempotent_replay: bool,
    completed_at: datetime,
) -> PersonalMemoryPatchTransitionReceipt:
    verify_personal_memory_patch_state(state)
    reason = {
        PatchState.PROPOSED: Step29ReasonCode.PROPOSAL_CREATED,
        PatchState.EVIDENCE_BOUND: Step29ReasonCode.EVIDENCE_BOUND,
        PatchState.VALIDATED: Step29ReasonCode.PROPOSAL_VALIDATED,
        PatchState.AWAITING_APPROVAL: Step29ReasonCode.AWAITING_APPROVAL_READY,
    }[state.state]
    if idempotent_replay:
        reason = Step29ReasonCode.PROPOSAL_EXACT_REPLAY
    return PersonalMemoryPatchTransitionReceipt(
        schema_version=STEP29_SCHEMA_VERSION,
        proposal_id=state.proposal.proposal_id,
        proposal_hash=state.proposal.proposal_hash,
        command_hash=command_hash,
        state_before=state_before,
        state_after=state.state,
        state_version=state.state_version,
        state_hash=state.state_hash,
        transition_id=transition_id,
        result_digest=result_digest,
        idempotent_replay=idempotent_replay,
        reason_code=reason,
        completed_at=completed_at,
    )


def build_personal_memory_patch_proposal(
    envelope: CorrectionCandidateEnvelope,
    command: CreatePersonalMemoryPatchProposal,
) -> PersonalMemoryPatchProposalState:
    verify_correction_candidate_envelope(envelope)
    if not isinstance(command, CreatePersonalMemoryPatchProposal):
        raise ContractValidationError("command must be CreatePersonalMemoryPatchProposal")
    candidate = envelope.submission.candidate
    target = envelope.submission.target_slot_binding
    lineage = envelope.submission.lineage
    if (
        command.tenant_id != candidate.tenant_id
        or command.owner_user_id != candidate.user_id
        or command.personal_memory_space_id != candidate.personal_memory_space_id
        or command.candidate_id != envelope.candidate_id
        or command.candidate_envelope_hash != envelope.envelope_hash
        or command.expected_target_binding_hash != target.target_binding_hash
    ):
        raise PersonalMemoryPatchValidationError(
            Step29ReasonCode.OWNER_SCOPE_MISMATCH
        )
    if command.requested_at < envelope.submission.submitted_at:
        raise ContractValidationError("proposal creation predates candidate intake")
    proposal = PersonalMemoryPatchProposal(
        schema_version=STEP29_SCHEMA_VERSION,
        contract_type=PERSONAL_MEMORY_PATCH_PROPOSAL_CONTRACT_TYPE,
        tenant_id=candidate.tenant_id,
        owner_user_id=candidate.user_id,
        personal_memory_space_id=candidate.personal_memory_space_id,
        hat_scope_id=target.hat_scope_id,
        target_slot_binding=target,
        target_binding_hash=target.target_binding_hash,
        model_binding_id=target.model_binding_id,
        model_binding_hash=target.model_binding_hash,
        candidate_id=envelope.candidate_id,
        candidate_hash=candidate.content_hash,
        candidate_envelope_hash=envelope.envelope_hash,
        candidate_claim_ids=tuple(sorted(item.claim_id for item in candidate.detected_claims)),
        candidate_evidence_reference_hashes=tuple(
            sorted(candidate.available_evidence_references)
        ),
        proposal_statement=candidate.proposed_correction,
        proposal_scope=lineage.effective_scope,
        proposal_kind=PersonalMemoryPatchProposalKind.FACT_CORRECTION,
        content_kind=MemoryContentKind.FACTUAL,
        origin=_origin_for(candidate.source_component),
        route_hash=lineage.route_hash,
        source_result_hash=lineage.result_hash,
        original_query_digest=lineage.original_query_digest,
        draft_v1_hash=lineage.draft_v1_hash,
        draft_v2_hash=lineage.draft_v2_hash,
        correction_packet_hash=lineage.correction_packet_hash,
        verification_summary_hash=lineage.verification_summary_hash,
        verified_answer_hash=lineage.verified_answer_hash,
        created_at=command.requested_at,
        reason_codes=(Step29ReasonCode.PROPOSAL_CREATED,),
    )
    return PersonalMemoryPatchProposalState(
        schema_version=STEP29_SCHEMA_VERSION,
        contract_type=PERSONAL_MEMORY_PATCH_STATE_CONTRACT_TYPE,
        proposal=proposal,
        state=PatchState.PROPOSED,
        state_version=1,
        evidence_binding=None,
        validation_receipt=None,
        updated_at=command.requested_at,
    )


def _bundle_item_map(
    bundles: Sequence[FrozenEvidenceBundle],
) -> dict[str, EvidenceBundleItem]:
    result: dict[str, EvidenceBundleItem] = {}
    for bundle in bundles:
        verify_evidence_bundle_hash(bundle)
        for item in bundle.ordered_items:
            verify_bundle_item_hash(item)
            if item.item_hash in result:
                raise ContractValidationError("evidence item hash is duplicated")
            result[item.item_hash] = item
    return result


def build_personal_memory_patch_evidence_binding(
    state: PersonalMemoryPatchProposalState,
    *,
    bundles: Sequence[FrozenEvidenceBundle],
    temporal_result: TemporalResolutionResult,
    claim_links: Sequence[ClaimEvidenceLink],
    claim_assessments: Sequence[ClaimEvidenceAssessment],
    correction_packet: CorrectionPacketV1A,
    verified_answer: VerifiedAnswer,
    bound_at: datetime,
) -> PersonalMemoryPatchEvidenceBinding:
    verify_personal_memory_patch_state(state)
    if state.state is not PatchState.PROPOSED:
        raise PersonalMemoryPatchValidationError(
            Step29ReasonCode.STATE_TRANSITION_INVALID
        )
    proposal = state.proposal
    verify_temporal_result_hash(temporal_result)
    verify_correction_packet_hash(correction_packet)
    verify_verified_answer_hash(verified_answer)
    bundle_values = tuple(bundles)
    if not bundle_values or len(bundle_values) > 2:
        raise PersonalMemoryPatchValidationError(Step29ReasonCode.EVIDENCE_INVALID)
    item_map = _bundle_item_map(bundle_values)
    bundle_hashes = tuple(sorted(bundle.bundle_hash for bundle in bundle_values))
    links = tuple(sorted(claim_links, key=lambda item: item.link_hash))
    assessments = tuple(sorted(claim_assessments, key=lambda item: item.claim_id))
    if not links or not assessments:
        raise PersonalMemoryPatchValidationError(Step29ReasonCode.EVIDENCE_INSUFFICIENT)
    for link in links:
        verify_claim_evidence_link_hash(link)
    for assessment in assessments:
        verify_claim_assessment_hash(assessment)
    identity = (
        temporal_result.tenant_id,
        temporal_result.user_id,
        temporal_result.route_hash,
        temporal_result.effective_scope,
    )
    if identity != (
        proposal.tenant_id,
        proposal.owner_user_id,
        proposal.route_hash,
        proposal.proposal_scope,
    ):
        raise PersonalMemoryPatchValidationError(
            Step29ReasonCode.OWNER_SCOPE_MISMATCH
        )
    if any(
        (
            bundle.tenant_id,
            bundle.user_id,
            bundle.route_hash,
            bundle.effective_scope,
        )
        != identity
        for bundle in bundle_values
    ):
        raise PersonalMemoryPatchValidationError(
            Step29ReasonCode.OWNER_SCOPE_MISMATCH
        )
    if (
        correction_packet.tenant_id != proposal.tenant_id
        or correction_packet.user_id != proposal.owner_user_id
        or correction_packet.route_hash != proposal.route_hash
        or correction_packet.effective_scope != proposal.proposal_scope
        or correction_packet.step21_resolution_hash != temporal_result.result_hash
        or tuple(sorted(correction_packet.step20_evidence_bundle_hashes))
        != bundle_hashes
        or correction_packet.packet_hash != proposal.correction_packet_hash
    ):
        raise PersonalMemoryPatchValidationError(Step29ReasonCode.EVIDENCE_INVALID)
    if (
        verified_answer.tenant_id != proposal.tenant_id
        or verified_answer.user_id != proposal.owner_user_id
        or verified_answer.route_hash != proposal.route_hash
        or verified_answer.effective_scope != proposal.proposal_scope
        or verified_answer.evidence_bundle_hash not in bundle_hashes
        or verified_answer.temporal_resolution_hash != temporal_result.result_hash
        or verified_answer.correction_packet_hash != correction_packet.packet_hash
        or verified_answer.answer_hash != proposal.verified_answer_hash
    ):
        raise PersonalMemoryPatchValidationError(Step29ReasonCode.EVIDENCE_INVALID)
    claim_ids = set(proposal.candidate_claim_ids)
    if any(link.claim_id not in claim_ids for link in links):
        raise PersonalMemoryPatchValidationError(Step29ReasonCode.EVIDENCE_INVALID)
    if tuple(sorted(link.link_hash for link in links)) != (
        proposal.candidate_evidence_reference_hashes
    ):
        raise PersonalMemoryPatchValidationError(Step29ReasonCode.EVIDENCE_INVALID)
    assessment_map = {item.claim_id: item for item in assessments}
    packet_claims = {item.claim_id: item for item in correction_packet.ordered_claims}
    matching_claim_ids = {
        claim_id
        for claim_id in claim_ids
        if claim_id in packet_claims
        and normalize_proposal_statement(packet_claims[claim_id].exact_claim_text)
        == proposal.normalized_statement
    }
    if not matching_claim_ids:
        raise PersonalMemoryPatchValidationError(Step29ReasonCode.EVIDENCE_INVALID)
    references: list[PersonalMemoryPatchEvidenceReference] = []
    for link in links:
        assessment = assessment_map.get(link.claim_id)
        item = item_map.get(link.step20_item_hash)
        if (
            assessment is None
            or assessment.candidate_status is not ClaimEvidenceCandidateStatus.SUPPORTED
            or link.link_hash not in assessment.supporting_link_hashes
            or link.claim_id not in matching_claim_ids
            or item is None
            or item.identity.identity_hash != link.candidate_identity_hash
            or item.identity.content_sha256 != link.content_sha256
            or item.identity.source_id != link.source_id
            or item.identity.knowledge_version_id != link.knowledge_version_id
            or item.identity.chunk_id != link.chunk_id
            or link.step20_item_hash not in temporal_result.resolved_item_hashes
            or link.relation is not ClaimEvidenceRelation.SUPPORTS
        ):
            raise PersonalMemoryPatchValidationError(Step29ReasonCode.EVIDENCE_INVALID)
        references.append(
            PersonalMemoryPatchEvidenceReference(
                claim_id=link.claim_id,
                evidence_link_hash=link.link_hash,
                step20_bundle_hash=link.step20_bundle_hash,
                step20_item_hash=link.step20_item_hash,
                candidate_identity_hash=link.candidate_identity_hash,
                evidence_id=link.evidence_id,
                source_id=link.source_id,
                knowledge_version_id=link.knowledge_version_id,
                chunk_id=link.chunk_id,
                content_sha256=link.content_sha256,
                authority_level=link.authority_level,
                publication_state=link.publication_state,
                temporal_assessment_hash=link.temporal_assessment_hash,
                temporal_applicability=link.temporal_applicability,
                freshness_status=link.freshness_status,
                conflict_group_id=link.conflict_group_id,
                relation=link.relation,
            )
        )
    normalized = proposal.normalized_statement
    prohibited = tuple(
        sorted(
            item.prohibition_hash
            for item in correction_packet.ordered_prohibited_claims
            if normalize_proposal_statement(item.exact_or_normalized_prohibited_content)
            == normalized
        )
    )
    return PersonalMemoryPatchEvidenceBinding(
        schema_version=STEP29_SCHEMA_VERSION,
        proposal_id=proposal.proposal_id,
        proposal_hash=proposal.proposal_hash,
        candidate_hash=proposal.candidate_hash,
        tenant_id=proposal.tenant_id,
        owner_user_id=proposal.owner_user_id,
        personal_memory_space_id=proposal.personal_memory_space_id,
        route_hash=proposal.route_hash,
        effective_scope=proposal.proposal_scope,
        evidence_bundle_hashes=bundle_hashes,
        temporal_resolution_hash=temporal_result.result_hash,
        claim_assessment_hashes=tuple(
            sorted(item.assessment_hash for item in assessments)
        ),
        correction_packet_hash=correction_packet.packet_hash,
        verified_answer_hash=verified_answer.answer_hash,
        ordered_evidence_references=tuple(
            sorted(references, key=lambda item: item.evidence_link_hash)
        ),
        evidence_status=temporal_result.evidence_status,
        conflict_group_hashes=tuple(
            sorted(item.conflict_group_hash for item in temporal_result.conflict_groups)
        ),
        prohibited_claim_hashes=prohibited,
        limitations=tuple(sorted(set(temporal_result.limitations))),
        reason_codes=(Step29ReasonCode.EVIDENCE_BOUND,),
        bound_at=bound_at,
    )


def bind_personal_memory_patch_evidence(
    state: PersonalMemoryPatchProposalState,
    binding: PersonalMemoryPatchEvidenceBinding,
    *,
    transitioned_at: datetime,
) -> PersonalMemoryPatchProposalState:
    verify_personal_memory_patch_state(state)
    verify_personal_memory_patch_evidence_binding(binding)
    if state.state is not PatchState.PROPOSED:
        raise PersonalMemoryPatchValidationError(Step29ReasonCode.STATE_TRANSITION_INVALID)
    if binding.proposal_hash != state.proposal.proposal_hash:
        raise PersonalMemoryPatchValidationError(Step29ReasonCode.EVIDENCE_INVALID)
    return PersonalMemoryPatchProposalState(
        schema_version=STEP29_SCHEMA_VERSION,
        contract_type=PERSONAL_MEMORY_PATCH_STATE_CONTRACT_TYPE,
        proposal=state.proposal,
        state=PatchState.EVIDENCE_BOUND,
        state_version=2,
        evidence_binding=binding,
        validation_receipt=None,
        updated_at=transitioned_at,
    )


def freshness_result_for(
    binding: PersonalMemoryPatchEvidenceBinding,
    *,
    temporal_trusted_now: datetime,
    validated_at: datetime,
    policy: PersonalMemoryPatchValidationPolicy,
) -> ProposalFreshnessResult:
    validated = ensure_utc(validated_at)
    temporal_now = ensure_utc(temporal_trusted_now)
    age = (validated - temporal_now).total_seconds()
    if age < 0 or age > policy.maximum_temporal_revalidation_age_seconds:
        return ProposalFreshnessResult.STALE
    status = binding.evidence_status
    if status is EvidenceStatus.STALE:
        return ProposalFreshnessResult.STALE
    if status is EvidenceStatus.INSUFFICIENT:
        return ProposalFreshnessResult.INSUFFICIENT
    if status is EvidenceStatus.UNAVAILABLE:
        return ProposalFreshnessResult.UNAVAILABLE
    if status is EvidenceStatus.CONFLICTING:
        return ProposalFreshnessResult.CONFLICTING
    if status is not EvidenceStatus.SUFFICIENT:
        return ProposalFreshnessResult.INVALID
    if any(
        item.freshness_status is not FreshnessStatus.FRESH
        for item in binding.ordered_evidence_references
    ):
        return ProposalFreshnessResult.STALE
    return ProposalFreshnessResult.FRESH


def build_personal_memory_patch_validation_receipt(
    state: PersonalMemoryPatchProposalState,
    *,
    dedup_result: ProposalDedupResult,
    conflict_result: ProposalConflictResult,
    temporal_trusted_now: datetime,
    owner_scope_result: ProposalGateResult,
    slot_state_result: ProposalGateResult,
    quota_result: ProposalGateResult,
    model_binding_result: ProposalGateResult,
    validated_at: datetime,
    policy: PersonalMemoryPatchValidationPolicy | None = None,
) -> PersonalMemoryPatchValidationReceipt:
    verify_personal_memory_patch_state(state)
    if state.state is not PatchState.EVIDENCE_BOUND or state.evidence_binding is None:
        raise PersonalMemoryPatchValidationError(Step29ReasonCode.STATE_TRANSITION_INVALID)
    selected_policy = policy or load_personal_memory_patch_validation_policy()
    verify_personal_memory_patch_validation_policy(selected_policy)
    binding = state.evidence_binding
    freshness = freshness_result_for(
        binding,
        temporal_trusted_now=temporal_trusted_now,
        validated_at=validated_at,
        policy=selected_policy,
    )
    temporal = (
        ProposalGateResult.PASS
        if not binding.conflict_group_hashes
        and all(
            item.temporal_applicability is TemporalApplicability.APPLICABLE
            for item in binding.ordered_evidence_references
        )
        else ProposalGateResult.FAIL
    )
    reasons = {
        Step29ReasonCode.PROPOSAL_VALIDATION_FAILED,
    }
    if dedup_result is ProposalDedupResult.PASS:
        reasons.add(Step29ReasonCode.DEDUP_PASS)
    else:
        reasons.add(Step29ReasonCode.DEDUP_FAIL)
    if conflict_result is ProposalConflictResult.PASS:
        reasons.add(Step29ReasonCode.CONFLICT_CHECK_PASS)
    else:
        reasons.add(Step29ReasonCode.CONFLICT_CHECK_FAIL)
    if freshness is ProposalFreshnessResult.FRESH:
        reasons.add(Step29ReasonCode.FRESHNESS_PASS)
    else:
        reasons.add(Step29ReasonCode.FRESHNESS_FAIL)
    reasons.add(
        Step29ReasonCode.TEMPORAL_PASS
        if temporal is ProposalGateResult.PASS
        else Step29ReasonCode.TEMPORAL_FAIL
    )
    reasons.add(
        Step29ReasonCode.OWNER_SCOPE_VALID
        if owner_scope_result is ProposalGateResult.PASS
        else Step29ReasonCode.OWNER_SCOPE_MISMATCH
    )
    reasons.add(
        Step29ReasonCode.TARGET_SLOT_VALID
        if slot_state_result is ProposalGateResult.PASS
        else Step29ReasonCode.TARGET_SLOT_INVALID
    )
    reasons.add(
        Step29ReasonCode.QUOTA_PASS
        if quota_result is ProposalGateResult.PASS
        else Step29ReasonCode.QUOTA_FAIL
    )
    reasons.add(
        Step29ReasonCode.MODEL_BINDING_PASS
        if model_binding_result is ProposalGateResult.PASS
        else Step29ReasonCode.MODEL_BINDING_FAIL
    )
    validated = (
        dedup_result is ProposalDedupResult.PASS
        and conflict_result is ProposalConflictResult.PASS
        and freshness is ProposalFreshnessResult.FRESH
        and temporal is ProposalGateResult.PASS
        and owner_scope_result is ProposalGateResult.PASS
        and slot_state_result is ProposalGateResult.PASS
        and quota_result is ProposalGateResult.PASS
        and model_binding_result is ProposalGateResult.PASS
    )
    if validated:
        reasons.remove(Step29ReasonCode.PROPOSAL_VALIDATION_FAILED)
        reasons.add(Step29ReasonCode.PROPOSAL_VALIDATED)
    return PersonalMemoryPatchValidationReceipt(
        schema_version=STEP29_SCHEMA_VERSION,
        proposal_id=state.proposal.proposal_id,
        proposal_hash=state.proposal.proposal_hash,
        evidence_binding_hash=binding.binding_hash,
        dedup_result=dedup_result,
        conflict_result=conflict_result,
        freshness_result=freshness,
        temporal_result=temporal,
        owner_scope_result=owner_scope_result,
        slot_state_result=slot_state_result,
        quota_result=quota_result,
        model_binding_result=model_binding_result,
        validation_policy_id=selected_policy.policy_id,
        validation_policy_version=selected_policy.policy_version,
        validation_policy_digest=selected_policy.policy_digest,
        reason_codes=tuple(sorted(reasons, key=lambda item: item.value)),
        validated=validated,
        validated_at=validated_at,
    )


def validate_personal_memory_patch(
    state: PersonalMemoryPatchProposalState,
    receipt: PersonalMemoryPatchValidationReceipt,
    *,
    transitioned_at: datetime,
) -> PersonalMemoryPatchProposalState:
    verify_personal_memory_patch_state(state)
    verify_personal_memory_patch_validation_receipt(receipt)
    if state.state is not PatchState.EVIDENCE_BOUND or not receipt.validated:
        raise PersonalMemoryPatchValidationError(
            Step29ReasonCode.PROPOSAL_VALIDATION_FAILED
        )
    if (
        receipt.proposal_hash != state.proposal.proposal_hash
        or receipt.evidence_binding_hash != state.evidence_binding.binding_hash
    ):
        raise PersonalMemoryPatchValidationError(Step29ReasonCode.EVIDENCE_INVALID)
    return PersonalMemoryPatchProposalState(
        schema_version=STEP29_SCHEMA_VERSION,
        contract_type=PERSONAL_MEMORY_PATCH_STATE_CONTRACT_TYPE,
        proposal=state.proposal,
        state=PatchState.VALIDATED,
        state_version=3,
        evidence_binding=state.evidence_binding,
        validation_receipt=receipt,
        updated_at=transitioned_at,
    )


def advance_personal_memory_patch_to_awaiting_approval(
    state: PersonalMemoryPatchProposalState,
    *,
    validation_receipt_hash: str,
    transitioned_at: datetime,
) -> PersonalMemoryPatchProposalState:
    verify_personal_memory_patch_state(state)
    require_sha256_hex(validation_receipt_hash, "validation_receipt_hash")
    if (
        state.state is not PatchState.VALIDATED
        or state.validation_receipt is None
        or not state.validation_receipt.validated
        or state.validation_receipt.receipt_hash != validation_receipt_hash
    ):
        raise PersonalMemoryPatchValidationError(
            Step29ReasonCode.STATE_TRANSITION_INVALID
        )
    return PersonalMemoryPatchProposalState(
        schema_version=STEP29_SCHEMA_VERSION,
        contract_type=PERSONAL_MEMORY_PATCH_STATE_CONTRACT_TYPE,
        proposal=state.proposal,
        state=PatchState.AWAITING_APPROVAL,
        state_version=4,
        evidence_binding=state.evidence_binding,
        validation_receipt=state.validation_receipt,
        updated_at=transitioned_at,
    )


def verify_personal_memory_patch_validation_policy(
    value: PersonalMemoryPatchValidationPolicy,
) -> None:
    verify_canonical_hash(value, value.policy_digest, exclude_fields=("policy_digest",))
    _reconstruct(value, "Step 29 validation policy")


def verify_personal_memory_patch_proposal(value: PersonalMemoryPatchProposal) -> None:
    verify_correction_candidate_target_binding(value.target_slot_binding)
    verify_canonical_hash(value, value.proposal_hash, exclude_fields=("proposal_hash",))
    _reconstruct(value, "Personal Memory Patch proposal")


def verify_personal_memory_patch_evidence_reference(
    value: PersonalMemoryPatchEvidenceReference,
) -> None:
    verify_canonical_hash(value, value.reference_hash, exclude_fields=("reference_hash",))
    _reconstruct(value, "Personal Memory Patch evidence reference")


def verify_personal_memory_patch_evidence_binding(
    value: PersonalMemoryPatchEvidenceBinding,
) -> None:
    verify_canonical_hash(value, value.binding_hash, exclude_fields=("binding_hash",))
    for item in value.ordered_evidence_references:
        verify_personal_memory_patch_evidence_reference(item)
    _reconstruct(value, "Personal Memory Patch evidence binding")


def verify_personal_memory_patch_validation_receipt(
    value: PersonalMemoryPatchValidationReceipt,
) -> None:
    verify_canonical_hash(value, value.receipt_hash, exclude_fields=("receipt_hash",))
    _reconstruct(value, "Personal Memory Patch validation receipt")


def verify_personal_memory_patch_state(value: PersonalMemoryPatchProposalState) -> None:
    verify_personal_memory_patch_proposal(value.proposal)
    if value.evidence_binding is not None:
        verify_personal_memory_patch_evidence_binding(value.evidence_binding)
    if value.validation_receipt is not None:
        verify_personal_memory_patch_validation_receipt(value.validation_receipt)
    verify_canonical_hash(value, value.state_hash, exclude_fields=("state_hash",))
    _reconstruct(value, "Personal Memory Patch proposal state")


def verify_personal_memory_patch_transition_receipt(
    value: PersonalMemoryPatchTransitionReceipt,
) -> None:
    verify_canonical_hash(value, value.receipt_hash, exclude_fields=("receipt_hash",))
    _reconstruct(value, "Personal Memory Patch transition receipt")


def personal_memory_patch_state_to_jsonb(
    value: PersonalMemoryPatchProposalState,
) -> dict[str, Any]:
    verify_personal_memory_patch_state(value)
    result = to_canonical_data(value)
    if not isinstance(result, dict):
        raise ContractValidationError("proposal state did not serialize as JSON object")
    return result


def _mapping(value: object, names: frozenset[str], context: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or frozenset(value) != names:
        raise ContractValidationError(f"{context} has missing or unknown fields")
    return value


def _datetime(value: object, name: str) -> datetime:
    if not isinstance(value, str):
        raise ContractValidationError(f"{name} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContractValidationError(f"{name} is invalid") from exc
    return ensure_utc(parsed, name)


def _scope_from_json(value: object) -> tuple[ScopeDimension, ...]:
    if not isinstance(value, list):
        raise ContractValidationError("scope must be an array")
    result = []
    for item in value:
        data = _mapping(
            item,
            frozenset(ScopeDimension.__dataclass_fields__),
            "scope dimension",
        )
        result.append(
            ScopeDimension(
                name=data["name"],
                value=data["value"],
                value_type=ScopeValueType(data["value_type"]),
                comparison_mode=ScopeComparisonMode(data["comparison_mode"]),
                source=data["source"],
                required=data["required"],
            )
        )
    return tuple(result)


def _proposal_from_json(value: object) -> PersonalMemoryPatchProposal:
    names = frozenset(PersonalMemoryPatchProposal.__dataclass_fields__)
    data = _mapping(value, names, "proposal")
    result = PersonalMemoryPatchProposal(
        schema_version=data["schema_version"],
        contract_type=data["contract_type"],
        tenant_id=data["tenant_id"],
        owner_user_id=data["owner_user_id"],
        personal_memory_space_id=data["personal_memory_space_id"],
        hat_scope_id=data["hat_scope_id"],
        target_slot_binding=parse_correction_candidate_target_binding(
            data["target_slot_binding"]
        ),
        target_binding_hash=data["target_binding_hash"],
        model_binding_id=data["model_binding_id"],
        model_binding_hash=data["model_binding_hash"],
        candidate_id=data["candidate_id"],
        candidate_hash=data["candidate_hash"],
        candidate_envelope_hash=data["candidate_envelope_hash"],
        candidate_claim_ids=tuple(data["candidate_claim_ids"]),
        candidate_evidence_reference_hashes=tuple(
            data["candidate_evidence_reference_hashes"]
        ),
        proposal_statement=data["proposal_statement"],
        proposal_scope=_scope_from_json(data["proposal_scope"]),
        proposal_kind=PersonalMemoryPatchProposalKind(data["proposal_kind"]),
        content_kind=MemoryContentKind(data["content_kind"]),
        origin=ProposalOrigin(data["origin"]),
        route_hash=data["route_hash"],
        source_result_hash=data["source_result_hash"],
        original_query_digest=data["original_query_digest"],
        draft_v1_hash=data["draft_v1_hash"],
        draft_v2_hash=data["draft_v2_hash"],
        correction_packet_hash=data["correction_packet_hash"],
        verification_summary_hash=data["verification_summary_hash"],
        verified_answer_hash=data["verified_answer_hash"],
        created_at=_datetime(data["created_at"], "created_at"),
        reason_codes=tuple(Step29ReasonCode(item) for item in data["reason_codes"]),
    )
    for name in (
        "proposal_statement_sha256",
        "normalized_statement",
        "normalized_statement_sha256",
        "conflict_subject_sha256",
        "negative_polarity",
        "exact_dedup_key",
        "proposal_id",
        "proposal_hash",
    ):
        if getattr(result, name) != data[name]:
            raise IntegrityError(f"stored proposal {name} mismatch")
    return result


def _reference_from_json(value: object) -> PersonalMemoryPatchEvidenceReference:
    data = _mapping(
        value,
        frozenset(PersonalMemoryPatchEvidenceReference.__dataclass_fields__),
        "evidence reference",
    )
    result = PersonalMemoryPatchEvidenceReference(
        claim_id=data["claim_id"],
        evidence_link_hash=data["evidence_link_hash"],
        step20_bundle_hash=data["step20_bundle_hash"],
        step20_item_hash=data["step20_item_hash"],
        candidate_identity_hash=data["candidate_identity_hash"],
        evidence_id=data["evidence_id"],
        source_id=data["source_id"],
        knowledge_version_id=data["knowledge_version_id"],
        chunk_id=data["chunk_id"],
        content_sha256=data["content_sha256"],
        authority_level=SourceAuthorityLevel(data["authority_level"]),
        publication_state=SourcePublicationState(data["publication_state"]),
        temporal_assessment_hash=data["temporal_assessment_hash"],
        temporal_applicability=TemporalApplicability(data["temporal_applicability"]),
        freshness_status=FreshnessStatus(data["freshness_status"]),
        conflict_group_id=data["conflict_group_id"],
        relation=ClaimEvidenceRelation(data["relation"]),
    )
    if result.reference_hash != data["reference_hash"]:
        raise IntegrityError("stored evidence reference hash mismatch")
    return result


def _binding_from_json(value: object) -> PersonalMemoryPatchEvidenceBinding:
    data = _mapping(
        value,
        frozenset(PersonalMemoryPatchEvidenceBinding.__dataclass_fields__),
        "evidence binding",
    )
    result = PersonalMemoryPatchEvidenceBinding(
        schema_version=data["schema_version"],
        proposal_id=data["proposal_id"],
        proposal_hash=data["proposal_hash"],
        candidate_hash=data["candidate_hash"],
        tenant_id=data["tenant_id"],
        owner_user_id=data["owner_user_id"],
        personal_memory_space_id=data["personal_memory_space_id"],
        route_hash=data["route_hash"],
        effective_scope=_scope_from_json(data["effective_scope"]),
        evidence_bundle_hashes=tuple(data["evidence_bundle_hashes"]),
        temporal_resolution_hash=data["temporal_resolution_hash"],
        claim_assessment_hashes=tuple(data["claim_assessment_hashes"]),
        correction_packet_hash=data["correction_packet_hash"],
        verified_answer_hash=data["verified_answer_hash"],
        ordered_evidence_references=tuple(
            _reference_from_json(item) for item in data["ordered_evidence_references"]
        ),
        evidence_status=EvidenceStatus(data["evidence_status"]),
        conflict_group_hashes=tuple(data["conflict_group_hashes"]),
        prohibited_claim_hashes=tuple(data["prohibited_claim_hashes"]),
        limitations=tuple(data["limitations"]),
        reason_codes=tuple(Step29ReasonCode(item) for item in data["reason_codes"]),
        bound_at=_datetime(data["bound_at"], "bound_at"),
    )
    if result.binding_hash != data["binding_hash"]:
        raise IntegrityError("stored evidence binding hash mismatch")
    return result


def _receipt_from_json(value: object) -> PersonalMemoryPatchValidationReceipt:
    data = _mapping(
        value,
        frozenset(PersonalMemoryPatchValidationReceipt.__dataclass_fields__),
        "validation receipt",
    )
    result = PersonalMemoryPatchValidationReceipt(
        schema_version=data["schema_version"],
        proposal_id=data["proposal_id"],
        proposal_hash=data["proposal_hash"],
        evidence_binding_hash=data["evidence_binding_hash"],
        dedup_result=ProposalDedupResult(data["dedup_result"]),
        conflict_result=ProposalConflictResult(data["conflict_result"]),
        freshness_result=ProposalFreshnessResult(data["freshness_result"]),
        temporal_result=ProposalGateResult(data["temporal_result"]),
        owner_scope_result=ProposalGateResult(data["owner_scope_result"]),
        slot_state_result=ProposalGateResult(data["slot_state_result"]),
        quota_result=ProposalGateResult(data["quota_result"]),
        model_binding_result=ProposalGateResult(data["model_binding_result"]),
        validation_policy_id=data["validation_policy_id"],
        validation_policy_version=data["validation_policy_version"],
        validation_policy_digest=data["validation_policy_digest"],
        reason_codes=tuple(Step29ReasonCode(item) for item in data["reason_codes"]),
        validated=data["validated"],
        validated_at=_datetime(data["validated_at"], "validated_at"),
    )
    if result.receipt_hash != data["receipt_hash"]:
        raise IntegrityError("stored validation receipt hash mismatch")
    return result


def parse_personal_memory_patch_state(
    value: Mapping[str, Any],
) -> PersonalMemoryPatchProposalState:
    data = _mapping(
        value,
        frozenset(PersonalMemoryPatchProposalState.__dataclass_fields__),
        "proposal state",
    )
    try:
        result = PersonalMemoryPatchProposalState(
            schema_version=data["schema_version"],
            contract_type=data["contract_type"],
            proposal=_proposal_from_json(data["proposal"]),
            state=PatchState(data["state"]),
            state_version=data["state_version"],
            evidence_binding=(
                None
                if data["evidence_binding"] is None
                else _binding_from_json(data["evidence_binding"])
            ),
            validation_receipt=(
                None
                if data["validation_receipt"] is None
                else _receipt_from_json(data["validation_receipt"])
            ),
            updated_at=_datetime(data["updated_at"], "updated_at"),
        )
    except (TypeError, ValueError) as exc:
        raise ContractValidationError("stored proposal state enum is invalid") from exc
    if result.state_hash != data["state_hash"]:
        raise IntegrityError("stored proposal state hash mismatch")
    verify_personal_memory_patch_state(result)
    return result


def proposal_conflict_between(
    proposal: PersonalMemoryPatchProposal,
    other: PersonalMemoryPatchProposal,
) -> ProposalConflictResult:
    verify_personal_memory_patch_proposal(proposal)
    verify_personal_memory_patch_proposal(other)
    if (
        proposal.tenant_id != other.tenant_id
        or proposal.owner_user_id != other.owner_user_id
        or proposal.personal_memory_space_id != other.personal_memory_space_id
    ):
        return ProposalConflictResult.PASS
    if proposal.exact_dedup_key == other.exact_dedup_key:
        return ProposalConflictResult.EXISTING_PATCH_CONFLICT
    if (
        proposal.conflict_subject_sha256 == other.conflict_subject_sha256
        and proposal.negative_polarity is not other.negative_polarity
    ):
        return ProposalConflictResult.DIRECT_CONTRADICTION
    left = {item.name: to_canonical_data(item) for item in proposal.proposal_scope}
    right = {item.name: to_canonical_data(item) for item in other.proposal_scope}
    overlap = set(left) & set(right)
    if overlap and any(left[name] != right[name] for name in overlap):
        return ProposalConflictResult.SCOPE_OVERLAP_CONFLICT
    return ProposalConflictResult.PASS


__all__ = [
    "MAXIMUM_PROPOSALS_PER_SLOT",
    "MAXIMUM_PROPOSAL_BYTES_PER_SLOT",
    "PERSONAL_MEMORY_PATCH_PROPOSAL_CONTRACT_TYPE",
    "PERSONAL_MEMORY_PATCH_STATE_CONTRACT_TYPE",
    "PERSONAL_MEMORY_PATCH_VALIDATION_POLICY_ID",
    "PERSONAL_MEMORY_PATCH_VALIDATION_POLICY_VERSION",
    "STEP29_SCHEMA_VERSION",
    "STEP29_STATES",
    "STEP29_TRANSITIONS",
    "CreatePersonalMemoryPatchProposal",
    "BindPersonalMemoryPatchEvidence",
    "ValidatePersonalMemoryPatchProposal",
    "AdvancePersonalMemoryPatchToAwaitingApproval",
    "PersonalMemoryPatchEvidenceBinding",
    "PersonalMemoryPatchEvidenceReference",
    "PersonalMemoryPatchProposal",
    "PersonalMemoryPatchProposalKind",
    "PersonalMemoryPatchProposalState",
    "PersonalMemoryPatchValidationError",
    "PersonalMemoryPatchValidationPolicy",
    "PersonalMemoryPatchValidationReceipt",
    "PersonalMemoryPatchTransitionReceipt",
    "ProposalConflictResult",
    "ProposalDedupResult",
    "ProposalFreshnessResult",
    "ProposalGateResult",
    "Step29ReasonCode",
    "Step29TransitionCommand",
    "advance_personal_memory_patch_to_awaiting_approval",
    "bind_personal_memory_patch_evidence",
    "build_personal_memory_patch_evidence_binding",
    "build_personal_memory_patch_proposal",
    "build_personal_memory_patch_validation_receipt",
    "build_personal_memory_patch_transition_receipt",
    "freshness_result_for",
    "load_personal_memory_patch_validation_policy",
    "normalize_proposal_statement",
    "parse_personal_memory_patch_state",
    "personal_memory_patch_state_to_jsonb",
    "proposal_conflict_between",
    "proposal_conflict_subject",
    "validate_personal_memory_patch",
    "verify_personal_memory_patch_evidence_binding",
    "verify_personal_memory_patch_evidence_reference",
    "verify_personal_memory_patch_proposal",
    "verify_personal_memory_patch_state",
    "verify_personal_memory_patch_validation_policy",
    "verify_personal_memory_patch_validation_receipt",
    "verify_personal_memory_patch_transition_receipt",
]
