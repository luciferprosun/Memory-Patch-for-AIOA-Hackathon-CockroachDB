"""Step 32 Personal Memory lifecycle and shared-review boundary tests."""

from __future__ import annotations

import dataclasses
import inspect
import json
import unittest
from dataclasses import replace
from datetime import timedelta

from tests._support import REPOSITORY_ROOT
from tests.test_step29_personal_memory_patch_proposal import fixture
from tests.test_step30_user_approval_commit_activation import lifecycle_chain
from tests.test_step31_active_patch_retrieval import active_fixture, lifecycle_row

from aioa_memory_kernel.contracts.enums import (
    PatchState,
    PersonalMemorySpaceState,
)
from aioa_memory_kernel.contracts.exceptions import (
    ContractValidationError,
    IntegrityError,
)
from aioa_memory_kernel.contracts.serialization import (
    canonical_json_bytes,
    canonical_sha256,
)
from aioa_memory_kernel.persistence import SerializableTransactionRunner
from aioa_memory_kernel.personal_memory import (
    STEP32_SCHEMA_VERSION,
    STEP32_STATE_VERSION,
    STEP32_SYSTEM_POLICY_ACTOR_ID,
    CanonicalEvidenceCompatibility,
    DeidentificationDecision,
    PersonalMemoryLifecycle32Service,
    PersonalMemoryLifecycleExportRecord,
    PersonalMemoryStep32Error,
    Step32ActorType,
    Step32ReasonCode,
    StoredActivePatchCandidate,
    active_patch_candidate_from_row,
    advance_personal_memory_patch_to_awaiting_approval,
    assess_shared_promotion_privacy,
    bind_personal_memory_patch_evidence,
    build_correction_candidate_envelope,
    build_deletion_request,
    build_lifecycle_export_bundle,
    build_lifecycle_export_request,
    build_personal_memory_activation_request,
    build_personal_memory_approval_request,
    build_personal_memory_commit_request,
    build_personal_memory_patch_evidence_binding,
    build_personal_memory_patch_proposal,
    build_personal_memory_patch_validation_receipt,
    build_revocation_request,
    build_shared_promotion_consent,
    build_shared_promotion_request,
    build_supersession_request,
    complete_logical_deletion,
    create_patch_revocation,
    create_patch_supersession,
    create_shared_promotion_proposal,
    parse_lifecycle_export_bundle,
    personal_memory_patch_lifecycle_to_jsonb,
    step32_to_jsonb,
    validate_personal_memory_patch,
    verify_deidentification_assessment,
    verify_deletion_result,
    verify_lifecycle_export_bundle,
    verify_patch_revocation,
    verify_patch_supersession,
    verify_shared_memory_promotion_proposal,
)
from aioa_memory_kernel.personal_memory.lifecycle import (
    activate_personal_memory_patch,
    approve_personal_memory_patch,
    commit_personal_memory_patch,
)
from aioa_memory_kernel.personal_memory.proposals import (
    ProposalConflictResult,
    ProposalDedupResult,
    ProposalGateResult,
)


ROOT = REPOSITORY_ROOT
BASE_SHA = "bf6cde9de87ab727f1bd5e48e2abfc7e8e3b85b5"


class Clock:
    def __init__(self, value):
        self.value = value

    def now(self):
        return self.value


def successor_lifecycle():
    """Build a second exact Step 30 ACTIVE patch in the same owner slot."""

    value = fixture()
    candidate = replace(
        value.envelope.submission.candidate,
        event_id="event-step32-successor",
    )
    envelope = build_correction_candidate_envelope(
        candidate=candidate,
        kernel_run=value.envelope.submission.run_identity,
        slot=value.slot,
        route_result_lineage=value.envelope.submission.lineage,
        metadata=value.envelope.submission.metadata,
        idempotency_key="step32-successor-candidate",
        submitted_at=value.envelope.submission.submitted_at,
    )
    command = replace(
        value.create_command,
        candidate_id=envelope.candidate_id,
        candidate_envelope_hash=envelope.envelope_hash,
        idempotency_key="step32-successor-proposal",
    )
    proposed = build_personal_memory_patch_proposal(envelope, command)
    binding = build_personal_memory_patch_evidence_binding(
        proposed,
        bundles=tuple(item.bundle for item in value.request.step20_outcomes),
        temporal_result=value.request.temporal_result,
        claim_links=value.links,
        claim_assessments=value.snapshot.ordered_candidate_assessments,
        correction_packet=value.request.correction_packet,
        verified_answer=value.answer,
        bound_at=value.binding.bound_at,
    )
    bound = bind_personal_memory_patch_evidence(
        proposed, binding, transitioned_at=value.bound.updated_at
    )
    receipt = build_personal_memory_patch_validation_receipt(
        bound,
        dedup_result=ProposalDedupResult.PASS,
        conflict_result=ProposalConflictResult.PASS,
        temporal_trusted_now=value.request.temporal_result.trusted_now,
        owner_scope_result=ProposalGateResult.PASS,
        slot_state_result=ProposalGateResult.PASS,
        quota_result=ProposalGateResult.PASS,
        model_binding_result=ProposalGateResult.PASS,
        validated_at=value.receipt.validated_at,
    )
    validated = validate_personal_memory_patch(
        bound, receipt, transitioned_at=value.validated.updated_at
    )
    awaiting = advance_personal_memory_patch_to_awaiting_approval(
        validated,
        validation_receipt_hash=receipt.receipt_hash,
        transitioned_at=value.awaiting.updated_at,
    )
    approval = build_personal_memory_approval_request(
        awaiting,
        approval_nonce="step32-successor-approval",
        requested_at=awaiting.updated_at + timedelta(seconds=1),
    )
    approved = approve_personal_memory_patch(
        awaiting,
        approval,
        authenticated_actor_user_id=value.slot.owner_user_id,
        approved_at=approval.requested_at,
    )
    commit = build_personal_memory_commit_request(
        approved,
        commit_idempotency_key="step32-successor-commit",
        requested_at=approved.updated_at + timedelta(seconds=1),
    )
    committed = commit_personal_memory_patch(
        approved, commit, committed_at=commit.requested_at
    )
    activation = build_personal_memory_activation_request(
        committed,
        activation_idempotency_key="step32-successor-activation",
        requested_at=committed.updated_at + timedelta(seconds=1),
    )
    return activate_personal_memory_patch(
        committed, activation, activated_at=activation.requested_at
    )


def active_pair():
    old = lifecycle_chain()[-1]
    new = successor_lifecycle()
    assert old.committed_patch is not None and new.committed_patch is not None
    assert old.committed_patch.patch_id != new.committed_patch.patch_id
    return old, new


def pending_slot(active):
    slot = fixture().slot
    requested = active.updated_at + timedelta(seconds=2)
    return replace(
        slot,
        state=PersonalMemorySpaceState.DELETED_PENDING,
        state_version=slot.state_version + 1,
        updated_at=requested,
        deletion_requested_at=requested,
    )


class Step32SupersessionAndRevocationTests(unittest.TestCase):
    def test_same_owner_supersession_is_hash_bound_and_preserves_history(self):
        old, new = active_pair()
        at = new.updated_at + timedelta(seconds=1)
        request = build_supersession_request(
            old,
            new,
            reason_codes=(Step32ReasonCode.SUPERSESSION_CREATED,),
            effective_at=at,
            idempotency_key="supersede-old-with-new",
        )
        record = create_patch_supersession(
            request,
            old,
            new,
            authenticated_owner_user_id=old.proposal.owner_user_id,
        )
        duplicate = create_patch_supersession(
            request,
            old,
            new,
            authenticated_owner_user_id=old.proposal.owner_user_id,
        )
        self.assertEqual(record, duplicate)
        self.assertIs(record.state, PatchState.SUPERSEDED)
        self.assertEqual(record.state_version, STEP32_STATE_VERSION)
        self.assertTrue(record.preserves_history)
        self.assertFalse(record.canonical_evidence)
        self.assertEqual(old.state, PatchState.ACTIVE)
        self.assertEqual(old.committed_patch.patch_statement_sha256,
                         new.committed_patch.patch_statement_sha256)
        verify_patch_supersession(record)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            record.old_patch_id = "changed"  # type: ignore[misc]

    def test_supersession_changed_replay_owner_and_scope_fail_closed(self):
        old, new = active_pair()
        request = build_supersession_request(
            old,
            new,
            reason_codes=(Step32ReasonCode.SUPERSESSION_CREATED,),
            effective_at=new.updated_at + timedelta(seconds=1),
            idempotency_key="supersession-conflict",
        )
        with self.assertRaises(PersonalMemoryStep32Error):
            create_patch_supersession(
                replace(request, new_patch_hash="0" * 64),
                old,
                new,
                authenticated_owner_user_id=old.proposal.owner_user_id,
            )
        with self.assertRaises(PersonalMemoryStep32Error):
            create_patch_supersession(
                request,
                old,
                new,
                authenticated_owner_user_id="different-owner",
            )
        tampered = create_patch_supersession(
            request,
            old,
            new,
            authenticated_owner_user_id=old.proposal.owner_user_id,
        )
        object.__setattr__(tampered, "old_patch_hash", "0" * 64)
        with self.assertRaises((ContractValidationError, IntegrityError)):
            verify_patch_supersession(tampered)

    def test_revocation_is_owner_or_exact_system_policy_and_not_deletion(self):
        active = lifecycle_chain()[-1]
        request = build_revocation_request(
            active,
            reason_codes=(Step32ReasonCode.REVOCATION_CREATED,),
            effective_at=active.updated_at + timedelta(seconds=1),
            idempotency_key="owner-revocation",
        )
        owner = create_patch_revocation(
            request,
            active,
            actor_type=Step32ActorType.HUMAN_OWNER,
            authenticated_actor_id=active.proposal.owner_user_id,
        )
        system = create_patch_revocation(
            replace(request, idempotency_key="system-revocation"),
            active,
            actor_type=Step32ActorType.DETERMINISTIC_SYSTEM_POLICY,
            authenticated_actor_id=STEP32_SYSTEM_POLICY_ACTOR_ID,
        )
        self.assertFalse(owner.deletion_performed)
        self.assertTrue(owner.content_preserved)
        self.assertEqual(owner.patch_hash, system.patch_hash)
        verify_patch_revocation(owner)
        verify_patch_revocation(system)
        with self.assertRaises(ContractValidationError):
            create_patch_revocation(
                request,
                active,
                actor_type=Step32ActorType.DETERMINISTIC_SYSTEM_POLICY,
                authenticated_actor_id="model-or-critic",
            )

    def test_revocation_tamper_and_wrong_owner_fail_closed(self):
        active = lifecycle_chain()[-1]
        request = build_revocation_request(
            active,
            reason_codes=(Step32ReasonCode.REVOCATION_CREATED,),
            effective_at=active.updated_at + timedelta(seconds=1),
            idempotency_key="revocation-tamper",
        )
        with self.assertRaises(PersonalMemoryStep32Error):
            create_patch_revocation(
                request,
                active,
                actor_type=Step32ActorType.HUMAN_OWNER,
                authenticated_actor_id="other-user",
            )
        with self.assertRaises(
            (ContractValidationError, IntegrityError, PersonalMemoryStep32Error)
        ):
            create_patch_revocation(
                replace(request, patch_hash="0" * 64),
                active,
                actor_type=Step32ActorType.HUMAN_OWNER,
                authenticated_actor_id=active.proposal.owner_user_id,
            )


class Step32ExportAndDeletionTests(unittest.TestCase):
    def test_owner_export_is_canonical_deterministic_private_and_roundtrips(self):
        active = lifecycle_chain()[-1]
        slot = fixture().slot
        request = build_lifecycle_export_request(
            slot,
            requested_at=active.updated_at + timedelta(seconds=1),
            idempotency_key="owner-export",
        )
        records = (
            PersonalMemoryLifecycleExportRecord(
                record_type="MEMORY_PATCH",
                record_id=active.committed_patch.patch_id,
                payload={
                    "patch_hash": active.committed_patch.patch_hash,
                    "owner_user_id": active.proposal.owner_user_id,
                },
            ),
        )
        bundle = build_lifecycle_export_bundle(request, slot, records)
        duplicate = build_lifecycle_export_bundle(request, slot, records)
        self.assertEqual(bundle, duplicate)
        self.assertTrue(bundle.owner_private)
        self.assertFalse(bundle.shared_promotion)
        self.assertFalse(bundle.canonical_evidence)
        self.assertEqual(
            parse_lifecycle_export_bundle(step32_to_jsonb(bundle)), bundle
        )
        self.assertEqual(
            canonical_json_bytes(step32_to_jsonb(bundle)),
            canonical_json_bytes(step32_to_jsonb(duplicate)),
        )
        verify_lifecycle_export_bundle(bundle)

    def test_export_blocks_secrets_paths_and_unauthenticated_owner(self):
        with self.assertRaises(ContractValidationError):
            PersonalMemoryLifecycleExportRecord(
                record_type="BAD", record_id="bad", payload={"api_key": "x"}
            )
        with self.assertRaises(ContractValidationError):
            PersonalMemoryLifecycleExportRecord(
                record_type="BAD",
                record_id="bad",
                payload={"location": "/home/private/file"},
            )
        with self.assertRaises(ContractValidationError):
            PersonalMemoryLifecycleExportRecord(
                record_type="BAD",
                record_id="bad",
                payload={"refresh-token": "sensitive"},
            )
        slot = fixture().slot
        request = build_lifecycle_export_request(
            slot,
            requested_at=slot.updated_at + timedelta(seconds=1),
            idempotency_key="wrong-export-owner",
        )
        runner = SerializableTransactionRunner(lambda: None)
        service = PersonalMemoryLifecycle32Service(
            runner,
            trusted_clock=Clock(request.requested_at),
        )
        with self.assertRaises(PersonalMemoryStep32Error) as caught:
            service.export(
                request, authenticated_owner_user_id="different-user"
            )
        self.assertIs(caught.exception.reason_code,
                      Step32ReasonCode.EXPORT_OWNER_MISMATCH)

    def test_export_bundle_rejects_cross_owner_or_cross_slot_payload(self):
        active = lifecycle_chain()[-1]
        slot = fixture().slot
        request = build_lifecycle_export_request(
            slot,
            requested_at=active.updated_at + timedelta(seconds=1),
            idempotency_key="owner-export-scope-negative",
        )
        for payload in (
            {"owner_user_id": "different-user"},
            {"tenant_id": "different-tenant"},
            {"personal_memory_space_id": "different-slot"},
        ):
            with self.assertRaises(PersonalMemoryStep32Error):
                build_lifecycle_export_bundle(
                    request,
                    slot,
                    (
                        PersonalMemoryLifecycleExportRecord(
                            record_type="FOREIGN",
                            record_id="foreign-record",
                            payload=payload,
                        ),
                    ),
                )

    def test_logical_deletion_is_distinct_from_revocation_and_keeps_hash(self):
        active = lifecycle_chain()[-1]
        slot = pending_slot(active)
        request = build_deletion_request(
            active,
            slot,
            requested_at=slot.updated_at,
            idempotency_key="owner-logical-delete",
        )
        result = complete_logical_deletion(
            request,
            active,
            slot,
            authenticated_owner_user_id=slot.owner_user_id,
        )
        self.assertTrue(result.logical_delete)
        self.assertFalse(result.physical_delete)
        self.assertFalse(result.revoked)
        self.assertFalse(result.shared_artifacts_mutated)
        self.assertEqual(result.patch_hash, active.committed_patch.patch_hash)
        self.assertIs(result.slot_state, PersonalMemorySpaceState.DELETED)
        verify_deletion_result(result)

    def test_deletion_requires_pending_exact_owner_state_and_replay_binding(self):
        active = lifecycle_chain()[-1]
        slot = pending_slot(active)
        request = build_deletion_request(
            active,
            slot,
            requested_at=slot.updated_at,
            idempotency_key="delete-replay",
        )
        with self.assertRaises(PersonalMemoryStep32Error):
            complete_logical_deletion(
                request,
                active,
                slot,
                authenticated_owner_user_id="other-user",
            )
        with self.assertRaises((PersonalMemoryStep32Error, IntegrityError)):
            complete_logical_deletion(
                replace(request, patch_hash="0" * 64),
                active,
                slot,
                authenticated_owner_user_id=slot.owner_user_id,
            )


class Step32SharedPromotionTests(unittest.TestCase):
    def _promotion(self, *, compatibility=CanonicalEvidenceCompatibility.MATCH):
        active = lifecycle_chain()[-1]
        statement = (
            active.committed_patch.patch_statement
            + " Contact owner@example.com about account ABCDEF1234."
        )
        # A promotion is always derived from the exact patch text.  Use a patch
        # whose existing deterministic text is the source for the valid case.
        assessment = assess_shared_promotion_privacy(
            active.committed_patch.patch_statement,
            private_identifiers=(active.proposal.owner_user_id,),
        )
        consent = build_shared_promotion_consent(
            active,
            assessment,
            target_hat_id="shared-review-hat",
            consent_nonce="separate-share-consent",
            authenticated_owner_user_id=active.proposal.owner_user_id,
            consented_at=active.updated_at + timedelta(seconds=1),
        )
        request = build_shared_promotion_request(
            active,
            target_hat_id="shared-review-hat",
            promotion_purpose="Candidate for separately reviewed shared memory",
            promotion_scope=active.committed_patch.patch_scope,
            deidentification=assessment,
            owner_consent=consent,
            canonical_evidence_compatibility=compatibility,
            reason_codes=(
                Step32ReasonCode.CANONICAL_EVIDENCE_AUTHORITY_NOT_GRANTED,
                Step32ReasonCode.SHARED_PROMOTION_PROPOSED,
            ),
            requested_at=consent.consented_at,
            idempotency_key="shared-promotion",
        )
        proposal = create_shared_promotion_proposal(
            request,
            active,
            authenticated_owner_user_id=active.proposal.owner_user_id,
        )
        return active, assessment, consent, request, proposal, statement

    def test_deidentification_redacts_obvious_identifiers_and_never_self_certifies(self):
        assessment = assess_shared_promotion_privacy(
            "Email jane@example.com, account ABCDEF1234, owner user-step20.",
            private_identifiers=("user-step20",),
        )
        self.assertNotIn("jane@example.com", assessment.candidate_shared_statement)
        self.assertNotIn("ABCDEF1234", assessment.candidate_shared_statement)
        self.assertNotIn("user-step20", assessment.candidate_shared_statement)
        self.assertIs(assessment.decision,
                      DeidentificationDecision.REVIEW_REQUIRED)
        self.assertTrue(assessment.review_required)
        self.assertFalse(assessment.model_certified)
        self.assertEqual(len(assessment.policy_digest), 64)

    def test_promotion_is_review_only_noncanonical_and_not_published(self):
        active, assessment, consent, request, proposal, _ = self._promotion()
        self.assertTrue(proposal.review_required)
        self.assertFalse(proposal.shared_active)
        self.assertFalse(proposal.source_registry_published)
        self.assertFalse(proposal.canonical_evidence)
        self.assertEqual(proposal.source_patch_hash,
                         active.committed_patch.patch_hash)
        self.assertEqual(proposal.owner_consent_hash, consent.consent_hash)
        self.assertEqual(proposal.candidate_shared_statement_sha256,
                         assessment.candidate_shared_statement_sha256)
        self.assertEqual(proposal.base_proposal.state.value,
                         "SHARED_PROMOTION_PROPOSED")
        verify_shared_memory_promotion_proposal(proposal)
        self.assertEqual(
            create_shared_promotion_proposal(
                request,
                active,
                authenticated_owner_user_id=active.proposal.owner_user_id,
            ),
            proposal,
        )

    def test_private_approval_is_not_share_consent_and_source_must_be_exact(self):
        active = lifecycle_chain()[-1]
        exact = assess_shared_promotion_privacy(
            active.committed_patch.patch_statement,
            private_identifiers=(active.proposal.owner_user_id,),
        )
        unrelated = assess_shared_promotion_privacy("Unrelated candidate text")
        with self.assertRaises(PersonalMemoryStep32Error):
            build_shared_promotion_consent(
                active,
                unrelated,
                target_hat_id="shared-review-hat",
                consent_nonce="wrong-source",
                authenticated_owner_user_id=active.proposal.owner_user_id,
                consented_at=active.updated_at + timedelta(seconds=1),
            )
        with self.assertRaises(PersonalMemoryStep32Error):
            build_shared_promotion_consent(
                active,
                exact,
                target_hat_id="shared-review-hat",
                consent_nonce="wrong-owner",
                authenticated_owner_user_id="model-or-critic",
                consented_at=active.updated_at + timedelta(seconds=1),
            )

    def test_scope_widening_and_rehashed_tamper_fail_closed(self):
        active, _, _, request, proposal, _ = self._promotion()
        widened = request.promotion_scope + (
            replace(request.promotion_scope[0], name="zz_extra_scope"),
        )
        with self.assertRaises(PersonalMemoryStep32Error):
            build_shared_promotion_request(
                active,
                target_hat_id=request.target_hat_id,
                promotion_purpose=request.promotion_purpose,
                promotion_scope=widened,
                deidentification=request.deidentification,
                owner_consent=request.owner_consent,
                canonical_evidence_compatibility=request.canonical_evidence_compatibility,
                reason_codes=request.reason_codes,
                requested_at=request.requested_at,
                idempotency_key=request.idempotency_key,
            )
        with self.assertRaises((ContractValidationError, IntegrityError)):
            verify_shared_memory_promotion_proposal(
                replace(proposal, source_patch_hash="0" * 64)
            )
        detached_text = "Detached but internally hash-bound candidate"
        with self.assertRaises((ContractValidationError, IntegrityError)):
            verify_shared_memory_promotion_proposal(
                replace(
                    proposal,
                    candidate_shared_statement=detached_text,
                    candidate_shared_statement_sha256=canonical_sha256(
                        detached_text
                    ),
                )
            )
        with self.assertRaises((ContractValidationError, IntegrityError)):
            verify_deidentification_assessment(
                replace(
                    request.deidentification,
                    policy_digest="0" * 64,
                )
            )

    def test_canonical_conflict_remains_review_only_and_grants_no_authority(self):
        _, _, _, _, proposal, _ = self._promotion(
            compatibility=CanonicalEvidenceCompatibility.CONFLICT
        )
        self.assertIs(proposal.canonical_evidence_compatibility,
                      CanonicalEvidenceCompatibility.CONFLICT)
        self.assertTrue(proposal.review_required)
        self.assertFalse(proposal.canonical_evidence)
        self.assertFalse(proposal.source_registry_published)


class Step32RetrievalPersistenceAndBoundaryTests(unittest.TestCase):
    def test_step31_row_parser_accepts_exact_historical_supersession_only(self):
        active = lifecycle_chain()[-1]
        row = lifecycle_row(active)
        row.update(
            {
                "item_active": False,
                "item_revoked": False,
                "step32_terminal_kind": "SUPERSEDED",
                "step32_effective_at": active.updated_at + timedelta(seconds=2),
                "step32_superseded_by_patch_id": "successor-patch",
            }
        )
        stored = active_patch_candidate_from_row(row)
        self.assertEqual(stored.step32_terminal_kind, "SUPERSEDED")
        self.assertFalse(stored.active)
        with self.assertRaises(ContractValidationError):
            StoredActivePatchCandidate(
                lifecycle_state=active,
                active=False,
                revoked=True,
                valid_from=None,
                valid_until=None,
                expires_at=None,
                step32_terminal_kind="SUPERSEDED",
                step32_effective_at=active.updated_at,
                step32_superseded_by_patch_id="successor-patch",
            )

    def test_retrieval_sql_suppresses_terminal_current_and_preserves_history(self):
        source = (ROOT / "src/aioa_memory_kernel/personal_memory/retrieval_repository.py").read_text()
        self.assertIn("item.step32_terminal_kind IS NULL", source)
        self.assertIn("item.step32_terminal_kind = 'SUPERSEDED'", source)
        self.assertIn("item.step32_effective_at > %s::TIMESTAMPTZ", source)
        self.assertIn("successor.new_patch_id = item.memory_item_id", source)
        migration = (
            ROOT
            / "sql/cockroachdb/migrations/0015_step32_personal_memory_lifecycle.sql"
        ).read_text()
        self.assertIn(
            "personal_memory_patch_supersessions_s32_successor_idx",
            migration,
        )
        self.assertNotIn("UPDATE memory_patch", source)
        self.assertNotIn("DELETE FROM memory_patch", source)

    def test_migration_is_owner_scoped_force_rls_and_append_only(self):
        sql = (ROOT / "sql/cockroachdb/migrations/0015_step32_personal_memory_lifecycle.sql").read_text()
        for table in (
            "personal_memory_patch_supersessions",
            "personal_memory_patch_revocations",
            "personal_memory_exports",
            "personal_memory_deletions",
            "shared_memory_promotion_proposals",
        ):
            self.assertIn(f"ALTER TABLE memory_patch.{table}\n  FORCE ROW LEVEL SECURITY", sql)
            self.assertIn(f"ALTER TABLE memory_patch.{table}\n  ENABLE ROW LEVEL SECURITY", sql)
        self.assertIn("user_context_matches(tenant_id, owner_user_id)", sql)
        self.assertGreaterEqual(sql.count("IS NOT DISTINCT FROM"), 40)
        self.assertIn("IS TRUE", sql)
        self.assertIn("IS FALSE", sql)
        self.assertIn("FROM mp_app_runtime", sql)
        self.assertIn(
            "TO mp_personal_memory_commit_helper;",
            sql,
        )
        security = json.loads(
            (
                ROOT
                / "config/cockroachdb/personal-memory-lifecycle-security-1a.json"
            ).read_text()
        )
        self.assertEqual(
            security["commit_helper_fk_reference_tables"],
            [
                "personal_memory_patch_supersessions",
                "personal_memory_patch_revocations",
                "personal_memory_exports",
                "personal_memory_deletions",
                "shared_memory_promotion_proposals",
            ],
        )
        self.assertEqual(
            security["step30_policy_planning_function_grants"],
            [
                {
                    "authority_result": False,
                    "function": "step30_commit_helper_authorized",
                    "purpose": "RLS_POLICY_PLANNING_ONLY",
                    "runtime_role": "mp_app_runtime",
                }
            ],
        )
        self.assertRegex(
            sql,
            r"GRANT\s+EXECUTE\s+ON\s+FUNCTION\s+"
            r"memory_patch\.step30_commit_helper_authorized\(\)\s+"
            r"TO\s+mp_app_runtime\s*;",
        )
        self.assertNotRegex(
            sql,
            r"GRANT\s+mp_personal_memory_commit_helper\s+TO\s+mp_app_runtime",
        )
        self.assertNotIn("BYPASSRLS", sql)
        self.assertNotIn("ON DELETE CASCADE", sql)
        self.assertNotRegex(sql, r"(?i)GRANT\s+DELETE")

    def test_database_mutations_bind_tenant_owner_slot_not_patch_only(self):
        source = (ROOT / "src/aioa_memory_kernel/personal_memory/lifecycle32_repository.py").read_text()
        self.assertIn("proposal.owner_user_id = %s", source)
        self.assertIn("proposal.personal_memory_space_id = %s", source)
        self.assertIn("WHERE tenant_id = %s AND memory_item_id = %s", source)
        self.assertIn("ORDER BY {id_column} LIMIT %s", source)
        self.assertIn("MAXIMUM_EXPORT_RECORDS + 1", source)
        self.assertNotIn("publish", source.lower())
        public = {
            name
            for name, value in inspect.getmembers(
                __import__(
                    "aioa_memory_kernel.personal_memory.lifecycle32_service",
                    fromlist=["PersonalMemoryLifecycle32Service"],
                ).PersonalMemoryLifecycle32Service,
                predicate=inspect.isfunction,
            )
            if not name.startswith("_")
        }
        self.assertEqual(public, {"supersede", "revoke", "export", "delete", "propose_shared"})

    def test_manifest_runner_and_offline_security_validation_include_step32(self):
        manifest = json.loads(
            (ROOT / "sql/cockroachdb/migrations/manifest.json").read_text()
        )
        self.assertEqual(manifest["schema_version"], 16)
        self.assertEqual(manifest["runner_version"], "16.0.0")
        step32 = next(
            item for item in manifest["migrations"]
            if item["migration_id"] == "0015_step32_personal_memory_lifecycle"
        )
        sql = (
            ROOT
            / "sql/cockroachdb/migrations"
            / step32["filename"]
        ).read_bytes()
        self.assertEqual(step32["sha256"],
                         __import__("hashlib").sha256(sql).hexdigest())

    def test_no_step33_ui_execution_or_source_publication_runtime(self):
        sources = "\n".join(
            (ROOT / path).read_text()
            for path in (
                "src/aioa_memory_kernel/personal_memory/lifecycle32.py",
                "src/aioa_memory_kernel/personal_memory/lifecycle32_repository.py",
                "src/aioa_memory_kernel/personal_memory/lifecycle32_service.py",
            )
        ).lower()
        for forbidden in (
            "subprocess",
            "os.system",
            "shell=true",
            "audit_ledger",
            "hash_chain",
            "review_workspace",
            "personal_memory_ui",
        ):
            self.assertNotIn(forbidden, sources)
        self.assertNotIn("source_registry_published=true", sources.replace(" ", ""))


class Step32DocumentationAndClosureTests(unittest.TestCase):
    def test_required_step32_documents_and_validator_exist(self):
        expected = (
            "docs/architecture/"
            "PERSONAL_MEMORY_SUPERSESSION_REVOCATION_EXPORT_DELETE_SHARED_PROMOTION_1A.md",
            "docs/adr/ADR-039-personal-memory-lifecycle-shared-promotion-boundary.md",
            "docs/operations/STEP_32_PERSONAL_MEMORY_LIFECYCLE_VALIDATION_1A.md",
            "docs/audits/STEP_32_PERSONAL_MEMORY_LIFECYCLE_CLOSURE_1A.md",
            "docs/evidence/personal-memory/"
            "step32-personal-memory-lifecycle-validation.json",
            "scripts/run_step32_personal_memory_lifecycle_validation.py",
        )
        for relative in expected:
            self.assertTrue((ROOT / relative).is_file(), relative)

    def test_validation_evidence_and_live_checkpoint_close_only_step32(self):
        path = (
            ROOT
            / "docs/evidence/personal-memory/"
            "step32-personal-memory-lifecycle-validation.json"
        )
        evidence = json.loads(path.read_text(encoding="utf-8"))
        canonical = (
            json.dumps(
                evidence,
                ensure_ascii=False,
                allow_nan=False,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
        self.assertEqual(path.read_bytes(), canonical)
        digest = evidence.pop("validation_digest")
        self.assertEqual(digest, canonical_sha256(evidence))
        self.assertEqual(evidence["step"], 32)
        self.assertEqual(evidence["start_sha"], BASE_SHA)
        self.assertEqual(evidence["status"], "PASS")
        self.assertTrue(evidence["supersession"]["old_patch_immutable"])
        self.assertTrue(evidence["revocation"]["revoked_patch_not_retrieved"])
        self.assertTrue(evidence["deletion"]["deleted_patch_not_retrieved"])
        self.assertFalse(evidence["deletion"]["physical_delete"])
        self.assertTrue(evidence["shared_promotion"]["review_required"])
        self.assertFalse(evidence["shared_promotion"]["shared_active"])
        self.assertFalse(
            evidence["shared_promotion"]["source_registry_published"]
        )
        self.assertFalse(evidence["step33_boundary"]["step33_started"])

        roadmap = (ROOT / "docs/roadmap/PRODUCTION_ROADMAP.md").read_text()
        agents = (ROOT / "AGENTS.md").read_text()
        self.assertIn("- [x] **Step 32", roadmap)
        self.assertIn(
            "Step 32: COMPLETE AND PUSHED at actual closure commit",
            roadmap,
        )
        self.assertIn("- [x] **Step 33", roadmap)
        self.assertIn("- [x] **Step 34", roadmap)
        self.assertIn("- [x] **Step 35", roadmap)
        self.assertIn("- [x] **Step 36", roadmap)
        self.assertIn("- [x] **Step 37", roadmap)
        self.assertIn("- [x] **Step 38", roadmap)
        self.assertIn("- [x] **Step 39", roadmap)
        self.assertIn("- [x] **Step 40", roadmap)
        self.assertIn("- [x] **Step 41", roadmap)
        self.assertIn("- [ ] **Step 42", roadmap)
        self.assertIn("Step 39: COMPLETE AND PUSHED", roadmap)
        self.assertIn("Step 40: COMPLETE AND PUSHED", roadmap)
        self.assertIn("Step 41: COMPLETE AND PUSHED", roadmap)
        self.assertIn("Step 42: NOT STARTED", roadmap)
        self.assertIn("Step 41 completion does not authorize Step 42.", roadmap)
        self.assertIn(
            "Step 32: COMPLETE AND PUSHED at actual closure commit",
            agents,
        )
        self.assertIn("Step 33: COMPLETE AND PUSHED", agents)
        self.assertIn("Step 34: COMPLETE AND PUSHED", agents)
        self.assertIn("Step 35: COMPLETE AND PUSHED", agents)
        self.assertIn("Step 36: COMPLETE AND PUSHED", agents)
        self.assertIn("Step 37: COMPLETE AND PUSHED", agents)
        self.assertIn("Step 38: COMPLETE AND PUSHED", agents)
        self.assertIn("Step 39: COMPLETE AND PUSHED", agents)
        self.assertIn("Step 40: COMPLETE AND PUSHED", agents)
        self.assertIn("Step 41: COMPLETE AND PUSHED", agents)
        self.assertIn("Step 42: NOT STARTED", agents)
        self.assertIn("Step 41 completion does not authorize Step 42.", agents)


if __name__ == "__main__":
    unittest.main()
