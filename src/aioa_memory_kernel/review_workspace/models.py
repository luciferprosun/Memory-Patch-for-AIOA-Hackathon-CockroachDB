"""Step 34 bounded human-review workspace contracts.

The workspace consumes typed review-required facts from Steps 26 and 32 and
verified audit context from Step 33.  It has no model, publication, patch,
source-authority, retrieval, or external-execution capability.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, fields as dataclass_fields
from datetime import datetime
from typing import Any

from aioa_memory_kernel.contracts.enums import StableStringEnum
from aioa_memory_kernel.contracts.exceptions import (
    ContractValidationError,
    IntegrityError,
)
from aioa_memory_kernel.contracts.serialization import (
    canonical_json_bytes,
    canonical_sha256,
    ensure_utc,
    freeze_json,
    require_sha256_hex,
    to_canonical_data,
    verify_canonical_hash,
)
from aioa_memory_kernel.security.redaction import assert_secret_free
from aioa_memory_kernel.persistence.errors import PersistenceError


STEP34_SCHEMA_VERSION = "human-review-workspace-1.0.0"
STEP34_REVIEW_ACCESS_POLICY_ID = "human-review-access-policy-1a"
STEP34_REVIEW_ACCESS_POLICY_VERSION = "1"
STEP34_DECISION_POLICY_ID = "human-review-decision-policy-1a"
STEP34_DECISION_POLICY_VERSION = "1"
DEFAULT_REVIEW_QUEUE_PAGE_SIZE = 25
MAXIMUM_REVIEW_QUEUE_PAGE_SIZE = 100
MAXIMUM_REVIEW_CONTEXT_REFERENCES = 64
MAXIMUM_REVIEW_AUDIT_REFERENCES = 32
MAXIMUM_REVIEW_REASON_CODES = 16
MAXIMUM_REVIEW_NOTE_BYTES = 2 * 1024
MAXIMUM_REVIEW_CONTEXT_BYTES = 64 * 1024
MAXIMUM_REVIEW_DETAIL_BYTES = 128 * 1024

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+-]{0,254}$")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SECRET_KEYS = (
    "api_key",
    "authorization",
    "aws_secret",
    "bearer",
    "credential",
    "database_url",
    "github_token",
    "password",
    "presigned",
    "private_key",
    "secret",
    "session_token",
)
_SECRET_VALUES = (
    "authorization:",
    "bearer ",
    "cockroachdb://",
    "github_pat_",
    "ghp_",
    "postgresql://",
    "sk-ant-",
    "sk-proj-",
    "x-amz-credential",
    "x-amz-signature",
)


class ReviewCaseType(StableStringEnum):
    ANSWER_VERIFICATION_FAILURE = "ANSWER_VERIFICATION_FAILURE"
    ANSWER_CONFLICTING_EVIDENCE = "ANSWER_CONFLICTING_EVIDENCE"
    ANSWER_INSUFFICIENT_EVIDENCE = "ANSWER_INSUFFICIENT_EVIDENCE"
    ANSWER_STALE_EVIDENCE = "ANSWER_STALE_EVIDENCE"
    ANSWER_CONFIRMATION_REQUIRED = "ANSWER_CONFIRMATION_REQUIRED"
    SHARED_MEMORY_PROMOTION = "SHARED_MEMORY_PROMOTION"
    SHARED_PROMOTION_PRIVACY_REVIEW = "SHARED_PROMOTION_PRIVACY_REVIEW"
    SHARED_PROMOTION_CANONICAL_CONFLICT = (
        "SHARED_PROMOTION_CANONICAL_CONFLICT"
    )


class ReviewSubjectType(StableStringEnum):
    ANSWER_REVIEW_RESULT = "ANSWER_REVIEW_RESULT"
    SHARED_MEMORY_PROMOTION_PROPOSAL = "SHARED_MEMORY_PROMOTION_PROPOSAL"


class ReviewSourceContract(StableStringEnum):
    STEP26_HUMAN_REVIEW_REQUIRED = "STEP26_HUMAN_REVIEW_REQUIRED"
    STEP26_BOUNDED_ANSWER_FAILURE = "STEP26_BOUNDED_ANSWER_FAILURE"
    STEP32_SHARED_MEMORY_PROMOTION_PROPOSAL = (
        "STEP32_SHARED_MEMORY_PROMOTION_PROPOSAL"
    )


class ReviewState(StableStringEnum):
    OPEN = "OPEN"
    CLAIMED = "CLAIMED"
    IN_REVIEW = "IN_REVIEW"
    RESOLVED = "RESOLVED"
    ESCALATED = "ESCALATED"
    STALE = "STALE"


class ReviewPriority(StableStringEnum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


REVIEW_PRIORITY_RANK = {
    ReviewPriority.LOW: 10,
    ReviewPriority.NORMAL: 20,
    ReviewPriority.HIGH: 30,
    ReviewPriority.CRITICAL: 40,
}


class ReviewerRole(StableStringEnum):
    ANSWER_REVIEWER = "ANSWER_REVIEWER"
    SHARED_MEMORY_REVIEWER = "SHARED_MEMORY_REVIEWER"
    SENIOR_REVIEWER = "SENIOR_REVIEWER"


class ReviewDecisionType(StableStringEnum):
    ALLOW_QUALIFIED_ANSWER = "ALLOW_QUALIFIED_ANSWER"
    REJECT_ANSWER = "REJECT_ANSWER"
    REQUEST_MORE_EVIDENCE = "REQUEST_MORE_EVIDENCE"
    CONFIRMATION_REQUIRED = "CONFIRMATION_REQUIRED"
    APPROVE_SHARED_PROMOTION_CANDIDATE = (
        "APPROVE_SHARED_PROMOTION_CANDIDATE"
    )
    REJECT_SHARED_PROMOTION = "REJECT_SHARED_PROMOTION"
    REQUEST_REDACTION_CHANGES = "REQUEST_REDACTION_CHANGES"
    ESCALATE = "ESCALATE"


class ReviewHandoffTarget(StableStringEnum):
    STEP26_ANSWER_OUTPUT_SERVICE = "STEP26_ANSWER_OUTPUT_SERVICE"
    STEP32_SHARED_PROMOTION_REVIEW_BOUNDARY = (
        "STEP32_SHARED_PROMOTION_REVIEW_BOUNDARY"
    )


class ReviewHandoffStatus(StableStringEnum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    CHANGES_REQUESTED = "CHANGES_REQUESTED"
    MORE_EVIDENCE_REQUESTED = "MORE_EVIDENCE_REQUESTED"
    CONFIRMATION_REQUIRED = "CONFIRMATION_REQUIRED"
    ESCALATED = "ESCALATED"


class ReviewReasonCode(StableStringEnum):
    REVIEW_CASE_CREATED = "REVIEW_CASE_CREATED"
    REVIEW_CASE_EXACT_REPLAY = "REVIEW_CASE_EXACT_REPLAY"
    REVIEW_CASE_DUPLICATE = "REVIEW_CASE_DUPLICATE"
    REVIEW_CASE_STALE = "REVIEW_CASE_STALE"
    REVIEW_CASE_CLAIMED = "REVIEW_CASE_CLAIMED"
    REVIEW_CASE_ALREADY_CLAIMED = "REVIEW_CASE_ALREADY_CLAIMED"
    REVIEW_CASE_CLAIM_CONFLICT = "REVIEW_CASE_CLAIM_CONFLICT"
    REVIEW_DECISION_RECORDED = "REVIEW_DECISION_RECORDED"
    REVIEW_DECISION_EXACT_REPLAY = "REVIEW_DECISION_EXACT_REPLAY"
    REVIEW_DECISION_CONFLICT = "REVIEW_DECISION_CONFLICT"
    REVIEW_DECISION_NOT_ALLOWED = "REVIEW_DECISION_NOT_ALLOWED"
    REVIEW_DECISION_STALE = "REVIEW_DECISION_STALE"
    REVIEW_ACCESS_DENIED = "REVIEW_ACCESS_DENIED"
    REVIEW_TENANT_DENIED = "REVIEW_TENANT_DENIED"
    REVIEW_OWNER_SCOPE_DENIED = "REVIEW_OWNER_SCOPE_DENIED"
    REVIEWER_ROLE_INSUFFICIENT = "REVIEWER_ROLE_INSUFFICIENT"
    REVIEW_AUDIT_CONTEXT_VERIFIED = "REVIEW_AUDIT_CONTEXT_VERIFIED"
    REVIEW_AUDIT_CONTEXT_INVALID = "REVIEW_AUDIT_CONTEXT_INVALID"
    REVIEW_HANDOFF_SUCCEEDED = "REVIEW_HANDOFF_SUCCEEDED"
    REVIEW_HANDOFF_FAILED = "REVIEW_HANDOFF_FAILED"
    REVIEW_RESOLVED = "REVIEW_RESOLVED"
    REVIEW_ESCALATED = "REVIEW_ESCALATED"
    REVIEW_REQUEST_CHANGES = "REVIEW_REQUEST_CHANGES"
    REVIEW_REQUEST_MORE_EVIDENCE = "REVIEW_REQUEST_MORE_EVIDENCE"


class HumanReviewWorkspaceError(PersistenceError):
    def __init__(self, reason_code: ReviewReasonCode) -> None:
        if not isinstance(reason_code, ReviewReasonCode):
            raise TypeError("reason_code must be ReviewReasonCode")
        super().__init__(
            "Step 34 human-review operation rejected",
            operation_kind="HUMAN_REVIEW_WORKSPACE_STEP34",
            sanitized_code=reason_code.value,
        )
        self.reason_code = reason_code


def _text(value: object, name: str, maximum: int = 255) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or unicodedata.normalize("NFC", value) != value
        or _CONTROL.search(value)
        or len(value.encode("utf-8")) > maximum
    ):
        raise ContractValidationError(f"{name} must be bounded canonical text")
    return value


def _logical_id(value: object, name: str) -> str:
    result = _text(value, name)
    if _SAFE_ID.fullmatch(result) is None:
        raise ContractValidationError(f"{name} must be a database-safe identifier")
    return result


def _optional_text(value: object | None, name: str, maximum: int = 255) -> str | None:
    return None if value is None else _text(value, name, maximum)


def _digest(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise ContractValidationError(f"{name} must be a SHA-256 digest")
    return require_sha256_hex(value, name)


def _optional_digest(value: object | None, name: str) -> str | None:
    return None if value is None else _digest(value, name)


def _timestamp(value: object, name: str) -> datetime:
    if not isinstance(value, datetime):
        raise ContractValidationError(f"{name} must be a datetime")
    return ensure_utc(value, name)


def _integer(value: object, name: str, minimum: int = 0) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise ContractValidationError(f"{name} is outside its bound")
    return value


def _enum(value: object, enum_type: type[StableStringEnum], name: str):
    if not isinstance(value, enum_type):
        raise ContractValidationError(f"{name} must be {enum_type.__name__}")
    return value


def _reason_codes(value: object) -> tuple[ReviewReasonCode, ...]:
    if not isinstance(value, (tuple, list)) or not value:
        raise ContractValidationError("review reason codes must be non-empty")
    result = tuple(value)
    if len(result) > MAXIMUM_REVIEW_REASON_CODES or any(
        not isinstance(item, ReviewReasonCode) for item in result
    ):
        raise ContractValidationError("review reason codes exceed the closed bound")
    if result != tuple(sorted(set(result), key=lambda item: item.value)):
        raise ContractValidationError("review reason codes must be sorted and unique")
    return result


def _hash_mapping(value: object, name: str) -> Mapping[str, str]:
    if not isinstance(value, Mapping) or len(value) > MAXIMUM_REVIEW_CONTEXT_REFERENCES:
        raise ContractValidationError(f"{name} must be a bounded mapping")
    result: dict[str, str] = {}
    for key in sorted(value):
        if not isinstance(key, str):
            raise ContractValidationError(f"{name} keys must be text")
        result[_text(key, f"{name} key", 128)] = _digest(
            value[key], f"{name}.{key}"
        )
    return freeze_json(result)


def _walk_safe(value: object, *, path: str = "review_context") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            key_text = _text(key, f"{path} key", 128)
            if any(token in key_text.casefold() for token in _SECRET_KEYS):
                raise ContractValidationError("review context contains a secret field")
            _walk_safe(item, path=f"{path}.{key_text}")
        return
    if isinstance(value, (tuple, list)):
        if len(value) > MAXIMUM_REVIEW_CONTEXT_REFERENCES:
            raise ContractValidationError("review context sequence is unbounded")
        for index, item in enumerate(value):
            _walk_safe(item, path=f"{path}[{index}]")
        return
    if value is None or isinstance(value, (bool, int)):
        return
    if isinstance(value, str):
        _text(value, path, 16 * 1024)
        lowered = value.casefold()
        if any(token in lowered for token in _SECRET_VALUES):
            raise ContractValidationError("review context contains secret-shaped text")
        if "/home/" in lowered or "/media/" in lowered:
            raise ContractValidationError("review context contains a machine path")
        return
    raise ContractValidationError("review context contains an unsupported value")


def safe_review_context(value: object) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ContractValidationError("review context must be an object")
    _walk_safe(value)
    frozen = freeze_json(value)
    try:
        assert_secret_free(
            frozen,
            surface="review context",
            reject_machine_paths=True,
        )
    except ValueError as error:
        raise ContractValidationError(
            "review context contains forbidden secret material"
        ) from error
    if len(canonical_json_bytes(frozen)) > MAXIMUM_REVIEW_CONTEXT_BYTES:
        raise ContractValidationError("review context exceeds the byte bound")
    return frozen


def _reconstruct(value: object, name: str) -> None:
    try:
        rebuilt = type(value)(
            **{
                item.name: getattr(value, item.name)
                for item in dataclass_fields(value)
                if item.init
            }
        )
    except Exception as exc:
        raise IntegrityError(f"{name} semantic reconstruction failed") from exc
    if rebuilt != value:
        raise IntegrityError(f"{name} contains detached derived fields")


def _review_replay_identity(
    operation: str,
    tenant_id: str,
    actor_id: str,
    idempotency_key: str,
) -> str:
    _text(operation, "operation", 64)
    _logical_id(tenant_id, "tenant_id")
    _logical_id(actor_id, "actor_id")
    _logical_id(idempotency_key, "idempotency_key")
    return "human-review-replay-" + canonical_sha256(
        {
            "operation": operation,
            "tenant_id": tenant_id,
            "actor_id": actor_id,
            "idempotency_key": idempotency_key,
        }
    )


@dataclass(frozen=True, slots=True)
class ReviewAccessPolicy:
    schema_version: str = STEP34_SCHEMA_VERSION
    policy_id: str = STEP34_REVIEW_ACCESS_POLICY_ID
    policy_version: str = STEP34_REVIEW_ACCESS_POLICY_VERSION
    ordinary_user_access: bool = False
    cross_tenant_access: bool = False
    queue_minimum_disclosure: bool = True
    reviewer_database_role: str = "mp_human_reviewer"
    review_service_database_role: str = "mp_review_service"
    policy_digest: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != STEP34_SCHEMA_VERSION:
            raise ContractValidationError("unsupported review access policy")
        _logical_id(self.policy_id, "policy_id")
        _logical_id(self.policy_version, "policy_version")
        _logical_id(self.reviewer_database_role, "reviewer_database_role")
        _logical_id(self.review_service_database_role, "review_service_database_role")
        if (
            self.ordinary_user_access is not False
            or self.cross_tenant_access is not False
            or self.queue_minimum_disclosure is not True
        ):
            raise ContractValidationError("review access policy weakens isolation")
        object.__setattr__(
            self,
            "policy_digest",
            canonical_sha256(self, exclude_fields=("policy_digest",)),
        )


def _role_case_types(role: ReviewerRole) -> tuple[ReviewCaseType, ...]:
    answer = (
        ReviewCaseType.ANSWER_CONFIRMATION_REQUIRED,
        ReviewCaseType.ANSWER_CONFLICTING_EVIDENCE,
        ReviewCaseType.ANSWER_INSUFFICIENT_EVIDENCE,
        ReviewCaseType.ANSWER_STALE_EVIDENCE,
        ReviewCaseType.ANSWER_VERIFICATION_FAILURE,
    )
    shared = (
        ReviewCaseType.SHARED_MEMORY_PROMOTION,
        ReviewCaseType.SHARED_PROMOTION_CANONICAL_CONFLICT,
        ReviewCaseType.SHARED_PROMOTION_PRIVACY_REVIEW,
    )
    if role is ReviewerRole.ANSWER_REVIEWER:
        return answer
    if role is ReviewerRole.SHARED_MEMORY_REVIEWER:
        return shared
    return tuple(sorted((*answer, *shared), key=lambda item: item.value))


@dataclass(frozen=True, slots=True)
class ReviewerPrincipal:
    schema_version: str
    tenant_id: str
    reviewer_id: str
    reviewer_role: ReviewerRole
    authentication_context_hash: str
    authenticated_at: datetime
    model_actor: bool = False
    critic_actor: bool = False
    principal_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != STEP34_SCHEMA_VERSION:
            raise ContractValidationError("unsupported reviewer principal schema")
        _logical_id(self.tenant_id, "tenant_id")
        _logical_id(self.reviewer_id, "reviewer_id")
        _enum(self.reviewer_role, ReviewerRole, "reviewer_role")
        _digest(self.authentication_context_hash, "authentication_context_hash")
        object.__setattr__(
            self, "authenticated_at", _timestamp(self.authenticated_at, "authenticated_at")
        )
        if self.model_actor is not False or self.critic_actor is not False:
            raise ContractValidationError("models and Critics cannot be reviewers")
        object.__setattr__(
            self,
            "principal_hash",
            canonical_sha256(self, exclude_fields=("principal_hash",)),
        )


@dataclass(frozen=True, slots=True)
class ReviewerAuthorization:
    schema_version: str
    authorization_id: str
    tenant_id: str
    reviewer_id: str
    reviewer_role: ReviewerRole
    case_type: ReviewCaseType
    owner_user_id: str | None
    access_policy_id: str
    access_policy_version: str
    access_policy_digest: str
    active: bool
    granted_at: datetime
    authorization_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != STEP34_SCHEMA_VERSION:
            raise ContractValidationError("unsupported reviewer authorization schema")
        for name in ("authorization_id", "tenant_id", "reviewer_id"):
            _logical_id(getattr(self, name), name)
        _enum(self.reviewer_role, ReviewerRole, "reviewer_role")
        _enum(self.case_type, ReviewCaseType, "case_type")
        if self.case_type not in _role_case_types(self.reviewer_role):
            raise ContractValidationError("reviewer role cannot access this case type")
        if self.owner_user_id is not None:
            _logical_id(self.owner_user_id, "owner_user_id")
        policy = ReviewAccessPolicy()
        if (
            self.access_policy_id != policy.policy_id
            or self.access_policy_version != policy.policy_version
            or self.access_policy_digest != policy.policy_digest
        ):
            raise ContractValidationError("review authorization policy is detached")
        if self.active is not True:
            raise ContractValidationError("inactive authorization is not usable")
        object.__setattr__(self, "granted_at", _timestamp(self.granted_at, "granted_at"))
        expected_id = "reviewer-authorization-" + canonical_sha256(
            {
                "tenant_id": self.tenant_id,
                "reviewer_id": self.reviewer_id,
                "reviewer_role": self.reviewer_role,
                "case_type": self.case_type,
                "owner_user_id": self.owner_user_id,
                "access_policy_digest": self.access_policy_digest,
            }
        )
        if self.authorization_id != expected_id:
            raise ContractValidationError("review authorization identity is detached")
        object.__setattr__(
            self,
            "authorization_hash",
            canonical_sha256(self, exclude_fields=("authorization_hash",)),
        )


def build_reviewer_authorization(
    principal: ReviewerPrincipal,
    *,
    case_type: ReviewCaseType,
    owner_user_id: str | None,
    granted_at: datetime,
) -> ReviewerAuthorization:
    verify_reviewer_principal(principal)
    policy = ReviewAccessPolicy()
    identity = {
        "tenant_id": principal.tenant_id,
        "reviewer_id": principal.reviewer_id,
        "reviewer_role": principal.reviewer_role,
        "case_type": case_type,
        "owner_user_id": owner_user_id,
        "access_policy_digest": policy.policy_digest,
    }
    return ReviewerAuthorization(
        schema_version=STEP34_SCHEMA_VERSION,
        authorization_id="reviewer-authorization-" + canonical_sha256(identity),
        tenant_id=principal.tenant_id,
        reviewer_id=principal.reviewer_id,
        reviewer_role=principal.reviewer_role,
        case_type=case_type,
        owner_user_id=owner_user_id,
        access_policy_id=policy.policy_id,
        access_policy_version=policy.policy_version,
        access_policy_digest=policy.policy_digest,
        active=True,
        granted_at=granted_at,
    )


@dataclass(frozen=True, slots=True)
class ReviewSourceContext:
    schema_version: str
    source_contract: ReviewSourceContract
    subject_hash: str
    context_payload: Mapping[str, Any]
    contains_raw_private_memory: bool
    canonical_evidence: bool
    model_authority: bool
    context_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != STEP34_SCHEMA_VERSION:
            raise ContractValidationError("unsupported review source context schema")
        _enum(self.source_contract, ReviewSourceContract, "source_contract")
        _digest(self.subject_hash, "subject_hash")
        object.__setattr__(self, "context_payload", safe_review_context(self.context_payload))
        if (
            not isinstance(self.contains_raw_private_memory, bool)
            or self.canonical_evidence is not False
            or self.model_authority is not False
        ):
            raise ContractValidationError("review source context authority is invalid")
        object.__setattr__(
            self,
            "context_hash",
            canonical_sha256(self, exclude_fields=("context_hash",)),
        )


def _review_case_trigger_hash(
    *,
    case_type: ReviewCaseType,
    tenant_id: str,
    owner_user_id: str,
    subject_type: ReviewSubjectType,
    subject_id: str,
    subject_hash: str,
    request_id: str | None,
    kernel_run_id: str | None,
    route_hash: str | None,
    review_reason_codes: Sequence[ReviewReasonCode],
    priority: ReviewPriority,
    source_audit_event_hash: str,
    source_chain_id: str,
    audit_verification_result_hash: str,
    audit_context_verified: bool,
    required_context_refs: Mapping[str, str],
    source_context_hash: str,
) -> str:
    """Bind every immutable intake semantic without binding lifecycle state."""

    return canonical_sha256(
        {
            "schema_version": STEP34_SCHEMA_VERSION,
            "case_type": case_type,
            "tenant_id": tenant_id,
            "owner_user_id": owner_user_id,
            "subject_type": subject_type,
            "subject_id": subject_id,
            "subject_hash": subject_hash,
            "request_id": request_id,
            "kernel_run_id": kernel_run_id,
            "route_hash": route_hash,
            "review_reason_codes": _reason_codes(review_reason_codes),
            "priority": priority,
            "source_audit_event_hash": source_audit_event_hash,
            "source_chain_id": source_chain_id,
            "audit_verification_result_hash": audit_verification_result_hash,
            "audit_context_verified": audit_context_verified,
            "required_context_refs": _hash_mapping(
                required_context_refs, "required_context_refs"
            ),
            "source_context_hash": source_context_hash,
        }
    )


@dataclass(frozen=True, slots=True)
class HumanReviewCase:
    schema_version: str
    review_case_id: str
    trigger_hash: str
    case_type: ReviewCaseType
    tenant_id: str
    owner_user_id: str
    subject_type: ReviewSubjectType
    subject_id: str
    subject_hash: str
    request_id: str | None
    kernel_run_id: str | None
    route_hash: str | None
    review_reason_codes: tuple[ReviewReasonCode, ...]
    priority: ReviewPriority
    source_audit_event_hash: str
    source_chain_id: str
    audit_verification_result_hash: str
    audit_context_verified: bool
    required_context_refs: Mapping[str, str]
    source_context_hash: str
    review_state: ReviewState
    review_state_version: int
    claimed_reviewer_id: str | None
    claimed_reviewer_role: ReviewerRole | None
    created_at: datetime
    updated_at: datetime
    case_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != STEP34_SCHEMA_VERSION:
            raise ContractValidationError("unsupported review case schema")
        for name in (
            "review_case_id",
            "tenant_id",
            "owner_user_id",
            "subject_id",
            "source_chain_id",
        ):
            _logical_id(getattr(self, name), name)
        _digest(self.trigger_hash, "trigger_hash")
        _enum(self.case_type, ReviewCaseType, "case_type")
        _enum(self.subject_type, ReviewSubjectType, "subject_type")
        _digest(self.subject_hash, "subject_hash")
        if self.request_id is not None:
            _logical_id(self.request_id, "request_id")
        if self.kernel_run_id is not None:
            _logical_id(self.kernel_run_id, "kernel_run_id")
        _optional_digest(self.route_hash, "route_hash")
        object.__setattr__(self, "review_reason_codes", _reason_codes(self.review_reason_codes))
        _enum(self.priority, ReviewPriority, "priority")
        for name in (
            "source_audit_event_hash",
            "audit_verification_result_hash",
            "source_context_hash",
        ):
            _digest(getattr(self, name), name)
        if not isinstance(self.audit_context_verified, bool):
            raise ContractValidationError("audit_context_verified must be boolean")
        object.__setattr__(
            self,
            "required_context_refs",
            _hash_mapping(self.required_context_refs, "required_context_refs"),
        )
        _enum(self.review_state, ReviewState, "review_state")
        _integer(self.review_state_version, "review_state_version", 1)
        expected_versions = {
            ReviewState.OPEN: 1,
            ReviewState.CLAIMED: 2,
            ReviewState.IN_REVIEW: 3,
            ReviewState.RESOLVED: 4,
            ReviewState.ESCALATED: 4,
            ReviewState.STALE: 4,
        }
        if self.review_state_version != expected_versions[self.review_state]:
            raise ContractValidationError("review state/version tuple is invalid")
        reviewer_values = (self.claimed_reviewer_id, self.claimed_reviewer_role)
        if self.review_state is ReviewState.OPEN:
            if any(value is not None for value in reviewer_values):
                raise ContractValidationError("open case cannot already be claimed")
        else:
            if any(value is None for value in reviewer_values):
                raise ContractValidationError("non-open case requires its reviewer")
            _logical_id(self.claimed_reviewer_id, "claimed_reviewer_id")
            _enum(self.claimed_reviewer_role, ReviewerRole, "claimed_reviewer_role")
        object.__setattr__(self, "created_at", _timestamp(self.created_at, "created_at"))
        object.__setattr__(self, "updated_at", _timestamp(self.updated_at, "updated_at"))
        if self.updated_at < self.created_at:
            raise ContractValidationError("case update precedes creation")
        expected_trigger = _review_case_trigger_hash(
            case_type=self.case_type,
            tenant_id=self.tenant_id,
            owner_user_id=self.owner_user_id,
            subject_type=self.subject_type,
            subject_id=self.subject_id,
            subject_hash=self.subject_hash,
            request_id=self.request_id,
            kernel_run_id=self.kernel_run_id,
            route_hash=self.route_hash,
            review_reason_codes=self.review_reason_codes,
            priority=self.priority,
            source_audit_event_hash=self.source_audit_event_hash,
            source_chain_id=self.source_chain_id,
            audit_verification_result_hash=self.audit_verification_result_hash,
            audit_context_verified=self.audit_context_verified,
            required_context_refs=self.required_context_refs,
            source_context_hash=self.source_context_hash,
        )
        if self.trigger_hash != expected_trigger:
            raise ContractValidationError("review case trigger is detached")
        expected_id = "human-review-case-" + canonical_sha256(
            {
                "trigger_hash": self.trigger_hash,
                "case_type": self.case_type,
                "tenant_id": self.tenant_id,
                "owner_user_id": self.owner_user_id,
                "subject_type": self.subject_type,
                "subject_id": self.subject_id,
                "subject_hash": self.subject_hash,
                "source_audit_event_hash": self.source_audit_event_hash,
                "source_context_hash": self.source_context_hash,
            }
        )
        if self.review_case_id != expected_id:
            raise ContractValidationError("review case identity is detached")
        object.__setattr__(
            self,
            "case_hash",
            canonical_sha256(self, exclude_fields=("case_hash",)),
        )


def build_open_review_case(
    *,
    case_type: ReviewCaseType,
    tenant_id: str,
    owner_user_id: str,
    subject_type: ReviewSubjectType,
    subject_id: str,
    subject_hash: str,
    request_id: str | None,
    kernel_run_id: str | None,
    route_hash: str | None,
    review_reason_codes: Sequence[ReviewReasonCode],
    priority: ReviewPriority,
    source_audit_event_hash: str,
    source_chain_id: str,
    audit_verification_result_hash: str,
    audit_context_verified: bool,
    required_context_refs: Mapping[str, str],
    source_context_hash: str,
    created_at: datetime,
) -> HumanReviewCase:
    trigger_hash = _review_case_trigger_hash(
        case_type=case_type,
        tenant_id=tenant_id,
        owner_user_id=owner_user_id,
        subject_type=subject_type,
        subject_id=subject_id,
        subject_hash=subject_hash,
        request_id=request_id,
        kernel_run_id=kernel_run_id,
        route_hash=route_hash,
        review_reason_codes=review_reason_codes,
        priority=priority,
        source_audit_event_hash=source_audit_event_hash,
        source_chain_id=source_chain_id,
        audit_verification_result_hash=audit_verification_result_hash,
        audit_context_verified=audit_context_verified,
        required_context_refs=required_context_refs,
        source_context_hash=source_context_hash,
    )
    identity = {
        "trigger_hash": trigger_hash,
        "case_type": case_type,
        "tenant_id": tenant_id,
        "owner_user_id": owner_user_id,
        "subject_type": subject_type,
        "subject_id": subject_id,
        "subject_hash": subject_hash,
        "source_audit_event_hash": source_audit_event_hash,
        "source_context_hash": source_context_hash,
    }
    return HumanReviewCase(
        schema_version=STEP34_SCHEMA_VERSION,
        review_case_id="human-review-case-" + canonical_sha256(identity),
        trigger_hash=trigger_hash,
        case_type=case_type,
        tenant_id=tenant_id,
        owner_user_id=owner_user_id,
        subject_type=subject_type,
        subject_id=subject_id,
        subject_hash=subject_hash,
        request_id=request_id,
        kernel_run_id=kernel_run_id,
        route_hash=route_hash,
        review_reason_codes=tuple(review_reason_codes),
        priority=priority,
        source_audit_event_hash=source_audit_event_hash,
        source_chain_id=source_chain_id,
        audit_verification_result_hash=audit_verification_result_hash,
        audit_context_verified=audit_context_verified,
        required_context_refs=required_context_refs,
        source_context_hash=source_context_hash,
        review_state=ReviewState.OPEN,
        review_state_version=1,
        claimed_reviewer_id=None,
        claimed_reviewer_role=None,
        created_at=created_at,
        updated_at=created_at,
    )


def transition_review_case(
    case: HumanReviewCase,
    *,
    state: ReviewState,
    updated_at: datetime,
    reviewer_id: str,
    reviewer_role: ReviewerRole,
) -> HumanReviewCase:
    verify_human_review_case(case)
    allowed = {
        ReviewState.OPEN: {ReviewState.CLAIMED},
        ReviewState.CLAIMED: {ReviewState.IN_REVIEW},
        ReviewState.IN_REVIEW: {
            ReviewState.RESOLVED,
            ReviewState.ESCALATED,
        },
    }
    if state not in allowed.get(case.review_state, set()):
        raise HumanReviewWorkspaceError(ReviewReasonCode.REVIEW_DECISION_NOT_ALLOWED)
    version = {
        ReviewState.CLAIMED: 2,
        ReviewState.IN_REVIEW: 3,
        ReviewState.RESOLVED: 4,
        ReviewState.ESCALATED: 4,
    }[state]
    return HumanReviewCase(
        schema_version=case.schema_version,
        review_case_id=case.review_case_id,
        trigger_hash=case.trigger_hash,
        case_type=case.case_type,
        tenant_id=case.tenant_id,
        owner_user_id=case.owner_user_id,
        subject_type=case.subject_type,
        subject_id=case.subject_id,
        subject_hash=case.subject_hash,
        request_id=case.request_id,
        kernel_run_id=case.kernel_run_id,
        route_hash=case.route_hash,
        review_reason_codes=case.review_reason_codes,
        priority=case.priority,
        source_audit_event_hash=case.source_audit_event_hash,
        source_chain_id=case.source_chain_id,
        audit_verification_result_hash=case.audit_verification_result_hash,
        audit_context_verified=case.audit_context_verified,
        required_context_refs=case.required_context_refs,
        source_context_hash=case.source_context_hash,
        review_state=state,
        review_state_version=version,
        claimed_reviewer_id=reviewer_id,
        claimed_reviewer_role=reviewer_role,
        created_at=case.created_at,
        updated_at=updated_at,
    )


@dataclass(frozen=True, slots=True)
class ClaimReviewCaseRequest:
    schema_version: str
    tenant_id: str
    review_case_id: str
    review_case_hash: str
    reviewer_principal_hash: str
    reviewer_id: str
    reviewer_role: ReviewerRole
    expected_state: ReviewState
    expected_state_version: int
    requested_at: datetime
    idempotency_key: str
    replay_identity: str = field(init=False)
    request_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != STEP34_SCHEMA_VERSION:
            raise ContractValidationError("unsupported review claim schema")
        for name in ("tenant_id", "review_case_id", "reviewer_id", "idempotency_key"):
            _logical_id(getattr(self, name), name)
        _digest(self.review_case_hash, "review_case_hash")
        _digest(self.reviewer_principal_hash, "reviewer_principal_hash")
        _enum(self.reviewer_role, ReviewerRole, "reviewer_role")
        if self.expected_state is not ReviewState.OPEN or self.expected_state_version != 1:
            raise ContractValidationError("claim must target OPEN version one")
        object.__setattr__(self, "requested_at", _timestamp(self.requested_at, "requested_at"))
        object.__setattr__(
            self,
            "replay_identity",
            _review_replay_identity(
                "claim", self.tenant_id, self.reviewer_id, self.idempotency_key
            ),
        )
        object.__setattr__(
            self,
            "request_hash",
            canonical_sha256(self, exclude_fields=("request_hash",)),
        )


@dataclass(frozen=True, slots=True)
class ReviewCaseClaimReceipt:
    schema_version: str
    claim_id: str
    request_hash: str
    replay_identity: str
    tenant_id: str
    review_case_id: str
    previous_case_hash: str
    claimed_case_hash: str
    reviewer_id: str
    reviewer_role: ReviewerRole
    claimed_at: datetime
    audit_event_hash: str
    reason_code: ReviewReasonCode
    receipt_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != STEP34_SCHEMA_VERSION:
            raise ContractValidationError("unsupported claim receipt schema")
        for name in ("claim_id", "replay_identity", "tenant_id", "review_case_id", "reviewer_id"):
            _logical_id(getattr(self, name), name)
        for name in ("request_hash", "previous_case_hash", "claimed_case_hash", "audit_event_hash"):
            _digest(getattr(self, name), name)
        _enum(self.reviewer_role, ReviewerRole, "reviewer_role")
        object.__setattr__(self, "claimed_at", _timestamp(self.claimed_at, "claimed_at"))
        if self.reason_code not in {
            ReviewReasonCode.REVIEW_CASE_CLAIMED,
            ReviewReasonCode.REVIEW_CASE_ALREADY_CLAIMED,
        }:
            raise ContractValidationError("claim receipt reason is invalid")
        expected_id = "human-review-claim-" + canonical_sha256(
            {"replay_identity": self.replay_identity, "request_hash": self.request_hash}
        )
        if self.claim_id != expected_id:
            raise ContractValidationError("claim identity is detached")
        object.__setattr__(
            self,
            "receipt_hash",
            canonical_sha256(self, exclude_fields=("receipt_hash",)),
        )


def build_claim_review_case_request(
    case: HumanReviewCase,
    principal: ReviewerPrincipal,
    *,
    requested_at: datetime,
    idempotency_key: str,
) -> ClaimReviewCaseRequest:
    verify_human_review_case(case)
    verify_reviewer_principal(principal)
    return ClaimReviewCaseRequest(
        schema_version=STEP34_SCHEMA_VERSION,
        tenant_id=case.tenant_id,
        review_case_id=case.review_case_id,
        review_case_hash=case.case_hash,
        reviewer_principal_hash=principal.principal_hash,
        reviewer_id=principal.reviewer_id,
        reviewer_role=principal.reviewer_role,
        expected_state=ReviewState.OPEN,
        expected_state_version=1,
        requested_at=requested_at,
        idempotency_key=idempotency_key,
    )


@dataclass(frozen=True, slots=True)
class ReviewQueueCursor:
    filter_digest: str
    priority_rank: int
    created_at: datetime
    review_case_id: str
    cursor_hash: str = field(init=False)

    def __post_init__(self) -> None:
        _digest(self.filter_digest, "filter_digest")
        _integer(self.priority_rank, "priority_rank", 0)
        object.__setattr__(self, "created_at", _timestamp(self.created_at, "created_at"))
        _logical_id(self.review_case_id, "review_case_id")
        object.__setattr__(
            self,
            "cursor_hash",
            canonical_sha256(self, exclude_fields=("cursor_hash",)),
        )


@dataclass(frozen=True, slots=True)
class ReviewQueueRequest:
    schema_version: str
    tenant_id: str
    reviewer_principal_hash: str
    reviewer_id: str
    reviewer_role: ReviewerRole
    case_types: tuple[ReviewCaseType, ...]
    page_size: int
    continuation: ReviewQueueCursor | None
    requested_at: datetime
    filter_digest: str = field(init=False)
    request_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != STEP34_SCHEMA_VERSION:
            raise ContractValidationError("unsupported review queue request schema")
        _logical_id(self.tenant_id, "tenant_id")
        _digest(self.reviewer_principal_hash, "reviewer_principal_hash")
        _logical_id(self.reviewer_id, "reviewer_id")
        _enum(self.reviewer_role, ReviewerRole, "reviewer_role")
        if not isinstance(self.case_types, (tuple, list)) or not self.case_types:
            raise ContractValidationError("queue requires a bounded case-type filter")
        case_types = tuple(self.case_types)
        if any(not isinstance(item, ReviewCaseType) for item in case_types):
            raise ContractValidationError("queue case type is invalid")
        if case_types != tuple(sorted(set(case_types), key=lambda item: item.value)):
            raise ContractValidationError("queue case types must be sorted and unique")
        if any(item not in _role_case_types(self.reviewer_role) for item in case_types):
            raise ContractValidationError("queue filter exceeds reviewer role")
        object.__setattr__(self, "case_types", case_types)
        if (
            not isinstance(self.page_size, int)
            or isinstance(self.page_size, bool)
            or not 1 <= self.page_size <= MAXIMUM_REVIEW_QUEUE_PAGE_SIZE
        ):
            raise ContractValidationError("queue page size exceeds its bound")
        if self.continuation is not None and not isinstance(
            self.continuation, ReviewQueueCursor
        ):
            raise ContractValidationError("queue continuation must be typed")
        object.__setattr__(self, "requested_at", _timestamp(self.requested_at, "requested_at"))
        digest = canonical_sha256(
            {
                "tenant_id": self.tenant_id,
                "reviewer_id": self.reviewer_id,
                "reviewer_role": self.reviewer_role,
                "case_types": case_types,
                "page_size": self.page_size,
            }
        )
        if self.continuation is not None and self.continuation.filter_digest != digest:
            raise ContractValidationError("queue continuation filter changed")
        object.__setattr__(self, "filter_digest", digest)
        object.__setattr__(
            self,
            "request_hash",
            canonical_sha256(self, exclude_fields=("request_hash",)),
        )


@dataclass(frozen=True, slots=True)
class ReviewQueueItem:
    review_case_id: str
    case_type: ReviewCaseType
    subject_type: ReviewSubjectType
    priority: ReviewPriority
    review_state: ReviewState
    review_state_version: int
    owner_scope_digest: str
    safe_summary: str
    safe_summary_digest: str
    created_at: datetime
    case_hash: str
    item_hash: str = field(init=False)

    def __post_init__(self) -> None:
        _logical_id(self.review_case_id, "review_case_id")
        _enum(self.case_type, ReviewCaseType, "case_type")
        _enum(self.subject_type, ReviewSubjectType, "subject_type")
        _enum(self.priority, ReviewPriority, "priority")
        if self.review_state not in {ReviewState.OPEN, ReviewState.CLAIMED}:
            raise ContractValidationError("queue item is not reviewable")
        _integer(self.review_state_version, "review_state_version", 1)
        _digest(self.owner_scope_digest, "owner_scope_digest")
        summary = _text(self.safe_summary, "safe_summary", 256)
        if canonical_sha256(summary) != self.safe_summary_digest:
            raise ContractValidationError("queue summary digest differs")
        object.__setattr__(self, "created_at", _timestamp(self.created_at, "created_at"))
        _digest(self.case_hash, "case_hash")
        object.__setattr__(
            self, "item_hash", canonical_sha256(self, exclude_fields=("item_hash",))
        )


@dataclass(frozen=True, slots=True)
class ReviewQueuePage:
    request_hash: str
    reviewer_principal_hash: str
    items: tuple[ReviewQueueItem, ...]
    truncated: bool
    continuation: ReviewQueueCursor | None
    deterministic_order: str = "priority_desc,created_at,review_case_id"
    minimum_disclosure: bool = True
    page_hash: str = field(init=False)

    def __post_init__(self) -> None:
        _digest(self.request_hash, "request_hash")
        _digest(self.reviewer_principal_hash, "reviewer_principal_hash")
        if not isinstance(self.items, (tuple, list)):
            raise ContractValidationError("queue items must be ordered")
        items = tuple(self.items)
        expected = tuple(
            sorted(
                items,
                key=lambda item: (
                    -REVIEW_PRIORITY_RANK[item.priority],
                    item.created_at,
                    item.review_case_id,
                ),
            )
        )
        if items != expected or any(not isinstance(item, ReviewQueueItem) for item in items):
            raise ContractValidationError("queue order is not deterministic")
        object.__setattr__(self, "items", items)
        if not isinstance(self.truncated, bool):
            raise ContractValidationError("truncated must be boolean")
        if self.truncated != (self.continuation is not None):
            raise ContractValidationError("queue continuation/truncation mismatch")
        if self.deterministic_order != "priority_desc,created_at,review_case_id" or self.minimum_disclosure is not True:
            raise ContractValidationError("queue disclosure/order boundary changed")
        object.__setattr__(
            self, "page_hash", canonical_sha256(self, exclude_fields=("page_hash",))
        )


@dataclass(frozen=True, slots=True)
class HumanReviewDetailProjection:
    schema_version: str
    review_case: HumanReviewCase
    source_context: ReviewSourceContext
    audit_event_hashes: tuple[str, ...]
    audit_context_verified: bool
    audit_verification_result_hash: str
    minimum_disclosure: bool
    render_untrusted_text_as_text: bool
    auto_open_links: bool
    contains_unrelated_private_data: bool
    detail_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != STEP34_SCHEMA_VERSION:
            raise ContractValidationError("unsupported review detail schema")
        verify_human_review_case(self.review_case)
        verify_review_source_context(self.source_context)
        if self.source_context.context_hash != self.review_case.source_context_hash:
            raise ContractValidationError("detail source context differs from case")
        if not isinstance(self.audit_event_hashes, (tuple, list)):
            raise ContractValidationError("audit references must be ordered")
        audit = tuple(self.audit_event_hashes)
        if len(audit) > MAXIMUM_REVIEW_AUDIT_REFERENCES:
            raise ContractValidationError("detail audit references exceed bound")
        for index, value in enumerate(audit):
            _digest(value, f"audit_event_hashes[{index}]")
        if len(set(audit)) != len(audit):
            raise ContractValidationError("detail audit references repeat")
        object.__setattr__(self, "audit_event_hashes", audit)
        _digest(self.audit_verification_result_hash, "audit_verification_result_hash")
        if (
            self.audit_context_verified is not self.review_case.audit_context_verified
            or self.audit_verification_result_hash
            != self.review_case.audit_verification_result_hash
        ):
            raise ContractValidationError("detail audit status differs from case")
        if (
            self.minimum_disclosure is not True
            or self.render_untrusted_text_as_text is not True
            or self.auto_open_links is not False
            or self.contains_unrelated_private_data is not False
        ):
            raise ContractValidationError("review detail safety boundary changed")
        object.__setattr__(
            self, "detail_hash", canonical_sha256(self, exclude_fields=("detail_hash",))
        )
        if len(canonical_json_bytes(self)) > MAXIMUM_REVIEW_DETAIL_BYTES:
            raise ContractValidationError("review detail exceeds its byte bound")


def _allowed_decisions(case_type: ReviewCaseType) -> tuple[ReviewDecisionType, ...]:
    if case_type is ReviewCaseType.ANSWER_CONFIRMATION_REQUIRED:
        values = (
            ReviewDecisionType.ALLOW_QUALIFIED_ANSWER,
            ReviewDecisionType.ESCALATE,
            ReviewDecisionType.REJECT_ANSWER,
        )
    elif case_type is ReviewCaseType.ANSWER_VERIFICATION_FAILURE:
        values = (
            ReviewDecisionType.ALLOW_QUALIFIED_ANSWER,
            ReviewDecisionType.CONFIRMATION_REQUIRED,
            ReviewDecisionType.ESCALATE,
            ReviewDecisionType.REJECT_ANSWER,
            ReviewDecisionType.REQUEST_MORE_EVIDENCE,
        )
    elif case_type in {
        ReviewCaseType.ANSWER_CONFLICTING_EVIDENCE,
        ReviewCaseType.ANSWER_INSUFFICIENT_EVIDENCE,
        ReviewCaseType.ANSWER_STALE_EVIDENCE,
    }:
        values = (
            ReviewDecisionType.CONFIRMATION_REQUIRED,
            ReviewDecisionType.ESCALATE,
            ReviewDecisionType.REJECT_ANSWER,
            ReviewDecisionType.REQUEST_MORE_EVIDENCE,
        )
    elif case_type is ReviewCaseType.SHARED_MEMORY_PROMOTION:
        values = (
            ReviewDecisionType.APPROVE_SHARED_PROMOTION_CANDIDATE,
            ReviewDecisionType.ESCALATE,
            ReviewDecisionType.REJECT_SHARED_PROMOTION,
            ReviewDecisionType.REQUEST_MORE_EVIDENCE,
            ReviewDecisionType.REQUEST_REDACTION_CHANGES,
        )
    else:
        values = (
            ReviewDecisionType.ESCALATE,
            ReviewDecisionType.REJECT_SHARED_PROMOTION,
            ReviewDecisionType.REQUEST_MORE_EVIDENCE,
            ReviewDecisionType.REQUEST_REDACTION_CHANGES,
        )
    return tuple(sorted(values, key=lambda item: item.value))


@dataclass(frozen=True, slots=True)
class ReviewDecisionPolicy:
    schema_version: str = STEP34_SCHEMA_VERSION
    policy_id: str = STEP34_DECISION_POLICY_ID
    policy_version: str = STEP34_DECISION_POLICY_VERSION
    audit_integrity_required: bool = True
    changed_subject_rejected: bool = True
    reviewer_notes_are_evidence: bool = False
    arbitrary_business_mutation: bool = False
    source_publication_authority: bool = False
    external_execution_authority: bool = False
    policy_digest: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != STEP34_SCHEMA_VERSION:
            raise ContractValidationError("unsupported review decision policy")
        _logical_id(self.policy_id, "policy_id")
        _logical_id(self.policy_version, "policy_version")
        if (
            self.audit_integrity_required is not True
            or self.changed_subject_rejected is not True
            or self.reviewer_notes_are_evidence is not False
            or self.arbitrary_business_mutation is not False
            or self.source_publication_authority is not False
            or self.external_execution_authority is not False
        ):
            raise ContractValidationError("review decision policy grants authority")
        object.__setattr__(
            self,
            "policy_digest",
            canonical_sha256(
                {
                    **to_canonical_data(self, exclude_fields=("policy_digest",)),
                    "allowed_decisions": {
                        case.value: [item.value for item in _allowed_decisions(case)]
                        for case in ReviewCaseType
                    },
                }
            ),
        )


@dataclass(frozen=True, slots=True)
class SubmitReviewDecision:
    schema_version: str
    tenant_id: str
    review_case_id: str
    review_case_hash: str
    subject_hash: str
    reviewer_principal_hash: str
    reviewer_id: str
    reviewer_role: ReviewerRole
    decision_type: ReviewDecisionType
    decision_reason_codes: tuple[ReviewReasonCode, ...]
    reviewer_note: str | None
    context_digest: str
    audit_verification_result_hash: str
    expected_state: ReviewState
    expected_state_version: int
    decided_at: datetime
    idempotency_key: str
    reviewer_note_digest: str | None = field(init=False)
    replay_identity: str = field(init=False)
    command_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != STEP34_SCHEMA_VERSION:
            raise ContractValidationError("unsupported review decision command schema")
        for name in ("tenant_id", "review_case_id", "reviewer_id", "idempotency_key"):
            _logical_id(getattr(self, name), name)
        for name in (
            "review_case_hash",
            "subject_hash",
            "reviewer_principal_hash",
            "context_digest",
            "audit_verification_result_hash",
        ):
            _digest(getattr(self, name), name)
        _enum(self.reviewer_role, ReviewerRole, "reviewer_role")
        _enum(self.decision_type, ReviewDecisionType, "decision_type")
        object.__setattr__(
            self, "decision_reason_codes", _reason_codes(self.decision_reason_codes)
        )
        note_digest = None
        if self.reviewer_note is not None:
            note = _text(self.reviewer_note, "reviewer_note", MAXIMUM_REVIEW_NOTE_BYTES)
            lowered = note.casefold()
            if any(token in lowered for token in _SECRET_VALUES):
                raise ContractValidationError("reviewer note contains secret-shaped text")
            try:
                assert_secret_free(
                    note,
                    surface="reviewer note",
                    reject_machine_paths=True,
                )
            except ValueError as error:
                raise ContractValidationError(
                    "reviewer note contains forbidden secret material"
                ) from error
            note_digest = canonical_sha256(note)
        object.__setattr__(self, "reviewer_note_digest", note_digest)
        if self.expected_state is not ReviewState.CLAIMED or self.expected_state_version != 2:
            raise ContractValidationError("decision must target CLAIMED version two")
        object.__setattr__(self, "decided_at", _timestamp(self.decided_at, "decided_at"))
        object.__setattr__(
            self,
            "replay_identity",
            _review_replay_identity(
                "decision", self.tenant_id, self.reviewer_id, self.idempotency_key
            ),
        )
        object.__setattr__(
            self,
            "command_hash",
            canonical_sha256(self, exclude_fields=("command_hash",)),
        )


@dataclass(frozen=True, slots=True)
class HumanReviewDecision:
    schema_version: str
    decision_id: str
    command_hash: str
    replay_identity: str
    review_case_id: str
    review_case_hash: str
    tenant_id: str
    owner_user_id: str
    reviewer_id: str
    reviewer_role: ReviewerRole
    case_type: ReviewCaseType
    decision_type: ReviewDecisionType
    decision_reason_codes: tuple[ReviewReasonCode, ...]
    reviewer_note: str | None
    reviewer_note_digest: str | None
    subject_hash: str
    context_digest: str
    audit_verification_result_hash: str
    expected_review_state: ReviewState
    expected_review_state_version: int
    decision_policy_id: str
    decision_policy_version: str
    decision_policy_digest: str
    decided_at: datetime
    reviewer_note_is_canonical_evidence: bool = False
    external_execution_authority: bool = False
    source_publication_authority: bool = False
    decision_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != STEP34_SCHEMA_VERSION:
            raise ContractValidationError("unsupported human review decision schema")
        for name in (
            "decision_id",
            "replay_identity",
            "review_case_id",
            "tenant_id",
            "owner_user_id",
            "reviewer_id",
        ):
            _logical_id(getattr(self, name), name)
        for name in (
            "command_hash",
            "review_case_hash",
            "subject_hash",
            "context_digest",
            "audit_verification_result_hash",
        ):
            _digest(getattr(self, name), name)
        _enum(self.reviewer_role, ReviewerRole, "reviewer_role")
        _enum(self.case_type, ReviewCaseType, "case_type")
        _enum(self.decision_type, ReviewDecisionType, "decision_type")
        if self.decision_type not in _allowed_decisions(self.case_type):
            raise ContractValidationError("decision is not allowed for this case type")
        object.__setattr__(
            self, "decision_reason_codes", _reason_codes(self.decision_reason_codes)
        )
        expected_note_digest = (
            None if self.reviewer_note is None else canonical_sha256(
                _text(self.reviewer_note, "reviewer_note", MAXIMUM_REVIEW_NOTE_BYTES)
            )
        )
        if self.reviewer_note_digest != expected_note_digest:
            raise ContractValidationError("reviewer note digest differs")
        if self.expected_review_state is not ReviewState.CLAIMED or self.expected_review_state_version != 2:
            raise ContractValidationError("decision state expectation differs")
        policy = ReviewDecisionPolicy()
        if (
            self.decision_policy_id != policy.policy_id
            or self.decision_policy_version != policy.policy_version
            or self.decision_policy_digest != policy.policy_digest
        ):
            raise ContractValidationError("review decision policy is detached")
        object.__setattr__(self, "decided_at", _timestamp(self.decided_at, "decided_at"))
        if (
            self.reviewer_note_is_canonical_evidence is not False
            or self.external_execution_authority is not False
            or self.source_publication_authority is not False
        ):
            raise ContractValidationError("review decision crossed authority boundary")
        expected_id = "human-review-decision-" + canonical_sha256(
            {"replay_identity": self.replay_identity, "command_hash": self.command_hash}
        )
        if self.decision_id != expected_id:
            raise ContractValidationError("decision identity is detached")
        object.__setattr__(
            self,
            "decision_hash",
            canonical_sha256(self, exclude_fields=("decision_hash",)),
        )


@dataclass(frozen=True, slots=True)
class HumanReviewDecisionReceipt:
    schema_version: str
    decision_id: str
    decision_hash: str
    review_case_id: str
    previous_case_hash: str
    in_review_case_hash: str
    reviewer_id: str
    reviewer_role: ReviewerRole
    subject_hash: str
    decision_type: ReviewDecisionType
    audit_event_hash: str
    handoff_result_hash: str | None
    handoff_completed: bool
    receipt_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != STEP34_SCHEMA_VERSION:
            raise ContractValidationError("unsupported decision receipt schema")
        for name in ("decision_id", "review_case_id", "reviewer_id"):
            _logical_id(getattr(self, name), name)
        for name in (
            "decision_hash",
            "previous_case_hash",
            "in_review_case_hash",
            "subject_hash",
            "audit_event_hash",
        ):
            _digest(getattr(self, name), name)
        _enum(self.reviewer_role, ReviewerRole, "reviewer_role")
        _enum(self.decision_type, ReviewDecisionType, "decision_type")
        _optional_digest(self.handoff_result_hash, "handoff_result_hash")
        if self.handoff_completed is not False or self.handoff_result_hash is not None:
            raise ContractValidationError("decision receipt cannot claim an early handoff")
        object.__setattr__(
            self,
            "receipt_hash",
            canonical_sha256(self, exclude_fields=("receipt_hash",)),
        )


def build_review_decision(
    command: SubmitReviewDecision,
    case: HumanReviewCase,
) -> HumanReviewDecision:
    verify_submit_review_decision(command)
    verify_human_review_case(case)
    if (
        case.tenant_id != command.tenant_id
        or case.review_case_id != command.review_case_id
        or case.case_hash != command.review_case_hash
        or case.subject_hash != command.subject_hash
        or case.review_state is not ReviewState.CLAIMED
        or case.review_state_version != 2
        or case.claimed_reviewer_id != command.reviewer_id
        or case.claimed_reviewer_role is not command.reviewer_role
        or case.audit_verification_result_hash
        != command.audit_verification_result_hash
    ):
        raise HumanReviewWorkspaceError(ReviewReasonCode.REVIEW_DECISION_STALE)
    if not case.audit_context_verified and command.decision_type is not ReviewDecisionType.ESCALATE:
        raise HumanReviewWorkspaceError(ReviewReasonCode.REVIEW_AUDIT_CONTEXT_INVALID)
    if command.decision_type not in _allowed_decisions(case.case_type):
        raise HumanReviewWorkspaceError(ReviewReasonCode.REVIEW_DECISION_NOT_ALLOWED)
    policy = ReviewDecisionPolicy()
    decision_id = "human-review-decision-" + canonical_sha256(
        {"replay_identity": command.replay_identity, "command_hash": command.command_hash}
    )
    return HumanReviewDecision(
        schema_version=STEP34_SCHEMA_VERSION,
        decision_id=decision_id,
        command_hash=command.command_hash,
        replay_identity=command.replay_identity,
        review_case_id=case.review_case_id,
        review_case_hash=case.case_hash,
        tenant_id=case.tenant_id,
        owner_user_id=case.owner_user_id,
        reviewer_id=command.reviewer_id,
        reviewer_role=command.reviewer_role,
        case_type=case.case_type,
        decision_type=command.decision_type,
        decision_reason_codes=command.decision_reason_codes,
        reviewer_note=command.reviewer_note,
        reviewer_note_digest=command.reviewer_note_digest,
        subject_hash=case.subject_hash,
        context_digest=command.context_digest,
        audit_verification_result_hash=command.audit_verification_result_hash,
        expected_review_state=command.expected_state,
        expected_review_state_version=command.expected_state_version,
        decision_policy_id=policy.policy_id,
        decision_policy_version=policy.policy_version,
        decision_policy_digest=policy.policy_digest,
        decided_at=command.decided_at,
    )


@dataclass(frozen=True, slots=True)
class ReviewBusinessHandoffResult:
    schema_version: str
    target: ReviewHandoffTarget
    review_case_id: str
    subject_id: str
    subject_hash: str
    decision_id: str
    decision_hash: str
    decision_type: ReviewDecisionType
    status: ReviewHandoffStatus
    accepted_for_typed_downstream: bool
    answer_returned: bool
    source_registry_published: bool
    private_source_mutated: bool
    external_execution_authority: bool
    result_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != STEP34_SCHEMA_VERSION:
            raise ContractValidationError("unsupported review handoff result schema")
        _enum(self.target, ReviewHandoffTarget, "target")
        for name in ("review_case_id", "subject_id", "decision_id"):
            _logical_id(getattr(self, name), name)
        _digest(self.subject_hash, "subject_hash")
        _digest(self.decision_hash, "decision_hash")
        _enum(self.decision_type, ReviewDecisionType, "decision_type")
        _enum(self.status, ReviewHandoffStatus, "status")
        if not isinstance(self.accepted_for_typed_downstream, bool):
            raise ContractValidationError("handoff acceptance must be boolean")
        if (
            self.answer_returned is not False
            or self.source_registry_published is not False
            or self.private_source_mutated is not False
            or self.external_execution_authority is not False
        ):
            raise ContractValidationError("Step 34 handoff crossed authority boundary")
        object.__setattr__(
            self, "result_hash", canonical_sha256(self, exclude_fields=("result_hash",))
        )


def build_review_handoff_result(
    decision: HumanReviewDecision,
    case: HumanReviewCase,
) -> ReviewBusinessHandoffResult:
    verify_human_review_decision(decision)
    verify_human_review_case(case)
    if (
        case.review_case_id != decision.review_case_id
        or case.subject_hash != decision.subject_hash
        or case.review_state is not ReviewState.IN_REVIEW
        or case.claimed_reviewer_id != decision.reviewer_id
    ):
        raise HumanReviewWorkspaceError(ReviewReasonCode.REVIEW_DECISION_STALE)
    answer_case = case.subject_type is ReviewSubjectType.ANSWER_REVIEW_RESULT
    status = {
        ReviewDecisionType.ALLOW_QUALIFIED_ANSWER: ReviewHandoffStatus.ACCEPTED,
        ReviewDecisionType.APPROVE_SHARED_PROMOTION_CANDIDATE: ReviewHandoffStatus.ACCEPTED,
        ReviewDecisionType.REJECT_ANSWER: ReviewHandoffStatus.REJECTED,
        ReviewDecisionType.REJECT_SHARED_PROMOTION: ReviewHandoffStatus.REJECTED,
        ReviewDecisionType.REQUEST_REDACTION_CHANGES: ReviewHandoffStatus.CHANGES_REQUESTED,
        ReviewDecisionType.REQUEST_MORE_EVIDENCE: ReviewHandoffStatus.MORE_EVIDENCE_REQUESTED,
        ReviewDecisionType.CONFIRMATION_REQUIRED: ReviewHandoffStatus.CONFIRMATION_REQUIRED,
        ReviewDecisionType.ESCALATE: ReviewHandoffStatus.ESCALATED,
    }[decision.decision_type]
    return ReviewBusinessHandoffResult(
        schema_version=STEP34_SCHEMA_VERSION,
        target=(
            ReviewHandoffTarget.STEP26_ANSWER_OUTPUT_SERVICE
            if answer_case
            else ReviewHandoffTarget.STEP32_SHARED_PROMOTION_REVIEW_BOUNDARY
        ),
        review_case_id=case.review_case_id,
        subject_id=case.subject_id,
        subject_hash=case.subject_hash,
        decision_id=decision.decision_id,
        decision_hash=decision.decision_hash,
        decision_type=decision.decision_type,
        status=status,
        accepted_for_typed_downstream=True,
        answer_returned=False,
        source_registry_published=False,
        private_source_mutated=False,
        external_execution_authority=False,
    )


@dataclass(frozen=True, slots=True)
class ReviewDecisionHandoffRequest:
    schema_version: str
    tenant_id: str
    review_case_id: str
    in_review_case_hash: str
    decision_id: str
    decision_hash: str
    decision_receipt_hash: str
    expected_state: ReviewState
    expected_state_version: int
    requested_at: datetime
    idempotency_key: str
    replay_identity: str = field(init=False)
    request_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != STEP34_SCHEMA_VERSION:
            raise ContractValidationError("unsupported review handoff request schema")
        for name in ("tenant_id", "review_case_id", "decision_id", "idempotency_key"):
            _logical_id(getattr(self, name), name)
        for name in ("in_review_case_hash", "decision_hash", "decision_receipt_hash"):
            _digest(getattr(self, name), name)
        if self.expected_state is not ReviewState.IN_REVIEW or self.expected_state_version != 3:
            raise ContractValidationError("handoff must target IN_REVIEW version three")
        object.__setattr__(self, "requested_at", _timestamp(self.requested_at, "requested_at"))
        object.__setattr__(
            self,
            "replay_identity",
            _review_replay_identity(
                "handoff", self.tenant_id, self.decision_id, self.idempotency_key
            ),
        )
        object.__setattr__(
            self,
            "request_hash",
            canonical_sha256(self, exclude_fields=("request_hash",)),
        )


@dataclass(frozen=True, slots=True)
class ReviewDecisionHandoffReceipt:
    schema_version: str
    handoff_id: str
    request_hash: str
    replay_identity: str
    review_case_id: str
    decision_id: str
    decision_hash: str
    decision_receipt_hash: str
    handoff_result_hash: str
    terminal_case_hash: str
    terminal_state: ReviewState
    audit_event_hash: str
    succeeded: bool
    receipt_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != STEP34_SCHEMA_VERSION:
            raise ContractValidationError("unsupported handoff receipt schema")
        for name in ("handoff_id", "replay_identity", "review_case_id", "decision_id"):
            _logical_id(getattr(self, name), name)
        for name in (
            "request_hash",
            "decision_hash",
            "decision_receipt_hash",
            "handoff_result_hash",
            "terminal_case_hash",
            "audit_event_hash",
        ):
            _digest(getattr(self, name), name)
        if self.terminal_state not in {ReviewState.RESOLVED, ReviewState.ESCALATED}:
            raise ContractValidationError("handoff terminal state is invalid")
        if self.succeeded is not True:
            raise ContractValidationError("persisted handoff receipt must be successful")
        expected_id = "review-decision-handoff-" + canonical_sha256(
            {"replay_identity": self.replay_identity, "request_hash": self.request_hash}
        )
        if self.handoff_id != expected_id:
            raise ContractValidationError("handoff identity is detached")
        object.__setattr__(
            self,
            "receipt_hash",
            canonical_sha256(self, exclude_fields=("receipt_hash",)),
        )


def build_review_handoff_request(
    case: HumanReviewCase,
    decision: HumanReviewDecision,
    decision_receipt: HumanReviewDecisionReceipt,
    *,
    requested_at: datetime,
    idempotency_key: str,
) -> ReviewDecisionHandoffRequest:
    verify_human_review_case(case)
    verify_human_review_decision(decision)
    verify_human_review_decision_receipt(decision_receipt)
    if (
        case.review_state is not ReviewState.IN_REVIEW
        or case.review_case_id != decision.review_case_id
        or decision_receipt.decision_hash != decision.decision_hash
        or decision_receipt.in_review_case_hash != case.case_hash
    ):
        raise HumanReviewWorkspaceError(ReviewReasonCode.REVIEW_DECISION_STALE)
    return ReviewDecisionHandoffRequest(
        schema_version=STEP34_SCHEMA_VERSION,
        tenant_id=case.tenant_id,
        review_case_id=case.review_case_id,
        in_review_case_hash=case.case_hash,
        decision_id=decision.decision_id,
        decision_hash=decision.decision_hash,
        decision_receipt_hash=decision_receipt.receipt_hash,
        expected_state=ReviewState.IN_REVIEW,
        expected_state_version=3,
        requested_at=requested_at,
        idempotency_key=idempotency_key,
    )


def review_to_jsonb(value: object) -> Mapping[str, Any]:
    result = to_canonical_data(value)
    if not isinstance(result, Mapping):
        raise ContractValidationError("Step 34 value must serialize as an object")
    return result


def _parse_mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ContractValidationError(f"{name} must be an object")
    return value


def _parse_time(value: object, name: str) -> datetime:
    if not isinstance(value, str):
        raise ContractValidationError(f"{name} must be a canonical timestamp")
    try:
        return ensure_utc(datetime.fromisoformat(value.replace("Z", "+00:00")), name)
    except ValueError as exc:
        raise ContractValidationError(f"{name} is invalid") from exc


def parse_review_source_context(value: object) -> ReviewSourceContext:
    data = _parse_mapping(value, "review source context")
    try:
        result = ReviewSourceContext(
            schema_version=data["schema_version"],
            source_contract=ReviewSourceContract(data["source_contract"]),
            subject_hash=data["subject_hash"],
            context_payload=_parse_mapping(data["context_payload"], "context_payload"),
            contains_raw_private_memory=data["contains_raw_private_memory"],
            canonical_evidence=data["canonical_evidence"],
            model_authority=data["model_authority"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ContractValidationError("review source context is invalid") from exc
    if result.context_hash != data.get("context_hash"):
        raise IntegrityError("persisted review source context hash differs")
    return result


def parse_human_review_case(value: object) -> HumanReviewCase:
    data = _parse_mapping(value, "human review case")
    try:
        result = HumanReviewCase(
            schema_version=data["schema_version"],
            review_case_id=data["review_case_id"],
            trigger_hash=data["trigger_hash"],
            case_type=ReviewCaseType(data["case_type"]),
            tenant_id=data["tenant_id"],
            owner_user_id=data["owner_user_id"],
            subject_type=ReviewSubjectType(data["subject_type"]),
            subject_id=data["subject_id"],
            subject_hash=data["subject_hash"],
            request_id=data["request_id"],
            kernel_run_id=data["kernel_run_id"],
            route_hash=data["route_hash"],
            review_reason_codes=tuple(ReviewReasonCode(item) for item in data["review_reason_codes"]),
            priority=ReviewPriority(data["priority"]),
            source_audit_event_hash=data["source_audit_event_hash"],
            source_chain_id=data["source_chain_id"],
            audit_verification_result_hash=data["audit_verification_result_hash"],
            audit_context_verified=data["audit_context_verified"],
            required_context_refs=_parse_mapping(data["required_context_refs"], "required_context_refs"),
            source_context_hash=data["source_context_hash"],
            review_state=ReviewState(data["review_state"]),
            review_state_version=data["review_state_version"],
            claimed_reviewer_id=data["claimed_reviewer_id"],
            claimed_reviewer_role=(
                None if data["claimed_reviewer_role"] is None else ReviewerRole(data["claimed_reviewer_role"])
            ),
            created_at=_parse_time(data["created_at"], "created_at"),
            updated_at=_parse_time(data["updated_at"], "updated_at"),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ContractValidationError("human review case is invalid") from exc
    if result.case_hash != data.get("case_hash"):
        raise IntegrityError("persisted review case hash differs")
    return result


def parse_reviewer_authorization(value: object) -> ReviewerAuthorization:
    data = _parse_mapping(value, "reviewer authorization")
    try:
        result = ReviewerAuthorization(
            schema_version=data["schema_version"],
            authorization_id=data["authorization_id"],
            tenant_id=data["tenant_id"],
            reviewer_id=data["reviewer_id"],
            reviewer_role=ReviewerRole(data["reviewer_role"]),
            case_type=ReviewCaseType(data["case_type"]),
            owner_user_id=data["owner_user_id"],
            access_policy_id=data["access_policy_id"],
            access_policy_version=data["access_policy_version"],
            access_policy_digest=data["access_policy_digest"],
            active=data["active"],
            granted_at=_parse_time(data["granted_at"], "granted_at"),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ContractValidationError("reviewer authorization is invalid") from exc
    if result.authorization_hash != data.get("authorization_hash"):
        raise IntegrityError("persisted reviewer authorization hash differs")
    return result


def parse_review_case_claim_receipt(value: object) -> ReviewCaseClaimReceipt:
    data = _parse_mapping(value, "review case claim receipt")
    try:
        result = ReviewCaseClaimReceipt(
            schema_version=data["schema_version"],
            claim_id=data["claim_id"],
            request_hash=data["request_hash"],
            replay_identity=data["replay_identity"],
            tenant_id=data["tenant_id"],
            review_case_id=data["review_case_id"],
            previous_case_hash=data["previous_case_hash"],
            claimed_case_hash=data["claimed_case_hash"],
            reviewer_id=data["reviewer_id"],
            reviewer_role=ReviewerRole(data["reviewer_role"]),
            claimed_at=_parse_time(data["claimed_at"], "claimed_at"),
            audit_event_hash=data["audit_event_hash"],
            reason_code=ReviewReasonCode(data["reason_code"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ContractValidationError("review case claim receipt is invalid") from exc
    if result.receipt_hash != data.get("receipt_hash"):
        raise IntegrityError("persisted claim receipt hash differs")
    return result


def parse_human_review_decision(value: object) -> HumanReviewDecision:
    data = _parse_mapping(value, "human review decision")
    try:
        result = HumanReviewDecision(
            schema_version=data["schema_version"],
            decision_id=data["decision_id"],
            command_hash=data["command_hash"],
            replay_identity=data["replay_identity"],
            review_case_id=data["review_case_id"],
            review_case_hash=data["review_case_hash"],
            tenant_id=data["tenant_id"],
            owner_user_id=data["owner_user_id"],
            reviewer_id=data["reviewer_id"],
            reviewer_role=ReviewerRole(data["reviewer_role"]),
            case_type=ReviewCaseType(data["case_type"]),
            decision_type=ReviewDecisionType(data["decision_type"]),
            decision_reason_codes=tuple(
                ReviewReasonCode(item) for item in data["decision_reason_codes"]
            ),
            reviewer_note=data["reviewer_note"],
            reviewer_note_digest=data["reviewer_note_digest"],
            subject_hash=data["subject_hash"],
            context_digest=data["context_digest"],
            audit_verification_result_hash=data["audit_verification_result_hash"],
            expected_review_state=ReviewState(data["expected_review_state"]),
            expected_review_state_version=data["expected_review_state_version"],
            decision_policy_id=data["decision_policy_id"],
            decision_policy_version=data["decision_policy_version"],
            decision_policy_digest=data["decision_policy_digest"],
            decided_at=_parse_time(data["decided_at"], "decided_at"),
            reviewer_note_is_canonical_evidence=data[
                "reviewer_note_is_canonical_evidence"
            ],
            external_execution_authority=data["external_execution_authority"],
            source_publication_authority=data["source_publication_authority"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ContractValidationError("human review decision is invalid") from exc
    if result.decision_hash != data.get("decision_hash"):
        raise IntegrityError("persisted review decision hash differs")
    return result


def parse_human_review_decision_receipt(
    value: object,
) -> HumanReviewDecisionReceipt:
    data = _parse_mapping(value, "human review decision receipt")
    try:
        result = HumanReviewDecisionReceipt(
            schema_version=data["schema_version"],
            decision_id=data["decision_id"],
            decision_hash=data["decision_hash"],
            review_case_id=data["review_case_id"],
            previous_case_hash=data["previous_case_hash"],
            in_review_case_hash=data["in_review_case_hash"],
            reviewer_id=data["reviewer_id"],
            reviewer_role=ReviewerRole(data["reviewer_role"]),
            subject_hash=data["subject_hash"],
            decision_type=ReviewDecisionType(data["decision_type"]),
            audit_event_hash=data["audit_event_hash"],
            handoff_result_hash=data["handoff_result_hash"],
            handoff_completed=data["handoff_completed"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ContractValidationError("human review decision receipt is invalid") from exc
    if result.receipt_hash != data.get("receipt_hash"):
        raise IntegrityError("persisted review decision receipt hash differs")
    return result


def parse_review_handoff_result(value: object) -> ReviewBusinessHandoffResult:
    data = _parse_mapping(value, "review handoff result")
    try:
        result = ReviewBusinessHandoffResult(
            schema_version=data["schema_version"],
            target=ReviewHandoffTarget(data["target"]),
            review_case_id=data["review_case_id"],
            subject_id=data["subject_id"],
            subject_hash=data["subject_hash"],
            decision_id=data["decision_id"],
            decision_hash=data["decision_hash"],
            decision_type=ReviewDecisionType(data["decision_type"]),
            status=ReviewHandoffStatus(data["status"]),
            accepted_for_typed_downstream=data["accepted_for_typed_downstream"],
            answer_returned=data["answer_returned"],
            source_registry_published=data["source_registry_published"],
            private_source_mutated=data["private_source_mutated"],
            external_execution_authority=data["external_execution_authority"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ContractValidationError("review handoff result is invalid") from exc
    if result.result_hash != data.get("result_hash"):
        raise IntegrityError("persisted review handoff result hash differs")
    return result


def parse_review_handoff_receipt(value: object) -> ReviewDecisionHandoffReceipt:
    data = _parse_mapping(value, "review handoff receipt")
    try:
        result = ReviewDecisionHandoffReceipt(
            schema_version=data["schema_version"],
            handoff_id=data["handoff_id"],
            request_hash=data["request_hash"],
            replay_identity=data["replay_identity"],
            review_case_id=data["review_case_id"],
            decision_id=data["decision_id"],
            decision_hash=data["decision_hash"],
            decision_receipt_hash=data["decision_receipt_hash"],
            handoff_result_hash=data["handoff_result_hash"],
            terminal_case_hash=data["terminal_case_hash"],
            terminal_state=ReviewState(data["terminal_state"]),
            audit_event_hash=data["audit_event_hash"],
            succeeded=data["succeeded"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ContractValidationError("review handoff receipt is invalid") from exc
    if result.receipt_hash != data.get("receipt_hash"):
        raise IntegrityError("persisted handoff receipt hash differs")
    return result


def _verify(value: object, digest: str, field_name: str, name: str) -> None:
    verify_canonical_hash(value, digest, exclude_fields=(field_name,))
    _reconstruct(value, name)


def verify_reviewer_principal(value: ReviewerPrincipal) -> None:
    _verify(value, value.principal_hash, "principal_hash", "reviewer principal")


def verify_reviewer_authorization(value: ReviewerAuthorization) -> None:
    _verify(value, value.authorization_hash, "authorization_hash", "reviewer authorization")


def verify_review_source_context(value: ReviewSourceContext) -> None:
    _verify(value, value.context_hash, "context_hash", "review source context")


def verify_human_review_case(value: HumanReviewCase) -> None:
    _verify(value, value.case_hash, "case_hash", "human review case")


def verify_review_case_source_binding(
    case: HumanReviewCase,
    context: ReviewSourceContext,
) -> None:
    """Require one exact Step 26 or Step 32 typed intake relationship."""

    verify_human_review_case(case)
    verify_review_source_context(context)
    if (
        context.context_hash != case.source_context_hash
        or context.subject_hash != case.subject_hash
    ):
        raise IntegrityError("review case/source context binding differs")

    answer_types = {
        ReviewCaseType.ANSWER_VERIFICATION_FAILURE,
        ReviewCaseType.ANSWER_CONFLICTING_EVIDENCE,
        ReviewCaseType.ANSWER_INSUFFICIENT_EVIDENCE,
        ReviewCaseType.ANSWER_STALE_EVIDENCE,
        ReviewCaseType.ANSWER_CONFIRMATION_REQUIRED,
    }
    shared_types = {
        ReviewCaseType.SHARED_MEMORY_PROMOTION,
        ReviewCaseType.SHARED_PROMOTION_PRIVACY_REVIEW,
        ReviewCaseType.SHARED_PROMOTION_CANONICAL_CONFLICT,
    }
    if case.case_type in answer_types:
        if (
            case.subject_type is not ReviewSubjectType.ANSWER_REVIEW_RESULT
            or context.source_contract
            not in {
                ReviewSourceContract.STEP26_HUMAN_REVIEW_REQUIRED,
                ReviewSourceContract.STEP26_BOUNDED_ANSWER_FAILURE,
            }
            or case.request_id is None
            or case.route_hash is None
            or context.context_payload.get("answer_returned") is not False
        ):
            raise ContractValidationError("answer review source relationship is invalid")
        required_key = (
            "human_review_result_hash"
            if context.source_contract
            is ReviewSourceContract.STEP26_HUMAN_REVIEW_REQUIRED
            else "bounded_failure_hash"
        )
    elif case.case_type in shared_types:
        if (
            case.subject_type
            is not ReviewSubjectType.SHARED_MEMORY_PROMOTION_PROPOSAL
            or context.source_contract
            is not ReviewSourceContract.STEP32_SHARED_MEMORY_PROMOTION_PROPOSAL
            or case.request_id is not None
            or case.kernel_run_id is not None
            or case.route_hash is not None
            or context.context_payload.get("review_required") is not True
            or context.context_payload.get("source_registry_published") is not False
        ):
            raise ContractValidationError(
                "shared-promotion review source relationship is invalid"
            )
        required_key = "promotion_proposal_hash"
    else:  # pragma: no cover - the closed enum makes this defensive
        raise ContractValidationError("unsupported review case family")

    if case.required_context_refs.get(required_key) != case.subject_hash:
        raise ContractValidationError("review subject reference is detached")


def verify_claim_review_case_request(value: ClaimReviewCaseRequest) -> None:
    _verify(value, value.request_hash, "request_hash", "claim review case request")


def verify_review_case_claim_receipt(value: ReviewCaseClaimReceipt) -> None:
    _verify(value, value.receipt_hash, "receipt_hash", "review case claim receipt")


def verify_submit_review_decision(value: SubmitReviewDecision) -> None:
    _verify(value, value.command_hash, "command_hash", "submit review decision")


def verify_human_review_decision(value: HumanReviewDecision) -> None:
    _verify(value, value.decision_hash, "decision_hash", "human review decision")


def verify_human_review_decision_receipt(value: HumanReviewDecisionReceipt) -> None:
    _verify(value, value.receipt_hash, "receipt_hash", "review decision receipt")


def verify_review_handoff_result(value: ReviewBusinessHandoffResult) -> None:
    _verify(value, value.result_hash, "result_hash", "review handoff result")


def verify_review_handoff_request(value: ReviewDecisionHandoffRequest) -> None:
    _verify(value, value.request_hash, "request_hash", "review handoff request")


def verify_review_handoff_receipt(value: ReviewDecisionHandoffReceipt) -> None:
    _verify(value, value.receipt_hash, "receipt_hash", "review handoff receipt")


__all__ = [name for name in globals() if not name.startswith("_")]
