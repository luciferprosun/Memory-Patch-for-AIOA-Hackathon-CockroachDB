"""Contract tests for the single-database Step 38 downstream adapter."""

from __future__ import annotations

import hashlib
import sys
import unittest
from dataclasses import replace
from pathlib import Path

from tests.test_step21_temporal_resolution import TemporalQueryMode, metadata, resolve
from tests.test_step38_german_law_e2e import NOW, bundle_outcome, primary_lineage


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import step38_real_retrieval as real_retrieval  # noqa: E402
import step38_coherent_runtime as coherent_runtime  # noqa: E402

from aioa_memory_kernel.contracts.exceptions import (
    ContractValidationError,
    IntegrityError,
)
from aioa_memory_kernel.contracts.serialization import canonical_sha256
from aioa_memory_kernel.german_law.e2e_runtime import (
    STEP38_ACTIVATION_RECOVERY_OBSERVATION_VERSION,
    STEP38_COHERENT_RUNTIME_PROOF_VERSION,
    STEP38_DATABASE_RETRIEVAL_PROOF_VERSION,
    STEP38_PERSONAL_MEMORY_SCENARIO_VERSION,
    STEP38_UPSTREAM_LINEAGE_VERSION,
    STEP38_UPSTREAM_RUNTIME_ATTESTATION_VERSION,
    Step38ActivationRecoveryObservation,
    Step38CoherentRuntimeProof,
    Step38DatabaseRetrievalProof,
    Step38PersonalMemoryScenario,
    Step38UpstreamRuntimeAttestation,
    Step38RealSecondModelInferenceStatus,
    Step38VerifiedUpstreamLineage,
)
from aioa_memory_kernel.reliability import (
    FailureDomain,
    FailurePoint,
    FailureRecoveryCaseResult,
    RecoveryStatus,
)
from aioa_memory_kernel.review_workspace import ReviewCaseType
from aioa_memory_kernel.security.credentials import CredentialPurpose


LATER_QUERY = (
    "Ab welchem Datum gilt die BMJErnAnO?"
)


def controlled_scenario() -> Step38PersonalMemoryScenario:
    value = primary_lineage()
    upstream = Step38VerifiedUpstreamLineage(
        lineage_version=STEP38_UPSTREAM_LINEAGE_VERSION,
        golden_case=value["case"],
        route=value["route"],
        post_retrieval_policy_receipt=value["post_retrieval_policy"],
        step20_outcome=value["outcome"],
        temporal_result=value["temporal"],
        draft_v1=value["draft"],
        packet_input_snapshot=value["snapshot"],
        correction_packet=value["packet"],
        step25_result=value["pipeline"],
        final_answer_request=value["final_request"],
        final_outcome=value["final"],
        evidence_context=value["context"],
        provider_input_receipt=value["evidence_provider"].input_receipts[0],
        before_after_trace=value["trace"],
    )
    digest = canonical_sha256("controlled-typed-fixture-runtime")
    retrieval_proof = Step38DatabaseRetrievalProof(
        proof_version=STEP38_DATABASE_RETRIEVAL_PROOF_VERSION,
        retrieval_input_hash=value["retrieval_input"].input_hash,
        primary_retrieval_input_hash=None,
        retrieval_input_kind="PRIMARY",
        route_hash=upstream.route.route_hash,
        step20_outcome_hash=upstream.step20_outcome.outcome_hash,
        evidence_bundle_hash=upstream.step20_outcome.bundle.bundle_hash,
        temporal_projection_receipt_hash=(
            value["temporal_projection"].receipt_hash
        ),
        real_retrieval_artifacts_hash=digest,
        real_retrieval_attestation_hash=digest,
        runtime_instance_digest=digest,
        database_instance_digest=canonical_sha256(
            "controlled-typed-fixture-database"
        ),
        data_plane_credential_purpose=CredentialPurpose.APPLICATION_DATABASE,
        data_plane_session_user="mp_s38_offline_contract",
        embedding_backend_identity_digest=digest,
        embedding_verified_files_digest=digest,
        approved_local_e5_backend=False,
        cross_tenant_rls_visible_count=0,
        owned_database=False,
        step18_exact_retrieval=False,
        step19_vector_retrieval=False,
        step20_hybrid_assembly=False,
    )
    attestation = Step38UpstreamRuntimeAttestation(
        attestation_version=STEP38_UPSTREAM_RUNTIME_ATTESTATION_VERSION,
        upstream_lineage_hash=upstream.lineage_hash,
        retrieval_proof=retrieval_proof,
    )
    related_input = real_retrieval.build_canonical_related_retrieval_input(
        value["retrieval_input"],
        question_text=LATER_QUERY,
        request_id="request-step38-later-related",
    )
    later_outcome = bundle_outcome(
        metadata(
            document=value["projection"].source_id,
            version=value["projection"].version_identity,
            provision="III.",
            official_identifier=value["projection"].official_identifier,
            effective_from=value["temporal_projection"].effective_from_date,
        ),
        contents=(value["projection"].exact_official_evidence[2],),
        source_ids=(value["projection"].source_id,),
        route_value=related_input.route,
        effective_scope=related_input.route.effective_scope,
    )
    later_temporal = resolve(
        later_outcome,
        route_value=related_input.route,
        mode=TemporalQueryMode.AS_OF,
        as_of=value["case"].knowledge_as_of,
        now=NOW,
    )
    later_proof = Step38DatabaseRetrievalProof(
        proof_version=STEP38_DATABASE_RETRIEVAL_PROOF_VERSION,
        retrieval_input_hash=related_input.input_hash,
        primary_retrieval_input_hash=value["retrieval_input"].input_hash,
        retrieval_input_kind="RELATED",
        route_hash=related_input.route.route_hash,
        step20_outcome_hash=later_outcome.outcome_hash,
        evidence_bundle_hash=later_outcome.bundle.bundle_hash,
        temporal_projection_receipt_hash=(
            value["temporal_projection"].receipt_hash
        ),
        real_retrieval_artifacts_hash=digest,
        real_retrieval_attestation_hash=digest,
        runtime_instance_digest=retrieval_proof.runtime_instance_digest,
        database_instance_digest=retrieval_proof.database_instance_digest,
        data_plane_credential_purpose=CredentialPurpose.APPLICATION_DATABASE,
        data_plane_session_user="mp_s38_offline_contract",
        embedding_backend_identity_digest=digest,
        embedding_verified_files_digest=digest,
        approved_local_e5_backend=False,
        cross_tenant_rls_visible_count=0,
        owned_database=False,
        step18_exact_retrieval=False,
        step19_vector_retrieval=False,
        step20_hybrid_assembly=False,
    )
    return Step38PersonalMemoryScenario(
        scenario_version=STEP38_PERSONAL_MEMORY_SCENARIO_VERSION,
        upstream=upstream,
        upstream_runtime_attestation=attestation,
        later_route=related_input.route,
        later_step20_outcome=later_outcome,
        later_temporal_result=later_temporal,
        later_retrieval_proof=later_proof,
        later_query_digest=hashlib.sha256(LATER_QUERY.encode("utf-8")).hexdigest(),
    )


def activation_recovery_observation(
    **changes,
) -> Step38ActivationRecoveryObservation:
    values = {
        "observation_version": STEP38_ACTIVATION_RECOVERY_OBSERVATION_VERSION,
        "activation_request_hash": canonical_sha256("activation-request"),
        "activation_receipt_hash": canonical_sha256(
            "step38-coherent-contract-placeholder"
        ),
        "activation_replay_identity_hash": canonical_sha256(
            "activation-replay-identity"
        ),
        "proposal_row_count": 1,
        "proposal_exact_match_count": 1,
        "approval_row_count": 1,
        "approval_exact_match_count": 1,
        "commit_row_count": 1,
        "commit_exact_match_count": 1,
        "memory_item_row_count": 1,
        "memory_item_exact_match_count": 1,
        "activation_transition_row_count": 1,
        "activation_transition_exact_match_count": 1,
        "activation_audit_event_row_count": 1,
        "activation_audit_event_exact_match_count": 1,
        "quota_memory_items_before": 1,
        "quota_memory_items_after": 1,
        "quota_active_patches_before": 0,
        "quota_active_patches_after": 1,
        "quota_stored_bytes_before": 256,
        "quota_stored_bytes_after": 256,
        "durable_authority_mismatch_count": 0,
        "rls_cross_scope_visible_count": 0,
        "control_plane_foreign_scope_count": 0,
        "cross_user_approval_denied": True,
        "replay_returned_existing_receipt": True,
    }
    values.update(changes)
    return Step38ActivationRecoveryObservation(**values)


def activation_recovery_result(
    observation: Step38ActivationRecoveryObservation,
) -> FailureRecoveryCaseResult:
    return FailureRecoveryCaseResult.build(
        case_id="personal-memory.step38-activation-ack-lost",
        failure_domain=FailureDomain.PERSONAL_MEMORY_ACTIVATION,
        failure_point=FailurePoint.PM_AFTER_ACTIVATION_ACK_LOST,
        subject_hash=observation.activation_receipt_hash,
        attempt_count=2,
        recovery_status=RecoveryStatus.RECOVERED_BY_IDEMPOTENT_REPLAY,
        final_semantic_state="ACTIVE_ONCE_DURABLE_STATE_RECONCILED",
        duplicate_side_effect_count=observation.duplicate_side_effect_count,
        authority_violation_count=observation.authority_violation_count,
        integrity_violation_count=observation.integrity_violation_count,
        reason_codes=(
            "ACTIVATION_ACK_LOST_DURABLE_OBSERVATION_RECONCILED",
        ),
    )


def coherent_proof(
    scenario: Step38PersonalMemoryScenario,
    *,
    primary_real: bool = False,
    later_real: bool = False,
    same_database: bool = False,
) -> Step38CoherentRuntimeProof:
    digest = canonical_sha256("step38-coherent-contract-placeholder")
    recovery_observation = activation_recovery_observation()
    recovery_result = activation_recovery_result(recovery_observation)
    return Step38CoherentRuntimeProof(
        proof_version=STEP38_COHERENT_RUNTIME_PROOF_VERSION,
        scenario_hash=scenario.scenario_hash,
        upstream_runtime_attestation_hash=(
            scenario.upstream_runtime_attestation.attestation_hash
        ),
        primary_retrieval_proof_hash=(
            scenario.upstream_runtime_attestation.retrieval_proof.proof_hash
        ),
        later_retrieval_proof_hash=scenario.later_retrieval_proof.proof_hash,
        runtime_instance_digest=digest,
        database_instance_digest=digest,
        tenant_id=scenario.upstream.route.tenant_id,
        owner_user_id=scenario.upstream.route.user_id,
        request_id=scenario.upstream.route.request_id,
        personal_memory_space_id="personal-slot-step38-contract",
        slot_hash=digest,
        candidate_hash=canonical_sha256("candidate"),
        candidate_envelope_hash=digest,
        proposal_hash=digest,
        validation_receipt_hash=digest,
        approval_receipt_hash=digest,
        commit_receipt_hash=digest,
        activation_receipt_hash=digest,
        active_patch_hash=digest,
        later_active_patch_retrieval_request_hash=canonical_sha256(
            "later-active-patch-request"
        ),
        disallowed_model_retrieval_hash=digest,
        cross_user_approval_denial_hash=digest,
        cross_user_export_denial_hash=digest,
        canonical_conflict_temporal_hash=digest,
        canonical_conflict_retrieval_hash=digest,
        activation_ack_lost_recovery_hash=recovery_result.result_hash,
        activation_ack_lost_recovery_status="RECOVERED_BY_IDEMPOTENT_REPLAY",
        activation_recovery_observation=recovery_observation,
        activation_recovery_case_result=recovery_result,
        activation_recovery_failure_point=(
            FailurePoint.PM_AFTER_ACTIVATION_ACK_LOST
        ),
        duplicate_semantic_side_effect_count=0,
        authority_violation_count=0,
        integrity_violation_count=0,
        model_a_identity_digest=canonical_sha256("model-a"),
        model_a_retrieval_hash=digest,
        model_a_context_hash=digest,
        model_b_identity_digest=canonical_sha256("model-b"),
        model_b_retrieval_hash=digest,
        model_b_context_hash=digest,
        audit_chain_id="audit-chain-step38-contract",
        audit_verification_hash=digest,
        audit_first_event_hash=canonical_sha256("first-audit-event"),
        audit_last_event_hash=digest,
        approval_audit_event_hash=canonical_sha256("approval-audit-event"),
        commit_audit_event_hash=canonical_sha256("commit-audit-event"),
        activation_audit_event_hash=canonical_sha256("activation-audit-event"),
        audit_export_bundle_hash=digest,
        shared_promotion_hash=digest,
        review_case_type=ReviewCaseType.SHARED_MEMORY_PROMOTION,
        review_case_hash=digest,
        review_detail_hash=digest,
        ui_awaiting_dashboard_hash=canonical_sha256("awaiting-dashboard"),
        ui_dashboard_hash=canonical_sha256("active-dashboard"),
        cross_model_same_patch=True,
        disallowed_model_access_denied=True,
        cross_user_access_denied=True,
        cross_tenant_access_denied=True,
        cross_user_approval_denied=True,
        cross_user_export_denied=True,
        canonical_evidence_conflict_suppressed=True,
        ordinary_user_review_access_denied=True,
        audit_chain_verified=True,
        lifecycle_audit_events_distinct=True,
        audit_export_hash_only=True,
        review_context_verified=True,
        ui_awaiting_state_verified=True,
        ui_active_state_verified=True,
        ui_same_active_patch=True,
        activation_ack_lost_recovered=True,
        real_second_model_inference_status=(
            Step38RealSecondModelInferenceStatus.NOT_REQUIRED_PROVIDER_NEUTRAL_RETRIEVAL_ONLY
        ),
        canonical_evidence_authority=False,
        source_publication_authority=False,
        external_execution_authority=False,
        real_retrieval_lineage=primary_real,
        later_real_retrieval_lineage=later_real,
        upstream_and_downstream_same_database=same_database,
        step39_started=False,
        closure_eligible=primary_real and later_real and same_database,
    )


class CoherentRuntimeContractTests(unittest.TestCase):
    def test_audit_request_lineage_does_not_forge_unpersisted_kernel_run(self):
        class CapturingAuditService:
            draft = None
            authenticated = None

            def append_event(self, draft, **authenticated):
                self.draft = draft
                self.authenticated = authenticated
                return object(), False

        service = CapturingAuditService()
        request_id = "request-step38-audit-lineage"
        route_hash = canonical_sha256("route-step38-audit-lineage")
        lineage_hash = canonical_sha256("upstream-step38-audit-lineage")
        coherent_runtime._append_audit(
            service,
            event_type=coherent_runtime.AuditEventType.VERIFIED_ANSWER_ASSEMBLED,
            subject_type=coherent_runtime.AuditSubjectType.VERIFIED_ANSWER,
            subject_id="answer-step38-audit-lineage",
            subject_hash=canonical_sha256("answer-step38-audit-lineage"),
            tenant_id="tenant-step38-audit-lineage",
            owner_user_id="user-step38-audit-lineage",
            space_id=None,
            actor_type=coherent_runtime.AuditActorType.KERNEL,
            actor_id="step38-coherent-kernel",
            idempotency_key="step38-audit-lineage",
            occurred_at=NOW,
            request_id=request_id,
            route_hash=route_hash,
            lineage_hashes={"upstream_lineage_hash": lineage_hash},
        )

        self.assertIsNotNone(service.draft)
        self.assertIsNone(service.draft.kernel_run_id)
        self.assertEqual(service.draft.request_id, request_id)
        self.assertEqual(service.draft.route_hash, route_hash)
        self.assertEqual(
            service.draft.lineage_hashes,
            {"upstream_lineage_hash": lineage_hash},
        )
        self.assertEqual(
            service.authenticated,
            {
                "authenticated_tenant_id": "tenant-step38-audit-lineage",
                "authenticated_actor_type": coherent_runtime.AuditActorType.KERNEL,
                "authenticated_actor_id": "step38-coherent-kernel",
            },
        )

    def test_scenario_binds_one_owner_scope_and_marks_fixture_upstream_non_real(self):
        scenario = controlled_scenario()
        self.assertEqual(
            scenario.upstream.route.tenant_id,
            scenario.later_route.tenant_id,
        )
        self.assertEqual(
            scenario.upstream.route.user_id,
            scenario.later_route.user_id,
        )
        self.assertFalse(
            scenario.upstream_runtime_attestation.real_retrieval_lineage
        )

    def test_later_owner_or_attestation_detachment_fails_closed(self):
        scenario = controlled_scenario()
        with self.assertRaises(IntegrityError):
            replace(
                scenario,
                later_route=replace(
                    scenario.later_route,
                    user_id="different-owner",
                ),
            )
        with self.assertRaises(IntegrityError):
            replace(
                scenario,
                upstream_runtime_attestation=replace(
                    scenario.upstream_runtime_attestation,
                    upstream_lineage_hash="f" * 64,
                ),
            )
        with self.assertRaises(IntegrityError):
            replace(
                scenario,
                later_retrieval_proof=replace(
                    scenario.later_retrieval_proof,
                    runtime_instance_digest=canonical_sha256("another-runtime"),
                ),
            )

    def test_hash_only_proof_cannot_overstate_closure_eligibility(self):
        scenario = controlled_scenario()
        proof = coherent_proof(scenario)
        self.assertFalse(proof.closure_eligible)
        with self.assertRaises(ContractValidationError):
            replace(proof, closure_eligible=True)

    def test_later_real_retrieval_is_an_independent_closure_gate(self):
        scenario = controlled_scenario()
        proof = coherent_proof(
            scenario,
            primary_real=True,
            later_real=False,
            same_database=True,
        )
        with self.assertRaises(ContractValidationError):
            replace(proof, closure_eligible=True)
        complete = replace(
            proof,
            later_real_retrieval_lineage=True,
            closure_eligible=True,
        )
        self.assertTrue(complete.closure_eligible)

    def test_each_bounded_downstream_proof_is_required(self):
        proof = coherent_proof(controlled_scenario())
        required_flags = (
            "cross_model_same_patch",
            "disallowed_model_access_denied",
            "cross_user_access_denied",
            "cross_tenant_access_denied",
            "cross_user_approval_denied",
            "cross_user_export_denied",
            "canonical_evidence_conflict_suppressed",
            "ordinary_user_review_access_denied",
            "audit_chain_verified",
            "lifecycle_audit_events_distinct",
            "audit_export_hash_only",
            "review_context_verified",
            "ui_awaiting_state_verified",
            "ui_active_state_verified",
            "ui_same_active_patch",
            "activation_ack_lost_recovered",
        )
        for field_name in required_flags:
            with self.subTest(field_name=field_name):
                with self.assertRaises(ContractValidationError):
                    replace(proof, **{field_name: False})

    def test_recovery_status_hash_and_step39_boundary_fail_closed(self):
        proof = coherent_proof(controlled_scenario())
        with self.assertRaises(ContractValidationError):
            replace(proof, activation_ack_lost_recovery_status="FAILED_CLOSED")
        with self.assertRaises(ContractValidationError):
            replace(proof, audit_export_bundle_hash="not-a-digest")
        with self.assertRaises(ContractValidationError):
            replace(proof, step39_started=True)

    def test_durable_recovery_counts_reject_duplicate_authority_and_integrity(self):
        proof = coherent_proof(controlled_scenario())
        observations = (
            activation_recovery_observation(
                memory_item_row_count=2,
                memory_item_exact_match_count=2,
            ),
            activation_recovery_observation(
                rls_cross_scope_visible_count=1,
            ),
            activation_recovery_observation(
                control_plane_foreign_scope_count=1,
            ),
            activation_recovery_observation(
                activation_transition_exact_match_count=0,
            ),
        )
        expected_counts = (
            (1, 0, 0),
            (0, 1, 0),
            (0, 1, 0),
            (0, 0, 1),
        )
        for observation, counts in zip(
            observations,
            expected_counts,
            strict=True,
        ):
            with self.subTest(counts=counts):
                self.assertEqual(
                    (
                        observation.duplicate_side_effect_count,
                        observation.authority_violation_count,
                        observation.integrity_violation_count,
                    ),
                    counts,
                )
                result = activation_recovery_result(observation)
                with self.assertRaises(ContractValidationError):
                    replace(
                        proof,
                        activation_recovery_observation=observation,
                        activation_recovery_case_result=result,
                        activation_ack_lost_recovery_hash=result.result_hash,
                        duplicate_semantic_side_effect_count=counts[0],
                        authority_violation_count=counts[1],
                        integrity_violation_count=counts[2],
                    )

    def test_recovery_observation_cannot_be_detached_from_rehashed_result(self):
        proof = coherent_proof(controlled_scenario())
        duplicate = activation_recovery_observation(
            activation_audit_event_row_count=2,
            activation_audit_event_exact_match_count=2,
        )
        clean_result = activation_recovery_result(
            activation_recovery_observation()
        )
        with self.assertRaises(ContractValidationError):
            replace(
                proof,
                activation_recovery_observation=duplicate,
                activation_recovery_case_result=clean_result,
            )

    def test_nested_recovery_observation_integrity_is_recomputed(self):
        proof = coherent_proof(controlled_scenario())
        observation = activation_recovery_observation()
        object.__setattr__(
            observation,
            "observation_hash",
            canonical_sha256("forged-observation-hash"),
        )
        with self.assertRaises(IntegrityError):
            replace(
                proof,
                activation_recovery_observation=observation,
            )

    def test_public_closure_fields_are_typed_and_hash_bound(self):
        proof = coherent_proof(controlled_scenario())
        with self.assertRaises(ContractValidationError):
            replace(proof, candidate_hash="not-a-digest")
        with self.assertRaises(ContractValidationError):
            replace(
                proof,
                later_active_patch_retrieval_request_hash="not-a-digest",
            )
        with self.assertRaises(ContractValidationError):
            replace(proof, audit_first_event_hash="not-a-digest")
        with self.assertRaises(ContractValidationError):
            replace(proof, review_case_type="SHARED_MEMORY_PROMOTION")
        with self.assertRaises(ContractValidationError):
            replace(
                proof,
                real_second_model_inference_status="REAL_SECOND_MODEL_INFERENCE_COMPLETED",
            )


if __name__ == "__main__":
    unittest.main()
