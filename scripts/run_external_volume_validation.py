#!/usr/bin/env python3
"""Preflight and explicitly gated live validation for Memory Patch Step 8."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from aioa_memory_kernel.storage import (  # noqa: E402
    EXTERNAL_VOLUME_EXPECTED_REMOTE,
    EXTERNAL_VOLUME_PROJECT_ID,
    ExternalVolumeConfig,
    ExternalVolumeError,
    ExternalVolumeIntegrityError,
    ExternalVolumeOperation,
    ExternalVolumeRuntimeAdapter,
    load_external_volume_environment,
)


EXPECTED_BRANCH = "main"
DEFAULT_CONFIG = REPOSITORY_ROOT / ".local" / "external-data.env"
VALIDATION_FIXTURE = (
    REPOSITORY_ROOT
    / "tests"
    / "fixtures"
    / "step7_live_validation_snapshot.json"
)
VALIDATION_SHA256 = (
    "d1bedd6275072d01cc932af7a57d826837e27b9c7d1039bb558d4039abf819fc"
)
VALIDATION_LENGTH = 88
VALIDATION_RELATIVE_PATH = f"step8-validation-{VALIDATION_SHA256}.json"


def _git(arguments: list[str]) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(REPOSITORY_ROOT), *arguments],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
            env={
                "PATH": os.environ.get("PATH", "/usr/sbin:/usr/bin:/sbin:/bin"),
                "LANG": "C",
                "LC_ALL": "C",
            },
        )
    except (
        FileNotFoundError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        UnicodeError,
    ) as exc:
        raise RuntimeError("repository guard failed") from exc
    return completed.stdout.strip()


def _repository_guard() -> None:
    if Path(_git(["rev-parse", "--show-toplevel"])) != REPOSITORY_ROOT:
        raise RuntimeError("repository guard failed")
    remote = _git(["remote", "get-url", "origin"]).removesuffix(".git").rstrip("/")
    if remote != EXTERNAL_VOLUME_EXPECTED_REMOTE:
        raise RuntimeError("repository guard failed")
    if _git(["branch", "--show-current"]) != EXPECTED_BRANCH:
        raise RuntimeError("repository guard failed")
    git_directory = Path(_git(["rev-parse", "--git-dir"]))
    if not git_directory.is_absolute():
        git_directory = REPOSITORY_ROOT / git_directory
    operation_markers = (
        "MERGE_HEAD",
        "CHERRY_PICK_HEAD",
        "REVERT_HEAD",
        "BISECT_LOG",
        "rebase-merge",
        "rebase-apply",
    )
    if any((git_directory / marker).exists() for marker in operation_markers):
        raise RuntimeError("repository guard failed")
    if _git(["ls-files", "-u"]):
        raise RuntimeError("repository guard failed")


def _adapter(config_path: Path) -> ExternalVolumeRuntimeAdapter:
    values = load_external_volume_environment(config_path)
    config = ExternalVolumeConfig.from_mapping(values)
    return ExternalVolumeRuntimeAdapter(config)


def _fixture() -> bytes:
    payload = VALIDATION_FIXTURE.read_bytes()
    import hashlib

    if (
        len(payload) != VALIDATION_LENGTH
        or hashlib.sha256(payload).hexdigest() != VALIDATION_SHA256
    ):
        raise ExternalVolumeIntegrityError(
            "Step 7 compatibility fixture identity differs",
            sanitized_code="STEP7_FIXTURE_IDENTITY_MISMATCH",
        )
    return payload


def _result(status: str, **details: Any) -> None:
    print(
        json.dumps(
            {
                "schema_version": "1.0.0",
                "step": (
                    "Step 8 - External Volume Runtime Adapter "
                    "and Fail-Closed Policy 1A"
                ),
                "status": status,
                **details,
            },
            indent=2,
            sort_keys=True,
        )
    )


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--preflight",
        action="store_true",
        help="run read-only live identity, marker, tree, and space checks",
    )
    mode.add_argument(
        "--write-validation",
        action="store_true",
        help="perform the one fixed exact-byte no-overwrite validation write",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help="private Step 0B machine-local configuration",
    )
    parser.add_argument("--confirm-project")
    parser.add_argument("--confirm-device-reference")
    return parser.parse_args()


def main() -> int:
    arguments = _arguments()
    try:
        _repository_guard()
        adapter = _adapter(arguments.config)
        # Preflight verifies write capability without creating or changing a
        # file. The actual write remains exclusive to --write-validation.
        status = adapter.verify(require_write=True)
        target = adapter.resolve_path(
            ExternalVolumeOperation.APPLICATION_SNAPSHOT_STAGING,
            VALIDATION_RELATIVE_PATH,
            require_write=True,
        )
        incomplete_artifacts = adapter.incomplete_atomic_artifacts(
            ExternalVolumeOperation.APPLICATION_SNAPSHOT_STAGING,
            VALIDATION_RELATIVE_PATH,
        )
        if incomplete_artifacts:
            raise ExternalVolumeError(
                "target-bound incomplete atomic staging requires recovery",
                operation=(
                    ExternalVolumeOperation.APPLICATION_SNAPSHOT_STAGING.value
                ),
                sanitized_code="EXTERNAL_STAGING_ARTIFACT_EXISTS",
            )
        target_state = "ABSENT"
        if os.path.lexists(target):
            adapter.read_exact(
                ExternalVolumeOperation.APPLICATION_SNAPSHOT_STAGING,
                VALIDATION_RELATIVE_PATH,
                expected_sha256=VALIDATION_SHA256,
                expected_length=VALIDATION_LENGTH,
            )
            target_state = "COMPLETE_VALIDATION_ARTIFACT"
        if not arguments.write_validation:
            _result(
                "PREFLIGHT_PASS_NO_WRITE",
                volume=status.to_dict(),
                validation_plan={
                    "operation": (
                        ExternalVolumeOperation.APPLICATION_SNAPSHOT_STAGING.value
                    ),
                    "relative_path": (
                        "snapshots/application/" + VALIDATION_RELATIVE_PATH
                    ),
                    "content_sha256": VALIDATION_SHA256,
                    "content_length": VALIDATION_LENGTH,
                    "atomic_no_replace": True,
                    "exact_read_back_required": True,
                    "target_state": target_state,
                    "incomplete_atomic_artifacts": [],
                    "system_drive_fallback_allowed": False,
                },
            )
            return 0
        if arguments.confirm_project != EXTERNAL_VOLUME_PROJECT_ID:
            raise ExternalVolumeError(
                "live validation requires exact project confirmation",
                sanitized_code="LIVE_VALIDATION_PROJECT_CONFIRMATION_REQUIRED",
            )
        if arguments.confirm_device_reference != status.device_reference:
            raise ExternalVolumeError(
                "live validation requires exact device-reference confirmation",
                sanitized_code="LIVE_VALIDATION_DEVICE_CONFIRMATION_REQUIRED",
            )
        payload = _fixture()
        if target_state == "COMPLETE_VALIDATION_ARTIFACT":
            _result(
                "ALREADY_VALID_NO_WRITE",
                volume=status.to_dict(),
                validation={
                    "operation": (
                        ExternalVolumeOperation.APPLICATION_SNAPSHOT_STAGING.value
                    ),
                    "relative_path": (
                        "snapshots/application/" + VALIDATION_RELATIVE_PATH
                    ),
                    "content_sha256": VALIDATION_SHA256,
                    "content_length": VALIDATION_LENGTH,
                    "exact_read_back": True,
                    "system_drive_fallback_allowed": False,
                },
            )
            return 0
        evidence = adapter.atomic_write_exact(
            ExternalVolumeOperation.APPLICATION_SNAPSHOT_STAGING,
            VALIDATION_RELATIVE_PATH,
            payload,
            expected_sha256=VALIDATION_SHA256,
            expected_length=VALIDATION_LENGTH,
        )
        _result(
            "LIVE_VALIDATION_PASS",
            volume=status.to_dict(),
            validation=evidence.to_dict(),
            step7_compatibility={
                "canonical_sha256_equal": True,
                "content_length_equal": True,
            },
        )
        return 0
    except ExternalVolumeError as exc:
        print(
            json.dumps(
                {
                    "schema_version": "1.0.0",
                    "status": "FAIL_CLOSED",
                    "sanitized_code": exc.sanitized_code,
                    "operation": exc.operation,
                    "system_drive_fallback_allowed": False,
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    except (OSError, RuntimeError, ValueError):
        print(
            json.dumps(
                {
                    "schema_version": "1.0.0",
                    "status": "FAIL_CLOSED",
                    "sanitized_code": "STEP8_VALIDATION_RUNTIME_FAILURE",
                    "operation": None,
                    "system_drive_fallback_allowed": False,
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
