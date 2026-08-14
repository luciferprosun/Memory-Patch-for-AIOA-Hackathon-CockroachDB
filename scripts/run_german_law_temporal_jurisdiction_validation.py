#!/usr/bin/env python3
"""Controlled Step 15 source-registry compatibility validation.

The durable Step-15 result is its hash-bound external normalization bundle.
This disposable CockroachDB run proves that its review-only source-registry
proposals remain compatible with the existing Step-9 boundary, replay safely,
and cannot publish a source.  It does not store corpus bodies or create a
second temporal authority table.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import subprocess
import sys
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator, Mapping


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]

import run_cockroachdb_migrations as migrations  # noqa: E402
import run_german_law_corpus_registration_validation as step14_validation  # noqa: E402
import run_source_registry_validation as step9_validation  # noqa: E402
from aioa_memory_kernel.contracts.serialization import (  # noqa: E402
    canonical_json_bytes,
    canonical_sha256,
)
from aioa_memory_kernel.corpus import verify_inventory_bundle  # noqa: E402
from aioa_memory_kernel.german_law.corpus import (  # noqa: E402
    STEP14_TENANT_ID,
    build_source_registry_record,
)
from aioa_memory_kernel.german_law.normalization import (  # noqa: E402
    verify_temporal_jurisdiction_bundle,
)
from aioa_memory_kernel.sources.models import (  # noqa: E402
    SourceAuthorityAssessment,
    SourceAuthorityLevel,
)
from aioa_memory_kernel.storage import (  # noqa: E402
    EXTERNAL_VOLUME_EXPECTED_REMOTE,
    ExternalVolumeConfig,
    ExternalVolumeOperation,
    ExternalVolumeRuntimeAdapter,
    load_external_volume_environment,
)


DEFAULT_CONFIG = ROOT / ".local/external-data.env"
EXPECTED_BRANCH = "main"
EXPECTED_BINARY_SHA256 = "5ad89c804abb3bf5afa9c073faecb3710a1c4f34a870f08cdef889c1c91d314b"
EVIDENCE_SCHEMA_VERSION = "1.0.0"


class ValidationFailure(RuntimeError):
    """Sanitized Step-15 controlled-validation failure."""

    def __init__(self, code: str) -> None:
        super().__init__("Step 15 controlled validation failed")
        self.code = code


def _git(*arguments: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(ROOT), *arguments],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=True,
            timeout=30,
            env={"PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"), "LANG": "C", "LC_ALL": "C"},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValidationFailure("REPOSITORY_GUARD_FAILED") from exc
    return result.stdout.decode("utf-8", "strict").strip()


def _allowed_dirty_path(relative: str) -> bool:
    fixed = {
        "AGENTS.md",
        "docs/README.md",
        "docs/roadmap/PRODUCTION_ROADMAP.md",
        "src/aioa_memory_kernel/german_law/__init__.py",
        "src/aioa_memory_kernel/german_law/normalization.py",
        "scripts/run_german_law_temporal_jurisdiction_normalization.py",
        "scripts/run_german_law_temporal_jurisdiction_validation.py",
        "scripts/run_cockroachdb_migrations.py",
        "tests/test_german_law_temporal_jurisdiction_normalization.py",
        "tests/test_german_law_temporal_jurisdiction_scripts.py",
        "tests/test_step15_documentation.py",
    }
    return (
        relative in fixed
        or relative == "docs/adr/ADR-022-german-law-temporal-jurisdictional-normalization.md"
        or relative.startswith("docs/architecture/GERMAN_LAW_TEMPORAL_")
        or relative.startswith("docs/operations/STEP_15_")
        or relative.startswith("docs/audits/STEP_15_")
        or relative.startswith("docs/evidence/corpus/step15-")
    )


def repository_guard() -> tuple[str, str]:
    if Path(_git("rev-parse", "--show-toplevel")) != ROOT:
        raise ValidationFailure("REPOSITORY_IDENTITY_MISMATCH")
    remote = _git("remote", "get-url", "origin").removesuffix(".git").rstrip("/")
    if remote != EXTERNAL_VOLUME_EXPECTED_REMOTE:
        raise ValidationFailure("REPOSITORY_REMOTE_MISMATCH")
    if _git("branch", "--show-current") != EXPECTED_BRANCH:
        raise ValidationFailure("REPOSITORY_BRANCH_MISMATCH")
    head = _git("rev-parse", "HEAD")
    if head != _git("rev-parse", "origin/main") or _git("rev-list", "--left-right", "--count", "origin/main...HEAD").split() != ["0", "0"]:
        raise ValidationFailure("REPOSITORY_DIVERGED")
    git_dir = Path(_git("rev-parse", "--git-dir"))
    if not git_dir.is_absolute():
        git_dir = ROOT / git_dir
    if any((git_dir / marker).exists() for marker in ("MERGE_HEAD", "REBASE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "BISECT_LOG", "rebase-merge", "rebase-apply")):
        raise ValidationFailure("INTERRUPTED_GIT_OPERATION")
    status = subprocess.run(
        ["git", "-C", str(ROOT), "status", "--porcelain=v1", "-z", "--untracked-files=all"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=True,
        timeout=30,
    ).stdout
    records: list[dict[str, object]] = []
    for item in status.split(b"\0"):
        if not item:
            continue
        decoded = item.decode("utf-8", "strict")
        if len(decoded) < 4 or decoded[0] in "DR" or decoded[1] in "DR":
            raise ValidationFailure("DIRTY_SCOPE_UNSAFE")
        relative = decoded[3:]
        if not _allowed_dirty_path(relative):
            raise ValidationFailure("UNRELATED_WORKTREE_CHANGE")
        target = ROOT / relative
        metadata = target.lstat()
        if target.is_symlink() or not stat.S_ISREG(metadata.st_mode):
            raise ValidationFailure("DIRTY_SCOPE_UNSAFE")
        payload = target.read_bytes()
        records.append({"relative_path": relative, "byte_length": len(payload), "sha256": hashlib.sha256(payload).hexdigest()})
    return head, canonical_sha256(sorted(records, key=lambda row: str(row["relative_path"])))


def _strict_json(payload: bytes, *, code: str) -> Mapping[str, Any]:
    def duplicate(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        output: dict[str, Any] = {}
        for key, value in pairs:
            if key in output:
                raise ValidationFailure(code)
            output[key] = value
        return output

    def nonfinite(_: str) -> None:
        raise ValidationFailure(code)

    try:
        value = json.loads(payload.decode("utf-8", "strict"), object_pairs_hook=duplicate, parse_constant=nonfinite)
    except (UnicodeError, json.JSONDecodeError, ValidationFailure) as exc:
        if isinstance(exc, ValidationFailure):
            raise
        raise ValidationFailure(code) from exc
    if not isinstance(value, Mapping):
        raise ValidationFailure(code)
    return value


def _jsonl(path: Path, *, code: str) -> Iterator[Mapping[str, Any]]:
    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0))
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise ValidationFailure(code)
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            for line in stream:
                if not line.endswith(b"\n") or len(line) > 4 * 1024 * 1024:
                    raise ValidationFailure(code)
                yield _strict_json(line[:-1], code=code)
        after = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
            raise ValidationFailure("STEP15_INPUT_CHANGED")
    except OSError as exc:
        raise ValidationFailure(code) from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _first_step14_candidate(path: Path) -> tuple[Any, str, int]:
    digest = hashlib.sha256()
    count = 0
    first: Any | None = None
    for row in _jsonl(path, code="STEP14_CANDIDATE_INVALID"):
        encoded = canonical_json_bytes(row) + b"\n"
        digest.update(encoded)
        count += 1
        if first is None:
            first = step14_validation._candidate_from_data(row)
    if first is None:
        raise ValidationFailure("STEP14_CANDIDATE_EMPTY")
    return first, digest.hexdigest(), count


def _proposal_summary(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    count = 0
    for row in _jsonl(path, code="STEP15_PROPOSAL_INVALID"):
        if row.get("automatic_update_allowed") is not False or row.get("normalization_status") not in {"REVIEW_REQUIRED", "CONFLICTING", "AMBIGUOUS", "QUARANTINED", "UNKNOWN"}:
            raise ValidationFailure("STEP15_PROPOSAL_AUTHORITY_VIOLATION")
        encoded = canonical_json_bytes(row) + b"\n"
        digest.update(encoded)
        count += 1
    if count == 0:
        raise ValidationFailure("STEP15_PROPOSAL_EMPTY")
    return count, digest.hexdigest()


def _database_registry_pair(client: migrations.SqlClient, database: str, source_id: str) -> tuple[str, str]:
    output = client.execute(
        database,
        "SELECT source_id, registry_digest FROM memory_patch.source_registry_entries "
        f"WHERE tenant_id={migrations.sql_literal(STEP14_TENANT_ID)} AND source_id={migrations.sql_literal(source_id)}",
        timeout=60,
    )
    rows = migrations.parse_tsv(output)
    if len(rows) != 1 or set(rows[0]) != {"source_id", "registry_digest"}:
        raise ValidationFailure("STEP15_REGISTRY_ROW_MISSING")
    if rows[0]["source_id"] != source_id or not rows[0]["registry_digest"]:
        raise ValidationFailure("STEP15_REGISTRY_ROW_MISSING")
    return rows[0]["source_id"], rows[0]["registry_digest"]


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--step14-bundle-root", type=Path, required=True)
    parser.add_argument("--step15-bundle-root", type=Path, required=True)
    parser.add_argument("--cockroach-binary", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--evidence-output", type=Path, required=True)
    parser.add_argument("--confirm-device-reference", required=True)
    parser.add_argument("--confirm-step14-manifest-digest", required=True)
    parser.add_argument("--confirm-step15-manifest-digest", required=True)
    parser.add_argument("--confirm-source-tree-digest", required=True)
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    runtime: migrations.LocalRuntime | None = None
    client: migrations.SqlClient | None = None
    database: str | None = None
    cleanup: dict[str, Any] | None = None
    try:
        starting_head, worktree_digest = repository_guard()
        config = ExternalVolumeConfig.from_mapping(load_external_volume_environment(args.config))
        adapter = ExternalVolumeRuntimeAdapter(config)
        device = adapter.verify(require_write=False)
        if device.device_reference != args.confirm_device_reference:
            raise ValidationFailure("DEVICE_REFERENCE_MISMATCH")
        manifest_parent = adapter.resolve_path(ExternalVolumeOperation.CORPUS_MANIFEST, "step15-validation-probe", require_write=False).parent
        step14_root = args.step14_bundle_root.resolve(strict=True)
        step15_root = args.step15_bundle_root.resolve(strict=True)
        if args.step14_bundle_root.is_symlink() or step14_root.parent != (manifest_parent / "step14").resolve(strict=True):
            raise ValidationFailure("STEP14_BUNDLE_OUTSIDE_APPROVED_BOUNDARY")
        if args.step15_bundle_root.is_symlink() or step15_root.parent != (manifest_parent / "step15").resolve(strict=True):
            raise ValidationFailure("STEP15_BUNDLE_OUTSIDE_APPROVED_BOUNDARY")
        verified_step14 = verify_inventory_bundle(step14_root)
        verified_step15 = verify_temporal_jurisdiction_bundle(step15_root)
        if verified_step14["manifest_digest"] != args.confirm_step14_manifest_digest or verified_step15["manifest_digest"] != args.confirm_step15_manifest_digest:
            raise ValidationFailure("NORMALIZATION_MANIFEST_DIGEST_MISMATCH")
        step15_manifest = _strict_json((step15_root / "artifact-manifest.json").read_bytes(), code="STEP15_MANIFEST_INVALID")
        if step15_manifest["run"].get("step14_manifest_digest") != verified_step14["manifest_digest"]:
            raise ValidationFailure("STEP15_INPUT_LINEAGE_MISMATCH")
        if step15_manifest["run"].get("source_tree_digest") != args.confirm_source_tree_digest:
            raise ValidationFailure("STEP15_SOURCE_TREE_DIGEST_MISMATCH")
        candidate, candidate_digest, candidate_count = _first_step14_candidate(step14_root / "source-registration-candidates.jsonl")
        proposal_count, proposal_digest = _proposal_summary(step15_root / "source-registry-normalization-proposals.jsonl")
        record = build_source_registry_record(candidate, created_at=datetime(2032, 1, 2, 3, 4, 5, tzinfo=UTC))

        binary = args.cockroach_binary.resolve()
        binary_identity = migrations.verify_binary_identity(binary)
        if binary_identity.get("binary_sha256") != EXPECTED_BINARY_SHA256:
            raise ValidationFailure("COCKROACH_BINARY_DIGEST_MISMATCH")
        run_id = "mp_step15_" + uuid.uuid4().hex[:12]
        database = run_id
        runtime = migrations.LocalRuntime(binary, run_id)
        client = runtime.start()
        server_version = migrations.one_value(client.execute("defaultdb", "SELECT version()"))
        if migrations.PINNED_VERSION not in server_version:
            raise ValidationFailure("COCKROACH_SERVER_VERSION_MISMATCH")
        migrations.create_database(client, database)
        first = migrations.apply_migrations(client, database, timeout=300)
        replay = migrations.apply_migrations(client, database, timeout=300)
        if len(first["applied"]) != len(migrations.load_migrations()) or replay["applied"] or len(replay["skipped"]) != len(migrations.load_migrations()):
            raise ValidationFailure("MIGRATION_REPLAY_NOT_NOOP")
        security = migrations.assert_step9_security_catalog(client, database)
        manifest_hash = hashlib.sha256((ROOT / "config/hats/german-law-1.0.0.json").read_bytes()).hexdigest()
        client.execute(database, step14_validation._base_fixture_sql(datetime(2032, 1, 2, 3, 4, 5, tzinfo=UTC), manifest_hash), timeout=60)
        before_registry = int(migrations.one_value(client.execute(database, "SELECT count(*) FROM memory_patch.source_registry_entries")))
        if before_registry != 0:
            raise ValidationFailure("STEP15_UNEXPECTED_REGISTRY_MUTATION")
        step14_validation._insert_records(client, database, ((candidate, record),), replay=False)
        before_replay = _database_registry_pair(client, database, record.source_id)
        step14_validation._insert_records(client, database, ((candidate, record),), replay=True)
        after_replay = _database_registry_pair(client, database, record.source_id)
        if before_replay != after_replay or after_replay[1] != record.registry_digest:
            raise ValidationFailure("STEP15_EXACT_REPLAY_CREATED_DUPLICATE")
        conflict = replace(
            record,
            authority=SourceAuthorityAssessment(
                SourceAuthorityLevel.AUTHORITATIVE_SECONDARY,
                {"official_identifier": candidate.official_identifier, "policy_version": "step15-conflict-probe-1a", "source_class": candidate.source_class},
            ),
            registry_digest="",
        )
        conflict_sqlstate = client.expect_error(database, step9_validation.registry_insert_sql(conflict), expected_sqlstate="23505")
        published_count = int(migrations.one_value(client.execute(database, "SELECT count(*) FROM memory_patch.source_registry_entries WHERE current_publication_state='PUBLISHED'")))
        if published_count != 0:
            raise ValidationFailure("STEP15_AUTOMATIC_PUBLICATION_DETECTED")
        delete_grants = int(migrations.one_value(client.execute(database, "SELECT count(*) FROM information_schema.table_privileges WHERE table_schema='memory_patch' AND grantee='mp_app_runtime' AND table_name IN ('source_registry_entries','source_publication_events') AND privilege_type='DELETE'")))
        if delete_grants != 0:
            raise ValidationFailure("STEP15_RUNTIME_DELETE_GRANT_DETECTED")
        migrations.drop_database(client, database, timeout=300)
        database = None
        cleanup = runtime.graceful_stop_and_remove(client, owned_children_reaped=True)
        client = None
        if cleanup["cleanup_errors"] or cleanup["force_kill_used"] or not all(cleanup[key] for key in ("pid_exited", "ports_closed", "temporary_store_removed", "drain_command_completed")):
            raise ValidationFailure("DISPOSABLE_RUNTIME_CLEANUP_FAILED")
        evidence = {
            "schema_version": EVIDENCE_SCHEMA_VERSION,
            "step": "STEP_15_GERMAN_LAW_TEMPORAL_JURISDICTIONAL_NORMALIZATION_1A",
            "verdict": "PASS",
            "repository": {"starting_head": starting_head, "worktree_digest": worktree_digest},
            "inputs": {
                "device_reference": device.device_reference,
                "step14_manifest_digest": verified_step14["manifest_digest"],
                "step15_manifest_digest": verified_step15["manifest_digest"],
                "source_tree_digest": args.confirm_source_tree_digest,
                "step15_logical_output_digest": verified_step15["logical_output_digest"],
            },
            "normalization_proposals": {
                "count": proposal_count,
                "digest": proposal_digest,
                "automatic_update_allowed": False,
                "automatic_publication": 0,
            },
            "source_registry_compatibility": {
                "candidate_count_in_input": candidate_count,
                "candidate_file_digest": candidate_digest,
                "one_metadata_only_control_registered": True,
                "exact_replay": "PASS",
                "conflicting_replay": "REJECTED",
                "conflicting_replay_sqlstate": conflict_sqlstate,
                "published_sources": published_count,
                "runtime_delete_grants": delete_grants,
            },
            "database": {
                "binary_sha256": binary_identity["binary_sha256"],
                "exact_server_version": migrations.PINNED_VERSION,
                "migration_count": len(migrations.load_migrations()),
                "migration_first_application": "PASS",
                "migration_replay": "PASS_NOOP",
                "rls_force_rls": "PASS",
                "security_digest": security["security_digest"],
                "persistent_database": False,
            },
            "cleanup": {
                "drain_completed": bool(cleanup["drain_command_completed"]),
                "force_kill_used": bool(cleanup["force_kill_used"]),
                "pid_exited": bool(cleanup["pid_exited"]),
                "ports_closed": bool(cleanup["ports_closed"]),
                "temporary_store_removed": bool(cleanup["temporary_store_removed"]),
                "shutdown_method": cleanup["shutdown_method"],
                "shutdown_budget": cleanup["shutdown_budget"],
            },
            "boundaries": {
                "raw_corpus_body_stored_in_database": False,
                "raw_corpus_writes": 0,
                "aws_writes": 0,
                "s3_writes": 0,
                "network_acquisitions": 0,
                "model_calls": 0,
                "publication_transitions": 0,
                "approval_transitions": 0,
                "step16_started": False,
            },
        }
        evidence_digest = step14_validation._write_evidence(args.evidence_output, evidence)
        print(json.dumps({"status": "PASS", "evidence_digest": evidence_digest, "force_kill_used": False, "published_sources": 0}, sort_keys=True))
        return 0
    except (ValidationFailure, migrations.MigrationError, OSError, ValueError) as exc:
        code = exc.code if isinstance(exc, ValidationFailure) else type(exc).__name__.upper()
        print(json.dumps({"status": "FAILED", "reason": code}, sort_keys=True), file=sys.stderr)
        return 1
    finally:
        if runtime is not None and client is not None:
            try:
                if database is not None:
                    migrations.drop_database(client, database, timeout=300)
                emergency = runtime.graceful_stop_and_remove(client, owned_children_reaped=True)
                if emergency["cleanup_errors"] or emergency["force_kill_used"]:
                    print(json.dumps({"status": "FAILED", "reason": "CLEANUP_FAILURE"}, sort_keys=True), file=sys.stderr)
            except Exception:
                print(json.dumps({"status": "FAILED", "reason": "CLEANUP_EXCEPTION"}, sort_keys=True), file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
