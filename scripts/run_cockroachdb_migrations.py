#!/usr/bin/env python3
"""Validate and apply the canonical Memory Patch CockroachDB migration chain.

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
import math
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
PERSISTENCE_MANIFEST_PATH = (
    REPOSITORY_ROOT
    / "config"
    / "cockroachdb"
    / "persistence-security-1a.json"
)
SOURCE_REGISTRY_MANIFEST_PATH = (
    REPOSITORY_ROOT
    / "config"
    / "source-registry"
    / "source-registry-policy-1a.json"
)
INGESTION_SAGA_MANIFEST_PATH = (
    REPOSITORY_ROOT
    / "config"
    / "cockroachdb"
    / "ingestion-saga-security-1a.json"
)
PARSING_PIPELINE_MANIFEST_PATH = (
    REPOSITORY_ROOT
    / "config"
    / "cockroachdb"
    / "parsing-pipeline-security-1a.json"
)
CORRECTION_CANDIDATE_SECURITY_MANIFEST_PATH = (
    REPOSITORY_ROOT
    / "config"
    / "cockroachdb"
    / "correction-candidate-security-1a.json"
)
PATCH_VALIDATION_SECURITY_MANIFEST_PATH = (
    REPOSITORY_ROOT
    / "config"
    / "cockroachdb"
    / "personal-memory-patch-validation-security-1a.json"
)
APPROVAL_COMMIT_SECURITY_MANIFEST_PATH = (
    REPOSITORY_ROOT
    / "config"
    / "cockroachdb"
    / "personal-memory-approval-commit-security-1a.json"
)
VERSION_PIN_PATH = (
    REPOSITORY_ROOT / "config" / "cockroachdb" / "version-pin.json"
)
RUNNER_VERSION = "12.0.0"
PINNED_VERSION = "v26.2.4"
PINNED_CLUSTER_VERSION = "26.2"
DEFAULT_TIMEOUT_SECONDS = 60.0
MAX_SQL_COMMAND_TIMEOUT_SECONDS = 300.0
START_TIMEOUT_SECONDS = 45.0
SHUTDOWN_SCHEDULING_CUSHION_SECONDS = 15
MIN_GRACEFUL_SHUTDOWN_SECONDS = 60
MAX_GRACEFUL_SHUTDOWN_SECONDS = 120
SHUTDOWN_COMMAND_CUSHION_SECONDS = 15
DISPOSABLE_DATABASE_PREFIXES = (
    "mp_step5_",
    "mp_step6_",
    "mp_step9_",
    "mp_step10_",
    "mp_step11_",
    "mp_step12_",
    "mp_step13_",
    "mp_step14_",
    "mp_step15_",
    "mp_step19_",
    "mp_step27_",
    "mp_step28_",
    "mp_step29_",
    "mp_step30_",
    "mp_step31_",
)
MIGRATION_ID_PATTERN = re.compile(r"^\d{4}_[a-z0-9_]+$")
SAFE_IDENTIFIER_PATTERN = re.compile(r"^[a-z][a-z0-9_]{2,62}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
GO_DURATION_COMPONENT_PATTERN = re.compile(
    r"(\d+(?:\.\d+)?)(ns|us|µs|ms|s|m|h)"
)

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
STEP5_MIGRATION_SHA256 = (
    "6a8968dab3aa063b2d6f34bb31ecd26039e50f2f9351d961140c3a739106fbcd"
)
STEP6_MIGRATION_ID = "0005_step6_persistence_idempotency_retry_foundation"
STEP6_MIGRATION_SHA256 = (
    "c6ad8cfe2b56b4bb59c6e604ae9f6281242e1baed135b94feaea6087f1651173"
)
STEP9_MIGRATION_ID = (
    "0006_step9_source_registry_provenance_publication_states"
)
STEP9_MIGRATION_SHA256 = (
    "921f5e1bb16142c082b1e91fbbaae729af3aad6f62fd0a5a0a15cda5f3fa5347"
)
STEP10_MIGRATION_ID = "0007_step10_idempotent_ingestion_saga"
STEP10_MIGRATION_SHA256 = (
    "9a2f62428d6ed088f5f282b21836d40f4ec632e989df9e3935861c2e4daec122"
)
STEP11_MIGRATION_ID = "0008_step11_generic_parsing_pipeline"
STEP11_MIGRATION_SHA256 = (
    "2b4d269d98b69bdc19137b3e92f741eae344ff705cc0ed86636b950ffe0d0b61"
)
STEP12_MIGRATION_ID = "0009_step12_hat_registry_runtime_boundary"
STEP12_MIGRATION_SHA256 = (
    "d4437055e913d5d4874c4d0b1b1985a4d93a0ec0606d905b04e43c38534204f7"
)
STEP19_MIGRATION_ID = "0010_step19_embedding_vector_retrieval"
STEP19_MIGRATION_SHA256 = (
    "e335631fb1087d191b775ef6d3fb77ac99156ae83bffd77a77651b75541e3377"
)
STEP27_MIGRATION_ID = "0011_step27_personal_memory_persistence"
STEP27_MIGRATION_SHA256 = (
    "acbe9f619da87cd77ab2a59eb212693cbbd060ff3b46a318b570a1700c8adaed"
)
STEP28_MIGRATION_ID = "0012_step28_correction_candidate_bridge"
STEP28_MIGRATION_SHA256 = (
    "28c11f7f60e92ce9a0bae80f5704b0d0d3e93d670c1e2e3388f4c4aee9c80903"
)
STEP28_FUNCTION_SIGNATURES = {
    "enforce_step28_candidate_quota": "enforce_step28_candidate_quota()",
    "guard_step28_slot_authority_update": (
        "guard_step28_slot_authority_update()"
    ),
    "step28_personal_hat_scope_id": (
        "step28_personal_hat_scope_id(text, text, text)"
    ),
    "step28_candidate_slot_is_eligible": (
        "step28_candidate_slot_is_eligible(text, text, text, text)"
    ),
    "step28_candidate_target_matches": (
        "step28_candidate_target_matches(text, text, text, text, text, "
        "int8, int8, text, text, text, text, text, text, int8, text, text, "
        "text, text, bool)"
    ),
}
STEP29_MIGRATION_ID = "0013_step29_personal_memory_patch_validation"
STEP29_MIGRATION_SHA256 = (
    "ae2165a4e2182dc5886c44ee602ab73b808f7dbfc1298afa2b65e101041e1699"
)
STEP29_FUNCTION_SIGNATURES = {
    "enforce_step29_proposal_quota": "enforce_step29_proposal_quota()",
    "guard_step29_proposal_transition": "guard_step29_proposal_transition()",
    "step29_proposal_target_matches": (
        "step29_proposal_target_matches(text, text, text, text, text, text, "
        "text, text, text, int8, int8, text, text, text, text, text, text, "
        "int8, text, text, text, text, bool, text, jsonb)"
    ),
    "step29_transition_target_matches": (
        "step29_transition_target_matches(text, text, text, text)"
    ),
}
STEP30_MIGRATION_ID = "0014_step30_user_approval_commit_activation"
STEP30_MIGRATION_SHA256 = (
    "8c8fce3bb6263698a43adfd844e67dd4d55c78f7b584468a8e10cd1edc0ca95f"
)
STEP30_CLUSTER_ROLE_BEGIN = "-- STEP30_CLUSTER_ROLE_DDL_BEGIN"
STEP30_CLUSTER_ROLE_END = "-- STEP30_CLUSTER_ROLE_DDL_END"
STEP30_FUNCTION_SIGNATURES = {
    "guard_step30_commit_slot_update": "guard_step30_commit_slot_update()",
    "guard_step30_memory_item": "guard_step30_memory_item()",
    "guard_step30_proposal_transition": "guard_step30_proposal_transition()",
    "step30_approval_target_matches": (
        "step30_approval_target_matches(text, text, text, text, text, text, text)"
    ),
    "step30_approval_transition_matches": (
        "step30_approval_transition_matches(text, text, text, text, text, text, text)"
    ),
    "step30_commit_helper_authorized": "step30_commit_helper_authorized()",
    "step30_commit_target_matches": (
        "step30_commit_target_matches(text, text, text, text, text, text, text, "
        "text, text, text, jsonb)"
    ),
    "step30_memory_item_commit_matches": (
        "step30_memory_item_commit_matches(text, text, text, text, text, text, "
        "text, text, text, jsonb)"
    ),
    "step30_proposal_lifecycle_matches": (
        "step30_proposal_lifecycle_matches(text, text, text, text, text, text, "
        "text, text, text, text, text, text, jsonb)"
    ),
    "step30_transition_target_matches": (
        "step30_transition_target_matches(text, text, text, text, text)"
    ),
}
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

STEP6_FORBIDDEN_MIGRATION_PATTERNS: tuple[
    tuple[str, re.Pattern[str]], ...
] = (
    (
        "role creation",
        re.compile(r"\bCREATE\s+ROLE\b", re.IGNORECASE),
    ),
    (
        "positive BYPASSRLS grant",
        re.compile(r"(?<!NO)\bBYPASSRLS\b", re.IGNORECASE),
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
        "NVIDIA-branded database object",
        re.compile(r"\b(?:NVIDIA|NOOA|OpenShell)\b", re.IGNORECASE),
    ),
    (
        "domain-specific persistence rule",
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

STEP9_FORBIDDEN_MIGRATION_PATTERNS: tuple[
    tuple[str, re.Pattern[str]], ...
] = (
    ("role creation", re.compile(r"\bCREATE\s+ROLE\b", re.IGNORECASE)),
    (
        "positive BYPASSRLS grant",
        re.compile(r"(?<!NO)\bBYPASSRLS\b", re.IGNORECASE),
    ),
    ("cascade deletion", re.compile(r"\bON\s+DELETE\s+CASCADE\b", re.IGNORECASE)),
    (
        "runtime DELETE grant",
        re.compile(
            r"^\s*GRANT\b[^;]*\bDELETE\b",
            re.IGNORECASE | re.DOTALL | re.MULTILINE,
        ),
    ),
    (
        "authority-bearing database object",
        re.compile(
            r"\b(?:approval_authority|commit_authority|execution_authority|"
            r"control_write_authority)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "cloud or NVIDIA integration",
        re.compile(
            r"\b(?:Amazon|AWS|S3|Object\s+Lock|NVIDIA|NOOA|OpenShell)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "domain-specific registry rule",
        re.compile(
            r"\b(?:Nachweisgesetz|German\s+law|German-law|employment\s+law)\b",
            re.IGNORECASE,
        ),
    ),
    ("machine-specific path", re.compile(r"(?:/home/|/Users/|[A-Za-z]:\\\\)")),
)

STEP10_FORBIDDEN_MIGRATION_PATTERNS: tuple[
    tuple[str, re.Pattern[str]], ...
] = (
    ("role creation", re.compile(r"\bCREATE\s+ROLE\b", re.IGNORECASE)),
    (
        "positive BYPASSRLS grant",
        re.compile(r"(?<!NO)\bBYPASSRLS\b", re.IGNORECASE),
    ),
    ("cascade deletion", re.compile(r"\bON\s+DELETE\s+CASCADE\b", re.IGNORECASE)),
    (
        "runtime DELETE grant",
        re.compile(
            r"^\s*GRANT\b[^;]*\bDELETE\b",
            re.IGNORECASE | re.DOTALL | re.MULTILINE,
        ),
    ),
    (
        "destructive data statement",
        re.compile(r"^\s*(?:DELETE\s+FROM|TRUNCATE|DROP\s+TABLE)\b", re.MULTILINE),
    ),
    (
        "retention bypass",
        re.compile(r"\bBypassGovernanceRetention\b", re.IGNORECASE),
    ),
    (
        "authority-bearing database object",
        re.compile(
            r"\b(?:approval_authority|commit_authority|execution_authority|"
            r"control_write_authority)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "domain-specific ingestion rule",
        re.compile(
            r"\b(?:Nachweisgesetz|German\s+law|German-law|employment\s+law)\b",
            re.IGNORECASE,
        ),
    ),
    ("machine-specific path", re.compile(r"(?:/home/|/Users/|[A-Za-z]:\\\\)")),
)

STEP11_FORBIDDEN_MIGRATION_PATTERNS: tuple[
    tuple[str, re.Pattern[str]], ...
] = (
    ("role creation", re.compile(r"\bCREATE\s+ROLE\b", re.IGNORECASE)),
    (
        "positive BYPASSRLS grant",
        re.compile(r"(?<!NO)\bBYPASSRLS\b", re.IGNORECASE),
    ),
    ("cascade deletion", re.compile(r"\bON\s+DELETE\s+CASCADE\b", re.IGNORECASE)),
    (
        "runtime DELETE grant",
        re.compile(
            r"^\s*GRANT\b[^;]*\bDELETE\b",
            re.IGNORECASE | re.DOTALL | re.MULTILINE,
        ),
    ),
    (
        "destructive data statement",
        re.compile(r"^\s*(?:DELETE\s+FROM|TRUNCATE|DROP\s+TABLE)\b", re.MULTILINE),
    ),
    (
        "retrieval population",
        re.compile(r"\bINSERT\s+INTO\s+memory_patch\.chunk_search_documents\b", re.IGNORECASE),
    ),
    (
        "vector or embedding object",
        re.compile(r"\b(?:VECTOR|EMBEDDING|EMBEDDINGS)\b", re.IGNORECASE),
    ),
    (
        "domain-specific parsing rule",
        re.compile(
            r"\b(?:Nachweisgesetz|German\s+law|German-law|employment\s+law)\b",
            re.IGNORECASE,
        ),
    ),
    ("machine-specific path", re.compile(r"(?:/home/|/Users/|[A-Za-z]:\\\\)")),
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


def parse_go_duration_seconds(value: str) -> float:
    """Parse the bounded non-negative Go durations used by CockroachDB."""

    if not isinstance(value, str) or not value:
        raise MigrationError("shutdown setting is not a duration")
    units = {
        "ns": 1e-9,
        "us": 1e-6,
        "µs": 1e-6,
        "ms": 1e-3,
        "s": 1.0,
        "m": 60.0,
        "h": 3600.0,
    }
    components = tuple(GO_DURATION_COMPONENT_PATTERN.finditer(value))
    if (
        not components
        or "".join(component.group(0) for component in components) != value
    ):
        raise MigrationError("shutdown setting uses an unsupported duration")
    return sum(
        float(component.group(1)) * units[component.group(2)]
        for component in components
    )


def derive_graceful_shutdown_budget(
    settings: Mapping[str, str],
) -> dict[str, Any]:
    """Derive one bounded grace period from every live shutdown phase."""

    if not isinstance(settings, Mapping) or not settings:
        raise MigrationError("CockroachDB shutdown settings are unavailable")
    normalized: dict[str, str] = {}
    for name, value in settings.items():
        if (
            not isinstance(name, str)
            or not name.startswith("server.shutdown.")
            or not isinstance(value, str)
        ):
            raise MigrationError("CockroachDB shutdown settings are malformed")
        normalized[name] = value
    phase_total = sum(
        parse_go_duration_seconds(value) for value in normalized.values()
    )
    calculated = math.ceil(phase_total) + SHUTDOWN_SCHEDULING_CUSHION_SECONDS
    if calculated > MAX_GRACEFUL_SHUTDOWN_SECONDS:
        raise MigrationError(
            "derived CockroachDB shutdown bound exceeds the test-only cap"
        )
    grace_seconds = max(MIN_GRACEFUL_SHUTDOWN_SECONDS, calculated)
    return {
        "calculation": (
            "max(test minimum, ceil(sum(server.shutdown.*)) + scheduling cushion)"
        ),
        "grace_seconds": grace_seconds,
        "minimum_grace_seconds": MIN_GRACEFUL_SHUTDOWN_SECONDS,
        "phase_total_seconds": phase_total,
        "scheduling_cushion_seconds": SHUTDOWN_SCHEDULING_CUSHION_SECONDS,
        "settings": dict(sorted(normalized.items())),
        "test_only_cap_seconds": MAX_GRACEFUL_SHUTDOWN_SECONDS,
    }


def read_graceful_shutdown_budget(client: SqlClient) -> dict[str, Any]:
    """Read all pinned-server shutdown phases and derive their bound."""

    raw = client.execute(
        "defaultdb",
        "SELECT variable, value FROM [SHOW ALL CLUSTER SETTINGS] "
        "WHERE variable LIKE 'server.shutdown.%' ORDER BY variable",
    )
    rows = csv.reader(io.StringIO(raw), delimiter="\t")
    settings = {
        row[0]: row[1]
        for row in rows
        if len(row) >= 2 and row[0].startswith("server.shutdown.")
    }
    return derive_graceful_shutdown_budget(settings)


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

    @staticmethod
    def _client_environment() -> dict[str, str]:
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
        environment["LANG"] = "C"
        environment["LC_ALL"] = "C"
        return environment

    def _node_identity(self) -> dict[str, str]:
        if self.sql_port is None or self.rpc_port is None:
            raise MigrationError("owned CockroachDB ports are unavailable")
        result = run_process(
            [
                str(self.binary),
                "node",
                "status",
                "--insecure",
                f"--host=127.0.0.1:{self.sql_port}",
                "--format=csv",
            ],
            timeout=30,
            environment=self._client_environment(),
        )
        if result.returncode != 0:
            raise MigrationError("owned CockroachDB node status failed")
        rows = list(csv.DictReader(io.StringIO(result.stdout)))
        if len(rows) != 1:
            raise MigrationError("disposable runtime did not expose one node")
        row = rows[0]
        expected_rpc = f"127.0.0.1:{self.rpc_port}"
        expected_sql = f"127.0.0.1:{self.sql_port}"
        if (
            not str(row.get("id", "")).isdigit()
            or row.get("address") != expected_rpc
            or row.get("sql_address") != expected_sql
            or row.get("build") != PINNED_VERSION
            or row.get("is_live") != "true"
        ):
            raise MigrationError("owned CockroachDB node identity differs")
        return {
            "node_id": str(row["id"]),
            "rpc_binding": "127.0.0.1:<owned-rpc-port>",
            "sql_binding": "127.0.0.1:<owned-sql-port>",
        }

    def _runtime_log_evidence(self) -> dict[str, Any]:
        if self.runtime_dir is None:
            return {
                "byte_length": 0,
                "drain_rpc_exit_observed": False,
                "graceful_drain_request_observed": False,
                "graceful_shutdown_completed": False,
                "panic_detected": False,
                "sha256": hashlib.sha256(b"").hexdigest(),
            }
        log_path = self.runtime_dir / "server.log"
        if not log_path.is_file() or log_path.is_symlink():
            return {
                "byte_length": 0,
                "drain_rpc_exit_observed": False,
                "graceful_drain_request_observed": False,
                "graceful_shutdown_completed": False,
                "panic_detected": False,
                "sha256": hashlib.sha256(b"").hexdigest(),
            }
        try:
            payload = log_path.read_bytes()
        except OSError as exc:
            raise MigrationError("owned runtime log could not be inspected") from exc
        text = payload.decode("utf-8", errors="replace")
        return {
            "byte_length": len(payload),
            "drain_rpc_exit_observed": (
                "shutdown requested by drain RPC" in text
            ),
            "graceful_drain_request_observed": (
                "graceful drain request" in text
            ),
            "graceful_shutdown_completed": (
                "server drained and shutdown completed" in text
            ),
            "panic_detected": re.search(
                r"(?i)(?:^|\s)(?:panic:|fatal error:)",
                text,
            )
            is not None,
            "sha256": hashlib.sha256(payload).hexdigest(),
        }

    def _finalize_cleanup(
        self,
        errors: list[str],
        *,
        details: Mapping[str, Any],
        preserve_runtime_on_failure: bool,
    ) -> dict[str, Any]:
        pid = self.process.pid if self.process is not None else None
        if self.log_handle is not None:
            self.log_handle.close()
            self.log_handle = None
        try:
            log_evidence = self._runtime_log_evidence()
        except MigrationError:
            errors.append("OWNED_RUNTIME_LOG_INSPECTION_FAILED")
            log_evidence = {
                "byte_length": 0,
                "drain_rpc_exit_observed": False,
                "graceful_drain_request_observed": False,
                "graceful_shutdown_completed": False,
                "panic_detected": False,
                "sha256": hashlib.sha256(b"").hexdigest(),
            }
        if log_evidence["panic_detected"]:
            errors.append("OWNED_RUNTIME_PANIC_MARKER")
        process_exit_code = details.get("process_exit_code")
        expected_drain_rpc_exit = bool(
            details.get("drain_command_completed")
            and details.get("drain_completion_marker")
            and details.get("drain_shutdown_requested")
            and log_evidence["graceful_shutdown_completed"]
            and log_evidence["drain_rpc_exit_observed"]
        )
        process_exit_accepted = process_exit_code == 0 or (
            process_exit_code == 1 and expected_drain_rpc_exit
        )
        if not process_exit_accepted:
            errors.append("GRACEFUL_SHUTDOWN_EXIT_CODE_MISMATCH")
        pid_exited = self.process is None or self.process.poll() is not None
        if not pid_exited:
            errors.append("OWNED_COCKROACH_PID_REMAINS")
        ports_closed = all(
            port is None or not can_connect("127.0.0.1", port)
            for port in (self.rpc_port, self.sql_port, self.http_port)
        )
        if not ports_closed:
            errors.append("OWNED_LOOPBACK_PORT_REMAINS")
        path_removed = self.runtime_dir is None
        should_preserve = bool(errors) and preserve_runtime_on_failure
        if self.runtime_dir is not None and not should_preserve and pid_exited:
            if self.runtime_dir.exists():
                assert_owned_runtime_path(
                    self.runtime_dir,
                    self.runtime_parent.expanduser().resolve(),
                )
                shutil.rmtree(self.runtime_dir)
            path_removed = not self.runtime_dir.exists()
        elif self.runtime_dir is not None:
            path_removed = not self.runtime_dir.exists()
        if not path_removed and not should_preserve:
            errors.append("OWNED_TEMPORARY_RUNTIME_REMAINS")
        duration = (
            round(time.monotonic() - self.started_at, 3)
            if self.started_at is not None
            else None
        )
        return {
            "cleanup_errors": tuple(dict.fromkeys(errors)),
            "force_kill_used": self.force_kill_used,
            "owned_pid_recorded": pid is not None,
            "panic_detected": bool(log_evidence["panic_detected"]),
            "pid_exited": pid_exited,
            "ports_closed": ports_closed,
            "process_exit_accepted": process_exit_accepted,
            "runtime_duration_seconds": duration,
            "runtime_log_evidence": log_evidence,
            "runtime_preserved_for_diagnostics": should_preserve,
            "temporary_store_removed": path_removed,
            **dict(details),
        }

    def graceful_stop_and_remove(
        self,
        client: SqlClient,
        *,
        owned_children_reaped: bool,
    ) -> dict[str, Any]:
        """Drain the exact owned node, then terminate its exact PID cleanly."""

        errors: list[str] = []
        details: dict[str, Any] = {
            "drain_command_completed": False,
            "drain_completion_marker": False,
            "drain_output_sha256": None,
            "drain_shutdown_requested": False,
            "graceful_shutdown_requested": False,
            "node_identity": None,
            "owned_child_processes_reaped": bool(owned_children_reaped),
            "process_exit_code": None,
            "shutdown_budget": None,
            "shutdown_method": "NODE_DRAIN_SELF_ON_RPC_WITH_SHUTDOWN",
            "sigterm_sent_to_exact_pid": False,
        }
        if not owned_children_reaped:
            errors.append("OWNED_SQL_CHILD_PROCESS_REMAINS")
        grace_seconds = MAX_GRACEFUL_SHUTDOWN_SECONDS
        try:
            budget = read_graceful_shutdown_budget(client)
            details["shutdown_budget"] = budget
            grace_seconds = int(budget["grace_seconds"])
            identity = self._node_identity()
            details["node_identity"] = identity
            if self.rpc_port is None:
                raise MigrationError("owned CockroachDB RPC port is unavailable")
            details["graceful_shutdown_requested"] = True
            details["drain_shutdown_requested"] = True
            started = time.monotonic()
            drained = run_process(
                [
                    str(self.binary),
                    "node",
                    "drain",
                    "--self",
                    "--shutdown",
                    "--insecure",
                    f"--host=127.0.0.1:{self.rpc_port}",
                    f"--drain-wait={grace_seconds}s",
                    "--format=json",
                ],
                timeout=grace_seconds + SHUTDOWN_COMMAND_CUSHION_SECONDS,
                environment=self._client_environment(),
            )
            combined = (drained.stdout + "\n" + drained.stderr).encode("utf-8")
            completion_marker = (
                "drain ok" in drained.stdout
                and "remaining: 0 (complete)" in drained.stderr
            )
            details.update(
                {
                    "drain_command_completed": drained.returncode == 0,
                    "drain_completion_marker": completion_marker,
                    "drain_elapsed_seconds": round(
                        time.monotonic() - started,
                        3,
                    ),
                    "drain_output_sha256": hashlib.sha256(combined).hexdigest(),
                }
            )
            if drained.returncode != 0 or not completion_marker:
                errors.append("BOUNDED_NODE_DRAIN_FAILED")
        except MigrationError:
            errors.append("BOUNDED_NODE_DRAIN_FAILED")
        if self.process is None:
            errors.append("OWNED_COCKROACH_PID_UNAVAILABLE")
        else:
            if self.process.poll() is None:
                try:
                    self.process.wait(timeout=grace_seconds)
                except subprocess.TimeoutExpired:
                    errors.append("GRACEFUL_SHUTDOWN_TIMEOUT")
                    details["sigterm_sent_to_exact_pid"] = True
                    self.process.send_signal(signal.SIGTERM)
                    try:
                        self.process.wait(timeout=grace_seconds)
                    except subprocess.TimeoutExpired:
                        self.force_kill_used = True
                        self.process.kill()
                        try:
                            self.process.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            errors.append("OWNED_COCKROACH_PID_REMAINS")
            details["process_exit_code"] = self.process.returncode
        if self.force_kill_used:
            errors.append("DISPOSABLE_RUNTIME_FORCE_KILL_USED")
        return self._finalize_cleanup(
            errors,
            details=details,
            preserve_runtime_on_failure=True,
        )

    def stop_and_remove(self) -> dict[str, Any]:
        """Emergency legacy cleanup for startup failure and older callers."""

        errors: list[str] = []
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
                    errors.append("OWNED_COCKROACH_PID_REMAINS")
        if self.force_kill_used:
            errors.append("DISPOSABLE_RUNTIME_FORCE_KILL_USED")
        return self._finalize_cleanup(
            errors,
            details={
                "drain_command_completed": False,
                "drain_completion_marker": False,
                "drain_shutdown_requested": False,
                "graceful_shutdown_requested": True,
                "owned_child_processes_reaped": True,
                "shutdown_method": "EXACT_PID_SIGTERM_EMERGENCY_FALLBACK",
                "sigterm_sent_to_exact_pid": True,
            },
            preserve_runtime_on_failure=False,
        )


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
    elif migration_id == STEP6_MIGRATION_ID:
        forbidden_patterns = STEP6_FORBIDDEN_MIGRATION_PATTERNS
        required_fragments = (
            "CREATE TABLE memory_patch.persistence_operations",
            "persistence_operations_shared_idempotency_uq",
            "persistence_operations_private_idempotency_uq",
            "persistence_operations_external_identity_uq",
            "guard_persistence_operation_identity",
            "OWNER TO mp_schema_owner",
            "GRANT SELECT, INSERT, UPDATE",
            "ENABLE ROW LEVEL SECURITY",
            "FORCE ROW LEVEL SECURITY",
            "CREATE POLICY persistence_operations_s6_select",
            "CREATE POLICY persistence_operations_s6_insert",
            "CREATE POLICY persistence_operations_s6_update",
            "TO mp_app_runtime",
        )
        for fragment in required_fragments:
            if fragment not in sql:
                raise MigrationError(
                    f"{migration_id} lacks required persistence fragment: {fragment}"
                )
        if re.search(r"\bTO\s+PUBLIC\b", sql, re.IGNORECASE):
            raise MigrationError("Step 6 policy or grant targets PUBLIC")
        if re.search(
            r"\bUSING\s*\(\s*(?:true|1\s*=\s*1)\s*\)",
            sql,
            re.IGNORECASE,
        ):
            raise MigrationError("Step 6 contains an allow-all RLS policy")
        if re.search(
            r"UNIQUE\s*(?:INDEX)?[^;]*\(\s*external_ref\s*\)",
            sql,
            re.IGNORECASE | re.DOTALL,
        ):
            raise MigrationError("external_ref cannot be globally unique alone")
    elif migration_id == STEP9_MIGRATION_ID:
        forbidden_patterns = STEP9_FORBIDDEN_MIGRATION_PATTERNS
        required_fragments = (
            "CREATE TABLE memory_patch.source_registry_entries",
            "CREATE TABLE memory_patch.source_provenance_edges",
            "CREATE TABLE memory_patch.source_publication_events",
            "guard_source_registry_publication_update",
            "source_registry_entries_s9_publication_guard",
            "OWNER TO mp_schema_owner",
            "GRANT SELECT, INSERT, UPDATE",
            "ENABLE ROW LEVEL SECURITY",
            "FORCE ROW LEVEL SECURITY",
            "CREATE POLICY source_registry_entries_s9_select",
            "CREATE POLICY source_registry_entries_s9_insert",
            "CREATE POLICY source_registry_entries_s9_update",
            "CREATE POLICY source_provenance_edges_s9_select",
            "CREATE POLICY source_provenance_edges_s9_insert",
            "CREATE POLICY source_publication_events_s9_select",
            "CREATE POLICY source_publication_events_s9_insert",
            "source-publication-eligibility-1a",
            "TO mp_app_runtime",
        )
        for fragment in required_fragments:
            if fragment not in sql:
                raise MigrationError(
                    f"{migration_id} lacks required registry fragment: {fragment}"
                )
        if re.search(r"\bTO\s+PUBLIC\b", sql, re.IGNORECASE):
            raise MigrationError("Step 9 policy or grant targets PUBLIC")
        if re.search(
            r"\bUSING\s*\(\s*(?:true|1\s*=\s*1)\s*\)",
            sql,
            re.IGNORECASE,
        ):
            raise MigrationError("Step 9 contains an allow-all RLS policy")
        if len(
            re.findall(
                r"^CREATE TABLE memory_patch\.(?:source_registry_entries|"
                r"source_provenance_edges|source_publication_events)\b",
                sql,
                re.MULTILINE,
            )
        ) != 3:
            raise MigrationError("Step 9 table set is not exact")
    elif migration_id == STEP10_MIGRATION_ID:
        forbidden_patterns = STEP10_FORBIDDEN_MIGRATION_PATTERNS
        required_fragments = (
            "CREATE TABLE memory_patch.ingestion_sagas",
            "CREATE TABLE memory_patch.ingestion_saga_events",
            "CREATE TABLE memory_patch.ingestion_external_effects",
            "CREATE TABLE memory_patch.ingestion_orphans",
            "guard_ingestion_saga_update",
            "guard_ingestion_effect_receipt_update",
            "ingestion_sagas_s10_state_guard",
            "ingestion_external_effects_s10_receipt_guard",
            "OWNER TO mp_schema_owner",
            "GRANT SELECT, INSERT, UPDATE",
            "ENABLE ROW LEVEL SECURITY",
            "FORCE ROW LEVEL SECURITY",
            "CREATE POLICY ingestion_sagas_s10_select",
            "CREATE POLICY ingestion_sagas_s10_insert",
            "CREATE POLICY ingestion_sagas_s10_update",
            "CREATE POLICY ingestion_saga_events_s10_select",
            "CREATE POLICY ingestion_saga_events_s10_insert",
            "CREATE POLICY ingestion_external_effects_s10_select",
            "CREATE POLICY ingestion_external_effects_s10_insert",
            "CREATE POLICY ingestion_external_effects_s10_update",
            "CREATE POLICY ingestion_orphans_s10_select",
            "CREATE POLICY ingestion_orphans_s10_insert",
            "TO mp_app_runtime",
        )
        for fragment in required_fragments:
            if fragment not in sql:
                raise MigrationError(
                    f"{migration_id} lacks required saga fragment: {fragment}"
                )
        if re.search(r"\bTO\s+PUBLIC\b", sql, re.IGNORECASE):
            raise MigrationError("Step 10 policy or grant targets PUBLIC")
        if re.search(
            r"\bUSING\s*\(\s*(?:true|1\s*=\s*1)\s*\)",
            sql,
            re.IGNORECASE,
        ):
            raise MigrationError("Step 10 contains an allow-all RLS policy")
        if len(
            re.findall(
                r"^CREATE TABLE memory_patch\.(?:ingestion_sagas|"
                r"ingestion_saga_events|ingestion_external_effects|"
                r"ingestion_orphans)\b",
                sql,
                re.MULTILINE,
            )
        ) != 4:
            raise MigrationError("Step 10 table set is not exact")
    elif migration_id == STEP11_MIGRATION_ID:
        forbidden_patterns = STEP11_FORBIDDEN_MIGRATION_PATTERNS
        required_fragments = (
            "CREATE TABLE memory_patch.parsed_documents",
            "CREATE TABLE memory_patch.parsed_sections",
            "CREATE TABLE memory_patch.parse_security_findings",
            "OWNER TO mp_schema_owner",
            "GRANT SELECT, INSERT",
            "ENABLE ROW LEVEL SECURITY",
            "FORCE ROW LEVEL SECURITY",
            "CREATE POLICY parsed_documents_s11_select",
            "CREATE POLICY parsed_documents_s11_insert",
            "CREATE POLICY parsed_sections_s11_select",
            "CREATE POLICY parsed_sections_s11_insert",
            "CREATE POLICY parse_security_findings_s11_select",
            "CREATE POLICY parse_security_findings_s11_insert",
            "NORMALIZED_UNICODE_CODE_POINTS_NFC",
            "ON DELETE RESTRICT",
            "TO mp_app_runtime",
        )
        for fragment in required_fragments:
            if fragment not in sql:
                raise MigrationError(
                    f"{migration_id} lacks required parsing fragment: {fragment}"
                )
        if re.search(r"\bTO\s+PUBLIC\b", sql, re.IGNORECASE):
            raise MigrationError("Step 11 policy or grant targets PUBLIC")
        if re.search(
            r"\bUSING\s*\(\s*(?:true|1\s*=\s*1)\s*\)",
            sql,
            re.IGNORECASE,
        ):
            raise MigrationError("Step 11 contains an allow-all RLS policy")
        if len(
            re.findall(
                r"^CREATE TABLE memory_patch\.(?:parsed_documents|"
                r"parsed_sections|parse_security_findings)\b",
                sql,
                re.MULTILINE,
            )
        ) != 3:
            raise MigrationError("Step 11 table set is not exact")
    elif migration_id == STEP12_MIGRATION_ID:
        forbidden_patterns = ()
        for fragment in (
            "CREATE TABLE memory_patch.hat_runtime_bindings",
            "CREATE TABLE memory_patch.hat_registry_entries",
            "CREATE TABLE memory_patch.hat_registry_events",
            "installation_class = 'SYSTEM_INSTALLED'",
            "events are append-only",
            "REVOKE ALL",
            "GRANT SELECT",
        ):
            if fragment not in sql:
                raise MigrationError(f"{migration_id} lacks required registry fragment: {fragment}")
        if "BYPASSRLS" in sql or "CREATE ROLE" in sql or "GRANT DELETE" in sql:
            raise MigrationError("Step 12 weakens global registry security")
    elif migration_id == STEP19_MIGRATION_ID:
        forbidden_patterns = ()
        for fragment in (
            "CREATE TABLE memory_patch.chunk_embeddings",
            "embedding VECTOR(384) NOT NULL",
            "chunk_embeddings_chunk_fk",
            "CREATE VECTOR INDEX chunk_embeddings_vector_l2_idx",
            "embedding vector_l2_ops",
            "CREATE INDEX chunk_embeddings_model_lookup_idx",
            "ENABLE ROW LEVEL SECURITY",
            "FORCE ROW LEVEL SECURITY",
            "CREATE POLICY chunk_embeddings_s19_select",
            "CREATE POLICY chunk_embeddings_s19_insert",
            "memory_patch.hat_scope_context_matches(tenant_id, hat_scope_id)",
            "GRANT SELECT, INSERT",
            "ON DELETE RESTRICT",
        ):
            if fragment not in sql:
                raise MigrationError(
                    f"{migration_id} lacks required vector fragment: {fragment}"
                )
        if re.search(r"\bTO\s+PUBLIC\b", sql, re.IGNORECASE):
            raise MigrationError("Step 19 policy or grant targets PUBLIC")
        if re.search(
            r"\bUSING\s*\(\s*(?:true|1\s*=\s*1)\s*\)",
            sql,
            re.IGNORECASE,
        ):
            raise MigrationError("Step 19 contains an allow-all RLS policy")
        if re.search(
            r"^\s*(?:DELETE\s+FROM|UPDATE\s+memory_patch\.|TRUNCATE|DROP\s+TABLE)\b",
            sql,
            re.IGNORECASE | re.MULTILINE,
        ):
            raise MigrationError("Step 19 mutates pre-existing authority data")
        if len(
            re.findall(
                r"^CREATE TABLE memory_patch\.chunk_embeddings\b",
                sql,
                re.MULTILINE,
            )
        ) != 1:
            raise MigrationError("Step 19 table set is not exact")
    elif migration_id == STEP27_MIGRATION_ID:
        forbidden_patterns = ()
        for fragment in (
            "CREATE TABLE memory_patch.personal_memory_quota_policies",
            "ALTER TABLE memory_patch.personal_memory_spaces",
            "ALTER TABLE memory_patch.personal_memory_model_bindings",
            "guard_personal_memory_step27_update",
            "personal_memory_spaces_s27_state_guard",
            "personal_memory_quota_policies_owner_fk",
            "personal_memory_spaces_s27_quota_fk",
            "personal_memory_model_bindings_s27_typed_tuple",
            "OWNER TO mp_schema_owner",
            "GRANT SELECT, INSERT",
            "ENABLE ROW LEVEL SECURITY",
            "FORCE ROW LEVEL SECURITY",
            "CREATE POLICY personal_memory_quota_policies_s27_select",
            "CREATE POLICY personal_memory_quota_policies_s27_insert",
            "memory_patch.user_context_matches(tenant_id, owner_user_id)",
            "ON DELETE RESTRICT",
        ):
            if fragment not in sql:
                raise MigrationError(
                    f"{migration_id} lacks required Personal Memory fragment: "
                    f"{fragment}"
                )
        if re.search(r"\bTO\s+PUBLIC\b", sql, re.IGNORECASE):
            raise MigrationError("Step 27 policy or grant targets PUBLIC")
        if re.search(
            r"\bUSING\s*\(\s*(?:true|1\s*=\s*1)\s*\)",
            sql,
            re.IGNORECASE,
        ):
            raise MigrationError("Step 27 contains an allow-all RLS policy")
        if re.search(r"\b(?:BYPASSRLS|CREATE\s+ROLE|ON\s+DELETE\s+CASCADE)\b", sql, re.I):
            raise MigrationError("Step 27 weakens a database security boundary")
        if len(
            re.findall(
                r"^CREATE TABLE memory_patch\.personal_memory_quota_policies\b",
                sql,
                re.MULTILINE,
            )
        ) != 1:
            raise MigrationError("Step 27 table set is not exact")
    elif migration_id == STEP28_MIGRATION_ID:
        forbidden_patterns = ()
        for fragment in (
            "DROP CONSTRAINT memory_patch_proposals_origin",
            "ADD CONSTRAINT memory_patch_proposals_origin",
            "'KNOWLEDGE_KERNEL'",
            "step28_candidate_slot_is_eligible",
            "step28_candidate_target_matches",
            "step28_personal_hat_scope_id",
            "enforce_step28_candidate_quota",
            "guard_step28_slot_authority_update",
            "candidate_quota_epoch",
            "personal_memory_spaces_s28_authority_tuple",
            "hat_scope_id = memory_patch.step28_personal_hat_scope_id(",
            "space.hat_scope_id = p_hat_scope_id",
            "space.slot_hash = p_slot_hash",
            "HAVING count(*) >= 128",
            "octet_length((NEW).proposed_content::STRING) > 8388608",
            "> 8388608 - octet_length((NEW).proposed_content::STRING)",
            "BEFORE INSERT ON memory_patch.memory_patch_proposals",
            "memory_patch_proposals_s28_candidate_quota",
            "personal_memory_spaces_s28_authority_guard",
            "SECURITY INVOKER",
            "OWNER TO mp_schema_owner",
            "REVOKE ALL ON FUNCTION",
            "GRANT EXECUTE ON FUNCTION",
            "space.state IN ('CONFIGURED', 'ACTIVE')",
            "GRANT INSERT ON TABLE",
            "REVOKE UPDATE, DELETE ON TABLE",
            "ENABLE ROW LEVEL SECURITY",
            "FORCE ROW LEVEL SECURITY",
            "CREATE POLICY hat_scopes_s28_personal_insert",
            "CREATE POLICY memory_patch_proposals_s28_insert",
            "origin IN ('KNOWLEDGE_KERNEL', 'CRITIC_PROMPT_LOOP')",
            "lifecycle_state = 'DETECTED'",
            "approval_requirement = 'OWNER'",
            "requested_trust_class = 'MODEL_EXPERIENCE_HINT'",
            "content_kind = 'MODEL_EXPERIENCE'",
            "proposed_content ->> 'contract_type' = 'CorrectionCandidateEnvelope'",
            "proposed_content ->> 'schema_version' = '1.0.0'",
            "proposal_id = proposed_content ->> 'candidate_id'",
            "content_hash = proposed_content ->> 'envelope_hash'",
            "'target_slot_binding'",
            "'available_evidence_references'",
            "'effective_scope'",
            "scope_dimensions = (",
            "origin = (",
            "binding.binding_digest = p_model_binding_hash",
            "p_binding_mode = 'EXACT_MODEL'",
            "p_model_binding_enabled IS TRUE",
            "memory_patch.user_context_matches",
        ):
            if fragment not in sql:
                raise MigrationError(
                    f"{migration_id} lacks required candidate fragment: {fragment}"
                )
        if re.search(r"\bTO\s+PUBLIC\b", sql, re.IGNORECASE):
            raise MigrationError("Step 28 policy or grant targets PUBLIC")
        if re.search(
            r"\b(?:USING|WITH\s+CHECK)\s*\(\s*(?:true|1\s*=\s*1)\s*\)",
            sql,
            re.IGNORECASE,
        ):
            raise MigrationError("Step 28 contains an allow-all RLS policy")
        if re.search(
            r"\b(?:BYPASSRLS|CREATE\s+ROLE|ON\s+DELETE\s+CASCADE)\b",
            sql,
            re.IGNORECASE,
        ):
            raise MigrationError("Step 28 weakens a database security boundary")
        if re.search(
            r"^\s*(?:CREATE\s+TABLE|DELETE\s+FROM|TRUNCATE|DROP\s+TABLE)\b",
            sql,
            re.IGNORECASE | re.MULTILINE,
        ):
            raise MigrationError("Step 28 exceeds the bounded existing-table delta")
        if re.search(
            r"^\s*UPDATE\s+memory_patch\.(?!personal_memory_spaces\b)",
            sql,
            re.IGNORECASE | re.MULTILINE,
        ):
            raise MigrationError("Step 28 updates a non-slot authority table")
        if re.search(
            r"^CREATE POLICY (?!hat_scopes_s28_personal_insert\b|"
            r"memory_patch_proposals_s28_insert\b)",
            sql,
            re.MULTILINE,
        ):
            raise MigrationError("Step 28 policy set is not exact")
    elif migration_id == STEP29_MIGRATION_ID:
        forbidden_patterns = ()
        for fragment in (
            "step29_dedup_key STRING",
            "step29_candidate_id STRING",
            "memory_patch_proposals_s29_candidate_projection",
            "memory_patch_proposals_s29_candidate_lineage_unique",
            "memory_patch_proposals_s29_candidate_lineage_fk",
            "memory_patch_proposals_s29_state_tuple",
            "memory_patch_proposals_s29_exact_dedup",
            "step29_proposal_target_matches",
            "step29_transition_target_matches",
            "enforce_step29_proposal_quota",
            "guard_step29_proposal_transition",
            "candidate_quota_epoch = space.candidate_quota_epoch + 1",
            "IF NOT EXISTS (",
            "FROM memory_patch.personal_memory_spaces AS current_space",
            "HAVING count(*) >= 128",
            "octet_length((NEW).proposed_content::STRING) > 8388608",
            "BEFORE INSERT OR UPDATE ON memory_patch.memory_patch_proposals",
            "proposal.proposal_id <> (NEW).proposal_id",
            "proposed_content -> 'evidence_binding' ->> 'binding_hash'",
            "proposed_content -> 'validation_receipt' ->> 'receipt_hash'",
            "BEFORE UPDATE ON memory_patch.memory_patch_proposals",
            "GRANT UPDATE ON TABLE memory_patch.memory_patch_proposals",
            "GRANT INSERT ON TABLE memory_patch.patch_transition_records",
            "REVOKE DELETE ON TABLE",
            "ENABLE ROW LEVEL SECURITY",
            "FORCE ROW LEVEL SECURITY",
            "CREATE POLICY memory_patch_proposals_s29_insert",
            "CREATE POLICY memory_patch_proposals_s29_update",
            "CREATE POLICY patch_transition_records_s29_insert",
            "PersonalMemoryPatchProposalState",
            "lifecycle_state = 'PROPOSED'",
            "lifecycle_state IN ('PROPOSED', 'EVIDENCE_BOUND', 'VALIDATED')",
            "'AWAITING_APPROVAL'",
            "state_before = 'DETECTED' AND state_after = 'PROPOSED'",
            "state_before = 'PROPOSED' AND state_after = 'EVIDENCE_BOUND'",
            "state_before = 'EVIDENCE_BOUND' AND state_after = 'VALIDATED'",
            "state_before = 'VALIDATED' AND state_after = 'AWAITING_APPROVAL'",
            "requested_trust_class = 'PERSONAL_VERIFIED_PATCH'",
            "approval_requirement = 'OWNER'",
            "content_kind = 'FACTUAL'",
            "memory_patch.user_context_matches",
            "memory_patch.step28_candidate_target_matches",
            "SECURITY INVOKER",
            "OWNER TO mp_schema_owner",
            "REVOKE ALL ON FUNCTION",
            "GRANT EXECUTE ON FUNCTION",
        ):
            if fragment not in sql:
                raise MigrationError(
                    f"{migration_id} lacks required proposal fragment: {fragment}"
                )
        if re.search(r"\bTO\s+PUBLIC\b", sql, re.IGNORECASE):
            raise MigrationError("Step 29 policy or grant targets PUBLIC")
        if re.search(
            r"\b(?:USING|WITH\s+CHECK)\s*\(\s*(?:true|1\s*=\s*1)\s*\)",
            sql,
            re.IGNORECASE,
        ):
            raise MigrationError("Step 29 contains an allow-all RLS policy")
        if re.search(
            r"\b(?:BYPASSRLS|CREATE\s+ROLE|ON\s+DELETE\s+CASCADE)\b",
            sql,
            re.IGNORECASE,
        ):
            raise MigrationError("Step 29 weakens a database security boundary")
        if re.search(
            r"^\s*(?:CREATE\s+TABLE|DELETE\s+FROM|TRUNCATE|DROP\s+TABLE)\b",
            sql,
            re.IGNORECASE | re.MULTILINE,
        ):
            raise MigrationError("Step 29 exceeds the bounded existing-table delta")
        if re.search(
            r"(?:lifecycle_state|state_after)\s*=\s*"
            r"'(?:APPROVED|COMMITTED|ACTIVE)'",
            sql,
        ):
            raise MigrationError("Step 29 migration enters a Step 30 lifecycle")
        policies = re.findall(r"^CREATE POLICY ([a-z0-9_]+)", sql, re.MULTILINE)
        if policies != [
            "memory_patch_proposals_s29_insert",
            "memory_patch_proposals_s29_update",
            "patch_transition_records_s29_insert",
        ]:
            raise MigrationError("Step 29 policy set is not exact")
    elif migration_id == STEP30_MIGRATION_ID:
        forbidden_patterns = ()
        for fragment in (
            "CREATE ROLE IF NOT EXISTS mp_personal_memory_commit_helper",
            "NOLOGIN NOCREATEROLE NOCREATEDB NOBYPASSRLS",
            "step30_approval_receipt_hash STRING",
            "step30_commit_receipt_hash STRING",
            "step30_activation_receipt_hash STRING",
            "step30_receipt_hash STRING",
            "memory_patch_approvals_s30_replay_uq",
            "memory_patch_commits_s30_replay_uq",
            "memory_items_s30_activation_replay_uq",
            "step30_commit_helper_authorized",
            "guard_step30_commit_slot_update",
            "personal_memory_spaces_s30_commit_guard",
            "guard_step30_proposal_transition",
            "guard_step30_memory_item",
            "step30_approval_transition_matches",
            "step30_memory_item_commit_matches",
            "step30_proposal_lifecycle_matches",
            "AWAITING_APPROVAL'",
            "APPROVED'",
            "COMMITTED'",
            "ACTIVE'",
            "PersonalMemoryPatchLifecycleState",
            "HUMAN_USER",
            "personal-memory-commit-helper-1a",
            "mp_personal_memory_commit_helper",
            "CREATE POLICY memory_patch_approvals_s30_insert",
            "CREATE POLICY memory_patch_commits_s30_insert",
            "CREATE POLICY memory_items_s30_commit_insert",
            "CREATE POLICY memory_items_s30_activate_update",
            "FORCE ROW LEVEL SECURITY",
        ):
            if fragment not in sql:
                raise MigrationError(
                    f"{migration_id} lacks required Step 30 fragment: {fragment}"
                )
        cluster_role_sql, database_sql = split_cluster_role_ddl(
            sql,
            begin_marker=STEP30_CLUSTER_ROLE_BEGIN,
            end_marker=STEP30_CLUSTER_ROLE_END,
            role_name="mp_personal_memory_commit_helper",
        )
        if "CREATE ROLE" in database_sql:
            raise MigrationError("Step 30 database transaction contains role creation")
        if re.search(r"\bTO\s+PUBLIC\b", database_sql, re.IGNORECASE):
            raise MigrationError("Step 30 policy or grant targets PUBLIC")
        if re.search(
            r"\b(?:USING|WITH\s+CHECK)\s*\(\s*(?:true|1\s*=\s*1)\s*\)",
            database_sql,
            re.IGNORECASE,
        ):
            raise MigrationError("Step 30 contains an allow-all RLS policy")
        if re.search(r"\bON\s+DELETE\s+CASCADE\b", database_sql, re.IGNORECASE):
            raise MigrationError("Step 30 introduces cascade deletion")
        if re.search(r"^\s*(?:CREATE\s+TABLE|DELETE\s+FROM|TRUNCATE)\b", database_sql, re.I | re.M):
            raise MigrationError("Step 30 exceeds the bounded existing-table delta")
        if re.search(
            r"active_patch_retrieval|cross_model_reuse|external_action",
            database_sql,
            re.IGNORECASE,
        ):
            raise MigrationError("Step 30 leaks a later-step or execution boundary")
    else:
        raise MigrationError(f"unrecognized migration security generation: {migration_id}")
    for description, pattern in forbidden_patterns:
        if pattern.search(sql):
            raise MigrationError(f"{migration_id} contains forbidden {description}")


def load_migrations() -> list[Migration]:
    manifest = load_json(MIGRATION_MANIFEST_PATH)
    if manifest.get("schema_version") != 12:
        raise MigrationError("migration manifest schema_version must be 12")
    if (
        manifest.get("manifest_id")
        != "memory-patch-step30-user-approval-commit-activation-1a"
    ):
        raise MigrationError("migration manifest identity mismatch")
    if manifest.get("runner_version") != RUNNER_VERSION:
        raise MigrationError("migration manifest runner_version mismatch")
    if manifest.get("target_database") != "CockroachDB":
        raise MigrationError("migration manifest must target CockroachDB")
    if manifest.get("target_version") != PINNED_VERSION:
        raise MigrationError("migration manifest target version mismatch")
    if manifest.get("transaction_policy") != (
        "STEP4_ONE_TRANSACTION_STEP5_NINE_IDEMPOTENT_DATABASE_PHASES_"
        "WITH_NONATOMIC_CLUSTER_ROLE_DDL_STEP6_ONE_TRANSACTION_"
        "STEP9_ONE_TRANSACTION_STEP10_ONE_TRANSACTION_"
        "STEP11_ONE_TRANSACTION_STEP12_ONE_TRANSACTION_"
        "STEP19_ONE_TRANSACTION_STEP27_ONE_TRANSACTION_"
        "STEP28_ONE_TRANSACTION_STEP29_ONE_TRANSACTION_"
        "STEP30_ONE_TRANSACTION_WITH_NONATOMIC_CLUSTER_ROLE_DDL"
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
        if (
            migration_id == STEP5_MIGRATION_ID
            and checksum != STEP5_MIGRATION_SHA256
        ):
            raise MigrationError("immutable Step 5 checksum changed")
        if (
            migration_id == STEP6_MIGRATION_ID
            and checksum != STEP6_MIGRATION_SHA256
        ):
            raise MigrationError("immutable Step 6 checksum changed")
        if (
            migration_id == STEP9_MIGRATION_ID
            and checksum != STEP9_MIGRATION_SHA256
        ):
            raise MigrationError("Step 9 checksum differs from the audited migration")
        if (
            migration_id == STEP10_MIGRATION_ID
            and checksum != STEP10_MIGRATION_SHA256
        ):
            raise MigrationError("Step 10 checksum differs from the audited migration")
        if (
            migration_id == STEP11_MIGRATION_ID
            and checksum != STEP11_MIGRATION_SHA256
        ):
            raise MigrationError("Step 11 checksum differs from the audited migration")
        if migration_id == STEP12_MIGRATION_ID and checksum != STEP12_MIGRATION_SHA256:
            raise MigrationError("Step 12 checksum differs from the audited migration")
        if migration_id == STEP19_MIGRATION_ID and checksum != STEP19_MIGRATION_SHA256:
            raise MigrationError("Step 19 checksum differs from the audited migration")
        if migration_id == STEP27_MIGRATION_ID and checksum != STEP27_MIGRATION_SHA256:
            raise MigrationError("Step 27 checksum differs from the audited migration")
        if migration_id == STEP28_MIGRATION_ID and checksum != STEP28_MIGRATION_SHA256:
            raise MigrationError("Step 28 checksum differs from the audited migration")
        if migration_id == STEP29_MIGRATION_ID and checksum != STEP29_MIGRATION_SHA256:
            raise MigrationError("Step 29 checksum differs from the audited migration")
        if migration_id == STEP30_MIGRATION_ID and checksum != STEP30_MIGRATION_SHA256:
            raise MigrationError("Step 30 checksum differs from the audited migration")
        migrations.append(Migration(migration_id, filename, checksum, path, sql))
        seen.add(migration_id)
    identifiers = [migration.migration_id for migration in migrations]
    if identifiers != sorted(identifiers):
        raise MigrationError("migrations are not in deterministic identifier order")
    discovered = {path.name for path in MIGRATION_ROOT.glob("*.sql")}
    declared = {migration.filename for migration in migrations}
    if discovered != declared:
        raise MigrationError("migration manifest and SQL directory differ")
    expected_identifiers = [
        *STEP4_MIGRATION_HASHES,
        STEP5_MIGRATION_ID,
        STEP6_MIGRATION_ID,
        STEP9_MIGRATION_ID,
        STEP10_MIGRATION_ID,
        STEP11_MIGRATION_ID,
        STEP12_MIGRATION_ID,
        STEP19_MIGRATION_ID,
        STEP27_MIGRATION_ID,
        STEP28_MIGRATION_ID,
        STEP29_MIGRATION_ID,
        STEP30_MIGRATION_ID,
    ]
    if identifiers != expected_identifiers:
        raise MigrationError(
            "migration chain is not the exact Step 4 -> Step 5 -> Step 6 -> "
            "Step 9 -> Step 10 -> Step 11 -> Step 12 -> Step 19 -> "
            "Step 27 -> Step 28 -> Step 29 -> Step 30 chain"
        )
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


def load_persistence_manifest() -> dict[str, Any]:
    manifest = load_json(PERSISTENCE_MANIFEST_PATH)
    if manifest.get("schema_version") != 1:
        raise MigrationError("persistence manifest schema_version must be 1")
    if (
        manifest.get("manifest_id")
        != "memory-patch-step6-persistence-security-1a"
    ):
        raise MigrationError("persistence manifest identity mismatch")
    if manifest.get("target_cockroachdb_version") != PINNED_VERSION:
        raise MigrationError("persistence manifest version pin mismatch")
    if manifest.get("fixed_roles") != [
        "mp_app_runtime",
        "mp_request_context_setter",
        "mp_schema_owner",
        "mp_security_owner",
    ]:
        raise MigrationError("persistence manifest fixed role set is not exact")
    if manifest.get("status_vocabulary") != [
        "PENDING",
        "IN_PROGRESS",
        "COMPLETED",
        "INTERRUPTED",
        "FAILED_FINAL",
    ]:
        raise MigrationError("persistence status vocabulary is not exact")
    if manifest.get("external_reference_identity") != [
        "tenant_id",
        "origin_kind",
        "origin_system",
        "origin_version",
        "adapter_version",
        "artifact_kind",
        "external_ref",
    ]:
        raise MigrationError("external reference identity is incomplete")
    tables = manifest.get("tables")
    if not isinstance(tables, list) or len(tables) != 1:
        raise MigrationError("Step 6 must declare exactly one persistence table")
    table = tables[0]
    required_fields = {
        "access_class",
        "delete_policy",
        "force_rls",
        "immutable_columns",
        "insert_policy",
        "owner_role",
        "rls_enabled",
        "runtime_privileges",
        "select_policy",
        "table",
        "tenant_column",
        "update_policy",
        "user_owner_column",
    }
    if not isinstance(table, dict) or set(table) != required_fields:
        raise MigrationError("persistence table classification has invalid shape")
    if (
        table["table"] != "persistence_operations"
        or table["tenant_column"] != "tenant_id"
        or table["user_owner_column"] != "owner_user_id"
        or table["owner_role"] != "mp_schema_owner"
        or table["rls_enabled"] is not True
        or table["force_rls"] is not True
        or table["delete_policy"] is not None
        or table["runtime_privileges"] != ["INSERT", "SELECT", "UPDATE"]
        or table["select_policy"] != "persistence_operations_s6_select"
        or table["insert_policy"] != "persistence_operations_s6_insert"
        or table["update_policy"] != "persistence_operations_s6_update"
    ):
        raise MigrationError("persistence table security decision is unsafe")
    immutable = table["immutable_columns"]
    if (
        not isinstance(immutable, list)
        or len(immutable) != len(set(immutable))
        or not {
            "tenant_id",
            "operation_id",
            "owner_user_id",
            "operation_kind",
            "idempotency_key",
            "request_digest",
            "scope_digest",
            "created_at",
            "origin_system",
            "external_ref",
        }.issubset(immutable)
    ):
        raise MigrationError("persistence immutable identity is incomplete")
    return manifest


def load_source_registry_manifest() -> dict[str, Any]:
    manifest = load_json(SOURCE_REGISTRY_MANIFEST_PATH)
    if manifest.get("schema_version") != 1:
        raise MigrationError("source registry manifest schema_version must be 1")
    if (
        manifest.get("manifest_id")
        != "memory-patch-step9-source-registry-policy-1a"
    ):
        raise MigrationError("source registry manifest identity mismatch")
    if manifest.get("target_cockroachdb_version") != PINNED_VERSION:
        raise MigrationError("source registry manifest version pin mismatch")
    if (
        manifest.get("policy_version")
        != "source-publication-eligibility-1a"
    ):
        raise MigrationError("source publication policy version is not exact")
    if (
        manifest.get("genesis_marker") != "SOURCE_PUBLICATION_GENESIS_1A"
        or manifest.get("genesis_digest")
        != "6d6e54df2447ab416012f2afbf0cdf857d2055a3ef05a3d9023b8561b20c9693"
    ):
        raise MigrationError("source publication genesis identity differs")
    if manifest.get("fixed_roles") != [
        "mp_app_runtime",
        "mp_request_context_setter",
        "mp_schema_owner",
        "mp_security_owner",
    ]:
        raise MigrationError("source registry fixed role set differs")
    expected_vocabularies = {
        "authority_levels": [
            "OFFICIAL_PRIMARY",
            "AUTHORITATIVE_SECONDARY",
            "INFORMATIONAL_SECONDARY",
            "USER_SUPPLIED",
            "DERIVED",
            "UNKNOWN",
        ],
        "license_statuses": [
            "PUBLIC_DOMAIN",
            "CONFIRMED_PERMISSIVE",
            "CONFIRMED_RESTRICTED",
            "PRIVATE_AUTHORIZED",
            "UNKNOWN",
            "PROHIBITED",
        ],
        "access_classes": [
            "PUBLIC",
            "TENANT_RESTRICTED",
            "USER_PRIVATE",
        ],
        "redaction_states": [
            "NOT_REQUIRED",
            "PENDING",
            "VERIFIED",
            "REJECTED",
        ],
        "publication_actor_types": [
            "TRUSTED_APPLICATION",
            "HUMAN_REVIEWER",
            "MIGRATION_SERVICE",
        ],
        "publication_states": [
            "REGISTERED",
            "REVIEW_REQUIRED",
            "ELIGIBLE",
            "PUBLISHED",
            "QUARANTINED",
            "WITHDRAWN",
            "REJECTED",
        ],
    }
    for field, expected in expected_vocabularies.items():
        if manifest.get(field) != expected:
            raise MigrationError(f"source registry {field} vocabulary differs")
    if manifest.get("transitions") != {
        "REGISTERED": ["REVIEW_REQUIRED", "QUARANTINED", "REJECTED"],
        "REVIEW_REQUIRED": ["ELIGIBLE", "QUARANTINED", "REJECTED"],
        "ELIGIBLE": ["PUBLISHED", "REVIEW_REQUIRED", "QUARANTINED"],
        "PUBLISHED": ["WITHDRAWN", "QUARANTINED"],
        "QUARANTINED": ["REVIEW_REQUIRED", "REJECTED"],
        "WITHDRAWN": ["REVIEW_REQUIRED"],
        "REJECTED": [],
    }:
        raise MigrationError("source publication transition graph differs")
    tables = manifest.get("tables")
    if (
        not isinstance(tables, list)
        or [row.get("table") for row in tables if isinstance(row, dict)]
        != [
            "source_registry_entries",
            "source_provenance_edges",
            "source_publication_events",
        ]
    ):
        raise MigrationError("source registry table classification is not exact")
    expected_privileges = {
        "source_registry_entries": ["INSERT", "SELECT", "UPDATE"],
        "source_provenance_edges": ["INSERT", "SELECT"],
        "source_publication_events": ["INSERT", "SELECT"],
    }
    expected_policies = {
        "source_registry_entries": (
            "source_registry_entries_s9_select",
            "source_registry_entries_s9_insert",
            "source_registry_entries_s9_update",
        ),
        "source_provenance_edges": (
            "source_provenance_edges_s9_select",
            "source_provenance_edges_s9_insert",
            None,
        ),
        "source_publication_events": (
            "source_publication_events_s9_select",
            "source_publication_events_s9_insert",
            None,
        ),
    }
    for table in tables:
        name = table["table"]
        if (
            table.get("tenant_column") != "tenant_id"
            or table.get("owner_role") != "mp_schema_owner"
            or table.get("rls_enabled") is not True
            or table.get("force_rls") is not True
            or table.get("delete_policy") is not None
            or table.get("runtime_privileges") != expected_privileges[name]
            or (
                table.get("select_policy"),
                table.get("insert_policy"),
                table.get("update_policy"),
            )
            != expected_policies[name]
            or not isinstance(table.get("immutable_columns"), list)
            or table["immutable_columns"] != sorted(table["immutable_columns"])
        ):
            raise MigrationError(f"unsafe source registry table decision: {name}")
    return manifest


def load_ingestion_saga_manifest() -> dict[str, Any]:
    manifest = load_json(INGESTION_SAGA_MANIFEST_PATH)
    if manifest.get("schema_version") != 1:
        raise MigrationError("ingestion saga manifest schema_version must be 1")
    if (
        manifest.get("manifest_id")
        != "memory-patch-step10-ingestion-saga-security-1a"
    ):
        raise MigrationError("ingestion saga manifest identity mismatch")
    if manifest.get("target_cockroachdb_version") != PINNED_VERSION:
        raise MigrationError("ingestion saga manifest version pin mismatch")
    if manifest.get("fixed_roles") != [
        "mp_app_runtime",
        "mp_request_context_setter",
        "mp_schema_owner",
        "mp_security_owner",
    ]:
        raise MigrationError("ingestion saga fixed role set differs")
    if manifest.get("milestones") != [
        "REGISTERED",
        "ACQUIRED_LOCAL",
        "HASH_VERIFIED",
        "SNAPSHOT_UPLOAD_PENDING",
        "SNAPSHOT_UPLOADED",
        "SNAPSHOT_LOCK_VERIFIED",
        "PARSED",
        "VALIDATED",
        "PUBLISHED",
    ]:
        raise MigrationError("ingestion milestone vocabulary differs")
    if manifest.get("execution_dispositions") != [
        "READY",
        "CLAIMED",
        "RETRY_WAIT",
        "OPERATOR_REVIEW",
        "QUARANTINED",
        "COMPLETED",
    ]:
        raise MigrationError("ingestion disposition vocabulary differs")
    if (
        manifest.get("genesis_digest")
        != "bcb38ae02e4305d0bec56081a371aeec436fec4a60e09bfbf4c7b3818f1e8b93"
    ):
        raise MigrationError("ingestion saga genesis digest differs")
    tables = manifest.get("tables")
    if not isinstance(tables, list) or [
        row.get("table") for row in tables if isinstance(row, dict)
    ] != [
        "ingestion_sagas",
        "ingestion_saga_events",
        "ingestion_external_effects",
        "ingestion_orphans",
    ]:
        raise MigrationError("ingestion saga table manifest differs")
    expected_privileges = {
        "ingestion_sagas": ["INSERT", "SELECT", "UPDATE"],
        "ingestion_saga_events": ["INSERT", "SELECT"],
        "ingestion_external_effects": ["INSERT", "SELECT", "UPDATE"],
        "ingestion_orphans": ["INSERT", "SELECT"],
    }
    for row in tables:
        name = row["table"]
        if (
            row.get("owner_role") != "mp_schema_owner"
            or row.get("force_rls") is not True
            or row.get("runtime_privileges") != expected_privileges[name]
            or not isinstance(row.get("select_policy"), str)
            or not isinstance(row.get("insert_policy"), str)
            or (
                ("UPDATE" in expected_privileges[name])
                != isinstance(row.get("update_policy"), str)
            )
        ):
            raise MigrationError(f"unsafe ingestion saga table decision: {name}")
    triggers = manifest.get("triggers")
    if triggers != [
        {
            "function": "guard_ingestion_saga_update",
            "table": "ingestion_sagas",
            "trigger": "ingestion_sagas_s10_state_guard",
        },
        {
            "function": "guard_ingestion_effect_receipt_update",
            "table": "ingestion_external_effects",
            "trigger": "ingestion_external_effects_s10_receipt_guard",
        },
    ]:
        raise MigrationError("ingestion saga trigger manifest differs")
    return manifest


def load_parsing_pipeline_manifest() -> dict[str, Any]:
    manifest = load_json(PARSING_PIPELINE_MANIFEST_PATH)
    if manifest.get("schema_version") != 1:
        raise MigrationError("parsing pipeline manifest schema_version must be 1")
    if (
        manifest.get("manifest_id")
        != "memory-patch-step11-parsing-pipeline-security-1a"
    ):
        raise MigrationError("parsing pipeline manifest identity mismatch")
    if manifest.get("target_cockroachdb_version") != PINNED_VERSION:
        raise MigrationError("parsing pipeline manifest version pin mismatch")
    if manifest.get("fixed_roles") != [
        "mp_app_runtime",
        "mp_request_context_setter",
        "mp_schema_owner",
        "mp_security_owner",
    ]:
        raise MigrationError("parsing pipeline fixed role set differs")
    if manifest.get("offset_basis") != "NORMALIZED_UNICODE_CODE_POINTS_NFC":
        raise MigrationError("parsing pipeline offset basis differs")
    if manifest.get("runtime_delete") is not False:
        raise MigrationError("parsing pipeline must prohibit runtime DELETE")
    tables = manifest.get("tables")
    if not isinstance(tables, list) or [
        row.get("table") for row in tables if isinstance(row, dict)
    ] != [
        "parse_security_findings",
        "parsed_documents",
        "parsed_sections",
    ]:
        raise MigrationError("parsing pipeline table manifest differs")
    expected_policies = {
        "parse_security_findings": (
            "parse_security_findings_s11_select",
            "parse_security_findings_s11_insert",
        ),
        "parsed_documents": (
            "parsed_documents_s11_select",
            "parsed_documents_s11_insert",
        ),
        "parsed_sections": (
            "parsed_sections_s11_select",
            "parsed_sections_s11_insert",
        ),
    }
    for row in tables:
        name = row["table"]
        if (
            row.get("owner_role") != "mp_schema_owner"
            or row.get("force_rls") is not True
            or row.get("runtime_privileges") != ["INSERT", "SELECT"]
            or (row.get("select_policy"), row.get("insert_policy"))
            != expected_policies[name]
            or row.get("update_policy") is not None
        ):
            raise MigrationError(f"unsafe parsing pipeline table decision: {name}")
    return manifest


def load_correction_candidate_security_manifest() -> dict[str, Any]:
    manifest = load_json(CORRECTION_CANDIDATE_SECURITY_MANIFEST_PATH)
    if set(manifest) != {
        "allowed_candidate_origins",
        "candidate_lifecycle_state",
        "fixed_roles",
        "helper_functions",
        "manifest_id",
        "quota_guard",
        "runtime_update_delete",
        "schema_version",
        "slot_authority",
        "tables",
        "target_cockroachdb_version",
    }:
        raise MigrationError("candidate security manifest shape is invalid")
    if manifest.get("schema_version") != 1:
        raise MigrationError("candidate security manifest schema_version must be 1")
    if (
        manifest.get("manifest_id")
        != "memory-patch-step28-correction-candidate-security-1a"
    ):
        raise MigrationError("candidate security manifest identity mismatch")
    if manifest.get("target_cockroachdb_version") != PINNED_VERSION:
        raise MigrationError("candidate security manifest version pin mismatch")
    if manifest.get("fixed_roles") != [
        "mp_app_runtime",
        "mp_request_context_setter",
        "mp_schema_owner",
        "mp_security_owner",
    ]:
        raise MigrationError("candidate security fixed role set differs")
    if manifest.get("allowed_candidate_origins") != [
        "CRITIC_PROMPT_LOOP",
        "KNOWLEDGE_KERNEL",
    ]:
        raise MigrationError("candidate producer set is not exact")
    if manifest.get("candidate_lifecycle_state") != "DETECTED":
        raise MigrationError("candidate lifecycle boundary is not DETECTED")
    if manifest.get("runtime_update_delete") is not False:
        raise MigrationError("candidate runtime UPDATE/DELETE must remain denied")
    functions = manifest.get("helper_functions")
    if functions != [
        {
            "function": "step28_candidate_slot_is_eligible",
            "owner_role": "mp_schema_owner",
            "runtime_privileges": ["EXECUTE"],
            "security_type": "INVOKER",
        },
        {
            "function": "step28_candidate_target_matches",
            "owner_role": "mp_schema_owner",
            "runtime_privileges": ["EXECUTE"],
            "security_type": "INVOKER",
        },
        {
            "function": "step28_personal_hat_scope_id",
            "owner_role": "mp_schema_owner",
            "runtime_privileges": ["EXECUTE"],
            "security_type": "INVOKER",
        },
    ]:
        raise MigrationError("candidate helper-function boundary is not exact")
    quota_guard = manifest.get("quota_guard")
    if quota_guard != {
        "byte_limit": 8 * 1024 * 1024,
        "count_limit": 128,
        "function": "enforce_step28_candidate_quota",
        "function_owner_role": "mp_schema_owner",
        "function_runtime_privileges": [],
        "function_security_type": "INVOKER",
        "lock_column": "candidate_quota_epoch",
        "trigger": "memory_patch_proposals_s28_candidate_quota",
        "usage_authority": "EXACT_PERSISTED_CORRECTION_CANDIDATE_ROWS",
    }:
        raise MigrationError("candidate database quota guard is not exact")
    slot_authority = manifest.get("slot_authority")
    if slot_authority != {
        "authority_columns": ["hat_scope_id", "slot_hash"],
        "legacy_null_intake": "DENY_UNTIL_ORDINARY_SLOT_UPDATE_SEALS",
        "owner_role": "mp_schema_owner",
        "policies": {
            "insert": "personal_memory_spaces_s5_insert",
            "select": "personal_memory_spaces_s5_select",
            "update": "personal_memory_spaces_s5_update",
        },
        "runtime_privileges": ["INSERT", "SELECT", "UPDATE"],
        "scope_derivation_function": "step28_personal_hat_scope_id",
        "table": "personal_memory_spaces",
        "update_guard_function": "guard_step28_slot_authority_update",
        "update_guard_trigger": "personal_memory_spaces_s28_authority_guard",
    }:
        raise MigrationError("candidate slot authority is not exact")
    tables = manifest.get("tables")
    if not isinstance(tables, list) or [
        row.get("table") for row in tables if isinstance(row, dict)
    ] != ["hat_scopes", "memory_patch_proposals"]:
        raise MigrationError("candidate security table delta is not exact")
    required_fields = {
        "delete_policy",
        "force_rls",
        "insert_policy",
        "owner_role",
        "rls_enabled",
        "runtime_privileges",
        "select_policy",
        "table",
        "tenant_column",
        "update_policy",
        "user_owner_column",
    }
    expected_policies = {
        "hat_scopes": (
            "hat_scopes_s5_select",
            "hat_scopes_s28_personal_insert",
        ),
        "memory_patch_proposals": (
            "memory_patch_proposals_s5_select",
            "memory_patch_proposals_s28_insert",
        ),
    }
    for table in tables:
        if not isinstance(table, dict) or set(table) != required_fields:
            raise MigrationError("candidate security table shape is invalid")
        name = table["table"]
        if (
            table["tenant_column"] != "tenant_id"
            or table["user_owner_column"] != "owner_user_id"
            or table["owner_role"] != "mp_schema_owner"
            or table["rls_enabled"] is not True
            or table["force_rls"] is not True
            or table["runtime_privileges"] != ["INSERT", "SELECT"]
            or (table["select_policy"], table["insert_policy"])
            != expected_policies[name]
            or table["update_policy"] is not None
            or table["delete_policy"] is not None
        ):
            raise MigrationError(f"unsafe candidate security decision: {name}")
    return manifest


def load_patch_validation_security_manifest() -> dict[str, Any]:
    manifest = load_json(PATCH_VALIDATION_SECURITY_MANIFEST_PATH)
    if set(manifest) != {
        "allowed_origins",
        "fixed_roles",
        "helper_functions",
        "manifest_id",
        "maximum_lifecycle_state",
        "quota_guard",
        "schema_version",
        "tables",
        "target_cockroachdb_version",
        "transition_edges",
        "transition_guard",
    }:
        raise MigrationError("patch-validation security manifest shape is invalid")
    if (
        manifest.get("schema_version") != 1
        or manifest.get("manifest_id")
        != "memory-patch-step29-personal-memory-patch-validation-security-1a"
        or manifest.get("target_cockroachdb_version") != PINNED_VERSION
    ):
        raise MigrationError("patch-validation security identity differs")
    if manifest.get("fixed_roles") != [
        "mp_app_runtime",
        "mp_request_context_setter",
        "mp_schema_owner",
        "mp_security_owner",
    ]:
        raise MigrationError("patch-validation fixed role set differs")
    if manifest.get("allowed_origins") != [
        "CRITIC_PROMPT_LOOP",
        "KNOWLEDGE_KERNEL",
    ]:
        raise MigrationError("patch-validation producer set differs")
    if manifest.get("maximum_lifecycle_state") != "AWAITING_APPROVAL":
        raise MigrationError("Step 29 lifecycle exceeds approval readiness")
    if manifest.get("transition_edges") != [
        "DETECTED_TO_PROPOSED",
        "PROPOSED_TO_EVIDENCE_BOUND",
        "EVIDENCE_BOUND_TO_VALIDATED",
        "VALIDATED_TO_AWAITING_APPROVAL",
    ]:
        raise MigrationError("Step 29 transition set differs")
    if manifest.get("helper_functions") != [
        {
            "function": "step29_proposal_target_matches",
            "owner_role": "mp_schema_owner",
            "runtime_privileges": ["EXECUTE"],
            "security_type": "INVOKER",
        },
        {
            "function": "step29_transition_target_matches",
            "owner_role": "mp_schema_owner",
            "runtime_privileges": ["EXECUTE"],
            "security_type": "INVOKER",
        },
    ]:
        raise MigrationError("Step 29 helper-function boundary differs")
    if manifest.get("quota_guard") != {
        "byte_limit": 8 * 1024 * 1024,
        "count_limit": 128,
        "function": "enforce_step29_proposal_quota",
        "function_owner_role": "mp_schema_owner",
        "function_runtime_privileges": [],
        "function_security_type": "INVOKER",
        "lock_column": "candidate_quota_epoch",
        "trigger": "memory_patch_proposals_s29_quota",
        "trigger_events": ["INSERT", "UPDATE"],
        "usage_authority": "EXACT_PERSISTED_STEP29_PROPOSAL_ROWS",
    }:
        raise MigrationError("Step 29 database quota guard differs")
    if manifest.get("transition_guard") != {
        "function": "guard_step29_proposal_transition",
        "function_owner_role": "mp_schema_owner",
        "function_runtime_privileges": [],
        "function_security_type": "INVOKER",
        "trigger": "memory_patch_proposals_s29_transition_guard",
    }:
        raise MigrationError("Step 29 transition guard differs")
    tables = manifest.get("tables")
    if not isinstance(tables, list) or [
        row.get("table") for row in tables if isinstance(row, dict)
    ] != ["memory_patch_proposals", "patch_transition_records"]:
        raise MigrationError("Step 29 protected table set differs")
    required = {
        "delete_policy",
        "force_rls",
        "insert_policies",
        "owner_role",
        "rls_enabled",
        "runtime_privileges",
        "select_policy",
        "table",
        "tenant_column",
        "update_policy",
        "user_owner_column",
    }
    expected = {
        "memory_patch_proposals": {
            "insert_policies": [
                "memory_patch_proposals_s28_insert",
                "memory_patch_proposals_s29_insert",
            ],
            "runtime_privileges": ["INSERT", "SELECT", "UPDATE"],
            "select_policy": "memory_patch_proposals_s5_select",
            "update_policy": "memory_patch_proposals_s29_update",
            "user_owner_column": "owner_user_id",
        },
        "patch_transition_records": {
            "insert_policies": ["patch_transition_records_s29_insert"],
            "runtime_privileges": ["INSERT", "SELECT"],
            "select_policy": "patch_transition_records_s5_select",
            "update_policy": None,
            "user_owner_column": None,
        },
    }
    for table in tables:
        if not isinstance(table, dict) or set(table) != required:
            raise MigrationError("Step 29 table manifest shape differs")
        decision = expected[table["table"]]
        if (
            table["tenant_column"] != "tenant_id"
            or table["owner_role"] != "mp_schema_owner"
            or table["rls_enabled"] is not True
            or table["force_rls"] is not True
            or table["delete_policy"] is not None
            or any(table[key] != value for key, value in decision.items())
        ):
            raise MigrationError(
                f"unsafe Step 29 table decision: {table['table']}"
            )
    return manifest


def load_approval_commit_security_manifest() -> dict[str, Any]:
    manifest = load_json(APPROVAL_COMMIT_SECURITY_MANIFEST_PATH)
    if set(manifest) != {
        "approval_boundary",
        "commit_audit_fk_read_boundary",
        "commit_authority",
        "commit_identity_read_boundary",
        "commit_run_lineage_read_boundary",
        "credential_separation",
        "helper_functions",
        "manifest_id",
        "maximum_lifecycle_state",
        "quota_serialization",
        "schema_version",
        "tables",
        "target_cockroachdb_version",
        "transition_edges",
        "transition_guard",
    }:
        raise MigrationError("Step 30 security manifest shape is invalid")
    if (
        manifest.get("schema_version") != 1
        or manifest.get("manifest_id")
        != "memory-patch-step30-personal-memory-approval-commit-security-1a"
        or manifest.get("target_cockroachdb_version") != PINNED_VERSION
    ):
        raise MigrationError("Step 30 security manifest identity differs")
    if manifest.get("approval_boundary") != {
        "actor_type": "HUMAN_USER",
        "insert_policy": "memory_patch_approvals_s30_insert",
        "runtime_role": "mp_app_runtime",
        "technical_roles_may_approve": False,
    }:
        raise MigrationError("Step 30 human approval boundary differs")
    if manifest.get("commit_authority") != {
        "bypass_rls": False,
        "can_approve": False,
        "can_publish_sources": False,
        "can_update_hat_registry": False,
        "cluster_role": "mp_personal_memory_commit_helper",
        "login": False,
    }:
        raise MigrationError("Step 30 commit authority is not least privileged")
    if manifest.get("commit_audit_fk_read_boundary") != {
        "owner_scoped": True,
        "policy": "audit_events_s30_commit_owner_select",
        "privilege": "SELECT",
        "table": "audit_events",
    }:
        raise MigrationError("Step 30 audit FK read boundary differs")
    if manifest.get("commit_identity_read_boundary") != {
        "policies": {
            "tenants": "tenants_s30_commit_identity_select",
            "users": "users_s30_commit_identity_select",
        },
        "privilege": "SELECT",
        "tables": ["tenants", "users"],
        "tenant_user_scoped": True,
    }:
        raise MigrationError("Step 30 identity read boundary differs")
    if manifest.get("commit_run_lineage_read_boundary") != {
        "owner_scoped": True,
        "policy": "kernel_runs_s30_commit_lineage_select",
        "privilege": "SELECT",
        "table": "kernel_runs",
    }:
        raise MigrationError("Step 30 run-lineage read boundary differs")
    if manifest.get("credential_separation") != {
        "approval_runtime_role": "mp_app_runtime",
        "commit_role": "mp_personal_memory_commit_helper",
        "model_credentials_available": False,
        "secret_material_in_repository": False,
    }:
        raise MigrationError("Step 30 credential separation differs")
    if manifest.get("maximum_lifecycle_state") != "ACTIVE":
        raise MigrationError("Step 30 lifecycle boundary differs")
    if manifest.get("quota_serialization") != {
        "column": "candidate_quota_epoch",
        "database_compatibility": "TABLE_UPDATE_GUARDED_TO_EPOCH_ONLY",
        "guard_function": "guard_step30_commit_slot_update",
        "guard_trigger": "personal_memory_spaces_s30_commit_guard",
        "policy": "personal_memory_spaces_s30_commit_quota_lock_update",
        "privilege": "UPDATE",
        "table": "personal_memory_spaces",
    }:
        raise MigrationError("Step 30 quota serialization boundary differs")
    if manifest.get("transition_edges") != [
        "AWAITING_APPROVAL_TO_APPROVED",
        "APPROVED_TO_COMMITTED",
        "COMMITTED_TO_ACTIVE",
    ]:
        raise MigrationError("Step 30 transition set differs")
    helpers = manifest.get("helper_functions")
    if not isinstance(helpers, list) or [
        item.get("function") for item in helpers if isinstance(item, dict)
    ] != [
        "step30_commit_helper_authorized",
        "step30_approval_target_matches",
        "step30_approval_transition_matches",
        "step30_commit_target_matches",
        "step30_memory_item_commit_matches",
        "step30_proposal_lifecycle_matches",
        "step30_transition_target_matches",
    ]:
        raise MigrationError("Step 30 helper-function boundary differs")
    for helper in helpers:
        if (
            set(helper)
            != {"function", "owner_role", "runtime_roles", "security_type"}
            or helper["owner_role"] != "mp_schema_owner"
            or helper["security_type"] != "INVOKER"
            or not helper["runtime_roles"]
        ):
            raise MigrationError("Step 30 helper-function decision is unsafe")
    tables = manifest.get("tables")
    if not isinstance(tables, list) or [
        item.get("table") for item in tables if isinstance(item, dict)
    ] != [
        "memory_patch_approvals",
        "memory_patch_proposals",
        "memory_patch_commits",
        "memory_items",
        "patch_transition_records",
        "persistence_operations",
    ]:
        raise MigrationError("Step 30 protected table set differs")
    for table in tables:
        if (
            set(table)
            != {
                "app_runtime_privileges",
                "commit_runtime_privileges",
                "force_rls",
                "table",
            }
            or table["force_rls"] is not True
            or "DELETE" in table["app_runtime_privileges"]
            or "DELETE" in table["commit_runtime_privileges"]
        ):
            raise MigrationError("Step 30 table decision is unsafe")
    if manifest.get("transition_guard") != {
        "function": "guard_step30_proposal_transition",
        "function_owner_role": "mp_schema_owner",
        "function_runtime_privileges": [],
        "function_security_type": "INVOKER",
        "trigger": "memory_patch_proposals_s30_transition_guard",
    }:
        raise MigrationError("Step 30 transition guard differs")
    return manifest


def offline_validate() -> dict[str, Any]:
    pin = load_version_pin()
    migrations = load_migrations()
    schema_manifest = load_schema_manifest()
    security_manifest = load_security_manifest()
    persistence_manifest = load_persistence_manifest()
    source_registry_manifest = load_source_registry_manifest()
    ingestion_saga_manifest = load_ingestion_saga_manifest()
    parsing_pipeline_manifest = load_parsing_pipeline_manifest()
    candidate_security_manifest = load_correction_candidate_security_manifest()
    patch_validation_manifest = load_patch_validation_security_manifest()
    approval_commit_manifest = load_approval_commit_security_manifest()
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
    step6_sql = next(
        migration.sql
        for migration in migrations
        if migration.migration_id == STEP6_MIGRATION_ID
    )
    step9_sql = next(
        migration.sql
        for migration in migrations
        if migration.migration_id == STEP9_MIGRATION_ID
    )
    step10_sql = next(
        migration.sql
        for migration in migrations
        if migration.migration_id == STEP10_MIGRATION_ID
    )
    step11_sql = next(
        migration.sql
        for migration in migrations
        if migration.migration_id == STEP11_MIGRATION_ID
    )
    step19_sql = next(
        migration.sql
        for migration in migrations
        if migration.migration_id == STEP19_MIGRATION_ID
    )
    step27_sql = next(
        migration.sql
        for migration in migrations
        if migration.migration_id == STEP27_MIGRATION_ID
    )
    step28_sql = next(
        migration.sql
        for migration in migrations
        if migration.migration_id == STEP28_MIGRATION_ID
    )
    step29_sql = next(
        migration.sql
        for migration in migrations
        if migration.migration_id == STEP29_MIGRATION_ID
    )
    step30_sql = next(
        migration.sql
        for migration in migrations
        if migration.migration_id == STEP30_MIGRATION_ID
    )
    historical_sql = step4_sql + "\n" + step5_sql
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
    persistence_tables = sorted(
        re.findall(
            r"^CREATE TABLE memory_patch\.([a-z0-9_]+)",
            step6_sql,
            re.MULTILINE,
        )
    )
    expected_persistence_tables = sorted(
        row["table"] for row in persistence_manifest["tables"]
    )
    if persistence_tables != expected_persistence_tables:
        raise MigrationError("persistence tables differ from migration SQL")
    source_registry_tables = sorted(
        re.findall(
            r"^CREATE TABLE memory_patch\.([a-z0-9_]+)",
            step9_sql,
            re.MULTILINE,
        )
    )
    expected_source_registry_tables = sorted(
        row["table"] for row in source_registry_manifest["tables"]
    )
    if source_registry_tables != expected_source_registry_tables:
        raise MigrationError("source registry tables differ from migration SQL")
    ingestion_saga_tables = sorted(
        re.findall(
            r"^CREATE TABLE memory_patch\.([a-z0-9_]+)",
            step10_sql,
            re.MULTILINE,
        )
    )
    expected_ingestion_saga_tables = sorted(
        row["table"] for row in ingestion_saga_manifest["tables"]
    )
    if ingestion_saga_tables != expected_ingestion_saga_tables:
        raise MigrationError("ingestion saga tables differ from migration SQL")
    parsing_pipeline_tables = sorted(
        re.findall(
            r"^CREATE TABLE memory_patch\.([a-z0-9_]+)",
            step11_sql,
            re.MULTILINE,
        )
    )
    expected_parsing_pipeline_tables = sorted(
        row["table"] for row in parsing_pipeline_manifest["tables"]
    )
    if parsing_pipeline_tables != expected_parsing_pipeline_tables:
        raise MigrationError("parsing pipeline tables differ from migration SQL")
    embedding_tables = sorted(
        re.findall(
            r"^CREATE TABLE memory_patch\.([a-z0-9_]+)",
            step19_sql,
            re.MULTILINE,
        )
    )
    if embedding_tables != ["chunk_embeddings"]:
        raise MigrationError("Step 19 embedding table set is not exact")
    personal_memory_tables = sorted(
        re.findall(
            r"^CREATE TABLE memory_patch\.([a-z0-9_]+)",
            step27_sql,
            re.MULTILINE,
        )
    )
    if personal_memory_tables != ["personal_memory_quota_policies"]:
        raise MigrationError("Step 27 Personal Memory table set is not exact")
    created_tables = sorted(
        [
            *step4_tables,
            *security_tables,
            *persistence_tables,
            *source_registry_tables,
            *ingestion_saga_tables,
            *parsing_pipeline_tables,
            *embedding_tables,
            *personal_memory_tables,
        ]
    )
    created_indexes = sorted(
        re.findall(
            r"^CREATE (?:UNIQUE |INVERTED )?INDEX ([a-z0-9_]+)",
            historical_sql,
            re.MULTILINE,
        )
    )
    if created_indexes != schema_manifest["explicit_indexes"]:
        raise MigrationError("schema manifest indexes differ from migration SQL")
    persistence_indexes = sorted(
        re.findall(
            r"^CREATE (?:UNIQUE |INVERTED )?INDEX ([a-z0-9_]+)",
            step6_sql,
            re.MULTILINE,
        )
    )
    if persistence_indexes != [
        "persistence_operations_external_identity_uq",
        "persistence_operations_private_idempotency_uq",
        "persistence_operations_shared_idempotency_uq",
    ]:
        raise MigrationError("persistence index boundary is not exact")
    parsing_indexes = sorted(
        re.findall(
            r"^CREATE (?:UNIQUE |INVERTED )?INDEX ([a-z0-9_]+)",
            step11_sql,
            re.MULTILINE,
        )
    )
    if parsing_indexes != [
        "parse_security_findings_review_idx",
        "parsed_documents_scope_idx",
        "parsed_sections_document_ordinal_idx",
    ]:
        raise MigrationError("parsing pipeline index boundary is not exact")
    embedding_indexes = sorted(
        re.findall(
            r"^CREATE (?:UNIQUE |INVERTED |VECTOR )?INDEX ([a-z0-9_]+)",
            step19_sql,
            re.MULTILINE,
        )
    )
    if embedding_indexes != [
        "chunk_embeddings_model_lookup_idx",
        "chunk_embeddings_vector_l2_idx",
    ]:
        raise MigrationError("Step 19 embedding index boundary is not exact")
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
    persistence_table = persistence_manifest["tables"][0]
    persistence_enabled = set(
        re.findall(
            r"ALTER TABLE memory_patch\.([a-z0-9_]+)\s+"
            r"ENABLE ROW LEVEL SECURITY;",
            step6_sql,
        )
    )
    persistence_forced = set(
        re.findall(
            r"ALTER TABLE memory_patch\.([a-z0-9_]+)\s+"
            r"FORCE ROW LEVEL SECURITY;",
            step6_sql,
        )
    )
    if persistence_enabled != {"persistence_operations"}:
        raise MigrationError("Step 6 RLS coverage is incomplete")
    if persistence_forced != {"persistence_operations"}:
        raise MigrationError("Step 6 FORCE RLS coverage is incomplete")
    for command in ("select", "insert", "update"):
        policy = persistence_table[f"{command}_policy"]
        pattern = re.compile(
            rf"CREATE POLICY {re.escape(policy)}\s+"
            r"ON memory_patch\.persistence_operations\s+"
            rf"FOR {command.upper()}\s+TO mp_app_runtime",
            re.MULTILINE,
        )
        if pattern.search(step6_sql) is None:
            raise MigrationError(f"Step 6 policy is missing: {policy}")
    if "raw_error" in step6_sql.lower() or "exception_message" in step6_sql.lower():
        raise MigrationError("Step 6 must not persist raw error payloads")
    if (
        "status = 'COMPLETED'" not in step6_sql
        or "result_digest IS NOT NULL" not in step6_sql
        or "completed_at IS NOT NULL" not in step6_sql
    ):
        raise MigrationError("Step 6 completion shape is incomplete")
    if (
        "persistence_operations_s6_identity_guard" not in step6_sql
        or "guard_persistence_operation_identity" not in step6_sql
    ):
        raise MigrationError("Step 6 immutable identity guard is missing")
    step9_enabled = set(
        re.findall(
            r"ALTER TABLE memory_patch\.([a-z0-9_]+)\s+"
            r"ENABLE ROW LEVEL SECURITY;",
            step9_sql,
        )
    )
    step9_forced = set(
        re.findall(
            r"ALTER TABLE memory_patch\.([a-z0-9_]+)\s+"
            r"FORCE ROW LEVEL SECURITY;",
            step9_sql,
        )
    )
    expected_step9_protected = set(expected_source_registry_tables)
    if step9_enabled != expected_step9_protected:
        raise MigrationError("Step 9 RLS coverage is incomplete")
    if step9_forced != expected_step9_protected:
        raise MigrationError("Step 9 FORCE RLS coverage is incomplete")
    for table in source_registry_manifest["tables"]:
        for command in ("select", "insert", "update"):
            policy = table[f"{command}_policy"]
            if policy is None:
                continue
            pattern = re.compile(
                rf"CREATE POLICY {re.escape(policy)}\s+"
                rf"ON memory_patch\.{re.escape(table['table'])}\s+"
                rf"FOR {command.upper()}\s+TO mp_app_runtime",
                re.MULTILINE,
            )
            if pattern.search(step9_sql) is None:
                raise MigrationError(f"Step 9 policy is missing: {policy}")
    if "guard_source_registry_publication_update" not in step9_sql:
        raise MigrationError("Step 9 publication consistency guard is missing")
    if re.search(r"\bTO\s+PUBLIC\b", step9_sql, re.IGNORECASE):
        raise MigrationError("Step 9 policy or grant targets PUBLIC")
    if re.search(
        r"\bUSING\s*\(\s*(?:true|1\s*=\s*1)\s*\)",
        step9_sql,
        re.IGNORECASE,
    ):
        raise MigrationError("Step 9 contains an allow-all policy")
    step10_enabled = set(
        re.findall(
            r"ALTER TABLE memory_patch\.([a-z0-9_]+)\s+"
            r"ENABLE ROW LEVEL SECURITY;",
            step10_sql,
        )
    )
    step10_forced = set(
        re.findall(
            r"ALTER TABLE memory_patch\.([a-z0-9_]+)\s+"
            r"FORCE ROW LEVEL SECURITY;",
            step10_sql,
        )
    )
    expected_step10_protected = set(expected_ingestion_saga_tables)
    if step10_enabled != expected_step10_protected:
        raise MigrationError("Step 10 RLS coverage is incomplete")
    if step10_forced != expected_step10_protected:
        raise MigrationError("Step 10 FORCE RLS coverage is incomplete")
    for table in ingestion_saga_manifest["tables"]:
        for command in ("select", "insert", "update"):
            policy = table[f"{command}_policy"]
            if policy is None:
                continue
            pattern = re.compile(
                rf"CREATE POLICY {re.escape(policy)}\s+"
                rf"ON memory_patch\.{re.escape(table['table'])}\s+"
                rf"FOR {command.upper()}\s+TO mp_app_runtime",
                re.MULTILINE,
            )
            if pattern.search(step10_sql) is None:
                raise MigrationError(f"Step 10 policy is missing: {policy}")
    step10_triggers = set(
        re.findall(
            r"^CREATE TRIGGER ([a-z0-9_]+)",
            step10_sql,
            re.MULTILINE,
        )
    )
    if step10_triggers != {
        "ingestion_external_effects_s10_receipt_guard",
        "ingestion_sagas_s10_state_guard",
    }:
        raise MigrationError("Step 10 trigger set differs")
    if re.search(r"\bTO\s+PUBLIC\b", step10_sql, re.IGNORECASE):
        raise MigrationError("Step 10 policy or grant targets PUBLIC")
    if re.search(
        r"\bUSING\s*\(\s*(?:true|1\s*=\s*1)\s*\)",
        step10_sql,
        re.IGNORECASE,
    ):
        raise MigrationError("Step 10 contains an allow-all policy")
    if re.search(
        r"^\s*(?:DELETE\s+FROM|TRUNCATE|DROP\s+TABLE)\b",
        step10_sql,
        re.IGNORECASE | re.MULTILINE,
    ):
        raise MigrationError("Step 10 contains destructive cleanup SQL")
    step11_enabled = set(
        re.findall(
            r"ALTER TABLE memory_patch\.([a-z0-9_]+)\s+"
            r"ENABLE ROW LEVEL SECURITY;",
            step11_sql,
        )
    )
    step11_forced = set(
        re.findall(
            r"ALTER TABLE memory_patch\.([a-z0-9_]+)\s+"
            r"FORCE ROW LEVEL SECURITY;",
            step11_sql,
        )
    )
    expected_step11_protected = set(expected_parsing_pipeline_tables)
    if step11_enabled != expected_step11_protected:
        raise MigrationError("Step 11 RLS coverage is incomplete")
    if step11_forced != expected_step11_protected:
        raise MigrationError("Step 11 FORCE RLS coverage is incomplete")
    for table in parsing_pipeline_manifest["tables"]:
        for command in ("select", "insert", "update"):
            policy = table[f"{command}_policy"]
            if policy is None:
                continue
            pattern = re.compile(
                rf"CREATE POLICY {re.escape(policy)}\s+"
                rf"ON memory_patch\.{re.escape(table['table'])}\s+"
                rf"FOR {command.upper()}\s+TO mp_app_runtime",
                re.MULTILINE,
            )
            if pattern.search(step11_sql) is None:
                raise MigrationError(f"Step 11 policy is missing: {policy}")
    if re.search(r"\bTO\s+PUBLIC\b", step11_sql, re.IGNORECASE):
        raise MigrationError("Step 11 policy or grant targets PUBLIC")
    if re.search(
        r"\bUSING\s*\(\s*(?:true|1\s*=\s*1)\s*\)",
        step11_sql,
        re.IGNORECASE,
    ):
        raise MigrationError("Step 11 contains an allow-all policy")
    if re.search(
        r"^\s*(?:DELETE\s+FROM|TRUNCATE|DROP\s+TABLE)\b",
        step11_sql,
        re.IGNORECASE | re.MULTILINE,
    ):
        raise MigrationError("Step 11 contains destructive cleanup SQL")
    step19_enabled = set(
        re.findall(
            r"ALTER TABLE memory_patch\.([a-z0-9_]+)\s+"
            r"ENABLE ROW LEVEL SECURITY;",
            step19_sql,
        )
    )
    step19_forced = set(
        re.findall(
            r"ALTER TABLE memory_patch\.([a-z0-9_]+)\s+"
            r"FORCE ROW LEVEL SECURITY;",
            step19_sql,
        )
    )
    if step19_enabled != {"chunk_embeddings"}:
        raise MigrationError("Step 19 RLS coverage is incomplete")
    if step19_forced != {"chunk_embeddings"}:
        raise MigrationError("Step 19 FORCE RLS coverage is incomplete")
    for policy, command in (
        ("chunk_embeddings_s19_select", "SELECT"),
        ("chunk_embeddings_s19_insert", "INSERT"),
    ):
        if re.search(
            rf"CREATE POLICY {policy}\s+ON memory_patch\.chunk_embeddings\s+"
            rf"FOR {command}\s+TO mp_app_runtime",
            step19_sql,
            re.MULTILINE,
        ) is None:
            raise MigrationError(f"Step 19 policy is missing: {policy}")
    step27_enabled = set(
        re.findall(
            r"ALTER TABLE memory_patch\.([a-z0-9_]+)\s+"
            r"ENABLE ROW LEVEL SECURITY;",
            step27_sql,
        )
    )
    step27_forced = set(
        re.findall(
            r"ALTER TABLE memory_patch\.([a-z0-9_]+)\s+"
            r"FORCE ROW LEVEL SECURITY;",
            step27_sql,
        )
    )
    if step27_enabled != {"personal_memory_quota_policies"}:
        raise MigrationError("Step 27 RLS coverage is incomplete")
    if step27_forced != {"personal_memory_quota_policies"}:
        raise MigrationError("Step 27 FORCE RLS coverage is incomplete")
    for policy, command in (
        ("personal_memory_quota_policies_s27_select", "SELECT"),
        ("personal_memory_quota_policies_s27_insert", "INSERT"),
    ):
        if re.search(
            rf"CREATE POLICY {policy}\s+"
            r"ON memory_patch\.personal_memory_quota_policies\s+"
            rf"FOR {command}\s+TO mp_app_runtime",
            step27_sql,
            re.MULTILINE,
        ) is None:
            raise MigrationError(f"Step 27 policy is missing: {policy}")
    if re.search(
        r"^CREATE TABLE memory_patch\.",
        step28_sql,
        re.MULTILINE,
    ):
        raise MigrationError("Step 28 must reuse the existing proposal table")
    step28_enabled = set(
        re.findall(
            r"ALTER TABLE memory_patch\.([a-z0-9_]+)\s+"
            r"ENABLE ROW LEVEL SECURITY;",
            step28_sql,
        )
    )
    step28_forced = set(
        re.findall(
            r"ALTER TABLE memory_patch\.([a-z0-9_]+)\s+"
            r"FORCE ROW LEVEL SECURITY;",
            step28_sql,
        )
    )
    expected_candidate_tables = {
        table["table"] for table in candidate_security_manifest["tables"]
    }
    if step28_enabled != expected_candidate_tables:
        raise MigrationError("Step 28 RLS reassertion coverage is incomplete")
    if step28_forced != expected_candidate_tables:
        raise MigrationError("Step 28 FORCE RLS reassertion coverage is incomplete")
    for table in candidate_security_manifest["tables"]:
        policy = table["insert_policy"]
        if re.search(
            rf"CREATE POLICY {re.escape(policy)}\s+"
            rf"ON memory_patch\.{re.escape(table['table'])}\s+"
            r"FOR INSERT\s+TO mp_app_runtime\s+WITH CHECK",
            step28_sql,
            re.MULTILINE,
        ) is None:
            raise MigrationError(f"Step 28 policy is missing: {policy}")
    if (
        "lifecycle_state = 'DETECTED'" not in step28_sql
        or "origin IN ('KNOWLEDGE_KERNEL', 'CRITIC_PROMPT_LOOP')"
        not in step28_sql
        or "space.state IN ('CONFIGURED', 'ACTIVE')" not in step28_sql
        or "content_hash = proposed_content ->> 'envelope_hash'" not in step28_sql
        or "requested_trust_class = 'MODEL_EXPERIENCE_HINT'" not in step28_sql
    ):
        raise MigrationError("Step 28 candidate boundary is incomplete")
    if re.search(r"^CREATE TABLE memory_patch\.", step29_sql, re.MULTILINE):
        raise MigrationError("Step 29 must reuse proposal and transition tables")
    step29_enabled = set(
        re.findall(
            r"ALTER TABLE memory_patch\.([a-z0-9_]+)\s+"
            r"ENABLE ROW LEVEL SECURITY;",
            step29_sql,
        )
    )
    step29_forced = set(
        re.findall(
            r"ALTER TABLE memory_patch\.([a-z0-9_]+)\s+"
            r"FORCE ROW LEVEL SECURITY;",
            step29_sql,
        )
    )
    expected_step29_tables = {
        table["table"] for table in patch_validation_manifest["tables"]
    }
    if step29_enabled != expected_step29_tables:
        raise MigrationError("Step 29 RLS coverage is incomplete")
    if step29_forced != expected_step29_tables:
        raise MigrationError("Step 29 FORCE RLS coverage is incomplete")
    for table in patch_validation_manifest["tables"]:
        for policy in table["insert_policies"]:
            if policy.endswith("_s28_insert"):
                continue
            if re.search(
                rf"CREATE POLICY {re.escape(policy)}\s+"
                rf"ON memory_patch\.{re.escape(table['table'])}\s+"
                r"FOR INSERT\s+TO mp_app_runtime\s+WITH CHECK",
                step29_sql,
                re.MULTILINE,
            ) is None:
                raise MigrationError(f"Step 29 INSERT policy is missing: {policy}")
        update_policy = table["update_policy"]
        if update_policy is not None and re.search(
            rf"CREATE POLICY {re.escape(update_policy)}\s+"
            rf"ON memory_patch\.{re.escape(table['table'])}\s+"
            r"FOR UPDATE\s+TO mp_app_runtime\s+USING\s*\(",
            step29_sql,
            re.MULTILINE,
        ) is None:
            raise MigrationError(
                f"Step 29 UPDATE policy is missing: {update_policy}"
            )
    if any(
        fragment not in step29_sql
        for fragment in (
            "DETECTED' AND state_after = 'PROPOSED",
            "PROPOSED' AND state_after = 'EVIDENCE_BOUND",
            "EVIDENCE_BOUND' AND state_after = 'VALIDATED",
            "VALIDATED' AND state_after = 'AWAITING_APPROVAL",
            "step29_evidence_binding_hash",
            "step29_validation_receipt_hash",
            "memory_patch.step29_proposal_target_matches",
        )
    ):
        raise MigrationError("Step 29 lifecycle or integrity boundary is incomplete")
    cluster_role_sql, step30_database_sql = split_cluster_role_ddl(
        step30_sql,
        begin_marker=STEP30_CLUSTER_ROLE_BEGIN,
        end_marker=STEP30_CLUSTER_ROLE_END,
        role_name="mp_personal_memory_commit_helper",
    )
    if any(
        fragment not in cluster_role_sql
        for fragment in (
            "NOLOGIN NOCREATEROLE NOCREATEDB NOBYPASSRLS",
            "REVOKE admin FROM mp_personal_memory_commit_helper",
            "REVOKE mp_app_runtime FROM mp_personal_memory_commit_helper",
        )
    ):
        raise MigrationError("Step 30 dedicated role is not least privileged")
    expected_step30_tables = {
        table["table"] for table in approval_commit_manifest["tables"]
    }
    step30_enabled = set(
        re.findall(
            r"ALTER TABLE memory_patch\.([a-z0-9_]+)\s+"
            r"ENABLE ROW LEVEL SECURITY;",
            step30_database_sql,
        )
    )
    step30_forced = set(
        re.findall(
            r"ALTER TABLE memory_patch\.([a-z0-9_]+)\s+"
            r"FORCE ROW LEVEL SECURITY;",
            step30_database_sql,
        )
    )
    if step30_enabled != expected_step30_tables or step30_forced != expected_step30_tables:
        raise MigrationError("Step 30 RLS/FORCE RLS coverage is incomplete")
    required = (
        "state_before = 'AWAITING_APPROVAL'",
        "state_after = 'APPROVED'",
        "state_before = 'APPROVED'",
        "state_after = 'COMMITTED'",
        "state_before = 'COMMITTED'",
        "state_after = 'ACTIVE'",
        "step30_approval_replay_identity",
        "step30_commit_replay_identity",
        "step30_activation_replay_identity",
        "step30_commit_helper_authorized",
        "guard_step30_commit_slot_update",
        "personal_memory_spaces_s30_commit_guard",
    )
    if any(fragment not in step30_database_sql for fragment in required):
        raise MigrationError("Step 30 lifecycle/replay boundary is incomplete")
    if re.search(
        r"active_patch_retrieval|cross_model_reuse|inject.*patch",
        step30_database_sql,
        re.IGNORECASE,
    ):
        raise MigrationError("Step 30 implements forbidden retrieval/reuse")
    return {
        "migration_count": len(migrations),
        "migration_ids": [migration.migration_id for migration in migrations],
        "schema_table_count": len(created_tables),
        "security_internal_table_count": len(security_tables),
        "persistence_table_count": len(persistence_tables),
        "source_registry_table_count": len(source_registry_tables),
        "ingestion_saga_table_count": len(ingestion_saga_tables),
        "parsing_pipeline_table_count": len(parsing_pipeline_tables),
        "embedding_table_count": len(embedding_tables),
        "personal_memory_table_count": len(personal_memory_tables),
        "step4_table_count": len(step4_tables),
        "protected_table_count": (
            len(protected)
            + len(persistence_tables)
            + len(source_registry_tables)
            + len(ingestion_saga_tables)
            + len(parsing_pipeline_tables)
            + len(embedding_tables)
            + len(personal_memory_tables)
        ),
        "identity_guard_trigger_count": (
            len(guard_triggers) + 2 + len(step10_triggers) + 1
        ),
        "explicit_index_count": (
            len(created_indexes) + len(persistence_indexes) + len(parsing_indexes)
        ),
        "status": "PASS",
        "target_version": pin["exact_version"],
        "vector_boundary": "STEP19_VECTOR_384_L2_PINNED",
        "personal_memory_boundary": "STEP27_OWNER_PRIVATE_RLS_FORCE_RLS",
        "correction_candidate_boundary": (
            "STEP28_DETECTED_OWNER_PRIVATE_INSERT_ONLY"
        ),
        "patch_validation_boundary": (
            "STEP29_AWAITING_APPROVAL_OWNER_PRIVATE"
        ),
    }


def validate_timeout(timeout: float) -> None:
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise MigrationError("timeout must be numeric")
    if timeout <= 0 or timeout > MAX_SQL_COMMAND_TIMEOUT_SECONDS:
        raise MigrationError("timeout must be bounded to 1..300 seconds")


def validate_database_identifier(database: str) -> None:
    if not SAFE_IDENTIFIER_PATTERN.fullmatch(database):
        raise MigrationError("database name must be a safe lowercase SQL identifier")


def assert_disposable_database(database: str) -> None:
    validate_database_identifier(database)
    if not database.startswith(DISPOSABLE_DATABASE_PREFIXES):
        raise MigrationError(
            "destructive cleanup requires an approved disposable validation "
            "database prefix"
        )


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
        DISPOSABLE_DATABASE_PREFIXES
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


def split_cluster_role_ddl(
    sql: str,
    *,
    begin_marker: str,
    end_marker: str,
    role_name: str,
) -> tuple[str, str]:
    if sql.count(begin_marker) != 1:
        raise MigrationError(f"{role_name} cluster-role begin marker is not exact")
    if sql.count(end_marker) != 1:
        raise MigrationError(f"{role_name} cluster-role end marker is not exact")
    before, marked = sql.split(begin_marker, 1)
    cluster_sql, after = marked.split(end_marker, 1)
    if re.search(
        r"\b(?:CREATE|ALTER|DROP)\s+(?:TABLE|SCHEMA|FUNCTION|POLICY)\b",
        cluster_sql,
        re.IGNORECASE,
    ):
        raise MigrationError(f"{role_name} cluster-role section contains database DDL")
    if f"CREATE ROLE IF NOT EXISTS {role_name}" not in cluster_sql:
        raise MigrationError("cluster-role section lacks idempotent role creation")
    if re.search(r"\bCREATE\s+ROLE\s+(?!IF\s+NOT\s+EXISTS)", cluster_sql, re.I):
        raise MigrationError("cluster-role creation is not idempotent")
    database_sql = before + after
    if re.search(r"\bCREATE\s+ROLE\b", database_sql, re.IGNORECASE):
        raise MigrationError("database transaction contains cluster role creation")
    return cluster_sql.strip() + "\n", database_sql


def split_step5_cluster_role_ddl(sql: str) -> tuple[str, str]:
    return split_cluster_role_ddl(
        sql,
        begin_marker=STEP5_CLUSTER_ROLE_BEGIN,
        end_marker=STEP5_CLUSTER_ROLE_END,
        role_name="mp_schema_owner",
    )


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
    *,
    include_step28_delta: bool = False,
) -> dict[str, Any]:
    manifest = load_security_manifest()
    protected_rows = [
        row for row in manifest["tables"] if row["rls_enabled"]
    ]
    protected_names = {row["table"] for row in protected_rows}
    historical_table_names = {
        row["table"] for row in manifest["tables"]
    } | {
        row["table"] for row in manifest["security_internal_tables"]
    }
    quoted_historical_tables = ", ".join(
        sql_literal(table) for table in sorted(historical_table_names)
    )
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
            f"AND c.relname IN ({quoted_historical_tables}) "
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
    if include_step28_delta:
        candidate_manifest = load_correction_candidate_security_manifest()
        expected_policies.update(
            {
                table["insert_policy"]: (table["table"], "INSERT")
                for table in candidate_manifest["tables"]
            }
        )
    policy_catalog = parse_tsv(
        client.execute(
            database,
            "SELECT tablename, policyname, cmd, roles, qual, with_check "
            "FROM pg_catalog.pg_policies "
            "WHERE schemaname = 'memory_patch' "
            f"AND tablename IN ({quoted_historical_tables}) "
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
    if include_step28_delta:
        expected_runtime_grants.update(
            {
                (table["table"], "INSERT")
                for table in load_correction_candidate_security_manifest()["tables"]
            }
        )
    runtime_grants = parse_tsv(
        client.execute(
            database,
            "SELECT table_name, privilege_type "
            "FROM information_schema.table_privileges "
            "WHERE table_schema = 'memory_patch' "
            "AND grantee = 'mp_app_runtime' "
            f"AND table_name IN ({quoted_historical_tables}) "
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
            "AND target.relname IN ('memory_items', 'personal_memory_spaces') "
            "AND trigger.tgname IN ("
            "'memory_items_s5_identity_guard', "
            "'personal_memory_spaces_s5_identity_guard') "
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


def assert_step6_security_catalog(
    client: SqlClient,
    database: str,
) -> dict[str, Any]:
    load_persistence_manifest()
    table_rows = parse_tsv(
        client.execute(
            database,
            "SELECT c.relname AS table_name, c.relrowsecurity, "
            "c.relforcerowsecurity, owner.rolname AS owner_role "
            "FROM pg_catalog.pg_class AS c "
            "JOIN pg_catalog.pg_namespace AS namespace "
            "ON namespace.oid = c.relnamespace "
            "JOIN pg_catalog.pg_roles AS owner ON owner.oid = c.relowner "
            "WHERE namespace.nspname = 'memory_patch' "
            "AND c.relname = 'persistence_operations' "
            "AND c.relkind = 'r'",
        )
    )
    if table_rows != [
        {
            "table_name": "persistence_operations",
            "relrowsecurity": "t",
            "relforcerowsecurity": "t",
            "owner_role": "mp_schema_owner",
        }
    ]:
        raise MigrationError("Step 6 table ownership or RLS state differs")
    policy_rows = parse_tsv(
        client.execute(
            database,
            "SELECT policyname, cmd, roles, qual, with_check "
            "FROM pg_catalog.pg_policies "
            "WHERE schemaname = 'memory_patch' "
            "AND tablename = 'persistence_operations' "
            "ORDER BY policyname",
        )
    )
    expected_policies = {
        "persistence_operations_s6_insert": "INSERT",
        "persistence_operations_s6_select": "SELECT",
        "persistence_operations_s6_update": "UPDATE",
    }
    if {
        row["policyname"]: row["cmd"].upper() for row in policy_rows
    } != expected_policies:
        raise MigrationError("Step 6 live policy set differs from manifest")
    for row in policy_rows:
        if "mp_app_runtime" not in row["roles"]:
            raise MigrationError("Step 6 policy lacks the runtime role")
        if row["cmd"].upper() == "SELECT" and not row["qual"]:
            raise MigrationError("Step 6 SELECT policy lacks USING")
        if row["cmd"].upper() == "INSERT" and not row["with_check"]:
            raise MigrationError("Step 6 INSERT policy lacks WITH CHECK")
        if row["cmd"].upper() == "UPDATE" and (
            not row["qual"] or not row["with_check"]
        ):
            raise MigrationError("Step 6 UPDATE policy lacks USING/WITH CHECK")
    grant_rows = parse_tsv(
        client.execute(
            database,
            "SELECT privilege_type "
            "FROM information_schema.table_privileges "
            "WHERE table_schema = 'memory_patch' "
            "AND table_name = 'persistence_operations' "
            "AND grantee = 'mp_app_runtime' "
            "ORDER BY privilege_type",
        )
    )
    grants = [row["privilege_type"] for row in grant_rows]
    if grants != ["INSERT", "SELECT", "UPDATE"]:
        raise MigrationError("Step 6 runtime grants are not least privilege")
    trigger_rows = parse_tsv(
        client.execute(
            database,
            "SELECT trigger.tgname AS trigger_name, "
            "procedure.proname AS function_name "
            "FROM pg_catalog.pg_trigger AS trigger "
            "JOIN pg_catalog.pg_class AS target "
            "ON target.oid = trigger.tgrelid "
            "JOIN pg_catalog.pg_namespace AS namespace "
            "ON namespace.oid = target.relnamespace "
            "JOIN pg_catalog.pg_proc AS procedure "
            "ON procedure.oid = trigger.tgfoid "
            "WHERE namespace.nspname = 'memory_patch' "
            "AND target.relname = 'persistence_operations' "
            "AND NOT trigger.tgisinternal",
        )
    )
    if trigger_rows != [
        {
            "trigger_name": "persistence_operations_s6_identity_guard",
            "function_name": "guard_persistence_operation_identity",
        }
    ]:
        raise MigrationError("Step 6 immutable identity trigger differs")
    index_rows = parse_tsv(
        client.execute(
            database,
            "SELECT DISTINCT index_name "
            "FROM information_schema.statistics "
            "WHERE table_schema = 'memory_patch' "
            "AND table_name = 'persistence_operations' "
            "AND index_name LIKE 'persistence_operations_%_uq' "
            "ORDER BY index_name",
        )
    )
    indexes = [row["index_name"] for row in index_rows]
    if indexes != [
        "persistence_operations_external_identity_uq",
        "persistence_operations_private_idempotency_uq",
        "persistence_operations_shared_idempotency_uq",
    ]:
        raise MigrationError("Step 6 unique index set differs")
    digest_input = {
        "grants": grants,
        "indexes": indexes,
        "policies": policy_rows,
        "table": table_rows[0],
        "trigger": trigger_rows[0],
    }
    return {
        "policy_count": len(policy_rows),
        "protected_table_count": 1,
        "runtime_table_grant_count": len(grant_rows),
        "security_digest": hashlib.sha256(
            canonical_json_bytes(digest_input)
        ).hexdigest(),
        "trigger_count": len(trigger_rows),
        "unique_index_count": len(index_rows),
    }


def assert_step9_security_catalog(
    client: SqlClient,
    database: str,
) -> dict[str, Any]:
    manifest = load_source_registry_manifest()
    table_names = [row["table"] for row in manifest["tables"]]
    quoted_tables = ", ".join(sql_literal(name) for name in table_names)
    table_rows = parse_tsv(
        client.execute(
            database,
            "SELECT c.relname AS table_name, c.relrowsecurity, "
            "c.relforcerowsecurity, owner.rolname AS owner_role "
            "FROM pg_catalog.pg_class AS c "
            "JOIN pg_catalog.pg_namespace AS namespace "
            "ON namespace.oid = c.relnamespace "
            "JOIN pg_catalog.pg_roles AS owner ON owner.oid = c.relowner "
            "WHERE namespace.nspname = 'memory_patch' "
            f"AND c.relname IN ({quoted_tables}) "
            "AND c.relkind = 'r' ORDER BY c.relname",
        )
    )
    expected_tables = [
        {
            "table_name": name,
            "relrowsecurity": "t",
            "relforcerowsecurity": "t",
            "owner_role": "mp_schema_owner",
        }
        for name in sorted(table_names)
    ]
    if table_rows != expected_tables:
        raise MigrationError("Step 9 table ownership or RLS state differs")
    policy_rows = parse_tsv(
        client.execute(
            database,
            "SELECT tablename, policyname, cmd, roles, qual, with_check "
            "FROM pg_catalog.pg_policies "
            "WHERE schemaname = 'memory_patch' "
            f"AND tablename IN ({quoted_tables}) "
            "ORDER BY tablename, policyname",
        )
    )
    expected_policies = {
        (
            row["table"],
            row[f"{command}_policy"],
            command.upper(),
        )
        for row in manifest["tables"]
        for command in ("select", "insert", "update")
        if row[f"{command}_policy"] is not None
    }
    actual_policies = {
        (row["tablename"], row["policyname"], row["cmd"].upper())
        for row in policy_rows
    }
    if actual_policies != expected_policies:
        raise MigrationError("Step 9 live policy set differs from manifest")
    for row in policy_rows:
        if "mp_app_runtime" not in row["roles"]:
            raise MigrationError("Step 9 policy lacks runtime role")
        command = row["cmd"].upper()
        if command == "SELECT" and not row["qual"]:
            raise MigrationError("Step 9 SELECT policy lacks USING")
        if command == "INSERT" and not row["with_check"]:
            raise MigrationError("Step 9 INSERT policy lacks WITH CHECK")
        if command == "UPDATE" and (
            not row["qual"] or not row["with_check"]
        ):
            raise MigrationError("Step 9 UPDATE policy lacks USING/WITH CHECK")
    grant_rows = parse_tsv(
        client.execute(
            database,
            "SELECT table_name, privilege_type "
            "FROM information_schema.table_privileges "
            "WHERE table_schema = 'memory_patch' "
            "AND grantee = 'mp_app_runtime' "
            f"AND table_name IN ({quoted_tables}) "
            "ORDER BY table_name, privilege_type",
        )
    )
    expected_grants = sorted(
        (row["table"], privilege)
        for row in manifest["tables"]
        for privilege in row["runtime_privileges"]
    )
    actual_grants = [
        (row["table_name"], row["privilege_type"]) for row in grant_rows
    ]
    if actual_grants != expected_grants:
        raise MigrationError("Step 9 runtime grants are not least privilege")
    trigger_rows = parse_tsv(
        client.execute(
            database,
            "SELECT trigger.tgname AS trigger_name, "
            "target.relname AS table_name, "
            "procedure.proname AS function_name "
            "FROM pg_catalog.pg_trigger AS trigger "
            "JOIN pg_catalog.pg_class AS target "
            "ON target.oid = trigger.tgrelid "
            "JOIN pg_catalog.pg_namespace AS namespace "
            "ON namespace.oid = target.relnamespace "
            "JOIN pg_catalog.pg_proc AS procedure "
            "ON procedure.oid = trigger.tgfoid "
            "WHERE namespace.nspname = 'memory_patch' "
            "AND target.relname = 'source_registry_entries' "
            "AND NOT trigger.tgisinternal",
        )
    )
    if trigger_rows != [
        {
            "trigger_name": "source_registry_entries_s9_publication_guard",
            "table_name": "source_registry_entries",
            "function_name": "guard_source_registry_publication_update",
        }
    ]:
        raise MigrationError("Step 9 publication consistency trigger differs")
    digest_input = {
        "grants": grant_rows,
        "policies": policy_rows,
        "tables": table_rows,
        "triggers": trigger_rows,
    }
    return {
        "policy_count": len(policy_rows),
        "protected_table_count": len(table_rows),
        "runtime_table_grant_count": len(grant_rows),
        "security_digest": hashlib.sha256(
            canonical_json_bytes(digest_input)
        ).hexdigest(),
        "trigger_count": len(trigger_rows),
    }


def assert_step10_security_catalog(
    client: SqlClient,
    database: str,
) -> dict[str, Any]:
    manifest = load_ingestion_saga_manifest()
    table_names = [row["table"] for row in manifest["tables"]]
    quoted_tables = ", ".join(sql_literal(name) for name in table_names)
    table_rows = parse_tsv(
        client.execute(
            database,
            "SELECT c.relname AS table_name, c.relrowsecurity, "
            "c.relforcerowsecurity, owner.rolname AS owner_role "
            "FROM pg_catalog.pg_class AS c "
            "JOIN pg_catalog.pg_namespace AS namespace "
            "ON namespace.oid = c.relnamespace "
            "JOIN pg_catalog.pg_roles AS owner ON owner.oid = c.relowner "
            "WHERE namespace.nspname = 'memory_patch' "
            f"AND c.relname IN ({quoted_tables}) "
            "AND c.relkind = 'r' ORDER BY c.relname",
        )
    )
    expected_tables = [
        {
            "table_name": name,
            "relrowsecurity": "t",
            "relforcerowsecurity": "t",
            "owner_role": "mp_schema_owner",
        }
        for name in sorted(table_names)
    ]
    if table_rows != expected_tables:
        raise MigrationError("Step 10 table ownership or RLS state differs")
    policy_rows = parse_tsv(
        client.execute(
            database,
            "SELECT tablename, policyname, cmd, roles, qual, with_check "
            "FROM pg_catalog.pg_policies "
            "WHERE schemaname = 'memory_patch' "
            f"AND tablename IN ({quoted_tables}) "
            "ORDER BY tablename, policyname",
        )
    )
    expected_policies = {
        (row["table"], row[f"{command}_policy"], command.upper())
        for row in manifest["tables"]
        for command in ("select", "insert", "update")
        if row[f"{command}_policy"] is not None
    }
    actual_policies = {
        (row["tablename"], row["policyname"], row["cmd"].upper())
        for row in policy_rows
    }
    if actual_policies != expected_policies:
        raise MigrationError("Step 10 live policy set differs from manifest")
    for row in policy_rows:
        if "mp_app_runtime" not in row["roles"]:
            raise MigrationError("Step 10 policy lacks runtime role")
        command = row["cmd"].upper()
        if command == "SELECT" and not row["qual"]:
            raise MigrationError("Step 10 SELECT policy lacks USING")
        if command == "INSERT" and not row["with_check"]:
            raise MigrationError("Step 10 INSERT policy lacks WITH CHECK")
        if command == "UPDATE" and (
            not row["qual"] or not row["with_check"]
        ):
            raise MigrationError("Step 10 UPDATE policy is incomplete")
    grant_rows = parse_tsv(
        client.execute(
            database,
            "SELECT table_name, privilege_type "
            "FROM information_schema.table_privileges "
            "WHERE table_schema = 'memory_patch' "
            "AND grantee = 'mp_app_runtime' "
            f"AND table_name IN ({quoted_tables}) "
            "ORDER BY table_name, privilege_type",
        )
    )
    expected_grants = sorted(
        (row["table"], privilege)
        for row in manifest["tables"]
        for privilege in row["runtime_privileges"]
    )
    actual_grants = [
        (row["table_name"], row["privilege_type"]) for row in grant_rows
    ]
    if actual_grants != expected_grants:
        raise MigrationError("Step 10 runtime grants are not least privilege")
    trigger_rows = parse_tsv(
        client.execute(
            database,
            "SELECT trigger.tgname AS trigger_name, "
            "target.relname AS table_name, "
            "procedure.proname AS function_name "
            "FROM pg_catalog.pg_trigger AS trigger "
            "JOIN pg_catalog.pg_class AS target "
            "ON target.oid = trigger.tgrelid "
            "JOIN pg_catalog.pg_namespace AS namespace "
            "ON namespace.oid = target.relnamespace "
            "JOIN pg_catalog.pg_proc AS procedure "
            "ON procedure.oid = trigger.tgfoid "
            "WHERE namespace.nspname = 'memory_patch' "
            f"AND target.relname IN ({quoted_tables}) "
            "AND NOT trigger.tgisinternal "
            "ORDER BY trigger.tgname",
        )
    )
    expected_triggers = sorted(
        (
            {
                "trigger_name": row["trigger"],
                "table_name": row["table"],
                "function_name": row["function"],
            }
            for row in manifest["triggers"]
        ),
        key=lambda row: row["trigger_name"],
    )
    if trigger_rows != expected_triggers:
        raise MigrationError("Step 10 consistency triggers differ")
    digest_input = {
        "grants": grant_rows,
        "policies": policy_rows,
        "tables": table_rows,
        "triggers": trigger_rows,
    }
    return {
        "policy_count": len(policy_rows),
        "protected_table_count": len(table_rows),
        "runtime_table_grant_count": len(grant_rows),
        "security_digest": hashlib.sha256(
            canonical_json_bytes(digest_input)
        ).hexdigest(),
        "trigger_count": len(trigger_rows),
    }


def assert_step11_security_catalog(
    client: SqlClient,
    database: str,
) -> dict[str, Any]:
    manifest = load_parsing_pipeline_manifest()
    table_names = [row["table"] for row in manifest["tables"]]
    quoted_tables = ", ".join(sql_literal(name) for name in table_names)
    table_rows = parse_tsv(
        client.execute(
            database,
            "SELECT c.relname AS table_name, c.relrowsecurity, "
            "c.relforcerowsecurity, owner.rolname AS owner_role "
            "FROM pg_catalog.pg_class AS c "
            "JOIN pg_catalog.pg_namespace AS namespace "
            "ON namespace.oid = c.relnamespace "
            "JOIN pg_catalog.pg_roles AS owner ON owner.oid = c.relowner "
            "WHERE namespace.nspname = 'memory_patch' "
            f"AND c.relname IN ({quoted_tables}) "
            "AND c.relkind = 'r' ORDER BY c.relname",
        )
    )
    expected_tables = [
        {
            "table_name": name,
            "relrowsecurity": "t",
            "relforcerowsecurity": "t",
            "owner_role": "mp_schema_owner",
        }
        for name in sorted(table_names)
    ]
    if table_rows != expected_tables:
        raise MigrationError("Step 11 table ownership or RLS state differs")
    policy_rows = parse_tsv(
        client.execute(
            database,
            "SELECT tablename, policyname, cmd, roles, qual, with_check "
            "FROM pg_catalog.pg_policies "
            "WHERE schemaname = 'memory_patch' "
            f"AND tablename IN ({quoted_tables}) "
            "ORDER BY tablename, policyname",
        )
    )
    expected_policies = {
        (row["table"], row[f"{command}_policy"], command.upper())
        for row in manifest["tables"]
        for command in ("select", "insert")
    }
    actual_policies = {
        (row["tablename"], row["policyname"], row["cmd"].upper())
        for row in policy_rows
    }
    if actual_policies != expected_policies:
        raise MigrationError("Step 11 live policy set differs from manifest")
    for row in policy_rows:
        if "mp_app_runtime" not in row["roles"]:
            raise MigrationError("Step 11 policy lacks runtime role")
        command = row["cmd"].upper()
        if command == "SELECT" and not row["qual"]:
            raise MigrationError("Step 11 SELECT policy lacks USING")
        if command == "INSERT" and not row["with_check"]:
            raise MigrationError("Step 11 INSERT policy lacks WITH CHECK")
    grant_rows = parse_tsv(
        client.execute(
            database,
            "SELECT table_name, privilege_type "
            "FROM information_schema.table_privileges "
            "WHERE table_schema = 'memory_patch' "
            "AND grantee = 'mp_app_runtime' "
            f"AND table_name IN ({quoted_tables}) "
            "ORDER BY table_name, privilege_type",
        )
    )
    expected_grants = sorted(
        (row["table"], privilege)
        for row in manifest["tables"]
        for privilege in row["runtime_privileges"]
    )
    actual_grants = [
        (row["table_name"], row["privilege_type"]) for row in grant_rows
    ]
    if actual_grants != expected_grants:
        raise MigrationError("Step 11 runtime grants are not least privilege")
    trigger_rows = parse_tsv(
        client.execute(
            database,
            "SELECT trigger.tgname AS trigger_name "
            "FROM pg_catalog.pg_trigger AS trigger "
            "JOIN pg_catalog.pg_class AS target ON target.oid = trigger.tgrelid "
            "JOIN pg_catalog.pg_namespace AS namespace "
            "ON namespace.oid = target.relnamespace "
            "WHERE namespace.nspname = 'memory_patch' "
            f"AND target.relname IN ({quoted_tables}) "
            "AND NOT trigger.tgisinternal ORDER BY trigger.tgname",
        )
    )
    if trigger_rows:
        raise MigrationError("Step 11 unexpectedly adds mutable table triggers")
    digest_input = {
        "grants": grant_rows,
        "policies": policy_rows,
        "tables": table_rows,
        "triggers": trigger_rows,
    }
    return {
        "policy_count": len(policy_rows),
        "protected_table_count": len(table_rows),
        "runtime_table_grant_count": len(grant_rows),
        "security_digest": hashlib.sha256(
            canonical_json_bytes(digest_input)
        ).hexdigest(),
        "trigger_count": 0,
    }


def assert_step27_security_catalog(
    client: SqlClient,
    database: str,
) -> dict[str, Any]:
    """Verify the new owner-private table and lifecycle guard exactly."""

    table_rows = parse_tsv(
        client.execute(
            database,
            "SELECT c.relname AS table_name, c.relrowsecurity, "
            "c.relforcerowsecurity, owner.rolname AS owner_role "
            "FROM pg_catalog.pg_class AS c "
            "JOIN pg_catalog.pg_namespace AS namespace "
            "ON namespace.oid = c.relnamespace "
            "JOIN pg_catalog.pg_roles AS owner ON owner.oid = c.relowner "
            "WHERE namespace.nspname = 'memory_patch' "
            "AND c.relname = 'personal_memory_quota_policies' "
            "AND c.relkind = 'r'",
        )
    )
    expected_table = [
        {
            "table_name": "personal_memory_quota_policies",
            "relrowsecurity": "t",
            "relforcerowsecurity": "t",
            "owner_role": "mp_schema_owner",
        }
    ]
    if table_rows != expected_table:
        raise MigrationError("Step 27 table ownership or RLS state differs")
    policy_rows = parse_tsv(
        client.execute(
            database,
            "SELECT policyname, cmd, roles, qual, with_check "
            "FROM pg_catalog.pg_policies "
            "WHERE schemaname = 'memory_patch' "
            "AND tablename = 'personal_memory_quota_policies' "
            "ORDER BY policyname",
        )
    )
    expected_policies = {
        "personal_memory_quota_policies_s27_insert": "INSERT",
        "personal_memory_quota_policies_s27_select": "SELECT",
    }
    if {
        row["policyname"]: row["cmd"].upper() for row in policy_rows
    } != expected_policies:
        raise MigrationError("Step 27 policy set differs")
    for row in policy_rows:
        if "mp_app_runtime" not in row["roles"]:
            raise MigrationError("Step 27 policy lacks runtime role")
        if row["cmd"].upper() == "SELECT" and not row["qual"]:
            raise MigrationError("Step 27 SELECT policy lacks USING")
        if row["cmd"].upper() == "INSERT" and not row["with_check"]:
            raise MigrationError("Step 27 INSERT policy lacks WITH CHECK")
    grants = parse_tsv(
        client.execute(
            database,
            "SELECT privilege_type FROM information_schema.table_privileges "
            "WHERE table_schema = 'memory_patch' "
            "AND table_name = 'personal_memory_quota_policies' "
            "AND grantee = 'mp_app_runtime' ORDER BY privilege_type",
        )
    )
    if [row["privilege_type"] for row in grants] != ["INSERT", "SELECT"]:
        raise MigrationError("Step 27 runtime grants are not least privilege")
    trigger_rows = parse_tsv(
        client.execute(
            database,
            "SELECT trigger.tgname AS trigger_name, "
            "procedure.proname AS function_name "
            "FROM pg_catalog.pg_trigger AS trigger "
            "JOIN pg_catalog.pg_class AS target ON target.oid = trigger.tgrelid "
            "JOIN pg_catalog.pg_namespace AS namespace "
            "ON namespace.oid = target.relnamespace "
            "JOIN pg_catalog.pg_proc AS procedure "
            "ON procedure.oid = trigger.tgfoid "
            "WHERE namespace.nspname = 'memory_patch' "
            "AND target.relname = 'personal_memory_spaces' "
            "AND trigger.tgname = 'personal_memory_spaces_s27_state_guard' "
            "AND NOT trigger.tgisinternal",
        )
    )
    if trigger_rows != [
        {
            "trigger_name": "personal_memory_spaces_s27_state_guard",
            "function_name": "guard_personal_memory_step27_update",
        }
    ]:
        raise MigrationError("Step 27 lifecycle guard differs")
    return {
        "policy_count": len(policy_rows),
        "protected_table_count": 1,
        "runtime_table_grant_count": len(grants),
        "trigger_count": len(trigger_rows),
    }


def _validate_step28_function_grants(
    *,
    database: str,
    function_name: str,
    owner_role: str,
    rows: list[dict[str, str]],
    runtime_privileges: Sequence[str] = ("EXECUTE",),
) -> int:
    """Validate Cockroach-native direct UDF grants, including grant options."""

    expected_signature = STEP28_FUNCTION_SIGNATURES.get(function_name)
    expected_columns = {
        "database_name",
        "schema_name",
        "routine_id",
        "routine_signature",
        "grantee",
        "privilege_type",
        "is_grantable",
    }
    if expected_signature is None or not rows or any(
        set(row) != expected_columns for row in rows
    ):
        raise MigrationError("Step 28 helper-function grant catalog is invalid")
    routine_ids = {row["routine_id"] for row in rows}
    if (
        len(routine_ids) != 1
        or not next(iter(routine_ids)).isdigit()
        or any(
            row["database_name"] != database
            or row["schema_name"] != "memory_patch"
            or row["routine_signature"] != expected_signature
            for row in rows
        )
    ):
        raise MigrationError("Step 28 helper-function grant identity differs")
    actual_grants = {
        (row["grantee"], row["privilege_type"], row["is_grantable"])
        for row in rows
    }
    expected_grants = {
        ("admin", "ALL", "t"),
        (owner_role, "ALL", "t"),
        ("root", "ALL", "t"),
    }
    expected_grants.update(
        ("mp_app_runtime", privilege, "f")
        for privilege in runtime_privileges
    )
    if len(rows) != len(expected_grants) or actual_grants != expected_grants:
        raise MigrationError("Step 28 helper-function grants differ")
    return len(runtime_privileges)


def assert_step28_security_catalog(
    client: SqlClient,
    database: str,
) -> dict[str, Any]:
    """Verify the bounded INSERT-only candidate delta on existing tables."""

    manifest = load_correction_candidate_security_manifest()
    tables = manifest["tables"]
    slot_authority = manifest["slot_authority"]
    catalog_tables = [
        *tables,
        {
            "table": slot_authority["table"],
            "owner_role": slot_authority["owner_role"],
        },
    ]
    names = sorted(table["table"] for table in catalog_tables)
    quoted = ", ".join(sql_literal(name) for name in names)
    table_rows = parse_tsv(
        client.execute(
            database,
            "SELECT c.relname AS table_name, c.relrowsecurity, "
            "c.relforcerowsecurity, owner.rolname AS owner_role "
            "FROM pg_catalog.pg_class AS c "
            "JOIN pg_catalog.pg_namespace AS namespace "
            "ON namespace.oid = c.relnamespace "
            "JOIN pg_catalog.pg_roles AS owner ON owner.oid = c.relowner "
            "WHERE namespace.nspname = 'memory_patch' "
            f"AND c.relname IN ({quoted}) AND c.relkind = 'r' "
            "ORDER BY c.relname",
        )
    )
    expected_tables = [
        {
            "table_name": table["table"],
            "relrowsecurity": "t",
            "relforcerowsecurity": "t",
            "owner_role": table["owner_role"],
        }
        for table in sorted(catalog_tables, key=lambda row: row["table"])
    ]
    if table_rows != expected_tables:
        raise MigrationError("Step 28 table ownership or RLS state differs")
    policy_rows = parse_tsv(
        client.execute(
            database,
            "SELECT tablename, policyname, cmd, roles, qual, with_check "
            "FROM pg_catalog.pg_policies "
            "WHERE schemaname = 'memory_patch' "
            f"AND tablename IN ({quoted}) ORDER BY tablename, policyname",
        )
    )
    expected_policies = {
        table["select_policy"]: (table["table"], "SELECT") for table in tables
    }
    expected_policies.update(
        {
            table["insert_policy"]: (table["table"], "INSERT")
            for table in tables
        }
    )
    expected_policies.update(
        {
            policy_name: (slot_authority["table"], command.upper())
            for command, policy_name in slot_authority["policies"].items()
        }
    )
    actual_policies = {
        row["policyname"]: (row["tablename"], row["cmd"].upper())
        for row in policy_rows
    }
    if actual_policies != expected_policies:
        raise MigrationError("Step 28 final candidate policy set differs")
    for row in policy_rows:
        if "mp_app_runtime" not in row["roles"]:
            raise MigrationError("Step 28 policy lacks runtime role")
        if row["cmd"].upper() == "SELECT" and not row["qual"]:
            raise MigrationError("Step 28 SELECT policy lacks USING")
        if row["cmd"].upper() == "INSERT" and not row["with_check"]:
            raise MigrationError("Step 28 INSERT policy lacks WITH CHECK")
    grant_rows = parse_tsv(
        client.execute(
            database,
            "SELECT table_name, privilege_type "
            "FROM information_schema.table_privileges "
            "WHERE table_schema = 'memory_patch' "
            "AND grantee = 'mp_app_runtime' "
            f"AND table_name IN ({quoted}) ORDER BY table_name, privilege_type",
        )
    )
    expected_grants = sorted(
        [
            (table["table"], privilege)
            for table in tables
            for privilege in table["runtime_privileges"]
        ]
        + [
            (slot_authority["table"], privilege)
            for privilege in slot_authority["runtime_privileges"]
        ]
    )
    actual_grants = [
        (row["table_name"], row["privilege_type"]) for row in grant_rows
    ]
    if actual_grants != expected_grants:
        raise MigrationError("Step 28 runtime grants are not least privilege")
    quota_guard = manifest["quota_guard"]
    function_manifest = [
        *manifest["helper_functions"],
        {
            "function": quota_guard["function"],
            "owner_role": quota_guard["function_owner_role"],
            "runtime_privileges": quota_guard["function_runtime_privileges"],
            "security_type": quota_guard["function_security_type"],
        },
        {
            "function": slot_authority["update_guard_function"],
            "owner_role": slot_authority["owner_role"],
            "runtime_privileges": [],
            "security_type": "INVOKER",
        },
    ]
    function_names = [row["function"] for row in function_manifest]
    quoted_functions = ", ".join(
        sql_literal(name) for name in function_names
    )
    function_rows = parse_tsv(
        client.execute(
            database,
            "SELECT procedure.proname AS function_name, "
            "owner.rolname AS owner_role, procedure.prosecdef "
            "FROM pg_catalog.pg_proc AS procedure "
            "JOIN pg_catalog.pg_namespace AS namespace "
            "ON namespace.oid = procedure.pronamespace "
            "JOIN pg_catalog.pg_roles AS owner ON owner.oid = procedure.proowner "
            "WHERE namespace.nspname = 'memory_patch' "
            f"AND procedure.proname IN ({quoted_functions}) "
            "ORDER BY procedure.proname",
        )
    )
    expected_functions = [
        {
            "function_name": row["function"],
            "owner_role": row["owner_role"],
            "prosecdef": "f",
        }
        for row in sorted(function_manifest, key=lambda value: value["function"])
    ]
    if function_rows != expected_functions:
        raise MigrationError("Step 28 helper-function ownership or mode differs")
    runtime_function_grant_count = 0
    for function in function_manifest:
        function_name = function["function"]
        if function_name not in STEP28_FUNCTION_SIGNATURES:
            raise MigrationError("Step 28 helper-function identity differs")
        rows = parse_tsv(
            client.execute(
                database,
                "SHOW GRANTS ON FUNCTION memory_patch." + function_name,
            )
        )
        runtime_function_grant_count += _validate_step28_function_grants(
            database=database,
            function_name=function_name,
            owner_role=function["owner_role"],
            rows=rows,
            runtime_privileges=function["runtime_privileges"],
        )
    trigger_rows = parse_tsv(
        client.execute(
            database,
            "SELECT trigger.tgname AS trigger_name, "
            "target.relname AS table_name, procedure.proname AS function_name "
            "FROM pg_catalog.pg_trigger AS trigger "
            "JOIN pg_catalog.pg_class AS target ON target.oid = trigger.tgrelid "
            "JOIN pg_catalog.pg_namespace AS namespace "
            "ON namespace.oid = target.relnamespace "
            "JOIN pg_catalog.pg_proc AS procedure "
            "ON procedure.oid = trigger.tgfoid "
            "WHERE namespace.nspname = 'memory_patch' "
            "AND trigger.tgname IN ("
            f"{sql_literal(quota_guard['trigger'])}, "
            f"{sql_literal(slot_authority['update_guard_trigger'])}) "
            "AND NOT trigger.tgisinternal ORDER BY trigger.tgname",
        )
    )
    expected_triggers = [
        {
            "trigger_name": quota_guard["trigger"],
            "table_name": "memory_patch_proposals",
            "function_name": quota_guard["function"],
        },
        {
            "trigger_name": slot_authority["update_guard_trigger"],
            "table_name": slot_authority["table"],
            "function_name": slot_authority["update_guard_function"],
        },
    ]
    if trigger_rows != expected_triggers:
        raise MigrationError("Step 28 authority/quota trigger set differs")
    return {
        "helper_function_count": len(function_rows),
        "policy_count": len(policy_rows),
        "protected_table_count": len(table_rows),
        "runtime_function_grant_count": runtime_function_grant_count,
        "runtime_table_grant_count": len(grant_rows),
        "runtime_update_delete": False,
        "trigger_count": len(trigger_rows),
    }


def _validate_step29_function_grants(
    *,
    database: str,
    function_name: str,
    owner_role: str,
    rows: list[dict[str, str]],
    runtime_privileges: Sequence[str],
) -> int:
    expected_signature = STEP29_FUNCTION_SIGNATURES.get(function_name)
    expected_columns = {
        "database_name",
        "schema_name",
        "routine_id",
        "routine_signature",
        "grantee",
        "privilege_type",
        "is_grantable",
    }
    if expected_signature is None or not rows or any(
        set(row) != expected_columns for row in rows
    ):
        raise MigrationError("Step 29 helper-function grant catalog is invalid")
    routine_ids = {row["routine_id"] for row in rows}
    if (
        len(routine_ids) != 1
        or not next(iter(routine_ids)).isdigit()
        or any(
            row["database_name"] != database
            or row["schema_name"] != "memory_patch"
            or row["routine_signature"] != expected_signature
            for row in rows
        )
    ):
        raise MigrationError("Step 29 helper-function identity differs")
    expected_grants = {
        ("admin", "ALL", "t"),
        (owner_role, "ALL", "t"),
        ("root", "ALL", "t"),
    }
    expected_grants.update(
        ("mp_app_runtime", privilege, "f")
        for privilege in runtime_privileges
    )
    actual_grants = {
        (row["grantee"], row["privilege_type"], row["is_grantable"])
        for row in rows
    }
    if len(rows) != len(expected_grants) or actual_grants != expected_grants:
        raise MigrationError("Step 29 helper-function grants differ")
    return len(runtime_privileges)


def assert_step29_security_catalog(
    client: SqlClient,
    database: str,
) -> dict[str, Any]:
    """Verify final Step 29 owner-only RLS, grants, guards, and columns."""

    manifest = load_patch_validation_security_manifest()
    tables = manifest["tables"]
    names = [table["table"] for table in tables]
    quoted = ", ".join(sql_literal(name) for name in names)
    table_rows = parse_tsv(
        client.execute(
            database,
            "SELECT c.relname AS table_name, c.relrowsecurity, "
            "c.relforcerowsecurity, owner.rolname AS owner_role "
            "FROM pg_catalog.pg_class AS c "
            "JOIN pg_catalog.pg_namespace AS namespace "
            "ON namespace.oid = c.relnamespace "
            "JOIN pg_catalog.pg_roles AS owner ON owner.oid = c.relowner "
            "WHERE namespace.nspname = 'memory_patch' "
            f"AND c.relname IN ({quoted}) AND c.relkind = 'r' "
            "ORDER BY c.relname",
        )
    )
    expected_tables = [
        {
            "table_name": table["table"],
            "relrowsecurity": "t",
            "relforcerowsecurity": "t",
            "owner_role": table["owner_role"],
        }
        for table in sorted(tables, key=lambda row: row["table"])
    ]
    if table_rows != expected_tables:
        raise MigrationError("Step 29 table ownership or RLS state differs")
    policy_rows = parse_tsv(
        client.execute(
            database,
            "SELECT tablename, policyname, cmd, roles, qual, with_check "
            "FROM pg_catalog.pg_policies WHERE schemaname = 'memory_patch' "
            f"AND tablename IN ({quoted}) ORDER BY tablename, policyname",
        )
    )
    expected_policies: dict[str, tuple[str, str]] = {}
    for table in tables:
        expected_policies[table["select_policy"]] = (table["table"], "SELECT")
        for policy in table["insert_policies"]:
            expected_policies[policy] = (table["table"], "INSERT")
        if table["update_policy"] is not None:
            expected_policies[table["update_policy"]] = (
                table["table"],
                "UPDATE",
            )
    actual_policies = {
        row["policyname"]: (row["tablename"], row["cmd"].upper())
        for row in policy_rows
    }
    if actual_policies != expected_policies:
        raise MigrationError("Step 29 final policy set differs")
    for row in policy_rows:
        command = row["cmd"].upper()
        if "mp_app_runtime" not in row["roles"]:
            raise MigrationError("Step 29 policy lacks runtime role")
        if command == "SELECT" and not row["qual"]:
            raise MigrationError("Step 29 SELECT policy lacks USING")
        if command == "INSERT" and not row["with_check"]:
            raise MigrationError("Step 29 INSERT policy lacks WITH CHECK")
        if command == "UPDATE" and (not row["qual"] or not row["with_check"]):
            raise MigrationError("Step 29 UPDATE policy is incomplete")
    grant_rows = parse_tsv(
        client.execute(
            database,
            "SELECT table_name, privilege_type "
            "FROM information_schema.table_privileges "
            "WHERE table_schema = 'memory_patch' "
            "AND grantee = 'mp_app_runtime' "
            f"AND table_name IN ({quoted}) ORDER BY table_name, privilege_type",
        )
    )
    expected_grants = sorted(
        (table["table"], privilege)
        for table in tables
        for privilege in table["runtime_privileges"]
    )
    actual_grants = [
        (row["table_name"], row["privilege_type"]) for row in grant_rows
    ]
    if actual_grants != expected_grants:
        raise MigrationError("Step 29 runtime table grants are not least privilege")
    quota_guard = manifest["quota_guard"]
    transition_guard = manifest["transition_guard"]
    function_manifest = [
        *manifest["helper_functions"],
        {
            "function": quota_guard["function"],
            "owner_role": quota_guard["function_owner_role"],
            "runtime_privileges": quota_guard["function_runtime_privileges"],
            "security_type": quota_guard["function_security_type"],
        },
        {
            "function": transition_guard["function"],
            "owner_role": transition_guard["function_owner_role"],
            "runtime_privileges": transition_guard["function_runtime_privileges"],
            "security_type": transition_guard["function_security_type"],
        },
    ]
    function_names = [item["function"] for item in function_manifest]
    quoted_functions = ", ".join(sql_literal(name) for name in function_names)
    function_rows = parse_tsv(
        client.execute(
            database,
            "SELECT procedure.proname AS function_name, "
            "owner.rolname AS owner_role, procedure.prosecdef "
            "FROM pg_catalog.pg_proc AS procedure "
            "JOIN pg_catalog.pg_namespace AS namespace "
            "ON namespace.oid = procedure.pronamespace "
            "JOIN pg_catalog.pg_roles AS owner ON owner.oid = procedure.proowner "
            "WHERE namespace.nspname = 'memory_patch' "
            f"AND procedure.proname IN ({quoted_functions}) "
            "ORDER BY procedure.proname",
        )
    )
    expected_functions = [
        {
            "function_name": item["function"],
            "owner_role": item["owner_role"],
            "prosecdef": "f",
        }
        for item in sorted(function_manifest, key=lambda value: value["function"])
    ]
    if function_rows != expected_functions:
        raise MigrationError("Step 29 helper-function ownership or mode differs")
    runtime_function_grant_count = 0
    for function in function_manifest:
        rows = parse_tsv(
            client.execute(
                database,
                "SHOW GRANTS ON FUNCTION memory_patch." + function["function"],
            )
        )
        runtime_function_grant_count += _validate_step29_function_grants(
            database=database,
            function_name=function["function"],
            owner_role=function["owner_role"],
            rows=rows,
            runtime_privileges=function["runtime_privileges"],
        )
    trigger_rows = parse_tsv(
        client.execute(
            database,
            "SELECT trigger.tgname AS trigger_name, "
            "target.relname AS table_name, procedure.proname AS function_name "
            "FROM pg_catalog.pg_trigger AS trigger "
            "JOIN pg_catalog.pg_class AS target ON target.oid = trigger.tgrelid "
            "JOIN pg_catalog.pg_namespace AS namespace "
            "ON namespace.oid = target.relnamespace "
            "JOIN pg_catalog.pg_proc AS procedure ON procedure.oid = trigger.tgfoid "
            "WHERE namespace.nspname = 'memory_patch' "
            "AND trigger.tgname IN ("
            f"{sql_literal(quota_guard['trigger'])}, "
            f"{sql_literal(transition_guard['trigger'])}) "
            "AND NOT trigger.tgisinternal ORDER BY trigger.tgname",
        )
    )
    expected_triggers = sorted(
        [
            {
                "trigger_name": quota_guard["trigger"],
                "table_name": "memory_patch_proposals",
                "function_name": quota_guard["function"],
            },
            {
                "trigger_name": transition_guard["trigger"],
                "table_name": "memory_patch_proposals",
                "function_name": transition_guard["function"],
            },
        ],
        key=lambda row: row["trigger_name"],
    )
    if trigger_rows != expected_triggers:
        raise MigrationError("Step 29 quota/transition trigger set differs")
    column_rows = parse_tsv(
        client.execute(
            database,
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'memory_patch' "
            "AND table_name = 'memory_patch_proposals' "
            "AND column_name IN ('step29_dedup_key', 'step29_candidate_id', "
            "'step29_candidate_hash', 'step29_candidate_envelope_hash', "
            "'step29_target_binding_hash', 'step29_state_version', "
            "'step29_state_hash', 'step29_evidence_binding_hash', "
            "'step29_validation_receipt_hash') ORDER BY column_name",
        )
    )
    if [row["column_name"] for row in column_rows] != sorted(
        [
            "step29_dedup_key",
            "step29_candidate_envelope_hash",
            "step29_candidate_hash",
            "step29_candidate_id",
            "step29_evidence_binding_hash",
            "step29_state_hash",
            "step29_state_version",
            "step29_target_binding_hash",
            "step29_validation_receipt_hash",
        ]
    ):
        raise MigrationError("Step 29 proposal columns are incomplete")
    return {
        "helper_function_count": len(function_rows),
        "policy_count": len(policy_rows),
        "protected_table_count": len(table_rows),
        "runtime_function_grant_count": runtime_function_grant_count,
        "runtime_table_grant_count": len(grant_rows),
        "runtime_delete": False,
        "trigger_count": len(trigger_rows),
    }


def _validate_step30_function_grants(
    *,
    database: str,
    function_name: str,
    rows: list[dict[str, str]],
    runtime_roles: Sequence[str],
) -> int:
    signature = STEP30_FUNCTION_SIGNATURES.get(function_name)
    columns = {
        "database_name",
        "schema_name",
        "routine_id",
        "routine_signature",
        "grantee",
        "privilege_type",
        "is_grantable",
    }
    if signature is None or not rows or any(set(row) != columns for row in rows):
        raise MigrationError("Step 30 helper-function grant catalog is invalid")
    if (
        len({row["routine_id"] for row in rows}) != 1
        or not rows[0]["routine_id"].isdigit()
        or any(
            row["database_name"] != database
            or row["schema_name"] != "memory_patch"
            or row["routine_signature"] != signature
            for row in rows
        )
    ):
        raise MigrationError("Step 30 helper-function identity differs")
    expected = {
        ("admin", "ALL", "t"),
        ("mp_schema_owner", "ALL", "t"),
        ("root", "ALL", "t"),
    }
    expected.update((role, "EXECUTE", "f") for role in runtime_roles)
    actual = {
        (row["grantee"], row["privilege_type"], row["is_grantable"])
        for row in rows
    }
    if len(rows) != len(expected) or actual != expected:
        raise MigrationError("Step 30 helper-function grants differ")
    return len(runtime_roles)


def assert_step30_security_catalog(
    client: SqlClient,
    database: str,
) -> dict[str, Any]:
    """Verify the dedicated role, owner RLS, receipts, and exact three edges."""

    manifest = load_approval_commit_security_manifest()
    role_rows = parse_tsv(
        client.execute(
            "defaultdb",
            "SELECT rolname, rolcanlogin, rolcreaterole, rolcreatedb, "
            "rolbypassrls, rolsuper FROM pg_catalog.pg_roles "
            "WHERE rolname = 'mp_personal_memory_commit_helper'",
        )
    )
    if role_rows != [
        {
            "rolname": "mp_personal_memory_commit_helper",
            "rolcanlogin": "f",
            "rolcreaterole": "f",
            "rolcreatedb": "f",
            "rolbypassrls": "f",
            "rolsuper": "f",
        }
    ]:
        raise MigrationError("Step 30 dedicated role options differ")
    memberships = parse_tsv(
        client.execute(
            "defaultdb",
            "SELECT parent.rolname AS parent_role "
            "FROM pg_catalog.pg_auth_members AS membership "
            "JOIN pg_catalog.pg_roles AS member ON member.oid = membership.member "
            "JOIN pg_catalog.pg_roles AS parent ON parent.oid = membership.roleid "
            "WHERE member.rolname = 'mp_personal_memory_commit_helper' "
            "ORDER BY parent.rolname",
        )
    )
    if memberships:
        raise MigrationError("Step 30 dedicated role inherits another role")
    table_names = [table["table"] for table in manifest["tables"]]
    quoted = ", ".join(sql_literal(name) for name in table_names)
    table_rows = parse_tsv(
        client.execute(
            database,
            "SELECT c.relname AS table_name, c.relrowsecurity, "
            "c.relforcerowsecurity, owner.rolname AS owner_role "
            "FROM pg_catalog.pg_class AS c "
            "JOIN pg_catalog.pg_namespace AS namespace "
            "ON namespace.oid = c.relnamespace "
            "JOIN pg_catalog.pg_roles AS owner ON owner.oid = c.relowner "
            "WHERE namespace.nspname = 'memory_patch' "
            f"AND c.relname IN ({quoted}) AND c.relkind = 'r' "
            "ORDER BY c.relname",
        )
    )
    expected_tables = [
        {
            "table_name": name,
            "relrowsecurity": "t",
            "relforcerowsecurity": "t",
            "owner_role": "mp_schema_owner",
        }
        for name in sorted(table_names)
    ]
    if table_rows != expected_tables:
        raise MigrationError("Step 30 table ownership or RLS state differs")
    grant_rows = parse_tsv(
        client.execute(
            database,
            "SELECT table_name, privilege_type "
            "FROM information_schema.table_privileges "
            "WHERE table_schema = 'memory_patch' "
            "AND grantee = 'mp_personal_memory_commit_helper' "
            "ORDER BY table_name, privilege_type",
        )
    )
    expected_grants = sorted(
        (
            ("audit_events", "SELECT"),
            ("hat_scopes", "SELECT"),
            ("kernel_runs", "SELECT"),
            ("memory_items", "INSERT"),
            ("memory_items", "SELECT"),
            ("memory_items", "UPDATE"),
            ("memory_patch_approvals", "SELECT"),
            ("memory_patch_commits", "INSERT"),
            ("memory_patch_commits", "SELECT"),
            ("memory_patch_proposals", "SELECT"),
            ("memory_patch_proposals", "UPDATE"),
            ("patch_transition_records", "INSERT"),
            ("patch_transition_records", "SELECT"),
            ("persistence_operations", "INSERT"),
            ("persistence_operations", "SELECT"),
            ("persistence_operations", "UPDATE"),
            ("personal_memory_model_bindings", "SELECT"),
            ("personal_memory_quota_policies", "SELECT"),
            ("personal_memory_spaces", "SELECT"),
            ("personal_memory_spaces", "UPDATE"),
            ("tenants", "SELECT"),
            ("users", "SELECT"),
        )
    )
    actual_grants = [
        (row["table_name"], row["privilege_type"]) for row in grant_rows
    ]
    if actual_grants != expected_grants:
        raise MigrationError("Step 30 dedicated table grants differ")
    identity_policy_rows = parse_tsv(
        client.execute(
            database,
            "SELECT tablename, policyname, cmd, roles, qual "
            "FROM pg_catalog.pg_policies "
            "WHERE schemaname = 'memory_patch' "
            "AND policyname IN ('audit_events_s30_commit_owner_select', "
            "'kernel_runs_s30_commit_lineage_select', "
            "'tenants_s30_commit_identity_select', "
            "'users_s30_commit_identity_select') "
            "ORDER BY policyname",
        )
    )
    if {
        (row["tablename"], row["policyname"], row["cmd"].upper())
        for row in identity_policy_rows
    } != {
        ("audit_events", "audit_events_s30_commit_owner_select", "SELECT"),
        ("kernel_runs", "kernel_runs_s30_commit_lineage_select", "SELECT"),
        ("tenants", "tenants_s30_commit_identity_select", "SELECT"),
        ("users", "users_s30_commit_identity_select", "SELECT"),
    }:
        raise MigrationError("Step 30 identity read policies differ")
    for row in identity_policy_rows:
        expected_function = (
            "tenant_context_matches"
            if row["tablename"] == "tenants"
            else "user_context_matches"
        )
        if (
            "mp_personal_memory_commit_helper" not in row["roles"]
            or expected_function not in row["qual"]
        ):
            raise MigrationError("Step 30 identity read policy is not scoped")
        if row["tablename"] == "audit_events" and (
            "USER_ID IS NOT NULL" not in row["qual"].upper()
            or "PERSONAL_MEMORY_SPACE_ID IS NOT NULL"
            not in row["qual"].upper()
        ):
            raise MigrationError("Step 30 audit read policy is not owner-private")
    functions = [
        *manifest["helper_functions"],
        {
            "function": "guard_step30_commit_slot_update",
            "owner_role": "mp_schema_owner",
            "runtime_roles": [],
            "security_type": "INVOKER",
        },
        {
            "function": "guard_step30_proposal_transition",
            "owner_role": "mp_schema_owner",
            "runtime_roles": [],
            "security_type": "INVOKER",
        },
        {
            "function": "guard_step30_memory_item",
            "owner_role": "mp_schema_owner",
            "runtime_roles": [],
            "security_type": "INVOKER",
        },
    ]
    names = [value["function"] for value in functions]
    quoted_functions = ", ".join(sql_literal(name) for name in names)
    function_rows = parse_tsv(
        client.execute(
            database,
            "SELECT procedure.proname AS function_name, "
            "owner.rolname AS owner_role, procedure.prosecdef "
            "FROM pg_catalog.pg_proc AS procedure "
            "JOIN pg_catalog.pg_namespace AS namespace "
            "ON namespace.oid = procedure.pronamespace "
            "JOIN pg_catalog.pg_roles AS owner ON owner.oid = procedure.proowner "
            "WHERE namespace.nspname = 'memory_patch' "
            f"AND procedure.proname IN ({quoted_functions}) "
            "ORDER BY procedure.proname",
        )
    )
    expected_functions = [
        {
            "function_name": value["function"],
            "owner_role": "mp_schema_owner",
            "prosecdef": "f",
        }
        for value in sorted(functions, key=lambda item: item["function"])
    ]
    if function_rows != expected_functions:
        raise MigrationError("Step 30 helper-function ownership or mode differs")
    function_grants = 0
    for function in functions:
        rows = parse_tsv(
            client.execute(
                database,
                "SHOW GRANTS ON FUNCTION memory_patch." + function["function"],
            )
        )
        function_grants += _validate_step30_function_grants(
            database=database,
            function_name=function["function"],
            rows=rows,
            runtime_roles=function["runtime_roles"],
        )
    trigger_rows = parse_tsv(
        client.execute(
            database,
            "SELECT trigger.tgname AS trigger_name, target.relname AS table_name, "
            "procedure.proname AS function_name "
            "FROM pg_catalog.pg_trigger AS trigger "
            "JOIN pg_catalog.pg_class AS target ON target.oid = trigger.tgrelid "
            "JOIN pg_catalog.pg_namespace AS namespace "
            "ON namespace.oid = target.relnamespace "
            "JOIN pg_catalog.pg_proc AS procedure ON procedure.oid = trigger.tgfoid "
            "WHERE namespace.nspname = 'memory_patch' "
            "AND trigger.tgname IN ('memory_items_s30_guard', "
            "'memory_patch_proposals_s30_transition_guard', "
            "'personal_memory_spaces_s30_commit_guard') "
            "AND NOT trigger.tgisinternal ORDER BY trigger.tgname",
        )
    )
    if trigger_rows != [
        {
            "trigger_name": "memory_items_s30_guard",
            "table_name": "memory_items",
            "function_name": "guard_step30_memory_item",
        },
        {
            "trigger_name": "memory_patch_proposals_s30_transition_guard",
            "table_name": "memory_patch_proposals",
            "function_name": "guard_step30_proposal_transition",
        },
        {
            "trigger_name": "personal_memory_spaces_s30_commit_guard",
            "table_name": "personal_memory_spaces",
            "function_name": "guard_step30_commit_slot_update",
        },
    ]:
        raise MigrationError("Step 30 transition/quota trigger set differs")
    return {
        "commit_role_bypassrls": False,
        "commit_role_can_login": False,
        "dedicated_table_grant_count": len(grant_rows),
        "helper_function_count": len(function_rows),
        "identity_read_policy_count": len(identity_policy_rows),
        "runtime_function_grant_count": function_grants,
        "quota_lock_effective_column": "candidate_quota_epoch",
        "quota_lock_guarded_table_update": True,
        "protected_table_count": len(table_rows),
        "trigger_count": len(trigger_rows),
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
        elif migration.migration_id == STEP9_MIGRATION_ID:
            client.execute(
                database,
                "BEGIN;\n" + database_sql + "\nCOMMIT;",
                timeout=timeout,
            )
            assert_step9_security_catalog(client, database)
            database_sql = ""
        elif migration.migration_id == STEP10_MIGRATION_ID:
            client.execute(
                database,
                "BEGIN;\n" + database_sql + "\nCOMMIT;",
                timeout=timeout,
            )
            assert_step10_security_catalog(client, database)
            database_sql = ""
        elif migration.migration_id == STEP11_MIGRATION_ID:
            client.execute(
                database,
                "BEGIN;\n" + database_sql + "\nCOMMIT;",
                timeout=timeout,
            )
            assert_step11_security_catalog(client, database)
            database_sql = ""
        elif migration.migration_id == STEP12_MIGRATION_ID:
            client.execute(
                database,
                "BEGIN;\n" + database_sql + "\nCOMMIT;",
                timeout=timeout,
            )
            database_sql = ""
        elif migration.migration_id == STEP19_MIGRATION_ID:
            client.execute(
                database,
                "BEGIN;\n" + database_sql + "\nCOMMIT;",
                timeout=timeout,
            )
            database_sql = ""
        elif migration.migration_id == STEP27_MIGRATION_ID:
            client.execute(
                database,
                "BEGIN;\n" + database_sql + "\nCOMMIT;",
                timeout=timeout,
            )
            assert_step27_security_catalog(client, database)
            database_sql = ""
        elif migration.migration_id == STEP28_MIGRATION_ID:
            client.execute(
                database,
                "BEGIN;\n" + database_sql + "\nCOMMIT;",
                timeout=timeout,
            )
            assert_step28_security_catalog(client, database)
            database_sql = ""
        elif migration.migration_id == STEP29_MIGRATION_ID:
            try:
                client.execute(
                    database,
                    "BEGIN;\n" + database_sql + "\nCOMMIT;",
                    timeout=timeout,
                )
            except SqlError as exc:
                raise SqlError(
                    f"{migration.migration_id} database transaction failed: {exc}",
                    sqlstate=exc.sqlstate,
                ) from exc
            assert_step29_security_catalog(client, database)
            database_sql = ""
        elif migration.migration_id == STEP30_MIGRATION_ID:
            cluster_role_sql, database_sql = split_cluster_role_ddl(
                migration.sql,
                begin_marker=STEP30_CLUSTER_ROLE_BEGIN,
                end_marker=STEP30_CLUSTER_ROLE_END,
                role_name="mp_personal_memory_commit_helper",
            )
            client.execute(
                database,
                "SET allow_role_memberships_to_change_during_transaction = true;\n"
                + cluster_role_sql,
                timeout=timeout,
            )
            try:
                client.execute(
                    database,
                    "BEGIN;\n" + database_sql + "\nCOMMIT;",
                    timeout=timeout,
                )
            except SqlError as exc:
                raise SqlError(
                    f"{migration.migration_id} database transaction failed: {exc}",
                    sqlstate=exc.sqlstate,
                ) from exc
            assert_step30_security_catalog(client, database)
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
    persistence_manifest = load_persistence_manifest()
    source_registry_manifest = load_source_registry_manifest()
    ingestion_saga_manifest = load_ingestion_saga_manifest()
    parsing_pipeline_manifest = load_parsing_pipeline_manifest()
    expected_tables = sorted(
        [
            *schema_manifest["required_tables"],
            *[
                row["table"]
                for row in security_manifest["security_internal_tables"]
            ],
            *[row["table"] for row in persistence_manifest["tables"]],
            *[row["table"] for row in source_registry_manifest["tables"]],
            *[row["table"] for row in ingestion_saga_manifest["tables"]],
            *[row["table"] for row in parsing_pipeline_manifest["tables"]],
            "hat_registry_entries",
            "hat_registry_events",
            "hat_runtime_bindings",
            "chunk_embeddings",
            "personal_memory_quota_policies",
        ]
    )
    if catalog["tables"] != expected_tables:
        raise MigrationError(
            "live catalog table set differs from the Step 4–28 chain"
        )
    indexes = set(catalog["explicit_indexes"])
    required_persistence_indexes = {
        "persistence_operations_external_identity_uq",
        "persistence_operations_private_idempotency_uq",
        "persistence_operations_shared_idempotency_uq",
    }
    required_ingestion_indexes = {
        "ingestion_sagas_private_idempotency_uq",
        "ingestion_sagas_shared_idempotency_uq",
    }
    required_parsing_indexes = {
        "parse_security_findings_review_idx",
        "parsed_documents_scope_idx",
        "parsed_sections_document_ordinal_idx",
    }
    required_embedding_indexes = {
        "chunk_embeddings_model_lookup_idx",
        "chunk_embeddings_vector_l2_idx",
    }
    missing_indexes = (
        set(schema_manifest["explicit_indexes"])
        | required_persistence_indexes
        | required_ingestion_indexes
        | required_parsing_indexes
        | required_embedding_indexes
    ) - indexes
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


def run_live_validation(
    binary: Path,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    validate_timeout(timeout)
    offline = offline_validate()
    migration_count = offline["migration_count"]
    binary_identity = verify_binary_identity(binary)
    run_id = "mp_step10_" + uuid.uuid4().hex[:12]
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
        first_apply = apply_migrations(client, database_a, timeout=timeout)
        second_apply = apply_migrations(client, database_a, timeout=timeout)
        reproduction_apply = apply_migrations(
            client,
            database_b,
            timeout=timeout,
        )
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
        step9_security_a = assert_step9_security_catalog(client, database_a)
        step9_security_b = assert_step9_security_catalog(client, database_b)
        if (
            step9_security_a["security_digest"]
            != step9_security_b["security_digest"]
        ):
            raise MigrationError("Step 9 security reproduction digest differs")
        step10_security_a = assert_step10_security_catalog(client, database_a)
        step10_security_b = assert_step10_security_catalog(client, database_b)
        if (
            step10_security_a["security_digest"]
            != step10_security_b["security_digest"]
        ):
            raise MigrationError("Step 10 security reproduction digest differs")
        step11_security_a = assert_step11_security_catalog(client, database_a)
        step11_security_b = assert_step11_security_catalog(client, database_b)
        if (
            step11_security_a["security_digest"]
            != step11_security_b["security_digest"]
        ):
            raise MigrationError("Step 11 security reproduction digest differs")
        step29_security_a = assert_step29_security_catalog(client, database_a)
        step29_security_b = assert_step29_security_catalog(client, database_b)
        if step29_security_a != step29_security_b:
            raise MigrationError("Step 29 security reproduction differs")
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
                "explicit_index_count": len(catalog_a["explicit_indexes"]),
                "schema_digest": catalog_a["schema_digest"],
                "table_count": len(catalog_a["tables"]),
                "protected_table_count": offline["protected_table_count"],
            },
            "server_version": server_version.splitlines()[0],
            "step9_security": step9_security_a,
            "step10_security": step10_security_a,
            "step11_security": step11_security_a,
            "step29_security": step29_security_a,
            "status": "PASS",
        }
    finally:
        if client is not None:
            for database in reversed(created_databases):
                try:
                    drop_database(client, database, timeout=timeout)
                except MigrationError as exc:
                    cleanup_errors.append(str(exc))
        cleanup = (
            runtime.graceful_stop_and_remove(
                client,
                owned_children_reaped=True,
            )
            if client is not None
            and runtime.process is not None
            and runtime.process.poll() is None
            else runtime.stop_and_remove()
        )
        cleanup_errors.extend(cleanup["cleanup_errors"])
    if cleanup_errors:
        raise MigrationError(f"cleanup failed: {cleanup_errors}")
    if live_result is None:
        raise MigrationError("live validation did not produce a result")
    live_result["cleanup"] = {
        "databases_removed": len(created_databases) == 2,
        "force_kill_used": cleanup["force_kill_used"],
        "drain_command_completed": cleanup["drain_command_completed"],
        "drain_completion_marker": cleanup["drain_completion_marker"],
        "drain_shutdown_requested": cleanup["drain_shutdown_requested"],
        "graceful_shutdown_requested": cleanup[
            "graceful_shutdown_requested"
        ],
        "no_password_credentials_created": True,
        "owned_child_processes_reaped": cleanup[
            "owned_child_processes_reaped"
        ],
        "owned_pid_exited": cleanup["pid_exited"],
        "ports_closed": cleanup["ports_closed"],
        "process_exit_accepted": cleanup["process_exit_accepted"],
        "process_exit_code": cleanup["process_exit_code"],
        "runtime_duration_seconds": cleanup["runtime_duration_seconds"],
        "shutdown_budget": cleanup.get("shutdown_budget"),
        "shutdown_method": cleanup["shutdown_method"],
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
                result = run_live_validation(binary, timeout=args.timeout)
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
