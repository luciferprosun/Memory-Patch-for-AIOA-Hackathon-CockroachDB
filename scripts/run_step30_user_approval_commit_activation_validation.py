#!/usr/bin/env python3
"""Controlled Step 30 owner approval, technical commit, and activation proof."""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import uuid
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts"), str(ROOT)]

import run_cockroachdb_migrations as migrations  # noqa: E402
import run_step18_retrieval_validation as step18  # noqa: E402
import run_step27_personal_memory_validation as step27  # noqa: E402
import run_step29_personal_memory_patch_validation as step29  # noqa: E402
from tests.test_step26_verified_answer_output import hat_lineage  # noqa: E402

from aioa_memory_kernel.contracts.enums import (  # noqa: E402
    PatchState,
    PersonalMemorySpaceState,
)
from aioa_memory_kernel.contracts.serialization import (  # noqa: E402
    canonical_json,
    canonical_sha256,
)
from aioa_memory_kernel.persistence import (  # noqa: E402
    IdempotencyService,
    SerializableTransactionRunner,
)
from aioa_memory_kernel.security.credentials import CredentialPurpose  # noqa: E402
from aioa_memory_kernel.personal_memory import (  # noqa: E402
    STEP27_SCHEMA_VERSION,
    STEP29_SCHEMA_VERSION,
    AdvancePersonalMemoryPatchToAwaitingApproval,
    BindPersonalMemoryPatchEvidence,
    ConfigureSlotCommand,
    CorrectionCandidateBridgeService,
    CreateEmptySlotCommand,
    CreatePersonalMemoryPatchProposal,
    ModelBindingCommand,
    PersonalMemoryActivationService,
    PersonalMemoryApprovalService,
    PersonalMemoryBindingAction,
    PersonalMemoryBindingMode,
    PersonalMemoryCommitHelper,
    PersonalMemoryModelBinding,
    PersonalMemoryMutationActor,
    PersonalMemoryPatchLifecycleError,
    PersonalMemoryPatchProposalService,
    PersonalMemoryQuotaPolicyRecord,
    PersonalMemoryService,
    TransitionSlotCommand,
    ValidatePersonalMemoryPatchProposal,
    build_personal_memory_activation_request,
    build_personal_memory_approval_request,
    build_personal_memory_commit_request,
    load_personal_memory_approval_policy,
)


START_SHA = "0805fcbb04822d48198aa95ead42abd281784001"
EXPECTED_COCKROACH_SHA256 = step27.EXPECTED_COCKROACH_SHA256
DEFAULT_EXTERNAL_ENV = step27.DEFAULT_EXTERNAL_ENV
# Reuse the exact identities inserted by the bounded Step 29 fixture builder so
# the Step 30 negatives reach owner-scoped RLS instead of stopping at an
# unrelated request-context foreign key.
OTHER_USER = step29.OTHER_USER
OTHER_TENANT = step29.OTHER_TENANT
OTHER_TENANT_USER = step29.OTHER_TENANT_USER


class ValidationFailure(RuntimeError):
    """Sanitized controlled-validation failure."""

    def __init__(self, code: str) -> None:
        super().__init__("Step 30 controlled validation failed")
        self.code = code


class _ValidationTrustedClock:
    """Deterministic application-owned time, independent of request payloads."""

    def __init__(self, current: datetime) -> None:
        self._current = current

    def now(self) -> datetime:
        return self._current

    def set(self, current: datetime) -> None:
        self._current = current


class _ProgressMigrationClient:
    def __init__(self, delegate: step18._Step18HttpSqlClient) -> None:
        self._delegate = delegate
        self._migration_ids = tuple(
            item.migration_id for item in migrations.load_migrations()
        )
        self._next = 0
        self._announced: set[str] = set()

    def execute(
        self,
        database: str,
        sql: str,
        *,
        timeout: float = 300,
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
        except migrations.SqlError as error:
            print(
                canonical_json(
                    {
                        "detail": str(error)[:2048],
                        "sqlstate": error.sqlstate,
                        "stage": current or "MIGRATION_CATALOG",
                        "status": "FAILED",
                        "step": 30,
                    }
                ),
                file=sys.stderr,
                flush=True,
            )
            raise
        if (
            current
            and "INSERT INTO memory_patch.schema_migrations" in sql
            and current in sql
        ):
            self._next += 1
        return result


class _Step30HttpSqlClient(step18._Step18HttpSqlClient):
    """Retain a bounded migration diagnostic without exposing SQL payloads."""

    def execute_results(
        self,
        database: str,
        statements: tuple[str, ...],
        *,
        timeout: float = 300,
        separate_transactions: bool = True,
    ) -> tuple[Mapping[str, Any], ...]:
        """Identify a rejected migration statement without logging its SQL."""

        if separate_transactions and len(statements) > 1:
            combined: list[Mapping[str, Any]] = []
            for statement_number, statement in enumerate(statements, start=1):
                try:
                    if self._requires_pgwire(statement):
                        combined.append(
                            self._execute_admin_pgwire(
                                database,
                                statement,
                                timeout=timeout,
                            )
                        )
                    else:
                        combined.extend(
                            super().execute_results(
                                database,
                                (statement,),
                                timeout=timeout,
                                separate_transactions=False,
                            )
                        )
                except migrations.SqlError as error:
                    raise migrations.SqlError(
                        "owned Step 30 SQL API statement "
                        f"{statement_number} failed",
                        sqlstate=error.sqlstate,
                    ) from error
                except migrations.MigrationError as error:
                    raise migrations.MigrationError(
                        "owned Step 30 SQL API statement "
                        f"{statement_number} failed: {error}"
                    ) from error
            return tuple(combined)
        return super().execute_results(
            database,
            statements,
            timeout=timeout,
            separate_transactions=separate_transactions,
        )

    @staticmethod
    def _pgwire_error(payload: bytes) -> migrations.SqlError:
        fields: dict[str, str] = {}
        for field in payload.rstrip(b"\x00").split(b"\x00"):
            if field:
                fields[field[:1].decode("ascii", "ignore")] = field[1:].decode(
                    "utf-8",
                    "replace",
                )
        message = " ".join(fields.get("M", "statement rejected").split())[:512]
        return migrations.SqlError(
            "owned Step 30 pgwire statement failed: " + message,
            sqlstate=fields.get("C"),
        )


class _Step30PgwireError(RuntimeError):
    """Bounded error used only by the disposable Step 30 validator."""

    def __init__(self, message: str, *, sqlstate: str | None) -> None:
        super().__init__(message)
        self.sqlstate = sqlstate
        self.pgcode = sqlstate


def _statement_stage(sql: str) -> str:
    normalized = " ".join(sql.upper().split())
    for fragment, stage in (
        ("STEP30_COMMIT_HELPER_AUTHORIZED", "COMMIT_AUTHORITY_PROBE"),
        ("INSERT INTO MEMORY_PATCH.PERSISTENCE_OPERATIONS", "COMMIT_IDEMPOTENCY_BEGIN"),
        ("INSERT INTO MEMORY_PATCH.MEMORY_PATCH_COMMITS", "COMMIT_RECORD_INSERT"),
        ("INSERT INTO MEMORY_PATCH.MEMORY_ITEMS", "COMMITTED_PATCH_INSERT"),
        ("UPDATE MEMORY_PATCH.MEMORY_PATCH_PROPOSALS", "COMMIT_STATE_TRANSITION"),
        ("INSERT INTO MEMORY_PATCH.PATCH_TRANSITION_RECORDS", "COMMIT_EVENT_INSERT"),
        ("UPDATE MEMORY_PATCH.PERSISTENCE_OPERATIONS", "COMMIT_IDEMPOTENCY_COMPLETE"),
        ("UPDATE MEMORY_PATCH.MEMORY_ITEMS", "ACTIVE_PATCH_UPDATE"),
        ("FROM MEMORY_PATCH.PERSONAL_MEMORY_SPACES", "COMMIT_SLOT_READ"),
        ("FROM MEMORY_PATCH.MEMORY_PATCH_PROPOSALS", "COMMIT_PROPOSAL_READ"),
    ):
        if fragment in normalized:
            return stage
    if normalized.startswith("SELECT MEMORY_PATCH.SET_REQUEST_CONTEXT"):
        return "COMMIT_REQUEST_CONTEXT"
    if normalized.startswith("BEGIN"):
        return "COMMIT_TRANSACTION_BEGIN"
    if normalized.startswith("COMMIT"):
        return "COMMIT_TRANSACTION_CLOSE"
    if normalized.startswith("ROLLBACK"):
        return "COMMIT_TRANSACTION_ROLLBACK"
    return "COMMIT_DATABASE_STATEMENT"


class _DiagnosticPgwireConnection(step27._PgwireConnection):
    """Report the failed statement class without logging SQL or parameters."""

    @staticmethod
    def _server_error(payload: bytes) -> _Step30PgwireError:
        fields: dict[str, str] = {}
        for field in payload.rstrip(b"\x00").split(b"\x00"):
            if field:
                fields[field[:1].decode("ascii", "ignore")] = field[1:].decode(
                    "utf-8", "replace"
                )
        message = " ".join(fields.get("M", "statement rejected").split())[:384]
        return _Step30PgwireError(message, sqlstate=fields.get("C"))

    def _query(self, sql: str) -> tuple[dict[str, object], ...]:
        try:
            return super()._query(sql)
        except _Step30PgwireError as error:
            _failure_progress(_statement_stage(sql), error)
            raise


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cockroach-binary", type=Path)
    parser.add_argument("--external-env", type=Path, default=DEFAULT_EXTERNAL_ENV)
    return parser.parse_args()


def _progress(stage: str) -> None:
    print(
        canonical_json({"stage": stage, "status": "RUNNING", "step": 30}),
        file=sys.stderr,
        flush=True,
    )


def _failure_progress(stage: str, error: BaseException) -> None:
    """Expose only bounded database diagnostics from the owned fixture."""

    current: BaseException | None = error
    chain: list[str] = []
    seen: set[int] = set()
    sqlstate = getattr(error, "sqlstate", None)
    while current is not None and id(current) not in seen and len(chain) < 5:
        seen.add(id(current))
        candidate_sqlstate = getattr(current, "sqlstate", None)
        if isinstance(candidate_sqlstate, str):
            sqlstate = candidate_sqlstate
        detail = " ".join(str(current).split())[:384]
        chain.append(type(current).__name__ + (":" + detail if detail else ""))
        current = current.__cause__
    print(
        canonical_json(
            {
                "detail": chain,
                "sqlstate": sqlstate,
                "stage": stage,
                "status": "FAILED",
                "step": 30,
            }
        ),
        file=sys.stderr,
        flush=True,
    )


def _runner(
    *,
    port: int,
    database: str,
    role: str,
    credential_purpose: CredentialPurpose,
    diagnostic: bool = False,
) -> SerializableTransactionRunner:
    connection_type = (
        _DiagnosticPgwireConnection if diagnostic else step27._PgwireConnection
    )
    return SerializableTransactionRunner(
        lambda: connection_type(
            port=port,
            database=database,
            user=role,
        ),
        credential_purpose=credential_purpose,
        sleep=lambda _delay: None,
    )


def _create_commit_validation_role(
    root: step18._Step18HttpSqlClient,
    role: str,
) -> None:
    identifier = step27.rls_validation.role_identifier(role)
    connection = step27._PgwireConnection(
        port=root.sql_port,
        database="defaultdb",
        user="root",
    )
    try:
        cursor = connection.cursor()
        cursor.execute(
            "SET allow_role_memberships_to_change_during_transaction = true"
        )
        cursor.execute(
            f"CREATE ROLE {identifier} "
            "WITH LOGIN NOCREATEROLE NOCREATEDB NOBYPASSRLS"
        )
        cursor.execute(
            "GRANT mp_personal_memory_commit_helper, "
            "mp_request_context_setter TO " + identifier
        )
        cursor.close()
    finally:
        connection.close()


def _drop_commit_validation_role(
    root: step18._Step18HttpSqlClient,
    role: str,
) -> None:
    identifier = step27.rls_validation.role_identifier(role)
    connection = step27._PgwireConnection(
        port=root.sql_port,
        database="defaultdb",
        user="root",
    )
    try:
        cursor = connection.cursor()
        cursor.execute(
            "SET allow_role_memberships_to_change_during_transaction = true"
        )
        cursor.execute(
            "REVOKE mp_personal_memory_commit_helper, "
            "mp_request_context_setter FROM " + identifier
        )
        cursor.execute("DROP ROLE IF EXISTS " + identifier)
        cursor.close()
    finally:
        connection.close()


def _quota(
    tenant_id: str,
    owner_user_id: str,
    *,
    suffix: str,
) -> PersonalMemoryQuotaPolicyRecord:
    base = step29._quota(tenant_id, owner_user_id)
    return replace(
        base,
        quota_policy_id=f"personal-quota-step30-{suffix}-v1",
        limits=replace(
            base.limits,
            maximum_total_spaces=3,
            maximum_active_spaces=3,
            maximum_archived_spaces=3,
        ),
    )


def _create_slot(
    service: PersonalMemoryService,
    request,
    *,
    slot_id: str,
    suffix: str,
    offset: int,
):
    tenant_id = request.route.tenant_id
    owner_user_id = request.route.user_id
    at = request.temporal_result.trusted_now - timedelta(seconds=20 - offset)
    quota = _quota(tenant_id, owner_user_id, suffix=suffix)
    slot, _ = service.create_empty_slot(
        CreateEmptySlotCommand(
            schema_version=STEP27_SCHEMA_VERSION,
            tenant_id=tenant_id,
            owner_user_id=owner_user_id,
            personal_memory_space_id=slot_id,
            quota_policy=quota,
            idempotency_key=f"step30-{suffix}-create-slot",
            requested_at=at,
            actor=PersonalMemoryMutationActor.OWNER_CONFIGURATION,
        )
    )
    slot, _ = service.configure_slot(
        ConfigureSlotCommand(
            schema_version=STEP27_SCHEMA_VERSION,
            tenant_id=tenant_id,
            owner_user_id=owner_user_id,
            personal_memory_space_id=slot_id,
            display_name=f"Step 30 {suffix} private memory",
            quota_policy=quota,
            expected_state_version=slot.state_version,
            expected_configuration_version=slot.configuration_version,
            idempotency_key=f"step30-{suffix}-configure-slot",
            requested_at=at + timedelta(seconds=1),
            actor=PersonalMemoryMutationActor.OWNER_CONFIGURATION,
        )
    )
    binding = PersonalMemoryModelBinding(
        schema_version=STEP27_SCHEMA_VERSION,
        tenant_id=tenant_id,
        owner_user_id=owner_user_id,
        personal_memory_space_id=slot_id,
        provider_id="provider-neutral-validation",
        model_id="model-step30-validation",
        model_revision_or_declared_version="revision-1",
        binding_mode=PersonalMemoryBindingMode.EXACT_MODEL,
        enabled=True,
        binding_version=1,
        bound_at=at + timedelta(seconds=2),
    )
    slot, _ = service.update_model_binding(
        ModelBindingCommand(
            schema_version=STEP27_SCHEMA_VERSION,
            tenant_id=tenant_id,
            owner_user_id=owner_user_id,
            personal_memory_space_id=slot_id,
            binding=binding,
            action=PersonalMemoryBindingAction.ADD,
            expected_state_version=slot.state_version,
            expected_configuration_version=slot.configuration_version,
            idempotency_key=f"step30-{suffix}-bind-model",
            requested_at=at + timedelta(seconds=2),
            actor=PersonalMemoryMutationActor.OWNER_CONFIGURATION,
        )
    )
    slot, _ = service.transition_slot(
        TransitionSlotCommand(
            schema_version=STEP27_SCHEMA_VERSION,
            tenant_id=tenant_id,
            owner_user_id=owner_user_id,
            personal_memory_space_id=slot_id,
            target_state=PersonalMemorySpaceState.ACTIVE,
            expected_state_version=slot.state_version,
            expected_configuration_version=slot.configuration_version,
            idempotency_key=f"step30-{suffix}-activate-slot",
            requested_at=at + timedelta(seconds=3),
            actor=PersonalMemoryMutationActor.OWNER_CONFIGURATION,
        )
    )
    return slot


def _prepare_awaiting(
    *,
    request,
    slot,
    candidate_service: CorrectionCandidateBridgeService,
    proposal_service: PersonalMemoryPatchProposalService,
    suffix: str,
    offset: int,
):
    now = request.temporal_result.trusted_now + timedelta(seconds=offset)
    envelope, answer, snapshot, links = step29._pipeline_candidate(
        request,
        slot,
        suffix=f"step30-{suffix}",
        text=request.correction_packet.ordered_claims[0].exact_claim_text,
    )
    candidate_service.submit_kernel_candidate(envelope)
    create = CreatePersonalMemoryPatchProposal(
        schema_version=STEP29_SCHEMA_VERSION,
        tenant_id=slot.tenant_id,
        owner_user_id=slot.owner_user_id,
        personal_memory_space_id=slot.personal_memory_space_id,
        candidate_id=envelope.candidate_id,
        candidate_envelope_hash=envelope.envelope_hash,
        expected_target_binding_hash=(
            envelope.submission.target_slot_binding.target_binding_hash
        ),
        idempotency_key=f"step30-{suffix}-create-proposal",
        requested_at=now + timedelta(seconds=3),
    )
    proposed, _ = proposal_service.create_proposal(envelope, create)
    evidence = {
        "bundles": tuple(item.bundle for item in request.step20_outcomes),
        "temporal_result": request.temporal_result,
        "claim_links": links,
        "claim_assessments": snapshot.ordered_candidate_assessments,
        "correction_packet": request.correction_packet,
        "verified_answer": answer,
    }
    bind = step29._transition_command(
        proposed,
        BindPersonalMemoryPatchEvidence,
        key=f"step30-{suffix}-bind-evidence",
        at=now + timedelta(seconds=4),
    )
    bound, _ = proposal_service.bind_evidence(bind, **evidence)
    validate = step29._transition_command(
        bound,
        ValidatePersonalMemoryPatchProposal,
        key=f"step30-{suffix}-validate",
        at=now + timedelta(seconds=5),
    )
    validated, receipt, _ = proposal_service.validate_proposal(
        validate,
        **evidence,
    )
    advance = step29._transition_command(
        validated,
        AdvancePersonalMemoryPatchToAwaitingApproval,
        key=f"step30-{suffix}-await",
        at=now + timedelta(seconds=6),
        receipt_hash=receipt.receipt_hash,
    )
    awaiting, _ = proposal_service.advance_to_awaiting_approval(advance)
    if awaiting.state is not PatchState.AWAITING_APPROVAL:
        raise ValidationFailure("STEP30_STEP29_PREREQUISITE_FAILED")
    return awaiting


def _direct_skip_denied(
    runner: SerializableTransactionRunner,
    *,
    state,
    target: PatchState,
    operation: str,
) -> bool:
    try:
        row = runner.run(
            step29._context(state.proposal.tenant_id, state.proposal.owner_user_id),
            lambda tx: tx.fetch_one(
                "UPDATE memory_patch.memory_patch_proposals "
                "SET lifecycle_state = %s, step29_state_version = %s "
                "WHERE tenant_id = %s AND owner_user_id = %s "
                "AND proposal_id = %s RETURNING proposal_id",
                (
                    target.value,
                    {PatchState.APPROVED: 5, PatchState.COMMITTED: 6, PatchState.ACTIVE: 7}[
                        target
                    ],
                    state.proposal.tenant_id,
                    state.proposal.owner_user_id,
                    state.proposal.proposal_id,
                ),
            ),
            operation_kind=operation,
        )
        return row is None
    except Exception as error:
        return getattr(error, "sqlstate", None) in {"23514", "42501", "44000"}


def _count_visible(
    runner: SerializableTransactionRunner,
    *,
    tenant_id: str,
    user_id: str,
    proposal_id: str,
) -> int:
    row = runner.run(
        step29._context(tenant_id, user_id),
        lambda tx: tx.fetch_one(
            "SELECT count(*) AS row_count "
            "FROM memory_patch.memory_patch_proposals "
            "WHERE proposal_id = %s",
            (proposal_id,),
        ),
        operation_kind="STEP30_OWNER_RLS_READ_PROBE",
    )
    return 0 if row is None else int(row["row_count"])


def _validate_service(
    *,
    root: step18._Step18HttpSqlClient,
    database: str,
    app_role: str,
    commit_role: str,
) -> Mapping[str, Any]:
    request, _ = hat_lineage()
    tenant_id = request.route.tenant_id
    owner_user_id = request.route.user_id
    now = request.temporal_result.trusted_now
    if root.sql_port is None:
        raise ValidationFailure("STEP30_SQL_PORT_MISSING")
    app_runner = _runner(
        port=root.sql_port,
        database=database,
        role=app_role,
        credential_purpose=CredentialPurpose.APPLICATION_DATABASE,
    )
    commit_runner = _runner(
        port=root.sql_port,
        database=database,
        role=commit_role,
        credential_purpose=CredentialPurpose.PERSONAL_MEMORY_COMMIT_DATABASE,
        diagnostic=True,
    )
    clock = lambda: now + timedelta(hours=1)  # noqa: E731
    trusted_clock = _ValidationTrustedClock(now + timedelta(hours=1))
    personal = PersonalMemoryService(
        app_runner,
        idempotency=IdempotencyService(clock=clock),
    )
    candidates = CorrectionCandidateBridgeService(
        app_runner,
        idempotency=IdempotencyService(clock=clock),
    )
    proposals = PersonalMemoryPatchProposalService(
        app_runner,
        idempotency=IdempotencyService(clock=clock),
    )
    approvals = PersonalMemoryApprovalService(
        app_runner,
        idempotency=IdempotencyService(clock=clock),
        trusted_clock=trusted_clock,
    )
    commits = PersonalMemoryCommitHelper(
        commit_runner,
        idempotency=IdempotencyService(clock=clock),
        trusted_clock=trusted_clock,
    )
    activations = PersonalMemoryActivationService(
        commit_runner,
        idempotency=IdempotencyService(clock=clock),
        trusted_clock=trusted_clock,
    )
    _progress("STEP30_SERVICES_CONSTRUCTED")

    slot = _create_slot(
        personal,
        request,
        slot_id="personal-slot-step30-primary",
        suffix="primary",
        offset=0,
    )
    _progress("STEP30_TARGET_SLOT_ACTIVE")
    awaiting = _prepare_awaiting(
        request=request,
        slot=slot,
        candidate_service=candidates,
        proposal_service=proposals,
        suffix="primary",
        offset=0,
    )
    _progress("STEP30_STEP29_AWAITING_FIXTURE_READY")
    if not _direct_skip_denied(
        app_runner,
        state=awaiting,
        target=PatchState.COMMITTED,
        operation="STEP30_SKIP_AWAITING_TO_COMMITTED",
    ):
        raise ValidationFailure("STEP30_AWAITING_COMMITTED_SKIP_ALLOWED")

    approval_request = build_personal_memory_approval_request(
        awaiting,
        approval_nonce="step30-primary-human-approval",
        requested_at=now + timedelta(seconds=7),
    )
    trusted_clock.set(approval_request.requested_at)
    approved, approval_receipt, approval_replay = approvals.approve(
        approval_request,
        authenticated_actor_user_id=owner_user_id,
    )
    approved_replay, replayed_approval, approval_exact_replay = approvals.approve(
        approval_request,
        authenticated_actor_user_id=owner_user_id,
    )
    if (
        approval_replay
        or not approval_exact_replay
        or approved_replay != approved
        or replayed_approval != approval_receipt
    ):
        raise ValidationFailure("STEP30_APPROVAL_REPLAY_FAILED")
    changed_approval_denied = False
    try:
        approvals.approve(
            replace(approval_request, approval_summary_digest="0" * 64),
            authenticated_actor_user_id=owner_user_id,
        )
    except Exception:
        changed_approval_denied = True
    if not changed_approval_denied:
        raise ValidationFailure("STEP30_APPROVAL_CHANGED_REPLAY_ALLOWED")
    if not _direct_skip_denied(
        commit_runner,
        state=approved,
        target=PatchState.ACTIVE,
        operation="STEP30_SKIP_APPROVED_TO_ACTIVE",
    ):
        raise ValidationFailure("STEP30_APPROVED_ACTIVE_SKIP_ALLOWED")
    _progress("STEP30_AWAITING_TO_APPROVED")

    commit_request = build_personal_memory_commit_request(
        approved,
        commit_idempotency_key="step30-primary-technical-commit",
        requested_at=approved.updated_at + timedelta(seconds=1),
    )
    trusted_clock.set(commit_request.requested_at)
    _progress("STEP30_TECHNICAL_COMMIT_STARTED")
    try:
        committed, commit_receipt, commit_replay = commits.commit(commit_request)
    except BaseException as error:
        _failure_progress("STEP30_TECHNICAL_COMMIT", error)
        raise
    _progress("STEP30_TECHNICAL_COMMIT_PERSISTED")
    committed_replay, replayed_commit, commit_exact_replay = commits.commit(
        commit_request
    )
    _progress("STEP30_TECHNICAL_COMMIT_REPLAYED")
    if (
        commit_replay
        or not commit_exact_replay
        or committed_replay != committed
        or replayed_commit != commit_receipt
    ):
        raise ValidationFailure("STEP30_COMMIT_REPLAY_FAILED")
    changed_commit_denied = False
    try:
        commits.commit(replace(commit_request, proposal_hash="0" * 64))
    except Exception:
        changed_commit_denied = True
    if not changed_commit_denied:
        raise ValidationFailure("STEP30_COMMIT_CHANGED_REPLAY_ALLOWED")
    _progress("STEP30_APPROVED_TO_COMMITTED")

    activation_request = build_personal_memory_activation_request(
        committed,
        activation_idempotency_key="step30-primary-activation",
        requested_at=committed.updated_at + timedelta(seconds=1),
    )
    trusted_clock.set(activation_request.requested_at)
    _progress("STEP30_ACTIVATION_STARTED")
    try:
        active, activation_receipt, activation_replay = activations.activate(
            activation_request
        )
    except BaseException as error:
        _failure_progress("STEP30_ACTIVATION", error)
        raise
    _progress("STEP30_ACTIVATION_PERSISTED")
    active_replay, replayed_activation, activation_exact_replay = (
        activations.activate(activation_request)
    )
    if (
        activation_replay
        or not activation_exact_replay
        or active_replay != active
        or replayed_activation != activation_receipt
    ):
        raise ValidationFailure("STEP30_ACTIVATION_REPLAY_FAILED")
    changed_activation_denied = False
    try:
        activations.activate(
            replace(activation_request, proposal_hash="0" * 64)
        )
    except Exception:
        changed_activation_denied = True
    if not changed_activation_denied:
        raise ValidationFailure("STEP30_ACTIVATION_CHANGED_REPLAY_ALLOWED")
    _progress("STEP30_COMMITTED_TO_ACTIVE")

    content_hashes = {
        awaiting.proposal.proposal_statement_sha256,
        committed.committed_patch.patch_statement_sha256,
        active.activation_receipt.patch_statement_sha256,
    }
    if len(content_hashes) != 1 or active.canonical_evidence:
        raise ValidationFailure("STEP30_CONTENT_IDENTITY_FAILED")

    cross_user_approval_denied = False
    try:
        approvals.approve(
            approval_request,
            authenticated_actor_user_id=OTHER_USER,
        )
    except PersonalMemoryPatchLifecycleError:
        cross_user_approval_denied = True
    cross_user_commit_denied = False
    try:
        commits.commit(replace(commit_request, owner_user_id=OTHER_USER))
    except Exception:
        cross_user_commit_denied = True
    cross_user_activation_denied = False
    try:
        activations.activate(
            replace(activation_request, owner_user_id=OTHER_USER)
        )
    except Exception:
        cross_user_activation_denied = True
    owner_rows = _count_visible(
        app_runner,
        tenant_id=tenant_id,
        user_id=owner_user_id,
        proposal_id=awaiting.proposal.proposal_id,
    )
    cross_user_rows = _count_visible(
        app_runner,
        tenant_id=tenant_id,
        user_id=OTHER_USER,
        proposal_id=awaiting.proposal.proposal_id,
    )
    cross_tenant_rows = _count_visible(
        app_runner,
        tenant_id=OTHER_TENANT,
        user_id=OTHER_TENANT_USER,
        proposal_id=awaiting.proposal.proposal_id,
    )
    if (
        not all(
            (
                cross_user_approval_denied,
                cross_user_commit_denied,
                cross_user_activation_denied,
            )
        )
        or (owner_rows, cross_user_rows, cross_tenant_rows) != (1, 0, 0)
    ):
        raise ValidationFailure("STEP30_OWNER_ISOLATION_FAILED")
    _progress("STEP30_OWNER_RLS_NEGATIVES")

    slot_toctou = _create_slot(
        personal,
        request,
        slot_id="personal-slot-step30-slot-toctou",
        suffix="slot-toctou",
        offset=20,
    )
    awaiting_toctou = _prepare_awaiting(
        request=request,
        slot=slot_toctou,
        candidate_service=candidates,
        proposal_service=proposals,
        suffix="slot-toctou",
        offset=20,
    )
    approval_toctou_request = build_personal_memory_approval_request(
        awaiting_toctou,
        approval_nonce="step30-slot-toctou-approval",
        requested_at=now + timedelta(seconds=27),
    )
    trusted_clock.set(approval_toctou_request.requested_at)
    approved_toctou, _, _ = approvals.approve(
        approval_toctou_request,
        authenticated_actor_user_id=owner_user_id,
    )
    slot_toctou = step29._transition_slot(
        personal,
        slot_toctou,
        PersonalMemorySpaceState.ARCHIVED,
        key="step30-post-approval-archive",
        at=now + timedelta(seconds=28),
    )
    post_approval_slot_denied = False
    try:
        toctou_commit_at = approved_toctou.updated_at + timedelta(seconds=1)
        trusted_clock.set(toctou_commit_at)
        commits.commit(
            build_personal_memory_commit_request(
                approved_toctou,
                commit_idempotency_key="step30-slot-toctou-commit",
                requested_at=toctou_commit_at,
            )
        )
    except Exception:
        post_approval_slot_denied = True
    if not post_approval_slot_denied:
        raise ValidationFailure("STEP30_POST_APPROVAL_SLOT_CHANGE_ALLOWED")

    slot_quota = _create_slot(
        personal,
        request,
        slot_id="personal-slot-step30-quota-toctou",
        suffix="quota-toctou",
        offset=40,
    )
    awaiting_quota = _prepare_awaiting(
        request=request,
        slot=slot_quota,
        candidate_service=candidates,
        proposal_service=proposals,
        suffix="quota-toctou",
        offset=40,
    )
    quota_approval_request = build_personal_memory_approval_request(
        awaiting_quota,
        approval_nonce="step30-quota-toctou-approval",
        requested_at=now + timedelta(seconds=47),
    )
    trusted_clock.set(quota_approval_request.requested_at)
    approved_quota, _, _ = approvals.approve(
        quota_approval_request,
        authenticated_actor_user_id=owner_user_id,
    )
    root.execute(
        database,
        "UPDATE memory_patch.personal_memory_quota_policies "
        "SET maximum_bytes = 0 WHERE tenant_id = "
        + migrations.sql_literal(tenant_id)
        + " AND owner_user_id = "
        + migrations.sql_literal(owner_user_id)
        + " AND quota_policy_id = "
        + migrations.sql_literal(slot_quota.quota_policy_id),
        timeout=60,
    )
    post_approval_quota_denied = False
    try:
        quota_commit_at = approved_quota.updated_at + timedelta(seconds=1)
        trusted_clock.set(quota_commit_at)
        commits.commit(
            build_personal_memory_commit_request(
                approved_quota,
                commit_idempotency_key="step30-quota-toctou-commit",
                requested_at=quota_commit_at,
            )
        )
    except Exception:
        post_approval_quota_denied = True
    if not post_approval_quota_denied:
        raise ValidationFailure("STEP30_POST_APPROVAL_QUOTA_CHANGE_ALLOWED")
    _progress("STEP30_TOCTOU_NEGATIVES")

    technical_privileges = commit_runner.run(
        step29._context(tenant_id, owner_user_id),
        lambda tx: tx.fetch_one(
            "SELECT "
            "has_table_privilege(current_user, "
            "'memory_patch.memory_patch_approvals', 'INSERT') AS can_approve, "
            "has_table_privilege(current_user, "
            "'memory_patch.hat_manifests', 'UPDATE') AS can_change_hat, "
            "has_table_privilege(current_user, "
            "'memory_patch.knowledge_sources', 'UPDATE') AS can_publish, "
            "has_table_privilege(current_user, "
            "'memory_patch.personal_memory_spaces', 'UPDATE') "
            "AS can_lock_quota"
        ),
        operation_kind="STEP30_COMMIT_AUTHORITY_NEGATIVE",
    )
    if (
        technical_privileges is None
        or any(
            technical_privileges[name] in (True, "true", "t", 1)
            for name in (
                "can_approve",
                "can_change_hat",
                "can_publish",
            )
        )
        or technical_privileges["can_lock_quota"]
        not in (True, "true", "t", 1)
    ):
        raise ValidationFailure("STEP30_COMMIT_ROLE_OVERPRIVILEGED")

    commit_role_slot_mutation_denied = False
    try:
        commit_runner.run(
            step29._context(tenant_id, owner_user_id),
            lambda tx: tx.execute(
                "UPDATE memory_patch.personal_memory_spaces "
                "SET display_name = %s "
                "WHERE tenant_id = %s AND owner_user_id = %s "
                "AND personal_memory_space_id = %s",
                (
                    "forbidden-commit-helper-slot-mutation",
                    tenant_id,
                    owner_user_id,
                    slot.personal_memory_space_id,
                ),
            ),
            operation_kind="STEP30_COMMIT_AUTHORITY_SLOT_MUTATION_NEGATIVE",
        )
    except Exception:
        commit_role_slot_mutation_denied = True
    if not commit_role_slot_mutation_denied:
        raise ValidationFailure("STEP30_COMMIT_ROLE_SLOT_MUTATION_ALLOWED")

    transition_row = app_runner.run(
        step29._context(tenant_id, owner_user_id),
        lambda tx: tx.fetch_one(
            "SELECT count(*) AS row_count "
            "FROM memory_patch.patch_transition_records "
            "WHERE tenant_id = %s AND proposal_id = %s",
            (tenant_id, awaiting.proposal.proposal_id),
        ),
        operation_kind="STEP30_TRANSITION_HISTORY",
    )
    if transition_row is None or int(transition_row["row_count"]) != 7:
        raise ValidationFailure("STEP30_TRANSITION_HISTORY_INVALID")

    policy = load_personal_memory_approval_policy()
    return {
        "activation": {
            "exact_content_identity": True,
            "preactivation_revalidation": "PASS",
            "receipt_hash": activation_receipt.receipt_hash,
            "replay": {
                "changed_replay": "DENIED",
                "exact_replay": "PASS",
            },
            "request_hash": activation_request.request_hash,
        },
        "approval": {
            "actor_type": approval_receipt.actor_type.value,
            "policy_digest": policy.policy_digest,
            "receipt_hash": approval_receipt.receipt_hash,
            "replay": {
                "changed_replay": "DENIED",
                "exact_replay": "PASS",
            },
            "request_hash": approval_request.request_hash,
        },
        "commit": {
            "dedicated_role": "mp_personal_memory_commit_helper",
            "dedicated_role_can_change_slot": False,
            "dedicated_role_quota_lock_trigger_guarded": True,
            "exact_content_identity": True,
            "precommit_revalidation": "PASS",
            "receipt_hash": commit_receipt.receipt_hash,
            "replay": {
                "changed_replay": "DENIED",
                "exact_replay": "PASS",
            },
            "request_hash": commit_request.request_hash,
        },
        "content_identity": {
            "active_statement_sha256": active.activation_receipt.patch_statement_sha256,
            "committed_statement_sha256": committed.committed_patch.patch_statement_sha256,
            "proposal_statement_sha256": awaiting.proposal.proposal_statement_sha256,
        },
        "owner_isolation": {
            "cross_tenant_rows": cross_tenant_rows,
            "cross_user_activation": "DENIED",
            "cross_user_approval": "DENIED",
            "cross_user_commit": "DENIED",
            "cross_user_rows": cross_user_rows,
            "owner_rows": owner_rows,
        },
        "proposal_hash": awaiting.proposal.proposal_hash,
        "proposal_id": awaiting.proposal.proposal_id,
        "state_matrix": {
            "ACTIVE": "PASS",
            "APPROVED": "PASS",
            "AWAITING_APPROVAL": "PASS_STEP29_INPUT",
            "COMMITTED": "PASS",
        },
        "target_slot": {
            "owner_user_id": owner_user_id,
            "personal_memory_space_id": slot.personal_memory_space_id,
            "tenant_id": tenant_id,
        },
        "toctou": {
            "post_approval_quota_change": "DENIED",
            "post_approval_slot_change": "DENIED",
        },
        "transition_history_count": int(transition_row["row_count"]),
        "transition_negatives": {
            "APPROVED_TO_ACTIVE": "DENIED",
            "AWAITING_APPROVAL_TO_COMMITTED": "DENIED",
        },
        "validation_receipt_hash": awaiting.validation_receipt.receipt_hash,
    }


def validate(args: argparse.Namespace) -> Mapping[str, Any]:
    source_binary = step27._source_binary(args)
    identity = migrations.verify_binary_identity(source_binary)
    if identity["binary_sha256"] != EXPECTED_COCKROACH_SHA256:
        raise ValidationFailure("STEP30_COCKROACH_BINARY_DIGEST_MISMATCH")
    request, _ = hat_lineage()
    runtime = None
    root = None
    database = None
    app_role = None
    commit_role = None
    cleanup: Mapping[str, Any] = {}
    cleanup_errors: list[str] = []
    primary_error: BaseException | None = None
    service_result = None
    migration_result = None
    replay_result = None
    catalog_result = None
    failure_stage = "STEP30_RUNTIME_SETUP"

    with tempfile.TemporaryDirectory(prefix="mp-step30-binary-", dir="/tmp") as temp:
        local_binary = Path(temp) / "cockroach"
        shutil.copy2(source_binary, local_binary)
        if (
            migrations.verify_binary_identity(local_binary)["binary_sha256"]
            != EXPECTED_COCKROACH_SHA256
        ):
            raise ValidationFailure("STEP30_COPIED_BINARY_DIGEST_MISMATCH")
        run_id = "mp_step30_" + uuid.uuid4().hex[:12]
        runtime = migrations.LocalRuntime(local_binary, run_id)
        try:
            failure_stage = "START_DISPOSABLE_COCKROACHDB"
            _progress("START_DISPOSABLE_COCKROACHDB")
            root = step18._start_disposable_runtime(runtime)
            root = _Step30HttpSqlClient(root.port, root.sql_port)
            client = _ProgressMigrationClient(root)
            database = run_id + "_db"
            migrations.create_database(root, database)
            failure_stage = "APPLY_MIGRATIONS"
            _progress("APPLY_MIGRATIONS")
            try:
                migration_result = migrations.apply_migrations(
                    client,
                    database,
                    timeout=300,
                )
            except migrations.MigrationError as error:
                print(
                    canonical_json(
                        {
                            "detail": " ".join(str(error).split())[:512],
                            "stage": "STEP30_MIGRATION_OR_CATALOG",
                            "status": "FAILED",
                            "step": 30,
                        }
                    ),
                    file=sys.stderr,
                    flush=True,
                )
                raise
            _progress("REPLAY_MIGRATIONS")
            failure_stage = "REPLAY_MIGRATIONS"
            replay_result = migrations.apply_migrations(
                client,
                database,
                timeout=300,
            )
            expected = len(migrations.load_migrations())
            if (
                len(migration_result["applied"]) != expected
                or replay_result["applied"]
                or len(replay_result["skipped"]) != expected
            ):
                raise ValidationFailure("STEP30_MIGRATION_REPLAY_MISMATCH")
            failure_stage = "SEED_STEP30_IDENTITY"
            root.execute(
                database,
                step29._seed_identity_sql(
                    request.route.tenant_id,
                    request.route.user_id,
                    request.temporal_result.trusted_now,
                ),
                timeout=120,
            )
            app_role = "mp_s30_app_" + uuid.uuid4().hex[:12]
            commit_role = "mp_s30_commit_" + uuid.uuid4().hex[:12]
            failure_stage = "CREATE_STEP30_APP_ROLE"
            step27._create_validation_role(root, app_role)
            failure_stage = "CREATE_STEP30_COMMIT_ROLE"
            _create_commit_validation_role(root, commit_role)
            failure_stage = "VALIDATE_STEP30_CATALOG"
            catalog_result = migrations.assert_step30_security_catalog(
                root,
                database,
            )
            failure_stage = "VALIDATE_STEP30_SERVICES"
            _progress("VALIDATE_STEP30_SERVICES")
            service_result = _validate_service(
                root=root,
                database=database,
                app_role=app_role,
                commit_role=commit_role,
            )
        except BaseException as error:
            _failure_progress(failure_stage, error)
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
                if commit_role is not None:
                    try:
                        _drop_commit_validation_role(root, commit_role)
                    except BaseException:
                        cleanup_errors.append("COMMIT_ROLE_CLEANUP_FAILED")
            if runtime is not None:
                try:
                    cleanup = step18._stop_owned_runtime(runtime)
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
        raise ValidationFailure("STEP30_" + "_".join(cleanup_errors))
    if not all(
        cleanup.get(field) is expected
        for field, expected in (
            ("pid_exited", True),
            ("ports_closed", True),
            ("temporary_store_removed", True),
            ("force_kill_used", False),
        )
    ):
        raise ValidationFailure("STEP30_RUNTIME_CLEANUP_INCOMPLETE")
    if None in (
        service_result,
        migration_result,
        replay_result,
        catalog_result,
    ):
        raise ValidationFailure("STEP30_VALIDATION_RESULT_INCOMPLETE")

    result: dict[str, Any] = {
        "active_patch_canonical_evidence": False,
        "authority": {
            "critic_approval_authority": False,
            "external_execution_authority": False,
            "hat_approval_authority": False,
            "kernel_auto_approval": False,
            "model_approval_authority": False,
        },
        "cleanup": {
            "app_role_removed": True,
            "commit_role_removed": True,
            "database_removed": True,
            "force_kill_used": cleanup["force_kill_used"],
            "pid_exited": cleanup["pid_exited"],
            "ports_closed": cleanup["ports_closed"],
            "temporary_store_removed": cleanup["temporary_store_removed"],
        },
        "database": {
            "binary_sha256": EXPECTED_COCKROACH_SHA256,
            "migration": "0014_step30_user_approval_commit_activation.sql",
            "migration_count": len(migration_result["applied"]),
            "replay_skipped_count": len(replay_result["skipped"]),
            "security_catalog": catalog_result,
            "version": migrations.PINNED_VERSION,
        },
        "effect_bounds": {
            "aws_mutations": 0,
            "external_execution_actions": 0,
            "model_calls": 0,
            "provider_calls": 0,
            "retrieval_calls": 0,
            "s3_mutations": 0,
            "web_calls": 0,
        },
        "schema_version": "step30-user-approval-commit-activation-validation-1a",
        "status": "PASS",
        "step": 30,
        "step31_boundary": {
            "active_patch_retrieval": 0,
            "cross_model_reuse": 0,
            "step31_started": False,
        },
        "start_sha": START_SHA,
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
        print(
            canonical_json({"reason": reason, "status": "FAILED"}),
            file=sys.stderr,
        )
        return 1
    print(canonical_json(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
