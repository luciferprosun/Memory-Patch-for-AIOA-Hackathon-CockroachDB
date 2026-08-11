"""Focused Step 36 secret redaction and downstream-surface tests."""

from __future__ import annotations

import io
import json
import logging
import unittest

from fastapi.testclient import TestClient
from tests._support import REPOSITORY_ROOT  # noqa: F401

from aioa_memory_kernel.audit_ledger.models import safe_audit_payload
from aioa_memory_kernel.contracts.exceptions import ContractValidationError
from aioa_memory_kernel.personal_memory import PersonalMemoryLifecycleExportRecord
from aioa_memory_kernel.review_workspace.models import safe_review_context
from aioa_memory_kernel.security import (
    CredentialBoundaryError,
    CredentialPurpose,
    SecretValue,
    assert_secret_free,
    contains_secret_material,
    redact_exception,
    redact_text,
)
from aioa_memory_kernel.personal_memory_ui import (
    MemoryOwnerSessionStore,
    OidcSettings,
    create_personal_memory_app,
)
from tests.test_step35_personal_memory_ui import FakeBackend, FakeOidcClient


RAW_SENTINEL = "step36-fake-secret-sentinel-never-render"


class CentralSecretDetectionTests(unittest.TestCase):
    def test_nested_secret_keys_and_values_are_detected(self) -> None:
        unsafe_values = (
            {"nested": {"api-key": RAW_SENTINEL}},
            {"safe": "Authorization: Bearer " + RAW_SENTINEL},
            {"safe": "postgresql://user:password@example.invalid/db"},
            {"safe": "cockroachdb://user:password@example.invalid/db"},
            {"safe": "AKIAABCDEFGHIJKLMNOP"},
            {"safe": "github_pat_" + "a" * 24},
            {"safe": "ghp_" + "a" * 24},
            {"safe": "sk-proj-" + "a" * 24},
            {"safe": "x-amz-signature=" + "a" * 32},
            {"safe": "-----BEGIN PRIVATE KEY-----"},
        )
        for value in unsafe_values:
            with self.subTest(value=value):
                self.assertTrue(contains_secret_material(value))
                with self.assertRaises(ValueError):
                    assert_secret_free(value, surface="STEP36_TEST")

    def test_machine_paths_are_policy_controlled(self) -> None:
        value = {"safe_label": "/media/private/workspace/credential.json"}
        self.assertFalse(contains_secret_material(value, reject_machine_paths=False))
        self.assertTrue(contains_secret_material(value, reject_machine_paths=True))
        with self.assertRaises(ValueError):
            assert_secret_free(
                value,
                surface="AUDIT_EXPORT",
                reject_machine_paths=True,
            )

    def test_safe_hash_references_and_reason_codes_are_accepted(self) -> None:
        safe = {
            "proposal_hash": "a" * 64,
            "reason_codes": ["COMMIT_COMPLETED", "AUDIT_EVENT_APPENDED"],
            "state_version": 6,
            "canonical_evidence": False,
        }
        self.assertFalse(contains_secret_material(safe, reject_machine_paths=True))
        for surface in (
            "LOG",
            "AUDIT",
            "PERSONAL_MEMORY_EXPORT",
            "REVIEW_DETAIL",
            "UI_RESPONSE",
            "VALIDATION_EVIDENCE",
        ):
            assert_secret_free(safe, surface=surface, reject_machine_paths=True)

        security_metadata = {
            "browser_privileged_secret_hits": 0,
            "credential_inventory_digest": "b" * 64,
            "master_credential_fallback": False,
            "missing_secret_fail_closed": {"commit_helper": True},
            "secret_leakage_count": 0,
        }
        assert_secret_free(
            security_metadata,
            surface="STEP36_VALIDATION_EVIDENCE",
            reject_machine_paths=True,
        )

    def test_non_string_mapping_keys_fail_closed(self) -> None:
        self.assertTrue(contains_secret_material({1: "value"}))
        with self.assertRaises(ValueError):
            assert_secret_free({1: "value"}, surface="AUDIT")


class RedactedRenderingTests(unittest.TestCase):
    def test_redact_text_removes_each_supported_secret_shape(self) -> None:
        values = (
            "Authorization: Bearer " + RAW_SENTINEL,
            "Bearer " + RAW_SENTINEL,
            "postgresql://user:password@example.invalid/db",
            "cockroachdb://user:password@example.invalid/db",
            "AKIAABCDEFGHIJKLMNOP",
            "github_pat_" + "a" * 24,
            "ghp_" + "a" * 24,
            "sk-proj-" + "a" * 24,
            "sk-" + "a" * 24,
            "x-amz-credential=" + "a" * 24,
            "-----BEGIN OPENSSH PRIVATE KEY-----\n"
            "step36-private-key-body-sentinel\n"
            "-----END OPENSSH PRIVATE KEY-----",
            'password="step36 quoted secret phrase" trailing-safe-text',
            "/home/private/secrets.env",
        )
        for value in values:
            with self.subTest(value=value):
                rendered = redact_text(value)
                self.assertNotEqual(rendered, value)
                self.assertNotIn(RAW_SENTINEL, rendered)
                self.assertNotIn("password@example.invalid", rendered)
                self.assertNotIn("AKIAABCDEFGHIJKLMNOP", rendered)
                self.assertNotIn("PRIVATE KEY", rendered)
                self.assertNotIn("private-key-body-sentinel", rendered)
                self.assertNotIn("quoted secret phrase", rendered)

    def test_redact_text_is_bounded_and_non_strings_disclose_only_type(self) -> None:
        rendered = redact_text("x" * 5000, maximum_bytes=37)
        self.assertLessEqual(len(rendered.encode("utf-8")), 37)
        self.assertEqual(redact_text({"api_key": RAW_SENTINEL}), "dict")

    def test_exception_redaction_uses_only_a_typed_safe_code_or_class(self) -> None:
        safe = CredentialBoundaryError("MISSING_DEDICATED_MODEL_PROVIDER_CREDENTIAL")
        self.assertEqual(
            redact_exception(safe),
            "MISSING_DEDICATED_MODEL_PROVIDER_CREDENTIAL",
        )
        unsafe = RuntimeError("database failed with " + RAW_SENTINEL)
        self.assertEqual(redact_exception(unsafe), "RUNTIMEERROR")
        self.assertNotIn(RAW_SENTINEL, redact_exception(unsafe))

    def test_secret_value_never_leaks_through_standard_logging(self) -> None:
        value = SecretValue(
            RAW_SENTINEL,
            purpose=CredentialPurpose.MODEL_PROVIDER,
            source_name="MOONSHOT_API_KEY",
        )
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        logger = logging.getLogger("step36-redaction-test")
        logger.handlers = [handler]
        logger.setLevel(logging.INFO)
        logger.propagate = False
        logger.info("provider credential=%s repr=%r", value, value)
        rendered = stream.getvalue()
        self.assertNotIn(RAW_SENTINEL, rendered)
        self.assertIn("redacted", rendered)


class ExistingSurfaceRegressionTests(unittest.TestCase):
    def test_audit_export_and_review_surfaces_reject_the_same_secret_shapes(self) -> None:
        payloads = (
            {"api_key": RAW_SENTINEL},
            {"safe": "Authorization: Bearer " + RAW_SENTINEL},
            {"safe": "postgresql://user:password@example.invalid/db"},
            {"safe": "AKIAABCDEFGHIJKLMNOP"},
            {"safe": "-----BEGIN OPENSSH PRIVATE KEY-----"},
            {"safe": "/home/private/credentials.txt"},
        )
        for payload in payloads:
            with self.subTest(surface="audit", payload=payload):
                with self.assertRaises(ContractValidationError):
                    safe_audit_payload(payload)
            with self.subTest(surface="personal-memory-export", payload=payload):
                with self.assertRaises(ContractValidationError):
                    PersonalMemoryLifecycleExportRecord(
                        record_type="STEP36_SECRET_NEGATIVE",
                        record_id="step36-secret-negative",
                        payload=payload,
                    )
            with self.subTest(surface="review", payload=payload):
                with self.assertRaises(ContractValidationError):
                    safe_review_context(payload)

    def test_unexpected_backend_exception_is_not_rendered_to_owner_ui(self) -> None:
        class SecretFailingBackend(FakeBackend):
            def dashboard(self, principal):
                raise RuntimeError(
                    "postgresql://user:password@example.invalid/db " + RAW_SENTINEL
                )

        oidc = FakeOidcClient()
        store = MemoryOwnerSessionStore(maximum_sessions=10)
        app = create_personal_memory_app(
            backend=SecretFailingBackend(),
            oidc_client=oidc,
            oidc_settings=OidcSettings(
                issuer="https://identity.example",
                client_id="personal-memory-ui",
                redirect_uri="https://testserver/memory/oidc/callback",
                public_origin="https://testserver",
            ),
            session_store=store,
            clock=lambda: 1000.0,
        )
        client = TestClient(
            app,
            base_url="https://testserver",
            raise_server_exceptions=False,
        )
        login = client.get("/memory/login", follow_redirects=False)
        state = login.headers["location"].split("state=", 1)[1].split("&", 1)[0]
        callback = client.get(
            "/memory/oidc/callback",
            params={"code": "valid-code", "state": state},
            follow_redirects=False,
        )
        self.assertEqual(callback.status_code, 303)
        response = client.get("/memory")
        self.assertEqual(response.status_code, 500)
        self.assertNotIn(RAW_SENTINEL, response.text)
        self.assertNotIn("password@example.invalid", response.text)
        self.assertNotIn("postgresql://", response.text)
        self.assertIn("failed safely", response.text.casefold())

    def test_validation_json_serializing_safe_metadata_contains_no_secret(self) -> None:
        evidence = {
            "step": 36,
            "browser_exposure_hits": 0,
            "leakage_count": 0,
            "commit_helper_can_approve": False,
            "app_bypassrls": False,
            "validation_digest": "a" * 64,
        }
        rendered = json.dumps(evidence, sort_keys=True)
        self.assertNotIn(RAW_SENTINEL, rendered)
        self.assertFalse(contains_secret_material(evidence))


if __name__ == "__main__":
    unittest.main()
