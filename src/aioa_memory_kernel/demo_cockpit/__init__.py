"""Bounded presentation contracts for the unified AIOA demo cockpit."""

from .legacy_archive import (
    AOIA_CORE_REPOSITORY,
    LEGACY_ARCHIVE_SCHEMA_VERSION,
    LegacyArchiveManifest,
    LegacyCompatibilityMode,
    LegacyObserverRole,
    LegacySourceClassification,
    LegacySourceReference,
    build_legacy_archive_manifest,
)

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
    "AOIA_CORE_REPOSITORY",
    "LEGACY_ARCHIVE_SCHEMA_VERSION",
    "CockpitExecutionKind",
    "CockpitMode",
    "CockpitModeOption",
    "CockpitRunState",
    "CockpitRuntimeStatus",
    "CockpitShell",
    "CockpitStageSummary",
    "CockpitView",
    "LegacyModeStatus",
    "LegacyArchiveManifest",
    "LegacyCompatibilityMode",
    "LegacyObserverRole",
    "LegacySourceClassification",
    "LegacySourceReference",
    "build_legacy_archive_manifest",
    "build_default_cockpit_shell",
]
