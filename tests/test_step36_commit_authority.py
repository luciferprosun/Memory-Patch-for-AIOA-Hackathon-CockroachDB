"""Focused Step 36 Commit Helper and authority-separation tests."""

from __future__ import annotations

import dataclasses
import inspect
import json
import os
import unittest
from datetime import UTC, datetime
from unittest import mock

from tests._support import REPOSITORY_ROOT  # noqa: F401

from aioa_memory_kernel.modeling import ModelAdapterError, ModelReasonCode
from aioa_memory_kernel.modeling.providers import OpenRouterDraftV1Adapter
from aioa_memory_kernel.persistence import (
    PersistenceConfigurationError,
    SerializableTransactionRunner,
)
from aioa_memory_kernel.personal_memory import (
    PersonalMemoryActivationService,
    PersonalMemoryApprovalService,
    PersonalMemoryCommitHelper,
    personal_memory_patch_lifecycle_to_jsonb,
)
from aioa_memory_kernel.security import CredentialPurpose, SecretValue
from tests.test_step30_user_approval_commit_activation import lifecycle_chain


class FixedClock:
    def now(self) -> datetime:
        return datetime(2045, 1, 2, 3, 4, 5, tzinfo=UTC)


def runner(purpose: CredentialPurpose | None) -> SerializableTransactionRunner:
    def connection_factory():
        raise AssertionError("service construction must not open a connection")

    return SerializableTransactionRunner(
        connection_factory,
        credential_purpose=purpose,
    )


class ServiceCredentialPurposeTests(unittest.TestCase):
    def test_exact_purposes_construct_only_their_bounded_services(self) -> None:
        approval = PersonalMemoryApprovalService(
            runner(CredentialPurpose.APPLICATION_DATABASE),
            trusted_clock=FixedClock(),
        )
        commit = PersonalMemoryCommitHelper(
            runner(CredentialPurpose.PERSONAL_MEMORY_COMMIT_DATABASE),
            trusted_clock=FixedClock(),
        )
        activation = PersonalMemoryActivationService(
            runner(CredentialPurpose.PERSONAL_MEMORY_COMMIT_DATABASE),
            trusted_clock=FixedClock(),
        )
        self.assertEqual(
            {
                name
                for name, value in inspect.getmembers(type(approval), inspect.isfunction)
                if not name.startswith("_")
            },
            {"approve"},
        )
        self.assertEqual(
            {
                name
                for name, value in inspect.getmembers(type(commit), inspect.isfunction)
                if not name.startswith("_")
            },
            {"commit"},
        )
        self.assertEqual(
            {
                name
                for name, value in inspect.getmembers(type(activation), inspect.isfunction)
                if not name.startswith("_")
            },
            {"activate"},
        )

    def test_normal_app_reviewer_provider_and_migrator_cannot_construct_commit_helper(self) -> None:
        forbidden = (
            None,
            CredentialPurpose.APPLICATION_DATABASE,
            CredentialPurpose.HUMAN_REVIEWER_DATABASE,
            CredentialPurpose.REVIEW_SERVICE_DATABASE,
            CredentialPurpose.MODEL_PROVIDER,
            CredentialPurpose.MIGRATION_DATABASE,
            CredentialPurpose.AUDIT_READER_DATABASE,
            CredentialPurpose.SOURCE_PUBLICATION_DATABASE,
        )
        for purpose in forbidden:
            with self.subTest(purpose=purpose):
                with self.assertRaises(PersistenceConfigurationError) as caught:
                    PersonalMemoryCommitHelper(
                        runner(purpose),
                        trusted_clock=FixedClock(),
                    )
                self.assertEqual(
                    caught.exception.sanitized_code,
                    "DEDICATED_CREDENTIAL_PURPOSE_REQUIRED",
                )

    def test_normal_app_reviewer_provider_and_migrator_cannot_construct_activation(self) -> None:
        forbidden = (
            None,
            CredentialPurpose.APPLICATION_DATABASE,
            CredentialPurpose.HUMAN_REVIEWER_DATABASE,
            CredentialPurpose.REVIEW_SERVICE_DATABASE,
            CredentialPurpose.MODEL_PROVIDER,
            CredentialPurpose.MIGRATION_DATABASE,
            CredentialPurpose.AUDIT_READER_DATABASE,
        )
        for purpose in forbidden:
            with self.subTest(purpose=purpose):
                with self.assertRaises(PersistenceConfigurationError) as caught:
                    PersonalMemoryActivationService(
                        runner(purpose),
                        trusted_clock=FixedClock(),
                    )
                self.assertEqual(
                    caught.exception.sanitized_code,
                    "DEDICATED_CREDENTIAL_PURPOSE_REQUIRED",
                )

    def test_commit_helper_credential_cannot_be_reinterpreted_as_owner_approval(self) -> None:
        for purpose in (
            None,
            CredentialPurpose.PERSONAL_MEMORY_COMMIT_DATABASE,
            CredentialPurpose.HUMAN_REVIEWER_DATABASE,
            CredentialPurpose.REVIEW_SERVICE_DATABASE,
            CredentialPurpose.MODEL_PROVIDER,
            CredentialPurpose.MIGRATION_DATABASE,
        ):
            with self.subTest(purpose=purpose):
                with self.assertRaises(PersistenceConfigurationError) as caught:
                    PersonalMemoryApprovalService(
                        runner(purpose),
                        trusted_clock=FixedClock(),
                    )
                self.assertEqual(
                    caught.exception.sanitized_code,
                    "DEDICATED_CREDENTIAL_PURPOSE_REQUIRED",
                )

    def test_no_service_exposes_generic_sql_or_arbitrary_state_authority(self) -> None:
        forbidden = {
            "approve_any",
            "execute",
            "execute_sql",
            "migrate",
            "publish_source",
            "set_patch_state",
            "update_any_table",
        }
        for service in (
            PersonalMemoryApprovalService,
            PersonalMemoryCommitHelper,
            PersonalMemoryActivationService,
        ):
            public = {
                name
                for name, value in inspect.getmembers(service, inspect.isfunction)
                if not name.startswith("_")
            }
            self.assertTrue(public.isdisjoint(forbidden), service.__name__)


class ProviderAuthorityIsolationTests(unittest.TestCase):
    def test_provider_rejects_a_commit_helper_secret(self) -> None:
        raw = "step36-fake-commit-helper-sentinel"
        secret = SecretValue(
            raw,
            purpose=CredentialPurpose.PERSONAL_MEMORY_COMMIT_DATABASE,
            source_name="DATABASE_URL_COMMIT_HELPER",
        )
        with self.assertRaises(ModelAdapterError) as caught:
            OpenRouterDraftV1Adapter(secret)
        self.assertIs(caught.exception.reason_code, ModelReasonCode.MODEL_AUTHENTICATION_FAILED)
        self.assertNotIn(raw, str(caught.exception))

    def test_provider_missing_key_does_not_fall_back_to_database_or_admin_secret(self) -> None:
        environment = {
            "DATABASE_URL": "postgresql://root:admin@example.invalid/defaultdb",
            "DATABASE_URL_APP": "postgresql://app:test@example.invalid/defaultdb",
            "DATABASE_URL_COMMIT_HELPER": "postgresql://commit:test@example.invalid/defaultdb",
            "DATABASE_URL_MIGRATOR": "postgresql://migrator:test@example.invalid/defaultdb",
        }
        with mock.patch.dict(os.environ, environment, clear=True):
            with self.assertRaises(ModelAdapterError) as caught:
                OpenRouterDraftV1Adapter.from_environment()
        self.assertIs(caught.exception.reason_code, ModelReasonCode.MODEL_ADAPTER_UNAVAILABLE)

    def test_provider_adapter_has_no_database_personal_memory_or_review_mutation_port(self) -> None:
        secret = SecretValue(
            "step36-fake-provider-sentinel",
            purpose=CredentialPurpose.MODEL_PROVIDER,
            source_name="OPENROUTER_API_KEY",
        )
        adapter = OpenRouterDraftV1Adapter(secret, transport=lambda *args: {})
        public = {
            name
            for name, value in inspect.getmembers(type(adapter))
            if not name.startswith("_") and callable(value)
        }
        self.assertEqual(public, {"from_environment", "generate", "provider_identity"})
        for forbidden in (
            "approve",
            "commit",
            "activate",
            "review",
            "execute_sql",
            "publish_source",
        ):
            self.assertFalse(hasattr(adapter, forbidden))
        rendered = repr(adapter)
        self.assertNotIn("step36-fake-provider-sentinel", rendered)
        self.assertIn("redacted", rendered)


class ReceiptAndAuthorityTests(unittest.TestCase):
    def test_receipts_bind_lineage_but_contain_no_secret_or_bearer_capability(self) -> None:
        (
            _,
            _,
            approved,
            commit_request,
            committed,
            activation_request,
            active,
        ) = lifecycle_chain()
        receipts = (
            approved.approval_receipt,
            committed.commit_receipt,
            active.activation_receipt,
        )
        forbidden_fragments = (
            "api_key",
            "authorization",
            "credential",
            "database_url",
            "password",
            "private_key",
            "secret",
            "token",
        )
        for receipt in receipts:
            fields = {field.name.casefold() for field in dataclasses.fields(receipt)}
            with self.subTest(receipt=type(receipt).__name__):
                self.assertTrue(
                    all(fragment not in name for name in fields for fragment in forbidden_fragments)
                )
                self.assertRegex(receipt.receipt_hash, r"^[0-9a-f]{64}$")
        self.assertEqual(
            commit_request.approval_receipt_hash,
            approved.approval_receipt.receipt_hash,
        )
        self.assertEqual(
            activation_request.commit_receipt_hash,
            committed.commit_receipt.receipt_hash,
        )
        self.assertFalse(approved.approval_receipt.execution_authority)
        self.assertFalse(active.committed_patch.canonical_evidence)

        serialized = json.dumps(personal_memory_patch_lifecycle_to_jsonb(active))
        for forbidden in forbidden_fragments:
            self.assertNotIn(forbidden, serialized.casefold())

    def test_receipt_hash_alone_is_not_a_typed_commit_request(self) -> None:
        _, _, approved, _, _, _, _ = lifecycle_chain()
        helper = PersonalMemoryCommitHelper(
            runner(CredentialPurpose.PERSONAL_MEMORY_COMMIT_DATABASE),
            trusted_clock=FixedClock(),
        )
        with self.assertRaises((AttributeError, TypeError, ValueError)):
            helper.commit(approved.approval_receipt.receipt_hash)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
