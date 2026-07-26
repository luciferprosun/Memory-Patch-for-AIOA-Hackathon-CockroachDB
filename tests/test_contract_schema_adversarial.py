"""Schema-version, serialization, and deserialization authority tests."""

from __future__ import annotations

import importlib.util
import json
import unittest
from dataclasses import fields, replace
from datetime import datetime

from tests._support import (
    NOW,
    REPOSITORY_ROOT,
    SPACE_A,
    TENANT_A,
    USER_A,
    make_approval,
    make_claim,
    make_commit,
    make_manifest,
    make_packet,
    make_personal_proposal,
    make_pool,
    make_scope,
    make_shared_promotion,
    ownership_a,
)
from aioa_memory_kernel.contracts import (
    CONTRACT_SCHEMA_VERSION,
    ActorType,
    ApprovalDecision,
    ApprovalRequirement,
    ContractValidationError,
    CorrectionCandidate,
    CorrectionCandidateState,
    DeidentificationStatus,
    MemoryContentKind,
    MemoryItem,
    MemoryOwnership,
    MemoryPatchApproval,
    MemoryPatchCommit,
    MemoryPatchProposal,
    MemoryTargetScope,
    MemoryTrustClass,
    MemoryVisibility,
    PatchState,
    PersonalMemorySpace,
    PersonalMemorySpaceState,
    PrivateDataClassification,
    ProposalOrigin,
    SharedPromotionProposal,
    SharedPromotionState,
    ScopeComparisonMode,
    ScopeDimension,
    ScopeValueType,
    StorageClass,
    build_audit_event,
    canonical_json,
    to_canonical_data,
    verify_approval_binding,
    verify_commit_binding,
)
from aioa_memory_kernel.state_machines import (
    activate_personal_memory_space,
    allocate_personal_memory_space,
    configure_personal_memory_space,
)


VALIDATOR_PATH = REPOSITORY_ROOT / "scripts" / "validate_contracts.py"
VALIDATOR_SPEC = importlib.util.spec_from_file_location(
    "contract_validator_for_tests",
    VALIDATOR_PATH,
)
assert VALIDATOR_SPEC is not None and VALIDATOR_SPEC.loader is not None
validator = importlib.util.module_from_spec(VALIDATOR_SPEC)
VALIDATOR_SPEC.loader.exec_module(validator)


def _schema(name: str) -> dict[str, object]:
    path = REPOSITORY_ROOT / "schemas" / name
    return json.loads(path.read_text(encoding="utf-8"))


def _validate(instance: object, schema_name: str) -> None:
    schema = _schema(schema_name)
    validator.validate_instance(
        instance,
        schema,
        root_schema=schema,
        location=f"tests.{schema_name}",
    )


def _parse_timestamp(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _restore_scope(raw: dict[str, object]) -> ScopeDimension:
    return ScopeDimension(
        name=raw["name"],  # type: ignore[arg-type]
        value=raw["value"],
        value_type=ScopeValueType(raw["value_type"]),
        comparison_mode=ScopeComparisonMode(raw["comparison_mode"]),
        source=raw["source"],  # type: ignore[arg-type]
        required=raw["required"],  # type: ignore[arg-type]
    )


def _restore_initial_proposal(raw: dict[str, object]) -> MemoryPatchProposal:
    return MemoryPatchProposal(
        schema_version=raw["schema_version"],  # type: ignore[arg-type]
        proposal_id=raw["proposal_id"],  # type: ignore[arg-type]
        tenant_id=raw["tenant_id"],  # type: ignore[arg-type]
        owner_user_id=raw["owner_user_id"],  # type: ignore[arg-type]
        target_scope=MemoryTargetScope(raw["target_scope"]),
        target_hat_id=raw["target_hat_id"],  # type: ignore[arg-type]
        target_personal_memory_space_id=raw[
            "target_personal_memory_space_id"
        ],  # type: ignore[arg-type]
        origin=ProposalOrigin(raw["origin"]),
        proposed_content=raw["proposed_content"],
        evidence_references=raw["evidence_references"],  # type: ignore[arg-type]
        scope_dimensions=tuple(
            _restore_scope(item)
            for item in raw["scope_dimensions"]  # type: ignore[union-attr]
        ),
        valid_from=_parse_timestamp(raw["valid_from"]),  # type: ignore[arg-type]
        valid_until=_parse_timestamp(raw["valid_until"]),  # type: ignore[arg-type]
        requested_trust_class=MemoryTrustClass(
            raw["requested_trust_class"]
        ),
        approval_requirement=ApprovalRequirement(raw["approval_requirement"]),
        lifecycle_state=PatchState(raw["lifecycle_state"]),
        content_kind=MemoryContentKind(raw["content_kind"]),
        created_at=_parse_timestamp(raw["created_at"]),  # type: ignore[arg-type]
    )


def _restore_personal_space(raw: dict[str, object]) -> PersonalMemorySpace:
    return PersonalMemorySpace(
        schema_version=raw["schema_version"],  # type: ignore[arg-type]
        personal_memory_space_id=raw[
            "personal_memory_space_id"
        ],  # type: ignore[arg-type]
        tenant_id=raw["tenant_id"],  # type: ignore[arg-type]
        user_id=raw["user_id"],  # type: ignore[arg-type]
        state=PersonalMemorySpaceState(raw["state"]),
        display_name=raw["display_name"],  # type: ignore[arg-type]
        created_at=_parse_timestamp(raw["created_at"]),  # type: ignore[arg-type]
        updated_at=_parse_timestamp(raw["updated_at"]),  # type: ignore[arg-type]
        model_binding_ids=tuple(raw["model_binding_ids"]),  # type: ignore[arg-type]
        export_requested_at=_parse_timestamp(raw["export_requested_at"]),
        deletion_requested_at=_parse_timestamp(
            raw["deletion_requested_at"]
        ),
        deleted_at=_parse_timestamp(raw["deleted_at"]),
    )


def _restore_shared_promotion(
    raw: dict[str, object],
) -> SharedPromotionProposal:
    return SharedPromotionProposal(
        schema_version=raw["schema_version"],  # type: ignore[arg-type]
        shared_promotion_proposal_id=raw[
            "shared_promotion_proposal_id"
        ],  # type: ignore[arg-type]
        originating_personal_patch_id=raw[
            "originating_personal_patch_id"
        ],  # type: ignore[arg-type]
        originating_personal_patch_hash=raw[
            "originating_personal_patch_hash"
        ],  # type: ignore[arg-type]
        originating_personal_memory_space_id=raw[
            "originating_personal_memory_space_id"
        ],  # type: ignore[arg-type]
        tenant_id=raw["tenant_id"],  # type: ignore[arg-type]
        owner_user_id=raw["owner_user_id"],  # type: ignore[arg-type]
        target_hat_id=raw["target_hat_id"],  # type: ignore[arg-type]
        private_data_classification=PrivateDataClassification(
            raw["private_data_classification"]
        ),
        deidentification_status=DeidentificationStatus(
            raw["deidentification_status"]
        ),
        independent_evidence_references=tuple(
            raw["independent_evidence_references"]  # type: ignore[arg-type]
        ),
        independent_evidence_validated=raw[
            "independent_evidence_validated"
        ],  # type: ignore[arg-type]
        hat_scope_dimensions=tuple(
            _restore_scope(item)
            for item in raw["hat_scope_dimensions"]  # type: ignore[union-attr]
        ),
        valid_from=_parse_timestamp(raw["valid_from"]),
        valid_until=_parse_timestamp(raw["valid_until"]),
        domain_approval_id=raw["domain_approval_id"],  # type: ignore[arg-type]
        shared_commit_id=raw["shared_commit_id"],  # type: ignore[arg-type]
        state=SharedPromotionState(raw["state"]),
        created_at=_parse_timestamp(raw["created_at"]),  # type: ignore[arg-type]
        updated_at=_parse_timestamp(raw["updated_at"]),  # type: ignore[arg-type]
    )


def _restore_memory_item(raw: dict[str, object]) -> MemoryItem:
    ownership_raw = raw["ownership"]
    ownership = (
        None
        if ownership_raw is None
        else MemoryOwnership(
            tenant_id=ownership_raw["tenant_id"],  # type: ignore[index]
            user_id=ownership_raw["user_id"],  # type: ignore[index]
            personal_memory_space_id=ownership_raw[
                "personal_memory_space_id"
            ],  # type: ignore[index]
        )
    )
    return MemoryItem(
        schema_version=raw["schema_version"],  # type: ignore[arg-type]
        memory_item_id=raw["memory_item_id"],  # type: ignore[arg-type]
        visibility=MemoryVisibility(raw["visibility"]),
        trust_class=MemoryTrustClass(raw["trust_class"]),
        content_kind=MemoryContentKind(raw["content_kind"]),
        content=raw["content"],
        scope_dimensions=tuple(
            _restore_scope(item)
            for item in raw["scope_dimensions"]  # type: ignore[union-attr]
        ),
        evidence_references=tuple(
            raw["evidence_references"]  # type: ignore[arg-type]
        ),
        created_at=_parse_timestamp(raw["created_at"]),  # type: ignore[arg-type]
        ownership=ownership,
        source_patch_id=raw["source_patch_id"],  # type: ignore[arg-type]
        valid_from=_parse_timestamp(raw["valid_from"]),
        valid_until=_parse_timestamp(raw["valid_until"]),
        expires_at=_parse_timestamp(raw["expires_at"]),
        active=raw["active"],  # type: ignore[arg-type]
        revoked=raw["revoked"],  # type: ignore[arg-type]
    )


class SchemaRuntimeParityTests(unittest.TestCase):
    def test_authority_records_require_explicit_schema_version(self) -> None:
        awaiting = make_personal_proposal(
            state=PatchState.AWAITING_APPROVAL
        )
        approval = make_approval(awaiting)
        approved = make_personal_proposal(state=PatchState.APPROVED)
        commit_approval = make_approval(approved)
        records = (
            approval,
            make_commit(approved, commit_approval),
            make_shared_promotion(),
            MemoryItem(
                schema_version=CONTRACT_SCHEMA_VERSION,
                memory_item_id="schema-versioned-memory",
                visibility=MemoryVisibility.PERSONAL,
                trust_class=MemoryTrustClass.USER_ASSERTED_MEMORY,
                content_kind=MemoryContentKind.FACTUAL,
                content={"statement": "Inert user assertion."},
                scope_dimensions=(),
                evidence_references=(),
                created_at=NOW,
                ownership=ownership_a(),
            ),
        )
        for record in records:
            with self.subTest(contract=type(record).__name__):
                self.assertIn(
                    "schema_version",
                    {field.name for field in fields(record)},
                )
                self.assertEqual(
                    record.schema_version,
                    CONTRACT_SCHEMA_VERSION,
                )
                with self.assertRaises(ContractValidationError):
                    replace(record, schema_version="2.0.0")

        self.assertIsInstance(records[2], SharedPromotionProposal)

    def test_all_schema_backed_runtime_records_validate(self) -> None:
        pool, space = allocate_personal_memory_space(
            make_pool(),
            personal_memory_space_id=SPACE_A,
            created_at=NOW,
            tenant_id=TENANT_A,
            user_id=USER_A,
        )
        pool = configure_personal_memory_space(
            pool,
            personal_memory_space_id=SPACE_A,
            display_name="Synthetic private memory",
            changed_at=NOW,
            tenant_id=TENANT_A,
            user_id=USER_A,
        )
        pool = activate_personal_memory_space(
            pool,
            personal_memory_space_id=SPACE_A,
            changed_at=NOW,
            tenant_id=TENANT_A,
            user_id=USER_A,
        )
        active_space = pool.spaces[0]
        audit = build_audit_event(
            audit_event_id="audit-runtime-1",
            tenant_id=TENANT_A,
            user_id=USER_A,
            kernel_run_id="run-runtime-1",
            event_type="PATCH_PROPOSED",
            sequence_number=0,
            previous_event=None,
            resource_type="MemoryPatchProposal",
            resource_id="proposal-personal-1",
            state_before=None,
            state_after="PROPOSED",
            actor_type=ActorType.USER,
            actor_id=USER_A,
            content_hashes={"proposal": "a" * 64},
            created_at=NOW,
            personal_memory_space_id=SPACE_A,
        )
        cases = (
            (
                make_manifest(),
                "hat-manifest.schema.json",
            ),
            (
                active_space,
                "personal-memory-space.schema.json",
            ),
            (
                make_personal_proposal(),
                "memory-patch-proposal.schema.json",
            ),
            (
                make_packet(),
                "correction-packet.schema.json",
            ),
            (
                audit,
                "audit-event.schema.json",
            ),
        )
        for contract, schema_name in cases:
            with self.subTest(schema=schema_name):
                data = to_canonical_data(contract)
                self.assertEqual(
                    data["schema_version"],  # type: ignore[index]
                    CONTRACT_SCHEMA_VERSION,
                )
                _validate(data, schema_name)
        self.assertEqual(space.schema_version, CONTRACT_SCHEMA_VERSION)

    def test_schema_and_runtime_top_level_fields_match_exactly(self) -> None:
        mappings = (
            ("hat-manifest.schema.json", make_manifest()),
            (
                "personal-memory-space.schema.json",
                allocate_personal_memory_space(
                    make_pool(),
                    personal_memory_space_id=SPACE_A,
                    created_at=NOW,
                    tenant_id=TENANT_A,
                    user_id=USER_A,
                )[1],
            ),
            ("memory-patch-proposal.schema.json", make_personal_proposal()),
            ("correction-packet.schema.json", make_packet()),
            (
                "audit-event.schema.json",
                build_audit_event(
                    audit_event_id="audit-fields-1",
                    tenant_id=TENANT_A,
                    user_id=None,
                    kernel_run_id=None,
                    event_type="SYNTHETIC",
                    sequence_number=0,
                    previous_event=None,
                    resource_type="Synthetic",
                    resource_id="resource-1",
                    state_before=None,
                    state_after=None,
                    actor_type=ActorType.SYSTEM,
                    actor_id="contract-validator",
                    content_hashes={"record": "a" * 64},
                    created_at=NOW,
                ),
            ),
        )
        for schema_name, contract in mappings:
            with self.subTest(schema=schema_name):
                self.assertEqual(
                    set(_schema(schema_name)["properties"]),  # type: ignore[arg-type]
                    set(to_canonical_data(contract)),
                )

    def test_missing_old_and_future_schema_versions_fail_closed(self) -> None:
        fixture_path = (
            REPOSITORY_ROOT
            / "tests"
            / "fixtures"
            / "synthetic_contract_fixtures.json"
        )
        fixtures = json.loads(fixture_path.read_text(encoding="utf-8"))
        cases = (
            ("hat-manifest.schema.json", fixtures["hat_manifests"][0]),
            (
                "personal-memory-space.schema.json",
                fixtures["personal_memory_space"],
            ),
            (
                "memory-patch-proposal.schema.json",
                fixtures["personal_patch"],
            ),
            (
                "correction-packet.schema.json",
                fixtures["correction_packet"],
            ),
            ("audit-event.schema.json", fixtures["audit_event"]),
        )
        for schema_name, valid in cases:
            for version in (None, "0.9.0", "2.0.0"):
                mutated = dict(valid)
                if version is None:
                    mutated.pop("schema_version")
                else:
                    mutated["schema_version"] = version
                with self.subTest(schema=schema_name, version=version):
                    with self.assertRaises(validator.ValidationFailure):
                        _validate(mutated, schema_name)

    def test_runtime_constructors_reject_unsupported_schema_versions(self) -> None:
        pool, empty_space = allocate_personal_memory_space(
            make_pool(),
            personal_memory_space_id=SPACE_A,
            created_at=NOW,
            tenant_id=TENANT_A,
            user_id=USER_A,
        )
        del pool
        audit = build_audit_event(
            audit_event_id="audit-version-1",
            tenant_id=TENANT_A,
            user_id=None,
            kernel_run_id=None,
            event_type="SYNTHETIC",
            sequence_number=0,
            previous_event=None,
            resource_type="Synthetic",
            resource_id="resource-1",
            state_before=None,
            state_after=None,
            actor_type=ActorType.SYSTEM,
            actor_id="validator",
            content_hashes={"record": "a" * 64},
            created_at=NOW,
        )
        for contract in (
            make_manifest(),
            empty_space,
            make_personal_proposal(),
            make_packet(),
            audit,
        ):
            with self.subTest(contract=type(contract).__name__):
                with self.assertRaises(ContractValidationError):
                    replace(contract, schema_version="2.0.0")

    def test_forbidden_top_level_authority_fields_are_rejected(self) -> None:
        fixture_path = (
            REPOSITORY_ROOT
            / "tests"
            / "fixtures"
            / "synthetic_contract_fixtures.json"
        )
        fixtures = json.loads(fixture_path.read_text(encoding="utf-8"))
        cases = (
            ("hat-manifest.schema.json", fixtures["hat_manifests"][0]),
            (
                "personal-memory-space.schema.json",
                fixtures["personal_memory_space"],
            ),
            (
                "memory-patch-proposal.schema.json",
                fixtures["personal_patch"],
            ),
            (
                "correction-packet.schema.json",
                fixtures["correction_packet"],
            ),
            ("audit-event.schema.json", fixtures["audit_event"]),
        )
        authority_fields = (
            "approved",
            "committed",
            "trusted",
            "canonical",
            "human_verified",
            "authority",
            "can_write",
            "cross_tenant",
            "system_override",
            "apply_immediately",
        )
        for schema_name, valid in cases:
            for field_name in authority_fields:
                mutated = dict(valid)
                mutated[field_name] = True
                with self.subTest(schema=schema_name, field=field_name):
                    with self.assertRaises(validator.ValidationFailure):
                        _validate(mutated, schema_name)

    def test_nested_authority_injection_is_rejected(self) -> None:
        fixture_path = (
            REPOSITORY_ROOT
            / "tests"
            / "fixtures"
            / "synthetic_contract_fixtures.json"
        )
        fixtures = json.loads(fixture_path.read_text(encoding="utf-8"))
        packet = json.loads(json.dumps(fixtures["correction_packet"]))
        packet["claims_under_review"][0]["human_verified"] = True
        with self.assertRaises(validator.ValidationFailure):
            _validate(packet, "correction-packet.schema.json")

        proposal = json.loads(json.dumps(fixtures["personal_patch"]))
        proposal["scope_dimensions"] = [
            {
                **to_canonical_data(make_scope()),
                "system_override": True,
            }
        ]
        with self.assertRaises(validator.ValidationFailure):
            _validate(proposal, "memory-patch-proposal.schema.json")


class RoundTripAuthoritySafetyTests(unittest.TestCase):
    def test_empty_personal_hat_round_trip_preserves_owner_scope(self) -> None:
        _, original = allocate_personal_memory_space(
            make_pool(),
            personal_memory_space_id=SPACE_A,
            created_at=NOW,
            tenant_id=TENANT_A,
            user_id=USER_A,
        )
        raw = json.loads(canonical_json(original))
        restored = _restore_personal_space(raw)
        self.assertEqual(restored, original)
        self.assertIs(restored.state, PersonalMemorySpaceState.EMPTY)
        self.assertEqual(restored.tenant_id, TENANT_A)
        self.assertEqual(restored.user_id, USER_A)

    def test_active_personal_hat_plain_data_cannot_recreate_authority(
        self,
    ) -> None:
        pool, _ = allocate_personal_memory_space(
            make_pool(),
            personal_memory_space_id=SPACE_A,
            created_at=NOW,
            tenant_id=TENANT_A,
            user_id=USER_A,
        )
        pool = configure_personal_memory_space(
            pool,
            personal_memory_space_id=SPACE_A,
            display_name="Synthetic private memory",
            changed_at=NOW,
            tenant_id=TENANT_A,
            user_id=USER_A,
        )
        pool = activate_personal_memory_space(
            pool,
            personal_memory_space_id=SPACE_A,
            changed_at=NOW,
            tenant_id=TENANT_A,
            user_id=USER_A,
        )
        raw = json.loads(canonical_json(pool.spaces[0]))
        with self.assertRaises(ContractValidationError):
            _restore_personal_space(raw)

    def test_shared_promotion_proposal_round_trip_stays_advisory(
        self,
    ) -> None:
        original = make_shared_promotion()
        raw = json.loads(canonical_json(original))
        restored = _restore_shared_promotion(raw)
        self.assertEqual(restored, original)
        self.assertIs(
            restored.state,
            SharedPromotionState.SHARED_PROMOTION_PROPOSED,
        )

    def test_memory_item_round_trip_cannot_manufacture_verified_activation(
        self,
    ) -> None:
        original = MemoryItem(
            schema_version=CONTRACT_SCHEMA_VERSION,
            memory_item_id="verified-inactive-round-trip",
            visibility=MemoryVisibility.PERSONAL,
            trust_class=MemoryTrustClass.PERSONAL_VERIFIED_PATCH,
            content_kind=MemoryContentKind.FACTUAL,
            content={"statement": "Still awaiting materialization."},
            scope_dimensions=(),
            evidence_references=("evidence-1",),
            created_at=NOW,
            ownership=ownership_a(),
            source_patch_id="proposal-awaiting-materialization",
        )
        raw = json.loads(canonical_json(original))
        restored = _restore_memory_item(raw)
        self.assertEqual(restored, original)
        self.assertFalse(restored.active)

        raw["active"] = True
        with self.assertRaises(ContractValidationError):
            _restore_memory_item(raw)

    def test_initial_proposal_round_trip_preserves_all_bindings(self) -> None:
        original = make_personal_proposal()
        raw = json.loads(canonical_json(original))
        restored = _restore_initial_proposal(raw)
        self.assertEqual(restored, original)
        self.assertEqual(restored.tenant_id, TENANT_A)
        self.assertEqual(restored.owner_user_id, USER_A)
        self.assertEqual(
            restored.target_personal_memory_space_id,
            SPACE_A,
        )
        self.assertIs(restored.lifecycle_state, PatchState.DETECTED)
        self.assertIs(
            restored.origin,
            ProposalOrigin.CRITIC_PROMPT_LOOP,
        )
        self.assertEqual(restored.content_hash, original.content_hash)

    def test_privileged_state_cannot_be_manufactured_during_round_trip(self) -> None:
        active = make_personal_proposal(state=PatchState.ACTIVE)
        raw = json.loads(canonical_json(active))
        with self.assertRaises(ContractValidationError):
            _restore_initial_proposal(raw)

    def test_approval_round_trip_preserves_identity_and_proof(self) -> None:
        proposal = make_personal_proposal(
            state=PatchState.AWAITING_APPROVAL
        )
        original = make_approval(proposal)
        raw = json.loads(canonical_json(original))
        restored = MemoryPatchApproval(
            schema_version=raw["schema_version"],
            approval_id=raw["approval_id"],
            proposal_id=raw["proposal_id"],
            proposal_content_hash=raw["proposal_content_hash"],
            tenant_id=raw["tenant_id"],
            owner_user_id=raw["owner_user_id"],
            personal_memory_space_id=raw["personal_memory_space_id"],
            decision=ApprovalDecision(raw["decision"]),
            approver_type=ActorType(raw["approver_type"]),
            approver_id=raw["approver_id"],
            reason_code=raw["reason_code"],
            decided_at=_parse_timestamp(raw["decided_at"]),
        )
        self.assertEqual(restored, original)
        verify_approval_binding(proposal, restored)

    def test_commit_round_trip_preserves_approval_binding(self) -> None:
        proposal = make_personal_proposal(state=PatchState.APPROVED)
        approval = make_approval(proposal)
        original = make_commit(proposal, approval)
        raw = json.loads(canonical_json(original))
        restored = MemoryPatchCommit(
            schema_version=raw["schema_version"],
            commit_id=raw["commit_id"],
            proposal_id=raw["proposal_id"],
            proposal_content_hash=raw["proposal_content_hash"],
            approval_id=raw["approval_id"],
            approval_proof=raw["approval_proof"],
            committed_patch_id=raw["committed_patch_id"],
            tenant_id=raw["tenant_id"],
            owner_user_id=raw["owner_user_id"],
            personal_memory_space_id=raw["personal_memory_space_id"],
            actor_type=ActorType(raw["actor_type"]),
            actor_id=raw["actor_id"],
            storage_class=StorageClass(raw["storage_class"]),
            committed_at=_parse_timestamp(raw["committed_at"]),
        )
        self.assertEqual(restored, original)
        verify_commit_binding(proposal, approval, restored)

    def test_critic_origin_cannot_be_changed_to_human_by_plain_data(self) -> None:
        proposal = make_personal_proposal()
        raw = json.loads(canonical_json(proposal))
        raw["origin"] = ProposalOrigin.HUMAN_REVIEW.value
        raw.pop("content_hash")
        restored = _restore_initial_proposal(raw)
        self.assertIs(restored.origin, ProposalOrigin.HUMAN_REVIEW)
        self.assertIs(restored.lifecycle_state, PatchState.DETECTED)
        self.assertNotEqual(restored.content_hash, proposal.content_hash)
        with self.assertRaises(ContractValidationError):
            replace(restored, lifecycle_state=PatchState.APPROVED)

    def test_authority_words_inside_candidate_content_remain_inert(self) -> None:
        proposal = make_personal_proposal(
            content={
                "approved": True,
                "committed": True,
                "trusted": True,
                "human_verified": True,
                "authority": "human",
            }
        )
        self.assertIs(proposal.lifecycle_state, PatchState.DETECTED)
        self.assertFalse(hasattr(proposal, "approved"))
        self.assertFalse(hasattr(proposal, "committed"))
        self.assertEqual(proposal.proposed_content["approved"], True)

    def test_correction_candidate_round_trip_stays_advisory(self) -> None:
        original = CorrectionCandidate(
            event_id="critic-round-trip-1",
            tenant_id=TENANT_A,
            user_id=USER_A,
            personal_memory_space_id=SPACE_A,
            source_component=ActorType.CRITIC_PROMPT_LOOP,
            run_id="run-1",
            model_binding_id="model-1",
            draft_v1_reference="draft-1",
            detected_claims=(make_claim(),),
            proposed_correction="Candidate only.",
            available_evidence_references=("evidence-1",),
            uncertainty=0.1,
            created_at=NOW,
            state=CorrectionCandidateState.PROPOSED,
        )
        raw = json.loads(canonical_json(original))
        restored = CorrectionCandidate(
            event_id=raw["event_id"],
            tenant_id=raw["tenant_id"],
            user_id=raw["user_id"],
            personal_memory_space_id=raw["personal_memory_space_id"],
            source_component=ActorType(raw["source_component"]),
            run_id=raw["run_id"],
            model_binding_id=raw["model_binding_id"],
            draft_v1_reference=raw["draft_v1_reference"],
            detected_claims=(make_claim(),),
            proposed_correction=raw["proposed_correction"],
            available_evidence_references=raw[
                "available_evidence_references"
            ],
            uncertainty=raw["uncertainty"],
            created_at=_parse_timestamp(raw["created_at"]),
            state=CorrectionCandidateState(raw["state"]),
        )
        self.assertEqual(restored, original)
        self.assertIs(restored.state, CorrectionCandidateState.PROPOSED)
        self.assertFalse(hasattr(restored, "approval_id"))
        self.assertFalse(hasattr(restored, "commit_id"))

    def test_serialization_preserves_proposal_schema_and_scope(self) -> None:
        proposal = make_personal_proposal()
        raw = json.loads(canonical_json(proposal))
        self.assertEqual(raw["schema_version"], CONTRACT_SCHEMA_VERSION)
        self.assertEqual(raw["tenant_id"], TENANT_A)
        self.assertEqual(raw["owner_user_id"], USER_A)
        self.assertEqual(raw["target_personal_memory_space_id"], SPACE_A)
        self.assertEqual(
            raw["scope_dimensions"][0]["name"],
            "runtime_version",
        )


if __name__ == "__main__":
    unittest.main()
