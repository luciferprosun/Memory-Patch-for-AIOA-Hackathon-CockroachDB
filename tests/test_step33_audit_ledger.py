"""Step 33 canonical event, append, chain, adapter, and DB-boundary tests."""

from __future__ import annotations

import dataclasses
import inspect
import json
import threading
import unittest
from datetime import UTC, datetime, timedelta

from tests._support import REPOSITORY_ROOT
from tests.test_step26_verified_answer_output import hat_lineage
from tests.test_step30_user_approval_commit_activation import lifecycle_chain
from tests.test_step32_personal_memory_lifecycle import (
    Step32SharedPromotionTests,
    active_pair,
    pending_slot,
)

from aioa_memory_kernel.audit_ledger import (
    STEP33_AUDIT_SCHEMA_VERSION,
    STEP33_GENESIS_SENTINEL,
    AuditActorType,
    AuditChainHead,
    AuditEventDraft,
    AuditEventType,
    AuditLedgerEntry,
    AuditLedgerCockroachRepository,
    AuditLedgerError,
    AuditLedgerService,
    AuditReasonCode,
    AuditSubjectType,
    activation_receipt_event,
    approval_receipt_event,
    bounded_answer_failure_event,
    audit_to_jsonb,
    build_audit_event_envelope,
    build_audit_ledger_entry,
    commit_receipt_event,
    compute_audit_chain_id,
    deletion_event,
    parse_audit_ledger_entry,
    revocation_event,
    shared_promotion_event,
    supersession_event,
    verify_audit_chain,
    verify_audit_event_draft,
    verified_answer_event,
)
from aioa_memory_kernel.answers import VerifiedAnswerService
from aioa_memory_kernel.contracts.exceptions import (
    ContractValidationError,
    IntegrityError,
)
from aioa_memory_kernel.contracts.serialization import canonical_sha256
from aioa_memory_kernel.persistence import SerializableTransactionRunner
from aioa_memory_kernel.routing import KnowledgePolicyDecision
from aioa_memory_kernel.personal_memory import (
    Step32ActorType,
    Step32ReasonCode,
    build_deletion_request,
    build_revocation_request,
    build_supersession_request,
    complete_logical_deletion,
    create_patch_revocation,
    create_patch_supersession,
)


ROOT = REPOSITORY_ROOT
NOW = datetime(2042, 2, 3, 4, 5, 6, tzinfo=UTC)


def draft(index: int = 1, *, owner: str = "audit-owner-a") -> AuditEventDraft:
    return AuditEventDraft(
        event_type=AuditEventType.POLICY_BLOCKED,
        tenant_id="audit-tenant-a",
        owner_user_id=owner,
        subject_type=AuditSubjectType.SECURITY_EVENT,
        subject_id=f"security-subject-{index}",
        subject_hash=canonical_sha256({"subject": index}),
        actor_type=AuditActorType.SYSTEM_POLICY,
        actor_id="audit-system-policy-1a",
        idempotency_key=f"audit-idempotency-{index}",
        occurred_at=NOW + timedelta(seconds=index),
        recorded_at=NOW + timedelta(seconds=index),
        event_payload={"decision": "BLOCKED", "ordinal": index},
        reason_codes=(AuditReasonCode.AUDIT_EVENT_APPENDED,),
        policy_id="audit-test-policy",
        policy_version="1",
        policy_digest=canonical_sha256("audit-test-policy-1"),
        lineage_hashes={"input_hash": canonical_sha256({"input": index})},
    )


def chain(count: int = 4) -> tuple[AuditLedgerEntry, ...]:
    result = []
    previous = STEP33_GENESIS_SENTINEL
    for index in range(1, count + 1):
        value = draft(index)
        envelope = build_audit_event_envelope(
            value, sequence_number=index, previous_event_hash=previous
        )
        entry = build_audit_ledger_entry(envelope, value.event_payload)
        result.append(entry)
        previous = envelope.event_hash
    return tuple(result)


class MemoryRunner(SerializableTransactionRunner):
    def __init__(self) -> None:
        self.lock = threading.RLock()

    def run(self, context, callback, *, operation_kind=None):
        with self.lock:
            return callback(object())


class MemoryRepository:
    def __init__(self) -> None:
        self.entries: list[AuditLedgerEntry] = []
        self.heads: dict[str, AuditChainHead] = {}

    def get_replay(self, transaction, *, tenant_id, chain_id, idempotency_key):
        return next(
            (
                item
                for item in self.entries
                if item.envelope.tenant_id == tenant_id
                and item.envelope.chain_id == chain_id
                and item.envelope.idempotency_key == idempotency_key
            ),
            None,
        )

    def lock_chain_head(self, transaction, *, tenant_id, owner_user_id, updated_at):
        chain_id = compute_audit_chain_id(tenant_id, owner_user_id)
        if chain_id not in self.heads:
            self.heads[chain_id] = AuditChainHead(
                tenant_id=tenant_id,
                owner_user_id=owner_user_id,
                chain_id=chain_id,
                last_sequence=0,
                last_event_hash=STEP33_GENESIS_SENTINEL,
                head_version=0,
                updated_at=updated_at,
            )
        return self.heads[chain_id]

    def insert_entry(self, transaction, entry):
        if any(
            existing.envelope.event_id == entry.envelope.event_id
            or (
                existing.envelope.chain_id == entry.envelope.chain_id
                and existing.envelope.sequence_number == entry.envelope.sequence_number
            )
            for existing in self.entries
        ):
            raise AssertionError("duplicate event")
        self.entries.append(entry)

    def advance_chain_head(self, transaction, *, current, entry):
        envelope = entry.envelope
        head = AuditChainHead(
            tenant_id=current.tenant_id,
            owner_user_id=current.owner_user_id,
            chain_id=current.chain_id,
            last_sequence=envelope.sequence_number,
            last_event_hash=envelope.event_hash,
            head_version=current.head_version + 1,
            updated_at=envelope.recorded_at,
        )
        self.heads[current.chain_id] = head
        return head

    def get_chain_head(self, transaction, *, tenant_id, owner_user_id, chain_id):
        return self.heads.get(chain_id)

    def load_range(
        self,
        transaction,
        *,
        tenant_id,
        owner_user_id,
        chain_id,
        start_sequence,
        end_sequence,
        maximum_events,
        event_type_values=(),
    ):
        selected = [
            item
            for item in self.entries
            if item.envelope.tenant_id == tenant_id
            and item.envelope.owner_user_id == owner_user_id
            and item.envelope.chain_id == chain_id
            and item.envelope.sequence_number >= start_sequence
            and (end_sequence is None or item.envelope.sequence_number <= end_sequence)
            and (
                not event_type_values
                or item.envelope.event_type.value in event_type_values
            )
        ]
        return tuple(sorted(selected, key=lambda item: item.envelope.sequence_number))[
            : maximum_events + 1
        ]

    def predecessor_hash(
        self,
        transaction,
        *,
        tenant_id,
        owner_user_id,
        chain_id,
        start_sequence,
    ):
        if start_sequence == 1:
            return STEP33_GENESIS_SENTINEL
        for item in self.entries:
            if (
                item.envelope.chain_id == chain_id
                and item.envelope.sequence_number == start_sequence - 1
            ):
                return item.envelope.event_hash
        return None


class Step33EventContractTests(unittest.TestCase):
    def test_event_is_frozen_canonical_deterministic_and_roundtrips(self):
        value = draft()
        duplicate = draft()
        self.assertEqual(value, duplicate)
        self.assertEqual(value.schema_version, STEP33_AUDIT_SCHEMA_VERSION)
        verify_audit_event_draft(value)
        event = chain(1)[0]
        parsed = parse_audit_ledger_entry(audit_to_jsonb(event))
        self.assertEqual(parsed, event)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            value.actor_id = "different"  # type: ignore[misc]

    def test_every_semantic_dimension_changes_hash(self):
        base = draft()
        changed_type = dataclasses.replace(
            base, event_type=AuditEventType.INTEGRITY_FAILURE
        )
        changed_subject = dataclasses.replace(
            base, subject_hash=canonical_sha256("other")
        )
        changed_reason = dataclasses.replace(
            base, reason_codes=(AuditReasonCode.AUDIT_EVENT_INVALID,)
        )
        changed_payload = dataclasses.replace(
            base, event_payload={"decision": "DENIED", "ordinal": 1}
        )
        self.assertEqual(
            len(
                {
                    base.draft_hash,
                    changed_type.draft_hash,
                    changed_subject.draft_hash,
                    changed_reason.draft_hash,
                    changed_payload.draft_hash,
                }
            ),
            5,
        )

    def test_event_and_actor_vocabularies_are_closed(self):
        with self.assertRaises(ContractValidationError):
            dataclasses.replace(draft(), event_type="USER_TEXT")  # type: ignore[arg-type]
        with self.assertRaises(ContractValidationError):
            dataclasses.replace(draft(), actor_type="HUMAN_USER")  # type: ignore[arg-type]

    def test_secret_raw_private_and_machine_content_fail_closed(self):
        for payload in (
            {"api_key": "secret"},
            {"patch_statement": "private"},
            {"safe": "Authorization: bearer token"},
            {"safe": "postgresql://user:credential@db.example/audit"},
            {"safe": "x-amz-signature=not-a-real-signature"},
            {"safe": "sk-proj-not-a-real-key"},
            {"safe": "/home/user/private"},
        ):
            with self.assertRaises(ContractValidationError):
                dataclasses.replace(draft(), event_payload=payload)
        with self.assertRaises(ContractValidationError):
            dataclasses.replace(
                draft(), lineage_hashes={1: canonical_sha256("invalid-key")}
            )

    def test_rehashed_in_memory_draft_tamper_fails_reconstruction(self):
        value = draft()
        object.__setattr__(value, "event_payload", {"decision": "CHANGED"})
        with self.assertRaises(IntegrityError):
            verify_audit_event_draft(value)


class Step33ChainTests(unittest.TestCase):
    def test_empty_chain_is_not_a_verified_genesis(self):
        result = verify_audit_chain(
            compute_audit_chain_id("audit-tenant-a", "audit-owner-a"), ()
        )
        self.assertFalse(result.verified)
        self.assertIn(
            AuditReasonCode.AUDIT_GENESIS_INVALID,
            result.failure_reason_codes,
        )

    def test_genesis_two_event_many_event_and_final_hash(self):
        for size in (1, 2, 20):
            entries = chain(size)
            result = verify_audit_chain(entries[0].envelope.chain_id, entries)
            self.assertTrue(result.verified)
            self.assertEqual(result.event_count, size)
            self.assertEqual(result.last_hash, entries[-1].envelope.event_hash)
            self.assertEqual(result, verify_audit_chain(result.chain_id, entries))

    def test_verification_result_cannot_claim_success_with_failure_metadata(self):
        verified = verify_audit_chain(chain(1)[0].envelope.chain_id, chain(1))
        with self.assertRaises(ContractValidationError):
            dataclasses.replace(verified, failure_sequence=1)
        broken = verify_audit_chain(
            compute_audit_chain_id("audit-tenant-a", "audit-owner-a"), ()
        )
        with self.assertRaises(ContractValidationError):
            dataclasses.replace(broken, failure_sequence=None)

    def test_boolean_values_are_not_integer_sequences_or_counts(self):
        entry = chain(1)[0]
        with self.assertRaises(ContractValidationError):
            dataclasses.replace(entry.append_receipt, sequence_number=True)
        head = AuditChainHead(
            tenant_id="audit-tenant-a",
            owner_user_id="audit-owner-a",
            chain_id=entry.envelope.chain_id,
            last_sequence=1,
            last_event_hash=entry.envelope.event_hash,
            head_version=1,
            updated_at=entry.envelope.recorded_at,
        )
        with self.assertRaises(ContractValidationError):
            dataclasses.replace(head, head_version=True)
        verified = verify_audit_chain(entry.envelope.chain_id, (entry,))
        with self.assertRaises(ContractValidationError):
            dataclasses.replace(verified, event_count=True)

    def test_tamper_matrix_is_detected_without_repair(self):
        scenarios = {}
        payload = list(chain())
        object.__setattr__(payload[1], "event_payload", {"decision": "FORGED"})
        scenarios["payload"] = tuple(payload)

        event_type = list(chain())
        object.__setattr__(
            event_type[1].envelope,
            "event_type",
            AuditEventType.INTEGRITY_FAILURE,
        )
        scenarios["event_type"] = tuple(event_type)

        previous = list(chain())
        object.__setattr__(previous[2].envelope, "previous_event_hash", "0" * 64)
        scenarios["previous"] = tuple(previous)

        scenarios["deleted_middle"] = (chain()[0], *chain()[2:])
        scenarios["reordered"] = tuple(reversed(chain()))

        forged = list(chain(3))
        bad = draft(4)
        forged_event = build_audit_event_envelope(
            bad, sequence_number=4, previous_event_hash="f" * 64
        )
        forged.append(build_audit_ledger_entry(forged_event, bad.event_payload))
        scenarios["forged"] = tuple(forged)

        duplicate = list(chain())
        object.__setattr__(duplicate[2].envelope, "sequence_number", 2)
        scenarios["duplicate_sequence"] = tuple(duplicate)

        duplicate_id = list(chain())
        object.__setattr__(
            duplicate_id[2].envelope,
            "event_id",
            duplicate_id[1].envelope.event_id,
        )
        scenarios["duplicate_event_id"] = tuple(duplicate_id)

        for name, entries in scenarios.items():
            with self.subTest(name=name):
                before = tuple(item.envelope.event_hash for item in entries)
                result = verify_audit_chain(
                    compute_audit_chain_id("audit-tenant-a", "audit-owner-a"),
                    entries,
                )
                self.assertFalse(result.verified)
                self.assertTrue(result.failure_reason_codes)
                self.assertEqual(
                    before, tuple(item.envelope.event_hash for item in entries)
                )

    def test_head_tamper_and_invalid_range_anchor_are_detected(self):
        entries = chain(3)
        head = AuditChainHead(
            tenant_id="audit-tenant-a",
            owner_user_id="audit-owner-a",
            chain_id=entries[0].envelope.chain_id,
            last_sequence=3,
            last_event_hash="0" * 64,
            head_version=3,
            updated_at=entries[-1].envelope.recorded_at,
        )
        result = verify_audit_chain(
            head.chain_id, entries, expected_head=head
        )
        self.assertFalse(result.verified)
        self.assertIn(
            AuditReasonCode.AUDIT_CHAIN_HEAD_MISMATCH,
            result.failure_reason_codes,
        )
        range_result = verify_audit_chain(
            head.chain_id, entries[1:], predecessor_hash="0" * 64
        )
        self.assertFalse(range_result.verified)

        object.__setattr__(head, "last_event_hash", entries[-1].envelope.event_hash)
        object.__setattr__(head, "head_version", 2)
        version_result = verify_audit_chain(
            head.chain_id, entries, expected_head=head
        )
        self.assertFalse(version_result.verified)
        self.assertIn(
            AuditReasonCode.AUDIT_CHAIN_HEAD_MISMATCH,
            version_result.failure_reason_codes,
        )


class Step33AppendServiceTests(unittest.TestCase):
    def test_repository_rehydrates_canonical_pgwire_head_timestamp(self):
        class HeadTransaction:
            def execute(self, _sql, _parameters):
                return None

            def fetch_one(self, _sql, _parameters):
                return {
                    "tenant_id": "audit-tenant-a",
                    "owner_user_id": "audit-owner-a",
                    "chain_id": compute_audit_chain_id(
                        "audit-tenant-a", "audit-owner-a"
                    ),
                    "last_sequence": "0",
                    "last_event_hash": STEP33_GENESIS_SENTINEL,
                    "head_version": "0",
                    "updated_at": NOW.isoformat(),
                }

        head = AuditLedgerCockroachRepository.lock_chain_head(
            HeadTransaction(),
            tenant_id="audit-tenant-a",
            owner_user_id="audit-owner-a",
            updated_at=NOW,
        )
        self.assertEqual(head.updated_at, NOW)

    def service(self):
        repository = MemoryRepository()
        return AuditLedgerService(MemoryRunner(), repository=repository), repository

    def test_append_exact_replay_and_conflicting_replay(self):
        service, repository = self.service()
        first, replay = service.append_event(
            draft(),
            authenticated_tenant_id="audit-tenant-a",
            authenticated_actor_type=AuditActorType.SYSTEM_POLICY,
            authenticated_actor_id="audit-system-policy-1a",
        )
        duplicate, replay = service.append_event(
            draft(),
            authenticated_tenant_id="audit-tenant-a",
            authenticated_actor_type=AuditActorType.SYSTEM_POLICY,
            authenticated_actor_id="audit-system-policy-1a",
        )
        self.assertTrue(replay)
        self.assertEqual(first, duplicate)
        changed = dataclasses.replace(draft(), subject_hash="0" * 64)
        with self.assertRaises(AuditLedgerError) as caught:
            service.append_event(
                changed,
                authenticated_tenant_id="audit-tenant-a",
                authenticated_actor_type=AuditActorType.SYSTEM_POLICY,
                authenticated_actor_id="audit-system-policy-1a",
            )
        self.assertIs(
            caught.exception.reason_code,
            AuditReasonCode.AUDIT_EVENT_REPLAY_CONFLICT,
        )
        self.assertEqual(len(repository.entries), 1)

    def test_append_rechecks_replay_after_serialized_head_lock(self):
        class ReplayAfterLockRepository(MemoryRepository):
            def __init__(self):
                super().__init__()
                existing = chain(1)[0]
                self.entries.append(existing)
                self.heads[existing.envelope.chain_id] = AuditChainHead(
                    tenant_id=existing.envelope.tenant_id,
                    owner_user_id=existing.envelope.owner_user_id,
                    chain_id=existing.envelope.chain_id,
                    last_sequence=1,
                    last_event_hash=existing.envelope.event_hash,
                    head_version=1,
                    updated_at=existing.envelope.recorded_at,
                )
                self.replay_reads = 0

            def get_replay(self, transaction, **kwargs):
                self.replay_reads += 1
                if self.replay_reads == 1:
                    return None
                return super().get_replay(transaction, **kwargs)

        repository = ReplayAfterLockRepository()
        service = AuditLedgerService(MemoryRunner(), repository=repository)
        existing, replay = service.append_event(
            draft(),
            authenticated_tenant_id="audit-tenant-a",
            authenticated_actor_type=AuditActorType.SYSTEM_POLICY,
            authenticated_actor_id="audit-system-policy-1a",
        )
        self.assertTrue(replay)
        self.assertEqual(existing, repository.entries[0])
        self.assertEqual(repository.replay_reads, 2)
        self.assertEqual(len(repository.entries), 1)

    def test_concurrent_append_is_unique_and_contiguous(self):
        service, repository = self.service()
        errors = []

        def append(index):
            try:
                service.append_event(
                    draft(index),
                    authenticated_tenant_id="audit-tenant-a",
                    authenticated_actor_type=AuditActorType.SYSTEM_POLICY,
                    authenticated_actor_id="audit-system-policy-1a",
                )
            except Exception as exc:  # pragma: no cover - asserted below
                errors.append(exc)

        threads = [threading.Thread(target=append, args=(index,)) for index in range(1, 25)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertFalse(errors)
        ordered = sorted(repository.entries, key=lambda item: item.envelope.sequence_number)
        self.assertEqual([item.envelope.sequence_number for item in ordered], list(range(1, 25)))
        self.assertTrue(verify_audit_chain(ordered[0].envelope.chain_id, ordered).verified)

    def test_concurrent_exact_retry_converges_and_changed_key_conflicts(self):
        service, repository = self.service()
        exact_results = []
        exact_barrier = threading.Barrier(2)

        def exact_append():
            exact_barrier.wait()
            exact_results.append(
                service.append_event(
                    draft(40),
                    authenticated_tenant_id="audit-tenant-a",
                    authenticated_actor_type=AuditActorType.SYSTEM_POLICY,
                    authenticated_actor_id="audit-system-policy-1a",
                )
            )

        exact_threads = [threading.Thread(target=exact_append) for _ in range(2)]
        for thread in exact_threads:
            thread.start()
        for thread in exact_threads:
            thread.join()
        self.assertEqual(len(exact_results), 2)
        self.assertEqual(exact_results[0][0], exact_results[1][0])
        self.assertEqual(sorted(item[1] for item in exact_results), [False, True])

        conflicts = []
        conflict_barrier = threading.Barrier(2)
        left = draft(41)
        right = dataclasses.replace(left, subject_hash="0" * 64)

        def conflicting_append(value):
            conflict_barrier.wait()
            try:
                service.append_event(
                    value,
                    authenticated_tenant_id="audit-tenant-a",
                    authenticated_actor_type=AuditActorType.SYSTEM_POLICY,
                    authenticated_actor_id="audit-system-policy-1a",
                )
                conflicts.append("APPENDED")
            except AuditLedgerError as error:
                conflicts.append(error.reason_code.value)

        conflict_threads = [
            threading.Thread(target=conflicting_append, args=(value,))
            for value in (left, right)
        ]
        for thread in conflict_threads:
            thread.start()
        for thread in conflict_threads:
            thread.join()
        self.assertEqual(
            sorted(conflicts),
            ["APPENDED", AuditReasonCode.AUDIT_EVENT_REPLAY_CONFLICT.value],
        )
        self.assertEqual(len(repository.entries), 2)

    def test_actor_authentication_and_human_owner_spoof_fail(self):
        service, _ = self.service()
        with self.assertRaises(AuditLedgerError):
            service.append_event(
                draft(),
                authenticated_tenant_id="audit-tenant-a",
                authenticated_actor_type=AuditActorType.KERNEL,
                authenticated_actor_id="different",
            )
        human = dataclasses.replace(
            draft(), actor_type=AuditActorType.HUMAN_USER, actor_id="other-owner"
        )
        with self.assertRaises(AuditLedgerError):
            service.append_event(
                human,
                authenticated_tenant_id="audit-tenant-a",
                authenticated_actor_type=AuditActorType.HUMAN_USER,
                authenticated_actor_id="other-owner",
            )

        with self.assertRaises(AuditLedgerError) as caught:
            service.append_event(
                draft(),
                authenticated_tenant_id="audit-tenant-b",
                authenticated_actor_type=AuditActorType.SYSTEM_POLICY,
                authenticated_actor_id="audit-system-policy-1a",
            )
        self.assertIs(
            caught.exception.reason_code,
            AuditReasonCode.AUDIT_TENANT_MISMATCH,
        )


class Step33AdapterAndAuthorityTests(unittest.TestCase):
    def test_verified_and_fail_closed_answer_adapters_store_hashes_not_text(self):
        verified_request, verified_authenticator = hat_lineage()
        verified = VerifiedAnswerService(verified_authenticator).finalize(
            verified_request
        ).verified_answer
        self.assertIsNotNone(verified)
        blocked_request, blocked_authenticator = hat_lineage(
            policy_decision=KnowledgePolicyDecision.BLOCK_ANSWER
        )
        blocked = VerifiedAnswerService(blocked_authenticator).finalize(
            blocked_request
        ).bounded_failure
        self.assertIsNotNone(blocked)
        drafts = (
            verified_answer_event(verified, recorded_at=NOW),
            bounded_answer_failure_event(blocked, recorded_at=NOW),
        )
        self.assertEqual(
            tuple(item.event_type for item in drafts),
            (
                AuditEventType.VERIFIED_ANSWER_ASSEMBLED,
                AuditEventType.VERIFIED_ANSWER_BLOCKED,
            ),
        )
        rendered = json.dumps([audit_to_jsonb(item) for item in drafts])
        self.assertNotIn(verified.answer_text, rendered)
        self.assertNotIn(blocked.safe_message, rendered)
        self.assertIn(verified.answer_text_sha256, rendered)
        self.assertIn(blocked.failure_hash, rendered)

    def test_step30_receipt_adapters_bind_hashes_without_private_text(self):
        _, _, approved, _, committed, _, active = lifecycle_chain()
        drafts = (
            approval_receipt_event(approved.approval_receipt, recorded_at=NOW),
            commit_receipt_event(committed.commit_receipt, recorded_at=NOW),
            activation_receipt_event(active.activation_receipt, recorded_at=NOW),
        )
        self.assertEqual(
            tuple(item.event_type for item in drafts),
            (
                AuditEventType.PERSONAL_MEMORY_APPROVED,
                AuditEventType.PERSONAL_MEMORY_COMMITTED,
                AuditEventType.PERSONAL_MEMORY_ACTIVATED,
            ),
        )
        rendered = json.dumps([audit_to_jsonb(item) for item in drafts], sort_keys=True)
        self.assertNotIn(active.committed_patch.patch_statement, rendered)
        self.assertIn(active.activation_receipt.receipt_hash, rendered)

    def test_step32_adapters_bind_terminal_and_review_only_facts(self):
        old, new = active_pair()
        supersession_request = build_supersession_request(
            old,
            new,
            reason_codes=(Step32ReasonCode.SUPERSESSION_CREATED,),
            effective_at=new.updated_at + timedelta(seconds=1),
            idempotency_key="step33-adapter-supersession",
        )
        supersession = create_patch_supersession(
            supersession_request,
            old,
            new,
            authenticated_owner_user_id=old.proposal.owner_user_id,
        )
        active = lifecycle_chain()[-1]
        revocation_request = build_revocation_request(
            active,
            reason_codes=(Step32ReasonCode.REVOCATION_CREATED,),
            effective_at=active.updated_at + timedelta(seconds=1),
            idempotency_key="step33-adapter-revocation",
        )
        revocation = create_patch_revocation(
            revocation_request,
            active,
            actor_type=Step32ActorType.HUMAN_OWNER,
            authenticated_actor_id=active.proposal.owner_user_id,
        )
        slot = pending_slot(active)
        deletion_request = build_deletion_request(
            active,
            slot,
            requested_at=slot.updated_at,
            idempotency_key="step33-adapter-deletion",
        )
        deletion = complete_logical_deletion(
            deletion_request,
            active,
            slot,
            authenticated_owner_user_id=slot.owner_user_id,
        )
        promotion = Step32SharedPromotionTests()._promotion()[4]
        drafts = (
            supersession_event(supersession, recorded_at=NOW),
            revocation_event(revocation, recorded_at=NOW),
            deletion_event(deletion, recorded_at=NOW),
            shared_promotion_event(promotion, recorded_at=NOW),
        )
        self.assertEqual(len({item.draft_hash for item in drafts}), 4)
        promotion_draft = drafts[-1]
        self.assertEqual(
            promotion_draft.event_payload["state"], "SHARED_PROMOTION_PROPOSED"
        )
        self.assertFalse(promotion_draft.event_payload["shared_active"])
        self.assertFalse(promotion_draft.event_payload["source_registry_published"])

    def test_public_api_has_no_business_mutation_or_step34_workspace(self):
        methods = {
            name
            for name, _ in inspect.getmembers(
                AuditLedgerService, predicate=inspect.isfunction
            )
            if not name.startswith("_")
        }
        self.assertEqual(methods, {"append_event", "export_chain", "verify_chain"})
        source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (ROOT / "src/aioa_memory_kernel/audit_ledger").glob("*.py")
        )
        for forbidden in (
            "review_queue",
            "review_dashboard",
            "personal_memory_ui",
            "execute_action",
            "approve_patch",
            "activate_patch",
        ):
            self.assertNotIn(forbidden, source)


class Step33MigrationTests(unittest.TestCase):
    def test_migration_is_append_only_rls_and_manifest_bound(self):
        migration = ROOT / "sql/cockroachdb/migrations/0016_step33_audit_ledger_hash_chain.sql"
        sql = migration.read_text(encoding="utf-8")
        manifest = json.loads(
            (ROOT / "sql/cockroachdb/migrations/manifest.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertIn("BEFORE UPDATE OR DELETE ON memory_patch.audit_events", sql)
        self.assertIn("REVOKE UPDATE, DELETE ON TABLE memory_patch.audit_events", sql)
        self.assertIn("FORCE ROW LEVEL SECURITY", sql)
        self.assertIn("audit_events_s33_chain_sequence_uq", sql)
        for value in (*AuditEventType, *AuditActorType):
            self.assertIn(f"'{value.value}'", sql)
        for fragment in (
            "recorded_at IS NOT NULL",
            "step33_envelope -> 'owner_user_id' IS NOT NULL",
            "step33_envelope -> 'event_hash' IS NOT NULL",
            "step33_append_receipt -> 'receipt_hash' IS NOT NULL",
            "step33_envelope ->> 'hash_domain'",
        ):
            self.assertIn(fragment, sql)
        self.assertIn("(step33_envelope ->> 'recorded_at')::TIMESTAMPTZ", sql)
        self.assertNotIn("BYPASSRLS", sql)
        self.assertNotRegex(sql, r"(?m)^\s*DELETE\s+FROM")
        entry = manifest["migrations"][-1]
        self.assertEqual(entry["filename"], migration.name)
        self.assertEqual(entry["sha256"], __import__("hashlib").sha256(migration.read_bytes()).hexdigest())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
