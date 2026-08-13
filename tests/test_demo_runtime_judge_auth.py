"""Focused R4 judge-only OIDC and owner-binding security tests."""

from __future__ import annotations

import re
import unittest
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

from aioa_memory_kernel.demo_runtime.config import (
    JUDGE_ALLOWED_SUBJECTS_ENV,
    OIDC_CLIENT_ID_ENV,
    OIDC_ISSUER_ENV,
    PUBLIC_ORIGIN_ENV,
    RUNTIME_MODE_ENV,
    RuntimeAssemblyError,
    RuntimeErrorCode,
    RuntimeSettings,
)
from aioa_memory_kernel.demo_runtime.composition import (
    JudgeSessionApplicationDependencyFactory,
    RuntimeDependencies,
    RuntimeStartupRecorder,
    RuntimeStartupStage,
    SessionStorageClass,
)
from aioa_memory_kernel.demo_runtime.judge_access import JudgeAccessPolicy
from aioa_memory_kernel.personal_memory_ui import (
    MemoryOwnerSessionStore,
    OidcSettings,
    create_personal_memory_app,
)
from aioa_memory_kernel.personal_memory_ui.auth import (
    OIDC_FLOW_COOKIE_NAME,
    SESSION_COOKIE_NAME,
)
from tests.test_step35_personal_memory_ui import (
    FakeBackend,
    FakeOidcClient,
    OWNER_A,
    OWNER_B,
)


class JudgeAuthenticationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = MemoryOwnerSessionStore(maximum_sessions=20)
        self.oidc = FakeOidcClient()
        self.policy = JudgeAccessPolicy((OWNER_A.oidc_subject,))
        self.app = create_personal_memory_app(
            backend=FakeBackend(),
            oidc_client=self.oidc,
            oidc_settings=OidcSettings(
                issuer="https://identity.example",
                client_id="personal-memory-ui",
                redirect_uri="https://testserver/memory/oidc/callback",
                public_origin="https://testserver",
            ),
            session_store=self.store,
            principal_authorizer=self.policy,
            clock=lambda: 1000.0,
        )
        self.client = TestClient(
            self.app, base_url="https://testserver", raise_server_exceptions=False
        )

    def _callback(self, principal, *, extra=""):
        self.oidc.next_principal = principal
        login = self.client.get("/memory/login", follow_redirects=False)
        state = parse_qs(urlparse(login.headers["location"]).query)["state"][0]
        return self.client.get(
            "/memory/oidc/callback?code=valid-code&state=" + state + extra,
            follow_redirects=False,
        )

    def test_allowed_verified_subject_creates_owner_bound_session(self):
        callback = self._callback(OWNER_A)
        self.assertEqual(callback.status_code, 303)
        handle = self.client.cookies.get(SESSION_COOKIE_NAME)
        session = self.store.get_session(handle, now=1001.0)
        self.assertEqual(session.principal, OWNER_A)
        self.assertIn("Secure", callback.headers["set-cookie"])
        self.assertIn("HttpOnly", callback.headers["set-cookie"])
        self.assertIn("SameSite=lax", callback.headers["set-cookie"])

    def test_authenticated_but_unlisted_subject_is_denied_without_session(self):
        callback = self._callback(OWNER_B)
        self.assertEqual(callback.status_code, 401)
        self.assertIsNone(self.client.cookies.get(SESSION_COOKIE_NAME))
        self.assertEqual(self.store._sessions, {})

    def test_browser_judge_flag_is_ignored(self):
        callback = self._callback(OWNER_B, extra="&judge=true&owner_id=owner-a")
        self.assertEqual(callback.status_code, 401)
        self.assertEqual(self.store._sessions, {})

    def test_login_rotates_existing_authenticated_session(self):
        old_handle, _ = self.store.create_session(OWNER_A, now=900.0)
        self.client.cookies.set(SESSION_COOKIE_NAME, old_handle, path="/memory")
        callback = self._callback(OWNER_A)
        self.assertEqual(callback.status_code, 303)
        match = re.search(
            rf"{re.escape(SESSION_COOKIE_NAME)}=([^;]+)",
            callback.headers["set-cookie"],
        )
        self.assertIsNotNone(match)
        new_handle = match.group(1)
        self.assertNotEqual(new_handle, old_handle)
        self.assertIsNone(self.store.get_session(old_handle, now=1001.0))

    def test_policy_repr_and_http_error_do_not_disclose_allowlist(self):
        self.assertNotIn(OWNER_A.oidc_subject, repr(self.policy))
        callback = self._callback(OWNER_B)
        self.assertNotIn(OWNER_B.oidc_subject, callback.text)
        self.assertNotIn("owner-b", callback.text)

    def test_malformed_callback_is_bounded_and_clears_pending_cookie(self):
        login = self.client.get("/memory/login", follow_redirects=False)
        state = parse_qs(urlparse(login.headers["location"]).query)["state"][0]
        callback = self.client.get(
            "/memory/oidc/callback?code=" + ("x" * 4097) + "&state=" + state,
            follow_redirects=False,
        )
        self.assertEqual(callback.status_code, 401)
        self.assertEqual(callback.text, "OIDC callback failed safely.")
        self.assertIsNone(self.client.cookies.get(OIDC_FLOW_COOKIE_NAME))
        self.assertIsNone(self.client.cookies.get(SESSION_COOKIE_NAME))
        self.assertEqual(self.store._pending, {})


class JudgeRuntimeConfigurationTests(unittest.TestCase):
    @staticmethod
    def hosted_environment() -> dict[str, str]:
        return {
            RUNTIME_MODE_ENV: "HOSTED_DEMO",
            OIDC_ISSUER_ENV: "https://identity.example",
            OIDC_CLIENT_ID_ENV: "memory-patch-demo",
            PUBLIC_ORIGIN_ENV: "https://demo.example",
            "DATABASE_URL_APP": (
                "postgresql://runtime-app:synthetic@db.example/memory_patch"
                "?sslmode=verify-full"
            ),
            "DATABASE_URL_MIGRATOR": (
                "postgresql://runtime-migrator:synthetic@db.example/memory_patch"
                "?sslmode=verify-full"
            ),
        }

    def test_hosted_mode_requires_nonempty_exact_judge_allowlist(self):
        with self.assertRaises(RuntimeAssemblyError) as raised:
            RuntimeSettings.from_mapping(self.hosted_environment())
        self.assertIn(
            raised.exception.code,
            {RuntimeErrorCode.CONFIG_INVALID, RuntimeErrorCode.AUTH_CONFIG_INVALID},
        )

        environment = self.hosted_environment()
        environment[JUDGE_ALLOWED_SUBJECTS_ENV] = "subject-b,subject-a"
        settings = RuntimeSettings.from_mapping(environment)
        self.assertEqual(
            settings.judge_access.allowed_oidc_subjects,
            ("subject-a", "subject-b"),
        )

    def test_allowlist_and_session_configuration_repr_are_secret_free(self):
        environment = self.hosted_environment()
        environment[JUDGE_ALLOWED_SUBJECTS_ENV] = "private-subject-sentinel"
        settings = RuntimeSettings.from_mapping(environment)
        rendered = repr(settings.judge_access)
        self.assertNotIn("private-subject-sentinel", rendered)
        self.assertEqual(settings.sessions.limits.maximum_total_records, 64)
        self.assertEqual(settings.sessions.limits.maximum_sessions_per_owner, 4)


class _Pool:
    @staticmethod
    def connection_factory():
        return lambda: (_ for _ in ()).throw(AssertionError("no DB use at assembly"))


class _Database:
    ready = True
    application_pool = _Pool()


class _ServiceFactory:
    def __init__(self) -> None:
        self.received = None

    def validate_availability(self, settings):
        return None

    def initialize_after_sessions(
        self, settings, recorder, database, oidc_client, session_store, authorizer
    ):
        self.received = (database, oidc_client, session_store, authorizer)
        for stage in (
            RuntimeStartupStage.SERVICE_COMPOSITION_INITIALIZED,
            RuntimeStartupStage.PROVIDER_ADAPTER_INITIALIZED,
            RuntimeStartupStage.RUNTIME_GUARDS_INITIALIZED,
        ):
            recorder.advance(stage)
        return RuntimeDependencies(
            backend=object(),
            oidc_client=oidc_client,
            session_store=session_store,
            provider_adapter=object(),
            session_storage_class=SessionStorageClass.TEST_ONLY,
        )

    def cleanup_partial(self):
        return None


class JudgeSessionCompositionTests(unittest.TestCase):
    def test_r4_stage_uses_cockroach_store_and_server_side_policy(self):
        settings = RuntimeSettings.from_mapping({RUNTIME_MODE_ENV: "TEST"})
        service = _ServiceFactory()
        factory = JudgeSessionApplicationDependencyFactory(service)
        recorder = RuntimeStartupRecorder()
        for stage in (
            RuntimeStartupStage.CONFIG_VALIDATED,
            RuntimeStartupStage.DEPENDENCY_AVAILABILITY_VALIDATED,
            RuntimeStartupStage.DATABASE_RESOURCES_INITIALIZED,
        ):
            recorder.advance(stage)
        dependencies = __import__("asyncio").run(
            factory.initialize_after_database(settings, recorder, _Database())
        )
        self.assertEqual(
            recorder.events[-4:],
            (
                RuntimeStartupStage.SESSION_RESOURCES_INITIALIZED,
                RuntimeStartupStage.SERVICE_COMPOSITION_INITIALIZED,
                RuntimeStartupStage.PROVIDER_ADAPTER_INITIALIZED,
                RuntimeStartupStage.RUNTIME_GUARDS_INITIALIZED,
            ),
        )
        self.assertEqual(dependencies.session_storage_class, SessionStorageClass.DURABLE)
        self.assertIs(dependencies.principal_authorizer, service.received[3])
        self.assertIn(dependencies.session_store, dependencies.owned_session_resources)
        self.assertIn(service.received[0], dependencies.owned_database_resources)
        dependencies.session_store.close()

    def test_partial_downstream_failure_closes_owned_auth_resources(self):
        class Failing(_ServiceFactory):
            def initialize_after_sessions(self, *args):
                raise RuntimeError("synthetic downstream failure")

        settings = RuntimeSettings.from_mapping({RUNTIME_MODE_ENV: "TEST"})
        factory = JudgeSessionApplicationDependencyFactory(Failing())
        recorder = RuntimeStartupRecorder()
        for stage in (
            RuntimeStartupStage.CONFIG_VALIDATED,
            RuntimeStartupStage.DEPENDENCY_AVAILABILITY_VALIDATED,
            RuntimeStartupStage.DATABASE_RESOURCES_INITIALIZED,
        ):
            recorder.advance(stage)
        with self.assertRaises(RuntimeError):
            __import__("asyncio").run(
                factory.initialize_after_database(settings, recorder, _Database())
            )
        __import__("asyncio").run(factory.cleanup_partial())


if __name__ == "__main__":
    unittest.main()
