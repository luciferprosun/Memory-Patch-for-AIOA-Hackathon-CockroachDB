"""Focused R6 liveness, readiness, transition and sanitization tests."""

from __future__ import annotations

import asyncio
import unittest

from fastapi.testclient import TestClient

from tests.test_demo_runtime_composition import (  # reuse the exact R2 fakes
    _Factory,
    _dependencies,
    _environment,
)

from aioa_memory_kernel.demo_runtime.composition import create_demo_runtime_app
from aioa_memory_kernel.demo_runtime.config import (
    RuntimeAssemblyError,
    RuntimeErrorCode,
    RuntimeSettings,
)
from aioa_memory_kernel.demo_runtime.health import (
    RuntimeReadinessPhase,
    RuntimeReadinessReason,
)


class _FailingFactory:
    def initialize(self, settings, recorder):
        raise RuntimeAssemblyError(RuntimeErrorCode.DATABASE_MIGRATION_FAILED)

    def cleanup_partial(self):
        return None


class _ShutdownObserver:
    def __init__(self) -> None:
        self.controller = None
        self.phase_seen = None

    def close(self) -> None:
        self.phase_seen = self.controller.readiness.phase


def _app(*, factory=None, dependencies=None):
    settings = RuntimeSettings.from_mapping(_environment())
    if dependencies is None:
        dependencies, provider, _ = _dependencies()
    else:
        provider = dependencies.provider_adapter
    application = create_demo_runtime_app(
        settings=settings,
        dependency_factory=factory or _Factory(dependencies),
    )
    return application, provider


class RuntimeHealthTests(unittest.TestCase):
    def test_imported_process_is_live_but_not_ready_before_lifespan(self) -> None:
        app, provider = _app()
        client = TestClient(app, base_url="https://testserver")
        live = client.get("/health/live")
        ready = client.get("/health/ready")
        self.assertEqual(live.status_code, 200)
        self.assertEqual(live.json(), {"status": "LIVE"})
        self.assertEqual(ready.status_code, 503)
        self.assertEqual(
            ready.json(),
            {"status": "NOT_READY", "reason": "STARTUP_INCOMPLETE"},
        )
        self.assertEqual(provider.generate_calls, 0)

    def test_all_initialized_dependencies_make_ready_without_provider_call(self) -> None:
        app, provider = _app()
        with TestClient(app, base_url="https://testserver") as client:
            self.assertEqual(client.get("/health/live").status_code, 200)
            ready = client.get("/health/ready")
            self.assertEqual(ready.status_code, 200)
            self.assertEqual(ready.json(), {"status": "READY"})
            snapshot = app.state.runtime_controller.readiness
            self.assertTrue(snapshot.ready)
            self.assertEqual(tuple(sorted(snapshot.dependency_ids)), snapshot.dependency_ids)
        self.assertEqual(provider.generate_calls, 0)

    def test_detected_dependency_failures_remove_traffic_readiness(self) -> None:
        reasons = (
            RuntimeReadinessReason.DATABASE_UNAVAILABLE,
            RuntimeReadinessReason.SESSION_STORE_UNAVAILABLE,
            RuntimeReadinessReason.AUTH_CONFIGURATION_INVALID,
            RuntimeReadinessReason.PROVIDER_CONFIGURATION_INVALID,
            RuntimeReadinessReason.PROVIDER_GUARD_UNAVAILABLE,
        )
        for reason in reasons:
            with self.subTest(reason=reason):
                app, provider = _app()
                with TestClient(app, base_url="https://testserver") as client:
                    app.state.runtime_controller.report_dependency_failure(reason)
                    response = client.get("/health/ready")
                    self.assertEqual(response.status_code, 503)
                    self.assertEqual(response.json()["reason"], reason.value)
                    with self.assertRaises(RuntimeAssemblyError):
                        _ = app.state.runtime_controller.provider_adapter
                self.assertEqual(provider.generate_calls, 0)

    def test_migration_startup_failure_is_sanitized_and_not_ready(self) -> None:
        app, provider = _app(factory=_FailingFactory())
        with self.assertRaises(RuntimeAssemblyError) as raised:
            asyncio.run(app.state.runtime_controller.start())
        self.assertEqual(
            raised.exception.code,
            RuntimeErrorCode.DATABASE_MIGRATION_FAILED,
        )
        snapshot = app.state.runtime_controller.readiness
        self.assertIs(snapshot.phase, RuntimeReadinessPhase.FAILED)
        self.assertIs(snapshot.reason, RuntimeReadinessReason.DATABASE_UNAVAILABLE)
        client = TestClient(app, base_url="https://testserver")
        payload = client.get("/health/ready").json()
        self.assertNotIn("postgresql://", str(payload))
        self.assertNotIn("synthetic", str(payload).casefold())
        self.assertEqual(provider.generate_calls, 0)

    def test_shutdown_marks_not_ready_before_owned_resource_teardown(self) -> None:
        observer = _ShutdownObserver()
        dependencies, provider, _ = _dependencies()
        dependencies = type(dependencies)(
            backend=dependencies.backend,
            oidc_client=dependencies.oidc_client,
            session_store=dependencies.session_store,
            provider_adapter=dependencies.provider_adapter,
            session_storage_class=dependencies.session_storage_class,
            principal_authorizer=dependencies.principal_authorizer,
            owned_background_resources=(observer,),
            owned_provider_resources=dependencies.owned_provider_resources,
            owned_session_resources=dependencies.owned_session_resources,
            owned_database_resources=dependencies.owned_database_resources,
        )
        app, _ = _app(dependencies=dependencies)
        observer.controller = app.state.runtime_controller
        with TestClient(app, base_url="https://testserver") as client:
            self.assertEqual(client.get("/health/ready").status_code, 200)
        self.assertIs(observer.phase_seen, RuntimeReadinessPhase.STOPPING)
        self.assertFalse(app.state.runtime_controller.accepting_work)
        self.assertEqual(provider.generate_calls, 0)

    def test_health_payload_never_exposes_secret_or_topology(self) -> None:
        app, _provider = _app()
        sentinel = "r6-health-secret-sentinel"
        with TestClient(app, base_url="https://testserver") as client:
            combined = repr(client.get("/health/live").json()) + repr(
                client.get("/health/ready").json()
            )
        self.assertNotIn(sentinel, combined)
        self.assertNotIn("postgresql://", combined)
        self.assertNotIn("openrouter", combined.casefold())


if __name__ == "__main__":
    unittest.main()
