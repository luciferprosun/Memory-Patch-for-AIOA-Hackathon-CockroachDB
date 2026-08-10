"""Typed Step 26/32 adapters into Step 34 review cases."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

from aioa_memory_kernel.answers.models import (
    BoundedAnswerFailure,
    FinalFailureClass,
    FinalOutputStatus,
    HumanReviewRequired,
    verify_bounded_failure_hash,
    verify_human_review_hash,
)
from aioa_memory_kernel.audit_ledger.models import AuditChainVerificationResult
from aioa_memory_kernel.contracts.enums import EvidenceStatus
from aioa_memory_kernel.contracts.exceptions import ContractValidationError, IntegrityError
from aioa_memory_kernel.contracts.serialization import (
    canonical_sha256,
    verify_canonical_hash,
)
from aioa_memory_kernel.personal_memory.lifecycle32 import (
    DeidentificationDecision,
    SharedMemoryPromotionProposal,
    verify_shared_memory_promotion_proposal,
)
from aioa_memory_kernel.personal_memory.retrieval import (
    CanonicalEvidenceCompatibility,
)

from .models import (
    STEP34_SCHEMA_VERSION,
    HumanReviewCase,
    ReviewCaseType,
    ReviewPriority,
    ReviewReasonCode,
    ReviewSourceContext,
    ReviewSourceContract,
    ReviewSubjectType,
    build_open_review_case,
)


def _verify_audit_result(value: AuditChainVerificationResult) -> None:
    if not isinstance(value, AuditChainVerificationResult):
        raise TypeError("audit_verification must be AuditChainVerificationResult")
    verify_canonical_hash(value, value.result_hash, exclude_fields=("result_hash",))
    rebuilt = AuditChainVerificationResult(
        chain_id=value.chain_id,
        event_count=value.event_count,
        first_sequence=value.first_sequence,
        last_sequence=value.last_sequence,
        first_hash=value.first_hash,
        last_hash=value.last_hash,
        verified=value.verified,
        failure_sequence=value.failure_sequence,
        failure_reason_codes=value.failure_reason_codes,
        verification_policy_id=value.verification_policy_id,
        verification_policy_version=value.verification_policy_version,
        verification_policy_digest=value.verification_policy_digest,
    )
    if rebuilt != value:
        raise IntegrityError("audit verification result contains detached fields")


def _audit_values(
    verification: AuditChainVerificationResult,
    source_audit_event_hash: str,
) -> tuple[str, str, bool]:
    _verify_audit_result(verification)
    if verification.event_count < 1 or verification.last_hash != source_audit_event_hash:
        raise ContractValidationError(
            "review source audit event must be the verification range tail"
        )
    return verification.chain_id, verification.result_hash, verification.verified


def _answer_case_type(
    *,
    output_status: FinalOutputStatus,
    evidence_status: EvidenceStatus,
) -> ReviewCaseType:
    if output_status is FinalOutputStatus.CONFIRMATION_REQUIRED:
        return ReviewCaseType.ANSWER_CONFIRMATION_REQUIRED
    return {
        EvidenceStatus.CONFLICTING: ReviewCaseType.ANSWER_CONFLICTING_EVIDENCE,
        EvidenceStatus.INSUFFICIENT: ReviewCaseType.ANSWER_INSUFFICIENT_EVIDENCE,
        EvidenceStatus.STALE: ReviewCaseType.ANSWER_STALE_EVIDENCE,
    }.get(evidence_status, ReviewCaseType.ANSWER_VERIFICATION_FAILURE)


def _case_reasons(audit_verified: bool) -> tuple[ReviewReasonCode, ...]:
    return tuple(
        sorted(
            {
                ReviewReasonCode.REVIEW_CASE_CREATED,
                (
                    ReviewReasonCode.REVIEW_AUDIT_CONTEXT_VERIFIED
                    if audit_verified
                    else ReviewReasonCode.REVIEW_AUDIT_CONTEXT_INVALID
                ),
            },
            key=lambda item: item.value,
        )
    )


def human_review_required_case(
    source: HumanReviewRequired,
    *,
    source_audit_event_hash: str,
    audit_verification: AuditChainVerificationResult,
    created_at: datetime,
) -> tuple[HumanReviewCase, ReviewSourceContext]:
    if not isinstance(source, HumanReviewRequired):
        raise TypeError("source must be HumanReviewRequired")
    verify_human_review_hash(source)
    chain_id, verification_hash, audit_verified = _audit_values(
        audit_verification, source_audit_event_hash
    )
    context_refs: dict[str, str] = {
        "human_review_result_hash": source.result_hash,
    }
    for name in (
        "draft_v1_hash",
        "correction_packet_hash",
        "verification_summary_hash",
        "selected_manifest_digest",
    ):
        value = getattr(source, name)
        if value is not None:
            context_refs[name] = value
    for index, value in enumerate(source.draft_v2_hashes):
        context_refs[f"draft_v2_hash_{index + 1}"] = value
    for index, value in enumerate(source.review_payload_hashes):
        context_refs[f"review_payload_hash_{index + 1}"] = value
    context = ReviewSourceContext(
        schema_version=STEP34_SCHEMA_VERSION,
        source_contract=ReviewSourceContract.STEP26_HUMAN_REVIEW_REQUIRED,
        subject_hash=source.result_hash,
        context_payload={
            "output_status": source.output_status.value,
            "evidence_status": source.evidence_status.value,
            "knowledge_policy_decision": source.knowledge_policy_decision.value,
            "selected_hat_id": source.selected_hat_id,
            "selected_hat_version": source.selected_hat_version,
            "business_reason_codes": [item.value for item in source.reason_codes],
            "hat_enforce_known_bad_draft_v1_fallback": False,
            "answer_returned": False,
        },
        contains_raw_private_memory=False,
        canonical_evidence=False,
        model_authority=False,
    )
    case_type = _answer_case_type(
        output_status=source.output_status,
        evidence_status=source.evidence_status,
    )
    case = build_open_review_case(
        case_type=case_type,
        tenant_id=source.tenant_id,
        owner_user_id=source.user_id,
        subject_type=ReviewSubjectType.ANSWER_REVIEW_RESULT,
        subject_id=f"step26-human-review-{source.result_hash}",
        subject_hash=source.result_hash,
        request_id=source.request_id,
        kernel_run_id=None,
        route_hash=source.route_hash,
        review_reason_codes=_case_reasons(audit_verified),
        priority=(
            ReviewPriority.CRITICAL
            if case_type is ReviewCaseType.ANSWER_CONFLICTING_EVIDENCE
            else ReviewPriority.HIGH
        ),
        source_audit_event_hash=source_audit_event_hash,
        source_chain_id=chain_id,
        audit_verification_result_hash=verification_hash,
        audit_context_verified=audit_verified,
        required_context_refs=context_refs,
        source_context_hash=context.context_hash,
        created_at=created_at,
    )
    return case, context


def bounded_answer_failure_case(
    source: BoundedAnswerFailure,
    *,
    source_audit_event_hash: str,
    audit_verification: AuditChainVerificationResult,
    created_at: datetime,
) -> tuple[HumanReviewCase, ReviewSourceContext]:
    if not isinstance(source, BoundedAnswerFailure):
        raise TypeError("source must be BoundedAnswerFailure")
    verify_bounded_failure_hash(source)
    allowed = {
        FinalFailureClass.CONFLICTING_EVIDENCE,
        FinalFailureClass.INSUFFICIENT_EVIDENCE,
        FinalFailureClass.STALE_EVIDENCE,
        FinalFailureClass.VERIFICATION_FAILED,
    }
    if source.failure_class not in allowed:
        raise ContractValidationError(
            "bounded failure is not routed to the Step 34 review policy"
        )
    chain_id, verification_hash, audit_verified = _audit_values(
        audit_verification, source_audit_event_hash
    )
    refs: dict[str, str] = {"bounded_failure_hash": source.failure_hash}
    if source.verification_summary_hash is not None:
        refs["verification_summary_hash"] = source.verification_summary_hash
    context = ReviewSourceContext(
        schema_version=STEP34_SCHEMA_VERSION,
        source_contract=ReviewSourceContract.STEP26_BOUNDED_ANSWER_FAILURE,
        subject_hash=source.failure_hash,
        context_payload={
            "failure_class": source.failure_class.value,
            "output_status": source.output_status.value,
            "answer_status": source.answer_status.value,
            "evidence_status": source.evidence_status.value,
            "knowledge_policy_decision": source.knowledge_policy_decision.value,
            "business_reason_codes": [item.value for item in source.reason_codes],
            "safe_message_digest": canonical_sha256(source.safe_message),
            "answer_returned": False,
        },
        contains_raw_private_memory=False,
        canonical_evidence=False,
        model_authority=False,
    )
    case_type = _answer_case_type(
        output_status=source.output_status,
        evidence_status=source.evidence_status,
    )
    case = build_open_review_case(
        case_type=case_type,
        tenant_id=source.tenant_id,
        owner_user_id=source.user_id,
        subject_type=ReviewSubjectType.ANSWER_REVIEW_RESULT,
        subject_id=f"step26-bounded-failure-{source.failure_hash}",
        subject_hash=source.failure_hash,
        request_id=source.request_id,
        kernel_run_id=None,
        route_hash=source.route_hash,
        review_reason_codes=_case_reasons(audit_verified),
        priority=(
            ReviewPriority.CRITICAL
            if case_type is ReviewCaseType.ANSWER_CONFLICTING_EVIDENCE
            else ReviewPriority.HIGH
        ),
        source_audit_event_hash=source_audit_event_hash,
        source_chain_id=chain_id,
        audit_verification_result_hash=verification_hash,
        audit_context_verified=audit_verified,
        required_context_refs=refs,
        source_context_hash=context.context_hash,
        created_at=created_at,
    )
    return case, context


def shared_promotion_review_case(
    source: SharedMemoryPromotionProposal,
    *,
    source_audit_event_hash: str,
    audit_verification: AuditChainVerificationResult,
    created_at: datetime,
) -> tuple[HumanReviewCase, ReviewSourceContext]:
    if not isinstance(source, SharedMemoryPromotionProposal):
        raise TypeError("source must be SharedMemoryPromotionProposal")
    verify_shared_memory_promotion_proposal(source)
    chain_id, verification_hash, audit_verified = _audit_values(
        audit_verification, source_audit_event_hash
    )
    if source.canonical_evidence_compatibility is CanonicalEvidenceCompatibility.CONFLICT:
        case_type = ReviewCaseType.SHARED_PROMOTION_CANONICAL_CONFLICT
        priority = ReviewPriority.CRITICAL
    elif source.deidentification.decision is DeidentificationDecision.REVIEW_REQUIRED:
        case_type = ReviewCaseType.SHARED_PROMOTION_PRIVACY_REVIEW
        priority = ReviewPriority.HIGH
    else:
        case_type = ReviewCaseType.SHARED_MEMORY_PROMOTION
        priority = ReviewPriority.NORMAL
    refs: Mapping[str, str] = {
        "promotion_proposal_hash": source.proposal_hash,
        "source_patch_hash": source.source_patch_hash,
        "source_state_hash": source.source_state_hash,
        "candidate_shared_statement_sha256": source.candidate_shared_statement_sha256,
        "deidentification_assessment_hash": source.deidentification.assessment_hash,
        "deidentification_policy_digest": source.deidentification.policy_digest,
        "owner_consent_hash": source.owner_consent_hash,
    }
    context = ReviewSourceContext(
        schema_version=STEP34_SCHEMA_VERSION,
        source_contract=ReviewSourceContract.STEP32_SHARED_MEMORY_PROMOTION_PROPOSAL,
        subject_hash=source.proposal_hash,
        context_payload={
            "candidate_shared_statement": source.candidate_shared_statement,
            "candidate_shared_statement_sha256": source.candidate_shared_statement_sha256,
            "promotion_purpose": source.promotion_purpose,
            "target_hat_id": source.target_hat_id,
            "deidentification_decision": source.deidentification.decision.value,
            "deidentification_status": source.deidentification.status.value,
            "detected_private_categories": list(
                source.deidentification.detected_categories
            ),
            "canonical_evidence_compatibility": (
                source.canonical_evidence_compatibility.value
            ),
            "owner_consent_present": True,
            "review_required": True,
            "shared_active": False,
            "source_registry_published": False,
        },
        contains_raw_private_memory=False,
        canonical_evidence=False,
        model_authority=False,
    )
    case = build_open_review_case(
        case_type=case_type,
        tenant_id=source.tenant_id,
        owner_user_id=source.owner_user_id,
        subject_type=ReviewSubjectType.SHARED_MEMORY_PROMOTION_PROPOSAL,
        subject_id=source.promotion_id,
        subject_hash=source.proposal_hash,
        request_id=None,
        kernel_run_id=None,
        route_hash=None,
        review_reason_codes=_case_reasons(audit_verified),
        priority=priority,
        source_audit_event_hash=source_audit_event_hash,
        source_chain_id=chain_id,
        audit_verification_result_hash=verification_hash,
        audit_context_verified=audit_verified,
        required_context_refs=refs,
        source_context_hash=context.context_hash,
        created_at=created_at,
    )
    return case, context


__all__ = [
    "bounded_answer_failure_case",
    "human_review_required_case",
    "shared_promotion_review_case",
]
