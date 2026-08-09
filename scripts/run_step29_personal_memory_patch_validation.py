#!/usr/bin/env python3
"""Controlled Step 29 Personal Memory patch-proposal validation.

The runner uses a disposable repository-pinned CockroachDB, a sanitized real
Step 20 through Step 28 lineage, and deterministic synthetic negative cases.
It performs no provider/model call, retrieval, web access, AWS/S3 mutation,
approval, commit, activation, or execution.
"""

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
from tests.test_step26_verified_answer_output import hat_lineage  # noqa: E402

from aioa_memory_kernel.answers import assemble_verified_answer  # noqa: E402
from aioa_memory_kernel.claims import (  # noqa: E402
    ClaimEvidenceBindingService,
    prepare_claim_binding_request,
)
from aioa_memory_kernel.contracts.correction import CorrectionCandidate  # noqa: E402
from aioa_memory_kernel.contracts.enums import (  # noqa: E402
    ActorType,
    AnswerStatus,
    CorrectionCandidateState,
    EvidenceStatus,
    PatchState,
    PersonalMemorySpaceState,
)
from aioa_memory_kernel.contracts.evidence import ClaimCandidate  # noqa: E402
from aioa_memory_kernel.contracts.identities import KernelRunIdentity  # noqa: E402
from aioa_memory_kernel.contracts.personal_memory import (  # noqa: E402
    PersonalHatQuotaPolicy,
)
from aioa_memory_kernel.contracts.serialization import (  # noqa: E402
    canonical_json,
    canonical_sha256,
)
from aioa_memory_kernel.persistence import (  # noqa: E402
    AccessMode,
    IdempotencyService,
    RequestContext,
    SerializableTransactionRunner,
)
from aioa_memory_kernel.personal_memory import (  # noqa: E402
    STEP27_SCHEMA_VERSION,
    STEP28_SCHEMA_VERSION,
    STEP29_SCHEMA_VERSION,
    AdvancePersonalMemoryPatchToAwaitingApproval,
    BindPersonalMemoryPatchEvidence,
    ConfigureSlotCommand,
    CorrectionCandidateBridgeService,
    CorrectionCandidateMetadata,
    CorrectionCandidateReasonCode,
    CorrectionCandidateRouteResultLineage,
    CorrectionCandidateTrigger,
    CreateEmptySlotCommand,
    CreatePersonalMemoryPatchProposal,
    ModelBindingCommand,
    PersonalMemoryBindingAction,
    PersonalMemoryBindingMode,
    PersonalMemoryHatSlot,
    PersonalMemoryModelBinding,
    PersonalMemoryMutationActor,
    PersonalMemoryPatchProposalService,
    PersonalMemoryPatchValidationError,
    PersonalMemoryQuotaPolicyRecord,
    PersonalMemoryService,
    ProposalConflictResult,
    ProposalDedupResult,
    ProposalFreshnessResult,
    ProposalGateResult,
    Step29ReasonCode,
    TransitionSlotCommand,
    ValidatePersonalMemoryPatchProposal,
    build_correction_candidate_envelope,
    build_personal_memory_patch_validation_receipt,
    load_personal_memory_patch_validation_policy,
    personal_memory_patch_state_to_jsonb,
    proposal_conflict_between,
)


START_SHA = "8ee125e3ab4b964c4ed85dcee95b08932fe0cab5"
EXPECTED_COCKROACH_SHA256 = step27.EXPECTED_COCKROACH_SHA256
DEFAULT_EXTERNAL_ENV = step27.DEFAULT_EXTERNAL_ENV
SLOT_ID = "personal-slot-step29-validation"
OTHER_USER = "user-step29-isolation"
OTHER_TENANT = "tenant-step29-isolation"
OTHER_TENANT_USER = "user-step29-other-tenant"


class ValidationFailure(RuntimeError):
    """Sanitized controlled-validation failure."""

    def __init__(self, code: str) -> None:
        super().__init__("Step 29 controlled validation failed")
        self.code = code


class _ProgressMigrationClient:
    def __init__(self, delegate: step18._Step18HttpSqlClient) -> None:
        self._delegate = delegate
        self._migration_ids = tuple(
            value.migration_id for value in migrations.load_migrations()
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
        bookkeeping_read = (
            "FROM memory_patch.schema_migrations" in sql
            and "INSERT INTO memory_patch.schema_migrations" not in sql
        )
        if current and not bookkeeping_read and current not in self._announced:
            _progress("MIGRATION_" + current.upper())
            self._announced.add(current)
        try:
            result = self._delegate.execute(database, sql, timeout=timeout)
        except migrations.SqlError as error:
            print(
                canonical_json(
                    {
                        "status": "FAILED",
                        "step": 29,
                        "stage": current or "MIGRATION_CATALOG",
                        "sqlstate": error.sqlstate,
                        "detail": str(error)[:2048],
                    }
                ),
                file=sys.stderr,
                flush=True,
            )
            raise
        if (
            current
            and "INSERT INTO memory_patch.schema_migrations" in sql
            and current in sql
        ):
            self._next += 1
        return result


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cockroach-binary", type=Path)
    parser.add_argument("--external-env", type=Path, default=DEFAULT_EXTERNAL_ENV)
    return parser.parse_args()


def _progress(stage: str) -> None:
    print(
        canonical_json({"status": "RUNNING", "step": 29, "stage": stage}),
        file=sys.stderr,
        flush=True,
    )


def _seed_identity_sql(tenant_id: str, owner_user_id: str, at) -> str:
    quote = migrations.sql_literal
    timestamp = quote(at.isoformat()) + "::TIMESTAMPTZ"
    return ";\n".join(
        (
            "INSERT INTO memory_patch.tenants "
            "(tenant_id, display_name, metadata, created_at, updated_at) VALUES "
            f"({quote(tenant_id)}, 'Step 29 owner tenant', '{{}}'::JSONB, "
            f"{timestamp}, {timestamp}), "
            f"({quote(OTHER_TENANT)}, 'Step 29 isolated tenant', "
            f"'{{}}'::JSONB, {timestamp}, {timestamp})",
            "INSERT INTO memory_patch.users "
            "(tenant_id, user_id, display_name, metadata, created_at, updated_at) "
            "VALUES "
            f"({quote(tenant_id)}, {quote(owner_user_id)}, 'Step 29 owner', "
            f"'{{}}'::JSONB, {timestamp}, {timestamp}), "
            f"({quote(tenant_id)}, {quote(OTHER_USER)}, 'Step 29 other user', "
            f"'{{}}'::JSONB, {timestamp}, {timestamp}), "
            f"({quote(OTHER_TENANT)}, {quote(OTHER_TENANT_USER)}, "
            f"'Step 29 other tenant user', '{{}}'::JSONB, {timestamp}, {timestamp})",
        )
    )


def _quota(tenant_id: str, owner_user_id: str) -> PersonalMemoryQuotaPolicyRecord:
    return PersonalMemoryQuotaPolicyRecord(
        schema_version=STEP27_SCHEMA_VERSION,
        tenant_id=tenant_id,
        owner_user_id=owner_user_id,
        quota_policy_id="personal-quota-step29-validation-v1",
        quota_policy_version="1",
        limits=PersonalHatQuotaPolicy(
            maximum_total_spaces=1,
            maximum_active_spaces=1,
            maximum_archived_spaces=1,
            maximum_bytes=8 * 1024 * 1024,
            maximum_personal_sources=0,
            maximum_active_memory_patches=4,
            maximum_session_memory_bytes=0,
            maximum_ingestion_jobs=0,
            maximum_embedding_or_index_bytes=0,
        ),
        maximum_model_bindings_per_space=1,
    )


def _create_slot(service: PersonalMemoryService, request) -> PersonalMemoryHatSlot:
    tenant_id = request.route.tenant_id
    owner_user_id = request.route.user_id
    at = request.temporal_result.trusted_now - timedelta(seconds=10)
    quota = _quota(tenant_id, owner_user_id)
    slot, _ = service.create_empty_slot(
        CreateEmptySlotCommand(
            schema_version=STEP27_SCHEMA_VERSION,
            tenant_id=tenant_id,
            owner_user_id=owner_user_id,
            personal_memory_space_id=SLOT_ID,
            quota_policy=quota,
            idempotency_key="step29-create-slot",
            requested_at=at,
            actor=PersonalMemoryMutationActor.OWNER_CONFIGURATION,
        )
    )
    slot, _ = service.configure_slot(
        ConfigureSlotCommand(
            schema_version=STEP27_SCHEMA_VERSION,
            tenant_id=tenant_id,
            owner_user_id=owner_user_id,
            personal_memory_space_id=SLOT_ID,
            display_name="Validated Personal Memory corrections",
            quota_policy=quota,
            expected_state_version=slot.state_version,
            expected_configuration_version=slot.configuration_version,
            idempotency_key="step29-configure-slot",
            requested_at=at + timedelta(seconds=1),
            actor=PersonalMemoryMutationActor.OWNER_CONFIGURATION,
        )
    )
    binding = PersonalMemoryModelBinding(
        schema_version=STEP27_SCHEMA_VERSION,
        tenant_id=tenant_id,
        owner_user_id=owner_user_id,
        personal_memory_space_id=SLOT_ID,
        provider_id="provider-neutral-validation",
        model_id="model-step29-validation",
        model_revision_or_declared_version="revision-1",
        binding_mode=PersonalMemoryBindingMode.EXACT_MODEL,
        enabled=True,
        binding_version=1,
        bound_at=at + timedelta(seconds=2),
    )
    slot, _ = service.update_model_binding(
        ModelBindingCommand(
            schema_version=STEP27_SCHEMA_VERSION,
            tenant_id=tenant_id,
            owner_user_id=owner_user_id,
            personal_memory_space_id=SLOT_ID,
            binding=binding,
            action=PersonalMemoryBindingAction.ADD,
            expected_state_version=slot.state_version,
            expected_configuration_version=slot.configuration_version,
            idempotency_key="step29-bind-model",
            requested_at=at + timedelta(seconds=2),
            actor=PersonalMemoryMutationActor.OWNER_CONFIGURATION,
        )
    )
    slot, _ = service.transition_slot(
        TransitionSlotCommand(
            schema_version=STEP27_SCHEMA_VERSION,
            tenant_id=tenant_id,
            owner_user_id=owner_user_id,
            personal_memory_space_id=SLOT_ID,
            target_state=PersonalMemorySpaceState.ACTIVE,
            expected_state_version=slot.state_version,
            expected_configuration_version=slot.configuration_version,
            idempotency_key="step29-activate-slot",
            requested_at=at + timedelta(seconds=3),
            actor=PersonalMemoryMutationActor.OWNER_CONFIGURATION,
        )
    )
    return slot


def _transition_slot(
    service: PersonalMemoryService,
    slot: PersonalMemoryHatSlot,
    target: PersonalMemorySpaceState,
    *,
    key: str,
    at,
) -> PersonalMemoryHatSlot:
    updated, _ = service.transition_slot(
        TransitionSlotCommand(
            schema_version=STEP27_SCHEMA_VERSION,
            tenant_id=slot.tenant_id,
            owner_user_id=slot.owner_user_id,
            personal_memory_space_id=slot.personal_memory_space_id,
            target_state=target,
            expected_state_version=slot.state_version,
            expected_configuration_version=slot.configuration_version,
            idempotency_key=key,
            requested_at=at,
            actor=PersonalMemoryMutationActor.OWNER_CONFIGURATION,
        )
    )
    return updated


def _pipeline_candidate(request, slot: PersonalMemoryHatSlot, *, suffix: str, text: str):
    answer = assemble_verified_answer(request)
    snapshot = ClaimEvidenceBindingService().freeze_packet_input(
        prepare_claim_binding_request(
            request.draft_v1,
            tuple(item.bundle for item in request.step20_outcomes),
            request.temporal_result,
        )
    )
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
    binding = slot.model_bindings[0]
    now = request.temporal_result.trusted_now
    candidate = CorrectionCandidate(
        event_id=f"event-step29-{suffix}",
        tenant_id=slot.tenant_id,
        user_id=slot.owner_user_id,
        personal_memory_space_id=slot.personal_memory_space_id,
        source_component=ActorType.KNOWLEDGE_KERNEL,
        run_id=request.route.request_id,
        model_binding_id=binding.binding_id,
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
        proposed_correction=text,
        available_evidence_references=tuple(item.link_hash for item in links),
        uncertainty=0.0,
        created_at=now + timedelta(seconds=1),
        state=CorrectionCandidateState.DETECTED,
    )
    run = KernelRunIdentity(
        kernel_run_id=request.route.request_id,
        tenant_id=slot.tenant_id,
        user_id=slot.owner_user_id,
        personal_memory_space_id=slot.personal_memory_space_id,
        model_binding_id=binding.binding_id,
        created_at=now,
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
        evidence_status=EvidenceStatus.SUFFICIENT,
        draft_v1_hash=request.draft_v1.draft_hash,
        draft_v2_hash=request.step25_result.draft_v2.draft_v2_hash,
        correction_packet_hash=request.correction_packet.packet_hash,
        verification_summary_hash=request.step25_result.verification_summary.summary_hash,
        verified_answer_hash=answer.answer_hash,
    )
    metadata = CorrectionCandidateMetadata(
        schema_version=STEP28_SCHEMA_VERSION,
        trigger=CorrectionCandidateTrigger.KNOWLEDGE_KERNEL_DETECTED,
        producer_id="kernel-step29-controlled-validation",
        producer_version="1.0.0",
        reason_codes=(CorrectionCandidateReasonCode.SOURCE_KNOWLEDGE_KERNEL,),
    )
    envelope = build_correction_candidate_envelope(
        candidate=candidate,
        kernel_run=run,
        slot=slot,
        route_result_lineage=lineage,
        metadata=metadata,
        idempotency_key=f"step29-candidate-{suffix}",
        submitted_at=now + timedelta(seconds=2),
    )
    return envelope, answer, snapshot, links


def _context(tenant_id: str, user_id: str) -> RequestContext:
    return RequestContext(
        tenant_id=tenant_id,
        user_id=user_id,
        access_mode=AccessMode.USER_PRIVATE,
    )


def _rls_catalog(root: step18._Step18HttpSqlClient, database: str) -> Mapping[str, Any]:
    output = root.execute(
        database,
        "SELECT relname, relrowsecurity, relforcerowsecurity "
        "FROM pg_catalog.pg_class WHERE oid IN ("
        "'memory_patch.memory_patch_proposals'::REGCLASS, "
        "'memory_patch.patch_transition_records'::REGCLASS, "
        "'memory_patch.personal_memory_spaces'::REGCLASS) ORDER BY relname",
        timeout=60,
    )
    rows = migrations.parse_tsv(output)
    if len(rows) != 3 or any(
        row.get("relrowsecurity") != "t"
        or row.get("relforcerowsecurity") != "t"
        for row in rows
    ):
        raise ValidationFailure("STEP29_RLS_FORCE_RLS_MISSING")
    return {
        "tables": [row["relname"] for row in rows],
        "rls_enabled": True,
        "force_rls_enabled": True,
    }


def _transition_command(state, command_type, *, key: str, at, receipt_hash=None):
    return command_type(
        schema_version=STEP29_SCHEMA_VERSION,
        tenant_id=state.proposal.tenant_id,
        owner_user_id=state.proposal.owner_user_id,
        personal_memory_space_id=state.proposal.personal_memory_space_id,
        proposal_id=state.proposal.proposal_id,
        proposal_hash=state.proposal.proposal_hash,
        expected_state=state.state,
        expected_state_version=state.state_version,
        expected_state_hash=state.state_hash,
        idempotency_key=key,
        requested_at=at,
        validation_receipt_hash=receipt_hash,
    )


def _validate_service(*, root, database: str, role: str) -> Mapping[str, Any]:
    request, _ = hat_lineage()
    tenant_id = request.route.tenant_id
    owner_user_id = request.route.user_id
    now = request.temporal_result.trusted_now
    if root.sql_port is None:
        raise ValidationFailure("STEP29_SQL_PORT_MISSING")
    factory = lambda: step27._PgwireConnection(  # noqa: E731
        port=root.sql_port, database=database, user=role
    )
    runner = SerializableTransactionRunner(factory, sleep=lambda _delay: None)
    clock = lambda: now + timedelta(hours=1)  # noqa: E731
    personal_service = PersonalMemoryService(
        runner, idempotency=IdempotencyService(clock=clock)
    )
    candidate_service = CorrectionCandidateBridgeService(
        runner, idempotency=IdempotencyService(clock=clock)
    )
    proposal_service = PersonalMemoryPatchProposalService(
        runner, idempotency=IdempotencyService(clock=clock)
    )
    slot = _create_slot(personal_service, request)
    _progress("STEP29_TARGET_SLOT_ACTIVE")

    envelope, answer, snapshot, links = _pipeline_candidate(
        request,
        slot,
        suffix="primary",
        text=request.correction_packet.ordered_claims[0].exact_claim_text,
    )
    stored_candidate, _ = candidate_service.submit_kernel_candidate(envelope)
    if stored_candidate.envelope_hash != envelope.envelope_hash:
        raise ValidationFailure("STEP29_CANDIDATE_INTAKE_MISMATCH")
    command = CreatePersonalMemoryPatchProposal(
        schema_version=STEP29_SCHEMA_VERSION,
        tenant_id=tenant_id,
        owner_user_id=owner_user_id,
        personal_memory_space_id=SLOT_ID,
        candidate_id=envelope.candidate_id,
        candidate_envelope_hash=envelope.envelope_hash,
        expected_target_binding_hash=(
            envelope.submission.target_slot_binding.target_binding_hash
        ),
        idempotency_key="step29-create-primary",
        requested_at=now + timedelta(seconds=3),
    )
    proposed, create_receipt = proposal_service.create_proposal(envelope, command)
    create_replay, create_replay_receipt = proposal_service.create_proposal(
        envelope, command
    )
    if (
        proposed.state is not PatchState.PROPOSED
        or create_replay != proposed
        or not create_replay_receipt.idempotent_replay
    ):
        raise ValidationFailure("STEP29_CREATE_OR_REPLAY_FAILED")
    _progress("STEP29_DETECTED_TO_PROPOSED")

    evidence = {
        "bundles": tuple(item.bundle for item in request.step20_outcomes),
        "temporal_result": request.temporal_result,
        "claim_links": links,
        "claim_assessments": snapshot.ordered_candidate_assessments,
        "correction_packet": request.correction_packet,
        "verified_answer": answer,
    }
    bind_command = _transition_command(
        proposed,
        BindPersonalMemoryPatchEvidence,
        key="step29-bind-primary",
        at=now + timedelta(seconds=4),
    )
    bound, bind_receipt = proposal_service.bind_evidence(bind_command, **evidence)
    bound_replay, bound_replay_receipt = proposal_service.bind_evidence(
        bind_command, **evidence
    )
    if (
        bound.state is not PatchState.EVIDENCE_BOUND
        or bound_replay != bound
        or not bound_replay_receipt.idempotent_replay
    ):
        raise ValidationFailure("STEP29_EVIDENCE_BIND_OR_REPLAY_FAILED")
    _progress("STEP29_PROPOSED_TO_EVIDENCE_BOUND")

    validate_command = _transition_command(
        bound,
        ValidatePersonalMemoryPatchProposal,
        key="step29-validate-primary",
        at=now + timedelta(seconds=5),
    )
    validated, validation_receipt, validation_transition = (
        proposal_service.validate_proposal(validate_command, **evidence)
    )
    validated_replay, validation_replay_receipt, validation_replay_transition = (
        proposal_service.validate_proposal(validate_command, **evidence)
    )
    if (
        validated.state is not PatchState.VALIDATED
        or not validation_receipt.validated
        or validation_transition is None
        or validated_replay != validated
        or validation_replay_receipt.receipt_hash
        != validation_receipt.receipt_hash
        or validation_replay_transition is None
        or not validation_replay_transition.idempotent_replay
    ):
        raise ValidationFailure("STEP29_VALIDATION_OR_REPLAY_FAILED")
    _progress("STEP29_EVIDENCE_BOUND_TO_VALIDATED")

    await_command = _transition_command(
        validated,
        AdvancePersonalMemoryPatchToAwaitingApproval,
        key="step29-await-primary",
        at=now + timedelta(seconds=6),
        receipt_hash=validation_receipt.receipt_hash,
    )
    awaiting, await_receipt = proposal_service.advance_to_awaiting_approval(
        await_command
    )
    awaiting_replay, await_replay_receipt = (
        proposal_service.advance_to_awaiting_approval(await_command)
    )
    if (
        awaiting.state is not PatchState.AWAITING_APPROVAL
        or awaiting_replay != awaiting
        or not await_replay_receipt.idempotent_replay
    ):
        raise ValidationFailure("STEP29_AWAITING_APPROVAL_OR_REPLAY_FAILED")
    _progress("STEP29_VALIDATED_TO_AWAITING_APPROVAL")

    duplicate_rejected = False
    try:
        proposal_service.create_proposal(
            envelope,
            replace(
                command,
                idempotency_key="step29-create-exact-duplicate",
                requested_at=now + timedelta(seconds=7),
            ),
        )
    except PersonalMemoryPatchValidationError as error:
        duplicate_rejected = error.reason_code is Step29ReasonCode.PROPOSAL_DUPLICATE
    if not duplicate_rejected:
        raise ValidationFailure("STEP29_EXACT_DEDUP_FAILED")

    stale_receipt = build_personal_memory_patch_validation_receipt(
        bound,
        dedup_result=ProposalDedupResult.PASS,
        conflict_result=ProposalConflictResult.PASS,
        temporal_trusted_now=request.temporal_result.trusted_now,
        owner_scope_result=ProposalGateResult.PASS,
        slot_state_result=ProposalGateResult.PASS,
        quota_result=ProposalGateResult.PASS,
        model_binding_result=ProposalGateResult.PASS,
        validated_at=now + timedelta(days=2),
    )
    insufficient_rejected = False
    try:
        proposal_service.bind_evidence(
            replace(
                bind_command,
                idempotency_key="step29-bind-insufficient-probe",
            ),
            **{**evidence, "bundles": ()},
        )
    except Exception:
        insufficient_rejected = True
    if (
        stale_receipt.validated
        or stale_receipt.freshness_result is not ProposalFreshnessResult.STALE
        or not insufficient_rejected
    ):
        raise ValidationFailure("STEP29_FRESHNESS_OR_SUFFICIENCY_GATE_FAILED")

    negative_envelope, _, _, _ = _pipeline_candidate(
        request,
        slot,
        suffix="conflict",
        text="Die Vorschrift ist nicht aufgehoben.",
    )
    negative_command = CreatePersonalMemoryPatchProposal(
        schema_version=STEP29_SCHEMA_VERSION,
        tenant_id=tenant_id,
        owner_user_id=owner_user_id,
        personal_memory_space_id=SLOT_ID,
        candidate_id=negative_envelope.candidate_id,
        candidate_envelope_hash=negative_envelope.envelope_hash,
        expected_target_binding_hash=(
            negative_envelope.submission.target_slot_binding.target_binding_hash
        ),
        idempotency_key="step29-conflict-contract",
        requested_at=now + timedelta(seconds=8),
    )
    from aioa_memory_kernel.personal_memory import build_personal_memory_patch_proposal

    negative_state = build_personal_memory_patch_proposal(
        negative_envelope, negative_command
    )
    conflict = proposal_conflict_between(
        awaiting.proposal, negative_state.proposal
    )
    if conflict is not ProposalConflictResult.DIRECT_CONTRADICTION:
        raise ValidationFailure("STEP29_CONFLICT_GATE_FAILED")
    candidate_service.submit_kernel_candidate(negative_envelope)
    negative_proposed, _ = proposal_service.create_proposal(
        negative_envelope, negative_command
    )

    def oversized_transition_probe(tx):
        return tx.fetch_one(
            "UPDATE memory_patch.memory_patch_proposals "
            "SET proposed_content = jsonb_set(proposed_content, "
            "ARRAY['database_quota_padding'], "
            "to_jsonb(repeat('x', %s))), "
            "lifecycle_state = 'EVIDENCE_BOUND', "
            "step29_state_version = 2, step29_state_hash = %s, "
            "step29_evidence_binding_hash = %s "
            "WHERE tenant_id = %s AND owner_user_id = %s "
            "AND proposal_id = %s RETURNING proposal_id",
            (
                8 * 1024 * 1024 + 1024,
                "1" * 64,
                "2" * 64,
                tenant_id,
                owner_user_id,
                negative_proposed.proposal.proposal_id,
            ),
        )

    update_byte_quota_rejected = False
    try:
        runner.run(
            _context(tenant_id, owner_user_id),
            oversized_transition_probe,
            operation_kind="STEP29_DATABASE_UPDATE_BYTE_QUOTA_PROBE",
        )
    except Exception as error:
        update_byte_quota_rejected = getattr(error, "sqlstate", None) == "23514"
    if not update_byte_quota_rejected:
        raise ValidationFailure("STEP29_DATABASE_UPDATE_BYTE_QUOTA_NOT_ENFORCED")

    def count_for(context_tenant: str, context_user: str) -> int:
        row = runner.run(
            _context(context_tenant, context_user),
            lambda tx: tx.fetch_one(
                "SELECT count(*) AS row_count "
                "FROM memory_patch.memory_patch_proposals "
                "WHERE tenant_id = %s AND owner_user_id = %s "
                "AND proposal_id = %s",
                (tenant_id, owner_user_id, awaiting.proposal.proposal_id),
            ),
            operation_kind="STEP29_OWNER_RLS_READ_PROBE",
        )
        return 0 if row is None else int(row["row_count"])

    owner_rows = count_for(tenant_id, owner_user_id)
    cross_user_rows = count_for(tenant_id, OTHER_USER)
    cross_tenant_rows = count_for(OTHER_TENANT, OTHER_TENANT_USER)
    if (owner_rows, cross_user_rows, cross_tenant_rows) != (1, 0, 0):
        raise ValidationFailure("STEP29_OWNER_RLS_READ_LEAK")

    def forbidden_update(context_tenant: str, context_user: str) -> bool:
        row = runner.run(
            _context(context_tenant, context_user),
            lambda tx: tx.fetch_one(
                "UPDATE memory_patch.memory_patch_proposals "
                "SET lifecycle_state = 'ACTIVE' "
                "WHERE tenant_id = %s AND owner_user_id = %s "
                "AND proposal_id = %s RETURNING proposal_id",
                (tenant_id, owner_user_id, awaiting.proposal.proposal_id),
            ),
            operation_kind="STEP29_FORBIDDEN_TRANSITION_PROBE",
        )
        return row is None

    if not forbidden_update(tenant_id, OTHER_USER):
        raise ValidationFailure("STEP29_CROSS_USER_UPDATE_ALLOWED")
    if not forbidden_update(OTHER_TENANT, OTHER_TENANT_USER):
        raise ValidationFailure("STEP29_CROSS_TENANT_UPDATE_ALLOWED")
    if not forbidden_update(tenant_id, owner_user_id):
        raise ValidationFailure("STEP29_STEP30_STATE_ENTERED")

    transition_row = runner.run(
        _context(tenant_id, owner_user_id),
        lambda tx: tx.fetch_one(
            "SELECT count(*) AS row_count "
            "FROM memory_patch.patch_transition_records "
            "WHERE tenant_id = %s AND proposal_id = %s",
            (tenant_id, awaiting.proposal.proposal_id),
        ),
        operation_kind="STEP29_TRANSITION_HISTORY_PROBE",
    )
    if transition_row is None or int(transition_row["row_count"]) != 4:
        raise ValidationFailure("STEP29_TRANSITION_HISTORY_INVALID")

    slot = _transition_slot(
        personal_service,
        slot,
        PersonalMemorySpaceState.ARCHIVED,
        key="step29-archive-target",
        at=now + timedelta(seconds=20),
    )
    archived_denied = False
    try:
        proposal_service.bind_evidence(
            _transition_command(
                negative_proposed,
                BindPersonalMemoryPatchEvidence,
                key="step29-archived-probe",
                at=now + timedelta(seconds=20),
            ),
            **evidence,
        )
    except PersonalMemoryPatchValidationError:
        archived_denied = True
    slot = _transition_slot(
        personal_service,
        slot,
        PersonalMemorySpaceState.CONFIGURED,
        key="step29-restore-target",
        at=now + timedelta(seconds=21),
    )
    slot = _transition_slot(
        personal_service,
        slot,
        PersonalMemorySpaceState.DELETED_PENDING,
        key="step29-delete-pending-target",
        at=now + timedelta(seconds=22),
    )
    delete_pending_denied = False
    try:
        proposal_service.bind_evidence(
            _transition_command(
                negative_proposed,
                BindPersonalMemoryPatchEvidence,
                key="step29-delete-pending-probe",
                at=now + timedelta(seconds=22),
            ),
            **evidence,
        )
    except PersonalMemoryPatchValidationError:
        delete_pending_denied = True
    if not archived_denied or not delete_pending_denied:
        raise ValidationFailure("STEP29_SLOT_STATE_NEGATIVE_FAILED")
    _progress("STEP29_OWNER_SLOT_AND_AUTHORITY_NEGATIVES")

    policy = load_personal_memory_patch_validation_policy()
    receipts = (
        create_receipt,
        bind_receipt,
        validation_transition,
        await_receipt,
    )
    return {
        "candidate_hash": envelope.envelope_hash,
        "proposal_hash": awaiting.proposal.proposal_hash,
        "target_slot_identity": {
            "tenant_id": tenant_id,
            "owner_user_id": owner_user_id,
            "personal_memory_space_id": SLOT_ID,
            "hat_scope_id": awaiting.proposal.hat_scope_id,
        },
        "state_matrix": {
            "DETECTED": "PASS_STEP28_INPUT",
            "PROPOSED": "PASS",
            "EVIDENCE_BOUND": "PASS",
            "VALIDATED": "PASS",
            "AWAITING_APPROVAL": "PASS",
            "SKIP_TRANSITIONS": "REJECTED",
        },
        "evidence_binding_hash": awaiting.evidence_binding.binding_hash,
        "validation_receipt_hash": awaiting.validation_receipt.receipt_hash,
        "validation_policy_digest": policy.policy_digest,
        "dedup": {
            "result": "EXACT_DUPLICATE_REJECTED",
            "dedup_key": awaiting.proposal.exact_dedup_key,
            "existing_patch_duplicate": "NOT_APPLICABLE_STEP30_NOT_STARTED",
        },
        "conflict": {
            "result": conflict.value,
            "deterministic": True,
            "model_resolution": False,
        },
        "freshness": {
            "fresh_result": validation_receipt.freshness_result.value,
            "stale_result": stale_receipt.freshness_result.value,
            "step21_status": request.temporal_result.evidence_status.value,
            "insufficient_evidence_rejected": insufficient_rejected,
        },
        "gates": {
            "owner_scope": validation_receipt.owner_scope_result.value,
            "slot_state": validation_receipt.slot_state_result.value,
            "quota": validation_receipt.quota_result.value,
            "database_update_byte_quota": "REJECTED",
            "model_binding": validation_receipt.model_binding_result.value,
            "archived_slot": "DENIED",
            "delete_pending_slot": "DENIED",
        },
        "idempotency_matrix": {
            "create_replay": create_replay_receipt.idempotent_replay,
            "bind_replay": bound_replay_receipt.idempotent_replay,
            "validate_replay": validation_replay_transition.idempotent_replay,
            "await_replay": await_replay_receipt.idempotent_replay,
        },
        "transition_receipt_hashes": sorted(
            receipt.receipt_hash for receipt in receipts if receipt is not None
        ),
        "owner_isolation": {
            "owner_rows": owner_rows,
            "cross_user_rows": cross_user_rows,
            "cross_tenant_rows": cross_tenant_rows,
            "cross_user_update": "DENIED",
            "cross_tenant_update": "DENIED",
        },
        "real_pipeline_case": True,
        "synthetic_edge_cases": True,
    }


def validate(args: argparse.Namespace) -> Mapping[str, Any]:
    source_binary = step27._source_binary(args)
    source_identity = migrations.verify_binary_identity(source_binary)
    if source_identity["binary_sha256"] != EXPECTED_COCKROACH_SHA256:
        raise ValidationFailure("STEP29_COCKROACH_BINARY_DIGEST_MISMATCH")

    request, _ = hat_lineage()
    runtime = None
    root = None
    database = None
    role = None
    cleanup: Mapping[str, Any] = {}
    primary_error: BaseException | None = None
    cleanup_errors: list[str] = []
    service_result = None
    migration_result = None
    replay_result = None
    rls_result = None

    with tempfile.TemporaryDirectory(prefix="mp-step29-binary-", dir="/tmp") as temp:
        local_binary = Path(temp) / "cockroach"
        shutil.copy2(source_binary, local_binary)
        if (
            migrations.verify_binary_identity(local_binary)["binary_sha256"]
            != EXPECTED_COCKROACH_SHA256
        ):
            raise ValidationFailure("STEP29_COPIED_BINARY_DIGEST_MISMATCH")
        run_id = "mp_step29_" + uuid.uuid4().hex[:12]
        runtime = migrations.LocalRuntime(local_binary, run_id)
        try:
            _progress("START_DISPOSABLE_COCKROACHDB")
            root = step18._start_disposable_runtime(runtime)
            migration_client = _ProgressMigrationClient(root)
            database = run_id + "_db"
            migrations.create_database(root, database)
            _progress("APPLY_MIGRATIONS")
            migration_result = migrations.apply_migrations(
                migration_client, database, timeout=300
            )
            _progress("REPLAY_MIGRATIONS")
            replay_result = migrations.apply_migrations(
                migration_client, database, timeout=300
            )
            if (
                len(migration_result["applied"]) != 13
                or replay_result["applied"]
                or len(replay_result["skipped"]) != 13
            ):
                raise ValidationFailure("STEP29_MIGRATION_REPLAY_MISMATCH")
            root.execute(
                database,
                _seed_identity_sql(
                    request.route.tenant_id,
                    request.route.user_id,
                    request.temporal_result.trusted_now,
                ),
                timeout=120,
            )
            role = "mp_s29_" + uuid.uuid4().hex[:16]
            step27._create_validation_role(root, role)
            rls_result = _rls_catalog(root, database)
            _progress("VALIDATE_STEP29_SERVICE")
            service_result = _validate_service(
                root=root, database=database, role=role
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
                if role is not None:
                    try:
                        step27._drop_validation_role(root, role)
                    except BaseException:
                        cleanup_errors.append("ROLE_CLEANUP_FAILED")
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
        raise ValidationFailure("STEP29_" + "_".join(cleanup_errors))
    if not all(
        cleanup.get(field) is expected
        for field, expected in (
            ("pid_exited", True),
            ("ports_closed", True),
            ("temporary_store_removed", True),
            ("force_kill_used", False),
        )
    ):
        raise ValidationFailure("STEP29_RUNTIME_CLEANUP_INCOMPLETE")
    if None in (service_result, migration_result, replay_result, rls_result):
        raise ValidationFailure("STEP29_VALIDATION_RESULT_INCOMPLETE")

    policy = load_personal_memory_patch_validation_policy()
    result: dict[str, Any] = {
        "step": 29,
        "schema_version": "step29-personal-memory-patch-validation-1a",
        "status": "PASS",
        "start_sha": START_SHA,
        **service_result,
        "database": {
            "version": migrations.PINNED_VERSION,
            "binary_sha256": EXPECTED_COCKROACH_SHA256,
            "migration": "0013_step29_personal_memory_patch_validation.sql",
            "migration_count": len(migration_result["applied"]),
            "replay_skipped_count": len(replay_result["skipped"]),
            "rls": rls_result,
        },
        "proposal_policy": {
            "policy_id": policy.policy_id,
            "policy_version": policy.policy_version,
            "policy_digest": policy.policy_digest,
            "maximum_proposals_per_slot": policy.maximum_proposals_per_slot,
            "maximum_proposal_bytes_per_slot": policy.maximum_proposal_bytes_per_slot,
        },
        "authority": {
            "model_approval_authority": False,
            "critic_approval_authority": False,
            "kernel_auto_approval": False,
            "hat_approval_authority": False,
            "commit_authority": False,
            "activation_authority": False,
            "execution_authority": False,
            "canonical_evidence_promotion": False,
        },
        "effect_bounds": {
            "provider_calls": 0,
            "model_calls": 0,
            "retrieval_calls": 0,
            "web_calls": 0,
            "aws_mutations": 0,
            "s3_mutations": 0,
            "approval_actions": 0,
            "commit_actions": 0,
            "activation_actions": 0,
            "execution_actions": 0,
        },
        "step30_started": False,
        "approved_transitions": 0,
        "committed_transitions": 0,
        "active_transitions": 0,
        "cleanup": {
            "pid_exited": cleanup["pid_exited"],
            "ports_closed": cleanup["ports_closed"],
            "temporary_store_removed": cleanup["temporary_store_removed"],
            "force_kill_used": cleanup["force_kill_used"],
            "database_removed": True,
            "role_removed": True,
        },
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
        print(canonical_json({"status": "FAILED", "reason": reason}), file=sys.stderr)
        return 1
    print(canonical_json(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
