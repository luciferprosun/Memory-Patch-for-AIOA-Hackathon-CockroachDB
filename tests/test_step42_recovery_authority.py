"""Step 42 recovery cannot become an authority or cloud-mutation path."""

from __future__ import annotations

import ast
import unittest
from pathlib import Path
from unittest import mock

import run_cockroachdb_migrations as migrations
import run_step42_rc_backup_restore_validation as validation
import step38_real_retrieval as real_retrieval
from aioa_memory_kernel.security.redaction import assert_secret_free
from aioa_memory_kernel.release_candidate import (
    RecoveryStateClass,
    build_recovery_asset_manifest,
)


ROOT = Path(__file__).resolve().parents[1]


class Step42RecoveryAuthorityTest(unittest.TestCase):
    def test_step42_runtime_prefix_is_destructive_allowlisted_but_production_is_not(self):
        migrations.assert_disposable_database("mp_step42_source_fixture")
        migrations.assert_disposable_database("mp_step42_restore_fixture")
        for value in ("production", "memory_patch", "main", "defaultdb"):
            with self.subTest(value=value), self.assertRaises(
                migrations.MigrationError
            ):
                migrations.assert_disposable_database(value)

    def test_recovery_script_has_no_aws_sdk_secret_store_or_authority_service_calls(self):
        tree = ast.parse(
            (ROOT / "scripts/run_step42_rc_backup_restore_validation.py").read_text(
                encoding="utf-8"
            )
        )
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        self.assertFalse(
            imported
            & {
                "boto3",
                "botocore",
                "PersonalMemoryApprovalService",
                "PersonalMemoryCommitHelper",
                "PersonalMemoryActivationService",
            }
        )
        calls = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        self.assertFalse(
            calls
            & {
                "put_object",
                "delete_object",
                "get_secret_value",
                "batch_get_secret_value",
                "approve",
                "commit",
                "activate",
                "publish_source",
            }
        )

    def test_only_database_state_is_backed_up_and_secret_values_are_excluded(self):
        manifest = build_recovery_asset_manifest(
            step41_base_sha=validation.STEP41_BASE_SHA
        )
        backed_up = tuple(
            item
            for item in manifest.assets
            if item.state_class
            is RecoveryStateClass.AUTHORITATIVE_BACKUP_REQUIRED
        )
        self.assertEqual(
            tuple(item.asset_id for item in backed_up),
            ("cockroachdb-authoritative-state",),
        )
        secret_assets = tuple(
            item
            for item in manifest.assets
            if item.state_class is RecoveryStateClass.SECRET_DO_NOT_ARCHIVE
        )
        self.assertTrue(secret_assets)
        self.assertTrue(
            all(item.backup_mechanism == "EXCLUDED" for item in secret_assets)
        )
        assert_secret_free(
            {"classification_counts": manifest.classification_counts},
            surface="Step 42 classification projection",
            reject_machine_paths=True,
        )
        with self.assertRaises(ValueError):
            assert_secret_free(
                {"classification_counts": dict(manifest.classification_counts)},
                surface="unsafe dynamic-key projection",
                reject_machine_paths=True,
            )

    def test_native_backup_is_integrity_checked_and_restore_is_detached_and_renamed(self):
        source = (
            ROOT / "scripts/run_step42_rc_backup_restore_validation.py"
        ).read_text(encoding="utf-8")
        self.assertIn("SHOW BACKUP FROM LATEST", source)
        self.assertIn("WITH check_files", source)
        self.assertIn("WITH detached", source)
        self.assertIn("new_db_name", source)
        self.assertIn("grants, detached", source)
        self.assertNotIn("DROP DATABASE production", source)
        self.assertNotIn("--external-io-disabled", source)

    def test_shared_step31_step33_fixture_identity_seed_is_idempotent(self):
        source = (
            ROOT / "scripts/run_step42_rc_backup_restore_validation.py"
        ).read_text(encoding="utf-8")
        self.assertIn('statement.strip() + " ON CONFLICT DO NOTHING"', source)

    def test_frozen_step40_evidence_and_profile_pass_restored_smoke(self):
        result = validation._step40_smoke()
        self.assertEqual(result["status"], "PASS_RESTORED_PROFILE_SMOKE")
        self.assertTrue(result["readiness"])
        self.assertFalse(result["security_or_authority_bypass"])

    def test_pass_evidence_is_written_only_after_final_cleanup_gate(self):
        source = (
            ROOT / "scripts/run_step42_rc_backup_restore_validation.py"
        ).read_text(encoding="utf-8")
        cleanup = source.index('result_payload["cleanup_status"] = {')
        evidence_write = source.index("_write_json(EVIDENCE_PATH, result_payload)")
        self.assertLess(source.index("finally:"), cleanup)
        self.assertLess(cleanup, evidence_write)

    def test_recovery_watermark_covers_authority_and_provenance_state(self):
        source = (
            ROOT / "scripts/run_step42_rc_backup_restore_validation.py"
        ).read_text(encoding="utf-8")
        for table in (
            "memory_patch.audit_events",
            "memory_patch.memory_patch_approvals",
            "memory_patch.memory_patch_commits",
            "memory_patch.patch_transition_records",
            "memory_patch.persistence_operations",
            "memory_patch.personal_memory_model_bindings",
            "memory_patch.personal_memory_quota_policies",
            "memory_patch.source_provenance_edges",
            "memory_patch.source_publication_events",
        ):
            with self.subTest(table=table):
                self.assertIn(table, source)

    def test_live_drill_records_exact_server_version_and_backup_timestamp(self):
        source = (
            ROOT / "scripts/run_step42_rc_backup_restore_validation.py"
        ).read_text(encoding="utf-8")
        self.assertIn('"cockroachdb_version": migrations.PINNED_VERSION', source)
        self.assertIn('"completed_at": datetime.now(UTC)', source)

    def test_post_restore_model_proof_uses_canonical_primary_then_bounded_backup(self):
        source = (
            ROOT / "scripts/run_step42_rc_backup_restore_validation.py"
        ).read_text(encoding="utf-8")
        primary = source.index("request-step42-restored-primary")
        self.assertIn(
            "run_step38_restored_primary_retrieval_on_owned_database",
            source[primary:],
        )
        self.assertNotIn(
            "_run_step38_retrieval_on_owned_database",
            source,
        )
        primary_provider = source.index(
            "provider_public, upstream = step38._real_provider_flow(",
            primary,
        )
        fallback_gate = source.index(
            "STEP38_PRIMARY_DEFECT_NOT_OBSERVED_BACKUP_REQUIRED",
            primary_provider,
        )
        backup = source.index("request-step42-restored-backup", fallback_gate)
        self.assertLess(primary, primary_provider)
        self.assertLess(primary_provider, fallback_gate)
        self.assertLess(fallback_gate, backup)
        self.assertIn('provider_public["primary_case_attempted"] = True', source)
        self.assertIn('provider_public["backup_case_attempted"] = True', source)

    def test_restored_primary_boundary_replays_without_seeding(self):
        suite = validation.step38.load_german_law_golden_cases(
            validation.step38.FIXTURE_PATH
        )
        primary = real_retrieval.build_canonical_primary_retrieval_input(
            suite,
            tenant_id="tenant-step38-golden",
            user_id="user-step38-owner-a",
            request_id="request-step42-restored-boundary-test",
        )
        sentinel = object()
        with mock.patch.object(
            real_retrieval,
            "_run_step38_retrieval_on_owned_database",
            return_value=sentinel,
        ) as operation:
            result = real_retrieval.run_step38_restored_primary_retrieval_on_owned_database(
                primary,
                root=object(),
                database="mp_step42_restore_fixture_db",
                database_runner=object(),
                data_plane_session_user="mp_s42_test_role",
                runtime_instance_digest="1" * 64,
                database_instance_digest="2" * 64,
                corpus_roots=object(),
                embedding_backend=object(),
                embedding_cache=object(),
            )
        self.assertIs(result, sentinel)
        self.assertFalse(operation.call_args.kwargs["seed_fixture"])
        self.assertTrue(operation.call_args.kwargs["allow_restored_primary"])

    def test_failure_diagnostic_records_only_bounded_repository_origin(self):
        try:
            raise ValueError("sensitive diagnostic must not be copied")
        except ValueError as error:
            payload = validation._failure_payload(error)
        self.assertEqual(payload["verdict"], "FAILED_VALIDATION_NOT_CLOSURE")
        self.assertEqual(payload["reason"].rsplit("_", 1)[-1], "VALUEERROR")
        self.assertEqual(payload["failure_origin"]["file"], "tests/test_step42_recovery_authority.py")
        self.assertNotIn("sensitive diagnostic", str(payload))

    def test_exact_real_provider_no_defect_result_is_canonical_fail_closed(self):
        result = validation._classify_post_restore_provider_result(
            {
                "status": "BLOCKED",
                "reason": "STEP38_BACKUP_DEFECT_NOT_OBSERVED",
                "provider_id": "openrouter",
                "model_id": "moonshotai/kimi-k2",
                "provider_material_recorded": False,
                "primary_case_attempted": True,
                "backup_case_attempted": True,
                "case_attempts": (
                    {
                        "case_id": "primary-entry-into-force",
                        "required_correction_count": 0,
                        "citation_count": 0,
                    },
                    {
                        "case_id": validation.step38.BACKUP_SPECIAL_CASE_ID,
                        "draft_shape_classification": (
                            validation.step38.BACKUP_DRAFT_CORRECT_EXACT
                        ),
                        "required_correction_count": 0,
                        "citation_count": 0,
                    },
                ),
            },
            None,
        )
        self.assertTrue(result["canonical_fail_closed"])
        self.assertEqual(
            result["status"],
            "PASS_REAL_PROVIDER_CANONICAL_FAIL_CLOSED_NO_DEFECT",
        )
        self.assertIsNone(result["verified_upstream_lineage_hash"])

    def test_any_other_provider_block_remains_a_closure_failure(self):
        base = {
            "status": "BLOCKED",
            "reason": "STEP38_REAL_MODEL_VALIDATION_REQUIRED",
            "provider_id": "openrouter",
            "model_id": "moonshotai/kimi-k2",
            "provider_material_recorded": False,
            "primary_case_attempted": True,
            "backup_case_attempted": True,
            "case_attempts": ({"case_id": "primary-entry-into-force"}, {}),
        }
        with self.assertRaises(validation.Step42ValidationError):
            validation._classify_post_restore_provider_result(base, None)


if __name__ == "__main__":
    unittest.main()
