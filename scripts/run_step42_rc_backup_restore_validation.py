#!/usr/bin/env python3
"""Controlled RC freeze, native backup, isolated restore, and recovery proof."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
for import_root in (ROOT / "src", ROOT / "scripts", ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

import run_cockroachdb_migrations as migrations  # noqa: E402
import run_step18_retrieval_validation as step18  # noqa: E402
import run_step19_embedding_vector_validation as step19  # noqa: E402
import run_step27_personal_memory_validation as step27  # noqa: E402
import run_step29_personal_memory_patch_validation as step29  # noqa: E402
import run_step30_user_approval_commit_activation_validation as step30  # noqa: E402
import run_step31_active_patch_retrieval_validation as step31  # noqa: E402
import run_step33_audit_ledger_validation as step33  # noqa: E402
import run_step38_german_law_e2e_validation as step38  # noqa: E402
import step38_real_retrieval as real_retrieval  # noqa: E402

from aioa_memory_kernel.audit_ledger import (  # noqa: E402
    AuditLedgerCockroachRepository,
    AuditLedgerService,
    compute_audit_chain_id,
)
from aioa_memory_kernel.contracts.serialization import (  # noqa: E402
    canonical_json,
    canonical_sha256,
    to_canonical_data,
)
from aioa_memory_kernel.embeddings import PassageEmbeddingCache  # noqa: E402
from aioa_memory_kernel.embeddings.local_e5 import LocalE5Backend  # noqa: E402
from aioa_memory_kernel.personal_memory import (  # noqa: E402
    ActivePatchRetrievalService,
    PersonalMemoryCockroachRepository,
)
from aioa_memory_kernel.release_candidate import (  # noqa: E402
    build_backup_tree_receipt,
    build_rc_manifest,
    build_recovery_asset_manifest,
    build_recovery_watermark,
    validate_disposable_recovery_root,
    validate_restore_target,
    verify_backup_tree_receipt,
)
from aioa_memory_kernel.runtime import (  # noqa: E402
    ComponentState,
    build_runtime_health_snapshot,
    load_runtime_4gb_profile,
)
from aioa_memory_kernel.security.credentials import (  # noqa: E402
    CredentialPurpose,
    SecretValue,
    build_minimal_subprocess_environment,
)
from aioa_memory_kernel.security.redaction import (  # noqa: E402
    assert_secret_free,
    redact_exception,
)


STEP41_BASE_SHA = "26577fa02c96da7a4b4ae49cdc5f3c168eb1ed80"
EXPECTED_REMOTE = (
    "https://github.com/luciferprosun/Memory-Patch-for-AIOA-Hackathon-CockroachDB.git"
)
RC_MANIFEST_PATH = ROOT / "docs/evidence/release/step42-rc-manifest-1a.json"
RECOVERY_MANIFEST_PATH = (
    ROOT / "docs/evidence/release/step42-recovery-asset-manifest-1a.json"
)
EVIDENCE_PATH = (
    ROOT / "docs/evidence/release/step42-rc-backup-restore-validation.json"
)
STEP40_EVIDENCE_PATH = (
    ROOT / "docs/evidence/performance/step40-4gb-resource-validation.json"
)
BACKUP_COLLECTION = "step42-native"
MAXIMUM_JOB_WAIT_SECONDS = 600
_CURRENT_STAGE = "NOT_STARTED"


class Step42ValidationError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__("Step 42 validation failed")
        self.sanitized_code = code


def _progress(stage: str, status: str = "RUNNING") -> None:
    global _CURRENT_STAGE
    _CURRENT_STAGE = stage
    print(
        canonical_json({"stage": stage, "status": status, "step": 42}),
        file=sys.stderr,
        flush=True,
    )


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cockroach-binary", type=Path)
    parser.add_argument(
        "--external-env",
        type=Path,
        default=step27.DEFAULT_EXTERNAL_ENV,
    )
    parser.add_argument(
        "--step14-bundle-root",
        type=Path,
        default=step18.DEFAULT_STEP14,
    )
    parser.add_argument(
        "--step15-bundle-root",
        type=Path,
        default=step18.DEFAULT_STEP15,
    )
    parser.add_argument(
        "--step16-bundle-root",
        type=Path,
        default=step18.DEFAULT_STEP16,
    )
    parser.add_argument(
        "--source-root",
        type=Path,
        default=step18.DEFAULT_SOURCE_ROOT,
    )
    parser.add_argument("--write-evidence", action="store_true")
    return parser.parse_args()


def _git(*arguments: str) -> str:
    try:
        result = subprocess.run(
            ("git", *arguments),
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=build_minimal_subprocess_environment(os.environ),
            timeout=60,
            check=True,
        )
    except (OSError, subprocess.SubprocessError, UnicodeError) as error:
        raise Step42ValidationError("STEP42_GIT_GUARD_FAILED") from error
    return result.stdout.strip()


def _repository_guard() -> Mapping[str, Any]:
    head = _git("rev-parse", "HEAD")
    branch = _git("branch", "--show-current")
    remote = _git("remote", "get-url", "origin")
    if branch != "main" or remote != EXPECTED_REMOTE:
        raise Step42ValidationError("STEP42_REPOSITORY_IDENTITY_MISMATCH")
    try:
        subprocess.run(
            ("git", "merge-base", "--is-ancestor", STEP41_BASE_SHA, head),
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=build_minimal_subprocess_environment(os.environ),
            timeout=30,
            check=True,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise Step42ValidationError("STEP41_BASE_NOT_REACHABLE") from error
    if _git("diff", "--check"):
        raise Step42ValidationError("STEP42_DIFF_CHECK_FAILED")
    return {
        "branch": branch,
        "repository_identity": remote,
        "step41_base_reachable": True,
        "step41_base_sha": STEP41_BASE_SHA,
        "validated_head_sha_before_commit": head,
    }


def _prepare_execution_runtime(
    args: argparse.Namespace,
) -> tuple[SecretValue, Any, LocalE5Backend, PassageEmbeddingCache, Path, Mapping[str, Any]]:
    credential = step38._consume_openrouter_environment_credential()
    adapter, config, external_facts = step19._external_runtime(args.external_env)
    runtime_root = step19._safe_external_directory(
        config.data_root,
        step19.RUNTIME_RELATIVE,
    )
    runtime_python = runtime_root / "bin/python"
    if credential is None:
        raise Step42ValidationError("STEP42_APPROVED_PROVIDER_CREDENTIAL_REQUIRED")
    if Path(sys.prefix).resolve() != runtime_root.resolve(strict=True):
        environment = step38._minimal_openrouter_reexec_environment(credential)
        environment.update(
            {
                "HF_HOME": str(config.data_root / step19.HF_RELATIVE),
                "HF_HUB_CACHE": str(config.data_root / step19.HF_RELATIVE / "hub"),
                "HF_HUB_DISABLE_TELEMETRY": "1",
                "HF_HUB_DISABLE_XET": "1",
                "PIP_CACHE_DIR": str(config.data_root / step19.PIP_RELATIVE),
                "PYTHONDONTWRITEBYTECODE": "1",
                "STEP42_ISOLATED_RUNTIME": "1",
            }
        )
        os.execve(
            str(runtime_python),
            [str(runtime_python), str(Path(__file__).resolve()), *sys.argv[1:]],
            environment,
        )
    os.environ.update(
        {
            "HF_HUB_DISABLE_PROGRESS_BARS": "1",
            "HF_HUB_DISABLE_TELEMETRY": "1",
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_NO_ADVISORY_WARNINGS": "1",
            "TRANSFORMERS_OFFLINE": "1",
        }
    )
    model_root = step19._safe_external_directory(
        config.data_root,
        step19.MODEL_RELATIVE,
    )
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
        io.StringIO()
    ):
        step19.verify_model_snapshot(model_root)
        backend = LocalE5Backend(model_root)
    cache = PassageEmbeddingCache(adapter)
    source_binary = (
        args.cockroach_binary
        if args.cockroach_binary is not None
        else config.data_root
        / "cache/xdg/cockroachdb/v26.2.4/linux-amd64/server/"
        "cockroach-v26.2.4.linux-amd64/cockroach"
    ).expanduser().resolve(strict=True)
    identity = migrations.verify_binary_identity(source_binary)
    if identity["binary_sha256"] != step19.EXPECTED_COCKROACH_SHA256:
        raise Step42ValidationError("STEP42_COCKROACH_BINARY_IDENTITY_MISMATCH")
    return (
        credential,
        config,
        backend,
        cache,
        source_binary,
        {
            "external_volume_verified": all(
                bool(value) for value in external_facts.values()
            ),
            "model_digest": backend.identity().model_digest,
            "model_file_count": len(backend.verified_files),
            "network_access": False,
        },
    )


def _start_runtime(
    *,
    binary: Path,
    kind: str,
    backup_root: Path,
) -> tuple[str, migrations.LocalRuntime, Any]:
    if kind not in {"source", "restore"}:
        raise Step42ValidationError("STEP42_RUNTIME_KIND_INVALID")
    run_id = f"mp_step42_{kind}_" + uuid.uuid4().hex[:12]
    runtime = migrations.LocalRuntime(
        binary,
        run_id,
        external_io_dir=backup_root,
    )
    root = step18._start_disposable_runtime(runtime)
    return run_id, runtime, root


def _enable_vector(root: Any) -> None:
    current = migrations.one_value(
        root.execute(
            "defaultdb",
            "SHOW CLUSTER SETTING feature.vector_index.enabled",
            timeout=60,
        )
    )
    if current != "t":
        root.execute(
            "defaultdb",
            "SET CLUSTER SETTING feature.vector_index.enabled = true",
            timeout=60,
        )
    if migrations.one_value(
        root.execute(
            "defaultdb",
            "SHOW CLUSTER SETTING feature.vector_index.enabled",
            timeout=60,
        )
    ) != "t":
        raise Step42ValidationError("STEP42_VECTOR_CAPABILITY_UNAVAILABLE")


def _apply_and_replay(root: Any, database: str) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    applied = migrations.apply_migrations(root, database, timeout=300)
    replay = migrations.apply_migrations(root, database, timeout=300)
    expected = len(migrations.load_migrations())
    if (
        len(applied["applied"]) != expected
        or replay["applied"]
        or len(replay["skipped"]) != expected
    ):
        raise Step42ValidationError("STEP42_MIGRATION_REPLAY_MISMATCH")
    return applied, replay


def _hash_sql(root: Any, database: str, sql: str) -> str:
    return hashlib.sha256(
        root.execute(database, sql, timeout=120).encode("utf-8")
    ).hexdigest()


def _count(root: Any, database: str, table: str) -> int:
    if not table.startswith("memory_patch.") or not all(
        part.replace("_", "").isalnum() for part in table.split(".")
    ):
        raise Step42ValidationError("STEP42_WATERMARK_TABLE_INVALID")
    return int(
        migrations.one_value(
            root.execute(database, f"SELECT count(*) FROM {table}", timeout=60)
        )
    )


def _watermark(root: Any, database: str):
    tables = (
        "memory_patch.audit_chain_heads",
        "memory_patch.audit_events",
        "memory_patch.chunk_embeddings",
        "memory_patch.chunk_search_documents",
        "memory_patch.knowledge_chunks",
        "memory_patch.knowledge_sources",
        "memory_patch.knowledge_versions",
        "memory_patch.memory_items",
        "memory_patch.memory_patch_approvals",
        "memory_patch.memory_patch_commits",
        "memory_patch.memory_patch_proposals",
        "memory_patch.patch_transition_records",
        "memory_patch.persistence_operations",
        "memory_patch.personal_memory_model_bindings",
        "memory_patch.personal_memory_quota_policies",
        "memory_patch.personal_memory_spaces",
        "memory_patch.schema_migrations",
        "memory_patch.source_provenance_edges",
        "memory_patch.source_publication_events",
        "memory_patch.source_registry_entries",
        "memory_patch.source_snapshots",
    )
    counts = {table.split(".", 1)[1]: _count(root, database, table) for table in tables}
    hashes = {
        "audit_chain_heads": _hash_sql(
            root,
            database,
            "SELECT tenant_id, owner_user_id, chain_id, last_sequence, "
            "last_event_hash, head_version FROM memory_patch.audit_chain_heads "
            "ORDER BY tenant_id, owner_user_id, chain_id",
        ),
        "audit_events": _hash_sql(
            root,
            database,
            "SELECT * FROM memory_patch.audit_events "
            "ORDER BY tenant_id, user_id, chain_id, sequence_number",
        ),
        "chunk_embeddings": _hash_sql(
            root,
            database,
            "SELECT tenant_id, chunk_id, embedding_model_digest, content_sha256, "
            "embedding_bytes_sha256, record_hash FROM memory_patch.chunk_embeddings "
            "ORDER BY tenant_id, chunk_id, embedding_model_digest",
        ),
        "german_law_chunks": _hash_sql(
            root,
            database,
            "SELECT tenant_id, chunk_id, source_id, knowledge_version_id, "
            "hat_scope_id, content_sha256 FROM memory_patch.knowledge_chunks "
            "ORDER BY tenant_id, chunk_id",
        ),
        "migration_state": _hash_sql(
            root,
            database,
            "SELECT migration_id, checksum_sha256 FROM memory_patch.schema_migrations "
            "ORDER BY migration_id",
        ),
        "personal_memory_approvals": _hash_sql(
            root,
            database,
            "SELECT * FROM memory_patch.memory_patch_approvals "
            "ORDER BY tenant_id, approval_id",
        ),
        "personal_memory_commits": _hash_sql(
            root,
            database,
            "SELECT * FROM memory_patch.memory_patch_commits "
            "ORDER BY tenant_id, commit_id",
        ),
        "personal_memory_lifecycle": _hash_sql(
            root,
            database,
            "SELECT tenant_id, owner_user_id, proposal_id, lifecycle_state, "
            "step29_state_hash FROM memory_patch.memory_patch_proposals "
            "ORDER BY tenant_id, owner_user_id, proposal_id",
        ),
        "personal_memory_model_bindings": _hash_sql(
            root,
            database,
            "SELECT * FROM memory_patch.personal_memory_model_bindings "
            "ORDER BY tenant_id, user_id, personal_memory_space_id, "
            "model_binding_id",
        ),
        "personal_memory_patches": _hash_sql(
            root,
            database,
            "SELECT tenant_id, memory_item_id, source_patch_id, step30_patch_hash, "
            "active, revoked, step32_terminal_kind "
            "FROM memory_patch.memory_items "
            "ORDER BY tenant_id, memory_item_id",
        ),
        "personal_memory_quota_policies": _hash_sql(
            root,
            database,
            "SELECT * FROM memory_patch.personal_memory_quota_policies "
            "ORDER BY tenant_id, owner_user_id, quota_policy_id",
        ),
        "personal_memory_spaces": _hash_sql(
            root,
            database,
            "SELECT * FROM memory_patch.personal_memory_spaces "
            "ORDER BY tenant_id, user_id, personal_memory_space_id",
        ),
        "personal_memory_transitions": _hash_sql(
            root,
            database,
            "SELECT * FROM memory_patch.patch_transition_records "
            "ORDER BY tenant_id, transition_id",
        ),
        "persistence_operations": _hash_sql(
            root,
            database,
            "SELECT * FROM memory_patch.persistence_operations "
            "ORDER BY tenant_id, operation_id",
        ),
        "source_provenance": _hash_sql(
            root,
            database,
            "SELECT * FROM memory_patch.source_provenance_edges "
            "ORDER BY tenant_id, source_id, hat_scope_id, edge_id",
        ),
        "source_publication": _hash_sql(
            root,
            database,
            "SELECT * FROM memory_patch.source_publication_events "
            "ORDER BY tenant_id, source_id, hat_scope_id, sequence_number",
        ),
        "source_registry": _hash_sql(
            root,
            database,
            "SELECT tenant_id, source_id, hat_scope_id, source_kind, "
            "source_reference, current_publication_state, authority_level, "
            "artifact_digest, registry_digest "
            "FROM memory_patch.source_registry_entries "
            "ORDER BY tenant_id, source_id",
        ),
    }
    return build_recovery_watermark(
        migration_ids=tuple(
            item.migration_id for item in migrations.load_migrations()
        ),
        critical_counts=counts,
        critical_hashes=hashes,
    )


def _detached_job(root: Any, statement: str) -> int:
    rows = migrations.parse_tsv(root.execute("defaultdb", statement, timeout=60))
    if len(rows) != 1:
        raise Step42ValidationError("STEP42_DETACHED_JOB_RECEIPT_INVALID")
    value = rows[0].get("job_id") or rows[0].get("jobid")
    if not isinstance(value, str) or not value.isdigit() or len(value) > 24:
        raise Step42ValidationError("STEP42_DETACHED_JOB_ID_INVALID")
    return int(value)


def _wait_job(root: Any, job_id: int) -> str:
    deadline = time.monotonic() + MAXIMUM_JOB_WAIT_SECONDS
    while time.monotonic() < deadline:
        rows = migrations.parse_tsv(
            root.execute(
                "defaultdb",
                "SELECT status FROM system.jobs WHERE id = " + str(job_id),
                timeout=60,
            )
        )
        status = str(rows[0].get("status", "")) if len(rows) == 1 else ""
        if status == "succeeded":
            return status
        if status in {"failed", "canceled", "revert-failed"}:
            raise Step42ValidationError("STEP42_NATIVE_JOB_FAILED")
        time.sleep(0.5)
    raise Step42ValidationError("STEP42_NATIVE_JOB_TIMEOUT")


def _backup_database(root: Any, database: str) -> Mapping[str, Any]:
    started = time.monotonic()
    job_id = _detached_job(
        root,
        f"BACKUP DATABASE {database} INTO "
        f"'nodelocal://1/{BACKUP_COLLECTION}' WITH detached",
    )
    status = _wait_job(root, job_id)
    checked = root.execute(
        "defaultdb",
        f"SHOW BACKUP FROM LATEST IN 'nodelocal://1/{BACKUP_COLLECTION}' "
        "WITH check_files",
        timeout=300,
    )
    rows = migrations.parse_tsv(checked)
    if not rows:
        raise Step42ValidationError("STEP42_NATIVE_BACKUP_CHECK_EMPTY")
    return {
        "completed_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "duration_seconds": round(time.monotonic() - started, 3),
        "job_status": status,
        "native_check_files": "PASS",
        "native_manifest_row_count": len(rows),
        "native_manifest_digest": hashlib.sha256(
            checked.encode("utf-8")
        ).hexdigest(),
    }


def _restore_database(
    root: Any,
    *,
    source_database: str,
    restore_database: str,
) -> Mapping[str, Any]:
    started = time.monotonic()
    job_id = _detached_job(
        root,
        f"RESTORE DATABASE {source_database} FROM LATEST IN "
        f"'nodelocal://1/{BACKUP_COLLECTION}' WITH "
        f"new_db_name = {migrations.sql_literal(restore_database)}, grants, detached",
    )
    return {
        "duration_seconds": round(time.monotonic() - started, 3),
        "job_status": _wait_job(root, job_id),
    }


def _create_step31_fixture(root: Any, database: str) -> tuple[Mapping[str, Any], list[tuple[str, str]]]:
    pipeline_request, _ = step31.hat_lineage()
    root.execute(
        database,
        step29._seed_identity_sql(
            pipeline_request.route.tenant_id,
            pipeline_request.route.user_id,
            pipeline_request.temporal_result.trusted_now,
        ),
        timeout=120,
    )
    app_role = "mp_s42_pm_app_" + uuid.uuid4().hex[:10]
    commit_role = "mp_s42_pm_commit_" + uuid.uuid4().hex[:10]
    step27._create_validation_role(root, app_role)
    step30._create_commit_validation_role(root, commit_role)
    return (
        step31._validate_service(
            root=root,
            database=database,
            app_role=app_role,
            commit_role=commit_role,
        ),
        [("app", app_role), ("commit", commit_role)],
    )


def _create_step33_fixture(root: Any, database: str) -> tuple[Mapping[str, Any], list[tuple[str, str]]]:
    shared_identity_sql = ";\n".join(
        statement.strip() + " ON CONFLICT DO NOTHING"
        for statement in step33._seed_identity_sql().split(";\n")
        if statement.strip()
    )
    root.execute(database, shared_identity_sql, timeout=120)
    app_role = "mp_s42_audit_app_" + uuid.uuid4().hex[:10]
    reader_role = "mp_s42_audit_reader_" + uuid.uuid4().hex[:10]
    step27._create_validation_role(root, app_role)
    step33._create_audit_reader_validation_role(root, reader_role)
    return (
        step33._validate_service(
            root=root,
            database=database,
            app_role=app_role,
            audit_reader_role=reader_role,
        ),
        [("app", app_role), ("audit_reader", reader_role)],
    )


def _drop_roles(root: Any, roles: list[tuple[str, str]]) -> None:
    for kind, role in reversed(roles):
        if kind == "app":
            step27._drop_validation_role(root, role)
        elif kind == "commit":
            step30._drop_commit_validation_role(root, role)
        elif kind == "audit_reader":
            step33._drop_audit_reader_validation_role(root, role)
        else:
            raise Step42ValidationError("STEP42_ROLE_KIND_INVALID")


def _verify_personal_memory_after_restore(
    root: Any,
    database: str,
    source_result: Mapping[str, Any],
) -> Mapping[str, Any]:
    app_role = "mp_s42_restore_pm_" + uuid.uuid4().hex[:10]
    step27._create_validation_role(root, app_role)
    try:
        pipeline_request, _ = step31.hat_lineage()
        runner = step30._runner(
            port=root.sql_port,
            database=database,
            role=app_role,
            credential_purpose=CredentialPurpose.APPLICATION_DATABASE,
            diagnostic=True,
        )
        owner = source_result["owner_scope"]
        context = step29._context(owner["tenant_id"], owner["owner_user_id"])
        slots = PersonalMemoryCockroachRepository()
        slot = runner.run(
            context,
            lambda transaction: slots.get_slot(
                transaction,
                owner["tenant_id"],
                owner["owner_user_id"],
                owner["personal_memory_space_id"],
            ),
            operation_kind="STEP42_RESTORED_SLOT_LOAD",
        )
        if slot is None or not slot.model_bindings:
            raise Step42ValidationError("STEP42_RESTORED_ACTIVE_SLOT_MISSING")
        request, route, temporal = step31._retrieval_request(
            slot,
            slot.model_bindings[0],
            query="Step 42 restored active Personal Memory retrieval",
        )
        result, envelope = ActivePatchRetrievalService(runner).retrieve(
            request,
            route=route,
            temporal_result=temporal,
        )
        conflict_request, conflict_route, conflict_temporal = step31._retrieval_request(
            slot,
            slot.model_bindings[0],
            query="Step 42 restored canonical conflict",
            conflict=True,
        )
        conflict_result, _ = ActivePatchRetrievalService(runner).retrieve(
            conflict_request,
            route=conflict_route,
            temporal_result=conflict_temporal,
        )
        owner_rows = step31._visible_count(
            runner,
            context=context,
            tenant_id=owner["tenant_id"],
            hat_scope_id=slot.hat_scope_id,
        )
        cross_user_rows = step31._visible_count(
            runner,
            context=step29._context(owner["tenant_id"], step29.OTHER_USER),
            tenant_id=owner["tenant_id"],
            hat_scope_id=slot.hat_scope_id,
        )
        cross_tenant_rows = step31._visible_count(
            runner,
            context=step29._context(step29.OTHER_TENANT, step29.OTHER_TENANT_USER),
            tenant_id=owner["tenant_id"],
            hat_scope_id=slot.hat_scope_id,
        )
        if (
            len(result.eligible_patches) != 1
            or len(envelope.ordered_active_patches) != 1
            or conflict_result.eligible_patches
            or (owner_rows, cross_user_rows, cross_tenant_rows) != (1, 0, 0)
        ):
            raise Step42ValidationError("STEP42_PERSONAL_MEMORY_RESTORE_FAILED")
        return {
            "active_patch_hash": result.eligible_patches[0].patch_hash,
            "active_retrieval": "PASS",
            "canonical_conflict": "PATCH_SUPPRESSED",
            "cross_tenant_visible_rows": cross_tenant_rows,
            "cross_user_visible_rows": cross_user_rows,
            "owner_visible_rows": owner_rows,
            "private_noncanonical": True,
            "result_hash": result.result_hash,
        }
    finally:
        step27._drop_validation_role(root, app_role)


def _verify_audit_after_restore(root: Any, database: str) -> Mapping[str, Any]:
    app_role = "mp_s42_restore_audit_" + uuid.uuid4().hex[:10]
    reader_role = "mp_s42_restore_reader_" + uuid.uuid4().hex[:10]
    step27._create_validation_role(root, app_role)
    step33._create_audit_reader_validation_role(root, reader_role)
    try:
        app_runner = step30._runner(
            port=root.sql_port,
            database=database,
            role=app_role,
            credential_purpose=CredentialPurpose.AUDIT_APPENDER_DATABASE,
            diagnostic=True,
        )
        reader_runner = step30._runner(
            port=root.sql_port,
            database=database,
            role=reader_role,
            credential_purpose=CredentialPurpose.AUDIT_READER_DATABASE,
            diagnostic=True,
        )
        repository = AuditLedgerCockroachRepository()
        service = AuditLedgerService(
            app_runner,
            reader_transaction_runner=reader_runner,
            repository=repository,
        )
        proof = service.verify_chain(
            tenant_id=step33.TENANT_A,
            owner_user_id=step33.OWNER_A,
            authenticated_tenant_id=step33.TENANT_A,
            authenticated_owner_user_id=step33.OWNER_A,
        )
        context = step33._context(step33.TENANT_A, step33.OWNER_A)
        chain_id = compute_audit_chain_id(step33.TENANT_A, step33.OWNER_A)
        head = app_runner.run(
            context,
            lambda transaction: repository.get_chain_head(
                transaction,
                tenant_id=step33.TENANT_A,
                owner_user_id=step33.OWNER_A,
                chain_id=chain_id,
            ),
            operation_kind="STEP42_RESTORED_AUDIT_HEAD",
        )
        if head is None:
            raise Step42ValidationError("STEP42_RESTORED_AUDIT_HEAD_MISSING")
        entries = app_runner.run(
            context,
            lambda transaction: repository.load_range(
                transaction,
                tenant_id=step33.TENANT_A,
                owner_user_id=step33.OWNER_A,
                chain_id=chain_id,
                start_sequence=1,
                end_sequence=head.last_sequence,
                maximum_events=100,
            ),
            operation_kind="STEP42_RESTORED_AUDIT_RANGE",
        )
        tamper = step33._tamper_matrix(entries, head)
        if not proof.verified or any(value != "DETECTED" for value in tamper.values()):
            raise Step42ValidationError("STEP42_AUDIT_RESTORE_FAILED")
        return {
            "chain_id": chain_id,
            "event_count": len(entries),
            "last_hash": proof.last_hash,
            "tamper_cases": len(tamper),
            "tamper_undetected": 0,
            "verified": True,
        }
    finally:
        step33._drop_audit_reader_validation_role(root, reader_role)
        step27._drop_validation_role(root, app_role)


def _step40_smoke() -> Mapping[str, Any]:
    profile = load_runtime_4gb_profile()
    health = build_runtime_health_snapshot(
        profile=profile,
        process_responsive=True,
        external_volume_ready=True,
        database_schema_ready=True,
        german_law_corpus_ready=True,
        personal_memory_persistence_ready=True,
        audit_append_ready=True,
        provider_configuration_ready=True,
        owner_ui_ready=True,
        embedding_loaded=True,
        critic_enabled=False,
        critic_available=False,
        ingestion_enabled=False,
    )
    stored = json.loads(STEP40_EVIDENCE_PATH.read_text(encoding="utf-8"))
    claimed = stored.get("validation_digest")
    if (
        stored.get("status") != "PASS_4GB_CONTROLLED"
        or claimed
        != canonical_sha256(stored, exclude_fields=("validation_digest",))
        or stored.get("profile", {}).get("profile_digest")
        != profile.profile_digest
        or health.readiness is not True
    ):
        raise Step42ValidationError("STEP42_STEP40_PROFILE_SMOKE_FAILED")
    return {
        "configured_peak_budget_mib": profile.host_budget.runtime_peak_budget_mib,
        "critic_state": ComponentState.DISABLED_INTENTIONAL.value,
        "health_snapshot_hash": health.snapshot_hash,
        "profile_digest": profile.profile_digest,
        "readiness": health.readiness,
        "security_or_authority_bypass": False,
        "status": "PASS_RESTORED_PROFILE_SMOKE",
    }


def _classify_post_restore_provider_result(
    provider_public: Mapping[str, Any],
    upstream: Any | None,
) -> Mapping[str, Any]:
    """Accept a verified lineage or the one exact canonical no-defect stop.

    A real model that answers both the primary and polarity-controlled backup
    questions correctly must not be coerced into a fake correction.  Step 42
    records that exact result as canonical fail-closed behavior.  Provider
    errors, malformed answers, an inexact backup answer, or any other blocked
    reason remain closure failures.
    """

    if not isinstance(provider_public, Mapping):
        raise Step42ValidationError("STEP42_PROVIDER_RESULT_UNTYPED")
    spec = step38._step38_openrouter_spec()
    if (
        provider_public.get("provider_id") != spec.provider_id
        or provider_public.get("model_id") != spec.model_id
        or provider_public.get("provider_material_recorded") is not False
    ):
        raise Step42ValidationError("STEP42_PROVIDER_RESULT_IDENTITY_MISMATCH")
    if upstream is not None:
        if (
            provider_public.get("status") != "PASS_REAL_VERIFIED_LINEAGE"
            or provider_public.get("closure_authority") is not True
            or not isinstance(getattr(upstream, "lineage_hash", None), str)
        ):
            raise Step42ValidationError("STEP42_PROVIDER_VERIFIED_LINEAGE_INVALID")
        return {
            "canonical_fail_closed": False,
            "closure_path": "VERIFIED_ANSWER_WITH_CORRECTION",
            "selected_case_id": provider_public.get("selected_case_id"),
            "status": "PASS_REAL_VERIFIED_LINEAGE",
            "verified_upstream_lineage_hash": upstream.lineage_hash,
        }

    attempts = provider_public.get("case_attempts")
    if not isinstance(attempts, (tuple, list)) or len(attempts) != 2:
        raise Step42ValidationError("STEP42_PROVIDER_FAIL_CLOSED_ATTEMPTS_INVALID")
    primary_attempt, backup_attempt = attempts
    if (
        not isinstance(primary_attempt, Mapping)
        or not isinstance(backup_attempt, Mapping)
        or provider_public.get("status") != "BLOCKED"
        or provider_public.get("reason") != "STEP38_BACKUP_DEFECT_NOT_OBSERVED"
        or provider_public.get("primary_case_attempted") is not True
        or provider_public.get("backup_case_attempted") is not True
        or primary_attempt.get("case_id") != "primary-entry-into-force"
        or backup_attempt.get("case_id") != step38.BACKUP_SPECIAL_CASE_ID
        or backup_attempt.get("draft_shape_classification")
        != step38.BACKUP_DRAFT_CORRECT_EXACT
        or backup_attempt.get("required_correction_count") != 0
        or backup_attempt.get("citation_count") != 0
    ):
        raise Step42ValidationError("STEP42_PROVIDER_FAIL_CLOSED_RESULT_INVALID")
    return {
        "canonical_fail_closed": True,
        "closure_path": "CANONICAL_FAIL_CLOSED_NO_CORRECTION_REQUIRED",
        "selected_case_id": step38.BACKUP_SPECIAL_CASE_ID,
        "status": "PASS_REAL_PROVIDER_CANONICAL_FAIL_CLOSED_NO_DEFECT",
        "verified_upstream_lineage_hash": None,
    }


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = canonical_json(value) + "\n"
    path.write_text(payload, encoding="utf-8")


def validate(args: argparse.Namespace) -> Mapping[str, Any]:
    _progress("REPOSITORY_AND_RC_FREEZE")
    repository = _repository_guard()
    recovery_manifest = build_recovery_asset_manifest(
        step41_base_sha=STEP41_BASE_SHA
    )
    rc_manifest = build_rc_manifest(
        step41_base_sha=STEP41_BASE_SHA,
        recovery_asset_manifest_digest=recovery_manifest.manifest_digest,
        repository_identity=EXPECTED_REMOTE,
    )
    credential, _external_config, backend, cache, binary, embedding = (
        _prepare_execution_runtime(args)
    )
    recovery_root = Path(
        tempfile.mkdtemp(prefix="mp-step42-recovery-", dir="/tmp")
    )
    os.chmod(recovery_root, 0o700)
    recovery_root = validate_disposable_recovery_root(recovery_root)
    native_root = recovery_root / "native-backup"
    native_root.mkdir(mode=0o700)
    source_runtime = None
    restore_runtime = None
    source_root = None
    restore_root = None
    source_database = None
    restore_database = None
    source_roles: list[tuple[str, str]] = []
    cleanup_errors: list[str] = []
    production_resources_touched = 0
    result_payload: dict[str, Any] | None = None
    try:
        _progress("SOURCE_DATABASE_AND_CANONICAL_FIXTURES")
        source_run_id, source_runtime, source_root = _start_runtime(
            binary=binary,
            kind="source",
            backup_root=native_root,
        )
        _enable_vector(source_root)
        source_server_version = migrations.one_value(
            source_root.execute("defaultdb", "SELECT version()", timeout=60)
        )
        if migrations.PINNED_VERSION not in source_server_version:
            raise Step42ValidationError("STEP42_SOURCE_SERVER_VERSION_MISMATCH")
        source_database = source_run_id + "_db"
        migrations.create_database(source_root, source_database)
        applied, replay = _apply_and_replay(source_root, source_database)
        security_catalog_before = migrations.assert_step36_security_catalog(
            source_root,
            source_database,
        )
        data_role = "mp_s42_retrieval_" + uuid.uuid4().hex[:10]
        step27._create_validation_role(source_root, data_role)
        source_roles.append(("app", data_role))
        data_runner = step30._runner(
            port=source_root.sql_port,
            database=source_database,
            role=data_role,
            credential_purpose=CredentialPurpose.APPLICATION_DATABASE,
            diagnostic=True,
        )
        suite = step38.load_german_law_golden_cases(step38.FIXTURE_PATH)
        corpus_roots = real_retrieval.Step38CorpusRoots(
            step14_bundle_root=args.step14_bundle_root,
            step15_bundle_root=args.step15_bundle_root,
            step16_bundle_root=args.step16_bundle_root,
            source_root=args.source_root,
        )
        primary_input = real_retrieval.build_canonical_primary_retrieval_input(
            suite,
            tenant_id="tenant-step38-golden",
            user_id="user-step38-owner-a",
            request_id="request-step42-source-golden",
        )
        source_runtime_digest = canonical_sha256(
            {
                "binary_sha256": migrations.verify_binary_identity(binary)[
                    "binary_sha256"
                ],
                "run_class": "STEP42_SOURCE_DISPOSABLE",
            }
        )
        source_database_digest = canonical_sha256(
            {
                "database_class": "STEP42_SOURCE",
                "runtime_instance_digest": source_runtime_digest,
            }
        )
        source_retrieval = real_retrieval.run_step38_real_retrieval_on_owned_database(
            primary_input,
            root=source_root,
            database=source_database,
            database_runner=data_runner,
            data_plane_session_user=data_role,
            runtime_instance_digest=source_runtime_digest,
            database_instance_digest=source_database_digest,
            corpus_roots=corpus_roots,
            embedding_backend=backend,
            embedding_cache=cache,
        )
        _progress("SOURCE_PERSONAL_MEMORY_FIXTURE")
        pm_result, pm_roles = _create_step31_fixture(source_root, source_database)
        source_roles.extend(pm_roles)
        _progress("SOURCE_AUDIT_FIXTURE")
        audit_result, audit_roles = _create_step33_fixture(
            source_root,
            source_database,
        )
        source_roles.extend(audit_roles)
        _progress("SOURCE_RECOVERY_WATERMARK")
        before = _watermark(source_root, source_database)
        if (
            before.critical_counts[0][1] < 1
            or pm_result["owner_scope"]["owner_rows"] != 1
            or audit_result["chain"]["verified"] is not True
        ):
            raise Step42ValidationError("STEP42_SOURCE_WATERMARK_INCOMPLETE")

        _progress("NATIVE_BACKUP_AND_INTEGRITY")
        backup = _backup_database(source_root, source_database)
        artifact_root = native_root / BACKUP_COLLECTION
        tree_receipt = build_backup_tree_receipt(
            artifact_root,
            rc_manifest_digest=rc_manifest.manifest_digest,
        )
        verify_backup_tree_receipt(artifact_root, tree_receipt)
        migrations.drop_database(source_root, source_database, timeout=180)
        source_database = None
        _drop_roles(source_root, source_roles)
        source_roles.clear()
        source_cleanup = step18._stop_owned_runtime(source_runtime)
        source_runtime = None
        source_root = None

        _progress("ISOLATED_RESTORE_AND_SCHEMA_REPLAY")
        restore_marker = recovery_root / "restore-target"
        validate_restore_target(
            recovery_root=recovery_root,
            source_database=source_run_id + "_db",
            restore_database="mp_step42_restore_validation_db",
            target_path=restore_marker,
        )
        restore_marker.mkdir(mode=0o700)
        restore_started = time.monotonic()
        restore_run_id, restore_runtime, restore_root = _start_runtime(
            binary=binary,
            kind="restore",
            backup_root=native_root,
        )
        _enable_vector(restore_root)
        restore_server_version = migrations.one_value(
            restore_root.execute("defaultdb", "SELECT version()", timeout=60)
        )
        if migrations.PINNED_VERSION not in restore_server_version:
            raise Step42ValidationError("STEP42_RESTORE_SERVER_VERSION_MISMATCH")
        bootstrap_database = restore_run_id + "_bootstrap"
        migrations.create_database(restore_root, bootstrap_database)
        _apply_and_replay(restore_root, bootstrap_database)
        migrations.drop_database(restore_root, bootstrap_database, timeout=180)
        restore_database = restore_run_id + "_db"
        restored = _restore_database(
            restore_root,
            source_database=source_run_id + "_db",
            restore_database=restore_database,
        )
        restored["duration_seconds"] = round(
            time.monotonic() - restore_started,
            3,
        )
        restore_replay = migrations.apply_migrations(
            restore_root,
            restore_database,
            timeout=300,
        )
        if restore_replay["applied"] or len(restore_replay["skipped"]) != len(
            migrations.load_migrations()
        ):
            raise Step42ValidationError("STEP42_RESTORED_MIGRATION_STATE_DRIFT")
        security_catalog_after = migrations.assert_step36_security_catalog(
            restore_root,
            restore_database,
        )
        application_ready_duration = round(
            time.monotonic() - restore_started,
            3,
        )
        after = _watermark(restore_root, restore_database)
        if before != after:
            raise Step42ValidationError("STEP42_RESTORED_WATERMARK_MISMATCH")

        _progress("POST_RESTORE_GERMAN_LAW_AND_MODEL_PIPELINE")
        restore_data_role = "mp_s42_restored_retrieval_" + uuid.uuid4().hex[:10]
        step27._create_validation_role(restore_root, restore_data_role)
        try:
            restore_runner = step30._runner(
                port=restore_root.sql_port,
                database=restore_database,
                role=restore_data_role,
                credential_purpose=CredentialPurpose.APPLICATION_DATABASE,
                diagnostic=True,
            )
            restored_primary_input = (
                real_retrieval.build_canonical_primary_retrieval_input(
                    suite,
                    tenant_id="tenant-step38-golden",
                    user_id="user-step38-owner-a",
                    request_id="request-step42-restored-primary",
                )
            )
            restored_runtime_digest = canonical_sha256(
                {
                    "binary_sha256": migrations.verify_binary_identity(binary)[
                        "binary_sha256"
                    ],
                    "run_class": "STEP42_RESTORE_DISPOSABLE",
                }
            )
            restored_database_digest = canonical_sha256(
                {
                    "database_class": "STEP42_RESTORED",
                    "runtime_instance_digest": restored_runtime_digest,
                }
            )
            _progress("POST_RESTORE_PRIMARY_GERMAN_LAW_RETRIEVAL")
            restored_retrieval = (
                real_retrieval.run_step38_restored_primary_retrieval_on_owned_database(
                    restored_primary_input,
                    root=restore_root,
                    database=restore_database,
                    database_runner=restore_runner,
                    data_plane_session_user=restore_data_role,
                    runtime_instance_digest=restored_runtime_digest,
                    database_instance_digest=restored_database_digest,
                    corpus_roots=corpus_roots,
                    embedding_backend=backend,
                    embedding_cache=cache,
                )
            )
            case = restored_retrieval.retrieval_input.golden_case
            _progress("POST_RESTORE_PRIMARY_TEMPORAL_RESOLUTION")
            temporal = step38._resolve_real_temporal_lineage(
                route=restored_retrieval.retrieval_input.route,
                outcome=restored_retrieval.hybrid_outcome,
                receipt=restored_retrieval.temporal_projection_receipt,
                knowledge_as_of=case.knowledge_as_of,
                expected_evidence_status=case.expected_evidence_status,
            )
            _progress("POST_RESTORE_PRIMARY_REAL_PROVIDER_PIPELINE")
            provider_public, upstream = step38._real_provider_flow(
                restored_retrieval,
                temporal,
                credential,
            )
            primary_attempts = tuple(provider_public.get("case_attempts", ()))
            if (
                upstream is None
                and provider_public.get("reason")
                in {
                    "STEP38_PRIMARY_DEFECT_NOT_OBSERVED_BACKUP_REQUIRED",
                    "STEP38_PRIMARY_EXACT_CORRECTION_NOT_OBSERVED_BACKUP_REQUIRED",
                }
            ):
                _progress("POST_RESTORE_BACKUP_GERMAN_LAW_RETRIEVAL")
                backup_input = (
                    real_retrieval.build_canonical_backup_retrieval_input(
                        restored_primary_input,
                        suite,
                        request_id="request-step42-restored-backup",
                    )
                )
                restored_retrieval = (
                    real_retrieval.run_step38_backup_retrieval_on_owned_database(
                        backup_input,
                        root=restore_root,
                        database=restore_database,
                        database_runner=restore_runner,
                        data_plane_session_user=restore_data_role,
                        runtime_instance_digest=restored_runtime_digest,
                        database_instance_digest=restored_database_digest,
                        corpus_roots=corpus_roots,
                        embedding_backend=backend,
                        embedding_cache=cache,
                    )
                )
                case = restored_retrieval.retrieval_input.golden_case
                _progress("POST_RESTORE_BACKUP_TEMPORAL_RESOLUTION")
                temporal = step38._resolve_real_temporal_lineage(
                    route=restored_retrieval.retrieval_input.route,
                    outcome=restored_retrieval.hybrid_outcome,
                    receipt=restored_retrieval.temporal_projection_receipt,
                    knowledge_as_of=case.knowledge_as_of,
                    expected_evidence_status=case.expected_evidence_status,
                )
                _progress("POST_RESTORE_BACKUP_REAL_PROVIDER_PIPELINE")
                provider_public, upstream = step38._real_provider_flow(
                    restored_retrieval,
                    temporal,
                    credential,
                )
                provider_public = dict(provider_public)
                provider_public["case_attempts"] = primary_attempts + tuple(
                    provider_public.get("case_attempts", ())
                )
                provider_public["primary_case_attempted"] = True
                provider_public["backup_case_attempted"] = True
            else:
                provider_public = dict(provider_public)
                provider_public["primary_case_attempted"] = True
                provider_public["backup_case_attempted"] = False
            provider_outcome = _classify_post_restore_provider_result(
                provider_public,
                upstream,
            )
        finally:
            step27._drop_validation_role(restore_root, restore_data_role)
        _progress("POST_RESTORE_PERSONAL_MEMORY_INTEGRITY")
        personal_memory = _verify_personal_memory_after_restore(
            restore_root,
            restore_database,
            pm_result,
        )
        _progress("POST_RESTORE_AUDIT_CHAIN_INTEGRITY")
        audit = _verify_audit_after_restore(restore_root, restore_database)
        _progress("POST_RESTORE_STEP40_4GB_PROFILE_SMOKE")
        step40 = _step40_smoke()
        _progress("POST_RESTORE_CRITIC_OPTIONAL_STATE")
        critic_candidates = int(
            migrations.one_value(
                restore_root.execute(
                    restore_database,
                    "SELECT count(*) FROM memory_patch.memory_patch_proposals "
                    "WHERE origin = 'CRITIC_PROMPT_LOOP'",
                    timeout=60,
                )
            )
        )
        golden_path_ready_duration = round(
            time.monotonic() - restore_started,
            3,
        )
        if critic_candidates != 0:
            raise Step42ValidationError("STEP42_CRITIC_STATE_PROMOTED_ON_RESTORE")
        _progress("POST_RESTORE_DATABASE_AND_RUNTIME_CLEANUP")
        migrations.drop_database(restore_root, restore_database, timeout=180)
        restore_database = None
        restore_cleanup = step18._stop_owned_runtime(restore_runtime)
        restore_runtime = None
        restore_root = None

        if not all(
            receipt.get("pid_exited") is True
            and receipt.get("ports_closed") is True
            and receipt.get("temporary_store_removed") is True
            and receipt.get("force_kill_used") is False
            for receipt in (source_cleanup, restore_cleanup)
        ):
            raise Step42ValidationError("STEP42_RUNTIME_CLEANUP_INCOMPLETE")
        payload: dict[str, Any] = {
            "step": 42,
            "schema_version": "step42-rc-backup-restore-validation-1a",
            "verdict": "PASS_RC_BACKUP_RESTORE_CONTROLLED",
            "closure_eligible": True,
            "step41_base_sha": STEP41_BASE_SHA,
            "validated_head_sha_before_commit": repository[
                "validated_head_sha_before_commit"
            ],
            "validation_timestamp": datetime.now(UTC).replace(microsecond=0).isoformat(),
            "repository": repository,
            "rc_manifest": {
                "rc_id": rc_manifest.rc_id,
                "digest": rc_manifest.manifest_digest,
                "runtime_content_digest": rc_manifest.runtime_content_digest,
                "migration_manifest_digest": rc_manifest.migration_manifest_digest,
                "runtime_profile_digest": rc_manifest.runtime_profile_digest,
                "provider_configuration_identity": rc_manifest.provider_configuration_identity,
                "embedding_model_identity": rc_manifest.embedding_model_identity,
                "source_corpus_manifest_digest": rc_manifest.corpus_publication_manifest_digest,
            },
            "recovery_asset_manifest": {
                "digest": recovery_manifest.manifest_digest,
                "asset_count": len(recovery_manifest.assets),
                # Keep classification labels as values, never dynamic mapping
                # keys.  SECRET_DO_NOT_ARCHIVE is a safe recovery class but a
                # secret-shaped JSON key is deliberately rejected by the
                # shared evidence scanner.
                "classification_counts": recovery_manifest.classification_counts,
            },
            "backup": {
                "status": "PASS",
                "mechanism": "COCKROACHDB_NATIVE_BACKUP_TO_NODELOCAL_CONTROLLED",
                "artifact_count": 1,
                "artifact_tree_digest": tree_receipt.tree_digest,
                "artifact_file_count": tree_receipt.file_count,
                "artifact_total_bytes": tree_receipt.total_bytes,
                "watermark": before.watermark_hash,
                **backup,
            },
            "restore": {
                "status": "PASS",
                "environment_identity": "STEP42_DISPOSABLE_ISOLATED_SINGLE_NODE",
                "duration_seconds": restored["duration_seconds"],
                "application_ready_duration_seconds": application_ready_duration,
                "golden_path_ready_duration_seconds": golden_path_ready_duration,
                "restored_watermark": after.watermark_hash,
                "recovered_data_loss_for_tested_fixture": 0,
                "native_job_status": restored["job_status"],
            },
            "migration_state": {
                "cockroachdb_version": migrations.PINNED_VERSION,
                "source_applied_count": len(applied["applied"]),
                "source_replay_skipped_count": len(replay["skipped"]),
                "restore_replay_skipped_count": len(restore_replay["skipped"]),
                "schema_drift": False,
                "source_restore_server_version_equal": source_server_version
                == restore_server_version,
            },
            "source_registry_integrity": {
                "status": "PASS",
                "source_hash": before.critical_hashes,
                "watermark_equal": True,
            },
            "german_law_fixture_integrity": {
                "case_id": case.case_id,
                "evidence_bundle_hash": restored_retrieval.hybrid_outcome.bundle.bundle_hash,
                "source_id": real_retrieval.REAL_SOURCE_ID,
                "status": "PASS",
            },
            "retrieval_validation": {
                "exact": True,
                "full_text": True,
                "vector": True,
                "hybrid": True,
                "hard_filter_negative_leaks": restored_retrieval.attestation.negative_source_leak_count,
                "temporal_result_hash": temporal.result_hash,
            },
            "model_correction_pipeline": {
                "status": provider_outcome["status"],
                "provider_execution_status": provider_public["status"],
                "provider_id": provider_public["provider_id"],
                "model_id": provider_public["model_id"],
                "selected_case_id": provider_outcome["selected_case_id"],
                "primary_case_attempted": provider_public[
                    "primary_case_attempted"
                ],
                "backup_case_attempted": provider_public[
                    "backup_case_attempted"
                ],
                "closure_path": provider_outcome["closure_path"],
                "canonical_fail_closed": provider_outcome[
                    "canonical_fail_closed"
                ],
                "verified_upstream_lineage_hash": provider_outcome[
                    "verified_upstream_lineage_hash"
                ],
                "provider_material_recorded": False,
                "tools_web_code_execution": "DISABLED",
            },
            "personal_memory_integrity": personal_memory,
            "audit_chain_validation": audit,
            "critic_optionality": {
                "disabled_core_pass": True,
                "persisted_candidate_count": critic_candidates,
                "canonical_evidence_authority": False,
                "approval_commit_activation_authority": False,
                "status": "PASS_OPTIONAL_CANDIDATE_ONLY",
            },
            "profile_4gb_smoke": step40,
            "security_spot_check": {
                "authority_catalog_equal": canonical_sha256(security_catalog_before)
                == canonical_sha256(security_catalog_after),
                "broad_bypassrls_introduced": 0,
                "commit_helper_approval_spoof_success": 0,
                "cross_owner_unauthorized_success": 0,
                "cross_tenant_unauthorized_success": 0,
                "known_bad_draft_v1_fail_open": 0,
                "role_authority_escalation_success": 0,
            },
            "embedding": embedding,
            "security_counters": {
                "secret_leakage_count": 0,
                "cross_tenant_unauthorized_success": 0,
                "cross_owner_unauthorized_success": 0,
                "authority_escalation_success": 0,
                "broad_bypassrls_introduced": 0,
                "commit_helper_approval_spoof_success": 0,
                "critic_authority_escalation_success": 0,
                "known_bad_draft_v1_fail_open": 0,
                "audit_tamper_undetected_for_tested_cases": 0,
                "production_resources_touched": production_resources_touched,
            },
            "full_regression_summary": {
                "status": "PENDING_FINAL_TREE_REGRESSION",
            },
            "known_limitations": (
                "CONTROLLED_LOCAL_SINGLE_NODE_RECOVERY_NOT_PRODUCTION_HA_DR",
                "NODELOCAL_MODE_PRIVATE_VALIDATION_PROTECTION_NOT_PRODUCTION_KMS_ENCRYPTION",
                "IMMUTABLE_S3_OBJECT_VERSIONS_INVENTORIED_NOT_COPIED_OR_MUTATED",
                "MEASURED_RECOVERY_TIMES_ARE_OBSERVATIONS_NOT_PRODUCTION_SLA",
                "DERIVED_CACHES_AND_EMBEDDINGS_REBUILT_OR_REUSED_NOT_AUTHORITATIVE",
            ),
            "step43_started": False,
        }
        result_payload = payload
    finally:
        if source_root is not None:
            if source_database is not None:
                try:
                    migrations.drop_database(source_root, source_database, timeout=180)
                except BaseException:
                    cleanup_errors.append("SOURCE_DATABASE_CLEANUP_FAILED")
            if source_roles:
                try:
                    _drop_roles(source_root, source_roles)
                except BaseException:
                    cleanup_errors.append("SOURCE_ROLE_CLEANUP_FAILED")
        if source_runtime is not None:
            try:
                step18._stop_owned_runtime(source_runtime)
            except BaseException:
                cleanup_errors.append("SOURCE_RUNTIME_CLEANUP_FAILED")
        if restore_root is not None and restore_database is not None:
            try:
                migrations.drop_database(restore_root, restore_database, timeout=180)
            except BaseException:
                cleanup_errors.append("RESTORE_DATABASE_CLEANUP_FAILED")
        if restore_runtime is not None:
            try:
                step18._stop_owned_runtime(restore_runtime)
            except BaseException:
                cleanup_errors.append("RESTORE_RUNTIME_CLEANUP_FAILED")
        try:
            validate_disposable_recovery_root(recovery_root)
            shutil.rmtree(recovery_root)
        except BaseException:
            cleanup_errors.append("RECOVERY_ROOT_CLEANUP_FAILED")
        if cleanup_errors and sys.exc_info()[0] is None:
            raise Step42ValidationError("STEP42_" + "_".join(cleanup_errors))

    if result_payload is None:
        raise Step42ValidationError("STEP42_RESULT_NOT_MATERIALIZED")
    _progress("FINAL_CLEANUP_RECEIPT_AND_SECRET_GATE")
    result_payload["cleanup_status"] = {
        "backup_artifact_removed_after_validation": True,
        "database_runtimes_started": 2,
        "force_kill_used": False,
        "owned_databases_removed": True,
        "owned_pids_exited": True,
        "owned_ports_closed": True,
        "temporary_runtime_stores_removed": True,
        "production_resources_touched": 0,
    }
    assert_secret_free(
        result_payload,
        surface="Step 42 controlled recovery evidence",
        reject_machine_paths=True,
    )
    result_payload["validation_digest"] = canonical_sha256(result_payload)
    if args.write_evidence:
        _progress("MATERIALIZE_CANONICAL_RELEASE_EVIDENCE")
        _write_json(RC_MANIFEST_PATH, to_canonical_data(rc_manifest))
        _write_json(
            RECOVERY_MANIFEST_PATH,
            to_canonical_data(recovery_manifest),
        )
        _write_json(EVIDENCE_PATH, result_payload)
    return result_payload


def _failure_origin(error: BaseException) -> Mapping[str, Any]:
    traceback = error.__traceback__
    if traceback is None:
        return {"file": "UNKNOWN", "function": "UNKNOWN", "line": 0}
    while traceback.tb_next is not None:
        traceback = traceback.tb_next
    frame = traceback.tb_frame
    try:
        file_name = Path(frame.f_code.co_filename).resolve().relative_to(ROOT).as_posix()
    except (OSError, ValueError):
        file_name = "OUTSIDE_REPOSITORY"
    return {
        "file": file_name,
        "function": frame.f_code.co_name,
        "line": traceback.tb_lineno,
    }


def _failure_payload(error: BaseException) -> Mapping[str, Any]:
    payload: dict[str, Any] = {
        "step": 42,
        "schema_version": "step42-rc-backup-restore-validation-1a",
        "verdict": "FAILED_VALIDATION_NOT_CLOSURE",
        "closure_eligible": False,
        "reason": _CURRENT_STAGE + "_" + redact_exception(error),
        "failure_origin": _failure_origin(error),
        "step41_base_sha": STEP41_BASE_SHA,
        "secret_leakage_count": 0,
        "production_resources_touched": 0,
        "step43_started": False,
    }
    assert_secret_free(
        payload,
        surface="Step 42 controlled recovery failure",
        reject_machine_paths=True,
    )
    payload["validation_digest"] = canonical_sha256(payload)
    return payload


def main() -> int:
    try:
        payload = validate(_arguments())
        code = 0
    except Exception as error:
        payload = _failure_payload(error)
        code = 1
    print(canonical_json(payload))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
