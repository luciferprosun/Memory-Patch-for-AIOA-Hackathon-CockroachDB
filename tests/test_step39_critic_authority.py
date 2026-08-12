"""Step 39 Critic audit and no-authority boundary tests."""

from __future__ import annotations

import ast
import hashlib
import json
import unittest
from dataclasses import replace
from pathlib import Path

from aioa_memory_kernel.audit_ledger import (
    AuditActorType,
    AuditEventType,
    AuditSubjectType,
    verify_audit_event_draft,
)
from aioa_memory_kernel.claims import ClaimEvidenceRelation
from aioa_memory_kernel.contracts.enums import ActorType, EvidenceStatus
from aioa_memory_kernel.contracts.exceptions import ContractValidationError, IntegrityError
from aioa_memory_kernel.contracts.serialization import canonical_sha256, to_canonical_data
from aioa_memory_kernel.critic.audit import (
    CRITIC_AUDIT_ACTOR_ID,
    critic_candidate_detected_event,
)
from aioa_memory_kernel.critic.bridge import (
    CriticTrustedCandidateContext,
    map_critic_assessment_to_step28,
)
from aioa_memory_kernel.critic.models import (
    CRITIC_REVIEW_OBJECTIVE,
    STEP39_SCHEMA_VERSION,
    CriticArtifactKind,
    CriticAssessment,
    CriticBridgeResult,
    CriticBridgeStatus,
    CriticCandidateMappingResult,
    CriticCandidateMappingStatus,
    CriticCandidateScope,
    CriticClaimReference,
    CriticClaimStatus,
    CriticEvidenceReference,
    CriticIssueType,
    CriticLimitationCode,
    CriticReasonCode,
    CriticReviewRequest,
    CriticTextArtifact,
    CriticProviderCallReceipt,
    CriticProviderCallStatus,
    load_critic_policy,
    load_critic_prompt_template,
)
from aioa_memory_kernel.critic.parser import (
    build_critic_prompt_payload,
    build_critic_provider_request,
    parse_critic_assessment,
)
from aioa_memory_kernel.modeling import load_approved_provider_spec
from aioa_memory_kernel.personal_memory.candidates import (
    CorrectionCandidateIntakeDisposition,
    build_correction_candidate_intake_receipt,
    build_correction_candidate_envelope,
)
from aioa_memory_kernel.sources import SourceAuthorityLevel, SourcePublicationState
from aioa_memory_kernel.temporal import FreshnessStatus, TemporalApplicability
from tests._support import REPOSITORY_ROOT
from tests.test_step28_correction_candidate_bridge import (
    NOW,
    envelope as step28_envelope,
    personal_slot,
)


CORRECTION_SENTINEL = "Use only the verified effective date; never approve this text."
QUESTION_SENTINEL = "Question text must never enter the audit event."
ARTIFACT_SENTINEL = "Provider output must never enter the audit event."
EVIDENCE_SENTINEL = "Private evidence snippet must never enter the audit event."


def _hash(label: str) -> str:
    return canonical_sha256({"step39-authority-test": label})


def fixture():
    initial = step28_envelope(
        source=ActorType.CRITIC_PROMPT_LOOP,
        proposed_correction=CORRECTION_SENTINEL,
        idempotency_key="step39-critic-candidate",
        event_id="step39-critic-event",
    )
    query_digest = hashlib.sha256(QUESTION_SENTINEL.encode("utf-8")).hexdigest()
    lineage = replace(
        initial.submission.lineage,
        original_query_digest=query_digest,
    )
    candidate_envelope = build_correction_candidate_envelope(
        candidate=initial.submission.candidate,
        kernel_run=initial.submission.run_identity,
        slot=personal_slot(),
        route_result_lineage=lineage,
        metadata=initial.submission.metadata,
        idempotency_key="step39-critic-candidate",
        submitted_at=initial.submission.submitted_at,
    )
    candidate = candidate_envelope.submission.candidate
    evidence_reference = candidate.available_evidence_references[0]
    policy = load_critic_policy()
    prompt = load_critic_prompt_template()
    request = CriticReviewRequest(
        schema_version=STEP39_SCHEMA_VERSION,
        critic_request_id="step39-critic-request",
        tenant_id=candidate.tenant_id,
        owner_user_id=candidate.user_id,
        request_id=candidate.run_id,
        kernel_run_id=candidate.run_id,
        route_hash=lineage.route_hash,
        selected_hat_id=lineage.selected_hat_id,
        selected_hat_version=lineage.selected_hat_version,
        selected_manifest_digest=lineage.selected_manifest_digest,
        original_query=QUESTION_SENTINEL,
        original_query_digest=query_digest,
        artifacts=(
            CriticTextArtifact(
                artifact_kind=CriticArtifactKind.DRAFT_V1,
                artifact_id="draft-v1-a",
                artifact_hash=candidate.draft_v1_reference,
                text=ARTIFACT_SENTINEL,
            ),
        ),
        claim_references=(
            CriticClaimReference(
                claim_id=candidate.detected_claims[0].claim_id,
                draft_id=candidate.detected_claims[0].draft_id,
                statement=candidate.detected_claims[0].statement,
                claim_category=candidate.detected_claims[0].claim_category,
                verification_status=CriticClaimStatus.REFUTED,
                evidence_reference_ids=(evidence_reference,),
            ),
        ),
        evidence_references=(
            CriticEvidenceReference(
                reference_id=evidence_reference,
                evidence_id="evidence-a",
                source_id="source-a",
                source_version_id="source-version-a",
                chunk_id="chunk-a",
                relation=ClaimEvidenceRelation.REFUTES,
                authority_level=SourceAuthorityLevel.OFFICIAL_PRIMARY,
                publication_state=SourcePublicationState.PUBLISHED,
                temporal_applicability=TemporalApplicability.APPLICABLE,
                freshness_status=FreshnessStatus.FRESH,
                snippet=EVIDENCE_SENTINEL,
            ),
        ),
        effective_scope=lineage.effective_scope,
        correction_packet_hash=lineage.correction_packet_hash,
        evidence_status=EvidenceStatus.SUFFICIENT,
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
    assessment = CriticAssessment(
        schema_version=STEP39_SCHEMA_VERSION,
        critic_request_hash=request.request_hash,
        issue_detected=True,
        issue_type=CriticIssueType.TEMPORAL_MISMATCH,
        affected_claim_ids=(candidate.detected_claims[0].claim_id,),
        candidate_correction_text=CORRECTION_SENTINEL,
        candidate_scope=CriticCandidateScope(
            scope_digest=request.scope_digest,
            dimension_names=tuple(item.name for item in request.effective_scope),
        ),
        evidence_reference_ids=(evidence_reference,),
        reason_codes=tuple(
            sorted(
                (
                    CriticReasonCode.CANDIDATE_NON_AUTHORITATIVE,
                    CriticReasonCode.CLAIM_REFERENCE_MATCHED,
                    CriticReasonCode.EVIDENCE_REFERENCE_MATCHED,
                    CriticReasonCode.ISSUE_DETECTED,
                ),
                key=lambda item: item.value,
            )
        ),
        diagnostic_confidence_basis_points=9500,
        limitations=tuple(
            sorted(
                (
                    CriticLimitationCode.BOUNDED_CONTEXT_ONLY,
                    CriticLimitationCode.HUMAN_VALIDATION_REQUIRED,
                    CriticLimitationCode.NOT_CANONICAL_EVIDENCE,
                    CriticLimitationCode.NO_APPROVAL_AUTHORITY,
                    CriticLimitationCode.NO_EXECUTION_AUTHORITY,
                ),
                key=lambda item: item.value,
            )
        ),
        provider_identity_digest=request.provider_identity.identity_digest,
        provider_response_hash=_hash("provider-response"),
        raw_response_digest=_hash("raw-response"),
    )
    bridge_result = accepted_bridge_result(request, assessment)
    trusted_context = CriticTrustedCandidateContext(
        kernel_run=candidate_envelope.submission.run_identity,
        target_slot=personal_slot(),
        route_result_lineage=lineage,
        detected_at=candidate_envelope.submission.submitted_at,
    )
    mapping, candidate_envelope = map_critic_assessment_to_step28(
        request,
        bridge_result,
        trusted_context=trusted_context,
    )
    assert candidate_envelope is not None
    intake_receipt = build_correction_candidate_intake_receipt(
        candidate_envelope,
        accepted_at=candidate_envelope.submission.submitted_at,
        disposition=CorrectionCandidateIntakeDisposition.ACCEPTED,
    )
    return (
        request,
        assessment,
        bridge_result,
        trusted_context,
        candidate_envelope,
        mapping,
        intake_receipt,
    )


def accepted_bridge_result(
    request: CriticReviewRequest,
    assessment: CriticAssessment,
) -> CriticBridgeResult:
    provider_request = build_critic_provider_request(request)
    return CriticBridgeResult(
        critic_request_hash=request.request_hash,
        status=CriticBridgeStatus.ASSESSMENT_ACCEPTED,
        provider_call_receipt=CriticProviderCallReceipt(
            critic_request_hash=request.request_hash,
            status=CriticProviderCallStatus.RESPONSE_ACCEPTED,
            provider_request_hash=provider_request.request_hash,
            provider_response_hash=assessment.provider_response_hash,
            attempt_count=1,
            failed_reason_codes=(),
        ),
        assessment=assessment,
        audit_draft_hashes=(),
    )


def _valid_provider_document(request: CriticReviewRequest) -> dict[str, object]:
    prompt_projection = json.loads(build_critic_prompt_payload(request))
    bindings = prompt_projection["bindings_to_echo_exactly"]
    return {
        "affected_claim_ids": [request.claim_references[0].claim_id],
        "artifact_references_digest": bindings["artifact_references_digest"],
        "candidate_correction_text": CORRECTION_SENTINEL,
        "claim_references_digest": bindings["claim_references_digest"],
        "critic_request_hash": request.request_hash,
        "diagnostic_confidence_basis_points": 9500,
        "evidence_reference_ids": [request.evidence_references[0].reference_id],
        "evidence_references_digest": bindings["evidence_references_digest"],
        "issue_detected": True,
        "issue_type": CriticIssueType.TEMPORAL_MISMATCH.value,
        "limitations": sorted(
            item.value
            for item in (
                CriticLimitationCode.BOUNDED_CONTEXT_ONLY,
                CriticLimitationCode.HUMAN_VALIDATION_REQUIRED,
                CriticLimitationCode.NOT_CANONICAL_EVIDENCE,
                CriticLimitationCode.NO_APPROVAL_AUTHORITY,
                CriticLimitationCode.NO_EXECUTION_AUTHORITY,
            )
        ),
        "provider_identity_digest": request.provider_identity.identity_digest,
        "reason_codes": sorted(
            item.value
            for item in (
                CriticReasonCode.CANDIDATE_NON_AUTHORITATIVE,
                CriticReasonCode.CLAIM_REFERENCE_MATCHED,
                CriticReasonCode.EVIDENCE_REFERENCE_MATCHED,
                CriticReasonCode.ISSUE_DETECTED,
            )
        ),
        "route_hash": request.route_hash,
        "schema_version": STEP39_SCHEMA_VERSION,
        "scope_digest": request.scope_digest,
    }


class CriticCandidateAuditTests(unittest.TestCase):
    def test_accepted_candidate_event_is_exact_hash_only_step33_fact(self) -> None:
        (
            request, assessment, bridge_result, trusted_context,
            candidate, mapping, intake_receipt,
        ) = fixture()
        draft = critic_candidate_detected_event(
            request,
            bridge_result,
            trusted_context,
            candidate,
            mapping,
            intake_receipt,
            occurred_at=intake_receipt.accepted_at,
        )
        verify_audit_event_draft(draft)
        self.assertIs(draft.event_type, AuditEventType.CORRECTION_CANDIDATE_DETECTED)
        self.assertIs(draft.actor_type, AuditActorType.CRITIC_LOOP)
        self.assertEqual(draft.actor_id, CRITIC_AUDIT_ACTOR_ID)
        self.assertIs(draft.subject_type, AuditSubjectType.CORRECTION_CANDIDATE)
        self.assertEqual(draft.subject_id, candidate.candidate_id)
        self.assertEqual(draft.subject_hash, candidate.envelope_hash)
        self.assertIsNone(draft.kernel_run_id)
        self.assertEqual(draft.request_id, request.request_id)
        self.assertEqual(draft.route_hash, request.route_hash)
        self.assertEqual(
            draft.lineage_hashes["kernel_run_identity_hash"],
            candidate.submission.run_identity_digest,
        )
        self.assertEqual(
            draft.lineage_hashes["step28_intake_receipt_hash"],
            intake_receipt.receipt_hash,
        )
        self.assertEqual(
            draft.event_payload["intake_disposition"],
            CorrectionCandidateIntakeDisposition.ACCEPTED.value,
        )
        self.assertEqual(
            draft.idempotency_key,
            "audit-critic-candidate-" + candidate.candidate_id,
        )

        rendered = json.dumps(to_canonical_data(draft), sort_keys=True)
        for private_text in (
            QUESTION_SENTINEL,
            ARTIFACT_SENTINEL,
            EVIDENCE_SENTINEL,
            CORRECTION_SENTINEL,
            candidate.submission.candidate.detected_claims[0].statement,
        ):
            self.assertNotIn(private_text, rendered)
        for key, value in draft.event_payload.items():
            if key.endswith("_authority"):
                self.assertIs(value, False)

    def test_same_immutable_candidate_builds_same_draft_and_idempotency(self) -> None:
        (
            request, _assessment, bridge_result, trusted_context,
            candidate, mapping, intake_receipt,
        ) = fixture()
        first = critic_candidate_detected_event(
            request, bridge_result, trusted_context, candidate, mapping, intake_receipt,
            occurred_at=intake_receipt.accepted_at,
        )
        second = critic_candidate_detected_event(
            request, bridge_result, trusted_context, candidate, mapping, intake_receipt,
            occurred_at=intake_receipt.accepted_at,
        )
        self.assertEqual(first, second)
        self.assertEqual(first.draft_hash, second.draft_hash)
        self.assertEqual(first.idempotency_key, second.idempotency_key)

    def test_detached_mapping_owner_run_route_text_and_timestamp_fail_closed(self) -> None:
        (
            request, assessment, bridge_result, trusted_context,
            candidate, mapping, intake_receipt,
        ) = fixture()
        with self.assertRaises(IntegrityError):
            critic_candidate_detected_event(
                request,
                bridge_result,
                trusted_context,
                candidate,
                replace(mapping, candidate_envelope_hash=_hash("foreign")),
                intake_receipt,
                occurred_at=intake_receipt.accepted_at,
            )

    def test_replay_and_rehashed_detached_lineage_fail_closed(self) -> None:
        (
            request, assessment, bridge_result, trusted_context,
            candidate, mapping, intake_receipt,
        ) = fixture()
        submission = candidate.submission
        forged_claim = replace(
            submission.candidate.detected_claims[0],
            statement="A different statement that the Critic never reviewed.",
        )
        forged_candidate = replace(
            submission.candidate,
            detected_claims=(forged_claim,),
        )
        forged_envelope = build_correction_candidate_envelope(
            candidate=forged_candidate,
            kernel_run=submission.run_identity,
            slot=trusted_context.target_slot,
            route_result_lineage=submission.lineage,
            metadata=submission.metadata,
            idempotency_key=submission.idempotency_key,
            submitted_at=submission.submitted_at,
        )
        forged_mapping = replace(
            mapping,
            candidate_content_hash=forged_candidate.content_hash,
            candidate_envelope_hash=forged_envelope.envelope_hash,
        )
        forged_receipt = build_correction_candidate_intake_receipt(
            forged_envelope,
            accepted_at=forged_envelope.submission.submitted_at,
            disposition=CorrectionCandidateIntakeDisposition.ACCEPTED,
        )
        with self.assertRaises(IntegrityError):
            critic_candidate_detected_event(
                request,
                bridge_result,
                trusted_context,
                forged_envelope,
                forged_mapping,
                forged_receipt,
                occurred_at=forged_receipt.accepted_at,
            )
        replay_receipt = build_correction_candidate_intake_receipt(
            candidate,
            accepted_at=intake_receipt.accepted_at,
            disposition=CorrectionCandidateIntakeDisposition.EXACT_REPLAY,
        )
        first_draft = critic_candidate_detected_event(
            request,
            bridge_result,
            trusted_context,
            candidate,
            mapping,
            intake_receipt,
            occurred_at=intake_receipt.accepted_at,
        )
        replay_draft = critic_candidate_detected_event(
            request,
            bridge_result,
            trusted_context,
            candidate,
            mapping,
            replay_receipt,
            occurred_at=replay_receipt.accepted_at,
        )
        self.assertEqual(replay_draft, first_draft)
        with self.assertRaises(IntegrityError):
            critic_candidate_detected_event(
                request,
                bridge_result,
                trusted_context,
                candidate,
                mapping,
                replace(intake_receipt, envelope_hash=_hash("foreign-receipt")),
                occurred_at=intake_receipt.accepted_at,
            )
        for detached_request in (
            replace(request, owner_user_id="other-user"),
            replace(request, request_id="other-run", kernel_run_id="other-run"),
            replace(request, route_hash=_hash("other-route")),
        ):
            with self.subTest(request_hash=detached_request.request_hash):
                with self.assertRaises(IntegrityError):
                    critic_candidate_detected_event(
                        detached_request,
                        bridge_result,
                        trusted_context,
                        candidate,
                        mapping,
                        intake_receipt,
                        occurred_at=intake_receipt.accepted_at,
                    )
        with self.assertRaises(IntegrityError):
            critic_candidate_detected_event(
                request,
                bridge_result,
                trusted_context,
                candidate,
                mapping,
                intake_receipt,
                occurred_at=NOW,
            )
        with self.assertRaises(IntegrityError):
            critic_candidate_detected_event(
                request,
                replace(
                    bridge_result,
                    assessment=replace(
                        assessment,
                        candidate_correction_text="Different correction.",
                    ),
                ),
                trusted_context,
                candidate,
                mapping,
                intake_receipt,
                occurred_at=intake_receipt.accepted_at,
            )


class CriticAuthorityCeilingTests(unittest.TestCase):
    def test_parser_rejects_all_model_supplied_authority_and_identity_fields(self) -> None:
        request, _, _, _, _, _, _ = fixture()
        document = _valid_provider_document(request)
        raw = json.dumps(document, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        parsed = parse_critic_assessment(
            raw,
            request=request,
            provider_response_hash=_hash("valid-parser-response"),
        )
        self.assertTrue(parsed.issue_detected)

        for field_name, spoofed_value in (
            ("approval_authority", True),
            ("commit_authority", True),
            ("activation_authority", True),
            ("reviewer_authority", True),
            ("execution_authority", True),
            ("external_action_authority", True),
            ("owner_user_id", request.owner_user_id),
            ("tenant_id", request.tenant_id),
            ("personal_memory_space_id", "spoofed-space"),
        ):
            spoofed = {**document, field_name: spoofed_value}
            with self.subTest(field_name=field_name):
                with self.assertRaises(ContractValidationError):
                    parse_critic_assessment(
                        json.dumps(
                            spoofed,
                            ensure_ascii=False,
                            separators=(",", ":"),
                            sort_keys=True,
                        ),
                        request=request,
                        provider_response_hash=_hash("spoofed-parser-response"),
                    )

    def test_all_critic_and_mapping_authority_flags_are_false(self) -> None:
        request, assessment, _, _, _, mapping, _ = fixture()
        policy = load_critic_policy()
        for name in (
            "canonical_evidence_authority",
            "route_authority",
            "source_authority",
            "approval_authority",
            "commit_authority",
            "activation_authority",
            "reviewer_authority",
            "execution_authority",
            "external_action_authority",
        ):
            self.assertIs(getattr(policy, name), False)
        for name in (
            "canonical_evidence_authority",
            "route_authority",
            "source_authority",
            "approval_authority",
            "commit_authority",
            "activation_authority",
            "reviewer_authority",
            "execution_authority",
            "external_action_authority",
        ):
            self.assertIs(getattr(assessment, name), False)
            with self.subTest(assessment_flag=name):
                with self.assertRaises(ContractValidationError):
                    replace(assessment, **{name: True})
        self.assertTrue(mapping.step29_required)
        self.assertTrue(mapping.step30_human_approval_required)
        for name in (
            "direct_proposal",
            "direct_validation",
            "canonical_evidence_authority",
            "route_authority",
            "source_authority",
            "approval_authority",
            "commit_authority",
            "activation_authority",
            "reviewer_authority",
            "execution_authority",
            "external_action_authority",
        ):
            self.assertIs(getattr(mapping, name), False)
            with self.subTest(mapping_flag=name):
                with self.assertRaises(ContractValidationError):
                    replace(mapping, **{name: True})
        self.assertEqual(request.provider_identity, load_approved_provider_spec().provider_identity())

    def test_critic_package_imports_and_calls_no_later_authority(self) -> None:
        critic_root = REPOSITORY_ROOT / "src/aioa_memory_kernel/critic"
        forbidden_modules = (
            "aioa_memory_kernel.personal_memory.proposal_repository",
            "aioa_memory_kernel.personal_memory.proposal_service",
            "aioa_memory_kernel.personal_memory.proposals",
            "aioa_memory_kernel.personal_memory.lifecycle",
            "aioa_memory_kernel.personal_memory.lifecycle_repository",
            "aioa_memory_kernel.personal_memory.lifecycle_service",
            "aioa_memory_kernel.review_workspace",
            "subprocess",
        )
        forbidden_calls = {
            "activate",
            "activate_patch",
            "advance_to_awaiting_approval",
            "approve",
            "approve_personal_memory_patch",
            "claim_case",
            "commit",
            "commit_personal_memory_patch",
            "execute",
            "execute_action",
            "execute_sql",
            "handoff",
            "publish_source",
            "record_decision",
            "submit_review_decision",
            "validate_personal_memory_patch",
            "validate_proposal",
        }
        forbidden_imported_symbols = {
            "PersonalMemoryActivationReceipt",
            "PersonalMemoryApprovalReceipt",
            "PersonalMemoryCommitReceipt",
            "PersonalMemoryLifecycleService",
            "PersonalMemoryProposalService",
            "ReviewWorkspaceService",
            "activate_patch",
            "approve_personal_memory_patch",
            "commit_personal_memory_patch",
            "submit_review_decision",
            "validate_personal_memory_patch",
        }
        files = sorted(critic_root.glob("*.py"))
        self.assertTrue(files)
        for path in files:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    modules = tuple(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    modules = (node.module or "",)
                    imported_symbols = tuple(alias.name for alias in node.names)
                else:
                    modules = ()
                    imported_symbols = ()
                for module in modules:
                    self.assertFalse(
                        any(
                            module == forbidden or module.startswith(forbidden + ".")
                            for forbidden in forbidden_modules
                        ),
                        f"{path.name} imports later authority {module}",
                    )
                for imported_symbol in imported_symbols:
                    self.assertNotIn(
                        imported_symbol,
                        forbidden_imported_symbols,
                        f"{path.name} imports later authority {imported_symbol}",
                    )
                if isinstance(node, ast.Call):
                    if isinstance(node.func, ast.Name):
                        called = node.func.id
                    elif isinstance(node.func, ast.Attribute):
                        called = node.func.attr
                    else:
                        called = ""
                    self.assertNotIn(
                        called,
                        forbidden_calls,
                        f"{path.name} calls later authority {called}",
                    )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
