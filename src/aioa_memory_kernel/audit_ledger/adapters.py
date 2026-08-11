"""Hash-only adapters from existing immutable business facts to audit drafts."""

from __future__ import annotations

from datetime import datetime

from aioa_memory_kernel.answers.models import BoundedAnswerFailure, VerifiedAnswer
from aioa_memory_kernel.personal_memory.lifecycle import (
    PersonalMemoryActivationReceipt,
    PersonalMemoryApprovalReceipt,
    PersonalMemoryCommitReceipt,
)
from aioa_memory_kernel.personal_memory.lifecycle32 import (
    PersonalMemoryDeletionResult,
    PersonalMemoryLifecycleExportBundle,
    PersonalMemoryPatchRevocation,
    PersonalMemoryPatchSupersession,
    SharedMemoryPromotionProposal,
    Step32ActorType,
)

from .models import (
    AuditActorType,
    AuditEventDraft,
    AuditEventType,
    AuditReasonCode,
    AuditSubjectType,
)


_APPENDED = (AuditReasonCode.AUDIT_EVENT_APPENDED,)


def _stable_recorded_at(
    recorded_at: datetime | None,
    *,
    occurred_at: datetime,
) -> datetime:
    """Use the immutable business timestamp when replay supplies no clock value."""

    return occurred_at if recorded_at is None else recorded_at


def approval_receipt_event(
    receipt: PersonalMemoryApprovalReceipt,
    *,
    recorded_at: datetime | None = None,
) -> AuditEventDraft:
    if not isinstance(receipt, PersonalMemoryApprovalReceipt):
        raise TypeError("receipt must be PersonalMemoryApprovalReceipt")
    return AuditEventDraft(
        event_type=AuditEventType.PERSONAL_MEMORY_APPROVED,
        tenant_id=receipt.tenant_id,
        owner_user_id=receipt.owner_user_id,
        personal_memory_space_id=receipt.personal_memory_space_id,
        subject_type=AuditSubjectType.PERSONAL_MEMORY_PROPOSAL,
        subject_id=receipt.proposal_id,
        subject_hash=receipt.proposal_hash,
        actor_type=AuditActorType.HUMAN_USER,
        actor_id=receipt.actor_id,
        idempotency_key=f"audit-approval-{receipt.approval_id}",
        occurred_at=receipt.approved_at,
        recorded_at=_stable_recorded_at(
            recorded_at, occurred_at=receipt.approved_at
        ),
        event_payload={
            "state": "APPROVED",
            "approval_id": receipt.approval_id,
            "business_reason": receipt.reason_code.value,
        },
        reason_codes=_APPENDED,
        policy_id=receipt.approval_policy_id,
        policy_version=receipt.approval_policy_version,
        policy_digest=receipt.approval_policy_digest,
        lineage_hashes={
            "approval_receipt_hash": receipt.receipt_hash,
            "evidence_binding_hash": receipt.evidence_binding_hash,
            "validation_receipt_hash": receipt.validation_receipt_hash,
        },
    )


def commit_receipt_event(
    receipt: PersonalMemoryCommitReceipt,
    *,
    recorded_at: datetime | None = None,
) -> AuditEventDraft:
    if not isinstance(receipt, PersonalMemoryCommitReceipt):
        raise TypeError("receipt must be PersonalMemoryCommitReceipt")
    return AuditEventDraft(
        event_type=AuditEventType.PERSONAL_MEMORY_COMMITTED,
        tenant_id=receipt.tenant_id,
        owner_user_id=receipt.owner_user_id,
        personal_memory_space_id=receipt.personal_memory_space_id,
        subject_type=AuditSubjectType.PERSONAL_MEMORY_PATCH,
        subject_id=receipt.patch_id,
        subject_hash=receipt.patch_hash,
        actor_type=AuditActorType.COMMIT_HELPER,
        actor_id=receipt.actor_id,
        idempotency_key=f"audit-commit-{receipt.commit_id}",
        occurred_at=receipt.committed_at,
        recorded_at=_stable_recorded_at(
            recorded_at, occurred_at=receipt.committed_at
        ),
        event_payload={
            "state": "COMMITTED",
            "commit_id": receipt.commit_id,
            "business_reason": receipt.reason_code.value,
        },
        reason_codes=_APPENDED,
        lineage_hashes={
            "approval_receipt_hash": receipt.approval_receipt_hash,
            "commit_receipt_hash": receipt.receipt_hash,
            "proposal_hash": receipt.proposal_hash,
            "validation_receipt_hash": receipt.validation_receipt_hash,
        },
    )


def activation_receipt_event(
    receipt: PersonalMemoryActivationReceipt,
    *,
    recorded_at: datetime | None = None,
) -> AuditEventDraft:
    if not isinstance(receipt, PersonalMemoryActivationReceipt):
        raise TypeError("receipt must be PersonalMemoryActivationReceipt")
    return AuditEventDraft(
        event_type=AuditEventType.PERSONAL_MEMORY_ACTIVATED,
        tenant_id=receipt.tenant_id,
        owner_user_id=receipt.owner_user_id,
        personal_memory_space_id=receipt.personal_memory_space_id,
        subject_type=AuditSubjectType.PERSONAL_MEMORY_PATCH,
        subject_id=receipt.patch_id,
        subject_hash=receipt.patch_hash,
        actor_type=AuditActorType.ACTIVATION_SERVICE,
        actor_id=receipt.actor_id,
        idempotency_key=f"audit-activation-{receipt.activation_id}",
        occurred_at=receipt.activated_at,
        recorded_at=_stable_recorded_at(
            recorded_at, occurred_at=receipt.activated_at
        ),
        event_payload={
            "state": "ACTIVE",
            "activation_id": receipt.activation_id,
            "business_reason": receipt.reason_code.value,
        },
        reason_codes=_APPENDED,
        lineage_hashes={
            "activation_receipt_hash": receipt.receipt_hash,
            "approval_receipt_hash": receipt.approval_receipt_hash,
            "commit_receipt_hash": receipt.commit_receipt_hash,
            "proposal_hash": receipt.proposal_hash,
        },
    )


def supersession_event(
    record: PersonalMemoryPatchSupersession,
    *,
    recorded_at: datetime | None = None,
) -> AuditEventDraft:
    if not isinstance(record, PersonalMemoryPatchSupersession):
        raise TypeError("record must be PersonalMemoryPatchSupersession")
    return AuditEventDraft(
        event_type=AuditEventType.PERSONAL_MEMORY_SUPERSEDED,
        tenant_id=record.tenant_id,
        owner_user_id=record.owner_user_id,
        personal_memory_space_id=record.personal_memory_space_id,
        subject_type=AuditSubjectType.PERSONAL_MEMORY_PATCH,
        subject_id=record.old_patch_id,
        subject_hash=record.old_patch_hash,
        actor_type=AuditActorType.HUMAN_USER,
        actor_id=record.actor_id,
        idempotency_key=f"audit-supersession-{record.supersession_id}",
        occurred_at=record.effective_at,
        recorded_at=_stable_recorded_at(
            recorded_at, occurred_at=record.effective_at
        ),
        event_payload={
            "state": "SUPERSEDED",
            "new_patch_id": record.new_patch_id,
            "business_reasons": [item.value for item in record.reason_codes],
        },
        reason_codes=_APPENDED,
        lineage_hashes={
            "new_patch_hash": record.new_patch_hash,
            "new_state_hash": record.new_state_hash,
            "old_state_hash": record.old_state_hash,
            "supersession_hash": record.supersession_hash,
        },
    )


def revocation_event(
    record: PersonalMemoryPatchRevocation,
    *,
    recorded_at: datetime | None = None,
) -> AuditEventDraft:
    if not isinstance(record, PersonalMemoryPatchRevocation):
        raise TypeError("record must be PersonalMemoryPatchRevocation")
    actor = (
        AuditActorType.HUMAN_USER
        if record.actor_type is Step32ActorType.HUMAN_OWNER
        else AuditActorType.SYSTEM_POLICY
    )
    return AuditEventDraft(
        event_type=AuditEventType.PERSONAL_MEMORY_REVOKED,
        tenant_id=record.tenant_id,
        owner_user_id=record.owner_user_id,
        personal_memory_space_id=record.personal_memory_space_id,
        subject_type=AuditSubjectType.PERSONAL_MEMORY_PATCH,
        subject_id=record.patch_id,
        subject_hash=record.patch_hash,
        actor_type=actor,
        actor_id=record.actor_id,
        idempotency_key=f"audit-revocation-{record.revocation_id}",
        occurred_at=record.effective_at,
        recorded_at=_stable_recorded_at(
            recorded_at, occurred_at=record.effective_at
        ),
        event_payload={
            "state": "REVOKED",
            "business_reasons": [item.value for item in record.reason_codes],
        },
        reason_codes=_APPENDED,
        lineage_hashes={
            "active_state_hash": record.active_state_hash,
            "revocation_hash": record.revocation_hash,
        },
    )


def deletion_event(
    result: PersonalMemoryDeletionResult,
    *,
    recorded_at: datetime | None = None,
) -> AuditEventDraft:
    if not isinstance(result, PersonalMemoryDeletionResult):
        raise TypeError("result must be PersonalMemoryDeletionResult")
    return AuditEventDraft(
        event_type=AuditEventType.PERSONAL_MEMORY_DELETED,
        tenant_id=result.tenant_id,
        owner_user_id=result.owner_user_id,
        personal_memory_space_id=result.personal_memory_space_id,
        subject_type=AuditSubjectType.PERSONAL_MEMORY_DELETION,
        subject_id=result.deletion_id,
        subject_hash=result.result_hash,
        actor_type=AuditActorType.HUMAN_USER,
        actor_id=result.owner_user_id,
        idempotency_key=f"audit-deletion-{result.deletion_id}",
        occurred_at=result.deleted_at,
        recorded_at=_stable_recorded_at(
            recorded_at, occurred_at=result.deleted_at
        ),
        event_payload={
            "state": "DELETED",
            "logical_delete": True,
            "physical_delete": False,
        },
        reason_codes=_APPENDED,
        lineage_hashes={
            "patch_hash": result.patch_hash,
            "tombstone_hash": result.tombstone_hash,
        },
    )


def lifecycle_export_event(
    bundle: PersonalMemoryLifecycleExportBundle,
    *,
    recorded_at: datetime | None = None,
) -> AuditEventDraft:
    if not isinstance(bundle, PersonalMemoryLifecycleExportBundle):
        raise TypeError("bundle must be PersonalMemoryLifecycleExportBundle")
    return AuditEventDraft(
        event_type=AuditEventType.PERSONAL_MEMORY_EXPORTED,
        tenant_id=bundle.tenant_id,
        owner_user_id=bundle.owner_user_id,
        personal_memory_space_id=bundle.personal_memory_space_id,
        subject_type=AuditSubjectType.PERSONAL_MEMORY_EXPORT,
        subject_id=bundle.export_id,
        subject_hash=bundle.bundle_hash,
        actor_type=AuditActorType.HUMAN_USER,
        actor_id=bundle.owner_user_id,
        idempotency_key=f"audit-export-{bundle.export_id}",
        occurred_at=bundle.exported_at,
        recorded_at=_stable_recorded_at(
            recorded_at, occurred_at=bundle.exported_at
        ),
        event_payload={
            "record_count": len(bundle.records),
            "owner_private": True,
            "shared_promotion": False,
        },
        reason_codes=_APPENDED,
        lineage_hashes={
            "export_bundle_hash": bundle.bundle_hash,
            "export_request_hash": bundle.request_hash,
            "slot_hash": bundle.slot_hash,
        },
    )


def shared_promotion_event(
    proposal: SharedMemoryPromotionProposal,
    *,
    recorded_at: datetime | None = None,
) -> AuditEventDraft:
    if not isinstance(proposal, SharedMemoryPromotionProposal):
        raise TypeError("proposal must be SharedMemoryPromotionProposal")
    return AuditEventDraft(
        event_type=AuditEventType.SHARED_PROMOTION_PROPOSED,
        tenant_id=proposal.tenant_id,
        owner_user_id=proposal.owner_user_id,
        personal_memory_space_id=proposal.personal_memory_space_id,
        subject_type=AuditSubjectType.SHARED_PROMOTION_PROPOSAL,
        subject_id=proposal.promotion_id,
        subject_hash=proposal.proposal_hash,
        actor_type=AuditActorType.HUMAN_USER,
        actor_id=proposal.owner_user_id,
        idempotency_key=f"audit-promotion-{proposal.promotion_id}",
        occurred_at=proposal.created_at,
        recorded_at=_stable_recorded_at(
            recorded_at, occurred_at=proposal.created_at
        ),
        event_payload={
            "state": "SHARED_PROMOTION_PROPOSED",
            "review_required": True,
            "shared_active": False,
            "source_registry_published": False,
        },
        reason_codes=_APPENDED,
        policy_id=proposal.deidentification.policy_id,
        policy_version=proposal.deidentification.policy_version,
        policy_digest=proposal.deidentification.policy_digest,
        lineage_hashes={
            "deidentification_assessment_hash": (
                proposal.deidentification.assessment_hash
            ),
            "owner_consent_hash": proposal.owner_consent_hash,
            "source_patch_hash": proposal.source_patch_hash,
        },
    )


def verified_answer_event(
    answer: VerifiedAnswer,
    *,
    recorded_at: datetime,
) -> AuditEventDraft:
    if not isinstance(answer, VerifiedAnswer):
        raise TypeError("answer must be VerifiedAnswer")
    return AuditEventDraft(
        event_type=AuditEventType.VERIFIED_ANSWER_ASSEMBLED,
        tenant_id=answer.tenant_id,
        owner_user_id=answer.user_id,
        request_id=answer.request_id,
        subject_type=AuditSubjectType.VERIFIED_ANSWER,
        subject_id=answer.answer_id,
        subject_hash=answer.answer_hash,
        actor_type=AuditActorType.KERNEL,
        actor_id="aioa-memory-kernel",
        idempotency_key=f"audit-verified-answer-{answer.answer_id}",
        occurred_at=recorded_at,
        recorded_at=recorded_at,
        event_payload={
            "output_status": answer.output_status.value,
            "content_sha256": answer.answer_text_sha256,
            "content_byte_length": answer.answer_byte_length,
        },
        reason_codes=_APPENDED,
        policy_id="step26-final-answer-policy",
        policy_version="1a",
        policy_digest=answer.final_policy_digest,
        route_hash=answer.route_hash,
        lineage_hashes={
            "correction_packet_hash": answer.correction_packet_hash,
            "evidence_bundle_hash": answer.evidence_bundle_hash,
            "temporal_resolution_hash": answer.temporal_resolution_hash,
            "verification_summary_hash": answer.verification_summary_hash,
        },
    )


def bounded_answer_failure_event(
    failure: BoundedAnswerFailure,
    *,
    recorded_at: datetime,
) -> AuditEventDraft:
    if not isinstance(failure, BoundedAnswerFailure):
        raise TypeError("failure must be BoundedAnswerFailure")
    lineage = {}
    if failure.verification_summary_hash is not None:
        lineage["verification_summary_hash"] = failure.verification_summary_hash
    return AuditEventDraft(
        event_type=AuditEventType.VERIFIED_ANSWER_BLOCKED,
        tenant_id=failure.tenant_id,
        owner_user_id=failure.user_id,
        request_id=failure.request_id,
        subject_type=AuditSubjectType.VERIFIED_ANSWER,
        subject_id=f"blocked-answer-{failure.failure_hash}",
        subject_hash=failure.failure_hash,
        actor_type=AuditActorType.KERNEL,
        actor_id="aioa-memory-kernel",
        idempotency_key=f"audit-blocked-answer-{failure.failure_hash}",
        occurred_at=recorded_at,
        recorded_at=recorded_at,
        event_payload={
            "answer_status": failure.answer_status.value,
            "evidence_status": failure.evidence_status.value,
            "failure_class": failure.failure_class.value,
            "output_status": failure.output_status.value,
            "business_reasons": [item.value for item in failure.reason_codes],
        },
        reason_codes=_APPENDED,
        route_hash=failure.route_hash,
        lineage_hashes=lineage,
    )


__all__ = [
    "activation_receipt_event",
    "approval_receipt_event",
    "bounded_answer_failure_event",
    "commit_receipt_event",
    "deletion_event",
    "lifecycle_export_event",
    "revocation_event",
    "shared_promotion_event",
    "supersession_event",
    "verified_answer_event",
]
