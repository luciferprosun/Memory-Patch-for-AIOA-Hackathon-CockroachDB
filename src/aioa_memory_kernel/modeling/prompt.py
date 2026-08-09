"""Evidence-blind provider request construction for genuine Draft V1."""

from __future__ import annotations

from .models import (
    ModelGenerationRequest,
    ProviderCallRequest,
    verify_generation_request_hash,
)


def build_provider_call_request(
    request: ModelGenerationRequest,
) -> ProviderCallRequest:
    """Project only the generic instruction and original user question."""

    if not isinstance(request, ModelGenerationRequest):
        raise TypeError("request must be a ModelGenerationRequest")
    verify_generation_request_hash(request)
    return ProviderCallRequest(
        provider_identity=request.provider_identity,
        prompt_template_id=request.prompt_template.template_id,
        prompt_template_digest=request.prompt_template.template_digest,
        system_instruction=request.prompt_template.system_instruction,
        original_query=request.original_query,
        original_query_digest=request.original_query_digest,
        generation_parameters=request.generation_parameters,
    )


__all__ = ["build_provider_call_request"]
