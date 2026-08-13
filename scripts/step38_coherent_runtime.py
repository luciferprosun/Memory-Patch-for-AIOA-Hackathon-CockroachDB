"""One-lineage Step 38 downstream proof on one owned CockroachDB database.

This is a validation adapter, not a production service and not a standalone
Step 38 runner.  A caller supplies one already-verified typed Step 17-26
lineage plus a later related query.  The adapter then exercises the real
Step 27-35 services on a single database without constructing a second
business lineage or calling a model/provider.
"""

from __future__ import annotations

import csv
import hashlib
import io
import shutil
import sys
import tempfile
import uuid
from collections.abc import Callable
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts"), str(ROOT)]

import run_cockroachdb_migrations as migrations  # noqa: E402
import run_step18_retrieval_validation as step18  # noqa: E402
import run_step27_personal_memory_validation as step27  # noqa: E402
import run_step29_personal_memory_patch_validation as step29  # noqa: E402
import run_step30_user_approval_commit_activation_validation as step30  # noqa: E402
import run_step31_active_patch_retrieval_validation as step31  # noqa: E402
import run_step34_human_review_validation as step34  # noqa: E402

from aioa_memory_kernel.audit_ledger import (  # noqa: E402
    AuditActorType,
    AuditEventDraft,
    AuditEventType,
    AuditExportRequest,
    AuditLedgerService,
    AuditReasonCode,
    AuditRedactionProfile,
    AuditSubjectType,
    activation_receipt_event,
    approval_receipt_event,
    commit_receipt_event,
)
from aioa_memory_kernel.claims import ClaimEvidenceRelation  # noqa: E402
from aioa_memory_kernel.contracts.correction import CorrectionCandidate  # noqa: E402
from aioa_memory_kernel.contracts.enums import (  # noqa: E402
    ActorType,
    AnswerStatus,
    CorrectionCandidateState,
    EvidenceStatus,
    PatchState,
)
from aioa_memory_kernel.contracts.evidence import ClaimCandidate  # noqa: E402
from aioa_memory_kernel.contracts.exceptions import (  # noqa: E402
    ContractValidationError,
)
from aioa_memory_kernel.contracts.identities import KernelRunIdentity  # noqa: E402
from aioa_memory_kernel.contracts.serialization import (  # noqa: E402
    canonical_sha256,
)
from aioa_memory_kernel.german_law.e2e_runtime import (  # noqa: E402
    STEP38_ACTIVATION_RECOVERY_OBSERVATION_VERSION,
    STEP38_COHERENT_RUNTIME_PROOF_VERSION,
    Step38ActivationRecoveryObservation,
    Step38CoherentRuntimeProof,
    Step38PersonalMemoryScenario,
    Step38RealSecondModelInferenceStatus,
)
from aioa_memory_kernel.modeling import ProviderIdentity  # noqa: E402
from aioa_memory_kernel.persistence import (  # noqa: E402
    SerializableTransactionRunner,
    extract_sqlstate,
)
from aioa_memory_kernel.personal_memory import (  # noqa: E402
    STEP28_SCHEMA_VERSION,
    STEP29_SCHEMA_VERSION,
    PERSONAL_MEMORY_ACTIVATION_ACTOR_ID,
    PERSONAL_MEMORY_COMMIT_ACTOR_ID,
    ActivePatchRetrievalService,
    AdvancePersonalMemoryPatchToAwaitingApproval,
    BindPersonalMemoryPatchEvidence,
    CanonicalEvidenceCompatibility,
    CorrectionCandidateMetadata,
    CorrectionCandidateIntakeDisposition,
    CorrectionCandidateReasonCode,
    CorrectionCandidateRouteResultLineage,
    CorrectionCandidateTrigger,
    CreatePersonalMemoryPatchProposal,
    PersonalMemoryActivationService,
    PersonalMemoryLifecycle32Service,
    PersonalMemoryPatchLifecycleError,
    PersonalMemoryStep32Error,
    Step30ReasonCode,
    Step31ReasonCode,
    Step32ReasonCode,
    ValidatePersonalMemoryPatchProposal,
    assess_shared_promotion_privacy,
    build_active_patch_retrieval_request,
    build_correction_candidate_envelope,
    build_personal_memory_activation_request,
    build_personal_memory_approval_request,
    build_personal_memory_commit_request,
    build_lifecycle_export_request,
    build_shared_promotion_consent,
    build_shared_promotion_request,
)
from aioa_memory_kernel.reliability import (  # noqa: E402
    FailureDomain,
    FailurePoint,
    FailureRecoveryCaseResult,
    RecoveryStatus,
)
from aioa_memory_kernel.personal_memory_ui import (  # noqa: E402
    KernelPersonalMemoryUiBackend,
    OwnerPrincipal,
)
from aioa_memory_kernel.review_workspace import (  # noqa: E402
    STEP34_REVIEW_SERVICE_ACTOR_ID,
    STEP34_SCHEMA_VERSION,
    HumanReviewWorkspaceService,
    ReviewCaseIntakeService,
    ReviewQueueRequest,
    ReviewerPrincipal,
    ReviewerRole,
    build_reviewer_authorization,
    shared_promotion_review_case,
)
from aioa_memory_kernel.security.credentials import CredentialPurpose  # noqa: E402
from aioa_memory_kernel.temporal import Step21ReasonCode  # noqa: E402


DEFAULT_COCKROACH_BINARY = step18.DEFAULT_COCKROACH
_SLOT_ID = "personal-slot-step38-coherent"
_REVIEWER_ID = "reviewer-step38-coherent"


class Step38CoherentRuntimeError(RuntimeError):
    """Sanitized failure from the bounded downstream validation adapter."""

    def __init__(self, code: str) -> None:
        super().__init__("Step 38 coherent runtime proof failed")
        self.code = code


class _MutableClock:
    def __init__(self, current) -> None:
        self.current = current

    def now(self):
        return self.current

    def set(self, current) -> None:
        self.current = current


class _AcknowledgementLostAfterDurableActivation(RuntimeError):
    """Test-only unknown-completion signal after one durable activation."""

    sanitized_code = "ACTIVATION_ACKNOWLEDGEMENT_LOST"


class _ActivationAcknowledgementLossRunner(SerializableTransactionRunner):
    """Delegate to the real DB runner, then lose exactly one acknowledgement."""

    def __init__(self, delegate: SerializableTransactionRunner) -> None:
        super().__init__(
            lambda: None,
            credential_purpose=CredentialPurpose.PERSONAL_MEMORY_COMMIT_DATABASE,
        )
        delegate.require_credential_purpose(
            CredentialPurpose.PERSONAL_MEMORY_COMMIT_DATABASE
        )
        self._delegate = delegate
        self._lost = False
        self.attempt_count = 0

    def run(self, context, callback, *, operation_kind=None):
        self.attempt_count += 1
        result = self._delegate.run(
            context,
            callback,
            operation_kind=operation_kind,
        )
        if not self._lost:
            self._lost = True
            raise _AcknowledgementLostAfterDurableActivation
        return result


def _denial_hash(
    *,
    operation: str,
    request_hash: str,
    authenticated_actor_id: str,
    reason_code: str,
) -> str:
    """Hash one sanitized denied service invocation without retaining content."""

    return canonical_sha256(
        {
            "operation": operation,
            "request_hash": request_hash,
            "authenticated_actor_id_digest": canonical_sha256(
                authenticated_actor_id
            ),
            "reason_code": reason_code,
            "result": "DENIED",
        }
    )


def _append_typed_audit(service: AuditLedgerService, draft: AuditEventDraft):
    entry, replay = service.append_event(
        draft,
        authenticated_tenant_id=draft.tenant_id,
        authenticated_actor_type=draft.actor_type,
        authenticated_actor_id=draft.actor_id,
    )
    if replay:
        raise Step38CoherentRuntimeError("AUDIT_UNEXPECTED_REPLAY")
    return entry


def _provider(binding) -> ProviderIdentity:
    return ProviderIdentity(
        provider_id=binding.provider_id,
        adapter_version="step38-coherent-adapter-1",
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


def _ensure_identities_sql(scenario: Step38PersonalMemoryScenario, at) -> str:
    quote = migrations.sql_literal
    tenant = scenario.upstream.route.tenant_id
    owner = scenario.upstream.route.user_id
    timestamp = quote(at.isoformat()) + "::TIMESTAMPTZ"
    tenants = (
        (tenant, "Step 38 coherent owner tenant"),
        (step29.OTHER_TENANT, "Step 38 coherent isolated tenant"),
    )
    users = (
        (tenant, owner, "Step 38 coherent owner"),
        (tenant, step29.OTHER_USER, "Step 38 coherent other owner"),
        (tenant, _REVIEWER_ID, "Step 38 coherent reviewer"),
        (
            tenant,
            STEP34_REVIEW_SERVICE_ACTOR_ID,
            "Step 38 coherent review service",
        ),
        (
            step29.OTHER_TENANT,
            step29.OTHER_TENANT_USER,
            "Step 38 coherent isolated owner",
        ),
    )
    tenant_values = ", ".join(
        f"({quote(value)}, {quote(label)}, '{{}}'::JSONB, {timestamp}, {timestamp})"
        for value, label in tenants
    )
    user_values = ", ".join(
        f"({quote(tenant_id)}, {quote(user_id)}, {quote(label)}, "
        f"'{{}}'::JSONB, {timestamp}, {timestamp})"
        for tenant_id, user_id, label in users
    )
    return ";\n".join(
        (
            "INSERT INTO memory_patch.tenants "
            "(tenant_id, display_name, metadata, created_at, updated_at) VALUES "
            + tenant_values
            + " ON CONFLICT (tenant_id) DO NOTHING",
            "INSERT INTO memory_patch.users "
            "(tenant_id, user_id, display_name, metadata, created_at, updated_at) "
            "VALUES "
            + user_values
            + " ON CONFLICT (tenant_id, user_id) DO NOTHING",
        )
    )


def _candidate_from_upstream(scenario, slot, *, created_at):
    upstream = scenario.upstream
    answer = upstream.final_outcome.verified_answer
    if answer is None:
        raise Step38CoherentRuntimeError("UPSTREAM_VERIFIED_ANSWER_MISSING")
    corrected = tuple(
        item
        for item in upstream.step25_result.ordered_claim_verifications
        if item.corrected_evidence_proof is not None
        and item.final_step25_verdict.value == "VERIFIED_SUPPORTED"
    )
    if len(corrected) != 1:
        raise Step38CoherentRuntimeError("CORRECTED_CLAIM_PROOF_NOT_UNIQUE")
    verification = corrected[0]
    proof = verification.corrected_evidence_proof
    assert proof is not None
    link = proof.original_evidence_link
    if link.relation is not ClaimEvidenceRelation.REFUTES:
        raise Step38CoherentRuntimeError("SOURCE_CLAIM_IS_NOT_REFUTED")
    source_claim = next(
        (
            item
            for item in upstream.packet_input_snapshot.ordered_claims
            if item.claim_id == link.claim_id
        ),
        None,
    )
    target_claim = next(
        (
            item
            for item in upstream.step25_result.ordered_claims
            if item.claim_id == verification.claim_id
        ),
        None,
    )
    if source_claim is None or target_claim is None:
        raise Step38CoherentRuntimeError("CORRECTED_CLAIM_LINEAGE_INCOMPLETE")
    binding = slot.model_bindings[0]
    route = upstream.route
    candidate = CorrectionCandidate(
        event_id="event-step38-coherent-correction",
        tenant_id=slot.tenant_id,
        user_id=slot.owner_user_id,
        personal_memory_space_id=slot.personal_memory_space_id,
        source_component=ActorType.KNOWLEDGE_KERNEL,
        run_id=route.request_id,
        model_binding_id=binding.binding_id,
        draft_v1_reference=upstream.draft_v1.draft_hash,
        detected_claims=(
            ClaimCandidate(
                claim_id=source_claim.claim_id,
                draft_id=source_claim.draft_id,
                statement=source_claim.exact_claim_text,
                claim_category=source_claim.claim_type.value,
                scope_dimensions=route.effective_scope,
            ),
        ),
        proposed_correction=target_claim.exact_claim_text,
        available_evidence_references=(link.link_hash,),
        uncertainty=0.0,
        created_at=created_at,
        state=CorrectionCandidateState.DETECTED,
    )
    run = KernelRunIdentity(
        kernel_run_id=route.request_id,
        tenant_id=slot.tenant_id,
        user_id=slot.owner_user_id,
        personal_memory_space_id=slot.personal_memory_space_id,
        model_binding_id=binding.binding_id,
        created_at=created_at - timedelta(seconds=1),
    )
    lineage = CorrectionCandidateRouteResultLineage(
        schema_version=STEP28_SCHEMA_VERSION,
        request_id=route.request_id,
        original_query_digest=upstream.draft_v1.original_query_digest,
        route_hash=route.route_hash,
        result_hash=answer.answer_hash,
        knowledge_route=route.knowledge_route,
        selected_hat_id=route.selected_hat_id,
        selected_hat_version=route.selected_hat_version,
        selected_manifest_digest=route.selected_manifest_digest,
        effective_scope=route.effective_scope,
        answer_status=AnswerStatus.VERIFIED,
        evidence_status=EvidenceStatus.SUFFICIENT,
        draft_v1_hash=upstream.draft_v1.draft_hash,
        draft_v2_hash=upstream.step25_result.draft_v2.draft_v2_hash,
        correction_packet_hash=upstream.correction_packet.packet_hash,
        verification_summary_hash=(
            upstream.step25_result.verification_summary.summary_hash
        ),
        verified_answer_hash=answer.answer_hash,
    )
    metadata = CorrectionCandidateMetadata(
        schema_version=STEP28_SCHEMA_VERSION,
        trigger=CorrectionCandidateTrigger.KNOWLEDGE_KERNEL_DETECTED,
        producer_id="kernel-step38-coherent-validation",
        producer_version="1.0.0",
        reason_codes=(CorrectionCandidateReasonCode.SOURCE_KNOWLEDGE_KERNEL,),
    )
    envelope = build_correction_candidate_envelope(
        candidate=candidate,
        kernel_run=run,
        slot=slot,
        route_result_lineage=lineage,
        metadata=metadata,
        idempotency_key="step38-coherent-candidate",
        submitted_at=created_at + timedelta(seconds=1),
    )
    return envelope, link, target_claim


def _append_audit(
    service: AuditLedgerService,
    *,
    event_type,
    subject_type,
    subject_id,
    subject_hash,
    tenant_id,
    owner_user_id,
    space_id,
    actor_type,
    actor_id,
    idempotency_key,
    occurred_at,
    request_id,
    route_hash,
    lineage_hashes,
):
    entry, replay = service.append_event(
        AuditEventDraft(
            event_type=event_type,
            tenant_id=tenant_id,
            owner_user_id=owner_user_id,
            personal_memory_space_id=space_id,
            # The real Step 38 retrieval path does not persist a Step 4
            # ``kernel_runs`` row.  Keep the request/route/lineage bindings,
            # but do not manufacture a detached kernel-run reference: the
            # Step 5 audit RLS policy deliberately rejects such references.
            kernel_run_id=None,
            request_id=request_id,
            subject_type=subject_type,
            subject_id=subject_id,
            subject_hash=subject_hash,
            actor_type=actor_type,
            actor_id=actor_id,
            idempotency_key=idempotency_key,
            occurred_at=occurred_at,
            recorded_at=occurred_at,
            event_payload={
                "canonical_evidence": False,
                "external_execution_authority": False,
                "source_publication_authority": False,
                "state": event_type.value,
            },
            reason_codes=(AuditReasonCode.AUDIT_EVENT_APPENDED,),
            route_hash=route_hash,
            lineage_hashes=lineage_hashes,
        ),
        authenticated_tenant_id=tenant_id,
        authenticated_actor_type=actor_type,
        authenticated_actor_id=actor_id,
    )
    if replay:
        raise Step38CoherentRuntimeError("AUDIT_UNEXPECTED_REPLAY")
    return entry


def _retrieval(
    service,
    scenario,
    slot,
    binding,
    query_text,
    *,
    route=None,
    temporal=None,
):
    selected_route = route or scenario.later_route
    selected_temporal = temporal or scenario.later_temporal_result
    request = build_active_patch_retrieval_request(
        route=selected_route,
        temporal_result=selected_temporal,
        personal_memory_space_id=slot.personal_memory_space_id,
        model_identity=_provider(binding),
        query_text=query_text,
    )
    result, context = service.retrieve(
        request,
        route=selected_route,
        temporal_result=selected_temporal,
    )
    return request, result, context


def _rebind_later_identity(scenario, *, tenant_id: str, user_id: str):
    route = replace(
        scenario.later_route,
        tenant_id=tenant_id,
        user_id=user_id,
    )
    temporal = replace(
        scenario.later_temporal_result,
        tenant_id=tenant_id,
        user_id=user_id,
        route_hash=route.route_hash,
    )
    return route, temporal


def _dashboard_hash(dashboard) -> str:
    return canonical_sha256(
        {
            "slots": tuple(
                {
                    "slot_hash": item.slot_hash,
                    "state": item.state,
                    "state_version": item.state_version,
                    "configuration_version": item.configuration_version,
                    "quota": {
                        "stored_bytes": item.quota.stored_bytes,
                        "maximum_bytes": item.quota.maximum_bytes,
                        "active_patches": item.quota.active_patches,
                        "maximum_active_patches": (
                            item.quota.maximum_active_patches
                        ),
                        "model_bindings": item.quota.model_bindings,
                        "maximum_model_bindings": (
                            item.quota.maximum_model_bindings
                        ),
                        "total_spaces": item.quota.total_spaces,
                        "maximum_total_spaces": item.quota.maximum_total_spaces,
                    },
                    "model_binding_hashes": tuple(
                        binding.binding_hash for binding in item.model_bindings
                    ),
                    "patch_count": item.patch_count,
                    "pending_approval_count": item.pending_approval_count,
                    "active_patch_count": item.active_patch_count,
                }
                for item in dashboard.slots
            ),
            "pending_proposal_hashes": tuple(
                item.proposal_hash for item in dashboard.pending_approvals
            ),
            "patches": tuple(
                (item.proposal_hash, item.patch_hash, item.state)
                for item in dashboard.recent_patches
            ),
            "audit_event_hashes": tuple(
                item.event_hash for item in dashboard.recent_audit_events
            ),
            "counts": {
                "slots": dashboard.slot_count,
                "active_slots": dashboard.active_slot_count,
                "pending": dashboard.pending_approval_count,
                "active": dashboard.active_patch_count,
            },
        }
    )


def _awaiting_dashboard_verified(
    dashboard,
    *,
    proposal_hash: str,
    model_binding_hashes: tuple[str, ...],
    proposal_audit_event_hash: str,
) -> bool:
    if len(dashboard.slots) != 1:
        return False
    slot = dashboard.slots[0]
    return (
        dashboard.pending_approval_count == 1
        and dashboard.active_patch_count == 0
        and tuple(item.proposal_hash for item in dashboard.pending_approvals)
        == (proposal_hash,)
        and slot.pending_approval_count == 1
        and slot.active_patch_count == 0
        and tuple(sorted(item.binding_hash for item in slot.model_bindings))
        == tuple(sorted(model_binding_hashes))
        and slot.quota.model_bindings == len(model_binding_hashes)
        and slot.quota.active_patches == 0
        and slot.quota.maximum_bytes >= slot.quota.stored_bytes
        and slot.quota.maximum_model_bindings >= slot.quota.model_bindings
        and any(
            item.event_hash == proposal_audit_event_hash
            for item in dashboard.recent_audit_events
        )
    )


def _active_dashboard_verified(
    dashboard,
    *,
    active_patch_hash: str,
    model_binding_hashes: tuple[str, ...],
    lifecycle_audit_event_hashes: tuple[str, ...],
) -> bool:
    if len(dashboard.slots) != 1:
        return False
    slot = dashboard.slots[0]
    visible_audit = {item.event_hash for item in dashboard.recent_audit_events}
    return (
        dashboard.pending_approval_count == 0
        and dashboard.active_patch_count == 1
        and slot.pending_approval_count == 0
        and slot.active_patch_count == 1
        and tuple(sorted(item.binding_hash for item in slot.model_bindings))
        == tuple(sorted(model_binding_hashes))
        and slot.quota.model_bindings == len(model_binding_hashes)
        and slot.quota.active_patches == 1
        and any(
            item.patch_hash == active_patch_hash
            and item.state == PatchState.ACTIVE.value
            for item in dashboard.recent_patches
        )
        and set(lifecycle_audit_event_hashes).issubset(visible_audit)
    )


def _observed_count(row, name: str) -> int:
    if row is None or name not in row or isinstance(row[name], bool):
        raise Step38CoherentRuntimeError("RECOVERY_OBSERVATION_INVALID")
    try:
        value = int(row[name])
    except (TypeError, ValueError) as error:
        raise Step38CoherentRuntimeError(
            "RECOVERY_OBSERVATION_INVALID"
        ) from error
    if value < 0:
        raise Step38CoherentRuntimeError("RECOVERY_OBSERVATION_INVALID")
    return value


def _control_plane_count(root, database: str, sql: str, name: str) -> int:
    rendered = root.execute(database, sql, timeout=120)
    rows = tuple(csv.reader(io.StringIO(rendered), delimiter="\t"))
    if len(rows) != 2 or rows[0] != [name] or len(rows[1]) != 1:
        raise Step38CoherentRuntimeError("RECOVERY_CONTROL_PLANE_READ_INVALID")
    try:
        value = int(rows[1][0])
    except ValueError as error:
        raise Step38CoherentRuntimeError(
            "RECOVERY_CONTROL_PLANE_READ_INVALID"
        ) from error
    if value < 0:
        raise Step38CoherentRuntimeError("RECOVERY_CONTROL_PLANE_READ_INVALID")
    return value


def _observe_activation_recovery(
    *,
    root,
    database: str,
    app_runner,
    personal,
    tenant_id: str,
    owner_user_id: str,
    slot,
    awaiting,
    active,
    approval_request,
    approval_receipt,
    commit_request,
    commit_receipt,
    activation_request,
    activation_receipt,
    activation_audit_event_hash: str,
    quota_before,
    cross_user_approval_denied: bool,
    replay_returned_existing_receipt: bool,
) -> Step38ActivationRecoveryObservation:
    """Read every activation side effect again in a fresh owner transaction."""

    def work(transaction):
        proposal = transaction.fetch_one(
            """
            SELECT count(*) AS row_count,
                   count(*) FILTER (
                     WHERE owner_user_id = %s
                       AND personal_memory_space_id = %s
                       AND content_hash = %s
                       AND lifecycle_state = 'ACTIVE'
                       AND step29_state_version = 7
                       AND step29_state_hash = %s
                       AND step30_approval_receipt_hash = %s
                       AND step30_commit_receipt_hash = %s
                       AND step30_activation_receipt_hash = %s
                       AND step30_patch_id = %s
                   ) AS exact_match_count,
                   count(*) FILTER (
                     WHERE owner_user_id IS DISTINCT FROM %s
                        OR personal_memory_space_id IS DISTINCT FROM %s
                   ) AS authority_mismatch_count
              FROM memory_patch.memory_patch_proposals
             WHERE tenant_id = %s AND proposal_id = %s
            """,
            (
                owner_user_id,
                slot.personal_memory_space_id,
                awaiting.proposal.proposal_hash,
                active.state_hash,
                approval_receipt.receipt_hash,
                commit_receipt.receipt_hash,
                activation_receipt.receipt_hash,
                active.committed_patch.patch_id,
                owner_user_id,
                slot.personal_memory_space_id,
                tenant_id,
                awaiting.proposal.proposal_id,
            ),
        )
        approval = transaction.fetch_one(
            """
            SELECT count(*) AS row_count,
                   count(*) FILTER (
                     WHERE approval_id = %s
                       AND step30_request_hash = %s
                       AND step30_approval_replay_identity = %s
                       AND step30_approval_receipt_hash = %s
                       AND owner_user_id = %s
                       AND personal_memory_space_id = %s
                       AND approver_type = 'USER'
                       AND approver_id = %s
                       AND decision = 'APPROVE'
                   ) AS exact_match_count,
                   count(*) FILTER (
                     WHERE owner_user_id IS DISTINCT FROM %s
                        OR personal_memory_space_id IS DISTINCT FROM %s
                        OR approver_type IS DISTINCT FROM 'USER'
                        OR approver_id IS DISTINCT FROM %s
                   ) AS authority_mismatch_count
              FROM memory_patch.memory_patch_approvals
             WHERE tenant_id = %s AND proposal_id = %s
            """,
            (
                approval_receipt.approval_id,
                approval_request.request_hash,
                approval_request.approval_replay_identity,
                approval_receipt.receipt_hash,
                owner_user_id,
                slot.personal_memory_space_id,
                owner_user_id,
                owner_user_id,
                slot.personal_memory_space_id,
                owner_user_id,
                tenant_id,
                awaiting.proposal.proposal_id,
            ),
        )
        commit = transaction.fetch_one(
            """
            SELECT count(*) AS row_count,
                   count(*) FILTER (
                     WHERE commit_id = %s
                       AND step30_request_hash = %s
                       AND step30_commit_replay_identity = %s
                       AND step30_commit_receipt_hash = %s
                       AND step30_patch_hash = %s
                       AND committed_patch_id = %s
                       AND owner_user_id = %s
                       AND personal_memory_space_id = %s
                       AND actor_type = 'COMMIT_SERVICE'
                       AND actor_id = %s
                   ) AS exact_match_count,
                   count(*) FILTER (
                     WHERE owner_user_id IS DISTINCT FROM %s
                        OR personal_memory_space_id IS DISTINCT FROM %s
                        OR actor_type IS DISTINCT FROM 'COMMIT_SERVICE'
                        OR actor_id IS DISTINCT FROM %s
                   ) AS authority_mismatch_count
              FROM memory_patch.memory_patch_commits
             WHERE tenant_id = %s AND proposal_id = %s
            """,
            (
                commit_receipt.commit_id,
                commit_request.request_hash,
                commit_request.commit_replay_identity,
                commit_receipt.receipt_hash,
                active.committed_patch.patch_hash,
                active.committed_patch.patch_id,
                owner_user_id,
                slot.personal_memory_space_id,
                PERSONAL_MEMORY_COMMIT_ACTOR_ID,
                owner_user_id,
                slot.personal_memory_space_id,
                PERSONAL_MEMORY_COMMIT_ACTOR_ID,
                tenant_id,
                awaiting.proposal.proposal_id,
            ),
        )
        item = transaction.fetch_one(
            """
            SELECT count(*) AS row_count,
                   count(*) FILTER (
                     WHERE scope.owner_user_id = %s
                       AND scope.personal_memory_space_id = %s
                       AND item.hat_scope_id = %s
                       AND item.target_scope = 'USER_PERSONAL_HAT'
                       AND item.visibility = 'PERSONAL'
                       AND item.trust_class = 'PERSONAL_VERIFIED_PATCH'
                       AND item.active = true AND item.revoked = false
                       AND item.step30_state_version = 7
                       AND item.step30_state_hash = %s
                       AND item.step30_patch_hash = %s
                       AND item.step30_approval_receipt_hash = %s
                       AND item.step30_commit_receipt_hash = %s
                       AND item.step30_activation_replay_identity = %s
                       AND item.step30_activation_receipt_hash = %s
                   ) AS exact_match_count,
                   count(*) FILTER (
                     WHERE scope.owner_user_id IS DISTINCT FROM %s
                        OR scope.personal_memory_space_id IS DISTINCT FROM %s
                        OR item.target_scope IS DISTINCT FROM 'USER_PERSONAL_HAT'
                        OR item.visibility IS DISTINCT FROM 'PERSONAL'
                   ) AS authority_mismatch_count
              FROM memory_patch.memory_items AS item
              JOIN memory_patch.hat_scopes AS scope
                ON scope.tenant_id = item.tenant_id
               AND scope.hat_scope_id = item.hat_scope_id
               AND scope.target_scope = item.target_scope
             WHERE item.tenant_id = %s AND item.source_patch_id = %s
            """,
            (
                owner_user_id,
                slot.personal_memory_space_id,
                active.committed_patch.hat_scope_id,
                active.state_hash,
                active.committed_patch.patch_hash,
                approval_receipt.receipt_hash,
                commit_receipt.receipt_hash,
                activation_request.activation_replay_identity,
                activation_receipt.receipt_hash,
                owner_user_id,
                slot.personal_memory_space_id,
                tenant_id,
                active.committed_patch.patch_id,
            ),
        )
        transition = transaction.fetch_one(
            """
            SELECT count(*) AS row_count,
                   count(*) FILTER (
                     WHERE state_before = 'COMMITTED'
                       AND state_after = 'ACTIVE'
                       AND actor_type = 'SYSTEM'
                       AND actor_id = %s
                       AND step30_receipt_hash = %s
                   ) AS exact_match_count,
                   count(*) FILTER (
                     WHERE actor_type IS DISTINCT FROM 'SYSTEM'
                        OR actor_id IS DISTINCT FROM %s
                   ) AS authority_mismatch_count
              FROM memory_patch.patch_transition_records
             WHERE tenant_id = %s AND proposal_id = %s
               AND state_after = 'ACTIVE'
            """,
            (
                PERSONAL_MEMORY_ACTIVATION_ACTOR_ID,
                activation_receipt.receipt_hash,
                PERSONAL_MEMORY_ACTIVATION_ACTOR_ID,
                tenant_id,
                awaiting.proposal.proposal_id,
            ),
        )
        audit_event = transaction.fetch_one(
            """
            SELECT count(*) AS row_count,
                   count(*) FILTER (
                     WHERE event_hash = %s
                       AND user_id = %s
                       AND personal_memory_space_id = %s
                       AND actor_type = 'ACTIVATION_SERVICE'
                       AND actor_id = %s
                       AND subject_hash = %s
                       AND lineage_hashes ->> 'activation_receipt_hash' = %s
                   ) AS exact_match_count,
                   count(*) FILTER (
                     WHERE user_id IS DISTINCT FROM %s
                        OR personal_memory_space_id IS DISTINCT FROM %s
                        OR actor_type IS DISTINCT FROM 'ACTIVATION_SERVICE'
                        OR actor_id IS DISTINCT FROM %s
                   ) AS authority_mismatch_count
              FROM memory_patch.audit_events
             WHERE tenant_id = %s
               AND event_type = 'PERSONAL_MEMORY_ACTIVATED'
               AND subject_id = %s
            """,
            (
                activation_audit_event_hash,
                owner_user_id,
                slot.personal_memory_space_id,
                PERSONAL_MEMORY_ACTIVATION_ACTOR_ID,
                active.committed_patch.patch_hash,
                activation_receipt.receipt_hash,
                owner_user_id,
                slot.personal_memory_space_id,
                PERSONAL_MEMORY_ACTIVATION_ACTOR_ID,
                tenant_id,
                active.committed_patch.patch_id,
            ),
        )
        rls_cross_scope = transaction.fetch_one(
            """
            SELECT count(*) AS visible_count
              FROM (
                SELECT proposal.tenant_id, proposal.owner_user_id,
                       proposal.personal_memory_space_id
                  FROM memory_patch.memory_patch_proposals AS proposal
                 WHERE (
                       proposal.proposal_id = %s
                       OR proposal.step30_approval_receipt_hash = %s
                       OR proposal.step30_commit_receipt_hash = %s
                       OR proposal.step30_activation_receipt_hash = %s
                       OR proposal.step30_patch_id = %s
                   )
                   AND (
                     proposal.tenant_id IS DISTINCT FROM %s
                     OR proposal.owner_user_id IS DISTINCT FROM %s
                     OR proposal.personal_memory_space_id IS DISTINCT FROM %s
                   )
                UNION ALL
                SELECT approval.tenant_id, approval.owner_user_id,
                       approval.personal_memory_space_id
                  FROM memory_patch.memory_patch_approvals AS approval
                 WHERE (
                       approval.proposal_id = %s
                       OR approval.approval_id = %s
                       OR approval.step30_request_hash = %s
                       OR approval.step30_approval_replay_identity = %s
                       OR approval.step30_approval_receipt_hash = %s
                   )
                   AND (
                     approval.tenant_id IS DISTINCT FROM %s
                     OR approval.owner_user_id IS DISTINCT FROM %s
                     OR approval.personal_memory_space_id IS DISTINCT FROM %s
                   )
                UNION ALL
                SELECT commit_row.tenant_id, commit_row.owner_user_id,
                       commit_row.personal_memory_space_id
                  FROM memory_patch.memory_patch_commits AS commit_row
                 WHERE (
                       commit_row.proposal_id = %s
                       OR commit_row.commit_id = %s
                       OR commit_row.step30_request_hash = %s
                       OR commit_row.step30_commit_replay_identity = %s
                       OR commit_row.step30_commit_receipt_hash = %s
                       OR commit_row.committed_patch_id = %s
                   )
                   AND (
                     commit_row.tenant_id IS DISTINCT FROM %s
                     OR commit_row.owner_user_id IS DISTINCT FROM %s
                     OR commit_row.personal_memory_space_id IS DISTINCT FROM %s
                   )
                UNION ALL
                SELECT item.tenant_id, scope.owner_user_id,
                       scope.personal_memory_space_id
                  FROM memory_patch.memory_items AS item
                  JOIN memory_patch.hat_scopes AS scope
                    ON scope.tenant_id = item.tenant_id
                   AND scope.hat_scope_id = item.hat_scope_id
                   AND scope.target_scope = item.target_scope
                 WHERE (
                       item.source_patch_id = %s
                       OR item.step30_activation_replay_identity = %s
                       OR item.step30_activation_receipt_hash = %s
                   )
                   AND (
                     item.tenant_id IS DISTINCT FROM %s
                     OR scope.owner_user_id IS DISTINCT FROM %s
                     OR scope.personal_memory_space_id IS DISTINCT FROM %s
                   )
                UNION ALL
                SELECT transition.tenant_id, proposal.owner_user_id,
                       proposal.personal_memory_space_id
                  FROM memory_patch.patch_transition_records AS transition
                  JOIN memory_patch.memory_patch_proposals AS proposal
                    ON proposal.tenant_id = transition.tenant_id
                   AND proposal.proposal_id = transition.proposal_id
                 WHERE (
                       transition.proposal_id = %s
                       OR transition.step30_receipt_hash = %s
                   )
                   AND (
                     transition.tenant_id IS DISTINCT FROM %s
                     OR proposal.owner_user_id IS DISTINCT FROM %s
                     OR proposal.personal_memory_space_id IS DISTINCT FROM %s
                   )
                UNION ALL
                SELECT event.tenant_id, event.user_id,
                       event.personal_memory_space_id
                  FROM memory_patch.audit_events AS event
                 WHERE (
                       event.event_hash = %s
                       OR event.subject_id = %s
                       OR event.lineage_hashes ->> 'activation_receipt_hash' = %s
                   )
                   AND (
                     event.tenant_id IS DISTINCT FROM %s
                     OR event.user_id IS DISTINCT FROM %s
                     OR event.personal_memory_space_id IS DISTINCT FROM %s
                   )
              ) AS cross_scope
            """,
            (
                awaiting.proposal.proposal_id,
                approval_receipt.receipt_hash,
                commit_receipt.receipt_hash,
                activation_receipt.receipt_hash,
                active.committed_patch.patch_id,
                tenant_id,
                owner_user_id,
                slot.personal_memory_space_id,
                awaiting.proposal.proposal_id,
                approval_receipt.approval_id,
                approval_request.request_hash,
                approval_request.approval_replay_identity,
                approval_receipt.receipt_hash,
                tenant_id,
                owner_user_id,
                slot.personal_memory_space_id,
                awaiting.proposal.proposal_id,
                commit_receipt.commit_id,
                commit_request.request_hash,
                commit_request.commit_replay_identity,
                commit_receipt.receipt_hash,
                active.committed_patch.patch_id,
                tenant_id,
                owner_user_id,
                slot.personal_memory_space_id,
                active.committed_patch.patch_id,
                activation_request.activation_replay_identity,
                activation_receipt.receipt_hash,
                tenant_id,
                owner_user_id,
                slot.personal_memory_space_id,
                awaiting.proposal.proposal_id,
                activation_receipt.receipt_hash,
                tenant_id,
                owner_user_id,
                slot.personal_memory_space_id,
                activation_audit_event_hash,
                active.committed_patch.patch_id,
                activation_receipt.receipt_hash,
                tenant_id,
                owner_user_id,
                slot.personal_memory_space_id,
            ),
        )
        return (
            proposal,
            approval,
            commit,
            item,
            transition,
            audit_event,
            rls_cross_scope,
        )

    rows = app_runner.run(
        step29._context(tenant_id, owner_user_id),
        work,
        operation_kind="STEP38_ACTIVATION_RECOVERY_DURABLE_OBSERVATION",
    )
    quota_after = personal.quota_usage(
        tenant_id=tenant_id,
        owner_user_id=owner_user_id,
        personal_memory_space_id=slot.personal_memory_space_id,
    )
    observed_rows = rows[:6]
    prefixes = ("proposal", "approval", "commit", "item", "transition", "audit")
    totals = {
        prefix: _observed_count(row, "row_count")
        for prefix, row in zip(prefixes, observed_rows, strict=True)
    }
    exact = {
        prefix: _observed_count(row, "exact_match_count")
        for prefix, row in zip(prefixes, observed_rows, strict=True)
    }
    authority_mismatches = sum(
        _observed_count(row, "authority_mismatch_count")
        for row in observed_rows
    )
    rls_cross_scope_visible_count = _observed_count(rows[6], "visible_count")
    quote = migrations.sql_literal
    foreign_scope_count = _control_plane_count(
        root,
        database,
        """
        SELECT count(*) AS foreign_scope_count
          FROM (
            SELECT proposal.tenant_id, proposal.owner_user_id,
                   proposal.personal_memory_space_id
              FROM memory_patch.memory_patch_proposals AS proposal
             WHERE (
                   proposal.proposal_id = {proposal_id}
                   OR proposal.step30_approval_receipt_hash = {approval_receipt}
                   OR proposal.step30_commit_receipt_hash = {commit_receipt}
                   OR proposal.step30_activation_receipt_hash = {activation_receipt}
                   OR proposal.step30_patch_id = {patch_id}
               )
               AND (
                 proposal.tenant_id IS DISTINCT FROM {tenant_id}
                 OR proposal.owner_user_id IS DISTINCT FROM {owner_user_id}
                 OR proposal.personal_memory_space_id IS DISTINCT FROM {space_id}
               )
            UNION ALL
            SELECT approval.tenant_id, approval.owner_user_id,
                   approval.personal_memory_space_id
              FROM memory_patch.memory_patch_approvals AS approval
             WHERE (
                   approval.proposal_id = {proposal_id}
                   OR approval.approval_id = {approval_id}
                   OR approval.step30_request_hash = {approval_request}
                   OR approval.step30_approval_replay_identity = {approval_replay}
                   OR approval.step30_approval_receipt_hash = {approval_receipt}
               )
               AND (
                 approval.tenant_id IS DISTINCT FROM {tenant_id}
                 OR approval.owner_user_id IS DISTINCT FROM {owner_user_id}
                 OR approval.personal_memory_space_id IS DISTINCT FROM {space_id}
               )
            UNION ALL
            SELECT commit_row.tenant_id, commit_row.owner_user_id,
                   commit_row.personal_memory_space_id
              FROM memory_patch.memory_patch_commits AS commit_row
             WHERE (
                   commit_row.proposal_id = {proposal_id}
                   OR commit_row.commit_id = {commit_id}
                   OR commit_row.step30_request_hash = {commit_request}
                   OR commit_row.step30_commit_replay_identity = {commit_replay}
                   OR commit_row.step30_commit_receipt_hash = {commit_receipt}
                   OR commit_row.committed_patch_id = {patch_id}
               )
               AND (
                 commit_row.tenant_id IS DISTINCT FROM {tenant_id}
                 OR commit_row.owner_user_id IS DISTINCT FROM {owner_user_id}
                 OR commit_row.personal_memory_space_id IS DISTINCT FROM {space_id}
               )
            UNION ALL
            SELECT item.tenant_id, scope.owner_user_id,
                   scope.personal_memory_space_id
              FROM memory_patch.memory_items AS item
              JOIN memory_patch.hat_scopes AS scope
                ON scope.tenant_id = item.tenant_id
               AND scope.hat_scope_id = item.hat_scope_id
               AND scope.target_scope = item.target_scope
             WHERE (
                   item.source_patch_id = {patch_id}
                   OR item.step30_activation_replay_identity = {activation_replay}
                   OR item.step30_activation_receipt_hash = {activation_receipt}
               )
               AND (
                 item.tenant_id IS DISTINCT FROM {tenant_id}
                 OR scope.owner_user_id IS DISTINCT FROM {owner_user_id}
                 OR scope.personal_memory_space_id IS DISTINCT FROM {space_id}
               )
            UNION ALL
            SELECT transition.tenant_id, proposal.owner_user_id,
                   proposal.personal_memory_space_id
              FROM memory_patch.patch_transition_records AS transition
              JOIN memory_patch.memory_patch_proposals AS proposal
                ON proposal.tenant_id = transition.tenant_id
               AND proposal.proposal_id = transition.proposal_id
             WHERE (
                   transition.proposal_id = {proposal_id}
                   OR transition.step30_receipt_hash = {activation_receipt}
               )
               AND (
                 transition.tenant_id IS DISTINCT FROM {tenant_id}
                 OR proposal.owner_user_id IS DISTINCT FROM {owner_user_id}
                 OR proposal.personal_memory_space_id IS DISTINCT FROM {space_id}
               )
            UNION ALL
            SELECT event.tenant_id, event.user_id,
                   event.personal_memory_space_id
              FROM memory_patch.audit_events AS event
             WHERE (
                   event.event_hash = {activation_audit_event_hash}
                   OR event.subject_id = {patch_id}
                   OR event.lineage_hashes ->> 'activation_receipt_hash'
                     = {activation_receipt}
               )
               AND (
                 event.tenant_id IS DISTINCT FROM {tenant_id}
                 OR event.user_id IS DISTINCT FROM {owner_user_id}
                 OR event.personal_memory_space_id IS DISTINCT FROM {space_id}
               )
          ) AS foreign_scope
        """.format(
            proposal_id=quote(awaiting.proposal.proposal_id),
            approval_id=quote(approval_receipt.approval_id),
            approval_request=quote(approval_request.request_hash),
            approval_replay=quote(approval_request.approval_replay_identity),
            approval_receipt=quote(approval_receipt.receipt_hash),
            commit_id=quote(commit_receipt.commit_id),
            commit_request=quote(commit_request.request_hash),
            commit_replay=quote(commit_request.commit_replay_identity),
            commit_receipt=quote(commit_receipt.receipt_hash),
            patch_id=quote(active.committed_patch.patch_id),
            activation_replay=quote(
                activation_request.activation_replay_identity
            ),
            activation_receipt=quote(activation_receipt.receipt_hash),
            activation_audit_event_hash=quote(activation_audit_event_hash),
            tenant_id=quote(tenant_id),
            owner_user_id=quote(owner_user_id),
            space_id=quote(slot.personal_memory_space_id),
        ),
        "foreign_scope_count",
    )
    return Step38ActivationRecoveryObservation(
        observation_version=STEP38_ACTIVATION_RECOVERY_OBSERVATION_VERSION,
        activation_request_hash=activation_request.request_hash,
        activation_receipt_hash=activation_receipt.receipt_hash,
        activation_replay_identity_hash=canonical_sha256(
            activation_request.activation_replay_identity
        ),
        proposal_row_count=totals["proposal"],
        proposal_exact_match_count=exact["proposal"],
        approval_row_count=totals["approval"],
        approval_exact_match_count=exact["approval"],
        commit_row_count=totals["commit"],
        commit_exact_match_count=exact["commit"],
        memory_item_row_count=totals["item"],
        memory_item_exact_match_count=exact["item"],
        activation_transition_row_count=totals["transition"],
        activation_transition_exact_match_count=exact["transition"],
        activation_audit_event_row_count=totals["audit"],
        activation_audit_event_exact_match_count=exact["audit"],
        quota_memory_items_before=quota_before.memory_item_count,
        quota_memory_items_after=quota_after.memory_item_count,
        quota_active_patches_before=quota_before.patch_count,
        quota_active_patches_after=quota_after.patch_count,
        quota_stored_bytes_before=quota_before.stored_bytes,
        quota_stored_bytes_after=quota_after.stored_bytes,
        durable_authority_mismatch_count=authority_mismatches,
        rls_cross_scope_visible_count=rls_cross_scope_visible_count,
        control_plane_foreign_scope_count=foreign_scope_count,
        cross_user_approval_denied=cross_user_approval_denied,
        replay_returned_existing_receipt=replay_returned_existing_receipt,
    )


def run_coherent_lineage_on_owned_database(
    scenario: Step38PersonalMemoryScenario,
    *,
    root,
    database: str,
    runtime_instance_digest: str,
    database_instance_digest: str,
    later_query_text: str,
    progress: Callable[[str], None] | None = None,
) -> Step38CoherentRuntimeProof:
    """Run Step 27-35 on an already-owned, fully migrated database."""

    if not isinstance(scenario, Step38PersonalMemoryScenario):
        raise TypeError("scenario must be Step38PersonalMemoryScenario")
    expected_query_digest = hashlib.sha256(later_query_text.encode("utf-8")).hexdigest()
    if expected_query_digest != scenario.later_query_digest:
        raise Step38CoherentRuntimeError("LATER_QUERY_DIGEST_MISMATCH")
    if root.sql_port is None:
        raise Step38CoherentRuntimeError("OWNED_SQL_PORT_MISSING")
    upstream = scenario.upstream
    tenant_id = upstream.route.tenant_id
    owner_user_id = upstream.route.user_id
    now = upstream.temporal_result.trusted_now
    mark = progress or (lambda _stage: None)
    mark("COHERENT_IDENTITIES_AND_ROLES")
    root.execute(database, _ensure_identities_sql(scenario, now), timeout=120)

    suffix = uuid.uuid4().hex[:12]
    app_role = "mp_s38_app_" + suffix
    commit_role = "mp_s38_commit_" + suffix
    audit_reader_role = "mp_s38_audit_" + suffix
    reviewer_role = "mp_s38_reviewer_" + suffix
    review_service_role = "mp_s38_service_" + suffix
    extra_roles = (
        (
            audit_reader_role,
            ("mp_audit_reader", "mp_request_context_setter"),
        ),
        (
            reviewer_role,
            ("mp_human_reviewer", "mp_request_context_setter"),
        ),
        (
            review_service_role,
            ("mp_review_service", "mp_request_context_setter"),
        ),
    )
    created: list[tuple[str, tuple[str, ...] | None]] = []
    try:
        step27._create_validation_role(root, app_role)
        created.append((app_role, None))
        step30._create_commit_validation_role(root, commit_role)
        created.append((commit_role, None))
        for role, memberships in extra_roles:
            step34._create_validation_role(root, role, memberships)
            created.append((role, memberships))

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
        audit_runner = step30._runner(
            port=root.sql_port,
            database=database,
            role=app_role,
            credential_purpose=CredentialPurpose.AUDIT_APPENDER_DATABASE,
            diagnostic=True,
        )
        audit_reader_runner = step30._runner(
            port=root.sql_port,
            database=database,
            role=audit_reader_role,
            credential_purpose=CredentialPurpose.AUDIT_READER_DATABASE,
            diagnostic=True,
        )
        reviewer_runner = step30._runner(
            port=root.sql_port,
            database=database,
            role=reviewer_role,
            credential_purpose=CredentialPurpose.HUMAN_REVIEWER_DATABASE,
            diagnostic=True,
        )
        review_service_runner = step30._runner(
            port=root.sql_port,
            database=database,
            role=review_service_role,
            credential_purpose=CredentialPurpose.REVIEW_SERVICE_DATABASE,
            diagnostic=True,
        )
        clock = _MutableClock(now + timedelta(hours=1))
        request_view = SimpleNamespace(
            route=upstream.route,
            temporal_result=upstream.temporal_result,
        )
        personal, candidates, proposals, approvals, commits, _activations = (
            step31._lifecycle_services(
                request=request_view,
                app_runner=app_runner,
                commit_runner=commit_runner,
                clock=clock,
            )
        )
        mark("COHERENT_SLOT_AND_CANDIDATE")
        slot, bindings = step31._create_slot(
            personal,
            request_view,
            slot_id=_SLOT_ID,
            suffix="step38-coherent",
            offset=0,
        )
        envelope, source_link, _target_claim = _candidate_from_upstream(
            scenario,
            slot,
            created_at=now + timedelta(seconds=1),
        )
        stored, candidate_receipt = candidates.submit_kernel_candidate(envelope)
        if (
            candidate_receipt.disposition
            is not CorrectionCandidateIntakeDisposition.ACCEPTED
            or stored.envelope_hash != envelope.envelope_hash
        ):
            raise Step38CoherentRuntimeError("CANDIDATE_INTAKE_FAILED")

        mark("COHERENT_PROPOSAL_TO_AWAITING_APPROVAL")
        create = CreatePersonalMemoryPatchProposal(
            schema_version=STEP29_SCHEMA_VERSION,
            tenant_id=tenant_id,
            owner_user_id=owner_user_id,
            personal_memory_space_id=slot.personal_memory_space_id,
            candidate_id=envelope.candidate_id,
            candidate_envelope_hash=envelope.envelope_hash,
            expected_target_binding_hash=(
                envelope.submission.target_slot_binding.target_binding_hash
            ),
            idempotency_key="step38-coherent-create-proposal",
            requested_at=now + timedelta(seconds=3),
        )
        proposed, _ = proposals.create_proposal(envelope, create)
        evidence = {
            "bundles": (upstream.step20_outcome.bundle,),
            "temporal_result": upstream.temporal_result,
            "claim_links": (source_link,),
            "claim_assessments": (
                upstream.packet_input_snapshot.ordered_candidate_assessments
            ),
            "correction_packet": upstream.correction_packet,
            "verified_answer": upstream.final_outcome.verified_answer,
            "step25_result": upstream.step25_result,
            "evidence_context": upstream.evidence_context,
        }
        bind = step29._transition_command(
            proposed,
            BindPersonalMemoryPatchEvidence,
            key="step38-coherent-bind-evidence",
            at=now + timedelta(seconds=4),
        )
        bound, _ = proposals.bind_evidence(bind, **evidence)
        validate = step29._transition_command(
            bound,
            ValidatePersonalMemoryPatchProposal,
            key="step38-coherent-validate",
            at=now + timedelta(seconds=5),
        )
        validated, validation_receipt, _ = proposals.validate_proposal(
            validate, **evidence
        )
        advance = step29._transition_command(
            validated,
            AdvancePersonalMemoryPatchToAwaitingApproval,
            key="step38-coherent-await-owner",
            at=now + timedelta(seconds=6),
            receipt_hash=validation_receipt.receipt_hash,
        )
        awaiting, _ = proposals.advance_to_awaiting_approval(advance)
        if awaiting.state is not PatchState.AWAITING_APPROVAL:
            raise Step38CoherentRuntimeError("AWAITING_APPROVAL_NOT_REACHED")

        mark("COHERENT_INITIAL_AUDIT_AND_AWAITING_UI")
        lifecycle = PersonalMemoryLifecycle32Service(
            app_runner,
            trusted_clock=clock,
        )
        audit = AuditLedgerService(
            audit_runner,
            reader_transaction_runner=audit_reader_runner,
        )
        answer = upstream.final_outcome.verified_answer
        assert answer is not None
        _append_audit(
            audit,
            event_type=AuditEventType.VERIFIED_ANSWER_ASSEMBLED,
            subject_type=AuditSubjectType.VERIFIED_ANSWER,
            subject_id=answer.answer_id,
            subject_hash=answer.answer_hash,
            tenant_id=tenant_id,
            owner_user_id=owner_user_id,
            space_id=None,
            actor_type=AuditActorType.KERNEL,
            actor_id="step38-coherent-kernel",
            idempotency_key="step38-coherent-audit-answer",
            occurred_at=now,
            request_id=upstream.route.request_id,
            route_hash=upstream.route.route_hash,
            lineage_hashes={"upstream_lineage_hash": upstream.lineage_hash},
        )
        _append_audit(
            audit,
            event_type=AuditEventType.CORRECTION_CANDIDATE_DETECTED,
            subject_type=AuditSubjectType.CORRECTION_CANDIDATE,
            subject_id=envelope.candidate_id,
            subject_hash=envelope.envelope_hash,
            tenant_id=tenant_id,
            owner_user_id=owner_user_id,
            space_id=slot.personal_memory_space_id,
            actor_type=AuditActorType.KERNEL,
            actor_id="step38-coherent-kernel",
            idempotency_key="step38-coherent-audit-candidate",
            occurred_at=envelope.submission.submitted_at,
            request_id=upstream.route.request_id,
            route_hash=upstream.route.route_hash,
            lineage_hashes={"verified_answer_hash": answer.answer_hash},
        )
        proposal_audit_entry = _append_audit(
            audit,
            event_type=AuditEventType.PERSONAL_MEMORY_PROPOSAL_CREATED,
            subject_type=AuditSubjectType.PERSONAL_MEMORY_PROPOSAL,
            subject_id=awaiting.proposal.proposal_id,
            subject_hash=awaiting.proposal.proposal_hash,
            tenant_id=tenant_id,
            owner_user_id=owner_user_id,
            space_id=slot.personal_memory_space_id,
            actor_type=AuditActorType.KERNEL,
            actor_id="step38-coherent-kernel",
            idempotency_key="step38-coherent-audit-proposal",
            occurred_at=awaiting.updated_at,
            request_id=upstream.route.request_id,
            route_hash=upstream.route.route_hash,
            lineage_hashes={"candidate_envelope_hash": envelope.envelope_hash},
        )

        backend = KernelPersonalMemoryUiBackend(
            app_runner,
            personal_memory_service=personal,
            approval_service=approvals,
            lifecycle_service=lifecycle,
            trusted_now=clock.now,
        )
        owner = OwnerPrincipal(
            tenant_id=tenant_id,
            owner_user_id=owner_user_id,
            oidc_subject="step38-coherent-owner-subject",
            display_name="Step 38 Coherent Owner",
        )
        awaiting_dashboard = backend.dashboard(owner)
        model_binding_hashes = tuple(
            sorted(binding.binding_hash for binding in bindings)
        )
        ui_awaiting_verified = _awaiting_dashboard_verified(
            awaiting_dashboard,
            proposal_hash=awaiting.proposal.proposal_hash,
            model_binding_hashes=model_binding_hashes,
            proposal_audit_event_hash=(
                proposal_audit_entry.envelope.event_hash
            ),
        )
        if not ui_awaiting_verified:
            raise Step38CoherentRuntimeError("UI_AWAITING_SNAPSHOT_FAILED")

        mark("COHERENT_APPROVAL_COMMIT_ACTIVATION_RECOVERY")
        approval_request = build_personal_memory_approval_request(
            awaiting,
            approval_nonce="step38-coherent-owner-approval",
            requested_at=now + timedelta(seconds=7),
        )
        clock.set(approval_request.requested_at)
        cross_user_approval_denied = False
        cross_user_approval_denial_hash = canonical_sha256(
            "step38-cross-user-approval-negative-missing"
        )
        try:
            approvals.approve(
                approval_request,
                authenticated_actor_user_id=step29.OTHER_USER,
            )
        except PersonalMemoryPatchLifecycleError as error:
            cross_user_approval_denied = (
                error.reason_code is Step30ReasonCode.APPROVAL_OWNER_MISMATCH
            )
            cross_user_approval_denial_hash = _denial_hash(
                operation="PERSONAL_MEMORY_OWNER_APPROVAL",
                request_hash=approval_request.request_hash,
                authenticated_actor_id=step29.OTHER_USER,
                reason_code=error.reason_code.value,
            )
        if not cross_user_approval_denied:
            raise Step38CoherentRuntimeError("CROSS_USER_APPROVAL_NOT_DENIED")
        approved, approval_receipt, approval_replay = approvals.approve(
            approval_request,
            authenticated_actor_user_id=owner_user_id,
        )
        commit_request = build_personal_memory_commit_request(
            approved,
            commit_idempotency_key="step38-coherent-technical-commit",
            requested_at=approved.updated_at + timedelta(seconds=1),
        )
        clock.set(commit_request.requested_at)
        committed, commit_receipt, commit_replay = commits.commit(commit_request)
        quota_before_activation = personal.quota_usage(
            tenant_id=tenant_id,
            owner_user_id=owner_user_id,
            personal_memory_space_id=slot.personal_memory_space_id,
        )
        activation_request = build_personal_memory_activation_request(
            committed,
            activation_idempotency_key="step38-coherent-activation",
            requested_at=committed.updated_at + timedelta(seconds=1),
        )
        clock.set(activation_request.requested_at)
        acknowledgement_loss_runner = _ActivationAcknowledgementLossRunner(
            commit_runner
        )
        acknowledgement_loss_activation = PersonalMemoryActivationService(
            acknowledgement_loss_runner,
            trusted_clock=clock,
        )
        acknowledgement_lost = False
        try:
            acknowledgement_loss_activation.activate(activation_request)
        except _AcknowledgementLostAfterDurableActivation:
            acknowledgement_lost = True
        if not acknowledgement_lost:
            raise Step38CoherentRuntimeError("ACTIVATION_ACK_LOSS_NOT_INJECTED")
        active, activation_receipt, activation_replay = (
            acknowledgement_loss_activation.activate(activation_request)
        )
        activation_ack_lost_recovered = (
            activation_replay
            and acknowledgement_loss_runner.attempt_count == 2
            and active.activation_receipt == activation_receipt
            and active.state is PatchState.ACTIVE
        )
        if (
            approval_replay
            or commit_replay
            or not activation_ack_lost_recovered
        ):
            raise Step38CoherentRuntimeError("LIFECYCLE_RECOVERY_FAILED")
        hashes = {
            awaiting.proposal.proposal_statement_sha256,
            active.committed_patch.patch_statement_sha256,
            activation_receipt.patch_statement_sha256,
        }
        if len(hashes) != 1 or active.canonical_evidence:
            raise Step38CoherentRuntimeError("LIFECYCLE_CONTENT_IDENTITY_FAILED")

        mark("COHERENT_LIFECYCLE_AUDIT_AND_DURABLE_OBSERVATION")
        approval_audit_entry = _append_typed_audit(
            audit,
            approval_receipt_event(approval_receipt),
        )
        commit_audit_entry = _append_typed_audit(
            audit,
            commit_receipt_event(commit_receipt),
        )
        activation_audit_entry = _append_typed_audit(
            audit,
            activation_receipt_event(activation_receipt),
        )
        lifecycle_audit_hashes = (
            approval_audit_entry.envelope.event_hash,
            commit_audit_entry.envelope.event_hash,
            activation_audit_entry.envelope.event_hash,
        )
        lifecycle_audit_events_distinct = len(set(lifecycle_audit_hashes)) == 3
        if not lifecycle_audit_events_distinct:
            raise Step38CoherentRuntimeError("LIFECYCLE_AUDIT_EVENTS_NOT_DISTINCT")
        activation_recovery_observation = _observe_activation_recovery(
            root=root,
            database=database,
            app_runner=app_runner,
            personal=personal,
            tenant_id=tenant_id,
            owner_user_id=owner_user_id,
            slot=slot,
            awaiting=awaiting,
            active=active,
            approval_request=approval_request,
            approval_receipt=approval_receipt,
            commit_request=commit_request,
            commit_receipt=commit_receipt,
            activation_request=activation_request,
            activation_receipt=activation_receipt,
            activation_audit_event_hash=(
                activation_audit_entry.envelope.event_hash
            ),
            quota_before=quota_before_activation,
            cross_user_approval_denied=cross_user_approval_denied,
            replay_returned_existing_receipt=activation_ack_lost_recovered,
        )
        activation_recovery = FailureRecoveryCaseResult.build(
            case_id="personal-memory.step38-activation-ack-lost",
            failure_domain=FailureDomain.PERSONAL_MEMORY_ACTIVATION,
            failure_point=FailurePoint.PM_AFTER_ACTIVATION_ACK_LOST,
            subject_hash=activation_receipt.receipt_hash,
            attempt_count=acknowledgement_loss_runner.attempt_count,
            recovery_status=RecoveryStatus.RECOVERED_BY_IDEMPOTENT_REPLAY,
            final_semantic_state="ACTIVE_ONCE_DURABLE_STATE_RECONCILED",
            duplicate_side_effect_count=(
                activation_recovery_observation.duplicate_side_effect_count
            ),
            authority_violation_count=(
                activation_recovery_observation.authority_violation_count
            ),
            integrity_violation_count=(
                activation_recovery_observation.integrity_violation_count
            ),
            reason_codes=(
                "ACTIVATION_ACK_LOST_DURABLE_OBSERVATION_RECONCILED",
            ),
        )
        if any(
            (
                activation_recovery.duplicate_side_effect_count,
                activation_recovery.authority_violation_count,
                activation_recovery.integrity_violation_count,
            )
        ):
            raise Step38CoherentRuntimeError(
                "ACTIVATION_RECOVERY_DURABLE_OBSERVATION_FAILED"
            )

        mark("COHERENT_ACTIVE_RETRIEVAL_AND_ISOLATION_NEGATIVES")
        retrieval = ActivePatchRetrievalService(app_runner)
        first_request, first_result, first_context = _retrieval(
            retrieval,
            scenario,
            slot,
            bindings[0],
            later_query_text,
        )
        _second_request, second_result, second_context = _retrieval(
            retrieval,
            scenario,
            slot,
            bindings[1],
            later_query_text,
        )
        first_hashes = tuple(item.patch_hash for item in first_result.eligible_patches)
        second_hashes = tuple(item.patch_hash for item in second_result.eligible_patches)
        cross_model_same_patch = (
            first_hashes
            == second_hashes
            == (active.committed_patch.patch_hash,)
            and first_context.canonical_evidence_authority is False
            and second_context.canonical_evidence_authority is False
        )
        denied_binding = replace(
            bindings[0],
            provider_id="provider-step38-disallowed",
            model_id="model-step38-disallowed",
            model_revision_or_declared_version="revision-denied",
            binding_version=bindings[0].binding_version + 1,
        )
        _denied_model_request, denied_model_result, denied_model_context = _retrieval(
            retrieval,
            scenario,
            slot,
            denied_binding,
            later_query_text,
        )
        disallowed_model_denied = (
            not denied_model_result.eligible_patches
            and not denied_model_context.ordered_active_patches
            and any(
                Step31ReasonCode.MODEL_BINDING_MISMATCH
                in item.reason_codes
                for item in denied_model_result.excluded_assessments
            )
        )
        if not disallowed_model_denied:
            raise Step38CoherentRuntimeError("DISALLOWED_MODEL_RETRIEVED_PATCH")
        other_route, other_temporal = _rebind_later_identity(
            scenario,
            tenant_id=tenant_id,
            user_id=step29.OTHER_USER,
        )
        _other_request, other_result, _other_context = _retrieval(
            retrieval,
            scenario,
            slot,
            bindings[0],
            later_query_text,
            route=other_route,
            temporal=other_temporal,
        )
        tenant_route, tenant_temporal = _rebind_later_identity(
            scenario,
            tenant_id=step29.OTHER_TENANT,
            user_id=step29.OTHER_TENANT_USER,
        )
        _tenant_request, tenant_result, _tenant_context = _retrieval(
            retrieval,
            scenario,
            slot,
            bindings[0],
            later_query_text,
            route=tenant_route,
            temporal=tenant_temporal,
        )
        cross_user_denied = not other_result.eligible_patches
        cross_tenant_denied = not tenant_result.eligible_patches

        mark("COHERENT_CANONICAL_CONFLICT_AND_EXPORT_NEGATIVES")
        conflict_temporal = replace(
            scenario.later_temporal_result,
            evidence_status=EvidenceStatus.CONFLICTING,
            reason_codes=tuple(
                sorted(
                    set(scenario.later_temporal_result.reason_codes)
                    | {
                        Step21ReasonCode.EVIDENCE_CONFLICTING,
                        Step21ReasonCode.MATERIAL_CONFLICT,
                    },
                    key=lambda item: item.value,
                )
            ),
            limitations=tuple(
                sorted(
                    set(scenario.later_temporal_result.limitations)
                    | {"STEP38_SYNTHETIC_CANONICAL_CONFLICT_EDGE"}
                )
            ),
        )
        active_state_hash_before_conflict = active.state_hash
        _conflict_request, conflict_result, conflict_context = _retrieval(
            retrieval,
            scenario,
            slot,
            bindings[0],
            later_query_text,
            temporal=conflict_temporal,
        )
        canonical_conflict_suppressed = (
            not conflict_result.eligible_patches
            and not conflict_context.ordered_active_patches
            and active.state_hash == active_state_hash_before_conflict
            and active.state is PatchState.ACTIVE
            and any(
                Step31ReasonCode.CANONICAL_EVIDENCE_CONFLICT
                in item.reason_codes
                and Step31ReasonCode.PATCH_SUPPRESSED_BY_CANONICAL_EVIDENCE
                in item.reason_codes
                for item in conflict_result.excluded_assessments
            )
        )
        if not canonical_conflict_suppressed:
            raise Step38CoherentRuntimeError(
                "CANONICAL_EVIDENCE_CONFLICT_NOT_SUPPRESSED"
            )

        export_request = build_lifecycle_export_request(
            slot,
            requested_at=active.updated_at + timedelta(seconds=1),
            idempotency_key="step38-coherent-cross-user-export-negative",
        )
        clock.set(export_request.requested_at)
        cross_user_export_denied = False
        cross_user_export_denial_hash = canonical_sha256(
            "step38-cross-user-export-negative-missing"
        )
        try:
            lifecycle.export(
                export_request,
                authenticated_owner_user_id=step29.OTHER_USER,
            )
        except PersonalMemoryStep32Error as error:
            cross_user_export_denied = (
                error.reason_code is Step32ReasonCode.EXPORT_OWNER_MISMATCH
            )
            cross_user_export_denial_hash = _denial_hash(
                operation="PERSONAL_MEMORY_OWNER_EXPORT",
                request_hash=export_request.request_hash,
                authenticated_actor_id=step29.OTHER_USER,
                reason_code=error.reason_code.value,
            )
        if not cross_user_export_denied:
            raise Step38CoherentRuntimeError("CROSS_USER_EXPORT_NOT_DENIED")
        mark("COHERENT_SHARED_PROMOTION_AND_REVIEW")
        privacy = assess_shared_promotion_privacy(
            active.committed_patch.patch_statement,
            private_identifiers=(owner_user_id,),
        )
        consent = build_shared_promotion_consent(
            active,
            privacy,
            target_hat_id="shared-review-hat-step38",
            consent_nonce="step38-coherent-shared-consent",
            authenticated_owner_user_id=owner_user_id,
            consented_at=active.updated_at + timedelta(seconds=1),
        )
        promotion_request = build_shared_promotion_request(
            active,
            target_hat_id="shared-review-hat-step38",
            promotion_purpose="Bounded Step 38 review-only candidate",
            promotion_scope=active.committed_patch.patch_scope,
            deidentification=privacy,
            owner_consent=consent,
            canonical_evidence_compatibility=CanonicalEvidenceCompatibility.MATCH,
            reason_codes=(
                Step32ReasonCode.CANONICAL_EVIDENCE_AUTHORITY_NOT_GRANTED,
                Step32ReasonCode.SHARED_PROMOTION_PROPOSED,
            ),
            requested_at=consent.consented_at,
            idempotency_key="step38-coherent-shared-promotion",
        )
        clock.set(promotion_request.requested_at)
        promotion, promotion_replay = lifecycle.propose_shared(
            promotion_request,
            authenticated_owner_user_id=owner_user_id,
        )
        if (
            promotion_replay
            or not promotion.review_required
            or promotion.shared_active
            or promotion.source_registry_published
            or promotion.canonical_evidence
        ):
            raise Step38CoherentRuntimeError("SHARED_PROMOTION_BOUNDARY_FAILED")

        promotion_entry = _append_audit(
            audit,
            event_type=AuditEventType.SHARED_PROMOTION_PROPOSED,
            subject_type=AuditSubjectType.SHARED_PROMOTION_PROPOSAL,
            subject_id=promotion.promotion_id,
            subject_hash=promotion.proposal_hash,
            tenant_id=tenant_id,
            owner_user_id=owner_user_id,
            space_id=slot.personal_memory_space_id,
            actor_type=AuditActorType.HUMAN_USER,
            actor_id=owner_user_id,
            idempotency_key="step38-coherent-audit-shared-promotion",
            occurred_at=promotion.created_at,
            request_id=upstream.route.request_id,
            route_hash=upstream.route.route_hash,
            lineage_hashes={"active_patch_hash": active.committed_patch.patch_hash},
        )
        source_verification = audit.verify_chain(
            tenant_id=tenant_id,
            owner_user_id=owner_user_id,
            authenticated_tenant_id=tenant_id,
            authenticated_owner_user_id=owner_user_id,
        )
        if (
            not source_verification.verified
            or source_verification.last_hash != promotion_entry.envelope.event_hash
        ):
            raise Step38CoherentRuntimeError("SOURCE_AUDIT_CHAIN_INVALID")

        review_time = promotion.created_at + timedelta(seconds=10)
        case, source_context = shared_promotion_review_case(
            promotion,
            source_audit_event_hash=promotion_entry.envelope.event_hash,
            audit_verification=source_verification,
            created_at=review_time - timedelta(seconds=1),
        )
        review_clock = _MutableClock(review_time)
        intake = ReviewCaseIntakeService(
            review_service_runner,
            trusted_clock=review_clock,
        )
        workspace = HumanReviewWorkspaceService(
            reviewer_runner,
            trusted_clock=review_clock,
        )
        principal = ReviewerPrincipal(
            schema_version=STEP34_SCHEMA_VERSION,
            tenant_id=tenant_id,
            reviewer_id=_REVIEWER_ID,
            reviewer_role=ReviewerRole.SENIOR_REVIEWER,
            authentication_context_hash=canonical_sha256(
                {
                    "authenticated": True,
                    "reviewer_id": _REVIEWER_ID,
                    "tenant_id": tenant_id,
                }
            ),
            authenticated_at=review_time - timedelta(seconds=2),
        )
        step34._insert_authorization(
            root,
            database,
            build_reviewer_authorization(
                principal,
                case_type=case.case_type,
                owner_user_id=owner_user_id,
                granted_at=review_time - timedelta(seconds=2),
            ),
        )
        stored_case, case_replay, _ = intake.create_case(
            case,
            source_context,
            authenticated_tenant_id=tenant_id,
            authenticated_owner_user_id=owner_user_id,
        )
        queue = workspace.list_queue(
            ReviewQueueRequest(
                schema_version=STEP34_SCHEMA_VERSION,
                tenant_id=tenant_id,
                reviewer_principal_hash=principal.principal_hash,
                reviewer_id=principal.reviewer_id,
                reviewer_role=principal.reviewer_role,
                case_types=(case.case_type,),
                page_size=8,
                continuation=None,
                requested_at=review_time,
            ),
            principal,
        )
        detail = workspace.get_detail(
            tenant_id=tenant_id,
            review_case_id=stored_case.review_case_id,
            principal=principal,
        )
        if (
            case_replay
            or len(queue.items) != 1
            or queue.items[0].review_case_id != stored_case.review_case_id
            or not detail.audit_context_verified
            or detail.review_case.case_hash != stored_case.case_hash
        ):
            raise Step38CoherentRuntimeError("REVIEW_WORKSPACE_PROOF_FAILED")

        mark("COHERENT_FINAL_AUDIT_EXPORT_AND_ACTIVE_UI")
        final_verification = audit.verify_chain(
            tenant_id=tenant_id,
            owner_user_id=owner_user_id,
            authenticated_tenant_id=tenant_id,
            authenticated_owner_user_id=owner_user_id,
        )
        if (
            not final_verification.verified
            or final_verification.first_hash is None
            or final_verification.last_sequence is None
            or final_verification.event_count < len(lifecycle_audit_hashes)
        ):
            raise Step38CoherentRuntimeError("FINAL_AUDIT_CHAIN_INVALID")
        audit_export_request = AuditExportRequest(
            tenant_id=tenant_id,
            requester_actor_type=AuditActorType.HUMAN_USER,
            requester_id=owner_user_id,
            owner_user_id=owner_user_id,
            chain_ids=(final_verification.chain_id,),
            start_sequence=1,
            end_sequence=final_verification.last_sequence,
            maximum_events=final_verification.event_count,
            redaction_profile=AuditRedactionProfile.HASH_ONLY,
            requested_at=review_time + timedelta(seconds=1),
        )
        audit_export = audit.export_chain(
            audit_export_request,
            authenticated_tenant_id=tenant_id,
            authenticated_owner_user_id=owner_user_id,
        )
        exported_event_hashes = {
            item.envelope.event_hash for item in audit_export.ordered_events
        }
        audit_export_hash_only = (
            audit_export.exported_event_count == final_verification.event_count
            and not audit_export.truncated
            and audit_export.owner_private
            and not audit_export.business_authority
            and all(
                item.redaction_profile is AuditRedactionProfile.HASH_ONLY
                and item.redacted
                and not item.payload_representation
                and item.original_payload_digest
                == item.envelope.event_payload_digest
                for item in audit_export.ordered_events
            )
            and all(
                result.verified for result in audit_export.verification_results
            )
            and set(lifecycle_audit_hashes).issubset(exported_event_hashes)
        )
        if not audit_export_hash_only:
            raise Step38CoherentRuntimeError("AUDIT_HASH_ONLY_EXPORT_INVALID")
        ordinary_review_denied = False
        try:
            row = app_runner.run(
                step29._context(tenant_id, owner_user_id),
                lambda transaction: transaction.fetch_one(
                    "SELECT count(*) AS row_count "
                    "FROM memory_patch.human_review_cases"
                ),
                operation_kind="STEP38_ORDINARY_REVIEW_READ_NEGATIVE",
            )
            ordinary_review_denied = row is None or int(row["row_count"]) == 0
        except BaseException as error:
            ordinary_review_denied = extract_sqlstate(error) in {"42501", "44000"}

        dashboard = backend.dashboard(owner)
        same_patch = tuple(
            item
            for item in dashboard.recent_patches
            if item.patch_hash == active.committed_patch.patch_hash
            and item.state == PatchState.ACTIVE.value
        )
        ui_active_verified = _active_dashboard_verified(
            dashboard,
            active_patch_hash=active.committed_patch.patch_hash,
            model_binding_hashes=model_binding_hashes,
            lifecycle_audit_event_hashes=lifecycle_audit_hashes,
        )
        if not ui_active_verified:
            raise Step38CoherentRuntimeError("UI_ACTIVE_SNAPSHOT_FAILED")
        other_dashboard = backend.dashboard(
            OwnerPrincipal(
                tenant_id=tenant_id,
                owner_user_id=step29.OTHER_USER,
                oidc_subject="step38-coherent-other-subject",
                display_name="Step 38 Other Owner",
            )
        )
        tenant_dashboard = backend.dashboard(
            OwnerPrincipal(
                tenant_id=step29.OTHER_TENANT,
                owner_user_id=step29.OTHER_TENANT_USER,
                oidc_subject="step38-coherent-tenant-b-subject",
                display_name="Step 38 Tenant B Owner",
            )
        )
        cross_user_denied = (
            cross_user_denied
            and not other_dashboard.slots
            and not other_dashboard.recent_patches
        )
        cross_tenant_denied = (
            cross_tenant_denied
            and not tenant_dashboard.slots
            and not tenant_dashboard.recent_patches
        )
        attestation = scenario.upstream_runtime_attestation
        primary_retrieval_proof = attestation.retrieval_proof
        later_retrieval_proof = scenario.later_retrieval_proof
        same_database = (
            primary_retrieval_proof.runtime_instance_digest
            == runtime_instance_digest
            and primary_retrieval_proof.database_instance_digest
            == database_instance_digest
            and later_retrieval_proof.runtime_instance_digest
            == runtime_instance_digest
            and later_retrieval_proof.database_instance_digest
            == database_instance_digest
        )
        proof_evidence_flags = (
            ("CROSS_MODEL_SAME_PATCH", cross_model_same_patch),
            ("DISALLOWED_MODEL_DENIED", disallowed_model_denied),
            ("CROSS_USER_DENIED", cross_user_denied),
            ("CROSS_TENANT_DENIED", cross_tenant_denied),
            ("CROSS_USER_APPROVAL_DENIED", cross_user_approval_denied),
            ("CROSS_USER_EXPORT_DENIED", cross_user_export_denied),
            ("CANONICAL_CONFLICT_SUPPRESSED", canonical_conflict_suppressed),
            ("ORDINARY_REVIEW_DENIED", ordinary_review_denied),
            ("AUDIT_CHAIN_VERIFIED", final_verification.verified),
            ("LIFECYCLE_AUDIT_DISTINCT", lifecycle_audit_events_distinct),
            ("AUDIT_EXPORT_HASH_ONLY", audit_export_hash_only),
            ("REVIEW_CONTEXT_VERIFIED", detail.audit_context_verified),
            ("UI_AWAITING_VERIFIED", ui_awaiting_verified),
            ("UI_ACTIVE_VERIFIED", ui_active_verified),
            ("UI_SINGLE_ACTIVE_PATCH", len(same_patch) == 1),
            ("ACTIVATION_ACK_RECOVERED", activation_ack_lost_recovered),
        )
        proof_evidence_complete = all(
            value for _name, value in proof_evidence_flags
        )
        if not proof_evidence_complete:
            failed_name = next(
                name for name, value in proof_evidence_flags if not value
            )
            raise Step38CoherentRuntimeError(
                f"COHERENT_PROOF_{failed_name}_FAILED"
            )
        closure_eligible = (
            attestation.real_retrieval_lineage
            and later_retrieval_proof.closure_eligible
            and same_database
            and proof_evidence_complete
        )
        mark("COHERENT_PROOF_ASSEMBLY")
        try:
            proof = Step38CoherentRuntimeProof(
            proof_version=STEP38_COHERENT_RUNTIME_PROOF_VERSION,
            scenario_hash=scenario.scenario_hash,
            upstream_runtime_attestation_hash=attestation.attestation_hash,
            primary_retrieval_proof_hash=primary_retrieval_proof.proof_hash,
            later_retrieval_proof_hash=later_retrieval_proof.proof_hash,
            runtime_instance_digest=runtime_instance_digest,
            database_instance_digest=database_instance_digest,
            tenant_id=tenant_id,
            owner_user_id=owner_user_id,
            request_id=upstream.route.request_id,
            personal_memory_space_id=slot.personal_memory_space_id,
            slot_hash=slot.slot_hash,
            candidate_hash=envelope.submission.candidate.content_hash,
            candidate_envelope_hash=envelope.envelope_hash,
            proposal_hash=awaiting.proposal.proposal_hash,
            validation_receipt_hash=validation_receipt.receipt_hash,
            approval_receipt_hash=approval_receipt.receipt_hash,
            commit_receipt_hash=commit_receipt.receipt_hash,
            activation_receipt_hash=activation_receipt.receipt_hash,
            active_patch_hash=active.committed_patch.patch_hash,
            later_active_patch_retrieval_request_hash=first_request.request_hash,
            disallowed_model_retrieval_hash=denied_model_result.result_hash,
            cross_user_approval_denial_hash=cross_user_approval_denial_hash,
            cross_user_export_denial_hash=cross_user_export_denial_hash,
            canonical_conflict_temporal_hash=conflict_temporal.result_hash,
            canonical_conflict_retrieval_hash=conflict_result.result_hash,
            activation_ack_lost_recovery_hash=activation_recovery.result_hash,
            activation_ack_lost_recovery_status=(
                activation_recovery.recovery_status.value
            ),
            activation_recovery_observation=activation_recovery_observation,
            activation_recovery_case_result=activation_recovery,
            activation_recovery_failure_point=activation_recovery.failure_point,
            duplicate_semantic_side_effect_count=(
                activation_recovery.duplicate_side_effect_count
            ),
            authority_violation_count=(
                activation_recovery.authority_violation_count
            ),
            integrity_violation_count=(
                activation_recovery.integrity_violation_count
            ),
            model_a_identity_digest=_provider(bindings[0]).identity_digest,
            model_a_retrieval_hash=first_result.result_hash,
            model_a_context_hash=first_context.context_hash,
            model_b_identity_digest=_provider(bindings[1]).identity_digest,
            model_b_retrieval_hash=second_result.result_hash,
            model_b_context_hash=second_context.context_hash,
            audit_chain_id=final_verification.chain_id,
            audit_verification_hash=final_verification.result_hash,
            audit_first_event_hash=final_verification.first_hash,
            audit_last_event_hash=final_verification.last_hash,
            approval_audit_event_hash=lifecycle_audit_hashes[0],
            commit_audit_event_hash=lifecycle_audit_hashes[1],
            activation_audit_event_hash=lifecycle_audit_hashes[2],
            audit_export_bundle_hash=audit_export.bundle_hash,
            shared_promotion_hash=promotion.proposal_hash,
            review_case_type=stored_case.case_type,
            review_case_hash=stored_case.case_hash,
            review_detail_hash=detail.detail_hash,
            ui_awaiting_dashboard_hash=_dashboard_hash(awaiting_dashboard),
            ui_dashboard_hash=_dashboard_hash(dashboard),
            cross_model_same_patch=cross_model_same_patch,
            disallowed_model_access_denied=disallowed_model_denied,
            cross_user_access_denied=cross_user_denied,
            cross_tenant_access_denied=cross_tenant_denied,
            cross_user_approval_denied=cross_user_approval_denied,
            cross_user_export_denied=cross_user_export_denied,
            canonical_evidence_conflict_suppressed=(
                canonical_conflict_suppressed
            ),
            ordinary_user_review_access_denied=ordinary_review_denied,
            audit_chain_verified=final_verification.verified,
            lifecycle_audit_events_distinct=lifecycle_audit_events_distinct,
            audit_export_hash_only=audit_export_hash_only,
            review_context_verified=detail.audit_context_verified,
            ui_awaiting_state_verified=ui_awaiting_verified,
            ui_active_state_verified=ui_active_verified,
            ui_same_active_patch=len(same_patch) == 1,
            activation_ack_lost_recovered=activation_ack_lost_recovered,
            real_second_model_inference_status=(
                Step38RealSecondModelInferenceStatus.NOT_REQUIRED_PROVIDER_NEUTRAL_RETRIEVAL_ONLY
            ),
            canonical_evidence_authority=False,
            source_publication_authority=False,
            external_execution_authority=False,
            real_retrieval_lineage=attestation.real_retrieval_lineage,
            later_real_retrieval_lineage=later_retrieval_proof.closure_eligible,
            upstream_and_downstream_same_database=same_database,
            step39_started=False,
                closure_eligible=closure_eligible,
            )
        except ContractValidationError as error:
            raise Step38CoherentRuntimeError(
                "COHERENT_PROOF_CONTRACT_INVALID"
            ) from error
        mark("COHERENT_PROOF_ASSEMBLY", "PASS")
        return proof
    finally:
        mark("COHERENT_ROLE_CLEANUP")
        for role, memberships in reversed(created):
            if role == commit_role:
                step30._drop_commit_validation_role(root, role)
            elif role == app_role:
                step27._drop_validation_role(root, role)
            else:
                assert memberships is not None
                step34._drop_validation_role(root, role, memberships)
        mark("COHERENT_ROLE_CLEANUP", "PASS")


def run_coherent_disposable_lineage(
    scenario: Step38PersonalMemoryScenario,
    *,
    later_query_text: str,
    cockroach_binary: Path = DEFAULT_COCKROACH_BINARY,
) -> Step38CoherentRuntimeProof:
    """Start, migrate, exercise, and clean one exact owned local runtime."""

    source = cockroach_binary.expanduser().resolve()
    identity = migrations.verify_binary_identity(source)
    if identity["binary_sha256"] != step27.EXPECTED_COCKROACH_SHA256:
        raise Step38CoherentRuntimeError("COCKROACH_BINARY_DIGEST_MISMATCH")
    runtime = None
    root = None
    database = None
    proof = None
    cleanup = None
    with tempfile.TemporaryDirectory(prefix="mp-step38-coherent-binary-", dir="/tmp") as temp:
        local_binary = Path(temp) / "cockroach"
        shutil.copy2(source, local_binary)
        # Step 38 adds no migration.  Reuse the latest already-approved
        # disposable-path prefix while retaining an explicit Step 38 suffix.
        run_id = "mp_step37_s38_" + uuid.uuid4().hex[:12]
        runtime = migrations.LocalRuntime(local_binary, run_id)
        try:
            root = step18._start_disposable_runtime(runtime)
            database = run_id + "_db"
            migrations.create_database(root, database)
            applied = migrations.apply_migrations(root, database, timeout=300)
            replay = migrations.apply_migrations(root, database, timeout=300)
            expected = len(migrations.load_migrations())
            if (
                len(applied["applied"]) != expected
                or replay["applied"]
                or len(replay["skipped"]) != expected
            ):
                raise Step38CoherentRuntimeError("MIGRATION_REPLAY_MISMATCH")
            runtime_digest = canonical_sha256(
                {
                    "run_id": run_id,
                    "binary_sha256": identity["binary_sha256"],
                    "sql_port": runtime.sql_port,
                }
            )
            database_digest = canonical_sha256(
                {"runtime_instance_digest": runtime_digest, "database": database}
            )
            proof = run_coherent_lineage_on_owned_database(
                scenario,
                root=root,
                database=database,
                runtime_instance_digest=runtime_digest,
                database_instance_digest=database_digest,
                later_query_text=later_query_text,
            )
        finally:
            if root is not None and database is not None:
                migrations.drop_database(root, database, timeout=180)
            if runtime is not None and runtime.process is not None:
                cleanup = step18._stop_owned_runtime(runtime)
    if proof is None:
        raise Step38CoherentRuntimeError("COHERENT_PROOF_MISSING")
    if cleanup is None or not all(
        cleanup.get(name) is expected
        for name, expected in (
            ("pid_exited", True),
            ("ports_closed", True),
            ("temporary_store_removed", True),
            ("force_kill_used", False),
        )
    ):
        raise Step38CoherentRuntimeError("OWNED_RUNTIME_CLEANUP_INCOMPLETE")
    return proof


__all__ = [
    "DEFAULT_COCKROACH_BINARY",
    "Step38CoherentRuntimeError",
    "run_coherent_disposable_lineage",
    "run_coherent_lineage_on_owned_database",
]
