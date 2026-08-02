"""Step 16 closure-document and sanitized-evidence contracts."""

from __future__ import annotations

import json
import unittest

from tests._support import REPOSITORY_ROOT

from aioa_memory_kernel.contracts.serialization import canonical_sha256


ARCHITECTURE = REPOSITORY_ROOT / "docs/architecture/GERMAN_LAW_HAT_PUBLICATION_CORPUS_VERIFICATION_1A.md"
ADR = REPOSITORY_ROOT / "docs/adr/ADR-023-german-law-hat-publication-corpus-verification.md"
OPERATIONS = REPOSITORY_ROOT / "docs/operations/STEP_16_GERMAN_LAW_HAT_PUBLICATION_VALIDATION_1A.md"
EVIDENCE = REPOSITORY_ROOT / "docs/evidence/corpus/step16-german-law-hat-publication-summary.json"
CLOSURE = REPOSITORY_ROOT / "docs/audits/STEP_16_GERMAN_LAW_HAT_PUBLICATION_CORPUS_VERIFICATION_CLOSURE_1A.md"
ROADMAP = REPOSITORY_ROOT / "docs/roadmap/PRODUCTION_ROADMAP.md"
README = REPOSITORY_ROOT / "docs/README.md"
AGENTS = REPOSITORY_ROOT / "AGENTS.md"


class Step16DocumentationTests(unittest.TestCase):
    def test_required_documents_exist_and_are_substantive(self) -> None:
        for path in (ARCHITECTURE, ADR, OPERATIONS, EVIDENCE, CLOSURE):
            with self.subTest(path=path):
                self.assertTrue(path.is_file(), path)
                self.assertGreater(len(path.read_text(encoding="utf-8")), 500)

    def test_architecture_keeps_trusted_publication_and_parser_boundaries(self) -> None:
        text = ARCHITECTURE.read_text(encoding="utf-8")
        for phrase in (
            "Step 14",
            "Step 15",
            "Object Lock",
            "text projection",
            "not claim that the generic Step 11 parser understands ZIP or XML",
            "duplicate version",
            "Step 21 question-time temporal resolver",
            "does not implement retrieval",
            "not an arbitrary-code sandbox",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)

    def test_runbook_requires_plan_bound_confirmation_and_graceful_cleanup(self) -> None:
        text = OPERATIONS.read_text(encoding="utf-8")
        for phrase in (
            "--confirm-plan-digest",
            "--confirm-source-root-identity",
            "--confirm-step14-manifest-digest",
            "--confirm-step15-manifest-digest",
            "conditional no-overwrite",
            "force kill",
            "Step 17",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)

    def test_evidence_is_canonical_sanitized_and_records_real_boundaries(self) -> None:
        value = json.loads(EVIDENCE.read_text(encoding="utf-8"))
        digest = value.pop("evidence_digest")
        self.assertEqual(digest, canonical_sha256(value))
        self.assertEqual(value["verdict"], "PASS")
        self.assertEqual(value["boundaries"]["source_tree_writes"], 0)
        self.assertEqual(value["boundaries"]["s3_deletes"], 0)
        self.assertEqual(value["boundaries"]["model_calls"], 0)
        self.assertFalse(value["boundaries"]["step17_started"])
        self.assertFalse(value["controlled_validation"]["cleanup"]["force_kill_used"])
        self.assertGreater(value["publication"]["publication_candidates"], 0)
        rendered = json.dumps(value, sort_keys=True)
        for unsafe in ("/media/", "/home/", "aws_session_token", "aws_secret_access_key", "authorization"):
            with self.subTest(unsafe=unsafe):
                self.assertNotIn(unsafe, rendered.casefold())

    def test_closure_and_indexes_close_step16_without_starting_step17(self) -> None:
        text = CLOSURE.read_text(encoding="utf-8")
        for phrase in (
            "COMPLETE AND PUSHED",
            "source-tree writes, modifications, and deletions: 0",
            "force kill: no",
            "Step 17: NOT STARTED",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)
        roadmap = ROADMAP.read_text(encoding="utf-8")
        self.assertIn("Step 16: COMPLETE AND PUSHED at actual closure commit", roadmap)
        step16 = roadmap[roadmap.index("**Step 16") - 10 : roadmap.index("**Step 16") + 120]
        self.assertIn("[x]", step16)
        step17 = roadmap[roadmap.index("**Step 17") - 10 : roadmap.index("**Step 17") + 120]
        self.assertIn("[ ]", step17)
        self.assertIn("Step 16 trusted publication", AGENTS.read_text(encoding="utf-8"))
        self.assertIn("Step 17: NOT STARTED", AGENTS.read_text(encoding="utf-8"))
        self.assertIn("Step 16 closure record", README.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
