"""CockroachDB persistence for the Step 29 proposal-only lifecycle."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from aioa_memory_kernel.contracts.enums import ActorType, PatchState
from aioa_memory_kernel.contracts.exceptions import IntegrityError
from aioa_memory_kernel.contracts.serialization import canonical_json, canonical_sha256
from aioa_memory_kernel.persistence.errors import (
    ImmutableRecordConflictError,
    PersistenceConfigurationError,
)
from aioa_memory_kernel.persistence.protocols import TransactionProtocol

from .proposals import (
    MAXIMUM_PROPOSAL_BYTES_PER_SLOT,
    MAXIMUM_PROPOSALS_PER_SLOT,
    STEP29_SCHEMA_VERSION,
    PersonalMemoryPatchProposal,
    PersonalMemoryPatchProposalState,
    parse_personal_memory_patch_state,
    personal_memory_patch_state_to_jsonb,
    verify_personal_memory_patch_state,
)


_STEP29_ORIGINS = ("KNOWLEDGE_KERNEL", "CRITIC_PROMPT_LOOP")


def proposal_transition_id(
    proposal: PersonalMemoryPatchProposal,
    state_before: PatchState,
    state_after: PatchState,
    state_version: int,
) -> str:
    return "step29-transition-" + canonical_sha256(
        {
            "proposal_id": proposal.proposal_id,
            "proposal_hash": proposal.proposal_hash,
            "state_before": state_before,
            "state_after": state_after,
            "state_version": state_version,
        }
    )


def _json_object(value: object, context: str) -> Mapping[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise PersistenceConfigurationError(
                f"{context} JSON is invalid",
                sanitized_code="INVALID_STEP29_PROPOSAL_ROW",
            ) from exc
    if not isinstance(value, Mapping):
        raise PersistenceConfigurationError(
            f"{context} must be an object",
            sanitized_code="INVALID_STEP29_PROPOSAL_ROW",
        )
    return value


def _integer(value: object, context: str) -> int:
    try:
        result = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise PersistenceConfigurationError(
            f"{context} is not an integer",
            sanitized_code="INVALID_STEP29_PROPOSAL_ROW",
        ) from exc
    if result < 0:
        raise PersistenceConfigurationError(
            f"{context} is negative",
            sanitized_code="INVALID_STEP29_PROPOSAL_ROW",
        )
    return result


def proposal_state_from_row(
    row: Mapping[str, object],
) -> PersonalMemoryPatchProposalState:
    try:
        state = parse_personal_memory_patch_state(
            _json_object(row["proposed_content"], "proposed_content")
        )
        lifecycle_state = str(row["lifecycle_state"])
        proposal_id = str(row["proposal_id"])
        content_hash = str(row["content_hash"])
        state_version = _integer(row["step29_state_version"], "state_version")
        state_hash = str(row["step29_state_hash"])
        dedup_key = str(row["step29_dedup_key"])
        candidate_id = str(row["step29_candidate_id"])
        candidate_hash = str(row["step29_candidate_hash"])
        candidate_envelope_hash = str(row["step29_candidate_envelope_hash"])
        target_binding_hash = str(row["step29_target_binding_hash"])
        binding_hash = row.get("step29_evidence_binding_hash")
        receipt_hash = row.get("step29_validation_receipt_hash")
    except KeyError as exc:
        raise PersistenceConfigurationError(
            "Step 29 proposal row is incomplete",
            sanitized_code="INVALID_STEP29_PROPOSAL_ROW",
        ) from exc
    proposal = state.proposal
    expected_binding = (
        None if state.evidence_binding is None else state.evidence_binding.binding_hash
    )
    expected_receipt = (
        None if state.validation_receipt is None else state.validation_receipt.receipt_hash
    )
    if (
        lifecycle_state != state.state.value
        or proposal_id != proposal.proposal_id
        or content_hash != proposal.proposal_hash
        or state_version != state.state_version
        or state_hash != state.state_hash
        or dedup_key != proposal.exact_dedup_key
        or candidate_id != proposal.candidate_id
        or candidate_hash != proposal.candidate_hash
        or candidate_envelope_hash != proposal.candidate_envelope_hash
        or target_binding_hash != proposal.target_binding_hash
        or binding_hash != expected_binding
        or receipt_hash != expected_receipt
    ):
        raise IntegrityError("persisted Step 29 proposal is detached from its state")
    verify_personal_memory_patch_state(state)
    return state


@dataclass(frozen=True, slots=True)
class ProposalUsage:
    proposal_count: int
    proposal_bytes: int

    @property
    def within_hard_limit(self) -> bool:
        return (
            self.proposal_count <= MAXIMUM_PROPOSALS_PER_SLOT
            and self.proposal_bytes <= MAXIMUM_PROPOSAL_BYTES_PER_SLOT
        )


@dataclass(frozen=True, slots=True)
class ProposalPeer:
    proposal_id: str
    lifecycle_state: PatchState
    statement: str
    exact_dedup_key: str | None
    conflict_subject_sha256: str | None
    negative_polarity: bool | None
    proposal: PersonalMemoryPatchProposal | None


class PersonalMemoryPatchProposalCockroachRepository:
    """Explicit proposal/state/event operations; no Step 30 mutations."""

    _SELECT = """
        SELECT proposal_id, proposed_content, lifecycle_state, content_hash,
               step29_dedup_key, step29_state_version, step29_state_hash,
               step29_candidate_id, step29_candidate_hash,
               step29_candidate_envelope_hash, step29_target_binding_hash,
               step29_evidence_binding_hash,
               step29_validation_receipt_hash
          FROM memory_patch.memory_patch_proposals
    """

    @classmethod
    def get_proposal(
        cls,
        transaction: TransactionProtocol,
        tenant_id: str,
        owner_user_id: str,
        proposal_id: str,
    ) -> PersonalMemoryPatchProposalState | None:
        row = transaction.fetch_one(
            cls._SELECT
            + """
             WHERE tenant_id = %s
               AND owner_user_id = %s
               AND proposal_id = %s
               AND target_scope = 'USER_PERSONAL_HAT'
               AND proposed_content->>'contract_type' =
                   'PersonalMemoryPatchProposalState'
            """,
            (tenant_id, owner_user_id, proposal_id),
        )
        return None if row is None else proposal_state_from_row(row)

    @classmethod
    def get_by_dedup_key(
        cls,
        transaction: TransactionProtocol,
        tenant_id: str,
        owner_user_id: str,
        personal_memory_space_id: str,
        dedup_key: str,
    ) -> PersonalMemoryPatchProposalState | None:
        row = transaction.fetch_one(
            cls._SELECT
            + """
             WHERE tenant_id = %s
               AND owner_user_id = %s
               AND personal_memory_space_id = %s
               AND step29_dedup_key = %s
               AND proposed_content->>'contract_type' =
                   'PersonalMemoryPatchProposalState'
            """,
            (
                tenant_id,
                owner_user_id,
                personal_memory_space_id,
                dedup_key,
            ),
        )
        return None if row is None else proposal_state_from_row(row)

    @staticmethod
    def proposal_usage(
        transaction: TransactionProtocol,
        tenant_id: str,
        owner_user_id: str,
        personal_memory_space_id: str,
    ) -> ProposalUsage:
        row = transaction.fetch_one(
            """
            SELECT count(*) AS proposal_count,
                   coalesce(sum(octet_length(proposed_content::STRING)), 0)
                     AS proposal_bytes
              FROM memory_patch.memory_patch_proposals
             WHERE tenant_id = %s
               AND owner_user_id = %s
               AND personal_memory_space_id = %s
               AND target_scope = 'USER_PERSONAL_HAT'
               AND proposed_content->>'contract_type' =
                   'PersonalMemoryPatchProposalState'
            """,
            (tenant_id, owner_user_id, personal_memory_space_id),
        )
        if row is None:
            raise PersistenceConfigurationError(
                "proposal usage is unavailable",
                sanitized_code="STEP29_PROPOSAL_QUOTA_UNAVAILABLE",
            )
        return ProposalUsage(
            proposal_count=_integer(row["proposal_count"], "proposal_count"),
            proposal_bytes=_integer(row["proposal_bytes"], "proposal_bytes"),
        )

    def insert_proposal(
        self,
        transaction: TransactionProtocol,
        state: PersonalMemoryPatchProposalState,
    ) -> tuple[PersonalMemoryPatchProposalState, bool]:
        verify_personal_memory_patch_state(state)
        if state.state is not PatchState.PROPOSED:
            raise IntegrityError("only PROPOSED may be inserted by Step 29")
        proposal = state.proposal
        row = transaction.fetch_one(
            """
            INSERT INTO memory_patch.memory_patch_proposals (
              tenant_id, proposal_id, schema_version, hat_scope_id,
              target_scope, target_hat_id, owner_user_id,
              personal_memory_space_id, origin, proposed_content,
              evidence_references, scope_dimensions, valid_from, valid_until,
              requested_trust_class, approval_requirement, lifecycle_state,
              content_kind, created_at, content_hash, step29_dedup_key,
              step29_candidate_id, step29_candidate_hash,
              step29_candidate_envelope_hash, step29_target_binding_hash,
              step29_state_version, step29_state_hash,
              step29_evidence_binding_hash,
              step29_validation_receipt_hash
            ) VALUES (
              %s, %s, %s, %s, 'USER_PERSONAL_HAT', NULL, %s, %s, %s,
              %s::JSONB, '[]'::JSONB, %s::JSONB, NULL, NULL,
              'PERSONAL_VERIFIED_PATCH', 'OWNER', 'PROPOSED', 'FACTUAL',
              %s, %s, %s, %s, %s, %s, %s, %s, %s, NULL, NULL
            )
            ON CONFLICT DO NOTHING
            RETURNING proposal_id, proposed_content, lifecycle_state,
                      content_hash, step29_dedup_key, step29_candidate_id,
                      step29_candidate_hash, step29_candidate_envelope_hash,
                      step29_target_binding_hash, step29_state_version,
                      step29_state_hash, step29_evidence_binding_hash,
                      step29_validation_receipt_hash
            """,
            (
                proposal.tenant_id,
                proposal.proposal_id,
                STEP29_SCHEMA_VERSION,
                proposal.hat_scope_id,
                proposal.owner_user_id,
                proposal.personal_memory_space_id,
                proposal.origin.value,
                canonical_json(personal_memory_patch_state_to_jsonb(state)),
                canonical_json(proposal.proposal_scope),
                proposal.created_at,
                proposal.proposal_hash,
                proposal.exact_dedup_key,
                proposal.candidate_id,
                proposal.candidate_hash,
                proposal.candidate_envelope_hash,
                proposal.target_binding_hash,
                state.state_version,
                state.state_hash,
            ),
        )
        if row is not None:
            return proposal_state_from_row(row), True
        existing = self.get_by_dedup_key(
            transaction,
            proposal.tenant_id,
            proposal.owner_user_id,
            proposal.personal_memory_space_id,
            proposal.exact_dedup_key,
        )
        if existing is None:
            existing = self.get_proposal(
                transaction,
                proposal.tenant_id,
                proposal.owner_user_id,
                proposal.proposal_id,
            )
        if existing is None:
            raise ImmutableRecordConflictError(
                "proposal identity cannot be resolved",
                sanitized_code="STEP29_PROPOSAL_CONFLICT",
            )
        return existing, False

    def transition_proposal(
        self,
        transaction: TransactionProtocol,
        *,
        expected: PersonalMemoryPatchProposalState,
        updated: PersonalMemoryPatchProposalState,
    ) -> PersonalMemoryPatchProposalState:
        verify_personal_memory_patch_state(expected)
        verify_personal_memory_patch_state(updated)
        if (
            expected.proposal.proposal_hash != updated.proposal.proposal_hash
            or expected.proposal.proposal_id != updated.proposal.proposal_id
            or expected.proposal != updated.proposal
        ):
            raise IntegrityError("proposal transition changed immutable content")
        references = (
            ()
            if updated.evidence_binding is None
            else tuple(
                item.evidence_link_hash
                for item in updated.evidence_binding.ordered_evidence_references
            )
        )
        row = transaction.fetch_one(
            """
            UPDATE memory_patch.memory_patch_proposals
               SET proposed_content = %s::JSONB,
                   evidence_references = %s::JSONB,
                   lifecycle_state = %s,
                   step29_state_version = %s,
                   step29_state_hash = %s,
                   step29_evidence_binding_hash = %s,
                   step29_validation_receipt_hash = %s
             WHERE tenant_id = %s
               AND owner_user_id = %s
               AND personal_memory_space_id = %s
               AND proposal_id = %s
               AND content_hash = %s
               AND lifecycle_state = %s
               AND step29_state_version = %s
               AND step29_state_hash = %s
               AND proposed_content->>'contract_type' =
                   'PersonalMemoryPatchProposalState'
            RETURNING proposal_id, proposed_content, lifecycle_state,
                      content_hash, step29_dedup_key, step29_candidate_id,
                      step29_candidate_hash, step29_candidate_envelope_hash,
                      step29_target_binding_hash, step29_state_version,
                      step29_state_hash, step29_evidence_binding_hash,
                      step29_validation_receipt_hash
            """,
            (
                canonical_json(personal_memory_patch_state_to_jsonb(updated)),
                canonical_json(references),
                updated.state.value,
                updated.state_version,
                updated.state_hash,
                None
                if updated.evidence_binding is None
                else updated.evidence_binding.binding_hash,
                None
                if updated.validation_receipt is None
                else updated.validation_receipt.receipt_hash,
                expected.proposal.tenant_id,
                expected.proposal.owner_user_id,
                expected.proposal.personal_memory_space_id,
                expected.proposal.proposal_id,
                expected.proposal.proposal_hash,
                expected.state.value,
                expected.state_version,
                expected.state_hash,
            ),
        )
        if row is None:
            raise ImmutableRecordConflictError(
                "proposal state changed concurrently",
                sanitized_code="STEP29_STATE_VERSION_CONFLICT",
            )
        return proposal_state_from_row(row)

    @staticmethod
    def insert_transition_event(
        transaction: TransactionProtocol,
        *,
        proposal: PersonalMemoryPatchProposal,
        state_before: PatchState,
        state_after: PatchState,
        state_version: int,
        transitioned_at: datetime,
        actor_type: ActorType = ActorType.SYSTEM,
        actor_id: str = "personal-memory-patch-validation-service",
    ) -> str:
        transition_id = proposal_transition_id(
            proposal,
            state_before,
            state_after,
            state_version,
        )
        row = transaction.fetch_one(
            """
            INSERT INTO memory_patch.patch_transition_records (
              tenant_id, transition_id, proposal_id,
              proposal_content_hash, state_before, state_after,
              actor_type, actor_id, transitioned_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING
            RETURNING transition_id
            """,
            (
                proposal.tenant_id,
                transition_id,
                proposal.proposal_id,
                proposal.proposal_hash,
                state_before.value,
                state_after.value,
                actor_type.value,
                actor_id,
                transitioned_at,
            ),
        )
        if row is None:
            existing = transaction.fetch_one(
                """
                SELECT transition_id
                  FROM memory_patch.patch_transition_records
                 WHERE tenant_id = %s AND transition_id = %s
                   AND proposal_id = %s AND proposal_content_hash = %s
                   AND state_before = %s AND state_after = %s
                """,
                (
                    proposal.tenant_id,
                    transition_id,
                    proposal.proposal_id,
                    proposal.proposal_hash,
                    state_before.value,
                    state_after.value,
                ),
            )
            if existing is None:
                raise ImmutableRecordConflictError(
                    "proposal transition event conflicts",
                    sanitized_code="STEP29_TRANSITION_EVENT_CONFLICT",
                )
        return transition_id

    @staticmethod
    def list_peers(
        transaction: TransactionProtocol,
        *,
        tenant_id: str,
        owner_user_id: str,
        personal_memory_space_id: str,
        exclude_proposal_id: str,
    ) -> tuple[ProposalPeer, ...]:
        rows = transaction.fetch_all(
            """
            SELECT proposal_id, proposed_content, lifecycle_state,
                   step29_dedup_key, step29_state_version,
                   step29_candidate_id, step29_candidate_hash,
                   step29_candidate_envelope_hash,
                   step29_target_binding_hash,
                   step29_state_hash, step29_evidence_binding_hash,
                   step29_validation_receipt_hash, content_hash
              FROM memory_patch.memory_patch_proposals
             WHERE tenant_id = %s
               AND owner_user_id = %s
               AND personal_memory_space_id = %s
               AND proposal_id <> %s
               AND lifecycle_state IN (
                 'PROPOSED', 'EVIDENCE_BOUND', 'VALIDATED',
                 'AWAITING_APPROVAL', 'COMMITTED', 'ACTIVE'
               )
               AND content_kind = 'FACTUAL'
             ORDER BY proposal_id
            """,
            (
                tenant_id,
                owner_user_id,
                personal_memory_space_id,
                exclude_proposal_id,
            ),
        )
        result: list[ProposalPeer] = []
        for row in rows:
            raw_value = row["proposed_content"]
            if isinstance(raw_value, str):
                try:
                    raw_value = json.loads(raw_value)
                except json.JSONDecodeError:
                    pass
            state_value = PatchState(str(row["lifecycle_state"]))
            if (
                isinstance(raw_value, Mapping)
                and raw_value.get("contract_type")
                == "PersonalMemoryPatchProposalState"
            ):
                state = proposal_state_from_row(row)
                proposal = state.proposal
                result.append(
                    ProposalPeer(
                        proposal_id=proposal.proposal_id,
                        lifecycle_state=state_value,
                        statement=proposal.proposal_statement,
                        exact_dedup_key=proposal.exact_dedup_key,
                        conflict_subject_sha256=proposal.conflict_subject_sha256,
                        negative_polarity=proposal.negative_polarity,
                        proposal=proposal,
                    )
                )
            else:
                statement = raw_value if isinstance(raw_value, str) else None
                if isinstance(raw_value, Mapping):
                    for key in (
                        "proposal_statement",
                        "statement",
                        "correction",
                        "value",
                        "text",
                    ):
                        if isinstance(raw_value.get(key), str):
                            statement = raw_value[key]
                            break
                if isinstance(statement, str) and statement.strip():
                    result.append(
                        ProposalPeer(
                            proposal_id=str(row["proposal_id"]),
                            lifecycle_state=state_value,
                            statement=statement,
                            exact_dedup_key=None,
                            conflict_subject_sha256=None,
                            negative_polarity=None,
                            proposal=None,
                        )
                    )
        return tuple(result)


__all__ = [
    "PersonalMemoryPatchProposalCockroachRepository",
    "ProposalPeer",
    "ProposalUsage",
    "proposal_state_from_row",
    "proposal_transition_id",
]
