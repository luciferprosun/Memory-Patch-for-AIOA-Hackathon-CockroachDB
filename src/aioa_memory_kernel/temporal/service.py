"""Step 21 orchestration over one verified Step 20 evidence boundary."""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Protocol

from aioa_memory_kernel.contracts.enums import EvidenceStatus, KnowledgeRoute
from aioa_memory_kernel.contracts.exceptions import ContractValidationError, IntegrityError
from aioa_memory_kernel.contracts.serialization import ensure_utc
from aioa_memory_kernel.evidence import (
    HybridEvidenceOutcome,
    RetrievalCoverageStatus,
)
from aioa_memory_kernel.routing import KnowledgeRouteResult

from .conflicts import apply_supersession_and_conflicts
from .freshness import evaluate_freshness
from .models import (
    STEP21_SCHEMA_VERSION,
    CompletenessFallbackSummary,
    CompletenessPolicy,
    EvidenceAvailability,
    FreshnessPolicy,
    FreshnessStatus,
    Step21ReasonCode,
    TemporalApplicability,
    TemporalBoundaryError,
    TemporalCandidateAssessment,
    TemporalQueryMode,
    TemporalResolutionRequest,
    TemporalResolutionResult,
    load_completeness_policy,
    load_temporal_policy,
    verify_temporal_request_hash,
)
from .resolver import CandidateTemporalState, assess_bundle_item


PERSISTENCE_DECISION = "NOT_REQUIRED_STEP21_PURE_EVIDENCE_POLICY"


class TrustedClock(Protocol):
    """Injected internal clock; model/provider metadata is never consulted."""

    def now(self) -> datetime:
        ...


def _mode_reason(mode: TemporalQueryMode) -> Step21ReasonCode:
    return {
        TemporalQueryMode.CURRENT: Step21ReasonCode.CURRENT_TIME_BOUND,
        TemporalQueryMode.AS_OF: Step21ReasonCode.EXPLICIT_AS_OF_BOUND,
        TemporalQueryMode.FUTURE: Step21ReasonCode.FUTURE_AS_OF_BOUND,
        TemporalQueryMode.UNSPECIFIED: Step21ReasonCode.AS_OF_UNSPECIFIED,
    }[mode]


def _bundle_states(request: TemporalResolutionRequest) -> tuple[CandidateTemporalState, ...]:
    bundle = request.step20_outcome.bundle
    if bundle is None:
        return ()
    return tuple(
        assess_bundle_item(bundle, item, as_of=request.evaluation_as_of)
        for item in bundle.ordered_items
    )


def _fallback_states(request: TemporalResolutionRequest) -> tuple[CandidateTemporalState, ...]:
    if request.fallback_outcome is None or request.fallback_outcome.bundle is None:
        return ()
    return tuple(
        assess_bundle_item(
            request.fallback_outcome.bundle,
            item,
            as_of=request.evaluation_as_of,
        )
        for item in request.fallback_outcome.bundle.ordered_items
    )


def _applicable_count(values: tuple[CandidateTemporalState, ...]) -> int:
    return sum(
        value.applicability is TemporalApplicability.APPLICABLE
        for value in values
    )


class TemporalResolutionService:
    """Resolve temporal evidence without a model, network, or side effect."""

    def prepare_request(
        self,
        *,
        route: KnowledgeRouteResult,
        step20_outcome: HybridEvidenceOutcome,
        temporal_mode: TemporalQueryMode,
        knowledge_as_of: datetime | None,
        clock: TrustedClock,
        availability: EvidenceAvailability,
        freshness_policy: FreshnessPolicy,
        completeness_policy: CompletenessPolicy | None = None,
        fallback_outcome: HybridEvidenceOutcome | None = None,
    ) -> TemporalResolutionRequest:
        if not hasattr(clock, "now") or not callable(clock.now):
            raise ContractValidationError("an injected trusted clock is required")
        trusted_now = ensure_utc(clock.now(), "trusted clock value")
        return TemporalResolutionRequest(
            route=route,
            step20_outcome=step20_outcome,
            temporal_mode=temporal_mode,
            knowledge_as_of=knowledge_as_of,
            trusted_now=trusted_now,
            availability=availability,
            freshness_policy=freshness_policy,
            completeness_policy=completeness_policy or load_completeness_policy(),
            fallback_outcome=fallback_outcome,
        )

    def resolve(self, request: TemporalResolutionRequest) -> TemporalResolutionResult:
        if not isinstance(request, TemporalResolutionRequest):
            raise TemporalBoundaryError(Step21ReasonCode.STEP20_INPUT_HASH_INVALID)
        try:
            verify_temporal_request_hash(request)
        except (ContractValidationError, IntegrityError) as exc:
            raise TemporalBoundaryError(
                Step21ReasonCode.STEP20_INPUT_HASH_INVALID
            ) from exc
        if request.route.knowledge_route is KnowledgeRoute.AMBIGUOUS:
            raise TemporalBoundaryError(Step21ReasonCode.AMBIGUOUS_ROUTE)
        if request.route.knowledge_route is KnowledgeRoute.PASS_THROUGH:
            summary = CompletenessFallbackSummary(
                attempted=False,
                maximum_attempts=request.completeness_policy.maximum_attempts,
                attempts_used=0,
                primary_candidate_count=0,
                additional_candidates_considered=0,
                additional_candidates_admitted=0,
                final_applicable_count=0,
                reason_codes=(),
            )
            return TemporalResolutionResult(
                schema_version=STEP21_SCHEMA_VERSION,
                temporal_request_hash=request.request_hash,
                request_id=request.route.request_id,
                tenant_id=request.route.tenant_id,
                user_id=request.route.user_id,
                route_hash=request.route.route_hash,
                selected_hat_id=None,
                selected_hat_version=None,
                selected_manifest_digest=None,
                effective_scope=request.route.effective_scope,
                step20_outcome_hash=request.step20_outcome.outcome_hash,
                step20_bundle_hash=None,
                fallback_outcome_hash=None,
                fallback_bundle_hash=None,
                temporal_mode=request.temporal_mode,
                knowledge_as_of=request.knowledge_as_of,
                trusted_now=request.trusted_now,
                evaluation_as_of=request.evaluation_as_of,
                temporal_policy_digest=request.temporal_policy_digest,
                freshness_policy_id=request.freshness_policy.policy_id,
                freshness_policy_version=request.freshness_policy.policy_version,
                freshness_policy_digest=request.freshness_policy.policy_digest,
                completeness_policy_digest=request.completeness_policy.policy_digest,
                answer_status=None,
                assessments=(),
                resolved_item_hashes=(),
                excluded_assessment_hashes=(),
                conflict_groups=(),
                freshness_summary={},
                completeness_fallback=summary,
                evidence_status=EvidenceStatus.NOT_REQUIRED,
                reason_codes=(
                    Step21ReasonCode.NO_HAT_SELECTED,
                    _mode_reason(request.temporal_mode),
                ),
                limitations=(
                    "NO_ANSWER_GENERATION",
                    "NO_AUTHORITY_ESCALATION",
                ),
            )
        if request.route.knowledge_route not in {
            KnowledgeRoute.HAT_ASSIST,
            KnowledgeRoute.HAT_ENFORCE,
        }:
            raise TemporalBoundaryError(Step21ReasonCode.AMBIGUOUS_ROUTE)

        primary = _bundle_states(request)
        resolved_primary, _primary_groups = apply_supersession_and_conflicts(primary)
        applicable = _applicable_count(resolved_primary)
        fallback_values: tuple[CandidateTemporalState, ...] = ()
        attempted = False
        considered = 0
        admitted = 0
        if (
            request.availability is EvidenceAvailability.AVAILABLE
            and applicable < request.completeness_policy.minimum_applicable_items
            and request.completeness_policy.maximum_attempts > 0
            and request.fallback_outcome is not None
        ):
            attempted = True
            fallback_values = _fallback_states(request)
            considered = len(fallback_values)
            primary_hashes = {value.item.item_hash for value in primary}
            fallback_values = tuple(
                value for value in fallback_values if value.item.item_hash not in primary_hashes
            )
            admitted = len(fallback_values)
        combined = (*primary, *fallback_values)
        if len(combined) > 80:
            raise TemporalBoundaryError(Step21ReasonCode.STEP20_INPUT_BINDING_MISMATCH)
        resolved_states, conflict_groups = apply_supersession_and_conflicts(
            tuple(combined)
        )
        resolved_states = tuple(
            evaluate_freshness(
                value,
                request.freshness_policy,
                trusted_now=request.trusted_now,
            )
            for value in resolved_states
        )
        applicable = _applicable_count(resolved_states)
        fallback_reasons: list[Step21ReasonCode] = []
        if attempted:
            fallback_reasons.append(Step21ReasonCode.COMPLETENESS_FALLBACK_ATTEMPTED)
            if admitted:
                fallback_reasons.append(Step21ReasonCode.COMPLETENESS_FALLBACK_ADMITTED)
        if applicable < request.completeness_policy.minimum_applicable_items:
            fallback_reasons.append(Step21ReasonCode.COMPLETENESS_FALLBACK_EXHAUSTED)
        fallback_summary = CompletenessFallbackSummary(
            attempted=attempted,
            maximum_attempts=request.completeness_policy.maximum_attempts,
            attempts_used=1 if attempted else 0,
            primary_candidate_count=len(primary),
            additional_candidates_considered=considered,
            additional_candidates_admitted=admitted,
            final_applicable_count=applicable,
            reason_codes=tuple(fallback_reasons),
        )

        has_invalid = any(not value.integrity_valid for value in resolved_states)
        has_conflict = bool(conflict_groups) or any(
            value.applicability is TemporalApplicability.CONFLICTING
            for value in resolved_states
        )
        selected_states = tuple(
            value
            for value in resolved_states
            if value.applicability is TemporalApplicability.APPLICABLE
            and request.availability is EvidenceAvailability.AVAILABLE
        )
        partial_input = (
            request.step20_outcome.retrieval_coverage
            is RetrievalCoverageStatus.PARTIAL
            or (
                attempted
                and request.fallback_outcome is not None
                and request.fallback_outcome.retrieval_coverage
                is RetrievalCoverageStatus.PARTIAL
            )
        )
        freshness = {value.freshness_status for value in selected_states}
        result_reasons: list[Step21ReasonCode] = [
            _mode_reason(request.temporal_mode)
        ]
        if request.availability is EvidenceAvailability.UNAVAILABLE:
            status = EvidenceStatus.UNAVAILABLE
            result_reasons.append(Step21ReasonCode.EVIDENCE_UNAVAILABLE)
        elif has_invalid:
            status = EvidenceStatus.INVALID
            result_reasons.append(Step21ReasonCode.EVIDENCE_INVALID)
        elif has_conflict:
            status = EvidenceStatus.CONFLICTING
            result_reasons.append(Step21ReasonCode.EVIDENCE_CONFLICTING)
        elif applicable < request.completeness_policy.minimum_applicable_items:
            status = EvidenceStatus.INSUFFICIENT
            result_reasons.extend(
                (
                    Step21ReasonCode.NO_APPLICABLE_EVIDENCE,
                    Step21ReasonCode.EVIDENCE_INSUFFICIENT,
                )
            )
        elif partial_input:
            status = EvidenceStatus.INSUFFICIENT
            result_reasons.extend(
                (
                    Step21ReasonCode.STEP20_COVERAGE_PARTIAL,
                    Step21ReasonCode.EVIDENCE_INSUFFICIENT,
                )
            )
        elif FreshnessStatus.UNKNOWN in freshness:
            status = EvidenceStatus.INSUFFICIENT
            result_reasons.append(Step21ReasonCode.EVIDENCE_INSUFFICIENT)
        elif FreshnessStatus.STALE in freshness:
            status = EvidenceStatus.STALE
            result_reasons.append(Step21ReasonCode.EVIDENCE_STALE)
        else:
            status = EvidenceStatus.SUFFICIENT
            result_reasons.extend(
                (Step21ReasonCode.TEMPORAL_OK, Step21ReasonCode.EVIDENCE_SUFFICIENT)
            )
        result_reasons.extend(fallback_reasons)

        assessments: list[TemporalCandidateAssessment] = []
        for value in resolved_states:
            is_selected = value in selected_states
            assessments.append(
                TemporalCandidateAssessment(
                    step20_bundle_hash=value.bundle_hash,
                    step20_item_hash=value.item.item_hash,
                    evidence_id=value.item.evidence_id,
                    candidate_identity_hash=value.item.identity.identity_hash,
                    source_id=value.item.identity.source_id,
                    knowledge_version_id=value.item.identity.knowledge_version_id,
                    chunk_id=value.item.identity.chunk_id,
                    document_identity=value.facts.document_identity,
                    version_identity=value.version_identity,
                    logical_subject_identity=value.logical_subject_identity,
                    temporal_facts_digest=value.facts.source_temporal_facts_digest,
                    temporal_facts_hash=value.facts.facts_hash,
                    as_of=request.evaluation_as_of,
                    temporal_applicability=value.applicability,
                    freshness_status=value.freshness_status,
                    supersession_status=value.supersession_status,
                    conflict_group_id=value.conflict_group_id,
                    selected=is_selected,
                    reason_codes=value.reasons,
                )
            )
        freshness_counts = Counter(value.freshness_status.value for value in assessments)
        limitations = [
            "COMPLETE_IS_BOUNDED_TO_VERIFIED_STEP20_INPUTS",
            "NO_ANSWER_GENERATION",
            "NO_AUTHORITY_ESCALATION",
            "SOURCE_AUTHORITY_UNCHANGED",
        ]
        if any(
            Step21ReasonCode.TEMPORAL_FACTS_DIGEST_PRESERVED in value.reason_codes
            for value in assessments
        ):
            limitations.append(
                "STEP15_DIGEST_PREIMAGE_NOT_PRESENT_IN_STEP20_PROJECTION"
            )
        primary_bundle = request.step20_outcome.bundle
        fallback_bundle = (
            request.fallback_outcome.bundle
            if attempted and request.fallback_outcome is not None
            else None
        )
        return TemporalResolutionResult(
            schema_version=STEP21_SCHEMA_VERSION,
            temporal_request_hash=request.request_hash,
            request_id=request.route.request_id,
            tenant_id=request.route.tenant_id,
            user_id=request.route.user_id,
            route_hash=request.route.route_hash,
            selected_hat_id=request.route.selected_hat_id,
            selected_hat_version=request.route.selected_hat_version,
            selected_manifest_digest=request.route.selected_manifest_digest,
            effective_scope=request.route.effective_scope,
            step20_outcome_hash=request.step20_outcome.outcome_hash,
            step20_bundle_hash=primary_bundle.bundle_hash if primary_bundle else None,
            fallback_outcome_hash=(
                request.fallback_outcome.outcome_hash
                if fallback_bundle is not None and request.fallback_outcome is not None
                else None
            ),
            fallback_bundle_hash=(fallback_bundle.bundle_hash if fallback_bundle else None),
            temporal_mode=request.temporal_mode,
            knowledge_as_of=request.knowledge_as_of,
            trusted_now=request.trusted_now,
            evaluation_as_of=request.evaluation_as_of,
            temporal_policy_digest=load_temporal_policy().policy_digest,
            freshness_policy_id=request.freshness_policy.policy_id,
            freshness_policy_version=request.freshness_policy.policy_version,
            freshness_policy_digest=request.freshness_policy.policy_digest,
            completeness_policy_digest=request.completeness_policy.policy_digest,
            answer_status=primary_bundle.answer_status if primary_bundle else None,
            assessments=tuple(assessments),
            resolved_item_hashes=tuple(
                value.step20_item_hash for value in assessments if value.selected
            ),
            excluded_assessment_hashes=tuple(
                value.assessment_hash for value in assessments if not value.selected
            ),
            conflict_groups=conflict_groups,
            freshness_summary=dict(freshness_counts),
            completeness_fallback=fallback_summary,
            evidence_status=status,
            reason_codes=tuple(result_reasons),
            limitations=tuple(limitations),
        )


__all__ = [
    "PERSISTENCE_DECISION",
    "TemporalResolutionService",
    "TrustedClock",
]
