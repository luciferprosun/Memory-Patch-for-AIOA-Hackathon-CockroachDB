"""Step 20 hybrid fusion, diversity, budget, and bundle boundary tests."""

from __future__ import annotations

import ast
import hashlib
import inspect
import json
import unittest
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

from aioa_memory_kernel.contracts.enums import (
    AnswerStatus,
    EvidenceStatus,
    KnowledgeRoute,
    MemoryTargetScope,
    ScopeComparisonMode,
    ScopeValueType,
)
from aioa_memory_kernel.contracts.exceptions import ContractValidationError, IntegrityError
from aioa_memory_kernel.contracts.scope import ScopeDimension
from aioa_memory_kernel.contracts.serialization import canonical_json, canonical_sha256
from aioa_memory_kernel.embeddings import (
    VectorRetrievalCandidate,
    VectorRetrievalRequest,
    VectorRetrievalResult,
    load_approved_model_spec,
)
from aioa_memory_kernel.evidence import (
    DEFAULT_CONTEXT_BUDGET_BYTES,
    MAX_BUNDLE_ITEMS,
    MAX_CONTEXT_BUDGET_BYTES,
    MAX_EXACT_PRIORITY_ITEMS,
    MAX_EXCERPT_BYTES_PER_ITEM,
    MAX_ITEMS_PER_KNOWLEDGE_VERSION,
    MAX_ITEMS_PER_SOURCE,
    PERSISTENCE_DECISION,
    RRF_K,
    RRF_SCALE,
    STEP18_RETRIEVAL_POLICY_VERSION,
    CandidateIdentity,
    HybridEvidenceService,
    HybridModality,
    HybridRetrievalRequest,
    RetrievalCoverageStatus,
    Step20BoundaryError,
    Step20ReasonCode,
    assemble_budgeted_items,
    load_diversity_policy,
    load_ranking_policy,
    merge_and_rank_candidates,
    select_diverse_candidates,
    utf8_safe_prefix,
    verify_bundle_item_hash,
    verify_evidence_bundle_hash,
    verify_outcome_hash,
)
from aioa_memory_kernel.evidence.fusion import RankedCandidateInput
from aioa_memory_kernel.retrieval import (
    ExactIdentifierField,
    ExactIdentifierSelector,
    FullTextQuery,
    KeywordQuery,
    RetrievalCandidate,
    RetrievalMode,
    RetrievalRequest,
    RetrievalResult,
    StatuteSectionSelector,
    Step18ReasonCode,
    selector_hash,
)
from aioa_memory_kernel.routing import (
    EvidenceCoverageStatus,
    ExecutionAuthorizationDecision,
    KnowledgePolicyDecision,
    KnowledgeRouteResult,
    PolicyGateResult,
    Step17ReasonCode,
)
from aioa_memory_kernel.sources import (
    SourceAccessClass,
    SourceAuthorityLevel,
    SourcePublicationState,
)


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_ROOT = ROOT / "src/aioa_memory_kernel/evidence"
VALIDATION_EVIDENCE = ROOT / "docs/evidence/retrieval/step20-hybrid-evidence-bundle-validation.json"
STEP20_DOCUMENTS = (
    ROOT / "docs/architecture/HYBRID_RETRIEVAL_EVIDENCE_BUNDLE_DETERMINISTIC_RANKING_1A.md",
    ROOT / "docs/adr/ADR-027-hybrid-retrieval-evidence-bundle-deterministic-ranking.md",
    ROOT / "docs/operations/STEP_20_HYBRID_EVIDENCE_VALIDATION_1A.md",
    ROOT / "docs/audits/STEP_20_HYBRID_RETRIEVAL_EVIDENCE_BUNDLE_CLOSURE_1A.md",
)
SPEC = load_approved_model_spec()
DIGEST_A = "1" * 64
DIGEST_B = "2" * 64
DIGEST_C = "3" * 64
DIGEST_D = "4" * 64


def scope() -> tuple[ScopeDimension, ...]:
    return (
        ScopeDimension("legal_jurisdiction", "DE_FEDERAL", ScopeValueType.STRING, ScopeComparisonMode.EXACT, "request", True),
        ScopeDimension("legal_source_class", ("DE_FEDERAL_OFFICIAL_CONSOLIDATED_LAW",), ScopeValueType.STRING_SET, ScopeComparisonMode.IN_SET, "policy", True),
        ScopeDimension("source_language", "de", ScopeValueType.STRING, ScopeComparisonMode.EXACT, "request", True),
    )


def route(
    kind: KnowledgeRoute = KnowledgeRoute.HAT_ASSIST,
    *,
    tenant_id: str = "tenant-step20",
    user_id: str = "user-step20",
    request_id: str = "request-step20-1",
) -> KnowledgeRouteResult:
    selected = kind in {KnowledgeRoute.HAT_ASSIST, KnowledgeRoute.HAT_ENFORCE}
    reason = {
        KnowledgeRoute.HAT_ASSIST: Step17ReasonCode.SINGLE_ASSISTING_HAT,
        KnowledgeRoute.HAT_ENFORCE: Step17ReasonCode.MANDATORY_HAT_POLICY,
        KnowledgeRoute.PASS_THROUGH: Step17ReasonCode.NO_ELIGIBLE_HAT,
        KnowledgeRoute.AMBIGUOUS: Step17ReasonCode.MULTIPLE_HAT_CONFLICT,
    }[kind]
    return KnowledgeRouteResult(
        request_id=request_id,
        tenant_id=tenant_id,
        user_id=user_id,
        routing_input_hash=DIGEST_A,
        registry_snapshot_hash=DIGEST_B,
        knowledge_route=kind,
        selected_hat_id="german-law" if selected else None,
        selected_hat_version="1.0.0" if selected else None,
        selected_manifest_digest=DIGEST_C if selected else None,
        effective_scope=scope(),
        eligible_candidate_hashes=(DIGEST_A,) if selected else (),
        reason_codes=(reason,),
    )


def policy_result(route_value: KnowledgeRouteResult) -> PolicyGateResult:
    return PolicyGateResult(
        request_id=route_value.request_id,
        tenant_id=route_value.tenant_id,
        user_id=route_value.user_id,
        route_hash=route_value.route_hash,
        policy_context_hash=DIGEST_D,
        evidence_status=EvidenceStatus.INSUFFICIENT,
        evidence_coverage_status=EvidenceCoverageStatus.PARTIAL,
        knowledge_policy_decision=KnowledgePolicyDecision.REQUIRE_CONFIRMATION,
        execution_authorization_decision=ExecutionAuthorizationDecision.REQUIRE_HUMAN,
        answer_status=AnswerStatus.BLOCKED_NO_VERIFIED_EVIDENCE,
        permitted_execution_scope=(),
        reason_codes=(
            Step17ReasonCode.EVIDENCE_INSUFFICIENT,
            Step17ReasonCode.EXECUTION_REQUIRES_HUMAN,
        ),
    )


def selector(mode: RetrievalMode) -> object:
    if mode is RetrievalMode.EXACT_IDENTIFIER:
        return ExactIdentifierSelector(ExactIdentifierField.SOURCE_ID, ("source-1",))
    if mode is RetrievalMode.STATUTE_SECTION:
        return StatuteSectionSelector("BGB", "§ 1")
    if mode is RetrievalMode.FULL_TEXT:
        return FullTextQuery("Würde und Rechte")
    return KeywordQuery(("Rechte", "Würde"))


def lexical_request(
    mode: RetrievalMode,
    route_value: KnowledgeRouteResult | None = None,
) -> RetrievalRequest:
    selected = route_value or route()
    return RetrievalRequest(
        route=selected,
        tenant_id=selected.tenant_id,
        user_id=selected.user_id,
        request_id=selected.request_id,
        route_hash=selected.route_hash,
        selected_hat_id=selected.selected_hat_id,
        selected_hat_version=selected.selected_hat_version,
        selected_manifest_digest=selected.selected_manifest_digest,
        effective_scope=selected.effective_scope,
        hat_scope_id="german-law-global-1a" if selected.selected_hat_id else None,
        retrieval_mode=mode,
        selector=selector(mode),  # type: ignore[arg-type]
    )


def content_for(chunk_id: str) -> str:
    return f"{chunk_id}: Die Würde und die Rechte bleiben geschützt."


def lexical_candidate(
    mode: RetrievalMode,
    *,
    source_id: str = "source-1",
    version_id: str = "version-1",
    chunk_id: str = "chunk-1",
    chunk_ordinal: int = 0,
    content: str | None = None,
    authority_level: SourceAuthorityLevel = SourceAuthorityLevel.AUTHORITATIVE_SECONDARY,
    publication_state: SourcePublicationState = SourcePublicationState.PUBLISHED,
    authority_basis: object | None = None,
    structured_metadata: object | None = None,
) -> RetrievalCandidate:
    body = content if content is not None else content_for(chunk_id)
    return RetrievalCandidate(
        tenant_id="tenant-step20",
        hat_scope_id="german-law-global-1a",
        source_id=source_id,
        knowledge_version_id=version_id,
        chunk_id=chunk_id,
        chunk_ordinal=chunk_ordinal,
        content_sha256=hashlib.sha256(body.encode("utf-8")).hexdigest(),
        content=body,
        language_tag="de",
        authority_level=authority_level,
        authority_basis=authority_basis or {"official_identifier": "BJNR001950896"},  # type: ignore[arg-type]
        source_kind="DE_FEDERAL_OFFICIAL_CONSOLIDATED_LAW",
        source_reference=f"gii:{source_id}",
        publication_state=publication_state,
        access_class=SourceAccessClass.PUBLIC,
        target_scope=MemoryTargetScope.SHARED_KNOWLEDGE_HAT,
        owner_user_id=None,
        personal_memory_space_id=None,
        scope_digest=DIGEST_A,
        registry_digest=DIGEST_B,
        artifact_digest=DIGEST_C,
        snapshot_id=f"snapshot-{version_id}",
        structured_metadata=structured_metadata or {
            "version_ordinal": 1,
            "effective_from": "2024-01-01",
            "is_current": True,
        },  # type: ignore[arg-type]
        effective_scope=scope(),
        retrieval_mode=mode,
        retrieval_score="0.75" if mode in {RetrievalMode.FULL_TEXT, RetrievalMode.KEYWORD} else None,
    )


def lexical_pair(
    mode: RetrievalMode,
    candidates: tuple[RetrievalCandidate, ...] | None = None,
    *,
    route_value: KnowledgeRouteResult | None = None,
    truncated: bool = False,
) -> tuple[RetrievalRequest, RetrievalResult]:
    request_value = lexical_request(mode, route_value)
    selected = candidates if candidates is not None else (lexical_candidate(mode),)
    result = RetrievalResult(
        request_id=request_value.request_id,
        tenant_id=request_value.tenant_id,
        user_id=request_value.user_id,
        route_hash=request_value.route_hash,
        selected_hat_id=request_value.selected_hat_id,
        selected_hat_version=request_value.selected_hat_version,
        selected_manifest_digest=request_value.selected_manifest_digest,
        hat_scope_id=request_value.hat_scope_id,
        effective_scope=request_value.effective_scope,
        retrieval_mode=mode,
        query_digest=selector_hash(request_value.selector),
        candidates=selected,
        truncated=truncated,
        reason_codes=(
            (Step18ReasonCode.RETRIEVAL_OK,)
            if selected
            else (Step18ReasonCode.NO_MATCH,)
        ),
    )
    return request_value, result


def vector_request(route_value: KnowledgeRouteResult | None = None) -> VectorRetrievalRequest:
    selected = route_value or route()
    return VectorRetrievalRequest(
        route=selected,
        tenant_id=selected.tenant_id,
        user_id=selected.user_id,
        request_id=selected.request_id,
        route_hash=selected.route_hash,
        selected_hat_id=selected.selected_hat_id,
        selected_hat_version=selected.selected_hat_version,
        selected_manifest_digest=selected.selected_manifest_digest,
        effective_scope=selected.effective_scope,
        hat_scope_id="german-law-global-1a" if selected.selected_hat_id else None,
        query_text="Welche Rechte schützt § 1?",
        model_digest=SPEC.model_digest,
    )


def vector_candidate(
    *,
    source_id: str = "source-1",
    version_id: str = "version-1",
    chunk_id: str = "chunk-1",
    chunk_ordinal: int = 0,
    content: str | None = None,
    distance: str = "0.1",
    authority_basis: object | None = None,
    structured_metadata: object | None = None,
) -> VectorRetrievalCandidate:
    body = content if content is not None else content_for(chunk_id)
    return VectorRetrievalCandidate(
        tenant_id="tenant-step20",
        hat_scope_id="german-law-global-1a",
        source_id=source_id,
        knowledge_version_id=version_id,
        chunk_id=chunk_id,
        chunk_ordinal=chunk_ordinal,
        content_sha256=hashlib.sha256(body.encode("utf-8")).hexdigest(),
        content=body,
        language_tag="de",
        authority_level=SourceAuthorityLevel.AUTHORITATIVE_SECONDARY,
        authority_basis=authority_basis or {"official_identifier": "BJNR001950896"},  # type: ignore[arg-type]
        source_kind="DE_FEDERAL_OFFICIAL_CONSOLIDATED_LAW",
        source_reference=f"gii:{source_id}",
        publication_state=SourcePublicationState.PUBLISHED,
        access_class=SourceAccessClass.PUBLIC,
        target_scope=MemoryTargetScope.SHARED_KNOWLEDGE_HAT,
        owner_user_id=None,
        personal_memory_space_id=None,
        scope_digest=DIGEST_A,
        registry_digest=DIGEST_B,
        artifact_digest=DIGEST_C,
        snapshot_id=f"snapshot-{version_id}",
        structured_metadata=structured_metadata or {
            "version_ordinal": 1,
            "effective_from": "2024-01-01",
            "is_current": True,
        },  # type: ignore[arg-type]
        effective_scope=scope(),
        model_digest=SPEC.model_digest,
        embedding_bytes_sha256=DIGEST_D,
        vector_distance=distance,
    )


def vector_pair(
    candidates: tuple[VectorRetrievalCandidate, ...] | None = None,
    *,
    route_value: KnowledgeRouteResult | None = None,
    truncated: bool = False,
) -> tuple[VectorRetrievalRequest, VectorRetrievalResult]:
    request_value = vector_request(route_value)
    selected = candidates if candidates is not None else (vector_candidate(),)
    result = VectorRetrievalResult(
        request_id=request_value.request_id,
        tenant_id=request_value.tenant_id,
        user_id=request_value.user_id,
        route_hash=request_value.route_hash,
        selected_hat_id=request_value.selected_hat_id,
        selected_hat_version=request_value.selected_hat_version,
        selected_manifest_digest=request_value.selected_manifest_digest,
        hat_scope_id=request_value.hat_scope_id,
        effective_scope=request_value.effective_scope,
        model_digest=SPEC.model_digest,
        query_digest=request_value.query_digest,
        candidates=selected,
        truncated=truncated,
        reason_codes=(),
    )
    return request_value, result


def hybrid_request(
    lexical_inputs: tuple[tuple[RetrievalRequest, RetrievalResult], ...] = (),
    vector_input: tuple[VectorRetrievalRequest, VectorRetrievalResult] | None = None,
    *,
    route_value: KnowledgeRouteResult | None = None,
    requested_modalities: tuple[HybridModality, ...] | None = None,
    context_budget_bytes: int = DEFAULT_CONTEXT_BUDGET_BYTES,
    maximum_bundle_items: int = MAX_BUNDLE_ITEMS,
    **overrides: object,
) -> HybridRetrievalRequest:
    selected_route = route_value or (
        lexical_inputs[0][0].route
        if lexical_inputs
        else vector_input[0].route if vector_input is not None else route()
    )
    modes = tuple(
        HybridModality[pair[0].retrieval_mode.name] for pair in lexical_inputs
    ) + ((HybridModality.VECTOR,) if vector_input is not None else ())
    ranking = load_ranking_policy()
    values: dict[str, object] = {
        "route": selected_route,
        "policy_result": policy_result(selected_route),
        "tenant_id": selected_route.tenant_id,
        "user_id": selected_route.user_id,
        "request_id": selected_route.request_id,
        "route_hash": selected_route.route_hash,
        "policy_result_hash": policy_result(selected_route).policy_result_hash,
        "selected_hat_id": selected_route.selected_hat_id,
        "selected_hat_version": selected_route.selected_hat_version,
        "selected_manifest_digest": selected_route.selected_manifest_digest,
        "hat_scope_id": "german-law-global-1a" if selected_route.selected_hat_id else None,
        "effective_scope": selected_route.effective_scope,
        "personal_memory_space_id": None,
        "requested_modalities": requested_modalities if requested_modalities is not None else modes,
        "lexical_request_hashes": tuple(pair[0].request_hash for pair in lexical_inputs),
        "lexical_result_hashes": tuple(pair[1].result_hash for pair in lexical_inputs),
        "vector_request_hash": vector_input[0].request_hash if vector_input else None,
        "vector_result_hash": vector_input[1].result_hash if vector_input else None,
        "embedding_model_digest": SPEC.model_digest,
        "step18_retrieval_policy_version": STEP18_RETRIEVAL_POLICY_VERSION,
        "ranking_policy_id": ranking.policy_id,
        "ranking_policy_version": ranking.policy_version,
        "ranking_policy_digest": ranking.policy_digest,
        "diversity_policy_digest": load_diversity_policy().policy_digest,
        "context_budget_bytes": context_budget_bytes,
        "maximum_bundle_items": maximum_bundle_items,
    }
    values.update(overrides)
    return HybridRetrievalRequest(**values)  # type: ignore[arg-type]


def assemble(
    lexical_inputs: tuple[tuple[RetrievalRequest, RetrievalResult], ...],
    vector_input: tuple[VectorRetrievalRequest, VectorRetrievalResult] | None = None,
    **request_overrides: object,
):
    request_value = hybrid_request(
        lexical_inputs,
        vector_input,
        **request_overrides,
    )
    return HybridEvidenceService().assemble(
        request_value,
        lexical_inputs=lexical_inputs,
        vector_input=vector_input,
    )


class InputBindingTests(unittest.TestCase):
    def test_valid_step18_and_step19_results_are_accepted(self) -> None:
        exact = lexical_pair(RetrievalMode.EXACT_IDENTIFIER)
        vector = vector_pair()
        outcome = assemble((exact,), vector)
        self.assertEqual(outcome.bundle.candidates_after_dedup, 1)

    def test_hat_enforce_is_accepted_without_execution_authority(self) -> None:
        selected_route = route(KnowledgeRoute.HAT_ENFORCE)
        exact = lexical_pair(RetrievalMode.EXACT_IDENTIFIER, route_value=selected_route)
        outcome = assemble((exact,), None, route_value=selected_route)
        self.assertEqual(outcome.bundle.execution_authorization_decision, ExecutionAuthorizationDecision.REQUIRE_HUMAN)

    def test_pass_through_produces_no_hat_bundle(self) -> None:
        selected_route = route(KnowledgeRoute.PASS_THROUGH)
        request_value = hybrid_request(route_value=selected_route, requested_modalities=())
        outcome = HybridEvidenceService().assemble(request_value)
        self.assertIsNone(outcome.bundle)
        self.assertEqual(outcome.reason_codes, (Step20ReasonCode.NO_HAT_SELECTED,))

    def test_ambiguous_route_is_denied(self) -> None:
        selected_route = route(KnowledgeRoute.AMBIGUOUS)
        request_value = hybrid_request(route_value=selected_route, requested_modalities=())
        with self.assertRaisesRegex(Step20BoundaryError, "AMBIGUOUS_ROUTE"):
            HybridEvidenceService().assemble(request_value)

    def test_stale_route_and_policy_hashes_are_denied(self) -> None:
        pair = lexical_pair(RetrievalMode.EXACT_IDENTIFIER)
        request_value = hybrid_request((pair,))
        object.__setattr__(request_value.route, "route_hash", DIGEST_D)
        with self.assertRaisesRegex(Step20BoundaryError, "HYBRID_INPUT_HASH_INVALID"):
            HybridEvidenceService().assemble(request_value, lexical_inputs=(pair,))

    def test_stale_upstream_request_result_and_candidate_hashes_are_denied(self) -> None:
        mutations = ("request", "result", "candidate")
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                pair = lexical_pair(RetrievalMode.FULL_TEXT)
                request_value = hybrid_request((pair,))
                target = {"request": pair[0], "result": pair[1], "candidate": pair[1].candidates[0]}[mutation]
                field = {"request": "request_hash", "result": "result_hash", "candidate": "candidate_hash"}[mutation]
                object.__setattr__(target, field, DIGEST_D)
                with self.assertRaisesRegex(Step20BoundaryError, "HYBRID_INPUT_HASH_INVALID"):
                    HybridEvidenceService().assemble(request_value, lexical_inputs=(pair,))

    def test_identity_field_mismatches_fail_closed(self) -> None:
        pair = lexical_pair(RetrievalMode.EXACT_IDENTIFIER)
        fields = {
            "request_id": "other-request",
            "tenant_id": "other-tenant",
            "user_id": "other-user",
            "route_hash": DIGEST_D,
            "selected_hat_id": "other-hat",
            "selected_hat_version": "2.0.0",
            "selected_manifest_digest": DIGEST_D,
            "hat_scope_id": "other-hat-scope",
        }
        for field_name, value in fields.items():
            with self.subTest(field=field_name):
                pair = lexical_pair(RetrievalMode.EXACT_IDENTIFIER)
                changed_result = pair[1]
                object.__setattr__(changed_result, field_name, value)
                object.__setattr__(
                    changed_result,
                    "result_hash",
                    canonical_sha256(changed_result, exclude_fields=("result_hash",)),
                )
                changed_pair = (pair[0], changed_result)
                request_value = hybrid_request((pair,))
                object.__setattr__(request_value, "lexical_result_hashes", (changed_result.result_hash,))
                object.__setattr__(request_value, "request_hash", canonical_sha256(request_value, exclude_fields=("request_hash",)))
                with self.assertRaisesRegex(Step20BoundaryError, "HYBRID_INPUT_BINDING_MISMATCH"):
                    HybridEvidenceService().assemble(request_value, lexical_inputs=(changed_pair,))

    def test_effective_scope_mismatch_is_denied(self) -> None:
        pair = lexical_pair(RetrievalMode.EXACT_IDENTIFIER)
        other_scope = tuple((*scope(), ScopeDimension("topic", "other", ScopeValueType.STRING, ScopeComparisonMode.EXACT, "request", True)))
        other_scope = tuple(sorted(other_scope, key=lambda item: item.name))
        changed = pair[1]
        object.__setattr__(changed, "effective_scope", other_scope)
        object.__setattr__(
            changed,
            "result_hash",
            canonical_sha256(changed, exclude_fields=("result_hash",)),
        )
        request_value = hybrid_request((pair,))
        object.__setattr__(request_value, "lexical_result_hashes", (changed.result_hash,))
        object.__setattr__(request_value, "request_hash", canonical_sha256(request_value, exclude_fields=("request_hash",)))
        with self.assertRaisesRegex(Step20BoundaryError, "HYBRID_INPUT_BINDING_MISMATCH"):
            HybridEvidenceService().assemble(request_value, lexical_inputs=((pair[0], changed),))

    def test_embedding_model_digest_mismatch_is_denied(self) -> None:
        pair = vector_pair()
        request_value = hybrid_request((), pair)
        object.__setattr__(pair[1], "model_digest", DIGEST_A)
        object.__setattr__(pair[1], "result_hash", canonical_sha256(pair[1], exclude_fields=("result_hash",)))
        object.__setattr__(request_value, "vector_result_hash", pair[1].result_hash)
        object.__setattr__(request_value, "request_hash", canonical_sha256(request_value, exclude_fields=("request_hash",)))
        with self.assertRaisesRegex(Step20BoundaryError, "HYBRID_MODEL_MISMATCH"):
            HybridEvidenceService().assemble(request_value, vector_input=pair)

    def test_duplicate_replayed_result_is_not_counted_twice(self) -> None:
        pair = lexical_pair(RetrievalMode.FULL_TEXT)
        request_value = hybrid_request((pair,))
        outcome = HybridEvidenceService().assemble(request_value, lexical_inputs=(pair, pair))
        self.assertEqual(outcome.bundle.candidates_before_dedup, 1)


class FusionAndDeduplicationTests(unittest.TestCase):
    def test_exact_and_full_text_duplicate_becomes_one_candidate(self) -> None:
        exact = lexical_pair(RetrievalMode.EXACT_IDENTIFIER)
        full = lexical_pair(RetrievalMode.FULL_TEXT)
        outcome = assemble((full, exact))
        item = outcome.bundle.ordered_items[0]
        self.assertEqual(len(item.contributions), 2)
        self.assertEqual(item.match_class, 1)

    def test_full_text_and_vector_duplicate_becomes_one_candidate(self) -> None:
        full = lexical_pair(RetrievalMode.FULL_TEXT)
        vector = vector_pair()
        outcome = assemble((full,), vector)
        self.assertEqual(outcome.bundle.candidates_after_dedup, 1)
        self.assertEqual({item.modality for item in outcome.bundle.ordered_items[0].contributions}, {HybridModality.FULL_TEXT, HybridModality.VECTOR})

    def test_same_chunk_id_with_changed_content_is_not_merged(self) -> None:
        full = lexical_pair(RetrievalMode.FULL_TEXT, (lexical_candidate(RetrievalMode.FULL_TEXT, content="Alte Fassung"),))
        vector = vector_pair((vector_candidate(content="Neue Fassung"),))
        outcome = assemble((full,), vector)
        self.assertEqual(outcome.bundle.candidates_after_dedup, 2)

    def test_conflicting_security_metadata_fails_closed(self) -> None:
        full = lexical_pair(RetrievalMode.FULL_TEXT)
        vector = vector_pair((vector_candidate(authority_basis={"official_identifier": "DIFFERENT"}),))
        with self.assertRaisesRegex(Step20BoundaryError, "HYBRID_CANDIDATE_METADATA_CONFLICT"):
            assemble((full,), vector)

    def test_different_knowledge_versions_remain_separate(self) -> None:
        full_candidates = (
            lexical_candidate(RetrievalMode.FULL_TEXT, version_id="version-1", chunk_id="chunk-1"),
            lexical_candidate(RetrievalMode.FULL_TEXT, version_id="version-2", chunk_id="chunk-2"),
        )
        outcome = assemble((lexical_pair(RetrievalMode.FULL_TEXT, full_candidates),))
        self.assertEqual(outcome.bundle.candidates_after_dedup, 2)

    def test_fixed_integer_fusion_is_deterministic(self) -> None:
        exact = lexical_pair(RetrievalMode.EXACT_IDENTIFIER)
        full = lexical_pair(RetrievalMode.FULL_TEXT)
        first = assemble((exact, full))
        second = assemble((full, exact))
        self.assertEqual(first.bundle.bundle_hash, second.bundle.bundle_hash)
        expected = (RRF_SCALE * 8) // (RRF_K + 1) + (RRF_SCALE * 4) // (RRF_K + 1)
        self.assertEqual(first.bundle.ordered_items[0].fused_score, expected)

    def test_exact_structured_matches_outrank_vector_only(self) -> None:
        exact_candidate = lexical_candidate(RetrievalMode.STATUTE_SECTION, source_id="source-z", chunk_id="chunk-z")
        vector_only = vector_candidate(source_id="source-a", chunk_id="chunk-a", distance="0")
        outcome = assemble(
            (lexical_pair(RetrievalMode.STATUTE_SECTION, (exact_candidate,)),),
            vector_pair((vector_only,)),
        )
        self.assertEqual(outcome.bundle.ordered_items[0].identity.chunk_id, "chunk-z")

    def test_multimodal_support_uses_rank_not_raw_score_comparison(self) -> None:
        full = lexical_pair(RetrievalMode.FULL_TEXT)
        keyword = lexical_pair(RetrievalMode.KEYWORD)
        vector = vector_pair((vector_candidate(source_id="source-vector", chunk_id="chunk-vector", distance="0"),))
        outcome = assemble((full, keyword), vector)
        self.assertEqual(outcome.bundle.ordered_items[0].match_class, 2)
        self.assertNotIn("0.75", str(outcome.bundle.ordered_items[0].fused_score))

    def test_no_native_float_enters_bundle_hash_material(self) -> None:
        outcome = assemble((lexical_pair(RetrievalMode.FULL_TEXT),), vector_pair())
        canonical = canonical_json(outcome.bundle)
        self.assertNotIn("NaN", canonical)
        self.assertNotIn("Infinity", canonical)
        self.assertTrue(all(isinstance(item.fused_score, int) for item in outcome.bundle.ordered_items))

    def test_ranking_policy_is_fixed_and_immutable(self) -> None:
        policy = load_ranking_policy()
        self.assertEqual((policy.rrf_k, policy.rrf_scale), (60, 1_000_000_000))
        self.assertEqual(policy.modality_weights, {"STATUTE_SECTION": 8, "EXACT_IDENTIFIER": 8, "FULL_TEXT": 4, "VECTOR": 3, "KEYWORD": 2})
        with self.assertRaises(FrozenInstanceError):
            policy.rrf_k = 1  # type: ignore[misc]

    def test_vector_only_candidate_cannot_claim_an_exact_match_class(self) -> None:
        pair = vector_pair()
        request_value = hybrid_request((), pair)
        ranked = merge_and_rank_candidates(
            request_value,
            (
                RankedCandidateInput(
                    modality=HybridModality.VECTOR,
                    upstream_request_hash=pair[0].request_hash,
                    upstream_result_hash=pair[1].result_hash,
                    one_based_rank=1,
                    candidate=pair[1].candidates[0],
                ),
            ),
        )
        self.assertEqual(ranked[0].match_class, 4)
        with self.assertRaises(ContractValidationError):
            replace(ranked[0], match_class=0)


class AuthorityAndIsolationTests(unittest.TestCase):
    def _tampered_candidate_denied(self, field_name: str, value: object) -> None:
        pair = lexical_pair(RetrievalMode.FULL_TEXT)
        candidate = pair[1].candidates[0]
        object.__setattr__(candidate, field_name, value)
        object.__setattr__(candidate, "candidate_hash", canonical_sha256(candidate, exclude_fields=("candidate_hash",)))
        object.__setattr__(pair[1], "result_hash", canonical_sha256(pair[1], exclude_fields=("result_hash",)))
        request_value = hybrid_request((pair,))
        with self.assertRaises(Step20BoundaryError):
            HybridEvidenceService().assemble(request_value, lexical_inputs=(pair,))

    def test_wrong_tenant_and_hat_scope_are_rejected_before_fusion(self) -> None:
        for field_name, value in (("tenant_id", "tenant-other"), ("hat_scope_id", "other-hat")):
            with self.subTest(field=field_name):
                self._tampered_candidate_denied(field_name, value)

    def test_unpublished_states_are_rejected_before_fusion(self) -> None:
        for state in (
            SourcePublicationState.QUARANTINED,
            SourcePublicationState.WITHDRAWN,
            SourcePublicationState.REJECTED,
        ):
            with self.subTest(state=state):
                with self.assertRaises(Exception):
                    lexical_candidate(RetrievalMode.FULL_TEXT, publication_state=state)

    def test_weak_authority_is_rejected_before_fusion(self) -> None:
        with self.assertRaises(Exception):
            lexical_candidate(RetrievalMode.FULL_TEXT, authority_level=SourceAuthorityLevel.INFORMATIONAL_SECONDARY)

    def test_vector_similarity_cannot_admit_rejected_source(self) -> None:
        pair = vector_pair()
        candidate = pair[1].candidates[0]
        object.__setattr__(candidate, "publication_state", SourcePublicationState.REJECTED)
        object.__setattr__(candidate, "candidate_hash", canonical_sha256(candidate, exclude_fields=("candidate_hash",)))
        object.__setattr__(pair[1], "result_hash", canonical_sha256(pair[1], exclude_fields=("result_hash",)))
        request_value = hybrid_request((), pair)
        with self.assertRaisesRegex(Step20BoundaryError, "HYBRID_CANDIDATE_INVALID"):
            HybridEvidenceService().assemble(request_value, vector_input=pair)

    def test_private_owner_and_personal_scope_are_not_weakened(self) -> None:
        pair = lexical_pair(RetrievalMode.EXACT_IDENTIFIER)
        self._tampered_candidate_denied("access_class", SourceAccessClass.USER_PRIVATE)
        self.assertEqual(pair[1].candidates[0].target_scope, MemoryTargetScope.SHARED_KNOWLEDGE_HAT)

    def test_bundle_preserves_policy_but_grants_no_authority(self) -> None:
        outcome = assemble((lexical_pair(RetrievalMode.EXACT_IDENTIFIER),))
        bundle = outcome.bundle
        self.assertEqual(bundle.knowledge_policy_decision, KnowledgePolicyDecision.REQUIRE_CONFIRMATION)
        self.assertEqual(bundle.execution_authorization_decision, ExecutionAuthorizationDecision.REQUIRE_HUMAN)
        for forbidden in ("execute", "invoke", "approve", "dispatch", "run"):
            self.assertFalse(hasattr(bundle, forbidden))

    def test_secret_shaped_metadata_is_rejected(self) -> None:
        candidate = lexical_candidate(
            RetrievalMode.FULL_TEXT,
            structured_metadata={"version_ordinal": 1, "api_token": "secret"},
        )
        pair = lexical_pair(RetrievalMode.FULL_TEXT, (candidate,))
        with self.assertRaises(ContractValidationError):
            assemble((pair,))


class DiversityTests(unittest.TestCase):
    def test_per_source_cap_and_global_limit_are_enforced(self) -> None:
        candidates = tuple(
            lexical_candidate(RetrievalMode.FULL_TEXT, chunk_id=f"chunk-{index}", chunk_ordinal=index)
            for index in range(6)
        )
        outcome = assemble((lexical_pair(RetrievalMode.FULL_TEXT, candidates),))
        self.assertEqual(len(outcome.bundle.ordered_items), MAX_ITEMS_PER_SOURCE)
        self.assertEqual(outcome.bundle.excluded_counts[Step20ReasonCode.DIVERSITY_SOURCE_CAP.value], 3)

    def test_per_version_cap_is_enforced_across_sources(self) -> None:
        candidates = tuple(
            lexical_candidate(RetrievalMode.FULL_TEXT, source_id=f"source-{index}", chunk_id=f"chunk-{index}", version_id="version-common", chunk_ordinal=index)
            for index in range(6)
        )
        outcome = assemble((lexical_pair(RetrievalMode.FULL_TEXT, candidates),))
        self.assertEqual(len(outcome.bundle.ordered_items), MAX_ITEMS_PER_KNOWLEDGE_VERSION)
        self.assertEqual(outcome.bundle.excluded_counts[Step20ReasonCode.DIVERSITY_VERSION_CAP.value], 2)

    def test_exact_priority_cap_is_honest(self) -> None:
        candidates = tuple(
            lexical_candidate(RetrievalMode.EXACT_IDENTIFIER, source_id=f"source-{index}", version_id=f"version-{index}", chunk_id=f"chunk-{index}", chunk_ordinal=index)
            for index in range(MAX_EXACT_PRIORITY_ITEMS + 1)
        )
        outcome = assemble((lexical_pair(RetrievalMode.EXACT_IDENTIFIER, candidates),))
        self.assertEqual(len(outcome.bundle.ordered_items), MAX_EXACT_PRIORITY_ITEMS)
        self.assertTrue(outcome.bundle.truncated)
        self.assertEqual(outcome.bundle.excluded_counts[Step20ReasonCode.DIVERSITY_EXACT_CAP.value], 1)

    def test_round_robin_is_deterministic_across_sources(self) -> None:
        candidates = tuple(
            lexical_candidate(RetrievalMode.FULL_TEXT, source_id=source_id, version_id=f"version-{source_id}-{index}", chunk_id=f"chunk-{source_id}-{index}", chunk_ordinal=index)
            for source_id in ("source-a", "source-b")
            for index in range(3)
        )
        pair = lexical_pair(RetrievalMode.FULL_TEXT, candidates)
        first = assemble((pair,))
        second = assemble((pair,))
        self.assertEqual(first.bundle.bundle_hash, second.bundle.bundle_hash)
        sources = tuple(item.identity.source_id for item in first.bundle.ordered_items)
        self.assertEqual(sources[:4], ("source-a", "source-b", "source-a", "source-b"))

    def test_caller_maximum_bundle_items_may_reduce_not_expand_policy(self) -> None:
        candidates = tuple(
            lexical_candidate(RetrievalMode.FULL_TEXT, source_id=f"source-{index}", version_id=f"version-{index}", chunk_id=f"chunk-{index}", chunk_ordinal=index)
            for index in range(5)
        )
        outcome = assemble((lexical_pair(RetrievalMode.FULL_TEXT, candidates),), maximum_bundle_items=2)
        self.assertEqual(len(outcome.bundle.ordered_items), 2)
        with self.assertRaises(ContractValidationError):
            hybrid_request((lexical_pair(RetrievalMode.FULL_TEXT),), maximum_bundle_items=MAX_BUNDLE_ITEMS + 1)


class ContextBudgetTests(unittest.TestCase):
    def test_full_content_is_used_when_it_fits(self) -> None:
        outcome = assemble((lexical_pair(RetrievalMode.EXACT_IDENTIFIER),))
        item = outcome.bundle.ordered_items[0]
        self.assertFalse(item.excerpt.truncated)
        self.assertEqual(item.excerpt.text, content_for("chunk-1"))

    def test_utf8_safe_german_excerpt_and_hashes(self) -> None:
        text = "äöüß" * 3000
        candidate = lexical_candidate(RetrievalMode.FULL_TEXT, content=text)
        outcome = assemble((lexical_pair(RetrievalMode.FULL_TEXT, (candidate,)),), context_budget_bytes=300)
        excerpt = outcome.bundle.ordered_items[0].excerpt
        excerpt.text.encode("utf-8").decode("utf-8")
        self.assertEqual(excerpt.end_byte, excerpt.utf8_byte_length)
        self.assertEqual(excerpt.excerpt_sha256, hashlib.sha256(excerpt.text.encode("utf-8")).hexdigest())
        self.assertEqual(excerpt.full_content_sha256, candidate.content_sha256)

    def test_too_small_remaining_budget_skips_deterministically(self) -> None:
        long_text = "Würde " * 200
        candidates = (
            lexical_candidate(RetrievalMode.FULL_TEXT, source_id="source-a", version_id="version-a", chunk_id="chunk-a", content=long_text),
            lexical_candidate(RetrievalMode.FULL_TEXT, source_id="source-b", version_id="version-b", chunk_id="chunk-b", content=long_text),
        )
        outcome = assemble((lexical_pair(RetrievalMode.FULL_TEXT, candidates),), context_budget_bytes=300)
        self.assertEqual(len(outcome.bundle.ordered_items), 1)
        self.assertGreater(outcome.bundle.excluded_counts[Step20ReasonCode.CONTEXT_BUDGET_EXCLUDED.value], 0)

    def test_budget_maximum_is_fail_closed(self) -> None:
        pair = lexical_pair(RetrievalMode.FULL_TEXT)
        with self.assertRaises(ContractValidationError):
            hybrid_request((pair,), context_budget_bytes=MAX_CONTEXT_BUDGET_BYTES + 1)

    def test_per_item_excerpt_limit_is_enforced(self) -> None:
        content = "Recht " * 3000
        outcome = assemble((lexical_pair(RetrievalMode.FULL_TEXT, (lexical_candidate(RetrievalMode.FULL_TEXT, content=content),)),))
        self.assertLessEqual(outcome.bundle.ordered_items[0].excerpt.utf8_byte_length, MAX_EXCERPT_BYTES_PER_ITEM)

    def test_repeated_budget_assembly_is_byte_identical(self) -> None:
        prefix = utf8_safe_prefix("Würde" * 1000, 257)
        self.assertEqual(prefix, utf8_safe_prefix("Würde" * 1000, 257))
        self.assertLessEqual(len(prefix.encode("utf-8")), 257)


class BundleContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.outcome = assemble(
            (lexical_pair(RetrievalMode.EXACT_IDENTIFIER), lexical_pair(RetrievalMode.FULL_TEXT)),
            vector_pair(),
        )

    def test_bundle_and_items_are_deeply_immutable(self) -> None:
        with self.assertRaises(FrozenInstanceError):
            self.outcome.bundle.truncated = True  # type: ignore[misc]
        with self.assertRaises(FrozenInstanceError):
            self.outcome.bundle.ordered_items[0].fused_score = 0  # type: ignore[misc]
        with self.assertRaises(TypeError):
            self.outcome.bundle.ordered_items[0].structured_metadata["x"] = "y"  # type: ignore[index]

    def test_bundle_item_and_outcome_hashes_verify(self) -> None:
        verify_bundle_item_hash(self.outcome.bundle.ordered_items[0])
        verify_evidence_bundle_hash(self.outcome.bundle)
        verify_outcome_hash(self.outcome)

    def test_stale_item_and_bundle_hashes_are_rejected(self) -> None:
        item = self.outcome.bundle.ordered_items[0]
        object.__setattr__(item, "item_hash", DIGEST_A)
        with self.assertRaises(IntegrityError):
            verify_bundle_item_hash(item)
        object.__setattr__(self.outcome.bundle, "bundle_hash", DIGEST_B)
        with self.assertRaises(IntegrityError):
            verify_evidence_bundle_hash(self.outcome.bundle)

    def test_route_model_policy_and_upstream_hashes_participate(self) -> None:
        bundle = self.outcome.bundle
        self.assertEqual(bundle.route_hash, route().route_hash)
        self.assertEqual(bundle.embedding_model_digest, SPEC.model_digest)
        self.assertEqual(bundle.input_result_hash_count, 3)
        self.assertEqual(bundle.ranking_policy_digest, load_ranking_policy().policy_digest)

    def test_bundle_id_is_derived_from_hash(self) -> None:
        self.assertEqual(self.outcome.bundle.evidence_bundle_id, f"evidence-bundle:{self.outcome.bundle.bundle_hash}")

    def test_bundle_binds_step20_request_and_reduced_limits(self) -> None:
        pair = lexical_pair(RetrievalMode.EXACT_IDENTIFIER)
        request_value = hybrid_request((pair,), maximum_bundle_items=1)
        outcome = HybridEvidenceService().assemble(
            request_value,
            lexical_inputs=(pair,),
        )
        self.assertEqual(outcome.hybrid_request_hash, request_value.request_hash)
        self.assertEqual(outcome.bundle.hybrid_request_hash, request_value.request_hash)
        self.assertEqual(outcome.bundle.maximum_bundle_items, 1)
        self.assertEqual(
            outcome.bundle.requested_modalities,
            (HybridModality.EXACT_IDENTIFIER,),
        )

    def test_final_item_revalidates_authority_semantics(self) -> None:
        item = self.outcome.bundle.ordered_items[0]
        with self.assertRaises(Step20BoundaryError):
            replace(
                item,
                authority_level=SourceAuthorityLevel.INFORMATIONAL_SECONDARY,
            )

    def test_candidate_identity_is_exact_lineage_and_content(self) -> None:
        identity = self.outcome.bundle.ordered_items[0].identity
        self.assertEqual((identity.tenant_id, identity.hat_scope_id, identity.source_id, identity.knowledge_version_id, identity.chunk_id), ("tenant-step20", "german-law-global-1a", "source-1", "version-1", "chunk-1"))

    def test_no_runtime_object_callable_or_secret_is_exposed(self) -> None:
        bundle = self.outcome.bundle
        self.assertFalse(any(callable(getattr(bundle, name)) for name in bundle.__slots__ if hasattr(bundle, name)))
        serialized = canonical_json(bundle).lower()
        for forbidden in ("password", "api_key", "private_key", "credential"):
            self.assertNotIn(forbidden, serialized)

    def test_in_memory_replay_is_deterministic(self) -> None:
        replay = assemble(
            (lexical_pair(RetrievalMode.FULL_TEXT), lexical_pair(RetrievalMode.EXACT_IDENTIFIER)),
            vector_pair(),
        )
        self.assertEqual(self.outcome.bundle.bundle_hash, replay.bundle.bundle_hash)


class CoverageAndBoundaryTests(unittest.TestCase):
    def test_zero_candidates_is_empty_and_insufficient(self) -> None:
        pair = lexical_pair(RetrievalMode.FULL_TEXT, ())
        outcome = assemble((pair,))
        self.assertEqual(outcome.retrieval_coverage, RetrievalCoverageStatus.EMPTY)
        self.assertEqual(outcome.evidence_status, EvidenceStatus.INSUFFICIENT)

    def test_missing_requested_vector_is_partial_not_false_complete(self) -> None:
        pair = lexical_pair(RetrievalMode.FULL_TEXT)
        request_value = hybrid_request((pair,), requested_modalities=(HybridModality.FULL_TEXT, HybridModality.VECTOR))
        outcome = HybridEvidenceService().assemble(request_value, lexical_inputs=(pair,))
        self.assertEqual(outcome.retrieval_coverage, RetrievalCoverageStatus.PARTIAL)
        self.assertEqual(outcome.bundle.missing_modalities, (HybridModality.VECTOR,))

    def test_upstream_or_context_truncation_is_partial(self) -> None:
        pair = lexical_pair(RetrievalMode.FULL_TEXT, truncated=True)
        outcome = assemble((pair,))
        self.assertEqual(outcome.retrieval_coverage, RetrievalCoverageStatus.PARTIAL)
        self.assertTrue(outcome.bundle.truncated)

    def test_complete_means_complete_only_for_bounded_requested_modes(self) -> None:
        outcome = assemble((lexical_pair(RetrievalMode.EXACT_IDENTIFIER),))
        self.assertEqual(outcome.retrieval_coverage, RetrievalCoverageStatus.COMPLETE)
        self.assertEqual(outcome.evidence_status, EvidenceStatus.SUFFICIENT)

    def test_integrity_failure_exposes_invalid_status_only_on_error(self) -> None:
        pair = lexical_pair(RetrievalMode.EXACT_IDENTIFIER)
        request_value = hybrid_request((pair,))
        object.__setattr__(pair[1], "result_hash", DIGEST_D)
        with self.assertRaises(Step20BoundaryError) as caught:
            HybridEvidenceService().assemble(request_value, lexical_inputs=(pair,))
        self.assertEqual(caught.exception.retrieval_coverage, RetrievalCoverageStatus.INVALID)
        self.assertEqual(caught.exception.evidence_status, EvidenceStatus.INVALID)

    def test_answer_and_evidence_status_remain_distinct(self) -> None:
        outcome = assemble((lexical_pair(RetrievalMode.EXACT_IDENTIFIER),))
        self.assertEqual(outcome.evidence_status, EvidenceStatus.SUFFICIENT)
        self.assertEqual(outcome.bundle.answer_status, AnswerStatus.BLOCKED_NO_VERIFIED_EVIDENCE)
        self.assertNotEqual(outcome.evidence_status.value, outcome.bundle.answer_status.value)

    def test_step20_does_not_manufacture_temporal_verdicts(self) -> None:
        outcome = assemble((lexical_pair(RetrievalMode.EXACT_IDENTIFIER),))
        self.assertNotIn(outcome.evidence_status, {EvidenceStatus.STALE, EvidenceStatus.CONFLICTING})
        metadata = outcome.bundle.ordered_items[0].structured_metadata
        self.assertIn("effective_from", metadata)


class PersistenceAndStaticBoundaryTests(unittest.TestCase):
    def test_existing_step4_persistence_is_not_misbound(self) -> None:
        self.assertEqual(PERSISTENCE_DECISION, "NOT_APPLICABLE_STEP4_REQUIRES_EXISTING_KERNEL_RUN_BINDING")
        self.assertFalse((EVIDENCE_ROOT / "repository.py").exists())

    def test_no_temporal_resolver_or_reranker_is_added(self) -> None:
        names = {path.name for path in EVIDENCE_ROOT.glob("*.py")}
        self.assertNotIn("temporal.py", names)
        self.assertNotIn("freshness.py", names)
        source = "\n".join(path.read_text(encoding="utf-8") for path in EVIDENCE_ROOT.glob("*.py"))
        for forbidden in ("cross_encoder", "llm_rerank", "remote_model", "provider_call", "random.shuffle"):
            self.assertNotIn(forbidden, source.lower())

    def test_no_network_provider_or_execution_imports(self) -> None:
        forbidden = {"requests", "httpx", "urllib", "socket", "subprocess", "boto3", "openai", "anthropic"}
        imports: set[str] = set()
        for path in EVIDENCE_ROOT.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports.update(alias.name.split(".")[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imports.add(node.module.split(".")[0])
        self.assertFalse(imports & forbidden)

    def test_no_database_or_migration_capability_in_service(self) -> None:
        source = inspect.getsource(HybridEvidenceService)
        for forbidden in ("INSERT", "UPDATE", "DELETE", "CREATE TABLE", "cursor", "transaction"):
            self.assertNotIn(forbidden, source)

    def test_controlled_validation_evidence_is_hash_bound_and_passed(self) -> None:
        value = json.loads(VALIDATION_EVIDENCE.read_text(encoding="utf-8"))
        claimed = value.pop("validation_digest")
        self.assertEqual(claimed, canonical_sha256(value))
        self.assertEqual(value["status"], "PASS")
        self.assertFalse(value["boundaries"]["step21_started"])
        self.assertEqual(value["evidence_bundle"]["deterministic_replay"], "PASS")

    def test_step20_documentation_freezes_step21_boundary(self) -> None:
        for path in STEP20_DOCUMENTS:
            self.assertTrue(path.is_file(), path)
            text = path.read_text(encoding="utf-8")
            self.assertIn("Step 21", text)
            self.assertIn("NOT STARTED", text)

    def test_critical_path_preserves_step19_before_step20(self) -> None:
        roadmap = (ROOT / "docs/roadmap/PRODUCTION_ROADMAP.md").read_text(
            encoding="utf-8"
        )
        critical_path = roadmap.split("# ŚCIEŻKA KRYTYCZNA", maxsplit=1)[1]
        self.assertIn("→ 18\n→ 19\n→ 20\n→ 21", critical_path)

    def test_no_step20_database_migration_was_added(self) -> None:
        migrations = ROOT / "sql/cockroachdb/migrations"
        self.assertFalse(any("step20" in path.name.lower() for path in migrations.glob("*.sql")))


if __name__ == "__main__":
    unittest.main()
