"""Controlled loopback startup/render/shutdown proof for D1."""

from __future__ import annotations

import http.client
import socket
import threading
import time
import unittest

import uvicorn

from aioa_memory_kernel.demo_runtime.composition import (
    STARTUP_ORDER,
    RuntimeDependencies,
    SessionStorageClass,
    create_demo_runtime_app,
)
from aioa_memory_kernel.demo_runtime.config import RuntimeMode, RuntimeSettings
from aioa_memory_kernel.modeling.models import load_approved_provider_spec
from aioa_memory_kernel.personal_memory_ui import MemoryOwnerSessionStore
from aioa_memory_kernel.personal_memory_ui.auth import SESSION_COOKIE_NAME
from tests.test_demo_runtime_composition import _environment
from tests.test_step35_personal_memory_ui import FakeBackend, OWNER_A


class _Oidc:
    def authorization_url(self, **_values):
        return "https://identity.test/authorize"

    def authenticate(self, **_values):  # pragma: no cover - forbidden path
        raise AssertionError("loopback proof must not invoke OIDC")

    def close(self):
        return None


class _Provider:
    def __init__(self) -> None:
        self.generate_calls = 0

    def provider_identity(self):
        return load_approved_provider_spec().provider_identity()

    def generate(self, *_args, **_values):  # pragma: no cover - forbidden path
        self.generate_calls += 1
        raise AssertionError("D1 loopback proof must make no provider call")

    def close(self):
        return None


class _Factory:
    def __init__(self, dependencies: RuntimeDependencies) -> None:
        self.dependencies = dependencies

    def initialize(self, _settings, recorder):
        for stage in STARTUP_ORDER[1:-1]:
            recorder.advance(stage)
        return self.dependencies

    def cleanup_partial(self):
        return None


class UnifiedCockpitLoopbackTests(unittest.TestCase):
    def test_controlled_runtime_starts_renders_and_stops_on_loopback(self) -> None:
        settings = RuntimeSettings.from_mapping(_environment(RuntimeMode.TEST))
        store = MemoryOwnerSessionStore(maximum_sessions=10)
        handle, _ = store.create_session(OWNER_A, now=time.time())
        provider = _Provider()
        dependencies = RuntimeDependencies(
            backend=FakeBackend(),
            oidc_client=_Oidc(),
            session_store=store,
            provider_adapter=provider,
            session_storage_class=SessionStorageClass.TEST_ONLY,
            owned_provider_resources=(provider,),
        )
        app = create_demo_runtime_app(
            settings=settings,
            dependency_factory=_Factory(dependencies),
        )
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        server = uvicorn.Server(
            uvicorn.Config(
                app,
                host="127.0.0.1",
                port=port,
                lifespan="on",
                access_log=False,
                log_level="warning",
            )
        )
        thread = threading.Thread(
            target=server.run,
            kwargs={"sockets": [listener]},
            daemon=True,
        )
        thread.start()
        deadline = time.monotonic() + 5
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(server.started)
        try:
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
            connection.request("GET", "/health/live", headers={"Host": "testserver"})
            live = connection.getresponse()
            self.assertEqual(live.status, 200)
            live.read()
            connection.close()

            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
            connection.request("GET", "/health/ready", headers={"Host": "testserver"})
            ready = connection.getresponse()
            self.assertEqual(ready.status, 200)
            ready.read()
            connection.close()

            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
            connection.request(
                "GET",
                "/memory/demo",
                headers={
                    "Host": "testserver",
                    "Cookie": f"{SESSION_COOKIE_NAME}={handle}",
                },
            )
            cockpit = connection.getresponse()
            rendered = cockpit.read().decode("utf-8")
            self.assertEqual(cockpit.status, 200)
            self.assertIn("CURRENT / EVIDENCE-BOUND", rendered)
            connection.close()
            self.assertEqual(provider.generate_calls, 0)
        finally:
            server.should_exit = True
            thread.join(timeout=5)
            listener.close()
        self.assertFalse(thread.is_alive())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
