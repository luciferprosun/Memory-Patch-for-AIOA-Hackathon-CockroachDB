"""Step 34 intake, queue, claim, decision, and typed handoff services."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from aioa_memory_kernel.contracts.serialization import canonical_sha256, ensure_utc
from aioa_memory_kernel.persistence import (
    AccessMode,
    RequestContext,
    SerializableTransactionRunner,
)
from aioa_memory_kernel.persistence.protocols import TransactionProtocol

from .audit import (
    review_case_claimed_event,
    review_case_created_event,
    review_case_terminal_event,
    review_decision_recorded_event,
    review_handoff_event,
)
from .models import (
    MAXIMUM_REVIEW_AUDIT_REFERENCES,
    STEP34_SCHEMA_VERSION,
    ClaimReviewCaseRequest,
    HumanReviewCase,
    HumanReviewDecision,
    HumanReviewDecisionReceipt,
    HumanReviewDetailProjection,
    HumanReviewWorkspaceError,
    REVIEW_PRIORITY_RANK,
    ReviewAccessPolicy,
    ReviewBusinessHandoffResult,
    ReviewCaseClaimReceipt,
    ReviewDecisionHandoffReceipt,
    ReviewDecisionHandoffRequest,
    ReviewDecisionType,
    ReviewQueueCursor,
    ReviewQueueItem,
    ReviewQueuePage,
    ReviewQueueRequest,
    ReviewReasonCode,
    ReviewSourceContext,
    ReviewState,
    ReviewerAuthorization,
    ReviewerPrincipal,
    SubmitReviewDecision,
    build_review_decision,
    build_review_handoff_result,
    transition_review_case,
    verify_claim_review_case_request,
    verify_human_review_case,
    verify_human_review_decision,
    verify_human_review_decision_receipt,
    verify_reviewer_authorization,
    verify_reviewer_principal,
    verify_review_handoff_request,
    verify_review_case_source_binding,
    verify_review_source_context,
    verify_submit_review_decision,
)
from .repository import HumanReviewCockroachRepository


STEP34_REVIEW_SERVICE_ACTOR_ID = "human-review-handoff-service-1a"


class Step34TrustedClock(Protocol):
    def now(self) -> datetime: ...


def _now(clock: Step34TrustedClock) -> datetime:
    if not hasattr(clock, "now") or not callable(clock.now):
        raise TypeError("a Step 34 trusted clock is required")
    return ensure_utc(clock.now(), "Step 34 trusted clock value")


def _context(tenant_id: str, user_id: str) -> RequestContext:
    return RequestContext(
        tenant_id=tenant_id,
        user_id=user_id,
        access_mode=AccessMode.USER_PRIVATE,
    )


def _require_principal(
    principal: ReviewerPrincipal,
    *,
    tenant_id: str,
    reviewer_id: str,
    reviewer_role,
    principal_hash: str,
) -> None:
    verify_reviewer_principal(principal)
    if (
        principal.tenant_id != tenant_id
        or principal.reviewer_id != reviewer_id
        or principal.reviewer_role is not reviewer_role
        or principal.principal_hash != principal_hash
    ):
        raise HumanReviewWorkspaceError(ReviewReasonCode.REVIEW_ACCESS_DENIED)


def _require_authorization(
    authorization: ReviewerAuthorization | None,
    principal: ReviewerPrincipal,
    case: HumanReviewCase,
) -> ReviewerAuthorization:
    if authorization is None:
        raise HumanReviewWorkspaceError(ReviewReasonCode.REVIEW_ACCESS_DENIED)
    verify_reviewer_authorization(authorization)
    if (
        authorization.tenant_id != case.tenant_id
        or authorization.reviewer_id != principal.reviewer_id
        or authorization.reviewer_role is not principal.reviewer_role
        or authorization.case_type is not case.case_type
        or (
            authorization.owner_user_id is not None
            and authorization.owner_user_id != case.owner_user_id
        )
    ):
        raise HumanReviewWorkspaceError(ReviewReasonCode.REVIEW_OWNER_SCOPE_DENIED)
    return authorization


def _queue_item(case: HumanReviewCase) -> ReviewQueueItem:
    summary = f"{case.case_type.value} review case"
    return ReviewQueueItem(
        review_case_id=case.review_case_id,
        case_type=case.case_type,
        subject_type=case.subject_type,
        priority=case.priority,
        review_state=case.review_state,
        review_state_version=case.review_state_version,
        owner_scope_digest=canonical_sha256(
            {
                "tenant_id": case.tenant_id,
                "owner_user_id": case.owner_user_id,
            }
        ),
        safe_summary=summary,
        safe_summary_digest=canonical_sha256(summary),
        created_at=case.created_at,
        case_hash=case.case_hash,
    )


def _detail(
    case: HumanReviewCase,
    context: ReviewSourceContext,
    audit_hashes: tuple[str, ...],
) -> HumanReviewDetailProjection:
    return HumanReviewDetailProjection(
        schema_version=STEP34_SCHEMA_VERSION,
        review_case=case,
        source_context=context,
        audit_event_hashes=audit_hashes,
        audit_context_verified=case.audit_context_verified,
        audit_verification_result_hash=case.audit_verification_result_hash,
        minimum_disclosure=True,
        render_untrusted_text_as_text=True,
        auto_open_links=False,
        contains_unrelated_private_data=False,
    )


class ReviewCaseIntakeService:
    """Trusted typed intake; ordinary users have no review-table grant."""

    def __init__(
        self,
        transaction_runner: SerializableTransactionRunner,
        *,
        repository: HumanReviewCockroachRepository | None = None,
        trusted_clock: Step34TrustedClock,
    ) -> None:
        if not isinstance(transaction_runner, SerializableTransactionRunner):
            raise TypeError("transaction_runner must be SerializableTransactionRunner")
        self._runner = transaction_runner
        self._repository = repository or HumanReviewCockroachRepository()
        self._clock = trusted_clock
        self._access_policy = ReviewAccessPolicy()

    def create_case(
        self,
        case: HumanReviewCase,
        source_context: ReviewSourceContext,
        *,
        authenticated_tenant_id: str,
        authenticated_owner_user_id: str,
    ) -> tuple[HumanReviewCase, bool, str]:
        verify_review_case_source_binding(case, source_context)
        if (
            case.review_state is not ReviewState.OPEN
            or case.review_state_version != 1
            or case.claimed_reviewer_id is not None
            or case.claimed_reviewer_role is not None
        ):
            raise HumanReviewWorkspaceError(ReviewReasonCode.REVIEW_CASE_STALE)
        if (
            authenticated_tenant_id != case.tenant_id
            or authenticated_owner_user_id != case.owner_user_id
        ):
            raise HumanReviewWorkspaceError(ReviewReasonCode.REVIEW_TENANT_DENIED)
        recorded_at = _now(self._clock)
        if recorded_at < case.created_at:
            raise HumanReviewWorkspaceError(ReviewReasonCode.REVIEW_CASE_STALE)

        def work(transaction: TransactionProtocol):
            audit_draft = review_case_created_event(
                case,
                access_policy_digest=self._access_policy.policy_digest,
                recorded_at=recorded_at,
            )
            audit_entry, _ = self._repository.append_audit(transaction, audit_draft)
            replay = self._repository.insert_case(transaction, case, source_context)
            stored = case
            if replay:
                stored = self._repository.get_case(
                    transaction,
                    tenant_id=case.tenant_id,
                    review_case_id=case.review_case_id,
                    for_update=False,
                )
                stored_context = self._repository.get_source_context(
                    transaction,
                    tenant_id=case.tenant_id,
                    review_case_id=case.review_case_id,
                )
                if stored is None or stored_context is None:
                    raise HumanReviewWorkspaceError(
                        ReviewReasonCode.REVIEW_CASE_STALE
                    )
                verify_review_case_source_binding(stored, stored_context)
                if stored.trigger_hash != case.trigger_hash:
                    raise HumanReviewWorkspaceError(
                        ReviewReasonCode.REVIEW_CASE_DUPLICATE
                    )
            return stored, replay, audit_entry.envelope.event_hash

        return self._runner.run(
            _context(case.tenant_id, STEP34_REVIEW_SERVICE_ACTOR_ID),
            work,
            operation_kind="STEP34_REVIEW_CASE_INTAKE",
        )


class HumanReviewWorkspaceService:
    """Least-privileged reviewer queue, claim, detail, and decision boundary."""

    def __init__(
        self,
        transaction_runner: SerializableTransactionRunner,
        *,
        repository: HumanReviewCockroachRepository | None = None,
        trusted_clock: Step34TrustedClock,
    ) -> None:
        if not isinstance(transaction_runner, SerializableTransactionRunner):
            raise TypeError("transaction_runner must be SerializableTransactionRunner")
        self._runner = transaction_runner
        self._repository = repository or HumanReviewCockroachRepository()
        self._clock = trusted_clock
        self._access_policy = ReviewAccessPolicy()

    def _authorization(
        self,
        transaction: TransactionProtocol,
        principal: ReviewerPrincipal,
        case: HumanReviewCase,
    ) -> ReviewerAuthorization:
        return _require_authorization(
            self._repository.get_authorization(
                transaction,
                tenant_id=case.tenant_id,
                reviewer_id=principal.reviewer_id,
                reviewer_role=principal.reviewer_role,
                case_type=case.case_type,
                owner_user_id=case.owner_user_id,
            ),
            principal,
            case,
        )

    def list_queue(
        self,
        request: ReviewQueueRequest,
        principal: ReviewerPrincipal,
    ) -> ReviewQueuePage:
        _require_principal(
            principal,
            tenant_id=request.tenant_id,
            reviewer_id=request.reviewer_id,
            reviewer_role=request.reviewer_role,
            principal_hash=request.reviewer_principal_hash,
        )

        def work(transaction: TransactionProtocol):
            loaded = self._repository.list_cases(
                transaction,
                tenant_id=request.tenant_id,
                reviewer_id=principal.reviewer_id,
                case_types=request.case_types,
                page_size=request.page_size,
                continuation=request.continuation,
            )
            for case in loaded:
                self._authorization(transaction, principal, case)
            truncated = len(loaded) > request.page_size
            visible = loaded[: request.page_size]
            items = tuple(_queue_item(case) for case in visible)
            continuation = None
            if truncated and items:
                last = items[-1]
                continuation = ReviewQueueCursor(
                    filter_digest=request.filter_digest,
                    priority_rank=REVIEW_PRIORITY_RANK[last.priority],
                    created_at=last.created_at,
                    review_case_id=last.review_case_id,
                )
            return ReviewQueuePage(
                request_hash=request.request_hash,
                reviewer_principal_hash=principal.principal_hash,
                items=items,
                truncated=truncated,
                continuation=continuation,
            )

        return self._runner.run(
            _context(principal.tenant_id, principal.reviewer_id),
            work,
            operation_kind="STEP34_REVIEW_QUEUE",
        )

    def get_detail(
        self,
        *,
        tenant_id: str,
        review_case_id: str,
        principal: ReviewerPrincipal,
    ) -> HumanReviewDetailProjection:
        verify_reviewer_principal(principal)
        if principal.tenant_id != tenant_id:
            raise HumanReviewWorkspaceError(ReviewReasonCode.REVIEW_TENANT_DENIED)

        def work(transaction: TransactionProtocol):
            case = self._repository.get_case(
                transaction,
                tenant_id=tenant_id,
                review_case_id=review_case_id,
                for_update=False,
            )
            if case is None:
                raise HumanReviewWorkspaceError(ReviewReasonCode.REVIEW_ACCESS_DENIED)
            self._authorization(transaction, principal, case)
            context = self._repository.get_source_context(
                transaction,
                tenant_id=tenant_id,
                review_case_id=review_case_id,
            )
            if context is None:
                raise HumanReviewWorkspaceError(ReviewReasonCode.REVIEW_CASE_STALE)
            audit_hashes = self._repository.load_review_audit_hashes(
                transaction,
                tenant_id=tenant_id,
                review_case_id=review_case_id,
                maximum_events=MAXIMUM_REVIEW_AUDIT_REFERENCES,
            )
            return _detail(case, context, audit_hashes)

        return self._runner.run(
            _context(principal.tenant_id, principal.reviewer_id),
            work,
            operation_kind="STEP34_REVIEW_DETAIL",
        )

    def claim_case(
        self,
        request: ClaimReviewCaseRequest,
        principal: ReviewerPrincipal,
    ) -> tuple[HumanReviewCase, ReviewCaseClaimReceipt, bool]:
        verify_claim_review_case_request(request)
        _require_principal(
            principal,
            tenant_id=request.tenant_id,
            reviewer_id=request.reviewer_id,
            reviewer_role=request.reviewer_role,
            principal_hash=request.reviewer_principal_hash,
        )
        trusted_now = _now(self._clock)
        if trusted_now < request.requested_at:
            raise HumanReviewWorkspaceError(ReviewReasonCode.REVIEW_CASE_STALE)

        def work(transaction: TransactionProtocol):
            replay = self._repository.get_claim_replay(
                transaction,
                tenant_id=request.tenant_id,
                reviewer_id=request.reviewer_id,
                replay_identity=request.replay_identity,
            )
            if replay is not None:
                if replay.request_hash != request.request_hash:
                    raise HumanReviewWorkspaceError(
                        ReviewReasonCode.REVIEW_CASE_CLAIM_CONFLICT
                    )
                case = self._repository.get_case(
                    transaction,
                    tenant_id=request.tenant_id,
                    review_case_id=request.review_case_id,
                    for_update=False,
                )
                if (
                    case is None
                    or replay.review_case_id != request.review_case_id
                    or case.claimed_reviewer_id != replay.reviewer_id
                    or case.claimed_reviewer_role is not replay.reviewer_role
                    or case.review_state_version < 2
                    or (
                        case.review_state is ReviewState.CLAIMED
                        and case.case_hash != replay.claimed_case_hash
                    )
                ):
                    raise HumanReviewWorkspaceError(ReviewReasonCode.REVIEW_CASE_STALE)
                self._authorization(transaction, principal, case)
                return case, replay, True
            case = self._repository.get_case(
                transaction,
                tenant_id=request.tenant_id,
                review_case_id=request.review_case_id,
                for_update=True,
            )
            if case is None:
                raise HumanReviewWorkspaceError(ReviewReasonCode.REVIEW_ACCESS_DENIED)
            self._authorization(transaction, principal, case)
            if (
                case.case_hash != request.review_case_hash
                or case.review_state is not request.expected_state
                or case.review_state_version != request.expected_state_version
            ):
                raise HumanReviewWorkspaceError(
                    ReviewReasonCode.REVIEW_CASE_ALREADY_CLAIMED
                )
            claimed = transition_review_case(
                case,
                state=ReviewState.CLAIMED,
                updated_at=trusted_now,
                reviewer_id=principal.reviewer_id,
                reviewer_role=principal.reviewer_role,
            )
            audit_entry, _ = self._repository.append_audit(
                transaction,
                review_case_claimed_event(
                    claimed,
                    principal,
                    request,
                    access_policy_digest=self._access_policy.policy_digest,
                    recorded_at=trusted_now,
                ),
            )
            receipt = ReviewCaseClaimReceipt(
                schema_version=STEP34_SCHEMA_VERSION,
                claim_id="human-review-claim-"
                + canonical_sha256(
                    {
                        "replay_identity": request.replay_identity,
                        "request_hash": request.request_hash,
                    }
                ),
                request_hash=request.request_hash,
                replay_identity=request.replay_identity,
                tenant_id=request.tenant_id,
                review_case_id=request.review_case_id,
                previous_case_hash=case.case_hash,
                claimed_case_hash=claimed.case_hash,
                reviewer_id=principal.reviewer_id,
                reviewer_role=principal.reviewer_role,
                claimed_at=trusted_now,
                audit_event_hash=audit_entry.envelope.event_hash,
                reason_code=ReviewReasonCode.REVIEW_CASE_CLAIMED,
            )
            self._repository.persist_claim_and_case(
                transaction,
                receipt=receipt,
                previous_case=case,
                claimed_case=claimed,
            )
            return claimed, receipt, False

        return self._runner.run(
            _context(principal.tenant_id, principal.reviewer_id),
            work,
            operation_kind="STEP34_REVIEW_CLAIM",
        )

    def record_decision(
        self,
        command: SubmitReviewDecision,
        principal: ReviewerPrincipal,
    ) -> tuple[
        HumanReviewCase,
        HumanReviewDecision,
        HumanReviewDecisionReceipt,
        bool,
    ]:
        verify_submit_review_decision(command)
        _require_principal(
            principal,
            tenant_id=command.tenant_id,
            reviewer_id=command.reviewer_id,
            reviewer_role=command.reviewer_role,
            principal_hash=command.reviewer_principal_hash,
        )
        trusted_now = _now(self._clock)
        if trusted_now < command.decided_at:
            raise HumanReviewWorkspaceError(ReviewReasonCode.REVIEW_DECISION_STALE)

        def work(transaction: TransactionProtocol):
            replay = self._repository.get_decision_replay(
                transaction,
                tenant_id=command.tenant_id,
                reviewer_id=command.reviewer_id,
                replay_identity=command.replay_identity,
            )
            if replay is not None:
                decision, receipt = replay
                if decision.command_hash != command.command_hash:
                    raise HumanReviewWorkspaceError(
                        ReviewReasonCode.REVIEW_DECISION_CONFLICT
                    )
                case = self._repository.get_case(
                    transaction,
                    tenant_id=command.tenant_id,
                    review_case_id=command.review_case_id,
                    for_update=False,
                )
                if case is None:
                    raise HumanReviewWorkspaceError(ReviewReasonCode.REVIEW_CASE_STALE)
                return case, decision, receipt, True
            case = self._repository.get_case(
                transaction,
                tenant_id=command.tenant_id,
                review_case_id=command.review_case_id,
                for_update=True,
            )
            if case is None:
                raise HumanReviewWorkspaceError(ReviewReasonCode.REVIEW_ACCESS_DENIED)
            self._authorization(transaction, principal, case)
            context = self._repository.get_source_context(
                transaction,
                tenant_id=case.tenant_id,
                review_case_id=case.review_case_id,
            )
            if context is None:
                raise HumanReviewWorkspaceError(ReviewReasonCode.REVIEW_CASE_STALE)
            audit_hashes = self._repository.load_review_audit_hashes(
                transaction,
                tenant_id=case.tenant_id,
                review_case_id=case.review_case_id,
                maximum_events=MAXIMUM_REVIEW_AUDIT_REFERENCES,
            )
            detail = _detail(case, context, audit_hashes)
            if command.context_digest != detail.detail_hash:
                raise HumanReviewWorkspaceError(ReviewReasonCode.REVIEW_DECISION_STALE)
            decision = build_review_decision(command, case)
            in_review = transition_review_case(
                case,
                state=ReviewState.IN_REVIEW,
                updated_at=trusted_now,
                reviewer_id=principal.reviewer_id,
                reviewer_role=principal.reviewer_role,
            )
            audit_entry, _ = self._repository.append_audit(
                transaction,
                review_decision_recorded_event(
                    decision,
                    in_review,
                    recorded_at=trusted_now,
                ),
            )
            receipt = HumanReviewDecisionReceipt(
                schema_version=STEP34_SCHEMA_VERSION,
                decision_id=decision.decision_id,
                decision_hash=decision.decision_hash,
                review_case_id=case.review_case_id,
                previous_case_hash=case.case_hash,
                in_review_case_hash=in_review.case_hash,
                reviewer_id=principal.reviewer_id,
                reviewer_role=principal.reviewer_role,
                subject_hash=case.subject_hash,
                decision_type=decision.decision_type,
                audit_event_hash=audit_entry.envelope.event_hash,
                handoff_result_hash=None,
                handoff_completed=False,
            )
            self._repository.persist_decision_and_case(
                transaction,
                decision=decision,
                receipt=receipt,
                previous_case=case,
                in_review_case=in_review,
            )
            return in_review, decision, receipt, False

        return self._runner.run(
            _context(principal.tenant_id, principal.reviewer_id),
            work,
            operation_kind="STEP34_REVIEW_DECISION",
        )


class ReviewDecisionHandoffService:
    """Typed receipt handoff; it cannot execute or publish the decision."""

    def __init__(
        self,
        transaction_runner: SerializableTransactionRunner,
        *,
        repository: HumanReviewCockroachRepository | None = None,
        trusted_clock: Step34TrustedClock,
    ) -> None:
        if not isinstance(transaction_runner, SerializableTransactionRunner):
            raise TypeError("transaction_runner must be SerializableTransactionRunner")
        self._runner = transaction_runner
        self._repository = repository or HumanReviewCockroachRepository()
        self._clock = trusted_clock

    def handoff(
        self,
        request: ReviewDecisionHandoffRequest,
        *,
        authenticated_service_id: str,
        current_subject_hash: str,
    ) -> tuple[
        HumanReviewCase,
        ReviewBusinessHandoffResult,
        ReviewDecisionHandoffReceipt,
        bool,
    ]:
        verify_review_handoff_request(request)
        if authenticated_service_id != STEP34_REVIEW_SERVICE_ACTOR_ID:
            raise HumanReviewWorkspaceError(ReviewReasonCode.REVIEW_ACCESS_DENIED)
        trusted_now = _now(self._clock)
        if trusted_now < request.requested_at:
            raise HumanReviewWorkspaceError(ReviewReasonCode.REVIEW_DECISION_STALE)

        def work(transaction: TransactionProtocol):
            replay = self._repository.get_handoff_replay(
                transaction,
                tenant_id=request.tenant_id,
                replay_identity=request.replay_identity,
            )
            if replay is not None:
                result, receipt = replay
                if receipt.request_hash != request.request_hash:
                    raise HumanReviewWorkspaceError(
                        ReviewReasonCode.REVIEW_DECISION_CONFLICT
                    )
                case = self._repository.get_case(
                    transaction,
                    tenant_id=request.tenant_id,
                    review_case_id=request.review_case_id,
                    for_update=False,
                )
                if case is None or case.case_hash != receipt.terminal_case_hash:
                    raise HumanReviewWorkspaceError(ReviewReasonCode.REVIEW_CASE_STALE)
                return case, result, receipt, True
            case = self._repository.get_case(
                transaction,
                tenant_id=request.tenant_id,
                review_case_id=request.review_case_id,
                for_update=True,
            )
            decision_pair = self._repository.get_decision_by_id(
                transaction,
                tenant_id=request.tenant_id,
                decision_id=request.decision_id,
            )
            if case is None or decision_pair is None:
                raise HumanReviewWorkspaceError(ReviewReasonCode.REVIEW_CASE_STALE)
            decision, decision_receipt = decision_pair
            verify_human_review_decision(decision)
            verify_human_review_decision_receipt(decision_receipt)
            if (
                case.review_state is not ReviewState.IN_REVIEW
                or case.review_state_version != 3
                or case.case_hash != request.in_review_case_hash
                or case.subject_hash != current_subject_hash
                or decision.decision_hash != request.decision_hash
                or decision_receipt.receipt_hash != request.decision_receipt_hash
                or decision_receipt.in_review_case_hash != case.case_hash
            ):
                raise HumanReviewWorkspaceError(ReviewReasonCode.REVIEW_DECISION_STALE)
            result = build_review_handoff_result(decision, case)
            terminal_state = (
                ReviewState.ESCALATED
                if decision.decision_type is ReviewDecisionType.ESCALATE
                else ReviewState.RESOLVED
            )
            terminal_case = transition_review_case(
                case,
                state=terminal_state,
                updated_at=trusted_now,
                reviewer_id=decision.reviewer_id,
                reviewer_role=decision.reviewer_role,
            )
            handoff_audit, _ = self._repository.append_audit(
                transaction,
                review_handoff_event(
                    result,
                    decision,
                    case,
                    recorded_at=trusted_now,
                ),
            )
            handoff_id = "review-decision-handoff-" + canonical_sha256(
                {
                    "replay_identity": request.replay_identity,
                    "request_hash": request.request_hash,
                }
            )
            receipt = ReviewDecisionHandoffReceipt(
                schema_version=STEP34_SCHEMA_VERSION,
                handoff_id=handoff_id,
                request_hash=request.request_hash,
                replay_identity=request.replay_identity,
                review_case_id=case.review_case_id,
                decision_id=decision.decision_id,
                decision_hash=decision.decision_hash,
                decision_receipt_hash=decision_receipt.receipt_hash,
                handoff_result_hash=result.result_hash,
                terminal_case_hash=terminal_case.case_hash,
                terminal_state=terminal_state,
                audit_event_hash=handoff_audit.envelope.event_hash,
                succeeded=True,
            )
            self._repository.append_audit(
                transaction,
                review_case_terminal_event(
                    terminal_case,
                    decision,
                    result,
                    recorded_at=trusted_now,
                ),
            )
            self._repository.persist_handoff_and_case(
                transaction,
                request_hash=request.request_hash,
                replay_identity=request.replay_identity,
                decision_receipt_hash=decision_receipt.receipt_hash,
                result=result,
                receipt=receipt,
                previous_case=case,
                terminal_case=terminal_case,
            )
            return terminal_case, result, receipt, False

        # The dedicated review-service principal is distinct from the owner.
        # Database policies bind its access to the exact case and tenant.
        return self._runner.run(
            _context(request.tenant_id, STEP34_REVIEW_SERVICE_ACTOR_ID),
            work,
            operation_kind="STEP34_REVIEW_HANDOFF",
        )


__all__ = [
    "HumanReviewWorkspaceService",
    "ReviewCaseIntakeService",
    "ReviewDecisionHandoffService",
    "STEP34_REVIEW_SERVICE_ACTOR_ID",
    "Step34TrustedClock",
]
