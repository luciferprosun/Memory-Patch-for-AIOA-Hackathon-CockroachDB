"""Focused R2 composition, lifecycle and hosted-safety tests."""

from __future__ import annotations

import asyncio
import dataclasses
import importlib
import inspect
import os
import sys
import time
import unittest
from unittest import mock

from tests._support import REPOSITORY_ROOT  # noqa: F401

from fastapi import FastAPI
from fastapi.testclient import TestClient

from aioa_memory_kernel.demo_runtime import composition
from aioa_memory_kernel.demo_runtime.composition import (
    STARTUP_ORDER,
    MissingRuntimeDependencyFactory,
    RuntimeDependencies,
    RuntimeStartupStage,
    SessionStorageClass,
    _startup_timeout_seconds,
    create_demo_runtime_app,
)
from aioa_memory_kernel.demo_runtime.config import (
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
from aioa_memory_kernel.contracts.serialization import canonical_sha256
from aioa_memory_kernel.modeling.models import load_approved_provider_spec
from aioa_memory_kernel.personal_memory_ui import (
    HttpxOidcClient,
    MemoryOwnerSessionStore,
    OidcSettings,
)
from aioa_memory_kernel.personal_memory_ui.web import (
    create_personal_memory_app as step35_app_factory,
)
from aioa_memory_kernel.runtime.resource_profile import (
    Runtime4GBProfile,
    StartupPolicy,
)


def _environment(mode: RuntimeMode = RuntimeMode.TEST) -> dict[str, str]:
    values = {
        RUNTIME_MODE_ENV: mode.value,
        OIDC_ISSUER_ENV: "https://identity.test",
        OIDC_CLIENT_ID_ENV: "memory-patch-runtime-test",
        PUBLIC_ORIGIN_ENV: "https://testserver",
        JUDGE_ALLOWED_SUBJECTS_ENV: "memory-patch-test-subject",
    }
    if mode is not RuntimeMode.TEST:
        values.update(
            {
                "DATABASE_URL_APP": (
                    "postgresql://runtime-app:synthetic-app@db.example.invalid/"
                    "memory_patch?sslmode=verify-full"
                ),
                "DATABASE_URL_MIGRATOR": (
                    "postgresql://runtime-migrator:synthetic-migrator@"
                    "db.example.invalid/memory_patch?sslmode=verify-full"
                ),
            }
        )
    return values


class _Backend:
    def _unused(self, *args, **kwargs):
        raise AssertionError("backend business method must not run during startup")

    dashboard = _unused
    slot_detail = _unused
    approve_proposal = _unused
    configure_slot = _unused
    transition_slot = _unused
    update_model_binding = _unused
    revoke_patch = _unused
    export_slot = _unused
    delete_patch = _unused


class _OidcClient:
    def __init__(self, close_log: list[str] | None = None) -> None:
        self.close_log = close_log
        self.close_count = 0

    def authorization_url(self, *, state, nonce, code_challenge):
        return "https://identity.test/authorize"

    def authenticate(self, *, code, code_verifier, nonce):
        raise AssertionError("OIDC must not authenticate during startup")

    def close(self):
        self.close_count += 1
        if self.close_log is not None:
            self.close_log.append("oidc")


class _Provider:
    def __init__(self, close_log: list[str] | None = None) -> None:
        self.generate_calls = 0
        self.close_count = 0
        self.close_log = close_log

    def provider_identity(self):
        return load_approved_provider_spec().provider_identity()

    def generate(self, *args, **kwargs):
        self.generate_calls += 1
        raise AssertionError("paid provider work is forbidden during startup")

    def close(self):
        self.close_count += 1
        if self.close_log is not None:
            self.close_log.append("provider")


class _Closeable:
    def __init__(self, label: str, close_log: list[str] | None = None) -> None:
        self.label = label
        self.close_log = close_log
        self.close_count = 0

    def close(self):
        self.close_count += 1
        if self.close_log is not None:
            self.close_log.append(self.label)


class _HangingCloseable:
    async def close(self):
        await asyncio.sleep(10)


class _DurableSessionStub:
    def __init__(self) -> None:
        self._delegate = MemoryOwnerSessionStore(maximum_sessions=10)

    def create_pending(self, **values):
        return self._delegate.create_pending(**values)

    def consume_pending(self, *args, **values):
        return self._delegate.consume_pending(*args, **values)

    def create_session(self, *args, **values):
        return self._delegate.create_session(*args, **values)

    def get_session(self, *args, **values):
        return self._delegate.get_session(*args, **values)

    def delete_session(self, *args, **values):
        return self._delegate.delete_session(*args, **values)


class _Factory:
    def __init__(self, dependencies: RuntimeDependencies) -> None:
        self.dependencies = dependencies
        self.initialize_calls = 0
        self.cleanup_calls = 0

    def initialize(self, settings, recorder):
        self.initialize_calls += 1
        for stage in STARTUP_ORDER[1:-1]:
            recorder.advance(stage)
        return self.dependencies

    def cleanup_partial(self):
        self.cleanup_calls += 1


class _PartialFailingFactory:
    def __init__(self) -> None:
        self.partial = _Closeable("partial")

    def initialize(self, settings, recorder):
        recorder.advance(RuntimeStartupStage.DEPENDENCY_AVAILABILITY_VALIDATED)
        raise RuntimeError("synthetic value that must not be exposed")

    def cleanup_partial(self):
        self.partial.close()


class _HangingFactory:
    def __init__(self) -> None:
        self.cleanup_calls = 0

    async def initialize(self, settings, recorder):
        recorder.advance(RuntimeStartupStage.DEPENDENCY_AVAILABILITY_VALIDATED)
        await asyncio.sleep(10)

    def cleanup_partial(self):
        self.cleanup_calls += 1


def _dependencies(
    *,
    session_store=None,
    session_class: SessionStorageClass = SessionStorageClass.TEST_ONLY,
    database_resources: tuple[object, ...] = (),
    close_log: list[str] | None = None,
) -> tuple[RuntimeDependencies, _Provider, _OidcClient]:
    provider = _Provider(close_log)
    oidc = _OidcClient(close_log)
    session_store = session_store or MemoryOwnerSessionStore(maximum_sessions=10)
    dependencies = RuntimeDependencies(
        backend=_Backend(),
        oidc_client=oidc,
        session_store=session_store,
        provider_adapter=provider,
        session_storage_class=session_class,
        owned_provider_resources=(oidc, provider),
        owned_database_resources=database_resources,
    )
    return dependencies, provider, oidc


def _settings_with_one_second_lifecycle_timeout() -> RuntimeSettings:
    settings = RuntimeSettings.from_mapping(_environment())
    profile = settings.profile
    startup = StartupPolicy(
        order=profile.startup.order,
        readiness_timeout_seconds=1,
        shutdown_timeout_seconds=1,
    )
    values = {
        field.name: getattr(profile, field.name)
        for field in dataclasses.fields(profile)
        if field.name != "profile_digest"
    }
    values["startup"] = startup
    values["profile_digest"] = canonical_sha256(values)
    bounded_profile = Runtime4GBProfile(**values)
    return dataclasses.replace(settings, profile=bounded_profile)


class RuntimeConfigurationTests(unittest.TestCase):
    def test_test_mode_uses_safe_loopback_defaults(self) -> None:
        settings = RuntimeSettings.from_mapping({RUNTIME_MODE_ENV: "TEST"})
        self.assertEqual(settings.bind_host, "127.0.0.1")
        self.assertEqual(settings.port, 8000)
        self.assertEqual(settings.oidc.public_origin, "https://testserver")
        self.assertEqual(settings.profile.process_layout.web_workers, 1)

    def test_local_demo_rejects_non_loopback_bind(self) -> None:
        values = _environment(RuntimeMode.LOCAL_DEMO)
        values["AIOA_RUNTIME_BIND_HOST"] = "0.0.0.0"
        with self.assertRaises(RuntimeAssemblyError) as raised:
            RuntimeSettings.from_mapping(values)
        self.assertEqual(raised.exception.code, RuntimeErrorCode.CONFIG_INVALID)

    def test_hosted_configuration_is_mandatory_and_sanitized(self) -> None:
        with self.assertRaises(RuntimeAssemblyError) as raised:
            RuntimeSettings.from_mapping({RUNTIME_MODE_ENV: "HOSTED_DEMO"})
        self.assertEqual(
            str(raised.exception),
            "Memory Patch runtime failed safely: CONFIG_INVALID",
        )


class RuntimeCompositionTests(unittest.TestCase):
    def test_startup_budget_keeps_migration_and_readiness_bounds_separate(self) -> None:
        test_settings = RuntimeSettings.from_mapping(_environment())
        self.assertEqual(
            _startup_timeout_seconds(test_settings),
            test_settings.profile.startup.readiness_timeout_seconds,
        )
        hosted_settings = RuntimeSettings.from_mapping(
            _environment(RuntimeMode.HOSTED_DEMO)
        )
        database = hosted_settings.require_database()
        self.assertEqual(
            _startup_timeout_seconds(hosted_settings),
            database.migration_timeout_seconds
            + hosted_settings.profile.startup.readiness_timeout_seconds,
        )

    def test_oidc_adapter_closes_only_its_owned_http_client_once(self) -> None:
        settings = OidcSettings(
            issuer="https://identity.test",
            client_id="runtime-test",
            redirect_uri="https://testserver/memory/oidc/callback",
            public_origin="https://testserver",
        )
        owned_client = mock.Mock()
        with mock.patch(
            "aioa_memory_kernel.personal_memory_ui.auth.httpx.Client",
            return_value=owned_client,
        ):
            owned = HttpxOidcClient(settings)
        owned.close()
        owned.close()
        owned_client.close.assert_called_once_with()

        injected_client = mock.Mock()
        injected = HttpxOidcClient(settings, client=injected_client)
        injected.close()
        injected_client.close.assert_not_called()

    def test_canonical_asgi_import_is_network_free_and_lazy(self) -> None:
        module_name = "aioa_memory_kernel.demo_runtime.asgi"
        sys.modules.pop(module_name, None)
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch(
            "urllib.request.urlopen", side_effect=AssertionError("network call")
        ):
            module = importlib.import_module(module_name)
        self.assertIsInstance(module.app, FastAPI)
        self.assertFalse(module.app.state.runtime_controller.started)

    def test_step35_factory_and_routes_are_reused_once(self) -> None:
        settings = RuntimeSettings.from_mapping(_environment())
        dependencies, _, _ = _dependencies()
        with mock.patch.object(
            composition,
            "create_personal_memory_app",
            wraps=step35_app_factory,
        ) as factory:
            app = create_demo_runtime_app(
                settings=settings,
                dependency_factory=_Factory(dependencies),
            )
        factory.assert_called_once()
        paths = {getattr(route, "path", None) for route in app.routes}
        self.assertTrue({"/", "/memory", "/memory/login"}.issubset(paths))
        self.assertNotIn("FastAPI(", inspect.getsource(composition))

    def test_startup_order_is_exact_and_provider_is_not_called(self) -> None:
        settings = RuntimeSettings.from_mapping(_environment())
        dependencies, provider, _ = _dependencies()
        factory = _Factory(dependencies)
        app = create_demo_runtime_app(settings=settings, dependency_factory=factory)
        with TestClient(app, base_url="https://testserver") as client:
            response = client.get("/", follow_redirects=False)
            self.assertEqual(response.status_code, 303)
            self.assertEqual(
                app.state.runtime_controller.startup_trace,
                STARTUP_ORDER,
            )
            self.assertTrue(app.state.runtime_controller.accepting_work)
        self.assertEqual(provider.generate_calls, 0)
        self.assertEqual(factory.initialize_calls, 1)

    def test_hosted_mode_rejects_memory_session_store(self) -> None:
        settings = RuntimeSettings.from_mapping(_environment(RuntimeMode.HOSTED_DEMO))
        database = _Closeable("database")
        dependencies, _, _ = _dependencies(database_resources=(database,))
        app = create_demo_runtime_app(
            settings=settings,
            dependency_factory=_Factory(dependencies),
        )
        with self.assertRaises(RuntimeAssemblyError) as raised:
            with TestClient(app, base_url="https://testserver"):
                pass
        self.assertEqual(
            raised.exception.code,
            RuntimeErrorCode.UNSAFE_TEST_ADAPTER_IN_HOSTED_MODE,
        )
        self.assertEqual(database.close_count, 1)

    def test_hosted_mode_rejects_missing_database_resource(self) -> None:
        settings = RuntimeSettings.from_mapping(_environment(RuntimeMode.HOSTED_DEMO))
        dependencies, _, _ = _dependencies(
            session_store=_DurableSessionStub(),
            session_class=SessionStorageClass.DURABLE,
        )
        app = create_demo_runtime_app(
            settings=settings,
            dependency_factory=_Factory(dependencies),
        )
        with self.assertRaises(RuntimeAssemblyError) as raised:
            with TestClient(app, base_url="https://testserver"):
                pass
        self.assertEqual(raised.exception.code, RuntimeErrorCode.DEPENDENCY_MISSING)

    def test_default_factory_fails_closed_without_r3_dependencies(self) -> None:
        settings = RuntimeSettings.from_mapping(_environment(RuntimeMode.HOSTED_DEMO))
        app = create_demo_runtime_app(
            settings=settings,
            dependency_factory=MissingRuntimeDependencyFactory(),
        )
        with self.assertRaises(RuntimeAssemblyError) as raised:
            with TestClient(app, base_url="https://testserver"):
                pass
        self.assertEqual(raised.exception.code, RuntimeErrorCode.DEPENDENCY_MISSING)

    def test_partial_startup_failure_cleans_factory_owned_resource(self) -> None:
        settings = RuntimeSettings.from_mapping(_environment())
        factory = _PartialFailingFactory()
        app = create_demo_runtime_app(settings=settings, dependency_factory=factory)
        with self.assertRaises(RuntimeAssemblyError) as raised:
            with TestClient(app, base_url="https://testserver"):
                pass
        self.assertEqual(raised.exception.code, RuntimeErrorCode.STARTUP_FAILED)
        self.assertEqual(factory.partial.close_count, 1)

    def test_startup_timeout_is_bounded_and_runs_partial_cleanup(self) -> None:
        factory = _HangingFactory()
        app = create_demo_runtime_app(
            settings=_settings_with_one_second_lifecycle_timeout(),
            dependency_factory=factory,
        )
        started = time.monotonic()
        with self.assertRaises(RuntimeAssemblyError) as raised:
            with TestClient(app, base_url="https://testserver"):
                pass
        self.assertLess(time.monotonic() - started, 3.0)
        self.assertEqual(raised.exception.code, RuntimeErrorCode.STARTUP_FAILED)
        self.assertEqual(factory.cleanup_calls, 1)

    def test_shutdown_timeout_is_bounded_and_sanitized(self) -> None:
        settings = _settings_with_one_second_lifecycle_timeout()
        dependencies, _, _ = _dependencies()
        dependencies = dataclasses.replace(
            dependencies,
            owned_background_resources=(_HangingCloseable(),),
        )
        app = create_demo_runtime_app(
            settings=settings,
            dependency_factory=_Factory(dependencies),
        )
        started = time.monotonic()
        with self.assertRaises(RuntimeAssemblyError) as raised:
            with TestClient(app, base_url="https://testserver"):
                pass
        self.assertLess(time.monotonic() - started, 3.0)
        self.assertEqual(raised.exception.code, RuntimeErrorCode.SHUTDOWN_FAILED)

    def test_shutdown_is_idempotent_and_closes_only_owned_resources(self) -> None:
        close_log: list[str] = []
        database = _Closeable("database", close_log)
        unowned = _Closeable("unowned", close_log)
        dependencies, provider, oidc = _dependencies(
            database_resources=(database,),
            close_log=close_log,
        )
        settings = RuntimeSettings.from_mapping(_environment())
        app = create_demo_runtime_app(
            settings=settings,
            dependency_factory=_Factory(dependencies),
        )
        with TestClient(app, base_url="https://testserver"):
            pass
        self.assertEqual(close_log, ["provider", "oidc", "database"])
        self.assertEqual(provider.close_count, 1)
        self.assertEqual(oidc.close_count, 1)
        self.assertEqual(database.close_count, 1)
        self.assertEqual(unowned.close_count, 0)
        asyncio.run(app.state.runtime_controller.shutdown())
        self.assertEqual(close_log, ["provider", "oidc", "database"])


if __name__ == "__main__":
    unittest.main()
