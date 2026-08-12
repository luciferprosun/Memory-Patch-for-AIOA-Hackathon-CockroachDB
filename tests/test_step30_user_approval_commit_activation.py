"""Step 30 owner approval, technical commit, and activation tests."""

from __future__ import annotations

import dataclasses
import inspect
import json
import unittest
from dataclasses import replace
from datetime import timedelta
from unittest import mock

from tests._support import REPOSITORY_ROOT
from tests.test_step28_correction_candidate_bridge import FakeIdempotency
from tests.test_step29_personal_memory_patch_proposal import (
    InMemoryCandidateRepository,
    InMemoryProposalRepository,
    InMemorySlotRepository,
    fixture,
)

from aioa_memory_kernel.contracts.enums import PatchState, PersonalMemorySpaceState
from aioa_memory_kernel.contracts.exceptions import ContractValidationError, IntegrityError
from aioa_memory_kernel.contracts.serialization import canonical_sha256
from aioa_memory_kernel.persistence import SerializableTransactionRunner
from aioa_memory_kernel.persistence.errors import IdempotencyConflictError
from aioa_memory_kernel.security.credentials import CredentialPurpose
from aioa_memory_kernel.personal_memory import (
    PERSONAL_MEMORY_COMMIT_ROLE,
    STEP30_SCHEMA_VERSION,
    CommittedPersonalMemoryPatch,
    PersonalMemoryActivationService,
    PersonalMemoryApprovalActorType,
    PersonalMemoryApprovalService,
    PersonalMemoryCommitHelper,
    PersonalMemoryPatchLifecycleError,
    PersonalMemoryPatchLifecycleState,
    PersonalMemoryTechnicalActorType,
    Step30ReasonCode,
    activate_personal_memory_patch,
    approve_personal_memory_patch,
    build_personal_memory_activation_request,
    build_personal_memory_approval_request,
    build_personal_memory_commit_request,
    commit_personal_memory_patch,
    lifecycle_state_from_row,
    load_personal_memory_approval_policy,
    parse_personal_memory_patch_lifecycle_state,
    personal_memory_patch_lifecycle_to_jsonb,
    verify_personal_memory_activation_receipt,
    verify_personal_memory_approval_receipt,
    verify_personal_memory_commit_receipt,
    verify_personal_memory_patch_lifecycle_state,
)
from aioa_memory_kernel.personal_memory import lifecycle as lifecycle_models


ROOT = REPOSITORY_ROOT


def lifecycle_chain():
    value = fixture()
    approval_request = build_personal_memory_approval_request(
        value.awaiting,
        approval_nonce="step30-owner-approval-nonce",
        requested_at=value.awaiting.updated_at + timedelta(seconds=1),
    )
    approved = approve_personal_memory_patch(
        value.awaiting,
        approval_request,
        authenticated_actor_user_id=value.slot.owner_user_id,
        approved_at=approval_request.requested_at,
    )
    commit_request = build_personal_memory_commit_request(
        approved,
        commit_idempotency_key="step30-technical-commit",
        requested_at=approved.updated_at + timedelta(seconds=1),
    )
    committed = commit_personal_memory_patch(
        approved,
        commit_request,
        committed_at=commit_request.requested_at,
    )
    activation_request = build_personal_memory_activation_request(
        committed,
        activation_idempotency_key="step30-activation",
        requested_at=committed.updated_at + timedelta(seconds=1),
    )
    active = activate_personal_memory_patch(
        committed,
        activation_request,
        activated_at=activation_request.requested_at,
    )
    return value, approval_request, approved, commit_request, committed, activation_request, active


class Step30ContractTests(unittest.TestCase):
    def test_exact_state_machine_and_content_identity(self):
        value, _, approved, _, committed, _, active = lifecycle_chain()
        self.assertEqual(
            [value.awaiting.state, approved.state, committed.state, active.state],
            [
                PatchState.AWAITING_APPROVAL,
                PatchState.APPROVED,
                PatchState.COMMITTED,
                PatchState.ACTIVE,
            ],
        )
        self.assertEqual(
            [approved.state_version, committed.state_version, active.state_version],
            [5, 6, 7],
        )
        hashes = {
            value.awaiting.proposal.proposal_statement_sha256,
            committed.committed_patch.patch_statement_sha256,
            active.activation_receipt.patch_statement_sha256,
        }
        self.assertEqual(len(hashes), 1)
        self.assertEqual(
            committed.committed_patch.patch_statement,
            value.awaiting.proposal.proposal_statement,
        )
        self.assertFalse(active.canonical_evidence)
        self.assertFalse(active.committed_patch.canonical_evidence)

    def test_contracts_are_frozen_deterministic_and_roundtrip(self):
        _, request, approved, commit_request, committed, activation_request, active = lifecycle_chain()
        duplicate = lifecycle_chain()
        self.assertEqual(active, duplicate[-1])
        verify_personal_memory_approval_receipt(approved.approval_receipt)
        verify_personal_memory_commit_receipt(committed.commit_receipt)
        verify_personal_memory_activation_receipt(active.activation_receipt)
        verify_personal_memory_patch_lifecycle_state(active)
        serialized = personal_memory_patch_lifecycle_to_jsonb(active)
        self.assertEqual(parse_personal_memory_patch_lifecycle_state(serialized), active)
        self.assertEqual(len(request.request_hash), 64)
        self.assertEqual(len(commit_request.request_hash), 64)
        self.assertEqual(len(activation_request.request_hash), 64)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            active.state = PatchState.COMMITTED  # type: ignore[misc]
        tampered = json.loads(json.dumps(serialized))
        tampered["committed_patch"]["patch_statement"] = "different"
        with self.assertRaises((ContractValidationError, IntegrityError)):
            parse_personal_memory_patch_lifecycle_state(tampered)

    def test_json_scope_parser_restores_timestamp_and_string_set_types(self):
        parsed = lifecycle_models._scope_from_json(
            [
                {
                    "name": "knowledge_as_of",
                    "value": "2026-08-01T21:51:24Z",
                    "value_type": "TIMESTAMP",
                    "comparison_mode": "TIMESTAMP",
                    "source": "step38-regression",
                    "required": True,
                },
                {
                    "name": "legal_source_class",
                    "value": ["DE_FEDERAL_OFFICIAL_CONSOLIDATED_LAW"],
                    "value_type": "STRING_SET",
                    "comparison_mode": "IN_SET",
                    "source": "step38-regression",
                    "required": True,
                },
            ]
        )
        self.assertEqual(parsed[0].value.isoformat(), "2026-08-01T21:51:24+00:00")
        self.assertEqual(
            parsed[1].value,
            ("DE_FEDERAL_OFFICIAL_CONSOLIDATED_LAW",),
        )

    def test_owner_human_approval_is_exact_and_non_executable(self):
        value = fixture()
        request = build_personal_memory_approval_request(
            value.awaiting,
            approval_nonce="owner-only",
            requested_at=value.awaiting.updated_at + timedelta(seconds=1),
        )
        with self.assertRaises(PersonalMemoryPatchLifecycleError) as caught:
            approve_personal_memory_patch(
                value.awaiting,
                request,
                authenticated_actor_user_id="different-user",
                approved_at=request.requested_at,
            )
        self.assertIs(caught.exception.reason_code, Step30ReasonCode.APPROVAL_OWNER_MISMATCH)
        approved = approve_personal_memory_patch(
            value.awaiting,
            request,
            authenticated_actor_user_id=value.slot.owner_user_id,
            approved_at=request.requested_at,
        )
        self.assertIs(
            approved.approval_receipt.actor_type,
            PersonalMemoryApprovalActorType.HUMAN_USER,
        )
        self.assertFalse(approved.approval_receipt.execution_authority)
        self.assertIsNone(approved.committed_patch)
        self.assertIsNone(approved.activation_receipt)

    def test_approval_presentation_change_invalidates_request(self):
        value = fixture()
        request = build_personal_memory_approval_request(
            value.awaiting,
            approval_nonce="presentation-bound",
            requested_at=value.awaiting.updated_at + timedelta(seconds=1),
        )
        with self.assertRaises(PersonalMemoryPatchLifecycleError):
            approve_personal_memory_patch(
                value.awaiting,
                replace(request, approval_summary_digest="0" * 64),
                authenticated_actor_user_id=value.slot.owner_user_id,
                approved_at=request.requested_at,
            )
        with self.assertRaises(PersonalMemoryPatchLifecycleError):
            approve_personal_memory_patch(
                value.awaiting,
                replace(request, proposal_hash="0" * 64),
                authenticated_actor_user_id=value.slot.owner_user_id,
                approved_at=request.requested_at,
            )

    def test_three_phase_replay_identities_are_independent_and_owner_scoped(self):
        _, approval, approved, commit, committed, activation, _ = lifecycle_chain()
        self.assertNotEqual(
            approval.approval_replay_identity,
            commit.commit_replay_identity,
        )
        self.assertNotEqual(
            commit.commit_replay_identity,
            activation.activation_replay_identity,
        )
        changed = replace(approval, proposal_id="different-proposal")
        self.assertEqual(
            changed.approval_replay_identity,
            approval.approval_replay_identity,
        )
        self.assertNotEqual(changed.request_hash, approval.request_hash)
        self.assertIs(
            committed.commit_receipt.actor_type,
            PersonalMemoryTechnicalActorType.COMMIT_HELPER,
        )
        self.assertEqual(committed.commit_receipt.authority_role, PERSONAL_MEMORY_COMMIT_ROLE)

    def test_skip_transitions_and_changed_content_are_impossible(self):
        value, approval_request, approved, _, committed, _, _ = lifecycle_chain()
        with self.assertRaises(PersonalMemoryPatchLifecycleError):
            commit_personal_memory_patch(
                approved,
                build_personal_memory_commit_request(
                    approved,
                    commit_idempotency_key="stale",
                    requested_at=approved.updated_at,
                ),
                committed_at=approved.updated_at - timedelta(seconds=1),
            )
        with self.assertRaises((ContractValidationError, PersonalMemoryPatchLifecycleError)):
            approve_personal_memory_patch(
                value.validated,
                approval_request,
                authenticated_actor_user_id=value.slot.owner_user_id,
                approved_at=approval_request.requested_at,
            )
        changed_patch = replace(committed.committed_patch, patch_statement="changed")
        with self.assertRaises(ContractValidationError):
            replace(committed, committed_patch=changed_patch)

    def test_policy_is_hash_bound_and_contains_no_secret(self):
        policy = load_personal_memory_approval_policy()
        self.assertEqual(policy, load_personal_memory_approval_policy())
        self.assertEqual(len(policy.policy_digest), 64)
        serialized = json.dumps(personal_memory_patch_lifecycle_to_jsonb(lifecycle_chain()[-1]))
        for forbidden in ("password", "api_key", "bearer ", "private_key"):
            self.assertNotIn(forbidden, serialized.lower())


class InMemoryLifecycleRepository:
    def __init__(self, initial):
        self.state = initial
        self.approvals = {}
        self.commits = {}
        self.activations = {}
        self.events = []
        self.authorized = True

    def get_state(self, transaction, tenant, owner, proposal_id):
        state = self.state
        proposal = state.proposal if isinstance(state, PersonalMemoryPatchLifecycleState) else state.proposal
        if (tenant, owner, proposal_id) != (
            proposal.tenant_id,
            proposal.owner_user_id,
            proposal.proposal_id,
        ):
            return None
        return state

    def get_approval_replay(self, transaction, **values):
        return self.approvals.get(values["replay_identity"])

    def insert_approval(self, transaction, receipt):
        self.approvals[receipt.approval_replay_identity] = {
            "proposal_id": receipt.proposal_id,
            "step30_request_hash": receipt.request_hash,
            "step30_approval_receipt_hash": receipt.receipt_hash,
        }

    def assert_commit_helper_authority(self, transaction):
        if not self.authorized:
            raise RuntimeError("not authorized")

    def get_commit_replay(self, transaction, **values):
        return self.commits.get(values["replay_identity"])

    def insert_commit_and_patch(self, transaction, state):
        receipt = state.commit_receipt
        self.commits[receipt.commit_replay_identity] = {
            "proposal_id": receipt.proposal_id,
            "step30_request_hash": receipt.request_hash,
            "step30_commit_receipt_hash": receipt.receipt_hash,
        }

    def get_activation_replay(self, transaction, **values):
        return self.activations.get(values["replay_identity"])

    def activate_patch(self, transaction, state):
        receipt = state.activation_receipt
        self.activations[receipt.activation_replay_identity] = {
            "step30_activation_receipt_hash": receipt.receipt_hash,
        }

    def transition_state(self, transaction, *, expected, updated):
        if self.state != expected:
            raise RuntimeError("state compare-and-set conflict")
        self.state = updated
        return updated

    def insert_transition_event(self, transaction, **values):
        self.events.append((values["state_before"], values["state"].state))
        return "transition"


class MutableTrustedClock:
    def __init__(self, current):
        self.current = current

    def now(self):
        return self.current


class Step30ServiceTests(unittest.TestCase):
    def setUp(self):
        self.value = fixture()
        self.lifecycle = InMemoryLifecycleRepository(self.value.awaiting)
        self.slots = InMemorySlotRepository(self.value)
        self.candidates = InMemoryCandidateRepository(self.value)
        self.proposals = InMemoryProposalRepository()
        self.proposals.values[self.value.awaiting.proposal.proposal_id] = self.value.awaiting
        self.idempotency = FakeIdempotency()
        self.app_runner = SerializableTransactionRunner(
            lambda: None,
            credential_purpose=CredentialPurpose.APPLICATION_DATABASE,
        )
        self.commit_runner = SerializableTransactionRunner(
            lambda: None,
            credential_purpose=CredentialPurpose.PERSONAL_MEMORY_COMMIT_DATABASE,
        )
        self.app_runner_patch = mock.patch.object(
            self.app_runner,
            "run",
            side_effect=lambda context, callback, **kwargs: callback(object()),
        )
        self.commit_runner_patch = mock.patch.object(
            self.commit_runner,
            "run",
            side_effect=lambda context, callback, **kwargs: callback(object()),
        )
        self.app_runner_patch.start()
        self.commit_runner_patch.start()
        self.clock = MutableTrustedClock(
            self.value.awaiting.updated_at + timedelta(seconds=1)
        )
        kwargs = {
            "lifecycle_repository": self.lifecycle,
            "slot_repository": self.slots,
            "candidate_repository": self.candidates,
            "proposal_repository": self.proposals,
            "idempotency": self.idempotency,
            "trusted_clock": self.clock,
        }
        self.approvals = PersonalMemoryApprovalService(self.app_runner, **kwargs)
        self.commits = PersonalMemoryCommitHelper(self.commit_runner, **kwargs)
        self.activations = PersonalMemoryActivationService(
            self.commit_runner, **kwargs
        )

    def tearDown(self):
        self.commit_runner_patch.stop()
        self.app_runner_patch.stop()

    def test_services_run_three_edges_and_exact_replays(self):
        approval_request = build_personal_memory_approval_request(
            self.value.awaiting,
            approval_nonce="service-approval",
            requested_at=self.value.awaiting.updated_at + timedelta(seconds=1),
        )
        approved, approval_receipt, replay = self.approvals.approve(
            approval_request,
            authenticated_actor_user_id=self.value.slot.owner_user_id,
        )
        self.assertFalse(replay)
        replayed, same_approval, replay = self.approvals.approve(
            approval_request,
            authenticated_actor_user_id=self.value.slot.owner_user_id,
        )
        self.assertTrue(replay)
        self.assertEqual(approval_receipt, same_approval)
        self.assertEqual(approved, replayed)

        commit_request = build_personal_memory_commit_request(
            approved,
            commit_idempotency_key="service-commit",
            requested_at=approved.updated_at + timedelta(seconds=1),
        )
        self.clock.current = commit_request.requested_at
        committed, commit_receipt, replay = self.commits.commit(commit_request)
        self.assertFalse(replay)
        _, same_commit, replay = self.commits.commit(commit_request)
        self.assertTrue(replay)
        self.assertEqual(commit_receipt, same_commit)

        activation_request = build_personal_memory_activation_request(
            committed,
            activation_idempotency_key="service-activation",
            requested_at=committed.updated_at + timedelta(seconds=1),
        )
        self.clock.current = activation_request.requested_at
        with mock.patch.object(
            PersonalMemoryActivationService,
            "_proposal_id_for_patch",
            return_value=committed.proposal.proposal_id,
        ):
            active, activation_receipt, replay = self.activations.activate(activation_request)
            self.assertFalse(replay)
            _, same_activation, replay = self.activations.activate(activation_request)
        self.assertTrue(replay)
        self.assertEqual(activation_receipt, same_activation)
        self.assertIs(active.state, PatchState.ACTIVE)
        self.assertEqual(len(self.lifecycle.events), 3)

    def test_changed_replays_fail_closed(self):
        request = build_personal_memory_approval_request(
            self.value.awaiting,
            approval_nonce="replay-conflict",
            requested_at=self.value.awaiting.updated_at + timedelta(seconds=1),
        )
        self.approvals.approve(
            request,
            authenticated_actor_user_id=self.value.slot.owner_user_id,
        )
        with self.assertRaises((IdempotencyConflictError, PersonalMemoryPatchLifecycleError)):
            self.approvals.approve(
                replace(request, proposal_hash="0" * 64),
                authenticated_actor_user_id=self.value.slot.owner_user_id,
            )

    def test_toctou_slot_and_quota_changes_block_later_phases(self):
        request = build_personal_memory_approval_request(
            self.value.awaiting,
            approval_nonce="toctou-approval",
            requested_at=self.value.awaiting.updated_at + timedelta(seconds=1),
        )
        approved, _, _ = self.approvals.approve(
            request,
            authenticated_actor_user_id=self.value.slot.owner_user_id,
        )
        commit_request = build_personal_memory_commit_request(
            approved,
            commit_idempotency_key="toctou-commit",
            requested_at=approved.updated_at + timedelta(seconds=1),
        )
        self.slots.slot = replace(
            self.slots.slot,
            state=PersonalMemorySpaceState.ARCHIVED,
            state_version=2,
            updated_at=self.slots.slot.updated_at + timedelta(seconds=10),
        )
        with self.assertRaises(PersonalMemoryPatchLifecycleError):
            self.commits.commit(commit_request)

    def test_stale_validation_blocks_approval_and_commit(self):
        request = build_personal_memory_approval_request(
            self.value.awaiting,
            approval_nonce="stale-approval",
            requested_at=self.value.awaiting.validation_receipt.validated_at
            + timedelta(days=2),
        )
        with self.assertRaises(PersonalMemoryPatchLifecycleError):
            self.approvals.approve(
                request,
                authenticated_actor_user_id=self.value.slot.owner_user_id,
            )

    def test_backdated_request_cannot_override_trusted_revalidation_time(self):
        request = build_personal_memory_approval_request(
            self.value.awaiting,
            approval_nonce="trusted-time-stale",
            requested_at=self.value.awaiting.updated_at + timedelta(seconds=1),
        )
        self.clock.current = self.value.awaiting.validation_receipt.validated_at + timedelta(
            days=2
        )
        with self.assertRaises(PersonalMemoryPatchLifecycleError) as caught:
            self.approvals.approve(
                request,
                authenticated_actor_user_id=self.value.slot.owner_user_id,
            )
        self.assertIn(
            caught.exception.reason_code,
            {
                Step30ReasonCode.COMMIT_EVIDENCE_STALE,
                Step30ReasonCode.COMMIT_VALIDATION_STALE,
            },
        )


class Step30PersistenceAndBoundaryTests(unittest.TestCase):
    def test_lifecycle_row_roundtrip_binds_all_projection_hashes(self):
        active = lifecycle_chain()[-1]
        proposal = active.proposal
        row = {
            "proposal_id": proposal.proposal_id,
            "proposed_content": personal_memory_patch_lifecycle_to_jsonb(active),
            "lifecycle_state": active.state.value,
            "content_hash": proposal.proposal_hash,
            "step29_dedup_key": proposal.exact_dedup_key,
            "step29_candidate_id": proposal.candidate_id,
            "step29_candidate_hash": proposal.candidate_hash,
            "step29_candidate_envelope_hash": proposal.candidate_envelope_hash,
            "step29_target_binding_hash": proposal.target_binding_hash,
            "step29_state_version": active.state_version,
            "step29_state_hash": active.state_hash,
            "step29_evidence_binding_hash": active.step29_state.evidence_binding.binding_hash,
            "step29_validation_receipt_hash": active.step29_state.validation_receipt.receipt_hash,
            "step30_approval_id": active.approval_receipt.approval_id,
            "step30_approval_receipt_hash": active.approval_receipt.receipt_hash,
            "step30_commit_receipt_hash": active.commit_receipt.receipt_hash,
            "step30_activation_receipt_hash": active.activation_receipt.receipt_hash,
            "step30_patch_id": active.committed_patch.patch_id,
        }
        self.assertEqual(lifecycle_state_from_row(row), active)
        bad = dict(row, step30_commit_receipt_hash="0" * 64)
        with self.assertRaises(IntegrityError):
            lifecycle_state_from_row(bad)

    def test_migration_uses_dedicated_nobypass_role_rls_and_exact_edges(self):
        sql = (ROOT / "sql/cockroachdb/migrations/0014_step30_user_approval_commit_activation.sql").read_text()
        self.assertIn("CREATE ROLE IF NOT EXISTS mp_personal_memory_commit_helper", sql)
        self.assertIn("NOLOGIN NOCREATEROLE NOCREATEDB NOBYPASSRLS", sql)
        self.assertNotRegex(sql, r"(?<!NO)BYPASSRLS")
        self.assertIn("REVOKE INSERT ON TABLE memory_patch.memory_patch_approvals", sql)
        for fragment in (
            "step30_receipt_hash STRING",
            "step30_approval_transition_matches",
            "step30_memory_item_commit_matches",
            "step30_proposal_lifecycle_matches",
            "guard_step30_commit_slot_update",
            "personal_memory_spaces_s30_commit_guard",
            "Commit Helper may only increment the slot quota epoch",
            "Personal Memory target slot is not commit-eligible",
            "Personal Memory target slot is not activation-eligible",
            "SET candidate_quota_epoch = space.candidate_quota_epoch + 1",
            "personal_memory_spaces_s30_commit_quota_lock_update",
            "tenants_s30_commit_identity_select",
            "users_s30_commit_identity_select",
            "kernel_runs_s30_commit_lineage_select",
            "audit_events_s30_commit_owner_select",
            "commit_record.step30_commit_payload -> 'committed_patch'",
        ):
            self.assertIn(fragment, sql)
        self.assertNotIn("GRANT UPDATE (candidate_quota_epoch)", sql)
        self.assertRegex(
            sql,
            r"GRANT UPDATE ON TABLE\s+[^;]*memory_patch\.personal_memory_spaces",
        )
        self.assertRegex(
            sql,
            r"(?s)GRANT SELECT ON TABLE.*?memory_patch\.tenants.*?"
            r"memory_patch\.users.*?memory_patch\.kernel_runs.*?"
            r"memory_patch\.audit_events.*?"
            r"TO mp_personal_memory_commit_helper",
        )
        self.assertRegex(
            sql,
            r"(?s)guard_step30_commit_slot_update\(\).*?"
            r"pg_catalog\.pg_has_role\(.*?"
            r"candidate_quota_epoch <> \(OLD\)\.candidate_quota_epoch \+ 1",
        )
        self.assertRegex(
            sql,
            r"(?s)\(OLD\)\.step29_state_version >= 2.*?"
            r"step29_evidence_binding_hash.*?IS DISTINCT FROM.*?"
            r"step29_evidence_binding_hash",
        )
        self.assertRegex(
            sql,
            r"(?s)\(OLD\)\.step29_state_version >= 3.*?"
            r"step29_validation_receipt_hash.*?IS DISTINCT FROM.*?"
            r"step29_validation_receipt_hash",
        )
        self.assertRegex(
            sql,
            r"patch_record\.content\s+= p_lifecycle_payload -> 'committed_patch'",
        )
        self.assertNotIn("IF NOT FOUND", sql)
        for before, after in (
            ("AWAITING_APPROVAL", "APPROVED"),
            ("APPROVED", "COMMITTED"),
            ("COMMITTED", "ACTIVE"),
        ):
            self.assertRegex(
                sql,
                rf"state_before\s*=\s*'{before}'\s+AND\s+state_after\s*=\s*'{after}'",
            )
        self.assertGreaterEqual(sql.count("FORCE ROW LEVEL SECURITY"), 6)
        manifest_path = (
            ROOT
            / "config/cockroachdb/personal-memory-approval-commit-security-1a.json"
        )
        manifest = json.loads(manifest_path.read_text())
        self.assertEqual(
            manifest_path.read_text(),
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        )
        self.assertFalse(manifest["commit_authority"]["bypass_rls"])
        self.assertFalse(manifest["commit_authority"]["can_approve"])
        self.assertEqual(
            manifest["commit_identity_read_boundary"]["tables"],
            ["tenants", "users"],
        )
        self.assertTrue(
            manifest["commit_identity_read_boundary"]["tenant_user_scoped"]
        )
        self.assertEqual(
            manifest["commit_run_lineage_read_boundary"]["table"],
            "kernel_runs",
        )
        self.assertTrue(
            manifest["commit_run_lineage_read_boundary"]["owner_scoped"]
        )
        self.assertEqual(
            manifest["commit_audit_fk_read_boundary"]["table"],
            "audit_events",
        )
        self.assertTrue(
            manifest["commit_audit_fk_read_boundary"]["owner_scoped"]
        )
        self.assertEqual(
            manifest["quota_serialization"]["database_compatibility"],
            "TABLE_UPDATE_GUARDED_TO_EPOCH_ONLY",
        )
        self.assertFalse(
            manifest["credential_separation"]["secret_material_in_repository"]
        )

    def test_services_have_no_generic_bypass_or_step31_retrieval(self):
        source = "\n".join(
            inspect.getsource(item)
            for item in (
                PersonalMemoryApprovalService,
                PersonalMemoryCommitHelper,
                PersonalMemoryActivationService,
            )
        )
        for forbidden in (
            "set_state",
            "retrieve_active_patch",
            "cross_model_reuse",
            "subprocess",
            "execute_action",
        ):
            self.assertNotIn(forbidden, source)
        self.assertIn("_trusted_now(self._clock)", source)
        self.assertEqual(
            {name for name in dir(PersonalMemoryApprovalService) if not name.startswith("_")},
            {"approve"},
        )
        self.assertEqual(
            {name for name in dir(PersonalMemoryCommitHelper) if not name.startswith("_")},
            {"commit"},
        )
        self.assertEqual(
            {name for name in dir(PersonalMemoryActivationService) if not name.startswith("_")},
            {"activate"},
        )

    def test_validation_evidence_docs_and_live_checkpoint_close_only_step30(self):
        evidence_path = (
            ROOT
            / "docs/evidence/personal-memory/step30-user-approval-commit-activation-validation.json"
        )
        evidence = json.loads(evidence_path.read_text())
        self.assertEqual(evidence["step"], 30)
        self.assertEqual(evidence["status"], "PASS")
        self.assertEqual(
            evidence["start_sha"],
            "0805fcbb04822d48198aa95ead42abd281784001",
        )
        self.assertEqual(evidence["approval"]["actor_type"], "HUMAN_USER")
        self.assertEqual(
            evidence["state_matrix"],
            {
                "ACTIVE": "PASS",
                "APPROVED": "PASS",
                "AWAITING_APPROVAL": "PASS_STEP29_INPUT",
                "COMMITTED": "PASS",
            },
        )
        self.assertEqual(
            len(set(evidence["content_identity"].values())),
            1,
        )
        self.assertEqual(evidence["owner_isolation"]["cross_user_rows"], 0)
        self.assertEqual(evidence["owner_isolation"]["cross_tenant_rows"], 0)
        self.assertFalse(evidence["active_patch_canonical_evidence"])
        self.assertFalse(evidence["step31_boundary"]["step31_started"])
        digest = evidence.pop("validation_digest")
        self.assertEqual(digest, canonical_sha256(evidence))

        for relative in (
            "docs/architecture/USER_APPROVAL_COMMIT_HELPER_ACTIVATION_1A.md",
            "docs/adr/ADR-037-user-approval-commit-helper-activation.md",
            "docs/operations/STEP_30_USER_APPROVAL_COMMIT_ACTIVATION_VALIDATION_1A.md",
            "docs/audits/STEP_30_USER_APPROVAL_COMMIT_HELPER_ACTIVATION_CLOSURE_1A.md",
        ):
            self.assertTrue((ROOT / relative).is_file(), relative)

        roadmap = (ROOT / "docs/roadmap/PRODUCTION_ROADMAP.md").read_text()
        agents = (ROOT / "AGENTS.md").read_text()
        self.assertIn("- [x] **Step 30", roadmap)
        self.assertIn("Step 30: COMPLETE AND PUSHED", roadmap)
        self.assertIn("- [x] **Step 31", roadmap)
        self.assertIn("- [x] **Step 32", roadmap)
        self.assertIn("- [x] **Step 33", roadmap)
        self.assertIn("- [x] **Step 34", roadmap)
        self.assertIn("- [x] **Step 35", roadmap)
        self.assertIn("- [x] **Step 36", roadmap)
        self.assertIn("- [x] **Step 37", roadmap)
        self.assertIn("- [x] **Step 38", roadmap)
        self.assertIn("- [x] **Step 39", roadmap)
        self.assertIn("- [ ] **Step 40", roadmap)
        self.assertIn("Step 39: COMPLETE AND PUSHED", roadmap)
        self.assertIn("Step 40: NOT STARTED", roadmap)
        self.assertIn("Step 39 completion does not authorize Step 40.", roadmap)
        self.assertIn("Step 30: COMPLETE AND PUSHED", agents)
        self.assertIn("Step 31: COMPLETE AND PUSHED", agents)
        self.assertIn("Step 32: COMPLETE AND PUSHED", agents)
        self.assertIn("Step 33: COMPLETE AND PUSHED", agents)
        self.assertIn("Step 34: COMPLETE AND PUSHED", agents)
        self.assertIn("Step 35: COMPLETE AND PUSHED", agents)
        self.assertIn("Step 36: COMPLETE AND PUSHED", agents)
        self.assertIn("Step 37: COMPLETE AND PUSHED", agents)
        self.assertIn("Step 38: COMPLETE AND PUSHED", agents)
        self.assertIn("Step 39: COMPLETE AND PUSHED", agents)
        self.assertIn("Step 40: NOT STARTED", agents)
        self.assertIn("Step 39 completion does not authorize Step 40.", agents)


if __name__ == "__main__":
    unittest.main()
