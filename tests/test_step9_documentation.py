"""Step 9 closure documentation and sanitized evidence contracts."""

from __future__ import annotations

import hashlib
import json
import re
import unittest
from pathlib import Path

from tests._support import REPOSITORY_ROOT


ROOT = REPOSITORY_ROOT
ROADMAP = ROOT / "docs" / "roadmap" / "PRODUCTION_ROADMAP.md"
ARCHITECTURE = (
    ROOT
    / "docs"
    / "architecture"
    / "SOURCE_REGISTRY_PROVENANCE_PUBLICATION_STATES_1A.md"
)
ADR = (
    ROOT
    / "docs"
    / "adr"
    / "ADR-014-source-registry-provenance-publication-boundary.md"
)
DEFERRAL = (
    ROOT
    / "docs"
    / "audits"
    / "STEP_7_STEP_8_EXPLICIT_DEFERRAL_2026_07_29.md"
)
CLOSURE = (
    ROOT
    / "docs"
    / "audits"
    / "STEP_9_SOURCE_REGISTRY_PROVENANCE_PUBLICATION_CLOSURE_1A.md"
)
EVIDENCE = (
    ROOT
    / "docs"
    / "evidence"
    / "cockroachdb-v26-2"
    / "step9-source-registry-validation.json"
)
MIGRATION = (
    ROOT
    / "sql"
    / "cockroachdb"
    / "migrations"
    / "0006_step9_source_registry_provenance_publication_states.sql"
)


class Step9DocumentationTests(unittest.TestCase):
    def test_required_step9_documents_exist(self) -> None:
        for path in (ARCHITECTURE, ADR, DEFERRAL, CLOSURE, EVIDENCE):
            with self.subTest(path=path):
                self.assertTrue(path.is_file())

    def test_documentation_index_links_complete_step9_package(self) -> None:
        index = (ROOT / "docs" / "README.md").read_text(encoding="utf-8")
        for relative in (
            "architecture/SOURCE_REGISTRY_PROVENANCE_PUBLICATION_STATES_1A.md",
            "adr/ADR-014-source-registry-provenance-publication-boundary.md",
            "audits/STEP_7_STEP_8_EXPLICIT_DEFERRAL_2026_07_29.md",
            "evidence/cockroachdb-v26-2/step9-source-registry-validation.json",
            "audits/STEP_9_SOURCE_REGISTRY_PROVENANCE_PUBLICATION_CLOSURE_1A.md",
        ):
            self.assertIn(relative, index)

    def test_new_local_links_resolve_inside_repository(self) -> None:
        documents = (
            ROOT / "docs" / "README.md",
            ROADMAP,
            ARCHITECTURE,
            ADR,
            DEFERRAL,
            CLOSURE,
        )
        for document in documents:
            text = document.read_text(encoding="utf-8")
            for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", text):
                if (
                    "://" in target
                    or target.startswith("#")
                    or target.startswith("mailto:")
                ):
                    continue
                relative = target.split("#", 1)[0]
                if not relative:
                    continue
                resolved = (document.parent / relative).resolve()
                self.assertTrue(resolved.is_relative_to(ROOT.resolve()))
                self.assertTrue(resolved.exists(), (document, target))

    def test_roadmap_records_exact_step7_through_step11_state(self) -> None:
        roadmap = ROADMAP.read_text(encoding="utf-8")
        for required in (
            "- [x] **Step 7 — S3 Snapshot Authority and Object Lock Adapter 1A**",
            "Step 7: COMPLETE AND PUSHED at actual closure commit",
            "- [x] **Step 8 — External Volume Runtime Adapter and Fail-Closed Policy 1A**",
            "Step 8: COMPLETE AND PUSHED at actual closure commit",
            "- [x] **Step 9 — Source Registry, Provenance and Publication States 1A**",
            "Step 9: COMPLETE AND PUSHED at actual closure commit",
            "- [x] **Step 10 — Idempotent S3–CockroachDB Ingestion Saga 1A**",
            "Step 10: COMPLETE AND PUSHED at actual closure commit",
            "- [x] **Step 11 — Generic Parsing, Normalization and Chunking Pipeline 1A**",
            "Step 11: COMPLETE AND PUSHED at actual closure commit",
            "- [x] **Step 12 — HAT Registry, Manifest Validation and Runtime Boundary 1A**",
            "Step 13: COMPLETE AND PUSHED at actual closure commit",
            "Step 14: COMPLETE AND PUSHED at actual closure commit",
            "Step 15: COMPLETE AND PUSHED at actual closure commit",
            "Step 16: COMPLETE AND PUSHED at actual closure commit",
        ):
            self.assertIn(required, roadmap)

    def test_deferral_reasons_are_exact_and_sanitized(self) -> None:
        text = DEFERRAL.read_text(encoding="utf-8")
        normalized = " ".join(text.split())
        self.assertIn("Step 7: DEFERRED BY USER — NOT COMPLETE", text)
        self.assertIn("Step 8: DEFERRED BY USER — NOT COMPLETE", text)
        self.assertIn(
            "AWS STS identity resolution succeeded previously, but S3 API "
            "activation remained unavailable with NotSignedUp. No S3 bucket "
            "or Object Lock implementation was completed.",
            normalized,
        )
        self.assertIn(
            "Step 8 remained outside the bounded deadline path. Step 0B "
            "exists, but the Step 8 production runtime adapter was not "
            "implemented.",
            normalized,
        )
        self.assertIsNone(re.search(r"\b\d{12}\b", text))
        self.assertNotIn("arn:aws", text.casefold())

    def test_architecture_covers_contract_and_authority_boundaries(self) -> None:
        text = ARCHITECTURE.read_text(encoding="utf-8")
        normalized = " ".join(text.split())
        for required in (
            "`memory_patch.source_registry_entries`",
            "`memory_patch.source_provenance_edges`",
            "`memory_patch.source_publication_events`",
            "RLS and FORCE RLS",
            "`SOURCE_REGISTER`",
            "`PROVENANCE_EDGE_APPEND`",
            "`PUBLICATION_STATE_TRANSITION`",
            "previous-event digest",
            "compare-and-set",
            "every edge and every root",
            "`MODEL`, `HAT`, and `CRITIC` are rejected",
            "no approval, commit, execution, or answer authority",
        ):
            self.assertIn(required, normalized)

    def test_step9_evidence_is_canonical_pinned_and_consistent(self) -> None:
        raw = EVIDENCE.read_bytes()
        value = json.loads(raw)
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
        self.assertEqual(raw, expected)
        self.assertEqual(value["status"], "PASS")
        self.assertEqual(
            value["archive_identity"]["archive_sha256"],
            "3c7de055c07f9101eb0f71b3f5e6b489b0fcf449d3d5a55bfe61eff4f935ce8f",
        )
        self.assertEqual(
            value["binary_identity"]["binary_sha256"],
            "a5392f4de2c7a2bd838a52b0dcde0d61dcecf2fb060a88b0771367309b5cbdcf",
        )
        migration_digest = hashlib.sha256(MIGRATION.read_bytes()).hexdigest()
        self.assertEqual(
            value["migration"]["migration_0006_sha256"],
            migration_digest,
        )
        manifest = json.loads(
            (
                ROOT / "sql" / "cockroachdb" / "migrations" / "manifest.json"
            ).read_text(encoding="utf-8")
        )
        step9 = next(
            migration
            for migration in manifest["migrations"]
            if migration["migration_id"]
            == "0006_step9_source_registry_provenance_publication_states"
        )
        self.assertEqual(step9["sha256"], migration_digest)
        self.assertEqual(value["migration"]["first_apply_count"], 6)
        self.assertEqual(value["migration"]["no_op_applied_count"], 0)
        self.assertEqual(value["migration"]["no_op_skipped_count"], 6)
        self.assertEqual(value["migration"]["reproduction_apply_count"], 6)
        self.assertTrue(value["migration"]["reproduction_digest_matched"])
        self.assertEqual(value["live"]["probe_count"], 48)
        self.assertEqual(value["live"]["pass_count"], 48)
        self.assertEqual(value["live"]["fail_count"], 0)
        self.assertEqual(
            value["validation"]["full_repository_regression_test_count"],
            516,
        )
        self.assertEqual(
            value["policy"]["policy_version"],
            "source-publication-eligibility-1a",
        )
        for required_true in (
            "databases_removed",
            "disposable_and_fixed_roles_removed",
            "owned_process_exited",
            "ports_closed",
            "temporary_store_removed",
        ):
            self.assertTrue(value["cleanup"][required_true])
        self.assertFalse(value["cleanup"]["force_kill_used"])
        self.assertFalse(value["cleanup"]["panic_detected"])

    def test_evidence_contains_no_local_or_secret_shaped_value(self) -> None:
        text = EVIDENCE.read_text(encoding="utf-8")
        lowered = text.casefold()
        for forbidden in (
            "postgresql://",
            "cockroachdb://",
            "arn:aws",
            "begin private key",
            "begin rsa private key",
        ):
            self.assertNotIn(forbidden, lowered)
        self.assertIsNone(re.search(r"/(?:home|media)/", text))
        self.assertIsNone(re.search(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b", text))
        self.assertIsNone(re.search(r"\b\d{12}\b", text))
        forbidden_keys = {
            "account_id",
            "bucket_name",
            "device_serial",
            "dsn",
            "filesystem_uuid",
            "password",
            "pid",
            "port",
            "presigned_url",
            "profile",
            "raw_log",
            "secret_key",
            "session_token",
            "username",
        }

        def walk(item: object) -> None:
            if isinstance(item, dict):
                self.assertTrue(forbidden_keys.isdisjoint(item))
                for nested in item.values():
                    walk(nested)
            elif isinstance(item, list):
                for nested in item:
                    walk(nested)

        walk(json.loads(text))

    def test_closure_records_recovery_validation_cleanup_and_scope(self) -> None:
        text = CLOSURE.read_text(encoding="utf-8")
        normalized = " ".join(text.split())
        for required in (
            "`18/18 PASS`",
            "fresh official archive",
            "`48 PASS / 0 FAIL`",
            "`516/516`",
            "no panic marker",
            "No AWS call was made.",
            "No Step 7 implementation",
            "Step 8 production adapter",
            "Step 10 ingestion behavior",
            "master-backup `.git`",
            "Step 7: DEFERRED — NOT COMPLETE",
            "Step 8: DEFERRED — NOT COMPLETE",
            "Step 9: COMPLETE AND PUSHED",
            "Step 10: NOT STARTED",
        ):
            self.assertIn(required, normalized)

    def test_agents_checkpoint_records_late_step7_closure(self) -> None:
        text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        self.assertNotIn(
            "The exact next implementation task is Step 7",
            text,
        )
        for required in (
            "Step 7 was completed after Step 9",
            "Step 8 external-volume runtime integration",
            "Step 10 durable ingestion orchestration",
            "Step 11 deterministic parsing",
            "Step 13 German Law HAT manifest",
            "complete in its intended closure commit",
            "Step 14 bounded corpus inventory",
            "Step 15 temporal and jurisdictional normalization",
            "`Step 16: Step 16 trusted publication complete and pushed at actual closure",
            "Step 17 deterministic Axis A routing",
            "Step 22 provider-neutral original-query-only",
            "Step 23 exact-span deterministic claim extraction",
            "Step 24 verified frozen Step 23 input binding",
            "Step 25 verified Correction Packet integrity gating",
            "Step 26 complete upstream integrity binding",
            "Step 30 exact owner-human approval",
            "Step 31 exact Step 30 ACTIVE-only retrieval",
            "Step 32 exact owner-scoped supersession",
            "Step 33 typed audit normalization",
            "Step 34 typed Step 26/32 review-case intake",
            "Step 36: COMPLETE AND PUSHED",
            "Step 37: COMPLETE AND PUSHED",
            "Step 38: COMPLETE AND PUSHED",
            "Step 39: COMPLETE AND PUSHED",
            "Step 40: COMPLETE AND PUSHED",
            "Step 41: NOT STARTED",
            "Step 40 completion does not authorize Step 41.",
        ):
            self.assertIn(required, text)


if __name__ == "__main__":
    unittest.main()
