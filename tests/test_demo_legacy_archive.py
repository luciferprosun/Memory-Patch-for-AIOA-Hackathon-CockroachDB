"""D2 contract and provenance tests for the archival legacy surface."""

from __future__ import annotations

import dataclasses
import json
import unittest
from pathlib import Path

from aioa_memory_kernel.contracts.serialization import canonical_json, canonical_sha256
from aioa_memory_kernel.demo_cockpit import (
    AOIA_CORE_REPOSITORY,
    CockpitMode,
    CockpitShell,
    LegacyCompatibilityMode,
    LegacyObserverRole,
    LegacySourceClassification,
    build_default_cockpit_shell,
    build_legacy_archive_manifest,
)
from run_d2_legacy_critical_compatibility_validation import run_validation


class LegacyArchiveContractTests(unittest.TestCase):
    def test_manifest_is_hash_bound_and_contains_only_verified_metadata(self) -> None:
        manifest = build_legacy_archive_manifest()
        self.assertEqual(
            manifest.metadata_digest,
            canonical_sha256(manifest, exclude_fields=("metadata_digest",)),
        )
        self.assertEqual(manifest.source_repository, AOIA_CORE_REPOSITORY)
        self.assertEqual(len(manifest.source_references), 8)
        self.assertEqual(
            manifest.observer_roles,
            (
                LegacyObserverRole.LOGIC_AND_CLAIMS,
                LegacyObserverRole.SAFETY_AND_AUTHORITY,
                LegacyObserverRole.EVIDENCE_AND_CONSISTENCY,
            ),
        )
        serialized = canonical_json(manifest)
        for forbidden_field in (
            "prompt_text",
            "main_draft_text",
            "observer_output_text",
            "final_revision_text",
        ):
            self.assertNotIn(forbidden_field, serialized)
        self.assertEqual(
            manifest.exact_prompt_status,
            "NOT_FOUND_AS_VERSIONED_EXECUTION_INPUT",
        )
        self.assertEqual(
            manifest.replay_bundle_status,
            "NOT_CREATED_MISSING_HISTORICAL_BYTES",
        )

    def test_source_identity_and_runtime_rejections_are_explicit(self) -> None:
        manifest = build_legacy_archive_manifest()
        identities = {
            (source.source_commit, source.source_path): source
            for source in manifest.source_references
        }
        desktop = identities[
            (
                "5ec74f85256c260dadbc795143eb132b4119aab6",
                "apps/aoia_desktop_demo/critical_review.py",
            )
        ]
        self.assertEqual(
            desktop.git_blob_sha1,
            "07fabcf104eac4de63aa7f60d6af79fcdd7c37e4",
        )
        self.assertEqual(
            desktop.content_sha256,
            "cf5c48a3ce236df994844659aa46f3e1d4359f6d62774e7148526dc66f184964",
        )
        rejected_paths = {
            source.source_path
            for source in manifest.source_references
            if source.classification is LegacySourceClassification.REJECT_RUNTIME
        }
        self.assertIn("runtime/run_web.sh", rejected_paths)
        self.assertIn("runtime/webapp.py", rejected_paths)
        self.assertIn("apps/aoia_desktop_demo/ui/main_window.py", rejected_paths)

    def test_historical_five_call_shape_is_not_an_executable_call_plan(self) -> None:
        manifest = build_legacy_archive_manifest()
        self.assertEqual(manifest.historical_completed_provider_calls, 5)
        self.assertEqual(manifest.effective_provider_call_minimum, 0)
        self.assertEqual(manifest.effective_provider_call_maximum, 0)
        self.assertFalse(manifest.legacy_personal_memory_write)
        self.assertIs(manifest.effective_mode, LegacyCompatibilityMode.ARCHIVAL_VIEW)

    def test_manifest_is_immutable_and_tampering_fails_closed(self) -> None:
        manifest = build_legacy_archive_manifest()
        with self.assertRaises(dataclasses.FrozenInstanceError):
            manifest.system_name = "tampered"  # type: ignore[misc]
        with self.assertRaises(ValueError):
            dataclasses.replace(manifest, metadata_digest="0" * 64)
        with self.assertRaises(ValueError):
            dataclasses.replace(
                manifest.source_references[0],
                source_path="../outside",
            )
        with self.assertRaises(ValueError):
            LegacyObserverRole("Execute & Publish")

    def test_missing_archive_keeps_current_mode_operational(self) -> None:
        default = build_default_cockpit_shell()
        shell = CockpitShell(
            default.runtime_status,
            legacy_mode=LegacyCompatibilityMode.ARCHIVAL_VIEW,
            legacy_archive=None,
        )
        current = shell.project()
        requested_legacy = shell.project(CockpitMode.CRITICAL_PROMPT_LEGACY.value)
        self.assertIs(current.selected_mode, CockpitMode.MEMORY_PATCH_CURRENT)
        self.assertIs(
            requested_legacy.selected_mode,
            CockpitMode.MEMORY_PATCH_CURRENT,
        )
        self.assertIn("integrity-valid", requested_legacy.notice or "")
        self.assertEqual(
            requested_legacy.legacy.availability,
            "ARCHIVE_INTEGRITY_OR_AVAILABILITY_FAILURE",
        )

    def test_offline_validation_runner_makes_no_execution_claim(self) -> None:
        result = run_validation()
        self.assertEqual(result["verdict"], "PASS")
        self.assertEqual(result["actual_paid_provider_calls"], 0)
        self.assertEqual(result["missing_historical_bytes_fabricated"], 0)
        self.assertIsNone(result["replay_bundle"])
        self.assertFalse(any(result["legacy_authority"].values()))

    def test_committed_d2_evidence_is_canonical_and_hash_bound(self) -> None:
        root = Path(__file__).resolve().parents[1]
        raw = (
            root
            / "docs/evidence/demo/d2-legacy-critical-compatibility-validation-1a.json"
        ).read_bytes()
        evidence = json.loads(raw.decode("utf-8", errors="strict"))
        self.assertEqual(raw, (canonical_json(evidence) + "\n").encode("utf-8"))
        self.assertEqual(
            evidence["validation_digest"],
            canonical_sha256(evidence, exclude_fields=("validation_digest",)),
        )
        self.assertEqual(evidence["actual_paid_provider_calls"], 0)
        self.assertEqual(
            evidence["historical_provenance"]["source_byte_hashes"],
            "PASS_8_OF_8",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
