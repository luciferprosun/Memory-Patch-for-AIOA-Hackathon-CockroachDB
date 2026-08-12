#!/usr/bin/env python3
"""Provider-free controlled validation for the optional Step 39 Critic bridge.

The runner reads only committed Step 38 evidence, reconstructs the bounded
German Law fixture with deterministic repository fakes, and exercises the
existing Step 28 and Step 29 contracts.  It starts no database, reads no
credential, opens no network connection, and never invokes Step 30.
"""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

from aioa_memory_kernel.audit_ledger import (  # noqa: E402
    STEP33_GENESIS_SENTINEL,
    build_audit_event_envelope,
    build_audit_ledger_entry,
    verify_audit_chain,
    verify_audit_event_draft,
)
from aioa_memory_kernel.contracts.enums import (  # noqa: E402
    ActorType,
    AnswerStatus,
    CorrectionCandidateState,
    PatchState,
    PersonalMemorySpaceState,
)
from aioa_memory_kernel.contracts.exceptions import IntegrityError  # noqa: E402
from aioa_memory_kernel.contracts.identities import KernelRunIdentity  # noqa: E402
from aioa_memory_kernel.contracts.serialization import (  # noqa: E402
    canonical_json,
    canonical_sha256,
)
from aioa_memory_kernel.critic import (  # noqa: E402
    CRITIC_BRIDGE_VERSION,
    CRITIC_REVIEW_OBJECTIVE,
    STEP39_SCHEMA_VERSION,
    CriticArtifactKind,
    CriticBridgeStatus,
    CriticClaimReference,
    CriticClaimStatus,
    CriticEvidenceReference,
    CriticPromptLoopService,
    CriticReviewRequest,
    CriticTextArtifact,
    CriticTrustedCandidateContext,
    load_critic_policy,
    load_critic_prompt_template,
    map_critic_assessment_to_step28,
    submit_critic_step28_candidate,
)
from aioa_memory_kernel.critic.audit import (  # noqa: E402
    critic_candidate_detected_event,
)
from aioa_memory_kernel.modeling import (  # noqa: E402
    ModelAdapterError,
    ModelReasonCode,
    load_approved_provider_spec,
)
from aioa_memory_kernel.personal_memory import (  # noqa: E402
    STEP27_SCHEMA_VERSION,
    STEP28_SCHEMA_VERSION,
    STEP29_SCHEMA_VERSION,
    CorrectionCandidateIntakeDisposition,
    CorrectionCandidateMetadata,
    CorrectionCandidateReasonCode,
    CorrectionCandidateRouteResultLineage,
    CorrectionCandidateTrigger,
    CreatePersonalMemoryPatchProposal,
    PersonalMemoryBindingMode,
    PersonalMemoryHatSlot,
    PersonalMemoryModelBinding,
    PersonalMemoryPatchValidationError,
    ProposalConflictResult,
    ProposalDedupResult,
    ProposalGateResult,
    advance_personal_memory_patch_to_awaiting_approval,
    bind_personal_memory_patch_evidence,
    build_correction_candidate_envelope,
    build_personal_memory_patch_evidence_binding,
    build_personal_memory_patch_proposal,
    build_personal_memory_patch_validation_receipt,
    personal_memory_hat_scope_id,
    proposal_conflict_between,
    validate_personal_memory_patch,
    verify_personal_memory_patch_state,
)
from aioa_memory_kernel.reliability import (  # noqa: E402
    FailureDirective,
    FailurePoint,
    InjectedFailure,
)
from aioa_memory_kernel.security import assert_secret_free  # noqa: E402
from aioa_memory_kernel.temporal import (  # noqa: E402
    FreshnessStatus,
    TemporalApplicability,
)
from tests.step39_support import (  # noqa: E402
    FakeProvider,
    InMemoryCriticIntake,
    assessment_document,
)
from tests.failure_injection.harness import ScriptedFailureInjector  # noqa: E402
from tests.test_step38_german_law_e2e import primary_lineage  # noqa: E402


SCHEMA_VERSION = "step39-critic-bridge-validation-1a"
STEP38_CLOSURE_SHA = "939395d355ce0630c5044c4ab427082c3cf72d23"
STEP38_EVIDENCE_FILE_SHA256 = (
    "b43152c0b7e9020b4078f2abd89dc25986b14cbd09e5cf4b7a67ce525130eb13"
)
STEP38_VALIDATION_DIGEST = (
    "b3536f019354226c77f8672464d21986b4dc37e63f8e0d725352e57f5d7e0042"
)
STEP38_FIXTURE_FILE_SHA256 = (
    "03afd8caa0a37bb911cfd968a157305d939caff0cbd7275e690ae669ac9af342"
)
STEP38_GOLDEN_SUITE_HASH = (
    "b9c424e2b00ed13317f73d4fd86bbd2c96b1f2e606765f98dc35cadc0663cd50"
)
STEP38_UPSTREAM_LINEAGE_HASH = (
    "b3175f1b3476aa88453bca4623475c0ce7835488af623f51a43b0b7b8a793a23"
)
STEP38_LIVE_ROUTE_HASH = (
    "e6b1912195e8c3cecb2a79cab235592c21b9fe6cab0ce5c0fa8c2e7a18794d0c"
)
STEP38_LIVE_DRAFT_V1_HASH = (
    "17513ff270ad13cab959bec4e80856cf3f9e1b69658b2c0040d3023cefba5139"
)
STEP38_LIVE_EVIDENCE_BUNDLE_HASH = (
    "563401ac507d0eef448b0b189bbfde3a8ab117c17eedc490ba92a2d977ed291e"
)
STEP38_LIVE_PACKET_HASH = (
    "5af8baa1a3708dc2fd6e6f951392b7799ef85a7a2065c91e5b01367e02b26bb6"
)
STEP38_LIVE_VERIFIED_ANSWER_HASH = (
    "21b3e8fd4f9c38eddcb5a545fe7d6b1631310357d3260ea3b31015fe0168cdea"
)
STEP38_QUESTION_DIGEST = (
    "f33243aa0b47a12cf7e86bae77c079d20d573f43ccea740c0dafc91d738dfa0b"
)
STEP38_EVIDENCE = (
    ROOT / "docs/evidence/e2e/step38-german-law-full-e2e-validation.json"
)
STEP38_FIXTURE = ROOT / "tests/fixtures/step38_german_law_cases.json"
FIXTURE_CLASSIFICATION = "REAL_STEP38_HASH_LINEAGE + SYNTHETIC_CRITIC_ADAPTER"


class ValidationFailure(RuntimeError):
    """A sanitized controlled-validation failure."""

    def __init__(self, code: str) -> None:
        super().__init__("Step 39 controlled validation failed")
        self.code = code


class _InjectedTransientProvider:
    """Test-only Step 37 failure point in front of one deterministic fake."""

    def __init__(
        self,
        delegate: FakeProvider,
        injector: ScriptedFailureInjector,
        *,
        subject_hash: str,
    ) -> None:
        self._delegate = delegate
        self._injector = injector
        self._subject_hash = subject_hash
        self.calls = 0

    def provider_identity(self):
        return self._delegate.provider_identity()

    def generate(self, request, timeout_policy):
        self.calls += 1
        try:
            self._injector.hit(
                FailurePoint.PROVIDER_TRANSIENT_FAILURE,
                subject_hash=self._subject_hash,
            )
        except InjectedFailure as failure:
            raise ModelAdapterError(
                ModelReasonCode.MODEL_TRANSIENT_FAILURE,
                retryable=True,
                unknown_completion=failure.completion_unknown,
            ) from None
        return self._delegate.generate(request, timeout_policy)


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise ValidationFailure(code)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(64 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _step38_anchor() -> Mapping[str, Any]:
    _require(
        _file_sha256(STEP38_EVIDENCE) == STEP38_EVIDENCE_FILE_SHA256,
        "STEP38_EVIDENCE_FILE_DIGEST_MISMATCH",
    )
    _require(
        _file_sha256(STEP38_FIXTURE) == STEP38_FIXTURE_FILE_SHA256,
        "STEP38_FIXTURE_FILE_DIGEST_MISMATCH",
    )
    document = json.loads(STEP38_EVIDENCE.read_text(encoding="utf-8"))
    _require(
        document.get("validation_digest") == STEP38_VALIDATION_DIGEST
        and canonical_sha256(document, exclude_fields=("validation_digest",))
        == STEP38_VALIDATION_DIGEST,
        "STEP38_VALIDATION_DIGEST_MISMATCH",
    )
    flow = document.get("real_model_flow", {})
    cases = document.get("golden_cases", {})
    corpus = document.get("real_corpus", {})
    case_attempts = flow.get("case_attempts", ())
    selected_attempt = (
        case_attempts[0]
        if isinstance(case_attempts, list) and len(case_attempts) == 1
        else {}
    )
    _require(
        document.get("status") == "PASS_LIVE_COHERENT_LINEAGE"
        and document.get("closure_eligible") is True
        and document.get("verified_upstream_lineage_hash")
        == STEP38_UPSTREAM_LINEAGE_HASH
        and cases.get("suite_hash") == STEP38_GOLDEN_SUITE_HASH
        and flow.get("selected_case_id") == "primary-entry-into-force"
        and selected_attempt.get("case_id") == "primary-entry-into-force"
        and selected_attempt.get("question_digest") == STEP38_QUESTION_DIGEST
        and flow.get("route_hash") == STEP38_LIVE_ROUTE_HASH
        and flow.get("draft_v1_hash") == STEP38_LIVE_DRAFT_V1_HASH
        and flow.get("evidence_bundle_hash") == STEP38_LIVE_EVIDENCE_BUNDLE_HASH
        and flow.get("correction_packet_hash") == STEP38_LIVE_PACKET_HASH
        and flow.get("verified_answer_hash") == STEP38_LIVE_VERIFIED_ANSWER_HASH
        and corpus.get("source_id") == "de-federal-gii-bjnr1330a0023"
        and corpus.get("official_identifier") == "BJNR1330A0023"
        and corpus.get("publication_state") == "PUBLISHED",
        "STEP38_FROZEN_LINEAGE_MISMATCH",
    )
    return {
        "closure_sha": STEP38_CLOSURE_SHA,
        "evidence_file_sha256": STEP38_EVIDENCE_FILE_SHA256,
        "fixture_file_sha256": STEP38_FIXTURE_FILE_SHA256,
        "golden_suite_hash": STEP38_GOLDEN_SUITE_HASH,
        "question_digest": STEP38_QUESTION_DIGEST,
        "route_hash": STEP38_LIVE_ROUTE_HASH,
        "draft_v1_hash": STEP38_LIVE_DRAFT_V1_HASH,
        "evidence_bundle_hash": STEP38_LIVE_EVIDENCE_BUNDLE_HASH,
        "correction_packet_hash": STEP38_LIVE_PACKET_HASH,
        "verified_answer_hash": STEP38_LIVE_VERIFIED_ANSWER_HASH,
        "validation_digest": STEP38_VALIDATION_DIGEST,
        "verified_upstream_lineage_hash": STEP38_UPSTREAM_LINEAGE_HASH,
    }


def _corrected_lineage(values: Mapping[str, Any]):
    verifications = tuple(
        item
        for item in values["pipeline"].ordered_claim_verifications
        if item.corrected_evidence_proof is not None
        and item.final_step25_verdict.value == "VERIFIED_SUPPORTED"
    )
    _require(len(verifications) == 1, "CORRECTED_CLAIM_PROOF_NOT_UNIQUE")
    verification = verifications[0]
    proof = verification.corrected_evidence_proof
    assert proof is not None
    link = proof.original_evidence_link
    source_claim = next(
        (
            item
            for item in values["snapshot"].ordered_claims
            if item.claim_id == link.claim_id
        ),
        None,
    )
    target_claim = next(
        (
            item
            for item in values["pipeline"].ordered_claims
            if item.claim_id == verification.claim_id
        ),
        None,
    )
    evidence_item = next(
        (
            item
            for item in values["outcome"].bundle.ordered_items
            if item.item_hash == link.step20_item_hash
        ),
        None,
    )
    _require(
        source_claim is not None and target_claim is not None and evidence_item is not None,
        "CORRECTED_CLAIM_LINEAGE_INCOMPLETE",
    )
    return verification, proof, link, source_claim, target_claim, evidence_item


def _german_law_critic_fixture(values: Mapping[str, Any]):
    route = values["route"]
    temporal = values["temporal"]
    answer = values["final"].verified_answer
    _require(answer is not None, "VERIFIED_ANSWER_MISSING")
    verification, proof, link, source_claim, target_claim, item = _corrected_lineage(
        values
    )
    now = temporal.trusted_now
    space_id = "slot-step39-german-law"
    binding = PersonalMemoryModelBinding(
        schema_version=STEP27_SCHEMA_VERSION,
        tenant_id=route.tenant_id,
        owner_user_id=route.user_id,
        personal_memory_space_id=space_id,
        provider_id="provider-neutral",
        model_id="step39-memory-target",
        model_revision_or_declared_version="revision-1",
        binding_mode=PersonalMemoryBindingMode.EXACT_MODEL,
        enabled=True,
        binding_version=1,
        bound_at=now,
    )
    slot = PersonalMemoryHatSlot(
        schema_version=STEP27_SCHEMA_VERSION,
        tenant_id=route.tenant_id,
        owner_user_id=route.user_id,
        personal_memory_space_id=space_id,
        hat_scope_id=personal_memory_hat_scope_id(
            route.tenant_id, route.user_id, space_id
        ),
        state=PersonalMemorySpaceState.CONFIGURED,
        display_name="Step 39 German Law corrections",
        quota_policy_id="quota-step39-german-law-1a",
        quota_policy_digest=canonical_sha256("step39-german-law-quota"),
        model_bindings=(binding,),
        state_version=1,
        configuration_version=1,
        created_at=now,
        updated_at=now,
    )
    run = KernelRunIdentity(
        kernel_run_id=route.request_id,
        tenant_id=route.tenant_id,
        user_id=route.user_id,
        personal_memory_space_id=space_id,
        model_binding_id=binding.binding_id,
        created_at=now,
    )
    lineage = CorrectionCandidateRouteResultLineage(
        schema_version=STEP28_SCHEMA_VERSION,
        request_id=route.request_id,
        original_query_digest=values["draft"].original_query_digest,
        route_hash=route.route_hash,
        result_hash=answer.answer_hash,
        knowledge_route=route.knowledge_route,
        selected_hat_id=route.selected_hat_id,
        selected_hat_version=route.selected_hat_version,
        selected_manifest_digest=route.selected_manifest_digest,
        effective_scope=route.effective_scope,
        answer_status=AnswerStatus.VERIFIED,
        evidence_status=temporal.evidence_status,
        draft_v1_hash=values["draft"].draft_hash,
        draft_v2_hash=values["pipeline"].draft_v2.draft_v2_hash,
        correction_packet_hash=values["packet"].packet_hash,
        verification_summary_hash=(
            values["pipeline"].verification_summary.summary_hash
        ),
        verified_answer_hash=answer.answer_hash,
    )
    context = CriticTrustedCandidateContext(
        kernel_run=run,
        target_slot=slot,
        route_result_lineage=lineage,
        detected_at=now + timedelta(seconds=1),
    )
    snippet = item.excerpt.text[
        link.evidence_start_offset : link.evidence_end_offset
    ]
    evidence = CriticEvidenceReference(
        reference_id=link.link_hash,
        evidence_id=link.evidence_id,
        source_id=link.source_id,
        source_version_id=link.knowledge_version_id,
        chunk_id=link.chunk_id,
        relation=link.relation,
        authority_level=link.authority_level,
        publication_state=link.publication_state,
        temporal_applicability=link.temporal_applicability,
        freshness_status=link.freshness_status,
        snippet=snippet,
    )
    policy = load_critic_policy()
    prompt = load_critic_prompt_template()
    request = CriticReviewRequest(
        schema_version=STEP39_SCHEMA_VERSION,
        critic_request_id="critic-review-step39-german-law-primary",
        tenant_id=route.tenant_id,
        owner_user_id=route.user_id,
        request_id=route.request_id,
        kernel_run_id=route.request_id,
        route_hash=route.route_hash,
        selected_hat_id=route.selected_hat_id,
        selected_hat_version=route.selected_hat_version,
        selected_manifest_digest=route.selected_manifest_digest,
        original_query=values["case"].question,
        original_query_digest=values["draft"].original_query_digest,
        artifacts=(
            CriticTextArtifact(
                CriticArtifactKind.DRAFT_V1,
                source_claim.draft_id,
                values["draft"].draft_hash,
                values["draft"].draft_text,
            ),
            CriticTextArtifact(
                CriticArtifactKind.DRAFT_V2,
                "step39-german-law-draft-v2",
                values["pipeline"].draft_v2.draft_v2_hash,
                values["pipeline"].draft_v2.draft_text,
            ),
            CriticTextArtifact(
                CriticArtifactKind.VERIFIED_ANSWER,
                answer.answer_id,
                answer.answer_hash,
                answer.answer_text,
            ),
        ),
        claim_references=(
            CriticClaimReference(
                claim_id=source_claim.claim_id,
                draft_id=source_claim.draft_id,
                statement=source_claim.exact_claim_text,
                claim_category=source_claim.claim_type.value,
                verification_status=CriticClaimStatus.REFUTED,
                evidence_reference_ids=(link.link_hash,),
            ),
        ),
        evidence_references=(evidence,),
        effective_scope=route.effective_scope,
        correction_packet_hash=values["packet"].packet_hash,
        evidence_status=temporal.evidence_status,
        temporal_applicability=TemporalApplicability.APPLICABLE,
        freshness_status=FreshnessStatus.FRESH,
        conflict_preserved=True,
        bounded_review_objective=CRITIC_REVIEW_OBJECTIVE,
        critic_policy_id=policy.policy_id,
        critic_policy_version=policy.policy_version,
        critic_policy_digest=policy.policy_digest,
        critic_prompt_id=prompt.prompt_id,
        critic_prompt_version=prompt.prompt_version,
        critic_prompt_digest=prompt.prompt_digest,
        provider_identity=load_approved_provider_spec().provider_identity(),
    )
    document = assessment_document(request)
    document["candidate_correction_text"] = target_claim.exact_claim_text
    return {
        "values": values,
        "context": context,
        "request": request,
        "document": document,
        "link": link,
        "target_claim": target_claim,
        "proof": proof,
        "verification": verification,
    }


def _core_projection(values: Mapping[str, Any]) -> Mapping[str, str]:
    answer = values["final"].verified_answer
    _require(answer is not None, "CORE_VERIFIED_ANSWER_MISSING")
    return {
        "case_hash": values["case"].case_hash,
        "route_hash": values["route"].route_hash,
        "draft_v1_hash": values["draft"].draft_hash,
        "claim_snapshot_hash": values["snapshot"].snapshot_hash,
        "evidence_bundle_hash": values["outcome"].bundle.bundle_hash,
        "temporal_result_hash": values["temporal"].result_hash,
        "correction_packet_hash": values["packet"].packet_hash,
        "draft_v2_hash": values["pipeline"].draft_v2.draft_v2_hash,
        "verification_summary_hash": (
            values["pipeline"].verification_summary.summary_hash
        ),
        "verified_answer_hash": answer.answer_hash,
    }


def _accepted_candidate(fixture: Mapping[str, Any]):
    request = fixture["request"]
    provider = FakeProvider(request, document=fixture["document"])
    result = CriticPromptLoopService(provider, sleep=lambda _delay: None).review(
        request, enabled=True
    )
    _require(
        result.status is CriticBridgeStatus.ASSESSMENT_ACCEPTED
        and result.assessment is not None
        and provider.calls == 1,
        "CRITIC_ACCEPTED_ASSESSMENT_FAILED",
    )
    mapping, envelope = map_critic_assessment_to_step28(
        request,
        result,
        trusted_context=fixture["context"],
    )
    _require(envelope is not None, "CRITIC_CANDIDATE_MAPPING_MISSING")
    intake = InMemoryCriticIntake()
    stored, receipt = submit_critic_step28_candidate(
        intake, request, result, fixture["context"], mapping, envelope
    )
    _require(
        receipt.disposition is CorrectionCandidateIntakeDisposition.ACCEPTED
        and stored.envelope_hash == envelope.envelope_hash
        and intake.calls == 1,
        "CRITIC_STEP28_INTAKE_FAILED",
    )
    replayed, replay_receipt = submit_critic_step28_candidate(
        intake, request, result, fixture["context"], mapping, envelope
    )
    _require(
        replay_receipt.disposition
        is CorrectionCandidateIntakeDisposition.EXACT_REPLAY
        and replayed.envelope_hash == stored.envelope_hash
        and len(intake.by_candidate) == 1,
        "CRITIC_STEP28_REPLAY_FAILED",
    )
    audit = critic_candidate_detected_event(
        request,
        result,
        fixture["context"],
        stored,
        mapping,
        receipt,
        occurred_at=receipt.accepted_at,
    )
    verify_audit_event_draft(audit)
    audit_envelope = build_audit_event_envelope(
        audit,
        sequence_number=1,
        previous_event_hash=STEP33_GENESIS_SENTINEL,
    )
    audit_entry = build_audit_ledger_entry(audit_envelope, audit.event_payload)
    audit_chain = verify_audit_chain(audit_envelope.chain_id, (audit_entry,))
    _require(
        audit_chain.verified
        and audit_chain.event_count == 1
        and audit_chain.first_hash == audit_envelope.event_hash
        and audit_chain.last_hash == audit_envelope.event_hash,
        "CRITIC_AUDIT_CHAIN_VERIFICATION_FAILED",
    )
    return (
        result,
        mapping,
        stored,
        receipt,
        replay_receipt,
        audit,
        audit_envelope,
        audit_entry,
        audit_chain,
    )


def _kernel_equivalent_envelope(stored, slot, *, submitted_at):
    submission = stored.submission
    candidate = replace(
        submission.candidate,
        event_id="event-step39-equivalent-kernel-candidate",
        source_component=ActorType.KNOWLEDGE_KERNEL,
    )
    metadata = CorrectionCandidateMetadata(
        schema_version=STEP28_SCHEMA_VERSION,
        trigger=CorrectionCandidateTrigger.KNOWLEDGE_KERNEL_DETECTED,
        producer_id="kernel-step39-dedup-proof",
        producer_version="1.0.0",
        reason_codes=(CorrectionCandidateReasonCode.SOURCE_KNOWLEDGE_KERNEL,),
    )
    return build_correction_candidate_envelope(
        candidate=candidate,
        kernel_run=submission.run_identity,
        slot=slot,
        route_result_lineage=submission.lineage,
        metadata=metadata,
        idempotency_key="step39-equivalent-kernel-candidate",
        submitted_at=submitted_at,
    )


def _create_command(envelope, *, key: str, requested_at):
    candidate = envelope.submission.candidate
    return CreatePersonalMemoryPatchProposal(
        schema_version=STEP29_SCHEMA_VERSION,
        tenant_id=candidate.tenant_id,
        owner_user_id=candidate.user_id,
        personal_memory_space_id=candidate.personal_memory_space_id,
        candidate_id=envelope.candidate_id,
        candidate_envelope_hash=envelope.envelope_hash,
        expected_target_binding_hash=(
            envelope.submission.target_slot_binding.target_binding_hash
        ),
        idempotency_key=key,
        requested_at=requested_at,
    )


def _step29_to_awaiting_approval(fixture: Mapping[str, Any], stored):
    values = fixture["values"]
    now = fixture["context"].detected_at
    proposed = build_personal_memory_patch_proposal(
        stored,
        _create_command(
            stored,
            key="step39-critic-create-proposal",
            requested_at=now + timedelta(seconds=1),
        ),
    )
    answer = values["final"].verified_answer
    assert answer is not None
    evidence = build_personal_memory_patch_evidence_binding(
        proposed,
        bundles=(values["outcome"].bundle,),
        temporal_result=values["temporal"],
        claim_links=(fixture["link"],),
        claim_assessments=values["snapshot"].ordered_candidate_assessments,
        correction_packet=values["packet"],
        verified_answer=answer,
        step25_result=values["pipeline"],
        evidence_context=values["context"],
        bound_at=now + timedelta(seconds=2),
    )
    bound = bind_personal_memory_patch_evidence(
        proposed,
        evidence,
        transitioned_at=now + timedelta(seconds=2),
    )
    validation = build_personal_memory_patch_validation_receipt(
        bound,
        dedup_result=ProposalDedupResult.PASS,
        conflict_result=ProposalConflictResult.PASS,
        temporal_trusted_now=values["temporal"].trusted_now,
        owner_scope_result=ProposalGateResult.PASS,
        slot_state_result=ProposalGateResult.PASS,
        quota_result=ProposalGateResult.PASS,
        model_binding_result=ProposalGateResult.PASS,
        validated_at=now + timedelta(seconds=3),
    )
    validated = validate_personal_memory_patch(
        bound,
        validation,
        transitioned_at=now + timedelta(seconds=3),
    )
    awaiting = advance_personal_memory_patch_to_awaiting_approval(
        validated,
        validation_receipt_hash=validation.receipt_hash,
        transitioned_at=now + timedelta(seconds=4),
    )
    verify_personal_memory_patch_state(awaiting)
    _require(
        (
            proposed.state,
            bound.state,
            validated.state,
            awaiting.state,
        )
        == (
            PatchState.PROPOSED,
            PatchState.EVIDENCE_BOUND,
            PatchState.VALIDATED,
            PatchState.AWAITING_APPROVAL,
        ),
        "STEP29_STATE_MACHINE_FAILED",
    )

    kernel_envelope = _kernel_equivalent_envelope(
        stored,
        fixture["context"].target_slot,
        submitted_at=stored.submission.submitted_at + timedelta(seconds=1),
    )
    kernel_proposed = build_personal_memory_patch_proposal(
        kernel_envelope,
        _create_command(
            kernel_envelope,
            key="step39-equivalent-kernel-proposal",
            requested_at=now + timedelta(seconds=2),
        ),
    )
    _require(
        kernel_proposed.proposal.proposal_hash != proposed.proposal.proposal_hash
        and kernel_proposed.proposal.exact_dedup_key
        == proposed.proposal.exact_dedup_key
        and proposal_conflict_between(
            proposed.proposal, kernel_proposed.proposal
        )
        is ProposalConflictResult.EXISTING_PATCH_CONFLICT,
        "STEP29_EQUIVALENT_CORRECTION_DEDUP_MISSING",
    )
    duplicate_receipt = build_personal_memory_patch_validation_receipt(
        bound,
        dedup_result=ProposalDedupResult.EXACT_DUPLICATE,
        conflict_result=ProposalConflictResult.EXISTING_PATCH_CONFLICT,
        temporal_trusted_now=values["temporal"].trusted_now,
        owner_scope_result=ProposalGateResult.PASS,
        slot_state_result=ProposalGateResult.PASS,
        quota_result=ProposalGateResult.PASS,
        model_binding_result=ProposalGateResult.PASS,
        validated_at=now + timedelta(seconds=3),
    )
    _require(not duplicate_receipt.validated, "STEP29_DUPLICATE_WAS_VALIDATED")
    try:
        validate_personal_memory_patch(
            bound,
            duplicate_receipt,
            transitioned_at=now + timedelta(seconds=3),
        )
    except PersonalMemoryPatchValidationError:
        duplicate_denied = True
    else:
        duplicate_denied = False
    _require(duplicate_denied, "STEP29_DUPLICATE_PROGRESSED")
    return proposed, evidence, validation, awaiting, kernel_proposed, duplicate_receipt


def _invalid_output_case(request, document: Mapping[str, Any]) -> str:
    result = CriticPromptLoopService(
        FakeProvider(request, document=dict(document)),
        sleep=lambda _delay: None,
    ).review(request, enabled=True)
    _require(
        result.status is CriticBridgeStatus.INVALID_OUTPUT
        and result.assessment is None
        and result.core_memory_patch_unaffected,
        "CRITIC_INVALID_OUTPUT_DID_NOT_FAIL_CLOSED",
    )
    return result.status.value


def _spoofed_request_denied(request, context, **changes: Any) -> bool:
    spoofed = replace(request, **changes)
    document = assessment_document(spoofed)
    document["candidate_correction_text"] = (
        "Diese Anordnung tritt am 1. Januar 2024 in Kraft."
    )
    result = CriticPromptLoopService(
        FakeProvider(spoofed, document=document), sleep=lambda _delay: None
    ).review(spoofed, enabled=True)
    _require(
        result.status is CriticBridgeStatus.ASSESSMENT_ACCEPTED,
        "SPOOF_TEST_PROVIDER_RESULT_NOT_ACCEPTED",
    )
    try:
        map_critic_assessment_to_step28(
            spoofed,
            result,
            trusted_context=context,
        )
    except IntegrityError:
        return True
    return False


def run_validation() -> Mapping[str, Any]:
    anchor = _step38_anchor()
    values = primary_lineage()
    fixture = _german_law_critic_fixture(values)
    request = fixture["request"]
    context = fixture["context"]
    before = _core_projection(values)
    core_hash = canonical_sha256(before)

    disabled_provider = FakeProvider(
        request, errors=(AssertionError("disabled fake provider called"),)
    )
    disabled = CriticPromptLoopService(disabled_provider).review(
        request, enabled=False
    )
    _require(
        disabled.status is CriticBridgeStatus.DISABLED
        and disabled_provider.calls == 0
        and disabled.core_memory_patch_unaffected,
        "CRITIC_DISABLED_CASE_FAILED",
    )

    (
        result,
        mapping,
        stored,
        intake_receipt,
        replay_receipt,
        audit,
        audit_envelope,
        audit_entry,
        audit_chain,
    ) = (
        _accepted_candidate(fixture)
    )
    proposed, evidence, validation, awaiting, kernel_proposed, duplicate_receipt = (
        _step29_to_awaiting_approval(fixture, stored)
    )

    no_issue_document = assessment_document(request, issue=False)
    no_issue_provider = FakeProvider(request, document=no_issue_document)
    no_issue = CriticPromptLoopService(
        no_issue_provider, sleep=lambda _delay: None
    ).review(request, enabled=True)
    no_issue_mapping, no_issue_envelope = map_critic_assessment_to_step28(
        request, no_issue, trusted_context=context
    )
    _require(
        no_issue.status is CriticBridgeStatus.NO_ISSUE
        and no_issue_envelope is None
        and not no_issue_mapping.step28_required,
        "CRITIC_NO_ISSUE_CASE_FAILED",
    )

    unavailable = CriticPromptLoopService(None).review(request, enabled=True)
    _require(
        unavailable.status is CriticBridgeStatus.PROVIDER_UNAVAILABLE
        and unavailable.core_memory_patch_unaffected,
        "CRITIC_PROVIDER_UNAVAILABLE_CASE_FAILED",
    )
    transient_injector = ScriptedFailureInjector(
        (
            FailureDirective(
                FailurePoint.PROVIDER_TRANSIENT_FAILURE,
                (1,),
                completion_unknown=True,
            ),
        )
    )
    transient_provider = _InjectedTransientProvider(
        FakeProvider(request, document=fixture["document"]),
        transient_injector,
        subject_hash=request.request_hash,
    )
    transient = CriticPromptLoopService(
        transient_provider, sleep=lambda _delay: None
    ).review(request, enabled=True)
    _require(
        transient.status is CriticBridgeStatus.ASSESSMENT_ACCEPTED
        and transient_provider.calls == 2
        and transient.provider_call_receipt.unknown_completion,
        "CRITIC_TRANSIENT_RECOVERY_FAILED",
    )
    transient_mapping, transient_envelope = map_critic_assessment_to_step28(
        request,
        transient,
        trusted_context=context,
    )
    _require(
        transient_envelope is not None,
        "CRITIC_TRANSIENT_CANDIDATE_MAPPING_MISSING",
    )
    transient_intake = InMemoryCriticIntake()
    transient_stored, transient_receipt = submit_critic_step28_candidate(
        transient_intake,
        request,
        transient,
        context,
        transient_mapping,
        transient_envelope,
    )
    transient_replayed, transient_replay_receipt = submit_critic_step28_candidate(
        transient_intake,
        request,
        transient,
        context,
        transient_mapping,
        transient_envelope,
    )
    transient_injector.assert_fully_exercised()
    _require(
        transient_receipt.disposition
        is CorrectionCandidateIntakeDisposition.ACCEPTED
        and transient_replay_receipt.disposition
        is CorrectionCandidateIntakeDisposition.EXACT_REPLAY
        and transient_stored.envelope_hash == transient_replayed.envelope_hash
        and len(transient_intake.by_candidate) == 1,
        "CRITIC_TRANSIENT_RECOVERY_DUPLICATED_CANDIDATE",
    )
    exhausted_provider = FakeProvider(
        request,
        errors=(
            ModelAdapterError(ModelReasonCode.MODEL_TIMEOUT, retryable=True),
            ModelAdapterError(ModelReasonCode.MODEL_TIMEOUT, retryable=True),
        ),
    )
    exhausted = CriticPromptLoopService(
        exhausted_provider, sleep=lambda _delay: None
    ).review(request, enabled=True)
    _require(
        exhausted.status is CriticBridgeStatus.PROVIDER_UNAVAILABLE
        and exhausted_provider.calls == 2
        and exhausted.core_memory_patch_unaffected,
        "CRITIC_RETRY_BOUND_FAILED",
    )

    malformed_status = _invalid_output_case(request, {"malformed": True})
    approval_spoof = dict(fixture["document"])
    approval_spoof["approval_authority"] = True
    approval_spoof_status = _invalid_output_case(request, approval_spoof)
    fake_evidence = dict(fixture["document"])
    fake_evidence["evidence_reference_ids"] = [
        canonical_sha256("step39-fake-evidence")
    ]
    fake_evidence_status = _invalid_output_case(request, fake_evidence)
    source_spoof = dict(fixture["document"])
    source_spoof["source_authority"] = "OFFICIAL"
    source_spoof_status = _invalid_output_case(request, source_spoof)

    owner_denied = _spoofed_request_denied(
        request, context, owner_user_id="other-step39-user"
    )
    tenant_denied = _spoofed_request_denied(
        request, context, tenant_id="other-step39-tenant"
    )
    route_denied = _spoofed_request_denied(
        request,
        context,
        route_hash=canonical_sha256("step39-spoof-route"),
    )
    _require(
        owner_denied and tenant_denied and route_denied,
        "CRITIC_TRUSTED_CONTEXT_SPOOF_ACCEPTED",
    )

    after = _core_projection(values)
    _require(before == after, "STEP38_CORE_MUTATED_BY_CRITIC")
    _require(
        request.selected_hat_id == "german-law"
        and request.original_query_digest == STEP38_QUESTION_DIGEST
        and request.evidence_references[0].source_id
        == "de-federal-gii-bjnr1330a0023"
        and request.evidence_references[0].source_version_id
        == "legal-version-001123facb9c2ff3c2b693b2f2b6b2946511457bbbf5f7d9ddd1047c5e181e95",
        "GERMAN_LAW_REQUEST_LINEAGE_MISMATCH",
    )

    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "step": 39,
        "start_sha": STEP38_CLOSURE_SHA,
        "status": "PASS_PROVIDER_FREE_CONTROLLED",
        "closure_eligible": True,
        "validation_mode": "PROVIDER_FREE_DETERMINISTIC",
        "fixture_classification": FIXTURE_CLASSIFICATION,
        "critic_contract": {
            "bridge_version": CRITIC_BRIDGE_VERSION,
            "prompt_id": request.critic_prompt_id,
            "prompt_version": request.critic_prompt_version,
            "prompt_digest": request.critic_prompt_digest,
            "policy_id": request.critic_policy_id,
            "policy_version": request.critic_policy_version,
            "policy_digest": request.critic_policy_digest,
        },
        "step38_anchor": anchor,
        "german_law": {
            "selected_case_id": "primary-entry-into-force",
            "hat_id": request.selected_hat_id,
            "official_identifier": "BJNR1330A0023",
            "source_id": request.evidence_references[0].source_id,
            "source_version_id": request.evidence_references[0].source_version_id,
            "question_digest": request.original_query_digest,
            "offline_reconstruction_hash": core_hash,
            # The controlled lane reconstructs the current deterministic core
            # projection, while the exact live hashes remain frozen and
            # verified separately in step38_anchor rather than being claimed
            # as regenerated by an offline run.
            "step38_live_hashes_verified_from_committed_evidence": True,
            "offline_core_reconstruction_matches_live_lineage": False,
        },
        "core_independence": {
            "before_hash": core_hash,
            "disabled_after_hash": canonical_sha256(_core_projection(values)),
            "enabled_after_hash": canonical_sha256(_core_projection(values)),
            "failure_after_hash": canonical_sha256(after),
            "all_equal": True,
            "critic_optional": True,
        },
        "provider": {
            "adapter_class": "SYNTHETIC_CRITIC_ADAPTER",
            "provider_identity_digest": request.provider_identity.identity_digest,
            "real_provider_called": False,
            "real_critic_provider_validation": "UNAVAILABLE_NOT_REQUIRED",
            "network_calls": 0,
            "credential_count": 0,
            "database_processes_started": 0,
        },
        "accepted_candidate": {
            "critic_request_hash": request.request_hash,
            "bridge_result_hash": result.result_hash,
            "provider_call_receipt_hash": result.provider_call_receipt.receipt_hash,
            "assessment_hash": result.assessment.assessment_hash,
            "issue_type": result.assessment.issue_type.value,
            "mapping_hash": mapping.mapping_hash,
            "candidate_hash": stored.submission.candidate.content_hash,
            "candidate_envelope_hash": stored.envelope_hash,
            "step28_intake_receipt_hash": intake_receipt.receipt_hash,
            "step28_intake_disposition": intake_receipt.disposition.value,
            "exact_replay_receipt_hash": replay_receipt.receipt_hash,
            "exact_replay_disposition": replay_receipt.disposition.value,
            "durable_candidate_count": 1,
            "candidate_state": stored.submission.candidate.state.value,
        },
        "step29": {
            "proposal_hash": proposed.proposal.proposal_hash,
            "evidence_binding_hash": evidence.binding_hash,
            "validation_receipt_hash": validation.receipt_hash,
            "awaiting_approval_state_hash": awaiting.state_hash,
            "final_state": awaiting.state.value,
            "equivalent_kernel_proposal_hash": (
                kernel_proposed.proposal.proposal_hash
            ),
            "same_exact_dedup_key": True,
            "duplicate_validation_receipt_hash": duplicate_receipt.receipt_hash,
            "duplicate_validated": False,
            "duplicate_semantic_side_effects": 0,
            "step30_calls": 0,
            "human_owner_approval_required": mapping.step30_human_approval_required,
        },
        "audit": {
            "draft_hash": audit.draft_hash,
            "event_payload_digest": audit.event_payload_digest,
            "step28_intake_receipt_hash": (
                audit.lineage_hashes["step28_intake_receipt_hash"]
            ),
            "hash_only": True,
            "draft_verified": True,
            "chain_id": audit_envelope.chain_id,
            "event_hash": audit_envelope.event_hash,
            "append_receipt_hash": audit_entry.append_receipt.receipt_hash,
            "chain_verified": audit_chain.verified,
            "event_count": audit_chain.event_count,
            "verification_result_hash": audit_chain.result_hash,
        },
        "case_matrix": {
            "critic_disabled": disabled.status.value,
            "critic_enabled_issue": result.status.value,
            "critic_enabled_no_issue": no_issue.status.value,
            "critic_provider_unavailable": unavailable.status.value,
            "critic_transient_recovered": transient.status.value,
            "critic_retry_exhausted": exhausted.status.value,
            "critic_malformed_output": malformed_status,
            "critic_fake_evidence": fake_evidence_status,
            "critic_source_spoof": source_spoof_status,
            "critic_approval_spoof": approval_spoof_status,
            "critic_owner_spoof_denied": owner_denied,
            "critic_tenant_spoof_denied": tenant_denied,
            "critic_route_spoof_denied": route_denied,
        },
        "failure_recovery": {
            "test_only_injector": "ScriptedFailureInjector",
            "failure_point": FailurePoint.PROVIDER_TRANSIENT_FAILURE.value,
            "completion_unknown": True,
            "attempt_count": transient_provider.calls,
            "failure_hit_count": transient_injector.hit_count(
                FailurePoint.PROVIDER_TRANSIENT_FAILURE
            ),
            "failure_emission_count": transient_injector.emitted_count(
                FailurePoint.PROVIDER_TRANSIENT_FAILURE
            ),
            "recovery_status": "RECOVERED_BY_RETRY",
            "step28_intake_disposition": transient_receipt.disposition.value,
            "step28_replay_disposition": (
                transient_replay_receipt.disposition.value
            ),
            "durable_candidate_count": len(transient_intake.by_candidate),
            "duplicate_semantic_side_effects": 0,
            "authority_violations": 0,
        },
        "authority": {
            "canonical_evidence_authority": False,
            "route_authority": False,
            "source_authority": False,
            "approval_authority": False,
            "commit_authority": False,
            "activation_authority": False,
            "reviewer_authority": False,
            "execution_authority": False,
            "external_action_authority": False,
            "authority_escalations": 0,
        },
        "privacy": {
            "raw_question_recorded": False,
            "raw_prompt_recorded": False,
            "raw_draft_recorded": False,
            "raw_evidence_recorded": False,
            "raw_correction_recorded": False,
            "raw_provider_response_recorded": False,
            "secret_leakage_count": 0,
        },
        "security": {
            "cross_user_denied": owner_denied,
            "cross_tenant_denied": tenant_denied,
            "secret_leakage_count": 0,
        },
        "cleanup": {
            "database_started": False,
            "network_opened": False,
            "temporary_files_created": 0,
            "production_resources_touched": 0,
        },
        "step40_started": False,
        "resource_optimization": 0,
    }
    report["validation_digest"] = canonical_sha256(report)
    assert_secret_free(
        report,
        surface="Step 39 controlled validation result",
        reject_machine_paths=True,
    )
    return report


def main() -> int:
    try:
        result = run_validation()
    except ValidationFailure as error:
        print(
            canonical_json(
                {
                    "schema_version": SCHEMA_VERSION,
                    "step": 39,
                    "status": "FAILED_PROVIDER_FREE_CONTROLLED",
                    "sanitized_code": error.code,
                    "closure_eligible": False,
                    "step40_started": False,
                }
            )
        )
        return 1
    except Exception:
        print(
            canonical_json(
                {
                    "schema_version": SCHEMA_VERSION,
                    "step": 39,
                    "status": "FAILED_PROVIDER_FREE_CONTROLLED",
                    "sanitized_code": "UNCLASSIFIED_CONTROLLED_VALIDATION_FAILURE",
                    "closure_eligible": False,
                    "step40_started": False,
                }
            )
        )
        return 1
    print(canonical_json(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
