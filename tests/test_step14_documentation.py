"""Step 14 architecture, evidence, and closure-document checks."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from tests._support import REPOSITORY_ROOT

from aioa_memory_kernel.contracts.serialization import canonical_sha256


ARCHITECTURE = REPOSITORY_ROOT / "docs/architecture/GERMAN_LAW_CORPUS_INVENTORY_DEDUP_SOURCE_REGISTRATION_1A.md"
ADR = REPOSITORY_ROOT / "docs/adr/ADR-021-german-law-corpus-inventory-dedup-source-registration.md"
OPERATIONS = REPOSITORY_ROOT / "docs/operations/STEP_14_GERMAN_LAW_CORPUS_INVENTORY_VALIDATION_1A.md"
EVIDENCE = REPOSITORY_ROOT / "docs/evidence/corpus/step14-german-law-corpus-inventory-summary.json"
CLOSURE = REPOSITORY_ROOT / "docs/audits/STEP_14_GERMAN_LAW_CORPUS_INVENTORY_DEDUP_SOURCE_REGISTRATION_CLOSURE_1A.md"
ROADMAP = REPOSITORY_ROOT / "docs/roadmap/PRODUCTION_ROADMAP.md"


class Step14DocumentationTests(unittest.TestCase):
    def test_architecture_preserves_source_and_authority_boundaries(self) -> None:
        text = ARCHITECTURE.read_text(encoding="utf-8")
        for phrase in (
            "logically immutable",
            "O_NOFOLLOW",
            "raw SHA-256",
            "review candidates",
            "no delete, move, rename, hardlink",
            "Step 9 `REGISTERED` genesis",
            "does not implement temporal or jurisdictional normalization",
        ):
            self.assertIn(phrase, text)

    def test_adr_rejects_destructive_deduplication(self) -> None:
        text = ADR.read_text(encoding="utf-8")
        self.assertIn("Delet", text)
        self.assertIn("destroy provenance", text)
        self.assertIn("existing Step 9 registry", text)
        self.assertIn("No new database migration", text)

    def test_runbook_has_plan_gate_resume_and_disposable_validation(self) -> None:
        text = OPERATIONS.read_text(encoding="utf-8")
        self.assertIn("--plan", text)
        self.assertIn("--write-bundle", text)
        self.assertIn("--verify-bundle", text)
        self.assertIn("--confirm-plan-digest", text)
        self.assertIn("run_german_law_corpus_registration_validation.py", text)
        self.assertIn("force kill", text)

    def test_evidence_is_canonical_digest_bound_and_sanitized(self) -> None:
        value = json.loads(EVIDENCE.read_text(encoding="utf-8"))
        digest = value.pop("evidence_digest")
        self.assertEqual(digest, canonical_sha256(value))
        self.assertEqual(value["verdict"], "PASS")
        self.assertEqual(value["registration"]["real_candidates_registered"], 6124)
        self.assertEqual(value["registration"]["published_sources"], 0)
        self.assertFalse(value["cleanup"]["force_kill_used"])
        rendered = json.dumps(value, sort_keys=True)
        self.assertNotIn("/media/", rendered)
        self.assertNotIn("/home/", rendered)
        self.assertNotIn("aws_session_token", rendered.lower())

    def test_closure_records_actual_inventory_and_zero_source_mutation(self) -> None:
        text = CLOSURE.read_text(encoding="utf-8")
        for phrase in (
            "19,391 stable files",
            "1,963,389,627 bytes",
            "6,124 registration candidates",
            "source-tree writes, modifications, and deletions: 0",
            "force kill: no",
        ):
            self.assertIn(phrase, text)

    def test_roadmap_preserves_step14_and_closes_step15_without_step16(self) -> None:
        text = ROADMAP.read_text(encoding="utf-8")
        self.assertIn("Step 14: COMPLETE AND PUSHED at actual closure commit", text)
        self.assertIn("Step 15: COMPLETE AND PUSHED at actual closure commit", text)
        step15 = text[text.index("**Step 15") - 10 : text.index("**Step 15") + 100]
        self.assertIn("[x]", step15)
        step16 = text[text.index("**Step 16") - 10 : text.index("**Step 16") + 100]
        self.assertIn("[x]", step16)

    def test_no_raw_corpus_is_committed_and_step15_stays_domain_bound(self) -> None:
        paths = [path.relative_to(REPOSITORY_ROOT).as_posix() for path in REPOSITORY_ROOT.rglob("*") if path.is_file()]
        self.assertFalse(any("law_record.json" in path for path in paths))
        production = "\n".join(
            path.read_text(encoding="utf-8", errors="ignore")
            for path in (REPOSITORY_ROOT / "src").rglob("*.py")
        )
        self.assertIn("TemporalJurisdictionNormalizationEngine", production)
        self.assertNotIn("Nachweisgesetz", production)


if __name__ == "__main__":
    unittest.main()
