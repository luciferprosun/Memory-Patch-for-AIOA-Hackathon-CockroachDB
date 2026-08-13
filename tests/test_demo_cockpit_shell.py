"""Focused D1 behavior tests for the unified AIOA cockpit shell."""

from __future__ import annotations

import unittest
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

from aioa_memory_kernel.demo_cockpit import (
    CockpitMode,
    CockpitRuntimeStatus,
    CockpitShell,
    LegacyCompatibilityMode,
    build_default_cockpit_shell,
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


class UnifiedCockpitShellTests(unittest.TestCase):
    def test_default_is_current_and_legacy_is_disabled(self) -> None:
        view = build_default_cockpit_shell().project()
        self.assertIs(view.selected_mode, CockpitMode.MEMORY_PATCH_CURRENT)
        self.assertEqual(view.mode_badge, "CURRENT / EVIDENCE-BOUND")
        self.assertFalse(view.legacy.enabled)
        self.assertEqual(
            view.legacy.classification,
            "DISABLED_WITH_ARCHIVAL_VIEW",
        )
        self.assertEqual(len(view.stages), 11)
        self.assertEqual(len(view.observer_cards), 3)

    def test_enabled_legacy_is_truthful_view_only(self) -> None:
        default = build_default_cockpit_shell()
        shell = CockpitShell(
            default.runtime_status,
            legacy_mode=LegacyCompatibilityMode.ARCHIVAL_VIEW,
            legacy_archive=build_legacy_archive_manifest(),
        )
        view = shell.project(CockpitMode.CRITICAL_PROMPT_LEGACY.value)
        self.assertIs(view.selected_mode, CockpitMode.CRITICAL_PROMPT_LEGACY)
        self.assertEqual(view.mode_badge, "LEGACY / ORIGIN — ARCHIVAL VIEW")
        self.assertIn("not live", view.introduction)
        self.assertIn("not a replay", view.introduction)
        self.assertTrue(all("Advisory" in item.authority for item in view.observer_cards))

    def test_disabled_or_unknown_mode_falls_back_without_reflection(self) -> None:
        shell = build_default_cockpit_shell()
        disabled = shell.project(CockpitMode.CRITICAL_PROMPT_LEGACY.value)
        hostile = shell.project("<script>alert(1)</script>")
        self.assertIs(disabled.selected_mode, CockpitMode.MEMORY_PATCH_CURRENT)
        self.assertIn("disabled", disabled.notice or "")
        self.assertIs(hostile.selected_mode, CockpitMode.MEMORY_PATCH_CURRENT)
        self.assertNotIn("script", hostile.notice or "")


class UnifiedCockpitRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.backend = FakeBackend()
        self.oidc = FakeOidcClient()
        self.store = MemoryOwnerSessionStore(maximum_sessions=20)
        settings = OidcSettings(
            issuer="https://identity.example",
            client_id="personal-memory-ui",
            redirect_uri="https://testserver/memory/oidc/callback",
            public_origin="https://testserver",
        )
        default = build_default_cockpit_shell()
        self.shell = CockpitShell(
            default.runtime_status,
            legacy_mode=LegacyCompatibilityMode.ARCHIVAL_VIEW,
            legacy_archive=build_legacy_archive_manifest(),
        )
        self.app = create_personal_memory_app(
            backend=self.backend,
            oidc_client=self.oidc,
            oidc_settings=settings,
            session_store=self.store,
            clock=lambda: 1000.0,
            cockpit_shell=self.shell,
        )
        self.client = TestClient(
            self.app,
            base_url="https://testserver",
            raise_server_exceptions=False,
        )

    def login(self) -> None:
        self.oidc.next_principal = OWNER_A
        response = self.client.get("/memory/login", follow_redirects=False)
        state = parse_qs(urlparse(response.headers["location"]).query)["state"][0]
        callback = self.client.get(
            "/memory/oidc/callback",
            params={"code": "valid-code", "state": state},
            follow_redirects=False,
        )
        self.assertEqual(callback.status_code, 303)

    def test_cockpit_requires_existing_authentication_boundary(self) -> None:
        response = self.client.get("/memory/demo", follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertTrue(response.headers["location"].startswith("/memory/login"))

    def test_current_mode_is_default_and_existing_workspace_remains(self) -> None:
        self.login()
        response = self.client.get("/memory/demo")
        self.assertEqual(response.status_code, 200)
        self.assertIn("CURRENT / EVIDENCE-BOUND", response.text)
        self.assertIn('aria-current="page"', response.text)
        self.assertIn("Question", response.text)
        self.assertIn("Verified Answer", response.text)
        self.assertIn("Personal Memory", response.text)
        self.assertEqual(self.client.get("/memory").status_code, 200)

    def test_mode_switch_is_get_only_and_has_zero_business_mutation(self) -> None:
        self.login()
        before = tuple(self.backend.calls)
        response = self.client.get(
            "/memory/demo", params={"mode": "critical_prompt_loop"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("LEGACY / ORIGIN — ARCHIVAL VIEW", response.text)
        self.assertIn("NOT LIVE", response.text)
        self.assertIn("NOT A REPLAY", response.text)
        self.assertIn("0 PROVIDER CALLS", response.text)
        self.assertIn("ADVISORY / DEMO ONLY", response.text)
        self.assertNotIn('action="/memory/proposals/', response.text)
        self.assertNotIn('action="/memory/patches/', response.text)
        self.assertEqual(tuple(self.backend.calls), before)
        route = next(item for item in self.app.routes if item.path == "/memory/demo")
        self.assertEqual(route.methods, {"GET"})

    def test_mode_switch_is_keyboard_named_and_responsive(self) -> None:
        self.login()
        response = self.client.get("/memory/demo")
        self.assertIn('aria-label="Demo presentation mode"', response.text)
        self.assertIn("Memory Patch — Current", response.text)
        self.assertIn("Critical Prompt Loop — Legacy", response.text)
        css = self.client.get("/memory/static/app.css")
        self.assertEqual(css.status_code, 200)
        self.assertIn(":focus-visible", css.text)
        self.assertIn("@media (max-width: 800px)", css.text)
        self.assertIn("@media (max-width: 520px)", css.text)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
