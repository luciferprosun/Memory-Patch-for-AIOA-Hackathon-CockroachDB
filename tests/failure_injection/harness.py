"""Deterministic fault scripts and bounded campaign utilities for Step 37."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from aioa_memory_kernel.contracts.serialization import canonical_sha256
from aioa_memory_kernel.reliability import (
    FailureDirective,
    FailurePoint,
    InjectedFailure,
)


T = TypeVar("T")
MAXIMUM_CAMPAIGN_ATTEMPTS = 10
MAXIMUM_PROCESS_RESTARTS = 2
PROCESS_TIMEOUT_SECONDS = 10


class ScriptedFailureInjector:
    """Repository-test-only exact-occurrence injector with no callbacks."""

    __slots__ = ("_directives", "_hits", "_emissions")

    def __init__(self, directives: Iterable[FailureDirective]) -> None:
        values = tuple(directives)
        if not values:
            raise ValueError("at least one failure directive is required")
        by_point = {value.failure_point: value for value in values}
        if len(by_point) != len(values):
            raise ValueError("failure points must be unique per script")
        self._directives = by_point
        self._hits: dict[FailurePoint, int] = defaultdict(int)
        self._emissions: dict[FailurePoint, int] = defaultdict(int)

    @property
    def test_only(self) -> bool:
        return True

    def hit(self, failure_point: FailurePoint, *, subject_hash: str) -> None:
        if not isinstance(failure_point, FailurePoint):
            raise TypeError("failure_point must be typed")
        if not isinstance(subject_hash, str) or len(subject_hash) != 64:
            raise ValueError("subject_hash must be a SHA-256 identity")
        self._hits[failure_point] += 1
        directive = self._directives.get(failure_point)
        occurrence = self._hits[failure_point]
        if directive is not None and occurrence in directive.fail_on_occurrences:
            self._emissions[failure_point] += 1
            raise InjectedFailure(
                failure_point,
                occurrence=occurrence,
                completion_unknown=directive.completion_unknown,
            )

    def hit_count(self, failure_point: FailurePoint) -> int:
        return self._hits[failure_point]

    def emitted_count(self, failure_point: FailurePoint) -> int:
        return self._emissions[failure_point]

    def assert_fully_exercised(self) -> None:
        for point, directive in self._directives.items():
            expected = len(directive.fail_on_occurrences)
            if self._emissions[point] != expected:
                raise AssertionError(
                    f"failure script was not exhausted for {point.value}"
                )


def retry_bounded(
    operation: Callable[[], T],
    *,
    retryable: Callable[[BaseException], bool],
    maximum_attempts: int,
) -> tuple[T, int]:
    """Retry without changing semantic identity or authority."""

    if not 1 <= maximum_attempts <= MAXIMUM_CAMPAIGN_ATTEMPTS:
        raise ValueError("maximum_attempts is outside Step 37 bounds")
    for attempt in range(1, maximum_attempts + 1):
        try:
            return operation(), attempt
        except Exception as error:
            if not retryable(error) or attempt == maximum_attempts:
                raise
    raise AssertionError("bounded retry ended without a result")


@dataclass(frozen=True, slots=True)
class DurableSemanticEffect:
    idempotency_identity: str
    request_hash: str
    result_hash: str


class DurableSemanticStore:
    """Tiny crash fixture: durable identity, exact replay, no fresh retry key."""

    def __init__(self, state_path: Path) -> None:
        self._state_path = state_path

    def apply(self, *, idempotency_identity: str, request_hash: str) -> DurableSemanticEffect:
        existing = self.load()
        if existing is not None:
            if (
                existing.idempotency_identity != idempotency_identity
                or existing.request_hash != request_hash
            ):
                raise ValueError("IDEMPOTENCY_REPLAY_CONFLICT")
            return existing
        result_hash = canonical_sha256(
            {
                "idempotency_identity": idempotency_identity,
                "request_hash": request_hash,
            }
        )
        payload = {
            "idempotency_identity": idempotency_identity,
            "request_hash": request_hash,
            "result_hash": result_hash,
        }
        temporary = self._state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        os.replace(temporary, self._state_path)
        return DurableSemanticEffect(**payload)

    def load(self) -> DurableSemanticEffect | None:
        if not self._state_path.exists():
            return None
        value = json.loads(self._state_path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or set(value) != {
            "idempotency_identity",
            "request_hash",
            "result_hash",
        }:
            raise ValueError("DURABLE_STATE_INVALID")
        effect = DurableSemanticEffect(**value)
        expected = canonical_sha256(
            {
                "idempotency_identity": effect.idempotency_identity,
                "request_hash": effect.request_hash,
            }
        )
        if effect.result_hash != expected:
            raise ValueError("DURABLE_STATE_HASH_MISMATCH")
        return effect


def run_crash_after_durable_write_case() -> tuple[DurableSemanticEffect, int]:
    """Kill only a disposable child after its atomic durable write."""

    with tempfile.TemporaryDirectory(prefix="mp-step37-crash-") as directory:
        state_path = Path(directory) / "effect.json"
        identity = "step37-process-crash-idempotency"
        request_hash = canonical_sha256({"case": "process-crash"})
        child = (
            "import os,sys; from pathlib import Path; "
            "from tests.failure_injection.harness import DurableSemanticStore; "
            "store=DurableSemanticStore(Path(sys.argv[1])); "
            "store.apply(idempotency_identity=sys.argv[2], request_hash=sys.argv[3]); "
            "os._exit(73)"
        )
        completed = subprocess.run(
            [sys.executable, "-c", child, str(state_path), identity, request_hash],
            cwd=Path(__file__).resolve().parents[2],
            env={
                "PATH": os.environ.get("PATH", ""),
                "PYTHONPATH": os.pathsep.join(
                    (
                        str(Path(__file__).resolve().parents[2]),
                        str(Path(__file__).resolve().parents[2] / "src"),
                    )
                ),
            },
            capture_output=True,
            text=True,
            timeout=PROCESS_TIMEOUT_SECONDS,
            check=False,
        )
        if completed.returncode != 73 or completed.stdout or completed.stderr:
            raise AssertionError("disposable crash fixture did not exit cleanly")
        store = DurableSemanticStore(state_path)
        before = store.load()
        replay = store.apply(
            idempotency_identity=identity,
            request_hash=request_hash,
        )
        if before != replay:
            raise AssertionError("restart replay changed durable semantic effect")
        return replay, 2


__all__ = [
    "DurableSemanticEffect",
    "DurableSemanticStore",
    "MAXIMUM_CAMPAIGN_ATTEMPTS",
    "MAXIMUM_PROCESS_RESTARTS",
    "PROCESS_TIMEOUT_SECONDS",
    "ScriptedFailureInjector",
    "retry_bounded",
    "run_crash_after_durable_write_case",
]
