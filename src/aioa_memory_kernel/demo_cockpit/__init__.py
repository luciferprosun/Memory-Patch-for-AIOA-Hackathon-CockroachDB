"""Bounded presentation contracts for the unified AIOA demo cockpit."""

from .models import (
    CockpitExecutionKind,
    CockpitMode,
    CockpitModeOption,
    CockpitRunState,
    CockpitRuntimeStatus,
    CockpitStageSummary,
    CockpitView,
    LegacyModeStatus,
)
from .shell import CockpitShell, build_default_cockpit_shell

__all__ = [
    "CockpitExecutionKind",
    "CockpitMode",
    "CockpitModeOption",
    "CockpitRunState",
    "CockpitRuntimeStatus",
    "CockpitShell",
    "CockpitStageSummary",
    "CockpitView",
    "LegacyModeStatus",
    "build_default_cockpit_shell",
]
