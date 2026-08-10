"""Step 23 deterministic claim extraction and evidence-binding tests."""

from __future__ import annotations

import ast
import copy
import json
import unittest
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime

from tests._support import REPOSITORY_ROOT

from aioa_memory_kernel.claims import (
    CLAIM_SPAN_CONVENTION,
    PERSISTENCE_DECISION,
    ClaimAtomicity,
    ClaimBoundaryError,
    ClaimEvidenceBindingService,
    ClaimEvidenceCandidateStatus,
    ClaimEvidenceRelation,
    ClaimReasonCode,
    ClaimType,
    exact_text_spans,
    load_claim_processing_policy,
    prepare_claim_binding_request,
    verify_claim_assessment_hash,
    verify_claim_binding_request_hash,
    verify_claim_evidence_link_hash,
    verify_claim_record_hash,
    verify_packet_input_snapshot_hash,
    verify_snapshot_against_request,
)
from aioa_memory_kernel.contracts.exceptions import ContractValidationError, IntegrityError
from aioa_memory_kernel.contracts.serialization import canonical_sha256
from aioa_memory_kernel.modeling import (
    DraftV1Service,
    ProviderResponse,
    prepare_model_generation_request,
)
from aioa_memory_kernel.sources import SourceAuthorityLevel, SourcePublicationState
from aioa_memory_kernel.temporal import FreshnessStatus, TemporalApplicability
from tests.test_step21_temporal_resolution import (
    bundle_outcome,
    freshness_policy,
    metadata,
    resolve,
)


ROOT = REPOSITORY_ROOT
CLAIMS_ROOT = ROOT / "src/aioa_memory_kernel/claims"
NOW = datetime(2026, 8, 9, 4, 0, tzinfo=UTC)


class FixedClock:
    def now(self) -> datetime:
        return NOW


class FakeProvider:
    def __init__(self, request, text: str) -> None:
        self.identity = request.provider_identity
        self.text = text
        self.calls = 0

    def provider_identity(self):
        return self.identity

    def generate(self, request, timeout_policy):
        self.calls += 1
        return ProviderResponse(
            provider_identity_digest=self.identity.identity_digest,
            model_id=self.identity.model_id,
            model_version=self.identity.model_revision_or_declared_version,
            provider_request_id="step23-fake-provider-request",
            finish_reason="stop",
            response_content=self.text,
            usage_metadata={"prompt_tokens": 8, "completion_tokens": 12, "total_tokens": 20},
            latency_milliseconds=1,
        )


def draft_for(temporal_result, text: str):
    request = prepare_model_generation_request(temporal_result, "Welche Aussage gilt?")
    provider = FakeProvider(request, text)
    receipt = DraftV1Service(
        provider,
        clock=FixedClock(),
        sleep=lambda _: None,
    ).generate(request)
    return receipt.draft


def pipeline(
    draft_text: str,
    *,
    contents: tuple[str, ...] = ("Die Vorschrift ist aufgehoben.",),
    metadatas: tuple[dict[str, object], ...] | None = None,
    source_ids: tuple[str, ...] | None = None,
    resolve_kwargs: dict[str, object] | None = None,
):
    values = metadatas or tuple(
        metadata(
            document=f"document-{index}",
            version=f"version-{index}",
            provision=f"§-{index + 1}",
        )
        for index in range(len(contents))
    )
    outcome = bundle_outcome(
        *values,
        contents=contents,
        source_ids=source_ids,
    )
    temporal = resolve(outcome, **(resolve_kwargs or {}))
    draft = draft_for(temporal, draft_text)
    request = prepare_claim_binding_request(draft, (outcome.bundle,), temporal)
    snapshot = ClaimEvidenceBindingService().freeze_packet_input(request)
    return outcome, temporal, draft, request, snapshot


def assessment_for(snapshot, exact_text: str):
    claim = next(item for item in snapshot.ordered_claims if item.exact_claim_text == exact_text)
    assessment = next(
        item for item in snapshot.ordered_candidate_assessments if item.claim_id == claim.claim_id
    )
    return claim, assessment


class ClaimContractTests(unittest.TestCase):
    def test_exact_closed_enum_values(self) -> None:
        self.assertEqual(
            [item.value for item in ClaimEvidenceCandidateStatus],
            ["SUPPORTED", "REFUTED", "UNVERIFIED"],
        )
        self.assertEqual(
            [item.value for item in ClaimEvidenceRelation],
            ["SUPPORTS", "REFUTES", "RELATED_ONLY", "INSUFFICIENT"],
        )
        self.assertEqual(CLAIM_SPAN_CONVENTION, "draft-v1-unicode-codepoints-start-inclusive-end-exclusive-v1")

    def test_contracts_are_deeply_immutable(self) -> None:
        _, _, _, request, snapshot = pipeline("Die Vorschrift ist aufgehoben.")
        with self.assertRaises(FrozenInstanceError):
            snapshot.ordered_claims = ()  # type: ignore[misc]
        with self.assertRaises(FrozenInstanceError):
            snapshot.ordered_claims[0].exact_claim_text = "changed"  # type: ignore[misc]
        with self.assertRaises(FrozenInstanceError):
            request.tenant_id = "changed"  # type: ignore[misc]

    def test_hashes_are_deterministic_and_verifiable(self) -> None:
        first = pipeline("Die Vorschrift ist aufgehoben.")
        second = pipeline("Die Vorschrift ist aufgehoben.")
        self.assertEqual(first[-1].snapshot_hash, second[-1].snapshot_hash)
        snapshot = first[-1]
        verify_packet_input_snapshot_hash(snapshot)
        for claim in snapshot.ordered_claims:
            verify_claim_record_hash(claim)
        for link in snapshot.ordered_evidence_links:
            verify_claim_evidence_link_hash(link)
        for assessment in snapshot.ordered_candidate_assessments:
            verify_claim_assessment_hash(assessment)

    def test_changed_draft_changes_claim_and_snapshot_hashes(self) -> None:
        first = pipeline("Die Vorschrift ist aufgehoben.")[-1]
        second = pipeline("Die Vorschrift bleibt aufgehoben.")[-1]
        self.assertNotEqual(first.ordered_claims[0].claim_id, second.ordered_claims[0].claim_id)
        self.assertNotEqual(first.snapshot_hash, second.snapshot_hash)

    def test_stale_request_or_snapshot_hash_is_rejected(self) -> None:
        _, _, _, request, snapshot = pipeline("Die Vorschrift ist aufgehoben.")
        bad_request = copy.copy(request)
        object.__setattr__(bad_request, "request_hash", "0" * 64)
        with self.assertRaises(IntegrityError):
            verify_claim_binding_request_hash(bad_request)
        bad_snapshot = copy.copy(snapshot)
        object.__setattr__(bad_snapshot, "snapshot_hash", "0" * 64)
        with self.assertRaises(IntegrityError):
            verify_packet_input_snapshot_hash(bad_snapshot)


class ExtractionTests(unittest.TestCase):
    def test_sentences_and_bullets_preserve_exact_codepoint_spans(self) -> None:
        text = "Erste Aussage.\n• Zweite äöüß Aussage.\n3) Dritte Aussage."
        spans = exact_text_spans(text)
        self.assertEqual([item.text for item in spans], ["Erste Aussage.", "Zweite äöüß Aussage.", "Dritte Aussage."])
        for span in spans:
            self.assertEqual(text[span.start_offset : span.end_offset], span.text)

    def test_multiple_claims_preserve_draft_order_and_exact_text(self) -> None:
        text = "Erste Aussage. Zweite Aussage. Dritte Aussage."
        snapshot = pipeline(text)[-1]
        self.assertEqual([item.exact_claim_text for item in snapshot.ordered_claims], ["Erste Aussage.", "Zweite Aussage.", "Dritte Aussage."])
        self.assertEqual(
            [item.start_offset for item in snapshot.ordered_claims],
            sorted(item.start_offset for item in snapshot.ordered_claims),
        )

    def test_compound_and_non_factual_are_classified(self) -> None:
        snapshot = pipeline("Hallo! Der Anspruch besteht und die Frist läuft.")[-1]
        first, second = snapshot.ordered_claims
        self.assertIs(first.claim_type, ClaimType.NON_FACTUAL)
        self.assertIs(first.atomicity, ClaimAtomicity.NON_FACTUAL)
        self.assertIs(second.atomicity, ClaimAtomicity.COMPOUND)
        self.assertIn(ClaimReasonCode.CLAIM_COMPOUND, second.reason_codes)

    def test_temporal_legal_source_and_quantitative_types_are_closed(self) -> None:
        snapshot = pipeline(
            "Die Vorschrift ist aktuell wirksam. Laut offizieller Quelle gilt das Gesetz. Die Frist beträgt 10 Tage."
        )[-1]
        self.assertEqual(
            [item.claim_type for item in snapshot.ordered_claims],
            [ClaimType.TEMPORAL, ClaimType.SOURCE_ASSERTION, ClaimType.QUANTITATIVE],
        )

    def test_bad_span_and_non_nfc_text_are_rejected(self) -> None:
        with self.assertRaises(ClaimBoundaryError):
            exact_text_spans("A\u0308nderung.")
        snapshot = pipeline("Die Vorschrift ist aufgehoben.")[-1]
        claim = snapshot.ordered_claims[0]
        with self.assertRaises(ClaimBoundaryError):
            replace(claim, start_offset=claim.end_offset)


class EvidenceBindingTests(unittest.TestCase):
    def test_exact_support_refutation_related_and_no_evidence(self) -> None:
        snapshot = pipeline(
            "Die Vorschrift ist aufgehoben. Das Gesetz gilt. Die Behörde entscheidet schriftlich. Ohne Nachweis bleibt die Behauptung offen.",
            contents=(
                "Die Vorschrift ist aufgehoben.",
                "Das Gesetz gilt nicht.",
                "Die Behörde entscheidet später schriftlich.",
            ),
        )[-1]
        _, supported = assessment_for(snapshot, "Die Vorschrift ist aufgehoben.")
        _, refuted = assessment_for(snapshot, "Das Gesetz gilt.")
        _, related = assessment_for(snapshot, "Die Behörde entscheidet schriftlich.")
        _, unverified = assessment_for(snapshot, "Ohne Nachweis bleibt die Behauptung offen.")
        self.assertIs(supported.candidate_status, ClaimEvidenceCandidateStatus.SUPPORTED)
        self.assertIs(refuted.candidate_status, ClaimEvidenceCandidateStatus.REFUTED)
        self.assertIs(related.candidate_status, ClaimEvidenceCandidateStatus.UNVERIFIED)
        self.assertTrue(related.related_link_hashes)
        self.assertIs(unverified.candidate_status, ClaimEvidenceCandidateStatus.UNVERIFIED)

    def test_link_preserves_exact_step20_and_step21_identity(self) -> None:
        outcome, temporal, _, request, snapshot = pipeline("Die Vorschrift ist aufgehoben.")
        item = outcome.bundle.ordered_items[0]
        temporal_item = temporal.assessments[0]
        link = snapshot.ordered_evidence_links[0]
        self.assertEqual(link.step20_item_hash, item.item_hash)
        self.assertEqual(link.candidate_identity_hash, item.identity.identity_hash)
        self.assertEqual(link.source_id, item.identity.source_id)
        self.assertEqual(link.knowledge_version_id, item.identity.knowledge_version_id)
        self.assertEqual(link.chunk_id, item.identity.chunk_id)
        self.assertEqual(link.temporal_assessment_hash, temporal_item.assessment_hash)
        verify_snapshot_against_request(snapshot, request)

    def test_exact_support_region_offsets_and_digest_are_verified(self) -> None:
        snapshot = pipeline(
            "Zweite Aussage.",
            contents=("Erste Aussage. Zweite Aussage.",),
        )[-1]
        link = snapshot.ordered_evidence_links[0]
        self.assertEqual(link.evidence_start_offset, len("Erste Aussage. "))
        self.assertEqual(link.evidence_end_offset, len("Erste Aussage. Zweite Aussage."))
        verify_claim_evidence_link_hash(link)

    def test_duplicate_evidence_result_does_not_inflate_link_count(self) -> None:
        snapshot = pipeline("Die Vorschrift ist aufgehoben.")[-1]
        self.assertEqual(len(snapshot.ordered_evidence_links), 1)
        self.assertEqual(len(snapshot.ordered_candidate_assessments[0].supporting_link_hashes), 1)

    def test_similarity_and_step20_rank_do_not_create_support(self) -> None:
        snapshot = pipeline(
            "Die Vorschrift ist heute aufgehoben.",
            contents=("Die Vorschrift ist möglicherweise aufgehoben.",),
        )[-1]
        _, assessment = assessment_for(snapshot, "Die Vorschrift ist heute aufgehoben.")
        self.assertIs(assessment.candidate_status, ClaimEvidenceCandidateStatus.UNVERIFIED)

    def test_negative_evidence_absence_is_not_refutation(self) -> None:
        snapshot = pipeline("Eine unbelegte Aussage.", contents=("Anderer Inhalt.",))[-1]
        _, assessment = assessment_for(snapshot, "Eine unbelegte Aussage.")
        self.assertIs(assessment.candidate_status, ClaimEvidenceCandidateStatus.UNVERIFIED)
        self.assertFalse(assessment.refuting_link_hashes)


class TemporalAuthorityAndConflictTests(unittest.TestCase):
    def test_future_effective_match_is_insufficient_not_supported(self) -> None:
        snapshot = pipeline(
            "Die Vorschrift ist aufgehoben.",
            metadatas=(metadata(effective_from="2030-01-01"),),
        )[-1]
        link = snapshot.ordered_evidence_links[0]
        _, assessment = assessment_for(snapshot, "Die Vorschrift ist aufgehoben.")
        self.assertIs(link.temporal_applicability, TemporalApplicability.NOT_YET_APPLICABLE)
        self.assertIs(link.relation, ClaimEvidenceRelation.INSUFFICIENT)
        self.assertIs(assessment.candidate_status, ClaimEvidenceCandidateStatus.UNVERIFIED)

    def test_stale_only_support_remains_inspectable_but_unverified(self) -> None:
        snapshot = pipeline(
            "Die Vorschrift ist aufgehoben.",
            metadatas=(metadata(verified_at="2020-01-01T00:00:00Z"),),
            resolve_kwargs={"policy": freshness_policy(days=30)},
        )[-1]
        link = snapshot.ordered_evidence_links[0]
        _, assessment = assessment_for(snapshot, "Die Vorschrift ist aufgehoben.")
        self.assertIs(link.freshness_status, FreshnessStatus.STALE)
        self.assertIs(assessment.candidate_status, ClaimEvidenceCandidateStatus.UNVERIFIED)
        self.assertIn(ClaimReasonCode.FRESHNESS_STALE, assessment.reason_codes)

    def test_material_support_refute_conflict_is_preserved(self) -> None:
        common = {
            "document": "document-shared",
            "provision": "§-1",
        }
        snapshot = pipeline(
            "Die Vorschrift ist aufgehoben.",
            contents=("Die Vorschrift ist aufgehoben.", "Die Vorschrift ist nicht aufgehoben."),
            metadatas=(
                metadata(version="version-a", **common),
                metadata(version="version-b", **common),
            ),
        )[-1]
        _, assessment = assessment_for(snapshot, "Die Vorschrift ist aufgehoben.")
        self.assertIs(assessment.candidate_status, ClaimEvidenceCandidateStatus.UNVERIFIED)
        self.assertTrue(assessment.supporting_link_hashes)
        self.assertTrue(assessment.refuting_link_hashes)
        self.assertIn(ClaimReasonCode.MATERIAL_CONFLICT, assessment.reason_codes)

    def test_source_assertion_requires_official_primary_authority(self) -> None:
        snapshot = pipeline("Laut offizieller Quelle gilt das Gesetz.", contents=("Laut offizieller Quelle gilt das Gesetz.",))[-1]
        item = snapshot.ordered_evidence_links[0]
        if item.authority_level is SourceAuthorityLevel.AUTHORITATIVE_SECONDARY:
            self.assertIs(item.relation, ClaimEvidenceRelation.INSUFFICIENT)
        else:
            self.assertIs(item.relation, ClaimEvidenceRelation.SUPPORTS)

    def test_weak_authority_and_unpublished_links_fail_closed(self) -> None:
        snapshot = pipeline("Die Vorschrift ist aufgehoben.")[-1]
        link = snapshot.ordered_evidence_links[0]
        with self.assertRaises(ClaimBoundaryError):
            replace(link, authority_level=SourceAuthorityLevel.INFORMATIONAL_SECONDARY)
        with self.assertRaises(ClaimBoundaryError):
            replace(link, publication_state=SourcePublicationState.QUARANTINED)


class InputIsolationTests(unittest.TestCase):
    def test_tampered_draft_hash_is_rejected_before_extraction(self) -> None:
        outcome, temporal, draft, _, _ = pipeline("Die Vorschrift ist aufgehoben.")
        tampered = copy.copy(draft)
        object.__setattr__(tampered, "draft_hash", "0" * 64)
        with self.assertRaises(ClaimBoundaryError) as caught:
            prepare_claim_binding_request(tampered, (outcome.bundle,), temporal)
        self.assertIs(caught.exception.reason_code, ClaimReasonCode.INPUT_HASH_INVALID)

    def test_cross_tenant_cross_user_and_route_mismatch_fail_closed(self) -> None:
        outcome, temporal, draft, _, _ = pipeline("Die Vorschrift ist aufgehoben.")
        for changed in (
            replace(draft, tenant_id="tenant-other"),
            replace(draft, user_id="user-other"),
            replace(draft, route_hash="1" * 64),
        ):
            with self.subTest(field=changed):
                with self.assertRaises(ClaimBoundaryError):
                    prepare_claim_binding_request(changed, (outcome.bundle,), temporal)

    def test_wrong_step20_item_hash_is_rejected(self) -> None:
        outcome, temporal, draft, _, _ = pipeline("Die Vorschrift ist aufgehoben.")
        bad_item = copy.copy(outcome.bundle.ordered_items[0])
        object.__setattr__(bad_item, "item_hash", "0" * 64)
        bad_bundle = copy.copy(outcome.bundle)
        object.__setattr__(bad_bundle, "ordered_items", (bad_item,))
        with self.assertRaises(ClaimBoundaryError):
            prepare_claim_binding_request(draft, (bad_bundle,), temporal)

    def test_wrong_step21_result_hash_is_rejected(self) -> None:
        outcome, temporal, draft, _, _ = pipeline("Die Vorschrift ist aufgehoben.")
        bad = copy.copy(temporal)
        object.__setattr__(bad, "result_hash", "0" * 64)
        with self.assertRaises(ClaimBoundaryError):
            prepare_claim_binding_request(draft, (outcome.bundle,), bad)


class SnapshotAndBoundaryTests(unittest.TestCase):
    def test_snapshot_contains_exact_canonical_partitions(self) -> None:
        _, _, _, request, snapshot = pipeline(
            "Die Vorschrift ist aufgehoben. Das Gesetz gilt.",
            contents=("Die Vorschrift ist aufgehoben.", "Das Gesetz gilt nicht."),
        )
        self.assertEqual(len(snapshot.ordered_claims), 2)
        self.assertEqual(len(snapshot.ordered_candidate_assessments), 2)
        self.assertEqual(
            tuple(item.claim_id for item in snapshot.ordered_candidate_assessments),
            tuple(sorted(item.claim_id for item in snapshot.ordered_claims)),
        )
        verify_snapshot_against_request(snapshot, request)

    def test_changed_link_or_candidate_status_changes_snapshot_hash(self) -> None:
        first = pipeline("Die Vorschrift ist aufgehoben.")[-1]
        second = pipeline(
            "Die Vorschrift ist aufgehoben.",
            contents=("Die Vorschrift ist nicht aufgehoben.",),
        )[-1]
        self.assertNotEqual(first.ordered_evidence_links[0].link_hash, second.ordered_evidence_links[0].link_hash)
        self.assertNotEqual(first.ordered_candidate_assessments[0].candidate_status, second.ordered_candidate_assessments[0].candidate_status)
        self.assertNotEqual(first.snapshot_hash, second.snapshot_hash)

    def test_nonfactual_and_compound_claims_are_unverified(self) -> None:
        snapshot = pipeline(
            "Hallo! Die Vorschrift gilt und die Frist läuft.",
            contents=("Die Vorschrift gilt und die Frist läuft.",),
        )[-1]
        self.assertTrue(
            all(
                item.candidate_status is ClaimEvidenceCandidateStatus.UNVERIFIED
                for item in snapshot.ordered_candidate_assessments
            )
        )

    def test_step23_has_no_retrieval_model_network_execution_or_persistence_port(self) -> None:
        imports: set[str] = set()
        source = ""
        for path in sorted(CLAIMS_ROOT.glob("*.py")):
            value = path.read_text(encoding="utf-8")
            source += value
            tree = ast.parse(value)
            imports |= {
                alias.name
                for node in ast.walk(tree)
                if isinstance(node, ast.Import)
                for alias in node.names
            }
            imports |= {
                node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
            }
        forbidden = ("requests", "httpx", "urllib", "socket", "boto", "subprocess", "persistence")
        self.assertFalse(any(any(word in name for word in forbidden) for name in imports))
        for forbidden_text in ("CorrectionPacket", "DraftV2", "required_corrections", "packet_hmac"):
            self.assertNotIn(forbidden_text, source)

    def test_step23_persistence_decision_adds_no_migration(self) -> None:
        self.assertEqual(PERSISTENCE_DECISION, "NOT_REQUIRED_STEP23_FROZEN_PACKET_INPUT_ONLY")
        migrations = (ROOT / "sql/cockroachdb/migrations").glob("*.sql")
        self.assertFalse(any("step23" in item.name.lower() for item in migrations))

    def test_policy_is_fixed_deterministic_and_model_free(self) -> None:
        policy = load_claim_processing_policy()
        self.assertFalse(policy.model_assisted_extraction)
        self.assertEqual(policy, load_claim_processing_policy())
        with self.assertRaises(ContractValidationError):
            replace(policy, model_assisted_extraction=True)


class DocumentationClosureTests(unittest.TestCase):
    def test_validation_evidence_is_canonical_and_sanitized(self) -> None:
        path = ROOT / "docs/evidence/modeling/step23-claim-evidence-binding-validation.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        claimed = value.pop("validation_digest")
        self.assertEqual(claimed, canonical_sha256(value))
        self.assertEqual(value["status"], "PASS")
        self.assertEqual(value["candidate_assessments"]["counts"], {"SUPPORTED": 1, "REFUTED": 1, "UNVERIFIED": 4})
        self.assertFalse(value["step24_started"])
        rendered = json.dumps(value, sort_keys=True).casefold()
        for unsafe in ("/media/", "/home/", "authorization", "aws_secret_access_key"):
            self.assertNotIn(unsafe, rendered)

    def test_closure_documents_and_roadmap_preserve_step27_boundary(self) -> None:
        required = {
            "docs/architecture/CLAIM_EXTRACTION_EVIDENCE_BINDING_1A.md": ("Unicode code", "Step 24 is NOT STARTED"),
            "docs/adr/ADR-030-claim-extraction-evidence-binding.md": ("deterministically bind", "Step 24: NOT STARTED"),
            "docs/operations/STEP_23_CLAIM_EVIDENCE_BINDING_VALIDATION_1A.md": ("run_step23_claim_evidence_validation.py", "Step 24: NOT STARTED"),
            "docs/audits/STEP_23_CLAIM_EXTRACTION_EVIDENCE_BINDING_CLOSURE_1A.md": ("a24e1439d5d3971182dbf79c5e317f390065e712", "Step 24 remains NOT STARTED"),
        }
        for relative, tokens in required.items():
            text = (ROOT / relative).read_text(encoding="utf-8")
            for token in tokens:
                self.assertIn(token, text, relative)
        roadmap = (ROOT / "docs/roadmap/PRODUCTION_ROADMAP.md").read_text(encoding="utf-8")
        self.assertIn("- [x] **Step 23 — Claim Extraction and Evidence Binding 1A**", roadmap)
        self.assertIn("- [x] **Step 24 — Correction Packet Construction and Integrity 1A**", roadmap)
        self.assertIn("- [x] **Step 25 — Draft V2 Generation and Layered Claim Verifier 1A**", roadmap)
        self.assertIn("- [x] **Step 26 — Verified Answer Assembly and Fail-Closed Output 1A**", roadmap)
        self.assertIn("- [x] **Step 27 — Personal Memory HAT Persistence, Quotas and Model Bindings 1A**", roadmap)
        self.assertIn("- [x] **Step 28 — Knowledge Hub and Critic Prompt Loop Correction Candidate Bridge 1A**", roadmap)
        self.assertIn("- [x] **Step 29 — Personal Memory Patch Proposal and Evidence Validation 1A**", roadmap)
        self.assertIn("- [x] **Step 30", roadmap)
        self.assertIn("- [x] **Step 31", roadmap)
        self.assertIn("- [x] **Step 32", roadmap)
        self.assertIn("- [x] **Step 33", roadmap)
        self.assertIn("- [ ] **Step 34", roadmap)
        agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("Step 23 exact-span deterministic claim extraction", agents)
        self.assertIn("Step 24 verified frozen Step 23 input binding", agents)
        self.assertIn("Step 25 verified Correction Packet integrity gating", agents)
        self.assertIn("Step 26 complete upstream integrity binding", agents)
        self.assertIn("Step 27 owner-private empty Personal Memory HAT slots", agents)
        self.assertIn("Step 28 owner- and slot-bound Correction Candidate", agents)
        self.assertIn("Step 29: COMPLETE AND PUSHED", agents)
        self.assertIn("Step 30: COMPLETE AND PUSHED", agents)
        self.assertIn("Step 31: COMPLETE AND PUSHED", agents)
        self.assertIn("Step 32: COMPLETE AND PUSHED", agents)
        self.assertIn("Step 33: COMPLETE AND PUSHED", agents)
        self.assertIn("Step 34: NOT STARTED", agents)


if __name__ == "__main__":
    unittest.main()
