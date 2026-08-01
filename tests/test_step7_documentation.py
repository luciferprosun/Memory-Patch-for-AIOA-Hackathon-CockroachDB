"""Step 7 architecture, deployment, and Step 8 handoff documentation tests."""

from __future__ import annotations

import hashlib
import json
import re
import unittest

from tests._support import REPOSITORY_ROOT


ROOT = REPOSITORY_ROOT
INDEX = ROOT / "docs" / "README.md"
ARCHITECTURE = (
    ROOT
    / "docs"
    / "architecture"
    / "S3_SNAPSHOT_AUTHORITY_OBJECT_LOCK_ADAPTER_1A.md"
)
ADR = (
    ROOT
    / "docs"
    / "adr"
    / "ADR-015-s3-snapshot-object-lock-and-cloudformation-boundary.md"
)
DEPLOYMENT = (
    ROOT
    / "docs"
    / "operations"
    / "STEP_7_S3_CLOUDFORMATION_DEPLOYMENT_1A.md"
)
HANDOFF = (
    ROOT
    / "docs"
    / "operations"
    / "STEP_8_READINESS_HANDOFF_FROM_STEP_7_1A.md"
)
TEMPLATE = (
    ROOT
    / "infra"
    / "cloudformation"
    / "step7-s3-snapshot-authority-1a.json"
)
CLOSURE = (
    ROOT
    / "docs"
    / "audits"
    / "STEP_7_S3_SNAPSHOT_AUTHORITY_OBJECT_LOCK_CLOSURE_1A.md"
)
EVIDENCE = (
    ROOT
    / "docs"
    / "evidence"
    / "aws-s3"
    / "step7-s3-snapshot-validation.json"
)
ROADMAP = ROOT / "docs" / "roadmap" / "PRODUCTION_ROADMAP.md"
DEFERRAL = (
    ROOT
    / "docs"
    / "audits"
    / "STEP_7_STEP_8_EXPLICIT_DEFERRAL_2026_07_29.md"
)
STEP9_CLOSURE = (
    ROOT
    / "docs"
    / "audits"
    / "STEP_9_SOURCE_REGISTRY_PROVENANCE_PUBLICATION_CLOSURE_1A.md"
)


class Step7DocumentationTests(unittest.TestCase):
    def test_required_documents_and_template_exist(self) -> None:
        for path in (
            ARCHITECTURE,
            ADR,
            DEPLOYMENT,
            HANDOFF,
            TEMPLATE,
            CLOSURE,
            EVIDENCE,
        ):
            with self.subTest(path=path):
                self.assertTrue(path.is_file())

    def test_documentation_index_links_step7_package(self) -> None:
        text = INDEX.read_text(encoding="utf-8")
        for relative in (
            "architecture/S3_SNAPSHOT_AUTHORITY_OBJECT_LOCK_ADAPTER_1A.md",
            "adr/ADR-015-s3-snapshot-object-lock-and-cloudformation-boundary.md",
            "operations/STEP_7_S3_CLOUDFORMATION_DEPLOYMENT_1A.md",
            "evidence/aws-s3/step7-s3-snapshot-validation.json",
            "audits/STEP_7_S3_SNAPSHOT_AUTHORITY_OBJECT_LOCK_CLOSURE_1A.md",
            "operations/STEP_8_READINESS_HANDOFF_FROM_STEP_7_1A.md",
        ):
            self.assertIn(relative, text)

    def test_new_local_links_resolve_inside_repository(self) -> None:
        for document in (
            INDEX,
            ARCHITECTURE,
            ADR,
            DEPLOYMENT,
            HANDOFF,
            CLOSURE,
        ):
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

    def test_architecture_preserves_authority_and_step9_boundaries(self) -> None:
        text = ARCHITECTURE.read_text(encoding="utf-8")
        normalized = " ".join(text.split())
        for required in (
            "S3 is storage and verification infrastructure only",
            "`S3_GLOBAL_LOCKED_SNAPSHOT`",
            "`S3_USER_PRIVATE_SNAPSHOT`",
            "`IfNoneMatch: *`",
            "`VersionId`",
            "`STORAGE_EVIDENCE_ONLY`",
            "can modify a Step 9 registry or publication record",
            "Step 9 was completed",
            "does not rewrite that history",
            "no deletion or cleanup API",
            "cross-system ACID",
        ):
            self.assertIn(required, normalized)

    def test_iac_decision_is_bounded_and_has_no_bootstrap(self) -> None:
        text = ADR.read_text(encoding="utf-8")
        normalized = " ".join(text.split())
        for required in (
            "The repository contained no infrastructure framework",
            "CDK v2 would add",
            "single native CloudFormation JSON template",
            "one Object-Lock-enabled bucket",
            "does not define IAM identities",
            "Step 8 remains unopened",
            "Step 10 orchestration",
        ):
            self.assertIn(required, normalized)
        self.assertIn("no CDK bootstrap", ARCHITECTURE.read_text(encoding="utf-8"))

    def test_deployment_plan_has_exact_gate_inputs_and_limits(self) -> None:
        text = DEPLOYMENT.read_text(encoding="utf-8")
        normalized = " ".join(text.split())
        for required in (
            "aioa-memory-patch-global-3f105fcd-eu-central-1",
            "`eu-central-1`",
            "`GOVERNANCE`, 7 days",
            "`BucketOwnerEnforced`",
            "`DeletionPolicy` and `UpdateReplacePolicy` retain",
            "--profile aoia-admin",
            "--region eu-central-1",
            "--on-stack-failure DO_NOTHING",
            "aws s3api put-object",
            "d1bedd6275072d01cc932af7a57d8268"
            "37e27b9c7d1039bb558d4039abf819fc",
            "change-set description must be inspected before execution",
            "No automatic lifecycle cleanup is configured",
        ):
            self.assertIn(required, normalized)
        self.assertIsNone(re.search(r"\b\d{12}\b", text))
        self.assertNotIn("arn:aws:iam", text.casefold())

    def test_least_privilege_documentation_excludes_mutating_escape_hatches(
        self,
    ) -> None:
        text = DEPLOYMENT.read_text(encoding="utf-8")
        normalized = " ".join(text.split())
        self.assertIn(
            "Neither deployment nor runtime policy should grant `s3:*`, "
            "`iam:*`, `s3:DeleteObject`, `s3:DeleteObjectVersion`, "
            "`s3:DeleteBucket`, or `s3:BypassGovernanceRetention`.",
            normalized,
        )
        self.assertIn(
            "The policy contains only explicit denies",
            normalized,
        )

    def test_step8_handoff_is_preparation_only(self) -> None:
        text = HANDOFF.read_text(encoding="utf-8")
        normalized = " ".join(text.split())
        for required in (
            "Step 8 remains `DEFERRED BY USER - NOT COMPLETE`",
            "${AIOA_EXTERNAL_MOUNTPOINT}/AIOA_DATA/Memory-Patch-for-AIOA",
            "auditing the existing Step 0B external-volume implementation",
            "no silent fallback to the system drive",
            "did not inspect broad external-drive contents",
            "Readiness verdict: `READY FOR STEP 8 AUDIT`",
            "authorizes only a separately requested audit",
        ):
            self.assertIn(required, normalized)
        self.assertNotIn("/media/", text)

    def test_step9_closure_is_not_rewritten_as_if_step7_existed(self) -> None:
        text = STEP9_CLOSURE.read_text(encoding="utf-8")
        self.assertIn("No AWS call was made. No Step 7 implementation", text)
        self.assertIn("Step 9: COMPLETE AND PUSHED", text)
        self.assertIn("Step 7: DEFERRED", text)
        self.assertEqual(
            hashlib.sha256(STEP9_CLOSURE.read_bytes()).hexdigest(),
            "9e5422405c9b8d37ddfadcc13a28baf1"
            "5638255460815e9a8def20fe6f6f7a87",
        )
        self.assertEqual(
            hashlib.sha256(DEFERRAL.read_bytes()).hexdigest(),
            "93d81df87ef83692c800fd01559baf31"
            "c851a7ad3f21981604d0fe7e39cb00b3",
        )

    def test_closure_evidence_is_canonical_sanitized_and_complete(self) -> None:
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
        self.assertEqual(value["stack"]["status"], "CREATE_COMPLETE")
        self.assertEqual(value["iac"]["resource_count"], 2)
        self.assertEqual(value["validation"]["full_suite"], "587/587")
        self.assertTrue(value["synthetic_snapshot"]["exact_version_read"])
        self.assertTrue(
            value["synthetic_snapshot"]["read_back_sha256_verified"]
        )
        self.assertEqual(
            value["authority_boundary"]["s3_authority_status"],
            "STORAGE_EVIDENCE_ONLY",
        )
        rendered = raw.decode("utf-8")
        self.assertIsNone(re.search(r"\b\d{12}\b", rendered))
        self.assertNotIn("arn:aws", rendered.casefold())

    def test_live_roadmap_and_agents_preserve_later_step_boundaries(self) -> None:
        roadmap = ROADMAP.read_text(encoding="utf-8")
        agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        closure = CLOSURE.read_text(encoding="utf-8")
        for text in (roadmap, agents, closure):
            self.assertIn("Step 7", text)
            self.assertIn("Step 8", text)
            self.assertIn("Step 10", text)
        self.assertIn(
            "- [x] **Step 7 — S3 Snapshot Authority and Object Lock Adapter 1A**",
            roadmap,
        )
        self.assertIn(
            "Step 8: COMPLETE AND PUSHED at actual closure commit",
            roadmap,
        )
        self.assertIn(
            "Step 10: COMPLETE AND PUSHED at actual closure commit",
            roadmap,
        )
        self.assertIn("Step 11: COMPLETE AND PUSHED at actual closure commit", roadmap)
        self.assertIn("Step 13: COMPLETE AND PUSHED at actual closure commit", roadmap)
        self.assertIn("Step 14: NOT STARTED", roadmap)
        self.assertIn("Step 7 was completed after Step 9", agents)
        self.assertIn(
            "Step 8 external-volume runtime integration",
            agents,
        )
        self.assertIn("Step 8 remains the next unopened production audit", closure)

    def test_new_documents_contain_no_secret_or_account_identifier(self) -> None:
        text = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (ARCHITECTURE, ADR, DEPLOYMENT, HANDOFF, CLOSURE)
        )
        self.assertIsNone(re.search(r"\b\d{12}\b", text))
        lowered = text.casefold()
        for forbidden in (
            "aws_access_key_id",
            "aws_secret_access_key",
            "aws_session_token",
            "begin private key",
        ):
            self.assertNotIn(forbidden, lowered)


if __name__ == "__main__":
    unittest.main()
