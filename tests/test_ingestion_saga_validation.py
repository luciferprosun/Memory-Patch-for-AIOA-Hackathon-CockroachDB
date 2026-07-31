"""Offline tests for the explicitly gated Step 10 live harness."""

from __future__ import annotations

import ast
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
import subprocess
import signal
from contextlib import redirect_stderr
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

from tests._support import REPOSITORY_ROOT, SOURCE_ROOT


SCRIPT_ROOT = REPOSITORY_ROOT / "scripts"
for import_root in (SOURCE_ROOT, SCRIPT_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))
MODULE_PATH = SCRIPT_ROOT / "run_ingestion_saga_validation.py"
SPEC = importlib.util.spec_from_file_location(
    "run_ingestion_saga_validation_test",
    MODULE_PATH,
)
assert SPEC is not None and SPEC.loader is not None
validation = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = validation
SPEC.loader.exec_module(validation)

from aioa_memory_kernel.sources import (  # noqa: E402
    ProvenanceGraph,
    evaluate_publication_eligibility,
)


CAPTURED = datetime(2026, 7, 31, 6, 30, 0, tzinfo=UTC)
RETAIN_UNTIL = CAPTURED + timedelta(days=30)


class Step10LiveHarnessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bundle = validation.build_validation_bundle(
            CAPTURED,
            RETAIN_UNTIL,
        )

    def _outcome_plan(self) -> dict[str, object]:
        return {
            "recovery_plan_digest": "b" * 64,
            "external_volume": {
                "relative_path": (
                    "ingestion/downloads/"
                    + self.bundle.saga.local_relative_path
                ),
            },
            "aws": {
                "bucket": validation.S3_BUCKET,
                "object_key": self.bundle.storage_plan.object_key,
                "version_id": validation.PRESERVED_S3_VERSION_ID,
            },
        }

    @staticmethod
    def _complete_cleanup() -> dict[str, object]:
        return {
            "cleanup_errors": (),
            "drain_command_completed": True,
            "drain_completion_marker": True,
            "drain_shutdown_requested": True,
            "drain_elapsed_seconds": 0.5,
            "drain_output_sha256": "d" * 64,
            "force_kill_used": False,
            "graceful_shutdown_requested": True,
            "owned_child_processes_reaped": True,
            "pid_exited": True,
            "ports_closed": True,
            "process_exit_accepted": True,
            "process_exit_code": 0,
            "runtime_log_evidence": {
                "byte_length": 1,
                "panic_detected": False,
                "sha256": "e" * 64,
            },
            "shutdown_budget": {
                "grace_seconds": 40,
                "phase_total_seconds": 25.0,
            },
            "shutdown_method": (
                "NODE_DRAIN_SELF_ON_RPC_WITH_SHUTDOWN"
            ),
            "sigterm_sent_to_exact_pid": True,
            "temporary_store_removed": True,
        }

    @staticmethod
    def _complete_validation() -> dict[str, object]:
        return {
            "external_volume": {"write_calls": 0},
            "s3": {"put_object_calls": 0},
            "saga": {"final_milestone": "PUBLISHED"},
        }

    def test_fixture_and_all_derived_identities_are_exact_and_stable(self) -> None:
        replay = validation.build_validation_bundle(CAPTURED, RETAIN_UNTIL)
        self.assertEqual(
            self.bundle.snapshot.content_sha256,
            validation.FIXTURE_SHA256,
        )
        self.assertEqual(
            self.bundle.snapshot.content_length,
            validation.FIXTURE_LENGTH,
        )
        self.assertEqual(self.bundle.snapshot, replay.snapshot)
        self.assertEqual(self.bundle.source_record, replay.source_record)
        self.assertEqual(self.bundle.saga, replay.saga)
        self.assertEqual(self.bundle.storage_plan, replay.storage_plan)
        self.assertTrue(self.bundle.saga.saga_id.startswith("ingsaga-"))
        self.assertTrue(self.bundle.snapshot.snapshot_id.startswith("s3snap-"))

    def test_worktree_fingerprint_binds_all_surviving_step10_files(self) -> None:
        fingerprint = validation._worktree_fingerprint()
        self.assertGreater(fingerprint["worktree_change_count"], 0)
        self.assertRegex(fingerprint["worktree_digest"], r"^[0-9a-f]{64}$")

    def test_timestamp_change_creates_a_fresh_non_overwriting_target(self) -> None:
        changed = validation.build_validation_bundle(
            CAPTURED + timedelta(seconds=1),
            RETAIN_UNTIL + timedelta(seconds=1),
        )
        self.assertNotEqual(
            self.bundle.snapshot.snapshot_id,
            changed.snapshot.snapshot_id,
        )
        self.assertNotEqual(
            self.bundle.storage_plan.object_key,
            changed.storage_plan.object_key,
        )
        self.assertNotEqual(self.bundle.saga.saga_id, changed.saga.saga_id)

    def test_step9_source_is_eligible_without_model_or_derived_claim(self) -> None:
        decision = evaluate_publication_eligibility(
            self.bundle.source_record,
            ProvenanceGraph(()),
            evaluated_at=CAPTURED,
        )
        self.assertTrue(decision.eligible)
        self.assertEqual(decision.reason_codes, ())
        self.assertTrue(
            self.bundle.source_record.artifact.exact_source_bytes
        )
        self.assertFalse(self.bundle.source_record.artifact.model_generated)

    def test_plan_binds_exact_gate_command_and_minimum_row_set(self) -> None:
        repository = {
            "ahead_behind": [0, 0],
            "branch": "main",
            "head": validation.EXPECTED_HEAD,
            "origin_main": validation.EXPECTED_HEAD,
            "remote": validation.EXPECTED_REMOTE + ".git",
            "worktree_change_count": 27,
            "worktree_digest": "c" * 64,
        }
        external = {
            "device_reference": "external-volume-sha256:" + "a" * 64,
            "filesystem_type": "ext4",
            "operation": "INGESTION_STAGING",
            "payload_length": validation.FIXTURE_LENGTH,
            "payload_sha256": validation.FIXTURE_SHA256,
            "relative_path": (
                "ingestion/downloads/"
                + self.bundle.saga.local_relative_path
            ),
            "root_filesystem_fallback": False,
            "target_state": "ABSENT",
            "transport": "mmc",
            "writable": True,
        }
        plan = validation._build_plan(
            repository=repository,
            binary=Path("/tmp/step10-owned/cockroach"),
            binary_identity={
                "binary_sha256": validation.PINNED_BINARY_SHA256,
                "build_tag": "v26.2.4",
            },
            aws_identity={
                "caller_type": "ASSUMED_ROLE",
                "permission_context": "LuciferSOL",
                "profile": "aoia-admin",
                "region": "eu-central-1",
                "root_principal": False,
                "sensitive_identifiers_redacted": True,
                "temporary_sso_role": True,
            },
            external=external,
            s3={
                "target_version_count": 0,
            },
            bundle=self.bundle,
            captured_at=CAPTURED,
            retain_until=RETAIN_UNTIL,
            evidence_output=validation.DEFAULT_EVIDENCE_OUTPUT,
            aws_binary=Path("/tmp/step10-owned/aws"),
            aws_binary_sha256="d" * 64,
        )
        command = plan["exact_command_argv"]
        self.assertIn("--confirm-plan-digest", command)
        self.assertIn(plan["plan_digest"], command)
        self.assertIn("--confirm-device-reference", command)
        self.assertIn(external["device_reference"], command)
        self.assertIn("--confirm-object-key", command)
        self.assertIn(self.bundle.storage_plan.object_key, command)
        self.assertEqual(
            plan["repository"]["worktree_digest"],
            "c" * 64,
        )
        self.assertEqual(plan["aws"]["cli_binary_sha256"], "d" * 64)
        self.assertEqual(
            plan["database_changes"]["ingestion_saga_events"],
            8,
        )
        self.assertEqual(
            plan["database_changes"]["ingestion_external_effects"],
            7,
        )
        self.assertEqual(plan["replay_and_reconciliation"]["s3_put_object_calls"], 1)
        self.assertFalse(plan["boundaries"]["step11_implemented"])

    def test_recovery_plan_binds_existing_evidence_and_zero_writes(self) -> None:
        external = {
            "device_reference": "external-volume-sha256:" + "a" * 64,
            "filesystem_type": "ext4",
            "operation": "INGESTION_STAGING",
            "payload_length": validation.FIXTURE_LENGTH,
            "payload_sha256": validation.FIXTURE_SHA256,
            "relative_path": (
                "ingestion/downloads/" + self.bundle.saga.local_relative_path
            ),
            "root_filesystem_fallback": False,
            "target_state": "EXACT_EXISTING_ARTIFACT",
            "transport": "mmc",
            "write_capability_required": False,
            "write_preflight_performed": False,
            "write_count_planned": 0,
        }
        plan = validation._build_recovery_plan(
            repository={
                "ahead_behind": [0, 0],
                "branch": "main",
                "head": validation.EXPECTED_HEAD,
                "origin_main": validation.EXPECTED_HEAD,
                "remote": validation.EXPECTED_REMOTE + ".git",
                "worktree_change_count": 35,
                "worktree_digest": "c" * 64,
            },
            repair={"repair_file_count": 6, "repair_diff_digest": "d" * 64},
            binary=Path("/tmp/step10-owned/cockroach"),
            binary_identity={
                "binary_sha256": validation.PINNED_BINARY_SHA256,
                "build_tag": "v26.2.4",
            },
            aws_identity={
                "caller_type": "ASSUMED_ROLE",
                "permission_context": "LuciferSOL",
                "profile": "aoia-admin",
                "region": "eu-central-1",
                "root_principal": False,
                "sensitive_identifiers_redacted": True,
                "temporary_sso_role": True,
            },
            external=external,
            s3={
                "target_version_count": 1,
                "version_id": validation.PRESERVED_S3_VERSION_ID,
            },
            bundle=self.bundle,
            captured_at=CAPTURED,
            retain_until=RETAIN_UNTIL,
            evidence_output=validation.DEFAULT_EVIDENCE_OUTPUT,
            recovery_failure_output=(
                validation.DEFAULT_RECOVERY_FAILURE_EVIDENCE_OUTPUT
            ),
            preserved_failure_output=(
                validation.DEFAULT_FAILURE_EVIDENCE_OUTPUT
            ),
            aws_binary=Path("/tmp/step10-owned/aws"),
            aws_binary_sha256="e" * 64,
        )
        self.assertEqual(plan["mode"], "ZERO_EXTERNAL_WRITE_RECOVERY_VALIDATION")
        self.assertEqual(plan["aws"]["new_writes"], 0)
        self.assertEqual(plan["external_volume"]["new_writes"], 0)
        self.assertEqual(plan["recovery"]["deletions"], 0)
        self.assertEqual(plan["recovery"]["retention_changes"], 0)
        self.assertEqual(plan["cockroachdb"]["shutdown_budget"]["grace_seconds"], 60)
        command = plan["exact_command_argv"]
        self.assertIn("--recovery-validation", command)
        self.assertIn("--confirm-version-id", command)
        self.assertIn(validation.PRESERVED_S3_VERSION_ID, command)
        self.assertIn("--confirm-failure-evidence-digest", command)
        self.assertIn("--confirm-recovery-plan-digest", command)
        self.assertIn(plan["recovery_plan_digest"], command)

    def test_recovery_boundaries_make_external_writes_uncallable(self) -> None:
        class External:
            system_drive_fallback_allowed = False

            @staticmethod
            def atomic_write_exact(*args: object, **kwargs: object) -> object:
                raise AssertionError("delegate must not receive recovery write")

        volume = validation.CountingExternalVolume(
            External(),
            writes_allowed=False,
        )
        with self.assertRaises(validation.Step10ValidationError):
            volume.atomic_write_exact("forbidden")
        self.assertEqual(volume.write_attempts, 1)
        self.assertEqual(volume.write_calls, 0)

        class S3:
            operation_counts: dict[str, int] = {}

            @staticmethod
            def put_object(**values: object) -> object:
                raise AssertionError("delegate must not receive recovery PutObject")

        client = validation.RecoveryReadOnlyS3Client(S3())
        with self.assertRaises(validation.Step10ValidationError):
            client.put_object(Body=b"forbidden")
        self.assertEqual(client.put_object_calls, 1)

    def test_preserved_first_attempt_evidence_digest_is_valid(self) -> None:
        evidence = validation._load_preserved_failure_evidence(
            validation.DEFAULT_FAILURE_EVIDENCE_OUTPUT
        )
        self.assertEqual(evidence["status"], "FAILED_VALIDATION_NOT_COMMITTED")
        self.assertEqual(
            evidence["first_root_cause"]["code"],
            "DISPOSABLE_RUNTIME_FORCE_KILL_USED",
        )

    def test_seed_is_synthetic_and_does_not_claim_step11_output(self) -> None:
        sql = validation._seed_sql(self.bundle)
        for table in (
            "tenants",
            "users",
            "hat_manifests",
            "hat_scopes",
            "knowledge_sources",
            "source_snapshots",
            "knowledge_versions",
        ):
            self.assertIn(f"memory_patch.{table}", sql)
        self.assertIn("step11_implemented", sql)
        self.assertIn("storage_intent_only", sql)
        self.assertNotIn("{at}", sql)
        self.assertNotIn("knowledge_chunks", sql)
        self.assertNotIn("DELETE FROM", sql.upper())
        self.assertNotIn("German", sql)

    def test_reporting_queries_use_canonical_step9_table_names(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertIn("memory_patch.source_registry_entries", source)
        self.assertNotIn("FROM memory_patch.source_registry ", source)

    def test_synthetic_ports_emit_only_typed_bound_receipts(self) -> None:
        clock = validation.ValidationClock(CAPTURED)
        parser = validation.SyntheticParserBoundary(clock)
        parsed = parser.parse(
            self.bundle.saga,
            s3_version_id="synthetic-version-id",
            locked_storage_evidence_digest="a" * 64,
        )
        validator = validation.SyntheticValidatorBoundary(clock)
        validated = validator.validate(self.bundle.saga, parsed)
        self.assertTrue(parsed.synthetic_validation_boundary)
        self.assertTrue(validated.synthetic_validation_boundary)
        self.assertEqual(
            validated.parse_output_digest,
            parsed.output_artifact_digest,
        )
        self.assertTrue(validated.accepted)
        self.assertEqual(validated.reason_codes, ())

    def test_missing_gate_confirmation_stops_before_live_execution(self) -> None:
        plan = {
            "external_volume": {
                "device_reference": "external-volume-sha256:" + "a" * 64,
            },
            "aws": {"object_key": self.bundle.storage_plan.object_key},
            "plan_digest": "b" * 64,
        }
        arguments = [
            "--write-validation",
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
            mock.patch.object(
                validation,
                "execute_live_validation",
            ) as execute,
            redirect_stderr(io.StringIO()),
        ):
            result = validation.main(arguments)
        self.assertEqual(result, 1)
        execute.assert_not_called()

    def test_unexpected_preflight_error_is_sanitized_without_traceback(self) -> None:
        arguments = [
            "--plan",
            "--cockroach-binary",
            "/tmp/cockroach",
            "--aws-binary",
            "/tmp/aws",
            "--captured-at",
            CAPTURED.isoformat(),
            "--retain-until",
            RETAIN_UNTIL.isoformat(),
        ]
        output = io.StringIO()
        with (
            mock.patch.object(
                validation,
                "prepare_plan",
                side_effect=RuntimeError("do-not-expose-this-detail"),
            ),
            redirect_stderr(output),
        ):
            result = validation.main(arguments)
        self.assertEqual(result, 1)
        rendered = output.getvalue()
        self.assertIn('"status": "FAILED_SAFELY"', rendered)
        self.assertIn('"sanitized_error": "RuntimeError"', rendered)
        self.assertNotIn("do-not-expose-this-detail", rendered)
        self.assertNotIn("Traceback", rendered)

    def test_validation_error_exposes_only_a_bounded_structured_code(self) -> None:
        error = validation.Step10ValidationError(
            "local diagnostic must not be emitted",
            sanitized_code="S3_LOCK_VERIFICATION_FAILURE",
        )
        self.assertEqual(
            validation._sanitized_failure(error),
            "S3_LOCK_VERIFICATION_FAILURE",
        )
        self.assertNotIn(
            "local diagnostic",
            validation._sanitized_failure(error),
        )

    def test_evidence_writer_is_exclusive(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            target = Path(raw) / "evidence.json"
            validation._write_exclusive(target, b"{}\n")
            self.assertEqual(target.read_bytes(), b"{}\n")
            with self.assertRaises(validation.Step10ValidationError):
                validation._write_exclusive(target, b"changed\n")
            self.assertEqual(target.read_bytes(), b"{}\n")

    def test_evidence_output_cannot_escape_the_repository(self) -> None:
        with self.assertRaises(validation.Step10ValidationError):
            validation._resolve_evidence_output(
                Path(tempfile.gettempdir()) / "step10-escape.json"
            )
        resolved = validation._resolve_evidence_output(
            Path("docs/evidence/ingestion/safe.json")
        )
        self.assertTrue(
            resolved.is_relative_to(validation.REPOSITORY_ROOT)
        )

    def test_failure_evidence_is_sanitized_and_preserves_external_targets(
        self,
    ) -> None:
        result = validation._failure_evidence(
            plan={
                "plan_digest": "b" * 64,
                "external_volume": {
                    "relative_path": (
                        "ingestion/downloads/"
                        + self.bundle.saga.local_relative_path
                    ),
                },
                "aws": {
                    "bucket": validation.S3_BUCKET,
                    "object_key": self.bundle.storage_plan.object_key,
                },
            },
            sanitized_error="S3_LOCK_VERIFICATION_FAILURE",
            cleanup={
                "force_kill_used": False,
                "pid_exited": True,
                "ports_closed": True,
                "temporary_store_removed": True,
            },
            cleanup_errors=(),
            external_write_attempts=1,
            s3_put_attempts=1,
        )
        self.assertEqual(result["status"], "FAILED_SAFELY")
        self.assertEqual(
            result["sanitized_error"],
            "S3_LOCK_VERIFICATION_FAILURE",
        )
        self.assertFalse(
            result["preservation"]["external_artifact_deleted"]
        )
        self.assertFalse(result["preservation"]["s3_version_deleted"])
        self.assertNotIn("/tmp/", json.dumps(result, sort_keys=True))
        self.assertEqual(
            result["evidence_digest"],
            validation.canonical_sha256(
                {
                    key: value
                    for key, value in result.items()
                    if key != "evidence_digest"
                }
            ),
        )

    def test_saga_success_plus_cleanup_failure_writes_failure_evidence(
        self,
    ) -> None:
        cleanup = self._complete_cleanup()
        cleanup["ports_closed"] = False
        with (
            mock.patch.object(validation, "_write_verified_evidence") as write,
            self.assertRaises(validation.Step10ValidationError),
        ):
            validation._finalize_validation_evidence(
                plan=self._outcome_plan(),
                validation=self._complete_validation(),
                primary_failure=None,
                cleanup=cleanup,
                cleanup_errors=(),
                external_write_attempts=0,
                s3_put_attempts=0,
                success_output=Path("success.json"),
                failure_output=Path("failure.json"),
                recovery_existing=True,
            )
        write.assert_called_once()
        path, evidence = write.call_args.args
        self.assertEqual(path, Path("failure.json"))
        self.assertEqual(evidence["validation_status_before_cleanup"], "PASS")
        self.assertEqual(evidence["first_root_cause"], "OWNED_LOOPBACK_PORT_REMAINS")

    def test_primary_failure_plus_cleanup_success_writes_failure_evidence(
        self,
    ) -> None:
        primary = validation.Step10ValidationError(
            "private diagnostic",
            sanitized_code="PRIMARY_SAGA_FAILURE",
        )
        with (
            mock.patch.object(validation, "_write_verified_evidence") as write,
            self.assertRaises(validation.Step10ValidationError),
        ):
            validation._finalize_validation_evidence(
                plan=self._outcome_plan(),
                validation=None,
                primary_failure=primary,
                cleanup=self._complete_cleanup(),
                cleanup_errors=(),
                external_write_attempts=0,
                s3_put_attempts=0,
                success_output=Path("success.json"),
                failure_output=Path("failure.json"),
                recovery_existing=True,
            )
        evidence = write.call_args.args[1]
        self.assertEqual(evidence["first_root_cause"], "PRIMARY_SAGA_FAILURE")
        self.assertEqual(evidence["primary_validation_error"], "PRIMARY_SAGA_FAILURE")

    def test_primary_and_cleanup_failure_preserve_first_root_and_both_facts(
        self,
    ) -> None:
        primary = validation.Step10ValidationError(
            "private diagnostic",
            sanitized_code="PRIMARY_SAGA_FAILURE",
        )
        cleanup = self._complete_cleanup()
        cleanup["force_kill_used"] = True
        cleanup["process_exit_accepted"] = False
        cleanup["process_exit_code"] = -9
        with (
            mock.patch.object(validation, "_write_verified_evidence") as write,
            self.assertRaises(validation.Step10ValidationError),
        ):
            validation._finalize_validation_evidence(
                plan=self._outcome_plan(),
                validation=None,
                primary_failure=primary,
                cleanup=cleanup,
                cleanup_errors=("GRACEFUL_SHUTDOWN_TIMEOUT",),
                external_write_attempts=0,
                s3_put_attempts=0,
                success_output=Path("success.json"),
                failure_output=Path("failure.json"),
                recovery_existing=True,
            )
        evidence = write.call_args.args[1]
        self.assertEqual(evidence["first_root_cause"], "PRIMARY_SAGA_FAILURE")
        self.assertTrue(evidence["cleanup"]["force_kill_used"])
        self.assertIn(
            "DISPOSABLE_RUNTIME_FORCE_KILL_USED",
            evidence["cleanup"]["cleanup_errors"],
        )

    def test_successful_saga_and_cleanup_write_success_evidence(self) -> None:
        with mock.patch.object(
            validation,
            "_write_verified_evidence",
        ) as write:
            result = validation._finalize_validation_evidence(
                plan=self._outcome_plan(),
                validation=self._complete_validation(),
                primary_failure=None,
                cleanup=self._complete_cleanup(),
                cleanup_errors=(),
                external_write_attempts=0,
                s3_put_attempts=0,
                success_output=Path("success.json"),
                failure_output=Path("failure.json"),
                recovery_existing=True,
            )
        write.assert_called_once_with(Path("success.json"), result)
        self.assertEqual(result["status"], "PASS")
        self.assertFalse(result["cleanup"]["force_kill_used"])

    def test_force_kill_always_prevents_closure(self) -> None:
        cleanup = self._complete_cleanup()
        cleanup["force_kill_used"] = True
        cleanup["process_exit_accepted"] = False
        cleanup["process_exit_code"] = -9
        with (
            mock.patch.object(validation, "_write_verified_evidence") as write,
            self.assertRaises(validation.Step10ValidationError),
        ):
            validation._finalize_validation_evidence(
                plan=self._outcome_plan(),
                validation=self._complete_validation(),
                primary_failure=None,
                cleanup=cleanup,
                cleanup_errors=(),
                external_write_attempts=0,
                s3_put_attempts=0,
                success_output=Path("success.json"),
                failure_output=Path("failure.json"),
                recovery_existing=True,
            )
        evidence = write.call_args.args[1]
        self.assertEqual(
            evidence["first_root_cause"],
            "DISPOSABLE_RUNTIME_FORCE_KILL_USED",
        )

    def test_evidence_write_is_canonical_and_digest_verifiable(self) -> None:
        result = validation._success_evidence(
            plan=self._outcome_plan(),
            validation=self._complete_validation(),
            cleanup=self._complete_cleanup(),
            recovery_existing=True,
        )
        with tempfile.TemporaryDirectory() as raw:
            target = Path(raw) / "evidence.json"
            validation._write_verified_evidence(target, result)
            observed = json.loads(target.read_text(encoding="utf-8"))
        digest = observed.pop("evidence_digest")
        self.assertEqual(digest, validation.canonical_sha256(observed))
        self.assertNotIn("/tmp/", json.dumps(observed, sort_keys=True))

    def test_cleanup_coordinator_runs_callback_exactly_once(self) -> None:
        calls = 0

        def cleanup() -> dict[str, object]:
            nonlocal calls
            calls += 1
            return self._complete_cleanup()

        coordinator = validation._CleanupOnce()
        coordinator.run(cleanup)
        with self.assertRaises(validation.Step10ValidationError):
            coordinator.run(cleanup)
        self.assertEqual(calls, 1)

    def test_failure_output_cannot_overwrite_success_output(self) -> None:
        with (
            mock.patch.object(validation, "_write_verified_evidence") as write,
            self.assertRaises(validation.Step10ValidationError),
        ):
            validation._finalize_validation_evidence(
                plan=self._outcome_plan(),
                validation=self._complete_validation(),
                primary_failure=None,
                cleanup=self._complete_cleanup(),
                cleanup_errors=(),
                external_write_attempts=0,
                s3_put_attempts=0,
                success_output=Path("same.json"),
                failure_output=Path("same.json"),
                recovery_existing=True,
            )
        write.assert_not_called()

    def test_observed_shutdown_settings_use_the_bounded_sixty_second_floor(self) -> None:
        budget = validation.migrations.derive_graceful_shutdown_budget(
            validation.AUDITED_SHUTDOWN_SETTINGS
        )
        self.assertEqual(budget["phase_total_seconds"], 25.0)
        self.assertEqual(budget["scheduling_cushion_seconds"], 15)
        self.assertEqual(budget["minimum_grace_seconds"], 60)
        self.assertEqual(budget["grace_seconds"], 60)
        self.assertEqual(budget["test_only_cap_seconds"], 120)

    def test_graceful_drain_with_shutdown_exits_exact_pid(self) -> None:
        class Process:
            pid = 12345
            returncode = None

            def poll(self) -> int | None:
                return self.returncode

            def send_signal(self, observed: int) -> None:
                self.signal = observed

            def wait(self, timeout: float) -> int:
                self.timeout = timeout
                self.returncode = 1
                return 1

        with tempfile.TemporaryDirectory(
            prefix="mp_step10_test_",
            dir="/tmp",
        ) as raw:
            parent = Path("/tmp")
            runtime_dir = Path(raw)
            log = (runtime_dir / "server.log").open("w", encoding="utf-8")
            log.write(
                "canceling jobs due to graceful drain request\n"
                "server drained and shutdown completed\n"
                "shutdown requested by drain RPC\n"
            )
            log.flush()
            process = Process()
            runtime = validation.migrations.LocalRuntime(
                binary=Path("/verified/cockroach"),
                run_id="mp_step10_test",
                runtime_parent=parent,
                runtime_dir=runtime_dir,
                process=process,
                log_handle=log,
                sql_port=26257,
                rpc_port=26258,
                http_port=8080,
            )
            with (
                mock.patch.object(
                    runtime,
                    "_node_identity",
                    return_value={"node_id": "1"},
                ),
                mock.patch.object(
                    validation.migrations,
                    "read_graceful_shutdown_budget",
                    return_value={
                        "grace_seconds": 40,
                        "phase_total_seconds": 25.0,
                    },
                ),
                mock.patch.object(
                    validation.migrations,
                    "run_process",
                    return_value=validation.migrations.ProcessResult(
                        0,
                        "drain ok\n",
                        "remaining: 0 (complete)\n",
                    ),
                ) as run_process,
                mock.patch.object(
                    validation.migrations,
                    "can_connect",
                    return_value=False,
                ),
            ):
                result = runtime.graceful_stop_and_remove(
                    object(),
                    owned_children_reaped=True,
                )
        self.assertEqual(result["cleanup_errors"], ())
        self.assertTrue(result["drain_command_completed"])
        self.assertTrue(result["drain_completion_marker"])
        self.assertFalse(result["force_kill_used"])
        self.assertTrue(result["process_exit_accepted"])
        self.assertEqual(result["process_exit_code"], 1)
        self.assertEqual(process.timeout, 40)
        command = run_process.call_args.args[0]
        self.assertIn("--self", command)
        self.assertIn("--shutdown", command)
        self.assertIn("--host=127.0.0.1:26258", command)
        self.assertIn("--drain-wait=40s", command)

    def test_graceful_timeout_uses_exact_pid_emergency_kill_and_fails(self) -> None:
        class Process:
            pid = 23456
            returncode = None
            killed = False

            def poll(self) -> int | None:
                return self.returncode

            def send_signal(self, observed: int) -> None:
                self.signal = observed

            def wait(self, timeout: float) -> int:
                if not self.killed:
                    raise subprocess.TimeoutExpired("cockroach", timeout)
                self.returncode = -9
                return -9

            def kill(self) -> None:
                self.killed = True

        with tempfile.TemporaryDirectory(
            prefix="mp_step10_timeout_",
            dir="/tmp",
        ) as raw:
            parent = Path("/tmp")
            runtime_dir = Path(raw)
            log = (runtime_dir / "server.log").open("w", encoding="utf-8")
            process = Process()
            runtime = validation.migrations.LocalRuntime(
                binary=Path("/verified/cockroach"),
                run_id="mp_step10_timeout",
                runtime_parent=parent,
                runtime_dir=runtime_dir,
                process=process,
                log_handle=log,
                sql_port=26257,
                rpc_port=26258,
                http_port=8080,
            )
            with (
                mock.patch.object(runtime, "_node_identity", return_value={}),
                mock.patch.object(
                    validation.migrations,
                    "read_graceful_shutdown_budget",
                    return_value={"grace_seconds": 40},
                ),
                mock.patch.object(
                    validation.migrations,
                    "run_process",
                    return_value=validation.migrations.ProcessResult(
                        0,
                        "drain ok\n",
                        "remaining: 0 (complete)\n",
                    ),
                ),
                mock.patch.object(
                    validation.migrations,
                    "can_connect",
                    return_value=False,
                ),
            ):
                result = runtime.graceful_stop_and_remove(
                    object(),
                    owned_children_reaped=True,
                )
        self.assertTrue(process.killed)
        self.assertEqual(process.signal, signal.SIGTERM)
        self.assertTrue(result["force_kill_used"])
        self.assertIn("GRACEFUL_SHUTDOWN_TIMEOUT", result["cleanup_errors"])
        self.assertIn(
            "DISPOSABLE_RUNTIME_FORCE_KILL_USED",
            result["cleanup_errors"],
        )

    def test_aws_identity_rejects_root_iam_user_and_wrong_role(self) -> None:
        fake_account = "123456" + "789012"
        candidates = (
            f"arn:aws:iam::{fake_account}:root",
            f"arn:aws:iam::{fake_account}:user/operator",
            f"arn:aws:sts::{fake_account}:assumed-role/OtherRole/session",
        )
        for arn in candidates:
            with self.subTest(arn=arn):
                with mock.patch.object(
                    validation,
                    "_aws_json",
                    return_value={"Arn": arn},
                ):
                    with self.assertRaises(validation.Step10ValidationError):
                        validation._aws_identity(Path("/tmp/aws"))

    def test_script_has_no_destructive_aws_or_external_cleanup_surface(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        lower = source.casefold()
        for forbidden in (
            "delete_object",
            "delete_bucket",
            "bypass-governance-retention",
            "pkill cockroach",
            "killall cockroach",
            "genericparser",
            "def chunk",
        ):
            self.assertNotIn(forbidden, lower)
        self.assertNotIn("/media/l/lsc_data", lower)
        tree = ast.parse(source)
        shell_keywords = [
            keyword
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            for keyword in node.keywords
            if keyword.arg == "shell"
        ]
        self.assertEqual(shell_keywords, [])

    def test_validation_module_import_is_inert(self) -> None:
        program = "\n".join(
            (
                "import importlib.util",
                "from pathlib import Path",
                "import sys",
                "from unittest import mock",
                f"path = Path({str(MODULE_PATH)!r})",
                "spec = importlib.util.spec_from_file_location('probe', path)",
                "module = importlib.util.module_from_spec(spec)",
                "sys.modules[spec.name] = module",
                "with mock.patch('subprocess.run', "
                "side_effect=AssertionError('process during import')), "
                "mock.patch('pathlib.Path.write_bytes', "
                "side_effect=AssertionError('write during import')):",
                "    spec.loader.exec_module(module)",
            )
        )
        environment = dict(os.environ)
        environment["PYTHONPATH"] = os.pathsep.join(
            (str(SOURCE_ROOT), str(SCRIPT_ROOT))
        )
        completed = __import__("subprocess").run(
            [sys.executable, "-c", program],
            cwd=REPOSITORY_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(
            completed.returncode,
            0,
            msg=completed.stderr or completed.stdout,
        )


if __name__ == "__main__":
    unittest.main()
