"""Hash-only Step 34 adapters into the Step 33 audit chain."""

from __future__ import annotations

from datetime import datetime

from aioa_memory_kernel.audit_ledger import (
    AuditActorType,
    AuditEventDraft,
    AuditEventType,
    AuditReasonCode,
    AuditSubjectType,
)

from .models import (
    ClaimReviewCaseRequest,
    HumanReviewCase,
    HumanReviewDecision,
    ReviewBusinessHandoffResult,
    ReviewReasonCode,
    ReviewerPrincipal,
    verify_human_review_case,
    verify_human_review_decision,
    verify_reviewer_principal,
    verify_review_handoff_result,
)


_APPENDED = (AuditReasonCode.AUDIT_EVENT_APPENDED,)
_ACCESS_POLICY_ID = "human-review-access-policy-1a"
_ACCESS_POLICY_VERSION = "1"


def review_case_created_event(
    case: HumanReviewCase,
    *,
    access_policy_digest: str,
    recorded_at: datetime,
) -> AuditEventDraft:
    verify_human_review_case(case)
    return AuditEventDraft(
        event_type=AuditEventType.REVIEW_CASE_CREATED,
        tenant_id=case.tenant_id,
        owner_user_id=case.owner_user_id,
        request_id=case.request_id,
        kernel_run_id=case.kernel_run_id,
        subject_type=AuditSubjectType.REVIEW_CASE,
        subject_id=case.review_case_id,
        subject_hash=case.case_hash,
        actor_type=AuditActorType.KERNEL,
        actor_id="aioa-memory-kernel",
        idempotency_key=f"audit-review-case-{case.review_case_id}",
        occurred_at=case.created_at,
        recorded_at=recorded_at,
        event_payload={
            "case_type": case.case_type.value,
            "review_state": case.review_state.value,
            "priority": case.priority.value,
            "audit_context_verified": case.audit_context_verified,
            "business_authority": False,
        },
        reason_codes=_APPENDED,
        policy_id=_ACCESS_POLICY_ID,
        policy_version=_ACCESS_POLICY_VERSION,
        policy_digest=access_policy_digest,
        route_hash=case.route_hash,
        lineage_hashes={
            "audit_verification_result_hash": case.audit_verification_result_hash,
            "source_audit_event_hash": case.source_audit_event_hash,
            "source_context_hash": case.source_context_hash,
            "subject_hash": case.subject_hash,
            "trigger_hash": case.trigger_hash,
        },
    )


def review_case_claimed_event(
    claimed_case: HumanReviewCase,
    principal: ReviewerPrincipal,
    request: ClaimReviewCaseRequest,
    *,
    access_policy_digest: str,
    recorded_at: datetime,
) -> AuditEventDraft:
    verify_human_review_case(claimed_case)
    verify_reviewer_principal(principal)
    return AuditEventDraft(
        event_type=AuditEventType.REVIEW_CASE_CLAIMED,
        tenant_id=claimed_case.tenant_id,
        owner_user_id=claimed_case.owner_user_id,
        request_id=claimed_case.request_id,
        kernel_run_id=claimed_case.kernel_run_id,
        subject_type=AuditSubjectType.REVIEW_CASE,
        subject_id=claimed_case.review_case_id,
        subject_hash=claimed_case.case_hash,
        actor_type=AuditActorType.HUMAN_REVIEWER,
        actor_id=principal.reviewer_id,
        idempotency_key=f"audit-review-claim-{request.replay_identity}",
        occurred_at=request.requested_at,
        recorded_at=recorded_at,
        event_payload={
            "case_type": claimed_case.case_type.value,
            "review_state": claimed_case.review_state.value,
            "review_state_version": claimed_case.review_state_version,
            "reviewer_role": principal.reviewer_role.value,
            "business_subject_mutated": False,
        },
        reason_codes=_APPENDED,
        policy_id=_ACCESS_POLICY_ID,
        policy_version=_ACCESS_POLICY_VERSION,
        policy_digest=access_policy_digest,
        route_hash=claimed_case.route_hash,
        lineage_hashes={
            "claim_request_hash": request.request_hash,
            "previous_case_hash": request.review_case_hash,
            "reviewer_principal_hash": principal.principal_hash,
            "subject_hash": claimed_case.subject_hash,
        },
    )


def review_decision_recorded_event(
    decision: HumanReviewDecision,
    in_review_case: HumanReviewCase,
    *,
    recorded_at: datetime,
) -> AuditEventDraft:
    verify_human_review_decision(decision)
    verify_human_review_case(in_review_case)
    return AuditEventDraft(
        event_type=AuditEventType.REVIEW_DECISION_RECORDED,
        tenant_id=decision.tenant_id,
        owner_user_id=decision.owner_user_id,
        request_id=in_review_case.request_id,
        kernel_run_id=in_review_case.kernel_run_id,
        subject_type=AuditSubjectType.REVIEW_CASE,
        subject_id=in_review_case.review_case_id,
        subject_hash=in_review_case.case_hash,
        actor_type=AuditActorType.HUMAN_REVIEWER,
        actor_id=decision.reviewer_id,
        idempotency_key=f"audit-review-decision-{decision.replay_identity}",
        occurred_at=decision.decided_at,
        recorded_at=recorded_at,
        event_payload={
            "case_type": decision.case_type.value,
            "decision_type": decision.decision_type.value,
            "review_state": in_review_case.review_state.value,
            "reviewer_role": decision.reviewer_role.value,
            "reviewer_note_is_evidence": False,
            "business_mutation": False,
        },
        reason_codes=_APPENDED,
        policy_id=decision.decision_policy_id,
        policy_version=decision.decision_policy_version,
        policy_digest=decision.decision_policy_digest,
        route_hash=in_review_case.route_hash,
        lineage_hashes={
            "audit_verification_result_hash": decision.audit_verification_result_hash,
            "decision_hash": decision.decision_hash,
            "previous_case_hash": decision.review_case_hash,
            "subject_hash": decision.subject_hash,
        },
    )


def review_handoff_event(
    result: ReviewBusinessHandoffResult,
    decision: HumanReviewDecision,
    case: HumanReviewCase,
    *,
    recorded_at: datetime,
) -> AuditEventDraft:
    verify_review_handoff_result(result)
    verify_human_review_decision(decision)
    verify_human_review_case(case)
    return AuditEventDraft(
        event_type=AuditEventType.REVIEW_HANDOFF_SUCCEEDED,
        tenant_id=decision.tenant_id,
        owner_user_id=decision.owner_user_id,
        request_id=case.request_id,
        kernel_run_id=case.kernel_run_id,
        subject_type=AuditSubjectType.REVIEW_CASE,
        subject_id=case.review_case_id,
        subject_hash=case.case_hash,
        actor_type=AuditActorType.REVIEW_SERVICE,
        actor_id="human-review-handoff-service-1a",
        idempotency_key=f"audit-review-handoff-{result.result_hash}",
        occurred_at=recorded_at,
        recorded_at=recorded_at,
        event_payload={
            "case_type": case.case_type.value,
            "decision_type": decision.decision_type.value,
            "handoff_status": result.status.value,
            "handoff_target": result.target.value,
            "answer_returned": False,
            "source_registry_published": False,
            "external_execution_authority": False,
        },
        reason_codes=_APPENDED,
        policy_id=decision.decision_policy_id,
        policy_version=decision.decision_policy_version,
        policy_digest=decision.decision_policy_digest,
        route_hash=case.route_hash,
        lineage_hashes={
            "decision_hash": decision.decision_hash,
            "handoff_result_hash": result.result_hash,
            "subject_hash": decision.subject_hash,
        },
    )


def review_case_terminal_event(
    terminal_case: HumanReviewCase,
    decision: HumanReviewDecision,
    handoff_result: ReviewBusinessHandoffResult,
    *,
    recorded_at: datetime,
) -> AuditEventDraft:
    verify_human_review_case(terminal_case)
    verify_human_review_decision(decision)
    verify_review_handoff_result(handoff_result)
    event_type = (
        AuditEventType.REVIEW_CASE_ESCALATED
        if terminal_case.review_state.value == "ESCALATED"
        else AuditEventType.REVIEW_CASE_RESOLVED
    )
    business_reason = (
        ReviewReasonCode.REVIEW_ESCALATED.value
        if event_type is AuditEventType.REVIEW_CASE_ESCALATED
        else ReviewReasonCode.REVIEW_RESOLVED.value
    )
    return AuditEventDraft(
        event_type=event_type,
        tenant_id=terminal_case.tenant_id,
        owner_user_id=terminal_case.owner_user_id,
        request_id=terminal_case.request_id,
        kernel_run_id=terminal_case.kernel_run_id,
        subject_type=AuditSubjectType.REVIEW_CASE,
        subject_id=terminal_case.review_case_id,
        subject_hash=terminal_case.case_hash,
        actor_type=AuditActorType.REVIEW_SERVICE,
        actor_id="human-review-handoff-service-1a",
        idempotency_key=f"audit-review-terminal-{handoff_result.result_hash}",
        occurred_at=recorded_at,
        recorded_at=recorded_at,
        event_payload={
            "case_type": terminal_case.case_type.value,
            "review_state": terminal_case.review_state.value,
            "review_state_version": terminal_case.review_state_version,
            "business_reason": business_reason,
            "business_authority": False,
        },
        reason_codes=_APPENDED,
        policy_id=decision.decision_policy_id,
        policy_version=decision.decision_policy_version,
        policy_digest=decision.decision_policy_digest,
        route_hash=terminal_case.route_hash,
        lineage_hashes={
            "decision_hash": decision.decision_hash,
            "handoff_result_hash": handoff_result.result_hash,
            "subject_hash": terminal_case.subject_hash,
        },
    )


__all__ = [name for name in globals() if name.endswith("_event")]
