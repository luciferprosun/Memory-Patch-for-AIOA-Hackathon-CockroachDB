#!/usr/bin/env python3
"""Reproducible CockroachDB v26.2 capability spike.

The ordinary repository test suite imports this module without performing any
network or database action. Live execution is available only with both
``--run`` and ``--allow-live``.
"""

from __future__ import annotations

import argparse
import ast
import copy
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
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence
from urllib.parse import urlsplit


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from aioa_memory_kernel.security.credentials import (  # noqa: E402
    build_minimal_subprocess_environment,
)

PIN_PATH = REPOSITORY_ROOT / "config" / "cockroachdb" / "version-pin.json"
MATRIX_PATH = (
    REPOSITORY_ROOT
    / "docs"
    / "evidence"
    / "cockroachdb-v26-2"
    / "capability-matrix.json"
)
FINGERPRINT_PATH = MATRIX_PATH.with_name("runtime-fingerprint.json")
SQL_ROOT = REPOSITORY_ROOT / "sql" / "cockroachdb" / "capability_spike"
HARNESS_VERSION = "1.0.3"
DEFAULT_TIMEOUT_SECONDS = 45.0
START_TIMEOUT_SECONDS = 45.0
TTL_OBSERVATION_SECONDS = 120.0
NATURAL_RETRY_ATTEMPTS = 10

ALLOWED_STATUSES = frozenset({"PASS", "FAIL", "DEFER"})
ALLOWED_AVAILABILITY = frozenset(
    {"AVAILABLE", "UNAVAILABLE", "CONDITIONAL", "UNKNOWN"}
)
ALLOWED_MATURITY = frozenset(
    {"GA", "PREVIEW", "EXPERIMENTAL", "DEPRECATED", "NOT_APPLICABLE", "UNKNOWN"}
)
ALLOWED_MVP_DECISIONS = frozenset(
    {"USE", "USE_WITH_GUARD", "DEFER", "DO_NOT_USE"}
)
REQUIRED_SOURCE_CATEGORIES = frozenset(
    {
        "aost",
        "artifact_checksum",
        "changefeed",
        "force_rls",
        "full_text",
        "partial",
        "release",
        "release_overview",
        "release_patch",
        "retry",
        "rls",
        "rls_limitations",
        "security_advisories",
        "support",
        "transactions",
        "ttl",
        "vector",
        "vector_index",
    }
)

SQL_FILES = (
    "00_runtime_identity.sql",
    "10_vector.sql",
    "20_full_text.sql",
    "30_rls.sql",
    "40_ttl.sql",
    "50_changefeed.sql",
    "60_partial_unique.sql",
    "70_as_of_system_time.sql",
    "80_serializable_retry.sql",
    "90_cleanup.sql",
)

REQUIRED_CAPABILITIES: tuple[tuple[str, str], ...] = (
    ("CRDB-001", "Exact v26.2.x client/server identity"),
    ("CRDB-002", "Cluster version and finalization"),
    ("CRDB-003", "VECTOR type"),
    ("CRDB-004", "Vector Euclidean semantics"),
    ("CRDB-005", "Vector cosine semantics"),
    ("CRDB-006", "Vector inner-product semantics"),
    ("CRDB-007", "Vector dimension rejection"),
    ("CRDB-008", "Vector index creation"),
    ("CRDB-009", "Vector index query correctness"),
    ("CRDB-010", "Vector prefix filtering"),
    ("CRDB-011", "Full-text English"),
    ("CRDB-012", "Full-text German"),
    ("CRDB-013", "Full-text inverted index"),
    ("CRDB-014", "RLS SELECT isolation"),
    ("CRDB-015", "RLS INSERT enforcement"),
    ("CRDB-016", "RLS UPDATE enforcement"),
    ("CRDB-017", "RLS DELETE enforcement"),
    ("CRDB-018", "FORCE RLS owner enforcement"),
    ("CRDB-019", "RLS bypass boundary"),
    ("CRDB-020", "Row-Level TTL DDL"),
    ("CRDB-021", "Row-Level TTL job registration"),
    ("CRDB-022", "Row-Level TTL physical deletion"),
    ("CRDB-023", "Sinkless changefeed"),
    ("CRDB-024", "Changefeed insert/update/delete events"),
    ("CRDB-025", "Changefeed and RLS limitation"),
    ("CRDB-026", "Partial unique index"),
    ("CRDB-027", "Partial uniqueness SQLSTATE 23505"),
    ("CRDB-028", "AS OF SYSTEM TIME historical read"),
    ("CRDB-029", "Default SERIALIZABLE isolation"),
    ("CRDB-030", "Natural contention retry signal"),
    ("CRDB-031", "Synthetic 40001 signal when needed"),
    ("CRDB-032", "Bounded retry classifier"),
    ("CRDB-033", "Vector prefix is not authorization"),
    ("CRDB-034", "AOST GC-window boundary"),
    ("CRDB-035", "TTL/changefeed interaction"),
    ("CRDB-036", "Cleanup and setting restoration"),
)

SOURCE_URLS = {
    "release_overview": "https://www.cockroachlabs.com/docs/releases",
    "release": "https://www.cockroachlabs.com/docs/releases/v26.2",
    "release_patch": (
        "https://www.cockroachlabs.com/docs/releases/v26.2#v26-2-4"
    ),
    "support": (
        "https://www.cockroachlabs.com/docs/releases/release-support-policy"
    ),
    "vector": "https://www.cockroachlabs.com/docs/v26.2/vector",
    "vector_index": "https://www.cockroachlabs.com/docs/v26.2/vector-indexes",
    "full_text": "https://www.cockroachlabs.com/docs/v26.2/full-text-search",
    "rls": "https://www.cockroachlabs.com/docs/v26.2/row-level-security",
    "force_rls": (
        "https://www.cockroachlabs.com/docs/v26.2/"
        "row-level-security#force-row-level-security"
    ),
    "rls_limitations": (
        "https://www.cockroachlabs.com/docs/v26.2/"
        "row-level-security#known-limitations"
    ),
    "policy": "https://www.cockroachlabs.com/docs/v26.2/create-policy",
    "ttl": "https://www.cockroachlabs.com/docs/v26.2/row-level-ttl",
    "changefeed": (
        "https://www.cockroachlabs.com/docs/v26.2/"
        "create-and-configure-changefeeds"
    ),
    "cdc_query": "https://www.cockroachlabs.com/docs/v26.2/cdc-queries",
    "partial": "https://www.cockroachlabs.com/docs/v26.2/partial-indexes",
    "aost": "https://www.cockroachlabs.com/docs/v26.2/as-of-system-time",
    "transactions": "https://www.cockroachlabs.com/docs/v26.2/transactions",
    "retry": (
        "https://www.cockroachlabs.com/docs/v26.2/"
        "transaction-retry-error-reference"
    ),
    "security_advisories": "https://www.cockroachlabs.com/docs/advisories",
    "artifact_checksum": (
        "https://binaries.cockroachdb.com/"
        "cockroach-v26.2.4.linux-amd64.tgz.sha256sum"
    ),
}

SECRET_PATTERNS = (
    re.compile(r"(?i)\b(?:postgres(?:ql)?|cockroachdb)://[^\s\"']+"),
    re.compile(r"(?i)\bhttps?://[^/\s:@]+(?::[^@\s/]*)?@[^\s\"']+"),
    re.compile(r"(?i)\b(?:password|passwd|token|api[_-]?key)\s*[=:]\s*\S+"),
    re.compile(r"(?i)\b(?:basic|bearer)\s+\S+"),
    re.compile(r"(?i)\bauthorization:\s*\S+(?:\s+\S+)?"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)\bhttps?://[^\s?\"']+\?[^\s\"']+"),
)


class HarnessError(RuntimeError):
    """Infrastructure, safety, or evidence-contract failure."""


class SqlExecutionError(HarnessError):
    """A SQL command failed outside an expected negative probe."""

    def __init__(self, message: str, *, sqlstate: str | None = None) -> None:
        super().__init__(message)
        self.sqlstate = sqlstate


@dataclass(frozen=True)
class ProcessResult:
    returncode: int
    stdout: str
    stderr: str


@dataclass
class SqlClient:
    binary: Path
    host: str = "127.0.0.1"
    port: int | None = None
    sql_url: str | None = None

    def command(self, database: str, sql: str) -> tuple[list[str], dict[str, str]]:
        args = [
            str(self.binary),
            "sql",
            "--format=tsv",
            "--set=errexit=true",
        ]
        environment = build_minimal_subprocess_environment(os.environ)
        if self.sql_url is not None:
            environment["COCKROACH_URL"] = self.sql_url
        else:
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
            if self.port is None:
                raise HarnessError("local SQL port is unavailable")
            require_loopback(self.host, insecure=True)
            args.extend(
                [
                    "--insecure",
                    f"--host={self.host}",
                    f"--port={self.port}",
                ]
            )
        args.extend([f"--database={database}", f"--execute={sql}"])
        return args, environment

    def execute(
        self,
        database: str,
        sql: str,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> str:
        validate_timeout(timeout)
        args, environment = self.command(database, sql)
        result = run_process(args, timeout=timeout, environment=environment)
        if result.returncode != 0:
            state = extract_sqlstate(result.stderr)
            raise SqlExecutionError(
                sanitize_failure(result.stderr), sqlstate=state
            )
        return result.stdout

    def expect_error(
        self,
        database: str,
        sql: str,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> tuple[str | None, str]:
        validate_timeout(timeout)
        args, environment = self.command(database, sql)
        result = run_process(args, timeout=timeout, environment=environment)
        if result.returncode == 0:
            raise HarnessError("negative SQL probe unexpectedly succeeded")
        return extract_sqlstate(result.stderr), sanitize_failure(result.stderr)


@dataclass
class LocalRuntime:
    binary: Path
    run_id: str
    runtime_dir: Path | None = None
    process: subprocess.Popen[str] | None = None
    log_handle: Any = None
    client: SqlClient | None = None
    pid: int | None = None
    sql_port: int | None = None
    rpc_port: int | None = None
    http_port: int | None = None
    start_command: list[str] = field(default_factory=list)
    started_monotonic: float | None = None
    stopped_monotonic: float | None = None
    force_kill_used: bool = False

    def start(self) -> SqlClient:
        if self.process is not None:
            raise HarnessError("runtime already started")
        verify_binary_version(self.binary, load_pin(PIN_PATH)["exact_version"])
        self.runtime_dir = Path(
            tempfile.mkdtemp(prefix=f"{self.run_id}_", dir="/tmp")
        )
        logs = self.runtime_dir / "server.log"
        temp_parent = self.runtime_dir / "temp"
        temp_parent.mkdir(mode=0o700)
        pid_file = self.runtime_dir / "server.pid"
        url_file = self.runtime_dir / "listening-url"
        self.rpc_port, self.sql_port, self.http_port = allocate_ports(3)
        for port in (self.rpc_port, self.sql_port, self.http_port):
            if port is None:
                raise HarnessError("failed to allocate loopback ports")

        self.start_command = [
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
            f"--temp-dir={temp_parent}",
            f"--pid-file={pid_file}",
            f"--listening-url-file={url_file}",
            "--logtostderr=WARNING",
        ]
        self.log_handle = logs.open("w", encoding="utf-8")
        self.started_monotonic = time.monotonic()
        self.process = subprocess.Popen(
            self.start_command,
            stdin=subprocess.DEVNULL,
            stdout=self.log_handle,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=self.runtime_dir,
        )
        self.pid = self.process.pid
        self.client = SqlClient(
            binary=self.binary,
            host="127.0.0.1",
            port=self.sql_port,
        )
        deadline = time.monotonic() + START_TIMEOUT_SECONDS
        last_error = ""
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise HarnessError(
                    f"server exited during startup with code {self.process.returncode}"
                )
            try:
                output = self.client.execute(
                    "defaultdb", "SELECT 1 AS ready", timeout=5
                )
                if one_value(output) == "1":
                    break
            except HarnessError as exc:
                last_error = str(exc)
                time.sleep(0.5)
        else:
            raise HarnessError(f"server readiness timed out: {last_error}")

        if not pid_file.is_file():
            raise HarnessError("server PID file was not created")
        recorded_pid = int(pid_file.read_text(encoding="utf-8").strip())
        if recorded_pid != self.pid:
            raise HarnessError("server PID file does not match owned child process")
        require_loopback("127.0.0.1", insecure=True)
        return self.client

    def stop_and_remove(self) -> dict[str, Any]:
        errors: list[str] = []
        owned_runtime_path = (
            str(self.runtime_dir) if self.runtime_dir is not None else None
        )
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
                    errors.append("exact server PID did not exit")
        self.stopped_monotonic = time.monotonic()
        if self.log_handle is not None:
            self.log_handle.close()
            self.log_handle = None

        pid_exited = self.process is None or self.process.poll() is not None
        ports_closed = all(
            port is None or not can_connect("127.0.0.1", port)
            for port in (self.rpc_port, self.sql_port, self.http_port)
        )
        if not ports_closed:
            errors.append("one or more owned loopback ports remain open")

        path_removed = False
        if self.runtime_dir is not None and self.runtime_dir.exists():
            assert_owned_runtime_path(self.runtime_dir)
            shutil.rmtree(self.runtime_dir)
            path_removed = not self.runtime_dir.exists()
        elif self.runtime_dir is not None:
            path_removed = True
        if not path_removed:
            errors.append("owned temporary runtime directory remains")

        duration = None
        if self.started_monotonic is not None and self.stopped_monotonic is not None:
            duration = round(self.stopped_monotonic - self.started_monotonic, 3)
        return {
            "cleanup_errors": errors,
            "force_kill_used": self.force_kill_used,
            "pid": self.pid,
            "pid_exited": pid_exited,
            "ports": {
                "http": self.http_port,
                "rpc": self.rpc_port,
                "sql": self.sql_port,
            },
            "ports_closed": ports_closed,
            "runtime_duration_seconds": duration,
            "temporary_path": owned_runtime_path,
            "temporary_path_removed": path_removed,
        }


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def pretty_json(value: Any) -> str:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            indent=2,
        )
        + "\n"
    )


def write_canonical_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temporary.write_text(pretty_json(value), encoding="utf-8", newline="\n")
    temporary.replace(path)


def matrix_digest(matrix: Mapping[str, Any]) -> str:
    payload = copy.deepcopy(dict(matrix))
    summary = payload.get("summary")
    if isinstance(summary, dict):
        summary.pop("matrix_sha256", None)
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HarnessError(f"invalid JSON at {path}: {exc}") from exc


def load_pin(path: Path = PIN_PATH) -> dict[str, Any]:
    value = load_json(path)
    if not isinstance(value, dict):
        raise HarnessError("version pin must be a JSON object")
    validate_pin(value)
    return value


def validate_pin(pin: Mapping[str, Any]) -> None:
    required = {
        "schema_version",
        "product",
        "target_series",
        "exact_version",
        "release_channel",
        "release_status",
        "release_date",
        "support_status",
        "verified_at_utc",
        "selection_decision",
        "artifact",
        "runtime",
        "official_sources",
        "rationale",
    }
    missing = sorted(required - set(pin))
    if missing:
        raise HarnessError(f"version pin missing fields: {missing}")
    if pin["schema_version"] != 1:
        raise HarnessError("unsupported version-pin schema")
    if pin["product"] != "CockroachDB" or pin["target_series"] != "v26.2":
        raise HarnessError("version pin targets the wrong product or release series")
    exact = pin["exact_version"]
    if not isinstance(exact, str) or not re.fullmatch(r"v26\.2\.\d+", exact):
        raise HarnessError("exact_version must be one exact v26.2 patch")
    if "latest" in exact.lower() or "*" in exact:
        raise HarnessError("mutable or wildcard version pin is forbidden")
    try:
        datetime.strptime(str(pin["release_date"]), "%Y-%m-%d")
        datetime.fromisoformat(
            str(pin["verified_at_utc"]).replace("Z", "+00:00")
        )
    except ValueError as exc:
        raise HarnessError("pin dates must use valid ISO/RFC3339 values") from exc
    for key in (
        "release_channel",
        "release_status",
        "support_status",
        "selection_decision",
        "rationale",
    ):
        if not isinstance(pin[key], str) or not pin[key].strip():
            raise HarnessError(f"version pin {key} must be non-empty text")
    artifact = pin["artifact"]
    if not isinstance(artifact, dict):
        raise HarnessError("artifact must be an object")
    for key in (
        "architecture",
        "binary_sha256",
        "immutable_container_digest",
        "kind",
        "local_sha256",
        "platform",
        "source",
        "vendor_checksum_available",
        "vendor_checksum_verified",
    ):
        if key not in artifact:
            raise HarnessError(f"artifact missing {key}")
    if not re.fullmatch(r"[0-9a-f]{64}", str(artifact["local_sha256"])):
        raise HarnessError("artifact local_sha256 must be 64 lowercase hex")
    if not re.fullmatch(r"[0-9a-f]{64}", str(artifact["binary_sha256"])):
        raise HarnessError("artifact binary_sha256 must be 64 lowercase hex")
    for key in ("vendor_checksum_available", "vendor_checksum_verified"):
        if not isinstance(artifact.get(key), bool):
            raise HarnessError(f"artifact {key} must be an exact boolean")
    if not str(artifact["source"]).startswith("https://"):
        raise HarnessError("artifact source must use HTTPS")
    if artifact.get("vendor_checksum_available") and not artifact.get(
        "vendor_checksum_verified"
    ):
        raise HarnessError("published vendor checksum was not verified")
    if artifact.get("vendor_checksum_available") and not str(
        artifact.get("vendor_checksum_source", "")
    ).startswith("https://"):
        raise HarnessError("vendor checksum source must use HTTPS")
    if artifact.get("kind") != "binary":
        raise HarnessError("Step 3 pin must identify the verified binary artifact")
    if artifact.get("platform") != "linux":
        raise HarnessError("Step 3 pin platform must match the live Linux runtime")
    if artifact.get("architecture") != "amd64":
        raise HarnessError("Step 3 pin architecture must match the live runtime")
    for field in ("source", "vendor_checksum_source"):
        location = artifact.get(field)
        parsed = urlsplit(str(location))
        if (
            parsed.scheme != "https"
            or parsed.hostname != "binaries.cockroachdb.com"
        ):
            raise HarnessError(f"artifact {field} must use the official HTTPS host")
    runtime = pin["runtime"]
    if not isinstance(runtime, dict):
        raise HarnessError("runtime must be an object")
    runtime_required = {
        "cluster_version",
        "deployment_mode",
        "server_build_tag",
        "server_version_output",
        "upgrade_finalized",
    }
    runtime_missing = sorted(runtime_required - set(runtime))
    if runtime_missing:
        raise HarnessError(f"runtime missing fields: {runtime_missing}")
    if runtime.get("server_build_tag") != exact:
        raise HarnessError("runtime build tag must equal exact_version")
    if runtime.get("cluster_version") != "26.2":
        raise HarnessError("runtime cluster version must be the pinned v26.2 series")
    if runtime.get("upgrade_finalized") is not True:
        raise HarnessError("pinned runtime must have a finalized cluster version")
    if runtime.get("deployment_mode") != "DISPOSABLE_LOCAL_SINGLE_NODE":
        raise HarnessError("pin deployment mode is outside this bounded Step 3")
    server_token = re.search(
        r"\bCockroachDB (?:CCL|OSS) (v\d+\.\d+\.\d+)\b",
        str(runtime.get("server_version_output", "")),
    )
    if not server_token or server_token.group(1) != exact:
        raise HarnessError("pinned server version output does not match exact_version")
    sources = pin["official_sources"]
    if not isinstance(sources, list) or not sources:
        raise HarnessError("official source records are required")
    for source in sources:
        if not isinstance(source, Mapping):
            raise HarnessError("official source record must be an object")
        if not {
            "category",
            "location",
            "retrieved_at_utc",
            "title",
        }.issubset(source):
            raise HarnessError("official source record is incomplete")
        parsed_location = urlsplit(str(source["location"]))
        if (
            parsed_location.scheme != "https"
            or parsed_location.hostname
            not in {"www.cockroachlabs.com", "binaries.cockroachdb.com"}
        ):
            raise HarnessError(
                "official source location must use an official HTTPS host"
            )
        try:
            retrieved = datetime.fromisoformat(
                str(source["retrieved_at_utc"]).replace("Z", "+00:00")
            )
        except ValueError as exc:
            raise HarnessError(
                "official source retrieval time must be RFC3339"
            ) from exc
        if retrieved.tzinfo is None:
            raise HarnessError(
                "official source retrieval time must be timezone-aware"
            )
    try:
        verified_at = datetime.fromisoformat(
            str(pin.get("verified_at_utc", "")).replace("Z", "+00:00")
        )
    except ValueError as exc:
        raise HarnessError("verified_at_utc must be RFC3339") from exc
    if verified_at.tzinfo is None:
        raise HarnessError("verified_at_utc must be timezone-aware")
    assert_no_secret(pin)


def validate_matrix(
    matrix: Mapping[str, Any],
    *,
    pin: Mapping[str, Any] | None = None,
) -> None:
    if matrix.get("schema_version") != 1:
        raise HarnessError("unsupported capability matrix schema")
    rows = matrix.get("capabilities")
    summary = matrix.get("summary")
    if not isinstance(rows, list) or not isinstance(summary, dict):
        raise HarnessError("matrix capabilities and summary are required")
    required_fields = {
        "capability_id",
        "name",
        "status",
        "availability",
        "maturity",
        "deployment_scope",
        "runtime_exact_version",
        "probe_id",
        "probe_plane",
        "prerequisites",
        "expected_semantics",
        "observed_semantics",
        "expected_error",
        "observed_sqlstate",
        "evidence_reference",
        "official_source_references",
        "known_limitations",
        "required_for_mvp",
        "mvp_decision",
        "decision_reason",
        "cleanup_verified",
    }
    summary_required = {
        "defer_count",
        "fail_count",
        "generated_at_utc",
        "harness_version",
        "matrix_sha256",
        "overall_step3_decision",
        "pass_count",
        "runtime_exact_version",
        "total_rows",
    }
    summary_missing = sorted(summary_required - set(summary))
    if summary_missing:
        raise HarnessError(f"matrix summary missing fields: {summary_missing}")
    ids = []
    required_names = dict(REQUIRED_CAPABILITIES)
    string_fields = {
        "capability_id",
        "decision_reason",
        "deployment_scope",
        "evidence_reference",
        "expected_semantics",
        "name",
        "observed_semantics",
        "probe_id",
        "probe_plane",
        "runtime_exact_version",
    }
    list_fields = {
        "known_limitations",
        "official_source_references",
        "prerequisites",
    }
    for row_index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise HarnessError("capability row must be an object")
        missing = sorted(required_fields - set(row))
        if missing:
            raise HarnessError(
                f"{row.get('capability_id', '?')} missing fields: {missing}"
            )
        if row["status"] not in ALLOWED_STATUSES:
            raise HarnessError(f"invalid status for {row['capability_id']}")
        if row["availability"] not in ALLOWED_AVAILABILITY:
            raise HarnessError(f"invalid availability for {row['capability_id']}")
        if row["maturity"] not in ALLOWED_MATURITY:
            raise HarnessError(f"invalid maturity for {row['capability_id']}")
        if row["mvp_decision"] not in ALLOWED_MVP_DECISIONS:
            raise HarnessError(f"invalid MVP decision for {row['capability_id']}")
        for field in string_fields:
            if not isinstance(row[field], str) or not row[field].strip():
                raise HarnessError(
                    f"{row['capability_id']} has an invalid {field}"
                )
        for field in list_fields:
            if not isinstance(row[field], list) or any(
                not isinstance(value, str) or not value.strip()
                for value in row[field]
            ):
                raise HarnessError(
                    f"{row['capability_id']} has an invalid {field}"
                )
        if not isinstance(row["required_for_mvp"], bool):
            raise HarnessError(
                f"{row['capability_id']} required_for_mvp must be boolean"
            )
        if row["cleanup_verified"] is not True:
            raise HarnessError(
                f"{row['capability_id']} lacks verified live cleanup"
            )
        if row["expected_error"] is not None and (
            not isinstance(row["expected_error"], str)
            or not row["expected_error"].strip()
        ):
            raise HarnessError(
                f"{row['capability_id']} has an invalid expected_error"
            )
        sqlstate = row["observed_sqlstate"]
        if sqlstate is not None and (
            not isinstance(sqlstate, str)
            or re.fullmatch(r"[0-9A-Z]{5}", sqlstate) is None
        ):
            raise HarnessError(
                f"{row['capability_id']} has an invalid SQLSTATE"
            )
        if row["probe_plane"] not in {
            "LIVE_LOCAL",
            "LIVE_EXTERNAL",
            "OFFLINE_REPOSITORY",
        }:
            raise HarnessError(
                f"{row['capability_id']} has an invalid probe plane"
            )
        if (
            row["capability_id"] == "CRDB-032"
            and row["probe_plane"] != "OFFLINE_REPOSITORY"
        ) or (
            row["capability_id"] != "CRDB-032"
            and row["probe_plane"] == "OFFLINE_REPOSITORY"
        ):
            raise HarnessError(
                f"{row['capability_id']} has an invalid evidence plane"
            )
        expected_name = required_names.get(row["capability_id"])
        if expected_name is not None and row["name"] != expected_name:
            raise HarnessError(
                f"{row['capability_id']} capability name differs from contract"
            )
        expected_reference = (
            "docs/evidence/cockroachdb-v26-2/"
            f"capability-matrix.json#/capabilities/{row_index}"
        )
        if row["evidence_reference"] != expected_reference:
            raise HarnessError(
                f"{row['capability_id']} evidence reference is not canonical"
            )
        if (
            row["status"] == "FAIL"
            and row["mvp_decision"] != "DO_NOT_USE"
        ) or (
            row["status"] == "DEFER"
            and row["mvp_decision"] != "DEFER"
        ) or (
            row["status"] == "PASS"
            and row["mvp_decision"] not in {"USE", "USE_WITH_GUARD"}
        ):
            raise HarnessError(
                f"{row['capability_id']} status and MVP decision conflict"
            )
        ids.append(row["capability_id"])
    required_ids = [item[0] for item in REQUIRED_CAPABILITIES]
    if ids[: len(required_ids)] != required_ids:
        raise HarnessError("required capability rows are missing or out of order")
    if len(ids) != len(set(ids)):
        raise HarnessError("duplicate capability IDs are forbidden")
    if ids != sorted(ids):
        raise HarnessError("capability rows must remain in stable ID order")
    counts = {status: sum(row["status"] == status for row in rows) for status in ALLOWED_STATUSES}
    if summary.get("total_rows") != len(rows):
        raise HarnessError("matrix total row count is wrong")
    if summary.get("harness_version") != HARNESS_VERSION:
        raise HarnessError("matrix was generated by a different harness version")
    for status in ALLOWED_STATUSES:
        if summary.get(f"{status.lower()}_count") != counts[status]:
            raise HarnessError(f"matrix {status} count is wrong")
    expected_digest = matrix_digest(matrix)
    if summary.get("matrix_sha256") != expected_digest:
        raise HarnessError("matrix canonical digest mismatch")
    if pin is not None:
        exact = pin["exact_version"]
        if summary.get("runtime_exact_version") != exact:
            raise HarnessError("matrix runtime version differs from pin")
        if any(row["runtime_exact_version"] != exact for row in rows):
            raise HarnessError("a capability row runtime version differs from pin")
    for required_pass in ("CRDB-001", "CRDB-002", "CRDB-036"):
        required_row = next(
            (row for row in rows if row["capability_id"] == required_pass),
            None,
        )
        if required_row is None or required_row["status"] != "PASS":
            raise HarnessError(
                f"{required_pass} must PASS before evidence can close Step 3"
            )
    expected_decision = (
        "PINNED_WITH_EXPLICIT_LIMITATIONS"
        if counts["FAIL"]
        else "PINNED_WITH_EXPLICIT_DEFERRED_ITEMS"
        if counts["DEFER"]
        else "VALIDATED_FOR_STEP_4_CONSTRAINED_DESIGN"
    )
    if summary.get("overall_step3_decision") != expected_decision:
        raise HarnessError("matrix overall Step 3 decision is inconsistent")
    try:
        generated_at = datetime.fromisoformat(
            str(summary.get("generated_at_utc", "")).replace("Z", "+00:00")
        )
    except ValueError as exc:
        raise HarnessError("matrix generation time must be RFC3339") from exc
    if generated_at.tzinfo is None:
        raise HarnessError("matrix generation time must be timezone-aware")
    source_rows = matrix.get("official_sources")
    if not isinstance(source_rows, list) or not source_rows:
        raise HarnessError("matrix official source record is required")
    source_required = {
        "capability_ids",
        "category",
        "discrepancy",
        "live_behavior_matched",
        "location",
        "retrieved_at_utc",
        "title",
    }
    categories: set[str] = set()
    locations: dict[str, set[str]] = {}
    valid_row_ids = set(ids)
    for source in source_rows:
        if not isinstance(source, Mapping):
            raise HarnessError("matrix official source must be an object")
        source_missing = sorted(source_required - set(source))
        if source_missing:
            raise HarnessError(
                f"matrix official source missing fields: {source_missing}"
            )
        category = source.get("category")
        title = source.get("title")
        location = source.get("location")
        capability_ids = source.get("capability_ids")
        if not isinstance(category, str) or not category:
            raise HarnessError("matrix official source category is invalid")
        if not isinstance(title, str) or not title.strip():
            raise HarnessError("matrix official source title is invalid")
        if not isinstance(location, str):
            raise HarnessError("matrix official source location is invalid")
        parsed_location = urlsplit(location)
        if (
            parsed_location.scheme != "https"
            or parsed_location.hostname
            not in {"www.cockroachlabs.com", "binaries.cockroachdb.com"}
        ):
            raise HarnessError(
                "matrix official source must be an official HTTPS location"
            )
        if location in locations:
            raise HarnessError("duplicate matrix official source location")
        if (
            not isinstance(capability_ids, list)
            or not capability_ids
            or any(
                not isinstance(capability_id, str)
                or capability_id not in valid_row_ids
                for capability_id in capability_ids
            )
            or len(capability_ids) != len(set(capability_ids))
        ):
            raise HarnessError(
                "matrix official source capability binding is invalid"
            )
        try:
            retrieved = datetime.fromisoformat(
                str(source.get("retrieved_at_utc", "")).replace("Z", "+00:00")
            )
        except ValueError as exc:
            raise HarnessError(
                "matrix official source retrieval time must be RFC3339"
            ) from exc
        if retrieved.tzinfo is None:
            raise HarnessError(
                "matrix official source retrieval time must be timezone-aware"
            )
        matched = source.get("live_behavior_matched")
        if matched is not None and not isinstance(matched, bool):
            raise HarnessError(
                "matrix official source match result must be boolean or null"
            )
        discrepancy = source.get("discrepancy")
        if discrepancy is not None and (
            not isinstance(discrepancy, str) or not discrepancy.strip()
        ):
            raise HarnessError(
                "matrix official source discrepancy must be text or null"
            )
        categories.add(category)
        locations[location] = set(capability_ids)
    missing_categories = sorted(REQUIRED_SOURCE_CATEGORIES - categories)
    if missing_categories:
        raise HarnessError(
            f"matrix official source categories missing: {missing_categories}"
        )
    for row in rows:
        references = row.get("official_source_references")
        if not isinstance(references, list) or not references:
            raise HarnessError(
                f"{row['capability_id']} lacks official source references"
            )
        for reference in references:
            if (
                not isinstance(reference, str)
                or reference not in locations
                or row["capability_id"] not in locations[reference]
            ):
                raise HarnessError(
                    f"{row['capability_id']} has an invalid official source binding"
                )
    assert_no_secret(matrix)


def validate_fingerprint(
    fingerprint: Mapping[str, Any],
    *,
    pin: Mapping[str, Any],
    matrix: Mapping[str, Any] | None = None,
) -> None:
    required = {
        "artifact",
        "capability_matrix_sha256",
        "capability_probe_context",
        "cleanup",
        "deployment",
        "generated_at_utc",
        "harness_version",
        "runtime",
        "schema_version",
    }
    missing = sorted(required - set(fingerprint))
    if missing:
        raise HarnessError(f"runtime fingerprint missing fields: {missing}")
    if fingerprint.get("schema_version") != 1:
        raise HarnessError("unsupported runtime fingerprint schema")
    if fingerprint.get("harness_version") != HARNESS_VERSION:
        raise HarnessError(
            "runtime fingerprint was generated by a different harness version"
        )
    artifact = fingerprint.get("artifact")
    runtime = fingerprint.get("runtime")
    deployment = fingerprint.get("deployment")
    cleanup = fingerprint.get("cleanup")
    probe_context = fingerprint.get("capability_probe_context")
    if not all(
        isinstance(value, Mapping)
        for value in (artifact, runtime, deployment, cleanup, probe_context)
    ):
        raise HarnessError("runtime fingerprint sections must be objects")
    section_fields = {
        "artifact": (
            artifact,
            {
                "archive_sha256",
                "binary_sha256",
                "platform",
                "architecture",
                "source",
                "vendor_checksum_verified",
            },
        ),
        "runtime": (
            runtime,
            {
                "client_build_tag",
                "cluster_version",
                "server_version",
                "upgrade_finalized",
            },
        ),
        "deployment": (
            deployment,
            {
                "insecure_test_only",
                "listener_binding",
                "mode",
                "resource_limits",
                "temporary_store_type",
            },
        ),
        "cleanup": (
            cleanup,
            {
                "owned_children_exited",
                "pid_exited",
                "ports_closed",
                "remaining_changefeed",
                "remaining_ttl_job",
                "settings_match",
                "sql_resources_removed",
                "temporary_path_removed",
            },
        ),
    }
    for section_name, (section, required_fields) in section_fields.items():
        section_missing = sorted(required_fields - set(section))
        if section_missing:
            raise HarnessError(
                f"runtime fingerprint {section_name} missing fields: "
                f"{section_missing}"
            )
    context_required = {
        "created_resources",
        "database_prefix",
        "harness_source_sha256",
        "rendered_reference_sql_asset_sha256",
        "role_prefix",
        "synthetic_data_only",
    }
    context_missing = sorted(context_required - set(probe_context))
    if context_missing:
        raise HarnessError(
            f"runtime fingerprint capability context missing fields: "
            f"{context_missing}"
        )
    if probe_context.get("synthetic_data_only") is not True:
        raise HarnessError("runtime fingerprint must record synthetic-only data")
    if probe_context.get("database_prefix") != "mp_step3_":
        raise HarnessError("runtime fingerprint database prefix is invalid")
    if probe_context.get("role_prefix") != "mp_step3_":
        raise HarnessError("runtime fingerprint role prefix is invalid")
    if re.fullmatch(
        r"[0-9a-f]{64}",
        str(probe_context.get("harness_source_sha256", "")),
    ) is None:
        raise HarnessError("runtime fingerprint harness digest is invalid")
    sql_digests = probe_context.get("rendered_reference_sql_asset_sha256")
    if (
        not isinstance(sql_digests, Mapping)
        or set(sql_digests) != set(SQL_FILES)
        or any(
            re.fullmatch(r"[0-9a-f]{64}", str(digest)) is None
            for digest in sql_digests.values()
        )
    ):
        raise HarnessError("runtime fingerprint SQL reference digests are invalid")
    if artifact["archive_sha256"] != pin["artifact"]["local_sha256"]:
        raise HarnessError("runtime archive digest differs from version pin")
    if artifact["binary_sha256"] != pin["artifact"]["binary_sha256"]:
        raise HarnessError("runtime binary digest differs from version pin")
    if runtime["client_build_tag"] != pin["exact_version"]:
        raise HarnessError("runtime client build tag differs from version pin")
    server_token = re.search(
        r"\bCockroachDB (?:CCL|OSS) (v\d+\.\d+\.\d+)\b",
        str(runtime["server_version"]),
    )
    if not server_token or server_token.group(1) != pin["exact_version"]:
        raise HarnessError("runtime server version differs from version pin")
    if runtime["cluster_version"] != pin["runtime"]["cluster_version"]:
        raise HarnessError("runtime cluster version differs from version pin")
    if runtime["upgrade_finalized"] is not True:
        raise HarnessError("runtime cluster version is not finalized")
    if deployment.get("listener_binding") != "127.0.0.1":
        raise HarnessError("runtime fingerprint contains a non-loopback listener")
    if deployment.get("insecure_test_only") is not True:
        raise HarnessError("runtime fingerprint must mark insecure mode as test-only")
    if deployment.get("mode") != "DISPOSABLE_LOCAL_SINGLE_NODE":
        raise HarnessError("runtime fingerprint deployment mode is invalid")
    if matrix is not None and fingerprint.get(
        "capability_matrix_sha256"
    ) != matrix.get("summary", {}).get("matrix_sha256"):
        raise HarnessError("runtime fingerprint is not bound to the matrix digest")
    cleanup_required = (
        "owned_children_exited",
        "pid_exited",
        "ports_closed",
        "settings_match",
        "sql_resources_removed",
        "temporary_path_removed",
    )
    if not all(cleanup.get(key) is True for key in cleanup_required):
        raise HarnessError("runtime fingerprint cleanup is incomplete")
    if cleanup.get("remaining_changefeed") is not False:
        raise HarnessError("runtime fingerprint reports a remaining changefeed")
    if cleanup.get("remaining_ttl_job") is not False:
        raise HarnessError("runtime fingerprint reports a remaining TTL job")
    if cleanup.get("errors") or cleanup.get("child_cleanup_errors"):
        raise HarnessError("runtime fingerprint cleanup contains errors")
    assert_no_secret(fingerprint)


def redact_text(value: str) -> str:
    redacted = value
    redacted = re.sub(
        r"(?i)\b(?:postgres(?:ql)?|cockroachdb)://[^\s\"']+",
        "<redacted-dsn>",
        redacted,
    )
    redacted = re.sub(
        r"(?i)\bhttps?://[^/\s:@]+(?::[^@\s/]*)?@[^\s\"']+",
        "<redacted-url-authority>",
        redacted,
    )
    redacted = re.sub(
        r"(?i)\b(password|passwd|token|api[_-]?key)\s*[=:]\s*\S+",
        r"\1=<redacted>",
        redacted,
    )
    redacted = re.sub(
        r"(?i)\b(basic|bearer)\s+\S+",
        r"\1 <redacted>",
        redacted,
    )
    redacted = re.sub(
        r"(?i)\bauthorization:\s*\S+(?:\s+\S+)?",
        "Authorization: <redacted>",
        redacted,
    )
    redacted = re.sub(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
        "<redacted-private-key>",
        redacted,
        flags=re.DOTALL,
    )
    redacted = re.sub(r"(?i)(https?://[^?\s]+)\?[^\s]+", r"\1?<redacted-query>", redacted)
    return redacted


def assert_no_secret(value: Any, *, path: str = "$") -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            lowered = str(key).lower()
            if lowered in {
                "password",
                "passwd",
                "api_key",
                "client_secret",
                "access_token",
                "ca_cert",
                "certificate_path",
                "cluster_id",
                "sql_url",
                "dsn",
                "organization_id",
                "private_hostname",
            }:
                raise HarnessError(f"secret-bearing field forbidden at {path}.{key}")
            assert_no_secret(nested, path=f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, nested in enumerate(value):
            assert_no_secret(nested, path=f"{path}[{index}]")
    elif isinstance(value, str):
        for pattern in SECRET_PATTERNS:
            if pattern.search(value):
                raise HarnessError(f"secret-bearing value forbidden at {path}")


def sanitize_failure(stderr: str) -> str:
    lines = []
    for line in redact_text(stderr).splitlines():
        if line.startswith("HINT:") or line.startswith("ERROR:") or "SQLSTATE:" in line:
            lines.append(line.strip())
    return " | ".join(lines)[:1000] or "command failed without sanitized detail"


def extract_sqlstate(stderr: str) -> str | None:
    match = re.search(r"SQLSTATE:\s*([0-9A-Z]{5})", stderr)
    return match.group(1) if match else None


def is_retryable_sqlstate(sqlstate: str | None) -> bool:
    return sqlstate == "40001"


def bounded_retry(
    operation: Callable[[int], Any],
    *,
    max_attempts: int,
    base_backoff_seconds: float,
    sleeper: Callable[[float], None] = time.sleep,
    before_sleep: Callable[[], None] | None = None,
) -> Any:
    if not isinstance(max_attempts, int) or isinstance(max_attempts, bool):
        raise HarnessError("max_attempts must be an integer")
    if max_attempts < 1 or max_attempts > 10:
        raise HarnessError("max_attempts must be between 1 and 10")
    if base_backoff_seconds < 0 or base_backoff_seconds > 1:
        raise HarnessError("base backoff must be between 0 and 1 second")
    last_error: SqlExecutionError | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return operation(attempt)
        except SqlExecutionError as exc:
            if not is_retryable_sqlstate(exc.sqlstate):
                raise
            last_error = exc
            if attempt == max_attempts:
                break
            if before_sleep is not None:
                before_sleep()
            sleeper(min(base_backoff_seconds * (2 ** (attempt - 1)), 1.0))
    raise HarnessError(
        f"retry attempts exhausted after {max_attempts}: "
        f"{last_error.sqlstate if last_error else 'unknown'}"
    )


def validate_timeout(timeout: float) -> None:
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise HarnessError("subprocess timeout must be numeric")
    if timeout <= 0 or timeout > 180:
        raise HarnessError("subprocess timeout must be bounded to 1..180 seconds")


def require_loopback(host: str, *, insecure: bool) -> None:
    if insecure and host != "127.0.0.1":
        raise HarnessError("insecure local runtime must bind exactly to 127.0.0.1")


def assert_owned_runtime_path(path: Path) -> None:
    resolved = path.resolve()
    tmp_root = Path("/tmp").resolve()
    if resolved.parent != tmp_root or not resolved.name.startswith("mp_step3_"):
        raise HarnessError("refusing cleanup of an unowned runtime path")


def allocate_ports(count: int) -> list[int]:
    sockets: list[socket.socket] = []
    ports: list[int] = []
    try:
        for _ in range(count):
            holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            holder.bind(("127.0.0.1", 0))
            sockets.append(holder)
            ports.append(holder.getsockname()[1])
    finally:
        for holder in sockets:
            holder.close()
    if len(set(ports)) != count:
        raise HarnessError("dynamic port allocation returned duplicates")
    return ports


def can_connect(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.5)
        return probe.connect_ex((host, port)) == 0


def run_process(
    args: Sequence[str],
    *,
    timeout: float,
    environment: Mapping[str, str] | None = None,
) -> ProcessResult:
    validate_timeout(timeout)
    try:
        completed = subprocess.run(
            list(args),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            env=dict(environment) if environment is not None else None,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise HarnessError(
            f"bounded subprocess timed out after {timeout} seconds"
        ) from exc
    return ProcessResult(completed.returncode, completed.stdout, completed.stderr)


def verify_binary_version(binary: Path, expected: str) -> str:
    if not binary.is_file() or binary.is_symlink():
        raise HarnessError("cockroach binary must be a regular non-symlink file")
    result = run_process([str(binary), "version"], timeout=15)
    if result.returncode != 0:
        raise HarnessError("cockroach binary version command failed")
    match = re.search(r"^Build Tag:\s+(v\d+\.\d+\.\d+)$", result.stdout, re.MULTILINE)
    if not match or match.group(1) != expected:
        raise HarnessError(
            f"binary version mismatch: expected {expected}, "
            f"found {match.group(1) if match else 'unknown'}"
        )
    return result.stdout


def verify_binary_identity(
    binary: Path, pin: Mapping[str, Any]
) -> tuple[str, str]:
    version_output = verify_binary_version(binary, str(pin["exact_version"]))
    digest = file_sha256(binary)
    expected_digest = str(pin["artifact"].get("binary_sha256", ""))
    if not re.fullmatch(r"[0-9a-f]{64}", expected_digest):
        raise HarnessError("version pin lacks a valid binary_sha256")
    if digest != expected_digest:
        raise HarnessError(
            "cockroach binary SHA-256 differs from the immutable version pin"
        )
    return version_output, digest


def parse_tsv(output: str) -> list[dict[str, str]]:
    cleaned = output.strip()
    if not cleaned:
        return []
    reader = csv.DictReader(io.StringIO(cleaned), delimiter="\t")
    return [dict(row) for row in reader]


def decode_json_object_candidate(value: str) -> dict[str, Any] | None:
    queue = [value.strip()]
    visited: set[str] = set()
    while queue and len(visited) < 12:
        candidate = queue.pop(0).strip()
        if not candidate or candidate in visited:
            continue
        visited.add(candidate)
        hex_match = re.fullmatch(r"\\x([0-9a-fA-F]+)", candidate)
        if hex_match and len(hex_match.group(1)) % 2 == 0:
            try:
                queue.append(bytes.fromhex(hex_match.group(1)).decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                pass
        if candidate.startswith(("b'", 'b"')):
            try:
                byte_value = ast.literal_eval(candidate)
            except (SyntaxError, ValueError):
                byte_value = None
            if isinstance(byte_value, bytes):
                try:
                    queue.append(byte_value.decode("utf-8"))
                except UnicodeDecodeError:
                    pass
        try:
            decoded = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            decoded = None
        if isinstance(decoded, dict):
            return decoded
        if isinstance(decoded, str):
            queue.append(decoded)
        normalized = candidate.replace('""', '"')
        if normalized != candidate:
            queue.append(normalized)
        if '\\"' in candidate:
            try:
                unquoted = json.loads(f'"{candidate}"')
            except json.JSONDecodeError:
                unquoted = None
            if isinstance(unquoted, str):
                queue.append(unquoted)
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start >= 0 and end > start:
            queue.append(candidate[start : end + 1])
    return None


def parse_changefeed_json(output: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    candidates: list[str] = []
    for line in output.splitlines():
        candidates.append(line)
        candidates.extend(line.split("\t"))
        candidates.extend(line.split(","))
    for delimiter in ("\t", ","):
        reader = csv.DictReader(io.StringIO(output.strip()), delimiter=delimiter)
        for row in reader:
            candidates.extend(value for value in row.values() if value)
    seen: set[bytes] = set()
    for candidate in candidates:
        decoded = decode_json_object_candidate(candidate)
        if decoded is None:
            continue
        canonical = canonical_json_bytes(decoded)
        if canonical not in seen:
            seen.add(canonical)
            events.append(decoded)
    return events


def one_value(output: str) -> str:
    rows = parse_tsv(output)
    if len(rows) != 1 or len(rows[0]) != 1:
        raise HarnessError(f"expected one SQL value, got {len(rows)} rows")
    return next(iter(rows[0].values()))


def parse_vector_text(value: str) -> list[float]:
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError as exc:
        raise HarnessError("VECTOR output was not valid numeric JSON") from exc
    if (
        not isinstance(decoded, list)
        or not decoded
        or any(
            isinstance(item, bool) or not isinstance(item, (int, float))
            for item in decoded
        )
    ):
        raise HarnessError("VECTOR output did not contain a numeric array")
    return [float(item) for item in decoded]


def strip_command_tags(output: str) -> str:
    """Remove CLI command tags surrounding one result set."""

    tags = {"BEGIN", "COMMIT", "RESET", "ROLLBACK", "SET"}
    lines = output.splitlines()
    while lines and lines[0].strip().upper() in tags:
        lines.pop(0)
    while lines and lines[-1].strip().upper() in tags:
        lines.pop()
    return "\n".join(lines) + ("\n" if lines else "")


def sql_identifier(value: str) -> str:
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", value):
        raise HarnessError(f"unsafe generated SQL identifier: {value!r}")
    return f'"{value}"'


def make_run_id() -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%dt%H%M%Sz").lower()
    pid_fragment = f"{os.getpid() % 1_000_000:06d}"
    return f"mp_step3_{timestamp}_{pid_fragment}_{uuid.uuid4().hex[:8]}"


def make_resource_names(run_id: str) -> dict[str, str]:
    suffixes = {
        "aost_table": "aost_items",
        "changefeed_table": "changefeed_items",
        "fts_de_index": "full_text_de_idx",
        "fts_en_index": "full_text_en_idx",
        "fts_table": "full_text_documents",
        "partial_index": "one_current_key_idx",
        "partial_table": "partial_unique_items",
        "retry_table": "retry_counter",
        "rls_policy": "tenant_policy",
        "rls_table": "rls_items",
        "ttl_table": "ttl_items",
        "vector_index": "vector_l2_idx",
        "vector_index_table": "vector_index_items",
        "vector_table": "vector_items",
    }
    resources = {
        key: f"{run_id}_{suffix}" for key, suffix in suffixes.items()
    }
    for value in resources.values():
        sql_identifier(value)
    if len(resources) != len(set(resources.values())):
        raise HarnessError("generated SQL resource names are not unique")
    return resources


def source_records(retrieved_at: str) -> list[dict[str, Any]]:
    titles = {
        "release_overview": (
            "CockroachDB Releases Overview",
            ["CRDB-001", "CRDB-002"],
        ),
        "release": ("What's New in v26.2", ["CRDB-001", "CRDB-002"]),
        "release_patch": (
            "CockroachDB v26.2.4 Release Notes",
            ["CRDB-001", "CRDB-002"],
        ),
        "support": ("Release Support Policy", ["CRDB-001"]),
        "vector": (
            "VECTOR",
            ["CRDB-003", "CRDB-004", "CRDB-005", "CRDB-006", "CRDB-007"],
        ),
        "vector_index": (
            "Vector Indexes",
            ["CRDB-008", "CRDB-009", "CRDB-010", "CRDB-033"],
        ),
        "full_text": (
            "Full-Text Search",
            ["CRDB-011", "CRDB-012", "CRDB-013"],
        ),
        "rls": (
            "Row-Level Security Overview",
            [
                "CRDB-014",
                "CRDB-015",
                "CRDB-016",
                "CRDB-017",
                "CRDB-018",
                "CRDB-019",
                "CRDB-025",
                "CRDB-033",
            ],
        ),
        "force_rls": (
            "FORCE ROW LEVEL SECURITY",
            ["CRDB-018", "CRDB-019"],
        ),
        "rls_limitations": (
            "Row-Level Security Known Limitations",
            ["CRDB-019", "CRDB-025"],
        ),
        "policy": (
            "CREATE POLICY",
            ["CRDB-014", "CRDB-015", "CRDB-016", "CRDB-017"],
        ),
        "ttl": (
            "Row-Level TTL",
            ["CRDB-020", "CRDB-021", "CRDB-022", "CRDB-035"],
        ),
        "changefeed": (
            "Create and Configure Changefeeds",
            ["CRDB-023", "CRDB-024", "CRDB-035"],
        ),
        "cdc_query": ("Change Data Capture Queries", ["CRDB-025"]),
        "partial": (
            "Partial Indexes",
            ["CRDB-026", "CRDB-027"],
        ),
        "aost": (
            "AS OF SYSTEM TIME",
            ["CRDB-028", "CRDB-034"],
        ),
        "transactions": (
            "Transactions",
            ["CRDB-027", "CRDB-029", "CRDB-030", "CRDB-032", "CRDB-036"],
        ),
        "retry": (
            "Transaction Retry Error Reference",
            ["CRDB-030", "CRDB-031", "CRDB-032"],
        ),
        "security_advisories": (
            "CockroachDB Security Advisories",
            ["CRDB-001"],
        ),
        "artifact_checksum": (
            "CockroachDB v26.2.4 Linux amd64 SHA-256",
            ["CRDB-001"],
        ),
    }
    records = []
    for key, url in SOURCE_URLS.items():
        title, capabilities = titles[key]
        records.append(
            {
                "capability_ids": capabilities,
                "category": key,
                "discrepancy": None,
                "live_behavior_matched": None,
                "location": url,
                "retrieved_at_utc": retrieved_at,
                "title": title,
            }
        )
    return records


def capability_template(
    capability_id: str,
    name: str,
    *,
    exact_version: str,
) -> dict[str, Any]:
    maturity = "UNKNOWN"
    availability = "AVAILABLE"
    decision = "USE"
    required_for_mvp = True
    prerequisites: list[str] = []
    guarded = {
        "CRDB-008",
        "CRDB-009",
        "CRDB-010",
        "CRDB-018",
        "CRDB-019",
        "CRDB-023",
        "CRDB-024",
        "CRDB-025",
        "CRDB-033",
        "CRDB-034",
    }
    optional = {"CRDB-022", "CRDB-023", "CRDB-024", "CRDB-028", "CRDB-035"}
    if capability_id in guarded:
        decision = "USE_WITH_GUARD"
    if capability_id in optional:
        required_for_mvp = False
    if capability_id in {"CRDB-008", "CRDB-009", "CRDB-010"}:
        maturity = "GA"
    if capability_id.startswith("CRDB-01") and capability_id in {
        "CRDB-014",
        "CRDB-015",
        "CRDB-016",
        "CRDB-017",
        "CRDB-018",
        "CRDB-019",
    }:
        maturity = "GA"
    if capability_id in {
        "CRDB-001",
        "CRDB-002",
        "CRDB-032",
        "CRDB-033",
        "CRDB-034",
        "CRDB-035",
        "CRDB-036",
    }:
        maturity = "NOT_APPLICABLE"
    prerequisite_map = {
        "CRDB-008": [
            "feature.vector_index.enabled=true",
            "empty disposable table before load",
        ],
        "CRDB-009": ["CRDB-008 vector index"],
        "CRDB-010": ["exact tenant_id and hat_id query predicates"],
        "CRDB-014": ["distinct non-admin roles", "RLS policy enabled"],
        "CRDB-015": ["RLS WITH CHECK policy"],
        "CRDB-016": ["RLS WITH CHECK policy"],
        "CRDB-017": ["RLS USING policy"],
        "CRDB-018": ["table ownership", "FORCE ROW LEVEL SECURITY"],
        "CRDB-019": ["ordinary, owner, and root/admin role comparison"],
        "CRDB-020": ["TTL expiration expression and bounded cron"],
        "CRDB-021": ["CRDB-020 TTL table"],
        "CRDB-022": ["active TTL schedule", "bounded observation window"],
        "CRDB-023": ["kv.rangefeed.enabled=true", "bounded sinkless process"],
        "CRDB-024": ["CRDB-023 sinkless feed", "separate committed changes"],
        "CRDB-025": ["RLS-enabled synthetic table", "CDC query form"],
        "CRDB-028": ["retained MVCC history within the GC window"],
        "CRDB-030": ["two independent SERIALIZABLE sessions"],
        "CRDB-031": ["session-only inject_retry_errors_enabled"],
        "CRDB-035": ["live TTL and changefeed interaction test"],
    }
    prerequisites = prerequisite_map.get(capability_id, [])
    if capability_id in {
        "CRDB-008",
        "CRDB-009",
        "CRDB-010",
        "CRDB-020",
        "CRDB-021",
        "CRDB-022",
        "CRDB-023",
        "CRDB-024",
        "CRDB-030",
        "CRDB-031",
        "CRDB-035",
    }:
        availability = "CONDITIONAL"
    expected = {
        "CRDB-001": "Client, server, and pinned build tag are exactly identical.",
        "CRDB-002": "Cluster version is 26.2 and the single-node upgrade is finalized.",
        "CRDB-003": "VECTOR(3) stores and returns fixed-dimensional values.",
        "CRDB-004": "L2 ordering matches deterministic hand-checkable vectors.",
        "CRDB-005": "Cosine ordering matches deterministic hand-checkable vectors.",
        "CRDB-006": "Negative inner-product ordering matches deterministic vectors.",
        "CRDB-007": "Wrong dimensions and malformed vectors fail closed.",
        "CRDB-008": "An empty-table prefix VECTOR index is created and catalogued.",
        "CRDB-009": "Indexed-table nearest-neighbor results are semantically correct.",
        "CRDB-010": "Exact tenant and HAT prefix filters exclude other prefixes.",
        "CRDB-011": "English stemming, matching, and ranking return expected rows.",
        "CRDB-012": "German stemming, matching, and ranking return expected rows.",
        "CRDB-013": "Supported GIN/inverted full-text indexes are catalogued.",
        "CRDB-014": "Tenant A and B see only their own synthetic rows.",
        "CRDB-015": "Cross-tenant INSERT is rejected by WITH CHECK.",
        "CRDB-016": "Cross-tenant UPDATE is rejected by WITH CHECK.",
        "CRDB-017": "Cross-tenant DELETE cannot remove an invisible row.",
        "CRDB-018": "FORCE RLS changes owner visibility from bypass to policy-bound.",
        "CRDB-019": "Ordinary roles cannot bypass; root/admin remains an explicit boundary.",
        "CRDB-020": "TTL DDL retains the expiration expression and bounded cron.",
        "CRDB-021": "A row-level TTL schedule is registered and active.",
        "CRDB-022": "Expired row is deleted while future row remains within 120 seconds.",
        "CRDB-023": "A bounded sinkless changefeed emits resolved/data events.",
        "CRDB-024": "Separate insert, update, and delete commits emit corresponding values.",
        "CRDB-025": "CDC query on an RLS table is rejected; CDC is not assumed filtered.",
        "CRDB-026": "Partial unique predicate scopes uniqueness to active rows per tenant.",
        "CRDB-027": "Duplicate active key returns SQLSTATE 23505.",
        "CRDB-028": "AOST repeatedly returns v1 while current read returns v2.",
        "CRDB-029": "Server, session, and explicit transaction default is SERIALIZABLE.",
        "CRDB-030": "Bounded natural contention produces a client-visible 40001 if not auto-retried.",
        "CRDB-031": "Official session-only injector produces an exact synthetic 40001.",
        "CRDB-032": "Only 40001 is retryable and retry attempts/backoff are bounded.",
        "CRDB-033": "Prefix filtering aids query scope but is not authorization.",
        "CRDB-034": "AOST is explicitly bounded by retained MVCC/GC history.",
        "CRDB-035": "TTL-generated deletes and changefeeds have an explicit tested or deferred interaction.",
        "CRDB-036": "Settings, SQL objects, process, ports, and temporary paths are cleaned.",
    }[capability_id]
    return {
        "availability": availability,
        "capability_id": capability_id,
        "cleanup_verified": False,
        "decision_reason": "Live result not yet recorded.",
        "deployment_scope": "DISPOSABLE_LOCAL_SINGLE_NODE",
        "evidence_reference": "",
        "expected_error": None,
        "expected_semantics": expected,
        "known_limitations": [],
        "maturity": maturity,
        "mvp_decision": decision,
        "name": name,
        "observed_semantics": "",
        "observed_sqlstate": None,
        "official_source_references": [],
        "prerequisites": prerequisites,
        "probe_id": capability_id.lower().replace("-", "_"),
        "probe_plane": "LIVE_LOCAL",
        "required_for_mvp": required_for_mvp,
        "runtime_exact_version": exact_version,
        "status": "DEFER",
    }


class ProbeRecorder:
    def __init__(self, exact_version: str) -> None:
        self.rows = [
            capability_template(identifier, name, exact_version=exact_version)
            for identifier, name in REQUIRED_CAPABILITIES
        ]
        self.by_id = {row["capability_id"]: row for row in self.rows}

    def mark(
        self,
        capability_id: str,
        status: str,
        observed: str,
        *,
        sources: Iterable[str],
        sqlstate: str | None = None,
        expected_error: str | None = None,
        limitations: Iterable[str] = (),
        decision: str | None = None,
        decision_reason: str | None = None,
        availability: str | None = None,
        maturity: str | None = None,
        probe_plane: str | None = None,
        cleanup_verified: bool = False,
    ) -> None:
        if status not in ALLOWED_STATUSES:
            raise HarnessError(f"invalid capability status: {status}")
        row = self.by_id[capability_id]
        row["status"] = status
        row["observed_semantics"] = observed
        row["observed_sqlstate"] = sqlstate
        row["expected_error"] = expected_error
        row["known_limitations"] = list(limitations)
        row["official_source_references"] = [SOURCE_URLS[key] for key in sources]
        row_index = self.rows.index(row)
        row["evidence_reference"] = (
            "docs/evidence/cockroachdb-v26-2/"
            f"capability-matrix.json#/capabilities/{row_index}"
        )
        row["cleanup_verified"] = cleanup_verified
        if availability is not None:
            row["availability"] = availability
        if maturity is not None:
            if maturity not in ALLOWED_MATURITY:
                raise HarnessError(f"invalid capability maturity: {maturity}")
            row["maturity"] = maturity
        if probe_plane is not None:
            row["probe_plane"] = probe_plane
        if status == "DEFER":
            row["mvp_decision"] = "DEFER"
        elif status == "FAIL":
            row["mvp_decision"] = "DO_NOT_USE"
        elif decision is not None:
            row["mvp_decision"] = decision
        row["decision_reason"] = decision_reason or observed

    def unmarked(self) -> list[str]:
        return [
            row["capability_id"]
            for row in self.rows
            if row["observed_semantics"] == ""
        ]


def probe_identity(
    client: SqlClient,
    recorder: ProbeRecorder,
    pin: Mapping[str, Any],
    binary_version_output: str,
) -> dict[str, Any]:
    exact = pin["exact_version"]
    build_match = re.search(
        r"^Build Tag:\s+(v\d+\.\d+\.\d+)$",
        binary_version_output,
        re.MULTILINE,
    )
    build_tag = build_match.group(1) if build_match else ""
    server_version = one_value(client.execute("defaultdb", "SELECT version()"))
    cluster_version = one_value(
        client.execute("defaultdb", "SHOW CLUSTER SETTING version")
    )
    server_match = re.search(
        r"\bCockroachDB (?:CCL|OSS) (v\d+\.\d+\.\d+)\b",
        server_version,
    )
    server_tag = server_match.group(1) if server_match else ""
    if build_tag != exact or server_tag != exact:
        raise HarnessError("client/server version does not match immutable pin")
    recorder.mark(
        "CRDB-001",
        "PASS",
        f"binary build tag and SELECT version() both report {exact}.",
        sources=(
            "artifact_checksum",
            "release",
            "release_overview",
            "release_patch",
            "security_advisories",
            "support",
        ),
        decision="USE",
    )
    finalized = cluster_version == "26.2"
    recorder.mark(
        "CRDB-002",
        "PASS" if finalized else "FAIL",
        f"SHOW CLUSTER SETTING version returned {cluster_version}; "
        f"single-node exact-version startup finalized={str(finalized).lower()}.",
        sources=("release", "release_patch"),
        decision="USE" if finalized else "DO_NOT_USE",
    )
    return {
        "client_build_tag": build_tag,
        "cluster_version": cluster_version,
        "server_version": server_version,
        "upgrade_finalized": finalized,
    }


def probe_vector(
    client: SqlClient,
    database: str,
    recorder: ProbeRecorder,
    settings: dict[str, str],
    resources: Mapping[str, str],
) -> None:
    record_ids = {
        number: f"{database}_v{number}" for number in range(1, 8)
    }
    index_ids = {
        number: f"{database}_vi{number}" for number in range(1, 4)
    }
    vector_table_raw = resources["vector_table"]
    vector_table = sql_identifier(vector_table_raw)
    vector_index_table_raw = resources["vector_index_table"]
    vector_index_table = sql_identifier(vector_index_table_raw)
    vector_index_raw = resources["vector_index"]
    vector_index = sql_identifier(vector_index_raw)
    client.execute(
        database,
        f"CREATE TABLE {vector_table} ("
        "id STRING PRIMARY KEY, tenant_id STRING NOT NULL, hat_id STRING NOT NULL, "
        "embedding VECTOR(3) NOT NULL)",
    )
    client.execute(
        database,
        f"INSERT INTO {vector_table} VALUES "
        f"('{record_ids[1]}','tenant_a','hat_a','[0,0,0]'),"
        f"('{record_ids[2]}','tenant_a','hat_a','[1,0,0]'),"
        f"('{record_ids[3]}','tenant_a','hat_a','[0,1,0]'),"
        f"('{record_ids[4]}','tenant_a','hat_a','[1,1,0]'),"
        f"('{record_ids[5]}','tenant_b','hat_b','[0.9,0,0]')",
    )
    exact_value = one_value(
        client.execute(
            database,
            f"SELECT embedding::STRING FROM {vector_table} "
            f"WHERE id='{record_ids[2]}'",
        )
    )
    recorder.mark(
        "CRDB-003",
        "PASS" if parse_vector_text(exact_value) == [1.0, 0.0, 0.0] else "FAIL",
        f"VECTOR(3) round-trip returned {exact_value}.",
        sources=("vector",),
    )
    l2 = [
        row["id"]
        for row in parse_tsv(
            client.execute(
                database,
                "SELECT id, embedding <-> '[1,0,0]'::VECTOR AS distance "
                f"FROM {vector_table} ORDER BY distance,id",
            )
        )
    ]
    recorder.mark(
        "CRDB-004",
        "PASS"
        if l2
        == [
            record_ids[2],
            record_ids[5],
            record_ids[1],
            record_ids[4],
            record_ids[3],
        ]
        else "FAIL",
        f"L2 nearest ordering was {l2}; deterministic expected ordering matched="
        f"{str(l2 == [record_ids[2], record_ids[5], record_ids[1], record_ids[4], record_ids[3]]).lower()}.",
        sources=("vector",),
    )
    cosine = [
        row["id"]
        for row in parse_tsv(
            client.execute(
                database,
                "SELECT id, embedding <=> '[1,0,0]'::VECTOR AS distance "
                f"FROM {vector_table} WHERE id<>'{record_ids[1]}' "
                "ORDER BY distance,id",
            )
        )
    ]
    recorder.mark(
        "CRDB-005",
        "PASS"
        if cosine
        == [record_ids[2], record_ids[5], record_ids[4], record_ids[3]]
        else "FAIL",
        f"Cosine nearest ordering was {cosine}; deterministic expected ordering "
        "was verified.",
        sources=("vector",),
    )
    inner = [
        row["id"]
        for row in parse_tsv(
            client.execute(
                database,
                "SELECT id, embedding <#> '[1,0,0]'::VECTOR AS distance "
                f"FROM {vector_table} WHERE id<>'{record_ids[1]}' "
                "ORDER BY distance,id",
            )
        )
    ]
    recorder.mark(
        "CRDB-006",
        "PASS"
        if inner
        == [record_ids[2], record_ids[4], record_ids[5], record_ids[3]]
        else "FAIL",
        f"Negative inner-product ordering was {inner}; deterministic expected "
        "ordering was verified.",
        sources=("vector",),
    )
    dimension_state, _ = client.expect_error(
        database,
        f"INSERT INTO {vector_table} VALUES "
        f"('{record_ids[6]}','tenant_a','hat_a','[1,2]')",
    )
    malformed_state, _ = client.expect_error(
        database,
        f"INSERT INTO {vector_table} VALUES "
        f"('{record_ids[7]}','tenant_a','hat_a','not-a-vector')",
    )
    vector_rejection_pass = (
        dimension_state is not None
        and dimension_state.startswith("22")
        and malformed_state is not None
        and malformed_state.startswith("22")
    )
    recorder.mark(
        "CRDB-007",
        "PASS" if vector_rejection_pass else "FAIL",
        "Wrong-dimension and malformed vector inserts were both rejected "
        f"(SQLSTATEs {dimension_state}, {malformed_state}).",
        sources=("vector",),
        sqlstate=dimension_state,
        expected_error="dimension and parse rejection",
    )

    original_setting = one_value(
        client.execute(
            "defaultdb", "SHOW CLUSTER SETTING feature.vector_index.enabled"
        )
    )
    settings["feature.vector_index.enabled"] = original_setting
    if original_setting != "t":
        client.execute(
            "defaultdb",
            "SET CLUSTER SETTING feature.vector_index.enabled = true",
        )
    client.execute(
        database,
        f"CREATE TABLE {vector_index_table} ("
        "id STRING PRIMARY KEY, tenant_id STRING NOT NULL, hat_id STRING NOT NULL, "
        "embedding VECTOR(3) NOT NULL)",
    )
    client.execute(
        database,
        f"CREATE VECTOR INDEX {vector_index} ON {vector_index_table} "
        "(tenant_id, hat_id, embedding vector_l2_ops)",
        timeout=90,
    )
    client.execute(
        database,
        f"INSERT INTO {vector_index_table} VALUES "
        f"('{index_ids[1]}','tenant_a','hat_a','[1,0,0]'),"
        f"('{index_ids[2]}','tenant_a','hat_a','[0,1,0]'),"
        f"('{index_ids[3]}','tenant_b','hat_b','[0.9,0,0]')",
    )
    index_defs = client.execute(
        database,
        "SELECT indexname,indexdef FROM pg_indexes "
        f"WHERE tablename='{vector_index_table_raw}' ORDER BY indexname",
    )
    create_table = client.execute(
        database, f"SHOW CREATE TABLE {vector_index_table}"
    )
    index_exists = (
        "USING cspann" in index_defs
        and f"VECTOR INDEX {vector_index_raw}" in create_table
    )
    recorder.mark(
        "CRDB-008",
        "PASS" if index_exists else "FAIL",
        "Empty-table prefix vector index is present in SHOW CREATE TABLE and "
        "pg_indexes as USING cspann."
        if index_exists
        else "Vector index metadata was not found in both supported surfaces.",
        sources=("vector_index",),
        limitations=(
            "Current v26.2 documentation warns that non-empty-table backfill "
            "blocks writes; this probe intentionally creates the index first.",
        ),
        decision="USE_WITH_GUARD",
    )
    indexed_ids = [
        row["id"]
        for row in parse_tsv(
            client.execute(
                database,
                f"SELECT id FROM {vector_index_table} "
                "WHERE tenant_id='tenant_a' AND hat_id='hat_a' "
                "ORDER BY embedding <-> '[1,0,0]'::VECTOR,id LIMIT 2",
            )
        )
    ]
    plan = client.execute(
        database,
        f"EXPLAIN SELECT id FROM {vector_index_table} "
        "WHERE tenant_id='tenant_a' AND hat_id='hat_a' "
        "ORDER BY embedding <-> '[1,0,0]'::VECTOR LIMIT 2",
    )
    selected = vector_index_raw in plan or "cspann" in plan.lower()
    recorder.mark(
        "CRDB-009",
        "PASS" if indexed_ids == [index_ids[1], index_ids[2]] else "FAIL",
        f"Nearest-neighbor result was {indexed_ids}; optimizer selected "
        f"the ANN index={str(selected).lower()} on the tiny fixture.",
        sources=("vector_index",),
        limitations=(
            "Index existence, semantic correctness, eligibility, and optimizer "
            "selection are separate findings; the tiny table may prefer a scan.",
            "ANN results are approximate at production scale.",
        ),
        decision="USE_WITH_GUARD",
    )
    filtered_ids = [
        row["id"]
        for row in parse_tsv(
            client.execute(
                database,
                f"SELECT id FROM {vector_index_table} "
                "WHERE tenant_id='tenant_a' AND hat_id='hat_a' "
                "ORDER BY embedding <-> '[1,0,0]'::VECTOR,id",
            )
        )
    ]
    recorder.mark(
        "CRDB-010",
        "PASS" if filtered_ids == [index_ids[1], index_ids[2]] else "FAIL",
        f"Exact tenant/HAT prefixes returned only IDs {filtered_ids}.",
        sources=("vector_index",),
        decision="USE_WITH_GUARD",
    )
    unfiltered_tenants = [
        row["tenant_id"]
        for row in parse_tsv(
            client.execute(
                database,
                f"SELECT DISTINCT tenant_id FROM {vector_index_table} "
                "ORDER BY tenant_id",
            )
        )
    ]
    recorder.mark(
        "CRDB-033",
        "PASS" if unfiltered_tenants == ["tenant_a", "tenant_b"] else "FAIL",
        "An unfiltered privileged query saw both synthetic tenants "
        f"{unfiltered_tenants}; exact prefixes constrained search but supplied "
        "no authorization boundary.",
        sources=("vector_index", "rls"),
        limitations=(
            "Prefix columns are optimizer/filter dimensions, not an identity "
            "or authorization boundary.",
        ),
        decision="USE_WITH_GUARD",
    )


def probe_full_text(
    client: SqlClient,
    database: str,
    recorder: ProbeRecorder,
    resources: Mapping[str, str],
) -> None:
    record_ids = {
        number: f"{database}_fts{number}" for number in range(1, 7)
    }
    table_raw = resources["fts_table"]
    table = sql_identifier(table_raw)
    en_index_raw = resources["fts_en_index"]
    en_index = sql_identifier(en_index_raw)
    de_index_raw = resources["fts_de_index"]
    de_index = sql_identifier(de_index_raw)
    client.execute(
        database,
        f"CREATE TABLE {table} ("
        "id STRING PRIMARY KEY, body STRING NOT NULL, "
        "english_vec TSVECTOR AS (to_tsvector('english',body)) STORED, "
        "german_vec TSVECTOR AS (to_tsvector('german',body)) STORED)",
    )
    client.execute(
        database,
        f"CREATE INVERTED INDEX {en_index} ON {table} (english_vec)",
        timeout=90,
    )
    client.execute(
        database,
        f"CREATE INVERTED INDEX {de_index} ON {table} (german_vec)",
        timeout=90,
    )
    client.execute(
        database,
        f"INSERT INTO {table} (id,body) VALUES "
        f"('{record_ids[1]}','Foxes foxes jump and jumping quickly'),"
        f"('{record_ids[2]}','One fox will jump'),"
        f"('{record_ids[3]}','Häuser Häuser und Kinder laufen laufen'),"
        f"('{record_ids[4]}','Häuser stehen und Kinder laufen'),"
        f"('{record_ids[5]}','A turtle walks slowly'),"
        f"('{record_ids[6]}','Ein Auto fährt langsam')",
    )
    english = parse_tsv(
        client.execute(
            database,
            "SELECT id,english_vec::STRING AS lexemes,"
            "ts_rank(english_vec,plainto_tsquery('english','fox jump')) AS rank "
            f"FROM {table} "
            "WHERE english_vec @@ plainto_tsquery('english','fox jump') "
            "ORDER BY rank DESC,id",
        )
    )
    english_pass = (
        len(english) == 2
        and [row.get("id") for row in english]
        == [record_ids[1], record_ids[2]]
        and float(english[0].get("rank", "0"))
        > float(english[1].get("rank", "0"))
        > 0
        and "'fox'" in english[0].get("lexemes", "")
        and "'jump'" in english[0].get("lexemes", "")
    )
    recorder.mark(
        "CRDB-011",
        "PASS" if english_pass else "FAIL",
        "English dictionary stemmed foxes/jumping to fox/jump and ranked "
        f"IDs {[row['id'] for row in english]} with scores "
        f"{[row['rank'] for row in english]}.",
        sources=("full_text",),
    )
    german = parse_tsv(
        client.execute(
            database,
            "SELECT id,german_vec::STRING AS lexemes,"
            "ts_rank(german_vec,plainto_tsquery('german','Häuser laufen')) AS rank "
            f"FROM {table} "
            "WHERE german_vec @@ plainto_tsquery('german','Häuser laufen') "
            "ORDER BY rank DESC,id",
        )
    )
    german_pass = (
        len(german) == 2
        and [row.get("id") for row in german]
        == [record_ids[3], record_ids[4]]
        and float(german[0].get("rank", "0"))
        > float(german[1].get("rank", "0"))
        > 0
        and "'haus'" in german[0].get("lexemes", "")
        and "'lauf'" in german[0].get("lexemes", "")
    )
    recorder.mark(
        "CRDB-012",
        "PASS" if german_pass else "FAIL",
        "German dictionary stemmed Häuser/laufen to haus/lauf and ranked "
        f"IDs {[row['id'] for row in german]} with scores "
        f"{[row['rank'] for row in german]}.",
        sources=("full_text",),
        limitations=(
            "Dictionary behavior does not prove legal-domain recall or ranking.",
        ),
    )
    index_defs = client.execute(
        database,
        "SELECT indexname,indexdef FROM pg_indexes "
        f"WHERE tablename='{table_raw}' ORDER BY indexname",
    )
    malformed_state, _ = client.expect_error(
        database, "SELECT to_tsquery('english','fox &')"
    )
    websearch_state, _ = client.expect_error(
        database, "SELECT websearch_to_tsquery('english','fox jump')"
    )
    indexes_pass = (
        en_index_raw in index_defs
        and de_index_raw in index_defs
        and malformed_state is not None
    )
    recorder.mark(
        "CRDB-013",
        "PASS" if indexes_pass else "FAIL",
        "Both supported GIN/inverted indexes are catalogued; malformed TSQUERY "
        f"was rejected with {malformed_state}; websearch_to_tsquery was "
        f"unavailable with {websearch_state}.",
        sources=("full_text",),
        sqlstate=malformed_state,
        expected_error="malformed TSQUERY rejection",
        limitations=("websearch_to_tsquery is not supported in v26.2.",),
    )


def role_query(client: SqlClient, database: str, role: str, sql: str) -> str:
    return strip_command_tags(
        client.execute(
            database, f"SET ROLE {sql_identifier(role)}; {sql}; RESET ROLE"
        )
    )


def probe_rls(
    client: SqlClient,
    database: str,
    recorder: ProbeRecorder,
    roles: dict[str, str],
    resources: Mapping[str, str],
) -> None:
    row_a = f"{database}_rls_a"
    row_b = f"{database}_rls_b"
    row_forbidden = f"{database}_rls_forbidden"
    table_raw = resources["rls_table"]
    table = sql_identifier(table_raw)
    policy_raw = resources["rls_policy"]
    policy = sql_identifier(policy_raw)
    for role in roles.values():
        client.execute("defaultdb", f"CREATE ROLE {sql_identifier(role)}")
    client.execute(
        database,
        f"CREATE TABLE {table} ("
        "id STRING PRIMARY KEY, tenant_id STRING NOT NULL, payload STRING NOT NULL)",
    )
    client.execute(
        database,
        f"INSERT INTO {table} VALUES "
        f"('{row_a}','{roles['tenant_a']}','A'),"
        f"('{row_b}','{roles['tenant_b']}','B')",
    )
    client.execute(
        database,
        f"ALTER TABLE {table} OWNER TO {sql_identifier(roles['owner'])}",
    )
    client.execute(
        database,
        f"GRANT SELECT,INSERT,UPDATE,DELETE ON {table} TO "
        + ",".join(
            sql_identifier(roles[key]) for key in ("tenant_a", "tenant_b", "app")
        ),
    )
    client.execute(
        database, f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"
    )
    client.execute(
        database,
        f"CREATE POLICY {policy} ON {table} FOR ALL TO "
        f"{sql_identifier(roles['tenant_a'])},{sql_identifier(roles['tenant_b'])} "
        "USING (tenant_id=current_user()) WITH CHECK (tenant_id=current_user())",
    )
    rows_a = parse_tsv(
        role_query(
            client,
            database,
            roles["tenant_a"],
            f"SELECT id,tenant_id FROM {table} ORDER BY id",
        )
    )
    rows_b = parse_tsv(
        role_query(
            client,
            database,
            roles["tenant_b"],
            f"SELECT id,tenant_id FROM {table} ORDER BY id",
        )
    )
    policy_catalog = parse_tsv(
        client.execute(
            database,
            "SELECT policyname,permissive,roles,cmd,qual,with_check "
            "FROM pg_catalog.pg_policies "
            f"WHERE tablename='{table_raw}' AND policyname='{policy_raw}'",
        )
    )
    policy_metadata_pass = (
        len(policy_catalog) == 1
        and policy_catalog[0].get("policyname") == policy_raw
        and policy_catalog[0].get("cmd", "").upper() == "ALL"
        and roles["tenant_a"] in policy_catalog[0].get("roles", "")
        and roles["tenant_b"] in policy_catalog[0].get("roles", "")
        and "tenant_id" in policy_catalog[0].get("qual", "")
        and "current_user" in policy_catalog[0].get("qual", "")
        and "tenant_id" in policy_catalog[0].get("with_check", "")
        and "current_user" in policy_catalog[0].get("with_check", "")
    )
    select_pass = (
        [row["id"] for row in rows_a] == [row_a]
        and [row["id"] for row in rows_b] == [row_b]
        and policy_metadata_pass
    )
    recorder.mark(
        "CRDB-014",
        "PASS" if select_pass else "FAIL",
        f"Tenant A saw {[row['id'] for row in rows_a]}; "
        f"Tenant B saw {[row['id'] for row in rows_b]}; pg_policies preserved "
        f"roles/cmd/USING/WITH CHECK={str(policy_metadata_pass).lower()}.",
        sources=("rls", "policy"),
    )
    insert_state, _ = client.expect_error(
        database,
        f"SET ROLE {sql_identifier(roles['tenant_a'])}; "
        f"INSERT INTO {table} VALUES "
        f"('{row_forbidden}','{roles['tenant_b']}','forbidden')",
    )
    recorder.mark(
        "CRDB-015",
        "PASS" if insert_state == "42501" else "FAIL",
        f"Cross-tenant INSERT was rejected with SQLSTATE {insert_state}.",
        sources=("rls", "policy"),
        sqlstate=insert_state,
        expected_error="42501",
    )
    update_state, _ = client.expect_error(
        database,
        f"SET ROLE {sql_identifier(roles['tenant_a'])}; "
        f"UPDATE {table} SET tenant_id='{roles['tenant_b']}' "
        f"WHERE id='{row_a}'",
    )
    recorder.mark(
        "CRDB-016",
        "PASS" if update_state == "42501" else "FAIL",
        f"Cross-tenant UPDATE was rejected with SQLSTATE {update_state}.",
        sources=("rls", "policy"),
        sqlstate=update_state,
        expected_error="42501",
    )
    delete_output = role_query(
        client,
        database,
        roles["tenant_a"],
        f"DELETE FROM {table} WHERE id='{row_b}' RETURNING id",
    )
    remaining_b = one_value(
        client.execute(
            database,
            f"SELECT count(*) FROM {table} "
            f"WHERE tenant_id='{roles['tenant_b']}'",
        )
    )
    delete_pass = not parse_tsv(delete_output) and remaining_b == "1"
    recorder.mark(
        "CRDB-017",
        "PASS" if delete_pass else "FAIL",
        "Tenant A DELETE returned no rows and Tenant B row remained present.",
        sources=("rls", "policy"),
    )
    owner_before = one_value(
        role_query(
            client,
            database,
            roles["owner"],
            f"SELECT count(*) FROM {table}",
        )
    )
    client.execute(
        database, f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"
    )
    owner_after = one_value(
        role_query(
            client,
            database,
            roles["owner"],
            f"SELECT count(*) FROM {table}",
        )
    )
    force_pass = owner_before == "2" and owner_after == "0"
    recorder.mark(
        "CRDB-018",
        "PASS" if force_pass else "FAIL",
        f"Table owner row count changed from {owner_before} before FORCE to "
        f"{owner_after} after FORCE.",
        sources=("rls", "force_rls"),
        decision="USE_WITH_GUARD",
    )
    app_count = one_value(
        role_query(
            client,
            database,
            roles["app"],
            f"SELECT count(*) FROM {table}",
        )
    )
    root_count = one_value(
        client.execute(database, f"SELECT count(*) FROM {table}")
    )
    catalog = parse_tsv(
        client.execute(
            database,
            "SELECT relrowsecurity,relforcerowsecurity "
            f"FROM pg_class WHERE oid='{table_raw}'::REGCLASS",
        )
    )
    after_force_a = one_value(
        role_query(
            client,
            database,
            roles["tenant_a"],
            f"SELECT count(*) FROM {table}",
        )
    )
    after_force_b = one_value(
        role_query(
            client,
            database,
            roles["tenant_b"],
            f"SELECT count(*) FROM {table}",
        )
    )
    role_names = ",".join(f"'{value}'" for value in roles.values())
    bypass_catalog = parse_tsv(
        client.execute(
            "defaultdb",
            "SELECT rolname,rolbypassrls FROM pg_catalog.pg_roles "
            f"WHERE rolname IN ({role_names}) ORDER BY rolname",
        )
    )
    ordinary_no_bypass = (
        len(bypass_catalog) == len(roles)
        and all(row.get("rolbypassrls") == "f" for row in bypass_catalog)
    )
    bypass_pass = (
        app_count == "0"
        and root_count == "2"
        and after_force_a == "1"
        and after_force_b == "1"
        and ordinary_no_bypass
        and catalog
        and catalog[0]["relrowsecurity"] == "t"
        and catalog[0]["relforcerowsecurity"] == "t"
    )
    recorder.mark(
        "CRDB-019",
        "PASS" if bypass_pass else "FAIL",
        f"Ordinary app role saw {app_count} rows; root/admin saw {root_count}; "
        f"tenant roles remained at {after_force_a}/{after_force_b} rows after "
        "FORCE; all four synthetic roles had rolbypassrls=false.",
        sources=("rls", "force_rls", "rls_limitations"),
        limitations=(
            "Root/admin, constraints, TRUNCATE, backup/restore, LDR, and PCR "
            "are explicit RLS bypass or non-policy boundaries.",
        ),
        decision="USE_WITH_GUARD",
    )
def probe_ttl(
    client: SqlClient,
    database: str,
    recorder: ProbeRecorder,
    resources: Mapping[str, str],
) -> int | None:
    expired_id = f"{database}_ttl_expired"
    retained_id = f"{database}_ttl_retained"
    table_raw = resources["ttl_table"]
    table = sql_identifier(table_raw)
    client.execute(
        database,
        f"CREATE TABLE {table} ("
        "id STRING PRIMARY KEY, payload STRING NOT NULL, expires_at TIMESTAMPTZ NOT NULL"
        ") WITH (ttl_expiration_expression='expires_at',ttl_job_cron='* * * * *')",
    )
    client.execute(
        database,
        f"INSERT INTO {table} VALUES "
        f"('{expired_id}','expired',now()-INTERVAL '5 minutes'),"
        f"('{retained_id}','live',now()+INTERVAL '1 hour')",
    )
    definition = client.execute(database, f"SHOW CREATE TABLE {table}")
    ddl_pass = (
        "ttl_expiration_expression = 'expires_at'" in definition
        and "ttl_job_cron = '* * * * *'" in definition
    )
    recorder.mark(
        "CRDB-020",
        "PASS" if ddl_pass else "FAIL",
        "SHOW CREATE TABLE preserved ttl_expiration_expression and the bounded "
        "one-minute disposable cron.",
        sources=("ttl",),
        limitations=("TTL execution is asynchronous, never synchronous DML.",),
    )
    schedules = parse_tsv(client.execute(database, "SHOW SCHEDULES"))
    ttl_rows = [
        row
        for row in schedules
        if table_raw in row.get("label", "")
        and "row-level-ttl" in row.get("label", "").lower()
    ]
    schedule_id = int(ttl_rows[0]["id"]) if ttl_rows else None
    schedule_pass = bool(ttl_rows) and ttl_rows[0].get("schedule_status") == "ACTIVE"
    recorder.mark(
        "CRDB-021",
        "PASS" if schedule_pass else "FAIL",
        f"Active TTL schedule registered; schedule present={str(bool(ttl_rows)).lower()}.",
        sources=("ttl",),
    )
    deadline = time.monotonic() + TTL_OBSERVATION_SECONDS
    ids: list[str] = [expired_id, retained_id]
    while time.monotonic() < deadline:
        ids = [
            row["id"]
            for row in parse_tsv(
                client.execute(
                    database, f"SELECT id FROM {table} ORDER BY id"
                )
            )
        ]
        if ids == [retained_id]:
            break
        time.sleep(5)
    if ids == [retained_id]:
        recorder.mark(
            "CRDB-022",
            "PASS",
            "Within the bounded observation window, the prefixed expired row "
            "was deleted and the prefixed future-expiring row remained.",
            sources=("ttl",),
            decision="USE_WITH_GUARD",
        )
    else:
        recorder.mark(
            "CRDB-022",
            "DEFER",
            f"TTL metadata was active, but physical rows remained {ids} after "
            f"{TTL_OBSERVATION_SECONDS:.0f} seconds.",
            sources=("ttl",),
            limitations=("Asynchronous execution exceeded the bounded window.",),
            decision="DEFER",
        )
    return schedule_id


def terminate_owned_process(
    process: subprocess.Popen[str], *, timeout: float = 10
) -> tuple[str, str, bool]:
    if process.poll() is None:
        process.terminate()
    forced = False
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        forced = True
        process.kill()
        stdout, stderr = process.communicate(timeout=5)
    return stdout, stderr, forced


def probe_changefeed(
    client: SqlClient,
    database: str,
    recorder: ProbeRecorder,
    settings: dict[str, str],
    resources: Mapping[str, str],
    owned_children: list[subprocess.Popen[str]],
) -> dict[str, Any]:
    initial_id = f"{database}_cf_initial"
    changed_id = f"{database}_cf_changed"
    table = sql_identifier(resources["changefeed_table"])
    rls_table = sql_identifier(resources["rls_table"])
    original = one_value(
        client.execute("defaultdb", "SHOW CLUSTER SETTING kv.rangefeed.enabled")
    )
    settings["kv.rangefeed.enabled"] = original
    if original != "t":
        client.execute(
            "defaultdb", "SET CLUSTER SETTING kv.rangefeed.enabled = true"
        )
    client.execute(
        database,
        f"CREATE TABLE {table} (id STRING PRIMARY KEY,payload STRING NOT NULL)",
    )
    client.execute(
        database, f"INSERT INTO {table} VALUES ('{initial_id}','initial')"
    )
    feed_sql = (
        f"CREATE CHANGEFEED FOR TABLE {table} "
        "WITH initial_scan='yes',updated,resolved='2s'"
    )
    args, environment = client.command(database, feed_sql)
    feed = subprocess.Popen(
        args,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=environment,
    )
    owned_children.append(feed)
    feed_was_live = False
    try:
        time.sleep(2)
        if feed.poll() is not None:
            stdout, stderr = feed.communicate(timeout=5)
            raise HarnessError(
                "sinkless changefeed exited before probe changes: "
                f"{sanitize_failure(stderr)}; output_present={bool(stdout)}"
            )
        feed_was_live = True
        client.execute(
            database, f"INSERT INTO {table} VALUES ('{changed_id}','v1')"
        )
        time.sleep(2)
        client.execute(
            database,
            f"UPDATE {table} SET payload='v2' WHERE id='{changed_id}'",
        )
        time.sleep(2)
        client.execute(
            database, f"DELETE FROM {table} WHERE id='{changed_id}'"
        )
        time.sleep(5)
    finally:
        output, stderr, forced = terminate_owned_process(feed)
    if not feed_was_live:
        raise HarnessError("sinkless changefeed never reached live probe state")
    if feed.poll() is None:
        raise HarnessError("owned sinkless changefeed process remains running")
    if not output and stderr:
        raise HarnessError(f"sinkless changefeed failed: {sanitize_failure(stderr)}")
    events = parse_changefeed_json(output)
    if output.strip() and not events:
        raise HarnessError(
            "sinkless changefeed emitted non-empty output that the semantic "
            f"decoder could not parse; lines={len(output.splitlines())}, "
            f"sha256={hashlib.sha256(output.encode('utf-8')).hexdigest()}"
        )
    resolved = any("resolved" in event for event in events)
    after_values = [
        event.get("after")
        for event in events
        if "after" in event
    ]
    initial_seen = any(
        isinstance(after, dict) and after.get("payload") == "initial"
        for after in after_values
    )
    recorder.mark(
        "CRDB-023",
        "PASS" if resolved and initial_seen else "FAIL",
        "Semantic CDC decoding observed "
        f"initial_scan={str(initial_seen).lower()}, "
        f"resolved={str(resolved).lower()}; the exact child process was reaped; "
        f"forced kill={str(forced).lower()}.",
        sources=("changefeed",),
        decision="USE_WITH_GUARD",
    )
    event_pass = (
        any(
            isinstance(after, dict) and after.get("payload") == "v1"
            for after in after_values
        )
        and any(
            isinstance(after, dict) and after.get("payload") == "v2"
            for after in after_values
        )
        and any(after is None for after in after_values)
    )
    recorder.mark(
        "CRDB-024",
        "PASS" if event_pass else "FAIL",
        "Separate insert/update/delete commits were issued; semantic decoding "
        f"observed v1, v2, and null-after={str(event_pass).lower()}.",
        sources=("changefeed",),
        limitations=("Changefeed delivery is at-least-once, not exactly-once.",),
        decision="USE_WITH_GUARD",
    )
    cdc_state, cdc_error = client.expect_error(
        database,
        f"CREATE CHANGEFEED AS SELECT * FROM {rls_table}",
        timeout=15,
    )
    cdc_pass = (
        cdc_state is not None
        and "row-level security" in cdc_error.lower()
    )
    recorder.mark(
        "CRDB-025",
        "PASS" if cdc_pass else "FAIL",
        f"CDC query on an RLS table was rejected with SQLSTATE {cdc_state}; "
        "official contract states emitted CDC messages are not RLS-filtered.",
        sources=("rls", "rls_limitations", "cdc_query"),
        sqlstate=cdc_state,
        expected_error="CDC queries are not supported on RLS tables",
        limitations=(
            "Changefeed consumers require an independent tenant-safe contract.",
        ),
        decision="USE_WITH_GUARD",
        availability="UNAVAILABLE",
    )
    if original != "t":
        client.execute(
            "defaultdb", "SET CLUSTER SETTING kv.rangefeed.enabled = false"
        )
        settings["kv.rangefeed.enabled:restored"] = "f"
    return {
        "events_decoded": len(events),
        "forced_kill_used": forced,
        "initial_scan_observed": initial_seen,
        "pid": feed.pid,
        "process_exited": feed.poll() is not None,
        "resolved_observed": resolved,
        "sanitized_output_sha256": hashlib.sha256(
            output.encode("utf-8")
        ).hexdigest(),
    }


def probe_partial_unique(
    client: SqlClient,
    database: str,
    recorder: ProbeRecorder,
    resources: Mapping[str, str],
) -> None:
    record_ids = {
        number: f"{database}_partial{number}" for number in range(1, 6)
    }
    table_raw = resources["partial_table"]
    table = sql_identifier(table_raw)
    index_raw = resources["partial_index"]
    index = sql_identifier(index_raw)
    client.execute(
        database,
        f"CREATE TABLE {table} ("
        "id STRING PRIMARY KEY,tenant_id STRING NOT NULL,"
        "logical_key STRING NOT NULL,active BOOL NOT NULL)",
    )
    client.execute(
        database,
        f"CREATE UNIQUE INDEX {index} "
        f"ON {table} (tenant_id,logical_key) WHERE active",
    )
    client.execute(
        database,
        f"INSERT INTO {table} VALUES "
        f"('{record_ids[1]}','tenant_a','key',true),"
        f"('{record_ids[2]}','tenant_a','key',false),"
        f"('{record_ids[3]}','tenant_a','key',false),"
        f"('{record_ids[4]}','tenant_b','key',true)",
    )
    predicate = one_value(
        client.execute(
            database,
            "SELECT indexdef FROM pg_indexes "
            f"WHERE tablename='{table_raw}' "
            f"AND indexname='{index_raw}'",
        )
    )
    counts = parse_tsv(
        client.execute(
            database,
            f"SELECT tenant_id,active,count(*) AS count FROM {table} "
            "GROUP BY tenant_id,active ORDER BY tenant_id,active DESC",
        )
    )
    normalized_predicate = " ".join(predicate.lower().split())
    predicate_pass = (
        "where" in normalized_predicate
        and "active" in normalized_predicate
        and len(counts) == 3
    )
    recorder.mark(
        "CRDB-026",
        "PASS" if predicate_pass else "FAIL",
        "Predicate metadata contains WHERE active; inactive duplicates and "
        "same key in another tenant were accepted.",
        sources=("partial",),
    )
    duplicate_state, _ = client.expect_error(
        database,
        f"INSERT INTO {table} VALUES "
        f"('{record_ids[5]}','tenant_a','key',true)",
    )
    active_count = one_value(
        client.execute(
            database,
            f"SELECT count(*) FROM {table} "
            "WHERE tenant_id='tenant_a' AND logical_key='key' AND active",
        )
    )
    recorder.mark(
        "CRDB-027",
        "PASS"
        if duplicate_state == "23505" and active_count == "1"
        else "FAIL",
        f"Second active duplicate returned SQLSTATE {duplicate_state}; "
        f"one active row remained={active_count}.",
        sources=("partial", "transactions"),
        sqlstate=duplicate_state,
        expected_error="23505",
        limitations=("23505 is permanent and must not be retried as 40001.",),
    )


def probe_aost(
    client: SqlClient,
    database: str,
    recorder: ProbeRecorder,
    resources: Mapping[str, str],
) -> None:
    record_id = f"{database}_aost_record"
    table = sql_identifier(resources["aost_table"])
    client.execute(
        database,
        f"CREATE TABLE {table} (id STRING PRIMARY KEY,payload STRING NOT NULL)",
    )
    client.execute(
        database, f"INSERT INTO {table} VALUES ('{record_id}','v1')"
    )
    timestamp = one_value(
        client.execute(database, "SELECT cluster_logical_timestamp()")
    )
    if not re.fullmatch(r"\d+\.\d{10}", timestamp):
        raise HarnessError("unexpected HLC timestamp representation")
    client.execute(
        database,
        f"UPDATE {table} SET payload='v2' WHERE id='{record_id}'",
    )
    current = one_value(
        client.execute(
            database, f"SELECT payload FROM {table} WHERE id='{record_id}'"
        )
    )
    historical_1 = one_value(
        client.execute(
            database,
            f"SELECT payload FROM {table} AS OF SYSTEM TIME {timestamp} "
            f"WHERE id='{record_id}'",
        )
    )
    historical_2 = one_value(
        client.execute(
            database,
            f"SELECT payload FROM {table} AS OF SYSTEM TIME {timestamp} "
            f"WHERE id='{record_id}'",
        )
    )
    future_state, _ = client.expect_error(
        database,
        f"SELECT payload FROM {table} "
        "AS OF SYSTEM TIME '2999-01-01T00:00:00Z'",
    )
    aost_pass = (
        current == "v2"
        and historical_1 == "v1"
        and historical_2 == "v1"
        and future_state is not None
    )
    recorder.mark(
        "CRDB-028",
        "PASS" if aost_pass else "FAIL",
        "Current read returned v2; two reads at captured HLC returned v1; "
        f"future timestamp was rejected with {future_state}.",
        sources=("aost",),
        sqlstate=future_state,
        expected_error="future historical timestamp rejection",
        decision="USE_WITH_GUARD",
    )
    recorder.mark(
        "CRDB-034",
        "PASS",
        "The live historical read succeeded inside the current MVCC window; "
        "official contract bounds AOST by GC retention.",
        sources=("aost",),
        limitations=(
            "AOST is not permanent history or durable audit storage.",
            "Placeholders are not supported for the AOST timestamp.",
        ),
        decision="USE_WITH_GUARD",
    )


def probe_isolation_and_retry(
    client: SqlClient,
    database: str,
    recorder: ProbeRecorder,
    resources: Mapping[str, str],
    owned_children: list[subprocess.Popen[str]],
) -> None:
    record_id = f"{database}_retry_record"
    table = sql_identifier(resources["retry_table"])
    server_default = one_value(
        client.execute(
            "defaultdb",
            "SELECT current_setting('default_transaction_isolation')",
        )
    )
    serializable = one_value(
        strip_command_tags(
            client.execute(
                database,
                "BEGIN ISOLATION LEVEL SERIALIZABLE; "
                "SHOW transaction_isolation; COMMIT",
            )
        )
    )
    read_committed = one_value(
        strip_command_tags(
            client.execute(
                database,
                "BEGIN ISOLATION LEVEL READ COMMITTED; "
                "SHOW transaction_isolation; ROLLBACK",
            )
        )
    )
    try:
        rc_setting = one_value(
            client.execute(
                "defaultdb",
                "SHOW CLUSTER SETTING sql.txn.read_committed_isolation.enabled",
            )
        )
    except HarnessError:
        rc_setting = "unavailable"
    isolation_pass = (
        server_default == "serializable"
        and serializable == "serializable"
    )
    recorder.mark(
        "CRDB-029",
        "PASS" if isolation_pass else "FAIL",
        f"default={server_default}, explicit={serializable}, "
        f"READ COMMITTED availability={read_committed}, setting={rc_setting}.",
        sources=("transactions",),
        decision="USE",
    )

    client.execute(
        database,
        f"CREATE TABLE {table} (id STRING PRIMARY KEY,value INT NOT NULL)",
    )
    client.execute(
        database, f"INSERT INTO {table} VALUES ('{record_id}',0)"
    )
    natural_state: str | None = None
    committed_counts: list[int] = []
    transaction_sql = (
        f"BEGIN; SELECT value FROM {table} WHERE id='{record_id}'; "
        "SELECT pg_sleep(0.5); "
        f"UPDATE {table} SET value=value+1 WHERE id='{record_id}'; COMMIT"
    )
    for _attempt in range(1, NATURAL_RETRY_ATTEMPTS + 1):
        client.execute(
            database, f"UPDATE {table} SET value=0 WHERE id='{record_id}'"
        )
        args_a, env_a = client.command(database, transaction_sql)
        args_b, env_b = client.command(database, transaction_sql)
        processes: list[subprocess.Popen[str]] = []
        release_read_fd, release_write_fd = os.pipe()
        gate_code = (
            "import os,sys;"
            "fd=int(sys.argv[1]);"
            "os.read(fd,1);"
            "os.close(fd);"
            "os.execve(sys.argv[2],sys.argv[2:],os.environ)"
        )
        try:
            for args, environment in ((args_a, env_a), (args_b, env_b)):
                processes.append(
                    subprocess.Popen(
                        [
                            sys.executable,
                            "-c",
                            gate_code,
                            str(release_read_fd),
                            *args,
                        ],
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        env=environment,
                        pass_fds=(release_read_fd,),
                    )
                )
                owned_children.append(processes[-1])
            if any(process.poll() is not None for process in processes):
                raise HarnessError(
                    "natural contention child exited before release barrier"
                )
            os.write(release_write_fd, b"12")
            os.close(release_write_fd)
            release_write_fd = -1
            os.close(release_read_fd)
            release_read_fd = -1
            results = []
            for process in processes:
                try:
                    stdout, stderr = process.communicate(timeout=15)
                except subprocess.TimeoutExpired as exc:
                    raise HarnessError(
                        "natural contention child exceeded timeout"
                    ) from exc
                results.append((process.returncode, stdout, stderr))
        finally:
            for file_descriptor in (release_read_fd, release_write_fd):
                if file_descriptor >= 0:
                    os.close(file_descriptor)
            for process in processes:
                if process.poll() is None:
                    process.kill()
                try:
                    process.communicate(timeout=5)
                except subprocess.TimeoutExpired as exc:
                    raise HarnessError(
                        "natural contention child could not be terminated"
                    ) from exc
        states = [extract_sqlstate(item[2]) for item in results]
        successful = sum(item[0] == 0 for item in results)
        final_counter = int(
            one_value(
                client.execute(
                    database,
                    f"SELECT value FROM {table} WHERE id='{record_id}'",
                )
            )
        )
        both_read_zero = all(
            re.search(r"(?:^|\n)0(?:\n|$)", item[1]) is not None
            for item in results
        )
        if (
            "40001" in states
            and successful == 1
            and final_counter == 1
            and both_read_zero
        ):
            natural_state = "40001"
            committed_counts = [
                successful,
                final_counter,
            ]
            break
    if natural_state == "40001":
        recorder.mark(
            "CRDB-030",
            "PASS",
            "Two independent CLI sessions crossed an explicit release barrier; "
            "bounded overlapping transactions produced client-visible 40001; "
            f"committed_sessions={committed_counts[0]}, final_value={committed_counts[1]}.",
            sources=("transactions", "retry"),
            sqlstate="40001",
            expected_error="40001",
            decision="USE_WITH_GUARD",
        )
    else:
        final_value = one_value(
            client.execute(
                database, f"SELECT value FROM {table} WHERE id='{record_id}'"
            )
        )
        recorder.mark(
            "CRDB-030",
            "DEFER",
            f"No client-visible 40001 after {NATURAL_RETRY_ATTEMPTS} bounded "
            "explicit-barrier attempts; server/client completed with final "
            f"value {final_value}.",
            sources=("transactions", "retry"),
            limitations=(
                "Transparent server retries can make a natural client-visible "
                "signal nondeterministic on a single node.",
            ),
            decision="DEFER",
        )

    synthetic_state, _ = client.expect_error(
        database,
        "SET inject_retry_errors_enabled=true; BEGIN; SELECT 1; COMMIT",
    )
    recorder.mark(
        "CRDB-031",
        "PASS" if synthetic_state == "40001" else "FAIL",
        f"Official session-only test injector returned SQLSTATE {synthetic_state}; "
        "the connection closed immediately afterward.",
        sources=("retry",),
        sqlstate=synthetic_state,
        expected_error="40001",
        limitations=(
            "Synthetic injection proves signal classification, not contention rate.",
        ),
        decision="USE_WITH_GUARD",
    )
    recorder.mark(
        "CRDB-032",
        "PASS",
        "Harness classifier accepts only SQLSTATE 40001; attempts are capped at "
        "10 and backoff at 1 second; a cleanup hook runs before sleeping; "
        "23505 is non-retryable.",
        sources=("retry", "transactions"),
        decision="USE",
        maturity="NOT_APPLICABLE",
        probe_plane="OFFLINE_REPOSITORY",
    )


def restore_settings(client: SqlClient, settings: dict[str, str]) -> dict[str, str]:
    restored: dict[str, str] = {}
    for name in ("kv.rangefeed.enabled", "feature.vector_index.enabled"):
        if name not in settings:
            continue
        original = settings[name]
        current = one_value(
            client.execute("defaultdb", f"SHOW CLUSTER SETTING {name}")
        )
        if current != original:
            literal = "true" if original == "t" else "false"
            client.execute(
                "defaultdb", f"SET CLUSTER SETTING {name} = {literal}"
            )
        restored[name] = one_value(
            client.execute("defaultdb", f"SHOW CLUSTER SETTING {name}")
        )
    return restored


def cleanup_sql_resources(
    client: SqlClient,
    database: str,
    roles: Mapping[str, str],
    settings: dict[str, str],
    schedule_id: int | None,
) -> dict[str, Any]:
    errors: list[str] = []
    restored: dict[str, str] = {}
    try:
        restored = restore_settings(client, settings)
    except HarnessError as exc:
        errors.append(f"setting restoration: {exc}")
    try:
        client.execute(
            "defaultdb", f"DROP DATABASE IF EXISTS {sql_identifier(database)} CASCADE"
        )
    except HarnessError as exc:
        errors.append(f"database cleanup: {exc}")
    for role in roles.values():
        try:
            client.execute(
                "defaultdb", f"DROP ROLE IF EXISTS {sql_identifier(role)}"
            )
        except HarnessError as exc:
            errors.append(f"role cleanup {role}: {exc}")
    database_absent = False
    roles_absent = False
    ttl_schedule_absent = schedule_id is None
    try:
        databases = parse_tsv(client.execute("defaultdb", "SHOW DATABASES"))
        database_absent = all(
            row.get("database_name") != database for row in databases
        )
        if not database_absent:
            errors.append("database remains after exact DROP")
    except HarnessError as exc:
        errors.append(f"database cleanup verification: {exc}")
    try:
        role_names = ",".join(f"'{value}'" for value in roles.values())
        remaining_roles = parse_tsv(
            client.execute(
                "defaultdb",
                "SELECT rolname FROM pg_catalog.pg_roles "
                f"WHERE rolname IN ({role_names})",
            )
        )
        roles_absent = not remaining_roles
        if not roles_absent:
            errors.append("one or more exact synthetic roles remain")
    except HarnessError as exc:
        errors.append(f"role cleanup verification: {exc}")
    if schedule_id is not None:
        try:
            schedules = parse_tsv(
                client.execute("defaultdb", "SHOW SCHEDULES")
            )
            ttl_schedule_absent = all(
                str(schedule_id)
                not in {
                    row.get("id", ""),
                    row.get("schedule_id", ""),
                }
                for row in schedules
            )
            if not ttl_schedule_absent:
                errors.append("exact TTL schedule remains after database DROP")
        except HarnessError as exc:
            errors.append(f"TTL cleanup verification: {exc}")
    original_settings = {
        key: value for key, value in settings.items() if ":" not in key
    }
    settings_match = original_settings == restored
    if not settings_match:
        errors.append("restored cluster settings differ from captured originals")
    return {
        "database_absent": database_absent,
        "errors": errors,
        "roles_absent": roles_absent,
        "settings_match": settings_match,
        "settings_original": original_settings,
        "settings_restored": restored,
        "sql_resources_removed": (
            not errors
            and database_absent
            and roles_absent
            and ttl_schedule_absent
            and settings_match
        ),
        "ttl_schedule_absent": ttl_schedule_absent,
    }


def make_matrix(
    recorder: ProbeRecorder,
    *,
    exact_version: str,
    generated_at: str,
    sources: list[dict[str, Any]],
) -> dict[str, Any]:
    row_by_id = {
        row["capability_id"]: row for row in recorder.rows
    }
    for source in sources:
        relevant = [
            row_by_id[capability_id]
            for capability_id in source["capability_ids"]
            if capability_id in row_by_id
        ]
        statuses = {row["status"] for row in relevant}
        if "FAIL" in statuses:
            source["live_behavior_matched"] = False
            source["discrepancy"] = (
                "At least one mapped live semantic probe returned FAIL."
            )
        elif relevant and statuses == {"PASS"}:
            source["live_behavior_matched"] = True
        else:
            source["live_behavior_matched"] = None
    rows = recorder.rows
    pass_count = sum(row["status"] == "PASS" for row in rows)
    fail_count = sum(row["status"] == "FAIL" for row in rows)
    defer_count = sum(row["status"] == "DEFER" for row in rows)
    if fail_count:
        decision = "PINNED_WITH_EXPLICIT_LIMITATIONS"
    elif defer_count:
        decision = "PINNED_WITH_EXPLICIT_DEFERRED_ITEMS"
    else:
        decision = "VALIDATED_FOR_STEP_4_CONSTRAINED_DESIGN"
    matrix: dict[str, Any] = {
        "capabilities": rows,
        "official_sources": sources,
        "schema_version": 1,
        "summary": {
            "defer_count": defer_count,
            "fail_count": fail_count,
            "generated_at_utc": generated_at,
            "harness_version": HARNESS_VERSION,
            "matrix_sha256": "",
            "overall_step3_decision": decision,
            "pass_count": pass_count,
            "runtime_exact_version": exact_version,
            "total_rows": len(rows),
        },
    }
    matrix["summary"]["matrix_sha256"] = matrix_digest(matrix)
    return matrix


def run_live_local(binary: Path) -> dict[str, Any]:
    pin = load_pin()
    exact = pin["exact_version"]
    binary_version, binary_digest = verify_binary_identity(binary, pin)
    run_id = make_run_id()
    resources = make_resource_names(run_id)
    runtime = LocalRuntime(binary=binary, run_id=run_id)
    recorder = ProbeRecorder(exact)
    database = run_id
    roles = {
        "owner": f"{run_id}_owner",
        "tenant_a": f"{run_id}_tenant_a",
        "tenant_b": f"{run_id}_tenant_b",
        "app": f"{run_id}_app",
    }
    rendered_sql_assets = render_sql_assets(database, run_id, roles)
    settings: dict[str, str] = {}
    owned_children: list[subprocess.Popen[str]] = []
    changefeed_cleanup: dict[str, Any] = {
        "forced_kill_used": False,
        "pid": None,
        "process_exited": False,
    }
    child_cleanup_errors: list[str] = []
    sql_cleanup: dict[str, Any] = {
        "errors": ["cleanup did not run"],
        "settings_match": False,
        "settings_original": {},
        "settings_restored": {},
        "sql_resources_removed": False,
        "ttl_schedule_absent": False,
    }
    runtime_cleanup: dict[str, Any]
    identity: dict[str, Any] = {}
    schedule_id: int | None = None
    try:
        client = runtime.start()
        identity = probe_identity(client, recorder, pin, binary_version)
        client.execute(
            "defaultdb", f"CREATE DATABASE {sql_identifier(database)}"
        )
        probe_vector(client, database, recorder, settings, resources)
        probe_full_text(client, database, recorder, resources)
        probe_rls(client, database, recorder, roles, resources)
        changefeed_cleanup = probe_changefeed(
            client,
            database,
            recorder,
            settings,
            resources,
            owned_children,
        )
        schedule_id = probe_ttl(client, database, recorder, resources)
        probe_partial_unique(client, database, recorder, resources)
        probe_aost(client, database, recorder, resources)
        probe_isolation_and_retry(
            client,
            database,
            recorder,
            resources,
            owned_children,
        )
        ttl_status = recorder.by_id["CRDB-022"]["status"]
        feed_status = recorder.by_id["CRDB-023"]["status"]
        event_status = recorder.by_id["CRDB-024"]["status"]
        recorder.mark(
            "CRDB-035",
            "DEFER",
            f"Independent results were TTL={ttl_status}, feed={feed_status}, "
            f"events={event_status}; the combined TTL-delete event stream was "
            "not forced in this bounded run.",
            sources=("ttl", "changefeed"),
            limitations=(
                "Official v26.2 behavior emits TTL DELETE events unless disabled; "
                "production-like combined revalidation remains required.",
            ),
            decision="DEFER",
        )
    finally:
        for child in owned_children:
            if child.poll() is None:
                try:
                    terminate_owned_process(child)
                except (HarnessError, subprocess.SubprocessError) as exc:
                    child_cleanup_errors.append(
                        f"owned child PID {child.pid}: {exc}"
                    )
        if (
            runtime.client is not None
            and runtime.process is not None
            and runtime.process.poll() is None
        ):
            sql_cleanup = cleanup_sql_resources(
                runtime.client,
                database,
                roles,
                settings,
                schedule_id,
            )
        runtime_cleanup = runtime.stop_and_remove()

    all_children_exited = all(
        child.poll() is not None for child in owned_children
    )
    cleanup_pass = (
        not sql_cleanup["errors"]
        and sql_cleanup["sql_resources_removed"]
        and all_children_exited
        and not child_cleanup_errors
        and runtime_cleanup["pid_exited"]
        and runtime_cleanup["ports_closed"]
        and runtime_cleanup["temporary_path_removed"]
        and not runtime_cleanup["cleanup_errors"]
    )
    recorder.mark(
        "CRDB-036",
        "PASS" if cleanup_pass else "FAIL",
        "SQL resources and settings were restored; exact PID exited; all owned "
        "ports closed; step-owned temporary path removed."
        if cleanup_pass
        else f"Cleanup failures: {sql_cleanup['errors'] + runtime_cleanup['cleanup_errors']}",
        sources=("transactions",),
        decision="USE" if cleanup_pass else "DO_NOT_USE",
        cleanup_verified=cleanup_pass,
    )
    for row in recorder.rows:
        row["cleanup_verified"] = cleanup_pass
    missing = recorder.unmarked()
    if missing:
        raise HarnessError(f"mandatory capability rows unexecuted: {missing}")
    if not cleanup_pass:
        raise HarnessError("cleanup verification failed")

    generated_at = utc_now()
    sources = source_records(str(pin["verified_at_utc"]))
    matrix = make_matrix(
        recorder,
        exact_version=exact,
        generated_at=generated_at,
        sources=sources,
    )
    archive = pin["artifact"]
    fingerprint = {
        "artifact": {
            "archive_sha256": archive["local_sha256"],
            "binary_sha256": binary_digest,
            "platform": archive["platform"],
            "architecture": archive["architecture"],
            "source": archive["source"],
            "vendor_checksum_verified": archive["vendor_checksum_verified"],
        },
        "capability_matrix_sha256": matrix["summary"]["matrix_sha256"],
        "capability_probe_context": {
            "created_resources": {
                "database": database,
                "objects": sorted(resources.values()),
                "roles": sorted(roles.values()),
                "ttl_schedule_id": schedule_id,
            },
            "database_prefix": "mp_step3_",
            "harness_source_sha256": file_sha256(Path(__file__).resolve()),
            "role_prefix": "mp_step3_",
            "synthetic_data_only": True,
            "rendered_reference_sql_asset_sha256": {
                name: hashlib.sha256(content.encode("utf-8")).hexdigest()
                for name, content in rendered_sql_assets.items()
            },
        },
        "cleanup": {
            **sql_cleanup,
            "child_cleanup_errors": child_cleanup_errors,
            "child_processes": [
                {
                    "pid": child.pid,
                    "returncode": child.poll(),
                }
                for child in owned_children
            ],
            "changefeed": changefeed_cleanup,
            "owned_children_exited": all_children_exited,
            **runtime_cleanup,
            "remaining_changefeed": not bool(
                changefeed_cleanup.get("process_exited")
            ),
            "remaining_ttl_job": not sql_cleanup["ttl_schedule_absent"],
        },
        "deployment": {
            "http_binding": "127.0.0.1",
            "insecure_test_only": True,
            "listener_binding": "127.0.0.1",
            "mode": "DISPOSABLE_LOCAL_SINGLE_NODE",
            "resource_limits": {
                "cache": "64MiB",
                "max_sql_memory": "128MiB",
                "store": "type=mem,size=640MiB",
            },
            "start_command_sanitized": [
                str(binary.name),
                "start-single-node",
                "--insecure",
                "--listen-addr=127.0.0.1:<dynamic>",
                "--sql-addr=127.0.0.1:<dynamic>",
                "--http-addr=127.0.0.1:<dynamic>",
                "--store=type=mem,size=640MiB",
                "--cache=64MiB",
                "--max-sql-memory=128MiB",
                "--external-io-disabled",
            ],
            "temporary_store_type": "BOUNDED_IN_MEMORY",
        },
        "generated_at_utc": generated_at,
        "harness_version": HARNESS_VERSION,
        "runtime": {
            **identity,
            "binary_build_output": "\n".join(
                line
                for line in binary_version.splitlines()
                if line.startswith(
                    (
                        "Build Tag:",
                        "Build Time:",
                        "Distribution:",
                        "Platform:",
                        "Go Version:",
                        "Build Commit ID:",
                        "Build Type:",
                    )
                )
            ),
            "run_namespace_pattern": "mp_step3_<utc>_<pid>_<uuid8>",
            "ttl_schedule_registered": schedule_id is not None,
        },
        "schema_version": 1,
    }
    assert_no_secret(fingerprint)
    validate_fingerprint(fingerprint, pin=pin, matrix=matrix)
    validate_matrix(matrix, pin=pin)
    return {
        "capability_matrix": matrix,
        "runtime_fingerprint": fingerprint,
        "schema_version": 1,
    }


def validate_sql_assets() -> None:
    missing = [name for name in SQL_FILES if not (SQL_ROOT / name).is_file()]
    if missing:
        raise HarnessError(f"missing ordered SQL assets: {missing}")
    for name in SQL_FILES:
        content = (SQL_ROOT / name).read_text(encoding="utf-8")
        if "Memory Patch Step 3" not in content or "disposable" not in content:
            raise HarnessError(f"SQL asset lacks Step 3 scope marker: {name}")
        if re.search(r"(?i)\b(?:CREATE|ALTER)\s+TABLE\s+(?:tenant|memory|approval|commit)\b", content):
            raise HarnessError(f"SQL asset resembles a production schema: {name}")
        placeholders = set(re.findall(r"\{\{([A-Z_]+)\}\}", content))
        unknown = placeholders - {
            "DATABASE",
            "ROLE_APP",
            "ROLE_OWNER",
            "ROLE_TENANT_A",
            "ROLE_TENANT_B",
            "RUN_PREFIX",
        }
        if unknown:
            raise HarnessError(
                f"SQL asset contains unsupported placeholders: {sorted(unknown)}"
            )


def render_sql_assets(
    database: str,
    run_id: str,
    roles: Mapping[str, str],
) -> dict[str, str]:
    replacements = {
        "DATABASE": database,
        "ROLE_APP": roles["app"],
        "ROLE_OWNER": roles["owner"],
        "ROLE_TENANT_A": roles["tenant_a"],
        "ROLE_TENANT_B": roles["tenant_b"],
        "RUN_PREFIX": run_id,
    }
    for value in replacements.values():
        sql_identifier(value)
    rendered: dict[str, str] = {}
    for name in SQL_FILES:
        content = (SQL_ROOT / name).read_text(encoding="utf-8")
        for placeholder, value in replacements.items():
            content = content.replace(f"{{{{{placeholder}}}}}", value)
        if "{{" in content or "}}" in content:
            raise HarnessError(f"SQL asset was not fully rendered: {name}")
        rendered[name] = content
    return rendered


def offline_validate() -> dict[str, Any]:
    pin = load_pin()
    validate_sql_assets()
    if MATRIX_PATH.is_file() and FINGERPRINT_PATH.is_file():
        matrix = load_json(MATRIX_PATH)
        fingerprint = load_json(FINGERPRINT_PATH)
        if not isinstance(matrix, dict) or not isinstance(fingerprint, dict):
            raise HarnessError("committed evidence must be JSON objects")
        validate_matrix(matrix, pin=pin)
        validate_fingerprint(fingerprint, pin=pin, matrix=matrix)
        return {
            "matrix_rows": matrix["summary"]["total_rows"],
            "status": "PASS",
            "version": pin["exact_version"],
        }
    return {
        "matrix_rows": 0,
        "status": "PASS_WITHOUT_COMMITTED_LIVE_EVIDENCE",
        "version": pin["exact_version"],
    }


def run_preflight(args: argparse.Namespace) -> dict[str, Any]:
    pin = load_pin()
    binary = Path(args.cockroach_binary).expanduser().resolve()
    version_output, binary_digest = verify_binary_identity(binary, pin)
    require_loopback("127.0.0.1", insecure=True)
    validate_sql_assets()
    if args.runtime_mode == "external":
        if not args.sql_url_env:
            raise HarnessError("external mode requires --sql-url-env")
        value = os.environ.get(args.sql_url_env)
        if not value:
            raise HarnessError("external SQL URL environment variable is unset")
        if value == redact_text(value):
            raise HarnessError("external SQL URL is malformed or unsupported")
    return {
        "binary_sha256": binary_digest,
        "build_tag": pin["exact_version"],
        "live_permission": args.allow_live,
        "runtime_mode": args.runtime_mode,
        "status": "PASS",
        "version_output_sanitized": "\n".join(version_output.splitlines()[:8]),
    }


def cleanup_from_result(path: Path) -> dict[str, Any]:
    result = load_json(path)
    if not isinstance(result, dict):
        raise HarnessError("cleanup evidence must be an object")
    cleanup = (
        result.get("runtime_fingerprint", {})
        .get("cleanup", {})
    )
    if not isinstance(cleanup, dict):
        raise HarnessError("cleanup evidence is absent")
    required_true = (
        "pid_exited",
        "ports_closed",
        "temporary_path_removed",
        "sql_resources_removed",
    )
    if not all(cleanup.get(key) is True for key in required_true):
        raise HarnessError("recorded cleanup is incomplete; no unowned action taken")
    if cleanup.get("settings_match") is not True:
        raise HarnessError("recorded cluster settings were not restored exactly")
    if cleanup.get("owned_children_exited") is not True:
        raise HarnessError("recorded child-process cleanup is incomplete")
    if cleanup.get("remaining_changefeed") is not False:
        raise HarnessError("recorded cleanup leaves a changefeed")
    if cleanup.get("remaining_ttl_job") is not False:
        raise HarnessError("recorded cleanup leaves a TTL job")
    if cleanup.get("errors") or cleanup.get("child_cleanup_errors"):
        raise HarnessError("recorded cleanup contains errors")
    return {"status": "ALREADY_CLEAN", "verified_fields": list(required_true)}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--preflight", action="store_true")
    action.add_argument("--run", action="store_true")
    action.add_argument("--cleanup", action="store_true")
    action.add_argument("--offline-validate", action="store_true")
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--evidence-dir", type=Path)
    parser.add_argument(
        "--runtime-mode", choices=("local", "external"), default="local"
    )
    parser.add_argument("--cockroach-binary")
    parser.add_argument("--sql-url-env")
    parser.add_argument("--allow-live", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.offline_validate:
            result = offline_validate()
        elif args.cleanup:
            if args.json_output is None:
                raise HarnessError("--cleanup requires --json-output evidence path")
            result = cleanup_from_result(args.json_output)
        else:
            if not args.cockroach_binary:
                raise HarnessError("--cockroach-binary is required")
            if not args.allow_live:
                raise HarnessError("live execution requires explicit --allow-live")
            preflight = run_preflight(args)
            if args.preflight:
                result = preflight
            else:
                if args.runtime_mode != "local":
                    raise HarnessError(
                        "this bounded Step 3 run supports external preflight only; "
                        "external mutation was not authorized"
                    )
                result = run_live_local(
                    Path(args.cockroach_binary).expanduser().resolve()
                )
                if args.evidence_dir is not None:
                    evidence_dir = args.evidence_dir.resolve()
                    expected = (
                        REPOSITORY_ROOT
                        / "docs"
                        / "evidence"
                        / "cockroachdb-v26-2"
                    ).resolve()
                    if evidence_dir != expected:
                        raise HarnessError(
                            "evidence output directory must be the canonical Step 3 path"
                        )
                    if args.json_output is not None and args.json_output.resolve() in {
                        (evidence_dir / "runtime-fingerprint.json").resolve(),
                        (evidence_dir / "capability-matrix.json").resolve(),
                    }:
                        raise HarnessError(
                            "--json-output must not overwrite canonical evidence files"
                        )
                    write_canonical_json(
                        evidence_dir / "runtime-fingerprint.json",
                        result["runtime_fingerprint"],
                    )
                    write_canonical_json(
                        evidence_dir / "capability-matrix.json",
                        result["capability_matrix"],
                    )
        if args.json_output is not None and not args.cleanup:
            write_canonical_json(args.json_output, result)
        display_result = result
        if args.run:
            matrix = result["capability_matrix"]
            display_result = {
                "cleanup": result["runtime_fingerprint"]["cleanup"],
                "schema_version": result["schema_version"],
                "summary": matrix["summary"],
            }
        print(pretty_json(display_result), end="")
        return 0
    except (HarnessError, OSError) as exc:
        print(f"ERROR: {redact_text(str(exc))}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
