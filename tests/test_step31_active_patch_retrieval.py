"""Step 31 ACTIVE patch retrieval and cross-model reuse tests."""

from __future__ import annotations

import dataclasses
import inspect
import json
import unittest
from dataclasses import replace
from datetime import timedelta
from unittest import mock

from tests._support import REPOSITORY_ROOT
from tests.test_step20_hybrid_evidence_bundle import (
    assemble,
    lexical_candidate,
    lexical_pair,
    route,
)
from tests.test_step21_temporal_resolution import bundle_outcome, metadata, resolve
from tests.test_step29_personal_memory_patch_proposal import fixture
from tests.test_step30_user_approval_commit_activation import lifecycle_chain

from aioa_memory_kernel.contracts.enums import (
    EvidenceStatus,
    PatchState,
    PersonalMemorySpaceState,
    ScopeComparisonMode,
    ScopeValueType,
)
from aioa_memory_kernel.contracts.exceptions import (
    ContractValidationError,
    IntegrityError,
)
from aioa_memory_kernel.contracts.scope import ScopeDimension
from aioa_memory_kernel.contracts.serialization import canonical_sha256
from aioa_memory_kernel.modeling import ProviderIdentity
from aioa_memory_kernel.persistence import SerializableTransactionRunner
from aioa_memory_kernel.retrieval import RetrievalMode
from aioa_memory_kernel.personal_memory import (
    ACTIVE_PATCH_RETRIEVAL_SQL,
    DEFAULT_ACTIVE_PATCH_RESULTS,
    MAXIMUM_ACTIVE_PATCH_CANDIDATES,
    MAXIMUM_ACTIVE_PATCH_RESULTS,
    PRIVATE_MEMORY_CONTEXT_CLASSIFICATION,
    STEP27_SCHEMA_VERSION,
    ActivePatchRetrievalCockroachRepository,
    ActivePatchRetrievalError,
    ActivePatchRetrievalService,
    CanonicalEvidenceCompatibility,
    PersonalMemoryBindingMode,
    PersonalMemoryModelBinding,
    Step31ReasonCode,
    StoredActivePatchCandidate,
    active_patch_candidate_from_row,
    active_patch_retrieval_request_to_jsonb,
    active_patch_retrieval_result_to_jsonb,
    assess_active_patch,
    build_active_patch_retrieval_request,
    build_active_patch_retrieval_result,
    build_personal_memory_context_envelope,
    load_active_patch_retrieval_policy,
    personal_memory_context_envelope_to_jsonb,
    personal_memory_context_payload,
    personal_memory_patch_lifecycle_to_jsonb,
    retrieved_active_patch,
    verify_active_patch_retrieval_request,
    verify_active_patch_retrieval_result,
    verify_personal_memory_context_envelope,
    verify_retrieved_active_patch,
)


ROOT = REPOSITORY_ROOT
BASE_SHA = "753e14b0a079bd48466f694435587a2b5acbe4ca"


def provider(binding: PersonalMemoryModelBinding) -> ProviderIdentity:
    return ProviderIdentity(
        provider_id=binding.provider_id,
        adapter_version="adapter-1",
        model_id=binding.model_id,
        model_revision_or_declared_version=(
            binding.model_revision_or_declared_version
        ),
        endpoint_class="chat-completions",
        tooling_disabled=True,
        function_calling_disabled=True,
        web_browsing_disabled=True,
        code_execution_disabled=True,
        immutable_model_revision=True,
    )


def later_pipeline(*, user_id=None, tenant_id=None, scope=None, conflict=False):
    value = fixture()
    requested_tenant = tenant_id or value.slot.tenant_id
    requested_user = user_id or value.slot.owner_user_id
    later_route = route(
        tenant_id=requested_tenant,
        user_id=requested_user,
        request_id="request-step31-later",
    )
    if scope is not None:
        later_route = replace(later_route, effective_scope=scope)
    values = (metadata(),)
    contents = ("Die Vorschrift ist aufgehoben.",)
    source_ids = None
    if conflict:
        values = (
            metadata(version="version-conflict-a"),
            metadata(version="version-conflict-b"),
        )
        contents = ("Die Vorschrift ist aufgehoben.", "Die Vorschrift gilt fort.")
        source_ids = ("source-conflict-a", "source-conflict-b")
    evidence_route = later_route
    if requested_tenant != "tenant-step20":
        # The old Step 20 unit fixture has a fixed candidate tenant.  Build a
        # valid canonical temporal fixture first, then rebind only the later
        # query identity for this service-level no-slot isolation negative.
        evidence_route = route(
            tenant_id="tenant-step20",
            user_id=requested_user,
            request_id="request-step31-later",
        )
    if scope is None:
        outcome = bundle_outcome(
            *values,
            contents=contents,
            source_ids=source_ids,
            route_value=evidence_route,
        )
    else:
        scoped_candidate = replace(
            lexical_candidate(
                RetrievalMode.EXACT_IDENTIFIER,
                source_id="source-0",
                version_id="version-1",
                chunk_id="chunk-0-version-1",
                content=contents[0],
                structured_metadata=values[0],
            ),
            effective_scope=scope,
        )
        outcome = assemble(
            (
                lexical_pair(
                    RetrievalMode.EXACT_IDENTIFIER,
                    (scoped_candidate,),
                    route_value=evidence_route,
                ),
            ),
            route_value=evidence_route,
        )
    temporal = resolve(outcome, route_value=evidence_route)
    if evidence_route is not later_route:
        temporal = replace(
            temporal,
            request_id=later_route.request_id,
            tenant_id=later_route.tenant_id,
            user_id=later_route.user_id,
            route_hash=later_route.route_hash,
            effective_scope=later_route.effective_scope,
        )
    return later_route, temporal


def active_fixture():
    value, _, _, _, committed, _, active = lifecycle_chain()
    original = value.slot.model_bindings[0]
    second = PersonalMemoryModelBinding(
        schema_version=STEP27_SCHEMA_VERSION,
        tenant_id=value.slot.tenant_id,
        owner_user_id=value.slot.owner_user_id,
        personal_memory_space_id=value.slot.personal_memory_space_id,
        provider_id="provider-neutral-second",
        model_id="other-approved-model",
        model_revision_or_declared_version="revision-2",
        binding_mode=PersonalMemoryBindingMode.EXACT_MODEL,
        enabled=True,
        binding_version=1,
        bound_at=value.slot.updated_at + timedelta(seconds=1),
    )
    slot = replace(
        value.slot,
        state=PersonalMemorySpaceState.ACTIVE,
        model_bindings=(original, second),
        state_version=value.slot.state_version + 1,
        configuration_version=value.slot.configuration_version + 1,
        updated_at=value.slot.updated_at + timedelta(seconds=1),
    )
    return value, slot, committed, active, original, second


def candidate(state=None, **changes):
    active = active_fixture()[3] if state is None else state
    return StoredActivePatchCandidate(
        lifecycle_state=active,
        active=changes.get("active", active.state is PatchState.ACTIVE),
        revoked=changes.get("revoked", False),
        valid_from=changes.get("valid_from"),
        valid_until=changes.get("valid_until"),
        expires_at=changes.get("expires_at"),
    )


def request_for(binding, *, maximum=DEFAULT_ACTIVE_PATCH_RESULTS, **pipeline):
    later_route, temporal = later_pipeline(**pipeline)
    request = build_active_patch_retrieval_request(
        route=later_route,
        temporal_result=temporal,
        personal_memory_space_id=fixture().slot.personal_memory_space_id,
        model_identity=provider(binding),
        query_text="Is the provision repealed in this exact scope?",
        maximum_results=maximum,
    )
    return request, later_route, temporal


class InMemorySlotRepository:
    def __init__(self, slot):
        self.slot = slot

    def get_slot(self, transaction, tenant, owner, space):
        if (tenant, owner, space) != (
            self.slot.tenant_id,
            self.slot.owner_user_id,
            self.slot.personal_memory_space_id,
        ):
            return None
        return self.slot


class InMemoryRetrievalRepository:
    def __init__(self, values):
        self.values = tuple(values)
        self.calls = []

    def list_active_patch_candidates(self, transaction, **kwargs):
        self.calls.append(kwargs)
        return self.values[: kwargs["limit"]]


def service(slot, values):
    runner = SerializableTransactionRunner(lambda: None)
    patcher = mock.patch.object(
        runner,
        "run",
        side_effect=lambda context, callback, **kwargs: callback(object()),
    )
    patcher.start()
    repository = InMemoryRetrievalRepository(values)
    result = ActivePatchRetrievalService(
        runner,
        slot_repository=InMemorySlotRepository(slot),
        retrieval_repository=repository,
    )
    return result, repository, patcher


def lifecycle_row(state):
    proposal = state.proposal
    patch = state.committed_patch
    binding = state.step29_state.evidence_binding
    validation = state.step29_state.validation_receipt
    return {
        "proposal_id": proposal.proposal_id,
        "proposed_content": personal_memory_patch_lifecycle_to_jsonb(state),
        "lifecycle_state": state.state.value,
        "content_hash": proposal.proposal_hash,
        "step29_dedup_key": proposal.exact_dedup_key,
        "step29_candidate_id": proposal.candidate_id,
        "step29_candidate_hash": proposal.candidate_hash,
        "step29_candidate_envelope_hash": proposal.candidate_envelope_hash,
        "step29_target_binding_hash": proposal.target_binding_hash,
        "step29_state_version": state.state_version,
        "step29_state_hash": state.state_hash,
        "step29_evidence_binding_hash": binding.binding_hash,
        "step29_validation_receipt_hash": validation.receipt_hash,
        "step30_approval_id": state.approval_receipt.approval_id,
        "step30_approval_receipt_hash": state.approval_receipt.receipt_hash,
        "step30_commit_receipt_hash": state.commit_receipt.receipt_hash,
        "step30_activation_receipt_hash": state.activation_receipt.receipt_hash,
        "step30_patch_id": patch.patch_id,
        "memory_item_id": patch.patch_id,
        "item_hat_scope_id": patch.hat_scope_id,
        "item_target_scope": "USER_PERSONAL_HAT",
        "item_visibility": "PERSONAL",
        "item_trust_class": "PERSONAL_VERIFIED_PATCH",
        "item_content_kind": "FACTUAL",
        "item_content": dataclasses.asdict(patch),
        "item_scope_dimensions": [dataclasses.asdict(item) for item in patch.patch_scope],
        "item_evidence_references": [
            item.evidence_link_hash for item in binding.ordered_evidence_references
        ],
        "item_source_patch_id": patch.patch_id,
        "valid_from": None,
        "valid_until": None,
        "expires_at": None,
        "item_active": True,
        "item_revoked": False,
        "item_step30_proposal_id": patch.proposal_id,
        "item_step30_proposal_hash": patch.proposal_hash,
        "item_step30_approval_receipt_hash": state.approval_receipt.receipt_hash,
        "item_step30_validation_receipt_hash": validation.receipt_hash,
        "item_step30_commit_receipt_hash": state.commit_receipt.receipt_hash,
        "item_step30_patch_hash": patch.patch_hash,
        "item_step30_state_version": 7,
        "item_step30_state_hash": state.state_hash,
        "item_step30_activation_receipt_hash": state.activation_receipt.receipt_hash,
        "item_step30_activation_payload": dataclasses.asdict(state.activation_receipt),
    }


class Step31ContractTests(unittest.TestCase):
    def test_request_policy_and_hashes_are_deterministic_and_private(self):
        _, _, _, _, first, _ = active_fixture()
        request, _, _ = request_for(first)
        duplicate, _, _ = request_for(first)
        self.assertEqual(request, duplicate)
        self.assertEqual(request.request_hash, duplicate.request_hash)
        self.assertEqual(len(request.query_text_digest), 64)
        self.assertNotIn("provision", json.dumps(active_patch_retrieval_request_to_jsonb(request)))
        self.assertEqual(request.maximum_results, DEFAULT_ACTIVE_PATCH_RESULTS)
        self.assertEqual(load_active_patch_retrieval_policy().maximum_results, 32)
        verify_active_patch_retrieval_request(request)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            request.maximum_results = 99  # type: ignore[misc]

    def test_active_patch_context_is_noncanonical_nonexecuting_and_hash_bound(self):
        _, slot, _, active, first, _ = active_fixture()
        request, _, temporal = request_for(first)
        assessment, matched = assess_active_patch(
            request, temporal_result=temporal, slot=slot, candidate=candidate(active)
        )
        retrieved = retrieved_active_patch(request, candidate(active), assessment, matched)
        result = build_active_patch_retrieval_result(
            request,
            eligible_patches=(retrieved,),
            excluded_assessments=(),
            truncated_patch_hashes=(),
            considered_count=1,
        )
        context = build_personal_memory_context_envelope(request, result)
        self.assertFalse(retrieved.canonical_evidence_authority)
        self.assertFalse(context.canonical_evidence_authority)
        self.assertFalse(context.source_authority_upgrade)
        self.assertFalse(context.execution_authority)
        self.assertEqual(context.classification, PRIVATE_MEMORY_CONTEXT_CLASSIFICATION)
        self.assertEqual(
            personal_memory_context_payload(context)["classification"],
            "PRIVATE USER MEMORY - NON-CANONICAL",
        )
        verify_active_patch_retrieval_result(result)
        verify_personal_memory_context_envelope(context)
        self.assertEqual(
            active_patch_retrieval_result_to_jsonb(result)["result_hash"],
            result.result_hash,
        )
        self.assertEqual(
            personal_memory_context_envelope_to_jsonb(context)["context_hash"],
            context.context_hash,
        )

    def test_request_rejects_unlimited_or_oversized_results(self):
        _, _, _, _, first, _ = active_fixture()
        for invalid in (0, MAXIMUM_ACTIVE_PATCH_RESULTS + 1):
            with self.assertRaises(ContractValidationError):
                request_for(first, maximum=invalid)

    def test_rehashed_request_policy_bypass_is_rejected_by_reconstruction(self):
        _, _, _, _, first, _ = active_fixture()
        request, _, _ = request_for(first)
        object.__setattr__(request, "maximum_results", MAXIMUM_ACTIVE_PATCH_RESULTS + 1)
        object.__setattr__(
            request,
            "request_hash",
            canonical_sha256(request, exclude_fields=("request_hash",)),
        )
        with self.assertRaises(IntegrityError):
            verify_active_patch_retrieval_request(request)

    def test_rehashed_context_authority_bypass_is_rejected(self):
        _, slot, _, active, first, _ = active_fixture()
        request, _, temporal = request_for(first)
        stored = candidate(active)
        assessment, matched = assess_active_patch(
            request, temporal_result=temporal, slot=slot, candidate=stored
        )
        retrieved = retrieved_active_patch(request, stored, assessment, matched)
        object.__setattr__(retrieved, "canonical_evidence_authority", True)
        object.__setattr__(
            retrieved,
            "retrieved_patch_hash",
            canonical_sha256(
                retrieved, exclude_fields=("retrieved_patch_hash",)
            ),
        )
        with self.assertRaises(IntegrityError):
            verify_retrieved_active_patch(retrieved)

    def test_result_rejects_patch_from_another_request(self):
        _, slot, _, active, first, second = active_fixture()
        request_a, _, temporal_a = request_for(first)
        assessment, matched = assess_active_patch(
            request_a,
            temporal_result=temporal_a,
            slot=slot,
            candidate=candidate(active),
        )
        retrieved = retrieved_active_patch(
            request_a, candidate(active), assessment, matched
        )
        request_b, _, _ = request_for(second)
        with self.assertRaises(ContractValidationError):
            build_active_patch_retrieval_result(
                request_b,
                eligible_patches=(retrieved,),
                excluded_assessments=(),
                truncated_patch_hashes=(),
                considered_count=1,
            )

    def test_changed_model_changes_request_and_context_not_patch_identity(self):
        _, slot, _, active, first, second = active_fixture()
        request_a, _, temporal_a = request_for(first)
        request_b, _, temporal_b = request_for(second)
        assessment_a, binding_a = assess_active_patch(
            request_a, temporal_result=temporal_a, slot=slot, candidate=candidate(active)
        )
        assessment_b, binding_b = assess_active_patch(
            request_b, temporal_result=temporal_b, slot=slot, candidate=candidate(active)
        )
        retrieved_a = retrieved_active_patch(
            request_a, candidate(active), assessment_a, binding_a
        )
        retrieved_b = retrieved_active_patch(
            request_b, candidate(active), assessment_b, binding_b
        )
        self.assertNotEqual(request_a.request_hash, request_b.request_hash)
        self.assertEqual(retrieved_a.patch_id, retrieved_b.patch_id)
        self.assertEqual(retrieved_a.patch_hash, retrieved_b.patch_hash)
        self.assertEqual(
            retrieved_a.patch_statement_sha256,
            retrieved_b.patch_statement_sha256,
        )


class Step31ApplicabilityTests(unittest.TestCase):
    def test_active_patch_is_retrieved_for_two_distinct_allowed_models(self):
        _, slot, _, active, first, second = active_fixture()
        for binding in (first, second):
            retrieval, repository, patcher = service(slot, (candidate(active),))
            try:
                request, later_route, temporal = request_for(binding)
                result, context = retrieval.retrieve(
                    request, route=later_route, temporal_result=temporal
                )
            finally:
                patcher.stop()
            self.assertEqual(len(result.eligible_patches), 1)
            self.assertEqual(
                result.eligible_patches[0].patch_id,
                active.committed_patch.patch_id,
            )
            self.assertEqual(context.ordered_active_patches, result.eligible_patches)
            self.assertEqual(repository.calls[0]["limit"], MAXIMUM_ACTIVE_PATCH_CANDIDATES + 1)

    def test_disallowed_model_is_denied_without_copying_patch(self):
        _, slot, _, active, _, _ = active_fixture()
        denied = PersonalMemoryModelBinding(
            schema_version=STEP27_SCHEMA_VERSION,
            tenant_id=slot.tenant_id,
            owner_user_id=slot.owner_user_id,
            personal_memory_space_id=slot.personal_memory_space_id,
            provider_id="unbound-provider",
            model_id="unbound-model",
            model_revision_or_declared_version="revision-9",
            binding_mode=PersonalMemoryBindingMode.EXACT_MODEL,
            enabled=True,
            binding_version=1,
            bound_at=slot.updated_at,
        )
        retrieval, _, patcher = service(slot, (candidate(active),))
        try:
            request, later_route, temporal = request_for(denied)
            result, context = retrieval.retrieve(
                request, route=later_route, temporal_result=temporal
            )
        finally:
            patcher.stop()
        self.assertFalse(result.eligible_patches)
        self.assertEqual(len(result.excluded_assessments), 1)
        self.assertIn(
            Step31ReasonCode.MODEL_BINDING_MISMATCH,
            result.excluded_assessments[0].reason_codes,
        )
        self.assertFalse(context.ordered_active_patches)

    def test_committed_but_inactive_patch_is_not_retrieved(self):
        _, slot, committed, _, first, _ = active_fixture()
        retrieval, _, patcher = service(slot, (candidate(committed, active=False),))
        try:
            request, later_route, temporal = request_for(first)
            result, _ = retrieval.retrieve(
                request, route=later_route, temporal_result=temporal
            )
        finally:
            patcher.stop()
        self.assertFalse(result.eligible_patches)
        self.assertIs(result.excluded_assessments[0].state, PatchState.COMMITTED)
        self.assertIn(
            Step31ReasonCode.PATCH_NOT_ACTIVE,
            result.excluded_assessments[0].reason_codes,
        )

    def test_wrong_scope_and_wider_scope_are_denied(self):
        _, slot, _, active, first, _ = active_fixture()
        changed_scope = tuple(
            replace(item, value="EU")
            if item.name == "legal_jurisdiction"
            else item
            for item in active.committed_patch.patch_scope
        )
        retrieval, _, patcher = service(slot, (candidate(active),))
        try:
            request, later_route, temporal = request_for(first, scope=changed_scope)
            result, _ = retrieval.retrieve(
                request, route=later_route, temporal_result=temporal
            )
        finally:
            patcher.stop()
        self.assertFalse(result.eligible_patches)
        assessment = result.excluded_assessments[0]
        self.assertIn(
            Step31ReasonCode.SCOPE_MISMATCH,
            assessment.reason_codes,
        )

    def test_temporal_window_is_enforced_at_query_evaluation_time(self):
        _, slot, _, active, first, _ = active_fixture()
        request, later_route, temporal = request_for(first)
        future = candidate(
            active,
            valid_from=request.evaluation_as_of + timedelta(seconds=1),
        )
        retrieval, _, patcher = service(slot, (future,))
        try:
            result, _ = retrieval.retrieve(
                request, route=later_route, temporal_result=temporal
            )
        finally:
            patcher.stop()
        self.assertFalse(result.eligible_patches)
        self.assertIn(
            Step31ReasonCode.TEMPORAL_MISMATCH,
            result.excluded_assessments[0].reason_codes,
        )

    def test_temporal_valid_until_boundary_is_end_exclusive(self):
        _, slot, _, active, first, _ = active_fixture()
        request, _, temporal = request_for(first)
        at_end = candidate(active, valid_until=request.evaluation_as_of)
        assessment, _ = assess_active_patch(
            request,
            temporal_result=temporal,
            slot=slot,
            candidate=at_end,
        )
        self.assertFalse(assessment.eligible)
        self.assertIn(
            Step31ReasonCode.TEMPORAL_MISMATCH,
            assessment.reason_codes,
        )

    def test_current_canonical_conflict_suppresses_without_state_mutation(self):
        _, slot, _, active, first, _ = active_fixture()
        retrieval, _, patcher = service(slot, (candidate(active),))
        try:
            request, later_route, temporal = request_for(first, conflict=True)
            self.assertIs(temporal.evidence_status, EvidenceStatus.CONFLICTING)
            before = active.state_hash
            result, _ = retrieval.retrieve(
                request, route=later_route, temporal_result=temporal
            )
        finally:
            patcher.stop()
        self.assertFalse(result.eligible_patches)
        assessment = result.excluded_assessments[0]
        self.assertIs(
            assessment.canonical_evidence_compatibility,
            CanonicalEvidenceCompatibility.CONFLICT,
        )
        self.assertIn(
            Step31ReasonCode.PATCH_SUPPRESSED_BY_CANONICAL_EVIDENCE,
            assessment.reason_codes,
        )
        self.assertEqual(active.state_hash, before)
        self.assertIs(active.state, PatchState.ACTIVE)

    def test_missing_current_support_suppresses_fail_closed(self):
        _, slot, _, active, first, _ = active_fixture()
        later_route = route(
            tenant_id=slot.tenant_id,
            user_id=slot.owner_user_id,
            request_id="request-step31-later",
        )
        temporal = resolve(
            bundle_outcome(
                metadata(version="different-version"),
                contents=("Different canonical fact.",),
                source_ids=("different-source",),
                route_value=later_route,
            ),
            route_value=later_route,
        )
        request = build_active_patch_retrieval_request(
            route=later_route,
            temporal_result=temporal,
            personal_memory_space_id=slot.personal_memory_space_id,
            model_identity=provider(first),
            query_text="Later query",
        )
        assessment, _ = assess_active_patch(
            request, temporal_result=temporal, slot=slot, candidate=candidate(active)
        )
        self.assertFalse(assessment.eligible)
        self.assertIs(
            assessment.canonical_evidence_compatibility,
            CanonicalEvidenceCompatibility.UNCONFIRMED,
        )

    def test_tampered_activation_receipt_fails_closed(self):
        _, slot, _, active, first, _ = active_fixture()
        stored = candidate(active)
        object.__setattr__(active.activation_receipt, "receipt_hash", "0" * 64)
        request, _, temporal = request_for(first)
        with self.assertRaises(ActivePatchRetrievalError):
            assess_active_patch(
                request, temporal_result=temporal, slot=slot, candidate=stored
            )


class Step31IsolationBoundsAndPersistenceTests(unittest.TestCase):
    def test_cross_user_and_cross_tenant_receive_no_slot_or_patch(self):
        _, slot, _, active, first, _ = active_fixture()
        for values in (
            {"user_id": "different-user"},
            {"tenant_id": "different-tenant", "user_id": "different-user"},
        ):
            later_route, temporal = later_pipeline(**values)
            request = build_active_patch_retrieval_request(
                route=later_route,
                temporal_result=temporal,
                personal_memory_space_id=slot.personal_memory_space_id,
                model_identity=provider(first),
                query_text="Owner isolation query",
            )
            retrieval, repository, patcher = service(slot, (candidate(active),))
            try:
                result, context = retrieval.retrieve(
                    request, route=later_route, temporal_result=temporal
                )
            finally:
                patcher.stop()
            self.assertFalse(result.eligible_patches)
            self.assertFalse(context.ordered_active_patches)
            self.assertFalse(repository.calls)

    def test_slot_archive_blocks_retrieval_before_patch_query(self):
        _, slot, _, active, first, _ = active_fixture()
        slot = replace(
            slot,
            state=PersonalMemorySpaceState.ARCHIVED,
            state_version=slot.state_version + 1,
            updated_at=slot.updated_at + timedelta(seconds=1),
        )
        retrieval, repository, patcher = service(slot, (candidate(active),))
        try:
            request, later_route, temporal = request_for(first)
            result, _ = retrieval.retrieve(
                request, route=later_route, temporal_result=temporal
            )
        finally:
            patcher.stop()
        self.assertFalse(result.eligible_patches)
        self.assertFalse(repository.calls)

    def test_retrieval_sql_is_parameterized_bounded_owner_scoped_and_read_only(self):
        normalized = " ".join(ACTIVE_PATCH_RETRIEVAL_SQL.upper().split())
        self.assertTrue(normalized.startswith("SELECT"))
        for forbidden in (" UPDATE ", " INSERT ", " DELETE ", " SET_STATE"):
            self.assertNotIn(forbidden, " " + normalized + " ")
        for required in (
            "ITEM.TENANT_ID = %S",
            "PROPOSAL.OWNER_USER_ID = %S",
            "PROPOSAL.PERSONAL_MEMORY_SPACE_ID = %S",
            "ITEM.HAT_SCOPE_ID = %S",
            "ITEM.ACTIVE = TRUE",
            "ITEM.REVOKED = FALSE",
            "LIMIT %S",
        ):
            self.assertIn(required, normalized)
        migration = (
            ROOT / "sql/cockroachdb/migrations/0003_step4_kernel_memory_and_audit_evidence.sql"
        ).read_text()
        self.assertIn("CREATE INDEX memory_items_scope_retrieval_idx", migration)
        self.assertFalse((ROOT / "sql/cockroachdb/migrations/0015_step31_active_patch_retrieval.sql").exists())

    def test_active_database_row_roundtrip_revalidates_all_step30_lineage(self):
        active = active_fixture()[3]
        row = lifecycle_row(active)
        stored = active_patch_candidate_from_row(row)
        self.assertEqual(stored.lifecycle_state, active)
        bad = dict(row, item_step30_activation_receipt_hash="0" * 64)
        with self.assertRaises(IntegrityError):
            active_patch_candidate_from_row(bad)

    def test_repository_enforces_hard_limit_before_sql(self):
        with self.assertRaises(Exception):
            ActivePatchRetrievalCockroachRepository.list_active_patch_candidates(
                mock.Mock(),
                tenant_id="tenant",
                owner_user_id="owner",
                personal_memory_space_id="slot",
                hat_scope_id="scope",
                limit=MAXIMUM_ACTIVE_PATCH_CANDIDATES + 2,
            )

    def test_deterministic_truncation_contract(self):
        _, slot, _, active, first, _ = active_fixture()
        request, _, temporal = request_for(first, maximum=1)
        assessment, matched = assess_active_patch(
            request, temporal_result=temporal, slot=slot, candidate=candidate(active)
        )
        one = retrieved_active_patch(request, candidate(active), assessment, matched)
        omitted_hash = canonical_sha256({"omitted": one.patch_hash})
        result = build_active_patch_retrieval_result(
            request,
            eligible_patches=(one,),
            excluded_assessments=(),
            truncated_patch_hashes=(omitted_hash,),
            considered_count=2,
        )
        self.assertTrue(result.truncated)
        self.assertIn(Step31ReasonCode.RESULT_TRUNCATED, result.reason_codes)

    def test_query_text_injection_is_digest_only_and_never_sql(self):
        _, _, _, _, first, _ = active_fixture()
        later_route, temporal = later_pipeline()
        injection = "x' OR 1=1 --"
        request = build_active_patch_retrieval_request(
            route=later_route,
            temporal_result=temporal,
            personal_memory_space_id=fixture().slot.personal_memory_space_id,
            model_identity=provider(first),
            query_text=injection,
        )
        self.assertNotIn(injection, json.dumps(active_patch_retrieval_request_to_jsonb(request)))
        self.assertNotIn(injection, ACTIVE_PATCH_RETRIEVAL_SQL)

    def test_public_retrieval_surface_has_no_mutation_or_later_step_actions(self):
        source = "\n".join(
            inspect.getsource(item)
            for item in (
                ActivePatchRetrievalService,
                ActivePatchRetrievalCockroachRepository,
            )
        ).lower()
        for forbidden in (
            "insert into",
            "update memory_patch",
            "delete from",
            "approve(",
            "commit_helper",
            "supersed",
            "revoke(",
            "shared_promotion",
            "execute_action",
        ):
            self.assertNotIn(forbidden, source)


class Step31ClosureTests(unittest.TestCase):
    def test_required_documents_and_validation_evidence_exist(self):
        expected = (
            "docs/architecture/ACTIVE_PATCH_RETRIEVAL_CROSS_MODEL_REUSE_1A.md",
            "docs/adr/ADR-038-active-patch-retrieval-cross-model-reuse.md",
            "docs/operations/STEP_31_ACTIVE_PATCH_RETRIEVAL_VALIDATION_1A.md",
            "docs/audits/STEP_31_ACTIVE_PATCH_RETRIEVAL_CROSS_MODEL_REUSE_CLOSURE_1A.md",
            "docs/evidence/personal-memory/step31-active-patch-retrieval-validation.json",
            "scripts/run_step31_active_patch_retrieval_validation.py",
        )
        for path in expected:
            self.assertTrue((ROOT / path).is_file(), path)

    def test_validation_evidence_and_live_checkpoint_close_only_step31(self):
        evidence = json.loads(
            (
                ROOT
                / "docs/evidence/personal-memory/step31-active-patch-retrieval-validation.json"
            ).read_text()
        )
        self.assertEqual(evidence["step"], 31)
        self.assertEqual(evidence["start_sha"], BASE_SHA)
        self.assertEqual(evidence["status"], "PASS")
        self.assertTrue(evidence["cross_model_reuse"]["same_patch_hash_across_models"])
        self.assertFalse(evidence["authority"]["patch_canonical_evidence"])
        self.assertFalse(evidence["step32_boundary"]["step32_started"])
        roadmap = (ROOT / "docs/roadmap/PRODUCTION_ROADMAP.md").read_text()
        agents = (ROOT / "AGENTS.md").read_text()
        self.assertIn("- [x] **Step 31", roadmap)
        self.assertIn("Step 31: COMPLETE AND PUSHED at actual closure commit", roadmap)
        self.assertIn("- [x] **Step 32", roadmap)
        self.assertIn("- [x] **Step 33", roadmap)
        self.assertIn("- [ ] **Step 34", roadmap)
        self.assertIn("Step 31: COMPLETE AND PUSHED at actual closure commit", agents)
        self.assertIn("Step 32: COMPLETE AND PUSHED", agents)
        self.assertIn("Step 33: COMPLETE AND PUSHED", agents)
        self.assertIn("Step 34: NOT STARTED", agents)


if __name__ == "__main__":
    unittest.main()
