from __future__ import annotations

import unittest
from tests._support import REPOSITORY_ROOT


class Step13DocumentationTests(unittest.TestCase):
    def test_required_documents_exist(self):
        paths=("docs/architecture/GERMAN_LAW_HAT_SOURCE_AUTHORITY_POLICY_1A.md","docs/architecture/GERMAN_LAW_SOURCE_AUTHORITY_POLICY_1A.md","docs/architecture/GERMAN_LAW_TEMPORAL_POLICY_1A.md","docs/architecture/GERMAN_LAW_METADATA_ADAPTER_CONTRACT_1A.md","docs/adr/ADR-020-german-law-hat-source-authority-policy.md","docs/provenance/STEP_13_OFFICIAL_SOURCE_RESEARCH_1A.md","docs/operations/STEP_13_GERMAN_LAW_HAT_VALIDATION_1A.md","docs/audits/STEP_13_GERMAN_LAW_HAT_SOURCE_AUTHORITY_CLOSURE_1A.md")
        for path in paths: self.assertTrue((REPOSITORY_ROOT/path).is_file(),path)

    def test_boundaries_are_documented(self):
        text=(REPOSITORY_ROOT/"docs/architecture/GERMAN_LAW_HAT_SOURCE_AUTHORITY_POLICY_1A.md").read_text()
        for token in ("not a legal adviser", "Step 14", "Step 15", "does not claim a Python sandbox"):
            self.assertIn(token,text)

    def test_research_uses_official_sources(self):
        text=(REPOSITORY_ROOT/"docs/provenance/STEP_13_OFFICIAL_SOURCE_RESEARCH_1A.md").read_text()
        for host in ("gesetze-im-internet.de","bundestag.de","eur-lex.europa.eu","bundesverfassungsgericht.de","bundesgerichtshof.de","bundesarbeitsgericht.de","bverwg.de","bundesfinanzhof.de","bsg.bund.de"):
            self.assertIn(host,text)

    def test_no_forbidden_scenario_or_final_question(self):
        files=(REPOSITORY_ROOT/"config/hats/german-law-1.0.0.json",REPOSITORY_ROOT/"src/aioa_memory_kernel/german_law/hat.py")
        combined="\n".join(path.read_text().casefold() for path in files)
        self.assertNotIn("nachweisgesetz",combined)
        self.assertNotIn("final question",combined)

    def test_no_step13_migration(self):
        import json
        manifest=json.loads((REPOSITORY_ROOT/"sql/cockroachdb/migrations/manifest.json").read_text())
        self.assertFalse(any("step13" in row["migration_id"] for row in manifest["migrations"]))


if __name__ == "__main__": unittest.main()
