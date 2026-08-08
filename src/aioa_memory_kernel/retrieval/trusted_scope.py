"""Shared hard-scope SQL boundary for lexical and vector retrieval.

The prefix constrains tenant, HAT, route scope, publication, authority, access,
and ownership before any lexical or vector candidate generator runs.  Index
prefixes are performance aids only; this CTE remains the authorization and
eligibility boundary.
"""

from __future__ import annotations

from typing import Protocol

from aioa_memory_kernel.contracts.enums import MemoryTargetScope
from aioa_memory_kernel.contracts.scope import ScopeDimension

from .models import RetrievalBoundaryError, Step18ReasonCode, scope_values


class TrustedScopeRequest(Protocol):
    tenant_id: str
    user_id: str
    selected_hat_id: str | None
    selected_hat_version: str | None
    selected_manifest_digest: str | None
    effective_scope: tuple[ScopeDimension, ...]
    hat_scope_id: str | None
    personal_memory_space_id: str | None


TRUSTED_SCOPE_CTE = """
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

LINEAGE_SELECT = """
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

LINEAGE_FROM = """
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


def _scope_filters(
    request: TrustedScopeRequest,
) -> tuple[str, tuple[object, ...]]:
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
        if (
            not isinstance(source_classes, tuple)
            or not source_classes
            or any(not isinstance(item, str) for item in source_classes)
        ):
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
    # knowledge_as_of remains bound but uninterpreted until Step 21.
    return "\n    ".join(clauses), tuple(parameters)


def trusted_scope_prefix(
    request: TrustedScopeRequest,
) -> tuple[str, tuple[object, ...], str]:
    """Return the canonical pre-candidate SQL prefix and closed parameters."""

    scope_sql, _ = _scope_filters(request)
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
    sql = TRUSTED_SCOPE_CTE.replace(
        "__ACCESS_SCOPE_PREDICATE__", access_predicate
    )
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
    scope = scope_values(request.effective_scope)
    extra: list[object] = []
    if scope.get("legal_jurisdiction") is not None:
        extra.append(scope["legal_jurisdiction"])
    if scope.get("source_language") is not None:
        extra.append(scope["source_language"])
    classes = scope.get("legal_source_class")
    if classes is not None:
        extra.extend(classes)
    return sql, parameters + tuple(extra), chunk_scope


__all__ = [
    "LINEAGE_FROM",
    "LINEAGE_SELECT",
    "TRUSTED_SCOPE_CTE",
    "TrustedScopeRequest",
    "trusted_scope_prefix",
]
