"""Step 15 closure-document and sanitized-evidence contracts."""

from __future__ import annotations

import json
import unittest

from tests._support import REPOSITORY_ROOT

from aioa_memory_kernel.contracts.serialization import canonical_sha256


ARCHITECTURE = REPOSITORY_ROOT / "docs/architecture/GERMAN_LAW_TEMPORAL_JURISDICTIONAL_NORMALIZATION_1A.md"
ADR = REPOSITORY_ROOT / "docs/adr/ADR-022-german-law-temporal-jurisdictional-normalization.md"
OPERATIONS = REPOSITORY_ROOT / "docs/operations/STEP_15_GERMAN_LAW_TEMPORAL_JURISDICTIONAL_VALIDATION_1A.md"
EVIDENCE = REPOSITORY_ROOT / "docs/evidence/corpus/step15-german-law-temporal-jurisdictional-summary.json"
CLOSURE = REPOSITORY_ROOT / "docs/audits/STEP_15_GERMAN_LAW_TEMPORAL_JURISDICTIONAL_NORMALIZATION_CLOSURE_1A.md"
ROADMAP = REPOSITORY_ROOT / "docs/roadmap/PRODUCTION_ROADMAP.md"
README = REPOSITORY_ROOT / "docs/README.md"
AGENTS = REPOSITORY_ROOT / "AGENTS.md"


class Step15DocumentationTests(unittest.TestCase):
    def test_required_documents_exist_and_are_substantive(self) -> None:
        for path in (ARCHITECTURE, ADR, OPERATIONS, EVIDENCE, CLOSURE):
            with self.subTest(path=path):
                self.assertTrue(path.is_file(), path)
                self.assertGreater(len(path.read_text(encoding="utf-8")), 500)

    def test_architecture_keeps_temporal_jurisdiction_and_authority_boundaries(self) -> None:
        text = ARCHITECTURE.read_text(encoding="utf-8")
        for phrase in (
            "`O_NOFOLLOW`",
            "`DE_FEDERAL`",
            "`DE_STATE`",
            "`EU`",
            "German language",
            "current clock",
            "Near duplicates",
            "Step 9 remains",
            "Step 16 publication/verification",
            "Step 21 temporal resolver",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)

    def test_adr_documents_no_overwrite_correction_and_deferred_authority(self) -> None:
        text = ADR.read_text(encoding="utf-8")
        for phrase in (
            "no-overwrite",
            "current clock",
            "Step 16 owns publication",
            "german-law-temporal-normalization-1a.1",
            "29 records",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)

    def test_runbook_requires_digest_confirmations_and_graceful_cleanup(self) -> None:
        text = OPERATIONS.read_text(encoding="utf-8")
        for phrase in (
            "--confirm-plan-digest",
            "--confirm-step15-manifest-digest",
            "--confirm-source-tree-digest",
            "force kill",
            "no-overwrite",
            "Step 16",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)

    def test_evidence_is_canonical_sanitized_and_matches_successful_validation(self) -> None:
        value = json.loads(EVIDENCE.read_text(encoding="utf-8"))
        digest = value.pop("evidence_digest")
        self.assertEqual(digest, canonical_sha256(value))
        self.assertEqual(value["verdict"], "PASS")
        self.assertEqual(value["normalization_proposals"]["count"], 6134)
        self.assertFalse(value["normalization_proposals"]["automatic_update_allowed"])
        self.assertEqual(value["source_registry_compatibility"]["published_sources"], 0)
        self.assertFalse(value["cleanup"]["force_kill_used"])
        rendered = json.dumps(value, sort_keys=True)
        for unsafe in ("/media/", "/home/", "aws_session_token", "aws_secret_access_key", "authorization"):
            with self.subTest(unsafe=unsafe):
                self.assertNotIn(unsafe, rendered.casefold())

    def test_closure_records_real_run_repair_and_no_step16(self) -> None:
        text = CLOSURE.read_text(encoding="utf-8")
        for phrase in (
            "6,134",
            "6,124 document identities",
            "24 review-only version relationships",
            "Source-tree writes, modifications, and deletions",
            "were all zero.",
            "force kill: no",
            "STEP15_REGISTRY_ROW_MISSING",
            "Step 16: NOT STARTED",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)

    def test_current_indexes_preserve_completed_step_order(self) -> None:
        roadmap = ROADMAP.read_text(encoding="utf-8")
        self.assertIn("Step 15: COMPLETE AND PUSHED at actual closure commit", roadmap)
        self.assertIn("- [x] **Step 15", roadmap)
        self.assertIn("- [x] **Step 16", roadmap)
        agents = AGENTS.read_text(encoding="utf-8")
        self.assertIn("Step 15 temporal and jurisdictional normalization", agents)
        self.assertIn("Step 17 deterministic Axis A routing", agents)
        self.assertIn("Step 19 immutable local-model embeddings", agents)
        self.assertIn("Step 20 verified Step 18/19 input binding", agents)
        self.assertIn("Step 21 verified Step 20 bundle binding", agents)
        self.assertIn("Step 22 provider-neutral original-query-only", agents)
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
        self.assertIn("Step 34: COMPLETE AND PUSHED", agents)
        self.assertIn("Step 35: COMPLETE AND PUSHED", agents)
        self.assertIn("Step 36: COMPLETE AND PUSHED", agents)
        self.assertIn("Step 37: COMPLETE AND PUSHED", agents)
        self.assertIn("Step 38: NOT STARTED", agents)
        self.assertIn("- [x] **Step 36", roadmap)
        self.assertIn("- [x] **Step 37", roadmap)
        self.assertIn("- [ ] **Step 38", roadmap)
        readme = README.read_text(encoding="utf-8")
        self.assertIn("Step 15 closure record", readme)
        self.assertIn("STEP_15_GERMAN_LAW_TEMPORAL_JURISDICTIONAL_VALIDATION_1A.md", readme)


if __name__ == "__main__":
    unittest.main()
