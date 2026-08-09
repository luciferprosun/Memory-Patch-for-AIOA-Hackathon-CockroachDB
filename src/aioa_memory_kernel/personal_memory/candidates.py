"""Immutable Step 28 correction-candidate intake contracts.

This module turns an existing proposal-only :class:`CorrectionCandidate` into
one owner-, run-, route-, slot-, and model-binding-bound envelope.  It contains
no persistence implementation and grants no proposal transition, approval,
commit, activation, retrieval, model, provider, network, or execution authority.

The public parsing helpers accept the same mapping shape that CockroachDB JSONB
returns.  Parsing is strict: missing and unknown fields fail closed, and every
stored canonical hash is compared with a reconstructed immutable value.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, field, fields as dataclass_fields
from datetime import datetime
from typing import Any

from aioa_memory_kernel.contracts.correction import (
    CorrectionCandidate,
    validate_correction_candidate_ownership,
    verify_correction_candidate_hash,
)
from aioa_memory_kernel.contracts.enums import (
    ActorType,
    AnswerStatus,
    CorrectionCandidateState,
    EvidenceStatus,
    KnowledgeRoute,
    PersonalMemorySpaceState,
    ScopeComparisonMode,
    ScopeValueType,
    StableStringEnum,
)
from aioa_memory_kernel.contracts.evidence import ClaimCandidate
from aioa_memory_kernel.contracts.exceptions import (
    ContractValidationError,
    IntegrityError,
)
from aioa_memory_kernel.contracts.identities import KernelRunIdentity
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
from aioa_memory_kernel.persistence.errors import PersistenceError

from .models import (
    PersonalMemoryBindingMode,
    PersonalMemoryHatSlot,
    PersonalMemoryModelBinding,
    verify_model_binding_hash,
    verify_slot_hash,
)


STEP28_SCHEMA_VERSION = "1.0.0"
CORRECTION_CANDIDATE_ENVELOPE_CONTRACT_TYPE = "CorrectionCandidateEnvelope"
CORRECTION_CANDIDATE_INTAKE_POLICY_ID = "correction-candidate-intake-1a"
CORRECTION_CANDIDATE_INTAKE_POLICY_VERSION = "1"
MAXIMUM_CANDIDATE_CLAIMS = 256
MAXIMUM_CANDIDATE_EVIDENCE_REFERENCES = 4096
MAXIMUM_CANDIDATE_REASON_CODES = 64
MAXIMUM_CANDIDATE_SCOPE_DIMENSIONS = 64
MAXIMUM_CANDIDATE_CORRECTION_UTF8_BYTES = 16 * 1024
MAXIMUM_CANDIDATE_CLAIM_UTF8_BYTES = 16 * 1024
MAXIMUM_CANDIDATE_ENVELOPE_BYTES = 512 * 1024
MAXIMUM_CANDIDATES_PER_SLOT = 128
MAXIMUM_CANDIDATE_BYTES_PER_SLOT = 8 * 1024 * 1024

CORRECTION_CANDIDATE_SOURCE_ALLOWLIST = frozenset(
    {
        ActorType.KNOWLEDGE_KERNEL,
        ActorType.CRITIC_PROMPT_LOOP,
    }
)
CORRECTION_CANDIDATE_TARGET_STATES = frozenset(
    {
        PersonalMemorySpaceState.CONFIGURED,
        PersonalMemorySpaceState.ACTIVE,
    }
)

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_LOGICAL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+-]{0,254}$")


class CorrectionCandidateTrigger(StableStringEnum):
    """Closed, non-authoritative reason for opening a candidate intake."""

    KNOWLEDGE_KERNEL_DETECTED = "KNOWLEDGE_KERNEL_DETECTED"
    CRITIC_PROMPT_LOOP_DETECTED = "CRITIC_PROMPT_LOOP_DETECTED"
    FAILED_VERIFICATION = "FAILED_VERIFICATION"
    CORRECTION_OBSERVED = "CORRECTION_OBSERVED"


class CorrectionCandidateIntakeDisposition(StableStringEnum):
    """Result of a future durable intake operation, not a patch decision."""

    ACCEPTED = "ACCEPTED"
    EXACT_REPLAY = "EXACT_REPLAY"
    DUPLICATE = "DUPLICATE"


class CorrectionCandidateReasonCode(StableStringEnum):
    """Closed, sanitized vocabulary for metadata, receipts, and failures."""

    CANDIDATE_ACCEPTED = "CANDIDATE_ACCEPTED"
    EXACT_REPLAY = "EXACT_REPLAY"
    EXACT_DUPLICATE = "EXACT_DUPLICATE"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    CONTENT_CONFLICT = "CONTENT_CONFLICT"
    SOURCE_KNOWLEDGE_KERNEL = "SOURCE_KNOWLEDGE_KERNEL"
    SOURCE_CRITIC_PROMPT_LOOP = "SOURCE_CRITIC_PROMPT_LOOP"
    SOURCE_NOT_ALLOWED = "SOURCE_NOT_ALLOWED"
    TARGET_NOT_FOUND = "TARGET_NOT_FOUND"
    TARGET_INELIGIBLE = "TARGET_INELIGIBLE"
    TARGET_ARCHIVED = "TARGET_ARCHIVED"
    TARGET_DELETE_PENDING = "TARGET_DELETE_PENDING"
    TENANT_MISMATCH = "TENANT_MISMATCH"
    OWNER_MISMATCH = "OWNER_MISMATCH"
    SCOPE_MISMATCH = "SCOPE_MISMATCH"
    ROUTE_MISMATCH = "ROUTE_MISMATCH"
    RUN_MISMATCH = "RUN_MISMATCH"
    MODEL_BINDING_MISMATCH = "MODEL_BINDING_MISMATCH"
    CANDIDATE_TOO_LARGE = "CANDIDATE_TOO_LARGE"
    CANDIDATE_INVALID = "CANDIDATE_INVALID"
    LINEAGE_INVALID = "LINEAGE_INVALID"
    CANDIDATE_QUOTA_EXCEEDED = "CANDIDATE_QUOTA_EXCEEDED"
    APPROVAL_FORBIDDEN = "APPROVAL_FORBIDDEN"
    COMMIT_FORBIDDEN = "COMMIT_FORBIDDEN"
    ACTIVATION_FORBIDDEN = "ACTIVATION_FORBIDDEN"


class CorrectionCandidateIntakeError(PersistenceError):
    """Typed fail-closed error whose public metadata is a closed reason code."""

    def __init__(
        self,
        reason_code: CorrectionCandidateReasonCode,
    ) -> None:
        if not isinstance(reason_code, CorrectionCandidateReasonCode):
            raise ContractValidationError("candidate intake reason code is invalid")
        super().__init__(
            "correction candidate intake failed",
            operation_kind="CORRECTION_CANDIDATE_INTAKE",
            sanitized_code=reason_code.value,
        )
        self.reason_code = reason_code


def _text(value: object, field_name: str, maximum_bytes: int = 255) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or unicodedata.normalize("NFC", value) != value
        or _CONTROL.search(value)
        or len(value.encode("utf-8")) > maximum_bytes
    ):
        raise ContractValidationError(
            f"{field_name} must be bounded canonical NFC text"
        )
    return value


def _logical_id(value: object, field_name: str) -> str:
    result = _text(value, field_name, 255)
    if _LOGICAL_ID.fullmatch(result) is None:
        raise ContractValidationError(f"{field_name} must be a logical identifier")
    return result


def _non_negative_integer(value: object, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ContractValidationError(f"{field_name} must be non-negative")
    return value


def _positive_integer(value: object, field_name: str) -> int:
    result = _non_negative_integer(value, field_name)
    if result < 1:
        raise ContractValidationError(f"{field_name} must be positive")
    return result


def _optional_hash(value: object | None, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ContractValidationError(f"{field_name} must be a SHA-256 digest")
    return require_sha256_hex(value, field_name)


def _hash_tuple(
    value: object,
    field_name: str,
    maximum: int,
) -> tuple[str, ...]:
    if not isinstance(value, (tuple, list)) or len(value) > maximum:
        raise ContractValidationError(f"{field_name} is outside Step 28 bounds")
    result = tuple(value)
    for item in result:
        if not isinstance(item, str):
            raise ContractValidationError(f"{field_name} must contain hashes")
        require_sha256_hex(item, f"{field_name} item")
    if result != tuple(sorted(set(result))):
        raise ContractValidationError(f"{field_name} must be unique and canonical")
    return result


def _reason_tuple(value: object) -> tuple[CorrectionCandidateReasonCode, ...]:
    if not isinstance(value, (tuple, list)) or len(value) > MAXIMUM_CANDIDATE_REASON_CODES:
        raise ContractValidationError("reason_codes are outside Step 28 bounds")
    result = tuple(value)
    for item in result:
        if not isinstance(item, CorrectionCandidateReasonCode):
            raise ContractValidationError("reason_codes must use the closed vocabulary")
    if result != tuple(sorted(set(result), key=lambda item: item.value)):
        raise ContractValidationError("reason_codes must be unique and canonical")
    return result


def _scope_tuple(value: object) -> tuple[ScopeDimension, ...]:
    if (
        not isinstance(value, (tuple, list))
        or len(value) > MAXIMUM_CANDIDATE_SCOPE_DIMENSIONS
    ):
        raise ContractValidationError(
            "effective_scope is outside the Step 28 dimension bound"
        )
    result = tuple(value)
    if not all(isinstance(item, ScopeDimension) for item in result):
        raise ContractValidationError("effective_scope must contain typed dimensions")
    for item in result:
        _text(item.name, "scope dimension name", 128)
        _text(item.source, "scope dimension source", 128)
        if item.value_type is ScopeValueType.STRING_SET:
            string_set = tuple(item.value)
            if string_set != tuple(sorted(set(string_set))):
                raise ContractValidationError(
                    "STRING_SET scope values must be unique and canonical"
                )
    ordered = tuple(sorted(result, key=lambda item: item.name))
    if len({item.name for item in ordered}) != len(ordered):
        raise ContractValidationError("effective_scope names must be unique")
    if result != ordered:
        raise ContractValidationError("effective_scope must be canonically ordered")
    return result


def _verify_contract_reconstruction(value: object, field_name: str) -> None:
    """Re-run a frozen dataclass constructor and compare every derived field.

    Hash verification alone cannot prove the semantic invariants enforced by
    ``__post_init__`` after an in-memory object has been force-mutated and all
    dependent digests have been recomputed.  Reconstruction is therefore the
    fail-closed verifier boundary for nested Step 28 contracts.
    """

    try:
        init_values = {
            item.name: getattr(value, item.name)
            for item in dataclass_fields(value)
            if item.init
        }
        reconstructed = type(value)(**init_values)
    except (ContractValidationError, IntegrityError, TypeError, ValueError) as error:
        raise IntegrityError(f"{field_name} semantic reconstruction failed") from error
    if reconstructed != value:
        raise IntegrityError(f"{field_name} semantic reconstruction mismatch")


def _canonical_scope_bytes(value: object) -> bytes:
    """Return the repository-canonical identity of an exact effective scope.

    Step 28 does not interpret or project scope dimensions.  Equality therefore
    covers every semantic ``ScopeDimension`` field after the repository's
    canonical ordering and JSON serialization rules have been applied.
    """

    return canonical_json_bytes(
        {"scope_dimensions": _scope_tuple(value)}
    )


def _validate_claim_scope_binding(
    candidate: CorrectionCandidate,
    lineage: CorrectionCandidateRouteResultLineage,
) -> None:
    """Fail closed unless every claim has the exact route-result scope.

    Neither widening nor narrowing is permitted: extra, missing, or changed
    dimensions (including type, comparison mode, source, and required flag)
    are detached from the verified route/result lineage.
    """

    lineage_scope = _canonical_scope_bytes(lineage.effective_scope)
    for claim in candidate.detected_claims:
        if _canonical_scope_bytes(claim.scope_dimensions) != lineage_scope:
            raise ContractValidationError(
                "candidate claim scope must exactly match lineage effective_scope"
            )


def _kernel_run_identity_digest(value: KernelRunIdentity) -> str:
    if not isinstance(value, KernelRunIdentity):
        raise ContractValidationError("kernel_run must be KernelRunIdentity")
    return canonical_sha256(
        {
            "contract_type": "KernelRunIdentity",
            "contract_version": STEP28_SCHEMA_VERSION,
            "kernel_run": value,
        }
    )


@dataclass(frozen=True, slots=True)
class CorrectionCandidateIntakePolicy:
    """Fixed bounds and authority ceiling for Step 28 candidate intake."""

    policy_id: str = CORRECTION_CANDIDATE_INTAKE_POLICY_ID
    policy_version: str = CORRECTION_CANDIDATE_INTAKE_POLICY_VERSION
    allowed_source_components: tuple[ActorType, ...] = (
        ActorType.CRITIC_PROMPT_LOOP,
        ActorType.KNOWLEDGE_KERNEL,
    )
    maximum_state: CorrectionCandidateState = CorrectionCandidateState.DETECTED
    maximum_candidates_per_submission: int = 1
    maximum_detected_claims: int = MAXIMUM_CANDIDATE_CLAIMS
    maximum_evidence_references: int = MAXIMUM_CANDIDATE_EVIDENCE_REFERENCES
    maximum_reason_codes: int = MAXIMUM_CANDIDATE_REASON_CODES
    maximum_scope_dimensions: int = MAXIMUM_CANDIDATE_SCOPE_DIMENSIONS
    maximum_correction_utf8_bytes: int = MAXIMUM_CANDIDATE_CORRECTION_UTF8_BYTES
    maximum_claim_utf8_bytes: int = MAXIMUM_CANDIDATE_CLAIM_UTF8_BYTES
    maximum_envelope_bytes: int = MAXIMUM_CANDIDATE_ENVELOPE_BYTES
    maximum_candidates_per_slot: int = MAXIMUM_CANDIDATES_PER_SLOT
    maximum_candidate_bytes_per_slot: int = MAXIMUM_CANDIDATE_BYTES_PER_SLOT
    grants_patch_proposal_transition: bool = False
    grants_approval: bool = False
    grants_commit: bool = False
    grants_activation: bool = False
    grants_memory_write: bool = False
    grants_retrieval: bool = False
    grants_canonical_evidence: bool = False
    policy_digest: str = field(init=False)

    def __post_init__(self) -> None:
        expected_sources = tuple(
            sorted(CORRECTION_CANDIDATE_SOURCE_ALLOWLIST, key=lambda x: x.value)
        )
        if (
            self.policy_id != CORRECTION_CANDIDATE_INTAKE_POLICY_ID
            or self.policy_version != CORRECTION_CANDIDATE_INTAKE_POLICY_VERSION
            or tuple(self.allowed_source_components) != expected_sources
            or self.maximum_state is not CorrectionCandidateState.DETECTED
            or self.maximum_candidates_per_submission != 1
            or self.maximum_detected_claims != MAXIMUM_CANDIDATE_CLAIMS
            or self.maximum_evidence_references
            != MAXIMUM_CANDIDATE_EVIDENCE_REFERENCES
            or self.maximum_reason_codes != MAXIMUM_CANDIDATE_REASON_CODES
            or self.maximum_scope_dimensions
            != MAXIMUM_CANDIDATE_SCOPE_DIMENSIONS
            or self.maximum_correction_utf8_bytes
            != MAXIMUM_CANDIDATE_CORRECTION_UTF8_BYTES
            or self.maximum_claim_utf8_bytes != MAXIMUM_CANDIDATE_CLAIM_UTF8_BYTES
            or self.maximum_envelope_bytes != MAXIMUM_CANDIDATE_ENVELOPE_BYTES
            or self.maximum_candidates_per_slot != MAXIMUM_CANDIDATES_PER_SLOT
            or self.maximum_candidate_bytes_per_slot
            != MAXIMUM_CANDIDATE_BYTES_PER_SLOT
        ):
            raise ContractValidationError("Step 28 candidate policy cannot be changed")
        authority_flags = (
            self.grants_patch_proposal_transition,
            self.grants_approval,
            self.grants_commit,
            self.grants_activation,
            self.grants_memory_write,
            self.grants_retrieval,
            self.grants_canonical_evidence,
        )
        if any(value is not False for value in authority_flags):
            raise ContractValidationError("Step 28 candidate policy grants authority")
        object.__setattr__(
            self,
            "allowed_source_components",
            expected_sources,
        )
        object.__setattr__(
            self,
            "policy_digest",
            canonical_sha256(self, exclude_fields=("policy_digest",)),
        )


def load_correction_candidate_intake_policy() -> CorrectionCandidateIntakePolicy:
    return CorrectionCandidateIntakePolicy()


@dataclass(frozen=True, slots=True)
class CorrectionCandidateTargetBinding:
    """Hash-bound snapshot of the exact Step 27 target and model binding."""

    schema_version: str
    tenant_id: str
    owner_user_id: str
    personal_memory_space_id: str
    hat_scope_id: str
    slot_state: PersonalMemorySpaceState
    slot_state_version: int
    slot_configuration_version: int
    slot_hash: str
    configuration_digest: str
    quota_policy_id: str
    quota_policy_digest: str
    model_binding_id: str
    model_binding_hash: str
    model_binding_version: int
    binding_mode: PersonalMemoryBindingMode
    provider_id: str
    model_id: str
    model_revision_or_declared_version: str
    model_binding_enabled: bool
    target_binding_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != STEP28_SCHEMA_VERSION:
            raise ContractValidationError("unsupported candidate target schema")
        for value, name in (
            (self.tenant_id, "tenant_id"),
            (self.owner_user_id, "owner_user_id"),
            (self.personal_memory_space_id, "personal_memory_space_id"),
            (self.hat_scope_id, "hat_scope_id"),
            (self.quota_policy_id, "quota_policy_id"),
            (self.model_binding_id, "model_binding_id"),
            (self.provider_id, "provider_id"),
        ):
            _logical_id(value, name)
        for value, name in (
            (self.model_id, "model_id"),
            (
                self.model_revision_or_declared_version,
                "model_revision_or_declared_version",
            ),
        ):
            _text(value, name, 128)
        for value, name in (
            (self.slot_hash, "slot_hash"),
            (self.configuration_digest, "configuration_digest"),
            (self.quota_policy_digest, "quota_policy_digest"),
            (self.model_binding_hash, "model_binding_hash"),
        ):
            require_sha256_hex(value, name)
        if self.slot_state not in CORRECTION_CANDIDATE_TARGET_STATES:
            raise ContractValidationError("candidate target slot state is ineligible")
        _non_negative_integer(self.slot_state_version, "slot_state_version")
        _non_negative_integer(
            self.slot_configuration_version,
            "slot_configuration_version",
        )
        _positive_integer(self.model_binding_version, "model_binding_version")
        if self.binding_mode is not PersonalMemoryBindingMode.EXACT_MODEL:
            raise ContractValidationError("candidate requires an exact model binding")
        if self.model_binding_enabled is not True:
            raise ContractValidationError("candidate model binding must be enabled")
        object.__setattr__(
            self,
            "target_binding_hash",
            canonical_sha256(self, exclude_fields=("target_binding_hash",)),
        )


@dataclass(frozen=True, slots=True)
class CorrectionCandidateRouteResultLineage:
    """Route/result lineage snapshot; failed verification remains representable."""

    schema_version: str
    request_id: str
    original_query_digest: str
    route_hash: str
    result_hash: str
    knowledge_route: KnowledgeRoute
    selected_hat_id: str | None
    selected_hat_version: str | None
    selected_manifest_digest: str | None
    effective_scope: tuple[ScopeDimension, ...]
    answer_status: AnswerStatus
    evidence_status: EvidenceStatus
    draft_v1_hash: str
    draft_v2_hash: str | None
    correction_packet_hash: str | None
    verification_summary_hash: str | None
    verified_answer_hash: str | None
    lineage_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != STEP28_SCHEMA_VERSION:
            raise ContractValidationError("unsupported candidate lineage schema")
        _logical_id(self.request_id, "request_id")
        for value, name in (
            (self.original_query_digest, "original_query_digest"),
            (self.route_hash, "route_hash"),
            (self.result_hash, "result_hash"),
            (self.draft_v1_hash, "draft_v1_hash"),
        ):
            require_sha256_hex(value, name)
        if not isinstance(self.knowledge_route, KnowledgeRoute):
            raise ContractValidationError("knowledge_route is invalid")
        if not isinstance(self.answer_status, AnswerStatus):
            raise ContractValidationError("answer_status is invalid")
        if not isinstance(self.evidence_status, EvidenceStatus):
            raise ContractValidationError("evidence_status is invalid")
        selected = (
            self.selected_hat_id,
            self.selected_hat_version,
            self.selected_manifest_digest,
        )
        if self.knowledge_route in {
            KnowledgeRoute.HAT_ASSIST,
            KnowledgeRoute.HAT_ENFORCE,
        }:
            if any(item is None for item in selected):
                raise ContractValidationError("HAT route requires exact HAT lineage")
            _logical_id(self.selected_hat_id, "selected_hat_id")
            _text(self.selected_hat_version, "selected_hat_version", 128)
            require_sha256_hex(
                self.selected_manifest_digest,
                "selected_manifest_digest",
            )
        elif any(item is not None for item in selected):
            raise ContractValidationError(
                "PASS_THROUGH and AMBIGUOUS routes cannot select a HAT"
            )
        object.__setattr__(self, "effective_scope", _scope_tuple(self.effective_scope))
        draft_v2_hash = _optional_hash(self.draft_v2_hash, "draft_v2_hash")
        _optional_hash(self.correction_packet_hash, "correction_packet_hash")
        verification_hash = _optional_hash(
            self.verification_summary_hash,
            "verification_summary_hash",
        )
        _optional_hash(self.verified_answer_hash, "verified_answer_hash")
        if (draft_v2_hash is None) != (verification_hash is None):
            raise ContractValidationError(
                "Draft V2 and verification-summary lineage must be both present or absent"
            )
        if self.answer_status is AnswerStatus.VERIFIED and draft_v2_hash is None:
            raise ContractValidationError("verified lineage requires Draft V2 verification")
        object.__setattr__(
            self,
            "lineage_hash",
            canonical_sha256(self, exclude_fields=("lineage_hash",)),
        )


@dataclass(frozen=True, slots=True)
class CorrectionCandidateMetadata:
    """Bounded producer metadata; it is never evidence or authority."""

    schema_version: str
    trigger: CorrectionCandidateTrigger
    producer_id: str
    producer_version: str
    reason_codes: tuple[CorrectionCandidateReasonCode, ...]
    metadata_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != STEP28_SCHEMA_VERSION:
            raise ContractValidationError("unsupported candidate metadata schema")
        if not isinstance(self.trigger, CorrectionCandidateTrigger):
            raise ContractValidationError("candidate trigger is invalid")
        _logical_id(self.producer_id, "producer_id")
        _text(self.producer_version, "producer_version", 128)
        object.__setattr__(self, "reason_codes", _reason_tuple(self.reason_codes))
        object.__setattr__(
            self,
            "metadata_hash",
            canonical_sha256(self, exclude_fields=("metadata_hash",)),
        )


def correction_candidate_submission_id(
    *,
    tenant_id: str,
    owner_user_id: str,
    idempotency_key: str,
) -> str:
    """Derive the stable operation identity used for exact replay detection."""

    digest = canonical_sha256(
        {
            "contract_type": "CorrectionCandidateSubmission",
            "contract_version": STEP28_SCHEMA_VERSION,
            "idempotency_key": _logical_id(idempotency_key, "idempotency_key"),
            "owner_user_id": _logical_id(owner_user_id, "owner_user_id"),
            "tenant_id": _logical_id(tenant_id, "tenant_id"),
        }
    )
    return f"correction-candidate-submission-{digest}"


def _candidate_id_from_deduplication_key(value: str) -> str:
    prefix = "correction-candidate-dedup-"
    if not isinstance(value, str) or not value.startswith(prefix):
        raise ContractValidationError("candidate deduplication identity is invalid")
    require_sha256_hex(value[len(prefix) :], "candidate deduplication digest")
    return f"correction-candidate-{value[len(prefix) :]}"


def _correction_candidate_receipt_id(
    *,
    submission_id: str,
    envelope_id: str,
    candidate_id: str,
    disposition: CorrectionCandidateIntakeDisposition,
) -> str:
    identity = canonical_sha256(
        {
            "candidate_id": _logical_id(candidate_id, "candidate_id"),
            "contract_type": "CorrectionCandidateIntakeReceipt",
            "contract_version": STEP28_SCHEMA_VERSION,
            "disposition": disposition,
            "envelope_id": _logical_id(envelope_id, "envelope_id"),
            "submission_id": _logical_id(submission_id, "submission_id"),
        }
    )
    return f"correction-candidate-receipt-{identity}"


def correction_candidate_semantic_deduplication_key(
    candidate: CorrectionCandidate,
    run_identity: KernelRunIdentity,
    target_slot_binding: CorrectionCandidateTargetBinding,
    lineage: CorrectionCandidateRouteResultLineage,
) -> str:
    """Derive a non-mutating semantic key for later durable deduplication.

    The caller's idempotency key, detection time, and uncertainty are excluded.
    Source component/run, route/result lineage, owner, exact Step 27 slot/model
    snapshot, scope, and correction content are deliberately included so
    provenance from independent producers or runs can never collapse together.
    """

    if not isinstance(candidate, CorrectionCandidate):
        raise ContractValidationError("candidate must be CorrectionCandidate")
    if not isinstance(run_identity, KernelRunIdentity):
        raise ContractValidationError("run_identity must be KernelRunIdentity")
    if not isinstance(target_slot_binding, CorrectionCandidateTargetBinding):
        raise ContractValidationError("target_slot_binding must be typed")
    if not isinstance(lineage, CorrectionCandidateRouteResultLineage):
        raise ContractValidationError("lineage must be typed")
    digest = canonical_sha256(
        {
            "contract_type": "CorrectionCandidateSemanticDeduplication",
            "contract_version": STEP28_SCHEMA_VERSION,
            "tenant_id": candidate.tenant_id,
            "owner_user_id": candidate.user_id,
            "personal_memory_space_id": candidate.personal_memory_space_id,
            "source_component": candidate.source_component,
            "source_run_id": candidate.run_id,
            "run_identity_digest": _kernel_run_identity_digest(run_identity),
            "hat_scope_id": target_slot_binding.hat_scope_id,
            "target_binding_hash": target_slot_binding.target_binding_hash,
            "lineage_hash": lineage.lineage_hash,
            "effective_scope": lineage.effective_scope,
            "draft_v1_hash": candidate.draft_v1_reference,
            "detected_claims": candidate.detected_claims,
            "proposed_correction": candidate.proposed_correction,
            "available_evidence_references": candidate.available_evidence_references,
        }
    )
    return f"correction-candidate-dedup-{digest}"


def _validate_candidate_payload(
    candidate: CorrectionCandidate,
    policy: CorrectionCandidateIntakePolicy,
) -> None:
    verify_correction_candidate_hash(candidate)
    for value, name in (
        (candidate.event_id, "candidate event_id"),
        (candidate.tenant_id, "candidate tenant_id"),
        (candidate.user_id, "candidate user_id"),
        (candidate.personal_memory_space_id, "candidate personal_memory_space_id"),
        (candidate.run_id, "candidate run_id"),
        (candidate.model_binding_id, "candidate model_binding_id"),
    ):
        _logical_id(value, name)
    if candidate.source_component not in CORRECTION_CANDIDATE_SOURCE_ALLOWLIST:
        raise ContractValidationError("candidate source is outside the Step 28 allowlist")
    if candidate.state is not CorrectionCandidateState.DETECTED:
        raise ContractValidationError("Step 28 candidate state ceiling is DETECTED")
    if len(candidate.detected_claims) > policy.maximum_detected_claims:
        raise ContractValidationError("candidate claim quota exceeded")
    claim_order = tuple(sorted(candidate.detected_claims, key=lambda item: item.claim_id))
    if candidate.detected_claims != claim_order:
        raise ContractValidationError("candidate claims must be canonically ordered")
    for claim in candidate.detected_claims:
        _logical_id(claim.claim_id, "claim_id")
        _logical_id(claim.draft_id, "claim draft_id")
        _text(claim.claim_category, "claim_category", 128)
        _text(claim.statement, "claim statement", policy.maximum_claim_utf8_bytes)
        _scope_tuple(claim.scope_dimensions)
    _text(
        candidate.proposed_correction,
        "proposed_correction",
        policy.maximum_correction_utf8_bytes,
    )
    if len(candidate.available_evidence_references) > policy.maximum_evidence_references:
        raise ContractValidationError("candidate evidence-reference quota exceeded")
    _hash_tuple(
        candidate.available_evidence_references,
        "available_evidence_references",
        policy.maximum_evidence_references,
    )
    require_sha256_hex(candidate.draft_v1_reference, "draft_v1_reference")


def _validate_trigger_source(
    source: ActorType,
    trigger: CorrectionCandidateTrigger,
    reason_codes: tuple[CorrectionCandidateReasonCode, ...],
) -> None:
    if (
        trigger is CorrectionCandidateTrigger.KNOWLEDGE_KERNEL_DETECTED
        and source is not ActorType.KNOWLEDGE_KERNEL
    ):
        raise ContractValidationError("Kernel trigger must come from Knowledge Kernel")
    if (
        trigger is CorrectionCandidateTrigger.CRITIC_PROMPT_LOOP_DETECTED
        and source is not ActorType.CRITIC_PROMPT_LOOP
    ):
        raise ContractValidationError("Critic trigger must come from Critic Prompt Loop")
    required_source_reason = (
        CorrectionCandidateReasonCode.SOURCE_KNOWLEDGE_KERNEL
        if source is ActorType.KNOWLEDGE_KERNEL
        else CorrectionCandidateReasonCode.SOURCE_CRITIC_PROMPT_LOOP
    )
    if required_source_reason not in reason_codes:
        raise ContractValidationError("candidate metadata source reason is missing")


@dataclass(frozen=True, slots=True)
class CorrectionCandidateSubmission:
    """One exact idempotent submission; construction writes nothing."""

    schema_version: str
    candidate: CorrectionCandidate
    run_identity: KernelRunIdentity
    target_slot_binding: CorrectionCandidateTargetBinding
    lineage: CorrectionCandidateRouteResultLineage
    metadata: CorrectionCandidateMetadata
    policy_digest: str
    idempotency_key: str
    submitted_at: datetime
    candidate_text_sha256: str = field(init=False)
    run_identity_digest: str = field(init=False)
    submission_id: str = field(init=False)
    semantic_deduplication_key: str = field(init=False)
    submission_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != STEP28_SCHEMA_VERSION:
            raise ContractValidationError("unsupported candidate submission schema")
        if not isinstance(self.candidate, CorrectionCandidate):
            raise ContractValidationError("candidate must be CorrectionCandidate")
        if not isinstance(self.run_identity, KernelRunIdentity):
            raise ContractValidationError("run_identity must be KernelRunIdentity")
        if not isinstance(self.target_slot_binding, CorrectionCandidateTargetBinding):
            raise ContractValidationError("target_slot_binding must be typed")
        if not isinstance(
            self.lineage,
            CorrectionCandidateRouteResultLineage,
        ):
            raise ContractValidationError("lineage must be typed")
        if not isinstance(self.metadata, CorrectionCandidateMetadata):
            raise ContractValidationError("metadata must be typed")
        policy = load_correction_candidate_intake_policy()
        require_sha256_hex(self.policy_digest, "policy_digest")
        if self.policy_digest != policy.policy_digest:
            raise ContractValidationError("candidate submission policy differs")
        _logical_id(self.idempotency_key, "idempotency_key")
        submitted_at = ensure_utc(self.submitted_at, "submitted_at")
        object.__setattr__(self, "submitted_at", submitted_at)

        _validate_candidate_payload(self.candidate, policy)
        _validate_claim_scope_binding(self.candidate, self.lineage)
        _validate_trigger_source(
            self.candidate.source_component,
            self.metadata.trigger,
            self.metadata.reason_codes,
        )
        validate_correction_candidate_ownership(
            self.candidate,
            run=self.run_identity,
            target_ownership=self.candidate.ownership,
        )
        target = self.target_slot_binding
        candidate = self.candidate
        if (
            target.tenant_id != candidate.tenant_id
            or target.owner_user_id != candidate.user_id
            or target.personal_memory_space_id != candidate.personal_memory_space_id
        ):
            raise ContractValidationError("candidate crosses the target slot owner")
        if (
            target.model_binding_id != candidate.model_binding_id
            or self.run_identity.model_binding_id != candidate.model_binding_id
        ):
            raise ContractValidationError("candidate model binding is detached")
        if self.lineage.draft_v1_hash != candidate.draft_v1_reference:
            raise ContractValidationError("candidate Draft V1 lineage is detached")
        if self.lineage.request_id != self.run_identity.kernel_run_id:
            raise ContractValidationError("candidate route/result lineage is detached")
        if candidate.created_at < self.run_identity.created_at:
            raise ContractValidationError("candidate predates its Kernel run")
        if submitted_at < candidate.created_at:
            raise ContractValidationError("candidate submission predates detection")

        object.__setattr__(
            self,
            "candidate_text_sha256",
            hashlib.sha256(candidate.proposed_correction.encode("utf-8")).hexdigest(),
        )
        run_digest = _kernel_run_identity_digest(self.run_identity)
        object.__setattr__(self, "run_identity_digest", run_digest)
        object.__setattr__(
            self,
            "submission_id",
            correction_candidate_submission_id(
                tenant_id=candidate.tenant_id,
                owner_user_id=candidate.user_id,
                idempotency_key=self.idempotency_key,
            ),
        )
        object.__setattr__(
            self,
            "semantic_deduplication_key",
            correction_candidate_semantic_deduplication_key(
                candidate,
                self.run_identity,
                target,
                self.lineage,
            ),
        )
        object.__setattr__(
            self,
            "submission_hash",
            canonical_sha256(self, exclude_fields=("submission_hash",)),
        )


@dataclass(frozen=True, slots=True)
class CorrectionCandidateEnvelope:
    """Strict non-authoritative envelope suitable for canonical JSONB storage."""

    schema_version: str
    contract_type: str
    submission: CorrectionCandidateSubmission
    policy: CorrectionCandidateIntakePolicy
    candidate_id: str = field(init=False)
    envelope_id: str = field(init=False)
    envelope_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != STEP28_SCHEMA_VERSION:
            raise ContractValidationError("unsupported candidate envelope schema")
        if self.contract_type != CORRECTION_CANDIDATE_ENVELOPE_CONTRACT_TYPE:
            raise ContractValidationError("candidate envelope contract type is invalid")
        if not isinstance(self.submission, CorrectionCandidateSubmission):
            raise ContractValidationError("submission must be typed")
        if not isinstance(self.policy, CorrectionCandidateIntakePolicy):
            raise ContractValidationError("policy must be typed")
        if self.policy != load_correction_candidate_intake_policy():
            raise ContractValidationError("candidate envelope policy differs")
        if self.submission.policy_digest != self.policy.policy_digest:
            raise ContractValidationError("candidate envelope policy is detached")
        object.__setattr__(
            self,
            "candidate_id",
            _candidate_id_from_deduplication_key(
                self.submission.semantic_deduplication_key
            ),
        )
        identity = canonical_sha256(
            {
                "contract_type": self.contract_type,
                "contract_version": self.schema_version,
                "candidate_id": self.candidate_id,
                "submission_id": self.submission.submission_id,
                "submission_hash": self.submission.submission_hash,
            }
        )
        object.__setattr__(self, "envelope_id", f"correction-candidate-envelope-{identity}")
        object.__setattr__(
            self,
            "envelope_hash",
            canonical_sha256(self, exclude_fields=("envelope_hash",)),
        )
        if len(canonical_json_bytes(self)) > self.policy.maximum_envelope_bytes:
            raise ContractValidationError("candidate envelope byte quota exceeded")

    def canonical_text(self) -> str:
        return canonical_json(self)

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self)


@dataclass(frozen=True, slots=True)
class CorrectionCandidateIntakeReceipt:
    """Candidate-intake acknowledgement; not approval or patch activation."""

    schema_version: str
    receipt_id: str
    submission_id: str
    submission_hash: str
    envelope_id: str
    envelope_hash: str
    candidate_id: str
    candidate_event_id: str
    candidate_content_hash: str
    semantic_deduplication_key: str
    tenant_id: str
    owner_user_id: str
    personal_memory_space_id: str
    idempotency_key: str
    disposition: CorrectionCandidateIntakeDisposition
    reason: CorrectionCandidateReasonCode
    accepted_at: datetime
    receipt_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != STEP28_SCHEMA_VERSION:
            raise ContractValidationError("unsupported candidate receipt schema")
        for value, name in (
            (self.receipt_id, "receipt_id"),
            (self.submission_id, "submission_id"),
            (self.envelope_id, "envelope_id"),
            (self.candidate_id, "candidate_id"),
            (self.candidate_event_id, "candidate_event_id"),
            (self.semantic_deduplication_key, "semantic_deduplication_key"),
            (self.tenant_id, "tenant_id"),
            (self.owner_user_id, "owner_user_id"),
            (self.personal_memory_space_id, "personal_memory_space_id"),
            (self.idempotency_key, "idempotency_key"),
        ):
            _logical_id(value, name)
        for value, name in (
            (self.submission_hash, "submission_hash"),
            (self.envelope_hash, "envelope_hash"),
            (self.candidate_content_hash, "candidate_content_hash"),
        ):
            require_sha256_hex(value, name)
        if not isinstance(self.disposition, CorrectionCandidateIntakeDisposition):
            raise ContractValidationError("candidate receipt disposition is invalid")
        if not isinstance(self.reason, CorrectionCandidateReasonCode):
            raise ContractValidationError("candidate receipt reason is invalid")
        expected_reason = {
            CorrectionCandidateIntakeDisposition.ACCEPTED: (
                CorrectionCandidateReasonCode.CANDIDATE_ACCEPTED
            ),
            CorrectionCandidateIntakeDisposition.EXACT_REPLAY: (
                CorrectionCandidateReasonCode.EXACT_REPLAY
            ),
            CorrectionCandidateIntakeDisposition.DUPLICATE: (
                CorrectionCandidateReasonCode.EXACT_DUPLICATE
            ),
        }[self.disposition]
        if self.reason is not expected_reason:
            raise ContractValidationError("candidate receipt reason differs from disposition")
        expected_candidate_id = _candidate_id_from_deduplication_key(
            self.semantic_deduplication_key
        )
        if self.candidate_id != expected_candidate_id:
            raise ContractValidationError("candidate receipt identity is detached")
        expected_receipt_id = _correction_candidate_receipt_id(
            submission_id=self.submission_id,
            envelope_id=self.envelope_id,
            candidate_id=self.candidate_id,
            disposition=self.disposition,
        )
        if self.receipt_id != expected_receipt_id:
            raise ContractValidationError("candidate receipt ID is detached")
        object.__setattr__(self, "accepted_at", ensure_utc(self.accepted_at, "accepted_at"))
        object.__setattr__(
            self,
            "receipt_hash",
            canonical_sha256(self, exclude_fields=("receipt_hash",)),
        )

    def canonical_text(self) -> str:
        return canonical_json(self)

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self)


def _target_binding_from_slot(
    slot: PersonalMemoryHatSlot,
    model_binding: PersonalMemoryModelBinding,
) -> CorrectionCandidateTargetBinding:
    verify_slot_hash(slot)
    verify_model_binding_hash(model_binding)
    if model_binding not in slot.model_bindings:
        raise ContractValidationError("model binding is not present in target slot")
    if (
        model_binding.tenant_id != slot.tenant_id
        or model_binding.owner_user_id != slot.owner_user_id
        or model_binding.personal_memory_space_id != slot.personal_memory_space_id
    ):
        raise ContractValidationError("model binding crosses the target slot owner")
    return CorrectionCandidateTargetBinding(
        schema_version=STEP28_SCHEMA_VERSION,
        tenant_id=slot.tenant_id,
        owner_user_id=slot.owner_user_id,
        personal_memory_space_id=slot.personal_memory_space_id,
        hat_scope_id=slot.hat_scope_id,
        slot_state=slot.state,
        slot_state_version=slot.state_version,
        slot_configuration_version=slot.configuration_version,
        slot_hash=slot.slot_hash,
        configuration_digest=slot.configuration_digest,
        quota_policy_id=slot.quota_policy_id,
        quota_policy_digest=slot.quota_policy_digest,
        model_binding_id=model_binding.binding_id,
        model_binding_hash=model_binding.binding_hash,
        model_binding_version=model_binding.binding_version,
        binding_mode=model_binding.binding_mode,
        provider_id=model_binding.provider_id,
        model_id=model_binding.model_id,
        model_revision_or_declared_version=(
            model_binding.model_revision_or_declared_version
        ),
        model_binding_enabled=model_binding.enabled,
    )


def build_correction_candidate_envelope(
    *,
    candidate: CorrectionCandidate,
    kernel_run: KernelRunIdentity,
    slot: PersonalMemoryHatSlot,
    route_result_lineage: CorrectionCandidateRouteResultLineage,
    metadata: CorrectionCandidateMetadata,
    idempotency_key: str,
    submitted_at: datetime,
    policy: CorrectionCandidateIntakePolicy | None = None,
) -> CorrectionCandidateEnvelope:
    """Build one inert envelope after exact typed cross-contract validation."""

    if not isinstance(slot, PersonalMemoryHatSlot):
        raise ContractValidationError("slot must be PersonalMemoryHatSlot")
    matching = tuple(
        binding
        for binding in slot.model_bindings
        if binding.binding_id == candidate.model_binding_id
    )
    if len(matching) != 1:
        raise ContractValidationError("candidate requires one exact target model binding")
    selected_policy = policy or load_correction_candidate_intake_policy()
    if selected_policy != load_correction_candidate_intake_policy():
        raise ContractValidationError("candidate intake policy differs")
    target_binding = _target_binding_from_slot(slot, matching[0])
    submission = CorrectionCandidateSubmission(
        schema_version=STEP28_SCHEMA_VERSION,
        candidate=candidate,
        run_identity=kernel_run,
        target_slot_binding=target_binding,
        lineage=route_result_lineage,
        metadata=metadata,
        policy_digest=selected_policy.policy_digest,
        idempotency_key=idempotency_key,
        submitted_at=submitted_at,
    )
    return CorrectionCandidateEnvelope(
        schema_version=STEP28_SCHEMA_VERSION,
        contract_type=CORRECTION_CANDIDATE_ENVELOPE_CONTRACT_TYPE,
        submission=submission,
        policy=selected_policy,
    )


def build_correction_candidate_intake_receipt(
    envelope: CorrectionCandidateEnvelope,
    *,
    accepted_at: datetime,
    disposition: CorrectionCandidateIntakeDisposition = (
        CorrectionCandidateIntakeDisposition.ACCEPTED
    ),
) -> CorrectionCandidateIntakeReceipt:
    """Build a receipt value after a caller's intake decision; performs no write."""

    verify_correction_candidate_envelope(envelope)
    submission = envelope.submission
    candidate = submission.candidate
    accepted = ensure_utc(accepted_at, "accepted_at")
    if accepted < submission.submitted_at:
        raise ContractValidationError("candidate receipt predates submission")
    if not isinstance(disposition, CorrectionCandidateIntakeDisposition):
        raise ContractValidationError("candidate receipt disposition is invalid")
    reason = {
        CorrectionCandidateIntakeDisposition.ACCEPTED: (
            CorrectionCandidateReasonCode.CANDIDATE_ACCEPTED
        ),
        CorrectionCandidateIntakeDisposition.EXACT_REPLAY: (
            CorrectionCandidateReasonCode.EXACT_REPLAY
        ),
        CorrectionCandidateIntakeDisposition.DUPLICATE: (
            CorrectionCandidateReasonCode.EXACT_DUPLICATE
        ),
    }[disposition]
    receipt_id = _correction_candidate_receipt_id(
        submission_id=submission.submission_id,
        envelope_id=envelope.envelope_id,
        candidate_id=envelope.candidate_id,
        disposition=disposition,
    )
    return CorrectionCandidateIntakeReceipt(
        schema_version=STEP28_SCHEMA_VERSION,
        receipt_id=receipt_id,
        submission_id=submission.submission_id,
        submission_hash=submission.submission_hash,
        envelope_id=envelope.envelope_id,
        envelope_hash=envelope.envelope_hash,
        candidate_id=envelope.candidate_id,
        candidate_event_id=candidate.event_id,
        candidate_content_hash=candidate.content_hash,
        semantic_deduplication_key=submission.semantic_deduplication_key,
        tenant_id=candidate.tenant_id,
        owner_user_id=candidate.user_id,
        personal_memory_space_id=candidate.personal_memory_space_id,
        idempotency_key=submission.idempotency_key,
        disposition=disposition,
        reason=reason,
        accepted_at=accepted,
    )


def verify_correction_candidate_target_binding(
    value: CorrectionCandidateTargetBinding,
) -> None:
    verify_canonical_hash(
        value,
        value.target_binding_hash,
        exclude_fields=("target_binding_hash",),
    )
    _verify_contract_reconstruction(value, "candidate target binding")


def verify_correction_candidate_lineage(
    value: CorrectionCandidateRouteResultLineage,
) -> None:
    verify_canonical_hash(value, value.lineage_hash, exclude_fields=("lineage_hash",))
    _verify_contract_reconstruction(value, "candidate route/result lineage")
    for dimension in value.effective_scope:
        _verify_contract_reconstruction(dimension, "lineage scope dimension")


def verify_correction_candidate_metadata(value: CorrectionCandidateMetadata) -> None:
    verify_canonical_hash(value, value.metadata_hash, exclude_fields=("metadata_hash",))
    _verify_contract_reconstruction(value, "candidate metadata")


def verify_correction_candidate_intake_policy(
    value: CorrectionCandidateIntakePolicy,
) -> None:
    verify_canonical_hash(value, value.policy_digest, exclude_fields=("policy_digest",))
    _verify_contract_reconstruction(value, "candidate intake policy")
    if value != load_correction_candidate_intake_policy():
        raise IntegrityError("candidate intake policy differs from the fixed policy")


def verify_correction_candidate_submission(
    value: CorrectionCandidateSubmission,
) -> None:
    verify_correction_candidate_hash(value.candidate)
    _verify_contract_reconstruction(value.candidate, "correction candidate")
    for claim in value.candidate.detected_claims:
        _verify_contract_reconstruction(claim, "candidate claim")
        for dimension in claim.scope_dimensions:
            _verify_contract_reconstruction(dimension, "claim scope dimension")
    verify_correction_candidate_target_binding(value.target_slot_binding)
    verify_correction_candidate_lineage(value.lineage)
    verify_correction_candidate_metadata(value.metadata)
    _verify_contract_reconstruction(value.run_identity, "candidate Kernel run")
    if value.run_identity_digest != _kernel_run_identity_digest(value.run_identity):
        raise IntegrityError("candidate Kernel run identity digest mismatch")
    verify_canonical_hash(
        value,
        value.submission_hash,
        exclude_fields=("submission_hash",),
    )
    expected_id = correction_candidate_submission_id(
        tenant_id=value.candidate.tenant_id,
        owner_user_id=value.candidate.user_id,
        idempotency_key=value.idempotency_key,
    )
    if value.submission_id != expected_id:
        raise IntegrityError("candidate submission identity mismatch")
    expected_dedup = correction_candidate_semantic_deduplication_key(
        value.candidate,
        value.run_identity,
        value.target_slot_binding,
        value.lineage,
    )
    if value.semantic_deduplication_key != expected_dedup:
        raise IntegrityError("candidate semantic deduplication key mismatch")
    _verify_contract_reconstruction(value, "candidate submission")


def verify_correction_candidate_envelope(value: CorrectionCandidateEnvelope) -> None:
    verify_correction_candidate_submission(value.submission)
    verify_correction_candidate_intake_policy(value.policy)
    verify_canonical_hash(
        value,
        value.envelope_hash,
        exclude_fields=("envelope_hash",),
    )
    identity = canonical_sha256(
        {
            "contract_type": value.contract_type,
            "contract_version": value.schema_version,
            "candidate_id": value.candidate_id,
            "submission_id": value.submission.submission_id,
            "submission_hash": value.submission.submission_hash,
        }
    )
    if value.envelope_id != f"correction-candidate-envelope-{identity}":
        raise IntegrityError("candidate envelope identity mismatch")
    expected_candidate_id = _candidate_id_from_deduplication_key(
        value.submission.semantic_deduplication_key
    )
    if value.candidate_id != expected_candidate_id:
        raise IntegrityError("candidate identity mismatch")
    _verify_contract_reconstruction(value, "candidate envelope")


def verify_correction_candidate_intake_receipt(
    value: CorrectionCandidateIntakeReceipt,
) -> None:
    verify_canonical_hash(value, value.receipt_hash, exclude_fields=("receipt_hash",))
    expected_candidate_id = _candidate_id_from_deduplication_key(
        value.semantic_deduplication_key
    )
    if value.candidate_id != expected_candidate_id:
        raise IntegrityError("candidate receipt identity mismatch")
    expected_receipt_id = _correction_candidate_receipt_id(
        submission_id=value.submission_id,
        envelope_id=value.envelope_id,
        candidate_id=value.candidate_id,
        disposition=value.disposition,
    )
    if value.receipt_id != expected_receipt_id:
        raise IntegrityError("candidate receipt ID mismatch")
    expected_reason = {
        CorrectionCandidateIntakeDisposition.ACCEPTED: (
            CorrectionCandidateReasonCode.CANDIDATE_ACCEPTED
        ),
        CorrectionCandidateIntakeDisposition.EXACT_REPLAY: (
            CorrectionCandidateReasonCode.EXACT_REPLAY
        ),
        CorrectionCandidateIntakeDisposition.DUPLICATE: (
            CorrectionCandidateReasonCode.EXACT_DUPLICATE
        ),
    }.get(value.disposition)
    if value.reason is not expected_reason:
        raise IntegrityError("candidate receipt reason mismatch")
    _verify_contract_reconstruction(value, "candidate intake receipt")


def correction_candidate_envelope_to_jsonb(
    value: CorrectionCandidateEnvelope,
) -> dict[str, Any]:
    """Return verified plain canonical data suitable for a JSONB parameter."""

    verify_correction_candidate_envelope(value)
    result = to_canonical_data(value)
    if not isinstance(result, dict):
        raise ContractValidationError("candidate envelope did not serialize as an object")
    return result


def correction_candidate_receipt_to_jsonb(
    value: CorrectionCandidateIntakeReceipt,
) -> dict[str, Any]:
    verify_correction_candidate_intake_receipt(value)
    result = to_canonical_data(value)
    if not isinstance(result, dict):
        raise ContractValidationError("candidate receipt did not serialize as an object")
    return result


def _mapping(
    value: object,
    field_names: frozenset[str],
    context: str,
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ContractValidationError(f"{context} must be a mapping")
    keys = frozenset(value.keys())
    if not all(isinstance(key, str) for key in keys):
        raise ContractValidationError(f"{context} keys must be strings")
    if keys != field_names:
        missing = sorted(field_names - keys)
        unknown = sorted(keys - field_names)
        raise ContractValidationError(
            f"{context} fields differ; missing={missing!r}, unknown={unknown!r}"
        )
    return value


def _stored_hash(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise ContractValidationError(f"{field_name} must be a stored hash")
    return require_sha256_hex(value, field_name)


def _stored_id(value: object, field_name: str) -> str:
    return _logical_id(value, field_name)


def _datetime(value: object, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise ContractValidationError(f"{field_name} must be a canonical timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContractValidationError(f"{field_name} is invalid") from exc
    return ensure_utc(parsed, field_name)


def _scope_dimension_from_jsonb(value: object) -> ScopeDimension:
    data = _mapping(
        value,
        frozenset(
            {
                "name",
                "value",
                "value_type",
                "comparison_mode",
                "source",
                "required",
            }
        ),
        "scope dimension",
    )
    try:
        value_type = ScopeValueType(data["value_type"])
        comparison_mode = ScopeComparisonMode(data["comparison_mode"])
    except (TypeError, ValueError) as exc:
        raise ContractValidationError("scope dimension enum is invalid") from exc
    raw_value = data["value"]
    if value_type is ScopeValueType.STRING_SET:
        if not isinstance(raw_value, list):
            raise ContractValidationError("STRING_SET JSONB value must be an array")
        raw_value = tuple(raw_value)
    elif value_type is ScopeValueType.TIMESTAMP:
        raw_value = _datetime(raw_value, "scope dimension value")
    return ScopeDimension(
        name=data["name"],
        value=raw_value,
        value_type=value_type,
        comparison_mode=comparison_mode,
        source=data["source"],
        required=data["required"],
    )


def _claim_candidate_from_jsonb(value: object) -> ClaimCandidate:
    data = _mapping(
        value,
        frozenset(
            {
                "claim_id",
                "draft_id",
                "statement",
                "claim_category",
                "scope_dimensions",
            }
        ),
        "claim candidate",
    )
    scopes = data["scope_dimensions"]
    if not isinstance(scopes, list):
        raise ContractValidationError("claim scope_dimensions must be an array")
    return ClaimCandidate(
        claim_id=data["claim_id"],
        draft_id=data["draft_id"],
        statement=data["statement"],
        claim_category=data["claim_category"],
        scope_dimensions=tuple(_scope_dimension_from_jsonb(item) for item in scopes),
    )


def _candidate_from_jsonb(value: object) -> CorrectionCandidate:
    fields = frozenset(
        {
            "event_id",
            "tenant_id",
            "user_id",
            "personal_memory_space_id",
            "source_component",
            "run_id",
            "model_binding_id",
            "draft_v1_reference",
            "detected_claims",
            "proposed_correction",
            "available_evidence_references",
            "uncertainty",
            "created_at",
            "state",
            "content_hash",
        }
    )
    data = _mapping(value, fields, "correction candidate")
    claims = data["detected_claims"]
    evidence = data["available_evidence_references"]
    if not isinstance(claims, list) or not isinstance(evidence, list):
        raise ContractValidationError("candidate collections must be arrays")
    try:
        source = ActorType(data["source_component"])
        state = CorrectionCandidateState(data["state"])
    except (TypeError, ValueError) as exc:
        raise ContractValidationError("candidate enum is invalid") from exc
    result = CorrectionCandidate(
        event_id=data["event_id"],
        tenant_id=data["tenant_id"],
        user_id=data["user_id"],
        personal_memory_space_id=data["personal_memory_space_id"],
        source_component=source,
        run_id=data["run_id"],
        model_binding_id=data["model_binding_id"],
        draft_v1_reference=data["draft_v1_reference"],
        detected_claims=tuple(_claim_candidate_from_jsonb(item) for item in claims),
        proposed_correction=data["proposed_correction"],
        available_evidence_references=tuple(evidence),
        uncertainty=data["uncertainty"],
        created_at=_datetime(data["created_at"], "candidate created_at"),
        state=state,
    )
    if result.content_hash != _stored_hash(data["content_hash"], "candidate content_hash"):
        raise IntegrityError("stored candidate hash mismatch")
    return result


def _kernel_run_from_jsonb(value: object) -> KernelRunIdentity:
    data = _mapping(
        value,
        frozenset(
            {
                "kernel_run_id",
                "tenant_id",
                "user_id",
                "personal_memory_space_id",
                "model_binding_id",
                "created_at",
            }
        ),
        "Kernel run",
    )
    return KernelRunIdentity(
        kernel_run_id=data["kernel_run_id"],
        tenant_id=data["tenant_id"],
        user_id=data["user_id"],
        personal_memory_space_id=data["personal_memory_space_id"],
        model_binding_id=data["model_binding_id"],
        created_at=_datetime(data["created_at"], "Kernel run created_at"),
    )


def _policy_from_jsonb(value: object) -> CorrectionCandidateIntakePolicy:
    names = frozenset(CorrectionCandidateIntakePolicy.__dataclass_fields__)
    data = _mapping(value, names, "candidate policy")
    sources = data["allowed_source_components"]
    if not isinstance(sources, list):
        raise ContractValidationError("allowed_source_components must be an array")
    try:
        parsed_sources = tuple(ActorType(item) for item in sources)
        maximum_state = CorrectionCandidateState(data["maximum_state"])
    except (TypeError, ValueError) as exc:
        raise ContractValidationError("candidate policy enum is invalid") from exc
    result = CorrectionCandidateIntakePolicy(
        policy_id=data["policy_id"],
        policy_version=data["policy_version"],
        allowed_source_components=parsed_sources,
        maximum_state=maximum_state,
        maximum_candidates_per_submission=data["maximum_candidates_per_submission"],
        maximum_detected_claims=data["maximum_detected_claims"],
        maximum_evidence_references=data["maximum_evidence_references"],
        maximum_reason_codes=data["maximum_reason_codes"],
        maximum_scope_dimensions=data["maximum_scope_dimensions"],
        maximum_correction_utf8_bytes=data["maximum_correction_utf8_bytes"],
        maximum_claim_utf8_bytes=data["maximum_claim_utf8_bytes"],
        maximum_envelope_bytes=data["maximum_envelope_bytes"],
        maximum_candidates_per_slot=data["maximum_candidates_per_slot"],
        maximum_candidate_bytes_per_slot=data["maximum_candidate_bytes_per_slot"],
        grants_patch_proposal_transition=data["grants_patch_proposal_transition"],
        grants_approval=data["grants_approval"],
        grants_commit=data["grants_commit"],
        grants_activation=data["grants_activation"],
        grants_memory_write=data["grants_memory_write"],
        grants_retrieval=data["grants_retrieval"],
        grants_canonical_evidence=data["grants_canonical_evidence"],
    )
    if result.policy_digest != _stored_hash(data["policy_digest"], "policy_digest"):
        raise IntegrityError("stored candidate policy hash mismatch")
    return result


def _target_binding_from_jsonb(value: object) -> CorrectionCandidateTargetBinding:
    names = frozenset(CorrectionCandidateTargetBinding.__dataclass_fields__)
    data = _mapping(value, names, "candidate target binding")
    try:
        slot_state = PersonalMemorySpaceState(data["slot_state"])
        binding_mode = PersonalMemoryBindingMode(data["binding_mode"])
    except (TypeError, ValueError) as exc:
        raise ContractValidationError("candidate target enum is invalid") from exc
    result = CorrectionCandidateTargetBinding(
        schema_version=data["schema_version"],
        tenant_id=data["tenant_id"],
        owner_user_id=data["owner_user_id"],
        personal_memory_space_id=data["personal_memory_space_id"],
        hat_scope_id=data["hat_scope_id"],
        slot_state=slot_state,
        slot_state_version=data["slot_state_version"],
        slot_configuration_version=data["slot_configuration_version"],
        slot_hash=data["slot_hash"],
        configuration_digest=data["configuration_digest"],
        quota_policy_id=data["quota_policy_id"],
        quota_policy_digest=data["quota_policy_digest"],
        model_binding_id=data["model_binding_id"],
        model_binding_hash=data["model_binding_hash"],
        model_binding_version=data["model_binding_version"],
        binding_mode=binding_mode,
        provider_id=data["provider_id"],
        model_id=data["model_id"],
        model_revision_or_declared_version=data["model_revision_or_declared_version"],
        model_binding_enabled=data["model_binding_enabled"],
    )
    if result.target_binding_hash != _stored_hash(
        data["target_binding_hash"], "target_binding_hash"
    ):
        raise IntegrityError("stored candidate target hash mismatch")
    return result


def parse_correction_candidate_target_binding(
    value: object,
) -> CorrectionCandidateTargetBinding:
    """Strictly reconstruct the reusable Step 28 target snapshot."""

    result = _target_binding_from_jsonb(value)
    verify_correction_candidate_target_binding(result)
    return result


def _lineage_from_jsonb(value: object) -> CorrectionCandidateRouteResultLineage:
    names = frozenset(CorrectionCandidateRouteResultLineage.__dataclass_fields__)
    data = _mapping(value, names, "candidate lineage")
    effective_scope = data["effective_scope"]
    if not isinstance(effective_scope, list):
        raise ContractValidationError("candidate effective_scope must be an array")
    try:
        route = KnowledgeRoute(data["knowledge_route"])
        answer = AnswerStatus(data["answer_status"])
        evidence = EvidenceStatus(data["evidence_status"])
    except (TypeError, ValueError) as exc:
        raise ContractValidationError("candidate lineage enum is invalid") from exc
    result = CorrectionCandidateRouteResultLineage(
        schema_version=data["schema_version"],
        request_id=data["request_id"],
        original_query_digest=data["original_query_digest"],
        route_hash=data["route_hash"],
        result_hash=data["result_hash"],
        knowledge_route=route,
        selected_hat_id=data["selected_hat_id"],
        selected_hat_version=data["selected_hat_version"],
        selected_manifest_digest=data["selected_manifest_digest"],
        effective_scope=tuple(
            _scope_dimension_from_jsonb(item) for item in effective_scope
        ),
        answer_status=answer,
        evidence_status=evidence,
        draft_v1_hash=data["draft_v1_hash"],
        draft_v2_hash=data["draft_v2_hash"],
        correction_packet_hash=data["correction_packet_hash"],
        verification_summary_hash=data["verification_summary_hash"],
        verified_answer_hash=data["verified_answer_hash"],
    )
    if result.lineage_hash != _stored_hash(data["lineage_hash"], "lineage_hash"):
        raise IntegrityError("stored candidate lineage hash mismatch")
    return result


def _metadata_from_jsonb(value: object) -> CorrectionCandidateMetadata:
    names = frozenset(CorrectionCandidateMetadata.__dataclass_fields__)
    data = _mapping(value, names, "candidate metadata")
    reasons = data["reason_codes"]
    if not isinstance(reasons, list):
        raise ContractValidationError("candidate reason_codes must be an array")
    try:
        trigger = CorrectionCandidateTrigger(data["trigger"])
        reason_codes = tuple(CorrectionCandidateReasonCode(item) for item in reasons)
    except (TypeError, ValueError) as exc:
        raise ContractValidationError("candidate metadata enum is invalid") from exc
    result = CorrectionCandidateMetadata(
        schema_version=data["schema_version"],
        trigger=trigger,
        producer_id=data["producer_id"],
        producer_version=data["producer_version"],
        reason_codes=reason_codes,
    )
    if result.metadata_hash != _stored_hash(data["metadata_hash"], "metadata_hash"):
        raise IntegrityError("stored candidate metadata hash mismatch")
    return result


def parse_correction_candidate_submission(
    value: Mapping[str, Any],
) -> CorrectionCandidateSubmission:
    """Strictly reconstruct and verify a candidate submission from JSONB data."""

    names = frozenset(CorrectionCandidateSubmission.__dataclass_fields__)
    data = _mapping(value, names, "candidate submission")
    result = CorrectionCandidateSubmission(
        schema_version=data["schema_version"],
        candidate=_candidate_from_jsonb(data["candidate"]),
        run_identity=_kernel_run_from_jsonb(data["run_identity"]),
        target_slot_binding=_target_binding_from_jsonb(data["target_slot_binding"]),
        lineage=_lineage_from_jsonb(data["lineage"]),
        metadata=_metadata_from_jsonb(data["metadata"]),
        policy_digest=data["policy_digest"],
        idempotency_key=data["idempotency_key"],
        submitted_at=_datetime(data["submitted_at"], "submitted_at"),
    )
    stored = {
        "candidate_text_sha256": _stored_hash(
            data["candidate_text_sha256"], "candidate_text_sha256"
        ),
        "run_identity_digest": _stored_hash(
            data["run_identity_digest"], "run_identity_digest"
        ),
        "submission_id": _stored_id(data["submission_id"], "submission_id"),
        "semantic_deduplication_key": _stored_id(
            data["semantic_deduplication_key"], "semantic_deduplication_key"
        ),
        "submission_hash": _stored_hash(data["submission_hash"], "submission_hash"),
    }
    for name, expected in stored.items():
        if getattr(result, name) != expected:
            raise IntegrityError(f"stored candidate {name} mismatch")
    verify_correction_candidate_submission(result)
    return result


def parse_correction_candidate_envelope(
    value: Mapping[str, Any],
) -> CorrectionCandidateEnvelope:
    """Strictly reconstruct and verify a complete envelope from JSONB data."""

    names = frozenset(CorrectionCandidateEnvelope.__dataclass_fields__)
    data = _mapping(value, names, "candidate envelope")
    result = CorrectionCandidateEnvelope(
        schema_version=data["schema_version"],
        contract_type=data["contract_type"],
        submission=parse_correction_candidate_submission(data["submission"]),
        policy=_policy_from_jsonb(data["policy"]),
    )
    if result.candidate_id != _stored_id(data["candidate_id"], "candidate_id"):
        raise IntegrityError("stored candidate ID mismatch")
    if result.envelope_id != _stored_id(data["envelope_id"], "envelope_id"):
        raise IntegrityError("stored candidate envelope ID mismatch")
    if result.envelope_hash != _stored_hash(data["envelope_hash"], "envelope_hash"):
        raise IntegrityError("stored candidate envelope hash mismatch")
    verify_correction_candidate_envelope(result)
    return result


def parse_correction_candidate_intake_receipt(
    value: Mapping[str, Any],
) -> CorrectionCandidateIntakeReceipt:
    """Strictly reconstruct and verify a candidate receipt from JSONB data."""

    names = frozenset(CorrectionCandidateIntakeReceipt.__dataclass_fields__)
    data = _mapping(value, names, "candidate receipt")
    try:
        disposition = CorrectionCandidateIntakeDisposition(data["disposition"])
        reason = CorrectionCandidateReasonCode(data["reason"])
    except (TypeError, ValueError) as exc:
        raise ContractValidationError("candidate receipt enum is invalid") from exc
    result = CorrectionCandidateIntakeReceipt(
        schema_version=data["schema_version"],
        receipt_id=data["receipt_id"],
        submission_id=data["submission_id"],
        submission_hash=data["submission_hash"],
        envelope_id=data["envelope_id"],
        envelope_hash=data["envelope_hash"],
        candidate_id=data["candidate_id"],
        candidate_event_id=data["candidate_event_id"],
        candidate_content_hash=data["candidate_content_hash"],
        semantic_deduplication_key=data["semantic_deduplication_key"],
        tenant_id=data["tenant_id"],
        owner_user_id=data["owner_user_id"],
        personal_memory_space_id=data["personal_memory_space_id"],
        idempotency_key=data["idempotency_key"],
        disposition=disposition,
        reason=reason,
        accepted_at=_datetime(data["accepted_at"], "accepted_at"),
    )
    if result.receipt_hash != _stored_hash(data["receipt_hash"], "receipt_hash"):
        raise IntegrityError("stored candidate receipt hash mismatch")
    verify_correction_candidate_intake_receipt(result)
    return result


__all__ = [
    "CORRECTION_CANDIDATE_ENVELOPE_CONTRACT_TYPE",
    "CORRECTION_CANDIDATE_INTAKE_POLICY_ID",
    "CORRECTION_CANDIDATE_INTAKE_POLICY_VERSION",
    "CORRECTION_CANDIDATE_SOURCE_ALLOWLIST",
    "CORRECTION_CANDIDATE_TARGET_STATES",
    "CorrectionCandidateEnvelope",
    "CorrectionCandidateIntakeDisposition",
    "CorrectionCandidateIntakeError",
    "CorrectionCandidateIntakePolicy",
    "CorrectionCandidateIntakeReceipt",
    "CorrectionCandidateMetadata",
    "CorrectionCandidateReasonCode",
    "CorrectionCandidateRouteResultLineage",
    "CorrectionCandidateSubmission",
    "CorrectionCandidateTargetBinding",
    "CorrectionCandidateTrigger",
    "MAXIMUM_CANDIDATE_CLAIMS",
    "MAXIMUM_CANDIDATE_CLAIM_UTF8_BYTES",
    "MAXIMUM_CANDIDATE_CORRECTION_UTF8_BYTES",
    "MAXIMUM_CANDIDATE_ENVELOPE_BYTES",
    "MAXIMUM_CANDIDATE_EVIDENCE_REFERENCES",
    "MAXIMUM_CANDIDATE_REASON_CODES",
    "MAXIMUM_CANDIDATES_PER_SLOT",
    "MAXIMUM_CANDIDATE_BYTES_PER_SLOT",
    "STEP28_SCHEMA_VERSION",
    "build_correction_candidate_envelope",
    "build_correction_candidate_intake_receipt",
    "correction_candidate_envelope_to_jsonb",
    "correction_candidate_receipt_to_jsonb",
    "correction_candidate_semantic_deduplication_key",
    "correction_candidate_submission_id",
    "load_correction_candidate_intake_policy",
    "parse_correction_candidate_envelope",
    "parse_correction_candidate_intake_receipt",
    "parse_correction_candidate_submission",
    "parse_correction_candidate_target_binding",
    "verify_correction_candidate_envelope",
    "verify_correction_candidate_intake_policy",
    "verify_correction_candidate_intake_receipt",
    "verify_correction_candidate_lineage",
    "verify_correction_candidate_metadata",
    "verify_correction_candidate_submission",
    "verify_correction_candidate_target_binding",
]
