"""Sanitized, typed failures for the persistence boundary."""

from __future__ import annotations


class PersistenceError(RuntimeError):
    """Base failure that exposes only bounded, non-secret metadata."""

    def __init__(
        self,
        message: str = "persistence operation failed",
        *,
        sqlstate: str | None = None,
        attempt: int | None = None,
        operation_kind: str | None = None,
        sanitized_code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.sqlstate = sqlstate
        self.attempt = attempt
        self.operation_kind = operation_kind
        self.sanitized_code = sanitized_code


class PersistenceConfigurationError(PersistenceError):
    """The connection factory or persistence configuration is unsafe."""


class PersistenceTransactionError(PersistenceError):
    """A transaction failed and was not automatically retried."""


class RetryableSerializationError(PersistenceTransactionError):
    """A typed SQLSTATE 40001 signal used by deterministic integrations."""

    def __init__(
        self,
        *,
        attempt: int | None = None,
        operation_kind: str | None = None,
    ) -> None:
        super().__init__(
            "serializable transaction requires a complete retry",
            sqlstate="40001",
            attempt=attempt,
            operation_kind=operation_kind,
            sanitized_code="SERIALIZATION_RETRY",
        )


class RetryExhaustedError(PersistenceTransactionError):
    """Ten complete serializable transaction attempts did not commit."""


class IdempotencyConflictError(PersistenceError):
    """An idempotency identity was reused with a different binding."""


class OperationStateConflictError(PersistenceError):
    """A compare-and-set lifecycle transition observed stale state."""


class ImmutableRecordConflictError(PersistenceError):
    """An immutable database identity was reused with different facts."""


class TransactionBoundaryViolation(PersistenceError):
    """Code crossed or retained the bounded persistence transaction boundary."""
