"""Trusted system-installed German Law HAT implementation."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

from aioa_memory_kernel.contracts import (
    HatManifest,
    ScopeComparisonMode,
    ScopeDimension,
    ScopeValueType,
    validate_hat_manifest,
)

from .adapters import source_metadata_from_mapping
from .errors import GermanLawPolicyError
from .models import GermanLawRequest, GermanLegalSourceClass, LegalJurisdiction
from .policy import assess_source, authority_sort_key


def request_from_mapping(data: Mapping[str, Any]) -> GermanLawRequest:
    if not isinstance(data, Mapping) or len(data) > 16:
        raise GermanLawPolicyError("INVALID_REQUEST", "request must be a bounded object")
    knowledge = data.get("knowledge_as_of")
    if isinstance(knowledge, str):
        try:
            knowledge = datetime.fromisoformat(knowledge.replace("Z", "+00:00"))
        except ValueError as exc:
            raise GermanLawPolicyError("INVALID_REQUEST", "knowledge_as_of is invalid") from exc
    try:
        return GermanLawRequest(
            request_id=str(data["request_id"]),
            query_text=str(data["query_text"]),
            request_language=str(data["request_language"]),
            legal_jurisdiction=LegalJurisdiction(str(data["legal_jurisdiction"])),
            knowledge_as_of=knowledge,
            federal_state=data.get("federal_state"),
            legal_domain=data.get("legal_domain"),
            official_identifier_hint=data.get("official_identifier_hint"),
            court_or_proceeding_hint=data.get("court_or_proceeding_hint"),
        )
    except GermanLawPolicyError:
        raise
    except (KeyError, ValueError, TypeError) as exc:
        raise GermanLawPolicyError("INVALID_REQUEST", "request construction failed") from exc


class GermanLawHat:
    """Pure policy adapter. It does not retrieve, answer, publish, or execute content."""

    def __init__(self, manifest: HatManifest) -> None:
        if manifest.hat_id != "german-law" or manifest.hat_version != "1.0.0":
            raise GermanLawPolicyError("MANIFEST_MISMATCH", "unexpected German Law HAT identity")
        self.manifest = manifest

    def validate_manifest(self) -> None:
        validate_hat_manifest(self.manifest)

    def normalize_request(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        value = request_from_mapping(request)
        return {
            "request_id": value.request_id,
            "query_text": value.query_text,
            "request_language": value.request_language,
            "legal_jurisdiction": value.legal_jurisdiction.value,
            "knowledge_as_of": value.knowledge_as_of.isoformat() if value.knowledge_as_of else None,
            "federal_state": value.federal_state,
            "legal_domain": value.legal_domain,
            "official_identifier_hint": value.official_identifier_hint,
            "court_or_proceeding_hint": value.court_or_proceeding_hint,
            "request_digest": value.request_digest,
            "ambiguities": ("KNOWLEDGE_AS_OF_MISSING",) if value.knowledge_as_of is None else (),
        }

    def derive_scope_requirements(self, request: Mapping[str, Any]) -> tuple[ScopeDimension, ...]:
        value = request_from_mapping(request)
        dimensions = [
            ScopeDimension("legal_jurisdiction", value.legal_jurisdiction.value, ScopeValueType.STRING, ScopeComparisonMode.EXACT, "request", True),
            ScopeDimension("source_language", value.request_language, ScopeValueType.STRING, ScopeComparisonMode.EXACT, "request", True),
            ScopeDimension("legal_source_class", tuple(item.value for item in GermanLegalSourceClass), ScopeValueType.STRING_SET, ScopeComparisonMode.IN_SET, "policy", True),
        ]
        if value.knowledge_as_of is not None:
            dimensions.append(ScopeDimension("knowledge_as_of", value.knowledge_as_of, ScopeValueType.TIMESTAMP, ScopeComparisonMode.TIMESTAMP, "request", True))
        if value.federal_state is not None:
            dimensions.append(ScopeDimension("federal_state", value.federal_state, ScopeValueType.STRING, ScopeComparisonMode.EXACT, "request", True))
        return tuple(dimensions)

    def build_retrieval_constraints(self, dimensions: tuple[ScopeDimension, ...]) -> Mapping[str, Any]:
        if not isinstance(dimensions, tuple) or any(not isinstance(item, ScopeDimension) for item in dimensions):
            raise GermanLawPolicyError("INVALID_SCOPE", "typed scope dimensions are required")
        values = {item.name: item.value for item in dimensions}
        missing = tuple(name for name in ("legal_jurisdiction", "knowledge_as_of", "source_language", "legal_source_class") if name not in values)
        return {"contract_version": "german-law-retrieval-constraints-1a", "constraints": values, "ambiguities": missing, "executable_query": False}

    def rank_source_authority(self, source_metadata: tuple[Mapping[str, Any], ...]) -> tuple[str, ...]:
        assessments = []
        for item in source_metadata:
            request_data = item.get("request")
            metadata_data = item.get("metadata")
            if not isinstance(request_data, Mapping) or not isinstance(metadata_data, Mapping):
                raise GermanLawPolicyError("INVALID_AUTHORITY_INPUT", "request and metadata objects are required")
            assessment = assess_source(source_metadata_from_mapping(metadata_data), request_from_mapping(request_data))
            assessments.append(assessment)
        return tuple(item.source_id for item in sorted(assessments, key=authority_sort_key))

    def _undeclared(self) -> None:
        raise GermanLawPolicyError("CAPABILITY_NOT_DECLARED", "capability is not declared by German Law HAT 1.0.0")

    def extract_candidate_claims(self, draft_reference: str): self._undeclared()
    def detect_conflicts(self, evidence_references: tuple[str, ...]): self._undeclared()
    def create_correction_requirements(self, claim_references: tuple[str, ...]): self._undeclared()
    def create_memory_patch_proposal(self, correction_reference: str): self._undeclared()
