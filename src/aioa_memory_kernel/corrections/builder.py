"""Pure deterministic Step 24 Correction Packet construction."""

from __future__ import annotations

from collections import defaultdict

from aioa_memory_kernel.claims import (
    ClaimAtomicity,
    ClaimEvidenceAssessment,
    ClaimEvidenceCandidateStatus,
    ClaimEvidenceLink,
    ClaimEvidenceRelation,
    ClaimReasonCode,
    ClaimType,
    PacketInputSnapshot,
    verify_packet_input_snapshot_hash,
)
from aioa_memory_kernel.contracts.enums import EvidenceStatus
from aioa_memory_kernel.contracts.exceptions import ContractValidationError, IntegrityError

from .models import (
    PERSISTENCE_DECISION,
    STEP24_SCHEMA_VERSION,
    ConflictHandling,
    CorrectionActionType,
    CorrectionCitation,
    CorrectionConflict,
    CorrectionFactReference,
    CorrectionPacketBoundaryError,
    CorrectionPacketV1A,
    KnowledgePolicyBinding,
    PacketIntegrityMetadata,
    ProhibitedClaim,
    ProhibitionType,
    RequiredCorrection,
    Step24ReasonCode,
    load_packet_policy,
    reason_codes,
    verify_packet_against_snapshot,
)


def _citation(link: ClaimEvidenceLink) -> CorrectionCitation:
    return CorrectionCitation(
        claim_id=link.claim_id,
        evidence_link_hash=link.link_hash,
        candidate_hash=link.candidate_identity_hash,
        source_id=link.source_id,
        knowledge_version_id=link.knowledge_version_id,
        chunk_id=link.chunk_id,
        content_sha256=link.content_sha256,
        source_reference=link.source_reference,
        citation_reference=link.citation_reference,
        authority_level=link.authority_level,
        publication_state=link.publication_state,
        temporal_assessment_hash=link.temporal_assessment_hash,
        relation=link.relation,
    )


def _fact_reference(
    link: ClaimEvidenceLink,
    citation: CorrectionCitation,
) -> CorrectionFactReference:
    return CorrectionFactReference(
        evidence_link_hash=link.link_hash,
        candidate_hash=link.candidate_identity_hash,
        content_sha256=link.content_sha256,
        temporal_assessment_hash=link.temporal_assessment_hash,
        relation=link.relation,
        citation_id=citation.citation_id,
    )


def _assessment_links(
    assessment: ClaimEvidenceAssessment,
    links_by_hash: dict[str, ClaimEvidenceLink],
) -> tuple[ClaimEvidenceLink, ...]:
    hashes = tuple(
        sorted(
            assessment.supporting_link_hashes
            + assessment.refuting_link_hashes
            + assessment.related_link_hashes
            + assessment.insufficient_link_hashes
        )
    )
    try:
        return tuple(links_by_hash[value] for value in hashes)
    except KeyError as exc:
        raise CorrectionPacketBoundaryError(
            Step24ReasonCode.EVIDENCE_LINK_INVALID
        ) from exc


def _correction_action(
    assessment: ClaimEvidenceAssessment,
    links: tuple[ClaimEvidenceLink, ...],
) -> tuple[CorrectionActionType, Step24ReasonCode]:
    reasons = set(assessment.reason_codes) | {
        reason for link in links for reason in link.reason_codes
    }
    if ClaimReasonCode.MATERIAL_CONFLICT in reasons:
        return (
            CorrectionActionType.QUALIFY_CLAIM,
            Step24ReasonCode.CORRECTION_REQUIRED_CONFLICT_QUALIFICATION,
        )
    if ClaimReasonCode.TEMPORAL_MISMATCH in reasons:
        return (
            CorrectionActionType.TEMPORAL_CORRECTION,
            Step24ReasonCode.CORRECTION_REQUIRED_TEMPORAL,
        )
    if ClaimReasonCode.SOURCE_AUTHORITY_INSUFFICIENT in reasons:
        return (
            CorrectionActionType.SOURCE_AUTHORITY_CORRECTION,
            Step24ReasonCode.CORRECTION_REQUIRED_AUTHORITY,
        )
    if assessment.candidate_status is ClaimEvidenceCandidateStatus.REFUTED:
        return (
            CorrectionActionType.REMOVE_CLAIM,
            Step24ReasonCode.CORRECTION_REQUIRED_REFUTED,
        )
    return (
        CorrectionActionType.QUALIFY_CLAIM,
        Step24ReasonCode.CORRECTION_REQUIRED_UNVERIFIED,
    )


def _build_required_correction(
    claim,
    assessment: ClaimEvidenceAssessment,
    links_by_hash: dict[str, ClaimEvidenceLink],
    citations_by_link: dict[str, CorrectionCitation],
) -> RequiredCorrection | None:
    if (
        claim.claim_type is ClaimType.NON_FACTUAL
        or claim.atomicity is ClaimAtomicity.NON_FACTUAL
        or assessment.candidate_status is ClaimEvidenceCandidateStatus.SUPPORTED
    ):
        return None
    links = _assessment_links(assessment, links_by_hash)
    action, reason = _correction_action(assessment, links)
    references = tuple(
        sorted(
            (
                _fact_reference(link, citations_by_link[link.link_hash])
                for link in links
            ),
            key=lambda item: (item.evidence_link_hash, item.fact_reference_hash),
        )
    )
    return RequiredCorrection(
        claim_id=claim.claim_id,
        original_claim_text=claim.exact_claim_text,
        correction_action=action,
        required_replacement_facts=references,
        supporting_evidence_link_hashes=tuple(
            sorted(link.link_hash for link in links)
        ),
        reason_codes=reason_codes(reason),
        limitations=assessment.limitations,
    )


def _build_prohibitions(
    claim,
    assessment: ClaimEvidenceAssessment,
    links_by_hash: dict[str, ClaimEvidenceLink],
) -> tuple[ProhibitedClaim, ...]:
    if claim.claim_type is ClaimType.NON_FACTUAL:
        return ()
    links = _assessment_links(assessment, links_by_hash)
    reasons = set(assessment.reason_codes) | {
        reason for link in links for reason in link.reason_codes
    }
    all_hashes = tuple(
        sorted(
            assessment.supporting_link_hashes
            + assessment.refuting_link_hashes
            + assessment.related_link_hashes
            + assessment.insufficient_link_hashes
        )
    )
    values: list[ProhibitedClaim] = []

    def add(kind: ProhibitionType, reason: Step24ReasonCode) -> None:
        values.append(
            ProhibitedClaim(
                source_claim_id=claim.claim_id,
                exact_or_normalized_prohibited_content=claim.exact_claim_text,
                prohibition_type=kind,
                reason_codes=reason_codes(reason),
                evidence_reference_hashes=all_hashes,
            )
        )

    if assessment.candidate_status is ClaimEvidenceCandidateStatus.REFUTED:
        add(
            ProhibitionType.DO_NOT_REPEAT_EXACT,
            Step24ReasonCode.PROHIBITED_REFUTED_CLAIM,
        )
    if ClaimReasonCode.MATERIAL_CONFLICT in reasons:
        add(
            ProhibitionType.DO_NOT_RESOLVE_CONFLICT_AS_CERTAIN,
            Step24ReasonCode.PROHIBITED_CONFLICT_CERTAINTY,
        )
    if ClaimReasonCode.TEMPORAL_MISMATCH in reasons:
        add(
            ProhibitionType.DO_NOT_USE_OUTSIDE_TEMPORAL_SCOPE,
            Step24ReasonCode.PROHIBITED_UNSUPPORTED_ASSERTION,
        )
    if ClaimReasonCode.SOURCE_AUTHORITY_INSUFFICIENT in reasons:
        add(
            ProhibitionType.DO_NOT_UPGRADE_SOURCE_AUTHORITY,
            Step24ReasonCode.PROHIBITED_AUTHORITY_UPGRADE,
        )
    if (
        assessment.candidate_status is ClaimEvidenceCandidateStatus.UNVERIFIED
        and not values
    ):
        add(
            ProhibitionType.DO_NOT_STATE_AS_FACT,
            Step24ReasonCode.PROHIBITED_UNSUPPORTED_ASSERTION,
        )
    return tuple(values)


def _build_conflicts(
    links: tuple[ClaimEvidenceLink, ...],
) -> tuple[CorrectionConflict, ...]:
    grouped: dict[str, list[ClaimEvidenceLink]] = defaultdict(list)
    for link in links:
        if link.conflict_group_id is not None:
            grouped[link.conflict_group_id].append(link)
    conflicts: list[CorrectionConflict] = []
    for group_id in sorted(grouped):
        group = tuple(grouped[group_id])
        conflicts.append(
            CorrectionConflict(
                conflict_group_id=group_id,
                affected_claim_ids=tuple(sorted({item.claim_id for item in group})),
                supporting_evidence_hashes=tuple(
                    sorted(
                        item.link_hash
                        for item in group
                        if item.relation is ClaimEvidenceRelation.SUPPORTS
                    )
                ),
                refuting_evidence_hashes=tuple(
                    sorted(
                        item.link_hash
                        for item in group
                        if item.relation is ClaimEvidenceRelation.REFUTES
                    )
                ),
                temporal_assessment_hashes=tuple(
                    sorted({item.temporal_assessment_hash for item in group})
                ),
                source_authority_levels=tuple(
                    sorted({item.authority_level for item in group}, key=lambda item: item.value)
                ),
                required_handling=ConflictHandling.PRESERVE_AND_QUALIFY,
            )
        )
    return tuple(conflicts)


def _limitations(
    snapshot: PacketInputSnapshot,
) -> tuple[str, ...]:
    values = {
        limitation
        for assessment in snapshot.ordered_candidate_assessments
        for limitation in assessment.limitations
    }
    reasons = {
        reason
        for assessment in snapshot.ordered_candidate_assessments
        for reason in assessment.reason_codes
    } | {
        reason for link in snapshot.ordered_evidence_links for reason in link.reason_codes
    }
    if ClaimReasonCode.TEMPORAL_MISMATCH in reasons:
        values.add("TEMPORAL_SCOPE_RESTRICTION")
    if ClaimReasonCode.FRESHNESS_STALE in reasons:
        values.add("FRESHNESS_STALE")
    if ClaimReasonCode.MATERIAL_CONFLICT in reasons:
        values.add("MATERIAL_CONFLICT")
    if snapshot.step21_evidence_status is EvidenceStatus.INSUFFICIENT:
        values.add("EVIDENCE_INSUFFICIENT")
    elif snapshot.step21_evidence_status is EvidenceStatus.CONFLICTING:
        values.add("EVIDENCE_CONFLICTING")
    elif snapshot.step21_evidence_status is EvidenceStatus.STALE:
        values.add("EVIDENCE_STALE")
    elif snapshot.step21_evidence_status is EvidenceStatus.UNAVAILABLE:
        values.add("EVIDENCE_UNAVAILABLE")
    return tuple(sorted(values))


def _packet_reasons(
    snapshot: PacketInputSnapshot,
    corrections: tuple[RequiredCorrection, ...],
    prohibited: tuple[ProhibitedClaim, ...],
    citations: tuple[CorrectionCitation, ...],
) -> tuple[Step24ReasonCode, ...]:
    values: set[Step24ReasonCode] = {Step24ReasonCode.PACKET_BUILT}
    for item in corrections:
        values.update(item.reason_codes)
    for item in prohibited:
        values.update(item.reason_codes)
    if citations:
        values.add(Step24ReasonCode.CITATION_REQUIRED)
    status_reason = {
        EvidenceStatus.INSUFFICIENT: Step24ReasonCode.EVIDENCE_INSUFFICIENT,
        EvidenceStatus.CONFLICTING: Step24ReasonCode.EVIDENCE_CONFLICTING,
        EvidenceStatus.STALE: Step24ReasonCode.EVIDENCE_STALE,
        EvidenceStatus.UNAVAILABLE: Step24ReasonCode.EVIDENCE_UNAVAILABLE,
    }.get(snapshot.step21_evidence_status)
    if status_reason is not None:
        values.add(status_reason)
    return reason_codes(*values)


def build_correction_packet(snapshot: PacketInputSnapshot) -> CorrectionPacketV1A:
    """Build one canonical packet from exactly one verified Step 23 snapshot."""

    if not isinstance(snapshot, PacketInputSnapshot):
        raise CorrectionPacketBoundaryError(Step24ReasonCode.PACKET_INPUT_HASH_INVALID)
    try:
        verify_packet_input_snapshot_hash(snapshot)
    except (ContractValidationError, IntegrityError) as exc:
        raise CorrectionPacketBoundaryError(
            Step24ReasonCode.PACKET_INPUT_HASH_INVALID
        ) from exc
    if any(
        value is None
        for value in (
            snapshot.selected_hat_id,
            snapshot.selected_hat_version,
            snapshot.selected_manifest_digest,
            snapshot.hat_scope_id,
        )
    ):
        raise CorrectionPacketBoundaryError(Step24ReasonCode.PACKET_INPUT_HASH_INVALID)

    claims_by_id = {item.claim_id: item for item in snapshot.ordered_claims}
    links_by_hash = {item.link_hash: item for item in snapshot.ordered_evidence_links}
    citations = tuple(_citation(item) for item in snapshot.ordered_evidence_links)
    claim_order = {
        item.claim_id: index for index, item in enumerate(snapshot.ordered_claims)
    }
    relation_order = {value: index for index, value in enumerate(ClaimEvidenceRelation)}
    citations = tuple(
        sorted(
            citations,
            key=lambda item: (
                claim_order[item.claim_id],
                relation_order[item.relation],
                item.source_id,
                item.knowledge_version_id,
                item.chunk_id,
                item.candidate_hash,
                item.citation_id,
            ),
        )
    )
    citations_by_link = {item.evidence_link_hash: item for item in citations}

    corrections: list[RequiredCorrection] = []
    prohibited: list[ProhibitedClaim] = []
    for assessment in snapshot.ordered_candidate_assessments:
        claim = claims_by_id.get(assessment.claim_id)
        if claim is None:
            raise CorrectionPacketBoundaryError(Step24ReasonCode.CLAIM_INPUT_INVALID)
        correction = _build_required_correction(
            claim,
            assessment,
            links_by_hash,
            citations_by_link,
        )
        if correction is not None:
            corrections.append(correction)
        prohibited.extend(_build_prohibitions(claim, assessment, links_by_hash))

    action_order = {value: index for index, value in enumerate(CorrectionActionType)}
    prohibition_order = {value: index for index, value in enumerate(ProhibitionType)}
    ordered_corrections = tuple(
        sorted(
            corrections,
            key=lambda item: (
                claim_order[item.claim_id],
                action_order[item.correction_action],
                item.correction_id,
            ),
        )
    )
    ordered_prohibited = tuple(
        sorted(
            prohibited,
            key=lambda item: (
                claim_order[item.source_claim_id],
                prohibition_order[item.prohibition_type],
                item.prohibited_claim_id,
            ),
        )
    )
    conflicts = _build_conflicts(snapshot.ordered_evidence_links)
    policy_binding = KnowledgePolicyBinding(
        binding_scheme="step20-bundle-hash-step21-status-v1",
        step20_bundle_ids=snapshot.step20_bundle_ids,
        step20_bundle_hashes=snapshot.step20_bundle_hashes,
        step21_resolution_hash=snapshot.step21_result_hash,
        evidence_status=snapshot.step21_evidence_status,
        explicit_decision_values_available=False,
    )
    packet = CorrectionPacketV1A(
        schema_version=STEP24_SCHEMA_VERSION,
        request_id=snapshot.request_id,
        tenant_id=snapshot.tenant_id,
        user_id=snapshot.user_id,
        route_hash=snapshot.route_hash,
        selected_hat_id=snapshot.selected_hat_id,
        selected_hat_version=snapshot.selected_hat_version,
        selected_manifest_digest=snapshot.selected_manifest_digest,
        hat_scope_id=snapshot.hat_scope_id,
        effective_scope=snapshot.effective_scope,
        draft_id=snapshot.draft_id,
        draft_v1_hash=snapshot.draft_v1_hash,
        draft_text_sha256=snapshot.draft_text_sha256,
        original_query_digest=snapshot.original_query_digest,
        step20_evidence_bundle_ids=snapshot.step20_bundle_ids,
        step20_evidence_bundle_hashes=snapshot.step20_bundle_hashes,
        step21_resolution_hash=snapshot.step21_result_hash,
        step23_input_snapshot_hash=snapshot.snapshot_hash,
        claim_processing_policy_digest=snapshot.claim_processing_policy_digest,
        knowledge_policy_binding=policy_binding,
        evidence_status=snapshot.step21_evidence_status,
        ordered_claims=snapshot.ordered_claims,
        ordered_claim_assessments=snapshot.ordered_candidate_assessments,
        ordered_required_corrections=ordered_corrections,
        ordered_prohibited_claims=ordered_prohibited,
        ordered_conflicts=conflicts,
        ordered_citations=citations,
        temporal_freshness_limitations=_limitations(snapshot),
        packet_policy=load_packet_policy(),
        integrity_metadata=PacketIntegrityMetadata(),
        persistence_decision=PERSISTENCE_DECISION,
        reason_codes=_packet_reasons(
            snapshot,
            ordered_corrections,
            ordered_prohibited,
            citations,
        ),
    )
    verify_packet_against_snapshot(packet, snapshot)
    return packet


__all__ = ["build_correction_packet"]
