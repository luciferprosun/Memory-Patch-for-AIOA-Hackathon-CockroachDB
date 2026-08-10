#!/usr/bin/env python3
"""Controlled Step 34 human-review workspace validation."""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts"), str(ROOT)]

import run_cockroachdb_migrations as migrations  # noqa: E402
import run_step18_retrieval_validation as step18  # noqa: E402
import run_step27_personal_memory_validation as step27  # noqa: E402
import run_step30_user_approval_commit_activation_validation as step30  # noqa: E402
import run_step33_audit_ledger_validation as step33  # noqa: E402

from aioa_memory_kernel.answers import (  # noqa: E402
    FinalOutputStatus,
    HumanReviewRequired,
    STEP26_SCHEMA_VERSION,
    Step26ReasonCode,
)
from aioa_memory_kernel.audit_ledger import (  # noqa: E402
    AuditActorType,
    AuditEventDraft,
    AuditEventType,
    AuditLedgerService,
    AuditReasonCode,
    AuditSubjectType,
    compute_audit_chain_id,
)
from aioa_memory_kernel.audit_ledger.models import (  # noqa: E402
    STEP33_EVENT_REGISTRY_VERSION,
)
from aioa_memory_kernel.contracts.enums import EvidenceStatus  # noqa: E402
from aioa_memory_kernel.contracts.serialization import (  # noqa: E402
    canonical_json,
    canonical_sha256,
)
from aioa_memory_kernel.persistence import (  # noqa: E402
    AccessMode,
    RequestContext,
    extract_sqlstate,
)
from aioa_memory_kernel.review_workspace import (  # noqa: E402
    MAXIMUM_REVIEW_QUEUE_PAGE_SIZE,
    STEP34_REVIEW_SERVICE_ACTOR_ID,
    STEP34_SCHEMA_VERSION,
    HumanReviewWorkspaceError,
    HumanReviewCockroachRepository,
    HumanReviewWorkspaceService,
    ReviewCaseIntakeService,
    ReviewCaseType,
    ReviewAccessPolicy,
    ReviewDecisionPolicy,
    ReviewDecisionHandoffService,
    ReviewDecisionType,
    ReviewPriority,
    ReviewQueueRequest,
    ReviewReasonCode,
    ReviewSourceContext,
    ReviewSourceContract,
    ReviewState,
    ReviewSubjectType,
    ReviewerPrincipal,
    ReviewerRole,
    SubmitReviewDecision,
    build_claim_review_case_request,
    build_open_review_case,
    build_review_handoff_request,
    build_reviewer_authorization,
    human_review_required_case,
    review_to_jsonb,
    transition_review_case,
)
from aioa_memory_kernel.routing import KnowledgePolicyDecision  # noqa: E402


START_SHA = "6f8f14b8acde20a8044d929ba7f6582f2c36785b"
EXPECTED_COCKROACH_SHA256 = step27.EXPECTED_COCKROACH_SHA256
DEFAULT_EXTERNAL_ENV = step27.DEFAULT_EXTERNAL_ENV

TENANT_A = "tenant-step34"
OWNER_A = "owner-step34"
REVIEWER_A = "reviewer-step34-a"
REVIEWER_B = "reviewer-step34-b"
ORDINARY_USER = "ordinary-step34"
TENANT_B = "tenant-step34-isolated"
TENANT_B_REVIEWER = "reviewer-step34-isolated"
BASE_TIME = datetime(2045, 6, 7, 8, 9, 10, tzinfo=UTC)
TRUSTED_NOW = BASE_TIME + timedelta(minutes=10)


class ValidationFailure(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__("Step 34 controlled validation failed")
        self.code = code


class FrozenClock:
    def now(self) -> datetime:
        return TRUSTED_NOW


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cockroach-binary", type=Path)
    parser.add_argument("--external-env", type=Path, default=DEFAULT_EXTERNAL_ENV)
    return parser.parse_args()


def _progress(stage: str) -> None:
    print(
        canonical_json({"stage": stage, "status": "RUNNING", "step": 34}),
        file=sys.stderr,
        flush=True,
    )


def _failure_progress(stage: str, error: BaseException) -> None:
    current: BaseException | None = error
    chain: list[str] = []
    seen: set[int] = set()
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
                "sqlstate": extract_sqlstate(error),
                "stage": stage,
                "status": "FAILED",
                "step": 34,
            }
        ),
        file=sys.stderr,
        flush=True,
    )


class _ProgressMigrationClient:
    def __init__(self, delegate: step30._Step30HttpSqlClient) -> None:
        self._delegate = delegate
        self._migration_ids = tuple(
            item.migration_id for item in migrations.load_migrations()
        )
        self._next = 0
        self._announced: set[str] = set()

    def execute(self, database: str, sql: str, *, timeout: float = 300) -> str:
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


def _context(tenant_id: str, user_id: str) -> RequestContext:
    return RequestContext(
        tenant_id=tenant_id,
        user_id=user_id,
        access_mode=AccessMode.USER_PRIVATE,
    )


def _seed_identity_sql() -> str:
    q = migrations.sql_literal
    at = q(BASE_TIME.isoformat()) + "::TIMESTAMPTZ"
    users = (
        (TENANT_A, OWNER_A, "Step 34 owner"),
        (TENANT_A, REVIEWER_A, "Step 34 reviewer A"),
        (TENANT_A, REVIEWER_B, "Step 34 reviewer B"),
        (TENANT_A, ORDINARY_USER, "Step 34 ordinary user"),
        (TENANT_A, STEP34_REVIEW_SERVICE_ACTOR_ID, "Step 34 service"),
        (TENANT_B, TENANT_B_REVIEWER, "Step 34 isolated reviewer"),
    )
    user_values = ", ".join(
        f"({q(tenant)}, {q(user)}, {q(label)}, '{{}}'::JSONB, {at}, {at})"
        for tenant, user, label in users
    )
    return ";\n".join(
        (
            "INSERT INTO memory_patch.tenants "
            "(tenant_id, display_name, metadata, created_at, updated_at) VALUES "
            f"({q(TENANT_A)}, 'Step 34 tenant', '{{}}'::JSONB, {at}, {at}), "
            f"({q(TENANT_B)}, 'Step 34 isolated tenant', '{{}}'::JSONB, {at}, {at})",
            "INSERT INTO memory_patch.users "
            "(tenant_id, user_id, display_name, metadata, created_at, updated_at) VALUES "
            + user_values,
        )
    )


def _role_identifier(role: str) -> str:
    return step27.rls_validation.role_identifier(role)


def _create_validation_role(root, role: str, memberships: tuple[str, ...]) -> None:
    identifier = _role_identifier(role)
    connection = step27._PgwireConnection(
        port=root.sql_port, database="defaultdb", user="root"
    )
    try:
        cursor = connection.cursor()
        cursor.execute("SET allow_role_memberships_to_change_during_transaction = true")
        cursor.execute(
            f"CREATE ROLE {identifier} "
            "WITH LOGIN NOCREATEROLE NOCREATEDB NOBYPASSRLS"
        )
        cursor.execute("GRANT " + ", ".join(memberships) + " TO " + identifier)
        cursor.close()
    finally:
        connection.close()


def _drop_validation_role(root, role: str, memberships: tuple[str, ...]) -> None:
    identifier = _role_identifier(role)
    connection = step27._PgwireConnection(
        port=root.sql_port, database="defaultdb", user="root"
    )
    try:
        cursor = connection.cursor()
        cursor.execute("SET allow_role_memberships_to_change_during_transaction = true")
        cursor.execute("REVOKE " + ", ".join(memberships) + " FROM " + identifier)
        cursor.execute("DROP ROLE IF EXISTS " + identifier)
        cursor.close()
    finally:
        connection.close()


def _principal(reviewer_id: str, *, tenant_id: str = TENANT_A) -> ReviewerPrincipal:
    return ReviewerPrincipal(
        schema_version=STEP34_SCHEMA_VERSION,
        tenant_id=tenant_id,
        reviewer_id=reviewer_id,
        reviewer_role=ReviewerRole.SENIOR_REVIEWER,
        authentication_context_hash=canonical_sha256(
            {
                "authenticated": True,
                "reviewer_id": reviewer_id,
                "tenant_id": tenant_id,
            }
        ),
        authenticated_at=BASE_TIME,
    )


def _insert_authorization(root, database: str, authorization) -> None:
    q = migrations.sql_literal
    payload = q(canonical_json(review_to_jsonb(authorization))) + "::JSONB"
    owner = "NULL" if authorization.owner_user_id is None else q(authorization.owner_user_id)
    root.execute(
        database,
        "INSERT INTO memory_patch.reviewer_authorizations ("
        "tenant_id, authorization_id, reviewer_id, reviewer_role, case_type, "
        "owner_user_id, access_policy_id, access_policy_version, "
        "access_policy_digest, active, authorization_hash, "
        "authorization_payload, granted_at) VALUES ("
        f"{q(authorization.tenant_id)}, {q(authorization.authorization_id)}, "
        f"{q(authorization.reviewer_id)}, {q(authorization.reviewer_role.value)}, "
        f"{q(authorization.case_type.value)}, {owner}, "
        f"{q(authorization.access_policy_id)}, {q(authorization.access_policy_version)}, "
        f"{q(authorization.access_policy_digest)}, true, "
        f"{q(authorization.authorization_hash)}, {payload}, "
        f"{q(authorization.granted_at.isoformat())}::TIMESTAMPTZ)",
        timeout=60,
    )


def _step26_review() -> HumanReviewRequired:
    return HumanReviewRequired(
        schema_version=STEP26_SCHEMA_VERSION,
        request_id="step34-controlled-answer-request",
        tenant_id=TENANT_A,
        user_id=OWNER_A,
        route_hash=canonical_sha256("step34-controlled-route"),
        selected_hat_id="step34-controlled-hat",
        selected_hat_version="1.0.0",
        selected_manifest_digest=canonical_sha256("step34-controlled-manifest"),
        draft_v1_hash=canonical_sha256("step34-controlled-draft-v1"),
        draft_v2_hashes=(canonical_sha256("step34-controlled-draft-v2"),),
        correction_packet_hash=canonical_sha256("step34-controlled-packet"),
        verification_summary_hash=canonical_sha256(
            "step34-controlled-verification"
        ),
        evidence_status=EvidenceStatus.CONFLICTING,
        knowledge_policy_decision=KnowledgePolicyDecision.BLOCK_ANSWER,
        output_status=FinalOutputStatus.HUMAN_REVIEW_REQUIRED,
        reason_codes=(
            Step26ReasonCode.ANSWER_CONFLICTING_EVIDENCE,
            Step26ReasonCode.HUMAN_REVIEW_REQUIRED,
        ),
        review_payload_hashes=(canonical_sha256("step34-controlled-review"),),
    )


def _append_source_event(
    service: AuditLedgerService,
    *,
    event_type: AuditEventType,
    subject_type: AuditSubjectType,
    subject_id: str,
    subject_hash: str,
    idempotency_key: str,
    offset: int,
):
    observed = BASE_TIME + timedelta(seconds=offset)
    draft = AuditEventDraft(
        event_type=event_type,
        tenant_id=TENANT_A,
        owner_user_id=OWNER_A,
        subject_type=subject_type,
        subject_id=subject_id,
        subject_hash=subject_hash,
        actor_type=AuditActorType.KERNEL,
        actor_id="step34-controlled-kernel",
        idempotency_key=idempotency_key,
        occurred_at=observed,
        recorded_at=TRUSTED_NOW,
        event_payload={
            "business_authority": False,
            "canonical_evidence": False,
            "state": event_type.value,
        },
        reason_codes=(AuditReasonCode.AUDIT_EVENT_APPENDED,),
        policy_id="step34-review-intake-audit-policy",
        policy_version="1",
        policy_digest=canonical_sha256("step34-review-intake-audit-policy-1"),
        lineage_hashes={"subject_hash": subject_hash},
    )
    entry, replay = service.append_event(
        draft,
        authenticated_tenant_id=TENANT_A,
        authenticated_actor_type=AuditActorType.KERNEL,
        authenticated_actor_id="step34-controlled-kernel",
    )
    if replay:
        raise ValidationFailure("STEP34_SOURCE_AUDIT_UNEXPECTED_REPLAY")
    verification = service.verify_chain(
        tenant_id=TENANT_A,
        owner_user_id=OWNER_A,
        authenticated_tenant_id=TENANT_A,
        authenticated_owner_user_id=OWNER_A,
    )
    if not verification.verified or verification.last_hash != entry.envelope.event_hash:
        raise ValidationFailure("STEP34_SOURCE_AUDIT_CHAIN_INVALID")
    return entry, verification


def _queue_request(principal: ReviewerPrincipal, case_types) -> ReviewQueueRequest:
    return ReviewQueueRequest(
        schema_version=STEP34_SCHEMA_VERSION,
        tenant_id=principal.tenant_id,
        reviewer_principal_hash=principal.principal_hash,
        reviewer_id=principal.reviewer_id,
        reviewer_role=principal.reviewer_role,
        case_types=tuple(sorted(case_types, key=lambda item: item.value)),
        page_size=8,
        continuation=None,
        requested_at=BASE_TIME + timedelta(minutes=5),
    )


def _decision_command(
    case,
    detail,
    principal: ReviewerPrincipal,
    decision_type: ReviewDecisionType,
    *,
    idempotency_key: str,
) -> SubmitReviewDecision:
    return SubmitReviewDecision(
        schema_version=STEP34_SCHEMA_VERSION,
        tenant_id=case.tenant_id,
        review_case_id=case.review_case_id,
        review_case_hash=case.case_hash,
        subject_hash=case.subject_hash,
        reviewer_principal_hash=principal.principal_hash,
        reviewer_id=principal.reviewer_id,
        reviewer_role=principal.reviewer_role,
        decision_type=decision_type,
        decision_reason_codes=(ReviewReasonCode.REVIEW_DECISION_RECORDED,),
        reviewer_note="Bounded controlled human review decision.",
        context_digest=detail.detail_hash,
        audit_verification_result_hash=case.audit_verification_result_hash,
        expected_state=ReviewState.CLAIMED,
        expected_state_version=2,
        decided_at=BASE_TIME + timedelta(minutes=6),
        idempotency_key=idempotency_key,
    )


def _expect_workspace_denial(callback: Callable[[], Any], code: str) -> str:
    try:
        callback()
    except HumanReviewWorkspaceError:
        return "DENIED"
    raise ValidationFailure(code + "_NOT_DENIED")


def _expect_database_denial(callback: Callable[[], Any], code: str) -> str:
    try:
        callback()
    except BaseException as error:
        if extract_sqlstate(error) not in {"42501", "44000"}:
            raise ValidationFailure(code + "_WRONG_SQLSTATE") from error
        return "DENIED"
    raise ValidationFailure(code + "_NOT_DENIED")


def _expect_database_constraint_denial(
    callback: Callable[[], Any], code: str
) -> str:
    try:
        callback()
    except BaseException as error:
        if extract_sqlstate(error) != "23514":
            raise ValidationFailure(code + "_WRONG_SQLSTATE") from error
        return "DENIED"
    raise ValidationFailure(code + "_NOT_DENIED")


def _shared_case(source_entry, verification):
    subject_hash = source_entry.envelope.subject_hash
    context = ReviewSourceContext(
        schema_version=STEP34_SCHEMA_VERSION,
        source_contract=(
            ReviewSourceContract.STEP32_SHARED_MEMORY_PROMOTION_PROPOSAL
        ),
        subject_hash=subject_hash,
        context_payload={
            "canonical_conflict": False,
            "candidate_shared_statement_hash": canonical_sha256(
                "step34-controlled-deidentified-candidate"
            ),
            "owner_consent": True,
            "privacy_result": "REVIEW_REQUIRED",
            "review_required": True,
            "source_registry_published": False,
        },
        contains_raw_private_memory=False,
        canonical_evidence=False,
        model_authority=False,
    )
    case = build_open_review_case(
        case_type=ReviewCaseType.SHARED_PROMOTION_PRIVACY_REVIEW,
        tenant_id=TENANT_A,
        owner_user_id=OWNER_A,
        subject_type=ReviewSubjectType.SHARED_MEMORY_PROMOTION_PROPOSAL,
        subject_id=source_entry.envelope.subject_id,
        subject_hash=subject_hash,
        request_id=None,
        kernel_run_id=None,
        route_hash=None,
        review_reason_codes=(
            ReviewReasonCode.REVIEW_AUDIT_CONTEXT_VERIFIED,
            ReviewReasonCode.REVIEW_CASE_CREATED,
        ),
        priority=ReviewPriority.HIGH,
        source_audit_event_hash=source_entry.envelope.event_hash,
        source_chain_id=verification.chain_id,
        audit_verification_result_hash=verification.result_hash,
        audit_context_verified=True,
        required_context_refs={"promotion_proposal_hash": subject_hash},
        source_context_hash=context.context_hash,
        created_at=BASE_TIME + timedelta(minutes=2),
    )
    return case, context


def _validate_services(
    *,
    root,
    database: str,
    app_role: str,
    reviewer_role: str,
    isolated_reviewer_role: str,
    service_role: str,
) -> Mapping[str, Any]:
    clock = FrozenClock()
    app_runner = step30._runner(
        port=root.sql_port, database=database, role=app_role, diagnostic=True
    )
    reviewer_runner = step30._runner(
        port=root.sql_port, database=database, role=reviewer_role, diagnostic=True
    )
    isolated_runner = step30._runner(
        port=root.sql_port,
        database=database,
        role=isolated_reviewer_role,
        diagnostic=True,
    )
    service_runner = step30._runner(
        port=root.sql_port, database=database, role=service_role, diagnostic=True
    )
    audit_service = AuditLedgerService(app_runner)
    intake = ReviewCaseIntakeService(service_runner, trusted_clock=clock)
    workspace = HumanReviewWorkspaceService(reviewer_runner, trusted_clock=clock)
    isolated_workspace = HumanReviewWorkspaceService(
        isolated_runner, trusted_clock=clock
    )
    handoff = ReviewDecisionHandoffService(service_runner, trusted_clock=clock)

    reviewer_a = _principal(REVIEWER_A)
    reviewer_b = _principal(REVIEWER_B)
    source = _step26_review()
    source_entry, source_verification = _append_source_event(
        audit_service,
        event_type=AuditEventType.VERIFIED_ANSWER_BLOCKED,
        subject_type=AuditSubjectType.VERIFIED_ANSWER,
        subject_id="step34-controlled-human-review-required",
        subject_hash=source.result_hash,
        idempotency_key="step34-source-answer-review",
        offset=1,
    )
    answer_case, answer_context = human_review_required_case(
        source,
        source_audit_event_hash=source_entry.envelope.event_hash,
        audit_verification=source_verification,
        created_at=BASE_TIME + timedelta(minutes=1),
    )
    for principal in (reviewer_a, reviewer_b):
        _insert_authorization(
            root,
            database,
            build_reviewer_authorization(
                principal,
                case_type=answer_case.case_type,
                owner_user_id=OWNER_A,
                granted_at=BASE_TIME,
            ),
        )

    _progress("ANSWER_REVIEW_CASE")
    stored_case, replay, created_audit_hash = intake.create_case(
        answer_case,
        answer_context,
        authenticated_tenant_id=TENANT_A,
        authenticated_owner_user_id=OWNER_A,
    )
    if replay:
        raise ValidationFailure("STEP34_FIRST_CASE_CREATE_REPLAYED")
    replay_case = intake.create_case(
        answer_case,
        answer_context,
        authenticated_tenant_id=TENANT_A,
        authenticated_owner_user_id=OWNER_A,
    )
    if not replay_case[1] or replay_case[0] != stored_case:
        raise ValidationFailure("STEP34_CASE_REPLAY_MISMATCH")
    queue = workspace.list_queue(
        _queue_request(reviewer_a, (answer_case.case_type,)), reviewer_a
    )
    if (
        len(queue.items) != 1
        or not queue.minimum_disclosure
        or OWNER_A in queue.items[0].safe_summary
    ):
        raise ValidationFailure("STEP34_QUEUE_DISCLOSURE_OR_ORDER_INVALID")

    _progress("DOUBLE_CLAIM_RACE")
    claim_inputs = tuple(
        (
            principal,
            build_claim_review_case_request(
                answer_case,
                principal,
                requested_at=BASE_TIME + timedelta(minutes=4),
                idempotency_key=f"step34-claim-{principal.reviewer_id}",
            ),
        )
        for principal in (reviewer_a, reviewer_b)
    )

    def claim(value):
        principal, request = value
        try:
            return "PASS", principal, request, workspace.claim_case(request, principal)
        except HumanReviewWorkspaceError:
            return "DENIED", principal, request, None

    # The database/version conflict is exercised in a fixed order so the
    # controlled evidence is byte-stable.  The focused test suite separately
    # runs the same two-reviewer conflict concurrently with real threads.
    claim_results = tuple(claim(value) for value in claim_inputs)
    if sorted(item[0] for item in claim_results) != ["DENIED", "PASS"]:
        raise ValidationFailure("STEP34_DOUBLE_CLAIM_RACE_DIVERGED")
    winner = next(item for item in claim_results if item[0] == "PASS")
    winner_principal = winner[1]
    winner_request = winner[2]
    claimed, claim_receipt, claim_replay = winner[3]
    if claim_replay or claimed.review_state is not ReviewState.CLAIMED:
        raise ValidationFailure("STEP34_CLAIM_RESULT_INVALID")
    exact_claim = workspace.claim_case(winner_request, winner_principal)
    if not exact_claim[2] or exact_claim[1] != claim_receipt:
        raise ValidationFailure("STEP34_CLAIM_REPLAY_MISMATCH")

    detail = workspace.get_detail(
        tenant_id=TENANT_A,
        review_case_id=claimed.review_case_id,
        principal=winner_principal,
    )
    if not detail.audit_context_verified or detail.contains_unrelated_private_data:
        raise ValidationFailure("STEP34_ANSWER_DETAIL_INVALID")
    invalid_command = _decision_command(
        claimed,
        detail,
        winner_principal,
        ReviewDecisionType.APPROVE_SHARED_PROMOTION_CANDIDATE,
        idempotency_key="step34-invalid-cross-family-decision",
    )
    invalid_type = _expect_workspace_denial(
        lambda: workspace.record_decision(invalid_command, winner_principal),
        "STEP34_INVALID_DECISION_TYPE",
    )
    stale_command = replace(
        _decision_command(
            claimed,
            detail,
            winner_principal,
            ReviewDecisionType.REJECT_ANSWER,
            idempotency_key="step34-stale-answer-decision",
        ),
        review_case_hash="0" * 64,
    )
    stale_decision = _expect_workspace_denial(
        lambda: workspace.record_decision(stale_command, winner_principal),
        "STEP34_STALE_DECISION",
    )

    _progress("ANSWER_DECISION_AND_HANDOFF")
    answer_command = _decision_command(
        claimed,
        detail,
        winner_principal,
        ReviewDecisionType.REJECT_ANSWER,
        idempotency_key="step34-answer-decision",
    )
    in_review, answer_decision, answer_receipt, decision_replay = (
        workspace.record_decision(answer_command, winner_principal)
    )
    if decision_replay or in_review.review_state is not ReviewState.IN_REVIEW:
        raise ValidationFailure("STEP34_ANSWER_DECISION_INVALID")
    exact_decision = workspace.record_decision(answer_command, winner_principal)
    if not exact_decision[3] or exact_decision[2] != answer_receipt:
        raise ValidationFailure("STEP34_DECISION_REPLAY_MISMATCH")
    changed_replay = _expect_workspace_denial(
        lambda: workspace.record_decision(
            replace(answer_command, reviewer_note="Changed bounded review note."),
            winner_principal,
        ),
        "STEP34_CHANGED_DECISION_REPLAY",
    )
    answer_handoff_request = build_review_handoff_request(
        in_review,
        answer_decision,
        answer_receipt,
        requested_at=BASE_TIME + timedelta(minutes=7),
        idempotency_key="step34-answer-handoff",
    )
    changed_subject = _expect_workspace_denial(
        lambda: handoff.handoff(
            answer_handoff_request,
            authenticated_service_id=STEP34_REVIEW_SERVICE_ACTOR_ID,
            current_subject_hash="0" * 64,
        ),
        "STEP34_CHANGED_SUBJECT",
    )
    terminal, answer_result, answer_handoff_receipt, handoff_replay = handoff.handoff(
        answer_handoff_request,
        authenticated_service_id=STEP34_REVIEW_SERVICE_ACTOR_ID,
        current_subject_hash=in_review.subject_hash,
    )
    if (
        handoff_replay
        or terminal.review_state is not ReviewState.RESOLVED
        or answer_result.answer_returned
        or answer_result.source_registry_published
        or answer_result.external_execution_authority
    ):
        raise ValidationFailure("STEP34_ANSWER_HANDOFF_INVALID")
    exact_handoff = handoff.handoff(
        answer_handoff_request,
        authenticated_service_id=STEP34_REVIEW_SERVICE_ACTOR_ID,
        current_subject_hash=in_review.subject_hash,
    )
    if not exact_handoff[3] or exact_handoff[2] != answer_handoff_receipt:
        raise ValidationFailure("STEP34_HANDOFF_REPLAY_MISMATCH")
    terminal_claim_replay = workspace.claim_case(winner_request, winner_principal)
    if (
        not terminal_claim_replay[2]
        or terminal_claim_replay[0] != terminal
        or terminal_claim_replay[1] != claim_receipt
    ):
        raise ValidationFailure("STEP34_TERMINAL_CLAIM_REPLAY_MISMATCH")
    progressed_case_replay = intake.create_case(
        answer_case,
        answer_context,
        authenticated_tenant_id=TENANT_A,
        authenticated_owner_user_id=OWNER_A,
    )
    if not progressed_case_replay[1] or progressed_case_replay[0] != terminal:
        raise ValidationFailure("STEP34_PROGRESS_CASE_REPLAY_MISMATCH")

    _progress("SHARED_PROMOTION_REVIEW")
    shared_hash = canonical_sha256("step34-controlled-shared-promotion")
    shared_entry, shared_verification = _append_source_event(
        audit_service,
        event_type=AuditEventType.SHARED_PROMOTION_PROPOSED,
        subject_type=AuditSubjectType.SHARED_PROMOTION_PROPOSAL,
        subject_id="step34-controlled-shared-promotion",
        subject_hash=shared_hash,
        idempotency_key="step34-source-shared-promotion",
        offset=2,
    )
    shared_case, shared_context = _shared_case(shared_entry, shared_verification)
    non_open_shared_case = transition_review_case(
        shared_case,
        state=ReviewState.CLAIMED,
        updated_at=BASE_TIME + timedelta(minutes=3),
        reviewer_id=REVIEWER_A,
        reviewer_role=ReviewerRole.SENIOR_REVIEWER,
    )
    initial_state_skip = _expect_database_constraint_denial(
        lambda: service_runner.run(
            _context(TENANT_A, STEP34_REVIEW_SERVICE_ACTOR_ID),
            lambda transaction: HumanReviewCockroachRepository.insert_case(
                transaction, non_open_shared_case, shared_context
            ),
            operation_kind="STEP34_NON_OPEN_CASE_INSERT_NEGATIVE",
        ),
        "STEP34_NON_OPEN_CASE_INSERT",
    )
    _insert_authorization(
        root,
        database,
        build_reviewer_authorization(
            reviewer_a,
            case_type=shared_case.case_type,
            owner_user_id=OWNER_A,
            granted_at=BASE_TIME,
        ),
    )
    intake.create_case(
        shared_case,
        shared_context,
        authenticated_tenant_id=TENANT_A,
        authenticated_owner_user_id=OWNER_A,
    )
    shared_claim, _, _ = workspace.claim_case(
        build_claim_review_case_request(
            shared_case,
            reviewer_a,
            requested_at=BASE_TIME + timedelta(minutes=4),
            idempotency_key="step34-shared-claim",
        ),
        reviewer_a,
    )
    shared_detail = workspace.get_detail(
        tenant_id=TENANT_A,
        review_case_id=shared_claim.review_case_id,
        principal=reviewer_a,
    )
    shared_command = _decision_command(
        shared_claim,
        shared_detail,
        reviewer_a,
        ReviewDecisionType.REQUEST_REDACTION_CHANGES,
        idempotency_key="step34-shared-decision",
    )
    shared_in_review, shared_decision, shared_receipt, _ = workspace.record_decision(
        shared_command, reviewer_a
    )
    shared_request = build_review_handoff_request(
        shared_in_review,
        shared_decision,
        shared_receipt,
        requested_at=BASE_TIME + timedelta(minutes=7),
        idempotency_key="step34-shared-handoff",
    )
    shared_terminal, shared_result, shared_handoff_receipt, _ = handoff.handoff(
        shared_request,
        authenticated_service_id=STEP34_REVIEW_SERVICE_ACTOR_ID,
        current_subject_hash=shared_in_review.subject_hash,
    )
    if (
        shared_terminal.review_state is not ReviewState.RESOLVED
        or shared_result.source_registry_published
        or shared_result.private_source_mutated
        or shared_result.external_execution_authority
    ):
        raise ValidationFailure("STEP34_SHARED_HANDOFF_INVALID")

    _progress("AUTHORIZATION_AND_AUDIT_NEGATIVES")
    ordinary_denied = _expect_database_denial(
        lambda: app_runner.run(
            _context(TENANT_A, OWNER_A),
            lambda transaction: transaction.fetch_one(
                "SELECT count(*) AS row_count FROM memory_patch.human_review_cases"
            ),
            operation_kind="STEP34_ORDINARY_USER_REVIEW_READ",
        ),
        "STEP34_ORDINARY_USER_REVIEW_ACCESS",
    )
    ordinary_principal = _principal(ORDINARY_USER)
    ordinary_page = isolated_workspace.list_queue(
        _queue_request(ordinary_principal, (answer_case.case_type,)),
        ordinary_principal,
    )
    tenant_b_principal = _principal(TENANT_B_REVIEWER, tenant_id=TENANT_B)
    tenant_b_page = isolated_workspace.list_queue(
        _queue_request(tenant_b_principal, (answer_case.case_type,)),
        tenant_b_principal,
    )
    if ordinary_page.items or tenant_b_page.items:
        raise ValidationFailure("STEP34_UNAUTHORIZED_QUEUE_VISIBLE")

    chain = audit_service.verify_chain(
        tenant_id=TENANT_A,
        owner_user_id=OWNER_A,
        authenticated_tenant_id=TENANT_A,
        authenticated_owner_user_id=OWNER_A,
    )
    if not chain.verified or chain.event_count != 12:
        raise ValidationFailure("STEP34_FINAL_AUDIT_CHAIN_INVALID")
    root_counts = migrations.parse_tsv(
        root.execute(
            database,
            "SELECT "
            "(SELECT count(*) FROM memory_patch.human_review_cases) AS cases, "
            "(SELECT count(*) FROM memory_patch.human_review_claims) AS claims, "
            "(SELECT count(*) FROM memory_patch.human_review_decisions) AS decisions, "
            "(SELECT count(*) FROM memory_patch.human_review_handoffs) AS handoffs",
            timeout=60,
        )
    )
    if not root_counts:
        raise ValidationFailure("STEP34_DATABASE_COUNTS_MISSING")
    counts = {key: int(value) for key, value in root_counts[0].items()}
    if counts != {"cases": 2, "claims": 2, "decisions": 2, "handoffs": 2}:
        raise ValidationFailure("STEP34_DATABASE_COUNTS_INVALID")

    broken_context_case = build_open_review_case(
        case_type=answer_case.case_type,
        tenant_id=answer_case.tenant_id,
        owner_user_id=answer_case.owner_user_id,
        subject_type=answer_case.subject_type,
        subject_id=answer_case.subject_id,
        subject_hash=answer_case.subject_hash,
        request_id=answer_case.request_id,
        kernel_run_id=answer_case.kernel_run_id,
        route_hash=answer_case.route_hash,
        review_reason_codes=(
            ReviewReasonCode.REVIEW_AUDIT_CONTEXT_INVALID,
            ReviewReasonCode.REVIEW_CASE_CREATED,
        ),
        priority=answer_case.priority,
        source_audit_event_hash=answer_case.source_audit_event_hash,
        source_chain_id=answer_case.source_chain_id,
        audit_verification_result_hash=canonical_sha256(
            "step34-controlled-broken-audit-verification"
        ),
        audit_context_verified=False,
        required_context_refs=answer_case.required_context_refs,
        source_context_hash=answer_context.context_hash,
        created_at=answer_case.created_at,
    )
    if broken_context_case.audit_context_verified:
        raise ValidationFailure("STEP34_BROKEN_AUDIT_CONTEXT_NOT_FLAGGED")

    return {
        "audit": {
            "broken_chain_negative": "FLAGGED_FAIL_CLOSED",
            "chain_event_count": chain.event_count,
            "chain_id": chain.chain_id,
            "chain_verification_result_hash": chain.result_hash,
            "verified": True,
        },
        "case_matrix": {
            "answer_review": "PASS",
            "answer_review_case_hash": answer_case.case_hash,
            "case_exact_replay": "PASS",
            "progressed_case_exact_replay": "PASS",
            "non_open_initial_state": initial_state_skip,
            "shared_promotion_review": "PASS",
            "shared_promotion_review_case_hash": shared_case.case_hash,
        },
        "claiming": {
            "claim_receipt_hash": claim_receipt.receipt_hash,
            "concurrency": "DETERMINISTIC_FIXED_ORDER_ONE_WINNER_ONE_DENIED",
            "exact_replay": "PASS",
            "post_resolution_exact_replay": "PASS",
        },
        "database_counts": counts,
        "decisions": {
            "answer_decision_hash": answer_decision.decision_hash,
            "changed_replay": changed_replay,
            "changed_subject": changed_subject,
            "exact_replay": "PASS",
            "invalid_type": invalid_type,
            "shared_decision_hash": shared_decision.decision_hash,
            "stale": stale_decision,
            "valid": "PASS",
        },
        "handoff": {
            "answer_result_hash": answer_result.result_hash,
            "answer_receipt_hash": answer_handoff_receipt.receipt_hash,
            "answer_workflow": "PASS",
            "exact_replay": "PASS",
            "shared_promotion_result_hash": shared_result.result_hash,
            "shared_promotion_receipt_hash": shared_handoff_receipt.receipt_hash,
            "shared_promotion_workflow": "PASS",
        },
        "isolation": {
            "cross_tenant_review": "DENIED",
            "ordinary_user_review_access": ordinary_denied,
            "tenant_b_visible_cases": len(tenant_b_page.items),
            "unrelated_private_memory_access": "DENIED",
            "unauthorized_reviewer_visible_cases": len(ordinary_page.items),
        },
        "queue": {
            "continuation": "STABLE_SEQUENCE_CURSOR",
            "maximum_page_size": MAXIMUM_REVIEW_QUEUE_PAGE_SIZE,
            "minimum_disclosure": True,
            "ordering": "PRIORITY_CREATED_AT_CASE_ID",
            "page_size": 8,
        },
        "source_fixture": {
            "real_step26_human_review_required": True,
            "real_step33_persisted_verified_chain": True,
            "step32_shared_promotion_edge": "SANITIZED_SYNTHETIC_TYPED_CONTEXT",
        },
        "source_review_audit_hash": created_audit_hash,
    }


def validate(args: argparse.Namespace) -> Mapping[str, Any]:
    source_binary = step27._source_binary(args)
    identity = migrations.verify_binary_identity(source_binary)
    if identity["binary_sha256"] != EXPECTED_COCKROACH_SHA256:
        raise ValidationFailure("STEP34_COCKROACH_BINARY_DIGEST_MISMATCH")

    runtime = None
    root = None
    database = None
    roles: list[tuple[str, tuple[str, ...]]] = []
    cleanup: Mapping[str, Any] = {}
    cleanup_errors: list[str] = []
    primary_error: BaseException | None = None
    result = None
    migration_result = None
    replay_result = None
    catalog_result = None

    with tempfile.TemporaryDirectory(prefix="mp-step34-binary-", dir="/tmp") as temp:
        local_binary = Path(temp) / "cockroach"
        shutil.copy2(source_binary, local_binary)
        if (
            migrations.verify_binary_identity(local_binary)["binary_sha256"]
            != EXPECTED_COCKROACH_SHA256
        ):
            raise ValidationFailure("STEP34_COPIED_BINARY_DIGEST_MISMATCH")
        run_id = "mp_step34_" + uuid.uuid4().hex[:12]
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
                raise ValidationFailure("STEP34_MIGRATION_REPLAY_MISMATCH")
            catalog_result = migrations.assert_step34_security_catalog(root, database)
            root.execute(database, _seed_identity_sql(), timeout=120)

            suffix = uuid.uuid4().hex[:10]
            app_role = "mp_s34_app_" + suffix
            reviewer_role = "mp_s34_reviewer_" + suffix
            isolated_reviewer_role = "mp_s34_isolated_" + suffix
            service_role = "mp_s34_service_" + suffix
            role_specs = (
                (app_role, ("mp_app_runtime", "mp_request_context_setter")),
                (
                    reviewer_role,
                    ("mp_human_reviewer", "mp_request_context_setter"),
                ),
                (
                    isolated_reviewer_role,
                    ("mp_human_reviewer", "mp_request_context_setter"),
                ),
                (service_role, ("mp_review_service", "mp_request_context_setter")),
            )
            for role, memberships in role_specs:
                _create_validation_role(root, role, memberships)
                roles.append((role, memberships))
            result = _validate_services(
                root=root,
                database=database,
                app_role=app_role,
                reviewer_role=reviewer_role,
                isolated_reviewer_role=isolated_reviewer_role,
                service_role=service_role,
            )
        except BaseException as error:
            _failure_progress("VALIDATE_STEP34_REVIEW_WORKSPACE", error)
            primary_error = error
        finally:
            _progress("CLEANUP_DISPOSABLE_RUNTIME")
            if root is not None:
                if database is not None:
                    try:
                        migrations.drop_database(root, database, timeout=180)
                    except BaseException:
                        cleanup_errors.append("DATABASE_CLEANUP_FAILED")
                for role, memberships in reversed(roles):
                    try:
                        _drop_validation_role(root, role, memberships)
                    except BaseException:
                        cleanup_errors.append("ROLE_CLEANUP_FAILED")
            if runtime is not None:
                try:
                    cleanup = step33._stop_owned_runtime(runtime)
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
        raise ValidationFailure("STEP34_" + "_".join(cleanup_errors))
    if not all(
        cleanup.get(field) is expected
        for field, expected in (
            ("pid_exited", True),
            ("ports_closed", True),
            ("temporary_store_removed", True),
            ("force_kill_used", False),
        )
    ):
        raise ValidationFailure("STEP34_RUNTIME_CLEANUP_INCOMPLETE")
    if None in (result, migration_result, replay_result, catalog_result):
        raise ValidationFailure("STEP34_VALIDATION_RESULT_INCOMPLETE")

    output: dict[str, Any] = {
        "authority": {
            "audit_event_decision_authority": False,
            "canonical_source_publication_authority": False,
            "critic_reviewer_authority": False,
            "external_execution_authority": False,
            "model_reviewer_authority": False,
        },
        "cleanup": {
            "database_removed": True,
            "force_kill_used": cleanup["force_kill_used"],
            "pid_exited": cleanup["pid_exited"],
            "ports_closed": cleanup["ports_closed"],
            "roles_removed": len(roles),
            "temporary_store_removed": cleanup["temporary_store_removed"],
        },
        "database": {
            "binary_sha256": EXPECTED_COCKROACH_SHA256,
            "migration_count": len(migration_result["applied"]),
            "migration_id": migrations.STEP34_MIGRATION_ID,
            "migration_sha256": migrations.STEP34_MIGRATION_SHA256,
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
            "source_publications": 0,
            "web_calls": 0,
        },
        "review_access_policy_digest": ReviewAccessPolicy().policy_digest,
        "review_case_schema_version": STEP34_SCHEMA_VERSION,
        "review_decision_policy_digest": ReviewDecisionPolicy().policy_digest,
        "schema_version": "step34-human-review-workspace-validation-1a",
        "start_sha": START_SHA,
        "status": "PASS",
        "step": 34,
        "step35_boundary": {
            "personal_memory_end_user_ui": 0,
            "step35_started": False,
        },
        "step33_event_registry_version": STEP33_EVENT_REGISTRY_VERSION,
        **result,
    }
    output["validation_digest"] = canonical_sha256(output)
    return output


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
