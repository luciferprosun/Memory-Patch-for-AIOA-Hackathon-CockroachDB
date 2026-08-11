#!/usr/bin/env python3
"""Validate Memory Patch Step 5 tenant/user isolation on CockroachDB v26.2.4.

Offline validation is deterministic and opens no socket. Live validation needs
an explicit opt-in, the exact pinned full-server binary, one loopback-only
single-node runtime, and a JSON output on the approved external volume.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence


SCRIPT_ROOT = Path(__file__).resolve().parent
SOURCE_ROOT = SCRIPT_ROOT.parent / "src"
for import_root in (SCRIPT_ROOT, SOURCE_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

import run_cockroachdb_migrations as migrations  # noqa: E402
from aioa_memory_kernel.security.credentials import (  # noqa: E402
    build_minimal_subprocess_environment,
)


REPOSITORY_ROOT = SCRIPT_ROOT.parent
PINNED_VERSION = "v26.2.4"
PINNED_CLUSTER_VERSION = "26.2"
RUN_PREFIX = "mp_step5_"
FIXED_ROLES = (
    "mp_app_runtime",
    "mp_request_context_setter",
    "mp_schema_owner",
    "mp_security_owner",
)
EXPECTED_POLICY_COUNT = 50
EXPECTED_PROTECTED_TABLE_COUNT = 27
ROLE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{2,62}$")


class RlsValidationError(RuntimeError):
    """The Step 5 validation contract was not met."""


@dataclass
class RoleSqlClient:
    binary: Path
    port: int
    user: str
    host: str = "127.0.0.1"

    def _command(
        self,
        database: str,
        sql: str,
        *,
        errexit: bool = True,
    ) -> tuple[list[str], dict[str, str]]:
        migrations.validate_database_identifier(database)
        migrations.require_loopback(self.host)
        validate_role_identifier(self.user)
        environment = build_minimal_subprocess_environment(os.environ)
        return (
            [
                str(self.binary),
                "sql",
                "--format=tsv",
                f"--set=errexit={'true' if errexit else 'false'}",
                "--insecure",
                f"--host={self.host}",
                f"--port={self.port}",
                f"--database={database}",
                f"--user={self.user}",
                f"--execute={sql}",
            ],
            environment,
        )

    def execute(
        self,
        database: str,
        sql: str,
        *,
        timeout: float = migrations.DEFAULT_TIMEOUT_SECONDS,
    ) -> str:
        command, environment = self._command(database, sql)
        result = migrations.run_process(
            command,
            timeout=timeout,
            environment=environment,
        )
        if result.returncode != 0:
            raise migrations.SqlError(
                migrations.sanitize_error(result.stderr),
                sqlstate=migrations.extract_sqlstate(result.stderr),
            )
        return result.stdout

    def execute_continuing(
        self,
        database: str,
        sql: str,
        *,
        timeout: float = migrations.DEFAULT_TIMEOUT_SECONDS,
    ) -> migrations.ProcessResult:
        command, environment = self._command(database, sql, errexit=False)
        return migrations.run_process(
            command,
            timeout=timeout,
            environment=environment,
        )

    def execute_stdin_continuing(
        self,
        database: str,
        sql: str,
        *,
        timeout: float = migrations.DEFAULT_TIMEOUT_SECONDS,
    ) -> migrations.ProcessResult:
        migrations.validate_timeout(timeout)
        command, environment = self._command(database, "", errexit=False)
        if not command[-1].startswith("--execute="):
            raise RlsValidationError("unexpected CockroachDB CLI command shape")
        command = [*command[:-1], "--no-line-editor"]
        try:
            completed = subprocess.run(
                command,
                input=sql.rstrip() + "\n\\q\n",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout,
                env=environment,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RlsValidationError(
                f"persistent SQL probe exceeded {timeout} seconds"
            ) from exc
        return migrations.ProcessResult(
            completed.returncode,
            completed.stdout,
            completed.stderr,
        )

    def expect_error(
        self,
        database: str,
        sql: str,
        *,
        expected: set[str] | None = None,
        timeout: float = migrations.DEFAULT_TIMEOUT_SECONDS,
    ) -> str:
        command, environment = self._command(database, sql)
        result = migrations.run_process(
            command,
            timeout=timeout,
            environment=environment,
        )
        if result.returncode == 0:
            raise RlsValidationError("negative SQL probe unexpectedly succeeded")
        state = migrations.extract_sqlstate(result.stderr)
        if state is None:
            raise RlsValidationError(
                "negative SQL probe failed without a captured SQLSTATE"
            )
        if expected is not None and state not in expected:
            raise RlsValidationError(
                f"negative SQL probe expected {sorted(expected)}, got {state}"
            )
        return state


@dataclass
class ProbeRecorder:
    rows: list[dict[str, Any]] = field(default_factory=list)

    def check(
        self,
        probe_id: str,
        category: str,
        condition: bool,
        observed: str,
        *,
        sqlstate: str | None = None,
    ) -> None:
        row: dict[str, Any] = {
            "category": category,
            "observed": observed,
            "probe_id": probe_id,
            "status": "PASS" if condition else "FAIL",
        }
        if sqlstate is not None:
            row["sqlstate"] = sqlstate
        self.rows.append(row)
        if not condition:
            raise RlsValidationError(f"{probe_id} failed: {observed}")

    def summary(self) -> dict[str, Any]:
        categories: dict[str, int] = {}
        for row in self.rows:
            categories[row["category"]] = categories.get(row["category"], 0) + 1
        return {
            "category_counts": dict(sorted(categories.items())),
            "fail_count": sum(row["status"] != "PASS" for row in self.rows),
            "pass_count": sum(row["status"] == "PASS" for row in self.rows),
            "probe_count": len(self.rows),
        }


def validate_role_identifier(role: str) -> None:
    if not ROLE_PATTERN.fullmatch(role):
        raise RlsValidationError(f"unsafe SQL role identifier: {role!r}")


def role_identifier(role: str) -> str:
    validate_role_identifier(role)
    return f'"{role}"'


def hash_for(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def extract_named_scalar(output: str, column: str = "probe_value") -> str:
    lines = [line.rstrip("\r") for line in output.splitlines()]
    positions = [index for index, line in enumerate(lines) if line == column]
    if not positions:
        raise RlsValidationError(
            f"SQL output lacks scalar column {column!r}; "
            f"sanitized line count={len(lines)}, tail={lines[-8:]!r}"
        )
    index = positions[-1] + 1
    if index >= len(lines):
        raise RlsValidationError("SQL scalar output has no value")
    return lines[index]


def context_setter_sql(
    tenant_id: str,
    user_id: str | None,
    access_mode: str,
) -> str:
    user_literal = "NULL" if user_id is None else migrations.sql_literal(user_id)
    return (
        "SELECT memory_patch.set_request_context("
        f"{migrations.sql_literal(tenant_id)}, "
        f"{user_literal}, "
        f"{migrations.sql_literal(access_mode)}"
        ")"
    )


def context_transaction(
    tenant_id: str,
    user_id: str | None,
    access_mode: str,
    body: str,
) -> str:
    return (
        "BEGIN;\n"
        + context_setter_sql(tenant_id, user_id, access_mode)
        + ";\n"
        + body.rstrip().rstrip(";")
        + ";\nCOMMIT;"
    )


def context_scalar(
    client: RoleSqlClient,
    database: str,
    tenant_id: str,
    user_id: str | None,
    access_mode: str,
    query: str,
) -> str:
    output = client.execute(
        database,
        context_transaction(
            tenant_id,
            user_id,
            access_mode,
            query,
        ),
    )
    return extract_named_scalar(output)


def expected_error_in_context(
    client: RoleSqlClient,
    database: str,
    tenant_id: str,
    user_id: str | None,
    access_mode: str,
    statement: str,
    *,
    expected: set[str],
) -> str:
    return client.expect_error(
        database,
        context_transaction(
            tenant_id,
            user_id,
            access_mode,
            statement,
        ),
        expected=expected,
    )


def external_runtime_parent(json_output: Path) -> Path:
    output = json_output.expanduser().resolve()
    if output.is_relative_to(REPOSITORY_ROOT.resolve()):
        raise RlsValidationError("live evidence output must stay outside the repository")
    if output.parent.name != "reports":
        raise RlsValidationError("live evidence output must use the reports directory")
    candidate = output.parent.parent / "cache" / "temporary"
    migrations.assert_safe_runtime_parent(candidate)
    return candidate


def canonical_json_bytes(value: Any) -> bytes:
    return migrations.canonical_json_bytes(value)


def offline_validate() -> dict[str, Any]:
    migration_result = migrations.offline_validate()
    security = migrations.load_security_manifest()
    step5_sql = (
        migrations.MIGRATION_ROOT
        / "0004_step5_tenant_roles_session_context_rls.sql"
    ).read_text(encoding="utf-8")
    policy_count = len(
        re.findall(
            r"^CREATE POLICY IF NOT EXISTS [a-z0-9_]+",
            step5_sql,
            re.MULTILINE,
        )
    )
    if policy_count != EXPECTED_POLICY_COUNT:
        raise RlsValidationError("offline policy count is not exact")
    if security["protected_table_count"] != EXPECTED_PROTECTED_TABLE_COUNT:
        raise RlsValidationError("offline protected-table count is not exact")
    if any(role["bypassrls"] for role in security["roles"]):
        raise RlsValidationError("a fixed role is classified with BYPASSRLS")
    if any(role["member_of"] for role in security["roles"]):
        raise RlsValidationError("a fixed role inherits another role")
    if re.search(
        r"(?i)\b(?:model|provider|critic|hat|nvidia|openshell)_"
        r"(?:login|runtime|role)\b",
        step5_sql,
    ):
        raise RlsValidationError("forbidden model/HAT/provider role exists")
    if "COMMENT ON FUNCTION" in step5_sql:
        raise RlsValidationError("unsupported CockroachDB function comment remains")
    if len(security["scope_identity_guards"]) != 2:
        raise RlsValidationError("scope-identity guard coverage is incomplete")
    for migration_id, expected_hash in migrations.STEP4_MIGRATION_HASHES.items():
        path = migrations.MIGRATION_ROOT / f"{migration_id}.sql"
        if migrations.file_sha256(path) != expected_hash:
            raise RlsValidationError(f"immutable Step 4 migration changed: {migration_id}")
    return {
        "fixed_role_count": len(security["roles"]),
        "identity_guard_trigger_count": len(security["scope_identity_guards"]),
        "migration_count": migration_result["migration_count"],
        "policy_count": policy_count,
        "protected_table_count": security["protected_table_count"],
        "status": "PASS",
        "step4_table_count": security["step4_table_count"],
        "target_version": PINNED_VERSION,
    }


def fixture_ids(run_id: str) -> dict[str, str]:
    values = {
        "tenant_a": f"{run_id}_tenant_a",
        "tenant_b": f"{run_id}_tenant_b",
        "user_a1": f"{run_id}_user_a1",
        "user_a2": f"{run_id}_user_a2",
        "user_b1": f"{run_id}_user_b1",
        "hat": f"{run_id}_knowledge_hat",
    }
    for suffix in ("a1", "a2", "b1"):
        for kind in (
            "action",
            "approval",
            "audit",
            "binding",
            "bundle",
            "claim",
            "commit",
            "draft",
            "memory",
            "packet",
            "proposal",
            "requirement",
            "route",
            "run",
            "space",
            "scope_personal",
            "transition",
        ):
            values[f"{kind}_{suffix}"] = f"{run_id}_{kind}_{suffix}"
    for suffix in ("a", "b"):
        for kind in (
            "chunk",
            "evidence",
            "scope_shared",
            "shared_memory",
            "snapshot",
            "source",
            "version",
        ):
            values[f"{kind}_{suffix}"] = f"{run_id}_{kind}_{suffix}"
    values["packet_a1"] = hash_for(values["packet_a1"])
    values["packet_a2"] = hash_for(values["packet_a2"])
    values["packet_b1"] = hash_for(values["packet_b1"])
    return values


def json_sql(value: Any) -> str:
    return migrations.sql_literal(
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    ) + "::JSONB"


def build_fixture_sql(ids: Mapping[str, str]) -> str:
    q = migrations.sql_literal
    at = "'2026-07-28T00:00:00Z'::TIMESTAMPTZ"
    statements: list[str] = []
    statements.append(
        "INSERT INTO memory_patch.tenants "
        "(tenant_id, display_name, metadata, created_at, updated_at) VALUES "
        f"({q(ids['tenant_a'])}, 'Synthetic Tenant A', '{{}}'::JSONB, {at}, {at}), "
        f"({q(ids['tenant_b'])}, 'Synthetic Tenant B', '{{}}'::JSONB, {at}, {at})"
    )
    statements.append(
        "INSERT INTO memory_patch.users "
        "(tenant_id, user_id, display_name, metadata, created_at, updated_at) VALUES "
        f"({q(ids['tenant_a'])}, {q(ids['user_a1'])}, 'Synthetic User A1', "
        f"'{{}}'::JSONB, {at}, {at}), "
        f"({q(ids['tenant_a'])}, {q(ids['user_a2'])}, 'Synthetic User A2', "
        f"'{{}}'::JSONB, {at}, {at}), "
        f"({q(ids['tenant_b'])}, {q(ids['user_b1'])}, 'Synthetic User B1', "
        f"'{{}}'::JSONB, {at}, {at})"
    )
    statements.append(
        "INSERT INTO memory_patch.hat_manifests "
        "(hat_id, hat_version, schema_version, display_name, manifest_hash, "
        "capabilities, approval_authority, commit_authority, "
        "canonical_write_authority, external_action_authority, "
        "allows_private_memory_access, allows_user_code, created_at) VALUES "
        f"({q(ids['hat'])}, '1.0.0', '1.0.0', 'Synthetic Knowledge HAT', "
        f"{q(hash_for(ids['hat']))}, '[]'::JSONB, 'NONE', 'NONE', 'NONE', "
        f"'NONE', false, false, {at})"
    )

    principals = (
        ("a1", "tenant_a", "user_a1"),
        ("a2", "tenant_a", "user_a2"),
        ("b1", "tenant_b", "user_b1"),
    )
    for suffix, tenant_key, user_key in principals:
        statements.append(
            "INSERT INTO memory_patch.personal_memory_spaces "
            "(tenant_id, user_id, personal_memory_space_id, schema_version, "
            "state, display_name, created_at, updated_at) VALUES "
            f"({q(ids[tenant_key])}, {q(ids[user_key])}, {q(ids[f'space_{suffix}'])}, "
            f"'1.0.0', 'ACTIVE', {q('Synthetic Memory ' + suffix.upper())}, "
            f"{at}, {at})"
        )
        statements.append(
            "INSERT INTO memory_patch.personal_memory_model_bindings "
            "(tenant_id, user_id, personal_memory_space_id, model_binding_id, "
            "bound_at) VALUES "
            f"({q(ids[tenant_key])}, {q(ids[user_key])}, "
            f"{q(ids[f'space_{suffix}'])}, {q(ids[f'binding_{suffix}'])}, {at})"
        )
        statements.append(
            "INSERT INTO memory_patch.hat_scopes "
            "(tenant_id, hat_scope_id, target_scope, owner_user_id, "
            "personal_memory_space_id, created_at) VALUES "
            f"({q(ids[tenant_key])}, {q(ids[f'scope_personal_{suffix}'])}, "
            f"'USER_PERSONAL_HAT', {q(ids[user_key])}, "
            f"{q(ids[f'space_{suffix}'])}, {at})"
        )

    for suffix, tenant_key in (("a", "tenant_a"), ("b", "tenant_b")):
        statements.append(
            "INSERT INTO memory_patch.hat_scopes "
            "(tenant_id, hat_scope_id, target_scope, knowledge_hat_id, "
            "knowledge_hat_version, created_at) VALUES "
            f"({q(ids[tenant_key])}, {q(ids[f'scope_shared_{suffix}'])}, "
            f"'SHARED_KNOWLEDGE_HAT', {q(ids['hat'])}, '1.0.0', {at})"
        )
        statements.append(
            "INSERT INTO memory_patch.knowledge_sources "
            "(tenant_id, source_id, hat_scope_id, source_kind, source_reference, "
            "provenance, created_at) VALUES "
            f"({q(ids[tenant_key])}, {q(ids[f'source_{suffix}'])}, "
            f"{q(ids[f'scope_shared_{suffix}'])}, 'SYNTHETIC', "
            f"{q('synthetic://source/' + suffix)}, "
            f"{json_sql({'producer': 'step5-live-validation'})}, {at})"
        )
        statements.append(
            "INSERT INTO memory_patch.source_snapshots "
            "(tenant_id, snapshot_id, source_id, hat_scope_id, content_sha256, "
            "byte_length, storage_class, immutable_object_reference, captured_at, "
            "provenance) VALUES "
            f"({q(ids[tenant_key])}, {q(ids[f'snapshot_{suffix}'])}, "
            f"{q(ids[f'source_{suffix}'])}, {q(ids[f'scope_shared_{suffix}'])}, "
            f"{q(hash_for('snapshot-' + suffix))}, 64, 'CRDB_TRANSACTIONAL', "
            f"{q('synthetic://snapshot/' + suffix)}, {at}, '{{}}'::JSONB)"
        )
        statements.append(
            "INSERT INTO memory_patch.knowledge_versions "
            "(tenant_id, knowledge_version_id, source_id, snapshot_id, "
            "hat_scope_id, version_ordinal, normalized_content_sha256, "
            "normalization_profile, is_current, created_at, provenance) VALUES "
            f"({q(ids[tenant_key])}, {q(ids[f'version_{suffix}'])}, "
            f"{q(ids[f'source_{suffix}'])}, {q(ids[f'snapshot_{suffix}'])}, "
            f"{q(ids[f'scope_shared_{suffix}'])}, 1, "
            f"{q(hash_for('version-' + suffix))}, 'synthetic-v1', true, "
            f"{at}, '{{}}'::JSONB)"
        )
        statements.append(
            "INSERT INTO memory_patch.knowledge_chunks "
            "(tenant_id, chunk_id, knowledge_version_id, source_id, hat_scope_id, "
            "chunk_ordinal, content_text, content_sha256, start_offset, end_offset, "
            "language_tag, metadata, created_at) VALUES "
            f"({q(ids[tenant_key])}, {q(ids[f'chunk_{suffix}'])}, "
            f"{q(ids[f'version_{suffix}'])}, {q(ids[f'source_{suffix}'])}, "
            f"{q(ids[f'scope_shared_{suffix}'])}, 0, "
            f"{q('synthetic shared content ' + suffix)}, "
            f"{q(hash_for('chunk-' + suffix))}, 0, 26, 'en', "
            f"'{{}}'::JSONB, {at})"
        )
        statements.append(
            "INSERT INTO memory_patch.chunk_search_documents "
            "(tenant_id, chunk_id, knowledge_version_id, source_id, hat_scope_id, "
            "search_config, search_vector, created_at) VALUES "
            f"({q(ids[tenant_key])}, {q(ids[f'chunk_{suffix}'])}, "
            f"{q(ids[f'version_{suffix}'])}, {q(ids[f'source_{suffix}'])}, "
            f"{q(ids[f'scope_shared_{suffix}'])}, 'english', "
            f"to_tsvector('english', {q('synthetic shared content ' + suffix)}), "
            f"{at})"
        )
        statements.append(
            "INSERT INTO memory_patch.evidence_items "
            "(tenant_id, evidence_id, source_id, knowledge_version_id, "
            "hat_scope_id, citation_reference, content_sha256, trust_class, "
            "authority_rank, scope_dimensions, metadata, retrieved_at) VALUES "
            f"({q(ids[tenant_key])}, {q(ids[f'evidence_{suffix}'])}, "
            f"{q(ids[f'source_{suffix}'])}, {q(ids[f'version_{suffix}'])}, "
            f"{q(ids[f'scope_shared_{suffix}'])}, "
            f"{q('synthetic://citation/' + suffix)}, "
            f"{q(hash_for('evidence-' + suffix))}, "
            f"'CANONICAL_SOURCE_EVIDENCE', 1, '[]'::JSONB, '{{}}'::JSONB, {at})"
        )
        statements.append(
            "INSERT INTO memory_patch.memory_items "
            "(tenant_id, memory_item_id, schema_version, hat_scope_id, "
            "target_scope, visibility, trust_class, content_kind, content, "
            "scope_dimensions, evidence_references, source_patch_id, active, "
            "revoked, created_at) VALUES "
            f"({q(ids[tenant_key])}, {q(ids[f'shared_memory_{suffix}'])}, "
            f"'1.0.0', {q(ids[f'scope_shared_{suffix}'])}, "
            f"'SHARED_KNOWLEDGE_HAT', 'SHARED', 'SHARED_HAT_VERIFIED_MEMORY', "
            f"'FACTUAL', {json_sql({'value': 'shared-' + suffix})}, "
            f"'[]'::JSONB, {json_sql([ids[f'evidence_{suffix}']])}, "
            f"{q('synthetic-patch-' + suffix)}, false, false, {at})"
        )

    for suffix, tenant_key, user_key in principals:
        tenant_suffix = "a" if suffix in {"a1", "a2"} else "b"
        statements.append(
            "INSERT INTO memory_patch.kernel_runs "
            "(tenant_id, kernel_run_id, user_id, personal_memory_space_id, "
            "model_binding_id, request_sha256, created_at) VALUES "
            f"({q(ids[tenant_key])}, {q(ids[f'run_{suffix}'])}, "
            f"{q(ids[user_key])}, {q(ids[f'space_{suffix}'])}, "
            f"{q(ids[f'binding_{suffix}'])}, {q(hash_for('request-' + suffix))}, "
            f"{at})"
        )
        statements.append(
            "INSERT INTO memory_patch.routing_decisions "
            "(tenant_id, routing_decision_id, kernel_run_id, knowledge_route, "
            "selected_hat_scope_id, selected_hat_id, reason_codes, decided_at) "
            "VALUES "
            f"({q(ids[tenant_key])}, {q(ids[f'route_{suffix}'])}, "
            f"{q(ids[f'run_{suffix}'])}, 'HAT_ASSIST', "
            f"{q(ids[f'scope_shared_{tenant_suffix}'])}, {q(ids['hat'])}, "
            f"{json_sql(['synthetic'])}, {at})"
        )
        statements.append(
            "INSERT INTO memory_patch.action_policy_decisions "
            "(tenant_id, action_policy_decision_id, kernel_run_id, action_policy, "
            "reason_codes, decided_at) VALUES "
            f"({q(ids[tenant_key])}, {q(ids[f'action_{suffix}'])}, "
            f"{q(ids[f'run_{suffix}'])}, 'ALLOW', {json_sql(['synthetic'])}, {at})"
        )
        statements.append(
            "INSERT INTO memory_patch.drafts "
            "(tenant_id, draft_id, kernel_run_id, draft_stage, content_sha256, "
            "immutable_content_reference, created_at) VALUES "
            f"({q(ids[tenant_key])}, {q(ids[f'draft_{suffix}'])}, "
            f"{q(ids[f'run_{suffix}'])}, 1, {q(hash_for('draft-' + suffix))}, "
            f"{q('synthetic://draft/' + suffix)}, {at})"
        )
        statements.append(
            "INSERT INTO memory_patch.claims "
            "(tenant_id, claim_id, kernel_run_id, draft_id, statement, "
            "claim_category, scope_dimensions, created_at) VALUES "
            f"({q(ids[tenant_key])}, {q(ids[f'claim_{suffix}'])}, "
            f"{q(ids[f'run_{suffix}'])}, {q(ids[f'draft_{suffix}'])}, "
            f"{q('synthetic claim ' + suffix)}, 'SYNTHETIC', '[]'::JSONB, {at})"
        )
        statements.append(
            "INSERT INTO memory_patch.evidence_bundles "
            "(tenant_id, evidence_bundle_id, kernel_run_id, hat_scope_id, hat_id, "
            "evidence_status, retrieval_policy_version, bundle_hash, created_at) "
            "VALUES "
            f"({q(ids[tenant_key])}, {q(ids[f'bundle_{suffix}'])}, "
            f"{q(ids[f'run_{suffix}'])}, {q(ids[f'scope_shared_{tenant_suffix}'])}, "
            f"{q(ids['hat'])}, 'SUFFICIENT', 'synthetic-v1', "
            f"{q(hash_for('bundle-' + suffix))}, {at})"
        )
        statements.append(
            "INSERT INTO memory_patch.evidence_bundle_items "
            "(tenant_id, evidence_bundle_id, evidence_id, item_ordinal) VALUES "
            f"({q(ids[tenant_key])}, {q(ids[f'bundle_{suffix}'])}, "
            f"{q(ids[f'evidence_{tenant_suffix}'])}, 0)"
        )
        statements.append(
            "INSERT INTO memory_patch.claim_verdicts "
            "(tenant_id, claim_id, verdict, evidence_references, verifier_id, "
            "explanation_code, verified_at) VALUES "
            f"({q(ids[tenant_key])}, {q(ids[f'claim_{suffix}'])}, 'SUPPORTED', "
            f"{json_sql([ids[f'evidence_{tenant_suffix}']])}, "
            f"{q('synthetic-verifier')}, 'SYNTHETIC_SUPPORTED', {at})"
        )
        statements.append(
            "INSERT INTO memory_patch.correction_packets "
            "(tenant_id, packet_hash, schema_version, kernel_run_id, draft_v1_id, "
            "selected_hat_scope_id, selected_hat_id, knowledge_route, "
            "action_policy, evidence_status, packet_payload, uncertainty, "
            "retrieval_policy_version, persisted_at) VALUES "
            f"({q(ids[tenant_key])}, {q(ids[f'packet_{suffix}'])}, '1.0.0', "
            f"{q(ids[f'run_{suffix}'])}, {q(ids[f'draft_{suffix}'])}, "
            f"{q(ids[f'scope_shared_{tenant_suffix}'])}, {q(ids['hat'])}, "
            f"'HAT_ASSIST', 'ALLOW', 'SUFFICIENT', '{{}}'::JSONB, 0, "
            f"'synthetic-v1', {at})"
        )
        statements.append(
            "INSERT INTO memory_patch.correction_requirements "
            "(tenant_id, packet_hash, requirement_id, claim_id, instruction, "
            "evidence_references, mandatory) VALUES "
            f"({q(ids[tenant_key])}, {q(ids[f'packet_{suffix}'])}, "
            f"{q(ids[f'requirement_{suffix}'])}, {q(ids[f'claim_{suffix}'])}, "
            f"'Use synthetic evidence', "
            f"{json_sql([ids[f'evidence_{tenant_suffix}']])}, true)"
        )
        proposal_hash = hash_for("proposal-" + suffix)
        approval_proof = hash_for("approval-" + suffix)
        statements.append(
            "INSERT INTO memory_patch.memory_patch_proposals "
            "(tenant_id, proposal_id, schema_version, hat_scope_id, target_scope, "
            "target_hat_id, owner_user_id, personal_memory_space_id, origin, "
            "proposed_content, evidence_references, scope_dimensions, "
            "requested_trust_class, approval_requirement, lifecycle_state, "
            "content_kind, created_at, content_hash) VALUES "
            f"({q(ids[tenant_key])}, {q(ids[f'proposal_{suffix}'])}, '1.0.0', "
            f"{q(ids[f'scope_personal_{suffix}'])}, 'USER_PERSONAL_HAT', NULL, "
            f"{q(ids[user_key])}, {q(ids[f'space_{suffix}'])}, 'USER_ENTRY', "
            f"{json_sql({'preference': suffix})}, '[]'::JSONB, '[]'::JSONB, "
            f"'USER_ASSERTED_MEMORY', 'OWNER', 'PROPOSED', 'PREFERENCE', "
            f"{at}, {q(proposal_hash)})"
        )
        statements.append(
            "INSERT INTO memory_patch.memory_patch_approvals "
            "(tenant_id, approval_id, schema_version, proposal_id, "
            "proposal_content_hash, target_scope, owner_user_id, "
            "personal_memory_space_id, decision, approver_type, approver_id, "
            "reason_code, decided_at, approval_proof) VALUES "
            f"({q(ids[tenant_key])}, {q(ids[f'approval_{suffix}'])}, '1.0.0', "
            f"{q(ids[f'proposal_{suffix}'])}, {q(proposal_hash)}, "
            f"'USER_PERSONAL_HAT', {q(ids[user_key])}, {q(ids[f'space_{suffix}'])}, "
            f"'APPROVE', 'USER', {q(ids[user_key])}, 'SYNTHETIC_OWNER_APPROVAL', "
            f"{at}, {q(approval_proof)})"
        )
        statements.append(
            "INSERT INTO memory_patch.memory_patch_commits "
            "(tenant_id, commit_id, schema_version, proposal_id, "
            "proposal_content_hash, target_scope, approval_id, approval_proof, "
            "approval_decision, committed_patch_id, owner_user_id, "
            "personal_memory_space_id, actor_type, actor_id, storage_class, "
            "committed_at, commit_hash) VALUES "
            f"({q(ids[tenant_key])}, {q(ids[f'commit_{suffix}'])}, '1.0.0', "
            f"{q(ids[f'proposal_{suffix}'])}, {q(proposal_hash)}, "
            f"'USER_PERSONAL_HAT', {q(ids[f'approval_{suffix}'])}, "
            f"{q(approval_proof)}, 'APPROVE', {q('synthetic-patch-' + suffix)}, "
            f"{q(ids[user_key])}, {q(ids[f'space_{suffix}'])}, "
            f"'MIGRATION_SERVICE', 'synthetic-fixture-loader', "
            f"'CRDB_TRANSACTIONAL', {at}, {q(hash_for('commit-' + suffix))})"
        )
        statements.append(
            "INSERT INTO memory_patch.patch_transition_records "
            "(tenant_id, transition_id, proposal_id, proposal_content_hash, "
            "state_before, state_after, actor_type, actor_id, transitioned_at) "
            "VALUES "
            f"({q(ids[tenant_key])}, {q(ids[f'transition_{suffix}'])}, "
            f"{q(ids[f'proposal_{suffix}'])}, {q(proposal_hash)}, 'DETECTED', "
            f"'PROPOSED', 'MIGRATION_SERVICE', 'synthetic-fixture-loader', {at})"
        )
        statements.append(
            "INSERT INTO memory_patch.memory_items "
            "(tenant_id, memory_item_id, schema_version, hat_scope_id, "
            "target_scope, visibility, trust_class, content_kind, content, "
            "scope_dimensions, evidence_references, active, revoked, created_at) "
            "VALUES "
            f"({q(ids[tenant_key])}, {q(ids[f'memory_{suffix}'])}, '1.0.0', "
            f"{q(ids[f'scope_personal_{suffix}'])}, 'USER_PERSONAL_HAT', "
            f"'PERSONAL', 'USER_ASSERTED_MEMORY', 'PREFERENCE', "
            f"{json_sql({'value': 'private-' + suffix})}, '[]'::JSONB, "
            f"'[]'::JSONB, true, false, {at})"
        )
        statements.append(
            "INSERT INTO memory_patch.audit_events "
            "(tenant_id, event_id, schema_version, event_type, actor_type, "
            "actor_id, kernel_run_id, user_id, personal_memory_space_id, "
            "payload_hash, event_hash, metadata, occurred_at) VALUES "
            f"({q(ids[tenant_key])}, {q(ids[f'audit_{suffix}'])}, '1.0.0', "
            f"'SYNTHETIC_FIXTURE', 'MIGRATION_SERVICE', "
            f"'synthetic-fixture-loader', {q(ids[f'run_{suffix}'])}, "
            f"{q(ids[user_key])}, {q(ids[f'space_{suffix}'])}, "
            f"{q(hash_for('payload-' + suffix))}, {q(hash_for('event-' + suffix))}, "
            f"'{{}}'::JSONB, {at})"
        )
    return "BEGIN;\n" + ";\n".join(statements) + ";\nCOMMIT;"


def create_test_roles(
    root: migrations.SqlClient,
    roles: Mapping[str, str],
) -> None:
    statements = [
        "SET allow_role_memberships_to_change_during_transaction = true"
    ]
    for role in roles.values():
        statements.append(
            f"CREATE ROLE {role_identifier(role)} "
            "WITH LOGIN NOCREATEROLE NOCREATEDB NOBYPASSRLS"
        )
    for key in ("a1", "a2", "b1"):
        statements.append(
            "GRANT mp_app_runtime, mp_request_context_setter TO "
            f"{role_identifier(roles[key])}"
        )
    statements.append(
        f"GRANT mp_app_runtime TO {role_identifier(roles['runtime_only'])}"
    )
    statements.append(
        "GRANT mp_schema_owner, mp_app_runtime, mp_request_context_setter TO "
        f"{role_identifier(roles['owner_probe'])}"
    )
    root.execute("defaultdb", ";\n".join(statements), timeout=180)


def drop_disposable_roles(
    root: migrations.SqlClient,
    roles: Mapping[str, str],
) -> None:
    for role in roles.values():
        if not role.startswith(RUN_PREFIX):
            raise RlsValidationError("refusing to drop an unmarked test role")
    statements = [
        "SET allow_role_memberships_to_change_during_transaction = true"
    ]
    statements.extend(
        f"DROP ROLE IF EXISTS {role_identifier(role)}" for role in roles.values()
    )
    statements.extend(
        f"DROP ROLE IF EXISTS {role_identifier(role)}" for role in FIXED_ROLES
    )
    root.execute("defaultdb", ";\n".join(statements), timeout=180)


def security_catalog(
    root: migrations.SqlClient,
    database: str,
) -> dict[str, Any]:
    summary = migrations.assert_step5_security_catalog(
        root,
        database,
        include_step28_delta=True,
    )
    table_rows = migrations.parse_tsv(
        root.execute(
            database,
            "SELECT c.relname AS table_name, c.relrowsecurity, "
            "c.relforcerowsecurity, owner.rolname AS owner_role "
            "FROM pg_catalog.pg_class AS c "
            "JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace "
            "JOIN pg_catalog.pg_roles AS owner ON owner.oid = c.relowner "
            "WHERE n.nspname = 'memory_patch' AND c.relkind = 'r' "
            "ORDER BY c.relname",
        )
    )
    policy_rows = migrations.parse_tsv(
        root.execute(
            database,
            "SELECT tablename, policyname, cmd, roles, qual, with_check "
            "FROM pg_catalog.pg_policies "
            "WHERE schemaname = 'memory_patch' "
            "ORDER BY tablename, policyname",
        )
    )
    table_grants = migrations.parse_tsv(
        root.execute(
            database,
            "SELECT grantee, table_name, privilege_type "
            "FROM information_schema.table_privileges "
            "WHERE table_schema = 'memory_patch' "
            "AND grantee IN ('mp_app_runtime', 'mp_request_context_setter', "
            "'PUBLIC') "
            "ORDER BY grantee, table_name, privilege_type",
        )
    )
    routine_grants = migrations.parse_tsv(
        root.execute(
            database,
            "SELECT grantee, routine_name, privilege_type "
            "FROM information_schema.routine_privileges "
            "WHERE specific_schema = 'memory_patch' "
            "AND grantee IN ('mp_app_runtime', 'mp_request_context_setter', "
            "'PUBLIC') "
            "ORDER BY grantee, routine_name, privilege_type",
        )
    )
    role_rows = migrations.parse_tsv(
        root.execute(
            "defaultdb",
            "SELECT rolname, rolcanlogin, rolcreaterole, rolcreatedb, "
            "rolbypassrls, rolsuper "
            "FROM pg_catalog.pg_roles "
            "WHERE rolname IN ('mp_app_runtime', 'mp_request_context_setter', "
            "'mp_schema_owner', 'mp_security_owner') "
            "ORDER BY rolname",
        )
    )
    membership_rows = migrations.parse_tsv(
        root.execute(
            "defaultdb",
            "SELECT parent.rolname AS parent_role, member.rolname AS member_role "
            "FROM pg_catalog.pg_auth_members AS membership "
            "JOIN pg_catalog.pg_roles AS parent "
            "ON parent.oid = membership.roleid "
            "JOIN pg_catalog.pg_roles AS member "
            "ON member.oid = membership.member "
            "WHERE parent.rolname IN ('mp_app_runtime', "
            "'mp_request_context_setter', 'mp_schema_owner', "
            "'mp_security_owner') "
            "OR member.rolname IN ('mp_app_runtime', "
            "'mp_request_context_setter', 'mp_schema_owner', "
            "'mp_security_owner') "
            "ORDER BY parent.rolname, member.rolname",
        )
    )
    trigger_rows = migrations.parse_tsv(
        root.execute(
            database,
            "SELECT trigger.tgname AS trigger_name, target.relname AS table_name, "
            "procedure.proname AS function_name "
            "FROM pg_catalog.pg_trigger AS trigger "
            "JOIN pg_catalog.pg_class AS target "
            "ON target.oid = trigger.tgrelid "
            "JOIN pg_catalog.pg_namespace AS namespace "
            "ON namespace.oid = target.relnamespace "
            "JOIN pg_catalog.pg_proc AS procedure "
            "ON procedure.oid = trigger.tgfoid "
            "WHERE namespace.nspname = 'memory_patch' "
            "AND NOT trigger.tgisinternal "
            "ORDER BY trigger.tgname",
        )
    )
    digest_input = {
        "policies": policy_rows,
        "routine_grants": routine_grants,
        "table_grants": table_grants,
        "tables": table_rows,
        "triggers": trigger_rows,
    }
    return {
        "digest": hashlib.sha256(canonical_json_bytes(digest_input)).hexdigest(),
        "fixed_roles": role_rows,
        "membership_edges": membership_rows,
        "policies": policy_rows,
        "routine_grants": routine_grants,
        "summary": summary,
        "table_grants": table_grants,
        "tables": table_rows,
        "triggers": trigger_rows,
    }


def root_count(
    root: migrations.SqlClient,
    database: str,
    table: str,
    where: str,
) -> int:
    if not re.fullmatch(r"[a-z][a-z0-9_]*", table):
        raise RlsValidationError("unsafe table name in root fixture count")
    return int(
        migrations.one_value(
            root.execute(
                database,
                f"SELECT count(*) FROM memory_patch.{table} WHERE {where}",
            )
        )
    )


def run_positive_probes(
    recorder: ProbeRecorder,
    root: migrations.SqlClient,
    clients: Mapping[str, RoleSqlClient],
    database: str,
    ids: Mapping[str, str],
) -> None:
    q = migrations.sql_literal
    shared_a = context_scalar(
        clients["a1"],
        database,
        ids["tenant_a"],
        None,
        "TENANT_SHARED",
        "SELECT count(*) AS probe_value FROM memory_patch.memory_items "
        f"WHERE memory_item_id = {q(ids['shared_memory_a'])}",
    )
    recorder.check(
        "POS-001",
        "positive",
        shared_a == "1",
        f"Tenant A shared row count={shared_a}",
    )
    shared_b = context_scalar(
        clients["b1"],
        database,
        ids["tenant_b"],
        None,
        "TENANT_SHARED",
        "SELECT count(*) AS probe_value FROM memory_patch.memory_items "
        f"WHERE memory_item_id = {q(ids['shared_memory_b'])}",
    )
    recorder.check(
        "POS-002",
        "positive",
        shared_b == "1",
        f"Tenant B shared row count={shared_b}",
    )
    for probe_id, key, tenant_key, user_key in (
        ("POS-003", "a1", "tenant_a", "user_a1"),
        ("POS-004", "a2", "tenant_a", "user_a2"),
        ("POS-005", "b1", "tenant_b", "user_b1"),
    ):
        count = context_scalar(
            clients[key],
            database,
            ids[tenant_key],
            ids[user_key],
            "USER_PRIVATE",
            "SELECT count(*) AS probe_value FROM memory_patch.memory_items "
            f"WHERE memory_item_id = {q(ids[f'memory_{key}'])}",
        )
        recorder.check(
            probe_id,
            "positive",
            count == "1",
            f"{key} private row count={count}",
        )

    for probe_id, key, tenant_key, user_key in (
        ("POS-006", "a1", "tenant_a", "user_a1"),
        ("POS-007", "a2", "tenant_a", "user_a2"),
    ):
        item_id = f"{ids[f'memory_{key}']}_valid_insert"
        clients[key].execute(
            database,
            context_transaction(
                ids[tenant_key],
                ids[user_key],
                "USER_PRIVATE",
                "INSERT INTO memory_patch.memory_items "
                "(tenant_id, memory_item_id, schema_version, hat_scope_id, "
                "target_scope, visibility, trust_class, content_kind, content, "
                "scope_dimensions, evidence_references, active, revoked, "
                "created_at) VALUES "
                f"({q(ids[tenant_key])}, {q(item_id)}, '1.0.0', "
                f"{q(ids[f'scope_personal_{key}'])}, 'USER_PERSONAL_HAT', "
                f"'PERSONAL', 'USER_ASSERTED_MEMORY', 'PREFERENCE', "
                f"{json_sql({'value': 'valid-' + key})}, '[]'::JSONB, "
                "'[]'::JSONB, true, false, now())",
            ),
        )
        count = root_count(
            root,
            database,
            "memory_items",
            f"tenant_id = {q(ids[tenant_key])} AND memory_item_id = {q(item_id)}",
        )
        recorder.check(
            probe_id,
            "positive",
            count == 1,
            f"valid private INSERT stored rows={count}",
        )

    source_id = f"{ids['source_a']}_valid_child"
    snapshot_id = f"{ids['snapshot_a']}_valid_child"
    clients["a1"].execute(
        database,
        context_transaction(
            ids["tenant_a"],
            None,
            "TENANT_SHARED",
            "INSERT INTO memory_patch.knowledge_sources "
            "(tenant_id, source_id, hat_scope_id, source_kind, "
            "source_reference, provenance, created_at) VALUES "
            f"({q(ids['tenant_a'])}, {q(source_id)}, "
            f"{q(ids['scope_shared_a'])}, 'SYNTHETIC', "
            f"{q('synthetic://valid-child')}, '{{}}'::JSONB, now()); "
            "INSERT INTO memory_patch.source_snapshots "
            "(tenant_id, snapshot_id, source_id, hat_scope_id, content_sha256, "
            "byte_length, storage_class, immutable_object_reference, captured_at, "
            "provenance) VALUES "
            f"({q(ids['tenant_a'])}, {q(snapshot_id)}, {q(source_id)}, "
            f"{q(ids['scope_shared_a'])}, {q(hash_for(snapshot_id))}, 1, "
            "'CRDB_TRANSACTIONAL', 'synthetic://valid-child-snapshot', now(), "
            "'{}'::JSONB)",
        ),
    )
    child_count = root_count(
        root,
        database,
        "source_snapshots",
        f"snapshot_id = {q(snapshot_id)}",
    )
    recorder.check(
        "POS-008",
        "positive",
        child_count == 1,
        f"same-tenant source/snapshot child rows={child_count}",
    )

    path_count = context_scalar(
        clients["a1"],
        database,
        ids["tenant_a"],
        ids["user_a1"],
        "USER_PRIVATE",
        "SELECT count(*) AS probe_value "
        "FROM memory_patch.kernel_runs AS run "
        "JOIN memory_patch.drafts AS draft "
        "ON draft.tenant_id = run.tenant_id "
        "AND draft.kernel_run_id = run.kernel_run_id "
        "JOIN memory_patch.claims AS claim "
        "ON claim.tenant_id = run.tenant_id "
        "AND claim.kernel_run_id = run.kernel_run_id "
        "JOIN memory_patch.correction_packets AS packet "
        "ON packet.tenant_id = run.tenant_id "
        "AND packet.kernel_run_id = run.kernel_run_id "
        f"WHERE run.kernel_run_id = {q(ids['run_a1'])}",
    )
    recorder.check(
        "POS-009",
        "positive",
        path_count == "1",
        f"valid Step 4 kernel path count={path_count}",
    )

    clients["a1"].execute(
        database,
        context_transaction(
            ids["tenant_a"],
            ids["user_a1"],
            "USER_PRIVATE",
            "UPDATE memory_patch.personal_memory_spaces "
            "SET state = 'SUSPENDED', updated_at = now() "
            f"WHERE personal_memory_space_id = {q(ids['space_a1'])}",
        ),
    )
    state = migrations.one_value(
        root.execute(
            database,
            "SELECT state FROM memory_patch.personal_memory_spaces "
            f"WHERE tenant_id = {q(ids['tenant_a'])} "
            f"AND personal_memory_space_id = {q(ids['space_a1'])}",
        )
    )
    recorder.check(
        "POS-010",
        "positive",
        state == "SUSPENDED",
        f"permitted lifecycle UPDATE state={state}",
    )

    clients["a1"].execute(
        database,
        context_transaction(
            ids["tenant_a"],
            ids["user_a1"],
            "USER_PRIVATE",
            "UPDATE memory_patch.memory_items "
            "SET active = false, revoked = true "
            f"WHERE memory_item_id = {q(ids['memory_a1'])}",
        ),
    )
    revocation = migrations.one_value(
        root.execute(
            database,
            "SELECT concat(active::STRING, ':', revoked::STRING) "
            "FROM memory_patch.memory_items "
            f"WHERE tenant_id = {q(ids['tenant_a'])} "
            f"AND memory_item_id = {q(ids['memory_a1'])}",
        )
    )
    recorder.check(
        "POS-011",
        "positive",
        revocation == "false:true",
        f"permitted revocation UPDATE={revocation}",
    )

    manifest_count = int(
        migrations.one_value(
            clients["runtime_only"].execute(
                database,
                "SELECT count(*) FROM memory_patch.hat_manifests",
            )
        )
    )
    recorder.check(
        "POS-012",
        "positive",
        manifest_count == 1,
        f"global read-only HAT manifest count={manifest_count}",
    )


def run_cross_tenant_probes(
    recorder: ProbeRecorder,
    root: migrations.SqlClient,
    clients: Mapping[str, RoleSqlClient],
    database: str,
    ids: Mapping[str, str],
) -> None:
    q = migrations.sql_literal
    broad = context_scalar(
        clients["a1"],
        database,
        ids["tenant_a"],
        None,
        "TENANT_SHARED",
        "SELECT count(*) AS probe_value FROM memory_patch.memory_items "
        f"WHERE tenant_id = {q(ids['tenant_b'])}",
    )
    recorder.check(
        "TENANT-001",
        "cross_tenant",
        broad == "0",
        f"Tenant A broad Tenant B SELECT rows={broad}",
    )
    exact = context_scalar(
        clients["a1"],
        database,
        ids["tenant_a"],
        None,
        "TENANT_SHARED",
        "SELECT count(*) AS probe_value FROM memory_patch.memory_items "
        f"WHERE memory_item_id = {q(ids['shared_memory_b'])}",
    )
    recorder.check(
        "TENANT-002",
        "cross_tenant",
        exact == "0",
        f"Tenant A exact Tenant B SELECT rows={exact}",
    )

    state = expected_error_in_context(
        clients["a1"],
        database,
        ids["tenant_a"],
        ids["user_a1"],
        "USER_PRIVATE",
        "INSERT INTO memory_patch.memory_items "
        "(tenant_id, memory_item_id, schema_version, hat_scope_id, target_scope, "
        "visibility, trust_class, content_kind, content, scope_dimensions, "
        "evidence_references, active, revoked, created_at) VALUES "
        f"({q(ids['tenant_b'])}, {q(ids['memory_b1'] + '_forbidden')}, '1.0.0', "
        f"{q(ids['scope_personal_b1'])}, 'USER_PERSONAL_HAT', 'PERSONAL', "
        "'USER_ASSERTED_MEMORY', 'PREFERENCE', '{}', '[]'::JSONB, "
        "'[]'::JSONB, true, false, now())",
        expected={"42501"},
    )
    recorder.check(
        "TENANT-003",
        "cross_tenant",
        state == "42501",
        "Tenant A cross-tenant INSERT denied",
        sqlstate=state,
    )

    state = expected_error_in_context(
        clients["a1"],
        database,
        ids["tenant_a"],
        None,
        "TENANT_SHARED",
        "INSERT INTO memory_patch.knowledge_sources "
        "(tenant_id, source_id, hat_scope_id, source_kind, source_reference, "
        "provenance, created_at) VALUES "
        f"({q(ids['tenant_a'])}, {q(ids['source_a'] + '_cross_scope')}, "
        f"{q(ids['scope_shared_b'])}, 'SYNTHETIC', "
        "'synthetic://cross-scope', '{}'::JSONB, now())",
        expected={"42501"},
    )
    recorder.check(
        "TENANT-004",
        "cross_tenant",
        state == "42501",
        "Tenant A child-to-Tenant B scope denied",
        sqlstate=state,
    )

    state = expected_error_in_context(
        clients["a1"],
        database,
        ids["tenant_a"],
        None,
        "TENANT_SHARED",
        "INSERT INTO memory_patch.source_snapshots "
        "(tenant_id, snapshot_id, source_id, hat_scope_id, content_sha256, "
        "byte_length, storage_class, immutable_object_reference, captured_at, "
        "provenance) VALUES "
        f"({q(ids['tenant_a'])}, {q(ids['snapshot_a'] + '_cross_parent')}, "
        f"{q(ids['source_b'])}, {q(ids['scope_shared_a'])}, "
        f"{q(hash_for('cross-parent-snapshot'))}, 1, 'CRDB_TRANSACTIONAL', "
        "'synthetic://cross-parent', now(), '{}'::JSONB)",
        expected={"23503"},
    )
    recorder.check(
        "TENANT-005",
        "cross_tenant",
        state == "23503",
        "cross-tenant source/snapshot FK denied",
        sqlstate=state,
    )

    state = expected_error_in_context(
        clients["a1"],
        database,
        ids["tenant_a"],
        None,
        "TENANT_SHARED",
        "INSERT INTO memory_patch.knowledge_versions "
        "(tenant_id, knowledge_version_id, source_id, snapshot_id, "
        "hat_scope_id, version_ordinal, normalized_content_sha256, "
        "normalization_profile, is_current, created_at, provenance) VALUES "
        f"({q(ids['tenant_a'])}, {q(ids['version_a'] + '_cross_snapshot')}, "
        f"{q(ids['source_a'])}, {q(ids['snapshot_b'])}, "
        f"{q(ids['scope_shared_a'])}, 2, "
        f"{q(hash_for('cross-tenant-version'))}, 'synthetic-v1', false, "
        "now(), '{}'::JSONB)",
        expected={"23503"},
    )
    recorder.check(
        "TENANT-005A",
        "cross_tenant",
        state == "23503",
        "cross-tenant snapshot/version lineage FK denied",
        sqlstate=state,
    )

    state = expected_error_in_context(
        clients["a1"],
        database,
        ids["tenant_a"],
        None,
        "TENANT_SHARED",
        "INSERT INTO memory_patch.knowledge_chunks "
        "(tenant_id, chunk_id, knowledge_version_id, source_id, hat_scope_id, "
        "chunk_ordinal, content_text, content_sha256, start_offset, end_offset, "
        "language_tag, metadata, created_at) VALUES "
        f"({q(ids['tenant_a'])}, {q(ids['chunk_a'] + '_cross_version')}, "
        f"{q(ids['version_b'])}, {q(ids['source_a'])}, "
        f"{q(ids['scope_shared_a'])}, 9, 'cross tenant chunk', "
        f"{q(hash_for('cross-tenant-chunk'))}, 0, 18, 'en', "
        "'{}'::JSONB, now())",
        expected={"23503"},
    )
    recorder.check(
        "TENANT-005B",
        "cross_tenant",
        state == "23503",
        "cross-tenant version/chunk lineage FK denied",
        sqlstate=state,
    )

    state = expected_error_in_context(
        clients["a1"],
        database,
        ids["tenant_a"],
        ids["user_a1"],
        "USER_PRIVATE",
        "UPDATE memory_patch.memory_items "
        f"SET tenant_id = {q(ids['tenant_b'])} "
        f"WHERE memory_item_id = {q(ids['memory_a1'])}",
        expected={"42501"},
    )
    recorder.check(
        "TENANT-006",
        "cross_tenant",
        state == "42501",
        "tenant identity UPDATE denied by immutable guard",
        sqlstate=state,
    )

    tenant_b_before = migrations.one_value(
        root.execute(
            database,
            "SELECT concat(active::STRING, ':', revoked::STRING) "
            "FROM memory_patch.memory_items "
            f"WHERE tenant_id = {q(ids['tenant_b'])} "
            f"AND memory_item_id = {q(ids['shared_memory_b'])}",
        )
    )
    clients["a1"].execute(
        database,
        context_transaction(
            ids["tenant_a"],
            None,
            "TENANT_SHARED",
            "UPDATE memory_patch.memory_items SET active = true "
            f"WHERE tenant_id = {q(ids['tenant_b'])} "
            f"AND memory_item_id = {q(ids['shared_memory_b'])}",
        ),
    )
    tenant_b_after = migrations.one_value(
        root.execute(
            database,
            "SELECT concat(active::STRING, ':', revoked::STRING) "
            "FROM memory_patch.memory_items "
            f"WHERE tenant_id = {q(ids['tenant_b'])} "
            f"AND memory_item_id = {q(ids['shared_memory_b'])}",
        )
    )
    recorder.check(
        "TENANT-006A",
        "cross_tenant",
        tenant_b_before == "false:false" and tenant_b_after == tenant_b_before,
        "Tenant A UPDATE of Tenant B row retained state="
        f"{tenant_b_before}:{tenant_b_after}",
    )

    before = root_count(
        root,
        database,
        "personal_memory_model_bindings",
        f"tenant_id = {q(ids['tenant_b'])} "
        f"AND model_binding_id = {q(ids['binding_b1'])}",
    )
    clients["a1"].execute(
        database,
        context_transaction(
            ids["tenant_a"],
            ids["user_a1"],
            "USER_PRIVATE",
            "DELETE FROM memory_patch.personal_memory_model_bindings "
            f"WHERE tenant_id = {q(ids['tenant_b'])} "
            f"AND model_binding_id = {q(ids['binding_b1'])}",
        ),
    )
    after = root_count(
        root,
        database,
        "personal_memory_model_bindings",
        f"tenant_id = {q(ids['tenant_b'])} "
        f"AND model_binding_id = {q(ids['binding_b1'])}",
    )
    recorder.check(
        "TENANT-007",
        "cross_tenant",
        before == 1 and after == 1,
        f"cross-tenant DELETE retained row before/after={before}/{after}",
    )

    join_count = context_scalar(
        clients["a1"],
        database,
        ids["tenant_a"],
        ids["user_a1"],
        "USER_PRIVATE",
        "SELECT count(*) AS probe_value "
        "FROM memory_patch.memory_items AS item "
        "JOIN memory_patch.hat_scopes AS scope "
        "ON scope.tenant_id = item.tenant_id "
        "AND scope.hat_scope_id = item.hat_scope_id "
        f"WHERE item.memory_item_id = {q(ids['memory_b1'])}",
    )
    recorder.check(
        "TENANT-008",
        "cross_tenant",
        join_count == "0",
        f"cross-tenant JOIN rows={join_count}",
    )
    subquery_count = context_scalar(
        clients["a1"],
        database,
        ids["tenant_a"],
        ids["user_a1"],
        "USER_PRIVATE",
        "SELECT count(*) AS probe_value FROM memory_patch.memory_items "
        "WHERE hat_scope_id IN "
        "(SELECT hat_scope_id FROM memory_patch.hat_scopes "
        f"WHERE tenant_id = {q(ids['tenant_b'])})",
    )
    recorder.check(
        "TENANT-009",
        "cross_tenant",
        subquery_count == "0",
        f"cross-tenant subquery rows={subquery_count}",
    )

    state = expected_error_in_context(
        clients["a1"],
        database,
        ids["tenant_a"],
        ids["user_a1"],
        "USER_PRIVATE",
        "INSERT INTO memory_patch.action_policy_decisions "
        "(tenant_id, action_policy_decision_id, kernel_run_id, action_policy, "
        "reason_codes, decided_at) VALUES "
        f"({q(ids['tenant_a'])}, {q(ids['action_a1'] + '_cross_run')}, "
        f"{q(ids['run_b1'])}, 'ALLOW', '[\"synthetic\"]'::JSONB, now())",
        expected={"42501"},
    )
    recorder.check(
        "TENANT-010",
        "cross_tenant",
        state == "42501",
        "cross-tenant run attachment denied",
        sqlstate=state,
    )

    state = expected_error_in_context(
        clients["a1"],
        database,
        ids["tenant_a"],
        ids["user_a1"],
        "USER_PRIVATE",
        "INSERT INTO memory_patch.evidence_bundles "
        "(tenant_id, evidence_bundle_id, kernel_run_id, hat_scope_id, hat_id, "
        "evidence_status, retrieval_policy_version, bundle_hash, created_at) "
        "VALUES "
        f"({q(ids['tenant_a'])}, {q(ids['bundle_a1'] + '_cross_run')}, "
        f"{q(ids['run_b1'])}, {q(ids['scope_shared_a'])}, {q(ids['hat'])}, "
        f"'SUFFICIENT', 'synthetic-v1', {q(hash_for('cross-run-bundle'))}, "
        "now())",
        expected={"42501"},
    )
    recorder.check(
        "TENANT-010A",
        "cross_tenant",
        state == "42501",
        "cross-tenant evidence bundle/run attachment denied",
        sqlstate=state,
    )

    opposite = context_scalar(
        clients["b1"],
        database,
        ids["tenant_b"],
        ids["user_b1"],
        "USER_PRIVATE",
        "SELECT count(*) AS probe_value FROM memory_patch.memory_items "
        f"WHERE tenant_id = {q(ids['tenant_a'])}",
    )
    recorder.check(
        "TENANT-011",
        "cross_tenant",
        opposite == "0",
        f"Tenant B representative Tenant A SELECT rows={opposite}",
    )
    state = expected_error_in_context(
        clients["b1"],
        database,
        ids["tenant_b"],
        ids["user_b1"],
        "USER_PRIVATE",
        "INSERT INTO memory_patch.memory_items "
        "(tenant_id, memory_item_id, schema_version, hat_scope_id, target_scope, "
        "visibility, trust_class, content_kind, content, scope_dimensions, "
        "evidence_references, active, revoked, created_at) VALUES "
        f"({q(ids['tenant_a'])}, {q(ids['memory_a1'] + '_from_b')}, '1.0.0', "
        f"{q(ids['scope_personal_a1'])}, 'USER_PERSONAL_HAT', 'PERSONAL', "
        "'USER_ASSERTED_MEMORY', 'PREFERENCE', '{}', '[]'::JSONB, "
        "'[]'::JSONB, true, false, now())",
        expected={"42501"},
    )
    recorder.check(
        "TENANT-012",
        "cross_tenant",
        state == "42501",
        "Tenant B representative Tenant A INSERT denied",
        sqlstate=state,
    )


def run_cross_user_probes(
    recorder: ProbeRecorder,
    root: migrations.SqlClient,
    clients: Mapping[str, RoleSqlClient],
    database: str,
    ids: Mapping[str, str],
) -> None:
    q = migrations.sql_literal
    for probe_id, table, identifier_column, identifier in (
        (
            "USER-001",
            "personal_memory_spaces",
            "personal_memory_space_id",
            ids["space_a2"],
        ),
        (
            "USER-002",
            "personal_memory_model_bindings",
            "model_binding_id",
            ids["binding_a2"],
        ),
        ("USER-003", "memory_items", "memory_item_id", ids["memory_a2"]),
        (
            "USER-004",
            "memory_patch_proposals",
            "proposal_id",
            ids["proposal_a2"],
        ),
        ("USER-005", "kernel_runs", "kernel_run_id", ids["run_a2"]),
    ):
        count = context_scalar(
            clients["a1"],
            database,
            ids["tenant_a"],
            ids["user_a1"],
            "USER_PRIVATE",
            f"SELECT count(*) AS probe_value FROM memory_patch.{table} "
            f"WHERE {identifier_column} = {q(identifier)}",
        )
        recorder.check(
            probe_id,
            "cross_user",
            count == "0",
            f"A1 known A2 {table} rows={count}",
        )

    state = expected_error_in_context(
        clients["a1"],
        database,
        ids["tenant_a"],
        ids["user_a1"],
        "USER_PRIVATE",
        "INSERT INTO memory_patch.memory_items "
        "(tenant_id, memory_item_id, schema_version, hat_scope_id, target_scope, "
        "visibility, trust_class, content_kind, content, scope_dimensions, "
        "evidence_references, active, revoked, created_at) VALUES "
        f"({q(ids['tenant_a'])}, {q(ids['memory_a2'] + '_from_a1')}, '1.0.0', "
        f"{q(ids['scope_personal_a2'])}, 'USER_PERSONAL_HAT', 'PERSONAL', "
        "'USER_ASSERTED_MEMORY', 'PREFERENCE', '{}', '[]'::JSONB, "
        "'[]'::JSONB, true, false, now())",
        expected={"42501"},
    )
    recorder.check(
        "USER-006",
        "cross_user",
        state == "42501",
        "A1 INSERT into A2 Personal Memory denied",
        sqlstate=state,
    )

    before_content = migrations.one_value(
        root.execute(
            database,
            "SELECT content::STRING FROM memory_patch.memory_items "
            f"WHERE tenant_id = {q(ids['tenant_a'])} "
            f"AND memory_item_id = {q(ids['memory_a2'])}",
        )
    )
    clients["a1"].execute(
        database,
        context_transaction(
            ids["tenant_a"],
            ids["user_a1"],
            "USER_PRIVATE",
            "UPDATE memory_patch.memory_items "
            "SET active = false "
            f"WHERE memory_item_id = {q(ids['memory_a2'])}",
        ),
    )
    after_content = migrations.one_value(
        root.execute(
            database,
            "SELECT content::STRING FROM memory_patch.memory_items "
            f"WHERE tenant_id = {q(ids['tenant_a'])} "
            f"AND memory_item_id = {q(ids['memory_a2'])}",
        )
    )
    a2_active = migrations.one_value(
        root.execute(
            database,
            "SELECT active::STRING FROM memory_patch.memory_items "
            f"WHERE tenant_id = {q(ids['tenant_a'])} "
            f"AND memory_item_id = {q(ids['memory_a2'])}",
        )
    )
    recorder.check(
        "USER-007",
        "cross_user",
        before_content == after_content and a2_active == "true",
        f"A1 UPDATE A2 retained active={a2_active}",
    )

    binding_before = root_count(
        root,
        database,
        "personal_memory_model_bindings",
        f"tenant_id = {q(ids['tenant_a'])} "
        f"AND model_binding_id = {q(ids['binding_a2'])}",
    )
    clients["a1"].execute(
        database,
        context_transaction(
            ids["tenant_a"],
            ids["user_a1"],
            "USER_PRIVATE",
            "DELETE FROM memory_patch.personal_memory_model_bindings "
            f"WHERE model_binding_id = {q(ids['binding_a2'])}",
        ),
    )
    binding_after = root_count(
        root,
        database,
        "personal_memory_model_bindings",
        f"tenant_id = {q(ids['tenant_a'])} "
        f"AND model_binding_id = {q(ids['binding_a2'])}",
    )
    recorder.check(
        "USER-008",
        "cross_user",
        binding_before == 1 and binding_after == 1,
        f"A1 DELETE A2 binding retained={binding_after}",
    )

    state = expected_error_in_context(
        clients["a1"],
        database,
        ids["tenant_a"],
        ids["user_a1"],
        "USER_PRIVATE",
        "UPDATE memory_patch.memory_items "
        f"SET hat_scope_id = {q(ids['scope_personal_a2'])} "
        f"WHERE memory_item_id = {q(ids['memory_a1'])}",
        expected={"42501"},
    )
    recorder.check(
        "USER-009",
        "cross_user",
        state == "42501",
        "A1 private item rebind to A2 denied",
        sqlstate=state,
    )

    state = expected_error_in_context(
        clients["a1"],
        database,
        ids["tenant_a"],
        ids["user_a1"],
        "USER_PRIVATE",
        "UPDATE memory_patch.personal_memory_spaces "
        f"SET user_id = {q(ids['user_a2'])} "
        f"WHERE personal_memory_space_id = {q(ids['space_a1'])}",
        expected={"42501"},
    )
    recorder.check(
        "USER-010",
        "cross_user",
        state == "42501",
        "owner_user_id rebind denied by identity guard",
        sqlstate=state,
    )

    state = expected_error_in_context(
        clients["a1"],
        database,
        ids["tenant_a"],
        ids["user_a1"],
        "USER_PRIVATE",
        "INSERT INTO memory_patch.knowledge_sources "
        "(tenant_id, source_id, hat_scope_id, source_kind, source_reference, "
        "provenance, created_at) VALUES "
        f"({q(ids['tenant_a'])}, {q(ids['source_a'] + '_a2_private_child')}, "
        f"{q(ids['scope_personal_a2'])}, 'SYNTHETIC', "
        "'synthetic://a2-private-child', '{}'::JSONB, now())",
        expected={"42501"},
    )
    recorder.check(
        "USER-011",
        "cross_user",
        state == "42501",
        "A1 child attachment to A2 private parent denied",
        sqlstate=state,
    )

    tenant_only = context_scalar(
        clients["a1"],
        database,
        ids["tenant_a"],
        None,
        "TENANT_SHARED",
        "SELECT count(*) AS probe_value FROM memory_patch.memory_items "
        f"WHERE memory_item_id IN ({q(ids['memory_a1'])}, {q(ids['memory_a2'])})",
    )
    recorder.check(
        "USER-012",
        "cross_user",
        tenant_only == "0",
        f"tenant-only context private rows={tenant_only}",
    )

    state = expected_error_in_context(
        clients["a1"],
        database,
        ids["tenant_a"],
        ids["user_a1"],
        "USER_PRIVATE",
        "UPDATE memory_patch.memory_items "
        f"SET hat_scope_id = {q(ids['scope_shared_a'])}, "
        "target_scope = 'SHARED_KNOWLEDGE_HAT', visibility = 'SHARED' "
        f"WHERE memory_item_id = {q(ids['memory_a1'])}",
        expected={"42501"},
    )
    recorder.check(
        "USER-013",
        "cross_user",
        state == "42501",
        "private-to-shared conversion denied",
        sqlstate=state,
    )

    state = expected_error_in_context(
        clients["a1"],
        database,
        ids["tenant_a"],
        ids["user_a1"],
        "USER_PRIVATE",
        "UPDATE memory_patch.memory_items "
        f"SET hat_scope_id = {q(ids['scope_personal_a1'])}, "
        "target_scope = 'USER_PERSONAL_HAT', visibility = 'PERSONAL' "
        f"WHERE memory_item_id = {q(ids['shared_memory_a'])}",
        expected={"42501"},
    )
    recorder.check(
        "USER-014",
        "cross_user",
        state == "42501",
        "shared-to-private conversion denied",
        sqlstate=state,
    )

    traversal = context_scalar(
        clients["a1"],
        database,
        ids["tenant_a"],
        ids["user_a1"],
        "USER_PRIVATE",
        "SELECT count(*) AS probe_value "
        "FROM memory_patch.kernel_runs AS run "
        "JOIN memory_patch.hat_scopes AS scope "
        "ON scope.tenant_id = run.tenant_id "
        "AND scope.owner_user_id = run.user_id "
        "JOIN memory_patch.memory_items AS item "
        "ON item.tenant_id = scope.tenant_id "
        "AND item.hat_scope_id = scope.hat_scope_id "
        f"WHERE run.kernel_run_id = {q(ids['run_a2'])} "
        f"OR item.memory_item_id = {q(ids['memory_a2'])}",
    )
    recorder.check(
        "USER-015",
        "cross_user",
        traversal == "0",
        f"routing/kernel relationship traversal into A2 rows={traversal}",
    )

    opposite_a2 = context_scalar(
        clients["a2"],
        database,
        ids["tenant_a"],
        ids["user_a2"],
        "USER_PRIVATE",
        "SELECT count(*) AS probe_value FROM memory_patch.memory_items "
        f"WHERE memory_item_id = {q(ids['memory_a1'])}",
    )
    recorder.check(
        "USER-016",
        "cross_user",
        opposite_a2 == "0",
        f"A2 representative A1 read rows={opposite_a2}",
    )
    opposite_b1 = context_scalar(
        clients["b1"],
        database,
        ids["tenant_b"],
        ids["user_b1"],
        "USER_PRIVATE",
        "SELECT count(*) AS probe_value FROM memory_patch.memory_items "
        f"WHERE tenant_id = {q(ids['tenant_a'])}",
    )
    recorder.check(
        "USER-017",
        "cross_user",
        opposite_b1 == "0",
        f"B1 representative Tenant A private rows={opposite_b1}",
    )


def run_context_probes(
    recorder: ProbeRecorder,
    clients: Mapping[str, RoleSqlClient],
    database: str,
    ids: Mapping[str, str],
) -> None:
    q = migrations.sql_literal
    unset = migrations.one_value(
        clients["a1"].execute(
            database,
            "SELECT count(*) FROM memory_patch.memory_items",
        )
    )
    recorder.check(
        "CTX-001",
        "session_context",
        unset == "0",
        f"unset context rows={unset}",
    )
    state = clients["a1"].expect_error(
        database,
        "BEGIN; SELECT memory_patch.set_request_context("
        "'', NULL, 'TENANT_SHARED'); COMMIT;",
        expected={"22023"},
    )
    recorder.check(
        "CTX-002",
        "session_context",
        state == "22023",
        "empty tenant rejected",
        sqlstate=state,
    )
    state = clients["a1"].expect_error(
        database,
        "BEGIN; SELECT memory_patch.set_request_context("
        "'   ', NULL, 'TENANT_SHARED'); COMMIT;",
        expected={"22023"},
    )
    recorder.check(
        "CTX-003",
        "session_context",
        state == "22023",
        "whitespace tenant rejected",
        sqlstate=state,
    )
    state = clients["a1"].expect_error(
        database,
        "BEGIN; SELECT memory_patch.set_request_context("
        f"{q(ids['tenant_a'] + '_unknown')}, NULL, 'TENANT_SHARED'); COMMIT;",
        expected={"23503"},
    )
    recorder.check(
        "CTX-004",
        "session_context",
        state == "23503",
        "unknown tenant rejected by FK",
        sqlstate=state,
    )
    tenant_without_user = context_scalar(
        clients["a1"],
        database,
        ids["tenant_a"],
        None,
        "TENANT_SHARED",
        "SELECT count(*) AS probe_value FROM memory_patch.memory_items "
        f"WHERE memory_item_id = {q(ids['memory_a1'])}",
    )
    recorder.check(
        "CTX-005",
        "session_context",
        tenant_without_user == "0",
        f"tenant-only private rows={tenant_without_user}",
    )
    state = clients["a1"].expect_error(
        database,
        "BEGIN; SELECT memory_patch.set_request_context("
        f"NULL, {q(ids['user_a1'])}, 'USER_PRIVATE'); COMMIT;",
        expected={"22023"},
    )
    recorder.check(
        "CTX-006",
        "session_context",
        state == "22023",
        "user without tenant rejected",
        sqlstate=state,
    )
    state = clients["a1"].expect_error(
        database,
        "BEGIN; SELECT memory_patch.set_request_context("
        f"{q(ids['tenant_a'])}, {q(ids['user_b1'])}, 'USER_PRIVATE'); COMMIT;",
        expected={"23503"},
    )
    recorder.check(
        "CTX-007",
        "session_context",
        state == "23503",
        "user from another tenant rejected",
        sqlstate=state,
    )
    state = clients["a1"].expect_error(
        database,
        "BEGIN; SELECT memory_patch.set_request_context("
        f"{q(ids['tenant_a'])}, NULL, 'USER_PRIVATE'); COMMIT;",
        expected={"22023"},
    )
    recorder.check(
        "CTX-008",
        "session_context",
        state == "22023",
        "private context without user rejected",
        sqlstate=state,
    )
    state = clients["a1"].expect_error(
        database,
        "BEGIN; SELECT memory_patch.set_request_context("
        f"{q(ids['tenant_a'])}, {q(ids['user_a1'] + '_unknown')}, "
        "'USER_PRIVATE'); COMMIT;",
        expected={"23503"},
    )
    recorder.check(
        "CTX-009",
        "session_context",
        state == "23503",
        "unknown user rejected by FK",
        sqlstate=state,
    )

    changed_output = clients["a1"].execute(
        database,
        "BEGIN; "
        + context_setter_sql(ids["tenant_a"], ids["user_a1"], "USER_PRIVATE")
        + "; "
        + context_setter_sql(ids["tenant_a"], ids["user_a2"], "USER_PRIVATE")
        + "; SELECT concat("
        "(SELECT count(*) FROM memory_patch.memory_items "
        f"WHERE memory_item_id = {q(ids['memory_a1'])}), ':', "
        "(SELECT count(*) FROM memory_patch.memory_items "
        f"WHERE memory_item_id = {q(ids['memory_a2'])})) AS probe_value; COMMIT;",
    )
    changed = extract_named_scalar(changed_output)
    recorder.check(
        "CTX-010",
        "session_context",
        changed == "0:1",
        f"explicit context replacement visibility={changed}",
    )

    cleared_output = clients["a1"].execute(
        database,
        "BEGIN; "
        + context_setter_sql(ids["tenant_a"], ids["user_a1"], "USER_PRIVATE")
        + "; SELECT memory_patch.clear_request_context(); "
        "SELECT count(*) AS probe_value FROM memory_patch.memory_items; COMMIT;",
    )
    cleared = extract_named_scalar(cleared_output)
    recorder.check(
        "CTX-011",
        "session_context",
        cleared == "0",
        f"explicit clear rows={cleared}",
    )

    commit_output = clients["a1"].execute(
        database,
        "BEGIN; "
        + context_setter_sql(ids["tenant_a"], ids["user_a1"], "USER_PRIVATE")
        + "; SELECT count(*) AS before_commit FROM memory_patch.memory_items; "
        "COMMIT; SELECT count(*) AS probe_value FROM memory_patch.memory_items;",
    )
    after_commit = extract_named_scalar(commit_output)
    recorder.check(
        "CTX-012",
        "session_context",
        after_commit == "0",
        f"same connection after COMMIT rows={after_commit}",
    )

    rollback_output = clients["a1"].execute(
        database,
        "BEGIN; "
        + context_setter_sql(ids["tenant_a"], ids["user_a1"], "USER_PRIVATE")
        + "; ROLLBACK; "
        "SELECT count(*) AS probe_value FROM memory_patch.memory_items;",
    )
    after_rollback = extract_named_scalar(rollback_output)
    recorder.check(
        "CTX-013",
        "session_context",
        after_rollback == "0",
        f"same connection after ROLLBACK rows={after_rollback}",
    )

    failed_script = (
        "BEGIN;\n"
        + context_setter_sql(ids["tenant_a"], ids["user_a1"], "USER_PRIVATE")
        + ";\nUPDATE memory_patch.memory_items "
        f"SET tenant_id = {q(ids['tenant_b'])} "
        f"WHERE memory_item_id = {q(ids['memory_a1'])};\n"
        "ROLLBACK;\nSELECT count(*) AS probe_value "
        "FROM memory_patch.memory_items;"
    )
    failed_result = clients["a1"].execute_stdin_continuing(
        database,
        failed_script,
    )
    failed_state = migrations.extract_sqlstate(failed_result.stderr)
    after_failure = extract_named_scalar(failed_result.stdout)
    recorder.check(
        "CTX-014",
        "session_context",
        failed_state == "42501" and after_failure == "0",
        f"failed statement SQLSTATE={failed_state}, post-rollback rows={after_failure}",
        sqlstate=failed_state,
    )

    second_connection = migrations.one_value(
        clients["a1"].execute(
            database,
            "SELECT count(*) FROM memory_patch.memory_items",
        )
    )
    recorder.check(
        "CTX-015",
        "session_context",
        second_connection == "0",
        f"independent CLI connection rows={second_connection}",
    )

    state = clients["runtime_only"].expect_error(
        database,
        "INSERT INTO memory_patch.request_contexts "
        "(database_principal, backend_pid, transaction_started_at, tenant_id, "
        "user_id, access_mode, context_set_at) VALUES "
        f"(session_user, pg_backend_pid(), transaction_timestamp(), "
        f"{q(ids['tenant_a'])}, {q(ids['user_a1'])}, 'USER_PRIVATE', now())",
        expected={"42501"},
    )
    recorder.check(
        "CTX-016",
        "session_context",
        state == "42501",
        "direct context-table spoof denied",
        sqlstate=state,
    )
    state = clients["runtime_only"].expect_error(
        database,
        "BEGIN; "
        + context_setter_sql(ids["tenant_a"], ids["user_a1"], "USER_PRIVATE")
        + "; COMMIT;",
        expected={"42501"},
    )
    recorder.check(
        "CTX-017",
        "session_context",
        state == "42501",
        "runtime-only caller cannot invoke trusted setter",
        sqlstate=state,
    )
    state = clients["runtime_only"].expect_error(
        database,
        "SET ROLE mp_request_context_setter",
        expected={"42501"},
    )
    recorder.check(
        "CTX-018",
        "session_context",
        state == "42501",
        "runtime-only caller cannot SET ROLE to setter",
        sqlstate=state,
    )
    spoof_output = clients["runtime_only"].execute(
        database,
        "SET memory_patch.tenant_id = 'spoofed'; "
        "SELECT count(*) AS probe_value FROM memory_patch.memory_items;",
    )
    spoof_count = extract_named_scalar(spoof_output)
    recorder.check(
        "CTX-019",
        "session_context",
        spoof_count == "0",
        "caller-controlled custom session variable is ignored by every policy; "
        f"visible rows={spoof_count}",
    )
    null_match = migrations.one_value(
        clients["runtime_only"].execute(
            database,
            "SELECT memory_patch.tenant_context_matches(NULL)",
        )
    )
    recorder.check(
        "CTX-020",
        "session_context",
        null_match in {"f", "false"},
        f"NULL context match result={null_match}",
    )


def run_force_and_escalation_probes(
    recorder: ProbeRecorder,
    root: migrations.SqlClient,
    clients: Mapping[str, RoleSqlClient],
    database: str,
    ids: Mapping[str, str],
    roles: Mapping[str, str],
) -> dict[str, Any]:
    q = migrations.sql_literal
    owner_output = clients["owner_probe"].execute(
        database,
        "SET ROLE mp_schema_owner; "
        "SELECT count(*) AS probe_value FROM memory_patch.memory_items; "
        "RESET ROLE;",
    )
    owner_count = extract_named_scalar(owner_output)
    root_rows = int(
        migrations.one_value(
            root.execute(
                database,
                "SELECT count(*) FROM memory_patch.memory_items",
            )
        )
    )
    recorder.check(
        "FORCE-001",
        "force_owner",
        owner_count == "0" and root_rows > 0,
        f"FORCE owner rows={owner_count}; root/admin rows={root_rows}",
    )

    state = clients["runtime_only"].expect_error(
        database,
        "SET ROLE mp_schema_owner",
        expected={"42501"},
    )
    recorder.check(
        "FORCE-002",
        "force_owner",
        state == "42501",
        "runtime cannot SET ROLE to table owner",
        sqlstate=state,
    )

    force_statements = (
        (
            "FORCE-003",
            "ALTER TABLE memory_patch.memory_items "
            f"OWNER TO {role_identifier(roles['runtime_only'])}",
            "runtime cannot change table owner",
        ),
        (
            "FORCE-004",
            "ALTER TABLE memory_patch.memory_items "
            "DISABLE ROW LEVEL SECURITY",
            "runtime cannot disable RLS",
        ),
        (
            "FORCE-005",
            "ALTER TABLE memory_patch.memory_items "
            "NO FORCE ROW LEVEL SECURITY",
            "runtime cannot remove FORCE RLS",
        ),
        (
            "FORCE-006",
            "DROP POLICY memory_items_s5_select "
            "ON memory_patch.memory_items",
            "runtime cannot drop protected policy",
        ),
        (
            "FORCE-007",
            "CREATE POLICY runtime_allow_all "
            "ON memory_patch.memory_items FOR SELECT "
            f"TO {role_identifier(roles['runtime_only'])} USING (true)",
            "runtime cannot create allow-all policy",
        ),
    )
    for probe_id, statement, observed in force_statements:
        state = clients["runtime_only"].expect_error(
            database,
            statement,
            expected={"42501"},
        )
        recorder.check(
            probe_id,
            "force_owner",
            state == "42501",
            observed,
            sqlstate=state,
        )

    escalation_statements = (
        (
            "ESC-001",
            f"CREATE ROLE {role_identifier(roles['runtime_only'] + '_child')}",
            "CREATE ROLE",
        ),
        (
            "ESC-002",
            "ALTER ROLE mp_schema_owner WITH BYPASSRLS",
            "ALTER privileged role",
        ),
        (
            "ESC-003",
            "GRANT mp_schema_owner TO "
            f"{role_identifier(roles['runtime_only'])}",
            "GRANT owner role to self",
        ),
        (
            "ESC-004",
            f"ALTER ROLE {role_identifier(roles['runtime_only'])} "
            "WITH BYPASSRLS",
            "ALTER own role to BYPASSRLS",
        ),
        (
            "ESC-005",
            f"CREATE DATABASE {role_identifier(roles['runtime_only'] + '_db')}",
            "CREATE DATABASE",
        ),
        (
            "ESC-006",
            f"ALTER DATABASE {database} OWNER TO "
            f"{role_identifier(roles['runtime_only'])}",
            "ALTER DATABASE owner",
        ),
        (
            "ESC-007",
            "ALTER SCHEMA memory_patch OWNER TO "
            f"{role_identifier(roles['runtime_only'])}",
            "ALTER SCHEMA owner",
        ),
        (
            "ESC-008",
            "ALTER TABLE memory_patch.memory_items "
            "ADD COLUMN runtime_escalation_probe STRING",
            "ALTER TABLE",
        ),
        (
            "ESC-009",
            "DROP TABLE memory_patch.memory_items",
            "DROP TABLE",
        ),
        (
            "ESC-010",
            "ALTER POLICY memory_items_s5_select "
            "ON memory_patch.memory_items USING (true)",
            "ALTER POLICY",
        ),
        (
            "ESC-011",
            "GRANT ALL ON TABLE memory_patch.memory_items TO "
            f"{role_identifier(roles['runtime_only'])}",
            "GRANT broad table access",
        ),
        (
            "ESC-012",
            "REVOKE SELECT ON TABLE memory_patch.memory_items "
            "FROM mp_app_runtime",
            "REVOKE runtime protections",
        ),
        (
            "ESC-013",
            "TRUNCATE TABLE memory_patch.memory_items",
            "TRUNCATE protected table",
        ),
        (
            "ESC-014",
            "INSERT INTO memory_patch.schema_migrations "
            "(migration_id, checksum_sha256, applied_at, runner_version) "
            f"VALUES ({q(roles['runtime_only'] + '_fake')}, "
            f"{q('0' * 64)}, now(), 'synthetic')",
            "modify migration bookkeeping",
        ),
        (
            "ESC-015",
            "SELECT count(*) FROM memory_patch.request_contexts",
            "read security-internal context table",
        ),
        (
            "ESC-016",
            "SELECT count(*) FROM memory_patch.schema_migrations",
            "read migration bookkeeping",
        ),
    )
    escalation_states: dict[str, str] = {}
    for probe_id, statement, action in escalation_statements:
        expected_states = {"42501", "01006"} if probe_id == "ESC-012" else {"42501"}
        try:
            state = clients["runtime_only"].expect_error(
                database,
                statement,
                expected=expected_states,
            )
        except RlsValidationError as exc:
            raise RlsValidationError(f"{probe_id} ({action}): {exc}") from exc
        escalation_states[probe_id] = state
        recorder.check(
            probe_id,
            "role_escalation",
            state in expected_states,
            f"{action} denied",
            sqlstate=state,
        )
    select_grant_count = int(
        migrations.one_value(
            root.execute(
                database,
                "SELECT count(*) FROM information_schema.table_privileges "
                "WHERE table_schema = 'memory_patch' "
                "AND table_name = 'memory_items' "
                "AND grantee = 'mp_app_runtime' "
                "AND privilege_type = 'SELECT'",
            )
        )
    )
    recorder.check(
        "ESC-012A",
        "role_escalation",
        select_grant_count == 1,
        f"runtime SELECT grant remained after rejected REVOKE={select_grant_count}",
    )

    authorization_state = clients["runtime_only"].expect_error(
        database,
        "SET SESSION AUTHORIZATION mp_schema_owner",
        expected={"42501", "0A000", "42601"},
    )
    recorder.check(
        "ESC-017",
        "role_escalation",
        authorization_state in {"42501", "0A000", "42601"},
        "session authorization change did not succeed; catalog membership is "
        f"the primary proof (SQLSTATE={authorization_state})",
        sqlstate=authorization_state,
    )

    role_names = [*FIXED_ROLES, *roles.values()]
    role_literals = ", ".join(q(role) for role in sorted(role_names))
    options = migrations.parse_tsv(
        root.execute(
            "defaultdb",
            "SELECT rolname, rolcanlogin, rolcreaterole, rolcreatedb, "
            "rolbypassrls, rolsuper "
            "FROM pg_catalog.pg_roles "
            f"WHERE rolname IN ({role_literals}) ORDER BY rolname",
        )
    )
    no_bypass = (
        len(options) == len(role_names)
        and all(row["rolbypassrls"] == "f" for row in options)
        and all(row["rolcreaterole"] == "f" for row in options)
        and all(row["rolcreatedb"] == "f" for row in options)
        and all(row["rolsuper"] == "f" for row in options)
    )
    recorder.check(
        "ESC-018",
        "role_escalation",
        no_bypass,
        f"fixed/test role option rows={len(options)}, all nonprivileged={no_bypass}",
    )

    runtime_membership = migrations.parse_tsv(
        root.execute(
            "defaultdb",
            "SELECT parent.rolname AS parent_role "
            "FROM pg_catalog.pg_auth_members AS membership "
            "JOIN pg_catalog.pg_roles AS parent "
            "ON parent.oid = membership.roleid "
            "JOIN pg_catalog.pg_roles AS member "
            "ON member.oid = membership.member "
            f"WHERE member.rolname = {q(roles['runtime_only'])} "
            "ORDER BY parent.rolname",
        )
    )
    parents = [row["parent_role"] for row in runtime_membership]
    recorder.check(
        "ESC-019",
        "role_escalation",
        parents == ["mp_app_runtime"],
        f"runtime-only inherited roles={parents}",
    )

    owner_rows = migrations.parse_tsv(
        root.execute(
            database,
            "SELECT DISTINCT owner.rolname AS owner_role "
            "FROM pg_catalog.pg_class AS target "
            "JOIN pg_catalog.pg_namespace AS namespace "
            "ON namespace.oid = target.relnamespace "
            "JOIN pg_catalog.pg_roles AS owner ON owner.oid = target.relowner "
            "WHERE namespace.nspname = 'memory_patch' "
            "AND target.relkind = 'r' ORDER BY owner.rolname",
        )
    )
    owners = [row["owner_role"] for row in owner_rows]
    recorder.check(
        "ESC-020",
        "role_escalation",
        owners == ["mp_schema_owner", "mp_security_owner"],
        f"table owners={owners}",
    )
    return {
        "escalation_sqlstates": escalation_states,
        "fixed_and_test_role_options": options,
        "root_admin_row_count_without_context": root_rows,
        "runtime_only_memberships": parents,
        "session_authorization_sqlstate": authorization_state,
        "table_owners": owners,
    }


def run_table_coverage_probe(
    recorder: ProbeRecorder,
    root: migrations.SqlClient,
    runtime_only: RoleSqlClient,
    database: str,
) -> dict[str, int]:
    security = migrations.load_security_manifest()
    protected = [
        row["table"] for row in security["tables"] if row["rls_enabled"]
    ]
    root_counts: dict[str, int] = {}
    runtime_counts: dict[str, int] = {}
    for table in protected:
        root_counts[table] = int(
            migrations.one_value(
                root.execute(
                    database,
                    f"SELECT count(*) FROM memory_patch.{table}",
                )
            )
        )
        runtime_counts[table] = int(
            migrations.one_value(
                runtime_only.execute(
                    database,
                    f"SELECT count(*) FROM memory_patch.{table}",
                )
            )
        )
    all_seeded = all(count > 0 for count in root_counts.values())
    all_closed = all(count == 0 for count in runtime_counts.values())
    recorder.check(
        "COVER-001",
        "table_coverage",
        len(protected) == EXPECTED_PROTECTED_TABLE_COUNT
        and all_seeded
        and all_closed,
        f"protected tables={len(protected)}, all seeded={all_seeded}, "
        f"unset-context all zero={all_closed}",
    )
    return {
        "protected_table_count": len(protected),
        "root_nonempty_table_count": sum(count > 0 for count in root_counts.values()),
        "unset_context_zero_table_count": sum(
            count == 0 for count in runtime_counts.values()
        ),
    }


def binary_identity(binary: Path) -> dict[str, str]:
    verified = migrations.verify_binary_identity(binary)
    result = migrations.run_process([str(binary), "version"], timeout=15)
    if result.returncode != 0:
        raise RlsValidationError("CockroachDB version command failed")
    commit = re.search(r"^Build Commit ID:\s+([0-9a-f]{40})$", result.stdout, re.M)
    platform = re.search(r"^Platform:\s+(.+)$", result.stdout, re.M)
    if commit is None or commit.group(1) != "80586181eb50e380e2cc982f61841eaf38af9982":
        raise RlsValidationError("CockroachDB build commit differs from pin")
    if platform is None or "linux" not in platform.group(1).lower():
        raise RlsValidationError("CockroachDB platform is not Linux")
    return {
        **verified,
        "build_commit": commit.group(1),
        "platform": platform.group(1).strip(),
    }


def run_live_validation(binary: Path, json_output: Path) -> dict[str, Any]:
    offline = offline_validate()
    identity = binary_identity(binary)
    runtime_parent = external_runtime_parent(json_output)
    run_id = RUN_PREFIX + uuid.uuid4().hex[:12]
    database_a = run_id + "_a"
    database_b = run_id + "_b"
    roles = {
        "a1": run_id + "_a1",
        "a2": run_id + "_a2",
        "b1": run_id + "_b1",
        "owner_probe": run_id + "_owner",
        "runtime_only": run_id + "_runtime",
    }
    ids = fixture_ids(run_id)
    runtime = migrations.LocalRuntime(
        binary=binary,
        run_id=run_id,
        runtime_parent=runtime_parent,
    )
    root: migrations.SqlClient | None = None
    created_databases: list[str] = []
    cleanup_errors: list[str] = []
    recorder = ProbeRecorder()
    result: dict[str, Any] | None = None
    failure: BaseException | None = None
    roles_attempted = False
    role_cleanup_verified = False
    try:
        root = runtime.start()
        server_version = migrations.one_value(
            root.execute("defaultdb", "SELECT version()")
        )
        if PINNED_VERSION not in server_version:
            raise RlsValidationError("live server version differs from v26.2.4")
        cluster_version = migrations.one_value(
            root.execute("defaultdb", "SHOW CLUSTER SETTING version")
        )
        if cluster_version != PINNED_CLUSTER_VERSION:
            raise RlsValidationError("live cluster version differs from 26.2")

        for database in (database_a, database_b):
            migrations.create_database(root, database)
            created_databases.append(database)
        first_apply = migrations.apply_migrations(
            root,
            database_a,
            timeout=180,
        )
        no_op_apply = migrations.apply_migrations(
            root,
            database_a,
            timeout=180,
        )
        reproduction_apply = migrations.apply_migrations(
            root,
            database_b,
            timeout=180,
        )
        expected_migration_count = len(migrations.load_migrations())
        if first_apply["applied_count"] != expected_migration_count:
            raise RlsValidationError("fresh database did not apply all migrations")
        if (
            no_op_apply["applied_count"] != 0
            or no_op_apply["skipped_count"] != expected_migration_count
        ):
            raise RlsValidationError("second migration run was not a complete no-op")
        if reproduction_apply["applied_count"] != expected_migration_count:
            raise RlsValidationError("reproduction database did not apply all migrations")

        security_a = security_catalog(root, database_a)
        security_b = security_catalog(root, database_b)
        if security_a["digest"] != security_b["digest"]:
            raise RlsValidationError("security catalog reproduction digest differs")
        recorder.check(
            "MIG-001",
            "migration",
            True,
            "fresh ordered migration apply, complete no-op replay, and "
            "second-database security digest matched",
        )
        print("LIVE PROGRESS: migrations and security reproduction passed", file=sys.stderr, flush=True)

        roles_attempted = True
        create_test_roles(root, roles)
        root.execute(database_a, build_fixture_sql(ids), timeout=180)
        print("LIVE PROGRESS: synthetic roles and fixtures created", file=sys.stderr, flush=True)
        assert runtime.sql_port is not None
        clients = {
            key: RoleSqlClient(
                binary=binary,
                port=runtime.sql_port,
                user=role,
            )
            for key, role in roles.items()
        }
        coverage = run_table_coverage_probe(
            recorder,
            root,
            clients["runtime_only"],
            database_a,
        )
        print("LIVE PROGRESS: table coverage passed", file=sys.stderr, flush=True)
        run_positive_probes(
            recorder,
            root,
            clients,
            database_a,
            ids,
        )
        print("LIVE PROGRESS: positive probes passed", file=sys.stderr, flush=True)
        run_cross_tenant_probes(
            recorder,
            root,
            clients,
            database_a,
            ids,
        )
        print("LIVE PROGRESS: cross-tenant probes passed", file=sys.stderr, flush=True)
        run_cross_user_probes(
            recorder,
            root,
            clients,
            database_a,
            ids,
        )
        print("LIVE PROGRESS: cross-user probes passed", file=sys.stderr, flush=True)
        run_context_probes(
            recorder,
            clients,
            database_a,
            ids,
        )
        print("LIVE PROGRESS: session-context probes passed", file=sys.stderr, flush=True)
        authority = run_force_and_escalation_probes(
            recorder,
            root,
            clients,
            database_a,
            ids,
            roles,
        )
        print("LIVE PROGRESS: FORCE and escalation probes passed", file=sys.stderr, flush=True)
        result = {
            "authority_and_role_escalation": authority,
            "binary_identity": identity,
            "cluster_version": cluster_version,
            "fixture_summary": {
                "personal_memory_principal_count": 3,
                "synthetic_only": True,
                "tenant_count": 2,
                "user_count": 3,
            },
            "insecure_local_transport": {
                "limitations": (
                    "Validates SQL principals, context, grants, RLS, FORCE RLS, "
                    "and denial semantics; it does not validate production "
                    "certificates, transport authentication, SSO, or secret storage."
                ),
                "loopback_only": True,
                "used": True,
            },
            "migration": {
                "first_apply": first_apply,
                "no_op_apply": no_op_apply,
                "reproduction_apply": reproduction_apply,
            },
            "offline": offline,
            "probes": recorder.rows,
            "probe_summary": recorder.summary(),
            "security_catalog": {
                "fixed_roles": security_a["fixed_roles"],
                "membership_edges_before_test_roles": security_a[
                    "membership_edges"
                ],
                "policy_count": len(security_a["policies"]),
                "policy_coverage": [
                    {
                        "command": row["cmd"],
                        "policy": row["policyname"],
                        "roles": row["roles"],
                        "table": row["tablename"],
                    }
                    for row in security_a["policies"]
                ],
                "reproduction_digest": security_a["digest"],
                "routine_grants": security_a["routine_grants"],
                "summary": security_a["summary"],
                "table_coverage": coverage,
                "table_grants": security_a["table_grants"],
                "table_security": security_a["tables"],
                "triggers": security_a["triggers"],
            },
            "server_version": server_version.splitlines()[0],
            "status": "PASS",
            "trust_boundary": {
                "database_principal_binding": False,
                "end_user_authentication_implemented": False,
                "setter_role": "mp_request_context_setter",
                "statement": (
                    "Only a future authenticated trusted application boundary may "
                    "hold the setter role; RLS context does not authenticate a human."
                ),
                "transaction_bound": True,
            },
        }
    except BaseException as exc:
        failure = exc
    finally:
        if root is not None:
            for database in reversed(created_databases):
                try:
                    migrations.drop_database(root, database, timeout=180)
                except Exception as exc:
                    cleanup_errors.append(f"database cleanup: {exc}")
            if roles_attempted or created_databases:
                try:
                    drop_disposable_roles(root, roles)
                    remaining_roles = int(
                        migrations.one_value(
                            root.execute(
                                "defaultdb",
                                "SELECT count(*) FROM pg_catalog.pg_roles "
                                "WHERE rolname LIKE 'mp_step5_%' "
                                "OR rolname IN ('mp_app_runtime', "
                                "'mp_request_context_setter', "
                                "'mp_schema_owner', 'mp_security_owner')",
                            )
                        )
                    )
                    role_cleanup_verified = remaining_roles == 0
                    if not role_cleanup_verified:
                        cleanup_errors.append(
                            f"disposable/fixed roles remain: {remaining_roles}"
                        )
                except Exception as exc:
                    cleanup_errors.append(f"role cleanup: {exc}")
        runtime_cleanup = runtime.stop_and_remove()
        cleanup_errors.extend(runtime_cleanup["cleanup_errors"])
        if runtime_cleanup["force_kill_used"]:
            cleanup_errors.append("graceful shutdown required a forced kill")
    if cleanup_errors:
        raise RlsValidationError(f"live cleanup failed: {cleanup_errors}")
    if failure is not None:
        if isinstance(failure, Exception):
            raise failure
        raise RlsValidationError("live validation was interrupted")
    if result is None:
        raise RlsValidationError("live validation produced no result")
    result["cleanup"] = {
        "databases_removed": len(created_databases) == 2,
        "disposable_and_fixed_roles_removed": role_cleanup_verified,
        "force_kill_used": runtime_cleanup["force_kill_used"],
        "owned_pid_exited": runtime_cleanup["pid_exited"],
        "ports_closed": runtime_cleanup["ports_closed"],
        "temporary_store_removed": runtime_cleanup["temporary_store_removed"],
    }
    result["generated_at_utc"] = migrations.utc_now()
    return result


def write_json_output(path: Path, value: Mapping[str, Any]) -> None:
    output = path.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".partial")
    if temporary.exists():
        if not temporary.is_file() or temporary.is_symlink():
            raise RlsValidationError("refusing unsafe JSON partial output")
        temporary.unlink()
    payload = canonical_json_bytes(value)
    with temporary.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temporary, 0o600)
    os.replace(temporary, output)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--offline-validate", action="store_true")
    action.add_argument("--live-test", action="store_true")
    parser.add_argument("--allow-live", action="store_true")
    parser.add_argument("--cockroach-binary", type=Path)
    parser.add_argument("--json-output", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.offline_validate:
            result = offline_validate()
        else:
            if not args.allow_live:
                raise RlsValidationError("live validation requires --allow-live")
            if args.cockroach_binary is None:
                raise RlsValidationError(
                    "live validation requires --cockroach-binary"
                )
            if args.json_output is None:
                raise RlsValidationError(
                    "live validation requires external --json-output"
                )
            result = run_live_validation(
                args.cockroach_binary.expanduser().resolve(),
                args.json_output,
            )
        if args.json_output is not None:
            write_json_output(args.json_output, result)
        print(canonical_json_bytes(result).decode("utf-8"), end="")
        return 0
    except (RlsValidationError, migrations.MigrationError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
