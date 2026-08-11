"""Security, OIDC, XSS, CSRF, and static boundary tests for Step 35."""

from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from aioa_memory_kernel.personal_memory_ui import (
    HttpxOidcClient,
    MemoryOwnerSessionStore,
    OidcSettings,
    OwnerPrincipal,
    pkce_challenge,
)
from aioa_memory_kernel.personal_memory_ui.auth import SESSION_COOKIE_NAME
from tests.test_step35_personal_memory_ui import (
    FakeBackend,
    FakeOidcClient,
    OWNER_A,
    create_personal_memory_app,
)


ROOT = Path(__file__).resolve().parents[1]


class PersonalMemoryUiSecurityTest(unittest.TestCase):
    def app_client(self, backend=None):
        oidc = FakeOidcClient()
        store = MemoryOwnerSessionStore(maximum_sessions=20)
        settings = OidcSettings(
            issuer="https://identity.example",
            client_id="personal-memory-ui",
            redirect_uri="https://testserver/memory/oidc/callback",
            public_origin="https://testserver",
        )
        app = create_personal_memory_app(
            backend=backend or FakeBackend(), oidc_client=oidc,
            oidc_settings=settings, session_store=store, clock=lambda: 1000.0,
        )
        return TestClient(app, base_url="https://testserver", raise_server_exceptions=False), oidc, store

    @staticmethod
    def login(client, oidc, principal=OWNER_A):
        oidc.next_principal = principal
        login = client.get("/memory/login", follow_redirects=False)
        state = parse_qs(urlparse(login.headers["location"]).query)["state"][0]
        callback = client.get(
            "/memory/oidc/callback", params={"code": "valid-code", "state": state},
            follow_redirects=False,
        )
        if callback.status_code != 303:
            raise AssertionError(callback.text)

    def test_session_store_keeps_only_hash_of_cookie_handle_and_expires(self):
        store = MemoryOwnerSessionStore(maximum_sessions=2)
        handle, session = store.create_session(OWNER_A, now=10.0)
        self.assertNotIn(handle, store._sessions)
        self.assertIs(store.get_session(handle, now=11.0), session)
        self.assertIsNone(store.get_session(handle, now=session.expires_at + 1))

    def test_oidc_pending_state_is_single_use(self):
        store = MemoryOwnerSessionStore(maximum_sessions=2)
        handle, pending = store.create_pending(return_path="//evil.example", now=10.0)
        self.assertEqual(pending.return_path, "/memory")
        self.assertEqual(store.consume_pending(handle, now=11.0), pending)
        self.assertIsNone(store.consume_pending(handle, now=11.0))

    def test_oidc_settings_require_https_and_openid(self):
        with self.assertRaises(ValueError):
            OidcSettings("http://idp", "client", "https://app/cb", "https://app")
        with self.assertRaises(ValueError):
            OidcSettings("https://idp", "client", "https://app/cb", "https://app", scopes=("profile",))

    def test_pkce_challenge_is_s256_and_not_the_verifier(self):
        value = pkce_challenge("A" * 64)
        self.assertEqual(len(value), 43)
        self.assertNotEqual(value, "A" * 64)

    def test_httpx_oidc_client_verifies_signature_issuer_audience_and_nonce(self):
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key()))
        jwk.update({"kid": "key-1", "use": "sig", "alg": "RS256"})
        now = datetime.now(UTC)
        token = jwt.encode(
            {
                "iss": "https://identity.example", "aud": "personal-memory-ui",
                "sub": "subject-a", "tenant_id": "tenant-a", "owner_user_id": "owner-a",
                "name": "Owner A", "nonce": "nonce-a", "iat": now,
                "exp": now + timedelta(minutes=5),
            },
            private_key, algorithm="RS256", headers={"kid": "key-1"},
        )

        def handler(request: httpx.Request):
            if request.url.path.endswith("openid-configuration"):
                return httpx.Response(200, json={
                    "issuer": "https://identity.example",
                    "authorization_endpoint": "https://identity.example/authorize",
                    "token_endpoint": "https://identity.example/token",
                    "jwks_uri": "https://identity.example/jwks",
                })
            if request.url.path == "/token":
                self.assertIn(b"code_verifier=verifier-a", request.content)
                return httpx.Response(200, json={"id_token": token, "access_token": "never-store-me"})
            if request.url.path == "/jwks":
                return httpx.Response(200, json={"keys": [jwk]})
            return httpx.Response(404)

        settings = OidcSettings(
            "https://identity.example", "personal-memory-ui",
            "https://app.example/memory/oidc/callback", "https://app.example",
        )
        client = HttpxOidcClient(
            settings, client=httpx.Client(transport=httpx.MockTransport(handler))
        )
        principal = client.authenticate(code="code-a", code_verifier="verifier-a", nonce="nonce-a")
        self.assertEqual(principal.tenant_id, OWNER_A.tenant_id)
        self.assertEqual(principal.owner_user_id, OWNER_A.owner_user_id)
        self.assertEqual(principal.oidc_subject, "subject-a")
        with self.assertRaises(ValueError):
            client.authenticate(code="code-a", code_verifier="verifier-a", nonce="wrong")
        self.assertFalse(hasattr(principal, "access_token"))

    def test_csrf_missing_and_cross_origin_requests_are_denied(self):
        client, oidc, _ = self.app_client()
        self.login(client, oidc)
        missing = client.post("/memory/logout", data={})
        self.assertEqual(missing.status_code, 400)
        page = client.get("/memory")
        token = page.text.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]
        cross_origin = client.post(
            "/memory/logout", data={"csrf_token": token},
            headers={"origin": "https://evil.example"},
        )
        self.assertEqual(cross_origin.status_code, 400)

    def test_stored_xss_is_escaped_and_never_becomes_markup(self):
        backend = FakeBackend()
        hostile = '<img src=x onerror="alert(1)"><script>alert(2)</script>'
        original = backend.patches[OWNER_A.owner_user_id][0]
        from dataclasses import replace
        backend.patches[OWNER_A.owner_user_id] = (replace(original, statement=hostile),) + backend.patches[OWNER_A.owner_user_id][1:]
        client, oidc, _ = self.app_client(backend)
        self.login(client, oidc)
        response = client.get("/memory")
        self.assertNotIn("<script>alert(2)</script>", response.text)
        self.assertNotIn("<img src=x onerror", response.text)
        self.assertIn("&lt;script&gt;alert(2)&lt;/script&gt;", response.text)
        self.assertNotIn("unsafe-inline", response.headers["content-security-policy"])

    def test_cookie_contains_no_identity_or_token_material(self):
        client, oidc, _ = self.app_client()
        self.login(client, oidc)
        cookie = client.cookies.get(SESSION_COOKIE_NAME)
        self.assertIsNotNone(cookie)
        for forbidden in ("tenant-a", "owner-a", "oidc-a", "bearer", "jwt"):
            self.assertNotIn(forbidden, cookie.lower())

    def test_no_credentials_or_direct_database_code_in_browser_package(self):
        package = ROOT / "src/aioa_memory_kernel/personal_memory_ui"
        text = "\n".join(
            path.read_text(encoding="utf-8", errors="ignore")
            for path in package.rglob("*") if path.is_file() and "htmx" not in path.name.lower()
        )
        for forbidden in (
            "BYPASSRLS", "COMMIT_HELPER_PASSWORD", "OPENROUTER_API_KEY",
            "AWS_SECRET_ACCESS_KEY", "GITHUB_TOKEN", "shell=True", "os.system(",
        ):
            self.assertNotIn(forbidden, text)
        web = (package / "web.py").read_text()
        self.assertNotIn("PersonalMemoryCommitHelper", web)
        self.assertNotIn("PersonalMemoryActivationService", web)

    def test_accessibility_and_responsive_contracts_are_present(self):
        template = (ROOT / "src/aioa_memory_kernel/personal_memory_ui/templates/slot.html").read_text()
        css = (ROOT / "src/aioa_memory_kernel/personal_memory_ui/static/app.css").read_text()
        self.assertIn('<label for="display-name">', template)
        self.assertIn('role="status"', (ROOT / "src/aioa_memory_kernel/personal_memory_ui/templates/base.html").read_text())
        self.assertIn("@media (max-width: 800px)", css)
        self.assertIn("@media (max-width: 520px)", css)
        self.assertIn(":focus-visible", css)
        self.assertIn("prefers-reduced-motion", css)

    def test_security_headers_and_mutation_body_bound_are_enforced(self):
        client, oidc, _ = self.app_client()
        self.login(client, oidc)
        page = client.get("/memory")
        self.assertIn("max-age=31536000", page.headers["strict-transport-security"])
        self.assertIn("camera=()", page.headers["permissions-policy"])
        token = page.text.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]
        response = client.post(
            "/memory/logout",
            data={"csrf_token": token, "padding": "x" * (33 * 1024)},
            headers={"origin": "https://testserver"},
        )
        self.assertEqual(response.status_code, 400)

    def test_step36_and_external_authority_are_absent(self):
        source = "\n".join(
            path.read_text(encoding="utf-8", errors="ignore")
            for path in (ROOT / "src/aioa_memory_kernel/personal_memory_ui").rglob("*.py")
        )
        self.assertNotIn("execute_action", source)
        self.assertNotIn("publish_source", source)
        self.assertNotIn("credential_architecture", source)
        self.assertNotIn("subprocess", source)


if __name__ == "__main__":
    unittest.main()
