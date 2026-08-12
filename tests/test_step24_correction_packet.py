"""Step 24 deterministic Correction Packet and integrity tests."""

from __future__ import annotations

import ast
import copy
import json
import unittest
from dataclasses import FrozenInstanceError, replace

from tests._support import REPOSITORY_ROOT

from aioa_memory_kernel.claims import ClaimReasonCode
from aioa_memory_kernel.contracts.enums import EvidenceStatus
from aioa_memory_kernel.contracts.exceptions import ContractValidationError
from aioa_memory_kernel.contracts.serialization import (
    canonical_json,
    canonical_sha256,
)
from aioa_memory_kernel.corrections import (
    PACKET_AUTHENTICITY_ALGORITHM,
    PACKET_HMAC_DOMAIN_ID,
    PERSISTENCE_DECISION,
    ConflictHandling,
    CorrectionActionType,
    CorrectionPacketBoundaryError,
    CorrectionPacketPolicy,
    CorrectionPacketService,
    HmacSha256PacketAuthenticator,
    ProhibitionType,
    Step24ReasonCode,
    build_correction_packet,
    canonical_packet_bytes,
    load_packet_policy,
    verify_citation_hash,
    verify_conflict_hash,
    verify_correction_packet_hash,
    verify_fact_reference_hash,
    verify_integrity_receipt_hash,
    verify_packet_against_snapshot,
    verify_prohibited_claim_hash,
    verify_required_correction_hash,
)
from tests.test_step21_temporal_resolution import metadata
from tests.test_step23_claim_evidence_binding import pipeline


ROOT = REPOSITORY_ROOT
CORRECTIONS_ROOT = ROOT / "src/aioa_memory_kernel/corrections"
TEST_KEY = bytes(range(32))


def packet_fixture():
    snapshot = pipeline(
        "Die Vorschrift ist aufgehoben. Das Gesetz gilt. Eine unbelegte Aussage. Hallo!",
        contents=(
            "Die Vorschrift ist aufgehoben.",
            "Das Gesetz gilt nicht.",
            "Anderer Inhalt.",
        ),
    )[-1]
    return snapshot, build_correction_packet(snapshot)


def future_packet():
    snapshot = pipeline(
        "Die Vorschrift ist aufgehoben.",
        metadatas=(metadata(version="future-v1", effective_from="2030-01-01"),),
        contents=("Die Vorschrift ist aufgehoben.",),
    )[-1]
    return snapshot, build_correction_packet(snapshot)


def authority_packet():
    text = "Laut offizieller Quelle gilt das Gesetz."
    snapshot = pipeline(text, contents=(text,))[-1]
    return snapshot, build_correction_packet(snapshot)


def conflict_packet():
    common = {"document": "step24-conflict-document", "provision": "§-24"}
    snapshot = pipeline(
        "Die Vorschrift ist aufgehoben.",
        contents=(
            "Die Vorschrift ist aufgehoben.",
            "Die Vorschrift ist nicht aufgehoben.",
        ),
        metadatas=(
            metadata(version="conflict-a", **common),
            metadata(version="conflict-b", **common),
        ),
    )[-1]
    return snapshot, build_correction_packet(snapshot)


class PacketContractTests(unittest.TestCase):
    def test_packet_and_nested_values_are_deeply_immutable(self) -> None:
        _, packet = packet_fixture()
        with self.assertRaises(FrozenInstanceError):
            packet.tenant_id = "changed"  # type: ignore[misc]
        with self.assertRaises(FrozenInstanceError):
            packet.ordered_claims[0].exact_claim_text = "changed"  # type: ignore[misc]

    def test_packet_id_hash_and_replay_are_deterministic(self) -> None:
        first_snapshot, first = packet_fixture()
        second_snapshot, second = packet_fixture()
        self.assertEqual(first_snapshot.snapshot_hash, second_snapshot.snapshot_hash)
        self.assertEqual(first.packet_id, second.packet_id)
        self.assertEqual(first.packet_hash, second.packet_hash)
        self.assertEqual(canonical_packet_bytes(first), canonical_packet_bytes(second))
        verify_correction_packet_hash(first)
        verify_packet_against_snapshot(first, first_snapshot)

    def test_changed_claim_changes_packet_identity(self) -> None:
        first = packet_fixture()[1]
        second_snapshot = pipeline(
            "Die Vorschrift bleibt aufgehoben.",
            contents=("Die Vorschrift bleibt aufgehoben.",),
        )[-1]
        second = build_correction_packet(second_snapshot)
        self.assertNotEqual(first.packet_id, second.packet_id)
        self.assertNotEqual(first.packet_hash, second.packet_hash)

    def test_changed_correction_changes_packet_hash(self) -> None:
        _, packet = packet_fixture()
        correction = packet.ordered_required_corrections[0]
        changed = replace(correction, limitations=("changed-correction",))
        corrections = tuple(
            changed if item.correction_id == correction.correction_id else item
            for item in packet.ordered_required_corrections
        )
        corrections = tuple(
            sorted(
                corrections,
                key=lambda item: (
                    next(
                        index
                        for index, claim in enumerate(packet.ordered_claims)
                        if claim.claim_id == item.claim_id
                    ),
                    list(CorrectionActionType).index(item.correction_action),
                    item.correction_id,
                ),
            )
        )
        changed_packet = replace(packet, ordered_required_corrections=corrections)
        self.assertNotEqual(packet.packet_hash, changed_packet.packet_hash)

    def test_changed_citation_changes_packet_hash(self) -> None:
        snapshot = pipeline("Die Vorschrift ist aufgehoben.")[-1]
        packet = build_correction_packet(snapshot)
        citation = packet.ordered_citations[0]
        changed = replace(citation, source_reference=f"{citation.source_reference}-changed")
        changed_packet = replace(packet, ordered_citations=(changed,))
        self.assertNotEqual(packet.packet_hash, changed_packet.packet_hash)

    def test_scope_and_policy_tampering_fail_integrity(self) -> None:
        snapshot, packet = packet_fixture()
        changed = copy.copy(packet)
        object.__setattr__(changed, "tenant_id", "tenant-other")
        with self.assertRaises(CorrectionPacketBoundaryError):
            verify_correction_packet_hash(changed)
        with self.assertRaises(CorrectionPacketBoundaryError):
            verify_packet_against_snapshot(replace(packet, tenant_id="tenant-other"), snapshot)
        changed_policy = CorrectionPacketPolicy(policy_version="2")
        policy_tamper = copy.copy(packet)
        object.__setattr__(policy_tamper, "packet_policy", changed_policy)
        with self.assertRaises(CorrectionPacketBoundaryError):
            verify_correction_packet_hash(policy_tamper)

    def test_tampered_step23_snapshot_is_rejected(self) -> None:
        snapshot, _ = packet_fixture()
        tampered = copy.copy(snapshot)
        object.__setattr__(tampered, "snapshot_hash", "0" * 64)
        with self.assertRaises(CorrectionPacketBoundaryError) as caught:
            build_correction_packet(tampered)
        self.assertIs(caught.exception.reason_code, Step24ReasonCode.PACKET_INPUT_HASH_INVALID)

    def test_packet_binds_exact_upstream_identity_and_policy_by_hash(self) -> None:
        snapshot, packet = packet_fixture()
        self.assertEqual(packet.draft_v1_hash, snapshot.draft_v1_hash)
        self.assertEqual(packet.step20_evidence_bundle_hashes, snapshot.step20_bundle_hashes)
        self.assertEqual(packet.step21_resolution_hash, snapshot.step21_result_hash)
        self.assertEqual(packet.step23_input_snapshot_hash, snapshot.snapshot_hash)
        self.assertEqual(packet.route_hash, snapshot.route_hash)
        self.assertEqual(packet.effective_scope, snapshot.effective_scope)
        self.assertFalse(packet.knowledge_policy_binding.explicit_decision_values_available)
        self.assertEqual(
            packet.knowledge_policy_binding.step20_bundle_hashes,
            snapshot.step20_bundle_hashes,
        )
        self.assertEqual(
            packet.ordered_claim_assessments,
            snapshot.ordered_candidate_assessments,
        )


class CorrectionDerivationTests(unittest.TestCase):
    def test_supported_claim_is_retained_without_factual_correction(self) -> None:
        _, packet = packet_fixture()
        supported = packet.ordered_claims[0]
        self.assertFalse(
            any(item.claim_id == supported.claim_id for item in packet.ordered_required_corrections)
        )
        self.assertTrue(any(item.claim_id == supported.claim_id for item in packet.ordered_citations))

    def test_refuted_claim_creates_remove_correction_and_exact_prohibition(self) -> None:
        _, packet = packet_fixture()
        refuted = next(item for item in packet.ordered_claims if item.exact_claim_text == "Das Gesetz gilt.")
        correction = next(item for item in packet.ordered_required_corrections if item.claim_id == refuted.claim_id)
        self.assertIs(correction.correction_action, CorrectionActionType.REMOVE_CLAIM)
        self.assertIn(Step24ReasonCode.CORRECTION_REQUIRED_REFUTED, correction.reason_codes)
        prohibited = [item for item in packet.ordered_prohibited_claims if item.source_claim_id == refuted.claim_id]
        self.assertIn(ProhibitionType.DO_NOT_REPEAT_EXACT, {item.prohibition_type for item in prohibited})
        self.assertTrue(correction.supporting_evidence_link_hashes)

    def test_unverified_claim_is_qualified_without_invented_replacement(self) -> None:
        _, packet = packet_fixture()
        claim = next(item for item in packet.ordered_claims if item.exact_claim_text == "Eine unbelegte Aussage.")
        correction = next(item for item in packet.ordered_required_corrections if item.claim_id == claim.claim_id)
        self.assertIs(correction.correction_action, CorrectionActionType.QUALIFY_CLAIM)
        self.assertFalse(correction.required_replacement_facts)
        prohibited = next(item for item in packet.ordered_prohibited_claims if item.source_claim_id == claim.claim_id)
        self.assertIs(prohibited.prohibition_type, ProhibitionType.DO_NOT_STATE_AS_FACT)

    def test_non_factual_segment_has_no_correction_or_prohibition(self) -> None:
        _, packet = packet_fixture()
        claim = next(item for item in packet.ordered_claims if item.exact_claim_text == "Hallo!")
        self.assertFalse(any(item.claim_id == claim.claim_id for item in packet.ordered_required_corrections))
        self.assertFalse(any(item.source_claim_id == claim.claim_id for item in packet.ordered_prohibited_claims))

    def test_temporal_mismatch_creates_temporal_correction(self) -> None:
        _, packet = future_packet()
        correction = packet.ordered_required_corrections[0]
        self.assertIs(correction.correction_action, CorrectionActionType.TEMPORAL_CORRECTION)
        self.assertIn(Step24ReasonCode.CORRECTION_REQUIRED_TEMPORAL, correction.reason_codes)
        self.assertIn(
            ProhibitionType.DO_NOT_USE_OUTSIDE_TEMPORAL_SCOPE,
            {item.prohibition_type for item in packet.ordered_prohibited_claims},
        )

    def test_source_authority_mismatch_creates_authority_correction(self) -> None:
        snapshot, packet = authority_packet()
        self.assertIn(
            ClaimReasonCode.SOURCE_AUTHORITY_INSUFFICIENT,
            snapshot.ordered_evidence_links[0].reason_codes,
        )
        correction = packet.ordered_required_corrections[0]
        self.assertIs(
            correction.correction_action,
            CorrectionActionType.SOURCE_AUTHORITY_CORRECTION,
        )
        self.assertIn(
            ProhibitionType.DO_NOT_UPGRADE_SOURCE_AUTHORITY,
            {item.prohibition_type for item in packet.ordered_prohibited_claims},
        )

    def test_material_conflict_is_preserved_and_never_resolved(self) -> None:
        _, packet = conflict_packet()
        self.assertEqual(len(packet.ordered_conflicts), 1)
        conflict = packet.ordered_conflicts[0]
        self.assertIs(conflict.required_handling, ConflictHandling.PRESERVE_AND_QUALIFY)
        self.assertTrue(conflict.supporting_evidence_hashes)
        self.assertTrue(conflict.refuting_evidence_hashes)
        correction = packet.ordered_required_corrections[0]
        self.assertIs(correction.correction_action, CorrectionActionType.QUALIFY_CLAIM)
        self.assertIn(
            ProhibitionType.DO_NOT_RESOLVE_CONFLICT_AS_CERTAIN,
            {item.prohibition_type for item in packet.ordered_prohibited_claims},
        )

    def test_all_nested_hashes_verify(self) -> None:
        _, packet = conflict_packet()
        for correction in packet.ordered_required_corrections:
            verify_required_correction_hash(correction)
            for fact in correction.required_replacement_facts:
                verify_fact_reference_hash(fact)
        for prohibited in packet.ordered_prohibited_claims:
            verify_prohibited_claim_hash(prohibited)
        for citation in packet.ordered_citations:
            verify_citation_hash(citation)
        for conflict in packet.ordered_conflicts:
            verify_conflict_hash(conflict)


class CitationOrderingAndStatusTests(unittest.TestCase):
    def test_citations_bind_exact_candidate_and_lineage(self) -> None:
        snapshot, packet = packet_fixture()
        link = snapshot.ordered_evidence_links[0]
        citation = next(item for item in packet.ordered_citations if item.evidence_link_hash == link.link_hash)
        self.assertEqual(citation.candidate_hash, link.candidate_identity_hash)
        self.assertEqual(citation.source_id, link.source_id)
        self.assertEqual(citation.knowledge_version_id, link.knowledge_version_id)
        self.assertEqual(citation.chunk_id, link.chunk_id)
        self.assertEqual(citation.content_sha256, link.content_sha256)
        self.assertEqual(citation.temporal_assessment_hash, link.temporal_assessment_hash)

    def test_corrections_prohibitions_citations_and_conflicts_are_ordered(self) -> None:
        _, packet = packet_fixture()
        claim_order = {item.claim_id: index for index, item in enumerate(packet.ordered_claims)}
        self.assertEqual(
            [claim_order[item.claim_id] for item in packet.ordered_required_corrections],
            sorted(claim_order[item.claim_id] for item in packet.ordered_required_corrections),
        )
        self.assertEqual(
            [claim_order[item.source_claim_id] for item in packet.ordered_prohibited_claims],
            sorted(claim_order[item.source_claim_id] for item in packet.ordered_prohibited_claims),
        )
        self.assertEqual(
            [claim_order[item.claim_id] for item in packet.ordered_citations],
            sorted(claim_order[item.claim_id] for item in packet.ordered_citations),
        )
        conflict = conflict_packet()[1]
        self.assertEqual(
            tuple(item.conflict_group_id for item in conflict.ordered_conflicts),
            tuple(sorted(item.conflict_group_id for item in conflict.ordered_conflicts)),
        )

    def test_evidence_status_is_preserved_without_upgrade(self) -> None:
        snapshot, packet = future_packet()
        self.assertIs(packet.evidence_status, snapshot.step21_evidence_status)
        self.assertIs(packet.evidence_status, EvidenceStatus.INSUFFICIENT)
        for status in (
            EvidenceStatus.SUFFICIENT,
            EvidenceStatus.INSUFFICIENT,
            EvidenceStatus.CONFLICTING,
            EvidenceStatus.STALE,
            EvidenceStatus.UNAVAILABLE,
        ):
            changed_snapshot = replace(snapshot, step21_evidence_status=status)
            changed_packet = build_correction_packet(changed_snapshot)
            self.assertIs(changed_packet.evidence_status, status)

    def test_packet_policy_is_fixed_non_authoritative_and_hash_bound(self) -> None:
        policy = load_packet_policy()
        self.assertEqual(policy, load_packet_policy())
        self.assertFalse(policy.packet_grants_execution)
        self.assertTrue(policy.must_preserve_conflicts)
        self.assertTrue(policy.must_not_upgrade_authority)
        with self.assertRaises(ContractValidationError):
            replace(policy, must_not_upgrade_authority=False)


class HmacIntegrityTests(unittest.TestCase):
    def signer(self, key: bytes = TEST_KEY, key_id: str = "step24-validation-key"):
        return HmacSha256PacketAuthenticator(key_id=key_id, key_material=key)

    def test_hmac_sha256_valid_and_deterministic(self) -> None:
        _, packet = packet_fixture()
        signer = self.signer()
        first = signer.authenticate(packet)
        second = signer.authenticate(packet)
        self.assertEqual(first.authenticator, second.authenticator)
        self.assertEqual(first.receipt_hash, second.receipt_hash)
        self.assertEqual(first.integrity_algorithm, PACKET_AUTHENTICITY_ALGORITHM)
        self.assertEqual(first.domain_id, PACKET_HMAC_DOMAIN_ID)
        signer.verify(packet, first)
        verify_integrity_receipt_hash(first)

    def test_wrong_key_and_key_id_fail(self) -> None:
        _, packet = packet_fixture()
        receipt = self.signer().authenticate(packet)
        with self.assertRaises(CorrectionPacketBoundaryError):
            self.signer(bytes(reversed(range(32)))).verify(packet, receipt)
        with self.assertRaises(CorrectionPacketBoundaryError):
            self.signer(key_id="other-key").verify(packet, receipt)

    def test_changed_packet_or_authenticator_fails(self) -> None:
        _, packet = packet_fixture()
        signer = self.signer()
        receipt = signer.authenticate(packet)
        tampered_packet = copy.copy(packet)
        object.__setattr__(tampered_packet, "packet_hash", "0" * 64)
        with self.assertRaises(CorrectionPacketBoundaryError):
            signer.verify(tampered_packet, receipt)
        tampered_receipt = copy.copy(receipt)
        object.__setattr__(tampered_receipt, "authenticator", "0" * 64)
        with self.assertRaises(CorrectionPacketBoundaryError):
            signer.verify(packet, tampered_receipt)

    def test_key_material_is_absent_from_packet_receipt_and_repr(self) -> None:
        _, packet = packet_fixture()
        signer = self.signer()
        receipt = signer.authenticate(packet)
        rendered = canonical_json({"packet": packet, "receipt": receipt})
        self.assertNotIn(TEST_KEY.hex(), rendered)
        self.assertNotIn(TEST_KEY.hex(), repr(signer))
        self.assertIn("<redacted>", repr(signer))

    def test_short_key_is_rejected_and_service_without_signer_fails_closed(self) -> None:
        with self.assertRaises(ContractValidationError):
            self.signer(b"short")
        _, packet = packet_fixture()
        with self.assertRaises(RuntimeError):
            CorrectionPacketService().authenticate(packet)


class IsolationAndBoundaryTests(unittest.TestCase):
    def test_packet_rejects_cross_tenant_user_route_hat_and_scope_detachment(self) -> None:
        snapshot, packet = packet_fixture()
        changed_values = (
            replace(packet, tenant_id="tenant-other"),
            replace(packet, user_id="user-other"),
            replace(packet, route_hash="1" * 64),
            replace(packet, selected_hat_id="other-hat"),
            replace(packet, hat_scope_id="other-scope"),
        )
        for changed in changed_values:
            with self.subTest(packet=changed.packet_hash):
                with self.assertRaises(CorrectionPacketBoundaryError):
                    verify_packet_against_snapshot(changed, snapshot)

    def test_citation_with_presigned_marker_is_rejected(self) -> None:
        _, packet = packet_fixture()
        citation = packet.ordered_citations[0]
        with self.assertRaises(CorrectionPacketBoundaryError):
            replace(citation, source_reference="https://example.invalid/?X-Amz-Signature=abc")

    def test_packet_package_has_no_model_retrieval_network_db_or_execution_capability(self) -> None:
        imports: set[str] = set()
        source = ""
        for path in sorted(CORRECTIONS_ROOT.glob("*.py")):
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
                node.module or ""
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom)
            }
        forbidden_imports = (
            "requests",
            "httpx",
            "urllib",
            "socket",
            "boto",
            "subprocess",
            "persistence",
            "retrieval",
            "modeling",
        )
        self.assertFalse(
            any(any(word in name for word in forbidden_imports) for name in imports)
        )
        for forbidden_text in (
            "generate_draft_v2",
            "LayeredFinalVerifier",
            "VerifiedAnswer",
            "approve_memory",
            "execute_action",
        ):
            self.assertNotIn(forbidden_text, source)

    def test_no_step24_migration_and_existing_step4_schema_is_not_overloaded(self) -> None:
        self.assertEqual(
            PERSISTENCE_DECISION,
            "DEFERRED_EXISTING_STEP4_SCHEMA_REQUIRES_DURABLE_UPSTREAM_LINEAGE",
        )
        migrations = (ROOT / "sql/cockroachdb/migrations").glob("*.sql")
        self.assertFalse(any("step24" in item.name.casefold() for item in migrations))
        schema = (
            ROOT / "sql/cockroachdb/migrations/0003_step4_kernel_memory_and_audit_evidence.sql"
        ).read_text(encoding="utf-8")
        self.assertIn("CREATE TABLE memory_patch.correction_packets", schema)
        self.assertIn("CREATE TABLE memory_patch.correction_requirements", schema)


class DocumentationClosureTests(unittest.TestCase):
    def test_evidence_docs_roadmap_and_agents_close_only_step24(self) -> None:
        evidence_path = (
            ROOT / "docs/evidence/modeling/step24-correction-packet-validation.json"
        )
        value = json.loads(evidence_path.read_text(encoding="utf-8"))
        claimed = value.pop("validation_digest")
        self.assertEqual(claimed, canonical_sha256(value))
        self.assertEqual(value["status"], "PASS")
        self.assertFalse(value["step25_started"])
        rendered = json.dumps(value, sort_keys=True).casefold()
        for unsafe in (
            "/media/",
            "/home/",
            "authorization:",
            "aws_secret_access_key",
        ):
            self.assertNotIn(unsafe, rendered)

        required = {
            "docs/architecture/CORRECTION_PACKET_CONSTRUCTION_INTEGRITY_1A.md": (
                "HMAC-SHA-256",
                "Step 25 is NOT STARTED",
            ),
            "docs/adr/ADR-031-correction-packet-construction-integrity.md": (
                "canonical JSON",
                "Step 25: NOT STARTED",
            ),
            "docs/operations/STEP_24_CORRECTION_PACKET_VALIDATION_1A.md": (
                "run_step24_correction_packet_validation.py",
                "Step 25: NOT STARTED",
            ),
            "docs/audits/STEP_24_CORRECTION_PACKET_CONSTRUCTION_INTEGRITY_CLOSURE_1A.md": (
                "f328168434984ea022346758770d6df23c67bb08",
                "Step 25 remains NOT STARTED",
            ),
        }
        for relative, tokens in required.items():
            text = (ROOT / relative).read_text(encoding="utf-8")
            for token in tokens:
                self.assertIn(token, text, relative)
        roadmap = (ROOT / "docs/roadmap/PRODUCTION_ROADMAP.md").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "- [x] **Step 24 — Correction Packet Construction and Integrity 1A**",
            roadmap,
        )
        self.assertIn(
            "- [x] **Step 25 — Draft V2 Generation and Layered Claim Verifier 1A**",
            roadmap,
        )
        self.assertIn(
            "- [x] **Step 26 — Verified Answer Assembly and Fail-Closed Output 1A**",
            roadmap,
        )
        self.assertIn(
            "- [x] **Step 27 — Personal Memory HAT Persistence, Quotas and Model Bindings 1A**",
            roadmap,
        )
        self.assertIn(
            "- [x] **Step 28 — Knowledge Hub and Critic Prompt Loop Correction Candidate Bridge 1A**",
            roadmap,
        )
        self.assertIn(
            "- [x] **Step 29 — Personal Memory Patch Proposal and Evidence Validation 1A**",
            roadmap,
        )
        self.assertIn("- [x] **Step 30", roadmap)
        self.assertIn("- [x] **Step 31", roadmap)
        self.assertIn("- [x] **Step 32", roadmap)
        self.assertIn("- [x] **Step 33", roadmap)
        self.assertIn("- [x] **Step 34", roadmap)
        self.assertIn("- [x] **Step 35", roadmap)
        self.assertIn("- [x] **Step 36", roadmap)
        self.assertIn("- [x] **Step 37", roadmap)
        self.assertIn("- [x] **Step 38", roadmap)
        self.assertIn("- [x] **Step 39", roadmap)
        self.assertIn("- [x] **Step 40", roadmap)
        self.assertIn("- [ ] **Step 41", roadmap)
        self.assertIn("Step 39: COMPLETE AND PUSHED", roadmap)
        self.assertIn("Step 40: COMPLETE AND PUSHED", roadmap)
        self.assertIn("Step 41: NOT STARTED", roadmap)
        self.assertIn("Step 40 completion does not authorize Step 41.", roadmap)
        agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
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
        self.assertIn("Step 34: COMPLETE AND PUSHED", agents)
        self.assertIn("Step 35: COMPLETE AND PUSHED", agents)
        self.assertIn("Step 36: COMPLETE AND PUSHED", agents)
        self.assertIn("Step 37: COMPLETE AND PUSHED", agents)
        self.assertIn("Step 38: COMPLETE AND PUSHED", agents)
        self.assertIn("Step 39: COMPLETE AND PUSHED", agents)
        self.assertIn("Step 40: COMPLETE AND PUSHED", agents)
        self.assertIn("Step 41: NOT STARTED", agents)
        self.assertIn("Step 40 completion does not authorize Step 41.", agents)


if __name__ == "__main__":
    unittest.main()
