"""Step 37 contracts for deterministic, test-only failure injection.

Production services do not import or construct a scripted injector.  They keep
their existing narrow ports; tests replace those ports with protocol-compatible
fault adapters.  The production-facing default in this module is therefore an
inert :class:`NoOpFailureInjector` only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from aioa_memory_kernel.contracts.enums import StableStringEnum
from aioa_memory_kernel.contracts.exceptions import ContractValidationError, IntegrityError
from aioa_memory_kernel.contracts.serialization import (
    canonical_sha256,
    require_enum_member,
    require_non_empty,
    require_sha256_hex,
)


STEP37_SCHEMA_VERSION = "1.0.0"
FAILURE_INJECTOR_VERSION = "step37-deterministic-failure-injector-1a"
FAILURE_POINT_REGISTRY_VERSION = "step37-closed-failure-point-registry-1a"
RECOVERY_POLICY_VERSION = "step37-recovery-policy-1a"
MAXIMUM_FAILURE_ATTEMPTS = 10
MAXIMUM_REASON_CODES = 16

_CASE_ID = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,127}$")
_REASON_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")


class FailureDomain(StableStringEnum):
    DATABASE = "DATABASE"
    TRANSACTION_RETRY = "TRANSACTION_RETRY"
    PROCESS_CRASH = "PROCESS_CRASH"
    INGESTION_SAGA = "INGESTION_SAGA"
    MODEL_PROVIDER = "MODEL_PROVIDER"
    S3_OBJECT_STORE = "S3_OBJECT_STORE"
    EXTERNAL_VOLUME = "EXTERNAL_VOLUME"
    AUDIT_LEDGER = "AUDIT_LEDGER"
    PERSONAL_MEMORY_APPROVAL = "PERSONAL_MEMORY_APPROVAL"
    PERSONAL_MEMORY_COMMIT = "PERSONAL_MEMORY_COMMIT"
    PERSONAL_MEMORY_ACTIVATION = "PERSONAL_MEMORY_ACTIVATION"
    PERSONAL_MEMORY_LIFECYCLE = "PERSONAL_MEMORY_LIFECYCLE"
    REVIEW_HANDOFF = "REVIEW_HANDOFF"
    CREDENTIAL_UNAVAILABLE = "CREDENTIAL_UNAVAILABLE"


class FailurePoint(StableStringEnum):
    DB_BEFORE_BEGIN = "DB_BEFORE_BEGIN"
    DB_BEFORE_COMMIT = "DB_BEFORE_COMMIT"
    DB_COMMIT_SERIALIZATION_FAILURE = "DB_COMMIT_SERIALIZATION_FAILURE"
    DB_AFTER_COMMIT_ACK_LOST = "DB_AFTER_COMMIT_ACK_LOST"
    DB_READ_FAILURE = "DB_READ_FAILURE"

    PROCESS_AFTER_DURABLE_WRITE = "PROCESS_AFTER_DURABLE_WRITE"
    PROCESS_AFTER_SAGA_CHECKPOINT = "PROCESS_AFTER_SAGA_CHECKPOINT"

    SAGA_AFTER_STATE_WRITE = "SAGA_AFTER_STATE_WRITE"
    SAGA_AFTER_OBJECT_WRITE = "SAGA_AFTER_OBJECT_WRITE"
    SAGA_BEFORE_FINALIZE = "SAGA_BEFORE_FINALIZE"

    PROVIDER_TIMEOUT = "PROVIDER_TIMEOUT"
    PROVIDER_TRANSIENT_FAILURE = "PROVIDER_TRANSIENT_FAILURE"
    PROVIDER_RESPONSE_LOST = "PROVIDER_RESPONSE_LOST"
    PROVIDER_AUTH_FAILURE = "PROVIDER_AUTH_FAILURE"
    PROVIDER_INVALID_RESPONSE = "PROVIDER_INVALID_RESPONSE"
    PROVIDER_OVERSIZED_RESPONSE = "PROVIDER_OVERSIZED_RESPONSE"

    S3_PUT_FAILURE = "S3_PUT_FAILURE"
    S3_ACK_LOST = "S3_ACK_LOST"
    S3_READ_FAILURE = "S3_READ_FAILURE"
    S3_CHECKSUM_MISMATCH = "S3_CHECKSUM_MISMATCH"
    S3_OBJECT_LOCK_REJECTION = "S3_OBJECT_LOCK_REJECTION"

    VOLUME_MISSING = "VOLUME_MISSING"
    VOLUME_WRITE_FAILURE = "VOLUME_WRITE_FAILURE"
    VOLUME_RENAME_FAILURE = "VOLUME_RENAME_FAILURE"
    VOLUME_CACHE_CORRUPTION = "VOLUME_CACHE_CORRUPTION"

    AUDIT_BEFORE_APPEND = "AUDIT_BEFORE_APPEND"
    AUDIT_AFTER_APPEND_ACK_LOST = "AUDIT_AFTER_APPEND_ACK_LOST"
    AUDIT_CHAIN_HEAD_CONTENTION = "AUDIT_CHAIN_HEAD_CONTENTION"
    AUDIT_CHAIN_TAMPER = "AUDIT_CHAIN_TAMPER"

    PM_BEFORE_APPROVAL = "PM_BEFORE_APPROVAL"
    PM_AFTER_APPROVAL_ACK_LOST = "PM_AFTER_APPROVAL_ACK_LOST"
    PM_BEFORE_COMMIT = "PM_BEFORE_COMMIT"
    PM_AFTER_COMMIT_ACK_LOST = "PM_AFTER_COMMIT_ACK_LOST"
    PM_BEFORE_ACTIVATION = "PM_BEFORE_ACTIVATION"
    PM_AFTER_ACTIVATION_ACK_LOST = "PM_AFTER_ACTIVATION_ACK_LOST"
    PM_LIFECYCLE_BEFORE_COMMIT = "PM_LIFECYCLE_BEFORE_COMMIT"
    PM_LIFECYCLE_AFTER_COMMIT_ACK_LOST = "PM_LIFECYCLE_AFTER_COMMIT_ACK_LOST"
    PM_EXPORT_INTERRUPTED = "PM_EXPORT_INTERRUPTED"

    REVIEW_BEFORE_HANDOFF = "REVIEW_BEFORE_HANDOFF"
    REVIEW_AFTER_HANDOFF_ACK_LOST = "REVIEW_AFTER_HANDOFF_ACK_LOST"

    CREDENTIAL_COMMIT_HELPER_UNAVAILABLE = "CREDENTIAL_COMMIT_HELPER_UNAVAILABLE"
    CREDENTIAL_PROVIDER_UNAVAILABLE = "CREDENTIAL_PROVIDER_UNAVAILABLE"
    CREDENTIAL_REVIEWER_UNAVAILABLE = "CREDENTIAL_REVIEWER_UNAVAILABLE"
    CREDENTIAL_AUDIT_APPENDER_UNAVAILABLE = "CREDENTIAL_AUDIT_APPENDER_UNAVAILABLE"


class RecoveryStatus(StableStringEnum):
    RECOVERED_BY_RETRY = "RECOVERED_BY_RETRY"
    RECOVERED_BY_IDEMPOTENT_REPLAY = "RECOVERED_BY_IDEMPOTENT_REPLAY"
    RECOVERED_BY_RESUME = "RECOVERED_BY_RESUME"
    RECOVERED_BY_REBUILD = "RECOVERED_BY_REBUILD"
    COMPENSATED = "COMPENSATED"
    FAILED_CLOSED = "FAILED_CLOSED"
    HUMAN_REVIEW_REQUIRED = "HUMAN_REVIEW_REQUIRED"
    MANUAL_OPERATOR_RECOVERY_REQUIRED = "MANUAL_OPERATOR_RECOVERY_REQUIRED"


FAILURE_POINT_DOMAINS = {
    FailurePoint.DB_BEFORE_BEGIN: FailureDomain.DATABASE,
    FailurePoint.DB_BEFORE_COMMIT: FailureDomain.DATABASE,
    FailurePoint.DB_COMMIT_SERIALIZATION_FAILURE: FailureDomain.TRANSACTION_RETRY,
    FailurePoint.DB_AFTER_COMMIT_ACK_LOST: FailureDomain.DATABASE,
    FailurePoint.DB_READ_FAILURE: FailureDomain.DATABASE,
    FailurePoint.PROCESS_AFTER_DURABLE_WRITE: FailureDomain.PROCESS_CRASH,
    FailurePoint.PROCESS_AFTER_SAGA_CHECKPOINT: FailureDomain.PROCESS_CRASH,
    FailurePoint.SAGA_AFTER_STATE_WRITE: FailureDomain.INGESTION_SAGA,
    FailurePoint.SAGA_AFTER_OBJECT_WRITE: FailureDomain.INGESTION_SAGA,
    FailurePoint.SAGA_BEFORE_FINALIZE: FailureDomain.INGESTION_SAGA,
    FailurePoint.PROVIDER_TIMEOUT: FailureDomain.MODEL_PROVIDER,
    FailurePoint.PROVIDER_TRANSIENT_FAILURE: FailureDomain.MODEL_PROVIDER,
    FailurePoint.PROVIDER_RESPONSE_LOST: FailureDomain.MODEL_PROVIDER,
    FailurePoint.PROVIDER_AUTH_FAILURE: FailureDomain.MODEL_PROVIDER,
    FailurePoint.PROVIDER_INVALID_RESPONSE: FailureDomain.MODEL_PROVIDER,
    FailurePoint.PROVIDER_OVERSIZED_RESPONSE: FailureDomain.MODEL_PROVIDER,
    FailurePoint.S3_PUT_FAILURE: FailureDomain.S3_OBJECT_STORE,
    FailurePoint.S3_ACK_LOST: FailureDomain.S3_OBJECT_STORE,
    FailurePoint.S3_READ_FAILURE: FailureDomain.S3_OBJECT_STORE,
    FailurePoint.S3_CHECKSUM_MISMATCH: FailureDomain.S3_OBJECT_STORE,
    FailurePoint.S3_OBJECT_LOCK_REJECTION: FailureDomain.S3_OBJECT_STORE,
    FailurePoint.VOLUME_MISSING: FailureDomain.EXTERNAL_VOLUME,
    FailurePoint.VOLUME_WRITE_FAILURE: FailureDomain.EXTERNAL_VOLUME,
    FailurePoint.VOLUME_RENAME_FAILURE: FailureDomain.EXTERNAL_VOLUME,
    FailurePoint.VOLUME_CACHE_CORRUPTION: FailureDomain.EXTERNAL_VOLUME,
    FailurePoint.AUDIT_BEFORE_APPEND: FailureDomain.AUDIT_LEDGER,
    FailurePoint.AUDIT_AFTER_APPEND_ACK_LOST: FailureDomain.AUDIT_LEDGER,
    FailurePoint.AUDIT_CHAIN_HEAD_CONTENTION: FailureDomain.AUDIT_LEDGER,
    FailurePoint.AUDIT_CHAIN_TAMPER: FailureDomain.AUDIT_LEDGER,
    FailurePoint.PM_BEFORE_APPROVAL: FailureDomain.PERSONAL_MEMORY_APPROVAL,
    FailurePoint.PM_AFTER_APPROVAL_ACK_LOST: FailureDomain.PERSONAL_MEMORY_APPROVAL,
    FailurePoint.PM_BEFORE_COMMIT: FailureDomain.PERSONAL_MEMORY_COMMIT,
    FailurePoint.PM_AFTER_COMMIT_ACK_LOST: FailureDomain.PERSONAL_MEMORY_COMMIT,
    FailurePoint.PM_BEFORE_ACTIVATION: FailureDomain.PERSONAL_MEMORY_ACTIVATION,
    FailurePoint.PM_AFTER_ACTIVATION_ACK_LOST: FailureDomain.PERSONAL_MEMORY_ACTIVATION,
    FailurePoint.PM_LIFECYCLE_BEFORE_COMMIT: FailureDomain.PERSONAL_MEMORY_LIFECYCLE,
    FailurePoint.PM_LIFECYCLE_AFTER_COMMIT_ACK_LOST: FailureDomain.PERSONAL_MEMORY_LIFECYCLE,
    FailurePoint.PM_EXPORT_INTERRUPTED: FailureDomain.PERSONAL_MEMORY_LIFECYCLE,
    FailurePoint.REVIEW_BEFORE_HANDOFF: FailureDomain.REVIEW_HANDOFF,
    FailurePoint.REVIEW_AFTER_HANDOFF_ACK_LOST: FailureDomain.REVIEW_HANDOFF,
    FailurePoint.CREDENTIAL_COMMIT_HELPER_UNAVAILABLE: FailureDomain.CREDENTIAL_UNAVAILABLE,
    FailurePoint.CREDENTIAL_PROVIDER_UNAVAILABLE: FailureDomain.CREDENTIAL_UNAVAILABLE,
    FailurePoint.CREDENTIAL_REVIEWER_UNAVAILABLE: FailureDomain.CREDENTIAL_UNAVAILABLE,
    FailurePoint.CREDENTIAL_AUDIT_APPENDER_UNAVAILABLE: FailureDomain.CREDENTIAL_UNAVAILABLE,
}
if set(FAILURE_POINT_DOMAINS) != set(FailurePoint):
    raise RuntimeError("Step 37 failure-point registry is incomplete")


class FailureInjector(Protocol):
    """One inert-or-test implementation at a closed architecture boundary."""

    def hit(self, failure_point: FailurePoint, *, subject_hash: str) -> None: ...


class NoOpFailureInjector:
    """Production-safe default.  It validates identity and never fails."""

    __slots__ = ()

    @property
    def test_only(self) -> bool:
        return False

    def hit(self, failure_point: FailurePoint, *, subject_hash: str) -> None:
        require_enum_member(failure_point, FailurePoint, "failure_point")
        require_sha256_hex(subject_hash, "subject_hash")


class InjectedFailure(RuntimeError):
    """Sanitized signal emitted only by the repository test harness."""

    def __init__(
        self,
        failure_point: FailurePoint,
        *,
        occurrence: int,
        completion_unknown: bool,
    ) -> None:
        require_enum_member(failure_point, FailurePoint, "failure_point")
        if not isinstance(occurrence, int) or isinstance(occurrence, bool) or occurrence < 1:
            raise ContractValidationError("occurrence must be a positive integer")
        if not isinstance(completion_unknown, bool):
            raise ContractValidationError("completion_unknown must be boolean")
        super().__init__(f"INJECTED_{failure_point.value}")
        self.failure_point = failure_point
        self.occurrence = occurrence
        self.completion_unknown = completion_unknown
        self.sanitized_code = f"INJECTED_{failure_point.value}"


@dataclass(frozen=True, slots=True)
class FailureDirective:
    """A bounded deterministic ``fail on exact occurrence`` test script."""

    failure_point: FailurePoint
    fail_on_occurrences: tuple[int, ...]
    completion_unknown: bool = False

    def __post_init__(self) -> None:
        require_enum_member(self.failure_point, FailurePoint, "failure_point")
        if (
            not isinstance(self.fail_on_occurrences, tuple)
            or not self.fail_on_occurrences
            or len(self.fail_on_occurrences) > MAXIMUM_FAILURE_ATTEMPTS
            or self.fail_on_occurrences != tuple(sorted(set(self.fail_on_occurrences)))
            or any(
                not isinstance(value, int)
                or isinstance(value, bool)
                or not 1 <= value <= MAXIMUM_FAILURE_ATTEMPTS
                for value in self.fail_on_occurrences
            )
        ):
            raise ContractValidationError(
                "failure occurrences must be sorted, unique, positive and bounded"
            )
        if not isinstance(self.completion_unknown, bool):
            raise ContractValidationError("completion_unknown must be boolean")


@dataclass(frozen=True, slots=True)
class FailureRecoveryCaseResult:
    """Immutable validation result for one named deterministic campaign."""

    schema_version: str
    case_id: str
    failure_domain: FailureDomain
    failure_point: FailurePoint
    subject_hash: str
    attempt_count: int
    recovery_status: RecoveryStatus
    final_semantic_state: str
    duplicate_side_effect_count: int
    authority_violation_count: int
    integrity_violation_count: int
    reason_codes: tuple[str, ...]
    result_hash: str

    def __post_init__(self) -> None:
        if self.schema_version != STEP37_SCHEMA_VERSION:
            raise ContractValidationError("unsupported Step 37 schema version")
        if not isinstance(self.case_id, str) or _CASE_ID.fullmatch(self.case_id) is None:
            raise ContractValidationError("case_id is not canonical")
        require_enum_member(self.failure_domain, FailureDomain, "failure_domain")
        require_enum_member(self.failure_point, FailurePoint, "failure_point")
        if FAILURE_POINT_DOMAINS[self.failure_point] is not self.failure_domain:
            raise ContractValidationError("failure point does not belong to failure domain")
        require_sha256_hex(self.subject_hash, "subject_hash")
        if (
            not isinstance(self.attempt_count, int)
            or isinstance(self.attempt_count, bool)
            or not 1 <= self.attempt_count <= MAXIMUM_FAILURE_ATTEMPTS
        ):
            raise ContractValidationError("attempt_count is outside the bounded policy")
        require_enum_member(self.recovery_status, RecoveryStatus, "recovery_status")
        require_non_empty(self.final_semantic_state, "final_semantic_state")
        for field_name in (
            "duplicate_side_effect_count",
            "authority_violation_count",
            "integrity_violation_count",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ContractValidationError(f"{field_name} must be a non-negative integer")
        if (
            not isinstance(self.reason_codes, tuple)
            or not self.reason_codes
            or len(self.reason_codes) > MAXIMUM_REASON_CODES
            or self.reason_codes != tuple(sorted(set(self.reason_codes)))
            or any(_REASON_CODE.fullmatch(code) is None for code in self.reason_codes)
        ):
            raise ContractValidationError("reason_codes must be closed, sorted and unique")
        require_sha256_hex(self.result_hash, "result_hash")
        expected = canonical_sha256(self, exclude_fields=("result_hash",))
        if self.result_hash != expected:
            raise IntegrityError("failure recovery result hash mismatch")

    @classmethod
    def build(
        cls,
        *,
        case_id: str,
        failure_domain: FailureDomain,
        failure_point: FailurePoint,
        subject_hash: str,
        attempt_count: int,
        recovery_status: RecoveryStatus,
        final_semantic_state: str,
        duplicate_side_effect_count: int = 0,
        authority_violation_count: int = 0,
        integrity_violation_count: int = 0,
        reason_codes: tuple[str, ...],
    ) -> "FailureRecoveryCaseResult":
        values = {
            "schema_version": STEP37_SCHEMA_VERSION,
            "case_id": case_id,
            "failure_domain": failure_domain,
            "failure_point": failure_point,
            "subject_hash": subject_hash,
            "attempt_count": attempt_count,
            "recovery_status": recovery_status,
            "final_semantic_state": final_semantic_state,
            "duplicate_side_effect_count": duplicate_side_effect_count,
            "authority_violation_count": authority_violation_count,
            "integrity_violation_count": integrity_violation_count,
            "reason_codes": tuple(sorted(set(reason_codes))),
        }
        return cls(**values, result_hash=canonical_sha256(values))


def verify_failure_recovery_case_result(value: FailureRecoveryCaseResult) -> None:
    if not isinstance(value, FailureRecoveryCaseResult):
        raise TypeError("value must be FailureRecoveryCaseResult")
    expected = canonical_sha256(value, exclude_fields=("result_hash",))
    if value.result_hash != expected:
        raise IntegrityError("failure recovery result hash mismatch")


__all__ = [
    "FAILURE_INJECTOR_VERSION",
    "FAILURE_POINT_DOMAINS",
    "FAILURE_POINT_REGISTRY_VERSION",
    "MAXIMUM_FAILURE_ATTEMPTS",
    "RECOVERY_POLICY_VERSION",
    "STEP37_SCHEMA_VERSION",
    "FailureDirective",
    "FailureDomain",
    "FailureInjector",
    "FailurePoint",
    "FailureRecoveryCaseResult",
    "InjectedFailure",
    "NoOpFailureInjector",
    "RecoveryStatus",
    "verify_failure_recovery_case_result",
]
