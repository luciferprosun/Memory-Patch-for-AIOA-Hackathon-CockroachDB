"""Pure deterministic layers and non-authoritative semantic aggregation."""

from __future__ import annotations

import hashlib
import re

from aioa_memory_kernel.claims import (
    ClaimAtomicity,
    ClaimEvidenceCandidateStatus,
    ClaimEvidenceRelation,
    ClaimReasonCode,
    ClaimType,
    classify_claim,
    exact_text_spans,
    normalize_claim_for_match,
    verify_claim_evidence_link_hash,
)
from aioa_memory_kernel.contracts.enums import EvidenceStatus
from aioa_memory_kernel.contracts.exceptions import (
    ContractValidationError,
    IntegrityError,
)
from aioa_memory_kernel.contracts.serialization import canonical_sha256
from aioa_memory_kernel.corrections import (
    CorrectionActionType,
    CorrectionPacketV1A,
    ProhibitionType,
    verify_citation_hash,
    verify_correction_packet_hash,
    verify_fact_reference_hash,
    verify_required_correction_hash,
)
from aioa_memory_kernel.sources import SourceAuthorityLevel

from .models import (
    CheckResult,
    CorrectionComplianceStatus,
    CorrectedEvidenceProof,
    CorrectedEvidenceVerdict,
    CorrectedEvidenceVerifierRequest,
    CorrectedEvidenceVerifierSignal,
    DraftV2,
    DraftV2ClaimRecord,
    DraftV2PipelineResult,
    DraftV2VerificationSummary,
    EvidenceBindingResult,
    FinalStep25ClaimVerdict,
    LayeredClaimVerification,
    ProhibitedClaimCompliance,
    ProhibitedClaimPresence,
    RequiredCorrectionCompliance,
    STEP25_SCHEMA_VERSION,
    SemanticCandidateVerdict,
    SemanticVerifierSignal,
    Step25BoundaryError,
    Step25ReasonCode,
    VerificationSummaryStatus,
    reason_codes,
    verify_corrected_evidence_proof_hash,
    verify_draft_v2_hash,
    verify_corrected_evidence_request_hash,
    verify_corrected_evidence_signal_hash,
    verify_draft_v2_pipeline_result_hash,
    verify_semantic_signal_hash,
)
from .protocols import CorrectedEvidenceVerifier
from .semantic import (
    SemanticClaimVerifier,
    build_semantic_verifier_request,
    not_required_signal,
)


_CITATION = re.compile(r"\[citation:([a-zA-Z0-9._:-]{1,255})\]")
_DATE = re.compile(r"(?<![0-9])(?:[12][0-9]{3}(?:-[01][0-9](?:-[0-3][0-9])?)?|[0-3]?[0-9]\.[01]?[0-9]\.[12][0-9]{3})(?![0-9])")
_SECTION = re.compile(r"(?:§\s*[0-9]+[a-z]?|\b(?:art(?:ikel|\.)?)\s*[0-9]+[a-z]?)", re.IGNORECASE)
_QUALIFIERS = (
    "unklar",
    "unsicher",
    "nicht verifiziert",
    "nicht belegt",
    "nach den vorliegenden",
    "kann",
    "möglicherweise",
    "conflict",
    "uncertain",
    "unverified",
    "according to the available",
)
_OFFICIAL_MARKERS = (
    "amtliche quelle",
    "amtlich",
    "offizielle quelle",
    "offiziell",
    "official source",
    "primary source",
)
_CURRENT_CERTAINTY = (
    "aktuell gilt",
    "derzeit gilt",
    "gegenwärtig gilt",
    "is currently effective",
    "currently applies",
)


def _without_citations(value: str) -> str:
    return " ".join(_CITATION.sub(" ", value).split())


def _exact_text_without_citations(value: str) -> str:
    """Remove citation markers while preserving exact sentence punctuation."""

    compact = " ".join(_CITATION.sub(" ", value).split())
    return re.sub(r"\s+([,.;:!?])", r"\1", compact)


def _normalized_semantic_text(value: str) -> str:
    return normalize_claim_for_match(_without_citations(value))


def _packet_text_values(packet: CorrectionPacketV1A) -> tuple[str, ...]:
    values = [item.exact_claim_text for item in packet.ordered_claims]
    values.extend(packet.temporal_freshness_limitations)
    for citation in packet.ordered_citations:
        values.extend((citation.source_reference, citation.citation_reference))
    for correction in packet.ordered_required_corrections:
        values.extend(correction.limitations)
    return tuple(values)


def _verify_lineage(draft_v2: DraftV2, packet: CorrectionPacketV1A) -> None:
    try:
        verify_draft_v2_hash(draft_v2)
        verify_correction_packet_hash(packet)
    except RuntimeError as exc:
        raise Step25BoundaryError(Step25ReasonCode.DRAFT_V2_INVALID) from exc
    if (
        draft_v2.request_id,
        draft_v2.tenant_id,
        draft_v2.user_id,
        draft_v2.route_hash,
        draft_v2.original_query_digest,
        draft_v2.draft_v1_hash,
        draft_v2.correction_packet_hash,
    ) != (
        packet.request_id,
        packet.tenant_id,
        packet.user_id,
        packet.route_hash,
        packet.original_query_digest,
        packet.draft_v1_hash,
        packet.packet_hash,
    ):
        raise Step25BoundaryError(Step25ReasonCode.PACKET_LINEAGE_INVALID)


def extract_draft_v2_claims(
    draft_v2: DraftV2,
    packet: CorrectionPacketV1A,
) -> tuple[DraftV2ClaimRecord, ...]:
    """Reuse Step 23 exact-span parsing while binding identities to Draft V2."""

    _verify_lineage(draft_v2, packet)
    source_by_normalized: dict[str, list[str]] = {}
    for source_claim in packet.ordered_claims:
        source_by_normalized.setdefault(source_claim.normalized_match_text, []).append(
            source_claim.claim_id
        )
    values: list[DraftV2ClaimRecord] = []
    for span in exact_text_spans(draft_v2.draft_text):
        semantic = _normalized_semantic_text(span.text)
        claim_type, atomicity = classify_claim(_without_citations(span.text))
        codes = [Step25ReasonCode.SCHEMA_CHECK_PASS]
        if claim_type is ClaimType.NON_FACTUAL:
            codes.append(Step25ReasonCode.NON_FACTUAL_NO_EVIDENCE_REQUIRED)
        values.append(
            DraftV2ClaimRecord(
                draft_v2_id=draft_v2.draft_v2_id,
                draft_v2_hash=draft_v2.draft_v2_hash,
                start_offset=span.start_offset,
                end_offset=span.end_offset,
                exact_claim_text=span.text,
                normalized_match_text=semantic,
                claim_type=claim_type,
                atomicity=atomicity,
                scope_dimensions=packet.effective_scope,
                cited_citation_ids=tuple(sorted(set(_CITATION.findall(span.text)))),
                aligned_draft_v1_claim_ids=tuple(
                    sorted(source_by_normalized.get(semantic, ()))
                ),
                reason_codes=reason_codes(*codes),
            )
        )
    return tuple(values)


def evaluate_required_corrections(
    claims: tuple[DraftV2ClaimRecord, ...],
    packet: CorrectionPacketV1A,
) -> tuple[RequiredCorrectionCompliance, ...]:
    normalized = {claim.claim_id: claim.normalized_match_text for claim in claims}
    used_citations = {
        citation_id for claim in claims for citation_id in claim.cited_citation_ids
    }
    results: list[RequiredCorrectionCompliance] = []
    for correction in packet.ordered_required_corrections:
        original = normalize_claim_for_match(correction.original_claim_text)
        required_citations = {
            item.citation_id for item in correction.required_replacement_facts
        }
        matched = tuple(
            sorted(
                claim.claim_id
                for claim in claims
                if correction.claim_id in claim.aligned_draft_v1_claim_ids
                or normalized[claim.claim_id] == original
                or original in normalized[claim.claim_id]
                or bool(required_citations & set(claim.cited_citation_ids))
            )
        )
        original_present = any(value == original for value in normalized.values())
        citations_present = required_citations.issubset(used_citations)
        qualified = any(
            marker in normalized[claim_id]
            for claim_id in matched
            for marker in _QUALIFIERS
        )
        action = correction.correction_action
        if action is CorrectionActionType.REMOVE_CLAIM:
            status = (
                CorrectionComplianceStatus.SATISFIED
                if not original_present
                else CorrectionComplianceStatus.NOT_SATISFIED
            )
        elif action is CorrectionActionType.REPLACE_CLAIM:
            if not original_present and citations_present:
                status = CorrectionComplianceStatus.SATISFIED
            elif not original_present or citations_present:
                status = CorrectionComplianceStatus.PARTIALLY_SATISFIED
            else:
                status = CorrectionComplianceStatus.NOT_SATISFIED
        elif action in {
            CorrectionActionType.QUALIFY_CLAIM,
            CorrectionActionType.TEMPORAL_CORRECTION,
            CorrectionActionType.SOURCE_AUTHORITY_CORRECTION,
        }:
            if not original_present or qualified:
                status = CorrectionComplianceStatus.SATISFIED
            elif matched:
                status = CorrectionComplianceStatus.PARTIALLY_SATISFIED
            else:
                status = CorrectionComplianceStatus.NOT_SATISFIED
        else:
            status = (
                CorrectionComplianceStatus.SATISFIED
                if matched and (qualified or citations_present)
                else CorrectionComplianceStatus.NOT_SATISFIED
            )
        code = {
            CorrectionComplianceStatus.SATISFIED: Step25ReasonCode.REQUIRED_CORRECTION_SATISFIED,
            CorrectionComplianceStatus.PARTIALLY_SATISFIED: Step25ReasonCode.REQUIRED_CORRECTION_PARTIAL,
            CorrectionComplianceStatus.NOT_SATISFIED: Step25ReasonCode.REQUIRED_CORRECTION_MISSING,
            CorrectionComplianceStatus.NOT_APPLICABLE: Step25ReasonCode.REQUIRED_CORRECTION_SATISFIED,
        }[status]
        results.append(
            RequiredCorrectionCompliance(
                correction_id=correction.correction_id,
                source_claim_id=correction.claim_id,
                status=status,
                matched_draft_v2_claim_ids=matched,
                reason_codes=reason_codes(code),
            )
        )
    return tuple(sorted(results, key=lambda item: item.correction_id))


def evaluate_prohibited_claims(
    claims: tuple[DraftV2ClaimRecord, ...],
    packet: CorrectionPacketV1A,
) -> tuple[ProhibitedClaimCompliance, ...]:
    results: list[ProhibitedClaimCompliance] = []
    for prohibited in packet.ordered_prohibited_claims:
        prohibited_text = normalize_claim_for_match(
            prohibited.exact_or_normalized_prohibited_content
        )
        matches = tuple(
            sorted(
                claim.claim_id
                for claim in claims
                if claim.normalized_match_text == prohibited_text
            )
        )
        presence = (
            ProhibitedClaimPresence.PRESENT_EXACT
            if matches
            else ProhibitedClaimPresence.NOT_PRESENT
        )
        code = (
            Step25ReasonCode.PROHIBITED_CLAIM_PRESENT
            if matches
            else Step25ReasonCode.PROHIBITED_CLAIM_ABSENT
        )
        results.append(
            ProhibitedClaimCompliance(
                prohibited_claim_id=prohibited.prohibited_claim_id,
                source_claim_id=prohibited.source_claim_id,
                presence=presence,
                matched_draft_v2_claim_ids=matches,
                reason_codes=reason_codes(code),
            )
        )
    return tuple(sorted(results, key=lambda item: item.prohibited_claim_id))


class DraftV2LayeredVerifier:
    """Run deterministic layers first and aggregate semantic signals last."""

    def __init__(
        self,
        semantic_verifier: SemanticClaimVerifier | None = None,
        *,
        corrected_evidence_verifier: CorrectedEvidenceVerifier | None = None,
    ) -> None:
        if semantic_verifier is not None and not callable(
            getattr(semantic_verifier, "verify", None)
        ):
            raise TypeError("semantic_verifier must implement SemanticClaimVerifier")
        if corrected_evidence_verifier is not None and not callable(
            getattr(corrected_evidence_verifier, "verify", None)
        ):
            raise TypeError(
                "corrected_evidence_verifier must implement CorrectedEvidenceVerifier"
            )
        self._semantic_verifier = semantic_verifier
        self._corrected_evidence_verifier = corrected_evidence_verifier

    @staticmethod
    def _semantic_request(
        claim: DraftV2ClaimRecord,
        packet: CorrectionPacketV1A,
    ):
        citations = {
            item.citation_id: item for item in packet.ordered_citations
        }
        context = tuple(
            sorted(
                (
                    f"citation_id={citation.citation_id};relation={citation.relation.value};"
                    f"source_id={citation.source_id};version_id={citation.knowledge_version_id};"
                    f"chunk_id={citation.chunk_id};authority={citation.authority_level.value};"
                    f"content_sha256={citation.content_sha256}"
                )
                for citation_id in claim.cited_citation_ids
                if (citation := citations.get(citation_id)) is not None
            )
        )
        digest = canonical_sha256(
            {
                "claim_hash": claim.claim_hash,
                "correction_packet_hash": packet.packet_hash,
                "allowed_citation_ids": tuple(sorted(citations)),
                "evidence_context": context,
            }
        )
        return build_semantic_verifier_request(
            claim_id=claim.claim_id,
            draft_v2_hash=claim.draft_v2_hash,
            correction_packet_hash=packet.packet_hash,
            claim_text=claim.exact_claim_text,
            allowed_citation_ids=tuple(sorted(citations)),
            evidence_context=context,
            deterministic_context_digest=digest,
        )

    @staticmethod
    def _verify_corrected_evidence_proof(
        proof: CorrectedEvidenceProof,
        request: CorrectedEvidenceVerifierRequest,
        claim: DraftV2ClaimRecord,
        packet: CorrectionPacketV1A,
    ) -> None:
        """Independently reconstruct the exact REFUTES-to-correction binding."""

        verify_corrected_evidence_request_hash(request)
        verify_corrected_evidence_proof_hash(proof)
        if (
            proof.request_hash != request.request_hash
            or proof.correction_packet_hash != packet.packet_hash
            or proof.target_claim_id != claim.claim_id
            or proof.target_claim_hash != claim.claim_hash
            or proof.satisfied_correction_ids != request.satisfied_correction_ids
            or request.claim_id != claim.claim_id
            or request.claim_hash != claim.claim_hash
            or request.claim_text != claim.exact_claim_text
            or request.correction_packet_hash != packet.packet_hash
            or request.cited_citation_ids != claim.cited_citation_ids
        ):
            raise RuntimeError("corrected evidence proof request binding failed")

        link = proof.original_evidence_link
        verify_claim_evidence_link_hash(link)
        if (
            link.relation is not ClaimEvidenceRelation.REFUTES
            or link.evidence_span_text_sha256 is None
            or link.evidence_span_text_sha256 != proof.evidence_span_text_sha256
        ):
            raise RuntimeError("corrected evidence proof is not a REFUTES span")

        citations = {
            item.citation_id: item for item in packet.ordered_citations
        }
        citation = citations.get(proof.packet_citation_id)
        if citation is None:
            raise RuntimeError("corrected evidence citation is not in the packet")
        verify_citation_hash(citation)
        if (
            citation.citation_hash != proof.packet_citation_hash
            or citation.citation_id not in claim.cited_citation_ids
            or citation.citation_id not in request.cited_citation_ids
            or citation.evidence_link_hash != link.link_hash
            or citation.relation is not ClaimEvidenceRelation.REFUTES
            or citation.claim_id != link.claim_id
            or (
                citation.source_id,
                citation.knowledge_version_id,
                citation.chunk_id,
                citation.content_sha256,
                citation.temporal_assessment_hash,
                citation.relation,
                citation.authority_level,
                citation.publication_state,
                citation.source_reference,
                citation.citation_reference,
            )
            != (
                link.source_id,
                link.knowledge_version_id,
                link.chunk_id,
                link.content_sha256,
                link.temporal_assessment_hash,
                link.relation,
                link.authority_level,
                link.publication_state,
                link.source_reference,
                link.citation_reference,
            )
        ):
            raise RuntimeError("corrected evidence citation/link binding failed")

        corrections = {
            item.correction_id: item
            for item in packet.ordered_required_corrections
        }
        for correction_id in proof.satisfied_correction_ids:
            correction = corrections.get(correction_id)
            if correction is None:
                raise RuntimeError("corrected evidence correction is not in the packet")
            verify_required_correction_hash(correction)
            if correction.claim_id != link.claim_id:
                raise RuntimeError("corrected evidence correction targets another claim")
            matching_facts = tuple(
                fact
                for fact in correction.required_replacement_facts
                if (
                    fact.evidence_link_hash == link.link_hash
                    and fact.citation_id == citation.citation_id
                    and fact.candidate_hash == citation.candidate_hash
                    and fact.content_sha256 == link.content_sha256
                    and fact.temporal_assessment_hash
                    == link.temporal_assessment_hash
                    and fact.relation is ClaimEvidenceRelation.REFUTES
                )
            )
            for fact in correction.required_replacement_facts:
                verify_fact_reference_hash(fact)
            if (
                len(matching_facts) != 1
                or link.link_hash not in correction.supporting_evidence_link_hashes
            ):
                raise RuntimeError(
                    "corrected evidence correction lacks the exact REFUTES fact"
                )

        target_text = _exact_text_without_citations(claim.exact_claim_text)
        target_text_sha256 = hashlib.sha256(target_text.encode("utf-8")).hexdigest()
        if (
            not target_text
            or target_text_sha256 != proof.target_claim_text_sha256
            or target_text_sha256 != proof.evidence_span_text_sha256
            or target_text_sha256 != link.evidence_span_text_sha256
        ):
            raise RuntimeError("corrected target text is not the exact evidence span")

    @staticmethod
    def _deterministic_layers(
        claim: DraftV2ClaimRecord,
        packet: CorrectionPacketV1A,
        prohibition_violating_claim_ids: frozenset[str],
    ) -> tuple[
        CheckResult,
        CheckResult,
        CheckResult,
        CheckResult,
        CheckResult,
        EvidenceBindingResult,
        tuple[Step25ReasonCode, ...],
        tuple[str, ...],
    ]:
        known = {item.citation_id: item for item in packet.ordered_citations}
        cited = tuple(
            known[item] for item in claim.cited_citation_ids if item in known
        )
        unknown = set(claim.cited_citation_ids) - set(known)
        citation_result = CheckResult.FAIL if unknown else (
            CheckResult.PASS if cited else CheckResult.UNDECIDED
        )
        packet_result = (
            CheckResult.FAIL
            if claim.claim_id in prohibition_violating_claim_ids
            else CheckResult.PASS
        )

        packet_text = "\n".join(_packet_text_values(packet))
        claim_text = _without_citations(claim.exact_claim_text)
        packet_dates = set(_DATE.findall(packet_text))
        claim_dates = set(_DATE.findall(claim_text))
        packet_sections = {value.casefold() for value in _SECTION.findall(packet_text)}
        claim_sections = {value.casefold() for value in _SECTION.findall(claim_text)}
        fact_result = (
            CheckResult.FAIL
            if claim_dates - packet_dates or claim_sections - packet_sections
            else CheckResult.PASS
        )

        assessment_by_claim = {
            item.claim_id: item for item in packet.ordered_claim_assessments
        }
        aligned_assessments = tuple(
            assessment_by_claim[item]
            for item in claim.aligned_draft_v1_claim_ids
            if item in assessment_by_claim
        )
        temporal_mismatch = any(
            ClaimReasonCode.TEMPORAL_MISMATCH in item.reason_codes
            for item in aligned_assessments
        ) or any(
            item.prohibition_type is ProhibitionType.DO_NOT_USE_OUTSIDE_TEMPORAL_SCOPE
            and item.source_claim_id in claim.aligned_draft_v1_claim_ids
            for item in packet.ordered_prohibited_claims
        )
        certainty = any(marker in claim.normalized_match_text for marker in _CURRENT_CERTAINTY)
        temporal_result = CheckResult.NOT_APPLICABLE
        if claim.claim_type is ClaimType.TEMPORAL or claim_dates or temporal_mismatch:
            temporal_result = (
                CheckResult.FAIL
                if temporal_mismatch
                or (certainty and packet.evidence_status in {
                    EvidenceStatus.CONFLICTING,
                    EvidenceStatus.STALE,
                    EvidenceStatus.INSUFFICIENT,
                    EvidenceStatus.UNAVAILABLE,
                })
                else CheckResult.PASS
            )

        source_assertion = any(
            marker in claim.normalized_match_text for marker in _OFFICIAL_MARKERS
        )
        source_result = CheckResult.NOT_APPLICABLE
        if claim.claim_type is ClaimType.SOURCE_ASSERTION or source_assertion:
            source_result = (
                CheckResult.PASS
                if cited
                and all(
                    item.authority_level is SourceAuthorityLevel.OFFICIAL_PRIMARY
                    for item in cited
                )
                else CheckResult.FAIL
            )

        relations = {item.relation for item in cited}
        conflict_claims = {
            claim_id
            for conflict in packet.ordered_conflicts
            for claim_id in conflict.affected_claim_ids
        }
        aligned_conflict = bool(set(claim.aligned_draft_v1_claim_ids) & conflict_claims)
        if claim.claim_type is ClaimType.NON_FACTUAL:
            evidence = EvidenceBindingResult.NOT_APPLICABLE
        elif (
            ClaimEvidenceRelation.SUPPORTS in relations
            and ClaimEvidenceRelation.REFUTES in relations
        ) or aligned_conflict:
            evidence = EvidenceBindingResult.CONFLICTING
        elif ClaimEvidenceRelation.REFUTES in relations:
            evidence = EvidenceBindingResult.REFUTED
        elif ClaimEvidenceRelation.SUPPORTS in relations:
            evidence = EvidenceBindingResult.SUPPORTED
        elif aligned_assessments:
            statuses = {item.candidate_status for item in aligned_assessments}
            if ClaimEvidenceCandidateStatus.REFUTED in statuses:
                evidence = EvidenceBindingResult.REFUTED
            elif ClaimEvidenceCandidateStatus.SUPPORTED in statuses:
                evidence = EvidenceBindingResult.SUPPORTED
            else:
                evidence = EvidenceBindingResult.UNVERIFIED
        else:
            evidence = EvidenceBindingResult.UNVERIFIED
        if evidence is EvidenceBindingResult.SUPPORTED and packet.evidence_status in {
            EvidenceStatus.INSUFFICIENT,
            EvidenceStatus.UNAVAILABLE,
            EvidenceStatus.STALE,
            EvidenceStatus.INVALID,
        }:
            evidence = EvidenceBindingResult.UNVERIFIED

        codes: list[Step25ReasonCode] = [Step25ReasonCode.SCHEMA_CHECK_PASS]
        codes.append(
            Step25ReasonCode.CITATION_INVALID
            if citation_result is CheckResult.FAIL
            else Step25ReasonCode.CITATION_VALID
        )
        codes.append(
            Step25ReasonCode.FACT_CHECK_FAIL
            if fact_result is CheckResult.FAIL
            else Step25ReasonCode.FACT_CHECK_PASS
        )
        if temporal_result is not CheckResult.NOT_APPLICABLE:
            codes.append(
                Step25ReasonCode.TEMPORAL_CHECK_FAIL
                if temporal_result is CheckResult.FAIL
                else Step25ReasonCode.TEMPORAL_CHECK_PASS
            )
        if source_result is not CheckResult.NOT_APPLICABLE:
            codes.append(
                Step25ReasonCode.SOURCE_CHECK_FAIL
                if source_result is CheckResult.FAIL
                else Step25ReasonCode.SOURCE_CHECK_PASS
            )
        codes.append(
            {
                EvidenceBindingResult.SUPPORTED: Step25ReasonCode.EVIDENCE_SUPPORTS,
                EvidenceBindingResult.REFUTED: Step25ReasonCode.EVIDENCE_REFUTES,
                EvidenceBindingResult.UNVERIFIED: Step25ReasonCode.EVIDENCE_UNVERIFIED,
                EvidenceBindingResult.CONFLICTING: Step25ReasonCode.EVIDENCE_CONFLICTING,
                EvidenceBindingResult.NOT_APPLICABLE: Step25ReasonCode.NON_FACTUAL_NO_EVIDENCE_REQUIRED,
            }[evidence]
        )
        limitations = set(packet.temporal_freshness_limitations)
        if packet.evidence_status is not EvidenceStatus.SUFFICIENT:
            limitations.add(f"upstream evidence status: {packet.evidence_status.value}")
        return (
            packet_result,
            fact_result,
            temporal_result,
            source_result,
            citation_result,
            evidence,
            reason_codes(*codes),
            tuple(sorted(limitations)),
        )

    def verify(
        self,
        draft_v2: DraftV2,
        packet: CorrectionPacketV1A,
    ) -> tuple[
        tuple[DraftV2ClaimRecord, ...],
        tuple[LayeredClaimVerification, ...],
        DraftV2VerificationSummary,
    ]:
        claims = extract_draft_v2_claims(draft_v2, packet)
        correction_results = evaluate_required_corrections(claims, packet)
        prohibition_results = evaluate_prohibited_claims(claims, packet)
        satisfied_corrections_by_claim: dict[str, tuple[str, ...]] = {}
        for claim in claims:
            satisfied_corrections_by_claim[claim.claim_id] = tuple(
                sorted(
                    item.correction_id
                    for item in correction_results
                    if item.status is CorrectionComplianceStatus.SATISFIED
                    and claim.claim_id in item.matched_draft_v2_claim_ids
                )
            )
        violating = frozenset(
            claim_id
            for item in prohibition_results
            if item.presence in {
                ProhibitedClaimPresence.PRESENT_EXACT,
                ProhibitedClaimPresence.PRESENT_SEMANTICALLY_EQUIVALENT,
            }
            for claim_id in item.matched_draft_v2_claim_ids
        )
        verifications: list[LayeredClaimVerification] = []
        for claim in claims:
            (
                packet_result,
                fact_result,
                temporal_result,
                source_result,
                citation_result,
                evidence,
                deterministic_codes,
                limitations,
            ) = self._deterministic_layers(claim, packet, violating)
            corrected_signal: CorrectedEvidenceVerifierSignal | None = None
            corrected_code: Step25ReasonCode | None = None
            corrected_support = False
            satisfied_correction_ids = satisfied_corrections_by_claim[claim.claim_id]
            if (
                evidence is EvidenceBindingResult.REFUTED
                and satisfied_correction_ids
                and claim.cited_citation_ids
                and self._corrected_evidence_verifier is not None
            ):
                corrected_request = CorrectedEvidenceVerifierRequest(
                    claim_id=claim.claim_id,
                    claim_hash=claim.claim_hash,
                    draft_v2_hash=draft_v2.draft_v2_hash,
                    correction_packet_hash=packet.packet_hash,
                    claim_text=claim.exact_claim_text,
                    satisfied_correction_ids=satisfied_correction_ids,
                    cited_citation_ids=claim.cited_citation_ids,
                )
                try:
                    verify_corrected_evidence_request_hash(corrected_request)
                    candidate = self._corrected_evidence_verifier.verify(
                        corrected_request
                    )
                    if not isinstance(candidate, CorrectedEvidenceVerifierSignal):
                        raise TypeError("corrected evidence signal must be typed")
                    verify_corrected_evidence_signal_hash(candidate)
                    if (
                        candidate.request_hash != corrected_request.request_hash
                        or not set(candidate.evidence_reference_ids).issubset(
                            claim.cited_citation_ids
                        )
                    ):
                        raise RuntimeError("corrected evidence signal detached")
                    if candidate.verdict is CorrectedEvidenceVerdict.SUPPORTS:
                        if candidate.proof is None:
                            raise RuntimeError("corrected evidence proof is missing")
                        self._verify_corrected_evidence_proof(
                            candidate.proof,
                            corrected_request,
                            claim,
                            packet,
                        )
                    corrected_signal = candidate
                except (TypeError, ValueError, RuntimeError):
                    corrected_signal = CorrectedEvidenceVerifierSignal(
                        request_hash=corrected_request.request_hash,
                        verdict=CorrectedEvidenceVerdict.INVALID,
                        evidence_reference_ids=(),
                        proof=None,
                    )
                corrected_code = {
                    CorrectedEvidenceVerdict.SUPPORTS: (
                        Step25ReasonCode.CORRECTED_EVIDENCE_SUPPORTS
                    ),
                    CorrectedEvidenceVerdict.UNCERTAIN: (
                        Step25ReasonCode.CORRECTED_EVIDENCE_UNCERTAIN
                    ),
                    CorrectedEvidenceVerdict.INVALID: (
                        Step25ReasonCode.CORRECTED_EVIDENCE_INVALID
                    ),
                }[corrected_signal.verdict]
                if corrected_signal.verdict is CorrectedEvidenceVerdict.SUPPORTS:
                    corrected_support = True
                    evidence = EvidenceBindingResult.SUPPORTED
                    if fact_result is CheckResult.FAIL:
                        # The reconstructed corrected-evidence proof binds the
                        # target bytes to the exact canonical source span.  A
                        # new date/section introduced by that span is therefore
                        # a verified correction, not an unsupported model fact.
                        fact_result = CheckResult.PASS
                    deterministic_codes = reason_codes(
                        *(
                            code
                            for code in deterministic_codes
                            if code
                            not in {
                                Step25ReasonCode.EVIDENCE_REFUTES,
                                Step25ReasonCode.FACT_CHECK_FAIL,
                            }
                        ),
                        Step25ReasonCode.FACT_CHECK_PASS,
                        Step25ReasonCode.EVIDENCE_SUPPORTS,
                    )
            semantic_request = self._semantic_request(claim, packet)
            deterministic_failure = any(
                value is CheckResult.FAIL
                for value in (
                    packet_result,
                    fact_result,
                    temporal_result,
                    source_result,
                    citation_result,
                )
            ) or evidence in {EvidenceBindingResult.REFUTED, EvidenceBindingResult.CONFLICTING}
            exact_supported_alignment = bool(claim.aligned_draft_v1_claim_ids) and (
                evidence is EvidenceBindingResult.SUPPORTED
            )
            semantic_needed = (
                claim.claim_type is not ClaimType.NON_FACTUAL
                and not deterministic_failure
                and not exact_supported_alignment
                and not corrected_support
            )
            if semantic_needed and self._semantic_verifier is not None:
                signal = self._semantic_verifier.verify(semantic_request)
                try:
                    verify_semantic_signal_hash(signal)
                    if (
                        signal.semantic_request_hash != semantic_request.request_hash
                        or not set(signal.evidence_reference_ids).issubset(
                            semantic_request.allowed_citation_ids
                        )
                    ):
                        raise RuntimeError("semantic signal detached")
                except RuntimeError:
                    signal = SemanticVerifierSignal(
                        semantic_request_hash=semantic_request.request_hash,
                        candidate_verdict=SemanticCandidateVerdict.INVALID,
                        evidence_reference_ids=(),
                        reason_codes=reason_codes(
                            Step25ReasonCode.SEMANTIC_VERIFIER_INVALID
                        ),
                        provider_response_hash=None,
                    )
            elif semantic_needed:
                signal = SemanticVerifierSignal(
                    semantic_request_hash=semantic_request.request_hash,
                    candidate_verdict=SemanticCandidateVerdict.UNAVAILABLE,
                    evidence_reference_ids=(),
                    reason_codes=reason_codes(
                        Step25ReasonCode.SEMANTIC_VERIFIER_UNAVAILABLE
                    ),
                    provider_response_hash=None,
                )
            else:
                signal = not_required_signal(semantic_request.request_hash)

            if claim.claim_type is ClaimType.NON_FACTUAL:
                verdict = FinalStep25ClaimVerdict.VERIFIED_SUPPORTED
            elif citation_result is CheckResult.FAIL:
                verdict = FinalStep25ClaimVerdict.INVALID
            elif evidence is EvidenceBindingResult.CONFLICTING:
                verdict = FinalStep25ClaimVerdict.CONFLICTING
            elif deterministic_failure or evidence is EvidenceBindingResult.REFUTED:
                verdict = FinalStep25ClaimVerdict.VERIFIED_REFUTED
            elif corrected_support:
                verdict = FinalStep25ClaimVerdict.VERIFIED_SUPPORTED
            elif exact_supported_alignment:
                verdict = FinalStep25ClaimVerdict.VERIFIED_SUPPORTED
            elif (
                evidence is EvidenceBindingResult.SUPPORTED
                and signal.candidate_verdict is SemanticCandidateVerdict.SUPPORTS
            ):
                verdict = FinalStep25ClaimVerdict.VERIFIED_SUPPORTED
            elif signal.candidate_verdict is SemanticCandidateVerdict.REFUTES:
                verdict = FinalStep25ClaimVerdict.VERIFIED_REFUTED
            else:
                verdict = FinalStep25ClaimVerdict.UNVERIFIED
            verdict_code = {
                FinalStep25ClaimVerdict.VERIFIED_SUPPORTED: Step25ReasonCode.CLAIM_VERIFIED_SUPPORTED,
                FinalStep25ClaimVerdict.VERIFIED_REFUTED: Step25ReasonCode.CLAIM_VERIFIED_REFUTED,
                FinalStep25ClaimVerdict.UNVERIFIED: Step25ReasonCode.CLAIM_UNVERIFIED,
                FinalStep25ClaimVerdict.CONFLICTING: Step25ReasonCode.CLAIM_CONFLICTING,
                FinalStep25ClaimVerdict.INVALID: Step25ReasonCode.CLAIM_INVALID,
            }[verdict]
            all_codes = reason_codes(
                *deterministic_codes,
                *signal.reason_codes,
                *((corrected_code,) if corrected_code is not None else ()),
                verdict_code,
            )
            verifications.append(
                LayeredClaimVerification(
                    claim_id=claim.claim_id,
                    claim_hash=claim.claim_hash,
                    draft_v2_hash=draft_v2.draft_v2_hash,
                    schema_layer_result=CheckResult.PASS,
                    packet_compliance_result=packet_result,
                    deterministic_fact_result=fact_result,
                    temporal_result=temporal_result,
                    source_result=source_result,
                    citation_result=citation_result,
                    evidence_binding_result=evidence,
                    semantic_verifier_result=signal,
                    final_step25_verdict=verdict,
                    reason_codes=all_codes,
                    limitations=limitations,
                    corrected_evidence_signal_hash=(
                        corrected_signal.signal_hash
                        if corrected_signal is not None
                        else None
                    ),
                    corrected_evidence_proof=(
                        corrected_signal.proof
                        if corrected_signal is not None
                        else None
                    ),
                    corrected_evidence_proof_hash=(
                        corrected_signal.proof.proof_hash
                        if corrected_signal is not None
                        and corrected_signal.proof is not None
                        else None
                    ),
                )
            )

        verdicts = [item.final_step25_verdict for item in verifications]
        required_satisfied = sum(
            item.status is CorrectionComplianceStatus.SATISFIED
            for item in correction_results
        )
        required_unsatisfied = len(correction_results) - required_satisfied
        prohibited_violations = sum(
            item.presence in {
                ProhibitedClaimPresence.PRESENT_EXACT,
                ProhibitedClaimPresence.PRESENT_SEMANTICALLY_EQUIVALENT,
            }
            for item in prohibition_results
        )
        citation_failures = sum(
            item.citation_result is CheckResult.FAIL for item in verifications
        )
        if (
            required_unsatisfied
            or prohibited_violations
            or citation_failures
            or FinalStep25ClaimVerdict.INVALID in verdicts
            or FinalStep25ClaimVerdict.VERIFIED_REFUTED in verdicts
        ):
            status = VerificationSummaryStatus.FAILED
            summary_reason = Step25ReasonCode.SUMMARY_FAILED
        elif FinalStep25ClaimVerdict.CONFLICTING in verdicts:
            status = VerificationSummaryStatus.CONFLICTING
            summary_reason = Step25ReasonCode.SUMMARY_CONFLICTING
        elif FinalStep25ClaimVerdict.UNVERIFIED in verdicts:
            status = VerificationSummaryStatus.INCOMPLETE
            summary_reason = Step25ReasonCode.SUMMARY_INCOMPLETE
        else:
            status = VerificationSummaryStatus.VERIFIED
            summary_reason = Step25ReasonCode.SUMMARY_VERIFIED
        verifications_tuple = tuple(verifications)
        summary = DraftV2VerificationSummary(
            schema_version=STEP25_SCHEMA_VERSION,
            request_id=draft_v2.request_id,
            tenant_id=draft_v2.tenant_id,
            user_id=draft_v2.user_id,
            route_hash=draft_v2.route_hash,
            draft_v2_hash=draft_v2.draft_v2_hash,
            correction_packet_hash=packet.packet_hash,
            claim_count=len(verifications),
            verified_supported_count=verdicts.count(
                FinalStep25ClaimVerdict.VERIFIED_SUPPORTED
            ),
            verified_refuted_count=verdicts.count(
                FinalStep25ClaimVerdict.VERIFIED_REFUTED
            ),
            unverified_count=verdicts.count(FinalStep25ClaimVerdict.UNVERIFIED),
            conflicting_count=verdicts.count(FinalStep25ClaimVerdict.CONFLICTING),
            invalid_count=verdicts.count(FinalStep25ClaimVerdict.INVALID),
            required_correction_results=correction_results,
            prohibited_claim_results=prohibition_results,
            required_corrections_satisfied=required_satisfied,
            required_corrections_unsatisfied=required_unsatisfied,
            prohibited_claim_violations=prohibited_violations,
            citation_failures=citation_failures,
            ordered_claim_verification_hashes=tuple(
                item.verification_hash for item in verifications_tuple
            ),
            summary_status=status,
            reason_codes=reason_codes(summary_reason),
        )
        return claims, verifications_tuple, summary


def verify_corrected_evidence_proofs_against_packet(
    pipeline: DraftV2PipelineResult,
    packet: CorrectionPacketV1A,
) -> None:
    """Reconstruct every persisted corrected-evidence signal against its packet.

    The ordinary Step 25 envelope hash proves only that its nested records are
    internally canonical.  A later boundary must additionally prove that a
    corrected-support proof still names the exact packet correction, citation,
    original REFUTES link, and corrected target claim that authorized it.
    """

    if not isinstance(pipeline, DraftV2PipelineResult):
        raise ContractValidationError("pipeline must be DraftV2PipelineResult")
    if not isinstance(packet, CorrectionPacketV1A):
        raise ContractValidationError("packet must be CorrectionPacketV1A")
    verify_draft_v2_pipeline_result_hash(pipeline)
    verify_correction_packet_hash(packet)
    if (
        pipeline.draft_v2.correction_packet_hash != packet.packet_hash
        or pipeline.verification_summary.correction_packet_hash
        != packet.packet_hash
    ):
        raise IntegrityError("Step 25 corrected proof packet lineage mismatch")

    claims = {item.claim_id: item for item in pipeline.ordered_claims}
    if len(claims) != len(pipeline.ordered_claims):
        raise IntegrityError("Step 25 corrected proof claim identities duplicate")
    citations = {item.citation_id: item for item in packet.ordered_citations}
    correction_results = pipeline.verification_summary.required_correction_results
    for verification in pipeline.ordered_claim_verifications:
        claim = claims.get(verification.claim_id)
        if claim is None or claim.claim_hash != verification.claim_hash:
            raise IntegrityError("Step 25 corrected proof target claim is detached")
        cites_refutation = any(
            (citation := citations.get(citation_id)) is not None
            and citation.relation is ClaimEvidenceRelation.REFUTES
            for citation_id in claim.cited_citation_ids
        )
        if verification.corrected_evidence_signal_hash is None:
            if cites_refutation and (
                verification.evidence_binding_result
                is EvidenceBindingResult.SUPPORTED
                or verification.final_step25_verdict
                is FinalStep25ClaimVerdict.VERIFIED_SUPPORTED
            ):
                raise IntegrityError(
                    "supported REFUTES citation requires corrected evidence proof"
                )
            continue
        satisfied = tuple(
            sorted(
                item.correction_id
                for item in correction_results
                if item.status is CorrectionComplianceStatus.SATISFIED
                and claim.claim_id in item.matched_draft_v2_claim_ids
            )
        )
        if not satisfied or not claim.cited_citation_ids:
            raise IntegrityError("Step 25 corrected signal lacks correction context")
        request = CorrectedEvidenceVerifierRequest(
            claim_id=claim.claim_id,
            claim_hash=claim.claim_hash,
            draft_v2_hash=pipeline.draft_v2.draft_v2_hash,
            correction_packet_hash=packet.packet_hash,
            claim_text=claim.exact_claim_text,
            satisfied_correction_ids=satisfied,
            cited_citation_ids=claim.cited_citation_ids,
        )
        corrected_codes = {
            Step25ReasonCode.CORRECTED_EVIDENCE_SUPPORTS,
            Step25ReasonCode.CORRECTED_EVIDENCE_UNCERTAIN,
            Step25ReasonCode.CORRECTED_EVIDENCE_INVALID,
        } & set(verification.reason_codes)
        if len(corrected_codes) != 1:
            raise IntegrityError("Step 25 corrected signal reason is ambiguous")
        code = next(iter(corrected_codes))
        verdict = {
            Step25ReasonCode.CORRECTED_EVIDENCE_SUPPORTS: (
                CorrectedEvidenceVerdict.SUPPORTS
            ),
            Step25ReasonCode.CORRECTED_EVIDENCE_UNCERTAIN: (
                CorrectedEvidenceVerdict.UNCERTAIN
            ),
            Step25ReasonCode.CORRECTED_EVIDENCE_INVALID: (
                CorrectedEvidenceVerdict.INVALID
            ),
        }[code]
        proof = verification.corrected_evidence_proof
        if cites_refutation and (
            verification.evidence_binding_result is EvidenceBindingResult.SUPPORTED
            or verification.final_step25_verdict
            is FinalStep25ClaimVerdict.VERIFIED_SUPPORTED
        ) and (
            verdict is not CorrectedEvidenceVerdict.SUPPORTS
            or proof is None
            or verification.corrected_evidence_proof_hash is None
        ):
            raise IntegrityError(
                "supported REFUTES citation requires a complete support proof"
            )
        references = (proof.packet_citation_id,) if proof is not None else ()
        reconstructed_signal = CorrectedEvidenceVerifierSignal(
            request_hash=request.request_hash,
            verdict=verdict,
            evidence_reference_ids=references,
            proof=proof,
        )
        if (
            reconstructed_signal.signal_hash
            != verification.corrected_evidence_signal_hash
        ):
            raise IntegrityError("Step 25 corrected signal hash is detached")
        if proof is not None:
            DraftV2LayeredVerifier._verify_corrected_evidence_proof(
                proof,
                request,
                claim,
                packet,
            )


__all__ = [
    "DraftV2LayeredVerifier",
    "evaluate_prohibited_claims",
    "evaluate_required_corrections",
    "extract_draft_v2_claims",
    "verify_corrected_evidence_proofs_against_packet",
]
