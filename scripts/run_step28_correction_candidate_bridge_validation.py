#!/usr/bin/env python3
"""Controlled Step 28 correction-candidate bridge validation.

The runner reuses the repository-pinned disposable CockroachDB harness and
the Step 27 owner-private slot service.  It exercises only DETECTED candidate
intake from the trusted Kernel and a synthetic Critic Prompt Loop fixture.
No provider, model, web, AWS, S3, proposal transition, approval, commit,
activation, retrieval, or execution boundary is invoked.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import shutil
import sys
import tempfile
import threading
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts"), str(ROOT)]

import run_cockroachdb_migrations as migrations  # noqa: E402
import run_step18_retrieval_validation as step18  # noqa: E402
import run_step27_personal_memory_validation as step27  # noqa: E402
from aioa_memory_kernel.contracts.correction import CorrectionCandidate  # noqa: E402
from aioa_memory_kernel.contracts.enums import (  # noqa: E402
    ActorType,
    AnswerStatus,
    CorrectionCandidateState,
    EvidenceStatus,
    KnowledgeRoute,
    PersonalMemorySpaceState,
    ScopeComparisonMode,
    ScopeValueType,
)
from aioa_memory_kernel.contracts.evidence import ClaimCandidate  # noqa: E402
from aioa_memory_kernel.contracts.identities import KernelRunIdentity  # noqa: E402
from aioa_memory_kernel.contracts.personal_memory import (  # noqa: E402
    PersonalHatQuotaPolicy,
)
from aioa_memory_kernel.contracts.scope import ScopeDimension  # noqa: E402
from aioa_memory_kernel.contracts.serialization import (  # noqa: E402
    canonical_json,
    canonical_sha256,
)
from aioa_memory_kernel.persistence import (  # noqa: E402
    AccessMode,
    IdempotencyConflictError,
    IdempotencyService,
    RequestContext,
    SerializableTransactionRunner,
)
from aioa_memory_kernel.personal_memory import (  # noqa: E402
    MAXIMUM_CANDIDATE_BYTES_PER_SLOT,
    MAXIMUM_CANDIDATES_PER_SLOT,
    STEP27_SCHEMA_VERSION,
    STEP28_SCHEMA_VERSION,
    ConfigureSlotCommand,
    CorrectionCandidateBridgeService,
    CorrectionCandidateCockroachRepository,
    CorrectionCandidateEnvelope,
    CorrectionCandidateIntakeDisposition,
    CorrectionCandidateIntakeError,
    CorrectionCandidateMetadata,
    CorrectionCandidateReasonCode,
    CorrectionCandidateRouteResultLineage,
    CorrectionCandidateTrigger,
    CreateEmptySlotCommand,
    ModelBindingCommand,
    PersonalMemoryBindingAction,
    PersonalMemoryBindingMode,
    PersonalMemoryHatSlot,
    PersonalMemoryModelBinding,
    PersonalMemoryMutationActor,
    PersonalMemoryQuotaPolicyRecord,
    PersonalMemoryService,
    TransitionSlotCommand,
    build_correction_candidate_envelope,
    correction_candidate_envelope_to_jsonb,
    load_correction_candidate_intake_policy,
    personal_memory_hat_scope_id,
    verify_correction_candidate_envelope,
    verify_correction_candidate_intake_receipt,
    verify_slot_hash,
)


START_SHA = "a4317d4c5689d35f649f19b45646e7205876581f"
EXPECTED_COCKROACH_SHA256 = step27.EXPECTED_COCKROACH_SHA256
DEFAULT_EXTERNAL_ENV = step27.DEFAULT_EXTERNAL_ENV
FIXTURE_TIME = datetime(2042, 2, 3, 4, 5, 6, tzinfo=UTC)
TENANT_A = "tenant-step28-a"
TENANT_B = "tenant-step28-b"
USER_A = "user-step28-a"
USER_B = "user-step28-b"
USER_C = "user-step28-c"
SLOT_A = "personal-slot-step28-a"


class ValidationFailure(RuntimeError):
    """Sanitized controlled-validation failure."""

    def __init__(self, code: str) -> None:
        super().__init__("Step 28 controlled validation failed")
        self.code = code


class _ProgressMigrationClient:
    """Annotate the existing Step 27 HTTP SQL path with migration identity."""

    def __init__(self, delegate: step18._Step18HttpSqlClient) -> None:
        self._delegate = delegate
        self._migration_ids = tuple(
            value.migration_id for value in migrations.load_migrations()
        )
        self._next_migration = 0
        self._announced: set[str] = set()

    def execute(
        self,
        database: str,
        sql: str,
        *,
        timeout: float = 300,
    ) -> str:
        current = (
            self._migration_ids[self._next_migration]
            if self._next_migration < len(self._migration_ids)
            else None
        )
        is_bookkeeping_read = (
            "FROM memory_patch.schema_migrations" in sql
            and "INSERT INTO memory_patch.schema_migrations" not in sql
        )
        if current is not None and not is_bookkeeping_read and current not in self._announced:
            _progress("MIGRATION_" + current.upper())
            self._announced.add(current)
        try:
            result = self._delegate.execute(database, sql, timeout=timeout)
        except migrations.MigrationError as error:
            label = current or "MIGRATION_REPLAY_OR_CATALOG"
            raise migrations.MigrationError(
                f"{label} controlled HTTP SQL failed: {error}"
            ) from error
        if (
            current is not None
            and "INSERT INTO memory_patch.schema_migrations" in sql
            and current in sql
        ):
            self._next_migration += 1
        return result


def _progress(stage: str) -> None:
    """Emit bounded non-secret progress separately from canonical stdout."""

    print(
        canonical_json({"status": "RUNNING", "step": 28, "stage": stage}),
        file=sys.stderr,
        flush=True,
    )


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cockroach-binary", type=Path)
    parser.add_argument(
        "--external-env",
        type=Path,
        default=DEFAULT_EXTERNAL_ENV,
    )
    return parser.parse_args()


def _digest(label: str) -> str:
    return canonical_sha256({"step28-validation": label})


def _scope() -> tuple[ScopeDimension, ...]:
    return (
        ScopeDimension(
            name="domain",
            value="german-law",
            value_type=ScopeValueType.STRING,
            comparison_mode=ScopeComparisonMode.EXACT,
            source="step28-controlled-route",
            required=True,
        ),
    )


def _quota_policy() -> PersonalMemoryQuotaPolicyRecord:
    return PersonalMemoryQuotaPolicyRecord(
        schema_version=STEP27_SCHEMA_VERSION,
        tenant_id=TENANT_A,
        owner_user_id=USER_A,
        quota_policy_id="personal-quota-step28-v1",
        quota_policy_version="1",
        limits=PersonalHatQuotaPolicy(
            maximum_total_spaces=1,
            maximum_active_spaces=1,
            maximum_archived_spaces=1,
            maximum_bytes=4096,
            maximum_personal_sources=0,
            maximum_active_memory_patches=0,
            maximum_session_memory_bytes=0,
            maximum_ingestion_jobs=0,
            maximum_embedding_or_index_bytes=0,
        ),
        maximum_model_bindings_per_space=1,
    )


def _model_binding() -> PersonalMemoryModelBinding:
    return PersonalMemoryModelBinding(
        schema_version=STEP27_SCHEMA_VERSION,
        tenant_id=TENANT_A,
        owner_user_id=USER_A,
        personal_memory_space_id=SLOT_A,
        provider_id="provider-neutral-validation",
        model_id="model-step28-validation",
        model_revision_or_declared_version="revision-1",
        binding_mode=PersonalMemoryBindingMode.EXACT_MODEL,
        enabled=True,
        binding_version=1,
        bound_at=FIXTURE_TIME + timedelta(seconds=2),
    )


def _seed_identity_sql() -> str:
    quote = migrations.sql_literal
    at = quote(FIXTURE_TIME.isoformat()) + "::TIMESTAMPTZ"
    return ";\n".join(
        (
            "INSERT INTO memory_patch.tenants "
            "(tenant_id, display_name, metadata, created_at, updated_at) VALUES "
            f"({quote(TENANT_A)}, 'Step 28 tenant A', '{{}}'::JSONB, {at}, {at}), "
            f"({quote(TENANT_B)}, 'Step 28 tenant B', '{{}}'::JSONB, {at}, {at})",
            "INSERT INTO memory_patch.users "
            "(tenant_id, user_id, display_name, metadata, created_at, updated_at) VALUES "
            f"({quote(TENANT_A)}, {quote(USER_A)}, 'Step 28 user A', "
            f"'{{}}'::JSONB, {at}, {at}), "
            f"({quote(TENANT_A)}, {quote(USER_B)}, 'Step 28 user B', "
            f"'{{}}'::JSONB, {at}, {at}), "
            f"({quote(TENANT_B)}, {quote(USER_C)}, 'Step 28 user C', "
            f"'{{}}'::JSONB, {at}, {at})",
        )
    )


def _transition(
    slot: PersonalMemoryHatSlot,
    target: PersonalMemorySpaceState,
    *,
    key: str,
    at: datetime,
) -> TransitionSlotCommand:
    return TransitionSlotCommand(
        schema_version=STEP27_SCHEMA_VERSION,
        tenant_id=TENANT_A,
        owner_user_id=USER_A,
        personal_memory_space_id=SLOT_A,
        target_state=target,
        expected_state_version=slot.state_version,
        expected_configuration_version=slot.configuration_version,
        idempotency_key=key,
        requested_at=at,
        actor=PersonalMemoryMutationActor.OWNER_CONFIGURATION,
    )


def _candidate_envelope(
    slot: PersonalMemoryHatSlot,
    *,
    source: ActorType,
    run_suffix: str,
    idempotency_key: str,
    proposed_correction: str,
    event_suffix: str | None = None,
    at_offset: int,
) -> CorrectionCandidateEnvelope:
    if len(slot.model_bindings) != 1:
        raise ValidationFailure("STEP28_TARGET_BINDING_CARDINALITY_INVALID")
    binding = slot.model_bindings[0]
    run_id = f"step28-run-{run_suffix}"
    draft_v1_hash = _digest(f"draft-v1-{run_suffix}")
    candidate = CorrectionCandidate(
        event_id=f"step28-event-{event_suffix or run_suffix}",
        tenant_id=TENANT_A,
        user_id=USER_A,
        personal_memory_space_id=SLOT_A,
        source_component=source,
        run_id=run_id,
        model_binding_id=binding.binding_id,
        draft_v1_reference=draft_v1_hash,
        detected_claims=(
            ClaimCandidate(
                claim_id=f"claim-{run_suffix}",
                draft_id=f"draft-v1-{run_suffix}",
                statement="The effective date requires a correction.",
                claim_category="TEMPORAL",
                scope_dimensions=_scope(),
            ),
        ),
        proposed_correction=proposed_correction,
        available_evidence_references=(_digest(f"evidence-{run_suffix}"),),
        uncertainty=0.25,
        created_at=FIXTURE_TIME + timedelta(seconds=at_offset),
        state=CorrectionCandidateState.DETECTED,
    )
    kernel_run = KernelRunIdentity(
        kernel_run_id=run_id,
        tenant_id=TENANT_A,
        user_id=USER_A,
        personal_memory_space_id=SLOT_A,
        model_binding_id=binding.binding_id,
        created_at=FIXTURE_TIME + timedelta(seconds=at_offset - 1),
    )
    lineage = CorrectionCandidateRouteResultLineage(
        schema_version=STEP28_SCHEMA_VERSION,
        request_id=run_id,
        original_query_digest=_digest(f"query-{run_suffix}"),
        route_hash=_digest(f"route-{run_suffix}"),
        result_hash=_digest(f"answer-result-{run_suffix}"),
        knowledge_route=KnowledgeRoute.HAT_ENFORCE,
        selected_hat_id="german-law",
        selected_hat_version="1.0.0",
        selected_manifest_digest=_digest("german-law-manifest"),
        effective_scope=_scope(),
        answer_status=AnswerStatus.VERIFIED,
        evidence_status=EvidenceStatus.SUFFICIENT,
        draft_v1_hash=draft_v1_hash,
        draft_v2_hash=_digest(f"draft-v2-{run_suffix}"),
        correction_packet_hash=_digest(f"packet-{run_suffix}"),
        verification_summary_hash=_digest(f"verification-{run_suffix}"),
        verified_answer_hash=_digest(f"verified-answer-{run_suffix}"),
    )
    is_kernel = source is ActorType.KNOWLEDGE_KERNEL
    metadata = CorrectionCandidateMetadata(
        schema_version=STEP28_SCHEMA_VERSION,
        trigger=(
            CorrectionCandidateTrigger.KNOWLEDGE_KERNEL_DETECTED
            if is_kernel
            else CorrectionCandidateTrigger.CRITIC_PROMPT_LOOP_DETECTED
        ),
        producer_id=(
            "knowledge-kernel-boundary"
            if is_kernel
            else "synthetic-critic-prompt-loop"
        ),
        producer_version="1.0.0",
        reason_codes=(
            CorrectionCandidateReasonCode.SOURCE_KNOWLEDGE_KERNEL
            if is_kernel
            else CorrectionCandidateReasonCode.SOURCE_CRITIC_PROMPT_LOOP,
        ),
    )
    envelope = build_correction_candidate_envelope(
        candidate=candidate,
        kernel_run=kernel_run,
        slot=slot,
        route_result_lineage=lineage,
        metadata=metadata,
        idempotency_key=idempotency_key,
        submitted_at=FIXTURE_TIME + timedelta(seconds=at_offset + 1),
    )
    verify_correction_candidate_envelope(envelope)
    return envelope


def _create_active_slot(
    service: PersonalMemoryService,
) -> tuple[PersonalMemoryHatSlot, PersonalMemoryQuotaPolicyRecord]:
    policy = _quota_policy()
    slot, _ = service.create_empty_slot(
        CreateEmptySlotCommand(
            schema_version=STEP27_SCHEMA_VERSION,
            tenant_id=TENANT_A,
            owner_user_id=USER_A,
            personal_memory_space_id=SLOT_A,
            quota_policy=policy,
            idempotency_key="step28-create-target-slot",
            requested_at=FIXTURE_TIME,
            actor=PersonalMemoryMutationActor.OWNER_CONFIGURATION,
        )
    )
    slot, _ = service.configure_slot(
        ConfigureSlotCommand(
            schema_version=STEP27_SCHEMA_VERSION,
            tenant_id=TENANT_A,
            owner_user_id=USER_A,
            personal_memory_space_id=SLOT_A,
            display_name="Owner correction candidates",
            quota_policy=policy,
            expected_state_version=slot.state_version,
            expected_configuration_version=slot.configuration_version,
            idempotency_key="step28-configure-target-slot",
            requested_at=FIXTURE_TIME + timedelta(seconds=1),
            actor=PersonalMemoryMutationActor.OWNER_CONFIGURATION,
        )
    )
    slot, _ = service.update_model_binding(
        ModelBindingCommand(
            schema_version=STEP27_SCHEMA_VERSION,
            tenant_id=TENANT_A,
            owner_user_id=USER_A,
            personal_memory_space_id=SLOT_A,
            binding=_model_binding(),
            action=PersonalMemoryBindingAction.ADD,
            expected_state_version=slot.state_version,
            expected_configuration_version=slot.configuration_version,
            idempotency_key="step28-bind-target-model",
            requested_at=FIXTURE_TIME + timedelta(seconds=2),
            actor=PersonalMemoryMutationActor.OWNER_CONFIGURATION,
        )
    )
    slot, _ = service.transition_slot(
        _transition(
            slot,
            PersonalMemorySpaceState.ACTIVE,
            key="step28-activate-target-slot",
            at=FIXTURE_TIME + timedelta(seconds=3),
        )
    )
    verify_slot_hash(slot)
    if slot.state is not PersonalMemorySpaceState.ACTIVE:
        raise ValidationFailure("STEP28_TARGET_SLOT_NOT_ACTIVE")
    return slot, policy


def _rls_catalog(
    root: step18._Step18HttpSqlClient,
    database: str,
) -> Mapping[str, Any]:
    output = root.execute(
        database,
        "SELECT relname, relrowsecurity, relforcerowsecurity "
        "FROM pg_catalog.pg_class WHERE oid IN ("
        "'memory_patch.hat_scopes'::REGCLASS, "
        "'memory_patch.memory_patch_proposals'::REGCLASS, "
        "'memory_patch.personal_memory_spaces'::REGCLASS) "
        "ORDER BY relname",
        timeout=60,
    )
    rows = migrations.parse_tsv(output)
    if len(rows) != 3 or any(
        row.get("relrowsecurity") != "t"
        or row.get("relforcerowsecurity") != "t"
        for row in rows
    ):
        raise ValidationFailure("STEP28_RLS_FORCE_RLS_MISSING")
    return {
        "tables": [row["relname"] for row in rows],
        "rls_enabled": True,
        "force_rls_enabled": True,
    }


def _scope_derivation_parity(
    root: step18._Step18HttpSqlClient,
    database: str,
) -> Mapping[str, Any]:
    """Prove Cockroach hashes the exact Step 27 canonical owner tuple bytes."""

    fixtures = (
        ("Tenant.scope:+_A-9", "Owner.scope:+_A-9", "Slot.scope:+_A-9"),
        ("T" + "a" * 254, "U" + "b" * 254, "S" + "c" * 254),
    )
    for tenant_id, owner_user_id, space_id in fixtures:
        output = root.execute(
            database,
            "SELECT memory_patch.step28_personal_hat_scope_id("
            f"{migrations.sql_literal(tenant_id)}, "
            f"{migrations.sql_literal(owner_user_id)}, "
            f"{migrations.sql_literal(space_id)}) AS derived_scope_id",
            timeout=60,
        )
        rows = migrations.parse_tsv(output)
        expected = personal_memory_hat_scope_id(
            tenant_id,
            owner_user_id,
            space_id,
        )
        if rows != [{"derived_scope_id": expected}]:
            raise ValidationFailure("STEP28_SCOPE_DERIVATION_PARITY_MISMATCH")
    return {
        "algorithm": "STEP27_CANONICAL_JSON_SHA256",
        "fixture_count": len(fixtures),
        "maximum_logical_id_bytes_exercised": 255,
        "status": "PASS",
    }


def _validate_service(
    *,
    root: step18._Step18HttpSqlClient,
    database: str,
    role: str,
) -> Mapping[str, Any]:
    if root.sql_port is None:
        raise ValidationFailure("STEP28_SQL_PORT_MISSING")
    factory = lambda: step27._PgwireConnection(  # noqa: E731
        port=root.sql_port,
        database=database,
        user=role,
    )
    runner = SerializableTransactionRunner(factory, sleep=lambda _delay: None)
    completion_time = FIXTURE_TIME + timedelta(hours=2)
    personal_service = PersonalMemoryService(
        runner,
        idempotency=IdempotencyService(clock=lambda: completion_time),
    )
    candidate_service = CorrectionCandidateBridgeService(
        runner,
        idempotency=IdempotencyService(clock=lambda: completion_time),
    )
    candidate_repository = CorrectionCandidateCockroachRepository()
    slot, policy = _create_active_slot(personal_service)
    _progress("CANDIDATE_SCENARIO_TARGET_SLOT_ACTIVE")

    kernel_input = _candidate_envelope(
        slot,
        source=ActorType.KNOWLEDGE_KERNEL,
        run_suffix="kernel",
        idempotency_key="step28-kernel-candidate",
        proposed_correction="Use the verified effective date.",
        at_offset=10,
    )
    kernel_stored, kernel_receipt = candidate_service.submit_kernel_candidate(
        kernel_input
    )
    kernel_replay, replay_receipt = candidate_service.submit_kernel_candidate(
        kernel_input
    )
    duplicate_input = _candidate_envelope(
        slot,
        source=ActorType.KNOWLEDGE_KERNEL,
        run_suffix="kernel",
        idempotency_key="step28-kernel-candidate-duplicate",
        proposed_correction="Use the verified effective date.",
        event_suffix="kernel-reobserved",
        at_offset=10,
    )
    duplicate_stored, duplicate_receipt = (
        candidate_service.submit_kernel_candidate(duplicate_input)
    )
    duplicate_replay_stored, duplicate_replay_receipt = (
        candidate_service.submit_kernel_candidate(duplicate_input)
    )
    if (
        kernel_receipt.disposition
        is not CorrectionCandidateIntakeDisposition.ACCEPTED
        or replay_receipt.disposition
        is not CorrectionCandidateIntakeDisposition.EXACT_REPLAY
        or duplicate_receipt.disposition
        is not CorrectionCandidateIntakeDisposition.DUPLICATE
        or duplicate_replay_receipt.disposition
        is not CorrectionCandidateIntakeDisposition.EXACT_REPLAY
        or len(
            {
                kernel_stored.candidate_id,
                kernel_replay.candidate_id,
                duplicate_stored.candidate_id,
                duplicate_replay_stored.candidate_id,
            }
        )
        != 1
        or duplicate_stored.envelope_hash != kernel_stored.envelope_hash
        or duplicate_receipt.submission_id
        != duplicate_input.submission.submission_id
        or duplicate_receipt.submission_hash
        != duplicate_input.submission.submission_hash
        or duplicate_receipt.envelope_id != duplicate_input.envelope_id
        or duplicate_receipt.envelope_hash != duplicate_input.envelope_hash
        or duplicate_receipt.candidate_event_id
        != duplicate_input.submission.candidate.event_id
        or duplicate_receipt.idempotency_key
        != duplicate_input.submission.idempotency_key
        or duplicate_replay_receipt.submission_hash
        != duplicate_input.submission.submission_hash
        or duplicate_replay_receipt.envelope_hash
        != duplicate_input.envelope_hash
    ):
        raise ValidationFailure("STEP28_IDEMPOTENCY_DEDUP_MISMATCH")
    for receipt in (
        kernel_receipt,
        replay_receipt,
        duplicate_receipt,
        duplicate_replay_receipt,
    ):
        verify_correction_candidate_intake_receipt(receipt)

    conflict_input = _candidate_envelope(
        slot,
        source=ActorType.KNOWLEDGE_KERNEL,
        run_suffix="kernel",
        idempotency_key="step28-kernel-candidate",
        proposed_correction="Use a different correction.",
        event_suffix="kernel-conflict",
        at_offset=12,
    )
    conflicting_replay_rejected = False
    try:
        candidate_service.submit_kernel_candidate(conflict_input)
    except IdempotencyConflictError:
        conflicting_replay_rejected = True
    if not conflicting_replay_rejected:
        raise ValidationFailure("STEP28_CONFLICTING_REPLAY_ACCEPTED")

    critic_input = _candidate_envelope(
        slot,
        source=ActorType.CRITIC_PROMPT_LOOP,
        run_suffix="critic",
        idempotency_key="step28-critic-candidate",
        proposed_correction="Preserve the temporal limitation.",
        at_offset=13,
    )
    critic_stored, critic_receipt = (
        candidate_service.submit_critic_loop_candidate(critic_input)
    )
    verify_correction_candidate_intake_receipt(critic_receipt)
    if critic_receipt.disposition is not CorrectionCandidateIntakeDisposition.ACCEPTED:
        raise ValidationFailure("STEP28_CRITIC_CANDIDATE_NOT_ACCEPTED")

    owner_candidates = candidate_service.list_owner_candidates(
        tenant_id=TENANT_A,
        owner_user_id=USER_A,
        personal_memory_space_id=SLOT_A,
    )
    if len(owner_candidates) != 2:
        raise ValidationFailure("STEP28_CANDIDATE_DEDUP_ROW_COUNT_INVALID")
    _progress("CANDIDATE_SCENARIO_INITIAL_INTAKE_COMPLETE")

    def context(tenant_id: str, user_id: str) -> RequestContext:
        return RequestContext(
            tenant_id=tenant_id,
            user_id=user_id,
            access_mode=AccessMode.USER_PRIVATE,
        )

    def candidate_count(context_tenant: str, context_user: str) -> int:
        row = runner.run(
            context(context_tenant, context_user),
            lambda transaction: transaction.fetch_one(
                "SELECT count(*) AS row_count "
                "FROM memory_patch.memory_patch_proposals "
                "WHERE tenant_id = %s AND owner_user_id = %s "
                "AND personal_memory_space_id = %s "
                "AND lifecycle_state = 'DETECTED'",
                (TENANT_A, USER_A, SLOT_A),
            ),
            operation_kind="STEP28_OWNER_RLS_READ_PROBE",
        )
        if row is None:
            raise ValidationFailure("STEP28_OWNER_RLS_COUNT_MISSING")
        return int(row["row_count"])

    owner_count = candidate_count(TENANT_A, USER_A)
    cross_user_count = candidate_count(TENANT_A, USER_B)
    cross_tenant_count = candidate_count(TENANT_B, USER_C)
    if (owner_count, cross_user_count, cross_tenant_count) != (2, 0, 0):
        raise ValidationFailure("STEP28_OWNER_RLS_READ_LEAK")

    def rejected_cross_scope_insert(
        probe: CorrectionCandidateEnvelope,
        context_tenant: str,
        context_user: str,
    ) -> bool:
        try:
            runner.run(
                context(context_tenant, context_user),
                lambda transaction: candidate_repository.insert_candidate(
                    transaction, probe
                ),
                operation_kind="STEP28_OWNER_RLS_INSERT_PROBE",
            )
        except Exception as error:
            # Cockroach runs the quota BEFORE trigger before the RLS WITH CHECK.
            # A foreign request context therefore cannot see/update the target
            # slot and may fail closed with 23514 before RLS emits 42501/44000.
            if getattr(error, "sqlstate", None) in {"23514", "42501", "44000"}:
                return True
            raise
        return False

    cross_user_probe = _candidate_envelope(
        slot,
        source=ActorType.KNOWLEDGE_KERNEL,
        run_suffix="cross-user-probe",
        idempotency_key="step28-cross-user-probe",
        proposed_correction="Cross-user insertion must be denied.",
        at_offset=14,
    )
    cross_tenant_probe = _candidate_envelope(
        slot,
        source=ActorType.KNOWLEDGE_KERNEL,
        run_suffix="cross-tenant-probe",
        idempotency_key="step28-cross-tenant-probe",
        proposed_correction="Cross-tenant insertion must be denied.",
        at_offset=15,
    )
    cross_user_submit_denied = rejected_cross_scope_insert(
        cross_user_probe, TENANT_A, USER_B
    )
    cross_tenant_submit_denied = rejected_cross_scope_insert(
        cross_tenant_probe, TENANT_B, USER_C
    )
    if not cross_user_submit_denied or not cross_tenant_submit_denied:
        raise ValidationFailure("STEP28_OWNER_RLS_INSERT_ALLOWED")
    if candidate_count(TENANT_A, USER_A) != 2:
        raise ValidationFailure("STEP28_RLS_NEGATIVE_MUTATED_CANDIDATES")
    _progress("CANDIDATE_SCENARIO_OWNER_RLS_COMPLETE")

    # The typed service rejects envelopes above 512 KiB before persistence.
    # This direct, structurally valid carrier proves that the independent DB
    # byte ceiling also rejects an INSERT that reaches the RLS/trigger boundary.
    # Build the oversized member inside CockroachDB so the probe measures the
    # database boundary rather than the client/HTTP SQL statement-size boundary.
    oversized_payload = dict(correction_candidate_envelope_to_jsonb(kernel_input))
    oversized_identity = _digest("database-byte-quota-probe")
    oversized_candidate_id = "correction-candidate-" + oversized_identity
    oversized_payload.update(
        {
            "candidate_id": oversized_candidate_id,
            "envelope_hash": oversized_identity,
            "envelope_id": "correction-candidate-envelope-" + oversized_identity,
        }
    )

    def insert_oversized_database_probe(transaction: object) -> None:
        candidate = kernel_input.submission.candidate
        target = kernel_input.submission.target_slot_binding
        transaction.fetch_one(
            """
            INSERT INTO memory_patch.memory_patch_proposals (
              tenant_id, proposal_id, schema_version, hat_scope_id,
              target_scope, target_hat_id, owner_user_id,
              personal_memory_space_id, origin, proposed_content,
              evidence_references, scope_dimensions, valid_from, valid_until,
              requested_trust_class, approval_requirement, lifecycle_state,
              content_kind, created_at, content_hash
            ) VALUES (
              %s, %s, %s, %s, 'USER_PERSONAL_HAT', NULL, %s, %s, %s,
              jsonb_set(
                %s::JSONB,
                ARRAY['database_quota_padding'],
                to_jsonb(repeat('x', %s))
              ),
              %s::JSONB, %s::JSONB, NULL, NULL,
              'MODEL_EXPERIENCE_HINT', 'OWNER', 'DETECTED',
              'MODEL_EXPERIENCE', %s, %s
            )
            RETURNING proposal_id
            """,
            (
                candidate.tenant_id,
                oversized_candidate_id,
                STEP28_SCHEMA_VERSION,
                target.hat_scope_id,
                candidate.user_id,
                candidate.personal_memory_space_id,
                candidate.source_component.value,
                canonical_json(oversized_payload),
                MAXIMUM_CANDIDATE_BYTES_PER_SLOT + 1024,
                canonical_json(candidate.available_evidence_references),
                canonical_json(kernel_input.submission.lineage.effective_scope),
                candidate.created_at,
                oversized_identity,
            ),
        )

    byte_quota_rejected = False
    try:
        runner.run(
            context(TENANT_A, USER_A),
            insert_oversized_database_probe,
            operation_kind="STEP28_REAL_DATABASE_BYTE_QUOTA_REJECTION",
        )
    except Exception as error:
        byte_quota_rejected = getattr(error, "sqlstate", None) == "23514"
    if not byte_quota_rejected or candidate_count(TENANT_A, USER_A) != 2:
        raise ValidationFailure("STEP28_CANDIDATE_BYTE_QUOTA_NOT_ENFORCED")
    _progress("CANDIDATE_SCENARIO_BYTE_QUOTA_COMPLETE")

    quota_fill = tuple(
        _candidate_envelope(
            slot,
            source=ActorType.KNOWLEDGE_KERNEL,
            run_suffix=f"quota-fill-{ordinal}",
            idempotency_key=f"step28-quota-fill-{ordinal}",
            proposed_correction=f"Bounded quota fixture {ordinal}.",
            at_offset=20 + ordinal,
        )
        for ordinal in range(2, MAXIMUM_CANDIDATES_PER_SLOT - 1)
    )

    def fill_real_database_quota(transaction: object) -> int:
        inserted = 0
        for value in quota_fill:
            _, created = candidate_repository.insert_candidate(transaction, value)
            if not created:
                raise ValidationFailure("STEP28_QUOTA_FILL_WAS_NOT_UNIQUE")
            inserted += 1
        return inserted

    filled = runner.run(
        context(TENANT_A, USER_A),
        fill_real_database_quota,
        operation_kind="STEP28_REAL_DATABASE_QUOTA_FILL",
    )
    if filled != MAXIMUM_CANDIDATES_PER_SLOT - 3:
        raise ValidationFailure("STEP28_REAL_DATABASE_QUOTA_FILL_INCOMPLETE")
    _progress("CANDIDATE_SCENARIO_COUNT_QUOTA_FILLED_TO_127")

    race_winner = _candidate_envelope(
        slot,
        source=ActorType.KNOWLEDGE_KERNEL,
        run_suffix="quota-race-winner",
        idempotency_key="step28-quota-race-winner",
        proposed_correction="Deterministic concurrent quota winner.",
        at_offset=MAXIMUM_CANDIDATES_PER_SLOT + 10,
    )
    race_loser = _candidate_envelope(
        slot,
        source=ActorType.KNOWLEDGE_KERNEL,
        run_suffix="quota-race-loser",
        idempotency_key="step28-quota-race-loser",
        proposed_correction="Concurrent quota overflow must fail closed.",
        at_offset=MAXIMUM_CANDIDATES_PER_SLOT + 11,
    )
    winner_inserted = threading.Event()
    loser_transaction_started = threading.Event()
    allow_winner_commit = threading.Event()

    def insert_race_winner() -> tuple[CorrectionCandidateEnvelope, bool]:
        def work(transaction: object) -> tuple[CorrectionCandidateEnvelope, bool]:
            result = candidate_repository.insert_candidate(transaction, race_winner)
            winner_inserted.set()
            if not allow_winner_commit.wait(timeout=30):
                raise ValidationFailure("STEP28_QUOTA_RACE_RELEASE_TIMEOUT")
            return result

        return runner.run(
            context(TENANT_A, USER_A),
            work,
            operation_kind="STEP28_DATABASE_QUOTA_RACE_WINNER",
        )

    def insert_race_loser() -> tuple[CorrectionCandidateEnvelope, bool]:
        if not winner_inserted.wait(timeout=30):
            raise ValidationFailure("STEP28_QUOTA_RACE_WINNER_TIMEOUT")

        def work(transaction: object) -> tuple[CorrectionCandidateEnvelope, bool]:
            loser_transaction_started.set()
            return candidate_repository.insert_candidate(transaction, race_loser)

        return runner.run(
            context(TENANT_A, USER_A),
            work,
            operation_kind="STEP28_DATABASE_QUOTA_RACE_LOSER",
        )

    race_loser_rejected = False
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        winner_future = executor.submit(insert_race_winner)
        if not winner_inserted.wait(timeout=30):
            allow_winner_commit.set()
            raise ValidationFailure("STEP28_QUOTA_RACE_WINNER_TIMEOUT")
        loser_future = executor.submit(insert_race_loser)
        if not loser_transaction_started.wait(timeout=30):
            allow_winner_commit.set()
            raise ValidationFailure("STEP28_QUOTA_RACE_LOSER_TIMEOUT")
        allow_winner_commit.set()
        winner_result, winner_created = winner_future.result(timeout=60)
        try:
            loser_future.result(timeout=60)
        except Exception as error:
            race_loser_rejected = getattr(error, "sqlstate", None) == "23514"
    if (
        not winner_created
        or winner_result.candidate_id != race_winner.candidate_id
        or not race_loser_rejected
    ):
        raise ValidationFailure("STEP28_CONCURRENT_QUOTA_NOT_ENFORCED")
    _progress("CANDIDATE_SCENARIO_CONCURRENT_QUOTA_COMPLETE")

    quota_input = _candidate_envelope(
        slot,
        source=ActorType.KNOWLEDGE_KERNEL,
        run_suffix="quota-probe",
        idempotency_key="step28-quota-probe",
        proposed_correction="Quota boundary must reject this candidate.",
        at_offset=MAXIMUM_CANDIDATES_PER_SLOT + 21,
    )
    quota_rejected = False
    try:
        runner.run(
            context(TENANT_A, USER_A),
            lambda transaction: candidate_repository.insert_candidate(
                transaction,
                quota_input,
            ),
            operation_kind="STEP28_REAL_DATABASE_QUOTA_REJECTION",
        )
    except Exception as error:
        quota_rejected = getattr(error, "sqlstate", None) == "23514"
    replay_at_limit, replay_at_limit_created = runner.run(
        context(TENANT_A, USER_A),
        lambda transaction: candidate_repository.insert_candidate(
            transaction,
            kernel_input,
        ),
        operation_kind="STEP28_REAL_DATABASE_AT_LIMIT_REPLAY",
    )
    quota_row = runner.run(
        context(TENANT_A, USER_A),
        lambda transaction: transaction.fetch_one(
            "SELECT candidate_quota_epoch FROM memory_patch.personal_memory_spaces "
            "WHERE tenant_id = %s AND user_id = %s "
            "AND personal_memory_space_id = %s",
            (TENANT_A, USER_A, SLOT_A),
        ),
        operation_kind="STEP28_REAL_DATABASE_QUOTA_EPOCH_READ",
    )
    quota_usage = runner.run(
        context(TENANT_A, USER_A),
        lambda transaction: candidate_repository.candidate_usage(
            transaction,
            TENANT_A,
            USER_A,
            SLOT_A,
        ),
        operation_kind="STEP28_REAL_DATABASE_QUOTA_USAGE_READ",
    )
    if (
        not quota_rejected
        or replay_at_limit_created
        or replay_at_limit.candidate_id != kernel_input.candidate_id
        or quota_row is None
        or int(quota_row["candidate_quota_epoch"])
        != MAXIMUM_CANDIDATES_PER_SLOT
        or quota_usage[0] != MAXIMUM_CANDIDATES_PER_SLOT
        or candidate_count(TENANT_A, USER_A) != MAXIMUM_CANDIDATES_PER_SLOT
    ):
        raise ValidationFailure("STEP28_CANDIDATE_QUOTA_NOT_ENFORCED")
    _progress("CANDIDATE_SCENARIO_AT_LIMIT_REPLAY_COMPLETE")

    archived_probe = _candidate_envelope(
        slot,
        source=ActorType.KNOWLEDGE_KERNEL,
        run_suffix="archived-probe",
        idempotency_key="step28-archived-probe",
        proposed_correction="Archived targets must reject intake.",
        at_offset=17,
    )
    slot, _ = personal_service.transition_slot(
        _transition(
            slot,
            PersonalMemorySpaceState.ARCHIVED,
            key="step28-archive-target",
            at=FIXTURE_TIME + timedelta(seconds=400),
        )
    )
    _progress("CANDIDATE_SCENARIO_TARGET_ARCHIVED")
    archived_replay_stored, archived_replay_receipt = (
        candidate_service.submit_kernel_candidate(kernel_input)
    )
    if (
        archived_replay_stored.candidate_id != kernel_stored.candidate_id
        or archived_replay_receipt.disposition
        is not CorrectionCandidateIntakeDisposition.EXACT_REPLAY
        or archived_replay_receipt.receipt_hash != replay_receipt.receipt_hash
    ):
        raise ValidationFailure("STEP28_COMPLETED_REPLAY_DEPENDS_ON_SLOT_STATE")
    archived_denied = False
    try:
        candidate_service.submit_kernel_candidate(archived_probe)
    except CorrectionCandidateIntakeError as error:
        archived_denied = (
            error.reason_code is CorrectionCandidateReasonCode.TARGET_ARCHIVED
        )
    if not archived_denied:
        raise ValidationFailure("STEP28_ARCHIVED_TARGET_ACCEPTED")
    _progress("CANDIDATE_SCENARIO_ARCHIVED_NEGATIVES_COMPLETE")

    slot, _ = personal_service.transition_slot(
        _transition(
            slot,
            PersonalMemorySpaceState.CONFIGURED,
            key="step28-restore-target",
            at=FIXTURE_TIME + timedelta(seconds=401),
        )
    )
    _progress("CANDIDATE_SCENARIO_TARGET_RESTORED")
    delete_pending_probe = _candidate_envelope(
        slot,
        source=ActorType.KNOWLEDGE_KERNEL,
        run_suffix="delete-pending-probe",
        idempotency_key="step28-delete-pending-probe",
        proposed_correction="Delete-pending targets must reject intake.",
        at_offset=402,
    )
    slot, _ = personal_service.transition_slot(
        _transition(
            slot,
            PersonalMemorySpaceState.DELETED_PENDING,
            key="step28-delete-pending-target",
            at=FIXTURE_TIME + timedelta(seconds=403),
        )
    )
    _progress("CANDIDATE_SCENARIO_TARGET_DELETE_PENDING")
    delete_pending_denied = False
    try:
        candidate_service.submit_kernel_candidate(delete_pending_probe)
    except CorrectionCandidateIntakeError as error:
        delete_pending_denied = (
            error.reason_code
            is CorrectionCandidateReasonCode.TARGET_DELETE_PENDING
        )
    if not delete_pending_denied:
        raise ValidationFailure("STEP28_DELETE_PENDING_TARGET_ACCEPTED")

    final_candidates = candidate_service.list_owner_candidates(
        tenant_id=TENANT_A,
        owner_user_id=USER_A,
        personal_memory_space_id=SLOT_A,
    )
    if len(final_candidates) != MAXIMUM_CANDIDATES_PER_SLOT:
        raise ValidationFailure("STEP28_NEGATIVE_PATH_MUTATED_CANDIDATES")
    if any(
        value.submission.candidate.state is not CorrectionCandidateState.DETECTED
        for value in final_candidates
    ):
        raise ValidationFailure("STEP28_CANDIDATE_STATE_CEILING_BREACHED")

    return {
        "candidate_envelope_version": STEP28_SCHEMA_VERSION,
        "candidate_state": CorrectionCandidateState.DETECTED.value,
        "target_slot_contract_identity": {
            "personal_memory_space_id": SLOT_A,
            "initial_slot_hash": kernel_input.submission.target_slot_binding.slot_hash,
            "configuration_digest": (
                kernel_input.submission.target_slot_binding.configuration_digest
            ),
            "model_binding_id": (
                kernel_input.submission.target_slot_binding.model_binding_id
            ),
            "final_state": slot.state.value,
        },
        "source_matrix": {
            "knowledge_kernel": "PASS",
            "critic_prompt_loop": "PASS",
            "knowledge_hub": "NOT_REQUIRED_NO_CANONICAL_RUNTIME",
        },
        "candidate_hashes": sorted(
            value.envelope_hash for value in final_candidates
        ),
        "receipt_hashes": sorted(
            {
                kernel_receipt.receipt_hash,
                replay_receipt.receipt_hash,
                duplicate_receipt.receipt_hash,
                duplicate_replay_receipt.receipt_hash,
                critic_receipt.receipt_hash,
            }
        ),
        "idempotency_matrix": {
            "first_intake": "ACCEPTED",
            "exact_replay": "PASS",
            "exact_replay_after_slot_archive": "PASS",
            "exact_duplicate_replay": "PASS",
            "conflicting_replay": "REJECTED",
        },
        "dedup_matrix": {
            "exact_duplicate": "DETERMINISTIC",
            "duplicate_receipt_binds_triggering_submission": True,
            "semantic_model_merge": False,
            "persisted_candidate_count": len(final_candidates),
            "original_provenance_preserved": True,
        },
        "owner_isolation": {
            "owner_visible_rows": owner_count,
            "cross_user_visible_rows": cross_user_count,
            "cross_tenant_visible_rows": cross_tenant_count,
            "cross_user_submit": "DENIED",
            "cross_tenant_submit": "DENIED",
        },
        "slot_state_negatives": {
            "archived_target": "DENIED",
            "delete_pending_target": "DENIED",
            "slot_reactivation_by_candidate": False,
        },
        "quota": {
            "configured_candidate_limit": MAXIMUM_CANDIDATES_PER_SLOT,
            "configured_candidate_byte_limit": (
                load_correction_candidate_intake_policy()
                .maximum_candidate_bytes_per_slot
            ),
            "database_candidate_bytes": quota_usage[1],
            "database_candidate_count": quota_usage[0],
            "database_quota_epoch": int(quota_row["candidate_quota_epoch"]),
            "at_limit_result": "REJECTED",
            "at_limit_exact_replay": "ACCEPTED_WITHOUT_NEW_ROW",
            "byte_limit_result": "REJECTED",
            "concurrent_boundary": "ONE_ACCEPTED_ONE_REJECTED",
            "fixture_mode": "REAL_DATABASE_BEFORE_INSERT_TRIGGER",
            "cross_user_usage_counted": False,
            "quota_policy_digest": policy.policy_digest,
        },
        "real_critic_fixture": False,
        "synthetic_critic_fixture": True,
    }


def validate(args: argparse.Namespace) -> Mapping[str, Any]:
    source_binary = step27._source_binary(args)
    source_identity = migrations.verify_binary_identity(source_binary)
    if source_identity["binary_sha256"] != EXPECTED_COCKROACH_SHA256:
        raise ValidationFailure("STEP28_COCKROACH_BINARY_DIGEST_MISMATCH")

    runtime: migrations.LocalRuntime | None = None
    root: step18._Step18HttpSqlClient | None = None
    database: str | None = None
    role: str | None = None
    cleanup: Mapping[str, Any] = {}
    service_result: Mapping[str, Any] | None = None
    migration_result: Mapping[str, Any] | None = None
    replay_result: Mapping[str, Any] | None = None
    rls_result: Mapping[str, Any] | None = None
    scope_derivation_result: Mapping[str, Any] | None = None
    primary_error: BaseException | None = None
    cleanup_errors: list[str] = []

    with tempfile.TemporaryDirectory(prefix="mp-step28-binary-", dir="/tmp") as temp:
        local_binary = Path(temp) / "cockroach"
        shutil.copy2(source_binary, local_binary)
        local_identity = migrations.verify_binary_identity(local_binary)
        if local_identity["binary_sha256"] != EXPECTED_COCKROACH_SHA256:
            raise ValidationFailure("STEP28_COPIED_BINARY_DIGEST_MISMATCH")
        run_id = "mp_step28_" + uuid.uuid4().hex[:12]
        runtime = migrations.LocalRuntime(local_binary, run_id)
        try:
            _progress("START_DISPOSABLE_COCKROACHDB")
            root = step18._start_disposable_runtime(runtime)
            if root.sql_port is None:
                raise ValidationFailure("STEP28_SQL_PORT_MISSING")
            migration_client = _ProgressMigrationClient(root)
            database = run_id + "_db"
            migrations.create_database(root, database)
            _progress("APPLY_MIGRATIONS")
            migration_result = migrations.apply_migrations(
                migration_client,
                database,
                timeout=300,
            )
            _progress("REPLAY_MIGRATIONS")
            replay_result = migrations.apply_migrations(
                migration_client,
                database,
                timeout=300,
            )
            if (
                len(migration_result["applied"]) != 12
                or replay_result["applied"]
                or len(replay_result["skipped"]) != 12
            ):
                raise ValidationFailure("STEP28_MIGRATION_REPLAY_MISMATCH")
            scope_derivation_result = _scope_derivation_parity(root, database)
            root.execute(database, _seed_identity_sql(), timeout=120)
            role = "mp_s28_" + uuid.uuid4().hex[:16]
            step27._create_validation_role(root, role)
            rls_result = _rls_catalog(root, database)
            _progress("VALIDATE_CANDIDATE_BRIDGE")
            service_result = _validate_service(
                root=root,
                database=database,
                role=role,
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
        raise ValidationFailure("STEP28_" + "_".join(cleanup_errors))
    if not all(
        cleanup.get(field) is expected
        for field, expected in (
            ("pid_exited", True),
            ("ports_closed", True),
            ("temporary_store_removed", True),
            ("force_kill_used", False),
        )
    ):
        raise ValidationFailure("STEP28_RUNTIME_CLEANUP_INCOMPLETE")
    if None in (
        service_result,
        migration_result,
        replay_result,
        rls_result,
        scope_derivation_result,
    ):
        raise ValidationFailure("STEP28_VALIDATION_RESULT_INCOMPLETE")

    assert service_result is not None
    assert migration_result is not None
    assert replay_result is not None
    assert rls_result is not None
    assert scope_derivation_result is not None
    policy = load_correction_candidate_intake_policy()
    result: dict[str, Any] = {
        "step": 28,
        "schema_version": "step28-correction-candidate-bridge-validation-1a",
        "status": "PASS",
        "start_sha": START_SHA,
        **service_result,
        "database": {
            "version": migrations.PINNED_VERSION,
            "binary_sha256": EXPECTED_COCKROACH_SHA256,
            "migration": "0012_step28_correction_candidate_bridge.sql",
            "migration_count": len(migration_result["applied"]),
            "replay_skipped_count": len(replay_result["skipped"]),
            "rls": rls_result,
            "scope_derivation": scope_derivation_result,
        },
        "candidate_policy": {
            "policy_id": policy.policy_id,
            "policy_version": policy.policy_version,
            "policy_digest": policy.policy_digest,
            "maximum_state": policy.maximum_state.value,
            "maximum_candidates_per_submission": (
                policy.maximum_candidates_per_submission
            ),
            "maximum_scope_dimensions": policy.maximum_scope_dimensions,
        },
        "authority": {
            "model_authority": False,
            "critic_approval_authority": False,
            "kernel_approval_authority": False,
            "commit_authority": False,
            "activation_authority": False,
            "execution_authority": False,
            "canonical_evidence_promotion": False,
        },
        "step29_started": False,
        "patch_proposals": 0,
        "evidence_bound_transitions": 0,
        "validated_patch_transitions": 0,
        "awaiting_approval_transitions": 0,
        "later_step_boundaries": {
            "step29_started": False,
            "patch_proposals": 0,
            "evidence_bound_transitions": 0,
            "validated_patch_transitions": 0,
            "awaiting_approval_transitions": 0,
            "approvals": 0,
            "commits": 0,
            "patch_activations": 0,
            "active_patch_retrieval": 0,
            "shared_promotion": 0,
        },
        "external_agents": {
            "nooa_implementation": 0,
            "openshell_implementation": 0,
            "nvidia_implementation": 0,
        },
        "effect_bounds": {
            "provider_calls": 0,
            "model_calls": 0,
            "web_calls": 0,
            "aws_mutations": 0,
            "s3_mutations": 0,
            "approval_actions": 0,
            "commit_actions": 0,
            "activation_actions": 0,
            "retrieval_actions": 0,
            "execution_actions": 0,
        },
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
    args = _arguments()
    try:
        result = validate(args)
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
