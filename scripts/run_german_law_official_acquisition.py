#!/usr/bin/env python3
"""Guarded, resumable orchestration for official German-law acquisition 1A."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import stat
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from aioa_memory_kernel.acquisition import (  # noqa: E402
    AcquisitionError,
    AcquisitionIntegrityError,
    AcquisitionPolicy,
    AcquisitionPolicyError,
    AcquisitionRootGuard,
    AcquisitionStorageError,
    AcquisitionTransportError,
    SafeHttpsClient,
    SourceStatus,
)
from aioa_memory_kernel.contracts.serialization import (  # noqa: E402
    canonical_json_bytes,
    canonical_sha256,
)
from aioa_memory_kernel.german_law.acquisition import (  # noqa: E402
    BAYERN_ID,
    BGBL_ID,
    BMF_ID,
    BREMEN_ID,
    DIP_ID,
    EURLEX_ID,
    GII_ID,
    OfficialGermanLawAcquisition,
)


EXPECTED_REMOTE = (
    "https://github.com/luciferprosun/"
    "Memory-Patch-for-AIOA-Hackathon-CockroachDB"
)
EXPECTED_BRANCH = "main"
EXPECTED_BASELINE = "19f553fd010aba0e2d0db0714920d38f49aff0fd"
DEFAULT_CONFIG = REPOSITORY_ROOT / ".local" / "external-data.env"
DEFAULT_RUNTIME_SECONDS = 21_600
MAXIMUM_RUNTIME_SECONDS = 604_800
COMPATIBLE_REPAIR_REASON = "ACQUISITION_RESUME_SECURITY_HARDENING_1A"

SOURCE_ORDER = (
    "bayern",
    "bmf",
    "dip",
    "eurlex",
    "bremen",
    "gii",
    "bgbl",
)
SOURCE_IDENTITIES = {
    "gii": GII_ID,
    "bgbl": BGBL_ID,
    "bremen": BREMEN_ID,
    "bayern": BAYERN_ID,
    "bmf": BMF_ID,
    "dip": DIP_ID,
    "eurlex": EURLEX_ID,
}
class OfficialAcquisitionScriptError(RuntimeError):
    """Sanitized orchestration failure that never exposes a local path."""

    def __init__(self, code: str) -> None:
        super().__init__("official acquisition orchestration failed")
        self.code = code


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _git(arguments: Sequence[str]) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(REPOSITORY_ROOT), *arguments],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=20,
            env={
                "PATH": os.environ.get(
                    "PATH",
                    "/usr/local/bin:/usr/bin:/bin",
                ),
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
        raise OfficialAcquisitionScriptError("REPOSITORY_GUARD_FAILED") from exc
    return completed.stdout


def _normalize_remote(value: str) -> str:
    return value.strip().removesuffix(".git").rstrip("/")


def _nul_items(value: str) -> tuple[str, ...]:
    return tuple(item for item in value.split("\0") if item)


def _dirty_path_allowed(relative: str) -> bool:
    fixed = {
        "src/aioa_memory_kernel/runtime/external_volume_linux.py",
        "src/aioa_memory_kernel/german_law/acquisition.py",
        "scripts/run_german_law_official_acquisition.py",
        "src/aioa_memory_kernel/acquisition/__init__.py",
        "tests/test_external_volume_linux_probe.py",
    }
    if relative in fixed:
        return True
    if re.fullmatch(
        r"src/aioa_memory_kernel/acquisition/[a-z][a-z0-9_]*\.py",
        relative,
    ):
        return True
    return (
        re.fullmatch(
            r"tests/test_(?:official_acquisition_[a-z0-9_]+|"
            r"german_law_official_acquisition(?:_[a-z0-9_]+)?)\.py",
            relative,
        )
        is not None
    )


def _worktree_records(paths: Sequence[str]) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for relative in sorted(paths):
        path = REPOSITORY_ROOT / relative
        try:
            metadata = path.lstat()
        except OSError as exc:
            raise OfficialAcquisitionScriptError("DIRTY_SCOPE_UNSAFE") from exc
        if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
            raise OfficialAcquisitionScriptError("DIRTY_SCOPE_UNSAFE")
        digest = hashlib.sha256()
        length = 0
        with path.open("rb", buffering=0) as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
                length += len(chunk)
        after = path.lstat()
        if (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_size,
            metadata.st_mtime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise OfficialAcquisitionScriptError("DIRTY_SCOPE_CHANGED")
        records.append(
            {
                "relative_path": relative,
                "byte_length": length,
                "sha256": digest.hexdigest(),
            }
        )
    return records


def _worktree_digest(paths: Sequence[str]) -> str:
    return canonical_sha256(_worktree_records(paths))


def _repository_guard() -> dict[str, object]:
    if Path(_git(("rev-parse", "--show-toplevel")).strip()) != REPOSITORY_ROOT:
        raise OfficialAcquisitionScriptError("REPOSITORY_IDENTITY_MISMATCH")
    if _normalize_remote(_git(("remote", "get-url", "origin"))) != EXPECTED_REMOTE:
        raise OfficialAcquisitionScriptError("REPOSITORY_REMOTE_MISMATCH")
    if _git(("branch", "--show-current")).strip() != EXPECTED_BRANCH:
        raise OfficialAcquisitionScriptError("REPOSITORY_BRANCH_MISMATCH")
    _git(("fetch", "origin", "--prune"))
    head = _git(("rev-parse", "HEAD")).strip()
    origin_main = _git(("rev-parse", "origin/main")).strip()
    if head != EXPECTED_BASELINE or origin_main != EXPECTED_BASELINE:
        raise OfficialAcquisitionScriptError("REPOSITORY_BASELINE_MISMATCH")
    if _git(("rev-list", "--left-right", "--count", "origin/main...HEAD")).split() != [
        "0",
        "0",
    ]:
        raise OfficialAcquisitionScriptError("REPOSITORY_DIVERGED")
    if _git(("ls-files", "-u")).strip():
        raise OfficialAcquisitionScriptError("INTERRUPTED_GIT_OPERATION")

    git_directory = Path(_git(("rev-parse", "--git-dir")).strip())
    if not git_directory.is_absolute():
        git_directory = REPOSITORY_ROOT / git_directory
    for marker in (
        "MERGE_HEAD",
        "REBASE_HEAD",
        "CHERRY_PICK_HEAD",
        "REVERT_HEAD",
        "BISECT_LOG",
        "rebase-merge",
        "rebase-apply",
    ):
        if (git_directory / marker).exists():
            raise OfficialAcquisitionScriptError("INTERRUPTED_GIT_OPERATION")

    worktrees = [
        Path(line.removeprefix("worktree "))
        for line in _git(("worktree", "list", "--porcelain")).splitlines()
        if line.startswith("worktree ")
    ]
    if worktrees != [REPOSITORY_ROOT]:
        raise OfficialAcquisitionScriptError("AMBIGUOUS_GIT_WORKTREE")

    tracked = _nul_items(
        _git(("diff", "--name-only", "-z", "HEAD", "--"))
    )
    untracked = _nul_items(
        _git(("ls-files", "--others", "--exclude-standard", "-z"))
    )
    dirty_paths = tuple(sorted(set((*tracked, *untracked))))
    if not dirty_paths or any(
        not _dirty_path_allowed(path) for path in dirty_paths
    ):
        raise OfficialAcquisitionScriptError("DIRTY_SCOPE_UNSAFE")
    dirty_file_records = _worktree_records(dirty_paths)
    return {
        "repository_head": head,
        "origin_main": origin_main,
        "ahead_behind": [0, 0],
        "dirty_paths": list(dirty_paths),
        "dirty_file_records": dirty_file_records,
        "worktree_digest": canonical_sha256(dirty_file_records),
    }


def _parse_sources(value: str) -> tuple[str, ...]:
    if not isinstance(value, str) or not value or value != value.strip():
        raise argparse.ArgumentTypeError("--sources must be a non-empty list")
    requested = value.split(",")
    if (
        any(not item or item != item.strip() for item in requested)
        or len(requested) != len(set(requested))
        or any(item not in SOURCE_ORDER for item in requested)
    ):
        raise argparse.ArgumentTypeError(
            "--sources contains an unknown, duplicated, or malformed token"
        )
    selected = set(requested)
    return tuple(item for item in SOURCE_ORDER if item in selected)


def _runtime_seconds(value: str) -> int:
    if not value.isdecimal():
        raise argparse.ArgumentTypeError("runtime must be a decimal integer")
    parsed = int(value)
    if not 1 <= parsed <= MAXIMUM_RUNTIME_SECONDS:
        raise argparse.ArgumentTypeError("runtime is outside its bounded policy")
    return parsed


def _sha256_argument(value: str) -> str:
    if re.fullmatch(r"[0-9a-f]{64}", value or "") is None:
        raise argparse.ArgumentTypeError("confirmation must be one SHA-256")
    return value


def _arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--initialize", action="store_true")
    mode.add_argument("--resume", action="store_true")
    mode.add_argument("--record-compatible-repair", action="store_true")
    parser.add_argument(
        "--sources",
        type=_parse_sources,
        default=SOURCE_ORDER,
        help="comma-separated subset: " + ",".join(SOURCE_ORDER),
    )
    parser.add_argument(
        "--max-runtime-seconds",
        type=_runtime_seconds,
        default=DEFAULT_RUNTIME_SECONDS,
    )
    parser.add_argument(
        "--repair-reason",
        choices=(COMPATIBLE_REPAIR_REASON,),
    )
    parser.add_argument(
        "--confirm-previous-worktree-digest",
        type=_sha256_argument,
    )
    parser.add_argument(
        "--confirm-compatible-worktree-digest",
        type=_sha256_argument,
    )
    parser.add_argument(
        "--confirm-worktree-content-manifest-digest",
        type=_sha256_argument,
    )
    parser.add_argument(
        "--confirm-prior-repair-digest",
        type=_sha256_argument,
    )
    parsed = parser.parse_args(argv)
    confirmations = (
        parsed.repair_reason,
        parsed.confirm_previous_worktree_digest,
        parsed.confirm_compatible_worktree_digest,
        parsed.confirm_worktree_content_manifest_digest,
        parsed.confirm_prior_repair_digest,
    )
    if parsed.record_compatible_repair:
        if any(value is None for value in confirmations):
            parser.error(
                "compatible repair requires every exact digest confirmation"
            )
    elif any(value is not None for value in confirmations):
        parser.error("repair confirmations require --record-compatible-repair")
    return parsed


def _resume_command(sources: Sequence[str], runtime_seconds: int) -> str:
    command = (
        "python3",
        "scripts/run_german_law_official_acquisition.py",
        "--resume",
        "--sources",
        ",".join(sources),
        "--max-runtime-seconds",
        str(runtime_seconds),
    )
    return shlex.join(command)


def _hash_optional(root: AcquisitionRootGuard, relative: str) -> str | None:
    path = root.resolve(relative)
    if not path.exists():
        return None
    path = root.require_regular_file(relative)
    digest = hashlib.sha256()
    with path.open("rb", buffering=0) as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_bytes_once(
    root: AcquisitionRootGuard,
    relative: str,
    payload: bytes,
) -> str:
    target = root.resolve(relative)
    if target.exists():
        target = root.require_regular_file(relative)
        if target.read_bytes() != payload:
            raise AcquisitionIntegrityError(
                "immutable versioned report conflicts",
                code="ACQUISITION_REPLAY_CONFLICT",
            )
        return hashlib.sha256(payload).hexdigest()
    with root.stream_writer(relative) as writer:
        writer.write(payload)
        digest, _ = writer.publish()
    return digest


def _digested(value: Mapping[str, object]) -> dict[str, object]:
    payload = dict(value)
    payload["evidence_digest"] = canonical_sha256(payload)
    return payload


def _assert_sanitized(value: object) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            _assert_sanitized(key)
            _assert_sanitized(child)
        return
    if isinstance(value, (list, tuple)):
        for child in value:
            _assert_sanitized(child)
        return
    if isinstance(value, str):
        if (
            value.startswith("/")
            or value.startswith("file://")
            or "external-data.env" in value
            or "\x00" in value
        ):
            raise OfficialAcquisitionScriptError("REPORT_NOT_SANITIZED")


def _write_reports(
    *,
    root: AcquisitionRootGuard,
    run_id: str,
    mode: str,
    started_at: str,
    guard_state: Mapping[str, object],
    selected_sources: Sequence[str],
    maximum_runtime_seconds: int,
    results: Mapping[str, Mapping[str, object]],
    resume_sources: Sequence[str],
) -> dict[str, object]:
    resume_command = (
        _resume_command(resume_sources, maximum_runtime_seconds)
        if resume_sources
        else None
    )
    statuses = {source_id: result.get("status") for source_id, result in results.items()}
    if any(value == SourceStatus.PARTIAL.value for value in statuses.values()):
        run_status = "AUTHORIZED_ACQUISITION_PARTIAL_SAFE_RESUME_REQUIRED"
    elif any(value == SourceStatus.FAILED.value for value in statuses.values()):
        run_status = "FAILED_SAFELY_NO_FALSE_COMPLETION"
    elif any(
        value == SourceStatus.BLOCKED_CHANGED.value
        for value in statuses.values()
    ):
        run_status = "BLOCKED_OFFICIAL_CONDITIONS_CHANGED"
    else:
        run_status = "AUTHORIZED_ACQUISITION_COMPLETE"

    common = {
        "schema_version": "1.0.0",
        "report_version": "official-acquisition-report-1a",
        "run_id": run_id,
        "mode": mode,
        "started_at": started_at,
        "repository_head": guard_state["repository_head"],
        "worktree_digest": guard_state["worktree_digest"],
        "acquisition_policy_digest": root.policy.digest,
        "usb_device_reference": root.status.device_reference,
        "selected_sources": [SOURCE_IDENTITIES[item] for item in selected_sources],
        "maximum_runtime_seconds": maximum_runtime_seconds,
    }
    summary = _digested(
        {
            **common,
            "status": run_status,
            "source_results": dict(results),
            "request_count": root.request_count,
            "root_bytes_before_reports": root.root_size(),
            "free_bytes_before_reports": root.free_bytes(),
            "request_ledger_sha256": _hash_optional(
                root, "00_CONTROL/request-ledger.jsonl"
            ),
            "object_ledger_sha256": _hash_optional(
                root, "00_CONTROL/object-ledger.jsonl"
            ),
            "source_catalog_sha256": _hash_optional(
                root,
                "03_SOURCE_CATALOG/official-source-catalog.jsonl",
            ),
            "safe_resume_required": bool(resume_sources),
            "safe_resume_command": resume_command,
            "s3_writes": 0,
            "database_writes": 0,
            "publication": 0,
            "step15_started": False,
        }
    )
    coverage = _digested(
        {
            **common,
            "sources": [
                {
                    "source_catalog_id": SOURCE_IDENTITIES[token],
                    "result": dict(results[SOURCE_IDENTITIES[token]]),
                }
                for token in selected_sources
            ],
        }
    )
    missing = _digested(
        {
            **common,
            "non_complete_sources": [
                {
                    "source_catalog_id": source_id,
                    "status": result.get("status"),
                    "reason": result.get("reason")
                    or result.get("reason_code")
                    or "BOUNDED_RUN_INCOMPLETE",
                }
                for source_id, result in results.items()
                if result.get("status") != SourceStatus.COMPLETE.value
            ],
        }
    )
    refresh = _digested(
        {
            **common,
            "refresh_policy": [
                {
                    "source_catalog_id": source_id,
                    "current_status": result.get("status"),
                    "next_action": (
                        "RESUME_EXACT_CHECKPOINT"
                        if result.get("status")
                        in {SourceStatus.PARTIAL.value, SourceStatus.FAILED.value}
                        else "REFRESH_FROM_FROZEN_OFFICIAL_INDEX"
                    ),
                }
                for source_id, result in results.items()
            ],
        }
    )
    resume = _digested(
        {
            **common,
            "required": bool(resume_sources),
            "source_tokens": list(resume_sources),
            "exact_command": resume_command,
        }
    )
    records = {
        f"99_REPORTS/acquisition-summary-{run_id}.json": summary,
        f"99_REPORTS/coverage-matrix-{run_id}.json": coverage,
        f"99_REPORTS/missing-and-blocked-sources-{run_id}.json": missing,
        f"99_REPORTS/refresh-plan-{run_id}.json": refresh,
        f"99_REPORTS/resume-status-{run_id}.json": resume,
    }
    _assert_sanitized(records)
    report_digests: dict[str, str] = {}
    for relative, value in records.items():
        report_digests[relative] = _write_bytes_once(
            root,
            relative,
            canonical_json_bytes(value) + b"\n",
        )
    markdown_relative = f"99_REPORTS/acquisition-report-{run_id}.md"
    markdown = (
        "# German Law Official Corpus Acquisition 1A\n\n"
        f"- Run ID: `{run_id}`\n"
        f"- Status: `{run_status}`\n"
        f"- Repository HEAD: `{guard_state['repository_head']}`\n"
        f"- Requests: `{root.request_count}`\n"
        f"- Safe resume required: `{str(bool(resume_sources)).lower()}`\n"
        f"- Exact safe resume command: `{resume_command or 'NONE'}`\n"
        "- S3 writes: `0`\n"
        "- Database writes: `0`\n"
        "- Publication: `0`\n"
        "- Step 15 started: `false`\n"
    ).encode("utf-8")
    report_digests[markdown_relative] = _write_bytes_once(
        root,
        markdown_relative,
        markdown,
    )
    if not resume_sources:
        final_records = {
            "99_REPORTS/acquisition-summary.json": summary,
            "99_REPORTS/coverage-matrix.json": coverage,
            "99_REPORTS/missing-and-blocked-sources.json": missing,
            "99_REPORTS/refresh-plan.json": refresh,
            "99_REPORTS/resume-status.json": resume,
        }
        for relative, value in final_records.items():
            report_digests[relative] = _write_bytes_once(
                root,
                relative,
                canonical_json_bytes(value) + b"\n",
            )
        report_digests["99_REPORTS/acquisition-report.md"] = (
            _write_bytes_once(
                root,
                "99_REPORTS/acquisition-report.md",
                markdown,
            )
        )
    return {
        "run_id": run_id,
        "status": run_status,
        "safe_resume_command": resume_command,
        "report_digests": report_digests,
    }


def _classify_source_error(error: AcquisitionError) -> str:
    if isinstance(error, (AcquisitionPolicyError, AcquisitionTransportError)):
        return SourceStatus.BLOCKED_CHANGED.value
    return SourceStatus.FAILED.value


def _invoke_source(
    acquisition: OfficialGermanLawAcquisition,
    token: str,
) -> Mapping[str, object]:
    if token == "bayern":
        return acquisition.run_bayern()
    if token == "bmf":
        return acquisition.run_bmf()
    if token == "dip":
        return acquisition.run_dip_plan()
    if token == "eurlex":
        return acquisition.run_eurlex_plan()
    if token == "bremen":
        return acquisition.run_bremen()
    if token == "gii":
        return acquisition.run_gii()
    if token == "bgbl":
        return acquisition.run_bgbl()
    raise OfficialAcquisitionScriptError("SOURCE_TOKEN_UNSUPPORTED")


def _verify_resume_binding(
    root: AcquisitionRootGuard,
    guard_state: Mapping[str, object],
) -> None:
    try:
        state_path = root.require_regular_file("00_CONTROL/run-state.json")
        if state_path.stat().st_size > 1024 * 1024:
            raise OfficialAcquisitionScriptError("RESUME_STATE_INVALID")
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise OfficialAcquisitionScriptError("RESUME_STATE_INVALID") from exc
    if not isinstance(state, Mapping):
        raise OfficialAcquisitionScriptError("RESUME_STATE_INVALID")
    if state.get("repository_head") != guard_state["repository_head"]:
        raise OfficialAcquisitionScriptError("RESUME_WORKTREE_CONFLICT")
    old_digest = state.get("worktree_digest")
    new_digest = guard_state["worktree_digest"]
    if old_digest == new_digest:
        return
    if not isinstance(old_digest, str) or not isinstance(new_digest, str):
        raise OfficialAcquisitionScriptError("RESUME_WORKTREE_CONFLICT")
    relative = (
        "00_CONTROL/checkpoints/"
        f"worktree-repair-{old_digest[:16]}-{new_digest[:16]}.json"
    )
    try:
        receipt_path = root.require_regular_file(relative)
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise OfficialAcquisitionScriptError("RESUME_WORKTREE_CONFLICT") from exc
    if not isinstance(receipt, Mapping):
        raise OfficialAcquisitionScriptError("RESUME_WORKTREE_CONFLICT")
    if (
        receipt.get("schema_version") != "1.1.0"
        or receipt.get("reason") != COMPATIBLE_REPAIR_REASON
        or receipt.get("repository_head") != guard_state["repository_head"]
        or receipt.get("previous_worktree_digest") != old_digest
        or receipt.get("compatible_worktree_digest") != new_digest
        or receipt.get("changed_paths") != guard_state["dirty_paths"]
        or receipt.get("worktree_files")
        != guard_state["dirty_file_records"]
        or receipt.get("worktree_content_manifest_digest") != new_digest
        or canonical_sha256(receipt.get("worktree_files")) != new_digest
        or receipt.get("corpus_https_requests") != 0
        or receipt.get("download_writes") != 0
        or receipt.get("control_evidence_writes") != 1
        or receipt.get("orphan_parts_reconciled") is not False
        or receipt.get("repair_digest")
        != canonical_sha256(receipt, exclude_fields=("repair_digest",))
    ):
        raise OfficialAcquisitionScriptError("RESUME_WORKTREE_CONFLICT")
    prior_digest = receipt.get("prior_repair_digest")
    if not isinstance(prior_digest, str):
        raise OfficialAcquisitionScriptError("RESUME_WORKTREE_CONFLICT")
    _find_prior_repair_receipt(root, old_digest, prior_digest)


def _find_prior_repair_receipt(
    root: AcquisitionRootGuard,
    previous_worktree_digest: str,
    expected_repair_digest: str,
) -> Mapping[str, object]:
    checkpoint = root.resolve("00_CONTROL/checkpoints")
    matches: list[Mapping[str, object]] = []
    try:
        entries = sorted(checkpoint.iterdir(), key=lambda item: item.name)
    except OSError as exc:
        raise OfficialAcquisitionScriptError("PRIOR_REPAIR_INVALID") from exc
    if len(entries) > 10_000:
        raise OfficialAcquisitionScriptError("PRIOR_REPAIR_INVALID")
    for path in entries:
        if re.fullmatch(r"worktree-repair-[0-9a-f]{16}-[0-9a-f]{16}\.json", path.name) is None:
            continue
        try:
            metadata = path.lstat()
            if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
                raise OfficialAcquisitionScriptError("PRIOR_REPAIR_INVALID")
            if metadata.st_size > 1024 * 1024:
                raise OfficialAcquisitionScriptError("PRIOR_REPAIR_INVALID")
            candidate = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise OfficialAcquisitionScriptError("PRIOR_REPAIR_INVALID") from exc
        if not isinstance(candidate, Mapping):
            raise OfficialAcquisitionScriptError("PRIOR_REPAIR_INVALID")
        if candidate.get("repair_digest") != expected_repair_digest:
            continue
        if (
            candidate.get("previous_worktree_digest")
            != previous_worktree_digest
            or candidate.get("repair_digest")
            != canonical_sha256(
                candidate,
                exclude_fields=("repair_digest",),
            )
        ):
            raise OfficialAcquisitionScriptError("PRIOR_REPAIR_INVALID")
        matches.append(candidate)
    if len(matches) != 1:
        raise OfficialAcquisitionScriptError("PRIOR_REPAIR_INVALID")
    return matches[0]


def _record_compatible_worktree_repair(
    root: AcquisitionRootGuard,
    guard_state: Mapping[str, object],
    *,
    reason: str,
    confirm_previous_worktree_digest: str,
    confirm_compatible_worktree_digest: str,
    confirm_worktree_content_manifest_digest: str,
    confirm_prior_repair_digest: str,
) -> dict[str, object]:
    state_path = root.require_regular_file("00_CONTROL/run-state.json")
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise OfficialAcquisitionScriptError("RESUME_STATE_INVALID") from exc
    if not isinstance(state, Mapping):
        raise OfficialAcquisitionScriptError("RESUME_STATE_INVALID")
    old_digest = state.get("worktree_digest")
    new_digest = guard_state["worktree_digest"]
    worktree_files = guard_state.get("dirty_file_records")
    if (
        state.get("repository_head") != guard_state["repository_head"]
        or not isinstance(old_digest, str)
        or not isinstance(new_digest, str)
        or not isinstance(worktree_files, list)
        or old_digest == new_digest
        or reason != COMPATIBLE_REPAIR_REASON
        or confirm_previous_worktree_digest != old_digest
        or confirm_compatible_worktree_digest != new_digest
        or confirm_worktree_content_manifest_digest != new_digest
        or canonical_sha256(worktree_files) != new_digest
    ):
        raise OfficialAcquisitionScriptError("COMPATIBLE_REPAIR_NOT_CONFIRMED")
    _find_prior_repair_receipt(
        root,
        old_digest,
        confirm_prior_repair_digest,
    )
    receipt: dict[str, object] = {
        "schema_version": "1.1.0",
        "reason": reason,
        "repository_head": guard_state["repository_head"],
        "previous_worktree_digest": old_digest,
        "compatible_worktree_digest": new_digest,
        "changed_paths": guard_state["dirty_paths"],
        "worktree_files": worktree_files,
        "worktree_content_manifest_digest": new_digest,
        "prior_repair_digest": confirm_prior_repair_digest,
        "corpus_https_requests": 0,
        "download_writes": 0,
        "control_evidence_writes": 1,
        "existing_objects_modified": 0,
        "orphan_parts_reconciled": False,
        "seed_archive_modified": False,
        "s3_writes": 0,
        "database_writes": 0,
        "step15_started": False,
    }
    receipt["repair_digest"] = canonical_sha256(receipt)
    relative = (
        "00_CONTROL/checkpoints/"
        f"worktree-repair-{old_digest[:16]}-{new_digest[:16]}.json"
    )
    target = root.resolve(relative)
    payload = canonical_json_bytes(receipt) + b"\n"
    if target.exists():
        if root.require_regular_file(relative).read_bytes() != payload:
            raise OfficialAcquisitionScriptError("COMPATIBLE_REPAIR_CONFLICT")
    else:
        root.write_json_absent(relative, receipt)
    return receipt


def _execute(arguments: argparse.Namespace) -> dict[str, object]:
    guard_state = _repository_guard()
    policy = AcquisitionPolicy()
    root = AcquisitionRootGuard(
        repository_root=REPOSITORY_ROOT,
        policy=policy,
        config_path=DEFAULT_CONFIG,
    )
    root_exists = os.path.lexists(root.root)
    if arguments.initialize and root_exists:
        raise OfficialAcquisitionScriptError("ROOT_EXISTS_USE_RESUME")
    if (arguments.resume or arguments.record_compatible_repair) and not root_exists:
        raise OfficialAcquisitionScriptError("RESUME_ROOT_MISSING")

    started_at = _utc_now()
    run_material = {
        "repository_head": guard_state["repository_head"],
        "worktree_digest": guard_state["worktree_digest"],
        "mode": (
            "INITIALIZE"
            if arguments.initialize
            else "COMPATIBLE_REPAIR"
            if arguments.record_compatible_repair
            else "RESUME"
        ),
        "sources": list(arguments.sources),
        "maximum_runtime_seconds": arguments.max_runtime_seconds,
        "started_at": started_at,
    }
    run_id = "official-acquisition-" + canonical_sha256(run_material)[:24]
    created = root.initialize(
        {
            "schema_version": "1.0.0",
            "repository_head": guard_state["repository_head"],
            "worktree_digest": guard_state["worktree_digest"],
            "run_id": run_id,
            "selected_sources": [
                SOURCE_IDENTITIES[token] for token in arguments.sources
            ],
            "maximum_runtime_seconds": arguments.max_runtime_seconds,
            "started_at": started_at,
        },
        reconcile_orphan_parts=not arguments.record_compatible_repair,
    )
    if created != bool(arguments.initialize):
        raise OfficialAcquisitionScriptError("ACQUISITION_MODE_MISMATCH")
    if arguments.record_compatible_repair:
        receipt = _record_compatible_worktree_repair(
            root,
            guard_state,
            reason=arguments.repair_reason,
            confirm_previous_worktree_digest=(
                arguments.confirm_previous_worktree_digest
            ),
            confirm_compatible_worktree_digest=(
                arguments.confirm_compatible_worktree_digest
            ),
            confirm_worktree_content_manifest_digest=(
                arguments.confirm_worktree_content_manifest_digest
            ),
            confirm_prior_repair_digest=(
                arguments.confirm_prior_repair_digest
            ),
        )
        return {
            "schema_version": "1.0.0",
            "status": "COMPATIBLE_WORKTREE_REPAIR_RECORDED",
            "repair_digest": receipt["repair_digest"],
            "corpus_https_requests": 0,
            "download_writes": 0,
            "control_evidence_writes": 1,
            "s3_writes": 0,
            "database_writes": 0,
            "publication": 0,
            "step15_started": False,
        }
    if arguments.resume:
        _verify_resume_binding(root, guard_state)

    client = SafeHttpsClient(root)
    acquisition = OfficialGermanLawAcquisition(
        root,
        client,
        maximum_runtime_seconds=arguments.max_runtime_seconds,
    )
    acquisition.initialize_catalog_and_plans()
    results: dict[str, dict[str, object]] = {}
    resume_sources: list[str] = []
    runtime_started = time.monotonic()
    selected = tuple(arguments.sources)
    for index, token in enumerate(selected):
        source_id = SOURCE_IDENTITIES[token]
        if time.monotonic() - runtime_started >= arguments.max_runtime_seconds:
            resume_sources.extend(selected[index:])
            for pending in selected[index:]:
                results[SOURCE_IDENTITIES[pending]] = {
                    "status": SourceStatus.PARTIAL.value,
                    "reason": "GLOBAL_RUNTIME_BOUND_REACHED_BEFORE_SOURCE",
                }
            break
        try:
            result = _invoke_source(acquisition, token)
            if not isinstance(result, Mapping):
                raise OfficialAcquisitionScriptError(
                    "SOURCE_RESULT_MALFORMED"
                )
            result = dict(result)
        except (AcquisitionStorageError, AcquisitionIntegrityError):
            raise
        except AcquisitionError as exc:
            result = {
                "status": _classify_source_error(exc),
                "reason_code": exc.code,
            }
        status = result.get("status")
        if status not in {item.value for item in SourceStatus}:
            raise OfficialAcquisitionScriptError("SOURCE_RESULT_MALFORMED")
        results[source_id] = result
        if status in {SourceStatus.PARTIAL.value, SourceStatus.FAILED.value}:
            resume_sources.extend(selected[index:])
            for pending in selected[index + 1 :]:
                results[SOURCE_IDENTITIES[pending]] = {
                    "status": SourceStatus.PARTIAL.value,
                    "reason": "PREVIOUS_SOURCE_REQUIRES_SAFE_RESUME",
                }
            break

    reports = _write_reports(
        root=root,
        run_id=run_id,
        mode="INITIALIZE" if arguments.initialize else "RESUME",
        started_at=started_at,
        guard_state=guard_state,
        selected_sources=selected,
        maximum_runtime_seconds=arguments.max_runtime_seconds,
        results=results,
        resume_sources=tuple(resume_sources),
    )
    return {
        "schema_version": "1.0.0",
        "status": reports["status"],
        "run_id": run_id,
        "source_results": results,
        "safe_resume_command": reports["safe_resume_command"],
        "report_digests": reports["report_digests"],
        "system_drive_fallback_allowed": False,
        "s3_writes": 0,
        "database_writes": 0,
        "publication": 0,
        "step15_started": False,
    }


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _arguments(argv)
    try:
        result = _execute(arguments)
    except (OfficialAcquisitionScriptError, AcquisitionError) as exc:
        code = getattr(exc, "code", "ACQUISITION_FAILED")
        print(
            json.dumps(
                {
                    "schema_version": "1.0.0",
                    "status": "FAIL_CLOSED",
                    "reason_code": code,
                    "system_drive_fallback_allowed": False,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 1
    except Exception:
        print(
            json.dumps(
                {
                    "schema_version": "1.0.0",
                    "status": "FAIL_CLOSED",
                    "reason_code": "UNEXPECTED_ORCHESTRATION_FAILURE",
                    "system_drive_fallback_allowed": False,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
