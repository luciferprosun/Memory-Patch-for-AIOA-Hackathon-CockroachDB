"""CockroachDB carrier for owner-private Step 28 correction candidates.

The existing ``memory_patch_proposals`` table is reused only as an immutable
``DETECTED`` candidate carrier.  This repository deliberately exposes no
update, delete, proposal-transition, approval, commit, or activation method.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from aioa_memory_kernel.contracts.enums import ActorType
from aioa_memory_kernel.contracts.exceptions import IntegrityError
from aioa_memory_kernel.contracts.serialization import canonical_json
from aioa_memory_kernel.persistence.errors import (
    ImmutableRecordConflictError,
    PersistenceConfigurationError,
)
from aioa_memory_kernel.persistence.protocols import TransactionProtocol

from .candidates import (
    STEP28_SCHEMA_VERSION,
    CorrectionCandidateEnvelope,
    CorrectionCandidateTargetBinding,
    correction_candidate_envelope_to_jsonb,
    parse_correction_candidate_envelope,
    verify_correction_candidate_envelope,
)


_ALLOWED_ORIGINS = frozenset(
    {
        ActorType.KNOWLEDGE_KERNEL.value,
        ActorType.CRITIC_PROMPT_LOOP.value,
    }
)


def _json_object(value: object, field_name: str) -> Mapping[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as error:
            raise PersistenceConfigurationError(
                "candidate JSONB row is invalid",
                sanitized_code="INVALID_CORRECTION_CANDIDATE_ROW",
            ) from error
    if not isinstance(value, Mapping):
        raise PersistenceConfigurationError(
            f"{field_name} must be a JSON object",
            sanitized_code="INVALID_CORRECTION_CANDIDATE_ROW",
        )
    return value


def _non_negative_integer(value: object, field_name: str) -> int:
    try:
        result = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as error:
        raise PersistenceConfigurationError(
            f"{field_name} is not an integer",
            sanitized_code="INVALID_CORRECTION_CANDIDATE_ROW",
        ) from error
    if result < 0:
        raise PersistenceConfigurationError(
            f"{field_name} is negative",
            sanitized_code="INVALID_CORRECTION_CANDIDATE_ROW",
        )
    return result


def candidate_envelope_from_row(
    row: Mapping[str, object],
) -> CorrectionCandidateEnvelope:
    """Strictly rehydrate and verify the canonical JSONB candidate envelope."""

    try:
        envelope = parse_correction_candidate_envelope(
            _json_object(row["proposed_content"], "proposed_content")
        )
        origin = str(row["origin"])
        lifecycle_state = str(row["lifecycle_state"])
        content_hash = str(row["content_hash"])
        proposal_id = str(row["proposal_id"])
        candidate_hash = str(row["step29_candidate_hash"])
        target_binding_hash = str(row["step29_target_binding_hash"])
    except KeyError as error:
        raise PersistenceConfigurationError(
            "candidate row is incomplete",
            sanitized_code="INVALID_CORRECTION_CANDIDATE_ROW",
        ) from error
    if origin not in _ALLOWED_ORIGINS or lifecycle_state != "DETECTED":
        raise IntegrityError("persisted row is outside the Step 28 candidate boundary")
    if (
        proposal_id != envelope.candidate_id
        or content_hash != envelope.envelope_hash
        or candidate_hash != envelope.submission.candidate.content_hash
        or target_binding_hash
        != envelope.submission.target_slot_binding.target_binding_hash
        or origin != envelope.submission.candidate.source_component.value
    ):
        raise IntegrityError("persisted candidate carrier is detached from its envelope")
    verify_correction_candidate_envelope(envelope)
    return envelope


class CorrectionCandidateCockroachRepository:
    """Explicit append/read operations for the existing candidate carrier."""

    @staticmethod
    def ensure_target_hat_scope(
        transaction: TransactionProtocol,
        target: CorrectionCandidateTargetBinding,
        created_at: datetime,
    ) -> None:
        """Project the exact eligible Step 27 slot into its private HAT scope."""

        row = transaction.fetch_one(
            """
            INSERT INTO memory_patch.hat_scopes (
              tenant_id, hat_scope_id, target_scope,
              knowledge_hat_id, knowledge_hat_version,
              owner_user_id, personal_memory_space_id, created_at
            ) VALUES (
              %s, %s, 'USER_PERSONAL_HAT', NULL, NULL, %s, %s, %s
            )
            ON CONFLICT DO NOTHING
            RETURNING tenant_id
            """,
            (
                target.tenant_id,
                target.hat_scope_id,
                target.owner_user_id,
                target.personal_memory_space_id,
                created_at,
            ),
        )
        if row is None:
            existing = transaction.fetch_one(
                """
                SELECT tenant_id
                  FROM memory_patch.hat_scopes
                 WHERE tenant_id = %s
                   AND hat_scope_id = %s
                   AND target_scope = 'USER_PERSONAL_HAT'
                   AND owner_user_id = %s
                   AND personal_memory_space_id = %s
                   AND knowledge_hat_id IS NULL
                   AND knowledge_hat_version IS NULL
                """,
                (
                    target.tenant_id,
                    target.hat_scope_id,
                    target.owner_user_id,
                    target.personal_memory_space_id,
                ),
            )
            if existing is None:
                raise ImmutableRecordConflictError(
                    "personal HAT scope identity conflicts",
                    sanitized_code="CORRECTION_CANDIDATE_SCOPE_CONFLICT",
                )

    @staticmethod
    def get_candidate(
        transaction: TransactionProtocol,
        tenant_id: str,
        owner_user_id: str,
        candidate_id: str,
    ) -> CorrectionCandidateEnvelope | None:
        row = transaction.fetch_one(
            """
            SELECT proposal_id, origin, proposed_content,
                   lifecycle_state, content_hash, step29_candidate_hash,
                   step29_target_binding_hash
              FROM memory_patch.memory_patch_proposals
             WHERE tenant_id = %s
               AND owner_user_id = %s
               AND proposal_id = %s
               AND target_scope = 'USER_PERSONAL_HAT'
               AND lifecycle_state = 'DETECTED'
               AND origin IN ('KNOWLEDGE_KERNEL', 'CRITIC_PROMPT_LOOP')
            """,
            (tenant_id, owner_user_id, candidate_id),
        )
        return None if row is None else candidate_envelope_from_row(row)

    @staticmethod
    def list_owner_candidates(
        transaction: TransactionProtocol,
        tenant_id: str,
        owner_user_id: str,
        personal_memory_space_id: str,
    ) -> tuple[CorrectionCandidateEnvelope, ...]:
        rows = transaction.fetch_all(
            """
            SELECT proposal_id, origin, proposed_content,
                   lifecycle_state, content_hash, step29_candidate_hash,
                   step29_target_binding_hash
              FROM memory_patch.memory_patch_proposals
             WHERE tenant_id = %s
               AND owner_user_id = %s
               AND personal_memory_space_id = %s
               AND target_scope = 'USER_PERSONAL_HAT'
               AND lifecycle_state = 'DETECTED'
               AND origin IN ('KNOWLEDGE_KERNEL', 'CRITIC_PROMPT_LOOP')
             ORDER BY proposal_id
            """,
            (tenant_id, owner_user_id, personal_memory_space_id),
        )
        return tuple(candidate_envelope_from_row(row) for row in rows)

    @staticmethod
    def candidate_usage(
        transaction: TransactionProtocol,
        tenant_id: str,
        owner_user_id: str,
        personal_memory_space_id: str,
    ) -> tuple[int, int]:
        row = transaction.fetch_one(
            """
            SELECT count(*) AS candidate_count,
                   coalesce(sum(octet_length(proposed_content::STRING)), 0)
                     AS candidate_bytes
              FROM memory_patch.memory_patch_proposals
             WHERE tenant_id = %s
               AND owner_user_id = %s
               AND personal_memory_space_id = %s
               AND target_scope = 'USER_PERSONAL_HAT'
               AND origin IN ('KNOWLEDGE_KERNEL', 'CRITIC_PROMPT_LOOP')
               AND approval_requirement = 'OWNER'
               AND requested_trust_class = 'MODEL_EXPERIENCE_HINT'
               AND content_kind = 'MODEL_EXPERIENCE'
               AND proposed_content->>'contract_type' =
                   'CorrectionCandidateEnvelope'
            """,
            (tenant_id, owner_user_id, personal_memory_space_id),
        )
        if row is None:
            raise PersistenceConfigurationError(
                "candidate quota row is unavailable",
                sanitized_code="CORRECTION_CANDIDATE_QUOTA_UNAVAILABLE",
            )
        return (
            _non_negative_integer(row.get("candidate_count"), "candidate_count"),
            _non_negative_integer(row.get("candidate_bytes"), "candidate_bytes"),
        )

    def insert_candidate(
        self,
        transaction: TransactionProtocol,
        envelope: CorrectionCandidateEnvelope,
    ) -> tuple[CorrectionCandidateEnvelope, bool]:
        """Insert once, or return the immutable exact semantic duplicate."""

        verify_correction_candidate_envelope(envelope)
        submission = envelope.submission
        candidate = submission.candidate
        target = submission.target_slot_binding
        payload = correction_candidate_envelope_to_jsonb(envelope)
        row = transaction.fetch_one(
            """
            INSERT INTO memory_patch.memory_patch_proposals (
              tenant_id, proposal_id, schema_version, hat_scope_id,
              target_scope, target_hat_id, owner_user_id,
              personal_memory_space_id, origin, proposed_content,
              evidence_references, scope_dimensions, valid_from, valid_until,
              requested_trust_class, approval_requirement, lifecycle_state,
              content_kind, created_at, content_hash,
              step29_candidate_hash, step29_target_binding_hash
            ) VALUES (
              %s, %s, %s, %s, 'USER_PERSONAL_HAT', NULL, %s, %s, %s,
              %s::JSONB, %s::JSONB, %s::JSONB, NULL, NULL,
              'MODEL_EXPERIENCE_HINT', 'OWNER', 'DETECTED',
              'MODEL_EXPERIENCE', %s, %s, %s, %s
            )
            ON CONFLICT DO NOTHING
            RETURNING proposal_id, origin, proposed_content,
                      lifecycle_state, content_hash, step29_candidate_hash,
                      step29_target_binding_hash
            """,
            (
                candidate.tenant_id,
                envelope.candidate_id,
                STEP28_SCHEMA_VERSION,
                target.hat_scope_id,
                candidate.user_id,
                candidate.personal_memory_space_id,
                candidate.source_component.value,
                canonical_json(payload),
                canonical_json(candidate.available_evidence_references),
                canonical_json(submission.lineage.effective_scope),
                candidate.created_at,
                envelope.envelope_hash,
                candidate.content_hash,
                target.target_binding_hash,
            ),
        )
        if row is not None:
            return candidate_envelope_from_row(row), True
        existing = self.get_candidate(
            transaction,
            candidate.tenant_id,
            candidate.user_id,
            envelope.candidate_id,
        )
        if existing is None:
            raise ImmutableRecordConflictError(
                "candidate identity could not be resolved",
                sanitized_code="CORRECTION_CANDIDATE_CONFLICT",
            )
        if (
            existing.submission.semantic_deduplication_key
            != submission.semantic_deduplication_key
        ):
            raise ImmutableRecordConflictError(
                "candidate identity was reused with different content",
                sanitized_code="CORRECTION_CANDIDATE_CONFLICT",
            )
        return existing, False


__all__ = [
    "CorrectionCandidateCockroachRepository",
    "candidate_envelope_from_row",
]
