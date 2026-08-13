"""R5 server-side provider request, call, concurrency, and spend guardrails.

The hard spend ceiling is deliberately expressed as a conservative paid-call
ceiling because the pinned OpenRouter response contract exposes token counts,
not verified billed cost.  Reservations are written before transport to the
existing RLS/FORCE-RLS ``persistence_operations`` ledger.  A named budget
epoch therefore survives process restart and can be reset only by an explicit
operator configuration change.
"""

from __future__ import annotations

import contextvars
import hashlib
import hmac
import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Protocol

from aioa_memory_kernel.answers.retry import FINAL_RETRY_PROVIDER_PURPOSE
from aioa_memory_kernel.contracts.serialization import canonical_sha256
from aioa_memory_kernel.critic.parser import CRITIC_PROVIDER_PURPOSE
from aioa_memory_kernel.modeling.models import (
    MAXIMUM_DRAFT_UTF8_BYTES,
    ModelAdapterError,
    ModelReasonCode,
    ProviderCallRequest,
    ProviderIdentity,
    ProviderResponse,
    ProviderTextRequest,
    TimeoutPolicy,
    load_approved_provider_spec,
)
from aioa_memory_kernel.persistence import (
    AccessMode,
    RequestContext,
    SerializableTransactionRunner,
)
from aioa_memory_kernel.persistence.errors import PersistenceError
from aioa_memory_kernel.persistence.protocols import TransactionProtocol
from aioa_memory_kernel.verification.prompt import DRAFT_V2_PROVIDER_PURPOSE
from aioa_memory_kernel.verification.semantic import SEMANTIC_PROVIDER_PURPOSE

from .config import RuntimeProviderGuardSettings


GUARD_SCHEMA_VERSION = "provider-guard-r5-1a"
GUARD_ORIGIN_KIND = "DEMO_PROVIDER_GUARD"
REQUEST_GLOBAL_KIND = "DEMO_PROVIDER_REQUEST_GLOBAL"
REQUEST_OWNER_KIND = "DEMO_PROVIDER_REQUEST_OWNER"
CALL_GLOBAL_KIND = "DEMO_PROVIDER_CALL_GLOBAL"
CALL_OWNER_KIND = "DEMO_PROVIDER_CALL_OWNER"


class ProviderCallPurpose(str, Enum):
    DRAFT_V1 = "DRAFT_V1"
    DRAFT_V2 = "DRAFT_V2"
    FINAL_RETRY = "FINAL_RETRY"
    VERIFIER = "VERIFIER"
    CRITIC = "CRITIC"


@dataclass(frozen=True, slots=True)
class ProviderRequestScope:
    """Trusted server identity for one user-triggered model workflow."""

    tenant_id: str
    owner_user_id: str
    session_id: str
    request_id: str

    def __post_init__(self) -> None:
        for value, maximum in (
            (self.tenant_id, 255),
            (self.owner_user_id, 255),
            (self.session_id, 512),
            (self.request_id, 512),
        ):
            if (
                not isinstance(value, str)
                or not value
                or value != value.strip()
                or len(value.encode("utf-8")) > maximum
                or any(ord(character) < 32 or ord(character) == 127 for character in value)
            ):
                raise ModelAdapterError(ModelReasonCode.MODEL_REQUEST_INVALID)

    @property
    def session_digest(self) -> str:
        return _digest(self.tenant_id, self.owner_user_id, self.session_id)

    @property
    def request_digest(self) -> str:
        return _digest(
            self.tenant_id,
            self.owner_user_id,
            self.session_digest,
            self.request_id,
        )

    def request_context(self) -> RequestContext:
        return RequestContext(
            tenant_id=self.tenant_id,
            user_id=self.owner_user_id,
            access_mode=AccessMode.USER_PRIVATE,
        )


@dataclass(frozen=True, slots=True)
class GuardReservation:
    scope: ProviderRequestScope
    global_operation_id: str
    owner_operation_id: str
    reservation_kind: str


class ProviderGuardLedger(Protocol):
    durable: bool

    def reserve_request(self, scope: ProviderRequestScope) -> GuardReservation: ...

    def reserve_call(
        self,
        scope: ProviderRequestScope,
        *,
        provider_request_hash: str,
        purpose: ProviderCallPurpose,
        attempt_number: int,
    ) -> GuardReservation: ...

    def complete(
        self,
        reservation: GuardReservation,
        *,
        result_digest: str | None,
        usage_reference: str | None,
        error_code: ModelReasonCode | None,
        unknown_completion: bool,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class ProviderGuardAccountingSnapshot:
    accounting_semantics: str
    requests_reserved: int
    calls_reserved: int
    calls_completed: int
    calls_failed: int
    calls_unknown_completion: int
    owner_calls_reserved: int
    session_calls_reserved: int
    maximum_calls_total: int
    calls_remaining: int
    budget_denied_calls: int

    def __post_init__(self) -> None:
        values = (
            self.requests_reserved,
            self.calls_reserved,
            self.calls_completed,
            self.calls_failed,
            self.calls_unknown_completion,
            self.owner_calls_reserved,
            self.session_calls_reserved,
            self.maximum_calls_total,
            self.calls_remaining,
            self.budget_denied_calls,
        )
        if (
            self.accounting_semantics != "CALL-COUNT CEILING"
            or any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in values)
            or self.calls_reserved > self.maximum_calls_total
            or self.calls_remaining != self.maximum_calls_total - self.calls_reserved
            or self.calls_completed + self.calls_failed + self.calls_unknown_completion
            > self.calls_reserved
        ):
            raise TypeError("provider guard accounting snapshot is invalid")


class ProviderGuardLedgerError(PersistenceError):
    """Sanitized durable-ledger decision; never contains request material."""

    def __init__(self, reason_code: ModelReasonCode) -> None:
        if reason_code not in {
            ModelReasonCode.MODEL_REQUEST_LIMIT_EXHAUSTED,
            ModelReasonCode.MODEL_CALL_LIMIT_EXHAUSTED,
            ModelReasonCode.MODEL_BUDGET_EXHAUSTED,
            ModelReasonCode.MODEL_DUPLICATE_REQUEST,
            ModelReasonCode.MODEL_TRANSIENT_FAILURE,
        }:
            raise TypeError("unsupported provider guard reason code")
        super().__init__(
            "provider guard denied safely",
            operation_kind="DEMO_PROVIDER_GUARD",
            sanitized_code=reason_code.value,
        )
        self.reason_code = reason_code


def _digest(*values: str) -> str:
    document = "\x1f".join(values).encode("utf-8")
    return hashlib.sha256(document).hexdigest()


def _constant_time_text_equal(left: str, right: str | None) -> bool:
    if not isinstance(left, str) or not isinstance(right, str):
        return False
    return hmac.compare_digest(
        hashlib.sha256(left.encode("utf-8")).digest(),
        hashlib.sha256(right.encode("utf-8")).digest(),
    )


def _count(row: Mapping[str, object] | None, name: str) -> int:
    if row is None:
        raise ProviderGuardLedgerError(ModelReasonCode.MODEL_TRANSIENT_FAILURE)
    value = row.get(name)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ProviderGuardLedgerError(ModelReasonCode.MODEL_TRANSIENT_FAILURE)
    return value


def _provider_usage_reference(response: ProviderResponse) -> str:
    """Return bounded provider-reported token counts, never billing truth."""

    if not isinstance(response, ProviderResponse):
        raise ModelAdapterError(ModelReasonCode.MODEL_RESPONSE_INVALID)

    def token_value(name: str) -> str:
        value = response.usage_metadata.get(name)
        if value is None:
            return "unknown"
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ModelAdapterError(ModelReasonCode.MODEL_RESPONSE_INVALID)
        rendered = str(value)
        if len(rendered) > 20:
            raise ModelAdapterError(ModelReasonCode.MODEL_RESPONSE_INVALID)
        return rendered

    return (
        "provider-reported-tokens:"
        f"prompt={token_value('prompt_tokens')};"
        f"completion={token_value('completion_tokens')};"
        f"total={token_value('total_tokens')};"
        f"response_bytes={response.response_byte_length}"
    )


class CockroachProviderGuardLedger:
    """Durable call-count ceiling on the existing purpose-neutral operation ledger."""

    durable = True

    def __init__(
        self,
        transaction_runner: SerializableTransactionRunner,
        *,
        provider_id: str,
        budget_epoch: str,
        limits: RuntimeProviderGuardSettings,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(transaction_runner, SerializableTransactionRunner):
            raise TypeError("transaction_runner must be SerializableTransactionRunner")
        if not isinstance(limits, RuntimeProviderGuardSettings):
            raise TypeError("limits must be RuntimeProviderGuardSettings")
        if limits.budget_epoch != budget_epoch:
            raise TypeError("budget epoch must match guard settings")
        if provider_id != load_approved_provider_spec().provider_id:
            raise ModelAdapterError(ModelReasonCode.MODEL_IDENTITY_MISMATCH)
        self._runner = transaction_runner
        self._provider_id = provider_id
        self._budget_epoch = budget_epoch
        self._limits = limits
        self._clock = clock or (lambda: datetime.now(UTC))

    def reserve_request(self, scope: ProviderRequestScope) -> GuardReservation:
        return self._reserve(scope, category="REQUEST", call_identity=None)

    def reserve_call(
        self,
        scope: ProviderRequestScope,
        *,
        provider_request_hash: str,
        purpose: ProviderCallPurpose,
        attempt_number: int,
    ) -> GuardReservation:
        if (
            not isinstance(provider_request_hash, str)
            or len(provider_request_hash) != 64
            or any(character not in "0123456789abcdef" for character in provider_request_hash)
            or not isinstance(purpose, ProviderCallPurpose)
            or isinstance(attempt_number, bool)
            or not 1 <= attempt_number <= self._limits.maximum_calls_per_request
        ):
            raise ModelAdapterError(ModelReasonCode.MODEL_REQUEST_INVALID)
        call_identity = _digest(
            scope.request_digest,
            purpose.value,
            provider_request_hash,
            str(attempt_number),
        )
        return self._reserve(scope, category="CALL", call_identity=call_identity)

    def _reserve(
        self,
        scope: ProviderRequestScope,
        *,
        category: str,
        call_identity: str | None,
    ) -> GuardReservation:
        if not isinstance(scope, ProviderRequestScope):
            raise ModelAdapterError(ModelReasonCode.MODEL_REQUEST_INVALID)
        if not _constant_time_text_equal(scope.tenant_id, self._limits.tenant_id):
            raise ModelAdapterError(ModelReasonCode.MODEL_POLICY_REJECTED)
        is_call = category == "CALL"
        global_kind = CALL_GLOBAL_KIND if is_call else REQUEST_GLOBAL_KIND
        owner_kind = CALL_OWNER_KIND if is_call else REQUEST_OWNER_KIND
        identity = call_identity or scope.request_digest
        idempotency_key = _digest(self._budget_epoch, category, identity)
        global_operation_id = f"r5-{category.casefold()}-global-{idempotency_key}"
        owner_operation_id = f"r5-{category.casefold()}-owner-{idempotency_key}"

        def work(transaction: TransactionProtocol) -> GuardReservation:
            existing = transaction.fetch_one(
                """
                SELECT operation_id
                  FROM memory_patch.persistence_operations
                 WHERE tenant_id = %s
                   AND operation_kind IN (%s, %s)
                   AND idempotency_key = %s
                   AND origin_kind = %s
                   AND origin_version = %s
                 LIMIT 1
                """,
                (
                    scope.tenant_id,
                    global_kind,
                    owner_kind,
                    idempotency_key,
                    GUARD_ORIGIN_KIND,
                    self._budget_epoch,
                ),
            )
            if existing is not None:
                raise ProviderGuardLedgerError(
                    ModelReasonCode.MODEL_DUPLICATE_REQUEST
                )
            counts = transaction.fetch_one(
                """
                SELECT
                  count(*) FILTER (WHERE operation_kind = %s)::INT8 AS global_count,
                  count(*) FILTER (WHERE operation_kind = %s)::INT8 AS owner_count,
                  count(*) FILTER (
                    WHERE operation_kind = %s AND scope_digest = %s
                  )::INT8 AS session_count,
                  count(*) FILTER (
                    WHERE operation_kind = %s AND request_digest = %s
                  )::INT8 AS request_count
                  FROM memory_patch.persistence_operations
                 WHERE tenant_id = %s
                   AND origin_kind = %s
                   AND origin_system = %s
                   AND origin_version = %s
                   AND operation_kind IN (%s, %s)
                """,
                (
                    global_kind,
                    owner_kind,
                    owner_kind,
                    scope.session_digest,
                    owner_kind,
                    scope.request_digest,
                    scope.tenant_id,
                    GUARD_ORIGIN_KIND,
                    self._provider_id,
                    self._budget_epoch,
                    global_kind,
                    owner_kind,
                ),
            )
            global_count = _count(counts, "global_count")
            owner_count = _count(counts, "owner_count")
            session_count = _count(counts, "session_count")
            request_count = _count(counts, "request_count")
            if is_call:
                if global_count >= self._limits.maximum_calls_total:
                    raise ProviderGuardLedgerError(
                        ModelReasonCode.MODEL_BUDGET_EXHAUSTED
                    )
                if (
                    owner_count >= self._limits.maximum_calls_per_owner
                    or session_count >= self._limits.maximum_calls_per_session
                    or request_count >= self._limits.maximum_calls_per_request
                ):
                    raise ProviderGuardLedgerError(
                        ModelReasonCode.MODEL_CALL_LIMIT_EXHAUSTED
                    )
            elif (
                global_count >= self._limits.maximum_requests_total
                or owner_count >= self._limits.maximum_requests_per_owner
                or session_count >= self._limits.maximum_requests_per_session
            ):
                raise ProviderGuardLedgerError(
                    ModelReasonCode.MODEL_REQUEST_LIMIT_EXHAUSTED
                )

            now = self._clock().astimezone(UTC)
            for operation_id, operation_kind, owner_user_id, artifact_kind in (
                (global_operation_id, global_kind, None, f"{category}_GLOBAL"),
                (
                    owner_operation_id,
                    owner_kind,
                    scope.owner_user_id,
                    f"{category}_OWNER",
                ),
            ):
                inserted = transaction.fetch_one(
                    """
                    INSERT INTO memory_patch.persistence_operations (
                      tenant_id, operation_id, schema_version, owner_user_id,
                      operation_kind, idempotency_key, request_digest,
                      scope_digest, status, attempt_count, result_ref,
                      result_digest, last_sqlstate, sanitized_error_code,
                      created_at, updated_at, completed_at, origin_kind,
                      origin_system, origin_version, adapter_version,
                      artifact_kind, external_ref
                    ) VALUES (
                      %s, %s, '1.0.0', %s, %s, %s, %s, %s,
                      'IN_PROGRESS', 1, NULL, NULL, NULL, NULL, %s, %s,
                      NULL, %s, %s, %s, %s, %s, %s
                    )
                    ON CONFLICT DO NOTHING
                    RETURNING operation_id
                    """,
                    (
                        scope.tenant_id,
                        operation_id,
                        owner_user_id,
                        operation_kind,
                        idempotency_key,
                        scope.request_digest,
                        scope.session_digest,
                        now,
                        now,
                        GUARD_ORIGIN_KIND,
                        self._provider_id,
                        self._budget_epoch,
                        GUARD_SCHEMA_VERSION,
                        artifact_kind,
                        f"reservation:{idempotency_key}",
                    ),
                )
                if inserted is None:
                    raise ProviderGuardLedgerError(
                        ModelReasonCode.MODEL_DUPLICATE_REQUEST
                    )
            return GuardReservation(
                scope=scope,
                global_operation_id=global_operation_id,
                owner_operation_id=owner_operation_id,
                reservation_kind=category,
            )

        try:
            return self._runner.run(
                scope.request_context(),
                work,
                operation_kind=f"demo-provider-{category.casefold()}-reserve",
            )
        except ProviderGuardLedgerError:
            raise
        except Exception:
            raise ProviderGuardLedgerError(
                ModelReasonCode.MODEL_TRANSIENT_FAILURE
            ) from None

    def complete(
        self,
        reservation: GuardReservation,
        *,
        result_digest: str | None,
        usage_reference: str | None,
        error_code: ModelReasonCode | None,
        unknown_completion: bool,
    ) -> None:
        if not isinstance(reservation, GuardReservation) or not isinstance(
            unknown_completion, bool
        ):
            raise TypeError("invalid provider guard completion")
        if result_digest is not None and (
            len(result_digest) != 64
            or any(character not in "0123456789abcdef" for character in result_digest)
        ):
            raise TypeError("result digest must be SHA-256")
        if result_digest is not None and error_code is not None:
            raise TypeError("provider guard completion cannot be success and failure")
        if usage_reference is not None and (
            result_digest is None
            or not isinstance(usage_reference, str)
            or not usage_reference.startswith("provider-reported-tokens:")
            or len(usage_reference.encode("utf-8")) > 256
            or any(
                ord(character) < 32 or ord(character) == 127
                for character in usage_reference
            )
        ):
            raise TypeError("provider usage reference is invalid")
        if result_digest is None and error_code is None:
            error_code = ModelReasonCode.MODEL_TRANSIENT_FAILURE
            unknown_completion = True
        if result_digest is not None:
            status = "COMPLETED"
            sanitized_error = None
            completed_at: datetime | None = self._clock().astimezone(UTC)
        else:
            status = "INTERRUPTED" if unknown_completion else "FAILED_FINAL"
            sanitized_error = error_code.value if error_code is not None else None
            completed_at = None
        now = self._clock().astimezone(UTC)

        def work(transaction: TransactionProtocol) -> None:
            transaction.execute(
                """
                UPDATE memory_patch.persistence_operations
                   SET status = %s,
                       result_ref = %s,
                       result_digest = %s,
                       sanitized_error_code = %s,
                       updated_at = %s,
                       completed_at = %s
                 WHERE tenant_id = %s
                   AND operation_id IN (%s, %s)
                   AND status = 'IN_PROGRESS'
                """,
                (
                    status,
                    usage_reference,
                    result_digest,
                    sanitized_error,
                    now,
                    completed_at,
                    reservation.scope.tenant_id,
                    reservation.global_operation_id,
                    reservation.owner_operation_id,
                ),
            )

        try:
            self._runner.run(
                reservation.scope.request_context(),
                work,
                operation_kind="demo-provider-reservation-complete",
            )
        except Exception:
            # The durable IN_PROGRESS reservation still consumes the ceiling.
            # Callers must fail closed because reconciliation is unknown.
            raise ProviderGuardLedgerError(
                ModelReasonCode.MODEL_TRANSIENT_FAILURE
            ) from None

    def snapshot(
        self,
        scope: ProviderRequestScope,
        *,
        budget_denied_calls: int = 0,
    ) -> ProviderGuardAccountingSnapshot:
        """Read bounded accounting metadata without prompt or response content."""

        if (
            not isinstance(scope, ProviderRequestScope)
            or not isinstance(budget_denied_calls, int)
            or isinstance(budget_denied_calls, bool)
            or budget_denied_calls < 0
        ):
            raise TypeError("provider guard accounting scope is invalid")

        def work(transaction: TransactionProtocol) -> ProviderGuardAccountingSnapshot:
            row = transaction.fetch_one(
                """
                SELECT
                  count(*) FILTER (
                    WHERE operation_kind = %s
                  )::INT8 AS requests_reserved,
                  count(*) FILTER (
                    WHERE operation_kind = %s
                  )::INT8 AS calls_reserved,
                  count(*) FILTER (
                    WHERE operation_kind = %s AND status = 'COMPLETED'
                  )::INT8 AS calls_completed,
                  count(*) FILTER (
                    WHERE operation_kind = %s AND status = 'FAILED_FINAL'
                  )::INT8 AS calls_failed,
                  count(*) FILTER (
                    WHERE operation_kind = %s AND status = 'INTERRUPTED'
                  )::INT8 AS calls_unknown_completion,
                  count(*) FILTER (
                    WHERE operation_kind = %s AND owner_user_id = %s
                  )::INT8 AS owner_calls_reserved,
                  count(*) FILTER (
                    WHERE operation_kind = %s AND scope_digest = %s
                  )::INT8 AS session_calls_reserved
                  FROM memory_patch.persistence_operations
                 WHERE tenant_id = %s
                   AND origin_kind = %s
                   AND origin_system = %s
                   AND origin_version = %s
                   AND operation_kind IN (%s, %s, %s, %s)
                """,
                (
                    REQUEST_GLOBAL_KIND,
                    CALL_GLOBAL_KIND,
                    CALL_GLOBAL_KIND,
                    CALL_GLOBAL_KIND,
                    CALL_GLOBAL_KIND,
                    CALL_OWNER_KIND,
                    scope.owner_user_id,
                    CALL_OWNER_KIND,
                    scope.session_digest,
                    scope.tenant_id,
                    GUARD_ORIGIN_KIND,
                    self._provider_id,
                    self._budget_epoch,
                    REQUEST_GLOBAL_KIND,
                    REQUEST_OWNER_KIND,
                    CALL_GLOBAL_KIND,
                    CALL_OWNER_KIND,
                ),
            )
            calls_reserved = _count(row, "calls_reserved")
            return ProviderGuardAccountingSnapshot(
                accounting_semantics="CALL-COUNT CEILING",
                requests_reserved=_count(row, "requests_reserved"),
                calls_reserved=calls_reserved,
                calls_completed=_count(row, "calls_completed"),
                calls_failed=_count(row, "calls_failed"),
                calls_unknown_completion=_count(
                    row, "calls_unknown_completion"
                ),
                owner_calls_reserved=_count(row, "owner_calls_reserved"),
                session_calls_reserved=_count(row, "session_calls_reserved"),
                maximum_calls_total=self._limits.maximum_calls_total,
                calls_remaining=max(
                    0, self._limits.maximum_calls_total - calls_reserved
                ),
                budget_denied_calls=budget_denied_calls,
            )

        try:
            return self._runner.run(
                scope.request_context(),
                work,
                operation_kind="demo-provider-accounting-snapshot",
            )
        except Exception:
            raise ProviderGuardLedgerError(
                ModelReasonCode.MODEL_TRANSIENT_FAILURE
            ) from None


@dataclass(slots=True)
class _ActiveRequest:
    scope: ProviderRequestScope
    reservation: GuardReservation
    attempt_count: int = 0


class _WindowRateLimiter:
    def __init__(
        self,
        limits: RuntimeProviderGuardSettings,
        *,
        monotonic: Callable[[], float],
    ) -> None:
        self._limits = limits
        self._monotonic = monotonic
        self._global: deque[float] = deque()
        self._owners: dict[str, deque[float]] = defaultdict(deque)
        self._sessions: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    @staticmethod
    def _prune(events: deque[float], earliest: float) -> None:
        while events and events[0] <= earliest:
            events.popleft()

    def admit(self, scope: ProviderRequestScope) -> None:
        now = self._monotonic()
        earliest = now - self._limits.request_window_seconds
        owner_key = _digest(scope.tenant_id, scope.owner_user_id)
        session_key = scope.session_digest
        with self._lock:
            if owner_key not in self._owners:
                if len(self._owners) >= self._limits.maximum_requests_total:
                    raise ModelAdapterError(
                        ModelReasonCode.MODEL_REQUEST_LIMIT_EXHAUSTED
                    )
                self._owners[owner_key] = deque()
            if session_key not in self._sessions:
                if len(self._sessions) >= self._limits.maximum_requests_total:
                    raise ModelAdapterError(
                        ModelReasonCode.MODEL_REQUEST_LIMIT_EXHAUSTED
                    )
                self._sessions[session_key] = deque()
            owner_events = self._owners[owner_key]
            session_events = self._sessions[session_key]
            for events in (self._global, owner_events, session_events):
                self._prune(events, earliest)
            if (
                len(self._global)
                >= self._limits.maximum_requests_per_window_global
                or len(owner_events)
                >= self._limits.maximum_requests_per_window_owner
                or len(session_events)
                >= self._limits.maximum_requests_per_window_session
            ):
                raise ModelAdapterError(
                    ModelReasonCode.MODEL_REQUEST_LIMIT_EXHAUSTED
                )
            self._global.append(now)
            owner_events.append(now)
            session_events.append(now)


class GuardedProviderAdapter:
    """One exact provider adapter behind durable, bounded pre-call admission."""

    def __init__(
        self,
        provider: object,
        *,
        ledger: ProviderGuardLedger,
        limits: RuntimeProviderGuardSettings,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        identity = getattr(provider, "provider_identity", None)
        generate = getattr(provider, "generate", None)
        if not callable(identity) or not callable(generate):
            raise TypeError("provider must implement the text provider contract")
        expected = load_approved_provider_spec().provider_identity()
        if identity() != expected:
            raise ModelAdapterError(ModelReasonCode.MODEL_IDENTITY_MISMATCH)
        if not isinstance(limits, RuntimeProviderGuardSettings):
            raise TypeError("limits must be RuntimeProviderGuardSettings")
        if not all(
            callable(getattr(ledger, name, None))
            for name in ("reserve_request", "reserve_call", "complete")
        ):
            raise TypeError("ledger must implement ProviderGuardLedger")
        self._provider = provider
        self._ledger = ledger
        self._limits = limits
        self._rate = _WindowRateLimiter(limits, monotonic=monotonic)
        self._semaphore = threading.BoundedSemaphore(
            value=limits.maximum_concurrent_calls
        )
        self._queue_lock = threading.Lock()
        self._queued = 0
        self._budget_denied_calls = 0
        self._closed = False
        self._state_lock = threading.Lock()
        self._active = contextvars.ContextVar[_ActiveRequest | None](
            "memory_patch_provider_guard_request", default=None
        )

    @property
    def durable_accounting(self) -> bool:
        return bool(getattr(self._ledger, "durable", False))

    @property
    def ready(self) -> bool:
        """Cheap structural readiness; it never invokes the provider."""

        with self._state_lock:
            return not self._closed and self.durable_accounting

    def provider_identity(self) -> ProviderIdentity:
        identity = self._provider.provider_identity()
        if identity != load_approved_provider_spec().provider_identity():
            raise ModelAdapterError(ModelReasonCode.MODEL_IDENTITY_MISMATCH)
        return identity

    def __repr__(self) -> str:
        return (
            "GuardedProviderAdapter(provider='openrouter', "
            "model='moonshotai/kimi-k2', credential='<redacted>', "
            f"durable_accounting={self.durable_accounting})"
        )

    @contextmanager
    def request_scope(self, scope: ProviderRequestScope) -> Iterator[None]:
        if not isinstance(scope, ProviderRequestScope):
            raise ModelAdapterError(ModelReasonCode.MODEL_REQUEST_INVALID)
        if not _constant_time_text_equal(scope.tenant_id, self._limits.tenant_id):
            raise ModelAdapterError(ModelReasonCode.MODEL_POLICY_REJECTED)
        with self._state_lock:
            if self._closed:
                raise ModelAdapterError(ModelReasonCode.MODEL_ADAPTER_UNAVAILABLE)
        if self._active.get() is not None:
            raise ModelAdapterError(ModelReasonCode.MODEL_DUPLICATE_REQUEST)
        self._rate.admit(scope)
        try:
            reservation = self._ledger.reserve_request(scope)
        except ProviderGuardLedgerError as error:
            raise ModelAdapterError(error.reason_code) from None
        active = _ActiveRequest(scope=scope, reservation=reservation)
        token = self._active.set(active)
        error_code: ModelReasonCode | None = None
        try:
            yield
        except ModelAdapterError as error:
            error_code = error.reason_code
            raise
        except BaseException:
            error_code = ModelReasonCode.MODEL_TRANSIENT_FAILURE
            raise
        finally:
            self._active.reset(token)
            try:
                self._ledger.complete(
                    reservation,
                    result_digest=(
                        canonical_sha256(
                            {
                                "request_digest": scope.request_digest,
                                "attempt_count": active.attempt_count,
                                "status": "COMPLETED",
                            }
                        )
                        if error_code is None
                        else None
                    ),
                    usage_reference=None,
                    error_code=error_code,
                    unknown_completion=False,
                )
            except ProviderGuardLedgerError:
                # A request reservation remains durable and counted. If the
                # workflow already raised, preserve its sanitized failure.
                if error_code is None:
                    raise ModelAdapterError(
                        ModelReasonCode.MODEL_TRANSIENT_FAILURE,
                        unknown_completion=True,
                    ) from None

    def accounting_snapshot(
        self, scope: ProviderRequestScope
    ) -> ProviderGuardAccountingSnapshot:
        snapshot = getattr(self._ledger, "snapshot", None)
        if not callable(snapshot):
            raise ModelAdapterError(ModelReasonCode.MODEL_ADAPTER_UNAVAILABLE)
        with self._state_lock:
            denied = self._budget_denied_calls
        try:
            result = snapshot(scope, budget_denied_calls=denied)
        except ProviderGuardLedgerError as error:
            raise ModelAdapterError(error.reason_code) from None
        if not isinstance(result, ProviderGuardAccountingSnapshot):
            raise ModelAdapterError(ModelReasonCode.MODEL_RESPONSE_INVALID)
        return result

    def _purpose(self, request: ProviderCallRequest | ProviderTextRequest) -> ProviderCallPurpose:
        if isinstance(request, ProviderCallRequest):
            return ProviderCallPurpose.DRAFT_V1
        purpose = request.purpose
        mapping = {
            DRAFT_V2_PROVIDER_PURPOSE: ProviderCallPurpose.DRAFT_V2,
            FINAL_RETRY_PROVIDER_PURPOSE: ProviderCallPurpose.FINAL_RETRY,
            SEMANTIC_PROVIDER_PURPOSE: ProviderCallPurpose.VERIFIER,
            CRITIC_PROVIDER_PURPOSE: ProviderCallPurpose.CRITIC,
        }
        try:
            return mapping[purpose]
        except KeyError:
            raise ModelAdapterError(ModelReasonCode.MODEL_POLICY_REJECTED) from None

    def _validate_request(
        self,
        request: ProviderCallRequest | ProviderTextRequest,
        timeout_policy: TimeoutPolicy,
    ) -> ProviderCallPurpose:
        if not isinstance(request, (ProviderCallRequest, ProviderTextRequest)) or not isinstance(
            timeout_policy, TimeoutPolicy
        ):
            raise ModelAdapterError(ModelReasonCode.MODEL_REQUEST_INVALID)
        if request.provider_identity != self.provider_identity():
            raise ModelAdapterError(ModelReasonCode.MODEL_IDENTITY_MISMATCH)
        user_content = (
            request.original_query
            if isinstance(request, ProviderCallRequest)
            else request.user_content
        )
        total_input_bytes = len(
            (request.system_instruction + user_content).encode("utf-8")
        )
        if (
            total_input_bytes > self._limits.maximum_input_bytes
            or request.generation_parameters.max_output_tokens
            > self._limits.maximum_output_tokens
            or timeout_policy.attempt_timeout_seconds > self._limits.timeout_seconds
        ):
            raise ModelAdapterError(ModelReasonCode.MODEL_REQUEST_INVALID)
        return self._purpose(request)

    def _acquire_permit(self) -> None:
        if self._semaphore.acquire(blocking=False):
            return
        with self._queue_lock:
            if self._queued >= self._limits.maximum_queued_calls:
                raise ModelAdapterError(ModelReasonCode.MODEL_CONCURRENCY_LIMIT)
            self._queued += 1
        try:
            acquired = self._semaphore.acquire(
                timeout=float(self._limits.queue_wait_seconds)
            )
        finally:
            with self._queue_lock:
                self._queued -= 1
        if not acquired:
            raise ModelAdapterError(ModelReasonCode.MODEL_CONCURRENCY_LIMIT)

    def generate(
        self,
        request: ProviderCallRequest | ProviderTextRequest,
        timeout_policy: TimeoutPolicy,
    ) -> ProviderResponse:
        active = self._active.get()
        if active is None:
            raise ModelAdapterError(ModelReasonCode.MODEL_REQUEST_SCOPE_REQUIRED)
        purpose = self._validate_request(request, timeout_policy)
        self._acquire_permit()
        reservation: GuardReservation | None = None
        try:
            active.attempt_count += 1
            if active.attempt_count > self._limits.maximum_calls_per_request:
                raise ModelAdapterError(ModelReasonCode.MODEL_CALL_LIMIT_EXHAUSTED)
            try:
                reservation = self._ledger.reserve_call(
                    active.scope,
                    provider_request_hash=request.request_hash,
                    purpose=purpose,
                    attempt_number=active.attempt_count,
                )
            except ProviderGuardLedgerError as error:
                if error.reason_code is ModelReasonCode.MODEL_BUDGET_EXHAUSTED:
                    with self._state_lock:
                        self._budget_denied_calls += 1
                raise ModelAdapterError(error.reason_code) from None
            try:
                response = self._provider.generate(request, timeout_policy)
            except ModelAdapterError as error:
                try:
                    self._ledger.complete(
                        reservation,
                        result_digest=None,
                        usage_reference=None,
                        error_code=error.reason_code,
                        unknown_completion=error.unknown_completion,
                    )
                except ProviderGuardLedgerError:
                    pass
                raise
            except Exception:
                try:
                    self._ledger.complete(
                        reservation,
                        result_digest=None,
                        usage_reference=None,
                        error_code=ModelReasonCode.MODEL_TRANSIENT_FAILURE,
                        unknown_completion=True,
                    )
                except ProviderGuardLedgerError:
                    pass
                raise ModelAdapterError(
                    ModelReasonCode.MODEL_TRANSIENT_FAILURE,
                    unknown_completion=True,
                ) from None
            if (
                not isinstance(response, ProviderResponse)
                or response.provider_identity_digest
                != self.provider_identity().identity_digest
                or response.model_id != self.provider_identity().model_id
                or response.model_version
                != self.provider_identity().model_revision_or_declared_version
                or response.response_byte_length > MAXIMUM_DRAFT_UTF8_BYTES
                or (
                    response.usage_metadata.get("completion_tokens") is not None
                    and response.usage_metadata["completion_tokens"]
                    > request.generation_parameters.max_output_tokens
                )
            ):
                error = ModelAdapterError(ModelReasonCode.MODEL_RESPONSE_INVALID)
                try:
                    self._ledger.complete(
                        reservation,
                        result_digest=None,
                        usage_reference=None,
                        error_code=error.reason_code,
                        unknown_completion=False,
                    )
                except ProviderGuardLedgerError:
                    pass
                raise error
            try:
                self._ledger.complete(
                    reservation,
                    result_digest=canonical_sha256(
                        {
                            "provider_response_hash": response.response_hash,
                            "purpose": purpose.value,
                            "usage_metadata": response.usage_metadata,
                        }
                    ),
                    usage_reference=_provider_usage_reference(response),
                    error_code=None,
                    unknown_completion=False,
                )
            except ProviderGuardLedgerError:
                raise ModelAdapterError(
                    ModelReasonCode.MODEL_TRANSIENT_FAILURE,
                    unknown_completion=True,
                ) from None
            return response
        finally:
            self._semaphore.release()

    def close(self) -> None:
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
        close = getattr(self._provider, "close", None)
        if callable(close):
            close()


__all__ = [
    "CALL_GLOBAL_KIND",
    "CALL_OWNER_KIND",
    "GUARD_ORIGIN_KIND",
    "GUARD_SCHEMA_VERSION",
    "REQUEST_GLOBAL_KIND",
    "REQUEST_OWNER_KIND",
    "CockroachProviderGuardLedger",
    "GuardReservation",
    "GuardedProviderAdapter",
    "ProviderCallPurpose",
    "ProviderGuardAccountingSnapshot",
    "ProviderGuardLedger",
    "ProviderGuardLedgerError",
    "ProviderRequestScope",
]
