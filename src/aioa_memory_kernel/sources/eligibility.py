"""Deterministic publication-eligibility policy 1A."""

from __future__ import annotations

from datetime import datetime
from typing import Iterable

from aioa_memory_kernel.contracts.enums import MemoryTargetScope

from .models import (
    PUBLICATION_ELIGIBILITY_POLICY_VERSION,
    PublicationEligibilityDecision,
    RedactionState,
    SourceAccessClass,
    SourceAuthorityLevel,
    SourceLicenseStatus,
    SourceRegistryRecord,
)
from .provenance import ProvenanceGraph
from .errors import SourceRegistryValidationError


def _codes(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted(set(values)))


def evaluate_publication_eligibility(
    record: SourceRegistryRecord,
    graph: ProvenanceGraph,
    *,
    evaluated_at: datetime,
    quarantine_reasons: Iterable[str] = (),
    authority_conflicts: Iterable[str] = (),
    licensing_conflicts: Iterable[str] = (),
    publication_conflicts: Iterable[str] = (),
) -> PublicationEligibilityDecision:
    """Evaluate frozen registry and lineage facts without changing state."""

    record_scope = (record.tenant_id, record.source_id, record.hat_scope_id)
    if graph.scope is not None and graph.scope != record_scope:
        raise SourceRegistryValidationError(
            "provenance graph belongs to a different source scope",
            sanitized_code="PROVENANCE_SCOPE_MISMATCH",
        )
    reasons: list[str] = []
    if record.authority.authority_level is SourceAuthorityLevel.UNKNOWN:
        reasons.append("AUTHORITY_UNKNOWN")
    if (
        record.authority.authority_level is not SourceAuthorityLevel.UNKNOWN
        and not record.authority.authority_basis
    ):
        reasons.append("AUTHORITY_BASIS_MISSING")
    if record.license.license_status is SourceLicenseStatus.UNKNOWN:
        reasons.append("LICENSE_UNKNOWN")
    if record.license.license_status is SourceLicenseStatus.PROHIBITED:
        reasons.append("LICENSE_PROHIBITED")
    if record.access_class is SourceAccessClass.USER_PRIVATE:
        if (
            record.scope.target_scope
            is not MemoryTargetScope.USER_PERSONAL_HAT
            or record.scope.owner_user_id is None
        ):
            reasons.append("PRIVATE_SCOPE_MISMATCH")
    elif record.scope.target_scope is not MemoryTargetScope.SHARED_KNOWLEDGE_HAT:
        reasons.append("SHARED_SCOPE_MISMATCH")
    if record.redaction_state is RedactionState.PENDING:
        reasons.append("REDACTION_PENDING")
    if record.redaction_state is RedactionState.REJECTED:
        reasons.append("REDACTION_REJECTED")
    if record.artifact.model_generated and record.artifact.exact_source_bytes:
        reasons.append("MODEL_OUTPUT_NOT_EXACT_SOURCE")
    if record.artifact.byte_length is None:
        reasons.append("EXACT_BYTE_LENGTH_MISSING")
    if not record.artifact.exact_source_bytes:
        reasons.append("EXACT_SOURCE_BYTES_UNVERIFIED")
    parents = graph.parent_digests(record.artifact.artifact_digest)
    if (
        record.authority.authority_level is SourceAuthorityLevel.DERIVED
        and not parents
    ):
        reasons.append("DERIVED_PARENT_LINEAGE_MISSING")
    roots = graph.root_digests(record.artifact.artifact_digest)
    if not roots:
        reasons.append("LINEAGE_ROOT_MISSING")
        roots = (record.artifact.artifact_digest,)
    if record.snapshot_id is None:
        reasons.append("SOURCE_SNAPSHOT_IDENTITY_MISSING")
    if record.knowledge_version_id is None:
        reasons.append("KNOWLEDGE_VERSION_IDENTITY_MISSING")
    reasons.extend(f"QUARANTINE:{value}" for value in quarantine_reasons)
    reasons.extend(f"AUTHORITY_CONFLICT:{value}" for value in authority_conflicts)
    reasons.extend(f"LICENSE_CONFLICT:{value}" for value in licensing_conflicts)
    reasons.extend(
        f"PUBLICATION_CONFLICT:{value}" for value in publication_conflicts
    )
    ordered = _codes(reasons)
    return PublicationEligibilityDecision(
        policy_version=PUBLICATION_ELIGIBILITY_POLICY_VERSION,
        eligible=not ordered,
        reason_codes=ordered,
        registry_digest=record.registry_digest,
        scope_digest=record.scope.scope_digest,
        lineage_root_digests=roots,
        lineage_digest=graph.lineage_digest(record.artifact.artifact_digest),
        lineage_terminal_digest=record.artifact.artifact_digest,
        snapshot_id=record.snapshot_id,
        knowledge_version_id=record.knowledge_version_id,
        evaluated_at=evaluated_at,
    )
