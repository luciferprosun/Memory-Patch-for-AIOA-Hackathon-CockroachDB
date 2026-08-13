"""CockroachDB-backed opaque owner sessions for the hosted demo runtime.

Only SHA-256 digests of browser handles are persisted.  The adapter uses the
normal application pool, parameterized SQL, bounded serializable retries, and
the fixed-capacity slots enforced by migration 0019.  It never owns or closes
the shared application pool.
"""

from __future__ import annotations

import math
import secrets
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, TypeVar

from aioa_memory_kernel.persistence import (
    RetryPolicy,
    extract_sqlstate,
    is_retryable_sqlstate,
)
from aioa_memory_kernel.persistence.protocols import ConnectionFactory

from .auth import (
    OwnerSession,
    PendingOidcAuthorization,
    _safe_return_path,
    _token_hash,
)
from .models import OwnerPrincipal


T = TypeVar("T")
BEGIN_SERIALIZABLE_SQL = "BEGIN TRANSACTION ISOLATION LEVEL SERIALIZABLE"
MAXIMUM_SCHEMA_CAPACITY = 256
MAXIMUM_SCHEMA_PENDING = 128
MAXIMUM_SCHEMA_OWNER_SESSIONS = 8


class DurableSessionError(RuntimeError):
    """Sanitized durable-session failure with no driver or secret text."""


@dataclass(frozen=True, slots=True)
class DurableSessionLimits:
    absolute_ttl_seconds: int = 8 * 60 * 60
    pending_ttl_seconds: int = 10 * 60
    maximum_total_records: int = 64
    maximum_pending_flows: int = 16
    maximum_sessions_per_owner: int = 4
    maximum_payload_bytes: int = 2048

    def __post_init__(self) -> None:
        if (
            not 900 <= self.absolute_ttl_seconds <= 8 * 60 * 60
            or not 60 <= self.pending_ttl_seconds <= 10 * 60
            or not 1 <= self.maximum_total_records <= MAXIMUM_SCHEMA_CAPACITY
            or not 1 <= self.maximum_pending_flows <= min(
                self.maximum_total_records, MAXIMUM_SCHEMA_PENDING
            )
            or not 1 <= self.maximum_sessions_per_owner <= min(
                self.maximum_total_records, MAXIMUM_SCHEMA_OWNER_SESSIONS
            )
            or not 512 <= self.maximum_payload_bytes <= 4096
        ):
            raise ValueError("durable session limits are outside the safe bound")


@dataclass(frozen=True, slots=True)
class PendingSessionRecord:
    handle_hash: str
    state: str
    nonce: str
    code_verifier: str
    return_path: str
    created_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class AuthenticatedSessionRecord:
    handle_hash: str
    principal: OwnerPrincipal
    csrf_token: str
    created_at: datetime
    expires_at: datetime


class DurableSessionRepository(Protocol):
    def insert_pending(
        self, record: PendingSessionRecord, limits: DurableSessionLimits
    ) -> bool: ...

    def consume_pending(
        self, handle_hash: str, now: datetime
    ) -> PendingSessionRecord | None: ...

    def insert_authenticated(
        self, record: AuthenticatedSessionRecord, limits: DurableSessionLimits
    ) -> bool: ...

    def get_authenticated(
        self, handle_hash: str, now: datetime
    ) -> AuthenticatedSessionRecord | None: ...

    def delete(self, handle_hash: str) -> None: ...


def _utc_timestamp(value: float) -> datetime:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(float(value))
        or value < 0
    ):
        raise ValueError("session time is invalid")
    try:
        return datetime.fromtimestamp(float(value), tz=UTC)
    except (OverflowError, OSError, ValueError):
        raise ValueError("session time is invalid") from None


def _mapping(row: object) -> Mapping[str, object] | None:
    if row is None:
        return None
    if not isinstance(row, Mapping):
        raise DurableSessionError("durable session storage failed safely")
    return row


def _required_string(
    row: Mapping[str, object], name: str, *, maximum_bytes: int
) -> str:
    value = row.get(name)
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > maximum_bytes
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise DurableSessionError("durable session storage failed safely")
    return value


def _required_datetime(row: Mapping[str, object], name: str) -> datetime:
    value = row.get(name)
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise DurableSessionError("durable session storage failed safely")
    return value.astimezone(UTC)


class CockroachOwnerSessionRepository:
    """Narrow SQL repository over the R3 normal application connection."""

    def __init__(
        self,
        connection_factory: ConnectionFactory,
        *,
        retry_policy: RetryPolicy | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not callable(connection_factory):
            raise TypeError("connection_factory must be callable")
        self._connection_factory = connection_factory
        self._retry = retry_policy or RetryPolicy()
        self._sleep = sleep

    def _run(self, callback: Callable[[object], T]) -> T:
        for attempt in range(1, self._retry.max_attempts + 1):
            connection = None
            cursor = None
            began = False
            try:
                connection = self._connection_factory()
                cursor = connection.cursor()
                cursor.execute(BEGIN_SERIALIZABLE_SQL)
                began = True
                result = callback(cursor)
                connection.commit()
            except BaseException as error:
                cleanup_ok = True
                if connection is not None:
                    try:
                        connection.rollback()
                    except BaseException:
                        cleanup_ok = False
                if cursor is not None:
                    try:
                        cursor.close()
                    except BaseException:
                        cleanup_ok = False
                if connection is not None:
                    try:
                        connection.close()
                    except BaseException:
                        cleanup_ok = False
                if not cleanup_ok:
                    raise DurableSessionError(
                        "durable session storage failed safely"
                    ) from None
                if not isinstance(error, Exception):
                    raise
                sqlstate = extract_sqlstate(error)
                if began and is_retryable_sqlstate(sqlstate):
                    if attempt < self._retry.max_attempts:
                        self._sleep(self._retry.backoff_seconds(attempt))
                        continue
                if isinstance(error, DurableSessionError):
                    raise
                raise DurableSessionError(
                    "durable session storage failed safely"
                ) from None
            else:
                cleanup_ok = True
                if cursor is not None:
                    try:
                        cursor.close()
                    except BaseException:
                        cleanup_ok = False
                if connection is not None:
                    try:
                        connection.close()
                    except BaseException:
                        cleanup_ok = False
                if not cleanup_ok:
                    raise DurableSessionError(
                        "durable session storage failed safely"
                    ) from None
                return result
        raise DurableSessionError("durable session storage failed safely")

    @staticmethod
    def _purge(cursor: object, now: datetime) -> None:
        cursor.execute(  # type: ignore[attr-defined]
            "DELETE FROM memory_patch.owner_ui_sessions WHERE expires_at <= %s",
            (now,),
        )

    @staticmethod
    def _slot_order(limit: int) -> tuple[int, ...]:
        start = secrets.randbelow(limit)
        return tuple((start + offset) % limit for offset in range(limit))

    def insert_pending(
        self, record: PendingSessionRecord, limits: DurableSessionLimits
    ) -> bool:
        def operation(cursor: object) -> bool:
            self._purge(cursor, record.created_at)
            for pending_slot in self._slot_order(limits.maximum_pending_flows):
                for capacity_slot in self._slot_order(limits.maximum_total_records):
                    cursor.execute(  # type: ignore[attr-defined]
                        """
                        INSERT INTO memory_patch.owner_ui_sessions (
                          session_handle_hash, record_kind, capacity_slot,
                          pending_slot, oidc_state, oidc_nonce, pkce_verifier,
                          return_path, created_at, expires_at
                        ) VALUES (%s, 'OIDC_PENDING', %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT DO NOTHING
                        RETURNING session_handle_hash
                        """,
                        (
                            record.handle_hash,
                            capacity_slot,
                            pending_slot,
                            record.state,
                            record.nonce,
                            record.code_verifier,
                            record.return_path,
                            record.created_at,
                            record.expires_at,
                        ),
                    )
                    if cursor.fetchone() is not None:  # type: ignore[attr-defined]
                        return True
            return False

        return self._run(operation)

    def consume_pending(
        self, handle_hash: str, now: datetime
    ) -> PendingSessionRecord | None:
        def operation(cursor: object) -> PendingSessionRecord | None:
            self._purge(cursor, now)
            cursor.execute(  # type: ignore[attr-defined]
                """
                DELETE FROM memory_patch.owner_ui_sessions
                 WHERE session_handle_hash = %s
                   AND record_kind = 'OIDC_PENDING'
                   AND expires_at > %s
                RETURNING session_handle_hash, oidc_state, oidc_nonce,
                          pkce_verifier, return_path, created_at, expires_at
                """,
                (handle_hash, now),
            )
            row = _mapping(cursor.fetchone())  # type: ignore[attr-defined]
            if row is None:
                return None
            return PendingSessionRecord(
                handle_hash=_required_string(
                    row, "session_handle_hash", maximum_bytes=64
                ),
                state=_required_string(row, "oidc_state", maximum_bytes=128),
                nonce=_required_string(row, "oidc_nonce", maximum_bytes=128),
                code_verifier=_required_string(
                    row, "pkce_verifier", maximum_bytes=128
                ),
                return_path=_required_string(
                    row, "return_path", maximum_bytes=1024
                ),
                created_at=_required_datetime(row, "created_at"),
                expires_at=_required_datetime(row, "expires_at"),
            )

        return self._run(operation)

    def insert_authenticated(
        self, record: AuthenticatedSessionRecord, limits: DurableSessionLimits
    ) -> bool:
        principal = record.principal

        def operation(cursor: object) -> bool:
            self._purge(cursor, record.created_at)
            for owner_slot in self._slot_order(limits.maximum_sessions_per_owner):
                for capacity_slot in self._slot_order(limits.maximum_total_records):
                    cursor.execute(  # type: ignore[attr-defined]
                        """
                        INSERT INTO memory_patch.owner_ui_sessions (
                          session_handle_hash, record_kind, capacity_slot,
                          owner_slot, tenant_id, owner_user_id, oidc_subject,
                          display_name, csrf_token, created_at, expires_at
                        ) VALUES (
                          %s, 'AUTHENTICATED', %s, %s, %s, %s, %s, %s, %s, %s, %s
                        )
                        ON CONFLICT DO NOTHING
                        RETURNING session_handle_hash
                        """,
                        (
                            record.handle_hash,
                            capacity_slot,
                            owner_slot,
                            principal.tenant_id,
                            principal.owner_user_id,
                            principal.oidc_subject,
                            principal.display_name,
                            record.csrf_token,
                            record.created_at,
                            record.expires_at,
                        ),
                    )
                    if cursor.fetchone() is not None:  # type: ignore[attr-defined]
                        return True
            return False

        return self._run(operation)

    def get_authenticated(
        self, handle_hash: str, now: datetime
    ) -> AuthenticatedSessionRecord | None:
        def operation(cursor: object) -> AuthenticatedSessionRecord | None:
            self._purge(cursor, now)
            cursor.execute(  # type: ignore[attr-defined]
                """
                SELECT session_handle_hash, tenant_id, owner_user_id,
                       oidc_subject, display_name, csrf_token,
                       created_at, expires_at
                  FROM memory_patch.owner_ui_sessions
                 WHERE session_handle_hash = %s
                   AND record_kind = 'AUTHENTICATED'
                   AND expires_at > %s
                 LIMIT 1
                """,
                (handle_hash, now),
            )
            row = _mapping(cursor.fetchone())  # type: ignore[attr-defined]
            if row is None:
                return None
            principal = OwnerPrincipal(
                tenant_id=_required_string(row, "tenant_id", maximum_bytes=255),
                owner_user_id=_required_string(
                    row, "owner_user_id", maximum_bytes=255
                ),
                oidc_subject=_required_string(
                    row, "oidc_subject", maximum_bytes=255
                ),
                display_name=_required_string(
                    row, "display_name", maximum_bytes=255
                ),
            )
            return AuthenticatedSessionRecord(
                handle_hash=_required_string(
                    row, "session_handle_hash", maximum_bytes=64
                ),
                principal=principal,
                csrf_token=_required_string(
                    row, "csrf_token", maximum_bytes=128
                ),
                created_at=_required_datetime(row, "created_at"),
                expires_at=_required_datetime(row, "expires_at"),
            )

        return self._run(operation)

    def delete(self, handle_hash: str) -> None:
        def operation(cursor: object) -> None:
            cursor.execute(  # type: ignore[attr-defined]
                "DELETE FROM memory_patch.owner_ui_sessions "
                "WHERE session_handle_hash = %s",
                (handle_hash,),
            )

        self._run(operation)

    def __repr__(self) -> str:
        return "CockroachOwnerSessionRepository(connection='<redacted>')"


class CockroachOwnerSessionStore:
    """Production-capable implementation of the Step35 session port."""

    def __init__(
        self,
        *,
        limits: DurableSessionLimits,
        repository: DurableSessionRepository | None = None,
        connection_factory: ConnectionFactory | None = None,
    ) -> None:
        if not isinstance(limits, DurableSessionLimits):
            raise TypeError("limits must be DurableSessionLimits")
        if (repository is None) == (connection_factory is None):
            raise TypeError("provide exactly one durable session repository boundary")
        self._limits = limits
        self._repository = repository or CockroachOwnerSessionRepository(
            connection_factory  # type: ignore[arg-type]
        )
        self._closed = False
        self._lock = threading.Lock()

    def _require_open(self) -> None:
        with self._lock:
            if self._closed:
                raise DurableSessionError("durable session store is closed")

    @property
    def ready(self) -> bool:
        """Cheap lifecycle signal; it performs no database query."""

        with self._lock:
            return not self._closed

    def _payload_is_bounded(self, values: tuple[str, ...]) -> bool:
        return sum(len(value.encode("utf-8")) for value in values) <= (
            self._limits.maximum_payload_bytes
        )

    def create_pending(
        self, *, return_path: str, now: float
    ) -> tuple[str, PendingOidcAuthorization]:
        self._require_open()
        created_at = _utc_timestamp(now)
        return_path = _safe_return_path(return_path)
        for _ in range(3):
            handle = secrets.token_urlsafe(32)
            handle_hash = _token_hash(handle)
            if handle_hash is None:  # pragma: no cover - generated ASCII token
                continue
            pending = PendingOidcAuthorization(
                state=secrets.token_urlsafe(32),
                nonce=secrets.token_urlsafe(32),
                code_verifier=secrets.token_urlsafe(64),
                return_path=return_path,
                expires_at=now + self._limits.pending_ttl_seconds,
            )
            if not self._payload_is_bounded(
                (
                    pending.state,
                    pending.nonce,
                    pending.code_verifier,
                    pending.return_path,
                )
            ):
                raise DurableSessionError("durable session payload exceeds its bound")
            record = PendingSessionRecord(
                handle_hash=handle_hash,
                state=pending.state,
                nonce=pending.nonce,
                code_verifier=pending.code_verifier,
                return_path=pending.return_path,
                created_at=created_at,
                expires_at=_utc_timestamp(pending.expires_at),
            )
            if self._repository.insert_pending(record, self._limits):
                return handle, pending
        raise DurableSessionError("OIDC flow capacity is unavailable")

    def consume_pending(
        self, handle: str, *, now: float
    ) -> PendingOidcAuthorization | None:
        self._require_open()
        handle_hash = _token_hash(handle)
        if handle_hash is None:
            return None
        record = self._repository.consume_pending(handle_hash, _utc_timestamp(now))
        if record is None:
            return None
        return PendingOidcAuthorization(
            state=record.state,
            nonce=record.nonce,
            code_verifier=record.code_verifier,
            return_path=record.return_path,
            expires_at=record.expires_at.timestamp(),
        )

    def create_session(
        self, principal: OwnerPrincipal, *, now: float
    ) -> tuple[str, OwnerSession]:
        self._require_open()
        if not isinstance(principal, OwnerPrincipal):
            raise TypeError("principal must be OwnerPrincipal")
        created_at = _utc_timestamp(now)
        for _ in range(3):
            handle = secrets.token_urlsafe(48)
            handle_hash = _token_hash(handle)
            if handle_hash is None:  # pragma: no cover - generated ASCII token
                continue
            session = OwnerSession(
                principal=principal,
                csrf_token=secrets.token_urlsafe(32),
                created_at=now,
                expires_at=now + self._limits.absolute_ttl_seconds,
            )
            if not self._payload_is_bounded(
                (
                    principal.tenant_id,
                    principal.owner_user_id,
                    principal.oidc_subject,
                    principal.display_name,
                    session.csrf_token,
                )
            ):
                raise DurableSessionError("durable session payload exceeds its bound")
            record = AuthenticatedSessionRecord(
                handle_hash=handle_hash,
                principal=principal,
                csrf_token=session.csrf_token,
                created_at=created_at,
                expires_at=_utc_timestamp(session.expires_at),
            )
            if self._repository.insert_authenticated(record, self._limits):
                return handle, session
        raise DurableSessionError("owner session capacity is unavailable")

    def get_session(self, handle: str, *, now: float) -> OwnerSession | None:
        self._require_open()
        handle_hash = _token_hash(handle)
        if handle_hash is None:
            return None
        record = self._repository.get_authenticated(handle_hash, _utc_timestamp(now))
        if record is None:
            return None
        return OwnerSession(
            principal=record.principal,
            csrf_token=record.csrf_token,
            created_at=record.created_at.timestamp(),
            expires_at=record.expires_at.timestamp(),
        )

    def delete_session(self, handle: str) -> None:
        self._require_open()
        handle_hash = _token_hash(handle)
        if handle_hash is not None:
            self._repository.delete(handle_hash)

    def close(self) -> None:
        with self._lock:
            self._closed = True

    def __repr__(self) -> str:
        return (
            "CockroachOwnerSessionStore(repository='<redacted>', "
            f"maximum_total_records={self._limits.maximum_total_records})"
        )


__all__ = [
    "AuthenticatedSessionRecord",
    "CockroachOwnerSessionRepository",
    "CockroachOwnerSessionStore",
    "DurableSessionError",
    "DurableSessionLimits",
    "DurableSessionRepository",
    "PendingSessionRecord",
]
