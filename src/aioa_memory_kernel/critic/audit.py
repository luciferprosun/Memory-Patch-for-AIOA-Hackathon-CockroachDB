"""Hash-only Step 33 audit adapter for an accepted Step 39 candidate.

The adapter records the one durable fact that the Critic is allowed to
produce: an owner-private Step 28 ``DETECTED`` correction candidate.  It never
serializes the Critic prompt, provider output, evidence snippets, claim text,
or proposed correction, and it grants no later Personal Memory authority.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from aioa_memory_kernel.audit_ledger import (
    AuditActorType,
    AuditEventDraft,
    AuditEventType,
    AuditReasonCode,
    AuditSubjectType,
)
from aioa_memory_kernel.contracts.enums import ActorType, CorrectionCandidateState
from aioa_memory_kernel.contracts.exceptions import IntegrityError
from aioa_memory_kernel.personal_memory.candidates import (
    CorrectionCandidateEnvelope,
    CorrectionCandidateIntakeDisposition,
    CorrectionCandidateIntakeReceipt,
    CorrectionCandidateReasonCode,
    CorrectionCandidateTrigger,
    build_correction_candidate_intake_receipt,
    verify_correction_candidate_envelope,
    verify_correction_candidate_intake_receipt,
)

from .models import (
    CriticBridgeResult,
    CriticBridgeStatus,
    CriticCandidateMappingResult,
    CriticCandidateMappingStatus,
    CriticReviewRequest,
    verify_critic_assessment_against_request,
    verify_critic_bridge_result,
    verify_critic_candidate_mapping_result,
    verify_critic_review_request,
)
from .parser import build_critic_provider_request

if TYPE_CHECKING:
    from .bridge import CriticTrustedCandidateContext


CRITIC_AUDIT_ACTOR_ID = "aoia-critic-prompt-loop-bridge-1a"


def _verify_candidate_lineage(
    request: CriticReviewRequest,
    bridge_result: CriticBridgeResult,
    stored_envelope: CorrectionCandidateEnvelope,
    mapping: CriticCandidateMappingResult,
    intake_receipt: CorrectionCandidateIntakeReceipt,
) -> CorrectionCandidateIntakeReceipt:
    verify_critic_review_request(request)
    verify_critic_bridge_result(bridge_result)
    if (
        bridge_result.status is not CriticBridgeStatus.ASSESSMENT_ACCEPTED
        or bridge_result.assessment is None
        or bridge_result.critic_request_hash != request.request_hash
    ):
        raise IntegrityError("Critic audit requires an accepted parsed bridge result")
    assessment = bridge_result.assessment
    verify_critic_assessment_against_request(assessment, request)
    if (
        bridge_result.provider_call_receipt.provider_request_hash
        != build_critic_provider_request(request).request_hash
    ):
        raise IntegrityError("Critic audit provider request lineage is detached")
    verify_correction_candidate_envelope(stored_envelope)
    verify_correction_candidate_intake_receipt(intake_receipt)
    verify_critic_candidate_mapping_result(mapping)

    submission = stored_envelope.submission
    candidate = submission.candidate
    lineage = submission.lineage
    metadata = submission.metadata
    draft_v1_hashes = tuple(
        item.artifact_hash
        for item in request.artifacts
        if item.artifact_kind.value == "DRAFT_V1"
    )
    expected_claim_ids = tuple(item.claim_id for item in candidate.detected_claims)

    if (
        mapping.status is not CriticCandidateMappingStatus.CANDIDATE_READY
        or intake_receipt.disposition
        not in {
            CorrectionCandidateIntakeDisposition.ACCEPTED,
            CorrectionCandidateIntakeDisposition.EXACT_REPLAY,
        }
        or mapping.critic_request_hash != request.request_hash
        or mapping.assessment_hash != assessment.assessment_hash
        or mapping.candidate_content_hash != candidate.content_hash
        or mapping.candidate_envelope_hash != stored_envelope.envelope_hash
        or intake_receipt.submission_id != submission.submission_id
        or intake_receipt.submission_hash != submission.submission_hash
        or intake_receipt.envelope_id != stored_envelope.envelope_id
        or intake_receipt.envelope_hash != stored_envelope.envelope_hash
        or intake_receipt.candidate_id != stored_envelope.candidate_id
        or intake_receipt.candidate_event_id != candidate.event_id
        or intake_receipt.candidate_content_hash != candidate.content_hash
        or intake_receipt.semantic_deduplication_key
        != submission.semantic_deduplication_key
        or intake_receipt.tenant_id != candidate.tenant_id
        or intake_receipt.owner_user_id != candidate.user_id
        or intake_receipt.personal_memory_space_id
        != candidate.personal_memory_space_id
        or intake_receipt.idempotency_key != submission.idempotency_key
        or candidate.source_component is not ActorType.CRITIC_PROMPT_LOOP
        or candidate.state is not CorrectionCandidateState.DETECTED
        or metadata.trigger is not CorrectionCandidateTrigger.CRITIC_PROMPT_LOOP_DETECTED
        or metadata.reason_codes
        != (CorrectionCandidateReasonCode.SOURCE_CRITIC_PROMPT_LOOP,)
        or request.owner_user_id is None
        or request.tenant_id != candidate.tenant_id
        or request.owner_user_id != candidate.user_id
        or request.kernel_run_id != candidate.run_id
        or request.kernel_run_id != submission.run_identity.kernel_run_id
        or request.request_id != lineage.request_id
        or request.original_query_digest != lineage.original_query_digest
        or request.route_hash != lineage.route_hash
        or request.effective_scope != lineage.effective_scope
        or request.evidence_status is not lineage.evidence_status
        or request.correction_packet_hash != lineage.correction_packet_hash
        or request.selected_hat_id != lineage.selected_hat_id
        or request.selected_hat_version != lineage.selected_hat_version
        or request.selected_manifest_digest != lineage.selected_manifest_digest
        or draft_v1_hashes != (candidate.draft_v1_reference,)
        or assessment.affected_claim_ids != expected_claim_ids
        or assessment.evidence_reference_ids
        != candidate.available_evidence_references
        or assessment.candidate_correction_text != candidate.proposed_correction
    ):
        raise IntegrityError("Critic audit candidate lineage is detached")
    # Reconstruct the same durable acceptance fact after either the first
    # acknowledgement or an exact Step 28 replay.  This keeps audit recovery
    # byte-identical after an append failure or unknown acknowledgement.
    return build_correction_candidate_intake_receipt(
        stored_envelope,
        accepted_at=stored_envelope.submission.submitted_at,
        disposition=CorrectionCandidateIntakeDisposition.ACCEPTED,
    )


def critic_candidate_detected_event(
    request: CriticReviewRequest,
    bridge_result: CriticBridgeResult,
    trusted_context: "CriticTrustedCandidateContext",
    stored_candidate_envelope: CorrectionCandidateEnvelope,
    mapping: CriticCandidateMappingResult,
    intake_receipt: CorrectionCandidateIntakeReceipt,
    *,
    occurred_at: datetime,
    recorded_at: datetime | None = None,
) -> AuditEventDraft:
    """Build one deterministic, hash-only audit draft for an accepted candidate."""

    if not isinstance(request, CriticReviewRequest):
        raise TypeError("request must be CriticReviewRequest")
    if not isinstance(bridge_result, CriticBridgeResult):
        raise TypeError("bridge_result must be CriticBridgeResult")
    if not isinstance(stored_candidate_envelope, CorrectionCandidateEnvelope):
        raise TypeError("stored_candidate_envelope must be CorrectionCandidateEnvelope")
    if not isinstance(intake_receipt, CorrectionCandidateIntakeReceipt):
        raise TypeError("intake_receipt must be CorrectionCandidateIntakeReceipt")
    # Import locally so the pure models/audit layer does not create a module
    # cycle.  Canonical remapping proves the durable Step 28 object is exactly
    # the one derived from the parsed assessment and trusted Kernel context.
    from .bridge import map_critic_assessment_to_step28

    expected_mapping, expected_envelope = map_critic_assessment_to_step28(
        request,
        bridge_result,
        trusted_context=trusted_context,
    )
    if (
        expected_envelope is None
        or expected_mapping != mapping
        or expected_envelope != stored_candidate_envelope
    ):
        raise IntegrityError("Critic audit candidate differs from canonical mapping")
    canonical_intake_receipt = _verify_candidate_lineage(
        request,
        bridge_result,
        stored_candidate_envelope,
        mapping,
        intake_receipt,
    )
    if occurred_at != canonical_intake_receipt.accepted_at:
        raise IntegrityError("Critic audit timestamp differs from durable intake receipt")

    candidate = stored_candidate_envelope.submission.candidate
    submission = stored_candidate_envelope.submission
    assessment = bridge_result.assessment
    assert assessment is not None
    return AuditEventDraft(
        event_type=AuditEventType.CORRECTION_CANDIDATE_DETECTED,
        tenant_id=candidate.tenant_id,
        owner_user_id=candidate.user_id,
        personal_memory_space_id=candidate.personal_memory_space_id,
        # A Step 39 request carries an immutable run identity but does not prove
        # that a matching Step 4 kernel_runs row is durable.  Step 5 RLS rightly
        # rejects manufactured foreign-key/run authority, so the exact run is
        # retained as a lineage hash instead.
        kernel_run_id=None,
        request_id=request.request_id,
        subject_type=AuditSubjectType.CORRECTION_CANDIDATE,
        subject_id=stored_candidate_envelope.candidate_id,
        subject_hash=stored_candidate_envelope.envelope_hash,
        actor_type=AuditActorType.CRITIC_LOOP,
        actor_id=CRITIC_AUDIT_ACTOR_ID,
        idempotency_key=(
            "audit-critic-candidate-" + stored_candidate_envelope.candidate_id
        ),
        occurred_at=occurred_at,
        recorded_at=occurred_at if recorded_at is None else recorded_at,
        event_payload={
            "activation_authority": False,
            "approval_authority": False,
            "candidate_only": True,
            "canonical_evidence_authority": False,
            "commit_authority": False,
            "execution_authority": False,
            "external_action_authority": False,
            "intake_disposition": canonical_intake_receipt.disposition.value,
            "mapping_status": mapping.status.value,
            "reviewer_authority": False,
            "route_authority": False,
            "source_authority": False,
            "state": candidate.state.value,
        },
        reason_codes=(AuditReasonCode.AUDIT_EVENT_APPENDED,),
        policy_id=request.critic_policy_id,
        policy_version=request.critic_policy_version,
        policy_digest=request.critic_policy_digest,
        route_hash=request.route_hash,
        lineage_hashes={
            "candidate_content_hash": candidate.content_hash,
            "candidate_envelope_hash": stored_candidate_envelope.envelope_hash,
            "candidate_lineage_hash": submission.lineage.lineage_hash,
            "candidate_submission_hash": submission.submission_hash,
            "critic_assessment_hash": assessment.assessment_hash,
            "critic_mapping_hash": mapping.mapping_hash,
            "critic_provider_call_receipt_hash": (
                bridge_result.provider_call_receipt.receipt_hash
            ),
            "critic_provider_request_hash": (
                bridge_result.provider_call_receipt.provider_request_hash
            ),
            "critic_provider_response_hash": assessment.provider_response_hash,
            "critic_raw_response_digest": assessment.raw_response_digest,
            "critic_request_hash": request.request_hash,
            "step28_intake_receipt_hash": canonical_intake_receipt.receipt_hash,
            "kernel_run_identity_hash": submission.run_identity_digest,
        },
    )


__all__ = ["CRITIC_AUDIT_ACTOR_ID", "critic_candidate_detected_event"]
