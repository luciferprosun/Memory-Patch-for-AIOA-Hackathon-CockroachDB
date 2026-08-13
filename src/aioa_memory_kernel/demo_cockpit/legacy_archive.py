"""Verified metadata for the non-executable Critical Prompt Loop archive view."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import PurePosixPath

from aioa_memory_kernel.contracts.serialization import canonical_sha256


AOIA_CORE_REPOSITORY = "https://github.com/luciferprosun/AOIA-Core.git"
LEGACY_ARCHIVE_SCHEMA_VERSION = "legacy-critical-prompt-archive-1a"
_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_MAXIMUM_SOURCE_REFERENCES = 16


class LegacyCompatibilityMode(str, Enum):
    """Closed server-side capability family for the legacy surface."""

    DISABLED = "DISABLED"
    ARCHIVAL_VIEW = "ARCHIVAL_VIEW"


class LegacySourceClassification(str, Enum):
    PORT_PRESENTATION = "PORT_PRESENTATION"
    REFERENCE_ONLY = "REFERENCE_ONLY"
    REJECT_RUNTIME = "REJECT_RUNTIME"


class LegacyObserverRole(str, Enum):
    LOGIC_AND_CLAIMS = "Logic & Claims"
    SAFETY_AND_AUTHORITY = "Safety & Authority"
    EVIDENCE_AND_CONSISTENCY = "Evidence & Consistency"


@dataclass(frozen=True, slots=True)
class LegacySourceReference:
    source_repository: str
    source_commit: str
    source_path: str
    git_blob_sha1: str
    content_sha256: str
    size_bytes: int
    classification: LegacySourceClassification
    target_component: str

    def __post_init__(self) -> None:
        try:
            path = PurePosixPath(self.source_path)
        except (TypeError, ValueError):
            raise ValueError("legacy source path is invalid") from None
        if (
            self.source_repository != AOIA_CORE_REPOSITORY
            or not isinstance(self.source_commit, str)
            or not _HEX40.fullmatch(self.source_commit)
            or not isinstance(self.git_blob_sha1, str)
            or not _HEX40.fullmatch(self.git_blob_sha1)
            or not isinstance(self.content_sha256, str)
            or not _HEX64.fullmatch(self.content_sha256)
            or not isinstance(self.size_bytes, int)
            or not 1 <= self.size_bytes <= 1_000_000
            or not isinstance(self.classification, LegacySourceClassification)
            or not isinstance(self.target_component, str)
            or not self.target_component
            or len(self.target_component.encode("utf-8")) > 128
            or any(
                ord(character) < 32 or ord(character) == 127
                for character in self.target_component
            )
            or path.is_absolute()
            or str(path) != self.source_path
            or ".." in path.parts
            or "\\" in self.source_path
            or len(self.source_path.encode("utf-8")) > 256
        ):
            raise ValueError("legacy source reference is invalid")


@dataclass(frozen=True, slots=True)
class LegacyArchiveManifest:
    """Hash-bound provenance metadata, deliberately without replay content."""

    schema_version: str
    system_name: str
    effective_mode: LegacyCompatibilityMode
    source_repository: str
    source_references: tuple[LegacySourceReference, ...]
    observer_roles: tuple[LegacyObserverRole, ...]
    historical_completed_call_plan: str
    historical_completed_provider_calls: int
    exact_prompt_status: str
    historical_output_status: str
    replay_bundle_status: str
    effective_provider_call_minimum: int
    effective_provider_call_maximum: int
    legacy_personal_memory_write: bool
    metadata_digest: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.source_references, tuple)
            or not 1 <= len(self.source_references) <= _MAXIMUM_SOURCE_REFERENCES
            or any(
                not isinstance(reference, LegacySourceReference)
                for reference in self.source_references
            )
            or not isinstance(self.observer_roles, tuple)
            or not isinstance(self.metadata_digest, str)
        ):
            raise ValueError("legacy archive manifest failed closed")
        identities = tuple(
            (reference.source_commit, reference.source_path)
            for reference in self.source_references
        )
        expected_roles = (
            LegacyObserverRole.LOGIC_AND_CLAIMS,
            LegacyObserverRole.SAFETY_AND_AUTHORITY,
            LegacyObserverRole.EVIDENCE_AND_CONSISTENCY,
        )
        if (
            self.schema_version != LEGACY_ARCHIVE_SCHEMA_VERSION
            or self.system_name != "AOIA Critical Prompt Loop"
            or self.effective_mode is not LegacyCompatibilityMode.ARCHIVAL_VIEW
            or self.source_repository != AOIA_CORE_REPOSITORY
            or len(set(identities)) != len(identities)
            or self.observer_roles != expected_roles
            or self.historical_completed_call_plan
            != "1 MAIN DRAFT + 3 SEQUENTIAL OBSERVERS + 1 FINAL REVISION"
            or self.historical_completed_provider_calls != 5
            or self.exact_prompt_status != "NOT_FOUND_AS_VERSIONED_EXECUTION_INPUT"
            or self.historical_output_status != "NOT_FOUND_AS_COMPLETE_EXACT_BYTES"
            or self.replay_bundle_status != "NOT_CREATED_MISSING_HISTORICAL_BYTES"
            or self.effective_provider_call_minimum != 0
            or self.effective_provider_call_maximum != 0
            or self.legacy_personal_memory_write is not False
            or not _HEX64.fullmatch(self.metadata_digest)
            or self.metadata_digest
            != canonical_sha256(self, exclude_fields=("metadata_digest",))
        ):
            raise ValueError("legacy archive manifest failed closed")


def _source(
    commit: str,
    path: str,
    blob: str,
    content_sha256: str,
    size_bytes: int,
    classification: LegacySourceClassification,
    target_component: str,
) -> LegacySourceReference:
    return LegacySourceReference(
        source_repository=AOIA_CORE_REPOSITORY,
        source_commit=commit,
        source_path=path,
        git_blob_sha1=blob,
        content_sha256=content_sha256,
        size_bytes=size_bytes,
        classification=classification,
        target_component=target_component,
    )


def build_legacy_archive_manifest() -> LegacyArchiveManifest:
    """Build the static D0/D2 source inventory and bind its canonical bytes."""

    sources = (
        _source(
            "eda1449e6a63b6a41d8bc16409aa31a128176804",
            "runtime/run_web.sh",
            "521dc4c5c51d4386fd8cebf0d3462bbe2d596201",
            "56e4e37b5b69c960e791c21b806aa8a217679f2159e3b190eba2083390e1193c",
            364,
            LegacySourceClassification.REJECT_RUNTIME,
            "launcher provenance only",
        ),
        _source(
            "eda1449e6a63b6a41d8bc16409aa31a128176804",
            "runtime/webapp.py",
            "be3cd5b2f45390a509e88fed42f0cce871b332e2",
            "cab5d5f4cbced96070b8572cd9a6fe9da4478591d4e415a302cf4a2dae1143ef",
            33_303,
            LegacySourceClassification.REJECT_RUNTIME,
            "cockpit visual reference",
        ),
        _source(
            "eda1449e6a63b6a41d8bc16409aa31a128176804",
            "runtime/orchestra_live_smoke_cli.py",
            "fb3ff9c79f8f8544e740a075761b478244de89f5",
            "a8e80ae44c6786af0a957a1eab13422aa1b7f75519f958a4010b13350f76eb6b",
            11_990,
            LegacySourceClassification.REFERENCE_ONLY,
            "bounded orchestration reference",
        ),
        _source(
            "46695cde96d12a52e20bea82ebe2e1798b7451fd",
            "runtime/webapp.py",
            "ec0952c75ecb884902fb5e7874c8f4c936b7a2da",
            "96845111f4025f30feca85d0f2d6564af9106724bd12d670fafd7eb0379deacd",
            35_313,
            LegacySourceClassification.PORT_PRESENTATION,
            "status and review presentation",
        ),
        _source(
            "51abb9faab2d07d21003a345c747b90b8eac5703",
            "apps/aoia_desktop_demo/critical_review.py",
            "ca2ea240d24525fb72ee9caccf475ecfff751625",
            "0109969c6fbb7ccaca69a91f42a961ea7bc9806f150443a5e6009502ac801615",
            19_913,
            LegacySourceClassification.REFERENCE_ONLY,
            "bounded review origin",
        ),
        _source(
            "5ec74f85256c260dadbc795143eb132b4119aab6",
            "apps/aoia_desktop_demo/critical_review.py",
            "07fabcf104eac4de63aa7f60d6af79fcdd7c37e4",
            "cf5c48a3ce236df994844659aa46f3e1d4359f6d62774e7148526dc66f184964",
            29_531,
            LegacySourceClassification.REFERENCE_ONLY,
            "completed five-call flow reference",
        ),
        _source(
            "5ec74f85256c260dadbc795143eb132b4119aab6",
            "apps/aoia_desktop_demo/ui/cockpit_state.py",
            "8960081a8e21ee76d59607afe5d235aec928d949",
            "7912240599abb0afe34c5cda6eaf2b3e5db134eacd475031bed1a4e1e2a1ed43",
            5_201,
            LegacySourceClassification.PORT_PRESENTATION,
            "closed three-observer presentation",
        ),
        _source(
            "5ec74f85256c260dadbc795143eb132b4119aab6",
            "apps/aoia_desktop_demo/ui/main_window.py",
            "b2d08e863a34512e93baf11b8114cedc2a183725",
            "7737f28681219495cacc0213614794d7f343933de9541619d99a60196eb9c94d",
            35_880,
            LegacySourceClassification.REJECT_RUNTIME,
            "desktop layout reference",
        ),
    )
    values = {
        "schema_version": LEGACY_ARCHIVE_SCHEMA_VERSION,
        "system_name": "AOIA Critical Prompt Loop",
        "effective_mode": LegacyCompatibilityMode.ARCHIVAL_VIEW,
        "source_repository": AOIA_CORE_REPOSITORY,
        "source_references": sources,
        "observer_roles": (
            LegacyObserverRole.LOGIC_AND_CLAIMS,
            LegacyObserverRole.SAFETY_AND_AUTHORITY,
            LegacyObserverRole.EVIDENCE_AND_CONSISTENCY,
        ),
        "historical_completed_call_plan": (
            "1 MAIN DRAFT + 3 SEQUENTIAL OBSERVERS + 1 FINAL REVISION"
        ),
        "historical_completed_provider_calls": 5,
        "exact_prompt_status": "NOT_FOUND_AS_VERSIONED_EXECUTION_INPUT",
        "historical_output_status": "NOT_FOUND_AS_COMPLETE_EXACT_BYTES",
        "replay_bundle_status": "NOT_CREATED_MISSING_HISTORICAL_BYTES",
        "effective_provider_call_minimum": 0,
        "effective_provider_call_maximum": 0,
        "legacy_personal_memory_write": False,
    }
    return LegacyArchiveManifest(
        **values,
        metadata_digest=canonical_sha256(values),
    )


__all__ = [
    "AOIA_CORE_REPOSITORY",
    "LEGACY_ARCHIVE_SCHEMA_VERSION",
    "LegacyArchiveManifest",
    "LegacyCompatibilityMode",
    "LegacyObserverRole",
    "LegacySourceClassification",
    "LegacySourceReference",
    "build_legacy_archive_manifest",
]
