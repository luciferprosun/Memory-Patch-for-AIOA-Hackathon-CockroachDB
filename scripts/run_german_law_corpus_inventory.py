#!/usr/bin/env python3
"""Plan or execute the approved, read-only-source Step 14 inventory."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src")]

from aioa_memory_kernel.contracts.serialization import (  # noqa: E402
    canonical_json_bytes,
    canonical_sha256,
    to_canonical_data,
)
from aioa_memory_kernel.corpus import (  # noqa: E402
    CorpusInventoryEngine,
    CorpusInventoryPolicy,
    verify_inventory_bundle,
)
from aioa_memory_kernel.storage import (  # noqa: E402
    EXTERNAL_VOLUME_EXPECTED_REMOTE,
    ExternalVolumeConfig,
    ExternalVolumeOperation,
    ExternalVolumeRuntimeAdapter,
    load_external_volume_environment,
)


EXPECTED_BRANCH = "main"
DEFAULT_CONFIG = ROOT / ".local/external-data.env"
OUTPUT_FILES = (
    "inventory-manifest.json",
    "inventory-summary.json",
    "file-records.jsonl",
    "path-aliases.jsonl",
    "exact-duplicate-groups.jsonl",
    "normalized-duplicate-groups.jsonl",
    "near-duplicate-candidates.jsonl",
    "source-registration-candidates.jsonl",
    "quarantine-candidates.jsonl",
    "license-assessments.jsonl",
    "privacy-assessments.jsonl",
    "checkpoints/completion.json",
)


class ScriptFailure(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__("Step 14 inventory orchestration failed")
        self.code = code


def _git(*arguments: str) -> bytes:
    try:
        result = subprocess.run(
            ["git", "-C", str(ROOT), *arguments],
            check=True,
            capture_output=True,
            timeout=30,
            env={
                "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
                "LANG": "C",
                "LC_ALL": "C",
            },
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ScriptFailure("REPOSITORY_GUARD_FAILED") from exc
    return result.stdout


def _allowed_dirty_path(relative: str) -> bool:
    fixed = {
        "AGENTS.md",
        "docs/README.md",
        "docs/roadmap/PRODUCTION_ROADMAP.md",
        "docs/adr/ADR-021-german-law-corpus-inventory-dedup-source-registration.md",
        "docs/architecture/GERMAN_LAW_CORPUS_INVENTORY_DEDUP_SOURCE_REGISTRATION_1A.md",
        "scripts/run_cockroachdb_migrations.py",
        "src/aioa_memory_kernel/storage/external_volume.py",
        "src/aioa_memory_kernel/german_law/__init__.py",
        "src/aioa_memory_kernel/german_law/corpus.py",
        "scripts/run_german_law_corpus_inventory.py",
        "scripts/run_german_law_corpus_registration_validation.py",
        "tests/test_corpus_inventory.py",
        "tests/test_step14_documentation.py",
    }
    return (
        relative in fixed
        or relative.startswith("src/aioa_memory_kernel/corpus/")
        or re.fullmatch(r"docs/(?:architecture|adr|operations|audits)/[^/]*STEP_14[^/]*\.(?:md|json)", relative, re.I) is not None
        or re.fullmatch(r"docs/evidence/corpus/step14-[a-z0-9-]+\.json", relative) is not None
    )


def _repository_guard() -> tuple[str, str, tuple[str, ...]]:
    if Path(_git("rev-parse", "--show-toplevel").decode().strip()) != ROOT:
        raise ScriptFailure("REPOSITORY_IDENTITY_MISMATCH")
    remote = _git("remote", "get-url", "origin").decode().strip().removesuffix(".git").rstrip("/")
    if remote != EXTERNAL_VOLUME_EXPECTED_REMOTE:
        raise ScriptFailure("REPOSITORY_REMOTE_MISMATCH")
    if _git("branch", "--show-current").decode().strip() != EXPECTED_BRANCH:
        raise ScriptFailure("REPOSITORY_BRANCH_MISMATCH")
    head = _git("rev-parse", "HEAD").decode().strip()
    origin = _git("rev-parse", "origin/main").decode().strip()
    if head != origin or _git("rev-list", "--left-right", "--count", "origin/main...HEAD").decode().split() != ["0", "0"]:
        raise ScriptFailure("REPOSITORY_DIVERGED")
    git_dir = Path(_git("rev-parse", "--git-dir").decode().strip())
    if not git_dir.is_absolute():
        git_dir = ROOT / git_dir
    if any((git_dir / marker).exists() for marker in ("MERGE_HEAD", "REBASE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "BISECT_LOG", "rebase-merge", "rebase-apply")):
        raise ScriptFailure("INTERRUPTED_GIT_OPERATION")
    raw = _git("status", "--porcelain=v1", "-z", "--untracked-files=all")
    dirty: list[str] = []
    for entry in raw.split(b"\0"):
        if not entry:
            continue
        decoded = entry.decode("utf-8", "strict")
        if len(decoded) < 4 or decoded[0] in "DR" or decoded[1] in "DR":
            raise ScriptFailure("DIRTY_SCOPE_UNSAFE")
        relative = decoded[3:]
        if not _allowed_dirty_path(relative):
            raise ScriptFailure("UNRELATED_WORKTREE_CHANGE")
        target = ROOT / relative
        metadata = target.lstat()
        if target.is_symlink() or not stat.S_ISREG(metadata.st_mode):
            raise ScriptFailure("DIRTY_SCOPE_UNSAFE")
        dirty.append(relative)
    records: list[dict[str, object]] = []
    for relative in sorted(dirty):
        target = ROOT / relative
        payload = target.read_bytes()
        records.append({"relative_path": relative, "byte_length": len(payload), "sha256": hashlib.sha256(payload).hexdigest()})
    return head, canonical_sha256(records), tuple(sorted(dirty))


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", action="store_true")
    mode.add_argument("--write-bundle", action="store_true")
    mode.add_argument("--verify-bundle", action="store_true")
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--confirm-device-reference")
    parser.add_argument("--confirm-source-root-identity")
    parser.add_argument("--confirm-plan-digest")
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    try:
        head, worktree_digest, dirty = _repository_guard()
        config = ExternalVolumeConfig.from_mapping(load_external_volume_environment(args.config))
        adapter = ExternalVolumeRuntimeAdapter(config)
        status = adapter.verify(require_write=args.write_bundle)
        source_root = args.source_root
        if not source_root.is_absolute() or os.path.normpath(str(source_root)) != str(source_root):
            raise ScriptFailure("UNSAFE_CORPUS_ROOT")
        try:
            if os.path.commonpath((source_root, config.mountpoint)) != str(config.mountpoint):
                raise ScriptFailure("CORPUS_ROOT_OUTSIDE_USB")
        except ValueError as exc:
            raise ScriptFailure("CORPUS_ROOT_OUTSIDE_USB") from exc
        operation_probe = adapter.resolve_path(
            ExternalVolumeOperation.CORPUS_MANIFEST,
            "step14-root-probe",
            require_write=args.write_bundle,
        )
        bundle_parent = operation_probe.parent / "step14"

        progress = lambda values: print(
            json.dumps({"progress": values}, sort_keys=True),
            file=sys.stderr,
            flush=True,
        )
        engine = CorpusInventoryEngine(
            source_root=source_root,
            bundle_parent=bundle_parent,
            device_reference=status.device_reference,
            starting_head=head,
            policy=CorpusInventoryPolicy(),
            progress=progress,
        )
        plan = engine.plan()
        plan_facts = {
            "schema_version": "1.0.0",
            "gate": "EXTERNAL_DERIVED_INVENTORY_WRITE_GATE_STEP14",
            "repository_head": head,
            "worktree_digest": worktree_digest,
            "dirty_files": dirty,
            "device_reference": status.device_reference,
            "source_root_identity_digest": plan.source_root_identity_digest,
            "source_tree_digest": plan.source_tree_digest,
            "source_directories": plan.directories,
            "source_objects": plan.objects,
            "source_regular_files": plan.regular_files,
            "source_bytes": plan.bytes_observed,
            "raw_corpus_planned_writes": 0,
            "raw_corpus_planned_deletes": 0,
            "raw_corpus_planned_moves": 0,
            "target_root_reference": f"corpora/manifests/step14/{plan.run_id}",
            "output_files": OUTPUT_FILES,
            "no_overwrite": True,
            "atomic_write": True,
            "estimated_output_bytes": plan.estimated_bundle_bytes,
            "policy_digest": engine.policy.policy_digest,
            "inventory_run_id": plan.run_id,
            "source_plan_digest": plan.plan_digest,
            "s3_writes": 0,
            "system_drive_fallback": False,
            "step15_work": 0,
        }
        plan_digest = canonical_sha256(plan_facts)
        plan_facts["plan_digest"] = plan_digest
        if args.plan:
            print((canonical_json_bytes(plan_facts) + b"\n").decode(), end="")
            return 0
        if args.confirm_device_reference != status.device_reference:
            raise ScriptFailure("DEVICE_CONFIRMATION_MISMATCH")
        if args.confirm_source_root_identity != plan.source_root_identity_digest:
            raise ScriptFailure("SOURCE_CONFIRMATION_MISMATCH")
        if args.confirm_plan_digest != plan_digest:
            raise ScriptFailure("PLAN_CONFIRMATION_MISMATCH")
        if args.verify_bundle:
            verification = verify_inventory_bundle(engine.bundle_root)
            print((canonical_json_bytes({"status": "PASS", "bundle": verification, "plan_digest": plan_digest}) + b"\n").decode(), end="")
            return 0
        summary, manifest = engine.execute(plan)
        verification = verify_inventory_bundle(engine.bundle_root)
        result = {
            "status": "PASS",
            "plan_digest": plan_digest,
            "summary": to_canonical_data(summary),
            "manifest_digest": manifest.manifest_digest,
            "bundle_verification": verification,
            "source_tree_writes": 0,
            "s3_writes": 0,
            "model_calls": 0,
            "step15_started": False,
        }
        print((canonical_json_bytes(result) + b"\n").decode(), end="")
        return 0
    except ScriptFailure as exc:
        print(json.dumps({"status": "FAIL_CLOSED", "sanitized_code": exc.code}, sort_keys=True), file=sys.stderr)
        return 1
    except Exception as exc:
        code = getattr(exc, "sanitized_code", "STEP14_INVENTORY_FAILED")
        print(json.dumps({"status": "FAIL_CLOSED", "sanitized_code": code, "exception_type": type(exc).__name__}, sort_keys=True), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
