"""Focused Step 36 credential-purpose and browser-boundary tests."""

from __future__ import annotations

import json
import pickle
import re
import unittest
from pathlib import Path

from tests._support import REPOSITORY_ROOT

from aioa_memory_kernel.persistence import (
    PersistenceConfigurationError,
    SerializableTransactionRunner,
)
from aioa_memory_kernel.security import (
    AWS_WORKLOAD_IDENTITY_ENVIRONMENT_NAMES,
    CREDENTIAL_SPECS,
    CredentialBoundaryError,
    CredentialPurpose,
    CredentialSpec,
    SecretValue,
    credential_inventory_digest,
    load_required_credential,
)
from aioa_memory_kernel.security.credentials import (
    build_minimal_subprocess_environment,
)


ROOT = REPOSITORY_ROOT
CAPABILITY_MATRIX = ROOT / "docs/security/STEP36_CREDENTIAL_CAPABILITY_MATRIX_1A.md"
PRIVILEGED_BROWSER_NAMES = (
    "DATABASE_URL_APP",
    "DATABASE_URL_COMMIT_HELPER",
    "DATABASE_URL_MIGRATOR",
    "DATABASE_URL_AUDIT_READER",
    "DATABASE_URL_REVIEWER",
    "DATABASE_URL_REVIEW_SERVICE",
    "DATABASE_URL_SOURCE_PUBLICATION",
    "DATABASE_URL_INGESTION",
    "OPENROUTER_API_KEY",
    # The retired Step 22 provider name remains privileged legacy material.
    "MOONSHOT_API_KEY",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "GITHUB_TOKEN",
)


def _markdown_table(path: Path, heading: str) -> tuple[list[str], list[list[str]]]:
    """Return one named Markdown table without depending on prose layout."""

    lines = path.read_text(encoding="utf-8").splitlines()
    start = next(
        index
        for index, line in enumerate(lines)
        if heading.casefold() in line.casefold()
    )
    table: list[str] = []
    for line in lines[start + 1 :]:
        if line.startswith("|"):
            table.append(line)
        elif table and line.strip():
            break
    if len(table) < 3:
        raise AssertionError(f"missing table after {heading!r}")
    headers = [cell.strip() for cell in table[0].strip("|").split("|")]
    rows = [
        [cell.strip() for cell in line.strip("|").split("|")]
        for line in table[2:]
        if line.strip("| ")
    ]
    return headers, rows


class CredentialInventoryTests(unittest.TestCase):
    def test_inventory_is_complete_unique_and_never_browser_visible(self) -> None:
        self.assertEqual(set(CREDENTIAL_SPECS), set(CredentialPurpose))
        environment_names = [
            spec.environment_variable
            for spec in CREDENTIAL_SPECS.values()
            if spec.environment_variable is not None
        ]
        self.assertEqual(len(environment_names), len(set(environment_names)))
        self.assertNotIn("DATABASE_URL", environment_names)
        self.assertFalse(any(spec.browser_visible for spec in CREDENTIAL_SPECS.values()))
        for purpose, spec in CREDENTIAL_SPECS.items():
            with self.subTest(purpose=purpose.value):
                self.assertIs(spec.purpose, purpose)
                self.assertTrue(spec.logical_name)
                self.assertTrue(spec.consumer)
                self.assertTrue(spec.rotation_mechanism)
                self.assertTrue(spec.capabilities)
                self.assertEqual(spec.capabilities, tuple(sorted(set(spec.capabilities))))

    def test_high_risk_capabilities_have_distinct_exact_environment_names(self) -> None:
        expected = {
            CredentialPurpose.APPLICATION_DATABASE: "DATABASE_URL_APP",
            CredentialPurpose.PERSONAL_MEMORY_COMMIT_DATABASE: "DATABASE_URL_COMMIT_HELPER",
            CredentialPurpose.MIGRATION_DATABASE: "DATABASE_URL_MIGRATOR",
            CredentialPurpose.AUDIT_READER_DATABASE: "DATABASE_URL_AUDIT_READER",
            CredentialPurpose.HUMAN_REVIEWER_DATABASE: "DATABASE_URL_REVIEWER",
            CredentialPurpose.REVIEW_SERVICE_DATABASE: "DATABASE_URL_REVIEW_SERVICE",
            CredentialPurpose.SOURCE_PUBLICATION_DATABASE: "DATABASE_URL_SOURCE_PUBLICATION",
            CredentialPurpose.INGESTION_DATABASE: "DATABASE_URL_INGESTION",
            CredentialPurpose.MODEL_PROVIDER: "OPENROUTER_API_KEY",
        }
        self.assertEqual(
            {purpose: CREDENTIAL_SPECS[purpose].environment_variable for purpose in expected},
            expected,
        )
        self.assertIsNone(
            CREDENTIAL_SPECS[CredentialPurpose.S3_RUNTIME_IDENTITY].environment_variable
        )
        self.assertIsNone(
            CREDENTIAL_SPECS[
                CredentialPurpose.AUDIT_APPENDER_DATABASE
            ].environment_variable
        )

    def test_missing_dedicated_credential_never_falls_back_to_admin_or_master(self) -> None:
        broad_only = {
            "DATABASE_URL": "postgresql://root:admin@example.invalid/defaultdb",
            "DATABASE_URL_ADMIN": "postgresql://root:admin@example.invalid/defaultdb",
            "DATABASE_URL_MIGRATOR": "postgresql://migrator:test@example.invalid/defaultdb",
            "MASTER_DATABASE_URL": "postgresql://root:admin@example.invalid/defaultdb",
        }
        with self.assertRaises(CredentialBoundaryError) as caught:
            load_required_credential(
                CredentialPurpose.PERSONAL_MEMORY_COMMIT_DATABASE,
                broad_only,
            )
        self.assertEqual(
            caught.exception.sanitized_code,
            "MISSING_DEDICATED_PERSONAL_MEMORY_COMMIT_DATABASE_CREDENTIAL",
        )

    def test_loader_returns_only_exact_purpose_bound_secret(self) -> None:
        environment = {
            "DATABASE_URL_APP": "postgresql://app:test@example.invalid/defaultdb",
            "DATABASE_URL_COMMIT_HELPER": "postgresql://commit:test@example.invalid/defaultdb",
        }
        app = load_required_credential(CredentialPurpose.APPLICATION_DATABASE, environment)
        commit = load_required_credential(
            CredentialPurpose.PERSONAL_MEMORY_COMMIT_DATABASE, environment
        )
        self.assertIs(app.purpose, CredentialPurpose.APPLICATION_DATABASE)
        self.assertIs(commit.purpose, CredentialPurpose.PERSONAL_MEMORY_COMMIT_DATABASE)
        self.assertNotEqual(
            app.reveal_for(CredentialPurpose.APPLICATION_DATABASE),
            commit.reveal_for(CredentialPurpose.PERSONAL_MEMORY_COMMIT_DATABASE),
        )
        with self.assertRaises(CredentialBoundaryError):
            app.reveal_for(CredentialPurpose.PERSONAL_MEMORY_COMMIT_DATABASE)

    def test_s3_uses_workload_identity_and_never_an_environment_secret_fallback(self) -> None:
        with self.assertRaises(CredentialBoundaryError) as caught:
            load_required_credential(
                CredentialPurpose.S3_RUNTIME_IDENTITY,
                {
                    "AWS_ACCESS_KEY_ID": "AKIAABCDEFGHIJKLMNOP",
                    "AWS_SECRET_ACCESS_KEY": "not-a-real-secret",
                },
            )
        self.assertEqual(caught.exception.sanitized_code, "WORKLOAD_IDENTITY_REQUIRED")

    def test_inventory_digest_is_deterministic(self) -> None:
        first = credential_inventory_digest()
        second = credential_inventory_digest()
        self.assertEqual(first, second)
        self.assertRegex(first, r"^[0-9a-f]{64}$")

    def test_credential_spec_rejects_browser_visibility_and_incomplete_cells(self) -> None:
        with self.assertRaises(ValueError):
            CredentialSpec(
                purpose=CredentialPurpose.MODEL_PROVIDER,
                logical_name="provider",
                environment_variable="NEXT_PUBLIC_PROVIDER_KEY",
                consumer="browser",
                required_for_operation=True,
                browser_visible=True,
                rotation_mechanism="rotate",
                capabilities=("CALL_PROVIDER",),
            )
        with self.assertRaises(ValueError):
            CredentialSpec(
                purpose=CredentialPurpose.MODEL_PROVIDER,
                logical_name="provider",
                environment_variable="OPENROUTER_API_KEY",
                consumer="adapter",
                required_for_operation=True,
                browser_visible=False,
                rotation_mechanism="rotate",
                capabilities=(),
            )


class SecretValueAndProcessBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.raw = "step36-fake-commit-helper-secret-sentinel"
        self.secret = SecretValue(
            self.raw,
            purpose=CredentialPurpose.PERSONAL_MEMORY_COMMIT_DATABASE,
            source_name="DATABASE_URL_COMMIT_HELPER",
        )

    def test_secret_value_is_immutable_redacted_and_nonserializable(self) -> None:
        for rendered in (str(self.secret), repr(self.secret), f"{self.secret}"):
            self.assertNotIn(self.raw, rendered)
            self.assertIn("redacted", rendered)
        with self.assertRaises(AttributeError):
            self.secret._purpose = CredentialPurpose.APPLICATION_DATABASE  # type: ignore[misc]
        with self.assertRaises(TypeError):
            json.dumps({"value": self.secret})
        with self.assertRaises(TypeError):
            pickle.dumps(self.secret)

    def test_secret_value_validation_is_fail_closed(self) -> None:
        for invalid in ("", " leading", "trailing ", "null\x00value"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(CredentialBoundaryError):
                    SecretValue(
                        invalid,
                        purpose=CredentialPurpose.MODEL_PROVIDER,
                        source_name="OPENROUTER_API_KEY",
                    )
        with self.assertRaises(TypeError):
            SecretValue("value", purpose="MODEL_PROVIDER", source_name="KEY")  # type: ignore[arg-type]
        with self.assertRaises(CredentialBoundaryError):
            SecretValue(
                "safe-value",
                purpose=CredentialPurpose.MODEL_PROVIDER,
                source_name="postgresql://user:password@example.invalid/db",
            )

    def test_transaction_runner_requires_the_exact_typed_credential_purpose(self) -> None:
        correct = SerializableTransactionRunner(
            lambda: None,
            credential_purpose=CredentialPurpose.PERSONAL_MEMORY_COMMIT_DATABASE,
        )
        correct.require_credential_purpose(
            CredentialPurpose.PERSONAL_MEMORY_COMMIT_DATABASE
        )
        for runner in (
            SerializableTransactionRunner(lambda: None),
            SerializableTransactionRunner(
                lambda: None,
                credential_purpose=CredentialPurpose.APPLICATION_DATABASE,
            ),
            SerializableTransactionRunner(
                lambda: None,
                credential_purpose=CredentialPurpose.MIGRATION_DATABASE,
            ),
        ):
            with self.subTest(purpose=runner.credential_purpose):
                with self.assertRaises(PersistenceConfigurationError) as caught:
                    runner.require_credential_purpose(
                        CredentialPurpose.PERSONAL_MEMORY_COMMIT_DATABASE
                    )
                self.assertEqual(
                    caught.exception.sanitized_code,
                    "DEDICATED_CREDENTIAL_PURPOSE_REQUIRED",
                )

    def test_minimal_child_environment_is_allowlist_based(self) -> None:
        environment = {
            "PATH": "/usr/bin",
            "LANG": "C.UTF-8",
            "TZ": "UTC",
            "DATABASE_URL_APP": "app-secret-sentinel",
            "DATABASE_URL_MIGRATOR": "migration-secret-sentinel",
            "OPENROUTER_API_KEY": "provider-secret-sentinel",
            "UNRELATED_SECRET_SENTINEL": "must-not-cross-boundary",
        }
        child = build_minimal_subprocess_environment(
            environment,
            allowed_names=("OPENROUTER_API_KEY",),
        )
        self.assertEqual(child["OPENROUTER_API_KEY"], "provider-secret-sentinel")
        self.assertEqual(child.get("PATH"), "/usr/bin")
        self.assertNotIn("DATABASE_URL_APP", child)
        self.assertNotIn("DATABASE_URL_MIGRATOR", child)
        self.assertNotIn("UNRELATED_SECRET_SENTINEL", child)
        self.assertNotIn("must-not-cross-boundary", child.values())

    def test_aws_workload_identity_pointers_do_not_allow_raw_aws_secrets(self) -> None:
        environment = {
            "PATH": "/usr/bin",
            "AWS_ROLE_ARN": "arn:aws:iam::123456789012:role/test-runtime",
            "AWS_WEB_IDENTITY_TOKEN_FILE": "/var/run/secrets/test-token",
            "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI": "/v2/credentials/test",
            "AWS_ACCESS_KEY_ID": "AKIAABCDEFGHIJKLMNOP",
            "AWS_SECRET_ACCESS_KEY": "step36-fake-aws-secret",
            "AWS_SESSION_TOKEN": "step36-fake-session-token",
            "DATABASE_URL_COMMIT_HELPER": "step36-fake-db-secret",
            "OPENROUTER_API_KEY": "step36-fake-provider-secret",
        }
        child = build_minimal_subprocess_environment(
            environment,
            allowed_names=AWS_WORKLOAD_IDENTITY_ENVIRONMENT_NAMES,
        )
        self.assertEqual(child["AWS_ROLE_ARN"], environment["AWS_ROLE_ARN"])
        self.assertEqual(
            child["AWS_WEB_IDENTITY_TOKEN_FILE"],
            environment["AWS_WEB_IDENTITY_TOKEN_FILE"],
        )
        self.assertEqual(
            child["AWS_CONTAINER_CREDENTIALS_RELATIVE_URI"],
            environment["AWS_CONTAINER_CREDENTIALS_RELATIVE_URI"],
        )
        for forbidden in (
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
            "AWS_SESSION_TOKEN",
            "DATABASE_URL_COMMIT_HELPER",
            "OPENROUTER_API_KEY",
        ):
            self.assertNotIn(forbidden, child)


class BrowserAndCapabilityMatrixTests(unittest.TestCase):
    def test_controlled_offline_browser_proof_reports_rendered_output(self) -> None:
        from scripts.run_step36_credential_authority_validation import (
            _offline_boundary_checks,
        )

        result = _offline_boundary_checks()
        self.assertEqual(result["browser_rendered_secret_hits"], 0)
        self.assertGreater(result["browser_rendered_utf8_bytes"], 0)
        self.assertRegex(result["capability_matrix_digest"], r"^[0-9a-f]{64}$")
        self.assertGreaterEqual(len(result["principal_list"]), 10)

    def test_owner_ui_source_and_pinned_assets_contain_no_privileged_env_contract(self) -> None:
        ui_root = ROOT / "src/aioa_memory_kernel/personal_memory_ui"
        files = [path for path in ui_root.rglob("*") if path.is_file()]
        files.append(ROOT / "package.json")
        combined = "\n".join(
            path.read_text(encoding="utf-8", errors="replace") for path in files
        )
        for name in PRIVILEGED_BROWSER_NAMES:
            with self.subTest(name=name):
                self.assertNotIn(name, combined)
        self.assertIsNone(re.search(r"\b(?:VITE_|NEXT_PUBLIC_)[A-Z0-9_]+", combined))
        self.assertNotIn("process.env", combined)
        self.assertNotIn("import.meta.env", combined)

    def test_capability_matrix_has_every_required_column_and_no_blank_capability(self) -> None:
        self.assertTrue(CAPABILITY_MATRIX.is_file())
        headers, rows = _markdown_table(CAPABILITY_MATRIX, "Capability matrix")
        normalized = [header.casefold().replace("`", "").strip() for header in headers]
        required = {
            "principal",
            "credential source",
            "read canonical knowledge",
            "write canonical knowledge",
            "publish source",
            "call provider",
            "read personal memory",
            "propose personal memory",
            "approve personal memory",
            "commit personal memory",
            "activate personal memory",
            "review cases",
            "append audit",
            "read audit",
            "write s3",
            "delete s3",
            "bypass rls",
            "browser-visible",
            "rotation mechanism",
        }
        self.assertTrue(required.issubset(set(normalized)))
        self.assertGreaterEqual(len(rows), 10)
        capability_indexes = [
            normalized.index(name)
            for name in required
            if name not in {"principal", "credential source", "rotation mechanism"}
        ]
        for row in rows:
            self.assertEqual(len(row), len(headers))
            self.assertTrue(all(cell for cell in row))
            for index in capability_indexes:
                self.assertIn(row[index].upper(), {"YES", "NO", "NOT_APPLICABLE"})


if __name__ == "__main__":
    unittest.main()
