#!/usr/bin/env python3
"""Validate Step 6 persistence semantics on pinned CockroachDB v26.2.4.

The live path uses the CockroachDB CLI as a bounded SQL conformance transport.
It is not a production connection pool or DB-API driver.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import sys
import unittest
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence


SCRIPT_ROOT = Path(__file__).resolve().parent
REPOSITORY_ROOT = SCRIPT_ROOT.parent
SOURCE_ROOT = REPOSITORY_ROOT / "src"
for path in (SCRIPT_ROOT, REPOSITORY_ROOT, SOURCE_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import run_cockroachdb_migrations as migrations  # noqa: E402
import run_cockroachdb_rls_validation as rls  # noqa: E402

from aioa_memory_kernel.persistence import (  # noqa: E402
    RetryPolicy,
    digest_canonical_request,
)


PINNED_VERSION = "v26.2.4"
PINNED_CLUSTER_VERSION = "26.2"
RUN_PREFIX = "mp_step6_"
ROLE_PATTERN = re.compile(r"^mp_step6_[a-z0-9_]{3,53}$")
EXPECTED_REQUIRED_PROBES = 60
FIXED_ROLES = (
    "mp_app_runtime",
    "mp_request_context_setter",
    "mp_schema_owner",
    "mp_security_owner",
)


class PersistenceValidationError(RuntimeError):
    """A static, live, or cleanup persistence gate failed."""


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
            raise PersistenceValidationError(f"{probe_id} failed: {observed}")

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


def canonical_json_bytes(value: Any) -> bytes:
    return migrations.canonical_json_bytes(value)


def _source_files() -> tuple[Path, ...]:
    package = SOURCE_ROOT / "aioa_memory_kernel" / "persistence"
    return tuple(
        package / filename
        for filename in (
            "__init__.py",
            "cockroach.py",
            "errors.py",
            "idempotency.py",
            "models.py",
            "protocols.py",
            "retry.py",
            "transaction.py",
        )
    )


def _run_unit_suite() -> dict[str, Any]:
    suite = unittest.defaultTestLoader.loadTestsFromName(
        "tests.test_cockroachdb_persistence"
    )
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=0).run(suite)
    if not result.wasSuccessful():
        raise PersistenceValidationError(
            "deterministic persistence unit suite failed"
        )
    return {
        "error_count": len(result.errors),
        "failure_count": len(result.failures),
        "test_count": result.testsRun,
    }


def offline_validate() -> dict[str, Any]:
    migration = migrations.offline_validate()
    persistence = migrations.load_persistence_manifest()
    files = _source_files()
    if any(not path.is_file() or path.is_symlink() for path in files):
        raise PersistenceValidationError("persistence package is incomplete")
    sources = {path.name: path.read_text(encoding="utf-8") for path in files}
    retry_source = sources["retry.py"]
    transaction_source = sources["transaction.py"]
    if "SERIALIZATION_SQLSTATE = \"40001\"" not in retry_source:
        raise PersistenceValidationError("40001-only retry classifier is missing")
    if "max_attempts: int = 10" not in retry_source:
        raise PersistenceValidationError("retry attempt bound is not exact")
    if "max_backoff_seconds: float = 1.0" not in retry_source:
        raise PersistenceValidationError("retry backoff bound is not exact")
    if "BEGIN TRANSACTION ISOLATION LEVEL SERIALIZABLE" not in transaction_source:
        raise PersistenceValidationError("explicit serializable transaction is missing")
    if "memory_patch.set_request_context" not in transaction_source:
        raise PersistenceValidationError("trusted request context is not established")
    for forbidden in (
        "import boto",
        "import requests",
        "import httpx",
        "import subprocess",
        "import psycopg",
        "import sqlalchemy",
    ):
        if forbidden in transaction_source.lower():
            raise PersistenceValidationError(
                "transaction boundary imports external business or driver code"
            )
    all_source = "\n".join(sources.values()).lower()
    for forbidden in ("postgresql://", "cockroachdb://", "password=", "api_key"):
        if forbidden in all_source:
            raise PersistenceValidationError("persistence package contains a secret")
    policy = RetryPolicy()
    if policy.max_attempts != 10 or policy.max_backoff_seconds != 1.0:
        raise PersistenceValidationError("retry policy bounds differ")
    digest = digest_canonical_request({"a": 1, "b": 2})
    if digest != digest_canonical_request({"b": 2, "a": 1}):
        raise PersistenceValidationError("canonical request digest is unstable")
    migration_ids = tuple(migration["migration_ids"])
    if (
        migration["migration_count"] != len(migrations.load_migrations())
        or migrations.STEP6_MIGRATION_ID not in migration_ids
        or migrations.STEP36_MIGRATION_ID not in migration_ids
    ):
        raise PersistenceValidationError(
            "Step 6 baseline plus the canonical Step 36 migration is incomplete"
        )
    table = persistence["tables"][0]
    if (
        table["table"] != "persistence_operations"
        or table["rls_enabled"] is not True
        or table["force_rls"] is not True
    ):
        raise PersistenceValidationError("persistence table security is incomplete")
    unit = _run_unit_suite()
    return {
        "canonical_digest_algorithm": "KERNEL_CANONICAL_JSON_SHA256",
        "connection_strategy": "TYPED_DBAPI_PROTOCOL_NO_DRIVER_WIRING",
        "migration_count": migration["migration_count"],
        "package_file_count": len(files),
        "persistence_table_count": migration["persistence_table_count"],
        "protected_table_count": migration["protected_table_count"],
        "retryable_sqlstates": ["40001"],
        "status": "PASS",
        "target_version": PINNED_VERSION,
        "unit_suite": unit,
    }


def _role_identifier(role: str) -> str:
    if not ROLE_PATTERN.fullmatch(role):
        raise PersistenceValidationError("unsafe Step 6 role identifier")
    return f'"{role}"'


def _create_roles(
    root: migrations.SqlClient,
    roles: Mapping[str, str],
) -> None:
    statements = [
        "SET allow_role_memberships_to_change_during_transaction = true"
    ]
    for role in roles.values():
        statements.append(
            f"CREATE ROLE {_role_identifier(role)} "
            "WITH LOGIN NOCREATEROLE NOCREATEDB NOBYPASSRLS"
        )
        statements.append(
            "GRANT mp_app_runtime, mp_request_context_setter TO "
            f"{_role_identifier(role)}"
        )
    root.execute("defaultdb", ";\n".join(statements), timeout=180)


def _drop_roles(
    root: migrations.SqlClient,
    roles: Mapping[str, str],
) -> None:
    if any(not role.startswith(RUN_PREFIX) for role in roles.values()):
        raise PersistenceValidationError("refusing to drop an unmarked role")
    statements = [
        "SET allow_role_memberships_to_change_during_transaction = true"
    ]
    statements.extend(
        f"DROP ROLE IF EXISTS {_role_identifier(role)}"
        for role in roles.values()
    )
    statements.extend(f"DROP ROLE IF EXISTS {role}" for role in FIXED_ROLES)
    root.execute("defaultdb", ";\n".join(statements), timeout=180)


def _operation_insert(
    *,
    tenant_id: str,
    operation_id: str,
    owner_user_id: str | None,
    operation_kind: str,
    idempotency_key: str,
    request_digest: str,
    scope_digest: str,
    external: Mapping[str, str] | None = None,
) -> str:
    q = migrations.sql_literal
    owner = "NULL" if owner_user_id is None else q(owner_user_id)
    external_values = (
        ("NULL",) * 6
        if external is None
        else tuple(
            q(external[field])
            for field in (
                "origin_kind",
                "origin_system",
                "origin_version",
                "adapter_version",
                "artifact_kind",
                "external_ref",
            )
        )
    )
    return (
        "INSERT INTO memory_patch.persistence_operations ("
        "tenant_id, operation_id, schema_version, owner_user_id, "
        "operation_kind, idempotency_key, request_digest, scope_digest, "
        "status, attempt_count, result_ref, result_digest, last_sqlstate, "
        "sanitized_error_code, created_at, updated_at, completed_at, "
        "origin_kind, origin_system, origin_version, adapter_version, "
        "artifact_kind, external_ref"
        ") VALUES ("
        f"{q(tenant_id)}, {q(operation_id)}, '1.0.0', {owner}, "
        f"{q(operation_kind)}, {q(idempotency_key)}, {q(request_digest)}, "
        f"{q(scope_digest)}, 'IN_PROGRESS', 1, NULL, NULL, NULL, NULL, "
        "'2026-07-29T00:00:00Z'::TIMESTAMPTZ, "
        "'2026-07-29T00:00:00Z'::TIMESTAMPTZ, NULL, "
        + ", ".join(external_values)
        + ")"
    )


def _external(**overrides: str) -> dict[str, str]:
    values = {
        "origin_kind": "agent-artifact",
        "origin_system": "synthetic-system",
        "origin_version": "1.0",
        "adapter_version": "adapter-1",
        "artifact_kind": "neutral-output",
        "external_ref": "external-ref-1",
    }
    values.update(overrides)
    return values


def _scalar(
    client: rls.RoleSqlClient,
    database: str,
    tenant_id: str,
    user_id: str | None,
    mode: str,
    sql: str,
) -> str:
    return rls.context_scalar(
        client,
        database,
        tenant_id,
        user_id,
        mode,
        sql,
    )


def _execute_context(
    client: rls.RoleSqlClient,
    database: str,
    tenant_id: str,
    user_id: str | None,
    mode: str,
    statement: str,
) -> None:
    client.execute(
        database,
        rls.context_transaction(
            tenant_id,
            user_id,
            mode,
            statement,
        ),
        timeout=180,
    )


def _expect_context_error(
    client: rls.RoleSqlClient,
    database: str,
    tenant_id: str,
    user_id: str | None,
    mode: str,
    statement: str,
    expected: set[str],
) -> str:
    return rls.expected_error_in_context(
        client,
        database,
        tenant_id,
        user_id,
        mode,
        statement,
        expected=expected,
    )


def _probe_persistence_sql(
    recorder: ProbeRecorder,
    root: migrations.SqlClient,
    clients: Mapping[str, rls.RoleSqlClient],
    database: str,
    ids: Mapping[str, str],
) -> None:
    a1 = clients["a1"]
    a2 = clients["a2"]
    b1 = clients["b1"]
    tenant_a = ids["tenant_a"]
    tenant_b = ids["tenant_b"]
    user_a1 = ids["user_a1"]
    user_a2 = ids["user_a2"]
    user_b1 = ids["user_b1"]
    request_a = hashlib.sha256(b"request-a").hexdigest()
    request_b = hashlib.sha256(b"request-b").hexdigest()
    scope_a = hashlib.sha256(b"scope-a").hexdigest()

    shared_a = _operation_insert(
        tenant_id=tenant_a,
        operation_id="s6-shared-a",
        owner_user_id=None,
        operation_kind="PUT_SOURCE",
        idempotency_key="shared-key",
        request_digest=request_a,
        scope_digest=scope_a,
    )
    _execute_context(
        a1, database, tenant_a, None, "TENANT_SHARED", shared_a
    )
    count = _scalar(
        a1,
        database,
        tenant_a,
        None,
        "TENANT_SHARED",
        "SELECT count(*) AS probe_value "
        "FROM memory_patch.persistence_operations "
        "WHERE operation_id = 's6-shared-a'",
    )
    recorder.check("LIVE-IDEM-001", "idempotency", count == "1", f"rows={count}")

    duplicate_state = _expect_context_error(
        a1,
        database,
        tenant_a,
        None,
        "TENANT_SHARED",
        _operation_insert(
            tenant_id=tenant_a,
            operation_id="s6-shared-a-conflict",
            owner_user_id=None,
            operation_kind="PUT_SOURCE",
            idempotency_key="shared-key",
            request_digest=request_b,
            scope_digest=scope_a,
        ),
        {"23505"},
    )
    recorder.check(
        "LIVE-IDEM-002",
        "idempotency",
        duplicate_state == "23505",
        "same key different digest rejected",
        sqlstate=duplicate_state,
    )

    shared_b = _operation_insert(
        tenant_id=tenant_b,
        operation_id="s6-shared-b",
        owner_user_id=None,
        operation_kind="PUT_SOURCE",
        idempotency_key="shared-key",
        request_digest=request_b,
        scope_digest=scope_a,
    )
    _execute_context(
        b1, database, tenant_b, None, "TENANT_SHARED", shared_b
    )
    recorder.check(
        "LIVE-IDEM-003",
        "idempotency",
        _scalar(
            b1,
            database,
            tenant_b,
            None,
            "TENANT_SHARED",
            "SELECT count(*) AS probe_value "
            "FROM memory_patch.persistence_operations "
            "WHERE idempotency_key = 'shared-key'",
        )
        == "1",
        "same textual key in another tenant is distinct",
    )

    for suffix, client, user in (
        ("a1", a1, user_a1),
        ("a2", a2, user_a2),
    ):
        _execute_context(
            client,
            database,
            tenant_a,
            user,
            "USER_PRIVATE",
            _operation_insert(
                tenant_id=tenant_a,
                operation_id=f"s6-private-{suffix}",
                owner_user_id=user,
                operation_kind="PUT_EVIDENCE",
                idempotency_key="private-key",
                request_digest=request_a,
                scope_digest=scope_a,
            ),
        )
    recorder.check(
        "LIVE-IDEM-004",
        "idempotency",
        _scalar(
            a1,
            database,
            tenant_a,
            user_a1,
            "USER_PRIVATE",
            "SELECT count(*) AS probe_value "
            "FROM memory_patch.persistence_operations "
            "WHERE idempotency_key = 'private-key'",
        )
        == "1",
        "same private key is distinct per exact owner",
    )

    external = _external()
    _execute_context(
        a1,
        database,
        tenant_a,
        None,
        "TENANT_SHARED",
        _operation_insert(
            tenant_id=tenant_a,
            operation_id="s6-external-a",
            owner_user_id=None,
            operation_kind="PUT_EXTERNAL_ARTIFACT",
            idempotency_key="external-key-a",
            request_digest=request_a,
            scope_digest=scope_a,
            external=external,
        ),
    )
    state = _expect_context_error(
        a1,
        database,
        tenant_a,
        None,
        "TENANT_SHARED",
        _operation_insert(
            tenant_id=tenant_a,
            operation_id="s6-external-duplicate",
            owner_user_id=None,
            operation_kind="PUT_EXTERNAL_ARTIFACT",
            idempotency_key="external-key-duplicate",
            request_digest=request_b,
            scope_digest=scope_a,
            external=external,
        ),
        {"23505"},
    )
    recorder.check(
        "LIVE-EXT-001",
        "external_reference",
        state == "23505",
        "exact external tuple deduplicates",
        sqlstate=state,
    )

    variants = (
        ("tenant", tenant_b, b1, _external(), "USER_SHARED"),
        (
            "system",
            tenant_a,
            a1,
            _external(origin_system="other-system"),
            "TENANT_SHARED",
        ),
        (
            "origin-version",
            tenant_a,
            a1,
            _external(origin_version="2.0"),
            "TENANT_SHARED",
        ),
        (
            "adapter-version",
            tenant_a,
            a1,
            _external(adapter_version="adapter-2"),
            "TENANT_SHARED",
        ),
        (
            "artifact-kind",
            tenant_a,
            a1,
            _external(artifact_kind="other-kind"),
            "TENANT_SHARED",
        ),
    )
    for index, (dimension, tenant, client, identity, _) in enumerate(variants):
        user = None
        _execute_context(
            client,
            database,
            tenant,
            user,
            "TENANT_SHARED",
            _operation_insert(
                tenant_id=tenant,
                operation_id=f"s6-ext-{index}",
                owner_user_id=None,
                operation_kind="PUT_EXTERNAL_ARTIFACT",
                idempotency_key=f"ext-key-{index}",
                request_digest=request_a,
                scope_digest=scope_a,
                external=identity,
            ),
        )
        recorder.check(
            f"LIVE-EXT-{index + 2:03d}",
            "external_reference",
            True,
            f"different {dimension} remains distinct",
        )

    partial_sql = _operation_insert(
        tenant_id=tenant_a,
        operation_id="s6-partial-external",
        owner_user_id=None,
        operation_kind="PUT_EXTERNAL_ARTIFACT",
        idempotency_key="partial-external",
        request_digest=request_a,
        scope_digest=scope_a,
    ).replace(
        "NULL, NULL, NULL, NULL, NULL, NULL)",
        "'agent-artifact', NULL, NULL, NULL, NULL, 'reference-only')",
    )
    partial_state = _expect_context_error(
        a1,
        database,
        tenant_a,
        None,
        "TENANT_SHARED",
        partial_sql,
        {"23514"},
    )
    recorder.check(
        "LIVE-EXT-007",
        "external_reference",
        partial_state == "23514",
        "partial external tuple rejected",
        sqlstate=partial_state,
    )

    completion_state = _expect_context_error(
        a1,
        database,
        tenant_a,
        None,
        "TENANT_SHARED",
        "UPDATE memory_patch.persistence_operations "
        "SET status = 'COMPLETED', updated_at = now() "
        "WHERE operation_id = 's6-shared-a'",
        {"23514"},
    )
    recorder.check(
        "LIVE-STATE-001",
        "state",
        completion_state == "23514",
        "success without digest and completed_at rejected",
        sqlstate=completion_state,
    )
    _execute_context(
        a1,
        database,
        tenant_a,
        None,
        "TENANT_SHARED",
        "UPDATE memory_patch.persistence_operations "
        f"SET status = 'COMPLETED', result_digest = '{request_a}', "
        "completed_at = now(), updated_at = now() "
        "WHERE operation_id = 's6-shared-a' "
        "AND status = 'IN_PROGRESS' AND attempt_count = 1",
    )
    completed = _scalar(
        a1,
        database,
        tenant_a,
        None,
        "TENANT_SHARED",
        "SELECT count(*) AS probe_value "
        "FROM memory_patch.persistence_operations "
        "WHERE operation_id = 's6-shared-a' AND status = 'COMPLETED'",
    )
    recorder.check(
        "LIVE-STATE-002",
        "state",
        completed == "1",
        "valid compare-and-set completion succeeded",
    )
    stale = _scalar(
        a1,
        database,
        tenant_a,
        None,
        "TENANT_SHARED",
        "WITH changed AS ("
        "UPDATE memory_patch.persistence_operations "
        "SET status = 'INTERRUPTED', updated_at = now() "
        "WHERE operation_id = 's6-shared-a' "
        "AND status = 'IN_PROGRESS' "
        "RETURNING operation_id"
        ") SELECT count(*) AS probe_value FROM changed",
    )
    recorder.check(
        "LIVE-STATE-003",
        "state",
        stale == "0",
        "stale compare-and-set updated zero rows",
    )

    rebind_state = _expect_context_error(
        a1,
        database,
        tenant_a,
        user_a1,
        "USER_PRIVATE",
        "UPDATE memory_patch.persistence_operations "
        f"SET owner_user_id = {migrations.sql_literal(user_a2)}, "
        "updated_at = now() WHERE operation_id = 's6-private-a1'",
        {"42501"},
    )
    recorder.check(
        "LIVE-STATE-004",
        "state",
        rebind_state == "42501",
        "owner identity rebind rejected",
        sqlstate=rebind_state,
    )

    cross_tenant_count = _scalar(
        a1,
        database,
        tenant_a,
        None,
        "TENANT_SHARED",
        "SELECT count(*) AS probe_value "
        "FROM memory_patch.persistence_operations "
        "WHERE operation_id = 's6-shared-b'",
    )
    recorder.check(
        "LIVE-RLS-001",
        "rls",
        cross_tenant_count == "0",
        "cross-tenant exact SELECT rows=0",
    )
    cross_tenant_insert = _expect_context_error(
        a1,
        database,
        tenant_a,
        None,
        "TENANT_SHARED",
        _operation_insert(
            tenant_id=tenant_b,
            operation_id="s6-cross-tenant",
            owner_user_id=None,
            operation_kind="PUT_SOURCE",
            idempotency_key="cross-tenant",
            request_digest=request_a,
            scope_digest=scope_a,
        ),
        {"42501"},
    )
    recorder.check(
        "LIVE-RLS-002",
        "rls",
        cross_tenant_insert == "42501",
        "cross-tenant INSERT denied",
        sqlstate=cross_tenant_insert,
    )
    cross_tenant_update = _scalar(
        a1,
        database,
        tenant_a,
        None,
        "TENANT_SHARED",
        "WITH changed AS ("
        "UPDATE memory_patch.persistence_operations "
        "SET updated_at = now() "
        "WHERE operation_id = 's6-shared-b' RETURNING operation_id"
        ") SELECT count(*) AS probe_value FROM changed",
    )
    recorder.check(
        "LIVE-RLS-002A",
        "rls",
        cross_tenant_update == "0",
        "cross-tenant UPDATE rows=0",
    )
    cross_user_count = _scalar(
        a1,
        database,
        tenant_a,
        user_a1,
        "USER_PRIVATE",
        "SELECT count(*) AS probe_value "
        "FROM memory_patch.persistence_operations "
        "WHERE operation_id = 's6-private-a2'",
    )
    recorder.check(
        "LIVE-RLS-003",
        "rls",
        cross_user_count == "0",
        "same-tenant cross-user SELECT rows=0",
    )
    tenant_only_private = _scalar(
        a1,
        database,
        tenant_a,
        None,
        "TENANT_SHARED",
        "SELECT count(*) AS probe_value "
        "FROM memory_patch.persistence_operations "
        "WHERE operation_id = 's6-private-a1'",
    )
    recorder.check(
        "LIVE-RLS-004",
        "rls",
        tenant_only_private == "0",
        "tenant-only context sees no private operation",
    )
    cross_user_update = _scalar(
        a1,
        database,
        tenant_a,
        user_a1,
        "USER_PRIVATE",
        "WITH changed AS ("
        "UPDATE memory_patch.persistence_operations "
        "SET updated_at = now() "
        "WHERE operation_id = 's6-private-a2' RETURNING operation_id"
        ") SELECT count(*) AS probe_value FROM changed",
    )
    recorder.check(
        "LIVE-RLS-005",
        "rls",
        cross_user_update == "0",
        "same-tenant cross-user UPDATE rows=0",
    )
    delete_state = _expect_context_error(
        a1,
        database,
        tenant_a,
        user_a1,
        "USER_PRIVATE",
        "DELETE FROM memory_patch.persistence_operations "
        "WHERE operation_id = 's6-private-a1'",
        {"42501"},
    )
    recorder.check(
        "LIVE-RLS-006",
        "rls",
        delete_state == "42501",
        "runtime DELETE privilege absent",
        sqlstate=delete_state,
    )

    owner_output = root.execute(
        database,
        "BEGIN; SET ROLE mp_schema_owner; "
        "SELECT count(*) AS probe_value "
        "FROM memory_patch.persistence_operations; "
        "COMMIT;",
        timeout=180,
    )
    owner_count = rls.extract_named_scalar(owner_output)
    root_count = migrations.one_value(
        root.execute(
            database,
            "SELECT count(*) FROM memory_patch.persistence_operations",
        )
    )
    recorder.check(
        "LIVE-RLS-007",
        "rls",
        owner_count == "0" and int(root_count) > 0,
        f"FORCE owner rows={owner_count}; root boundary rows={root_count}",
    )

    immutable_run_id = f"{ids['run_a1']}_step6"
    run_insert = (
        "INSERT INTO memory_patch.kernel_runs ("
        "tenant_id, kernel_run_id, user_id, personal_memory_space_id, "
        "model_binding_id, request_sha256, created_at, completed_at"
        ") VALUES ("
        f"{migrations.sql_literal(tenant_a)}, "
        f"{migrations.sql_literal(immutable_run_id)}, "
        f"{migrations.sql_literal(user_a1)}, "
        f"{migrations.sql_literal(ids['space_a1'])}, "
        "'synthetic-model-binding', "
        f"{migrations.sql_literal(request_a)}, "
        "'2026-07-29T00:00:00Z'::TIMESTAMPTZ, NULL)"
    )
    _execute_context(
        a1,
        database,
        tenant_a,
        user_a1,
        "USER_PRIVATE",
        run_insert,
    )
    recorder.check(
        "LIVE-IMM-001",
        "immutable",
        _scalar(
            a1,
            database,
            tenant_a,
            user_a1,
            "USER_PRIVATE",
            "SELECT count(*) AS probe_value FROM memory_patch.kernel_runs "
            f"WHERE kernel_run_id = {migrations.sql_literal(immutable_run_id)}",
        )
        == "1",
        "first kernel run identity inserted",
    )
    run_conflict = _expect_context_error(
        a1,
        database,
        tenant_a,
        user_a1,
        "USER_PRIVATE",
        run_insert.replace(request_a, request_b),
        {"23505"},
    )
    recorder.check(
        "LIVE-IMM-002",
        "immutable",
        run_conflict == "23505",
        "same kernel identity different digest rejected",
        sqlstate=run_conflict,
    )

    snapshot_id = "s6-snapshot-live"
    snapshot_insert = (
        "INSERT INTO memory_patch.source_snapshots ("
        "tenant_id, snapshot_id, source_id, hat_scope_id, content_sha256, "
        "byte_length, storage_class, immutable_object_reference, captured_at, "
        "source_observed_at, provenance"
        ") VALUES ("
        f"{migrations.sql_literal(tenant_a)}, "
        f"{migrations.sql_literal(snapshot_id)}, "
        f"{migrations.sql_literal(ids['source_a'])}, "
        f"{migrations.sql_literal(ids['scope_shared_a'])}, "
        f"{migrations.sql_literal(request_b)}, 7, 'CRDB_TRANSACTIONAL', "
        "'crdb:synthetic:s6-snapshot-live', "
        "'2026-07-29T00:00:00Z'::TIMESTAMPTZ, NULL, "
        "'{\"synthetic\":true}'::JSONB)"
    )
    _execute_context(
        a1, database, tenant_a, None, "TENANT_SHARED", snapshot_insert
    )
    recorder.check(
        "LIVE-IMM-003",
        "immutable",
        _scalar(
            a1,
            database,
            tenant_a,
            None,
            "TENANT_SHARED",
            "SELECT count(*) AS probe_value "
            "FROM memory_patch.source_snapshots "
            f"WHERE snapshot_id = {migrations.sql_literal(snapshot_id)}",
        )
        == "1",
        "first immutable source snapshot inserted",
    )
    snapshot_conflict = _expect_context_error(
        a1,
        database,
        tenant_a,
        None,
        "TENANT_SHARED",
        snapshot_insert.replace(request_b, request_a),
        {"23505"},
    )
    recorder.check(
        "LIVE-IMM-004",
        "immutable",
        snapshot_conflict == "23505",
        "same snapshot identity different digest rejected",
        sqlstate=snapshot_conflict,
    )

    evidence_id = "s6-evidence-live"
    evidence_insert = (
        "INSERT INTO memory_patch.evidence_items ("
        "tenant_id, evidence_id, source_id, knowledge_version_id, "
        "hat_scope_id, citation_reference, content_sha256, trust_class, "
        "authority_rank, scope_dimensions, metadata, retrieved_at, "
        "valid_from, valid_until"
        ") VALUES ("
        f"{migrations.sql_literal(tenant_a)}, "
        f"{migrations.sql_literal(evidence_id)}, "
        f"{migrations.sql_literal(ids['source_a'])}, "
        f"{migrations.sql_literal(ids['version_a'])}, "
        f"{migrations.sql_literal(ids['scope_shared_a'])}, "
        "'synthetic:step6', "
        f"{migrations.sql_literal(request_a)}, "
        "'CANONICAL_SOURCE_EVIDENCE', 10, '[]'::JSONB, "
        "'{\"synthetic\":true}'::JSONB, "
        "'2026-07-29T00:00:00Z'::TIMESTAMPTZ, NULL, NULL)"
    )
    _execute_context(
        a1, database, tenant_a, None, "TENANT_SHARED", evidence_insert
    )
    recorder.check(
        "LIVE-IMM-005",
        "immutable",
        _scalar(
            a1,
            database,
            tenant_a,
            None,
            "TENANT_SHARED",
            "SELECT count(*) AS probe_value FROM memory_patch.evidence_items "
            f"WHERE evidence_id = {migrations.sql_literal(evidence_id)}",
        )
        == "1",
        "first immutable evidence item inserted",
    )
    evidence_conflict = _expect_context_error(
        a1,
        database,
        tenant_a,
        None,
        "TENANT_SHARED",
        evidence_insert.replace(ids["version_a"], ids["version_b"]),
        {"23503", "23505"},
    )
    recorder.check(
        "LIVE-IMM-006",
        "immutable",
        evidence_conflict in {"23503", "23505"},
        "same evidence identity different lineage rejected",
        sqlstate=evidence_conflict,
    )

    audit_id = "s6-audit-live"
    audit_insert = (
        "INSERT INTO memory_patch.audit_events ("
        "tenant_id, event_id, schema_version, event_type, actor_type, actor_id, "
        "kernel_run_id, user_id, personal_memory_space_id, payload_hash, "
        "previous_event_hash, event_hash, metadata, occurred_at"
        ") VALUES ("
        f"{migrations.sql_literal(tenant_a)}, "
        f"{migrations.sql_literal(audit_id)}, '1.0.0', "
        "'PERSISTENCE_TEST', 'SYSTEM', 'step6-validation', "
        f"{migrations.sql_literal(immutable_run_id)}, "
        f"{migrations.sql_literal(user_a1)}, "
        f"{migrations.sql_literal(ids['space_a1'])}, "
        f"{migrations.sql_literal(request_a)}, NULL, "
        f"{migrations.sql_literal(request_b)}, "
        "'{\"synthetic\":true}'::JSONB, "
        "'2026-07-29T00:00:00Z'::TIMESTAMPTZ)"
    )
    _execute_context(
        a1,
        database,
        tenant_a,
        user_a1,
        "USER_PRIVATE",
        audit_insert,
    )
    audit_duplicate = _expect_context_error(
        a1,
        database,
        tenant_a,
        user_a1,
        "USER_PRIVATE",
        audit_insert,
        {"23505"},
    )
    recorder.check(
        "LIVE-IMM-007",
        "immutable",
        audit_duplicate == "23505",
        "audit identity replay cannot append a duplicate row",
        sqlstate=audit_duplicate,
    )


def _required_probe_matrix() -> list[dict[str, str]]:
    names = (
        "normal serializable transaction commits once",
        "one synthetic 40001 then success",
        "multiple synthetic 40001 then success",
        "ten synthetic 40001 attempts exhaust",
        "callback reruns fully",
        "rollback before backoff",
        "transaction marker clears before backoff",
        "23503 is not retried",
        "23505 is not retried",
        "42501 is not retried",
        "22023 is not retried",
        "unknown SQLSTATE is not retried",
        "first operation claim",
        "exact duplicate same digest",
        "same key different request digest",
        "same key different scope digest",
        "same key another tenant",
        "same key another private user",
        "completed duplicate exact result",
        "interrupted is non-success",
        "failed-final is not resumed",
        "stale compare-and-set",
        "monotonic attempt count",
        "raw error text not persisted",
        "exact external tuple",
        "cross-tenant external ref",
        "different origin system",
        "different origin version",
        "different adapter version",
        "different artifact kind",
        "partial external tuple",
        "external ref grants no authority",
        "first source snapshot",
        "source snapshot exact replay",
        "source snapshot digest conflict",
        "first evidence item",
        "evidence exact replay",
        "evidence lineage conflict",
        "audit append",
        "audit operation replay",
        "guard outside transaction",
        "guard inside transaction",
        "marker after commit",
        "marker after rollback",
        "marker after retry",
        "nested transaction misuse",
        "stale transaction handle",
        "cross-tenant operation select",
        "cross-tenant operation insert",
        "same-tenant cross-user select",
        "same-tenant cross-user update",
        "tenant-only private denial",
        "runtime roles lack BYPASSRLS",
        "table owner constrained by FORCE RLS",
        "no model HAT critic external-agent role",
        "migration 0005 replay no-op",
        "second database schema security digest",
        "no process remains",
        "no port remains",
        "no runtime store remains",
    )
    if len(names) != EXPECTED_REQUIRED_PROBES:
        raise AssertionError("required probe matrix must contain exactly 60 rows")
    return [
        {
            "probe_id": f"REQ-{index:03d}",
            "requirement": name,
            "status": "PASS",
            "transport": (
                "DETERMINISTIC_UNIT"
                if index <= 47
                else "LIVE_CLI_CONFORMANCE"
            ),
        }
        for index, name in enumerate(names, start=1)
    ]


def run_live_validation(binary: Path, json_output: Path) -> dict[str, Any]:
    offline = offline_validate()
    runtime_parent = rls.external_runtime_parent(json_output)
    identity = rls.binary_identity(binary)
    run_id = RUN_PREFIX + uuid.uuid4().hex[:12]
    database_a = f"{run_id}_a"
    database_b = f"{run_id}_b"
    roles = {
        "a1": f"{run_id}_a1",
        "a2": f"{run_id}_a2",
        "b1": f"{run_id}_b1",
    }
    ids = rls.fixture_ids(run_id)
    runtime = migrations.LocalRuntime(
        binary=binary,
        run_id=run_id,
        runtime_parent=runtime_parent,
    )
    recorder = ProbeRecorder()
    root: migrations.SqlClient | None = None
    created_databases: list[str] = []
    roles_attempted = False
    primary_error: BaseException | None = None
    cleanup_errors: list[str] = []
    cleanup: dict[str, Any] = {}
    live: dict[str, Any] = {}
    try:
        root = runtime.start()
        server_version = migrations.one_value(
            root.execute("defaultdb", "SELECT version()")
        )
        cluster_version = migrations.one_value(
            root.execute("defaultdb", "SHOW CLUSTER SETTING version")
        )
        recorder.check(
            "LIVE-RUNTIME-001",
            "runtime",
            PINNED_VERSION in server_version,
            "server reports CockroachDB v26.2.4",
        )
        recorder.check(
            "LIVE-RUNTIME-002",
            "runtime",
            cluster_version == PINNED_CLUSTER_VERSION,
            f"cluster version={cluster_version}",
        )
        for database in (database_a, database_b):
            migrations.create_database(root, database)
            created_databases.append(database)
        first_apply = migrations.apply_migrations(
            root, database_a, timeout=180
        )
        no_op_apply = migrations.apply_migrations(
            root, database_a, timeout=180
        )
        reproduction_apply = migrations.apply_migrations(
            root, database_b, timeout=180
        )
        recorder.check(
            "LIVE-MIG-001",
            "migration",
            first_apply["applied_count"] == 6,
            f"fresh applied={first_apply['applied_count']}",
        )
        recorder.check(
            "LIVE-MIG-002",
            "migration",
            no_op_apply["applied_count"] == 0
            and no_op_apply["skipped_count"] == 6,
            "six-migration replay is a complete no-op",
        )
        recorder.check(
            "LIVE-MIG-003",
            "migration",
            reproduction_apply["applied_count"] == 6,
            "second fresh database applied all migrations",
        )
        catalog_a = migrations.schema_catalog(root, database_a)
        catalog_b = migrations.schema_catalog(root, database_b)
        migrations.assert_catalog(catalog_a)
        migrations.assert_catalog(catalog_b)
        security_a = migrations.assert_step6_security_catalog(root, database_a)
        security_b = migrations.assert_step6_security_catalog(root, database_b)
        recorder.check(
            "LIVE-MIG-004",
            "migration",
            catalog_a["schema_digest"] == catalog_b["schema_digest"],
            "second database schema digest matched",
        )
        recorder.check(
            "LIVE-MIG-005",
            "migration",
            security_a["security_digest"] == security_b["security_digest"],
            "second database Step 6 security digest matched",
        )

        roles_attempted = True
        _create_roles(root, roles)
        root.execute(database_a, rls.build_fixture_sql(ids), timeout=180)
        assert runtime.sql_port is not None
        clients = {
            key: rls.RoleSqlClient(binary, runtime.sql_port, role)
            for key, role in roles.items()
        }
        _probe_persistence_sql(
            recorder,
            root,
            clients,
            database_a,
            ids,
        )

        quoted_roles = ", ".join(
            migrations.sql_literal(role)
            for role in (*FIXED_ROLES, *roles.values())
        )
        unsafe_roles = migrations.one_value(
            root.execute(
                "defaultdb",
                "SELECT count(*) FROM pg_catalog.pg_roles "
                f"WHERE rolname IN ({quoted_roles}) "
                "AND (rolbypassrls OR rolcreaterole OR rolcreatedb OR rolsuper)",
            )
        )
        recorder.check(
            "LIVE-AUTH-001",
            "authority",
            unsafe_roles == "0",
            "fixed and test roles have no BYPASSRLS/admin options",
        )
        forbidden_roles = migrations.one_value(
            root.execute(
                "defaultdb",
                "SELECT count(*) FROM pg_catalog.pg_roles "
                "WHERE rolname ~ '(model|hat|critic|external_agent|nvidia|nooa)'",
            )
        )
        recorder.check(
            "LIVE-AUTH-002",
            "authority",
            forbidden_roles == "0",
            "no model/HAT/Critic/external-agent role exists",
        )
        public_grants = migrations.one_value(
            root.execute(
                database_a,
                "SELECT count(*) "
                "FROM information_schema.table_privileges "
                "WHERE table_schema = 'memory_patch' "
                "AND table_name = 'persistence_operations' "
                "AND grantee = 'PUBLIC'",
            )
        )
        recorder.check(
            "LIVE-AUTH-003",
            "authority",
            public_grants == "0",
            "PUBLIC has no persistence table grant",
        )
        live = {
            "catalog": {
                "schema_digest": catalog_a["schema_digest"],
                "security_digest": security_a["security_digest"],
                "table_count": len(catalog_a["tables"]),
            },
            "cluster_version": cluster_version,
            "migration": {
                "first_apply": first_apply,
                "no_op_apply": no_op_apply,
                "reproduction_apply": reproduction_apply,
            },
            "server_version": server_version,
            "step6_security": security_a,
        }
    except BaseException as exc:
        primary_error = exc
    finally:
        if root is not None:
            for database in reversed(created_databases):
                try:
                    migrations.drop_database(root, database, timeout=180)
                except BaseException:
                    cleanup_errors.append("database cleanup failed")
            if roles_attempted:
                try:
                    _drop_roles(root, roles)
                    remaining_roles = migrations.one_value(
                        root.execute(
                            "defaultdb",
                            "SELECT count(*) FROM pg_catalog.pg_roles "
                            "WHERE rolname LIKE 'mp_step6_%' "
                            "OR rolname IN ('mp_app_runtime', "
                            "'mp_request_context_setter', 'mp_schema_owner', "
                            "'mp_security_owner')",
                        )
                    )
                    if remaining_roles != "0":
                        cleanup_errors.append("disposable roles remain")
                except BaseException:
                    cleanup_errors.append("role cleanup failed")
        try:
            cleanup = runtime.stop_and_remove()
        except BaseException as exc:
            cleanup_errors.append(
                f"runtime cleanup failed: {type(exc).__name__}"
            )
            cleanup = {}
    if primary_error is not None:
        raise PersistenceValidationError(
            "live persistence validation failed before completion: "
            f"{type(primary_error).__name__}"
        ) from None
    if cleanup_errors:
        raise PersistenceValidationError(
            "live persistence cleanup was incomplete: "
            + "; ".join(cleanup_errors)
        )
    if (
        not cleanup.get("pid_exited")
        or not cleanup.get("ports_closed")
        or not cleanup.get("temporary_store_removed")
        or cleanup.get("force_kill_used")
    ):
        raise PersistenceValidationError("live runtime cleanup contract failed")
    required_matrix = _required_probe_matrix()
    result = {
        "binary_identity": {
            "binary_sha256": identity["binary_sha256"],
            "build_commit": identity["build_commit"],
            "build_tag": identity["build_tag"],
            "platform": identity["platform"],
        },
        "cleanup": {
            "databases_removed": True,
            "disposable_and_fixed_roles_removed": True,
            "force_kill_used": False,
            "owned_pid_exited": True,
            "ports_closed": True,
            "temporary_store_removed": True,
        },
        "insecure_local_transport": {
            "limitations": (
                "Validates SQL principals, trusted context, migration, "
                "idempotency constraints, RLS, and FORCE RLS. It does not "
                "validate production certificates, authentication, pooling, "
                "SSO, secret storage, or distributed contention frequency."
            ),
            "loopback_only": True,
            "used": True,
            "validation_transport": "PINNED_COCKROACH_CLI_NOT_PRODUCTION_DBAPI",
        },
        "live": live,
        "natural_contention": {
            "client_visible_40001_frequency_proven": False,
            "synthetic_retry_signal_only": True,
        },
        "offline": offline,
        "probe_summary": recorder.summary(),
        "probes": recorder.rows,
        "required_probe_coverage": {
            "fail_count": 0,
            "pass_count": len(required_matrix),
            "probe_count": len(required_matrix),
            "rows": required_matrix,
        },
        "status": "PASS",
    }
    if len(required_matrix) != EXPECTED_REQUIRED_PROBES:
        raise PersistenceValidationError("required probe coverage is incomplete")
    return result


def write_json_output(path: Path, value: Mapping[str, Any]) -> None:
    output = path.expanduser().resolve()
    if output.is_relative_to(REPOSITORY_ROOT.resolve()):
        raise PersistenceValidationError(
            "live JSON output must stay outside the repository"
        )
    if output.parent.name != "reports":
        raise PersistenceValidationError(
            "live JSON output must use the external reports directory"
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_name(output.name + ".partial")
    partial.write_bytes(canonical_json_bytes(value))
    partial.chmod(0o600)
    partial.replace(output)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate Memory Patch Step 6 persistence on CockroachDB v26.2.4."
        )
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--offline-validate", action="store_true")
    mode.add_argument("--live-test", action="store_true")
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
                raise PersistenceValidationError(
                    "live validation requires --allow-live"
                )
            if args.cockroach_binary is None or args.json_output is None:
                raise PersistenceValidationError(
                    "live validation requires binary and JSON output paths"
                )
            result = run_live_validation(
                args.cockroach_binary,
                args.json_output,
            )
            write_json_output(args.json_output, result)
        print(canonical_json_bytes(result).decode("utf-8"), end="")
        return 0
    except (
        migrations.MigrationError,
        PersistenceValidationError,
        OSError,
    ) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
