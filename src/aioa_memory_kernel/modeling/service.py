"""Bounded Step 22 orchestration for an evidence-blind Draft V1."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Callable

from aioa_memory_kernel.contracts.exceptions import ContractValidationError, IntegrityError
from aioa_memory_kernel.persistence import (
    ImmutableRecordConflictError,
    PersistenceError,
    assert_no_open_persistence_transaction,
)

from .models import (
    DraftGenerationReceipt,
    DraftV1,
    ModelAdapterError,
    ModelGenerationRequest,
    ModelGenerationResult,
    ModelReasonCode,
    ProviderResponse,
    STEP22_SCHEMA_VERSION,
    derive_draft_id,
    load_approved_provider_spec,
    verify_draft_v1_hash,
    verify_generation_request_hash,
    verify_provider_response_hash,
)
from .prompt import build_provider_call_request
from .protocols import DraftV1Provider, DraftV1Store, TrustedClock


class SystemUTCClock:
    """Internal wall clock; tests and controlled runs may inject a fixed clock."""

    def now(self) -> datetime:
        return datetime.now(UTC)


def _validate_replay(draft: DraftV1, request: ModelGenerationRequest) -> None:
    try:
        verify_draft_v1_hash(draft)
    except (ContractValidationError, IntegrityError) as exc:
        raise ModelAdapterError(ModelReasonCode.DRAFT_PERSISTENCE_CONFLICT) from exc
    expected = (
        draft.draft_id == derive_draft_id(request.request_hash)
        and draft.generation_request_hash == request.request_hash
        and draft.request_id == request.request_id
        and draft.tenant_id == request.tenant_id
        and draft.user_id == request.user_id
        and draft.original_query_digest == request.original_query_digest
        and draft.route_hash == request.route_hash
        and draft.step21_result_hash == request.step21_result_hash
        and draft.step21_evidence_status is request.step21_evidence_status
        and draft.provider_identity_digest == request.provider_identity.identity_digest
        and draft.prompt_template_digest == request.prompt_template.template_digest
        and draft.generation_parameters_digest
        == request.generation_parameters.parameters_digest
        and draft.timeout_policy_digest == request.timeout_policy.policy_digest
        and draft.attempt_policy_digest == request.attempt_policy.policy_digest
        and draft.model_id == request.provider_identity.model_id
        and draft.model_version
        == request.provider_identity.model_revision_or_declared_version
    )
    if not expected:
        raise ModelAdapterError(ModelReasonCode.DRAFT_PERSISTENCE_CONFLICT)


class DraftV1Service:
    """Calls one approved text provider outside DB transactions, then persists."""

    def __init__(
        self,
        provider: DraftV1Provider,
        *,
        store: DraftV1Store | None = None,
        clock: TrustedClock | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not callable(getattr(provider, "provider_identity", None)) or not callable(
            getattr(provider, "generate", None)
        ):
            raise TypeError("provider must implement DraftV1Provider")
        if store is not None and (
            not callable(getattr(store, "load", None))
            or not callable(getattr(store, "put", None))
        ):
            raise TypeError("store must implement DraftV1Store")
        if clock is not None and not callable(getattr(clock, "now", None)):
            raise TypeError("clock must implement TrustedClock")
        if not callable(sleep):
            raise TypeError("sleep must be callable")
        self._provider = provider
        self._store = store
        self._clock = clock or SystemUTCClock()
        self._sleep = sleep

    def generate(self, request: ModelGenerationRequest) -> DraftGenerationReceipt:
        if not isinstance(request, ModelGenerationRequest):
            raise ModelAdapterError(ModelReasonCode.MODEL_REQUEST_INVALID)
        try:
            verify_generation_request_hash(request)
        except (ContractValidationError, IntegrityError) as exc:
            raise ModelAdapterError(ModelReasonCode.MODEL_REQUEST_INVALID) from exc
        approved_identity = load_approved_provider_spec().provider_identity()
        if (
            request.provider_identity != approved_identity
            or self._provider.provider_identity() != approved_identity
        ):
            raise ModelAdapterError(ModelReasonCode.MODEL_IDENTITY_MISMATCH)

        draft_id = derive_draft_id(request.request_hash)
        if self._store is not None:
            try:
                existing = self._store.load(
                    tenant_id=request.tenant_id,
                    user_id=request.user_id,
                    draft_id=draft_id,
                )
            except PersistenceError as exc:
                raise ModelAdapterError(ModelReasonCode.DRAFT_PERSISTENCE_CONFLICT) from exc
            if existing is not None:
                _validate_replay(existing, request)
                return DraftGenerationReceipt(
                    draft=existing,
                    generation_result=None,
                    replayed=True,
                    persisted=True,
                    reason_codes=(ModelReasonCode.DRAFT_REPLAYED,),
                )

        provider_request = build_provider_call_request(request)
        failed: list[ModelReasonCode] = []
        response: ProviderResponse | None = None
        attempt_count = 0
        unknown_completion = False
        for attempt_count in range(1, request.attempt_policy.max_attempts + 1):
            assert_no_open_persistence_transaction()
            try:
                response = self._provider.generate(provider_request, request.timeout_policy)
            except ModelAdapterError as exc:
                unknown_completion = unknown_completion or exc.unknown_completion
                if not exc.retryable:
                    raise ModelAdapterError(
                        exc.reason_code,
                        retryable=False,
                        unknown_completion=unknown_completion,
                    ) from exc
                failed.append(exc.reason_code)
                if attempt_count >= request.attempt_policy.max_attempts:
                    raise ModelAdapterError(
                        ModelReasonCode.MODEL_RETRY_EXHAUSTED,
                        unknown_completion=unknown_completion,
                    ) from exc
                self._sleep(request.attempt_policy.retry_delay_milliseconds / 1000)
                continue
            break
        if response is None:
            raise ModelAdapterError(ModelReasonCode.MODEL_RETRY_EXHAUSTED)

        try:
            verify_provider_response_hash(response)
        except (ContractValidationError, IntegrityError) as exc:
            raise ModelAdapterError(ModelReasonCode.MODEL_RESPONSE_INVALID) from exc
        if (
            response.provider_identity_digest != request.provider_identity.identity_digest
            or response.model_id != request.provider_identity.model_id
            or response.model_version
            != request.provider_identity.model_revision_or_declared_version
        ):
            raise ModelAdapterError(ModelReasonCode.MODEL_IDENTITY_MISMATCH)

        generation_result = ModelGenerationResult(
            generation_request_hash=request.request_hash,
            provider_identity_digest=response.provider_identity_digest,
            attempt_count=attempt_count,
            failed_attempt_reason_codes=tuple(failed),
            provider_request_id=response.provider_request_id,
            model_id=response.model_id,
            model_version=response.model_version,
            finish_reason=response.finish_reason,
            usage_metadata=response.usage_metadata,
            response_content=response.response_content,
            response_content_sha256=response.response_content_sha256,
            response_byte_length=response.response_byte_length,
            latency_milliseconds=response.latency_milliseconds,
            provider_response_hash=response.response_hash,
        )
        draft = DraftV1(
            schema_version=STEP22_SCHEMA_VERSION,
            draft_id=draft_id,
            generation_request_hash=request.request_hash,
            request_id=request.request_id,
            tenant_id=request.tenant_id,
            user_id=request.user_id,
            original_query_digest=request.original_query_digest,
            route_hash=request.route_hash,
            provider_identity_digest=request.provider_identity.identity_digest,
            prompt_template_digest=request.prompt_template.template_digest,
            generation_parameters_digest=request.generation_parameters.parameters_digest,
            timeout_policy_digest=request.timeout_policy.policy_digest,
            attempt_policy_digest=request.attempt_policy.policy_digest,
            model_generation_result_hash=generation_result.result_hash,
            provider_request_id=response.provider_request_id,
            model_id=response.model_id,
            model_version=response.model_version,
            finish_reason=response.finish_reason,
            attempt_count=attempt_count,
            usage_metadata=response.usage_metadata,
            draft_text=response.response_content,
            step21_result_hash=request.step21_result_hash,
            step21_evidence_status=request.step21_evidence_status,
            created_at=self._clock.now(),
        )
        persisted = False
        if self._store is not None:
            try:
                stored = self._store.put(draft)
            except (ImmutableRecordConflictError, PersistenceError) as exc:
                raise ModelAdapterError(ModelReasonCode.DRAFT_PERSISTENCE_CONFLICT) from exc
            _validate_replay(stored, request)
            if stored.draft_hash != draft.draft_hash:
                raise ModelAdapterError(ModelReasonCode.DRAFT_PERSISTENCE_CONFLICT)
            draft = stored
            persisted = True

        receipt_reasons = (ModelReasonCode.MODEL_GENERATION_OK,)
        if persisted:
            receipt_reasons += (ModelReasonCode.DRAFT_PERSISTED,)
        return DraftGenerationReceipt(
            draft=draft,
            generation_result=generation_result,
            replayed=False,
            persisted=persisted,
            reason_codes=receipt_reasons,
        )


__all__ = ["DraftV1Service", "SystemUTCClock"]
