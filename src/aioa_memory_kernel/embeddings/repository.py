"""CockroachDB persistence and read-only vector search for Step 19."""

from __future__ import annotations

import json
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import Any

from aioa_memory_kernel.contracts.enums import MemoryTargetScope
from aioa_memory_kernel.persistence.protocols import TransactionProtocol
from aioa_memory_kernel.retrieval.models import MAXIMUM_CANDIDATE_CONTENT_BYTES, scope_values
from aioa_memory_kernel.retrieval.trusted_scope import (
    LINEAGE_FROM,
    LINEAGE_SELECT,
    trusted_scope_prefix,
)
from aioa_memory_kernel.sources import (
    SourceAccessClass,
    SourceAuthorityLevel,
    SourcePublicationState,
)

from .models import (
    EmbeddingBoundaryError,
    EmbeddingGenerationRequest,
    EmbeddingRecord,
    EmbeddingSource,
    EmbeddingVector,
    Step19ReasonCode,
    VectorRetrievalCandidate,
    VectorRetrievalRequest,
    vector_sql_literal,
)


_RECORD_COLUMNS = """
  tenant_id,
  hat_scope_id,
  source_id,
  knowledge_version_id,
  chunk_id,
  content_sha256,
  embedding_model_id,
  embedding_model_revision,
  embedding_model_digest,
  embedding_dimension,
  embedding_input_digest,
  embedding_bytes_sha256,
  cache_key,
  generation_backend,
  generation_backend_version,
  generation_backend_fingerprint,
  truncated,
  record_hash
"""


def _json_object(value: object) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError as exc:
            raise EmbeddingBoundaryError(Step19ReasonCode.SCHEMA_UNSUPPORTED) from exc
        if isinstance(decoded, Mapping):
            return dict(decoded)
    raise EmbeddingBoundaryError(Step19ReasonCode.SCHEMA_UNSUPPORTED)


def _boolean(value: object) -> bool:
    if value in (True, "true", "t", "TRUE", "1", 1):
        return True
    if value in (False, "false", "f", "FALSE", "0", 0):
        return False
    raise EmbeddingBoundaryError(Step19ReasonCode.SCHEMA_UNSUPPORTED)


def _canonical_distance(value: object) -> str:
    try:
        decimal = Decimal(str(value))
        if not decimal.is_finite() or decimal < 0:
            raise InvalidOperation
        return format(decimal.quantize(Decimal("0.00000001")), "f")
    except (InvalidOperation, ValueError) as exc:
        raise EmbeddingBoundaryError(Step19ReasonCode.SCHEMA_UNSUPPORTED) from exc


def source_from_row(
    row: Mapping[str, object],
    request: EmbeddingGenerationRequest,
) -> EmbeddingSource:
    return EmbeddingSource(
        tenant_id=str(row["tenant_id"]),
        hat_scope_id=str(row["hat_scope_id"]),
        source_id=str(row["source_id"]),
        knowledge_version_id=str(row["knowledge_version_id"]),
        chunk_id=str(row["chunk_id"]),
        chunk_ordinal=int(row["chunk_ordinal"]),
        content=str(row["content_text"]),
        content_sha256=str(row["content_sha256"]),
        version_ordinal=int(row["version_ordinal"]),
        source_scope_digest=str(row["scope_digest"]),
        source_registry_digest=str(row["registry_digest"]),
        source_artifact_digest=str(row["artifact_digest"]),
        effective_scope=request.effective_scope,
    )


def record_from_row(row: Mapping[str, object]) -> EmbeddingRecord:
    record = EmbeddingRecord(
        tenant_id=str(row["tenant_id"]),
        hat_scope_id=str(row["hat_scope_id"]),
        source_id=str(row["source_id"]),
        knowledge_version_id=str(row["knowledge_version_id"]),
        chunk_id=str(row["chunk_id"]),
        content_sha256=str(row["content_sha256"]),
        model_id=str(row["embedding_model_id"]),
        model_revision=str(row["embedding_model_revision"]),
        model_digest=str(row["embedding_model_digest"]),
        embedding_dimension=int(row["embedding_dimension"]),
        embedding_input_digest=str(row["embedding_input_digest"]),
        embedding_bytes_sha256=str(row["embedding_bytes_sha256"]),
        cache_key=str(row["cache_key"]),
        generation_backend=str(row["generation_backend"]),
        generation_backend_version=str(row["generation_backend_version"]),
        generation_backend_fingerprint=str(row["generation_backend_fingerprint"]),
        truncated=_boolean(row["truncated"]),
    )
    if row.get("record_hash") != record.record_hash:
        raise EmbeddingBoundaryError(Step19ReasonCode.EMBEDDING_RECORD_CONFLICT)
    return record


def vector_candidate_from_row(
    row: Mapping[str, object],
    request: VectorRetrievalRequest,
) -> VectorRetrievalCandidate:
    metadata = dict(_json_object(row["metadata"]))
    metadata.update(
        {
            "is_current": _boolean(row["is_current"]),
            "source_scope_dimensions": _json_object(row["scope_dimensions"]),
            "snapshot_content_sha256": str(row["snapshot_content_sha256"]),
            "version_ordinal": int(row["version_ordinal"]),
        }
    )
    return VectorRetrievalCandidate(
        tenant_id=str(row["tenant_id"]),
        hat_scope_id=str(row["hat_scope_id"]),
        source_id=str(row["source_id"]),
        knowledge_version_id=str(row["knowledge_version_id"]),
        chunk_id=str(row["chunk_id"]),
        chunk_ordinal=int(row["chunk_ordinal"]),
        content_sha256=str(row["content_sha256"]),
        content=str(row["content_text"]),
        language_tag=None if row.get("language_tag") is None else str(row["language_tag"]),
        authority_level=SourceAuthorityLevel(str(row["authority_level"])),
        authority_basis=_json_object(row["authority_basis"]),
        source_kind=str(row["source_kind"]),
        source_reference=str(row["source_reference"]),
        publication_state=SourcePublicationState(str(row["publication_state"])),
        access_class=SourceAccessClass(str(row["access_class"])),
        target_scope=MemoryTargetScope(str(row["target_scope"])),
        owner_user_id=None if row.get("owner_user_id") is None else str(row["owner_user_id"]),
        personal_memory_space_id=None if row.get("personal_memory_space_id") is None else str(row["personal_memory_space_id"]),
        scope_digest=str(row["scope_digest"]),
        registry_digest=str(row["registry_digest"]),
        artifact_digest=str(row["artifact_digest"]),
        snapshot_id=str(row["snapshot_id"]),
        structured_metadata=metadata,
        effective_scope=request.effective_scope,
        model_digest=str(row["embedding_model_digest"]),
        embedding_bytes_sha256=str(row["embedding_bytes_sha256"]),
        vector_distance=_canonical_distance(row["vector_distance"]),
    )


class CockroachEmbeddingRepository:
    """Own only chunk embeddings; all source/HAT records remain read-only."""

    def select_sources(
        self,
        transaction: TransactionProtocol,
        request: EmbeddingGenerationRequest,
    ) -> tuple[EmbeddingSource, ...]:
        prefix, parameters, chunk_scope = trusted_scope_prefix(request)
        sql = prefix + """
)
SELECT
  ts.tenant_id,
  ts.hat_scope_id,
  ts.source_id,
  kv.knowledge_version_id,
  kc.chunk_id,
  kc.chunk_ordinal,
  kc.content_text,
  kc.content_sha256,
  kv.version_ordinal,
  ts.scope_digest,
  ts.registry_digest,
  ts.artifact_digest
""" + LINEAGE_FROM + "\nWHERE TRUE\n"
        if chunk_scope:
            sql += f"  {chunk_scope}\n"
            parameters += (scope_values(request.effective_scope)["federal_state"],)
        sql += """  AND octet_length(kc.content_text) <= %s
ORDER BY ts.tenant_id, ts.hat_scope_id, ts.source_id,
         kv.version_ordinal, kc.chunk_ordinal, kc.chunk_id
LIMIT %s"""
        parameters += (
            MAXIMUM_CANDIDATE_CONTENT_BYTES,
            request.maximum_items + 1,
        )
        rows = transaction.fetch_all(sql, parameters)
        return tuple(source_from_row(row, request) for row in rows)

    def find_record(
        self,
        transaction: TransactionProtocol,
        source: EmbeddingSource,
        model_digest: str,
    ) -> EmbeddingRecord | None:
        row = transaction.fetch_one(
            "SELECT" + _RECORD_COLUMNS + """
FROM memory_patch.chunk_embeddings
WHERE tenant_id = %s
  AND chunk_id = %s
  AND knowledge_version_id = %s
  AND source_id = %s
  AND hat_scope_id = %s
  AND embedding_model_digest = %s""",
            (
                source.tenant_id,
                source.chunk_id,
                source.knowledge_version_id,
                source.source_id,
                source.hat_scope_id,
                model_digest,
            ),
        )
        return None if row is None else record_from_row(row)

    def insert_record(
        self,
        transaction: TransactionProtocol,
        record: EmbeddingRecord,
        vector: EmbeddingVector,
    ) -> None:
        transaction.execute(
            """INSERT INTO memory_patch.chunk_embeddings (
  tenant_id, chunk_id, knowledge_version_id, source_id, hat_scope_id,
  embedding_model_id, embedding_model_revision, embedding_model_digest,
  embedding_dimension, content_sha256, embedding_input_digest,
  embedding_bytes_sha256, cache_key, generation_backend,
  generation_backend_version, generation_backend_fingerprint,
  truncated, record_hash, embedding, created_at
) VALUES (
  %s, %s, %s, %s, %s,
  %s, %s, %s,
  %s, %s, %s,
  %s, %s, %s,
  %s, %s,
  %s, %s, %s::VECTOR(384), current_timestamp()
)""",
            (
                record.tenant_id,
                record.chunk_id,
                record.knowledge_version_id,
                record.source_id,
                record.hat_scope_id,
                record.model_id,
                record.model_revision,
                record.model_digest,
                record.embedding_dimension,
                record.content_sha256,
                record.embedding_input_digest,
                record.embedding_bytes_sha256,
                record.cache_key,
                record.generation_backend,
                record.generation_backend_version,
                record.generation_backend_fingerprint,
                record.truncated,
                record.record_hash,
                vector_sql_literal(vector),
            ),
        )

    def search_vectors(
        self,
        transaction: TransactionProtocol,
        request: VectorRetrievalRequest,
        query_vector: EmbeddingVector,
    ) -> tuple[VectorRetrievalCandidate, ...]:
        prefix, parameters, chunk_scope = trusted_scope_prefix(request)
        vector_literal = vector_sql_literal(query_vector)
        sql = (
            prefix
            + LINEAGE_SELECT
            + ",\n  ce.embedding_model_digest,\n"
            + "  ce.embedding_bytes_sha256,\n"
            + "  CAST(CAST(ce.embedding <-> %s::VECTOR(384) AS DECIMAL(20, 8)) AS STRING) AS vector_distance\n"
            + LINEAGE_FROM
            + """JOIN memory_patch.chunk_embeddings AS ce
  ON ce.tenant_id = kc.tenant_id
 AND ce.chunk_id = kc.chunk_id
 AND ce.knowledge_version_id = kc.knowledge_version_id
 AND ce.source_id = kc.source_id
 AND ce.hat_scope_id = kc.hat_scope_id
 AND ce.content_sha256 = kc.content_sha256
 AND ce.embedding_model_digest = %s
WHERE TRUE
"""
        )
        parameters += (vector_literal, request.model_digest)
        if chunk_scope:
            sql += f"  {chunk_scope}\n"
            parameters += (scope_values(request.effective_scope)["federal_state"],)
        sql += """  AND octet_length(kc.content_text) <= %s
ORDER BY ce.embedding <-> %s::VECTOR(384),
         kv.version_ordinal, kc.chunk_ordinal, kc.chunk_id
LIMIT %s"""
        parameters += (
            MAXIMUM_CANDIDATE_CONTENT_BYTES,
            vector_literal,
            request.maximum_results + 1,
        )
        rows = transaction.fetch_all(sql, parameters)
        return tuple(vector_candidate_from_row(row, request) for row in rows)


__all__ = [
    "CockroachEmbeddingRepository",
    "record_from_row",
    "source_from_row",
    "vector_candidate_from_row",
]
