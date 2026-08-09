"""Deterministic Step 26 answer, review, and bounded-failure assembly."""

from __future__ import annotations

from aioa_memory_kernel.contracts.enums import AnswerStatus, EvidenceStatus, KnowledgeRoute
from aioa_memory_kernel.contracts.exceptions import ContractValidationError, IntegrityError
from aioa_memory_kernel.routing import KnowledgePolicyDecision
from aioa_memory_kernel.verification import (
    CheckResult,
    CorrectionComplianceStatus,
    DraftV2PipelineResult,
    FinalStep25ClaimVerdict,
    ProhibitedClaimPresence,
    verify_draft_v2_pipeline_result_hash,
)

from .models import (
    BoundedAnswerFailure,
    ClaimVerificationReference,
    FinalAnswerRequest,
    FinalFailureClass,
    FinalOutputStatus,
    HumanReviewRequired,
    RetryFailureSummary,
    STEP26_SCHEMA_VERSION,
    Step26BoundaryError,
    Step26ReasonCode,
    VerifiedAnswer,
    reason_codes,
    verify_final_answer_request_hash,
)
from .policy import evaluate_final_eligibility


def derive_retry_failure_summary(
    pipeline: DraftV2PipelineResult,
) -> RetryFailureSummary:
    """Project only bounded verification failures into the single retry."""

    try:
        verify_draft_v2_pipeline_result_hash(pipeline)
    except (ContractValidationError, IntegrityError) as exc:
        raise Step26BoundaryError(Step26ReasonCode.INTEGRITY_FAILURE) from exc
    summary = pipeline.verification_summary
    unsatisfied = tuple(
        sorted(
            item.correction_id
            for item in summary.required_correction_results
            if item.status
            in {
                CorrectionComplianceStatus.NOT_SATISFIED,
                CorrectionComplianceStatus.PARTIALLY_SATISFIED,
            }
        )
    )
    prohibited = tuple(
        sorted(
            item.prohibited_claim_id
            for item in summary.prohibited_claim_results
            if item.presence
            in {
                ProhibitedClaimPresence.PRESENT_EXACT,
                ProhibitedClaimPresence.PRESENT_SEMANTICALLY_EQUIVALENT,
            }
        )
    )
    invalid_citations = tuple(
        sorted(
            item.claim_id
            for item in pipeline.ordered_claim_verifications
            if item.citation_result is CheckResult.FAIL
        )
    )
    by_verdict = {
        verdict: tuple(
            sorted(
                item.claim_id
                for item in pipeline.ordered_claim_verifications
                if item.final_step25_verdict is verdict
            )
        )
        for verdict in FinalStep25ClaimVerdict
    }
    reasons = [Step26ReasonCode.FINAL_RETRY_REQUIRED]
    if unsatisfied:
        reasons.append(Step26ReasonCode.REQUIRED_CORRECTION_UNSATISFIED)
    if prohibited:
        reasons.append(Step26ReasonCode.PROHIBITED_CLAIM_VIOLATION)
    if invalid_citations:
        reasons.append(Step26ReasonCode.INVALID_CITATION)
    if by_verdict[FinalStep25ClaimVerdict.UNVERIFIED]:
        reasons.append(Step26ReasonCode.UNVERIFIED_CLAIM_REMAINS)
    return RetryFailureSummary(
        original_draft_v2_hash=pipeline.draft_v2.draft_v2_hash,
        original_verification_summary_hash=summary.summary_hash,
        unsatisfied_correction_ids=unsatisfied,
        violated_prohibited_claim_ids=prohibited,
        invalid_citation_claim_ids=invalid_citations,
        unverified_claim_ids=by_verdict[FinalStep25ClaimVerdict.UNVERIFIED],
        refuted_claim_ids=by_verdict[FinalStep25ClaimVerdict.VERIFIED_REFUTED],
        conflicting_claim_ids=by_verdict[FinalStep25ClaimVerdict.CONFLICTING],
        invalid_claim_ids=by_verdict[FinalStep25ClaimVerdict.INVALID],
        reason_codes=reason_codes(*reasons),
    )


def _request_for_pipeline(
    request: FinalAnswerRequest,
    pipeline: DraftV2PipelineResult,
) -> FinalAnswerRequest:
    if request.step25_result is pipeline:
        return request
    return FinalAnswerRequest(
        route=request.route,
        policy_result=request.policy_result,
        step20_outcomes=request.step20_outcomes,
        temporal_result=request.temporal_result,
        draft_v1=request.draft_v1,
        correction_packet=request.correction_packet,
        integrity_receipt=request.integrity_receipt,
        step25_result=pipeline,
        final_policy=request.final_policy,
    )


def assemble_verified_answer(
    request: FinalAnswerRequest,
    *,
    pipeline: DraftV2PipelineResult | None = None,
) -> VerifiedAnswer:
    """Expose the exact Draft V2 text only after every Step 26 ceiling passes."""

    selected_pipeline = pipeline or request.step25_result
    if selected_pipeline is None:
        raise Step26BoundaryError(Step26ReasonCode.INTEGRITY_FAILURE)
    candidate_request = _request_for_pipeline(request, selected_pipeline)
    decision = evaluate_final_eligibility(candidate_request)
    if decision.output_status is not FinalOutputStatus.VERIFIED_ANSWER:
        raise Step26BoundaryError(Step26ReasonCode.DRAFT_V2_VERIFICATION_FAILED)
    verify_final_answer_request_hash(candidate_request)
    assert candidate_request.correction_packet is not None
    assert candidate_request.integrity_receipt is not None
    assert candidate_request.temporal_result is not None
    packet = candidate_request.correction_packet
    temporal = candidate_request.temporal_result
    draft_v2 = selected_pipeline.draft_v2

    claims_by_id = {item.claim_id: item for item in selected_pipeline.ordered_claims}
    verification_by_id = {
        item.claim_id: item
        for item in selected_pipeline.ordered_claim_verifications
    }
    if set(claims_by_id) != set(verification_by_id):
        raise Step26BoundaryError(Step26ReasonCode.INTEGRITY_FAILURE)
    for claim_id, claim in claims_by_id.items():
        verification = verification_by_id[claim_id]
        if (
            verification.claim_hash != claim.claim_hash
            or verification.draft_v2_hash != draft_v2.draft_v2_hash
            or verification.final_step25_verdict
            is not FinalStep25ClaimVerdict.VERIFIED_SUPPORTED
        ):
            raise Step26BoundaryError(
                Step26ReasonCode.UNVERIFIED_CLAIM_REMAINS
            )

    references = tuple(
        sorted(
            (
                ClaimVerificationReference(
                    claim_id=item.claim_id,
                    claim_hash=item.claim_hash,
                    verification_hash=verification_by_id[item.claim_id].verification_hash,
                    final_verdict=verification_by_id[item.claim_id].final_step25_verdict,
                )
                for item in selected_pipeline.ordered_claims
            ),
            key=lambda item: item.claim_id,
        )
    )
    supported_source_claim_ids = {
        source_claim_id
        for claim in selected_pipeline.ordered_claims
        for source_claim_id in claim.aligned_draft_v1_claim_ids
        if verification_by_id[claim.claim_id].final_step25_verdict
        is FinalStep25ClaimVerdict.VERIFIED_SUPPORTED
    }
    cited_ids = {
        citation_id
        for claim in selected_pipeline.ordered_claims
        for citation_id in claim.cited_citation_ids
    }
    citations = tuple(
        item
        for item in packet.ordered_citations
        if item.claim_id in supported_source_claim_ids or item.citation_id in cited_ids
    )
    allowed_citation_ids = {item.citation_id for item in packet.ordered_citations}
    if not cited_ids.issubset(allowed_citation_ids):
        raise Step26BoundaryError(Step26ReasonCode.INVALID_CITATION)

    limitations = tuple(
        sorted(
            {
                *temporal.limitations,
                *packet.temporal_freshness_limitations,
                *(
                    limitation
                    for verification in selected_pipeline.ordered_claim_verifications
                    for limitation in verification.limitations
                ),
            }
        )
    )
    bundles = tuple(item.bundle for item in candidate_request.step20_outcomes)
    if not bundles or any(item is None for item in bundles):
        raise Step26BoundaryError(Step26ReasonCode.INTEGRITY_FAILURE)
    primary = bundles[0]
    assert primary is not None
    fallback = bundles[1] if len(bundles) > 1 else None
    return VerifiedAnswer(
        schema_version=STEP26_SCHEMA_VERSION,
        request_id=candidate_request.route.request_id,
        tenant_id=candidate_request.route.tenant_id,
        user_id=candidate_request.route.user_id,
        route_hash=candidate_request.route.route_hash,
        selected_hat_id=primary.selected_hat_id,
        selected_hat_version=primary.selected_hat_version,
        selected_manifest_digest=primary.selected_manifest_digest,
        effective_scope=primary.effective_scope,
        evidence_bundle_hash=primary.bundle_hash,
        fallback_evidence_bundle_hash=(
            fallback.bundle_hash if fallback is not None else None
        ),
        temporal_resolution_hash=temporal.result_hash,
        correction_packet_hash=packet.packet_hash,
        packet_integrity_receipt_hash=candidate_request.integrity_receipt.receipt_hash,
        draft_v2_hash=draft_v2.draft_v2_hash,
        verification_summary_hash=(
            selected_pipeline.verification_summary.summary_hash
        ),
        answer_text=draft_v2.draft_text,
        ordered_citations=citations,
        claim_verification_references=references,
        limitations=limitations,
        evidence_status=temporal.evidence_status,
        knowledge_policy_decision=(
            candidate_request.policy_result.knowledge_policy_decision
        ),
        execution_authorization_decision=(
            candidate_request.policy_result.execution_authorization_decision
        ),
        answer_status=AnswerStatus.VERIFIED,
        output_status=FinalOutputStatus.VERIFIED_ANSWER,
        final_policy_digest=candidate_request.final_policy.policy_digest,
    )


def build_human_review_result(
    request: FinalAnswerRequest,
    *,
    pipelines: tuple[DraftV2PipelineResult, ...] = (),
    confirmation: bool = False,
    extra_reasons: tuple[Step26ReasonCode, ...] = (),
) -> HumanReviewRequired:
    for pipeline in pipelines:
        verify_draft_v2_pipeline_result_hash(pipeline)
    latest = pipelines[-1] if pipelines else request.step25_result
    hashes = tuple(
        item.draft_v2.draft_v2_hash for item in pipelines
    ) or (
        (request.step25_result.draft_v2.draft_v2_hash,)
        if request.step25_result is not None
        else ()
    )
    payload_hashes = {
        request.route.route_hash,
        request.policy_result.policy_result_hash,
    }
    if request.correction_packet is not None:
        payload_hashes.add(request.correction_packet.packet_hash)
    if latest is not None:
        payload_hashes.add(latest.verification_summary.summary_hash)
    return HumanReviewRequired(
        schema_version=STEP26_SCHEMA_VERSION,
        request_id=request.route.request_id,
        tenant_id=request.route.tenant_id,
        user_id=request.route.user_id,
        route_hash=request.route.route_hash,
        selected_hat_id=request.route.selected_hat_id,
        selected_hat_version=request.route.selected_hat_version,
        selected_manifest_digest=request.route.selected_manifest_digest,
        draft_v1_hash=request.draft_v1.draft_hash if request.draft_v1 else None,
        draft_v2_hashes=hashes,
        correction_packet_hash=(
            request.correction_packet.packet_hash
            if request.correction_packet is not None
            else None
        ),
        verification_summary_hash=(
            latest.verification_summary.summary_hash if latest is not None else None
        ),
        evidence_status=request.policy_result.evidence_status,
        knowledge_policy_decision=request.policy_result.knowledge_policy_decision,
        output_status=(
            FinalOutputStatus.CONFIRMATION_REQUIRED
            if confirmation
            else FinalOutputStatus.HUMAN_REVIEW_REQUIRED
        ),
        reason_codes=reason_codes(
            *extra_reasons,
            Step26ReasonCode.HUMAN_REVIEW_REQUIRED,
        ),
        review_payload_hashes=tuple(sorted(payload_hashes)),
    )


_FAILURE_DETAILS = {
    FinalOutputStatus.BLOCKED_POLICY: (
        FinalFailureClass.POLICY_BLOCK,
        AnswerStatus.BLOCKED_POLICY,
        "Answer output is blocked by the verified knowledge policy.",
    ),
    FinalOutputStatus.INSUFFICIENT_EVIDENCE: (
        FinalFailureClass.INSUFFICIENT_EVIDENCE,
        AnswerStatus.BLOCKED_NO_VERIFIED_EVIDENCE,
        "Unable to verify an answer from the available evidence.",
    ),
    FinalOutputStatus.STALE_EVIDENCE: (
        FinalFailureClass.STALE_EVIDENCE,
        AnswerStatus.BLOCKED_NO_VERIFIED_EVIDENCE,
        "The available evidence is stale under the verified policy.",
    ),
    FinalOutputStatus.UNAVAILABLE_EVIDENCE: (
        FinalFailureClass.UNAVAILABLE_EVIDENCE,
        AnswerStatus.BLOCKED_NO_VERIFIED_EVIDENCE,
        "Required evidence is unavailable.",
    ),
    FinalOutputStatus.CONFLICTING_EVIDENCE: (
        FinalFailureClass.CONFLICTING_EVIDENCE,
        AnswerStatus.BLOCKED_CONFLICTING_EVIDENCE,
        "Conflicting evidence requires review.",
    ),
    FinalOutputStatus.FAILED_VERIFICATION: (
        FinalFailureClass.VERIFICATION_FAILED,
        AnswerStatus.BLOCKED_VERIFICATION_FAILED,
        "The answer could not be verified.",
    ),
    FinalOutputStatus.INTEGRITY_FAILURE: (
        FinalFailureClass.INTEGRITY_FAILURE,
        AnswerStatus.BLOCKED_VERIFICATION_FAILED,
        "Answer integrity verification failed.",
    ),
}


def build_bounded_failure(
    request: FinalAnswerRequest,
    output_status: FinalOutputStatus,
    *,
    extra_reasons: tuple[Step26ReasonCode, ...] = (),
) -> BoundedAnswerFailure:
    if output_status not in _FAILURE_DETAILS:
        raise ContractValidationError("output status cannot produce a bounded failure")
    failure_class, answer_status, message = _FAILURE_DETAILS[output_status]
    if request.route.knowledge_route is KnowledgeRoute.AMBIGUOUS:
        failure_class = FinalFailureClass.AMBIGUOUS_ROUTE
        answer_status = AnswerStatus.BLOCKED_AMBIGUOUS_ROUTE
    elif request.route.knowledge_route is KnowledgeRoute.PASS_THROUGH:
        failure_class = FinalFailureClass.PASS_THROUGH_NOT_AUTHORIZED
    summary = (
        request.step25_result.verification_summary
        if request.step25_result is not None
        else None
    )
    return BoundedAnswerFailure(
        schema_version=STEP26_SCHEMA_VERSION,
        request_id=request.route.request_id,
        tenant_id=request.route.tenant_id,
        user_id=request.route.user_id,
        route_hash=request.route.route_hash,
        failure_class=failure_class,
        evidence_status=request.policy_result.evidence_status,
        knowledge_policy_decision=request.policy_result.knowledge_policy_decision,
        answer_status=answer_status,
        output_status=output_status,
        verification_summary_hash=(summary.summary_hash if summary else None),
        reason_codes=reason_codes(*extra_reasons),
        safe_message=message,
    )


__all__ = [
    "assemble_verified_answer",
    "build_bounded_failure",
    "build_human_review_result",
    "derive_retry_failure_summary",
]
