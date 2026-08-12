"""Provider-free German Law integration proof for the optional Step 39 bridge."""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from tests._support import REPOSITORY_ROOT

SCRIPTS = REPOSITORY_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import run_step39_critic_bridge_validation as validation  # noqa: E402

from aioa_memory_kernel.contracts.serialization import (  # noqa: E402
    canonical_json,
    canonical_sha256,
)
from aioa_memory_kernel.security import assert_secret_free  # noqa: E402


class Step39CriticGermanLawE2ETests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.report = validation.run_validation()

    def test_frozen_step38_live_evidence_and_german_law_lineage_are_bound(self) -> None:
        report = self.report
        anchor = report["step38_anchor"]
        german_law = report["german_law"]
        self.assertEqual(report["start_sha"], validation.STEP38_CLOSURE_SHA)
        self.assertEqual(
            anchor["validation_digest"], validation.STEP38_VALIDATION_DIGEST
        )
        self.assertEqual(
            anchor["verified_upstream_lineage_hash"],
            validation.STEP38_UPSTREAM_LINEAGE_HASH,
        )
        self.assertEqual(anchor["route_hash"], validation.STEP38_LIVE_ROUTE_HASH)
        self.assertEqual(
            anchor["draft_v1_hash"], validation.STEP38_LIVE_DRAFT_V1_HASH
        )
        self.assertEqual(
            anchor["evidence_bundle_hash"],
            validation.STEP38_LIVE_EVIDENCE_BUNDLE_HASH,
        )
        self.assertEqual(
            anchor["correction_packet_hash"], validation.STEP38_LIVE_PACKET_HASH
        )
        self.assertEqual(
            anchor["verified_answer_hash"],
            validation.STEP38_LIVE_VERIFIED_ANSWER_HASH,
        )
        self.assertEqual(german_law["hat_id"], "german-law")
        self.assertEqual(german_law["official_identifier"], "BJNR1330A0023")
        self.assertEqual(
            german_law["source_id"], "de-federal-gii-bjnr1330a0023"
        )
        self.assertEqual(
            german_law["question_digest"], validation.STEP38_QUESTION_DIGEST
        )
        self.assertTrue(
            german_law["step38_live_hashes_verified_from_committed_evidence"]
        )
        self.assertFalse(
            german_law["offline_core_reconstruction_matches_live_lineage"]
        )

    def test_optional_critic_never_changes_the_step38_core_projection(self) -> None:
        core = self.report["core_independence"]
        self.assertTrue(core["all_equal"])
        self.assertTrue(core["critic_optional"])
        self.assertEqual(
            {
                core["before_hash"],
                core["disabled_after_hash"],
                core["enabled_after_hash"],
                core["failure_after_hash"],
            },
            {core["before_hash"]},
        )

    def test_enabled_candidate_enters_step28_then_only_step29(self) -> None:
        candidate = self.report["accepted_candidate"]
        proposal = self.report["step29"]
        self.assertEqual(candidate["issue_type"], "TEMPORAL_MISMATCH")
        self.assertEqual(candidate["candidate_state"], "DETECTED")
        self.assertEqual(candidate["step28_intake_disposition"], "ACCEPTED")
        self.assertEqual(candidate["exact_replay_disposition"], "EXACT_REPLAY")
        self.assertEqual(candidate["durable_candidate_count"], 1)
        self.assertEqual(proposal["final_state"], "AWAITING_APPROVAL")
        self.assertTrue(proposal["human_owner_approval_required"])
        self.assertEqual(proposal["step30_calls"], 0)
        self.assertTrue(proposal["same_exact_dedup_key"])
        self.assertFalse(proposal["duplicate_validated"])
        self.assertEqual(proposal["duplicate_semantic_side_effects"], 0)

    def test_fail_closed_matrix_rejects_untrusted_identity_and_authority(self) -> None:
        matrix = self.report["case_matrix"]
        expected = {
            "critic_disabled": "DISABLED",
            "critic_enabled_issue": "ASSESSMENT_ACCEPTED",
            "critic_enabled_no_issue": "NO_ISSUE",
            "critic_provider_unavailable": "PROVIDER_UNAVAILABLE",
            "critic_transient_recovered": "ASSESSMENT_ACCEPTED",
            "critic_retry_exhausted": "PROVIDER_UNAVAILABLE",
            "critic_malformed_output": "INVALID_OUTPUT",
            "critic_fake_evidence": "INVALID_OUTPUT",
            "critic_source_spoof": "INVALID_OUTPUT",
            "critic_approval_spoof": "INVALID_OUTPUT",
        }
        for name, status in expected.items():
            with self.subTest(name=name):
                self.assertEqual(matrix[name], status)
        for name in (
            "critic_owner_spoof_denied",
            "critic_tenant_spoof_denied",
            "critic_route_spoof_denied",
        ):
            self.assertTrue(matrix[name])

    def test_step37_scripted_transient_failure_recovers_once_without_duplicate(self) -> None:
        recovery = self.report["failure_recovery"]
        self.assertEqual(recovery["test_only_injector"], "ScriptedFailureInjector")
        self.assertEqual(recovery["failure_point"], "PROVIDER_TRANSIENT_FAILURE")
        self.assertTrue(recovery["completion_unknown"])
        self.assertEqual(recovery["attempt_count"], 2)
        self.assertEqual(recovery["failure_hit_count"], 2)
        self.assertEqual(recovery["failure_emission_count"], 1)
        self.assertEqual(recovery["recovery_status"], "RECOVERED_BY_RETRY")
        self.assertEqual(recovery["durable_candidate_count"], 1)
        self.assertEqual(recovery["duplicate_semantic_side_effects"], 0)
        self.assertEqual(recovery["authority_violations"], 0)

    def test_receipt_gated_audit_is_hash_only_and_all_authority_is_false(self) -> None:
        audit = self.report["audit"]
        candidate = self.report["accepted_candidate"]
        self.assertTrue(audit["draft_verified"])
        self.assertTrue(audit["hash_only"])
        self.assertTrue(audit["chain_verified"])
        self.assertEqual(audit["event_count"], 1)
        self.assertEqual(len(audit["append_receipt_hash"]), 64)
        self.assertEqual(len(audit["event_hash"]), 64)
        self.assertEqual(
            audit["step28_intake_receipt_hash"],
            candidate["step28_intake_receipt_hash"],
        )
        self.assertEqual(self.report["authority"]["authority_escalations"], 0)
        for name, value in self.report["authority"].items():
            if name.endswith("_authority"):
                self.assertIs(value, False)

    def test_evidence_ready_contract_metadata_and_safety_fields_are_exact(self) -> None:
        contract = self.report["critic_contract"]
        provider = self.report["provider"]
        security = self.report["security"]
        self.assertEqual(contract["bridge_version"], validation.CRITIC_BRIDGE_VERSION)
        for name in ("prompt_id", "prompt_version", "prompt_digest"):
            self.assertEqual(contract[name], getattr(validation._german_law_critic_fixture(
                validation.primary_lineage()
            )["request"], "critic_" + name))
        self.assertEqual(
            provider["real_critic_provider_validation"],
            "UNAVAILABLE_NOT_REQUIRED",
        )
        self.assertFalse(provider["real_provider_called"])
        self.assertEqual(provider["network_calls"], 0)
        self.assertEqual(provider["credential_count"], 0)
        self.assertTrue(security["cross_user_denied"])
        self.assertTrue(security["cross_tenant_denied"])
        self.assertEqual(security["secret_leakage_count"], 0)
        self.assertEqual(self.report["resource_optimization"], 0)
        self.assertFalse(self.report["step40_started"])

    def test_validation_digest_is_canonical_and_result_is_secret_free(self) -> None:
        self.assertEqual(
            self.report["validation_digest"],
            canonical_sha256(self.report, exclude_fields=("validation_digest",)),
        )
        assert_secret_free(
            self.report,
            surface="Step 39 provider-free E2E test",
            reject_machine_paths=True,
        )
        rendered = canonical_json(self.report)
        fixture = validation._german_law_critic_fixture(validation.primary_lineage())
        for private_text in (
            fixture["request"].original_query,
            fixture["request"].artifacts[0].text,
            fixture["document"]["candidate_correction_text"],
            fixture["request"].evidence_references[0].snippet,
        ):
            self.assertNotIn(private_text, rendered)

    def test_committed_validation_evidence_is_exact_canonical_runner_output(self) -> None:
        path = (
            REPOSITORY_ROOT
            / "docs/evidence/critic/step39-critic-prompt-loop-bridge-validation.json"
        )
        raw = path.read_text(encoding="utf-8")
        self.assertEqual(raw, canonical_json(self.report) + "\n")
        evidence = json.loads(raw)
        self.assertEqual(evidence, self.report)
        self.assertEqual(
            evidence["validation_digest"],
            canonical_sha256(evidence, exclude_fields=("validation_digest",)),
        )
        self.assertEqual(evidence["status"], "PASS_PROVIDER_FREE_CONTROLLED")
        self.assertTrue(evidence["closure_eligible"])
        self.assertTrue(evidence["audit"]["chain_verified"])
        self.assertEqual(evidence["step29"]["final_state"], "AWAITING_APPROVAL")
        self.assertEqual(evidence["step29"]["step30_calls"], 0)
        self.assertFalse(evidence["step40_started"])
        assert_secret_free(
            evidence,
            surface="Step 39 committed validation evidence",
            reject_machine_paths=True,
        )

    def test_runner_is_provider_free_and_emits_one_canonical_json_line(self) -> None:
        script = SCRIPTS / "run_step39_critic_bridge_validation.py"
        completed = subprocess.run(
            [sys.executable, str(script)],
            cwd=REPOSITORY_ROOT,
            env={
                "PATH": os.environ.get("PATH", ""),
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONPATH": os.pathsep.join(
                    (str(REPOSITORY_ROOT / "src"), str(REPOSITORY_ROOT))
                ),
            },
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(len(completed.stdout.splitlines()), 1)
        parsed = json.loads(completed.stdout)
        self.assertEqual(parsed["status"], "PASS_PROVIDER_FREE_CONTROLLED")
        self.assertEqual(parsed["validation_digest"], self.report["validation_digest"])

    def test_runner_has_no_network_database_step30_or_secret_capability(self) -> None:
        path = SCRIPTS / "run_step39_critic_bridge_validation.py"
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        forbidden_modules = {
            "boto3",
            "httpx",
            "psycopg",
            "requests",
            "socket",
            "subprocess",
            "urllib",
        }
        forbidden_calls = {
            "activate_patch",
            "approve_personal_memory_patch",
            "commit_personal_memory_patch",
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                self.assertTrue(
                    forbidden_modules.isdisjoint(alias.name for alias in node.names)
                )
            elif isinstance(node, ast.ImportFrom):
                self.assertNotIn((node.module or "").split(".")[0], forbidden_modules)
            elif isinstance(node, ast.Call):
                called = (
                    node.func.id
                    if isinstance(node.func, ast.Name)
                    else node.func.attr
                    if isinstance(node.func, ast.Attribute)
                    else ""
                )
                self.assertNotIn(called, forbidden_calls)
        self.assertNotIn("OPENROUTER_API_KEY", source)

    def test_tampered_step38_evidence_anchor_fails_closed(self) -> None:
        with mock.patch.object(validation, "_file_sha256", return_value="0" * 64):
            with self.assertRaises(validation.ValidationFailure) as raised:
                validation._step38_anchor()
        self.assertEqual(
            raised.exception.code, "STEP38_EVIDENCE_FILE_DIGEST_MISMATCH"
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
