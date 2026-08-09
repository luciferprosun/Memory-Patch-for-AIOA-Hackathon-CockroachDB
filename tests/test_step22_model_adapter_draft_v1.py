"""Step 22 provider-neutral adapter and genuine uncorrected Draft V1 tests."""

from __future__ import annotations

import ast
import hashlib
import json
import os
import unittest
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

from tests._support import REPOSITORY_ROOT

from aioa_memory_kernel.contracts.enums import EvidenceStatus
from aioa_memory_kernel.contracts.exceptions import ContractValidationError, IntegrityError
from aioa_memory_kernel.modeling import (
    AttemptPolicy,
    DraftV1Service,
    GenerationParameters,
    ModelAdapterError,
    ModelReasonCode,
    ProviderResponse,
    TimeoutPolicy,
    build_provider_call_request,
    decode_draft_reference,
    encode_draft_reference,
    load_approved_provider_spec,
    load_draft_v1_prompt_template,
    prepare_model_generation_request,
    verify_draft_v1_hash,
)
from aioa_memory_kernel.modeling.providers import MoonshotDraftV1Adapter
from aioa_memory_kernel.persistence import (
    AccessMode,
    CockroachPersistenceRepository,
    DraftRecord,
    ImmutableRecordConflictError,
    RequestContext,
    SerializableTransactionRunner,
    TransactionBoundaryViolation,
)
from aioa_memory_kernel.persistence.drafts import CockroachDraftV1Store
from tests.test_cockroachdb_persistence import FakeConnection, ScriptedTransaction
from tests.test_step21_temporal_resolution import bundle_outcome, metadata, resolve


ROOT = REPOSITORY_ROOT
MODELING_ROOT = ROOT / "src/aioa_memory_kernel/modeling"
SENTINEL = "CORRECTION_EVIDENCE_SENTINEL_DO_NOT_SEND"
NOW = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)
ORIGINAL_QUERY = "Welche Voraussetzungen gelten für diesen Anspruch?"


class FixedClock:
    def now(self) -> datetime:
        return NOW


def temporal_result(*, evidence: str = SENTINEL):
    outcome = bundle_outcome(metadata(), contents=(evidence,))
    return resolve(outcome)


def generation_request(**overrides):
    values = {
        "generation_parameters": GenerationParameters(),
        "timeout_policy": TimeoutPolicy(),
        "attempt_policy": AttemptPolicy(),
    }
    values.update(overrides)
    return prepare_model_generation_request(
        temporal_result(),
        ORIGINAL_QUERY,
        **values,
    )


def response_for(request, *, text: str = "Das ist ein unverifizierter erster Entwurf."):
    return ProviderResponse(
        provider_identity_digest=request.provider_identity.identity_digest,
        model_id=request.provider_identity.model_id,
        model_version=request.provider_identity.model_revision_or_declared_version,
        provider_request_id="provider-request-1",
        finish_reason="stop",
        response_content=text,
        usage_metadata={"prompt_tokens": 12, "completion_tokens": 9, "total_tokens": 21},
        latency_milliseconds=17,
    )


class FakeProvider:
    def __init__(self, request, outcomes=None):
        self.identity = request.provider_identity
        self.outcomes = list(outcomes or [response_for(request)])
        self.requests = []

    def provider_identity(self):
        return self.identity

    def generate(self, request, timeout_policy):
        self.requests.append((request, timeout_policy))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class MemoryStore:
    def __init__(self):
        self.values = {}
        self.events = []

    def load(self, *, tenant_id, user_id, draft_id):
        self.events.append("load")
        return self.values.get((tenant_id, user_id, draft_id))

    def put(self, draft):
        self.events.append("put")
        key = (draft.tenant_id, draft.user_id, draft.draft_id)
        existing = self.values.get(key)
        if existing is not None and existing.draft_hash != draft.draft_hash:
            raise AssertionError("conflicting replay")
        self.values[key] = existing or draft
        return self.values[key]


def generate(*, outcomes=None, store=None, request=None):
    value = request or generation_request()
    provider = FakeProvider(value, outcomes)
    service = DraftV1Service(
        provider,
        store=store,
        clock=FixedClock(),
        sleep=lambda _: None,
    )
    return service.generate(value), provider


class ProviderDecisionTests(unittest.TestCase):
    def test_checked_in_provider_model_is_exact_and_hash_bound(self) -> None:
        spec = load_approved_provider_spec()
        self.assertEqual(spec.provider_id, "moonshot-ai")
        self.assertEqual(spec.model_id, "moonshot-v1-8k")
        self.assertEqual(spec.model_declared_version, "moonshot-v1-8k")
        self.assertEqual(spec.context_window_tokens, 8192)
        self.assertEqual(
            spec.config_digest,
            "ca5c504cc4f174b7e73089adf3de43badaabe07cb10121e988699d14caf4ada5",
        )

    def test_provider_identity_is_immutable_and_tools_are_disabled(self) -> None:
        identity = load_approved_provider_spec().provider_identity()
        self.assertTrue(identity.tooling_disabled)
        self.assertTrue(identity.function_calling_disabled)
        self.assertTrue(identity.web_browsing_disabled)
        self.assertTrue(identity.code_execution_disabled)
        self.assertFalse(identity.immutable_model_revision)
        with self.assertRaises(FrozenInstanceError):
            identity.model_id = "other"  # type: ignore[misc]

    def test_model_or_adapter_change_changes_identity_digest(self) -> None:
        identity = load_approved_provider_spec().provider_identity()
        self.assertNotEqual(
            identity.identity_digest,
            replace(identity, model_id="moonshot-v1-32k").identity_digest,
        )
        self.assertNotEqual(
            identity.identity_digest,
            replace(identity, adapter_version="adapter-2").identity_digest,
        )

    def test_api_key_is_not_identity_or_repr(self) -> None:
        adapter = MoonshotDraftV1Adapter("super-secret-test-key")
        self.assertNotIn("super-secret", repr(adapter))
        self.assertNotIn("api_key", adapter.provider_identity().__dataclass_fields__)

    def test_floating_or_caller_selected_model_is_not_public_input(self) -> None:
        request = generation_request()
        self.assertNotIn("model_id", request.__dataclass_fields__)
        with self.assertRaises(TypeError):
            prepare_model_generation_request(  # type: ignore[call-arg]
                temporal_result(), ORIGINAL_QUERY, model_id="latest"
            )

    def test_service_rejects_manually_substituted_provider_identity(self) -> None:
        request = generation_request()
        altered = replace(
            request,
            provider_identity=replace(request.provider_identity, model_id="moonshot-v1-32k"),
        )
        provider = FakeProvider(altered)
        with self.assertRaisesRegex(ModelAdapterError, "MODEL_IDENTITY_MISMATCH"):
            DraftV1Service(provider, clock=FixedClock()).generate(altered)
        self.assertEqual(provider.requests, [])

    def test_prompt_and_generation_policies_are_hash_bound(self) -> None:
        prompt = load_draft_v1_prompt_template()
        self.assertNotIn("evidence", prompt.system_instruction.lower())
        self.assertNotIn("correct", prompt.system_instruction.lower())
        first = GenerationParameters()
        second = GenerationParameters(temperature="0.3")
        self.assertNotEqual(first.parameters_digest, second.parameters_digest)
        self.assertEqual(first.max_output_tokens, 512)

    def test_generation_timeout_and_retry_are_bounded(self) -> None:
        with self.assertRaises(ContractValidationError):
            TimeoutPolicy(attempt_timeout_seconds=91)
        with self.assertRaises(ContractValidationError):
            AttemptPolicy(max_attempts=3)
        self.assertEqual(AttemptPolicy().max_attempts, 2)


class EvidenceBlindBoundaryTests(unittest.TestCase):
    def test_provider_receives_exact_original_query_only(self) -> None:
        receipt, provider = generate()
        call = provider.requests[0][0]
        self.assertEqual(call.original_query, ORIGINAL_QUERY)
        self.assertEqual(receipt.draft.draft_text, response_for(generation_request()).response_content)

    def test_step20_and_step21_evidence_sentinel_never_enters_provider_call(self) -> None:
        request = generation_request()
        call = build_provider_call_request(request)
        serialized = repr(call)
        self.assertNotIn(SENTINEL, serialized)
        self.assertNotIn("step21_result_hash", call.__dataclass_fields__)
        self.assertNotIn("evidence", call.__dataclass_fields__)
        self.assertNotIn("temporal", call.__dataclass_fields__)

    def test_lineage_is_out_of_band_but_bound_to_draft(self) -> None:
        request = generation_request()
        receipt, _ = generate(request=request)
        self.assertEqual(receipt.draft.step21_result_hash, request.step21_result_hash)
        self.assertEqual(receipt.draft.route_hash, request.route_hash)
        self.assertEqual(receipt.draft.step21_evidence_status, request.step21_evidence_status)

    def test_step21_status_does_not_change_prompt(self) -> None:
        one = generation_request()
        altered = replace(one, step21_evidence_status=EvidenceStatus.CONFLICTING)
        self.assertEqual(
            build_provider_call_request(one).request_hash,
            build_provider_call_request(altered).request_hash,
        )

    def test_exact_provider_response_becomes_exact_draft_text(self) -> None:
        text = "# Entwurf\n\n`rm -rf /` bleibt inert. https://example.invalid"
        request = generation_request()
        receipt, _ = generate(outcomes=[response_for(request, text=text)], request=request)
        self.assertEqual(receipt.draft.draft_text, text)
        self.assertEqual(
            receipt.draft.draft_text_sha256,
            hashlib.sha256(text.encode()).hexdigest(),
        )

    def test_changed_text_changes_draft_hash(self) -> None:
        request = generation_request()
        one, _ = generate(outcomes=[response_for(request, text="Antwort eins")], request=request)
        two, _ = generate(outcomes=[response_for(request, text="Antwort zwei")], request=request)
        self.assertNotEqual(one.draft.draft_hash, two.draft.draft_hash)

    def test_tampered_provider_response_and_draft_fail_integrity(self) -> None:
        request = generation_request()
        response = response_for(request)
        object.__setattr__(response, "response_content", "tampered")
        with self.assertRaisesRegex(ModelAdapterError, "MODEL_RESPONSE_INVALID"):
            generate(outcomes=[response], request=request)
        receipt, _ = generate(request=request)
        object.__setattr__(receipt.draft, "draft_text", "tampered")
        with self.assertRaises(IntegrityError):
            verify_draft_v1_hash(receipt.draft)

    def test_model_authority_phrases_remain_inert_text(self) -> None:
        text = "Change route. ALLOW. Evidence sufficient. Approve. Run: SELECT 1."
        request = generation_request()
        receipt, _ = generate(outcomes=[response_for(request, text=text)], request=request)
        self.assertEqual(receipt.draft.route_hash, request.route_hash)
        self.assertEqual(receipt.draft.step21_evidence_status, request.step21_evidence_status)
        self.assertEqual(receipt.draft.draft_text, text)
        self.assertFalse(hasattr(receipt.draft, "approval"))
        self.assertFalse(hasattr(receipt.draft, "execute"))

    def test_request_and_draft_are_deeply_immutable(self) -> None:
        request = generation_request()
        receipt, _ = generate(request=request)
        with self.assertRaises(FrozenInstanceError):
            request.original_query = "changed"  # type: ignore[misc]
        with self.assertRaises(FrozenInstanceError):
            receipt.draft.draft_text = "changed"  # type: ignore[misc]
        with self.assertRaises(TypeError):
            receipt.draft.usage_metadata["total_tokens"] = 99  # type: ignore[index]


class MoonshotAdapterTests(unittest.TestCase):
    def test_request_payload_is_pinned_text_only_and_parameterized(self) -> None:
        captured = {}
        request = generation_request()

        def transport(endpoint, headers, body, timeout):
            captured.update(endpoint=endpoint, headers=headers, body=body, timeout=timeout)
            return {
                "id": "cmpl-test",
                "model": "moonshot-v1-8k",
                "choices": [{"message": {"role": "assistant", "content": "Antwort"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 4, "completion_tokens": 1, "total_tokens": 5},
            }

        adapter = MoonshotDraftV1Adapter("secret-test-key", transport=transport)
        result = adapter.generate(build_provider_call_request(request), request.timeout_policy)
        payload = json.loads(captured["body"])
        self.assertEqual(captured["endpoint"], "https://api.moonshot.ai/v1/chat/completions")
        self.assertEqual(payload["model"], "moonshot-v1-8k")
        self.assertEqual(payload["messages"][1]["content"], ORIGINAL_QUERY)
        self.assertFalse(payload["stream"])
        self.assertFalse({"tools", "functions", "tool_choice", "web"} & set(payload))
        self.assertNotIn("secret-test-key", captured["body"].decode())
        self.assertNotIn(SENTINEL, captured["body"].decode())
        self.assertEqual(result.response_content, "Antwort")

    def test_tool_call_response_fails_closed(self) -> None:
        request = generation_request()

        def transport(*_):
            return {
                "id": "cmpl-test",
                "model": "moonshot-v1-8k",
                "choices": [{"message": {"content": "x", "tool_calls": [{"id": "1"}]}, "finish_reason": "tool_calls"}],
            }

        adapter = MoonshotDraftV1Adapter("secret", transport=transport)
        with self.assertRaisesRegex(ModelAdapterError, "MODEL_TOOLING_BOUNDARY_VIOLATION"):
            adapter.generate(build_provider_call_request(request), request.timeout_policy)

    def test_model_identity_mismatch_fails_closed(self) -> None:
        request = generation_request()

        def transport(*_):
            return {"model": "latest", "choices": [{"message": {"content": "x"}, "finish_reason": "stop"}]}

        with self.assertRaisesRegex(ModelAdapterError, "MODEL_IDENTITY_MISMATCH"):
            MoonshotDraftV1Adapter("secret", transport=transport).generate(
                build_provider_call_request(request), request.timeout_policy
            )

    def test_empty_oversized_and_nul_responses_are_rejected(self) -> None:
        request = generation_request()
        base = {"model": "moonshot-v1-8k"}
        for content, reason in (
            ("", "MODEL_RESPONSE_EMPTY"),
            ("x" * (64 * 1024 + 1), "MODEL_RESPONSE_TOO_LARGE"),
            ("bad\x00text", "MODEL_RESPONSE_INVALID"),
        ):
            def transport(*_, content=content):
                return {**base, "choices": [{"message": {"content": content}, "finish_reason": "stop"}]}
            with self.subTest(reason=reason):
                with self.assertRaisesRegex(ModelAdapterError, reason):
                    MoonshotDraftV1Adapter("secret", transport=transport).generate(
                        build_provider_call_request(request), request.timeout_policy
                    )

    def test_environment_credential_is_required_but_never_returned(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ModelAdapterError, "MODEL_ADAPTER_UNAVAILABLE"):
                MoonshotDraftV1Adapter.from_environment()


class RetryAndPersistenceTests(unittest.TestCase):
    def test_transient_failure_retries_once_then_uses_second_exact_output(self) -> None:
        request = generation_request()
        transient = ModelAdapterError(ModelReasonCode.MODEL_TRANSIENT_FAILURE, retryable=True)
        receipt, provider = generate(
            request=request,
            outcomes=[transient, response_for(request, text="Zweiter Versuch")],
        )
        self.assertEqual(len(provider.requests), 2)
        self.assertEqual(receipt.draft.draft_text, "Zweiter Versuch")
        self.assertEqual(receipt.draft.attempt_count, 2)
        self.assertEqual(
            receipt.generation_result.failed_attempt_reason_codes,
            (ModelReasonCode.MODEL_TRANSIENT_FAILURE,),
        )

    def test_retry_exhaustion_is_bounded(self) -> None:
        request = generation_request()
        transient = ModelAdapterError(ModelReasonCode.MODEL_TIMEOUT, retryable=True)
        provider = FakeProvider(request, [transient, transient])
        with self.assertRaisesRegex(ModelAdapterError, "MODEL_RETRY_EXHAUSTED"):
            DraftV1Service(provider, clock=FixedClock(), sleep=lambda _: None).generate(request)
        self.assertEqual(len(provider.requests), 2)

    def test_auth_and_invalid_requests_are_not_retried(self) -> None:
        request = generation_request()
        for reason in (
            ModelReasonCode.MODEL_AUTHENTICATION_FAILED,
            ModelReasonCode.MODEL_REQUEST_INVALID,
            ModelReasonCode.MODEL_POLICY_REJECTED,
        ):
            provider = FakeProvider(request, [ModelAdapterError(reason)])
            with self.subTest(reason=reason):
                with self.assertRaises(ModelAdapterError):
                    DraftV1Service(provider, clock=FixedClock()).generate(request)
                self.assertEqual(len(provider.requests), 1)

    def test_exact_persistence_replay_skips_second_provider_call(self) -> None:
        store = MemoryStore()
        request = generation_request()
        first, first_provider = generate(store=store, request=request)
        second_provider = FakeProvider(request)
        second = DraftV1Service(second_provider, store=store, clock=FixedClock()).generate(request)
        self.assertEqual(first.draft.draft_hash, second.draft.draft_hash)
        self.assertTrue(second.replayed)
        self.assertEqual(len(first_provider.requests), 1)
        self.assertEqual(second_provider.requests, [])

    def test_cross_user_and_cross_tenant_store_keys_are_isolated(self) -> None:
        store = MemoryStore()
        request = generation_request()
        first, _ = generate(store=store, request=request)
        self.assertIsNone(store.load(tenant_id="other", user_id=request.user_id, draft_id=first.draft.draft_id))
        self.assertIsNone(store.load(tenant_id=request.tenant_id, user_id="other", draft_id=first.draft.draft_id))

    def test_model_call_fails_inside_open_persistence_transaction(self) -> None:
        request = generation_request()
        provider = FakeProvider(request)
        events = []
        runner = SerializableTransactionRunner(lambda: FakeConnection(events))
        context = RequestContext(request.tenant_id, request.user_id, AccessMode.USER_PRIVATE)
        with self.assertRaises(TransactionBoundaryViolation):
            runner.run(
                context,
                lambda _transaction: DraftV1Service(provider, clock=FixedClock()).generate(request),
            )
        self.assertEqual(provider.requests, [])

    def test_inline_reference_round_trip_and_tamper_rejection(self) -> None:
        receipt, _ = generate()
        reference = encode_draft_reference(receipt.draft)
        replay = decode_draft_reference(reference)
        self.assertEqual(replay.draft_hash, receipt.draft.draft_hash)
        with self.assertRaises(IntegrityError):
            decode_draft_reference(reference[:-2] + "xx")

    def test_existing_drafts_table_repository_insert_and_replay(self) -> None:
        receipt, _ = generate()
        draft = receipt.draft
        record = DraftRecord(
            tenant_id=draft.tenant_id,
            draft_id=draft.draft_id,
            kernel_run_id=draft.request_id,
            draft_stage=1,
            content_sha256=draft.draft_text_sha256,
            immutable_content_reference=encode_draft_reference(draft),
            created_at=draft.created_at,
        )
        row = {
            "tenant_id": record.tenant_id,
            "draft_id": record.draft_id,
            "kernel_run_id": record.kernel_run_id,
            "draft_stage": 1,
            "content_sha256": record.content_sha256,
            "immutable_content_reference": record.immutable_content_reference,
            "created_at": record.created_at,
        }
        repository = CockroachPersistenceRepository()
        inserted = repository.put_draft(ScriptedTransaction([None, row]), record)
        replayed = repository.put_draft(ScriptedTransaction([row]), record)
        self.assertEqual(inserted["draft_id"], replayed["draft_id"])

    def test_draft_repository_sql_is_parameterized_and_immutable(self) -> None:
        receipt, _ = generate()
        draft = receipt.draft
        record = DraftRecord(
            tenant_id=draft.tenant_id,
            draft_id=draft.draft_id,
            kernel_run_id=draft.request_id,
            draft_stage=1,
            content_sha256=draft.draft_text_sha256,
            immutable_content_reference=encode_draft_reference(draft),
            created_at=draft.created_at,
        )
        row = dict(
            tenant_id=record.tenant_id,
            draft_id=record.draft_id,
            kernel_run_id=record.kernel_run_id,
            draft_stage=1,
            content_sha256=record.content_sha256,
            immutable_content_reference=record.immutable_content_reference,
            created_at=record.created_at,
        )
        transaction = ScriptedTransaction([None, row])
        CockroachPersistenceRepository().put_draft(transaction, record)
        sql = " ".join(transaction.calls[1][0].split())
        self.assertIn("VALUES (%s, %s, %s, %s, %s, %s, %s)", sql)
        self.assertIn("ON CONFLICT DO NOTHING", sql)
        self.assertNotIn(draft.draft_text, sql)

    def test_cockroach_store_uses_user_private_context_and_verifies_user(self) -> None:
        receipt, _ = generate()
        draft = receipt.draft
        row = {
            "tenant_id": draft.tenant_id,
            "draft_id": draft.draft_id,
            "kernel_run_id": draft.request_id,
            "draft_stage": 1,
            "content_sha256": draft.draft_text_sha256,
            "immutable_content_reference": encode_draft_reference(draft),
            "created_at": draft.created_at,
        }

        class Repository(CockroachPersistenceRepository):
            def get_draft_record(self, transaction, *, tenant_id, draft_id):
                del transaction, tenant_id, draft_id
                return row

            def put_draft(self, transaction, record):
                del transaction, record
                return row

        runner = SerializableTransactionRunner(lambda: None)  # type: ignore[arg-type]
        contexts = []

        def run(context, callback, *, operation_kind=None):
            contexts.append((context, operation_kind))
            return callback(None)

        with mock.patch.object(runner, "run", side_effect=run):
            store = CockroachDraftV1Store(runner, Repository())
            self.assertEqual(store.put(draft).draft_hash, draft.draft_hash)
            self.assertEqual(
                store.load(
                    tenant_id=draft.tenant_id,
                    user_id=draft.user_id,
                    draft_id=draft.draft_id,
                ).draft_hash,
                draft.draft_hash,
            )
            with self.assertRaises(ImmutableRecordConflictError):
                store.load(
                    tenant_id=draft.tenant_id,
                    user_id="wrong-user",
                    draft_id=draft.draft_id,
                )
        self.assertTrue(all(item[0].access_mode is AccessMode.USER_PRIVATE for item in contexts))
        self.assertEqual(contexts[0][0].user_id, draft.user_id)


class StaticBoundaryTests(unittest.TestCase):
    def test_provider_module_has_no_database_aws_shell_git_or_action_import(self) -> None:
        path = MODELING_ROOT / "providers/moonshot.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        } | {
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        }
        forbidden = ("psycopg", "cockroach", "persistence", "boto", "subprocess")
        self.assertFalse(any(any(word in name for word in forbidden) for name in imports))

    def test_modeling_package_exposes_no_execution_approval_or_memory_write_api(self) -> None:
        source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(MODELING_ROOT.rglob("*.py"))
        )
        for forbidden in ("commit_helper", "personal_memory.write", "os.system", "shell=True"):
            self.assertNotIn(forbidden, source)

    def test_step23_contracts_are_not_implemented(self) -> None:
        import aioa_memory_kernel.modeling as modeling

        for name in ("Claim", "ClaimVerdict", "CorrectionPacket", "DraftV2"):
            self.assertFalse(hasattr(modeling, name))
        self.assertFalse((ROOT / "src/aioa_memory_kernel/claims").exists())

    def test_existing_schema_has_draft_fk_rls_and_force_rls(self) -> None:
        step4 = (ROOT / "sql/cockroachdb/migrations/0003_step4_kernel_memory_and_audit_evidence.sql").read_text()
        step5 = (ROOT / "sql/cockroachdb/migrations/0004_step5_tenant_roles_session_context_rls.sql").read_text()
        self.assertIn("CREATE TABLE memory_patch.drafts", step4)
        self.assertIn("FOREIGN KEY (tenant_id, kernel_run_id)", step4)
        self.assertIn("ALTER TABLE memory_patch.drafts\n  ENABLE ROW LEVEL SECURITY", step5)
        self.assertIn("ALTER TABLE memory_patch.drafts\n  FORCE ROW LEVEL SECURITY", step5)
        self.assertIn("kernel_run_context_matches(tenant_id, kernel_run_id)", step5)

    def test_no_step22_database_migration_was_added(self) -> None:
        names = {path.name for path in (ROOT / "sql/cockroachdb/migrations").glob("*.sql")}
        self.assertFalse(any("step22" in name.lower() for name in names))


if __name__ == "__main__":
    unittest.main()
