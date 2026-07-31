"""Offline safety tests for the Step 11 zero-external-write live harness."""

from __future__ import annotations

import ast
import importlib.util
import io
import sys
import unittest
from contextlib import redirect_stderr
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

from aioa_memory_kernel.contracts.serialization import canonical_sha256
from tests._support import REPOSITORY_ROOT, SOURCE_ROOT


SCRIPT_ROOT = REPOSITORY_ROOT / "scripts"
for import_root in (SOURCE_ROOT, SCRIPT_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

MODULE_PATH = SCRIPT_ROOT / "run_parsing_pipeline_validation.py"
SPEC = importlib.util.spec_from_file_location(
    "run_parsing_pipeline_validation_test",
    MODULE_PATH,
)
assert SPEC is not None and SPEC.loader is not None
validation = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = validation
SPEC.loader.exec_module(validation)


CAPTURED = datetime(2026, 7, 31, 7, 39, 23, tzinfo=UTC)
RETAIN_UNTIL = datetime(2026, 8, 30, 7, 39, 23, tzinfo=UTC)


class Step11LiveHarnessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bundle = validation._build_bundle(CAPTURED, RETAIN_UNTIL)

    @staticmethod
    def _repository() -> dict[str, object]:
        return {
            "ahead_behind": [0, 0],
            "branch": "main",
            "changed_file_count": 31,
            "head": validation.EXPECTED_HEAD,
            "origin_main": validation.EXPECTED_HEAD,
            "remote": validation.EXPECTED_REMOTE,
            "worktree_digest": "a" * 64,
        }

    def _prepare_plan(self) -> dict[str, object]:
        external = {
            "device_reference": "external-volume-sha256:" + "b" * 64,
            "filesystem_type": "ext4",
            "operation": "INGESTION_STAGING",
            "payload_length": validation.FIXTURE_LENGTH,
            "payload_sha256": validation.FIXTURE_SHA256,
            "relative_path": (
                "ingestion/downloads/"
                + self.bundle.saga.local_relative_path
            ),
            "root_filesystem_fallback": False,
            "target_state": "EXACT_EXISTING_ARTIFACT",
            "transport": "usb",
            "write_capability_required": False,
            "write_preflight_performed": False,
            "write_count_planned": 0,
        }
        with (
            mock.patch.object(
                validation,
                "_repository_facts",
                return_value=self._repository(),
            ),
            mock.patch.object(
                validation.migrations,
                "verify_binary_identity",
                return_value={
                    "binary_sha256": validation.PINNED_BINARY_SHA256,
                    "build_tag": "v26.2.4",
                },
            ),
            mock.patch.object(
                validation.step10,
                "_verify_aws_binary",
                return_value=Path("/tmp/step11-owned/aws"),
            ),
            mock.patch.object(
                validation.step10,
                "_file_sha256",
                return_value="c" * 64,
            ),
            mock.patch.object(
                validation.step10,
                "_aws_identity",
                return_value={
                    "caller_type": "ASSUMED_ROLE",
                    "permission_context": "LuciferSOL",
                    "profile": "aoia-admin",
                    "region": "eu-central-1",
                    "root_principal": False,
                    "sensitive_identifiers_redacted": True,
                    "temporary_sso_role": True,
                },
            ),
            mock.patch.object(
                validation.step10,
                "_external_recovery_preflight",
                return_value=(object(), external),
            ),
            mock.patch.object(
                validation.step10,
                "_s3_recovery_preflight",
                return_value=(object(), object(), {"target_version_count": 1}),
            ),
            mock.patch.object(validation, "_delete_marker_count", return_value=0),
        ):
            plan, _, _ = validation.prepare_plan(
                cockroach_binary=Path("/tmp/step11-owned/cockroach"),
                aws_binary=Path("/tmp/step11-owned/aws"),
                captured_at=CAPTURED,
                retain_until=RETAIN_UNTIL,
                external_config=Path("/tmp/private-machine-config"),
                evidence_output=Path(
                    "docs/evidence/parsing/step11-offline-plan-test.json"
                ),
                failure_evidence_output=Path(
                    "docs/evidence/parsing/step11-offline-plan-test-failure.json"
                ),
            )
        return plan

    def test_existing_fixture_and_real_profiles_are_exact(self) -> None:
        self.assertEqual(self.bundle.snapshot.content_length, 92)
        self.assertEqual(
            self.bundle.snapshot.content_sha256,
            validation.FIXTURE_SHA256,
        )
        self.assertEqual(
            self.bundle.source_record.parser.parser_name,
            "generic-canonical-json-document-parser",
        )
        self.assertEqual(
            self.bundle.source_record.transformation.transformation_name,
            "unicode-nfc-json-canonicalization",
        )
        self.assertNotEqual(
            self.bundle.saga.saga_id,
            validation.step10.build_validation_bundle(
                CAPTURED, RETAIN_UNTIL
            ).saga.saga_id,
        )

    def test_seed_reserves_exact_real_normalized_knowledge_version(self) -> None:
        sql = validation._seed_sql(self.bundle)
        self.assertIn("knowledge_versions", sql)
        self.assertIn(
            "3f7e39c561b1a1c2af8d0a5ad39e15459c19dcca950ea09f338f223b8c38497e",
            sql,
        )
        self.assertIn("unicode-nfc-text-normalization:1.0.0", sql)
        self.assertIn("STEP_11_GENERIC_PARSING_PIPELINE_1A", sql)
        self.assertNotIn("exact-byte-synthetic-validation-placeholder-v1", sql)
        self.assertNotIn("knowledge_chunks", sql)
        self.assertNotIn("DELETE FROM", sql.upper())
        self.assertTrue(sql.startswith("BEGIN;"))
        self.assertTrue(sql.endswith("COMMIT;"))

    def test_plan_is_exactly_zero_external_write(self) -> None:
        plan = self._prepare_plan()
        self.assertEqual(plan["mode"], "ZERO_EXTERNAL_WRITE_LIVE_VALIDATION")
        self.assertEqual(plan["safety"]["new_s3_writes"], 0)
        self.assertEqual(plan["safety"]["external_volume_writes"], 0)
        self.assertEqual(plan["safety"]["deletions"], 0)
        self.assertEqual(plan["safety"]["retention_changes"], 0)
        self.assertEqual(plan["aws"]["version_id"], validation.S3_VERSION_ID)
        self.assertEqual(plan["aws"]["version_count"], 1)
        self.assertEqual(plan["cockroachdb"]["migration_range"], "0001-0008")
        self.assertEqual(
            plan["cockroachdb"]["per_migration_timeout_seconds"],
            validation.LIVE_MIGRATION_TIMEOUT_SECONDS,
        )
        self.assertEqual(validation.LIVE_MIGRATION_TIMEOUT_SECONDS, 300)
        self.assertEqual(
            plan["cockroachdb"]["per_transaction_timeout_seconds"],
            validation.LIVE_TRANSACTION_TIMEOUT_SECONDS,
        )
        self.assertEqual(validation.LIVE_TRANSACTION_TIMEOUT_SECONDS, 180)
        self.assertEqual(
            plan["failure_evidence_output"],
            "docs/evidence/parsing/step11-offline-plan-test-failure.json",
        )
        self.assertRegex(plan["plan_digest"], r"^[0-9a-f]{64}$")

    def test_plan_command_binds_every_existing_evidence_confirmation(self) -> None:
        plan = self._prepare_plan()
        command = plan["exact_command_argv"]
        for option in (
            "--validate-existing",
            "--confirm-project",
            "--confirm-device-reference",
            "--confirm-external-relative-path",
            "--confirm-payload-sha256",
            "--confirm-bucket",
            "--confirm-object-key",
            "--confirm-version-id",
            "--confirm-plan-digest",
            "--failure-evidence-output",
        ):
            self.assertIn(option, command)
        self.assertIn(validation.S3_VERSION_ID, command)
        self.assertIn(plan["plan_digest"], command)

    def test_plan_declares_only_expected_ephemeral_rows(self) -> None:
        rows = self._prepare_plan()["expected_database_rows"]
        self.assertEqual(rows["parsed_documents"], 1)
        self.assertEqual(rows["parsed_sections"], 3)
        self.assertEqual(rows["knowledge_chunks"], 3)
        self.assertEqual(rows["parse_security_findings"], 0)
        self.assertEqual(rows["ingestion_saga_events"], 8)

    def test_missing_confirmations_stop_before_execution(self) -> None:
        plan = {
            "external_volume": {
                "device_reference": "external-volume-sha256:" + "b" * 64,
                "relative_path": "ingestion/downloads/existing.json",
            },
            "aws": {"object_key": "memory-patch/existing.bin"},
            "plan_digest": "d" * 64,
        }
        arguments = [
            "--validate-existing",
            "--cockroach-binary",
            "/tmp/cockroach",
            "--aws-binary",
            "/tmp/aws",
            "--captured-at",
            CAPTURED.isoformat(),
            "--retain-until",
            RETAIN_UNTIL.isoformat(),
        ]
        with (
            mock.patch.object(
                validation,
                "prepare_plan",
                return_value=(plan, self.bundle, object()),
            ),
            mock.patch.object(validation, "execute_validation") as execute,
            redirect_stderr(io.StringIO()),
        ):
            result = validation.main(arguments)
        self.assertEqual(result, 1)
        execute.assert_not_called()

    def test_read_only_boundaries_reject_external_write_calls(self) -> None:
        class Volume:
            system_drive_fallback_allowed = False

            @staticmethod
            def atomic_write_exact(*args: object, **kwargs: object) -> object:
                raise AssertionError("delegate must not receive a write")

        wrapped_volume = validation.step10.CountingExternalVolume(
            Volume(), writes_allowed=False
        )
        with self.assertRaises(validation.step10.Step10ValidationError):
            wrapped_volume.atomic_write_exact("forbidden")
        self.assertEqual(wrapped_volume.write_calls, 0)

        class S3:
            operation_counts: dict[str, int] = {}

            @staticmethod
            def put_object(**values: object) -> object:
                raise AssertionError("delegate must not receive PutObject")

        wrapped_s3 = validation.step10.RecoveryReadOnlyS3Client(S3())
        with self.assertRaises(validation.step10.Step10ValidationError):
            wrapped_s3.put_object(Body=b"forbidden")
        self.assertEqual(wrapped_s3.put_object_calls, 1)

    def test_evidence_digest_is_canonical_and_verifiable(self) -> None:
        evidence = validation._canonical_evidence(
            {
                "cleanup": {"cleanup_errors": ("bounded",)},
                "schema_version": "1.0.0",
                "status": "PASS",
            }
        )
        self.assertEqual(evidence["cleanup"]["cleanup_errors"], ["bounded"])
        digest = evidence.pop("evidence_digest")
        self.assertEqual(digest, canonical_sha256(evidence))

    def test_migration_timeout_has_a_stable_root_cause(self) -> None:
        error = validation.migrations.MigrationError(
            "subprocess exceeded 180 seconds"
        )
        self.assertEqual(
            validation._failure_code(error),
            "COCKROACHDB_MIGRATION_TIMEOUT",
        )

    def test_failure_chain_exposes_only_bounded_structured_metadata(self) -> None:
        inner = RuntimeError("sensitive database detail")
        inner.operation_kind = "STEP11_PARSE_ARTIFACT_PERSIST"  # type: ignore[attr-defined]
        inner.sanitized_code = "COCKROACH_CLI_STATEMENT_TIMEOUT"  # type: ignore[attr-defined]
        outer = RuntimeError("bounded ingestion failure")
        outer.sanitized_code = "COCKROACH_CLI_STATEMENT_TIMEOUT"  # type: ignore[attr-defined]
        outer.__cause__ = inner
        chain = validation._failure_chain(outer)
        self.assertEqual(
            chain[1]["sanitized_code"],
            "COCKROACH_CLI_STATEMENT_TIMEOUT",
        )
        self.assertEqual(
            chain[1]["operation_kind"],
            "STEP11_PARSE_ARTIFACT_PERSIST",
        )
        self.assertNotIn("sensitive", str(chain))

    def test_import_is_inert_and_main_is_guarded(self) -> None:
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        guarded = [
            node
            for node in tree.body
            if isinstance(node, ast.If)
            and isinstance(node.test, ast.Compare)
            and "__main__" in ast.unparse(node.test)
        ]
        self.assertEqual(len(guarded), 1)
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertNotIn("--write-validation", source)
        self.assertNotIn("delete_object", source)
        self.assertNotIn("BypassGovernanceRetention", source)

    def test_success_and_failure_evidence_paths_cannot_alias(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertIn("if output == failed_output", source)
        self.assertIn("EVIDENCE_OUTPUT_CONFLICT", source)


if __name__ == "__main__":
    unittest.main()
