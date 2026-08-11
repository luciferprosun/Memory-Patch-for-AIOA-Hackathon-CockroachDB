#!/usr/bin/env python3
"""Plan, execute, or verify the gated Step 16 publication boundary.

The runner has no import-time I/O.  ``--plan`` performs only repository and
external-volume reads.  ``--write-publication`` requires exact plan-bound
confirmations, rechecks the approved S3 capabilities, and delegates every
object write to the existing Step 7 Object-Lock adapter.  It never discovers
or writes arbitrary paths, and it never selects a legal question.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT), str(ROOT / "src")]

from aioa_memory_kernel.contracts.serialization import (  # noqa: E402
    canonical_json_bytes,
    canonical_sha256,
    to_canonical_data,
)
from aioa_memory_kernel.german_law.publication import (  # noqa: E402
    GermanLawPublicationEngine,
    Step16PublicationPolicy,
    verify_german_law_publication_bundle,
)
from aioa_memory_kernel.runtime import LinuxExternalVolumeProbe  # noqa: E402
from aioa_memory_kernel.security.credentials import (  # noqa: E402
    AWS_WORKLOAD_IDENTITY_ENVIRONMENT_NAMES,
    build_minimal_subprocess_environment,
)
from aioa_memory_kernel.storage import (  # noqa: E402
    EXTERNAL_VOLUME_EXPECTED_REMOTE,
    ExternalVolumeConfig,
    ExternalVolumeOperation,
    ExternalVolumeRuntimeAdapter,
    S3ObjectLockMode,
    S3SnapshotAdapter,
    S3SnapshotConfig,
    SnapshotServiceUnavailableError,
    load_external_volume_environment,
)
from aws_cli_s3_client import AwsCliS3Client  # noqa: E402


EXPECTED_BRANCH = "main"
AWS_PROFILE = "aoia-admin"
AWS_REGION = "eu-central-1"
S3_BUCKET = "aioa-memory-patch-global-3f105fcd-eu-central-1"
S3_PREFIX = "memory-patch/snapshots/v1"
S3_RETENTION_DAYS = 7
AWS_CLI_SHA256 = "cf8592f6d5830307168c6397158b8263566471fac00dfc5028fb8a540ddd8332"
DEFAULT_CONFIG = ROOT / ".local" / "external-data.env"
_ASSUMED_ROLE = re.compile(r"^arn:[a-z0-9-]+:sts::[0-9]{12}:assumed-role/[^/]+/[^/]+$")

OUTPUT_FILES = (
    "publication-eligibility.jsonl",
    "publication-items.jsonl",
    "publication-exclusions.jsonl",
    "snapshot-bindings.jsonl",
    "provenance-chains.jsonl",
    "parser-coverage.jsonl",
    "chunk-coverage.jsonl",
    "temporal-validation.jsonl",
    "jurisdiction-validation.jsonl",
    "publication-conflicts.jsonl",
    "run-summary.json",
    "publication-batch.json",
    "checkpoints/completion.json",
    "artifact-manifest.json",
)
_SNAPSHOT_RETRY_DELAYS_SECONDS = (1.0, 2.0, 4.0)


class ScriptFailure(RuntimeError):
    """A sanitized Step 16 orchestration failure."""

    def __init__(self, code: str) -> None:
        super().__init__("Step 16 publication orchestration failed")
        self.code = code


class _BoundedSnapshotWriter:
    """One isolated existing Step 7 adapter per bounded worker thread.

    The publication engine owns ordering and its SQLite interruption spool.  A
    worker receives only one immutable snapshot envelope at a time and uses the
    same exact-byte, conditional-write, Object-Lock adapter as the serial path.
    Separate narrow CLI clients prevent shared mutable request counters or
    temporary paths while retaining a fixed maximum of four in-flight bodies.
    """

    def __init__(
        self,
        *,
        aws_binary: Path,
        temporary_root: Path,
        max_workers: int,
    ) -> None:
        if not isinstance(max_workers, int) or isinstance(max_workers, bool) or not 1 <= max_workers <= 4:
            raise ValueError("Step 16 snapshot worker count is outside its fixed bound")
        if temporary_root.is_symlink() or not temporary_root.is_dir():
            raise ScriptFailure("UNSAFE_S3_TEMPORARY_ROOT")
        self._aws_binary = aws_binary
        self._temporary_root = temporary_root
        self._max_workers = max_workers
        self._local = threading.local()
        self._lock = threading.Lock()
        self._states: list[dict[str, object]] = []

    def _state(self) -> dict[str, object]:
        state = getattr(self._local, "state", None)
        if state is not None:
            return state
        worker_directory = Path(
            tempfile.mkdtemp(prefix="worker-", dir=self._temporary_root)
        )
        if (
            worker_directory.parent != self._temporary_root
            or worker_directory.is_symlink()
            or not worker_directory.is_dir()
        ):
            raise ScriptFailure("UNSAFE_S3_WORKER_DIRECTORY")
        client = AwsCliS3Client(
            aws_binary=self._aws_binary,
            profile=AWS_PROFILE,
            region=AWS_REGION,
            temporary_directory=worker_directory,
            expected_binary_sha256=AWS_CLI_SHA256,
            timeout_seconds=180,
        )
        storage = S3SnapshotAdapter(
            S3SnapshotConfig(
                region=AWS_REGION,
                bucket_name=S3_BUCKET,
                retention_mode=S3ObjectLockMode.GOVERNANCE,
                retention_days=S3_RETENTION_DAYS,
                key_prefix=S3_PREFIX,
            ),
            client,
        )
        state = {
            "client": client,
            "storage": storage,
            "capabilities": storage.inspect_bucket_capabilities(),
            "write_count": 0,
        }
        self._local.state = state
        with self._lock:
            if len(self._states) >= self._max_workers:
                raise ScriptFailure("S3_WORKER_BOUND_EXCEEDED")
            self._states.append(state)
        return state

    def __call__(self, snapshot):
        state = self._state()
        storage = state["storage"]
        write_count = state["write_count"]
        if not isinstance(storage, S3SnapshotAdapter) or not isinstance(write_count, int):
            raise ScriptFailure("S3_WORKER_STATE_INVALID")
        if write_count and write_count % 64 == 0:
            state["capabilities"] = storage.inspect_bucket_capabilities()
        for attempt in range(len(_SNAPSHOT_RETRY_DELAYS_SECONDS) + 1):
            try:
                evidence = storage.persist_snapshot_after_capability_validation(
                    snapshot,
                    state["capabilities"],
                )
                break
            except SnapshotServiceUnavailableError:
                if attempt == len(_SNAPSHOT_RETRY_DELAYS_SECONDS):
                    raise
                # The existing adapter performs a conditional create and exact
                # replay verification.  Retrying only its typed unavailable
                # failure cannot overwrite a potentially successful first put.
                time.sleep(_SNAPSHOT_RETRY_DELAYS_SECONDS[attempt])
        state["write_count"] = write_count + 1
        return evidence

    @property
    def operation_counts(self) -> Mapping[str, int]:
        totals: dict[str, int] = {}
        with self._lock:
            states = tuple(self._states)
        for state in states:
            client = state.get("client")
            if not isinstance(client, AwsCliS3Client):
                raise ScriptFailure("S3_WORKER_STATE_INVALID")
            for operation, count in client.operation_counts.items():
                totals[operation] = totals.get(operation, 0) + count
        return dict(sorted(totals.items()))


def _git(*arguments: str) -> bytes:
    try:
        result = subprocess.run(
            ["git", "-C", str(ROOT), *arguments],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
            timeout=120,
            env={"PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"), "LANG": "C", "LC_ALL": "C"},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ScriptFailure("REPOSITORY_GUARD_FAILED") from exc
    return result.stdout


def _allowed_dirty_path(relative: str) -> bool:
    fixed = {
        "AGENTS.md",
        "docs/README.md",
        "docs/roadmap/PRODUCTION_ROADMAP.md",
        "src/aioa_memory_kernel/german_law/__init__.py",
        "src/aioa_memory_kernel/german_law/publication.py",
        "src/aioa_memory_kernel/storage/s3.py",
        "scripts/aws_cli_s3_client.py",
        "scripts/run_german_law_publication.py",
        "scripts/run_german_law_publication_validation.py",
        "tests/test_german_law_publication.py",
        "tests/test_german_law_publication_scripts.py",
        "tests/test_aws_cli_s3_client.py",
        "tests/test_s3_snapshot_storage.py",
        "tests/test_step16_documentation.py",
        "docs/architecture/GERMAN_LAW_HAT_PUBLICATION_CORPUS_VERIFICATION_1A.md",
        "docs/operations/STEP_16_GERMAN_LAW_HAT_PUBLICATION_VALIDATION_1A.md",
    }
    return relative in fixed or re.fullmatch(r"docs/(?:architecture|adr|operations|audits)/[^/]*STEP_16[^/]*\.md", relative, re.I) is not None or re.fullmatch(r"docs/evidence/corpus/step16-[a-z0-9-]+\.json", relative) is not None or relative == "docs/adr/ADR-023-german-law-hat-publication-corpus-verification.md"


def repository_guard() -> tuple[str, str, tuple[str, ...]]:
    if Path(_git("rev-parse", "--show-toplevel").decode().strip()) != ROOT:
        raise ScriptFailure("REPOSITORY_IDENTITY_MISMATCH")
    remote = _git("remote", "get-url", "origin").decode().strip().removesuffix(".git").rstrip("/")
    if remote != EXTERNAL_VOLUME_EXPECTED_REMOTE:
        raise ScriptFailure("REPOSITORY_REMOTE_MISMATCH")
    if _git("branch", "--show-current").decode().strip() != EXPECTED_BRANCH:
        raise ScriptFailure("REPOSITORY_BRANCH_MISMATCH")
    _git("fetch", "origin", "--prune")
    head = _git("rev-parse", "HEAD").decode().strip()
    origin = _git("rev-parse", "origin/main").decode().strip()
    if head != origin or _git("rev-list", "--left-right", "--count", "origin/main...HEAD").decode().split() != ["0", "0"]:
        raise ScriptFailure("REPOSITORY_DIVERGED")
    git_dir = Path(_git("rev-parse", "--git-dir").decode().strip())
    if not git_dir.is_absolute():
        git_dir = ROOT / git_dir
    if any((git_dir / marker).exists() for marker in ("MERGE_HEAD", "REBASE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "BISECT_LOG", "rebase-merge", "rebase-apply")):
        raise ScriptFailure("INTERRUPTED_GIT_OPERATION")
    dirty: list[str] = []
    for value in _git("status", "--porcelain=v1", "-z", "--untracked-files=all").split(b"\0"):
        if not value:
            continue
        item = value.decode("utf-8", "strict")
        if len(item) < 4 or item[:2].strip() in {"D", "R"}:
            raise ScriptFailure("DIRTY_SCOPE_UNSAFE")
        relative = item[3:]
        if not _allowed_dirty_path(relative):
            raise ScriptFailure("UNRELATED_WORKTREE_CHANGE")
        target = ROOT / relative
        metadata = target.lstat()
        if target.is_symlink() or not stat.S_ISREG(metadata.st_mode):
            raise ScriptFailure("DIRTY_SCOPE_UNSAFE")
        dirty.append(relative)
    records = [
        {"relative_path": path, "byte_length": (ROOT / path).stat().st_size, "sha256": hashlib.sha256((ROOT / path).read_bytes()).hexdigest()}
        for path in sorted(dirty)
    ]
    return head, canonical_sha256(records), tuple(sorted(dirty))


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", action="store_true")
    mode.add_argument("--write-publication", action="store_true")
    mode.add_argument("--verify-bundle", action="store_true")
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--step14-bundle-root", type=Path, required=True)
    parser.add_argument("--step15-bundle-root", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--confirm-device-reference")
    parser.add_argument("--confirm-source-root-identity")
    parser.add_argument("--confirm-step14-manifest-digest")
    parser.add_argument("--confirm-step15-manifest-digest")
    parser.add_argument("--confirm-plan-digest")
    return parser.parse_args()


def _external_adapter(config_path: Path) -> tuple[ExternalVolumeRuntimeAdapter, ExternalVolumeConfig]:
    config = ExternalVolumeConfig.from_mapping(load_external_volume_environment(config_path))
    return ExternalVolumeRuntimeAdapter(config, probe=LinuxExternalVolumeProbe()), config


def _aws_binary() -> Path:
    raw = shutil.which("aws")
    if raw is None:
        raise ScriptFailure("AWS_CLI_UNAVAILABLE")
    path = Path(raw).resolve(strict=True)
    if path.is_symlink() or not path.is_file() or not os.access(path, os.X_OK):
        raise ScriptFailure("AWS_CLI_UNSAFE")
    if hashlib.sha256(path.read_bytes()).hexdigest() != AWS_CLI_SHA256:
        raise ScriptFailure("AWS_CLI_DIGEST_MISMATCH")
    return path


def _aws_json(binary: Path, service: str, operation: str) -> Mapping[str, Any]:
    environment = build_minimal_subprocess_environment(
        os.environ,
        allowed_names=AWS_WORKLOAD_IDENTITY_ENVIRONMENT_NAMES,
    )
    environment["AWS_PAGER"] = ""
    try:
        completed = subprocess.run(
            [str(binary), service, operation, "--profile", AWS_PROFILE, "--region", AWS_REGION, "--no-cli-pager", "--output", "json"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            timeout=60,
            env=environment,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ScriptFailure("AWS_IDENTITY_PREFLIGHT_FAILED") from exc
    if completed.returncode != 0:
        raise ScriptFailure("AWS_IDENTITY_PREFLIGHT_FAILED")
    try:
        value = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise ScriptFailure("AWS_IDENTITY_PREFLIGHT_FAILED") from exc
    if not isinstance(value, Mapping):
        raise ScriptFailure("AWS_IDENTITY_PREFLIGHT_FAILED")
    return value


def _aws_identity(binary: Path) -> Mapping[str, Any]:
    identity = _aws_json(binary, "sts", "get-caller-identity")
    arn = identity.get("Arn")
    if not isinstance(arn, str) or _ASSUMED_ROLE.fullmatch(arn) is None:
        raise ScriptFailure("AWS_IDENTITY_NOT_APPROVED_TEMPORARY_ROLE")
    return {"profile": AWS_PROFILE, "region": AWS_REGION, "temporary_assumed_role": True, "root_principal": False, "sensitive_identifiers_redacted": True}


def _validate_input_bundle(path: Path, *, expected_parent: Path, prefix: str, code: str) -> Path:
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise ScriptFailure(code) from exc
    if path.is_symlink() or resolved.parent != expected_parent.resolve(strict=True) or not resolved.name.startswith(prefix):
        raise ScriptFailure(code)
    return resolved


def main() -> int:
    args = _arguments()
    try:
        head, worktree_digest, dirty = repository_guard()
        adapter, external_config = _external_adapter(args.config)
        volume = adapter.verify(require_write=args.write_publication)
        source_root = args.source_root
        if not source_root.is_absolute() or os.path.normpath(str(source_root)) != str(source_root):
            raise ScriptFailure("UNSAFE_CORPUS_ROOT")
        try:
            if os.path.commonpath((source_root, external_config.mountpoint)) != str(external_config.mountpoint):
                raise ScriptFailure("CORPUS_ROOT_OUTSIDE_APPROVED_USB")
        except ValueError as exc:
            raise ScriptFailure("CORPUS_ROOT_OUTSIDE_APPROVED_USB") from exc
        operation_probe = adapter.resolve_path(ExternalVolumeOperation.CORPUS_MANIFEST, "step16-root-probe", require_write=args.write_publication)
        manifest_parent = operation_probe.parent
        step14 = _validate_input_bundle(args.step14_bundle_root, expected_parent=manifest_parent / "step14", prefix="step14-", code="STEP14_BUNDLE_OUTSIDE_APPROVED_BOUNDARY")
        step15 = _validate_input_bundle(args.step15_bundle_root, expected_parent=manifest_parent / "step15", prefix="step15-", code="STEP15_BUNDLE_OUTSIDE_APPROVED_BOUNDARY")
        engine = GermanLawPublicationEngine(
            source_root=source_root,
            step14_bundle_root=step14,
            step15_bundle_root=step15,
            bundle_parent=manifest_parent / "step16",
            device_reference=volume.device_reference,
            starting_head=head,
            policy=Step16PublicationPolicy(max_snapshot_workers=4),
            progress=lambda values: print(json.dumps({"progress": dict(values)}, sort_keys=True), file=sys.stderr, flush=True),
        )
        plan = engine.plan()
        gate = {
            "schema_version": "1.0.0",
            "gate": "TRUSTED_PUBLICATION_WRITE_GATE_STEP16",
            "repository_head": head,
            "worktree_digest": worktree_digest,
            "dirty_files": list(dirty),
            "device_reference": volume.device_reference,
            "source_root_identity_digest": plan.source_root_identity_digest,
            "source_tree_digest": plan.source_tree_digest,
            "step14_manifest_digest": plan.step14_manifest_digest,
            "step15_manifest_digest": plan.step15_manifest_digest,
            "target_root_reference": f"corpora/manifests/step16/{plan.run_id}",
            "output_files": list(OUTPUT_FILES),
            "candidate_count": plan.candidate_count,
            "static_eligible_precheck_count": plan.static_eligible_precheck_count,
            "eligible_precheck_count": plan.eligible_precheck_count,
            "projection_preflight_exclusion_counts": dict(plan.projection_preflight_reason_counts),
            "projection_preflight_digest": plan.projection_preflight_digest,
            "estimated_output_bytes": plan.estimated_output_bytes,
            "policy_digest": plan.policy_digest,
            "source_plan_digest": plan.plan_digest,
            "raw_corpus_writes": 0,
            "raw_corpus_deletes": 0,
            "raw_corpus_moves": 0,
            "s3_writes_required_for_eligible_candidates": plan.eligible_precheck_count * 2,
            "s3_deletes": 0,
            "aws_writes_outside_approved_s3_boundary": 0,
            "database_writes": "DISPOSABLE_VALIDATION_ONLY",
            "model_calls": 0,
            "network_acquisitions": 0,
            "step17_work": 0,
            "system_drive_fallback": False,
            "no_overwrite": True,
            "atomic_write": True,
        }
        plan_digest = canonical_sha256(gate)
        gate["plan_digest"] = plan_digest
        if args.plan:
            print((canonical_json_bytes(gate) + b"\n").decode(), end="")
            return 0
        required = {
            "device": volume.device_reference,
            "source": plan.source_root_identity_digest,
            "step14": plan.step14_manifest_digest,
            "step15": plan.step15_manifest_digest,
            "plan": plan_digest,
        }
        provided = {
            "device": args.confirm_device_reference,
            "source": args.confirm_source_root_identity,
            "step14": args.confirm_step14_manifest_digest,
            "step15": args.confirm_step15_manifest_digest,
            "plan": args.confirm_plan_digest,
        }
        if any(provided[key] != value for key, value in required.items()):
            raise ScriptFailure("WRITE_CONFIRMATION_MISMATCH")
        if args.verify_bundle:
            if engine.bundle_root is None:
                raise ScriptFailure("STEP16_OUTPUT_ROOT_UNAVAILABLE")
            print((canonical_json_bytes({"status": "PASS", "plan_digest": plan_digest, "bundle": verify_german_law_publication_bundle(engine.bundle_root)}) + b"\n").decode(), end="")
            return 0
        binary = _aws_binary()
        identity = _aws_identity(binary)
        with tempfile.TemporaryDirectory(prefix="memory-patch-step16-s3-") as temp:
            snapshot_writer = _BoundedSnapshotWriter(
                aws_binary=binary,
                temporary_root=Path(temp),
                max_workers=engine.policy.max_snapshot_workers,
            )
            result = engine.execute(plan, snapshot_writer=snapshot_writer)
        payload = {
            "status": "PASS",
            "plan_digest": plan_digest,
            "summary": to_canonical_data(result.summary),
            "manifest_digest": result.manifest["manifest_digest"],
            "bundle_verification": result.verification,
            "aws_identity": identity,
            "s3_operation_counts": dict(snapshot_writer.operation_counts),
            "source_tree_writes": 0,
            "s3_deletes": 0,
            "model_calls": 0,
            "step17_started": False,
        }
        print((canonical_json_bytes(payload) + b"\n").decode(), end="")
        return 0
    except ScriptFailure as exc:
        print(json.dumps({"status": "FAIL_CLOSED", "sanitized_code": exc.code}, sort_keys=True), file=sys.stderr)
        return 1
    except Exception as exc:
        print(json.dumps({"status": "FAIL_CLOSED", "sanitized_code": getattr(exc, "sanitized_code", "STEP16_PUBLICATION_FAILED"), "exception_type": type(exc).__name__}, sort_keys=True), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
