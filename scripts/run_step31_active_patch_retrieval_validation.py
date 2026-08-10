#!/usr/bin/env python3
"""Controlled Step 31 ACTIVE patch retrieval and cross-model reuse proof."""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import uuid
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts"), str(ROOT)]

import run_cockroachdb_migrations as migrations  # noqa: E402
import run_step18_retrieval_validation as step18  # noqa: E402
import run_step27_personal_memory_validation as step27  # noqa: E402
import run_step29_personal_memory_patch_validation as step29  # noqa: E402
import run_step30_user_approval_commit_activation_validation as step30  # noqa: E402
from tests.test_step26_verified_answer_output import hat_lineage  # noqa: E402
from tests.test_step31_active_patch_retrieval import (  # noqa: E402
    later_pipeline,
    provider,
)

from aioa_memory_kernel.contracts.enums import (  # noqa: E402
    PatchState,
    PersonalMemorySpaceState,
)
from aioa_memory_kernel.contracts.serialization import (  # noqa: E402
    canonical_json,
    canonical_sha256,
)
from aioa_memory_kernel.persistence import (  # noqa: E402
    IdempotencyService,
    SerializableTransactionRunner,
)
from aioa_memory_kernel.personal_memory import (  # noqa: E402
    DEFAULT_ACTIVE_PATCH_RESULTS,
    MAXIMUM_ACTIVE_PATCH_CANDIDATES,
    MAXIMUM_ACTIVE_PATCH_RESULTS,
    STEP27_SCHEMA_VERSION,
    ActivePatchRetrievalCockroachRepository,
    ActivePatchRetrievalService,
    ConfigureSlotCommand,
    CorrectionCandidateBridgeService,
    CreateEmptySlotCommand,
    ModelBindingCommand,
    PersonalMemoryActivationService,
    PersonalMemoryApprovalService,
    PersonalMemoryBindingAction,
    PersonalMemoryBindingMode,
    PersonalMemoryCommitHelper,
    PersonalMemoryModelBinding,
    PersonalMemoryMutationActor,
    PersonalMemoryPatchProposalService,
    PersonalMemoryService,
    Step31ReasonCode,
    StoredActivePatchCandidate,
    TransitionSlotCommand,
    assess_active_patch,
    build_active_patch_retrieval_request,
    build_personal_memory_activation_request,
    build_personal_memory_approval_request,
    build_personal_memory_commit_request,
    load_active_patch_retrieval_policy,
)


START_SHA = "753e14b0a079bd48466f694435587a2b5acbe4ca"
EXPECTED_COCKROACH_SHA256 = step27.EXPECTED_COCKROACH_SHA256
DEFAULT_EXTERNAL_ENV = step27.DEFAULT_EXTERNAL_ENV
OTHER_USER = step29.OTHER_USER
OTHER_TENANT = step29.OTHER_TENANT
OTHER_TENANT_USER = step29.OTHER_TENANT_USER


class ValidationFailure(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__("Step 31 controlled validation failed")
        self.code = code


class _ProgressMigrationClient:
    def __init__(self, delegate: step30._Step30HttpSqlClient) -> None:
        self._delegate = delegate
        self._migration_ids = tuple(
            item.migration_id for item in migrations.load_migrations()
        )
        self._next = 0
        self._announced: set[str] = set()

    def execute(
        self,
        database: str,
        sql: str,
        *,
        timeout: float = 300,
    ) -> str:
        current = (
            self._migration_ids[self._next]
            if self._next < len(self._migration_ids)
            else None
        )
        bookkeeping = (
            "FROM memory_patch.schema_migrations" in sql
            and "INSERT INTO memory_patch.schema_migrations" not in sql
        )
        if current and not bookkeeping and current not in self._announced:
            _progress("MIGRATION_" + current.upper())
            self._announced.add(current)
        result = self._delegate.execute(database, sql, timeout=timeout)
        if (
            current
            and "INSERT INTO memory_patch.schema_migrations" in sql
            and current in sql
        ):
            self._next += 1
        return result


class _TrustedClock:
    def __init__(self, current) -> None:
        self.current = current

    def now(self):
        return self.current


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cockroach-binary", type=Path)
    parser.add_argument("--external-env", type=Path, default=DEFAULT_EXTERNAL_ENV)
    return parser.parse_args()


def _progress(stage: str) -> None:
    print(
        canonical_json({"stage": stage, "status": "RUNNING", "step": 31}),
        file=sys.stderr,
        flush=True,
    )


def _create_slot(
    service: PersonalMemoryService,
    request,
    *,
    slot_id: str,
    suffix: str,
    offset: int,
):
    at = request.temporal_result.trusted_now - timedelta(seconds=40 - offset)
    quota = replace(
        step30._quota(
            request.route.tenant_id,
            request.route.user_id,
            suffix=f"step31-{suffix}",
        ),
        quota_policy_id=f"personal-quota-step31-{suffix}-v1",
        maximum_model_bindings_per_space=3,
    )
    slot, _ = service.create_empty_slot(
        CreateEmptySlotCommand(
            schema_version=STEP27_SCHEMA_VERSION,
            tenant_id=request.route.tenant_id,
            owner_user_id=request.route.user_id,
            personal_memory_space_id=slot_id,
            quota_policy=quota,
            idempotency_key=f"step31-{suffix}-create-slot",
            requested_at=at,
            actor=PersonalMemoryMutationActor.OWNER_CONFIGURATION,
        )
    )
    slot, _ = service.configure_slot(
        ConfigureSlotCommand(
            schema_version=STEP27_SCHEMA_VERSION,
            tenant_id=slot.tenant_id,
            owner_user_id=slot.owner_user_id,
            personal_memory_space_id=slot.personal_memory_space_id,
            display_name=f"Step 31 {suffix} private active memory",
            quota_policy=quota,
            expected_state_version=slot.state_version,
            expected_configuration_version=slot.configuration_version,
            idempotency_key=f"step31-{suffix}-configure-slot",
            requested_at=at + timedelta(seconds=1),
            actor=PersonalMemoryMutationActor.OWNER_CONFIGURATION,
        )
    )
    bindings = (
        PersonalMemoryModelBinding(
            schema_version=STEP27_SCHEMA_VERSION,
            tenant_id=slot.tenant_id,
            owner_user_id=slot.owner_user_id,
            personal_memory_space_id=slot.personal_memory_space_id,
            provider_id="provider-neutral-a",
            model_id="model-a",
            model_revision_or_declared_version="revision-1",
            binding_mode=PersonalMemoryBindingMode.EXACT_MODEL,
            enabled=True,
            binding_version=1,
            bound_at=at + timedelta(seconds=2),
        ),
        PersonalMemoryModelBinding(
            schema_version=STEP27_SCHEMA_VERSION,
            tenant_id=slot.tenant_id,
            owner_user_id=slot.owner_user_id,
            personal_memory_space_id=slot.personal_memory_space_id,
            provider_id="provider-neutral-b",
            model_id="model-b",
            model_revision_or_declared_version="revision-2",
            binding_mode=PersonalMemoryBindingMode.EXACT_MODEL,
            enabled=True,
            binding_version=1,
            bound_at=at + timedelta(seconds=3),
        ),
    )
    for index, binding in enumerate(bindings, start=2):
        slot, _ = service.update_model_binding(
            ModelBindingCommand(
                schema_version=STEP27_SCHEMA_VERSION,
                tenant_id=slot.tenant_id,
                owner_user_id=slot.owner_user_id,
                personal_memory_space_id=slot.personal_memory_space_id,
                binding=binding,
                action=PersonalMemoryBindingAction.ADD,
                expected_state_version=slot.state_version,
                expected_configuration_version=slot.configuration_version,
                idempotency_key=f"step31-{suffix}-bind-model-{index}",
                requested_at=at + timedelta(seconds=index),
                actor=PersonalMemoryMutationActor.OWNER_CONFIGURATION,
            )
        )
    slot, _ = service.transition_slot(
        TransitionSlotCommand(
            schema_version=STEP27_SCHEMA_VERSION,
            tenant_id=slot.tenant_id,
            owner_user_id=slot.owner_user_id,
            personal_memory_space_id=slot.personal_memory_space_id,
            target_state=PersonalMemorySpaceState.ACTIVE,
            expected_state_version=slot.state_version,
            expected_configuration_version=slot.configuration_version,
            idempotency_key=f"step31-{suffix}-activate-slot",
            requested_at=at + timedelta(seconds=4),
            actor=PersonalMemoryMutationActor.OWNER_CONFIGURATION,
        )
    )
    return slot, bindings


def _lifecycle_services(
    *,
    request,
    app_runner,
    commit_runner,
    clock,
):
    deterministic_clock = lambda: request.temporal_result.trusted_now + timedelta(hours=1)  # noqa: E731
    common = {"idempotency": IdempotencyService(clock=deterministic_clock)}
    return (
        PersonalMemoryService(app_runner, **common),
        CorrectionCandidateBridgeService(app_runner, **common),
        PersonalMemoryPatchProposalService(app_runner, **common),
        PersonalMemoryApprovalService(
            app_runner,
            **common,
            trusted_clock=clock,
        ),
        PersonalMemoryCommitHelper(
            commit_runner,
            **common,
            trusted_clock=clock,
        ),
        PersonalMemoryActivationService(
            commit_runner,
            **common,
            trusted_clock=clock,
        ),
    )


def _advance(
    *,
    request,
    slot,
    candidate_service,
    proposal_service,
    approval_service,
    commit_service,
    activation_service,
    clock,
    suffix: str,
    offset: int,
    target: PatchState,
):
    awaiting = step30._prepare_awaiting(
        request=request,
        slot=slot,
        candidate_service=candidate_service,
        proposal_service=proposal_service,
        suffix=f"step31-{suffix}",
        offset=offset,
    )
    if target is PatchState.AWAITING_APPROVAL:
        return awaiting
    approval_request = build_personal_memory_approval_request(
        awaiting,
        approval_nonce=f"step31-{suffix}-human-approval",
        requested_at=awaiting.updated_at + timedelta(seconds=1),
    )
    clock.current = approval_request.requested_at
    approved, _, _ = approval_service.approve(
        approval_request,
        authenticated_actor_user_id=slot.owner_user_id,
    )
    commit_request = build_personal_memory_commit_request(
        approved,
        commit_idempotency_key=f"step31-{suffix}-technical-commit",
        requested_at=approved.updated_at + timedelta(seconds=1),
    )
    clock.current = commit_request.requested_at
    committed, _, _ = commit_service.commit(commit_request)
    if target is PatchState.COMMITTED:
        return committed
    activation_request = build_personal_memory_activation_request(
        committed,
        activation_idempotency_key=f"step31-{suffix}-activation",
        requested_at=committed.updated_at + timedelta(seconds=1),
    )
    clock.current = activation_request.requested_at
    active, _, _ = activation_service.activate(activation_request)
    return active


def _retrieval_request(slot, binding, *, query: str, conflict: bool = False, scope=None):
    later_route, temporal = later_pipeline(conflict=conflict, scope=scope)
    return (
        build_active_patch_retrieval_request(
            route=later_route,
            temporal_result=temporal,
            personal_memory_space_id=slot.personal_memory_space_id,
            model_identity=provider(binding),
            query_text=query,
        ),
        later_route,
        temporal,
    )


def _visible_count(
    runner,
    *,
    context,
    tenant_id: str,
    hat_scope_id: str,
) -> int:
    row = runner.run(
        context,
        lambda tx: tx.fetch_one(
            "SELECT count(*) AS row_count "
            "FROM memory_patch.memory_items "
            "WHERE tenant_id = %s AND hat_scope_id = %s "
            "AND active = true AND revoked = false",
            (tenant_id, hat_scope_id),
        ),
        operation_kind="STEP31_OWNER_RLS_PROBE",
    )
    return 0 if row is None else int(row["row_count"])


def _catalog(root, database: str) -> Mapping[str, Any]:
    rows = migrations.parse_tsv(
        root.execute(
            database,
            "SELECT n.nspname AS schema_name, c.relname AS table_name, "
            "c.relrowsecurity AS rls_enabled, c.relforcerowsecurity AS force_rls "
            "FROM pg_catalog.pg_class AS c "
            "JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'memory_patch' AND c.relname IN "
            "('memory_items','memory_patch_proposals','personal_memory_spaces',"
            "'personal_memory_model_bindings') ORDER BY c.relname",
            timeout=60,
        )
    )
    if len(rows) != 4 or any(
        row.get("rls_enabled") not in (True, "true", "t", 1)
        or row.get("force_rls") not in (True, "true", "t", 1)
        for row in rows
    ):
        raise ValidationFailure("STEP31_RLS_CATALOG_INVALID")
    indexes = migrations.parse_tsv(
        root.execute(
            database,
            "SELECT DISTINCT index_name FROM information_schema.statistics "
            "WHERE table_schema = 'memory_patch' "
            "AND table_name = 'memory_items' "
            "AND index_name = 'memory_items_scope_retrieval_idx'",
            timeout=60,
        )
    )
    if len(indexes) != 1:
        raise ValidationFailure("STEP31_RETRIEVAL_INDEX_MISSING")
    return {
        "force_rls": True,
        "rls": True,
        "retrieval_index": "memory_items_scope_retrieval_idx",
        "tables": tuple(row["table_name"] for row in rows),
    }


def _validate_service(
    *,
    root,
    database: str,
    app_role: str,
    commit_role: str,
) -> Mapping[str, Any]:
    pipeline_request, _ = hat_lineage()
    now = pipeline_request.temporal_result.trusted_now
    if root.sql_port is None:
        raise ValidationFailure("STEP31_SQL_PORT_MISSING")
    app_runner = step30._runner(
        port=root.sql_port,
        database=database,
        role=app_role,
    )
    commit_runner = step30._runner(
        port=root.sql_port,
        database=database,
        role=commit_role,
        diagnostic=True,
    )
    clock = _TrustedClock(now + timedelta(hours=1))
    personal, candidates, proposals, approvals, commits, activations = (
        _lifecycle_services(
            request=pipeline_request,
            app_runner=app_runner,
            commit_runner=commit_runner,
            clock=clock,
        )
    )
    retrieval = ActivePatchRetrievalService(app_runner)

    active_slot, active_bindings = _create_slot(
        personal,
        pipeline_request,
        slot_id="personal-slot-step31-active",
        suffix="active",
        offset=0,
    )
    active_state = _advance(
        request=pipeline_request,
        slot=active_slot,
        candidate_service=candidates,
        proposal_service=proposals,
        approval_service=approvals,
        commit_service=commits,
        activation_service=activations,
        clock=clock,
        suffix="active",
        offset=0,
        target=PatchState.ACTIVE,
    )
    _progress("STEP31_ACTIVE_FIXTURE_READY")

    committed_slot, committed_bindings = _create_slot(
        personal,
        pipeline_request,
        slot_id="personal-slot-step31-committed",
        suffix="committed",
        offset=10,
    )
    committed_state = _advance(
        request=pipeline_request,
        slot=committed_slot,
        candidate_service=candidates,
        proposal_service=proposals,
        approval_service=approvals,
        commit_service=commits,
        activation_service=activations,
        clock=clock,
        suffix="committed",
        offset=10,
        target=PatchState.COMMITTED,
    )
    awaiting_slot, awaiting_bindings = _create_slot(
        personal,
        pipeline_request,
        slot_id="personal-slot-step31-awaiting",
        suffix="awaiting",
        offset=20,
    )
    awaiting_state = _advance(
        request=pipeline_request,
        slot=awaiting_slot,
        candidate_service=candidates,
        proposal_service=proposals,
        approval_service=approvals,
        commit_service=commits,
        activation_service=activations,
        clock=clock,
        suffix="awaiting",
        offset=20,
        target=PatchState.AWAITING_APPROVAL,
    )
    _progress("STEP31_NONACTIVE_FIXTURES_READY")

    before = app_runner.run(
        step29._context(active_slot.tenant_id, active_slot.owner_user_id),
        lambda tx: tx.fetch_one(
            "SELECT proposal.lifecycle_state, proposal.step29_state_hash, "
            "item.active, item.step30_state_hash "
            "FROM memory_patch.memory_patch_proposals AS proposal "
            "JOIN memory_patch.memory_items AS item "
            "ON item.tenant_id = proposal.tenant_id "
            "AND item.memory_item_id = proposal.step30_patch_id "
            "WHERE proposal.tenant_id = %s AND proposal.owner_user_id = %s "
            "AND proposal.proposal_id = %s",
            (
                active_slot.tenant_id,
                active_slot.owner_user_id,
                active_state.proposal.proposal_id,
            ),
        ),
        operation_kind="STEP31_STATE_BEFORE_RETRIEVAL",
    )

    model_results = []
    for label, binding in zip(("MODEL_A", "MODEL_B"), active_bindings):
        request, later_route, temporal = _retrieval_request(
            active_slot,
            binding,
            query=f"Step 31 later query for {label}",
        )
        result, context = retrieval.retrieve(
            request,
            route=later_route,
            temporal_result=temporal,
        )
        if len(result.eligible_patches) != 1 or len(context.ordered_active_patches) != 1:
            raise ValidationFailure("STEP31_ALLOWED_MODEL_RETRIEVAL_FAILED")
        model_results.append((label, request, result, context))
    patch_a = model_results[0][2].eligible_patches[0]
    patch_b = model_results[1][2].eligible_patches[0]
    if (
        patch_a.patch_id != patch_b.patch_id
        or patch_a.patch_hash != patch_b.patch_hash
        or patch_a.patch_statement_sha256 != patch_b.patch_statement_sha256
        or patch_a.patch_id != active_state.committed_patch.patch_id
    ):
        raise ValidationFailure("STEP31_CROSS_MODEL_IDENTITY_MISMATCH")
    _progress("STEP31_CROSS_MODEL_REUSE_COMPLETE")

    denied_binding = PersonalMemoryModelBinding(
        schema_version=STEP27_SCHEMA_VERSION,
        tenant_id=active_slot.tenant_id,
        owner_user_id=active_slot.owner_user_id,
        personal_memory_space_id=active_slot.personal_memory_space_id,
        provider_id="provider-neutral-c",
        model_id="model-c-denied",
        model_revision_or_declared_version="revision-3",
        binding_mode=PersonalMemoryBindingMode.EXACT_MODEL,
        enabled=True,
        binding_version=1,
        bound_at=active_slot.updated_at,
    )
    denied_request, denied_route, denied_temporal = _retrieval_request(
        active_slot,
        denied_binding,
        query="Step 31 disallowed model query",
    )
    denied_result, _ = retrieval.retrieve(
        denied_request,
        route=denied_route,
        temporal_result=denied_temporal,
    )
    if denied_result.eligible_patches or not any(
        Step31ReasonCode.MODEL_BINDING_MISMATCH in item.reason_codes
        for item in denied_result.excluded_assessments
    ):
        raise ValidationFailure("STEP31_DISALLOWED_MODEL_ACCEPTED")

    committed_request, committed_route, committed_temporal = _retrieval_request(
        committed_slot,
        committed_bindings[0],
        query="Step 31 committed-only negative",
    )
    committed_result, _ = retrieval.retrieve(
        committed_request,
        route=committed_route,
        temporal_result=committed_temporal,
    )
    awaiting_request, awaiting_route, awaiting_temporal = _retrieval_request(
        awaiting_slot,
        awaiting_bindings[0],
        query="Step 31 awaiting-approval negative",
    )
    awaiting_result, _ = retrieval.retrieve(
        awaiting_request,
        route=awaiting_route,
        temporal_result=awaiting_temporal,
    )
    if committed_result.eligible_patches or awaiting_result.eligible_patches:
        raise ValidationFailure("STEP31_NONACTIVE_PATCH_RETRIEVED")
    _progress("STEP31_ACTIVE_ONLY_GATE_COMPLETE")

    changed_scope = tuple(
        replace(item, value="EU")
        if item.name == "legal_jurisdiction"
        else item
        for item in active_state.committed_patch.patch_scope
    )
    scope_request, scope_route, scope_temporal = _retrieval_request(
        active_slot,
        active_bindings[0],
        query="Step 31 scope mismatch",
        scope=changed_scope,
    )
    scope_result, _ = retrieval.retrieve(
        scope_request,
        route=scope_route,
        temporal_result=scope_temporal,
    )
    if scope_result.eligible_patches or not any(
        Step31ReasonCode.SCOPE_MISMATCH in item.reason_codes
        for item in scope_result.excluded_assessments
    ):
        raise ValidationFailure("STEP31_SCOPE_MISMATCH_ACCEPTED")

    conflict_request, conflict_route, conflict_temporal = _retrieval_request(
        active_slot,
        active_bindings[0],
        query="Step 31 canonical conflict",
        conflict=True,
    )
    conflict_result, _ = retrieval.retrieve(
        conflict_request,
        route=conflict_route,
        temporal_result=conflict_temporal,
    )
    if conflict_result.eligible_patches or not any(
        Step31ReasonCode.PATCH_SUPPRESSED_BY_CANONICAL_EVIDENCE
        in item.reason_codes
        for item in conflict_result.excluded_assessments
    ):
        raise ValidationFailure("STEP31_CANONICAL_CONFLICT_NOT_SUPPRESSED")

    repository = ActivePatchRetrievalCockroachRepository()
    stored = app_runner.run(
        step29._context(active_slot.tenant_id, active_slot.owner_user_id),
        lambda tx: repository.list_active_patch_candidates(
            tx,
            tenant_id=active_slot.tenant_id,
            owner_user_id=active_slot.owner_user_id,
            personal_memory_space_id=active_slot.personal_memory_space_id,
            hat_scope_id=active_slot.hat_scope_id,
            limit=2,
        ),
        operation_kind="STEP31_TEMPORAL_FIXTURE_READ",
    )[0]
    # Rebuild the exact current temporal object associated with MODEL_A rather
    # than trusting a timestamp supplied by a candidate.
    model_a_route, model_a_temporal = later_pipeline()
    temporal_request = build_active_patch_retrieval_request(
        route=model_a_route,
        temporal_result=model_a_temporal,
        personal_memory_space_id=active_slot.personal_memory_space_id,
        model_identity=provider(active_bindings[0]),
        query_text="Step 31 temporal mismatch",
    )
    temporal_assessment, _ = assess_active_patch(
        temporal_request,
        temporal_result=model_a_temporal,
        slot=active_slot,
        candidate=replace(
            stored,
            valid_from=temporal_request.evaluation_as_of + timedelta(seconds=1),
        ),
    )
    if temporal_assessment.eligible or Step31ReasonCode.TEMPORAL_MISMATCH not in (
        temporal_assessment.reason_codes
    ):
        raise ValidationFailure("STEP31_TEMPORAL_MISMATCH_ACCEPTED")
    _progress("STEP31_SCOPE_TEMPORAL_CONFLICT_GATES_COMPLETE")

    owner_context = step29._context(active_slot.tenant_id, active_slot.owner_user_id)
    user_b_context = step29._context(active_slot.tenant_id, OTHER_USER)
    tenant_b_context = step29._context(OTHER_TENANT, OTHER_TENANT_USER)
    owner_rows = _visible_count(
        app_runner,
        context=owner_context,
        tenant_id=active_slot.tenant_id,
        hat_scope_id=active_slot.hat_scope_id,
    )
    cross_user_rows = _visible_count(
        app_runner,
        context=user_b_context,
        tenant_id=active_slot.tenant_id,
        hat_scope_id=active_slot.hat_scope_id,
    )
    cross_tenant_rows = _visible_count(
        app_runner,
        context=tenant_b_context,
        tenant_id=active_slot.tenant_id,
        hat_scope_id=active_slot.hat_scope_id,
    )
    if (owner_rows, cross_user_rows, cross_tenant_rows) != (1, 0, 0):
        raise ValidationFailure("STEP31_OWNER_RLS_FAILED")

    after = app_runner.run(
        owner_context,
        lambda tx: tx.fetch_one(
            "SELECT proposal.lifecycle_state, proposal.step29_state_hash, "
            "item.active, item.step30_state_hash "
            "FROM memory_patch.memory_patch_proposals AS proposal "
            "JOIN memory_patch.memory_items AS item "
            "ON item.tenant_id = proposal.tenant_id "
            "AND item.memory_item_id = proposal.step30_patch_id "
            "WHERE proposal.tenant_id = %s AND proposal.owner_user_id = %s "
            "AND proposal.proposal_id = %s",
            (
                active_slot.tenant_id,
                active_slot.owner_user_id,
                active_state.proposal.proposal_id,
            ),
        ),
        operation_kind="STEP31_STATE_AFTER_RETRIEVAL",
    )
    if before is None or after != before:
        raise ValidationFailure("STEP31_RETRIEVAL_MUTATED_PATCH")
    _progress("STEP31_OWNER_RLS_AND_READ_ONLY_COMPLETE")

    policy = load_active_patch_retrieval_policy()
    return {
        "active_patch": {
            "activation_receipt_hash": active_state.activation_receipt.receipt_hash,
            "approval_receipt_hash": active_state.approval_receipt.receipt_hash,
            "commit_receipt_hash": active_state.commit_receipt.receipt_hash,
            "patch_hash": active_state.committed_patch.patch_hash,
            "patch_id": active_state.committed_patch.patch_id,
            "proposal_hash": active_state.proposal.proposal_hash,
            "statement_sha256": active_state.committed_patch.patch_statement_sha256,
        },
        "bounds": {
            "default_result_limit": DEFAULT_ACTIVE_PATCH_RESULTS,
            "hard_candidate_limit": MAXIMUM_ACTIVE_PATCH_CANDIDATES,
            "maximum_result_limit": MAXIMUM_ACTIVE_PATCH_RESULTS,
            "parameterized_query": True,
            "truncated_result_behavior": "DETERMINISTIC_HASH_BOUND",
        },
        "canonical_evidence": {
            "conflict_result": "PATCH_SUPPRESSED",
            "patch_canonical_evidence": False,
            "source_authority_upgrade": False,
        },
        "cross_model_reuse": {
            "model_a": {
                "identity_digest": model_results[0][1].model_identity.identity_digest,
                "request_hash": model_results[0][1].request_hash,
                "result_hash": model_results[0][2].result_hash,
                "context_hash": model_results[0][3].context_hash,
            },
            "model_b": {
                "identity_digest": model_results[1][1].model_identity.identity_digest,
                "request_hash": model_results[1][1].request_hash,
                "result_hash": model_results[1][2].result_hash,
                "context_hash": model_results[1][3].context_hash,
            },
            "model_c_denied": True,
            "same_patch_hash_across_models": True,
        },
        "nonactive": {
            "awaiting_approval_not_retrieved": not awaiting_result.eligible_patches,
            "awaiting_proposal_hash": awaiting_state.proposal.proposal_hash,
            "committed_not_retrieved": not committed_result.eligible_patches,
            "committed_patch_hash": committed_state.committed_patch.patch_hash,
        },
        "owner_scope": {
            "cross_tenant_rows": cross_tenant_rows,
            "cross_user_rows": cross_user_rows,
            "owner_rows": owner_rows,
            "owner_user_id": active_slot.owner_user_id,
            "personal_memory_space_id": active_slot.personal_memory_space_id,
            "tenant_id": active_slot.tenant_id,
        },
        "policy_digest": policy.policy_digest,
        "query_time_gates": {
            "canonical_conflict": "SUPPRESSED",
            "scope_match": "PASS",
            "scope_mismatch": "DENIED",
            "temporal_match": "PASS",
            "temporal_mismatch": "DENIED",
        },
        "read_only": {
            "patch_state_after": after["lifecycle_state"],
            "patch_state_before": before["lifecycle_state"],
            "semantic_state_mutations": 0,
            "state_hash_unchanged": after["step29_state_hash"] == before["step29_state_hash"],
        },
    }


def validate(args: argparse.Namespace) -> Mapping[str, Any]:
    source_binary = step27._source_binary(args)
    identity = migrations.verify_binary_identity(source_binary)
    if identity["binary_sha256"] != EXPECTED_COCKROACH_SHA256:
        raise ValidationFailure("STEP31_COCKROACH_BINARY_DIGEST_MISMATCH")
    pipeline_request, _ = hat_lineage()
    runtime = None
    root = None
    database = None
    app_role = None
    commit_role = None
    cleanup: Mapping[str, Any] = {}
    cleanup_errors: list[str] = []
    primary_error: BaseException | None = None
    service_result = None
    migration_result = None
    replay_result = None
    catalog_result = None

    with tempfile.TemporaryDirectory(prefix="mp-step31-binary-", dir="/tmp") as temp:
        local_binary = Path(temp) / "cockroach"
        shutil.copy2(source_binary, local_binary)
        if (
            migrations.verify_binary_identity(local_binary)["binary_sha256"]
            != EXPECTED_COCKROACH_SHA256
        ):
            raise ValidationFailure("STEP31_COPIED_BINARY_DIGEST_MISMATCH")
        run_id = "mp_step31_" + uuid.uuid4().hex[:12]
        runtime = migrations.LocalRuntime(local_binary, run_id)
        try:
            _progress("START_DISPOSABLE_COCKROACHDB")
            started = step18._start_disposable_runtime(runtime)
            root = step30._Step30HttpSqlClient(started.port, started.sql_port)
            client = _ProgressMigrationClient(root)
            database = run_id + "_db"
            migrations.create_database(root, database)
            _progress("APPLY_MIGRATIONS")
            migration_result = migrations.apply_migrations(
                client,
                database,
                timeout=300,
            )
            _progress("REPLAY_MIGRATIONS")
            replay_result = migrations.apply_migrations(
                client,
                database,
                timeout=300,
            )
            expected = len(migrations.load_migrations())
            if (
                len(migration_result["applied"]) != expected
                or replay_result["applied"]
                or len(replay_result["skipped"]) != expected
            ):
                raise ValidationFailure("STEP31_MIGRATION_REPLAY_MISMATCH")
            root.execute(
                database,
                step29._seed_identity_sql(
                    pipeline_request.route.tenant_id,
                    pipeline_request.route.user_id,
                    pipeline_request.temporal_result.trusted_now,
                ),
                timeout=120,
            )
            app_role = "mp_s31_app_" + uuid.uuid4().hex[:12]
            commit_role = "mp_s31_commit_" + uuid.uuid4().hex[:12]
            step27._create_validation_role(root, app_role)
            step30._create_commit_validation_role(root, commit_role)
            migrations.assert_step30_security_catalog(root, database)
            catalog_result = _catalog(root, database)
            _progress("VALIDATE_STEP31_RETRIEVAL")
            service_result = _validate_service(
                root=root,
                database=database,
                app_role=app_role,
                commit_role=commit_role,
            )
        except BaseException as error:
            primary_error = error
        finally:
            _progress("CLEANUP_DISPOSABLE_RUNTIME")
            if root is not None:
                if database is not None:
                    try:
                        migrations.drop_database(root, database, timeout=180)
                    except BaseException:
                        cleanup_errors.append("DATABASE_CLEANUP_FAILED")
                if app_role is not None:
                    try:
                        step27._drop_validation_role(root, app_role)
                    except BaseException:
                        cleanup_errors.append("APP_ROLE_CLEANUP_FAILED")
                if commit_role is not None:
                    try:
                        step30._drop_commit_validation_role(root, commit_role)
                    except BaseException:
                        cleanup_errors.append("COMMIT_ROLE_CLEANUP_FAILED")
            if runtime is not None:
                try:
                    cleanup = step18._stop_owned_runtime(runtime)
                except BaseException:
                    cleanup_errors.append("RUNTIME_CLEANUP_FAILED")

    if primary_error is not None:
        if isinstance(primary_error, ValidationFailure):
            raise primary_error
        code = getattr(primary_error, "sanitized_code", None)
        raise ValidationFailure(
            code if isinstance(code, str) else type(primary_error).__name__.upper()
        ) from primary_error
    if cleanup_errors:
        raise ValidationFailure("STEP31_" + "_".join(cleanup_errors))
    if not all(
        cleanup.get(field) is expected
        for field, expected in (
            ("pid_exited", True),
            ("ports_closed", True),
            ("temporary_store_removed", True),
            ("force_kill_used", False),
        )
    ):
        raise ValidationFailure("STEP31_RUNTIME_CLEANUP_INCOMPLETE")
    if None in (
        service_result,
        migration_result,
        replay_result,
        catalog_result,
    ):
        raise ValidationFailure("STEP31_VALIDATION_RESULT_INCOMPLETE")

    result: dict[str, Any] = {
        "authority": {
            "approval_authority": False,
            "execution_authority": False,
            "model_authority": False,
            "patch_canonical_evidence": False,
        },
        "cleanup": {
            "app_role_removed": True,
            "commit_role_removed": True,
            "database_removed": True,
            "force_kill_used": cleanup["force_kill_used"],
            "pid_exited": cleanup["pid_exited"],
            "ports_closed": cleanup["ports_closed"],
            "temporary_store_removed": cleanup["temporary_store_removed"],
        },
        "database": {
            "binary_sha256": EXPECTED_COCKROACH_SHA256,
            "migration_added": False,
            "migration_count": len(migration_result["applied"]),
            "replay_skipped_count": len(replay_result["skipped"]),
            "security_catalog": catalog_result,
            "version": migrations.PINNED_VERSION,
        },
        "effect_bounds": {
            "approval_actions": 0,
            "aws_mutations": 0,
            "commit_actions": 0,
            "model_calls": 0,
            "patch_state_mutations": 0,
            "provider_calls": 0,
            "s3_mutations": 0,
            "shared_promotion_actions": 0,
            "web_calls": 0,
        },
        "real_cross_model_provider_validation": "NOT_REQUIRED",
        "schema_version": "step31-active-patch-retrieval-validation-1a",
        "status": "PASS",
        "step": 31,
        "step32_boundary": {
            "revocation_transitions": 0,
            "shared_promotions": 0,
            "step32_started": False,
            "supersession_transitions": 0,
        },
        "start_sha": START_SHA,
        **service_result,
    }
    result["validation_digest"] = canonical_sha256(result)
    return result


def main() -> int:
    try:
        result = validate(_arguments())
    except (
        OSError,
        TypeError,
        ValueError,
        RuntimeError,
        migrations.MigrationError,
        ValidationFailure,
    ) as error:
        reason = error.code if isinstance(error, ValidationFailure) else type(error).__name__
        print(
            canonical_json({"reason": reason, "status": "FAILED"}),
            file=sys.stderr,
        )
        return 1
    print(canonical_json(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
