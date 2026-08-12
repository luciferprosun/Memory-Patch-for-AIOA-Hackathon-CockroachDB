"""Step 28 candidate-only Kernel/Critic bridge tests."""

from __future__ import annotations

import dataclasses
import hashlib
import inspect
import json
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tests._support import REPOSITORY_ROOT

from aioa_memory_kernel.contracts.correction import CorrectionCandidate
from aioa_memory_kernel.contracts.enums import (
    ActorType,
    AnswerStatus,
    CorrectionCandidateState,
    EvidenceStatus,
    KnowledgeRoute,
    PersonalMemorySpaceState,
    ScopeComparisonMode,
    ScopeValueType,
)
from aioa_memory_kernel.contracts.evidence import ClaimCandidate
from aioa_memory_kernel.contracts.exceptions import (
    ContractValidationError,
    IntegrityError,
    OwnershipViolation,
)
from aioa_memory_kernel.contracts.identities import KernelRunIdentity
from aioa_memory_kernel.contracts.scope import ScopeDimension
from aioa_memory_kernel.contracts.serialization import canonical_sha256, to_canonical_data
from aioa_memory_kernel.persistence import (
    IdempotencyConflictError,
    SerializableTransactionRunner,
)
from aioa_memory_kernel.personal_memory.candidate_repository import (
    CorrectionCandidateCockroachRepository,
    candidate_envelope_from_row,
)
from aioa_memory_kernel.personal_memory.candidate_service import (
    CorrectionCandidateBridgeService,
)
from aioa_memory_kernel.personal_memory.candidates import (
    CORRECTION_CANDIDATE_SOURCE_ALLOWLIST,
    MAXIMUM_CANDIDATES_PER_SLOT,
    STEP28_SCHEMA_VERSION,
    CorrectionCandidateEnvelope,
    CorrectionCandidateIntakeDisposition,
    CorrectionCandidateIntakeError,
    CorrectionCandidateMetadata,
    CorrectionCandidateReasonCode,
    CorrectionCandidateRouteResultLineage,
    CorrectionCandidateTrigger,
    build_correction_candidate_envelope,
    build_correction_candidate_intake_receipt,
    correction_candidate_envelope_to_jsonb,
    correction_candidate_receipt_to_jsonb,
    correction_candidate_semantic_deduplication_key,
    load_correction_candidate_intake_policy,
    parse_correction_candidate_envelope,
    parse_correction_candidate_intake_receipt,
    verify_correction_candidate_envelope,
    verify_correction_candidate_intake_receipt,
)
from aioa_memory_kernel.personal_memory.models import (
    STEP27_SCHEMA_VERSION,
    PersonalMemoryBindingMode,
    PersonalMemoryHatSlot,
    PersonalMemoryModelBinding,
    personal_memory_hat_scope_id,
)


ROOT = REPOSITORY_ROOT
BASE_SHA = "a4317d4c5689d35f649f19b45646e7205876581f"
NOW = datetime(2042, 2, 3, 4, 5, 6, tzinfo=UTC)


def digest(label: str) -> str:
    return canonical_sha256({"step28-test": label})


def model_binding(
    *,
    tenant_id: str = "tenant-a",
    owner_user_id: str = "user-a",
    slot_id: str = "slot-a",
) -> PersonalMemoryModelBinding:
    return PersonalMemoryModelBinding(
        schema_version=STEP27_SCHEMA_VERSION,
        tenant_id=tenant_id,
        owner_user_id=owner_user_id,
        personal_memory_space_id=slot_id,
        provider_id="provider-a",
        model_id="model-a",
        model_revision_or_declared_version="revision-1",
        binding_mode=PersonalMemoryBindingMode.EXACT_MODEL,
        enabled=True,
        binding_version=1,
        bound_at=NOW,
    )


def personal_slot(
    *,
    state: PersonalMemorySpaceState = PersonalMemorySpaceState.CONFIGURED,
    tenant_id: str = "tenant-a",
    owner_user_id: str = "user-a",
    slot_id: str = "slot-a",
    binding: PersonalMemoryModelBinding | None = None,
    state_version: int = 1,
    configuration_version: int = 2,
    updated_at: datetime = NOW,
) -> PersonalMemoryHatSlot:
    selected_binding = binding or model_binding(
        tenant_id=tenant_id,
        owner_user_id=owner_user_id,
        slot_id=slot_id,
    )
    deletion_requested_at = (
        updated_at
        if state in {
            PersonalMemorySpaceState.DELETED_PENDING,
            PersonalMemorySpaceState.DELETED,
        }
        else None
    )
    deleted_at = updated_at if state is PersonalMemorySpaceState.DELETED else None
    inert = state is PersonalMemorySpaceState.DELETED
    return PersonalMemoryHatSlot(
        schema_version=STEP27_SCHEMA_VERSION,
        tenant_id=tenant_id,
        owner_user_id=owner_user_id,
        personal_memory_space_id=slot_id,
        hat_scope_id=personal_memory_hat_scope_id(
            tenant_id, owner_user_id, slot_id
        ),
        state=state,
        display_name=None if inert else "Owner corrections",
        quota_policy_id="quota-owner-v1",
        quota_policy_digest=digest("quota-owner-v1"),
        model_bindings=() if inert else (selected_binding,),
        state_version=state_version,
        configuration_version=configuration_version,
        created_at=NOW,
        updated_at=updated_at,
        deletion_requested_at=deletion_requested_at,
        deleted_at=deleted_at,
    )


def scope() -> tuple[ScopeDimension, ...]:
    return (
        ScopeDimension(
            name="domain",
            value="german-law",
            value_type=ScopeValueType.STRING,
            comparison_mode=ScopeComparisonMode.EXACT,
            source="route-result",
            required=True,
        ),
    )


def envelope(
    *,
    source: ActorType = ActorType.KNOWLEDGE_KERNEL,
    slot: PersonalMemoryHatSlot | None = None,
    idempotency_key: str = "candidate-command-a",
    proposed_correction: str = "Use the verified effective date.",
    event_id: str = "candidate-event-a",
    run_id: str = "run-a",
    route_hash: str | None = None,
    trigger: CorrectionCandidateTrigger | None = None,
    owner_user_id: str | None = None,
    tenant_id: str | None = None,
) -> CorrectionCandidateEnvelope:
    selected_slot = slot or personal_slot()
    tenant = tenant_id or selected_slot.tenant_id
    owner = owner_user_id or selected_slot.owner_user_id
    selected_binding = selected_slot.model_bindings[0]
    draft_v1_hash = digest("draft-v1")
    candidate = CorrectionCandidate(
        event_id=event_id,
        tenant_id=tenant,
        user_id=owner,
        personal_memory_space_id=selected_slot.personal_memory_space_id,
        source_component=source,
        run_id=run_id,
        model_binding_id=selected_binding.binding_id,
        draft_v1_reference=draft_v1_hash,
        detected_claims=(
            ClaimCandidate(
                claim_id="claim-a",
                draft_id="draft-v1-a",
                statement="The rule is already effective.",
                claim_category="TEMPORAL",
                scope_dimensions=scope(),
            ),
        ),
        proposed_correction=proposed_correction,
        available_evidence_references=(digest("evidence-a"),),
        uncertainty=0.25,
        created_at=NOW + timedelta(seconds=1),
        state=CorrectionCandidateState.DETECTED,
    )
    kernel_run = KernelRunIdentity(
        kernel_run_id=run_id,
        tenant_id=tenant,
        user_id=owner,
        personal_memory_space_id=selected_slot.personal_memory_space_id,
        model_binding_id=selected_binding.binding_id,
        created_at=NOW,
    )
    lineage = CorrectionCandidateRouteResultLineage(
        schema_version=STEP28_SCHEMA_VERSION,
        request_id=run_id,
        original_query_digest=digest("query"),
        route_hash=route_hash or digest("route"),
        result_hash=digest("verified-answer-result"),
        knowledge_route=KnowledgeRoute.HAT_ENFORCE,
        selected_hat_id="german-law",
        selected_hat_version="1.0.0",
        selected_manifest_digest=digest("manifest"),
        effective_scope=scope(),
        answer_status=AnswerStatus.VERIFIED,
        evidence_status=EvidenceStatus.SUFFICIENT,
        draft_v1_hash=draft_v1_hash,
        draft_v2_hash=digest("draft-v2"),
        correction_packet_hash=digest("packet"),
        verification_summary_hash=digest("verification-summary"),
        verified_answer_hash=digest("verified-answer"),
    )
    is_kernel = source is ActorType.KNOWLEDGE_KERNEL
    metadata = CorrectionCandidateMetadata(
        schema_version=STEP28_SCHEMA_VERSION,
        trigger=(
            trigger
            or (
                CorrectionCandidateTrigger.KNOWLEDGE_KERNEL_DETECTED
                if is_kernel
                else CorrectionCandidateTrigger.CRITIC_PROMPT_LOOP_DETECTED
            )
        ),
        producer_id="kernel-boundary" if is_kernel else "critic-loop-a",
        producer_version="1.0.0",
        reason_codes=(
            CorrectionCandidateReasonCode.SOURCE_KNOWLEDGE_KERNEL
            if is_kernel
            else CorrectionCandidateReasonCode.SOURCE_CRITIC_PROMPT_LOOP,
        ),
    )
    return build_correction_candidate_envelope(
        candidate=candidate,
        kernel_run=kernel_run,
        slot=selected_slot,
        route_result_lineage=lineage,
        metadata=metadata,
        idempotency_key=idempotency_key,
        submitted_at=NOW + timedelta(seconds=2),
    )


class FakeIdempotency:
    def __init__(self) -> None:
        self.operations: dict[tuple[str, str, str, str], dict[str, object]] = {}

    def begin_or_resume_operation(self, transaction: object, request: object):
        key = (
            request.tenant_id,
            request.owner_user_id,
            request.operation_kind,
            request.idempotency_key,
        )
        existing = self.operations.get(key)
        if existing is not None:
            if (
                existing["request_digest"] != request.request_digest
                or existing["scope_digest"] != request.scope_digest
            ):
                raise IdempotencyConflictError(
                    "conflicting candidate replay",
                    sanitized_code="TEST_CANDIDATE_REPLAY_CONFLICT",
                )
            return SimpleNamespace(
                may_proceed=not existing["completed"],
                operation=SimpleNamespace(
                    operation_id=existing["operation_id"],
                    attempt_count=1,
                    result_ref=existing.get("result_ref"),
                    result_digest=existing.get("result_digest"),
                ),
            )
        self.operations[key] = {
            "completed": False,
            "operation_id": request.operation_id,
            "request_digest": request.request_digest,
            "scope_digest": request.scope_digest,
        }
        return SimpleNamespace(
            may_proceed=True,
            operation=SimpleNamespace(
                operation_id=request.operation_id,
                attempt_count=1,
                result_ref=None,
                result_digest=None,
            ),
        )

    def complete_operation(self, transaction: object, **values: object):
        for operation in self.operations.values():
            if operation["operation_id"] == values["operation_id"]:
                operation["completed"] = True
                operation["result_ref"] = values["result_ref"]
                operation["result_digest"] = values["result_digest"]
                return values
        raise AssertionError("test idempotency operation was not found")


class InMemorySlotRepository:
    def __init__(self) -> None:
        self.slots: dict[tuple[str, str, str], PersonalMemoryHatSlot] = {}

    def get_slot(self, transaction: object, tenant: str, owner: str, slot_id: str):
        return self.slots.get((tenant, owner, slot_id))


class InMemoryCandidateRepository:
    def __init__(self) -> None:
        self.candidates: dict[tuple[str, str, str], CorrectionCandidateEnvelope] = {}
        self.scope_projections: list[str] = []
        self.usage_override: tuple[int, int] | None = None

    @staticmethod
    def key(value: CorrectionCandidateEnvelope) -> tuple[str, str, str]:
        candidate = value.submission.candidate
        return candidate.tenant_id, candidate.user_id, value.candidate_id

    def get_candidate(
        self, transaction: object, tenant: str, owner: str, candidate_id: str
    ):
        return self.candidates.get((tenant, owner, candidate_id))

    def list_owner_candidates(
        self, transaction: object, tenant: str, owner: str, slot_id: str
    ):
        return tuple(
            sorted(
                (
                    value
                    for (row_tenant, row_owner, _), value in self.candidates.items()
                    if row_tenant == tenant
                    and row_owner == owner
                    and value.submission.candidate.personal_memory_space_id == slot_id
                ),
                key=lambda value: value.candidate_id,
            )
        )

    def candidate_usage(
        self, transaction: object, tenant: str, owner: str, slot_id: str
    ):
        if self.usage_override is not None:
            return self.usage_override
        values = self.list_owner_candidates(
            transaction, tenant, owner, slot_id
        )
        return len(values), sum(len(value.canonical_bytes()) for value in values)

    def ensure_target_hat_scope(self, transaction: object, target: object, at: datetime):
        self.scope_projections.append(target.hat_scope_id)

    def insert_candidate(self, transaction: object, value: CorrectionCandidateEnvelope):
        key = self.key(value)
        existing = self.candidates.get(key)
        if existing is not None:
            if (
                existing.submission.semantic_deduplication_key
                != value.submission.semantic_deduplication_key
            ):
                raise AssertionError("test semantic candidate collision")
            return existing, False
        self.candidates[key] = value
        return value, True


class ServiceHarness:
    def __init__(self, slot: PersonalMemoryHatSlot | None = None) -> None:
        self.slot_repository = InMemorySlotRepository()
        self.candidate_repository = InMemoryCandidateRepository()
        self.idempotency = FakeIdempotency()
        self.slot = slot or personal_slot()
        self.slot_repository.slots[
            (
                self.slot.tenant_id,
                self.slot.owner_user_id,
                self.slot.personal_memory_space_id,
            )
        ] = self.slot
        self.runner = SerializableTransactionRunner(lambda: None)
        self.runner_patch = mock.patch.object(
            self.runner,
            "run",
            side_effect=lambda context, callback, **kwargs: callback(object()),
        )
        self.runner_patch.start()
        self.service = CorrectionCandidateBridgeService(
            self.runner,
            slot_repository=self.slot_repository,  # type: ignore[arg-type]
            candidate_repository=self.candidate_repository,  # type: ignore[arg-type]
            idempotency=self.idempotency,  # type: ignore[arg-type]
        )

    def close(self) -> None:
        self.runner_patch.stop()


class CorrectionCandidateContractTests(unittest.TestCase):
    def test_envelope_is_immutable_hash_bound_and_deterministic(self) -> None:
        first = envelope()
        second = envelope()
        self.assertEqual(first, second)
        self.assertEqual(first.envelope_hash, second.envelope_hash)
        self.assertEqual(first.candidate_id, second.candidate_id)
        self.assertEqual(
            first.submission.candidate_text_sha256,
            hashlib.sha256(
                first.submission.candidate.proposed_correction.encode("utf-8")
            ).hexdigest(),
        )
        self.assertEqual(first.submission.candidate.state, CorrectionCandidateState.DETECTED)
        verify_correction_candidate_envelope(first)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            first.candidate_id = "changed"  # type: ignore[misc]

    def test_changed_semantics_change_hashes_and_target(self) -> None:
        first = envelope()
        changed_text = envelope(proposed_correction="Use the historical date.")
        changed_slot = envelope(slot=personal_slot(slot_id="slot-b"))
        changed_owner = envelope(
            slot=personal_slot(owner_user_id="user-b", slot_id="slot-b")
        )
        for changed in (changed_text, changed_slot, changed_owner):
            self.assertNotEqual(first.envelope_hash, changed.envelope_hash)
            self.assertNotEqual(first.candidate_id, changed.candidate_id)

    def test_envelope_and_receipt_json_roundtrip_and_tamper_rejection(self) -> None:
        value = envelope()
        serialized = correction_candidate_envelope_to_jsonb(value)
        self.assertEqual(parse_correction_candidate_envelope(serialized), value)
        receipt = build_correction_candidate_intake_receipt(
            value, accepted_at=NOW + timedelta(seconds=2)
        )
        receipt_json = correction_candidate_receipt_to_jsonb(receipt)
        self.assertEqual(parse_correction_candidate_intake_receipt(receipt_json), receipt)
        verify_correction_candidate_intake_receipt(receipt)
        tampered = json.loads(json.dumps(serialized))
        tampered["submission"]["candidate"]["proposed_correction"] = "tampered"
        with self.assertRaises(IntegrityError):
            parse_correction_candidate_envelope(tampered)

    def test_claim_scope_exactly_matches_route_result_effective_scope(self) -> None:
        value = envelope()
        claim_scope = value.submission.candidate.detected_claims[0].scope_dimensions
        lineage_scope = value.submission.lineage.effective_scope
        self.assertEqual(claim_scope, lineage_scope)
        verify_correction_candidate_envelope(value)

    def test_claim_scope_widening_and_semantic_mismatch_fail_closed(self) -> None:
        valid = envelope()
        claim = valid.submission.candidate.detected_claims[0]
        widened_scope = claim.scope_dimensions + (
            ScopeDimension(
                name="jurisdiction",
                value="de",
                value_type=ScopeValueType.STRING,
                comparison_mode=ScopeComparisonMode.EXACT,
                source="route-result",
                required=True,
            ),
        )
        changed_source_scope = (
            replace(claim.scope_dimensions[0], source="candidate"),
        )
        for changed_scope in (widened_scope, changed_source_scope):
            with self.subTest(changed_scope=changed_scope), self.assertRaisesRegex(
                ContractValidationError,
                "candidate claim scope must exactly match lineage effective_scope",
            ):
                build_correction_candidate_envelope(
                    candidate=replace(
                        valid.submission.candidate,
                        detected_claims=(
                            replace(claim, scope_dimensions=changed_scope),
                        ),
                    ),
                    kernel_run=valid.submission.run_identity,
                    slot=personal_slot(),
                    route_result_lineage=valid.submission.lineage,
                    metadata=valid.submission.metadata,
                    idempotency_key="scope-mismatch",
                    submitted_at=valid.submission.submitted_at,
                )

    def test_internally_rehashed_claim_scope_tamper_fails_lineage_binding(self) -> None:
        valid = envelope()
        serialized = correction_candidate_envelope_to_jsonb(valid)
        claim = valid.submission.candidate.detected_claims[0]
        tampered_candidate = replace(
            valid.submission.candidate,
            detected_claims=(
                replace(
                    claim,
                    scope_dimensions=(
                        replace(claim.scope_dimensions[0], value="unrelated-domain"),
                    ),
                ),
            ),
        )
        serialized["submission"]["candidate"] = to_canonical_data(
            tampered_candidate
        )
        with self.assertRaisesRegex(
            ContractValidationError,
            "candidate claim scope must exactly match lineage effective_scope",
        ):
            parse_correction_candidate_envelope(serialized)

    def test_repository_row_roundtrip_binds_origin_state_and_hash(self) -> None:
        value = envelope()
        row = {
            "proposal_id": value.candidate_id,
            "origin": ActorType.KNOWLEDGE_KERNEL.value,
            "proposed_content": correction_candidate_envelope_to_jsonb(value),
            "lifecycle_state": "DETECTED",
            "content_hash": value.envelope_hash,
            "step29_candidate_hash": value.submission.candidate.content_hash,
            "step29_target_binding_hash": (
                value.submission.target_slot_binding.target_binding_hash
            ),
        }
        self.assertEqual(candidate_envelope_from_row(row), value)
        with self.assertRaises(IntegrityError):
            candidate_envelope_from_row({**row, "lifecycle_state": "PROPOSED"})

    def test_policy_is_hard_candidate_only_authority_ceiling(self) -> None:
        policy = load_correction_candidate_intake_policy()
        self.assertEqual(
            CORRECTION_CANDIDATE_SOURCE_ALLOWLIST,
            frozenset(
                {ActorType.KNOWLEDGE_KERNEL, ActorType.CRITIC_PROMPT_LOOP}
            ),
        )
        self.assertEqual(policy.maximum_state, CorrectionCandidateState.DETECTED)
        self.assertEqual(policy.maximum_candidates_per_slot, MAXIMUM_CANDIDATES_PER_SLOT)
        self.assertFalse(policy.grants_patch_proposal_transition)
        self.assertFalse(policy.grants_approval)
        self.assertFalse(policy.grants_commit)
        self.assertFalse(policy.grants_activation)
        self.assertFalse(policy.grants_memory_write)
        self.assertFalse(policy.grants_retrieval)
        self.assertFalse(policy.grants_canonical_evidence)

    def test_kernel_and_critic_sources_are_typed_and_hub_is_rejected(self) -> None:
        self.assertEqual(
            envelope(source=ActorType.KNOWLEDGE_KERNEL).submission.candidate.source_component,
            ActorType.KNOWLEDGE_KERNEL,
        )
        self.assertEqual(
            envelope(source=ActorType.CRITIC_PROMPT_LOOP).submission.candidate.source_component,
            ActorType.CRITIC_PROMPT_LOOP,
        )
        with self.assertRaises(ContractValidationError):
            envelope(source=ActorType.KNOWLEDGE_HUB)
        with self.assertRaises(ContractValidationError):
            envelope(source=ActorType.SYSTEM)

    def test_trigger_source_mismatch_and_oversized_text_fail_closed(self) -> None:
        with self.assertRaises(ContractValidationError):
            envelope(
                source=ActorType.KNOWLEDGE_KERNEL,
                trigger=CorrectionCandidateTrigger.CRITIC_PROMPT_LOOP_DETECTED,
            )
        maximum = load_correction_candidate_intake_policy().maximum_correction_utf8_bytes
        with self.assertRaises(ContractValidationError):
            envelope(proposed_correction="x" * (maximum + 1))
        valid = envelope()
        excessive_scope = tuple(
            ScopeDimension(
                name=f"dimension-{index:02d}",
                value="bounded",
                value_type=ScopeValueType.STRING,
                comparison_mode=ScopeComparisonMode.EXACT,
                source="route-result",
                required=True,
            )
            for index in range(65)
        )
        with self.assertRaises(ContractValidationError):
            replace(valid.submission.lineage, effective_scope=excessive_scope)

    def test_only_configured_and_active_slot_snapshots_build(self) -> None:
        for state in (
            PersonalMemorySpaceState.CONFIGURED,
            PersonalMemorySpaceState.ACTIVE,
        ):
            self.assertEqual(envelope(slot=personal_slot(state=state)).submission.target_slot_binding.slot_state, state)
        valid = envelope()
        for state in (
            PersonalMemorySpaceState.SUSPENDED,
            PersonalMemorySpaceState.ARCHIVED,
            PersonalMemorySpaceState.DELETED_PENDING,
            PersonalMemorySpaceState.DELETED,
        ):
            with self.subTest(state=state), self.assertRaises(ContractValidationError):
                build_correction_candidate_envelope(
                    candidate=valid.submission.candidate,
                    kernel_run=valid.submission.run_identity,
                    slot=personal_slot(state=state),
                    route_result_lineage=valid.submission.lineage,
                    metadata=valid.submission.metadata,
                    idempotency_key=f"ineligible-{state.value.lower()}",
                    submitted_at=valid.submission.submitted_at,
                )

    def test_owner_tenant_binding_and_route_tampering_fail_closed(self) -> None:
        valid = envelope()
        with self.assertRaises((ContractValidationError, OwnershipViolation)):
            envelope(owner_user_id="user-b")
        with self.assertRaises((ContractValidationError, OwnershipViolation)):
            envelope(tenant_id="tenant-b")
        with self.assertRaises(ContractValidationError):
            build_correction_candidate_envelope(
                candidate=replace(
                    valid.submission.candidate,
                    model_binding_id="personal-model-binding-" + ("0" * 64),
                ),
                kernel_run=valid.submission.run_identity,
                slot=personal_slot(),
                route_result_lineage=valid.submission.lineage,
                metadata=valid.submission.metadata,
                idempotency_key="binding-tamper",
                submitted_at=valid.submission.submitted_at,
            )
        with self.assertRaises(ContractValidationError):
            build_correction_candidate_envelope(
                candidate=valid.submission.candidate,
                kernel_run=valid.submission.run_identity,
                slot=personal_slot(),
                route_result_lineage=replace(
                    valid.submission.lineage, request_id="different-run"
                ),
                metadata=valid.submission.metadata,
                idempotency_key="route-tamper",
                submitted_at=valid.submission.submitted_at,
            )


class CorrectionCandidateServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.harness = ServiceHarness()

    def tearDown(self) -> None:
        self.harness.close()

    def test_kernel_and_critic_bridges_accept_exact_sources(self) -> None:
        kernel, kernel_receipt = self.harness.service.submit_kernel_candidate(
            envelope()
        )
        critic, critic_receipt = self.harness.service.submit_critic_loop_candidate(
            envelope(
                source=ActorType.CRITIC_PROMPT_LOOP,
                idempotency_key="critic-command",
                event_id="critic-event",
            )
        )
        self.assertEqual(kernel_receipt.disposition, CorrectionCandidateIntakeDisposition.ACCEPTED)
        self.assertEqual(critic_receipt.disposition, CorrectionCandidateIntakeDisposition.ACCEPTED)
        self.assertEqual(kernel.submission.candidate.state, CorrectionCandidateState.DETECTED)
        self.assertEqual(critic.submission.candidate.state, CorrectionCandidateState.DETECTED)
        self.assertEqual(len(self.harness.candidate_repository.scope_projections), 2)

    def test_producer_entry_points_reject_the_other_source(self) -> None:
        with self.assertRaises(CorrectionCandidateIntakeError) as kernel_error:
            self.harness.service.submit_kernel_candidate(
                envelope(source=ActorType.CRITIC_PROMPT_LOOP)
            )
        self.assertEqual(kernel_error.exception.reason_code, CorrectionCandidateReasonCode.SOURCE_NOT_ALLOWED)
        with self.assertRaises(CorrectionCandidateIntakeError):
            self.harness.service.submit_critic_loop_candidate(envelope())

    def test_exact_replay_and_distinct_idempotency_exact_duplicate(self) -> None:
        original = envelope()
        stored, accepted = self.harness.service.submit_kernel_candidate(original)
        replayed, replay_receipt = self.harness.service.submit_kernel_candidate(original)
        duplicate_input = envelope(
            idempotency_key="candidate-command-b",
            event_id="candidate-event-b",
        )
        duplicate, duplicate_receipt = self.harness.service.submit_kernel_candidate(
            duplicate_input
        )
        duplicate_replay, duplicate_replay_receipt = (
            self.harness.service.submit_kernel_candidate(duplicate_input)
        )
        self.assertEqual(accepted.disposition, CorrectionCandidateIntakeDisposition.ACCEPTED)
        self.assertEqual(replay_receipt.disposition, CorrectionCandidateIntakeDisposition.EXACT_REPLAY)
        self.assertEqual(duplicate_receipt.disposition, CorrectionCandidateIntakeDisposition.DUPLICATE)
        self.assertEqual(
            duplicate_replay_receipt.disposition,
            CorrectionCandidateIntakeDisposition.EXACT_REPLAY,
        )
        self.assertEqual(stored.candidate_id, replayed.candidate_id)
        self.assertEqual(stored.candidate_id, duplicate.candidate_id)
        self.assertEqual(stored.candidate_id, duplicate_replay.candidate_id)
        self.assertEqual(
            duplicate_receipt.submission_id,
            duplicate_input.submission.submission_id,
        )
        self.assertEqual(
            duplicate_receipt.submission_hash,
            duplicate_input.submission.submission_hash,
        )
        self.assertEqual(duplicate_receipt.envelope_id, duplicate_input.envelope_id)
        self.assertEqual(
            duplicate_receipt.envelope_hash,
            duplicate_input.envelope_hash,
        )
        self.assertEqual(
            duplicate_receipt.candidate_event_id,
            duplicate_input.submission.candidate.event_id,
        )
        self.assertEqual(
            duplicate_receipt.idempotency_key,
            duplicate_input.submission.idempotency_key,
        )
        self.assertEqual(
            duplicate_replay_receipt.submission_hash,
            duplicate_input.submission.submission_hash,
        )
        self.assertEqual(
            duplicate_replay_receipt.envelope_hash,
            duplicate_input.envelope_hash,
        )
        self.assertNotEqual(
            duplicate_receipt.submission_id,
            stored.submission.submission_id,
        )
        self.assertEqual(len(self.harness.candidate_repository.candidates), 1)

    def test_completed_exact_replay_does_not_depend_on_mutable_slot_state(self) -> None:
        original = envelope(slot=self.harness.slot)
        stored, _ = self.harness.service.submit_kernel_candidate(original)
        self.harness.slot_repository.slots[("tenant-a", "user-a", "slot-a")] = (
            personal_slot(
                state=PersonalMemorySpaceState.ARCHIVED,
                state_version=2,
                configuration_version=3,
                updated_at=NOW + timedelta(seconds=3),
            )
        )

        replayed, receipt = self.harness.service.submit_kernel_candidate(original)

        self.assertEqual(replayed.envelope_hash, stored.envelope_hash)
        self.assertEqual(
            receipt.disposition,
            CorrectionCandidateIntakeDisposition.EXACT_REPLAY,
        )
        self.assertEqual(receipt.envelope_hash, stored.envelope_hash)
        self.assertEqual(len(self.harness.candidate_repository.candidates), 1)

    def test_rehashed_in_memory_scope_tamper_is_rejected_before_service_write(self) -> None:
        value = envelope(slot=self.harness.slot)
        claim = value.submission.candidate.detected_claims[0]
        tampered_candidate = replace(
            value.submission.candidate,
            detected_claims=(
                replace(
                    claim,
                    scope_dimensions=(
                        replace(claim.scope_dimensions[0], value="unrelated-domain"),
                    ),
                ),
            ),
        )
        submission = value.submission
        object.__setattr__(submission, "candidate", tampered_candidate)
        deduplication_key = correction_candidate_semantic_deduplication_key(
            tampered_candidate,
            submission.run_identity,
            submission.target_slot_binding,
            submission.lineage,
        )
        object.__setattr__(
            submission,
            "semantic_deduplication_key",
            deduplication_key,
        )
        object.__setattr__(
            submission,
            "submission_hash",
            canonical_sha256(submission, exclude_fields=("submission_hash",)),
        )
        candidate_id = "correction-candidate-" + deduplication_key.rsplit("-", 1)[1]
        object.__setattr__(value, "candidate_id", candidate_id)
        identity = canonical_sha256(
            {
                "contract_type": value.contract_type,
                "contract_version": value.schema_version,
                "candidate_id": candidate_id,
                "submission_id": submission.submission_id,
                "submission_hash": submission.submission_hash,
            }
        )
        object.__setattr__(
            value,
            "envelope_id",
            "correction-candidate-envelope-" + identity,
        )
        object.__setattr__(
            value,
            "envelope_hash",
            canonical_sha256(value, exclude_fields=("envelope_hash",)),
        )

        with self.assertRaises(IntegrityError):
            verify_correction_candidate_envelope(value)
        with self.assertRaises(IntegrityError):
            self.harness.service.submit_kernel_candidate(value)
        self.assertEqual(self.harness.candidate_repository.candidates, {})

    def test_conflicting_idempotency_replay_is_rejected(self) -> None:
        self.harness.service.submit_kernel_candidate(envelope())
        with self.assertRaises(IdempotencyConflictError):
            self.harness.service.submit_kernel_candidate(
                envelope(proposed_correction="Different correction content.")
            )

    def test_hard_candidate_count_quota_fails_closed(self) -> None:
        self.harness.candidate_repository.usage_override = (
            load_correction_candidate_intake_policy().maximum_candidates_per_slot,
            0,
        )
        with self.assertRaises(CorrectionCandidateIntakeError) as error:
            self.harness.service.submit_kernel_candidate(envelope())
        self.assertEqual(error.exception.reason_code, CorrectionCandidateReasonCode.CANDIDATE_QUOTA_EXCEEDED)
        self.assertEqual(self.harness.candidate_repository.candidates, {})

    def test_stale_archived_and_delete_pending_targets_fail_closed(self) -> None:
        valid = envelope(slot=self.harness.slot)
        stale = personal_slot(
            state=PersonalMemorySpaceState.ACTIVE,
            state_version=2,
            configuration_version=3,
            updated_at=NOW + timedelta(seconds=3),
        )
        self.harness.slot_repository.slots[("tenant-a", "user-a", "slot-a")] = stale
        with self.assertRaises(CorrectionCandidateIntakeError) as stale_error:
            self.harness.service.submit_kernel_candidate(valid)
        self.assertEqual(stale_error.exception.reason_code, CorrectionCandidateReasonCode.TARGET_INELIGIBLE)

        for state, reason in (
            (PersonalMemorySpaceState.ARCHIVED, CorrectionCandidateReasonCode.TARGET_ARCHIVED),
            (PersonalMemorySpaceState.DELETED_PENDING, CorrectionCandidateReasonCode.TARGET_DELETE_PENDING),
        ):
            current = personal_slot(
                state=state,
                state_version=2,
                configuration_version=3,
                updated_at=NOW + timedelta(seconds=3),
            )
            self.harness.slot_repository.slots[("tenant-a", "user-a", "slot-a")] = current
            with self.subTest(state=state), self.assertRaises(CorrectionCandidateIntakeError) as rejected:
                self.harness.service.submit_kernel_candidate(
                    envelope(idempotency_key=f"state-{state.value.lower()}")
                )
            self.assertEqual(rejected.exception.reason_code, reason)

    def test_unknown_owner_tenant_and_owner_scoped_reads(self) -> None:
        accepted, _ = self.harness.service.submit_kernel_candidate(envelope())
        self.assertIsNotNone(
            self.harness.service.get_candidate(
                tenant_id="tenant-a",
                owner_user_id="user-a",
                candidate_id=accepted.candidate_id,
            )
        )
        self.assertIsNone(
            self.harness.service.get_candidate(
                tenant_id="tenant-a",
                owner_user_id="user-b",
                candidate_id=accepted.candidate_id,
            )
        )
        self.assertIsNone(
            self.harness.service.get_candidate(
                tenant_id="tenant-b",
                owner_user_id="user-a",
                candidate_id=accepted.candidate_id,
            )
        )
        self.assertEqual(
            self.harness.service.list_owner_candidates(
                tenant_id="tenant-a",
                owner_user_id="user-b",
                personal_memory_space_id="slot-a",
            ),
            (),
        )


class CorrectionCandidatePersistenceBoundaryTests(unittest.TestCase):
    def test_repository_is_append_read_only_and_sql_is_candidate_scoped(self) -> None:
        public = {
            name
            for name, value in inspect.getmembers(
                CorrectionCandidateCockroachRepository, inspect.isfunction
            )
            if not name.startswith("_")
        }
        self.assertEqual(
            public,
            {
                "candidate_usage",
                "ensure_target_hat_scope",
                "get_candidate",
                "insert_candidate",
                "list_owner_candidates",
            },
        )
        source = inspect.getsource(CorrectionCandidateCockroachRepository)
        self.assertIn("lifecycle_state = 'DETECTED'", source)
        self.assertIn("ON CONFLICT DO NOTHING", source)
        self.assertIn("'KNOWLEDGE_KERNEL', 'CRITIC_PROMPT_LOOP'", source)
        self.assertNotIn("UPDATE memory_patch.memory_patch_proposals", source)
        self.assertNotIn("DELETE FROM memory_patch.memory_patch_proposals", source)

    def test_migration_0012_has_rls_force_owner_insert_and_no_mutation_grant(self) -> None:
        migration = (
            ROOT / "sql/cockroachdb/migrations/0012_step28_correction_candidate_bridge.sql"
        ).read_text(encoding="utf-8")
        for fragment in (
            "memory_patch_proposals_s28_insert",
            "hat_scopes_s28_personal_insert",
            "ENABLE ROW LEVEL SECURITY",
            "FORCE ROW LEVEL SECURITY",
            "lifecycle_state = 'DETECTED'",
            "origin IN ('KNOWLEDGE_KERNEL', 'CRITIC_PROMPT_LOOP')",
            "space.state IN ('CONFIGURED', 'ACTIVE')",
            "GRANT INSERT ON TABLE",
            "REVOKE UPDATE, DELETE ON TABLE",
            "step28_personal_hat_scope_id",
            "hat_scope_id IS NOT NULL",
            "slot_hash IS NOT NULL",
            "{\"owner_user_id\":\"",
            "\",\"personal_memory_space_id\":\"",
            "\",\"target_scope\":\"USER_PERSONAL_HAT\",\"tenant_id\":\"",
            "step28_candidate_target_matches",
            "space.hat_scope_id = p_hat_scope_id",
            "space.slot_hash = p_slot_hash",
            "candidate_quota_epoch",
            "BEFORE INSERT ON memory_patch.memory_patch_proposals",
            "HAVING count(*) >= 128",
            "octet_length((NEW).proposed_content::STRING) > 8388608",
            "> 8388608 - octet_length((NEW).proposed_content::STRING)",
        ):
            self.assertIn(fragment, migration)
        self.assertNotRegex(
            migration,
            r"GRANT\s+(?:UPDATE|DELETE).*TO\s+mp_app_runtime",
        )

    def test_manifest_and_security_manifest_pin_step28_boundary(self) -> None:
        manifest = json.loads(
            (ROOT / "sql/cockroachdb/migrations/manifest.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(manifest["schema_version"], 16)
        self.assertEqual(manifest["runner_version"], "16.0.0")
        self.assertEqual(len(manifest["migrations"]), 18)
        latest = manifest["migrations"][11]
        self.assertEqual(latest["migration_id"], "0012_step28_correction_candidate_bridge")
        migration_bytes = (
            ROOT / "sql/cockroachdb/migrations" / latest["filename"]
        ).read_bytes()
        self.assertEqual(latest["sha256"], hashlib.sha256(migration_bytes).hexdigest())

        security = json.loads(
            (ROOT / "config/cockroachdb/correction-candidate-security-1a.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            security["allowed_candidate_origins"],
            ["CRITIC_PROMPT_LOOP", "KNOWLEDGE_KERNEL"],
        )
        self.assertEqual(security["candidate_lifecycle_state"], "DETECTED")
        self.assertFalse(security["runtime_update_delete"])
        self.assertEqual(
            {value["table"] for value in security["tables"]},
            {"hat_scopes", "memory_patch_proposals"},
        )
        self.assertTrue(all(value["rls_enabled"] for value in security["tables"]))
        self.assertTrue(all(value["force_rls"] for value in security["tables"]))
        self.assertEqual(security["quota_guard"]["count_limit"], 128)
        self.assertEqual(security["quota_guard"]["byte_limit"], 8 * 1024 * 1024)
        self.assertEqual(
            security["slot_authority"]["authority_columns"],
            ["hat_scope_id", "slot_hash"],
        )
        self.assertEqual(
            security["slot_authority"]["scope_derivation_function"],
            "step28_personal_hat_scope_id",
        )

    def test_database_quota_uses_cumulative_carrier_rows_and_real_live_probe(self) -> None:
        repository_source = inspect.getsource(
            CorrectionCandidateCockroachRepository.candidate_usage
        )
        self.assertNotIn("lifecycle_state", repository_source)
        migration = (
            ROOT / "sql/cockroachdb/migrations/0012_step28_correction_candidate_bridge.sql"
        ).read_text(encoding="utf-8")
        quota_aggregate = migration.split(
            "CREATE OR REPLACE FUNCTION memory_patch.enforce_step28_candidate_quota()",
            1,
        )[1].split(
            "CREATE OR REPLACE FUNCTION memory_patch.guard_step28_slot_authority_update()",
            1,
        )[0]
        self.assertNotIn("candidate.lifecycle_state", quota_aggregate)
        live_source = (
            ROOT / "scripts/run_step28_correction_candidate_bridge_validation.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("_AtLimitCandidateRepository", live_source)
        self.assertNotIn("BOUNDED_USAGE_PROJECTION", live_source)
        self.assertIn("STEP28_REAL_DATABASE_QUOTA_REJECTION", live_source)
        self.assertIn("STEP28_REAL_DATABASE_BYTE_QUOTA_REJECTION", live_source)
        self.assertIn("jsonb_set(", live_source)
        self.assertIn("to_jsonb(repeat('x', %s))", live_source)
        self.assertNotIn(
            '"database_quota_padding": "x"',
            live_source,
        )
        self.assertIn('{"23514", "42501", "44000"}', live_source)
        self.assertIn("STEP28_SCOPE_DERIVATION_PARITY_MISMATCH", live_source)
        self.assertIn("maximum_logical_id_bytes_exercised", live_source)
        self.assertIn("REAL_DATABASE_BEFORE_INSERT_TRIGGER", live_source)


class CorrectionCandidateAuthorityAndClosureTests(unittest.TestCase):
    def test_bridge_has_no_hub_approval_commit_activation_or_patch_api(self) -> None:
        public = {
            name
            for name, value in inspect.getmembers(
                CorrectionCandidateBridgeService, inspect.isfunction
            )
            if not name.startswith("_")
        }
        self.assertEqual(
            public,
            {
                "get_candidate",
                "list_owner_candidates",
                "submit_critic_loop_candidate",
                "submit_kernel_candidate",
            },
        )
        for forbidden in (
            "approve",
            "commit",
            "activate",
            "propose_patch",
            "validate_evidence",
            "retrieve_active_patch",
            "submit_knowledge_hub_candidate",
        ):
            self.assertNotIn(forbidden, public)

    def test_candidate_contract_contains_no_later_state_or_authority_field(self) -> None:
        value = envelope()
        payload = correction_candidate_envelope_to_jsonb(value)
        serialized = json.dumps(payload, sort_keys=True)
        self.assertEqual(value.submission.candidate.state, CorrectionCandidateState.DETECTED)
        for forbidden_field in (
            '"approval"',
            '"commit_receipt"',
            '"activation"',
            '"active_patch_id"',
            '"canonical_evidence_authority"',
        ):
            self.assertNotIn(forbidden_field, serialized)
        for later_state in ("EVIDENCE_BOUND", "VALIDATED", "AWAITING_APPROVAL"):
            self.assertNotIn(f'"state": "{later_state}"', serialized)

    def test_candidate_code_has_no_provider_tool_or_external_agent_runtime(self) -> None:
        combined = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (
                ROOT / "src/aioa_memory_kernel/personal_memory/candidates.py",
                ROOT / "src/aioa_memory_kernel/personal_memory/candidate_repository.py",
                ROOT / "src/aioa_memory_kernel/personal_memory/candidate_service.py",
            )
        )
        for forbidden in (
            "subprocess",
            "os.system",
            "shell=True",
            "execute_action(",
            "activate_patch(",
            "OpenShell",
            "NOOA",
            "NVIDIA",
        ):
            self.assertNotIn(forbidden, combined)

    def test_step28_documents_evidence_and_live_roadmap_close_only_this_step(self) -> None:
        expected_docs = (
            ROOT / "docs/architecture/KNOWLEDGE_HUB_CRITIC_CORRECTION_CANDIDATE_BRIDGE_1A.md",
            ROOT / "docs/adr/ADR-035-correction-candidate-bridge.md",
            ROOT / "docs/operations/STEP_28_CORRECTION_CANDIDATE_BRIDGE_VALIDATION_1A.md",
            ROOT / "docs/audits/STEP_28_KNOWLEDGE_HUB_CRITIC_CANDIDATE_BRIDGE_CLOSURE_1A.md",
        )
        self.assertTrue(all(path.is_file() for path in expected_docs))
        evidence_path = (
            ROOT
            / "docs/evidence/personal-memory/step28-correction-candidate-bridge-validation.json"
        )
        self.assertTrue(evidence_path.is_file())
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        self.assertEqual(evidence["step"], 28)
        self.assertEqual(evidence["start_sha"], BASE_SHA)
        self.assertEqual(evidence["candidate_state"], "DETECTED")
        self.assertFalse(evidence["step29_started"])
        self.assertEqual(evidence["status"], "PASS")
        self.assertEqual(
            evidence["database"]["rls"]["tables"],
            ["hat_scopes", "memory_patch_proposals", "personal_memory_spaces"],
        )
        self.assertEqual(evidence["database"]["scope_derivation"]["status"], "PASS")
        self.assertEqual(evidence["dedup_matrix"]["persisted_candidate_count"], 128)
        self.assertEqual(
            evidence["quota"]["fixture_mode"],
            "REAL_DATABASE_BEFORE_INSERT_TRIGGER",
        )
        self.assertEqual(evidence["quota"]["byte_limit_result"], "REJECTED")
        self.assertEqual(
            evidence["quota"]["concurrent_boundary"],
            "ONE_ACCEPTED_ONE_REJECTED",
        )
        self.assertEqual(evidence["quota"]["database_candidate_count"], 128)
        self.assertEqual(evidence["quota"]["database_quota_epoch"], 128)
        claimed_digest = evidence.pop("validation_digest")
        self.assertEqual(claimed_digest, canonical_sha256(evidence))
        authority = evidence["authority"]
        for name in (
            "model_authority",
            "critic_approval_authority",
            "kernel_approval_authority",
            "commit_authority",
            "activation_authority",
            "execution_authority",
        ):
            self.assertFalse(authority[name])

        roadmap = (ROOT / "docs/roadmap/PRODUCTION_ROADMAP.md").read_text(
            encoding="utf-8"
        )
        self.assertRegex(roadmap, r"(?m)^- \[x\] \*\*Step 28 — ")
        self.assertIn("`Step 28: COMPLETE AND PUSHED at actual closure commit`", roadmap)
        self.assertRegex(roadmap, r"(?m)^- \[x\] \*\*Step 29 — ")
        self.assertRegex(roadmap, r"(?m)^- \[x\] \*\*Step 30 — ")
        self.assertRegex(roadmap, r"(?m)^- \[x\] \*\*Step 31 — ")
        self.assertRegex(roadmap, r"(?m)^- \[x\] \*\*Step 32 — ")
        self.assertRegex(roadmap, r"(?m)^- \[x\] \*\*Step 33 — ")
        self.assertRegex(roadmap, r"(?m)^- \[x\] \*\*Step 34 — ")
        self.assertRegex(roadmap, r"(?m)^- \[x\] \*\*Step 35 — ")
        self.assertRegex(roadmap, r"(?m)^- \[x\] \*\*Step 36 — ")
        self.assertRegex(roadmap, r"(?m)^- \[x\] \*\*Step 37 — ")
        self.assertRegex(roadmap, r"(?m)^- \[x\] \*\*Step 38 — ")
        self.assertRegex(roadmap, r"(?m)^- \[x\] \*\*Step 39 — ")
        self.assertRegex(roadmap, r"(?m)^- \[ \] \*\*Step 40 — ")
        self.assertIn("Step 39: COMPLETE AND PUSHED", roadmap)
        self.assertIn("Step 40: NOT STARTED", roadmap)
        self.assertIn("Step 39 completion does not authorize Step 40.", roadmap)
        agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        self.assertNotIn("`Step 28: NOT STARTED`", agents)
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
        self.assertIn("Step 39: COMPLETE AND PUSHED", agents)
        self.assertIn("Step 40: NOT STARTED", agents)
        self.assertIn("Step 39 completion does not authorize Step 40.", agents)


if __name__ == "__main__":
    unittest.main()
