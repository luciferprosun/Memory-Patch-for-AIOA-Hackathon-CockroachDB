"""Step 41 adversarial authentication, browser, and input-boundary regressions."""

from __future__ import annotations

import inspect
import json
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SCRIPTS = ROOT / "scripts"
for import_root in (SRC, SCRIPTS):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from fastapi.testclient import TestClient
import httpx

from aioa_memory_kernel.personal_memory_ui import (
    MemoryOwnerSessionStore,
    OidcSettings,
    HttpxOidcClient,
    PersonalMemoryUiReadRepository,
    create_personal_memory_app,
)
from aioa_memory_kernel.personal_memory_ui import web as ui_web
from aioa_memory_kernel.personal_memory_ui.auth import SESSION_COOKIE_NAME
from aioa_memory_kernel.security.redaction import assert_secret_free
from aioa_memory_kernel.contracts.serialization import canonical_json, canonical_sha256

import run_step41_full_security_regression_validation as step41_validation
import run_step37_failure_recovery_validation as step37_validation
import run_cockroachdb_migrations as cockroach_validation
from tests.test_step35_personal_memory_ui import (
    FakeBackend,
    FakeOidcClient,
    OWNER_A,
)

class Step41UiSecurityCampaignTest(unittest.TestCase):
    def app_client(self):
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
        )
        client = TestClient(
            app,
            base_url="https://testserver",
            raise_server_exceptions=False,
        )
        return client, oidc, store, backend

    @staticmethod
    def login(client: TestClient, oidc: FakeOidcClient) -> str:
        login = client.get("/memory/login", follow_redirects=False)
        state = parse_qs(urlparse(login.headers["location"]).query)["state"][0]
        callback = client.get(
            "/memory/oidc/callback",
            params={"code": "valid-code", "state": state},
            follow_redirects=False,
        )
        if callback.status_code != 303:
            raise AssertionError(callback.text)
        page = client.get("/memory")
        return page.text.split('name="csrf_token" value="', 1)[1].split('"', 1)[0]

    def test_oidc_redirect_and_public_origin_are_exactly_bound(self):
        invalid = (
            {
                "redirect_uri": "https://evil.example/memory/oidc/callback",
                "public_origin": "https://app.example",
            },
            {
                "redirect_uri": "https://app.example/other/callback",
                "public_origin": "https://app.example",
            },
            {
                "redirect_uri": "https://app.example/memory/oidc/callback",
                "public_origin": "https://app.example/untrusted/path",
            },
            {
                "redirect_uri": "https://user@app.example/memory/oidc/callback",
                "public_origin": "https://app.example",
            },
        )
        for values in invalid:
            with self.subTest(values=values), self.assertRaises(ValueError):
                OidcSettings(
                    issuer="https://identity.example",
                    client_id="personal-memory-ui",
                    **values,
                )
        valid = OidcSettings(
            issuer="https://identity.example/tenant",
            client_id="personal-memory-ui",
            redirect_uri="https://app.example:443/memory/oidc/callback",
            public_origin="https://app.example",
        )
        self.assertEqual(valid.public_origin, "https://app.example")

    def test_oidc_metadata_rejects_embedded_credentials_and_fragments(self):
        settings = OidcSettings(
            issuer="https://identity.example",
            client_id="personal-memory-ui",
            redirect_uri="https://app.example/memory/oidc/callback",
            public_origin="https://app.example",
        )
        for endpoint in (
            "https://user@identity.example/authorize",
            "https://identity.example/authorize#fragment",
        ):
            with self.subTest(endpoint=endpoint):
                client = HttpxOidcClient(
                    settings,
                    client=httpx.Client(
                        transport=httpx.MockTransport(
                            lambda request: httpx.Response(
                                200,
                                json={
                                    "issuer": settings.issuer,
                                    "authorization_endpoint": endpoint,
                                    "token_endpoint": "https://identity.example/token",
                                    "jwks_uri": "https://identity.example/jwks",
                                },
                            )
                        )
                    ),
                )
                with self.assertRaises(ValueError):
                    client.authorization_url(
                        state="state", nonce="nonce", code_challenge="challenge"
                    )

    def test_oidc_json_is_strict_and_bounded_before_parsing(self):
        settings = OidcSettings(
            issuer="https://identity.example",
            client_id="personal-memory-ui",
            redirect_uri="https://app.example/memory/oidc/callback",
            public_origin="https://app.example",
        )
        responses = (
            (
                b'{"issuer":"https://identity.example",'
                b'"issuer":"https://identity.example"}',
                "duplicate",
            ),
            (b"{" + b" " * (256 * 1024) + b"}", "oversized"),
            (b'{"issuer":NaN}', "non-finite"),
        )
        for content, label in responses:
            with self.subTest(label=label):
                client = HttpxOidcClient(
                    settings,
                    client=httpx.Client(
                        transport=httpx.MockTransport(
                            lambda request, content=content: httpx.Response(
                                200, content=content
                            )
                        )
                    ),
                )
                with self.assertRaises(ValueError):
                    client.authorization_url(
                        state="state", nonce="nonce", code_challenge="challenge"
                    )

    def test_host_header_is_fail_closed_without_redirect(self):
        client, _, _, _ = self.app_client()
        response = client.get(
            "/memory/login",
            headers={"host": "evil.example"},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("location", response.headers)
        self.assertEqual(response.headers["x-frame-options"], "DENY")

    def test_callback_state_is_single_use_and_login_rotates_existing_session(self):
        client, _, store, _ = self.app_client()
        old_handle, _ = store.create_session(OWNER_A, now=1000.0)
        client.cookies.set(SESSION_COOKIE_NAME, old_handle, path="/memory")
        login = client.get("/memory/login", follow_redirects=False)
        state = parse_qs(urlparse(login.headers["location"]).query)["state"][0]
        forged = client.get(
            "/memory/oidc/callback",
            params={"code": "valid-code", "state": state + "-forged"},
            follow_redirects=False,
        )
        replay = client.get(
            "/memory/oidc/callback",
            params={"code": "valid-code", "state": state},
            follow_redirects=False,
        )
        self.assertEqual(forged.status_code, 401)
        self.assertEqual(replay.status_code, 401)
        self.assertIsNotNone(store.get_session(old_handle, now=1000.0))

        login = client.get("/memory/login", follow_redirects=False)
        state = parse_qs(urlparse(login.headers["location"]).query)["state"][0]
        accepted = client.get(
            "/memory/oidc/callback",
            params={"code": "valid-code", "state": state},
            follow_redirects=False,
        )
        self.assertEqual(accepted.status_code, 303)
        self.assertIsNone(store.get_session(old_handle, now=1000.0))
        self.assertNotIn(old_handle, accepted.headers["set-cookie"])

    def test_oidc_callback_rejects_duplicate_code_or_state_parameters(self):
        client, _, _, _ = self.app_client()
        for duplicate_name in ("code", "state"):
            with self.subTest(duplicate_name=duplicate_name):
                login = client.get("/memory/login", follow_redirects=False)
                state = parse_qs(urlparse(login.headers["location"]).query)["state"][0]
                parameters = [("code", "valid-code"), ("state", state)]
                parameters.append(
                    (duplicate_name, "attacker-controlled-duplicate")
                )
                response = client.get(
                    "/memory/oidc/callback",
                    params=parameters,
                    follow_redirects=False,
                )
                self.assertEqual(response.status_code, 401)
                self.assertNotIn(SESSION_COOKIE_NAME, response.headers.get("set-cookie", ""))

    def test_return_path_is_a_bounded_local_memory_path(self):
        store = MemoryOwnerSessionStore(maximum_sessions=20)
        for hostile in (
            "/memoryevil",
            "//evil.example/memory",
            "/memory/../admin",
            "/memory/%2e%2e/admin",
            "/memory\\evil.example",
            "/memory#fragment",
            "/memory\x00other",
        ):
            with self.subTest(hostile=hostile):
                _, pending = store.create_pending(return_path=hostile, now=1000.0)
                self.assertEqual(pending.return_path, "/memory")
        _, pending = store.create_pending(
            return_path="/memory/slots/space-a?view=active", now=1000.0
        )
        self.assertEqual(pending.return_path, "/memory/slots/space-a?view=active")

    def test_mutation_parser_rejects_duplicates_invalid_utf8_and_multipart(self):
        client, oidc, _, backend = self.app_client()
        token = self.login(client, oidc)
        duplicate = client.post(
            "/memory/logout",
            content=f"csrf_token={token}&csrf_token={token}",
            headers={"content-type": "application/x-www-form-urlencoded"},
        )
        invalid_utf8 = client.post(
            "/memory/logout",
            content=b"csrf_token=" + token.encode("ascii") + b"&value=%FF",
            headers={"content-type": "application/x-www-form-urlencoded"},
        )
        multipart = client.post(
            "/memory/logout",
            files={"csrf_token": (None, token)},
        )
        self.assertEqual(duplicate.status_code, 400)
        self.assertEqual(invalid_utf8.status_code, 400)
        self.assertEqual(multipart.status_code, 400)
        malformed_percent = client.post(
            "/memory/logout",
            content=f"csrf_token={token}&value=%ZZ",
            headers={"content-type": "application/x-www-form-urlencoded"},
        )
        self.assertEqual(malformed_percent.status_code, 400)
        self.assertEqual(backend.calls, [])

    def test_control_characters_and_unbounded_integers_never_reach_backend(self):
        client, oidc, _, backend = self.app_client()
        token = self.login(client, oidc)
        control = client.post(
            "/memory/slots/space-owner-a/configure",
            data={
                "csrf_token": token,
                "display_name": "unsafe\x00name",
                "expected_state_version": "3",
                "expected_configuration_version": "2",
            },
        )
        enormous = client.post(
            "/memory/proposals/proposal-owner-a-pending/approve",
            data={
                "csrf_token": token,
                "expected_state_version": "9" * 1000,
                "proposal_hash": "a" * 64,
                "expected_state_hash": "b" * 64,
                "idempotency_key": "step41-malformed-number",
            },
        )
        self.assertEqual(control.status_code, 400)
        self.assertEqual(enormous.status_code, 400)
        self.assertEqual(backend.calls, [])

    def test_body_is_streamed_under_a_hard_bound_and_htmx_eval_is_disabled(self):
        source = inspect.getsource(ui_web.create_personal_memory_app)
        self.assertIn("async for chunk in request.stream()", source)
        self.assertNotIn("await request.body()", source)
        client, oidc, _, _ = self.app_client()
        self.login(client, oidc)
        page = client.get("/memory")
        self.assertIn('"allowEval":false', page.text)
        self.assertIn('"allowScriptTags":false', page.text)
        self.assertNotIn("unsafe-eval", page.headers["content-security-policy"])

    def test_sql_injection_shaped_owner_values_remain_parameters(self):
        class CapturingTransaction:
            def __init__(self) -> None:
                self.calls: list[tuple[str, tuple[object, ...]]] = []

            def fetch_all(self, sql, parameters):
                self.calls.append((sql, parameters))
                return ()

        malicious = "owner-a' OR 1=1; DROP TABLE memory_patch.memory_items;--"
        transaction = CapturingTransaction()
        self.assertEqual(
            PersonalMemoryUiReadRepository.list_patch_records(
                transaction,
                tenant_id="tenant-a' UNION SELECT current_user;--",
                owner_user_id=malicious,
                personal_memory_space_id="space-a'; DELETE FROM audit;--",
                maximum_results=20,
            ),
            (),
        )
        sql, parameters = transaction.calls[0]
        self.assertNotIn(malicious, sql)
        self.assertEqual(sql.count("%s"), 4)
        self.assertEqual(
            parameters,
            (
                "tenant-a' UNION SELECT current_user;--",
                malicious,
                "space-a'; DELETE FROM audit;--",
                20,
            ),
        )

    def test_runtime_source_avoids_known_high_risk_primitives(self):
        forbidden = (
            "shell=True",
            "os.system(",
            "pickle.loads(",
            "pickle.load(",
            "trust_remote_code=True",
            "debug=True",
        )
        for path in sorted((ROOT / "src/aioa_memory_kernel").rglob("*.py")):
            source = path.read_text(encoding="utf-8")
            for marker in forbidden:
                with self.subTest(path=path.relative_to(ROOT), marker=marker):
                    self.assertNotIn(marker, source)

    def test_step41_security_metadata_names_do_not_hide_secret_values(self):
        safe = {
            "credential_matrix_digest": "a" * 64,
            "credential_boundary_summary": {"status": "PASS"},
            "secret_scan_summary": {"leakage_count": 0},
        }
        assert_secret_free(safe, surface="STEP41_TEST", reject_machine_paths=True)
        unsafe = dict(safe)
        unsafe["secret_scan_summary"] = {
            "status": "Authorization: Bearer deliberately-not-a-real-token"
        }
        with self.assertRaises(ValueError):
            assert_secret_free(
                unsafe,
                surface="STEP41_TEST",
                reject_machine_paths=True,
            )


class Step41ValidationContractTest(unittest.TestCase):
    def test_committed_evidence_is_canonical_sanitized_and_closure_eligible(self):
        path = (
            ROOT
            / "docs/evidence/security/step41-full-security-regression-validation.json"
        )
        raw = path.read_bytes()
        payload = json.loads(raw.decode("utf-8", errors="strict"))
        self.assertEqual(raw, (canonical_json(payload) + "\n").encode("utf-8"))
        self.assertEqual(payload["step"], 41)
        self.assertEqual(
            payload["status"], "PASS_FULL_SECURITY_REGRESSION_CONTROLLED"
        )
        self.assertTrue(payload["closure_eligible"])
        self.assertEqual(
            canonical_sha256(
                payload, exclude_fields=("final_validation_digest",)
            ),
            payload["final_validation_digest"],
        )
        self.assertFalse(payload["step42_started"])
        for counter in (
            "secret_leakage_count",
            "cross_tenant_unauthorized_success",
            "cross_owner_unauthorized_success",
            "idor_success",
            "sql_injection_success",
            "csrf_bypass_success",
            "xss_execution_success",
            "authority_escalation_success",
            "commit_helper_approval_bypass_success",
            "critic_authority_escalation_success",
            "unauthorized_canonical_evidence_inclusion",
            "known_bad_draft_v1_fail_open",
            "audit_tamper_undetected_for_tested_cases",
            "production_resources_touched",
        ):
            self.assertEqual(payload[counter], 0, counter)
        assert_secret_free(
            payload,
            surface="STEP41_COMMITTED_EVIDENCE",
            reject_machine_paths=True,
        )
        roadmap = (ROOT / "docs/roadmap/PRODUCTION_ROADMAP.md").read_text(
            encoding="utf-8"
        )
        agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("- [x] **Step 41", roadmap)
        self.assertIn("- [x] **Step 42", roadmap)
        self.assertIn("- [ ] **Step 43", roadmap)
        self.assertIn("Step 41: COMPLETE AND PUSHED", roadmap)
        self.assertIn("Step 42: COMPLETE AND PUSHED", roadmap)
        self.assertIn("Step 43: NOT STARTED", roadmap)
        self.assertIn("Step 42 completion does not authorize Step 43.", roadmap)
        self.assertIn("Step 41: COMPLETE AND PUSHED", agents)
        self.assertIn("Step 42: COMPLETE AND PUSHED", agents)
        self.assertIn("Step 43: NOT STARTED", agents)
        self.assertIn("Step 42 completion does not authorize Step 43.", agents)

    def test_step37_live_probe_reuses_owned_transport_without_a_cli_child(self):
        source = inspect.getsource(step37_validation._disposable_cockroachdb_validation)
        self.assertNotIn("migrations.SqlClient(", source)
        self.assertIn("_expect_owned_pgwire_error(", source)
        self.assertIn("_expect_owned_sql_error(", source)

        class FailingOwnedClient:
            @staticmethod
            def execute(database, sql, *, timeout):
                raise cockroach_validation.SqlError(
                    "synthetic owned failure", sqlstate="23505"
                )

        self.assertEqual(
            step37_validation._expect_owned_sql_error(
                FailingOwnedClient(),
                "mp_step37_contract_db",
                "INSERT INTO owned_probe VALUES ('duplicate')",
                expected_sqlstate="23505",
                timeout=30,
            ),
            "23505",
        )

    def test_security_matrix_is_closed_complete_and_deterministic(self):
        expected = {
            "AUTH_SESSION",
            "CSRF",
            "XSS",
            "IDOR",
            "SQL_INJECTION",
            "RLS_FORCE_RLS",
            "CREDENTIAL_SEPARATION",
            "PROVIDER_BOUNDARY",
            "SOURCE_AUTHORITY",
            "TEMPORAL_CONFLICT",
            "CORRECTION_VERIFIER",
            "PERSONAL_MEMORY",
            "COMMIT_HELPER",
            "AUDIT",
            "HUMAN_REVIEW",
            "CRITIC_OPTIONALITY",
            "FAILURE_RECOVERY",
            "GERMAN_LAW_E2E",
            "RESOURCE_4GB",
        }
        controls = tuple(
            item["control_id"] for item in step41_validation.SECURITY_MATRIX
        )
        self.assertEqual(set(controls), expected)
        self.assertEqual(len(controls), len(set(controls)))
        self.assertEqual(
            canonical_sha256(step41_validation.SECURITY_MATRIX),
            "bf207380f82b9d3232fc3e46172a12e88ab4cee12ada6e9b8227f1af1db487e8",
        )

    def test_capture_loader_requires_a_canonical_secret_free_digest(self):
        payload = {"step": 99, "status": "PASS", "leakage_count": 0}
        payload["validation_digest"] = canonical_sha256(payload)
        with tempfile.TemporaryDirectory(prefix="step41-test-") as temporary:
            path = Path(temporary) / "capture.jsonl"
            path.write_text(canonical_json(payload) + "\n", encoding="utf-8")
            loaded, file_hash = step41_validation._load_capture(path, step=99)
            self.assertEqual(loaded, payload)
            self.assertEqual(len(file_hash), 64)
            tampered = dict(payload)
            tampered["status"] = "FAILED"
            path.write_text(json.dumps(tampered) + "\n", encoding="utf-8")
            with self.assertRaises(step41_validation.Step41ValidationError):
                step41_validation._load_capture(path, step=99)

    def test_step42_has_no_runtime_implementation_in_step41_changes(self):
        source = (ROOT / "scripts/run_step41_full_security_regression_validation.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('"step42_started": False', source)
        self.assertNotIn("run_step42", source)
        self.assertNotIn("backup_and_restore", source)


if __name__ == "__main__":
    unittest.main()
