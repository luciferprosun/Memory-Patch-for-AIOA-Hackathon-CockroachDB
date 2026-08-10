"""Step 6 retry-safe persistence, idempotency, and immutable-write tests."""

from __future__ import annotations

import hashlib
import ast
import json
import sys
import unittest
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tests._support import REPOSITORY_ROOT, SOURCE_ROOT


if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from aioa_memory_kernel.persistence import (  # noqa: E402
    AccessMode,
    AuditEventRecord,
    BeginOperation,
    CockroachPersistenceRepository,
    EvidenceItemRecord,
    ExternalReferenceIdentity,
    IdempotencyConflictError,
    IdempotencyService,
    ImmutableRecordConflictError,
    KernelRunRecord,
    OperationStateConflictError,
    OperationStatus,
    PersistenceConfigurationError,
    PersistenceTransactionError,
    RequestContext,
    RetryExhaustedError,
    SerializableTransactionRunner,
    SourceSnapshotRecord,
    TransactionBoundaryViolation,
    assert_no_open_persistence_transaction,
    digest_canonical_request,
    operation_from_row,
)


NOW = datetime(2035, 1, 2, 3, 4, 5, tzinfo=UTC)
DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
TENANT_A = "tenant-step6-a"
TENANT_B = "tenant-step6-b"
USER_A1 = "user-step6-a1"
USER_A2 = "user-step6-a2"


class DatabaseSignal(Exception):
    def __init__(
        self,
        sqlstate: str | None,
        *,
        sanitized_code: str | None = None,
    ) -> None:
        super().__init__("sensitive driver detail must not escape")
        self.sqlstate = sqlstate
        self.sanitized_code = sanitized_code


class FakeCursor:
    def __init__(
        self,
        events: list[str],
        work_error: DatabaseSignal | None = None,
        rows: list[Mapping[str, object] | None] | None = None,
    ) -> None:
        self.events = events
        self.work_error = work_error
        self.rows = list(rows or [])
        self.closed = False

    def execute(self, sql: str, parameters: object = None) -> object:
        del parameters
        normalized = " ".join(sql.split())
        self.events.append(f"execute:{normalized}")
        if normalized == "WORK" and self.work_error is not None:
            raise self.work_error
        return self

    def fetchone(self) -> Mapping[str, object] | None:
        return None if not self.rows else self.rows.pop(0)

    def fetchall(self) -> list[Mapping[str, object]]:
        return []

    def close(self) -> None:
        self.closed = True
        self.events.append("cursor.close")


class FakeConnection:
    def __init__(
        self,
        events: list[str],
        *,
        work_error: DatabaseSignal | None = None,
        commit_error: DatabaseSignal | None = None,
    ) -> None:
        self.events = events
        self.cursor_value = FakeCursor(events, work_error)
        self.commit_error = commit_error

    def cursor(self) -> FakeCursor:
        self.events.append("connection.cursor")
        return self.cursor_value

    def commit(self) -> None:
        self.events.append("commit")
        if self.commit_error is not None:
            raise self.commit_error

    def rollback(self) -> None:
        self.events.append("rollback")

    def close(self) -> None:
        self.events.append("connection.close")


class SequenceFactory:
    def __init__(
        self,
        states: list[str | None],
        events: list[str],
        *,
        on_commit: bool = False,
    ) -> None:
        self.states = list(states)
        self.events = events
        self.on_commit = on_commit
        self.calls = 0

    def __call__(self) -> FakeConnection:
        state = self.states[self.calls] if self.calls < len(self.states) else None
        self.calls += 1
        error = None if state is None else DatabaseSignal(state)
        return FakeConnection(
            self.events,
            work_error=None if self.on_commit else error,
            commit_error=error if self.on_commit else None,
        )


class ScriptedTransaction:
    def __init__(self, responses: list[Mapping[str, object] | None]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, object]] = []
        self.active = True

    def execute(self, sql: str, parameters: object = None) -> None:
        self.calls.append((sql, parameters))

    def fetch_one(
        self,
        sql: str,
        parameters: object = None,
    ) -> Mapping[str, object] | None:
        self.calls.append((sql, parameters))
        if not self.responses:
            raise AssertionError("unexpected fetch_one")
        return self.responses.pop(0)

    def fetch_all(self, sql: str, parameters: object = None) -> tuple[()]:
        self.calls.append((sql, parameters))
        return ()


def external_identity(**overrides: str) -> ExternalReferenceIdentity:
    values = {
        "origin_kind": "agent-artifact",
        "origin_system": "synthetic-system",
        "origin_version": "1.0",
        "adapter_version": "adapter-1",
        "artifact_kind": "neutral-output",
        "external_ref": "external-reference-1",
    }
    values.update(overrides)
    return ExternalReferenceIdentity(**values)


def begin_request(**overrides: Any) -> BeginOperation:
    values: dict[str, Any] = {
        "operation_id": "operation-1",
        "tenant_id": TENANT_A,
        "owner_user_id": USER_A1,
        "operation_kind": "PUT_EVIDENCE",
        "idempotency_key": "key-1",
        "request_digest": DIGEST_A,
        "scope_digest": DIGEST_B,
        "created_at": NOW,
        "external_identity": None,
    }
    values.update(overrides)
    return BeginOperation(**values)


def operation_row(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "tenant_id": TENANT_A,
        "operation_id": "operation-1",
        "schema_version": "1.0.0",
        "owner_user_id": USER_A1,
        "operation_kind": "PUT_EVIDENCE",
        "idempotency_key": "key-1",
        "request_digest": DIGEST_A,
        "scope_digest": DIGEST_B,
        "status": "IN_PROGRESS",
        "attempt_count": 1,
        "result_ref": None,
        "result_digest": None,
        "last_sqlstate": None,
        "sanitized_error_code": None,
        "created_at": NOW,
        "updated_at": NOW,
        "completed_at": None,
        "origin_kind": None,
        "origin_system": None,
        "origin_version": None,
        "adapter_version": None,
        "artifact_kind": None,
        "external_ref": None,
    }
    values.update(overrides)
    return values


def row_with_external(**overrides: object) -> dict[str, object]:
    identity = external_identity()
    values = operation_row(
        origin_kind=identity.origin_kind,
        origin_system=identity.origin_system,
        origin_version=identity.origin_version,
        adapter_version=identity.adapter_version,
        artifact_kind=identity.artifact_kind,
        external_ref=identity.external_ref,
    )
    values.update(overrides)
    return values


class TransactionRetryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = RequestContext(
            TENANT_A,
            USER_A1,
            AccessMode.USER_PRIVATE,
        )

    def run_with_states(
        self,
        states: list[str | None],
        *,
        on_commit: bool = False,
    ) -> tuple[object, SequenceFactory, list[str]]:
        events: list[str] = []
        factory = SequenceFactory(states, events, on_commit=on_commit)
        runner = SerializableTransactionRunner(
            factory,
            sleep=lambda delay: events.append(f"sleep:{delay}"),
            backoff=lambda attempt: attempt / 100,
        )

        def callback(transaction: object) -> str:
            transaction.execute("WORK")  # type: ignore[attr-defined]
            return "committed"

        result = runner.run(self.context, callback, operation_kind="TEST")
        return result, factory, events

    def test_normal_serializable_transaction_commits_once(self) -> None:
        result, factory, events = self.run_with_states([None])
        self.assertEqual(result, "committed")
        self.assertEqual(factory.calls, 1)
        self.assertEqual(events.count("commit"), 1)
        self.assertNotIn("rollback", events)

    def test_one_40001_retries_complete_callback(self) -> None:
        result, factory, events = self.run_with_states(["40001", None])
        self.assertEqual(result, "committed")
        self.assertEqual(factory.calls, 2)
        self.assertEqual(
            sum(event == "execute:WORK" for event in events),
            2,
        )

    def test_multiple_40001_signals_retry_then_succeed(self) -> None:
        result, factory, events = self.run_with_states(
            ["40001", "40001", "40001", None]
        )
        self.assertEqual(result, "committed")
        self.assertEqual(factory.calls, 4)
        self.assertEqual(events.count("rollback"), 3)

    def test_ten_40001_attempts_exhaust(self) -> None:
        events: list[str] = []
        factory = SequenceFactory(["40001"] * 10, events)
        runner = SerializableTransactionRunner(
            factory,
            sleep=lambda delay: events.append(f"sleep:{delay}"),
            backoff=lambda attempt: 0,
        )
        with self.assertRaises(RetryExhaustedError) as caught:
            runner.run(
                self.context,
                lambda transaction: transaction.execute("WORK"),
                operation_kind="TEST",
            )
        self.assertEqual(caught.exception.sqlstate, "40001")
        self.assertEqual(caught.exception.attempt, 10)
        self.assertEqual(factory.calls, 10)
        self.assertEqual(events.count("rollback"), 10)
        self.assertEqual(sum(event.startswith("sleep:") for event in events), 9)

    def test_rollback_and_marker_clear_before_backoff(self) -> None:
        events: list[str] = []
        factory = SequenceFactory(["40001", None], events)

        def sleep(delay: float) -> None:
            self.assertGreaterEqual(delay, 0)
            self.assertEqual(events[-2:], ["cursor.close", "connection.close"])
            assert_no_open_persistence_transaction()
            events.append("sleep")

        runner = SerializableTransactionRunner(
            factory,
            sleep=sleep,
            backoff=lambda attempt: 0,
        )
        runner.run(
            self.context,
            lambda transaction: transaction.execute("WORK"),
        )
        self.assertIn("sleep", events)

    def test_commit_40001_retries_complete_callback(self) -> None:
        result, factory, events = self.run_with_states(
            ["40001", None],
            on_commit=True,
        )
        self.assertEqual(result, "committed")
        self.assertEqual(factory.calls, 2)
        self.assertEqual(events.count("rollback"), 1)

    def test_non_40001_is_never_retried(self) -> None:
        for state in ("23503", "23505", "23514", "42501", "22023", "0A000", None):
            with self.subTest(sqlstate=state):
                events: list[str] = []
                factory = SequenceFactory([state], events)
                runner = SerializableTransactionRunner(factory, sleep=lambda _: None)
                if state is None:
                    callback = lambda transaction: (_ for _ in ()).throw(  # noqa: E731
                        DatabaseSignal(None)
                    )
                else:
                    callback = lambda transaction: transaction.execute("WORK")  # noqa: E731
                with self.assertRaises(PersistenceTransactionError) as caught:
                    runner.run(self.context, callback)
                self.assertEqual(factory.calls, 1)
                self.assertEqual(caught.exception.sqlstate, state)
                self.assertEqual(
                    caught.exception.sanitized_code,
                    (
                        f"SQLSTATE_{state}"
                        if state is not None
                        else "UNCLASSIFIED_DATABASE_FAILURE"
                    ),
                )

    def test_driver_error_text_is_not_exposed(self) -> None:
        events: list[str] = []
        runner = SerializableTransactionRunner(
            SequenceFactory(["23505"], events),
            sleep=lambda _: None,
        )
        with self.assertRaises(PersistenceTransactionError) as caught:
            runner.run(
                self.context,
                lambda transaction: transaction.execute("WORK"),
            )
        self.assertNotIn("sensitive", str(caught.exception))

    def test_bounded_driver_code_is_preserved_without_driver_text(self) -> None:
        runner = SerializableTransactionRunner(
            SequenceFactory([None], []),
            sleep=lambda _: None,
        )
        with self.assertRaises(PersistenceTransactionError) as caught:
            runner.run(
                self.context,
                lambda transaction: (_ for _ in ()).throw(
                    DatabaseSignal(
                        None,
                        sanitized_code="COCKROACH_CLI_STATEMENT_TIMEOUT",
                    )
                ),
            )
        self.assertEqual(
            caught.exception.sanitized_code,
            "COCKROACH_CLI_STATEMENT_TIMEOUT",
        )
        self.assertNotIn("sensitive", str(caught.exception))

    def test_untrusted_driver_code_is_not_preserved(self) -> None:
        runner = SerializableTransactionRunner(
            SequenceFactory([None], []),
            sleep=lambda _: None,
        )
        with self.assertRaises(PersistenceTransactionError) as caught:
            runner.run(
                self.context,
                lambda transaction: (_ for _ in ()).throw(
                    DatabaseSignal(None, sanitized_code="unsafe detail /tmp/value")
                ),
            )
        self.assertEqual(
            caught.exception.sanitized_code,
            "UNCLASSIFIED_DATABASE_FAILURE",
        )

    def test_external_call_guard_fails_inside_transaction(self) -> None:
        runner = SerializableTransactionRunner(
            SequenceFactory([None], []),
            sleep=lambda _: None,
        )
        with self.assertRaises(TransactionBoundaryViolation):
            runner.run(
                self.context,
                lambda transaction: assert_no_open_persistence_transaction(),
            )
        assert_no_open_persistence_transaction()

    def test_marker_clears_after_commit_and_rollback(self) -> None:
        self.run_with_states([None])
        assert_no_open_persistence_transaction()
        events: list[str] = []
        runner = SerializableTransactionRunner(
            SequenceFactory(["23503"], events),
            sleep=lambda _: None,
        )
        with self.assertRaises(PersistenceTransactionError):
            runner.run(
                self.context,
                lambda transaction: transaction.execute("WORK"),
            )
        assert_no_open_persistence_transaction()

    def test_nested_transaction_fails_closed(self) -> None:
        runner = SerializableTransactionRunner(
            SequenceFactory([None], []),
            sleep=lambda _: None,
        )
        with self.assertRaises(TransactionBoundaryViolation):
            runner.run(
                self.context,
                lambda transaction: runner.run(
                    self.context,
                    lambda nested: None,
                ),
            )

    def test_retained_transaction_handle_is_invalid(self) -> None:
        runner = SerializableTransactionRunner(
            SequenceFactory([None], []),
            sleep=lambda _: None,
        )
        retained: list[object] = []
        runner.run(self.context, lambda transaction: retained.append(transaction))
        with self.assertRaises(TransactionBoundaryViolation):
            retained[0].execute("WORK")  # type: ignore[attr-defined]

    def test_context_is_set_inside_every_retry_attempt(self) -> None:
        _, _, events = self.run_with_states(["40001", None])
        context_calls = [
            event
            for event in events
            if "memory_patch.set_request_context" in event
        ]
        begin_calls = [
            event
            for event in events
            if event == "execute:BEGIN TRANSACTION ISOLATION LEVEL SERIALIZABLE"
        ]
        self.assertEqual(len(context_calls), 2)
        self.assertEqual(len(begin_calls), 2)

    def test_base_exception_is_cleaned_up_and_propagated(self) -> None:
        events: list[str] = []
        runner = SerializableTransactionRunner(
            SequenceFactory([None], events),
            sleep=lambda _: None,
        )

        with self.assertRaises(KeyboardInterrupt):
            runner.run(
                self.context,
                lambda transaction: (_ for _ in ()).throw(KeyboardInterrupt()),
            )

        self.assertIn("rollback", events)
        self.assertEqual(events[-2:], ["cursor.close", "connection.close"])
        assert_no_open_persistence_transaction()


class ModelAndDigestTests(unittest.TestCase):
    def test_shared_and_private_context_shapes(self) -> None:
        shared = RequestContext(
            TENANT_A,
            None,
            AccessMode.TENANT_SHARED,
        )
        private = RequestContext(
            TENANT_A,
            USER_A1,
            AccessMode.USER_PRIVATE,
        )
        self.assertIsNone(shared.user_id)
        self.assertEqual(private.user_id, USER_A1)
        with self.assertRaises(PersistenceConfigurationError):
            RequestContext(TENANT_A, USER_A1, AccessMode.TENANT_SHARED)
        with self.assertRaises(PersistenceConfigurationError):
            RequestContext(TENANT_A, None, AccessMode.USER_PRIVATE)

    def test_canonical_digest_uses_existing_kernel_format(self) -> None:
        first = digest_canonical_request({"b": 2, "a": 1})
        second = digest_canonical_request({"a": 1, "b": 2})
        self.assertEqual(first, second)
        self.assertEqual(len(first), 64)

    def test_partial_external_identity_is_rejected(self) -> None:
        with self.assertRaises(PersistenceConfigurationError):
            begin_request(external_identity="external-reference-only")

    def test_unknown_digest_shape_is_rejected(self) -> None:
        with self.assertRaises(PersistenceConfigurationError):
            begin_request(request_digest="not-a-digest")

    def test_completed_operation_timestamp_must_not_exceed_updated_at(self) -> None:
        with self.assertRaises(PersistenceConfigurationError):
            operation_from_row(
                operation_row(
                    status="COMPLETED",
                    result_digest="e" * 64,
                    completed_at=NOW.replace(second=6),
                )
            )


class IdempotencyServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = IdempotencyService(clock=lambda: NOW)

    def test_first_claim_creates_one_in_progress_operation(self) -> None:
        transaction = ScriptedTransaction([None, operation_row()])
        claim = self.service.begin_or_resume_operation(
            transaction,
            begin_request(),
        )
        self.assertTrue(claim.may_proceed)
        self.assertFalse(claim.resumed)
        self.assertEqual(claim.operation.attempt_count, 1)

    def test_exact_duplicate_in_progress_returns_existing(self) -> None:
        transaction = ScriptedTransaction([operation_row()])
        claim = self.service.begin_or_resume_operation(
            transaction,
            begin_request(operation_id="new-caller-operation-id"),
        )
        self.assertFalse(claim.may_proceed)
        self.assertEqual(claim.operation.operation_id, "operation-1")
        self.assertEqual(len(transaction.calls), 1)

    def test_same_key_different_request_or_scope_digest_conflicts(self) -> None:
        for field, digest in (
            ("request_digest", "c" * 64),
            ("scope_digest", "d" * 64),
        ):
            with self.subTest(field=field):
                transaction = ScriptedTransaction([operation_row()])
                with self.assertRaises(IdempotencyConflictError):
                    self.service.begin_or_resume_operation(
                        transaction,
                        begin_request(**{field: digest}),
                    )
                self.assertEqual(len(transaction.calls), 1)

    def test_same_text_key_other_tenant_or_private_user_is_distinct(self) -> None:
        for overrides in (
            {"tenant_id": TENANT_B, "operation_id": "operation-b"},
            {"owner_user_id": USER_A2, "operation_id": "operation-a2"},
        ):
            with self.subTest(overrides=overrides):
                inserted = operation_row(**overrides)
                transaction = ScriptedTransaction([None, inserted])
                claim = self.service.begin_or_resume_operation(
                    transaction,
                    begin_request(**overrides),
                )
                self.assertTrue(claim.may_proceed)

    def test_completed_duplicate_returns_same_result(self) -> None:
        completed = operation_row(
            status="COMPLETED",
            result_ref="result-1",
            result_digest="e" * 64,
            completed_at=NOW,
        )
        transaction = ScriptedTransaction([completed])
        claim = self.service.begin_or_resume_operation(
            transaction,
            begin_request(),
        )
        self.assertFalse(claim.may_proceed)
        self.assertEqual(claim.operation.result_ref, "result-1")

    def test_interrupted_operation_resumes_with_monotonic_attempt(self) -> None:
        interrupted = operation_row(
            status="INTERRUPTED",
            attempt_count=2,
            last_sqlstate="40001",
            sanitized_error_code="INTERRUPTED",
        )
        resumed = operation_row(status="IN_PROGRESS", attempt_count=3)
        transaction = ScriptedTransaction([interrupted, resumed])
        claim = self.service.begin_or_resume_operation(
            transaction,
            begin_request(),
        )
        self.assertTrue(claim.may_proceed)
        self.assertTrue(claim.resumed)
        self.assertEqual(claim.operation.attempt_count, 3)

    def test_failed_final_does_not_resume(self) -> None:
        transaction = ScriptedTransaction(
            [
                operation_row(
                    status="FAILED_FINAL",
                    sanitized_error_code="FINAL",
                )
            ]
        )
        with self.assertRaises(OperationStateConflictError):
            self.service.begin_or_resume_operation(
                transaction,
                begin_request(),
            )

    def test_stale_resume_compare_and_set_fails(self) -> None:
        transaction = ScriptedTransaction(
            [operation_row(status="INTERRUPTED"), None]
        )
        with self.assertRaises(OperationStateConflictError):
            self.service.begin_or_resume_operation(
                transaction,
                begin_request(),
            )

    def test_complete_operation_and_exact_replay(self) -> None:
        completed = operation_row(
            status="COMPLETED",
            result_ref="result-1",
            result_digest="e" * 64,
            completed_at=NOW,
        )
        transaction = ScriptedTransaction([completed])
        result = self.service.complete_operation(
            transaction,
            tenant_id=TENANT_A,
            operation_id="operation-1",
            expected_attempt_count=1,
            result_ref="result-1",
            result_digest="e" * 64,
        )
        self.assertEqual(result.status, OperationStatus.COMPLETED)

        replay = ScriptedTransaction([None, completed])
        replayed = self.service.complete_operation(
            replay,
            tenant_id=TENANT_A,
            operation_id="operation-1",
            expected_attempt_count=1,
            result_ref="result-1",
            result_digest="e" * 64,
        )
        self.assertEqual(replayed.result_digest, "e" * 64)

    def test_complete_operation_rejects_stale_or_conflicting_result(self) -> None:
        existing = operation_row(status="IN_PROGRESS", attempt_count=2)
        transaction = ScriptedTransaction([None, existing])
        with self.assertRaises(OperationStateConflictError):
            self.service.complete_operation(
                transaction,
                tenant_id=TENANT_A,
                operation_id="operation-1",
                expected_attempt_count=1,
                result_digest="e" * 64,
            )

    def test_interrupted_and_failed_final_store_only_sanitized_metadata(self) -> None:
        for method_name, status in (
            ("mark_operation_interrupted", "INTERRUPTED"),
            ("mark_operation_failed_final", "FAILED_FINAL"),
        ):
            with self.subTest(method=method_name):
                row = operation_row(
                    status=status,
                    last_sqlstate="42501",
                    sanitized_error_code="SAFE_CODE",
                )
                transaction = ScriptedTransaction([row])
                method = getattr(self.service, method_name)
                result = method(
                    transaction,
                    tenant_id=TENANT_A,
                    operation_id="operation-1",
                    expected_attempt_count=1,
                    last_sqlstate="42501",
                    sanitized_error_code="SAFE_CODE",
                )
                self.assertEqual(result.status.value, status)
                parameters = transaction.calls[0][1]
                self.assertNotIn("sensitive", repr(parameters))

    def test_exact_external_tuple_deduplicates(self) -> None:
        request = begin_request(
            external_identity=external_identity(),
            idempotency_key="different-key",
            operation_id="different-operation",
        )
        existing = row_with_external()
        transaction = ScriptedTransaction([None, None, None, existing])
        claim = self.service.begin_or_resume_operation(transaction, request)
        self.assertFalse(claim.may_proceed)
        self.assertEqual(claim.operation.operation_id, "operation-1")

    def test_exact_external_tuple_different_digest_conflicts(self) -> None:
        request = begin_request(
            external_identity=external_identity(),
            request_digest="c" * 64,
        )
        transaction = ScriptedTransaction(
            [None, None, None, row_with_external()]
        )
        with self.assertRaises(IdempotencyConflictError):
            self.service.begin_or_resume_operation(transaction, request)

    def test_external_dimensions_are_part_of_insert_identity(self) -> None:
        identities = (
            external_identity(origin_system="other-system"),
            external_identity(origin_version="2.0"),
            external_identity(adapter_version="adapter-2"),
            external_identity(artifact_kind="other-kind"),
        )
        parameter_sets: set[tuple[object, ...]] = set()
        for index, identity in enumerate(identities):
            inserted = row_with_external(
                operation_id=f"operation-{index}",
                idempotency_key=f"key-{index}",
                origin_system=identity.origin_system,
                origin_version=identity.origin_version,
                adapter_version=identity.adapter_version,
                artifact_kind=identity.artifact_kind,
            )
            transaction = ScriptedTransaction([None, inserted])
            self.service.begin_or_resume_operation(
                transaction,
                begin_request(
                    operation_id=f"operation-{index}",
                    idempotency_key=f"key-{index}",
                    external_identity=identity,
                ),
            )
            parameter_sets.add(tuple(transaction.calls[1][1]))  # type: ignore[arg-type]
        self.assertEqual(len(parameter_sets), len(identities))


class ImmutableRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = CockroachPersistenceRepository()

    def test_kernel_run_first_insert_replay_and_conflict(self) -> None:
        record = KernelRunRecord(
            tenant_id=TENANT_A,
            kernel_run_id="run-1",
            user_id=USER_A1,
            personal_memory_space_id="space-a1",
            model_binding_id="model-binding-1",
            request_sha256=DIGEST_A,
            created_at=NOW,
        )
        row = {
            "tenant_id": TENANT_A,
            "kernel_run_id": "run-1",
            "user_id": USER_A1,
            "personal_memory_space_id": "space-a1",
            "model_binding_id": "model-binding-1",
            "request_sha256": DIGEST_A,
            "created_at": NOW,
            "completed_at": None,
        }
        inserted = self.repository.create_kernel_run(
            ScriptedTransaction([None, row]),
            record,
        )
        self.assertEqual(inserted["kernel_run_id"], "run-1")
        replayed = self.repository.create_kernel_run(
            ScriptedTransaction([row]),
            record,
        )
        self.assertEqual(replayed["request_sha256"], DIGEST_A)
        conflict = dict(row, request_sha256=DIGEST_B)
        with self.assertRaises(ImmutableRecordConflictError):
            self.repository.create_kernel_run(
                ScriptedTransaction([conflict]),
                record,
            )

    def test_source_snapshot_first_insert_replay_and_conflict(self) -> None:
        record = SourceSnapshotRecord(
            tenant_id=TENANT_A,
            snapshot_id="snapshot-1",
            source_id="source-1",
            hat_scope_id="scope-1",
            content_sha256=DIGEST_A,
            byte_length=12,
            storage_class="CRDB_TRANSACTIONAL",
            immutable_object_reference="crdb:synthetic:snapshot-1",
            captured_at=NOW,
            source_observed_at=None,
            provenance={"synthetic": True},
        )
        row = {
            "tenant_id": TENANT_A,
            "snapshot_id": "snapshot-1",
            "source_id": "source-1",
            "hat_scope_id": "scope-1",
            "content_sha256": DIGEST_A,
            "byte_length": 12,
            "storage_class": "CRDB_TRANSACTIONAL",
            "immutable_object_reference": "crdb:synthetic:snapshot-1",
            "captured_at": NOW,
            "source_observed_at": None,
            "provenance": {"synthetic": True},
        }
        self.repository.put_source_snapshot(
            ScriptedTransaction([None, row]),
            record,
        )
        self.repository.put_source_snapshot(ScriptedTransaction([row]), record)
        with self.assertRaises(ImmutableRecordConflictError):
            self.repository.put_source_snapshot(
                ScriptedTransaction([dict(row, content_sha256=DIGEST_B)]),
                record,
            )

    def test_evidence_first_insert_replay_and_lineage_conflict(self) -> None:
        record = EvidenceItemRecord(
            tenant_id=TENANT_A,
            evidence_id="evidence-1",
            source_id="source-1",
            knowledge_version_id="version-1",
            hat_scope_id="scope-1",
            citation_reference="synthetic:1",
            content_sha256=DIGEST_A,
            trust_class="CANONICAL_SOURCE_EVIDENCE",
            authority_rank=10,
            scope_dimensions=({"name": "version", "value": "1"},),
            metadata={"synthetic": True},
            retrieved_at=NOW,
        )
        row = {
            "tenant_id": TENANT_A,
            "evidence_id": "evidence-1",
            "source_id": "source-1",
            "knowledge_version_id": "version-1",
            "hat_scope_id": "scope-1",
            "citation_reference": "synthetic:1",
            "content_sha256": DIGEST_A,
            "trust_class": "CANONICAL_SOURCE_EVIDENCE",
            "authority_rank": 10,
            "scope_dimensions": [{"name": "version", "value": "1"}],
            "metadata": {"synthetic": True},
            "retrieved_at": NOW,
            "valid_from": None,
            "valid_until": None,
        }
        self.repository.put_evidence_item(
            ScriptedTransaction([None, row]),
            record,
        )
        self.repository.put_evidence_item(ScriptedTransaction([row]), record)
        with self.assertRaises(ImmutableRecordConflictError):
            self.repository.put_evidence_item(
                ScriptedTransaction(
                    [dict(row, knowledge_version_id="version-other")]
                ),
                record,
            )

    def test_audit_event_append_and_idempotent_replay(self) -> None:
        record = AuditEventRecord(
            tenant_id=TENANT_A,
            event_id="event-1",
            event_type="PERSISTENCE_TEST",
            actor_type="SYSTEM",
            actor_id="step6-test",
            payload_hash=DIGEST_A,
            event_hash=DIGEST_B,
            metadata={"synthetic": True},
            occurred_at=NOW,
        )
        row = {
            "tenant_id": TENANT_A,
            "event_id": "event-1",
            "schema_version": "1.0.0",
            "event_type": "PERSISTENCE_TEST",
            "actor_type": "SYSTEM",
            "actor_id": "step6-test",
            "kernel_run_id": None,
            "user_id": None,
            "personal_memory_space_id": None,
            "payload_hash": DIGEST_A,
            "previous_event_hash": None,
            "event_hash": DIGEST_B,
            "metadata": {"synthetic": True},
            "occurred_at": NOW,
        }
        self.repository.append_audit_event(
            ScriptedTransaction([None, row]),
            record,
        )
        replay = ScriptedTransaction([row])
        self.repository.append_audit_event(replay, record)
        self.assertEqual(len(replay.calls), 1)


class PersistenceStaticValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.migration_root = (
            REPOSITORY_ROOT / "sql" / "cockroachdb" / "migrations"
        )
        self.step6_sql = (
            self.migration_root
            / "0005_step6_persistence_idempotency_retry_foundation.sql"
        ).read_text(encoding="utf-8")

    def test_migrations_0001_through_0004_remain_frozen(self) -> None:
        expected = {
            "0001_step4_identity_and_hat_scopes.sql": (
                "a7d6e835d16debc77830cbcb2803c3b01400622c3186a64f7d627f4bf0a767a0"
            ),
            "0002_step4_knowledge_lineage_and_retrieval.sql": (
                "a8ddf1d342e58c8e12ecb082443b6367921fa5ec0e6edce92401f560300058d4"
            ),
            "0003_step4_kernel_memory_and_audit_evidence.sql": (
                "f25865e958d9bb352b8fc03512474fb848a688cfa6c73a5790664745779de881"
            ),
            "0004_step5_tenant_roles_session_context_rls.sql": (
                "6a8968dab3aa063b2d6f34bb31ecd26039e50f2f9351d961140c3a739106fbcd"
            ),
        }
        for filename, digest in expected.items():
            with self.subTest(filename=filename):
                self.assertEqual(
                    hashlib.sha256(
                        (self.migration_root / filename).read_bytes()
                    ).hexdigest(),
                    digest,
                )

    def test_new_table_has_rls_force_and_command_policies(self) -> None:
        for fragment in (
            "ENABLE ROW LEVEL SECURITY",
            "FORCE ROW LEVEL SECURITY",
            "persistence_operations_s6_select",
            "persistence_operations_s6_insert",
            "persistence_operations_s6_update",
            "WITH CHECK",
            "TO mp_app_runtime",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, self.step6_sql)
        self.assertNotIn("TO PUBLIC", self.step6_sql)
        self.assertNotIn("USING (true)", self.step6_sql)

    def test_shared_private_and_external_unique_boundaries_are_composite(self) -> None:
        for index in (
            "persistence_operations_shared_idempotency_uq",
            "persistence_operations_private_idempotency_uq",
            "persistence_operations_external_identity_uq",
        ):
            self.assertIn(index, self.step6_sql)
        self.assertNotIn("UNIQUE (external_ref)", self.step6_sql)
        self.assertIn("origin_system", self.step6_sql)
        self.assertIn("adapter_version", self.step6_sql)
        self.assertIn("artifact_kind", self.step6_sql)

    def test_no_raw_error_or_authority_columns(self) -> None:
        lowered = self.step6_sql.lower()
        for forbidden in (
            "raw_error",
            "exception_message",
            "approval_authority",
            "commit_authority",
            "execution_authority",
        ):
            self.assertNotIn(forbidden, lowered)

    def test_manifest_is_canonical_and_declares_exact_chain(self) -> None:
        path = self.migration_root / "manifest.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        canonical = (
            json.dumps(
                value,
                ensure_ascii=False,
                allow_nan=False,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode()
        self.assertEqual(path.read_bytes(), canonical)
        self.assertEqual(len(value["migrations"]), 16)
        self.assertEqual(
            value["migrations"][-1]["migration_id"],
            "0016_step33_audit_ledger_hash_chain",
        )

    def test_transaction_module_has_no_external_business_imports(self) -> None:
        source = (
            SOURCE_ROOT / "aioa_memory_kernel" / "persistence" / "transaction.py"
        ).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported = {
            alias.name.lower()
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        } | {
            (node.module or "").lower()
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        }
        for forbidden in ("boto", "requests", "httpx", "subprocess"):
            self.assertFalse(
                any(forbidden in module for module in imported),
                forbidden,
            )
        self.assertIn("max_attempts", source)
        self.assertIn("40001", (
            SOURCE_ROOT / "aioa_memory_kernel" / "persistence" / "retry.py"
        ).read_text(encoding="utf-8"))

    def test_no_nvidia_branded_database_object_or_role(self) -> None:
        lowered = self.step6_sql.lower()
        self.assertNotIn("nvidia", lowered)
        self.assertNotIn("nooa", lowered)
        self.assertNotIn("openshell", lowered)
        self.assertNotIn("create role", lowered)

    def test_step6_documentation_and_roadmap_are_complete(self) -> None:
        required_documents = (
            "docs/architecture/"
            "COCKROACHDB_PERSISTENCE_IDEMPOTENCY_RETRY_FOUNDATION_1A.md",
            "docs/adr/"
            "ADR-013-cockroachdb-persistence-idempotency-retry-boundary.md",
            "docs/audits/"
            "STEP_6_PERSISTENCE_IDEMPOTENCY_RETRY_CLOSURE_1A.md",
            "docs/evidence/cockroachdb-v26-2/"
            "step6-persistence-validation.json",
        )
        for relative in required_documents:
            with self.subTest(relative=relative):
                self.assertTrue((REPOSITORY_ROOT / relative).is_file())
        architecture = (
            REPOSITORY_ROOT / required_documents[0]
        ).read_text(encoding="utf-8")
        normalized_architecture = " ".join(architecture.split())
        for required in (
            "CockroachDB remains the system of record",
            "SQLSTATE `40001` only",
            "A missing receipt or result is never recorded as completed success",
            "No NVIDIA-branded table",
            "Step 7 was not started",
        ):
            with self.subTest(required=required):
                self.assertIn(required, normalized_architecture)
        roadmap = (
            REPOSITORY_ROOT / "docs" / "roadmap" / "PRODUCTION_ROADMAP.md"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "[x] **Step 6 — Persistence Adapters, Idempotency and "
            "Transaction Retry Foundation 1A**",
            roadmap,
        )
        self.assertIn(
            "Step 7: COMPLETE AND PUSHED at actual closure commit",
            roadmap,
        )
        self.assertIn(
            "Step 9: COMPLETE AND PUSHED at actual closure commit",
            roadmap,
        )
        self.assertIn(
            "Step 10: COMPLETE AND PUSHED at actual closure commit",
            roadmap,
        )
        self.assertIn("Step 11: COMPLETE AND PUSHED at actual closure commit", roadmap)
        self.assertIn("Step 13: COMPLETE AND PUSHED at actual closure commit", roadmap)
        self.assertIn("Step 14: COMPLETE AND PUSHED at actual closure commit", roadmap)
        self.assertIn("Step 15: COMPLETE AND PUSHED at actual closure commit", roadmap)
        self.assertIn("Step 16: COMPLETE AND PUSHED at actual closure commit", roadmap)

    def test_step6_evidence_is_canonical_sanitized_and_consistent(self) -> None:
        path = (
            REPOSITORY_ROOT
            / "docs"
            / "evidence"
            / "cockroachdb-v26-2"
            / "step6-persistence-validation.json"
        )
        value = json.loads(path.read_text(encoding="utf-8"))
        canonical = (
            json.dumps(
                value,
                ensure_ascii=False,
                allow_nan=False,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode()
        self.assertEqual(path.read_bytes(), canonical)
        self.assertEqual(value["status"], "PASS")
        self.assertEqual(value["live"]["probe_summary"]["pass_count"], 40)
        self.assertEqual(value["required_probe_coverage"]["pass_count"], 60)
        self.assertEqual(
            value["required_probe_coverage"]["step6_unit_test_count"],
            46,
        )
        serialized = path.read_text(encoding="utf-8")
        for forbidden in (
            "/" + "home/",
            "/" + "mnt/",
            "postgres" + "ql://",
            "cockroach" + "db://",
            "BEGIN " + "PRIVATE KEY",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, serialized)
        self.assertIsNone(
            __import__("re").search(
                r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
                r"[89ab][0-9a-f]{3}-[0-9a-f]{12}\b",
                serialized,
                flags=__import__("re").IGNORECASE,
            )
        )


if __name__ == "__main__":
    unittest.main()
