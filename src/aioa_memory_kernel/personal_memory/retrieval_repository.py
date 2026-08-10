"""Read-only CockroachDB repository for Step 31 ACTIVE Personal Memory."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from aioa_memory_kernel.contracts.enums import PatchState
from aioa_memory_kernel.contracts.exceptions import IntegrityError
from aioa_memory_kernel.contracts.serialization import (
    canonical_json_bytes,
    to_canonical_data,
)
from aioa_memory_kernel.persistence.errors import PersistenceConfigurationError
from aioa_memory_kernel.persistence.protocols import TransactionProtocol

from .lifecycle import PersonalMemoryPatchLifecycleState
from .lifecycle_repository import lifecycle_state_from_row
from .retrieval import (
    MAXIMUM_ACTIVE_PATCH_CANDIDATES,
    StoredActivePatchCandidate,
)


# The query is deliberately owner/tenant/slot/ACTIVE filtered before Python
# sees a row.  LIMIT is always supplied as <= MAXIMUM_ACTIVE_PATCH_CANDIDATES+1
# so a caller can report deterministic truncation without an owner-history dump.
ACTIVE_PATCH_RETRIEVAL_SQL = """
    SELECT
      proposal.proposal_id,
      proposal.proposed_content,
      proposal.lifecycle_state,
      proposal.content_hash,
      proposal.step29_dedup_key,
      proposal.step29_state_version,
      proposal.step29_state_hash,
      proposal.step29_candidate_id,
      proposal.step29_candidate_hash,
      proposal.step29_candidate_envelope_hash,
      proposal.step29_target_binding_hash,
      proposal.step29_evidence_binding_hash,
      proposal.step29_validation_receipt_hash,
      proposal.step30_approval_id,
      proposal.step30_approval_receipt_hash,
      proposal.step30_commit_receipt_hash,
      proposal.step30_activation_receipt_hash,
      proposal.step30_patch_id,
      item.memory_item_id,
      item.hat_scope_id AS item_hat_scope_id,
      item.target_scope AS item_target_scope,
      item.visibility AS item_visibility,
      item.trust_class AS item_trust_class,
      item.content_kind AS item_content_kind,
      item.content AS item_content,
      item.scope_dimensions AS item_scope_dimensions,
      item.evidence_references AS item_evidence_references,
      item.source_patch_id AS item_source_patch_id,
      item.valid_from,
      item.valid_until,
      item.expires_at,
      item.active AS item_active,
      item.revoked AS item_revoked,
      item.step30_proposal_id AS item_step30_proposal_id,
      item.step30_proposal_hash AS item_step30_proposal_hash,
      item.step30_approval_receipt_hash AS item_step30_approval_receipt_hash,
      item.step30_validation_receipt_hash AS item_step30_validation_receipt_hash,
      item.step30_commit_receipt_hash AS item_step30_commit_receipt_hash,
      item.step30_patch_hash AS item_step30_patch_hash,
      item.step30_state_version AS item_step30_state_version,
      item.step30_state_hash AS item_step30_state_hash,
      item.step30_activation_receipt_hash AS item_step30_activation_receipt_hash,
      item.step30_activation_payload AS item_step30_activation_payload,
      item.step32_terminal_kind,
      item.step32_effective_at,
      item.step32_superseded_by_patch_id
    FROM memory_patch.memory_items AS item
    JOIN memory_patch.memory_patch_proposals AS proposal
      ON proposal.tenant_id = item.tenant_id
     AND proposal.proposal_id = item.step30_proposal_id
     AND proposal.content_hash = item.step30_proposal_hash
     AND proposal.step30_patch_id = item.memory_item_id
   WHERE item.tenant_id = %s
     AND proposal.tenant_id = %s
     AND proposal.owner_user_id = %s
     AND proposal.personal_memory_space_id = %s
     AND item.hat_scope_id = %s
     AND item.target_scope = 'USER_PERSONAL_HAT'
     AND item.visibility = 'PERSONAL'
     AND item.trust_class = 'PERSONAL_VERIFIED_PATCH'
     AND (
       (
         item.active = true
         AND item.revoked = false
         AND item.step32_terminal_kind IS NULL
       )
       OR (
         %s::TIMESTAMPTZ IS NOT NULL
         AND item.active = false
         AND item.revoked = false
         AND item.step32_terminal_kind = 'SUPERSEDED'
         AND item.step32_effective_at > %s::TIMESTAMPTZ
       )
     )
     AND NOT EXISTS (
       SELECT 1
         FROM memory_patch.personal_memory_patch_supersessions AS successor
        WHERE successor.tenant_id = item.tenant_id
          AND successor.new_patch_id = item.memory_item_id
          AND %s::TIMESTAMPTZ IS NOT NULL
          AND successor.effective_at > %s::TIMESTAMPTZ
     )
     AND item.step30_state_version = 7
     AND item.step30_activation_receipt_hash IS NOT NULL
     AND proposal.lifecycle_state = 'ACTIVE'
     AND proposal.step29_state_version = 7
   ORDER BY item.step30_state_version DESC,
            item.memory_item_id ASC,
            item.step30_patch_hash ASC
   LIMIT %s
"""


def _json_value(value: object, field_name: str) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError as exc:
            raise PersistenceConfigurationError(
                f"{field_name} JSON is invalid",
                sanitized_code="INVALID_STEP31_ACTIVE_PATCH_ROW",
            ) from exc
    return value


def _timestamp(value: object, field_name: str) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise PersistenceConfigurationError(
                f"{field_name} must be timezone-aware",
                sanitized_code="INVALID_STEP31_ACTIVE_PATCH_ROW",
            )
        return value.astimezone(UTC)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise PersistenceConfigurationError(
                f"{field_name} timestamp is invalid",
                sanitized_code="INVALID_STEP31_ACTIVE_PATCH_ROW",
            ) from exc
        if parsed.tzinfo is None:
            raise PersistenceConfigurationError(
                f"{field_name} must be timezone-aware",
                sanitized_code="INVALID_STEP31_ACTIVE_PATCH_ROW",
            )
        return parsed.astimezone(UTC)
    raise PersistenceConfigurationError(
        f"{field_name} timestamp is invalid",
        sanitized_code="INVALID_STEP31_ACTIVE_PATCH_ROW",
    )


def _boolean(value: object, field_name: str) -> bool:
    if value in (True, "t", "true", "TRUE", "1", 1):
        return True
    if value in (False, "f", "false", "FALSE", "0", 0):
        return False
    raise PersistenceConfigurationError(
        f"{field_name} is not boolean",
        sanitized_code="INVALID_STEP31_ACTIVE_PATCH_ROW",
    )


def active_patch_candidate_from_row(
    row: Mapping[str, object],
) -> StoredActivePatchCandidate:
    state = lifecycle_state_from_row(row)
    if not isinstance(state, PersonalMemoryPatchLifecycleState):
        raise IntegrityError("Step 31 row is not a Step 30 lifecycle")
    if state.state is not PatchState.ACTIVE:
        raise IntegrityError("Step 31 repository admitted a non-ACTIVE lifecycle")
    patch = state.committed_patch
    commit = state.commit_receipt
    activation = state.activation_receipt
    evidence = state.step29_state.evidence_binding
    validation = state.step29_state.validation_receipt
    if patch is None or commit is None or activation is None:
        raise IntegrityError("Step 31 active lifecycle lost Step 30 receipts")
    if evidence is None or validation is None:
        raise IntegrityError("Step 31 active lifecycle lost Step 29 lineage")
    content = _json_value(row.get("item_content"), "item_content")
    scope = _json_value(row.get("item_scope_dimensions"), "item_scope_dimensions")
    references = _json_value(
        row.get("item_evidence_references"), "item_evidence_references"
    )
    activation_payload = _json_value(
        row.get("item_step30_activation_payload"),
        "item_step30_activation_payload",
    )
    expected_references = tuple(
        item.evidence_link_hash for item in evidence.ordered_evidence_references
    )
    if (
        row.get("memory_item_id") != patch.patch_id
        or row.get("item_source_patch_id") != patch.patch_id
        or row.get("item_hat_scope_id") != patch.hat_scope_id
        or row.get("item_target_scope") != "USER_PERSONAL_HAT"
        or row.get("item_visibility") != "PERSONAL"
        or row.get("item_trust_class") != "PERSONAL_VERIFIED_PATCH"
        or row.get("item_content_kind") != "FACTUAL"
        or row.get("item_step30_proposal_id") != patch.proposal_id
        or row.get("item_step30_proposal_hash") != patch.proposal_hash
        or row.get("item_step30_approval_receipt_hash")
        != state.approval_receipt.receipt_hash
        or row.get("item_step30_validation_receipt_hash")
        != validation.receipt_hash
        or row.get("item_step30_commit_receipt_hash") != commit.receipt_hash
        or row.get("item_step30_patch_hash") != patch.patch_hash
        or int(row.get("item_step30_state_version", -1)) != 7
        or row.get("item_step30_state_hash") != state.state_hash
        or row.get("item_step30_activation_receipt_hash")
        != activation.receipt_hash
        or canonical_json_bytes(content)
        != canonical_json_bytes(to_canonical_data(patch))
        or canonical_json_bytes(scope)
        != canonical_json_bytes(to_canonical_data(patch.patch_scope))
        or canonical_json_bytes(references)
        != canonical_json_bytes(expected_references)
        or canonical_json_bytes(activation_payload)
        != canonical_json_bytes(to_canonical_data(activation))
    ):
        raise IntegrityError("persisted Step 31 active patch row is detached")
    return StoredActivePatchCandidate(
        lifecycle_state=state,
        active=_boolean(row.get("item_active"), "item_active"),
        revoked=_boolean(row.get("item_revoked"), "item_revoked"),
        valid_from=_timestamp(row.get("valid_from"), "valid_from"),
        valid_until=_timestamp(row.get("valid_until"), "valid_until"),
        expires_at=_timestamp(row.get("expires_at"), "expires_at"),
        step32_terminal_kind=row.get("step32_terminal_kind"),
        step32_effective_at=_timestamp(
            row.get("step32_effective_at"), "step32_effective_at"
        ),
        step32_superseded_by_patch_id=row.get(
            "step32_superseded_by_patch_id"
        ),
    )


class ActivePatchRetrievalCockroachRepository:
    """One bounded parameterized SELECT; no mutation method is exposed."""

    @staticmethod
    def list_active_patch_candidates(
        transaction: TransactionProtocol,
        *,
        tenant_id: str,
        owner_user_id: str,
        personal_memory_space_id: str,
        hat_scope_id: str,
        limit: int,
        knowledge_as_of: datetime | None = None,
    ) -> tuple[StoredActivePatchCandidate, ...]:
        if (
            not isinstance(limit, int)
            or isinstance(limit, bool)
            or not 1 <= limit <= MAXIMUM_ACTIVE_PATCH_CANDIDATES + 1
        ):
            raise PersistenceConfigurationError(
                "Step 31 query limit is invalid",
                sanitized_code="STEP31_RETRIEVAL_LIMIT_INVALID",
            )
        rows = transaction.fetch_all(
            ACTIVE_PATCH_RETRIEVAL_SQL,
            (
                tenant_id,
                tenant_id,
                owner_user_id,
                personal_memory_space_id,
                hat_scope_id,
                knowledge_as_of,
                knowledge_as_of,
                knowledge_as_of,
                knowledge_as_of,
                limit,
            ),
        )
        if len(rows) > limit:
            raise IntegrityError("database exceeded Step 31 query limit")
        try:
            return tuple(active_patch_candidate_from_row(row) for row in rows)
        except (KeyError, TypeError, ValueError) as exc:
            raise PersistenceConfigurationError(
                "database returned an invalid Step 31 active patch row",
                sanitized_code="INVALID_STEP31_ACTIVE_PATCH_ROW",
            ) from exc


__all__ = [
    "ACTIVE_PATCH_RETRIEVAL_SQL",
    "ActivePatchRetrievalCockroachRepository",
    "active_patch_candidate_from_row",
]
