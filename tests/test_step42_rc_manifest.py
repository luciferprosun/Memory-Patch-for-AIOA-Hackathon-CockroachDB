"""Step 42 deterministic RC and recovery-asset manifest tests."""

from __future__ import annotations

import dataclasses
import json
import unittest
from pathlib import Path

from aioa_memory_kernel.contracts.exceptions import ContractValidationError
from aioa_memory_kernel.contracts.serialization import (
    canonical_json,
    canonical_sha256,
    to_canonical_data,
)
from aioa_memory_kernel.release_candidate import (
    RecoveryStateClass,
    build_rc_manifest,
    build_recovery_asset_manifest,
    runtime_content_manifest,
    verify_recovery_asset_manifest,
    verify_release_candidate_manifest,
)
from aioa_memory_kernel.security.redaction import assert_secret_free


STEP41_BASE = "26577fa02c96da7a4b4ae49cdc5f3c168eb1ed80"
REMOTE = (
    "https://github.com/luciferprosun/"
    "Memory-Patch-for-AIOA-Hackathon-CockroachDB.git"
)
ROOT = Path(__file__).resolve().parents[1]


class Step42RcManifestTest(unittest.TestCase):
    def manifests(self):
        assets = build_recovery_asset_manifest(step41_base_sha=STEP41_BASE)
        rc = build_rc_manifest(
            step41_base_sha=STEP41_BASE,
            recovery_asset_manifest_digest=assets.manifest_digest,
            repository_identity=REMOTE,
        )
        return assets, rc

    def test_rc_and_recovery_manifests_are_deterministic_and_deep_verified(self):
        first_assets, first_rc = self.manifests()
        second_assets, second_rc = self.manifests()
        self.assertEqual(first_assets, second_assets)
        self.assertEqual(first_rc, second_rc)
        self.assertEqual(verify_recovery_asset_manifest(first_assets), first_assets)
        self.assertEqual(verify_release_candidate_manifest(first_rc), first_rc)
        self.assertEqual(first_rc.created_from_step41_sha, STEP41_BASE)
        self.assertEqual(first_rc.release_commit_expected_parent, STEP41_BASE)
        self.assertEqual(first_rc.branch, "main")
        self.assertEqual(
            first_rc.recovery_asset_manifest_digest,
            first_assets.manifest_digest,
        )
        assert_secret_free(first_assets, surface="Step42 assets")
        assert_secret_free(first_rc, surface="Step42 RC")

    def test_every_state_class_is_explicit_and_secrets_are_never_archived(self):
        assets, _ = self.manifests()
        self.assertEqual(
            {item.state_class for item in assets.assets},
            set(RecoveryStateClass),
        )
        secrets = tuple(
            item
            for item in assets.assets
            if item.state_class is RecoveryStateClass.SECRET_DO_NOT_ARCHIVE
        )
        self.assertEqual(len(secrets), 1)
        self.assertEqual(secrets[0].backup_mechanism, "EXCLUDED")
        self.assertFalse(secrets[0].rebuildable)
        required = tuple(
            item
            for item in assets.assets
            if item.state_class
            is RecoveryStateClass.AUTHORITATIVE_BACKUP_REQUIRED
        )
        self.assertEqual(len(required), 1)
        self.assertEqual(
            required[0].backup_mechanism,
            "COCKROACHDB_NATIVE_BACKUP",
        )

    def test_manifest_hash_or_parent_tamper_fails_closed(self):
        assets, rc = self.manifests()
        with self.assertRaises(ContractValidationError):
            dataclasses.replace(assets, manifest_digest="0" * 64)
        with self.assertRaises(ContractValidationError):
            dataclasses.replace(rc, created_from_step41_sha="1" * 40)
        with self.assertRaises(ContractValidationError):
            dataclasses.replace(rc, runtime_content_digest="2" * 64)

    def test_runtime_content_manifest_is_sorted_unique_and_excludes_caches(self):
        entries, digest = runtime_content_manifest()
        self.assertEqual(entries, tuple(sorted(entries)))
        self.assertEqual(len(entries), len({name for name, _size, _hash in entries}))
        self.assertEqual(len(digest), 64)
        self.assertTrue(any(name.startswith("src/") for name, *_ in entries))
        self.assertTrue(any(name.startswith("scripts/") for name, *_ in entries))
        self.assertFalse(any("__pycache__" in name for name, *_ in entries))
        self.assertFalse(any(name.endswith(".pyc") for name, *_ in entries))
        self.assertFalse(any(name.startswith("docs/") for name, *_ in entries))

    def test_committed_manifests_and_recovery_evidence_are_canonical_and_bound(self):
        assets, rc = self.manifests()
        expected = {
            "step42-recovery-asset-manifest-1a.json": to_canonical_data(assets),
            "step42-rc-manifest-1a.json": to_canonical_data(rc),
        }
        evidence_root = ROOT / "docs/evidence/release"
        for name, value in expected.items():
            with self.subTest(name=name):
                raw = (evidence_root / name).read_bytes()
                self.assertEqual(raw, (canonical_json(value) + "\n").encode())
                self.assertEqual(json.loads(raw), value)

        evidence_path = evidence_root / "step42-rc-backup-restore-validation.json"
        raw = evidence_path.read_bytes()
        evidence = json.loads(raw.decode("utf-8", errors="strict"))
        self.assertEqual(raw, (canonical_json(evidence) + "\n").encode())
        self.assertEqual(evidence["step"], 42)
        self.assertEqual(evidence["verdict"], "PASS_RC_BACKUP_RESTORE_CONTROLLED")
        self.assertTrue(evidence["closure_eligible"])
        self.assertFalse(evidence["step43_started"])
        self.assertEqual(evidence["rc_manifest"]["digest"], rc.manifest_digest)
        self.assertEqual(
            evidence["recovery_asset_manifest"]["digest"],
            assets.manifest_digest,
        )
        self.assertEqual(
            evidence["validation_digest"],
            canonical_sha256(evidence, exclude_fields=("validation_digest",)),
        )
        self.assertEqual(
            evidence["full_regression_summary"]["status"],
            "PASS_FINAL_TREE_REGRESSION",
        )
        self.assertTrue(
            all(value == 0 for value in evidence["security_counters"].values())
        )
        cleanup = evidence["cleanup_status"]
        self.assertTrue(cleanup["backup_artifact_removed_after_validation"])
        self.assertTrue(cleanup["owned_databases_removed"])
        self.assertTrue(cleanup["owned_pids_exited"])
        self.assertTrue(cleanup["owned_ports_closed"])
        self.assertTrue(cleanup["temporary_runtime_stores_removed"])
        self.assertFalse(cleanup["force_kill_used"])
        self.assertEqual(cleanup["database_runtimes_started"], 2)
        self.assertEqual(cleanup["production_resources_touched"], 0)
        assert_secret_free(
            evidence,
            surface="Step42 committed evidence",
            reject_machine_paths=True,
        )


if __name__ == "__main__":
    unittest.main()
