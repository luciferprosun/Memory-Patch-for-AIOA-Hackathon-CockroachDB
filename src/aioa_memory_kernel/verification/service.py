"""Step 25 Draft V2 generation followed by layered verification."""

from __future__ import annotations

import time
from collections.abc import Callable

from aioa_memory_kernel.contracts.exceptions import ContractValidationError, IntegrityError
from aioa_memory_kernel.corrections import (
    CorrectionPacketAuthenticator,
    verify_packet_authenticity,
)
from aioa_memory_kernel.modeling import (
    ModelAdapterError,
    ModelReasonCode,
    ProviderResponse,
    SystemUTCClock,
    TextGenerationProvider,
    TrustedClock,
    load_approved_provider_spec,
    verify_provider_response_hash,
)
from aioa_memory_kernel.persistence import (
    ImmutableRecordConflictError,
    PersistenceError,
    assert_no_open_persistence_transaction,
)

from .layers import DraftV2LayeredVerifier
from .models import (
    DraftV2,
    DraftV2GenerationRequest,
    DraftV2GenerationResult,
    DraftV2PipelineResult,
    STEP25_SCHEMA_VERSION,
    Step25BoundaryError,
    Step25ReasonCode,
    derive_draft_v2_id,
    verify_draft_v2_generation_request_hash,
    verify_draft_v2_hash,
)
from .prompt import build_draft_v2_provider_request
from .protocols import DraftV2Store


def _validate_replay(draft: DraftV2, request: DraftV2GenerationRequest) -> None:
    try:
        verify_draft_v2_hash(draft)
    except (ContractValidationError, IntegrityError) as exc:
        raise Step25BoundaryError(Step25ReasonCode.DRAFT_V2_PERSISTENCE_CONFLICT) from exc
    if not (
        draft.draft_v2_id == derive_draft_v2_id(request.request_hash)
        and draft.generation_request_hash == request.request_hash
        and draft.request_id == request.request_id
        and draft.tenant_id == request.tenant_id
        and draft.user_id == request.user_id
        and draft.route_hash == request.route_hash
        and draft.original_query_digest == request.original_query_digest
        and draft.draft_v1_hash == request.draft_v1_hash
        and draft.correction_packet_hash == request.correction_packet_hash
        and draft.packet_integrity_receipt_hash == request.packet_integrity_receipt_hash
        and draft.provider_identity_digest == request.provider_identity.identity_digest
        and draft.prompt_template_digest == request.prompt_template.template_digest
        and draft.generation_parameters_digest
        == request.generation_parameters.parameters_digest
        and draft.timeout_policy_digest == request.timeout_policy.policy_digest
        and draft.attempt_policy_digest == request.attempt_policy.policy_digest
        and draft.model_id == request.provider_identity.model_id
        and draft.model_version
        == request.provider_identity.model_revision_or_declared_version
    ):
        raise Step25BoundaryError(Step25ReasonCode.DRAFT_V2_PERSISTENCE_CONFLICT)


class DraftV2Service:
    """Generate one corrected draft, persist briefly, then verify every claim."""

    def __init__(
        self,
        provider: TextGenerationProvider,
        authenticator: CorrectionPacketAuthenticator,
        *,
        verifier: DraftV2LayeredVerifier | None = None,
        store: DraftV2Store | None = None,
        clock: TrustedClock | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not callable(getattr(provider, "provider_identity", None)) or not callable(
            getattr(provider, "generate", None)
        ):
            raise TypeError("provider must implement TextGenerationProvider")
        if not callable(getattr(authenticator, "verify", None)):
            raise TypeError("authenticator must implement CorrectionPacketAuthenticator")
        if store is not None and (
            not callable(getattr(store, "load", None))
            or not callable(getattr(store, "put", None))
        ):
            raise TypeError("store must implement DraftV2Store")
        if clock is not None and not callable(getattr(clock, "now", None)):
            raise TypeError("clock must implement TrustedClock")
        if not callable(sleep):
            raise TypeError("sleep must be callable")
        self._provider = provider
        self._authenticator = authenticator
        self._verifier = verifier or DraftV2LayeredVerifier()
        self._store = store
        self._clock = clock or SystemUTCClock()
        self._sleep = sleep

    def generate_and_verify(
        self,
        request: DraftV2GenerationRequest,
    ) -> DraftV2PipelineResult:
        if not isinstance(request, DraftV2GenerationRequest):
            raise Step25BoundaryError(Step25ReasonCode.DRAFT_V2_INVALID)
        try:
            verify_draft_v2_generation_request_hash(request)
            verify_packet_authenticity(
                request.correction_packet,
                request.integrity_receipt,
                self._authenticator,
            )
        except (ContractValidationError, IntegrityError, RuntimeError) as exc:
            raise Step25BoundaryError(Step25ReasonCode.PACKET_HMAC_INVALID) from exc
        approved = load_approved_provider_spec().provider_identity()
        if request.provider_identity != approved or self._provider.provider_identity() != approved:
            raise ModelAdapterError(ModelReasonCode.MODEL_IDENTITY_MISMATCH)

        draft_id = derive_draft_v2_id(request.request_hash)
        if self._store is not None:
            try:
                existing = self._store.load(
                    tenant_id=request.tenant_id,
                    user_id=request.user_id,
                    draft_id=draft_id,
                )
            except PersistenceError as exc:
                raise Step25BoundaryError(
                    Step25ReasonCode.DRAFT_V2_PERSISTENCE_CONFLICT
                ) from exc
            if existing is not None:
                _validate_replay(existing, request)
                claims, verifications, summary = self._verifier.verify(
                    existing,
                    request.correction_packet,
                )
                return DraftV2PipelineResult(
                    draft_v2=existing,
                    generation_result=None,
                    ordered_claims=claims,
                    ordered_claim_verifications=verifications,
                    verification_summary=summary,
                    replayed=True,
                    persisted=True,
                )

        provider_request = build_draft_v2_provider_request(request)
        response: ProviderResponse | None = None
        failed: list[ModelReasonCode] = []
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

        generation_result = DraftV2GenerationResult(
            generation_request_hash=request.request_hash,
            provider_identity_digest=response.provider_identity_digest,
            attempt_count=attempt_count,
            failed_attempt_reason_codes=tuple(failed),
            provider_request_id=response.provider_request_id,
            model_id=response.model_id,
            model_version=response.model_version,
            finish_reason=response.finish_reason,
            usage_metadata=response.usage_metadata,
            response_text=response.response_content,
            response_text_sha256=response.response_content_sha256,
            response_byte_length=response.response_byte_length,
            latency_milliseconds=response.latency_milliseconds,
            provider_response_hash=response.response_hash,
        )
        draft = DraftV2(
            schema_version=STEP25_SCHEMA_VERSION,
            draft_v2_id=draft_id,
            generation_request_hash=request.request_hash,
            request_id=request.request_id,
            tenant_id=request.tenant_id,
            user_id=request.user_id,
            route_hash=request.route_hash,
            original_query_digest=request.original_query_digest,
            draft_v1_hash=request.draft_v1_hash,
            correction_packet_hash=request.correction_packet_hash,
            packet_integrity_receipt_hash=request.packet_integrity_receipt_hash,
            generation_result_hash=generation_result.result_hash,
            provider_identity_digest=request.provider_identity.identity_digest,
            prompt_template_digest=request.prompt_template.template_digest,
            generation_parameters_digest=request.generation_parameters.parameters_digest,
            timeout_policy_digest=request.timeout_policy.policy_digest,
            attempt_policy_digest=request.attempt_policy.policy_digest,
            provider_request_id=response.provider_request_id,
            model_id=response.model_id,
            model_version=response.model_version,
            finish_reason=response.finish_reason,
            attempt_count=attempt_count,
            usage_metadata=response.usage_metadata,
            draft_text=response.response_content,
            created_at=self._clock.now(),
        )
        persisted = False
        if self._store is not None:
            try:
                stored = self._store.put(draft)
            except (ImmutableRecordConflictError, PersistenceError) as exc:
                raise Step25BoundaryError(
                    Step25ReasonCode.DRAFT_V2_PERSISTENCE_CONFLICT
                ) from exc
            _validate_replay(stored, request)
            if stored.draft_v2_hash != draft.draft_v2_hash:
                raise Step25BoundaryError(
                    Step25ReasonCode.DRAFT_V2_PERSISTENCE_CONFLICT
                )
            draft = stored
            persisted = True

        claims, verifications, summary = self._verifier.verify(
            draft,
            request.correction_packet,
        )
        return DraftV2PipelineResult(
            draft_v2=draft,
            generation_result=generation_result,
            ordered_claims=claims,
            ordered_claim_verifications=verifications,
            verification_summary=summary,
            replayed=False,
            persisted=persisted,
        )


__all__ = ["DraftV2Service"]
