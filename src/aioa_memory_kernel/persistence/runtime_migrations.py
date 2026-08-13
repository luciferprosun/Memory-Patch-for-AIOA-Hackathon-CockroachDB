"""Hosted pgwire adapter for the repository's one canonical migration runner.

The migration plan, checksums, special role phases, catalog assertions, and
ledger semantics remain defined by ``scripts/run_cockroachdb_migrations.py``.
This module supplies only a TLS-capable Psycopg transport and a fail-closed
state classifier so the ASGI runtime does not invent a second migration
system.
"""

from __future__ import annotations

import csv
import importlib.util
import io
import re
import socket
import sys
import threading
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from types import ModuleType
from typing import Any, Mapping, Protocol, Sequence

from aioa_memory_kernel.security.credentials import CredentialPurpose, SecretValue


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
CANONICAL_RUNNER_PATH = REPOSITORY_ROOT / "scripts" / "run_cockroachdb_migrations.py"
CANONICAL_RUNNER_MODULE_NAME = "_aioa_memory_patch_canonical_migration_runner"
PINNED_COCKROACHDB_VERSION = "v26.2.4"
CANONICAL_MIGRATION_COUNT = 19
LATEST_MIGRATION_ID = "0019_post_roadmap_demo_runtime_state"
MAX_MIGRATION_SESSION_SECONDS = 45 * 60
_COCKROACH_VERSION_PATTERN = re.compile(
    r"\bCockroachDB\s+CCL\s+v(?P<version>\d+\.\d+\.\d+)\b"
)


class MigrationState(str, Enum):
    UP_TO_DATE = "UP_TO_DATE"
    MIGRATIONS_REQUIRED = "MIGRATIONS_REQUIRED"
    MIGRATION_FAILED = "MIGRATION_FAILED"
    SCHEMA_AHEAD_OR_UNKNOWN = "SCHEMA_AHEAD_OR_UNKNOWN"
    MIGRATION_METADATA_INVALID = "MIGRATION_METADATA_INVALID"


class RuntimeMigrationError(RuntimeError):
    """Sanitized migration transport/state failure."""

    def __init__(self, state: MigrationState, code: str) -> None:
        if not isinstance(state, MigrationState):
            raise TypeError("migration state must be typed")
        if not isinstance(code, str) or re.fullmatch(r"[A-Z][A-Z0-9_]{2,127}", code) is None:
            raise TypeError("migration error code must be sanitized")
        super().__init__(f"CockroachDB migration failed safely: {code}")
        self.state = state
        self.sanitized_code = code


class CanonicalMigrationRunner(Protocol):
    def load_migrations(self) -> Sequence[object]: ...

    def applied_migrations(self, client: object, database: str) -> Mapping[str, str]: ...

    def apply_migrations(
        self,
        client: object,
        database: str,
        *,
        timeout: float,
    ) -> Mapping[str, object]: ...


@dataclass(frozen=True, slots=True)
class MigrationInspection:
    state: MigrationState
    discovered: int
    applied: int
    pending: int
    latest_migration_id: str


@dataclass(frozen=True, slots=True)
class MigrationExecutionSummary:
    initial_state: MigrationState
    final_state: MigrationState
    discovered: int
    applied: int
    replay_skipped: int
    failures: int
    latest_migration_id: str

    def __post_init__(self) -> None:
        if self.final_state is not MigrationState.UP_TO_DATE or self.failures != 0:
            raise RuntimeMigrationError(
                MigrationState.MIGRATION_FAILED,
                "MIGRATION_SUMMARY_NOT_READY",
            )


class _MigrationSqlFailure(RuntimeError):
    def __init__(self, *, sqlstate: str | None = None) -> None:
        super().__init__("CockroachDB command failed safely")
        self.sqlstate = sqlstate


def _render_tsv_value(value: object) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        # The canonical runner was deliberately written against CockroachDB
        # CLI TSV output. Preserve its exact boolean representation so its
        # already-audited catalog assertions remain the one source of truth.
        return "t" if value else "f"
    if isinstance(value, bytes):
        return "\\x" + value.hex()
    return str(value)


def _split_canonical_sql_statements(sql: str) -> tuple[str, ...]:
    """Split fixed repository SQL without splitting quoted function bodies.

    The canonical CockroachDB CLI executes the audited migration batches as
    individual statements. Psycopg must preserve that shape:
    a single simple-query message containing the whole Step 30 migration can
    turn its 119 DDL statements into one pathologically long server batch.
    This scanner is adapted from the already validated Step 18 migration
    transport and is used only for SQL loaded by the fixed canonical runner.
    The scanner retains wrapper statements; the transport applies the
    repository's existing Step18/30/35 statement-wise autocommit policy.
    """

    if (
        not isinstance(sql, str)
        or not sql.strip()
        or len(sql.encode("utf-8")) > 4 * 1024 * 1024
    ):
        raise RuntimeMigrationError(
            MigrationState.MIGRATION_FAILED,
            "MIGRATION_SQL_REQUEST_INVALID",
        )
    statements: list[str] = []
    current: list[str] = []
    has_code = False
    index = 0
    single = False
    double = False
    line_comment = False
    block_depth = 0
    dollar_tag: str | None = None
    while index < len(sql):
        if dollar_tag is not None:
            if sql.startswith(dollar_tag, index):
                current.append(dollar_tag)
                index += len(dollar_tag)
                dollar_tag = None
            else:
                current.append(sql[index])
                index += 1
            continue
        character = sql[index]
        following = sql[index + 1] if index + 1 < len(sql) else ""
        if line_comment:
            current.append(character)
            index += 1
            if character == "\n":
                line_comment = False
            continue
        if block_depth:
            current.append(character)
            if character == "/" and following == "*":
                current.append(following)
                block_depth += 1
                index += 2
            elif character == "*" and following == "/":
                current.append(following)
                block_depth -= 1
                index += 2
            else:
                index += 1
            continue
        if single:
            current.append(character)
            index += 1
            if character == "'":
                if following == "'":
                    current.append(following)
                    index += 1
                else:
                    single = False
            continue
        if double:
            current.append(character)
            index += 1
            if character == '"':
                if following == '"':
                    current.append(following)
                    index += 1
                else:
                    double = False
            continue
        if character == "-" and following == "-":
            current.extend((character, following))
            line_comment = True
            index += 2
            continue
        if character == "/" and following == "*":
            current.extend((character, following))
            block_depth = 1
            index += 2
            continue
        if character == "'":
            current.append(character)
            single = True
            has_code = True
            index += 1
            continue
        if character == '"':
            current.append(character)
            double = True
            has_code = True
            index += 1
            continue
        if character == "$":
            end = sql.find("$", index + 1)
            if end != -1:
                candidate = sql[index : end + 1]
                tag_body = candidate[1:-1]
                if not tag_body or (
                    (tag_body[0].isalpha() or tag_body[0] == "_")
                    and all(value.isalnum() or value == "_" for value in tag_body)
                ):
                    current.append(candidate)
                    dollar_tag = candidate
                    has_code = True
                    index = end + 1
                    continue
        if character == ";":
            if has_code:
                statements.append("".join(current).strip())
                if len(statements) > 2048:
                    raise RuntimeMigrationError(
                        MigrationState.MIGRATION_FAILED,
                        "MIGRATION_SQL_REQUEST_INVALID",
                    )
            current = []
            has_code = False
            index += 1
            continue
        current.append(character)
        if not character.isspace():
            has_code = True
        index += 1
    if single or double or block_depth or dollar_tag:
        raise RuntimeMigrationError(
            MigrationState.MIGRATION_FAILED,
            "MIGRATION_SQL_TOKEN_UNTERMINATED",
        )
    if has_code:
        statements.append("".join(current).strip())
    if not statements or len(statements) > 2048:
        raise RuntimeMigrationError(
            MigrationState.MIGRATION_FAILED,
            "MIGRATION_SQL_REQUEST_INVALID",
        )
    return tuple(statements)


def _leading_sql_keyword(statement: str) -> str:
    index = 0
    while index < len(statement):
        if statement[index].isspace():
            index += 1
            continue
        if statement.startswith("--", index):
            newline = statement.find("\n", index + 2)
            index = len(statement) if newline < 0 else newline + 1
            continue
        if statement.startswith("/*", index):
            end = statement.find("*/", index + 2)
            if end < 0:
                raise RuntimeMigrationError(
                    MigrationState.MIGRATION_FAILED,
                    "MIGRATION_SQL_TOKEN_UNTERMINATED",
                )
            index = end + 2
            continue
        end = index
        while end < len(statement) and (
            statement[end].isalpha() or statement[end] == "_"
        ):
            end += 1
        return statement[index:end].upper()
    return ""


def _cancel_owned_connection(
    connection: object,
    timed_out: threading.Event,
    finished: threading.Event,
) -> None:
    timed_out.set()
    cancel_safe = getattr(connection, "cancel_safe", None)
    cancel = getattr(connection, "cancel", None)
    if callable(cancel_safe):
        try:
            cancel_safe(timeout=5.0)
        except Exception:
            pass
    elif callable(cancel):
        # Test doubles and older drivers only. The pinned R3 runtime supplies
        # cancel_safe() through Psycopg 3.3.4 and bundled libpq 18.
        try:
            cancel()
        except Exception:
            pass
    if finished.wait(timeout=5.0):
        return
    # A successful PostgreSQL cancellation request is not a guarantee that
    # the server cancelled the operation. Shut down only this owned
    # connection's socket after the grace period so startup cannot remain
    # stuck with migrator authority. detach() avoids taking ownership of
    # libpq's descriptor; normal connection.close() still owns it.
    fileno = getattr(connection, "fileno", None)
    if callable(fileno):
        try:
            descriptor = fileno()
            transport = socket.socket(fileno=descriptor)
            try:
                transport.shutdown(socket.SHUT_RDWR)
            finally:
                transport.detach()
        except Exception:
            pass


class PsycopgMigrationSqlClient:
    """One short-lived autocommit connection for operations-only migrations."""

    def __init__(
        self,
        *,
        credential: SecretValue,
        database: str,
        connection_timeout_seconds: int,
        statement_timeout_seconds: int,
        connect: object | None = None,
    ) -> None:
        if (
            not isinstance(credential, SecretValue)
            or credential.purpose is not CredentialPurpose.MIGRATION_DATABASE
        ):
            raise RuntimeMigrationError(
                MigrationState.MIGRATION_FAILED,
                "MIGRATION_DATABASE_CREDENTIAL_REQUIRED",
            )
        if (
            not isinstance(database, str)
            or not database
            or len(database.encode("utf-8")) > 128
            or any(ord(character) < 32 or ord(character) == 127 for character in database)
        ):
            raise RuntimeMigrationError(
                MigrationState.MIGRATION_FAILED,
                "MIGRATION_DATABASE_IDENTITY_INVALID",
            )
        for value, upper in (
            (connection_timeout_seconds, 15),
            (statement_timeout_seconds, 300),
        ):
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or not 1 <= value <= upper
            ):
                raise RuntimeMigrationError(
                    MigrationState.MIGRATION_FAILED,
                    "MIGRATION_TIMEOUT_INVALID",
                )
        self._credential = credential
        self._database = database
        self._connection_timeout_seconds = connection_timeout_seconds
        self._statement_timeout_seconds = statement_timeout_seconds
        self._connect = connect
        self._connect_callable: object | None = None
        self._connect_kwargs: Mapping[str, object] | None = None
        self._session_deadline: float | None = None
        self._opened = False
        self._closed = False
        self._state_lock = threading.Lock()
        self._operation_lock = threading.Lock()

    def open(self) -> "PsycopgMigrationSqlClient":
        with self._state_lock:
            if self._closed:
                raise RuntimeMigrationError(
                    MigrationState.MIGRATION_FAILED,
                    "MIGRATION_CONNECTION_ALREADY_CLOSED",
                )
            if self._opened:
                return self
            connect = self._connect
            if connect is None:
                try:
                    import psycopg
                    from psycopg import ClientCursor
                    from psycopg.rows import dict_row
                except ImportError:
                    raise RuntimeMigrationError(
                        MigrationState.MIGRATION_FAILED,
                        "PSYCOPG_RUNTIME_DEPENDENCY_MISSING",
                    ) from None
                connect = psycopg.connect
                row_factory: object | None = dict_row
                cursor_factory: object | None = ClientCursor
            else:
                row_factory = None
                cursor_factory = None
            kwargs: dict[str, object] = {
                "autocommit": True,
                "connect_timeout": self._connection_timeout_seconds,
                "options": (
                    "-c statement_timeout="
                    f"{self._statement_timeout_seconds * 1000}"
                ),
                "prepare_threshold": None,
            }
            if row_factory is not None:
                kwargs["row_factory"] = row_factory
            if cursor_factory is not None:
                # The audited runner intentionally sends DDL-heavy,
                # multi-statement batches. ClientCursor preserves the simple
                # query protocol used by the Cockroach CLI reference path.
                kwargs["cursor_factory"] = cursor_factory
            self._connect_callable = connect
            self._connect_kwargs = dict(kwargs)
            self._session_deadline = (
                time.monotonic() + MAX_MIGRATION_SESSION_SECONDS
            )
            self._opened = True
        return self

    def _new_connection(self) -> object:
        with self._state_lock:
            if (
                self._closed
                or not self._opened
                or self._connect_callable is None
                or self._connect_kwargs is None
            ):
                raise RuntimeMigrationError(
                    MigrationState.MIGRATION_FAILED,
                    "MIGRATION_DATABASE_CONNECTION_UNAVAILABLE",
                )
            connect = self._connect_callable
            kwargs = dict(self._connect_kwargs)
        raw_dsn = self._credential.reveal_for(CredentialPurpose.MIGRATION_DATABASE)
        try:
            return connect(raw_dsn, **kwargs)  # type: ignore[operator]
        except Exception:
            raise RuntimeMigrationError(
                MigrationState.MIGRATION_FAILED,
                "MIGRATION_DATABASE_CONNECTION_FAILED",
            ) from None

    def _remaining_session_seconds(self) -> float:
        with self._state_lock:
            deadline = self._session_deadline
            if self._closed or not self._opened or deadline is None:
                raise RuntimeMigrationError(
                    MigrationState.MIGRATION_FAILED,
                    "MIGRATION_DATABASE_CONNECTION_UNAVAILABLE",
                )
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeMigrationError(
                MigrationState.MIGRATION_FAILED,
                "MIGRATION_SESSION_TIMEOUT",
            )
        return remaining

    def execute(
        self,
        database: str,
        sql: str,
        *,
        timeout: float = 60.0,
    ) -> str:
        # The fixed canonical runner performs cluster-role catalog assertions
        # against the logical ``defaultdb`` target. Those pg_catalog views are
        # cluster-scoped, so the same purpose-sealed connection may service
        # exactly that one additional audited identity. Arbitrary databases
        # remain forbidden.
        if database not in {self._database, "defaultdb"}:
            raise RuntimeMigrationError(
                MigrationState.MIGRATION_FAILED,
                "MIGRATION_DATABASE_TARGET_MISMATCH",
            )
        if (
            not isinstance(sql, str)
            or not sql
            or not isinstance(timeout, (int, float))
            or isinstance(timeout, bool)
            or not 0 < float(timeout) <= self._statement_timeout_seconds
        ):
            raise RuntimeMigrationError(
                MigrationState.MIGRATION_FAILED,
                "MIGRATION_SQL_REQUEST_INVALID",
            )
        # Reuse the repository's established Step18/30/35 transport policy:
        # canonical DDL is statement-wise autocommit, while outer transaction
        # wrapper tokens are not forwarded. The migration ledger is still
        # inserted only after every statement and catalog assertion succeeds.
        # Therefore any partial DDL leaves no successful ledger record and the
        # application pool is never opened against uncertain schema state.
        statements = tuple(
            statement
            for statement in _split_canonical_sql_statements(sql)
            if _leading_sql_keyword(statement) not in {"BEGIN", "COMMIT", "END"}
        )
        if not statements:
            raise RuntimeMigrationError(
                MigrationState.MIGRATION_FAILED,
                "MIGRATION_SQL_REQUEST_INVALID",
            )
        with self._operation_lock:
            connection = self._new_connection()
            cursor = None
            statement_timed_out: threading.Event | None = None
            statement_finished: threading.Event | None = None
            watchdog: threading.Timer | None = None
            result_names: tuple[str, ...] | None = None
            result_rows: tuple[object, ...] = ()
            try:
                cursor = connection.cursor()  # type: ignore[attr-defined]
                cursor.execute(
                    "SELECT set_config('statement_timeout', %s, false)",
                    (f"{int(float(timeout) * 1000)}ms",),
                )
                cursor.close()
                cursor = None
                for statement in statements:
                    statement_timeout = min(
                        float(timeout),
                        self._remaining_session_seconds(),
                    )
                    statement_timed_out = threading.Event()
                    statement_finished = threading.Event()
                    watchdog = threading.Timer(
                        statement_timeout,
                        _cancel_owned_connection,
                        args=(
                            connection,
                            statement_timed_out,
                            statement_finished,
                        ),
                    )
                    watchdog.daemon = True
                    cursor = connection.cursor()  # type: ignore[attr-defined]
                    watchdog.start()
                    try:
                        cursor.execute(statement, prepare=False)
                    except TypeError:
                        cursor.execute(statement)
                    finally:
                        statement_finished.set()
                        watchdog.cancel()
                        if watchdog is not threading.current_thread():
                            watchdog.join(timeout=11.0)
                        watchdog = None
                    if statement_timed_out.is_set():
                        raise RuntimeMigrationError(
                            MigrationState.MIGRATION_FAILED,
                            "MIGRATION_OPERATION_TIMEOUT",
                        )
                    if cursor.description is not None:
                        result_names = tuple(
                            column.name for column in cursor.description
                        )
                        result_rows = tuple(cursor.fetchall())
                    cursor.close()
                    cursor = None
                if result_names is None:
                    return ""
                output = io.StringIO()
                writer = csv.writer(output, delimiter="\t", lineterminator="\n")
                writer.writerow(result_names)
                for row in result_rows:
                    if isinstance(row, Mapping):
                        values = tuple(row[name] for name in result_names)
                    else:
                        values = tuple(row)
                    writer.writerow(
                        tuple(_render_tsv_value(value) for value in values)
                    )
                return output.getvalue()
            except RuntimeMigrationError:
                raise
            except Exception as error:
                if statement_timed_out is not None and statement_timed_out.is_set():
                    raise RuntimeMigrationError(
                        MigrationState.MIGRATION_FAILED,
                        "MIGRATION_OPERATION_TIMEOUT",
                    ) from None
                sqlstate = getattr(error, "sqlstate", None)
                if (
                    not isinstance(sqlstate, str)
                    or re.fullmatch(r"[0-9A-Z]{5}", sqlstate) is None
                ):
                    sqlstate = None
                raise _MigrationSqlFailure(sqlstate=sqlstate) from None
            finally:
                if statement_finished is not None:
                    statement_finished.set()
                if watchdog is not None:
                    watchdog.cancel()
                    if watchdog is not threading.current_thread():
                        watchdog.join(timeout=11.0)
                if cursor is not None:
                    try:
                        cursor.close()
                    except Exception:
                        pass
                try:
                    connection.close()  # type: ignore[attr-defined]
                except Exception:
                    raise RuntimeMigrationError(
                        MigrationState.MIGRATION_FAILED,
                        "MIGRATION_DATABASE_CLOSE_FAILED",
                    ) from None

    def close(self) -> None:
        with self._operation_lock:
            with self._state_lock:
                if self._closed:
                    return
                self._closed = True
                self._opened = False
                self._connect_callable = None
                self._connect_kwargs = None
                self._session_deadline = None

    def __repr__(self) -> str:
        return "PsycopgMigrationSqlClient(credential='<redacted>')"


_CANONICAL_RUNNER: ModuleType | None = None
_CANONICAL_RUNNER_LOCK = threading.Lock()


def load_canonical_migration_runner() -> ModuleType:
    """Load only the fixed in-repository audited runner, never an env path."""

    global _CANONICAL_RUNNER
    with _CANONICAL_RUNNER_LOCK:
        if _CANONICAL_RUNNER is not None:
            return _CANONICAL_RUNNER
        path = CANONICAL_RUNNER_PATH.resolve(strict=True)
        if (
            path != CANONICAL_RUNNER_PATH
            or not path.is_file()
            or path.is_symlink()
            or path.parent != (REPOSITORY_ROOT / "scripts").resolve(strict=True)
        ):
            raise RuntimeMigrationError(
                MigrationState.MIGRATION_METADATA_INVALID,
                "CANONICAL_MIGRATION_RUNNER_INVALID",
            )
        spec = importlib.util.spec_from_file_location(
            CANONICAL_RUNNER_MODULE_NAME,
            path,
        )
        if spec is None or spec.loader is None:
            raise RuntimeMigrationError(
                MigrationState.MIGRATION_METADATA_INVALID,
                "CANONICAL_MIGRATION_RUNNER_INVALID",
            )
        module = importlib.util.module_from_spec(spec)
        sys.modules[CANONICAL_RUNNER_MODULE_NAME] = module
        try:
            spec.loader.exec_module(module)
        except Exception:
            sys.modules.pop(CANONICAL_RUNNER_MODULE_NAME, None)
            raise RuntimeMigrationError(
                MigrationState.MIGRATION_METADATA_INVALID,
                "CANONICAL_MIGRATION_RUNNER_INVALID",
            ) from None
        if (
            getattr(module, "PINNED_VERSION", None) != PINNED_COCKROACHDB_VERSION
            or getattr(module, "R4_RUNTIME_MIGRATION_ID", None)
            != LATEST_MIGRATION_ID
        ):
            raise RuntimeMigrationError(
                MigrationState.MIGRATION_METADATA_INVALID,
                "CANONICAL_MIGRATION_IDENTITY_MISMATCH",
            )
        _CANONICAL_RUNNER = module
        return module


class CanonicalMigrationCoordinator:
    """Inspect, apply, replay, and re-inspect one exact manifest chain."""

    def __init__(
        self,
        *,
        client: object,
        database: str,
        timeout_seconds: int = 60,
        runner: CanonicalMigrationRunner | None = None,
    ) -> None:
        if not isinstance(database, str) or not database:
            raise RuntimeMigrationError(
                MigrationState.MIGRATION_METADATA_INVALID,
                "MIGRATION_DATABASE_IDENTITY_INVALID",
            )
        if (
            not isinstance(timeout_seconds, int)
            or isinstance(timeout_seconds, bool)
            or not 1 <= timeout_seconds <= 300
        ):
            raise RuntimeMigrationError(
                MigrationState.MIGRATION_METADATA_INVALID,
                "MIGRATION_TIMEOUT_INVALID",
            )
        self._client = client
        self._database = database
        self._timeout_seconds = timeout_seconds
        self._runner = runner or load_canonical_migration_runner()

    def _metadata(self) -> tuple[tuple[object, ...], dict[str, str]]:
        try:
            migrations = tuple(self._runner.load_migrations())
        except RuntimeMigrationError:
            raise
        except Exception:
            raise RuntimeMigrationError(
                MigrationState.MIGRATION_METADATA_INVALID,
                "CANONICAL_MIGRATION_MANIFEST_INVALID",
            ) from None
        if len(migrations) != CANONICAL_MIGRATION_COUNT:
            raise RuntimeMigrationError(
                MigrationState.MIGRATION_METADATA_INVALID,
                "CANONICAL_MIGRATION_COUNT_MISMATCH",
            )
        expected: dict[str, str] = {}
        for migration in migrations:
            migration_id = getattr(migration, "migration_id", None)
            checksum = getattr(migration, "sha256", None)
            if (
                not isinstance(migration_id, str)
                or not isinstance(checksum, str)
                or migration_id in expected
            ):
                raise RuntimeMigrationError(
                    MigrationState.MIGRATION_METADATA_INVALID,
                    "CANONICAL_MIGRATION_MANIFEST_INVALID",
                )
            expected[migration_id] = checksum
        if tuple(expected)[-1] != LATEST_MIGRATION_ID:
            raise RuntimeMigrationError(
                MigrationState.MIGRATION_METADATA_INVALID,
                "CANONICAL_MIGRATION_LATEST_MISMATCH",
            )
        return migrations, expected

    def inspect(self) -> MigrationInspection:
        migrations, expected = self._metadata()
        try:
            raw_applied = self._runner.applied_migrations(
                self._client,
                self._database,
            )
            applied = dict(raw_applied)
        except RuntimeMigrationError:
            raise
        except Exception:
            raise RuntimeMigrationError(
                MigrationState.MIGRATION_FAILED,
                "MIGRATION_STATE_INSPECTION_FAILED",
            ) from None
        unknown = set(applied).difference(expected)
        if unknown:
            raise RuntimeMigrationError(
                MigrationState.SCHEMA_AHEAD_OR_UNKNOWN,
                "SCHEMA_CONTAINS_UNKNOWN_MIGRATION",
            )
        for migration_id, checksum in applied.items():
            if expected.get(migration_id) != checksum:
                raise RuntimeMigrationError(
                    MigrationState.MIGRATION_METADATA_INVALID,
                    "APPLIED_MIGRATION_CHECKSUM_MISMATCH",
                )
        expected_ids = tuple(expected)
        applied_ids = tuple(
            migration_id for migration_id in expected_ids if migration_id in applied
        )
        if applied_ids != expected_ids[: len(applied_ids)]:
            raise RuntimeMigrationError(
                MigrationState.SCHEMA_AHEAD_OR_UNKNOWN,
                "MIGRATION_LEDGER_IS_NOT_A_PREFIX",
            )
        state = (
            MigrationState.UP_TO_DATE
            if len(applied_ids) == len(migrations)
            else MigrationState.MIGRATIONS_REQUIRED
        )
        return MigrationInspection(
            state=state,
            discovered=len(migrations),
            applied=len(applied_ids),
            pending=len(migrations) - len(applied_ids),
            latest_migration_id=expected_ids[-1],
        )

    def prepare(self) -> MigrationExecutionSummary:
        initial = self.inspect()
        first_apply: Mapping[str, object]
        try:
            first_apply = self._runner.apply_migrations(
                self._client,
                self._database,
                timeout=float(self._timeout_seconds),
            )
            replay = self._runner.apply_migrations(
                self._client,
                self._database,
                timeout=float(self._timeout_seconds),
            )
        except RuntimeMigrationError:
            raise
        except Exception:
            raise RuntimeMigrationError(
                MigrationState.MIGRATION_FAILED,
                "CANONICAL_MIGRATION_APPLICATION_FAILED",
            ) from None
        final = self.inspect()
        applied_count = first_apply.get("applied_count")
        replay_skipped = replay.get("skipped_count")
        if (
            final.state is not MigrationState.UP_TO_DATE
            or not isinstance(applied_count, int)
            or isinstance(applied_count, bool)
            or applied_count != initial.pending
            or not isinstance(replay_skipped, int)
            or isinstance(replay_skipped, bool)
            or replay_skipped != final.discovered
        ):
            raise RuntimeMigrationError(
                MigrationState.MIGRATION_FAILED,
                "CANONICAL_MIGRATION_REPLAY_MISMATCH",
            )
        return MigrationExecutionSummary(
            initial_state=initial.state,
            final_state=final.state,
            discovered=final.discovered,
            applied=applied_count,
            replay_skipped=replay_skipped,
            failures=0,
            latest_migration_id=final.latest_migration_id,
        )


def cockroach_server_version_is_pinned(version_output: str) -> bool:
    if not isinstance(version_output, str):
        return False
    match = _COCKROACH_VERSION_PATTERN.search(version_output)
    return bool(match and f"v{match.group('version')}" == PINNED_COCKROACHDB_VERSION)


__all__ = [
    "CANONICAL_MIGRATION_COUNT",
    "CANONICAL_RUNNER_PATH",
    "CanonicalMigrationCoordinator",
    "LATEST_MIGRATION_ID",
    "MigrationExecutionSummary",
    "MigrationInspection",
    "MigrationState",
    "PINNED_COCKROACHDB_VERSION",
    "PsycopgMigrationSqlClient",
    "RuntimeMigrationError",
    "cockroach_server_version_is_pinned",
    "load_canonical_migration_runner",
]
