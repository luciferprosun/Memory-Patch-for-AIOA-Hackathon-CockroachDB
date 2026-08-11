"""Typed Step 38 bridge from one verified answer lineage to one DB campaign.

The contracts in this module add no business authority.  They bind existing
Step 17-26 immutable results to a later owner-scoped query, and record only
sanitized hashes from one Step 27-35 disposable CockroachDB campaign.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from aioa_memory_kernel.answers import (
    FinalAnswerRequest,
    FinalAnswerOutcome,
    FinalOutputStatus,
    verify_final_answer_request_hash,
    verify_final_answer_outcome_hash,
)
from aioa_memory_kernel.claims import (
    PacketInputSnapshot,
    verify_packet_input_snapshot_hash,
)
from aioa_memory_kernel.contracts.exceptions import (
    ContractValidationError,
    IntegrityError,
)
from aioa_memory_kernel.contracts.enums import EvidenceStatus, StableStringEnum
from aioa_memory_kernel.contracts.serialization import (
    canonical_sha256,
    require_sha256_hex,
)
from aioa_memory_kernel.corrections import (
    CorrectionPacketV1A,
    verify_correction_packet_hash,
    verify_packet_against_snapshot,
)
from aioa_memory_kernel.evidence import HybridEvidenceOutcome, verify_outcome_hash
from aioa_memory_kernel.german_law.e2e import (
    EvidenceBoundCorrectionContext,
    EvidenceBoundProviderInputReceipt,
    GermanLawBeforeAfterTrace,
    GermanLawGoldenCase,
    build_before_after_trace,
    verify_corrected_evidence_context_against_inputs,
    verify_corrected_evidence_proofs_against_context,
    verify_evidence_bound_provider_input_receipt,
)
from aioa_memory_kernel.modeling import DraftV1, verify_draft_v1_hash
from aioa_memory_kernel.routing import (
    AuthorityPolicyContext,
    EvidenceCoverageStatus,
    KnowledgePolicyDecision,
    KnowledgeRouteResult,
    PolicyGateResult,
    RoutingInput,
    evaluate_policy_gate,
    route_knowledge_request,
    verify_policy_result_hash,
    verify_route_hash,
    verify_routing_input_hash,
)
from aioa_memory_kernel.temporal import (
    TemporalResolutionResult,
    verify_temporal_result_hash,
)
from aioa_memory_kernel.verification import (
    DraftV2PipelineResult,
    verify_corrected_evidence_proofs_against_packet,
    verify_draft_v2_pipeline_result_hash,
)
from aioa_memory_kernel.reliability import (
    FailureDomain,
    FailurePoint,
    FailureRecoveryCaseResult,
    RecoveryStatus,
    verify_failure_recovery_case_result,
)
from aioa_memory_kernel.review_workspace import ReviewCaseType
from aioa_memory_kernel.security.credentials import CredentialPurpose


STEP38_UPSTREAM_LINEAGE_VERSION = "step38-verified-upstream-lineage-1a"
STEP38_PERSONAL_MEMORY_SCENARIO_VERSION = "step38-personal-memory-scenario-1a"
STEP38_COHERENT_RUNTIME_PROOF_VERSION = "step38-coherent-runtime-proof-1a"
STEP38_UPSTREAM_RUNTIME_ATTESTATION_VERSION = (
    "step38-upstream-runtime-attestation-1a"
)
STEP38_POST_RETRIEVAL_POLICY_VERSION = "step38-post-retrieval-policy-1a"
STEP38_DATABASE_RETRIEVAL_PROOF_VERSION = (
    "step38-database-retrieval-proof-1a"
)
STEP38_ACTIVATION_RECOVERY_OBSERVATION_VERSION = (
    "step38-activation-recovery-observation-1a"
)


class Step38RealSecondModelInferenceStatus(StableStringEnum):
    """Closed disclosure of whether Step 38 invoked a second real model."""

    NOT_REQUIRED_PROVIDER_NEUTRAL_RETRIEVAL_ONLY = (
        "NOT_REQUIRED_PROVIDER_NEUTRAL_RETRIEVAL_ONLY"
    )
    REAL_SECOND_MODEL_INFERENCE_COMPLETED = (
        "REAL_SECOND_MODEL_INFERENCE_COMPLETED"
    )
    UNAVAILABLE = "UNAVAILABLE"


def _logical_id(value: object, field_name: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value.encode("utf-8")) > 255
        or any(ord(character) < 32 for character in value)
    ):
        raise ContractValidationError(f"{field_name} must be a bounded identifier")
    return value


def _post_retrieval_coverage(status: EvidenceStatus) -> EvidenceCoverageStatus:
    if status is EvidenceStatus.SUFFICIENT:
        return EvidenceCoverageStatus.COMPLETE
    if status is EvidenceStatus.CONFLICTING:
        return EvidenceCoverageStatus.CONFLICTING
    if status in {EvidenceStatus.STALE, EvidenceStatus.INVALID}:
        return EvidenceCoverageStatus.COMPLETE
    if status is EvidenceStatus.INSUFFICIENT:
        return EvidenceCoverageStatus.PARTIAL
    if status in {EvidenceStatus.UNAVAILABLE, EvidenceStatus.NOT_REQUIRED}:
        return EvidenceCoverageStatus.EMPTY
    raise ContractValidationError("unsupported post-retrieval evidence status")


@dataclass(frozen=True, slots=True)
class Step38PostRetrievalPolicyReceipt:
    """Re-evaluate Step 17 policy after Step 21 without changing the route.

    Axis A necessarily runs before retrieval.  This receipt executes the
    existing public router and policy evaluator again over the same trusted
    input with only Step 21 evidence state updated.  It then rebinds the
    resulting knowledge decision to the original route hash consumed by
    Steps 18-26.  Execution authority and route semantics cannot change.
    """

    receipt_version: str
    initial_routing_input: RoutingInput
    initial_route: KnowledgeRouteResult
    policy_context: AuthorityPolicyContext
    initial_policy_result: PolicyGateResult
    temporal_result: TemporalResolutionResult
    post_retrieval_routing_input: RoutingInput
    post_retrieval_route: KnowledgeRouteResult
    evaluated_post_retrieval_policy: PolicyGateResult
    final_policy_result: PolicyGateResult
    receipt_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.receipt_version != STEP38_POST_RETRIEVAL_POLICY_VERSION:
            raise ContractValidationError("unsupported post-retrieval policy receipt")
        for value, expected, name in (
            (self.initial_routing_input, RoutingInput, "initial_routing_input"),
            (self.initial_route, KnowledgeRouteResult, "initial_route"),
            (self.policy_context, AuthorityPolicyContext, "policy_context"),
            (self.initial_policy_result, PolicyGateResult, "initial_policy_result"),
            (self.temporal_result, TemporalResolutionResult, "temporal_result"),
            (
                self.post_retrieval_routing_input,
                RoutingInput,
                "post_retrieval_routing_input",
            ),
            (
                self.post_retrieval_route,
                KnowledgeRouteResult,
                "post_retrieval_route",
            ),
            (
                self.evaluated_post_retrieval_policy,
                PolicyGateResult,
                "evaluated_post_retrieval_policy",
            ),
            (self.final_policy_result, PolicyGateResult, "final_policy_result"),
        ):
            if not isinstance(value, expected):
                raise ContractValidationError(f"{name} must be typed")
        verify_routing_input_hash(self.initial_routing_input)
        verify_route_hash(self.initial_route)
        verify_policy_result_hash(self.initial_policy_result)
        verify_temporal_result_hash(self.temporal_result)
        verify_routing_input_hash(self.post_retrieval_routing_input)
        verify_route_hash(self.post_retrieval_route)
        verify_policy_result_hash(self.evaluated_post_retrieval_policy)
        verify_policy_result_hash(self.final_policy_result)

        expected_initial = evaluate_policy_gate(
            self.initial_routing_input,
            self.initial_route,
            self.policy_context,
        )
        coverage = _post_retrieval_coverage(self.temporal_result.evidence_status)
        expected_metadata = dict(self.initial_routing_input.context_metadata)
        expected_metadata["post_retrieval_step21_result_hash"] = (
            self.temporal_result.result_hash
        )
        expected_input = replace(
            self.initial_routing_input,
            evidence_status=self.temporal_result.evidence_status,
            evidence_coverage_status=coverage,
            context_metadata=expected_metadata,
        )
        expected_route = route_knowledge_request(expected_input)
        expected_evaluated = evaluate_policy_gate(
            expected_input,
            expected_route,
            self.policy_context,
        )
        expected_final = replace(
            expected_evaluated,
            route_hash=self.initial_route.route_hash,
        )
        route_semantics = lambda value: (
            value.request_id,
            value.tenant_id,
            value.user_id,
            value.registry_snapshot_hash,
            value.knowledge_route,
            value.selected_hat_id,
            value.selected_hat_version,
            value.selected_manifest_digest,
            canonical_sha256(value.effective_scope),
            value.eligible_candidate_hashes,
            value.reason_codes,
        )
        if (
            self.initial_policy_result != expected_initial
            or self.post_retrieval_routing_input != expected_input
            or self.post_retrieval_route != expected_route
            or self.evaluated_post_retrieval_policy != expected_evaluated
            or self.final_policy_result != expected_final
            or route_semantics(self.initial_route) != route_semantics(expected_route)
            or (
                self.temporal_result.request_id,
                self.temporal_result.tenant_id,
                self.temporal_result.user_id,
                self.temporal_result.route_hash,
            )
            != (
                self.initial_route.request_id,
                self.initial_route.tenant_id,
                self.initial_route.user_id,
                self.initial_route.route_hash,
            )
            or self.final_policy_result.execution_authorization_decision
            is not self.initial_policy_result.execution_authorization_decision
        ):
            raise IntegrityError("post-retrieval policy derivation is invalid")
        object.__setattr__(
            self,
            "receipt_hash",
            canonical_sha256(self, exclude_fields=("receipt_hash",)),
        )


def build_step38_post_retrieval_policy_receipt(
    routing_input: RoutingInput,
    route: KnowledgeRouteResult,
    policy_context: AuthorityPolicyContext,
    initial_policy_result: PolicyGateResult,
    temporal_result: TemporalResolutionResult,
) -> Step38PostRetrievalPolicyReceipt:
    """Build the reconstructible policy receipt for one Step 21 result."""

    coverage = _post_retrieval_coverage(temporal_result.evidence_status)
    metadata = dict(routing_input.context_metadata)
    metadata["post_retrieval_step21_result_hash"] = temporal_result.result_hash
    post_input = replace(
        routing_input,
        evidence_status=temporal_result.evidence_status,
        evidence_coverage_status=coverage,
        context_metadata=metadata,
    )
    post_route = route_knowledge_request(post_input)
    evaluated = evaluate_policy_gate(post_input, post_route, policy_context)
    final_policy = replace(evaluated, route_hash=route.route_hash)
    return Step38PostRetrievalPolicyReceipt(
        receipt_version=STEP38_POST_RETRIEVAL_POLICY_VERSION,
        initial_routing_input=routing_input,
        initial_route=route,
        policy_context=policy_context,
        initial_policy_result=initial_policy_result,
        temporal_result=temporal_result,
        post_retrieval_routing_input=post_input,
        post_retrieval_route=post_route,
        evaluated_post_retrieval_policy=evaluated,
        final_policy_result=final_policy,
    )


@dataclass(frozen=True, slots=True)
class Step38VerifiedUpstreamLineage:
    """One verified Step 17-26 lineage consumable by downstream services."""

    lineage_version: str
    golden_case: GermanLawGoldenCase
    route: KnowledgeRouteResult
    post_retrieval_policy_receipt: Step38PostRetrievalPolicyReceipt
    step20_outcome: HybridEvidenceOutcome
    temporal_result: TemporalResolutionResult
    draft_v1: DraftV1
    packet_input_snapshot: PacketInputSnapshot
    correction_packet: CorrectionPacketV1A
    step25_result: DraftV2PipelineResult
    final_answer_request: FinalAnswerRequest
    final_outcome: FinalAnswerOutcome
    evidence_context: EvidenceBoundCorrectionContext
    provider_input_receipt: EvidenceBoundProviderInputReceipt
    before_after_trace: GermanLawBeforeAfterTrace
    lineage_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.lineage_version != STEP38_UPSTREAM_LINEAGE_VERSION:
            raise ContractValidationError("unsupported Step 38 upstream lineage")
        for value, expected, field_name in (
            (self.golden_case, GermanLawGoldenCase, "golden_case"),
            (self.route, KnowledgeRouteResult, "route"),
            (
                self.post_retrieval_policy_receipt,
                Step38PostRetrievalPolicyReceipt,
                "post_retrieval_policy_receipt",
            ),
            (self.step20_outcome, HybridEvidenceOutcome, "step20_outcome"),
            (self.temporal_result, TemporalResolutionResult, "temporal_result"),
            (self.draft_v1, DraftV1, "draft_v1"),
            (
                self.packet_input_snapshot,
                PacketInputSnapshot,
                "packet_input_snapshot",
            ),
            (self.correction_packet, CorrectionPacketV1A, "correction_packet"),
            (self.step25_result, DraftV2PipelineResult, "step25_result"),
            (
                self.final_answer_request,
                FinalAnswerRequest,
                "final_answer_request",
            ),
            (self.final_outcome, FinalAnswerOutcome, "final_outcome"),
            (
                self.evidence_context,
                EvidenceBoundCorrectionContext,
                "evidence_context",
            ),
            (
                self.provider_input_receipt,
                EvidenceBoundProviderInputReceipt,
                "provider_input_receipt",
            ),
            (
                self.before_after_trace,
                GermanLawBeforeAfterTrace,
                "before_after_trace",
            ),
        ):
            if not isinstance(value, expected):
                raise ContractValidationError(f"{field_name} must be typed")
        try:
            verify_route_hash(self.route)
            verify_outcome_hash(self.step20_outcome)
            verify_temporal_result_hash(self.temporal_result)
            verify_draft_v1_hash(self.draft_v1)
            verify_packet_input_snapshot_hash(self.packet_input_snapshot)
            verify_correction_packet_hash(self.correction_packet)
            verify_packet_against_snapshot(
                self.correction_packet, self.packet_input_snapshot
            )
            verify_draft_v2_pipeline_result_hash(self.step25_result)
            verify_corrected_evidence_proofs_against_packet(
                self.step25_result, self.correction_packet
            )
            verify_corrected_evidence_context_against_inputs(
                self.evidence_context,
                (self.step20_outcome.bundle,),
                self.correction_packet,
                self.packet_input_snapshot.ordered_evidence_links,
            )
            verify_corrected_evidence_proofs_against_context(
                self.step25_result,
                self.correction_packet,
                self.evidence_context,
            )
            verify_evidence_bound_provider_input_receipt(
                self.provider_input_receipt
            )
            verify_final_answer_outcome_hash(self.final_outcome)
            verify_final_answer_request_hash(self.final_answer_request)
        except (ContractValidationError, IntegrityError) as exc:
            raise IntegrityError("Step 38 upstream contract integrity failed") from exc
        bundle = self.step20_outcome.bundle
        answer = self.final_outcome.verified_answer
        if bundle is None:
            raise IntegrityError("Step 38 upstream bundle is required")
        try:
            reconstructed_trace = build_before_after_trace(
                self.golden_case,
                self.draft_v1,
                self.correction_packet,
                self.step25_result,
                self.final_outcome,
                self.evidence_context,
                self.provider_input_receipt,
            )
        except (ContractValidationError, IntegrityError, RuntimeError) as exc:
            raise IntegrityError("Step 38 upstream trace reconstruction failed") from exc
        common = (
            self.route.request_id,
            self.route.tenant_id,
            self.route.user_id,
            self.route.route_hash,
        )
        if (
            answer is None
            or self.final_outcome.output_status is not FinalOutputStatus.VERIFIED_ANSWER
            or (
                self.step20_outcome.request_id,
                self.step20_outcome.tenant_id,
                self.step20_outcome.user_id,
                self.step20_outcome.route_hash,
            )
            != common
            or (
                self.temporal_result.request_id,
                self.temporal_result.tenant_id,
                self.temporal_result.user_id,
                self.temporal_result.route_hash,
            )
            != common
            or (
                self.draft_v1.request_id,
                self.draft_v1.tenant_id,
                self.draft_v1.user_id,
                self.draft_v1.route_hash,
            )
            != common
            or (
                self.packet_input_snapshot.request_id,
                self.packet_input_snapshot.tenant_id,
                self.packet_input_snapshot.user_id,
                self.packet_input_snapshot.route_hash,
            )
            != common
            or (
                answer.request_id,
                answer.tenant_id,
                answer.user_id,
                answer.route_hash,
            )
            != common
        ):
            raise IntegrityError("Step 38 upstream request identity is detached")
        if (
            self.temporal_result.step20_outcome_hash
            != self.step20_outcome.outcome_hash
            or self.temporal_result.step20_bundle_hash != bundle.bundle_hash
            or self.draft_v1.step21_result_hash != self.temporal_result.result_hash
            or self.packet_input_snapshot.draft_v1_hash != self.draft_v1.draft_hash
            or self.packet_input_snapshot.step20_bundle_hashes
            != (bundle.bundle_hash,)
            or self.packet_input_snapshot.step21_result_hash
            != self.temporal_result.result_hash
            or self.correction_packet.draft_v1_hash != self.draft_v1.draft_hash
            or self.correction_packet.step23_input_snapshot_hash
            != self.packet_input_snapshot.snapshot_hash
            or self.step25_result.draft_v2.draft_v1_hash
            != self.draft_v1.draft_hash
            or self.step25_result.draft_v2.correction_packet_hash
            != self.correction_packet.packet_hash
            or answer.evidence_bundle_hash != bundle.bundle_hash
            or answer.temporal_resolution_hash != self.temporal_result.result_hash
            or answer.correction_packet_hash != self.correction_packet.packet_hash
            or answer.draft_v2_hash != self.step25_result.draft_v2.draft_v2_hash
            or answer.verification_summary_hash
            != self.step25_result.verification_summary.summary_hash
            or self.post_retrieval_policy_receipt.initial_route
            != self.route
            or self.post_retrieval_policy_receipt.temporal_result
            != self.temporal_result
            or self.post_retrieval_policy_receipt.final_policy_result
            != self.final_answer_request.policy_result
            or self.final_answer_request.route != self.route
            or self.final_answer_request.step20_outcomes
            != (self.step20_outcome,)
            or self.final_answer_request.temporal_result
            != self.temporal_result
            or self.final_answer_request.draft_v1 != self.draft_v1
            or self.final_answer_request.correction_packet
            != self.correction_packet
            or self.final_answer_request.step25_result != self.step25_result
            or self.final_answer_request.integrity_receipt is None
            or self.final_outcome.request_hash
            != self.final_answer_request.request_hash
            or self.evidence_context.packet_input_snapshot_hash
            != self.packet_input_snapshot.snapshot_hash
            or self.evidence_context.correction_packet_hash
            != self.correction_packet.packet_hash
            or self.evidence_context.step20_bundle_hashes != (bundle.bundle_hash,)
            or self.provider_input_receipt.evidence_context_hash
            != self.evidence_context.context_hash
            or self.provider_input_receipt.correction_packet_hash
            != self.correction_packet.packet_hash
            or canonical_sha256(
                self.provider_input_receipt,
                exclude_fields=("receipt_hash",),
            )
            != self.provider_input_receipt.receipt_hash
            or reconstructed_trace != self.before_after_trace
        ):
            raise IntegrityError("Step 38 upstream lineage hash binding failed")
        object.__setattr__(
            self,
            "lineage_hash",
            canonical_sha256(
                {
                    "lineage_version": self.lineage_version,
                    "golden_case_hash": self.golden_case.case_hash,
                    "route_hash": self.route.route_hash,
                    "post_retrieval_policy_receipt_hash": (
                        self.post_retrieval_policy_receipt.receipt_hash
                    ),
                    "step20_outcome_hash": self.step20_outcome.outcome_hash,
                    "temporal_result_hash": self.temporal_result.result_hash,
                    "draft_v1_hash": self.draft_v1.draft_hash,
                    "packet_input_snapshot_hash": (
                        self.packet_input_snapshot.snapshot_hash
                    ),
                    "correction_packet_hash": self.correction_packet.packet_hash,
                    "step25_result_hash": self.step25_result.result_hash,
                    "final_outcome_hash": self.final_outcome.outcome_hash,
                    "final_answer_request_hash": (
                        self.final_answer_request.request_hash
                    ),
                    "verified_answer_hash": answer.answer_hash,
                    "evidence_context_hash": self.evidence_context.context_hash,
                    "provider_input_receipt_hash": (
                        self.provider_input_receipt.receipt_hash
                    ),
                    "before_after_trace_hash": self.before_after_trace.trace_hash,
                }
            ),
        )


@dataclass(frozen=True, slots=True)
class Step38DatabaseRetrievalProof:
    """Hash-only projection of typed Step 17-20 database artifacts."""

    proof_version: str
    retrieval_input_hash: str
    primary_retrieval_input_hash: str | None
    retrieval_input_kind: str
    route_hash: str
    step20_outcome_hash: str
    evidence_bundle_hash: str
    temporal_projection_receipt_hash: str
    real_retrieval_artifacts_hash: str
    real_retrieval_attestation_hash: str
    runtime_instance_digest: str
    database_instance_digest: str
    data_plane_credential_purpose: CredentialPurpose
    data_plane_session_user: str
    embedding_backend_identity_digest: str
    embedding_verified_files_digest: str
    approved_local_e5_backend: bool
    cross_tenant_rls_visible_count: int
    owned_database: bool
    step18_exact_retrieval: bool
    step19_vector_retrieval: bool
    step20_hybrid_assembly: bool
    proof_hash: str = field(init=False)
    closure_eligible: bool = field(init=False)

    def __post_init__(self) -> None:
        if self.proof_version != STEP38_DATABASE_RETRIEVAL_PROOF_VERSION:
            raise ContractValidationError("unsupported database retrieval proof")
        for value, name in (
            (self.retrieval_input_hash, "retrieval_input_hash"),
            (self.route_hash, "route_hash"),
            (self.step20_outcome_hash, "step20_outcome_hash"),
            (self.evidence_bundle_hash, "evidence_bundle_hash"),
            (
                self.temporal_projection_receipt_hash,
                "temporal_projection_receipt_hash",
            ),
            (self.real_retrieval_artifacts_hash, "real_retrieval_artifacts_hash"),
            (
                self.real_retrieval_attestation_hash,
                "real_retrieval_attestation_hash",
            ),
            (self.runtime_instance_digest, "runtime_instance_digest"),
            (self.database_instance_digest, "database_instance_digest"),
            (
                self.embedding_backend_identity_digest,
                "embedding_backend_identity_digest",
            ),
            (
                self.embedding_verified_files_digest,
                "embedding_verified_files_digest",
            ),
        ):
            require_sha256_hex(value, name)
        if self.retrieval_input_kind not in {"PRIMARY", "RELATED"}:
            raise ContractValidationError("retrieval input kind is invalid")
        if self.retrieval_input_kind == "PRIMARY":
            if self.primary_retrieval_input_hash is not None:
                raise IntegrityError("primary retrieval cannot name a parent input")
        else:
            require_sha256_hex(
                self.primary_retrieval_input_hash,
                "primary_retrieval_input_hash",
            )
            if self.primary_retrieval_input_hash == self.retrieval_input_hash:
                raise IntegrityError("related retrieval input must be distinct")
        if (
            self.data_plane_credential_purpose
            is not CredentialPurpose.APPLICATION_DATABASE
            or not isinstance(self.data_plane_session_user, str)
            or not self.data_plane_session_user
            or self.data_plane_session_user in {"root", "admin"}
            or self.cross_tenant_rls_visible_count != 0
        ):
            raise IntegrityError("retrieval data-plane authority is invalid")
        flags = (
            self.approved_local_e5_backend,
            self.owned_database,
            self.step18_exact_retrieval,
            self.step19_vector_retrieval,
            self.step20_hybrid_assembly,
        )
        if any(not isinstance(value, bool) for value in flags):
            raise ContractValidationError("retrieval proof flags must be boolean")
        object.__setattr__(self, "closure_eligible", all(flags))
        object.__setattr__(
            self,
            "proof_hash",
            canonical_sha256(
                self,
                exclude_fields=("proof_hash", "closure_eligible"),
            ),
        )


@dataclass(frozen=True, slots=True)
class Step38UpstreamRuntimeAttestation:
    """Bind one verified lineage to its typed Step 17-20 DB proof."""

    attestation_version: str
    upstream_lineage_hash: str
    retrieval_proof: Step38DatabaseRetrievalProof
    attestation_hash: str = field(init=False)
    real_retrieval_lineage: bool = field(init=False)

    def __post_init__(self) -> None:
        if self.attestation_version != STEP38_UPSTREAM_RUNTIME_ATTESTATION_VERSION:
            raise ContractValidationError("unsupported upstream runtime attestation")
        require_sha256_hex(self.upstream_lineage_hash, "upstream_lineage_hash")
        if not isinstance(self.retrieval_proof, Step38DatabaseRetrievalProof):
            raise ContractValidationError("retrieval_proof must be typed")
        if self.retrieval_proof.retrieval_input_kind != "PRIMARY":
            raise IntegrityError("upstream retrieval proof is not primary")
        object.__setattr__(
            self,
            "real_retrieval_lineage",
            self.retrieval_proof.closure_eligible,
        )
        object.__setattr__(
            self,
            "attestation_hash",
            canonical_sha256(
                self,
                exclude_fields=("attestation_hash", "real_retrieval_lineage"),
            ),
        )


@dataclass(frozen=True, slots=True)
class Step38PersonalMemoryScenario:
    """Bind the verified correction to one later owner-scoped query."""

    scenario_version: str
    upstream: Step38VerifiedUpstreamLineage
    upstream_runtime_attestation: Step38UpstreamRuntimeAttestation
    later_route: KnowledgeRouteResult
    later_step20_outcome: HybridEvidenceOutcome
    later_temporal_result: TemporalResolutionResult
    later_retrieval_proof: Step38DatabaseRetrievalProof
    later_query_digest: str
    scenario_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.scenario_version != STEP38_PERSONAL_MEMORY_SCENARIO_VERSION:
            raise ContractValidationError("unsupported Step 38 Personal Memory scenario")
        if not isinstance(self.upstream, Step38VerifiedUpstreamLineage):
            raise ContractValidationError("upstream must be a verified lineage")
        if not isinstance(
            self.upstream_runtime_attestation, Step38UpstreamRuntimeAttestation
        ) or (
            self.upstream_runtime_attestation.upstream_lineage_hash
            != self.upstream.lineage_hash
        ):
            raise IntegrityError("upstream runtime attestation is detached")
        primary_proof = self.upstream_runtime_attestation.retrieval_proof
        primary_bundle = self.upstream.step20_outcome.bundle
        if (
            primary_bundle is None
            or primary_proof.route_hash != self.upstream.route.route_hash
            or primary_proof.step20_outcome_hash
            != self.upstream.step20_outcome.outcome_hash
            or primary_proof.evidence_bundle_hash != primary_bundle.bundle_hash
        ):
            raise IntegrityError("upstream retrieval proof names another lineage")
        if (
            not isinstance(self.later_route, KnowledgeRouteResult)
            or not isinstance(self.later_step20_outcome, HybridEvidenceOutcome)
            or not isinstance(self.later_temporal_result, TemporalResolutionResult)
            or not isinstance(
                self.later_retrieval_proof, Step38DatabaseRetrievalProof
            )
        ):
            raise ContractValidationError("later query lineage must be typed")
        require_sha256_hex(self.later_query_digest, "later_query_digest")
        verify_route_hash(self.later_route)
        verify_outcome_hash(self.later_step20_outcome)
        verify_temporal_result_hash(self.later_temporal_result)
        route = self.upstream.route
        later_bundle = self.later_step20_outcome.bundle
        if (
            later_bundle is None
            or self.later_route.request_id == route.request_id
            or self.later_route.tenant_id != route.tenant_id
            or self.later_route.user_id != route.user_id
            or self.later_route.selected_hat_id != route.selected_hat_id
            or self.later_route.selected_hat_version != route.selected_hat_version
            or self.later_route.selected_manifest_digest
            != route.selected_manifest_digest
            or canonical_sha256(self.later_route.effective_scope)
            != canonical_sha256(route.effective_scope)
            or self.later_temporal_result.request_id != self.later_route.request_id
            or self.later_temporal_result.tenant_id != self.later_route.tenant_id
            or self.later_temporal_result.user_id != self.later_route.user_id
            or self.later_temporal_result.route_hash != self.later_route.route_hash
            or self.later_temporal_result.step20_outcome_hash
            != self.later_step20_outcome.outcome_hash
            or self.later_temporal_result.step20_bundle_hash
            != later_bundle.bundle_hash
            or self.later_retrieval_proof.retrieval_input_kind != "RELATED"
            or self.later_retrieval_proof.primary_retrieval_input_hash
            != primary_proof.retrieval_input_hash
            or self.later_retrieval_proof.route_hash != self.later_route.route_hash
            or self.later_retrieval_proof.step20_outcome_hash
            != self.later_step20_outcome.outcome_hash
            or self.later_retrieval_proof.evidence_bundle_hash
            != later_bundle.bundle_hash
            or self.later_retrieval_proof.runtime_instance_digest
            != primary_proof.runtime_instance_digest
            or self.later_retrieval_proof.database_instance_digest
            != primary_proof.database_instance_digest
            or self.later_query_digest == self.upstream.golden_case.question_digest
        ):
            raise IntegrityError("Step 38 later query scope or identity widened")
        object.__setattr__(
            self,
            "scenario_hash",
            canonical_sha256(
                {
                    "scenario_version": self.scenario_version,
                    "upstream_lineage_hash": self.upstream.lineage_hash,
                    "upstream_runtime_attestation_hash": (
                        self.upstream_runtime_attestation.attestation_hash
                    ),
                    "later_route_hash": self.later_route.route_hash,
                    "later_step20_outcome_hash": (
                        self.later_step20_outcome.outcome_hash
                    ),
                    "later_temporal_result_hash": (
                        self.later_temporal_result.result_hash
                    ),
                    "later_query_digest": self.later_query_digest,
                    "later_retrieval_proof_hash": (
                        self.later_retrieval_proof.proof_hash
                    ),
                }
            ),
        )


@dataclass(frozen=True, slots=True)
class Step38ActivationRecoveryObservation:
    """Fresh durable observations after the activation acknowledgement loss.

    Counts are observations, not asserted conclusions.  Derived violation
    counts deliberately remain available on a failed observation so the
    enclosing coherent proof can fail closed with an auditable digest.
    """

    observation_version: str
    activation_request_hash: str
    activation_receipt_hash: str
    activation_replay_identity_hash: str
    proposal_row_count: int
    proposal_exact_match_count: int
    approval_row_count: int
    approval_exact_match_count: int
    commit_row_count: int
    commit_exact_match_count: int
    memory_item_row_count: int
    memory_item_exact_match_count: int
    activation_transition_row_count: int
    activation_transition_exact_match_count: int
    activation_audit_event_row_count: int
    activation_audit_event_exact_match_count: int
    quota_memory_items_before: int
    quota_memory_items_after: int
    quota_active_patches_before: int
    quota_active_patches_after: int
    quota_stored_bytes_before: int
    quota_stored_bytes_after: int
    durable_authority_mismatch_count: int
    rls_cross_scope_visible_count: int
    control_plane_foreign_scope_count: int
    cross_user_approval_denied: bool
    replay_returned_existing_receipt: bool
    duplicate_side_effect_count: int = field(init=False)
    authority_violation_count: int = field(init=False)
    integrity_violation_count: int = field(init=False)
    observation_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if (
            self.observation_version
            != STEP38_ACTIVATION_RECOVERY_OBSERVATION_VERSION
        ):
            raise ContractValidationError(
                "unsupported activation recovery observation"
            )
        for value, name in (
            (self.activation_request_hash, "activation_request_hash"),
            (self.activation_receipt_hash, "activation_receipt_hash"),
            (
                self.activation_replay_identity_hash,
                "activation_replay_identity_hash",
            ),
        ):
            require_sha256_hex(value, name)
        count_names = (
            "proposal_row_count",
            "proposal_exact_match_count",
            "approval_row_count",
            "approval_exact_match_count",
            "commit_row_count",
            "commit_exact_match_count",
            "memory_item_row_count",
            "memory_item_exact_match_count",
            "activation_transition_row_count",
            "activation_transition_exact_match_count",
            "activation_audit_event_row_count",
            "activation_audit_event_exact_match_count",
            "quota_memory_items_before",
            "quota_memory_items_after",
            "quota_active_patches_before",
            "quota_active_patches_after",
            "quota_stored_bytes_before",
            "quota_stored_bytes_after",
            "durable_authority_mismatch_count",
            "rls_cross_scope_visible_count",
            "control_plane_foreign_scope_count",
        )
        for name in count_names:
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ContractValidationError(f"{name} must be non-negative")
        if not isinstance(self.cross_user_approval_denied, bool) or not isinstance(
            self.replay_returned_existing_receipt, bool
        ):
            raise ContractValidationError(
                "activation recovery observations must be boolean"
            )
        row_pairs = (
            (self.proposal_row_count, self.proposal_exact_match_count),
            (self.approval_row_count, self.approval_exact_match_count),
            (self.commit_row_count, self.commit_exact_match_count),
            (self.memory_item_row_count, self.memory_item_exact_match_count),
            (
                self.activation_transition_row_count,
                self.activation_transition_exact_match_count,
            ),
            (
                self.activation_audit_event_row_count,
                self.activation_audit_event_exact_match_count,
            ),
        )
        if any(exact > total for total, exact in row_pairs):
            raise ContractValidationError(
                "exact recovery matches cannot exceed observed rows"
            )
        duplicate_count = sum(max(total - 1, 0) for total, _ in row_pairs)
        integrity_count = sum(
            (1 if total == 0 else 0) + (total - exact)
            for total, exact in row_pairs
        )
        integrity_count += sum(
            (
                int(self.quota_memory_items_before != 1),
                int(self.quota_memory_items_after != 1),
                int(self.quota_active_patches_before != 0),
                int(self.quota_active_patches_after != 1),
                int(self.quota_stored_bytes_before == 0),
                int(
                    self.quota_stored_bytes_after
                    != self.quota_stored_bytes_before
                ),
                int(not self.replay_returned_existing_receipt),
            )
        )
        authority_count = (
            self.durable_authority_mismatch_count
            + self.rls_cross_scope_visible_count
            + self.control_plane_foreign_scope_count
            + int(not self.cross_user_approval_denied)
        )
        object.__setattr__(self, "duplicate_side_effect_count", duplicate_count)
        object.__setattr__(self, "authority_violation_count", authority_count)
        object.__setattr__(self, "integrity_violation_count", integrity_count)
        object.__setattr__(
            self,
            "observation_hash",
            canonical_sha256(
                self,
                exclude_fields=(
                    "duplicate_side_effect_count",
                    "authority_violation_count",
                    "integrity_violation_count",
                    "observation_hash",
                ),
            ),
        )


def verify_step38_activation_recovery_observation(
    value: Step38ActivationRecoveryObservation,
) -> None:
    if not isinstance(value, Step38ActivationRecoveryObservation):
        raise TypeError("value must be Step38ActivationRecoveryObservation")
    reconstructed = replace(value)
    if reconstructed != value:
        raise IntegrityError("activation recovery observation hash or counts differ")


@dataclass(frozen=True, slots=True)
class Step38CoherentRuntimeProof:
    """Sanitized result of one Step 27-35 disposable database campaign."""

    proof_version: str
    scenario_hash: str
    upstream_runtime_attestation_hash: str
    primary_retrieval_proof_hash: str
    later_retrieval_proof_hash: str
    runtime_instance_digest: str
    database_instance_digest: str
    tenant_id: str
    owner_user_id: str
    request_id: str
    personal_memory_space_id: str
    slot_hash: str
    candidate_hash: str
    candidate_envelope_hash: str
    proposal_hash: str
    validation_receipt_hash: str
    approval_receipt_hash: str
    commit_receipt_hash: str
    activation_receipt_hash: str
    active_patch_hash: str
    later_active_patch_retrieval_request_hash: str
    disallowed_model_retrieval_hash: str
    cross_user_approval_denial_hash: str
    cross_user_export_denial_hash: str
    canonical_conflict_temporal_hash: str
    canonical_conflict_retrieval_hash: str
    activation_ack_lost_recovery_hash: str
    activation_ack_lost_recovery_status: str
    activation_recovery_observation: Step38ActivationRecoveryObservation
    activation_recovery_case_result: FailureRecoveryCaseResult
    activation_recovery_failure_point: FailurePoint
    duplicate_semantic_side_effect_count: int
    authority_violation_count: int
    integrity_violation_count: int
    model_a_identity_digest: str
    model_a_retrieval_hash: str
    model_a_context_hash: str
    model_b_identity_digest: str
    model_b_retrieval_hash: str
    model_b_context_hash: str
    audit_chain_id: str
    audit_verification_hash: str
    audit_first_event_hash: str
    audit_last_event_hash: str
    approval_audit_event_hash: str
    commit_audit_event_hash: str
    activation_audit_event_hash: str
    audit_export_bundle_hash: str
    shared_promotion_hash: str
    review_case_type: ReviewCaseType
    review_case_hash: str
    review_detail_hash: str
    ui_awaiting_dashboard_hash: str
    ui_dashboard_hash: str
    cross_model_same_patch: bool
    disallowed_model_access_denied: bool
    cross_user_access_denied: bool
    cross_tenant_access_denied: bool
    cross_user_approval_denied: bool
    cross_user_export_denied: bool
    canonical_evidence_conflict_suppressed: bool
    ordinary_user_review_access_denied: bool
    audit_chain_verified: bool
    lifecycle_audit_events_distinct: bool
    audit_export_hash_only: bool
    review_context_verified: bool
    ui_awaiting_state_verified: bool
    ui_active_state_verified: bool
    ui_same_active_patch: bool
    activation_ack_lost_recovered: bool
    real_second_model_inference_status: Step38RealSecondModelInferenceStatus
    canonical_evidence_authority: bool
    source_publication_authority: bool
    external_execution_authority: bool
    real_retrieval_lineage: bool
    later_real_retrieval_lineage: bool
    upstream_and_downstream_same_database: bool
    step39_started: bool
    closure_eligible: bool
    proof_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.proof_version != STEP38_COHERENT_RUNTIME_PROOF_VERSION:
            raise ContractValidationError("unsupported coherent runtime proof")
        for value, name in (
            (self.scenario_hash, "scenario_hash"),
            (
                self.upstream_runtime_attestation_hash,
                "upstream_runtime_attestation_hash",
            ),
            (self.primary_retrieval_proof_hash, "primary_retrieval_proof_hash"),
            (self.later_retrieval_proof_hash, "later_retrieval_proof_hash"),
            (self.runtime_instance_digest, "runtime_instance_digest"),
            (self.database_instance_digest, "database_instance_digest"),
            (self.slot_hash, "slot_hash"),
            (self.candidate_hash, "candidate_hash"),
            (self.candidate_envelope_hash, "candidate_envelope_hash"),
            (self.proposal_hash, "proposal_hash"),
            (self.validation_receipt_hash, "validation_receipt_hash"),
            (self.approval_receipt_hash, "approval_receipt_hash"),
            (self.commit_receipt_hash, "commit_receipt_hash"),
            (self.activation_receipt_hash, "activation_receipt_hash"),
            (self.active_patch_hash, "active_patch_hash"),
            (
                self.later_active_patch_retrieval_request_hash,
                "later_active_patch_retrieval_request_hash",
            ),
            (
                self.disallowed_model_retrieval_hash,
                "disallowed_model_retrieval_hash",
            ),
            (
                self.cross_user_approval_denial_hash,
                "cross_user_approval_denial_hash",
            ),
            (
                self.cross_user_export_denial_hash,
                "cross_user_export_denial_hash",
            ),
            (
                self.canonical_conflict_temporal_hash,
                "canonical_conflict_temporal_hash",
            ),
            (
                self.canonical_conflict_retrieval_hash,
                "canonical_conflict_retrieval_hash",
            ),
            (
                self.activation_ack_lost_recovery_hash,
                "activation_ack_lost_recovery_hash",
            ),
            (self.model_a_identity_digest, "model_a_identity_digest"),
            (self.model_a_retrieval_hash, "model_a_retrieval_hash"),
            (self.model_a_context_hash, "model_a_context_hash"),
            (self.model_b_identity_digest, "model_b_identity_digest"),
            (self.model_b_retrieval_hash, "model_b_retrieval_hash"),
            (self.model_b_context_hash, "model_b_context_hash"),
            (self.audit_verification_hash, "audit_verification_hash"),
            (self.audit_first_event_hash, "audit_first_event_hash"),
            (self.audit_last_event_hash, "audit_last_event_hash"),
            (self.approval_audit_event_hash, "approval_audit_event_hash"),
            (self.commit_audit_event_hash, "commit_audit_event_hash"),
            (self.activation_audit_event_hash, "activation_audit_event_hash"),
            (self.audit_export_bundle_hash, "audit_export_bundle_hash"),
            (self.shared_promotion_hash, "shared_promotion_hash"),
            (self.review_case_hash, "review_case_hash"),
            (self.review_detail_hash, "review_detail_hash"),
            (self.ui_awaiting_dashboard_hash, "ui_awaiting_dashboard_hash"),
            (self.ui_dashboard_hash, "ui_dashboard_hash"),
        ):
            require_sha256_hex(value, name)
        for value, name in (
            (self.tenant_id, "tenant_id"),
            (self.owner_user_id, "owner_user_id"),
            (self.request_id, "request_id"),
            (self.personal_memory_space_id, "personal_memory_space_id"),
            (self.audit_chain_id, "audit_chain_id"),
        ):
            _logical_id(value, name)
        if (
            self.activation_ack_lost_recovery_status
            != "RECOVERED_BY_IDEMPOTENT_REPLAY"
        ):
            raise ContractValidationError(
                "activation acknowledgement-loss recovery status differs"
            )
        if not isinstance(
            self.activation_recovery_observation,
            Step38ActivationRecoveryObservation,
        ):
            raise ContractValidationError(
                "activation recovery observation must be typed"
            )
        verify_step38_activation_recovery_observation(
            self.activation_recovery_observation
        )
        if not isinstance(
            self.activation_recovery_case_result,
            FailureRecoveryCaseResult,
        ):
            raise ContractValidationError(
                "activation recovery result must be typed"
            )
        verify_failure_recovery_case_result(self.activation_recovery_case_result)
        observation = self.activation_recovery_observation
        recovery = self.activation_recovery_case_result
        for value, name in (
            (
                self.duplicate_semantic_side_effect_count,
                "duplicate_semantic_side_effect_count",
            ),
            (self.authority_violation_count, "authority_violation_count"),
            (self.integrity_violation_count, "integrity_violation_count"),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ContractValidationError(f"{name} must be non-negative")
        if (
            observation.activation_receipt_hash != self.activation_receipt_hash
            or recovery.result_hash != self.activation_ack_lost_recovery_hash
            or recovery.subject_hash != self.activation_receipt_hash
            or recovery.failure_domain
            is not FailureDomain.PERSONAL_MEMORY_ACTIVATION
            or recovery.failure_point is not FailurePoint.PM_AFTER_ACTIVATION_ACK_LOST
            or self.activation_recovery_failure_point is not recovery.failure_point
            or recovery.attempt_count != 2
            or recovery.recovery_status
            is not RecoveryStatus.RECOVERED_BY_IDEMPOTENT_REPLAY
            or recovery.recovery_status.value
            != self.activation_ack_lost_recovery_status
            or recovery.duplicate_side_effect_count
            != observation.duplicate_side_effect_count
            or recovery.authority_violation_count
            != observation.authority_violation_count
            or recovery.integrity_violation_count
            != observation.integrity_violation_count
            or self.duplicate_semantic_side_effect_count
            != observation.duplicate_side_effect_count
            or self.authority_violation_count
            != observation.authority_violation_count
            or self.integrity_violation_count
            != observation.integrity_violation_count
            or any(
                (
                    observation.duplicate_side_effect_count,
                    observation.authority_violation_count,
                    observation.integrity_violation_count,
                )
            )
        ):
            raise ContractValidationError(
                "activation recovery is not proven by durable observations"
            )
        if not isinstance(self.review_case_type, ReviewCaseType):
            raise ContractValidationError("review_case_type must be typed")
        if not isinstance(
            self.real_second_model_inference_status,
            Step38RealSecondModelInferenceStatus,
        ):
            raise ContractValidationError(
                "real second-model inference status must be typed"
            )
        expected_second_model_status = (
            Step38RealSecondModelInferenceStatus
            .NOT_REQUIRED_PROVIDER_NEUTRAL_RETRIEVAL_ONLY
        )
        if self.real_second_model_inference_status is not expected_second_model_status:
            raise ContractValidationError(
                "coherent downstream proof must not claim a real second-model run"
            )
        evidence_flags = (
            self.cross_model_same_patch,
            self.disallowed_model_access_denied,
            self.cross_user_access_denied,
            self.cross_tenant_access_denied,
            self.cross_user_approval_denied,
            self.cross_user_export_denied,
            self.canonical_evidence_conflict_suppressed,
            self.ordinary_user_review_access_denied,
            self.audit_chain_verified,
            self.lifecycle_audit_events_distinct,
            self.audit_export_hash_only,
            self.review_context_verified,
            self.ui_awaiting_state_verified,
            self.ui_active_state_verified,
            self.ui_same_active_patch,
            self.activation_ack_lost_recovered,
        )
        if any(not isinstance(value, bool) for value in evidence_flags):
            raise ContractValidationError("coherent proof flags must be boolean")
        if not all(
            evidence_flags
        ):
            raise ContractValidationError("coherent runtime proof is incomplete")
        if any(
            (
                self.canonical_evidence_authority,
                self.source_publication_authority,
                self.external_execution_authority,
            )
        ):
            raise ContractValidationError("runtime proof crossed an authority boundary")
        if any(
            not isinstance(value, bool)
            for value in (
                self.real_retrieval_lineage,
                self.later_real_retrieval_lineage,
                self.upstream_and_downstream_same_database,
                self.step39_started,
                self.closure_eligible,
            )
        ):
            raise ContractValidationError("closure classification must be boolean")
        if self.step39_started:
            raise ContractValidationError("Step 39 must remain not started")
        expected_eligible = (
            self.real_retrieval_lineage
            and self.later_real_retrieval_lineage
            and self.upstream_and_downstream_same_database
            and all(evidence_flags)
            and not self.step39_started
        )
        if self.closure_eligible is not expected_eligible:
            raise ContractValidationError("closure eligibility is overstated")
        object.__setattr__(
            self,
            "proof_hash",
            canonical_sha256(self, exclude_fields=("proof_hash",)),
        )


__all__ = [
    "STEP38_ACTIVATION_RECOVERY_OBSERVATION_VERSION",
    "STEP38_COHERENT_RUNTIME_PROOF_VERSION",
    "STEP38_DATABASE_RETRIEVAL_PROOF_VERSION",
    "STEP38_PERSONAL_MEMORY_SCENARIO_VERSION",
    "STEP38_POST_RETRIEVAL_POLICY_VERSION",
    "STEP38_UPSTREAM_LINEAGE_VERSION",
    "STEP38_UPSTREAM_RUNTIME_ATTESTATION_VERSION",
    "Step38RealSecondModelInferenceStatus",
    "Step38CoherentRuntimeProof",
    "Step38ActivationRecoveryObservation",
    "Step38DatabaseRetrievalProof",
    "Step38PersonalMemoryScenario",
    "Step38PostRetrievalPolicyReceipt",
    "Step38VerifiedUpstreamLineage",
    "Step38UpstreamRuntimeAttestation",
    "build_step38_post_retrieval_policy_receipt",
    "verify_step38_activation_recovery_observation",
]
