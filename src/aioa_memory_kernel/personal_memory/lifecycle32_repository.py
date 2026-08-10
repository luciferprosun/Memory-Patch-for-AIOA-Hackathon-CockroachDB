"""Owner-scoped CockroachDB persistence for Step 32 lifecycle overlays."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from aioa_memory_kernel.contracts.exceptions import IntegrityError
from aioa_memory_kernel.contracts.serialization import canonical_json, to_canonical_data
from aioa_memory_kernel.persistence.errors import (
    ImmutableRecordConflictError,
    PersistenceConfigurationError,
)
from aioa_memory_kernel.persistence.protocols import TransactionProtocol

from .lifecycle import PersonalMemoryPatchLifecycleState
from .lifecycle32 import (
    MAXIMUM_EXPORT_RECORDS,
    PersonalMemoryDeletionResult,
    PersonalMemoryLifecycleExportBundle,
    PersonalMemoryLifecycleExportRecord,
    PersonalMemoryPatchRevocation,
    PersonalMemoryPatchSupersession,
    SharedMemoryPromotionProposal,
    parse_lifecycle_export_bundle,
    step32_to_jsonb,
    verify_deletion_result,
    verify_lifecycle_export_bundle,
    verify_patch_revocation,
    verify_patch_supersession,
    verify_shared_memory_promotion_proposal,
)
from .lifecycle_repository import (
    PersonalMemoryPatchLifecycleCockroachRepository,
)


_JSON_COLUMNS = frozenset(
    {
        "proposed_content",
        "approval_payload",
        "commit_payload",
        "content",
        "activation_payload",
        "record_payload",
        "promotion_payload",
        "deletion_payload",
    }
)


def _json_value(value: object, field_name: str) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError as exc:
            raise PersistenceConfigurationError(
                f"{field_name} JSON is invalid",
                sanitized_code="INVALID_STEP32_LIFECYCLE_ROW",
            ) from exc
    return value


def _boolean(value: object, field_name: str) -> bool:
    if value in (True, "t", "true", "TRUE", "1", 1):
        return True
    if value in (False, "f", "false", "FALSE", "0", 0):
        return False
    raise PersistenceConfigurationError(
        f"{field_name} is not boolean",
        sanitized_code="INVALID_STEP32_LIFECYCLE_ROW",
    )


def _export_record(
    record_type: str,
    record_id: object,
    payload: Mapping[str, object],
) -> PersonalMemoryLifecycleExportRecord:
    normalized: dict[str, Any] = {}
    for key, value in payload.items():
        normalized[key] = _json_value(value, key) if key in _JSON_COLUMNS else value
    return PersonalMemoryLifecycleExportRecord(
        record_type=record_type,
        record_id=str(record_id),
        payload=normalized,
    )


class PersonalMemoryLifecycle32CockroachRepository:
    """Append-only records plus exact terminal updates; no shared publication."""

    def __init__(
        self,
        lifecycle_repository: PersonalMemoryPatchLifecycleCockroachRepository | None = None,
    ) -> None:
        self._lifecycle = (
            lifecycle_repository
            or PersonalMemoryPatchLifecycleCockroachRepository()
        )

    def get_step30_state(
        self,
        transaction: TransactionProtocol,
        tenant_id: str,
        owner_user_id: str,
        proposal_id: str,
    ) -> PersonalMemoryPatchLifecycleState | None:
        value = self._lifecycle.get_state(
            transaction, tenant_id, owner_user_id, proposal_id
        )
        return value if isinstance(value, PersonalMemoryPatchLifecycleState) else None

    @staticmethod
    def get_supersession_replay(
        transaction: TransactionProtocol,
        *,
        tenant_id: str,
        owner_user_id: str,
        replay_identity: str,
    ) -> Mapping[str, object] | None:
        return transaction.fetch_one(
            """
            SELECT supersession_id, request_hash, supersession_hash,
                   old_patch_id, new_patch_id
              FROM memory_patch.personal_memory_patch_supersessions
             WHERE tenant_id = %s AND owner_user_id = %s
               AND replay_identity = %s
            """,
            (tenant_id, owner_user_id, replay_identity),
        )

    @staticmethod
    def get_revocation_replay(
        transaction: TransactionProtocol,
        *,
        tenant_id: str,
        owner_user_id: str,
        replay_identity: str,
    ) -> Mapping[str, object] | None:
        return transaction.fetch_one(
            """
            SELECT revocation_id, request_hash, revocation_hash, patch_id
              FROM memory_patch.personal_memory_patch_revocations
             WHERE tenant_id = %s AND owner_user_id = %s
               AND replay_identity = %s
            """,
            (tenant_id, owner_user_id, replay_identity),
        )

    @staticmethod
    def get_deletion_replay(
        transaction: TransactionProtocol,
        *,
        tenant_id: str,
        owner_user_id: str,
        replay_identity: str,
    ) -> Mapping[str, object] | None:
        return transaction.fetch_one(
            """
            SELECT deletion_id, request_hash, result_hash, patch_id,
                   tombstone_hash
              FROM memory_patch.personal_memory_deletions
             WHERE tenant_id = %s AND owner_user_id = %s
               AND replay_identity = %s
            """,
            (tenant_id, owner_user_id, replay_identity),
        )

    @staticmethod
    def get_promotion_replay(
        transaction: TransactionProtocol,
        *,
        tenant_id: str,
        owner_user_id: str,
        replay_identity: str,
    ) -> Mapping[str, object] | None:
        return transaction.fetch_one(
            """
            SELECT promotion_id, request_hash, proposal_hash, source_patch_id
              FROM memory_patch.shared_memory_promotion_proposals
             WHERE tenant_id = %s AND owner_user_id = %s
               AND replay_identity = %s
            """,
            (tenant_id, owner_user_id, replay_identity),
        )

    @staticmethod
    def get_export_replay(
        transaction: TransactionProtocol,
        *,
        tenant_id: str,
        owner_user_id: str,
        replay_identity: str,
    ) -> PersonalMemoryLifecycleExportBundle | None:
        row = transaction.fetch_one(
            """
            SELECT request_hash, bundle_hash, bundle_payload
              FROM memory_patch.personal_memory_exports
             WHERE tenant_id = %s AND owner_user_id = %s
               AND replay_identity = %s
            """,
            (tenant_id, owner_user_id, replay_identity),
        )
        if row is None:
            return None
        raw = _json_value(row["bundle_payload"], "bundle_payload")
        bundle = parse_lifecycle_export_bundle(raw)
        if (
            row["request_hash"] != bundle.request_hash
            or row["bundle_hash"] != bundle.bundle_hash
        ):
            raise IntegrityError("persisted lifecycle export metadata differs")
        return bundle

    @staticmethod
    def lock_active_patch(
        transaction: TransactionProtocol,
        *,
        tenant_id: str,
        owner_user_id: str,
        personal_memory_space_id: str,
        patch_id: str,
        patch_hash: str,
    ) -> Mapping[str, object] | None:
        return transaction.fetch_one(
            """
            SELECT item.memory_item_id, item.step30_patch_hash,
                   item.step30_state_hash, item.active, item.revoked,
                   item.step32_terminal_kind, item.hat_scope_id
              FROM memory_patch.memory_items AS item
              JOIN memory_patch.memory_patch_proposals AS proposal
                ON proposal.tenant_id = item.tenant_id
               AND proposal.proposal_id = item.step30_proposal_id
               AND proposal.step30_patch_id = item.memory_item_id
             WHERE item.tenant_id = %s
               AND proposal.owner_user_id = %s
               AND proposal.personal_memory_space_id = %s
               AND item.memory_item_id = %s
               AND item.step30_patch_hash = %s
               AND item.active = true AND item.revoked = false
               AND item.step32_terminal_kind IS NULL
            """,
            (
                tenant_id,
                owner_user_id,
                personal_memory_space_id,
                patch_id,
                patch_hash,
            ),
        )

    @staticmethod
    def _terminalize_patch(
        transaction: TransactionProtocol,
        *,
        tenant_id: str,
        patch_id: str,
        patch_hash: str,
        terminal_kind: str,
        terminal_record_hash: str,
        effective_at: object,
        superseded_by_patch_id: str | None,
        revoked: bool,
    ) -> None:
        row = transaction.fetch_one(
            """
            UPDATE memory_patch.memory_items
               SET active = false,
                   revoked = %s,
                   step32_terminal_kind = %s,
                   step32_terminal_record_hash = %s,
                   step32_effective_at = %s,
                   step32_superseded_by_patch_id = %s
             WHERE tenant_id = %s AND memory_item_id = %s
               AND step30_patch_hash = %s
               AND active = true AND revoked = false
               AND step32_terminal_kind IS NULL
            RETURNING memory_item_id
            """,
            (
                revoked,
                terminal_kind,
                terminal_record_hash,
                effective_at,
                superseded_by_patch_id,
                tenant_id,
                patch_id,
                patch_hash,
            ),
        )
        if row is None:
            existing = transaction.fetch_one(
                """
                SELECT step32_terminal_kind, step32_terminal_record_hash,
                       step32_superseded_by_patch_id, active, revoked
                  FROM memory_patch.memory_items
                 WHERE tenant_id = %s AND memory_item_id = %s
                   AND step30_patch_hash = %s
                """,
                (tenant_id, patch_id, patch_hash),
            )
            if (
                existing is None
                or existing["step32_terminal_kind"] != terminal_kind
                or existing["step32_terminal_record_hash"] != terminal_record_hash
                or existing["step32_superseded_by_patch_id"]
                != superseded_by_patch_id
                or _boolean(existing["active"], "active")
                or _boolean(existing["revoked"], "revoked") is not revoked
            ):
                raise ImmutableRecordConflictError(
                    "patch terminal state conflicts",
                    sanitized_code="STEP32_PATCH_TERMINAL_CONFLICT",
                )

    @classmethod
    def persist_supersession(
        cls,
        transaction: TransactionProtocol,
        record: PersonalMemoryPatchSupersession,
    ) -> None:
        verify_patch_supersession(record)
        row = transaction.fetch_one(
            """
            INSERT INTO memory_patch.personal_memory_patch_supersessions (
              tenant_id, supersession_id, owner_user_id,
              personal_memory_space_id, old_proposal_id, old_patch_id,
              old_patch_hash, old_state_hash, new_proposal_id, new_patch_id,
              new_patch_hash, new_state_hash, request_hash, replay_identity,
              effective_at, state_version, actor_type, actor_id,
              supersession_hash, record_payload
            ) VALUES (
              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::JSONB
            ) ON CONFLICT DO NOTHING RETURNING supersession_id
            """,
            (
                record.tenant_id,
                record.supersession_id,
                record.owner_user_id,
                record.personal_memory_space_id,
                record.old_proposal_id,
                record.old_patch_id,
                record.old_patch_hash,
                record.old_state_hash,
                record.new_proposal_id,
                record.new_patch_id,
                record.new_patch_hash,
                record.new_state_hash,
                record.request_hash,
                record.replay_identity,
                record.effective_at,
                record.state_version,
                record.actor_type.value,
                record.actor_id,
                record.supersession_hash,
                canonical_json(step32_to_jsonb(record)),
            ),
        )
        if row is None:
            existing = cls.get_supersession_replay(
                transaction,
                tenant_id=record.tenant_id,
                owner_user_id=record.owner_user_id,
                replay_identity=record.replay_identity,
            )
            if (
                existing is None
                or existing["supersession_id"] != record.supersession_id
                or existing["request_hash"] != record.request_hash
                or existing["supersession_hash"] != record.supersession_hash
            ):
                raise ImmutableRecordConflictError(
                    "supersession replay conflicts",
                    sanitized_code="SUPERSESSION_REPLAY_CONFLICT",
                )
        cls._terminalize_patch(
            transaction,
            tenant_id=record.tenant_id,
            patch_id=record.old_patch_id,
            patch_hash=record.old_patch_hash,
            terminal_kind="SUPERSEDED",
            terminal_record_hash=record.supersession_hash,
            effective_at=record.effective_at,
            superseded_by_patch_id=record.new_patch_id,
            revoked=False,
        )

    @classmethod
    def persist_revocation(
        cls,
        transaction: TransactionProtocol,
        record: PersonalMemoryPatchRevocation,
    ) -> None:
        verify_patch_revocation(record)
        row = transaction.fetch_one(
            """
            INSERT INTO memory_patch.personal_memory_patch_revocations (
              tenant_id, revocation_id, owner_user_id,
              personal_memory_space_id, proposal_id, patch_id, patch_hash,
              active_state_hash, request_hash, replay_identity, effective_at,
              state_version, actor_type, actor_id, revocation_hash,
              record_payload
            ) VALUES (
              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
              %s, %s, %s, %s, %s, %s::JSONB
            ) ON CONFLICT DO NOTHING RETURNING revocation_id
            """,
            (
                record.tenant_id,
                record.revocation_id,
                record.owner_user_id,
                record.personal_memory_space_id,
                record.proposal_id,
                record.patch_id,
                record.patch_hash,
                record.active_state_hash,
                record.request_hash,
                record.replay_identity,
                record.effective_at,
                record.state_version,
                record.actor_type.value,
                record.actor_id,
                record.revocation_hash,
                canonical_json(step32_to_jsonb(record)),
            ),
        )
        if row is None:
            existing = cls.get_revocation_replay(
                transaction,
                tenant_id=record.tenant_id,
                owner_user_id=record.owner_user_id,
                replay_identity=record.replay_identity,
            )
            if (
                existing is None
                or existing["revocation_id"] != record.revocation_id
                or existing["request_hash"] != record.request_hash
                or existing["revocation_hash"] != record.revocation_hash
            ):
                raise ImmutableRecordConflictError(
                    "revocation replay conflicts",
                    sanitized_code="REVOCATION_REPLAY_CONFLICT",
                )
        cls._terminalize_patch(
            transaction,
            tenant_id=record.tenant_id,
            patch_id=record.patch_id,
            patch_hash=record.patch_hash,
            terminal_kind="REVOKED",
            terminal_record_hash=record.revocation_hash,
            effective_at=record.effective_at,
            superseded_by_patch_id=None,
            revoked=True,
        )

    @classmethod
    def persist_deletion(
        cls,
        transaction: TransactionProtocol,
        result: PersonalMemoryDeletionResult,
    ) -> None:
        verify_deletion_result(result)
        row = transaction.fetch_one(
            """
            INSERT INTO memory_patch.personal_memory_deletions (
              tenant_id, deletion_id, owner_user_id,
              personal_memory_space_id, proposal_id, patch_id, patch_hash,
              request_hash, replay_identity, tombstone_hash, deleted_at,
              logical_delete, physical_delete, result_hash, deletion_payload
            ) VALUES (
              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
              %s, true, false, %s, %s::JSONB
            ) ON CONFLICT DO NOTHING RETURNING deletion_id
            """,
            (
                result.tenant_id,
                result.deletion_id,
                result.owner_user_id,
                result.personal_memory_space_id,
                result.proposal_id,
                result.patch_id,
                result.patch_hash,
                result.request_hash,
                result.replay_identity,
                result.tombstone_hash,
                result.deleted_at,
                result.result_hash,
                canonical_json(step32_to_jsonb(result)),
            ),
        )
        if row is None:
            existing = cls.get_deletion_replay(
                transaction,
                tenant_id=result.tenant_id,
                owner_user_id=result.owner_user_id,
                replay_identity=result.replay_identity,
            )
            if (
                existing is None
                or existing["deletion_id"] != result.deletion_id
                or existing["request_hash"] != result.request_hash
                or existing["result_hash"] != result.result_hash
            ):
                raise ImmutableRecordConflictError(
                    "deletion replay conflicts",
                    sanitized_code="DELETE_REPLAY_CONFLICT",
                )
        cls._terminalize_patch(
            transaction,
            tenant_id=result.tenant_id,
            patch_id=result.patch_id,
            patch_hash=result.patch_hash,
            terminal_kind="DELETED",
            terminal_record_hash=result.result_hash,
            effective_at=result.deleted_at,
            superseded_by_patch_id=None,
            revoked=True,
        )

    @staticmethod
    def persist_promotion(
        transaction: TransactionProtocol,
        proposal: SharedMemoryPromotionProposal,
    ) -> None:
        verify_shared_memory_promotion_proposal(proposal)
        row = transaction.fetch_one(
            """
            INSERT INTO memory_patch.shared_memory_promotion_proposals (
              tenant_id, promotion_id, owner_user_id,
              personal_memory_space_id, source_proposal_id, source_patch_id,
              source_patch_hash, source_state_hash, target_hat_id,
              candidate_shared_statement_hash, deidentification_policy_digest,
              privacy_decision, owner_consent_hash,
              canonical_evidence_compatibility, review_required,
              lifecycle_state, request_hash, replay_identity, proposal_hash,
              promotion_payload, created_at
            ) VALUES (
              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
              %s, %s, %s, %s, true, 'SHARED_PROMOTION_PROPOSED',
              %s, %s, %s, %s::JSONB, %s
            ) ON CONFLICT DO NOTHING RETURNING promotion_id
            """,
            (
                proposal.tenant_id,
                proposal.promotion_id,
                proposal.owner_user_id,
                proposal.personal_memory_space_id,
                proposal.source_proposal_id,
                proposal.source_patch_id,
                proposal.source_patch_hash,
                proposal.source_state_hash,
                proposal.target_hat_id,
                proposal.candidate_shared_statement_sha256,
                proposal.deidentification.policy_digest,
                proposal.deidentification.decision.value,
                proposal.owner_consent_hash,
                proposal.canonical_evidence_compatibility.value,
                proposal.request_hash,
                proposal.replay_identity,
                proposal.proposal_hash,
                canonical_json(step32_to_jsonb(proposal)),
                proposal.created_at,
            ),
        )
        if row is None:
            existing = PersonalMemoryLifecycle32CockroachRepository.get_promotion_replay(
                transaction,
                tenant_id=proposal.tenant_id,
                owner_user_id=proposal.owner_user_id,
                replay_identity=proposal.replay_identity,
            )
            if (
                existing is None
                or existing["promotion_id"] != proposal.promotion_id
                or existing["request_hash"] != proposal.request_hash
                or existing["proposal_hash"] != proposal.proposal_hash
            ):
                raise ImmutableRecordConflictError(
                    "promotion replay conflicts",
                    sanitized_code="SHARED_PROMOTION_REPLAY_CONFLICT",
                )

    @staticmethod
    def persist_export(
        transaction: TransactionProtocol,
        bundle: PersonalMemoryLifecycleExportBundle,
    ) -> None:
        verify_lifecycle_export_bundle(bundle)
        row = transaction.fetch_one(
            """
            INSERT INTO memory_patch.personal_memory_exports (
              tenant_id, export_id, owner_user_id, personal_memory_space_id,
              request_hash, replay_identity, slot_hash, bundle_hash,
              record_count, bundle_payload, exported_at
            ) VALUES (
              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::JSONB, %s
            ) ON CONFLICT DO NOTHING RETURNING export_id
            """,
            (
                bundle.tenant_id,
                bundle.export_id,
                bundle.owner_user_id,
                bundle.personal_memory_space_id,
                bundle.request_hash,
                bundle.replay_identity,
                bundle.slot_hash,
                bundle.bundle_hash,
                len(bundle.records),
                canonical_json(step32_to_jsonb(bundle)),
                bundle.exported_at,
            ),
        )
        if row is None:
            existing = PersonalMemoryLifecycle32CockroachRepository.get_export_replay(
                transaction,
                tenant_id=bundle.tenant_id,
                owner_user_id=bundle.owner_user_id,
                replay_identity=bundle.replay_identity,
            )
            if existing is None or existing.bundle_hash != bundle.bundle_hash:
                raise ImmutableRecordConflictError(
                    "export replay conflicts",
                    sanitized_code="EXPORT_REPLAY_CONFLICT",
                )

    @staticmethod
    def export_records(
        transaction: TransactionProtocol,
        *,
        tenant_id: str,
        owner_user_id: str,
        personal_memory_space_id: str,
        slot_payload: Mapping[str, object],
        slot_hash: str,
    ) -> tuple[PersonalMemoryLifecycleExportRecord, ...]:
        records: list[PersonalMemoryLifecycleExportRecord] = [
            _export_record(
                "PERSONAL_MEMORY_SLOT",
                personal_memory_space_id,
                {"slot": to_canonical_data(slot_payload), "slot_hash": slot_hash},
            )
        ]
        query_specs = (
            (
                "PATCH_PROPOSAL",
                "proposal_id",
                """
                SELECT proposal_id, lifecycle_state, content_hash,
                       proposed_content
                  FROM memory_patch.memory_patch_proposals
                 WHERE tenant_id = %s AND owner_user_id = %s
                   AND personal_memory_space_id = %s
                   AND target_scope = 'USER_PERSONAL_HAT'
                """,
            ),
            (
                "APPROVAL_RECEIPT",
                "approval_id",
                """
                SELECT approval_id, proposal_id,
                       step30_approval_receipt_hash, decided_at,
                       step30_approval_payload AS approval_payload
                  FROM memory_patch.memory_patch_approvals
                 WHERE tenant_id = %s AND owner_user_id = %s
                   AND personal_memory_space_id = %s
                """,
            ),
            (
                "COMMIT_RECEIPT",
                "commit_id",
                """
                SELECT commit_id, proposal_id, committed_patch_id,
                       step30_commit_receipt_hash, committed_at,
                       step30_commit_payload AS commit_payload
                  FROM memory_patch.memory_patch_commits
                 WHERE tenant_id = %s AND owner_user_id = %s
                   AND personal_memory_space_id = %s
                """,
            ),
            (
                "MEMORY_PATCH",
                "memory_item_id",
                """
                SELECT item.memory_item_id, item.step30_patch_hash,
                       item.active, item.revoked, item.content,
                       item.step30_activation_receipt_hash,
                       item.step30_activation_payload AS activation_payload,
                       item.step32_terminal_kind,
                       item.step32_terminal_record_hash,
                       item.step32_effective_at,
                       item.step32_superseded_by_patch_id
                  FROM memory_patch.memory_items AS item
                  JOIN memory_patch.memory_patch_proposals AS proposal
                    ON proposal.tenant_id = item.tenant_id
                   AND proposal.proposal_id = item.step30_proposal_id
                 WHERE item.tenant_id = %s AND proposal.owner_user_id = %s
                   AND proposal.personal_memory_space_id = %s
                """,
            ),
            (
                "SUPERSESSION",
                "supersession_id",
                """
                SELECT supersession_id, supersession_hash,
                       record_payload
                  FROM memory_patch.personal_memory_patch_supersessions
                 WHERE tenant_id = %s AND owner_user_id = %s
                   AND personal_memory_space_id = %s
                """,
            ),
            (
                "REVOCATION",
                "revocation_id",
                """
                SELECT revocation_id, revocation_hash, record_payload
                  FROM memory_patch.personal_memory_patch_revocations
                 WHERE tenant_id = %s AND owner_user_id = %s
                   AND personal_memory_space_id = %s
                """,
            ),
            (
                "DELETION",
                "deletion_id",
                """
                SELECT deletion_id, result_hash,
                       deletion_payload AS record_payload
                  FROM memory_patch.personal_memory_deletions
                 WHERE tenant_id = %s AND owner_user_id = %s
                   AND personal_memory_space_id = %s
                """,
            ),
            (
                "SHARED_PROMOTION_PROPOSAL",
                "promotion_id",
                """
                SELECT promotion_id, proposal_hash,
                       promotion_payload AS record_payload
                  FROM memory_patch.shared_memory_promotion_proposals
                 WHERE tenant_id = %s AND owner_user_id = %s
                   AND personal_memory_space_id = %s
                """,
            ),
        )
        parameters = (tenant_id, owner_user_id, personal_memory_space_id)
        for record_type, id_column, sql in query_specs:
            bounded_sql = sql + f" ORDER BY {id_column} LIMIT %s"
            for row in transaction.fetch_all(
                bounded_sql,
                parameters + (MAXIMUM_EXPORT_RECORDS + 1,),
            ):
                records.append(_export_record(record_type, row[id_column], row))
                if len(records) > MAXIMUM_EXPORT_RECORDS:
                    raise PersistenceConfigurationError(
                        "lifecycle export record count exceeds bound",
                        sanitized_code="STEP32_EXPORT_TOO_LARGE",
                    )
        return tuple(sorted(records, key=lambda item: (item.record_type, item.record_id, item.record_hash)))


__all__ = ["PersonalMemoryLifecycle32CockroachRepository"]
