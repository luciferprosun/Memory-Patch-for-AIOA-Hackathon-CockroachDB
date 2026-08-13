"""Focused R5 provider identity, durable budget, and failure-safety tests."""

from __future__ import annotations

import dataclasses
import hashlib
import inspect
import threading
import unittest
from types import SimpleNamespace

from aioa_memory_kernel.demo_runtime.composition import (
    ProviderRuntimeServiceDependencyFactory,
    RuntimeStartupRecorder,
    RuntimeStartupStage,
)
from aioa_memory_kernel.demo_runtime.config import (
    PROVIDER_BUDGET_EPOCH_ENV,
    PROVIDER_TENANT_ID_ENV,
    RUNTIME_MODE_ENV,
    RuntimeAssemblyError,
    RuntimeErrorCode,
    RuntimeMode,
    RuntimeProviderGuardSettings,
    RuntimeSettings,
)
from aioa_memory_kernel.demo_runtime.provider_guard import (
    CALL_GLOBAL_KIND,
    CALL_OWNER_KIND,
    CockroachProviderGuardLedger,
    GuardReservation,
    GuardedProviderAdapter,
    ProviderCallPurpose,
    ProviderGuardLedgerError,
    ProviderRequestScope,
)
from aioa_memory_kernel.modeling import (
    GenerationParameters,
    ModelAdapterError,
    ModelReasonCode,
    ProviderCallRequest,
    ProviderResponse,
    ProviderTextRequest,
    TimeoutPolicy,
    load_approved_provider_spec,
    load_draft_v1_prompt_template,
    load_step22_moonshot_provider_spec,
)
from aioa_memory_kernel.persistence import SerializableTransactionRunner
from aioa_memory_kernel.security.credentials import CredentialPurpose
from aioa_memory_kernel.verification.prompt import DRAFT_V2_PROVIDER_PURPOSE


def _limits(**changes: int | str | None) -> RuntimeProviderGuardSettings:
    values: dict[str, int | str | None] = {
        "budget_epoch": "jury-budget-r5-1a",
        "tenant_id": "tenant-r5",
        "maximum_requests_total": 24,
        "maximum_requests_per_owner": 8,
        "maximum_requests_per_session": 6,
        "request_window_seconds": 60,
        "maximum_requests_per_window_global": 12,
        "maximum_requests_per_window_owner": 4,
        "maximum_requests_per_window_session": 3,
        "maximum_calls_total": 32,
        "maximum_calls_per_owner": 12,
        "maximum_calls_per_session": 10,
        "maximum_calls_per_request": 8,
        "maximum_concurrent_calls": 1,
        "maximum_queued_calls": 2,
        "queue_wait_seconds": 1,
        "maximum_input_bytes": 24 * 1024,
        "maximum_output_tokens": 1024,
        "timeout_seconds": 45,
    }
    values.update(changes)
    return RuntimeProviderGuardSettings(**values)  # type: ignore[arg-type]


def _scope(suffix: str = "one") -> ProviderRequestScope:
    return ProviderRequestScope(
        tenant_id="tenant-r5",
        owner_user_id="owner-r5",
        session_id="session-r5",
        request_id=f"request-r5-{suffix}",
    )


def _draft_v1_request(*, max_output_tokens: int = 96) -> ProviderCallRequest:
    spec = load_approved_provider_spec()
    prompt = load_draft_v1_prompt_template()
    question = "What is the effective date?"
    return ProviderCallRequest(
        provider_identity=spec.provider_identity(),
        prompt_template_id=prompt.template_id,
        prompt_template_digest=prompt.template_digest,
        system_instruction=prompt.system_instruction,
        original_query=question,
        original_query_digest=hashlib.sha256(question.encode("utf-8")).hexdigest(),
        generation_parameters=GenerationParameters(max_output_tokens=max_output_tokens),
    )


def _text_request(
    purpose: str = DRAFT_V2_PROVIDER_PURPOSE,
    *,
    max_output_tokens: int = 96,
) -> ProviderTextRequest:
    spec = load_approved_provider_spec()
    content = '{"bounded":"correction"}'
    instruction = "Return bounded inert text only."
    return ProviderTextRequest(
        provider_identity=spec.provider_identity(),
        purpose=purpose,
        prompt_template_id="r5-provider-purpose-test",
        prompt_template_digest=hashlib.sha256(instruction.encode("utf-8")).hexdigest(),
        system_instruction=instruction,
        user_content=content,
        user_content_digest=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        generation_parameters=GenerationParameters(
            max_output_tokens=max_output_tokens,
        ),
    )


class _Provider:
    def __init__(self, outcomes=None, *, model_id: str | None = None) -> None:
        self.outcomes = list(outcomes or ())
        self.calls = 0
        self.closed = 0
        self.model_id = model_id

    def provider_identity(self):
        return load_approved_provider_spec().provider_identity()

    def generate(self, request, timeout_policy):
        self.calls += 1
        if self.outcomes:
            outcome = self.outcomes.pop(0)
            if isinstance(outcome, BaseException):
                raise outcome
        identity = self.provider_identity()
        return ProviderResponse(
            provider_identity_digest=identity.identity_digest,
            model_id=self.model_id or identity.model_id,
            model_version=identity.model_revision_or_declared_version,
            provider_request_id=f"provider-r5-{self.calls}",
            finish_reason="stop",
            response_content="Bounded answer.",
            usage_metadata={"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14},
            latency_milliseconds=1,
        )

    def close(self):
        self.closed += 1


class _MemoryLedger:
    durable = True

    def __init__(self) -> None:
        self.requests: set[str] = set()
        self.calls: list[ProviderCallPurpose] = []
        self.completions: list[tuple[str, object, object, object]] = []
        self.denial: ModelReasonCode | None = None

    def reserve_request(self, scope):
        if self.denial is not None:
            raise ProviderGuardLedgerError(self.denial)
        if scope.request_digest in self.requests:
            raise ProviderGuardLedgerError(ModelReasonCode.MODEL_DUPLICATE_REQUEST)
        self.requests.add(scope.request_digest)
        return GuardReservation(scope, "request-global", "request-owner", "REQUEST")

    def reserve_call(self, scope, *, provider_request_hash, purpose, attempt_number):
        if self.denial is not None:
            raise ProviderGuardLedgerError(self.denial)
        self.calls.append(purpose)
        return GuardReservation(
            scope,
            f"call-global-{attempt_number}",
            f"call-owner-{attempt_number}",
            "CALL",
        )

    def complete(
        self,
        reservation,
        *,
        result_digest,
        usage_reference,
        error_code,
        unknown_completion,
    ):
        self.completions.append(
            (reservation.reservation_kind, result_digest, usage_reference, error_code)
        )


class _NeverPermit:
    def acquire(self, blocking=True, timeout=None):
        return False

    def release(self):
        raise AssertionError("unacquired permit must not be released")


class _FakeTransaction:
    def __init__(self) -> None:
        self.rows: list[dict[str, object]] = []
        self.current_user_id: str | None = None

    def fetch_one(self, sql, parameters=None):
        parameters = tuple(parameters or ())
        if "SELECT operation_id" in sql:
            tenant, kind_a, kind_b, key, origin_kind, origin_version = parameters
            for row in self.rows:
                if (
                    row["tenant_id"] == tenant
                    and row["operation_kind"] in {kind_a, kind_b}
                    and row["idempotency_key"] == key
                    and row["origin_kind"] == origin_kind
                    and row["origin_version"] == origin_version
                ):
                    return {"operation_id": row["operation_id"]}
            return None
        if "AS global_count" in sql:
            (
                global_kind,
                owner_kind,
                _owner_kind_session,
                session_digest,
                _owner_kind_request,
                request_digest,
                tenant,
                origin_kind,
                origin_system,
                origin_version,
                _kind_a,
                _kind_b,
            ) = parameters
            eligible = [
                row
                for row in self.rows
                if row["tenant_id"] == tenant
                and row["origin_kind"] == origin_kind
                and row["origin_system"] == origin_system
                and row["origin_version"] == origin_version
                and row["operation_kind"] in {global_kind, owner_kind}
                and (
                    row["operation_kind"] == global_kind
                    or row["owner_user_id"] == self.current_user_id
                )
            ]
            return {
                "global_count": sum(
                    row["operation_kind"] == global_kind for row in eligible
                ),
                "owner_count": sum(
                    row["operation_kind"] == owner_kind for row in eligible
                ),
                "session_count": sum(
                    row["operation_kind"] == owner_kind
                    and row["scope_digest"] == session_digest
                    for row in eligible
                ),
                "request_count": sum(
                    row["operation_kind"] == owner_kind
                    and row["request_digest"] == request_digest
                    for row in eligible
                ),
            }
        if "AS requests_reserved" in sql:
            (
                request_global_kind,
                call_global_kind,
                _completed_kind,
                _failed_kind,
                _interrupted_kind,
                call_owner_kind,
                owner_user_id,
                _session_owner_kind,
                session_digest,
                tenant,
                origin_kind,
                origin_system,
                origin_version,
                *_operation_kinds,
            ) = parameters
            eligible = [
                row
                for row in self.rows
                if row["tenant_id"] == tenant
                and row["origin_kind"] == origin_kind
                and row["origin_system"] == origin_system
                and row["origin_version"] == origin_version
            ]
            global_calls = [
                row
                for row in eligible
                if row["operation_kind"] == call_global_kind
            ]
            return {
                "requests_reserved": sum(
                    row["operation_kind"] == request_global_kind
                    for row in eligible
                ),
                "calls_reserved": len(global_calls),
                "calls_completed": sum(
                    row["status"] == "COMPLETED" for row in global_calls
                ),
                "calls_failed": sum(
                    row["status"] == "FAILED_FINAL" for row in global_calls
                ),
                "calls_unknown_completion": sum(
                    row["status"] == "INTERRUPTED" for row in global_calls
                ),
                "owner_calls_reserved": sum(
                    row["operation_kind"] == call_owner_kind
                    and row["owner_user_id"] == owner_user_id
                    for row in eligible
                ),
                "session_calls_reserved": sum(
                    row["operation_kind"] == call_owner_kind
                    and row["scope_digest"] == session_digest
                    for row in eligible
                ),
            }
        if "INSERT INTO memory_patch.persistence_operations" in sql:
            (
                tenant,
                operation_id,
                owner_user_id,
                operation_kind,
                idempotency_key,
                request_digest,
                scope_digest,
                _created,
                _updated,
                origin_kind,
                origin_system,
                origin_version,
                adapter_version,
                artifact_kind,
                external_ref,
            ) = parameters
            row = {
                "tenant_id": tenant,
                "operation_id": operation_id,
                "owner_user_id": owner_user_id,
                "operation_kind": operation_kind,
                "idempotency_key": idempotency_key,
                "request_digest": request_digest,
                "scope_digest": scope_digest,
                "origin_kind": origin_kind,
                "origin_system": origin_system,
                "origin_version": origin_version,
                "adapter_version": adapter_version,
                "artifact_kind": artifact_kind,
                "external_ref": external_ref,
                "status": "IN_PROGRESS",
            }
            if any(
                existing["tenant_id"] == tenant
                and existing["operation_kind"] == operation_kind
                and existing["idempotency_key"] == idempotency_key
                for existing in self.rows
            ):
                return None
            self.rows.append(row)
            return {"operation_id": operation_id}
        raise AssertionError("unexpected provider-guard SQL")

    def execute(self, sql, parameters=None):
        if "UPDATE memory_patch.persistence_operations" not in sql:
            raise AssertionError("unexpected provider-guard update")
        values = tuple(parameters or ())
        status, *_metadata, tenant, global_id, owner_id = values
        for row in self.rows:
            if (
                row["tenant_id"] == tenant
                and row["operation_id"] in {global_id, owner_id}
                and row["status"] == "IN_PROGRESS"
            ):
                row["status"] = status


class _FakeRunner(SerializableTransactionRunner):
    def __init__(self, transaction: _FakeTransaction) -> None:
        self.transaction = transaction

    def run(self, context, callback, *, operation_kind=None):
        self.transaction.current_user_id = context.user_id
        try:
            return callback(self.transaction)
        finally:
            self.transaction.current_user_id = None


class RuntimeProviderConfigurationTests(unittest.TestCase):
    def test_exact_provider_identity_and_secret_are_server_side_and_redacted(self):
        sentinel = "r5-provider-secret-sentinel"
        settings = RuntimeSettings.from_mapping(
            {
                RUNTIME_MODE_ENV: RuntimeMode.TEST.value,
                "OPENROUTER_API_KEY": sentinel,
                PROVIDER_BUDGET_EPOCH_ENV: "jury-budget-r5-1a",
                PROVIDER_TENANT_ID_ENV: "tenant-r5",
            }
        )
        provider = settings.require_provider()
        spec = load_approved_provider_spec()
        self.assertEqual(
            (provider.provider_id, provider.model_id, provider.adapter_id),
            (spec.provider_id, spec.model_id, spec.adapter_version),
        )
        rendered = repr(settings) + repr(provider)
        self.assertNotIn(sentinel, rendered)
        self.assertIn("<redacted>", rendered)

    def test_missing_provider_key_and_budget_epoch_fail_closed_when_required(self):
        settings = RuntimeSettings.from_mapping({RUNTIME_MODE_ENV: "TEST"})
        with self.assertRaises(RuntimeAssemblyError) as key_error:
            settings.require_provider()
        self.assertEqual(
            key_error.exception.code, RuntimeErrorCode.PROVIDER_CREDENTIAL_MISSING
        )
        hosted_like = dataclasses.replace(
            settings,
            provider_guard=dataclasses.replace(
                settings.require_provider_guard(), budget_epoch=None
            ),
        )
        with self.assertRaises(RuntimeAssemblyError) as epoch_error:
            hosted_like.require_provider_guard()
        self.assertEqual(
            epoch_error.exception.code,
            RuntimeErrorCode.PROVIDER_GUARD_CONFIG_INVALID,
        )
        missing_tenant = dataclasses.replace(
            settings,
            provider_guard=dataclasses.replace(
                settings.require_provider_guard(), tenant_id=None
            ),
        )
        with self.assertRaises(RuntimeAssemblyError) as tenant_error:
            missing_tenant.require_provider_guard()
        self.assertEqual(
            tenant_error.exception.code,
            RuntimeErrorCode.PROVIDER_GUARD_CONFIG_INVALID,
        )

    def test_guard_ordering_and_step40_bounds_are_fail_closed(self):
        with self.assertRaises(RuntimeAssemblyError):
            _limits(maximum_calls_per_session=13)
        with self.assertRaises(RuntimeAssemblyError):
            _limits(maximum_concurrent_calls=2)
        with self.assertRaises(RuntimeAssemblyError):
            _limits(maximum_queued_calls=3)


class GuardedProviderTests(unittest.TestCase):
    def _guard(self, provider=None, ledger=None, limits=None):
        provider = provider or _Provider()
        ledger = ledger or _MemoryLedger()
        return (
            GuardedProviderAdapter(
                provider,
                ledger=ledger,
                limits=limits or _limits(),
                monotonic=lambda: 100.0,
            ),
            provider,
            ledger,
        )

    def test_paid_call_requires_trusted_request_scope(self):
        guard, provider, _ledger = self._guard()
        with self.assertRaises(ModelAdapterError) as raised:
            guard.generate(_draft_v1_request(), TimeoutPolicy())
        self.assertEqual(
            raised.exception.reason_code,
            ModelReasonCode.MODEL_REQUEST_SCOPE_REQUIRED,
        )
        self.assertEqual(provider.calls, 0)

    def test_guard_is_pinned_to_one_demo_tenant_for_a_true_global_ceiling(self):
        guard, provider, ledger = self._guard()
        cross_tenant = dataclasses.replace(_scope(), tenant_id="tenant-other")
        with self.assertRaises(ModelAdapterError) as raised:
            with guard.request_scope(cross_tenant):
                guard.generate(_draft_v1_request(), TimeoutPolicy())
        self.assertEqual(
            raised.exception.reason_code,
            ModelReasonCode.MODEL_POLICY_REJECTED,
        )
        self.assertEqual(provider.calls, 0)
        self.assertEqual(ledger.requests, set())

    def test_success_reserves_before_call_reconciles_and_redacts(self):
        guard, provider, ledger = self._guard()
        with guard.request_scope(_scope()):
            response = guard.generate(_draft_v1_request(), TimeoutPolicy())
        self.assertEqual(response.response_content, "Bounded answer.")
        self.assertEqual(provider.calls, 1)
        self.assertEqual(ledger.calls, [ProviderCallPurpose.DRAFT_V1])
        self.assertEqual(
            [kind for kind, _digest_value, _usage, _error in ledger.completions],
            ["CALL", "REQUEST"],
        )
        self.assertEqual(
            ledger.completions[0][2],
            "provider-reported-tokens:prompt=10;completion=4;total=14;"
            "response_bytes=15",
        )
        self.assertIn("<redacted>", repr(guard))

    def test_budget_and_call_limit_denials_make_zero_provider_calls(self):
        for reason in (
            ModelReasonCode.MODEL_BUDGET_EXHAUSTED,
            ModelReasonCode.MODEL_CALL_LIMIT_EXHAUSTED,
        ):
            with self.subTest(reason=reason):
                ledger = _MemoryLedger()
                guard, provider, _ = self._guard(ledger=ledger)
                with guard.request_scope(_scope(reason.value)):
                    ledger.denial = reason
                    with self.assertRaises(ModelAdapterError) as raised:
                        guard.generate(_draft_v1_request(), TimeoutPolicy())
                self.assertEqual(raised.exception.reason_code, reason)
                self.assertEqual(provider.calls, 0)

    def test_duplicate_trigger_is_denied_without_a_second_provider_call(self):
        guard, provider, _ledger = self._guard()
        scope = _scope("duplicate-trigger")
        with guard.request_scope(scope):
            guard.generate(_draft_v1_request(), TimeoutPolicy())
        with self.assertRaises(ModelAdapterError) as raised:
            with guard.request_scope(scope):
                guard.generate(_draft_v1_request(), TimeoutPolicy())
        self.assertEqual(
            raised.exception.reason_code,
            ModelReasonCode.MODEL_DUPLICATE_REQUEST,
        )
        self.assertEqual(provider.calls, 1)

    def test_per_session_window_and_per_request_call_bounds_are_server_side(self):
        window_guard, window_provider, _ledger = self._guard()
        for index in range(3):
            with window_guard.request_scope(_scope(f"window-{index}")):
                pass
        with self.assertRaises(ModelAdapterError) as rate_error:
            with window_guard.request_scope(_scope("window-denied")):
                pass
        self.assertEqual(
            rate_error.exception.reason_code,
            ModelReasonCode.MODEL_REQUEST_LIMIT_EXHAUSTED,
        )
        self.assertEqual(window_provider.calls, 0)

        limits = _limits(
            maximum_calls_total=2,
            maximum_calls_per_owner=2,
            maximum_calls_per_session=2,
            maximum_calls_per_request=1,
        )
        call_guard, call_provider, _ledger = self._guard(limits=limits)
        with call_guard.request_scope(_scope("one-call-only")):
            call_guard.generate(_draft_v1_request(), TimeoutPolicy())
            with self.assertRaises(ModelAdapterError) as call_error:
                call_guard.generate(_draft_v1_request(), TimeoutPolicy())
        self.assertEqual(
            call_error.exception.reason_code,
            ModelReasonCode.MODEL_CALL_LIMIT_EXHAUSTED,
        )
        self.assertEqual(call_provider.calls, 1)

    def test_unknown_completion_is_counted_and_never_refunded(self):
        provider = _Provider(
            [
                ModelAdapterError(
                    ModelReasonCode.MODEL_TIMEOUT,
                    retryable=True,
                    unknown_completion=True,
                )
            ]
        )
        guard, provider, ledger = self._guard(provider=provider)
        with self.assertRaises(ModelAdapterError) as raised:
            with guard.request_scope(_scope("timeout")):
                guard.generate(_draft_v1_request(), TimeoutPolicy())
        self.assertEqual(raised.exception.reason_code, ModelReasonCode.MODEL_TIMEOUT)
        self.assertEqual(provider.calls, 1)
        call_completion = ledger.completions[0]
        self.assertEqual(call_completion[0], "CALL")
        self.assertIsNone(call_completion[1])
        self.assertIsNone(call_completion[2])
        self.assertEqual(call_completion[3], ModelReasonCode.MODEL_TIMEOUT)

    def test_failure_releases_permit_for_next_request(self):
        provider = _Provider(
            [ModelAdapterError(ModelReasonCode.MODEL_AUTHENTICATION_FAILED)]
        )
        guard, provider, _ledger = self._guard(provider=provider)
        with self.assertRaises(ModelAdapterError):
            with guard.request_scope(_scope("auth-fail")):
                guard.generate(_draft_v1_request(), TimeoutPolicy())
        with guard.request_scope(_scope("after-fail")):
            guard.generate(_draft_v1_request(), TimeoutPolicy())
        self.assertEqual(provider.calls, 2)

    def test_provider_failure_classes_never_bypass_reservation(self):
        for index, reason in enumerate(
            (
                ModelReasonCode.MODEL_TRANSIENT_FAILURE,
                ModelReasonCode.MODEL_AUTHENTICATION_FAILED,
                ModelReasonCode.MODEL_RESPONSE_INVALID,
                ModelReasonCode.MODEL_RESPONSE_TOO_LARGE,
            )
        ):
            with self.subTest(reason=reason):
                provider = _Provider([ModelAdapterError(reason)])
                guard, provider, ledger = self._guard(provider=provider)
                with self.assertRaises(ModelAdapterError) as raised:
                    with guard.request_scope(_scope(f"failure-{index}")):
                        guard.generate(_draft_v1_request(), TimeoutPolicy())
                self.assertEqual(raised.exception.reason_code, reason)
                self.assertEqual(provider.calls, 1)
                self.assertEqual(ledger.calls, [ProviderCallPurpose.DRAFT_V1])

    def test_concurrency_denial_happens_before_reservation_or_provider(self):
        guard, provider, ledger = self._guard()
        guard._semaphore = _NeverPermit()  # explicit deterministic saturation
        with guard.request_scope(_scope("busy")):
            with self.assertRaises(ModelAdapterError) as raised:
                guard.generate(_draft_v1_request(), TimeoutPolicy())
        self.assertEqual(
            raised.exception.reason_code,
            ModelReasonCode.MODEL_CONCURRENCY_LIMIT,
        )
        self.assertEqual(provider.calls, 0)
        self.assertEqual(ledger.calls, [])

    def test_timeout_token_input_and_unknown_purpose_bounds_fail_before_call(self):
        guard, provider, _ledger = self._guard(limits=_limits(maximum_output_tokens=512))
        checks = (
            (_draft_v1_request(), TimeoutPolicy(attempt_timeout_seconds=46)),
            (_draft_v1_request(max_output_tokens=513), TimeoutPolicy()),
            (_text_request("unapproved-runtime-purpose"), TimeoutPolicy()),
        )
        for index, (request, timeout) in enumerate(checks):
            with self.subTest(index=index):
                with self.assertRaises(ModelAdapterError):
                    with guard.request_scope(_scope(f"bounded-{index}")):
                        guard.generate(request, timeout)
        self.assertEqual(provider.calls, 0)

    def test_silent_model_substitution_is_rejected(self):
        provider = _Provider(model_id="unapproved/model")
        guard, provider, _ledger = self._guard(provider=provider)
        with self.assertRaises(ModelAdapterError) as raised:
            with guard.request_scope(_scope("substitution")):
                guard.generate(_draft_v1_request(), TimeoutPolicy())
        self.assertEqual(
            raised.exception.reason_code,
            ModelReasonCode.MODEL_RESPONSE_INVALID,
        )
        self.assertEqual(provider.calls, 1)

    def test_historical_provider_identity_cannot_be_substituted(self):
        provider = _Provider()
        provider.provider_identity = (
            lambda: load_step22_moonshot_provider_spec().provider_identity()
        )
        with self.assertRaises(ModelAdapterError) as raised:
            GuardedProviderAdapter(provider, ledger=_MemoryLedger(), limits=_limits())
        self.assertEqual(
            raised.exception.reason_code,
            ModelReasonCode.MODEL_IDENTITY_MISMATCH,
        )

    def test_supported_text_purpose_is_typed_and_guarded(self):
        guard, provider, ledger = self._guard()
        with guard.request_scope(_scope("draft-v2")):
            guard.generate(
                _text_request(max_output_tokens=1024),
                TimeoutPolicy(),
            )
        self.assertEqual(ledger.calls, [ProviderCallPurpose.DRAFT_V2])
        self.assertEqual(provider.calls, 1)


class DurableLedgerTests(unittest.TestCase):
    def test_accounting_snapshot_is_bounded_hash_only_and_reconciled(self):
        transaction = _FakeTransaction()
        limits = _limits()
        ledger = CockroachProviderGuardLedger(
            _FakeRunner(transaction),
            provider_id="openrouter",
            budget_epoch="jury-budget-r5-1a",
            limits=limits,
        )
        scope = _scope("accounting")
        request = ledger.reserve_request(scope)
        call = ledger.reserve_call(
            scope,
            provider_request_hash="9" * 64,
            purpose=ProviderCallPurpose.DRAFT_V1,
            attempt_number=1,
        )
        ledger.complete(
            call,
            result_digest="8" * 64,
            usage_reference=(
                "provider-reported-tokens:prompt=10;completion=4;total=14;"
                "response_bytes=15"
            ),
            error_code=None,
            unknown_completion=False,
        )
        ledger.complete(
            request,
            result_digest="7" * 64,
            usage_reference=None,
            error_code=None,
            unknown_completion=False,
        )
        snapshot = ledger.snapshot(scope, budget_denied_calls=2)
        self.assertEqual(snapshot.accounting_semantics, "CALL-COUNT CEILING")
        self.assertEqual(snapshot.requests_reserved, 1)
        self.assertEqual(snapshot.calls_reserved, 1)
        self.assertEqual(snapshot.calls_completed, 1)
        self.assertEqual(snapshot.calls_failed, 0)
        self.assertEqual(snapshot.calls_unknown_completion, 0)
        self.assertEqual(snapshot.owner_calls_reserved, 1)
        self.assertEqual(snapshot.session_calls_reserved, 1)
        self.assertEqual(snapshot.calls_remaining, limits.maximum_calls_total - 1)
        self.assertEqual(snapshot.budget_denied_calls, 2)

    def test_existing_rls_operation_ledger_survives_restart_and_caps_calls(self):
        transaction = _FakeTransaction()
        runner = _FakeRunner(transaction)
        limits = _limits(
            maximum_calls_total=1,
            maximum_calls_per_owner=1,
            maximum_calls_per_session=1,
            maximum_calls_per_request=1,
        )
        ledger_one = CockroachProviderGuardLedger(
            runner,
            provider_id="openrouter",
            budget_epoch="jury-budget-r5-1a",
            limits=limits,
        )
        first = _scope("first")
        ledger_one.reserve_call(
            first,
            provider_request_hash="a" * 64,
            purpose=ProviderCallPurpose.DRAFT_V1,
            attempt_number=1,
        )
        self.assertEqual(
            {row["operation_kind"] for row in transaction.rows},
            {CALL_GLOBAL_KIND, CALL_OWNER_KIND},
        )

        ledger_after_restart = CockroachProviderGuardLedger(
            runner,
            provider_id="openrouter",
            budget_epoch="jury-budget-r5-1a",
            limits=limits,
        )
        with self.assertRaises(ProviderGuardLedgerError) as raised:
            ledger_after_restart.reserve_call(
                _scope("second"),
                provider_request_hash="b" * 64,
                purpose=ProviderCallPurpose.DRAFT_V1,
                attempt_number=1,
            )
        self.assertEqual(
            raised.exception.reason_code,
            ModelReasonCode.MODEL_BUDGET_EXHAUSTED,
        )

    def test_same_call_identity_is_duplicate_not_a_second_paid_reservation(self):
        transaction = _FakeTransaction()
        ledger = CockroachProviderGuardLedger(
            _FakeRunner(transaction),
            provider_id="openrouter",
            budget_epoch="jury-budget-r5-1a",
            limits=_limits(),
        )
        scope = _scope("duplicate")
        values = {
            "provider_request_hash": "c" * 64,
            "purpose": ProviderCallPurpose.DRAFT_V1,
            "attempt_number": 1,
        }
        ledger.reserve_call(scope, **values)
        with self.assertRaises(ProviderGuardLedgerError) as raised:
            ledger.reserve_call(scope, **values)
        self.assertEqual(
            raised.exception.reason_code,
            ModelReasonCode.MODEL_DUPLICATE_REQUEST,
        )
        self.assertEqual(len(transaction.rows), 2)

    def test_owner_and_session_paid_call_limits_are_durable_and_isolated(self):
        transaction = _FakeTransaction()
        ledger = CockroachProviderGuardLedger(
            _FakeRunner(transaction),
            provider_id="openrouter",
            budget_epoch="jury-budget-r5-1a",
            limits=_limits(
                maximum_calls_total=4,
                maximum_calls_per_owner=2,
                maximum_calls_per_session=1,
                maximum_calls_per_request=1,
            ),
        )
        owner_one = _scope("owner-one-first")
        ledger.reserve_call(
            owner_one,
            provider_request_hash="d" * 64,
            purpose=ProviderCallPurpose.DRAFT_V1,
            attempt_number=1,
        )
        with self.assertRaises(ProviderGuardLedgerError) as session_error:
            ledger.reserve_call(
                dataclasses.replace(owner_one, request_id="owner-one-second"),
                provider_request_hash="e" * 64,
                purpose=ProviderCallPurpose.DRAFT_V1,
                attempt_number=1,
            )
        self.assertEqual(
            session_error.exception.reason_code,
            ModelReasonCode.MODEL_CALL_LIMIT_EXHAUSTED,
        )

        second_session = dataclasses.replace(
            owner_one,
            session_id="session-r5-second",
            request_id="owner-one-third",
        )
        ledger.reserve_call(
            second_session,
            provider_request_hash="f" * 64,
            purpose=ProviderCallPurpose.DRAFT_V1,
            attempt_number=1,
        )
        with self.assertRaises(ProviderGuardLedgerError) as owner_error:
            ledger.reserve_call(
                dataclasses.replace(
                    second_session,
                    session_id="session-r5-third",
                    request_id="owner-one-fourth",
                ),
                provider_request_hash="1" * 64,
                purpose=ProviderCallPurpose.DRAFT_V1,
                attempt_number=1,
            )
        self.assertEqual(
            owner_error.exception.reason_code,
            ModelReasonCode.MODEL_CALL_LIMIT_EXHAUSTED,
        )

        owner_two = dataclasses.replace(
            owner_one,
            owner_user_id="owner-r5-two",
            session_id="session-r5-owner-two",
            request_id="owner-two-first",
        )
        ledger.reserve_call(
            owner_two,
            provider_request_hash="2" * 64,
            purpose=ProviderCallPurpose.DRAFT_V1,
            attempt_number=1,
        )
        self.assertEqual(
            sum(row["operation_kind"] == CALL_GLOBAL_KIND for row in transaction.rows),
            3,
        )

    def test_request_totals_are_durable_before_any_paid_call(self):
        transaction = _FakeTransaction()
        ledger = CockroachProviderGuardLedger(
            _FakeRunner(transaction),
            provider_id="openrouter",
            budget_epoch="jury-budget-r5-1a",
            limits=_limits(
                maximum_requests_total=2,
                maximum_requests_per_owner=1,
                maximum_requests_per_session=1,
                maximum_requests_per_window_global=2,
                maximum_requests_per_window_owner=1,
                maximum_requests_per_window_session=1,
            ),
        )
        ledger.reserve_request(_scope("durable-request-one"))
        with self.assertRaises(ProviderGuardLedgerError) as owner_error:
            ledger.reserve_request(
                dataclasses.replace(
                    _scope("durable-request-two"),
                    session_id="session-r5-second",
                )
            )
        self.assertEqual(
            owner_error.exception.reason_code,
            ModelReasonCode.MODEL_REQUEST_LIMIT_EXHAUSTED,
        )
        ledger.reserve_request(
            ProviderRequestScope(
                tenant_id="tenant-r5",
                owner_user_id="owner-r5-two",
                session_id="session-r5-owner-two",
                request_id="durable-request-owner-two",
            )
        )
        with self.assertRaises(ProviderGuardLedgerError) as global_error:
            ledger.reserve_request(
                ProviderRequestScope(
                    tenant_id="tenant-r5",
                    owner_user_id="owner-r5-three",
                    session_id="session-r5-owner-three",
                    request_id="durable-request-owner-three",
                )
            )
        self.assertEqual(
            global_error.exception.reason_code,
            ModelReasonCode.MODEL_REQUEST_LIMIT_EXHAUSTED,
        )

    def test_ledger_reuses_generic_table_without_migration_or_privileged_role(self):
        source = inspect.getsource(CockroachProviderGuardLedger)
        self.assertIn("memory_patch.persistence_operations", source)
        self.assertNotIn("DATABASE_URL_MIGRATOR", source)
        self.assertNotIn("mp_schema_owner", source)
        self.assertNotIn("BYPASSRLS", source)
        self.assertNotIn("DROP TABLE", source)

    def test_guard_has_no_database_commit_or_approval_capability_on_provider(self):
        source = inspect.getsource(GuardedProviderAdapter)
        self.assertNotIn("DATABASE_URL", source)
        self.assertNotIn("CommitHelper", source)
        guard = GuardedProviderAdapter(
            _Provider(), ledger=_MemoryLedger(), limits=_limits()
        )
        for capability in (
            "approve",
            "commit",
            "activate",
            "execute_sql",
            "connection_factory",
        ):
            self.assertFalse(hasattr(guard, capability))


class RuntimeProviderCompositionTests(unittest.TestCase):
    def test_service_factory_builds_without_network_or_paid_call(self):
        settings = RuntimeSettings.from_mapping(
            {
                RUNTIME_MODE_ENV: "TEST",
                "OPENROUTER_API_KEY": "synthetic-r5-provider-key",
                PROVIDER_BUDGET_EPOCH_ENV: "jury-budget-r5-1a",
                PROVIDER_TENANT_ID_ENV: "tenant-r5",
            }
        )
        runner = SerializableTransactionRunner(
            lambda: (_ for _ in ()).throw(AssertionError("DB must remain lazy")),
            credential_purpose=CredentialPurpose.APPLICATION_DATABASE,
        )
        factory = ProviderRuntimeServiceDependencyFactory()
        factory.validate_availability(settings)
        recorder = RuntimeStartupRecorder()
        for stage in (
            RuntimeStartupStage.CONFIG_VALIDATED,
            RuntimeStartupStage.DEPENDENCY_AVAILABILITY_VALIDATED,
            RuntimeStartupStage.DATABASE_RESOURCES_INITIALIZED,
            RuntimeStartupStage.SESSION_RESOURCES_INITIALIZED,
        ):
            recorder.advance(stage)
        dependencies = factory.initialize_after_sessions(
            settings,
            recorder,
            SimpleNamespace(transaction_runner=runner),
            SimpleNamespace(),
            SimpleNamespace(),
            lambda _principal: True,
        )
        self.assertEqual(
            recorder.events[-3:],
            (
                RuntimeStartupStage.SERVICE_COMPOSITION_INITIALIZED,
                RuntimeStartupStage.PROVIDER_ADAPTER_INITIALIZED,
                RuntimeStartupStage.RUNTIME_GUARDS_INITIALIZED,
            ),
        )
        self.assertTrue(dependencies.provider_adapter.durable_accounting)
        self.assertEqual(
            dependencies.provider_adapter.provider_identity().provider_id,
            "openrouter",
        )
        dependencies.provider_adapter.close()


if __name__ == "__main__":
    unittest.main()
