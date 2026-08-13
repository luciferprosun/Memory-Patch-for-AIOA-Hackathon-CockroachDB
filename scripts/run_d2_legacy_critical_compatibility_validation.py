#!/usr/bin/env python3
"""Offline D2 validator for the non-executable legacy archive surface."""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from aioa_memory_kernel.contracts.serialization import (  # noqa: E402
    canonical_json,
    canonical_sha256,
)
from aioa_memory_kernel.demo_cockpit import (  # noqa: E402
    AOIA_CORE_REPOSITORY,
    CockpitMode,
    CockpitShell,
    LegacyCompatibilityMode,
    build_default_cockpit_shell,
    build_legacy_archive_manifest,
)


_HEX40 = re.compile(r"^[0-9a-f]{40}$")


def _git(repository: Path, *arguments: str, binary: bool = False) -> str | bytes:
    result = subprocess.run(
        ("git", "-C", str(repository), *arguments),
        check=True,
        capture_output=True,
        text=not binary,
    )
    if binary:
        return result.stdout
    return result.stdout.strip()


def _verify_aoia_core_bytes(repository: Path) -> dict[str, Any]:
    root = Path(str(_git(repository, "rev-parse", "--show-toplevel")))
    remote = str(_git(root, "remote", "get-url", "origin"))
    if remote != AOIA_CORE_REPOSITORY:
        raise ValueError("AOIA-Core source repository identity mismatch")
    manifest = build_legacy_archive_manifest()
    verified = 0
    for source in manifest.source_references:
        object_name = f"{source.source_commit}:{source.source_path}"
        if str(_git(root, "cat-file", "-t", object_name)) != "blob":
            raise ValueError("AOIA-Core source object is not a blob")
        if str(_git(root, "rev-parse", object_name)) != source.git_blob_sha1:
            raise ValueError("AOIA-Core Git blob identity mismatch")
        payload = _git(root, "show", object_name, binary=True)
        if not isinstance(payload, bytes):
            raise TypeError("AOIA-Core source payload was not bytes")
        if len(payload) != source.size_bytes:
            raise ValueError("AOIA-Core source byte size mismatch")
        if hashlib.sha256(payload).hexdigest() != source.content_sha256:
            raise ValueError("AOIA-Core source SHA-256 mismatch")
        verified += 1
    return {
        "repository": AOIA_CORE_REPOSITORY,
        "source_byte_hashes": "PASS",
        "source_references_verified": verified,
    }


def run_validation(aoia_core: Path | None = None) -> dict[str, Any]:
    head = str(_git(ROOT, "rev-parse", "HEAD"))
    if not _HEX40.fullmatch(head):
        raise ValueError("D2 base HEAD is invalid")
    manifest = build_legacy_archive_manifest()
    default = build_default_cockpit_shell()
    shell = CockpitShell(
        default.runtime_status,
        legacy_mode=LegacyCompatibilityMode.ARCHIVAL_VIEW,
        legacy_archive=manifest,
    )
    current = shell.project()
    archive = shell.project(CockpitMode.CRITICAL_PROMPT_LEGACY.value)
    missing = CockpitShell(
        default.runtime_status,
        legacy_mode=LegacyCompatibilityMode.ARCHIVAL_VIEW,
        legacy_archive=None,
    ).project(CockpitMode.CRITICAL_PROMPT_LEGACY.value)
    if (
        current.selected_mode is not CockpitMode.MEMORY_PATCH_CURRENT
        or archive.selected_mode is not CockpitMode.CRITICAL_PROMPT_LEGACY
        or archive.legacy_archive != manifest
        or archive.legacy.classification != "DISABLED_WITH_ARCHIVAL_VIEW"
        or missing.selected_mode is not CockpitMode.MEMORY_PATCH_CURRENT
        or manifest.effective_provider_call_maximum != 0
        or manifest.legacy_personal_memory_write
    ):
        raise ValueError("D2 compatibility invariant failed")
    source_validation = (
        _verify_aoia_core_bytes(aoia_core)
        if aoia_core is not None
        else {
            "repository": AOIA_CORE_REPOSITORY,
            "source_byte_hashes": "NOT_RUN_PATH_NOT_SUPPLIED",
            "source_references_verified": 0,
        }
    )
    report: dict[str, Any] = {
        "step": "D2",
        "schema_version": "d2-legacy-critical-compatibility-validation-1a",
        "base_head": head,
        "d0_classification": "LEGACY_VIEW_ONLY",
        "d1_verdict": "COMPLETE_AND_PUSHED_READY_FOR_D2",
        "effective_legacy_decision": "DISABLED_WITH_ARCHIVAL_VIEW",
        "archive_manifest_digest": manifest.metadata_digest,
        "historical_source_repository": AOIA_CORE_REPOSITORY,
        "historical_source_validation": source_validation,
        "historical_completed_provider_calls": (
            manifest.historical_completed_provider_calls
        ),
        "effective_provider_calls_minimum": 0,
        "effective_provider_calls_maximum": 0,
        "actual_paid_provider_calls": 0,
        "provider_guard_binding": "NO_PROVIDER_PATH_IN_ARCHIVAL_MODE",
        "current_mode_independent": True,
        "archive_missing_failure_mode": "CURRENT_MODE_REMAINS_AVAILABLE",
        "replay_bundle": None,
        "missing_historical_bytes_fabricated": 0,
        "legacy_authority": {
            "canonical_evidence": False,
            "route_or_hat": False,
            "approval": False,
            "commit": False,
            "activation": False,
            "personal_memory_write": False,
            "reviewer": False,
            "external_execution": False,
        },
        "authority_violations": 0,
        "cross_owner_or_session_violations": 0,
        "secret_leakage_count": 0,
        "production_or_aws_resources_touched": 0,
        "d3_started": False,
        "cleanup": "NO_RUNTIME_RESOURCES_CREATED",
        "verdict": "PASS",
    }
    report["validation_digest"] = canonical_sha256(report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--aoia-core",
        type=Path,
        help="Optional read-only AOIA-Core checkout used to verify exact source bytes.",
    )
    arguments = parser.parse_args()
    try:
        report = run_validation(arguments.aoia_core)
    except (OSError, subprocess.SubprocessError, TypeError, ValueError):
        print(
            canonical_json(
                {
                    "status": "FAILED",
                    "reason": "D2_VALIDATION_FAILED_CLOSED",
                }
            ),
            file=sys.stderr,
        )
        return 1
    print(canonical_json(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
