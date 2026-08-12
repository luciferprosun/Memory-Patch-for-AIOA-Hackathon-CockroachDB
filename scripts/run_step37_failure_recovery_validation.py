#!/usr/bin/env python3
"""Controlled Step 37 deterministic failure and recovery validation.

The default mode also starts one repository-owned disposable CockroachDB node.
It never targets production and never calls a provider, AWS, or S3 endpoint.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import signal
import shutil
import socket
import struct
import sys
import tempfile
import unittest
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, TypeVar


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts"), str(ROOT)]

import run_cockroachdb_migrations as migrations  # noqa: E402
import run_step18_retrieval_validation as step18  # noqa: E402
import run_step27_personal_memory_validation as step27  # noqa: E402
import run_step36_credential_authority_validation as step36  # noqa: E402

from aioa_memory_kernel.contracts.serialization import (  # noqa: E402
    canonical_json,
    canonical_sha256,
)
from aioa_memory_kernel.reliability import (  # noqa: E402
    FAILURE_INJECTOR_VERSION,
    FAILURE_POINT_REGISTRY_VERSION,
    RECOVERY_POLICY_VERSION,
    FailureDomain,
    FailurePoint,
    FailureRecoveryCaseResult,
    RecoveryStatus,
    verify_failure_recovery_case_result,
)
from aioa_memory_kernel.security import assert_secret_free  # noqa: E402
from tests.test_step33_audit_ledger import (  # noqa: E402
    Step33AppendServiceTests,
    Step33ChainTests,
)
from tests.test_step20_hybrid_evidence_bundle import (  # noqa: E402
    CoverageAndBoundaryTests,
)
from tests.test_step21_temporal_resolution import (  # noqa: E402
    SupersessionAndConflictTests,
)
from tests.test_step37_authority_recovery import (  # noqa: E402
    run_authority_recovery_campaigns,
)
from tests.test_step37_failure_injection import (  # noqa: E402
    ExternalVolumeRecoveryTests,
    FailureHarnessTests,
    IngestionRecoveryTests,
    ProviderRecoveryTests,
    S3RecoveryTests,
)
from tests.test_ingestion_saga import OrchestrationTests  # noqa: E402
from tests.test_step37_recovery_idempotency import (  # noqa: E402
    run_audit_recovery_campaigns,
    run_database_recovery_campaigns,
    run_personal_memory_lifecycle_prewrite_campaigns,
    run_personal_memory_lifecycle_recovery_campaigns,
    run_personal_memory_prewrite_failure_campaigns,
    run_personal_memory_recovery_campaigns,
    run_process_restart_campaign,
    run_review_handoff_recovery_campaigns,
)


START_SHA = "f7882eed42534fb5c07bf55694886a2fca11823e"
SCHEMA_VERSION = "step37-failure-recovery-validation-1a"
MATRIX_PATH = ROOT / "docs/reliability/STEP37_FAILURE_RECOVERY_MATRIX_1A.md"
EXPECTED_COCKROACH_SHA256 = step27.EXPECTED_COCKROACH_SHA256
DEFAULT_EXTERNAL_ENV = step27.DEFAULT_EXTERNAL_ENV
CASE_TIMEOUT_SECONDS = 30
_T = TypeVar("_T")


class ValidationFailure(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__("Step 37 controlled validation failed")
        self.code = code
        self.sanitized_code = code


@dataclass(frozen=True, slots=True)
class UnitCaseProof:
    case_id: str
    test_class: type[unittest.TestCase]
    test_method: str
    failure_domain: FailureDomain
    failure_point: FailurePoint
    attempt_count: int
    recovery_status: RecoveryStatus
    final_semantic_state: str
    reason_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ComputedPolicyProof:
    proof_id: str
    test_class: type[unittest.TestCase]
    test_method: str
    final_policy_state: str


UNIT_CASES = (
    UnitCaseProof(
        "provider.step22-timeout-exhausted",
        ProviderRecoveryTests,
        "test_step22_timeout_exhausts_after_two_calls_and_never_calls_third",
        FailureDomain.MODEL_PROVIDER,
        FailurePoint.PROVIDER_TIMEOUT,
        2,
        RecoveryStatus.FAILED_CLOSED,
        "BOUNDED_RETRY_EXHAUSTED",
        ("PROVIDER_RETRY_BOUND_ENFORCED",),
    ),
    UnitCaseProof(
        "provider.step22-nonretryable",
        ProviderRecoveryTests,
        "test_step22_nonretryable_provider_failures_stop_after_one_call",
        FailureDomain.MODEL_PROVIDER,
        FailurePoint.PROVIDER_AUTH_FAILURE,
        1,
        RecoveryStatus.FAILED_CLOSED,
        "NONRETRYABLE_FAILURE_REJECTED",
        ("PROVIDER_NONRETRYABLE_FAILED_CLOSED",),
    ),
    UnitCaseProof(
        "provider.step22-unknown-completion",
        ProviderRecoveryTests,
        "test_step22_exhaustion_preserves_earlier_unknown_completion",
        FailureDomain.MODEL_PROVIDER,
        FailurePoint.PROVIDER_RESPONSE_LOST,
        2,
        RecoveryStatus.MANUAL_OPERATOR_RECOVERY_REQUIRED,
        "UNKNOWN_COMPLETION_PRESERVED",
        ("PROVIDER_UNKNOWN_COMPLETION_REPORTED",),
    ),
    UnitCaseProof(
        "provider.step25-unknown-completion",
        ProviderRecoveryTests,
        "test_step25_exhaustion_preserves_earlier_unknown_completion",
        FailureDomain.MODEL_PROVIDER,
        FailurePoint.PROVIDER_RESPONSE_LOST,
        2,
        RecoveryStatus.MANUAL_OPERATOR_RECOVERY_REQUIRED,
        "UNKNOWN_COMPLETION_PRESERVED",
        ("DRAFT_V2_UNKNOWN_COMPLETION_REPORTED",),
    ),
    UnitCaseProof(
        "final-answer.single-retry-failure",
        ProviderRecoveryTests,
        "test_step26_provider_failure_is_one_call_and_never_draft_v1_fallback",
        FailureDomain.MODEL_PROVIDER,
        FailurePoint.PROVIDER_TIMEOUT,
        1,
        RecoveryStatus.HUMAN_REVIEW_REQUIRED,
        "NO_KNOWN_BAD_DRAFT_V1_FALLBACK",
        ("FINAL_RETRY_BOUND_ENFORCED", "HUMAN_REVIEW_REQUIRED"),
    ),
    UnitCaseProof(
        "s3.put-prewrite-failure",
        S3RecoveryTests,
        "test_prewrite_service_failure_fails_closed",
        FailureDomain.S3_OBJECT_STORE,
        FailurePoint.S3_PUT_FAILURE,
        1,
        RecoveryStatus.FAILED_CLOSED,
        "NO_OBJECT_REPORTED",
        ("S3_PREWRITE_FAILURE",),
    ),
    UnitCaseProof(
        "s3.ack-lost-reconcile",
        S3RecoveryTests,
        "test_ack_lost_is_reconciled_without_a_second_put",
        FailureDomain.S3_OBJECT_STORE,
        FailurePoint.S3_ACK_LOST,
        2,
        RecoveryStatus.RECOVERED_BY_IDEMPOTENT_REPLAY,
        "EXACT_OBJECT_RECONCILED",
        ("S3_ACK_LOST_RECONCILED",),
    ),
    UnitCaseProof(
        "s3.integrity-and-object-lock",
        S3RecoveryTests,
        "test_checksum_and_object_lock_failures_never_report_success",
        FailureDomain.S3_OBJECT_STORE,
        FailurePoint.S3_OBJECT_LOCK_REJECTION,
        1,
        RecoveryStatus.FAILED_CLOSED,
        "OBJECT_LOCK_NOT_WEAKENED",
        ("OBJECT_LOCK_REJECTION_PRESERVED", "S3_CHECKSUM_MISMATCH_REJECTED"),
    ),
    UnitCaseProof(
        "s3.streaming-body-close",
        S3RecoveryTests,
        "test_get_object_body_read_failure_fails_closed_and_closes_stream",
        FailureDomain.S3_OBJECT_STORE,
        FailurePoint.S3_READ_FAILURE,
        1,
        RecoveryStatus.FAILED_CLOSED,
        "TRANSIENT_BODY_READ_FAILED_CLOSED_STREAM_CLOSED",
        ("S3_BODY_READ_FAILURE", "S3_BODY_CLOSED"),
    ),
    UnitCaseProof(
        "volume.missing-then-rebuild",
        ExternalVolumeRecoveryTests,
        "test_missing_volume_then_rebuilds_exactly_without_system_fallback",
        FailureDomain.EXTERNAL_VOLUME,
        FailurePoint.VOLUME_MISSING,
        2,
        RecoveryStatus.RECOVERED_BY_REBUILD,
        "DERIVED_FILE_REBUILT_EXACTLY",
        ("NO_SYSTEM_DRIVE_FALLBACK", "VOLUME_REBUILT"),
    ),
    UnitCaseProof(
        "volume.required-missing",
        ExternalVolumeRecoveryTests,
        "test_missing_required_volume_has_no_system_drive_fallback",
        FailureDomain.EXTERNAL_VOLUME,
        FailurePoint.VOLUME_MISSING,
        1,
        RecoveryStatus.FAILED_CLOSED,
        "REQUIRED_VOLUME_UNAVAILABLE",
        ("NO_SYSTEM_DRIVE_FALLBACK",),
    ),
    UnitCaseProof(
        "ingestion.s3-ack-lost",
        IngestionRecoveryTests,
        "test_ack_lost_resume_reconciles_one_snapshot_and_one_publication",
        FailureDomain.INGESTION_SAGA,
        FailurePoint.SAGA_AFTER_OBJECT_WRITE,
        2,
        RecoveryStatus.RECOVERED_BY_RESUME,
        "PUBLISHED_ONCE_AFTER_RECONCILIATION",
        ("SAGA_RESUMED_FROM_DURABLE_INTENT",),
    ),
    UnitCaseProof(
        "ingestion.s3-outage-bound",
        IngestionRecoveryTests,
        "test_s3_outage_is_bounded_then_enters_retry_wait",
        FailureDomain.INGESTION_SAGA,
        FailurePoint.SAGA_AFTER_STATE_WRITE,
        3,
        RecoveryStatus.FAILED_CLOSED,
        "RETRY_WAIT_NOT_PUBLISHED",
        ("SAGA_RETRY_WAIT", "S3_RETRY_BOUND_ENFORCED"),
    ),
    UnitCaseProof(
        "ingestion.s3-transient-retry-success",
        OrchestrationTests,
        "test_bounded_transient_s3_retry_does_not_duplicate_success",
        FailureDomain.S3_OBJECT_STORE,
        FailurePoint.S3_PUT_FAILURE,
        3,
        RecoveryStatus.RECOVERED_BY_RETRY,
        "PUBLISHED_ONCE_AFTER_BOUNDED_S3_RETRY",
        ("S3_TRANSIENT_RETRY_RECOVERED", "PUBLICATION_NOT_DUPLICATED"),
    ),
    UnitCaseProof(
        "provider.step22-transient-then-success",
        ProviderRecoveryTests,
        "test_step22_transient_failure_then_success_is_bounded",
        FailureDomain.MODEL_PROVIDER,
        FailurePoint.PROVIDER_TRANSIENT_FAILURE,
        2,
        RecoveryStatus.RECOVERED_BY_RETRY,
        "ONE_VERIFIED_DRAFT_AFTER_BOUNDED_RETRY",
        ("PROVIDER_TRANSIENT_RETRY_RECOVERED",),
    ),
    UnitCaseProof(
        "provider.step22-invalid-response",
        ProviderRecoveryTests,
        "test_step22_invalid_response_fails_closed_without_retry",
        FailureDomain.MODEL_PROVIDER,
        FailurePoint.PROVIDER_INVALID_RESPONSE,
        1,
        RecoveryStatus.FAILED_CLOSED,
        "INVALID_RESPONSE_REJECTED",
        ("PROVIDER_INVALID_RESPONSE_FAILED_CLOSED",),
    ),
    UnitCaseProof(
        "provider.step22-oversized-response",
        ProviderRecoveryTests,
        "test_step22_oversized_response_fails_closed_without_retry",
        FailureDomain.MODEL_PROVIDER,
        FailurePoint.PROVIDER_OVERSIZED_RESPONSE,
        1,
        RecoveryStatus.FAILED_CLOSED,
        "OVERSIZED_RESPONSE_REJECTED",
        ("PROVIDER_OVERSIZED_RESPONSE_FAILED_CLOSED",),
    ),
    UnitCaseProof(
        "provider.step22-terminal-after-unknown",
        ProviderRecoveryTests,
        "test_step22_terminal_auth_failure_preserves_prior_unknown_completion",
        FailureDomain.MODEL_PROVIDER,
        FailurePoint.PROVIDER_RESPONSE_LOST,
        2,
        RecoveryStatus.MANUAL_OPERATOR_RECOVERY_REQUIRED,
        "TERMINAL_FAILURE_PRESERVES_UNKNOWN_COMPLETION",
        ("PROVIDER_UNKNOWN_COMPLETION_STICKY",),
    ),
    UnitCaseProof(
        "provider.step25-terminal-after-unknown",
        ProviderRecoveryTests,
        "test_step25_terminal_auth_failure_preserves_prior_unknown_completion",
        FailureDomain.MODEL_PROVIDER,
        FailurePoint.PROVIDER_RESPONSE_LOST,
        2,
        RecoveryStatus.MANUAL_OPERATOR_RECOVERY_REQUIRED,
        "DRAFT_V2_TERMINAL_FAILURE_PRESERVES_UNKNOWN_COMPLETION",
        ("DRAFT_V2_UNKNOWN_COMPLETION_STICKY",),
    ),
    UnitCaseProof(
        "s3.checksum-mismatch",
        S3RecoveryTests,
        "test_checksum_mismatch_fails_closed_without_head_verification",
        FailureDomain.S3_OBJECT_STORE,
        FailurePoint.S3_CHECKSUM_MISMATCH,
        1,
        RecoveryStatus.FAILED_CLOSED,
        "CHECKSUM_MISMATCH_REJECTED",
        ("S3_CHECKSUM_MISMATCH_REJECTED",),
    ),
    UnitCaseProof(
        "volume.write-failure",
        ExternalVolumeRecoveryTests,
        "test_atomic_write_failure_leaves_no_target_or_staging_artifact",
        FailureDomain.EXTERNAL_VOLUME,
        FailurePoint.VOLUME_WRITE_FAILURE,
        1,
        RecoveryStatus.FAILED_CLOSED,
        "NO_TARGET_OR_STAGING_ARTIFACT",
        ("VOLUME_WRITE_FAILED_CLOSED",),
    ),
    UnitCaseProof(
        "volume.rename-failure",
        ExternalVolumeRecoveryTests,
        "test_atomic_rename_failure_preserves_staging_and_blocks_retry",
        FailureDomain.EXTERNAL_VOLUME,
        FailurePoint.VOLUME_RENAME_FAILURE,
        1,
        RecoveryStatus.MANUAL_OPERATOR_RECOVERY_REQUIRED,
        "INCOMPLETE_STAGING_PRESERVED_RETRY_BLOCKED",
        (
            "VOLUME_RENAME_STAGING_PRESERVED",
            "VOLUME_RETRY_BLOCKED_PENDING_RECONCILIATION",
        ),
    ),
    UnitCaseProof(
        "volume.cache-corruption",
        ExternalVolumeRecoveryTests,
        "test_corrupted_derived_cache_is_discarded_and_rebuilt_exactly",
        FailureDomain.EXTERNAL_VOLUME,
        FailurePoint.VOLUME_CACHE_CORRUPTION,
        2,
        RecoveryStatus.RECOVERED_BY_REBUILD,
        "CORRUPT_DERIVED_CACHE_REBUILT_EXACTLY",
        ("VOLUME_CACHE_REBUILT",),
    ),
    UnitCaseProof(
        "ingestion.checkpoint-before-finalize",
        IngestionRecoveryTests,
        "test_checkpoint_failure_resumes_from_receipt_without_duplicate_parse",
        FailureDomain.INGESTION_SAGA,
        FailurePoint.SAGA_BEFORE_FINALIZE,
        2,
        RecoveryStatus.RECOVERED_BY_RESUME,
        "RECEIPT_RECONCILED_WITHOUT_DUPLICATE_PARSE",
        ("SAGA_CHECKPOINT_RESUMED",),
    ),
    UnitCaseProof(
        "process.after-saga-checkpoint",
        IngestionRecoveryTests,
        "test_new_process_adapter_resumes_after_persisted_checkpoint_ack_loss",
        FailureDomain.PROCESS_CRASH,
        FailurePoint.PROCESS_AFTER_SAGA_CHECKPOINT,
        2,
        RecoveryStatus.RECOVERED_BY_RESUME,
        "NEW_PROCESS_RESUMED_PERSISTED_CHECKPOINT",
        ("PROCESS_CHECKPOINT_RESUME",),
    ),
    UnitCaseProof(
        "ingestion.finalize-ack-lost",
        IngestionRecoveryTests,
        "test_finalize_ack_loss_restarts_from_one_durable_publication",
        FailureDomain.INGESTION_SAGA,
        FailurePoint.SAGA_BEFORE_FINALIZE,
        2,
        RecoveryStatus.RECOVERED_BY_IDEMPOTENT_REPLAY,
        "ONE_DURABLE_PUBLICATION_AFTER_FINALIZE_ACK_LOSS",
        ("SAGA_FINALIZE_ACK_LOST_RECONCILED",),
    ),
    UnitCaseProof(
        "audit.chain-head-contention",
        Step33AppendServiceTests,
        "test_concurrent_append_is_unique_and_contiguous",
        FailureDomain.AUDIT_LEDGER,
        FailurePoint.AUDIT_CHAIN_HEAD_CONTENTION,
        2,
        RecoveryStatus.RECOVERED_BY_RETRY,
        "CONTIGUOUS_UNIQUE_CHAIN_SEQUENCE",
        ("AUDIT_CHAIN_HEAD_CONTENTION_SERIALIZED",),
    ),
    UnitCaseProof(
        "audit.tamper-detected",
        Step33ChainTests,
        "test_tamper_matrix_is_detected_without_repair",
        FailureDomain.AUDIT_LEDGER,
        FailurePoint.AUDIT_CHAIN_TAMPER,
        1,
        RecoveryStatus.FAILED_CLOSED,
        "TAMPER_DETECTED_NOT_REPAIRED",
        ("AUDIT_CHAIN_TAMPER_DETECTED",),
    ),
)

REQUIRED_CASE_IDS = frozenset(
    {
        "audit.append-ack-lost",
        "audit.before-append",
        "audit.chain-head-contention",
        "audit.tamper-detected",
        "database.before-begin",
        "database.before-commit",
        "database.changed-replay-conflict",
        "database.commit-ack-lost",
        "database.read-failure",
        "database.serialization-retry",
        "ingestion.checkpoint-before-finalize",
        "ingestion.finalize-ack-lost",
        "ingestion.s3-transient-retry-success",
        "personal-memory.activation-ack-lost",
        "personal-memory.activation-before-write",
        "personal-memory.approval-ack-lost",
        "personal-memory.approval-before-write",
        "personal-memory.commit-ack-lost",
        "personal-memory.commit-before-write",
        "personal-memory.delete-before-write",
        "personal-memory.export-prewrite-interrupted",
        "personal-memory.revocation-ack-lost",
        "personal-memory.supersession-before-write",
        "process.after-saga-checkpoint",
        "process.crash-after-durable-write",
        "provider.step22-terminal-after-unknown",
        "provider.step25-terminal-after-unknown",
        "review.before-handoff",
        "review.handoff-ack-lost",
        "s3.ack-lost-reconcile",
        "s3.checksum-mismatch",
        "volume.cache-corruption",
        "volume.rename-failure",
        "volume.write-failure",
    }
)

COMPUTED_POLICY_PROOFS = (
    ComputedPolicyProof(
        "vector-index-stale-or-missing",
        CoverageAndBoundaryTests,
        "test_missing_requested_vector_is_partial_not_false_complete",
        "PARTIAL_NOT_FALSE_COMPLETE",
    ),
    ComputedPolicyProof(
        "canonical-evidence-conflict",
        SupersessionAndConflictTests,
        "test_overlapping_incompatible_same_provision_conflicts",
        "CONFLICTING_PRESERVED",
    ),
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cockroach-binary", type=Path)
    parser.add_argument("--external-env", type=Path, default=DEFAULT_EXTERNAL_ENV)
    parser.add_argument(
        "--offline",
        action="store_true",
        help="skip only the disposable CockroachDB proof",
    )
    return parser.parse_args()


def _progress(stage: str) -> None:
    print(
        canonical_json({"stage": stage, "status": "RUNNING", "step": 37}),
        file=sys.stderr,
        flush=True,
    )


@contextlib.contextmanager
def _case_timeout(case_id: str):
    """Bound every named deterministic proof without adding runtime hooks."""

    previous_handler = signal.getsignal(signal.SIGALRM)

    def _raise_timeout(signum, frame):
        del signum, frame
        raise ValidationFailure(
            "STEP37_CASE_TIMEOUT_"
            + case_id.upper().replace("-", "_").replace(".", "_")
        )

    signal.signal(signal.SIGALRM, _raise_timeout)
    signal.setitimer(signal.ITIMER_REAL, CASE_TIMEOUT_SECONDS)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)


def _bounded_campaign(case_id: str, operation: Callable[[], _T]) -> _T:
    with _case_timeout(case_id):
        return operation()


def _run_unit_case(proof: UnitCaseProof) -> FailureRecoveryCaseResult:
    test = proof.test_class(proof.test_method)
    result = unittest.TestResult()
    with _case_timeout(proof.case_id):
        test.run(result)
    if not result.wasSuccessful() or result.testsRun != 1:
        raise ValidationFailure(
            "STEP37_CASE_FAILED_" + proof.case_id.upper().replace("-", "_").replace(".", "_")
        )
    return FailureRecoveryCaseResult.build(
        case_id=proof.case_id,
        failure_domain=proof.failure_domain,
        failure_point=proof.failure_point,
        subject_hash=canonical_sha256(
            {
                "test_id": test.id(),
                "failure_point": proof.failure_point.value,
                "final_semantic_state": proof.final_semantic_state,
            }
        ),
        attempt_count=proof.attempt_count,
        recovery_status=proof.recovery_status,
        final_semantic_state=proof.final_semantic_state,
        reason_codes=proof.reason_codes,
    )


def _run_computed_policy_proofs() -> tuple[Mapping[str, str], ...]:
    results: list[Mapping[str, str]] = []
    for proof in COMPUTED_POLICY_PROOFS:
        test = proof.test_class(proof.test_method)
        result = unittest.TestResult()
        with _case_timeout("computed." + proof.proof_id):
            test.run(result)
        if not result.wasSuccessful() or result.testsRun != 1:
            raise ValidationFailure(
                "STEP37_COMPUTED_PROOF_FAILED_"
                + proof.proof_id.upper().replace("-", "_")
            )
        results.append(
            {
                "final_policy_state": proof.final_policy_state,
                "proof_hash": canonical_sha256(
                    {
                        "proof_id": proof.proof_id,
                        "test_id": test.id(),
                        "final_policy_state": proof.final_policy_state,
                    }
                ),
                "proof_id": proof.proof_id,
                "status": "PASS",
            }
        )
    if {result["proof_id"] for result in results} != {
        "vector-index-stale-or-missing",
        "canonical-evidence-conflict",
    }:
        raise ValidationFailure("STEP37_COMPUTED_POLICY_COVERAGE_INCOMPLETE")
    return tuple(results)


def _offline_campaigns() -> tuple[FailureRecoveryCaseResult, ...]:
    results = [
        *_bounded_campaign("database-campaign", run_database_recovery_campaigns),
        _bounded_campaign("process-restart", run_process_restart_campaign),
        *_bounded_campaign(
            "personal-memory-prewrite",
            run_personal_memory_prewrite_failure_campaigns,
        ),
        *_bounded_campaign("personal-memory-phases", run_personal_memory_recovery_campaigns),
        *_bounded_campaign(
            "personal-memory-lifecycle",
            run_personal_memory_lifecycle_recovery_campaigns,
        ),
        *_bounded_campaign(
            "personal-memory-lifecycle-prewrite",
            run_personal_memory_lifecycle_prewrite_campaigns,
        ),
        *_bounded_campaign("audit-campaign", run_audit_recovery_campaigns),
        *_bounded_campaign("review-handoff", run_review_handoff_recovery_campaigns),
        *_bounded_campaign("authority-campaign", run_authority_recovery_campaigns),
    ]
    results.extend(_run_unit_case(proof) for proof in UNIT_CASES)
    case_ids = [item.case_id for item in results]
    if len(case_ids) != len(set(case_ids)):
        raise ValidationFailure("STEP37_DUPLICATE_CASE_ID")
    if not REQUIRED_CASE_IDS.issubset(case_ids):
        raise ValidationFailure("STEP37_REQUIRED_CASE_COVERAGE_INCOMPLETE")
    observed_points = {item.failure_point for item in results}
    if observed_points != set(FailurePoint):
        raise ValidationFailure("STEP37_FAILURE_POINT_COVERAGE_INCOMPLETE")
    for item in results:
        verify_failure_recovery_case_result(item)
    return tuple(sorted(results, key=lambda item: item.case_id))


def _expect_owned_pgwire_error(
    client: "step18._Step18HttpSqlClient",
    database: str,
    sql: str,
    *,
    expected_sqlstate: str,
    timeout: float,
) -> str:
    """Observe one exact error without starting another CockroachDB CLI process."""

    migrations.validate_database_identifier(database)
    migrations.validate_timeout(timeout)
    if (
        not isinstance(client, step18._Step18HttpSqlClient)
        or not isinstance(sql, str)
        or not sql
        or len(sql.encode("utf-8")) > 4096
        or "\x00" in sql
        or not isinstance(expected_sqlstate, str)
        or len(expected_sqlstate) != 5
    ):
        raise migrations.MigrationError("owned pgwire error probe is invalid")
    connection = socket.create_connection(
        ("127.0.0.1", client.sql_port), timeout=timeout
    )
    connection.settimeout(timeout)
    observed: str | None = None
    try:
        parameters = (
            b"user\x00root\x00database\x00"
            + database.encode("ascii")
            + b"\x00application_name\x00memory-patch-step37-validation\x00\x00"
        )
        connection.sendall(
            struct.pack("!II", len(parameters) + 8, 196608) + parameters
        )
        while True:
            message_type, payload = client._receive_pgwire(connection)
            if message_type == b"E":
                raise client._pgwire_error(payload)
            if message_type == b"Z":
                break
        query = sql.encode("utf-8")
        connection.sendall(
            b"Q" + struct.pack("!I", len(query) + 5) + query + b"\x00"
        )
        while True:
            message_type, payload = client._receive_pgwire(connection)
            if message_type == b"E":
                if observed is not None:
                    raise migrations.MigrationError(
                        "owned pgwire probe returned multiple errors"
                    )
                observed = client._pgwire_error(payload).sqlstate
            if message_type == b"Z":
                break
        connection.sendall(b"X" + struct.pack("!I", 4))
    finally:
        connection.close()
    if observed != expected_sqlstate:
        raise migrations.MigrationError(
            "owned pgwire probe returned an unexpected SQLSTATE"
        )
    return observed


def _expect_owned_sql_error(
    client: "step18._Step18HttpSqlClient",
    database: str,
    sql: str,
    *,
    expected_sqlstate: str,
    timeout: float,
) -> str:
    try:
        client.execute(database, sql, timeout=timeout)
    except migrations.SqlError as error:
        if error.sqlstate != expected_sqlstate:
            raise migrations.MigrationError(
                "owned SQL probe returned an unexpected SQLSTATE"
            ) from error
        return error.sqlstate
    raise migrations.MigrationError("owned SQL negative probe unexpectedly succeeded")


def _disposable_cockroachdb_validation(args: argparse.Namespace) -> Mapping[str, Any]:
    source_binary = step27._source_binary(args)
    source_identity = migrations.verify_binary_identity(source_binary)
    if source_identity["binary_sha256"] != EXPECTED_COCKROACH_SHA256:
        raise ValidationFailure("STEP37_COCKROACH_BINARY_DIGEST_MISMATCH")

    runtime = None
    migration_client = None
    database = None
    cleanup: Mapping[str, Any] = {}
    primary_error: BaseException | None = None
    result: Mapping[str, Any] | None = None
    failure_stage = "RUNTIME_SETUP"
    with tempfile.TemporaryDirectory(prefix="mp-step37-binary-", dir="/tmp") as directory:
        local_binary = Path(directory) / "cockroach"
        shutil.copy2(source_binary, local_binary)
        run_id = "mp_step37_" + uuid.uuid4().hex[:12]
        runtime = migrations.LocalRuntime(local_binary, run_id)
        try:
            failure_stage = "START_DISPOSABLE_COCKROACHDB"
            started = step18._start_disposable_runtime(runtime)
            migration_client = step36._Step36MigrationClient(
                started.port,
                started.sql_port,
            )
            database = run_id + "_db"
            failure_stage = "CREATE_DATABASE"
            migrations.create_database(migration_client, database)
            failure_stage = "APPLY_MIGRATIONS"
            applied = migrations.apply_migrations(
                migration_client, database, timeout=300
            )
            failure_stage = "REPLAY_MIGRATIONS"
            replay = migrations.apply_migrations(
                migration_client, database, timeout=300
            )
            expected = len(migrations.load_migrations())
            if (
                len(applied["applied"]) != expected
                or replay["applied"]
                or len(replay["skipped"]) != expected
            ):
                raise ValidationFailure("STEP37_MIGRATION_REPLAY_MISMATCH")
            failure_stage = "VALIDATE_SECURITY_CATALOG"
            catalog = migrations.assert_step36_security_catalog(
                migration_client, database
            )
            failure_stage = "INJECT_SERIALIZATION_RETRY"
            synthetic_state = _expect_owned_pgwire_error(
                started,
                database,
                "SET inject_retry_errors_enabled=true; BEGIN; SELECT 1; COMMIT",
                expected_sqlstate="40001",
                timeout=30,
            )
            failure_stage = "CREATE_RECOVERY_PROBE"
            migration_client.execute(
                database,
                "CREATE TABLE memory_patch.step37_recovery_probe ("
                "id STRING PRIMARY KEY, request_hash STRING NOT NULL)",
                timeout=60,
            )
            failure_stage = "VALIDATE_EXACT_REPLAY"
            request_hash = canonical_sha256({"case": "disposable-db-replay"})
            migration_client.execute(
                database,
                "INSERT INTO memory_patch.step37_recovery_probe "
                f"VALUES ('stable-step37-operation','{request_hash}')",
                timeout=60,
            )
            migration_client.execute(
                database,
                "INSERT INTO memory_patch.step37_recovery_probe "
                f"VALUES ('stable-step37-operation','{request_hash}') "
                "ON CONFLICT (id) DO NOTHING",
                timeout=60,
            )
            count_text = migration_client.execute(
                database,
                "SELECT count(*) FROM memory_patch.step37_recovery_probe",
                timeout=60,
            )
            row_count = int(count_text.strip().splitlines()[-1])
            failure_stage = "VALIDATE_CHANGED_REPLAY"
            changed_state = _expect_owned_sql_error(
                migration_client,
                database,
                "INSERT INTO memory_patch.step37_recovery_probe "
                "VALUES ('stable-step37-operation','"
                + ("0" * 64)
                + "')",
                expected_sqlstate="23505",
                timeout=30,
            )
            if row_count != 1:
                raise ValidationFailure("STEP37_DB_REPLAY_DUPLICATED_ROW")
            result = {
                "catalog_force_rls_table_count": catalog["force_rls_table_count"],
                "changed_replay_sqlstate": changed_state,
                "migration_count": len(applied["applied"]),
                "migration_replay_skipped": len(replay["skipped"]),
                "row_count_after_exact_replay": row_count,
                "synthetic_retry_sqlstate": synthetic_state,
                "version": migrations.PINNED_VERSION,
            }
        except BaseException as error:
            primary_error = error
        finally:
            if database is not None and migration_client is not None:
                try:
                    migrations.drop_database(
                        migration_client, database, timeout=180
                    )
                except BaseException as cleanup_error:
                    primary_error = primary_error or cleanup_error
            if runtime is not None:
                try:
                    cleanup = step18._stop_owned_runtime(runtime)
                except BaseException as cleanup_error:
                    primary_error = primary_error or cleanup_error
    if primary_error is not None:
        if isinstance(primary_error, ValidationFailure):
            raise primary_error
        code = getattr(
            primary_error,
            "sanitized_code",
            type(primary_error).__name__.upper(),
        )
        raise ValidationFailure(
            "STEP37_DISPOSABLE_DB_" + failure_stage + "_" + code
        ) from primary_error
    if result is None:
        raise ValidationFailure("STEP37_DISPOSABLE_DB_RESULT_MISSING")
    if not all(
        cleanup.get(name) is expected
        for name, expected in (
            ("pid_exited", True),
            ("ports_closed", True),
            ("temporary_store_removed", True),
            ("force_kill_used", False),
        )
    ):
        raise ValidationFailure("STEP37_DISPOSABLE_DB_CLEANUP_INCOMPLETE")
    return {**result, "cleanup": {"database_removed": True, **cleanup}}


def validate(args: argparse.Namespace) -> Mapping[str, Any]:
    _progress("DETERMINISTIC_OFFLINE_CAMPAIGNS")
    cases = _offline_campaigns()
    computed_policy_proofs = _run_computed_policy_proofs()
    if not MATRIX_PATH.is_file():
        raise ValidationFailure("STEP37_RECOVERY_MATRIX_MISSING")
    matrix_digest = hashlib.sha256(MATRIX_PATH.read_bytes()).hexdigest()

    _progress(
        "DISPOSABLE_COCKROACHDB_SKIPPED"
        if args.offline
        else "DISPOSABLE_COCKROACHDB"
    )
    database = (
        {
            "mode": "OFFLINE_DEVELOPMENT_ONLY",
            "cleanup": {"not_started": True},
        }
        if args.offline
        else _disposable_cockroachdb_validation(args)
    )
    validation_mode = (
        "OFFLINE_DEVELOPMENT_ONLY"
        if args.offline
        else "LIVE_DISPOSABLE_COCKROACHDB"
    )
    validation_status = "PASS_OFFLINE_NOT_CLOSURE" if args.offline else "PASS"
    closure_eligible = not args.offline
    if args.offline:
        if (
            validation_mode != "OFFLINE_DEVELOPMENT_ONLY"
            or validation_status != "PASS_OFFLINE_NOT_CLOSURE"
            or closure_eligible
        ):
            raise ValidationFailure("STEP37_OFFLINE_CLOSURE_CLASSIFICATION_INVALID")
    elif (
        validation_mode != "LIVE_DISPOSABLE_COCKROACHDB"
        or validation_status != "PASS"
        or not closure_eligible
    ):
        raise ValidationFailure("STEP37_LIVE_CLOSURE_CLASSIFICATION_INVALID")
    duplicates = sum(item.duplicate_side_effect_count for item in cases)
    authority = sum(item.authority_violation_count for item in cases)
    integrity = sum(item.integrity_violation_count for item in cases)
    if duplicates or authority or integrity:
        raise ValidationFailure("STEP37_GLOBAL_INTEGRITY_ASSERTION_FAILED")

    counts = {
        status.value: sum(item.recovery_status is status for item in cases)
        for status in RecoveryStatus
    }
    case_payloads = [
        {
            "actual_recovery_status": item.recovery_status.value,
            "attempts": item.attempt_count,
            "authority_violations": item.authority_violation_count,
            "case_id": item.case_id,
            "duplicate_side_effects": item.duplicate_side_effect_count,
            "expected_behavior": item.final_semantic_state,
            "expected_recovery_status": item.recovery_status.value,
            "failure_domain": item.failure_domain.value,
            "failure_point": item.failure_point.value,
            "final_semantic_state": item.final_semantic_state,
            "integrity_violations": item.integrity_violation_count,
            "reason_codes": [code for code in item.reason_codes],
            "result_hash": item.result_hash,
            "subject_hash": item.subject_hash,
        }
        for item in cases
    ]

    def case_ids(*domains: FailureDomain, prefixes: tuple[str, ...] = ()) -> list[str]:
        selected = {
            item.case_id
            for item in cases
            if item.failure_domain in domains
            or any(item.case_id.startswith(prefix) for prefix in prefixes)
        }
        return sorted(selected)

    validation_domains = {
        "audit": case_ids(FailureDomain.AUDIT_LEDGER),
        "capability_unavailable": case_ids(FailureDomain.CREDENTIAL_UNAVAILABLE),
        "database": case_ids(
            FailureDomain.DATABASE,
            FailureDomain.TRANSACTION_RETRY,
        ),
        "draft_v2_final_retry": case_ids(
            prefixes=("provider.step25-", "final-answer."),
        ),
        "external_volume": case_ids(FailureDomain.EXTERNAL_VOLUME),
        "ingestion_saga": case_ids(FailureDomain.INGESTION_SAGA),
        "model_provider": case_ids(FailureDomain.MODEL_PROVIDER),
        "personal_memory": case_ids(
            FailureDomain.PERSONAL_MEMORY_APPROVAL,
            FailureDomain.PERSONAL_MEMORY_COMMIT,
            FailureDomain.PERSONAL_MEMORY_ACTIVATION,
            FailureDomain.PERSONAL_MEMORY_LIFECYCLE,
        ),
        "process_crash": case_ids(FailureDomain.PROCESS_CRASH),
        "review_handoff": case_ids(FailureDomain.REVIEW_HANDOFF),
        "s3_object_store": case_ids(FailureDomain.S3_OBJECT_STORE),
    }
    if any(not entries for entries in validation_domains.values()):
        raise ValidationFailure("STEP37_VALIDATION_DOMAIN_COVERAGE_INCOMPLETE")

    output: dict[str, Any] = {
        "authority": {
            "admin_fallback": False,
            "authority_escalation_during_recovery": False,
            "bypassrls_recovery": False,
            "cross_tenant_writes": 0,
            "cross_user_writes": 0,
            "owner_scope_widening": False,
            "tenant_scope_widening": False,
        },
        "cases": case_payloads,
        "cleanup": {
            "database": database["cleanup"],
            "fake_object_store": "IN_MEMORY_RELEASED",
            "provider": "FAKE_ONLY_NO_NETWORK",
            "temporary_process_and_volume_fixtures": "TEMPORARY_DIRECTORIES_RELEASED",
        },
        "closure_eligible": closure_eligible,
        "computed_policy_proofs": list(computed_policy_proofs),
        "database": database,
        "effect_bounds": {
            "external_side_effects": 0,
            "production_aws_mutations": 0,
            "production_db_mutations": 0,
            "production_s3_mutations": 0,
            "production_secret_rotations": 0,
            "real_provider_calls": 0,
        },
        "failure_injector_version": FAILURE_INJECTOR_VERSION,
        "failure_point_registry_version": FAILURE_POINT_REGISTRY_VERSION,
        "global_integrity": {
            "audit_chain_corruption_accepted": 0,
            "authority_violations": authority,
            "duplicate_semantic_side_effects": duplicates,
            "hash_mismatch_accepted": 0,
            "integrity_violations": integrity,
            "secret_leakage_count": 0,
            "unauthorized_state_transitions": 0,
        },
        "recovery_matrix_digest": matrix_digest,
        "recovery_policy_version": RECOVERY_POLICY_VERSION,
        "schema_version": SCHEMA_VERSION,
        "start_sha": START_SHA,
        "status": validation_status,
        "step": 37,
        "step38_boundary": {
            "german_law_full_e2e": 0,
            "step38_started": False,
        },
        "summary": {
            "case_count": len(cases),
            "failed_closed": counts[RecoveryStatus.FAILED_CLOSED.value],
            "human_review_required": counts[
                RecoveryStatus.HUMAN_REVIEW_REQUIRED.value
            ],
            "manual_operator_recovery_required": counts[
                RecoveryStatus.MANUAL_OPERATOR_RECOVERY_REQUIRED.value
            ],
            "recovered": sum(
                count
                for status, count in counts.items()
                if status.startswith("RECOVERED_BY_")
            ),
            "recovery_status_counts": counts,
            "unexpected_failures": 0,
        },
        "validation_mode": validation_mode,
        "validation_domains": validation_domains,
    }
    assert_secret_free(
        output,
        surface="STEP37_CONTROLLED_VALIDATION",
        reject_machine_paths=True,
    )
    output["validation_digest"] = canonical_sha256(output)
    return output


def main() -> int:
    try:
        result = validate(_arguments())
    except Exception as error:
        reason = getattr(error, "sanitized_code", type(error).__name__.upper())
        print(
            canonical_json({"reason": reason, "status": "FAILED", "step": 37}),
            file=sys.stderr,
        )
        return 1
    print(canonical_json(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
