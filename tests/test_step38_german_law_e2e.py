"""Offline Step 38 German Law integration and authority-boundary tests."""

from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
import unittest
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tests._support import REPOSITORY_ROOT
from tests.test_step20_hybrid_evidence_bundle import route
from tests.test_step21_temporal_resolution import bundle_outcome, metadata, resolve
from tests.test_step23_claim_evidence_binding import pipeline as claim_pipeline
from tests.test_step26_verified_answer_output import final_policy_result, hat_lineage
from tests.test_step31_active_patch_retrieval import (
    active_fixture,
    candidate,
    request_for,
    service,
)
from tests.test_step33_audit_ledger import chain
from tests.test_step35_personal_memory_ui import FakeBackend, OWNER_A

SCRIPTS = REPOSITORY_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import step38_real_retrieval as real_retrieval  # noqa: E402
import run_step38_german_law_e2e_validation as controlled_validation  # noqa: E402

from aioa_memory_kernel.answers import (
    FinalAnswerRequest,
    FinalOutputStatus,
    Step26BoundaryError,
    VerifiedAnswerService,
)
from aioa_memory_kernel.audit_ledger import verify_audit_chain
from aioa_memory_kernel.claims import (
    ClaimEvidenceCandidateStatus,
    ClaimEvidenceBindingService,
    ClaimEvidenceRelation,
    prepare_claim_binding_request,
)
from aioa_memory_kernel.contracts.exceptions import (
    ContractValidationError,
    IntegrityError,
)
from aioa_memory_kernel.contracts.enums import EvidenceStatus, KnowledgeRoute
from aioa_memory_kernel.contracts.serialization import canonical_json, canonical_sha256
from aioa_memory_kernel.corrections import (
    HmacSha256PacketAuthenticator,
    build_correction_packet,
)
from aioa_memory_kernel.german_law.e2e import (
    CanonicalEvidenceExactVerifier,
    DraftV2TargetMode,
    EvidenceBoundCorrectionContext,
    EvidenceBoundCorrectionItem,
    EvidenceBoundDraftV2Provider,
    GoldenCaseKind,
    GoldenCorrectionCondition,
    GoldenExpectedFinalOutput,
    HashBoundOfficialEvidenceProjection,
    REAL_OFFICIAL_IDENTIFIER,
    REAL_PROVISION_HASHES,
    REAL_SOURCE_ID,
    REAL_VERSION_IDENTITY,
    STEP38_PROJECTION_VERSION,
    Step38FixtureClass,
    build_before_after_trace,
    build_draft_v2_target_projection,
    build_evidence_bound_correction_context,
    load_german_law_golden_cases,
    project_bmjernano_temporal_facts,
    project_verified_bmjernano_evidence,
    prove_draft_v1_evidence_blind,
    step38_component_inventory,
)
from aioa_memory_kernel.german_law.e2e_runtime import (
    build_step38_post_retrieval_policy_receipt,
)
from aioa_memory_kernel.modeling import (
    DraftV1Service,
    ProviderIdentity,
    ProviderResponse,
    build_provider_call_request,
    load_approved_provider_spec,
    prepare_model_generation_request,
)
from aioa_memory_kernel.personal_memory import Step31ReasonCode
from aioa_memory_kernel.reliability import (
    FailureDomain,
    FailurePoint,
    FailureRecoveryCaseResult,
    RecoveryStatus,
)
from aioa_memory_kernel.security.credentials import CredentialPurpose, SecretValue
from aioa_memory_kernel.security import assert_secret_free
from aioa_memory_kernel.temporal import TemporalQueryMode
from aioa_memory_kernel.verification import (
    CorrectedEvidenceVerdict,
    CorrectedEvidenceVerifierRequest,
    CorrectedEvidenceVerifierSignal,
    DeterministicFakeSemanticVerifier,
    DraftV2PipelineResult,
    DraftV2LayeredVerifier,
    DraftV2Service,
    EvidenceBindingResult,
    FinalStep25ClaimVerdict,
    SemanticCandidateVerdict,
    Step25ReasonCode,
    VerificationSummaryStatus,
    prepare_draft_v2_generation_request,
)


FIXTURE = REPOSITORY_ROOT / "tests/fixtures/step38_german_law_cases.json"
LIVE_EVIDENCE = (
    REPOSITORY_ROOT
    / "docs/evidence/e2e/step38-german-law-full-e2e-validation.json"
)
DEMO_TRACE = (
    REPOSITORY_ROOT
    / "docs/evidence/e2e/step38-german-law-demo-trace.json"
)
LIVE_EVIDENCE_FILE_SHA256 = (
    "b43152c0b7e9020b4078f2abd89dc25986b14cbd09e5cf4b7a67ce525130eb13"
)
LIVE_EVIDENCE_VALIDATION_DIGEST = (
    "b3536f019354226c77f8672464d21986b4dc37e63f8e0d725352e57f5d7e0042"
)
DEMO_TRACE_ROOT_DIGEST = (
    "624dd6aaea273ec5cf0afe72825b7d853601f6f133a97ea1a9132ade45ebce31"
)
DEMO_TRACE_STAGE_IDS = (
    "QUESTION",
    "DRAFT_V1",
    "DETECTED_ISSUE",
    "SOURCE_EVIDENCE",
    "CORRECTION",
    "DRAFT_V2",
    "VERIFIED_ANSWER",
    "PERSONAL_MEMORY_PROPOSAL",
    "OWNER_APPROVAL",
    "ACTIVE_PATCH",
    "LATER_QUESTION",
    "CROSS_MODEL_REUSE",
)
NOW = datetime(2026, 8, 11, 10, 0, tzinfo=UTC)
TEST_KEY = bytes(range(32))
PROVISION_I = (
    "Auf Grund des Artikels 1 Absatz 1 der Anordnung des Bundespräsidenten über "
    "die Ernennung und Entlassung der Beamtinnen, Beamten, Richterinnen und "
    "Richter des Bundes vom 23. Juni 2004 (BGBl. I S. 1286) übertrage ich "
    "widerruflich 1.der Präsidentin oder dem Präsidenten des "
    "Bundesgerichtshofs,2.der Präsidentin oder dem Präsidenten des "
    "Bundesverwaltungsgerichts,3.der Präsidentin oder dem Präsidenten des "
    "Bundesfinanzhofs,4.der Präsidentin oder dem Präsidenten des "
    "Bundespatentgerichts,5.der Präsidentin oder dem Präsidenten des "
    "Bundesamtes für Justiz,6.der Präsidentin oder dem Präsidenten des "
    "Deutschen Patent- und Markenamtes,7.der Generalbundesanwältin oder dem "
    "Generalbundesanwalt beim Bundesgerichtshofjeweils für ihren oder seinen "
    "Geschäftsbereich die Ausübung des Rechtes zur Ernennung und Entlassung "
    "der Bundesbeamtinnen und Bundesbeamten der Besoldungsgruppen bis "
    "einschließlich A 15 der Bundesbesoldungsordnung A (Anlage I des "
    "Bundesbesoldungsgesetzes)."
)
PROVISION_II = (
    "Für besondere Fälle behalte ich mir die Ernennung und Entlassung der "
    "unter I. genannten Beamtinnen und Beamten vor."
)
PROVISION_III = (
    "Diese Anordnung tritt am 1. Januar 2024 in Kraft. Frühere Anordnungen "
    "zum selben Gegenstand sind nicht mehr anzuwenden."
)


class FixedClock:
    def now(self) -> datetime:
        return NOW


class CapturingProvider:
    def __init__(self, response_text: str, identity=None) -> None:
        self.identity = identity or load_approved_provider_spec().provider_identity()
        self.response_text = response_text
        self.requests = []

    def provider_identity(self):
        return self.identity

    def generate(self, request, timeout_policy):
        self.requests.append((request, timeout_policy))
        return ProviderResponse(
            provider_identity_digest=self.identity.identity_digest,
            model_id=self.identity.model_id,
            model_version=self.identity.model_revision_or_declared_version,
            provider_request_id=f"step38-offline-{len(self.requests)}",
            finish_reason="stop",
            response_content=self.response_text,
            usage_metadata={"prompt_tokens": 40, "completion_tokens": 20, "total_tokens": 60},
            latency_milliseconds=1,
        )


class DraftV1ThenExactTargetProvider(CapturingProvider):
    """Deterministic fake: wrong/correct V1, then the typed Step38 target."""

    def generate(self, request, timeout_policy):
        if hasattr(request, "original_query"):
            response_text = self.response_text
        else:
            document = json.loads(request.user_content)
            target = document["draft_v2_target_projection"]
            response_text = "\n".join(
                item["rendered_text"] for item in target["ordered_segments"]
            )
        self.requests.append((request, timeout_policy))
        return ProviderResponse(
            provider_identity_digest=self.identity.identity_digest,
            model_id=self.identity.model_id,
            model_version=self.identity.model_revision_or_declared_version,
            provider_request_id=f"step38-target-fake-{len(self.requests)}",
            finish_reason="stop",
            response_content=response_text,
            usage_metadata={
                "prompt_tokens": 40,
                "completion_tokens": 20,
                "total_tokens": 60,
            },
            latency_milliseconds=1,
        )


def primary_lineage():
    suite = load_german_law_golden_cases(FIXTURE)
    case = suite.case("primary-entry-into-force")
    projection = project_verified_bmjernano_evidence(
        {"I.": PROVISION_I, "II.": PROVISION_II, "III.": PROVISION_III}
    )
    temporal_projection = project_bmjernano_temporal_facts(PROVISION_III)
    retrieval_input = real_retrieval.build_canonical_primary_retrieval_input(
        suite,
        tenant_id="tenant-step20",
        user_id="user-step20",
        request_id="request-step38-primary",
    )
    selected_route = retrieval_input.route
    outcome = bundle_outcome(
        metadata(
            document=projection.source_id,
            version=projection.version_identity,
            provision="III.",
            official_identifier=projection.official_identifier,
            effective_from=temporal_projection.effective_from_date,
            extras={
                "step38_temporal_projection_receipt_hash": (
                    temporal_projection.receipt_hash
                ),
                "step38_temporal_projection_method": (
                    temporal_projection.projection_method
                ),
            },
        ),
        contents=(projection.exact_official_evidence[2],),
        source_ids=(projection.source_id,),
        route_value=selected_route,
        effective_scope=selected_route.effective_scope,
    )
    temporal = resolve(
        outcome,
        route_value=selected_route,
        mode=TemporalQueryMode.AS_OF,
        as_of=case.knowledge_as_of,
        now=NOW,
    )
    request = prepare_model_generation_request(temporal, case.question)
    v1_text = "Diese Anordnung tritt am 1. Januar 2025 in Kraft."
    v1_provider = CapturingProvider(v1_text, request.provider_identity)
    draft = DraftV1Service(
        v1_provider,
        clock=FixedClock(),
        sleep=lambda _delay: None,
    ).generate(request).draft
    binding = prepare_claim_binding_request(draft, (outcome.bundle,), temporal)
    snapshot = ClaimEvidenceBindingService().freeze_packet_input(binding)
    packet = build_correction_packet(snapshot)
    context = build_evidence_bound_correction_context(
        snapshot, (outcome.bundle,), packet
    )
    target_projection = build_draft_v2_target_projection(packet, context)
    false_claim = next(
        item
        for item in snapshot.ordered_claims
        if item.exact_claim_text
        == "Diese Anordnung tritt am 1. Januar 2025 in Kraft."
    )
    citation = next(
        item
        for item in packet.ordered_citations
        if item.claim_id == false_claim.claim_id
        and item.relation is ClaimEvidenceRelation.REFUTES
    )
    v2_text = target_projection.exact_output
    authenticator = HmacSha256PacketAuthenticator(
        key_id="step38-offline-packet-key",
        key_material=TEST_KEY,
    )
    receipt = authenticator.authenticate(packet)
    v2_request = prepare_draft_v2_generation_request(
        draft, packet, receipt, authenticator
    )
    base_provider = CapturingProvider(v2_text, v2_request.provider_identity)
    provider = EvidenceBoundDraftV2Provider(
        base_provider,
        packet,
        context,
        target_projection,
    )
    pipeline = DraftV2Service(
        provider,
        authenticator,
        verifier=DraftV2LayeredVerifier(
            corrected_evidence_verifier=CanonicalEvidenceExactVerifier(context)
        ),
        clock=FixedClock(),
        sleep=lambda _delay: None,
    ).generate_and_verify(v2_request)
    post_retrieval_policy = build_step38_post_retrieval_policy_receipt(
        retrieval_input.routing_input,
        selected_route,
        retrieval_input.policy_context,
        retrieval_input.policy_result,
        temporal,
    )
    final_request = FinalAnswerRequest(
        route=selected_route,
        policy_result=post_retrieval_policy.final_policy_result,
        step20_outcomes=(outcome,),
        temporal_result=temporal,
        draft_v1=draft,
        correction_packet=packet,
        integrity_receipt=receipt,
        step25_result=pipeline,
    )
    final = VerifiedAnswerService(authenticator).finalize(final_request)
    trace = build_before_after_trace(
        case,
        draft,
        packet,
        pipeline,
        final,
        context,
        provider.input_receipts[0],
    )
    return {
        "suite": suite,
        "case": case,
        "route": selected_route,
        "projection": projection,
        "temporal_projection": temporal_projection,
        "outcome": outcome,
        "temporal": temporal,
        "draft_request": request,
        "draft": draft,
        "snapshot": snapshot,
        "packet": packet,
        "context": context,
        "target_projection": target_projection,
        "citation": citation,
        "v2_request": v2_request,
        "base_provider": base_provider,
        "evidence_provider": provider,
        "pipeline": pipeline,
        "authenticator": authenticator,
        "integrity_receipt": receipt,
        "retrieval_input": retrieval_input,
        "post_retrieval_policy": post_retrieval_policy,
        "final_request": final_request,
        "final": final,
        "trace": trace,
    }


def backup_special_case_lineage(draft_text: str):
    """Run the provider-free Provision II fallback through its fail-closed gate."""

    suite = load_german_law_golden_cases(FIXTURE)
    case = suite.case(controlled_validation.BACKUP_SPECIAL_CASE_ID)
    projection = project_verified_bmjernano_evidence(
        {"I.": PROVISION_I, "II.": PROVISION_II, "III.": PROVISION_III}
    )
    temporal_projection = project_bmjernano_temporal_facts(PROVISION_III)
    primary_input = real_retrieval.build_canonical_primary_retrieval_input(
        suite,
        tenant_id="tenant-step20",
        user_id="user-step20",
        request_id="request-step38-backup-primary-attempt",
    )
    retrieval_input = real_retrieval.build_canonical_backup_retrieval_input(
        primary_input,
        suite,
        request_id="request-step38-backup-special-case",
    )
    selected_route = retrieval_input.route
    outcome = bundle_outcome(
        metadata(
            document=projection.source_id,
            version=projection.version_identity,
            provision="II.",
            official_identifier=projection.official_identifier,
            effective_from=temporal_projection.effective_from_date,
            extras={
                "step38_temporal_projection_receipt_hash": (
                    temporal_projection.receipt_hash
                ),
                "step38_temporal_projection_method": (
                    temporal_projection.projection_method
                ),
            },
        ),
        contents=(projection.exact_official_evidence[1],),
        source_ids=(projection.source_id,),
        route_value=selected_route,
        effective_scope=selected_route.effective_scope,
    )
    temporal = resolve(
        outcome,
        route_value=selected_route,
        mode=TemporalQueryMode.AS_OF,
        as_of=case.knowledge_as_of,
        now=NOW,
    )
    request = controlled_validation._prepare_real_draft_v1_request(
        temporal,
        case,
    )
    v1_provider = CapturingProvider(draft_text, request.provider_identity)
    draft = DraftV1Service(
        v1_provider,
        clock=FixedClock(),
        sleep=lambda _delay: None,
    ).generate(request).draft
    classification = (
        controlled_validation._backup_special_case_draft_classification(
            case,
            draft.draft_text,
        )
    )
    values = {
        "suite": suite,
        "case": case,
        "projection": projection,
        "temporal_projection": temporal_projection,
        "retrieval_input": retrieval_input,
        "route": selected_route,
        "outcome": outcome,
        "temporal": temporal,
        "draft_request": request,
        "draft": draft,
        "v1_provider": v1_provider,
        "classification": classification,
    }
    if classification == controlled_validation.BACKUP_DRAFT_INVALID:
        values["blocked_reason"] = "STEP38_BACKUP_RESPONSE_SHAPE_INVALID"
        return values

    snapshot = ClaimEvidenceBindingService().freeze_packet_input(
        prepare_claim_binding_request(draft, (outcome.bundle,), temporal)
    )
    packet = build_correction_packet(snapshot)
    values.update({"snapshot": snapshot, "packet": packet})
    if not packet.ordered_required_corrections or not packet.ordered_citations:
        values["blocked_reason"] = "STEP38_BACKUP_DEFECT_NOT_OBSERVED"
        return values

    context = build_evidence_bound_correction_context(
        snapshot,
        (outcome.bundle,),
        packet,
    )
    target_projection = build_draft_v2_target_projection(packet, context)
    authenticator = HmacSha256PacketAuthenticator(
        key_id="step38-backup-offline-packet-key",
        key_material=TEST_KEY,
    )
    integrity_receipt = authenticator.authenticate(packet)
    v2_request = prepare_draft_v2_generation_request(
        draft,
        packet,
        integrity_receipt,
        authenticator,
    )
    base_provider = CapturingProvider(
        target_projection.exact_output,
        v2_request.provider_identity,
    )
    evidence_provider = EvidenceBoundDraftV2Provider(
        base_provider,
        packet,
        context,
        target_projection,
    )
    pipeline = DraftV2Service(
        evidence_provider,
        authenticator,
        verifier=DraftV2LayeredVerifier(
            corrected_evidence_verifier=CanonicalEvidenceExactVerifier(context)
        ),
        clock=FixedClock(),
        sleep=lambda _delay: None,
    ).generate_and_verify(v2_request)
    post_retrieval_policy = build_step38_post_retrieval_policy_receipt(
        retrieval_input.routing_input,
        selected_route,
        retrieval_input.policy_context,
        retrieval_input.policy_result,
        temporal,
    )
    final_request = FinalAnswerRequest(
        route=selected_route,
        policy_result=post_retrieval_policy.final_policy_result,
        step20_outcomes=(outcome,),
        temporal_result=temporal,
        draft_v1=draft,
        correction_packet=packet,
        integrity_receipt=integrity_receipt,
        step25_result=pipeline,
    )
    final = VerifiedAnswerService(authenticator).finalize(final_request)
    values.update(
        {
            "context": context,
            "target_projection": target_projection,
            "authenticator": authenticator,
            "integrity_receipt": integrity_receipt,
            "v2_request": v2_request,
            "base_provider": base_provider,
            "evidence_provider": evidence_provider,
            "pipeline": pipeline,
            "post_retrieval_policy": post_retrieval_policy,
            "final_request": final_request,
            "final": final,
        }
    )
    return values


class GoldenCaseFixtureTests(unittest.TestCase):
    def test_fixture_distinguishes_real_and_synthetic_cases(self) -> None:
        first = load_german_law_golden_cases(FIXTURE)
        second = load_german_law_golden_cases(FIXTURE)
        self.assertEqual(first.suite_hash, second.suite_hash)
        self.assertEqual(len(first.cases), 6)
        self.assertIs(
            first.case("primary-entry-into-force").fixture_class,
            Step38FixtureClass.REAL_GERMAN_LAW_CORPUS_FIXTURE,
        )
        self.assertIs(
            first.case("temporal-unavailable-edge").fixture_class,
            Step38FixtureClass.SYNTHETIC_EDGE_CASE,
        )
        self.assertEqual(
            {item.case_kind for item in first.cases},
            set(GoldenCaseKind),
        )
        primary = first.case("primary-entry-into-force")
        self.assertIsInstance(primary.expected_route, KnowledgeRoute)
        self.assertIsInstance(primary.expected_evidence_status, EvidenceStatus)
        self.assertIsInstance(
            primary.expected_correction_condition,
            GoldenCorrectionCondition,
        )
        self.assertIsInstance(
            primary.expected_final_output,
            GoldenExpectedFinalOutput,
        )

    def test_fixture_parser_is_closed_immutable_and_real_identity_cannot_be_relabelled(
        self,
    ) -> None:
        suite = load_german_law_golden_cases(FIXTURE)
        with self.assertRaises(FrozenInstanceError):
            suite.real_corpus_fixture.source_id = "forged-source"
        self.assertIsInstance(suite.real_corpus_fixture.provisions, tuple)
        with self.assertRaises(ContractValidationError):
            replace(
                suite.case("temporal-unavailable-edge"),
                fixture_class=Step38FixtureClass.REAL_GERMAN_LAW_CORPUS_FIXTURE,
            )
        with self.assertRaises(ContractValidationError):
            replace(
                suite.case("primary-entry-into-force"),
                expected_route="HAT_ASSIST",
            )

        original = json.loads(FIXTURE.read_text(encoding="utf-8"))
        invalid_payloads = []
        unknown_suite = copy.deepcopy(original)
        unknown_suite["unexpected"] = True
        invalid_payloads.append(unknown_suite)
        unknown_case = copy.deepcopy(original)
        unknown_case["cases"][0]["unexpected"] = True
        invalid_payloads.append(unknown_case)
        unknown_nested = copy.deepcopy(original)
        unknown_nested["real_corpus_fixture"]["provisions"][0]["unexpected"] = True
        invalid_payloads.append(unknown_nested)
        open_enum = copy.deepcopy(original)
        open_enum["cases"][0]["expected_route"] = "FORGED_ROUTE"
        invalid_payloads.append(open_enum)

        with tempfile.TemporaryDirectory() as directory:
            for ordinal, payload in enumerate(invalid_payloads):
                path = Path(directory) / f"invalid-{ordinal}.json"
                path.write_text(json.dumps(payload), encoding="utf-8")
                with self.subTest(ordinal=ordinal):
                    with self.assertRaises(ContractValidationError):
                        load_german_law_golden_cases(path)

            raw = FIXTURE.read_text(encoding="utf-8")
            duplicate = raw.replace(
                '"schema_version":',
                '"schema_version": "1.0.0", "schema_version":',
                1,
            )
            path = Path(directory) / "duplicate.json"
            path.write_text(duplicate, encoding="utf-8")
            with self.assertRaises(ContractValidationError):
                load_german_law_golden_cases(path)

    def test_component_inventory_covers_every_step_13_through_37(self) -> None:
        inventory = step38_component_inventory()
        self.assertEqual({item.step for item in inventory}, set(range(13, 38)))
        self.assertFalse(any(item.grants_new_authority for item in inventory))

    def test_real_bmjernano_projection_is_exact_hash_bound_and_non_authoritative(self) -> None:
        projection = project_verified_bmjernano_evidence(
            {"I.": PROVISION_I, "II.": PROVISION_II, "III.": PROVISION_III}
        )
        self.assertEqual(
            projection.provision_hashes,
            tuple(REAL_PROVISION_HASHES[item] for item in ("I.", "II.", "III.")),
        )
        self.assertEqual(projection.exact_official_evidence[2], PROVISION_III)
        self.assertFalse(projection.canonical_evidence_authority)
        self.assertFalse(projection.source_publication_authority)
        with self.assertRaises(IntegrityError):
            project_verified_bmjernano_evidence(
                {
                    "I.": PROVISION_I.replace("A 15", "A 14"),
                    "II.": PROVISION_II,
                    "III.": PROVISION_III,
                }
            )
        with self.assertRaises(IntegrityError):
            HashBoundOfficialEvidenceProjection(
                projection_version=STEP38_PROJECTION_VERSION,
                source_id=REAL_SOURCE_ID,
                official_identifier=REAL_OFFICIAL_IDENTIFIER,
                version_identity=REAL_VERSION_IDENTITY,
                provision_hashes=tuple(
                    REAL_PROVISION_HASHES[item] for item in ("I.", "II.", "III.")
                ),
                exact_official_evidence=("forged", PROVISION_II, PROVISION_III),
                canonical_evidence_authority=False,
                source_publication_authority=False,
            )

    def test_temporal_projection_is_fixture_bound_exact_and_used_by_step21(self) -> None:
        receipt = project_bmjernano_temporal_facts(PROVISION_III)
        self.assertEqual(receipt.parsed_effective_date_text, "1. Januar 2024")
        self.assertEqual(receipt.effective_from_date, "2024-01-01")
        self.assertTrue(receipt.fixture_bound)
        self.assertFalse(receipt.preexisting_temporal_metadata_used)
        self.assertFalse(receipt.model_inference_used)
        self.assertFalse(receipt.canonical_evidence_authority)
        with self.assertRaises(IntegrityError):
            project_bmjernano_temporal_facts(
                PROVISION_III.replace("1. Januar 2024", "2. Januar 2024")
            )


class BackupSpecialCaseFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.wrong = backup_special_case_lineage(
            controlled_validation.PROVISION_II_WRONG_POLARITY_TEXT
        )
        cls.correct = backup_special_case_lineage(PROVISION_II)
        cls.bare = backup_special_case_lineage("nicht")

    def _run_runner_flow_with_v1(self, draft_text: str):
        provider = DraftV1ThenExactTargetProvider(
            draft_text,
            load_approved_provider_spec().provider_identity(),
        )
        retrieval = SimpleNamespace(
            retrieval_input=self.wrong["retrieval_input"],
            hybrid_outcome=self.wrong["outcome"],
            temporal_projection_receipt=self.wrong["temporal_projection"],
        )
        credential = SecretValue(
            "step38-offline-provider-test-key",
            purpose=CredentialPurpose.MODEL_PROVIDER,
            source_name="OPENROUTER_API_KEY",
        )
        with patch.object(
            controlled_validation,
            "OpenRouterDraftV1Adapter",
            return_value=provider,
        ):
            public, upstream = controlled_validation._real_provider_flow(
                retrieval,
                self.wrong["temporal"],
                credential,
            )
        return public, upstream, provider

    def test_provider_prompt_requires_complete_sentence_without_revealing_polarity(
        self,
    ) -> None:
        request = self.wrong["draft_request"]
        case = self.wrong["case"]
        provider_call = build_provider_call_request(request)
        instruction = provider_call.system_instruction
        self.assertEqual(provider_call.original_query, case.question)
        self.assertIn("gesamten, vollständig ausgefüllten deutschen Satz", instruction)
        self.assertIn("Einzelwortantwort", instruction)
        self.assertIn("Platzhalter", instruction)
        self.assertNotIn(PROVISION_II, instruction)
        self.assertNotIn(
            controlled_validation.PROVISION_II_WRONG_POLARITY_TEXT,
            instruction,
        )
        self.assertNotRegex(instruction.casefold(), r"\bnicht\b")
        self.assertNotIn("___", instruction)
        blindness = prove_draft_v1_evidence_blind(request, case.question)
        self.assertFalse(blindness.evidence_fields_projected)
        self.assertFalse(blindness.tools_enabled)

    def test_wrong_exact_polarity_runs_step23_through_verified_step26(self) -> None:
        values = self.wrong
        self.assertEqual(
            values["classification"],
            controlled_validation.BACKUP_DRAFT_WRONG_EXACT,
        )
        self.assertNotIn("blocked_reason", values)
        snapshot = values["snapshot"]
        self.assertEqual(len(snapshot.ordered_claims), 1)
        self.assertEqual(snapshot.ordered_claims[0].exact_claim_text, values["draft"].draft_text)
        self.assertEqual(snapshot.ordered_claims[0].atomicity.value, "ATOMIC")
        self.assertEqual(len(snapshot.ordered_candidate_assessments), 1)
        self.assertIs(
            snapshot.ordered_candidate_assessments[0].candidate_status,
            ClaimEvidenceCandidateStatus.REFUTED,
        )
        self.assertEqual(len(snapshot.ordered_evidence_links), 1)
        self.assertIs(
            snapshot.ordered_evidence_links[0].relation,
            ClaimEvidenceRelation.REFUTES,
        )
        self.assertEqual(len(values["packet"].ordered_required_corrections), 1)
        self.assertEqual(len(values["packet"].ordered_citations), 1)
        target = values["target_projection"]
        self.assertTrue(target.exact_output)
        self.assertNotIn(values["draft"].draft_text, target.exact_output)
        self.assertIn(PROVISION_II.removesuffix("."), target.exact_output)
        target.verify_generated_output(target.exact_output)
        pipeline = values["pipeline"]
        self.assertIs(
            pipeline.verification_summary.summary_status,
            VerificationSummaryStatus.VERIFIED,
        )
        self.assertEqual(
            pipeline.verification_summary.required_corrections_satisfied,
            1,
        )
        self.assertEqual(
            pipeline.verification_summary.required_corrections_unsatisfied,
            0,
        )
        self.assertEqual(
            pipeline.verification_summary.prohibited_claim_violations,
            0,
        )
        final = values["final"]
        self.assertIs(final.output_status, FinalOutputStatus.VERIFIED_ANSWER)
        self.assertIsNotNone(final.verified_answer)
        self.assertEqual(
            final.verified_answer.answer_text,
            pipeline.draft_v2.draft_text,
        )

    def test_correct_exact_polarity_has_no_material_defect_and_blocks_fallback(
        self,
    ) -> None:
        values = self.correct
        self.assertEqual(
            values["classification"],
            controlled_validation.BACKUP_DRAFT_CORRECT_EXACT,
        )
        snapshot = values["snapshot"]
        self.assertEqual(len(snapshot.ordered_claims), 1)
        self.assertIs(
            snapshot.ordered_candidate_assessments[0].candidate_status,
            ClaimEvidenceCandidateStatus.SUPPORTED,
        )
        self.assertEqual(values["packet"].ordered_required_corrections, ())
        self.assertEqual(
            values["blocked_reason"],
            "STEP38_BACKUP_DEFECT_NOT_OBSERVED",
        )
        self.assertNotIn("target_projection", values)
        self.assertNotIn("pipeline", values)
        self.assertNotIn("final", values)
        public, upstream, provider = self._run_runner_flow_with_v1(PROVISION_II)
        self.assertIsNone(upstream)
        self.assertEqual(len(provider.requests), 1)
        self.assertEqual(public["reason"], "STEP38_BACKUP_DEFECT_NOT_OBSERVED")
        self.assertEqual(
            public["case_attempts"][0]["draft_shape_classification"],
            controlled_validation.BACKUP_DRAFT_CORRECT_EXACT,
        )

    def test_bare_polarity_word_fails_closed_before_step23(self) -> None:
        values = self.bare
        self.assertEqual(
            values["classification"],
            controlled_validation.BACKUP_DRAFT_INVALID,
        )
        self.assertEqual(
            values["blocked_reason"],
            "STEP38_BACKUP_RESPONSE_SHAPE_INVALID",
        )
        self.assertEqual(len(values["v1_provider"].requests), 1)
        self.assertNotIn("snapshot", values)
        self.assertNotIn("packet", values)
        self.assertNotIn("target_projection", values)
        self.assertNotIn("pipeline", values)
        self.assertNotIn("final", values)
        public, upstream, provider = self._run_runner_flow_with_v1("nicht")
        self.assertIsNone(upstream)
        self.assertEqual(len(provider.requests), 1)
        self.assertEqual(public["reason"], "STEP38_BACKUP_RESPONSE_SHAPE_INVALID")
        self.assertEqual(
            public["case_attempts"][0]["draft_shape_classification"],
            controlled_validation.BACKUP_DRAFT_INVALID,
        )
        self.assertIsNone(
            public["case_attempts"][0]["required_correction_count"]
        )

    def test_runner_fake_provider_executes_wrong_backup_through_verified_lineage(
        self,
    ) -> None:
        public, upstream, provider = self._run_runner_flow_with_v1(
            controlled_validation.PROVISION_II_WRONG_POLARITY_TEXT
        )
        self.assertIsNotNone(upstream)
        self.assertEqual(len(provider.requests), 2)
        self.assertEqual(public["status"], "PASS_REAL_VERIFIED_LINEAGE")
        self.assertEqual(
            public["selected_case_id"],
            controlled_validation.BACKUP_SPECIAL_CASE_ID,
        )
        self.assertEqual(
            public["case_attempts"][0]["draft_shape_classification"],
            controlled_validation.BACKUP_DRAFT_WRONG_EXACT,
        )
        self.assertTrue(public["draft_v1_evidence_blind"])
        self.assertTrue(public["corrected_evidence"]["proofs"])
        self.assertEqual(
            upstream.step25_result.verification_summary.summary_status,
            VerificationSummaryStatus.VERIFIED,
        )
        self.assertIs(
            upstream.final_outcome.output_status,
            FinalOutputStatus.VERIFIED_ANSWER,
        )

    def test_golden_outcome_projection_retains_all_six_named_cases(self) -> None:
        suite = self.wrong["suite"]
        proofs = controlled_validation._golden_case_outcome_proofs(suite)
        self.assertEqual(
            set(proofs) - {"proof_digest"},
            {case.case_id for case in suite.cases},
        )
        self.assertIn(controlled_validation.BACKUP_SPECIAL_CASE_ID, proofs)
        self.assertNotIn("backup-a15-ceiling", proofs)


class PrimaryCorrectionFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.values = primary_lineage()

    def test_draft_v1_is_evidence_blind_and_fake_is_not_real_provider_claim(
        self,
    ) -> None:
        proof = prove_draft_v1_evidence_blind(
            self.values["draft_request"],
            self.values["case"].question,
        )
        self.assertFalse(proof.evidence_fields_projected)
        self.assertFalse(proof.tools_enabled)
        self.assertEqual(
            self.values["draft"].draft_text,
            "Diese Anordnung tritt am 1. Januar 2025 in Kraft.",
        )
        self.assertEqual(
            self.values["temporal_projection"].effective_from_date,
            self.values["outcome"]
            .bundle.ordered_items[0]
            .structured_metadata["temporal_facts"]["effective_from"],
        )
        self.assertEqual(
            self.values["temporal"].assessments[0].temporal_facts_digest,
            self.values["outcome"]
            .bundle.ordered_items[0]
            .structured_metadata["temporal_facts_digest"],
        )
        provider_call = build_provider_call_request(self.values["draft_request"])
        calls_before = len(self.values["base_provider"].requests)
        with self.assertRaises(TypeError):
            EvidenceBoundDraftV2Provider(
                self.values["base_provider"],
                self.values["packet"],
                self.values["context"],
                self.values["target_projection"],
            ).generate(provider_call, self.values["draft_request"].timeout_policy)
        self.assertEqual(len(self.values["base_provider"].requests), calls_before)

    def test_context_verifies_citation_link_bundle_item_and_exact_span(self) -> None:
        context = self.values["context"]
        packet = self.values["packet"]
        self.assertEqual(len(context.ordered_items), len(packet.ordered_citations))
        self.assertFalse(context.canonical_evidence_authority)
        self.assertFalse(context.final_answer_authority)
        supported = context.item(self.values["citation"].citation_id)
        self.assertIsNotNone(supported)
        self.assertEqual(
            supported.exact_excerpt,
            "Diese Anordnung tritt am 1. Januar 2024 in Kraft.",
        )
        tampered = copy.copy(self.values["snapshot"])
        object.__setattr__(tampered, "snapshot_hash", "0" * 64)
        with self.assertRaises(IntegrityError):
            build_evidence_bound_correction_context(
                tampered,
                (self.values["outcome"].bundle,),
                packet,
            )

    def test_exact_target_projection_is_typed_atomic_and_deterministic(self) -> None:
        packet = self.values["packet"]
        context = self.values["context"]
        projection = self.values["target_projection"]
        replay = build_draft_v2_target_projection(packet, context)
        self.assertIs(projection.mode, DraftV2TargetMode.EXACT_REFUTES_ONLY)
        self.assertEqual(projection.projection_hash, replay.projection_hash)
        self.assertEqual(projection.exact_output, replay.exact_output)
        self.assertFalse(projection.canonical_evidence_authority)
        self.assertFalse(projection.final_answer_authority)
        self.assertFalse(projection.model_authority)
        self.assertEqual(projection.correction_packet_hash, packet.packet_hash)
        self.assertEqual(projection.evidence_context_hash, context.context_hash)
        self.assertEqual(len(projection.ordered_segments), 1)
        segment = projection.ordered_segments[0]
        self.assertEqual(segment.citation_id, self.values["citation"].citation_id)
        self.assertTrue(
            segment.rendered_text.endswith(f"[citation:{segment.citation_id}].")
        )
        projection.verify_generated_output(projection.exact_output)

    def test_self_consistent_forged_target_is_rejected_before_provider_call(
        self,
    ) -> None:
        projection = self.values["target_projection"]
        segment = projection.ordered_segments[0]
        forged_excerpt = "Dieser Inhalt wurde nicht kanonisch belegt."
        forged_rendered = (
            "Dieser Inhalt wurde nicht kanonisch belegt "
            f"[citation:{segment.citation_id}]."
        )
        forged_segment = replace(
            segment,
            exact_excerpt=forged_excerpt,
            exact_excerpt_sha256=hashlib.sha256(
                forged_excerpt.encode("utf-8")
            ).hexdigest(),
            rendered_text=forged_rendered,
            rendered_text_sha256=hashlib.sha256(
                forged_rendered.encode("utf-8")
            ).hexdigest(),
        )
        forged_projection = replace(
            projection,
            ordered_segments=(forged_segment,),
        )
        underlying = CapturingProvider(
            projection.exact_output,
            self.values["v2_request"].provider_identity,
        )
        with self.assertRaises(IntegrityError):
            EvidenceBoundDraftV2Provider(
                underlying,
                self.values["packet"],
                self.values["context"],
                forged_projection,
            )
        self.assertEqual(underlying.requests, [])

    def test_exact_target_projection_accounts_for_omission_safe_correction(self) -> None:
        outcome, _temporal, draft, _request, snapshot = claim_pipeline(
            "Das Gesetz gilt. Die Regel gilt. Eine unbelegte Aussage.",
            contents=(
                "Das Gesetz gilt nicht.",
                "Die Regel gilt nicht.",
                "Anderer Inhalt.",
            ),
        )
        packet = build_correction_packet(snapshot)
        context = build_evidence_bound_correction_context(
            snapshot,
            (outcome.bundle,),
            packet,
        )
        projection = build_draft_v2_target_projection(packet, context)
        projected_corrections = {
            item.correction_hash for item in projection.ordered_segments
        }
        self.assertEqual(len(projection.ordered_segments), 2)
        self.assertEqual(len(projection.omitted_correction_hashes), 1)
        self.assertEqual(
            projected_corrections | set(projection.omitted_correction_hashes),
            {item.correction_hash for item in packet.ordered_required_corrections},
        )
        authenticator = HmacSha256PacketAuthenticator(
            key_id="step38-target-projection-test-key",
            key_material=TEST_KEY,
        )
        receipt = authenticator.authenticate(packet)
        request = prepare_draft_v2_generation_request(
            draft,
            packet,
            receipt,
            authenticator,
        )
        provider = EvidenceBoundDraftV2Provider(
            CapturingProvider(projection.exact_output, request.provider_identity),
            packet,
            context,
            projection,
        )
        result = DraftV2Service(
            provider,
            authenticator,
            verifier=DraftV2LayeredVerifier(
                corrected_evidence_verifier=CanonicalEvidenceExactVerifier(context)
            ),
            clock=FixedClock(),
            sleep=lambda _delay: None,
        ).generate_and_verify(request)
        self.assertIs(
            result.verification_summary.summary_status,
            VerificationSummaryStatus.VERIFIED,
        )
        self.assertEqual(result.verification_summary.required_corrections_satisfied, 3)
        self.assertEqual(result.verification_summary.required_corrections_unsatisfied, 0)
        self.assertEqual(result.verification_summary.prohibited_claim_violations, 0)

    def test_exact_target_whitelist_rejects_extra_prefixed_and_prohibited_text(
        self,
    ) -> None:
        projection = self.values["target_projection"]
        expected = projection.exact_output
        for value in (
            f"Korrektur: {expected}",
            expected.replace("2024", "2025"),
            f"{expected}\nZusätzliche Erläuterung.",
            self.values["draft"].draft_text,
        ):
            with self.subTest(value=value):
                with self.assertRaises(ContractValidationError):
                    projection.verify_generated_output(value)

        rejecting = EvidenceBoundDraftV2Provider(
            CapturingProvider(f"Korrektur: {expected}", self.values["v2_request"].provider_identity),
            self.values["packet"],
            self.values["context"],
            projection,
        )
        with self.assertRaises(IntegrityError):
            DraftV2Service(
                rejecting,
                self.values["authenticator"],
                verifier=DraftV2LayeredVerifier(
                    corrected_evidence_verifier=CanonicalEvidenceExactVerifier(
                        self.values["context"]
                    )
                ),
                clock=FixedClock(),
                sleep=lambda _delay: None,
            ).generate_and_verify(self.values["v2_request"])
        self.assertEqual(rejecting.input_receipts, ())

    def test_context_budget_and_provider_identity_fail_closed(self) -> None:
        context = self.values["context"]
        seed = context.ordered_items[0]
        oversized = "A" * (16 * 1024 + 1)
        with self.assertRaises(ContractValidationError):
            replace(
                seed,
                exact_excerpt=oversized,
                exact_excerpt_sha256=hashlib.sha256(
                    oversized.encode("utf-8")
                ).hexdigest(),
            )
        approved = load_approved_provider_spec().provider_identity()
        wrong = ProviderIdentity(
            provider_id=approved.provider_id,
            adapter_version=approved.adapter_version,
            model_id="unapproved-step38-model",
            model_revision_or_declared_version=approved.model_revision_or_declared_version,
            endpoint_class=approved.endpoint_class,
            tooling_disabled=True,
            function_calling_disabled=True,
            web_browsing_disabled=True,
            code_execution_disabled=True,
            immutable_model_revision=True,
        )
        with self.assertRaises(ContractValidationError):
            EvidenceBoundDraftV2Provider(
                CapturingProvider("x", wrong),
                self.values["packet"],
                context,
                self.values["target_projection"],
            )

    def test_draft_v2_augmented_input_has_a_separate_auditable_receipt(self) -> None:
        original_hash = self.values["v2_request"].request_hash
        forwarded = self.values["base_provider"].requests[0][0]
        body = json.loads(forwarded.user_content)
        projected = body["verified_evidence_context"]
        target = body["draft_v2_target_projection"]
        self.assertEqual(projected["context_hash"], self.values["context"].context_hash)
        self.assertEqual(
            projected["correction_packet_hash"],
            self.values["packet"].packet_hash,
        )
        self.assertIn("exact_excerpt", projected["items"][0])
        self.assertEqual(
            target["projection_hash"],
            self.values["target_projection"].projection_hash,
        )
        self.assertEqual(
            target["expected_output_sha256"],
            self.values["target_projection"].expected_output_sha256,
        )
        self.assertEqual(self.values["v2_request"].request_hash, original_hash)
        receipts = self.values["evidence_provider"].input_receipts
        self.assertEqual(len(receipts), 1)
        self.assertEqual(receipts[0].evidence_context_hash, self.values["context"].context_hash)
        self.assertEqual(
            receipts[0].draft_v2_target_projection_hash,
            self.values["target_projection"].projection_hash,
        )
        self.assertEqual(receipts[0].augmented_provider_request_hash, forwarded.request_hash)
        self.assertNotEqual(
            receipts[0].base_provider_request_hash,
            receipts[0].augmented_provider_request_hash,
        )
        self.assertEqual(
            self.values["pipeline"].verification_summary.summary_status,
            VerificationSummaryStatus.VERIFIED,
        )

    def test_exact_verifier_supports_only_exact_cited_span(self) -> None:
        pipeline = self.values["pipeline"]
        claim = pipeline.ordered_claims[0]
        citation = self.values["citation"]
        correction_ids = tuple(
            item.correction_id
            for item in pipeline.verification_summary.required_correction_results
            if item.status.value == "SATISFIED"
            and claim.claim_id in item.matched_draft_v2_claim_ids
        )
        request = CorrectedEvidenceVerifierRequest(
            claim_id=claim.claim_id,
            claim_hash=claim.claim_hash,
            draft_v2_hash=pipeline.draft_v2.draft_v2_hash,
            correction_packet_hash=self.values["packet"].packet_hash,
            claim_text=claim.exact_claim_text,
            satisfied_correction_ids=correction_ids,
            cited_citation_ids=(citation.citation_id,),
        )
        verifier = CanonicalEvidenceExactVerifier(self.values["context"])
        supported = verifier.verify(request)
        self.assertIs(supported.verdict, CorrectedEvidenceVerdict.SUPPORTS)
        self.assertIsNotNone(supported.proof)
        self.assertEqual(
            supported.proof.proof_hash,
            self.values["pipeline"]
            .ordered_claim_verifications[0]
            .corrected_evidence_proof_hash,
        )
        self.assertEqual(
            supported.proof.original_evidence_link.link_hash,
            self.values["context"]
            .item(citation.citation_id)
            .original_evidence_link.link_hash,
        )
        changed = replace(
            request,
            claim_text=request.claim_text.replace("2024", "2025"),
        )
        uncertain = verifier.verify(changed)
        self.assertIs(uncertain.verdict, CorrectedEvidenceVerdict.UNCERTAIN)
        self.assertEqual(uncertain.evidence_reference_ids, ())

    def test_refuted_v1_requires_full_proof_and_forged_proof_fails_closed(
        self,
    ) -> None:
        pipeline = self.values["pipeline"]
        source_assessment = next(
            item
            for item in self.values["snapshot"].ordered_candidate_assessments
            if item.claim_id == self.values["citation"].claim_id
        )
        self.assertIs(
            source_assessment.candidate_status,
            ClaimEvidenceCandidateStatus.REFUTED,
        )
        verified = pipeline.ordered_claim_verifications[0]
        self.assertIs(verified.evidence_binding_result, EvidenceBindingResult.SUPPORTED)
        self.assertIs(
            verified.final_step25_verdict,
            FinalStep25ClaimVerdict.VERIFIED_SUPPORTED,
        )
        self.assertIn(
            Step25ReasonCode.CORRECTED_EVIDENCE_SUPPORTS,
            verified.reason_codes,
        )
        self.assertIsNotNone(verified.corrected_evidence_signal_hash)
        self.assertIsNotNone(verified.corrected_evidence_proof)
        self.assertIsNotNone(verified.corrected_evidence_proof_hash)
        self.assertEqual(
            verified.corrected_evidence_proof.proof_hash,
            verified.corrected_evidence_proof_hash,
        )
        self.assertTrue(
            all(
                item.status.value == "SATISFIED"
                for item in pipeline.verification_summary.required_correction_results
            )
        )

        ordinary = DeterministicFakeSemanticVerifier(
            default=SemanticCandidateVerdict.SUPPORTS
        )
        _claims, ordinary_results, ordinary_summary = DraftV2LayeredVerifier(
            ordinary
        ).verify(pipeline.draft_v2, self.values["packet"])
        self.assertEqual(ordinary.requests, [])
        self.assertIs(
            ordinary_results[0].final_step25_verdict,
            FinalStep25ClaimVerdict.VERIFIED_REFUTED,
        )
        self.assertIs(ordinary_summary.summary_status, VerificationSummaryStatus.FAILED)

        forged_link = replace(
            verified.corrected_evidence_proof.original_evidence_link,
            temporal_assessment_hash="f" * 64,
        )
        forged_proof = replace(
            verified.corrected_evidence_proof,
            original_evidence_link=forged_link,
        )

        class ForgedCorrectedVerifier:
            def verify(self, request):
                return CorrectedEvidenceVerifierSignal(
                    request_hash=request.request_hash,
                    verdict=CorrectedEvidenceVerdict.SUPPORTS,
                    evidence_reference_ids=request.cited_citation_ids,
                    proof=forged_proof,
                )

        _claims, forged_results, forged_summary = DraftV2LayeredVerifier(
            corrected_evidence_verifier=ForgedCorrectedVerifier()
        ).verify(pipeline.draft_v2, self.values["packet"])
        self.assertIn(
            Step25ReasonCode.CORRECTED_EVIDENCE_INVALID,
            forged_results[0].reason_codes,
        )
        self.assertIs(
            forged_results[0].final_step25_verdict,
            FinalStep25ClaimVerdict.VERIFIED_REFUTED,
        )
        self.assertIs(forged_summary.summary_status, VerificationSummaryStatus.FAILED)

    def test_proofless_uncertain_corrected_signal_cannot_finalize_step26(self) -> None:
        pipeline = self.values["pipeline"]
        verified = pipeline.ordered_claim_verifications[0]
        proof = verified.corrected_evidence_proof
        assert proof is not None
        uncertain = CorrectedEvidenceVerifierSignal(
            request_hash=proof.request_hash,
            verdict=CorrectedEvidenceVerdict.UNCERTAIN,
            evidence_reference_ids=(),
            proof=None,
        )
        uncertain_reasons = tuple(
            Step25ReasonCode.CORRECTED_EVIDENCE_UNCERTAIN
            if code is Step25ReasonCode.CORRECTED_EVIDENCE_SUPPORTS
            else code
            for code in verified.reason_codes
        )
        with self.assertRaises(ContractValidationError):
            replace(
                verified,
                corrected_evidence_signal_hash=uncertain.signal_hash,
                corrected_evidence_proof=None,
                corrected_evidence_proof_hash=None,
                reason_codes=uncertain_reasons,
            )

        persisted_tamper = copy.copy(verified)
        object.__setattr__(
            persisted_tamper,
            "corrected_evidence_signal_hash",
            uncertain.signal_hash,
        )
        object.__setattr__(persisted_tamper, "corrected_evidence_proof", None)
        object.__setattr__(persisted_tamper, "corrected_evidence_proof_hash", None)
        object.__setattr__(persisted_tamper, "reason_codes", uncertain_reasons)
        object.__setattr__(
            persisted_tamper,
            "verification_hash",
            canonical_sha256(
                persisted_tamper,
                exclude_fields=("verification_hash",),
            ),
        )
        tampered_summary = replace(
            pipeline.verification_summary,
            ordered_claim_verification_hashes=(persisted_tamper.verification_hash,),
        )
        tampered_pipeline = DraftV2PipelineResult(
            draft_v2=pipeline.draft_v2,
            generation_result=pipeline.generation_result,
            ordered_claims=pipeline.ordered_claims,
            ordered_claim_verifications=(persisted_tamper,),
            verification_summary=tampered_summary,
            replayed=pipeline.replayed,
            persisted=pipeline.persisted,
        )
        with self.assertRaises(Step26BoundaryError):
            FinalAnswerRequest(
                route=self.values["route"],
                policy_result=final_policy_result(
                    self.values["route"],
                    self.values["temporal"].evidence_status,
                ),
                step20_outcomes=(self.values["outcome"],),
                temporal_result=self.values["temporal"],
                draft_v1=self.values["draft"],
                correction_packet=self.values["packet"],
                integrity_receipt=self.values["integrity_receipt"],
                step25_result=tampered_pipeline,
            )

    def test_step26_reconstructs_corrected_proof_against_packet(self) -> None:
        pipeline = self.values["pipeline"]
        verified = pipeline.ordered_claim_verifications[0]
        proof = verified.corrected_evidence_proof
        self.assertIsNotNone(proof)
        assert proof is not None
        altered_link = replace(
            proof.original_evidence_link,
            temporal_assessment_hash="f" * 64,
        )
        altered_proof = replace(proof, original_evidence_link=altered_link)
        altered_signal = CorrectedEvidenceVerifierSignal(
            request_hash=altered_proof.request_hash,
            verdict=CorrectedEvidenceVerdict.SUPPORTS,
            evidence_reference_ids=(altered_proof.packet_citation_id,),
            proof=altered_proof,
        )
        altered_verification = replace(
            verified,
            corrected_evidence_signal_hash=altered_signal.signal_hash,
            corrected_evidence_proof=altered_proof,
            corrected_evidence_proof_hash=altered_proof.proof_hash,
        )
        altered_summary = replace(
            pipeline.verification_summary,
            ordered_claim_verification_hashes=(
                altered_verification.verification_hash,
            ),
        )
        altered_pipeline = DraftV2PipelineResult(
            draft_v2=pipeline.draft_v2,
            generation_result=pipeline.generation_result,
            ordered_claims=pipeline.ordered_claims,
            ordered_claim_verifications=(altered_verification,),
            verification_summary=altered_summary,
            replayed=pipeline.replayed,
            persisted=pipeline.persisted,
        )
        with self.assertRaises(Step26BoundaryError):
            FinalAnswerRequest(
                route=self.values["route"],
                policy_result=final_policy_result(
                    self.values["route"],
                    self.values["temporal"].evidence_status,
                ),
                step20_outcomes=(self.values["outcome"],),
                temporal_result=self.values["temporal"],
                draft_v1=self.values["draft"],
                correction_packet=self.values["packet"],
                integrity_receipt=self.values["integrity_receipt"],
                step25_result=altered_pipeline,
            )

    def test_verified_answer_and_hash_only_before_after_trace(self) -> None:
        final = self.values["final"]
        self.assertIs(final.output_status, FinalOutputStatus.VERIFIED_ANSWER)
        trace = build_before_after_trace(
            self.values["case"],
            self.values["draft"],
            self.values["packet"],
            self.values["pipeline"],
            final,
            self.values["context"],
            self.values["evidence_provider"].input_receipts[0],
        )
        self.assertTrue(trace.defect_claim_ids)
        self.assertTrue(trace.evidence_reference_hashes)
        self.assertEqual(
            trace.augmented_provider_input_receipt_hash,
            self.values["evidence_provider"].input_receipts[0].receipt_hash,
        )
        self.assertEqual(
            trace.draft_v2_target_projection_hash,
            self.values["target_projection"].projection_hash,
        )
        rendered = json.dumps(
            {
                "question_digest": trace.question_digest,
                "draft_v1_hash": trace.draft_v1_hash,
                "draft_v2_hash": trace.draft_v2_hash,
                "verified_answer_hash": trace.verified_answer_hash,
            }
        )
        self.assertNotIn(self.values["draft"].draft_text, rendered)
        self.assertNotIn(final.verified_answer.answer_text, rendered)

    def test_trace_rejects_cross_result_receipt_and_rehashed_proof_mixing(self) -> None:
        pipeline = self.values["pipeline"]
        final = self.values["final"]
        receipt = self.values["evidence_provider"].input_receipts[0]
        verified = pipeline.ordered_claim_verifications[0]

        mixed_verification = replace(
            verified,
            limitations=verified.limitations + ("cross-result-mix",),
        )
        mixed_summary = replace(
            pipeline.verification_summary,
            ordered_claim_verification_hashes=(mixed_verification.verification_hash,),
        )
        mixed_pipeline = DraftV2PipelineResult(
            draft_v2=pipeline.draft_v2,
            generation_result=pipeline.generation_result,
            ordered_claims=pipeline.ordered_claims,
            ordered_claim_verifications=(mixed_verification,),
            verification_summary=mixed_summary,
            replayed=pipeline.replayed,
            persisted=pipeline.persisted,
        )
        with self.assertRaises(IntegrityError):
            build_before_after_trace(
                self.values["case"],
                self.values["draft"],
                self.values["packet"],
                mixed_pipeline,
                final,
                self.values["context"],
                receipt,
            )

        forged_receipt = replace(receipt, provider_response_hash="f" * 64)
        with self.assertRaises(IntegrityError):
            build_before_after_trace(
                self.values["case"],
                self.values["draft"],
                self.values["packet"],
                pipeline,
                final,
                self.values["context"],
                forged_receipt,
            )

        proof = verified.corrected_evidence_proof
        assert proof is not None
        forged_proof = replace(proof, evidence_context_hash="f" * 64)
        forged_signal = CorrectedEvidenceVerifierSignal(
            request_hash=forged_proof.request_hash,
            verdict=CorrectedEvidenceVerdict.SUPPORTS,
            evidence_reference_ids=(forged_proof.packet_citation_id,),
            proof=forged_proof,
        )
        forged_verification = replace(
            verified,
            corrected_evidence_signal_hash=forged_signal.signal_hash,
            corrected_evidence_proof=forged_proof,
            corrected_evidence_proof_hash=forged_proof.proof_hash,
        )
        forged_summary = replace(
            pipeline.verification_summary,
            ordered_claim_verification_hashes=(forged_verification.verification_hash,),
        )
        forged_pipeline = DraftV2PipelineResult(
            draft_v2=pipeline.draft_v2,
            generation_result=pipeline.generation_result,
            ordered_claims=pipeline.ordered_claims,
            ordered_claim_verifications=(forged_verification,),
            verification_summary=forged_summary,
            replayed=pipeline.replayed,
            persisted=pipeline.persisted,
        )
        answer = final.verified_answer
        assert answer is not None
        forged_reference = replace(
            answer.claim_verification_references[0],
            verification_hash=forged_verification.verification_hash,
        )
        forged_answer = replace(
            answer,
            verification_summary_hash=forged_summary.summary_hash,
            claim_verification_references=(forged_reference,),
        )
        forged_final = replace(final, verified_answer=forged_answer)
        with self.assertRaises(IntegrityError):
            build_before_after_trace(
                self.values["case"],
                self.values["draft"],
                self.values["packet"],
                forged_pipeline,
                forged_final,
                self.values["context"],
                receipt,
            )


class PersonalMemoryAndSafetyIntegrationTests(unittest.TestCase):
    def test_same_active_patch_is_reused_by_two_models_and_denied_to_other_owner(self) -> None:
        _value, slot, _committed, active, first, second = active_fixture()
        patch_hashes = []
        for binding in (first, second):
            request, selected_route, temporal = request_for(binding)
            retrieval, _repository, patcher = service(slot, (candidate(),))
            try:
                result, context = retrieval.retrieve(
                    request,
                    route=selected_route,
                    temporal_result=temporal,
                )
            finally:
                patcher.stop()
            self.assertEqual(len(result.eligible_patches), 1)
            self.assertFalse(context.canonical_evidence_authority)
            patch_hashes.append(result.eligible_patches[0].patch_hash)
        self.assertEqual(patch_hashes, [active.committed_patch.patch_hash] * 2)

        other_request, other_route, other_temporal = request_for(first, user_id="other-owner")
        retrieval, _repository, patcher = service(slot, (candidate(),))
        try:
            other_result, _ = retrieval.retrieve(
                other_request,
                route=other_route,
                temporal_result=other_temporal,
            )
        finally:
            patcher.stop()
        self.assertEqual(other_result.eligible_patches, ())

    def test_canonical_conflict_suppresses_private_patch(self) -> None:
        _value, slot, _committed, _active, first, _second = active_fixture()
        request, selected_route, temporal = request_for(first, conflict=True)
        retrieval, _repository, patcher = service(slot, (candidate(),))
        try:
            result, _ = retrieval.retrieve(
                request,
                route=selected_route,
                temporal_result=temporal,
            )
        finally:
            patcher.stop()
        self.assertEqual(result.eligible_patches, ())
        self.assertTrue(
            any(
                Step31ReasonCode.PATCH_SUPPRESSED_BY_CANONICAL_EVIDENCE
                in item.reason_codes
                for item in result.excluded_assessments
            )
        )

    def test_audit_review_ui_and_recovery_contracts_remain_connected(self) -> None:
        entries = chain(4)
        verification = verify_audit_chain(entries[0].envelope.chain_id, entries)
        self.assertTrue(verification.verified)

        request, authenticator = hat_lineage(
            values=(metadata(version="conflict-a"), metadata(version="conflict-b")),
            contents=("Widerspruch A", "Widerspruch B"),
        )
        review = VerifiedAnswerService(authenticator).finalize(request)
        self.assertIs(review.output_status, FinalOutputStatus.HUMAN_REVIEW_REQUIRED)
        self.assertIsNotNone(review.human_review)

        dashboard = FakeBackend().dashboard(OWNER_A)
        self.assertEqual(dashboard.pending_approval_count, 1)
        self.assertEqual(dashboard.active_patch_count, 1)
        self.assertEqual(len(dashboard.recent_audit_events), 1)

        recovery = FailureRecoveryCaseResult.build(
            case_id="step38-pm-ack-lost",
            failure_domain=FailureDomain.PERSONAL_MEMORY_COMMIT,
            failure_point=FailurePoint.PM_AFTER_COMMIT_ACK_LOST,
            subject_hash=canonical_sha256("step38-pm-subject"),
            attempt_count=2,
            recovery_status=RecoveryStatus.RECOVERED_BY_IDEMPOTENT_REPLAY,
            final_semantic_state="ONE_COMMITTED_PATCH",
            reason_codes=("NO_AUTHORITY_ESCALATION", "NO_DUPLICATE_SIDE_EFFECT"),
        )
        self.assertEqual(recovery.duplicate_side_effect_count, 0)
        self.assertEqual(recovery.authority_violation_count, 0)


class CommittedLiveEvidenceTests(unittest.TestCase):
    def test_live_evidence_is_exact_canonical_secret_free_closure_artifact(self) -> None:
        raw = LIVE_EVIDENCE.read_bytes()
        evidence = json.loads(raw)

        self.assertEqual(
            hashlib.sha256(raw).hexdigest(),
            LIVE_EVIDENCE_FILE_SHA256,
        )
        self.assertEqual(raw, (canonical_json(evidence) + "\n").encode("utf-8"))
        self.assertEqual(
            evidence["validation_digest"],
            LIVE_EVIDENCE_VALIDATION_DIGEST,
        )
        self.assertEqual(
            canonical_sha256(evidence, exclude_fields=("validation_digest",)),
            LIVE_EVIDENCE_VALIDATION_DIGEST,
        )
        self.assertEqual(evidence["status"], "PASS_LIVE_COHERENT_LINEAGE")
        self.assertTrue(evidence["closure_eligible"])
        self.assertEqual(
            evidence["validation_mode"],
            "LIVE_ONE_OWNED_SAME_DATABASE_LINEAGE",
        )
        self.assertFalse(evidence["coherent_runtime"]["step39_started"])
        self.assertEqual(evidence["step39_boundary"]["status"], "PASS")
        self.assertEqual(
            evidence["step39_boundary"]["unexpected_production_bridge_hits"],
            0,
        )
        assert_secret_free(
            evidence,
            surface="STEP38_COMMITTED_LIVE_EVIDENCE",
            reject_machine_paths=True,
        )

    def test_demo_trace_is_exactly_twelve_hash_only_canonical_stages(self) -> None:
        live_raw = LIVE_EVIDENCE.read_bytes()
        live = json.loads(live_raw)
        trace_raw = DEMO_TRACE.read_bytes()
        trace = json.loads(trace_raw)

        self.assertEqual(trace_raw, (canonical_json(trace) + "\n").encode("utf-8"))
        self.assertEqual(trace["status"], "PASS_LIVE_COHERENT_LINEAGE")
        self.assertTrue(trace["closure_eligible"])
        self.assertEqual(
            trace["source_validation_digest"],
            live["validation_digest"],
        )
        self.assertEqual(
            trace["source_artifact_sha256"],
            hashlib.sha256(live_raw).hexdigest(),
        )

        stages = trace["stages"]
        self.assertEqual(len(stages), 12)
        self.assertEqual(
            tuple(stage["sequence"] for stage in stages),
            tuple(range(1, 13)),
        )
        self.assertEqual(
            tuple(stage["stage_id"] for stage in stages),
            DEMO_TRACE_STAGE_IDS,
        )
        for stage in stages:
            with self.subTest(stage_id=stage["stage_id"]):
                self.assertEqual(
                    stage["stage_digest"],
                    canonical_sha256(stage, exclude_fields=("stage_digest",)),
                )
                for digest in stage["evidence"].values():
                    self.assertEqual(len(digest), 64)
                    self.assertTrue(
                        all(character in "0123456789abcdef" for character in digest)
                    )

        self.assertEqual(trace["root_digest"], DEMO_TRACE_ROOT_DIGEST)
        self.assertEqual(
            canonical_sha256(trace, exclude_fields=("root_digest",)),
            DEMO_TRACE_ROOT_DIGEST,
        )
        self.assertEqual(trace["step39_boundary"]["status"], "PASS")
        self.assertFalse(trace["step39_boundary"]["step39_started"])
        self.assertEqual(
            trace["step39_boundary"]["proof_hash"],
            live["step39_boundary"]["proof_hash"],
        )

        forbidden_raw_fields = {
            "answer_text",
            "draft_text",
            "draft_v1_text",
            "draft_v2_text",
            "personal_memory_text",
            "provider_response_content",
            "question",
            "question_text",
            "raw_content",
            "source_chunk_text",
        }

        def nested_keys(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    yield key
                    yield from nested_keys(item)
            elif isinstance(value, list):
                for item in value:
                    yield from nested_keys(item)

        self.assertTrue(forbidden_raw_fields.isdisjoint(nested_keys(trace)))
        assert_secret_free(
            trace,
            surface="STEP38_COMMITTED_DEMO_TRACE",
            reject_machine_paths=True,
        )


if __name__ == "__main__":
    unittest.main()
