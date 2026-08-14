"""Focused R3 configuration, migration, pool and lifecycle tests."""

from __future__ import annotations

import asyncio
import re
import threading
import time
import types
import unittest

from tests._support import REPOSITORY_ROOT  # noqa: F401

from aioa_memory_kernel.demo_runtime.composition import (
    STARTUP_ORDER,
    CockroachBoundRuntimeDependencyFactory,
    RuntimeDependencies,
    RuntimeStartupRecorder,
    RuntimeStartupStage,
    SessionStorageClass,
)
from aioa_memory_kernel.demo_runtime.config import (
    DATABASE_ALLOW_INSECURE_LOCAL_ENV,
    JUDGE_ALLOWED_SUBJECTS_ENV,
    OIDC_CLIENT_ID_ENV,
    OIDC_ISSUER_ENV,
    PUBLIC_ORIGIN_ENV,
    RUNTIME_MODE_ENV,
    RuntimeAssemblyError,
    RuntimeErrorCode,
    RuntimeMode,
    RuntimeSettings,
)
from aioa_memory_kernel.demo_runtime.database import (
    ApplicationDatabaseAuthoritySummary,
    CockroachRuntimeDatabaseFactory,
    EXPECTED_RLS_FORCE_TABLE_COUNT,
    EXPECTED_SCHEMA_TABLE_COUNT,
    RuntimeDatabaseResources,
    validate_application_database_authority,
)
from aioa_memory_kernel.persistence.errors import PersistenceConfigurationError
from aioa_memory_kernel.persistence.psycopg_pool import (
    PsycopgApplicationPool,
    PsycopgPoolConfiguration,
)
from aioa_memory_kernel.persistence.runtime_migrations import (
    CANONICAL_MIGRATION_COUNT,
    LATEST_MIGRATION_ID,
    CanonicalMigrationCoordinator,
    MigrationExecutionSummary,
    MigrationState,
    PsycopgMigrationSqlClient,
    RuntimeMigrationError,
    _split_canonical_sql_statements,
    load_canonical_migration_runner,
)
from aioa_memory_kernel.security.credentials import (
    CredentialPurpose,
    SecretValue,
)


def _database_urls(*, sslmode: str = "verify-full", host: str = "db.example.invalid"):
    return {
        "DATABASE_URL_APP": (
            f"postgresql://runtime-app:synthetic-app@{host}/memory_patch"
            f"?sslmode={sslmode}"
        ),
        "DATABASE_URL_MIGRATOR": (
            f"postgresql://runtime-migrator:synthetic-migrator@{host}/"
            f"memory_patch?sslmode={sslmode}"
        ),
    }


def _environment(
    mode: RuntimeMode = RuntimeMode.HOSTED_DEMO,
    *,
    sslmode: str = "verify-full",
    host: str = "db.example.invalid",
) -> dict[str, str]:
    values = {
        RUNTIME_MODE_ENV: mode.value,
        OIDC_ISSUER_ENV: "https://identity.test",
        OIDC_CLIENT_ID_ENV: "runtime-r3-test",
        PUBLIC_ORIGIN_ENV: "https://runtime.test",
        JUDGE_ALLOWED_SUBJECTS_ENV: "memory-patch-test-subject",
    }
    values.update(_database_urls(sslmode=sslmode, host=host))
    return values


def _application_secret(value: str = "application-secret-sentinel") -> SecretValue:
    return SecretValue(
        value,
        purpose=CredentialPurpose.APPLICATION_DATABASE,
        source_name="DATABASE_URL_APP",
    )


class RuntimeDatabaseConfigurationTests(unittest.TestCase):
    def test_valid_hosted_configuration_is_tls_verified_and_step40_bounded(self):
        settings = RuntimeSettings.from_mapping(_environment())
        database = settings.require_database()
        self.assertEqual(database.application.sslmode, "verify-full")
        self.assertEqual(database.migration.sslmode, "verify-full")
        self.assertEqual((database.pool_minimum, database.pool_maximum), (1, 4))
        self.assertEqual(database.migration_timeout_seconds, 300)
        self.assertEqual(database.database_identity[1], 26257)

    def test_missing_host_is_rejected(self):
        values = _environment()
        values["DATABASE_URL_APP"] = (
            "postgresql://runtime-app:synthetic-app@/memory_patch"
            "?sslmode=verify-full"
        )
        with self.assertRaises(RuntimeAssemblyError) as raised:
            RuntimeSettings.from_mapping(values)
        self.assertEqual(raised.exception.code, RuntimeErrorCode.DATABASE_CONFIG_INVALID)

    def test_missing_each_purpose_specific_credential_is_rejected(self):
        for missing in ("DATABASE_URL_APP", "DATABASE_URL_MIGRATOR"):
            with self.subTest(missing=missing):
                values = _environment()
                values.pop(missing)
                with self.assertRaises(RuntimeAssemblyError) as raised:
                    RuntimeSettings.from_mapping(values)
                self.assertEqual(
                    raised.exception.code,
                    RuntimeErrorCode.DATABASE_CREDENTIAL_MISSING,
                )

    def test_hosted_disable_require_and_local_override_are_fail_closed(self):
        for sslmode in ("disable", "require", "verify-ca"):
            with self.subTest(sslmode=sslmode), self.assertRaises(
                RuntimeAssemblyError
            ) as raised:
                RuntimeSettings.from_mapping(_environment(sslmode=sslmode))
            self.assertEqual(raised.exception.code, RuntimeErrorCode.DATABASE_TLS_REQUIRED)

        local = _environment(
            RuntimeMode.LOCAL_DEMO,
            sslmode="disable",
            host="127.0.0.1",
        )
        with self.assertRaises(RuntimeAssemblyError):
            RuntimeSettings.from_mapping(local)
        local[DATABASE_ALLOW_INSECURE_LOCAL_ENV] = "1"
        settings = RuntimeSettings.from_mapping(local)
        self.assertTrue(settings.require_database().local_insecure_disposable)

        hosted = _environment()
        hosted[DATABASE_ALLOW_INSECURE_LOCAL_ENV] = "1"
        with self.assertRaises(RuntimeAssemblyError) as raised:
            RuntimeSettings.from_mapping(hosted)
        self.assertEqual(raised.exception.code, RuntimeErrorCode.DATABASE_TLS_REQUIRED)

    def test_credentials_and_principals_must_be_distinct(self):
        values = _environment()
        values["DATABASE_URL_MIGRATOR"] = values["DATABASE_URL_APP"]
        with self.assertRaises(RuntimeAssemblyError) as raised:
            RuntimeSettings.from_mapping(values)
        self.assertEqual(raised.exception.code, RuntimeErrorCode.DATABASE_CONFIG_INVALID)

    def test_endpoint_targets_must_match(self):
        values = _environment()
        values["DATABASE_URL_MIGRATOR"] = (
            "postgresql://runtime-migrator:synthetic-migrator@other.example.invalid/"
            "memory_patch?sslmode=verify-full"
        )
        with self.assertRaises(RuntimeAssemblyError) as raised:
            RuntimeSettings.from_mapping(values)
        self.assertEqual(raised.exception.code, RuntimeErrorCode.DATABASE_CONFIG_INVALID)

    def test_step40_pool_ceiling_cannot_be_overridden(self):
        values = _environment()
        values["AIOA_DB_POOL_MAX"] = "5"
        with self.assertRaises(RuntimeAssemblyError):
            RuntimeSettings.from_mapping(values)

    def test_secret_values_are_absent_from_repr_and_errors(self):
        values = _environment()
        sentinel = "r3-secret-must-never-render"
        values["DATABASE_URL_APP"] = (
            f"postgresql://runtime-app:{sentinel}@db.example.invalid/"
            "memory_patch?sslmode=verify-full"
        )
        settings = RuntimeSettings.from_mapping(values)
        rendered = repr(settings.require_database()) + repr(
            settings.require_database().application
        )
        self.assertNotIn(sentinel, rendered)
        self.assertIn("<redacted>", rendered)


class _FakePhysicalConnection:
    def __init__(self, rows: dict[str, dict[str, object]] | None = None) -> None:
        self.rows = rows or {}
        self.rollback_count = 0
        self.commit_count = 0

    def cursor(self):
        return _FakeCursor(self.rows)

    def rollback(self):
        self.rollback_count += 1

    def commit(self):
        self.commit_count += 1


class _FakeCursor:
    def __init__(self, rows: dict[str, dict[str, object]]) -> None:
        self.rows = rows
        self.current: dict[str, object] | None = None
        self.closed = False

    def execute(self, sql, parameters=None):
        if "current_database()" in sql:
            self.current = self.rows.get("identity")
        elif "relrowsecurity" in sql:
            self.current = self.rows.get("catalog")
        elif "forbidden_role_memberships" in sql:
            self.current = self.rows.get("forbidden")
        elif "has_schema_privilege" in sql:
            self.current = self.rows.get("privilege")
        else:
            self.current = {}

    def fetchone(self):
        return self.current

    def fetchall(self):
        return []

    def close(self):
        self.closed = True


class _FakePoolBackend:
    last = None

    def __init__(self, **kwargs) -> None:
        type(self).last = self
        self.kwargs = kwargs
        self.connection = _FakePhysicalConnection()
        self.open_count = 0
        self.close_count = 0
        self.put_count = 0

    def open(self, *, wait, timeout):
        self.open_count += 1

    def getconn(self, *, timeout):
        return self.connection

    def putconn(self, connection):
        self.put_count += 1

    def close(self):
        self.close_count += 1


class PsycopgPoolAdapterTests(unittest.TestCase):
    def _pool(self) -> PsycopgApplicationPool:
        return PsycopgApplicationPool(
            PsycopgPoolConfiguration(credential=_application_secret()),
            pool_class=_FakePoolBackend,
            dict_row_factory=object(),
        )

    def test_pool_is_exactly_step40_bounded_and_lease_returns_once(self):
        pool = self._pool().open()
        backend = _FakePoolBackend.last
        self.assertEqual(backend.kwargs["min_size"], 1)
        self.assertEqual(backend.kwargs["max_size"], 4)
        self.assertEqual(backend.kwargs["num_workers"], 1)
        self.assertEqual(backend.kwargs["reconnect_timeout"], 5.0)
        self.assertIs(backend.kwargs["kwargs"]["autocommit"], True)
        lease = pool.connection_factory()()
        lease.close()
        lease.close()
        self.assertEqual(backend.put_count, 1)
        pool.close()
        pool.close()
        self.assertEqual(backend.close_count, 1)

    def test_transaction_runner_is_application_purpose_only(self):
        pool = self._pool().open()
        self.assertIs(
            pool.transaction_runner().credential_purpose,
            CredentialPurpose.APPLICATION_DATABASE,
        )
        pool.close()

    def test_wrong_purpose_and_broader_pool_are_rejected(self):
        migrator = SecretValue(
            "migration-secret-sentinel",
            purpose=CredentialPurpose.MIGRATION_DATABASE,
            source_name="DATABASE_URL_MIGRATOR",
        )
        with self.assertRaises(PersistenceConfigurationError):
            PsycopgPoolConfiguration(credential=migrator)
        with self.assertRaises(PersistenceConfigurationError):
            PsycopgPoolConfiguration(
                credential=_application_secret(),
                maximum_size=5,
            )

    def test_pool_repr_never_contains_connection_value(self):
        sentinel = "pool-secret-never-render"
        pool = PsycopgApplicationPool(
            PsycopgPoolConfiguration(credential=_application_secret(sentinel)),
            pool_class=_FakePoolBackend,
            dict_row_factory=object(),
        )
        self.assertNotIn(sentinel, repr(pool))


class _TransportCursor:
    def __init__(self, *, query_cursor: bool) -> None:
        self.description = (
            (
                types.SimpleNamespace(name="enabled"),
                types.SimpleNamespace(name="disabled"),
            )
            if query_cursor
            else None
        )

    def execute(self, sql, parameters=None, **kwargs):
        return None

    def fetchall(self):
        return ({"enabled": True, "disabled": False},)

    def close(self):
        return None


class _TransportConnection:
    def __init__(self) -> None:
        self.cursor_count = 0
        self.close_count = 0

    def cursor(self):
        self.cursor_count += 1
        return _TransportCursor(query_cursor=self.cursor_count == 2)

    def close(self):
        self.close_count += 1

    def cancel(self):
        return None


class _TimeoutCursor(_TransportCursor):
    def __init__(self, connection, *, query_cursor: bool) -> None:
        super().__init__(query_cursor=query_cursor)
        self._connection = connection

    def execute(self, sql, parameters=None, **kwargs):
        if self.description is not None:
            self._connection.cancelled.wait(timeout=1)


class _TimeoutConnection(_TransportConnection):
    def __init__(self) -> None:
        super().__init__()
        self.cancelled = threading.Event()
        self.cancel_count = 0
        self.cancel_timeout = None

    def cursor(self):
        self.cursor_count += 1
        return _TimeoutCursor(self, query_cursor=self.cursor_count == 2)

    def cancel(self):
        self.cancel_count += 1
        self.cancelled.set()

    def cancel_safe(self, *, timeout):
        self.cancel_timeout = timeout
        self.cancel()


class _RecordingCursor:
    def __init__(self, statements: list[str]) -> None:
        self._statements = statements
        self.description = None

    def execute(self, sql, parameters=None, **kwargs):
        self._statements.append(sql)

    def close(self):
        return None


class _RecordingConnection(_TransportConnection):
    def __init__(self) -> None:
        super().__init__()
        self.statements: list[str] = []

    def cursor(self):
        return _RecordingCursor(self.statements)


class PsycopgMigrationTransportTests(unittest.TestCase):
    def test_trusted_sql_splitter_preserves_quotes_functions_and_transaction(self):
        sql = """BEGIN;
CREATE FUNCTION memory_patch.synthetic() RETURNS STRING LANGUAGE SQL AS $$
  SELECT 'body;value'
$$;
-- a comment; is not a statement
SELECT 'quoted;''value';
COMMIT;
"""
        statements = _split_canonical_sql_statements(sql)
        self.assertEqual(len(statements), 4)
        self.assertEqual(statements[0], "BEGIN")
        self.assertIn("SELECT 'body;value'", statements[1])
        self.assertIn("SELECT 'quoted;''value'", statements[2])
        self.assertEqual(statements[3], "COMMIT")
        with self.assertRaises(RuntimeMigrationError) as raised:
            _split_canonical_sql_statements("SELECT $$unterminated")
        self.assertEqual(
            raised.exception.sanitized_code,
            "MIGRATION_SQL_TOKEN_UNTERMINATED",
        )

    def test_multi_statement_execution_reuses_established_autocommit_policy(self):
        connection = _RecordingConnection()
        credential = SecretValue(
            "postgresql://migrator:synthetic@db.invalid/memory_patch"
            "?sslmode=verify-full",
            purpose=CredentialPurpose.MIGRATION_DATABASE,
            source_name="DATABASE_URL_MIGRATOR",
        )
        client = PsycopgMigrationSqlClient(
            credential=credential,
            database="memory_patch",
            connection_timeout_seconds=5,
            statement_timeout_seconds=60,
            connect=lambda *args, **kwargs: connection,
        ).open()
        self.assertEqual(
            client.execute(
                "memory_patch",
                "BEGIN; SELECT 'inside;transaction'; COMMIT;",
            ),
            "",
        )
        self.assertEqual(
            connection.statements[1:],
            ["SELECT 'inside;transaction'"],
        )
        self.assertEqual(connection.close_count, 1)
        client.close()

    def test_boolean_tsv_matches_the_canonical_cli_contract(self):
        connections: list[_TransportConnection] = []

        def connect(*args, **kwargs):
            connection = _TransportConnection()
            connections.append(connection)
            return connection

        credential = SecretValue(
            "postgresql://migrator:synthetic@db.invalid/memory_patch"
            "?sslmode=verify-full",
            purpose=CredentialPurpose.MIGRATION_DATABASE,
            source_name="DATABASE_URL_MIGRATOR",
        )
        client = PsycopgMigrationSqlClient(
            credential=credential,
            database="memory_patch",
            connection_timeout_seconds=5,
            statement_timeout_seconds=60,
            connect=connect,
        ).open()
        self.assertEqual(
            client.execute("defaultdb", "SELECT true, false"),
            "enabled\tdisabled\nt\tf\n",
        )
        self.assertEqual(
            client.execute("memory_patch", "SELECT true, false"),
            "enabled\tdisabled\nt\tf\n",
        )
        self.assertEqual(len(connections), 2)
        self.assertTrue(all(item.close_count == 1 for item in connections))
        with self.assertRaises(RuntimeMigrationError) as raised:
            client.execute("unrelated_database", "SELECT true")
        self.assertEqual(
            raised.exception.sanitized_code,
            "MIGRATION_DATABASE_TARGET_MISMATCH",
        )
        client.close()

    def test_client_watchdog_cancels_one_bounded_statement(self):
        connection = _TimeoutConnection()
        credential = SecretValue(
            "postgresql://migrator:synthetic@db.invalid/memory_patch"
            "?sslmode=verify-full",
            purpose=CredentialPurpose.MIGRATION_DATABASE,
            source_name="DATABASE_URL_MIGRATOR",
        )
        client = PsycopgMigrationSqlClient(
            credential=credential,
            database="memory_patch",
            connection_timeout_seconds=5,
            statement_timeout_seconds=60,
            connect=lambda *args, **kwargs: connection,
        ).open()
        started = time.monotonic()
        with self.assertRaises(RuntimeMigrationError) as raised:
            client.execute("memory_patch", "SELECT pg_sleep(60)", timeout=0.01)
        self.assertEqual(raised.exception.sanitized_code, "MIGRATION_OPERATION_TIMEOUT")
        self.assertEqual(connection.cancel_count, 1)
        self.assertEqual(connection.cancel_timeout, 5.0)
        self.assertLess(time.monotonic() - started, 0.5)
        client.close()

    def test_whole_migration_session_deadline_fails_closed(self):
        connection = _RecordingConnection()
        credential = SecretValue(
            "postgresql://migrator:synthetic@db.invalid/memory_patch"
            "?sslmode=verify-full",
            purpose=CredentialPurpose.MIGRATION_DATABASE,
            source_name="DATABASE_URL_MIGRATOR",
        )
        client = PsycopgMigrationSqlClient(
            credential=credential,
            database="memory_patch",
            connection_timeout_seconds=5,
            statement_timeout_seconds=60,
            connect=lambda *args, **kwargs: connection,
        ).open()
        client._session_deadline = time.monotonic() - 1
        with self.assertRaises(RuntimeMigrationError) as raised:
            client.execute("memory_patch", "SELECT 1")
        self.assertEqual(
            raised.exception.sanitized_code,
            "MIGRATION_SESSION_TIMEOUT",
        )
        self.assertEqual(connection.close_count, 1)
        client.close()


def _migrations() -> tuple[object, ...]:
    rows = [
        types.SimpleNamespace(migration_id=f"{index:04d}_synthetic", sha256=f"sha-{index}")
        for index in range(1, CANONICAL_MIGRATION_COUNT)
    ]
    rows.append(
        types.SimpleNamespace(
            migration_id=LATEST_MIGRATION_ID,
            sha256="sha-latest",
        )
    )
    return tuple(rows)


class _FakeRunner:
    def __init__(self, ledger=None, *, fail_apply=False, invalid_manifest=False):
        self.migrations = _migrations()
        self.ledger = dict(ledger or {})
        self.fail_apply = fail_apply
        self.invalid_manifest = invalid_manifest
        self.apply_calls = 0

    def load_migrations(self):
        if self.invalid_manifest:
            raise ValueError("synthetic manifest failure")
        return self.migrations

    def applied_migrations(self, client, database):
        return dict(self.ledger)

    def apply_migrations(self, client, database, *, timeout):
        self.apply_calls += 1
        if self.fail_apply:
            raise ValueError("synthetic migration failure")
        applied = []
        skipped = []
        for migration in self.migrations:
            if migration.migration_id in self.ledger:
                skipped.append(migration.migration_id)
            else:
                self.ledger[migration.migration_id] = migration.sha256
                applied.append(migration.migration_id)
        return {
            "applied_count": len(applied),
            "skipped_count": len(skipped),
        }


class CanonicalMigrationCoordinatorTests(unittest.TestCase):
    def test_actual_canonical_runner_identity_is_reused(self):
        runner = load_canonical_migration_runner()
        migrations = runner.load_migrations()
        self.assertEqual(len(migrations), 19)
        self.assertEqual(migrations[-1].migration_id, LATEST_MIGRATION_ID)

    def test_runtime_catalog_gate_covers_the_complete_frozen_chain(self):
        migrations = load_canonical_migration_runner().load_migrations()
        sql = "\n".join(migration.sql for migration in migrations)
        tables = set(
            re.findall(
                r"^CREATE TABLE(?: IF NOT EXISTS)? memory_patch\.([a-z0-9_]+)",
                sql,
                re.MULTILINE,
            )
        )
        rls_enabled = set(
            re.findall(
                r"ALTER TABLE memory_patch\.([a-z0-9_]+)\s+"
                r"ENABLE ROW LEVEL SECURITY;",
                sql,
            )
        )
        force_rls = set(
            re.findall(
                r"ALTER TABLE memory_patch\.([a-z0-9_]+)\s+"
                r"FORCE ROW LEVEL SECURITY;",
                sql,
            )
        )
        self.assertEqual(len(tables), EXPECTED_SCHEMA_TABLE_COUNT)
        self.assertEqual(rls_enabled, force_rls)
        self.assertEqual(len(rls_enabled), EXPECTED_RLS_FORCE_TABLE_COUNT)

    def test_pending_chain_applies_in_order_and_replay_skips_all(self):
        runner = _FakeRunner()
        coordinator = CanonicalMigrationCoordinator(
            client=object(),
            database="memory_patch",
            runner=runner,
        )
        summary = coordinator.prepare()
        self.assertIs(summary.initial_state, MigrationState.MIGRATIONS_REQUIRED)
        self.assertEqual(summary.applied, 19)
        self.assertEqual(summary.replay_skipped, 19)
        self.assertEqual(summary.failures, 0)
        self.assertEqual(runner.apply_calls, 2)

    def test_up_to_date_chain_is_idempotent(self):
        runner = _FakeRunner()
        runner.ledger = {
            migration.migration_id: migration.sha256 for migration in runner.migrations
        }
        summary = CanonicalMigrationCoordinator(
            client=object(), database="memory_patch", runner=runner
        ).prepare()
        self.assertIs(summary.initial_state, MigrationState.UP_TO_DATE)
        self.assertEqual(summary.applied, 0)
        self.assertEqual(summary.replay_skipped, 19)

    def test_checksum_unknown_and_gap_states_fail_closed(self):
        base = _FakeRunner()
        first, second = base.migrations[:2]
        cases = (
            (
                {first.migration_id: "wrong"},
                MigrationState.MIGRATION_METADATA_INVALID,
            ),
            ({"9999_unknown": "sha"}, MigrationState.SCHEMA_AHEAD_OR_UNKNOWN),
            (
                {second.migration_id: second.sha256},
                MigrationState.SCHEMA_AHEAD_OR_UNKNOWN,
            ),
        )
        for ledger, state in cases:
            with self.subTest(state=state), self.assertRaises(
                RuntimeMigrationError
            ) as raised:
                CanonicalMigrationCoordinator(
                    client=object(),
                    database="memory_patch",
                    runner=_FakeRunner(ledger),
                ).inspect()
            self.assertIs(raised.exception.state, state)

    def test_manifest_and_application_failure_are_sanitized(self):
        with self.assertRaises(RuntimeMigrationError) as manifest:
            CanonicalMigrationCoordinator(
                client=object(),
                database="memory_patch",
                runner=_FakeRunner(invalid_manifest=True),
            ).inspect()
        self.assertIs(manifest.exception.state, MigrationState.MIGRATION_METADATA_INVALID)
        with self.assertRaises(RuntimeMigrationError) as apply:
            CanonicalMigrationCoordinator(
                client=object(),
                database="memory_patch",
                runner=_FakeRunner(fail_apply=True),
            ).prepare()
        self.assertIs(apply.exception.state, MigrationState.MIGRATION_FAILED)
        self.assertNotIn("synthetic migration failure", str(apply.exception))


def _authority_summary() -> ApplicationDatabaseAuthoritySummary:
    return ApplicationDatabaseAuthoritySummary(
        server_version="v26.2.5",
        table_count=EXPECTED_SCHEMA_TABLE_COUNT,
        rls_enabled_count=EXPECTED_RLS_FORCE_TABLE_COUNT,
        force_rls_count=EXPECTED_RLS_FORCE_TABLE_COUNT,
        application_role_member=True,
        request_context_role_member=True,
        bypass_rls=False,
        migration_privilege=False,
        forbidden_role_memberships=0,
    )


class _FakeMigrationClient:
    def __init__(self, log, **kwargs):
        self.log = log
        self.kwargs = kwargs

    def open(self):
        self.log.append("migration-open")

    def close(self):
        self.log.append("migration-close")


class _FakeCoordinator:
    def __init__(self, log, **kwargs):
        self.log = log

    def prepare(self):
        self.log.append("migration-prepare")
        return MigrationExecutionSummary(
            initial_state=MigrationState.MIGRATIONS_REQUIRED,
            final_state=MigrationState.UP_TO_DATE,
            discovered=19,
            applied=19,
            replay_skipped=19,
            failures=0,
            latest_migration_id=LATEST_MIGRATION_ID,
        )


class RuntimeDatabaseFactoryTests(unittest.TestCase):
    def _settings(self):
        values = _environment(
            RuntimeMode.TEST,
            sslmode="disable",
            host="127.0.0.1",
        )
        values[DATABASE_ALLOW_INSECURE_LOCAL_ENV] = "1"
        return RuntimeSettings.from_mapping(values)

    def test_migrator_closes_before_normal_pool_and_only_app_pool_survives(self):
        log: list[str] = []

        def migration_client_factory(**kwargs):
            return _FakeMigrationClient(log, **kwargs)

        def coordinator_factory(**kwargs):
            return _FakeCoordinator(log, **kwargs)

        def pool_factory(configuration):
            pool = PsycopgApplicationPool(
                configuration,
                pool_class=_FakePoolBackend,
                dict_row_factory=object(),
            )
            original_open = pool.open

            def open_with_log():
                log.append("application-pool-open")
                return original_open()

            pool.open = open_with_log  # type: ignore[method-assign]
            return pool

        factory = CockroachRuntimeDatabaseFactory(
            migration_client_factory=migration_client_factory,
            migration_coordinator_factory=coordinator_factory,
            application_pool_factory=pool_factory,
            authority_validator=lambda pool, database: _authority_summary(),
        )
        resources = asyncio.run(factory.initialize(self._settings()))
        self.assertIsInstance(resources, RuntimeDatabaseResources)
        self.assertTrue(resources.ready)
        self.assertEqual(
            log,
            [
                "migration-open",
                "migration-prepare",
                "migration-close",
                "application-pool-open",
            ],
        )
        self.assertIs(
            resources.transaction_runner.credential_purpose,
            CredentialPurpose.APPLICATION_DATABASE,
        )
        resources.close()
        asyncio.run(factory.cleanup_partial())

    def test_application_authority_probe_accepts_only_exact_role_and_rls_state(self):
        settings = self._settings()
        pool = PsycopgApplicationPool(
            PsycopgPoolConfiguration(
                credential=settings.require_database().application.credential
            ),
            pool_class=_FakePoolBackend,
            dict_row_factory=object(),
        ).open()
        backend = _FakePoolBackend.last
        backend.connection.rows = {
            "identity": {
                "database_name": "memory_patch",
                "server_version": "CockroachDB CCL v26.2.5 (synthetic test)",
                "app_member": True,
                "context_member": True,
                "bypass_rls": False,
                "administrative_role": False,
            },
            "catalog": {
                "table_count": EXPECTED_SCHEMA_TABLE_COUNT,
                "rls_enabled_count": EXPECTED_RLS_FORCE_TABLE_COUNT,
                "force_rls_count": EXPECTED_RLS_FORCE_TABLE_COUNT,
            },
            "forbidden": {"forbidden_role_memberships": 0},
            "privilege": {"migration_privilege": False},
        }
        result = validate_application_database_authority(
            pool, settings.require_database()
        )
        self.assertFalse(result.bypass_rls)
        self.assertFalse(result.migration_privilege)
        self.assertEqual(backend.connection.rollback_count, 1)
        pool.close()

    def test_application_authority_probe_rejects_bypassrls(self):
        with self.assertRaises(RuntimeAssemblyError):
            ApplicationDatabaseAuthoritySummary(
                server_version="v26.2.5",
                table_count=EXPECTED_SCHEMA_TABLE_COUNT,
                rls_enabled_count=EXPECTED_RLS_FORCE_TABLE_COUNT,
                force_rls_count=EXPECTED_RLS_FORCE_TABLE_COUNT,
                application_role_member=True,
                request_context_role_member=True,
                bypass_rls=True,
                migration_privilege=False,
                forbidden_role_memberships=0,
            )


class _CloseableDatabase:
    def __init__(self):
        self.close_count = 0

    def close(self):
        self.close_count += 1


class _DatabaseFactory:
    def __init__(self, database):
        self.database = database
        self.cleanup_count = 0

    async def initialize(self, settings):
        return self.database

    async def cleanup_partial(self):
        self.cleanup_count += 1


class _ApplicationFactory:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.cleanup_count = 0

    def validate_availability(self, settings):
        return None

    def initialize_after_database(self, settings, recorder, database):
        if self.fail:
            raise RuntimeError("synthetic downstream failure")
        for stage in (
            RuntimeStartupStage.SESSION_RESOURCES_INITIALIZED,
            RuntimeStartupStage.SERVICE_COMPOSITION_INITIALIZED,
            RuntimeStartupStage.PROVIDER_ADAPTER_INITIALIZED,
            RuntimeStartupStage.RUNTIME_GUARDS_INITIALIZED,
        ):
            recorder.advance(stage)
        return RuntimeDependencies(
            backend=object(),
            oidc_client=object(),
            session_store=object(),
            provider_adapter=object(),
            session_storage_class=SessionStorageClass.TEST_ONLY,
            owned_database_resources=(database,),
        )

    def cleanup_partial(self):
        self.cleanup_count += 1


class CockroachCompositionSequenceTests(unittest.TestCase):
    def test_database_stage_precedes_sessions_services_and_provider(self):
        database = _CloseableDatabase()
        factory = CockroachBoundRuntimeDependencyFactory(
            database_factory=_DatabaseFactory(database),
            application_factory=_ApplicationFactory(),
        )
        recorder = RuntimeStartupRecorder()
        recorder.advance(RuntimeStartupStage.CONFIG_VALIDATED)
        dependencies = asyncio.run(
            factory.initialize(
                RuntimeSettings.from_mapping({RUNTIME_MODE_ENV: "TEST"}),
                recorder,
            )
        )
        self.assertEqual(recorder.events, STARTUP_ORDER[:-1])
        self.assertIs(dependencies.owned_database_resources[0], database)

    def test_downstream_failure_closes_partial_database(self):
        database = _CloseableDatabase()
        database_factory = _DatabaseFactory(database)
        application_factory = _ApplicationFactory(fail=True)
        factory = CockroachBoundRuntimeDependencyFactory(
            database_factory=database_factory,
            application_factory=application_factory,
        )
        recorder = RuntimeStartupRecorder()
        recorder.advance(RuntimeStartupStage.CONFIG_VALIDATED)
        with self.assertRaises(RuntimeError):
            asyncio.run(
                factory.initialize(
                    RuntimeSettings.from_mapping({RUNTIME_MODE_ENV: "TEST"}),
                    recorder,
                )
            )
        asyncio.run(factory.cleanup_partial())
        self.assertEqual(database.close_count, 1)
        self.assertEqual(application_factory.cleanup_count, 1)
        self.assertEqual(database_factory.cleanup_count, 1)


if __name__ == "__main__":
    unittest.main()
