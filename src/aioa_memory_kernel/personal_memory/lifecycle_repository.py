"""CockroachDB persistence for the receipt-gated Step 30 lifecycle."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from aioa_memory_kernel.contracts.enums import ActorType, PatchState
from aioa_memory_kernel.contracts.exceptions import IntegrityError
from aioa_memory_kernel.contracts.serialization import (
    canonical_json,
    canonical_sha256,
    to_canonical_data,
)
from aioa_memory_kernel.persistence.errors import (
    ImmutableRecordConflictError,
    PersistenceConfigurationError,
)
from aioa_memory_kernel.persistence.protocols import TransactionProtocol

from .lifecycle import (
    PERSONAL_MEMORY_ACTIVATION_ACTOR_ID,
    PERSONAL_MEMORY_COMMIT_ACTOR_ID,
    PERSONAL_MEMORY_COMMIT_ROLE,
    PERSONAL_MEMORY_PATCH_LIFECYCLE_CONTRACT_TYPE,
    STEP30_SCHEMA_VERSION,
    CommittedPersonalMemoryPatch,
    PersonalMemoryApprovalReceipt,
    PersonalMemoryCommitReceipt,
    PersonalMemoryPatchLifecycleState,
    parse_personal_memory_patch_lifecycle_state,
    personal_memory_patch_lifecycle_to_jsonb,
    verify_personal_memory_approval_receipt,
    verify_personal_memory_commit_receipt,
    verify_personal_memory_patch_lifecycle_state,
)
from .proposal_repository import proposal_state_from_row
from .proposals import PersonalMemoryPatchProposalState


_SELECT = """
    SELECT proposal_id, proposed_content, lifecycle_state, content_hash,
           step29_dedup_key, step29_state_version, step29_state_hash,
           step29_candidate_id, step29_candidate_hash,
           step29_candidate_envelope_hash, step29_target_binding_hash,
           step29_evidence_binding_hash, step29_validation_receipt_hash,
           step30_approval_id, step30_approval_receipt_hash,
           step30_commit_receipt_hash, step30_activation_receipt_hash,
           step30_patch_id
      FROM memory_patch.memory_patch_proposals
"""


def _json_object(value: object, context: str) -> Mapping[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise PersistenceConfigurationError(
                f"{context} JSON is invalid",
                sanitized_code="INVALID_STEP30_LIFECYCLE_ROW",
            ) from exc
    if not isinstance(value, Mapping):
        raise PersistenceConfigurationError(
            f"{context} must be an object",
            sanitized_code="INVALID_STEP30_LIFECYCLE_ROW",
        )
    return value


def lifecycle_state_from_row(
    row: Mapping[str, object],
) -> PersonalMemoryPatchProposalState | PersonalMemoryPatchLifecycleState:
    raw = _json_object(row["proposed_content"], "proposed_content")
    contract_type = raw.get("contract_type")
    if contract_type == "PersonalMemoryPatchProposalState":
        return proposal_state_from_row(row)
    if contract_type != PERSONAL_MEMORY_PATCH_LIFECYCLE_CONTRACT_TYPE:
        raise PersistenceConfigurationError(
            "proposal row is not a Step 29/30 lifecycle",
            sanitized_code="INVALID_STEP30_LIFECYCLE_ROW",
        )
    state = parse_personal_memory_patch_lifecycle_state(raw)
    proposal = state.proposal
    expected_commit = (
        None if state.commit_receipt is None else state.commit_receipt.receipt_hash
    )
    expected_activation = (
        None
        if state.activation_receipt is None
        else state.activation_receipt.receipt_hash
    )
    expected_patch = (
        None if state.committed_patch is None else state.committed_patch.patch_id
    )
    if (
        str(row["proposal_id"]) != proposal.proposal_id
        or str(row["content_hash"]) != proposal.proposal_hash
        or str(row["lifecycle_state"]) != state.state.value
        or int(row["step29_state_version"]) != state.state_version
        or str(row["step29_state_hash"]) != state.state_hash
        or row["step30_approval_id"] != state.approval_receipt.approval_id
        or row["step30_approval_receipt_hash"]
        != state.approval_receipt.receipt_hash
        or row["step30_commit_receipt_hash"] != expected_commit
        or row["step30_activation_receipt_hash"] != expected_activation
        or row["step30_patch_id"] != expected_patch
        or row["step29_evidence_binding_hash"]
        != state.step29_state.evidence_binding.binding_hash
        or row["step29_validation_receipt_hash"]
        != state.step29_state.validation_receipt.receipt_hash
    ):
        raise IntegrityError("persisted Step 30 lifecycle is detached")
    verify_personal_memory_patch_lifecycle_state(state)
    return state


def step30_transition_id(
    state_before: PatchState,
    state_after: PatchState,
    proposal_id: str,
    proposal_hash: str,
    state_version: int,
    receipt_hash: str,
) -> str:
    return "step30-transition-" + canonical_sha256(
        {
            "state_before": state_before,
            "state_after": state_after,
            "proposal_id": proposal_id,
            "proposal_hash": proposal_hash,
            "state_version": state_version,
            "receipt_hash": receipt_hash,
        }
    )


class PersonalMemoryPatchLifecycleCockroachRepository:
    """Narrow Step 30 persistence; there is no retrieval/ranking API."""

    @classmethod
    def get_state(
        cls,
        transaction: TransactionProtocol,
        tenant_id: str,
        owner_user_id: str,
        proposal_id: str,
    ) -> PersonalMemoryPatchProposalState | PersonalMemoryPatchLifecycleState | None:
        row = transaction.fetch_one(
            _SELECT
            + """
             WHERE tenant_id = %s
               AND owner_user_id = %s
               AND proposal_id = %s
               AND target_scope = 'USER_PERSONAL_HAT'
               AND proposed_content->>'contract_type' IN (
                 'PersonalMemoryPatchProposalState',
                 'PersonalMemoryPatchLifecycleState'
               )
            """,
            (tenant_id, owner_user_id, proposal_id),
        )
        return None if row is None else lifecycle_state_from_row(row)

    @staticmethod
    def get_approval_replay(
        transaction: TransactionProtocol,
        *,
        tenant_id: str,
        owner_user_id: str,
        replay_identity: str,
    ) -> Mapping[str, object] | None:
        return transaction.fetch_one(
            """
            SELECT proposal_id, step30_request_hash,
                   step30_approval_receipt_hash
              FROM memory_patch.memory_patch_approvals
             WHERE tenant_id = %s AND owner_user_id = %s
               AND step30_approval_replay_identity = %s
            """,
            (tenant_id, owner_user_id, replay_identity),
        )

    @staticmethod
    def insert_approval(
        transaction: TransactionProtocol,
        receipt: PersonalMemoryApprovalReceipt,
    ) -> None:
        verify_personal_memory_approval_receipt(receipt)
        row = transaction.fetch_one(
            """
            INSERT INTO memory_patch.memory_patch_approvals (
              tenant_id, approval_id, schema_version, proposal_id,
              proposal_content_hash, target_scope, owner_user_id,
              personal_memory_space_id, decision, approver_type, approver_id,
              reason_code, decided_at, approval_proof,
              step30_request_hash, step30_evidence_binding_hash,
              step30_validation_receipt_hash,
              step30_approval_replay_identity,
              step30_approval_receipt_hash, step30_approval_payload
            ) VALUES (
              %s, %s, %s, %s, %s, 'USER_PERSONAL_HAT', %s, %s,
              'APPROVE', 'USER', %s, 'APPROVAL_GRANTED', %s, %s,
              %s, %s, %s, %s, %s, %s::JSONB
            )
            ON CONFLICT DO NOTHING
            RETURNING approval_id
            """,
            (
                receipt.tenant_id,
                receipt.approval_id,
                STEP30_SCHEMA_VERSION,
                receipt.proposal_id,
                receipt.proposal_hash,
                receipt.owner_user_id,
                receipt.personal_memory_space_id,
                receipt.actor_id,
                receipt.approved_at,
                receipt.receipt_hash,
                receipt.request_hash,
                receipt.evidence_binding_hash,
                receipt.validation_receipt_hash,
                receipt.approval_replay_identity,
                receipt.receipt_hash,
                canonical_json(receipt),
            ),
        )
        if row is None:
            existing = transaction.fetch_one(
                """
                SELECT approval_id, step30_request_hash,
                       step30_approval_receipt_hash
                  FROM memory_patch.memory_patch_approvals
                 WHERE tenant_id = %s AND owner_user_id = %s
                   AND step30_approval_replay_identity = %s
                """,
                (
                    receipt.tenant_id,
                    receipt.owner_user_id,
                    receipt.approval_replay_identity,
                ),
            )
            if (
                existing is None
                or existing["approval_id"] != receipt.approval_id
                or existing["step30_request_hash"] != receipt.request_hash
                or existing["step30_approval_receipt_hash"]
                != receipt.receipt_hash
            ):
                raise ImmutableRecordConflictError(
                    "approval replay identity conflicts",
                    sanitized_code="APPROVAL_REPLAY_CONFLICT",
                )

    @staticmethod
    def get_commit_replay(
        transaction: TransactionProtocol,
        *,
        tenant_id: str,
        owner_user_id: str,
        replay_identity: str,
    ) -> Mapping[str, object] | None:
        return transaction.fetch_one(
            """
            SELECT proposal_id, step30_request_hash, step30_commit_receipt_hash
              FROM memory_patch.memory_patch_commits
             WHERE tenant_id = %s AND owner_user_id = %s
               AND step30_commit_replay_identity = %s
            """,
            (tenant_id, owner_user_id, replay_identity),
        )

    @staticmethod
    def assert_commit_helper_authority(transaction: TransactionProtocol) -> None:
        row = transaction.fetch_one(
            "SELECT memory_patch.step30_commit_helper_authorized() AS authorized"
        )
        if row is None or row.get("authorized") not in (True, "true", "t", 1):
            raise PersistenceConfigurationError(
                "dedicated commit helper authority is required",
                sanitized_code="COMMIT_HELPER_AUTHORITY_REQUIRED",
            )

    @staticmethod
    def insert_commit_and_patch(
        transaction: TransactionProtocol,
        state: PersonalMemoryPatchLifecycleState,
    ) -> None:
        verify_personal_memory_patch_lifecycle_state(state)
        if state.state is not PatchState.COMMITTED:
            raise IntegrityError("commit persistence requires COMMITTED")
        receipt = state.commit_receipt
        patch = state.committed_patch
        verify_personal_memory_commit_receipt(receipt)
        row = transaction.fetch_one(
            """
            INSERT INTO memory_patch.memory_patch_commits (
              tenant_id, commit_id, schema_version, proposal_id,
              proposal_content_hash, target_scope, approval_id,
              approval_proof, approval_decision, committed_patch_id,
              owner_user_id, personal_memory_space_id, actor_type, actor_id,
              storage_class, committed_at, commit_hash,
              step30_request_hash, step30_validation_receipt_hash,
              step30_approval_receipt_hash, step30_commit_replay_identity,
              step30_patch_hash, step30_commit_receipt_hash,
              step30_commit_payload
            ) VALUES (
              %s, %s, %s, %s, %s, 'USER_PERSONAL_HAT', %s, %s, 'APPROVE',
              %s, %s, %s, 'COMMIT_SERVICE', %s, 'CRDB_TRANSACTIONAL', %s,
              %s, %s, %s, %s, %s, %s, %s, %s::JSONB
            )
            ON CONFLICT DO NOTHING
            RETURNING commit_id
            """,
            (
                receipt.tenant_id,
                receipt.commit_id,
                STEP30_SCHEMA_VERSION,
                receipt.proposal_id,
                receipt.proposal_hash,
                state.approval_receipt.approval_id,
                receipt.approval_receipt_hash,
                patch.patch_id,
                receipt.owner_user_id,
                receipt.personal_memory_space_id,
                PERSONAL_MEMORY_COMMIT_ACTOR_ID,
                receipt.committed_at,
                receipt.receipt_hash,
                receipt.request_hash,
                receipt.validation_receipt_hash,
                receipt.approval_receipt_hash,
                receipt.commit_replay_identity,
                patch.patch_hash,
                receipt.receipt_hash,
                canonical_json(
                    {"committed_patch": patch, "commit_receipt": receipt}
                ),
            ),
        )
        if row is None:
            existing = transaction.fetch_one(
                """
                SELECT commit_id, step30_request_hash,
                       step30_commit_receipt_hash, step30_patch_hash
                  FROM memory_patch.memory_patch_commits
                 WHERE tenant_id = %s AND owner_user_id = %s
                   AND step30_commit_replay_identity = %s
                """,
                (
                    receipt.tenant_id,
                    receipt.owner_user_id,
                    receipt.commit_replay_identity,
                ),
            )
            if (
                existing is None
                or existing["commit_id"] != receipt.commit_id
                or existing["step30_request_hash"] != receipt.request_hash
                or existing["step30_commit_receipt_hash"] != receipt.receipt_hash
                or existing["step30_patch_hash"] != patch.patch_hash
            ):
                raise ImmutableRecordConflictError(
                    "commit replay identity conflicts",
                    sanitized_code="COMMIT_REPLAY_CONFLICT",
                )
        evidence = tuple(
            item.evidence_link_hash
            for item in state.step29_state.evidence_binding.ordered_evidence_references
        )
        item = transaction.fetch_one(
            """
            INSERT INTO memory_patch.memory_items (
              tenant_id, memory_item_id, schema_version, hat_scope_id,
              target_scope, visibility, trust_class, content_kind, content,
              scope_dimensions, evidence_references, source_patch_id,
              valid_from, valid_until, expires_at, active, revoked, created_at,
              step30_proposal_id, step30_proposal_hash,
              step30_approval_receipt_hash, step30_validation_receipt_hash,
              step30_commit_receipt_hash, step30_patch_hash,
              step30_state_version, step30_state_hash,
              step30_activation_replay_identity,
              step30_activation_receipt_hash, step30_activation_payload
            ) VALUES (
              %s, %s, %s, %s, 'USER_PERSONAL_HAT', 'PERSONAL',
              'PERSONAL_VERIFIED_PATCH', 'FACTUAL', %s::JSONB, %s::JSONB,
              %s::JSONB, %s, NULL, NULL, NULL, false, false, %s,
              %s, %s, %s, %s, %s, %s, 6, %s, NULL, NULL, NULL
            )
            ON CONFLICT DO NOTHING
            RETURNING memory_item_id
            """,
            (
                patch.tenant_id,
                patch.patch_id,
                STEP30_SCHEMA_VERSION,
                patch.hat_scope_id,
                canonical_json(to_canonical_data(patch)),
                canonical_json(patch.patch_scope),
                canonical_json(evidence),
                patch.patch_id,
                patch.committed_at,
                patch.proposal_id,
                patch.proposal_hash,
                patch.approval_receipt_hash,
                patch.validation_receipt_hash,
                receipt.receipt_hash,
                patch.patch_hash,
                state.state_hash,
            ),
        )
        if item is None:
            existing = transaction.fetch_one(
                """
                SELECT memory_item_id, step30_patch_hash,
                       step30_commit_receipt_hash, active
                  FROM memory_patch.memory_items
                 WHERE tenant_id = %s AND memory_item_id = %s
                   AND hat_scope_id = %s
                """,
                (patch.tenant_id, patch.patch_id, patch.hat_scope_id),
            )
            if (
                existing is None
                or existing["step30_patch_hash"] != patch.patch_hash
                or existing["step30_commit_receipt_hash"] != receipt.receipt_hash
                or existing["active"] not in (False, "false", "f", 0)
            ):
                raise ImmutableRecordConflictError(
                    "committed patch identity conflicts",
                    sanitized_code="COMMIT_PATCH_CONFLICT",
                )

    @staticmethod
    def get_activation_replay(
        transaction: TransactionProtocol,
        *,
        tenant_id: str,
        patch_id: str,
        replay_identity: str,
    ) -> Mapping[str, object] | None:
        return transaction.fetch_one(
            """
            SELECT memory_item_id, step30_activation_replay_identity,
                   step30_activation_receipt_hash, step30_state_hash
              FROM memory_patch.memory_items
             WHERE tenant_id = %s AND memory_item_id = %s
               AND step30_activation_replay_identity = %s
            """,
            (tenant_id, patch_id, replay_identity),
        )

    @staticmethod
    def activate_patch(
        transaction: TransactionProtocol,
        state: PersonalMemoryPatchLifecycleState,
    ) -> None:
        verify_personal_memory_patch_lifecycle_state(state)
        if state.state is not PatchState.ACTIVE:
            raise IntegrityError("activation persistence requires ACTIVE")
        patch = state.committed_patch
        receipt = state.activation_receipt
        row = transaction.fetch_one(
            """
            UPDATE memory_patch.memory_items
               SET active = true,
                   step30_state_version = 7,
                   step30_state_hash = %s,
                   step30_activation_replay_identity = %s,
                   step30_activation_receipt_hash = %s,
                   step30_activation_payload = %s::JSONB
             WHERE tenant_id = %s AND memory_item_id = %s
               AND hat_scope_id = %s AND source_patch_id = %s
               AND step30_patch_hash = %s
               AND step30_commit_receipt_hash = %s
               AND step30_state_version = 6
               AND active = false AND revoked = false
            RETURNING memory_item_id
            """,
            (
                state.state_hash,
                receipt.activation_replay_identity,
                receipt.receipt_hash,
                canonical_json(receipt),
                patch.tenant_id,
                patch.patch_id,
                patch.hat_scope_id,
                patch.patch_id,
                patch.patch_hash,
                state.commit_receipt.receipt_hash,
            ),
        )
        if row is None:
            existing = transaction.fetch_one(
                """
                SELECT step30_activation_replay_identity,
                       step30_activation_receipt_hash, step30_state_hash, active
                  FROM memory_patch.memory_items
                 WHERE tenant_id = %s AND memory_item_id = %s
                """,
                (patch.tenant_id, patch.patch_id),
            )
            if (
                existing is None
                or existing["step30_activation_replay_identity"]
                != receipt.activation_replay_identity
                or existing["step30_activation_receipt_hash"]
                != receipt.receipt_hash
                or existing["step30_state_hash"] != state.state_hash
                or existing["active"] not in (True, "true", "t", 1)
            ):
                raise ImmutableRecordConflictError(
                    "activation replay identity conflicts",
                    sanitized_code="ACTIVATION_REPLAY_CONFLICT",
                )

    @staticmethod
    def transition_state(
        transaction: TransactionProtocol,
        *,
        expected: PersonalMemoryPatchProposalState | PersonalMemoryPatchLifecycleState,
        updated: PersonalMemoryPatchLifecycleState,
    ) -> PersonalMemoryPatchLifecycleState:
        verify_personal_memory_patch_lifecycle_state(updated)
        proposal = updated.proposal
        expected_state = expected.state
        expected_version = expected.state_version
        expected_hash = expected.state_hash
        row = transaction.fetch_one(
            """
            UPDATE memory_patch.memory_patch_proposals
               SET proposed_content = %s::JSONB,
                   lifecycle_state = %s,
                   step29_state_version = %s,
                   step29_state_hash = %s,
                   step30_approval_id = %s,
                   step30_approval_receipt_hash = %s,
                   step30_commit_receipt_hash = %s,
                   step30_activation_receipt_hash = %s,
                   step30_patch_id = %s
             WHERE tenant_id = %s AND owner_user_id = %s
               AND personal_memory_space_id = %s AND proposal_id = %s
               AND content_hash = %s AND lifecycle_state = %s
               AND step29_state_version = %s AND step29_state_hash = %s
            RETURNING proposal_id, proposed_content, lifecycle_state,
                      content_hash, step29_dedup_key, step29_state_version,
                      step29_state_hash, step29_candidate_id,
                      step29_candidate_hash, step29_candidate_envelope_hash,
                      step29_target_binding_hash, step29_evidence_binding_hash,
                      step29_validation_receipt_hash, step30_approval_id,
                      step30_approval_receipt_hash, step30_commit_receipt_hash,
                      step30_activation_receipt_hash, step30_patch_id
            """,
            (
                canonical_json(personal_memory_patch_lifecycle_to_jsonb(updated)),
                updated.state.value,
                updated.state_version,
                updated.state_hash,
                updated.approval_receipt.approval_id,
                updated.approval_receipt.receipt_hash,
                None
                if updated.commit_receipt is None
                else updated.commit_receipt.receipt_hash,
                None
                if updated.activation_receipt is None
                else updated.activation_receipt.receipt_hash,
                None
                if updated.committed_patch is None
                else updated.committed_patch.patch_id,
                proposal.tenant_id,
                proposal.owner_user_id,
                proposal.personal_memory_space_id,
                proposal.proposal_id,
                proposal.proposal_hash,
                expected_state.value,
                expected_version,
                expected_hash,
            ),
        )
        if row is None:
            raise ImmutableRecordConflictError(
                "Step 30 state changed concurrently",
                sanitized_code="STEP30_STATE_VERSION_CONFLICT",
            )
        stored = lifecycle_state_from_row(row)
        if not isinstance(stored, PersonalMemoryPatchLifecycleState):
            raise IntegrityError("Step 30 transition did not persist lifecycle state")
        return stored

    @staticmethod
    def insert_transition_event(
        transaction: TransactionProtocol,
        *,
        state: PersonalMemoryPatchLifecycleState,
        state_before: PatchState,
        receipt_hash: str,
        transitioned_at: datetime,
    ) -> str:
        state_after = state.state
        transition_id = step30_transition_id(
            state_before,
            state_after,
            state.proposal.proposal_id,
            state.proposal.proposal_hash,
            state.state_version,
            receipt_hash,
        )
        if state_after is PatchState.APPROVED:
            actor_type = ActorType.USER.value
            actor_id = state.proposal.owner_user_id
        elif state_after is PatchState.COMMITTED:
            actor_type = ActorType.COMMIT_SERVICE.value
            actor_id = PERSONAL_MEMORY_COMMIT_ACTOR_ID
        else:
            actor_type = ActorType.SYSTEM.value
            actor_id = PERSONAL_MEMORY_ACTIVATION_ACTOR_ID
        row = transaction.fetch_one(
            """
            INSERT INTO memory_patch.patch_transition_records (
              tenant_id, transition_id, proposal_id, proposal_content_hash,
              state_before, state_after, actor_type, actor_id, transitioned_at,
              step30_receipt_hash
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING RETURNING transition_id
            """,
            (
                state.proposal.tenant_id,
                transition_id,
                state.proposal.proposal_id,
                state.proposal.proposal_hash,
                state_before.value,
                state_after.value,
                actor_type,
                actor_id,
                transitioned_at,
                receipt_hash,
            ),
        )
        if row is None:
            existing = transaction.fetch_one(
                """
                SELECT transition_id FROM memory_patch.patch_transition_records
                 WHERE tenant_id = %s AND transition_id = %s
                   AND proposal_id = %s AND proposal_content_hash = %s
                   AND state_before = %s AND state_after = %s
                   AND actor_type = %s AND actor_id = %s
                   AND step30_receipt_hash = %s
                """,
                (
                    state.proposal.tenant_id,
                    transition_id,
                    state.proposal.proposal_id,
                    state.proposal.proposal_hash,
                    state_before.value,
                    state_after.value,
                    actor_type,
                    actor_id,
                    receipt_hash,
                ),
            )
            if existing is None:
                raise ImmutableRecordConflictError(
                    "Step 30 transition event conflicts",
                    sanitized_code="STEP30_TRANSITION_EVENT_CONFLICT",
                )
        return transition_id


__all__ = [
    "PersonalMemoryPatchLifecycleCockroachRepository",
    "lifecycle_state_from_row",
    "step30_transition_id",
]
