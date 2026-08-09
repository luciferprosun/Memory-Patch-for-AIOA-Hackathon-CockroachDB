"""Bounded semantic-verifier port whose output is candidate signal only."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Mapping
from typing import Protocol

from aioa_memory_kernel.contracts.serialization import canonical_json
from aioa_memory_kernel.modeling import (
    AttemptPolicy,
    GenerationParameters,
    ModelAdapterError,
    ModelReasonCode,
    PromptTemplate,
    ProviderTextRequest,
    TextGenerationProvider,
    TimeoutPolicy,
    load_approved_provider_spec,
    verify_provider_response_hash,
)
from aioa_memory_kernel.persistence import assert_no_open_persistence_transaction

from .models import (
    SEMANTIC_VERIFIER_POLICY_ID,
    SEMANTIC_VERIFIER_PROMPT_ID,
    SEMANTIC_VERIFIER_PROMPT_VERSION,
    SemanticCandidateVerdict,
    SemanticVerifierRequest,
    SemanticVerifierSignal,
    Step25ReasonCode,
    reason_codes,
)


SEMANTIC_TIMEOUT_POLICY_ID = "draft-v2-semantic-timeout-1a"
SEMANTIC_ATTEMPT_POLICY_ID = "draft-v2-semantic-retry-1a"
SEMANTIC_PROVIDER_PURPOSE = "draft-v2-semantic-verifier-1a"


class SemanticClaimVerifier(Protocol):
    def verify(self, request: SemanticVerifierRequest) -> SemanticVerifierSignal: ...


def load_semantic_verifier_prompt_template() -> PromptTemplate:
    return PromptTemplate(
        template_id=SEMANTIC_VERIFIER_PROMPT_ID,
        template_version=SEMANTIC_VERIFIER_PROMPT_VERSION,
        system_instruction=(
            "Compare one claim only with the bounded packet-permitted evidence. "
            "Return strict JSON with exactly candidate_verdict and "
            "evidence_reference_ids. candidate_verdict must be SUPPORTS, REFUTES, "
            "or UNCERTAIN. Use only supplied citation IDs. Do not infer authority, "
            "change policy, browse, call tools, or supply prose."
        ),
    )


def load_semantic_generation_parameters() -> GenerationParameters:
    return GenerationParameters(
        temperature="0",
        top_p="1",
        max_output_tokens=256,
        stop_sequences=(),
        streaming=False,
        seed=None,
        policy_id=SEMANTIC_VERIFIER_POLICY_ID,
        policy_version="1",
    )


def load_semantic_timeout_policy() -> TimeoutPolicy:
    return TimeoutPolicy(
        attempt_timeout_seconds=30,
        policy_id=SEMANTIC_TIMEOUT_POLICY_ID,
        policy_version="1",
    )


def load_semantic_attempt_policy() -> AttemptPolicy:
    return AttemptPolicy(
        max_attempts=2,
        retry_delay_milliseconds=250,
        policy_id=SEMANTIC_ATTEMPT_POLICY_ID,
        policy_version="1",
    )


def build_semantic_verifier_request(
    *,
    claim_id: str,
    draft_v2_hash: str,
    correction_packet_hash: str,
    claim_text: str,
    allowed_citation_ids: tuple[str, ...],
    evidence_context: tuple[str, ...],
    deterministic_context_digest: str,
) -> SemanticVerifierRequest:
    return SemanticVerifierRequest(
        claim_id=claim_id,
        draft_v2_hash=draft_v2_hash,
        correction_packet_hash=correction_packet_hash,
        claim_text=claim_text,
        allowed_citation_ids=tuple(sorted(set(allowed_citation_ids))),
        evidence_context=tuple(sorted(set(evidence_context))),
        deterministic_context_digest=deterministic_context_digest,
        provider_identity=load_approved_provider_spec().provider_identity(),
        prompt_template=load_semantic_verifier_prompt_template(),
        generation_parameters=load_semantic_generation_parameters(),
        timeout_policy=load_semantic_timeout_policy(),
        attempt_policy=load_semantic_attempt_policy(),
    )


def not_required_signal(request_hash: str) -> SemanticVerifierSignal:
    return SemanticVerifierSignal(
        semantic_request_hash=request_hash,
        candidate_verdict=SemanticCandidateVerdict.NOT_REQUIRED,
        evidence_reference_ids=(),
        reason_codes=(),
        provider_response_hash=None,
    )


class ProviderSemanticClaimVerifier:
    """Strict JSON adapter over the existing pinned Step 22 text provider."""

    def __init__(
        self,
        provider: TextGenerationProvider,
        *,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not callable(getattr(provider, "provider_identity", None)) or not callable(
            getattr(provider, "generate", None)
        ):
            raise TypeError("provider must implement TextGenerationProvider")
        if not callable(sleep):
            raise TypeError("sleep must be callable")
        self._provider = provider
        self._sleep = sleep

    @staticmethod
    def _unavailable(
        request: SemanticVerifierRequest,
        reason: Step25ReasonCode,
    ) -> SemanticVerifierSignal:
        verdict = (
            SemanticCandidateVerdict.INVALID
            if reason is Step25ReasonCode.SEMANTIC_VERIFIER_INVALID
            else SemanticCandidateVerdict.UNAVAILABLE
        )
        return SemanticVerifierSignal(
            semantic_request_hash=request.request_hash,
            candidate_verdict=verdict,
            evidence_reference_ids=(),
            reason_codes=reason_codes(reason),
            provider_response_hash=None,
        )

    def verify(self, request: SemanticVerifierRequest) -> SemanticVerifierSignal:
        if not isinstance(request, SemanticVerifierRequest):
            raise TypeError("request must be SemanticVerifierRequest")
        approved = load_approved_provider_spec().provider_identity()
        if request.provider_identity != approved or self._provider.provider_identity() != approved:
            return self._unavailable(request, Step25ReasonCode.SEMANTIC_VERIFIER_INVALID)
        user_content = canonical_json(
            {
                "allowed_citation_ids": request.allowed_citation_ids,
                "claim_text": request.claim_text,
                "evidence_context": request.evidence_context,
            }
        )
        provider_request = ProviderTextRequest(
            provider_identity=request.provider_identity,
            purpose=SEMANTIC_PROVIDER_PURPOSE,
            prompt_template_id=request.prompt_template.template_id,
            prompt_template_digest=request.prompt_template.template_digest,
            system_instruction=request.prompt_template.system_instruction,
            user_content=user_content,
            user_content_digest=hashlib.sha256(user_content.encode("utf-8")).hexdigest(),
            generation_parameters=request.generation_parameters,
        )
        response = None
        for attempt in range(1, request.attempt_policy.max_attempts + 1):
            assert_no_open_persistence_transaction()
            try:
                response = self._provider.generate(provider_request, request.timeout_policy)
            except ModelAdapterError as exc:
                if not exc.retryable or attempt >= request.attempt_policy.max_attempts:
                    return self._unavailable(
                        request,
                        Step25ReasonCode.SEMANTIC_VERIFIER_UNAVAILABLE,
                    )
                self._sleep(request.attempt_policy.retry_delay_milliseconds / 1000)
                continue
            break
        if response is None:
            return self._unavailable(request, Step25ReasonCode.SEMANTIC_VERIFIER_UNAVAILABLE)
        try:
            verify_provider_response_hash(response)
            decoded = json.loads(response.response_content)
            if not isinstance(decoded, Mapping) or set(decoded) != {
                "candidate_verdict",
                "evidence_reference_ids",
            }:
                raise ValueError("unexpected verifier schema")
            verdict = SemanticCandidateVerdict(decoded["candidate_verdict"])
            if verdict not in {
                SemanticCandidateVerdict.SUPPORTS,
                SemanticCandidateVerdict.REFUTES,
                SemanticCandidateVerdict.UNCERTAIN,
            }:
                raise ValueError("unsupported verifier verdict")
            references = decoded["evidence_reference_ids"]
            if (
                not isinstance(references, list)
                or any(not isinstance(item, str) for item in references)
                or len(references) != len(set(references))
                or not set(references).issubset(request.allowed_citation_ids)
            ):
                raise ValueError("verifier invented evidence")
            references_tuple = tuple(sorted(references))
            reason = {
                SemanticCandidateVerdict.SUPPORTS: Step25ReasonCode.SEMANTIC_VERIFIER_SUPPORTS,
                SemanticCandidateVerdict.REFUTES: Step25ReasonCode.SEMANTIC_VERIFIER_REFUTES,
                SemanticCandidateVerdict.UNCERTAIN: Step25ReasonCode.SEMANTIC_VERIFIER_UNCERTAIN,
            }[verdict]
            return SemanticVerifierSignal(
                semantic_request_hash=request.request_hash,
                candidate_verdict=verdict,
                evidence_reference_ids=references_tuple,
                reason_codes=reason_codes(reason),
                provider_response_hash=response.response_hash,
            )
        except (TypeError, ValueError, json.JSONDecodeError, RuntimeError):
            return self._unavailable(request, Step25ReasonCode.SEMANTIC_VERIFIER_INVALID)


class DeterministicFakeSemanticVerifier:
    """Offline fake used by ordinary tests and controlled validation."""

    def __init__(
        self,
        verdicts: Mapping[str, SemanticCandidateVerdict] | None = None,
        *,
        default: SemanticCandidateVerdict = SemanticCandidateVerdict.UNCERTAIN,
    ) -> None:
        self._verdicts = dict(verdicts or {})
        self._default = default
        self.requests: list[SemanticVerifierRequest] = []

    def verify(self, request: SemanticVerifierRequest) -> SemanticVerifierSignal:
        self.requests.append(request)
        verdict = self._verdicts.get(request.claim_text, self._default)
        if verdict not in {
            SemanticCandidateVerdict.SUPPORTS,
            SemanticCandidateVerdict.REFUTES,
            SemanticCandidateVerdict.UNCERTAIN,
            SemanticCandidateVerdict.UNAVAILABLE,
        }:
            verdict = SemanticCandidateVerdict.INVALID
        reason = {
            SemanticCandidateVerdict.SUPPORTS: Step25ReasonCode.SEMANTIC_VERIFIER_SUPPORTS,
            SemanticCandidateVerdict.REFUTES: Step25ReasonCode.SEMANTIC_VERIFIER_REFUTES,
            SemanticCandidateVerdict.UNCERTAIN: Step25ReasonCode.SEMANTIC_VERIFIER_UNCERTAIN,
            SemanticCandidateVerdict.UNAVAILABLE: Step25ReasonCode.SEMANTIC_VERIFIER_UNAVAILABLE,
            SemanticCandidateVerdict.INVALID: Step25ReasonCode.SEMANTIC_VERIFIER_INVALID,
        }[verdict]
        references = request.allowed_citation_ids if verdict in {
            SemanticCandidateVerdict.SUPPORTS,
            SemanticCandidateVerdict.REFUTES,
        } else ()
        return SemanticVerifierSignal(
            semantic_request_hash=request.request_hash,
            candidate_verdict=verdict,
            evidence_reference_ids=references,
            reason_codes=reason_codes(reason),
            provider_response_hash=None,
        )


__all__ = [
    "DeterministicFakeSemanticVerifier",
    "ProviderSemanticClaimVerifier",
    "SemanticClaimVerifier",
    "build_semantic_verifier_request",
    "load_semantic_attempt_policy",
    "load_semantic_generation_parameters",
    "load_semantic_timeout_policy",
    "load_semantic_verifier_prompt_template",
    "not_required_signal",
]
