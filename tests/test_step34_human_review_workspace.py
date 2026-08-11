"""Offline deterministic tests for the Step 34 human-review workspace."""

from __future__ import annotations

import inspect
import threading
import unittest
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta

from tests._support import REPOSITORY_ROOT

from aioa_memory_kernel.answers import (
    FinalOutputStatus,
    HumanReviewRequired,
    STEP26_SCHEMA_VERSION,
    Step26ReasonCode,
)
from aioa_memory_kernel.audit_ledger import (
    STEP33_GENESIS_SENTINEL,
    AuditChainVerificationResult,
    build_audit_event_envelope,
    build_audit_ledger_entry,
    compute_audit_chain_id,
)
from aioa_memory_kernel.audit_ledger.models import (
    STEP33_VERIFICATION_POLICY_DIGEST,
    STEP33_VERIFICATION_POLICY_ID,
    STEP33_VERIFICATION_POLICY_VERSION,
)
from aioa_memory_kernel.contracts.enums import EvidenceStatus
from aioa_memory_kernel.contracts.exceptions import (
    ContractValidationError,
    IntegrityError,
)
from aioa_memory_kernel.contracts.serialization import canonical_sha256
from aioa_memory_kernel.persistence import SerializableTransactionRunner
from aioa_memory_kernel.review_workspace import (
    MAXIMUM_REVIEW_QUEUE_PAGE_SIZE,
    STEP34_REVIEW_SERVICE_ACTOR_ID,
    STEP34_SCHEMA_VERSION,
    ClaimReviewCaseRequest,
    HumanReviewCase,
    HumanReviewWorkspaceError,
    HumanReviewWorkspaceService,
    ReviewCaseIntakeService,
    ReviewCaseType,
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
    parse_human_review_case,
    review_to_jsonb,
    transition_review_case,
    verify_human_review_case,
)
from aioa_memory_kernel.security.credentials import CredentialPurpose
from aioa_memory_kernel.routing import KnowledgePolicyDecision


NOW = datetime(2044, 5, 6, 7, 8, 9, tzinfo=UTC)
TENANT = "step34-tenant"
OWNER = "step34-owner"
REVIEWER = "step34-reviewer"
SUBJECT_HASH = canonical_sha256({"step": 34, "subject": "answer"})
SOURCE_EVENT_HASH = canonical_sha256({"step": 33, "event": "source"})


class FrozenClock:
    def __init__(self, value: datetime = NOW) -> None:
        self.value = value

    def now(self) -> datetime:
        return self.value


class MemoryRunner(SerializableTransactionRunner):
    def __init__(
        self,
        purpose: CredentialPurpose = CredentialPurpose.HUMAN_REVIEWER_DATABASE,
    ) -> None:
        super().__init__(
            lambda: None,
            credential_purpose=purpose,
        )
        self.lock = threading.RLock()
        self.contexts = []

    def run(self, context, callback, *, operation_kind=None):
        with self.lock:
            self.contexts.append((context, operation_kind))
            return callback(object())


class MemoryReviewRepository:
    """Behavioral repository double preserving Step 34 replay semantics."""

    def __init__(self) -> None:
        self.authorizations = {}
        self.cases = {}
        self.contexts = {}
        self.claims = {}
        self.decisions = {}
        self.decisions_by_id = {}
        self.handoffs = {}
        self.audit_entries = []

    def append_audit(self, transaction, draft):
        for entry in self.audit_entries:
            if (
                entry.envelope.tenant_id == draft.tenant_id
                and entry.envelope.idempotency_key == draft.idempotency_key
            ):
                if entry.envelope.draft_hash != draft.draft_hash:
                    raise IntegrityError("audit replay conflict")
                return entry, True
        chain = [
            item
            for item in self.audit_entries
            if item.envelope.chain_id
            == compute_audit_chain_id(draft.tenant_id, draft.owner_user_id)
        ]
        previous = (
            STEP33_GENESIS_SENTINEL
            if not chain
            else chain[-1].envelope.event_hash
        )
        envelope = build_audit_event_envelope(
            draft,
            sequence_number=len(chain) + 1,
            previous_event_hash=previous,
        )
        entry = build_audit_ledger_entry(envelope, draft.event_payload)
        self.audit_entries.append(entry)
        return entry, False

    def get_authorization(
        self,
        transaction,
        *,
        tenant_id,
        reviewer_id,
        reviewer_role,
        case_type,
        owner_user_id,
    ):
        exact = self.authorizations.get(
            (tenant_id, reviewer_id, reviewer_role, case_type, owner_user_id)
        )
        return exact or self.authorizations.get(
            (tenant_id, reviewer_id, reviewer_role, case_type, None)
        )

    def insert_case(self, transaction, case, context):
        existing = self.cases.get((case.tenant_id, case.review_case_id))
        if existing is not None:
            if existing.trigger_hash != case.trigger_hash:
                raise IntegrityError("case replay conflict")
            return True
        self.cases[(case.tenant_id, case.review_case_id)] = case
        self.contexts[(case.tenant_id, case.review_case_id)] = context
        return False

    def get_case(self, transaction, *, tenant_id, review_case_id, for_update):
        return self.cases.get((tenant_id, review_case_id))

    def get_source_context(self, transaction, *, tenant_id, review_case_id):
        return self.contexts.get((tenant_id, review_case_id))

    def load_review_audit_hashes(
        self, transaction, *, tenant_id, review_case_id, maximum_events
    ):
        return tuple(
            item.envelope.event_hash
            for item in self.audit_entries
            if item.envelope.tenant_id == tenant_id
            and item.envelope.subject_id == review_case_id
        )[:maximum_events]

    def list_cases(
        self,
        transaction,
        *,
        tenant_id,
        reviewer_id,
        case_types,
        page_size,
        continuation,
    ):
        rank = {
            ReviewPriority.CRITICAL: 40,
            ReviewPriority.HIGH: 30,
            ReviewPriority.NORMAL: 20,
            ReviewPriority.LOW: 10,
        }
        values = [
            case
            for (tenant, _), case in self.cases.items()
            if tenant == tenant_id
            and case.case_type in case_types
            and (
                case.review_state is ReviewState.OPEN
                or (
                    case.review_state is ReviewState.CLAIMED
                    and case.claimed_reviewer_id == reviewer_id
                )
            )
        ]
        values.sort(
            key=lambda case: (
                -rank[case.priority],
                case.created_at,
                case.review_case_id,
            )
        )
        if continuation is not None:
            cursor = (
                -continuation.priority_rank,
                continuation.created_at,
                continuation.review_case_id,
            )
            values = [
                case
                for case in values
                if (
                    -rank[case.priority],
                    case.created_at,
                    case.review_case_id,
                )
                > cursor
            ]
        return tuple(values[: page_size + 1])

    def get_claim_replay(
        self, transaction, *, tenant_id, reviewer_id, replay_identity
    ):
        return self.claims.get((tenant_id, reviewer_id, replay_identity))

    def persist_claim_and_case(
        self, transaction, *, receipt, previous_case, claimed_case
    ):
        key = (receipt.tenant_id, receipt.reviewer_id, receipt.replay_identity)
        current = self.claims.get(key)
        if current is not None and current != receipt:
            raise IntegrityError("claim replay conflict")
        if self.cases[(previous_case.tenant_id, previous_case.review_case_id)] != previous_case:
            raise IntegrityError("claim race")
        self.claims[key] = receipt
        self.cases[(claimed_case.tenant_id, claimed_case.review_case_id)] = claimed_case

    def get_decision_replay(
        self, transaction, *, tenant_id, reviewer_id, replay_identity
    ):
        return self.decisions.get((tenant_id, reviewer_id, replay_identity))

    def get_decision_by_id(self, transaction, *, tenant_id, decision_id):
        return self.decisions_by_id.get((tenant_id, decision_id))

    def persist_decision_and_case(
        self,
        transaction,
        *,
        decision,
        receipt,
        previous_case,
        in_review_case,
    ):
        key = (decision.tenant_id, decision.reviewer_id, decision.replay_identity)
        pair = (decision, receipt)
        current = self.decisions.get(key)
        if current is not None and current != pair:
            raise IntegrityError("decision replay conflict")
        if self.cases[(previous_case.tenant_id, previous_case.review_case_id)] != previous_case:
            raise IntegrityError("decision race")
        self.decisions[key] = pair
        self.decisions_by_id[(decision.tenant_id, decision.decision_id)] = pair
        self.cases[(in_review_case.tenant_id, in_review_case.review_case_id)] = in_review_case

    def get_handoff_replay(self, transaction, *, tenant_id, replay_identity):
        return self.handoffs.get((tenant_id, replay_identity))

    def persist_handoff_and_case(
        self,
        transaction,
        *,
        request_hash,
        replay_identity,
        decision_receipt_hash,
        result,
        receipt,
        previous_case,
        terminal_case,
    ):
        key = (previous_case.tenant_id, replay_identity)
        pair = (result, receipt)
        current = self.handoffs.get(key)
        if current is not None and current != pair:
            raise IntegrityError("handoff replay conflict")
        if self.cases[(previous_case.tenant_id, previous_case.review_case_id)] != previous_case:
            raise IntegrityError("handoff race")
        self.handoffs[key] = pair
        self.cases[(terminal_case.tenant_id, terminal_case.review_case_id)] = terminal_case


def audit_verification(*, verified: bool = True) -> AuditChainVerificationResult:
    if verified:
        reasons = ()
        failure_sequence = None
    else:
        from aioa_memory_kernel.audit_ledger import AuditReasonCode

        reasons = (AuditReasonCode.AUDIT_EVENT_HASH_MISMATCH,)
        failure_sequence = 1
    return AuditChainVerificationResult(
        chain_id=compute_audit_chain_id(TENANT, OWNER),
        event_count=1,
        first_sequence=1,
        last_sequence=1,
        first_hash=SOURCE_EVENT_HASH,
        last_hash=SOURCE_EVENT_HASH,
        verified=verified,
        failure_sequence=failure_sequence,
        failure_reason_codes=reasons,
        verification_policy_id=STEP33_VERIFICATION_POLICY_ID,
        verification_policy_version=STEP33_VERIFICATION_POLICY_VERSION,
        verification_policy_digest=STEP33_VERIFICATION_POLICY_DIGEST,
    )


def step26_review() -> HumanReviewRequired:
    return HumanReviewRequired(
        schema_version=STEP26_SCHEMA_VERSION,
        request_id="step34-request",
        tenant_id=TENANT,
        user_id=OWNER,
        route_hash=canonical_sha256("step34-route"),
        selected_hat_id="step34-hat",
        selected_hat_version="1.0.0",
        selected_manifest_digest=canonical_sha256("step34-manifest"),
        draft_v1_hash=canonical_sha256("draft-v1"),
        draft_v2_hashes=(canonical_sha256("draft-v2"),),
        correction_packet_hash=canonical_sha256("packet"),
        verification_summary_hash=canonical_sha256("verification"),
        evidence_status=EvidenceStatus.CONFLICTING,
        knowledge_policy_decision=KnowledgePolicyDecision.BLOCK_ANSWER,
        output_status=FinalOutputStatus.HUMAN_REVIEW_REQUIRED,
        reason_codes=(
            Step26ReasonCode.ANSWER_CONFLICTING_EVIDENCE,
            Step26ReasonCode.HUMAN_REVIEW_REQUIRED,
        ),
        review_payload_hashes=(canonical_sha256("review-payload"),),
    )


def case_fixture(
    *,
    created_at: datetime = NOW - timedelta(seconds=10),
    case_type: ReviewCaseType = ReviewCaseType.ANSWER_CONFLICTING_EVIDENCE,
    audit_verified: bool = True,
) -> tuple[HumanReviewCase, ReviewSourceContext]:
    if case_type is ReviewCaseType.ANSWER_CONFLICTING_EVIDENCE:
        return human_review_required_case(
            step26_review(),
            source_audit_event_hash=SOURCE_EVENT_HASH,
            audit_verification=audit_verification(verified=audit_verified),
            created_at=created_at,
        )
    context = ReviewSourceContext(
        schema_version=STEP34_SCHEMA_VERSION,
        source_contract=(
            ReviewSourceContract.STEP32_SHARED_MEMORY_PROMOTION_PROPOSAL
        ),
        subject_hash=SUBJECT_HASH,
        context_payload={
            "review_required": True,
            "privacy_result": "REVIEW_REQUIRED",
            "source_registry_published": False,
        },
        contains_raw_private_memory=False,
        canonical_evidence=False,
        model_authority=False,
    )
    case = build_open_review_case(
        case_type=case_type,
        tenant_id=TENANT,
        owner_user_id=OWNER,
        subject_type=ReviewSubjectType.SHARED_MEMORY_PROMOTION_PROPOSAL,
        subject_id="shared-promotion-review-subject",
        subject_hash=SUBJECT_HASH,
        request_id=None,
        kernel_run_id=None,
        route_hash=None,
        review_reason_codes=(
            ReviewReasonCode.REVIEW_AUDIT_CONTEXT_VERIFIED,
            ReviewReasonCode.REVIEW_CASE_CREATED,
        ),
        priority=ReviewPriority.HIGH,
        source_audit_event_hash=SOURCE_EVENT_HASH,
        source_chain_id=compute_audit_chain_id(TENANT, OWNER),
        audit_verification_result_hash=audit_verification().result_hash,
        audit_context_verified=audit_verified,
        required_context_refs={"promotion_proposal_hash": SUBJECT_HASH},
        source_context_hash=context.context_hash,
        created_at=created_at,
    )
    return case, context


def principal(
    *,
    tenant: str = TENANT,
    reviewer: str = REVIEWER,
    role: ReviewerRole = ReviewerRole.SENIOR_REVIEWER,
) -> ReviewerPrincipal:
    return ReviewerPrincipal(
        schema_version=STEP34_SCHEMA_VERSION,
        tenant_id=tenant,
        reviewer_id=reviewer,
        reviewer_role=role,
        authentication_context_hash=canonical_sha256(
            {"tenant": tenant, "reviewer": reviewer, "authenticated": True}
        ),
        authenticated_at=NOW - timedelta(minutes=1),
    )


def queue_request(value: ReviewerPrincipal, case_type: ReviewCaseType, page_size=8):
    return ReviewQueueRequest(
        schema_version=STEP34_SCHEMA_VERSION,
        tenant_id=value.tenant_id,
        reviewer_principal_hash=value.principal_hash,
        reviewer_id=value.reviewer_id,
        reviewer_role=value.reviewer_role,
        case_types=(case_type,),
        page_size=page_size,
        continuation=None,
        requested_at=NOW,
    )


class Step34WorkspaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = MemoryReviewRepository()
        self.reviewer_runner = MemoryRunner()
        self.service_runner = MemoryRunner(CredentialPurpose.REVIEW_SERVICE_DATABASE)
        self.clock = FrozenClock()
        self.intake = ReviewCaseIntakeService(
            self.service_runner, repository=self.repo, trusted_clock=self.clock
        )
        self.workspace = HumanReviewWorkspaceService(
            self.reviewer_runner, repository=self.repo, trusted_clock=self.clock
        )
        self.handoff = ReviewDecisionHandoffService(
            self.service_runner, repository=self.repo, trusted_clock=self.clock
        )
        self.case, self.context = case_fixture()
        self.reviewer = principal()
        authorization = build_reviewer_authorization(
            self.reviewer,
            case_type=self.case.case_type,
            owner_user_id=OWNER,
            granted_at=NOW - timedelta(minutes=1),
        )
        self.repo.authorizations[
            (
                TENANT,
                REVIEWER,
                self.reviewer.reviewer_role,
                self.case.case_type,
                OWNER,
            )
        ] = authorization

    def create(self):
        return self.intake.create_case(
            self.case,
            self.context,
            authenticated_tenant_id=TENANT,
            authenticated_owner_user_id=OWNER,
        )

    def claim(self):
        self.create()
        request = build_claim_review_case_request(
            self.case,
            self.reviewer,
            requested_at=NOW,
            idempotency_key="claim-review-case",
        )
        return self.workspace.claim_case(request, self.reviewer), request

    def decide(self, decision_type=ReviewDecisionType.REJECT_ANSWER):
        (claimed, _, _), _ = self.claim()
        detail = self.workspace.get_detail(
            tenant_id=TENANT,
            review_case_id=claimed.review_case_id,
            principal=self.reviewer,
        )
        command = SubmitReviewDecision(
            schema_version=STEP34_SCHEMA_VERSION,
            tenant_id=TENANT,
            review_case_id=claimed.review_case_id,
            review_case_hash=claimed.case_hash,
            subject_hash=claimed.subject_hash,
            reviewer_principal_hash=self.reviewer.principal_hash,
            reviewer_id=REVIEWER,
            reviewer_role=self.reviewer.reviewer_role,
            decision_type=decision_type,
            decision_reason_codes=(ReviewReasonCode.REVIEW_DECISION_RECORDED,),
            reviewer_note="Bounded human review decision.",
            context_digest=detail.detail_hash,
            audit_verification_result_hash=claimed.audit_verification_result_hash,
            expected_state=ReviewState.CLAIMED,
            expected_state_version=2,
            decided_at=NOW,
            idempotency_key="record-review-decision",
        )
        return self.workspace.record_decision(command, self.reviewer), command

    def test_step26_adapter_creates_exact_review_required_case(self):
        self.assertIs(
            self.case.case_type, ReviewCaseType.ANSWER_CONFLICTING_EVIDENCE
        )
        self.assertIs(self.case.review_state, ReviewState.OPEN)
        self.assertTrue(self.case.audit_context_verified)
        self.assertFalse(self.context.canonical_evidence)
        self.assertFalse(self.context.model_authority)
        self.assertFalse(
            self.context.context_payload[
                "hat_enforce_known_bad_draft_v1_fallback"
            ]
        )

    def test_step32_shared_promotion_case_uses_exact_source_contract(self):
        case, context = case_fixture(
            case_type=ReviewCaseType.SHARED_PROMOTION_PRIVACY_REVIEW
        )
        verify_human_review_case(case)
        self.assertIs(
            context.source_contract,
            ReviewSourceContract.STEP32_SHARED_MEMORY_PROMOTION_PROPOSAL,
        )
        self.assertIs(
            case.subject_type,
            ReviewSubjectType.SHARED_MEMORY_PROMOTION_PROPOSAL,
        )
        self.assertFalse(context.canonical_evidence)
        self.assertFalse(context.model_authority)

    def test_case_is_immutable_hash_bound_and_round_trips(self):
        verify_human_review_case(self.case)
        self.assertEqual(
            parse_human_review_case(review_to_jsonb(self.case)), self.case
        )
        with self.assertRaises(FrozenInstanceError):
            self.case.review_state = ReviewState.RESOLVED  # type: ignore[misc]
        tampered = object.__new__(HumanReviewCase)
        for field in self.case.__dataclass_fields__:
            object.__setattr__(tampered, field, getattr(self.case, field))
        object.__setattr__(tampered, "subject_hash", "0" * 64)
        with self.assertRaises((IntegrityError, ContractValidationError)):
            verify_human_review_case(tampered)

        detached = object.__new__(HumanReviewCase)
        for field in self.case.__dataclass_fields__:
            object.__setattr__(detached, field, getattr(self.case, field))
        object.__setattr__(detached, "route_hash", canonical_sha256("other-route"))
        object.__setattr__(
            detached,
            "case_hash",
            canonical_sha256(detached, exclude_fields=("case_hash",)),
        )
        with self.assertRaises((IntegrityError, ContractValidationError)):
            verify_human_review_case(detached)

    def test_exact_case_trigger_replay_is_idempotent(self):
        first = self.create()
        second = self.create()
        self.assertFalse(first[1])
        self.assertTrue(second[1])
        self.assertEqual(first[0], second[0])

    def test_case_replay_after_claim_returns_current_persisted_state(self):
        (claimed, _, _), _ = self.claim()
        replayed = self.create()
        self.assertTrue(replayed[1])
        self.assertEqual(replayed[0], claimed)

    def test_intake_rejects_non_open_and_cross_contract_cases(self):
        claimed = transition_review_case(
            self.case,
            state=ReviewState.CLAIMED,
            updated_at=NOW,
            reviewer_id=REVIEWER,
            reviewer_role=self.reviewer.reviewer_role,
        )
        with self.assertRaises(HumanReviewWorkspaceError):
            self.intake.create_case(
                claimed,
                self.context,
                authenticated_tenant_id=TENANT,
                authenticated_owner_user_id=OWNER,
            )

        shared_context = ReviewSourceContext(
            schema_version=STEP34_SCHEMA_VERSION,
            source_contract=(
                ReviewSourceContract.STEP32_SHARED_MEMORY_PROMOTION_PROPOSAL
            ),
            subject_hash=self.case.subject_hash,
            context_payload={
                "review_required": True,
                "source_registry_published": False,
            },
            contains_raw_private_memory=False,
            canonical_evidence=False,
            model_authority=False,
        )
        detached = build_open_review_case(
            case_type=self.case.case_type,
            tenant_id=TENANT,
            owner_user_id=OWNER,
            subject_type=self.case.subject_type,
            subject_id="cross-contract-answer-case",
            subject_hash=self.case.subject_hash,
            request_id=self.case.request_id,
            kernel_run_id=self.case.kernel_run_id,
            route_hash=self.case.route_hash,
            review_reason_codes=self.case.review_reason_codes,
            priority=self.case.priority,
            source_audit_event_hash=self.case.source_audit_event_hash,
            source_chain_id=self.case.source_chain_id,
            audit_verification_result_hash=self.case.audit_verification_result_hash,
            audit_context_verified=True,
            required_context_refs={"human_review_result_hash": self.case.subject_hash},
            source_context_hash=shared_context.context_hash,
            created_at=self.case.created_at,
        )
        with self.assertRaises((ContractValidationError, IntegrityError)):
            self.intake.create_case(
                detached,
                shared_context,
                authenticated_tenant_id=TENANT,
                authenticated_owner_user_id=OWNER,
            )

    def test_wrong_intake_owner_or_tenant_fails_closed(self):
        with self.assertRaises(HumanReviewWorkspaceError):
            self.intake.create_case(
                self.case,
                self.context,
                authenticated_tenant_id="other-tenant",
                authenticated_owner_user_id=OWNER,
            )
        with self.assertRaises(HumanReviewWorkspaceError):
            self.intake.create_case(
                self.case,
                self.context,
                authenticated_tenant_id=TENANT,
                authenticated_owner_user_id="other-owner",
            )

    def test_queue_is_bounded_stable_and_minimum_disclosure(self):
        self.create()
        page = self.workspace.list_queue(
            queue_request(self.reviewer, self.case.case_type), self.reviewer
        )
        self.assertEqual(len(page.items), 1)
        self.assertTrue(page.minimum_disclosure)
        self.assertNotIn(OWNER, page.items[0].safe_summary)
        self.assertNotIn("draft", page.items[0].safe_summary.casefold())
        with self.assertRaises(ContractValidationError):
            queue_request(
                self.reviewer,
                self.case.case_type,
                MAXIMUM_REVIEW_QUEUE_PAGE_SIZE + 1,
            )

    def test_claim_transition_and_exact_replay(self):
        (claimed, receipt, replay), request = self.claim()
        self.assertIs(claimed.review_state, ReviewState.CLAIMED)
        self.assertEqual(claimed.review_state_version, 2)
        self.assertFalse(replay)
        again = self.workspace.claim_case(request, self.reviewer)
        self.assertTrue(again[2])
        self.assertEqual(again[1], receipt)

    def test_claim_replay_after_decision_returns_current_case(self):
        (in_review, _, _, _), _ = self.decide()
        request = build_claim_review_case_request(
            self.case,
            self.reviewer,
            requested_at=NOW,
            idempotency_key="claim-review-case",
        )
        current, _, replay = self.workspace.claim_case(request, self.reviewer)
        self.assertTrue(replay)
        self.assertEqual(current, in_review)

    def test_second_reviewer_cannot_win_claim_race(self):
        (claimed, _, _), _ = self.claim()
        other = principal(reviewer="step34-reviewer-two")
        authorization = build_reviewer_authorization(
            other,
            case_type=self.case.case_type,
            owner_user_id=OWNER,
            granted_at=NOW,
        )
        self.repo.authorizations[
            (TENANT, other.reviewer_id, other.reviewer_role, self.case.case_type, OWNER)
        ] = authorization
        stale = build_claim_review_case_request(
            self.case, other, requested_at=NOW, idempotency_key="second-claim"
        )
        with self.assertRaises(HumanReviewWorkspaceError):
            self.workspace.claim_case(stale, other)
        self.assertEqual(
            self.repo.cases[(TENANT, self.case.review_case_id)], claimed
        )

    def test_decision_transition_and_exact_replay(self):
        (in_review, decision, receipt, replay), command = self.decide()
        self.assertIs(in_review.review_state, ReviewState.IN_REVIEW)
        self.assertEqual(in_review.review_state_version, 3)
        self.assertFalse(replay)
        self.assertFalse(decision.reviewer_note_is_canonical_evidence)
        again = self.workspace.record_decision(command, self.reviewer)
        self.assertTrue(again[3])
        self.assertEqual(again[1:3], (decision, receipt))

    def test_changed_decision_under_same_replay_is_rejected(self):
        (_, _, _, _), command = self.decide()
        changed = replace(command, reviewer_note="Changed semantic decision note.")
        with self.assertRaises(HumanReviewWorkspaceError):
            self.workspace.record_decision(changed, self.reviewer)

    def test_case_specific_decision_family_rejects_shared_decision(self):
        (claimed, _, _), _ = self.claim()
        detail = self.workspace.get_detail(
            tenant_id=TENANT,
            review_case_id=claimed.review_case_id,
            principal=self.reviewer,
        )
        command = SubmitReviewDecision(
            schema_version=STEP34_SCHEMA_VERSION,
            tenant_id=TENANT,
            review_case_id=claimed.review_case_id,
            review_case_hash=claimed.case_hash,
            subject_hash=claimed.subject_hash,
            reviewer_principal_hash=self.reviewer.principal_hash,
            reviewer_id=REVIEWER,
            reviewer_role=self.reviewer.reviewer_role,
            decision_type=ReviewDecisionType.APPROVE_SHARED_PROMOTION_CANDIDATE,
            decision_reason_codes=(ReviewReasonCode.REVIEW_DECISION_RECORDED,),
            reviewer_note=None,
            context_digest=detail.detail_hash,
            audit_verification_result_hash=claimed.audit_verification_result_hash,
            expected_state=ReviewState.CLAIMED,
            expected_state_version=2,
            decided_at=NOW,
            idempotency_key="invalid-cross-family-decision",
        )
        with self.assertRaises(HumanReviewWorkspaceError):
            self.workspace.record_decision(command, self.reviewer)

    def test_broken_audit_context_can_only_escalate(self):
        case, context = case_fixture(audit_verified=False)
        self.case, self.context = case, context
        authorization = build_reviewer_authorization(
            self.reviewer,
            case_type=case.case_type,
            owner_user_id=OWNER,
            granted_at=NOW,
        )
        self.repo.authorizations[
            (TENANT, REVIEWER, self.reviewer.reviewer_role, case.case_type, OWNER)
        ] = authorization
        with self.assertRaises(HumanReviewWorkspaceError):
            self.decide(ReviewDecisionType.REJECT_ANSWER)

    def test_typed_handoff_resolves_without_returning_answer_or_publishing(self):
        (in_review, decision, decision_receipt, _), _ = self.decide()
        request = build_review_handoff_request(
            in_review,
            decision,
            decision_receipt,
            requested_at=NOW,
            idempotency_key="handoff-review-decision",
        )
        terminal, result, receipt, replay = self.handoff.handoff(
            request,
            authenticated_service_id=STEP34_REVIEW_SERVICE_ACTOR_ID,
            current_subject_hash=in_review.subject_hash,
        )
        self.assertIs(terminal.review_state, ReviewState.RESOLVED)
        self.assertTrue(result.accepted_for_typed_downstream)
        self.assertFalse(result.answer_returned)
        self.assertFalse(result.source_registry_published)
        self.assertFalse(result.external_execution_authority)
        self.assertFalse(replay)
        again = self.handoff.handoff(
            request,
            authenticated_service_id=STEP34_REVIEW_SERVICE_ACTOR_ID,
            current_subject_hash=in_review.subject_hash,
        )
        self.assertTrue(again[3])
        self.assertEqual(again[2], receipt)

    def test_stale_subject_blocks_handoff(self):
        (in_review, decision, receipt, _), _ = self.decide()
        request = build_review_handoff_request(
            in_review,
            decision,
            receipt,
            requested_at=NOW,
            idempotency_key="stale-handoff",
        )
        with self.assertRaises(HumanReviewWorkspaceError):
            self.handoff.handoff(
                request,
                authenticated_service_id=STEP34_REVIEW_SERVICE_ACTOR_ID,
                current_subject_hash="0" * 64,
            )

    def test_review_actions_append_typed_step33_events(self):
        (in_review, decision, receipt, _), _ = self.decide()
        request = build_review_handoff_request(
            in_review,
            decision,
            receipt,
            requested_at=NOW,
            idempotency_key="audited-handoff",
        )
        self.handoff.handoff(
            request,
            authenticated_service_id=STEP34_REVIEW_SERVICE_ACTOR_ID,
            current_subject_hash=in_review.subject_hash,
        )
        event_types = [item.envelope.event_type.value for item in self.repo.audit_entries]
        self.assertEqual(
            event_types,
            [
                "REVIEW_CASE_CREATED",
                "REVIEW_CASE_CLAIMED",
                "REVIEW_DECISION_RECORDED",
                "REVIEW_HANDOFF_SUCCEEDED",
                "REVIEW_CASE_RESOLVED",
            ],
        )
        self.assertTrue(all("case_type" in item.event_payload for item in self.repo.audit_entries))

    def test_review_public_services_expose_no_generic_mutation(self):
        names = {
            name
            for service in (
                ReviewCaseIntakeService,
                HumanReviewWorkspaceService,
                ReviewDecisionHandoffService,
            )
            for name, value in inspect.getmembers(service, inspect.isfunction)
            if not name.startswith("_")
        }
        self.assertEqual(
            names,
            {
                "claim_case",
                "create_case",
                "get_detail",
                "handoff",
                "list_queue",
                "record_decision",
            },
        )
        for forbidden in (
            "execute_action",
            "publish_source",
            "activate_patch",
            "set_business_state",
        ):
            self.assertNotIn(forbidden, names)

    def test_review_state_machine_rejects_skips(self):
        for state in (
            ReviewState.IN_REVIEW,
            ReviewState.RESOLVED,
            ReviewState.STALE,
        ):
            with self.assertRaises(HumanReviewWorkspaceError):
                transition_review_case(
                    self.case,
                    state=state,
                    updated_at=NOW,
                    reviewer_id=REVIEWER,
                    reviewer_role=self.reviewer.reviewer_role,
                )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
