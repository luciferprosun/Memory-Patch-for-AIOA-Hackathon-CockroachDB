"""R3 CockroachDB composition for the canonical post-roadmap runtime."""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from typing import Callable, Mapping, Protocol

from aioa_memory_kernel.persistence.psycopg_pool import (
    PsycopgApplicationPool,
    PsycopgPoolConfiguration,
)
from aioa_memory_kernel.persistence.runtime_migrations import (
    CanonicalMigrationCoordinator,
    MigrationExecutionSummary,
    MigrationState,
    PINNED_COCKROACHDB_VERSION,
    PsycopgMigrationSqlClient,
    RuntimeMigrationError,
    cockroach_server_version_is_pinned,
)
from aioa_memory_kernel.persistence.transaction import SerializableTransactionRunner

from .config import (
    APPLICATION_DATABASE_ROLE,
    REQUEST_CONTEXT_ROLE,
    RuntimeAssemblyError,
    RuntimeDatabaseSettings,
    RuntimeErrorCode,
    RuntimeSettings,
)


# The Step 4-27 offline migration summary reports 43/40, but the complete
# frozen migration schema also contains the Step 12 registry tables, Step 32-34
# lifecycle/audit/review tables, and the authorized post-roadmap R4 durable
# session table. Runtime admission binds to the complete catalog.
EXPECTED_SCHEMA_TABLE_COUNT = 58
EXPECTED_RLS_FORCE_TABLE_COUNT = 52
FORBIDDEN_APPLICATION_ROLES = (
    "admin",
    "mp_audit_reader",
    "mp_human_reviewer",
    "mp_personal_memory_commit_helper",
    "mp_review_service",
    "mp_schema_owner",
    "mp_security_owner",
    "mp_source_publication_worker",
)


class RuntimeDatabaseFactory(Protocol):
    def initialize(
        self,
        settings: RuntimeSettings,
    ) -> "RuntimeDatabaseResources | object": ...

    def cleanup_partial(self) -> object: ...


@dataclass(frozen=True, slots=True)
class ApplicationDatabaseAuthoritySummary:
    server_version: str
    table_count: int
    rls_enabled_count: int
    force_rls_count: int
    application_role_member: bool
    request_context_role_member: bool
    bypass_rls: bool
    migration_privilege: bool
    forbidden_role_memberships: int

    def __post_init__(self) -> None:
        if (
            self.server_version != PINNED_COCKROACHDB_VERSION
            or self.table_count != EXPECTED_SCHEMA_TABLE_COUNT
            or self.rls_enabled_count != EXPECTED_RLS_FORCE_TABLE_COUNT
            or self.force_rls_count != EXPECTED_RLS_FORCE_TABLE_COUNT
            or not self.application_role_member
            or not self.request_context_role_member
            or self.bypass_rls
            or self.migration_privilege
            or self.forbidden_role_memberships != 0
        ):
            raise RuntimeAssemblyError(RuntimeErrorCode.DATABASE_AUTHORITY_INVALID)


class RuntimeDatabaseResources:
    """Only the normal application pool survives migration preparation."""

    def __init__(
        self,
        *,
        application_pool: PsycopgApplicationPool,
        migration: MigrationExecutionSummary,
        authority: ApplicationDatabaseAuthoritySummary,
    ) -> None:
        if (
            not isinstance(application_pool, PsycopgApplicationPool)
            or not isinstance(migration, MigrationExecutionSummary)
            or not isinstance(authority, ApplicationDatabaseAuthoritySummary)
        ):
            raise RuntimeAssemblyError(RuntimeErrorCode.DATABASE_POOL_FAILED)
        self.application_pool = application_pool
        self.migration = migration
        self.authority = authority
        self.transaction_runner: SerializableTransactionRunner = (
            application_pool.transaction_runner()
        )
        self._closed = False
        self._lock = threading.Lock()

    @property
    def ready(self) -> bool:
        return (
            not self._closed
            and self.migration.final_state is MigrationState.UP_TO_DATE
            and not self.authority.bypass_rls
            and not self.authority.migration_privilege
        )

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self.application_pool.close()

    def __repr__(self) -> str:
        return (
            "RuntimeDatabaseResources(application_credential='<redacted>', "
            f"migration_state={self.migration.final_state.value!r}, "
            f"ready={self.ready})"
        )


def _mapping_row(cursor: object) -> Mapping[str, object]:
    row = cursor.fetchone()  # type: ignore[attr-defined]
    if not isinstance(row, Mapping):
        raise RuntimeAssemblyError(RuntimeErrorCode.DATABASE_AUTHORITY_INVALID)
    return row


def _boolean(row: Mapping[str, object], name: str) -> bool:
    value = row.get(name)
    if not isinstance(value, bool):
        raise RuntimeAssemblyError(RuntimeErrorCode.DATABASE_AUTHORITY_INVALID)
    return value


def validate_application_database_authority(
    pool: PsycopgApplicationPool,
    database: RuntimeDatabaseSettings,
) -> ApplicationDatabaseAuthoritySummary:
    """Read-only proof that the long-lived pool is normal runtime authority."""

    if not isinstance(pool, PsycopgApplicationPool) or not isinstance(
        database, RuntimeDatabaseSettings
    ):
        raise RuntimeAssemblyError(RuntimeErrorCode.DATABASE_AUTHORITY_INVALID)

    def probe(connection: object) -> ApplicationDatabaseAuthoritySummary:
        cursor = None
        try:
            cursor = connection.cursor()  # type: ignore[attr-defined]
            cursor.execute(
                """
                SELECT current_database() AS database_name,
                       version() AS server_version,
                       pg_has_role(current_user, %s, 'MEMBER') AS app_member,
                       pg_has_role(current_user, %s, 'MEMBER') AS context_member,
                       COALESCE((
                         SELECT count(*) > 0
                           FROM pg_catalog.pg_roles
                          WHERE pg_has_role(
                                  current_user, rolname, 'MEMBER'
                                )
                            AND rolbypassrls
                       ), true) AS bypass_rls,
                       COALESCE((
                         SELECT count(*) > 0
                           FROM pg_catalog.pg_roles
                          WHERE pg_has_role(
                                  current_user, rolname, 'MEMBER'
                                )
                            AND (rolcreatedb OR rolcreaterole OR rolsuper)
                       ), true) AS administrative_role
                """,
                (APPLICATION_DATABASE_ROLE, REQUEST_CONTEXT_ROLE),
            )
            identity = _mapping_row(cursor)
            if identity.get("database_name") != database.application.database:
                raise RuntimeAssemblyError(RuntimeErrorCode.DATABASE_AUTHORITY_INVALID)
            version_output = identity.get("server_version")
            if not isinstance(version_output, str) or not cockroach_server_version_is_pinned(
                version_output
            ):
                raise RuntimeAssemblyError(RuntimeErrorCode.DATABASE_AUTHORITY_INVALID)

            cursor.execute(
                """
                SELECT count(*)::INT8 AS table_count,
                       count(*) FILTER (WHERE class.relrowsecurity)::INT8
                         AS rls_enabled_count,
                       count(*) FILTER (WHERE class.relforcerowsecurity)::INT8
                         AS force_rls_count
                  FROM pg_catalog.pg_class AS class
                  JOIN pg_catalog.pg_namespace AS namespace
                    ON namespace.oid = class.relnamespace
                 WHERE namespace.nspname = 'memory_patch'
                   AND class.relkind = 'r'
                """
            )
            catalog = _mapping_row(cursor)

            role_placeholders = ", ".join("%s" for _ in FORBIDDEN_APPLICATION_ROLES)
            cursor.execute(
                "SELECT count(*)::INT8 AS forbidden_role_memberships "
                "FROM unnest(ARRAY[" + role_placeholders
                + "]::STRING[]) AS forbidden(role_name) "
                "WHERE pg_has_role(current_user, role_name, 'MEMBER')",
                FORBIDDEN_APPLICATION_ROLES,
            )
            forbidden = _mapping_row(cursor)

            cursor.execute(
                """
                SELECT has_schema_privilege(
                         current_user, 'memory_patch', 'CREATE'
                       )
                       OR has_table_privilege(
                         current_user,
                         'memory_patch.schema_migrations',
                         'SELECT'
                       )
                       OR has_table_privilege(
                         current_user,
                         'memory_patch.schema_migrations',
                         'INSERT'
                       ) AS migration_privilege
                """
            )
            privilege = _mapping_row(cursor)
            summary = ApplicationDatabaseAuthoritySummary(
                server_version=PINNED_COCKROACHDB_VERSION,
                table_count=int(catalog.get("table_count", -1)),
                rls_enabled_count=int(catalog.get("rls_enabled_count", -1)),
                force_rls_count=int(catalog.get("force_rls_count", -1)),
                application_role_member=_boolean(identity, "app_member"),
                request_context_role_member=_boolean(identity, "context_member"),
                bypass_rls=_boolean(identity, "bypass_rls"),
                migration_privilege=(
                    _boolean(identity, "administrative_role")
                    or _boolean(privilege, "migration_privilege")
                ),
                forbidden_role_memberships=int(
                    forbidden.get("forbidden_role_memberships", -1)
                ),
            )
            return summary
        except RuntimeAssemblyError:
            raise
        except Exception:
            raise RuntimeAssemblyError(
                RuntimeErrorCode.DATABASE_AUTHORITY_INVALID
            ) from None
        finally:
            if cursor is not None:
                try:
                    cursor.close()
                except Exception:
                    pass
            try:
                connection.rollback()  # type: ignore[attr-defined]
            except Exception:
                pass

    return pool.with_connection(probe)


class CockroachRuntimeDatabaseFactory:
    """Migrate with operations authority, close it, then open normal runtime."""

    def __init__(
        self,
        *,
        migration_client_factory: Callable[..., object] = PsycopgMigrationSqlClient,
        migration_coordinator_factory: Callable[..., object] = CanonicalMigrationCoordinator,
        application_pool_factory: Callable[..., PsycopgApplicationPool] = PsycopgApplicationPool,
        authority_validator: Callable[
            [PsycopgApplicationPool, RuntimeDatabaseSettings],
            ApplicationDatabaseAuthoritySummary,
        ] = validate_application_database_authority,
    ) -> None:
        self._migration_client_factory = migration_client_factory
        self._migration_coordinator_factory = migration_coordinator_factory
        self._application_pool_factory = application_pool_factory
        self._authority_validator = authority_validator
        self._partial: RuntimeDatabaseResources | PsycopgApplicationPool | None = None
        self._lock = threading.Lock()

    def _initialize_sync(self, settings: RuntimeSettings) -> RuntimeDatabaseResources:
        if not isinstance(settings, RuntimeSettings):
            raise RuntimeAssemblyError(RuntimeErrorCode.DATABASE_CONFIG_INVALID)
        database = settings.require_database()
        migration_client = self._migration_client_factory(
            credential=database.migration.credential,
            database=database.migration.database,
            connection_timeout_seconds=database.connection_timeout_seconds,
            statement_timeout_seconds=database.migration_timeout_seconds,
        )
        migration_summary: MigrationExecutionSummary
        try:
            migration_client.open()
            coordinator = self._migration_coordinator_factory(
                client=migration_client,
                database=database.migration.database,
                timeout_seconds=database.migration_timeout_seconds,
            )
            migration_summary = coordinator.prepare()
            if not isinstance(migration_summary, MigrationExecutionSummary):
                raise RuntimeAssemblyError(RuntimeErrorCode.DATABASE_MIGRATION_FAILED)
        except RuntimeMigrationError:
            raise RuntimeAssemblyError(RuntimeErrorCode.DATABASE_MIGRATION_FAILED) from None
        except RuntimeAssemblyError:
            raise
        except Exception:
            raise RuntimeAssemblyError(RuntimeErrorCode.DATABASE_MIGRATION_FAILED) from None
        finally:
            try:
                migration_client.close()
            except Exception:
                raise RuntimeAssemblyError(RuntimeErrorCode.DATABASE_MIGRATION_FAILED) from None

        pool = self._application_pool_factory(
            PsycopgPoolConfiguration(
                credential=database.application.credential,
                minimum_size=database.pool_minimum,
                maximum_size=database.pool_maximum,
                acquisition_timeout_seconds=database.acquisition_timeout_seconds,
                connection_timeout_seconds=database.connection_timeout_seconds,
                statement_timeout_seconds=database.statement_timeout_seconds,
                maximum_waiting=settings.profile.queues.review,
            )
        )
        with self._lock:
            self._partial = pool
        try:
            pool.open()
            authority = self._authority_validator(pool, database)
            resources = RuntimeDatabaseResources(
                application_pool=pool,
                migration=migration_summary,
                authority=authority,
            )
        except RuntimeAssemblyError:
            pool.close()
            with self._lock:
                self._partial = None
            raise
        except Exception:
            pool.close()
            with self._lock:
                self._partial = None
            raise RuntimeAssemblyError(RuntimeErrorCode.DATABASE_POOL_FAILED) from None
        with self._lock:
            self._partial = resources
        return resources

    async def initialize(self, settings: RuntimeSettings) -> RuntimeDatabaseResources:
        return await asyncio.to_thread(self._initialize_sync, settings)

    def _cleanup_sync(self) -> None:
        with self._lock:
            resource = self._partial
            self._partial = None
        if resource is not None:
            resource.close()

    async def cleanup_partial(self) -> None:
        await asyncio.to_thread(self._cleanup_sync)


def build_runtime_database_factory() -> CockroachRuntimeDatabaseFactory:
    return CockroachRuntimeDatabaseFactory()


async def prepare_runtime_database(settings: RuntimeSettings) -> MigrationExecutionSummary:
    """Explicit operator preflight; never retains the migration credential."""

    factory = build_runtime_database_factory()
    resources: RuntimeDatabaseResources | None = None
    try:
        resources = await factory.initialize(settings)
        return resources.migration
    finally:
        if resources is not None:
            resources.close()
        await factory.cleanup_partial()


__all__ = [
    "ApplicationDatabaseAuthoritySummary",
    "CockroachRuntimeDatabaseFactory",
    "EXPECTED_RLS_FORCE_TABLE_COUNT",
    "EXPECTED_SCHEMA_TABLE_COUNT",
    "FORBIDDEN_APPLICATION_ROLES",
    "RuntimeDatabaseFactory",
    "RuntimeDatabaseResources",
    "build_runtime_database_factory",
    "prepare_runtime_database",
    "validate_application_database_authority",
]
