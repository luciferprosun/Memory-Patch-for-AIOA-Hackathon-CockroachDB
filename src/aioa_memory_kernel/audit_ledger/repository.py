"""CockroachDB adapter for the Step 33 owner-partitioned audit ledger."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from aioa_memory_kernel.contracts.exceptions import IntegrityError
from aioa_memory_kernel.contracts.serialization import canonical_json
from aioa_memory_kernel.persistence.errors import (
    ImmutableRecordConflictError,
    PersistenceConfigurationError,
)
from aioa_memory_kernel.persistence.protocols import TransactionProtocol

from .models import (
    STEP33_GENESIS_SENTINEL,
    AuditChainHead,
    AuditLedgerEntry,
    audit_to_jsonb,
    compute_audit_chain_id,
    parse_audit_append_receipt,
    parse_audit_event_envelope,
)


def _json_object(value: object, name: str) -> Mapping[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise PersistenceConfigurationError(
                f"{name} is invalid JSON",
                sanitized_code="INVALID_STEP33_AUDIT_ROW",
            ) from exc
    if not isinstance(value, Mapping):
        raise PersistenceConfigurationError(
            f"{name} is not an object",
            sanitized_code="INVALID_STEP33_AUDIT_ROW",
        )
    return value


def _integer(value: object, name: str) -> int:
    if isinstance(value, bool):
        raise PersistenceConfigurationError(
            f"{name} is invalid", sanitized_code="INVALID_STEP33_AUDIT_ROW"
        )
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise PersistenceConfigurationError(
            f"{name} is invalid", sanitized_code="INVALID_STEP33_AUDIT_ROW"
        ) from exc
    return parsed


def _timestamp(value: object, name: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise PersistenceConfigurationError(
                f"{name} is invalid",
                sanitized_code="INVALID_STEP33_AUDIT_ROW",
            ) from exc
    else:
        raise PersistenceConfigurationError(
            f"{name} is invalid",
            sanitized_code="INVALID_STEP33_AUDIT_ROW",
        )
    if parsed.tzinfo is None:
        raise PersistenceConfigurationError(
            f"{name} is not timezone-aware",
            sanitized_code="INVALID_STEP33_AUDIT_ROW",
        )
    return parsed.astimezone(UTC)


def _entry_from_row(row: Mapping[str, object]) -> AuditLedgerEntry:
    envelope = parse_audit_event_envelope(
        _json_object(row["step33_envelope"], "step33_envelope")
    )
    receipt = parse_audit_append_receipt(
        _json_object(row["step33_append_receipt"], "step33_append_receipt")
    )
    entry = AuditLedgerEntry(
        envelope=envelope,
        event_payload=_json_object(row["step33_payload"], "step33_payload"),
        append_receipt=receipt,
    )
    if (
        row.get("event_id") != envelope.event_id
        or row.get("event_hash") != envelope.event_hash
        or row.get("payload_hash") != envelope.event_payload_digest
        or row.get("previous_event_hash") != envelope.previous_event_hash
        or _timestamp(row.get("occurred_at"), "occurred_at")
        != envelope.occurred_at
        or _timestamp(row.get("recorded_at"), "recorded_at")
        != envelope.recorded_at
        or row.get("step33_entry_hash") != entry.entry_hash
    ):
        raise IntegrityError("persisted audit relational projection differs")
    return entry


class AuditLedgerCockroachRepository:
    """Append events and advance a locked chain head in one transaction.

    There is deliberately no audit-row UPDATE or DELETE method.
    """

    @staticmethod
    def lock_chain_head(
        transaction: TransactionProtocol,
        *,
        tenant_id: str,
        owner_user_id: str | None,
        updated_at: datetime,
    ) -> AuditChainHead:
        chain_id = compute_audit_chain_id(tenant_id, owner_user_id)
        transaction.execute(
            """
            INSERT INTO memory_patch.audit_chain_heads (
              tenant_id, owner_user_id, chain_id, last_sequence,
              last_event_hash, head_version, updated_at
            ) VALUES (%s, %s, %s, 0, %s, 0, %s)
            ON CONFLICT (tenant_id, chain_id) DO NOTHING
            """,
            (tenant_id, owner_user_id, chain_id, STEP33_GENESIS_SENTINEL, updated_at),
        )
        row = transaction.fetch_one(
            """
            SELECT tenant_id, owner_user_id, chain_id, last_sequence,
                   last_event_hash, head_version, updated_at
              FROM memory_patch.audit_chain_heads
             WHERE tenant_id = %s AND chain_id = %s
               AND owner_user_id IS NOT DISTINCT FROM %s
             FOR UPDATE
            """,
            (tenant_id, chain_id, owner_user_id),
        )
        if row is None:
            raise PersistenceConfigurationError(
                "audit chain head is outside request scope",
                sanitized_code="STEP33_AUDIT_SCOPE_DENIED",
            )
        return AuditChainHead(
            tenant_id=str(row["tenant_id"]),
            owner_user_id=(
                None if row["owner_user_id"] is None else str(row["owner_user_id"])
            ),
            chain_id=str(row["chain_id"]),
            last_sequence=_integer(row["last_sequence"], "last_sequence"),
            last_event_hash=str(row["last_event_hash"]),
            head_version=_integer(row["head_version"], "head_version"),
            updated_at=_timestamp(row["updated_at"], "updated_at"),
        )

    @staticmethod
    def get_replay(
        transaction: TransactionProtocol,
        *,
        tenant_id: str,
        chain_id: str,
        idempotency_key: str,
    ) -> AuditLedgerEntry | None:
        row = transaction.fetch_one(
            """
            SELECT event_id, event_hash, payload_hash, previous_event_hash,
                   occurred_at, recorded_at,
                   step33_envelope, step33_payload, step33_append_receipt,
                   step33_entry_hash
              FROM memory_patch.audit_events
             WHERE tenant_id = %s AND chain_id = %s
               AND idempotency_key = %s
            """,
            (tenant_id, chain_id, idempotency_key),
        )
        return None if row is None else _entry_from_row(row)

    @staticmethod
    def insert_entry(
        transaction: TransactionProtocol,
        entry: AuditLedgerEntry,
    ) -> None:
        event = entry.envelope
        row = transaction.fetch_one(
            """
            INSERT INTO memory_patch.audit_events (
              tenant_id, event_id, schema_version, event_type, actor_type,
              actor_id, kernel_run_id, user_id, personal_memory_space_id,
              payload_hash, previous_event_hash, event_hash, metadata,
              occurred_at, recorded_at, chain_id, sequence_number,
              idempotency_key, draft_hash, subject_type, subject_id,
              subject_hash, request_id, policy_id, policy_version,
              policy_digest, route_hash, lineage_hashes, reason_codes,
              step33_envelope, step33_payload, step33_append_receipt,
              step33_entry_hash
            ) VALUES (
              %s, %s, %s, %s, %s, %s, %s, %s, %s,
              %s, %s, %s, %s::JSONB, %s, %s, %s, %s,
              %s, %s, %s, %s, %s, %s, %s, %s, %s,
              %s, %s::JSONB, %s::JSONB, %s::JSONB, %s::JSONB,
              %s::JSONB, %s
            )
            ON CONFLICT DO NOTHING
            RETURNING event_id
            """,
            (
                event.tenant_id,
                event.event_id,
                event.schema_version,
                event.event_type.value,
                event.actor_type.value,
                event.actor_id,
                event.kernel_run_id,
                event.owner_user_id,
                event.personal_memory_space_id,
                event.event_payload_digest,
                event.previous_event_hash,
                event.event_hash,
                canonical_json({"step33_entry_hash": entry.entry_hash}),
                event.occurred_at,
                event.recorded_at,
                event.chain_id,
                event.sequence_number,
                event.idempotency_key,
                event.draft_hash,
                event.subject_type.value,
                event.subject_id,
                event.subject_hash,
                event.request_id,
                event.policy_id,
                event.policy_version,
                event.policy_digest,
                event.route_hash,
                canonical_json(event.lineage_hashes),
                canonical_json([item.value for item in event.reason_codes]),
                canonical_json(audit_to_jsonb(event)),
                canonical_json(entry.event_payload),
                canonical_json(audit_to_jsonb(entry.append_receipt)),
                entry.entry_hash,
            ),
        )
        if row is None:
            raise ImmutableRecordConflictError(
                "audit event identity already exists",
                sanitized_code="STEP33_AUDIT_APPEND_CONFLICT",
            )

    @staticmethod
    def advance_chain_head(
        transaction: TransactionProtocol,
        *,
        current: AuditChainHead,
        entry: AuditLedgerEntry,
    ) -> AuditChainHead:
        event = entry.envelope
        row = transaction.fetch_one(
            """
            UPDATE memory_patch.audit_chain_heads
               SET last_sequence = %s, last_event_hash = %s,
                   head_version = %s, updated_at = %s
             WHERE tenant_id = %s AND chain_id = %s
               AND owner_user_id IS NOT DISTINCT FROM %s
               AND last_sequence = %s AND last_event_hash = %s
               AND head_version = %s
            RETURNING tenant_id, owner_user_id, chain_id, last_sequence,
                      last_event_hash, head_version, updated_at
            """,
            (
                event.sequence_number,
                event.event_hash,
                current.head_version + 1,
                event.recorded_at,
                current.tenant_id,
                current.chain_id,
                current.owner_user_id,
                current.last_sequence,
                current.last_event_hash,
                current.head_version,
            ),
        )
        if row is None:
            raise ImmutableRecordConflictError(
                "audit chain head changed concurrently",
                sanitized_code="STEP33_AUDIT_HEAD_CONFLICT",
            )
        return AuditChainHead(
            tenant_id=str(row["tenant_id"]),
            owner_user_id=(
                None if row["owner_user_id"] is None else str(row["owner_user_id"])
            ),
            chain_id=str(row["chain_id"]),
            last_sequence=_integer(row["last_sequence"], "last_sequence"),
            last_event_hash=str(row["last_event_hash"]),
            head_version=_integer(row["head_version"], "head_version"),
            updated_at=_timestamp(row["updated_at"], "updated_at"),
        )

    @staticmethod
    def get_chain_head(
        transaction: TransactionProtocol,
        *,
        tenant_id: str,
        owner_user_id: str | None,
        chain_id: str,
    ) -> AuditChainHead | None:
        row = transaction.fetch_one(
            """
            SELECT tenant_id, owner_user_id, chain_id, last_sequence,
                   last_event_hash, head_version, updated_at
              FROM memory_patch.audit_chain_heads
             WHERE tenant_id = %s AND chain_id = %s
               AND owner_user_id IS NOT DISTINCT FROM %s
            """,
            (tenant_id, chain_id, owner_user_id),
        )
        if row is None:
            return None
        return AuditChainHead(
            tenant_id=str(row["tenant_id"]),
            owner_user_id=(
                None if row["owner_user_id"] is None else str(row["owner_user_id"])
            ),
            chain_id=str(row["chain_id"]),
            last_sequence=_integer(row["last_sequence"], "last_sequence"),
            last_event_hash=str(row["last_event_hash"]),
            head_version=_integer(row["head_version"], "head_version"),
            updated_at=_timestamp(row["updated_at"], "updated_at"),
        )

    @staticmethod
    def load_range(
        transaction: TransactionProtocol,
        *,
        tenant_id: str,
        owner_user_id: str | None,
        chain_id: str,
        start_sequence: int,
        end_sequence: int | None,
        maximum_events: int,
        event_type_values: tuple[str, ...] = (),
    ) -> tuple[AuditLedgerEntry, ...]:
        # Fetch one additional row so callers can produce stable continuation.
        sql = """
            SELECT event_id, event_hash, payload_hash, previous_event_hash,
                   occurred_at, recorded_at,
                   step33_envelope, step33_payload, step33_append_receipt,
                   step33_entry_hash
              FROM memory_patch.audit_events
             WHERE tenant_id = %s AND user_id IS NOT DISTINCT FROM %s
               AND chain_id = %s AND sequence_number >= %s
               AND (%s IS NULL OR sequence_number <= %s)
        """
        parameters: list[object] = [
            tenant_id,
            owner_user_id,
            chain_id,
            start_sequence,
            end_sequence,
            end_sequence,
        ]
        if event_type_values:
            placeholders = ", ".join("%s" for _ in event_type_values)
            sql += f" AND event_type IN ({placeholders})"
            parameters.extend(event_type_values)
        sql += " ORDER BY chain_id, sequence_number LIMIT %s"
        parameters.append(maximum_events + 1)
        rows = transaction.fetch_all(sql, tuple(parameters))
        return tuple(_entry_from_row(row) for row in rows)

    @staticmethod
    def predecessor_hash(
        transaction: TransactionProtocol,
        *,
        tenant_id: str,
        owner_user_id: str | None,
        chain_id: str,
        start_sequence: int,
    ) -> str | None:
        if start_sequence == 1:
            return STEP33_GENESIS_SENTINEL
        row = transaction.fetch_one(
            """
            SELECT event_hash
              FROM memory_patch.audit_events
             WHERE tenant_id = %s AND user_id IS NOT DISTINCT FROM %s
               AND chain_id = %s AND sequence_number = %s
            """,
            (tenant_id, owner_user_id, chain_id, start_sequence - 1),
        )
        return None if row is None else str(row["event_hash"])


__all__ = ["AuditLedgerCockroachRepository"]
