"""Immutable German-law request, source-authority, and temporal contracts."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from aioa_memory_kernel.contracts.serialization import canonical_sha256, ensure_utc
from aioa_memory_kernel.sources.models import SourceAuthorityLevel

from .errors import GermanLawPolicyError

GERMAN_LAW_POLICY_VERSION = "german-law-source-authority-1a"
GERMAN_LAW_TEMPORAL_POLICY_VERSION = "german-law-temporal-policy-1a"
GERMAN_LAW_ADAPTER_POLICY_VERSION = "german-law-metadata-adapters-1a"


class LegalJurisdiction(str, Enum):
    DE_FEDERAL = "DE_FEDERAL"
    DE_STATE = "DE_STATE"
    EU = "EU"


class GermanLegalSourceClass(str, Enum):
    DE_FEDERAL_AUTHENTIC_PROMULGATION = "DE_FEDERAL_AUTHENTIC_PROMULGATION"
    DE_FEDERAL_OFFICIAL_CONSOLIDATED_LAW = "DE_FEDERAL_OFFICIAL_CONSOLIDATED_LAW"
    DE_FEDERAL_OFFICIAL_COURT_DECISION = "DE_FEDERAL_OFFICIAL_COURT_DECISION"
    DE_STATE_AUTHENTIC_PROMULGATION = "DE_STATE_AUTHENTIC_PROMULGATION"
    DE_STATE_OFFICIAL_CONSOLIDATED_LAW = "DE_STATE_OFFICIAL_CONSOLIDATED_LAW"
    DE_STATE_OFFICIAL_COURT_DECISION = "DE_STATE_OFFICIAL_COURT_DECISION"
    EU_AUTHENTIC_OFFICIAL_JOURNAL = "EU_AUTHENTIC_OFFICIAL_JOURNAL"
    EU_OFFICIAL_CONSOLIDATED_ACT = "EU_OFFICIAL_CONSOLIDATED_ACT"
    OFFICIAL_LEGISLATIVE_MATERIAL = "OFFICIAL_LEGISLATIVE_MATERIAL"
    OFFICIAL_ADMINISTRATIVE_GUIDANCE = "OFFICIAL_ADMINISTRATIVE_GUIDANCE"
    OFFICIAL_RESEARCH_OR_EXPLANATORY_MATERIAL = "OFFICIAL_RESEARCH_OR_EXPLANATORY_MATERIAL"
    REPUTABLE_LEGAL_SECONDARY = "REPUTABLE_LEGAL_SECONDARY"
    PRIVATE_LEGAL_DATABASE = "PRIVATE_LEGAL_DATABASE"
    USER_SUPPLIED_LEGAL_DOCUMENT = "USER_SUPPLIED_LEGAL_DOCUMENT"
    DERIVED_SUMMARY = "DERIVED_SUMMARY"
    UNKNOWN_LEGAL_SOURCE = "UNKNOWN_LEGAL_SOURCE"


class AuthenticityStatus(str, Enum):
    AUTHENTIC = "AUTHENTIC"
    OFFICIAL_NON_AUTHENTIC = "OFFICIAL_NON_AUTHENTIC"
    NOT_AUTHENTIC = "NOT_AUTHENTIC"
    UNKNOWN = "UNKNOWN"


class ConsolidationStatus(str, Enum):
    NOT_CONSOLIDATED = "NOT_CONSOLIDATED"
    OFFICIAL_CONSOLIDATED = "OFFICIAL_CONSOLIDATED"
    PRIVATE_CONSOLIDATED = "PRIVATE_CONSOLIDATED"
    UNKNOWN = "UNKNOWN"


class VerificationStatus(str, Enum):
    UNVERIFIED = "UNVERIFIED"
    OFFICIAL_REFERENCE_VERIFIED = "OFFICIAL_REFERENCE_VERIFIED"
    AUTHENTICITY_VERIFIED = "AUTHENTICITY_VERIFIED"
    CONFLICTING = "CONFLICTING"
    INSUFFICIENT = "INSUFFICIENT"


class TemporalDecision(str, Enum):
    APPLICABLE = "APPLICABLE"
    NOT_YET_APPLICABLE = "NOT_YET_APPLICABLE"
    EXPIRED = "EXPIRED"
    UNKNOWN = "UNKNOWN"
    CONFLICTING = "CONFLICTING"


def _text(value: object, field_name: str, maximum: int = 1024) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip() or len(value) > maximum:
        raise GermanLawPolicyError("INVALID_VALUE", f"{field_name} must be bounded canonical text")
    lowered = value.casefold()
    if any(token in lowered for token in ("password=", "aws_secret", "authorization: bearer")):
        raise GermanLawPolicyError("HIDDEN_AUTHORITY_OR_SECRET", f"{field_name} contains forbidden material")
    return value


def _optional_text(value: object | None, field_name: str, maximum: int = 512) -> str | None:
    return None if value is None else _text(value, field_name, maximum)


@dataclass(frozen=True, slots=True)
class GermanLawRequest:
    request_id: str
    query_text: str
    request_language: str
    legal_jurisdiction: LegalJurisdiction
    knowledge_as_of: datetime | None
    federal_state: str | None = None
    legal_domain: str | None = None
    official_identifier_hint: str | None = None
    court_or_proceeding_hint: str | None = None
    request_digest: str = field(init=False)

    def __post_init__(self) -> None:
        for name in ("request_id", "query_text"):
            object.__setattr__(self, name, _text(getattr(self, name), name, 4096))
        if self.request_language != "de":
            raise GermanLawPolicyError("UNSUPPORTED_LANGUAGE", "Step 13 supports German requests only")
        if not isinstance(self.legal_jurisdiction, LegalJurisdiction):
            raise GermanLawPolicyError("UNSUPPORTED_JURISDICTION", "legal jurisdiction must be explicit")
        if self.knowledge_as_of is not None:
            object.__setattr__(self, "knowledge_as_of", ensure_utc(self.knowledge_as_of, "knowledge_as_of"))
        if self.legal_jurisdiction is LegalJurisdiction.DE_STATE and self.federal_state is None:
            raise GermanLawPolicyError("MISSING_FEDERAL_STATE", "state-law requests require an explicit state")
        if self.legal_jurisdiction is not LegalJurisdiction.DE_STATE and self.federal_state is not None:
            raise GermanLawPolicyError("JURISDICTION_SCOPE_MISMATCH", "federal_state is valid only for DE_STATE")
        for name in ("federal_state", "legal_domain", "official_identifier_hint", "court_or_proceeding_hint"):
            object.__setattr__(self, name, _optional_text(getattr(self, name), name))
        object.__setattr__(self, "request_digest", canonical_sha256(self, exclude_fields=("request_digest",)))


@dataclass(frozen=True, slots=True)
class GermanLawTemporalFacts:
    published_at: datetime | None = None
    promulgated_at: datetime | None = None
    adopted_at: datetime | None = None
    effective_from: datetime | None = None
    effective_to: datetime | None = None
    applicable_from: datetime | None = None
    applicable_to: datetime | None = None
    decision_date: datetime | None = None
    retrieved_at: datetime | None = None
    ingested_at: datetime | None = None
    verified_at: datetime | None = None
    superseded_at: datetime | None = None

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, ensure_utc(value, name))


@dataclass(frozen=True, slots=True)
class GermanLawSourceMetadata:
    source_id: str
    source_registry_reference: str
    source_class: GermanLegalSourceClass
    official_publisher: str | None
    canonical_official_identifier: str | None
    jurisdiction: LegalJurisdiction
    language: str
    authenticity_status: AuthenticityStatus
    consolidation_status: ConsolidationStatus
    verification_status: VerificationStatus
    retrieval_reference: str
    temporal: GermanLawTemporalFacts = field(default_factory=GermanLawTemporalFacts)
    court_identity: str | None = None
    court_level: str | None = None
    metadata_digest: str = field(init=False)

    def __post_init__(self) -> None:
        for name in ("source_id", "source_registry_reference", "retrieval_reference"):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        if not isinstance(self.source_class, GermanLegalSourceClass) or not isinstance(self.jurisdiction, LegalJurisdiction):
            raise GermanLawPolicyError("INVALID_SOURCE_CLASS", "typed source class and jurisdiction are required")
        if self.language != "de" and self.language != "en":
            raise GermanLawPolicyError("UNSUPPORTED_LANGUAGE", "unsupported source language")
        for name in ("official_publisher", "canonical_official_identifier", "court_identity", "court_level"):
            object.__setattr__(self, name, _optional_text(getattr(self, name), name))
        if not isinstance(self.temporal, GermanLawTemporalFacts):
            raise GermanLawPolicyError("INVALID_TEMPORAL_METADATA", "temporal facts must be typed")
        object.__setattr__(self, "metadata_digest", canonical_sha256(self, exclude_fields=("metadata_digest",)))


@dataclass(frozen=True, slots=True)
class GermanLawTemporalAssessment:
    decision: TemporalDecision
    policy_version: str
    reason_codes: tuple[str, ...]
    assessment_digest: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "reason_codes", tuple(sorted(set(self.reason_codes))))
        object.__setattr__(self, "assessment_digest", canonical_sha256(self, exclude_fields=("assessment_digest",)))


@dataclass(frozen=True, slots=True)
class GermanLawSourceAuthorityAssessment:
    source_id: str
    authority_level: SourceAuthorityLevel
    source_class: GermanLegalSourceClass
    authenticity_status: AuthenticityStatus
    verification_status: VerificationStatus
    policy_version: str
    reason_codes: tuple[str, ...]
    unresolved_limitations: tuple[str, ...]
    temporal_assessment_digest: str
    assessment_digest: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "reason_codes", tuple(sorted(set(self.reason_codes))))
        object.__setattr__(self, "unresolved_limitations", tuple(sorted(set(self.unresolved_limitations))))
        object.__setattr__(self, "assessment_digest", canonical_sha256(self, exclude_fields=("assessment_digest",)))
