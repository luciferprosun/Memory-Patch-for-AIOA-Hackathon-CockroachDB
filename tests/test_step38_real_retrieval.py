"""Pure contract tests for the caller-owned Step 38 retrieval adapter."""

from __future__ import annotations

import inspect
import json
import os
import sys
import tempfile
import unittest
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tests._support import REPOSITORY_ROOT
from tests.test_source_registry import make_record


SCRIPTS = REPOSITORY_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import step38_real_retrieval as adapter  # noqa: E402
import run_step38_german_law_e2e_validation as controlled  # noqa: E402

from aioa_memory_kernel.contracts.enums import KnowledgeRoute  # noqa: E402
from aioa_memory_kernel.contracts.exceptions import (  # noqa: E402
    ContractValidationError,
    IntegrityError,
)
from aioa_memory_kernel.contracts.serialization import canonical_sha256  # noqa: E402
from aioa_memory_kernel.german_law.e2e import (  # noqa: E402
    REAL_OFFICIAL_IDENTIFIER,
    REAL_PROVISION_HASHES,
    REAL_SOURCE_ID,
    REAL_VERSION_IDENTITY,
    load_german_law_golden_cases,
    project_bmjernano_temporal_facts,
)
from aioa_memory_kernel.hats import decode_manifest  # noqa: E402
from aioa_memory_kernel.retrieval import (  # noqa: E402
    CockroachRetrievalRepository,
    ExactIdentifierField,
    ExactIdentifierSelector,
    RetrievalMode,
    StatuteSectionSelector,
)
from aioa_memory_kernel.sources import (  # noqa: E402
    SourceAuthorityLevel,
    SourcePublicationState,
)
from aioa_memory_kernel.routing import route_knowledge_request  # noqa: E402
from aioa_memory_kernel.security.credentials import (  # noqa: E402
    CredentialPurpose,
    SecretValue,
)
from aioa_memory_kernel.security import assert_secret_free  # noqa: E402
from tests.test_step21_temporal_resolution import (  # noqa: E402
    bundle_outcome,
    metadata,
    resolve,
)
from tests.test_step20_hybrid_evidence_bundle import route as fixture_route  # noqa: E402


FIXTURE = REPOSITORY_ROOT / "tests/fixtures/step38_german_law_cases.json"
SUITE = load_german_law_golden_cases(FIXTURE)
PRIMARY = SUITE.case("primary-entry-into-force")
BACKUP = SUITE.case("backup-special-case-reservation")
PROVISION_III = (
    "Diese Anordnung tritt am 1. Januar 2024 in Kraft. Frühere Anordnungen "
    "zum selben Gegenstand sind nicht mehr anzuwenden."
)


def retrieval_input():
    return adapter.build_canonical_primary_retrieval_input(
        SUITE,
        tenant_id="tenant-step38-real-retrieval",
        user_id="user-step38-real-retrieval",
        request_id="request-step38-real-retrieval",
    )


class _CaptureTransaction:
    def __init__(self) -> None:
        self.sql = ""
        self.parameters = ()

    def fetch_all(self, sql, parameters=None):
        self.sql = sql
        self.parameters = tuple(parameters or ())
        return ()


class InputBoundaryTests(unittest.TestCase):
    def test_real_draft_v1_prompts_are_case_bound_and_evidence_blind(self) -> None:
        from tests.test_step38_german_law_e2e import primary_lineage

        temporal = primary_lineage()["temporal"]
        primary_request = controlled._prepare_real_draft_v1_request(
            temporal,
            PRIMARY,
        )
        backup_request = controlled._prepare_real_draft_v1_request(
            temporal,
            BACKUP,
        )
        self.assertEqual(primary_request.original_query, PRIMARY.question)
        self.assertEqual(backup_request.original_query, BACKUP.question)
        self.assertNotEqual(
            primary_request.prompt_template.template_digest,
            backup_request.prompt_template.template_digest,
        )
        for request in (primary_request, backup_request):
            self.assertNotIn(REAL_SOURCE_ID, request.prompt_template.system_instruction)
            self.assertNotIn(
                REAL_OFFICIAL_IDENTIFIER,
                request.prompt_template.system_instruction,
            )
            self.assertNotIn("1. Januar 2024", request.prompt_template.system_instruction)

    def test_required_correction_projection_uses_step24_claim_ids(self) -> None:
        packet = SimpleNamespace(
            ordered_required_corrections=(
                SimpleNamespace(claim_id="claim-step38-a"),
                SimpleNamespace(claim_id="claim-step38-b"),
            )
        )
        self.assertEqual(
            controlled._required_correction_claim_ids(packet),
            ("claim-step38-a", "claim-step38-b"),
        )

    def test_all_named_non_provider_case_outcomes_execute(self) -> None:
        proof = controlled._golden_case_outcome_proofs(SUITE)
        self.assertEqual(
            set(proof) - {"proof_digest"},
            {case.case_id for case in SUITE.cases},
        )
        for case_id in (
            "supported-entry-into-force-clean",
            "temporal-unavailable-edge",
            "conflicting-ceiling-edge",
            "non-german-law-route",
        ):
            case = SUITE.case(case_id)
            executed = proof[case_id]
            self.assertEqual(executed["case_hash"], case.case_hash)
            self.assertEqual(
                executed["executed_question_digest"],
                case.question_digest,
            )
            self.assertTrue(executed["question_exactly_executed"])
            self.assertEqual(executed["knowledge_as_of"], case.knowledge_as_of.isoformat())
            self.assertEqual(executed["executed_route"], case.expected_route.value)
            self.assertEqual(
                executed["executed_evidence_status"],
                case.expected_evidence_status.value,
            )
            self.assertEqual(
                executed["executed_final_output"],
                case.expected_final_output.value,
            )
            self.assertEqual(
                executed["executed_provision_ids"],
                case.expected_provision_ids,
            )

        supported = proof["supported-entry-into-force-clean"]
        self.assertEqual(supported["actual_outcome_status"], "VERIFIED_ANSWER")
        self.assertEqual(supported["required_correction_count"], 0)
        self.assertIsNotNone(supported["verified_answer_hash"])
        self.assertEqual(supported["executed_source_ids"], (REAL_SOURCE_ID,))
        self.assertEqual(supported["executed_version_ids"], (REAL_VERSION_IDENTITY,))

        for case_id in ("conflicting-ceiling-edge", "temporal-unavailable-edge"):
            value = proof[case_id]
            self.assertIsNotNone(value["review_result_hash"])
            self.assertIsNone(value["verified_answer_hash"])
            self.assertIn(
                value["actual_outcome_status"],
                {"CONFIRMATION_REQUIRED", "HUMAN_REVIEW_REQUIRED"},
            )

        route_only = proof["non-german-law-route"]
        self.assertEqual(route_only["actual_outcome_status"], "PASS_THROUGH_RESULT")
        self.assertFalse(route_only["step26_invoked"])
        self.assertIsNone(route_only["step20_outcome_hash"])

    def test_named_case_execution_proof_rejects_detached_question_digest(self) -> None:
        supported = controlled._execute_named_golden_case_proofs(SUITE)[0]
        with self.assertRaises(IntegrityError):
            replace(supported, executed_question_digest="f" * 64)

    def test_retrieval_projection_uses_typed_item_authority_and_publication(self) -> None:
        selected_route = fixture_route()
        outcome = bundle_outcome(
            metadata(),
            route_value=selected_route,
            effective_scope=selected_route.effective_scope,
        )
        temporal = resolve(outcome, route_value=selected_route)
        digest = "d" * 64
        artifacts = SimpleNamespace(
            hybrid_outcome=outcome,
            artifacts_hash=digest,
            retrieval_input=SimpleNamespace(
                input_hash=digest,
                question_text="Welche Regel gilt?",
                route=selected_route,
            ),
            lexical_inputs=(
                (
                    SimpleNamespace(retrieval_mode=RetrievalMode.EXACT_IDENTIFIER),
                    SimpleNamespace(result_hash=digest, candidates=()),
                ),
            ),
            embedding_result=SimpleNamespace(result_hash=digest),
            vector_result=SimpleNamespace(result_hash=digest),
            temporal_projection_receipt=SimpleNamespace(receipt_hash=digest),
            attestation=SimpleNamespace(
                retrieval_input_kind="PRIMARY",
                attestation_hash=digest,
                data_plane_credential_purpose=(
                    CredentialPurpose.APPLICATION_DATABASE
                ),
                cross_tenant_rls_visible_count=0,
                negative_source_leak_count=0,
                approved_local_e5_backend=False,
                same_database=True,
            ),
        )
        projected = controlled._retrieval_evidence(artifacts, temporal)
        actual = outcome.bundle.ordered_items[0]
        public = projected["selected_candidate_identities"][0]
        self.assertEqual(public["authority_level"], actual.authority_level.value)
        self.assertEqual(
            public["publication_state"],
            actual.publication_state.value,
        )

    def test_offline_base_security_is_unknown_not_runtime_pass(self) -> None:
        # This test exercises only the Step 38 offline-security projection.
        # Scan an isolated empty production root so a later, legitimate Step
        # 39 implementation does not rewrite or weaken the historical Step 38
        # boundary scanner and its committed closure evidence.
        with tempfile.TemporaryDirectory(prefix="step38-empty-boundary-") as root:
            boundary = controlled._step39_boundary_scan(
                root_specs=(("synthetic-empty-root", Path(root)),),
                allowlisted_hit_counts={},
            )
        self.assertTrue(boundary.passed)
        self.assertEqual(boundary.unexpected_production_bridge_hits, 0)
        payload = controlled._base_payload(
            mode="OFFLINE_DEVELOPMENT_ONLY",
            repository={},
            suite=SUITE,
            inventory=(),
            offline={"status": "NOT_RUN"},
            step39_boundary=boundary,
        )
        security = payload["security"]
        self.assertEqual(security["runtime_security_status"], "NOT_RUN_OFFLINE")
        self.assertEqual(security["cross_user_access"], "UNKNOWN_NOT_RUN")
        self.assertEqual(security["secret_leakage_count"], "UNKNOWN_NOT_RUN")
        self.assertEqual(
            security["static_authority_contracts"]["evidence_class"],
            "STATIC_CONTRACT_ATTESTATION",
        )
        self.assertEqual(payload["step39_boundary"]["status"], "PASS")
        self.assertEqual(
            payload["step39_boundary"]["reviewed_allowlisted_hits_count"],
            0,
        )
        provider = payload["provider"]
        self.assertEqual(provider["provider_id"], "openrouter")
        self.assertEqual(provider["model_id"], "moonshotai/kimi-k2")
        self.assertEqual(provider["api_origin"], "https://openrouter.ai")
        self.assertEqual(
            provider["chat_completions_path"],
            "/api/v1/chat/completions",
        )
        for field in (
            "adapter_version",
            "endpoint_class",
            "immutable_model_revision",
            "config_digest",
        ):
            self.assertIn(field, provider)

    def test_openrouter_credential_is_consumed_before_child_work(self) -> None:
        fake_value = "openrouter-test-value-never-serialized"
        with patch.dict(
            os.environ,
            {controlled.OPENROUTER_KEY_ENVIRONMENT_NAME: fake_value},
            clear=False,
        ):
            secret = controlled._consume_openrouter_environment_credential()
            self.assertNotIn(
                controlled.OPENROUTER_KEY_ENVIRONMENT_NAME,
                os.environ,
            )
        self.assertIsInstance(secret, SecretValue)
        assert secret is not None
        self.assertEqual(secret.purpose, CredentialPurpose.MODEL_PROVIDER)
        self.assertEqual(str(secret), "<redacted>")
        self.assertNotIn(fake_value, repr(secret))

    def test_only_minimal_reexec_environment_receives_openrouter_secret(self) -> None:
        fake_value = "openrouter-test-value-never-serialized"
        secret = SecretValue(
            fake_value,
            purpose=CredentialPurpose.MODEL_PROVIDER,
            source_name=controlled.OPENROUTER_KEY_ENVIRONMENT_NAME,
        )
        with patch.dict(
            os.environ,
            {
                "MOONSHOT_API_KEY": "legacy-provider-test-secret",
                "UNRELATED_SECRET": "unrelated-test-secret",
            },
            clear=False,
        ):
            child = controlled._minimal_openrouter_reexec_environment(secret)
        self.assertEqual(
            child[controlled.OPENROUTER_KEY_ENVIRONMENT_NAME],
            fake_value,
        )
        self.assertNotIn("MOONSHOT_API_KEY", child)
        self.assertNotIn("UNRELATED_SECRET", child)

    def test_step39_boundary_scan_rejects_fake_production_bridge(self) -> None:
        with tempfile.TemporaryDirectory(prefix="step38-step39-negative-") as root:
            path = Path(root) / "critic_bridge.py"
            path.write_text(
                "def start_critic_prompt_loop_production_bridge():\n"
                "    return True\n",
                encoding="utf-8",
            )
            proof = controlled._step39_boundary_scan(
                root_specs=(("synthetic-production-root", Path(root)),),
                allowlisted_hit_counts={},
            )
        self.assertFalse(proof.passed)
        self.assertEqual(proof.status, "FAIL")
        self.assertEqual(proof.unexpected_production_bridge_hits, 1)
        self.assertEqual(proof.reviewed_allowlisted_hit_count, 0)
        self.assertFalse(
            controlled._closure_eligible_with_boundary_scan(
                SimpleNamespace(closure_eligible=True),
                proof,
            )
        )

    def test_coherent_projection_status_follows_closure_gate(self) -> None:
        class ProjectionProof:
            def __init__(self, closure_eligible: bool) -> None:
                self.closure_eligible = closure_eligible

            def __getattr__(self, name: str):
                if name == "activation_recovery_observation":
                    return SimpleNamespace(observation_hash="d" * 64)
                if name in {
                    "activation_recovery_failure_point",
                    "real_second_model_inference_status",
                    "review_case_type",
                }:
                    return SimpleNamespace(value="TYPED_TEST_VALUE")
                if name.endswith(("verified", "denied", "suppressed")):
                    return True
                return "d" * 64

        self.assertEqual(
            controlled._coherent_runtime_evidence(ProjectionProof(False))["status"],
            "BLOCKED_SAFELY_NOT_CLOSURE",
        )
        self.assertEqual(
            controlled._coherent_runtime_evidence(ProjectionProof(True))["status"],
            "PASS_REAL_COHERENT_PERSONAL_MEMORY_LINEAGE",
        )


class SanitizedRealPhaseTests(unittest.TestCase):
    def test_primary_backup_later_reuse_one_owned_database_exactly(self) -> None:
        primary = retrieval_input()
        backup = adapter.build_canonical_backup_retrieval_input(
            primary,
            SUITE,
            request_id="request-step38-one-db-backup",
        )
        later = adapter.build_canonical_related_retrieval_input(
            backup,
            question_text=adapter.canonical_later_question(BACKUP),
            request_id="request-step38-one-db-later",
        )
        owned_root = object()
        owned_runner = object()
        owned_database = "step38_one_owned_database"
        common = {
            "root": owned_root,
            "database": owned_database,
            "database_runner": owned_runner,
            "data_plane_session_user": "mp_s38_app_contract",
            "runtime_instance_digest": "d" * 64,
            "database_instance_digest": "e" * 64,
            "corpus_roots": object(),
            "embedding_backend": object(),
            "embedding_cache": object(),
        }
        marker = object()
        with patch.object(
            adapter,
            "_run_step38_retrieval_on_owned_database",
            return_value=marker,
        ) as run:
            self.assertIs(
                adapter.run_step38_real_retrieval_on_owned_database(
                    primary,
                    **common,
                ),
                marker,
            )
            self.assertIs(
                adapter.run_step38_backup_retrieval_on_owned_database(
                    backup,
                    **common,
                ),
                marker,
            )
            self.assertIs(
                adapter.run_step38_related_retrieval_on_owned_database(
                    later,
                    **common,
                ),
                marker,
            )

        calls = run.call_args_list
        self.assertEqual(
            tuple(value.args[0] for value in calls),
            (primary, backup, later),
        )
        self.assertEqual(
            tuple(value.kwargs["seed_fixture"] for value in calls),
            (True, False, False),
        )
        self.assertEqual(
            {value.kwargs["database"] for value in calls},
            {owned_database},
        )
        self.assertEqual(
            {value.kwargs["database_instance_digest"] for value in calls},
            {"e" * 64},
        )
        self.assertTrue(
            all(value.kwargs["root"] is owned_root for value in calls)
        )
        self.assertTrue(
            all(
                value.kwargs["database_runner"] is owned_runner
                for value in calls
            )
        )
        self.assertEqual(
            backup.routing_input.context_metadata[
                "step38_primary_attempt_input_hash"
            ],
            primary.input_hash,
        )
        self.assertEqual(
            later.routing_input.context_metadata["step38_primary_input_hash"],
            backup.input_hash,
        )
        self.assertEqual(
            {
                primary.golden_case.expected_source_id,
                backup.golden_case.expected_source_id,
                later.primary_input.golden_case.expected_source_id,
            },
            {REAL_SOURCE_ID},
        )
        self.assertEqual(
            {
                primary.route.selected_manifest_digest,
                backup.route.selected_manifest_digest,
                later.route.selected_manifest_digest,
            },
            {primary.route.selected_manifest_digest},
        )
        self.assertEqual(
            {
                primary.route.selected_hat_id,
                backup.route.selected_hat_id,
                later.route.selected_hat_id,
            },
            {adapter.REAL_HAT_ID},
        )
        self.assertEqual(
            SUITE.real_corpus_fixture.version_identity,
            REAL_VERSION_IDENTITY,
        )

    def test_local_e5_verified_files_are_canonicalized_before_artifact(self) -> None:
        class UnsortedLocalE5:
            verified_files = (
                ("tokenizer.json", "b" * 64, 20),
                ("config.json", "a" * 64, 10),
            )

            @staticmethod
            def identity():
                return adapter.EmbeddingBackendIdentity(
                    backend_name="local-transformers-e5",
                    backend_version="test-runtime",
                    backend_fingerprint="c" * 64,
                    model_digest=adapter.load_approved_model_spec().model_digest,
                )

        with patch.object(adapter, "LocalE5Backend", UnsortedLocalE5):
            identity, files, approved = adapter._embedding_backend_facts(
                UnsortedLocalE5()
            )
        self.assertTrue(approved)
        self.assertEqual(identity.backend_name, "local-transformers-e5")
        self.assertEqual(files, tuple(sorted(UnsortedLocalE5.verified_files)))

    def test_wrapper_maps_contract_and_integrity_faults_to_exact_codes(self) -> None:
        faults = (ContractValidationError, IntegrityError)
        for code in sorted(adapter._SANITIZED_REAL_PHASE_CODES):
            for fault in faults:
                with self.subTest(code=code, fault=fault.__name__):
                    with self.assertRaises(adapter.Step38RealRetrievalError) as caught:
                        adapter._run_sanitized_real_phase(
                            code,
                            lambda fault=fault: (_ for _ in ()).throw(
                                fault("sensitive-internal-detail")
                            ),
                        )
                    self.assertEqual(caught.exception.code, code)
                    self.assertNotIn(
                        "sensitive-internal-detail",
                        str(caught.exception),
                    )
                    self.assertIsNone(caught.exception.__cause__)

    def test_wrapper_preserves_success_and_existing_sanitized_errors(self) -> None:
        code = "PRIMARY_REAL_RETRIEVAL_CONTRACT_OR_INTEGRITY_FAILURE"
        marker = object()
        self.assertIs(
            adapter._run_sanitized_real_phase(code, lambda: marker),
            marker,
        )
        original = adapter.Step38RealRetrievalError("EXACT_EXISTING_CODE")
        with self.assertRaises(adapter.Step38RealRetrievalError) as caught:
            adapter._run_sanitized_real_phase(
                code,
                lambda: (_ for _ in ()).throw(original),
            )
        self.assertIs(caught.exception, original)

    def test_wrapper_does_not_reclassify_unrelated_runtime_faults(self) -> None:
        code = "PRIMARY_REAL_RETRIEVAL_CONTRACT_OR_INTEGRITY_FAILURE"
        with self.assertRaisesRegex(RuntimeError, "unrelated-runtime-fault"):
            adapter._run_sanitized_real_phase(
                code,
                lambda: (_ for _ in ()).throw(
                    RuntimeError("unrelated-runtime-fault")
                ),
            )
        with self.assertRaises(ValueError):
            adapter._run_sanitized_real_phase("UNREVIEWED_CODE", lambda: None)

    def test_public_real_entrypoints_use_phase_specific_sanitization(self) -> None:
        primary = retrieval_input()
        backup = adapter.build_canonical_backup_retrieval_input(
            primary,
            SUITE,
            request_id="request-step38-sanitized-backup",
        )
        related = adapter.build_canonical_related_retrieval_input(
            primary,
            question_text=adapter.canonical_later_question(PRIMARY),
            request_id="request-step38-sanitized-related",
        )
        common = {
            "root": object(),
            "database": "step38_contract_test",
            "database_runner": object(),
            "data_plane_session_user": "mp_s38_app_contract",
            "runtime_instance_digest": "d" * 64,
            "database_instance_digest": "e" * 64,
            "corpus_roots": object(),
            "embedding_backend": object(),
            "embedding_cache": object(),
        }
        cases = (
            (
                adapter.run_step38_real_retrieval_on_owned_database,
                primary,
                "PRIMARY_REAL_RETRIEVAL_CONTRACT_OR_INTEGRITY_FAILURE",
            ),
            (
                adapter.run_step38_related_retrieval_on_owned_database,
                related,
                "RELATED_REAL_RETRIEVAL_CONTRACT_OR_INTEGRITY_FAILURE",
            ),
            (
                adapter.run_step38_backup_retrieval_on_owned_database,
                backup,
                "BACKUP_REAL_RETRIEVAL_CONTRACT_OR_INTEGRITY_FAILURE",
            ),
        )
        for entrypoint, value, expected_code in cases:
            with self.subTest(entrypoint=entrypoint.__name__):
                with patch.object(
                    adapter,
                    "_run_step38_retrieval_on_owned_database",
                    side_effect=ContractValidationError(
                        "sensitive-phase-detail"
                    ),
                ):
                    with self.assertRaises(
                        adapter.Step38RealRetrievalError
                    ) as caught:
                        entrypoint(value, **common)
                self.assertEqual(caught.exception.code, expected_code)
                self.assertNotIn("sensitive-phase-detail", str(caught.exception))


class InputBoundaryContinuationTests(unittest.TestCase):
    def test_backup_contract_is_exact_special_case_reservation_case(self) -> None:
        self.assertEqual(BACKUP.case_id, "backup-special-case-reservation")
        self.assertEqual(
            BACKUP.question,
            "Vervollständige nach Abschnitt II der BMJErnAnO den Satz, indem "
            "du „nicht“ einsetzt oder die Lücke leer lässt: „Für besondere "
            "Fälle behalte ich mir die Ernennung und Entlassung der unter I. "
            "genannten Beamtinnen und Beamten ___ vor.“",
        )
        self.assertEqual(BACKUP.expected_provision_ids, ("II.",))
        self.assertEqual(
            BACKUP.expected_correction_condition.value,
            "SPECIAL_CASE_RESERVATION_NEGATION_MUST_BE_REMOVED",
        )
        primary = retrieval_input()
        backup = adapter.build_canonical_backup_retrieval_input(
            primary,
            SUITE,
            request_id="request-step38-backup-special-case-contract",
        )
        self.assertEqual(
            adapter._full_text_query(backup),
            "besondere Fälle behalte Ernennung Entlassung",
        )
        self.assertEqual(
            adapter._keyword_query(backup),
            ("besondere Fälle", "behalte", "Ernennung", "Entlassung"),
        )
        self.assertEqual(
            adapter.canonical_later_question(BACKUP),
            "Ist die Ernennung und Entlassung der unter I. genannten "
            "Beamtinnen und Beamten nach Abschnitt II der BMJErnAnO für "
            "besondere Fälle vorbehalten?",
        )

    def test_exact_primary_question_and_provision_three_are_required(self) -> None:
        value = retrieval_input()
        self.assertEqual(value.question_text, PRIMARY.question)
        self.assertEqual(value.provision_selector.section_identifier, "III.")

        with self.assertRaisesRegex(
            adapter.Step38RealRetrievalError,
            "same-database retrieval proof failed",
        ) as caught:
            adapter.Step38GermanLawRetrievalInput(
                routing_input=value.routing_input,
                route=value.route,
                policy_context=value.policy_context,
                policy_result=value.policy_result,
                golden_case=value.golden_case,
                question_text=value.question_text,
                provision_selector=StatuteSectionSelector(
                    REAL_OFFICIAL_IDENTIFIER,
                    "II.",
                ),
            )
        self.assertEqual(
            caught.exception.code,
            "GOLDEN_PROVISION_SELECTOR_REQUIRED",
        )

    def test_non_german_law_route_is_rejected(self) -> None:
        value = retrieval_input()
        pass_input = replace(
            value.routing_input,
            candidate_hat_descriptors=(),
        )
        selected_route = route_knowledge_request(pass_input)
        self.assertIs(selected_route.knowledge_route, KnowledgeRoute.PASS_THROUGH)
        with self.assertRaises(adapter.Step38RealRetrievalError) as caught:
            adapter.Step38GermanLawRetrievalInput(
                routing_input=pass_input,
                route=selected_route,
                policy_context=value.policy_context,
                policy_result=value.policy_result,
                golden_case=PRIMARY,
                question_text=PRIMARY.question,
                provision_selector=StatuteSectionSelector(
                    REAL_OFFICIAL_IDENTIFIER,
                    "III.",
                ),
            )
        self.assertEqual(caught.exception.code, "GERMAN_LAW_ROUTE_REQUIRED")

    def test_policy_from_another_route_is_rejected(self) -> None:
        value = retrieval_input()
        detached_policy = replace(
            value.policy_result,
            request_id="request-step38-detached-policy",
        )
        with self.assertRaises(adapter.Step38RealRetrievalError) as caught:
            adapter.Step38GermanLawRetrievalInput(
                routing_input=value.routing_input,
                route=value.route,
                policy_context=value.policy_context,
                policy_result=detached_policy,
                golden_case=PRIMARY,
                question_text=PRIMARY.question,
                provision_selector=value.provision_selector,
            )
        self.assertEqual(caught.exception.code, "ROUTE_POLICY_BINDING_MISMATCH")

    def test_canonical_builder_runs_step17_with_real_manifest_digest(self) -> None:
        value = retrieval_input()
        identity = decode_manifest(
            (
                REPOSITORY_ROOT
                / "config/hats/german-law-1.0.0.json"
            ).read_bytes(),
            schema_path=REPOSITORY_ROOT / "schemas/hat-manifest.schema.json",
        )
        self.assertEqual(
            value.route.selected_manifest_digest,
            identity.typed_manifest_digest,
        )
        self.assertNotEqual(value.route.selected_manifest_digest, "3" * 64)
        self.assertEqual(
            route_knowledge_request(value.routing_input).route_hash,
            value.route.route_hash,
        )
        self.assertEqual(value.routing_input.normalized_query_or_subject, PRIMARY.question)

    def test_related_builder_reuses_scope_but_not_request_or_question(self) -> None:
        primary = retrieval_input()
        question = adapter.canonical_later_question(primary.golden_case)
        related = adapter.build_canonical_related_retrieval_input(
            primary,
            question_text=question,
            request_id="request-step38-related-retrieval",
        )
        self.assertEqual(related.primary_input, primary)
        self.assertEqual(related.question_text, question)
        self.assertNotEqual(related.route.request_id, primary.route.request_id)
        self.assertNotEqual(related.input_hash, primary.input_hash)
        self.assertEqual(related.route.tenant_id, primary.route.tenant_id)
        self.assertEqual(related.route.user_id, primary.route.user_id)
        self.assertEqual(related.route.effective_scope, primary.route.effective_scope)
        self.assertEqual(
            route_knowledge_request(related.routing_input).route_hash,
            related.route.route_hash,
        )
        with self.assertRaises(ContractValidationError):
            adapter.build_canonical_related_retrieval_input(
                primary,
                question_text=primary.question_text,
                request_id="request-step38-related-duplicate",
            )

    def test_backup_builder_is_real_distinct_and_scope_bound(self) -> None:
        primary = retrieval_input()
        backup = adapter.build_canonical_backup_retrieval_input(
            primary,
            SUITE,
            request_id="request-step38-backup-retrieval",
        )
        self.assertEqual(backup.golden_case, BACKUP)
        self.assertEqual(backup.question_text, BACKUP.question)
        self.assertEqual(backup.provision_selector.section_identifier, "II.")
        self.assertNotEqual(backup.route.request_id, primary.route.request_id)
        self.assertNotEqual(backup.input_hash, primary.input_hash)
        self.assertEqual(backup.route.tenant_id, primary.route.tenant_id)
        self.assertEqual(backup.route.user_id, primary.route.user_id)
        self.assertEqual(backup.route.effective_scope, primary.route.effective_scope)
        self.assertEqual(
            route_knowledge_request(backup.routing_input).route_hash,
            backup.route.route_hash,
        )
        self.assertEqual(
            backup.routing_input.context_metadata[
                "step38_primary_attempt_input_hash"
            ],
            primary.input_hash,
        )
        related = adapter.build_canonical_related_retrieval_input(
            backup,
            question_text=adapter.canonical_later_question(BACKUP),
            request_id="request-step38-backup-related",
        )
        self.assertEqual(related.provision_selector.section_identifier, "II.")
        self.assertIn("besondere Fälle", related.question_text)
        self.assertEqual(
            related.routing_input.context_metadata["step38_primary_input_hash"],
            backup.input_hash,
        )
        self.assertEqual(related.primary_input, backup)
        self.assertEqual(
            (
                primary.golden_case.case_id,
                backup.golden_case.case_id,
                related.primary_input.golden_case.case_id,
            ),
            (
                "primary-entry-into-force",
                "backup-special-case-reservation",
                "backup-special-case-reservation",
            ),
        )
        self.assertEqual(
            (
                primary.provision_selector.section_identifier,
                backup.provision_selector.section_identifier,
                related.provision_selector.section_identifier,
            ),
            ("III.", "II.", "II."),
        )
        self.assertEqual(
            {
                primary.route.tenant_id,
                backup.route.tenant_id,
                related.route.tenant_id,
            },
            {primary.route.tenant_id},
        )
        self.assertEqual(
            {
                primary.route.user_id,
                backup.route.user_id,
                related.route.user_id,
            },
            {primary.route.user_id},
        )
        self.assertEqual(
            {
                primary.route.selected_manifest_digest,
                backup.route.selected_manifest_digest,
                related.route.selected_manifest_digest,
            },
            {primary.route.selected_manifest_digest},
        )
        self.assertEqual(
            {
                canonical_sha256(primary.route.effective_scope),
                canonical_sha256(backup.route.effective_scope),
                canonical_sha256(related.route.effective_scope),
            },
            {canonical_sha256(primary.route.effective_scope)},
        )
        self.assertEqual(
            len(
                {
                    primary.input_hash,
                    backup.input_hash,
                    related.input_hash,
                }
            ),
            3,
        )
        with self.assertRaises(ContractValidationError):
            adapter.build_canonical_related_retrieval_input(
                backup,
                question_text=adapter.canonical_later_question(PRIMARY),
                request_id="request-step38-backup-wrong-related",
            )


class IsolationPlanTests(unittest.TestCase):
    def test_unpublished_weak_cross_hat_and_cross_tenant_records_are_explicit(self) -> None:
        records = adapter._fixture_records(
            make_record(),
            "tenant-step38-real-retrieval",
        )
        published, unpublished, weak, other_hat, other_tenant = records
        self.assertEqual(published.source_id, REAL_SOURCE_ID)
        self.assertIs(
            published.current_publication_state,
            SourcePublicationState.PUBLISHED,
        )
        self.assertIs(
            unpublished.current_publication_state,
            SourcePublicationState.REGISTERED,
        )
        self.assertIs(
            weak.authority.authority_level,
            SourceAuthorityLevel.INFORMATIONAL_SECONDARY,
        )
        self.assertNotEqual(other_hat.hat_scope_id, published.hat_scope_id)
        self.assertNotEqual(other_tenant.tenant_id, published.tenant_id)
        self.assertEqual(
            tuple(record.source_id for record in records[1:]),
            adapter._NEGATIVE_SOURCE_IDS,
        )

    def test_each_negative_probe_uses_pre_candidate_hard_scope_sql(self) -> None:
        value = retrieval_input()
        for source_id in adapter._NEGATIVE_SOURCE_IDS:
            request = adapter._retrieval_request(
                value,
                RetrievalMode.EXACT_IDENTIFIER,
                ExactIdentifierSelector(
                    ExactIdentifierField.SOURCE_ID,
                    (source_id,),
                ),
            )
            transaction = _CaptureTransaction()
            self.assertEqual(
                CockroachRetrievalRepository().search(transaction, request),
                (),
            )
            self.assertIn(
                "sre.current_publication_state = 'PUBLISHED'",
                transaction.sql,
            )
            self.assertIn(
                "sre.authority_level IN ('OFFICIAL_PRIMARY', "
                "'AUTHORITATIVE_SECONDARY')",
                transaction.sql,
            )
            self.assertEqual(transaction.parameters[0], value.route.tenant_id)
            self.assertEqual(transaction.parameters[1], adapter.REAL_HAT_SCOPE_ID)
            self.assertIn(source_id, transaction.parameters)


class ReplayAndAttestationTests(unittest.TestCase):
    def test_public_data_plane_purpose_metadata_is_secret_scan_safe(self) -> None:
        assert_secret_free(
            {
                "retrieval": {
                    "data_plane_purpose": "APPLICATION_DATABASE",
                    "data_plane_session_user_recorded": False,
                }
            },
            surface="STEP38_LIVE_RETRIEVAL_PUBLIC_PROJECTION",
            reject_machine_paths=True,
        )
        with self.assertRaises(ValueError):
            assert_secret_free(
                {"data_plane_credential_purpose": "APPLICATION_DATABASE"},
                surface="STEP38_REJECT_UNSAFE_METADATA_KEY",
                reject_machine_paths=True,
            )

    def test_seed_projection_is_deterministic_for_same_inputs(self) -> None:
        records = adapter._fixture_records(
            make_record(),
            "tenant-step38-real-retrieval",
        )
        item = {
            "publication_item_digest": "a" * 64,
            "version_identity": REAL_VERSION_IDENTITY,
            "document_identity": "step38-document",
            "source_class": records[0].source_kind,
            "official_identifier": REAL_OFFICIAL_IDENTIFIER,
            "temporal_facts_digest": "b" * 64,
        }
        provisions = tuple(
            {
                "provision_identifier": identifier,
                "record_id": f"record-{offset}",
                "official_text_de": f"Abschnitt {identifier}",
                "content_sha256": canonical_sha256(f"Abschnitt {identifier}"),
            }
            for offset, identifier in enumerate(REAL_PROVISION_HASHES)
        )
        first = adapter.step18._seed_sql(
            records,
            item,
            provisions,
            "c" * 64,
        )
        second = adapter.step18._seed_sql(
            records,
            item,
            provisions,
            "c" * 64,
        )
        self.assertEqual(first, second)
        self.assertEqual(first.count("content_text"), len(records) * 3)

    def test_attestation_is_hash_only_and_separates_temporal_projection(self) -> None:
        digest = "d" * 64
        value = adapter.Step38RealRetrievalAttestation(
            attestation_version=adapter.ATTESTATION_VERSION,
            adapter_version=adapter.ADAPTER_VERSION,
            input_hash=digest,
            runtime_instance_digest=digest,
            database_instance_digest=digest,
            step14_manifest_digest=digest,
            step15_manifest_digest=digest,
            step16_manifest_digest=digest,
            source_id=REAL_SOURCE_ID,
            official_identifier=REAL_OFFICIAL_IDENTIFIER,
            version_identity=REAL_VERSION_IDENTITY,
            provision_hashes=tuple(REAL_PROVISION_HASHES.items()),
            corpus_bytes_class=adapter.CORPUS_BYTES_CLASS,
            temporal_projection_class=adapter.TEMPORAL_PROJECTION_CLASS,
            temporal_projection_receipt_hash=digest,
            retrieval_input_kind="PRIMARY",
            lexical_result_hashes=(digest,),
            embedding_record_hashes=(digest,),
            embedding_result_hash=digest,
            embedding_backend_identity_digest=digest,
            embedding_verified_files_digest=digest,
            approved_local_e5_backend=False,
            vector_result_hash=digest,
            hybrid_outcome_hash=digest,
            evidence_bundle_hash=digest,
            data_plane_credential_purpose=CredentialPurpose.APPLICATION_DATABASE,
            data_plane_session_user="mp_s38_app_contract",
            cross_tenant_rls_visible_count=0,
            negative_source_leak_count=0,
            same_database=True,
            provider_calls=0,
            aws_calls=0,
            s3_mutations=0,
            migration_calls=0,
            runtime_lifecycle_calls=0,
        )
        serialized = json.dumps(asdict(value), sort_keys=True)
        self.assertIn("REAL_GERMAN_LAW_CORPUS_FIXTURE", serialized)
        self.assertIn(
            "FIXTURE_BOUND_EXACT_GERMAN_DATE_PARSE_APPLIED_BEFORE_RETRIEVAL",
            serialized,
        )
        self.assertNotIn("Diese Anordnung tritt", serialized)

        with self.assertRaises(ContractValidationError):
            replace(value, provider_calls=1)
        with self.assertRaises(ContractValidationError):
            replace(value, data_plane_session_user="root")

    def test_temporal_projection_sql_is_exact_hash_bound_and_non_authoritative(self) -> None:
        receipt = project_bmjernano_temporal_facts(PROVISION_III)
        sql = adapter._temporal_projection_sql(
            tenant_id="tenant-step38-real-retrieval",
            receipt=receipt,
        )
        self.assertIn(receipt.receipt_hash, sql)
        self.assertIn("2024-01-01", sql)
        self.assertIn(REAL_SOURCE_ID, sql)
        self.assertIn(REAL_PROVISION_HASHES["III."], sql)
        self.assertIn('"step38_temporal_projection_model_inference_used":false', sql)
        self.assertIn(
            '"step38_temporal_projection_canonical_evidence_authority":false',
            sql,
        )
        self.assertNotIn(PROVISION_III, sql)

    def test_data_plane_probe_matches_shared_retrieval_context(self) -> None:
        value = retrieval_input()

        class Transaction:
            def fetch_one(self, sql, parameters=None):
                return {
                    "session_user": "mp_s38_app_contract",
                    "current_user": "mp_s38_app_contract",
                    "cross_tenant_visible": 0,
                }

        class Runner:
            context = None
            operation_kind = None

            def require_credential_purpose(self, purpose):
                self.purpose = purpose

            def run(self, context, callback, *, operation_kind):
                self.context = context
                self.operation_kind = operation_kind
                return callback(Transaction())

        runner = Runner()
        session_user, cross_tenant_visible = adapter._data_plane_probe(
            runner,
            value,
            expected_session_user="mp_s38_app_contract",
        )
        self.assertEqual(runner.purpose, CredentialPurpose.APPLICATION_DATABASE)
        self.assertEqual(runner.context.tenant_id, value.route.tenant_id)
        self.assertIsNone(runner.context.user_id)
        self.assertEqual(runner.context.access_mode.value, "TENANT_SHARED")
        self.assertEqual(
            runner.operation_kind,
            "STEP38_RETRIEVAL_ROLE_RLS_PROBE",
        )
        self.assertEqual(session_user, "mp_s38_app_contract")
        self.assertEqual(cross_tenant_visible, 0)

    def test_public_api_requires_caller_owned_runtime_inputs(self) -> None:
        parameters = inspect.signature(
            adapter.run_step38_real_retrieval_on_owned_database
        ).parameters
        self.assertEqual(
            tuple(parameters),
            (
                "retrieval_input",
                "root",
                "database",
                "database_runner",
                "data_plane_session_user",
                "runtime_instance_digest",
                "database_instance_digest",
                "corpus_roots",
                "embedding_backend",
                "embedding_cache",
            ),
        )
        related_parameters = inspect.signature(
            adapter.run_step38_related_retrieval_on_owned_database
        ).parameters
        self.assertEqual(tuple(related_parameters), tuple(parameters))
        backup_parameters = inspect.signature(
            adapter.run_step38_backup_retrieval_on_owned_database
        ).parameters
        self.assertEqual(tuple(backup_parameters), tuple(parameters))

    def test_adapter_has_no_runtime_migration_provider_or_cloud_ownership(self) -> None:
        source = (SCRIPTS / "step38_real_retrieval.py").read_text(
            encoding="utf-8"
        )
        for forbidden in (
            "LocalRuntime(",
            "create_database(",
            "drop_database(",
            "apply_migrations(",
            "SerializableTransactionRunner(\n        lambda: step18._HttpConnection",
            "boto3",
            "OpenAI",
            "OpenRouter",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
