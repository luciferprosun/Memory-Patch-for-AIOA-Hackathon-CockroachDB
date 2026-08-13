"""Step 43 final documentation, demo, and submission contract tests."""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from aioa_memory_kernel.contracts.serialization import canonical_json, canonical_sha256
from aioa_memory_kernel.security.redaction import assert_secret_free
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "docs/demo"))
import run_step43_demo as step43  # noqa: E402


class Step43DocumentationDemoTest(unittest.TestCase):
    def test_replay_revalidates_frozen_live_lineage_and_zero_authority(self):
        result = step43.build_replay_validation()
        self.assertEqual(result["step"], 43)
        self.assertEqual(result["base_sha"], step43.STEP42_BASE_SHA)
        self.assertEqual(
            result["status"], "PASS_DOCUMENTATION_DEMO_SUBMISSION_REPLAY"
        )
        self.assertTrue(result["closure_eligible"])
        self.assertEqual(
            result["replay_demo_validation_status"],
            "PASS_REPLAY_NOT_A_NEW_LIVE_PROVIDER_VALIDATION",
        )
        self.assertEqual(result["verified_answer_status"], "VERIFIED_ANSWER")
        self.assertEqual(result["provider_calls_observed"]["step43_replay"], 0)
        self.assertEqual(result["network_calls"], 0)
        self.assertEqual(result["database_processes_started"], 0)
        self.assertEqual(result["production_resources_touched"], 0)
        self.assertTrue(result["rc_runtime_semantics_changed"])
        self.assertTrue(
            result["rc_restore_reference"]["historical_frozen_rc_reference"]
        )
        self.assertNotEqual(
            result["rc_restore_reference"]["frozen_runtime_content_digest"],
            result["rc_restore_reference"]["current_runtime_content_digest"],
        )
        self.assertEqual(result["secret_leakage_count"], 0)
        self.assertEqual(result["broken_documentation_links"], 0)
        self.assertTrue(result["submission_artifacts_verified"])
        self.assertEqual(
            result["validation_digest"],
            canonical_sha256(result, exclude_fields=("validation_digest",)),
        )

        authority = result["security_spot_check"]
        self.assertEqual(authority["browser_provider_key_count"], 0)
        self.assertEqual(authority["cross_tenant_or_owner_leakage"], 0)
        self.assertFalse(authority["personal_memory_canonical_evidence_authority"])
        self.assertEqual(authority["critic_authority_escalation_success"], 0)
        self.assertEqual(authority["approval_or_commit_helper_bypass"], 0)
        assert_secret_free(
            result,
            surface="Step43 replay test",
            reject_machine_paths=True,
        )

    def test_exact_primary_backup_supported_and_fail_closed_cases_are_frozen(self):
        suite = json.loads(
            (ROOT / "tests/fixtures/step38_german_law_cases.json").read_text(
                encoding="utf-8"
            )
        )
        cases = {case["case_id"]: case for case in suite["cases"]}
        self.assertEqual(
            cases[step43.PRIMARY_CASE_ID]["question"],
            "Vervollständige den Satz zur BMJErnAnO: „Diese Anordnung tritt am [Datum] in Kraft.“",
        )
        self.assertEqual(cases[step43.PRIMARY_CASE_ID]["expected_route"], "HAT_ASSIST")
        self.assertEqual(
            cases[step43.PRIMARY_CASE_ID]["expected_final_output"],
            "VERIFIED_ANSWER",
        )
        self.assertEqual(
            cases[step43.BACKUP_CASE_ID]["expected_correction_condition"],
            "SPECIAL_CASE_RESERVATION_NEGATION_MUST_BE_REMOVED",
        )
        self.assertEqual(
            cases[step43.SUPPORTED_CASE_ID]["expected_correction_condition"],
            "NO_MATERIAL_CORRECTION_REQUIRED",
        )
        for case_id in step43.FAIL_CLOSED_CASE_IDS:
            with self.subTest(case_id=case_id):
                self.assertEqual(
                    cases[case_id]["expected_final_output"],
                    "HUMAN_REVIEW_REQUIRED",
                )

    def test_live_mode_is_explicitly_cost_gated_and_at_most_two_calls(self):
        sentinel = "not-a-real-provider-secret"
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": sentinel}, clear=False):
            with self.assertRaisesRegex(
                step43.Step43ValidationError,
                "Step 43 demo validation failed",
            ):
                step43.run_live_observation(
                    {},
                    allow_live_provider_cost=False,
                )
            self.assertEqual(os.environ["OPENROUTER_API_KEY"], sentinel)
        self.assertEqual(step43.MAXIMUM_LIVE_PROVIDER_CALLS, 2)

    def test_live_classification_never_manufactures_a_defect(self):
        self.assertEqual(
            step43._classify_live_text(
                step43.PRIMARY_CASE_ID,
                "Diese Anordnung tritt am 1. Januar 2024 in Kraft.",
            ),
            "CORRECT_CANONICAL_DATE_BACKUP_CASE_REQUIRED",
        )
        self.assertEqual(
            step43._classify_live_text(
                step43.PRIMARY_CASE_ID,
                "Diese Anordnung tritt am 1. Januar 2025 in Kraft.",
            ),
            "NONCANONICAL_DATE_DEFECT_CANDIDATE",
        )
        self.assertEqual(
            step43._classify_live_text(
                step43.BACKUP_CASE_ID,
                step43.BACKUP_CANONICAL_TEXT,
            ),
            "CORRECT_EXACT_NO_MATERIAL_DEFECT",
        )
        self.assertEqual(
            step43._classify_live_text(
                step43.BACKUP_CASE_ID,
                step43.BACKUP_WRONG_TEXT,
            ),
            "WRONG_EXACT_MATERIAL_DEFECT",
        )
        self.assertEqual(
            step43._classify_live_text(step43.BACKUP_CASE_ID, "nicht"),
            "INVALID_FAIL_CLOSED",
        )

    def test_submission_documents_exist_and_every_internal_link_resolves(self):
        for relative in step43.SUBMISSION_ARTIFACTS:
            with self.subTest(relative=relative):
                path = ROOT / relative
                self.assertTrue(path.is_file())
                self.assertFalse(path.is_symlink())
                self.assertGreater(path.stat().st_size, 0)
        runner = ROOT / "docs/demo/run_step43_demo.py"
        self.assertTrue(runner.is_file())
        self.assertFalse(runner.is_symlink())
        checked, broken = step43._validate_markdown_links()
        self.assertGreater(checked, 40)
        self.assertEqual(broken, ())

    def test_docs_are_truthful_about_replay_memory_critic_and_live_text(self):
        golden = (
            ROOT / "docs/demo/MEMORY_PATCH_DEMO_GOLDEN_PATH_1A.md"
        ).read_text(encoding="utf-8")
        architecture = (
            ROOT / "docs/architecture/MEMORY_PATCH_SYSTEM_OVERVIEW_1A.md"
        ).read_text(encoding="utf-8")
        submission = (
            ROOT / "docs/submission/HACKATHON_SUBMISSION_PACKAGE_1A.md"
        ).read_text(encoding="utf-8")
        combined = "\n".join((golden, architecture, submission))
        self.assertIn("REPLAY — NOT A NEW LIVE PROVIDER VALIDATION", golden)
        self.assertIn("not represented as a verbatim live-provider transcript", golden)
        self.assertIn("canonical_evidence_authority=false", golden)
        self.assertIn("candidate-only", combined)
        self.assertIn("explicit owner approval", combined)
        self.assertIn("single-node", submission)
        self.assertNotIn("production HA is proven", combined)

    def test_committed_evidence_is_canonical_sanitized_and_bound(self):
        raw = step43.EVIDENCE_PATH.read_bytes()
        evidence = json.loads(raw.decode("utf-8", errors="strict"))
        self.assertEqual(raw, (canonical_json(evidence) + "\n").encode("utf-8"))
        self.assertEqual(evidence["step"], 43)
        self.assertEqual(evidence["base_sha"], step43.STEP42_BASE_SHA)
        self.assertTrue(evidence["closure_eligible"])
        self.assertEqual(
            evidence["validation_digest"],
            canonical_sha256(evidence, exclude_fields=("validation_digest",)),
        )
        self.assertEqual(evidence["secret_leakage_count"], 0)
        self.assertEqual(evidence["broken_documentation_links"], 0)
        self.assertTrue(evidence["submission_artifacts_verified"])
        current = step43.build_replay_validation()
        # The committed Step 43 digest remains historical evidence. The
        # post-roadmap runtime compatibility change updates this runner and its
        # tests, so the current submission surface must be validated afresh and
        # must not be misrepresented as the exact frozen Step 43 byte set.
        self.assertNotEqual(
            evidence["documentation"]["submission_artifact_digest"],
            current["documentation"]["submission_artifact_digest"],
        )
        self.assertTrue(current["submission_artifacts_verified"])
        self.assertEqual(current["broken_documentation_links"], 0)
        assert_secret_free(
            evidence,
            surface="Step43 committed evidence",
            reject_machine_paths=True,
        )

    def test_final_checkpoint_closes_the_numbered_roadmap_without_a_successor(self):
        roadmap = (ROOT / "docs/roadmap/PRODUCTION_ROADMAP.md").read_text(
            encoding="utf-8"
        )
        agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("- [x] **Step 43", roadmap)
        self.assertIn("Step 43: COMPLETE AND PUSHED", roadmap)
        self.assertIn("Step 43 is the final numbered roadmap step.", roadmap)
        self.assertIn("Step 43: COMPLETE AND PUSHED", agents)
        self.assertIn("Step 43 is the final numbered roadmap step.", agents)
        historical_open_state = "Step 43: NOT " + "STARTED"
        self.assertNotIn(historical_open_state, roadmap)
        self.assertNotIn(historical_open_state, agents)

    def test_closure_records_final_gates_without_pending_claims(self):
        closure = (
            ROOT
            / "docs/audits/STEP_43_DOCUMENTATION_DEMO_SUBMISSION_CLOSURE_1A.md"
        ).read_text(encoding="utf-8")
        evidence = json.loads(step43.EVIDENCE_PATH.read_text(encoding="utf-8"))
        self.assertIn("2199/2199 PASS", closure)
        self.assertIn(evidence["validation_digest"], closure)
        self.assertIn(
            evidence["documentation"]["submission_artifact_digest"], closure
        )
        self.assertIn("CANONICAL ROADMAP 0A–43 COMPLETE", closure)
        self.assertNotIn("PENDING_FINAL_" + "VALIDATION", closure)


if __name__ == "__main__":
    unittest.main()
