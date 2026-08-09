"""Pure deterministic Step 23 claim-to-evidence binding.

The binder never retrieves data and never calls a model. It only compares
exact Draft V1 spans with exact excerpts from the already verified Step 20
bundle and applies the Step 21 temporal/freshness ceiling.
"""

from __future__ import annotations

import hashlib
import re

from aioa_memory_kernel.contracts.enums import EvidenceStatus
from aioa_memory_kernel.evidence import EvidenceBundleItem, FrozenEvidenceBundle
from aioa_memory_kernel.sources import SourceAuthorityLevel
from aioa_memory_kernel.temporal import (
    FreshnessStatus,
    TemporalApplicability,
    TemporalCandidateAssessment,
)

from .extractor import TextSpan, exact_text_spans
from .models import (
    EVIDENCE_SPAN_CONVENTION,
    MAX_EVIDENCE_LINKS,
    ClaimAtomicity,
    ClaimBindingRequest,
    ClaimBoundaryError,
    ClaimEvidenceAssessment,
    ClaimEvidenceCandidateStatus,
    ClaimEvidenceLink,
    ClaimEvidenceRelation,
    ClaimReasonCode,
    ClaimRecord,
    ClaimType,
    link_order_key,
    normalize_claim_for_match,
    reason_codes,
)


_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)
_NEGATIONS = frozenset(
    {
        "kein",
        "keine",
        "keinen",
        "keiner",
        "keines",
        "keinem",
        "nicht",
        "nie",
        "niemals",
        "no",
        "not",
        "never",
    }
)
_STOPWORDS = frozenset(
    {
        "aber",
        "als",
        "and",
        "auch",
        "das",
        "dem",
        "den",
        "der",
        "des",
        "die",
        "ein",
        "eine",
        "einer",
        "eines",
        "für",
        "in",
        "ist",
        "mit",
        "oder",
        "the",
        "und",
        "von",
        "zu",
    }
)
_RELATION_PRIORITY = {
    ClaimEvidenceRelation.SUPPORTS: 0,
    ClaimEvidenceRelation.REFUTES: 1,
    ClaimEvidenceRelation.RELATED_ONLY: 2,
}


def _tokens(value: str) -> tuple[str, ...]:
    return tuple(_TOKEN.findall(normalize_claim_for_match(value)))


def _negation_counterparts(left: str, right: str) -> bool:
    left_tokens = _tokens(left)
    right_tokens = _tokens(right)
    left_negations = tuple(token for token in left_tokens if token in _NEGATIONS)
    right_negations = tuple(token for token in right_tokens if token in _NEGATIONS)
    if (len(left_negations), len(right_negations)) not in {(1, 0), (0, 1)}:
        return False
    left_base = tuple(token for token in left_tokens if token not in _NEGATIONS)
    right_base = tuple(token for token in right_tokens if token not in _NEGATIONS)
    return bool(left_base) and left_base == right_base


def _related(left: str, right: str) -> bool:
    left_tokens = {token for token in _tokens(left) if token not in _STOPWORDS}
    right_tokens = {token for token in _tokens(right) if token not in _STOPWORDS}
    shared = left_tokens & right_tokens
    return (
        bool(left_tokens)
        and bool(right_tokens)
        and len(shared) >= 2
        and len(shared) * 2 >= min(len(left_tokens), len(right_tokens))
    )


def _text_relation(claim: ClaimRecord, evidence: TextSpan) -> ClaimEvidenceRelation | None:
    if claim.normalized_match_text == normalize_claim_for_match(evidence.text):
        return ClaimEvidenceRelation.SUPPORTS
    if _negation_counterparts(claim.exact_claim_text, evidence.text):
        return ClaimEvidenceRelation.REFUTES
    if _related(claim.exact_claim_text, evidence.text):
        return ClaimEvidenceRelation.RELATED_ONLY
    return None


def _item_and_assessment_maps(
    request: ClaimBindingRequest,
) -> tuple[
    tuple[tuple[FrozenEvidenceBundle, EvidenceBundleItem], ...],
    dict[str, TemporalCandidateAssessment],
]:
    ordered_items = tuple(
        (bundle, item)
        for bundle in request.step20_bundles
        for item in bundle.ordered_items
    )
    assessments = {
        assessment.step20_item_hash: assessment
        for assessment in request.temporal_result.assessments
    }
    return ordered_items, assessments


def _relation_after_policy(
    claim: ClaimRecord,
    raw_relation: ClaimEvidenceRelation,
    item: EvidenceBundleItem,
    temporal: TemporalCandidateAssessment,
) -> tuple[ClaimEvidenceRelation, tuple[ClaimReasonCode, ...]]:
    codes: list[ClaimReasonCode] = []
    relation = raw_relation
    if claim.claim_type is ClaimType.SOURCE_ASSERTION and (
        item.authority_level is not SourceAuthorityLevel.OFFICIAL_PRIMARY
    ):
        relation = ClaimEvidenceRelation.INSUFFICIENT
        codes.append(ClaimReasonCode.SOURCE_AUTHORITY_INSUFFICIENT)
    elif (
        temporal.temporal_applicability is not TemporalApplicability.APPLICABLE
        and temporal.conflict_group_id is None
    ):
        relation = ClaimEvidenceRelation.INSUFFICIENT
        codes.append(ClaimReasonCode.TEMPORAL_MISMATCH)
    elif not temporal.selected and temporal.conflict_group_id is None:
        relation = ClaimEvidenceRelation.INSUFFICIENT
        codes.append(ClaimReasonCode.TEMPORAL_MISMATCH)

    if temporal.conflict_group_id is not None:
        codes.extend((ClaimReasonCode.TEMPORAL_CONFLICT, ClaimReasonCode.MATERIAL_CONFLICT))
    if temporal.freshness_status is FreshnessStatus.STALE:
        codes.append(ClaimReasonCode.FRESHNESS_STALE)
    elif temporal.freshness_status in {
        FreshnessStatus.UNKNOWN,
        FreshnessStatus.NOT_APPLICABLE,
    }:
        codes.append(ClaimReasonCode.EVIDENCE_INSUFFICIENT)

    codes.append(
        {
            ClaimEvidenceRelation.SUPPORTS: ClaimReasonCode.EVIDENCE_SUPPORTS,
            ClaimEvidenceRelation.REFUTES: ClaimReasonCode.EVIDENCE_REFUTES,
            ClaimEvidenceRelation.RELATED_ONLY: ClaimReasonCode.EVIDENCE_RELATED_ONLY,
            ClaimEvidenceRelation.INSUFFICIENT: ClaimReasonCode.EVIDENCE_INSUFFICIENT,
        }[relation]
    )
    return relation, reason_codes(*codes)


def _build_link(
    claim: ClaimRecord,
    bundle: FrozenEvidenceBundle,
    item: EvidenceBundleItem,
    temporal: TemporalCandidateAssessment,
    evidence_span: TextSpan,
    raw_relation: ClaimEvidenceRelation,
) -> ClaimEvidenceLink:
    relation, codes = _relation_after_policy(claim, raw_relation, item, temporal)
    return ClaimEvidenceLink(
        claim_id=claim.claim_id,
        step20_bundle_hash=bundle.bundle_hash,
        step20_item_ordinal=item.item_ordinal,
        step20_item_hash=item.item_hash,
        evidence_id=item.evidence_id,
        candidate_identity_hash=item.identity.identity_hash,
        source_id=item.identity.source_id,
        knowledge_version_id=item.identity.knowledge_version_id,
        chunk_id=item.identity.chunk_id,
        content_sha256=item.identity.content_sha256,
        citation_reference=item.citation_reference,
        source_reference=item.source_reference,
        authority_level=item.authority_level,
        publication_state=item.publication_state,
        scope_digest=item.scope_digest,
        effective_scope=item.effective_scope,
        relation=relation,
        evidence_span_convention=EVIDENCE_SPAN_CONVENTION,
        evidence_start_offset=evidence_span.start_offset,
        evidence_end_offset=evidence_span.end_offset,
        evidence_span_text_sha256=hashlib.sha256(
            evidence_span.text.encode("utf-8")
        ).hexdigest(),
        temporal_assessment_hash=temporal.assessment_hash,
        temporal_applicability=temporal.temporal_applicability,
        freshness_status=temporal.freshness_status,
        conflict_group_id=temporal.conflict_group_id,
        reason_codes=codes,
    )


def bind_claims_to_evidence(
    request: ClaimBindingRequest,
    claims: tuple[ClaimRecord, ...],
) -> tuple[ClaimEvidenceLink, ...]:
    """Bind claims only to exact Step 20/21 evidence already in the request."""

    ordered_items, temporal_by_item = _item_and_assessment_maps(request)
    links: list[ClaimEvidenceLink] = []
    for claim in claims:
        if claim.atomicity is ClaimAtomicity.NON_FACTUAL:
            continue
        for bundle, item in ordered_items:
            temporal = temporal_by_item[item.item_hash]
            best: tuple[ClaimEvidenceRelation, TextSpan] | None = None
            for span in exact_text_spans(item.excerpt.text):
                relation = _text_relation(claim, span)
                if relation is None:
                    continue
                candidate_key = (
                    _RELATION_PRIORITY[relation],
                    span.start_offset,
                    span.end_offset,
                )
                if best is None or candidate_key < (
                    _RELATION_PRIORITY[best[0]],
                    best[1].start_offset,
                    best[1].end_offset,
                ):
                    best = (relation, span)
            if best is not None:
                links.append(_build_link(claim, bundle, item, temporal, best[1], best[0]))
                if len(links) > MAX_EVIDENCE_LINKS:
                    raise ClaimBoundaryError(ClaimReasonCode.EVIDENCE_INSUFFICIENT)
    return tuple(sorted(links, key=link_order_key))


def assess_claims(
    request: ClaimBindingRequest,
    claims: tuple[ClaimRecord, ...],
    links: tuple[ClaimEvidenceLink, ...],
) -> tuple[ClaimEvidenceAssessment, ...]:
    """Create conservative Step 23 candidate statuses under Step 21 ceilings."""

    global_ceiling = request.temporal_result.evidence_status in {
        EvidenceStatus.INSUFFICIENT,
        EvidenceStatus.UNAVAILABLE,
        EvidenceStatus.STALE,
        EvidenceStatus.INVALID,
        EvidenceStatus.NOT_REQUIRED,
    }
    assessments: list[ClaimEvidenceAssessment] = []
    for claim in claims:
        claim_links = tuple(item for item in links if item.claim_id == claim.claim_id)
        supporting = tuple(
            item.link_hash
            for item in claim_links
            if item.relation is ClaimEvidenceRelation.SUPPORTS
        )
        refuting = tuple(
            item.link_hash
            for item in claim_links
            if item.relation is ClaimEvidenceRelation.REFUTES
        )
        related = tuple(
            item.link_hash
            for item in claim_links
            if item.relation is ClaimEvidenceRelation.RELATED_ONLY
        )
        insufficient = tuple(
            item.link_hash
            for item in claim_links
            if item.relation is ClaimEvidenceRelation.INSUFFICIENT
        )
        conflict = any(item.conflict_group_id is not None for item in claim_links) or bool(
            supporting and refuting
        )
        stale = any(item.freshness_status is FreshnessStatus.STALE for item in claim_links)
        limitations: set[str] = set()
        codes: list[ClaimReasonCode] = []
        if claim.atomicity is ClaimAtomicity.NON_FACTUAL:
            limitations.add("NON_FACTUAL_SEGMENT_NO_EVIDENCE_VERDICT")
        if claim.atomicity is ClaimAtomicity.COMPOUND:
            limitations.add("COMPOUND_CLAIM_REQUIRES_LATER_DECOMPOSITION")
        if conflict:
            limitations.add("MATERIAL_SUPPORT_REFUTATION_OR_TEMPORAL_CONFLICT")
            codes.append(ClaimReasonCode.MATERIAL_CONFLICT)
        if stale or request.temporal_result.evidence_status is EvidenceStatus.STALE:
            limitations.add("STALE_EVIDENCE_CANNOT_ESTABLISH_UNQUALIFIED_SUPPORT")
            codes.append(ClaimReasonCode.FRESHNESS_STALE)
        if request.temporal_result.evidence_status is EvidenceStatus.UNAVAILABLE:
            limitations.add("STEP21_EVIDENCE_UNAVAILABLE")
            codes.append(ClaimReasonCode.EVIDENCE_UNAVAILABLE)

        eligible_for_binary = (
            claim.atomicity is ClaimAtomicity.ATOMIC
            and not global_ceiling
            and not conflict
            and not stale
        )
        if eligible_for_binary and supporting and not refuting:
            status = ClaimEvidenceCandidateStatus.SUPPORTED
            codes.extend((ClaimReasonCode.EVIDENCE_SUPPORTS, ClaimReasonCode.CLAIM_SUPPORTED))
        elif eligible_for_binary and refuting and not supporting:
            status = ClaimEvidenceCandidateStatus.REFUTED
            codes.extend((ClaimReasonCode.EVIDENCE_REFUTES, ClaimReasonCode.CLAIM_REFUTED))
        else:
            status = ClaimEvidenceCandidateStatus.UNVERIFIED
            codes.append(ClaimReasonCode.CLAIM_UNVERIFIED)
            if not claim_links or insufficient:
                codes.append(ClaimReasonCode.EVIDENCE_INSUFFICIENT)
        assessments.append(
            ClaimEvidenceAssessment(
                claim_id=claim.claim_id,
                candidate_status=status,
                supporting_link_hashes=supporting,
                refuting_link_hashes=refuting,
                related_link_hashes=related,
                insufficient_link_hashes=insufficient,
                reason_codes=reason_codes(*codes),
                limitations=tuple(sorted(limitations)),
            )
        )
    return tuple(sorted(assessments, key=lambda item: item.claim_id))


__all__ = ["assess_claims", "bind_claims_to_evidence"]
