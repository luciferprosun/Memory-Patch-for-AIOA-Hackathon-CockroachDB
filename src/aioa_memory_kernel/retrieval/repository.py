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


_TRUSTED_SCOPE = """
WITH trusted_scope AS (
  SELECT
    sre.tenant_id,
    sre.source_id,
    sre.hat_scope_id,
    sre.source_kind,
    sre.source_reference,
    sre.target_scope,
    sre.owner_user_id,
    sre.personal_memory_space_id,
    sre.authority_level,
    sre.authority_basis,
    sre.access_class,
    sre.scope_dimensions,
    sre.scope_digest,
    sre.registry_digest,
    sre.artifact_digest
  FROM memory_patch.source_registry_entries AS sre
  JOIN memory_patch.hat_scopes AS hs
    ON hs.tenant_id = sre.tenant_id
   AND hs.hat_scope_id = sre.hat_scope_id
  JOIN memory_patch.hat_manifests AS hm
    ON hm.hat_id = hs.knowledge_hat_id
   AND hm.hat_version = hs.knowledge_hat_version
  JOIN memory_patch.knowledge_sources AS ks
    ON ks.tenant_id = sre.tenant_id
   AND ks.source_id = sre.source_id
   AND ks.hat_scope_id = sre.hat_scope_id
   AND ks.source_kind = sre.source_kind
   AND ks.source_reference = sre.source_reference
  WHERE sre.tenant_id = %s
    AND sre.hat_scope_id = %s
    AND hs.knowledge_hat_id = %s
    AND hs.knowledge_hat_version = %s
    AND hm.manifest_hash = %s
    AND hs.target_scope = %s
    AND sre.target_scope = hs.target_scope
    AND sre.current_publication_state = 'PUBLISHED'
    AND sre.authority_level IN ('OFFICIAL_PRIMARY', 'AUTHORITATIVE_SECONDARY')
    AND sre.redaction_state IN ('NOT_REQUIRED', 'VERIFIED')
    AND sre.model_generated = false
    __ACCESS_SCOPE_PREDICATE__
"""

_LINEAGE = """
)
SELECT
  ts.tenant_id,
  ts.hat_scope_id,
  ts.source_id,
  kv.knowledge_version_id,
  kc.chunk_id,
  kc.chunk_ordinal,
  kc.content_sha256,
  kc.content_text,
  kc.language_tag,
  ts.authority_level,
  ts.authority_basis,
  ts.source_kind,
  ts.source_reference,
  'PUBLISHED' AS publication_state,
  ts.access_class,
  ts.target_scope,
  ts.owner_user_id,
  ts.personal_memory_space_id,
  ts.scope_digest,
  ts.registry_digest,
  ts.artifact_digest,
  ss.snapshot_id,
  kc.metadata,
  kv.version_ordinal,
  kv.is_current,
  ss.content_sha256 AS snapshot_content_sha256,
  ts.scope_dimensions
"""

_FROM_LINEAGE = """
FROM trusted_scope AS ts
JOIN memory_patch.knowledge_versions AS kv
  ON kv.tenant_id = ts.tenant_id
 AND kv.source_id = ts.source_id
 AND kv.hat_scope_id = ts.hat_scope_id
JOIN memory_patch.source_snapshots AS ss
  ON ss.tenant_id = kv.tenant_id
 AND ss.snapshot_id = kv.snapshot_id
 AND ss.source_id = kv.source_id
 AND ss.hat_scope_id = kv.hat_scope_id
JOIN memory_patch.knowledge_chunks AS kc
  ON kc.tenant_id = kv.tenant_id
 AND kc.knowledge_version_id = kv.knowledge_version_id
 AND kc.source_id = kv.source_id
 AND kc.hat_scope_id = kv.hat_scope_id
"""

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


def _scope_filters(request: RetrievalRequest) -> tuple[str, tuple[object, ...]]:
    """Map only closed route dimensions to pre-candidate SQL predicates."""

    values = scope_values(request.effective_scope)
    clauses: list[str] = []
    parameters: list[object] = []
    allowed = {
        "legal_jurisdiction",
        "source_language",
        "legal_source_class",
        "federal_state",
        "knowledge_as_of",
        "personal_memory_space_id",
        "target_scope",
    }
    unsupported = tuple(
        item.name for item in request.effective_scope if item.name not in allowed
    )
    if unsupported:
        raise RetrievalBoundaryError(Step18ReasonCode.ROUTE_SCOPE_MISMATCH)
    jurisdiction = values.get("legal_jurisdiction")
    if jurisdiction is not None:
        if not isinstance(jurisdiction, str):
            raise RetrievalBoundaryError(Step18ReasonCode.ROUTE_SCOPE_MISMATCH)
        clauses.append("AND sre.scope_dimensions->>'jurisdiction' = %s")
        parameters.append(jurisdiction)
    language = values.get("source_language")
    if language is not None:
        if not isinstance(language, str):
            raise RetrievalBoundaryError(Step18ReasonCode.ROUTE_SCOPE_MISMATCH)
        clauses.append("AND sre.scope_dimensions->>'language' = %s")
        parameters.append(language)
    source_classes = values.get("legal_source_class")
    if source_classes is not None:
        if not isinstance(source_classes, tuple) or not source_classes or any(not isinstance(item, str) for item in source_classes):
            raise RetrievalBoundaryError(Step18ReasonCode.ROUTE_SCOPE_MISMATCH)
        placeholders = ", ".join("%s" for _ in source_classes)
        clauses.append(f"AND sre.source_kind IN ({placeholders})")
        parameters.extend(source_classes)
    federal_state = values.get("federal_state")
    if federal_state is not None:
        if not isinstance(federal_state, str):
            raise RetrievalBoundaryError(Step18ReasonCode.ROUTE_SCOPE_MISMATCH)
        clauses.append("AND kc.metadata->>'federal_state' = %s")
        parameters.append(federal_state)
    # knowledge_as_of is carried unchanged for Step 21.  Step 18 deliberately
    # returns every matching version instead of choosing legal applicability.
    return "\n    ".join(clauses), tuple(parameters)


def _trusted_prefix(request: RetrievalRequest) -> tuple[str, tuple[object, ...], str]:
    scope_sql, _ = _scope_filters(request)
    # The federal-state predicate references the chunk and therefore belongs
    # after lineage joins; all source-level predicates remain in trusted_scope.
    chunk_scope = ""
    source_scope_lines: list[str] = []
    for line in scope_sql.splitlines():
        if "kc.metadata" in line:
            chunk_scope = line.strip()
        elif line.strip():
            source_scope_lines.append(line)
    source_scope = "\n    ".join(source_scope_lines)
    if request.personal_memory_space_id is None:
        target_scope = MemoryTargetScope.SHARED_KNOWLEDGE_HAT
        access_predicate = """AND sre.access_class IN ('PUBLIC', 'TENANT_RESTRICTED')
    AND sre.owner_user_id IS NULL
    AND sre.personal_memory_space_id IS NULL
    AND hs.owner_user_id IS NULL
    AND hs.personal_memory_space_id IS NULL"""
        access_parameters: tuple[object, ...] = ()
    else:
        target_scope = MemoryTargetScope.USER_PERSONAL_HAT
        access_predicate = """AND sre.access_class = 'USER_PRIVATE'
    AND sre.owner_user_id = %s
    AND sre.personal_memory_space_id = %s
    AND hs.owner_user_id = %s
    AND hs.personal_memory_space_id = %s"""
        access_parameters = (
            request.user_id,
            request.personal_memory_space_id,
            request.user_id,
            request.personal_memory_space_id,
        )
    sql = _TRUSTED_SCOPE.replace("__ACCESS_SCOPE_PREDICATE__", access_predicate)
    if source_scope:
        sql += "    " + source_scope + "\n"
    parameters: tuple[object, ...] = (
        request.tenant_id,
        request.hat_scope_id,
        request.selected_hat_id,
        request.selected_hat_version,
        request.selected_manifest_digest,
        target_scope.value,
        *access_parameters,
    )
    # Rebuild source-level values in the same closed order as the SQL clauses.
    scope = scope_values(request.effective_scope)
    extra: list[object] = []
    if scope.get("legal_jurisdiction") is not None:
        extra.append(scope["legal_jurisdiction"])
    if scope.get("source_language") is not None:
        extra.append(scope["source_language"])
    classes = scope.get("legal_source_class")
    if classes is not None:
        extra.extend(classes)
    parameters += tuple(extra)
    return sql, parameters, chunk_scope


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
        prefix, base_parameters, chunk_scope = _trusted_prefix(request)
        selector = request.selector
        selected = _LINEAGE + "\n" + _FROM_LINEAGE
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
            sql = prefix + _LINEAGE + ",\n  CAST(CAST(ts_rank(csd.search_vector, plainto_tsquery('german', %s)) AS DECIMAL(20, 8)) AS STRING) AS retrieval_score\n" + _FROM_LINEAGE + "JOIN memory_patch.chunk_search_documents AS csd\n  ON csd.tenant_id = kc.tenant_id\n AND csd.chunk_id = kc.chunk_id\n AND csd.knowledge_version_id = kc.knowledge_version_id\n AND csd.source_id = kc.source_id\n AND csd.hat_scope_id = kc.hat_scope_id\n AND csd.search_config = 'german'\nWHERE csd.search_vector @@ plainto_tsquery('german', %s)\n"
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
