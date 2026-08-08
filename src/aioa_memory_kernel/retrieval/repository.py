"""Read-only CockroachDB repository for the Step 18 lexical baseline."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from aioa_memory_kernel.contracts.enums import MemoryTargetScope
from aioa_memory_kernel.persistence.protocols import TransactionProtocol
from aioa_memory_kernel.sources import (
    SourceAccessClass,
    SourceAuthorityLevel,
    SourcePublicationState,
)

from .models import (
    ExactIdentifierField,
    ExactIdentifierSelector,
    FullTextQuery,
    KeywordQuery,
    MAXIMUM_CANDIDATE_CONTENT_BYTES,
    RetrievalBoundaryError,
    RetrievalCandidate,
    RetrievalMode,
    RetrievalRequest,
    StatuteSectionSelector,
    Step18ReasonCode,
    scope_values,
)
from .trusted_scope import LINEAGE_FROM, LINEAGE_SELECT, trusted_scope_prefix

_EXACT_COLUMNS = {
    ExactIdentifierField.SOURCE_ID: "ts.source_id",
    ExactIdentifierField.KNOWLEDGE_VERSION_ID: "kv.knowledge_version_id",
    ExactIdentifierField.CHUNK_ID: "kc.chunk_id",
    ExactIdentifierField.OFFICIAL_IDENTIFIER: "kc.metadata->>'official_identifier'",
    ExactIdentifierField.DOCUMENT_IDENTITY: "kc.metadata->>'document_identity'",
    ExactIdentifierField.VERSION_IDENTITY: "kc.metadata->>'version_identity'",
}


def _json_object(value: object, field_name: str) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError as exc:
            raise RetrievalBoundaryError(Step18ReasonCode.RETRIEVAL_SCHEMA_UNSUPPORTED) from exc
        if isinstance(decoded, Mapping):
            return dict(decoded)
    raise RetrievalBoundaryError(Step18ReasonCode.RETRIEVAL_SCHEMA_UNSUPPORTED)


def _boolean(value: object) -> bool:
    if value in (True, "true", "t", "TRUE", "1", 1):
        return True
    if value in (False, "false", "f", "FALSE", "0", 0):
        return False
    raise RetrievalBoundaryError(Step18ReasonCode.RETRIEVAL_SCHEMA_UNSUPPORTED)


def _metadata(row: Mapping[str, object]) -> Mapping[str, Any]:
    metadata = dict(_json_object(row["metadata"], "metadata"))
    metadata.update(
        {
            "is_current": _boolean(row["is_current"]),
            "source_scope_dimensions": _json_object(row["scope_dimensions"], "scope_dimensions"),
            "snapshot_content_sha256": str(row["snapshot_content_sha256"]),
            "version_ordinal": int(row["version_ordinal"]),
        }
    )
    return metadata


def candidate_from_row(
    row: Mapping[str, object],
    request: RetrievalRequest,
) -> RetrievalCandidate:
    score = row.get("retrieval_score")
    return RetrievalCandidate(
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
        authority_basis=_json_object(row["authority_basis"], "authority_basis"),
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
        structured_metadata=_metadata(row),
        effective_scope=request.effective_scope,
        retrieval_mode=request.retrieval_mode,
        retrieval_score=None if score is None else str(score),
    )


class CockroachRetrievalRepository:
    """Issue only bounded, parameterized reads over the canonical schema."""

    def search(
        self,
        transaction: TransactionProtocol,
        request: RetrievalRequest,
    ) -> tuple[RetrievalCandidate, ...]:
        prefix, base_parameters, chunk_scope = trusted_scope_prefix(request)
        selector = request.selector
        selected = LINEAGE_SELECT + "\n" + LINEAGE_FROM
        parameters: tuple[object, ...]
        if isinstance(selector, ExactIdentifierSelector):
            column = _EXACT_COLUMNS[selector.field]
            placeholders = ", ".join("%s" for _ in selector.values)
            sql = prefix + selected + f"\nWHERE {column} IN ({placeholders})\n"
            if chunk_scope:
                sql += f"  {chunk_scope}\n"
            sql += "  AND octet_length(kc.content_text) <= %s\nORDER BY kv.version_ordinal, kc.chunk_ordinal, kc.chunk_id\nLIMIT %s"
            parameters = (*base_parameters, *selector.values)
            if chunk_scope:
                parameters += (scope_values(request.effective_scope)["federal_state"],)
        elif isinstance(selector, StatuteSectionSelector):
            sql = prefix + selected + "\nWHERE kc.metadata->>'official_identifier' = %s\n  AND kc.metadata->>'provision_identifier' = %s\n"
            if chunk_scope:
                sql += f"  {chunk_scope}\n"
            sql += "  AND octet_length(kc.content_text) <= %s\nORDER BY kv.version_ordinal, kc.chunk_ordinal, kc.chunk_id\nLIMIT %s"
            parameters = (*base_parameters, selector.statute_identifier, selector.section_identifier)
            if chunk_scope:
                parameters += (scope_values(request.effective_scope)["federal_state"],)
        elif isinstance(selector, (FullTextQuery, KeywordQuery)):
            query = selector.query_text if isinstance(selector, FullTextQuery) else " ".join(selector.keywords)
            sql = prefix + LINEAGE_SELECT + ",\n  CAST(CAST(ts_rank(csd.search_vector, plainto_tsquery('german', %s)) AS DECIMAL(20, 8)) AS STRING) AS retrieval_score\n" + LINEAGE_FROM + "JOIN memory_patch.chunk_search_documents AS csd\n  ON csd.tenant_id = kc.tenant_id\n AND csd.chunk_id = kc.chunk_id\n AND csd.knowledge_version_id = kc.knowledge_version_id\n AND csd.source_id = kc.source_id\n AND csd.hat_scope_id = kc.hat_scope_id\n AND csd.search_config = 'german'\nWHERE csd.search_vector @@ plainto_tsquery('german', %s)\n"
            if chunk_scope:
                sql += f"  {chunk_scope}\n"
            sql += "  AND octet_length(kc.content_text) <= %s\nORDER BY ts_rank(csd.search_vector, plainto_tsquery('german', %s)) DESC, kv.version_ordinal, kc.chunk_ordinal, kc.chunk_id\nLIMIT %s"
            parameters = (*base_parameters, query, query)
            if chunk_scope:
                parameters += (scope_values(request.effective_scope)["federal_state"],)
            parameters += (MAXIMUM_CANDIDATE_CONTENT_BYTES, query, request.maximum_results + 1)
            rows = transaction.fetch_all(sql, parameters)
            return tuple(candidate_from_row(row, request) for row in rows)
        else:
            raise RetrievalBoundaryError(Step18ReasonCode.RETRIEVAL_SCHEMA_UNSUPPORTED)
        parameters += (MAXIMUM_CANDIDATE_CONTENT_BYTES, request.maximum_results + 1)
        rows = transaction.fetch_all(sql, parameters)
        return tuple(candidate_from_row(row, request) for row in rows)


__all__ = ["CockroachRetrievalRepository", "candidate_from_row"]
