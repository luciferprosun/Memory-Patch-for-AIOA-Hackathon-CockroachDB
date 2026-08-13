"""Canonical dependency composition and ASGI lifecycle for the demo runtime.

This module is glue only.  It deliberately does not perform retrieval,
verification, approval, commit, activation, migration, or provider work.
"""

from __future__ import annotations

import asyncio
import inspect
import os
from collections.abc import Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from enum import Enum
from typing import Protocol

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from aioa_memory_kernel.demo_cockpit import CockpitRuntimeStatus, CockpitShell
from aioa_memory_kernel.modeling.models import load_approved_provider_spec
from aioa_memory_kernel.modeling.providers import OpenRouterDraftV1Adapter
from aioa_memory_kernel.modeling.service import SystemUTCClock
from aioa_memory_kernel.personal_memory import (
    PersonalMemoryApprovalService,
    PersonalMemoryLifecycle32Service,
    PersonalMemoryService,
)
from aioa_memory_kernel.personal_memory_ui import (
    HttpxOidcClient,
    KernelPersonalMemoryUiBackend,
    MemoryOwnerSessionStore,
    create_personal_memory_app,
)
from aioa_memory_kernel.personal_memory_ui.cockroach_sessions import (
    CockroachOwnerSessionStore,
)
from aioa_memory_kernel.personal_memory_ui.models import OwnerPrincipal

from .config import (
    RuntimeAssemblyError,
    RuntimeErrorCode,
    RuntimeMode,
    RuntimeSettings,
)
from .judge_access import JudgeAccessPolicy
from .health import (
    RuntimeReadinessReason,
    RuntimeReadinessSnapshot,
    RuntimeReadinessState,
)
from .provider_guard import (
    CockroachProviderGuardLedger,
    GuardedProviderAdapter,
)


class RuntimeStartupStage(str, Enum):
    CONFIG_VALIDATED = "CONFIG_VALIDATED"
    DEPENDENCY_AVAILABILITY_VALIDATED = "DEPENDENCY_AVAILABILITY_VALIDATED"
    DATABASE_RESOURCES_INITIALIZED = "DATABASE_RESOURCES_INITIALIZED"
    SESSION_RESOURCES_INITIALIZED = "SESSION_RESOURCES_INITIALIZED"
    SERVICE_COMPOSITION_INITIALIZED = "SERVICE_COMPOSITION_INITIALIZED"
    PROVIDER_ADAPTER_INITIALIZED = "PROVIDER_ADAPTER_INITIALIZED"
    RUNTIME_GUARDS_INITIALIZED = "RUNTIME_GUARDS_INITIALIZED"
    APPLICATION_STARTED = "APPLICATION_STARTED"


STARTUP_ORDER = tuple(RuntimeStartupStage)


class SessionStorageClass(str, Enum):
    TEST_ONLY = "TEST_ONLY"
    DURABLE = "DURABLE"


def _allow_test_principal(_principal: OwnerPrincipal) -> bool:
    return True


class RuntimeStartupRecorder:
    """Enforce one deterministic startup sequence across R2-R6 factories."""

    def __init__(self) -> None:
        self._events: list[RuntimeStartupStage] = []

    @property
    def events(self) -> tuple[RuntimeStartupStage, ...]:
        return tuple(self._events)

    def advance(self, stage: RuntimeStartupStage) -> None:
        if not isinstance(stage, RuntimeStartupStage):
            raise RuntimeAssemblyError(RuntimeErrorCode.STARTUP_FAILED)
        expected_index = len(self._events)
        if expected_index >= len(STARTUP_ORDER) or STARTUP_ORDER[expected_index] is not stage:
            raise RuntimeAssemblyError(RuntimeErrorCode.STARTUP_FAILED)
        self._events.append(stage)

    def require_dependencies_initialized(self) -> None:
        if self.events != STARTUP_ORDER[:-1]:
            raise RuntimeAssemblyError(RuntimeErrorCode.STARTUP_FAILED)


@dataclass(frozen=True, slots=True)
class RuntimeDependencies:
    """Top-level adapters assembled by one purpose-aware runtime factory."""

    backend: object
    oidc_client: object
    session_store: object
    provider_adapter: object
    session_storage_class: SessionStorageClass
    principal_authorizer: Callable[[OwnerPrincipal], bool] = _allow_test_principal
    owned_background_resources: tuple[object, ...] = ()
    owned_provider_resources: tuple[object, ...] = ()
    owned_session_resources: tuple[object, ...] = ()
    owned_database_resources: tuple[object, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.session_storage_class, SessionStorageClass):
            raise RuntimeAssemblyError(RuntimeErrorCode.DEPENDENCY_MISSING)
        for name in (
            "owned_background_resources",
            "owned_provider_resources",
            "owned_session_resources",
            "owned_database_resources",
        ):
            resources = getattr(self, name)
            if not isinstance(resources, tuple) or any(item is None for item in resources):
                raise RuntimeAssemblyError(RuntimeErrorCode.DEPENDENCY_MISSING)


class RuntimeDependencyFactory(Protocol):
    def initialize(
        self,
        settings: RuntimeSettings,
        recorder: RuntimeStartupRecorder,
    ) -> RuntimeDependencies | Awaitable[RuntimeDependencies]:
        ...

    def cleanup_partial(self) -> object | Awaitable[object]:
        ...


class RuntimeApplicationDependencyFactory(Protocol):
    """R4/R5 assembly that receives only the normal R3 database resource."""

    def validate_availability(self, settings: RuntimeSettings) -> object: ...

    def initialize_after_database(
        self,
        settings: RuntimeSettings,
        recorder: RuntimeStartupRecorder,
        database: object,
    ) -> RuntimeDependencies | Awaitable[RuntimeDependencies]: ...

    def cleanup_partial(self) -> object | Awaitable[object]: ...


class RuntimeServiceDependencyFactory(Protocol):
    """R5 service/provider assembly after R4 auth/session initialization."""

    def validate_availability(self, settings: RuntimeSettings) -> object: ...

    def initialize_after_sessions(
        self,
        settings: RuntimeSettings,
        recorder: RuntimeStartupRecorder,
        database: object,
        oidc_client: object,
        session_store: object,
        principal_authorizer: Callable[[OwnerPrincipal], bool],
    ) -> RuntimeDependencies | Awaitable[RuntimeDependencies]: ...

    def cleanup_partial(self) -> object | Awaitable[object]: ...


class MissingRuntimeDependencyFactory:
    """Fail-closed R2 boundary until R3-R5 provide the concrete factory."""

    def initialize(
        self,
        settings: RuntimeSettings,
        recorder: RuntimeStartupRecorder,
    ) -> RuntimeDependencies:
        raise RuntimeAssemblyError(RuntimeErrorCode.DEPENDENCY_MISSING)

    def cleanup_partial(self) -> None:
        return None


class MissingApplicationDependencyFactory:
    """Keep R3 fail-closed until R4/R5 supply sessions, services and provider."""

    def validate_availability(self, settings: RuntimeSettings) -> None:
        raise RuntimeAssemblyError(RuntimeErrorCode.DEPENDENCY_MISSING)

    def initialize_after_database(
        self,
        settings: RuntimeSettings,
        recorder: RuntimeStartupRecorder,
        database: object,
    ) -> RuntimeDependencies:
        raise RuntimeAssemblyError(RuntimeErrorCode.DEPENDENCY_MISSING)

    def cleanup_partial(self) -> None:
        return None


class MissingServiceDependencyFactory:
    """Keep the canonical runtime fail-closed until R5 service composition."""

    def validate_availability(self, settings: RuntimeSettings) -> None:
        raise RuntimeAssemblyError(RuntimeErrorCode.DEPENDENCY_MISSING)

    def initialize_after_sessions(
        self,
        settings: RuntimeSettings,
        recorder: RuntimeStartupRecorder,
        database: object,
        oidc_client: object,
        session_store: object,
        principal_authorizer: Callable[[OwnerPrincipal], bool],
    ) -> RuntimeDependencies:
        raise RuntimeAssemblyError(RuntimeErrorCode.DEPENDENCY_MISSING)

    def cleanup_partial(self) -> None:
        return None


class ProviderRuntimeServiceDependencyFactory:
    """Assemble existing owner services and the one R5 guarded provider."""

    def __init__(self) -> None:
        self._partial_provider: GuardedProviderAdapter | None = None

    def validate_availability(self, settings: RuntimeSettings) -> None:
        if not isinstance(settings, RuntimeSettings):
            raise RuntimeAssemblyError(RuntimeErrorCode.CONFIG_INVALID)
        provider = settings.require_provider()
        guard = settings.require_provider_guard()
        spec = load_approved_provider_spec()
        if (
            provider.provider_id != spec.provider_id
            or provider.model_id != spec.model_id
            or provider.adapter_id != spec.adapter_version
            or provider.configuration_digest != spec.config_digest
            or guard.maximum_queued_calls > settings.profile.queues.provider
            or settings.profile.process_layout.web_workers != 1
            or settings.profile.optional_services.critic_enabled_by_default
        ):
            raise RuntimeAssemblyError(RuntimeErrorCode.PROVIDER_CONFIG_INVALID)

    def initialize_after_sessions(
        self,
        settings: RuntimeSettings,
        recorder: RuntimeStartupRecorder,
        database: object,
        oidc_client: object,
        session_store: object,
        principal_authorizer: Callable[[OwnerPrincipal], bool],
    ) -> RuntimeDependencies:
        runner = getattr(database, "transaction_runner", None)
        if runner is None:
            raise RuntimeAssemblyError(RuntimeErrorCode.DEPENDENCY_MISSING)
        provider_settings = settings.require_provider()
        guard_settings = settings.require_provider_guard()
        clock = SystemUTCClock()
        try:
            personal_memory = PersonalMemoryService(runner)
            approvals = PersonalMemoryApprovalService(
                runner,
                trusted_clock=clock,
            )
            lifecycle = PersonalMemoryLifecycle32Service(
                runner,
                trusted_clock=clock,
            )
            backend = KernelPersonalMemoryUiBackend(
                runner,
                personal_memory_service=personal_memory,
                approval_service=approvals,
                lifecycle_service=lifecycle,
                trusted_now=clock.now,
            )
        except Exception:
            raise RuntimeAssemblyError(RuntimeErrorCode.DEPENDENCY_MISSING) from None
        recorder.advance(RuntimeStartupStage.SERVICE_COMPOSITION_INITIALIZED)

        try:
            raw_provider = OpenRouterDraftV1Adapter(
                provider_settings.credential,
                spec=load_approved_provider_spec(),
            )
            ledger = CockroachProviderGuardLedger(
                runner,
                provider_id=provider_settings.provider_id,
                budget_epoch=guard_settings.budget_epoch or "",
                limits=guard_settings,
            )
            guarded_provider = GuardedProviderAdapter(
                raw_provider,
                ledger=ledger,
                limits=guard_settings,
            )
        except RuntimeAssemblyError:
            raise
        except Exception:
            raise RuntimeAssemblyError(RuntimeErrorCode.PROVIDER_CONFIG_INVALID) from None
        self._partial_provider = guarded_provider
        recorder.advance(RuntimeStartupStage.PROVIDER_ADAPTER_INITIALIZED)
        if not guarded_provider.durable_accounting:
            raise RuntimeAssemblyError(RuntimeErrorCode.PROVIDER_GUARD_CONFIG_INVALID)
        recorder.advance(RuntimeStartupStage.RUNTIME_GUARDS_INITIALIZED)
        dependencies = RuntimeDependencies(
            backend=backend,
            oidc_client=oidc_client,
            session_store=session_store,
            provider_adapter=guarded_provider,
            session_storage_class=SessionStorageClass.DURABLE,
            principal_authorizer=principal_authorizer,
            owned_provider_resources=(guarded_provider,),
        )
        self._partial_provider = None
        return dependencies

    def cleanup_partial(self) -> None:
        provider = self._partial_provider
        self._partial_provider = None
        if provider is not None:
            provider.close()


class JudgeSessionApplicationDependencyFactory:
    """Compose Step35 OIDC with the R4 CockroachDB session implementation."""

    def __init__(self, service_factory: RuntimeServiceDependencyFactory) -> None:
        if not _has_callables(
            service_factory,
            ("validate_availability", "initialize_after_sessions", "cleanup_partial"),
        ):
            raise RuntimeAssemblyError(RuntimeErrorCode.DEPENDENCY_MISSING)
        self._service_factory = service_factory
        self._partial_oidc: object | None = None
        self._partial_session: object | None = None

    @property
    def deployment_dependencies_complete(self) -> bool:
        return not isinstance(self._service_factory, MissingServiceDependencyFactory)

    def validate_availability(self, settings: RuntimeSettings) -> object:
        if not isinstance(settings, RuntimeSettings):
            raise RuntimeAssemblyError(RuntimeErrorCode.CONFIG_INVALID)
        JudgeAccessPolicy(settings.judge_access.allowed_oidc_subjects)
        return self._service_factory.validate_availability(settings)

    async def initialize_after_database(
        self,
        settings: RuntimeSettings,
        recorder: RuntimeStartupRecorder,
        database: object,
    ) -> RuntimeDependencies:
        application_pool = getattr(database, "application_pool", None)
        connection_factory = getattr(application_pool, "connection_factory", None)
        if not bool(getattr(database, "ready", False)) or not callable(
            connection_factory
        ):
            raise RuntimeAssemblyError(RuntimeErrorCode.DEPENDENCY_MISSING)
        session_store = CockroachOwnerSessionStore(
            limits=settings.sessions.limits,
            connection_factory=connection_factory(),
        )
        oidc_client = HttpxOidcClient(settings.oidc)
        judge_policy = JudgeAccessPolicy(
            settings.judge_access.allowed_oidc_subjects
        )
        self._partial_session = session_store
        self._partial_oidc = oidc_client
        recorder.advance(RuntimeStartupStage.SESSION_RESOURCES_INITIALIZED)
        dependencies = await _await_if_needed(
            self._service_factory.initialize_after_sessions(
                settings,
                recorder,
                database,
                oidc_client,
                session_store,
                judge_policy,
            )
        )
        if (
            not isinstance(dependencies, RuntimeDependencies)
            or dependencies.oidc_client is not oidc_client
            or dependencies.session_store is not session_store
        ):
            raise RuntimeAssemblyError(RuntimeErrorCode.DEPENDENCY_MISSING)
        dependencies = replace(
            dependencies,
            principal_authorizer=judge_policy,
            session_storage_class=SessionStorageClass.DURABLE,
            owned_provider_resources=_identity_union(
                dependencies.owned_provider_resources, (oidc_client,)
            ),
            owned_session_resources=_identity_union(
                dependencies.owned_session_resources, (session_store,)
            ),
            owned_database_resources=_identity_union(
                dependencies.owned_database_resources, (database,)
            ),
        )
        self._partial_session = None
        self._partial_oidc = None
        return dependencies

    async def cleanup_partial(self) -> None:
        try:
            await _await_if_needed(self._service_factory.cleanup_partial())
        finally:
            session = self._partial_session
            oidc = self._partial_oidc
            self._partial_session = None
            self._partial_oidc = None
            if session is not None:
                await _await_if_needed(session.close())  # type: ignore[attr-defined]
            if oidc is not None:
                await _await_if_needed(oidc.close())  # type: ignore[attr-defined]


class CockroachBoundRuntimeDependencyFactory:
    """Sequence R3 migration/pool closure before R4/R5 service assembly."""

    def __init__(
        self,
        *,
        database_factory: object,
        application_factory: RuntimeApplicationDependencyFactory,
    ) -> None:
        if not _has_callables(database_factory, ("initialize", "cleanup_partial")):
            raise RuntimeAssemblyError(RuntimeErrorCode.DEPENDENCY_MISSING)
        if not _has_callables(
            application_factory,
            ("validate_availability", "initialize_after_database", "cleanup_partial"),
        ):
            raise RuntimeAssemblyError(RuntimeErrorCode.DEPENDENCY_MISSING)
        self._database_factory = database_factory
        self._application_factory = application_factory
        self._partial_database: object | None = None

    @property
    def deployment_dependencies_complete(self) -> bool:
        return bool(
            getattr(
                self._application_factory,
                "deployment_dependencies_complete",
                not isinstance(
                    self._application_factory, MissingApplicationDependencyFactory
                ),
            )
        )

    async def initialize(
        self,
        settings: RuntimeSettings,
        recorder: RuntimeStartupRecorder,
    ) -> RuntimeDependencies:
        await _await_if_needed(
            self._application_factory.validate_availability(settings)
        )
        recorder.advance(RuntimeStartupStage.DEPENDENCY_AVAILABILITY_VALIDATED)
        database = await _await_if_needed(
            self._database_factory.initialize(settings)  # type: ignore[attr-defined]
        )
        if database is None or not callable(getattr(database, "close", None)):
            raise RuntimeAssemblyError(RuntimeErrorCode.DATABASE_POOL_FAILED)
        self._partial_database = database
        recorder.advance(RuntimeStartupStage.DATABASE_RESOURCES_INITIALIZED)
        dependencies = await _await_if_needed(
            self._application_factory.initialize_after_database(
                settings,
                recorder,
                database,
            )
        )
        if (
            not isinstance(dependencies, RuntimeDependencies)
            or all(
                database is not resource
                for resource in dependencies.owned_database_resources
            )
        ):
            raise RuntimeAssemblyError(RuntimeErrorCode.DEPENDENCY_MISSING)
        self._partial_database = None
        return dependencies

    async def cleanup_partial(self) -> None:
        try:
            await _await_if_needed(self._application_factory.cleanup_partial())
        finally:
            database = self._partial_database
            self._partial_database = None
            if database is not None:
                await _await_if_needed(database.close())
            await _await_if_needed(
                self._database_factory.cleanup_partial()  # type: ignore[attr-defined]
            )


def build_default_application_dependency_factory() -> RuntimeApplicationDependencyFactory:
    return JudgeSessionApplicationDependencyFactory(
        ProviderRuntimeServiceDependencyFactory()
    )


def build_default_runtime_dependency_factory() -> RuntimeDependencyFactory:
    """Bind the complete R2-R5 DB, auth, owner-service, and provider path."""

    from .database import build_runtime_database_factory

    return CockroachBoundRuntimeDependencyFactory(
        database_factory=build_runtime_database_factory(),
        application_factory=build_default_application_dependency_factory(),
    )


def require_default_runtime_dependencies() -> RuntimeDependencyFactory:
    factory = build_default_runtime_dependency_factory()
    if isinstance(factory, MissingRuntimeDependencyFactory) or not bool(
        getattr(factory, "deployment_dependencies_complete", False)
    ):
        raise RuntimeAssemblyError(RuntimeErrorCode.DEPENDENCY_MISSING)
    return factory


class _DeferredDependency:
    """Instance-local late binding used only to preserve the existing app factory."""

    __slots__ = ("_delegate", "_label")

    def __init__(self, label: str) -> None:
        self._delegate: object | None = None
        self._label = label

    def bind(self, delegate: object) -> None:
        if delegate is None or self._delegate is not None:
            raise RuntimeAssemblyError(RuntimeErrorCode.STARTUP_FAILED)
        self._delegate = delegate

    def clear(self) -> None:
        self._delegate = None

    def __getattr__(self, name: str):
        delegate = self._delegate
        if delegate is None:
            raise RuntimeAssemblyError(RuntimeErrorCode.DEPENDENCY_MISSING)
        return getattr(delegate, name)

    def __call__(self, *args: object, **kwargs: object) -> object:
        delegate = self._delegate
        if not callable(delegate):
            raise RuntimeAssemblyError(RuntimeErrorCode.DEPENDENCY_MISSING)
        return delegate(*args, **kwargs)

    def __repr__(self) -> str:
        return f"DeferredRuntimeDependency(label={self._label!r}, value='<redacted>')"


def _has_callables(value: object, names: tuple[str, ...]) -> bool:
    return all(callable(getattr(value, name, None)) for name in names)


def _identity_union(
    existing: tuple[object, ...], added: tuple[object, ...]
) -> tuple[object, ...]:
    result = list(existing)
    identities = {id(value) for value in result}
    for value in added:
        if id(value) not in identities:
            result.append(value)
            identities.add(id(value))
    return tuple(result)


def _validate_dependencies(
    settings: RuntimeSettings,
    dependencies: RuntimeDependencies,
) -> None:
    if not isinstance(dependencies, RuntimeDependencies):
        raise RuntimeAssemblyError(RuntimeErrorCode.DEPENDENCY_MISSING)
    if not _has_callables(
        dependencies.backend,
        (
            "dashboard",
            "slot_detail",
            "approve_proposal",
            "configure_slot",
            "transition_slot",
            "update_model_binding",
            "revoke_patch",
            "export_slot",
            "delete_patch",
        ),
    ):
        raise RuntimeAssemblyError(RuntimeErrorCode.DEPENDENCY_MISSING)
    if not callable(dependencies.principal_authorizer):
        raise RuntimeAssemblyError(RuntimeErrorCode.DEPENDENCY_MISSING)
    if not _has_callables(dependencies.oidc_client, ("authorization_url", "authenticate")):
        raise RuntimeAssemblyError(RuntimeErrorCode.DEPENDENCY_MISSING)
    if not _has_callables(
        dependencies.session_store,
        (
            "create_pending",
            "consume_pending",
            "create_session",
            "get_session",
            "delete_session",
        ),
    ):
        raise RuntimeAssemblyError(RuntimeErrorCode.DEPENDENCY_MISSING)
    provider_identity = getattr(dependencies.provider_adapter, "provider_identity", None)
    if not callable(provider_identity):
        raise RuntimeAssemblyError(RuntimeErrorCode.DEPENDENCY_MISSING)
    try:
        identity = provider_identity()
        expected = load_approved_provider_spec().provider_identity()
    except Exception:
        raise RuntimeAssemblyError(RuntimeErrorCode.DEPENDENCY_MISSING) from None
    if identity != expected:
        raise RuntimeAssemblyError(RuntimeErrorCode.DEPENDENCY_MISSING)
    if dependencies.provider_adapter in (
        dependencies.backend,
        dependencies.session_store,
    ) or any(
        dependencies.provider_adapter is resource
        for resource in dependencies.owned_database_resources
    ):
        raise RuntimeAssemblyError(RuntimeErrorCode.STARTUP_FAILED)

    if settings.mode is not RuntimeMode.TEST:
        if (
            dependencies.session_storage_class is not SessionStorageClass.DURABLE
            or isinstance(dependencies.session_store, MemoryOwnerSessionStore)
        ):
            raise RuntimeAssemblyError(
                RuntimeErrorCode.UNSAFE_TEST_ADAPTER_IN_HOSTED_MODE
            )
        if not dependencies.owned_database_resources:
            raise RuntimeAssemblyError(RuntimeErrorCode.DEPENDENCY_MISSING)
        if not isinstance(dependencies.principal_authorizer, JudgeAccessPolicy):
            raise RuntimeAssemblyError(RuntimeErrorCode.UNSAFE_TEST_ADAPTER_IN_HOSTED_MODE)


def _readiness_dependency_ids(
    settings: RuntimeSettings,
    dependencies: RuntimeDependencies,
) -> tuple[str, ...]:
    """Recheck every R2-R5 traffic-admission dependency without I/O."""

    dependency_ids = {
        "approved_provider_identity",
        "judge_auth_configuration",
        "mandatory_runtime_services",
        "provider_configuration",
        "runtime_configuration",
        "startup_sequence",
    }
    if settings.mode is not RuntimeMode.TEST:
        database_ready = any(
            bool(getattr(resource, "ready", False))
            for resource in dependencies.owned_database_resources
        )
        if not database_ready:
            raise RuntimeAssemblyError(RuntimeErrorCode.DATABASE_POOL_FAILED)
        if not bool(getattr(dependencies.session_store, "ready", True)):
            raise RuntimeAssemblyError(RuntimeErrorCode.DEPENDENCY_MISSING)
        if not bool(getattr(dependencies.provider_adapter, "durable_accounting", False)):
            raise RuntimeAssemblyError(RuntimeErrorCode.PROVIDER_GUARD_CONFIG_INVALID)
        settings.require_database()
        settings.require_provider()
        settings.require_provider_guard()
        dependency_ids.update(
            {
                "application_database_pool",
                "cockroachdb_tls_policy",
                "durable_session_store",
                "migration_state",
                "provider_call_spend_guard",
            }
        )
    return tuple(sorted(dependency_ids))


def _readiness_reason(error: RuntimeAssemblyError) -> RuntimeReadinessReason:
    mapping = {
        RuntimeErrorCode.CONFIG_INVALID: RuntimeReadinessReason.CONFIG_INVALID,
        RuntimeErrorCode.DATABASE_CONFIG_INVALID: RuntimeReadinessReason.CONFIG_INVALID,
        RuntimeErrorCode.DATABASE_TLS_REQUIRED: RuntimeReadinessReason.CONFIG_INVALID,
        RuntimeErrorCode.DATABASE_POOL_FAILED: RuntimeReadinessReason.DATABASE_UNAVAILABLE,
        RuntimeErrorCode.DATABASE_MIGRATION_FAILED: (
            RuntimeReadinessReason.DATABASE_UNAVAILABLE
        ),
        RuntimeErrorCode.AUTH_CONFIG_INVALID: (
            RuntimeReadinessReason.AUTH_CONFIGURATION_INVALID
        ),
        RuntimeErrorCode.SESSION_CONFIG_INVALID: (
            RuntimeReadinessReason.SESSION_STORE_UNAVAILABLE
        ),
        RuntimeErrorCode.DEPENDENCY_MISSING: (
            RuntimeReadinessReason.DEPENDENCY_UNAVAILABLE
        ),
        RuntimeErrorCode.PROVIDER_CONFIG_INVALID: (
            RuntimeReadinessReason.PROVIDER_CONFIGURATION_INVALID
        ),
        RuntimeErrorCode.PROVIDER_GUARD_CONFIG_INVALID: (
            RuntimeReadinessReason.PROVIDER_GUARD_UNAVAILABLE
        ),
    }
    return mapping.get(error.code, RuntimeReadinessReason.STARTUP_FAILED)


def _startup_timeout_seconds(settings: RuntimeSettings) -> int:
    """Bound migration preparation separately from post-schema readiness.

    Step 40's readiness timeout applies after database connection and schema
    verification. R3 migration preparation has its own bounded timeout and is
    deliberately part of the fail-closed startup sequence, so the lifespan
    budget is their sum rather than silently replacing the migration bound.
    """

    readiness = settings.profile.startup.readiness_timeout_seconds
    database = settings.database
    if database is None:
        return readiness
    return database.migration_timeout_seconds + readiness


async def _await_if_needed(value: object) -> object:
    if inspect.isawaitable(value):
        return await value
    return value


async def _close_resources(resources: tuple[object, ...], seen: set[int]) -> bool:
    failed = False
    for resource in reversed(resources):
        identity = id(resource)
        if identity in seen:
            continue
        seen.add(identity)
        close = getattr(resource, "close", None)
        if not callable(close):
            failed = True
            continue
        try:
            await _await_if_needed(close())
        except Exception:
            failed = True
    return failed


async def _close_dependencies(dependencies: RuntimeDependencies) -> bool:
    seen: set[int] = set()
    failed = False
    for resources in (
        dependencies.owned_background_resources,
        dependencies.owned_provider_resources,
        dependencies.owned_session_resources,
        dependencies.owned_database_resources,
    ):
        failed = await _close_resources(resources, seen) or failed
    return failed


class RuntimeController:
    """Own startup state and close only resources explicitly granted to it."""

    def __init__(
        self,
        *,
        import_settings: RuntimeSettings,
        settings_loader: Callable[[], RuntimeSettings],
        dependency_factory: RuntimeDependencyFactory,
        backend_proxy: _DeferredDependency,
        oidc_proxy: _DeferredDependency,
        session_proxy: _DeferredDependency,
        authorizer_proxy: _DeferredDependency,
        readiness: RuntimeReadinessState,
    ) -> None:
        self._import_settings = import_settings
        self._settings_loader = settings_loader
        self._factory = dependency_factory
        self._backend_proxy = backend_proxy
        self._oidc_proxy = oidc_proxy
        self._session_proxy = session_proxy
        self._authorizer_proxy = authorizer_proxy
        self._readiness = readiness
        self._dependencies: RuntimeDependencies | None = None
        self._started = False
        self._shutdown = False
        self._startup_trace: tuple[RuntimeStartupStage, ...] = ()

    @property
    def started(self) -> bool:
        return self._started

    @property
    def accepting_work(self) -> bool:
        return (
            self._started
            and not self._shutdown
            and self._readiness.snapshot().ready
        )

    @property
    def readiness(self) -> RuntimeReadinessSnapshot:
        return self._readiness.snapshot()

    @property
    def session_store(self) -> object:
        dependencies = self._dependencies
        if dependencies is None or not self.accepting_work:
            raise RuntimeAssemblyError(RuntimeErrorCode.DEPENDENCY_MISSING)
        return dependencies.session_store

    def report_dependency_failure(self, reason: RuntimeReadinessReason) -> None:
        """Fail traffic admission after a sanitized runtime-detected outage."""

        self._readiness.mark_not_ready(reason)

    @property
    def startup_trace(self) -> tuple[RuntimeStartupStage, ...]:
        return self._startup_trace

    @property
    def provider_adapter(self) -> object:
        dependencies = self._dependencies
        if dependencies is None or not self.accepting_work:
            raise RuntimeAssemblyError(RuntimeErrorCode.DEPENDENCY_MISSING)
        return dependencies.provider_adapter

    async def start(self) -> None:
        if self._started:
            return
        if self._shutdown:
            raise RuntimeAssemblyError(RuntimeErrorCode.STARTUP_FAILED)
        self._readiness.begin_startup()
        recorder = RuntimeStartupRecorder()
        dependencies: RuntimeDependencies | None = None
        initialization_attempted = False
        try:
            settings = self._settings_loader()
            if not isinstance(settings, RuntimeSettings) or settings != self._import_settings:
                raise RuntimeAssemblyError(RuntimeErrorCode.CONFIG_INVALID)
            recorder.advance(RuntimeStartupStage.CONFIG_VALIDATED)
            initialization_attempted = True
            dependencies = await _await_if_needed(
                self._factory.initialize(settings, recorder)
            )
            if not isinstance(dependencies, RuntimeDependencies):
                raise RuntimeAssemblyError(RuntimeErrorCode.DEPENDENCY_MISSING)
            recorder.require_dependencies_initialized()
            _validate_dependencies(settings, dependencies)
            readiness_dependencies = _readiness_dependency_ids(settings, dependencies)
            self._backend_proxy.bind(dependencies.backend)
            self._oidc_proxy.bind(dependencies.oidc_client)
            self._session_proxy.bind(dependencies.session_store)
            self._authorizer_proxy.bind(dependencies.principal_authorizer)
            recorder.advance(RuntimeStartupStage.APPLICATION_STARTED)
            self._dependencies = dependencies
            self._startup_trace = recorder.events
            self._started = True
            self._readiness.mark_ready(readiness_dependencies)
        except BaseException as error:
            self._startup_trace = recorder.events
            self._backend_proxy.clear()
            self._oidc_proxy.clear()
            self._session_proxy.clear()
            self._authorizer_proxy.clear()
            if dependencies is not None:
                await _close_dependencies(dependencies)
            elif initialization_attempted:
                cleanup = getattr(self._factory, "cleanup_partial", None)
                if callable(cleanup):
                    try:
                        await _await_if_needed(cleanup())
                    except Exception:
                        pass
            if isinstance(error, RuntimeAssemblyError):
                self._readiness.mark_failed(_readiness_reason(error))
                raise error from None
            if isinstance(error, (KeyboardInterrupt, SystemExit, asyncio.CancelledError)):
                raise
            self._readiness.mark_failed(RuntimeReadinessReason.STARTUP_FAILED)
            raise RuntimeAssemblyError(RuntimeErrorCode.STARTUP_FAILED) from None

    async def shutdown(self) -> None:
        if self._shutdown:
            return
        self._readiness.mark_stopping()
        self._shutdown = True
        self._backend_proxy.clear()
        self._oidc_proxy.clear()
        self._session_proxy.clear()
        self._authorizer_proxy.clear()
        dependencies = self._dependencies
        self._dependencies = None
        self._started = False
        if dependencies is not None and await _close_dependencies(dependencies):
            raise RuntimeAssemblyError(RuntimeErrorCode.SHUTDOWN_FAILED)


def create_demo_runtime_app(
    *,
    settings: RuntimeSettings,
    dependency_factory: RuntimeDependencyFactory,
    settings_loader: Callable[[], RuntimeSettings] | None = None,
) -> FastAPI:
    """Create the one canonical app by reusing the Step35 application factory."""

    if not isinstance(settings, RuntimeSettings):
        raise RuntimeAssemblyError(RuntimeErrorCode.CONFIG_INVALID)
    loader = settings_loader or (lambda: settings)
    backend_proxy = _DeferredDependency("personal-memory-ui-backend")
    oidc_proxy = _DeferredDependency("oidc-client")
    session_proxy = _DeferredDependency("owner-session-store")
    authorizer_proxy = _DeferredDependency("judge-principal-authorizer")
    readiness = RuntimeReadinessState()
    controller = RuntimeController(
        import_settings=settings,
        settings_loader=loader,
        dependency_factory=dependency_factory,
        backend_proxy=backend_proxy,
        oidc_proxy=oidc_proxy,
        session_proxy=session_proxy,
        authorizer_proxy=authorizer_proxy,
        readiness=readiness,
    )
    approved_provider = load_approved_provider_spec()
    cockpit_shell = CockpitShell(
        CockpitRuntimeStatus(
            profile_id=settings.profile.profile_id,
            asgi_application="aioa_memory_kernel.demo_runtime.asgi:app",
            authentication="OIDC + PKCE / deny-by-default judge access",
            session_backend=(
                "CockroachOwnerSessionStore"
                if settings.mode is not RuntimeMode.TEST
                else "Explicit test OwnerSessionStore"
            ),
            database="CockroachDB / TLS and migration gated",
            provider=(
                f"{approved_provider.provider_id} / {approved_provider.model_id}"
            ),
            provider_guard="GuardedProviderAdapter / CALL-COUNT CEILING",
            readiness_contract="/health/live + /health/ready",
        ),
        legacy_enabled=settings.legacy_cockpit_enabled,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            async with asyncio.timeout(
                _startup_timeout_seconds(settings)
            ):
                await controller.start()
        except TimeoutError:
            raise RuntimeAssemblyError(RuntimeErrorCode.STARTUP_FAILED) from None
        try:
            yield
        finally:
            try:
                async with asyncio.timeout(
                    settings.profile.startup.shutdown_timeout_seconds
                ):
                    await controller.shutdown()
            except TimeoutError:
                raise RuntimeAssemblyError(RuntimeErrorCode.SHUTDOWN_FAILED) from None

    app = create_personal_memory_app(
        backend=backend_proxy,
        oidc_client=oidc_proxy,
        oidc_settings=settings.oidc,
        session_store=session_proxy,
        principal_authorizer=authorizer_proxy,
        session_cookie_max_age=settings.sessions.limits.absolute_ttl_seconds,
        oidc_flow_cookie_max_age=settings.sessions.limits.pending_ttl_seconds,
        lifespan=lifespan,
        cockpit_shell=cockpit_shell,
    )
    app.state.runtime_controller = controller
    app.state.runtime_mode = settings.mode.value
    app.state.runtime_profile_id = settings.profile.profile_id
    app.state.runtime_profile_digest = settings.profile.profile_digest

    @app.get("/health/live", include_in_schema=False)
    async def health_live() -> JSONResponse:
        return JSONResponse({"status": "LIVE"}, status_code=200)

    @app.get("/health/ready", include_in_schema=False)
    async def health_ready() -> JSONResponse:
        snapshot = controller.readiness
        if snapshot.ready:
            return JSONResponse({"status": "READY"}, status_code=200)
        return JSONResponse(
            {"status": "NOT_READY", "reason": snapshot.reason.value},
            status_code=503,
        )
    return app


def create_canonical_asgi_app(
    environment: Mapping[str, str] | None = None,
) -> FastAPI:
    """Build an import-safe app; strict configuration is rechecked at startup."""

    source = os.environ if environment is None else environment
    try:
        import_settings = RuntimeSettings.from_mapping(source)
    except RuntimeAssemblyError:
        import_settings = RuntimeSettings.import_placeholder()

    def strict_settings() -> RuntimeSettings:
        return RuntimeSettings.from_mapping(source)

    return create_demo_runtime_app(
        settings=import_settings,
        dependency_factory=build_default_runtime_dependency_factory(),
        settings_loader=strict_settings,
    )


__all__ = [
    "STARTUP_ORDER",
    "CockroachBoundRuntimeDependencyFactory",
    "JudgeSessionApplicationDependencyFactory",
    "MissingApplicationDependencyFactory",
    "MissingServiceDependencyFactory",
    "MissingRuntimeDependencyFactory",
    "ProviderRuntimeServiceDependencyFactory",
    "RuntimeController",
    "RuntimeDependencies",
    "RuntimeDependencyFactory",
    "RuntimeApplicationDependencyFactory",
    "RuntimeServiceDependencyFactory",
    "RuntimeStartupRecorder",
    "RuntimeStartupStage",
    "RuntimeReadinessReason",
    "SessionStorageClass",
    "build_default_application_dependency_factory",
    "build_default_runtime_dependency_factory",
    "create_canonical_asgi_app",
    "create_demo_runtime_app",
    "require_default_runtime_dependencies",
]
