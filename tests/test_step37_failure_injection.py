"""Offline deterministic Step 37 failure-injection and recovery tests."""

from __future__ import annotations

import hashlib
import unittest
from unittest import mock

from tests._support import SOURCE_ROOT as _SOURCE_ROOT

from aioa_memory_kernel.answers import (
    FinalOutputStatus,
    Step26ReasonCode,
    VerifiedAnswerService,
)
from aioa_memory_kernel.ingestion import (
    IngestionExecutionError,
    SagaExecutionDisposition,
    SagaMilestone,
)
from aioa_memory_kernel.modeling import (
    DraftV1Service,
    ModelAdapterError,
    ModelReasonCode,
    load_approved_provider_spec,
)
from aioa_memory_kernel.persistence import PersistenceError
from aioa_memory_kernel.reliability import (
    FailureDirective,
    FailurePoint,
    InjectedFailure,
)
from aioa_memory_kernel.storage import (
    ExternalVolumeConflictError,
    ExternalVolumeIntegrityError,
    ExternalVolumeOperation,
    ExternalVolumeRuntimeAdapter,
    ExternalVolumeUnavailableError,
    SnapshotCapabilityError,
    SnapshotIntegrityError,
    SnapshotServiceUnavailableError,
)
from aioa_memory_kernel.storage import external_volume as external_volume_module
from aioa_memory_kernel.verification import DraftV2Service
from tests.failure_injection.harness import (
    ScriptedFailureInjector,
    retry_bounded,
    run_crash_after_durable_write_case,
)
from tests.test_external_volume_runtime import ExternalVolumeFixture
from tests.test_ingestion_saga import (
    CONTEXT,
    FakeStorage,
    MemoryControl,
    make_orchestrator,
    make_saga,
    make_snapshot as make_ingestion_snapshot,
)
from tests.test_s3_snapshot_storage import (
    VERSION_ID,
    FakeAwsError,
    FakeBody,
    FakeS3Client,
    make_adapter,
    make_snapshot as make_s3_snapshot,
    object_response,
    queue_capabilities,
)
from tests.test_step22_model_adapter_draft_v1 import (
    FixedClock as Step22FixedClock,
    generation_request,
    response_for as step22_response_for,
)
from tests.test_step25_draft_v2_layered_verifier import (
    authentication as step25_authentication,
    supported_inputs,
)
from tests.test_step26_verified_answer_output import hat_lineage


_PROVIDER_FAILURES = {
    FailurePoint.PROVIDER_TIMEOUT: (
        ModelReasonCode.MODEL_TIMEOUT,
        True,
    ),
    FailurePoint.PROVIDER_TRANSIENT_FAILURE: (
        ModelReasonCode.MODEL_TRANSIENT_FAILURE,
        True,
    ),
    FailurePoint.PROVIDER_RESPONSE_LOST: (
        ModelReasonCode.MODEL_TRANSIENT_FAILURE,
        True,
    ),
    FailurePoint.PROVIDER_AUTH_FAILURE: (
        ModelReasonCode.MODEL_AUTHENTICATION_FAILED,
        False,
    ),
    FailurePoint.PROVIDER_INVALID_RESPONSE: (
        ModelReasonCode.MODEL_RESPONSE_INVALID,
        False,
    ),
    FailurePoint.PROVIDER_OVERSIZED_RESPONSE: (
        ModelReasonCode.MODEL_RESPONSE_TOO_LARGE,
        False,
    ),
}


class FailureInjectedProvider:
    """Map closed Step 37 provider points to the existing typed adapter errors."""

    def __init__(
        self,
        identity: object,
        injector: ScriptedFailureInjector,
        points: tuple[FailurePoint, ...],
        *,
        subject_hash: str,
    ) -> None:
        self._identity = identity
        self._injector = injector
        self._points = points
        self._subject_hash = subject_hash
        self.requests: list[tuple[object, object]] = []

    def provider_identity(self) -> object:
        return self._identity

    def generate(self, request: object, timeout_policy: object) -> object:
        self.requests.append((request, timeout_policy))
        index = len(self.requests) - 1
        if index >= len(self._points):
            raise AssertionError("provider exceeded its bounded failure script")
        point = self._points[index]
        try:
            self._injector.hit(point, subject_hash=self._subject_hash)
        except InjectedFailure as injected:
            reason_code, retryable = _PROVIDER_FAILURES[point]
            raise ModelAdapterError(
                reason_code,
                retryable=retryable,
                unknown_completion=injected.completion_unknown,
            ) from None
        raise AssertionError("scripted provider failure was not emitted")


class TransientThenSuccessfulProvider:
    """Fail once through the closed injector, then return one exact response."""

    def __init__(
        self,
        request: object,
        injector: ScriptedFailureInjector,
        response: object,
    ) -> None:
        self._identity = request.provider_identity
        self._injector = injector
        self._response = response
        self._subject_hash = request.request_hash
        self.requests: list[tuple[object, object]] = []

    def provider_identity(self) -> object:
        return self._identity

    def generate(self, request: object, timeout_policy: object) -> object:
        self.requests.append((request, timeout_policy))
        if len(self.requests) == 1:
            try:
                self._injector.hit(
                    FailurePoint.PROVIDER_TRANSIENT_FAILURE,
                    subject_hash=self._subject_hash,
                )
            except InjectedFailure as injected:
                raise ModelAdapterError(
                    ModelReasonCode.MODEL_TRANSIENT_FAILURE,
                    retryable=True,
                    unknown_completion=injected.completion_unknown,
                ) from None
            raise AssertionError("the first scripted provider failure was not emitted")
        if len(self.requests) == 2:
            return self._response
        raise AssertionError("provider exceeded the two-attempt policy")


class SequenceProbe:
    """Return a bounded sequence of mount probe results."""

    def __init__(self, *outcomes: object) -> None:
        self._outcomes = list(outcomes)
        self.calls = 0

    def inspect(self, mountpoint: object) -> object:
        self.calls += 1
        if not self._outcomes:
            raise AssertionError("mount probe exceeded its bounded script")
        outcome = (
            self._outcomes.pop(0)
            if len(self._outcomes) > 1
            else self._outcomes[0]
        )
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class FailureHarnessTests(unittest.TestCase):
    def test_exact_occurrence_retry_is_bounded_and_deterministic(self) -> None:
        injector = ScriptedFailureInjector(
            (
                FailureDirective(
                    FailurePoint.PROVIDER_TRANSIENT_FAILURE,
                    (1,),
                    completion_unknown=True,
                ),
            )
        )
        subject_hash = hashlib.sha256(b"step37-harness").hexdigest()

        def operation() -> str:
            injector.hit(
                FailurePoint.PROVIDER_TRANSIENT_FAILURE,
                subject_hash=subject_hash,
            )
            return "RECOVERED"

        result, attempts = retry_bounded(
            operation,
            retryable=lambda error: isinstance(error, InjectedFailure),
            maximum_attempts=2,
        )
        self.assertEqual(result, "RECOVERED")
        self.assertEqual(attempts, 2)
        self.assertEqual(
            injector.hit_count(FailurePoint.PROVIDER_TRANSIENT_FAILURE),
            2,
        )
        injector.assert_fully_exercised()

    def test_disposable_process_crash_replays_one_durable_effect(self) -> None:
        effect, attempts = run_crash_after_durable_write_case()
        self.assertEqual(attempts, 2)
        self.assertEqual(
            effect.result_hash,
            hashlib.sha256(
                (
                    '{"idempotency_identity":"step37-process-crash-idempotency",'
                    '"request_hash":"'
                    + effect.request_hash
                    + '"}'
                ).encode("utf-8")
            ).hexdigest(),
        )


class ProviderRecoveryTests(unittest.TestCase):
    def _step22_provider(
        self,
        request: object,
        directives: tuple[FailureDirective, ...],
        points: tuple[FailurePoint, ...],
    ) -> tuple[FailureInjectedProvider, ScriptedFailureInjector]:
        injector = ScriptedFailureInjector(directives)
        provider = FailureInjectedProvider(
            request.provider_identity,
            injector,
            points,
            subject_hash=request.request_hash,
        )
        return provider, injector

    def test_step22_timeout_exhausts_after_two_calls_and_never_calls_third(
        self,
    ) -> None:
        request = generation_request()
        provider, injector = self._step22_provider(
            request,
            (
                FailureDirective(
                    FailurePoint.PROVIDER_TIMEOUT,
                    (1, 2),
                    completion_unknown=True,
                ),
            ),
            (FailurePoint.PROVIDER_TIMEOUT, FailurePoint.PROVIDER_TIMEOUT),
        )
        service = DraftV1Service(
            provider,
            clock=Step22FixedClock(),
            sleep=lambda _: None,
        )
        with self.assertRaises(ModelAdapterError) as captured:
            service.generate(request)
        self.assertIs(
            captured.exception.reason_code,
            ModelReasonCode.MODEL_RETRY_EXHAUSTED,
        )
        self.assertTrue(captured.exception.unknown_completion)
        self.assertEqual(len(provider.requests), 2)
        injector.assert_fully_exercised()

    def test_step22_transient_failure_then_success_is_bounded(self) -> None:
        request = generation_request()
        injector = ScriptedFailureInjector(
            (
                FailureDirective(
                    FailurePoint.PROVIDER_TRANSIENT_FAILURE,
                    (1,),
                ),
            )
        )
        provider = TransientThenSuccessfulProvider(
            request,
            injector,
            step22_response_for(request, text="Recovered exact response"),
        )
        receipt = DraftV1Service(
            provider,
            clock=Step22FixedClock(),
            sleep=lambda _: None,
        ).generate(request)
        self.assertEqual(receipt.draft.draft_text, "Recovered exact response")
        self.assertEqual(receipt.draft.attempt_count, 2)
        self.assertEqual(len(provider.requests), 2)
        self.assertEqual(
            receipt.generation_result.failed_attempt_reason_codes,
            (ModelReasonCode.MODEL_TRANSIENT_FAILURE,),
        )
        injector.assert_fully_exercised()

    def test_step22_invalid_response_fails_closed_without_retry(self) -> None:
        self._assert_nonretryable_provider_failure(
            FailurePoint.PROVIDER_INVALID_RESPONSE,
            ModelReasonCode.MODEL_RESPONSE_INVALID,
        )

    def test_step22_oversized_response_fails_closed_without_retry(self) -> None:
        self._assert_nonretryable_provider_failure(
            FailurePoint.PROVIDER_OVERSIZED_RESPONSE,
            ModelReasonCode.MODEL_RESPONSE_TOO_LARGE,
        )

    def test_step22_auth_failure_stops_after_one_call(self) -> None:
        self._assert_nonretryable_provider_failure(
            FailurePoint.PROVIDER_AUTH_FAILURE,
            ModelReasonCode.MODEL_AUTHENTICATION_FAILED,
        )

    def test_step22_nonretryable_provider_failures_stop_after_one_call(self) -> None:
        for point, expected_reason in (
            (
                FailurePoint.PROVIDER_AUTH_FAILURE,
                ModelReasonCode.MODEL_AUTHENTICATION_FAILED,
            ),
            (
                FailurePoint.PROVIDER_INVALID_RESPONSE,
                ModelReasonCode.MODEL_RESPONSE_INVALID,
            ),
            (
                FailurePoint.PROVIDER_OVERSIZED_RESPONSE,
                ModelReasonCode.MODEL_RESPONSE_TOO_LARGE,
            ),
        ):
            with self.subTest(point=point.value):
                self._assert_nonretryable_provider_failure(
                    point,
                    expected_reason,
                )

    def _assert_nonretryable_provider_failure(
        self,
        point: FailurePoint,
        expected_reason: ModelReasonCode,
    ) -> None:
        request = generation_request()
        provider, injector = self._step22_provider(
            request,
            (FailureDirective(point, (1,)),),
            (point,),
        )
        service = DraftV1Service(
            provider,
            clock=Step22FixedClock(),
            sleep=lambda _: None,
        )
        with self.assertRaises(ModelAdapterError) as captured:
            service.generate(request)
        self.assertIs(captured.exception.reason_code, expected_reason)
        self.assertEqual(len(provider.requests), 1)
        injector.assert_fully_exercised()

    def test_step22_exhaustion_preserves_earlier_unknown_completion(self) -> None:
        request = generation_request()
        provider, injector = self._step22_provider(
            request,
            (
                FailureDirective(
                    FailurePoint.PROVIDER_TIMEOUT,
                    (1,),
                    completion_unknown=True,
                ),
                FailureDirective(
                    FailurePoint.PROVIDER_TRANSIENT_FAILURE,
                    (1,),
                    completion_unknown=False,
                ),
            ),
            (
                FailurePoint.PROVIDER_TIMEOUT,
                FailurePoint.PROVIDER_TRANSIENT_FAILURE,
            ),
        )
        with self.assertRaises(ModelAdapterError) as captured:
            DraftV1Service(provider, sleep=lambda _: None).generate(request)
        self.assertIs(
            captured.exception.reason_code,
            ModelReasonCode.MODEL_RETRY_EXHAUSTED,
        )
        self.assertTrue(captured.exception.unknown_completion)
        self.assertEqual(len(provider.requests), 2)
        injector.assert_fully_exercised()

    def test_step22_terminal_auth_failure_preserves_prior_unknown_completion(
        self,
    ) -> None:
        request = generation_request()
        provider, injector = self._step22_provider(
            request,
            (
                FailureDirective(
                    FailurePoint.PROVIDER_TIMEOUT,
                    (1,),
                    completion_unknown=True,
                ),
                FailureDirective(
                    FailurePoint.PROVIDER_AUTH_FAILURE,
                    (1,),
                ),
            ),
            (
                FailurePoint.PROVIDER_TIMEOUT,
                FailurePoint.PROVIDER_AUTH_FAILURE,
            ),
        )
        with self.assertRaises(ModelAdapterError) as captured:
            DraftV1Service(provider, sleep=lambda _: None).generate(request)
        self.assertIs(
            captured.exception.reason_code,
            ModelReasonCode.MODEL_AUTHENTICATION_FAILED,
        )
        self.assertTrue(captured.exception.unknown_completion)
        self.assertEqual(len(provider.requests), 2)
        injector.assert_fully_exercised()

    def test_step25_exhaustion_preserves_earlier_unknown_completion(self) -> None:
        draft, packet = supported_inputs()
        authenticator, _, request = step25_authentication(draft, packet)
        injector = ScriptedFailureInjector(
            (
                FailureDirective(
                    FailurePoint.PROVIDER_RESPONSE_LOST,
                    (1,),
                    completion_unknown=True,
                ),
                FailureDirective(
                    FailurePoint.PROVIDER_TRANSIENT_FAILURE,
                    (1,),
                    completion_unknown=False,
                ),
            )
        )
        provider = FailureInjectedProvider(
            request.provider_identity,
            injector,
            (
                FailurePoint.PROVIDER_RESPONSE_LOST,
                FailurePoint.PROVIDER_TRANSIENT_FAILURE,
            ),
            subject_hash=request.request_hash,
        )
        with self.assertRaises(ModelAdapterError) as captured:
            DraftV2Service(
                provider,
                authenticator,
                sleep=lambda _: None,
            ).generate_and_verify(request)
        self.assertIs(
            captured.exception.reason_code,
            ModelReasonCode.MODEL_RETRY_EXHAUSTED,
        )
        self.assertTrue(captured.exception.unknown_completion)
        self.assertEqual(len(provider.requests), 2)
        injector.assert_fully_exercised()

    def test_step25_terminal_auth_failure_preserves_prior_unknown_completion(
        self,
    ) -> None:
        draft, packet = supported_inputs()
        authenticator, _, request = step25_authentication(draft, packet)
        injector = ScriptedFailureInjector(
            (
                FailureDirective(
                    FailurePoint.PROVIDER_RESPONSE_LOST,
                    (1,),
                    completion_unknown=True,
                ),
                FailureDirective(
                    FailurePoint.PROVIDER_AUTH_FAILURE,
                    (1,),
                ),
            )
        )
        provider = FailureInjectedProvider(
            request.provider_identity,
            injector,
            (
                FailurePoint.PROVIDER_RESPONSE_LOST,
                FailurePoint.PROVIDER_AUTH_FAILURE,
            ),
            subject_hash=request.request_hash,
        )
        with self.assertRaises(ModelAdapterError) as captured:
            DraftV2Service(
                provider,
                authenticator,
                sleep=lambda _: None,
            ).generate_and_verify(request)
        self.assertIs(
            captured.exception.reason_code,
            ModelReasonCode.MODEL_AUTHENTICATION_FAILED,
        )
        self.assertTrue(captured.exception.unknown_completion)
        self.assertEqual(len(provider.requests), 2)
        injector.assert_fully_exercised()

    def test_step26_provider_failure_is_one_call_and_never_draft_v1_fallback(
        self,
    ) -> None:
        request, authenticator = hat_lineage(
            draft_v2_text="Eine unbelegte Aussage."
        )
        injector = ScriptedFailureInjector(
            (
                FailureDirective(
                    FailurePoint.PROVIDER_TIMEOUT,
                    (1,),
                    completion_unknown=True,
                ),
            )
        )
        provider = FailureInjectedProvider(
            load_approved_provider_spec().provider_identity(),
            injector,
            (FailurePoint.PROVIDER_TIMEOUT,),
            subject_hash=request.request_hash,
        )
        outcome = VerifiedAnswerService(
            authenticator,
            provider=provider,
        ).finalize(request)
        self.assertEqual(len(provider.requests), 1)
        self.assertIs(
            outcome.output_status,
            FinalOutputStatus.HUMAN_REVIEW_REQUIRED,
        )
        self.assertIsNone(outcome.verified_answer)
        self.assertIn(
            Step26ReasonCode.HAT_ENFORCE_DRAFT_V1_FALLBACK_FORBIDDEN,
            outcome.human_review.reason_codes,
        )
        injector.assert_fully_exercised()


class S3RecoveryTests(unittest.TestCase):
    def test_prewrite_service_failure_fails_closed(self) -> None:
        client = FakeS3Client()
        snapshot = make_s3_snapshot()
        queue_capabilities(client)
        client.queue("put_object", FakeAwsError("RequestTimeout"))
        with self.assertRaises(SnapshotServiceUnavailableError):
            make_adapter(client).persist_snapshot(snapshot)
        self.assertEqual(
            sum(operation == "put_object" for operation, _ in client.calls),
            1,
        )

    def test_ack_lost_is_reconciled_without_a_second_put(self) -> None:
        client = FakeS3Client()
        snapshot = make_s3_snapshot()
        queue_capabilities(client)
        client.queue("put_object", FakeAwsError("RequestTimeout"))
        client.queue("head_object", object_response(snapshot))
        client.queue(
            "get_object",
            object_response(snapshot, payload=snapshot.canonical_payload),
        )
        adapter = make_adapter(client)
        with self.assertRaises(SnapshotServiceUnavailableError):
            adapter.persist_snapshot(snapshot)
        evidence = adapter.reconcile_snapshot(snapshot)
        self.assertIsNotNone(evidence)
        self.assertEqual(evidence.version_id, VERSION_ID)
        self.assertTrue(evidence.content_verified)
        self.assertEqual(
            sum(operation == "put_object" for operation, _ in client.calls),
            1,
        )

    def test_checksum_mismatch_fails_closed_without_head_verification(self) -> None:
        snapshot = make_s3_snapshot()
        checksum_client = FakeS3Client()
        queue_capabilities(checksum_client)
        checksum_client.queue(
            "put_object",
            {"VersionId": VERSION_ID, "ChecksumSHA256": "invalid-checksum"},
        )
        with self.assertRaises(SnapshotIntegrityError) as captured:
            make_adapter(checksum_client).persist_snapshot(snapshot)
        self.assertEqual(captured.exception.sanitized_code, "S3_CHECKSUM_MISMATCH")
        self.assertEqual(
            sum(operation == "put_object" for operation, _ in checksum_client.calls),
            1,
        )
        self.assertFalse(
            any(operation == "head_object" for operation, _ in checksum_client.calls)
        )

    def test_object_lock_disabled_never_attempts_put(self) -> None:
        snapshot = make_s3_snapshot()
        lock_client = FakeS3Client()
        queue_capabilities(lock_client, lock_enabled="Disabled")
        with self.assertRaises(SnapshotCapabilityError):
            make_adapter(lock_client).persist_snapshot(snapshot)
        self.assertFalse(
            any(operation == "put_object" for operation, _ in lock_client.calls)
        )

    def test_checksum_and_object_lock_failures_never_report_success(self) -> None:
        self.test_checksum_mismatch_fails_closed_without_head_verification()
        self.test_object_lock_disabled_never_attempts_put()

    def test_content_length_failure_still_closes_streaming_body(self) -> None:
        client = FakeS3Client()
        snapshot = make_s3_snapshot()
        body = FakeBody(snapshot.canonical_payload)
        response = object_response(snapshot)
        response["Body"] = body
        response["ContentLength"] = snapshot.content_length + 1
        client.queue("get_object", response)
        with self.assertRaises(SnapshotIntegrityError) as captured:
            make_adapter(client).retrieve_snapshot(
                snapshot,
                version_id=VERSION_ID,
            )
        self.assertEqual(
            captured.exception.sanitized_code,
            "S3_CONTENT_LENGTH_MISMATCH",
        )
        self.assertTrue(body.closed)
        self.assertIsNone(body.read_amount)

    def test_get_object_body_read_failure_fails_closed_and_closes_stream(
        self,
    ) -> None:
        client = FakeS3Client()
        snapshot = make_s3_snapshot()
        body = FakeBody(
            snapshot.canonical_payload,
            read_error=FakeAwsError("RequestTimeout"),
        )
        response = object_response(snapshot)
        response["Body"] = body
        client.queue("get_object", response)
        with self.assertRaises(SnapshotServiceUnavailableError) as captured:
            make_adapter(client).retrieve_snapshot(
                snapshot,
                version_id=VERSION_ID,
            )
        self.assertEqual(captured.exception.sanitized_code, "S3_UNAVAILABLE")
        self.assertEqual(captured.exception.operation, "GetObjectBodyRead")
        self.assertTrue(body.closed)
        self.assertEqual(body.read_amount, snapshot.content_length + 1)
        self.assertEqual(
            [operation for operation, _parameters in client.calls],
            ["get_object"],
        )


class ExternalVolumeRecoveryTests(ExternalVolumeFixture):
    def test_missing_volume_then_rebuilds_exactly_without_system_fallback(
        self,
    ) -> None:
        probe = SequenceProbe(
            ExternalVolumeUnavailableError(
                "synthetic missing volume",
                sanitized_code="VOLUME_MISSING",
            ),
            self.identity,
        )
        adapter = ExternalVolumeRuntimeAdapter(self.config, probe)
        payload = b"step37-rebuilt-derived-evidence\n"
        digest = hashlib.sha256(payload).hexdigest()

        evidence, attempts = retry_bounded(
            lambda: adapter.atomic_write_exact(
                ExternalVolumeOperation.VALIDATION_EVIDENCE,
                "step37-rebuild.bin",
                payload,
                expected_sha256=digest,
                expected_length=len(payload),
            ),
            retryable=lambda error: isinstance(
                error,
                ExternalVolumeUnavailableError,
            ),
            maximum_attempts=2,
        )
        self.assertEqual(attempts, 2)
        self.assertTrue(evidence.exact_read_back)
        self.assertFalse(evidence.system_drive_fallback_allowed)
        self.assertFalse(adapter.system_drive_fallback_allowed)
        self.assertEqual(
            adapter.read_exact(
                ExternalVolumeOperation.VALIDATION_EVIDENCE,
                "step37-rebuild.bin",
                expected_sha256=digest,
                expected_length=len(payload),
            ),
            payload,
        )

    def test_missing_required_volume_has_no_system_drive_fallback(self) -> None:
        adapter = ExternalVolumeRuntimeAdapter(
            self.config,
            SequenceProbe(
                ExternalVolumeUnavailableError(
                    "synthetic missing volume",
                    sanitized_code="VOLUME_MISSING",
                )
            ),
        )
        with self.assertRaises(ExternalVolumeUnavailableError) as captured:
            adapter.resolve_path(
                ExternalVolumeOperation.INGESTION_STAGING,
                "step37.bin",
            )
        self.assertEqual(captured.exception.operation, "INGESTION_STAGING")
        self.assertFalse(captured.exception.system_drive_fallback_allowed)
        self.assertFalse(adapter.system_drive_fallback_allowed)

    def test_atomic_write_failure_leaves_no_target_or_staging_artifact(self) -> None:
        payload = b"step37-volume-write-failure\n"
        digest = hashlib.sha256(payload).hexdigest()
        with mock.patch.object(
            external_volume_module.os,
            "write",
            side_effect=OSError("synthetic write failure"),
        ):
            with self.assertRaises(ExternalVolumeUnavailableError) as captured:
                self.adapter.atomic_write_exact(
                    ExternalVolumeOperation.VALIDATION_EVIDENCE,
                    "step37-write-failure.bin",
                    payload,
                    expected_sha256=digest,
                    expected_length=len(payload),
                )
        self.assertEqual(
            captured.exception.sanitized_code,
            "EXTERNAL_ATOMIC_WRITE_FAILED",
        )
        self.assertFalse(
            (
                self.data_root
                / "reports"
                / "step37-write-failure.bin"
            ).exists()
        )
        self.assertEqual(
            self.adapter.incomplete_atomic_artifacts(
                ExternalVolumeOperation.VALIDATION_EVIDENCE,
                "step37-write-failure.bin",
            ),
            (),
        )

    def test_atomic_rename_failure_preserves_staging_and_blocks_retry(self) -> None:
        relative_path = "step37-rename-failure.bin"
        staging_key = hashlib.sha256(
            f"reports/{relative_path}".encode("utf-8")
        ).hexdigest()[:16]
        staging = (
            self.data_root
            / "reports"
            / f".aioa-step8-atomic-{staging_key}-4242-rename.tmp"
        )
        incomplete = b"target-bound-incomplete-staging"
        staging.write_bytes(incomplete)
        artifacts = self.adapter.incomplete_atomic_artifacts(
            ExternalVolumeOperation.VALIDATION_EVIDENCE,
            relative_path,
        )
        self.assertEqual(artifacts, (f"reports/{staging.name}",))

        payload = b"step37-volume-retry-after-rename\n"
        with self.assertRaises(ExternalVolumeConflictError) as captured:
            self.adapter.atomic_write_exact(
                ExternalVolumeOperation.VALIDATION_EVIDENCE,
                relative_path,
                payload,
                expected_sha256=hashlib.sha256(payload).hexdigest(),
                expected_length=len(payload),
            )
        self.assertEqual(
            captured.exception.sanitized_code,
            "EXTERNAL_STAGING_ARTIFACT_EXISTS",
        )
        self.assertEqual(staging.read_bytes(), incomplete)
        self.assertEqual(
            self.adapter.incomplete_atomic_artifacts(
                ExternalVolumeOperation.VALIDATION_EVIDENCE,
                relative_path,
            ),
            artifacts,
        )
        self.assertFalse((self.data_root / "reports" / relative_path).exists())

    def test_corrupted_derived_cache_is_discarded_and_rebuilt_exactly(self) -> None:
        relative_path = "step37-cache.bin"
        target = self.data_root / "embeddings" / relative_path
        target.write_bytes(b"corrupted-derived-cache")
        rebuilt = b"recomputed-derived-cache\n"
        rebuilt_digest = hashlib.sha256(rebuilt).hexdigest()

        with self.assertRaises(ExternalVolumeIntegrityError) as captured:
            self.adapter.read_exact(
                ExternalVolumeOperation.EMBEDDING_CACHE,
                relative_path,
                expected_sha256=rebuilt_digest,
                expected_length=len(rebuilt),
            )
        self.assertEqual(
            captured.exception.sanitized_code,
            "EXTERNAL_READBACK_MISMATCH",
        )

        target.unlink()
        evidence = self.adapter.atomic_write_exact(
            ExternalVolumeOperation.EMBEDDING_CACHE,
            relative_path,
            rebuilt,
            expected_sha256=rebuilt_digest,
            expected_length=len(rebuilt),
        )
        self.assertTrue(evidence.exact_read_back)
        self.assertFalse(evidence.system_drive_fallback_allowed)
        self.assertEqual(
            self.adapter.read_exact(
                ExternalVolumeOperation.EMBEDDING_CACHE,
                relative_path,
                expected_sha256=rebuilt_digest,
                expected_length=len(rebuilt),
            ),
            rebuilt,
        )


class AckLostStorage(FakeStorage):
    """Persist exact S3 evidence, then lose only the acknowledgement once."""

    def __init__(self, snapshot: object) -> None:
        super().__init__(snapshot)
        self._ack_lost = False

    def persist_snapshot(self, snapshot: object) -> object:
        self.puts += 1
        self.evidence = self._evidence(content_verified=False)
        if not self._ack_lost:
            self._ack_lost = True
            raise SnapshotServiceUnavailableError(
                sanitized_code="S3_ACK_LOST"
            )
        return self.evidence


class FailOnceTransitionControl(MemoryControl):
    """Fail before or after one durable saga milestone transition."""

    def __init__(
        self,
        target_milestone: SagaMilestone,
        *,
        after_persist: bool,
    ) -> None:
        super().__init__()
        self._target_milestone = target_milestone
        self._after_persist = after_persist
        self.failure_emitted = False

    def transition(
        self,
        context: object,
        *,
        target_milestone: SagaMilestone,
        **values: object,
    ) -> object:
        should_fail = (
            target_milestone is self._target_milestone
            and not self.failure_emitted
        )
        if should_fail and not self._after_persist:
            self.failure_emitted = True
            raise PersistenceError(sqlstate="40001")
        result = super().transition(
            context,
            target_milestone=target_milestone,
            **values,
        )
        if should_fail:
            self.failure_emitted = True
            raise SimulatedProcessCrash
        return result


class SimulatedProcessCrash(BaseException):
    """Escape application error handling after a durable test checkpoint."""

    sanitized_code = "SIMULATED_PROCESS_CRASH_AFTER_DURABLE_CHECKPOINT"


class IngestionRecoveryTests(unittest.TestCase):
    @staticmethod
    def _restart_orchestrator(
        snapshot: object,
        boundaries: tuple[object, ...],
    ) -> object:
        restarted, _ = make_orchestrator(
            snapshot=snapshot,
            control=boundaries[0],
            volume=boundaries[1],
            acquisition=boundaries[2],
            storage=boundaries[3],
            parser=boundaries[4],
            validator=boundaries[5],
            publication=boundaries[6],
        )
        return restarted

    def test_ack_lost_resume_reconciles_one_snapshot_and_one_publication(
        self,
    ) -> None:
        snapshot = make_ingestion_snapshot()
        saga = make_saga(snapshot=snapshot)
        storage = AckLostStorage(snapshot)
        orchestrator, boundaries = make_orchestrator(
            snapshot=snapshot,
            storage=storage,
        )
        result = orchestrator.execute(CONTEXT, saga, snapshot)
        publication = boundaries[-1]
        self.assertIs(result.current_milestone, SagaMilestone.PUBLISHED)
        self.assertEqual(storage.puts, 1)
        self.assertEqual(publication.calls, 1)
        replay = orchestrator.execute(CONTEXT, saga, snapshot)
        self.assertEqual(replay, result)
        self.assertEqual(storage.puts, 1)
        self.assertEqual(publication.calls, 1)

    def test_s3_outage_is_bounded_then_enters_retry_wait(self) -> None:
        snapshot = make_ingestion_snapshot()
        saga = make_saga(snapshot=snapshot)
        storage = FakeStorage(snapshot)
        storage.transient_failures = 3
        orchestrator, boundaries = make_orchestrator(
            snapshot=snapshot,
            storage=storage,
        )
        control = boundaries[0]
        with self.assertRaises(IngestionExecutionError) as captured:
            orchestrator.execute(CONTEXT, saga, snapshot)
        self.assertTrue(captured.exception.decision.retryable)
        self.assertEqual(storage.puts, 3)
        self.assertIs(
            control.saga.execution_disposition,
            SagaExecutionDisposition.RETRY_WAIT,
        )
        self.assertEqual(
            control.failures,
            [(SagaExecutionDisposition.RETRY_WAIT, "S3_UNAVAILABLE")],
        )

    def test_checkpoint_failure_resumes_from_receipt_without_duplicate_parse(
        self,
    ) -> None:
        snapshot = make_ingestion_snapshot()
        saga = make_saga(snapshot=snapshot)
        control = FailOnceTransitionControl(
            SagaMilestone.PARSED,
            after_persist=False,
        )
        orchestrator, boundaries = make_orchestrator(
            snapshot=snapshot,
            control=control,
        )
        parser = boundaries[4]
        with self.assertRaises(IngestionExecutionError) as captured:
            orchestrator.execute(CONTEXT, saga, snapshot)
        self.assertTrue(captured.exception.decision.retryable)
        self.assertIs(
            control.saga.current_milestone,
            SagaMilestone.SNAPSHOT_LOCK_VERIFIED,
        )
        self.assertEqual(parser.calls, 1)

        result = self._restart_orchestrator(
            snapshot,
            boundaries,
        ).execute(CONTEXT, saga, snapshot)
        self.assertIs(result.current_milestone, SagaMilestone.PUBLISHED)
        self.assertEqual(parser.calls, 1)
        self.assertEqual(boundaries[6].calls, 1)

    def test_new_process_adapter_resumes_after_persisted_checkpoint_ack_loss(
        self,
    ) -> None:
        snapshot = make_ingestion_snapshot()
        saga = make_saga(snapshot=snapshot)
        control = FailOnceTransitionControl(
            SagaMilestone.VALIDATED,
            after_persist=True,
        )
        orchestrator, boundaries = make_orchestrator(
            snapshot=snapshot,
            control=control,
        )
        validator = boundaries[5]
        with self.assertRaises(SimulatedProcessCrash):
            orchestrator.execute(CONTEXT, saga, snapshot)
        self.assertIs(control.saga.current_milestone, SagaMilestone.VALIDATED)
        self.assertEqual(validator.calls, 1)

        result = self._restart_orchestrator(
            snapshot,
            boundaries,
        ).execute(CONTEXT, saga, snapshot)
        self.assertIs(result.current_milestone, SagaMilestone.PUBLISHED)
        self.assertEqual(validator.calls, 1)
        self.assertEqual(boundaries[6].calls, 1)

    def test_finalize_ack_loss_restarts_from_one_durable_publication(self) -> None:
        snapshot = make_ingestion_snapshot()
        saga = make_saga(snapshot=snapshot)
        control = FailOnceTransitionControl(
            SagaMilestone.PUBLISHED,
            after_persist=True,
        )
        orchestrator, boundaries = make_orchestrator(
            snapshot=snapshot,
            control=control,
        )
        publication = boundaries[6]
        with self.assertRaises(SimulatedProcessCrash):
            orchestrator.execute(CONTEXT, saga, snapshot)
        self.assertIs(control.saga.current_milestone, SagaMilestone.PUBLISHED)
        self.assertEqual(publication.calls, 1)

        result = self._restart_orchestrator(
            snapshot,
            boundaries,
        ).execute(CONTEXT, saga, snapshot)
        self.assertIs(result.current_milestone, SagaMilestone.PUBLISHED)
        self.assertEqual(publication.calls, 1)


if __name__ == "__main__":
    unittest.main()
