"""Step 37 transaction, acknowledgement-loss, and restart recovery tests."""

from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import timedelta
from typing import Any
from unittest import mock

from aioa_memory_kernel.audit_ledger import (
    AuditActorType,
    AuditLedgerService,
    approval_receipt_event,
    verify_audit_chain,
)
from aioa_memory_kernel.contracts.serialization import canonical_sha256
from aioa_memory_kernel.persistence import (
    AccessMode,
    IdempotencyConflictError,
    PersistenceTransactionError,
    RequestContext,
    SerializableTransactionRunner,
)
from aioa_memory_kernel.personal_memory import (
    ActivePatchRetrievalService,
    PersonalMemoryActivationService,
    PersonalMemoryApprovalService,
    PersonalMemoryCommitHelper,
    PersonalMemoryLifecycle32Service,
    PersonalMemoryLifecycleExportRecord,
    Step32ActorType,
    Step32ReasonCode,
    build_personal_memory_activation_request,
    build_personal_memory_approval_request,
    build_personal_memory_commit_request,
    build_deletion_request,
    build_lifecycle_export_request,
    build_revocation_request,
    build_supersession_request,
)
from aioa_memory_kernel.reliability import (
    FailureDomain,
    FailurePoint,
    FailureRecoveryCaseResult,
    RecoveryStatus,
)
from aioa_memory_kernel.review_workspace import (
    HumanReviewWorkspaceService,
    ReviewCaseIntakeService,
    ReviewDecisionHandoffService,
    ReviewDecisionType,
    STEP34_REVIEW_SERVICE_ACTOR_ID,
    build_claim_review_case_request,
    build_review_handoff_request,
    build_reviewer_authorization,
)
from aioa_memory_kernel.security.credentials import CredentialPurpose
from tests.failure_injection.harness import run_crash_after_durable_write_case
from tests.test_step28_correction_candidate_bridge import FakeIdempotency
from tests.test_step29_personal_memory_patch_proposal import (
    InMemoryCandidateRepository,
    InMemoryProposalRepository,
    InMemorySlotRepository,
    fixture,
)
from tests.test_step30_user_approval_commit_activation import (
    InMemoryLifecycleRepository,
    MutableTrustedClock,
    lifecycle_chain,
)
from tests.test_step31_active_patch_retrieval import (
    InMemoryRetrievalRepository,
    InMemorySlotRepository as RetrievalSlotRepository,
    candidate as retrieval_candidate,
    request_for,
)
from tests.test_step32_personal_memory_lifecycle import active_pair, pending_slot
from tests.test_step33_audit_ledger import MemoryRepository, MemoryRunner, draft
from tests.test_step34_human_review_workspace import (
    FrozenClock,
    MemoryReviewRepository,
    NOW as REVIEW_NOW,
    OWNER as REVIEW_OWNER,
    REVIEWER,
    TENANT as REVIEW_TENANT,
    case_fixture,
    principal,
)


class AcknowledgementLost(RuntimeError):
    """Sanitized client outcome after a durable callback result."""

    sanitized_code = "ACKNOWLEDGEMENT_LOST_AFTER_DURABLE_WRITE"


class AcknowledgementLossRunner(SerializableTransactionRunner):
    """Test-only runner that loses exactly one post-callback acknowledgement."""

    def __init__(self, purpose: CredentialPurpose) -> None:
        super().__init__(lambda: None, credential_purpose=purpose)
        self.calls = 0

    def run(self, context, callback, *, operation_kind=None):
        del context, operation_kind
        self.calls += 1
        result = callback(object())
        if self.calls == 1:
            raise AcknowledgementLost
        return result


class BeforeCallbackFailure(RuntimeError):
    """Sanitized pre-write failure emitted by a test-only runner."""

    sanitized_code = "FAILURE_BEFORE_TRANSACTION_CALLBACK"


class BeforeCallbackFailureRunner(SerializableTransactionRunner):
    """Fail once before business code, then permit an exact explicit retry."""

    def __init__(self, purpose: CredentialPurpose) -> None:
        super().__init__(lambda: None, credential_purpose=purpose)
        self.calls = 0

    def run(self, context, callback, *, operation_kind=None):
        del context, operation_kind
        self.calls += 1
        if self.calls == 1:
            raise BeforeCallbackFailure
        return callback(object())


class SerializationSignal(RuntimeError):
    def __init__(self) -> None:
        super().__init__("sanitized serialization retry")
        self.sqlstate = "40001"


class _StoreCursor:
    def __init__(self, connection: "_StoreConnection") -> None:
        self.connection = connection
        self.row: dict[str, str] | None = None

    def execute(self, sql: str, parameters: Any = None) -> None:
        if sql == "SELECT_RESULT":
            if self.connection.index in self.connection.factory.read_failures:
                raise RuntimeError("sanitized synthetic read failure")
            key = parameters[0]
            value = self.connection.factory.values.get(key)
            self.row = None if value is None else {"result_hash": value}
        elif sql == "INSERT_RESULT":
            key, value = parameters
            if key in self.connection.factory.values:
                raise AssertionError("duplicate durable semantic effect")
            self.connection.pending[key] = value
            self.connection.factory.insert_attempts += 1
            if self.connection.index in self.connection.factory.write_failures:
                raise RuntimeError("sanitized synthetic pre-commit failure")

    def fetchone(self):
        return self.row

    def fetchall(self):
        return []

    def close(self) -> None:
        return None


class _StoreConnection:
    def __init__(self, factory: "TransactionalStoreFactory", index: int) -> None:
        self.factory = factory
        self.index = index
        self.pending: dict[str, str] = {}
        self.cursor_value = _StoreCursor(self)

    def cursor(self) -> _StoreCursor:
        return self.cursor_value

    def commit(self) -> None:
        if self.index in self.factory.serialization_failures:
            raise SerializationSignal
        self.factory.values.update(self.pending)
        self.pending.clear()

    def rollback(self) -> None:
        self.pending.clear()

    def close(self) -> None:
        if self.index in self.factory.close_failures:
            raise RuntimeError("sanitized close failure")


class TransactionalStoreFactory:
    """A transactional test adapter with rollback and commit-ack ambiguity."""

    def __init__(
        self,
        *,
        connection_failures: tuple[int, ...] = (),
        read_failures: tuple[int, ...] = (),
        write_failures: tuple[int, ...] = (),
        serialization_failures: tuple[int, ...] = (),
        close_failures: tuple[int, ...] = (),
    ) -> None:
        self.connection_failures = frozenset(connection_failures)
        self.read_failures = frozenset(read_failures)
        self.write_failures = frozenset(write_failures)
        self.serialization_failures = frozenset(serialization_failures)
        self.close_failures = frozenset(close_failures)
        self.values: dict[str, str] = {}
        self.calls = 0
        self.insert_attempts = 0

    def __call__(self) -> _StoreConnection:
        self.calls += 1
        if self.calls in self.connection_failures:
            raise RuntimeError("sanitized synthetic connection failure")
        return _StoreConnection(self, self.calls)


class _Lifecycle32Repository:
    def __init__(self, active) -> None:
        self.active = active
        self.states = {active.proposal.proposal_id: active}
        self.supersessions: dict[str, dict[str, str]] = {}
        self.revocations: dict[str, dict[str, str]] = {}
        self.exports = {}
        self.deletions: dict[str, dict[str, str]] = {}

    def get_step30_state(self, transaction, tenant, owner, proposal_id):
        del transaction
        state = self.states.get(proposal_id)
        if state is None:
            return None
        if (tenant, owner) != (
            state.proposal.tenant_id,
            state.proposal.owner_user_id,
        ):
            return None
        return state

    def get_supersession_replay(self, transaction, **values):
        del transaction
        return self.supersessions.get(values["replay_identity"])

    def get_revocation_replay(self, transaction, **values):
        del transaction
        return self.revocations.get(values["replay_identity"])

    def lock_active_patch(self, transaction, **values):
        del transaction
        for state in self.states.values():
            patch = state.committed_patch
            if patch is not None and (
                values["tenant_id"],
                values["owner_user_id"],
                values["personal_memory_space_id"],
                values["patch_id"],
                values["patch_hash"],
            ) == (
                patch.tenant_id,
                patch.owner_user_id,
                patch.personal_memory_space_id,
                patch.patch_id,
                patch.patch_hash,
            ):
                return {"patch_id": patch.patch_id}
        return None

    def persist_supersession(self, transaction, record):
        del transaction
        self.supersessions[record.replay_identity] = {
            "request_hash": record.request_hash,
            "supersession_hash": record.supersession_hash,
            "old_patch_id": record.old_patch_id,
            "new_patch_id": record.new_patch_id,
        }

    def persist_revocation(self, transaction, record):
        del transaction
        self.revocations[record.replay_identity] = {
            "request_hash": record.request_hash,
            "revocation_hash": record.revocation_hash,
            "patch_id": record.patch_id,
        }

    def get_export_replay(self, transaction, **values):
        del transaction
        return self.exports.get(values["replay_identity"])

    def export_records(self, transaction, **values):
        del transaction
        return (
            PersonalMemoryLifecycleExportRecord(
                record_type="STEP37_LIFECYCLE",
                record_id="step37-export-record",
                payload={
                    "slot_hash": values["slot_hash"],
                    "owner_private": True,
                },
            ),
        )

    def persist_export(self, transaction, bundle):
        del transaction
        self.exports[bundle.replay_identity] = bundle

    def get_deletion_replay(self, transaction, **values):
        del transaction
        return self.deletions.get(values["replay_identity"])

    def persist_deletion(self, transaction, result):
        del transaction
        self.deletions[result.replay_identity] = {
            "request_hash": result.request_hash,
            "result_hash": result.result_hash,
            "tombstone_hash": result.tombstone_hash,
        }


class _Slot32Repository:
    def __init__(self, slot, *, policy=None) -> None:
        self.slot = slot
        self.policy = fixture().policy if policy is None else policy
        self.binding_delete_calls = 0

    def get_slot(self, transaction, tenant, owner, slot_id):
        del transaction
        if (tenant, owner, slot_id) == (
            self.slot.tenant_id,
            self.slot.owner_user_id,
            self.slot.personal_memory_space_id,
        ):
            return self.slot
        return None

    def get_quota_policy(self, transaction, tenant, owner, policy_id):
        del transaction
        if (tenant, owner, policy_id) == (
            self.policy.tenant_id,
            self.policy.owner_user_id,
            self.policy.quota_policy_id,
        ):
            return self.policy
        return None

    def delete_all_bindings(self, transaction, slot):
        del transaction
        if slot != self.slot:
            raise AssertionError("binding deletion did not bind the current slot")
        self.binding_delete_calls += 1

    def update_slot(
        self,
        transaction,
        *,
        current,
        state,
        display_name,
        quota_policy,
        model_bindings,
        state_version,
        configuration_version,
        changed_at,
        export_requested_at,
        deletion_requested_at,
        deleted_at,
    ):
        del transaction
        if current != self.slot:
            raise AssertionError("slot update lost its compare-and-set identity")
        self.slot = replace(
            current,
            state=state,
            display_name=display_name,
            quota_policy_id=quota_policy.quota_policy_id,
            quota_policy_digest=quota_policy.policy_digest,
            model_bindings=model_bindings,
            state_version=state_version,
            configuration_version=configuration_version,
            updated_at=changed_at,
            export_requested_at=export_requested_at,
            deletion_requested_at=deletion_requested_at,
            deleted_at=deleted_at,
        )
        return self.slot


def _database_operation(
    runner: SerializableTransactionRunner,
    *,
    key: str,
    result_hash: str,
) -> str:
    context = RequestContext("step37-tenant", "step37-owner", AccessMode.USER_PRIVATE)

    def work(transaction):
        existing = transaction.fetch_one("SELECT_RESULT", (key,))
        if existing is not None:
            if existing["result_hash"] != result_hash:
                raise IdempotencyConflictError(
                    "semantic replay changed",
                    sanitized_code="CONFLICTING_REPLAY",
                )
            return existing["result_hash"]
        transaction.execute("INSERT_RESULT", (key, result_hash))
        return result_hash

    return runner.run(context, work, operation_kind="STEP37_RECOVERY_PROBE")


def run_database_recovery_campaigns() -> tuple[FailureRecoveryCaseResult, ...]:
    subject_hash = canonical_sha256({"case": "database-recovery"})
    result_hash = canonical_sha256({"semantic-result": subject_hash})

    serialization_factory = TransactionalStoreFactory(serialization_failures=(1,))
    serialization_runner = SerializableTransactionRunner(
        serialization_factory,
        sleep=lambda _: None,
        backoff=lambda _: 0,
    )
    result = _database_operation(
        serialization_runner, key="stable-operation", result_hash=result_hash
    )
    if (
        result != result_hash
        or serialization_factory.values != {"stable-operation": result_hash}
        or serialization_factory.calls != 2
        or serialization_factory.insert_attempts != 2
    ):
        raise AssertionError("serialization retry did not converge transactionally")

    ack_factory = TransactionalStoreFactory(close_failures=(1,))
    ack_runner = SerializableTransactionRunner(ack_factory, sleep=lambda _: None)
    try:
        _database_operation(ack_runner, key="ack-operation", result_hash=result_hash)
    except PersistenceTransactionError as error:
        if error.sanitized_code != "CONNECTION_RELEASE_FAILED":
            raise
    else:
        raise AssertionError("post-commit acknowledgement loss was not observed")
    replay = _database_operation(
        ack_runner, key="ack-operation", result_hash=result_hash
    )
    if (
        replay != result_hash
        or ack_factory.values != {"ack-operation": result_hash}
        or ack_factory.insert_attempts != 1
    ):
        raise AssertionError("acknowledgement-loss replay duplicated the effect")

    before_begin_factory = TransactionalStoreFactory(connection_failures=(1,))
    before_begin_runner = SerializableTransactionRunner(
        before_begin_factory, sleep=lambda _: None
    )
    try:
        _database_operation(
            before_begin_runner, key="before-begin", result_hash=result_hash
        )
    except PersistenceTransactionError:
        pass
    else:
        raise AssertionError("pre-BEGIN connection failure was not observed")
    if before_begin_factory.values or before_begin_factory.insert_attempts:
        raise AssertionError("pre-BEGIN failure produced a durable effect")
    _database_operation(
        before_begin_runner, key="before-begin", result_hash=result_hash
    )

    before_commit_factory = TransactionalStoreFactory(write_failures=(1,))
    before_commit_runner = SerializableTransactionRunner(
        before_commit_factory, sleep=lambda _: None
    )
    try:
        _database_operation(
            before_commit_runner, key="before-commit", result_hash=result_hash
        )
    except PersistenceTransactionError:
        pass
    else:
        raise AssertionError("pre-commit failure was not observed")
    if before_commit_factory.values:
        raise AssertionError("pre-commit failure escaped rollback")
    _database_operation(
        before_commit_runner, key="before-commit", result_hash=result_hash
    )
    if before_commit_factory.values != {"before-commit": result_hash}:
        raise AssertionError("pre-commit retry did not converge once")

    read_factory = TransactionalStoreFactory(read_failures=(1,))
    read_runner = SerializableTransactionRunner(read_factory, sleep=lambda _: None)
    try:
        _database_operation(read_runner, key="read-retry", result_hash=result_hash)
    except PersistenceTransactionError:
        pass
    else:
        raise AssertionError("read failure was not observed")
    if read_factory.values or read_factory.insert_attempts:
        raise AssertionError("read failure produced a durable effect")
    _database_operation(read_runner, key="read-retry", result_hash=result_hash)

    changed_result_hash = canonical_sha256({"semantic-result": "changed"})
    try:
        _database_operation(
            ack_runner, key="ack-operation", result_hash=changed_result_hash
        )
    except IdempotencyConflictError:
        pass
    else:
        raise AssertionError("changed semantic replay was not rejected")
    if ack_factory.values != {"ack-operation": result_hash}:
        raise AssertionError("changed replay altered the durable result")

    return (
        FailureRecoveryCaseResult.build(
            case_id="database.serialization-retry",
            failure_domain=FailureDomain.TRANSACTION_RETRY,
            failure_point=FailurePoint.DB_COMMIT_SERIALIZATION_FAILURE,
            subject_hash=subject_hash,
            attempt_count=2,
            recovery_status=RecoveryStatus.RECOVERED_BY_RETRY,
            final_semantic_state="ONE_DURABLE_RESULT",
            reason_codes=("DB_SERIALIZATION_RETRY_RECOVERED",),
        ),
        FailureRecoveryCaseResult.build(
            case_id="database.commit-ack-lost",
            failure_domain=FailureDomain.DATABASE,
            failure_point=FailurePoint.DB_AFTER_COMMIT_ACK_LOST,
            subject_hash=subject_hash,
            attempt_count=2,
            recovery_status=RecoveryStatus.RECOVERED_BY_IDEMPOTENT_REPLAY,
            final_semantic_state="ONE_DURABLE_RESULT",
            reason_codes=("DB_ACK_LOST_REPLAY_RECOVERED",),
        ),
        FailureRecoveryCaseResult.build(
            case_id="database.before-begin",
            failure_domain=FailureDomain.DATABASE,
            failure_point=FailurePoint.DB_BEFORE_BEGIN,
            subject_hash=subject_hash,
            attempt_count=2,
            recovery_status=RecoveryStatus.RECOVERED_BY_RETRY,
            final_semantic_state="NO_MUTATION_THEN_ONE_DURABLE_RESULT",
            reason_codes=("DB_BEFORE_BEGIN_FAILED_CLOSED",),
        ),
        FailureRecoveryCaseResult.build(
            case_id="database.before-commit",
            failure_domain=FailureDomain.DATABASE,
            failure_point=FailurePoint.DB_BEFORE_COMMIT,
            subject_hash=subject_hash,
            attempt_count=2,
            recovery_status=RecoveryStatus.RECOVERED_BY_RETRY,
            final_semantic_state="ROLLED_BACK_THEN_COMMITTED_ONCE",
            reason_codes=("DB_PRECOMMIT_ROLLBACK_VERIFIED",),
        ),
        FailureRecoveryCaseResult.build(
            case_id="database.read-failure",
            failure_domain=FailureDomain.DATABASE,
            failure_point=FailurePoint.DB_READ_FAILURE,
            subject_hash=subject_hash,
            attempt_count=2,
            recovery_status=RecoveryStatus.RECOVERED_BY_RETRY,
            final_semantic_state="READ_FAILURE_DID_NOT_WIDEN_SCOPE",
            reason_codes=("DB_READ_FAILURE_FAILED_CLOSED",),
        ),
        FailureRecoveryCaseResult.build(
            case_id="database.changed-replay-conflict",
            failure_domain=FailureDomain.DATABASE,
            failure_point=FailurePoint.DB_BEFORE_COMMIT,
            subject_hash=subject_hash,
            attempt_count=1,
            recovery_status=RecoveryStatus.FAILED_CLOSED,
            final_semantic_state="CONFLICT_REJECTED_DURABLE_RESULT_UNCHANGED",
            reason_codes=("DB_REPLAY_CONFLICT",),
        ),
    )


def _personal_memory_services():
    value = fixture()
    lifecycle = InMemoryLifecycleRepository(value.awaiting)
    slots = InMemorySlotRepository(value)
    candidates = InMemoryCandidateRepository(value)
    proposals = InMemoryProposalRepository()
    proposals.values[value.awaiting.proposal.proposal_id] = value.awaiting
    idempotency = FakeIdempotency()
    clock = MutableTrustedClock(value.awaiting.updated_at + timedelta(seconds=1))
    common = {
        "lifecycle_repository": lifecycle,
        "slot_repository": slots,
        "candidate_repository": candidates,
        "proposal_repository": proposals,
        "idempotency": idempotency,
        "trusted_clock": clock,
    }
    return value, lifecycle, clock, common


def _approve_personal_memory_fixture(*, suffix: str):
    value, lifecycle, clock, common = _personal_memory_services()
    service = PersonalMemoryApprovalService(
        MemoryRunner(CredentialPurpose.APPLICATION_DATABASE), **common
    )
    request = build_personal_memory_approval_request(
        value.awaiting,
        approval_nonce=f"step37-{suffix}-approval",
        requested_at=value.awaiting.updated_at + timedelta(seconds=1),
    )
    approved, receipt, replay = service.approve(
        request,
        authenticated_actor_user_id=value.slot.owner_user_id,
    )
    if replay:
        raise AssertionError("fixture approval unexpectedly replayed")
    return value, lifecycle, clock, common, approved, receipt


def run_personal_memory_prewrite_failure_campaigns(
) -> tuple[FailureRecoveryCaseResult, ...]:
    value, lifecycle, _, common = _personal_memory_services()
    approval_runner = BeforeCallbackFailureRunner(
        CredentialPurpose.APPLICATION_DATABASE
    )
    approval_service = PersonalMemoryApprovalService(approval_runner, **common)
    approval_request = build_personal_memory_approval_request(
        value.awaiting,
        approval_nonce="step37-approval-before-write",
        requested_at=value.awaiting.updated_at + timedelta(seconds=1),
    )
    try:
        approval_service.approve(
            approval_request,
            authenticated_actor_user_id=value.slot.owner_user_id,
        )
    except BeforeCallbackFailure:
        pass
    else:
        raise AssertionError("approval pre-write failure was not injected")
    if lifecycle.approvals or lifecycle.state != value.awaiting:
        raise AssertionError("approval pre-write failure mutated lifecycle state")
    _, approval_receipt, approval_replay = approval_service.approve(
        approval_request,
        authenticated_actor_user_id=value.slot.owner_user_id,
    )
    if approval_replay or len(lifecycle.approvals) != 1:
        raise AssertionError("approval retry did not create exactly one receipt")

    (
        _,
        commit_lifecycle,
        commit_clock,
        commit_common,
        approved,
        _,
    ) = _approve_personal_memory_fixture(suffix="commit-before-write")
    commit_request = build_personal_memory_commit_request(
        approved,
        commit_idempotency_key="step37-commit-before-write",
        requested_at=approved.updated_at + timedelta(seconds=1),
    )
    commit_clock.current = commit_request.requested_at
    commit_runner = BeforeCallbackFailureRunner(
        CredentialPurpose.PERSONAL_MEMORY_COMMIT_DATABASE
    )
    commit_service = PersonalMemoryCommitHelper(commit_runner, **commit_common)
    try:
        commit_service.commit(commit_request)
    except BeforeCallbackFailure:
        pass
    else:
        raise AssertionError("technical commit pre-write failure was not injected")
    if commit_lifecycle.commits:
        raise AssertionError("technical commit pre-write failure persisted a patch")
    _, commit_receipt, commit_replay = commit_service.commit(commit_request)
    if commit_replay or len(commit_lifecycle.commits) != 1:
        raise AssertionError("technical commit retry did not persist exactly once")

    (
        _,
        activation_lifecycle,
        activation_clock,
        activation_common,
        activation_approved,
        _,
    ) = _approve_personal_memory_fixture(suffix="activation-before-write")
    activation_commit_request = build_personal_memory_commit_request(
        activation_approved,
        commit_idempotency_key="step37-activation-precondition-commit",
        requested_at=activation_approved.updated_at + timedelta(seconds=1),
    )
    activation_clock.current = activation_commit_request.requested_at
    committed, _, _ = PersonalMemoryCommitHelper(
        MemoryRunner(CredentialPurpose.PERSONAL_MEMORY_COMMIT_DATABASE),
        **activation_common,
    ).commit(activation_commit_request)
    activation_request = build_personal_memory_activation_request(
        committed,
        activation_idempotency_key="step37-activation-before-write",
        requested_at=committed.updated_at + timedelta(seconds=1),
    )
    activation_clock.current = activation_request.requested_at
    activation_runner = BeforeCallbackFailureRunner(
        CredentialPurpose.PERSONAL_MEMORY_COMMIT_DATABASE
    )
    activation_service = PersonalMemoryActivationService(
        activation_runner, **activation_common
    )
    with mock.patch.object(
        PersonalMemoryActivationService,
        "_proposal_id_for_patch",
        return_value=committed.proposal.proposal_id,
    ):
        try:
            activation_service.activate(activation_request)
        except BeforeCallbackFailure:
            pass
        else:
            raise AssertionError("activation pre-write failure was not injected")
        if activation_lifecycle.activations:
            raise AssertionError("activation pre-write failure changed active state")
        active, activation_receipt, activation_replay = activation_service.activate(
            activation_request
        )
    if (
        activation_replay
        or len(activation_lifecycle.activations) != 1
        or active.committed_patch.patch_statement_sha256
        != committed.committed_patch.patch_statement_sha256
    ):
        raise AssertionError("activation retry did not preserve exact content")

    return (
        FailureRecoveryCaseResult.build(
            case_id="personal-memory.approval-before-write",
            failure_domain=FailureDomain.PERSONAL_MEMORY_APPROVAL,
            failure_point=FailurePoint.PM_BEFORE_APPROVAL,
            subject_hash=approval_receipt.receipt_hash,
            attempt_count=2,
            recovery_status=RecoveryStatus.RECOVERED_BY_RETRY,
            final_semantic_state="NO_APPROVAL_THEN_APPROVED_ONCE",
            reason_codes=("APPROVAL_PREWRITE_FAILED_CLOSED",),
        ),
        FailureRecoveryCaseResult.build(
            case_id="personal-memory.commit-before-write",
            failure_domain=FailureDomain.PERSONAL_MEMORY_COMMIT,
            failure_point=FailurePoint.PM_BEFORE_COMMIT,
            subject_hash=commit_receipt.receipt_hash,
            attempt_count=2,
            recovery_status=RecoveryStatus.RECOVERED_BY_RETRY,
            final_semantic_state="NO_PATCH_THEN_COMMITTED_ONCE",
            reason_codes=("COMMIT_PREWRITE_FAILED_CLOSED",),
        ),
        FailureRecoveryCaseResult.build(
            case_id="personal-memory.activation-before-write",
            failure_domain=FailureDomain.PERSONAL_MEMORY_ACTIVATION,
            failure_point=FailurePoint.PM_BEFORE_ACTIVATION,
            subject_hash=activation_receipt.receipt_hash,
            attempt_count=2,
            recovery_status=RecoveryStatus.RECOVERED_BY_RETRY,
            final_semantic_state="INACTIVE_THEN_ACTIVE_ONCE",
            reason_codes=("ACTIVATION_PREWRITE_FAILED_CLOSED",),
        ),
    )


def run_personal_memory_lifecycle_prewrite_campaigns(
) -> tuple[FailureRecoveryCaseResult, ...]:
    old, new = active_pair()
    old_patch = old.committed_patch
    new_patch = new.committed_patch
    if old_patch is None or new_patch is None:
        raise AssertionError("Step 32 supersession fixture is not committed")
    supersession_repository = _Lifecycle32Repository(old)
    supersession_repository.states[new.proposal.proposal_id] = new
    supersession_slot = fixture().slot
    supersession_slots = _Slot32Repository(supersession_slot)
    supersession_at = new.updated_at + timedelta(seconds=1)
    supersession_request = build_supersession_request(
        old,
        new,
        reason_codes=(Step32ReasonCode.SUPERSESSION_CREATED,),
        effective_at=supersession_at,
        idempotency_key="step37-supersession-before-write",
    )
    supersession_runner = BeforeCallbackFailureRunner(
        CredentialPurpose.APPLICATION_DATABASE
    )
    supersession_service = PersonalMemoryLifecycle32Service(
        supersession_runner,
        lifecycle_repository=supersession_repository,
        slot_repository=supersession_slots,
        trusted_clock=MutableTrustedClock(supersession_at),
    )
    immutable_patch_identity = (
        old.state_hash,
        old_patch.patch_hash,
        old_patch.patch_statement_sha256,
        new.state_hash,
        new_patch.patch_hash,
        new_patch.patch_statement_sha256,
    )
    try:
        supersession_service.supersede(
            supersession_request,
            authenticated_owner_user_id=old.proposal.owner_user_id,
        )
    except BeforeCallbackFailure:
        pass
    else:
        raise AssertionError("supersession pre-write failure was not injected")
    if supersession_repository.supersessions:
        raise AssertionError("supersession pre-write failure persisted a relation")
    if supersession_slots.slot != supersession_slot:
        raise AssertionError("supersession pre-write failure changed the owner slot")
    if immutable_patch_identity != (
        old.state_hash,
        old_patch.patch_hash,
        old_patch.patch_statement_sha256,
        new.state_hash,
        new_patch.patch_hash,
        new_patch.patch_statement_sha256,
    ):
        raise AssertionError("supersession pre-write failure rewrote patch history")

    supersession, supersession_replay = supersession_service.supersede(
        supersession_request,
        authenticated_owner_user_id=old.proposal.owner_user_id,
    )
    duplicate_supersession, exact_supersession_replay = (
        supersession_service.supersede(
            supersession_request,
            authenticated_owner_user_id=old.proposal.owner_user_id,
        )
    )
    if (
        supersession_replay
        or not exact_supersession_replay
        or duplicate_supersession != supersession
        or len(supersession_repository.supersessions) != 1
        or not supersession.preserves_history
        or supersession.old_patch_hash != old_patch.patch_hash
        or supersession.new_patch_hash != new_patch.patch_hash
        or immutable_patch_identity
        != (
            old.state_hash,
            old_patch.patch_hash,
            old_patch.patch_statement_sha256,
            new.state_hash,
            new_patch.patch_hash,
            new_patch.patch_statement_sha256,
        )
    ):
        raise AssertionError(
            "supersession retry/replay did not preserve one immutable relation"
        )

    active = lifecycle_chain()[-1]
    active_patch = active.committed_patch
    if active_patch is None:
        raise AssertionError("Step 32 deletion fixture is not committed")
    deletion_slot = pending_slot(active)
    if not deletion_slot.model_bindings:
        raise AssertionError("Step 32 deletion fixture has no retrieval binding")
    retrieval_binding = deletion_slot.model_bindings[0]
    deletion_repository = _Lifecycle32Repository(active)
    deletion_slots = _Slot32Repository(deletion_slot)
    deletion_request = build_deletion_request(
        active,
        deletion_slot,
        requested_at=deletion_slot.updated_at,
        idempotency_key="step37-delete-before-write",
    )
    deletion_runner = BeforeCallbackFailureRunner(
        CredentialPurpose.APPLICATION_DATABASE
    )
    deletion_service = PersonalMemoryLifecycle32Service(
        deletion_runner,
        lifecycle_repository=deletion_repository,
        slot_repository=deletion_slots,
        trusted_clock=MutableTrustedClock(deletion_request.requested_at),
    )
    try:
        deletion_service.delete(
            deletion_request,
            authenticated_owner_user_id=active.proposal.owner_user_id,
        )
    except BeforeCallbackFailure:
        pass
    else:
        raise AssertionError("deletion pre-write failure was not injected")
    if deletion_repository.deletions:
        raise AssertionError("deletion pre-write failure persisted a tombstone")
    if deletion_slots.slot != deletion_slot or deletion_slots.binding_delete_calls:
        raise AssertionError("deletion pre-write failure partially changed the slot")

    deletion, deleted_slot, deletion_replay = deletion_service.delete(
        deletion_request,
        authenticated_owner_user_id=active.proposal.owner_user_id,
    )
    duplicate_deletion, duplicate_deleted_slot, exact_deletion_replay = (
        deletion_service.delete(
            deletion_request,
            authenticated_owner_user_id=active.proposal.owner_user_id,
        )
    )
    if (
        deletion_replay
        or not exact_deletion_replay
        or duplicate_deletion != deletion
        or duplicate_deleted_slot != deleted_slot
        or len(deletion_repository.deletions) != 1
        or deletion_slots.binding_delete_calls != 1
        or deleted_slot.state is not deletion.slot_state
        or deleted_slot.model_bindings
        or deletion.patch_hash != active_patch.patch_hash
        or not deletion.logical_delete
        or deletion.physical_delete
    ):
        raise AssertionError(
            "deletion retry/replay did not produce one exact logical tombstone"
        )

    retrieval_repository = InMemoryRetrievalRepository(
        (retrieval_candidate(active),)
    )
    retrieval_service = ActivePatchRetrievalService(
        MemoryRunner(CredentialPurpose.APPLICATION_DATABASE),
        slot_repository=RetrievalSlotRepository(deleted_slot),
        retrieval_repository=retrieval_repository,
    )
    retrieval_request, route, temporal = request_for(retrieval_binding)
    retrieval_result, retrieval_context = retrieval_service.retrieve(
        retrieval_request,
        route=route,
        temporal_result=temporal,
    )
    if (
        retrieval_result.eligible_patches
        or retrieval_context.ordered_active_patches
        or retrieval_repository.calls
    ):
        raise AssertionError("Step 31 returned or queried a logically deleted patch")

    return (
        FailureRecoveryCaseResult.build(
            case_id="personal-memory.supersession-before-write",
            failure_domain=FailureDomain.PERSONAL_MEMORY_LIFECYCLE,
            failure_point=FailurePoint.PM_LIFECYCLE_BEFORE_COMMIT,
            subject_hash=supersession.supersession_hash,
            attempt_count=3,
            recovery_status=RecoveryStatus.RECOVERED_BY_RETRY,
            final_semantic_state=(
                "NO_RELATION_THEN_SUPERSEDED_ONCE_EXACT_REPLAY_HISTORY_PRESERVED"
            ),
            reason_codes=(
                "SUPERSESSION_EXACT_REPLAY",
                "SUPERSESSION_PREWRITE_FAILED_CLOSED",
                "SUPERSESSION_HISTORY_PRESERVED",
            ),
        ),
        FailureRecoveryCaseResult.build(
            case_id="personal-memory.delete-before-write",
            failure_domain=FailureDomain.PERSONAL_MEMORY_LIFECYCLE,
            failure_point=FailurePoint.PM_LIFECYCLE_BEFORE_COMMIT,
            subject_hash=deletion.result_hash,
            attempt_count=3,
            recovery_status=RecoveryStatus.RECOVERED_BY_RETRY,
            final_semantic_state=(
                "NO_TOMBSTONE_THEN_DELETED_ONCE_EXACT_REPLAY_RETRIEVAL_SUPPRESSED"
            ),
            reason_codes=(
                "DELETE_EXACT_REPLAY",
                "DELETE_PREWRITE_FAILED_CLOSED",
                "STEP31_DELETED_PATCH_SUPPRESSED",
            ),
        ),
    )


def run_personal_memory_lifecycle_recovery_campaigns(
) -> tuple[FailureRecoveryCaseResult, ...]:
    active = lifecycle_chain()[-1]
    slot = fixture().slot
    repository = _Lifecycle32Repository(active)
    slots = _Slot32Repository(slot)

    revocation_request = build_revocation_request(
        active,
        reason_codes=(Step32ReasonCode.REVOCATION_CREATED,),
        effective_at=active.updated_at + timedelta(seconds=1),
        idempotency_key="step37-revocation-ack-lost",
    )
    revocation_runner = AcknowledgementLossRunner(
        CredentialPurpose.APPLICATION_DATABASE
    )
    revocation_service = PersonalMemoryLifecycle32Service(
        revocation_runner,
        lifecycle_repository=repository,
        slot_repository=slots,
        trusted_clock=MutableTrustedClock(revocation_request.effective_at),
    )
    try:
        revocation_service.revoke(
            revocation_request,
            actor_type=Step32ActorType.HUMAN_OWNER,
            authenticated_actor_id=active.proposal.owner_user_id,
        )
    except AcknowledgementLost:
        pass
    else:
        raise AssertionError("revocation acknowledgement loss was not injected")
    revocation, replay = revocation_service.revoke(
        revocation_request,
        actor_type=Step32ActorType.HUMAN_OWNER,
        authenticated_actor_id=active.proposal.owner_user_id,
    )
    if not replay or len(repository.revocations) != 1:
        raise AssertionError("revocation replay duplicated the lifecycle record")

    export_request = build_lifecycle_export_request(
        slot,
        requested_at=revocation_request.effective_at + timedelta(seconds=1),
        idempotency_key="step37-export-prewrite-interrupted",
    )
    export_runner = BeforeCallbackFailureRunner(
        CredentialPurpose.APPLICATION_DATABASE
    )
    export_service = PersonalMemoryLifecycle32Service(
        export_runner,
        lifecycle_repository=repository,
        slot_repository=slots,
        trusted_clock=MutableTrustedClock(export_request.requested_at),
    )
    try:
        export_service.export(
            export_request,
            authenticated_owner_user_id=slot.owner_user_id,
        )
    except BeforeCallbackFailure:
        pass
    else:
        raise AssertionError("export prewrite interruption was not injected")
    if repository.exports:
        raise AssertionError("interrupted export exposed a ready bundle")
    bundle, export_replay = export_service.export(
        export_request,
        authenticated_owner_user_id=slot.owner_user_id,
    )
    if export_replay or len(repository.exports) != 1:
        raise AssertionError("explicit export retry did not create one bundle")

    return (
        FailureRecoveryCaseResult.build(
            case_id="personal-memory.revocation-ack-lost",
            failure_domain=FailureDomain.PERSONAL_MEMORY_LIFECYCLE,
            failure_point=FailurePoint.PM_LIFECYCLE_AFTER_COMMIT_ACK_LOST,
            subject_hash=revocation.revocation_hash,
            attempt_count=2,
            recovery_status=RecoveryStatus.RECOVERED_BY_IDEMPOTENT_REPLAY,
            final_semantic_state="REVOKED_ONCE_CONTENT_UNCHANGED",
            reason_codes=("REVOCATION_EXACT_REPLAY",),
        ),
        FailureRecoveryCaseResult.build(
            case_id="personal-memory.export-prewrite-interrupted",
            failure_domain=FailureDomain.PERSONAL_MEMORY_LIFECYCLE,
            failure_point=FailurePoint.PM_EXPORT_INTERRUPTED,
            subject_hash=bundle.bundle_hash,
            attempt_count=2,
            recovery_status=RecoveryStatus.RECOVERED_BY_RETRY,
            final_semantic_state="NO_READY_BUNDLE_THEN_OWNER_EXPORT_READY_ONCE",
            reason_codes=("EXPORT_PREWRITE_FAILED_CLOSED",),
        ),
    )


def run_personal_memory_recovery_campaigns() -> tuple[FailureRecoveryCaseResult, ...]:
    value, lifecycle, clock, common = _personal_memory_services()
    approval_runner = AcknowledgementLossRunner(
        CredentialPurpose.APPLICATION_DATABASE
    )
    approval_service = PersonalMemoryApprovalService(approval_runner, **common)
    approval_request = build_personal_memory_approval_request(
        value.awaiting,
        approval_nonce="step37-approval-ack-lost",
        requested_at=value.awaiting.updated_at + timedelta(seconds=1),
    )
    try:
        approval_service.approve(
            approval_request,
            authenticated_actor_user_id=value.slot.owner_user_id,
        )
    except AcknowledgementLost:
        pass
    else:
        raise AssertionError("approval acknowledgement loss was not injected")
    approved, approval_receipt, approval_replay = approval_service.approve(
        approval_request,
        authenticated_actor_user_id=value.slot.owner_user_id,
    )
    if not approval_replay or len(lifecycle.approvals) != 1:
        raise AssertionError("approval replay was not exactly idempotent")

    commit_runner = AcknowledgementLossRunner(
        CredentialPurpose.PERSONAL_MEMORY_COMMIT_DATABASE
    )
    commit_service = PersonalMemoryCommitHelper(commit_runner, **common)
    commit_request = build_personal_memory_commit_request(
        approved,
        commit_idempotency_key="step37-commit-ack-lost",
        requested_at=approved.updated_at + timedelta(seconds=1),
    )
    clock.current = commit_request.requested_at
    try:
        commit_service.commit(commit_request)
    except AcknowledgementLost:
        pass
    else:
        raise AssertionError("commit acknowledgement loss was not injected")
    committed, commit_receipt, commit_replay = commit_service.commit(commit_request)
    if not commit_replay or len(lifecycle.commits) != 1:
        raise AssertionError("commit replay was not exactly idempotent")

    activation_runner = AcknowledgementLossRunner(
        CredentialPurpose.PERSONAL_MEMORY_COMMIT_DATABASE
    )
    activation_service = PersonalMemoryActivationService(
        activation_runner, **common
    )
    activation_request = build_personal_memory_activation_request(
        committed,
        activation_idempotency_key="step37-activation-ack-lost",
        requested_at=committed.updated_at + timedelta(seconds=1),
    )
    clock.current = activation_request.requested_at
    with mock.patch.object(
        PersonalMemoryActivationService,
        "_proposal_id_for_patch",
        return_value=committed.proposal.proposal_id,
    ):
        try:
            activation_service.activate(activation_request)
        except AcknowledgementLost:
            pass
        else:
            raise AssertionError("activation acknowledgement loss was not injected")
        active, activation_receipt, activation_replay = activation_service.activate(
            activation_request
        )
    if (
        not activation_replay
        or len(lifecycle.activations) != 1
        or len(lifecycle.events) != 3
        or active.committed_patch.patch_statement_sha256
        != committed.committed_patch.patch_statement_sha256
    ):
        raise AssertionError("activation replay violated lifecycle integrity")

    common_result = {
        "attempt_count": 2,
        "recovery_status": RecoveryStatus.RECOVERED_BY_IDEMPOTENT_REPLAY,
        "duplicate_side_effect_count": 0,
        "authority_violation_count": 0,
        "integrity_violation_count": 0,
    }
    return (
        FailureRecoveryCaseResult.build(
            case_id="personal-memory.approval-ack-lost",
            failure_domain=FailureDomain.PERSONAL_MEMORY_APPROVAL,
            failure_point=FailurePoint.PM_AFTER_APPROVAL_ACK_LOST,
            subject_hash=approval_receipt.receipt_hash,
            final_semantic_state="APPROVED_ONCE",
            reason_codes=("APPROVAL_EXACT_REPLAY",),
            **common_result,
        ),
        FailureRecoveryCaseResult.build(
            case_id="personal-memory.commit-ack-lost",
            failure_domain=FailureDomain.PERSONAL_MEMORY_COMMIT,
            failure_point=FailurePoint.PM_AFTER_COMMIT_ACK_LOST,
            subject_hash=commit_receipt.receipt_hash,
            final_semantic_state="COMMITTED_ONCE",
            reason_codes=("COMMIT_EXACT_REPLAY",),
            **common_result,
        ),
        FailureRecoveryCaseResult.build(
            case_id="personal-memory.activation-ack-lost",
            failure_domain=FailureDomain.PERSONAL_MEMORY_ACTIVATION,
            failure_point=FailurePoint.PM_AFTER_ACTIVATION_ACK_LOST,
            subject_hash=activation_receipt.receipt_hash,
            final_semantic_state="ACTIVE_ONCE",
            reason_codes=("ACTIVATION_EXACT_REPLAY",),
            **common_result,
        ),
    )


def run_audit_recovery_campaigns() -> tuple[FailureRecoveryCaseResult, ...]:
    pre_repository = MemoryRepository()
    pre_runner = BeforeCallbackFailureRunner(
        CredentialPurpose.AUDIT_APPENDER_DATABASE
    )
    pre_service = AuditLedgerService(pre_runner, repository=pre_repository)
    pre_value = draft(36)
    try:
        pre_service.append_event(
            pre_value,
            authenticated_tenant_id=pre_value.tenant_id,
            authenticated_actor_type=pre_value.actor_type,
            authenticated_actor_id=pre_value.actor_id,
        )
    except BeforeCallbackFailure:
        pass
    else:
        raise AssertionError("audit pre-append failure was not injected")
    if pre_repository.entries or pre_repository.heads:
        raise AssertionError("audit pre-append failure created a false audited success")
    pre_entry, pre_replay = pre_service.append_event(
        pre_value,
        authenticated_tenant_id=pre_value.tenant_id,
        authenticated_actor_type=pre_value.actor_type,
        authenticated_actor_id=pre_value.actor_id,
    )
    pre_verification = verify_audit_chain(
        pre_entry.envelope.chain_id, tuple(pre_repository.entries)
    )
    if pre_replay or len(pre_repository.entries) != 1 or not pre_verification.verified:
        raise AssertionError("audit pre-append retry did not converge once")

    repository = MemoryRepository()
    runner = AcknowledgementLossRunner(CredentialPurpose.AUDIT_APPENDER_DATABASE)
    service = AuditLedgerService(runner, repository=repository)
    value = draft(37)
    try:
        service.append_event(
            value,
            authenticated_tenant_id=value.tenant_id,
            authenticated_actor_type=value.actor_type,
            authenticated_actor_id=value.actor_id,
        )
    except AcknowledgementLost:
        pass
    else:
        raise AssertionError("audit acknowledgement loss was not injected")
    entry, replay = service.append_event(
        value,
        authenticated_tenant_id=value.tenant_id,
        authenticated_actor_type=value.actor_type,
        authenticated_actor_id=value.actor_id,
    )
    verification = verify_audit_chain(entry.envelope.chain_id, tuple(repository.entries))
    if not replay or len(repository.entries) != 1 or not verification.verified:
        raise AssertionError("audit replay duplicated or broke the hash chain")
    return (
        FailureRecoveryCaseResult.build(
            case_id="audit.before-append",
            failure_domain=FailureDomain.AUDIT_LEDGER,
            failure_point=FailurePoint.AUDIT_BEFORE_APPEND,
            subject_hash=pre_entry.envelope.event_hash,
            attempt_count=2,
            recovery_status=RecoveryStatus.RECOVERED_BY_RETRY,
            final_semantic_state="NO_FALSE_SUCCESS_THEN_ONE_CHAIN_EVENT",
            reason_codes=("AUDIT_PREAPPEND_FAILED_CLOSED", "AUDIT_CHAIN_VERIFIED"),
        ),
        FailureRecoveryCaseResult.build(
            case_id="audit.append-ack-lost",
            failure_domain=FailureDomain.AUDIT_LEDGER,
            failure_point=FailurePoint.AUDIT_AFTER_APPEND_ACK_LOST,
            subject_hash=entry.envelope.event_hash,
            attempt_count=2,
            recovery_status=RecoveryStatus.RECOVERED_BY_IDEMPOTENT_REPLAY,
            final_semantic_state="ONE_CHAIN_EVENT_VERIFIED",
            reason_codes=("AUDIT_EXACT_REPLAY", "AUDIT_CHAIN_VERIFIED"),
        ),
    )


def _review_handoff_fixture(*, suffix: str):
    repository = MemoryReviewRepository()
    reviewer_runner = MemoryRunner(CredentialPurpose.HUMAN_REVIEWER_DATABASE)
    service_runner = MemoryRunner(CredentialPurpose.REVIEW_SERVICE_DATABASE)
    clock = FrozenClock()
    intake = ReviewCaseIntakeService(
        service_runner, repository=repository, trusted_clock=clock
    )
    workspace = HumanReviewWorkspaceService(
        reviewer_runner, repository=repository, trusted_clock=clock
    )
    case, context = case_fixture()
    reviewer = principal()
    repository.authorizations[
        (
            REVIEW_TENANT,
            REVIEWER,
            reviewer.reviewer_role,
            case.case_type,
            REVIEW_OWNER,
        )
    ] = build_reviewer_authorization(
        reviewer,
        case_type=case.case_type,
        owner_user_id=REVIEW_OWNER,
        granted_at=REVIEW_NOW - timedelta(minutes=1),
    )
    intake.create_case(
        case,
        context,
        authenticated_tenant_id=REVIEW_TENANT,
        authenticated_owner_user_id=REVIEW_OWNER,
    )
    claim = build_claim_review_case_request(
        case,
        reviewer,
        requested_at=REVIEW_NOW,
        idempotency_key=f"step37-review-claim-{suffix}",
    )
    claimed, _, _ = workspace.claim_case(claim, reviewer)
    detail = workspace.get_detail(
        tenant_id=REVIEW_TENANT,
        review_case_id=claimed.review_case_id,
        principal=reviewer,
    )
    from aioa_memory_kernel.review_workspace import SubmitReviewDecision
    from aioa_memory_kernel.review_workspace import ReviewReasonCode, ReviewState
    from aioa_memory_kernel.review_workspace import STEP34_SCHEMA_VERSION

    command = SubmitReviewDecision(
        schema_version=STEP34_SCHEMA_VERSION,
        tenant_id=REVIEW_TENANT,
        review_case_id=claimed.review_case_id,
        review_case_hash=claimed.case_hash,
        subject_hash=claimed.subject_hash,
        reviewer_principal_hash=reviewer.principal_hash,
        reviewer_id=REVIEWER,
        reviewer_role=reviewer.reviewer_role,
        decision_type=ReviewDecisionType.REJECT_ANSWER,
        decision_reason_codes=(ReviewReasonCode.REVIEW_DECISION_RECORDED,),
        reviewer_note="Bounded Step 37 recovery decision.",
        context_digest=detail.detail_hash,
        audit_verification_result_hash=claimed.audit_verification_result_hash,
        expected_state=ReviewState.CLAIMED,
        expected_state_version=2,
        decided_at=REVIEW_NOW,
        idempotency_key=f"step37-review-decision-{suffix}",
    )
    in_review, decision, decision_receipt, _ = workspace.record_decision(
        command, reviewer
    )
    request = build_review_handoff_request(
        in_review,
        decision,
        decision_receipt,
        requested_at=REVIEW_NOW,
        idempotency_key=f"step37-review-handoff-{suffix}",
    )
    return repository, clock, in_review, request, ReviewState


def run_review_handoff_recovery_campaigns(
) -> tuple[FailureRecoveryCaseResult, ...]:
    pre_repository, pre_clock, pre_review, pre_request, ReviewState = (
        _review_handoff_fixture(suffix="before-write")
    )
    pre_runner = BeforeCallbackFailureRunner(
        CredentialPurpose.REVIEW_SERVICE_DATABASE
    )
    pre_handoff = ReviewDecisionHandoffService(
        pre_runner, repository=pre_repository, trusted_clock=pre_clock
    )
    try:
        pre_handoff.handoff(
            pre_request,
            authenticated_service_id=STEP34_REVIEW_SERVICE_ACTOR_ID,
            current_subject_hash=pre_review.subject_hash,
        )
    except BeforeCallbackFailure:
        pass
    else:
        raise AssertionError("review pre-handoff failure was not injected")
    if pre_repository.handoffs:
        raise AssertionError("failed handoff recorded a false business success")
    pre_terminal, pre_result, pre_receipt, pre_replay = pre_handoff.handoff(
        pre_request,
        authenticated_service_id=STEP34_REVIEW_SERVICE_ACTOR_ID,
        current_subject_hash=pre_review.subject_hash,
    )
    if (
        pre_replay
        or not pre_receipt.succeeded
        or not pre_result.accepted_for_typed_downstream
        or len(pre_repository.handoffs) != 1
        or pre_terminal.review_state is not ReviewState.RESOLVED
    ):
        raise AssertionError("review pre-handoff retry did not resolve exactly once")

    repository, clock, in_review, request, ReviewState = _review_handoff_fixture(
        suffix="ack-lost"
    )
    handoff_runner = AcknowledgementLossRunner(
        CredentialPurpose.REVIEW_SERVICE_DATABASE
    )
    handoff = ReviewDecisionHandoffService(
        handoff_runner, repository=repository, trusted_clock=clock
    )
    try:
        handoff.handoff(
            request,
            authenticated_service_id=STEP34_REVIEW_SERVICE_ACTOR_ID,
            current_subject_hash=in_review.subject_hash,
        )
    except AcknowledgementLost:
        pass
    else:
        raise AssertionError("review handoff acknowledgement loss was not injected")
    terminal, result, receipt, replay = handoff.handoff(
        request,
        authenticated_service_id=STEP34_REVIEW_SERVICE_ACTOR_ID,
        current_subject_hash=in_review.subject_hash,
    )
    if (
        not replay
        or not receipt.succeeded
        or not result.accepted_for_typed_downstream
        or len(repository.handoffs) != 1
        or terminal.review_state is not ReviewState.RESOLVED
    ):
        raise AssertionError("review handoff replay did not converge exactly")
    return (
        FailureRecoveryCaseResult.build(
            case_id="review.before-handoff",
            failure_domain=FailureDomain.REVIEW_HANDOFF,
            failure_point=FailurePoint.REVIEW_BEFORE_HANDOFF,
            subject_hash=pre_receipt.receipt_hash,
            attempt_count=2,
            recovery_status=RecoveryStatus.RECOVERED_BY_RETRY,
            final_semantic_state="UNRESOLVED_THEN_RESOLVED_ONCE",
            reason_codes=("REVIEW_PREHANDOFF_FAILED_CLOSED",),
        ),
        FailureRecoveryCaseResult.build(
            case_id="review.handoff-ack-lost",
            failure_domain=FailureDomain.REVIEW_HANDOFF,
            failure_point=FailurePoint.REVIEW_AFTER_HANDOFF_ACK_LOST,
            subject_hash=receipt.receipt_hash,
            attempt_count=2,
            recovery_status=RecoveryStatus.RECOVERED_BY_IDEMPOTENT_REPLAY,
            final_semantic_state="RESOLVED_ONCE_AFTER_TYPED_HANDOFF",
            reason_codes=("REVIEW_HANDOFF_EXACT_REPLAY",),
        ),
    )


def run_process_restart_campaign() -> FailureRecoveryCaseResult:
    effect, attempts = run_crash_after_durable_write_case()
    return FailureRecoveryCaseResult.build(
        case_id="process.crash-after-durable-write",
        failure_domain=FailureDomain.PROCESS_CRASH,
        failure_point=FailurePoint.PROCESS_AFTER_DURABLE_WRITE,
        subject_hash=effect.result_hash,
        attempt_count=attempts,
        recovery_status=RecoveryStatus.RECOVERED_BY_IDEMPOTENT_REPLAY,
        final_semantic_state="DURABLE_RESULT_REDISCOVERED",
        reason_codes=("PROCESS_RESTART_REPLAYED",),
    )


class Step37RecoveryIdempotencyTests(unittest.TestCase):
    def test_serialization_and_commit_ack_loss_produce_one_effect(self) -> None:
        values = run_database_recovery_campaigns()
        self.assertEqual(len(values), 6)
        self.assertTrue(all(value.duplicate_side_effect_count == 0 for value in values))

    def test_process_restart_rediscovers_one_durable_effect(self) -> None:
        value = run_process_restart_campaign()
        self.assertIs(
            value.recovery_status, RecoveryStatus.RECOVERED_BY_IDEMPOTENT_REPLAY
        )

    def test_personal_memory_phase_ack_losses_replay_exact_receipts(self) -> None:
        values = run_personal_memory_recovery_campaigns()
        self.assertEqual(
            {value.final_semantic_state for value in values},
            {"APPROVED_ONCE", "COMMITTED_ONCE", "ACTIVE_ONCE"},
        )

    def test_personal_memory_prewrite_failures_leave_no_partial_state(self) -> None:
        values = run_personal_memory_prewrite_failure_campaigns()
        self.assertEqual(len(values), 3)
        self.assertTrue(all(value.duplicate_side_effect_count == 0 for value in values))

    def test_step32_lifecycle_prewrite_retries_preserve_history_and_suppress_delete(
        self,
    ) -> None:
        values = run_personal_memory_lifecycle_prewrite_campaigns()
        self.assertEqual(
            {value.case_id for value in values},
            {
                "personal-memory.supersession-before-write",
                "personal-memory.delete-before-write",
            },
        )
        self.assertTrue(all(value.attempt_count == 3 for value in values))
        self.assertTrue(all(value.duplicate_side_effect_count == 0 for value in values))

    def test_audit_ack_loss_reuses_event_and_chain_verifies(self) -> None:
        values = run_audit_recovery_campaigns()
        self.assertEqual(len(values), 2)
        self.assertIn(
            "ONE_CHAIN_EVENT_VERIFIED",
            {value.final_semantic_state for value in values},
        )

    def test_lifecycle_revocation_replay_and_export_prewrite_retry_once(
        self,
    ) -> None:
        values = run_personal_memory_lifecycle_recovery_campaigns()
        self.assertEqual(len(values), 2)
        self.assertTrue(all(item.duplicate_side_effect_count == 0 for item in values))
        export = next(
            item
            for item in values
            if item.failure_point is FailurePoint.PM_EXPORT_INTERRUPTED
        )
        self.assertIs(export.recovery_status, RecoveryStatus.RECOVERED_BY_RETRY)
        self.assertEqual(
            export.final_semantic_state,
            "NO_READY_BUNDLE_THEN_OWNER_EXPORT_READY_ONCE",
        )

    def test_receipt_adapter_reconstructs_stable_audit_draft(self) -> None:
        value = fixture()
        approval_request = build_personal_memory_approval_request(
            value.awaiting,
            approval_nonce="step37-audit-reconstruction",
            requested_at=value.awaiting.updated_at + timedelta(seconds=1),
        )
        from aioa_memory_kernel.personal_memory import approve_personal_memory_patch

        approved = approve_personal_memory_patch(
            value.awaiting,
            approval_request,
            authenticated_actor_user_id=value.slot.owner_user_id,
            approved_at=approval_request.requested_at,
        )
        first = approval_receipt_event(approved.approval_receipt)
        reconstructed = approval_receipt_event(approved.approval_receipt)
        self.assertEqual(first.draft_hash, reconstructed.draft_hash)
        self.assertEqual(first.recorded_at, approved.approval_receipt.approved_at)

    def test_review_handoff_ack_loss_resolves_once(self) -> None:
        values = run_review_handoff_recovery_campaigns()
        self.assertEqual(
            {value.final_semantic_state for value in values},
            {"UNRESOLVED_THEN_RESOLVED_ONCE", "RESOLVED_ONCE_AFTER_TYPED_HANDOFF"},
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
