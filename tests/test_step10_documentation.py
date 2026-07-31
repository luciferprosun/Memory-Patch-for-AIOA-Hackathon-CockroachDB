"""Step 10 pre-closure architecture, ADR, and live-runbook contracts."""

from __future__ import annotations

import json
import re
import unittest

from tests._support import REPOSITORY_ROOT


ARCHITECTURE = (
    REPOSITORY_ROOT
    / "docs"
    / "architecture"
    / "IDEMPOTENT_S3_COCKROACHDB_INGESTION_SAGA_1A.md"
)
ADR = (
    REPOSITORY_ROOT
    / "docs"
    / "adr"
    / "ADR-017-idempotent-s3-cockroachdb-ingestion-saga-boundary.md"
)
RUNBOOK = (
    REPOSITORY_ROOT
    / "docs"
    / "operations"
    / "STEP_10_INGESTION_SAGA_LIVE_VALIDATION_1A.md"
)
CLOSURE = (
    REPOSITORY_ROOT
    / "docs"
    / "audits"
    / "STEP_10_IDEMPOTENT_S3_COCKROACHDB_INGESTION_SAGA_CLOSURE_1A.md"
)
FAILURE_EVIDENCE = (
    REPOSITORY_ROOT
    / "docs"
    / "evidence"
    / "ingestion"
    / "step10-ingestion-saga-validation-failure.json"
)
SUCCESS_EVIDENCE = (
    REPOSITORY_ROOT
    / "docs"
    / "evidence"
    / "ingestion"
    / "step10-ingestion-saga-validation.json"
)


class Step10DocumentationTests(unittest.TestCase):
    def test_required_closure_documents_and_evidence_exist(self) -> None:
        for path in (
            ARCHITECTURE,
            ADR,
            RUNBOOK,
            CLOSURE,
            FAILURE_EVIDENCE,
            SUCCESS_EVIDENCE,
        ):
            with self.subTest(path=path):
                self.assertTrue(path.is_file())

    def test_architecture_records_authority_and_transaction_boundaries(
        self,
    ) -> None:
        text = " ".join(ARCHITECTURE.read_text(encoding="utf-8").split())
        for phrase in (
            "CockroachDB is the durable orchestration authority",
            "S3 is immutable storage evidence only",
            "The external volume is derived staging only",
            "Step 9 remains the publication authority boundary",
            "No external call occurs inside a database transaction",
            "No distributed ACID transaction is claimed",
            "SQLSTATE `40001`",
            "RLS and FORCE RLS",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)

    def test_all_canonical_milestones_and_receipt_boundaries_are_documented(
        self,
    ) -> None:
        combined = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (ARCHITECTURE, ADR, RUNBOOK, CLOSURE)
        )
        for milestone in (
            "REGISTERED",
            "ACQUIRED_LOCAL",
            "HASH_VERIFIED",
            "SNAPSHOT_UPLOAD_PENDING",
            "SNAPSHOT_UPLOADED",
            "SNAPSHOT_LOCK_VERIFIED",
            "PARSED",
            "VALIDATED",
            "PUBLISHED",
        ):
            with self.subTest(milestone=milestone):
                self.assertIn(milestone, combined)
        self.assertIn("synthetic typed receipt", combined.casefold())
        self.assertIn("Concrete parsing", combined)
        self.assertIn("remain Step 11", combined)

    def test_runbook_contains_exact_gate_and_no_implicit_approval(self) -> None:
        text = RUNBOOK.read_text(encoding="utf-8")
        normalized = " ".join(text.split())
        for phrase in (
            "MULTI-SYSTEM WRITE GATE — STEP 10",
            "No final Step 10 multi-system write has been performed yet.",
            "Explicitly approve or reject the exact displayed plan.",
            "Do not run `--write-validation`",
            "No existing item is overwritten or deleted.",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(" ".join(phrase.split()), normalized)
        self.assertEqual(text.count("--confirm-plan-digest"), 1)

    def test_documents_record_step10_closure_without_claiming_step11(self) -> None:
        combined = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (ARCHITECTURE, ADR, RUNBOOK, CLOSURE)
        )
        self.assertIn("live-recovery validated", combined)
        self.assertIn("Step 10: COMPLETE AND PUSHED at actual closure commit", combined)
        self.assertIn("Step 11 remains not started", combined)
        for forbidden in (
            "Step 11: COMPLETE",
            "production parser implemented",
            "provides distributed ACID",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, combined)

    def test_documents_contain_no_machine_or_credential_material(self) -> None:
        combined = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (ARCHITECTURE, ADR, RUNBOOK, CLOSURE)
        )
        self.assertNotIn("/media/l/LSC_DATA", combined)
        self.assertNotIn(".local/external-data.env", combined)
        self.assertIsNone(re.search(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b", combined))
        self.assertIsNone(re.search(r"\b\d{12}\b", combined))
        for secret_name in (
            "aws_access_key_id",
            "aws_secret_access_key",
            "aws_session_token",
        ):
            self.assertNotIn(secret_name, combined.casefold())

    def test_success_and_failure_evidence_are_canonical_and_distinct(self) -> None:
        failure = json.loads(FAILURE_EVIDENCE.read_text(encoding="utf-8"))
        success = json.loads(SUCCESS_EVIDENCE.read_text(encoding="utf-8"))
        for path, value in (
            (FAILURE_EVIDENCE, failure),
            (SUCCESS_EVIDENCE, success),
        ):
            expected = (
                json.dumps(
                    value,
                    ensure_ascii=False,
                    allow_nan=False,
                    indent=2,
                    sort_keys=True,
                )
                + "\n"
            ).encode("utf-8")
            self.assertEqual(path.read_bytes(), expected)
        self.assertEqual(failure["status"], "FAILED_VALIDATION_NOT_COMMITTED")
        self.assertEqual(
            failure["first_root_cause"]["code"],
            "DISPOSABLE_RUNTIME_FORCE_KILL_USED",
        )
        self.assertEqual(success["status"], "PASS")
        self.assertEqual(success["mode"], "ZERO_EXTERNAL_WRITE_RECOVERY_VALIDATION")
        self.assertEqual(success["recovery"]["new_external_volume_writes"], 0)
        self.assertEqual(success["recovery"]["new_s3_writes"], 0)
        self.assertFalse(success["cleanup"]["force_kill_used"])
        self.assertTrue(success["cleanup"]["drain_command_completed"])

    def test_documentation_index_links_complete_step10_package(self) -> None:
        index = (REPOSITORY_ROOT / "docs" / "README.md").read_text(
            encoding="utf-8"
        )
        for relative in (
            "architecture/IDEMPOTENT_S3_COCKROACHDB_INGESTION_SAGA_1A.md",
            "adr/ADR-017-idempotent-s3-cockroachdb-ingestion-saga-boundary.md",
            "operations/STEP_10_INGESTION_SAGA_LIVE_VALIDATION_1A.md",
            "evidence/ingestion/step10-ingestion-saga-validation-failure.json",
            "evidence/ingestion/step10-ingestion-saga-validation.json",
            "audits/STEP_10_IDEMPOTENT_S3_COCKROACHDB_INGESTION_SAGA_CLOSURE_1A.md",
        ):
            self.assertIn(relative, index)


if __name__ == "__main__":
    unittest.main()
