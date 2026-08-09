"""Typed immutable CockroachDB writes behind the bounded transaction facade."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from types import MappingProxyType
from typing import Any

from aioa_memory_kernel.contracts.serialization import canonical_json

from .errors import ImmutableRecordConflictError, PersistenceConfigurationError
from .models import (
    AuditEventRecord,
    DraftRecord,
    DraftV2Record,
    EvidenceItemRecord,
    KernelRunRecord,
    SourceSnapshotRecord,
)
from .protocols import Row, TransactionProtocol


def _json(value: Any) -> str:
    return canonical_json(value)


def _normalized(value: object) -> object:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.isoformat()
        return value.isoformat()
    if isinstance(value, Mapping):
        return json.loads(canonical_json(value))
    if isinstance(value, (list, tuple)):
        return json.loads(canonical_json(value))
    if isinstance(value, str) and value[:1] in {"{", "["}:
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _same(row: Mapping[str, object], expected: Mapping[str, object]) -> bool:
    return all(
        key in row and _normalized(row[key]) == _normalized(value)
        for key, value in expected.items()
    )


class CockroachPersistenceRepository:
    """Representative Step 6 production slice; no raw cursor is exposed."""

    def create_kernel_run(
        self,
        transaction: TransactionProtocol,
        record: KernelRunRecord,
    ) -> Row:
        if not isinstance(record, KernelRunRecord):
            raise PersistenceConfigurationError(
                "kernel run record has the wrong type",
                sanitized_code="INVALID_RECORD_TYPE",
            )
        columns = (
            "tenant_id, kernel_run_id, user_id, personal_memory_space_id, "
            "model_binding_id, request_sha256, created_at, completed_at"
        )
        expected = {
            "tenant_id": record.tenant_id,
            "kernel_run_id": record.kernel_run_id,
            "user_id": record.user_id,
            "personal_memory_space_id": record.personal_memory_space_id,
            "model_binding_id": record.model_binding_id,
            "request_sha256": record.request_sha256,
            "created_at": record.created_at,
            "completed_at": record.completed_at,
        }
        return self._put_immutable(
            transaction,
            select_sql=f"""
                SELECT {columns}
                  FROM memory_patch.kernel_runs
                 WHERE tenant_id = %s AND kernel_run_id = %s
            """,
            select_parameters=(record.tenant_id, record.kernel_run_id),
            insert_sql=f"""
                INSERT INTO memory_patch.kernel_runs (
                  tenant_id, kernel_run_id, user_id,
                  personal_memory_space_id, model_binding_id,
                  request_sha256, created_at, completed_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                RETURNING {columns}
            """,
            insert_parameters=(
                record.tenant_id,
                record.kernel_run_id,
                record.user_id,
                record.personal_memory_space_id,
                record.model_binding_id,
                record.request_sha256,
                record.created_at,
                record.completed_at,
            ),
            expected=expected,
            conflict_code="KERNEL_RUN_IMMUTABLE_CONFLICT",
        )

    def put_source_snapshot(
        self,
        transaction: TransactionProtocol,
        record: SourceSnapshotRecord,
    ) -> Row:
        if not isinstance(record, SourceSnapshotRecord):
            raise PersistenceConfigurationError(
                "source snapshot record has the wrong type",
                sanitized_code="INVALID_RECORD_TYPE",
            )
        columns = (
            "tenant_id, snapshot_id, source_id, hat_scope_id, content_sha256, "
            "byte_length, storage_class, immutable_object_reference, "
            "captured_at, source_observed_at, provenance"
        )
        expected = {
            "tenant_id": record.tenant_id,
            "snapshot_id": record.snapshot_id,
            "source_id": record.source_id,
            "hat_scope_id": record.hat_scope_id,
            "content_sha256": record.content_sha256,
            "byte_length": record.byte_length,
            "storage_class": record.storage_class,
            "immutable_object_reference": record.immutable_object_reference,
            "captured_at": record.captured_at,
            "source_observed_at": record.source_observed_at,
            "provenance": record.provenance,
        }
        return self._put_immutable(
            transaction,
            select_sql=f"""
                SELECT {columns}
                  FROM memory_patch.source_snapshots
                 WHERE tenant_id = %s AND snapshot_id = %s
            """,
            select_parameters=(record.tenant_id, record.snapshot_id),
            insert_sql=f"""
                INSERT INTO memory_patch.source_snapshots (
                  tenant_id, snapshot_id, source_id, hat_scope_id,
                  content_sha256, byte_length, storage_class,
                  immutable_object_reference, captured_at,
                  source_observed_at, provenance
                )
                VALUES (
                  %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::JSONB
                )
                ON CONFLICT DO NOTHING
                RETURNING {columns}
            """,
            insert_parameters=(
                record.tenant_id,
                record.snapshot_id,
                record.source_id,
                record.hat_scope_id,
                record.content_sha256,
                record.byte_length,
                record.storage_class,
                record.immutable_object_reference,
                record.captured_at,
                record.source_observed_at,
                _json(record.provenance),
            ),
            expected=expected,
            conflict_code="SOURCE_SNAPSHOT_IMMUTABLE_CONFLICT",
        )

    def get_draft_record(
        self,
        transaction: TransactionProtocol,
        *,
        tenant_id: str,
        draft_id: str,
    ) -> Row | None:
        if not isinstance(tenant_id, str) or not tenant_id:
            raise PersistenceConfigurationError(
                "tenant identity is invalid",
                sanitized_code="INVALID_DRAFT_LOOKUP",
            )
        if not isinstance(draft_id, str) or not draft_id:
            raise PersistenceConfigurationError(
                "draft identity is invalid",
                sanitized_code="INVALID_DRAFT_LOOKUP",
            )
        return transaction.fetch_one(
            """
                SELECT tenant_id, draft_id, kernel_run_id, draft_stage,
                       content_sha256, immutable_content_reference, created_at
                  FROM memory_patch.drafts
                 WHERE tenant_id = %s AND draft_id = %s
            """,
            (tenant_id, draft_id),
        )

    def put_draft(
        self,
        transaction: TransactionProtocol,
        record: DraftRecord | DraftV2Record,
    ) -> Row:
        if not isinstance(record, (DraftRecord, DraftV2Record)):
            raise PersistenceConfigurationError(
                "draft record has the wrong type",
                sanitized_code="INVALID_RECORD_TYPE",
            )
        columns = (
            "tenant_id, draft_id, kernel_run_id, draft_stage, content_sha256, "
            "immutable_content_reference, created_at"
        )
        expected = {
            "tenant_id": record.tenant_id,
            "draft_id": record.draft_id,
            "kernel_run_id": record.kernel_run_id,
            "draft_stage": record.draft_stage,
            "content_sha256": record.content_sha256,
            "immutable_content_reference": record.immutable_content_reference,
            "created_at": record.created_at,
        }
        return self._put_immutable(
            transaction,
            select_sql=f"""
                SELECT {columns}
                  FROM memory_patch.drafts
                 WHERE tenant_id = %s AND draft_id = %s
            """,
            select_parameters=(record.tenant_id, record.draft_id),
            insert_sql=f"""
                INSERT INTO memory_patch.drafts (
                  tenant_id, draft_id, kernel_run_id, draft_stage,
                  content_sha256, immutable_content_reference, created_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                RETURNING {columns}
            """,
            insert_parameters=(
                record.tenant_id,
                record.draft_id,
                record.kernel_run_id,
                record.draft_stage,
                record.content_sha256,
                record.immutable_content_reference,
                record.created_at,
            ),
            expected=expected,
            conflict_code="DRAFT_IMMUTABLE_CONFLICT",
        )

    def put_evidence_item(
        self,
        transaction: TransactionProtocol,
        record: EvidenceItemRecord,
    ) -> Row:
        if not isinstance(record, EvidenceItemRecord):
            raise PersistenceConfigurationError(
                "evidence item record has the wrong type",
                sanitized_code="INVALID_RECORD_TYPE",
            )
        columns = (
            "tenant_id, evidence_id, source_id, knowledge_version_id, "
            "hat_scope_id, citation_reference, content_sha256, trust_class, "
            "authority_rank, scope_dimensions, metadata, retrieved_at, "
            "valid_from, valid_until"
        )
        expected = {
            "tenant_id": record.tenant_id,
            "evidence_id": record.evidence_id,
            "source_id": record.source_id,
            "knowledge_version_id": record.knowledge_version_id,
            "hat_scope_id": record.hat_scope_id,
            "citation_reference": record.citation_reference,
            "content_sha256": record.content_sha256,
            "trust_class": record.trust_class,
            "authority_rank": record.authority_rank,
            "scope_dimensions": record.scope_dimensions,
            "metadata": record.metadata,
            "retrieved_at": record.retrieved_at,
            "valid_from": record.valid_from,
            "valid_until": record.valid_until,
        }
        return self._put_immutable(
            transaction,
            select_sql=f"""
                SELECT {columns}
                  FROM memory_patch.evidence_items
                 WHERE tenant_id = %s AND evidence_id = %s
            """,
            select_parameters=(record.tenant_id, record.evidence_id),
            insert_sql=f"""
                INSERT INTO memory_patch.evidence_items (
                  tenant_id, evidence_id, source_id, knowledge_version_id,
                  hat_scope_id, citation_reference, content_sha256,
                  trust_class, authority_rank, scope_dimensions, metadata,
                  retrieved_at, valid_from, valid_until
                )
                VALUES (
                  %s, %s, %s, %s, %s, %s, %s, %s, %s,
                  %s::JSONB, %s::JSONB, %s, %s, %s
                )
                ON CONFLICT DO NOTHING
                RETURNING {columns}
            """,
            insert_parameters=(
                record.tenant_id,
                record.evidence_id,
                record.source_id,
                record.knowledge_version_id,
                record.hat_scope_id,
                record.citation_reference,
                record.content_sha256,
                record.trust_class,
                record.authority_rank,
                _json(record.scope_dimensions),
                _json(record.metadata),
                record.retrieved_at,
                record.valid_from,
                record.valid_until,
            ),
            expected=expected,
            conflict_code="EVIDENCE_ITEM_IMMUTABLE_CONFLICT",
        )

    def append_audit_event(
        self,
        transaction: TransactionProtocol,
        record: AuditEventRecord,
    ) -> Row:
        if not isinstance(record, AuditEventRecord):
            raise PersistenceConfigurationError(
                "audit event record has the wrong type",
                sanitized_code="INVALID_RECORD_TYPE",
            )
        columns = (
            "tenant_id, event_id, schema_version, event_type, actor_type, "
            "actor_id, kernel_run_id, user_id, personal_memory_space_id, "
            "payload_hash, previous_event_hash, event_hash, metadata, occurred_at"
        )
        expected = {
            "tenant_id": record.tenant_id,
            "event_id": record.event_id,
            "schema_version": record.schema_version,
            "event_type": record.event_type,
            "actor_type": record.actor_type,
            "actor_id": record.actor_id,
            "kernel_run_id": record.kernel_run_id,
            "user_id": record.user_id,
            "personal_memory_space_id": record.personal_memory_space_id,
            "payload_hash": record.payload_hash,
            "previous_event_hash": record.previous_event_hash,
            "event_hash": record.event_hash,
            "metadata": record.metadata,
            "occurred_at": record.occurred_at,
        }
        return self._put_immutable(
            transaction,
            select_sql=f"""
                SELECT {columns}
                  FROM memory_patch.audit_events
                 WHERE tenant_id = %s AND event_id = %s
            """,
            select_parameters=(record.tenant_id, record.event_id),
            insert_sql=f"""
                INSERT INTO memory_patch.audit_events (
                  tenant_id, event_id, schema_version, event_type, actor_type,
                  actor_id, kernel_run_id, user_id, personal_memory_space_id,
                  payload_hash, previous_event_hash, event_hash, metadata,
                  occurred_at
                )
                VALUES (
                  %s, %s, %s, %s, %s, %s, %s, %s, %s,
                  %s, %s, %s, %s::JSONB, %s
                )
                ON CONFLICT DO NOTHING
                RETURNING {columns}
            """,
            insert_parameters=(
                record.tenant_id,
                record.event_id,
                record.schema_version,
                record.event_type,
                record.actor_type,
                record.actor_id,
                record.kernel_run_id,
                record.user_id,
                record.personal_memory_space_id,
                record.payload_hash,
                record.previous_event_hash,
                record.event_hash,
                _json(record.metadata),
                record.occurred_at,
            ),
            expected=expected,
            conflict_code="AUDIT_EVENT_IMMUTABLE_CONFLICT",
        )

    @staticmethod
    def _put_immutable(
        transaction: TransactionProtocol,
        *,
        select_sql: str,
        select_parameters: tuple[object, ...],
        insert_sql: str,
        insert_parameters: tuple[object, ...],
        expected: Mapping[str, object],
        conflict_code: str,
    ) -> Row:
        existing = transaction.fetch_one(select_sql, select_parameters)
        if existing is not None:
            if _same(existing, expected):
                return MappingProxyType(dict(existing))
            raise ImmutableRecordConflictError(
                "immutable identity is bound to different facts",
                sanitized_code=conflict_code,
            )
        inserted = transaction.fetch_one(insert_sql, insert_parameters)
        if inserted is not None:
            if not _same(inserted, expected):
                raise ImmutableRecordConflictError(
                    "database returned conflicting immutable facts",
                    sanitized_code=conflict_code,
                )
            return MappingProxyType(dict(inserted))
        raced = transaction.fetch_one(select_sql, select_parameters)
        if raced is not None and _same(raced, expected):
            return MappingProxyType(dict(raced))
        raise ImmutableRecordConflictError(
            "immutable identity collided with different facts",
            sanitized_code=conflict_code,
        )
