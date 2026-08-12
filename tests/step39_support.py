"""Deterministic provider-free builders shared by Step 39 tests and runner."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta

from aioa_memory_kernel.claims import ClaimEvidenceRelation
from aioa_memory_kernel.contracts.enums import (
    AnswerStatus,
    EvidenceStatus,
    KnowledgeRoute,
    PersonalMemorySpaceState,
    ScopeComparisonMode,
    ScopeValueType,
)
from aioa_memory_kernel.contracts.exceptions import IntegrityError
from aioa_memory_kernel.contracts.identities import KernelRunIdentity
from aioa_memory_kernel.contracts.scope import ScopeDimension
from aioa_memory_kernel.contracts.serialization import canonical_json, canonical_sha256
from aioa_memory_kernel.critic import (
    STEP39_SCHEMA_VERSION,
    CriticArtifactKind,
    CriticClaimReference,
    CriticClaimStatus,
    CriticEvidenceReference,
    CriticReviewRequest,
    CriticTextArtifact,
    CriticTrustedCandidateContext,
    load_critic_policy,
    load_critic_prompt_template,
)
from aioa_memory_kernel.critic.parser import build_critic_prompt_payload
from aioa_memory_kernel.modeling import ProviderResponse, load_approved_provider_spec
from aioa_memory_kernel.personal_memory import (
    STEP27_SCHEMA_VERSION,
    STEP28_SCHEMA_VERSION,
    CorrectionCandidateEnvelope,
    CorrectionCandidateIntakeDisposition,
    CorrectionCandidateRouteResultLineage,
    PersonalMemoryBindingMode,
    PersonalMemoryHatSlot,
    PersonalMemoryModelBinding,
    build_correction_candidate_intake_receipt,
    personal_memory_hat_scope_id,
    verify_correction_candidate_envelope,
)
from aioa_memory_kernel.sources import SourceAuthorityLevel, SourcePublicationState
from aioa_memory_kernel.temporal import FreshnessStatus, TemporalApplicability


NOW = datetime(2043, 3, 4, 5, 6, 7, tzinfo=UTC)
QUESTION = "When does the bounded rule enter into force?"
DRAFT_V1_TEXT = "The bounded rule enters into force in 2025."
DRAFT_V2_TEXT = "The bounded rule enters into force in 2024."
VERIFIED_ANSWER_TEXT = DRAFT_V2_TEXT


def digest(label: str) -> str:
    return canonical_sha256({"step39-test": label})


def scope() -> tuple[ScopeDimension, ...]:
    return (
        ScopeDimension(
            name="domain",
            value="german-law",
            value_type=ScopeValueType.STRING,
            comparison_mode=ScopeComparisonMode.EXACT,
            source="step39-trusted-route",
            required=True,
        ),
    )


def target_slot(
    *,
    tenant_id: str = "tenant-step39-a",
    owner_user_id: str = "user-step39-a",
    space_id: str = "slot-step39-a",
) -> PersonalMemoryHatSlot:
    binding = PersonalMemoryModelBinding(
        schema_version=STEP27_SCHEMA_VERSION,
        tenant_id=tenant_id,
        owner_user_id=owner_user_id,
        personal_memory_space_id=space_id,
        provider_id="target-model-provider",
        model_id="target-model-a",
        model_revision_or_declared_version="target-revision-1",
        binding_mode=PersonalMemoryBindingMode.EXACT_MODEL,
        enabled=True,
        binding_version=1,
        bound_at=NOW,
    )
    return PersonalMemoryHatSlot(
        schema_version=STEP27_SCHEMA_VERSION,
        tenant_id=tenant_id,
        owner_user_id=owner_user_id,
        personal_memory_space_id=space_id,
        hat_scope_id=personal_memory_hat_scope_id(tenant_id, owner_user_id, space_id),
        state=PersonalMemorySpaceState.CONFIGURED,
        display_name="Step 39 corrections",
        quota_policy_id="quota-step39-v1",
        quota_policy_digest=digest("quota"),
        model_bindings=(binding,),
        state_version=1,
        configuration_version=1,
        created_at=NOW,
        updated_at=NOW,
    )


def trusted_context(
    *,
    slot: PersonalMemoryHatSlot | None = None,
) -> CriticTrustedCandidateContext:
    selected = slot or target_slot()
    binding = selected.model_bindings[0]
    run = KernelRunIdentity(
        kernel_run_id="run-step39-a",
        tenant_id=selected.tenant_id,
        user_id=selected.owner_user_id,
        personal_memory_space_id=selected.personal_memory_space_id,
        model_binding_id=binding.binding_id,
        created_at=NOW + timedelta(seconds=1),
    )
    lineage = CorrectionCandidateRouteResultLineage(
        schema_version=STEP28_SCHEMA_VERSION,
        request_id=run.kernel_run_id,
        original_query_digest=hashlib.sha256(QUESTION.encode("utf-8")).hexdigest(),
        route_hash=digest("route"),
        result_hash=digest("verified-answer-result"),
        knowledge_route=KnowledgeRoute.HAT_ENFORCE,
        selected_hat_id="german-law",
        selected_hat_version="1.0.0",
        selected_manifest_digest=digest("manifest"),
        effective_scope=scope(),
        answer_status=AnswerStatus.VERIFIED,
        evidence_status=EvidenceStatus.SUFFICIENT,
        draft_v1_hash=digest("draft-v1"),
        draft_v2_hash=digest("draft-v2"),
        correction_packet_hash=digest("packet"),
        verification_summary_hash=digest("verification-summary"),
        verified_answer_hash=digest("verified-answer"),
    )
    return CriticTrustedCandidateContext(
        kernel_run=run,
        target_slot=selected,
        route_result_lineage=lineage,
        detected_at=NOW + timedelta(seconds=2),
    )


def review_request(
    *,
    context: CriticTrustedCandidateContext | None = None,
) -> CriticReviewRequest:
    trusted = context or trusted_context()
    lineage = trusted.route_result_lineage
    evidence = CriticEvidenceReference(
        reference_id=digest("evidence-link"),
        evidence_id="evidence-step39-a",
        source_id="source-step39-a",
        source_version_id="version-step39-a",
        chunk_id="chunk-step39-a",
        relation=ClaimEvidenceRelation.REFUTES,
        authority_level=SourceAuthorityLevel.OFFICIAL_PRIMARY,
        publication_state=SourcePublicationState.PUBLISHED,
        temporal_applicability=TemporalApplicability.APPLICABLE,
        freshness_status=FreshnessStatus.FRESH,
        snippet=DRAFT_V2_TEXT,
    )
    artifacts = (
        CriticTextArtifact(
            CriticArtifactKind.DRAFT_V1,
            "draft-step39-a",
            lineage.draft_v1_hash,
            DRAFT_V1_TEXT,
        ),
        CriticTextArtifact(
            CriticArtifactKind.DRAFT_V2,
            "draft-v2-step39-a",
            lineage.draft_v2_hash,
            DRAFT_V2_TEXT,
        ),
        CriticTextArtifact(
            CriticArtifactKind.VERIFIED_ANSWER,
            "verified-answer-step39-a",
            lineage.verified_answer_hash,
            VERIFIED_ANSWER_TEXT,
        ),
    )
    claims = (
        CriticClaimReference(
            claim_id="claim-step39-a",
            draft_id="draft-step39-a",
            statement=DRAFT_V1_TEXT,
            claim_category="TEMPORAL",
            verification_status=CriticClaimStatus.REFUTED,
            evidence_reference_ids=(evidence.reference_id,),
        ),
    )
    policy = load_critic_policy()
    prompt = load_critic_prompt_template()
    return CriticReviewRequest(
        schema_version=STEP39_SCHEMA_VERSION,
        critic_request_id="critic-review-step39-a",
        tenant_id=trusted.kernel_run.tenant_id,
        owner_user_id=trusted.kernel_run.user_id,
        request_id=trusted.kernel_run.kernel_run_id,
        kernel_run_id=trusted.kernel_run.kernel_run_id,
        route_hash=lineage.route_hash,
        selected_hat_id=lineage.selected_hat_id,
        selected_hat_version=lineage.selected_hat_version,
        selected_manifest_digest=lineage.selected_manifest_digest,
        original_query=QUESTION,
        original_query_digest=lineage.original_query_digest,
        artifacts=artifacts,
        claim_references=claims,
        evidence_references=(evidence,),
        effective_scope=lineage.effective_scope,
        correction_packet_hash=lineage.correction_packet_hash,
        evidence_status=lineage.evidence_status,
        temporal_applicability=TemporalApplicability.APPLICABLE,
        freshness_status=FreshnessStatus.FRESH,
        conflict_preserved=True,
        bounded_review_objective="Identify a bounded evidence-linked correction candidate.",
        critic_policy_id=policy.policy_id,
        critic_policy_version=policy.policy_version,
        critic_policy_digest=policy.policy_digest,
        critic_prompt_id=prompt.prompt_id,
        critic_prompt_version=prompt.prompt_version,
        critic_prompt_digest=prompt.prompt_digest,
        provider_identity=load_approved_provider_spec().provider_identity(),
    )


def assessment_document(
    request: CriticReviewRequest,
    *,
    issue: bool = True,
) -> dict[str, object]:
    bindings = json.loads(build_critic_prompt_payload(request))["bindings_to_echo_exactly"]
    if not issue:
        return {
            **bindings,
            "schema_version": STEP39_SCHEMA_VERSION,
            "issue_detected": False,
            "issue_type": "NO_ISSUE",
            "affected_claim_ids": [],
            "candidate_correction_text": None,
            "evidence_reference_ids": [],
            "reason_codes": ["NO_ISSUE"],
            "diagnostic_confidence_basis_points": None,
            "limitations": [
                "BOUNDED_CONTEXT_ONLY",
                "HUMAN_VALIDATION_REQUIRED",
                "NOT_CANONICAL_EVIDENCE",
                "NO_APPROVAL_AUTHORITY",
                "NO_EXECUTION_AUTHORITY",
            ],
        }
    return {
        **bindings,
        "schema_version": STEP39_SCHEMA_VERSION,
        "issue_detected": True,
        "issue_type": "TEMPORAL_MISMATCH",
        "affected_claim_ids": [request.claim_references[0].claim_id],
        "candidate_correction_text": DRAFT_V2_TEXT,
        "evidence_reference_ids": [request.evidence_references[0].reference_id],
        "reason_codes": [
            "CANDIDATE_NON_AUTHORITATIVE",
            "CLAIM_REFERENCE_MATCHED",
            "EVIDENCE_REFERENCE_MATCHED",
            "ISSUE_DETECTED",
        ],
        "diagnostic_confidence_basis_points": 9000,
        "limitations": [
            "BOUNDED_CONTEXT_ONLY",
            "HUMAN_VALIDATION_REQUIRED",
            "NOT_CANONICAL_EVIDENCE",
            "NO_APPROVAL_AUTHORITY",
            "NO_EXECUTION_AUTHORITY",
        ],
    }


class FakeProvider:
    def __init__(
        self,
        request: CriticReviewRequest,
        *,
        document: dict[str, object] | None = None,
        errors: tuple[BaseException, ...] = (),
    ) -> None:
        self._identity = request.provider_identity
        self._document = document or assessment_document(request)
        self._errors = list(errors)
        self.calls = 0
        self.requests: list[object] = []

    def provider_identity(self):
        return self._identity

    def generate(self, request, timeout_policy):
        self.calls += 1
        self.requests.append(request)
        if self._errors:
            raise self._errors.pop(0)
        return ProviderResponse(
            provider_identity_digest=self._identity.identity_digest,
            model_id=self._identity.model_id,
            model_version=self._identity.model_revision_or_declared_version,
            provider_request_id=f"step39-fake-{self.calls}",
            finish_reason="stop",
            response_content=canonical_json(self._document),
            usage_metadata={
                "prompt_tokens": 80,
                "completion_tokens": 40,
                "total_tokens": 120,
            },
            latency_milliseconds=1,
        )


class InMemoryCriticIntake:
    """Minimal deterministic Step 28 acknowledgement fake, not a DB substitute."""

    def __init__(self) -> None:
        self.by_submission: dict[str, CorrectionCandidateEnvelope] = {}
        self.by_candidate: dict[str, CorrectionCandidateEnvelope] = {}
        self.calls = 0

    def submit_critic_loop_candidate(self, envelope: CorrectionCandidateEnvelope):
        verify_correction_candidate_envelope(envelope)
        self.calls += 1
        submission = envelope.submission
        previous = self.by_submission.get(submission.submission_id)
        if previous is not None:
            if previous.envelope_hash != envelope.envelope_hash:
                raise IntegrityError("changed Critic replay")
            disposition = CorrectionCandidateIntakeDisposition.EXACT_REPLAY
            stored = previous
        else:
            stored = self.by_candidate.get(envelope.candidate_id)
            if stored is None:
                stored = envelope
                self.by_candidate[envelope.candidate_id] = envelope
                disposition = CorrectionCandidateIntakeDisposition.ACCEPTED
            elif stored.envelope_hash == envelope.envelope_hash:
                disposition = CorrectionCandidateIntakeDisposition.DUPLICATE
            else:
                raise IntegrityError("Critic candidate content conflict")
            self.by_submission[submission.submission_id] = stored
        receipt = build_correction_candidate_intake_receipt(
            envelope,
            accepted_at=envelope.submission.submitted_at,
            disposition=disposition,
        )
        return stored, receipt


__all__ = [
    "DRAFT_V1_TEXT",
    "DRAFT_V2_TEXT",
    "FakeProvider",
    "InMemoryCriticIntake",
    "NOW",
    "QUESTION",
    "assessment_document",
    "digest",
    "review_request",
    "scope",
    "target_slot",
    "trusted_context",
]
