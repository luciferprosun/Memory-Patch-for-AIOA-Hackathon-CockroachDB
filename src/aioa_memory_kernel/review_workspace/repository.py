"""CockroachDB persistence for the Step 34 review workspace."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from aioa_memory_kernel.audit_ledger import (
    AuditEventDraft,
    AuditLedgerCockroachRepository,
    AuditLedgerEntry,
    build_audit_event_envelope,
    build_audit_ledger_entry,
    compute_audit_chain_id,
)
from aioa_memory_kernel.contracts.exceptions import IntegrityError
from aioa_memory_kernel.contracts.serialization import canonical_json
from aioa_memory_kernel.persistence.errors import (
    ImmutableRecordConflictError,
    PersistenceConfigurationError,
)
from aioa_memory_kernel.persistence.protocols import TransactionProtocol

from .models import (
    HumanReviewCase,
    HumanReviewDecision,
    HumanReviewDecisionReceipt,
    ReviewBusinessHandoffResult,
    ReviewCaseClaimReceipt,
    ReviewCaseType,
    ReviewDecisionHandoffReceipt,
    ReviewQueueCursor,
    ReviewSourceContext,
    ReviewState,
    ReviewerAuthorization,
    ReviewerRole,
    parse_human_review_case,
    parse_human_review_decision,
    parse_human_review_decision_receipt,
    parse_review_case_claim_receipt,
    parse_review_handoff_receipt,
    parse_review_handoff_result,
    parse_review_source_context,
    parse_reviewer_authorization,
    review_to_jsonb,
    verify_human_review_case,
    verify_human_review_decision,
    verify_human_review_decision_receipt,
    verify_review_case_claim_receipt,
    verify_review_handoff_receipt,
    verify_review_handoff_result,
    verify_review_case_source_binding,
    verify_review_source_context,
)


def _json_object(value: object, name: str) -> Mapping[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise PersistenceConfigurationError(
                f"{name} is invalid JSON",
                sanitized_code="INVALID_STEP34_REVIEW_ROW",
            ) from exc
    if not isinstance(value, Mapping):
        raise PersistenceConfigurationError(
            f"{name} is not an object",
            sanitized_code="INVALID_STEP34_REVIEW_ROW",
        )
    return value


def _timestamp(value: object, name: str) -> datetime:
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str):
        try:
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise PersistenceConfigurationError(
                f"{name} is invalid",
                sanitized_code="INVALID_STEP34_REVIEW_ROW",
            ) from exc
    else:
        raise PersistenceConfigurationError(
            f"{name} is invalid",
            sanitized_code="INVALID_STEP34_REVIEW_ROW",
        )
    if result.tzinfo is None:
        raise PersistenceConfigurationError(
            f"{name} is not timezone aware",
            sanitized_code="INVALID_STEP34_REVIEW_ROW",
        )
    return result.astimezone(UTC)


def _integer(value: object, name: str) -> int:
    if isinstance(value, bool):
        raise PersistenceConfigurationError(
            f"{name} is invalid", sanitized_code="INVALID_STEP34_REVIEW_ROW"
        )
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise PersistenceConfigurationError(
            f"{name} is invalid", sanitized_code="INVALID_STEP34_REVIEW_ROW"
        ) from exc


def _case_from_row(row: Mapping[str, object]) -> HumanReviewCase:
    case = parse_human_review_case(_json_object(row["case_payload"], "case_payload"))
    if (
        row.get("tenant_id") != case.tenant_id
        or row.get("review_case_id") != case.review_case_id
        or row.get("owner_user_id") != case.owner_user_id
        or row.get("case_type") != case.case_type.value
        or row.get("subject_hash") != case.subject_hash
        or row.get("case_hash") != case.case_hash
        or row.get("review_state") != case.review_state.value
        or _integer(row.get("review_state_version"), "review_state_version")
        != case.review_state_version
        or row.get("claimed_reviewer_id") != case.claimed_reviewer_id
        or row.get("claimed_reviewer_role")
        != (
            None
            if case.claimed_reviewer_role is None
            else case.claimed_reviewer_role.value
        )
    ):
        raise IntegrityError("persisted review case relational projection differs")
    return case


class HumanReviewCockroachRepository:
    """Exact Step 34 persistence with no arbitrary business mutation methods."""

    def __init__(
        self,
        *,
        audit_repository: AuditLedgerCockroachRepository | None = None,
    ) -> None:
        self._audit = audit_repository or AuditLedgerCockroachRepository()

    def append_audit(
        self,
        transaction: TransactionProtocol,
        draft: AuditEventDraft,
    ) -> tuple[AuditLedgerEntry, bool]:
        chain_id = compute_audit_chain_id(draft.tenant_id, draft.owner_user_id)
        replay = self._audit.get_replay(
            transaction,
            tenant_id=draft.tenant_id,
            chain_id=chain_id,
            idempotency_key=draft.idempotency_key,
        )
        if replay is not None:
            if replay.envelope.draft_hash != draft.draft_hash:
                raise ImmutableRecordConflictError(
                    "review audit replay differs",
                    sanitized_code="STEP34_AUDIT_REPLAY_CONFLICT",
                )
            return replay, True
        head = self._audit.lock_chain_head(
            transaction,
            tenant_id=draft.tenant_id,
            owner_user_id=draft.owner_user_id,
            updated_at=draft.recorded_at,
        )
        replay = self._audit.get_replay(
            transaction,
            tenant_id=draft.tenant_id,
            chain_id=chain_id,
            idempotency_key=draft.idempotency_key,
        )
        if replay is not None:
            if replay.envelope.draft_hash != draft.draft_hash:
                raise ImmutableRecordConflictError(
                    "review audit replay differs",
                    sanitized_code="STEP34_AUDIT_REPLAY_CONFLICT",
                )
            return replay, True
        event = build_audit_event_envelope(
            draft,
            sequence_number=head.last_sequence + 1,
            previous_event_hash=head.last_event_hash,
        )
        entry = build_audit_ledger_entry(event, draft.event_payload)
        self._audit.insert_entry(transaction, entry)
        self._audit.advance_chain_head(transaction, current=head, entry=entry)
        return entry, False

    @staticmethod
    def get_authorization(
        transaction: TransactionProtocol,
        *,
        tenant_id: str,
        reviewer_id: str,
        reviewer_role: ReviewerRole,
        case_type: ReviewCaseType,
        owner_user_id: str,
    ) -> ReviewerAuthorization | None:
        row = transaction.fetch_one(
            """
            SELECT authorization_payload
              FROM memory_patch.reviewer_authorizations
             WHERE tenant_id = %s AND reviewer_id = %s
               AND reviewer_role = %s AND case_type = %s
               AND active = true
               AND (owner_user_id IS NULL OR owner_user_id = %s)
             ORDER BY owner_user_id NULLS LAST
             LIMIT 1
            """,
            (
                tenant_id,
                reviewer_id,
                reviewer_role.value,
                case_type.value,
                owner_user_id,
            ),
        )
        if row is None:
            return None
        return parse_reviewer_authorization(
            _json_object(row["authorization_payload"], "authorization_payload")
        )

    @staticmethod
    def insert_case(
        transaction: TransactionProtocol,
        case: HumanReviewCase,
        context: ReviewSourceContext,
    ) -> bool:
        verify_review_case_source_binding(case, context)
        row = transaction.fetch_one(
            """
            INSERT INTO memory_patch.human_review_cases (
              tenant_id, review_case_id, trigger_hash, case_type,
              owner_user_id, subject_type, subject_id, subject_hash,
              source_audit_event_hash, source_chain_id,
              audit_verification_result_hash, audit_context_verified,
              source_context_hash, review_state, review_state_version,
              priority, claimed_reviewer_id, claimed_reviewer_role,
              case_hash, case_payload, source_context_payload,
              created_at, updated_at
            ) VALUES (
              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::JSONB,
              %s::JSONB, %s, %s
            ) ON CONFLICT DO NOTHING RETURNING review_case_id
            """,
            (
                case.tenant_id,
                case.review_case_id,
                case.trigger_hash,
                case.case_type.value,
                case.owner_user_id,
                case.subject_type.value,
                case.subject_id,
                case.subject_hash,
                case.source_audit_event_hash,
                case.source_chain_id,
                case.audit_verification_result_hash,
                case.audit_context_verified,
                case.source_context_hash,
                case.review_state.value,
                case.review_state_version,
                case.priority.value,
                case.claimed_reviewer_id,
                (
                    None
                    if case.claimed_reviewer_role is None
                    else case.claimed_reviewer_role.value
                ),
                case.case_hash,
                canonical_json(review_to_jsonb(case)),
                canonical_json(review_to_jsonb(context)),
                case.created_at,
                case.updated_at,
            ),
        )
        if row is not None:
            return False
        existing = HumanReviewCockroachRepository.get_case(
            transaction,
            tenant_id=case.tenant_id,
            review_case_id=case.review_case_id,
            for_update=False,
        )
        existing_context = HumanReviewCockroachRepository.get_source_context(
            transaction,
            tenant_id=case.tenant_id,
            review_case_id=case.review_case_id,
        )
        if (
            existing is None
            or existing_context is None
            or existing.trigger_hash != case.trigger_hash
            or existing_context.context_hash != context.context_hash
        ):
            raise ImmutableRecordConflictError(
                "review case identity conflicts",
                sanitized_code="STEP34_REVIEW_CASE_CONFLICT",
            )
        return True

    @staticmethod
    def get_case(
        transaction: TransactionProtocol,
        *,
        tenant_id: str,
        review_case_id: str,
        for_update: bool,
    ) -> HumanReviewCase | None:
        suffix = " FOR UPDATE" if for_update else ""
        row = transaction.fetch_one(
            """
            SELECT tenant_id, review_case_id, owner_user_id, case_type,
                   subject_hash, case_hash, review_state,
                   review_state_version, claimed_reviewer_id,
                   claimed_reviewer_role, case_payload
              FROM memory_patch.human_review_cases
             WHERE tenant_id = %s AND review_case_id = %s
            """ + suffix,
            (tenant_id, review_case_id),
        )
        return None if row is None else _case_from_row(row)

    @staticmethod
    def get_source_context(
        transaction: TransactionProtocol,
        *,
        tenant_id: str,
        review_case_id: str,
    ) -> ReviewSourceContext | None:
        row = transaction.fetch_one(
            """
            SELECT source_context_payload, source_context_hash
              FROM memory_patch.human_review_cases
             WHERE tenant_id = %s AND review_case_id = %s
            """,
            (tenant_id, review_case_id),
        )
        if row is None:
            return None
        context = parse_review_source_context(
            _json_object(row["source_context_payload"], "source_context_payload")
        )
        if row["source_context_hash"] != context.context_hash:
            raise IntegrityError("persisted source context relational hash differs")
        return context

    @staticmethod
    def load_review_audit_hashes(
        transaction: TransactionProtocol,
        *,
        tenant_id: str,
        review_case_id: str,
        maximum_events: int,
    ) -> tuple[str, ...]:
        rows = transaction.fetch_all(
            """
            SELECT event_hash
              FROM memory_patch.audit_events
             WHERE tenant_id = %s AND subject_type = 'REVIEW_CASE'
               AND subject_id = %s
               AND event_type IN (
                 'REVIEW_CASE_CREATED', 'REVIEW_CASE_CLAIMED',
                 'REVIEW_DECISION_RECORDED', 'REVIEW_HANDOFF_SUCCEEDED',
                 'REVIEW_HANDOFF_FAILED', 'REVIEW_CASE_RESOLVED',
                 'REVIEW_CASE_ESCALATED'
               )
             ORDER BY sequence_number
             LIMIT %s
            """,
            (tenant_id, review_case_id, maximum_events),
        )
        return tuple(str(row["event_hash"]) for row in rows)

    @staticmethod
    def list_cases(
        transaction: TransactionProtocol,
        *,
        tenant_id: str,
        reviewer_id: str,
        case_types: tuple[ReviewCaseType, ...],
        page_size: int,
        continuation: ReviewQueueCursor | None,
    ) -> tuple[HumanReviewCase, ...]:
        placeholders = ", ".join("%s" for _ in case_types)
        sql = f"""
            SELECT tenant_id, review_case_id, owner_user_id, case_type,
                   subject_hash, case_hash, review_state,
                   review_state_version, claimed_reviewer_id,
                   claimed_reviewer_role, case_payload
              FROM memory_patch.human_review_cases
             WHERE tenant_id = %s
               AND case_type IN ({placeholders})
               AND (
                 review_state = 'OPEN'
                 OR (review_state = 'CLAIMED' AND claimed_reviewer_id = %s)
               )
        """
        parameters: list[object] = [tenant_id, *(item.value for item in case_types), reviewer_id]
        if continuation is not None:
            sql += """
               AND (
                 CASE priority
                   WHEN 'CRITICAL' THEN 40 WHEN 'HIGH' THEN 30
                   WHEN 'NORMAL' THEN 20 ELSE 10 END < %s
                 OR (
                   CASE priority
                     WHEN 'CRITICAL' THEN 40 WHEN 'HIGH' THEN 30
                     WHEN 'NORMAL' THEN 20 ELSE 10 END = %s
                   AND created_at > %s
                 )
                 OR (
                   CASE priority
                     WHEN 'CRITICAL' THEN 40 WHEN 'HIGH' THEN 30
                     WHEN 'NORMAL' THEN 20 ELSE 10 END = %s
                   AND created_at = %s AND review_case_id > %s
                 )
               )
            """
            parameters.extend(
                (
                    continuation.priority_rank,
                    continuation.priority_rank,
                    continuation.created_at,
                    continuation.priority_rank,
                    continuation.created_at,
                    continuation.review_case_id,
                )
            )
        sql += """
             ORDER BY CASE priority
               WHEN 'CRITICAL' THEN 40 WHEN 'HIGH' THEN 30
               WHEN 'NORMAL' THEN 20 ELSE 10 END DESC,
               created_at, review_case_id
             LIMIT %s
        """
        parameters.append(page_size + 1)
        return tuple(
            _case_from_row(row)
            for row in transaction.fetch_all(sql, tuple(parameters))
        )

    @staticmethod
    def get_claim_replay(
        transaction: TransactionProtocol,
        *,
        tenant_id: str,
        reviewer_id: str,
        replay_identity: str,
    ) -> ReviewCaseClaimReceipt | None:
        row = transaction.fetch_one(
            """
            SELECT claim_payload
              FROM memory_patch.human_review_claims
             WHERE tenant_id = %s AND reviewer_id = %s
               AND replay_identity = %s
            """,
            (tenant_id, reviewer_id, replay_identity),
        )
        return None if row is None else parse_review_case_claim_receipt(
            _json_object(row["claim_payload"], "claim_payload")
        )

    @staticmethod
    def persist_claim_and_case(
        transaction: TransactionProtocol,
        *,
        receipt: ReviewCaseClaimReceipt,
        previous_case: HumanReviewCase,
        claimed_case: HumanReviewCase,
    ) -> None:
        verify_review_case_claim_receipt(receipt)
        verify_human_review_case(previous_case)
        verify_human_review_case(claimed_case)
        row = transaction.fetch_one(
            """
            INSERT INTO memory_patch.human_review_claims (
              tenant_id, claim_id, review_case_id, reviewer_id,
              reviewer_role, request_hash, replay_identity,
              previous_case_hash, claimed_case_hash, audit_event_hash,
              claim_receipt_hash, claim_payload, claimed_at
            ) VALUES (
              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
              %s, %s::JSONB, %s
            ) ON CONFLICT DO NOTHING RETURNING claim_id
            """,
            (
                receipt.tenant_id,
                receipt.claim_id,
                receipt.review_case_id,
                receipt.reviewer_id,
                receipt.reviewer_role.value,
                receipt.request_hash,
                receipt.replay_identity,
                receipt.previous_case_hash,
                receipt.claimed_case_hash,
                receipt.audit_event_hash,
                receipt.receipt_hash,
                canonical_json(review_to_jsonb(receipt)),
                receipt.claimed_at,
            ),
        )
        if row is None:
            replay = HumanReviewCockroachRepository.get_claim_replay(
                transaction,
                tenant_id=receipt.tenant_id,
                reviewer_id=receipt.reviewer_id,
                replay_identity=receipt.replay_identity,
            )
            if replay != receipt:
                raise ImmutableRecordConflictError(
                    "review claim replay conflicts",
                    sanitized_code="STEP34_REVIEW_CLAIM_CONFLICT",
                )
        HumanReviewCockroachRepository._update_case(
            transaction, previous_case=previous_case, new_case=claimed_case
        )

    @staticmethod
    def _update_case(
        transaction: TransactionProtocol,
        *,
        previous_case: HumanReviewCase,
        new_case: HumanReviewCase,
    ) -> None:
        row = transaction.fetch_one(
            """
            UPDATE memory_patch.human_review_cases
               SET review_state = %s, review_state_version = %s,
                   claimed_reviewer_id = %s, claimed_reviewer_role = %s,
                   case_hash = %s, case_payload = %s::JSONB, updated_at = %s
             WHERE tenant_id = %s AND review_case_id = %s
               AND review_state = %s AND review_state_version = %s
               AND case_hash = %s
            RETURNING review_case_id
            """,
            (
                new_case.review_state.value,
                new_case.review_state_version,
                new_case.claimed_reviewer_id,
                (
                    None
                    if new_case.claimed_reviewer_role is None
                    else new_case.claimed_reviewer_role.value
                ),
                new_case.case_hash,
                canonical_json(review_to_jsonb(new_case)),
                new_case.updated_at,
                previous_case.tenant_id,
                previous_case.review_case_id,
                previous_case.review_state.value,
                previous_case.review_state_version,
                previous_case.case_hash,
            ),
        )
        if row is None:
            current = HumanReviewCockroachRepository.get_case(
                transaction,
                tenant_id=new_case.tenant_id,
                review_case_id=new_case.review_case_id,
                for_update=False,
            )
            if current != new_case:
                raise ImmutableRecordConflictError(
                    "review case state changed concurrently",
                    sanitized_code="STEP34_REVIEW_STATE_CONFLICT",
                )

    @staticmethod
    def get_decision_replay(
        transaction: TransactionProtocol,
        *,
        tenant_id: str,
        reviewer_id: str,
        replay_identity: str,
    ) -> tuple[HumanReviewDecision, HumanReviewDecisionReceipt] | None:
        row = transaction.fetch_one(
            """
            SELECT decision_payload, decision_receipt_payload
              FROM memory_patch.human_review_decisions
             WHERE tenant_id = %s AND reviewer_id = %s
               AND replay_identity = %s
            """,
            (tenant_id, reviewer_id, replay_identity),
        )
        if row is None:
            return None
        return (
            parse_human_review_decision(
                _json_object(row["decision_payload"], "decision_payload")
            ),
            parse_human_review_decision_receipt(
                _json_object(
                    row["decision_receipt_payload"], "decision_receipt_payload"
                )
            ),
        )

    @staticmethod
    def get_decision_by_id(
        transaction: TransactionProtocol,
        *,
        tenant_id: str,
        decision_id: str,
    ) -> tuple[HumanReviewDecision, HumanReviewDecisionReceipt] | None:
        row = transaction.fetch_one(
            """
            SELECT decision_payload, decision_receipt_payload
              FROM memory_patch.human_review_decisions
             WHERE tenant_id = %s AND decision_id = %s
            """,
            (tenant_id, decision_id),
        )
        if row is None:
            return None
        return (
            parse_human_review_decision(
                _json_object(row["decision_payload"], "decision_payload")
            ),
            parse_human_review_decision_receipt(
                _json_object(
                    row["decision_receipt_payload"], "decision_receipt_payload"
                )
            ),
        )

    @staticmethod
    def persist_decision_and_case(
        transaction: TransactionProtocol,
        *,
        decision: HumanReviewDecision,
        receipt: HumanReviewDecisionReceipt,
        previous_case: HumanReviewCase,
        in_review_case: HumanReviewCase,
    ) -> None:
        verify_human_review_decision(decision)
        verify_human_review_decision_receipt(receipt)
        row = transaction.fetch_one(
            """
            INSERT INTO memory_patch.human_review_decisions (
              tenant_id, decision_id, review_case_id, reviewer_id,
              reviewer_role, case_type, decision_type, command_hash,
              replay_identity, previous_case_hash, in_review_case_hash,
              subject_hash, decision_policy_digest, decision_hash,
              audit_event_hash, decision_receipt_hash, decision_payload,
              decision_receipt_payload, decided_at
            ) VALUES (
              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
              %s, %s, %s, %s, %s, %s, %s::JSONB, %s::JSONB, %s
            ) ON CONFLICT DO NOTHING RETURNING decision_id
            """,
            (
                decision.tenant_id,
                decision.decision_id,
                decision.review_case_id,
                decision.reviewer_id,
                decision.reviewer_role.value,
                decision.case_type.value,
                decision.decision_type.value,
                decision.command_hash,
                decision.replay_identity,
                receipt.previous_case_hash,
                receipt.in_review_case_hash,
                decision.subject_hash,
                decision.decision_policy_digest,
                decision.decision_hash,
                receipt.audit_event_hash,
                receipt.receipt_hash,
                canonical_json(review_to_jsonb(decision)),
                canonical_json(review_to_jsonb(receipt)),
                decision.decided_at,
            ),
        )
        if row is None:
            replay = HumanReviewCockroachRepository.get_decision_replay(
                transaction,
                tenant_id=decision.tenant_id,
                reviewer_id=decision.reviewer_id,
                replay_identity=decision.replay_identity,
            )
            if replay != (decision, receipt):
                raise ImmutableRecordConflictError(
                    "review decision replay conflicts",
                    sanitized_code="STEP34_REVIEW_DECISION_CONFLICT",
                )
        HumanReviewCockroachRepository._update_case(
            transaction, previous_case=previous_case, new_case=in_review_case
        )

    @staticmethod
    def get_handoff_replay(
        transaction: TransactionProtocol,
        *,
        tenant_id: str,
        replay_identity: str,
    ) -> tuple[ReviewBusinessHandoffResult, ReviewDecisionHandoffReceipt] | None:
        row = transaction.fetch_one(
            """
            SELECT handoff_result_payload, handoff_receipt_payload
              FROM memory_patch.human_review_handoffs
             WHERE tenant_id = %s AND replay_identity = %s
            """,
            (tenant_id, replay_identity),
        )
        if row is None:
            return None
        return (
            parse_review_handoff_result(
                _json_object(row["handoff_result_payload"], "handoff_result_payload")
            ),
            parse_review_handoff_receipt(
                _json_object(row["handoff_receipt_payload"], "handoff_receipt_payload")
            ),
        )

    @staticmethod
    def persist_handoff_and_case(
        transaction: TransactionProtocol,
        *,
        request_hash: str,
        replay_identity: str,
        decision_receipt_hash: str,
        result: ReviewBusinessHandoffResult,
        receipt: ReviewDecisionHandoffReceipt,
        previous_case: HumanReviewCase,
        terminal_case: HumanReviewCase,
    ) -> None:
        verify_review_handoff_result(result)
        verify_review_handoff_receipt(receipt)
        row = transaction.fetch_one(
            """
            INSERT INTO memory_patch.human_review_handoffs (
              tenant_id, handoff_id, review_case_id, decision_id,
              request_hash, replay_identity, decision_hash,
              decision_receipt_hash, handoff_result_hash,
              terminal_case_hash, terminal_state, audit_event_hash,
              handoff_receipt_hash, handoff_result_payload,
              handoff_receipt_payload, completed_at
            ) VALUES (
              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
              %s, %s, %s, %s::JSONB, %s::JSONB, %s
            ) ON CONFLICT DO NOTHING RETURNING handoff_id
            """,
            (
                previous_case.tenant_id,
                receipt.handoff_id,
                receipt.review_case_id,
                receipt.decision_id,
                request_hash,
                replay_identity,
                receipt.decision_hash,
                decision_receipt_hash,
                result.result_hash,
                terminal_case.case_hash,
                terminal_case.review_state.value,
                receipt.audit_event_hash,
                receipt.receipt_hash,
                canonical_json(review_to_jsonb(result)),
                canonical_json(review_to_jsonb(receipt)),
                terminal_case.updated_at,
            ),
        )
        if row is None:
            replay = HumanReviewCockroachRepository.get_handoff_replay(
                transaction,
                tenant_id=previous_case.tenant_id,
                replay_identity=replay_identity,
            )
            if replay != (result, receipt):
                raise ImmutableRecordConflictError(
                    "review handoff replay conflicts",
                    sanitized_code="STEP34_REVIEW_HANDOFF_CONFLICT",
                )
        HumanReviewCockroachRepository._update_case(
            transaction, previous_case=previous_case, new_case=terminal_case
        )


__all__ = ["HumanReviewCockroachRepository"]
