"""Validation-only DB-API subset over one interactive CockroachDB CLI.

This module exists only for repository-controlled disposable validation.
Production code continues to depend on the typed DB-API protocols from the
Step 6 persistence boundary. Each connection owns exactly one CLI child and
therefore preserves one server transaction across cursor calls.
"""

from __future__ import annotations

import csv
import os
import queue
import re
import subprocess
import threading
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any


_SAFE_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]{2,62}$")
_SQLSTATE = re.compile(r"SQLSTATE:\s*([0-9A-Z]{5})")
_END_OF_STREAM = object()


class CockroachCliDbapiError(RuntimeError):
    """Sanitized validation transport failure with optional SQLSTATE."""

    def __init__(
        self,
        message: str = "CockroachDB CLI transaction failed",
        *,
        sqlstate: str | None = None,
        sanitized_code: str = "COCKROACH_CLI_TRANSACTION_FAILED",
    ) -> None:
        super().__init__(message)
        self.sqlstate = sqlstate
        self.pgcode = sqlstate
        self.sanitized_code = sanitized_code


class OwnedChildRegistry:
    """Track every interactive CLI child until its exact PID is reaped."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._processes: dict[int, subprocess.Popen[str]] = {}

    def register(self, process: subprocess.Popen[str]) -> None:
        with self._lock:
            if process.pid in self._processes:
                raise CockroachCliDbapiError(
                    "interactive CLI child was registered twice",
                    sanitized_code="DUPLICATE_COCKROACH_CLI_CHILD",
                )
            self._processes[process.pid] = process

    def release(self, process: subprocess.Popen[str]) -> None:
        if process.poll() is None:
            raise CockroachCliDbapiError(
                "interactive CLI child was not reaped",
                sanitized_code="COCKROACH_CLI_CHILD_REMAINS",
            )
        with self._lock:
            observed = self._processes.pop(process.pid, None)
        if observed is not process:
            raise CockroachCliDbapiError(
                "interactive CLI child registry differs",
                sanitized_code="COCKROACH_CLI_CHILD_REGISTRY_MISMATCH",
            )

    @property
    def active_pids(self) -> tuple[int, ...]:
        with self._lock:
            return tuple(sorted(self._processes))

    @property
    def all_reaped(self) -> bool:
        return not self.active_pids


def decode_csv_cell(value: str) -> object:
    """Decode Cockroach CLI's validation-only CSV NULL sentinel."""

    if not isinstance(value, str):
        raise CockroachCliDbapiError(
            "interactive CLI returned a non-text CSV cell",
            sanitized_code="COCKROACH_CLI_PROTOCOL_MISMATCH",
        )
    return None if value == "NULL" else value


def _identifier(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _SAFE_IDENTIFIER.fullmatch(value) is None:
        raise CockroachCliDbapiError(
            f"{field_name} is not a safe disposable identifier",
            sanitized_code="INVALID_COCKROACH_CLI_CONFIG",
        )
    return value


def sql_literal(value: object) -> str:
    """Render the bounded values used by repository validation SQL."""

    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise CockroachCliDbapiError(
                "naive timestamp is forbidden",
                sanitized_code="INVALID_COCKROACH_CLI_PARAMETER",
            )
        value = value.isoformat()
    if isinstance(value, str):
        if "\x00" in value:
            raise CockroachCliDbapiError(
                "NUL is forbidden in SQL parameters",
                sanitized_code="INVALID_COCKROACH_CLI_PARAMETER",
            )
        return "'" + value.replace("'", "''") + "'"
    raise CockroachCliDbapiError(
        "unsupported SQL parameter type",
        sanitized_code="INVALID_COCKROACH_CLI_PARAMETER",
    )


def render_sql(
    sql: str,
    parameters: Sequence[object] | None,
) -> str:
    """Substitute DB-API ``%s`` positions with validated SQL literals."""

    if not isinstance(sql, str) or not sql.strip():
        raise CockroachCliDbapiError(
            "SQL statement is empty",
            sanitized_code="INVALID_COCKROACH_CLI_STATEMENT",
        )
    values = tuple(parameters or ())
    fragments = sql.split("%s")
    if len(fragments) - 1 != len(values):
        raise CockroachCliDbapiError(
            "SQL placeholder count differs from parameters",
            sanitized_code="INVALID_COCKROACH_CLI_PARAMETERS",
        )
    rendered = fragments[0]
    for value, fragment in zip(values, fragments[1:], strict=True):
        rendered += sql_literal(value) + fragment
    return rendered.strip().rstrip(";")


class CockroachCliCursor:
    """Mapping-row cursor for the Step 6 transaction runner."""

    def __init__(self, connection: "CockroachCliConnection") -> None:
        self._connection = connection
        self._rows: tuple[dict[str, object], ...] = ()
        self._offset = 0
        self._closed = False

    def execute(
        self,
        sql: str,
        parameters: Sequence[object] | None = None,
    ) -> None:
        if self._closed:
            raise CockroachCliDbapiError(
                "cursor is closed",
                sanitized_code="CLOSED_COCKROACH_CLI_CURSOR",
            )
        self._rows = self._connection._execute(sql, parameters)
        self._offset = 0

    def fetchone(self) -> Mapping[str, object] | None:
        if self._closed:
            raise CockroachCliDbapiError(
                "cursor is closed",
                sanitized_code="CLOSED_COCKROACH_CLI_CURSOR",
            )
        if self._offset >= len(self._rows):
            return None
        row = self._rows[self._offset]
        self._offset += 1
        return row

    def fetchall(self) -> Sequence[Mapping[str, object]]:
        if self._closed:
            raise CockroachCliDbapiError(
                "cursor is closed",
                sanitized_code="CLOSED_COCKROACH_CLI_CURSOR",
            )
        rows = self._rows[self._offset :]
        self._offset = len(self._rows)
        return rows

    def close(self) -> None:
        self._closed = True
        self._rows = ()


class CockroachCliConnection:
    """One transaction-capable interactive CLI child."""

    def __init__(
        self,
        *,
        binary: Path,
        host: str,
        port: int,
        database: str,
        user: str,
        log_directory: Path,
        child_registry: OwnedChildRegistry | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        if (
            not isinstance(binary, Path)
            or not binary.is_file()
            or binary.is_symlink()
            or host != "127.0.0.1"
            or not isinstance(port, int)
            or not 1 <= port <= 65535
            or not isinstance(log_directory, Path)
            or not log_directory.is_dir()
            or log_directory.is_symlink()
            or not isinstance(timeout_seconds, (int, float))
            or not 1 <= timeout_seconds <= 300
        ):
            raise CockroachCliDbapiError(
                "interactive CLI connection configuration is unsafe",
                sanitized_code="INVALID_COCKROACH_CLI_CONFIG",
            )
        self._binary = binary
        self._host = host
        self._port = port
        self._database = _identifier(database, "database")
        self._user = _identifier(user, "user")
        self._timeout = float(timeout_seconds)
        self._child_registry = child_registry or OwnedChildRegistry()
        self._closed = False
        self._cursor: CockroachCliCursor | None = None
        self._rows: queue.Queue[object] = queue.Queue()
        log_name = f"dbapi-{os.getpid()}-{uuid.uuid4().hex[:12]}.log"
        self._log_path = log_directory / log_name
        self._stderr = self._log_path.open("w+", encoding="utf-8")
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
        self._process = subprocess.Popen(
            [
                str(binary),
                "sql",
                "--insecure",
                f"--host={host}",
                f"--port={port}",
                f"--database={self._database}",
                f"--user={self._user}",
                "--format=csv",
                "--set=errexit=true",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self._stderr,
            text=True,
            encoding="utf-8",
            env=environment,
        )
        self._child_registry.register(self._process)
        if self._process.stdin is None or self._process.stdout is None:
            self._terminate_exact_child()
            self._child_registry.release(self._process)
            raise CockroachCliDbapiError(
                "interactive CLI pipes are unavailable",
                sanitized_code="COCKROACH_CLI_START_FAILED",
            )
        self._reader = threading.Thread(
            target=self._read_rows,
            name=f"mp-step10-dbapi-{self._process.pid}",
            daemon=True,
        )
        self._reader.start()

    @property
    def child_pid(self) -> int:
        return self._process.pid

    def _read_rows(self) -> None:
        assert self._process.stdout is not None
        try:
            for row in csv.reader(self._process.stdout):
                self._rows.put(tuple(row))
        finally:
            self._rows.put(_END_OF_STREAM)

    def _error(self, code: str) -> CockroachCliDbapiError:
        self._stderr.flush()
        self._stderr.seek(0)
        diagnostics = self._stderr.read()
        match = _SQLSTATE.search(diagnostics)
        return CockroachCliDbapiError(
            sqlstate=match.group(1) if match else None,
            sanitized_code=code,
        )

    def _next_row(self, deadline: float) -> tuple[str, ...]:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            self._terminate_exact_child()
            raise self._error("COCKROACH_CLI_STATEMENT_TIMEOUT")
        try:
            row = self._rows.get(timeout=remaining)
        except queue.Empty:
            self._terminate_exact_child()
            raise self._error("COCKROACH_CLI_STATEMENT_TIMEOUT") from None
        if row is _END_OF_STREAM:
            raise self._error("COCKROACH_CLI_TRANSACTION_FAILED")
        assert isinstance(row, tuple)
        return row

    def _execute(
        self,
        sql: str,
        parameters: Sequence[object] | None,
    ) -> tuple[dict[str, object], ...]:
        if self._closed or self._process.poll() is not None:
            raise self._error("CLOSED_COCKROACH_CLI_CONNECTION")
        rendered = render_sql(sql, parameters)
        marker = "mp_marker_" + uuid.uuid4().hex
        marker_column = "__" + marker
        command = (
            rendered
            + ";\nSELECT "
            + sql_literal(marker)
            + ' AS "'
            + marker_column
            + '";\n'
        )
        assert self._process.stdin is not None
        try:
            self._process.stdin.write(command)
            self._process.stdin.flush()
        except (BrokenPipeError, OSError):
            raise self._error("COCKROACH_CLI_TRANSACTION_FAILED") from None
        deadline = time.monotonic() + self._timeout
        header: tuple[str, ...] | None = None
        values: list[dict[str, object]] = []
        while True:
            row = self._next_row(deadline)
            if row == (marker_column,):
                terminal = self._next_row(deadline)
                if terminal != (marker,):
                    raise CockroachCliDbapiError(
                        "interactive CLI marker binding failed",
                        sanitized_code="COCKROACH_CLI_PROTOCOL_MISMATCH",
                    )
                return tuple(values)
            if header is None:
                header = row
                if not header or len(set(header)) != len(header):
                    raise CockroachCliDbapiError(
                        "interactive CLI returned invalid columns",
                        sanitized_code="COCKROACH_CLI_PROTOCOL_MISMATCH",
                    )
                continue
            if len(row) != len(header):
                raise CockroachCliDbapiError(
                    "interactive CLI row width differs",
                    sanitized_code="COCKROACH_CLI_PROTOCOL_MISMATCH",
                )
            values.append(
                {
                    key: decode_csv_cell(value)
                    for key, value in zip(header, row, strict=True)
                }
            )

    def cursor(self) -> CockroachCliCursor:
        if self._closed or self._cursor is not None:
            raise CockroachCliDbapiError(
                "connection supports exactly one cursor",
                sanitized_code="INVALID_COCKROACH_CLI_CURSOR",
            )
        self._cursor = CockroachCliCursor(self)
        return self._cursor

    def commit(self) -> None:
        self._execute("COMMIT", None)

    def rollback(self) -> None:
        if self._closed or self._process.poll() is not None:
            return
        self._execute("ROLLBACK", None)

    def _terminate_exact_child(self) -> None:
        if self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=3)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            if self._process.poll() is None and self._process.stdin is not None:
                try:
                    self._process.stdin.write("\\q\n")
                    self._process.stdin.flush()
                    self._process.wait(timeout=3)
                except (BrokenPipeError, OSError, subprocess.TimeoutExpired):
                    self._terminate_exact_child()
            self._reader.join(timeout=3)
            if self._process.stdout is not None:
                self._process.stdout.close()
            if self._process.stdin is not None:
                self._process.stdin.close()
            self._stderr.close()
            self._reader.join(timeout=1)
            if self._reader.is_alive():
                raise CockroachCliDbapiError(
                    "interactive CLI reader thread remains",
                    sanitized_code="COCKROACH_CLI_READER_REMAINS",
                )
        finally:
            self._child_registry.release(self._process)


def connection_factory(
    *,
    binary: Path,
    port: int,
    database: str,
    user: str,
    log_directory: Path,
    child_registry: OwnedChildRegistry | None = None,
    timeout_seconds: float = 30.0,
) -> Callable[[], CockroachCliConnection]:
    """Return a validation-only factory accepted by the Step 6 runner."""

    def connect() -> CockroachCliConnection:
        return CockroachCliConnection(
            binary=binary,
            host="127.0.0.1",
            port=port,
            database=database,
            user=user,
            log_directory=log_directory,
            child_registry=child_registry,
            timeout_seconds=timeout_seconds,
        )

    return connect


__all__ = [
    "CockroachCliConnection",
    "CockroachCliCursor",
    "CockroachCliDbapiError",
    "OwnedChildRegistry",
    "connection_factory",
    "decode_csv_cell",
    "render_sql",
    "sql_literal",
]
