"""Step 29 Personal Memory Patch proposal and evidence-validation tests."""

from __future__ import annotations

import dataclasses
import copy
import hashlib
import inspect
import json
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from types import SimpleNamespace
from unittest import mock

from tests._support import REPOSITORY_ROOT
from tests.test_step26_verified_answer_output import hat_lineage
from tests.test_step27_personal_memory_persistence import quota_policy
from tests.test_step28_correction_candidate_bridge import FakeIdempotency

from aioa_memory_kernel.answers import assemble_verified_answer
from aioa_memory_kernel.claims import (
    ClaimEvidenceBindingService,
    prepare_claim_binding_request,
)
from aioa_memory_kernel.contracts.correction import CorrectionCandidate
from aioa_memory_kernel.contracts.enums import (
    ActorType,
    AnswerStatus,
    CorrectionCandidateState,
    EvidenceStatus,
    PatchState,
    PersonalMemorySpaceState,
    ScopeComparisonMode,
    ScopeValueType,
)
from aioa_memory_kernel.contracts.evidence import ClaimCandidate
from aioa_memory_kernel.contracts.exceptions import (
    ContractValidationError,
    IntegrityError,
)
from aioa_memory_kernel.contracts.identities import KernelRunIdentity
from aioa_memory_kernel.contracts.personal_memory import PersonalHatQuotaUsage
from aioa_memory_kernel.contracts.serialization import canonical_sha256
from aioa_memory_kernel.contracts.scope import ScopeDimension
from aioa_memory_kernel.persistence import SerializableTransactionRunner
from aioa_memory_kernel.corrections.models import CorrectionPacketBoundaryError
from aioa_memory_kernel.personal_memory import (
    STEP27_SCHEMA_VERSION,
    STEP28_SCHEMA_VERSION,
    STEP29_SCHEMA_VERSION,
    AdvancePersonalMemoryPatchToAwaitingApproval,
    BindPersonalMemoryPatchEvidence,
    CreatePersonalMemoryPatchProposal,
    PersonalMemoryBindingMode,
    PersonalMemoryHatSlot,
    PersonalMemoryModelBinding,
    PersonalMemoryPatchProposalCockroachRepository,
    PersonalMemoryPatchProposalService,
    PersonalMemoryPatchProposalState,
    PersonalMemoryPatchValidationError,
    PersonalMemoryQuotaUsageView,
    ProposalConflictResult,
    ProposalDedupResult,
    ProposalFreshnessResult,
    ProposalGateResult,
    ProposalPeer,
    ProposalUsage,
    Step29ReasonCode,
    ValidatePersonalMemoryPatchProposal,
    advance_personal_memory_patch_to_awaiting_approval,
    bind_personal_memory_patch_evidence,
    build_correction_candidate_envelope,
    build_personal_memory_patch_evidence_binding,
    build_personal_memory_patch_proposal,
    build_personal_memory_patch_validation_receipt,
    load_personal_memory_patch_validation_policy,
    parse_personal_memory_patch_state,
    personal_memory_hat_scope_id,
    personal_memory_patch_state_to_jsonb,
    proposal_conflict_between,
    proposal_state_from_row,
    validate_personal_memory_patch,
    verify_personal_memory_patch_evidence_binding,
    verify_personal_memory_patch_state,
    verify_personal_memory_patch_validation_receipt,
)
from aioa_memory_kernel.personal_memory.candidates import (
    CorrectionCandidateMetadata,
    CorrectionCandidateReasonCode,
    CorrectionCandidateRouteResultLineage,
    CorrectionCandidateTrigger,
)
from aioa_memory_kernel.personal_memory.proposal_repository import (
    proposal_transition_id,
)
from aioa_memory_kernel.personal_memory import proposals as proposal_models


ROOT = REPOSITORY_ROOT


def _slot(tenant: str, owner: str, slot_id: str, at):
    policy = quota_policy(
        tenant_id=tenant,
        owner_user_id=owner,
        policy_id="step29-owner-policy",
    )
    binding = PersonalMemoryModelBinding(
        schema_version=STEP27_SCHEMA_VERSION,
        tenant_id=tenant,
        owner_user_id=owner,
        personal_memory_space_id=slot_id,
        provider_id="provider-neutral",
        model_id="non-gemma-model",
        model_revision_or_declared_version="revision-1",
        binding_mode=PersonalMemoryBindingMode.EXACT_MODEL,
        enabled=True,
        binding_version=1,
        bound_at=at,
    )
    slot = PersonalMemoryHatSlot(
        schema_version=STEP27_SCHEMA_VERSION,
        tenant_id=tenant,
        owner_user_id=owner,
        personal_memory_space_id=slot_id,
        hat_scope_id=personal_memory_hat_scope_id(tenant, owner, slot_id),
        state=PersonalMemorySpaceState.CONFIGURED,
        display_name="Validated corrections",
        quota_policy_id=policy.quota_policy_id,
        quota_policy_digest=policy.policy_digest,
        model_bindings=(binding,),
        state_version=1,
        configuration_version=1,
        created_at=at,
        updated_at=at,
    )
    return policy, slot


@lru_cache(maxsize=1)
def fixture():
    request, _ = hat_lineage()
    answer = assemble_verified_answer(request)
    snapshot = ClaimEvidenceBindingService().freeze_packet_input(
        prepare_claim_binding_request(
            request.draft_v1,
            tuple(item.bundle for item in request.step20_outcomes),
            request.temporal_result,
        )
    )
    trusted_now = request.temporal_result.trusted_now
    policy, slot = _slot(
        request.route.tenant_id,
        request.route.user_id,
        "slot-step29",
        trusted_now,
    )
    model_binding = slot.model_bindings[0]
    claim = request.correction_packet.ordered_claims[0]
    links = tuple(
        sorted(
            (
                item
                for item in snapshot.ordered_evidence_links
                if item.claim_id == claim.claim_id
                and item.relation.value == "SUPPORTS"
            ),
            key=lambda item: item.link_hash,
        )
    )
    candidate = CorrectionCandidate(
        event_id="event-step29",
        tenant_id=request.route.tenant_id,
        user_id=request.route.user_id,
        personal_memory_space_id=slot.personal_memory_space_id,
        source_component=ActorType.KNOWLEDGE_KERNEL,
        run_id=request.route.request_id,
        model_binding_id=model_binding.binding_id,
        draft_v1_reference=request.draft_v1.draft_hash,
        detected_claims=(
            ClaimCandidate(
                claim_id=claim.claim_id,
                draft_id=claim.draft_id,
                statement=claim.exact_claim_text,
                claim_category=claim.claim_type.value,
                scope_dimensions=request.route.effective_scope,
            ),
        ),
        proposed_correction=claim.exact_claim_text,
        available_evidence_references=tuple(item.link_hash for item in links),
        uncertainty=0.0,
        created_at=trusted_now,
        state=CorrectionCandidateState.DETECTED,
    )
    run = KernelRunIdentity(
        kernel_run_id=request.route.request_id,
        tenant_id=request.route.tenant_id,
        user_id=request.route.user_id,
        personal_memory_space_id=slot.personal_memory_space_id,
        model_binding_id=model_binding.binding_id,
        created_at=trusted_now,
    )
    lineage = CorrectionCandidateRouteResultLineage(
        schema_version=STEP28_SCHEMA_VERSION,
        request_id=request.route.request_id,
        original_query_digest=request.draft_v1.original_query_digest,
        route_hash=request.route.route_hash,
        result_hash=answer.answer_hash,
        knowledge_route=request.route.knowledge_route,
        selected_hat_id=request.route.selected_hat_id,
        selected_hat_version=request.route.selected_hat_version,
        selected_manifest_digest=request.route.selected_manifest_digest,
        effective_scope=request.route.effective_scope,
        answer_status=AnswerStatus.VERIFIED,
        evidence_status=request.temporal_result.evidence_status,
        draft_v1_hash=request.draft_v1.draft_hash,
        draft_v2_hash=request.step25_result.draft_v2.draft_v2_hash,
        correction_packet_hash=request.correction_packet.packet_hash,
        verification_summary_hash=(
            request.step25_result.verification_summary.summary_hash
        ),
        verified_answer_hash=answer.answer_hash,
    )
    metadata = CorrectionCandidateMetadata(
        schema_version=STEP28_SCHEMA_VERSION,
        trigger=CorrectionCandidateTrigger.KNOWLEDGE_KERNEL_DETECTED,
        producer_id="kernel-boundary",
        producer_version="1.0.0",
        reason_codes=(CorrectionCandidateReasonCode.SOURCE_KNOWLEDGE_KERNEL,),
    )
    envelope = build_correction_candidate_envelope(
        candidate=candidate,
        kernel_run=run,
        slot=slot,
        route_result_lineage=lineage,
        metadata=metadata,
        idempotency_key="step29-candidate",
        submitted_at=trusted_now + timedelta(seconds=1),
    )
    command = CreatePersonalMemoryPatchProposal(
        STEP29_SCHEMA_VERSION,
        slot.tenant_id,
        slot.owner_user_id,
        slot.personal_memory_space_id,
        envelope.candidate_id,
        envelope.envelope_hash,
        envelope.submission.target_slot_binding.target_binding_hash,
        "step29-create",
        trusted_now + timedelta(seconds=2),
    )
    proposed = build_personal_memory_patch_proposal(envelope, command)
    binding = build_personal_memory_patch_evidence_binding(
        proposed,
        bundles=tuple(item.bundle for item in request.step20_outcomes),
        temporal_result=request.temporal_result,
        claim_links=links,
        claim_assessments=snapshot.ordered_candidate_assessments,
        correction_packet=request.correction_packet,
        verified_answer=answer,
        bound_at=trusted_now + timedelta(seconds=3),
    )
    bound = bind_personal_memory_patch_evidence(
        proposed,
        binding,
        transitioned_at=trusted_now + timedelta(seconds=3),
    )
    receipt = build_personal_memory_patch_validation_receipt(
        bound,
        dedup_result=ProposalDedupResult.PASS,
        conflict_result=ProposalConflictResult.PASS,
        temporal_trusted_now=trusted_now,
        owner_scope_result=ProposalGateResult.PASS,
        slot_state_result=ProposalGateResult.PASS,
        quota_result=ProposalGateResult.PASS,
        model_binding_result=ProposalGateResult.PASS,
        validated_at=trusted_now + timedelta(seconds=4),
    )
    validated = validate_personal_memory_patch(
        bound,
        receipt,
        transitioned_at=trusted_now + timedelta(seconds=4),
    )
    awaiting = advance_personal_memory_patch_to_awaiting_approval(
        validated,
        validation_receipt_hash=receipt.receipt_hash,
        transitioned_at=trusted_now + timedelta(seconds=5),
    )
    return SimpleNamespace(
        request=request,
        answer=answer,
        snapshot=snapshot,
        links=links,
        policy=policy,
        slot=slot,
        envelope=envelope,
        create_command=command,
        proposed=proposed,
        binding=binding,
        bound=bound,
        receipt=receipt,
        validated=validated,
        awaiting=awaiting,
    )


class ProposalContractTests(unittest.TestCase):
    def test_exact_state_machine_reaches_awaiting_approval_without_skips(self):
        value = fixture()
        self.assertEqual(
            [
                value.proposed.state,
                value.bound.state,
                value.validated.state,
                value.awaiting.state,
            ],
            [
                PatchState.PROPOSED,
                PatchState.EVIDENCE_BOUND,
                PatchState.VALIDATED,
                PatchState.AWAITING_APPROVAL,
            ],
        )
        self.assertEqual(
            [
                value.proposed.state_version,
                value.bound.state_version,
                value.validated.state_version,
                value.awaiting.state_version,
            ],
            [1, 2, 3, 4],
        )
        with self.assertRaises(PersonalMemoryPatchValidationError):
            validate_personal_memory_patch(
                value.proposed,
                value.receipt,
                transitioned_at=value.validated.updated_at,
            )
        with self.assertRaises(PersonalMemoryPatchValidationError):
            advance_personal_memory_patch_to_awaiting_approval(
                value.bound,
                validation_receipt_hash=value.receipt.receipt_hash,
                transitioned_at=value.awaiting.updated_at,
            )

    def test_contracts_are_immutable_deterministic_and_strictly_roundtrip(self):
        value = fixture()
        self.assertEqual(
            build_personal_memory_patch_proposal(
                value.envelope, value.create_command
            ),
            value.proposed,
        )
        verify_personal_memory_patch_state(value.awaiting)
        verify_personal_memory_patch_evidence_binding(value.binding)
        verify_personal_memory_patch_validation_receipt(value.receipt)
        serialized = personal_memory_patch_state_to_jsonb(value.awaiting)
        self.assertEqual(parse_personal_memory_patch_state(serialized), value.awaiting)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            value.proposed.state = PatchState.VALIDATED  # type: ignore[misc]
        tampered = json.loads(json.dumps(serialized))
        tampered["proposal"]["proposal_statement"] = "tampered"
        with self.assertRaises((IntegrityError, ContractValidationError)):
            parse_personal_memory_patch_state(tampered)

    def test_json_scope_parser_restores_timestamp_and_string_set_types(self):
        parsed = proposal_models._scope_from_json(
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

    def test_public_state_json_roundtrip_preserves_step38_typed_scope(self):
        value = fixture()
        scope = (
            ScopeDimension(
                "knowledge_as_of",
                datetime(2026, 8, 1, 21, 51, 24, tzinfo=timezone.utc),
                ScopeValueType.TIMESTAMP,
                ScopeComparisonMode.TIMESTAMP,
                "step38-regression",
                True,
            ),
            ScopeDimension(
                "legal_source_class",
                ("DE_FEDERAL_OFFICIAL_CONSOLIDATED_LAW",),
                ScopeValueType.STRING_SET,
                ScopeComparisonMode.IN_SET,
                "step38-regression",
                True,
            ),
        )
        proposal = replace(value.proposed.proposal, proposal_scope=scope)
        state = replace(value.proposed, proposal=proposal)
        serialized = personal_memory_patch_state_to_jsonb(state)
        self.assertEqual(parse_personal_memory_patch_state(serialized), state)

    def test_proposal_binds_candidate_owner_slot_route_and_exact_text(self):
        value = fixture()
        proposal = value.proposed.proposal
        candidate = value.envelope.submission.candidate
        self.assertEqual(proposal.candidate_id, value.envelope.candidate_id)
        self.assertEqual(proposal.candidate_hash, candidate.content_hash)
        self.assertEqual(proposal.proposal_statement, candidate.proposed_correction)
        self.assertEqual(
            proposal.proposal_statement_sha256,
            hashlib.sha256(candidate.proposed_correction.encode()).hexdigest(),
        )
        self.assertEqual(proposal.owner_user_id, value.slot.owner_user_id)
        self.assertEqual(proposal.tenant_id, value.slot.tenant_id)
        self.assertEqual(proposal.route_hash, value.request.route.route_hash)
        self.assertFalse(proposal.canonical_evidence)
        with self.assertRaises(PersonalMemoryPatchValidationError):
            build_personal_memory_patch_proposal(
                value.envelope,
                replace(value.create_command, owner_user_id="other-user"),
            )

    def test_critic_candidate_remains_proposal_only_and_needs_same_evidence_gate(self):
        value = fixture()
        critic_candidate = replace(
            value.envelope.submission.candidate,
            event_id="event-step29-critic",
            source_component=ActorType.CRITIC_PROMPT_LOOP,
        )
        critic_metadata = CorrectionCandidateMetadata(
            schema_version=STEP28_SCHEMA_VERSION,
            trigger=CorrectionCandidateTrigger.CRITIC_PROMPT_LOOP_DETECTED,
            producer_id="critic-step29-test",
            producer_version="1.0.0",
            reason_codes=(
                CorrectionCandidateReasonCode.SOURCE_CRITIC_PROMPT_LOOP,
            ),
        )
        envelope = build_correction_candidate_envelope(
            candidate=critic_candidate,
            kernel_run=value.envelope.submission.run_identity,
            slot=value.slot,
            route_result_lineage=value.envelope.submission.lineage,
            metadata=critic_metadata,
            idempotency_key="step29-critic-candidate",
            submitted_at=value.envelope.submission.submitted_at,
        )
        proposed = build_personal_memory_patch_proposal(
            envelope,
            replace(
                value.create_command,
                candidate_id=envelope.candidate_id,
                candidate_envelope_hash=envelope.envelope_hash,
                idempotency_key="step29-critic-proposal",
            ),
        )
        self.assertEqual(proposed.proposal.origin.value, "CRITIC_PROMPT_LOOP")
        self.assertIs(proposed.state, PatchState.PROPOSED)
        self.assertFalse(proposed.proposal.canonical_evidence)

    def test_evidence_binding_uses_only_exact_verified_kernel_universe(self):
        value = fixture()
        binding = value.binding
        self.assertEqual(
            binding.temporal_resolution_hash,
            value.request.temporal_result.result_hash,
        )
        self.assertEqual(
            binding.correction_packet_hash,
            value.request.correction_packet.packet_hash,
        )
        self.assertEqual(binding.verified_answer_hash, value.answer.answer_hash)
        self.assertEqual(
            tuple(item.evidence_link_hash for item in binding.ordered_evidence_references),
            value.proposed.proposal.candidate_evidence_reference_hashes,
        )
        self.assertTrue(
            all(item.publication_state.value == "PUBLISHED" for item in binding.ordered_evidence_references)
        )
        with self.assertRaises(PersonalMemoryPatchValidationError):
            build_personal_memory_patch_evidence_binding(
                value.proposed,
                bundles=(),
                temporal_result=value.request.temporal_result,
                claim_links=value.links,
                claim_assessments=value.snapshot.ordered_candidate_assessments,
                correction_packet=value.request.correction_packet,
                verified_answer=value.answer,
                bound_at=value.binding.bound_at,
            )

    def test_tampered_or_noncanonical_evidence_never_binds(self):
        value = fixture()
        tampered_packet = copy.deepcopy(value.request.correction_packet)
        object.__setattr__(tampered_packet, "packet_hash", "0" * 64)
        with self.assertRaises(
            (
                IntegrityError,
                PersonalMemoryPatchValidationError,
                CorrectionPacketBoundaryError,
            )
        ):
            build_personal_memory_patch_evidence_binding(
                value.proposed,
                bundles=tuple(
                    item.bundle for item in value.request.step20_outcomes
                ),
                temporal_result=value.request.temporal_result,
                claim_links=value.links,
                claim_assessments=value.snapshot.ordered_candidate_assessments,
                correction_packet=tampered_packet,
                verified_answer=value.answer,
                bound_at=value.binding.bound_at,
            )
        with self.assertRaises((TypeError, AttributeError, PersonalMemoryPatchValidationError)):
            build_personal_memory_patch_evidence_binding(
                value.proposed,
                bundles=(value.envelope,),
                temporal_result=value.request.temporal_result,
                claim_links=value.links,
                claim_assessments=value.snapshot.ordered_candidate_assessments,
                correction_packet=value.request.correction_packet,
                verified_answer=value.answer,
                bound_at=value.binding.bound_at,
            )

    def test_freshness_conflict_dedup_and_all_fail_closed_gates(self):
        value = fixture()
        self.assertTrue(value.receipt.validated)
        for field, replacement_value in (
            ("dedup_result", ProposalDedupResult.EXACT_DUPLICATE),
            ("conflict_result", ProposalConflictResult.DIRECT_CONTRADICTION),
            ("quota_result", ProposalGateResult.FAIL),
            ("model_binding_result", ProposalGateResult.FAIL),
            ("owner_scope_result", ProposalGateResult.FAIL),
            ("slot_state_result", ProposalGateResult.FAIL),
        ):
            arguments = {
                "dedup_result": ProposalDedupResult.PASS,
                "conflict_result": ProposalConflictResult.PASS,
                "temporal_trusted_now": value.request.temporal_result.trusted_now,
                "owner_scope_result": ProposalGateResult.PASS,
                "slot_state_result": ProposalGateResult.PASS,
                "quota_result": ProposalGateResult.PASS,
                "model_binding_result": ProposalGateResult.PASS,
                "validated_at": value.receipt.validated_at,
            }
            arguments[field] = replacement_value
            receipt = build_personal_memory_patch_validation_receipt(
                value.bound, **arguments
            )
            self.assertFalse(receipt.validated)
        stale = build_personal_memory_patch_validation_receipt(
            value.bound,
            dedup_result=ProposalDedupResult.PASS,
            conflict_result=ProposalConflictResult.PASS,
            temporal_trusted_now=value.request.temporal_result.trusted_now,
            owner_scope_result=ProposalGateResult.PASS,
            slot_state_result=ProposalGateResult.PASS,
            quota_result=ProposalGateResult.PASS,
            model_binding_result=ProposalGateResult.PASS,
            validated_at=value.request.temporal_result.trusted_now
            + timedelta(days=2),
        )
        self.assertIs(stale.freshness_result, ProposalFreshnessResult.STALE)
        self.assertFalse(stale.validated)

    def test_direct_conflict_is_deterministic_and_owner_slot_scoped(self):
        value = fixture()
        negative_candidate = replace(
            value.envelope.submission.candidate,
            proposed_correction="Die Vorschrift ist nicht aufgehoben.",
        )
        negative_envelope = build_correction_candidate_envelope(
            candidate=negative_candidate,
            kernel_run=value.envelope.submission.run_identity,
            slot=value.slot,
            route_result_lineage=value.envelope.submission.lineage,
            metadata=value.envelope.submission.metadata,
            idempotency_key="negative-candidate",
            submitted_at=value.envelope.submission.submitted_at,
        )
        negative_state = build_personal_memory_patch_proposal(
            negative_envelope,
            CreatePersonalMemoryPatchProposal(
                STEP29_SCHEMA_VERSION,
                value.slot.tenant_id,
                value.slot.owner_user_id,
                value.slot.personal_memory_space_id,
                negative_envelope.candidate_id,
                negative_envelope.envelope_hash,
                negative_envelope.submission.target_slot_binding.target_binding_hash,
                "negative-proposal",
                value.proposed.updated_at,
            ),
        )
        self.assertIs(
            proposal_conflict_between(
                value.proposed.proposal, negative_state.proposal
            ),
            ProposalConflictResult.DIRECT_CONTRADICTION,
        )
        self.assertEqual(
            value.proposed.proposal.exact_dedup_key,
            build_personal_memory_patch_proposal(
                value.envelope, value.create_command
            ).proposal.exact_dedup_key,
        )

    def test_awaiting_approval_requires_exact_receipt_and_grants_nothing(self):
        value = fixture()
        self.assertIs(value.awaiting.state, PatchState.AWAITING_APPROVAL)
        self.assertTrue(value.awaiting.validation_receipt.validated)
        with self.assertRaises(PersonalMemoryPatchValidationError):
            advance_personal_memory_patch_to_awaiting_approval(
                value.validated,
                validation_receipt_hash="0" * 64,
                transitioned_at=value.awaiting.updated_at,
            )
        serialized = json.dumps(personal_memory_patch_state_to_jsonb(value.awaiting))
        for forbidden in (
            '"approval_actor"',
            '"approval_token"',
            '"commit_id"',
            '"activation_receipt"',
        ):
            self.assertNotIn(forbidden, serialized)

    def test_validation_policy_is_hash_bound_and_model_neutral(self):
        policy = load_personal_memory_patch_validation_policy()
        self.assertEqual(policy, load_personal_memory_patch_validation_policy())
        self.assertEqual(len(policy.policy_digest), 64)
        source = inspect.getsource(
            __import__(
                "aioa_memory_kernel.personal_memory.proposals",
                fromlist=["proposals"],
            )
        )
        self.assertNotIn("Gemma", source)
        self.assertNotIn("vector_search", source)
        self.assertNotIn("web_search", source)


class InMemorySlotRepository:
    def __init__(self, value):
        self.slot = value.slot
        self.policy = value.policy
        self.active_patches = 0

    def get_slot(self, transaction, tenant, owner, slot_id):
        if (tenant, owner, slot_id) == (
            self.slot.tenant_id,
            self.slot.owner_user_id,
            self.slot.personal_memory_space_id,
        ):
            return self.slot
        return None

    def get_quota_policy(self, transaction, tenant, owner, policy_id):
        if (tenant, owner, policy_id) == (
            self.policy.tenant_id,
            self.policy.owner_user_id,
            self.policy.quota_policy_id,
        ):
            return self.policy
        return None

    def owner_usage(self, transaction, **values):
        return PersonalMemoryQuotaUsageView(
            schema_version=STEP27_SCHEMA_VERSION,
            tenant_id=self.slot.tenant_id,
            owner_user_id=self.slot.owner_user_id,
            personal_memory_space_id=self.slot.personal_memory_space_id,
            quota_policy_digest=self.policy.policy_digest,
            usage=PersonalHatQuotaUsage(
                total_spaces=1,
                active_spaces=0,
                archived_spaces=0,
                active_memory_patches=self.active_patches,
            ),
            model_binding_count=1,
            memory_item_count=0,
            patch_count=self.active_patches,
            stored_bytes=0,
        )


class InMemoryCandidateRepository:
    def __init__(self, value):
        self.value = value.envelope

    def get_candidate(self, transaction, tenant, owner, candidate_id):
        if (tenant, owner, candidate_id) == (
            self.value.submission.candidate.tenant_id,
            self.value.submission.candidate.user_id,
            self.value.candidate_id,
        ):
            return self.value
        return None


class InMemoryProposalRepository:
    def __init__(self):
        self.values = {}
        self.transitions = []
        self.peers = []

    def get_proposal(self, transaction, tenant, owner, proposal_id):
        value = self.values.get(proposal_id)
        if value is None:
            return None
        if (tenant, owner) != (
            value.proposal.tenant_id,
            value.proposal.owner_user_id,
        ):
            return None
        return value

    def get_by_dedup_key(self, transaction, tenant, owner, slot, dedup):
        return next(
            (
                value
                for value in self.values.values()
                if value.proposal.tenant_id == tenant
                and value.proposal.owner_user_id == owner
                and value.proposal.personal_memory_space_id == slot
                and value.proposal.exact_dedup_key == dedup
            ),
            None,
        )

    def proposal_usage(self, transaction, tenant, owner, slot):
        values = [
            value
            for value in self.values.values()
            if value.proposal.tenant_id == tenant
            and value.proposal.owner_user_id == owner
            and value.proposal.personal_memory_space_id == slot
        ]
        return ProposalUsage(len(values), sum(len(json.dumps(personal_memory_patch_state_to_jsonb(v))) for v in values))

    def insert_proposal(self, transaction, state):
        existing = self.get_by_dedup_key(
            transaction,
            state.proposal.tenant_id,
            state.proposal.owner_user_id,
            state.proposal.personal_memory_space_id,
            state.proposal.exact_dedup_key,
        )
        if existing is not None:
            return existing, False
        self.values[state.proposal.proposal_id] = state
        return state, True

    def transition_proposal(self, transaction, *, expected, updated):
        if self.values.get(expected.proposal.proposal_id) != expected:
            raise RuntimeError("compare-and-set conflict")
        self.values[expected.proposal.proposal_id] = updated
        return updated

    def insert_transition_event(self, transaction, **values):
        transition_id = proposal_transition_id(
            values["proposal"],
            values["state_before"],
            values["state_after"],
            values["state_version"],
        )
        self.transitions.append(transition_id)
        return transition_id

    def list_peers(self, transaction, **values):
        return tuple(self.peers)


class ProposalServiceTests(unittest.TestCase):
    def setUp(self):
        self.value = fixture()
        self.slots = InMemorySlotRepository(self.value)
        self.candidates = InMemoryCandidateRepository(self.value)
        self.proposals = InMemoryProposalRepository()
        self.idempotency = FakeIdempotency()
        self.runner = SerializableTransactionRunner(lambda: None)
        self.patch = mock.patch.object(
            self.runner,
            "run",
            side_effect=lambda context, callback, **kwargs: callback(object()),
        )
        self.patch.start()
        self.service = PersonalMemoryPatchProposalService(
            self.runner,
            slot_repository=self.slots,
            candidate_repository=self.candidates,
            proposal_repository=self.proposals,
            idempotency=self.idempotency,
        )

    def tearDown(self):
        self.patch.stop()

    def _bind_command(self, state):
        return BindPersonalMemoryPatchEvidence(
            STEP29_SCHEMA_VERSION,
            state.proposal.tenant_id,
            state.proposal.owner_user_id,
            state.proposal.personal_memory_space_id,
            state.proposal.proposal_id,
            state.proposal.proposal_hash,
            state.state,
            state.state_version,
            state.state_hash,
            "step29-bind",
            self.value.binding.bound_at,
        )

    def _validate_command(self, state):
        return ValidatePersonalMemoryPatchProposal(
            STEP29_SCHEMA_VERSION,
            state.proposal.tenant_id,
            state.proposal.owner_user_id,
            state.proposal.personal_memory_space_id,
            state.proposal.proposal_id,
            state.proposal.proposal_hash,
            state.state,
            state.state_version,
            state.state_hash,
            "step29-validate",
            self.value.receipt.validated_at,
        )

    def _evidence(self):
        return {
            "bundles": tuple(item.bundle for item in self.value.request.step20_outcomes),
            "temporal_result": self.value.request.temporal_result,
            "claim_links": self.value.links,
            "claim_assessments": self.value.snapshot.ordered_candidate_assessments,
            "correction_packet": self.value.request.correction_packet,
            "verified_answer": self.value.answer,
        }

    def test_service_runs_all_four_edges_and_exact_replay(self):
        proposed, first = self.service.create_proposal(
            self.value.envelope, self.value.create_command
        )
        replayed, replay = self.service.create_proposal(
            self.value.envelope, self.value.create_command
        )
        self.assertEqual(proposed, replayed)
        self.assertFalse(first.idempotent_replay)
        self.assertTrue(replay.idempotent_replay)
        bound, _ = self.service.bind_evidence(
            self._bind_command(proposed), **self._evidence()
        )
        validated, receipt, _ = self.service.validate_proposal(
            self._validate_command(bound), **self._evidence()
        )
        awaiting, _ = self.service.advance_to_awaiting_approval(
            AdvancePersonalMemoryPatchToAwaitingApproval(
                STEP29_SCHEMA_VERSION,
                validated.proposal.tenant_id,
                validated.proposal.owner_user_id,
                validated.proposal.personal_memory_space_id,
                validated.proposal.proposal_id,
                validated.proposal.proposal_hash,
                validated.state,
                validated.state_version,
                validated.state_hash,
                "step29-await",
                self.value.awaiting.updated_at,
                receipt.receipt_hash,
            )
        )
        self.assertIs(awaiting.state, PatchState.AWAITING_APPROVAL)
        self.assertEqual(len(self.proposals.transitions), 4)

    def test_service_revalidates_owner_slot_model_quota_and_conflict(self):
        proposed, _ = self.service.create_proposal(
            self.value.envelope, self.value.create_command
        )
        bound, _ = self.service.bind_evidence(
            self._bind_command(proposed), **self._evidence()
        )
        self.slots.active_patches = self.value.policy.limits.maximum_active_memory_patches
        _, quota_receipt, transition = self.service.validate_proposal(
            self._validate_command(bound), **self._evidence()
        )
        self.assertFalse(quota_receipt.validated)
        self.assertIsNone(transition)
        self.assertIs(quota_receipt.quota_result, ProposalGateResult.FAIL)

    def test_service_duplicate_or_conflict_never_validates(self):
        proposed, _ = self.service.create_proposal(
            self.value.envelope, self.value.create_command
        )
        bound, _ = self.service.bind_evidence(
            self._bind_command(proposed), **self._evidence()
        )
        self.proposals.peers = [
            ProposalPeer(
                proposal_id="existing-proposal",
                lifecycle_state=PatchState.AWAITING_APPROVAL,
                statement=proposed.proposal.proposal_statement,
                exact_dedup_key=proposed.proposal.exact_dedup_key,
                conflict_subject_sha256=proposed.proposal.conflict_subject_sha256,
                negative_polarity=proposed.proposal.negative_polarity,
                proposal=None,
            )
        ]
        _, receipt, transition = self.service.validate_proposal(
            self._validate_command(bound), **self._evidence()
        )
        self.assertIs(receipt.dedup_result, ProposalDedupResult.EXACT_DUPLICATE)
        self.assertFalse(receipt.validated)
        self.assertIsNone(transition)

    def test_existing_active_patch_duplicate_is_detected_not_modified(self):
        proposed, _ = self.service.create_proposal(
            self.value.envelope, self.value.create_command
        )
        bound, _ = self.service.bind_evidence(
            self._bind_command(proposed), **self._evidence()
        )
        self.proposals.peers = [
            ProposalPeer(
                proposal_id="existing-active-patch",
                lifecycle_state=PatchState.ACTIVE,
                statement=proposed.proposal.proposal_statement,
                exact_dedup_key=proposed.proposal.exact_dedup_key,
                conflict_subject_sha256=proposed.proposal.conflict_subject_sha256,
                negative_polarity=proposed.proposal.negative_polarity,
                proposal=None,
            )
        ]
        unchanged, receipt, transition = self.service.validate_proposal(
            self._validate_command(bound), **self._evidence()
        )
        self.assertEqual(unchanged, bound)
        self.assertIs(
            receipt.dedup_result,
            ProposalDedupResult.EXISTING_PATCH_DUPLICATE,
        )
        self.assertFalse(receipt.validated)
        self.assertIsNone(transition)

    def test_archived_slot_and_cross_owner_commands_fail_closed(self):
        proposed, _ = self.service.create_proposal(
            self.value.envelope, self.value.create_command
        )
        self.slots.slot = replace(
            self.slots.slot,
            state=PersonalMemorySpaceState.ARCHIVED,
            state_version=2,
            updated_at=self.slots.slot.updated_at + timedelta(seconds=10),
        )
        with self.assertRaises(PersonalMemoryPatchValidationError):
            self.service.bind_evidence(
                self._bind_command(proposed), **self._evidence()
            )
        with self.assertRaises((ContractValidationError, PersonalMemoryPatchValidationError)):
            self.service.bind_evidence(
                replace(self._bind_command(proposed), owner_user_id="other-user"),
                **self._evidence(),
            )


class PersistenceAndBoundaryTests(unittest.TestCase):
    def test_row_roundtrip_and_repository_exposes_no_step30_operation(self):
        value = fixture().awaiting
        row = {
            "proposal_id": value.proposal.proposal_id,
            "proposed_content": personal_memory_patch_state_to_jsonb(value),
            "lifecycle_state": value.state.value,
            "content_hash": value.proposal.proposal_hash,
            "step29_dedup_key": value.proposal.exact_dedup_key,
            "step29_candidate_id": value.proposal.candidate_id,
            "step29_candidate_hash": value.proposal.candidate_hash,
            "step29_candidate_envelope_hash": (
                value.proposal.candidate_envelope_hash
            ),
            "step29_target_binding_hash": value.proposal.target_binding_hash,
            "step29_state_version": value.state_version,
            "step29_state_hash": value.state_hash,
            "step29_evidence_binding_hash": value.evidence_binding.binding_hash,
            "step29_validation_receipt_hash": value.validation_receipt.receipt_hash,
        }
        self.assertEqual(proposal_state_from_row(row), value)
        public = {
            name
            for name in dir(PersonalMemoryPatchProposalCockroachRepository)
            if not name.startswith("_")
            and callable(
                getattr(PersonalMemoryPatchProposalCockroachRepository, name)
            )
        }
        self.assertEqual(
            public,
            {
                "get_proposal",
                "get_by_dedup_key",
                "proposal_usage",
                "insert_proposal",
                "transition_proposal",
                "insert_transition_event",
                "list_peers",
            },
        )

    def test_migration_0013_is_rls_force_and_step29_only(self):
        migration = (
            ROOT
            / "sql/cockroachdb/migrations/0013_step29_personal_memory_patch_validation.sql"
        ).read_text(encoding="utf-8")
        for fragment in (
            "memory_patch_proposals_s29_insert",
            "memory_patch_proposals_s29_update",
            "patch_transition_records_s29_insert",
            "ENABLE ROW LEVEL SECURITY",
            "FORCE ROW LEVEL SECURITY",
            "memory_patch_proposals_s29_exact_dedup",
            "memory_patch_proposals_s29_candidate_lineage_fk",
            "enforce_step29_proposal_quota",
            "BEFORE INSERT OR UPDATE",
            "FROM memory_patch.personal_memory_spaces AS current_space",
            "proposal.proposal_id <> (NEW).proposal_id",
            "guard_step29_proposal_transition",
            "VALIDATED' AND state_after = 'AWAITING_APPROVAL",
        ):
            self.assertIn(fragment, migration)
        self.assertNotIn("IF NOT FOUND", migration)
        self.assertNotIn(
            "FROM memory_patch.memory_patch_proposals AS candidate",
            migration,
        )
        self.assertNotRegex(
            migration,
            r"(?:lifecycle_state|state_after)\s*=\s*'(?:APPROVED|COMMITTED|ACTIVE)'",
        )
        self.assertNotIn("GRANT DELETE", migration)

    def test_manifest_and_security_config_pin_step29(self):
        manifest = json.loads(
            (ROOT / "sql/cockroachdb/migrations/manifest.json").read_text()
        )
        self.assertEqual(manifest["schema_version"], 16)
        self.assertEqual(manifest["runner_version"], "16.0.0")
        self.assertEqual(len(manifest["migrations"]), 18)
        latest = next(
            item
            for item in manifest["migrations"]
            if item["migration_id"]
            == "0013_step29_personal_memory_patch_validation"
        )
        self.assertEqual(
            latest["migration_id"],
            "0013_step29_personal_memory_patch_validation",
        )
        self.assertEqual(
            latest["sha256"],
            hashlib.sha256(
                (ROOT / "sql/cockroachdb/migrations" / latest["filename"]).read_bytes()
            ).hexdigest(),
        )
        security = json.loads(
            (
                ROOT
                / "config/cockroachdb/personal-memory-patch-validation-security-1a.json"
            ).read_text()
        )
        self.assertEqual(security["maximum_lifecycle_state"], "AWAITING_APPROVAL")
        self.assertEqual(len(security["transition_edges"]), 4)
        self.assertTrue(all(row["force_rls"] for row in security["tables"]))

    def test_no_model_network_retrieval_approval_commit_or_activation_capability(self):
        source = "\n".join(
            (
                (ROOT / "src/aioa_memory_kernel/personal_memory/proposals.py").read_text(),
                (ROOT / "src/aioa_memory_kernel/personal_memory/proposal_service.py").read_text(),
                (ROOT / "src/aioa_memory_kernel/personal_memory/proposal_repository.py").read_text(),
            )
        )
        for forbidden in (
            "subprocess",
            "os.system",
            "shell=True",
            "vector_search",
            "hybrid_retrieval",
            "web_search",
            "httpx",
            "requests.",
            "commit_helper",
            "activate_patch",
        ):
            self.assertNotIn(forbidden, source)
        service_public = {
            name
            for name, member in inspect.getmembers(
                PersonalMemoryPatchProposalService, inspect.isfunction
            )
            if not name.startswith("_")
        }
        self.assertEqual(
            service_public,
            {
                "create_proposal",
                "bind_evidence",
                "validate_proposal",
                "advance_to_awaiting_approval",
                "get_proposal",
            },
        )

    def test_closure_documents_evidence_and_live_checkpoints_are_exact(self):
        for relative in (
            "docs/architecture/PERSONAL_MEMORY_PATCH_PROPOSAL_EVIDENCE_VALIDATION_1A.md",
            "docs/adr/ADR-036-personal-memory-patch-proposal-validation.md",
            "docs/operations/STEP_29_PERSONAL_MEMORY_PATCH_VALIDATION_1A.md",
            "docs/audits/STEP_29_PERSONAL_MEMORY_PATCH_PROPOSAL_VALIDATION_CLOSURE_1A.md",
        ):
            self.assertTrue((ROOT / relative).is_file(), relative)
        evidence = json.loads(
            (
                ROOT
                / "docs/evidence/personal-memory/step29-personal-memory-patch-validation.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(evidence["step"], 29)
        self.assertEqual(evidence["status"], "PASS")
        self.assertEqual(evidence["start_sha"], "8ee125e3ab4b964c4ed85dcee95b08932fe0cab5")
        self.assertFalse(evidence["step30_started"])
        self.assertEqual(evidence["approved_transitions"], 0)
        self.assertEqual(evidence["committed_transitions"], 0)
        self.assertEqual(evidence["active_transitions"], 0)
        digest = evidence.pop("validation_digest")
        self.assertEqual(digest, canonical_sha256(evidence))

    def test_roadmap_and_agents_preserve_step29_history_and_close_step30(self):
        roadmap = (ROOT / "docs/roadmap/PRODUCTION_ROADMAP.md").read_text()
        agents = (ROOT / "AGENTS.md").read_text()
        self.assertIn("`Step 29: COMPLETE AND PUSHED at actual closure commit`", roadmap)
        self.assertIn("- [x] **Step 30", roadmap)
        self.assertIn("- [x] **Step 31", roadmap)
        self.assertIn("- [x] **Step 32", roadmap)
        self.assertIn("- [x] **Step 33", roadmap)
        self.assertIn("- [x] **Step 34", roadmap)
        self.assertIn("- [x] **Step 35", roadmap)
        self.assertIn("- [x] **Step 36", roadmap)
        self.assertIn("- [x] **Step 37", roadmap)
        self.assertIn("- [x] **Step 38", roadmap)
        self.assertIn("- [ ] **Step 39", roadmap)
        self.assertIn("Step 39: NOT STARTED", roadmap)
        self.assertIn("Step 38 completion does not authorize Step 39.", roadmap)
        self.assertIn("Step 29: COMPLETE AND PUSHED", agents)
        self.assertIn("Step 30: COMPLETE AND PUSHED", agents)
        self.assertIn("Step 31: COMPLETE AND PUSHED", agents)
        self.assertIn("Step 32: COMPLETE AND PUSHED", agents)
        self.assertIn("Step 33: COMPLETE AND PUSHED", agents)
        self.assertIn("Step 34: COMPLETE AND PUSHED", agents)
        self.assertIn("Step 35: COMPLETE AND PUSHED", agents)
        self.assertIn("Step 36: COMPLETE AND PUSHED", agents)
        self.assertIn("Step 37: COMPLETE AND PUSHED", agents)
        self.assertIn("Step 38: COMPLETE AND PUSHED", agents)
        self.assertIn("Step 39: NOT STARTED", agents)
        self.assertIn("Step 38 completion does not authorize Step 39.", agents)


if __name__ == "__main__":
    unittest.main()
