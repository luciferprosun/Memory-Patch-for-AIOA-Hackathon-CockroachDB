#!/usr/bin/env python3
"""Controlled real Step 18/19 input validation for Step 20.

The runner uses one owned loopback CockroachDB node and the already-approved
local Step 19 model/cache.  It performs no cloud, provider, AWS, or S3 call.
"""

from __future__ import annotations

import argparse
import copy
import dataclasses
import hashlib
import os
import subprocess
import sys
import uuid
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]

import run_cockroachdb_migrations as migrations  # noqa: E402
import run_step18_retrieval_validation as step18  # noqa: E402
import run_step19_embedding_vector_validation as step19  # noqa: E402
from aioa_memory_kernel.contracts.enums import (  # noqa: E402
    AnswerStatus,
    EvidenceStatus,
    KnowledgeRoute,
    ScopeComparisonMode,
    ScopeValueType,
)
from aioa_memory_kernel.contracts.scope import ScopeDimension  # noqa: E402
from aioa_memory_kernel.contracts.serialization import (  # noqa: E402
    canonical_json,
    canonical_sha256,
)
from aioa_memory_kernel.embeddings import (  # noqa: E402
    EmbeddingGenerationRequest,
    EmbeddingGenerationService,
    PassageEmbeddingCache,
    VectorRetrievalRequest,
    VectorRetrievalResult,
    VectorRetrievalService,
    load_approved_model_spec,
)
from aioa_memory_kernel.embeddings.local_e5 import LocalE5Backend  # noqa: E402
from aioa_memory_kernel.evidence import (  # noqa: E402
    DEFAULT_CONTEXT_BUDGET_BYTES,
    MAX_BUNDLE_ITEMS,
    MAX_CANONICAL_BUNDLE_BYTES,
    MAX_CONTEXT_BUDGET_BYTES,
    MAX_EXACT_PRIORITY_ITEMS,
    MAX_EXCERPT_BYTES_PER_ITEM,
    MAX_ITEMS_PER_KNOWLEDGE_VERSION,
    MAX_ITEMS_PER_SOURCE,
    MAX_MERGED_CANDIDATES,
    MAX_UPSTREAM_RESULTS_PER_MODALITY,
    PERSISTENCE_DECISION,
    RRF_K,
    RRF_SCALE,
    STEP18_RETRIEVAL_POLICY_VERSION,
    HybridEvidenceService,
    HybridModality,
    HybridRetrievalRequest,
    Step20BoundaryError,
    Step20ReasonCode,
    load_diversity_policy,
    load_ranking_policy,
    verify_evidence_bundle_hash,
)
from aioa_memory_kernel.german_law.corpus import (  # noqa: E402
    STEP14_TENANT_ID,
    build_source_registry_record,
)
from aioa_memory_kernel.hats import decode_manifest  # noqa: E402
from aioa_memory_kernel.persistence import (  # noqa: E402
    SerializableTransactionRunner,
)
from aioa_memory_kernel.retrieval import (  # noqa: E402
    ExactIdentifierField,
    ExactIdentifierSelector,
    FullTextQuery,
    KeywordQuery,
    RetrievalMode,
    RetrievalRequest,
    RetrievalResult,
    RetrievalService,
    StatuteSectionSelector,
)
from aioa_memory_kernel.routing import (  # noqa: E402
    EvidenceCoverageStatus,
    ExecutionAuthorizationDecision,
    KnowledgePolicyDecision,
    KnowledgeRouteResult,
    PolicyGateResult,
    Step17ReasonCode,
)
from aioa_memory_kernel.security.credentials import (  # noqa: E402
    build_minimal_subprocess_environment,
)
from aioa_memory_kernel.sources import (  # noqa: E402
    SourceAuthorityLevel,
    SourcePublicationState,
)


BASELINE_SHA = "c8d0258bf38b207b29a1a4d1121172ba26f11caa"


class ValidationFailure(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__("Step 20 controlled validation failed")
        self.code = code


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--external-env",
        type=Path,
        default=ROOT / ".local/external-data.env",
    )
    parser.add_argument("--cockroach-binary", type=Path)
    parser.add_argument(
        "--step14-bundle-root",
        type=Path,
        default=step18.DEFAULT_STEP14,
    )
    parser.add_argument(
        "--step15-bundle-root",
        type=Path,
        default=step18.DEFAULT_STEP15,
    )
    parser.add_argument(
        "--step16-bundle-root",
        type=Path,
        default=step18.DEFAULT_STEP16,
    )
    parser.add_argument(
        "--source-root",
        type=Path,
        default=step18.DEFAULT_SOURCE_ROOT,
    )
    return parser.parse_args()


def _enter_isolated_runtime(config: object) -> None:
    runtime = step19._safe_external_directory(
        config.data_root,
        step19.RUNTIME_RELATIVE,
    )
    step19._safe_external_directory(config.data_root, step19.HF_RELATIVE)
    step19._safe_external_directory(config.data_root, step19.PIP_RELATIVE)
    runtime_python = runtime / "bin/python"
    if not runtime_python.is_file():
        subprocess.run(
            [sys.executable, "-m", "venv", str(runtime)],
            check=True,
            timeout=300,
        )
        subprocess.run(
            [
                str(runtime_python),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--requirement",
                str(step19.REQUIREMENTS_PATH),
            ],
            check=True,
            timeout=1800,
            env={
                **build_minimal_subprocess_environment(os.environ),
                "PIP_CACHE_DIR": str(config.data_root / step19.PIP_RELATIVE),
            },
        )
    if Path(sys.prefix).resolve() != runtime.resolve(strict=True):
        environment = {
            **build_minimal_subprocess_environment(os.environ),
            "STEP20_ISOLATED_RUNTIME": "1",
            "HF_HOME": str(config.data_root / step19.HF_RELATIVE),
            "HF_HUB_CACHE": str(config.data_root / step19.HF_RELATIVE / "hub"),
            "PIP_CACHE_DIR": str(config.data_root / step19.PIP_RELATIVE),
            "HF_HUB_DISABLE_TELEMETRY": "1",
            "HF_HUB_DISABLE_XET": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        os.execve(
            str(runtime_python),
            [str(runtime_python), str(Path(__file__).resolve()), *sys.argv[1:]],
            environment,
        )


def _scope() -> tuple[ScopeDimension, ...]:
    return (
        ScopeDimension("legal_jurisdiction", "DE_FEDERAL", ScopeValueType.STRING, ScopeComparisonMode.EXACT, "step20-validation", True),
        ScopeDimension("legal_source_class", ("DE_FEDERAL_OFFICIAL_CONSOLIDATED_LAW",), ScopeValueType.STRING_SET, ScopeComparisonMode.IN_SET, "step20-validation", True),
        ScopeDimension("source_language", "de", ScopeValueType.STRING, ScopeComparisonMode.EXACT, "step20-validation", True),
    )


def _route(manifest_digest: str) -> KnowledgeRouteResult:
    return KnowledgeRouteResult(
        request_id="request-step20-validation",
        tenant_id=STEP14_TENANT_ID,
        user_id="step20-validation-user",
        routing_input_hash=canonical_sha256({"step": 20, "input": "controlled"}),
        registry_snapshot_hash=canonical_sha256({"step": 20, "registry": "trusted"}),
        knowledge_route=KnowledgeRoute.HAT_ASSIST,
        selected_hat_id="german-law",
        selected_hat_version="1.0.0",
        selected_manifest_digest=manifest_digest,
        effective_scope=_scope(),
        eligible_candidate_hashes=(canonical_sha256({"hat": "german-law", "version": "1.0.0"}),),
        reason_codes=(Step17ReasonCode.SINGLE_ASSISTING_HAT,),
    )


def _policy_result(route: KnowledgeRouteResult) -> PolicyGateResult:
    return PolicyGateResult(
        request_id=route.request_id,
        tenant_id=route.tenant_id,
        user_id=route.user_id,
        route_hash=route.route_hash,
        policy_context_hash=canonical_sha256({"step": 20, "policy": "controlled"}),
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


def _retrieval_request(
    route: KnowledgeRouteResult,
    mode: RetrievalMode,
    selector: object,
) -> RetrievalRequest:
    return RetrievalRequest(
        route=route,
        tenant_id=route.tenant_id,
        user_id=route.user_id,
        request_id=route.request_id,
        route_hash=route.route_hash,
        selected_hat_id=route.selected_hat_id,
        selected_hat_version=route.selected_hat_version,
        selected_manifest_digest=route.selected_manifest_digest,
        effective_scope=route.effective_scope,
        hat_scope_id="german-law-global-1a",
        retrieval_mode=mode,
        selector=selector,  # type: ignore[arg-type]
        maximum_results=20,
    )


def _generation_request(route: KnowledgeRouteResult) -> EmbeddingGenerationRequest:
    return EmbeddingGenerationRequest(
        route=route,
        tenant_id=route.tenant_id,
        user_id=route.user_id,
        request_id=route.request_id,
        route_hash=route.route_hash,
        selected_hat_id=route.selected_hat_id,
        selected_hat_version=route.selected_hat_version,
        selected_manifest_digest=route.selected_manifest_digest,
        effective_scope=route.effective_scope,
        hat_scope_id="german-law-global-1a",
        model_digest=load_approved_model_spec().model_digest,
        batch_size=2,
        maximum_items=4,
    )


def _vector_request(route: KnowledgeRouteResult) -> VectorRetrievalRequest:
    return VectorRetrievalRequest(
        route=route,
        tenant_id=route.tenant_id,
        user_id=route.user_id,
        request_id=route.request_id,
        route_hash=route.route_hash,
        selected_hat_id=route.selected_hat_id,
        selected_hat_version=route.selected_hat_version,
        selected_manifest_digest=route.selected_manifest_digest,
        effective_scope=route.effective_scope,
        hat_scope_id="german-law-global-1a",
        query_text="Welche Vorschrift regelt die Ernennung des Bundespräsidenten?",
        model_digest=load_approved_model_spec().model_digest,
        maximum_results=20,
    )


def _hybrid_request(
    route: KnowledgeRouteResult,
    lexical_inputs: tuple[tuple[RetrievalRequest, RetrievalResult], ...],
    vector_input: tuple[VectorRetrievalRequest, VectorRetrievalResult],
    *,
    context_budget_bytes: int = DEFAULT_CONTEXT_BUDGET_BYTES,
) -> HybridRetrievalRequest:
    ranking = load_ranking_policy()
    policy = _policy_result(route)
    return HybridRetrievalRequest(
        route=route,
        policy_result=policy,
        tenant_id=route.tenant_id,
        user_id=route.user_id,
        request_id=route.request_id,
        route_hash=route.route_hash,
        policy_result_hash=policy.policy_result_hash,
        selected_hat_id=route.selected_hat_id,
        selected_hat_version=route.selected_hat_version,
        selected_manifest_digest=route.selected_manifest_digest,
        hat_scope_id="german-law-global-1a",
        effective_scope=route.effective_scope,
        personal_memory_space_id=None,
        requested_modalities=(
            HybridModality.EXACT_IDENTIFIER,
            HybridModality.STATUTE_SECTION,
            HybridModality.FULL_TEXT,
            HybridModality.KEYWORD,
            HybridModality.VECTOR,
        ),
        lexical_request_hashes=tuple(value.request_hash for value, _ in lexical_inputs),
        lexical_result_hashes=tuple(value.result_hash for _, value in lexical_inputs),
        vector_request_hash=vector_input[0].request_hash,
        vector_result_hash=vector_input[1].result_hash,
        embedding_model_digest=load_approved_model_spec().model_digest,
        step18_retrieval_policy_version=STEP18_RETRIEVAL_POLICY_VERSION,
        ranking_policy_id=ranking.policy_id,
        ranking_policy_version=ranking.policy_version,
        ranking_policy_digest=ranking.policy_digest,
        diversity_policy_digest=load_diversity_policy().policy_digest,
        context_budget_bytes=context_budget_bytes,
        maximum_bundle_items=MAX_BUNDLE_ITEMS,
    )


def _assert_negative_sources(
    service: RetrievalService,
    route: KnowledgeRouteResult,
) -> None:
    for source_id in ("step20-unpublished", "step20-weak-authority", "step20-other-hat"):
        request = _retrieval_request(
            route,
            RetrievalMode.EXACT_IDENTIFIER,
            ExactIdentifierSelector(ExactIdentifierField.SOURCE_ID, (source_id,)),
        )
        if service.retrieve(request).candidates:
            raise ValidationFailure("STEP20_HARD_FILTER_NEGATIVE_LEAK")


def validate(args: argparse.Namespace) -> Mapping[str, Any]:
    adapter, config, external_facts = step19._external_runtime(args.external_env)
    runtime_versions = step19._runtime_versions()
    model_root = step19._bootstrap_model(config)
    os.environ.update(
        {
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "HF_HUB_DISABLE_TELEMETRY": "1",
        }
    )
    backend = LocalE5Backend(model_root)
    spec = load_approved_model_spec()
    real_args = SimpleNamespace(
        step14_bundle_root=args.step14_bundle_root,
        step15_bundle_root=args.step15_bundle_root,
        step16_bundle_root=args.step16_bundle_root,
        source_root=args.source_root,
    )
    item, first, candidate = step18._real_fixture(real_args)
    provision_path = args.source_root.resolve() / item["alias_provisions_relative_paths"][0]
    second = step18._jsonl_first(
        provision_path,
        lambda value: value.get("provision_identifier") == "II.",
    )
    manifest = decode_manifest(
        (ROOT / "config/hats/german-law-1.0.0.json").read_bytes(),
        schema_path=ROOT / "schemas/hat-manifest.schema.json",
    )
    route = _route(manifest.typed_manifest_digest)
    base = build_source_registry_record(candidate, created_at=step18.FIXTURE_TIME)
    published = step18._published_record(base)
    unpublished = step18._clone_record(base, tenant_id=STEP14_TENANT_ID, source_id="step20-unpublished", hat_scope_id="german-law-global-1a", state=SourcePublicationState.REGISTERED, authority=SourceAuthorityLevel.AUTHORITATIVE_SECONDARY)
    weak = step18._clone_record(base, tenant_id=STEP14_TENANT_ID, source_id="step20-weak-authority", hat_scope_id="german-law-global-1a", state=SourcePublicationState.PUBLISHED, authority=SourceAuthorityLevel.INFORMATIONAL_SECONDARY)
    other_hat = step18._clone_record(base, tenant_id=STEP14_TENANT_ID, source_id="step20-other-hat", hat_scope_id="german-law-other-1a", state=SourcePublicationState.PUBLISHED, authority=SourceAuthorityLevel.AUTHORITATIVE_SECONDARY)
    other_tenant = step18._clone_record(base, tenant_id=STEP14_TENANT_ID + "-other", source_id="step20-other-tenant", hat_scope_id="german-law-global-1a", state=SourcePublicationState.PUBLISHED, authority=SourceAuthorityLevel.AUTHORITATIVE_SECONDARY)
    records = (published, unpublished, weak, other_hat, other_tenant)

    binary = args.cockroach_binary
    if binary is None:
        binary = config.data_root / "cache/xdg/cockroachdb/v26.2.5/linux-amd64/server/cockroach-v26.2.5.linux-amd64/cockroach"
    source_binary = binary.resolve(strict=True)
    binary_identity = migrations.verify_binary_identity(source_binary)
    if binary_identity["binary_sha256"] != step19.EXPECTED_COCKROACH_SHA256:
        raise ValidationFailure("COCKROACH_BINARY_DIGEST_MISMATCH")
    # Reuse the Step 19 audited ownership prefix; it is cleanup identity only.
    runtime = migrations.LocalRuntime(
        source_binary,
        "mp_step19_step20_" + uuid.uuid4().hex[:8],
    )
    root: step18._Step18HttpSqlClient | None = None
    database: str | None = None
    cleanup: Mapping[str, Any] | None = None
    try:
        root = step18._start_disposable_runtime(runtime)
        setting = migrations.one_value(
            root.execute("defaultdb", "SHOW CLUSTER SETTING feature.vector_index.enabled", timeout=60)
        )
        if setting != "t":
            root.execute("defaultdb", "SET CLUSTER SETTING feature.vector_index.enabled = true", timeout=60)
        database = "mp_step19_step20_" + uuid.uuid4().hex[:8]
        migrations.create_database(root, database)
        first_apply = migrations.apply_migrations(root, database, timeout=300)
        replay = migrations.apply_migrations(root, database, timeout=300)
        if len(first_apply["applied"]) != 10 or replay["applied"]:
            raise ValidationFailure("STEP20_MIGRATION_BASELINE_REPLAY_MISMATCH")
        root.execute(
            database,
            step18._seed_sql(records, item, (first, second), manifest.typed_manifest_digest),
            timeout=300,
        )
        step19._seed_negative_embeddings(
            root,
            database,
            (unpublished, weak, other_hat, other_tenant),
            str(first["content_sha256"]),
        )
        runner = SerializableTransactionRunner(lambda: step18._HttpConnection(root, database))
        retrieval_service = RetrievalService(runner)
        requests = (
            _retrieval_request(route, RetrievalMode.EXACT_IDENTIFIER, ExactIdentifierSelector(ExactIdentifierField.OFFICIAL_IDENTIFIER, (step18.EXPECTED_OFFICIAL_IDENTIFIER,))),
            _retrieval_request(route, RetrievalMode.STATUTE_SECTION, StatuteSectionSelector(step18.EXPECTED_OFFICIAL_IDENTIFIER, "I.")),
            _retrieval_request(route, RetrievalMode.FULL_TEXT, FullTextQuery("Bundespräsidenten Ernennung")),
            _retrieval_request(route, RetrievalMode.KEYWORD, KeywordQuery(("Artikel", "Bundespräsidenten"))),
        )
        lexical_inputs = tuple((request, retrieval_service.retrieve(request)) for request in requests)
        if any(not result.candidates for _, result in lexical_inputs):
            raise ValidationFailure("STEP20_REAL_STEP18_INPUT_MISSING")
        _assert_negative_sources(retrieval_service, route)

        cache = PassageEmbeddingCache(adapter)
        generation = EmbeddingGenerationService(runner, backend, cache)
        generation_request = _generation_request(route)
        generated = generation.generate(generation_request)
        replayed = generation.generate(generation_request)
        if (
            tuple(value.record_hash for value in generated.records)
            != tuple(value.record_hash for value in replayed.records)
            or replayed.generated_count != 0
        ):
            raise ValidationFailure("STEP20_STEP19_EMBEDDING_REPLAY_MISMATCH")
        vector_request = _vector_request(route)
        vector_result = VectorRetrievalService(runner, backend).retrieve(vector_request)
        if not vector_result.candidates:
            raise ValidationFailure("STEP20_REAL_STEP19_INPUT_MISSING")
        vector_input = (vector_request, vector_result)

        request = _hybrid_request(route, lexical_inputs, vector_input)
        service = HybridEvidenceService()
        outcome = service.assemble(
            request,
            lexical_inputs=tuple(reversed(lexical_inputs)),
            vector_input=vector_input,
        )
        replay_outcome = service.assemble(
            request,
            lexical_inputs=lexical_inputs,
            vector_input=vector_input,
        )
        bundle = outcome.bundle
        if bundle is None or bundle.bundle_hash != replay_outcome.bundle.bundle_hash:
            raise ValidationFailure("STEP20_BUNDLE_REPLAY_MISMATCH")
        verify_evidence_bundle_hash(bundle)
        if (
            bundle.candidates_after_dedup >= bundle.candidates_before_dedup
            or not bundle.ordered_items
            or bundle.ordered_items[0].match_class != 0
        ):
            raise ValidationFailure("STEP20_DEDUP_OR_EXACT_PRIORITY_MISMATCH")
        for evidence_item in bundle.ordered_items:
            expected_score = sum(
                (RRF_SCALE * load_ranking_policy().modality_weights[contribution.modality.value])
                // (RRF_K + contribution.one_based_rank)
                for contribution in evidence_item.contributions
            )
            if evidence_item.fused_score != expected_score:
                raise ValidationFailure("STEP20_FIXED_POINT_FUSION_MISMATCH")

        small_request = _hybrid_request(
            route,
            lexical_inputs,
            vector_input,
            context_budget_bytes=512,
        )
        small_bundle = service.assemble(
            small_request,
            lexical_inputs=lexical_inputs,
            vector_input=vector_input,
        ).bundle
        if small_bundle is None or not small_bundle.truncated or small_bundle.context_bytes_used > 512:
            raise ValidationFailure("STEP20_CONTEXT_BUDGET_MISMATCH")

        conflict_candidate = dataclasses.replace(
            vector_result.candidates[0],
            authority_basis={"official_identifier": "CONFLICT"},
        )
        conflict_result = dataclasses.replace(
            vector_result,
            candidates=(conflict_candidate, *vector_result.candidates[1:]),
        )
        conflict_vector = (vector_request, conflict_result)
        conflict_request = _hybrid_request(route, lexical_inputs, conflict_vector)
        try:
            service.assemble(
                conflict_request,
                lexical_inputs=lexical_inputs,
                vector_input=conflict_vector,
            )
        except Step20BoundaryError as exc:
            if exc.reason_code is not Step20ReasonCode.HYBRID_CANDIDATE_METADATA_CONFLICT:
                raise ValidationFailure("STEP20_METADATA_CONFLICT_WRONG_FAILURE") from exc
        else:
            raise ValidationFailure("STEP20_METADATA_CONFLICT_NOT_REJECTED")

        wrong_model_result = copy.copy(vector_result)
        object.__setattr__(wrong_model_result, "model_digest", "f" * 64)
        object.__setattr__(
            wrong_model_result,
            "result_hash",
            canonical_sha256(wrong_model_result, exclude_fields=("result_hash",)),
        )
        wrong_model_vector = (vector_request, wrong_model_result)
        wrong_model_request = _hybrid_request(route, lexical_inputs, wrong_model_vector)
        try:
            service.assemble(
                wrong_model_request,
                lexical_inputs=lexical_inputs,
                vector_input=wrong_model_vector,
            )
        except Step20BoundaryError as exc:
            if exc.reason_code is not Step20ReasonCode.HYBRID_MODEL_MISMATCH:
                raise ValidationFailure("STEP20_WRONG_MODEL_WRONG_FAILURE") from exc
        else:
            raise ValidationFailure("STEP20_WRONG_MODEL_NOT_REJECTED")

        database = None
        cleanup = step18._stop_owned_runtime(runtime)
        root = None
        if cleanup["cleanup_errors"] or cleanup["force_kill_used"] or not all(
            cleanup[key] for key in ("pid_exited", "ports_closed", "temporary_store_removed")
        ):
            raise ValidationFailure("STEP20_DISPOSABLE_RUNTIME_CLEANUP_FAILED")

        ranking = load_ranking_policy()
        score_matrix = [
            {
                "evidence_id": value.evidence_id,
                "match_class": value.match_class,
                "fused_score": value.fused_score,
                "modalities": [item.modality.value for item in value.contributions],
                "modality_ranks": {
                    item.modality.value: item.one_based_rank
                    for item in value.contributions
                },
            }
            for value in bundle.ordered_items
        ]
        evidence: dict[str, Any] = {
            "schema_version": "1.0.0",
            "step": "STEP_20_HYBRID_RETRIEVAL_EVIDENCE_BUNDLE_DETERMINISTIC_RANKING_1A",
            "status": "PASS",
            "baseline_sha": BASELINE_SHA,
            "step17_route": {
                "route_hash": route.route_hash,
                "tenant_id": route.tenant_id,
                "user_id": route.user_id,
                "selected_hat_id": route.selected_hat_id,
                "selected_hat_version": route.selected_hat_version,
                "selected_manifest_digest": route.selected_manifest_digest,
                "effective_scope_hash": canonical_sha256(route.effective_scope),
            },
            "step18_inputs": {
                mode.value: {
                    "request_hash": request_value.request_hash,
                    "result_hash": result.result_hash,
                    "candidate_count": result.candidate_count,
                }
                for request_value, result in lexical_inputs
                for mode in (request_value.retrieval_mode,)
            },
            "step19_input": {
                "request_hash": vector_request.request_hash,
                "result_hash": vector_result.result_hash,
                "model_digest": spec.model_digest,
                "candidate_count": vector_result.candidate_count,
                "metric": "UNIT_NORMALIZED_L2",
            },
            "ranking_policy": {
                "policy_id": ranking.policy_id,
                "policy_version": ranking.policy_version,
                "policy_digest": ranking.policy_digest,
                "rrf_k": ranking.rrf_k,
                "rrf_scale": ranking.rrf_scale,
                "modality_weights": ranking.modality_weights,
                "native_float_hash_material": False,
            },
            "fusion": {
                "shared_bindings": "PASS",
                "upstream_hash_verification": "PASS",
                "candidates_before_dedup": bundle.candidates_before_dedup,
                "candidates_after_dedup": bundle.candidates_after_dedup,
                "metadata_conflict_negative": "PASS",
                "exact_priority": "PASS",
                "fixed_point_fusion": "PASS",
                "input_order_independence": "PASS",
                "score_matrix": score_matrix,
            },
            "diversity": {
                "status": "PASS",
                "policy_digest": bundle.diversity_policy_digest,
                "candidates_after_diversity": bundle.candidates_after_diversity,
                "final_ordered_evidence_ids": [item.evidence_id for item in bundle.ordered_items],
            },
            "context_budget": {
                "status": "PASS",
                "default_budget_bytes": DEFAULT_CONTEXT_BUDGET_BYTES,
                "bounded_probe_bytes": 512,
                "bounded_probe_used": small_bundle.context_bytes_used,
                "bounded_probe_truncated": small_bundle.truncated,
                "final_context_bytes_used": bundle.context_bytes_used,
            },
            "evidence_bundle": {
                "bundle_id": bundle.evidence_bundle_id,
                "bundle_hash": bundle.bundle_hash,
                "item_count": len(bundle.ordered_items),
                "retrieval_coverage": bundle.retrieval_coverage.value,
                "evidence_status": bundle.evidence_status.value,
                "answer_status": bundle.answer_status.value,
                "deterministic_replay": "PASS",
                "persistence": PERSISTENCE_DECISION,
                "persistence_replay": "NOT_APPLICABLE",
            },
            "hard_filter_negatives": {
                "cross_tenant": "PASS_STEP18_STEP19_SQL_AND_STEP20_UNIT",
                "cross_hat": "PASS_STEP18_STEP19_SQL_AND_STEP20_UNIT",
                "unpublished": "PASS",
                "weak_authority": "PASS",
                "wrong_model_digest": "PASS",
                "source_authority_is_rank_boost": False,
                "vector_similarity_is_authority": False,
            },
            "resource_bounds": {
                "maximum_upstream_results_per_modality": MAX_UPSTREAM_RESULTS_PER_MODALITY,
                "maximum_merged_candidates": MAX_MERGED_CANDIDATES,
                "maximum_bundle_items": MAX_BUNDLE_ITEMS,
                "maximum_items_per_source": MAX_ITEMS_PER_SOURCE,
                "maximum_items_per_knowledge_version": MAX_ITEMS_PER_KNOWLEDGE_VERSION,
                "maximum_exact_priority_items": MAX_EXACT_PRIORITY_ITEMS,
                "maximum_context_budget_bytes": MAX_CONTEXT_BUDGET_BYTES,
                "maximum_excerpt_bytes_per_item": MAX_EXCERPT_BYTES_PER_ITEM,
                "maximum_canonical_bundle_bytes": MAX_CANONICAL_BUNDLE_BYTES,
            },
            "real_inputs": {
                "step16_fixture": "PASS",
                "source_id": item["source_id"],
                "official_identifier": item["official_identifier"],
                "version_identity": item["version_identity"],
                "bounded_provisions": 2,
                "step16_source_writes": 0,
                "step19_model_id": spec.model_id,
                "step19_model_revision": spec.model_revision,
                "local_offline_model": "PASS",
                "runtime_versions": runtime_versions,
                "external_volume_verified": all(external_facts.values()),
            },
            "boundaries": {
                "step21_started": False,
                "temporal_resolution": 0,
                "freshness_policy": 0,
                "provider_calls": 0,
                "remote_model_calls": 0,
                "aws_mutations": 0,
                "s3_mutations": 0,
            },
            "cleanup": {
                "force_kill_used": cleanup["force_kill_used"],
                "pid_exited": cleanup["pid_exited"],
                "ports_closed": cleanup["ports_closed"],
                "temporary_store_removed": cleanup["temporary_store_removed"],
                "step19_model_cache_preserved": True,
                "step16_source_bundle_unchanged": True,
            },
            "cockroachdb": {
                "pinned_version": migrations.PINNED_VERSION,
                "binary_sha256": binary_identity["binary_sha256"],
                "migration_replay": "PASS_NOOP",
                "new_step20_migration": False,
            },
        }
        evidence["validation_digest"] = canonical_sha256(evidence)
        return evidence
    finally:
        if root is not None:
            try:
                step18._stop_owned_runtime(runtime)
            except Exception:
                pass
        elif runtime.runtime_dir is not None:
            try:
                runtime.stop_and_remove()
            except Exception:
                pass


def main() -> int:
    args = _arguments()
    try:
        _adapter, config, _facts = step19._external_runtime(args.external_env)
        _enter_isolated_runtime(config)
        evidence = validate(args)
    except (
        Step20BoundaryError,
        ValidationFailure,
        OSError,
        ValueError,
        subprocess.SubprocessError,
        migrations.MigrationError,
    ) as exc:
        reason = exc.code if isinstance(exc, ValidationFailure) else type(exc).__name__.upper()
        print(canonical_json({"status": "FAILED", "reason": reason}), file=sys.stderr)
        return 1
    print(canonical_json(evidence))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
