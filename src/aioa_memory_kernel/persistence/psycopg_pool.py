"""Bounded Psycopg pool adapter for the existing persistence protocols.

The adapter is deliberately transport-only.  It owns no tenant identity,
business authority, migration capability, or fallback credential.  A leased
connection's ``close()`` returns that exact connection to the bounded pool so
the existing ``SerializableTransactionRunner`` can retain its DB-API contract.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Callable

from aioa_memory_kernel.security.credentials import (
    CredentialPurpose,
    SecretValue,
)

from .errors import PersistenceConfigurationError
from .protocols import ConnectionFactory, ConnectionProtocol, CursorProtocol
from .transaction import SerializableTransactionRunner


STEP40_APPLICATION_POOL_MAX = 4
STEP40_APPLICATION_POOL_MIN = 1
STEP40_POOL_WORKERS = 1


def _configuration_error(code: str) -> PersistenceConfigurationError:
    return PersistenceConfigurationError(
        "CockroachDB application pool configuration is invalid",
        sanitized_code=code,
    )


@dataclass(frozen=True, slots=True, repr=False)
class PsycopgPoolConfiguration:
    """Purpose-bound, bounded inputs for the application pgwire pool."""

    credential: SecretValue
    minimum_size: int = STEP40_APPLICATION_POOL_MIN
    maximum_size: int = STEP40_APPLICATION_POOL_MAX
    acquisition_timeout_seconds: int = 5
    connection_timeout_seconds: int = 5
    statement_timeout_seconds: int = 10
    maximum_waiting: int = STEP40_APPLICATION_POOL_MAX

    def __post_init__(self) -> None:
        if (
            not isinstance(self.credential, SecretValue)
            or self.credential.purpose is not CredentialPurpose.APPLICATION_DATABASE
        ):
            raise _configuration_error("APPLICATION_DATABASE_CREDENTIAL_REQUIRED")
        if (
            not isinstance(self.minimum_size, int)
            or isinstance(self.minimum_size, bool)
            or self.minimum_size != STEP40_APPLICATION_POOL_MIN
            or not isinstance(self.maximum_size, int)
            or isinstance(self.maximum_size, bool)
            or self.maximum_size != STEP40_APPLICATION_POOL_MAX
            or self.minimum_size > self.maximum_size
        ):
            raise _configuration_error("STEP40_APPLICATION_POOL_BOUND_REQUIRED")
        for value, code, upper in (
            (
                self.acquisition_timeout_seconds,
                "INVALID_POOL_ACQUISITION_TIMEOUT",
                15,
            ),
            (self.connection_timeout_seconds, "INVALID_DB_CONNECTION_TIMEOUT", 15),
            (self.statement_timeout_seconds, "INVALID_DB_STATEMENT_TIMEOUT", 60),
            (self.maximum_waiting, "INVALID_POOL_WAITING_BOUND", 16),
        ):
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or not 1 <= value <= upper
            ):
                raise _configuration_error(code)

    def __repr__(self) -> str:
        return (
            "PsycopgPoolConfiguration(credential='<redacted>', "
            f"minimum_size={self.minimum_size}, "
            f"maximum_size={self.maximum_size}, "
            f"acquisition_timeout_seconds={self.acquisition_timeout_seconds}, "
            f"connection_timeout_seconds={self.connection_timeout_seconds}, "
            f"statement_timeout_seconds={self.statement_timeout_seconds}, "
            f"maximum_waiting={self.maximum_waiting})"
        )


class _PooledConnectionLease(ConnectionProtocol):
    """Return one physical connection to its owner exactly once."""

    __slots__ = ("_connection", "_pool", "_released", "_release_lock")

    def __init__(self, pool: object, connection: object) -> None:
        self._pool = pool
        self._connection = connection
        self._released = False
        self._release_lock = threading.Lock()

    def _active_connection(self) -> object:
        if self._released:
            raise _configuration_error("STALE_POOLED_CONNECTION_LEASE")
        return self._connection

    def cursor(self) -> CursorProtocol:
        cursor = getattr(self._active_connection(), "cursor", None)
        if not callable(cursor):
            raise _configuration_error("PSYCOPG_CONNECTION_CONTRACT_INVALID")
        return cursor()

    def commit(self) -> None:
        commit = getattr(self._active_connection(), "commit", None)
        if not callable(commit):
            raise _configuration_error("PSYCOPG_CONNECTION_CONTRACT_INVALID")
        commit()

    def rollback(self) -> None:
        rollback = getattr(self._active_connection(), "rollback", None)
        if not callable(rollback):
            raise _configuration_error("PSYCOPG_CONNECTION_CONTRACT_INVALID")
        rollback()

    def close(self) -> None:
        with self._release_lock:
            if self._released:
                return
            putconn = getattr(self._pool, "putconn", None)
            if not callable(putconn):
                raise _configuration_error("PSYCOPG_POOL_CONTRACT_INVALID")
            putconn(self._connection)
            self._released = True

    def __repr__(self) -> str:
        return "PooledConnectionLease(connection='<redacted>')"


class PsycopgApplicationPool:
    """One-process Step40 application pool with lazy driver construction."""

    def __init__(
        self,
        configuration: PsycopgPoolConfiguration,
        *,
        pool_class: type | None = None,
        dict_row_factory: object | None = None,
    ) -> None:
        if not isinstance(configuration, PsycopgPoolConfiguration):
            raise _configuration_error("INVALID_PSYCOPG_POOL_CONFIGURATION")
        self._configuration = configuration
        self._pool_class = pool_class
        self._dict_row_factory = dict_row_factory
        self._pool: object | None = None
        self._closed = False
        self._state_lock = threading.Lock()

    @property
    def minimum_size(self) -> int:
        return self._configuration.minimum_size

    @property
    def maximum_size(self) -> int:
        return self._configuration.maximum_size

    @property
    def acquisition_timeout_seconds(self) -> int:
        return self._configuration.acquisition_timeout_seconds

    @property
    def credential_purpose(self) -> CredentialPurpose:
        return CredentialPurpose.APPLICATION_DATABASE

    def _dependencies(self) -> tuple[type, object]:
        pool_class = self._pool_class
        row_factory = self._dict_row_factory
        if pool_class is None or row_factory is None:
            try:
                from psycopg.rows import dict_row
                from psycopg_pool import ConnectionPool
            except ImportError:
                raise _configuration_error("PSYCOPG_RUNTIME_DEPENDENCY_MISSING") from None
            pool_class = pool_class or ConnectionPool
            row_factory = row_factory or dict_row
        return pool_class, row_factory

    def open(self) -> "PsycopgApplicationPool":
        with self._state_lock:
            if self._closed:
                raise _configuration_error("PSYCOPG_POOL_ALREADY_CLOSED")
            if self._pool is not None:
                return self
            pool_class, row_factory = self._dependencies()
            raw_dsn = self._configuration.credential.reveal_for(
                CredentialPurpose.APPLICATION_DATABASE
            )
            try:
                pool = pool_class(
                    conninfo=raw_dsn,
                    min_size=self._configuration.minimum_size,
                    max_size=self._configuration.maximum_size,
                    open=False,
                    timeout=float(self._configuration.acquisition_timeout_seconds),
                    max_waiting=self._configuration.maximum_waiting,
                    reconnect_timeout=float(
                        self._configuration.connection_timeout_seconds
                    ),
                    num_workers=STEP40_POOL_WORKERS,
                    name="memory-patch-application",
                    kwargs={
                        # The repository transaction boundary issues its own
                        # explicit serializable BEGIN.  Psycopg's implicit
                        # transaction mode would otherwise send a BEGIN before
                        # that statement and CockroachDB correctly rejects the
                        # nested transaction.
                        "autocommit": True,
                        "connect_timeout": self._configuration.connection_timeout_seconds,
                        "options": (
                            "-c statement_timeout="
                            f"{self._configuration.statement_timeout_seconds * 1000}"
                        ),
                        "prepare_threshold": None,
                        "row_factory": row_factory,
                    },
                )
                pool.open(
                    wait=True,
                    timeout=float(self._configuration.connection_timeout_seconds),
                )
            except Exception:
                close = getattr(locals().get("pool"), "close", None)
                if callable(close):
                    try:
                        close()
                    except Exception:
                        pass
                raise _configuration_error("APPLICATION_DATABASE_POOL_OPEN_FAILED") from None
            self._pool = pool
        return self

    def connection_factory(self) -> ConnectionFactory:
        def acquire() -> ConnectionProtocol:
            pool = self._pool
            if pool is None or self._closed:
                raise _configuration_error("APPLICATION_DATABASE_POOL_UNAVAILABLE")
            getconn = getattr(pool, "getconn", None)
            if not callable(getconn):
                raise _configuration_error("PSYCOPG_POOL_CONTRACT_INVALID")
            try:
                connection = getconn(
                    timeout=float(self._configuration.acquisition_timeout_seconds)
                )
            except Exception:
                raise _configuration_error("APPLICATION_DATABASE_POOL_ACQUIRE_FAILED") from None
            return _PooledConnectionLease(pool, connection)

        return acquire

    def transaction_runner(self) -> SerializableTransactionRunner:
        return SerializableTransactionRunner(
            self.connection_factory(),
            credential_purpose=CredentialPurpose.APPLICATION_DATABASE,
        )

    def with_connection(self, callback: Callable[[object], Any]) -> Any:
        """Run one bounded startup probe and always release its lease."""

        if not callable(callback):
            raise _configuration_error("INVALID_DATABASE_PROBE")
        lease = self.connection_factory()()
        try:
            return callback(lease)
        finally:
            lease.close()

    def close(self) -> None:
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
            pool = self._pool
            self._pool = None
        if pool is not None:
            close = getattr(pool, "close", None)
            if not callable(close):
                raise _configuration_error("PSYCOPG_POOL_CONTRACT_INVALID")
            try:
                close()
            except Exception:
                raise _configuration_error("APPLICATION_DATABASE_POOL_CLOSE_FAILED") from None

    def __repr__(self) -> str:
        return (
            "PsycopgApplicationPool(credential='<redacted>', "
            f"minimum_size={self.minimum_size}, maximum_size={self.maximum_size})"
        )


__all__ = [
    "PsycopgApplicationPool",
    "PsycopgPoolConfiguration",
    "STEP40_APPLICATION_POOL_MAX",
    "STEP40_APPLICATION_POOL_MIN",
    "STEP40_POOL_WORKERS",
]
