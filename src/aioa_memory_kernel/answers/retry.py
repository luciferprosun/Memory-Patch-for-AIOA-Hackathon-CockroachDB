"""Single bounded Step 26 correction retry through the Step 22 provider port."""

from __future__ import annotations

import hashlib
import json

from aioa_memory_kernel.contracts.exceptions import ContractValidationError, IntegrityError
from aioa_memory_kernel.contracts.serialization import canonical_json
from aioa_memory_kernel.corrections import (
    CorrectionPacketAuthenticator,
    canonical_packet_json,
    verify_packet_authenticity,
)
from aioa_memory_kernel.modeling import (
    AttemptPolicy,
    GenerationParameters,
    ModelAdapterError,
    ModelReasonCode,
    PromptTemplate,
    ProviderTextRequest,
    SystemUTCClock,
    TextGenerationProvider,
    TimeoutPolicy,
    TrustedClock,
    load_approved_provider_spec,
    verify_provider_response_hash,
)
from aioa_memory_kernel.persistence import assert_no_open_persistence_transaction
from aioa_memory_kernel.verification import (
    DraftV2,
    DraftV2GenerationResult,
    DraftV2LayeredVerifier,
    DraftV2PipelineResult,
    STEP25_SCHEMA_VERSION,
    derive_draft_v2_id,
)

from .assembler import derive_retry_failure_summary
from .models import (
    FINAL_RETRY_ATTEMPT_POLICY_ID,
    FINAL_RETRY_GENERATION_POLICY_ID,
    FINAL_RETRY_PROMPT_ID,
    FINAL_RETRY_PROMPT_VERSION,
    FINAL_RETRY_TIMEOUT_POLICY_ID,
    FinalAnswerRequest,
    FinalCorrectionRetryRequest,
    FinalOutputStatus,
    Step26BoundaryError,
    Step26ReasonCode,
    verify_final_retry_request_hash,
)
from .policy import evaluate_final_eligibility


FINAL_RETRY_PROVIDER_PURPOSE = "verified-answer-single-retry-1a"


def load_final_retry_prompt_template() -> PromptTemplate:
    return PromptTemplate(
        template_id=FINAL_RETRY_PROMPT_ID,
        template_version=FINAL_RETRY_PROMPT_VERSION,
        system_instruction=(
            "Repair the failed Draft V2 using only Draft V1, the same verified "
            "Correction Packet, and the bounded deterministic failure summary in "
            "the user message. Apply every remaining correction, omit prohibited "
            "claims, preserve uncertainty and conflicts, and use only packet "
            "citation IDs rendered as [citation:CITATION_ID]. Do not add evidence, "
            "browse, use tools, access databases, approve, execute, or self-certify. "
            "Return only the complete repaired Draft V2 text."
        ),
    )


def load_final_retry_generation_parameters() -> GenerationParameters:
    return GenerationParameters(
        temperature="0.2",
        top_p="0.9",
        max_output_tokens=1024,
        stop_sequences=(),
        streaming=False,
        seed=None,
        policy_id=FINAL_RETRY_GENERATION_POLICY_ID,
        policy_version="1",
    )


def load_final_retry_timeout_policy() -> TimeoutPolicy:
    return TimeoutPolicy(
        attempt_timeout_seconds=45,
        policy_id=FINAL_RETRY_TIMEOUT_POLICY_ID,
        policy_version="1",
    )


def load_final_retry_attempt_policy() -> AttemptPolicy:
    # This policy represents the one final correction attempt. Provider-level
    # retry is deliberately disabled so Step 26 cannot exceed its global bound.
    return AttemptPolicy(
        max_attempts=1,
        retry_delay_milliseconds=0,
        policy_id=FINAL_RETRY_ATTEMPT_POLICY_ID,
        policy_version="1",
    )


def prepare_final_retry_request(
    request: FinalAnswerRequest,
    authenticator: CorrectionPacketAuthenticator,
) -> FinalCorrectionRetryRequest:
    decision = evaluate_final_eligibility(request)
    if (
        decision.output_status is not FinalOutputStatus.RETRY_REQUIRED
        or not decision.retry_permitted
        or request.draft_v1 is None
        or request.correction_packet is None
        or request.integrity_receipt is None
        or request.step25_result is None
    ):
        raise Step26BoundaryError(Step26ReasonCode.DRAFT_V2_VERIFICATION_FAILED)
    try:
        verify_packet_authenticity(
            request.correction_packet,
            request.integrity_receipt,
            authenticator,
        )
    except (TypeError, ContractValidationError, IntegrityError, RuntimeError) as exc:
        raise Step26BoundaryError(Step26ReasonCode.INTEGRITY_FAILURE) from exc
    return FinalCorrectionRetryRequest(
        draft_v1=request.draft_v1,
        correction_packet=request.correction_packet,
        integrity_receipt=request.integrity_receipt,
        original_step25_result=request.step25_result,
        failure_summary=derive_retry_failure_summary(request.step25_result),
        provider_identity=load_approved_provider_spec().provider_identity(),
        prompt_template=load_final_retry_prompt_template(),
        generation_parameters=load_final_retry_generation_parameters(),
        timeout_policy=load_final_retry_timeout_policy(),
        attempt_policy=load_final_retry_attempt_policy(),
    )


def build_final_retry_provider_request(
    request: FinalCorrectionRetryRequest,
) -> ProviderTextRequest:
    verify_final_retry_request_hash(request)
    packet_document = json.loads(canonical_packet_json(request.correction_packet))
    user_content = canonical_json(
        {
            "bounded_failure_summary": request.failure_summary,
            "correction_packet": packet_document,
            "draft_v1": {
                "draft_v1_hash": request.draft_v1.draft_hash,
                "exact_text": request.draft_v1.draft_text,
            },
            "failed_draft_v2": {
                "draft_v2_hash": (
                    request.original_step25_result.draft_v2.draft_v2_hash
                ),
                "exact_text": request.original_step25_result.draft_v2.draft_text,
                "verification_summary_hash": (
                    request.original_step25_result.verification_summary.summary_hash
                ),
            },
            "instruction": "Return one fully repaired Draft V2 using no new evidence.",
        }
    )
    return ProviderTextRequest(
        provider_identity=request.provider_identity,
        purpose=FINAL_RETRY_PROVIDER_PURPOSE,
        prompt_template_id=request.prompt_template.template_id,
        prompt_template_digest=request.prompt_template.template_digest,
        system_instruction=request.prompt_template.system_instruction,
        user_content=user_content,
        user_content_digest=hashlib.sha256(user_content.encode("utf-8")).hexdigest(),
        generation_parameters=request.generation_parameters,
    )


def execute_final_retry(
    request: FinalCorrectionRetryRequest,
    provider: TextGenerationProvider,
    authenticator: CorrectionPacketAuthenticator,
    *,
    verifier: DraftV2LayeredVerifier | None = None,
    clock: TrustedClock | None = None,
) -> DraftV2PipelineResult:
    """Call the provider exactly once and fully re-verify the whole new draft."""

    verify_final_retry_request_hash(request)
    try:
        verify_packet_authenticity(
            request.correction_packet,
            request.integrity_receipt,
            authenticator,
        )
    except (TypeError, ContractValidationError, IntegrityError, RuntimeError) as exc:
        raise Step26BoundaryError(Step26ReasonCode.INTEGRITY_FAILURE) from exc
    approved = load_approved_provider_spec().provider_identity()
    if request.provider_identity != approved or provider.provider_identity() != approved:
        raise ModelAdapterError(ModelReasonCode.MODEL_IDENTITY_MISMATCH)
    provider_request = build_final_retry_provider_request(request)
    assert_no_open_persistence_transaction()
    response = provider.generate(provider_request, request.timeout_policy)
    try:
        verify_provider_response_hash(response)
    except (ContractValidationError, IntegrityError) as exc:
        raise ModelAdapterError(ModelReasonCode.MODEL_RESPONSE_INVALID) from exc
    if (
        response.provider_identity_digest != approved.identity_digest
        or response.model_id != approved.model_id
        or response.model_version != approved.model_revision_or_declared_version
    ):
        raise ModelAdapterError(ModelReasonCode.MODEL_IDENTITY_MISMATCH)

    generation_result = DraftV2GenerationResult(
        generation_request_hash=request.request_hash,
        provider_identity_digest=response.provider_identity_digest,
        attempt_count=1,
        failed_attempt_reason_codes=(),
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
        draft_v2_id=derive_draft_v2_id(request.request_hash),
        generation_request_hash=request.request_hash,
        request_id=request.draft_v1.request_id,
        tenant_id=request.draft_v1.tenant_id,
        user_id=request.draft_v1.user_id,
        route_hash=request.draft_v1.route_hash,
        original_query_digest=request.draft_v1.original_query_digest,
        draft_v1_hash=request.draft_v1.draft_hash,
        correction_packet_hash=request.correction_packet.packet_hash,
        packet_integrity_receipt_hash=request.integrity_receipt.receipt_hash,
        generation_result_hash=generation_result.result_hash,
        provider_identity_digest=approved.identity_digest,
        prompt_template_digest=request.prompt_template.template_digest,
        generation_parameters_digest=request.generation_parameters.parameters_digest,
        timeout_policy_digest=request.timeout_policy.policy_digest,
        attempt_policy_digest=request.attempt_policy.policy_digest,
        provider_request_id=response.provider_request_id,
        model_id=response.model_id,
        model_version=response.model_version,
        finish_reason=response.finish_reason,
        attempt_count=1,
        usage_metadata=response.usage_metadata,
        draft_text=response.response_content,
        created_at=(clock or SystemUTCClock()).now(),
    )
    if draft.draft_v2_hash == (
        request.original_step25_result.draft_v2.draft_v2_hash
    ):
        raise Step26BoundaryError(Step26ReasonCode.INTEGRITY_FAILURE)
    claims, verifications, summary = (verifier or DraftV2LayeredVerifier()).verify(
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
        persisted=False,
    )


__all__ = [
    "FINAL_RETRY_PROVIDER_PURPOSE",
    "build_final_retry_provider_request",
    "execute_final_retry",
    "load_final_retry_attempt_policy",
    "load_final_retry_generation_parameters",
    "load_final_retry_prompt_template",
    "load_final_retry_timeout_policy",
    "prepare_final_retry_request",
]
