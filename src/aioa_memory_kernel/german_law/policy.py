"""Deterministic German-law temporal and source-authority policy."""
from __future__ import annotations

from aioa_memory_kernel.sources.models import SourceAuthorityLevel

from .models import (
    AuthenticityStatus,
    ConsolidationStatus,
    GERMAN_LAW_POLICY_VERSION,
    GERMAN_LAW_TEMPORAL_POLICY_VERSION,
    GermanLawRequest,
    GermanLawSourceAuthorityAssessment,
    GermanLawSourceMetadata,
    GermanLawTemporalAssessment,
    GermanLegalSourceClass,
    LegalJurisdiction,
    TemporalDecision,
    VerificationStatus,
)

_AUTHORITY = {
    GermanLegalSourceClass.DE_FEDERAL_AUTHENTIC_PROMULGATION: SourceAuthorityLevel.OFFICIAL_PRIMARY,
    GermanLegalSourceClass.DE_STATE_AUTHENTIC_PROMULGATION: SourceAuthorityLevel.OFFICIAL_PRIMARY,
    GermanLegalSourceClass.EU_AUTHENTIC_OFFICIAL_JOURNAL: SourceAuthorityLevel.OFFICIAL_PRIMARY,
    GermanLegalSourceClass.DE_FEDERAL_OFFICIAL_COURT_DECISION: SourceAuthorityLevel.OFFICIAL_PRIMARY,
    GermanLegalSourceClass.DE_STATE_OFFICIAL_COURT_DECISION: SourceAuthorityLevel.OFFICIAL_PRIMARY,
    GermanLegalSourceClass.DE_FEDERAL_OFFICIAL_CONSOLIDATED_LAW: SourceAuthorityLevel.AUTHORITATIVE_SECONDARY,
    GermanLegalSourceClass.DE_STATE_OFFICIAL_CONSOLIDATED_LAW: SourceAuthorityLevel.AUTHORITATIVE_SECONDARY,
    GermanLegalSourceClass.EU_OFFICIAL_CONSOLIDATED_ACT: SourceAuthorityLevel.AUTHORITATIVE_SECONDARY,
    GermanLegalSourceClass.OFFICIAL_LEGISLATIVE_MATERIAL: SourceAuthorityLevel.AUTHORITATIVE_SECONDARY,
    GermanLegalSourceClass.OFFICIAL_ADMINISTRATIVE_GUIDANCE: SourceAuthorityLevel.AUTHORITATIVE_SECONDARY,
    GermanLegalSourceClass.OFFICIAL_RESEARCH_OR_EXPLANATORY_MATERIAL: SourceAuthorityLevel.INFORMATIONAL_SECONDARY,
    GermanLegalSourceClass.REPUTABLE_LEGAL_SECONDARY: SourceAuthorityLevel.INFORMATIONAL_SECONDARY,
    GermanLegalSourceClass.PRIVATE_LEGAL_DATABASE: SourceAuthorityLevel.INFORMATIONAL_SECONDARY,
    GermanLegalSourceClass.USER_SUPPLIED_LEGAL_DOCUMENT: SourceAuthorityLevel.USER_SUPPLIED,
    GermanLegalSourceClass.DERIVED_SUMMARY: SourceAuthorityLevel.DERIVED,
    GermanLegalSourceClass.UNKNOWN_LEGAL_SOURCE: SourceAuthorityLevel.UNKNOWN,
}

_EXPECTED_PUBLISHERS = {
    GermanLegalSourceClass.DE_FEDERAL_AUTHENTIC_PROMULGATION: {"Bundesministerium der Justiz", "Bundesamt für Justiz"},
    GermanLegalSourceClass.DE_FEDERAL_OFFICIAL_CONSOLIDATED_LAW: {"Bundesministerium der Justiz", "Bundesamt für Justiz"},
    GermanLegalSourceClass.DE_FEDERAL_OFFICIAL_COURT_DECISION: {"Bundesverfassungsgericht", "Bundesgerichtshof", "Bundesarbeitsgericht", "Bundesverwaltungsgericht", "Bundesfinanzhof", "Bundessozialgericht"},
    GermanLegalSourceClass.OFFICIAL_LEGISLATIVE_MATERIAL: {"Deutscher Bundestag", "Bundesrat"},
    GermanLegalSourceClass.EU_AUTHENTIC_OFFICIAL_JOURNAL: {"Publications Office of the European Union"},
    GermanLegalSourceClass.EU_OFFICIAL_CONSOLIDATED_ACT: {"Publications Office of the European Union"},
}


def assess_temporal(metadata: GermanLawSourceMetadata, request: GermanLawRequest) -> GermanLawTemporalAssessment:
    facts = metadata.temporal
    reasons: list[str] = []
    if request.knowledge_as_of is None:
        return GermanLawTemporalAssessment(TemporalDecision.UNKNOWN, GERMAN_LAW_TEMPORAL_POLICY_VERSION, ("KNOWLEDGE_AS_OF_MISSING",))
    for start, end, label in ((facts.effective_from, facts.effective_to, "EFFECTIVE"), (facts.applicable_from, facts.applicable_to, "APPLICABLE")):
        if start is not None and end is not None and start > end:
            return GermanLawTemporalAssessment(TemporalDecision.CONFLICTING, GERMAN_LAW_TEMPORAL_POLICY_VERSION, (f"{label}_INTERVAL_INVALID",))
    start = facts.applicable_from or facts.effective_from
    end = facts.applicable_to or facts.effective_to
    if start is not None and request.knowledge_as_of < start:
        return GermanLawTemporalAssessment(TemporalDecision.NOT_YET_APPLICABLE, GERMAN_LAW_TEMPORAL_POLICY_VERSION, ("FUTURE_AT_KNOWLEDGE_AS_OF",))
    if end is not None and request.knowledge_as_of >= end:
        return GermanLawTemporalAssessment(TemporalDecision.EXPIRED, GERMAN_LAW_TEMPORAL_POLICY_VERSION, ("EXPIRED_AT_KNOWLEDGE_AS_OF",))
    if start is None and end is None and facts.decision_date is None:
        reasons.append("LEGAL_VALIDITY_INTERVAL_UNKNOWN")
        decision = TemporalDecision.UNKNOWN
    else:
        reasons.append("WITHIN_DECLARED_INTERVAL")
        decision = TemporalDecision.APPLICABLE
    return GermanLawTemporalAssessment(decision, GERMAN_LAW_TEMPORAL_POLICY_VERSION, tuple(reasons))


def assess_source(metadata: GermanLawSourceMetadata, request: GermanLawRequest) -> GermanLawSourceAuthorityAssessment:
    temporal = assess_temporal(metadata, request)
    reasons: list[str] = ["SOURCE_CLASS_MAPPED"]
    limitations: list[str] = []
    authority = _AUTHORITY[metadata.source_class]
    if metadata.jurisdiction is not request.legal_jurisdiction:
        limitations.append("JURISDICTION_MISMATCH")
    if metadata.official_publisher is None or metadata.canonical_official_identifier is None:
        limitations.append("OFFICIAL_IDENTITY_INCOMPLETE")
    expected_publishers = _EXPECTED_PUBLISHERS.get(metadata.source_class)
    if expected_publishers is not None and metadata.official_publisher not in expected_publishers:
        limitations.append("OFFICIAL_PUBLISHER_MISMATCH")
    if metadata.verification_status in {VerificationStatus.CONFLICTING, VerificationStatus.INSUFFICIENT, VerificationStatus.UNVERIFIED}:
        limitations.append("SOURCE_VERIFICATION_INCOMPLETE")
    authentic_classes = {
        GermanLegalSourceClass.DE_FEDERAL_AUTHENTIC_PROMULGATION,
        GermanLegalSourceClass.DE_STATE_AUTHENTIC_PROMULGATION,
        GermanLegalSourceClass.EU_AUTHENTIC_OFFICIAL_JOURNAL,
    }
    if metadata.source_class in authentic_classes and metadata.authenticity_status is not AuthenticityStatus.AUTHENTIC:
        limitations.append("AUTHENTICITY_NOT_PROVEN")
    if metadata.source_class in {GermanLegalSourceClass.DE_FEDERAL_OFFICIAL_CONSOLIDATED_LAW, GermanLegalSourceClass.DE_STATE_OFFICIAL_CONSOLIDATED_LAW, GermanLegalSourceClass.EU_OFFICIAL_CONSOLIDATED_ACT}:
        reasons.append("CONSOLIDATED_TEXT_NOT_AUTHENTIC_PROMULGATION")
        if metadata.consolidation_status is not ConsolidationStatus.OFFICIAL_CONSOLIDATED:
            limitations.append("CONSOLIDATION_STATUS_MISMATCH")
    if metadata.source_class is GermanLegalSourceClass.OFFICIAL_LEGISLATIVE_MATERIAL:
        reasons.append("LEGISLATIVE_HISTORY_NOT_ENACTED_LAW")
    if metadata.source_class is GermanLegalSourceClass.OFFICIAL_ADMINISTRATIVE_GUIDANCE:
        reasons.append("AGENCY_INTERPRETATION_NOT_ENACTED_LAW")
    if metadata.source_class in {GermanLegalSourceClass.DE_FEDERAL_OFFICIAL_COURT_DECISION, GermanLegalSourceClass.DE_STATE_OFFICIAL_COURT_DECISION}:
        reasons.append("DECISION_PRIMARY_ONLY_FOR_IDENTIFIED_CASE")
        if metadata.court_identity is None:
            limitations.append("COURT_IDENTITY_MISSING")
        court_scope_hints = tuple(
            hint
            for hint in (request.court_or_proceeding_hint, request.official_identifier_hint)
            if hint is not None
        )
        if not court_scope_hints:
            limitations.append("COURT_OR_PROCEEDING_SCOPE_UNBOUND")
        else:
            exact_scope_identities = {
                identity
                for identity in (metadata.court_identity, metadata.canonical_official_identifier)
                if identity is not None
            }
            if not any(hint in exact_scope_identities for hint in court_scope_hints):
                limitations.append("COURT_OR_PROCEEDING_SCOPE_MISMATCH")
            else:
                reasons.append("COURT_OR_PROCEEDING_SCOPE_BOUND")
    if temporal.decision is not TemporalDecision.APPLICABLE:
        limitations.append("TEMPORAL_APPLICABILITY_UNRESOLVED")
    return GermanLawSourceAuthorityAssessment(metadata.source_id, authority, metadata.source_class, metadata.authenticity_status, metadata.verification_status, GERMAN_LAW_POLICY_VERSION, tuple(reasons), tuple(limitations), temporal.assessment_digest)


def authority_sort_key(assessment: GermanLawSourceAuthorityAssessment) -> tuple[int, int, str]:
    levels = {
        SourceAuthorityLevel.OFFICIAL_PRIMARY: 0,
        SourceAuthorityLevel.AUTHORITATIVE_SECONDARY: 1,
        SourceAuthorityLevel.INFORMATIONAL_SECONDARY: 2,
        SourceAuthorityLevel.USER_SUPPLIED: 3,
        SourceAuthorityLevel.DERIVED: 4,
        SourceAuthorityLevel.UNKNOWN: 5,
    }
    return levels[assessment.authority_level], len(assessment.unresolved_limitations), assessment.source_id
