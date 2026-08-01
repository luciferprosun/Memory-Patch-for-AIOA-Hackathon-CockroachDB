"""Step 14 mapping from inventory evidence into the existing Step 9 registry.

This module creates typed registration inputs only.  It cannot publish,
approve, fetch, parse legal meaning, or infer a missing jurisdiction/time.
"""

from __future__ import annotations

from datetime import datetime

from aioa_memory_kernel.contracts.enums import MemoryTargetScope
from aioa_memory_kernel.contracts.serialization import canonical_sha256, ensure_utc
from aioa_memory_kernel.corpus.models import (
    RegistrationDisposition,
    SourceRegistrationCandidate,
)
from aioa_memory_kernel.sources.models import (
    PUBLICATION_GENESIS_DIGEST,
    OriginMetadata,
    ParserIdentity,
    ProvenanceArtifactIdentity,
    RedactionState,
    SourceAccessClass,
    SourceAuthorityAssessment,
    SourceAuthorityLevel,
    SourceLicenseAssessment,
    SourceLicenseStatus,
    SourcePublicationState,
    SourceRegistryRecord,
    SourceScopeDimensions,
    TransformationIdentity,
)

from .models import GermanLegalSourceClass, LegalJurisdiction


STEP14_TENANT_ID = "memory-patch-validation-tenant"
STEP14_HAT_SCOPE_ID = "german-law-global-1a"
STEP14_MAPPING_VERSION = "german-law-step9-source-registration-mapping-1a"


def build_source_registry_record(
    candidate: SourceRegistrationCandidate,
    *,
    created_at: datetime,
    tenant_id: str = STEP14_TENANT_ID,
) -> SourceRegistryRecord:
    """Build an exact Step 9 genesis record from one Step 14 candidate."""

    if not isinstance(candidate, SourceRegistrationCandidate):
        raise TypeError("candidate must be a SourceRegistrationCandidate")
    if candidate.disposition not in {
        RegistrationDisposition.READY_FOR_REGISTRATION,
        RegistrationDisposition.REVIEW_REQUIRED,
        RegistrationDisposition.QUARANTINED,
    }:
        raise ValueError("candidate disposition does not permit Step 9 registration")
    if candidate.source_class != GermanLegalSourceClass.DE_FEDERAL_OFFICIAL_CONSOLIDATED_LAW.value:
        raise ValueError("Step 14 candidate does not use the Step 13 source class")
    if candidate.jurisdiction != LegalJurisdiction.DE_FEDERAL.value:
        raise ValueError("Step 14 does not infer or repair jurisdiction")
    timestamp = ensure_utc(created_at, "created_at")
    scope = SourceScopeDimensions(
        tenant_id=tenant_id,
        hat_scope_id=candidate.hat_scope_id,
        target_scope=MemoryTargetScope.SHARED_KNOWLEDGE_HAT,
        domain="german-law",
        jurisdiction=candidate.jurisdiction,
        language=candidate.language,
        temporal_policy_reference="german-law-temporal-policy-1a",
        source_collection=("gii-official-consolidated", "step14-inventory"),
        additional_dimensions={
            "candidate_digest": candidate.candidate_digest,
            "inventory_run_id": candidate.inventory_run_id,
            "official_identifier": candidate.official_identifier,
            "provenance_aliases_digest": canonical_sha256(
                candidate.provenance_alias_digests
            ),
        },
    )
    parser = ParserIdentity(
        "step14-inventory-metadata-projection",
        "1.0.0",
        "1.0.0",
    )
    transformation = TransformationIdentity(
        "step14-source-registration-mapping",
        "1.0.0",
        "1.0.0",
    )
    origin = OriginMetadata(
        origin_kind="OWNED_CORPUS_INVENTORY",
        origin_system="German_Federal_Law_Knowledge_Library_1A",
        origin_version="1A",
        adapter_version=STEP14_MAPPING_VERSION,
        external_ref=f"inventory:{candidate.inventory_run_id}:{candidate.candidate_id}",
        observed_at=timestamp,
    )
    artifact = ProvenanceArtifactIdentity(
        artifact_kind="EXACT_OFFICIAL_SOURCE_SNAPSHOT",
        artifact_digest=candidate.content_sha256,
        byte_length=None,
        media_type="application/zip",
        origin=origin,
        parser=parser,
        transformation=transformation,
        created_at=timestamp,
        exact_source_bytes=True,
        model_generated=False,
    )
    return SourceRegistryRecord(
        tenant_id=tenant_id,
        source_id=candidate.logical_source_candidate_id,
        hat_scope_id=candidate.hat_scope_id,
        source_kind=candidate.source_class,
        source_reference=candidate.sanitized_source_reference,
        scope=scope,
        authority=SourceAuthorityAssessment(
            SourceAuthorityLevel(candidate.authority_level),
            {
                "policy_version": "german-law-source-authority-1a",
                "source_class": candidate.source_class,
                "official_identifier": candidate.official_identifier,
                "consolidated_text_is_authentic_promulgation": False,
            },
        ),
        license=SourceLicenseAssessment(
            SourceLicenseStatus(candidate.license_status),
            "DE-OFFICIAL-WORKS-AND-GII-DECLARATION",
            "library-license-ledger:14_LICENSE_AND_REUSE_LEDGER",
        ),
        access_class=SourceAccessClass(candidate.access_class),
        redaction_state=RedactionState(candidate.redaction_state),
        parser=parser,
        transformation=transformation,
        origin=origin,
        artifact=artifact,
        snapshot_id=None,
        knowledge_version_id=None,
        current_publication_state=SourcePublicationState.REGISTERED,
        current_publication_sequence=0,
        current_publication_event_digest=PUBLICATION_GENESIS_DIGEST,
        created_at=timestamp,
        updated_at=timestamp,
    )


def registration_operation_identity(
    candidate: SourceRegistrationCandidate,
) -> tuple[str, str]:
    """Return deterministic Step 6 operation and idempotency identities."""

    digest = canonical_sha256(
        {
            "policy": STEP14_MAPPING_VERSION,
            "candidate_id": candidate.candidate_id,
            "candidate_digest": candidate.candidate_digest,
        }
    )
    return f"step14-source-register-{digest}", f"step14:{digest}"


__all__ = [
    "STEP14_HAT_SCOPE_ID",
    "STEP14_MAPPING_VERSION",
    "STEP14_TENANT_ID",
    "build_source_registry_record",
    "registration_operation_identity",
]
