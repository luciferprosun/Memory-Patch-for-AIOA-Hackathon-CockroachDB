"""Strict Step 39 Critic prompt projection and response parser.

The provider receives a bounded, deterministic review projection.  Its raw
response is untrusted and is never retained by the returned contract: only the
SHA-256 digest of the exact UTF-8 response bytes survives parsing.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from aioa_memory_kernel.contracts.exceptions import (
    ContractValidationError,
    IntegrityError,
)
from aioa_memory_kernel.contracts.serialization import (
    canonical_json,
    canonical_sha256,
    require_sha256_hex,
    to_canonical_data,
)
from aioa_memory_kernel.modeling import GenerationParameters, ProviderTextRequest
from aioa_memory_kernel.security import assert_secret_free

from .models import (
    MAX_CRITIC_PROVIDER_CONTENT_BYTES,
    MAX_CRITIC_RAW_RESPONSE_BYTES,
    STEP39_SCHEMA_VERSION,
    CriticAssessment,
    CriticCandidateScope,
    CriticIssueType,
    CriticLimitationCode,
    CriticReasonCode,
    CriticReviewRequest,
    REQUIRED_ASSESSMENT_LIMITATIONS,
    REQUIRED_ISSUE_REASON_CODES,
    load_critic_prompt_template,
    verify_critic_assessment_against_request,
    verify_critic_review_request,
)


CRITIC_PROVIDER_PURPOSE = "step39-critic-bounded-review"
CRITIC_GENERATION_POLICY_ID = "step39-critic-bounded-generation-1a"
CRITIC_GENERATION_POLICY_VERSION = "1"

_RESPONSE_FIELDS = frozenset(
    {
        "affected_claim_ids",
        "artifact_references_digest",
        "candidate_correction_text",
        "claim_references_digest",
        "critic_request_hash",
        "diagnostic_confidence_basis_points",
        "evidence_reference_ids",
        "evidence_references_digest",
        "issue_detected",
        "issue_type",
        "limitations",
        "provider_identity_digest",
        "reason_codes",
        "route_hash",
        "schema_version",
        "scope_digest",
    }
)

def _strict_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number {value!r}")


def _raw_sha256(value: str) -> str:
    try:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()
    except UnicodeEncodeError as error:
        raise ContractValidationError("Critic response is not valid UTF-8 text") from error


def _reference_digest(kind: str, values: tuple[object, ...]) -> str:
    if kind == "artifact":
        items = [
            {
                "artifact_kind": value.artifact_kind.value,
                "artifact_id": value.artifact_id,
                "artifact_hash": value.artifact_hash,
                "reference_hash": value.reference_hash,
            }
            for value in values
        ]
    elif kind == "claim":
        items = [
            {
                "claim_id": value.claim_id,
                "statement_sha256": value.statement_sha256,
                "reference_hash": value.reference_hash,
            }
            for value in values
        ]
    elif kind == "evidence":
        items = [
            {
                "reference_id": value.reference_id,
                "snippet_sha256": value.snippet_sha256,
                "reference_hash": value.reference_hash,
            }
            for value in values
        ]
    else:  # pragma: no cover - all callers use a closed internal value
        raise AssertionError("unsupported reference digest kind")
    return canonical_sha256({f"{kind}_references": items})


def _bindings(request: CriticReviewRequest) -> dict[str, str]:
    return {
        "artifact_references_digest": _reference_digest(
            "artifact", request.artifacts
        ),
        "claim_references_digest": _reference_digest(
            "claim", request.claim_references
        ),
        "critic_request_hash": request.request_hash,
        "evidence_references_digest": _reference_digest(
            "evidence", request.evidence_references
        ),
        "provider_identity_digest": request.provider_identity.identity_digest,
        "route_hash": request.route_hash,
        "scope_digest": request.scope_digest,
    }


def _artifact_projection(request: CriticReviewRequest) -> list[dict[str, object]]:
    return [
        {
            "artifact_hash": item.artifact_hash,
            "artifact_id": item.artifact_id,
            "artifact_kind": item.artifact_kind.value,
            "byte_length": item.byte_length,
            "reference_hash": item.reference_hash,
            "text": item.text,
            "text_sha256": item.text_sha256,
        }
        for item in request.artifacts
    ]


def _claim_projection(request: CriticReviewRequest) -> list[dict[str, object]]:
    return [
        {
            "claim_category": item.claim_category,
            "claim_id": item.claim_id,
            "draft_id": item.draft_id,
            "evidence_reference_ids": list(item.evidence_reference_ids),
            "reference_hash": item.reference_hash,
            "statement": item.statement,
            "statement_sha256": item.statement_sha256,
            "verification_status": item.verification_status.value,
        }
        for item in request.claim_references
    ]


def _evidence_projection(request: CriticReviewRequest) -> list[dict[str, object]]:
    return [
        {
            "authority_level": item.authority_level.value,
            "chunk_id": item.chunk_id,
            "evidence_id": item.evidence_id,
            "freshness_status": item.freshness_status.value,
            "publication_state": item.publication_state.value,
            "reference_hash": item.reference_hash,
            "reference_id": item.reference_id,
            "relation": item.relation.value,
            "snippet": item.snippet,
            "snippet_sha256": item.snippet_sha256,
            "source_id": item.source_id,
            "source_version_id": item.source_version_id,
            "temporal_applicability": item.temporal_applicability.value,
        }
        for item in request.evidence_references
    ]


def build_critic_prompt_payload(request: CriticReviewRequest) -> str:
    """Return the deterministic, bounded provider-visible JSON projection."""

    if not isinstance(request, CriticReviewRequest):
        raise TypeError("request must be CriticReviewRequest")
    verify_critic_review_request(request)
    bindings = _bindings(request)
    payload = canonical_json(
        {
            "bindings_to_echo_exactly": bindings,
            "bounded_context": {
                "artifacts": _artifact_projection(request),
                "claims": _claim_projection(request),
                "conflict_preserved": request.conflict_preserved,
                "correction_packet_hash": request.correction_packet_hash,
                "effective_scope": to_canonical_data(request.effective_scope),
                "evidence_references": _evidence_projection(request),
                "evidence_status": request.evidence_status.value,
                "freshness_status": request.freshness_status.value,
                "original_query": request.original_query,
                "original_query_digest": request.original_query_digest,
                "selected_hat": {
                    "hat_id": request.selected_hat_id,
                    "hat_version": request.selected_hat_version,
                    "manifest_digest": request.selected_manifest_digest,
                },
                "temporal_applicability": request.temporal_applicability.value,
            },
            "objective": request.bounded_review_objective,
            "output_contract": {
                "exact_fields": sorted(_RESPONSE_FIELDS),
                "issue_types": sorted(item.value for item in CriticIssueType),
                "limitation_codes": sorted(
                    item.value for item in CriticLimitationCode
                ),
                "reason_codes": sorted(item.value for item in CriticReasonCode),
                "required_issue_reason_codes": sorted(
                    item.value for item in REQUIRED_ISSUE_REASON_CODES
                ),
                "required_limitations": sorted(
                    item.value for item in REQUIRED_ASSESSMENT_LIMITATIONS
                ),
                "rules": [
                    "Echo every binding exactly.",
                    "Use only supplied claim_id and reference_id values.",
                    "Arrays must be sorted, unique JSON arrays.",
                    "For NO_ISSUE use empty reference arrays, null correction, null confidence, and only NO_ISSUE reason.",
                    "Do not add ownership, authority, action, tool, prose, markdown, or wrapper fields.",
                ],
            },
            "schema_version": STEP39_SCHEMA_VERSION,
        }
    )
    if len(payload.encode("utf-8")) > MAX_CRITIC_PROVIDER_CONTENT_BYTES:
        raise ContractValidationError("Critic provider projection exceeds policy")
    try:
        assert_secret_free(
            json.loads(payload),
            surface="Step 39 Critic provider projection",
            reject_machine_paths=True,
        )
    except ValueError as error:
        raise ContractValidationError(
            "Critic provider projection contains forbidden material"
        ) from error
    return payload


def build_critic_provider_request(request: CriticReviewRequest) -> ProviderTextRequest:
    """Build a pinned, tool-less provider request from one verified review request."""

    payload = build_critic_prompt_payload(request)
    prompt = load_critic_prompt_template()
    return ProviderTextRequest(
        provider_identity=request.provider_identity,
        purpose=CRITIC_PROVIDER_PURPOSE,
        prompt_template_id=prompt.prompt_id,
        prompt_template_digest=prompt.prompt_digest,
        system_instruction=prompt.system_instruction,
        user_content=payload,
        user_content_digest=_raw_sha256(payload),
        generation_parameters=GenerationParameters(
            temperature="0",
            top_p="1",
            max_output_tokens=1024,
            stop_sequences=(),
            streaming=False,
            seed=None,
            policy_id=CRITIC_GENERATION_POLICY_ID,
            policy_version=CRITIC_GENERATION_POLICY_VERSION,
        ),
    )


def _decode_response(raw_response: str) -> Mapping[str, Any]:
    if not isinstance(raw_response, str):
        raise TypeError("raw_response must be text")
    try:
        raw = raw_response.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ContractValidationError("Critic response is not valid UTF-8 text") from error
    if not raw or len(raw) > MAX_CRITIC_RAW_RESPONSE_BYTES:
        raise ContractValidationError("Critic response is outside the byte bound")
    try:
        decoded = json.loads(
            raw_response,
            object_pairs_hook=_strict_pairs,
            parse_constant=_reject_json_constant,
        )
    except (json.JSONDecodeError, TypeError, ValueError, RecursionError, OverflowError) as error:
        raise ContractValidationError("Critic response is not strict JSON") from error
    if not isinstance(decoded, Mapping) or set(decoded) != _RESPONSE_FIELDS:
        raise ContractValidationError("Critic response fields differ from contract")
    return decoded


def _string_list(value: object, name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ContractValidationError(f"{name} must be a JSON string array")
    result = tuple(value)
    if result != tuple(sorted(set(result))):
        raise ContractValidationError(f"{name} must be sorted and unique")
    return result


def _enum_list(
    value: object,
    enum_type: type[CriticReasonCode] | type[CriticLimitationCode],
    name: str,
) -> tuple[CriticReasonCode, ...] | tuple[CriticLimitationCode, ...]:
    result = _string_list(value, name)
    try:
        return tuple(enum_type(item) for item in result)
    except ValueError as error:
        raise ContractValidationError(f"{name} contains an unknown value") from error


def _require_binding(document: Mapping[str, Any], name: str, expected: str) -> None:
    value = document[name]
    if not isinstance(value, str) or value != expected:
        raise IntegrityError(f"Critic response {name} binding mismatch")


def parse_critic_assessment(
    raw_response: str,
    *,
    request: CriticReviewRequest,
    provider_response_hash: str,
) -> CriticAssessment:
    """Parse one exact provider object and discard its raw content after hashing."""

    if not isinstance(request, CriticReviewRequest):
        raise TypeError("request must be CriticReviewRequest")
    verify_critic_review_request(request)
    require_sha256_hex(provider_response_hash, "provider_response_hash")
    document = _decode_response(raw_response)
    bindings = _bindings(request)
    if document["schema_version"] != STEP39_SCHEMA_VERSION:
        raise ContractValidationError("unsupported Critic response schema")
    for name, expected in bindings.items():
        _require_binding(document, name, expected)

    if not isinstance(document["issue_detected"], bool):
        raise ContractValidationError("issue_detected must be boolean")
    try:
        issue_type = CriticIssueType(document["issue_type"])
    except (TypeError, ValueError) as error:
        raise ContractValidationError("issue_type is outside the closed enum") from error
    affected_claim_ids = _string_list(
        document["affected_claim_ids"], "affected_claim_ids"
    )
    evidence_reference_ids = _string_list(
        document["evidence_reference_ids"], "evidence_reference_ids"
    )
    reason_codes = _enum_list(
        document["reason_codes"], CriticReasonCode, "reason_codes"
    )
    limitations = _enum_list(
        document["limitations"], CriticLimitationCode, "limitations"
    )
    if not REQUIRED_ASSESSMENT_LIMITATIONS.issubset(limitations):
        raise ContractValidationError("Critic response omits authority limitations")

    correction = document["candidate_correction_text"]
    confidence = document["diagnostic_confidence_basis_points"]
    issue_detected = document["issue_detected"]
    if issue_detected:
        if not REQUIRED_ISSUE_REASON_CODES.issubset(reason_codes):
            raise ContractValidationError("Critic issue omits required reason codes")
        if CriticReasonCode.NO_ISSUE in reason_codes:
            raise ContractValidationError("Critic issue contains a contradictory reason")
        candidate_scope = CriticCandidateScope(
            scope_digest=request.scope_digest,
            dimension_names=tuple(item.name for item in request.effective_scope),
        )
    else:
        if confidence is not None:
            raise ContractValidationError("NO_ISSUE confidence must be null")
        candidate_scope = None

    assessment = CriticAssessment(
        schema_version=STEP39_SCHEMA_VERSION,
        critic_request_hash=request.request_hash,
        issue_detected=issue_detected,
        issue_type=issue_type,
        affected_claim_ids=affected_claim_ids,
        candidate_correction_text=correction,
        candidate_scope=candidate_scope,
        evidence_reference_ids=evidence_reference_ids,
        reason_codes=reason_codes,
        diagnostic_confidence_basis_points=confidence,
        limitations=limitations,
        provider_identity_digest=request.provider_identity.identity_digest,
        provider_response_hash=provider_response_hash,
        raw_response_digest=_raw_sha256(raw_response),
    )
    verify_critic_assessment_against_request(assessment, request)

    if assessment.issue_detected:
        claims = {item.claim_id: item for item in request.claim_references}
        selected_evidence = set(assessment.evidence_reference_ids)
        if any(
            not selected_evidence.intersection(claims[claim_id].evidence_reference_ids)
            for claim_id in assessment.affected_claim_ids
        ):
            raise IntegrityError("Critic issue evidence is detached from a claim")
    return assessment


__all__ = [
    "CRITIC_GENERATION_POLICY_ID",
    "CRITIC_GENERATION_POLICY_VERSION",
    "CRITIC_PROVIDER_PURPOSE",
    "build_critic_prompt_payload",
    "build_critic_provider_request",
    "parse_critic_assessment",
]
