"""Step 23 orchestration: pure extraction, binding, assessment, and freeze."""

from __future__ import annotations

from .binding import assess_claims, bind_claims_to_evidence
from .extractor import extract_claims
from .models import (
    STEP23_SCHEMA_VERSION,
    ClaimBindingRequest,
    ClaimReasonCode,
    PacketInputSnapshot,
    reason_codes,
    verify_claim_binding_request_hash,
    verify_snapshot_against_request,
)


class ClaimEvidenceBindingService:
    """A deterministic service with no retrieval, model, network, or persistence port."""

    __slots__ = ()

    def freeze_packet_input(self, request: ClaimBindingRequest) -> PacketInputSnapshot:
        verify_claim_binding_request_hash(request)
        claims = extract_claims(
            request.draft_v1,
            scope_dimensions=request.effective_scope,
            policy=request.policy,
        )
        links = bind_claims_to_evidence(request, claims)
        assessments = assess_claims(request, claims, links)
        snapshot = PacketInputSnapshot(
            schema_version=STEP23_SCHEMA_VERSION,
            claim_binding_request_hash=request.request_hash,
            request_id=request.request_id,
            tenant_id=request.tenant_id,
            user_id=request.user_id,
            route_hash=request.route_hash,
            selected_hat_id=request.selected_hat_id,
            selected_hat_version=request.selected_hat_version,
            selected_manifest_digest=request.selected_manifest_digest,
            hat_scope_id=request.hat_scope_id,
            effective_scope=request.effective_scope,
            draft_id=request.draft_v1.draft_id,
            draft_v1_hash=request.draft_v1_hash,
            draft_text_sha256=request.draft_v1.draft_text_sha256,
            original_query_digest=request.original_query_digest,
            step20_bundle_ids=tuple(
                bundle.evidence_bundle_id for bundle in request.step20_bundles
            ),
            step20_bundle_hashes=request.step20_bundle_hashes,
            step21_result_hash=request.step21_result_hash,
            step21_evidence_status=request.temporal_result.evidence_status,
            claim_processing_policy_digest=request.policy.policy_digest,
            ordered_claims=claims,
            ordered_evidence_links=links,
            ordered_candidate_assessments=assessments,
            reason_codes=reason_codes(ClaimReasonCode.PACKET_INPUT_FROZEN),
        )
        verify_snapshot_against_request(snapshot, request)
        return snapshot


__all__ = ["ClaimEvidenceBindingService"]
