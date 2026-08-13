"""D3 live German Law jury flow over the existing Step 17-26 contracts.

The application service is deliberately narrow: it accepts only the two
versioned guided cases, reads canonical evidence with the normal RLS-bound
application runner, and sends every paid call through the R5 guarded provider.
It has no Personal Memory write, Commit Helper, publication, or review
authority.
"""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from aioa_memory_kernel.answers import (
    FinalAnswerRequest,
    FinalOutputStatus,
    VerifiedAnswerService,
)
from aioa_memory_kernel.claims import (
    ClaimEvidenceBindingService,
    prepare_claim_binding_request,
)
from aioa_memory_kernel.contracts.enums import (
    EvidenceStatus,
    KnowledgeRoute,
    ScopeComparisonMode,
    ScopeValueType,
)
from aioa_memory_kernel.contracts.exceptions import ContractValidationError, IntegrityError
from aioa_memory_kernel.contracts.scope import ScopeDimension
from aioa_memory_kernel.contracts.serialization import canonical_sha256
from aioa_memory_kernel.corrections import (
    HmacSha256PacketAuthenticator,
    build_correction_packet,
)
from aioa_memory_kernel.demo_cockpit.jury_flow import (
    GuidedJuryCase,
    JuryCorrectionProjection,
    JuryEvidenceProjection,
    JuryExecutionKind,
    JuryFlowRequest,
    JuryFlowResult,
    JuryProviderSummary,
    JuryRunState,
    JuryStageProjection,
    JuryStageState,
)
from aioa_memory_kernel.embeddings import load_approved_model_spec
from aioa_memory_kernel.evidence import (
    DEFAULT_CONTEXT_BUDGET_BYTES,
    MAX_BUNDLE_ITEMS,
    STEP18_RETRIEVAL_POLICY_VERSION,
    HybridEvidenceService,
    HybridModality,
    HybridRetrievalRequest,
    load_diversity_policy,
    load_ranking_policy,
)
from aioa_memory_kernel.german_law.e2e import (
    CanonicalEvidenceExactVerifier,
    EvidenceBoundDraftV2Provider,
    GermanLawGoldenCase,
    GermanLawGoldenCaseSuite,
    REAL_HAT_ID,
    REAL_HAT_SCOPE_ID,
    REAL_HAT_VERSION,
    REAL_OFFICIAL_IDENTIFIER,
    REAL_PROVISION_HASHES,
    REAL_SOURCE_ID,
    build_draft_v2_target_projection,
    build_evidence_bound_correction_context,
    load_german_law_golden_cases,
    prove_draft_v1_evidence_blind,
)
from aioa_memory_kernel.german_law.e2e_runtime import (
    build_step38_post_retrieval_policy_receipt,
)
from aioa_memory_kernel.hats import (
    CompatibilityDecision,
    HatRegistryService,
    ReviewActor,
    ReviewReceipt,
    RuntimeBinding,
    decide_compatibility,
    decode_manifest,
)
from aioa_memory_kernel.modeling import (
    DraftV1Service,
    ModelAdapterError,
    ModelReasonCode,
    PromptTemplate,
    prepare_model_generation_request,
)
from aioa_memory_kernel.persistence import SerializableTransactionRunner
from aioa_memory_kernel.retrieval import (
    RetrievalBoundaryError,
    RetrievalMode,
    RetrievalRequest,
    RetrievalService,
    StatuteSectionSelector,
)
from aioa_memory_kernel.routing import (
    AuthorityPolicyContext,
    EvidenceCoverageStatus,
    ExecutionAuthorizationDecision,
    HatPolicyRequirement,
    HatRoutingCandidate,
    KnowledgePolicyDecision,
    RoutingInput,
    TrustedHatRegistrySnapshot,
    evaluate_policy_gate,
    route_knowledge_request,
)
from aioa_memory_kernel.temporal import (
    EvidenceAvailability,
    FreshnessPolicy,
    TemporalBoundaryError,
    TemporalQueryMode,
    TemporalResolutionService,
)
from aioa_memory_kernel.verification import (
    DraftV2LayeredVerifier,
    DraftV2Service,
    Step25BoundaryError,
    VerificationSummaryStatus,
    prepare_draft_v2_generation_request,
)

from .provider_guard import GuardedProviderAdapter, ProviderRequestScope


_ROOT = Path(__file__).resolve().parents[3]
_CASES = _ROOT / "tests" / "fixtures" / "step38_german_law_cases.json"
_HAT_MANIFEST = _ROOT / "config" / "hats" / "german-law-1.0.0.json"
_HAT_SCHEMA = _ROOT / "schemas" / "hat-manifest.schema.json"
_FLOW_VERSION = "d3-live-memory-patch-jury-flow-1a"
_PRIMARY_CASE_ID = "primary-entry-into-force"
_BACKUP_CASE_ID = "backup-special-case-reservation"
_ALLOWED_CASES = (_PRIMARY_CASE_ID, _BACKUP_CASE_ID)
_PROVISION_II_CORRECT = (
    "Für besondere Fälle behalte ich mir die Ernennung und Entlassung der "
    "unter I. genannten Beamtinnen und Beamten vor."
)
_PROVISION_II_WRONG = (
    "Für besondere Fälle behalte ich mir die Ernennung und Entlassung der "
    "unter I. genannten Beamtinnen und Beamten nicht vor."
)

_STAGE_DEFINITIONS = (
    ("question", "User Question", "Authenticated owner input only"),
    ("draft-v1", "Draft V1", "Untrusted, evidence-blind provider output"),
    ("route-hat", "Route / HAT Decision", "Deterministic Step 17 authority"),
    ("retrieved-evidence", "Retrieved Evidence", "Canonical published source only"),
    ("temporal", "Temporal / Conflict / Freshness", "Deterministic Step 21 result"),
    ("claims", "Claim Analysis", "Deterministic Step 23 binding"),
    ("correction-packet", "Correction Packet", "Deterministic Step 24 packet"),
    ("draft-v2", "Draft V2", "Evidence-bound provider output"),
    ("verification", "Layered Verification", "Step 25 verifier decides eligibility"),
    ("verified-answer", "Verified Answer", "Step 26 fail-closed output boundary"),
    ("memory-proposal", "Personal Memory Proposal", "Optional private candidate only"),
    ("owner-approval", "Owner Approval", "Explicit CSRF-bound owner action only"),
    ("activation", "Commit / Activation", "Separate Commit Helper receipt required"),
    ("later-reuse", "Later Question / Reuse", "Private non-canonical context only"),
)


class JuryFlowFailure(RuntimeError):
    def __init__(self, reason_code: str, stage_index: int) -> None:
        super().__init__("D3 jury flow failed safely")
        self.reason_code = reason_code
        self.stage_index = stage_index


@dataclass(frozen=True, slots=True)
class _RouteBindings:
    case: GermanLawGoldenCase
    routing_input: RoutingInput
    route: object
    policy_context: AuthorityPolicyContext
    policy_result: object


def _stage_list(case: GuidedJuryCase) -> list[JuryStageProjection]:
    result = [
        JuryStageProjection(
            stage_id,
            label,
            JuryStageState.NOT_RUN,
            "This stage has not run.",
            authority,
        )
        for stage_id, label, authority in _STAGE_DEFINITIONS
    ]
    result[0] = replace(
        result[0],
        state=JuryStageState.COMPLETED,
        summary=case.question,
        reference=case.question_digest,
    )
    return result


def _set_stage(
    values: list[JuryStageProjection],
    index: int,
    *,
    state: JuryStageState,
    summary: str,
    reference: str | None = None,
) -> None:
    values[index] = replace(
        values[index],
        state=state,
        summary=summary,
        reference=reference,
    )


def _guided_cases(suite: GermanLawGoldenCaseSuite) -> tuple[GuidedJuryCase, ...]:
    labels = {
        _PRIMARY_CASE_ID: "Primary: entry into force",
        _BACKUP_CASE_ID: "Backup: special-case reservation",
    }
    return tuple(
        GuidedJuryCase(
            case.case_id,
            labels[case.case_id],
            case.question,
            case.case_kind.value,
            case.question_digest,
        )
        for case in (suite.case(value) for value in _ALLOWED_CASES)
    )


def load_guided_jury_cases() -> tuple[GuidedJuryCase, ...]:
    """Load only the two versioned real-corpus cases approved by Step 38."""

    return _guided_cases(load_german_law_golden_cases(_CASES))


def _trusted_manifest_entry(case: GermanLawGoldenCase):
    identity = decode_manifest(_HAT_MANIFEST.read_bytes(), schema_path=_HAT_SCHEMA)
    service = HatRegistryService(clock=lambda: case.knowledge_as_of)
    compatibility = decide_compatibility(identity)
    service.register(identity, compatibility)
    service.validate(identity.manifest.hat_id, identity.manifest.hat_version)
    implementation_digest = canonical_sha256(
        {
            "installation_class": "SYSTEM_INSTALLED",
            "hat_id": identity.manifest.hat_id,
            "hat_version": identity.manifest.hat_version,
            "adapter": _FLOW_VERSION,
        }
    )
    binding = RuntimeBinding(
        "german-law-system-d3",
        identity.manifest.hat_id,
        identity.manifest.hat_version,
        "GermanLawHat",
        identity.manifest.hat_version,
        "hat-sdk-1a",
        implementation_digest,
    )
    receipt = ReviewReceipt(
        identity.manifest.hat_id,
        identity.manifest.hat_version,
        identity.typed_manifest_digest,
        identity.raw_manifest_sha256,
        identity.schema_file_sha256,
        CompatibilityDecision.COMPATIBLE,
        canonical_sha256(identity.manifest.capabilities),
        binding.runtime_binding_id,
        implementation_digest,
        "ENABLE",
        ("TRUSTED_D3_CANONICAL_INPUT",),
        ReviewActor.TRUSTED_OPERATOR,
        "operator-redacted",
        case.knowledge_as_of,
    )
    return identity, service.enable(
        identity.manifest.hat_id,
        identity.manifest.hat_version,
        binding,
        receipt,
    )


def _route_bindings(
    case: GermanLawGoldenCase,
    *,
    tenant_id: str,
    user_id: str,
    request_id: str,
) -> _RouteBindings:
    identity, entry = _trusted_manifest_entry(case)
    requested_scope = (
        ScopeDimension(
            "legal_jurisdiction",
            "DE_FEDERAL",
            ScopeValueType.STRING,
            ScopeComparisonMode.EXACT,
            _FLOW_VERSION,
            True,
        ),
        ScopeDimension(
            "knowledge_as_of",
            case.knowledge_as_of,
            ScopeValueType.TIMESTAMP,
            ScopeComparisonMode.TIMESTAMP,
            _FLOW_VERSION,
            True,
        ),
        ScopeDimension(
            "source_language",
            "de",
            ScopeValueType.STRING,
            ScopeComparisonMode.EXACT,
            _FLOW_VERSION,
            True,
        ),
        ScopeDimension(
            "legal_source_class",
            ("DE_FEDERAL_OFFICIAL_CONSOLIDATED_LAW",),
            ScopeValueType.STRING_SET,
            ScopeComparisonMode.IN_SET,
            _FLOW_VERSION,
            True,
        ),
    )
    routing = RoutingInput(
        tenant_id=tenant_id,
        user_id=user_id,
        request_id=request_id,
        request_kind="knowledge-question",
        normalized_query_or_subject=case.question,
        requested_domain_id="law.de.federal",
        requested_scope=requested_scope,
        candidate_hat_descriptors=(
            HatRoutingCandidate(
                identity.manifest.hat_id,
                identity.manifest.hat_version,
                identity.typed_manifest_digest,
                HatPolicyRequirement.ADVISORY,
            ),
        ),
        trusted_hat_registry_snapshot=TrustedHatRegistrySnapshot(
            "trusted-registry:d3:guided-german-law",
            (entry,),
        ),
        evidence_status=EvidenceStatus.INSUFFICIENT,
        evidence_coverage_status=EvidenceCoverageStatus.PARTIAL,
        context_metadata={
            "classification_source": "d3-guided-german-law",
            "golden_case_hash": case.case_hash,
        },
    )
    route = route_knowledge_request(routing)
    if (
        route.knowledge_route is not KnowledgeRoute.HAT_ASSIST
        or route.selected_hat_id != REAL_HAT_ID
        or route.selected_hat_version != REAL_HAT_VERSION
    ):
        raise JuryFlowFailure("RETRIEVAL_FAILED", 2)
    policy_context = AuthorityPolicyContext(
        request_id=request_id,
        tenant_id=tenant_id,
        user_id=user_id,
        policy_reference="policy:d3:guided-retrieval-preflight",
        policy_digest=canonical_sha256(
            {"policy_id": "d3-guided-german-law", "version": "1a"}
        ),
        knowledge_policy_ceiling=KnowledgePolicyDecision.ALLOW_ANSWER,
        execution_authorization_ceiling=ExecutionAuthorizationDecision.DENY,
        scope_allowed=True,
        selected_hat_id=route.selected_hat_id,
        selected_hat_version=route.selected_hat_version,
        selected_manifest_digest=route.selected_manifest_digest,
    )
    policy = evaluate_policy_gate(routing, route, policy_context)
    return _RouteBindings(case, routing, route, policy_context, policy)


def _retrieval_request(bindings: _RouteBindings) -> RetrievalRequest:
    route = bindings.route
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
        hat_scope_id=REAL_HAT_SCOPE_ID,
        retrieval_mode=RetrievalMode.STATUTE_SECTION,
        selector=StatuteSectionSelector(
            REAL_OFFICIAL_IDENTIFIER,
            bindings.case.expected_provision_ids[0],
        ),
        maximum_results=20,
    )


def _hybrid_request(bindings: _RouteBindings, request, result) -> HybridRetrievalRequest:
    route = bindings.route
    ranking = load_ranking_policy()
    return HybridRetrievalRequest(
        route=route,
        policy_result=bindings.policy_result,
        tenant_id=route.tenant_id,
        user_id=route.user_id,
        request_id=route.request_id,
        route_hash=route.route_hash,
        policy_result_hash=bindings.policy_result.policy_result_hash,
        selected_hat_id=route.selected_hat_id,
        selected_hat_version=route.selected_hat_version,
        selected_manifest_digest=route.selected_manifest_digest,
        hat_scope_id=REAL_HAT_SCOPE_ID,
        effective_scope=route.effective_scope,
        personal_memory_space_id=None,
        requested_modalities=(HybridModality.STATUTE_SECTION,),
        lexical_request_hashes=(request.request_hash,),
        lexical_result_hashes=(result.result_hash,),
        vector_request_hash=None,
        vector_result_hash=None,
        embedding_model_digest=load_approved_model_spec().model_digest,
        step18_retrieval_policy_version=STEP18_RETRIEVAL_POLICY_VERSION,
        ranking_policy_id=ranking.policy_id,
        ranking_policy_version=ranking.policy_version,
        ranking_policy_digest=ranking.policy_digest,
        diversity_policy_digest=load_diversity_policy().policy_digest,
        context_budget_bytes=DEFAULT_CONTEXT_BUDGET_BYTES,
        maximum_bundle_items=MAX_BUNDLE_ITEMS,
    )


def _draft_v1_request(temporal, case: GermanLawGoldenCase):
    request = prepare_model_generation_request(temporal, case.question)
    if case.case_id == _PRIMARY_CASE_ID:
        instruction = (
            "Beantworte die Frage ausschließlich aus deinem eigenen Modellwissen "
            "und ohne Quellen oder Werkzeuge. Antworte auf Deutsch in genau einem "
            "kurzen, eigenständigen Satz, der nur das Datum des Inkrafttretens "
            "nennt. Verwende keine Überschrift, kein Markdown, keine Zitate, keine "
            "Paragraphen, keine Erläuterung und keine Einschränkung."
        )
        template_id = "d3-draft-v1-evidence-blind-german-date-1a"
    else:
        instruction = (
            "Beantworte die Frage ausschließlich aus deinem eigenen Modellwissen "
            "und ohne Quellen oder Werkzeuge. Gib ausschließlich den gesamten, "
            "vollständig ausgefüllten deutschen Satz aus. Der Satz darf weder "
            "einen Platzhalter noch eine Einzelwortantwort enthalten. Verwende "
            "keine Überschrift, kein Markdown und keine Erläuterung."
        )
        template_id = "d3-draft-v1-evidence-blind-german-reservation-1a"
    return replace(
        request,
        prompt_template=PromptTemplate(
            template_id=template_id,
            template_version="1",
            system_instruction=instruction,
        ),
    )


def _map_provider_failure(error: ModelAdapterError) -> str:
    if error.reason_code is ModelReasonCode.MODEL_TIMEOUT:
        return "PROVIDER_TIMEOUT"
    if error.reason_code is ModelReasonCode.MODEL_CONCURRENCY_LIMIT:
        return "PROVIDER_BUSY"
    if error.reason_code in {
        ModelReasonCode.MODEL_CALL_LIMIT_EXHAUSTED,
        ModelReasonCode.MODEL_REQUEST_LIMIT_EXHAUSTED,
    }:
        return "PROVIDER_CALL_LIMIT_REACHED"
    if error.reason_code is ModelReasonCode.MODEL_BUDGET_EXHAUSTED:
        return "DEMO_BUDGET_EXHAUSTED"
    return "INTERNAL_RUNTIME_FAILURE"


def _guard_summary(provider: GuardedProviderAdapter, scope: ProviderRequestScope):
    try:
        value = provider.accounting_snapshot(scope)
    except ModelAdapterError:
        return None
    return JuryProviderSummary(
        value.accounting_semantics,
        value.calls_reserved,
        value.calls_completed,
        value.calls_failed + value.calls_unknown_completion,
        value.calls_remaining,
    )


class LiveMemoryPatchJuryFlow:
    """Exact current-mode executor; it owns no mutation or approval service."""

    def __init__(
        self,
        runner: SerializableTransactionRunner,
        provider: GuardedProviderAdapter,
        *,
        execution_kind: JuryExecutionKind = JuryExecutionKind.LIVE,
    ) -> None:
        if not isinstance(runner, SerializableTransactionRunner):
            raise TypeError("application transaction runner is required")
        if not isinstance(provider, GuardedProviderAdapter):
            raise TypeError("R5 guarded provider is required")
        if not isinstance(execution_kind, JuryExecutionKind):
            raise TypeError("jury execution kind must be typed")
        self._runner = runner
        self._provider = provider
        self._execution_kind = execution_kind
        self._suite = load_german_law_golden_cases(_CASES)
        self._authenticator = HmacSha256PacketAuthenticator(
            key_id="d3-runtime-ephemeral-1a",
            key_material=secrets.token_bytes(32),
        )

    @property
    def cases(self) -> tuple[GuidedJuryCase, ...]:
        return _guided_cases(self._suite)

    def _evidence_projection(self, bundle, temporal, snapshot=None):
        temporal_by_item = {
            value.step20_item_hash: value for value in temporal.assessments
        }
        relation_by_item: dict[str, set[str]] = {}
        if snapshot is not None:
            for link in snapshot.ordered_evidence_links:
                relation_by_item.setdefault(link.step20_item_hash, set()).add(
                    link.relation.value
                )
        result = []
        for item in bundle.ordered_items[:8]:
            assessment = temporal_by_item.get(item.item_hash)
            relation = ", ".join(sorted(relation_by_item.get(item.item_hash, {"PENDING"})))
            result.append(
                JuryEvidenceProjection(
                    source_id=item.identity.source_id,
                    official_identifier=str(
                        item.structured_metadata.get(
                            "official_identifier", REAL_OFFICIAL_IDENTIFIER
                        )
                    ),
                    provision=str(
                        item.structured_metadata.get("provision_identifier", "unknown")
                    ),
                    authority=item.authority_level.value,
                    excerpt=item.excerpt.text,
                    source_reference=item.source_reference,
                    temporal_status=(
                        assessment.temporal_applicability.value
                        if assessment is not None
                        else "UNRESOLVED"
                    ),
                    relation=relation,
                    item_hash=item.item_hash,
                )
            )
        return tuple(result)

    def execute(
        self,
        request: JuryFlowRequest,
        progress: Callable[[tuple[JuryStageProjection, ...]], None],
    ) -> JuryFlowResult:
        stages = _stage_list(request.case)
        progress(tuple(stages))
        scope = ProviderRequestScope(
            tenant_id=request.principal.tenant_id,
            owner_user_id=request.principal.owner_user_id,
            session_id=request.session_digest,
            request_id=request.run_id,
        )
        evidence = ()
        corrections = ()
        try:
            case = self._suite.case(request.case.case_id)
            if case.case_id not in _ALLOWED_CASES:
                raise JuryFlowFailure("REQUEST_INVALID", 0)
            bindings = _route_bindings(
                case,
                tenant_id=request.principal.tenant_id,
                user_id=request.principal.owner_user_id,
                request_id=request.run_id,
            )
            retrieval_request = _retrieval_request(bindings)
            retrieval = RetrievalService(self._runner).retrieve(retrieval_request)
            expected_hash = REAL_PROVISION_HASHES[case.expected_provision_ids[0]]
            if (
                len(retrieval.candidates) != 1
                or retrieval.candidates[0].source_id != REAL_SOURCE_ID
                or retrieval.candidates[0].content_sha256 != expected_hash
            ):
                raise JuryFlowFailure("INSUFFICIENT_EVIDENCE", 3)
            hybrid_request = _hybrid_request(bindings, retrieval_request, retrieval)
            outcome = HybridEvidenceService().assemble(
                hybrid_request,
                lexical_inputs=((retrieval_request, retrieval),),
            )
            bundle = outcome.bundle
            if bundle is None or outcome.evidence_status is not EvidenceStatus.SUFFICIENT:
                raise JuryFlowFailure("INSUFFICIENT_EVIDENCE", 3)
            source_kinds = tuple(sorted({item.source_kind for item in bundle.ordered_items}))
            temporal_service = TemporalResolutionService()
            temporal_request = temporal_service.prepare_request(
                route=bindings.route,
                step20_outcome=outcome,
                temporal_mode=TemporalQueryMode.AS_OF,
                knowledge_as_of=case.knowledge_as_of,
                clock=type("D3Clock", (), {"now": lambda _self: case.knowledge_as_of})(),
                availability=EvidenceAvailability.AVAILABLE,
                freshness_policy=FreshnessPolicy(
                    policy_id="d3-guided-german-law-freshness-1a",
                    policy_version="1",
                    maximum_age_seconds_by_source_kind={
                        value: 10 * 365 * 24 * 60 * 60 for value in source_kinds
                    },
                ),
            )
            temporal = temporal_service.resolve(temporal_request)
            if temporal.evidence_status is EvidenceStatus.CONFLICTING:
                raise JuryFlowFailure("CONFLICTING_EVIDENCE", 4)
            if temporal.evidence_status is not EvidenceStatus.SUFFICIENT:
                raise JuryFlowFailure("INSUFFICIENT_EVIDENCE", 4)
            post_policy = build_step38_post_retrieval_policy_receipt(
                bindings.routing_input,
                bindings.route,
                bindings.policy_context,
                bindings.policy_result,
                temporal,
            )
            if post_policy.final_policy_result.knowledge_policy_decision is not KnowledgePolicyDecision.ALLOW_ANSWER:
                raise JuryFlowFailure("INSUFFICIENT_EVIDENCE", 4)

            draft_request = _draft_v1_request(temporal, case)
            blindness = prove_draft_v1_evidence_blind(draft_request, case.question)
            if blindness.evidence_fields_projected or blindness.tools_enabled:
                raise JuryFlowFailure("INTERNAL_RUNTIME_FAILURE", 1)
            _set_stage(
                stages,
                1,
                state=JuryStageState.RUNNING,
                summary="The guarded provider is generating an evidence-blind Draft V1.",
            )
            progress(tuple(stages))
            with self._provider.request_scope(scope):
                draft = DraftV1Service(self._provider).generate(draft_request).draft
                if case.case_id == _BACKUP_CASE_ID and draft.draft_text not in {
                    _PROVISION_II_CORRECT,
                    _PROVISION_II_WRONG,
                }:
                    raise JuryFlowFailure("VERIFICATION_FAILED", 1)
                _set_stage(
                    stages,
                    1,
                    state=JuryStageState.COMPLETED,
                    summary=draft.draft_text,
                    reference=draft.draft_hash,
                )
                _set_stage(
                    stages,
                    2,
                    state=JuryStageState.COMPLETED,
                    summary=(
                        f"{bindings.route.knowledge_route.value} via "
                        f"{bindings.route.selected_hat_id}@{bindings.route.selected_hat_version}."
                    ),
                    reference=bindings.route.route_hash,
                )
                _set_stage(
                    stages,
                    3,
                    state=JuryStageState.COMPLETED,
                    summary=(
                        f"{len(bundle.ordered_items)} published official item(s); "
                        f"evidence status {outcome.evidence_status.value}."
                    ),
                    reference=bundle.bundle_hash,
                )
                _set_stage(
                    stages,
                    4,
                    state=JuryStageState.COMPLETED,
                    summary=(
                        f"{temporal.evidence_status.value}; "
                        f"{len(temporal.conflict_groups)} conflict group(s); "
                        f"as of {case.knowledge_as_of.date().isoformat()}."
                    ),
                    reference=temporal.result_hash,
                )
                progress(tuple(stages))

                snapshot = ClaimEvidenceBindingService().freeze_packet_input(
                    prepare_claim_binding_request(draft, (bundle,), temporal)
                )
                packet = build_correction_packet(snapshot)
                evidence = self._evidence_projection(bundle, temporal, snapshot)
                status_counts: dict[str, int] = {}
                for value in snapshot.ordered_candidate_assessments:
                    status_counts[value.candidate_status.value] = (
                        status_counts.get(value.candidate_status.value, 0) + 1
                    )
                _set_stage(
                    stages,
                    5,
                    state=JuryStageState.COMPLETED,
                    summary=(
                        f"{len(snapshot.ordered_claims)} claim(s): "
                        + ", ".join(
                            f"{name}={count}" for name, count in sorted(status_counts.items())
                        )
                        + "."
                    ),
                    reference=snapshot.snapshot_hash,
                )
                _set_stage(
                    stages,
                    6,
                    state=JuryStageState.COMPLETED,
                    summary=(
                        f"{len(packet.ordered_required_corrections)} required correction(s); "
                        f"{len(packet.ordered_citations)} canonical citation(s)."
                    ),
                    reference=packet.packet_hash,
                )
                progress(tuple(stages))

                if not packet.ordered_required_corrections:
                    for index in range(7, 10):
                        _set_stage(
                            stages,
                            index,
                            state=JuryStageState.NOT_APPLICABLE,
                            summary=(
                                "No material correction was observed; no Draft V2 or "
                                "Verified Answer was fabricated."
                            ),
                        )
                    for index in range(10, 14):
                        _set_stage(
                            stages,
                            index,
                            state=JuryStageState.NOT_APPLICABLE,
                            summary="No verified correction exists for a Personal Memory proposal.",
                        )
                    return JuryFlowResult(
                        self._execution_kind,
                        JuryRunState.COMPLETED,
                        "CORRECTION_NOT_REQUIRED",
                        tuple(stages),
                        evidence=evidence,
                        provider_summary=_guard_summary(self._provider, scope),
                    )

                context = build_evidence_bound_correction_context(
                    snapshot,
                    (bundle,),
                    packet,
                )
                target = build_draft_v2_target_projection(packet, context)
                citations = {value.citation_id: value for value in packet.ordered_citations}
                corrections = tuple(
                    JuryCorrectionProjection(
                        original_claim=value.original_claim_text,
                        verdict="REFUTES",
                        required_correction=target.exact_output,
                        citation=(
                            citations[value.required_replacement_facts[0].citation_id].citation_reference
                            if value.required_replacement_facts
                            else "Canonical packet reference"
                        ),
                        correction_hash=value.correction_hash,
                    )
                    for value in packet.ordered_required_corrections
                )
                integrity = self._authenticator.authenticate(packet)
                draft_v2_request = prepare_draft_v2_generation_request(
                    draft,
                    packet,
                    integrity,
                    self._authenticator,
                )
                evidence_provider = EvidenceBoundDraftV2Provider(
                    self._provider,
                    packet,
                    context,
                    target,
                )
                pipeline = DraftV2Service(
                    evidence_provider,
                    self._authenticator,
                    verifier=DraftV2LayeredVerifier(
                        corrected_evidence_verifier=CanonicalEvidenceExactVerifier(context)
                    ),
                ).generate_and_verify(draft_v2_request)
                _set_stage(
                    stages,
                    7,
                    state=JuryStageState.COMPLETED,
                    summary=pipeline.draft_v2.draft_text,
                    reference=pipeline.draft_v2.draft_v2_hash,
                )
                if pipeline.verification_summary.summary_status is not VerificationSummaryStatus.VERIFIED:
                    raise JuryFlowFailure("VERIFICATION_FAILED", 8)
                _set_stage(
                    stages,
                    8,
                    state=JuryStageState.COMPLETED,
                    summary=(
                        "All required corrections, prohibited-claim checks and "
                        "evidence bindings passed."
                    ),
                    reference=pipeline.verification_summary.summary_hash,
                )
                final_request = FinalAnswerRequest(
                    route=bindings.route,
                    policy_result=post_policy.final_policy_result,
                    step20_outcomes=(outcome,),
                    temporal_result=temporal,
                    draft_v1=draft,
                    correction_packet=packet,
                    integrity_receipt=integrity,
                    step25_result=pipeline,
                )
                final = VerifiedAnswerService(self._authenticator).finalize(final_request)
                if (
                    final.output_status is not FinalOutputStatus.VERIFIED_ANSWER
                    or final.verified_answer is None
                ):
                    raise JuryFlowFailure("VERIFICATION_FAILED", 9)
                answer = final.verified_answer
                _set_stage(
                    stages,
                    9,
                    state=JuryStageState.COMPLETED,
                    summary=answer.answer_text,
                    reference=answer.answer_hash,
                )
                _set_stage(
                    stages,
                    10,
                    state=JuryStageState.NOT_RUN,
                    summary=(
                        "The correction is eligible for the separate existing Step "
                        "28-29 path; this run did not fabricate or auto-create a proposal."
                    ),
                )
                _set_stage(
                    stages,
                    11,
                    state=JuryStageState.NOT_RUN,
                    summary="Use the explicit owner approval form below when a real proposal exists.",
                )
                _set_stage(
                    stages,
                    12,
                    state=JuryStageState.NOT_RUN,
                    summary="Activation requires the separate Commit Helper receipt.",
                )
                _set_stage(
                    stages,
                    13,
                    state=JuryStageState.NOT_RUN,
                    summary="An ACTIVE owner-scoped patch will appear below for later reuse.",
                )
                progress(tuple(stages))
                return JuryFlowResult(
                    self._execution_kind,
                    JuryRunState.COMPLETED,
                    "VERIFIED_ANSWER",
                    tuple(stages),
                    evidence=evidence,
                    corrections=corrections,
                    verified_answer=answer.answer_text,
                    verified_answer_hash=answer.answer_hash,
                    provider_summary=_guard_summary(self._provider, scope),
                    personal_memory_eligible=True,
                )
        except JuryFlowFailure as error:
            index = min(max(error.stage_index, 0), len(stages) - 1)
            _set_stage(
                stages,
                index,
                state=JuryStageState.BLOCKED,
                summary=f"The flow stopped safely: {error.reason_code}.",
            )
            return JuryFlowResult(
                self._execution_kind,
                JuryRunState.BLOCKED,
                error.reason_code,
                tuple(stages),
                evidence=evidence,
                corrections=corrections,
                provider_summary=_guard_summary(self._provider, scope),
            )
        except ModelAdapterError as error:
            reason = _map_provider_failure(error)
            _set_stage(
                stages,
                1,
                state=JuryStageState.BLOCKED,
                summary=f"The provider boundary stopped safely: {reason}.",
            )
            return JuryFlowResult(
                self._execution_kind,
                JuryRunState.BLOCKED,
                reason,
                tuple(stages),
                evidence=evidence,
                corrections=corrections,
                provider_summary=_guard_summary(self._provider, scope),
            )
        except (RetrievalBoundaryError, TemporalBoundaryError):
            _set_stage(
                stages,
                3,
                state=JuryStageState.BLOCKED,
                summary="Canonical retrieval or temporal resolution failed safely.",
            )
            return JuryFlowResult(
                self._execution_kind,
                JuryRunState.BLOCKED,
                "RETRIEVAL_FAILED",
                tuple(stages),
            )
        except Step25BoundaryError:
            _set_stage(
                stages,
                8,
                state=JuryStageState.BLOCKED,
                summary="Layered verification failed safely; no final answer was emitted.",
            )
            return JuryFlowResult(
                self._execution_kind,
                JuryRunState.BLOCKED,
                "VERIFICATION_FAILED",
                tuple(stages),
                evidence=evidence,
                corrections=corrections,
            )
        except (ContractValidationError, IntegrityError, OSError, ValueError, KeyError):
            _set_stage(
                stages,
                0,
                state=JuryStageState.BLOCKED,
                summary="The typed runtime rejected invalid or unavailable lineage.",
            )
            return JuryFlowResult(
                self._execution_kind,
                JuryRunState.BLOCKED,
                "INTERNAL_RUNTIME_FAILURE",
                tuple(stages),
            )


__all__ = ["LiveMemoryPatchJuryFlow", "load_guided_jury_cases"]
