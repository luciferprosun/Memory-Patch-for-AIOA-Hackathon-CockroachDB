#!/usr/bin/env python3
"""Validate and apply the Memory Patch CockroachDB Step 4–5 migrations.

Ordinary repository tests import this module without opening a socket or
starting a process. Live actions require ``--allow-live`` and an exact pinned
CockroachDB v26.2.4 binary.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_ROOT = REPOSITORY_ROOT / "sql" / "cockroachdb" / "migrations"
MIGRATION_MANIFEST_PATH = MIGRATION_ROOT / "manifest.json"
SCHEMA_MANIFEST_PATH = (
    REPOSITORY_ROOT / "config" / "cockroachdb" / "schema-foundation-1a.json"
)
SECURITY_MANIFEST_PATH = (
    REPOSITORY_ROOT / "config" / "cockroachdb" / "rls-security-1a.json"
)
VERSION_PIN_PATH = (
    REPOSITORY_ROOT / "config" / "cockroachdb" / "version-pin.json"
)
RUNNER_VERSION = "2.1.0"
PINNED_VERSION = "v26.2.4"
PINNED_CLUSTER_VERSION = "26.2"
DEFAULT_TIMEOUT_SECONDS = 60.0
START_TIMEOUT_SECONDS = 45.0
DISPOSABLE_DATABASE_PREFIX = "mp_step5_"
MIGRATION_ID_PATTERN = re.compile(r"^\d{4}_[a-z0-9_]+$")
SAFE_IDENTIFIER_PATTERN = re.compile(r"^[a-z][a-z0-9_]{2,62}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")

STEP4_MIGRATION_HASHES = {
    "0001_step4_identity_and_hat_scopes": (
        "a7d6e835d16debc77830cbcb2803c3b01400622c3186a64f7d627f4bf0a767a0"
    ),
    "0002_step4_knowledge_lineage_and_retrieval": (
        "a8ddf1d342e58c8e12ecb082443b6367921fa5ec0e6edce92401f560300058d4"
    ),
    "0003_step4_kernel_memory_and_audit_evidence": (
        "f25865e958d9bb352b8fc03512474fb848a688cfa6c73a5790664745779de881"
    ),
}
STEP5_MIGRATION_ID = "0004_step5_tenant_roles_session_context_rls"
STEP5_CLUSTER_ROLE_BEGIN = "-- STEP5_CLUSTER_ROLE_DDL_BEGIN"
STEP5_CLUSTER_ROLE_END = "-- STEP5_CLUSTER_ROLE_DDL_END"
STEP5_DATABASE_PHASE_MARKERS = tuple(
    (
        f"-- STEP5_DATABASE_PHASE_{phase}_BEGIN",
        f"-- STEP5_DATABASE_PHASE_{phase}_END",
    )
    for phase in range(1, 10)
)

STEP4_FORBIDDEN_MIGRATION_PATTERNS: tuple[
    tuple[str, re.Pattern[str]], ...
] = (
    ("SQL roles", re.compile(r"\bCREATE\s+ROLE\b", re.IGNORECASE)),
    ("SQL grants", re.compile(r"\bGRANT\b", re.IGNORECASE)),
    ("RLS policy", re.compile(r"\bCREATE\s+POLICY\b", re.IGNORECASE)),
    (
        "RLS enablement",
        re.compile(r"\bENABLE\s+ROW\s+LEVEL\s+SECURITY\b", re.IGNORECASE),
    ),
    (
        "FORCE RLS",
        re.compile(r"\bFORCE\s+ROW\s+LEVEL\s+SECURITY\b", re.IGNORECASE),
    ),
    ("BYPASSRLS", re.compile(r"\bBYPASSRLS\b", re.IGNORECASE)),
    ("database trigger", re.compile(r"\bCREATE\s+TRIGGER\b", re.IGNORECASE)),
    ("cascade deletion", re.compile(r"\bON\s+DELETE\s+CASCADE\b", re.IGNORECASE)),
    (
        "authority-bearing default",
        re.compile(
            r"\bDEFAULT\s+'?(?:APPROVED|COMMITTED|ACTIVE|HUMAN|TRUSTED|CANONICAL)'?\b",
            re.IGNORECASE,
        ),
    ),
    (
        "domain-specific kernel rule",
        re.compile(
            r"\b(?:Nachweisgesetz|German\s+law|German-law|employment\s+law)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "machine-specific path",
        re.compile(r"(?:/home/|/Users/|[A-Za-z]:\\\\)"),
    ),
)

STEP5_FORBIDDEN_MIGRATION_PATTERNS: tuple[
    tuple[str, re.Pattern[str]], ...
] = (
    (
        "positive BYPASSRLS grant",
        re.compile(r"(?<!NO)\bBYPASSRLS\b", re.IGNORECASE),
    ),
    (
        "login-enabled production role",
        re.compile(
            r"\b(?:CREATE|ALTER)\s+ROLE\b[^;]*?(?<!NO)\bLOGIN\b",
            re.IGNORECASE,
        ),
    ),
    (
        "cascade deletion",
        re.compile(r"\bON\s+DELETE\s+CASCADE\b", re.IGNORECASE),
    ),
    (
        "authority-bearing default",
        re.compile(
            r"\bDEFAULT\s+'?(?:APPROVED|COMMITTED|ACTIVE|HUMAN|TRUSTED|CANONICAL)'?\b",
            re.IGNORECASE,
        ),
    ),
    (
        "domain-specific kernel rule",
        re.compile(
            r"\b(?:Nachweisgesetz|German\s+law|German-law|employment\s+law)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "machine-specific path",
        re.compile(r"(?:/home/|/Users/|[A-Za-z]:\\\\)"),
    ),
)

SECRET_PATTERN = re.compile(
    r"(?i)(?:api[_-]?key|client[_-]?secret|password|passwd|bearer\s+|"
    r"authorization:|BEGIN\s+.*PRIVATE\s+KEY|sk-[A-Za-z0-9]|"
    r"postgres(?:ql)?://|cockroachdb://)"
)


class MigrationError(RuntimeError):
    """Deterministic migration, safety, or live-validation failure."""


class SqlError(MigrationError):
    """A CockroachDB CLI command failed."""

    def __init__(self, message: str, *, sqlstate: str | None = None) -> None:
        super().__init__(message)
        self.sqlstate = sqlstate


@dataclass(frozen=True)
class Migration:
    migration_id: str
    filename: str
    sha256: str
    path: Path
    sql: str


@dataclass(frozen=True)
class ProcessResult:
    returncode: int
    stdout: str
    stderr: str


@dataclass
class SqlClient:
    binary: Path
    port: int
    host: str = "127.0.0.1"

    def _command(self, database: str, sql: str) -> tuple[list[str], dict[str, str]]:
        validate_database_identifier(database)
        require_loopback(self.host)
        environment = os.environ.copy()
        for variable in (
            "COCKROACH_URL",
            "COCKROACH_SQL_URL",
            "DATABASE_URL",
            "PGDATABASE",
            "PGHOST",
            "PGPASSWORD",
            "PGPORT",
            "PGSERVICE",
            "PGSERVICEFILE",
            "PGUSER",
        ):
            environment.pop(variable, None)
        return (
            [
                str(self.binary),
                "sql",
                "--format=tsv",
                "--set=errexit=true",
                "--insecure",
                f"--host={self.host}",
                f"--port={self.port}",
                f"--database={database}",
                f"--execute={sql}",
            ],
            environment,
        )

    def execute(
        self,
        database: str,
        sql: str,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> str:
        validate_timeout(timeout)
        command, environment = self._command(database, sql)
        result = run_process(command, timeout=timeout, environment=environment)
        if result.returncode != 0:
            raise SqlError(
                sanitize_error(result.stderr),
                sqlstate=extract_sqlstate(result.stderr),
            )
        return result.stdout

    def expect_error(
        self,
        database: str,
        sql: str,
        *,
        expected_sqlstate: str | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> str | None:
        validate_timeout(timeout)
        command, environment = self._command(database, sql)
        result = run_process(command, timeout=timeout, environment=environment)
        if result.returncode == 0:
            raise MigrationError("negative SQL probe unexpectedly succeeded")
        state = extract_sqlstate(result.stderr)
        if expected_sqlstate is not None and state != expected_sqlstate:
            raise MigrationError(
                f"negative SQL probe expected {expected_sqlstate}, got {state}"
            )
        return state


@dataclass
class LocalRuntime:
    binary: Path
    run_id: str
    runtime_parent: Path = Path("/tmp")
    runtime_dir: Path | None = None
    process: subprocess.Popen[str] | None = None
    log_handle: Any = None
    sql_port: int | None = None
    rpc_port: int | None = None
    http_port: int | None = None
    started_at: float | None = None
    force_kill_used: bool = False

    def start(self) -> SqlClient:
        if self.process is not None:
            raise MigrationError("runtime is already started")
        verify_binary_identity(self.binary)
        runtime_parent = self.runtime_parent.expanduser().resolve()
        assert_safe_runtime_parent(runtime_parent)
        self.runtime_dir = Path(
            tempfile.mkdtemp(prefix=f"{self.run_id}_", dir=runtime_parent)
        )
        assert_owned_runtime_path(self.runtime_dir, runtime_parent)
        temp_dir = self.runtime_dir / "temp"
        temp_dir.mkdir(mode=0o700)
        pid_file = self.runtime_dir / "server.pid"
        url_file = self.runtime_dir / "listening-url"
        log_path = self.runtime_dir / "server.log"
        self.rpc_port, self.sql_port, self.http_port = allocate_ports(3)
        command = [
            str(self.binary),
            "start-single-node",
            "--insecure",
            f"--listen-addr=127.0.0.1:{self.rpc_port}",
            f"--sql-addr=127.0.0.1:{self.sql_port}",
            f"--http-addr=127.0.0.1:{self.http_port}",
            "--store=type=mem,size=640MiB",
            "--cache=64MiB",
            "--max-sql-memory=128MiB",
            "--external-io-disabled",
            f"--temp-dir={temp_dir}",
            f"--pid-file={pid_file}",
            f"--listening-url-file={url_file}",
            "--logtostderr=WARNING",
        ]
        self.log_handle = log_path.open("w", encoding="utf-8")
        self.started_at = time.monotonic()
        self.process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=self.log_handle,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=self.runtime_dir,
        )
        client = SqlClient(binary=self.binary, port=self.sql_port)
        deadline = time.monotonic() + START_TIMEOUT_SECONDS
        last_error = ""
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise MigrationError(
                    f"CockroachDB exited during startup with "
                    f"code {self.process.returncode}"
                )
            try:
                if one_value(client.execute("defaultdb", "SELECT 1", timeout=5)) == "1":
                    break
            except MigrationError as exc:
                last_error = str(exc)
                time.sleep(0.5)
        else:
            raise MigrationError(f"runtime readiness timed out: {last_error}")
        if not pid_file.is_file():
            raise MigrationError("owned runtime did not create its PID file")
        if int(pid_file.read_text(encoding="utf-8").strip()) != self.process.pid:
            raise MigrationError("runtime PID file differs from owned child PID")
        return client

    def stop_and_remove(self) -> dict[str, Any]:
        errors: list[str] = []
        pid = self.process.pid if self.process is not None else None
        if self.process is not None and self.process.poll() is None:
            self.process.send_signal(signal.SIGTERM)
            try:
                self.process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.force_kill_used = True
                self.process.kill()
                try:
                    self.process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    errors.append("owned CockroachDB PID did not exit")
        if self.log_handle is not None:
            self.log_handle.close()
            self.log_handle = None
        ports_closed = all(
            port is None or not can_connect("127.0.0.1", port)
            for port in (self.rpc_port, self.sql_port, self.http_port)
        )
        if not ports_closed:
            errors.append("an owned loopback port remains open")
        path_removed = self.runtime_dir is None
        if self.runtime_dir is not None:
            if self.runtime_dir.exists():
                assert_owned_runtime_path(
                    self.runtime_dir,
                    self.runtime_parent.expanduser().resolve(),
                )
                shutil.rmtree(self.runtime_dir)
            path_removed = not self.runtime_dir.exists()
        if not path_removed:
            errors.append("owned temporary runtime directory remains")
        duration = (
            round(time.monotonic() - self.started_at, 3)
            if self.started_at is not None
            else None
        )
        return {
            "cleanup_errors": errors,
            "force_kill_used": self.force_kill_used,
            "pid_exited": self.process is None or self.process.poll() is not None,
            "ports_closed": ports_closed,
            "runtime_duration_seconds": duration,
            "temporary_store_removed": path_removed,
            "owned_pid_recorded": pid is not None,
        }


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MigrationError(f"invalid JSON artifact: {path}") from exc
    if not isinstance(value, dict):
        raise MigrationError(f"JSON artifact must be an object: {path}")
    if path.read_bytes() != canonical_json_bytes(value):
        raise MigrationError(f"JSON artifact is not canonical: {path}")
    return value


def load_version_pin() -> dict[str, Any]:
    pin = load_json(VERSION_PIN_PATH)
    if pin.get("exact_version") != PINNED_VERSION:
        raise MigrationError("Step 4 requires the immutable v26.2.4 version pin")
    if pin.get("target_series") != "v26.2":
        raise MigrationError("version pin target series is not v26.2")
    runtime = pin.get("runtime")
    if not isinstance(runtime, dict) or runtime.get("cluster_version") != "26.2":
        raise MigrationError("version pin cluster version is not 26.2")
    artifact = pin.get("artifact")
    if not isinstance(artifact, dict):
        raise MigrationError("version pin artifact object is missing")
    if not SHA256_PATTERN.fullmatch(str(artifact.get("binary_sha256", ""))):
        raise MigrationError("version pin binary SHA-256 is invalid")
    return pin


def validate_migration_sql(migration_id: str, sql: str) -> None:
    if "\r" in sql or not sql.endswith("\n"):
        raise MigrationError(f"{migration_id} must use LF and a final newline")
    step5_phase_terminated = (
        migration_id == STEP5_MIGRATION_ID
        and sql.strip().endswith(STEP5_DATABASE_PHASE_MARKERS[-1][1])
    )
    if not sql.strip().endswith(";") and not step5_phase_terminated:
        raise MigrationError(f"{migration_id} must end with a SQL semicolon")
    if SECRET_PATTERN.search(sql):
        raise MigrationError(f"{migration_id} contains secret-shaped content")
    if migration_id in STEP4_MIGRATION_HASHES:
        forbidden_patterns = STEP4_FORBIDDEN_MIGRATION_PATTERNS
    elif migration_id == STEP5_MIGRATION_ID:
        forbidden_patterns = STEP5_FORBIDDEN_MIGRATION_PATTERNS
        required_fragments = (
            "CREATE ROLE IF NOT EXISTS mp_schema_owner",
            "CREATE TABLE IF NOT EXISTS memory_patch.request_contexts",
            "CREATE OR REPLACE FUNCTION memory_patch.set_request_context",
            "SECURITY DEFINER",
            "ENABLE ROW LEVEL SECURITY",
            "FORCE ROW LEVEL SECURITY",
            "CREATE POLICY IF NOT EXISTS",
            "TO mp_app_runtime",
        )
        for fragment in required_fragments:
            if fragment not in sql:
                raise MigrationError(
                    f"{migration_id} lacks required security fragment: {fragment}"
                )
    else:
        raise MigrationError(f"unrecognized migration security generation: {migration_id}")
    for description, pattern in forbidden_patterns:
        if pattern.search(sql):
            raise MigrationError(f"{migration_id} contains forbidden {description}")


def load_migrations() -> list[Migration]:
    manifest = load_json(MIGRATION_MANIFEST_PATH)
    if manifest.get("schema_version") != 2:
        raise MigrationError("migration manifest schema_version must be 2")
    if manifest.get("runner_version") != RUNNER_VERSION:
        raise MigrationError("migration manifest runner_version mismatch")
    if manifest.get("target_database") != "CockroachDB":
        raise MigrationError("migration manifest must target CockroachDB")
    if manifest.get("target_version") != PINNED_VERSION:
        raise MigrationError("migration manifest target version mismatch")
    if manifest.get("transaction_policy") != (
        "STEP4_ONE_TRANSACTION_STEP5_NINE_IDEMPOTENT_DATABASE_PHASES_"
        "WITH_NONATOMIC_CLUSTER_ROLE_DDL"
    ):
        raise MigrationError("unsupported migration transaction policy")
    if manifest.get("cluster_role_policy") != (
        "IDEMPOTENT_CLUSTER_SCOPED_ROLE_DDL_MAY_OUTLIVE_A_FAILED_"
        "DATABASE_MIGRATION"
    ):
        raise MigrationError("cluster role execution policy is not explicit")
    raw_migrations = manifest.get("migrations")
    if not isinstance(raw_migrations, list) or not raw_migrations:
        raise MigrationError("migration manifest has no migrations")
    migrations: list[Migration] = []
    seen: set[str] = set()
    for raw in raw_migrations:
        if not isinstance(raw, dict) or set(raw) != {
            "filename",
            "migration_id",
            "sha256",
        }:
            raise MigrationError("migration entry has an invalid shape")
        migration_id = raw["migration_id"]
        filename = raw["filename"]
        checksum = raw["sha256"]
        if not isinstance(migration_id, str) or not MIGRATION_ID_PATTERN.fullmatch(
            migration_id
        ):
            raise MigrationError("migration_id is not canonical")
        if migration_id in seen:
            raise MigrationError(f"duplicate migration_id: {migration_id}")
        if filename != f"{migration_id}.sql":
            raise MigrationError(f"migration filename does not match {migration_id}")
        if not isinstance(checksum, str) or not SHA256_PATTERN.fullmatch(checksum):
            raise MigrationError(f"invalid checksum for {migration_id}")
        path = MIGRATION_ROOT / filename
        if path.parent != MIGRATION_ROOT or not path.is_file() or path.is_symlink():
            raise MigrationError(f"missing regular migration file: {filename}")
        sql = path.read_text(encoding="utf-8")
        validate_migration_sql(migration_id, sql)
        if file_sha256(path) != checksum:
            raise MigrationError(f"checksum mismatch for {migration_id}")
        immutable_step4_checksum = STEP4_MIGRATION_HASHES.get(migration_id)
        if (
            immutable_step4_checksum is not None
            and checksum != immutable_step4_checksum
        ):
            raise MigrationError(f"immutable Step 4 checksum changed: {migration_id}")
        migrations.append(Migration(migration_id, filename, checksum, path, sql))
        seen.add(migration_id)
    identifiers = [migration.migration_id for migration in migrations]
    if identifiers != sorted(identifiers):
        raise MigrationError("migrations are not in deterministic identifier order")
    discovered = {path.name for path in MIGRATION_ROOT.glob("*.sql")}
    declared = {migration.filename for migration in migrations}
    if discovered != declared:
        raise MigrationError("migration manifest and SQL directory differ")
    expected_identifiers = [*STEP4_MIGRATION_HASHES, STEP5_MIGRATION_ID]
    if identifiers != expected_identifiers:
        raise MigrationError("migration chain is not the exact Step 4 -> Step 5 chain")
    return migrations


def load_schema_manifest() -> dict[str, Any]:
    manifest = load_json(SCHEMA_MANIFEST_PATH)
    if manifest.get("schema_version") != 1:
        raise MigrationError("schema manifest schema_version must be 1")
    if manifest.get("target_cockroachdb_version") != PINNED_VERSION:
        raise MigrationError("schema manifest version pin mismatch")
    tables = manifest.get("required_tables")
    indexes = manifest.get("explicit_indexes")
    if (
        not isinstance(tables, list)
        or tables != sorted(tables)
        or len(tables) != len(set(tables))
    ):
        raise MigrationError("schema manifest table list is not canonical")
    if (
        not isinstance(indexes, list)
        or indexes != sorted(indexes)
        or len(indexes) != len(set(indexes))
    ):
        raise MigrationError("schema manifest index list is not canonical")
    return manifest


def load_security_manifest() -> dict[str, Any]:
    manifest = load_json(SECURITY_MANIFEST_PATH)
    if manifest.get("schema_version") != 1:
        raise MigrationError("security manifest schema_version must be 1")
    if manifest.get("manifest_id") != "memory-patch-step5-rls-security-1a":
        raise MigrationError("security manifest identity mismatch")
    schema_manifest = load_schema_manifest()
    raw_tables = manifest.get("tables")
    if not isinstance(raw_tables, list):
        raise MigrationError("security manifest tables must be a list")
    names = [row.get("table") for row in raw_tables if isinstance(row, dict)]
    if names != schema_manifest["required_tables"]:
        raise MigrationError(
            "security manifest must classify every Step 4 table exactly once"
        )
    if manifest.get("step4_table_count") != len(names):
        raise MigrationError("security manifest Step 4 table count mismatch")
    required_fields = {
        "access_class",
        "append_model",
        "force_rls",
        "normal_runtime_access_required",
        "operations",
        "owner_role",
        "policies",
        "reason_for_rls_exception",
        "rls_enabled",
        "runtime_grants",
        "table",
        "tenant_column",
        "user_owner_column",
    }
    protected: list[dict[str, Any]] = []
    for row in raw_tables:
        if not isinstance(row, dict) or not required_fields.issubset(row):
            raise MigrationError("security table classification has invalid shape")
        operations = row["operations"]
        policies = row["policies"]
        if (
            not isinstance(operations, dict)
            or set(operations) != {"delete", "insert", "select", "update"}
            or not isinstance(policies, dict)
            or set(policies) != {"delete", "insert", "select", "update"}
        ):
            raise MigrationError(
                f"security operation matrix is incomplete: {row['table']}"
            )
        if not isinstance(row["runtime_grants"], list):
            raise MigrationError(f"runtime grant list is invalid: {row['table']}")
        if row["rls_enabled"]:
            protected.append(row)
            if row["force_rls"] is not True:
                raise MigrationError(f"FORCE RLS missing: {row['table']}")
            if row["tenant_column"] != "tenant_id":
                raise MigrationError(
                    f"protected table lacks authoritative tenant_id: {row['table']}"
                )
            if row["owner_role"] == "mp_app_runtime":
                raise MigrationError(f"runtime owns protected table: {row['table']}")
            if row["reason_for_rls_exception"] is not None:
                raise MigrationError(
                    f"protected table has spurious RLS exception: {row['table']}"
                )
        else:
            if not row["reason_for_rls_exception"]:
                raise MigrationError(
                    f"unprotected table lacks explicit reason: {row['table']}"
                )
        if row["access_class"] in {
            "HAT_SCOPE_APPEND_ORIENTED",
            "HAT_SCOPE_MIXED",
            "TENANT_OR_USER_APPEND_ORIENTED",
            "USER_PRIVATE",
            "USER_PRIVATE_APPEND_ORIENTED",
        }:
            if (
                row["user_owner_column"] is None
                and not row.get("user_owner_resolution")
            ):
                raise MigrationError(
                    f"private/mixed table lacks owner dimension: {row['table']}"
                )
    if len(protected) != manifest.get("protected_table_count"):
        raise MigrationError("security manifest protected table count mismatch")
    if {row["table"] for row in raw_tables if not row["rls_enabled"]} != {
        "hat_manifests",
        "schema_migrations",
    }:
        raise MigrationError("RLS exception set is not exact")
    security_tables = manifest.get("security_internal_tables")
    if (
        not isinstance(security_tables, list)
        or len(security_tables) != 1
        or security_tables[0].get("table") != "request_contexts"
        or security_tables[0].get("access_class") != "SECURITY_INTERNAL"
        or security_tables[0].get("rls_enabled") is not False
        or security_tables[0].get("runtime_grants") != []
        or not security_tables[0].get("reason_for_rls_exception")
    ):
        raise MigrationError("security-internal context table classification is invalid")
    identity_guards = manifest.get("scope_identity_guards")
    if (
        not isinstance(identity_guards, list)
        or {
            (guard.get("table"), guard.get("trigger"), guard.get("function"))
            for guard in identity_guards
            if isinstance(guard, dict)
        }
        != {
            (
                "memory_items",
                "memory_items_s5_identity_guard",
                "guard_memory_item_identity",
            ),
            (
                "personal_memory_spaces",
                "personal_memory_spaces_s5_identity_guard",
                "guard_personal_memory_space_identity",
            ),
        }
        or any(
            not isinstance(guard.get("immutable_columns"), list)
            or guard["immutable_columns"] != sorted(guard["immutable_columns"])
            or not guard["immutable_columns"]
            for guard in identity_guards
        )
    ):
        raise MigrationError("scope-identity guard classification is invalid")
    roles = manifest.get("roles")
    expected_roles = {
        "mp_app_runtime",
        "mp_request_context_setter",
        "mp_schema_owner",
        "mp_security_owner",
    }
    if (
        not isinstance(roles, list)
        or {row.get("role") for row in roles if isinstance(row, dict)}
        != expected_roles
    ):
        raise MigrationError("security role matrix is incomplete")
    for role in roles:
        if (
            role.get("login") is not False
            or role.get("bypassrls") is not False
            or role.get("createrole") is not False
            or role.get("createdb") is not False
            or role.get("member_of") != []
        ):
            raise MigrationError(f"unsafe fixed role options: {role.get('role')}")
    return manifest


def offline_validate() -> dict[str, Any]:
    pin = load_version_pin()
    migrations = load_migrations()
    schema_manifest = load_schema_manifest()
    security_manifest = load_security_manifest()
    step4_sql = "\n".join(
        migration.sql
        for migration in migrations
        if migration.migration_id in STEP4_MIGRATION_HASHES
    )
    step5_sql = next(
        migration.sql
        for migration in migrations
        if migration.migration_id == STEP5_MIGRATION_ID
    )
    all_sql = step4_sql + "\n" + step5_sql
    step4_tables = sorted(
        re.findall(
            r"^CREATE TABLE memory_patch\.([a-z0-9_]+)",
            step4_sql,
            re.MULTILINE,
        )
    )
    if step4_tables != schema_manifest["required_tables"]:
        raise MigrationError("schema manifest tables differ from migration SQL")
    security_tables = sorted(
        re.findall(
            r"^CREATE TABLE IF NOT EXISTS memory_patch\.([a-z0-9_]+)",
            step5_sql,
            re.MULTILINE,
        )
    )
    expected_security_tables = sorted(
        row["table"] for row in security_manifest["security_internal_tables"]
    )
    if security_tables != expected_security_tables:
        raise MigrationError("security-internal tables differ from migration SQL")
    created_tables = sorted([*step4_tables, *security_tables])
    created_indexes = sorted(
        re.findall(
            r"^CREATE (?:UNIQUE |INVERTED )?INDEX ([a-z0-9_]+)",
            all_sql,
            re.MULTILINE,
        )
    )
    if created_indexes != schema_manifest["explicit_indexes"]:
        raise MigrationError("schema manifest indexes differ from migration SQL")
    required_entities = {
        "tenants",
        "users",
        "hat_manifests",
        "hat_scopes",
        "personal_memory_spaces",
        "knowledge_sources",
        "source_snapshots",
        "knowledge_versions",
        "knowledge_chunks",
        "kernel_runs",
        "routing_decisions",
        "schema_migrations",
    }
    if not required_entities.issubset(created_tables):
        raise MigrationError("required Step 4 entity coverage is incomplete")
    protected = [
        row for row in security_manifest["tables"] if row["rls_enabled"]
    ]
    enabled_tables = set(
        re.findall(
            r"ALTER TABLE memory_patch\.([a-z0-9_]+)\s+"
            r"ENABLE ROW LEVEL SECURITY;",
            step5_sql,
        )
    )
    forced_tables = set(
        re.findall(
            r"ALTER TABLE memory_patch\.([a-z0-9_]+)\s+"
            r"FORCE ROW LEVEL SECURITY;",
            step5_sql,
        )
    )
    expected_protected = {row["table"] for row in protected}
    if enabled_tables != expected_protected:
        raise MigrationError("table-level RLS enablement coverage is incomplete")
    if forced_tables != expected_protected:
        raise MigrationError("table-level FORCE RLS coverage is incomplete")
    if re.search(r"\bTO\s+PUBLIC\b", step5_sql, re.IGNORECASE):
        raise MigrationError("Step 5 policy or grant targets PUBLIC")
    if re.search(
        r"\bUSING\s*\(\s*(?:true|1\s*=\s*1)\s*\)",
        step5_sql,
        re.IGNORECASE,
    ):
        raise MigrationError("Step 5 contains an allow-all USING policy")
    guard_triggers = set(
        re.findall(
            r"^CREATE TRIGGER ([a-z0-9_]+)",
            step5_sql,
            re.MULTILINE,
        )
    )
    if guard_triggers != {
        "memory_items_s5_identity_guard",
        "personal_memory_spaces_s5_identity_guard",
    }:
        raise MigrationError("Step 5 identity-guard trigger set is not exact")
    for row in protected:
        for command, policy_name in row["policies"].items():
            operation = command.upper()
            if policy_name is None:
                continue
            pattern = re.compile(
                rf"CREATE POLICY IF NOT EXISTS {re.escape(policy_name)}\s+"
                rf"ON memory_patch\.{re.escape(row['table'])}\s+"
                rf"FOR {operation}\s+TO mp_app_runtime",
                re.MULTILINE,
            )
            if pattern.search(step5_sql) is None:
                raise MigrationError(
                    f"declared policy missing from migration: {policy_name}"
                )
    return {
        "migration_count": len(migrations),
        "migration_ids": [migration.migration_id for migration in migrations],
        "schema_table_count": len(created_tables),
        "security_internal_table_count": len(security_tables),
        "step4_table_count": len(step4_tables),
        "protected_table_count": len(protected),
        "identity_guard_trigger_count": len(guard_triggers),
        "explicit_index_count": len(created_indexes),
        "status": "PASS",
        "target_version": pin["exact_version"],
        "vector_boundary": "DEFERRED_NO_CANONICAL_DIMENSION",
    }


def validate_timeout(timeout: float) -> None:
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise MigrationError("timeout must be numeric")
    if timeout <= 0 or timeout > 180:
        raise MigrationError("timeout must be bounded to 1..180 seconds")


def validate_database_identifier(database: str) -> None:
    if not SAFE_IDENTIFIER_PATTERN.fullmatch(database):
        raise MigrationError("database name must be a safe lowercase SQL identifier")


def assert_disposable_database(database: str) -> None:
    validate_database_identifier(database)
    if not database.startswith(DISPOSABLE_DATABASE_PREFIX):
        raise MigrationError("destructive cleanup requires an mp_step5_ database")


def require_loopback(host: str) -> None:
    if host != "127.0.0.1":
        raise MigrationError("insecure migration runtime must bind to 127.0.0.1")


def assert_safe_runtime_parent(path: Path) -> None:
    resolved = path.resolve()
    if (
        not resolved.is_dir()
        or resolved.is_symlink()
        or not os.access(resolved, os.R_OK | os.W_OK | os.X_OK)
    ):
        raise MigrationError("runtime parent must be a writable real directory")
    if resolved in {
        Path("/"),
        Path("/home"),
        Path("/home/l"),
        REPOSITORY_ROOT.resolve(),
    }:
        raise MigrationError("runtime parent is dangerously broad")
    if resolved != Path("/tmp") and (
        resolved.name != "temporary" or resolved.parent.name != "cache"
    ):
        raise MigrationError(
            "external runtime parent must be an approved cache/temporary directory"
        )
    if resolved.is_relative_to(REPOSITORY_ROOT.resolve()):
        raise MigrationError("runtime parent must remain outside the repository")


def assert_owned_runtime_path(
    path: Path,
    parent: Path = Path("/tmp"),
) -> None:
    resolved = path.resolve()
    runtime_parent = parent.resolve()
    assert_safe_runtime_parent(runtime_parent)
    if resolved.parent != runtime_parent or not resolved.name.startswith(
        DISPOSABLE_DATABASE_PREFIX
    ):
        raise MigrationError("refusing cleanup of an unowned runtime path")


def allocate_ports(count: int) -> list[int]:
    holders: list[socket.socket] = []
    ports: list[int] = []
    try:
        try:
            for _ in range(count):
                holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                holder.bind(("127.0.0.1", 0))
                holders.append(holder)
                ports.append(holder.getsockname()[1])
        except OSError as exc:
            raise MigrationError(
                "loopback port allocation is unavailable"
            ) from exc
    finally:
        for holder in holders:
            holder.close()
    if len(ports) != count or len(set(ports)) != count:
        raise MigrationError("dynamic loopback port allocation failed")
    return ports


def can_connect(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.5)
        return probe.connect_ex((host, port)) == 0


def run_process(
    command: Sequence[str],
    *,
    timeout: float,
    environment: Mapping[str, str] | None = None,
) -> ProcessResult:
    validate_timeout(timeout)
    try:
        completed = subprocess.run(
            list(command),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            env=dict(environment) if environment is not None else None,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise MigrationError(f"subprocess exceeded {timeout} seconds") from exc
    return ProcessResult(completed.returncode, completed.stdout, completed.stderr)


def extract_sqlstate(stderr: str) -> str | None:
    match = re.search(r"SQLSTATE:\s*([0-9A-Z]{5})", stderr)
    return match.group(1) if match else None


def sanitize_error(stderr: str) -> str:
    messages = []
    for line in stderr.splitlines():
        if line.startswith("ERROR:") or "SQLSTATE:" in line:
            messages.append(line.strip())
    sanitized = " | ".join(messages)[:1000] or "CockroachDB command failed"
    return re.sub(r"(?i)(?:postgres(?:ql)?|cockroachdb)://\S+", "[REDACTED_DSN]", sanitized)


def verify_binary_identity(binary: Path) -> dict[str, str]:
    pin = load_version_pin()
    if not binary.is_file() or binary.is_symlink():
        raise MigrationError("CockroachDB binary must be a regular non-symlink file")
    result = run_process([str(binary), "version"], timeout=15)
    if result.returncode != 0:
        raise MigrationError("CockroachDB version command failed")
    match = re.search(r"^Build Tag:\s+(v\d+\.\d+\.\d+)$", result.stdout, re.MULTILINE)
    if not match or match.group(1) != pin["exact_version"]:
        raise MigrationError(
            f"binary version mismatch: expected {pin['exact_version']}, "
            f"found {match.group(1) if match else 'unknown'}"
        )
    digest = file_sha256(binary)
    expected = pin["artifact"]["binary_sha256"]
    if digest != expected:
        raise MigrationError("CockroachDB binary SHA-256 differs from version pin")
    return {"binary_sha256": digest, "build_tag": match.group(1)}


def parse_tsv(output: str) -> list[dict[str, str]]:
    if not output.strip():
        return []
    return list(csv.DictReader(io.StringIO(output.strip()), delimiter="\t"))


def one_value(output: str) -> str:
    rows = parse_tsv(output)
    if len(rows) != 1 or len(rows[0]) != 1:
        raise MigrationError("expected exactly one SQL value")
    return next(iter(rows[0].values()))


def sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def migration_table_exists(client: SqlClient, database: str) -> bool:
    result = client.execute(
        database,
        "SELECT count(*) FROM information_schema.tables "
        "WHERE table_schema = 'memory_patch' "
        "AND table_name = 'schema_migrations'",
    )
    return one_value(result) == "1"


def applied_migrations(client: SqlClient, database: str) -> dict[str, str]:
    if not migration_table_exists(client, database):
        return {}
    rows = parse_tsv(
        client.execute(
            database,
            "SELECT migration_id, checksum_sha256 "
            "FROM memory_patch.schema_migrations ORDER BY migration_id",
        )
    )
    return {row["migration_id"]: row["checksum_sha256"] for row in rows}


def split_step5_cluster_role_ddl(sql: str) -> tuple[str, str]:
    if sql.count(STEP5_CLUSTER_ROLE_BEGIN) != 1:
        raise MigrationError("Step 5 cluster-role begin marker is not exact")
    if sql.count(STEP5_CLUSTER_ROLE_END) != 1:
        raise MigrationError("Step 5 cluster-role end marker is not exact")
    before, marked = sql.split(STEP5_CLUSTER_ROLE_BEGIN, 1)
    cluster_sql, after = marked.split(STEP5_CLUSTER_ROLE_END, 1)
    if re.search(
        r"\b(?:CREATE|ALTER|DROP)\s+(?:TABLE|SCHEMA|FUNCTION|POLICY)\b",
        cluster_sql,
        re.IGNORECASE,
    ):
        raise MigrationError("cluster-role section contains database object DDL")
    if "CREATE ROLE IF NOT EXISTS" not in cluster_sql:
        raise MigrationError("cluster-role section lacks idempotent role creation")
    if re.search(r"\bCREATE\s+ROLE\s+(?!IF\s+NOT\s+EXISTS)", cluster_sql, re.I):
        raise MigrationError("cluster-role creation is not idempotent")
    database_sql = before + after
    if re.search(r"\bCREATE\s+ROLE\b", database_sql, re.IGNORECASE):
        raise MigrationError("database transaction contains cluster role creation")
    return cluster_sql.strip() + "\n", database_sql


def split_step5_database_phases(database_sql: str) -> list[str]:
    phases: list[str] = []
    remainder = database_sql
    for phase_number, (begin_marker, end_marker) in enumerate(
        STEP5_DATABASE_PHASE_MARKERS,
        start=1,
    ):
        if remainder.count(begin_marker) != 1:
            raise MigrationError(
                f"Step 5 database phase {phase_number} begin marker is not exact"
            )
        if remainder.count(end_marker) != 1:
            raise MigrationError(
                f"Step 5 database phase {phase_number} end marker is not exact"
            )
        prefix, marked = remainder.split(begin_marker, 1)
        if re.sub(r"--[^\n]*(?:\n|$)", "", prefix).strip():
            raise MigrationError(
                f"SQL exists outside Step 5 database phase {phase_number}"
            )
        phase_sql, remainder = marked.split(end_marker, 1)
        phase_sql = phase_sql.strip()
        if not phase_sql or not phase_sql.endswith(";"):
            raise MigrationError(
                f"Step 5 database phase {phase_number} is empty or incomplete"
            )
        phases.append(phase_sql + "\n")
    if re.sub(r"--[^\n]*(?:\n|$)", "", remainder).strip():
        raise MigrationError("SQL exists after the final Step 5 database phase")
    if len(phases) != 9:
        raise MigrationError("Step 5 must contain exactly nine database phases")
    if "CREATE TABLE IF NOT EXISTS memory_patch.request_contexts" not in phases[0]:
        raise MigrationError("Step 5 context table is not replay-safe")
    if len(
        re.findall(
            r"^CREATE OR REPLACE FUNCTION memory_patch\.",
            phases[0],
            re.MULTILINE,
        )
    ) != 15:
        raise MigrationError("Step 5 context function phase is not replay-safe")
    policy_sql = "\n".join(phases[6:])
    policy_creates = re.findall(
        r"^CREATE POLICY(?: IF NOT EXISTS)?\s+",
        policy_sql,
        re.MULTILINE,
    )
    if len(policy_creates) != 50 or any(
        fragment != "CREATE POLICY IF NOT EXISTS " for fragment in policy_creates
    ):
        raise MigrationError("Step 5 policy phase is not exactly replay-safe")
    return phases


def assert_step5_security_catalog(
    client: SqlClient,
    database: str,
) -> dict[str, Any]:
    manifest = load_security_manifest()
    protected_rows = [
        row for row in manifest["tables"] if row["rls_enabled"]
    ]
    protected_names = {row["table"] for row in protected_rows}
    table_catalog = parse_tsv(
        client.execute(
            database,
            "SELECT c.relname AS table_name, "
            "c.relrowsecurity, c.relforcerowsecurity, "
            "owner.rolname AS owner_role "
            "FROM pg_catalog.pg_class AS c "
            "JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace "
            "JOIN pg_catalog.pg_roles AS owner ON owner.oid = c.relowner "
            "WHERE n.nspname = 'memory_patch' AND c.relkind = 'r' "
            "ORDER BY c.relname",
        )
    )
    expected_owners = {
        row["table"]: row["owner_role"] for row in manifest["tables"]
    }
    expected_owners.update(
        {
            row["table"]: row["owner_role"]
            for row in manifest["security_internal_tables"]
        }
    )
    actual_owners = {
        row["table_name"]: row["owner_role"] for row in table_catalog
    }
    if actual_owners != expected_owners:
        raise MigrationError("Step 5 live table ownership differs from manifest")
    for row in table_catalog:
        protected = row["table_name"] in protected_names
        if (row["relrowsecurity"] == "t") is not protected:
            raise MigrationError(
                f"Step 5 live RLS state differs: {row['table_name']}"
            )
        if (row["relforcerowsecurity"] == "t") is not protected:
            raise MigrationError(
                f"Step 5 live FORCE RLS state differs: {row['table_name']}"
            )

    expected_policies: dict[str, tuple[str, str]] = {}
    for table in protected_rows:
        for command, policy_name in table["policies"].items():
            if policy_name is not None:
                expected_policies[policy_name] = (table["table"], command.upper())
    policy_catalog = parse_tsv(
        client.execute(
            database,
            "SELECT tablename, policyname, cmd, roles, qual, with_check "
            "FROM pg_catalog.pg_policies "
            "WHERE schemaname = 'memory_patch' "
            "ORDER BY tablename, policyname",
        )
    )
    actual_policies: dict[str, tuple[str, str]] = {}
    for row in policy_catalog:
        policy_name = row["policyname"]
        if policy_name in actual_policies:
            raise MigrationError(f"duplicate live policy name: {policy_name}")
        if "mp_app_runtime" not in row["roles"]:
            raise MigrationError(f"runtime role missing from policy: {policy_name}")
        command = row["cmd"].upper()
        if command in {"SELECT", "DELETE"} and not row["qual"]:
            raise MigrationError(f"USING expression missing: {policy_name}")
        if command == "INSERT" and not row["with_check"]:
            raise MigrationError(f"WITH CHECK expression missing: {policy_name}")
        if command == "UPDATE" and (
            not row["qual"] or not row["with_check"]
        ):
            raise MigrationError(
                f"UPDATE policy lacks USING/WITH CHECK: {policy_name}"
            )
        actual_policies[policy_name] = (row["tablename"], command)
    if actual_policies != expected_policies:
        raise MigrationError("Step 5 live policy set differs from manifest")

    fixed_roles = {
        role["role"]: role for role in manifest["roles"]
    }
    quoted_roles = ", ".join(sql_literal(role) for role in sorted(fixed_roles))
    role_catalog = parse_tsv(
        client.execute(
            "defaultdb",
            "SELECT rolname, rolcanlogin, rolcreaterole, rolcreatedb, "
            "rolbypassrls, rolsuper "
            "FROM pg_catalog.pg_roles "
            f"WHERE rolname IN ({quoted_roles}) ORDER BY rolname",
        )
    )
    if {row["rolname"] for row in role_catalog} != set(fixed_roles):
        raise MigrationError("Step 5 fixed role catalog is incomplete")
    for role in role_catalog:
        if any(
            role[field] != "f"
            for field in (
                "rolcanlogin",
                "rolcreaterole",
                "rolcreatedb",
                "rolbypassrls",
                "rolsuper",
            )
        ):
            raise MigrationError(f"unsafe live role options: {role['rolname']}")
    membership_rows = parse_tsv(
        client.execute(
            "defaultdb",
            "SELECT member.rolname AS member_role, parent.rolname AS parent_role "
            "FROM pg_catalog.pg_auth_members AS membership "
            "JOIN pg_catalog.pg_roles AS member "
            "ON member.oid = membership.member "
            "JOIN pg_catalog.pg_roles AS parent "
            "ON parent.oid = membership.roleid "
            f"WHERE member.rolname IN ({quoted_roles}) "
            "ORDER BY member.rolname, parent.rolname",
        )
    )
    if membership_rows:
        raise MigrationError("Step 5 fixed role inherits another SQL role")

    expected_runtime_grants = {
        (row["table"], privilege)
        for row in manifest["tables"]
        for privilege in row["runtime_grants"]
    }
    runtime_grants = parse_tsv(
        client.execute(
            database,
            "SELECT table_name, privilege_type "
            "FROM information_schema.table_privileges "
            "WHERE table_schema = 'memory_patch' "
            "AND grantee = 'mp_app_runtime' "
            "ORDER BY table_name, privilege_type",
        )
    )
    actual_runtime_grants = {
        (row["table_name"], row["privilege_type"]) for row in runtime_grants
    }
    if actual_runtime_grants != expected_runtime_grants:
        raise MigrationError("Step 5 live runtime grants differ from manifest")
    trigger_catalog = parse_tsv(
        client.execute(
            database,
            "SELECT trigger.tgname AS trigger_name, "
            "target.relname AS table_name, procedure.proname AS function_name "
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
    expected_triggers = {
        (
            "memory_items_s5_identity_guard",
            "memory_items",
            "guard_memory_item_identity",
        ),
        (
            "personal_memory_spaces_s5_identity_guard",
            "personal_memory_spaces",
            "guard_personal_memory_space_identity",
        ),
    }
    actual_triggers = {
        (
            row["trigger_name"],
            row["table_name"],
            row["function_name"],
        )
        for row in trigger_catalog
    }
    if actual_triggers != expected_triggers:
        raise MigrationError("Step 5 live identity-guard trigger set differs")
    return {
        "fixed_role_count": len(role_catalog),
        "identity_guard_trigger_count": len(trigger_catalog),
        "policy_count": len(policy_catalog),
        "protected_table_count": len(protected_names),
        "runtime_table_grant_count": len(runtime_grants),
        "table_owner_count": len(table_catalog),
    }


def apply_migrations(
    client: SqlClient,
    database: str,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    validate_timeout(timeout)
    migrations = load_migrations()
    already_applied = applied_migrations(client, database)
    unknown = set(already_applied) - {migration.migration_id for migration in migrations}
    if unknown:
        raise MigrationError(f"database contains unknown migrations: {sorted(unknown)}")
    applied: list[str] = []
    skipped: list[str] = []
    for migration in migrations:
        existing = already_applied.get(migration.migration_id)
        if existing is not None:
            if existing != migration.sha256:
                raise MigrationError(
                    f"applied checksum mismatch for {migration.migration_id}"
                )
            skipped.append(migration.migration_id)
            continue
        timestamp = utc_now()
        database_sql = migration.sql
        if migration.migration_id == STEP5_MIGRATION_ID:
            cluster_role_sql, database_sql = split_step5_cluster_role_ddl(
                migration.sql
            )
            client.execute(
                database,
                "SET allow_role_memberships_to_change_during_transaction = true;\n"
                + cluster_role_sql,
                timeout=timeout,
            )
            for phase_number, phase_sql in enumerate(
                split_step5_database_phases(database_sql),
                start=1,
            ):
                try:
                    client.execute(
                        database,
                        "BEGIN;\n" + phase_sql + "\nCOMMIT;",
                        timeout=timeout,
                    )
                except SqlError as exc:
                    raise SqlError(
                        f"{migration.migration_id} database phase "
                        f"{phase_number} failed: {exc}",
                        sqlstate=exc.sqlstate,
                    ) from exc
                except MigrationError as exc:
                    raise MigrationError(
                        f"{migration.migration_id} database phase "
                        f"{phase_number} failed: {exc}"
                    ) from exc
            assert_step5_security_catalog(client, database)
            database_sql = ""
        migration_record_sql = (
            "INSERT INTO memory_patch.schema_migrations "
            "(migration_id, checksum_sha256, applied_at, runner_version) VALUES ("
            + ", ".join(
                (
                    sql_literal(migration.migration_id),
                    sql_literal(migration.sha256),
                    sql_literal(timestamp) + "::TIMESTAMPTZ",
                    sql_literal(RUNNER_VERSION),
                )
            )
            + ");"
        )
        if database_sql:
            statement = (
                "BEGIN;\n"
                + database_sql
                + "\n"
                + migration_record_sql
                + "\nCOMMIT;"
            )
        else:
            statement = "BEGIN;\n" + migration_record_sql + "\nCOMMIT;"
        client.execute(database, statement, timeout=timeout)
        recorded = applied_migrations(client, database)
        if recorded.get(migration.migration_id) != migration.sha256:
            raise MigrationError(
                f"migration {migration.migration_id} was not recorded exactly"
            )
        applied.append(migration.migration_id)
        already_applied = recorded
    return {
        "applied": applied,
        "applied_count": len(applied),
        "skipped": skipped,
        "skipped_count": len(skipped),
    }


def schema_catalog(client: SqlClient, database: str) -> dict[str, Any]:
    table_rows = parse_tsv(
        client.execute(
            database,
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'memory_patch' ORDER BY table_name",
        )
    )
    column_rows = parse_tsv(
        client.execute(
            database,
            "SELECT table_name, column_name, data_type, is_nullable "
            "FROM information_schema.columns "
            "WHERE table_schema = 'memory_patch' "
            "ORDER BY table_name, ordinal_position",
        )
    )
    constraint_rows = parse_tsv(
        client.execute(
            database,
            "SELECT constraint_type, count(*) AS constraint_count "
            "FROM information_schema.table_constraints "
            "WHERE table_schema = 'memory_patch' "
            "GROUP BY constraint_type ORDER BY constraint_type",
        )
    )
    index_rows = parse_tsv(
        client.execute(
            database,
            "SELECT DISTINCT index_name FROM information_schema.statistics "
            "WHERE table_schema = 'memory_patch' ORDER BY index_name",
        )
    )
    digest_input = {
        "columns": column_rows,
        "constraints": constraint_rows,
        "tables": [row["table_name"] for row in table_rows],
    }
    return {
        "column_count": len(column_rows),
        "constraint_counts": {
            row["constraint_type"]: int(row["constraint_count"])
            for row in constraint_rows
        },
        "explicit_indexes": [row["index_name"] for row in index_rows],
        "schema_digest": hashlib.sha256(canonical_json_bytes(digest_input)).hexdigest(),
        "tables": [row["table_name"] for row in table_rows],
    }


def assert_catalog(catalog: Mapping[str, Any]) -> None:
    schema_manifest = load_schema_manifest()
    security_manifest = load_security_manifest()
    expected_tables = sorted(
        [
            *schema_manifest["required_tables"],
            *[
                row["table"]
                for row in security_manifest["security_internal_tables"]
            ],
        ]
    )
    if catalog["tables"] != expected_tables:
        raise MigrationError(
            "live catalog table set differs from Step 4 + Step 5 manifests"
        )
    indexes = set(catalog["explicit_indexes"])
    missing_indexes = set(schema_manifest["explicit_indexes"]) - indexes
    if missing_indexes:
        raise MigrationError(f"live catalog lacks indexes: {sorted(missing_indexes)}")
    constraint_counts = catalog["constraint_counts"]
    for required_type in ("CHECK", "FOREIGN KEY", "PRIMARY KEY", "UNIQUE"):
        if int(constraint_counts.get(required_type, 0)) <= 0:
            raise MigrationError(f"live catalog lacks {required_type} constraints")


def seed_and_probe(client: SqlClient, database: str, run_id: str) -> dict[str, Any]:
    digest_a = "a" * 64
    digest_b = "b" * 64
    digest_c = "c" * 64
    digest_d = "d" * 64
    tenant_a = f"{run_id}_tenant_a"
    tenant_b = f"{run_id}_tenant_b"
    user_a = f"{run_id}_user_a"
    user_b = f"{run_id}_user_b"
    personal_a = f"{run_id}_personal_a"
    personal_b = f"{run_id}_personal_b"
    shared_scope = f"{run_id}_shared_scope"
    personal_scope = f"{run_id}_personal_scope"
    personal_scope_b = f"{run_id}_personal_scope_b"
    source = f"{run_id}_source"
    snapshot = f"{run_id}_snapshot"
    version = f"{run_id}_version"
    chunk = f"{run_id}_chunk"
    kernel_run = f"{run_id}_run"
    proposal = f"{run_id}_proposal"
    approval = f"{run_id}_approval"
    commit = f"{run_id}_commit"
    statements = [
        (
            "INSERT INTO memory_patch.tenants "
            "(tenant_id, display_name, metadata, created_at, updated_at) VALUES "
            f"({sql_literal(tenant_a)}, 'Tenant A', '{{}}'::JSONB, now(), now()), "
            f"({sql_literal(tenant_b)}, 'Tenant B', '{{}}'::JSONB, now(), now())"
        ),
        (
            "INSERT INTO memory_patch.users "
            "(tenant_id, user_id, display_name, metadata, created_at, updated_at) "
            f"VALUES ({sql_literal(tenant_a)}, {sql_literal(user_a)}, 'User A', "
            "'{}'::JSONB, now(), now()), "
            f"({sql_literal(tenant_b)}, {sql_literal(user_b)}, 'User B', "
            "'{}'::JSONB, now(), now())"
        ),
        (
            "INSERT INTO memory_patch.hat_manifests "
            "(hat_id, hat_version, schema_version, display_name, manifest_hash, "
            "capabilities, approval_authority, commit_authority, "
            "canonical_write_authority, external_action_authority, "
            "allows_private_memory_access, allows_user_code, created_at) VALUES "
            f"({sql_literal(run_id + '_hat')}, '1.0.0', '1.0.0', 'Synthetic HAT', "
            f"'{digest_a}', '[]'::JSONB, 'NONE', 'NONE', 'NONE', 'NONE', "
            "false, false, now())"
        ),
        (
            "INSERT INTO memory_patch.personal_memory_spaces "
            "(tenant_id, user_id, personal_memory_space_id, schema_version, state, "
            "display_name, created_at, updated_at) VALUES "
            f"({sql_literal(tenant_a)}, {sql_literal(user_a)}, "
            f"{sql_literal(personal_a)}, '1.0.0', 'EMPTY', NULL, now(), now()), "
            f"({sql_literal(tenant_b)}, {sql_literal(user_b)}, "
            f"{sql_literal(personal_b)}, '1.0.0', 'EMPTY', NULL, now(), now())"
        ),
        (
            "INSERT INTO memory_patch.hat_scopes "
            "(tenant_id, hat_scope_id, target_scope, knowledge_hat_id, "
            "knowledge_hat_version, owner_user_id, personal_memory_space_id, "
            "created_at) VALUES "
            f"({sql_literal(tenant_a)}, {sql_literal(shared_scope)}, "
            f"'SHARED_KNOWLEDGE_HAT', {sql_literal(run_id + '_hat')}, "
            "'1.0.0', NULL, NULL, now()), "
            f"({sql_literal(tenant_a)}, {sql_literal(personal_scope)}, "
            f"'USER_PERSONAL_HAT', NULL, NULL, {sql_literal(user_a)}, "
            f"{sql_literal(personal_a)}, now()), "
            f"({sql_literal(tenant_b)}, {sql_literal(personal_scope_b)}, "
            f"'USER_PERSONAL_HAT', NULL, NULL, {sql_literal(user_b)}, "
            f"{sql_literal(personal_b)}, now())"
        ),
        (
            "INSERT INTO memory_patch.knowledge_sources "
            "(tenant_id, source_id, hat_scope_id, source_kind, source_reference, "
            "provenance, created_at) VALUES "
            f"({sql_literal(tenant_a)}, {sql_literal(source)}, "
            f"{sql_literal(shared_scope)}, 'SYNTHETIC', 'synthetic://source', "
            "'{\"producer\":\"step4-live-test\"}'::JSONB, now())"
        ),
        (
            "INSERT INTO memory_patch.source_snapshots "
            "(tenant_id, snapshot_id, source_id, hat_scope_id, content_sha256, "
            "byte_length, storage_class, immutable_object_reference, captured_at, "
            "provenance) VALUES "
            f"({sql_literal(tenant_a)}, {sql_literal(snapshot)}, "
            f"{sql_literal(source)}, {sql_literal(shared_scope)}, '{digest_a}', 21, "
            "'CRDB_TRANSACTIONAL', 'synthetic://snapshot', now(), '{}'::JSONB)"
        ),
        (
            "INSERT INTO memory_patch.knowledge_versions "
            "(tenant_id, knowledge_version_id, source_id, snapshot_id, hat_scope_id, "
            "version_ordinal, normalized_content_sha256, normalization_profile, "
            "is_current, created_at, provenance) VALUES "
            f"({sql_literal(tenant_a)}, {sql_literal(version)}, "
            f"{sql_literal(source)}, {sql_literal(snapshot)}, "
            f"{sql_literal(shared_scope)}, 1, '{digest_b}', 'synthetic-v1', true, "
            "now(), '{}'::JSONB)"
        ),
        (
            "INSERT INTO memory_patch.knowledge_chunks "
            "(tenant_id, chunk_id, knowledge_version_id, source_id, hat_scope_id, "
            "chunk_ordinal, content_text, content_sha256, start_offset, end_offset, "
            "language_tag, metadata, created_at) VALUES "
            f"({sql_literal(tenant_a)}, {sql_literal(chunk)}, "
            f"{sql_literal(version)}, {sql_literal(source)}, "
            f"{sql_literal(shared_scope)}, 0, 'neutral synthetic knowledge', "
            f"'{digest_c}', 0, 27, 'en', '{{}}'::JSONB, now())"
        ),
        (
            "INSERT INTO memory_patch.chunk_search_documents "
            "(tenant_id, chunk_id, knowledge_version_id, source_id, hat_scope_id, "
            "search_config, search_vector, created_at) VALUES "
            f"({sql_literal(tenant_a)}, {sql_literal(chunk)}, "
            f"{sql_literal(version)}, {sql_literal(source)}, "
            f"{sql_literal(shared_scope)}, 'english', "
            "to_tsvector('english', 'neutral synthetic knowledge'), now())"
        ),
        (
            "INSERT INTO memory_patch.kernel_runs "
            "(tenant_id, kernel_run_id, user_id, personal_memory_space_id, "
            "model_binding_id, request_sha256, created_at) VALUES "
            f"({sql_literal(tenant_a)}, {sql_literal(kernel_run)}, "
            f"{sql_literal(user_a)}, {sql_literal(personal_a)}, "
            f"{sql_literal(run_id + '_model')}, '{digest_d}', now())"
        ),
        (
            "INSERT INTO memory_patch.routing_decisions "
            "(tenant_id, routing_decision_id, kernel_run_id, knowledge_route, "
            "selected_hat_scope_id, selected_hat_id, reason_codes, decided_at) "
            "VALUES "
            f"({sql_literal(tenant_a)}, {sql_literal(run_id + '_routing')}, "
            f"{sql_literal(kernel_run)}, 'HAT_ASSIST', "
            f"{sql_literal(shared_scope)}, "
            f"{sql_literal(run_id + '_hat')}, '[\"synthetic\"]'::JSONB, now())"
        ),
        (
            "INSERT INTO memory_patch.action_policy_decisions "
            "(tenant_id, action_policy_decision_id, kernel_run_id, action_policy, "
            "reason_codes, decided_at) VALUES "
            f"({sql_literal(tenant_a)}, {sql_literal(run_id + '_policy')}, "
            f"{sql_literal(kernel_run)}, 'ALLOW', '[\"synthetic\"]'::JSONB, now())"
        ),
        (
            "INSERT INTO memory_patch.memory_patch_proposals "
            "(tenant_id, proposal_id, schema_version, hat_scope_id, target_scope, "
            "target_hat_id, owner_user_id, personal_memory_space_id, origin, "
            "proposed_content, evidence_references, scope_dimensions, "
            "requested_trust_class, approval_requirement, lifecycle_state, "
            "content_kind, created_at, content_hash) VALUES "
            f"({sql_literal(tenant_a)}, {sql_literal(proposal)}, '1.0.0', "
            f"{sql_literal(personal_scope)}, 'USER_PERSONAL_HAT', NULL, "
            f"{sql_literal(user_a)}, {sql_literal(personal_a)}, 'USER_ENTRY', "
            "'{\"preference\":\"concise\"}'::JSONB, '[]'::JSONB, '[]'::JSONB, "
            f"'USER_ASSERTED_MEMORY', 'OWNER', 'PROPOSED', 'PREFERENCE', now(), "
            f"'{digest_a}')"
        ),
        (
            "INSERT INTO memory_patch.memory_patch_approvals "
            "(tenant_id, approval_id, schema_version, proposal_id, "
            "proposal_content_hash, target_scope, owner_user_id, "
            "personal_memory_space_id, decision, approver_type, approver_id, "
            "reason_code, decided_at, approval_proof) VALUES "
            f"({sql_literal(tenant_a)}, {sql_literal(approval)}, '1.0.0', "
            f"{sql_literal(proposal)}, '{digest_a}', 'USER_PERSONAL_HAT', "
            f"{sql_literal(user_a)}, {sql_literal(personal_a)}, 'APPROVE', 'USER', "
            f"{sql_literal(user_a)}, 'OWNER_APPROVED', now(), '{digest_b}')"
        ),
        (
            "INSERT INTO memory_patch.memory_patch_commits "
            "(tenant_id, commit_id, schema_version, proposal_id, "
            "proposal_content_hash, target_scope, approval_id, approval_proof, "
            "approval_decision, committed_patch_id, owner_user_id, "
            "personal_memory_space_id, actor_type, actor_id, storage_class, "
            "committed_at, commit_hash) VALUES "
            f"({sql_literal(tenant_a)}, {sql_literal(commit)}, '1.0.0', "
            f"{sql_literal(proposal)}, '{digest_a}', 'USER_PERSONAL_HAT', "
            f"{sql_literal(approval)}, '{digest_b}', 'APPROVE', "
            f"{sql_literal(run_id + '_patch')}, "
            f"{sql_literal(user_a)}, {sql_literal(personal_a)}, 'COMMIT_SERVICE', "
            f"{sql_literal(run_id + '_commit_service')}, 'CRDB_TRANSACTIONAL', "
            f"now(), '{digest_c}')"
        ),
    ]
    for statement in statements:
        client.execute(database, statement)

    negative_results: dict[str, str | None] = {}
    negative_results["cross_tenant_snapshot"] = client.expect_error(
        database,
        "INSERT INTO memory_patch.source_snapshots "
        "(tenant_id, snapshot_id, source_id, hat_scope_id, content_sha256, "
        "byte_length, storage_class, immutable_object_reference, captured_at, "
        "provenance) VALUES "
        f"({sql_literal(tenant_b)}, {sql_literal(run_id + '_bad_snapshot')}, "
        f"{sql_literal(source)}, {sql_literal(shared_scope)}, '{digest_d}', 1, "
        "'CRDB_TRANSACTIONAL', 'synthetic://bad', now(), '{}'::JSONB)",
        expected_sqlstate="23503",
    )
    negative_results["cross_hat_snapshot"] = client.expect_error(
        database,
        "INSERT INTO memory_patch.source_snapshots "
        "(tenant_id, snapshot_id, source_id, hat_scope_id, content_sha256, "
        "byte_length, storage_class, immutable_object_reference, captured_at, "
        "provenance) VALUES "
        f"({sql_literal(tenant_a)}, {sql_literal(run_id + '_cross_hat_snapshot')}, "
        f"{sql_literal(source)}, {sql_literal(personal_scope)}, '{digest_d}', 1, "
        "'CRDB_TRANSACTIONAL', 'synthetic://cross-hat', now(), '{}'::JSONB)",
        expected_sqlstate="23503",
    )
    negative_results["cross_tenant_version"] = client.expect_error(
        database,
        "INSERT INTO memory_patch.knowledge_versions "
        "(tenant_id, knowledge_version_id, source_id, snapshot_id, hat_scope_id, "
        "version_ordinal, normalized_content_sha256, normalization_profile, "
        "is_current, created_at, provenance) VALUES "
        f"({sql_literal(tenant_b)}, {sql_literal(run_id + '_cross_tenant_version')}, "
        f"{sql_literal(source)}, {sql_literal(snapshot)}, "
        f"{sql_literal(shared_scope)}, 2, '{digest_d}', 'synthetic-v1', false, "
        "now(), '{}'::JSONB)",
        expected_sqlstate="23503",
    )
    negative_results["cross_tenant_chunk"] = client.expect_error(
        database,
        "INSERT INTO memory_patch.knowledge_chunks "
        "(tenant_id, chunk_id, knowledge_version_id, source_id, hat_scope_id, "
        "chunk_ordinal, content_text, content_sha256, metadata, created_at) VALUES "
        f"({sql_literal(tenant_b)}, {sql_literal(run_id + '_cross_tenant_chunk')}, "
        f"{sql_literal(version)}, {sql_literal(source)}, "
        f"{sql_literal(shared_scope)}, 1, 'forbidden cross tenant', '{digest_d}', "
        "'{}'::JSONB, now())",
        expected_sqlstate="23503",
    )
    negative_results["cross_hat_chunk"] = client.expect_error(
        database,
        "INSERT INTO memory_patch.knowledge_chunks "
        "(tenant_id, chunk_id, knowledge_version_id, source_id, hat_scope_id, "
        "chunk_ordinal, content_text, content_sha256, metadata, created_at) VALUES "
        f"({sql_literal(tenant_a)}, {sql_literal(run_id + '_cross_hat_chunk')}, "
        f"{sql_literal(version)}, {sql_literal(source)}, "
        f"{sql_literal(personal_scope)}, 1, 'forbidden cross hat', '{digest_d}', "
        "'{}'::JSONB, now())",
        expected_sqlstate="23503",
    )
    negative_results["cross_owner_scope"] = client.expect_error(
        database,
        "INSERT INTO memory_patch.hat_scopes "
        "(tenant_id, hat_scope_id, target_scope, owner_user_id, "
        "personal_memory_space_id, created_at) VALUES "
        f"({sql_literal(tenant_a)}, {sql_literal(run_id + '_bad_owner_scope')}, "
        f"'USER_PERSONAL_HAT', {sql_literal(user_b)}, "
        f"{sql_literal(personal_a)}, now())",
        expected_sqlstate="23503",
    )
    negative_results["private_scope_requires_owner"] = client.expect_error(
        database,
        "INSERT INTO memory_patch.hat_scopes "
        "(tenant_id, hat_scope_id, target_scope, created_at) VALUES "
        f"({sql_literal(tenant_a)}, {sql_literal(run_id + '_ownerless_private')}, "
        "'USER_PERSONAL_HAT', now())",
        expected_sqlstate="23514",
    )
    negative_results["shared_scope_rejects_private_owner"] = client.expect_error(
        database,
        "INSERT INTO memory_patch.hat_scopes "
        "(tenant_id, hat_scope_id, target_scope, knowledge_hat_id, "
        "knowledge_hat_version, owner_user_id, personal_memory_space_id, "
        "created_at) VALUES "
        f"({sql_literal(tenant_a)}, {sql_literal(run_id + '_owned_shared')}, "
        f"'SHARED_KNOWLEDGE_HAT', {sql_literal(run_id + '_hat')}, '1.0.0', "
        f"{sql_literal(user_a)}, {sql_literal(personal_a)}, now())",
        expected_sqlstate="23514",
    )
    negative_results["duplicate_source_identity"] = client.expect_error(
        database,
        "INSERT INTO memory_patch.knowledge_sources "
        "(tenant_id, source_id, hat_scope_id, source_kind, source_reference, "
        "provenance, created_at) VALUES "
        f"({sql_literal(tenant_a)}, {sql_literal(source)}, "
        f"{sql_literal(shared_scope)}, 'SYNTHETIC', 'synthetic://duplicate', "
        "'{}'::JSONB, now())",
        expected_sqlstate="23505",
    )
    negative_results["model_cannot_claim_approval"] = client.expect_error(
        database,
        "INSERT INTO memory_patch.memory_patch_approvals "
        "(tenant_id, approval_id, schema_version, proposal_id, "
        "proposal_content_hash, target_scope, owner_user_id, "
        "personal_memory_space_id, decision, approver_type, approver_id, "
        "reason_code, decided_at, approval_proof) VALUES "
        f"({sql_literal(tenant_a)}, {sql_literal(run_id + '_model_approval')}, "
        f"'1.0.0', {sql_literal(proposal)}, '{digest_a}', 'USER_PERSONAL_HAT', "
        f"{sql_literal(user_a)}, {sql_literal(personal_a)}, 'APPROVE', 'MODEL', "
        f"{sql_literal(run_id + '_model')}, 'FORBIDDEN', now(), '{digest_d}')",
        expected_sqlstate="23514",
    )
    negative_results["hat_cannot_claim_authority"] = client.expect_error(
        database,
        "INSERT INTO memory_patch.hat_manifests "
        "(hat_id, hat_version, schema_version, display_name, manifest_hash, "
        "capabilities, approval_authority, commit_authority, "
        "canonical_write_authority, external_action_authority, "
        "allows_private_memory_access, allows_user_code, created_at) VALUES "
        f"({sql_literal(run_id + '_bad_hat')}, '1.0.0', '1.0.0', 'Bad HAT', "
        f"'{digest_d}', '[]'::JSONB, 'HUMAN', 'NONE', 'NONE', 'NONE', "
        "false, false, now())",
        expected_sqlstate="23514",
    )
    negative_results["verified_memory_stays_inert"] = client.expect_error(
        database,
        "INSERT INTO memory_patch.memory_items "
        "(tenant_id, memory_item_id, schema_version, hat_scope_id, target_scope, "
        "visibility, trust_class, content_kind, content, scope_dimensions, "
        "evidence_references, source_patch_id, active, revoked, created_at) VALUES "
        f"({sql_literal(tenant_a)}, {sql_literal(run_id + '_bad_memory')}, "
        f"'1.0.0', {sql_literal(personal_scope)}, 'USER_PERSONAL_HAT', "
        "'PERSONAL', 'PERSONAL_VERIFIED_PATCH', 'FACTUAL', "
        "'{\"value\":\"synthetic\"}'::JSONB, '[]'::JSONB, '[]'::JSONB, "
        f"{sql_literal(run_id + '_patch')}, true, false, now())",
        expected_sqlstate="23514",
    )
    negative_results["forbidden_transition_edge"] = client.expect_error(
        database,
        "INSERT INTO memory_patch.patch_transition_records "
        "(tenant_id, transition_id, proposal_id, proposal_content_hash, "
        "state_before, state_after, actor_type, actor_id, transitioned_at) VALUES "
        f"({sql_literal(tenant_a)}, {sql_literal(run_id + '_bad_transition')}, "
        f"{sql_literal(proposal)}, '{digest_a}', 'PROPOSED', 'APPROVED', "
        f"'MODEL', {sql_literal(run_id + '_model')}, now())",
        expected_sqlstate="23514",
    )
    negative_results["cross_owner_commit_binding"] = client.expect_error(
        database,
        "INSERT INTO memory_patch.memory_patch_commits "
        "(tenant_id, commit_id, schema_version, proposal_id, "
        "proposal_content_hash, target_scope, approval_id, approval_proof, "
        "approval_decision, committed_patch_id, owner_user_id, "
        "personal_memory_space_id, actor_type, actor_id, storage_class, "
        "committed_at, commit_hash) VALUES "
        f"({sql_literal(tenant_a)}, {sql_literal(run_id + '_bad_commit')}, "
        f"'1.0.0', {sql_literal(proposal)}, '{digest_a}', 'USER_PERSONAL_HAT', "
        f"{sql_literal(approval)}, '{digest_b}', 'APPROVE', "
        f"{sql_literal(run_id + '_bad_patch')}, "
        f"{sql_literal(run_id + '_other_owner')}, {sql_literal(personal_a)}, "
        f"'COMMIT_SERVICE', {sql_literal(run_id + '_commit_service')}, "
        f"'CRDB_TRANSACTIONAL', now(), '{digest_d}')",
        expected_sqlstate="23503",
    )
    negative_results["personal_approval_requires_owner"] = client.expect_error(
        database,
        "INSERT INTO memory_patch.memory_patch_approvals "
        "(tenant_id, approval_id, schema_version, proposal_id, "
        "proposal_content_hash, target_scope, owner_user_id, "
        "personal_memory_space_id, decision, approver_type, approver_id, "
        "reason_code, decided_at, approval_proof) VALUES "
        f"({sql_literal(tenant_a)}, "
        f"{sql_literal(run_id + '_ownerless_approval')}, '1.0.0', "
        f"{sql_literal(proposal)}, '{digest_a}', 'USER_PERSONAL_HAT', "
        f"NULL, NULL, 'APPROVE', 'USER', {sql_literal(user_a)}, "
        f"'OWNER_REQUIRED', now(), '{digest_d}')",
        expected_sqlstate="23514",
    )
    negative_results["personal_approval_scope_is_exact"] = client.expect_error(
        database,
        "INSERT INTO memory_patch.memory_patch_approvals "
        "(tenant_id, approval_id, schema_version, proposal_id, "
        "proposal_content_hash, target_scope, owner_user_id, "
        "personal_memory_space_id, decision, approver_type, approver_id, "
        "reason_code, decided_at, approval_proof) VALUES "
        f"({sql_literal(tenant_a)}, "
        f"{sql_literal(run_id + '_wrong_scope_approval')}, '1.0.0', "
        f"{sql_literal(proposal)}, '{digest_a}', 'SHARED_KNOWLEDGE_HAT', "
        f"NULL, NULL, 'APPROVE', 'HUMAN_REVIEWER', {sql_literal(user_a)}, "
        f"'SCOPE_MISMATCH', now(), '{digest_d}')",
        expected_sqlstate="23503",
    )
    negative_results["personal_commit_requires_owner"] = client.expect_error(
        database,
        "INSERT INTO memory_patch.memory_patch_commits "
        "(tenant_id, commit_id, schema_version, proposal_id, "
        "proposal_content_hash, target_scope, approval_id, approval_proof, "
        "approval_decision, committed_patch_id, owner_user_id, "
        "personal_memory_space_id, actor_type, actor_id, storage_class, "
        "committed_at, commit_hash) VALUES "
        f"({sql_literal(tenant_a)}, "
        f"{sql_literal(run_id + '_ownerless_commit')}, '1.0.0', "
        f"{sql_literal(proposal)}, '{digest_a}', 'USER_PERSONAL_HAT', "
        f"{sql_literal(approval)}, '{digest_b}', 'APPROVE', "
        f"{sql_literal(run_id + '_ownerless_patch')}, NULL, NULL, "
        f"'COMMIT_SERVICE', {sql_literal(run_id + '_commit_service')}, "
        f"'CRDB_TRANSACTIONAL', now(), '{digest_d}')",
        expected_sqlstate="23514",
    )
    negative_results["personal_scope_cannot_replace_knowledge_hat"] = (
        client.expect_error(
            database,
            "INSERT INTO memory_patch.evidence_bundles "
            "(tenant_id, evidence_bundle_id, kernel_run_id, hat_scope_id, hat_id, "
            "evidence_status, retrieval_policy_version, bundle_hash, created_at) "
            "VALUES "
            f"({sql_literal(tenant_a)}, {sql_literal(run_id + '_bad_bundle')}, "
            f"{sql_literal(kernel_run)}, {sql_literal(personal_scope)}, "
            f"{sql_literal(run_id + '_hat')}, 'INSUFFICIENT', 'synthetic-v1', "
            f"'{digest_d}', now())",
            expected_sqlstate="23503",
        )
    )
    search_count = one_value(
        client.execute(
            database,
            "SELECT count(*) FROM memory_patch.chunk_search_documents "
            "WHERE search_vector @@ plainto_tsquery('english', 'synthetic knowledge')",
        )
    )
    if search_count != "1":
        raise MigrationError("live full-text foundation query returned wrong result")
    if one_value(
        client.execute(
            database,
            "SELECT count(*) FROM memory_patch.memory_patch_commits",
        )
    ) != "1":
        raise MigrationError("valid approval/commit structural binding was not stored")
    return {
        "full_text_match_count": int(search_count),
        "negative_sqlstates": negative_results,
        "seeded_lineage": "source->snapshot->version->chunk",
        "stored_kernel_run": True,
        "stored_routing_decision": True,
        "stored_structural_commit_receipt": True,
    }


def create_database(client: SqlClient, database: str) -> None:
    assert_disposable_database(database)
    client.execute("defaultdb", f"CREATE DATABASE {database}")


def drop_database(
    client: SqlClient,
    database: str,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> None:
    assert_disposable_database(database)
    validate_timeout(timeout)
    client.execute(
        "defaultdb",
        f"DROP DATABASE IF EXISTS {database} CASCADE",
        timeout=timeout,
    )
    remaining = one_value(
        client.execute(
            "defaultdb",
            "SELECT count(*) FROM [SHOW DATABASES] "
            f"WHERE database_name = {sql_literal(database)}",
            timeout=timeout,
        )
    )
    if remaining != "0":
        raise MigrationError(f"disposable database survived cleanup: {database}")


def run_live_validation(binary: Path) -> dict[str, Any]:
    offline = offline_validate()
    migration_count = offline["migration_count"]
    binary_identity = verify_binary_identity(binary)
    run_id = DISPOSABLE_DATABASE_PREFIX + uuid.uuid4().hex[:12]
    database_a = run_id + "_a"
    database_b = run_id + "_b"
    runtime = LocalRuntime(binary=binary, run_id=run_id)
    client: SqlClient | None = None
    created_databases: list[str] = []
    cleanup: dict[str, Any] = {}
    live_result: dict[str, Any] | None = None
    cleanup_errors: list[str] = []
    try:
        client = runtime.start()
        server_version = one_value(client.execute("defaultdb", "SELECT version()"))
        if PINNED_VERSION not in server_version:
            raise MigrationError("live server version differs from pinned v26.2.4")
        cluster_version = one_value(
            client.execute("defaultdb", "SHOW CLUSTER SETTING version")
        )
        if cluster_version != PINNED_CLUSTER_VERSION:
            raise MigrationError("live cluster version differs from pinned 26.2")
        for database in (database_a, database_b):
            create_database(client, database)
            created_databases.append(database)
        first_apply = apply_migrations(client, database_a)
        second_apply = apply_migrations(client, database_a)
        reproduction_apply = apply_migrations(client, database_b)
        if first_apply["applied_count"] != migration_count:
            raise MigrationError("fresh database did not apply all migrations")
        if (
            second_apply["applied_count"] != 0
            or second_apply["skipped_count"] != migration_count
        ):
            raise MigrationError("second migration invocation was not a full no-op")
        if reproduction_apply["applied_count"] != migration_count:
            raise MigrationError("reproduction database did not apply all migrations")
        catalog_a = schema_catalog(client, database_a)
        catalog_b = schema_catalog(client, database_b)
        assert_catalog(catalog_a)
        assert_catalog(catalog_b)
        if catalog_a["schema_digest"] != catalog_b["schema_digest"]:
            raise MigrationError("fresh database schema reproduction digest differs")
        probes = seed_and_probe(client, database_a, run_id)
        if len(applied_migrations(client, database_a)) != migration_count:
            raise MigrationError("invalid data probes changed migration bookkeeping")
        live_result = {
            "binary_identity": binary_identity,
            "cluster_version": cluster_version,
            "database_count": 2,
            "first_apply": first_apply,
            "loopback_only": True,
            "no_op_apply": second_apply,
            "offline": offline,
            "probes": probes,
            "reproduction_apply": reproduction_apply,
            "schema": {
                "column_count": catalog_a["column_count"],
                "constraint_counts": catalog_a["constraint_counts"],
                "explicit_index_count": len(load_schema_manifest()["explicit_indexes"]),
                "schema_digest": catalog_a["schema_digest"],
                "table_count": len(catalog_a["tables"]),
                "protected_table_count": offline["protected_table_count"],
            },
            "server_version": server_version.splitlines()[0],
            "status": "PASS",
        }
    finally:
        if client is not None:
            for database in reversed(created_databases):
                try:
                    drop_database(client, database)
                except MigrationError as exc:
                    cleanup_errors.append(str(exc))
        cleanup = runtime.stop_and_remove()
        cleanup_errors.extend(cleanup["cleanup_errors"])
    if cleanup_errors:
        raise MigrationError(f"cleanup failed: {cleanup_errors}")
    if live_result is None:
        raise MigrationError("live validation did not produce a result")
    live_result["cleanup"] = {
        "databases_removed": len(created_databases) == 2,
        "force_kill_used": cleanup["force_kill_used"],
        "no_password_credentials_created": True,
        "owned_pid_exited": cleanup["pid_exited"],
        "ports_closed": cleanup["ports_closed"],
        "runtime_duration_seconds": cleanup["runtime_duration_seconds"],
        "temporary_store_removed": cleanup["temporary_store_removed"],
    }
    live_result["generated_at_utc"] = utc_now()
    return live_result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--offline-validate", action="store_true")
    action.add_argument("--live-test", action="store_true")
    action.add_argument("--apply", action="store_true")
    action.add_argument("--status", action="store_true")
    parser.add_argument("--allow-live", action="store_true")
    parser.add_argument("--cockroach-binary", type=Path)
    parser.add_argument("--database")
    parser.add_argument("--port", type=int)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--json-output", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.offline_validate:
            result = offline_validate()
        else:
            if not args.allow_live:
                raise MigrationError("live action requires explicit --allow-live")
            if args.cockroach_binary is None:
                raise MigrationError("--cockroach-binary is required")
            binary = args.cockroach_binary.expanduser().resolve()
            verify_binary_identity(binary)
            if args.live_test:
                result = run_live_validation(binary)
            else:
                if args.database is None or args.port is None:
                    raise MigrationError("--database and --port are required")
                client = SqlClient(binary=binary, port=args.port)
                if args.apply:
                    result = apply_migrations(
                        client,
                        args.database,
                        timeout=args.timeout,
                    )
                else:
                    result = {
                        "applied": applied_migrations(client, args.database),
                        "status": "PASS",
                    }
        payload = canonical_json_bytes(result)
        if args.json_output is not None:
            output = args.json_output.expanduser().resolve()
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(payload)
        print(payload.decode("utf-8"), end="")
        return 0
    except MigrationError as exc:
        print(f"ERROR: {exc}", file=os.sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
