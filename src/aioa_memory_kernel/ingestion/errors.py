"""Sanitized failures and retry policy for the Step 10 ingestion saga."""

from __future__ import annotations

from dataclasses import dataclass

from aioa_memory_kernel.contracts.enums import StableStringEnum
from aioa_memory_kernel.persistence import (
    IdempotencyConflictError,
    PersistenceError,
    RetryExhaustedError,
)
from aioa_memory_kernel.sources import (
    PublicationEligibilityError,
    SourceRegistryError,
)
from aioa_memory_kernel.storage import (
    ExternalVolumeConflictError,
    ExternalVolumeIdentityError,
    ExternalVolumeIntegrityError,
    ExternalVolumeUnavailableError,
    ExternalVolumeUnsafePathError,
    SnapshotAccessDeniedError,
    SnapshotConflictError,
    SnapshotIntegrityError,
    SnapshotServiceUnavailableError,
    SnapshotSessionError,
    SnapshotStorageError,
)


class IngestionFailureClass(StableStringEnum):
    """Bounded recovery classes; no class grants publication authority."""

    DATABASE_SERIALIZATION = "DATABASE_SERIALIZATION"
    TRANSIENT_SERVICE = "TRANSIENT_SERVICE"
    CREDENTIALS_EXPIRED = "CREDENTIALS_EXPIRED"
    AUTHORIZATION_FAILURE = "AUTHORIZATION_FAILURE"
    CONTRACT_VIOLATION = "CONTRACT_VIOLATION"
    DATA_INTEGRITY_MISMATCH = "DATA_INTEGRITY_MISMATCH"
    EXTERNAL_VOLUME_UNAVAILABLE = "EXTERNAL_VOLUME_UNAVAILABLE"
    UNSAFE_FILESYSTEM_IDENTITY = "UNSAFE_FILESYSTEM_IDENTITY"
    S3_LOCK_VERIFICATION_FAILURE = "S3_LOCK_VERIFICATION_FAILURE"
    PARSE_FAILURE = "PARSE_FAILURE"
    VALIDATION_FAILURE = "VALIDATION_FAILURE"
    PUBLICATION_INELIGIBLE = "PUBLICATION_INELIGIBLE"
    CONFLICTING_REPLAY = "CONFLICTING_REPLAY"
    WORKER_CLAIM_CONFLICT = "WORKER_CLAIM_CONFLICT"
    OPERATOR_REVIEW_REQUIRED = "OPERATOR_REVIEW_REQUIRED"


@dataclass(frozen=True, slots=True)
class FailureDecision:
    """A deterministic execution response, separate from saga milestones."""

    failure_class: IngestionFailureClass
    retryable: bool
    quarantine_required: bool
    operator_review_required: bool
    sanitized_code: str


class IngestionError(RuntimeError):
    """Base failure exposing only bounded, non-secret diagnostics."""

    def __init__(
        self,
        message: str = "ingestion saga operation failed",
        *,
        sanitized_code: str = "INGESTION_FAILURE",
        failure_class: IngestionFailureClass = (
            IngestionFailureClass.CONTRACT_VIOLATION
        ),
    ) -> None:
        super().__init__(message)
        self.sanitized_code = sanitized_code
        self.failure_class = failure_class


class IngestionValidationError(IngestionError):
    """Typed request, state, receipt, or repository data is invalid."""


class IngestionTransitionError(IngestionError):
    """A canonical milestone transition was skipped, reversed, or stale."""


class IngestionConflictError(IngestionError):
    """An idempotency or deterministic identity bound conflicting facts."""


class IngestionClaimError(IngestionError):
    """A worker claim was active, stale, malformed, or not owned."""


class IngestionReceiptError(IngestionError):
    """A receipt was absent or bound to the wrong immutable input."""


class IngestionReconciliationError(IngestionError):
    """External and database evidence could not be reconciled safely."""


class IngestionExecutionError(IngestionError):
    """An external boundary failed after durable intent was recorded."""

    def __init__(
        self,
        message: str,
        *,
        decision: FailureDecision,
    ) -> None:
        super().__init__(
            message,
            sanitized_code=decision.sanitized_code,
            failure_class=decision.failure_class,
        )
        self.decision = decision


def _code(error: BaseException, default: str) -> str:
    value = getattr(error, "sanitized_code", None)
    if (
        isinstance(value, str)
        and value
        and len(value) <= 128
        and value.replace("_", "").replace(":", "").isalnum()
    ):
        return value
    return default


def classify_ingestion_failure(error: BaseException) -> FailureDecision:
    """Classify only structured exception types, never exception text."""

    if isinstance(error, IngestionExecutionError):
        return error.decision
    if isinstance(error, IngestionClaimError):
        return FailureDecision(
            IngestionFailureClass.WORKER_CLAIM_CONFLICT,
            retryable=True,
            quarantine_required=False,
            operator_review_required=False,
            sanitized_code=_code(error, "INGESTION_CLAIM_CONFLICT"),
        )
    if isinstance(error, (IdempotencyConflictError, IngestionConflictError)):
        return FailureDecision(
            IngestionFailureClass.CONFLICTING_REPLAY,
            retryable=False,
            quarantine_required=True,
            operator_review_required=True,
            sanitized_code=_code(error, "CONFLICTING_REPLAY"),
        )
    if isinstance(error, RetryExhaustedError) or (
        isinstance(error, PersistenceError)
        and getattr(error, "sqlstate", None) == "40001"
    ):
        return FailureDecision(
            IngestionFailureClass.DATABASE_SERIALIZATION,
            retryable=True,
            quarantine_required=False,
            operator_review_required=False,
            sanitized_code=_code(error, "DATABASE_SERIALIZATION_RETRY"),
        )
    if isinstance(error, SnapshotSessionError):
        return FailureDecision(
            IngestionFailureClass.CREDENTIALS_EXPIRED,
            retryable=False,
            quarantine_required=False,
            operator_review_required=True,
            sanitized_code=_code(error, "AWS_SESSION_INVALID"),
        )
    if isinstance(error, SnapshotAccessDeniedError):
        return FailureDecision(
            IngestionFailureClass.AUTHORIZATION_FAILURE,
            retryable=False,
            quarantine_required=False,
            operator_review_required=True,
            sanitized_code=_code(error, "S3_ACCESS_DENIED"),
        )
    if isinstance(error, SnapshotServiceUnavailableError):
        return FailureDecision(
            IngestionFailureClass.TRANSIENT_SERVICE,
            retryable=True,
            quarantine_required=False,
            operator_review_required=False,
            sanitized_code=_code(error, "S3_UNAVAILABLE"),
        )
    if isinstance(error, (SnapshotIntegrityError, SnapshotConflictError)):
        code = _code(error, "S3_INTEGRITY_MISMATCH")
        failure_class = (
            IngestionFailureClass.S3_LOCK_VERIFICATION_FAILURE
            if "RETENTION" in code or "LOCK" in code
            else IngestionFailureClass.DATA_INTEGRITY_MISMATCH
        )
        return FailureDecision(
            failure_class,
            retryable=False,
            quarantine_required=True,
            operator_review_required=True,
            sanitized_code=code,
        )
    if isinstance(
        error,
        (
            ExternalVolumeIdentityError,
            ExternalVolumeUnsafePathError,
        ),
    ):
        return FailureDecision(
            IngestionFailureClass.UNSAFE_FILESYSTEM_IDENTITY,
            retryable=False,
            quarantine_required=True,
            operator_review_required=True,
            sanitized_code=_code(error, "UNSAFE_FILESYSTEM_IDENTITY"),
        )
    if isinstance(error, ExternalVolumeIntegrityError):
        return FailureDecision(
            IngestionFailureClass.DATA_INTEGRITY_MISMATCH,
            retryable=False,
            quarantine_required=True,
            operator_review_required=True,
            sanitized_code=_code(error, "LOCAL_INTEGRITY_MISMATCH"),
        )
    if isinstance(error, ExternalVolumeConflictError):
        return FailureDecision(
            IngestionFailureClass.CONFLICTING_REPLAY,
            retryable=False,
            quarantine_required=True,
            operator_review_required=True,
            sanitized_code=_code(error, "LOCAL_ARTIFACT_CONFLICT"),
        )
    if isinstance(error, ExternalVolumeUnavailableError):
        return FailureDecision(
            IngestionFailureClass.EXTERNAL_VOLUME_UNAVAILABLE,
            retryable=True,
            quarantine_required=False,
            operator_review_required=False,
            sanitized_code=_code(error, "EXTERNAL_VOLUME_UNAVAILABLE"),
        )
    if isinstance(error, IngestionReceiptError):
        code = _code(error, "INGESTION_RECEIPT_INVALID")
        failure_class = (
            IngestionFailureClass.PARSE_FAILURE
            if code.startswith("PARSE_")
            else (
                IngestionFailureClass.VALIDATION_FAILURE
                if code.startswith("VALIDATION_")
                else IngestionFailureClass.DATA_INTEGRITY_MISMATCH
            )
        )
        quarantine = any(
            marker in code
            for marker in (
                "BINDING",
                "INTEGRITY",
                "MALFORMED",
                "MISMATCH",
                "REJECTED",
            )
        )
        return FailureDecision(
            failure_class,
            retryable=False,
            quarantine_required=quarantine,
            operator_review_required=True,
            sanitized_code=code,
        )
    if isinstance(error, PublicationEligibilityError):
        return FailureDecision(
            IngestionFailureClass.PUBLICATION_INELIGIBLE,
            retryable=False,
            quarantine_required=False,
            operator_review_required=True,
            sanitized_code=_code(error, "PUBLICATION_INELIGIBLE"),
        )
    if isinstance(error, (SnapshotStorageError, SourceRegistryError)):
        return FailureDecision(
            IngestionFailureClass.CONTRACT_VIOLATION,
            retryable=False,
            quarantine_required=False,
            operator_review_required=True,
            sanitized_code=_code(error, "BOUNDARY_CONTRACT_FAILURE"),
        )
    if isinstance(error, PersistenceError):
        return FailureDecision(
            IngestionFailureClass.OPERATOR_REVIEW_REQUIRED,
            retryable=False,
            quarantine_required=False,
            operator_review_required=True,
            sanitized_code=_code(error, "DATABASE_OPERATION_FAILURE"),
        )
    return FailureDecision(
        IngestionFailureClass.OPERATOR_REVIEW_REQUIRED,
        retryable=False,
        quarantine_required=False,
        operator_review_required=True,
        sanitized_code="UNCLASSIFIED_INGESTION_FAILURE",
    )
