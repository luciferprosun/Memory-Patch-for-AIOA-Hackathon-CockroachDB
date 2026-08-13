"""Focused D1 security and authority tests for cockpit presentation."""

from __future__ import annotations

import unittest
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

from aioa_memory_kernel.demo_cockpit import (
    CockpitRuntimeStatus,
    CockpitShell,
    LegacyCompatibilityMode,
    build_legacy_archive_manifest,
)
from aioa_memory_kernel.personal_memory_ui import (
    MemoryOwnerSessionStore,
    OidcSettings,
    create_personal_memory_app,
)
from tests.test_step35_personal_memory_ui import (
    FakeBackend,
    FakeOidcClient,
    OWNER_A,
)


class UnifiedCockpitSecurityTests(unittest.TestCase):
    def _app(self, shell: CockpitShell, *, allow=True):
        backend = FakeBackend()
        oidc = FakeOidcClient()
        store = MemoryOwnerSessionStore(maximum_sessions=20)
        settings = OidcSettings(
            issuer="https://identity.example",
            client_id="personal-memory-ui",
            redirect_uri="https://testserver/memory/oidc/callback",
            public_origin="https://testserver",
        )
        app = create_personal_memory_app(
            backend=backend,
            oidc_client=oidc,
            oidc_settings=settings,
            session_store=store,
            clock=lambda: 1000.0,
            principal_authorizer=lambda _principal: allow,
            cockpit_shell=shell,
        )
        client = TestClient(
            app,
            base_url="https://testserver",
            raise_server_exceptions=False,
        )
        return app, client, backend, oidc

    @staticmethod
    def _login(client: TestClient, oidc: FakeOidcClient) -> int:
        oidc.next_principal = OWNER_A
        response = client.get("/memory/login", follow_redirects=False)
        state = parse_qs(urlparse(response.headers["location"]).query)["state"][0]
        callback = client.get(
            "/memory/oidc/callback",
            params={"code": "valid-code", "state": state},
            follow_redirects=False,
        )
        return callback.status_code

    def test_untrusted_status_text_is_autoescaped(self) -> None:
        hostile = '<img src=x onerror="alert(1)">'
        runtime = CockpitRuntimeStatus(
            profile_id=hostile,
            asgi_application="aioa_memory_kernel.demo_runtime.asgi:app",
            authentication="OIDC + PKCE",
            session_backend="OwnerSessionStore",
            database="CockroachDB",
            provider="OpenRouter",
            provider_guard="GuardedProviderAdapter",
            readiness_contract="health endpoints",
        )
        _, client, _, oidc = self._app(
            CockpitShell(
                runtime,
                legacy_mode=LegacyCompatibilityMode.ARCHIVAL_VIEW,
                legacy_archive=build_legacy_archive_manifest(),
            )
        )
        self.assertEqual(self._login(client, oidc), 303)
        response = client.get("/memory/demo")
        self.assertNotIn(hostile, response.text)
        self.assertIn("&lt;img src=x onerror=", response.text)
        self.assertNotIn("<img src=x", response.text.casefold())

    def test_disallowed_authenticated_identity_gets_no_session(self) -> None:
        runtime = CockpitRuntimeStatus(
            profile_id="profile",
            asgi_application="app",
            authentication="OIDC + PKCE",
            session_backend="OwnerSessionStore",
            database="CockroachDB",
            provider="OpenRouter",
            provider_guard="Provider guard",
            readiness_contract="health endpoints",
        )
        _, client, _, oidc = self._app(CockpitShell(runtime), allow=False)
        self.assertEqual(self._login(client, oidc), 401)
        response = client.get("/memory/demo", follow_redirects=False)
        self.assertEqual(response.status_code, 303)

    def test_html_contains_no_privileged_secret_or_legacy_authority_path(self) -> None:
        runtime = CockpitRuntimeStatus(
            profile_id="memory-patch-4gb-demo-1a",
            asgi_application="aioa_memory_kernel.demo_runtime.asgi:app",
            authentication="OIDC + PKCE",
            session_backend="CockroachOwnerSessionStore",
            database="CockroachDB",
            provider="openrouter / moonshotai/kimi-k2",
            provider_guard="GuardedProviderAdapter",
            readiness_contract="/health/live + /health/ready",
        )
        app, client, backend, oidc = self._app(
            CockpitShell(
                runtime,
                legacy_mode=LegacyCompatibilityMode.ARCHIVAL_VIEW,
                legacy_archive=build_legacy_archive_manifest(),
            )
        )
        self.assertEqual(self._login(client, oidc), 303)
        response = client.get(
            "/memory/demo", params={"mode": "critical_prompt_loop"}
        )
        sentinels = (
            "d1-provider-sentinel-value",
            "d1-database-sentinel-value",
            "d1-oidc-sentinel-value",
            "d1-commit-helper-sentinel-value",
        )
        for sentinel in sentinels:
            self.assertNotIn(sentinel, response.text)
        self.assertIn("frame-ancestors 'none'", response.headers["content-security-policy"])
        self.assertEqual(response.headers["x-frame-options"], "DENY")
        self.assertEqual(backend.calls, [])
        demo_routes = [route for route in app.routes if route.path.startswith("/memory/demo")]
        self.assertEqual(len(demo_routes), 1)
        self.assertEqual(demo_routes[0].methods, {"GET"})


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
