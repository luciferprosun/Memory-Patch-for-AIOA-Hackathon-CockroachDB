"""Static Step 8 architecture, runbook, and scope-boundary tests."""

from __future__ import annotations

import ast
import hashlib
import json
import unittest
from pathlib import Path

from tests._support import REPOSITORY_ROOT


ARCHITECTURE = (
    REPOSITORY_ROOT
    / "docs"
    / "architecture"
    / "EXTERNAL_VOLUME_RUNTIME_ADAPTER_FAIL_CLOSED_POLICY_1A.md"
)
ADR = (
    REPOSITORY_ROOT
    / "docs"
    / "adr"
    / "ADR-016-external-volume-runtime-fail-closed-boundary.md"
)
RUNBOOK = (
    REPOSITORY_ROOT
    / "docs"
    / "operations"
    / "STEP_8_EXTERNAL_VOLUME_LIVE_VALIDATION_1A.md"
)
CLOSURE = (
    REPOSITORY_ROOT
    / "docs"
    / "audits"
    / "STEP_8_EXTERNAL_VOLUME_RUNTIME_INTEGRATION_CLOSURE_1A.md"
)
EVIDENCE = (
    REPOSITORY_ROOT
    / "docs"
    / "evidence"
    / "external-volume"
    / "step8-external-volume-validation.json"
)
EXTERNAL_CONTRACT = REPOSITORY_ROOT / "docs" / "EXTERNAL_DATA_VOLUME.md"
README = REPOSITORY_ROOT / "docs" / "README.md"
ROADMAP = REPOSITORY_ROOT / "docs" / "roadmap" / "PRODUCTION_ROADMAP.md"
AGENTS = REPOSITORY_ROOT / "AGENTS.md"
CONFIG_EXAMPLE = REPOSITORY_ROOT / "config" / "external-data.env.example"
RUNTIME_SOURCE = (
    REPOSITORY_ROOT
    / "src"
    / "aioa_memory_kernel"
    / "runtime"
    / "external_volume_linux.py"
)
VALIDATION_SCRIPT = (
    REPOSITORY_ROOT / "scripts" / "run_external_volume_validation.py"
)
STEP7_FIXTURE = (
    REPOSITORY_ROOT / "tests" / "fixtures" / "step7_live_validation_snapshot.json"
)
STEP7_SHA256 = "d1bedd6275072d01cc932af7a57d826837e27b9c7d1039bb558d4039abf819fc"


def imported_module_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    return imported


class Step8DocumentationTests(unittest.TestCase):
    def test_architecture_records_all_runtime_identity_checks(self) -> None:
        text = ARCHITECTURE.read_text(encoding="utf-8")
        required = (
            "findmnt",
            "block device",
            "UUID",
            "label",
            "transport",
            "`rw`, `nodev`, and `nosuid`",
            "system root",
            "20 GiB",
            "marker",
            "every prepared directory",
        )
        for phrase in required:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)

    def test_architecture_defines_operation_specific_no_fallback_behavior(
        self,
    ) -> None:
        text = ARCHITECTURE.read_text(encoding="utf-8")
        for operation in (
            "CORPUS_REPLICA",
            "EMBEDDING_CACHE",
            "INDEX_CACHE",
            "INGESTION_STAGING",
            "PACKAGE_CACHE",
            "APPLICATION_SNAPSHOT_STAGING",
            "DATABASE_EXPORT",
            "BACKUP",
            "VALIDATION_EVIDENCE",
        ):
            with self.subTest(operation=operation):
                self.assertIn(f"`{operation}`", text)
        self.assertIn("DISABLE_OPERATION_WITHOUT_FALLBACK", text)
        self.assertIn("No operation can receive an internal-disk fallback", text)

    def test_architecture_records_symlink_special_file_and_atomic_policy(
        self,
    ) -> None:
        text = ARCHITECTURE.read_text(encoding="utf-8")
        for phrase in (
            "`O_NOFOLLOW`",
            "FIFOs",
            "`O_EXCL`",
            "file-`fsync`",
            "directory-`fsync`",
            "existing target cannot",
            "incomplete Step 8 staging artifact",
            "stops without deleting",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)

    def test_adr_preserves_authority_and_step_boundaries(self) -> None:
        text = ADR.read_text(encoding="utf-8")
        for phrase in (
            "EXTERNAL_DERIVED",
            "STORAGE_EVIDENCE_ONLY",
            "no delete or overwrite operation",
            "Step 10",
            "credentials",
            "CockroachDB node",
            "HAT runtime",
            "UI",
            "AOIA-Core",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)

    def test_runbook_has_read_only_preflight_and_explicit_write_gate(self) -> None:
        text = RUNBOOK.read_text(encoding="utf-8")
        required = (
            "--preflight",
            "PREFLIGHT_PASS_NO_WRITE",
            "Mandatory operator gate",
            "--write-validation",
            "--confirm-project memory-patch-for-aioa",
            "--confirm-device-reference",
            "LIVE_VALIDATION_PASS",
            "ALREADY_VALID_NO_WRITE",
        )
        for phrase in required:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)

    def test_runbook_fixed_plan_matches_step7_fixture(self) -> None:
        payload = STEP7_FIXTURE.read_bytes()
        self.assertEqual(len(payload), 88)
        self.assertEqual(hashlib.sha256(payload).hexdigest(), STEP7_SHA256)
        text = RUNBOOK.read_text(encoding="utf-8")
        self.assertIn(STEP7_SHA256, text)
        self.assertIn("content length: 88 bytes", text)
        self.assertIn("atomic create, no overwrite", text)

    def test_generic_config_adds_transport_and_capacity_without_local_values(
        self,
    ) -> None:
        text = CONFIG_EXAMPLE.read_text(encoding="utf-8")
        for name in (
            "AIOA_EXTERNAL_DEVICE_TRANSPORT",
            "AIOA_EXTERNAL_MINIMUM_FREE_BYTES",
            "AIOA_EXTERNAL_RESERVE_PERCENT",
            "AIOA_EXTERNAL_MAXIMUM_ATOMIC_WRITE_BYTES",
        ):
            with self.subTest(name=name):
                self.assertIn(name, text)
        self.assertIn("replace-with-verified-transport", text)
        self.assertIn('AIOA_EXTERNAL_MOUNTPOINT="/absolute/mountpoint"', text)

    def test_linux_probe_is_separate_and_never_uses_a_shell(self) -> None:
        imported = imported_module_roots(RUNTIME_SOURCE)
        self.assertIn("subprocess", imported)
        source = RUNTIME_SOURCE.read_text(encoding="utf-8")
        self.assertIn('"findmnt"', source)
        self.assertIn('"lsblk"', source)
        self.assertNotIn("shell=True", source)
        self.assertNotIn("check_output", source)

    def test_validation_script_has_fixed_target_and_two_confirmations(self) -> None:
        text = VALIDATION_SCRIPT.read_text(encoding="utf-8")
        self.assertIn(STEP7_SHA256, text)
        self.assertIn("VALIDATION_LENGTH = 88", text)
        self.assertIn("--confirm-project", text)
        self.assertIn("--confirm-device-reference", text)
        self.assertIn("os.path.lexists", text)
        self.assertIn("ALREADY_VALID_NO_WRITE", text)
        self.assertNotIn("shell=True", text)

    def test_external_contract_and_index_link_step8_documents(self) -> None:
        contract = EXTERNAL_CONTRACT.read_text(encoding="utf-8")
        index = README.read_text(encoding="utf-8")
        self.assertIn("## Step 8 runtime boundary", contract)
        self.assertIn(
            "EXTERNAL_VOLUME_RUNTIME_ADAPTER_FAIL_CLOSED_POLICY_1A.md",
            contract,
        )
        self.assertIn(
            "STEP_8_EXTERNAL_VOLUME_LIVE_VALIDATION_1A.md",
            contract,
        )
        self.assertIn(
            "EXTERNAL_VOLUME_RUNTIME_ADAPTER_FAIL_CLOSED_POLICY_1A.md",
            index,
        )
        self.assertIn(
            "STEP_8_EXTERNAL_VOLUME_LIVE_VALIDATION_1A.md",
            index,
        )
        for name in (
            "step8-external-volume-validation.json",
            "STEP_8_EXTERNAL_VOLUME_RUNTIME_INTEGRATION_CLOSURE_1A.md",
        ):
            with self.subTest(name=name):
                self.assertIn(name, contract)
                self.assertIn(name, index)

    def test_closure_evidence_is_canonical_sanitized_and_complete(self) -> None:
        raw = EVIDENCE.read_bytes()
        payload = json.loads(raw)
        canonical = (
            json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
            + b"\n"
        )
        self.assertEqual(raw, canonical)
        self.assertEqual(payload["status"], "PASS")
        self.assertEqual(payload["live"]["content_length"], 88)
        self.assertEqual(
            payload["live"]["content_sha256"],
            STEP7_SHA256,
        )
        self.assertTrue(payload["live"]["atomic_no_replace"])
        self.assertTrue(payload["live"]["exact_read_back"])
        self.assertEqual(payload["live"]["target_mode"], "0600")
        self.assertEqual(
            payload["live"]["target_state_after"],
            "COMPLETE_VALIDATION_ARTIFACT",
        )
        self.assertEqual(
            payload["live"]["incomplete_atomic_artifact_count"],
            0,
        )
        self.assertFalse(payload["live"]["system_drive_fallback_allowed"])
        self.assertFalse(payload["scope"]["step10_started"])
        self.assertEqual(payload["validation"]["step8_targeted"], "46/46")
        self.assertEqual(payload["validation"]["full_suite"], "633/633")

        tracked_evidence = (
            raw + b"\n" + CLOSURE.read_bytes()
        ).decode("utf-8")
        for forbidden in (
            "/media/",
            "/home/",
            "/dev/",
            '"device_label"',
            '"device_path"',
            '"mountpoint"',
            "password=",
            "secret=",
            "token=",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, tracked_evidence)
        self.assertNotRegex(
            tracked_evidence,
            (
                r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-"
                r"[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                r"[0-9a-fA-F]{12}\b"
            ),
        )

    def test_step8_history_is_preserved_after_step10_closure(self) -> None:
        roadmap = ROADMAP.read_text(encoding="utf-8")
        agents = AGENTS.read_text(encoding="utf-8")
        closure = CLOSURE.read_text(encoding="utf-8")
        self.assertIn("- [x] **Step 8", roadmap)
        self.assertIn(
            "`Step 8: COMPLETE AND PUSHED at actual closure commit`",
            roadmap,
        )
        self.assertIn(
            "Step 8 external-volume runtime integration",
            agents,
        )
        self.assertIn("IMPLEMENTATION AND LIVE VALIDATION COMPLETE", closure)
        self.assertIn("Step 8: COMPLETE AND PUSHED", closure)
        self.assertIn("- [x] **Step 10", roadmap)
        self.assertIn(
            "`Step 10: COMPLETE AND PUSHED at actual closure commit`",
            roadmap,
        )
        self.assertIn("Step 10 durable ingestion orchestration", agents)
        self.assertIn("Step 11 deterministic parsing", agents)
        self.assertIn("Step 13 German Law HAT manifest", agents)
        self.assertIn("complete in its intended closure commit", agents)
        self.assertIn("Step 14 bounded corpus inventory", agents)
        self.assertIn("`Step 15: NOT STARTED`", agents)
        self.assertIn("Step 10: NOT STARTED", closure)


if __name__ == "__main__":
    unittest.main()
