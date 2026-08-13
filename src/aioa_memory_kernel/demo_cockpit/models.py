"""Immutable, secret-free view models for the jury-facing cockpit."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .legacy_archive import LegacyArchiveManifest, LegacyCompatibilityMode


_MAXIMUM_VIEW_TEXT_BYTES = 1024


def _bounded_text(value: str, field_name: str, maximum_bytes: int = 256) -> None:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value.encode("utf-8")) > maximum_bytes
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError(f"{field_name} is invalid")


class CockpitMode(str, Enum):
    MEMORY_PATCH_CURRENT = "memory_patch"
    CRITICAL_PROMPT_LEGACY = "critical_prompt_loop"


class CockpitExecutionKind(str, Enum):
    LIVE = "LIVE"
    HISTORICAL_VIEW = "HISTORICAL_VIEW"


class CockpitRunState(str, Enum):
    IDLE = "IDLE"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class CockpitModeOption:
    mode: CockpitMode
    label: str
    enabled: bool
    selected: bool
    status_label: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.mode, CockpitMode)
            or not isinstance(self.enabled, bool)
            or not isinstance(self.selected, bool)
        ):
            raise ValueError("cockpit mode is invalid")
        _bounded_text(self.label, "mode label")
        _bounded_text(self.status_label, "mode status label")


@dataclass(frozen=True, slots=True)
class CockpitRuntimeStatus:
    profile_id: str
    asgi_application: str
    authentication: str
    session_backend: str
    database: str
    provider: str
    provider_guard: str
    readiness_contract: str

    def __post_init__(self) -> None:
        for field_name in (
            "profile_id",
            "asgi_application",
            "authentication",
            "session_backend",
            "database",
            "provider",
            "provider_guard",
            "readiness_contract",
        ):
            _bounded_text(getattr(self, field_name), field_name)


@dataclass(frozen=True, slots=True)
class CockpitStageSummary:
    stage_id: str
    label: str
    status: str
    detail: str
    authority: str

    def __post_init__(self) -> None:
        _bounded_text(self.stage_id, "stage id", 64)
        _bounded_text(self.label, "stage label")
        _bounded_text(self.status, "stage status", 64)
        _bounded_text(self.detail, "stage detail", _MAXIMUM_VIEW_TEXT_BYTES)
        _bounded_text(self.authority, "stage authority", 256)


@dataclass(frozen=True, slots=True)
class LegacyModeStatus:
    enabled: bool
    configured_mode: LegacyCompatibilityMode
    classification: str
    availability: str
    execution_kind: CockpitExecutionKind
    explanation: str

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise ValueError("legacy availability flag is invalid")
        if not isinstance(self.configured_mode, LegacyCompatibilityMode):
            raise ValueError("legacy configured mode is invalid")
        _bounded_text(self.classification, "legacy classification")
        _bounded_text(self.availability, "legacy availability")
        if not isinstance(self.execution_kind, CockpitExecutionKind):
            raise ValueError("legacy execution kind is invalid")
        _bounded_text(
            self.explanation,
            "legacy explanation",
            _MAXIMUM_VIEW_TEXT_BYTES,
        )


@dataclass(frozen=True, slots=True)
class CockpitView:
    selected_mode: CockpitMode
    execution_kind: CockpitExecutionKind
    run_state: CockpitRunState
    heading: str
    mode_badge: str
    introduction: str
    notice: str | None
    mode_options: tuple[CockpitModeOption, ...]
    runtime: CockpitRuntimeStatus
    stages: tuple[CockpitStageSummary, ...]
    observer_cards: tuple[CockpitStageSummary, ...]
    legacy: LegacyModeStatus
    legacy_archive: LegacyArchiveManifest | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.selected_mode, CockpitMode):
            raise ValueError("selected mode is invalid")
        if not isinstance(self.execution_kind, CockpitExecutionKind):
            raise ValueError("execution kind is invalid")
        if not isinstance(self.run_state, CockpitRunState):
            raise ValueError("run state is invalid")
        _bounded_text(self.heading, "heading")
        _bounded_text(self.mode_badge, "mode badge")
        _bounded_text(self.introduction, "introduction", _MAXIMUM_VIEW_TEXT_BYTES)
        if self.notice is not None:
            _bounded_text(self.notice, "notice", _MAXIMUM_VIEW_TEXT_BYTES)
        if (
            not isinstance(self.mode_options, tuple)
            or not isinstance(self.stages, tuple)
            or not isinstance(self.observer_cards, tuple)
            or len(self.mode_options) != 2
            or any(
                not isinstance(option, CockpitModeOption)
                for option in self.mode_options
            )
            or {option.mode for option in self.mode_options} != set(CockpitMode)
            or sum(option.selected for option in self.mode_options) != 1
            or not any(
                option.selected and option.mode is self.selected_mode
                for option in self.mode_options
            )
            or not 1 <= len(self.stages) <= 16
            or any(not isinstance(stage, CockpitStageSummary) for stage in self.stages)
            or len(self.observer_cards) != 3
            or any(
                not isinstance(observer, CockpitStageSummary)
                for observer in self.observer_cards
            )
            or not isinstance(self.legacy, LegacyModeStatus)
            or (
                self.legacy_archive is not None
                and not isinstance(self.legacy_archive, LegacyArchiveManifest)
            )
            or (
                self.selected_mode is CockpitMode.CRITICAL_PROMPT_LEGACY
                and self.legacy_archive is None
            )
        ):
            raise ValueError("cockpit view collection is outside its bound")
