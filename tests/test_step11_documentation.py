"""Documentation and declared-boundary tests for Step 11."""

from __future__ import annotations

import json
import unittest

from tests._support import REPOSITORY_ROOT


ARCHITECTURE = REPOSITORY_ROOT / "docs/architecture/GENERIC_PARSING_NORMALIZATION_CHUNKING_PIPELINE_1A.md"
ADR = REPOSITORY_ROOT / "docs/adr/ADR-018-generic-parsing-normalization-chunking-boundary.md"
OPERATIONS = REPOSITORY_ROOT / "docs/operations/STEP_11_PARSING_PIPELINE_LIVE_VALIDATION_1A.md"
CLOSURE = REPOSITORY_ROOT / "docs/audits/STEP_11_GENERIC_PARSING_NORMALIZATION_CHUNKING_PIPELINE_CLOSURE_1A.md"
ROADMAP = REPOSITORY_ROOT / "docs/roadmap/PRODUCTION_ROADMAP.md"
AGENTS = REPOSITORY_ROOT / "AGENTS.md"
README = REPOSITORY_ROOT / "docs/README.md"
SECURITY = REPOSITORY_ROOT / "config/cockroachdb/parsing-pipeline-security-1a.json"
MIGRATION = REPOSITORY_ROOT / "sql/cockroachdb/migrations/0008_step11_generic_parsing_pipeline.sql"


class Step11DocumentationTests(unittest.TestCase):
    def test_required_architecture_adr_and_runbook_exist(self) -> None:
        for path in (ARCHITECTURE, ADR, OPERATIONS, CLOSURE):
            self.assertTrue(path.is_file(), path)
            self.assertGreater(len(path.read_text(encoding="utf-8")), 1000)

    def test_primary_standards_and_nfc_decision_are_explicit(self) -> None:
        text = ARCHITECTURE.read_text(encoding="utf-8")
        for value in (
            "https://www.unicode.org/reports/tr15/",
            "https://www.rfc-editor.org/rfc/rfc8259",
            "https://www.rfc-editor.org/rfc/rfc5646",
            "https://cheatsheetseries.owasp.org/",
            "NFC",
            "NFKC",
            "NFKD",
        ):
            self.assertIn(value, text)

    def test_supported_and_unsupported_media_are_exact(self) -> None:
        text = ARCHITECTURE.read_text(encoding="utf-8")
        self.assertIn("`text/plain`", text)
        self.assertIn("`application/json`", text)
        self.assertIn("`text/markdown`", text)
        self.assertIn("Unsupported", text)
        self.assertIn("PDF, OCR, Office", text)

    def test_offset_and_chunking_contract_is_documented(self) -> None:
        text = ARCHITECTURE.read_text(encoding="utf-8")
        for value in (
            "[start, end)",
            "NORMALIZED_UNICODE_CODE_POINTS_NFC",
            "1024",
            "896",
            "64 characters",
            "No model tokenizer",
            "Cross-section chunks",
        ):
            self.assertIn(value, text)

    def test_findings_are_evidence_not_authority(self) -> None:
        text = ARCHITECTURE.read_text(encoding="utf-8")
        self.assertIn("security evidence", text)
        self.assertIn("statement of intent", text)
        self.assertIn("INFO", text)
        self.assertIn("WARNING", text)
        self.assertIn("BLOCKING", text)
        self.assertIn("executes an instruction", ADR.read_text(encoding="utf-8"))

    def test_persistence_reuses_canonical_tables_and_defers_retrieval(self) -> None:
        text = ARCHITECTURE.read_text(encoding="utf-8")
        self.assertIn("`memory_patch.knowledge_versions`", text)
        self.assertIn("`memory_patch.knowledge_chunks`", text)
        self.assertIn("`memory_patch.parsed_documents`", text)
        self.assertIn("`memory_patch.parsed_sections`", text)
        self.assertIn("`memory_patch.parse_security_findings`", text)
        self.assertIn("`memory_patch.chunk_search_documents` is not populated", text)

    def test_security_manifest_matches_append_only_runtime_policy(self) -> None:
        manifest = json.loads(SECURITY.read_text(encoding="utf-8"))
        self.assertFalse(manifest["runtime_delete"])
        self.assertEqual(
            {item["table"] for item in manifest["tables"]},
            {"parsed_documents", "parsed_sections", "parse_security_findings"},
        )
        for item in manifest["tables"]:
            self.assertTrue(item["force_rls"])
            self.assertEqual(item["runtime_privileges"], ["INSERT", "SELECT"])
            self.assertIsNone(item["update_policy"])

    def test_migration_has_no_retrieval_or_vector_implementation(self) -> None:
        sql = MIGRATION.read_text(encoding="utf-8")
        self.assertNotIn("chunk_search_documents", sql)
        self.assertNotIn("VECTOR", sql.upper())
        self.assertNotIn("DELETE FROM", sql.upper())
        self.assertIn("FORCE ROW LEVEL SECURITY", sql)

    def test_live_runbook_is_zero_external_write_and_exact_version_bound(self) -> None:
        text = OPERATIONS.read_text(encoding="utf-8")
        for value in (
            "new S3 writes: 0",
            "new external-volume writes: 0",
            "deletions: 0",
            "retention changes: 0",
            "kfDFfBsGlAR_KoQxDodzESlhebuYpAMx",
            "--confirm-plan-digest",
            "synthetic_validation_boundary=false",
            "force kill is false",
        ):
            self.assertIn(value, text)

    def test_step9_and_step10_boundaries_are_preserved(self) -> None:
        text = ARCHITECTURE.read_text(encoding="utf-8")
        self.assertIn("Step 10 continues to own milestone progression", text)
        self.assertIn("Step 9 remains the only legal", text)
        self.assertIn("Neither adapter directly updates publication state", text)

    def test_no_step12_implementation_is_claimed(self) -> None:
        combined = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (ARCHITECTURE, ADR, OPERATIONS)
        )
        self.assertIn("Step 12", combined)
        self.assertNotIn("Step 12: COMPLETE", combined)
        self.assertNotIn("HAT registry implemented", combined)

    def test_closure_binds_success_evidence_and_zero_external_writes(self) -> None:
        text = CLOSURE.read_text(encoding="utf-8")
        for value in (
            "4eac1d5c4bb88c477f10f35eac49e4121c3457c9671897741a32e1a0444b254a",
            "new S3 writes: 0",
            "new external-volume writes: 0",
            "force kill: NO",
            "Step 12 remains not started",
        ):
            self.assertIn(value, text)

    def test_canonical_roadmap_preserves_step11_after_step12_closure(self) -> None:
        text = ROADMAP.read_text(encoding="utf-8")
        self.assertIn("[x] **Step 11", text)
        self.assertIn("[x] **Step 12", text)
        self.assertIn("Step 13: COMPLETE AND PUSHED at actual closure commit", text)
        self.assertIn("Step 14: COMPLETE AND PUSHED at actual closure commit", text)
        self.assertIn("Step 15: COMPLETE AND PUSHED at actual closure commit", text)
        self.assertIn("Step 16: NOT STARTED", text)

    def test_repository_indexes_preserve_step11_after_step12(self) -> None:
        agents = AGENTS.read_text(encoding="utf-8")
        self.assertIn("Step 13 German Law HAT manifest", agents)
        self.assertIn("complete in its intended closure commit", agents)
        self.assertIn("Step 14 bounded corpus inventory", agents)
        self.assertIn("Step 15 temporal and jurisdictional normalization", agents)
        self.assertIn("Step 16: NOT STARTED", agents)
        readme = README.read_text(encoding="utf-8")
        self.assertIn("Step 11 closure record", readme)
        self.assertIn("Step 12 closure record", readme)


if __name__ == "__main__":
    unittest.main()
