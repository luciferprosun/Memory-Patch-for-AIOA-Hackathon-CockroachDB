"""Pure Step 26 final-answer eligibility and retry policy."""

from __future__ import annotations

from dataclasses import dataclass, field

from aioa_memory_kernel.contracts.enums import AnswerStatus, EvidenceStatus, KnowledgeRoute
from aioa_memory_kernel.contracts.exceptions import ContractValidationError, IntegrityError
from aioa_memory_kernel.contracts.serialization import (
    canonical_sha256,
    require_enum_member,
    require_sha256_hex,
    verify_canonical_hash,
)
from aioa_memory_kernel.routing import KnowledgePolicyDecision
from aioa_memory_kernel.verification import VerificationSummaryStatus

from .models import (
    FinalAnswerRequest,
    FinalOutputStatus,
    Step26ReasonCode,
    reason_codes,
    verify_final_answer_request_hash,
)


@dataclass(frozen=True, slots=True)
class FinalEligibilityDecision:
    final_answer_request_hash: str
    output_status: FinalOutputStatus
    retry_permitted: bool
    reason_codes: tuple[Step26ReasonCode, ...]
    decision_hash: str = field(init=False)

    def __post_init__(self) -> None:
        require_sha256_hex(self.final_answer_request_hash, "final_answer_request_hash")
        require_enum_member(self.output_status, FinalOutputStatus, "output_status")
        if not isinstance(self.retry_permitted, bool):
            raise ContractValidationError("retry_permitted must be boolean")
        if self.retry_permitted != (
            self.output_status is FinalOutputStatus.RETRY_REQUIRED
        ):
            raise ContractValidationError("retry disposition and output status differ")
        canonical_reasons = reason_codes(*self.reason_codes)
        if tuple(self.reason_codes) != canonical_reasons or not canonical_reasons:
            raise ContractValidationError("eligibility reasons must be canonical")
        object.__setattr__(self, "reason_codes", canonical_reasons)
        object.__setattr__(
            self,
            "decision_hash",
            canonical_sha256(self, exclude_fields=("decision_hash",)),
        )


def _decision(
    request: FinalAnswerRequest,
    status: FinalOutputStatus,
    *reasons: Step26ReasonCode,
    retry: bool = False,
) -> FinalEligibilityDecision:
    return FinalEligibilityDecision(
        final_answer_request_hash=request.request_hash,
        output_status=status,
        retry_permitted=retry,
        reason_codes=reason_codes(*reasons),
    )


def _policy_shape_is_valid(request: FinalAnswerRequest) -> bool:
    policy = request.policy_result
    decision = policy.knowledge_policy_decision
    if decision is KnowledgePolicyDecision.ALLOW_ANSWER:
        return policy.answer_status in {
            AnswerStatus.DRAFT,
            AnswerStatus.PASS_THROUGH_RESULT,
        }
    if decision is KnowledgePolicyDecision.REQUIRE_CONFIRMATION:
        return policy.answer_status in {
            AnswerStatus.DRAFT,
            AnswerStatus.BLOCKED_CONFLICTING_EVIDENCE,
            AnswerStatus.BLOCKED_NO_VERIFIED_EVIDENCE,
        }
    return policy.answer_status in {
        AnswerStatus.BLOCKED_NO_VERIFIED_EVIDENCE,
        AnswerStatus.BLOCKED_CONFLICTING_EVIDENCE,
        AnswerStatus.BLOCKED_VERIFICATION_FAILED,
        AnswerStatus.BLOCKED_POLICY,
        AnswerStatus.BLOCKED_STORAGE_UNAVAILABLE,
        AnswerStatus.BLOCKED_AMBIGUOUS_ROUTE,
        AnswerStatus.MODEL_GENERATION_FAILED,
    }


def evaluate_final_eligibility(
    request: FinalAnswerRequest,
) -> FinalEligibilityDecision:
    """Return a deterministic final disposition without side effects."""

    if not isinstance(request, FinalAnswerRequest):
        raise TypeError("request must be FinalAnswerRequest")
    try:
        verify_final_answer_request_hash(request)
    except (ContractValidationError, IntegrityError, RuntimeError):
        return _decision(
            request,
            FinalOutputStatus.INTEGRITY_FAILURE,
            Step26ReasonCode.INTEGRITY_FAILURE,
        )
    route = request.route.knowledge_route
    if route is KnowledgeRoute.AMBIGUOUS:
        return _decision(
            request,
            FinalOutputStatus.BLOCKED_POLICY,
            Step26ReasonCode.AMBIGUOUS_ROUTE,
            Step26ReasonCode.ANSWER_BLOCKED_POLICY,
        )
    if route is KnowledgeRoute.PASS_THROUGH:
        return _decision(
            request,
            FinalOutputStatus.BLOCKED_POLICY,
            Step26ReasonCode.PASS_THROUGH_FALLBACK_NOT_AUTHORIZED,
            Step26ReasonCode.ANSWER_BLOCKED_POLICY,
        )
    if not _policy_shape_is_valid(request):
        return _decision(
            request,
            FinalOutputStatus.INTEGRITY_FAILURE,
            Step26ReasonCode.INTEGRITY_FAILURE,
        )
    policy = request.policy_result.knowledge_policy_decision
    if policy is KnowledgePolicyDecision.BLOCK_ANSWER:
        return _decision(
            request,
            FinalOutputStatus.BLOCKED_POLICY,
            Step26ReasonCode.ANSWER_BLOCKED_POLICY,
        )
    if policy is KnowledgePolicyDecision.REQUIRE_CONFIRMATION:
        return _decision(
            request,
            FinalOutputStatus.CONFIRMATION_REQUIRED,
            Step26ReasonCode.ANSWER_REQUIRE_CONFIRMATION,
            Step26ReasonCode.HUMAN_REVIEW_REQUIRED,
        )

    assert request.temporal_result is not None
    evidence = request.temporal_result.evidence_status
    if evidence is EvidenceStatus.INSUFFICIENT or evidence is EvidenceStatus.NOT_REQUIRED:
        return _decision(
            request,
            FinalOutputStatus.INSUFFICIENT_EVIDENCE,
            Step26ReasonCode.ANSWER_INSUFFICIENT_EVIDENCE,
        )
    if evidence is EvidenceStatus.CONFLICTING:
        return _decision(
            request,
            FinalOutputStatus.CONFLICTING_EVIDENCE,
            Step26ReasonCode.ANSWER_CONFLICTING_EVIDENCE,
            Step26ReasonCode.HUMAN_REVIEW_REQUIRED,
        )
    if evidence is EvidenceStatus.STALE:
        return _decision(
            request,
            FinalOutputStatus.STALE_EVIDENCE,
            Step26ReasonCode.ANSWER_STALE_EVIDENCE,
        )
    if evidence is EvidenceStatus.UNAVAILABLE:
        return _decision(
            request,
            FinalOutputStatus.UNAVAILABLE_EVIDENCE,
            Step26ReasonCode.ANSWER_UNAVAILABLE_EVIDENCE,
        )
    if evidence is EvidenceStatus.INVALID:
        return _decision(
            request,
            FinalOutputStatus.INTEGRITY_FAILURE,
            Step26ReasonCode.INTEGRITY_FAILURE,
        )
    if evidence is not EvidenceStatus.SUFFICIENT:
        return _decision(
            request,
            FinalOutputStatus.INTEGRITY_FAILURE,
            Step26ReasonCode.INTEGRITY_FAILURE,
        )

    assert request.step25_result is not None
    summary = request.step25_result.verification_summary
    fully_verified = (
        summary.summary_status is VerificationSummaryStatus.VERIFIED
        and summary.required_corrections_unsatisfied == 0
        and summary.prohibited_claim_violations == 0
        and summary.citation_failures == 0
        and summary.verified_refuted_count == 0
        and summary.unverified_count == 0
        and summary.conflicting_count == 0
        and summary.invalid_count == 0
        and summary.verified_supported_count == summary.claim_count
    )
    if fully_verified:
        return _decision(
            request,
            FinalOutputStatus.VERIFIED_ANSWER,
            Step26ReasonCode.ANSWER_VERIFIED,
        )

    if summary.conflicting_count or summary.summary_status is VerificationSummaryStatus.CONFLICTING:
        return _decision(
            request,
            FinalOutputStatus.HUMAN_REVIEW_REQUIRED,
            Step26ReasonCode.DRAFT_V2_VERIFICATION_FAILED,
            Step26ReasonCode.ANSWER_CONFLICTING_EVIDENCE,
            Step26ReasonCode.HUMAN_REVIEW_REQUIRED,
        )

    retryable = (
        summary.invalid_count == 0
        and summary.verified_refuted_count == 0
        and bool(
            summary.required_corrections_unsatisfied
            or summary.prohibited_claim_violations
            or summary.citation_failures
            or summary.unverified_count
        )
    )
    failure_reasons = [Step26ReasonCode.DRAFT_V2_VERIFICATION_FAILED]
    if summary.required_corrections_unsatisfied:
        failure_reasons.append(Step26ReasonCode.REQUIRED_CORRECTION_UNSATISFIED)
    if summary.prohibited_claim_violations:
        failure_reasons.append(Step26ReasonCode.PROHIBITED_CLAIM_VIOLATION)
    if summary.citation_failures:
        failure_reasons.append(Step26ReasonCode.INVALID_CITATION)
    if summary.unverified_count:
        failure_reasons.append(Step26ReasonCode.UNVERIFIED_CLAIM_REMAINS)
    if retryable:
        failure_reasons.extend(
            (
                Step26ReasonCode.FINAL_RETRY_REQUIRED,
                Step26ReasonCode.RETRY_SAME_PACKET,
                Step26ReasonCode.RETRY_NEW_EVIDENCE_FORBIDDEN,
            )
        )
        return _decision(
            request,
            FinalOutputStatus.RETRY_REQUIRED,
            *failure_reasons,
            retry=True,
        )
    return _decision(
        request,
        FinalOutputStatus.HUMAN_REVIEW_REQUIRED,
        *failure_reasons,
        Step26ReasonCode.HUMAN_REVIEW_REQUIRED,
    )


def verify_final_eligibility_decision_hash(value: FinalEligibilityDecision) -> None:
    verify_canonical_hash(value, value.decision_hash, exclude_fields=("decision_hash",))


__all__ = [
    "FinalEligibilityDecision",
    "evaluate_final_eligibility",
    "verify_final_eligibility_decision_hash",
]
