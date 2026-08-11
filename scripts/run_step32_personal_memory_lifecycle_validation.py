#!/usr/bin/env python3
"""Controlled Step 32 lifecycle and private-to-shared review validation."""

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
import run_step31_active_patch_retrieval_validation as step31  # noqa: E402
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
from aioa_memory_kernel.persistence import IdempotencyService  # noqa: E402
from aioa_memory_kernel.security.credentials import CredentialPurpose  # noqa: E402
from aioa_memory_kernel.personal_memory import (  # noqa: E402
    STEP29_SCHEMA_VERSION,
    ActivePatchRetrievalCockroachRepository,
    ActivePatchRetrievalService,
    AdvancePersonalMemoryPatchToAwaitingApproval,
    BindPersonalMemoryPatchEvidence,
    CreatePersonalMemoryPatchProposal,
    PersonalMemoryLifecycle32CockroachRepository,
    PersonalMemoryLifecycle32Service,
    Step32ActorType,
    Step32ReasonCode,
    TransitionSlotCommand,
    ValidatePersonalMemoryPatchProposal,
    assess_shared_promotion_privacy,
    build_deletion_request,
    build_lifecycle_export_request,
    build_personal_memory_activation_request,
    build_personal_memory_approval_request,
    build_personal_memory_commit_request,
    build_revocation_request,
    build_shared_promotion_consent,
    build_shared_promotion_request,
    build_supersession_request,
    create_shared_promotion_proposal,
)
from aioa_memory_kernel.personal_memory.retrieval import (  # noqa: E402
    CanonicalEvidenceCompatibility,
)


START_SHA = "bf6cde9de87ab727f1bd5e48e2abfc7e8e3b85b5"
EXPECTED_COCKROACH_SHA256 = step27.EXPECTED_COCKROACH_SHA256
DEFAULT_EXTERNAL_ENV = step27.DEFAULT_EXTERNAL_ENV
OTHER_USER = step29.OTHER_USER
OTHER_TENANT = step29.OTHER_TENANT
OTHER_TENANT_USER = step29.OTHER_TENANT_USER


class ValidationFailure(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__("Step 32 controlled validation failed")
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
        self, database: str, sql: str, *, timeout: float = 300
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
        try:
            result = self._delegate.execute(database, sql, timeout=timeout)
        except BaseException as error:
            _failure_progress(
                "MIGRATION_" + (current or "UNKNOWN").upper(), error
            )
            raise
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
        canonical_json({"stage": stage, "status": "RUNNING", "step": 32}),
        file=sys.stderr,
        flush=True,
    )


def _failure_progress(stage: str, error: BaseException) -> None:
    """Emit bounded sanitized diagnostics from the owned disposable runtime."""

    current: BaseException | None = error
    chain: list[str] = []
    seen: set[int] = set()
    sqlstate = getattr(error, "sqlstate", None)
    while current is not None and id(current) not in seen and len(chain) < 5:
        seen.add(id(current))
        candidate_sqlstate = getattr(current, "sqlstate", None)
        if isinstance(candidate_sqlstate, str):
            sqlstate = candidate_sqlstate
        detail = " ".join(str(current).split())[:384]
        chain.append(type(current).__name__ + (":" + detail if detail else ""))
        current = current.__cause__
    print(
        canonical_json(
            {
                "detail": chain,
                "sanitized_code": getattr(error, "sanitized_code", None),
                "sqlstate": sqlstate,
                "stage": stage,
                "status": "FAILED",
                "step": 32,
            }
        ),
        file=sys.stderr,
        flush=True,
    )


def _advance_text(
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
    text: str,
):
    _progress("STEP32_FIXTURE_" + suffix.upper().replace("-", "_") + "_START")
    now = request.temporal_result.trusted_now + timedelta(seconds=offset)
    envelope, answer, snapshot, links = step29._pipeline_candidate(
        request,
        slot,
        suffix=f"step32-{suffix}",
        text=text,
    )
    candidate_service.submit_kernel_candidate(envelope)
    create = CreatePersonalMemoryPatchProposal(
        schema_version=STEP29_SCHEMA_VERSION,
        tenant_id=slot.tenant_id,
        owner_user_id=slot.owner_user_id,
        personal_memory_space_id=slot.personal_memory_space_id,
        candidate_id=envelope.candidate_id,
        candidate_envelope_hash=envelope.envelope_hash,
        expected_target_binding_hash=(
            envelope.submission.target_slot_binding.target_binding_hash
        ),
        idempotency_key=f"step32-{suffix}-create-proposal",
        requested_at=now + timedelta(seconds=3),
    )
    proposed, _ = proposal_service.create_proposal(envelope, create)
    evidence = {
        "bundles": tuple(item.bundle for item in request.step20_outcomes),
        "temporal_result": request.temporal_result,
        "claim_links": links,
        "claim_assessments": snapshot.ordered_candidate_assessments,
        "correction_packet": request.correction_packet,
        "verified_answer": answer,
    }
    bind = step29._transition_command(
        proposed,
        BindPersonalMemoryPatchEvidence,
        key=f"step32-{suffix}-bind-evidence",
        at=now + timedelta(seconds=4),
    )
    bound, _ = proposal_service.bind_evidence(bind, **evidence)
    validate = step29._transition_command(
        bound,
        ValidatePersonalMemoryPatchProposal,
        key=f"step32-{suffix}-validate",
        at=now + timedelta(seconds=5),
    )
    validated, receipt, _ = proposal_service.validate_proposal(
        validate, **evidence
    )
    advance = step29._transition_command(
        validated,
        AdvancePersonalMemoryPatchToAwaitingApproval,
        key=f"step32-{suffix}-await",
        at=now + timedelta(seconds=6),
        receipt_hash=receipt.receipt_hash,
    )
    awaiting, _ = proposal_service.advance_to_awaiting_approval(advance)
    approval_request = build_personal_memory_approval_request(
        awaiting,
        approval_nonce=f"step32-{suffix}-human-approval",
        requested_at=awaiting.updated_at + timedelta(seconds=1),
    )
    clock.current = approval_request.requested_at
    approved, _, _ = approval_service.approve(
        approval_request,
        authenticated_actor_user_id=slot.owner_user_id,
    )
    commit_request = build_personal_memory_commit_request(
        approved,
        commit_idempotency_key=f"step32-{suffix}-technical-commit",
        requested_at=approved.updated_at + timedelta(seconds=1),
    )
    clock.current = commit_request.requested_at
    committed, _, _ = commit_service.commit(commit_request)
    activation_request = build_personal_memory_activation_request(
        committed,
        activation_idempotency_key=f"step32-{suffix}-activation",
        requested_at=committed.updated_at + timedelta(seconds=1),
    )
    clock.current = activation_request.requested_at
    active, _, _ = activation_service.activate(activation_request)
    if active.state is not PatchState.ACTIVE:
        raise ValidationFailure("STEP32_ACTIVE_FIXTURE_FAILED")
    _progress("STEP32_FIXTURE_" + suffix.upper().replace("-", "_") + "_ACTIVE")
    return active


def _retrieve(
    service,
    slot,
    binding,
    *,
    query: str,
    route_value=None,
    temporal_value=None,
):
    if (route_value is None) is not (temporal_value is None):
        raise ValidationFailure("STEP32_RETRIEVAL_EVIDENCE_INPUT_INCOMPLETE")
    if route_value is None:
        route_value, temporal_value = later_pipeline()
    request = step31.build_active_patch_retrieval_request(
        route=route_value,
        temporal_result=temporal_value,
        personal_memory_space_id=slot.personal_memory_space_id,
        model_identity=provider(binding),
        query_text=query,
    )
    return service.retrieve(
        request,
        route=route_value,
        temporal_result=temporal_value,
    )


def _visible_count(runner, context, table: str) -> int:
    allowed = {
        "personal_memory_patch_supersessions",
        "personal_memory_patch_revocations",
        "personal_memory_exports",
        "personal_memory_deletions",
        "shared_memory_promotion_proposals",
    }
    if table not in allowed:
        raise ValidationFailure("STEP32_RLS_TABLE_NOT_ALLOWLISTED")
    row = runner.run(
        context,
        lambda tx: tx.fetch_one(
            f"SELECT count(*) AS row_count FROM memory_patch.{table}",
        ),
        operation_kind="STEP32_RLS_READ_" + table.upper(),
    )
    return 0 if row is None else int(row["row_count"])


def _expected_rls_denial(error: BaseException) -> bool:
    return getattr(error, "sqlstate", None) in {"23514", "42501", "44000"}


def _validate_service(
    *,
    root,
    database: str,
    app_role: str,
    commit_role: str,
) -> Mapping[str, Any]:
    pipeline_request, _ = hat_lineage()
    if root.sql_port is None:
        raise ValidationFailure("STEP32_SQL_PORT_MISSING")
    app_runner = step30._runner(
        port=root.sql_port,
        database=database,
        role=app_role,
        credential_purpose=CredentialPurpose.APPLICATION_DATABASE,
        diagnostic=True,
    )
    commit_runner = step30._runner(
        port=root.sql_port,
        database=database,
        role=commit_role,
        credential_purpose=CredentialPurpose.PERSONAL_MEMORY_COMMIT_DATABASE,
        diagnostic=True,
    )
    now = pipeline_request.temporal_result.trusted_now
    clock = _TrustedClock(now + timedelta(hours=2))
    personal, candidates, proposals, approvals, commits, activations = (
        step31._lifecycle_services(
            request=pipeline_request,
            app_runner=app_runner,
            commit_runner=commit_runner,
            clock=clock,
        )
    )
    lifecycle = PersonalMemoryLifecycle32Service(
        app_runner, trusted_clock=clock
    )
    lifecycle_repository = PersonalMemoryLifecycle32CockroachRepository()
    retrieval = ActivePatchRetrievalService(app_runner)

    supersession_slot, supersession_bindings = step31._create_slot(
        personal,
        pipeline_request,
        slot_id="personal-slot-step32-supersession",
        suffix="step32-supersession",
        offset=0,
    )
    original_text = pipeline_request.correction_packet.ordered_claims[0].exact_claim_text
    old_state = _advance_text(
        request=pipeline_request,
        slot=supersession_slot,
        candidate_service=candidates,
        proposal_service=proposals,
        approval_service=approvals,
        commit_service=commits,
        activation_service=activations,
        clock=clock,
        suffix="supersession-old",
        offset=0,
        text=original_text,
    )
    successor_text = "Die Vorschrift ist seit dem Folgetag aufgehoben."
    successor_request, _ = hat_lineage(
        draft_v1_text=successor_text,
        draft_v2_text=successor_text,
        contents=(successor_text,),
    )
    new_state = _advance_text(
        request=successor_request,
        slot=supersession_slot,
        candidate_service=candidates,
        proposal_service=proposals,
        approval_service=approvals,
        commit_service=commits,
        activation_service=activations,
        clock=clock,
        suffix="supersession-new",
        offset=10,
        text=successor_text,
    )
    _progress("STEP32_SUPERSESSION_FIXTURES_READY")

    privacy = assess_shared_promotion_privacy(
        new_state.committed_patch.patch_statement,
        private_identifiers=(supersession_slot.owner_user_id,),
    )
    consent = build_shared_promotion_consent(
        new_state,
        privacy,
        target_hat_id="shared-review-hat-step32",
        consent_nonce="step32-separate-owner-share-consent",
        authenticated_owner_user_id=supersession_slot.owner_user_id,
        consented_at=new_state.updated_at + timedelta(seconds=1),
    )
    promotion_request = build_shared_promotion_request(
        new_state,
        target_hat_id="shared-review-hat-step32",
        promotion_purpose="Bounded candidate for independent shared review",
        promotion_scope=new_state.committed_patch.patch_scope,
        deidentification=privacy,
        owner_consent=consent,
        canonical_evidence_compatibility=CanonicalEvidenceCompatibility.MATCH,
        reason_codes=(
            Step32ReasonCode.CANONICAL_EVIDENCE_AUTHORITY_NOT_GRANTED,
            Step32ReasonCode.SHARED_PROMOTION_PROPOSED,
        ),
        requested_at=consent.consented_at,
        idempotency_key="step32-shared-promotion",
    )
    clock.current = promotion_request.requested_at

    persisted = app_runner.run(
        step29._context(
            supersession_slot.tenant_id, supersession_slot.owner_user_id
        ),
        lambda transaction: lifecycle_repository.get_step30_state(
            transaction,
            promotion_request.tenant_id,
            promotion_request.owner_user_id,
            promotion_request.source_proposal_id,
        ),
        operation_kind="STEP32_SHARED_PROMOTION_STATE_PREFLIGHT",
    )
    if persisted is None:
        raise ValidationFailure("STEP32_PROMOTION_SOURCE_STATE_NOT_VISIBLE")
    if persisted.state_hash != promotion_request.source_state_hash:
        raise ValidationFailure("STEP32_PROMOTION_SOURCE_STATE_HASH_CHANGED")
    create_shared_promotion_proposal(
        promotion_request,
        persisted,
        authenticated_owner_user_id=supersession_slot.owner_user_id,
    )
    locked = app_runner.run(
        step29._context(
            supersession_slot.tenant_id, supersession_slot.owner_user_id
        ),
        lambda transaction: lifecycle_repository.lock_active_patch(
            transaction,
            tenant_id=promotion_request.tenant_id,
            owner_user_id=promotion_request.owner_user_id,
            personal_memory_space_id=promotion_request.personal_memory_space_id,
            patch_id=promotion_request.source_patch_id,
            patch_hash=promotion_request.source_patch_hash,
        ),
        operation_kind="STEP32_SHARED_PROMOTION_PATCH_PREFLIGHT",
    )
    if locked is None:
        raise ValidationFailure("STEP32_PROMOTION_ACTIVE_PATCH_NOT_VISIBLE")
    _progress("STEP32_SHARED_PROMOTION_PREFLIGHT_COMPLETE")
    promotion, promotion_replayed = lifecycle.propose_shared(
        promotion_request,
        authenticated_owner_user_id=supersession_slot.owner_user_id,
    )
    replayed_promotion, replay_flag = lifecycle.propose_shared(
        promotion_request,
        authenticated_owner_user_id=supersession_slot.owner_user_id,
    )
    if (
        promotion_replayed
        or not replay_flag
        or replayed_promotion != promotion
        or not promotion.review_required
        or promotion.shared_active
        or promotion.source_registry_published
        or promotion.canonical_evidence
    ):
        raise ValidationFailure("STEP32_SHARED_PROMOTION_BOUNDARY_FAILED")
    _progress("STEP32_SHARED_PROMOTION_PROPOSAL_COMPLETE")

    effective_at = promotion.created_at + timedelta(seconds=1)
    supersession_request = build_supersession_request(
        old_state,
        new_state,
        reason_codes=(Step32ReasonCode.SUPERSESSION_CREATED,),
        effective_at=effective_at,
        idempotency_key="step32-supersede-old-with-new",
    )
    clock.current = effective_at
    supersession, replayed = lifecycle.supersede(
        supersession_request,
        authenticated_owner_user_id=supersession_slot.owner_user_id,
    )
    same_supersession, exact_replay = lifecycle.supersede(
        supersession_request,
        authenticated_owner_user_id=supersession_slot.owner_user_id,
    )
    changed_supersession_denied = False
    try:
        lifecycle.supersede(
            replace(supersession_request, new_patch_hash="0" * 64),
            authenticated_owner_user_id=supersession_slot.owner_user_id,
        )
    except Exception:
        changed_supersession_denied = True
    if (
        replayed
        or not exact_replay
        or same_supersession != supersession
        or not changed_supersession_denied
    ):
        raise ValidationFailure("STEP32_SUPERSESSION_REPLAY_FAILED")

    current_result, _ = _retrieve(
        retrieval,
        supersession_slot,
        supersession_bindings[0],
        query="Step 32 current supersession selection",
        route_value=successor_request.route,
        temporal_value=successor_request.temporal_result,
    )
    current_ids = tuple(item.patch_id for item in current_result.eligible_patches)
    if current_ids != (new_state.committed_patch.patch_id,):
        raise ValidationFailure("STEP32_SUPERSEDED_PATCH_RETRIEVED_CURRENT")
    historical = app_runner.run(
        step29._context(
            supersession_slot.tenant_id, supersession_slot.owner_user_id
        ),
        lambda tx: ActivePatchRetrievalCockroachRepository.list_active_patch_candidates(
            tx,
            tenant_id=supersession_slot.tenant_id,
            owner_user_id=supersession_slot.owner_user_id,
            personal_memory_space_id=supersession_slot.personal_memory_space_id,
            hat_scope_id=supersession_slot.hat_scope_id,
            limit=3,
            knowledge_as_of=effective_at - timedelta(microseconds=1),
        ),
        operation_kind="STEP32_HISTORICAL_SUPERSESSION_READ",
    )
    if tuple(
        item.lifecycle_state.committed_patch.patch_id for item in historical
    ) != (old_state.committed_patch.patch_id,):
        raise ValidationFailure("STEP32_HISTORICAL_SUPERSESSION_FAILED")
    _progress("STEP32_SUPERSESSION_COMPLETE")

    export_request = build_lifecycle_export_request(
        supersession_slot,
        requested_at=effective_at + timedelta(seconds=1),
        idempotency_key="step32-owner-export",
    )
    clock.current = export_request.requested_at
    export_bundle, export_replayed = lifecycle.export(
        export_request,
        authenticated_owner_user_id=supersession_slot.owner_user_id,
    )
    same_export, export_exact_replay = lifecycle.export(
        export_request,
        authenticated_owner_user_id=supersession_slot.owner_user_id,
    )
    export_cross_user_denied = False
    try:
        lifecycle.export(
            export_request, authenticated_owner_user_id=OTHER_USER
        )
    except Exception:
        export_cross_user_denied = True
    exported_types = {record.record_type for record in export_bundle.records}
    if (
        export_replayed
        or not export_exact_replay
        or same_export != export_bundle
        or not export_cross_user_denied
        or not {
            "PERSONAL_MEMORY_SLOT", "MEMORY_PATCH", "SUPERSESSION",
            "SHARED_PROMOTION_PROPOSAL",
        }.issubset(exported_types)
        or export_bundle.canonical_evidence
        or export_bundle.shared_promotion
    ):
        raise ValidationFailure("STEP32_OWNER_EXPORT_FAILED")
    _progress("STEP32_OWNER_EXPORT_COMPLETE")

    revocation_slot, revocation_bindings = step31._create_slot(
        personal,
        pipeline_request,
        slot_id="personal-slot-step32-revocation",
        suffix="step32-revocation",
        offset=10,
    )
    revocation_text = "Die Vorschrift ist endgültig aufgehoben."
    revocation_pipeline_request, _ = hat_lineage(
        draft_v1_text=revocation_text,
        draft_v2_text=revocation_text,
        contents=(revocation_text,),
    )
    revocation_state = _advance_text(
        request=revocation_pipeline_request,
        slot=revocation_slot,
        candidate_service=candidates,
        proposal_service=proposals,
        approval_service=approvals,
        commit_service=commits,
        activation_service=activations,
        clock=clock,
        suffix="revocation",
        offset=20,
        text=revocation_text,
    )
    revocation_request = build_revocation_request(
        revocation_state,
        reason_codes=(Step32ReasonCode.REVOCATION_CREATED,),
        effective_at=revocation_state.updated_at + timedelta(seconds=1),
        idempotency_key="step32-owner-revocation",
    )
    clock.current = revocation_request.effective_at
    revocation, revocation_replayed = lifecycle.revoke(
        revocation_request,
        actor_type=Step32ActorType.HUMAN_OWNER,
        authenticated_actor_id=revocation_slot.owner_user_id,
    )
    same_revocation, revocation_exact_replay = lifecycle.revoke(
        revocation_request,
        actor_type=Step32ActorType.HUMAN_OWNER,
        authenticated_actor_id=revocation_slot.owner_user_id,
    )
    revoked_result, _ = _retrieve(
        retrieval,
        revocation_slot,
        revocation_bindings[0],
        query="Step 32 revoked patch suppression",
        route_value=revocation_pipeline_request.route,
        temporal_value=revocation_pipeline_request.temporal_result,
    )
    if (
        revocation_replayed
        or not revocation_exact_replay
        or same_revocation != revocation
        or revoked_result.eligible_patches
        or revocation.deletion_performed
    ):
        raise ValidationFailure("STEP32_REVOCATION_FAILED")
    _progress("STEP32_REVOCATION_COMPLETE")

    deletion_slot, deletion_bindings = step31._create_slot(
        personal,
        pipeline_request,
        slot_id="personal-slot-step32-deletion",
        suffix="step32-deletion",
        offset=20,
    )
    deletion_text = "Die Vorschrift ist am Stichtag aufgehoben."
    deletion_pipeline_request, _ = hat_lineage(
        draft_v1_text=deletion_text,
        draft_v2_text=deletion_text,
        contents=(deletion_text,),
    )
    deletion_state = _advance_text(
        request=deletion_pipeline_request,
        slot=deletion_slot,
        candidate_service=candidates,
        proposal_service=proposals,
        approval_service=approvals,
        commit_service=commits,
        activation_service=activations,
        clock=clock,
        suffix="deletion",
        offset=30,
        text=deletion_text,
    )
    pending_at = deletion_state.updated_at + timedelta(seconds=1)
    deletion_slot, _ = personal.transition_slot(
        TransitionSlotCommand(
            schema_version=deletion_slot.schema_version,
            tenant_id=deletion_slot.tenant_id,
            owner_user_id=deletion_slot.owner_user_id,
            personal_memory_space_id=deletion_slot.personal_memory_space_id,
            target_state=PersonalMemorySpaceState.DELETED_PENDING,
            expected_state_version=deletion_slot.state_version,
            expected_configuration_version=deletion_slot.configuration_version,
            idempotency_key="step32-delete-request-slot",
            requested_at=pending_at,
            actor=step31.PersonalMemoryMutationActor.OWNER_CONFIGURATION,
        )
    )
    deletion_request = build_deletion_request(
        deletion_state,
        deletion_slot,
        requested_at=pending_at,
        idempotency_key="step32-complete-logical-delete",
    )
    clock.current = deletion_request.requested_at
    deletion, deleted_slot, deletion_replayed = lifecycle.delete(
        deletion_request,
        authenticated_owner_user_id=deletion_slot.owner_user_id,
    )
    same_deletion, same_deleted_slot, deletion_exact_replay = lifecycle.delete(
        deletion_request,
        authenticated_owner_user_id=deletion_slot.owner_user_id,
    )
    deleted_result, _ = _retrieve(
        retrieval,
        deleted_slot,
        deletion_bindings[0],
        query="Step 32 deleted patch suppression",
        route_value=deletion_pipeline_request.route,
        temporal_value=deletion_pipeline_request.temporal_result,
    )
    if (
        deletion_replayed
        or not deletion_exact_replay
        or same_deletion != deletion
        or same_deleted_slot != deleted_slot
        or deleted_slot.state is not PersonalMemorySpaceState.DELETED
        or deleted_result.eligible_patches
        or deletion.physical_delete
        or deletion.revoked
        or deletion.shared_artifacts_mutated
    ):
        raise ValidationFailure("STEP32_LOGICAL_DELETION_FAILED")
    _progress("STEP32_LOGICAL_DELETION_COMPLETE")

    owner_context = step29._context(
        supersession_slot.tenant_id, supersession_slot.owner_user_id
    )
    user_b_context = step29._context(supersession_slot.tenant_id, OTHER_USER)
    tenant_b_context = step29._context(OTHER_TENANT, OTHER_TENANT_USER)
    table_names = (
        "personal_memory_patch_supersessions",
        "personal_memory_patch_revocations",
        "personal_memory_exports",
        "personal_memory_deletions",
        "shared_memory_promotion_proposals",
    )
    owner_counts = {
        table: _visible_count(app_runner, owner_context, table)
        for table in table_names
    }
    cross_user_counts = {
        table: _visible_count(app_runner, user_b_context, table)
        for table in table_names
    }
    cross_tenant_counts = {
        table: _visible_count(app_runner, tenant_b_context, table)
        for table in table_names
    }
    if any(value < 1 for value in owner_counts.values()) or any(
        value != 0
        for value in (*cross_user_counts.values(), *cross_tenant_counts.values())
    ):
        raise ValidationFailure("STEP32_OWNER_RLS_READ_FAILED")

    probe_consent = build_shared_promotion_consent(
        new_state,
        privacy,
        target_hat_id="shared-review-hat-step32",
        consent_nonce="step32-cross-scope-consent",
        authenticated_owner_user_id=supersession_slot.owner_user_id,
        consented_at=effective_at + timedelta(seconds=2),
    )
    probe_request = build_shared_promotion_request(
        new_state,
        target_hat_id="shared-review-hat-step32",
        promotion_purpose="Cross-scope RLS rejection probe",
        promotion_scope=new_state.committed_patch.patch_scope,
        deidentification=privacy,
        owner_consent=probe_consent,
        canonical_evidence_compatibility=CanonicalEvidenceCompatibility.MATCH,
        reason_codes=(Step32ReasonCode.SHARED_PROMOTION_PROPOSED,),
        requested_at=probe_consent.consented_at,
        idempotency_key="step32-cross-scope-promotion",
    )
    probe = create_shared_promotion_proposal(
        probe_request,
        new_state,
        authenticated_owner_user_id=supersession_slot.owner_user_id,
    )

    def denied_insert(context) -> bool:
        try:
            app_runner.run(
                context,
                lambda tx: lifecycle_repository.persist_promotion(tx, probe),
                operation_kind="STEP32_CROSS_SCOPE_INSERT_PROBE",
            )
        except Exception as error:
            if _expected_rls_denial(error):
                return True
            raise
        return False

    if not denied_insert(user_b_context) or not denied_insert(tenant_b_context):
        raise ValidationFailure("STEP32_OWNER_RLS_INSERT_ALLOWED")
    if _visible_count(
        app_runner, owner_context, "shared_memory_promotion_proposals"
    ) != owner_counts["shared_memory_promotion_proposals"]:
        raise ValidationFailure("STEP32_CROSS_SCOPE_INSERT_MUTATED")
    commit_helper_fk_visible_rows = {
        table: _visible_count(commit_runner, owner_context, table)
        for table in (
            "personal_memory_patch_supersessions",
            "personal_memory_patch_revocations",
            "personal_memory_exports",
            "personal_memory_deletions",
            "shared_memory_promotion_proposals",
        )
    }
    if any(commit_helper_fk_visible_rows.values()):
        raise ValidationFailure("STEP32_COMMIT_HELPER_LIFECYCLE_READ_ALLOWED")
    _progress("STEP32_OWNER_RLS_NEGATIVES_COMPLETE")

    return {
        "deletion": {
            "deleted_patch_not_retrieved": True,
            "logical_delete": True,
            "patch_hash": deletion.patch_hash,
            "physical_delete": False,
            "replay": "EXACT_REPLAY",
            "request_hash": deletion_request.request_hash,
            "result_hash": deletion.result_hash,
            "shared_artifacts_mutated": False,
            "tombstone_hash": deletion.tombstone_hash,
        },
        "export": {
            "bundle_hash": export_bundle.bundle_hash,
            "deterministic": True,
            "owner_isolation": "PASS",
            "record_count": len(export_bundle.records),
            "request_hash": export_request.request_hash,
        },
        "owner_isolation": {
            "cross_tenant_insert": "DENIED",
            "cross_tenant_visible_rows": cross_tenant_counts,
            "cross_user_export": "DENIED",
            "cross_user_insert": "DENIED",
            "cross_user_visible_rows": cross_user_counts,
            "commit_helper_fk_visible_rows": commit_helper_fk_visible_rows,
            "owner_visible_rows": owner_counts,
        },
        "revocation": {
            "content_preserved": True,
            "patch_hash": revocation.patch_hash,
            "receipt_hash": revocation.revocation_hash,
            "replay": "EXACT_REPLAY",
            "revocation_is_deletion": False,
            "revoked_patch_not_retrieved": True,
        },
        "shared_promotion": {
            "candidate_shared_statement_hash": promotion.candidate_shared_statement_sha256,
            "canonical_conflict_result": promotion.canonical_evidence_compatibility.value,
            "canonical_evidence": False,
            "deidentification_policy_digest": promotion.deidentification.policy_digest,
            "owner_consent_hash": promotion.owner_consent_hash,
            "privacy_result": promotion.deidentification.decision.value,
            "proposal_hash": promotion.proposal_hash,
            "review_required": True,
            "shared_active": False,
            "source_patch_hash": promotion.source_patch_hash,
            "source_registry_published": False,
        },
        "supersession": {
            "current_patch_ids": current_ids,
            "historical_old_patch_applicable": True,
            "new_patch_hash": supersession.new_patch_hash,
            "old_patch_hash": supersession.old_patch_hash,
            "old_patch_immutable": True,
            "receipt_hash": supersession.supersession_hash,
            "replay": "EXACT_REPLAY",
        },
    }


def validate(args: argparse.Namespace) -> Mapping[str, Any]:
    source_binary = step27._source_binary(args)
    identity = migrations.verify_binary_identity(source_binary)
    if identity["binary_sha256"] != EXPECTED_COCKROACH_SHA256:
        raise ValidationFailure("STEP32_COCKROACH_BINARY_DIGEST_MISMATCH")
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
    source_registry_before = None
    source_registry_after = None

    with tempfile.TemporaryDirectory(prefix="mp-step32-binary-", dir="/tmp") as temp:
        local_binary = Path(temp) / "cockroach"
        shutil.copy2(source_binary, local_binary)
        if (
            migrations.verify_binary_identity(local_binary)["binary_sha256"]
            != EXPECTED_COCKROACH_SHA256
        ):
            raise ValidationFailure("STEP32_COPIED_BINARY_DIGEST_MISMATCH")
        run_id = "mp_step32_" + uuid.uuid4().hex[:12]
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
                client, database, timeout=300
            )
            _progress("REPLAY_MIGRATIONS")
            replay_result = migrations.apply_migrations(
                client, database, timeout=300
            )
            expected = len(migrations.load_migrations())
            if (
                len(migration_result["applied"]) != expected
                or replay_result["applied"]
                or len(replay_result["skipped"]) != expected
            ):
                raise ValidationFailure("STEP32_MIGRATION_REPLAY_MISMATCH")
            catalog_result = migrations.assert_step32_security_catalog(
                root, database
            )
            root.execute(
                database,
                step29._seed_identity_sql(
                    pipeline_request.route.tenant_id,
                    pipeline_request.route.user_id,
                    pipeline_request.temporal_result.trusted_now,
                ),
                timeout=120,
            )
            source_registry_before = int(
                migrations.parse_tsv(
                    root.execute(
                        database,
                        "SELECT count(*) AS row_count "
                        "FROM memory_patch.source_registry_entries",
                        timeout=60,
                    )
                )[0]["row_count"]
            )
            app_role = "mp_s32_app_" + uuid.uuid4().hex[:12]
            commit_role = "mp_s32_commit_" + uuid.uuid4().hex[:12]
            step27._create_validation_role(root, app_role)
            step30._create_commit_validation_role(root, commit_role)
            _progress("VALIDATE_STEP32_LIFECYCLE")
            service_result = _validate_service(
                root=root,
                database=database,
                app_role=app_role,
                commit_role=commit_role,
            )
            source_registry_after = int(
                migrations.parse_tsv(
                    root.execute(
                        database,
                        "SELECT count(*) AS row_count "
                        "FROM memory_patch.source_registry_entries",
                        timeout=60,
                    )
                )[0]["row_count"]
            )
            if source_registry_before != source_registry_after:
                raise ValidationFailure("STEP32_SOURCE_REGISTRY_MUTATED")
        except BaseException as error:
            _failure_progress("VALIDATE_STEP32_LIFECYCLE", error)
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
        raise ValidationFailure("STEP32_" + "_".join(cleanup_errors))
    if not all(
        cleanup.get(field) is expected
        for field, expected in (
            ("pid_exited", True),
            ("ports_closed", True),
            ("temporary_store_removed", True),
            ("force_kill_used", False),
        )
    ):
        raise ValidationFailure("STEP32_RUNTIME_CLEANUP_INCOMPLETE")
    if None in (
        service_result,
        migration_result,
        replay_result,
        catalog_result,
        source_registry_before,
        source_registry_after,
    ):
        raise ValidationFailure("STEP32_VALIDATION_RESULT_INCOMPLETE")

    result: dict[str, Any] = {
        "authority": {
            "execution_authority": False,
            "model_authority": False,
            "private_patch_canonical_evidence": False,
            "shared_promotion_canonical_evidence": False,
            "source_publication_authority": False,
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
            "migration_added": True,
            "migration_count": len(migration_result["applied"]),
            "migration_id": migrations.STEP32_MIGRATION_ID,
            "migration_sha256": migrations.STEP32_MIGRATION_SHA256,
            "replay_skipped_count": len(replay_result["skipped"]),
            "security_catalog": catalog_result,
            "version": migrations.PINNED_VERSION,
        },
        "effect_bounds": {
            "aws_mutations": 0,
            "external_execution_actions": 0,
            "global_audit_ledger": 0,
            "human_review_workspace": 0,
            "model_calls": 0,
            "personal_memory_ui": 0,
            "provider_calls": 0,
            "s3_mutations": 0,
            "source_registry_publications": source_registry_after - source_registry_before,
            "web_calls": 0,
        },
        "rls": {
            "force_rls": True,
            "owner_isolation": "PASS",
            "runtime_delete_grants": 0,
        },
        "schema_version": "step32-personal-memory-lifecycle-validation-1a",
        "start_sha": START_SHA,
        "status": "PASS",
        "step": 32,
        "step33_boundary": {
            "global_audit_ledger": 0,
            "human_review_workspace": 0,
            "personal_memory_ui": 0,
            "step33_started": False,
        },
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
        reason = (
            error.code
            if isinstance(error, ValidationFailure)
            else type(error).__name__
        )
        print(
            canonical_json({"reason": reason, "status": "FAILED"}),
            file=sys.stderr,
        )
        return 1
    print(canonical_json(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
