"""Bounded, owner-scoped projection store for the D3 jury flow.

The coordinator stores only short-lived presentation projections. Canonical
evidence, verification results, and Personal Memory remain owned by their
existing services. One bounded worker prevents an unbounded task queue and
all status reads are bound to the authenticated owner and opaque session
digest.
"""

from __future__ import annotations

import secrets
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from enum import Enum
from typing import Protocol

from aioa_memory_kernel.personal_memory_ui.models import OwnerPrincipal


MAXIMUM_JURY_RUNS = 20
JURY_RUN_TTL_SECONDS = 30 * 60
MAXIMUM_STAGE_TEXT_BYTES = 16 * 1024
MAXIMUM_EVIDENCE_ITEMS = 8
MAXIMUM_CORRECTION_ITEMS = 16


def _text(value: object, name: str, maximum: int = 512) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value.encode("utf-8")) > maximum
        or any(ord(character) < 32 and character not in "\n\t" for character in value)
        or any(ord(character) == 127 for character in value)
    ):
        raise ValueError(f"{name} is invalid")
    return value


def _optional_text(value: object, name: str, maximum: int = 512) -> str | None:
    if value is None:
        return None
    return _text(value, name, maximum)


def _digest(value: object, name: str) -> str:
    result = _text(value, name, 64)
    if len(result) != 64 or any(character not in "0123456789abcdef" for character in result):
        raise ValueError(f"{name} must be a SHA-256 digest")
    return result


class JuryExecutionKind(str, Enum):
    LIVE = "LIVE"
    DETERMINISTIC_TEST = "DETERMINISTIC_TEST"


class JuryRunState(str, Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"

    @property
    def terminal(self) -> bool:
        return self in {JuryRunState.COMPLETED, JuryRunState.BLOCKED}


class JuryStageState(str, Enum):
    NOT_RUN = "NOT RUN"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"
    NOT_APPLICABLE = "NOT APPLICABLE"


@dataclass(frozen=True, slots=True)
class GuidedJuryCase:
    case_id: str
    label: str
    question: str
    case_kind: str
    question_digest: str

    def __post_init__(self) -> None:
        _text(self.case_id, "case id", 128)
        _text(self.label, "case label", 256)
        _text(self.question, "case question", 4096)
        _text(self.case_kind, "case kind", 32)
        _digest(self.question_digest, "question digest")


@dataclass(frozen=True, slots=True)
class JuryStageProjection:
    stage_id: str
    label: str
    state: JuryStageState
    summary: str
    authority: str
    reference: str | None = None

    def __post_init__(self) -> None:
        _text(self.stage_id, "stage id", 64)
        _text(self.label, "stage label", 128)
        if not isinstance(self.state, JuryStageState):
            raise ValueError("stage state is invalid")
        _text(self.summary, "stage summary", MAXIMUM_STAGE_TEXT_BYTES)
        _text(self.authority, "stage authority", 512)
        if self.reference is not None:
            _digest(self.reference, "stage reference")


@dataclass(frozen=True, slots=True)
class JuryEvidenceProjection:
    source_id: str
    official_identifier: str
    provision: str
    authority: str
    excerpt: str
    source_reference: str
    temporal_status: str
    relation: str
    item_hash: str

    def __post_init__(self) -> None:
        for value, name, maximum in (
            (self.source_id, "source id", 255),
            (self.official_identifier, "official identifier", 255),
            (self.provision, "provision", 128),
            (self.authority, "evidence authority", 128),
            (self.excerpt, "evidence excerpt", 4096),
            (self.source_reference, "source reference", 2048),
            (self.temporal_status, "temporal status", 128),
            (self.relation, "evidence relation", 128),
        ):
            _text(value, name, maximum)
        _digest(self.item_hash, "evidence item hash")


@dataclass(frozen=True, slots=True)
class JuryCorrectionProjection:
    original_claim: str
    verdict: str
    required_correction: str
    citation: str
    correction_hash: str

    def __post_init__(self) -> None:
        _text(self.original_claim, "original claim", 4096)
        _text(self.verdict, "correction verdict", 128)
        _text(self.required_correction, "required correction", 4096)
        _text(self.citation, "correction citation", 2048)
        _digest(self.correction_hash, "correction hash")


@dataclass(frozen=True, slots=True)
class JuryProviderSummary:
    accounting_semantics: str
    calls_reserved: int
    calls_completed: int
    calls_failed: int
    calls_remaining: int

    def __post_init__(self) -> None:
        _text(self.accounting_semantics, "accounting semantics", 128)
        for value in (
            self.calls_reserved,
            self.calls_completed,
            self.calls_failed,
            self.calls_remaining,
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError("provider counter is invalid")


@dataclass(frozen=True, slots=True)
class JuryFlowRequest:
    run_id: str
    principal: OwnerPrincipal
    session_digest: str
    case: GuidedJuryCase

    def __post_init__(self) -> None:
        _text(self.run_id, "run id", 128)
        if not isinstance(self.principal, OwnerPrincipal):
            raise ValueError("principal is invalid")
        _digest(self.session_digest, "session digest")
        if not isinstance(self.case, GuidedJuryCase):
            raise ValueError("guided case is invalid")


@dataclass(frozen=True, slots=True)
class JuryFlowResult:
    execution_kind: JuryExecutionKind
    state: JuryRunState
    reason_code: str
    stages: tuple[JuryStageProjection, ...]
    evidence: tuple[JuryEvidenceProjection, ...] = ()
    corrections: tuple[JuryCorrectionProjection, ...] = ()
    verified_answer: str | None = None
    verified_answer_hash: str | None = None
    provider_summary: JuryProviderSummary | None = None
    personal_memory_eligible: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.execution_kind, JuryExecutionKind):
            raise ValueError("execution kind is invalid")
        if not isinstance(self.state, JuryRunState) or not self.state.terminal:
            raise ValueError("flow result must be terminal")
        _text(self.reason_code, "reason code", 128)
        if (
            not isinstance(self.stages, tuple)
            or not 1 <= len(self.stages) <= 14
            or any(not isinstance(value, JuryStageProjection) for value in self.stages)
            or not isinstance(self.evidence, tuple)
            or len(self.evidence) > MAXIMUM_EVIDENCE_ITEMS
            or any(not isinstance(value, JuryEvidenceProjection) for value in self.evidence)
            or not isinstance(self.corrections, tuple)
            or len(self.corrections) > MAXIMUM_CORRECTION_ITEMS
            or any(not isinstance(value, JuryCorrectionProjection) for value in self.corrections)
        ):
            raise ValueError("flow result collection is outside its bound")
        answer = _optional_text(
            self.verified_answer,
            "verified answer",
            MAXIMUM_STAGE_TEXT_BYTES,
        )
        object.__setattr__(self, "verified_answer", answer)
        if answer is None:
            if self.verified_answer_hash is not None:
                raise ValueError("verified answer hash cannot exist without an answer")
        else:
            _digest(self.verified_answer_hash, "verified answer hash")
            if self.state is not JuryRunState.COMPLETED:
                raise ValueError("blocked flow cannot expose a verified answer")
        if self.provider_summary is not None and not isinstance(
            self.provider_summary, JuryProviderSummary
        ):
            raise ValueError("provider summary is invalid")
        if not isinstance(self.personal_memory_eligible, bool):
            raise ValueError("Personal Memory eligibility must be boolean")


@dataclass(frozen=True, slots=True)
class JuryRunProjection:
    run_id: str
    case: GuidedJuryCase
    execution_kind: JuryExecutionKind
    state: JuryRunState
    reason_code: str
    stages: tuple[JuryStageProjection, ...]
    evidence: tuple[JuryEvidenceProjection, ...] = ()
    corrections: tuple[JuryCorrectionProjection, ...] = ()
    verified_answer: str | None = None
    verified_answer_hash: str | None = None
    provider_summary: JuryProviderSummary | None = None
    personal_memory_eligible: bool = False

    @property
    def terminal(self) -> bool:
        return self.state.terminal


class JuryFlowExecutor(Protocol):
    def execute(
        self,
        request: JuryFlowRequest,
        progress: Callable[[tuple[JuryStageProjection, ...]], None],
    ) -> JuryFlowResult: ...


@dataclass(slots=True)
class _OwnedRun:
    tenant_id: str
    owner_user_id: str
    session_digest: str
    idempotency_key: str
    projection: JuryRunProjection
    updated_at: float


class BoundedJuryRunCoordinator:
    """One-worker, TTL-bound current-mode orchestration boundary."""

    def __init__(
        self,
        executor: JuryFlowExecutor,
        cases: tuple[GuidedJuryCase, ...],
        *,
        maximum_runs: int = MAXIMUM_JURY_RUNS,
        ttl_seconds: int = JURY_RUN_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not callable(getattr(executor, "execute", None)):
            raise TypeError("executor must implement JuryFlowExecutor")
        if (
            not isinstance(cases, tuple)
            or not cases
            or len(cases) > 8
            or any(not isinstance(value, GuidedJuryCase) for value in cases)
            or len({value.case_id for value in cases}) != len(cases)
            or not isinstance(maximum_runs, int)
            or not 1 <= maximum_runs <= MAXIMUM_JURY_RUNS
            or not isinstance(ttl_seconds, int)
            or not 60 <= ttl_seconds <= JURY_RUN_TTL_SECONDS
            or not callable(clock)
        ):
            raise ValueError("jury coordinator configuration is invalid")
        self._executor = executor
        self._cases = cases
        self._case_by_id = {value.case_id: value for value in cases}
        self._maximum_runs = maximum_runs
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._runs: dict[str, _OwnedRun] = {}
        self._idempotency: dict[tuple[str, str, str, str], str] = {}
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="aioa-d3-jury")
        self._lock = threading.RLock()
        self._closed = False

    @property
    def ready(self) -> bool:
        with self._lock:
            return not self._closed

    @property
    def cases(self) -> tuple[GuidedJuryCase, ...]:
        return self._cases

    def _prune(self, now: float) -> None:
        expired = tuple(
            run_id
            for run_id, value in self._runs.items()
            if value.projection.terminal and now - value.updated_at >= self._ttl_seconds
        )
        for run_id in expired:
            value = self._runs.pop(run_id)
            key = (
                value.tenant_id,
                value.owner_user_id,
                value.session_digest,
                value.idempotency_key,
            )
            if self._idempotency.get(key) == run_id:
                self._idempotency.pop(key, None)

    @staticmethod
    def _authorize(value: _OwnedRun, principal: OwnerPrincipal, session_digest: str) -> None:
        if not (
            secrets.compare_digest(value.tenant_id, principal.tenant_id)
            and secrets.compare_digest(value.owner_user_id, principal.owner_user_id)
            and secrets.compare_digest(value.session_digest, session_digest)
        ):
            raise PermissionError("jury run is unavailable")

    def submit(
        self,
        principal: OwnerPrincipal,
        *,
        session_digest: str,
        case_id: str,
        idempotency_key: str,
    ) -> JuryRunProjection:
        if not isinstance(principal, OwnerPrincipal):
            raise ValueError("trusted principal is required")
        _digest(session_digest, "session digest")
        _text(case_id, "case id", 128)
        _text(idempotency_key, "idempotency key", 255)
        case = self._case_by_id.get(case_id)
        if case is None:
            raise ValueError("guided case is not approved")
        now = self._clock()
        key = (principal.tenant_id, principal.owner_user_id, session_digest, idempotency_key)
        with self._lock:
            if self._closed:
                raise RuntimeError("jury coordinator is unavailable")
            self._prune(now)
            existing_id = self._idempotency.get(key)
            if existing_id is not None:
                existing = self._runs[existing_id]
                self._authorize(existing, principal, session_digest)
                return existing.projection
            if len(self._runs) >= self._maximum_runs:
                raise RuntimeError("jury run capacity is exhausted")
            run_id = "d3-" + secrets.token_hex(16)
            projection = JuryRunProjection(
                run_id=run_id,
                case=case,
                execution_kind=JuryExecutionKind.LIVE,
                state=JuryRunState.QUEUED,
                reason_code="QUEUED",
                stages=(),
            )
            owned = _OwnedRun(
                tenant_id=principal.tenant_id,
                owner_user_id=principal.owner_user_id,
                session_digest=session_digest,
                idempotency_key=idempotency_key,
                projection=projection,
                updated_at=now,
            )
            self._runs[run_id] = owned
            self._idempotency[key] = run_id
            request = JuryFlowRequest(run_id, principal, session_digest, case)
            self._pool.submit(self._execute, request)
            return projection

    def _execute(self, request: JuryFlowRequest) -> None:
        with self._lock:
            owned = self._runs.get(request.run_id)
            if owned is None or self._closed:
                return
            owned.projection = replace(
                owned.projection,
                state=JuryRunState.RUNNING,
                reason_code="RUNNING",
            )
            owned.updated_at = self._clock()

        def progress(stages: tuple[JuryStageProjection, ...]) -> None:
            if (
                not isinstance(stages, tuple)
                or len(stages) > 14
                or any(not isinstance(value, JuryStageProjection) for value in stages)
            ):
                raise ValueError("jury progress is invalid")
            with self._lock:
                current = self._runs.get(request.run_id)
                if current is None or self._closed or current.projection.terminal:
                    return
                current.projection = replace(current.projection, stages=stages)
                current.updated_at = self._clock()

        try:
            result = self._executor.execute(request, progress)
            if not isinstance(result, JuryFlowResult):
                raise TypeError("jury executor returned an invalid result")
        except Exception:
            result = JuryFlowResult(
                execution_kind=JuryExecutionKind.LIVE,
                state=JuryRunState.BLOCKED,
                reason_code="INTERNAL_RUNTIME_FAILURE",
                stages=(
                    JuryStageProjection(
                        "runtime-failure",
                        "Runtime",
                        JuryStageState.BLOCKED,
                        "The run failed safely. No unverified answer was emitted.",
                        "Fail-closed application boundary",
                    ),
                ),
            )
        with self._lock:
            current = self._runs.get(request.run_id)
            if current is None:
                return
            current.projection = JuryRunProjection(
                run_id=request.run_id,
                case=request.case,
                execution_kind=result.execution_kind,
                state=result.state,
                reason_code=result.reason_code,
                stages=result.stages,
                evidence=result.evidence,
                corrections=result.corrections,
                verified_answer=result.verified_answer,
                verified_answer_hash=result.verified_answer_hash,
                provider_summary=result.provider_summary,
                personal_memory_eligible=result.personal_memory_eligible,
            )
            current.updated_at = self._clock()

    def get(
        self,
        principal: OwnerPrincipal,
        *,
        session_digest: str,
        run_id: str,
    ) -> JuryRunProjection:
        if not isinstance(principal, OwnerPrincipal):
            raise ValueError("trusted principal is required")
        _digest(session_digest, "session digest")
        _text(run_id, "run id", 128)
        with self._lock:
            self._prune(self._clock())
            value = self._runs.get(run_id)
            if value is None:
                raise KeyError("jury run is unavailable")
            self._authorize(value, principal, session_digest)
            return value.projection

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._pool.shutdown(wait=True, cancel_futures=True)
        with self._lock:
            self._runs.clear()
            self._idempotency.clear()


__all__ = [
    "BoundedJuryRunCoordinator",
    "GuidedJuryCase",
    "JuryCorrectionProjection",
    "JuryEvidenceProjection",
    "JuryExecutionKind",
    "JuryFlowExecutor",
    "JuryFlowRequest",
    "JuryFlowResult",
    "JuryProviderSummary",
    "JuryRunProjection",
    "JuryRunState",
    "JuryStageProjection",
    "JuryStageState",
]
