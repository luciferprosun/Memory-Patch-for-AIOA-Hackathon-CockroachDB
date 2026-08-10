#!/usr/bin/env python3
"""Controlled Step 33 append-only ledger, chain, and export validation."""

from __future__ import annotations

import argparse
import copy
import json
import shutil
import sys
import tempfile
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts"), str(ROOT)]

import run_cockroachdb_migrations as migrations  # noqa: E402
import run_step18_retrieval_validation as step18  # noqa: E402
import run_step27_personal_memory_validation as step27  # noqa: E402
import run_step30_user_approval_commit_activation_validation as step30  # noqa: E402

from aioa_memory_kernel.audit_ledger import (  # noqa: E402
    MAX_AUDIT_EXPORT_EVENTS,
    STEP33_AUDIT_SCHEMA_VERSION,
    STEP33_CANONICALIZATION_ID,
    STEP33_CHAIN_POLICY_ID,
    STEP33_EVENT_REGISTRY_VERSION,
    STEP33_GENESIS_SENTINEL,
    STEP33_HASH_ALGORITHM,
    STEP33_VERIFICATION_POLICY_DIGEST,
    AuditActorType,
    AuditChainHead,
    AuditEventDraft,
    AuditEventType,
    AuditExportRequest,
    AuditLedgerCockroachRepository,
    AuditLedgerError,
    AuditLedgerService,
    AuditReasonCode,
    AuditRedactionPolicy,
    AuditRedactionProfile,
    AuditSubjectType,
    audit_to_jsonb,
    build_audit_event_envelope,
    build_audit_ledger_entry,
    compute_audit_chain_id,
    parse_audit_ledger_entry,
    verify_audit_chain,
)
from aioa_memory_kernel.contracts.serialization import (  # noqa: E402
    canonical_json,
    canonical_sha256,
)
from aioa_memory_kernel.persistence import (  # noqa: E402
    AccessMode,
    RequestContext,
    extract_sqlstate,
)


START_SHA = "355a790b50a6412adcf64dd0a463219574a3f849"
EXPECTED_COCKROACH_SHA256 = step27.EXPECTED_COCKROACH_SHA256
DEFAULT_EXTERNAL_ENV = step27.DEFAULT_EXTERNAL_ENV

TENANT_A = "tenant-step20"
OWNER_A = "user-step20"
CONCURRENCY_OWNER = "user-step33-concurrency"
OTHER_OWNER = "user-step29-isolation"
TENANT_B = "tenant-step29-isolation"
TENANT_B_OWNER = "user-step29-other-tenant"
BASE_TIME = datetime(2044, 3, 4, 5, 6, 7, tzinfo=UTC)
STEP33_RUNTIME_STOP_TIMEOUT_SECONDS = 120


class ValidationFailure(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__("Step 33 controlled validation failed")
        self.code = code


class _ProgressMigrationClient:
    def __init__(self, delegate: step30._Step30HttpSqlClient) -> None:
        self._delegate = delegate
        self._migration_ids = tuple(
            item.migration_id for item in migrations.load_migrations()
        )
        self._next = 0
        self._announced: set[str] = set()

    def execute(
        self, database: str, sql: str, *, timeout: float = 300
    ) -> str:
        current = (
            self._migration_ids[self._next]
            if self._next < len(self._migration_ids)
            else None
        )
        bookkeeping = (
            "FROM memory_patch.schema_migrations" in sql
            and "INSERT INTO memory_patch.schema_migrations" not in sql
        )
        if current and not bookkeeping and current not in self._announced:
            _progress("MIGRATION_" + current.upper())
            self._announced.add(current)
        try:
            result = self._delegate.execute(database, sql, timeout=timeout)
        except BaseException as error:
            _failure_progress("MIGRATION_" + (current or "UNKNOWN").upper(), error)
            raise
        if (
            current
            and "INSERT INTO memory_patch.schema_migrations" in sql
            and current in sql
        ):
            self._next += 1
        return result


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cockroach-binary", type=Path)
    parser.add_argument("--external-env", type=Path, default=DEFAULT_EXTERNAL_ENV)
    return parser.parse_args()


def _progress(stage: str) -> None:
    print(
        canonical_json({"stage": stage, "status": "RUNNING", "step": 33}),
        file=sys.stderr,
        flush=True,
    )


def _failure_progress(stage: str, error: BaseException) -> None:
    current: BaseException | None = error
    chain: list[str] = []
    seen: set[int] = set()
    sqlstate = extract_sqlstate(error)
    while current is not None and id(current) not in seen and len(chain) < 5:
        seen.add(id(current))
        detail = " ".join(str(current).split())[:384]
        chain.append(type(current).__name__ + (":" + detail if detail else ""))
        current = current.__cause__
    print(
        canonical_json(
            {
                "detail": chain,
                "sanitized_code": getattr(error, "sanitized_code", None),
                "sqlstate": sqlstate,
                "stage": stage,
                "status": "FAILED",
                "step": 33,
            }
        ),
        file=sys.stderr,
        flush=True,
    )


def _stop_owned_runtime(runtime: migrations.LocalRuntime) -> Mapping[str, Any]:
    """Stop the exact disposable PID with a bounded busy-node grace period."""

    if runtime.process is None:
        raise migrations.MigrationError("owned CockroachDB PID is unavailable")
    errors: list[str] = []
    sigterm_sent = False
    if runtime.process.poll() is None:
        runtime.process.terminate()
        sigterm_sent = True
        try:
            runtime.process.wait(timeout=STEP33_RUNTIME_STOP_TIMEOUT_SECONDS)
        except Exception as exc:
            errors.append("OWNED_COCKROACH_PID_REMAINS")
            raise migrations.MigrationError(
                "owned CockroachDB process did not stop within the bounded grace period"
            ) from exc
    return runtime._finalize_cleanup(
        errors,
        details={
            "drain_command_completed": False,
            "drain_completion_marker": False,
            "drain_shutdown_requested": False,
            "graceful_shutdown_requested": sigterm_sent,
            "owned_child_processes_reaped": True,
            "process_exit_code": runtime.process.returncode,
            "shutdown_method": "EXACT_OWNED_PID_SIGTERM",
            "sigterm_sent_to_exact_pid": sigterm_sent,
        },
        preserve_runtime_on_failure=False,
    )


def _read_evidence(name: str) -> Mapping[str, Any]:
    path = ROOT / "docs/evidence/personal-memory" / name
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping) or value.get("status") != "PASS":
        raise ValidationFailure("STEP33_UPSTREAM_EVIDENCE_INVALID")
    return value


def _seed_identity_sql() -> str:
    quote = migrations.sql_literal
    at = quote(BASE_TIME.isoformat()) + "::TIMESTAMPTZ"
    return ";\n".join(
        (
            "INSERT INTO memory_patch.tenants "
            "(tenant_id, display_name, metadata, created_at, updated_at) VALUES "
            f"({quote(TENANT_A)}, 'Step 33 tenant A', '{{}}'::JSONB, {at}, {at}), "
            f"({quote(TENANT_B)}, 'Step 33 tenant B', '{{}}'::JSONB, {at}, {at})",
            "INSERT INTO memory_patch.users "
            "(tenant_id, user_id, display_name, metadata, created_at, updated_at) VALUES "
            f"({quote(TENANT_A)}, {quote(OWNER_A)}, 'Step 33 owner', "
            f"'{{}}'::JSONB, {at}, {at}), "
            f"({quote(TENANT_A)}, {quote(CONCURRENCY_OWNER)}, "
            f"'Step 33 concurrency owner', '{{}}'::JSONB, {at}, {at}), "
            f"({quote(TENANT_A)}, {quote(OTHER_OWNER)}, 'Step 33 isolated user', "
            f"'{{}}'::JSONB, {at}, {at}), "
            f"({quote(TENANT_B)}, {quote(TENANT_B_OWNER)}, "
            f"'Step 33 isolated tenant user', '{{}}'::JSONB, {at}, {at})",
        )
    )


def _draft(
    *,
    index: int,
    event_type: AuditEventType,
    subject_type: AuditSubjectType,
    subject_id: str,
    subject_hash: str,
    actor_type: AuditActorType,
    actor_id: str,
    state: str,
    lineage_hashes: Mapping[str, str],
    owner_user_id: str = OWNER_A,
    recorded_at=None,
) -> AuditEventDraft:
    observed = recorded_at or (BASE_TIME + timedelta(seconds=index))
    return AuditEventDraft(
        event_type=event_type,
        tenant_id=TENANT_A,
        owner_user_id=owner_user_id,
        subject_type=subject_type,
        subject_id=subject_id,
        subject_hash=subject_hash,
        actor_type=actor_type,
        actor_id=actor_id,
        idempotency_key=f"step33-audit-event-{index}",
        occurred_at=observed,
        recorded_at=observed,
        event_payload={
            "business_authority": False,
            "canonical_evidence": False,
            "state": state,
        },
        reason_codes=(AuditReasonCode.AUDIT_EVENT_APPENDED,),
        policy_id="step33-audit-ingestion-policy",
        policy_version="1",
        policy_digest=canonical_sha256("step33-audit-ingestion-policy-1"),
        lineage_hashes=lineage_hashes,
    )


def _representative_drafts() -> tuple[AuditEventDraft, ...]:
    step30_evidence = _read_evidence(
        "step30-user-approval-commit-activation-validation.json"
    )
    step32_evidence = _read_evidence(
        "step32-personal-memory-lifecycle-validation.json"
    )
    proposal_hash = str(step30_evidence["proposal_hash"])
    approval_hash = str(step30_evidence["approval"]["receipt_hash"])
    commit_hash = str(step30_evidence["commit"]["receipt_hash"])
    activation_hash = str(step30_evidence["activation"]["receipt_hash"])
    supersession_hash = str(step32_evidence["supersession"]["receipt_hash"])
    revocation_hash = str(step32_evidence["revocation"]["receipt_hash"])
    export_hash = str(step32_evidence["export"]["bundle_hash"])
    deletion_hash = str(step32_evidence["deletion"]["result_hash"])
    promotion_hash = str(step32_evidence["shared_promotion"]["proposal_hash"])
    specifications = (
        (
            AuditEventType.PERSONAL_MEMORY_APPROVED,
            AuditSubjectType.PERSONAL_MEMORY_PROPOSAL,
            str(step30_evidence["proposal_id"]),
            approval_hash,
            AuditActorType.HUMAN_USER,
            OWNER_A,
            "APPROVED",
            {"approval_receipt_hash": approval_hash, "proposal_hash": proposal_hash},
        ),
        (
            AuditEventType.PERSONAL_MEMORY_COMMITTED,
            AuditSubjectType.PERSONAL_MEMORY_PATCH,
            "step33-controlled-committed-patch",
            commit_hash,
            AuditActorType.COMMIT_HELPER,
            "step30-commit-helper",
            "COMMITTED",
            {"commit_receipt_hash": commit_hash, "approval_receipt_hash": approval_hash},
        ),
        (
            AuditEventType.PERSONAL_MEMORY_ACTIVATED,
            AuditSubjectType.PERSONAL_MEMORY_PATCH,
            "step33-controlled-active-patch",
            activation_hash,
            AuditActorType.ACTIVATION_SERVICE,
            "step30-activation-service",
            "ACTIVE",
            {"activation_receipt_hash": activation_hash, "commit_receipt_hash": commit_hash},
        ),
        (
            AuditEventType.PERSONAL_MEMORY_SUPERSEDED,
            AuditSubjectType.PERSONAL_MEMORY_PATCH,
            "step33-controlled-supersession",
            supersession_hash,
            AuditActorType.HUMAN_USER,
            OWNER_A,
            "SUPERSEDED",
            {"supersession_receipt_hash": supersession_hash},
        ),
        (
            AuditEventType.PERSONAL_MEMORY_REVOKED,
            AuditSubjectType.PERSONAL_MEMORY_PATCH,
            "step33-controlled-revocation",
            revocation_hash,
            AuditActorType.HUMAN_USER,
            OWNER_A,
            "REVOKED",
            {"revocation_receipt_hash": revocation_hash},
        ),
        (
            AuditEventType.PERSONAL_MEMORY_EXPORTED,
            AuditSubjectType.PERSONAL_MEMORY_EXPORT,
            "step33-controlled-lifecycle-export",
            export_hash,
            AuditActorType.HUMAN_USER,
            OWNER_A,
            "EXPORTED",
            {"lifecycle_export_bundle_hash": export_hash},
        ),
        (
            AuditEventType.PERSONAL_MEMORY_DELETED,
            AuditSubjectType.PERSONAL_MEMORY_DELETION,
            "step33-controlled-logical-deletion",
            deletion_hash,
            AuditActorType.HUMAN_USER,
            OWNER_A,
            "DELETED",
            {"deletion_result_hash": deletion_hash},
        ),
        (
            AuditEventType.SHARED_PROMOTION_PROPOSED,
            AuditSubjectType.SHARED_PROMOTION_PROPOSAL,
            "step33-controlled-shared-promotion",
            promotion_hash,
            AuditActorType.HUMAN_USER,
            OWNER_A,
            "AWAITING_SHARED_REVIEW",
            {"shared_promotion_proposal_hash": promotion_hash},
        ),
    )
    return tuple(
        _draft(
            index=index,
            event_type=specification[0],
            subject_type=specification[1],
            subject_id=specification[2],
            subject_hash=specification[3],
            actor_type=specification[4],
            actor_id=specification[5],
            state=specification[6],
            lineage_hashes=specification[7],
        )
        for index, specification in enumerate(specifications, start=1)
    )


def _context(tenant_id: str, owner_user_id: str) -> RequestContext:
    return RequestContext(
        tenant_id=tenant_id,
        user_id=owner_user_id,
        access_mode=AccessMode.USER_PRIVATE,
    )


def _denial_sqlstate(error: BaseException) -> str | None:
    code = extract_sqlstate(error)
    if code is not None:
        return code
    current = error.__cause__
    while current is not None:
        candidate = getattr(current, "sqlstate", None)
        if isinstance(candidate, str):
            return candidate
        current = current.__cause__
    return None


def _require_database_denial(callback, code: str) -> str:
    try:
        callback()
    except BaseException as error:
        sqlstate = _denial_sqlstate(error)
        if sqlstate not in {"42501", "44000"}:
            raise ValidationFailure(code + "_WRONG_SQLSTATE") from error
        return "DENIED"
    raise ValidationFailure(code + "_NOT_DENIED")


def _clone_entries(entries):
    return tuple(
        parse_audit_ledger_entry(audit_to_jsonb(entry)) for entry in entries
    )


def _tamper_matrix(entries, head: AuditChainHead) -> Mapping[str, str]:
    chain_id = head.chain_id
    scenarios: dict[str, tuple] = {}

    payload = list(_clone_entries(entries))
    object.__setattr__(payload[1], "event_payload", {"state": "FORGED"})
    scenarios["payload"] = tuple(payload)

    event_type = list(_clone_entries(entries))
    object.__setattr__(
        event_type[1].envelope, "event_type", AuditEventType.INTEGRITY_FAILURE
    )
    scenarios["event_type"] = tuple(event_type)

    subject = list(_clone_entries(entries))
    object.__setattr__(subject[1].envelope, "subject_hash", "0" * 64)
    scenarios["subject_hash"] = tuple(subject)

    previous = list(_clone_entries(entries))
    object.__setattr__(previous[2].envelope, "previous_event_hash", "f" * 64)
    scenarios["previous_hash"] = tuple(previous)

    scenarios["deleted_event"] = (entries[0], *entries[2:])
    reordered = list(_clone_entries(entries))
    reordered[1], reordered[2] = reordered[2], reordered[1]
    scenarios["reordered_event"] = tuple(reordered)

    event_hash = list(_clone_entries(entries))
    object.__setattr__(event_hash[2].envelope, "event_hash", "a" * 64)
    scenarios["event_hash"] = tuple(event_hash)

    forged_draft = _draft(
        index=700,
        event_type=AuditEventType.INTEGRITY_FAILURE,
        subject_type=AuditSubjectType.SECURITY_EVENT,
        subject_id="step33-forged-insert",
        subject_hash=canonical_sha256("step33-forged-insert"),
        actor_type=AuditActorType.SYSTEM_POLICY,
        actor_id="step33-system-policy",
        state="FORGED",
        lineage_hashes={"input_hash": canonical_sha256("forged")},
        recorded_at=BASE_TIME + timedelta(minutes=3),
    )
    forged_event = build_audit_event_envelope(
        forged_draft,
        sequence_number=2,
        previous_event_hash=entries[0].envelope.event_hash,
    )
    forged_entry = build_audit_ledger_entry(
        forged_event, forged_draft.event_payload
    )
    scenarios["forged_event"] = (
        entries[0],
        forged_entry,
        *entries[1:],
    )

    duplicate = list(_clone_entries(entries))
    object.__setattr__(
        duplicate[2].envelope,
        "sequence_number",
        duplicate[1].envelope.sequence_number,
    )
    scenarios["duplicate_sequence"] = tuple(duplicate)

    result: dict[str, str] = {}
    for name, scenario in scenarios.items():
        verification = verify_audit_chain(chain_id, scenario)
        if verification.verified:
            raise ValidationFailure("STEP33_TAMPER_" + name.upper() + "_MISSED")
        result[name] = "DETECTED"

    tampered_head = copy.deepcopy(head)
    object.__setattr__(tampered_head, "last_event_hash", "0" * 64)
    if verify_audit_chain(chain_id, entries, expected_head=tampered_head).verified:
        raise ValidationFailure("STEP33_TAMPER_CHAIN_HEAD_MISSED")
    result["chain_head"] = "DETECTED"
    return result


def _validate_service(
    *,
    root,
    database: str,
    app_role: str,
) -> Mapping[str, Any]:
    runner = step30._runner(
        port=root.sql_port,
        database=database,
        role=app_role,
        diagnostic=True,
    )
    repository = AuditLedgerCockroachRepository()
    redaction_policy = AuditRedactionPolicy()
    service = AuditLedgerService(
        runner, repository=repository, redaction_policy=redaction_policy
    )
    owner_context = _context(TENANT_A, OWNER_A)
    concurrency_context = _context(TENANT_A, CONCURRENCY_OWNER)

    _progress("APPEND_REPRESENTATIVE_EVENTS")
    representative = _representative_drafts()
    stored = []
    for item in representative:
        entry, replay = service.append_event(
            item,
            authenticated_tenant_id=TENANT_A,
            authenticated_actor_type=item.actor_type,
            authenticated_actor_id=item.actor_id,
        )
        if replay:
            raise ValidationFailure("STEP33_FIRST_APPEND_REPLAYED")
        stored.append(entry)

    replay_entry, exact_replay = service.append_event(
        representative[0],
        authenticated_tenant_id=TENANT_A,
        authenticated_actor_type=representative[0].actor_type,
        authenticated_actor_id=representative[0].actor_id,
    )
    if not exact_replay or replay_entry != stored[0]:
        raise ValidationFailure("STEP33_EXACT_REPLAY_MISMATCH")
    conflicting = replace(
        representative[0], subject_hash=canonical_sha256("conflicting-replay")
    )
    try:
        service.append_event(
            conflicting,
            authenticated_tenant_id=TENANT_A,
            authenticated_actor_type=conflicting.actor_type,
            authenticated_actor_id=conflicting.actor_id,
        )
    except AuditLedgerError as error:
        if error.reason_code is not AuditReasonCode.AUDIT_EVENT_REPLAY_CONFLICT:
            raise ValidationFailure("STEP33_CONFLICTING_REPLAY_WRONG_REASON") from error
    else:
        raise ValidationFailure("STEP33_CONFLICTING_REPLAY_ACCEPTED")

    _progress("CONCURRENT_APPEND")
    common_time = BASE_TIME + timedelta(minutes=1)

    exact_concurrent = _draft(
        index=80,
        event_type=AuditEventType.INTEGRITY_FAILURE,
        subject_type=AuditSubjectType.SECURITY_EVENT,
        subject_id="step33-concurrent-exact-replay",
        subject_hash=canonical_sha256("step33-concurrent-exact-replay"),
        actor_type=AuditActorType.SYSTEM_POLICY,
        actor_id="step33-system-policy",
        state="INTEGRITY_FAILURE",
        lineage_hashes={"input_hash": canonical_sha256("concurrent-exact")},
        owner_user_id=CONCURRENCY_OWNER,
        recorded_at=common_time,
    )
    exact_barrier = threading.Barrier(2)

    def append_exact_concurrent():
        exact_barrier.wait(timeout=30)
        return service.append_event(
            exact_concurrent,
            authenticated_tenant_id=TENANT_A,
            authenticated_actor_type=exact_concurrent.actor_type,
            authenticated_actor_id=exact_concurrent.actor_id,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        exact_results = tuple(
            executor.map(lambda _index: append_exact_concurrent(), range(2))
        )
    if (
        exact_results[0][0] != exact_results[1][0]
        or sorted(item[1] for item in exact_results) != [False, True]
    ):
        raise ValidationFailure("STEP33_CONCURRENT_EXACT_REPLAY_DIVERGED")

    conflict_base = _draft(
        index=90,
        event_type=AuditEventType.IDEMPOTENCY_CONFLICT,
        subject_type=AuditSubjectType.SECURITY_EVENT,
        subject_id="step33-concurrent-conflict",
        subject_hash=canonical_sha256("step33-concurrent-conflict-a"),
        actor_type=AuditActorType.SYSTEM_POLICY,
        actor_id="step33-system-policy",
        state="IDEMPOTENCY_CONFLICT",
        lineage_hashes={"input_hash": canonical_sha256("conflict-a")},
        owner_user_id=CONCURRENCY_OWNER,
        recorded_at=common_time,
    )
    conflict_changed = replace(
        conflict_base,
        subject_hash=canonical_sha256("step33-concurrent-conflict-b"),
        lineage_hashes={"input_hash": canonical_sha256("conflict-b")},
    )
    conflict_barrier = threading.Barrier(2)

    def append_conflicting_concurrent(item):
        conflict_barrier.wait(timeout=30)
        try:
            entry, _ = service.append_event(
                item,
                authenticated_tenant_id=TENANT_A,
                authenticated_actor_type=item.actor_type,
                authenticated_actor_id=item.actor_id,
            )
            return "APPENDED", entry.envelope.event_id
        except AuditLedgerError as error:
            if error.reason_code is not AuditReasonCode.AUDIT_EVENT_REPLAY_CONFLICT:
                raise
            return "DENIED", error.reason_code.value

    with ThreadPoolExecutor(max_workers=2) as executor:
        conflict_results = tuple(
            executor.map(
                append_conflicting_concurrent,
                (conflict_base, conflict_changed),
            )
        )
    if sorted(item[0] for item in conflict_results) != ["APPENDED", "DENIED"]:
        raise ValidationFailure("STEP33_CONCURRENT_CONFLICT_NOT_SERIALIZED")

    def append_concurrent(index: int):
        item = _draft(
            index=100 + index,
            event_type=AuditEventType.POLICY_BLOCKED,
            subject_type=AuditSubjectType.SECURITY_EVENT,
            subject_id=f"step33-concurrent-policy-{index}",
            subject_hash=canonical_sha256({"concurrent": index}),
            actor_type=AuditActorType.SYSTEM_POLICY,
            actor_id="step33-system-policy",
            state="POLICY_BLOCKED",
            lineage_hashes={"input_hash": canonical_sha256({"input": index})},
            owner_user_id=CONCURRENCY_OWNER,
            recorded_at=common_time,
        )
        return service.append_event(
            item,
            authenticated_tenant_id=TENANT_A,
            authenticated_actor_type=item.actor_type,
            authenticated_actor_id=item.actor_id,
        )[0]

    with ThreadPoolExecutor(max_workers=6) as executor:
        concurrent_entries = tuple(executor.map(append_concurrent, range(1, 7)))
    if len({item.envelope.sequence_number for item in concurrent_entries}) != 6:
        raise ValidationFailure("STEP33_CONCURRENT_SEQUENCE_DUPLICATE")

    concurrency_head = runner.run(
        concurrency_context,
        lambda transaction: repository.get_chain_head(
            transaction,
            tenant_id=TENANT_A,
            owner_user_id=CONCURRENCY_OWNER,
            chain_id=compute_audit_chain_id(TENANT_A, CONCURRENCY_OWNER),
        ),
        operation_kind="STEP33_VALIDATION_LOAD_CONCURRENCY_HEAD",
    )
    if concurrency_head is None:
        raise ValidationFailure("STEP33_CONCURRENCY_CHAIN_HEAD_MISSING")
    concurrency_entries = runner.run(
        concurrency_context,
        lambda transaction: repository.load_range(
            transaction,
            tenant_id=TENANT_A,
            owner_user_id=CONCURRENCY_OWNER,
            chain_id=concurrency_head.chain_id,
            start_sequence=1,
            end_sequence=concurrency_head.last_sequence,
            maximum_events=MAX_AUDIT_EXPORT_EVENTS,
        ),
        operation_kind="STEP33_VALIDATION_LOAD_CONCURRENCY_CHAIN",
    )
    if (
        len(concurrency_entries) != 8
        or [item.envelope.sequence_number for item in concurrency_entries]
        != list(range(1, 9))
        or not verify_audit_chain(
            concurrency_head.chain_id,
            concurrency_entries,
            expected_head=concurrency_head,
        ).verified
    ):
        raise ValidationFailure("STEP33_CONCURRENT_CHAIN_INVALID")

    head = runner.run(
        owner_context,
        lambda transaction: repository.get_chain_head(
            transaction,
            tenant_id=TENANT_A,
            owner_user_id=OWNER_A,
            chain_id=compute_audit_chain_id(TENANT_A, OWNER_A),
        ),
        operation_kind="STEP33_VALIDATION_LOAD_HEAD",
    )
    if head is None:
        raise ValidationFailure("STEP33_CHAIN_HEAD_MISSING")
    entries = runner.run(
        owner_context,
        lambda transaction: repository.load_range(
            transaction,
            tenant_id=TENANT_A,
            owner_user_id=OWNER_A,
            chain_id=head.chain_id,
            start_sequence=1,
            end_sequence=head.last_sequence,
            maximum_events=MAX_AUDIT_EXPORT_EVENTS,
        ),
        operation_kind="STEP33_VALIDATION_LOAD_CHAIN",
    )
    expected_count = len(representative)
    if len(entries) != expected_count:
        raise ValidationFailure("STEP33_CHAIN_EVENT_COUNT_MISMATCH")
    sequences = [item.envelope.sequence_number for item in entries]
    if sequences != list(range(1, expected_count + 1)):
        raise ValidationFailure("STEP33_CHAIN_SEQUENCE_NOT_CONTIGUOUS")
    verification = service.verify_chain(
        tenant_id=TENANT_A,
        owner_user_id=OWNER_A,
        authenticated_tenant_id=TENANT_A,
        authenticated_owner_user_id=OWNER_A,
    )
    if not verification.verified or verification.last_hash != head.last_event_hash:
        raise ValidationFailure("STEP33_CHAIN_VERIFICATION_FAILED")

    _progress("RLS_AND_APPEND_ONLY_NEGATIVES")
    owner_count = runner.run(
        owner_context,
        lambda transaction: int(
            transaction.fetch_one(
                "SELECT count(*) AS row_count FROM memory_patch.audit_events "
                "WHERE tenant_id = %s AND chain_id = %s",
                (TENANT_A, head.chain_id),
            )["row_count"]
        ),
        operation_kind="STEP33_VALIDATION_OWNER_COUNT",
    )

    def visible_count(tenant_id: str, owner_user_id: str) -> tuple[int, int]:
        return runner.run(
            _context(tenant_id, owner_user_id),
            lambda transaction: (
                int(
                    transaction.fetch_one(
                        "SELECT count(*) AS row_count "
                        "FROM memory_patch.audit_events WHERE chain_id = %s",
                        (head.chain_id,),
                    )["row_count"]
                ),
                int(
                    transaction.fetch_one(
                        "SELECT count(*) AS row_count "
                        "FROM memory_patch.audit_chain_heads WHERE chain_id = %s",
                        (head.chain_id,),
                    )["row_count"]
                ),
            ),
            operation_kind="STEP33_VALIDATION_RLS_READ",
        )

    cross_user_rows = visible_count(TENANT_A, OTHER_OWNER)
    cross_tenant_rows = visible_count(TENANT_B, TENANT_B_OWNER)
    if owner_count != expected_count or cross_user_rows != (0, 0) or cross_tenant_rows != (0, 0):
        raise ValidationFailure("STEP33_RLS_READ_ISOLATION_FAILED")

    detached_draft = _draft(
        index=500,
        event_type=AuditEventType.OWNER_SCOPE_DENIED,
        subject_type=AuditSubjectType.SECURITY_EVENT,
        subject_id="step33-rls-insert-negative",
        subject_hash=canonical_sha256("step33-rls-insert-negative"),
        actor_type=AuditActorType.SYSTEM_POLICY,
        actor_id="step33-system-policy",
        state="DENIED",
        lineage_hashes={"attempt_hash": canonical_sha256("foreign-write")},
        recorded_at=common_time + timedelta(seconds=1),
    )
    detached_event = build_audit_event_envelope(
        detached_draft,
        sequence_number=head.last_sequence + 1,
        previous_event_hash=head.last_event_hash,
    )
    detached_entry = build_audit_ledger_entry(
        detached_event, detached_draft.event_payload
    )
    cross_user_insert = _require_database_denial(
        lambda: runner.run(
            _context(TENANT_A, OTHER_OWNER),
            lambda transaction: repository.insert_entry(transaction, detached_entry),
            operation_kind="STEP33_VALIDATION_CROSS_USER_INSERT",
        ),
        "STEP33_CROSS_USER_INSERT",
    )
    cross_tenant_insert = _require_database_denial(
        lambda: runner.run(
            _context(TENANT_B, TENANT_B_OWNER),
            lambda transaction: repository.insert_entry(transaction, detached_entry),
            operation_kind="STEP33_VALIDATION_CROSS_TENANT_INSERT",
        ),
        "STEP33_CROSS_TENANT_INSERT",
    )

    update_denied = _require_database_denial(
        lambda: runner.run(
            owner_context,
            lambda transaction: transaction.execute(
                "UPDATE memory_patch.audit_events SET metadata = metadata "
                "WHERE tenant_id = %s AND event_id = %s",
                (TENANT_A, entries[0].envelope.event_id),
            ),
            operation_kind="STEP33_VALIDATION_APPEND_ONLY_UPDATE",
        ),
        "STEP33_AUDIT_UPDATE",
    )
    delete_denied = _require_database_denial(
        lambda: runner.run(
            owner_context,
            lambda transaction: transaction.execute(
                "DELETE FROM memory_patch.audit_events "
                "WHERE tenant_id = %s AND event_id = %s",
                (TENANT_A, entries[0].envelope.event_id),
            ),
            operation_kind="STEP33_VALIDATION_APPEND_ONLY_DELETE",
        ),
        "STEP33_AUDIT_DELETE",
    )

    _progress("EXPORT_AND_TAMPER_VALIDATION")
    export_request = AuditExportRequest(
        tenant_id=TENANT_A,
        requester_actor_type=AuditActorType.HUMAN_USER,
        requester_id=OWNER_A,
        owner_user_id=OWNER_A,
        chain_ids=(head.chain_id,),
        start_sequence=2,
        end_sequence=None,
        maximum_events=4,
        redaction_profile=AuditRedactionProfile.HASH_ONLY,
        requested_at=BASE_TIME + timedelta(minutes=2),
    )
    export_bundle = service.export_chain(
        export_request,
        authenticated_tenant_id=TENANT_A,
        authenticated_owner_user_id=OWNER_A,
    )
    if (
        not export_bundle.truncated
        or export_bundle.exported_event_count != 4
        or not export_bundle.verification_results[0].verified
        or export_bundle.range_proofs[0].predecessor_hash
        != entries[0].envelope.event_hash
    ):
        raise ValidationFailure("STEP33_EXPORT_RANGE_PROOF_FAILED")
    try:
        service.export_chain(
            export_request,
            authenticated_tenant_id=TENANT_A,
            authenticated_owner_user_id=OTHER_OWNER,
        )
    except AuditLedgerError:
        cross_user_export = "DENIED"
    else:
        raise ValidationFailure("STEP33_CROSS_USER_EXPORT_ACCEPTED")
    try:
        service.export_chain(
            export_request,
            authenticated_tenant_id=TENANT_B,
            authenticated_owner_user_id=OWNER_A,
        )
    except AuditLedgerError as error:
        if error.reason_code is not AuditReasonCode.AUDIT_TENANT_MISMATCH:
            raise ValidationFailure("STEP33_CROSS_TENANT_EXPORT_WRONG_REASON") from error
        cross_tenant_export = "DENIED"
    else:
        raise ValidationFailure("STEP33_CROSS_TENANT_EXPORT_ACCEPTED")

    rendered_export = canonical_json(audit_to_jsonb(export_bundle)).lower()
    forbidden = (
        "api_key",
        "authorization:",
        "bearer ",
        "aws_secret",
        "github_token",
        "/home/",
        "/media/",
    )
    leaked = tuple(token for token in forbidden if token in rendered_export)
    if leaked:
        raise ValidationFailure("STEP33_EXPORT_SECRET_LEAK")

    tamper = _tamper_matrix(entries, head)
    remaining_count = runner.run(
        owner_context,
        lambda transaction: int(
            transaction.fetch_one(
                "SELECT count(*) AS row_count FROM memory_patch.audit_events "
                "WHERE tenant_id = %s AND chain_id = %s",
                (TENANT_A, head.chain_id),
            )["row_count"]
        ),
        operation_kind="STEP33_VALIDATION_POST_NEGATIVE_COUNT",
    )
    if remaining_count != expected_count:
        raise ValidationFailure("STEP33_APPEND_ONLY_NEGATIVE_MUTATED_ROWS")

    return {
        "append": {
            "conflicting_replay": "DENIED",
            "concurrent_attempts": 10,
            "concurrent_committed_events": 8,
            "concurrent_conflicting_replay": "DENIED",
            "concurrent_exact_replay": "PASS",
            "concurrency_result": "UNIQUE_CONTIGUOUS_SEQUENCE",
            "concurrency_chain_event_count": len(concurrency_entries),
            "concurrency_chain_verified": True,
            "exact_replay": "PASS",
            "first_append_receipt_hash": entries[0].append_receipt.receipt_hash,
        },
        "chain": {
            "chain_id": head.chain_id,
            "event_count": expected_count,
            "first_hash": entries[0].envelope.event_hash,
            "first_sequence": 1,
            "genesis_sentinel": STEP33_GENESIS_SENTINEL,
            "last_hash": entries[-1].envelope.event_hash,
            "last_sequence": expected_count,
            "verification_result_hash": verification.result_hash,
            "verified": True,
        },
        "export": {
            "bundle_hash": export_bundle.bundle_hash,
            "continuation_stable": export_bundle.continuation_token is not None,
            "event_count": export_bundle.exported_event_count,
            "range_anchor": export_bundle.range_proofs[0].predecessor_hash,
            "range_proof_hash": export_bundle.range_proofs[0].proof_hash,
            "redacted": all(item.redacted for item in export_bundle.ordered_events),
            "redaction_policy_digest": redaction_policy.policy_digest,
            "request_hash": export_request.request_hash,
            "secret_leakage_count": 0,
            "truncated": export_bundle.truncated,
            "verification_status": "PASS",
        },
        "fixture": {
            "personal_memory_content_persisted": False,
            "real_step30_receipt_hashes": True,
            "real_step32_receipt_hashes": True,
            "synthetic_tamper_cases": True,
        },
        "isolation": {
            "cross_tenant_export": cross_tenant_export,
            "cross_tenant_insert": cross_tenant_insert,
            "cross_tenant_visible_rows": list(cross_tenant_rows),
            "cross_user_export": cross_user_export,
            "cross_user_insert": cross_user_insert,
            "cross_user_visible_rows": list(cross_user_rows),
            "owner_visible_rows": owner_count,
        },
        "storage": {
            "append_only_delete": delete_denied,
            "append_only_update": update_denied,
            "chain_head_consistent": True,
            "remaining_rows": remaining_count,
        },
        "tamper_matrix": tamper,
    }


def validate(args: argparse.Namespace) -> Mapping[str, Any]:
    source_binary = step27._source_binary(args)
    identity = migrations.verify_binary_identity(source_binary)
    if identity["binary_sha256"] != EXPECTED_COCKROACH_SHA256:
        raise ValidationFailure("STEP33_COCKROACH_BINARY_DIGEST_MISMATCH")

    runtime = None
    root = None
    database = None
    app_role = None
    cleanup: Mapping[str, Any] = {}
    cleanup_errors: list[str] = []
    primary_error: BaseException | None = None
    service_result = None
    migration_result = None
    replay_result = None
    catalog_result = None

    with tempfile.TemporaryDirectory(prefix="mp-step33-binary-", dir="/tmp") as temp:
        local_binary = Path(temp) / "cockroach"
        shutil.copy2(source_binary, local_binary)
        if migrations.verify_binary_identity(local_binary)["binary_sha256"] != EXPECTED_COCKROACH_SHA256:
            raise ValidationFailure("STEP33_COPIED_BINARY_DIGEST_MISMATCH")
        run_id = "mp_step33_" + uuid.uuid4().hex[:12]
        runtime = migrations.LocalRuntime(local_binary, run_id)
        try:
            _progress("START_DISPOSABLE_COCKROACHDB")
            started = step18._start_disposable_runtime(runtime)
            root = step30._Step30HttpSqlClient(started.port, started.sql_port)
            migration_client = _ProgressMigrationClient(root)
            database = run_id + "_db"
            migrations.create_database(root, database)
            _progress("APPLY_MIGRATIONS")
            migration_result = migrations.apply_migrations(
                migration_client, database, timeout=300
            )
            _progress("REPLAY_MIGRATIONS")
            replay_result = migrations.apply_migrations(
                migration_client, database, timeout=300
            )
            expected = len(migrations.load_migrations())
            if (
                len(migration_result["applied"]) != expected
                or replay_result["applied"]
                or len(replay_result["skipped"]) != expected
            ):
                raise ValidationFailure("STEP33_MIGRATION_REPLAY_MISMATCH")
            catalog_result = migrations.assert_step33_security_catalog(root, database)
            root.execute(database, _seed_identity_sql(), timeout=120)
            app_role = "mp_s33_app_" + uuid.uuid4().hex[:12]
            step27._create_validation_role(root, app_role)
            service_result = _validate_service(
                root=root,
                database=database,
                app_role=app_role,
            )
        except BaseException as error:
            _failure_progress("VALIDATE_STEP33_LEDGER", error)
            primary_error = error
        finally:
            _progress("CLEANUP_DISPOSABLE_RUNTIME")
            if root is not None:
                if database is not None:
                    try:
                        migrations.drop_database(root, database, timeout=180)
                    except BaseException:
                        cleanup_errors.append("DATABASE_CLEANUP_FAILED")
                if app_role is not None:
                    try:
                        step27._drop_validation_role(root, app_role)
                    except BaseException:
                        cleanup_errors.append("APP_ROLE_CLEANUP_FAILED")
            if runtime is not None:
                try:
                    cleanup = _stop_owned_runtime(runtime)
                except BaseException:
                    cleanup_errors.append("RUNTIME_CLEANUP_FAILED")

    if primary_error is not None:
        if isinstance(primary_error, ValidationFailure):
            raise primary_error
        code = getattr(primary_error, "sanitized_code", None)
        raise ValidationFailure(
            code if isinstance(code, str) else type(primary_error).__name__.upper()
        ) from primary_error
    if cleanup_errors:
        raise ValidationFailure("STEP33_" + "_".join(cleanup_errors))
    if not all(
        cleanup.get(field) is expected
        for field, expected in (
            ("pid_exited", True),
            ("ports_closed", True),
            ("temporary_store_removed", True),
            ("force_kill_used", False),
        )
    ):
        raise ValidationFailure("STEP33_RUNTIME_CLEANUP_INCOMPLETE")
    if None in (service_result, migration_result, replay_result, catalog_result):
        raise ValidationFailure("STEP33_VALIDATION_RESULT_INCOMPLETE")

    result: dict[str, Any] = {
        "authority": {
            "audit_approval_authority": False,
            "audit_business_authority": False,
            "audit_commit_authority": False,
            "audit_execution_authority": False,
            "audit_publication_authority": False,
        },
        "canonicalization_identity": STEP33_CANONICALIZATION_ID,
        "chain_hash_algorithm": STEP33_HASH_ALGORITHM,
        "chain_partition_policy": STEP33_CHAIN_POLICY_ID,
        "cleanup": {
            "app_role_removed": True,
            "database_removed": True,
            "force_kill_used": cleanup["force_kill_used"],
            "pid_exited": cleanup["pid_exited"],
            "ports_closed": cleanup["ports_closed"],
            "temporary_store_removed": cleanup["temporary_store_removed"],
        },
        "database": {
            "binary_sha256": EXPECTED_COCKROACH_SHA256,
            "migration_count": len(migration_result["applied"]),
            "migration_id": migrations.STEP33_MIGRATION_ID,
            "migration_sha256": migrations.STEP33_MIGRATION_SHA256,
            "replay_skipped_count": len(replay_result["skipped"]),
            "security_catalog": catalog_result,
            "version": migrations.PINNED_VERSION,
        },
        "effect_bounds": {
            "aws_mutations": 0,
            "external_execution_actions": 0,
            "human_review_workspace": 0,
            "model_calls": 0,
            "personal_memory_ui": 0,
            "provider_calls": 0,
            "s3_mutations": 0,
            "web_calls": 0,
        },
        "event_schema_version": STEP33_AUDIT_SCHEMA_VERSION,
        "event_type_registry_version": STEP33_EVENT_REGISTRY_VERSION,
        "rls": {
            "force_rls": True,
            "rls_enabled": True,
            "runtime_bypass_rls": False,
        },
        "scope": {
            "owner_user_id": OWNER_A,
            "tenant_id": TENANT_A,
        },
        "schema_version": "step33-audit-ledger-validation-1a",
        "start_sha": START_SHA,
        "status": "PASS",
        "step": 33,
        "step34_boundary": {
            "human_review_workspace": 0,
            "personal_memory_ui": 0,
            "step34_started": False,
        },
        "verification_policy_digest": STEP33_VERIFICATION_POLICY_DIGEST,
        **service_result,
    }
    result["validation_digest"] = canonical_sha256(result)
    return result


def main() -> int:
    try:
        result = validate(_arguments())
    except (
        OSError,
        TypeError,
        ValueError,
        RuntimeError,
        migrations.MigrationError,
        ValidationFailure,
    ) as error:
        reason = error.code if isinstance(error, ValidationFailure) else type(error).__name__
        print(canonical_json({"reason": reason, "status": "FAILED"}), file=sys.stderr)
        return 1
    print(canonical_json(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
