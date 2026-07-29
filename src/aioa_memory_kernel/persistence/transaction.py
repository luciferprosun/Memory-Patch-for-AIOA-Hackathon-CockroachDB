"""Short serializable transaction runner with trusted Step 5 context."""

from __future__ import annotations

import contextvars
import time
from collections.abc import Callable
from typing import TypeVar

from .errors import (
    PersistenceError,
    PersistenceTransactionError,
    RetryExhaustedError,
    TransactionBoundaryViolation,
)
from .models import RequestContext
from .protocols import (
    ConnectionFactory,
    CursorProtocol,
    Parameters,
    Row,
)
from .retry import RetryPolicy, extract_sqlstate, is_retryable_sqlstate


T = TypeVar("T")
_transaction_depth: contextvars.ContextVar[int] = contextvars.ContextVar(
    "memory_patch_persistence_transaction_depth",
    default=0,
)

BEGIN_SERIALIZABLE_SQL = "BEGIN TRANSACTION ISOLATION LEVEL SERIALIZABLE"
SET_CONTEXT_SQL = (
    "SELECT memory_patch.set_request_context(%s, %s, %s)"
)


def assert_no_open_persistence_transaction() -> None:
    """Fail before model, HTTP, AWS, S3, agent, or other external work."""

    if _transaction_depth.get() != 0:
        raise TransactionBoundaryViolation(
            "external work is forbidden inside a persistence transaction",
            sanitized_code="OPEN_PERSISTENCE_TRANSACTION",
        )


class Transaction:
    """A bounded cursor facade invalidated before commit or rollback returns."""

    __slots__ = ("__active", "__cursor")

    def __init__(self, cursor: CursorProtocol) -> None:
        self.__cursor = cursor
        self.__active = True

    @property
    def active(self) -> bool:
        return self.__active

    def _require_active(self) -> None:
        if not self.__active:
            raise TransactionBoundaryViolation(
                "transaction handle is no longer active",
                sanitized_code="STALE_TRANSACTION_HANDLE",
            )

    def execute(self, sql: str, parameters: Parameters = None) -> None:
        self._require_active()
        self.__cursor.execute(sql, parameters)

    def fetch_one(self, sql: str, parameters: Parameters = None) -> Row | None:
        self._require_active()
        self.__cursor.execute(sql, parameters)
        row = self.__cursor.fetchone()
        if row is not None and not isinstance(row, dict) and not hasattr(
            row, "keys"
        ):
            raise PersistenceTransactionError(
                "connection factory must return mapping rows",
                sanitized_code="NON_MAPPING_ROW",
            )
        return row

    def fetch_all(
        self,
        sql: str,
        parameters: Parameters = None,
    ) -> tuple[Row, ...]:
        self._require_active()
        self.__cursor.execute(sql, parameters)
        rows = tuple(self.__cursor.fetchall())
        if any(
            not isinstance(row, dict) and not hasattr(row, "keys")
            for row in rows
        ):
            raise PersistenceTransactionError(
                "connection factory must return mapping rows",
                sanitized_code="NON_MAPPING_ROW",
            )
        return rows

    def _invalidate(self) -> None:
        self.__active = False


class SerializableTransactionRunner:
    """Retry the complete callback only for transaction-scoped SQLSTATE 40001."""

    def __init__(
        self,
        connection_factory: ConnectionFactory,
        *,
        retry_policy: RetryPolicy | None = None,
        sleep: Callable[[float], None] = time.sleep,
        backoff: Callable[[int], float] | None = None,
    ) -> None:
        if not callable(connection_factory):
            raise PersistenceTransactionError(
                "connection factory must be callable",
                sanitized_code="INVALID_CONNECTION_FACTORY",
            )
        self._connection_factory = connection_factory
        self._policy = retry_policy or RetryPolicy()
        self._sleep = sleep
        self._backoff = backoff or self._policy.backoff_seconds

    def run(
        self,
        context: RequestContext,
        callback: Callable[[Transaction], T],
        *,
        operation_kind: str | None = None,
    ) -> T:
        if _transaction_depth.get() != 0:
            raise TransactionBoundaryViolation(
                "nested persistence transactions are unsupported",
                operation_kind=operation_kind,
                sanitized_code="NESTED_TRANSACTION",
            )
        for attempt in range(1, self._policy.max_attempts + 1):
            connection = None
            cursor = None
            transaction = None
            marker = None
            began = False
            try:
                connection = self._connection_factory()
                cursor = connection.cursor()
                cursor.execute(BEGIN_SERIALIZABLE_SQL)
                began = True
                marker = _transaction_depth.set(1)
                cursor.execute(
                    SET_CONTEXT_SQL,
                    (
                        context.tenant_id,
                        context.user_id,
                        context.access_mode.value,
                    ),
                )
                transaction = Transaction(cursor)
                result = callback(transaction)
                transaction._invalidate()
                connection.commit()
            except BaseException as error:
                if transaction is not None:
                    transaction._invalidate()
                cleanup_ok = self._cleanup_failed_attempt(
                    connection,
                    cursor,
                    marker,
                )
                marker = None
                sqlstate = extract_sqlstate(error)
                if not cleanup_ok:
                    raise PersistenceTransactionError(
                        "failed transaction could not be safely cleaned up",
                        sqlstate=sqlstate,
                        attempt=attempt,
                        operation_kind=operation_kind,
                        sanitized_code="TRANSACTION_CLEANUP_FAILED",
                    ) from None
                if not isinstance(error, Exception):
                    raise
                if began and is_retryable_sqlstate(sqlstate):
                    if attempt >= self._policy.max_attempts:
                        raise RetryExhaustedError(
                            "serializable transaction retry limit exhausted",
                            sqlstate=sqlstate,
                            attempt=attempt,
                            operation_kind=operation_kind,
                            sanitized_code="RETRY_EXHAUSTED",
                        ) from None
                    delay = self._backoff(attempt)
                    if delay < 0 or delay > self._policy.max_backoff_seconds:
                        raise PersistenceTransactionError(
                            "retry backoff exceeded its safety bound",
                            sqlstate=sqlstate,
                            attempt=attempt,
                            operation_kind=operation_kind,
                            sanitized_code="UNSAFE_RETRY_BACKOFF",
                        ) from None
                    self._sleep(delay)
                    continue
                if isinstance(error, PersistenceError):
                    raise
                raise PersistenceTransactionError(
                    "serializable transaction failed",
                    sqlstate=sqlstate,
                    attempt=attempt,
                    operation_kind=operation_kind,
                    sanitized_code=(
                        f"SQLSTATE_{sqlstate}"
                        if sqlstate is not None
                        else "UNCLASSIFIED_DATABASE_FAILURE"
                    ),
                ) from None
            else:
                if marker is not None:
                    _transaction_depth.reset(marker)
                self._close_success(connection, cursor)
                return result
        raise AssertionError("bounded retry loop ended without a result")

    @staticmethod
    def _cleanup_failed_attempt(
        connection: object | None,
        cursor: CursorProtocol | None,
        marker: contextvars.Token[int] | None,
    ) -> bool:
        cleanup_ok = True
        if connection is not None:
            try:
                connection.rollback()  # type: ignore[attr-defined]
            except BaseException:
                cleanup_ok = False
        if marker is not None:
            _transaction_depth.reset(marker)
        if cursor is not None:
            try:
                cursor.close()
            except BaseException:
                cleanup_ok = False
        if connection is not None:
            try:
                connection.close()  # type: ignore[attr-defined]
            except BaseException:
                cleanup_ok = False
        return cleanup_ok

    @staticmethod
    def _close_success(
        connection: object | None,
        cursor: CursorProtocol | None,
    ) -> None:
        cleanup_ok = True
        if cursor is not None:
            try:
                cursor.close()
            except BaseException:
                cleanup_ok = False
        if connection is not None:
            try:
                connection.close()  # type: ignore[attr-defined]
            except BaseException:
                cleanup_ok = False
        if not cleanup_ok:
            raise PersistenceTransactionError(
                "committed connection could not be safely released",
                sanitized_code="CONNECTION_RELEASE_FAILED",
            )
