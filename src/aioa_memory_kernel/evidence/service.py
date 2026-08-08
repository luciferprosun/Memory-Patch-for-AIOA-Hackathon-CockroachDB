"""Step 20 verified-input Evidence Bundle assembly service."""

from __future__ import annotations

from collections import defaultdict
from typing import TypeAlias

from aioa_memory_kernel.contracts.enums import EvidenceStatus, KnowledgeRoute
from aioa_memory_kernel.contracts.exceptions import ContractValidationError, IntegrityError
from aioa_memory_kernel.embeddings import (
    VectorRetrievalRequest,
    VectorRetrievalResult,
    load_approved_model_spec,
    verify_vector_candidate_hash,
    verify_vector_request_hash,
    verify_vector_result_hash,
)
from aioa_memory_kernel.retrieval import (
    RetrievalRequest,
    RetrievalResult,
    selector_hash,
    verify_candidate_hash,
    verify_request_hash,
    verify_result_hash,
)

from .budget import assemble_budgeted_items
from .diversity import select_diverse_candidates
from .fusion import (
    RankedCandidateInput,
    merge_and_rank_candidates,
    modality_for_retrieval_mode,
)
from .models import (
    STEP20_SCHEMA_VERSION,
    VECTOR_METRIC_POLICY,
    FrozenEvidenceBundle,
    HybridEvidenceOutcome,
    HybridModality,
    HybridRetrievalRequest,
    RetrievalCoverageStatus,
    Step20BoundaryError,
    Step20ReasonCode,
    load_diversity_policy,
    load_ranking_policy,
    verify_hybrid_request_hash,
)


LexicalInput: TypeAlias = tuple[RetrievalRequest, RetrievalResult]
VectorInput: TypeAlias = tuple[VectorRetrievalRequest, VectorRetrievalResult]

PERSISTENCE_DECISION = (
    "NOT_APPLICABLE_STEP4_REQUIRES_EXISTING_KERNEL_RUN_BINDING"
)


def _binding_tuple(value: object) -> tuple[object, ...]:
    return (
        value.request_id,
        value.tenant_id,
        value.user_id,
        value.route_hash,
        value.selected_hat_id,
        value.selected_hat_version,
        value.selected_manifest_digest,
        value.hat_scope_id,
        value.effective_scope,
    )


def _expected_binding(request: HybridRetrievalRequest) -> tuple[object, ...]:
    return (
        request.request_id,
        request.tenant_id,
        request.user_id,
        request.route_hash,
        request.selected_hat_id,
        request.selected_hat_version,
        request.selected_manifest_digest,
        request.hat_scope_id,
        request.effective_scope,
    )


def _verify_lexical_input(
    request: HybridRetrievalRequest,
    retrieval_request: RetrievalRequest,
    result: RetrievalResult,
) -> HybridModality:
    if not isinstance(retrieval_request, RetrievalRequest) or not isinstance(
        result, RetrievalResult
    ):
        raise Step20BoundaryError(Step20ReasonCode.HYBRID_INPUT_REQUIRED)
    try:
        verify_request_hash(retrieval_request)
        verify_result_hash(result)
        for candidate in result.candidates:
            verify_candidate_hash(candidate)
    except (ContractValidationError, IntegrityError) as exc:
        raise Step20BoundaryError(
            Step20ReasonCode.HYBRID_INPUT_HASH_INVALID
        ) from exc
    if _binding_tuple(retrieval_request) != _expected_binding(request):
        raise Step20BoundaryError(
            Step20ReasonCode.HYBRID_INPUT_BINDING_MISMATCH
        )
    if _binding_tuple(result) != _expected_binding(request):
        raise Step20BoundaryError(
            Step20ReasonCode.HYBRID_INPUT_BINDING_MISMATCH
        )
    if (
        retrieval_request.retrieval_mode is not result.retrieval_mode
        or selector_hash(retrieval_request.selector) != result.query_digest
    ):
        raise Step20BoundaryError(
            Step20ReasonCode.HYBRID_INPUT_BINDING_MISMATCH
        )
    modality = modality_for_retrieval_mode(result.retrieval_mode)
    if modality not in request.requested_modalities:
        raise Step20BoundaryError(
            Step20ReasonCode.HYBRID_INPUT_BINDING_MISMATCH
        )
    return modality


def _verify_vector_input(
    request: HybridRetrievalRequest,
    vector_request: VectorRetrievalRequest,
    result: VectorRetrievalResult,
) -> None:
    if not isinstance(vector_request, VectorRetrievalRequest) or not isinstance(
        result, VectorRetrievalResult
    ):
        raise Step20BoundaryError(Step20ReasonCode.HYBRID_INPUT_REQUIRED)
    try:
        verify_vector_request_hash(vector_request)
        verify_vector_result_hash(result)
        for candidate in result.candidates:
            verify_vector_candidate_hash(candidate)
    except (ContractValidationError, IntegrityError) as exc:
        raise Step20BoundaryError(
            Step20ReasonCode.HYBRID_INPUT_HASH_INVALID
        ) from exc
    if _binding_tuple(vector_request) != _expected_binding(request):
        raise Step20BoundaryError(
            Step20ReasonCode.HYBRID_INPUT_BINDING_MISMATCH
        )
    if _binding_tuple(result) != _expected_binding(request):
        raise Step20BoundaryError(
            Step20ReasonCode.HYBRID_INPUT_BINDING_MISMATCH
        )
    if (
        vector_request.query_digest != result.query_digest
        or vector_request.model_digest != result.model_digest
        or result.model_digest != request.embedding_model_digest
    ):
        raise Step20BoundaryError(Step20ReasonCode.HYBRID_MODEL_MISMATCH)
    if HybridModality.VECTOR not in request.requested_modalities:
        raise Step20BoundaryError(
            Step20ReasonCode.HYBRID_INPUT_BINDING_MISMATCH
        )


def _merge_counts(*values: dict[str, int] | object) -> dict[str, int]:
    merged: dict[str, int] = defaultdict(int)
    for value in values:
        for key, count in dict(value).items():  # type: ignore[arg-type]
            merged[key] += count
    return dict(sorted(merged.items()))


class HybridEvidenceService:
    """Fuse already-bounded retrieval results without external effects."""

    def assemble(
        self,
        request: HybridRetrievalRequest,
        *,
        lexical_inputs: tuple[LexicalInput, ...] = (),
        vector_input: VectorInput | None = None,
    ) -> HybridEvidenceOutcome:
        if not isinstance(request, HybridRetrievalRequest):
            raise Step20BoundaryError(Step20ReasonCode.HYBRID_INPUT_REQUIRED)
        try:
            verify_hybrid_request_hash(request)
        except (ContractValidationError, IntegrityError) as exc:
            raise Step20BoundaryError(
                Step20ReasonCode.HYBRID_INPUT_HASH_INVALID
            ) from exc
        if request.route.knowledge_route is KnowledgeRoute.PASS_THROUGH:
            if lexical_inputs or vector_input is not None:
                raise Step20BoundaryError(
                    Step20ReasonCode.HYBRID_INPUT_BINDING_MISMATCH
                )
            return HybridEvidenceOutcome(
                hybrid_request_hash=request.request_hash,
                request_id=request.request_id,
                tenant_id=request.tenant_id,
                user_id=request.user_id,
                route_hash=request.route_hash,
                retrieval_coverage=RetrievalCoverageStatus.EMPTY,
                evidence_status=EvidenceStatus.NOT_REQUIRED,
                bundle=None,
                reason_codes=(Step20ReasonCode.NO_HAT_SELECTED,),
            )
        if request.route.knowledge_route is KnowledgeRoute.AMBIGUOUS:
            raise Step20BoundaryError(Step20ReasonCode.AMBIGUOUS_ROUTE)
        if request.route.knowledge_route not in {
            KnowledgeRoute.HAT_ASSIST,
            KnowledgeRoute.HAT_ENFORCE,
        }:
            raise Step20BoundaryError(Step20ReasonCode.AMBIGUOUS_ROUTE)

        lexical_by_modality: dict[HybridModality, LexicalInput] = {}
        lexical_request_hashes: set[str] = set()
        lexical_result_hashes: set[str] = set()
        for pair in lexical_inputs:
            if not isinstance(pair, tuple) or len(pair) != 2:
                raise Step20BoundaryError(Step20ReasonCode.HYBRID_INPUT_REQUIRED)
            retrieval_request, result = pair
            modality = _verify_lexical_input(request, retrieval_request, result)
            existing = lexical_by_modality.get(modality)
            if existing is not None:
                if (
                    existing[0].request_hash == retrieval_request.request_hash
                    and existing[1].result_hash == result.result_hash
                ):
                    continue
                raise Step20BoundaryError(
                    Step20ReasonCode.HYBRID_INPUT_BINDING_MISMATCH
                )
            lexical_by_modality[modality] = pair
            lexical_request_hashes.add(retrieval_request.request_hash)
            lexical_result_hashes.add(result.result_hash)
        if (
            lexical_request_hashes != set(request.lexical_request_hashes)
            or lexical_result_hashes != set(request.lexical_result_hashes)
        ):
            raise Step20BoundaryError(
                Step20ReasonCode.HYBRID_INPUT_BINDING_MISMATCH
            )

        verified_vector: VectorInput | None = None
        if vector_input is not None:
            if not isinstance(vector_input, tuple) or len(vector_input) != 2:
                raise Step20BoundaryError(Step20ReasonCode.HYBRID_INPUT_REQUIRED)
            vector_request, vector_result = vector_input
            _verify_vector_input(request, vector_request, vector_result)
            if (
                request.vector_request_hash != vector_request.request_hash
                or request.vector_result_hash != vector_result.result_hash
            ):
                raise Step20BoundaryError(
                    Step20ReasonCode.HYBRID_INPUT_BINDING_MISMATCH
                )
            verified_vector = vector_input
        elif request.vector_request_hash is not None or request.vector_result_hash is not None:
            raise Step20BoundaryError(
                Step20ReasonCode.HYBRID_INPUT_BINDING_MISMATCH
            )

        ranked_inputs: list[RankedCandidateInput] = []
        upstream_truncated = False
        for modality in sorted(lexical_by_modality, key=lambda item: item.value):
            retrieval_request, result = lexical_by_modality[modality]
            upstream_truncated = upstream_truncated or result.truncated
            ranked_inputs.extend(
                RankedCandidateInput(
                    modality=modality,
                    upstream_request_hash=retrieval_request.request_hash,
                    upstream_result_hash=result.result_hash,
                    one_based_rank=rank,
                    candidate=candidate,
                )
                for rank, candidate in enumerate(result.candidates, start=1)
            )
        if verified_vector is not None:
            vector_request, vector_result = verified_vector
            upstream_truncated = upstream_truncated or vector_result.truncated
            ranked_inputs.extend(
                RankedCandidateInput(
                    modality=HybridModality.VECTOR,
                    upstream_request_hash=vector_request.request_hash,
                    upstream_result_hash=vector_result.result_hash,
                    one_based_rank=rank,
                    candidate=candidate,
                )
                for rank, candidate in enumerate(vector_result.candidates, start=1)
            )

        available = tuple(
            sorted(
                (
                    *lexical_by_modality.keys(),
                    *((HybridModality.VECTOR,) if verified_vector is not None else ()),
                ),
                key=lambda item: item.value,
            )
        )
        missing = tuple(
            modality
            for modality in request.requested_modalities
            if modality not in available
        )
        before_dedup = len(ranked_inputs)
        ranked = merge_and_rank_candidates(request, tuple(ranked_inputs))
        diversity = select_diverse_candidates(
            ranked,
            maximum_items=request.maximum_bundle_items,
        )
        budget = assemble_budgeted_items(
            diversity.selected,
            context_budget_bytes=request.context_budget_bytes,
        )
        excluded = _merge_counts(diversity.excluded_counts, budget.excluded_counts)
        if missing:
            excluded[Step20ReasonCode.HYBRID_INPUT_REQUIRED.value] = len(missing)
        if upstream_truncated:
            excluded[Step20ReasonCode.BUNDLE_TRUNCATED.value] = (
                excluded.get(Step20ReasonCode.BUNDLE_TRUNCATED.value, 0) + 1
            )
        truncated = bool(missing) or upstream_truncated or diversity.truncated or budget.truncated
        if not budget.items:
            coverage = (
                RetrievalCoverageStatus.PARTIAL
                if truncated
                else RetrievalCoverageStatus.EMPTY
            )
            evidence_status = EvidenceStatus.INSUFFICIENT
        elif truncated:
            coverage = RetrievalCoverageStatus.PARTIAL
            evidence_status = EvidenceStatus.INSUFFICIENT
        else:
            coverage = RetrievalCoverageStatus.COMPLETE
            evidence_status = EvidenceStatus.SUFFICIENT
        reasons: list[Step20ReasonCode] = [
            Step20ReasonCode.HYBRID_OK
            if budget.items
            else Step20ReasonCode.NO_ADMISSIBLE_EVIDENCE
        ]
        if before_dedup > len(ranked):
            reasons.append(Step20ReasonCode.HYBRID_DUPLICATE_MERGED)
        if truncated:
            reasons.append(Step20ReasonCode.BUNDLE_TRUNCATED)
        ranking = load_ranking_policy()
        diversity_policy = load_diversity_policy()
        spec = load_approved_model_spec()
        bundle = FrozenEvidenceBundle(
            schema_version=STEP20_SCHEMA_VERSION,
            hybrid_request_hash=request.request_hash,
            request_id=request.request_id,
            tenant_id=request.tenant_id,
            user_id=request.user_id,
            route_hash=request.route_hash,
            policy_result_hash=request.policy_result_hash,
            selected_hat_id=request.selected_hat_id,
            selected_hat_version=request.selected_hat_version,
            selected_manifest_digest=request.selected_manifest_digest,
            hat_scope_id=request.hat_scope_id,
            effective_scope=request.effective_scope,
            knowledge_policy_decision=request.policy_result.knowledge_policy_decision,
            execution_authorization_decision=request.policy_result.execution_authorization_decision,
            answer_status=request.policy_result.answer_status,
            step18_result_hashes=tuple(sorted(lexical_result_hashes)),
            step19_vector_result_hash=(
                verified_vector[1].result_hash if verified_vector is not None else None
            ),
            embedding_model_id=spec.model_id,
            embedding_model_revision=spec.model_revision,
            embedding_model_digest=spec.model_digest,
            embedding_dimension=spec.embedding_dimension,
            vector_metric_policy=VECTOR_METRIC_POLICY,
            ranking_policy_id=ranking.policy_id,
            ranking_policy_version=ranking.policy_version,
            ranking_policy_digest=ranking.policy_digest,
            diversity_policy_digest=diversity_policy.policy_digest,
            context_budget_bytes=request.context_budget_bytes,
            maximum_bundle_items=request.maximum_bundle_items,
            requested_modalities=request.requested_modalities,
            context_bytes_used=budget.context_bytes_used,
            ordered_items=budget.items,
            candidates_before_dedup=before_dedup,
            candidates_after_dedup=len(ranked),
            candidates_after_diversity=len(diversity.selected),
            excluded_counts=excluded,
            available_modalities=available,
            missing_modalities=missing,
            truncated=truncated,
            retrieval_coverage=coverage,
            evidence_status=evidence_status,
            reason_codes=tuple(reasons),
        )
        return HybridEvidenceOutcome(
            hybrid_request_hash=request.request_hash,
            request_id=request.request_id,
            tenant_id=request.tenant_id,
            user_id=request.user_id,
            route_hash=request.route_hash,
            retrieval_coverage=coverage,
            evidence_status=evidence_status,
            bundle=bundle,
            reason_codes=tuple(reasons),
        )


__all__ = [
    "HybridEvidenceService",
    "LexicalInput",
    "PERSISTENCE_DECISION",
    "VectorInput",
]
