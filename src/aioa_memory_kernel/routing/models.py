"""Immutable Step 17 routing and policy decision contracts.

The records in this module describe knowledge routing and policy outcomes.
They do not retrieve content, invoke a HAT, call a provider, or authorize an
external effect by themselves.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from aioa_memory_kernel.contracts.enums import (
    AnswerStatus,
    EvidenceStatus,
    KnowledgeRoute,
    StableStringEnum,
)
from aioa_memory_kernel.contracts.exceptions import ContractValidationError
from aioa_memory_kernel.contracts.scope import ScopeDimension
from aioa_memory_kernel.contracts.serialization import (
    canonical_json_bytes,
    canonical_sha256,
    freeze_json,
    freeze_string_tuple,
    freeze_typed_tuple,
    require_enum_member,
    require_non_empty,
    require_sha256_hex,
    verify_canonical_hash,
)
from aioa_memory_kernel.hats.models import (
    CompatibilityDecision,
    RegistryEntry,
    RegistryState,
    ReviewActor,
)
from aioa_memory_kernel.hats.manifest import parse_semver


KnowledgeRouteDecision = KnowledgeRoute


class KnowledgePolicyDecision(StableStringEnum):
    """Whether the knowledge answer may proceed at the Step 17 boundary."""

    ALLOW_ANSWER = "ALLOW_ANSWER"
    BLOCK_ANSWER = "BLOCK_ANSWER"
    REQUIRE_CONFIRMATION = "REQUIRE_CONFIRMATION"


class ExecutionAuthorizationDecision(StableStringEnum):
    """Pure execution-policy metadata; it performs and approves nothing."""

    ALLOW = "ALLOW"
    ALLOW_SCOPED = "ALLOW_SCOPED"
    REQUIRE_HUMAN = "REQUIRE_HUMAN"
    DENY = "DENY"


class EvidenceCoverageStatus(StableStringEnum):
    """Non-persistent coverage qualifier for canonical evidence status."""

    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    EMPTY = "EMPTY"
    CONFLICTING = "CONFLICTING"


class HatPolicyRequirement(StableStringEnum):
    """Trusted request-local interpretation of a registered HAT policy."""

    ADVISORY = "ADVISORY"
    MANDATORY = "MANDATORY"


class HatCandidateAvailability(StableStringEnum):
    """Additional fail-closed disposition layered over registry state."""

    ACTIVE = "ACTIVE"
    QUARANTINED = "QUARANTINED"
    REVOKED = "REVOKED"


class Step17ReasonCode(StableStringEnum):
    """Closed reason vocabulary for deterministic routing and policy."""

    NO_ELIGIBLE_HAT = "NO_ELIGIBLE_HAT"
    SINGLE_ASSISTING_HAT = "SINGLE_ASSISTING_HAT"
    MANDATORY_HAT_POLICY = "MANDATORY_HAT_POLICY"
    MULTIPLE_HAT_CONFLICT = "MULTIPLE_HAT_CONFLICT"
    SCOPE_AMBIGUOUS = "SCOPE_AMBIGUOUS"
    HAT_UNKNOWN = "HAT_UNKNOWN"
    HAT_DISABLED = "HAT_DISABLED"
    HAT_UNTRUSTED = "HAT_UNTRUSTED"
    HAT_QUARANTINED = "HAT_QUARANTINED"
    HAT_REVOKED = "HAT_REVOKED"
    HAT_VERSION_MISMATCH = "HAT_VERSION_MISMATCH"
    HAT_SCOPE_MISMATCH = "HAT_SCOPE_MISMATCH"
    TENANT_SCOPE_MISMATCH = "TENANT_SCOPE_MISMATCH"
    USER_SCOPE_MISMATCH = "USER_SCOPE_MISMATCH"
    ANSWER_ALLOWED = "ANSWER_ALLOWED"
    POLICY_DENY = "POLICY_DENY"
    EVIDENCE_INSUFFICIENT = "EVIDENCE_INSUFFICIENT"
    EVIDENCE_CONFLICTING = "EVIDENCE_CONFLICTING"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"
    HUMAN_CONFIRMATION_REQUIRED = "HUMAN_CONFIRMATION_REQUIRED"
    EXECUTION_ALLOWED = "EXECUTION_ALLOWED"
    EXECUTION_SCOPED = "EXECUTION_SCOPED"
    EXECUTION_REQUIRES_HUMAN = "EXECUTION_REQUIRES_HUMAN"
    EXECUTION_DENIED = "EXECUTION_DENIED"


_DOMAIN_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_FORBIDDEN_CONTEXT_KEYS = (
    "api_key",
    "authorization",
    "callable",
    "command",
    "credential",
    "executable",
    "password",
    "private_key",
    "secret",
    "token",
)
_MAX_CONTEXT_BYTES = 32 * 1024
_EVIDENCE_COVERAGE_COMBINATIONS = frozenset(
    {
        (EvidenceStatus.NOT_REQUIRED, EvidenceCoverageStatus.EMPTY),
        (EvidenceStatus.SUFFICIENT, EvidenceCoverageStatus.COMPLETE),
        (EvidenceStatus.INSUFFICIENT, EvidenceCoverageStatus.PARTIAL),
        (EvidenceStatus.INSUFFICIENT, EvidenceCoverageStatus.EMPTY),
        (EvidenceStatus.CONFLICTING, EvidenceCoverageStatus.CONFLICTING),
        (EvidenceStatus.UNAVAILABLE, EvidenceCoverageStatus.EMPTY),
        (EvidenceStatus.STALE, EvidenceCoverageStatus.COMPLETE),
        (EvidenceStatus.STALE, EvidenceCoverageStatus.PARTIAL),
        (EvidenceStatus.INVALID, EvidenceCoverageStatus.COMPLETE),
        (EvidenceStatus.INVALID, EvidenceCoverageStatus.PARTIAL),
    }
)


def _bounded_text(value: object, field_name: str, maximum: int = 256) -> str:
    if not isinstance(value, str):
        raise ContractValidationError(f"{field_name} must be a string")
    require_non_empty(value, field_name)
    if value != value.strip() or len(value.encode("utf-8")) > maximum:
        raise ContractValidationError(
            f"{field_name} must be bounded canonical text"
        )
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ContractValidationError(f"{field_name} contains control characters")
    return value


def _domain_id(value: object, field_name: str) -> str:
    text = _bounded_text(value, field_name, 128)
    if _DOMAIN_ID.fullmatch(text) is None:
        raise ContractValidationError(f"{field_name} is not a domain identifier")
    return text


def _normalized_subject(value: object) -> str:
    text = _bounded_text(value, "normalized_query_or_subject", 8192)
    if unicodedata.normalize("NFC", text) != text:
        raise ContractValidationError(
            "normalized_query_or_subject must use Unicode NFC"
        )
    return text


def _scope_tuple(value: object, field_name: str) -> tuple[ScopeDimension, ...]:
    frozen = freeze_typed_tuple(value, ScopeDimension, field_name)
    names = [dimension.name for dimension in frozen]
    if len(names) != len(set(names)):
        raise ContractValidationError(f"{field_name} names must be unique")
    return tuple(sorted(frozen, key=lambda dimension: dimension.name))


def _reason_tuple(
    value: object,
    field_name: str,
    *,
    allow_empty: bool = False,
) -> tuple[Step17ReasonCode, ...]:
    frozen = freeze_typed_tuple(value, Step17ReasonCode, field_name)
    result = tuple(sorted(set(frozen), key=lambda reason: reason.value))
    if not allow_empty and not result:
        raise ContractValidationError(f"{field_name} must not be empty")
    return result


def _assert_context_keys(value: object) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            lowered = key.casefold()
            if any(token in lowered for token in _FORBIDDEN_CONTEXT_KEYS):
                raise ContractValidationError(
                    f"context_metadata key {key!r} is forbidden"
                )
            _assert_context_keys(nested)
    elif isinstance(value, (tuple, frozenset)):
        for nested in value:
            _assert_context_keys(nested)


def _context_metadata(value: object) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ContractValidationError("context_metadata must be an object")
    frozen = freeze_json(value)
    _assert_context_keys(frozen)
    if len(canonical_json_bytes(frozen)) > _MAX_CONTEXT_BYTES:
        raise ContractValidationError("context_metadata exceeds its byte limit")
    return frozen


def _optional_text(value: object | None, field_name: str) -> str | None:
    return None if value is None else _bounded_text(value, field_name)


def _validate_evidence_state(
    status: EvidenceStatus,
    coverage: EvidenceCoverageStatus,
) -> None:
    require_enum_member(status, EvidenceStatus, "evidence_status")
    require_enum_member(
        coverage,
        EvidenceCoverageStatus,
        "evidence_coverage_status",
    )
    if (status, coverage) not in _EVIDENCE_COVERAGE_COMBINATIONS:
        raise ContractValidationError(
            "evidence status and coverage are inconsistent"
        )


@dataclass(frozen=True, slots=True)
class TrustedHatRegistrySnapshot:
    """Hash-bound view of existing Step 12 registry entries.

    This is a snapshot of ``RegistryEntry`` records, not another registry and
    not a runtime catalog. Disabled and rejected entries may be represented so
    that routing can deny them deterministically.
    """

    snapshot_reference: str
    entries: tuple[RegistryEntry, ...]
    snapshot_hash: str = field(init=False)

    def __post_init__(self) -> None:
        _bounded_text(self.snapshot_reference, "snapshot_reference")
        entries = freeze_typed_tuple(self.entries, RegistryEntry, "entries")
        ordered = tuple(
            sorted(
                entries,
                key=lambda entry: (
                    entry.identity.manifest.hat_id,
                    entry.identity.manifest.hat_version,
                ),
            )
        )
        keys = tuple(
            (
                entry.identity.manifest.hat_id,
                entry.identity.manifest.hat_version,
            )
            for entry in ordered
        )
        if len(keys) != len(set(keys)):
            raise ContractValidationError(
                "trusted registry snapshot contains duplicate HAT identities"
            )
        for entry in ordered:
            require_enum_member(entry.compatibility, CompatibilityDecision, "compatibility")
            require_enum_member(entry.state, RegistryState, "registry state")
            if (
                not isinstance(entry.state_version, int)
                or isinstance(entry.state_version, bool)
                or entry.state_version < 1
            ):
                raise ContractValidationError(
                    "registry state_version must be a positive integer"
                )
            for value, name in (
                (entry.current_event_digest, "current_event_digest"),
                (entry.identity.raw_manifest_sha256, "raw_manifest_sha256"),
                (
                    entry.identity.canonical_manifest_sha256,
                    "canonical_manifest_sha256",
                ),
                (entry.identity.typed_manifest_digest, "typed_manifest_digest"),
                (entry.identity.schema_file_sha256, "schema_file_sha256"),
            ):
                require_sha256_hex(value, name)
            if (
                canonical_sha256(entry.identity.manifest)
                != entry.identity.typed_manifest_digest
            ):
                raise ContractValidationError(
                    "registry entry typed manifest digest is stale"
                )
            if entry.review_receipt is not None:
                require_enum_member(
                    entry.review_receipt.actor_type,
                    ReviewActor,
                    "review actor",
                )
                verify_canonical_hash(
                    entry.review_receipt,
                    entry.review_receipt.receipt_digest,
                    exclude_fields=("receipt_digest",),
                )
        object.__setattr__(self, "entries", ordered)
        object.__setattr__(
            self,
            "snapshot_hash",
            canonical_sha256(self, exclude_fields=("snapshot_hash",)),
        )


def verify_routing_input_hash(value: RoutingInput) -> None:
    """Verify the full Axis A input, including nested registry identity."""

    verify_canonical_hash(value, value.input_hash, exclude_fields=("input_hash",))
    verify_canonical_hash(
        value.trusted_hat_registry_snapshot,
        value.trusted_hat_registry_snapshot.snapshot_hash,
        exclude_fields=("snapshot_hash",),
    )
    for candidate in value.candidate_hat_descriptors:
        verify_canonical_hash(
            candidate,
            candidate.candidate_hash,
            exclude_fields=("candidate_hash",),
        )


@dataclass(frozen=True, slots=True)
class HatRoutingCandidate:
    """Request-local candidate bound to one exact registry manifest."""

    hat_id: str
    hat_version: str
    manifest_digest: str
    policy_requirement: HatPolicyRequirement
    availability: HatCandidateAvailability = HatCandidateAvailability.ACTIVE
    tenant_id: str | None = None
    owner_user_id: str | None = None
    candidate_hash: str = field(init=False)

    def __post_init__(self) -> None:
        _domain_id(self.hat_id, "hat_id")
        _bounded_text(self.hat_version, "hat_version")
        parse_semver(self.hat_version)
        require_sha256_hex(self.manifest_digest, "manifest_digest")
        require_enum_member(
            self.policy_requirement,
            HatPolicyRequirement,
            "policy_requirement",
        )
        require_enum_member(
            self.availability,
            HatCandidateAvailability,
            "availability",
        )
        _optional_text(self.tenant_id, "tenant_id")
        _optional_text(self.owner_user_id, "owner_user_id")
        if self.owner_user_id is not None and self.tenant_id is None:
            raise ContractValidationError(
                "an owner-scoped HAT candidate requires tenant_id"
            )
        object.__setattr__(
            self,
            "candidate_hash",
            canonical_sha256(self, exclude_fields=("candidate_hash",)),
        )


@dataclass(frozen=True, slots=True)
class RoutingInput:
    """Canonical, provider-independent input to Axis A."""

    tenant_id: str
    user_id: str
    request_id: str
    request_kind: str
    normalized_query_or_subject: str
    requested_domain_id: str
    requested_scope: tuple[ScopeDimension, ...]
    candidate_hat_descriptors: tuple[HatRoutingCandidate, ...]
    trusted_hat_registry_snapshot: TrustedHatRegistrySnapshot
    evidence_status: EvidenceStatus
    evidence_coverage_status: EvidenceCoverageStatus
    context_metadata: Mapping[str, Any]
    input_hash: str = field(init=False)

    def __post_init__(self) -> None:
        for value, name in (
            (self.tenant_id, "tenant_id"),
            (self.user_id, "user_id"),
            (self.request_id, "request_id"),
            (self.request_kind, "request_kind"),
        ):
            _bounded_text(value, name)
        _normalized_subject(self.normalized_query_or_subject)
        _domain_id(self.requested_domain_id, "requested_domain_id")
        object.__setattr__(
            self,
            "requested_scope",
            _scope_tuple(self.requested_scope, "requested_scope"),
        )
        candidates = freeze_typed_tuple(
            self.candidate_hat_descriptors,
            HatRoutingCandidate,
            "candidate_hat_descriptors",
        )
        ordered = tuple(
            sorted(candidates, key=lambda item: (item.hat_id, item.hat_version))
        )
        keys = tuple((item.hat_id, item.hat_version) for item in ordered)
        if len(keys) != len(set(keys)):
            raise ContractValidationError(
                "candidate_hat_descriptors contain duplicate identities"
            )
        object.__setattr__(self, "candidate_hat_descriptors", ordered)
        if not isinstance(
            self.trusted_hat_registry_snapshot,
            TrustedHatRegistrySnapshot,
        ):
            raise ContractValidationError(
                "trusted_hat_registry_snapshot must be typed"
            )
        _validate_evidence_state(
            self.evidence_status,
            self.evidence_coverage_status,
        )
        object.__setattr__(
            self,
            "context_metadata",
            _context_metadata(self.context_metadata),
        )
        object.__setattr__(
            self,
            "input_hash",
            canonical_sha256(self, exclude_fields=("input_hash",)),
        )


@dataclass(frozen=True, slots=True)
class KnowledgeRouteResult:
    """Deterministic Axis A result; it exposes no HAT runtime handle."""

    request_id: str
    tenant_id: str
    user_id: str
    routing_input_hash: str
    registry_snapshot_hash: str
    knowledge_route: KnowledgeRoute
    selected_hat_id: str | None
    selected_hat_version: str | None
    selected_manifest_digest: str | None
    effective_scope: tuple[ScopeDimension, ...]
    eligible_candidate_hashes: tuple[str, ...]
    reason_codes: tuple[Step17ReasonCode, ...]
    route_hash: str = field(init=False)

    def __post_init__(self) -> None:
        for value, name in (
            (self.request_id, "request_id"),
            (self.tenant_id, "tenant_id"),
            (self.user_id, "user_id"),
        ):
            _bounded_text(value, name)
        require_sha256_hex(self.routing_input_hash, "routing_input_hash")
        require_sha256_hex(self.registry_snapshot_hash, "registry_snapshot_hash")
        require_enum_member(self.knowledge_route, KnowledgeRoute, "knowledge_route")
        selected = (
            self.selected_hat_id,
            self.selected_hat_version,
            self.selected_manifest_digest,
        )
        if self.knowledge_route in {
            KnowledgeRoute.HAT_ASSIST,
            KnowledgeRoute.HAT_ENFORCE,
        }:
            if any(value is None for value in selected):
                raise ContractValidationError(
                    "a HAT route requires an exact selected HAT identity"
                )
            _domain_id(self.selected_hat_id, "selected_hat_id")
            _bounded_text(self.selected_hat_version, "selected_hat_version")
            require_sha256_hex(
                self.selected_manifest_digest,
                "selected_manifest_digest",
            )
        elif any(value is not None for value in selected):
            raise ContractValidationError(
                "PASS_THROUGH and AMBIGUOUS cannot select a HAT"
            )
        object.__setattr__(
            self,
            "effective_scope",
            _scope_tuple(self.effective_scope, "effective_scope"),
        )
        hashes = tuple(sorted(self.eligible_candidate_hashes))
        if len(hashes) != len(set(hashes)):
            raise ContractValidationError(
                "eligible_candidate_hashes must be unique"
            )
        for digest in hashes:
            require_sha256_hex(digest, "eligible candidate hash")
        object.__setattr__(self, "eligible_candidate_hashes", hashes)
        object.__setattr__(
            self,
            "reason_codes",
            _reason_tuple(self.reason_codes, "reason_codes"),
        )
        object.__setattr__(
            self,
            "route_hash",
            canonical_sha256(self, exclude_fields=("route_hash",)),
        )


@dataclass(frozen=True, slots=True)
class AuthorityPolicyContext:
    """Trusted policy ceiling consumed by Axis B, never an approval."""

    request_id: str
    tenant_id: str
    user_id: str
    policy_reference: str
    policy_digest: str
    knowledge_policy_ceiling: KnowledgePolicyDecision
    execution_authorization_ceiling: ExecutionAuthorizationDecision
    scope_allowed: bool
    permitted_execution_scope: tuple[ScopeDimension, ...] = ()
    selected_hat_id: str | None = None
    selected_hat_version: str | None = None
    selected_manifest_digest: str | None = None
    context_hash: str = field(init=False)

    def __post_init__(self) -> None:
        for value, name in (
            (self.request_id, "request_id"),
            (self.tenant_id, "tenant_id"),
            (self.user_id, "user_id"),
            (self.policy_reference, "policy_reference"),
        ):
            _bounded_text(value, name)
        require_sha256_hex(self.policy_digest, "policy_digest")
        require_enum_member(
            self.knowledge_policy_ceiling,
            KnowledgePolicyDecision,
            "knowledge_policy_ceiling",
        )
        require_enum_member(
            self.execution_authorization_ceiling,
            ExecutionAuthorizationDecision,
            "execution_authorization_ceiling",
        )
        if self.scope_allowed is not True and self.scope_allowed is not False:
            raise ContractValidationError("scope_allowed must be a boolean")
        scope = _scope_tuple(
            self.permitted_execution_scope,
            "permitted_execution_scope",
        )
        if (
            self.execution_authorization_ceiling
            is ExecutionAuthorizationDecision.ALLOW_SCOPED
        ):
            if not scope:
                raise ContractValidationError(
                    "ALLOW_SCOPED requires an exact permitted scope"
                )
        elif scope:
            raise ContractValidationError(
                "only ALLOW_SCOPED may carry permitted_execution_scope"
            )
        object.__setattr__(self, "permitted_execution_scope", scope)
        selected = (
            self.selected_hat_id,
            self.selected_hat_version,
            self.selected_manifest_digest,
        )
        if any(value is not None for value in selected):
            if any(value is None for value in selected):
                raise ContractValidationError(
                    "selected HAT policy identity must be complete"
                )
            _domain_id(self.selected_hat_id, "selected_hat_id")
            _bounded_text(self.selected_hat_version, "selected_hat_version")
            require_sha256_hex(
                self.selected_manifest_digest,
                "selected_manifest_digest",
            )
        object.__setattr__(
            self,
            "context_hash",
            canonical_sha256(self, exclude_fields=("context_hash",)),
        )


@dataclass(frozen=True, slots=True)
class PolicyGateResult:
    """Independent knowledge and execution policy decisions from Axis B."""

    request_id: str
    tenant_id: str
    user_id: str
    route_hash: str
    policy_context_hash: str
    evidence_status: EvidenceStatus
    evidence_coverage_status: EvidenceCoverageStatus
    knowledge_policy_decision: KnowledgePolicyDecision
    execution_authorization_decision: ExecutionAuthorizationDecision
    answer_status: AnswerStatus
    permitted_execution_scope: tuple[ScopeDimension, ...]
    reason_codes: tuple[Step17ReasonCode, ...]
    policy_result_hash: str = field(init=False)

    def __post_init__(self) -> None:
        for value, name in (
            (self.request_id, "request_id"),
            (self.tenant_id, "tenant_id"),
            (self.user_id, "user_id"),
        ):
            _bounded_text(value, name)
        require_sha256_hex(self.route_hash, "route_hash")
        require_sha256_hex(self.policy_context_hash, "policy_context_hash")
        _validate_evidence_state(
            self.evidence_status,
            self.evidence_coverage_status,
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
        scope = _scope_tuple(
            self.permitted_execution_scope,
            "permitted_execution_scope",
        )
        if (
            self.execution_authorization_decision
            is ExecutionAuthorizationDecision.ALLOW_SCOPED
        ) != bool(scope):
            raise ContractValidationError(
                "ALLOW_SCOPED and permitted execution scope must agree"
            )
        object.__setattr__(self, "permitted_execution_scope", scope)
        object.__setattr__(
            self,
            "reason_codes",
            _reason_tuple(self.reason_codes, "reason_codes"),
        )
        object.__setattr__(
            self,
            "policy_result_hash",
            canonical_sha256(self, exclude_fields=("policy_result_hash",)),
        )


@dataclass(frozen=True, slots=True)
class HatKernelResult:
    """HAT-to-Kernel envelope carrying data and policy, never authority."""

    request_id: str
    tenant_id: str
    user_id: str
    hat_id: str | None
    hat_version: str | None
    selected_manifest_digest: str | None
    knowledge_route: KnowledgeRoute
    route_hash: str
    requested_scope: tuple[ScopeDimension, ...]
    effective_scope: tuple[ScopeDimension, ...]
    evidence_status: EvidenceStatus
    evidence_coverage_status: EvidenceCoverageStatus
    knowledge_policy_decision: KnowledgePolicyDecision
    execution_authorization_decision: ExecutionAuthorizationDecision
    answer_status: AnswerStatus
    reason_codes: tuple[Step17ReasonCode, ...]
    provenance_references: tuple[str, ...]
    result_hash: str = field(init=False)

    def __post_init__(self) -> None:
        for value, name in (
            (self.request_id, "request_id"),
            (self.tenant_id, "tenant_id"),
            (self.user_id, "user_id"),
        ):
            _bounded_text(value, name)
        require_enum_member(self.knowledge_route, KnowledgeRoute, "knowledge_route")
        selected = (
            self.hat_id,
            self.hat_version,
            self.selected_manifest_digest,
        )
        if any(value is None for value in selected) and any(
            value is not None for value in selected
        ):
            raise ContractValidationError("HAT result identity must be complete")
        if self.knowledge_route in {
            KnowledgeRoute.HAT_ASSIST,
            KnowledgeRoute.HAT_ENFORCE,
        } and any(value is None for value in selected):
            raise ContractValidationError("a HAT result requires exact identity")
        if self.knowledge_route in {
            KnowledgeRoute.PASS_THROUGH,
            KnowledgeRoute.AMBIGUOUS,
        } and any(value is not None for value in selected):
            raise ContractValidationError("a non-HAT result cannot select a HAT")
        if self.hat_id is not None:
            _domain_id(self.hat_id, "hat_id")
            _bounded_text(self.hat_version, "hat_version")
            require_sha256_hex(
                self.selected_manifest_digest,
                "selected_manifest_digest",
            )
        require_sha256_hex(self.route_hash, "route_hash")
        object.__setattr__(
            self,
            "requested_scope",
            _scope_tuple(self.requested_scope, "requested_scope"),
        )
        object.__setattr__(
            self,
            "effective_scope",
            _scope_tuple(self.effective_scope, "effective_scope"),
        )
        _validate_evidence_state(
            self.evidence_status,
            self.evidence_coverage_status,
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
        object.__setattr__(
            self,
            "reason_codes",
            _reason_tuple(self.reason_codes, "reason_codes"),
        )
        references = freeze_string_tuple(
            self.provenance_references,
            "provenance_references",
            unique=True,
        )
        references = tuple(sorted(references))
        for reference in references:
            _bounded_text(reference, "provenance reference", 1024)
        object.__setattr__(self, "provenance_references", references)
        object.__setattr__(
            self,
            "result_hash",
            canonical_sha256(self, exclude_fields=("result_hash",)),
        )


def verify_route_hash(result: KnowledgeRouteResult) -> None:
    verify_canonical_hash(result, result.route_hash, exclude_fields=("route_hash",))


def verify_policy_result_hash(result: PolicyGateResult) -> None:
    verify_canonical_hash(
        result,
        result.policy_result_hash,
        exclude_fields=("policy_result_hash",),
    )


def verify_kernel_result_hash(result: HatKernelResult) -> None:
    verify_canonical_hash(result, result.result_hash, exclude_fields=("result_hash",))
