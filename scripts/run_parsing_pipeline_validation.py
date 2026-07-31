#!/usr/bin/env python3
"""Plan or run the zero-external-write Step 11 parsing validation.

The live mode reconciles the exact existing Step 10 external-volume artifact
and S3 version through read-only wrappers. Its only durable writes are one
canonical repository evidence file; all SQL writes live in an owned,
loopback-only, in-memory CockroachDB runtime that is removed afterward.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence


sys.dont_write_bytecode = True
SCRIPT_ROOT = Path(__file__).resolve().parent
REPOSITORY_ROOT = SCRIPT_ROOT.parent
SOURCE_ROOT = REPOSITORY_ROOT / "src"
for import_root in (SCRIPT_ROOT, SOURCE_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

import run_cockroachdb_migrations as migrations  # noqa: E402
import run_ingestion_saga_validation as step10  # noqa: E402
from aws_cli_s3_client import AwsCliS3Client  # noqa: E402
from cockroach_cli_dbapi import OwnedChildRegistry, connection_factory  # noqa: E402
from aioa_memory_kernel.contracts.serialization import (  # noqa: E402
    canonical_json_bytes,
    canonical_sha256,
)
from aioa_memory_kernel.ingestion import (  # noqa: E402
    IngestionOrchestrator,
    IngestionSagaService,
    SagaMilestone,
    Step9PublicationPort,
    build_initial_saga,
)
from aioa_memory_kernel.parsing import (  # noqa: E402
    CHUNKING_PROFILE_NAME,
    CHUNKING_PROFILE_VERSION,
    NORMALIZATION_PROFILE_NAME,
    NORMALIZATION_PROFILE_VERSION,
    SECURITY_RULESET_NAME,
    SECURITY_RULESET_VERSION,
    CockroachParsingArtifactRepository,
    GenericParseArtifactValidatorPort,
    GenericParsingPipeline,
    GenericParsingPipelinePort,
    ParsingRequest,
    ParsingPersistenceService,
)
from aioa_memory_kernel.persistence import (  # noqa: E402
    AccessMode,
    IdempotencyConflictError,
    RequestContext,
    SerializableTransactionRunner,
)
from aioa_memory_kernel.sources import (  # noqa: E402
    ParserIdentity,
    SourcePublicationState,
    SourceRegistryService,
    TransformationIdentity,
)
from aioa_memory_kernel.storage import (  # noqa: E402
    EXTERNAL_VOLUME_PROJECT_ID,
    ExternalVolumeOperation,
    S3SnapshotAdapter,
)


EXPECTED_HEAD = "e9a4416e67c99718b47dac354c73fe393881be15"
EXPECTED_REMOTE = (
    "https://github.com/luciferprosun/"
    "Memory-Patch-for-AIOA-Hackathon-CockroachDB.git"
)
EXPECTED_BRANCH = "main"
PINNED_BINARY_SHA256 = step10.PINNED_BINARY_SHA256
AWS_PROFILE = step10.AWS_PROFILE
AWS_REGION = step10.AWS_REGION
S3_BUCKET = step10.S3_BUCKET
S3_VERSION_ID = step10.PRESERVED_S3_VERSION_ID
FIXTURE_SHA256 = step10.FIXTURE_SHA256
FIXTURE_LENGTH = step10.FIXTURE_LENGTH
DEFAULT_EXTERNAL_CONFIG = step10.DEFAULT_EXTERNAL_CONFIG
DEFAULT_EVIDENCE_OUTPUT = (
    REPOSITORY_ROOT
    / "docs"
    / "evidence"
    / "parsing"
    / "step11-parsing-pipeline-validation.json"
)
DEFAULT_FAILURE_OUTPUT = (
    REPOSITORY_ROOT
    / "docs"
    / "evidence"
    / "parsing"
    / "step11-parsing-pipeline-validation-failure.json"
)
DEFAULT_RECOVERY_FAILURE_OUTPUT = (
    REPOSITORY_ROOT
    / "docs"
    / "evidence"
    / "parsing"
    / "step11-parsing-pipeline-validation-recovery-failure.json"
)
DEFAULT_RECOVERY_2_FAILURE_OUTPUT = (
    REPOSITORY_ROOT
    / "docs"
    / "evidence"
    / "parsing"
    / "step11-parsing-pipeline-validation-recovery-2-failure.json"
)
DEFAULT_RECOVERY_3_FAILURE_OUTPUT = (
    REPOSITORY_ROOT
    / "docs"
    / "evidence"
    / "parsing"
    / "step11-parsing-pipeline-validation-recovery-3-failure.json"
)
DEFAULT_RECOVERY_4_FAILURE_OUTPUT = (
    REPOSITORY_ROOT
    / "docs"
    / "evidence"
    / "parsing"
    / "step11-parsing-pipeline-validation-recovery-4-failure.json"
)
DEFAULT_RECOVERY_5_FAILURE_OUTPUT = (
    REPOSITORY_ROOT
    / "docs"
    / "evidence"
    / "parsing"
    / "step11-parsing-pipeline-validation-recovery-5-failure.json"
)
DEFAULT_RECOVERY_6_FAILURE_OUTPUT = (
    REPOSITORY_ROOT
    / "docs"
    / "evidence"
    / "parsing"
    / "step11-parsing-pipeline-validation-recovery-6-failure.json"
)
LIVE_MIGRATION_TIMEOUT_SECONDS = 300
LIVE_TRANSACTION_TIMEOUT_SECONDS = 180
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_SAFE_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")


class Step11ValidationError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        sanitized_code: str = "STEP11_VALIDATION_FAILED",
    ) -> None:
        super().__init__(message)
        self.sanitized_code = sanitized_code


def _git(arguments: Sequence[str]) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=REPOSITORY_ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )
    if completed.returncode != 0:
        raise Step11ValidationError("repository inspection failed")
    return completed.stdout.strip()


def _repository_facts() -> dict[str, object]:
    top = Path(_git(("rev-parse", "--show-toplevel"))).resolve()
    remote = _git(("remote", "get-url", "origin"))
    branch = _git(("branch", "--show-current"))
    head = _git(("rev-parse", "HEAD"))
    origin = _git(("rev-parse", "origin/main"))
    ahead_behind = [
        int(value)
        for value in _git(("rev-list", "--left-right", "--count", "origin/main...HEAD")).split()
    ]
    if (
        top != REPOSITORY_ROOT.resolve()
        or remote.rstrip("/") != EXPECTED_REMOTE.rstrip("/")
        or branch != EXPECTED_BRANCH
        or head != EXPECTED_HEAD
        or origin != EXPECTED_HEAD
        or ahead_behind != [0, 0]
        or _git(("ls-files", "-u"))
    ):
        raise Step11ValidationError("repository identity differs from the Step 11 baseline")
    status = _git(("status", "--short"))
    paths = []
    for line in status.splitlines():
        path = line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        paths.append(path)
    files: list[dict[str, object]] = []
    for relative in sorted(set(paths)):
        candidate = REPOSITORY_ROOT / relative
        files.append(
            {
                "path": relative,
                "sha256": (
                    hashlib.sha256(candidate.read_bytes()).hexdigest()
                    if candidate.is_file() and not candidate.is_symlink()
                    else None
                ),
            }
        )
    fingerprint = canonical_sha256(
        {"head": head, "origin_main": origin, "status": status, "files": files}
    )
    return {
        "ahead_behind": ahead_behind,
        "branch": branch,
        "changed_file_count": len(files),
        "head": head,
        "origin_main": origin,
        "remote": remote,
        "worktree_digest": fingerprint,
    }


def _build_bundle(captured_at: datetime, retain_until: datetime):
    base = step10.build_validation_bundle(captured_at, retain_until)
    parser = ParserIdentity(
        "generic-canonical-json-document-parser",
        "1.0.0",
        "1.0.0",
    )
    transformation = TransformationIdentity(
        "unicode-nfc-json-canonicalization",
        "1.0.0",
        "1.0.0",
    )
    source_artifact = dataclasses.replace(
        base.source_record.artifact,
        parser=parser,
        transformation=transformation,
    )
    source_record = dataclasses.replace(
        base.source_record,
        parser=parser,
        transformation=transformation,
        artifact=source_artifact,
        registry_digest="",
    )
    saga = build_initial_saga(
        tenant_id=base.saga.tenant_id,
        source_id=base.saga.source_id,
        hat_scope_id=base.saga.hat_scope_id,
        owner_user_id=None,
        knowledge_version_id=base.saga.knowledge_version_id,
        idempotency_key=(
            "step11-live-1a:" + str(base.ids["namespace"])
        ),
        scope_digest=base.saga.scope_digest,
        source_registry_digest=source_record.registry_digest,
        content_sha256=base.saga.content_sha256,
        content_length=base.saga.content_length,
        media_type=base.saga.media_type,
        local_relative_path=base.saga.local_relative_path,
        snapshot_id=base.saga.snapshot_id,
        captured_at=base.saga.captured_at,
        retain_until=base.saga.retain_until,
        created_at=captured_at,
    )
    return step10.ValidationBundle(
        ids=base.ids,
        snapshot=base.snapshot,
        source_record=source_record,
        saga=saga,
        storage_config=base.storage_config,
        storage_plan=base.storage_plan,
    )


def _seed_sql(bundle: object) -> str:
    sql = step10._seed_sql(bundle)
    preview = GenericParsingPipeline().parse(
        ParsingRequest(
            tenant_id=bundle.saga.tenant_id,
            owner_user_id=bundle.saga.owner_user_id,
            saga_id=bundle.saga.saga_id,
            source_id=bundle.saga.source_id,
            snapshot_id=bundle.saga.snapshot_id,
            knowledge_version_id=bundle.saga.knowledge_version_id,
            knowledge_version_ordinal=1,
            hat_scope_id=bundle.saga.hat_scope_id,
            s3_version_id=S3_VERSION_ID,
            locked_storage_evidence_digest="0" * 64,
            input_sha256=bundle.saga.content_sha256,
            input_byte_length=bundle.saga.content_length,
            media_type=bundle.saga.media_type,
            completed_at=bundle.saga.created_at,
        ),
        bundle.snapshot.canonical_payload,
    )
    document = preview.document
    normalization_profile = (
        f"{document.normalization_profile}:{document.normalization_version}"
    )
    old_binding = (
        f", 1, {migrations.sql_literal(bundle.snapshot.content_sha256)}, "
        "'exact-byte-synthetic-validation-placeholder-v1'"
    )
    new_binding = (
        f", 1, {migrations.sql_literal(document.normalized_content_sha256)}, "
        f"{migrations.sql_literal(normalization_profile)}"
    )
    old_provenance = step10._sql_json({"step11_implemented": False})
    new_provenance = step10._sql_json(
        {
            "normalization_profile": normalization_profile,
            "parser_contract_version": document.parser_contract_version,
            "step": "STEP_11_GENERIC_PARSING_PIPELINE_1A",
        }
    )
    if sql.count(old_binding) != 1 or sql.count(old_provenance) != 1:
        raise Step11ValidationError("Step 10 seed reservation shape changed")
    return (
        sql.replace(old_binding, new_binding)
        .replace(old_provenance, new_provenance)
        .replace("Step10 Validation", "Step11 Validation")
    )


def _resolve_output(path: Path) -> Path:
    candidate = path if path.is_absolute() else REPOSITORY_ROOT / path
    resolved = candidate.resolve()
    try:
        resolved.relative_to(REPOSITORY_ROOT)
    except ValueError as exc:
        raise Step11ValidationError("evidence output must remain in the repository") from exc
    return resolved


def _delete_marker_count(aws_binary: Path, object_key: str) -> int:
    value = step10._aws_json(
        aws_binary,
        "s3api",
        "list-object-versions",
        ("--bucket", S3_BUCKET, "--prefix", object_key),
    )
    markers = value.get("DeleteMarkers", [])
    if not isinstance(markers, list):
        raise Step11ValidationError("S3 delete-marker response is malformed")
    return sum(
        1
        for item in markers
        if isinstance(item, Mapping) and item.get("Key") == object_key
    )


def prepare_plan(
    *,
    cockroach_binary: Path,
    aws_binary: Path,
    captured_at: datetime,
    retain_until: datetime,
    external_config: Path,
    evidence_output: Path,
    failure_evidence_output: Path,
) -> tuple[dict[str, object], object, object]:
    repository = _repository_facts()
    binary = cockroach_binary.expanduser().resolve()
    identity = migrations.verify_binary_identity(binary)
    if identity["binary_sha256"] != PINNED_BINARY_SHA256:
        raise Step11ValidationError("CockroachDB binary digest differs")
    aws = step10._verify_aws_binary(aws_binary)
    aws_sha256 = step10._file_sha256(aws)
    aws_identity = step10._aws_identity(aws)
    bundle = _build_bundle(captured_at, retain_until)
    adapter, external = step10._external_recovery_preflight(
        bundle,
        external_config,
    )
    with tempfile.TemporaryDirectory(prefix="mp-step11-s3-read-") as raw:
        _, _, s3 = step10._s3_recovery_preflight(
            bundle,
            aws,
            aws_sha256,
            Path(raw),
            S3_VERSION_ID,
        )
    delete_markers = _delete_marker_count(aws, bundle.storage_plan.object_key)
    if delete_markers != 0:
        raise Step11ValidationError("exact S3 key has a delete marker")
    output = _resolve_output(evidence_output)
    failed_output = _resolve_output(failure_evidence_output)
    preserved_failure_paths = tuple(
        _resolve_output(path)
        for path in (
            DEFAULT_FAILURE_OUTPUT,
            DEFAULT_RECOVERY_FAILURE_OUTPUT,
            DEFAULT_RECOVERY_2_FAILURE_OUTPUT,
            DEFAULT_RECOVERY_3_FAILURE_OUTPUT,
            DEFAULT_RECOVERY_4_FAILURE_OUTPUT,
            DEFAULT_RECOVERY_5_FAILURE_OUTPUT,
            DEFAULT_RECOVERY_6_FAILURE_OUTPUT,
        )
    )
    if os.path.lexists(output):
        raise Step11ValidationError("Step 11 evidence output already exists")
    if output == failed_output or failed_output in preserved_failure_paths:
        raise Step11ValidationError(
            "success, recovery-failure, and preserved evidence must be distinct"
        )
    if os.path.lexists(failed_output):
        raise Step11ValidationError("Step 11 recovery-failure output already exists")
    previous_attempts: list[dict[str, object]] = []
    for preserved_failure in preserved_failure_paths:
        if not preserved_failure.exists():
            continue
        if not preserved_failure.is_file() or preserved_failure.is_symlink():
            raise Step11ValidationError(
                "preserved Step 11 failure evidence is not a regular file"
            )
        try:
            preserved_value = json.loads(
                preserved_failure.read_text(encoding="utf-8")
            )
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise Step11ValidationError(
                "preserved Step 11 failure evidence is malformed"
            ) from exc
        if not isinstance(preserved_value, Mapping) or not _DIGEST.fullmatch(
            str(preserved_value.get("evidence_digest", ""))
        ):
            raise Step11ValidationError(
                "preserved Step 11 failure evidence digest is malformed"
            )
        previous_attempts.append(
            {
                "evidence_digest": preserved_value["evidence_digest"],
                "file_sha256": hashlib.sha256(
                    preserved_failure.read_bytes()
                ).hexdigest(),
                "path": str(preserved_failure.relative_to(REPOSITORY_ROOT)),
                "status": "PRESERVED_FAILED_ATTEMPT",
            }
        )
    suffix = str(bundle.ids["namespace"])[:16]
    facts: dict[str, object] = {
        "schema_version": "1.0.0",
        "step": "Step 11 - Generic Parsing, Normalization and Chunking Pipeline 1A",
        "mode": "ZERO_EXTERNAL_WRITE_LIVE_VALIDATION",
        "repository": repository,
        "aws": {
            **dict(aws_identity),
            "bucket": S3_BUCKET,
            "cli_binary_sha256": aws_sha256,
            "delete_markers": 0,
            "new_writes": 0,
            "object_key": bundle.storage_plan.object_key,
            "payload_length": FIXTURE_LENGTH,
            "payload_sha256": FIXTURE_SHA256,
            "retention_changes": 0,
            "retain_until": retain_until.isoformat(),
            "version_count": 1,
            "version_id": S3_VERSION_ID,
        },
        "external_volume": {**dict(external), "new_writes": 0},
        "cockroachdb": {
            "binary_sha256": identity["binary_sha256"],
            "database": f"mp_step11_{suffix}_live",
            "exact_server_version": identity["build_tag"],
            "external_io": "DISABLED",
            "login_role": f"mp_step11_{suffix}_app",
            "loopback_only": True,
            "migration_range": "0001-0008",
            "per_migration_timeout_seconds": LIVE_MIGRATION_TIMEOUT_SECONDS,
            "per_transaction_timeout_seconds": LIVE_TRANSACTION_TIMEOUT_SECONDS,
            "persistent_data": False,
            "runtime_mode": "DISPOSABLE_LOCAL_SINGLE_NODE",
            "shutdown_method": "NODE_DRAIN_SELF_ON_RPC_WITH_SHUTDOWN",
            "store": "type=mem,size=640MiB",
        },
        "profiles": {
            "chunking": f"{CHUNKING_PROFILE_NAME}:{CHUNKING_PROFILE_VERSION}",
            "json_parser": "generic-canonical-json-document-parser:1.0.0",
            "normalization": f"{NORMALIZATION_PROFILE_NAME}:{NORMALIZATION_PROFILE_VERSION}",
            "security_ruleset": f"{SECURITY_RULESET_NAME}:{SECURITY_RULESET_VERSION}",
        },
        "synthetic_scope": {
            "hat_scope_id": bundle.ids["hat_scope"],
            "knowledge_version_id": bundle.ids["knowledge_version"],
            "saga_id": bundle.saga.saga_id,
            "snapshot_id": bundle.snapshot.snapshot_id,
            "source_id": bundle.ids["source"],
            "tenant_id": bundle.ids["tenant"],
        },
        "expected_database_rows": {
            "ingestion_external_effects": 7,
            "ingestion_saga_events": 8,
            "ingestion_sagas": 1,
            "knowledge_chunks": 3,
            "knowledge_versions": 1,
            "parse_security_findings": 0,
            "parsed_documents": 1,
            "parsed_sections": 3,
            "persistence_operations": 6,
            "source_publication_events": 3,
            "source_registry_entries": 1,
        },
        "safety": {
            "deletions": 0,
            "external_volume_writes": 0,
            "german_law_data": False,
            "model_calls": False,
            "new_s3_writes": 0,
            "personal_data": False,
            "retention_changes": 0,
            "step12_started": False,
        },
        "evidence_output": str(output.relative_to(REPOSITORY_ROOT)),
        "failure_evidence_output": str(failed_output.relative_to(REPOSITORY_ROOT)),
        "previous_attempts": previous_attempts,
    }
    plan_digest = canonical_sha256(facts)
    command = [
        "python3",
        "scripts/run_parsing_pipeline_validation.py",
        "--validate-existing",
        "--cockroach-binary",
        str(binary),
        "--aws-binary",
        str(aws),
        "--captured-at",
        captured_at.isoformat(),
        "--retain-until",
        retain_until.isoformat(),
        "--evidence-output",
        str(output.relative_to(REPOSITORY_ROOT)),
        "--failure-evidence-output",
        str(failed_output.relative_to(REPOSITORY_ROOT)),
        "--confirm-project",
        EXTERNAL_VOLUME_PROJECT_ID,
        "--confirm-device-reference",
        str(external["device_reference"]),
        "--confirm-external-relative-path",
        str(external["relative_path"]),
        "--confirm-payload-sha256",
        FIXTURE_SHA256,
        "--confirm-bucket",
        S3_BUCKET,
        "--confirm-object-key",
        bundle.storage_plan.object_key,
        "--confirm-version-id",
        S3_VERSION_ID,
        "--confirm-plan-digest",
        plan_digest,
    ]
    for previous_attempt in previous_attempts:
        previous_sha256 = previous_attempt["file_sha256"]
        command.extend(
            (
                "--confirm-preserved-failure-evidence-sha256",
                str(previous_sha256),
            )
        )
    return ({**facts, "plan_digest": plan_digest, "exact_command_argv": command}, bundle, adapter)


def _parsing_counts(
    root: migrations.SqlClient,
    database: str,
    tenant_id: str,
    saga_id: str,
    document_id: str,
    knowledge_version_id: str,
) -> dict[str, int]:
    q = migrations.sql_literal
    output = root.execute(
        database,
        "SELECT "
        "(SELECT count(*) FROM memory_patch.parsed_documents "
        f"WHERE tenant_id={q(tenant_id)} AND saga_id={q(saga_id)}) AS parsed_documents, "
        "(SELECT count(*) FROM memory_patch.parsed_sections "
        f"WHERE tenant_id={q(tenant_id)} AND document_id={q(document_id)}) AS parsed_sections, "
        "(SELECT count(*) FROM memory_patch.parse_security_findings "
        f"WHERE tenant_id={q(tenant_id)} AND document_id={q(document_id)}) AS parse_security_findings, "
        "(SELECT count(*) FROM memory_patch.knowledge_versions "
        f"WHERE tenant_id={q(tenant_id)} AND knowledge_version_id={q(knowledge_version_id)}) AS knowledge_versions, "
        "(SELECT count(*) FROM memory_patch.knowledge_chunks "
        f"WHERE tenant_id={q(tenant_id)} AND knowledge_version_id={q(knowledge_version_id)}) AS knowledge_chunks, "
        "(SELECT count(*) FROM memory_patch.chunk_search_documents "
        f"WHERE tenant_id={q(tenant_id)} AND knowledge_version_id={q(knowledge_version_id)}) AS chunk_search_documents",
    )
    rows = migrations.parse_tsv(output)
    if len(rows) != 1:
        raise Step11ValidationError("parse row-count probe differs")
    return {key: int(value) for key, value in rows[0].items()}


def _effect_evidence(
    root: migrations.SqlClient,
    database: str,
    tenant_id: str,
    saga_id: str,
    effect_kind: str,
) -> Mapping[str, object]:
    q = migrations.sql_literal
    rows = migrations.parse_tsv(
        root.execute(
            database,
            "SELECT evidence FROM memory_patch.ingestion_external_effects "
            f"WHERE tenant_id={q(tenant_id)} AND saga_id={q(saga_id)} "
            f"AND effect_kind={q(effect_kind)} AND status='RECEIPT_RECORDED'",
        )
    )
    if len(rows) != 1:
        raise Step11ValidationError("expected external receipt evidence is missing")
    try:
        value = json.loads(str(rows[0]["evidence"]))
    except (KeyError, json.JSONDecodeError) as exc:
        raise Step11ValidationError("external receipt evidence is malformed") from exc
    if not isinstance(value, Mapping):
        raise Step11ValidationError("external receipt evidence is malformed")
    return value


def _canonical_evidence(value: Mapping[str, object]) -> dict[str, object]:
    result = json.loads(canonical_json_bytes(value))
    if not isinstance(result, dict):
        raise Step11ValidationError("evidence must be a canonical JSON object")
    result["evidence_digest"] = canonical_sha256(result)
    return result


def _failure_code(error: BaseException) -> str:
    if isinstance(error, migrations.MigrationError) and re.search(
        r"subprocess exceeded \d+(?:\.\d+)? seconds",
        str(error),
    ):
        return "COCKROACHDB_MIGRATION_TIMEOUT"
    return step10._sanitized_failure(error)


def _failure_chain(error: BaseException | None) -> list[dict[str, object]]:
    """Return bounded structured failure metadata without exception text."""

    result: list[dict[str, object]] = []
    current = error
    observed: set[int] = set()
    while current is not None and len(result) < 6 and id(current) not in observed:
        observed.add(id(current))
        item: dict[str, object] = {"type": type(current).__name__}
        code = getattr(current, "sanitized_code", None)
        if isinstance(code, str) and _SAFE_CODE.fullmatch(code):
            item["sanitized_code"] = code
        sqlstate = getattr(current, "sqlstate", None)
        if isinstance(sqlstate, str) and re.fullmatch(r"[0-9A-Z]{5}", sqlstate):
            item["sqlstate"] = sqlstate
        operation = getattr(current, "operation_kind", None)
        if (
            isinstance(operation, str)
            and len(operation) <= 128
            and _SAFE_CODE.fullmatch(operation)
        ):
            item["operation_kind"] = operation
        result.append(item)
        current = current.__cause__
    return result


def execute_validation(
    *,
    plan: Mapping[str, object],
    bundle: object,
    external_adapter: object,
    cockroach_binary: Path,
    aws_binary: Path,
    evidence_output: Path,
    failure_output: Path,
) -> Mapping[str, object]:
    cockroach = plan["cockroachdb"]
    assert isinstance(cockroach, Mapping)
    database = str(cockroach["database"])
    role = str(cockroach["login_role"])
    runtime = migrations.LocalRuntime(
        binary=cockroach_binary,
        run_id="mp_step11_" + str(bundle.ids["namespace"])[:12],
    )
    child_registry = OwnedChildRegistry()
    root: migrations.SqlClient | None = None
    database_created = False
    role_created = False
    cleanup_errors: list[str] = []
    cleanup: dict[str, Any] = {
        "pid_exited": False,
        "ports_closed": False,
        "temporary_store_removed": False,
    }
    validation: dict[str, object] | None = None
    failure: BaseException | None = None
    volume: object | None = None
    aws_client: AwsCliS3Client | None = None
    readonly_s3: object | None = None
    failure_progress: dict[str, object] = {"status": "NOT_AVAILABLE"}
    phase = "START_RUNTIME"
    try:
        root = runtime.start()
        phase = "VERIFY_SERVER_IDENTITY"
        server_version = migrations.one_value(root.execute("defaultdb", "SELECT version()"))
        cluster_version = migrations.one_value(
            root.execute("defaultdb", "SHOW CLUSTER SETTING version")
        )
        if migrations.PINNED_VERSION not in server_version or cluster_version != "26.2":
            raise Step11ValidationError("live CockroachDB identity differs")
        phase = "CREATE_DATABASE"
        migrations.create_database(root, database)
        database_created = True
        phase = "APPLY_MIGRATIONS"
        first_apply = migrations.apply_migrations(
            root,
            database,
            timeout=LIVE_MIGRATION_TIMEOUT_SECONDS,
        )
        phase = "REPLAY_MIGRATIONS"
        replay_apply = migrations.apply_migrations(
            root,
            database,
            timeout=LIVE_MIGRATION_TIMEOUT_SECONDS,
        )
        if first_apply["applied_count"] != 8 or replay_apply != {
            "applied": [],
            "applied_count": 0,
            "skipped": [item.migration_id for item in migrations.load_migrations()],
            "skipped_count": 8,
        }:
            raise Step11ValidationError("migration application or replay differs")
        phase = "VERIFY_DATABASE_CATALOG"
        catalog = migrations.schema_catalog(root, database)
        migrations.assert_catalog(catalog)
        step9_security = migrations.assert_step9_security_catalog(root, database)
        step10_security = migrations.assert_step10_security_catalog(root, database)
        step11_security = migrations.assert_step11_security_catalog(root, database)
        phase = "CREATE_RUNTIME_ROLE"
        step10._create_login_role(root, role)
        role_created = True
        phase = "SEED_SYNTHETIC_SCOPE"
        root.execute(database, _seed_sql(bundle), timeout=180)
        if runtime.runtime_dir is None or runtime.sql_port is None:
            raise Step11ValidationError("owned CockroachDB runtime facts are absent")
        factory = connection_factory(
            binary=cockroach_binary,
            port=runtime.sql_port,
            database=database,
            user=role,
            log_directory=runtime.runtime_dir,
            child_registry=child_registry,
            timeout_seconds=LIVE_TRANSACTION_TIMEOUT_SECONDS,
        )
        clock = step10.ValidationClock(bundle.snapshot.captured_at)
        runner = SerializableTransactionRunner(factory)
        context = RequestContext(bundle.ids["tenant"], None, AccessMode.TENANT_SHARED)
        source_service = SourceRegistryService(runner, clock=clock)
        registered = source_service.register_source(
            context,
            bundle.source_record,
            operation_id="step11-source-register-" + str(bundle.ids["namespace"])[:32],
            idempotency_key="step11-source-register:" + str(bundle.ids["namespace"]),
        )
        if registered.registry_digest != bundle.source_record.registry_digest:
            raise Step11ValidationError("Step 9 source registration differs")
        eligibility = source_service.evaluate_publication_eligibility(
            context,
            tenant_id=bundle.ids["tenant"],
            source_id=bundle.ids["source"],
            hat_scope_id=bundle.ids["hat_scope"],
            evaluated_at=clock(),
        )
        if not eligibility.eligible:
            raise Step11ValidationError("synthetic source is not publication eligible")
        saga_service = IngestionSagaService(runner, clock=clock)
        volume = step10.CountingExternalVolume(external_adapter, writes_allowed=False)
        aws_section = plan["aws"]
        assert isinstance(aws_section, Mapping)
        aws_client = AwsCliS3Client(
            aws_binary=aws_binary,
            profile=AWS_PROFILE,
            region=AWS_REGION,
            temporary_directory=runtime.runtime_dir,
            expected_binary_sha256=str(aws_section["cli_binary_sha256"]),
        )
        readonly_s3 = step10.RecoveryReadOnlyS3Client(aws_client)
        storage = S3SnapshotAdapter(bundle.storage_config, readonly_s3)
        persistence = ParsingPersistenceService(
            runner,
            repository=CockroachParsingArtifactRepository(),
        )
        parser = GenericParsingPipelinePort(
            snapshot_storage=storage,
            snapshot_resolver=lambda saga: bundle.snapshot,
            persistence=persistence,
            clock=clock,
        )
        validator = GenericParseArtifactValidatorPort(
            persistence=persistence,
            clock=clock,
        )
        orchestrator = IngestionOrchestrator(
            control=saga_service,
            external_volume=volume,
            snapshot_storage=storage,
            acquisition=step10.ReconciliationOnlyAcquisition(),
            parser=parser,
            validator=validator,
            publication=Step9PublicationPort(source_service, clock=clock),
            clock=clock,
            token_bytes=lambda count: bytes.fromhex(str(bundle.ids["namespace"]))[:count],
        )
        before_versions = aws_client.list_object_versions(
            bucket=S3_BUCKET,
            key=bundle.storage_plan.object_key,
        )
        phase = "EXECUTE_INGESTION_AND_PARSE"
        completed = orchestrator.execute(context, bundle.saga, bundle.snapshot)
        if completed.current_milestone is not SagaMilestone.PUBLISHED:
            raise Step11ValidationError("Step 11 saga did not reach PUBLISHED")
        events = saga_service.verify_event_chain(
            context,
            tenant_id=completed.tenant_id,
            saga_id=completed.saga_id,
        )
        lock_digest = _effect_evidence(
            root, database, completed.tenant_id, completed.saga_id,
            "PARSE",
        ).get("locked_storage_evidence_digest")
        if not isinstance(lock_digest, str) or not _DIGEST.fullmatch(lock_digest):
            artifact_record = persistence.get_by_saga(
                context,
                tenant_id=completed.tenant_id,
                saga_id=completed.saga_id,
            )
            if artifact_record is None:
                raise Step11ValidationError("persisted parse artifact is missing")
            lock_digest = artifact_record.document.locked_storage_evidence_digest
        parsed = parser.reconcile(
            completed,
            s3_version_id=S3_VERSION_ID,
            locked_storage_evidence_digest=lock_digest,
        )
        if parsed is None or parsed.synthetic_validation_boundary:
            raise Step11ValidationError("real parser receipt did not reconcile")
        validated = validator.reconcile(completed, parsed)
        if validated is None or not validated.accepted or validated.synthetic_validation_boundary:
            raise Step11ValidationError("real validator receipt did not reconcile")
        stored_artifact = persistence.get_by_saga(
            context,
            tenant_id=completed.tenant_id,
            saga_id=completed.saga_id,
        )
        if stored_artifact is None:
            raise Step11ValidationError("persisted parse artifact is missing")
        parse_counts = _parsing_counts(
            root,
            database,
            completed.tenant_id,
            completed.saga_id,
            stored_artifact.document.document_id,
            completed.knowledge_version_id,
        )
        expected_parse_counts = {
            "chunk_search_documents": 0,
            "knowledge_chunks": 3,
            "knowledge_versions": 1,
            "parse_security_findings": 0,
            "parsed_documents": 1,
            "parsed_sections": 3,
        }
        if parse_counts != expected_parse_counts:
            raise Step11ValidationError("Step 11 durable row counts differ")
        counts_before = step10._row_counts(
            root, database, completed.tenant_id, completed.saga_id, completed.source_id
        )
        expected_saga_counts = {
            "ingestion_external_effects": 7,
            "ingestion_orphans": 0,
            "ingestion_saga_events": 8,
            "ingestion_sagas": 1,
            "persistence_operations": 6,
            "source_publication_events": 3,
            "source_registry": 1,
        }
        if counts_before != expected_saga_counts or len(events) != 8:
            raise Step11ValidationError("Step 10 integration counts differ")
        phase = "VERIFY_EXACT_REPLAY"
        replay = orchestrator.execute(context, bundle.saga, bundle.snapshot)
        counts_after = step10._row_counts(
            root, database, replay.tenant_id, replay.saga_id, replay.source_id
        )
        parse_counts_after = _parsing_counts(
            root,
            database,
            replay.tenant_id,
            replay.saga_id,
            stored_artifact.document.document_id,
            replay.knowledge_version_id,
        )
        if counts_after != counts_before or parse_counts_after != parse_counts:
            raise Step11ValidationError("exact replay duplicated persistent artifacts")
        phase = "VERIFY_CONFLICTING_REPLAY"
        conflicting = build_initial_saga(
            tenant_id=bundle.saga.tenant_id,
            source_id=bundle.saga.source_id,
            hat_scope_id=bundle.saga.hat_scope_id,
            owner_user_id=None,
            knowledge_version_id=bundle.saga.knowledge_version_id,
            idempotency_key=bundle.saga.idempotency_key,
            scope_digest=bundle.saga.scope_digest,
            source_registry_digest=bundle.saga.source_registry_digest,
            content_sha256="f" * 64,
            content_length=bundle.saga.content_length,
            media_type=bundle.saga.media_type,
            local_relative_path=bundle.saga.local_relative_path,
            snapshot_id=bundle.saga.snapshot_id,
            captured_at=bundle.saga.captured_at,
            retain_until=bundle.saga.retain_until,
            created_at=bundle.saga.created_at,
        )
        conflict_code = None
        try:
            saga_service.register_saga(context, conflicting)
        except IdempotencyConflictError as error:
            conflict_code = getattr(error, "sanitized_code", None)
        if conflict_code != "IDEMPOTENCY_BINDING_CONFLICT":
            raise Step11ValidationError("conflicting replay did not fail closed")
        phase = "VERIFY_ZERO_EXTERNAL_WRITES"
        local_payload = external_adapter.read_exact(
            ExternalVolumeOperation.INGESTION_STAGING,
            bundle.saga.local_relative_path,
            expected_sha256=FIXTURE_SHA256,
            expected_length=FIXTURE_LENGTH,
        )
        after_versions = aws_client.list_object_versions(
            bucket=S3_BUCKET,
            key=bundle.storage_plan.object_key,
        )
        delete_markers = _delete_marker_count(aws_binary, bundle.storage_plan.object_key)
        publication_events = source_service.verify_publication_event_chain(
            context,
            tenant_id=completed.tenant_id,
            source_id=completed.source_id,
            hat_scope_id=completed.hat_scope_id,
        )
        if (
            local_payload != bundle.snapshot.canonical_payload
            or len(before_versions) != 1
            or len(after_versions) != 1
            or before_versions[0].get("VersionId") != S3_VERSION_ID
            or after_versions[0].get("VersionId") != S3_VERSION_ID
            or delete_markers != 0
            or aws_client.operation_counts.get("put-object", 0) != 0
            or readonly_s3.put_object_calls != 0
            or volume.write_calls != 0
            or volume.write_attempts != 0
            or len(publication_events) != 3
            or step10._publication_state(
                root, database, completed.tenant_id, completed.source_id
            ) != SourcePublicationState.PUBLISHED.value
        ):
            raise Step11ValidationError("zero-external-write or publication invariant differs")
        section_range_digest = canonical_sha256(
            tuple(
                {
                    "section_id": section.section_id,
                    "start": section.normalized_start_offset,
                    "end": section.normalized_end_offset,
                }
                for section in stored_artifact.sections
            )
        )
        validation = {
            "database": {
                "cluster_version": cluster_version,
                "first_migration_apply": first_apply,
                "migration_replay": replay_apply,
                "parse_row_counts": parse_counts_after,
                "saga_row_counts": counts_after,
                "server_version": server_version.splitlines()[0],
                "step9_security_digest": step9_security["security_digest"],
                "step10_security_digest": step10_security["security_digest"],
                "step11_security_digest": step11_security["security_digest"],
            },
            "external_volume": {
                "artifact_reconciled": True,
                "content_length": len(local_payload),
                "content_sha256": hashlib.sha256(local_payload).hexdigest(),
                "relative_path": plan["external_volume"]["relative_path"],
                "root_filesystem_fallback": False,
                "write_attempts": volume.write_attempts,
                "write_calls": volume.write_calls,
            },
            "s3": {
                "bucket": S3_BUCKET,
                "delete_markers": delete_markers,
                "object_key": bundle.storage_plan.object_key,
                "put_object_attempts": readonly_s3.put_object_calls,
                "put_object_calls": aws_client.operation_counts.get("put-object", 0),
                "retention_changes": 0,
                "version_count": len(after_versions),
                "version_id": S3_VERSION_ID,
            },
            "parse": {
                "chunk_combined_digest": stored_artifact.document.chunk_manifest_digest,
                "chunk_count": len(stored_artifact.chunks),
                "document_id": stored_artifact.document.document_id,
                "finding_combined_digest": stored_artifact.document.finding_manifest_digest,
                "finding_count": len(stored_artifact.findings),
                "knowledge_version_id": stored_artifact.document.knowledge_version_id,
                "normalized_content_sha256": stored_artifact.document.normalized_content_sha256,
                "parse_artifact_digest": stored_artifact.document.parse_artifact_digest,
                "parse_receipt_digest": parsed.receipt_digest,
                "parse_receipt_synthetic_validation_boundary": parsed.synthetic_validation_boundary,
                "section_count": len(stored_artifact.sections),
                "section_range_digest": section_range_digest,
                "validation_receipt_digest": validated.receipt_digest,
                "validation_receipt_synthetic_validation_boundary": validated.synthetic_validation_boundary,
            },
            "saga": {
                "conflicting_replay": "REJECTED",
                "conflicting_replay_code": conflict_code,
                "event_chain_length": len(events),
                "exact_replay": "SAME_COMPLETED_SAGA_NO_DUPLICATES",
                "final_milestone": completed.current_milestone.value,
                "publication_event_count": len(publication_events),
                "run_digest": completed.run_digest,
            },
        }
    except BaseException as error:
        failure = error
        if root is not None and database_created:
            try:
                q = migrations.sql_literal
                rows = migrations.parse_tsv(
                    root.execute(
                        database,
                        "SELECT current_milestone, execution_disposition, "
                        "state_version, attempt_count FROM memory_patch.ingestion_sagas "
                        f"WHERE tenant_id={q(bundle.saga.tenant_id)} "
                        f"AND saga_id={q(bundle.saga.saga_id)}",
                        timeout=30,
                    )
                )
                failure_progress = (
                    {"status": "NO_SAGA_ROW"}
                    if not rows
                    else {"status": "OBSERVED", **rows[0]}
                )
            except BaseException as progress_error:
                failure_progress = {
                    "status": "PROBE_FAILED",
                    "sanitized_code": step10._sanitized_failure(progress_error),
                }
    finally:
        if root is not None and database_created:
            try:
                migrations.drop_database(root, database, timeout=180)
            except BaseException as error:
                cleanup_errors.append(step10._sanitized_failure(error))
        if root is not None and role_created:
            try:
                step10._drop_login_role(root, role)
            except BaseException as error:
                cleanup_errors.append(step10._sanitized_failure(error))
        cleanup = dict(
            runtime.graceful_stop_and_remove(
                root,
                owned_children_reaped=child_registry.all_reaped,
            )
            if root is not None
            and runtime.process is not None
            and runtime.process.poll() is None
            else runtime.stop_and_remove()
        )
        cleanup.setdefault("owned_child_processes_reaped", child_registry.all_reaped)
        cleanup_errors.extend(str(value) for value in cleanup.get("cleanup_errors", ()))
    cleanup_failures = step10._cleanup_invariant_failures(cleanup, cleanup_errors)
    output = _resolve_output(evidence_output)
    failed_output = _resolve_output(failure_output)
    if output == failed_output:
        raise Step11ValidationError(
            "success and failure evidence outputs must be distinct",
            sanitized_code="EVIDENCE_OUTPUT_CONFLICT",
        )
    if failure is not None or cleanup_failures:
        first = (
            _failure_code(failure)
            if failure is not None
            else cleanup_failures[0]
        )
        evidence = _canonical_evidence(
            {
                "schema_version": "1.0.0",
                "step": "Step 11 - Generic Parsing, Normalization and Chunking Pipeline 1A",
                "status": "FAILED_SAFELY",
                "first_root_cause": first,
                "failure_detail_sha256": (
                    hashlib.sha256(str(failure).encode("utf-8")).hexdigest()
                    if failure is not None
                    else None
                ),
                "failure_chain": _failure_chain(failure),
                "failure_phase": phase,
                "failure_progress": failure_progress,
                "failure_type": type(failure).__name__ if failure is not None else None,
                "plan_digest": plan["plan_digest"],
                "previous_attempts": plan["previous_attempts"],
                "cleanup": cleanup,
                "cleanup_failures": cleanup_failures,
                "external_volume_write_attempts": getattr(volume, "write_attempts", None),
                "s3_put_attempts": getattr(readonly_s3, "put_object_calls", None),
            }
        )
        step10._write_verified_evidence(failed_output, evidence)
        raise Step11ValidationError(
            "Step 11 live validation failed",
            sanitized_code=first,
        )
    assert validation is not None
    evidence = _canonical_evidence(
        {
            "schema_version": "1.0.0",
            "step": "Step 11 - Generic Parsing, Normalization and Chunking Pipeline 1A",
            "status": "PASS",
            "mode": "ZERO_EXTERNAL_WRITE_LIVE_VALIDATION",
            "plan_digest": plan["plan_digest"],
            "previous_attempts": plan["previous_attempts"],
            "repository": {
                "baseline": EXPECTED_HEAD,
                "remote": EXPECTED_REMOTE,
                "worktree_digest": plan["repository"]["worktree_digest"],
            },
            "profiles": plan["profiles"],
            "aws": {
                "caller_type": "ASSUMED_ROLE",
                "profile": AWS_PROFILE,
                "region": AWS_REGION,
                "root_principal": False,
                "sensitive_identifiers_redacted": True,
                "temporary_sso_role": True,
            },
            "validation": validation,
            "cleanup": {
                "drain_command_completed": cleanup["drain_command_completed"],
                "drain_completion_marker": cleanup["drain_completion_marker"],
                "force_kill_used": cleanup["force_kill_used"],
                "owned_child_processes_reaped": cleanup["owned_child_processes_reaped"],
                "persistent_database_created": False,
                "persistent_service_created": False,
                "ports_closed": cleanup["ports_closed"],
                "runtime_mode": "DISPOSABLE_LOCAL_SINGLE_NODE",
                "runtime_pid_exited": cleanup["pid_exited"],
                "shutdown_method": cleanup["shutdown_method"],
                "temporary_directory_removed": cleanup["temporary_store_removed"],
            },
            "authority": {
                "external_volume": "DERIVED_STAGING_EVIDENCE_ONLY",
                "findings": "SECURITY_EVIDENCE_ONLY",
                "memory_patch_kernel": "SEMANTIC_AUTHORITY",
                "model_or_hat": "NO_AUTHORITY",
                "parser": "DETERMINISTIC_TRANSFORMATION_ONLY",
                "s3": "IMMUTABLE_STORAGE_EVIDENCE_ONLY",
                "step9": "PUBLICATION_POLICY_BOUNDARY",
                "validator": "STRUCTURAL_VALIDATION_ONLY",
            },
            "safety": {
                "deletions": 0,
                "external_volume_writes": 0,
                "model_calls": False,
                "new_s3_writes": 0,
                "retention_changes": 0,
                "step12_started": False,
            },
        }
    )
    step10._write_verified_evidence(output, evidence)
    return evidence


def _parse_timestamp(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise Step11ValidationError(f"{field} is not an ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise Step11ValidationError(f"{field} must be timezone-aware")
    return parsed.astimezone(UTC)


def _arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", action="store_true")
    mode.add_argument("--validate-existing", action="store_true")
    parser.add_argument("--cockroach-binary", type=Path, required=True)
    parser.add_argument("--aws-binary", type=Path, required=True)
    parser.add_argument("--captured-at", required=True)
    parser.add_argument("--retain-until", required=True)
    parser.add_argument("--external-config", type=Path, default=DEFAULT_EXTERNAL_CONFIG)
    parser.add_argument("--evidence-output", type=Path, default=DEFAULT_EVIDENCE_OUTPUT)
    parser.add_argument("--failure-evidence-output", type=Path, default=DEFAULT_FAILURE_OUTPUT)
    parser.add_argument("--confirm-project")
    parser.add_argument("--confirm-device-reference")
    parser.add_argument("--confirm-external-relative-path")
    parser.add_argument("--confirm-payload-sha256")
    parser.add_argument("--confirm-bucket")
    parser.add_argument("--confirm-object-key")
    parser.add_argument("--confirm-version-id")
    parser.add_argument("--confirm-plan-digest")
    parser.add_argument(
        "--confirm-preserved-failure-evidence-sha256",
        action="append",
        default=[],
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _arguments(argv)
    try:
        captured_at = _parse_timestamp(arguments.captured_at, "captured-at")
        retain_until = _parse_timestamp(arguments.retain_until, "retain-until")
        plan, bundle, external_adapter = prepare_plan(
            cockroach_binary=arguments.cockroach_binary,
            aws_binary=arguments.aws_binary,
            captured_at=captured_at,
            retain_until=retain_until,
            external_config=arguments.external_config,
            evidence_output=arguments.evidence_output,
            failure_evidence_output=arguments.failure_evidence_output,
        )
        if arguments.plan:
            print((json.dumps(plan, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True) + "\n"), end="")
            return 0
        external = plan["external_volume"]
        aws = plan["aws"]
        assert isinstance(external, Mapping) and isinstance(aws, Mapping)
        confirmations = {
            "project": (arguments.confirm_project, EXTERNAL_VOLUME_PROJECT_ID),
            "device": (arguments.confirm_device_reference, external["device_reference"]),
            "external_path": (arguments.confirm_external_relative_path, external["relative_path"]),
            "payload": (arguments.confirm_payload_sha256, FIXTURE_SHA256),
            "bucket": (arguments.confirm_bucket, S3_BUCKET),
            "object_key": (arguments.confirm_object_key, aws["object_key"]),
            "version_id": (arguments.confirm_version_id, S3_VERSION_ID),
            "plan": (arguments.confirm_plan_digest, plan["plan_digest"]),
        }
        previous_attempts = plan.get("previous_attempts")
        if not isinstance(previous_attempts, list):
            raise Step11ValidationError("preserved failure evidence plan is malformed")
        expected_failure_digests = [
            str(item["file_sha256"])
            for item in previous_attempts
            if isinstance(item, Mapping)
        ]
        confirmations["preserved_failures"] = (
            arguments.confirm_preserved_failure_evidence_sha256,
            expected_failure_digests,
        )
        mismatches = [name for name, values in confirmations.items() if values[0] != values[1]]
        if mismatches:
            raise Step11ValidationError(
                "exact zero-write confirmations differ: " + ", ".join(mismatches)
            )
        result = execute_validation(
            plan=plan,
            bundle=bundle,
            external_adapter=external_adapter,
            cockroach_binary=arguments.cockroach_binary.expanduser().resolve(),
            aws_binary=arguments.aws_binary.expanduser().resolve(),
            evidence_output=arguments.evidence_output,
            failure_output=arguments.failure_evidence_output,
        )
        print((json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True) + "\n"), end="")
        return 0
    except BaseException as error:
        print(
            f"ERROR [{step10._sanitized_failure(error)}]: Step 11 validation stopped safely",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
