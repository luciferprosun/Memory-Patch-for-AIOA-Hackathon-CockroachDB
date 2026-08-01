"""Fixed, non-fetching metadata adapters over bounded parsed metadata."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Mapping

from aioa_memory_kernel.contracts.serialization import canonical_sha256

from .errors import GermanLawPolicyError
from .models import (
    AuthenticityStatus,
    ConsolidationStatus,
    GERMAN_LAW_ADAPTER_POLICY_VERSION,
    GermanLawSourceMetadata,
    GermanLawTemporalFacts,
    GermanLegalSourceClass,
    LegalJurisdiction,
    VerificationStatus,
)


def _timestamp(value: object | None) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise GermanLawPolicyError("INVALID_TEMPORAL_METADATA", "invalid metadata timestamp") from exc
    raise GermanLawPolicyError("INVALID_TEMPORAL_METADATA", "timestamp must be ISO-8601 text")


def source_metadata_from_mapping(data: Mapping[str, object], expected_class: GermanLegalSourceClass | None = None) -> GermanLawSourceMetadata:
    """Construct typed metadata without fetching, parsing a PDF, or verifying it."""
    if not isinstance(data, Mapping) or len(data) > 48:
        raise GermanLawPolicyError("INVALID_METADATA", "metadata must be a bounded object")
    try:
        source_class = GermanLegalSourceClass(str(data["source_class"]))
        if expected_class is not None and source_class is not expected_class:
            raise GermanLawPolicyError("UNSUPPORTED_ADAPTER", "adapter/source class mismatch")
        temporal_data = data.get("temporal", {})
        if not isinstance(temporal_data, Mapping) or len(temporal_data) > 12:
            raise GermanLawPolicyError("INVALID_TEMPORAL_METADATA", "temporal metadata must be bounded")
        temporal = GermanLawTemporalFacts(**{name: _timestamp(temporal_data.get(name)) for name in GermanLawTemporalFacts.__dataclass_fields__})
        return GermanLawSourceMetadata(
            source_id=str(data["source_id"]),
            source_registry_reference=str(data["source_registry_reference"]),
            source_class=source_class,
            official_publisher=None if data.get("official_publisher") is None else str(data["official_publisher"]),
            canonical_official_identifier=None if data.get("canonical_official_identifier") is None else str(data["canonical_official_identifier"]),
            jurisdiction=LegalJurisdiction(str(data["jurisdiction"])),
            language=str(data.get("language", "de")),
            authenticity_status=AuthenticityStatus(str(data.get("authenticity_status", "UNKNOWN"))),
            consolidation_status=ConsolidationStatus(str(data.get("consolidation_status", "UNKNOWN"))),
            verification_status=VerificationStatus(str(data.get("verification_status", "UNVERIFIED"))),
            retrieval_reference=str(data["retrieval_reference"]),
            temporal=temporal,
            court_identity=None if data.get("court_identity") is None else str(data["court_identity"]),
            court_level=None if data.get("court_level") is None else str(data["court_level"]),
        )
    except GermanLawPolicyError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise GermanLawPolicyError("INVALID_METADATA", "metadata contract construction failed") from exc


@dataclass(frozen=True, slots=True)
class GermanLawMetadataCandidate:
    metadata: GermanLawSourceMetadata
    adapter_name: str
    adapter_version: str
    findings: tuple[str, ...]
    candidate_digest: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "findings", tuple(sorted(set(self.findings))))
        object.__setattr__(self, "candidate_digest", canonical_sha256(self, exclude_fields=("candidate_digest",)))


class FixedMetadataAdapter:
    """Pure adapter with a fixed source class; it performs no I/O or authority decision."""

    adapter_name = "fixed-german-law-metadata-adapter"
    source_class: GermanLegalSourceClass

    def adapt(self, data: Mapping[str, object]) -> GermanLawMetadataCandidate:
        metadata = source_metadata_from_mapping(data, self.source_class)
        findings = () if metadata.canonical_official_identifier else ("OFFICIAL_IDENTIFIER_MISSING",)
        return GermanLawMetadataCandidate(metadata, self.adapter_name, GERMAN_LAW_ADAPTER_POLICY_VERSION, findings)


class FederalGazetteMetadataAdapter(FixedMetadataAdapter):
    adapter_name = "federal-gazette-metadata-1a"
    source_class = GermanLegalSourceClass.DE_FEDERAL_AUTHENTIC_PROMULGATION


class ConsolidatedFederalLawMetadataAdapter(FixedMetadataAdapter):
    adapter_name = "consolidated-federal-law-metadata-1a"
    source_class = GermanLegalSourceClass.DE_FEDERAL_OFFICIAL_CONSOLIDATED_LAW


class OfficialCourtDecisionMetadataAdapter(FixedMetadataAdapter):
    adapter_name = "official-court-decision-metadata-1a"
    source_class = GermanLegalSourceClass.DE_FEDERAL_OFFICIAL_COURT_DECISION


class EuLegalActMetadataAdapter(FixedMetadataAdapter):
    adapter_name = "eu-legal-act-metadata-1a"
    source_class = GermanLegalSourceClass.EU_AUTHENTIC_OFFICIAL_JOURNAL


class LegislativeMaterialMetadataAdapter(FixedMetadataAdapter):
    adapter_name = "legislative-material-metadata-1a"
    source_class = GermanLegalSourceClass.OFFICIAL_LEGISLATIVE_MATERIAL


class GermanLawMetadataAdapterRegistry:
    def __init__(self) -> None:
        adapters = (
            FederalGazetteMetadataAdapter(),
            ConsolidatedFederalLawMetadataAdapter(),
            OfficialCourtDecisionMetadataAdapter(),
            EuLegalActMetadataAdapter(),
            LegislativeMaterialMetadataAdapter(),
        )
        self._adapters = {adapter.source_class: adapter for adapter in adapters}

    def adapt(self, source_class: GermanLegalSourceClass, data: Mapping[str, object]) -> GermanLawMetadataCandidate:
        try:
            adapter = self._adapters[source_class]
        except KeyError as exc:
            raise GermanLawPolicyError("UNSUPPORTED_ADAPTER", "no fixed adapter for source class") from exc
        return adapter.adapt(data)
