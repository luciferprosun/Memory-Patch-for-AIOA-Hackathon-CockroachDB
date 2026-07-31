"""CockroachDB persistence for the Step 10 ingestion saga."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from aioa_memory_kernel.contracts.serialization import canonical_json
from aioa_memory_kernel.persistence import PersistenceConfigurationError
from aioa_memory_kernel.persistence.protocols import TransactionProtocol

from .errors import (
    IngestionClaimError,
    IngestionConflictError,
    IngestionTransitionError,
)
from .models import (
    ExternalEffectIntent,
    ExternalEffectKind,
    ExternalEffectReceipt,
    ExternalEffectRecord,
    ExternalEffectStatus,
    IngestionSaga,
    OrphanBackend,
    OrphanClassification,
    OrphanRecord,
    OrphanResolution,
    SagaExecutionDisposition,
    SagaMilestone,
    SagaTransitionEvent,
)


SAGA_COLUMNS = """
tenant_id, saga_id, source_id, hat_scope_id, owner_user_id,
knowledge_version_id, schema_version, idempotency_key, request_digest,
scope_digest, source_registry_digest, content_sha256, content_length,
media_type, local_relative_path, snapshot_id, captured_at, retain_until,
current_milestone, execution_disposition, state_version, attempt_count,
event_sequence, current_event_digest, next_retry_at, claim_token_digest,
claimed_at, claim_expires_at, quarantine_reason, run_digest, created_at,
updated_at, terminal_at
""".replace("\n", " ")

EVENT_COLUMNS = """
tenant_id, saga_id, event_id, sequence_number, from_milestone, to_milestone,
reason_code, actor_boundary, idempotency_reference,
prerequisite_receipt_digests, previous_event_digest, event_digest, created_at
""".replace("\n", " ")

EFFECT_COLUMNS = """
tenant_id, saga_id, effect_id, effect_kind, deterministic_locator,
expected_snapshot_id, expected_sha256, expected_length, intent_digest,
intent_created_at, status, evidence_digest, evidence, receipt_digest,
completed_at
""".replace("\n", " ")

ORPHAN_COLUMNS = """
tenant_id, orphan_id, saga_id, backend, deterministic_locator,
expected_snapshot_id, observed_evidence_digest, classification, resolution,
reason_code, retention_constraint, cleanup_performed, record_digest, created_at
""".replace("\n", " ")


def _timestamp(value: object, field_name: str) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            parsed = None
        if parsed is not None and parsed.tzinfo is not None:
            return parsed
    raise PersistenceConfigurationError(
        f"database returned invalid {field_name}",
        sanitized_code="INVALID_INGESTION_DATABASE_ROW",
    )


def _optional_timestamp(value: object, field_name: str) -> datetime | None:
    return None if value is None else _timestamp(value, field_name)


def _json_value(value: object, expected: type, field_name: str) -> Any:
    parsed = value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise PersistenceConfigurationError(
                f"database returned invalid {field_name}",
                sanitized_code="INVALID_INGESTION_DATABASE_ROW",
            ) from exc
    if not isinstance(parsed, expected):
        raise PersistenceConfigurationError(
            f"database returned invalid {field_name}",
            sanitized_code="INVALID_INGESTION_DATABASE_ROW",
        )
    return parsed


def _boolean(value: object, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    if value in {"t", "true", "TRUE", "1", 1}:
        return True
    if value in {"f", "false", "FALSE", "0", 0}:
        return False
    raise PersistenceConfigurationError(
        f"database returned invalid {field_name}",
        sanitized_code="INVALID_INGESTION_DATABASE_ROW",
    )


def saga_from_row(row: Mapping[str, object]) -> IngestionSaga:
    try:
        return IngestionSaga(
            tenant_id=str(row["tenant_id"]),
            saga_id=str(row["saga_id"]),
            source_id=str(row["source_id"]),
            hat_scope_id=str(row["hat_scope_id"]),
            owner_user_id=(
                None
                if row.get("owner_user_id") is None
                else str(row["owner_user_id"])
            ),
            knowledge_version_id=str(row["knowledge_version_id"]),
            idempotency_key=str(row["idempotency_key"]),
            request_digest=str(row["request_digest"]),
            scope_digest=str(row["scope_digest"]),
            source_registry_digest=str(row["source_registry_digest"]),
            content_sha256=str(row["content_sha256"]),
            content_length=int(row["content_length"]),
            media_type=str(row["media_type"]),
            local_relative_path=str(row["local_relative_path"]),
            snapshot_id=str(row["snapshot_id"]),
            captured_at=_timestamp(row["captured_at"], "captured_at"),
            retain_until=_timestamp(row["retain_until"], "retain_until"),
            current_milestone=SagaMilestone(str(row["current_milestone"])),
            execution_disposition=SagaExecutionDisposition(
                str(row["execution_disposition"])
            ),
            state_version=int(row["state_version"]),
            attempt_count=int(row["attempt_count"]),
            event_sequence=int(row["event_sequence"]),
            current_event_digest=str(row["current_event_digest"]),
            next_retry_at=_optional_timestamp(
                row.get("next_retry_at"),
                "next_retry_at",
            ),
            claim_token_digest=(
                None
                if row.get("claim_token_digest") is None
                else str(row["claim_token_digest"])
            ),
            claimed_at=_optional_timestamp(row.get("claimed_at"), "claimed_at"),
            claim_expires_at=_optional_timestamp(
                row.get("claim_expires_at"),
                "claim_expires_at",
            ),
            quarantine_reason=(
                None
                if row.get("quarantine_reason") is None
                else str(row["quarantine_reason"])
            ),
            created_at=_timestamp(row["created_at"], "created_at"),
            updated_at=_timestamp(row["updated_at"], "updated_at"),
            terminal_at=_optional_timestamp(
                row.get("terminal_at"),
                "terminal_at",
            ),
            run_digest=str(row["run_digest"]),
            schema_version=str(row["schema_version"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise PersistenceConfigurationError(
            "database returned an invalid ingestion saga row",
            sanitized_code="INVALID_INGESTION_DATABASE_ROW",
        ) from exc


def event_from_row(row: Mapping[str, object]) -> SagaTransitionEvent:
    try:
        receipts = _json_value(
            row["prerequisite_receipt_digests"],
            list,
            "prerequisite_receipt_digests",
        )
        return SagaTransitionEvent(
            tenant_id=str(row["tenant_id"]),
            saga_id=str(row["saga_id"]),
            event_id=str(row["event_id"]),
            sequence_number=int(row["sequence_number"]),
            from_milestone=SagaMilestone(str(row["from_milestone"])),
            to_milestone=SagaMilestone(str(row["to_milestone"])),
            reason_code=str(row["reason_code"]),
            actor_boundary=str(row["actor_boundary"]),
            idempotency_reference=str(row["idempotency_reference"]),
            prerequisite_receipt_digests=tuple(
                str(value) for value in receipts
            ),
            previous_event_digest=str(row["previous_event_digest"]),
            event_digest=str(row["event_digest"]),
            created_at=_timestamp(row["created_at"], "created_at"),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise PersistenceConfigurationError(
            "database returned an invalid ingestion event row",
            sanitized_code="INVALID_INGESTION_DATABASE_ROW",
        ) from exc


def effect_from_row(row: Mapping[str, object]) -> ExternalEffectRecord:
    try:
        intent = ExternalEffectIntent(
            tenant_id=str(row["tenant_id"]),
            saga_id=str(row["saga_id"]),
            effect_id=str(row["effect_id"]),
            effect_kind=ExternalEffectKind(str(row["effect_kind"])),
            deterministic_locator=str(row["deterministic_locator"]),
            expected_snapshot_id=str(row["expected_snapshot_id"]),
            expected_sha256=str(row["expected_sha256"]),
            expected_length=int(row["expected_length"]),
            intent_digest=str(row["intent_digest"]),
            created_at=_timestamp(row["intent_created_at"], "intent_created_at"),
        )
        status = ExternalEffectStatus(str(row["status"]))
        receipt = None
        if status is ExternalEffectStatus.RECEIPT_RECORDED:
            evidence = _json_value(row["evidence"], dict, "effect evidence")
            receipt = ExternalEffectReceipt(
                tenant_id=intent.tenant_id,
                saga_id=intent.saga_id,
                effect_id=intent.effect_id,
                effect_kind=intent.effect_kind,
                intent_digest=intent.intent_digest,
                evidence_digest=str(row["evidence_digest"]),
                evidence=evidence,
                receipt_digest=str(row["receipt_digest"]),
                completed_at=_timestamp(row["completed_at"], "completed_at"),
            )
        return ExternalEffectRecord(
            intent=intent,
            status=status,
            receipt=receipt,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise PersistenceConfigurationError(
            "database returned an invalid external effect row",
            sanitized_code="INVALID_INGESTION_DATABASE_ROW",
        ) from exc


def orphan_from_row(row: Mapping[str, object]) -> OrphanRecord:
    try:
        return OrphanRecord(
            tenant_id=str(row["tenant_id"]),
            orphan_id=str(row["orphan_id"]),
            saga_id=(
                None if row.get("saga_id") is None else str(row["saga_id"])
            ),
            backend=OrphanBackend(str(row["backend"])),
            deterministic_locator=str(row["deterministic_locator"]),
            expected_snapshot_id=str(row["expected_snapshot_id"]),
            observed_evidence_digest=str(row["observed_evidence_digest"]),
            classification=OrphanClassification(str(row["classification"])),
            resolution=OrphanResolution(str(row["resolution"])),
            reason_code=str(row["reason_code"]),
            retention_constraint=str(row["retention_constraint"]),
            cleanup_performed=_boolean(
                row["cleanup_performed"],
                "cleanup_performed",
            ),
            record_digest=str(row["record_digest"]),
            created_at=_timestamp(row["created_at"], "created_at"),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise PersistenceConfigurationError(
            "database returned an invalid orphan row",
            sanitized_code="INVALID_INGESTION_DATABASE_ROW",
        ) from exc


class CockroachIngestionSagaRepository:
    """Typed compare-and-set SQL behind the Step 6 transaction facade."""

    def get_saga(
        self,
        transaction: TransactionProtocol,
        tenant_id: str,
        saga_id: str,
    ) -> IngestionSaga | None:
        row = transaction.fetch_one(
            f"""
            SELECT {SAGA_COLUMNS}
              FROM memory_patch.ingestion_sagas
             WHERE tenant_id = %s AND saga_id = %s
            """,
            (tenant_id, saga_id),
        )
        return None if row is None else saga_from_row(row)

    def put_saga(
        self,
        transaction: TransactionProtocol,
        saga: IngestionSaga,
    ) -> IngestionSaga:
        row = transaction.fetch_one(
            f"""
            INSERT INTO memory_patch.ingestion_sagas (
              tenant_id, saga_id, source_id, hat_scope_id, owner_user_id,
              knowledge_version_id, schema_version, idempotency_key,
              request_digest, scope_digest, source_registry_digest,
              content_sha256, content_length, media_type, local_relative_path,
              snapshot_id, captured_at, retain_until, current_milestone,
              execution_disposition, state_version, attempt_count,
              event_sequence, current_event_digest, next_retry_at,
              claim_token_digest, claimed_at, claim_expires_at,
              quarantine_reason, run_digest, created_at, updated_at, terminal_at
            )
            VALUES (
              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            ON CONFLICT DO NOTHING
            RETURNING {SAGA_COLUMNS}
            """,
            (
                saga.tenant_id,
                saga.saga_id,
                saga.source_id,
                saga.hat_scope_id,
                saga.owner_user_id,
                saga.knowledge_version_id,
                saga.schema_version,
                saga.idempotency_key,
                saga.request_digest,
                saga.scope_digest,
                saga.source_registry_digest,
                saga.content_sha256,
                saga.content_length,
                saga.media_type,
                saga.local_relative_path,
                saga.snapshot_id,
                saga.captured_at,
                saga.retain_until,
                saga.current_milestone.value,
                saga.execution_disposition.value,
                saga.state_version,
                saga.attempt_count,
                saga.event_sequence,
                saga.current_event_digest,
                saga.next_retry_at,
                saga.claim_token_digest,
                saga.claimed_at,
                saga.claim_expires_at,
                saga.quarantine_reason,
                saga.run_digest,
                saga.created_at,
                saga.updated_at,
                saga.terminal_at,
            ),
        )
        if row is not None:
            return saga_from_row(row)
        existing = self.get_saga(transaction, saga.tenant_id, saga.saga_id)
        if existing == saga:
            return existing
        raise IngestionConflictError(
            "saga identity is already bound to different immutable facts",
            sanitized_code="INGESTION_SAGA_CONFLICT",
        )

    def get_event(
        self,
        transaction: TransactionProtocol,
        tenant_id: str,
        saga_id: str,
        event_id: str,
    ) -> SagaTransitionEvent | None:
        row = transaction.fetch_one(
            f"""
            SELECT {EVENT_COLUMNS}
              FROM memory_patch.ingestion_saga_events
             WHERE tenant_id = %s AND saga_id = %s AND event_id = %s
            """,
            (tenant_id, saga_id, event_id),
        )
        return None if row is None else event_from_row(row)

    def list_events(
        self,
        transaction: TransactionProtocol,
        tenant_id: str,
        saga_id: str,
    ) -> tuple[SagaTransitionEvent, ...]:
        rows = transaction.fetch_all(
            f"""
            SELECT {EVENT_COLUMNS}
              FROM memory_patch.ingestion_saga_events
             WHERE tenant_id = %s AND saga_id = %s
             ORDER BY sequence_number
            """,
            (tenant_id, saga_id),
        )
        return tuple(event_from_row(row) for row in rows)

    def append_event(
        self,
        transaction: TransactionProtocol,
        event: SagaTransitionEvent,
    ) -> SagaTransitionEvent:
        row = transaction.fetch_one(
            f"""
            INSERT INTO memory_patch.ingestion_saga_events (
              tenant_id, saga_id, hat_scope_id, event_id, sequence_number,
              from_milestone, to_milestone, reason_code, actor_boundary,
              idempotency_reference, prerequisite_receipt_digests,
              previous_event_digest, event_digest, created_at
            )
            SELECT %s, %s, saga.hat_scope_id, %s, %s, %s, %s, %s, %s,
                   %s, %s::JSONB, %s, %s, %s
              FROM memory_patch.ingestion_sagas AS saga
             WHERE saga.tenant_id = %s AND saga.saga_id = %s
            ON CONFLICT DO NOTHING
            RETURNING {EVENT_COLUMNS}
            """,
            (
                event.tenant_id,
                event.saga_id,
                event.event_id,
                event.sequence_number,
                event.from_milestone.value,
                event.to_milestone.value,
                event.reason_code,
                event.actor_boundary,
                event.idempotency_reference,
                canonical_json(event.prerequisite_receipt_digests),
                event.previous_event_digest,
                event.event_digest,
                event.created_at,
                event.tenant_id,
                event.saga_id,
            ),
        )
        if row is not None:
            return event_from_row(row)
        existing = self.get_event(
            transaction,
            event.tenant_id,
            event.saga_id,
            event.event_id,
        )
        if existing == event:
            return existing
        raise IngestionConflictError(
            "saga event identity collided with different canonical facts",
            sanitized_code="INGESTION_EVENT_CONFLICT",
        )

    def compare_and_set_transition(
        self,
        transaction: TransactionProtocol,
        saga: IngestionSaga,
        event: SagaTransitionEvent,
    ) -> IngestionSaga:
        terminal = event.to_milestone is SagaMilestone.PUBLISHED
        row = transaction.fetch_one(
            f"""
            UPDATE memory_patch.ingestion_sagas
               SET current_milestone = %s,
                   execution_disposition = %s,
                   state_version = state_version + 1,
                   event_sequence = %s,
                   current_event_digest = %s,
                   next_retry_at = NULL,
                   claim_token_digest = NULL,
                   claimed_at = NULL,
                   claim_expires_at = NULL,
                   updated_at = %s,
                   terminal_at = %s
             WHERE tenant_id = %s
               AND saga_id = %s
               AND state_version = %s
               AND current_milestone = %s
               AND event_sequence = %s
               AND current_event_digest = %s
            RETURNING {SAGA_COLUMNS}
            """,
            (
                event.to_milestone.value,
                (
                    SagaExecutionDisposition.COMPLETED.value
                    if terminal
                    else SagaExecutionDisposition.READY.value
                ),
                event.sequence_number,
                event.event_digest,
                event.created_at,
                event.created_at if terminal else None,
                saga.tenant_id,
                saga.saga_id,
                saga.state_version,
                saga.current_milestone.value,
                saga.event_sequence,
                saga.current_event_digest,
            ),
        )
        if row is None:
            raise IngestionTransitionError(
                "saga transition compare-and-set observed stale state",
                sanitized_code="STALE_INGESTION_STATE",
            )
        return saga_from_row(row)

    def get_effect(
        self,
        transaction: TransactionProtocol,
        tenant_id: str,
        saga_id: str,
        effect_id: str,
    ) -> ExternalEffectRecord | None:
        row = transaction.fetch_one(
            f"""
            SELECT {EFFECT_COLUMNS}
              FROM memory_patch.ingestion_external_effects
             WHERE tenant_id = %s AND saga_id = %s AND effect_id = %s
            """,
            (tenant_id, saga_id, effect_id),
        )
        return None if row is None else effect_from_row(row)

    def put_effect_intent(
        self,
        transaction: TransactionProtocol,
        intent: ExternalEffectIntent,
    ) -> ExternalEffectRecord:
        row = transaction.fetch_one(
            f"""
            INSERT INTO memory_patch.ingestion_external_effects (
              tenant_id, saga_id, hat_scope_id, effect_id, effect_kind,
              deterministic_locator, expected_snapshot_id, expected_sha256,
              expected_length, intent_digest, intent_created_at, status,
              evidence_digest, evidence, receipt_digest, completed_at
            )
            SELECT %s, %s, saga.hat_scope_id, %s, %s, %s, %s, %s, %s, %s, %s,
                   'INTENT_RECORDED', NULL, NULL, NULL, NULL
              FROM memory_patch.ingestion_sagas AS saga
             WHERE saga.tenant_id = %s AND saga.saga_id = %s
            ON CONFLICT DO NOTHING
            RETURNING {EFFECT_COLUMNS}
            """,
            (
                intent.tenant_id,
                intent.saga_id,
                intent.effect_id,
                intent.effect_kind.value,
                intent.deterministic_locator,
                intent.expected_snapshot_id,
                intent.expected_sha256,
                intent.expected_length,
                intent.intent_digest,
                intent.created_at,
                intent.tenant_id,
                intent.saga_id,
            ),
        )
        if row is not None:
            return effect_from_row(row)
        existing = self.get_effect(
            transaction,
            intent.tenant_id,
            intent.saga_id,
            intent.effect_id,
        )
        if existing is not None and existing.intent == intent:
            return existing
        raise IngestionConflictError(
            "external intent identity is already bound to different facts",
            sanitized_code="EXTERNAL_INTENT_CONFLICT",
        )

    def attach_effect_receipt(
        self,
        transaction: TransactionProtocol,
        receipt: ExternalEffectReceipt,
    ) -> ExternalEffectRecord:
        row = transaction.fetch_one(
            f"""
            UPDATE memory_patch.ingestion_external_effects
               SET status = 'RECEIPT_RECORDED',
                   evidence_digest = %s,
                   evidence = %s::JSONB,
                   receipt_digest = %s,
                   completed_at = %s
             WHERE tenant_id = %s
               AND saga_id = %s
               AND effect_id = %s
               AND effect_kind = %s
               AND intent_digest = %s
               AND status = 'INTENT_RECORDED'
            RETURNING {EFFECT_COLUMNS}
            """,
            (
                receipt.evidence_digest,
                canonical_json(receipt.evidence),
                receipt.receipt_digest,
                receipt.completed_at,
                receipt.tenant_id,
                receipt.saga_id,
                receipt.effect_id,
                receipt.effect_kind.value,
                receipt.intent_digest,
            ),
        )
        if row is not None:
            return effect_from_row(row)
        existing = self.get_effect(
            transaction,
            receipt.tenant_id,
            receipt.saga_id,
            receipt.effect_id,
        )
        expected = (
            None
            if existing is None
            else ExternalEffectRecord(
                intent=existing.intent,
                status=ExternalEffectStatus.RECEIPT_RECORDED,
                receipt=receipt,
            )
        )
        if existing == expected:
            return existing
        raise IngestionConflictError(
            "external receipt conflicts with durable intent or prior evidence",
            sanitized_code="EXTERNAL_RECEIPT_CONFLICT",
        )

    def has_effect_prerequisite(
        self,
        transaction: TransactionProtocol,
        *,
        tenant_id: str,
        saga_id: str,
        effect_kind: object,
        prerequisite_digest: str,
        require_receipt: bool,
    ) -> bool:
        if not isinstance(effect_kind, ExternalEffectKind):
            return False
        digest_column = "receipt_digest" if require_receipt else "intent_digest"
        status = (
            ExternalEffectStatus.RECEIPT_RECORDED.value
            if require_receipt
            else ExternalEffectStatus.INTENT_RECORDED.value
        )
        row = transaction.fetch_one(
            f"""
            SELECT 1 AS found
              FROM memory_patch.ingestion_external_effects
             WHERE tenant_id = %s
               AND saga_id = %s
               AND effect_kind = %s
               AND {digest_column} = %s
               AND (
                 status = %s
                 OR (%s = 'INTENT_RECORDED' AND status = 'RECEIPT_RECORDED')
               )
            """,
            (
                tenant_id,
                saga_id,
                effect_kind.value,
                prerequisite_digest,
                status,
                status,
            ),
        )
        return row is not None

    def claim_worker(
        self,
        transaction: TransactionProtocol,
        saga: IngestionSaga,
        *,
        claim_token_digest: str,
        claimed_at: object,
        claim_expires_at: object,
    ) -> IngestionSaga:
        row = transaction.fetch_one(
            f"""
            UPDATE memory_patch.ingestion_sagas
               SET execution_disposition = 'CLAIMED',
                   state_version = state_version + 1,
                   attempt_count = attempt_count + 1,
                   claim_token_digest = %s,
                   claimed_at = %s,
                   claim_expires_at = %s,
                   next_retry_at = NULL,
                   updated_at = %s
             WHERE tenant_id = %s
               AND saga_id = %s
               AND state_version = %s
               AND current_milestone <> 'PUBLISHED'
               AND execution_disposition NOT IN (
                 'QUARANTINED', 'OPERATOR_REVIEW', 'COMPLETED'
               )
               AND (claim_expires_at IS NULL OR claim_expires_at <= %s)
               AND (next_retry_at IS NULL OR next_retry_at <= %s)
            RETURNING {SAGA_COLUMNS}
            """,
            (
                claim_token_digest,
                claimed_at,
                claim_expires_at,
                claimed_at,
                saga.tenant_id,
                saga.saga_id,
                saga.state_version,
                claimed_at,
                claimed_at,
            ),
        )
        if row is None:
            raise IngestionClaimError(
                "saga could not be claimed with the expected state",
                sanitized_code="INGESTION_CLAIM_CONFLICT",
            )
        return saga_from_row(row)

    def release_worker(
        self,
        transaction: TransactionProtocol,
        saga: IngestionSaga,
        *,
        claim_token_digest: str,
        released_at: object,
    ) -> IngestionSaga:
        row = transaction.fetch_one(
            f"""
            UPDATE memory_patch.ingestion_sagas
               SET execution_disposition = 'READY',
                   state_version = state_version + 1,
                   claim_token_digest = NULL,
                   claimed_at = NULL,
                   claim_expires_at = NULL,
                   updated_at = %s
             WHERE tenant_id = %s
               AND saga_id = %s
               AND state_version = %s
               AND execution_disposition = 'CLAIMED'
               AND claim_token_digest = %s
            RETURNING {SAGA_COLUMNS}
            """,
            (
                released_at,
                saga.tenant_id,
                saga.saga_id,
                saga.state_version,
                claim_token_digest,
            ),
        )
        if row is None:
            raise IngestionClaimError(
                "worker release does not own the active saga claim",
                sanitized_code="INGESTION_CLAIM_OWNERSHIP_MISMATCH",
            )
        return saga_from_row(row)

    def set_disposition(
        self,
        transaction: TransactionProtocol,
        saga: IngestionSaga,
        *,
        disposition: SagaExecutionDisposition,
        reason_code: str | None,
        next_retry_at: object | None,
        changed_at: object,
    ) -> IngestionSaga:
        quarantine_reason = (
            reason_code
            if disposition is SagaExecutionDisposition.QUARANTINED
            else None
        )
        row = transaction.fetch_one(
            f"""
            UPDATE memory_patch.ingestion_sagas
               SET execution_disposition = %s,
                   state_version = state_version + 1,
                   next_retry_at = %s,
                   claim_token_digest = NULL,
                   claimed_at = NULL,
                   claim_expires_at = NULL,
                   quarantine_reason = %s,
                   updated_at = %s
             WHERE tenant_id = %s
               AND saga_id = %s
               AND state_version = %s
               AND current_milestone <> 'PUBLISHED'
            RETURNING {SAGA_COLUMNS}
            """,
            (
                disposition.value,
                next_retry_at,
                quarantine_reason,
                changed_at,
                saga.tenant_id,
                saga.saga_id,
                saga.state_version,
            ),
        )
        if row is None:
            raise IngestionTransitionError(
                "saga disposition compare-and-set observed stale state",
                sanitized_code="STALE_INGESTION_STATE",
            )
        return saga_from_row(row)

    def put_orphan(
        self,
        transaction: TransactionProtocol,
        orphan: OrphanRecord,
    ) -> OrphanRecord:
        row = transaction.fetch_one(
            f"""
            INSERT INTO memory_patch.ingestion_orphans (
              tenant_id, orphan_id, saga_id, hat_scope_id, backend,
              deterministic_locator, expected_snapshot_id,
              observed_evidence_digest, classification, resolution,
              reason_code, retention_constraint, cleanup_performed,
              record_digest, created_at
            )
            SELECT %s, %s, %s, saga.hat_scope_id, %s, %s, %s, %s, %s,
                   %s, %s, %s, %s, %s, %s
              FROM (SELECT 1 AS seed) AS seed
              LEFT JOIN memory_patch.ingestion_sagas AS saga
                ON saga.tenant_id = %s AND saga.saga_id = %s
             WHERE %s IS NULL OR saga.saga_id IS NOT NULL
            ON CONFLICT DO NOTHING
            RETURNING {ORPHAN_COLUMNS}
            """,
            (
                orphan.tenant_id,
                orphan.orphan_id,
                orphan.saga_id,
                orphan.backend.value,
                orphan.deterministic_locator,
                orphan.expected_snapshot_id,
                orphan.observed_evidence_digest,
                orphan.classification.value,
                orphan.resolution.value,
                orphan.reason_code,
                orphan.retention_constraint,
                orphan.cleanup_performed,
                orphan.record_digest,
                orphan.created_at,
                orphan.tenant_id,
                orphan.saga_id,
                orphan.saga_id,
            ),
        )
        if row is not None:
            return orphan_from_row(row)
        existing = transaction.fetch_one(
            f"""
            SELECT {ORPHAN_COLUMNS}
              FROM memory_patch.ingestion_orphans
             WHERE tenant_id = %s AND orphan_id = %s
            """,
            (orphan.tenant_id, orphan.orphan_id),
        )
        stored = None if existing is None else orphan_from_row(existing)
        if stored == orphan:
            return stored
        raise IngestionConflictError(
            "orphan identity collided with different evidence",
            sanitized_code="INGESTION_ORPHAN_CONFLICT",
        )
