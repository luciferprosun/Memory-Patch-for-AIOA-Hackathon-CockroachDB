#!/usr/bin/env python3
"""Controlled Step 16 publication validation against one real projection.

This program reads a completed Step 16 external bundle and proves the trusted
Step 7, Step 9, Step 10, and Step 11 boundary with one representative already
published projection.  It never creates an S3 object: the S3 transport is
read-only and fails closed if reconciliation would require ``PutObject``.
Its CockroachDB database, role, and parsed text are disposable validation
state.  Importing this module has no filesystem, network, AWS, or database I/O.
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
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator, Mapping


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]

import run_cockroachdb_migrations as migrations  # noqa: E402
import run_german_law_corpus_registration_validation as step14_validation  # noqa: E402
from aws_cli_s3_client import AwsCliS3Client  # noqa: E402
from cockroach_cli_dbapi import OwnedChildRegistry, connection_factory  # noqa: E402
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
from aioa_memory_kernel.german_law.publication import (  # noqa: E402
    GermanLawPublicationEngine,
    STEP16_PROJECTION_ADAPTER_VERSION,
    verify_german_law_publication_bundle,
)
from aioa_memory_kernel.ingestion import (  # noqa: E402
    IngestionOrchestrator,
    IngestionSagaService,
    SagaMilestone,
    Step9PublicationPort,
    build_initial_saga,
)
from aioa_memory_kernel.parsing import (  # noqa: E402
    GenericParseArtifactValidatorPort,
    GenericParsingPipelinePort,
    LanguageTag,
    ParsingPersistenceService,
)
from aioa_memory_kernel.persistence import (  # noqa: E402
    AccessMode,
    RequestContext,
    SerializableTransactionRunner,
)
from aioa_memory_kernel.runtime import LinuxExternalVolumeProbe  # noqa: E402
from aioa_memory_kernel.security.credentials import (  # noqa: E402
    AWS_WORKLOAD_IDENTITY_ENVIRONMENT_NAMES,
    build_minimal_subprocess_environment,
)
from aioa_memory_kernel.sources import (  # noqa: E402
    OriginMetadata,
    ParserIdentity,
    ProvenanceArtifactIdentity,
    SourceRegistryRecord,
    SourceRegistryService,
    SourceRegistryActorType,
    TransformationIdentity,
)
from aioa_memory_kernel.storage import (  # noqa: E402
    ExternalVolumeConfig,
    ExternalVolumeOperation,
    ExternalVolumeRuntimeAdapter,
    S3ObjectLockMode,
    S3SnapshotAdapter,
    S3SnapshotConfig,
    SnapshotEnvelope,
    load_external_volume_environment,
)


DEFAULT_CONFIG = ROOT / ".local" / "external-data.env"
EXPECTED_BRANCH = "main"
EXPECTED_REMOTE = "https://github.com/luciferprosun/Memory-Patch-for-AIOA-Hackathon-CockroachDB"
EXPECTED_COCKROACH_SHA256 = "a5392f4de2c7a2bd838a52b0dcde0d61dcecf2fb060a88b0771367309b5cbdcf"
AWS_PROFILE = "aoia-admin"
AWS_REGION = "eu-central-1"
S3_BUCKET = "aioa-memory-patch-global-3f105fcd-eu-central-1"
S3_PREFIX = "memory-patch/snapshots/v1"
S3_RETENTION_DAYS = 7
AWS_CLI_SHA256 = "cf8592f6d5830307168c6397158b8263566471fac00dfc5028fb8a540ddd8332"
_ASSUMED_ROLE = re.compile(r"^arn:[a-z0-9-]+:sts::[0-9]{12}:assumed-role/[^/]+/[^/]+$")
_SAFE_ROLE = re.compile(r"^[a-z][a-z0-9_]{2,62}$")


class ValidationFailure(RuntimeError):
    """A sanitized controlled-validation failure."""

    def __init__(self, code: str) -> None:
        super().__init__("Step 16 controlled validation failed")
        self.code = code


class _Clock:
    """A deterministic monotonic clock without legal-time inference."""

    def __init__(self, start: datetime) -> None:
        self._start = start.astimezone(UTC)
        self._index = 0

    def __call__(self) -> datetime:
        value = self._start + timedelta(microseconds=self._index)
        self._index += 1
        if self._index >= 1_000_000:
            raise ValidationFailure("VALIDATION_CLOCK_EXHAUSTED")
        return value


class _ProjectionAcquisition:
    """Return one verified existing projection without fetch or interpretation."""

    def __init__(self, payload: bytes) -> None:
        self._payload = payload
        self.calls = 0

    def acquire(self, _saga: object) -> bytes:
        self.calls += 1
        return self._payload


class _ReadOnlyS3Transport:
    """Allow exact S3 verification while making all new writes fail closed."""

    def __init__(self, client: AwsCliS3Client) -> None:
        self._client = client
        self.put_attempts = 0

    @property
    def operation_counts(self) -> Mapping[str, int]:
        return self._client.operation_counts

    def put_object(self, **_values: object) -> Mapping[str, object]:
        self.put_attempts += 1
        raise ValidationFailure("STEP16_VALIDATION_S3_WRITE_FORBIDDEN")

    def __getattr__(self, name: str) -> object:
        return getattr(self._client, name)


@dataclass(frozen=True, slots=True)
class _Selection:
    item: Mapping[str, Any]
    raw_binding: Mapping[str, Any]
    projection_binding: Mapping[str, Any]
    parser_coverage: Mapping[str, Any]
    candidate: object


def _git(*arguments: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(ROOT), *arguments],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            timeout=120,
            env={"PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"), "LANG": "C", "LC_ALL": "C"},
            check=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValidationFailure("REPOSITORY_GUARD_FAILED") from exc
    return completed.stdout.strip()


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
        "docs/adr/ADR-023-german-law-hat-publication-corpus-verification.md",
        "docs/operations/STEP_16_GERMAN_LAW_HAT_PUBLICATION_VALIDATION_1A.md",
    }
    return (
        relative in fixed
        or re.fullmatch(r"docs/(?:audits|evidence/corpus)/[^/]*step_?16[^/]*\.(?:md|json)", relative, re.I) is not None
    )


def _repository_guard() -> tuple[str, str]:
    if Path(_git("rev-parse", "--show-toplevel")) != ROOT:
        raise ValidationFailure("REPOSITORY_IDENTITY_MISMATCH")
    if _git("remote", "get-url", "origin").removesuffix(".git").rstrip("/") != EXPECTED_REMOTE:
        raise ValidationFailure("REPOSITORY_REMOTE_MISMATCH")
    if _git("branch", "--show-current") != EXPECTED_BRANCH:
        raise ValidationFailure("REPOSITORY_BRANCH_MISMATCH")
    _git("fetch", "origin", "--prune")
    head = _git("rev-parse", "HEAD")
    if head != _git("rev-parse", "origin/main") or _git("rev-list", "--left-right", "--count", "origin/main...HEAD").split() != ["0", "0"]:
        raise ValidationFailure("REPOSITORY_DIVERGED")
    git_dir = Path(_git("rev-parse", "--git-dir"))
    if not git_dir.is_absolute():
        git_dir = ROOT / git_dir
    if any((git_dir / marker).exists() for marker in ("MERGE_HEAD", "REBASE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "BISECT_LOG", "rebase-merge", "rebase-apply")):
        raise ValidationFailure("INTERRUPTED_GIT_OPERATION")
    records: list[dict[str, object]] = []
    raw = subprocess.run(
        ["git", "-C", str(ROOT), "status", "--porcelain=v1", "-z", "--untracked-files=all"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
        timeout=30,
    ).stdout
    for entry in raw.split(b"\0"):
        if not entry:
            continue
        value = entry.decode("utf-8", "strict")
        if len(value) < 4 or value[0] in "DR" or value[1] in "DR":
            raise ValidationFailure("DIRTY_SCOPE_UNSAFE")
        relative = value[3:]
        if not _allowed_dirty_path(relative):
            raise ValidationFailure("UNRELATED_WORKTREE_CHANGE")
        path = ROOT / relative
        metadata = path.lstat()
        if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
            raise ValidationFailure("DIRTY_SCOPE_UNSAFE")
        payload = path.read_bytes()
        records.append({"relative_path": relative, "byte_length": len(payload), "sha256": hashlib.sha256(payload).hexdigest()})
    return head, canonical_sha256(sorted(records, key=lambda value: str(value["relative_path"])))


def _strict_json(payload: bytes, *, code: str) -> Mapping[str, Any]:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, member in items:
            if key in value:
                raise ValidationFailure(code)
            value[key] = member
        return value

    def nonfinite(_: str) -> None:
        raise ValidationFailure(code)

    try:
        value = json.loads(payload.decode("utf-8", "strict"), object_pairs_hook=pairs, parse_constant=nonfinite)
    except (UnicodeError, json.JSONDecodeError, ValidationFailure) as exc:
        if isinstance(exc, ValidationFailure):
            raise
        raise ValidationFailure(code) from exc
    if not isinstance(value, dict):
        raise ValidationFailure(code)
    return value


def _jsonl(path: Path, *, code: str) -> Iterator[Mapping[str, Any]]:
    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0))
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ValidationFailure(code)
        with os.fdopen(descriptor, "rb", closefd=True) as stream:
            descriptor = None
            for line in stream:
                if not line.endswith(b"\n") or len(line) > 4 * 1024 * 1024:
                    raise ValidationFailure(code)
                value = _strict_json(line[:-1], code=code)
                if canonical_json_bytes(value) + b"\n" != line:
                    raise ValidationFailure(code)
                yield value
            after = os.fstat(stream.fileno())
    except OSError as exc:
        raise ValidationFailure(code) from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
        raise ValidationFailure("STEP16_BUNDLE_CHANGED_DURING_READ")


def _parse_time(value: object, *, code: str) -> datetime:
    if not isinstance(value, str):
        raise ValidationFailure(code)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValidationFailure(code) from exc
    if parsed.tzinfo is None:
        raise ValidationFailure(code)
    return parsed.astimezone(UTC)


def _selection(bundle: Path, step14: Path) -> _Selection:
    bindings = {
        str(row.get("binding_digest")): row
        for row in _jsonl(bundle / "snapshot-bindings.jsonl", code="STEP16_SNAPSHOT_BINDINGS_INVALID")
    }
    parser = {
        str(row.get("version_identity")): row
        for row in _jsonl(bundle / "parser-coverage.jsonl", code="STEP16_PARSER_COVERAGE_INVALID")
    }
    candidates = {
        str(row.get("candidate_id")): row
        for row in _jsonl(step14 / "source-registration-candidates.jsonl", code="STEP14_CANDIDATES_INVALID")
    }
    for item in _jsonl(bundle / "publication-items.jsonl", code="STEP16_PUBLICATION_ITEMS_INVALID"):
        version = item.get("version_identity")
        raw = bindings.get(str(item.get("raw_snapshot_binding_digest")))
        projection = bindings.get(str(item.get("projection_snapshot_binding_digest")))
        coverage = parser.get(str(version))
        candidate_data = candidates.get(str(item.get("source_registry_candidate_id")))
        if raw is None or projection is None or coverage is None or candidate_data is None:
            raise ValidationFailure("STEP16_PUBLICATION_CHAIN_INCOMPLETE")
        if raw.get("binding_kind") != "RAW_GII_XML_ZIP" or projection.get("media_type") != "text/plain":
            raise ValidationFailure("STEP16_PUBLICATION_CHAIN_INCOMPLETE")
        try:
            candidate = step14_validation._candidate_from_data(candidate_data)
        except Exception as exc:
            raise ValidationFailure("STEP14_CANDIDATE_CONTRACT_REJECTED") from exc
        return _Selection(item, raw, projection, coverage, candidate)
    raise ValidationFailure("STEP16_NO_PUBLISHED_REPRESENTATIVE")


def _assert_binding(envelope: SnapshotEnvelope, binding: Mapping[str, Any]) -> None:
    expected = {
        "snapshot_id": envelope.snapshot_id,
        "content_sha256": envelope.content_sha256,
        "content_length": envelope.content_length,
        "serialization_version": envelope.serialization_version,
        "media_type": envelope.media_type,
        "scope_digest": envelope.scope_digest,
        "snapshot_manifest_sha256": envelope.manifest_sha256,
    }
    if any(binding.get(key) != value for key, value in expected.items()):
        raise ValidationFailure("STEP16_SNAPSHOT_REHYDRATION_MISMATCH")
    if _parse_time(binding.get("captured_at"), code="STEP16_SNAPSHOT_CAPTURE_INVALID") != envelope.captured_at:
        raise ValidationFailure("STEP16_SNAPSHOT_REHYDRATION_MISMATCH")
    if _parse_time(binding.get("retain_until"), code="STEP16_SNAPSHOT_RETENTION_INVALID") != envelope.retain_until:
        raise ValidationFailure("STEP16_SNAPSHOT_REHYDRATION_MISMATCH")


def _quoted_role(value: str) -> str:
    if _SAFE_ROLE.fullmatch(value) is None:
        raise ValidationFailure("DISPOSABLE_ROLE_IDENTIFIER_INVALID")
    return f'"{value}"'


def _create_login_role(root: migrations.SqlClient, role: str) -> None:
    root.execute(
        "defaultdb",
        "SET allow_role_memberships_to_change_during_transaction = true;\n"
        f"CREATE ROLE {_quoted_role(role)} WITH LOGIN NOCREATEROLE NOCREATEDB NOBYPASSRLS;\n"
        f"GRANT mp_app_runtime, mp_request_context_setter TO {_quoted_role(role)}",
        timeout=180,
    )


def _drop_login_role(root: migrations.SqlClient, role: str) -> None:
    root.execute(
        "defaultdb",
        "SET allow_role_memberships_to_change_during_transaction = true;\n"
        f"DROP ROLE IF EXISTS {_quoted_role(role)}",
        timeout=180,
    )


def _seed_sql(record: SourceRegistryRecord, selection: _Selection, projection: SnapshotEnvelope, binding: Mapping[str, Any], at: datetime) -> str:
    manifest_hash = hashlib.sha256((ROOT / "config/hats/german-law-1.0.0.json").read_bytes()).hexdigest()
    base = step14_validation._base_fixture_sql(at, manifest_hash)
    q = migrations.sql_literal
    timestamp = q(at.isoformat()) + "::TIMESTAMPTZ"
    source = step14_validation._source_insert_sql(record, selection.candidate)
    object_reference = f"s3:{binding['bucket_reference']}:{binding['object_key']}:{binding['version_id']}"
    snapshot = (
        "INSERT INTO memory_patch.source_snapshots "
        "(tenant_id, snapshot_id, source_id, hat_scope_id, content_sha256, "
        "byte_length, storage_class, immutable_object_reference, captured_at, "
        "source_observed_at, provenance) VALUES ("
        f"{q(record.tenant_id)}, {q(projection.snapshot_id)}, {q(record.source_id)}, "
        f"{q(record.hat_scope_id)}, {q(projection.content_sha256)}, "
        f"{projection.content_length}, 'S3_GLOBAL_LOCKED_SNAPSHOT', "
        f"{q(object_reference)}, {timestamp}, {timestamp}, "
        f"{step14_validation.sql_json({'step16_projection_binding_digest': binding['binding_digest'], 'snapshot_manifest_sha256': projection.manifest_sha256})})"
    )
    return base + "\nBEGIN;\n" + source + ";\n" + snapshot + ";\nCOMMIT;"


def _projection_record(selection: _Selection, projection: SnapshotEnvelope, at: datetime) -> SourceRegistryRecord:
    base = build_source_registry_record(selection.candidate, created_at=at)
    parser = ParserIdentity(
        str(selection.parser_coverage["parser_name"]),
        str(selection.parser_coverage["parser_version"]),
        str(selection.parser_coverage["parser_contract_version"]),
    )
    transformation = TransformationIdentity(
        "german-law-gii-textual-projection",
        "1.0.0",
        STEP16_PROJECTION_ADAPTER_VERSION,
    )
    origin = OriginMetadata(
        "STEP16_DERIVED_PROJECTION",
        "memory-patch-step16-publication",
        "1.0.0",
        STEP16_PROJECTION_ADAPTER_VERSION,
        f"step16-projection:{selection.projection_binding['binding_id']}",
        at,
    )
    return SourceRegistryRecord(
        tenant_id=base.tenant_id,
        source_id=base.source_id,
        hat_scope_id=base.hat_scope_id,
        source_kind=base.source_kind,
        source_reference=base.source_reference,
        scope=base.scope,
        authority=base.authority,
        license=base.license,
        access_class=base.access_class,
        redaction_state=base.redaction_state,
        parser=parser,
        transformation=transformation,
        origin=origin,
        artifact=ProvenanceArtifactIdentity(
            "STEP16_VERIFIED_TEXTUAL_PROJECTION",
            projection.content_sha256,
            projection.content_length,
            projection.media_type,
            origin,
            parser,
            transformation,
            at,
            exact_source_bytes=True,
            model_generated=False,
        ),
        snapshot_id=projection.snapshot_id,
        knowledge_version_id=f"step16-kv-{canonical_sha256({'version': selection.item['version_identity']})}",
        current_publication_state=base.current_publication_state,
        current_publication_sequence=base.current_publication_sequence,
        current_publication_event_digest=base.current_publication_event_digest,
        created_at=at,
        updated_at=at,
    )


def _aws_binary() -> Path:
    raw = os.environ.get("AWS_CLI_PATH") or shutil.which("aws")
    if raw is None:
        raise ValidationFailure("AWS_CLI_UNAVAILABLE")
    path = Path(raw).resolve(strict=True)
    if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != AWS_CLI_SHA256:
        raise ValidationFailure("AWS_CLI_DIGEST_MISMATCH")
    return path


def _aws_identity(binary: Path) -> Mapping[str, Any]:
    environment = build_minimal_subprocess_environment(
        os.environ,
        allowed_names=AWS_WORKLOAD_IDENTITY_ENVIRONMENT_NAMES,
    )
    environment["AWS_PAGER"] = ""
    try:
        completed = subprocess.run(
            [str(binary), "sts", "get-caller-identity", "--profile", AWS_PROFILE, "--region", AWS_REGION, "--no-cli-pager", "--output", "json"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            timeout=60,
            env=environment,
            check=False,
        )
        value = json.loads(completed.stdout or "{}") if completed.returncode == 0 else {}
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        raise ValidationFailure("AWS_IDENTITY_PREFLIGHT_FAILED") from exc
    if not isinstance(value, Mapping) or not isinstance(value.get("Arn"), str) or _ASSUMED_ROLE.fullmatch(str(value["Arn"])) is None:
        raise ValidationFailure("AWS_IDENTITY_NOT_APPROVED_TEMPORARY_ROLE")
    return {"profile": AWS_PROFILE, "region": AWS_REGION, "temporary_assumed_role": True, "root_principal": False}


def _write_evidence(path: Path, value: Mapping[str, Any]) -> str:
    if path.is_symlink() or path.exists():
        raise ValidationFailure("EVIDENCE_OUTPUT_EXISTS")
    payload = dict(value)
    payload["evidence_digest"] = canonical_sha256(payload)
    encoded = canonical_json_bytes(payload) + b"\n"
    try:
        with path.open("xb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as exc:
        raise ValidationFailure("EVIDENCE_WRITE_FAILED") from exc
    if hashlib.sha256(path.read_bytes()).hexdigest() != hashlib.sha256(encoded).hexdigest():
        raise ValidationFailure("EVIDENCE_READBACK_MISMATCH")
    return str(payload["evidence_digest"])


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--step14-bundle-root", type=Path, required=True)
    parser.add_argument("--step15-bundle-root", type=Path, required=True)
    parser.add_argument("--publication-bundle-root", type=Path, required=True)
    parser.add_argument("--cockroach-binary", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--evidence-output", type=Path, required=True)
    parser.add_argument("--confirm-device-reference", required=True)
    parser.add_argument("--confirm-step14-manifest-digest", required=True)
    parser.add_argument("--confirm-step15-manifest-digest", required=True)
    parser.add_argument("--confirm-publication-manifest-digest", required=True)
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    runtime: migrations.LocalRuntime | None = None
    root: migrations.SqlClient | None = None
    database: str | None = None
    role: str | None = None
    cleanup: Mapping[str, Any] | None = None
    try:
        head, worktree_digest = _repository_guard()
        config = ExternalVolumeConfig.from_mapping(load_external_volume_environment(args.config))
        external = ExternalVolumeRuntimeAdapter(config, probe=LinuxExternalVolumeProbe())
        volume = external.verify(require_write=True)
        if volume.device_reference != args.confirm_device_reference:
            raise ValidationFailure("DEVICE_REFERENCE_MISMATCH")
        step14 = args.step14_bundle_root.resolve(strict=True)
        step15 = args.step15_bundle_root.resolve(strict=True)
        bundle = args.publication_bundle_root.resolve(strict=True)
        if any(path.is_symlink() for path in (step14, step15, bundle)):
            raise ValidationFailure("UNSAFE_EXTERNAL_BUNDLE")
        verified14 = verify_inventory_bundle(step14)
        verified15 = verify_temporal_jurisdiction_bundle(step15)
        verified16 = verify_german_law_publication_bundle(bundle)
        if (
            verified14["manifest_digest"] != args.confirm_step14_manifest_digest
            or verified15["manifest_digest"] != args.confirm_step15_manifest_digest
            or verified16["manifest_digest"] != args.confirm_publication_manifest_digest
        ):
            raise ValidationFailure("PUBLICATION_INPUT_DIGEST_MISMATCH")
        selection = _selection(bundle, step14)
        engine = GermanLawPublicationEngine(
            source_root=args.source_root.resolve(strict=True),
            step14_bundle_root=step14,
            step15_bundle_root=step15,
            bundle_parent=bundle.parent,
            device_reference=volume.device_reference,
            starting_head=head,
        )
        inputs, _context = engine._load_input_context()
        item = next((value for value in inputs if value.version_identity == selection.item.get("version_identity")), None)
        if item is None:
            raise ValidationFailure("STEP16_SELECTION_INPUT_MISSING")
        raw_payload = engine._read_verified_source(item.raw_source_relative_path, item.raw_source_sha256, item.raw_source_length, maximum_length=64 * 1024 * 1024)
        projection_payload, _metadata = engine._projection_payload(item)
        captured_at = _parse_time(selection.projection_binding.get("captured_at"), code="STEP16_SNAPSHOT_CAPTURE_INVALID")
        retain_until = _parse_time(selection.projection_binding.get("retain_until"), code="STEP16_SNAPSHOT_RETENTION_INVALID")
        raw, projection = engine._snapshot_envelopes(item, raw_payload, projection_payload, captured_at=captured_at, retain_until=retain_until)
        _assert_binding(raw, selection.raw_binding)
        _assert_binding(projection, selection.projection_binding)
        if projection.content_sha256 != selection.parser_coverage.get("input_sha256"):
            raise ValidationFailure("STEP16_PARSER_INPUT_BINDING_MISMATCH")
        record = _projection_record(selection, projection, captured_at)
        binary = args.cockroach_binary.resolve(strict=True)
        binary_identity = migrations.verify_binary_identity(binary)
        if binary_identity.get("binary_sha256") != EXPECTED_COCKROACH_SHA256:
            raise ValidationFailure("COCKROACH_BINARY_DIGEST_MISMATCH")
        run_id = "mp_step16_" + uuid.uuid4().hex[:12]
        database = run_id
        runtime = migrations.LocalRuntime(binary, run_id)
        root = runtime.start()
        if migrations.PINNED_VERSION not in migrations.one_value(root.execute("defaultdb", "SELECT version()")):
            raise ValidationFailure("COCKROACH_SERVER_VERSION_MISMATCH")
        migrations.create_database(root, database)
        first = migrations.apply_migrations(root, database, timeout=300)
        replay = migrations.apply_migrations(root, database, timeout=300)
        if len(first["applied"]) != len(migrations.load_migrations()) or replay["applied"]:
            raise ValidationFailure("MIGRATION_REPLAY_NOT_NOOP")
        security9 = migrations.assert_step9_security_catalog(root, database)
        security10 = migrations.assert_step10_security_catalog(root, database)
        security11 = migrations.assert_step11_security_catalog(root, database)
        role = f"mp_step16_{uuid.uuid4().hex[:12]}"
        _create_login_role(root, role)
        root.execute(database, _seed_sql(record, selection, projection, selection.projection_binding, captured_at), timeout=180)
        if runtime.runtime_dir is None or runtime.sql_port is None:
            raise ValidationFailure("COCKROACH_RUNTIME_FACTS_MISSING")
        children = OwnedChildRegistry()
        factory = connection_factory(binary=binary, port=runtime.sql_port, database=database, user=role, log_directory=runtime.runtime_dir, child_registry=children, timeout_seconds=120)
        clock = _Clock(captured_at)
        runner = SerializableTransactionRunner(factory)
        source_service = SourceRegistryService(runner, clock=clock)
        parsing_service = ParsingPersistenceService(runner)
        saga_service = IngestionSagaService(runner, clock=clock)
        context = RequestContext(STEP14_TENANT_ID, None, AccessMode.TENANT_SHARED)
        registered = source_service.register_source(context, record, operation_id=f"step16-register-{record.registry_digest}", idempotency_key=f"step16-register:{record.registry_digest}")
        if registered.registry_digest != record.registry_digest:
            raise ValidationFailure("STEP16_SOURCE_REGISTRATION_MISMATCH")
        eligibility = source_service.evaluate_publication_eligibility(context, tenant_id=record.tenant_id, source_id=record.source_id, hat_scope_id=record.hat_scope_id, evaluated_at=clock())
        if not eligibility.eligible:
            raise ValidationFailure("STEP16_SOURCE_ELIGIBILITY_FAILED")
        saga = build_initial_saga(
            tenant_id=record.tenant_id,
            source_id=record.source_id,
            hat_scope_id=record.hat_scope_id,
            owner_user_id=None,
            knowledge_version_id=str(record.knowledge_version_id),
            idempotency_key=f"step16-publication-validation:{projection.snapshot_id}",
            scope_digest=record.scope.scope_digest,
            source_registry_digest=record.registry_digest,
            content_sha256=projection.content_sha256,
            content_length=projection.content_length,
            media_type=projection.media_type,
            local_relative_path=f"step16-validation/{projection.snapshot_id}.txt",
            snapshot_id=projection.snapshot_id,
            captured_at=projection.captured_at,
            retain_until=projection.retain_until,
            created_at=captured_at,
        )
        aws_binary = _aws_binary()
        aws_identity = _aws_identity(aws_binary)
        with tempfile.TemporaryDirectory(prefix="memory-patch-step16-s3-") as temporary:
            client = AwsCliS3Client(aws_binary=aws_binary, profile=AWS_PROFILE, region=AWS_REGION, temporary_directory=Path(temporary), expected_binary_sha256=AWS_CLI_SHA256, timeout_seconds=180)
            transport = _ReadOnlyS3Transport(client)
            storage = S3SnapshotAdapter(S3SnapshotConfig(region=AWS_REGION, bucket_name=S3_BUCKET, retention_mode=S3ObjectLockMode.GOVERNANCE, retention_days=S3_RETENTION_DAYS, key_prefix=S3_PREFIX), transport)
            existing = storage.reconcile_snapshot(projection)
            if existing is None or existing.version_id != selection.projection_binding.get("version_id") or not (existing.metadata_verified and existing.content_verified):
                raise ValidationFailure("STEP16_EXISTING_SNAPSHOT_NOT_VERIFIED")
            parser = GenericParsingPipelinePort(snapshot_storage=storage, snapshot_resolver=lambda _saga: projection, persistence=parsing_service, clock=clock, language_tag=lambda _saga: LanguageTag("de"))
            validator = GenericParseArtifactValidatorPort(persistence=parsing_service, clock=clock)
            publication = Step9PublicationPort(source_service, clock=clock)
            acquisition = _ProjectionAcquisition(projection.canonical_payload)
            orchestrator = IngestionOrchestrator(control=saga_service, external_volume=external, snapshot_storage=storage, acquisition=acquisition, parser=parser, validator=validator, publication=publication, clock=clock, token_bytes=lambda count: bytes.fromhex(canonical_sha256({"step16": projection.snapshot_id}))[:count])
            completed = orchestrator.execute(context, saga, projection)
            if completed.current_milestone is not SagaMilestone.PUBLISHED:
                raise ValidationFailure("STEP16_SAGA_NOT_PUBLISHED")
            replayed = orchestrator.execute(context, saga, projection)
            if replayed.run_digest != completed.run_digest:
                raise ValidationFailure("STEP16_EXACT_REPLAY_FAILED")
            if transport.put_attempts or client.operation_counts.get("put-object", 0):
                raise ValidationFailure("STEP16_VALIDATION_S3_WRITE_FORBIDDEN")
            parse = parsing_service.get_by_saga(context, tenant_id=record.tenant_id, saga_id=saga.saga_id)
            if parse is None or not parse.chunks or not parse.sections:
                raise ValidationFailure("STEP16_PARSED_COVERAGE_MISSING")
            events = saga_service.verify_event_chain(context, tenant_id=record.tenant_id, saga_id=saga.saga_id)
            publication_events = source_service.verify_publication_event_chain(context, tenant_id=record.tenant_id, source_id=record.source_id, hat_scope_id=record.hat_scope_id)
            if len(events) != 8 or len(publication_events) != 3:
                raise ValidationFailure("STEP16_EVENT_CHAIN_INCOMPLETE")
            try:
                source_service.register_source(RequestContext("step16-isolation", None, AccessMode.TENANT_SHARED), record, operation_id="step16-cross-tenant", idempotency_key="step16-cross-tenant")
            except Exception:
                cross_tenant_rejected = True
            else:
                cross_tenant_rejected = False
            if not cross_tenant_rejected:
                raise ValidationFailure("STEP16_CROSS_TENANT_NOT_REJECTED")
            state = migrations.one_value(root.execute(database, "SELECT current_publication_state FROM memory_patch.source_registry_entries WHERE tenant_id=" + migrations.sql_literal(record.tenant_id) + " AND source_id=" + migrations.sql_literal(record.source_id)))
            if state != "PUBLISHED":
                raise ValidationFailure("STEP16_TRUSTED_PUBLICATION_STATE_MISSING")
            deletes = int(migrations.one_value(root.execute(database, "SELECT count(*) FROM information_schema.table_privileges WHERE table_schema='memory_patch' AND grantee='mp_app_runtime' AND table_name IN ('source_registry_entries','source_publication_events','ingestion_sagas','knowledge_versions','knowledge_chunks') AND privilege_type='DELETE'")))
            if deletes:
                raise ValidationFailure("STEP16_RUNTIME_DELETE_GRANT_DETECTED")
            if {"HAT", "MODEL"} & {member.value for member in SourceRegistryActorType}:
                raise ValidationFailure("STEP16_FORBIDDEN_PUBLICATION_ACTOR")
            operation_counts = dict(client.operation_counts)
        if not children.all_reaped:
            raise ValidationFailure("OWNED_SQL_CHILD_PROCESS_REMAINS")
        migrations.drop_database(root, database, timeout=300)
        database = None
        _drop_login_role(root, role)
        role = None
        cleanup = runtime.graceful_stop_and_remove(
            root,
            owned_children_reaped=children.all_reaped,
        )
        root = None
        if cleanup["cleanup_errors"] or cleanup["force_kill_used"] or not all(cleanup[key] for key in ("pid_exited", "ports_closed", "temporary_store_removed", "drain_command_completed")):
            raise ValidationFailure("DISPOSABLE_RUNTIME_CLEANUP_FAILED")
        evidence = {
            "schema_version": "1.0.0",
            "step": "STEP_16_GERMAN_LAW_HAT_PUBLICATION_CORPUS_VERIFICATION_1A",
            "verdict": "PASS",
            "repository": {"starting_head": head, "worktree_digest": worktree_digest},
            "inputs": {"device_reference": volume.device_reference, "step14_manifest_digest": verified14["manifest_digest"], "step15_manifest_digest": verified15["manifest_digest"], "step16_manifest_digest": verified16["manifest_digest"], "publication_run_id": verified16["run_id"]},
            "representative_projection": {"publication_item_id": selection.item["publication_item_id"], "projection_binding_digest": selection.projection_binding["binding_digest"], "raw_binding_digest": selection.raw_binding["binding_digest"], "s3_version_id": selection.projection_binding["version_id"], "parse_artifact_digest": parse.document.parse_artifact_digest, "sections": len(parse.sections), "chunks": len(parse.chunks)},
            "trusted_state_machine": {"saga_milestone": completed.current_milestone.value, "saga_event_count": len(events), "publication_event_count": len(publication_events), "exact_replay": "PASS", "cross_tenant": "REJECTED", "hat_or_model_publish_actor": "NOT_REPRESENTABLE"},
            "s3": {"existing_exact_snapshot_reconciled": True, "new_s3_writes": 0, "put_attempts": transport.put_attempts, "operation_counts": operation_counts, "object_lock_verified": True, "aws_identity": aws_identity},
            "database": {"exact_server_version": migrations.PINNED_VERSION, "binary_sha256": binary_identity["binary_sha256"], "migration_first_application": "PASS", "migration_replay": "PASS_NOOP", "step9_security_digest": security9["security_digest"], "step10_security_digest": security10["security_digest"], "step11_security_digest": security11["security_digest"], "runtime_delete_grants": deletes, "persistent_database": False, "persistent_login_roles": 0},
            "cleanup": {"drain_completed": bool(cleanup["drain_command_completed"]), "force_kill_used": bool(cleanup["force_kill_used"]), "pid_exited": bool(cleanup["pid_exited"]), "ports_closed": bool(cleanup["ports_closed"]), "temporary_store_removed": bool(cleanup["temporary_store_removed"]), "shutdown_method": cleanup["shutdown_method"]},
            "boundaries": {"raw_corpus_writes": 0, "source_tree_writes": 0, "s3_deletes": 0, "aws_writes_outside_approved_s3_boundary": 0, "model_calls": 0, "network_acquisitions": 0, "step17_started": False, "raw_corpus_body_in_git": False},
        }
        digest = _write_evidence(args.evidence_output, evidence)
        print(json.dumps({"status": "PASS", "evidence_digest": digest, "force_kill_used": False, "new_s3_writes": 0}, sort_keys=True))
        return 0
    except (ValidationFailure, OSError, ValueError, migrations.MigrationError) as exc:
        code = exc.code if isinstance(exc, ValidationFailure) else type(exc).__name__.upper()
        print(json.dumps({"status": "FAILED", "reason": code}, sort_keys=True), file=sys.stderr)
        return 1
    finally:
        if runtime is not None and root is not None:
            try:
                if database is not None:
                    migrations.drop_database(root, database, timeout=300)
                if role is not None:
                    _drop_login_role(root, role)
                runtime.graceful_stop_and_remove(root, owned_children_reaped=False)
            except Exception:
                print(json.dumps({"status": "FAILED", "reason": "CLEANUP_EXCEPTION"}, sort_keys=True), file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
