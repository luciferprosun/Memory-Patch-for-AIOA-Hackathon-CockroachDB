"""Step 25 Draft V2 generation and layered verifier tests."""

from __future__ import annotations

import ast
import copy
import json
import unittest
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime

from tests._support import REPOSITORY_ROOT

from aioa_memory_kernel.contracts.exceptions import ContractValidationError, IntegrityError
from aioa_memory_kernel.corrections import (
    HmacSha256PacketAuthenticator,
    build_correction_packet,
)
from aioa_memory_kernel.modeling import (
    ModelAdapterError,
    ModelReasonCode,
    ProviderResponse,
)
from aioa_memory_kernel.persistence import (
    AccessMode,
    DraftV2Record,
    ImmutableRecordConflictError,
    RequestContext,
    SerializableTransactionRunner,
    TransactionBoundaryViolation,
)
from aioa_memory_kernel.verification import (
    CheckResult,
    CorrectionComplianceStatus,
    DeterministicFakeSemanticVerifier,
    DraftV2LayeredVerifier,
    DraftV2Service,
    EvidenceBindingResult,
    FinalStep25ClaimVerdict,
    ProhibitedClaimPresence,
    ProviderSemanticClaimVerifier,
    SemanticCandidateVerdict,
    Step25BoundaryError,
    VerificationSummaryStatus,
    build_draft_v2_provider_request,
    build_semantic_verifier_request,
    decode_draft_v2_reference,
    encode_draft_v2_reference,
    extract_draft_v2_claims,
    prepare_draft_v2_generation_request,
    verify_draft_v2_hash,
    verify_verification_summary_hash,
)
from tests.test_cockroachdb_persistence import FakeConnection
from tests.test_step23_claim_evidence_binding import pipeline
from tests.test_step24_correction_packet import (
    TEST_KEY,
    authority_packet,
    conflict_packet,
    future_packet,
)


ROOT = REPOSITORY_ROOT
STEP25_ROOT = ROOT / "src/aioa_memory_kernel/verification"
NOW = datetime(2026, 8, 9, 8, 0, tzinfo=UTC)


class FixedClock:
    def now(self):
        return NOW


class FakeProvider:
    def __init__(self, request, outcomes):
        self.identity = request.provider_identity
        self.outcomes = list(outcomes)
        self.requests = []

    def provider_identity(self):
        return self.identity

    def generate(self, request, timeout_policy):
        self.requests.append((request, timeout_policy))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class MemoryStore:
    def __init__(self):
        self.values = {}
        self.put_count = 0

    def load(self, *, tenant_id, user_id, draft_id):
        return self.values.get((tenant_id, user_id, draft_id))

    def put(self, draft):
        self.put_count += 1
        key = (draft.tenant_id, draft.user_id, draft.draft_v2_id)
        previous = self.values.get(key)
        if previous is not None and previous.draft_v2_hash != draft.draft_v2_hash:
            raise ImmutableRecordConflictError(sanitized_code="DRAFT_IMMUTABLE_CONFLICT")
        self.values[key] = draft
        return draft


def main_inputs():
    values = pipeline(
        "Die Vorschrift ist aufgehoben. Das Gesetz gilt. Eine unbelegte Aussage. Hallo!",
        contents=(
            "Die Vorschrift ist aufgehoben.",
            "Das Gesetz gilt nicht.",
            "Anderer Inhalt.",
        ),
    )
    packet = build_correction_packet(values[-1])
    return values[2], packet


def supported_inputs():
    values = pipeline(
        "Die Vorschrift ist aufgehoben.",
        contents=("Die Vorschrift ist aufgehoben.",),
    )
    return values[2], build_correction_packet(values[-1])


def authentication(draft, packet):
    authenticator = HmacSha256PacketAuthenticator(
        key_id="step25-test-key",
        key_material=TEST_KEY,
    )
    receipt = authenticator.authenticate(packet)
    request = prepare_draft_v2_generation_request(
        draft,
        packet,
        receipt,
        authenticator,
    )
    return authenticator, receipt, request


def response_for(request, text, *, request_id="step25-provider-request"):
    return ProviderResponse(
        provider_identity_digest=request.provider_identity.identity_digest,
        model_id=request.provider_identity.model_id,
        model_version=request.provider_identity.model_revision_or_declared_version,
        provider_request_id=request_id,
        finish_reason="stop",
        response_content=text,
        usage_metadata={"prompt_tokens": 20, "completion_tokens": 12, "total_tokens": 32},
        latency_milliseconds=2,
    )


def run_v2(
    draft,
    packet,
    text,
    *,
    semantic=None,
    store=None,
    outcomes=None,
):
    authenticator, _, request = authentication(draft, packet)
    provider = FakeProvider(request, outcomes or [response_for(request, text)])
    verifier = DraftV2LayeredVerifier(
        semantic or DeterministicFakeSemanticVerifier()
    )
    result = DraftV2Service(
        provider,
        authenticator,
        verifier=verifier,
        store=store,
        clock=FixedClock(),
        sleep=lambda _: None,
    ).generate_and_verify(request)
    return result, provider, request, authenticator


def citation_for(packet, relation="SUPPORTS"):
    return next(item for item in packet.ordered_citations if item.relation.value == relation)


class ContractAndGenerationTests(unittest.TestCase):
    def test_verified_packet_generates_exact_immutable_hash_bound_draft_v2(self) -> None:
        draft, packet = supported_inputs()
        citation = citation_for(packet)
        text = f"Die Vorschrift ist aufgehoben [citation:{citation.citation_id}]."
        result, provider, request, _ = run_v2(draft, packet, text)
        self.assertEqual(result.draft_v2.draft_text, text)
        self.assertEqual(result.draft_v2.draft_text_sha256, response_for(request, text).response_content_sha256)
        verify_draft_v2_hash(result.draft_v2)
        verify_verification_summary_hash(result.verification_summary)
        self.assertEqual(len(provider.requests), 1)
        with self.assertRaises(FrozenInstanceError):
            result.draft_v2.draft_text = "changed"  # type: ignore[misc]

    def test_request_prompt_and_hashes_are_deterministic(self) -> None:
        draft, packet = supported_inputs()
        authenticator, receipt, first = authentication(draft, packet)
        second = prepare_draft_v2_generation_request(draft, packet, receipt, authenticator)
        self.assertEqual(first.request_hash, second.request_hash)
        projected = build_draft_v2_provider_request(first)
        body = json.loads(projected.user_content)
        self.assertEqual(body["draft_v1"]["exact_text"], draft.draft_text)
        self.assertEqual(body["correction_packet"]["packet_hash"], packet.packet_hash)
        self.assertTrue(first.provider_identity.tooling_disabled)
        self.assertTrue(first.provider_identity.web_browsing_disabled)
        self.assertTrue(first.provider_identity.code_execution_disabled)

    def test_tampered_packet_and_wrong_hmac_fail_before_model_call(self) -> None:
        draft, packet = supported_inputs()
        authenticator, receipt, request = authentication(draft, packet)
        bad_packet = copy.copy(packet)
        object.__setattr__(bad_packet, "packet_hash", "0" * 64)
        with self.assertRaises(Step25BoundaryError):
            prepare_draft_v2_generation_request(draft, bad_packet, receipt, authenticator)
        bad_receipt = replace(receipt, authenticator="0" * 64)
        with self.assertRaises(Step25BoundaryError):
            prepare_draft_v2_generation_request(draft, packet, bad_receipt, authenticator)
        provider = FakeProvider(request, [response_for(request, "never")])
        tampered_request = copy.copy(request)
        object.__setattr__(tampered_request.integrity_receipt, "authenticator", "f" * 64)
        with self.assertRaises(Step25BoundaryError):
            DraftV2Service(provider, authenticator).generate_and_verify(tampered_request)
        self.assertEqual(provider.requests, [])

    def test_lineage_cross_tenant_user_route_and_draft_v1_mismatches_fail(self) -> None:
        draft, packet = supported_inputs()
        authenticator, receipt, request = authentication(draft, packet)
        for field, value in (
            ("tenant_id", "tenant-other"),
            ("user_id", "user-other"),
            ("route_hash", "1" * 64),
            ("draft_v1_hash", "2" * 64),
        ):
            with self.subTest(field=field):
                with self.assertRaises(Step25BoundaryError):
                    replace(request, **{field: value})
        self.assertEqual(receipt.packet_hash, packet.packet_hash)
        self.assertEqual(authenticator.key_id, "step25-test-key")

    def test_retry_is_bounded_and_second_response_is_not_merged(self) -> None:
        draft, packet = supported_inputs()
        _, _, request = authentication(draft, packet)
        transient = ModelAdapterError(
            ModelReasonCode.MODEL_TRANSIENT_FAILURE,
            retryable=True,
        )
        result, provider, _, _ = run_v2(
            draft,
            packet,
            "Zweiter Entwurf.",
            outcomes=[transient, response_for(request, "Zweiter Entwurf.")],
        )
        self.assertEqual(len(provider.requests), 2)
        self.assertEqual(result.draft_v2.draft_text, "Zweiter Entwurf.")
        self.assertEqual(result.draft_v2.attempt_count, 2)
        self.assertEqual(
            result.generation_result.failed_attempt_reason_codes,
            (ModelReasonCode.MODEL_TRANSIENT_FAILURE,),
        )

    def test_non_retryable_failure_is_not_retried(self) -> None:
        draft, packet = supported_inputs()
        authenticator, _, request = authentication(draft, packet)
        provider = FakeProvider(
            request,
            [ModelAdapterError(ModelReasonCode.MODEL_AUTHENTICATION_FAILED)],
        )
        with self.assertRaises(ModelAdapterError):
            DraftV2Service(provider, authenticator).generate_and_verify(request)
        self.assertEqual(len(provider.requests), 1)

    def test_model_call_is_forbidden_inside_database_transaction(self) -> None:
        draft, packet = supported_inputs()
        authenticator, _, request = authentication(draft, packet)
        provider = FakeProvider(request, [response_for(request, "x")])
        runner = SerializableTransactionRunner(lambda: FakeConnection([]))
        context = RequestContext(draft.tenant_id, draft.user_id, AccessMode.USER_PRIVATE)
        with self.assertRaises(TransactionBoundaryViolation):
            runner.run(
                context,
                lambda _transaction: DraftV2Service(provider, authenticator).generate_and_verify(request),
                operation_kind="step25-negative",
            )
        self.assertEqual(provider.requests, [])

    def test_exact_store_replay_is_idempotent_and_isolated(self) -> None:
        draft, packet = supported_inputs()
        citation = citation_for(packet)
        text = f"Die Vorschrift ist aufgehoben [citation:{citation.citation_id}]."
        store = MemoryStore()
        first, provider, request, authenticator = run_v2(draft, packet, text, store=store)
        second_provider = FakeProvider(request, [response_for(request, "must-not-run")])
        second = DraftV2Service(
            second_provider,
            authenticator,
            verifier=DraftV2LayeredVerifier(DeterministicFakeSemanticVerifier()),
            store=store,
            clock=FixedClock(),
        ).generate_and_verify(request)
        self.assertEqual(first.draft_v2.draft_v2_hash, second.draft_v2.draft_v2_hash)
        self.assertTrue(second.replayed)
        self.assertEqual(second_provider.requests, [])
        self.assertIsNone(store.load(tenant_id="other", user_id=draft.user_id, draft_id=first.draft_v2.draft_v2_id))
        self.assertIsNone(store.load(tenant_id=draft.tenant_id, user_id="other", draft_id=first.draft_v2.draft_v2_id))
        self.assertEqual(len(provider.requests), 1)

    def test_stage_two_reference_round_trip_and_record_contract(self) -> None:
        draft, packet = supported_inputs()
        citation = citation_for(packet)
        result = run_v2(draft, packet, f"Die Vorschrift ist aufgehoben [citation:{citation.citation_id}].")[0]
        decoded = decode_draft_v2_reference(encode_draft_v2_reference(result.draft_v2))
        self.assertEqual(decoded, result.draft_v2)
        record = DraftV2Record(
            tenant_id=decoded.tenant_id,
            draft_id=decoded.draft_v2_id,
            kernel_run_id=decoded.request_id,
            draft_stage=2,
            content_sha256=decoded.draft_text_sha256,
            immutable_content_reference=encode_draft_v2_reference(decoded),
            created_at=decoded.created_at,
        )
        self.assertEqual(record.draft_stage, 2)
        with self.assertRaisesRegex(Exception, "Draft V2 only"):
            replace(record, draft_stage=1)


class ExtractionAndDeterministicLayerTests(unittest.TestCase):
    def test_claims_reuse_exact_unicode_spans_and_alignment(self) -> None:
        draft, packet = supported_inputs()
        citation = citation_for(packet)
        text = f"Die Vorschrift ist aufgehoben [citation:{citation.citation_id}]. Grüße äöü!"
        result = run_v2(draft, packet, text)[0]
        for claim in result.ordered_claims:
            self.assertEqual(
                result.draft_v2.draft_text[claim.start_offset : claim.end_offset],
                claim.exact_claim_text,
            )
        self.assertTrue(result.ordered_claims[0].aligned_draft_v1_claim_ids)
        self.assertNotEqual(result.ordered_claims[0].claim_id, packet.ordered_claims[0].claim_id)

    def test_required_remove_qualify_and_prohibition_compliance(self) -> None:
        draft, packet = main_inputs()
        support = citation_for(packet)
        text = (
            f"Die Vorschrift ist aufgehoben [citation:{support.citation_id}]. "
            "Eine unbelegte Aussage ist nicht verifiziert. Hallo!"
        )
        summary = run_v2(draft, packet, text)[0].verification_summary
        self.assertTrue(all(
            item.status is CorrectionComplianceStatus.SATISFIED
            for item in summary.required_correction_results
        ))
        self.assertTrue(all(
            item.presence is ProhibitedClaimPresence.NOT_PRESENT
            for item in summary.prohibited_claim_results
        ))

    def test_missing_correction_and_exact_prohibited_repeat_fail(self) -> None:
        draft, packet = main_inputs()
        result = run_v2(draft, packet, draft.draft_text)[0]
        self.assertGreater(result.verification_summary.required_corrections_unsatisfied, 0)
        self.assertGreater(result.verification_summary.prohibited_claim_violations, 0)
        self.assertIs(result.verification_summary.summary_status, VerificationSummaryStatus.FAILED)

    def test_unknown_citation_is_invalid_and_fails_summary(self) -> None:
        draft, packet = supported_inputs()
        result = run_v2(draft, packet, "Neue Aussage [citation:unknown-citation].")[0]
        verification = result.ordered_claim_verifications[0]
        self.assertIs(verification.citation_result, CheckResult.FAIL)
        self.assertIs(verification.final_step25_verdict, FinalStep25ClaimVerdict.INVALID)
        self.assertIs(result.verification_summary.summary_status, VerificationSummaryStatus.FAILED)

    def test_exact_identifier_and_known_date_pass_unknown_date_fails(self) -> None:
        values = pipeline(
            "§ 25 gilt ab 2030-01-01.",
            contents=("§ 25 gilt ab 2030-01-01.",),
        )
        draft, packet = values[2], build_correction_packet(values[-1])
        citation = citation_for(packet)
        good = run_v2(draft, packet, f"§ 25 gilt ab 2030-01-01 [citation:{citation.citation_id}].")[0]
        bad = run_v2(draft, packet, f"§ 25 gilt ab 2031-01-01 [citation:{citation.citation_id}].")[0]
        self.assertIs(good.ordered_claim_verifications[0].deterministic_fact_result, CheckResult.PASS)
        self.assertIs(bad.ordered_claim_verifications[0].deterministic_fact_result, CheckResult.FAIL)
        self.assertIs(bad.ordered_claim_verifications[0].final_step25_verdict, FinalStep25ClaimVerdict.VERIFIED_REFUTED)

    def test_future_effective_claim_fails_temporal_layer_despite_semantic_support(self) -> None:
        snapshot, packet = future_packet()
        draft = pipeline(
            "Die Vorschrift ist aufgehoben.",
            metadatas=(),
        )[2]
        # Recreate the exact Draft V1 bound by future_packet rather than use the unrelated one.
        from tests.test_step23_claim_evidence_binding import draft_for
        from tests.test_step21_temporal_resolution import bundle_outcome, metadata, resolve
        outcome = bundle_outcome(
            metadata(version="future-v1", effective_from="2030-01-01"),
            contents=("Die Vorschrift ist aufgehoben.",),
        )
        draft = draft_for(resolve(outcome), "Die Vorschrift ist aufgehoben.")
        citation = packet.ordered_citations[0]
        semantic = DeterministicFakeSemanticVerifier(default=SemanticCandidateVerdict.SUPPORTS)
        result = run_v2(
            draft,
            packet,
            f"Die Vorschrift ist aufgehoben [citation:{citation.citation_id}].",
            semantic=semantic,
        )[0]
        verification = result.ordered_claim_verifications[0]
        self.assertIs(verification.temporal_result, CheckResult.FAIL)
        self.assertIs(verification.final_step25_verdict, FinalStep25ClaimVerdict.VERIFIED_REFUTED)

    def test_false_official_source_claim_fails_source_layer(self) -> None:
        snapshot, packet = authority_packet()
        from tests.test_step23_claim_evidence_binding import pipeline as step23_pipeline
        draft = step23_pipeline(
            "Laut offizieller Quelle gilt das Gesetz.",
            contents=("Laut offizieller Quelle gilt das Gesetz.",),
        )[2]
        citation = packet.ordered_citations[0]
        result = run_v2(
            draft,
            packet,
            f"Laut offizieller Quelle gilt das Gesetz [citation:{citation.citation_id}].",
            semantic=DeterministicFakeSemanticVerifier(default=SemanticCandidateVerdict.SUPPORTS),
        )[0]
        self.assertIs(result.ordered_claim_verifications[0].source_result, CheckResult.FAIL)
        self.assertIs(result.ordered_claim_verifications[0].final_step25_verdict, FinalStep25ClaimVerdict.VERIFIED_REFUTED)

    def test_material_conflict_is_preserved_not_rank_resolved(self) -> None:
        _, packet = conflict_packet()
        values = pipeline(
            "Die Vorschrift ist aufgehoben.",
            contents=("Die Vorschrift ist aufgehoben.", "Die Vorschrift ist nicht aufgehoben."),
            metadatas=None,
        )
        # conflict_packet has exact deterministic identities; recover its matching draft separately.
        from tests.test_step24_correction_packet import conflict_packet as make_conflict
        _, packet = make_conflict()
        from tests.test_step21_temporal_resolution import metadata
        common = {"document": "step24-conflict-document", "provision": "§-24"}
        match = pipeline(
            "Die Vorschrift ist aufgehoben.",
            contents=("Die Vorschrift ist aufgehoben.", "Die Vorschrift ist nicht aufgehoben."),
            metadatas=(
                metadata(version="conflict-a", **common),
                metadata(version="conflict-b", **common),
            ),
        )
        draft = match[2]
        citations = " ".join(f"[citation:{item.citation_id}]" for item in packet.ordered_citations)
        text = f"Nach den vorliegenden Quellen ist unklar, ob die Vorschrift aufgehoben ist {citations}."
        result = run_v2(draft, packet, text)[0]
        self.assertIn(
            EvidenceBindingResult.CONFLICTING,
            {item.evidence_binding_result for item in result.ordered_claim_verifications},
        )
        self.assertIs(result.verification_summary.summary_status, VerificationSummaryStatus.CONFLICTING)


class SemanticVerifierAndAggregationTests(unittest.TestCase):
    def test_structured_support_refute_uncertain_signals_are_candidates(self) -> None:
        draft, packet = supported_inputs()
        citation = citation_for(packet)
        text = f"Die Regel wurde aufgehoben [citation:{citation.citation_id}]."
        for signal, expected in (
            (SemanticCandidateVerdict.SUPPORTS, FinalStep25ClaimVerdict.VERIFIED_SUPPORTED),
            (SemanticCandidateVerdict.REFUTES, FinalStep25ClaimVerdict.VERIFIED_REFUTED),
            (SemanticCandidateVerdict.UNCERTAIN, FinalStep25ClaimVerdict.UNVERIFIED),
        ):
            with self.subTest(signal=signal):
                semantic = DeterministicFakeSemanticVerifier(default=signal)
                result = run_v2(draft, packet, text, semantic=semantic)[0]
                self.assertIs(result.ordered_claim_verifications[0].final_step25_verdict, expected)

    def test_provider_semantic_verifier_requires_strict_json_and_known_citations(self) -> None:
        draft, packet = supported_inputs()
        base = run_v2(draft, packet, "Neue Behauptung.")[0]
        claim = base.ordered_claims[0]
        citation = citation_for(packet)
        request = build_semantic_verifier_request(
            claim_id=claim.claim_id,
            draft_v2_hash=claim.draft_v2_hash,
            correction_packet_hash=packet.packet_hash,
            claim_text=claim.exact_claim_text,
            allowed_citation_ids=(citation.citation_id,),
            evidence_context=("bounded evidence identity",),
            deterministic_context_digest="a" * 64,
        )
        valid_text = json.dumps({
            "candidate_verdict": "SUPPORTS",
            "evidence_reference_ids": [citation.citation_id],
        })
        provider = FakeProvider(request, [response_for(request, valid_text)])
        signal = ProviderSemanticClaimVerifier(provider, sleep=lambda _: None).verify(request)
        self.assertIs(signal.candidate_verdict, SemanticCandidateVerdict.SUPPORTS)
        malformed = FakeProvider(request, [response_for(request, "not-json")])
        bad = ProviderSemanticClaimVerifier(malformed, sleep=lambda _: None).verify(request)
        self.assertIs(bad.candidate_verdict, SemanticCandidateVerdict.INVALID)

    def test_verifier_cannot_invent_citation_or_override_deterministic_failure(self) -> None:
        draft, packet = supported_inputs()
        citation = citation_for(packet)
        text = f"Die Vorschrift gilt ab 2099-01-01 [citation:{citation.citation_id}]."
        semantic = DeterministicFakeSemanticVerifier(default=SemanticCandidateVerdict.SUPPORTS)
        result = run_v2(draft, packet, text, semantic=semantic)[0]
        verification = result.ordered_claim_verifications[0]
        self.assertIs(verification.deterministic_fact_result, CheckResult.FAIL)
        self.assertIs(verification.final_step25_verdict, FinalStep25ClaimVerdict.VERIFIED_REFUTED)

    def test_unavailable_semantic_verifier_never_promotes_to_verified(self) -> None:
        draft, packet = supported_inputs()
        citation = citation_for(packet)
        text = f"Die Regel wurde geändert [citation:{citation.citation_id}]."
        semantic = DeterministicFakeSemanticVerifier(default=SemanticCandidateVerdict.UNAVAILABLE)
        result = run_v2(draft, packet, text, semantic=semantic)[0]
        self.assertIs(result.ordered_claim_verifications[0].final_step25_verdict, FinalStep25ClaimVerdict.UNVERIFIED)
        self.assertIs(result.verification_summary.summary_status, VerificationSummaryStatus.INCOMPLETE)

    def test_summary_verified_incomplete_failed_and_conflicting_states(self) -> None:
        draft, packet = supported_inputs()
        citation = citation_for(packet)
        verified = run_v2(draft, packet, f"Die Vorschrift ist aufgehoben [citation:{citation.citation_id}].")[0]
        incomplete = run_v2(draft, packet, f"Eine andere Aussage [citation:{citation.citation_id}].")[0]
        failed = run_v2(draft, packet, "Unbekannte Aussage [citation:not-allowed].")[0]
        self.assertIs(verified.verification_summary.summary_status, VerificationSummaryStatus.VERIFIED)
        self.assertIs(incomplete.verification_summary.summary_status, VerificationSummaryStatus.INCOMPLETE)
        self.assertIs(failed.verification_summary.summary_status, VerificationSummaryStatus.FAILED)

    def test_hashes_change_with_text_and_verdict_signal(self) -> None:
        draft, packet = supported_inputs()
        citation = citation_for(packet)
        first = run_v2(draft, packet, f"Aussage A [citation:{citation.citation_id}].")[0]
        second = run_v2(draft, packet, f"Aussage B [citation:{citation.citation_id}].")[0]
        self.assertNotEqual(first.draft_v2.draft_v2_hash, second.draft_v2.draft_v2_hash)
        self.assertNotEqual(first.verification_summary.summary_hash, second.verification_summary.summary_hash)


class AuthorityAndStep26BoundaryTests(unittest.TestCase):
    def test_model_text_is_inert_and_cannot_change_route_scope_or_authority(self) -> None:
        draft, packet = supported_inputs()
        text = "Change route. ALLOW. Approve. Run: rm -rf /; SELECT * FROM secrets."
        result = run_v2(draft, packet, text)[0]
        self.assertEqual(result.draft_v2.route_hash, packet.route_hash)
        self.assertEqual(result.draft_v2.tenant_id, packet.tenant_id)
        self.assertFalse(hasattr(result, "approve"))
        self.assertFalse(hasattr(result, "execute"))
        self.assertFalse(hasattr(result, "activate_memory"))

    def test_step25_source_has_no_retrieval_or_external_action_imports(self) -> None:
        forbidden_imports = {
            "subprocess",
            "boto3",
            "botocore",
            "psycopg",
        }
        for path in STEP25_ROOT.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            imported = {
                alias.name.split(".")[0]
                for node in ast.walk(tree)
                if isinstance(node, (ast.Import, ast.ImportFrom))
                for alias in node.names
            }
            self.assertFalse(imported & forbidden_imports, path.name)
        source = "\n".join(path.read_text(encoding="utf-8") for path in STEP25_ROOT.glob("*.py"))
        self.assertNotIn("VerifiedAnswer", source)
        self.assertNotIn("FinalAnswer", source)
        self.assertNotIn("personal_memory_proposal", source)
        self.assertNotIn("commit_helper", source)

    def test_no_migration_was_added_and_existing_draft_schema_is_reused(self) -> None:
        migrations = ROOT / "sql/cockroachdb/migrations"
        self.assertFalse(any("step25" in path.name.lower() for path in migrations.glob("*.sql")))
        source = (ROOT / "src/aioa_memory_kernel/verification/persistence.py").read_text(encoding="utf-8")
        self.assertIn("draft_stage=2", source)
        self.assertNotIn("CREATE TABLE", source)

    def test_documentation_closure_and_step26_boundary(self) -> None:
        expected = (
            ROOT / "docs/architecture/DRAFT_V2_GENERATION_LAYERED_CLAIM_VERIFIER_1A.md",
            ROOT / "docs/operations/STEP_25_DRAFT_V2_LAYERED_VERIFIER_VALIDATION_1A.md",
            ROOT / "docs/audits/STEP_25_DRAFT_V2_LAYERED_CLAIM_VERIFIER_CLOSURE_1A.md",
            ROOT / "docs/evidence/modeling/step25-draft-v2-layered-verifier-validation.json",
        )
        for path in expected:
            self.assertTrue(path.is_file(), path)
        roadmap = (ROOT / "docs/roadmap/PRODUCTION_ROADMAP.md").read_text(encoding="utf-8")
        self.assertIn("Step 25", roadmap)
        self.assertIn("Step 26", roadmap)
        agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("Step 25", agents)
        self.assertIn("Step 26", agents)


if __name__ == "__main__":
    unittest.main()
