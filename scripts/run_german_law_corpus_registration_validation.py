#!/usr/bin/env python3
"""Validate the Step 14 registration bundle in disposable CockroachDB.

The script reads only the already verified external inventory bundle.  It
stores registration metadata in one in-memory database, proves replay and
conflict behaviour, writes one sanitized repository evidence file, and then
removes the exact owned runtime.  It never reads raw corpus bodies.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import stat
import subprocess
import sys
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]

import run_cockroachdb_migrations as migrations  # noqa: E402
import run_source_registry_validation as step9_validation  # noqa: E402
from aioa_memory_kernel.contracts.serialization import (  # noqa: E402
    canonical_json_bytes,
    canonical_sha256,
)
from aioa_memory_kernel.corpus import (  # noqa: E402
    CorpusInventoryEngine,
    verify_inventory_bundle,
)
from aioa_memory_kernel.corpus.models import (  # noqa: E402
    ParserSupportStatus,
    RegistrationDisposition,
    SourceRegistrationCandidate,
)
from aioa_memory_kernel.german_law.corpus import (  # noqa: E402
    STEP14_TENANT_ID,
    build_source_registry_record,
    registration_operation_identity,
)
from aioa_memory_kernel.sources.eligibility import (  # noqa: E402
    evaluate_publication_eligibility,
)
from aioa_memory_kernel.sources.models import (  # noqa: E402
    SourceAuthorityAssessment,
    SourceAuthorityLevel,
    SourcePublicationState,
    SourceRegistryActor,
    SourceRegistryActorType,
)
from aioa_memory_kernel.sources.provenance import ProvenanceGraph  # noqa: E402
from aioa_memory_kernel.sources.states import (  # noqa: E402
    advance_registry_state,
    build_publication_event,
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
EXPECTED_BINARY_SHA256 = (
    "a5392f4de2c7a2bd838a52b0dcde0d61dcecf2fb060a88b0771367309b5cbdcf"
)
EVIDENCE_SCHEMA_VERSION = "1.0.0"
BATCH_SIZE = 24
SQL_ARGUMENT_MAX_BYTES = 96 * 1024


class ValidationFailure(RuntimeError):
    """A sanitized Step 14 controlled-validation failure."""

    def __init__(self, code: str) -> None:
        super().__init__("Step 14 registration validation failed")
        self.code = code


def _git(*arguments: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(ROOT), *arguments],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=True,
            timeout=30,
            env={
                "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
                "LANG": "C",
                "LC_ALL": "C",
            },
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValidationFailure("REPOSITORY_GUARD_FAILED") from exc
    return result.stdout.decode("utf-8", "strict").strip()


def _allowed_dirty_path(relative: str) -> bool:
    fixed = {
        "AGENTS.md",
        "docs/README.md",
        "docs/roadmap/PRODUCTION_ROADMAP.md",
        "docs/architecture/GERMAN_LAW_CORPUS_INVENTORY_DEDUP_SOURCE_REGISTRATION_1A.md",
        "scripts/run_cockroachdb_migrations.py",
        "scripts/run_german_law_corpus_inventory.py",
        "scripts/run_german_law_corpus_registration_validation.py",
        "src/aioa_memory_kernel/german_law/__init__.py",
        "src/aioa_memory_kernel/german_law/corpus.py",
        "src/aioa_memory_kernel/storage/external_volume.py",
        "tests/test_corpus_inventory.py",
        "tests/test_step14_documentation.py",
    }
    return (
        relative in fixed
        or relative.startswith("src/aioa_memory_kernel/corpus/")
        or relative.startswith("docs/evidence/corpus/step14-")
        or relative.startswith("docs/architecture/STEP_14_")
        or relative.startswith("docs/adr/ADR-021-")
        or relative.startswith("docs/operations/STEP_14_")
        or relative.startswith("docs/audits/STEP_14_")
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
    if head != _git("rev-parse", "origin/main"):
        raise ValidationFailure("REPOSITORY_DIVERGED")
    if _git("rev-list", "--left-right", "--count", "origin/main...HEAD").split() != ["0", "0"]:
        raise ValidationFailure("REPOSITORY_DIVERGED")
    git_dir = Path(_git("rev-parse", "--git-dir"))
    if not git_dir.is_absolute():
        git_dir = ROOT / git_dir
    if any(
        (git_dir / marker).exists()
        for marker in (
            "MERGE_HEAD",
            "REBASE_HEAD",
            "CHERRY_PICK_HEAD",
            "REVERT_HEAD",
            "BISECT_LOG",
            "rebase-merge",
            "rebase-apply",
        )
    ):
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
        records.append(
            {
                "relative_path": relative,
                "byte_length": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        )
    return head, canonical_sha256(sorted(records, key=lambda row: str(row["relative_path"])))


def _strict_json_line(line: bytes) -> dict[str, Any]:
    def duplicate(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValidationFailure("DUPLICATE_CANDIDATE_MEMBER")
            result[key] = value
        return result

    def nonfinite(_: str) -> None:
        raise ValidationFailure("NONFINITE_CANDIDATE_NUMBER")

    try:
        value = json.loads(
            line.decode("utf-8", "strict"),
            object_pairs_hook=duplicate,
            parse_constant=nonfinite,
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValidationFailure("CANDIDATE_DECODE_FAILED") from exc
    if not isinstance(value, dict):
        raise ValidationFailure("CANDIDATE_NOT_OBJECT")
    return value


def _candidate_from_data(value: Mapping[str, Any]) -> SourceRegistrationCandidate:
    expected = dict(value)
    candidate_id = expected.pop("candidate_id", None)
    candidate_digest = expected.pop("candidate_digest", None)
    try:
        expected["parser_support_status"] = ParserSupportStatus(
            expected["parser_support_status"]
        )
        expected["disposition"] = RegistrationDisposition(expected["disposition"])
        expected["provenance_alias_digests"] = tuple(
            expected["provenance_alias_digests"]
        )
        expected["reason_codes"] = tuple(expected["reason_codes"])
        candidate = SourceRegistrationCandidate(**expected)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValidationFailure("CANDIDATE_CONTRACT_REJECTED") from exc
    if candidate.candidate_id != candidate_id or candidate.candidate_digest != candidate_digest:
        raise ValidationFailure("CANDIDATE_DIGEST_MISMATCH")
    return candidate


def load_candidates(
    path: Path,
    *,
    expected_sha256: str | None = None,
    expected_length: int | None = None,
) -> tuple[SourceRegistrationCandidate, ...]:
    candidates: list[SourceRegistrationCandidate] = []
    seen_ids: set[str] = set()
    seen_sources: set[str] = set()
    digest = hashlib.sha256()
    byte_length = 0
    descriptor: int | None = None
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
        )
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise ValidationFailure("CANDIDATE_BUNDLE_UNREADABLE")
        with os.fdopen(descriptor, "rb", closefd=True) as handle:
            descriptor = None
            for line_number, line in enumerate(handle, start=1):
                digest.update(line)
                byte_length += len(line)
                if not line.endswith(b"\n") or len(line) > 64 * 1024:
                    raise ValidationFailure("CANDIDATE_JSONL_NOT_CANONICAL")
                value = _strict_json_line(line)
                if canonical_json_bytes(value) + b"\n" != line:
                    raise ValidationFailure("CANDIDATE_JSONL_NOT_CANONICAL")
                candidate = _candidate_from_data(value)
                if candidate.candidate_id in seen_ids or candidate.logical_source_candidate_id in seen_sources:
                    raise ValidationFailure("CANDIDATE_IDENTITY_CONFLICT")
                if candidate.disposition is not RegistrationDisposition.READY_FOR_REGISTRATION:
                    raise ValidationFailure("UNEXPECTED_REAL_CANDIDATE_DISPOSITION")
                seen_ids.add(candidate.candidate_id)
                seen_sources.add(candidate.logical_source_candidate_id)
                candidates.append(candidate)
            after = os.fstat(handle.fileno())
    except OSError as exc:
        raise ValidationFailure("CANDIDATE_BUNDLE_UNREADABLE") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if (
        opened.st_dev != after.st_dev
        or opened.st_ino != after.st_ino
        or opened.st_size != after.st_size
        or opened.st_mtime_ns != after.st_mtime_ns
    ):
        raise ValidationFailure("CANDIDATE_BUNDLE_CHANGED")
    if (expected_sha256 is None) != (expected_length is None):
        raise ValidationFailure("CANDIDATE_EXPECTATION_INCOMPLETE")
    if expected_sha256 is not None and (
        digest.hexdigest() != expected_sha256
        or byte_length != expected_length
        or opened.st_size != expected_length
    ):
        raise ValidationFailure("CANDIDATE_FILE_DIGEST_MISMATCH")
    if not candidates:
        raise ValidationFailure("CANDIDATE_BUNDLE_EMPTY")
    return tuple(candidates)


def _batches(values: tuple[Any, ...], size: int = BATCH_SIZE) -> Iterable[tuple[Any, ...]]:
    for offset in range(0, len(values), size):
        yield values[offset : offset + size]


def _source_insert_sql(record: Any, candidate: SourceRegistrationCandidate) -> str:
    q = migrations.sql_literal
    provenance = {
        "candidate_digest": candidate.candidate_digest,
        "inventory_run_id": candidate.inventory_run_id,
        "provenance_aliases_digest": canonical_sha256(candidate.provenance_alias_digests),
        "source_mapping_policy": "german-law-step9-source-registration-mapping-1a",
    }
    return (
        "INSERT INTO memory_patch.knowledge_sources (tenant_id, source_id, "
        "hat_scope_id, source_kind, source_reference, provenance, "
        "source_observed_at, created_at) VALUES ("
        f"{q(record.tenant_id)}, {q(record.source_id)}, {q(record.hat_scope_id)}, "
        f"{q(record.source_kind)}, {q(record.source_reference)}, "
        f"{step9_validation.sql_json(provenance)}, "
        f"{step9_validation.timestamp_sql(record.created_at)}, "
        f"{step9_validation.timestamp_sql(record.created_at)})"
    )


def _base_fixture_sql(timestamp: datetime, manifest_digest: str) -> str:
    q = migrations.sql_literal
    at = step9_validation.timestamp_sql(timestamp)
    second_tenant = STEP14_TENANT_ID + "-isolation"
    return "BEGIN;\n" + ";\n".join(
        (
            "INSERT INTO memory_patch.tenants (tenant_id, display_name, metadata, "
            "created_at, updated_at) VALUES "
            f"({q(STEP14_TENANT_ID)}, 'Step 14 controlled validation', "
            f"{step9_validation.sql_json({'fixture': 'step14'})}, {at}, {at}), "
            f"({q(second_tenant)}, 'Step 14 isolation control', "
            f"{step9_validation.sql_json({'fixture': 'step14-isolation'})}, {at}, {at})",
            "INSERT INTO memory_patch.hat_manifests (hat_id, hat_version, "
            "schema_version, display_name, manifest_hash, capabilities, "
            "approval_authority, commit_authority, canonical_write_authority, "
            "external_action_authority, allows_private_memory_access, "
            "allows_user_code, created_at) VALUES ("
            f"'german-law', '1.0.0', '1.0.0', 'German Law', {q(manifest_digest)}, "
            "'[\"SOURCE_AUTHORITY_RANKING\"]'::JSONB, 'NONE', 'NONE', "
            f"'NONE', 'NONE', false, false, {at})",
            "INSERT INTO memory_patch.hat_scopes (tenant_id, hat_scope_id, "
            "target_scope, knowledge_hat_id, knowledge_hat_version, created_at) "
            f"VALUES ({q(STEP14_TENANT_ID)}, 'german-law-global-1a', "
            f"'SHARED_KNOWLEDGE_HAT', 'german-law', '1.0.0', {at}), "
            f"({q(second_tenant)}, 'german-law-isolation-1a', "
            f"'SHARED_KNOWLEDGE_HAT', 'german-law', '1.0.0', {at})",
        )
    ) + ";\nCOMMIT;"


def _registration_sql(
    record: Any,
    candidate: SourceRegistrationCandidate,
    *,
    replay: bool,
) -> tuple[str, ...]:
    source_sql = _source_insert_sql(record, candidate)
    registry_sql = step9_validation.registry_insert_sql(record)
    if replay:
        return (source_sql + " ON CONFLICT DO NOTHING", registry_sql + " ON CONFLICT DO NOTHING")
    operation_id, idempotency_key = registration_operation_identity(candidate)
    return (
        source_sql,
        step9_validation.operation_begin_sql(
            tenant_id=record.tenant_id,
            owner_user_id=None,
            operation_id=operation_id,
            operation_kind="SOURCE_REGISTER",
            idempotency_key=idempotency_key,
            request_digest=record.registry_digest,
            scope_digest=record.scope.scope_digest,
            created_at=record.created_at,
        ),
        registry_sql,
        step9_validation.operation_complete_sql(
            tenant_id=record.tenant_id,
            operation_id=operation_id,
            result_ref=record.source_id,
            result_digest=record.registry_digest,
            completed_at=record.created_at,
        ),
    )


def _insert_records(
    client: migrations.SqlClient,
    database: str,
    pairs: tuple[tuple[SourceRegistrationCandidate, Any], ...],
    *,
    replay: bool,
) -> None:
    statements: list[str] = []
    record_count = 0

    def render(values: list[str]) -> str:
        return "BEGIN;\n" + ";\n".join(values) + ";\nCOMMIT;"

    def execute_batch() -> None:
        nonlocal statements, record_count
        if not statements:
            return
        sql = render(statements)
        if len(sql.encode("utf-8")) > SQL_ARGUMENT_MAX_BYTES:
            raise ValidationFailure("REGISTRATION_SQL_ARGUMENT_LIMIT_EXCEEDED")
        try:
            client.execute(database, sql, timeout=300)
        except OSError as exc:
            raise ValidationFailure("SQL_CLIENT_PROCESS_FAILED") from exc
        statements = []
        record_count = 0

    for candidate, record in pairs:
        record_statements = list(_registration_sql(record, candidate, replay=replay))
        single_sql = render(record_statements)
        if len(single_sql.encode("utf-8")) > SQL_ARGUMENT_MAX_BYTES:
            raise ValidationFailure("REGISTRATION_RECORD_TOO_LARGE")
        proposed = render([*statements, *record_statements])
        if statements and (
            record_count >= BATCH_SIZE
            or len(proposed.encode("utf-8")) > SQL_ARGUMENT_MAX_BYTES
        ):
            execute_batch()
        statements.extend(record_statements)
        record_count += 1
    execute_batch()


def _control_candidate(
    candidate: SourceRegistrationCandidate,
    *,
    suffix: str,
    disposition: RegistrationDisposition,
) -> SourceRegistrationCandidate:
    return SourceRegistrationCandidate(
        inventory_run_id=candidate.inventory_run_id,
        logical_source_candidate_id=f"step14-control-{suffix}",
        content_sha256=canonical_sha256({"step14_control": suffix}),
        normalized_sha256=None,
        sanitized_source_reference=f"step14-control:{suffix}",
        hat_scope_id=candidate.hat_scope_id,
        source_class=candidate.source_class,
        authority_level=candidate.authority_level,
        license_status=candidate.license_status,
        access_class=candidate.access_class,
        redaction_state=candidate.redaction_state,
        jurisdiction=candidate.jurisdiction,
        language=candidate.language,
        official_identifier=f"STEP14-CONTROL-{suffix.upper()}",
        provenance_alias_digests=(canonical_sha256({"alias": suffix}),),
        parser_support_status=candidate.parser_support_status,
        disposition=disposition,
        reason_codes=("SYNTHETIC_CONTROL_ONLY",),
    )


def _transition_control(
    client: migrations.SqlClient,
    database: str,
    record: Any,
    *,
    target: SourcePublicationState,
    timestamp: datetime,
) -> Any:
    eligibility = evaluate_publication_eligibility(
        record,
        ProvenanceGraph(),
        evaluated_at=timestamp,
        quarantine_reasons=("STEP14_SYNTHETIC_CONTROL",),
    )
    event = build_publication_event(
        record,
        event_id=f"step14-{target.value.lower()}-control-event",
        target_state=target,
        eligibility=eligibility,
        actor=SourceRegistryActor(
            SourceRegistryActorType.TRUSTED_APPLICATION,
            "step14-controlled-validation",
        ),
        reason_codes=("STEP14_SYNTHETIC_CONTROL",),
        reviewer_reference=None,
        created_at=timestamp,
    )
    operation_id = f"step14-transition-{canonical_sha256({'event': event.event_digest})}"
    mutation = step9_validation.publication_transaction_sql(record, event)
    durable = step9_validation.durable_mutation_sql(
        tenant_id=record.tenant_id,
        owner_user_id=None,
        operation_id=operation_id,
        operation_kind="PUBLICATION_STATE_TRANSITION",
        idempotency_key=operation_id,
        request_digest=event.event_digest,
        scope_digest=record.scope.scope_digest,
        result_ref=event.event_id,
        result_digest=event.event_digest,
        occurred_at=timestamp,
        mutation_sql=mutation,
    )
    client.execute(database, "BEGIN;\n" + durable + ";\nCOMMIT;", timeout=60)
    return advance_registry_state(record, event)


def _database_pairs(
    client: migrations.SqlClient,
    database: str,
    source_ids: set[str],
) -> dict[str, str]:
    q = migrations.sql_literal
    output = client.execute(
        database,
        "SELECT source_id, registry_digest FROM memory_patch.source_registry_entries "
        f"WHERE tenant_id = {q(STEP14_TENANT_ID)} ORDER BY source_id",
        timeout=120,
    )
    result: dict[str, str] = {}
    for row in csv.reader(io.StringIO(output), delimiter="\t"):
        if len(row) == 2 and row[0] in source_ids:
            result[row[0]] = row[1]
    return result


def _write_evidence(path: Path, payload: dict[str, Any]) -> str:
    if not path.is_absolute():
        path = ROOT / path
    expected_parent = ROOT / "docs/evidence/corpus"
    evidence_root = ROOT / "docs/evidence"
    try:
        evidence_root_metadata = evidence_root.lstat()
        if (
            evidence_root.is_symlink()
            or not stat.S_ISDIR(evidence_root_metadata.st_mode)
            or evidence_root.resolve() != evidence_root
        ):
            raise ValidationFailure("UNSAFE_EVIDENCE_PATH")
        evidence_root_fd = os.open(
            evidence_root,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
        )
        try:
            try:
                os.mkdir("corpus", mode=0o775, dir_fd=evidence_root_fd)
                os.fsync(evidence_root_fd)
            except FileExistsError:
                pass
            parent_metadata = os.stat(
                "corpus",
                dir_fd=evidence_root_fd,
                follow_symlinks=False,
            )
            if not stat.S_ISDIR(parent_metadata.st_mode):
                raise ValidationFailure("UNSAFE_EVIDENCE_PATH")
            parent_fd = os.open(
                "corpus",
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=evidence_root_fd,
            )
            os.close(parent_fd)
        finally:
            os.close(evidence_root_fd)
    except ValidationFailure:
        raise
    except OSError as exc:
        raise ValidationFailure("UNSAFE_EVIDENCE_PATH") from exc
    try:
        parent_metadata = path.parent.lstat()
    except OSError as exc:
        raise ValidationFailure("UNSAFE_EVIDENCE_PATH") from exc
    if (
        path.parent != expected_parent
        or path.parent.resolve() != expected_parent
        or path.parent.is_symlink()
        or not stat.S_ISDIR(parent_metadata.st_mode)
    ):
        raise ValidationFailure("UNSAFE_EVIDENCE_PATH")
    if path.exists() or path.is_symlink():
        raise ValidationFailure("EVIDENCE_NO_OVERWRITE")
    digest = canonical_sha256(payload)
    final = {**payload, "evidence_digest": digest}
    temporary = path.with_name(path.name + ".part")
    if temporary.exists() or temporary.is_symlink():
        raise ValidationFailure("EVIDENCE_PARTIAL_PATH_EXISTS")
    data = canonical_json_bytes(final) + b"\n"
    try:
        with temporary.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path, follow_symlinks=False)
        except FileExistsError as exc:
            temporary.unlink(missing_ok=True)
            raise ValidationFailure("EVIDENCE_NO_OVERWRITE") from exc
        os.unlink(temporary)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise ValidationFailure("EVIDENCE_WRITE_FAILED") from exc
    if hashlib.sha256(path.read_bytes()).hexdigest() != hashlib.sha256(data).hexdigest():
        raise ValidationFailure("EVIDENCE_READBACK_FAILED")
    return digest


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle-root", type=Path, required=True)
    parser.add_argument("--cockroach-binary", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--evidence-output", type=Path, required=True)
    parser.add_argument("--confirm-device-reference", required=True)
    parser.add_argument("--confirm-manifest-digest", required=True)
    parser.add_argument("--confirm-source-tree-digest", required=True)
    parser.add_argument("--confirm-candidate-count", type=int, required=True)
    parser.add_argument("--confirm-declared-provision-count", type=int, required=True)
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    runtime: migrations.LocalRuntime | None = None
    client: migrations.SqlClient | None = None
    database: str | None = None
    cleanup: dict[str, Any] | None = None
    try:
        starting_head, worktree_digest = repository_guard()
        config = ExternalVolumeConfig.from_mapping(
            load_external_volume_environment(args.config)
        )
        adapter = ExternalVolumeRuntimeAdapter(config)
        device = adapter.verify(require_write=False)
        if device.device_reference != args.confirm_device_reference:
            raise ValidationFailure("DEVICE_REFERENCE_MISMATCH")
        expected_parent = adapter.resolve_path(
            ExternalVolumeOperation.CORPUS_MANIFEST,
            "step14-root-probe",
            require_write=False,
        ).parent / "step14"
        bundle_root = args.bundle_root.resolve()
        if (
            args.bundle_root.is_symlink()
            or bundle_root.parent != expected_parent.resolve()
            or bundle_root.name.startswith("step14-") is False
        ):
            raise ValidationFailure("BUNDLE_ROOT_OUTSIDE_APPROVED_BOUNDARY")
        verified_bundle = verify_inventory_bundle(bundle_root)
        if verified_bundle["manifest_digest"] != args.confirm_manifest_digest:
            raise ValidationFailure("MANIFEST_DIGEST_MISMATCH")
        manifest = _strict_json_line(
            CorpusInventoryEngine._read_bounded_regular(
                bundle_root / "inventory-manifest.json",
                maximum_bytes=4 * 1024 * 1024,
            )
        )
        summary = _strict_json_line(
            CorpusInventoryEngine._read_bounded_regular(
                bundle_root / "inventory-summary.json",
                maximum_bytes=4 * 1024 * 1024,
            )
        )
        if (
            canonical_sha256(summary, exclude_fields=("summary_digest",))
            != manifest["summary_digest"]
            or summary.get("summary_digest") != manifest["summary_digest"]
        ):
            raise ValidationFailure("SUMMARY_DIGEST_MISMATCH")
        if manifest["run"]["device_reference"] != device.device_reference:
            raise ValidationFailure("BUNDLE_DEVICE_MISMATCH")
        if manifest["run"]["source_tree_after_digest"] != args.confirm_source_tree_digest:
            raise ValidationFailure("SOURCE_TREE_DIGEST_MISMATCH")
        completed_at = datetime.fromisoformat(
            str(manifest["run"]["completed_at"]).replace("Z", "+00:00")
        ).astimezone(UTC)
        candidate_file_evidence = next(
            (
                item
                for item in verified_bundle["verified_files"]
                if item["relative_path"] == "source-registration-candidates.jsonl"
            ),
            None,
        )
        if candidate_file_evidence is None:
            raise ValidationFailure("CANDIDATE_MANIFEST_ENTRY_MISSING")
        candidates = load_candidates(
            bundle_root / "source-registration-candidates.jsonl",
            expected_sha256=str(candidate_file_evidence["sha256"]),
            expected_length=int(candidate_file_evidence["byte_length"]),
        )
        if len(candidates) != args.confirm_candidate_count:
            raise ValidationFailure("CANDIDATE_COUNT_MISMATCH")
        if args.confirm_declared_provision_count < 0:
            raise ValidationFailure("PROVISION_COUNT_INVALID")
        records = tuple(build_source_registry_record(item, created_at=completed_at) for item in candidates)
        real_pairs = tuple(zip(candidates, records, strict=True))
        expected_pairs = {record.source_id: record.registry_digest for record in records}
        candidate_set_digest = canonical_sha256(
            [(item.candidate_id, item.candidate_digest) for item in candidates]
        )
        registry_set_digest = canonical_sha256(sorted(expected_pairs.items()))

        binary = args.cockroach_binary.resolve()
        binary_identity = migrations.verify_binary_identity(binary)
        if binary_identity.get("binary_sha256") != EXPECTED_BINARY_SHA256:
            raise ValidationFailure("COCKROACH_BINARY_DIGEST_MISMATCH")
        run_id = "mp_step14_" + uuid.uuid4().hex[:12]
        database = run_id
        runtime = migrations.LocalRuntime(binary, run_id)
        client = runtime.start()
        server_version = migrations.one_value(client.execute("defaultdb", "SELECT version()"))
        if migrations.PINNED_VERSION not in server_version:
            raise ValidationFailure("COCKROACH_SERVER_VERSION_MISMATCH")
        migrations.create_database(client, database)
        first_migration = migrations.apply_migrations(client, database, timeout=300)
        replay_migration = migrations.apply_migrations(client, database, timeout=300)
        if len(first_migration["applied"]) != len(migrations.load_migrations()):
            raise ValidationFailure("MIGRATION_FIRST_APPLICATION_INCOMPLETE")
        if replay_migration["applied"] or len(replay_migration["skipped"]) != len(migrations.load_migrations()):
            raise ValidationFailure("MIGRATION_REPLAY_NOT_NOOP")
        security = migrations.assert_step9_security_catalog(client, database)
        manifest_hash = hashlib.sha256((ROOT / "config/hats/german-law-1.0.0.json").read_bytes()).hexdigest()
        client.execute(database, _base_fixture_sql(completed_at, manifest_hash), timeout=60)
        _insert_records(client, database, real_pairs, replay=False)

        control_review = _control_candidate(
            candidates[0], suffix="review", disposition=RegistrationDisposition.REVIEW_REQUIRED
        )
        control_quarantine = _control_candidate(
            candidates[1], suffix="quarantine", disposition=RegistrationDisposition.QUARANTINED
        )
        control_records = (
            build_source_registry_record(control_review, created_at=completed_at),
            build_source_registry_record(control_quarantine, created_at=completed_at),
        )
        control_pairs = ((control_review, control_records[0]), (control_quarantine, control_records[1]))
        _insert_records(client, database, control_pairs, replay=False)
        review_terminal = _transition_control(
            client,
            database,
            control_records[0],
            target=SourcePublicationState.REVIEW_REQUIRED,
            timestamp=completed_at + timedelta(seconds=1),
        )
        quarantine_terminal = _transition_control(
            client,
            database,
            control_records[1],
            target=SourcePublicationState.QUARANTINED,
            timestamp=completed_at + timedelta(seconds=2),
        )

        stored = _database_pairs(client, database, set(expected_pairs))
        if stored != expected_pairs:
            raise ValidationFailure("REGISTRY_PERSISTENCE_MISMATCH")
        stored_digest = canonical_sha256(sorted(stored.items()))
        if stored_digest != registry_set_digest:
            raise ValidationFailure("REGISTRY_SET_DIGEST_MISMATCH")
        counts_before = migrations.parse_tsv(
            client.execute(
                database,
                "SELECT (SELECT count(*) FROM memory_patch.knowledge_sources) AS sources, "
                "(SELECT count(*) FROM memory_patch.source_registry_entries) AS registry, "
                "(SELECT count(*) FROM memory_patch.persistence_operations WHERE operation_kind = 'SOURCE_REGISTER') AS operations",
            )
        )[0]
        _insert_records(client, database, real_pairs, replay=True)
        counts_after = migrations.parse_tsv(
            client.execute(
                database,
                "SELECT (SELECT count(*) FROM memory_patch.knowledge_sources) AS sources, "
                "(SELECT count(*) FROM memory_patch.source_registry_entries) AS registry, "
                "(SELECT count(*) FROM memory_patch.persistence_operations WHERE operation_kind = 'SOURCE_REGISTER') AS operations",
            )
        )[0]
        if counts_before != counts_after or _database_pairs(client, database, set(expected_pairs)) != expected_pairs:
            raise ValidationFailure("EXACT_REPLAY_CREATED_DUPLICATE")

        conflicting = replace(
            records[0],
            authority=SourceAuthorityAssessment(
                SourceAuthorityLevel.AUTHORITATIVE_SECONDARY,
                {
                    "official_identifier": candidates[0].official_identifier,
                    "policy_version": "german-law-source-authority-conflict-probe-1a",
                    "source_class": candidates[0].source_class,
                },
            ),
            registry_digest="",
        )
        conflict_sqlstate = client.expect_error(
            database,
            step9_validation.registry_insert_sql(conflicting),
            expected_sqlstate="23505",
        )
        cross_tenant_state = client.expect_error(
            database,
            "INSERT INTO memory_patch.knowledge_sources (tenant_id, source_id, "
            "hat_scope_id, source_kind, source_reference, provenance, created_at) "
            f"VALUES ({migrations.sql_literal(STEP14_TENANT_ID + '-isolation')}, "
            "'step14-cross-tenant-control', 'german-law-global-1a', 'CONTROL', "
            "'step14-control:cross-tenant', '{}'::JSONB, now())",
            expected_sqlstate="23503",
        )
        published_count = int(
            migrations.one_value(
                client.execute(
                    database,
                    "SELECT count(*) FROM memory_patch.source_registry_entries "
                    "WHERE current_publication_state = 'PUBLISHED'",
                )
            )
        )
        if published_count != 0:
            raise ValidationFailure("AUTOMATIC_PUBLICATION_DETECTED")
        state_rows = migrations.parse_tsv(
            client.execute(
                database,
                "SELECT current_publication_state, count(*) AS count FROM "
                "memory_patch.source_registry_entries GROUP BY current_publication_state "
                "ORDER BY current_publication_state",
            )
        )
        if review_terminal.current_publication_state is not SourcePublicationState.REVIEW_REQUIRED:
            raise ValidationFailure("REVIEW_CONTROL_TRANSITION_FAILED")
        if quarantine_terminal.current_publication_state is not SourcePublicationState.QUARANTINED:
            raise ValidationFailure("QUARANTINE_CONTROL_TRANSITION_FAILED")
        event_chain_count = int(
            migrations.one_value(
                client.execute(database, "SELECT count(*) FROM memory_patch.source_publication_events")
            )
        )
        if event_chain_count != 2:
            raise ValidationFailure("CONTROL_EVENT_CHAIN_MISMATCH")
        delete_grants = int(
            migrations.one_value(
                client.execute(
                    database,
                    "SELECT count(*) FROM information_schema.table_privileges "
                    "WHERE table_schema = 'memory_patch' AND grantee = 'mp_app_runtime' "
                    "AND table_name IN ('source_registry_entries', "
                    "'source_provenance_edges', 'source_publication_events') "
                    "AND privilege_type = 'DELETE'",
                )
            )
        )
        if delete_grants != 0:
            raise ValidationFailure("RUNTIME_DELETE_GRANT_DETECTED")

        migrations.drop_database(client, database, timeout=300)
        database = None
        cleanup = runtime.graceful_stop_and_remove(client, owned_children_reaped=True)
        client = None
        if cleanup["cleanup_errors"] or cleanup["force_kill_used"]:
            raise ValidationFailure("DISPOSABLE_RUNTIME_CLEANUP_FAILED")
        if not all(cleanup[key] for key in ("pid_exited", "ports_closed", "temporary_store_removed")):
            raise ValidationFailure("DISPOSABLE_RUNTIME_CLEANUP_INCOMPLETE")

        payload = {
            "schema_version": EVIDENCE_SCHEMA_VERSION,
            "step": "STEP_14_GERMAN_LAW_CORPUS_INVENTORY_DEDUP_SOURCE_REGISTRATION_1A",
            "verdict": "PASS",
            "repository": {
                "starting_head": starting_head,
                "worktree_digest": worktree_digest,
            },
            "external_bundle": {
                "artifact_root_reference": manifest["artifact_root_reference"],
                "candidate_count": len(candidates),
                "candidate_set_digest": candidate_set_digest,
                "device_reference": device.device_reference,
                "manifest_digest": verified_bundle["manifest_digest"],
                "source_tree_digest": args.confirm_source_tree_digest,
                "verified_file_count": len(verified_bundle["verified_files"]),
            },
            "inventory": {
                "directories_observed": summary["directories_observed"],
                "objects_observed": summary["objects_observed"],
                "stable_files": summary["stable_files"],
                "bytes_observed": summary["bytes_observed"],
                "raw_sha256_count": summary["raw_sha256_count"],
                "symlink_count": summary["symlink_count"],
                "special_count": summary["special_count"],
                "unreadable_count": summary["unreadable_count"],
                "unstable_count": summary["unstable_count"],
                "exact_duplicate_group_count": summary["exact_duplicate_group_count"],
                "exact_duplicate_member_count": summary["exact_duplicate_member_count"],
                "informational_duplicate_bytes": summary["informational_duplicate_bytes"],
                "normalized_duplicate_group_count": summary["normalized_duplicate_group_count"],
                "near_duplicate_candidate_count": summary["near_duplicate_candidate_count"],
                "registration_candidate_count": summary["registration_candidate_count"],
                "registration_conflict_count": summary["registration_conflict_count"],
                "declared_provision_count_sum": args.confirm_declared_provision_count,
                "distributions": summary["distributions"],
                "source_tree_writes": summary["source_tree_writes"],
                "source_files_modified": summary["source_files_modified"],
                "source_files_deleted": summary["source_files_deleted"],
                "summary_digest": summary["summary_digest"],
            },
            "registration": {
                "real_candidates_registered": len(candidates),
                "registry_set_digest": registry_set_digest,
                "stored_registry_set_digest": stored_digest,
                "exact_replay": "PASS",
                "conflicting_replay": "REJECTED",
                "conflicting_replay_sqlstate": conflict_sqlstate,
                "registration_counts_before_replay": counts_before,
                "registration_counts_after_replay": counts_after,
                "publication_state_counts": {
                    row["current_publication_state"]: int(row["count"])
                    for row in state_rows
                },
                "published_sources": published_count,
                "synthetic_review_controls": 1,
                "synthetic_quarantine_controls": 1,
                "synthetic_publication_event_count": event_chain_count,
                "cross_tenant_insert": "REJECTED",
                "cross_tenant_sqlstate": cross_tenant_state,
            },
            "database": {
                "binary_sha256": binary_identity["binary_sha256"],
                "exact_server_version": migrations.PINNED_VERSION,
                "migration_count": len(migrations.load_migrations()),
                "migration_first_application": "PASS",
                "migration_replay": "PASS_NOOP",
                "rls_force_rls": "PASS",
                "security_digest": security["security_digest"],
                "runtime_delete_grants": delete_grants,
                "runtime_mode": "DISPOSABLE_LOCAL_SINGLE_NODE",
                "persistent_database": False,
            },
            "cleanup": {
                "drain_completed": bool(cleanup["drain_command_completed"]),
                "force_kill_used": bool(cleanup["force_kill_used"]),
                "owned_children_reaped": bool(cleanup["owned_child_processes_reaped"]),
                "pid_exited": bool(cleanup["pid_exited"]),
                "ports_closed": bool(cleanup["ports_closed"]),
                "temporary_store_removed": bool(cleanup["temporary_store_removed"]),
                "shutdown_method": cleanup["shutdown_method"],
            },
            "boundaries": {
                "raw_corpus_body_stored_in_database": False,
                "raw_corpus_writes": 0,
                "aws_writes": 0,
                "s3_writes": 0,
                "model_calls": 0,
                "network_acquisitions": 0,
                "automatic_publication": 0,
                "runtime_delete": 0,
                "step15_started": False,
            },
        }
        evidence_digest = _write_evidence(args.evidence_output, payload)
        print(
            json.dumps(
                {
                    "status": "PASS",
                    "candidate_count": len(candidates),
                    "evidence_digest": evidence_digest,
                    "force_kill_used": False,
                    "published_sources": 0,
                },
                sort_keys=True,
            )
        )
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
                    print(
                        json.dumps(
                            {"status": "FAILED", "reason": "CLEANUP_FAILURE"},
                            sort_keys=True,
                        ),
                        file=sys.stderr,
                    )
            except Exception:
                print(
                    json.dumps(
                        {"status": "FAILED", "reason": "CLEANUP_EXCEPTION"},
                        sort_keys=True,
                    ),
                    file=sys.stderr,
                )


if __name__ == "__main__":
    raise SystemExit(main())
