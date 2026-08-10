"""Step 31 owner-private ACTIVE patch retrieval contracts and policy.

The contracts in this module expose approved Personal Memory only as bounded
private personalization context.  They never promote it to canonical evidence,
mutate patch state, approve, commit, activate, retrieve new evidence, or grant
execution authority.
"""

from __future__ import annotations

import hashlib
import unicodedata
from dataclasses import dataclass, field, fields as dataclass_fields
from datetime import datetime
from typing import Any, Mapping, Sequence

from aioa_memory_kernel.contracts.enums import (
    EvidenceStatus,
    PatchState,
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
    canonical_json_bytes,
    canonical_sha256,
    ensure_utc,
    require_sha256_hex,
    to_canonical_data,
    verify_canonical_hash,
)
from aioa_memory_kernel.modeling import ProviderIdentity
from aioa_memory_kernel.persistence.errors import PersistenceError
from aioa_memory_kernel.routing import KnowledgeRouteResult, verify_route_hash
from aioa_memory_kernel.temporal import (
    FreshnessStatus,
    TemporalApplicability,
    TemporalQueryMode,
    TemporalResolutionResult,
    verify_temporal_result_hash,
)

from .lifecycle import (
    PersonalMemoryPatchLifecycleState,
    verify_personal_memory_patch_lifecycle_state,
)
from .models import (
    PersonalMemoryBindingMode,
    PersonalMemoryHatSlot,
    PersonalMemoryModelBinding,
    verify_model_binding_hash,
    verify_slot_hash,
)


STEP31_SCHEMA_VERSION = "1.0.0"
ACTIVE_PATCH_RETRIEVAL_POLICY_ID = "active-personal-memory-retrieval-1a"
ACTIVE_PATCH_RETRIEVAL_POLICY_VERSION = "1"
PRIVATE_MEMORY_CONTEXT_CLASSIFICATION = (
    "PRIVATE_USER_MEMORY_NON_CANONICAL_PERSONALIZATION"
)
DEFAULT_ACTIVE_PATCH_RESULTS = 8
MAXIMUM_ACTIVE_PATCH_RESULTS = 32
MAXIMUM_ACTIVE_PATCH_CANDIDATES = 128
MAXIMUM_QUERY_TEXT_BYTES = 64 * 1024
MAXIMUM_LOGICAL_ID_BYTES = 255
MAXIMUM_PATCH_TEXT_BYTES = 16 * 1024


class CanonicalEvidenceCompatibility(StableStringEnum):
    """Computed query-time relationship to current canonical evidence."""

    MATCH = "MATCH"
    CONFLICT = "CONFLICT"
    UNCONFIRMED = "UNCONFIRMED"


class Step31ReasonCode(StableStringEnum):
    ACTIVE_PATCH_ELIGIBLE = "ACTIVE_PATCH_ELIGIBLE"
    ACTIVE_PATCH_RETRIEVED = "ACTIVE_PATCH_RETRIEVED"
    NO_ACTIVE_PATCH = "NO_ACTIVE_PATCH"
    PATCH_NOT_ACTIVE = "PATCH_NOT_ACTIVE"
    PATCH_ACTIVATION_RECEIPT_INVALID = "PATCH_ACTIVATION_RECEIPT_INVALID"
    PATCH_COMMIT_RECEIPT_INVALID = "PATCH_COMMIT_RECEIPT_INVALID"
    PATCH_APPROVAL_LINEAGE_INVALID = "PATCH_APPROVAL_LINEAGE_INVALID"
    PATCH_CONTENT_HASH_MISMATCH = "PATCH_CONTENT_HASH_MISMATCH"
    TENANT_MISMATCH = "TENANT_MISMATCH"
    OWNER_MISMATCH = "OWNER_MISMATCH"
    SLOT_MISMATCH = "SLOT_MISMATCH"
    SLOT_NOT_ELIGIBLE = "SLOT_NOT_ELIGIBLE"
    SCOPE_MISMATCH = "SCOPE_MISMATCH"
    TEMPORAL_MATCH = "TEMPORAL_MATCH"
    TEMPORAL_MISMATCH = "TEMPORAL_MISMATCH"
    TEMPORAL_UNKNOWN = "TEMPORAL_UNKNOWN"
    MODEL_BINDING_MATCH = "MODEL_BINDING_MATCH"
    MODEL_BINDING_MISMATCH = "MODEL_BINDING_MISMATCH"
    CANONICAL_EVIDENCE_MATCH = "CANONICAL_EVIDENCE_MATCH"
    CANONICAL_EVIDENCE_CONFLICT = "CANONICAL_EVIDENCE_CONFLICT"
    PATCH_SUPPRESSED_BY_CANONICAL_EVIDENCE = (
        "PATCH_SUPPRESSED_BY_CANONICAL_EVIDENCE"
    )
    RESULT_TRUNCATED = "RESULT_TRUNCATED"
    INPUT_INTEGRITY_INVALID = "INPUT_INTEGRITY_INVALID"


class ActivePatchRetrievalError(PersistenceError):
    """Sanitized, fail-closed Step 31 boundary error."""

    def __init__(self, reason_code: Step31ReasonCode) -> None:
        if not isinstance(reason_code, Step31ReasonCode):
            raise TypeError("reason_code must be Step31ReasonCode")
        super().__init__(
            "Active Personal Memory retrieval rejected",
            operation_kind="PERSONAL_MEMORY_ACTIVE_PATCH_RETRIEVAL",
            sanitized_code=reason_code.value,
        )
        self.reason_code = reason_code


def _text(value: object, field_name: str, maximum_bytes: int) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or unicodedata.normalize("NFC", value) != value
        or len(value.encode("utf-8")) > maximum_bytes
        or any(ord(character) < 32 for character in value)
    ):
        raise ContractValidationError(
            f"{field_name} must be bounded canonical NFC text"
        )
    return value


def _logical_id(value: object, field_name: str) -> str:
    return _text(value, field_name, MAXIMUM_LOGICAL_ID_BYTES)


def _optional_text(value: object, field_name: str) -> str | None:
    return None if value is None else _text(value, field_name, 512)


def _optional_timestamp(value: object, field_name: str) -> datetime | None:
    return None if value is None else ensure_utc(value, field_name)


def _scope_tuple(value: object) -> tuple[ScopeDimension, ...]:
    if not isinstance(value, (tuple, list)) or not value:
        raise ContractValidationError("effective scope must be non-empty")
    result = tuple(value)
    if any(not isinstance(item, ScopeDimension) for item in result):
        raise ContractValidationError("effective scope must be typed")
    if result != tuple(sorted(result, key=lambda item: item.name)):
        raise ContractValidationError("effective scope must be ordered")
    if len({item.name for item in result}) != len(result):
        raise ContractValidationError("effective scope names must be unique")
    return result


def _hash_tuple(value: object, field_name: str, maximum: int) -> tuple[str, ...]:
    if not isinstance(value, (tuple, list)):
        raise ContractValidationError(f"{field_name} must be ordered")
    result = tuple(value)
    if len(result) > maximum or result != tuple(sorted(set(result))):
        raise ContractValidationError(f"{field_name} must be unique and ordered")
    for item in result:
        require_sha256_hex(item, field_name)
    return result


def _reason_tuple(value: object) -> tuple[Step31ReasonCode, ...]:
    if not isinstance(value, (tuple, list)) or not value:
        raise ContractValidationError("Step 31 reason codes must be non-empty")
    result = tuple(value)
    if any(not isinstance(item, Step31ReasonCode) for item in result):
        raise ContractValidationError("Step 31 reason code is invalid")
    expected = tuple(sorted(set(result), key=lambda item: item.value))
    if result != expected:
        raise ContractValidationError("Step 31 reason codes must be unique and ordered")
    return result


def _boolean(value: object, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ContractValidationError(f"{field_name} must be boolean")
    return value


def _provider_identity(value: object) -> ProviderIdentity:
    if not isinstance(value, ProviderIdentity):
        raise ContractValidationError("model_identity must reuse ProviderIdentity")
    reconstructed = ProviderIdentity(
        provider_id=value.provider_id,
        adapter_version=value.adapter_version,
        model_id=value.model_id,
        model_revision_or_declared_version=(
            value.model_revision_or_declared_version
        ),
        endpoint_class=value.endpoint_class,
        tooling_disabled=value.tooling_disabled,
        function_calling_disabled=value.function_calling_disabled,
        web_browsing_disabled=value.web_browsing_disabled,
        code_execution_disabled=value.code_execution_disabled,
        immutable_model_revision=value.immutable_model_revision,
    )
    if reconstructed.identity_digest != value.identity_digest:
        raise IntegrityError("provider identity digest mismatch")
    return value


def _scope_equal(
    left: Sequence[ScopeDimension], right: Sequence[ScopeDimension]
) -> bool:
    return canonical_json_bytes(tuple(left)) == canonical_json_bytes(tuple(right))


def _verify_contract_reconstruction(value: object, field_name: str) -> None:
    """Re-run frozen-contract invariants after hash verification.

    A frozen dataclass can still be force-mutated by hostile in-process code.
    Recomputing its hash must not make such a value acceptable when relational
    or authority invariants enforced by ``__post_init__`` no longer hold.
    """

    try:
        reconstructed = type(value)(
            **{
                item.name: getattr(value, item.name)
                for item in dataclass_fields(value)
                if item.init
            }
        )
    except (ContractValidationError, IntegrityError, TypeError, ValueError) as exc:
        raise IntegrityError(f"{field_name} semantic reconstruction failed") from exc
    if reconstructed != value:
        raise IntegrityError(f"{field_name} semantic reconstruction mismatch")


@dataclass(frozen=True, slots=True)
class ActivePatchRetrievalPolicy:
    schema_version: str
    policy_id: str
    policy_version: str
    default_results: int
    maximum_results: int
    maximum_candidates: int
    require_active_state: bool
    require_current_canonical_support: bool
    canonical_evidence_authority: bool
    policy_digest: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != STEP31_SCHEMA_VERSION:
            raise ContractValidationError("unsupported retrieval policy schema")
        _logical_id(self.policy_id, "policy_id")
        _logical_id(self.policy_version, "policy_version")
        if (
            self.policy_id != ACTIVE_PATCH_RETRIEVAL_POLICY_ID
            or self.policy_version != ACTIVE_PATCH_RETRIEVAL_POLICY_VERSION
            or self.default_results != DEFAULT_ACTIVE_PATCH_RESULTS
            or self.maximum_results != MAXIMUM_ACTIVE_PATCH_RESULTS
            or self.maximum_candidates != MAXIMUM_ACTIVE_PATCH_CANDIDATES
            or self.require_active_state is not True
            or self.require_current_canonical_support is not True
            or self.canonical_evidence_authority is not False
        ):
            raise ContractValidationError("retrieval policy differs from Step 31 1A")
        object.__setattr__(
            self,
            "policy_digest",
            canonical_sha256(self, exclude_fields=("policy_digest",)),
        )


def load_active_patch_retrieval_policy() -> ActivePatchRetrievalPolicy:
    return ActivePatchRetrievalPolicy(
        schema_version=STEP31_SCHEMA_VERSION,
        policy_id=ACTIVE_PATCH_RETRIEVAL_POLICY_ID,
        policy_version=ACTIVE_PATCH_RETRIEVAL_POLICY_VERSION,
        default_results=DEFAULT_ACTIVE_PATCH_RESULTS,
        maximum_results=MAXIMUM_ACTIVE_PATCH_RESULTS,
        maximum_candidates=MAXIMUM_ACTIVE_PATCH_CANDIDATES,
        require_active_state=True,
        require_current_canonical_support=True,
        canonical_evidence_authority=False,
    )


@dataclass(frozen=True, slots=True)
class ActivePatchRetrievalRequest:
    schema_version: str
    request_id: str
    tenant_id: str
    user_id: str
    route_hash: str
    selected_hat_id: str | None
    selected_hat_version: str | None
    selected_manifest_digest: str | None
    effective_scope: tuple[ScopeDimension, ...]
    personal_memory_space_id: str
    model_identity: ProviderIdentity
    query_text_digest: str
    temporal_resolution_hash: str
    temporal_mode: TemporalQueryMode
    knowledge_as_of: datetime | None
    evaluation_as_of: datetime
    canonical_evidence_status: EvidenceStatus
    maximum_results: int
    policy_digest: str
    request_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != STEP31_SCHEMA_VERSION:
            raise ContractValidationError("unsupported retrieval request schema")
        for value, name in (
            (self.request_id, "request_id"),
            (self.tenant_id, "tenant_id"),
            (self.user_id, "user_id"),
            (self.personal_memory_space_id, "personal_memory_space_id"),
        ):
            _logical_id(value, name)
        for value, name in (
            (self.route_hash, "route_hash"),
            (self.query_text_digest, "query_text_digest"),
            (self.temporal_resolution_hash, "temporal_resolution_hash"),
            (self.policy_digest, "policy_digest"),
        ):
            require_sha256_hex(value, name)
        policy = load_active_patch_retrieval_policy()
        if self.policy_digest != policy.policy_digest:
            raise ContractValidationError("retrieval policy digest differs")
        selected = (
            self.selected_hat_id,
            self.selected_hat_version,
            self.selected_manifest_digest,
        )
        if any(item is None for item in selected) and any(
            item is not None for item in selected
        ):
            raise ContractValidationError("selected HAT identity must be complete")
        if self.selected_hat_id is not None:
            _logical_id(self.selected_hat_id, "selected_hat_id")
            _text(self.selected_hat_version, "selected_hat_version", 128)
            require_sha256_hex(
                self.selected_manifest_digest, "selected_manifest_digest"
            )
        object.__setattr__(self, "effective_scope", _scope_tuple(self.effective_scope))
        _provider_identity(self.model_identity)
        if not isinstance(self.temporal_mode, TemporalQueryMode):
            raise ContractValidationError("temporal_mode is invalid")
        object.__setattr__(
            self,
            "knowledge_as_of",
            _optional_timestamp(self.knowledge_as_of, "knowledge_as_of"),
        )
        object.__setattr__(
            self,
            "evaluation_as_of",
            ensure_utc(self.evaluation_as_of, "evaluation_as_of"),
        )
        if not isinstance(self.canonical_evidence_status, EvidenceStatus):
            raise ContractValidationError("canonical_evidence_status is invalid")
        if (
            not isinstance(self.maximum_results, int)
            or isinstance(self.maximum_results, bool)
            or not 1 <= self.maximum_results <= policy.maximum_results
        ):
            raise ContractValidationError("maximum_results is outside policy")
        object.__setattr__(
            self,
            "request_hash",
            canonical_sha256(self, exclude_fields=("request_hash",)),
        )


@dataclass(frozen=True, slots=True)
class StoredActivePatchCandidate:
    """Read-only database carrier reconstructed before applicability checks."""

    lifecycle_state: PersonalMemoryPatchLifecycleState
    active: bool
    revoked: bool
    valid_from: datetime | None
    valid_until: datetime | None
    expires_at: datetime | None
    row_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.lifecycle_state, PersonalMemoryPatchLifecycleState):
            raise ContractValidationError("lifecycle_state must be Step 30 typed state")
        verify_personal_memory_patch_lifecycle_state(self.lifecycle_state)
        if self.lifecycle_state.committed_patch is None:
            raise ContractValidationError("retrieval carrier requires a committed patch")
        _boolean(self.active, "active")
        _boolean(self.revoked, "revoked")
        valid_from = _optional_timestamp(self.valid_from, "valid_from")
        valid_until = _optional_timestamp(self.valid_until, "valid_until")
        expires_at = _optional_timestamp(self.expires_at, "expires_at")
        if valid_from is not None and valid_until is not None and valid_from > valid_until:
            raise ContractValidationError("patch validity interval is invalid")
        object.__setattr__(self, "valid_from", valid_from)
        object.__setattr__(self, "valid_until", valid_until)
        object.__setattr__(self, "expires_at", expires_at)
        object.__setattr__(
            self,
            "row_hash",
            canonical_sha256(self, exclude_fields=("row_hash",)),
        )


@dataclass(frozen=True, slots=True)
class ActivePatchAssessment:
    schema_version: str
    request_hash: str
    patch_id: str
    patch_hash: str
    state: PatchState
    tenant_match: bool
    owner_match: bool
    slot_match: bool
    scope_match: bool
    temporal_match: bool
    model_binding_match: bool
    canonical_evidence_compatibility: CanonicalEvidenceCompatibility
    eligible: bool
    reason_codes: tuple[Step31ReasonCode, ...]
    assessment_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != STEP31_SCHEMA_VERSION:
            raise ContractValidationError("unsupported assessment schema")
        require_sha256_hex(self.request_hash, "request_hash")
        _logical_id(self.patch_id, "patch_id")
        require_sha256_hex(self.patch_hash, "patch_hash")
        if not isinstance(self.state, PatchState):
            raise ContractValidationError("patch state is invalid")
        for name in (
            "tenant_match",
            "owner_match",
            "slot_match",
            "scope_match",
            "temporal_match",
            "model_binding_match",
            "eligible",
        ):
            _boolean(getattr(self, name), name)
        if not isinstance(
            self.canonical_evidence_compatibility,
            CanonicalEvidenceCompatibility,
        ):
            raise ContractValidationError("canonical evidence compatibility is invalid")
        reasons = _reason_tuple(self.reason_codes)
        object.__setattr__(self, "reason_codes", reasons)
        expected = (
            self.state is PatchState.ACTIVE
            and self.tenant_match
            and self.owner_match
            and self.slot_match
            and self.scope_match
            and self.temporal_match
            and self.model_binding_match
            and self.canonical_evidence_compatibility
            is CanonicalEvidenceCompatibility.MATCH
        )
        if self.eligible is not expected:
            raise ContractValidationError("eligible differs from fail-closed gates")
        required = (
            Step31ReasonCode.ACTIVE_PATCH_ELIGIBLE
            if expected
            else None
        )
        if required is not None and required not in reasons:
            raise ContractValidationError("eligible assessment reason is missing")
        if required is None and Step31ReasonCode.ACTIVE_PATCH_ELIGIBLE in reasons:
            raise ContractValidationError("ineligible assessment claims eligibility")
        expected_reason_presence = {
            Step31ReasonCode.TENANT_MISMATCH: not self.tenant_match,
            Step31ReasonCode.OWNER_MISMATCH: not self.owner_match,
            Step31ReasonCode.SCOPE_MISMATCH: not self.scope_match,
            Step31ReasonCode.MODEL_BINDING_MATCH: self.model_binding_match,
            Step31ReasonCode.MODEL_BINDING_MISMATCH: not self.model_binding_match,
            Step31ReasonCode.CANONICAL_EVIDENCE_MATCH: (
                self.canonical_evidence_compatibility
                is CanonicalEvidenceCompatibility.MATCH
            ),
            Step31ReasonCode.CANONICAL_EVIDENCE_CONFLICT: (
                self.canonical_evidence_compatibility
                is CanonicalEvidenceCompatibility.CONFLICT
            ),
            Step31ReasonCode.PATCH_SUPPRESSED_BY_CANONICAL_EVIDENCE: (
                self.canonical_evidence_compatibility
                is not CanonicalEvidenceCompatibility.MATCH
            ),
        }
        if any((code in reasons) is not present for code, present in expected_reason_presence.items()):
            raise ContractValidationError("assessment reasons differ from gate results")
        if (
            self.state is not PatchState.ACTIVE
            and Step31ReasonCode.PATCH_NOT_ACTIVE not in reasons
        ):
            raise ContractValidationError("non-active state reason is missing")
        slot_failure_reasons = {
            Step31ReasonCode.SLOT_MISMATCH,
            Step31ReasonCode.SLOT_NOT_ELIGIBLE,
        }.intersection(reasons)
        if self.slot_match is bool(slot_failure_reasons):
            raise ContractValidationError("slot reasons differ from gate result")
        temporal_reasons = {
            Step31ReasonCode.TEMPORAL_MATCH,
            Step31ReasonCode.TEMPORAL_MISMATCH,
            Step31ReasonCode.TEMPORAL_UNKNOWN,
        }.intersection(reasons)
        if len(temporal_reasons) != 1 or (
            self.temporal_match
            is not (Step31ReasonCode.TEMPORAL_MATCH in temporal_reasons)
        ):
            raise ContractValidationError("temporal reasons differ from gate result")
        object.__setattr__(
            self,
            "assessment_hash",
            canonical_sha256(self, exclude_fields=("assessment_hash",)),
        )


@dataclass(frozen=True, slots=True)
class RetrievedActivePatch:
    schema_version: str
    request_hash: str
    patch_id: str
    patch_hash: str
    patch_statement: str
    patch_statement_sha256: str
    patch_scope: tuple[ScopeDimension, ...]
    candidate_hash: str
    proposal_hash: str
    evidence_binding_hash: str
    validation_receipt_hash: str
    approval_receipt_hash: str
    commit_receipt_hash: str
    activation_receipt_hash: str
    origin_model_binding_id: str
    origin_model_binding_hash: str
    retrieval_model_binding_id: str
    retrieval_model_binding_hash: str
    assessment_hash: str
    reason_codes: tuple[Step31ReasonCode, ...]
    canonical_evidence_authority: bool = False
    execution_authority: bool = False
    retrieved_patch_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != STEP31_SCHEMA_VERSION:
            raise ContractValidationError("unsupported retrieved patch schema")
        for value, name in (
            (self.patch_id, "patch_id"),
            (self.origin_model_binding_id, "origin_model_binding_id"),
            (self.retrieval_model_binding_id, "retrieval_model_binding_id"),
        ):
            _logical_id(value, name)
        for value, name in (
            (self.request_hash, "request_hash"),
            (self.patch_hash, "patch_hash"),
            (self.patch_statement_sha256, "patch_statement_sha256"),
            (self.candidate_hash, "candidate_hash"),
            (self.proposal_hash, "proposal_hash"),
            (self.evidence_binding_hash, "evidence_binding_hash"),
            (self.validation_receipt_hash, "validation_receipt_hash"),
            (self.approval_receipt_hash, "approval_receipt_hash"),
            (self.commit_receipt_hash, "commit_receipt_hash"),
            (self.activation_receipt_hash, "activation_receipt_hash"),
            (self.origin_model_binding_hash, "origin_model_binding_hash"),
            (self.retrieval_model_binding_hash, "retrieval_model_binding_hash"),
            (self.assessment_hash, "assessment_hash"),
        ):
            require_sha256_hex(value, name)
        statement = _text(
            self.patch_statement, "patch_statement", MAXIMUM_PATCH_TEXT_BYTES
        )
        if hashlib.sha256(statement.encode("utf-8")).hexdigest() != (
            self.patch_statement_sha256
        ):
            raise ContractValidationError("retrieved patch statement hash mismatch")
        object.__setattr__(self, "patch_scope", _scope_tuple(self.patch_scope))
        reasons = _reason_tuple(self.reason_codes)
        expected_reasons = tuple(
            sorted(
                (
                    Step31ReasonCode.ACTIVE_PATCH_ELIGIBLE,
                    Step31ReasonCode.ACTIVE_PATCH_RETRIEVED,
                    Step31ReasonCode.CANONICAL_EVIDENCE_MATCH,
                    Step31ReasonCode.MODEL_BINDING_MATCH,
                    Step31ReasonCode.TEMPORAL_MATCH,
                ),
                key=lambda item: item.value,
            )
        )
        if reasons != expected_reasons:
            raise ContractValidationError("retrieved patch reasons differ")
        object.__setattr__(self, "reason_codes", reasons)
        if self.canonical_evidence_authority is not False:
            raise ContractValidationError("Personal Memory cannot become evidence")
        if self.execution_authority is not False:
            raise ContractValidationError("Personal Memory cannot grant execution")
        object.__setattr__(
            self,
            "retrieved_patch_hash",
            canonical_sha256(self, exclude_fields=("retrieved_patch_hash",)),
        )


@dataclass(frozen=True, slots=True)
class ActivePatchRetrievalResult:
    schema_version: str
    request_id: str
    request_hash: str
    tenant_id: str
    user_id: str
    route_hash: str
    personal_memory_space_id: str
    model_identity_digest: str
    eligible_patches: tuple[RetrievedActivePatch, ...]
    excluded_assessments: tuple[ActivePatchAssessment, ...]
    truncated_patch_hashes: tuple[str, ...]
    considered_count: int
    truncated: bool
    reason_codes: tuple[Step31ReasonCode, ...]
    policy_digest: str
    result_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != STEP31_SCHEMA_VERSION:
            raise ContractValidationError("unsupported retrieval result schema")
        for value, name in (
            (self.request_id, "request_id"),
            (self.tenant_id, "tenant_id"),
            (self.user_id, "user_id"),
            (self.personal_memory_space_id, "personal_memory_space_id"),
        ):
            _logical_id(value, name)
        for value, name in (
            (self.request_hash, "request_hash"),
            (self.route_hash, "route_hash"),
            (self.model_identity_digest, "model_identity_digest"),
            (self.policy_digest, "policy_digest"),
        ):
            require_sha256_hex(value, name)
        if self.policy_digest != load_active_patch_retrieval_policy().policy_digest:
            raise ContractValidationError("retrieval result policy differs")
        patches = tuple(self.eligible_patches)
        if (
            len(patches) > MAXIMUM_ACTIVE_PATCH_RESULTS
            or any(not isinstance(item, RetrievedActivePatch) for item in patches)
            or patches != tuple(sorted(patches, key=lambda item: item.patch_id))
            or len({item.patch_id for item in patches}) != len(patches)
            or any(item.request_hash != self.request_hash for item in patches)
        ):
            raise ContractValidationError("eligible patches are invalid")
        object.__setattr__(self, "eligible_patches", patches)
        assessments = tuple(self.excluded_assessments)
        if (
            len(assessments) > MAXIMUM_ACTIVE_PATCH_CANDIDATES
            or any(not isinstance(item, ActivePatchAssessment) for item in assessments)
            or any(item.eligible for item in assessments)
            or assessments != tuple(sorted(assessments, key=lambda item: item.patch_id))
            or any(item.request_hash != self.request_hash for item in assessments)
            or {item.patch_id for item in patches}.intersection(
                item.patch_id for item in assessments
            )
        ):
            raise ContractValidationError("excluded assessments are invalid")
        object.__setattr__(self, "excluded_assessments", assessments)
        object.__setattr__(
            self,
            "truncated_patch_hashes",
            _hash_tuple(
                self.truncated_patch_hashes,
                "truncated_patch_hashes",
                MAXIMUM_ACTIVE_PATCH_CANDIDATES,
            ),
        )
        if (
            not isinstance(self.considered_count, int)
            or isinstance(self.considered_count, bool)
            or not 0 <= self.considered_count <= MAXIMUM_ACTIVE_PATCH_CANDIDATES
            or self.considered_count < len(patches) + len(assessments)
        ):
            raise ContractValidationError("considered_count is invalid")
        _boolean(self.truncated, "truncated")
        if self.truncated is not bool(self.truncated_patch_hashes):
            raise ContractValidationError("truncated flag differs from omitted patches")
        reasons = _reason_tuple(self.reason_codes)
        expected_primary = (
            Step31ReasonCode.ACTIVE_PATCH_RETRIEVED
            if patches
            else Step31ReasonCode.NO_ACTIVE_PATCH
        )
        expected_reasons = {expected_primary}
        if self.truncated:
            expected_reasons.add(Step31ReasonCode.RESULT_TRUNCATED)
        if set(reasons) != expected_reasons:
            raise ContractValidationError("retrieval result reasons differ")
        object.__setattr__(self, "reason_codes", reasons)
        object.__setattr__(
            self,
            "result_hash",
            canonical_sha256(self, exclude_fields=("result_hash",)),
        )


@dataclass(frozen=True, slots=True)
class PersonalMemoryContextEnvelope:
    schema_version: str
    request_id: str
    request_hash: str
    retrieval_result_hash: str
    tenant_id: str
    owner_user_id: str
    route_hash: str
    personal_memory_space_id: str
    model_identity_digest: str
    query_text_digest: str
    classification: str
    ordered_active_patches: tuple[RetrievedActivePatch, ...]
    canonical_evidence_authority: bool
    source_authority_upgrade: bool
    execution_authority: bool
    context_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != STEP31_SCHEMA_VERSION:
            raise ContractValidationError("unsupported context envelope schema")
        for value, name in (
            (self.request_id, "request_id"),
            (self.tenant_id, "tenant_id"),
            (self.owner_user_id, "owner_user_id"),
            (self.personal_memory_space_id, "personal_memory_space_id"),
        ):
            _logical_id(value, name)
        for value, name in (
            (self.request_hash, "request_hash"),
            (self.retrieval_result_hash, "retrieval_result_hash"),
            (self.route_hash, "route_hash"),
            (self.model_identity_digest, "model_identity_digest"),
            (self.query_text_digest, "query_text_digest"),
        ):
            require_sha256_hex(value, name)
        if self.classification != PRIVATE_MEMORY_CONTEXT_CLASSIFICATION:
            raise ContractValidationError("private context classification differs")
        patches = tuple(self.ordered_active_patches)
        if (
            any(not isinstance(item, RetrievedActivePatch) for item in patches)
            or patches != tuple(sorted(patches, key=lambda item: item.patch_id))
            or len(patches) > MAXIMUM_ACTIVE_PATCH_RESULTS
            or any(item.request_hash != self.request_hash for item in patches)
        ):
            raise ContractValidationError("context patches must be deterministic")
        object.__setattr__(self, "ordered_active_patches", patches)
        if any(
            value is not False
            for value in (
                self.canonical_evidence_authority,
                self.source_authority_upgrade,
                self.execution_authority,
            )
        ):
            raise ContractValidationError("private context cannot carry authority")
        object.__setattr__(
            self,
            "context_hash",
            canonical_sha256(self, exclude_fields=("context_hash",)),
        )


def build_active_patch_retrieval_request(
    *,
    route: KnowledgeRouteResult,
    temporal_result: TemporalResolutionResult,
    personal_memory_space_id: str,
    model_identity: ProviderIdentity,
    query_text: str,
    maximum_results: int = DEFAULT_ACTIVE_PATCH_RESULTS,
    policy: ActivePatchRetrievalPolicy | None = None,
) -> ActivePatchRetrievalRequest:
    """Bind a later query without retaining its raw text."""

    try:
        verify_route_hash(route)
        verify_temporal_result_hash(temporal_result)
    except (ContractValidationError, IntegrityError) as exc:
        raise ActivePatchRetrievalError(
            Step31ReasonCode.INPUT_INTEGRITY_INVALID
        ) from exc
    if (
        temporal_result.request_id != route.request_id
        or temporal_result.tenant_id != route.tenant_id
        or temporal_result.user_id != route.user_id
        or temporal_result.route_hash != route.route_hash
        or temporal_result.selected_hat_id != route.selected_hat_id
        or temporal_result.selected_hat_version != route.selected_hat_version
        or temporal_result.selected_manifest_digest != route.selected_manifest_digest
        or not _scope_equal(temporal_result.effective_scope, route.effective_scope)
    ):
        raise ActivePatchRetrievalError(Step31ReasonCode.INPUT_INTEGRITY_INVALID)
    selected_policy = policy or load_active_patch_retrieval_policy()
    if selected_policy != load_active_patch_retrieval_policy():
        raise ContractValidationError("unsupported active retrieval policy")
    query = _text(query_text, "query_text", MAXIMUM_QUERY_TEXT_BYTES)
    return ActivePatchRetrievalRequest(
        schema_version=STEP31_SCHEMA_VERSION,
        request_id=route.request_id,
        tenant_id=route.tenant_id,
        user_id=route.user_id,
        route_hash=route.route_hash,
        selected_hat_id=route.selected_hat_id,
        selected_hat_version=route.selected_hat_version,
        selected_manifest_digest=route.selected_manifest_digest,
        effective_scope=route.effective_scope,
        personal_memory_space_id=personal_memory_space_id,
        model_identity=_provider_identity(model_identity),
        query_text_digest=hashlib.sha256(query.encode("utf-8")).hexdigest(),
        temporal_resolution_hash=temporal_result.result_hash,
        temporal_mode=temporal_result.temporal_mode,
        knowledge_as_of=temporal_result.knowledge_as_of,
        evaluation_as_of=temporal_result.evaluation_as_of,
        canonical_evidence_status=temporal_result.evidence_status,
        maximum_results=maximum_results,
        policy_digest=selected_policy.policy_digest,
    )


def verify_active_patch_retrieval_request(
    value: ActivePatchRetrievalRequest,
) -> None:
    verify_canonical_hash(
        value, value.request_hash, exclude_fields=("request_hash",)
    )
    _provider_identity(value.model_identity)
    for dimension in value.effective_scope:
        _verify_contract_reconstruction(dimension, "request scope dimension")
    _verify_contract_reconstruction(value, "active patch retrieval request")


def verify_stored_active_patch_candidate(
    value: StoredActivePatchCandidate,
) -> None:
    verify_personal_memory_patch_lifecycle_state(value.lifecycle_state)
    verify_canonical_hash(value, value.row_hash, exclude_fields=("row_hash",))
    _verify_contract_reconstruction(value, "stored active patch candidate")


def verify_active_patch_retrieval_inputs(
    request: ActivePatchRetrievalRequest,
    route: KnowledgeRouteResult,
    temporal_result: TemporalResolutionResult,
) -> None:
    try:
        verify_active_patch_retrieval_request(request)
        verify_route_hash(route)
        verify_temporal_result_hash(temporal_result)
    except (ContractValidationError, IntegrityError) as exc:
        raise ActivePatchRetrievalError(
            Step31ReasonCode.INPUT_INTEGRITY_INVALID
        ) from exc
    if (
        request.request_id != route.request_id
        or request.tenant_id != route.tenant_id
        or request.user_id != route.user_id
        or request.route_hash != route.route_hash
        or request.selected_hat_id != route.selected_hat_id
        or request.selected_hat_version != route.selected_hat_version
        or request.selected_manifest_digest != route.selected_manifest_digest
        or not _scope_equal(request.effective_scope, route.effective_scope)
        or temporal_result.request_id != request.request_id
        or temporal_result.tenant_id != request.tenant_id
        or temporal_result.user_id != request.user_id
        or temporal_result.route_hash != request.route_hash
        or temporal_result.selected_hat_id != request.selected_hat_id
        or temporal_result.selected_hat_version != request.selected_hat_version
        or temporal_result.selected_manifest_digest
        != request.selected_manifest_digest
        or temporal_result.result_hash != request.temporal_resolution_hash
        or temporal_result.temporal_mode is not request.temporal_mode
        or temporal_result.knowledge_as_of != request.knowledge_as_of
        or temporal_result.evaluation_as_of != request.evaluation_as_of
        or temporal_result.evidence_status is not request.canonical_evidence_status
        or not _scope_equal(temporal_result.effective_scope, request.effective_scope)
    ):
        raise ActivePatchRetrievalError(Step31ReasonCode.INPUT_INTEGRITY_INVALID)


def _matching_model_binding(
    slot: PersonalMemoryHatSlot,
    identity: ProviderIdentity,
) -> PersonalMemoryModelBinding | None:
    matches = tuple(
        binding
        for binding in slot.model_bindings
        if binding.enabled
        and binding.binding_mode is PersonalMemoryBindingMode.EXACT_MODEL
        and binding.provider_id == identity.provider_id
        and binding.model_id == identity.model_id
        and binding.model_revision_or_declared_version
        == identity.model_revision_or_declared_version
    )
    return matches[0] if len(matches) == 1 else None


def _current_canonical_compatibility(
    state: PersonalMemoryPatchLifecycleState,
    temporal_result: TemporalResolutionResult,
) -> CanonicalEvidenceCompatibility:
    binding = state.step29_state.evidence_binding
    if binding is None:
        return CanonicalEvidenceCompatibility.UNCONFIRMED
    if (
        temporal_result.evidence_status is EvidenceStatus.CONFLICTING
        or temporal_result.conflict_groups
    ):
        return CanonicalEvidenceCompatibility.CONFLICT
    if temporal_result.evidence_status is not EvidenceStatus.SUFFICIENT:
        return CanonicalEvidenceCompatibility.UNCONFIRMED
    selected = tuple(item for item in temporal_result.assessments if item.selected)
    if any(
        item.temporal_applicability is not TemporalApplicability.APPLICABLE
        or item.freshness_status is not FreshnessStatus.FRESH
        or item.conflict_group_id is not None
        for item in selected
    ):
        return CanonicalEvidenceCompatibility.CONFLICT
    current_identities = {
        (
            item.candidate_identity_hash,
            item.source_id,
            item.knowledge_version_id,
            item.chunk_id,
        )
        for item in selected
    }
    required_identities = {
        (
            item.candidate_identity_hash,
            item.source_id,
            item.knowledge_version_id,
            item.chunk_id,
        )
        for item in binding.ordered_evidence_references
    }
    return (
        CanonicalEvidenceCompatibility.MATCH
        if required_identities and required_identities.issubset(current_identities)
        else CanonicalEvidenceCompatibility.UNCONFIRMED
    )


def _temporal_match(
    candidate: StoredActivePatchCandidate,
    request: ActivePatchRetrievalRequest,
) -> tuple[bool, bool]:
    """Return (match, unknown) without mutating lifecycle state."""

    evaluation = request.evaluation_as_of
    if candidate.valid_from is not None and evaluation < candidate.valid_from:
        return False, False
    if candidate.valid_until is not None and evaluation >= candidate.valid_until:
        return False, False
    if candidate.expires_at is not None and evaluation >= candidate.expires_at:
        return False, False
    dimensions = tuple(
        item for item in request.effective_scope if item.name == "knowledge_as_of"
    )
    if dimensions:
        if (
            len(dimensions) != 1
            or dimensions[0].value_type is not ScopeValueType.TIMESTAMP
            or not isinstance(dimensions[0].value, datetime)
        ):
            return False, True
        if ensure_utc(dimensions[0].value, "knowledge_as_of") != evaluation:
            return False, False
    if request.temporal_mode in {TemporalQueryMode.AS_OF, TemporalQueryMode.FUTURE}:
        if request.knowledge_as_of is None:
            return False, True
    elif request.knowledge_as_of is not None:
        return False, True
    return True, False


def assess_active_patch(
    request: ActivePatchRetrievalRequest,
    *,
    temporal_result: TemporalResolutionResult,
    slot: PersonalMemoryHatSlot,
    candidate: StoredActivePatchCandidate,
) -> tuple[ActivePatchAssessment, PersonalMemoryModelBinding | None]:
    verify_active_patch_retrieval_request(request)
    verify_temporal_result_hash(temporal_result)
    if (
        temporal_result.request_id != request.request_id
        or temporal_result.tenant_id != request.tenant_id
        or temporal_result.user_id != request.user_id
        or temporal_result.route_hash != request.route_hash
        or temporal_result.selected_hat_id != request.selected_hat_id
        or temporal_result.selected_hat_version != request.selected_hat_version
        or temporal_result.selected_manifest_digest
        != request.selected_manifest_digest
        or temporal_result.result_hash != request.temporal_resolution_hash
        or temporal_result.temporal_mode is not request.temporal_mode
        or temporal_result.knowledge_as_of != request.knowledge_as_of
        or temporal_result.evidence_status is not request.canonical_evidence_status
        or temporal_result.evaluation_as_of != request.evaluation_as_of
        or not _scope_equal(temporal_result.effective_scope, request.effective_scope)
    ):
        raise ActivePatchRetrievalError(Step31ReasonCode.INPUT_INTEGRITY_INVALID)
    verify_slot_hash(slot)
    try:
        _verify_contract_reconstruction(slot, "Personal Memory retrieval slot")
        for model_binding in slot.model_bindings:
            verify_model_binding_hash(model_binding)
            _verify_contract_reconstruction(
                model_binding, "Personal Memory model binding"
            )
        verify_stored_active_patch_candidate(candidate)
    except (ContractValidationError, IntegrityError) as exc:
        raise ActivePatchRetrievalError(
            Step31ReasonCode.PATCH_ACTIVATION_RECEIPT_INVALID
        ) from exc
    state = candidate.lifecycle_state
    patch = state.committed_patch
    if patch is None:
        raise ActivePatchRetrievalError(
            Step31ReasonCode.PATCH_COMMIT_RECEIPT_INVALID
        )
    tenant_match = patch.tenant_id == request.tenant_id == slot.tenant_id
    owner_match = patch.owner_user_id == request.user_id == slot.owner_user_id
    slot_match = (
        patch.personal_memory_space_id
        == request.personal_memory_space_id
        == slot.personal_memory_space_id
        and patch.hat_scope_id == slot.hat_scope_id
    )
    scope_match = _scope_equal(patch.patch_scope, request.effective_scope)
    temporal_match, temporal_unknown = _temporal_match(candidate, request)
    origin_binding = next(
        (
            item
            for item in slot.model_bindings
            if item.enabled
            and item.binding_id == patch.model_binding_id
            and item.binding_hash == patch.model_binding_hash
        ),
        None,
    )
    retrieval_binding = _matching_model_binding(slot, request.model_identity)
    model_match = origin_binding is not None and retrieval_binding is not None
    compatibility = _current_canonical_compatibility(state, temporal_result)
    active = (
        state.state is PatchState.ACTIVE
        and candidate.active
        and not candidate.revoked
        and state.activation_receipt is not None
    )
    reasons: set[Step31ReasonCode] = set()
    if not active:
        reasons.add(Step31ReasonCode.PATCH_NOT_ACTIVE)
    if not tenant_match:
        reasons.add(Step31ReasonCode.TENANT_MISMATCH)
    if not owner_match:
        reasons.add(Step31ReasonCode.OWNER_MISMATCH)
    if not slot_match:
        reasons.add(Step31ReasonCode.SLOT_MISMATCH)
    if not slot.retrieval_eligible:
        reasons.add(Step31ReasonCode.SLOT_NOT_ELIGIBLE)
        slot_match = False
    if not scope_match:
        reasons.add(Step31ReasonCode.SCOPE_MISMATCH)
    if temporal_unknown:
        reasons.add(Step31ReasonCode.TEMPORAL_UNKNOWN)
    elif temporal_match:
        reasons.add(Step31ReasonCode.TEMPORAL_MATCH)
    else:
        reasons.add(Step31ReasonCode.TEMPORAL_MISMATCH)
    reasons.add(
        Step31ReasonCode.MODEL_BINDING_MATCH
        if model_match
        else Step31ReasonCode.MODEL_BINDING_MISMATCH
    )
    if compatibility is CanonicalEvidenceCompatibility.MATCH:
        reasons.add(Step31ReasonCode.CANONICAL_EVIDENCE_MATCH)
    elif compatibility is CanonicalEvidenceCompatibility.CONFLICT:
        reasons.update(
            {
                Step31ReasonCode.CANONICAL_EVIDENCE_CONFLICT,
                Step31ReasonCode.PATCH_SUPPRESSED_BY_CANONICAL_EVIDENCE,
            }
        )
    else:
        reasons.add(Step31ReasonCode.PATCH_SUPPRESSED_BY_CANONICAL_EVIDENCE)
    eligible = (
        active
        and tenant_match
        and owner_match
        and slot_match
        and scope_match
        and temporal_match
        and model_match
        and compatibility is CanonicalEvidenceCompatibility.MATCH
    )
    if eligible:
        reasons.add(Step31ReasonCode.ACTIVE_PATCH_ELIGIBLE)
    assessment = ActivePatchAssessment(
        schema_version=STEP31_SCHEMA_VERSION,
        request_hash=request.request_hash,
        patch_id=patch.patch_id,
        patch_hash=patch.patch_hash,
        state=state.state,
        tenant_match=tenant_match,
        owner_match=owner_match,
        slot_match=slot_match,
        scope_match=scope_match,
        temporal_match=temporal_match,
        model_binding_match=model_match,
        canonical_evidence_compatibility=compatibility,
        eligible=eligible,
        reason_codes=tuple(sorted(reasons, key=lambda item: item.value)),
    )
    return assessment, retrieval_binding


def retrieved_active_patch(
    request: ActivePatchRetrievalRequest,
    candidate: StoredActivePatchCandidate,
    assessment: ActivePatchAssessment,
    retrieval_binding: PersonalMemoryModelBinding,
) -> RetrievedActivePatch:
    verify_active_patch_retrieval_request(request)
    verify_stored_active_patch_candidate(candidate)
    verify_active_patch_assessment(assessment)
    verify_model_binding_hash(retrieval_binding)
    _verify_contract_reconstruction(
        retrieval_binding, "retrieval model binding"
    )
    state = candidate.lifecycle_state
    patch = state.committed_patch
    binding = state.step29_state.evidence_binding
    validation = state.step29_state.validation_receipt
    if (
        patch is None
        or binding is None
        or validation is None
        or state.commit_receipt is None
        or state.activation_receipt is None
    ):
        raise ActivePatchRetrievalError(
            Step31ReasonCode.PATCH_APPROVAL_LINEAGE_INVALID
        )
    if (
        not assessment.eligible
        or assessment.request_hash != request.request_hash
        or assessment.patch_id != patch.patch_id
        or assessment.patch_hash != patch.patch_hash
        or not retrieval_binding.enabled
        or retrieval_binding.binding_mode
        is not PersonalMemoryBindingMode.EXACT_MODEL
        or retrieval_binding.tenant_id != request.tenant_id
        or retrieval_binding.owner_user_id != request.user_id
        or retrieval_binding.personal_memory_space_id
        != request.personal_memory_space_id
        or retrieval_binding.provider_id != request.model_identity.provider_id
        or retrieval_binding.model_id != request.model_identity.model_id
        or retrieval_binding.model_revision_or_declared_version
        != request.model_identity.model_revision_or_declared_version
    ):
        raise ContractValidationError(
            "retrieved patch inputs differ from the eligible assessment"
        )
    return RetrievedActivePatch(
        schema_version=STEP31_SCHEMA_VERSION,
        request_hash=request.request_hash,
        patch_id=patch.patch_id,
        patch_hash=patch.patch_hash,
        patch_statement=patch.patch_statement,
        patch_statement_sha256=patch.patch_statement_sha256,
        patch_scope=patch.patch_scope,
        candidate_hash=state.proposal.candidate_hash,
        proposal_hash=patch.proposal_hash,
        evidence_binding_hash=binding.binding_hash,
        validation_receipt_hash=validation.receipt_hash,
        approval_receipt_hash=state.approval_receipt.receipt_hash,
        commit_receipt_hash=state.commit_receipt.receipt_hash,
        activation_receipt_hash=state.activation_receipt.receipt_hash,
        origin_model_binding_id=patch.model_binding_id,
        origin_model_binding_hash=patch.model_binding_hash,
        retrieval_model_binding_id=retrieval_binding.binding_id,
        retrieval_model_binding_hash=retrieval_binding.binding_hash,
        assessment_hash=assessment.assessment_hash,
        reason_codes=(
            Step31ReasonCode.ACTIVE_PATCH_ELIGIBLE,
            Step31ReasonCode.ACTIVE_PATCH_RETRIEVED,
            Step31ReasonCode.CANONICAL_EVIDENCE_MATCH,
            Step31ReasonCode.MODEL_BINDING_MATCH,
            Step31ReasonCode.TEMPORAL_MATCH,
        ),
    )


def build_active_patch_retrieval_result(
    request: ActivePatchRetrievalRequest,
    *,
    eligible_patches: Sequence[RetrievedActivePatch],
    excluded_assessments: Sequence[ActivePatchAssessment],
    truncated_patch_hashes: Sequence[str],
    considered_count: int,
) -> ActivePatchRetrievalResult:
    verify_active_patch_retrieval_request(request)
    for patch in eligible_patches:
        verify_retrieved_active_patch(patch)
    for assessment in excluded_assessments:
        verify_active_patch_assessment(assessment)
    patches = tuple(sorted(eligible_patches, key=lambda item: item.patch_id))
    excluded = tuple(sorted(excluded_assessments, key=lambda item: item.patch_id))
    truncated_hashes = tuple(sorted(set(truncated_patch_hashes)))
    reasons = {
        Step31ReasonCode.ACTIVE_PATCH_RETRIEVED
        if patches
        else Step31ReasonCode.NO_ACTIVE_PATCH
    }
    if truncated_hashes:
        reasons.add(Step31ReasonCode.RESULT_TRUNCATED)
    return ActivePatchRetrievalResult(
        schema_version=STEP31_SCHEMA_VERSION,
        request_id=request.request_id,
        request_hash=request.request_hash,
        tenant_id=request.tenant_id,
        user_id=request.user_id,
        route_hash=request.route_hash,
        personal_memory_space_id=request.personal_memory_space_id,
        model_identity_digest=request.model_identity.identity_digest,
        eligible_patches=patches,
        excluded_assessments=excluded,
        truncated_patch_hashes=truncated_hashes,
        considered_count=considered_count,
        truncated=bool(truncated_hashes),
        reason_codes=tuple(sorted(reasons, key=lambda item: item.value)),
        policy_digest=request.policy_digest,
    )


def build_personal_memory_context_envelope(
    request: ActivePatchRetrievalRequest,
    result: ActivePatchRetrievalResult,
) -> PersonalMemoryContextEnvelope:
    verify_active_patch_retrieval_request(request)
    verify_active_patch_retrieval_result(result)
    if (
        result.request_id != request.request_id
        or result.request_hash != request.request_hash
        or result.tenant_id != request.tenant_id
        or result.user_id != request.user_id
        or result.route_hash != request.route_hash
        or result.personal_memory_space_id != request.personal_memory_space_id
        or result.model_identity_digest != request.model_identity.identity_digest
    ):
        raise IntegrityError("retrieval result is detached from request")
    return PersonalMemoryContextEnvelope(
        schema_version=STEP31_SCHEMA_VERSION,
        request_id=request.request_id,
        request_hash=request.request_hash,
        retrieval_result_hash=result.result_hash,
        tenant_id=request.tenant_id,
        owner_user_id=request.user_id,
        route_hash=request.route_hash,
        personal_memory_space_id=request.personal_memory_space_id,
        model_identity_digest=request.model_identity.identity_digest,
        query_text_digest=request.query_text_digest,
        classification=PRIVATE_MEMORY_CONTEXT_CLASSIFICATION,
        ordered_active_patches=result.eligible_patches,
        canonical_evidence_authority=False,
        source_authority_upgrade=False,
        execution_authority=False,
    )


def personal_memory_context_payload(
    envelope: PersonalMemoryContextEnvelope,
) -> Mapping[str, Any]:
    """Return an explicitly non-canonical structured provider-neutral layer."""

    verify_personal_memory_context_envelope(envelope)
    return {
        "classification": "PRIVATE USER MEMORY - NON-CANONICAL",
        "canonical_evidence_authority": False,
        "execution_authority": False,
        "patches": tuple(
            {
                "patch_id": patch.patch_id,
                "patch_content_hash": patch.patch_statement_sha256,
                "private_memory_text": patch.patch_statement,
                "scope": to_canonical_data(patch.patch_scope),
            }
            for patch in envelope.ordered_active_patches
        ),
    }


def verify_active_patch_assessment(value: ActivePatchAssessment) -> None:
    verify_canonical_hash(
        value, value.assessment_hash, exclude_fields=("assessment_hash",)
    )
    _verify_contract_reconstruction(value, "active patch assessment")


def verify_retrieved_active_patch(value: RetrievedActivePatch) -> None:
    verify_canonical_hash(
        value,
        value.retrieved_patch_hash,
        exclude_fields=("retrieved_patch_hash",),
    )
    for dimension in value.patch_scope:
        _verify_contract_reconstruction(dimension, "retrieved patch scope dimension")
    _verify_contract_reconstruction(value, "retrieved active patch")


def verify_active_patch_retrieval_result(
    value: ActivePatchRetrievalResult,
) -> None:
    verify_canonical_hash(value, value.result_hash, exclude_fields=("result_hash",))
    for patch in value.eligible_patches:
        verify_retrieved_active_patch(patch)
    for assessment in value.excluded_assessments:
        verify_active_patch_assessment(assessment)
    _verify_contract_reconstruction(value, "active patch retrieval result")


def verify_personal_memory_context_envelope(
    value: PersonalMemoryContextEnvelope,
) -> None:
    verify_canonical_hash(
        value, value.context_hash, exclude_fields=("context_hash",)
    )
    for patch in value.ordered_active_patches:
        verify_retrieved_active_patch(patch)
    _verify_contract_reconstruction(value, "Personal Memory context envelope")


def active_patch_retrieval_request_to_jsonb(
    value: ActivePatchRetrievalRequest,
) -> Mapping[str, Any]:
    verify_active_patch_retrieval_request(value)
    return to_canonical_data(value)


def active_patch_retrieval_result_to_jsonb(
    value: ActivePatchRetrievalResult,
) -> Mapping[str, Any]:
    verify_active_patch_retrieval_result(value)
    return to_canonical_data(value)


def personal_memory_context_envelope_to_jsonb(
    value: PersonalMemoryContextEnvelope,
) -> Mapping[str, Any]:
    verify_personal_memory_context_envelope(value)
    return to_canonical_data(value)


__all__ = [
    "ACTIVE_PATCH_RETRIEVAL_POLICY_ID",
    "ACTIVE_PATCH_RETRIEVAL_POLICY_VERSION",
    "DEFAULT_ACTIVE_PATCH_RESULTS",
    "MAXIMUM_ACTIVE_PATCH_CANDIDATES",
    "MAXIMUM_ACTIVE_PATCH_RESULTS",
    "PRIVATE_MEMORY_CONTEXT_CLASSIFICATION",
    "STEP31_SCHEMA_VERSION",
    "ActivePatchAssessment",
    "ActivePatchRetrievalError",
    "ActivePatchRetrievalPolicy",
    "ActivePatchRetrievalRequest",
    "ActivePatchRetrievalResult",
    "CanonicalEvidenceCompatibility",
    "PersonalMemoryContextEnvelope",
    "RetrievedActivePatch",
    "Step31ReasonCode",
    "StoredActivePatchCandidate",
    "active_patch_retrieval_request_to_jsonb",
    "active_patch_retrieval_result_to_jsonb",
    "assess_active_patch",
    "build_active_patch_retrieval_request",
    "build_active_patch_retrieval_result",
    "build_personal_memory_context_envelope",
    "load_active_patch_retrieval_policy",
    "personal_memory_context_envelope_to_jsonb",
    "personal_memory_context_payload",
    "retrieved_active_patch",
    "verify_active_patch_assessment",
    "verify_active_patch_retrieval_inputs",
    "verify_active_patch_retrieval_request",
    "verify_active_patch_retrieval_result",
    "verify_personal_memory_context_envelope",
    "verify_retrieved_active_patch",
    "verify_stored_active_patch_candidate",
]
