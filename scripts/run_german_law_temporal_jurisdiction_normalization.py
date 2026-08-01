#!/usr/bin/env python3
"""Plan or execute the bounded, read-only-source Step 15 normalizer.

This command deliberately consumes the immutable Step-14 bundle instead of
rescanning a corpus into a competing inventory.  The only source reads are
hash-bound ``law_record.json`` metadata files enumerated by that bundle.
"""

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
from aioa_memory_kernel.german_law.normalization import (  # noqa: E402
    TemporalJurisdictionNormalizationEngine,
    TemporalJurisdictionNormalizationPolicy,
    verify_temporal_jurisdiction_bundle,
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
    "temporal-normalization.jsonl",
    "jurisdiction-normalization.jsonl",
    "document-versions.jsonl",
    "supersession-candidates.jsonl",
    "normalization-conflicts.jsonl",
    "source-registry-normalization-proposals.jsonl",
    "run-summary.json",
    "checkpoints/completion.json",
    "artifact-manifest.json",
)


class ScriptFailure(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__("Step 15 normalization orchestration failed")
        self.code = code


def _git(*arguments: str) -> bytes:
    try:
        result = subprocess.run(
            ["git", "-C", str(ROOT), *arguments],
            stdin=subprocess.DEVNULL,
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
        "src/aioa_memory_kernel/german_law/__init__.py",
        "src/aioa_memory_kernel/german_law/normalization.py",
        "scripts/run_german_law_temporal_jurisdiction_normalization.py",
        "scripts/run_german_law_temporal_jurisdiction_validation.py",
        "tests/test_german_law_temporal_jurisdiction_normalization.py",
        "tests/test_german_law_temporal_jurisdiction_scripts.py",
        "tests/test_step15_documentation.py",
        "docs/architecture/GERMAN_LAW_TEMPORAL_JURISDICTIONAL_NORMALIZATION_1A.md",
        "docs/adr/ADR-022-german-law-temporal-jurisdictional-normalization.md",
        "docs/operations/STEP_15_GERMAN_LAW_TEMPORAL_JURISDICTIONAL_VALIDATION_1A.md",
    }
    return (
        relative in fixed
        or re.fullmatch(r"docs/(?:architecture|adr|operations|audits)/[^/]*STEP_15[^/]*\.md", relative, re.I) is not None
        or relative == "docs/adr/ADR-022-german-law-temporal-jurisdictional-normalization.md"
        or re.fullmatch(r"docs/evidence/corpus/step15-[a-z0-9-]+\.json", relative) is not None
    )


def repository_guard() -> tuple[str, str, tuple[str, ...]]:
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
    for item in raw.split(b"\0"):
        if not item:
            continue
        decoded = item.decode("utf-8", "strict")
        if len(decoded) < 4 or decoded[0] in "DR" or decoded[1] in "DR":
            raise ScriptFailure("DIRTY_SCOPE_UNSAFE")
        relative = decoded[3:]
        if not _allowed_dirty_path(relative):
            raise ScriptFailure("UNRELATED_WORKTREE_CHANGE")
        target = ROOT / relative
        metadata = target.lstat()
        if target.is_symlink() or not stat.S_ISREG(metadata.st_mode):
            raise ScriptFailure("DIRTY_SCOPE_UNSAFE")
        payload = target.read_bytes()
        dirty.append(relative)
    worktree = [
        {
            "relative_path": relative,
            "byte_length": (ROOT / relative).stat().st_size,
            "sha256": hashlib.sha256((ROOT / relative).read_bytes()).hexdigest(),
        }
        for relative in sorted(dirty)
    ]
    return head, canonical_sha256(worktree), tuple(sorted(dirty))


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", action="store_true")
    mode.add_argument("--write-bundle", action="store_true")
    mode.add_argument("--verify-bundle", action="store_true")
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--step14-bundle-root", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--confirm-device-reference")
    parser.add_argument("--confirm-source-root-identity")
    parser.add_argument("--confirm-step14-manifest-digest")
    parser.add_argument("--confirm-plan-digest")
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    try:
        head, worktree_digest, dirty = repository_guard()
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
            "step15-root-probe",
            require_write=args.write_bundle,
        )
        manifest_parent = operation_probe.parent
        step14_parent = manifest_parent / "step14"
        step15_parent = manifest_parent / "step15"
        step14_bundle = args.step14_bundle_root
        try:
            resolved_step14 = step14_bundle.resolve(strict=True)
        except OSError as exc:
            raise ScriptFailure("STEP14_BUNDLE_UNAVAILABLE") from exc
        if (
            step14_bundle.is_symlink()
            or resolved_step14.parent != step14_parent.resolve(strict=True)
            or not resolved_step14.name.startswith("step14-")
        ):
            raise ScriptFailure("STEP14_BUNDLE_OUTSIDE_APPROVED_BOUNDARY")
        progress = lambda values: print(json.dumps({"progress": values}, sort_keys=True), file=sys.stderr, flush=True)
        engine = TemporalJurisdictionNormalizationEngine(
            source_root=source_root,
            step14_bundle_root=resolved_step14,
            bundle_parent=step15_parent,
            device_reference=status.device_reference,
            starting_head=head,
            policy=TemporalJurisdictionNormalizationPolicy(),
            progress=progress,
        )
        plan = engine.plan()
        plan_facts = {
            "schema_version": "1.0.0",
            "gate": "EXTERNAL_DERIVED_NORMALIZATION_WRITE_GATE_STEP15",
            "repository_head": head,
            "worktree_digest": worktree_digest,
            "dirty_files": dirty,
            "device_reference": status.device_reference,
            "source_root_identity_digest": plan.source_root_identity_digest,
            "source_tree_digest": plan.source_tree_digest,
            "step14_manifest_digest": plan.step14_manifest_digest,
            "step14_metadata_records": plan.metadata_records,
            "raw_corpus_planned_writes": 0,
            "raw_corpus_planned_deletes": 0,
            "raw_corpus_planned_moves": 0,
            "target_root_reference": f"corpora/manifests/step15/{plan.run_id}",
            "output_files": OUTPUT_FILES,
            "no_overwrite": True,
            "atomic_write": True,
            "estimated_output_bytes": plan.estimated_output_bytes,
            "policy_digest": plan.policy_digest,
            "normalization_run_id": plan.run_id,
            "source_plan_digest": plan.plan_digest,
            "aws_writes": 0,
            "s3_writes": 0,
            "network_acquisitions": 0,
            "model_calls": 0,
            "publication_transitions": 0,
            "step16_work": 0,
            "system_drive_fallback": False,
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
        if args.confirm_step14_manifest_digest != plan.step14_manifest_digest:
            raise ScriptFailure("STEP14_MANIFEST_CONFIRMATION_MISMATCH")
        if args.confirm_plan_digest != plan_digest:
            raise ScriptFailure("PLAN_CONFIRMATION_MISMATCH")
        if args.verify_bundle:
            if engine.bundle_root is None:
                raise ScriptFailure("STEP15_PLAN_MISSING_OUTPUT_ROOT")
            verification = verify_temporal_jurisdiction_bundle(engine.bundle_root)
            print((canonical_json_bytes({"status": "PASS", "bundle": verification, "plan_digest": plan_digest}) + b"\n").decode(), end="")
            return 0
        result = engine.execute(plan)
        verification = verify_temporal_jurisdiction_bundle(engine.bundle_root) if engine.bundle_root is not None else {"status": "FAIL"}
        payload = {
            "status": "PASS",
            "plan_digest": plan_digest,
            "summary": to_canonical_data(result.summary),
            "manifest_digest": result.manifest.manifest_digest,
            "bundle_verification": verification,
            "source_tree_writes": 0,
            "aws_writes": 0,
            "s3_writes": 0,
            "network_acquisitions": 0,
            "model_calls": 0,
            "publication_transitions": 0,
            "step16_started": False,
        }
        print((canonical_json_bytes(payload) + b"\n").decode(), end="")
        return 0
    except ScriptFailure as exc:
        print(json.dumps({"status": "FAIL_CLOSED", "sanitized_code": exc.code}, sort_keys=True), file=sys.stderr)
        return 1
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "FAIL_CLOSED",
                    "sanitized_code": getattr(exc, "sanitized_code", "STEP15_NORMALIZATION_FAILED"),
                    "exception_type": type(exc).__name__,
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
