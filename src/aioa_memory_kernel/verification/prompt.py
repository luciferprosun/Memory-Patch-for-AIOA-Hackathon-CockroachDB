"""Step 25 prompt construction after the Correction Packet integrity gate."""

from __future__ import annotations

import hashlib
import json

from aioa_memory_kernel.contracts.serialization import canonical_json
from aioa_memory_kernel.corrections import (
    CorrectionPacketAuthenticator,
    CorrectionPacketIntegrityReceipt,
    CorrectionPacketV1A,
    canonical_packet_json,
    verify_packet_authenticity,
)
from aioa_memory_kernel.modeling import (
    AttemptPolicy,
    DraftV1,
    GenerationParameters,
    PromptTemplate,
    ProviderTextRequest,
    TimeoutPolicy,
    load_approved_provider_spec,
)

from .models import (
    DRAFT_V2_ATTEMPT_POLICY_ID,
    DRAFT_V2_GENERATION_POLICY_ID,
    DRAFT_V2_PROMPT_TEMPLATE_ID,
    DRAFT_V2_PROMPT_TEMPLATE_VERSION,
    DRAFT_V2_TIMEOUT_POLICY_ID,
    DraftV2GenerationRequest,
    Step25BoundaryError,
    Step25ReasonCode,
)


DRAFT_V2_GENERATION_POLICY_VERSION = "1"
DRAFT_V2_TIMEOUT_POLICY_VERSION = "1"
DRAFT_V2_ATTEMPT_POLICY_VERSION = "1"
DRAFT_V2_PROVIDER_PURPOSE = "draft-v2-generation-1a"


def load_draft_v2_prompt_template() -> PromptTemplate:
    """Return the one fixed, provider-neutral Draft V2 instruction."""

    return PromptTemplate(
        template_id=DRAFT_V2_PROMPT_TEMPLATE_ID,
        template_version=DRAFT_V2_PROMPT_TEMPLATE_VERSION,
        system_instruction=(
            "Revise Draft V1 using only the verified Correction Packet supplied in "
            "the user message. Preserve supported claims when relevant, apply every "
            "required correction, do not repeat prohibited claims, preserve stated "
            "conflicts and uncertainty, respect temporal and source-authority limits, "
            "and use only packet citation IDs rendered as [citation:CITATION_ID]. "
            "Do not invent evidence, tools, browsing, database access, approval, or "
            "external action. Return only the revised inert Draft V2 text."
        ),
    )


def load_draft_v2_generation_parameters() -> GenerationParameters:
    return GenerationParameters(
        temperature="0.2",
        top_p="0.9",
        max_output_tokens=1024,
        stop_sequences=(),
        streaming=False,
        seed=None,
        policy_id=DRAFT_V2_GENERATION_POLICY_ID,
        policy_version=DRAFT_V2_GENERATION_POLICY_VERSION,
    )


def load_draft_v2_timeout_policy() -> TimeoutPolicy:
    return TimeoutPolicy(
        attempt_timeout_seconds=45,
        policy_id=DRAFT_V2_TIMEOUT_POLICY_ID,
        policy_version=DRAFT_V2_TIMEOUT_POLICY_VERSION,
    )


def load_draft_v2_attempt_policy() -> AttemptPolicy:
    return AttemptPolicy(
        max_attempts=2,
        retry_delay_milliseconds=250,
        policy_id=DRAFT_V2_ATTEMPT_POLICY_ID,
        policy_version=DRAFT_V2_ATTEMPT_POLICY_VERSION,
    )


def prepare_draft_v2_generation_request(
    draft_v1: DraftV1,
    packet: CorrectionPacketV1A,
    receipt: CorrectionPacketIntegrityReceipt,
    authenticator: CorrectionPacketAuthenticator,
) -> DraftV2GenerationRequest:
    """Verify HMAC before producing any model-call-capable request."""

    try:
        verify_packet_authenticity(packet, receipt, authenticator)
    except (TypeError, RuntimeError) as exc:
        raise Step25BoundaryError(Step25ReasonCode.PACKET_HMAC_INVALID) from exc
    return DraftV2GenerationRequest(
        draft_v1=draft_v1,
        correction_packet=packet,
        integrity_receipt=receipt,
        request_id=draft_v1.request_id,
        tenant_id=draft_v1.tenant_id,
        user_id=draft_v1.user_id,
        route_hash=draft_v1.route_hash,
        original_query_digest=draft_v1.original_query_digest,
        draft_v1_hash=draft_v1.draft_hash,
        correction_packet_hash=packet.packet_hash,
        packet_integrity_receipt_hash=receipt.receipt_hash,
        provider_identity=load_approved_provider_spec().provider_identity(),
        prompt_template=load_draft_v2_prompt_template(),
        generation_parameters=load_draft_v2_generation_parameters(),
        timeout_policy=load_draft_v2_timeout_policy(),
        attempt_policy=load_draft_v2_attempt_policy(),
    )


def build_draft_v2_provider_request(
    request: DraftV2GenerationRequest,
) -> ProviderTextRequest:
    """Project exactly Draft V1 and the verified canonical packet into the call."""

    if not isinstance(request, DraftV2GenerationRequest):
        raise TypeError("request must be DraftV2GenerationRequest")
    try:
        packet_document = json.loads(canonical_packet_json(request.correction_packet))
        user_content = canonical_json(
            {
                "correction_packet": packet_document,
                "draft_v1": {
                    "draft_v1_hash": request.draft_v1_hash,
                    "exact_text": request.draft_v1.draft_text,
                },
                "instruction": "Produce the corrected Draft V2 only.",
            }
        )
        return ProviderTextRequest(
            provider_identity=request.provider_identity,
            purpose=DRAFT_V2_PROVIDER_PURPOSE,
            prompt_template_id=request.prompt_template.template_id,
            prompt_template_digest=request.prompt_template.template_digest,
            system_instruction=request.prompt_template.system_instruction,
            user_content=user_content,
            user_content_digest=hashlib.sha256(user_content.encode("utf-8")).hexdigest(),
            generation_parameters=request.generation_parameters,
        )
    except (TypeError, ValueError, RuntimeError) as exc:
        raise Step25BoundaryError(Step25ReasonCode.DRAFT_V2_INVALID) from exc


__all__ = [
    "DRAFT_V2_PROVIDER_PURPOSE",
    "build_draft_v2_provider_request",
    "load_draft_v2_attempt_policy",
    "load_draft_v2_generation_parameters",
    "load_draft_v2_prompt_template",
    "load_draft_v2_timeout_policy",
    "prepare_draft_v2_generation_request",
]
