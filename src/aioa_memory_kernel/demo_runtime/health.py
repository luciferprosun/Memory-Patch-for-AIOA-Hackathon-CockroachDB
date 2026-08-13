"""Minimal process liveness and fail-closed runtime readiness state.

The state is owned by one ASGI application instance.  It deliberately exposes
only bounded reason codes: dependency topology, credentials, DSNs and raw
exceptions never enter an HTTP health response.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from enum import Enum


class RuntimeReadinessPhase(str, Enum):
    STARTING = "STARTING"
    READY = "READY"
    NOT_READY = "NOT_READY"
    STOPPING = "STOPPING"
    FAILED = "FAILED"


class RuntimeReadinessReason(str, Enum):
    STARTUP_INCOMPLETE = "STARTUP_INCOMPLETE"
    READY = "READY"
    CONFIG_INVALID = "CONFIG_INVALID"
    DATABASE_UNAVAILABLE = "DATABASE_UNAVAILABLE"
    SESSION_STORE_UNAVAILABLE = "SESSION_STORE_UNAVAILABLE"
    AUTH_CONFIGURATION_INVALID = "AUTH_CONFIGURATION_INVALID"
    PROVIDER_CONFIGURATION_INVALID = "PROVIDER_CONFIGURATION_INVALID"
    PROVIDER_GUARD_UNAVAILABLE = "PROVIDER_GUARD_UNAVAILABLE"
    MANDATORY_SERVICE_UNAVAILABLE = "MANDATORY_SERVICE_UNAVAILABLE"
    DEPENDENCY_UNAVAILABLE = "DEPENDENCY_UNAVAILABLE"
    SHUTTING_DOWN = "SHUTTING_DOWN"
    STARTUP_FAILED = "STARTUP_FAILED"


@dataclass(frozen=True, slots=True)
class RuntimeReadinessSnapshot:
    phase: RuntimeReadinessPhase
    reason: RuntimeReadinessReason
    dependency_ids: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return (
            self.phase is RuntimeReadinessPhase.READY
            and self.reason is RuntimeReadinessReason.READY
        )


class RuntimeReadinessState:
    """Thread-safe lifecycle state for one application instance."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._phase = RuntimeReadinessPhase.STARTING
        self._reason = RuntimeReadinessReason.STARTUP_INCOMPLETE
        self._dependency_ids: tuple[str, ...] = ()

    def snapshot(self) -> RuntimeReadinessSnapshot:
        with self._lock:
            return RuntimeReadinessSnapshot(
                phase=self._phase,
                reason=self._reason,
                dependency_ids=self._dependency_ids,
            )

    def begin_startup(self) -> None:
        with self._lock:
            self._phase = RuntimeReadinessPhase.STARTING
            self._reason = RuntimeReadinessReason.STARTUP_INCOMPLETE
            self._dependency_ids = ()

    def mark_ready(self, dependency_ids: tuple[str, ...]) -> None:
        if (
            not isinstance(dependency_ids, tuple)
            or not dependency_ids
            or tuple(sorted(set(dependency_ids))) != dependency_ids
        ):
            raise TypeError("readiness dependency IDs must be sorted and unique")
        with self._lock:
            if self._phase is RuntimeReadinessPhase.STOPPING:
                raise RuntimeError("stopping runtime cannot become ready")
            self._phase = RuntimeReadinessPhase.READY
            self._reason = RuntimeReadinessReason.READY
            self._dependency_ids = dependency_ids

    def mark_not_ready(self, reason: RuntimeReadinessReason) -> None:
        if not isinstance(reason, RuntimeReadinessReason) or reason in {
            RuntimeReadinessReason.READY,
            RuntimeReadinessReason.SHUTTING_DOWN,
        }:
            raise TypeError("not-ready reason is invalid")
        with self._lock:
            if self._phase is not RuntimeReadinessPhase.STOPPING:
                self._phase = RuntimeReadinessPhase.NOT_READY
                self._reason = reason

    def mark_failed(self, reason: RuntimeReadinessReason) -> None:
        if not isinstance(reason, RuntimeReadinessReason) or reason in {
            RuntimeReadinessReason.READY,
            RuntimeReadinessReason.SHUTTING_DOWN,
        }:
            raise TypeError("failure reason is invalid")
        with self._lock:
            self._phase = RuntimeReadinessPhase.FAILED
            self._reason = reason

    def mark_stopping(self) -> None:
        with self._lock:
            self._phase = RuntimeReadinessPhase.STOPPING
            self._reason = RuntimeReadinessReason.SHUTTING_DOWN


__all__ = [
    "RuntimeReadinessPhase",
    "RuntimeReadinessReason",
    "RuntimeReadinessSnapshot",
    "RuntimeReadinessState",
]
