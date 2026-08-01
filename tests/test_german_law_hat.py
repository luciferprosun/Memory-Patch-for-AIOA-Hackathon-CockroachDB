from __future__ import annotations

import dataclasses
import hashlib
import json
import unittest
from datetime import UTC, datetime
from pathlib import Path

from aioa_memory_kernel.contracts import assert_system_installed_hat
from aioa_memory_kernel.contracts.serialization import canonical_sha256
from aioa_memory_kernel.german_law import (
    AuthenticityStatus,
    ConsolidatedFederalLawMetadataAdapter,
    ConsolidationStatus,
    EuLegalActMetadataAdapter,
    FederalGazetteMetadataAdapter,
    GermanLawHat,
    GermanLawMetadataAdapterRegistry,
    GermanLawPolicyError,
    GermanLawRequest,
    GermanLawSourceMetadata,
    GermanLawTemporalFacts,
    GermanLegalSourceClass,
    LegalJurisdiction,
    LegislativeMaterialMetadataAdapter,
    OfficialCourtDecisionMetadataAdapter,
    TemporalDecision,
    VerificationStatus,
    assess_source,
    assess_temporal,
)
from aioa_memory_kernel.hats import (
    CompatibilityDecision,
    HatRegistryError,
    HatRegistryService,
    ReviewActor,
    ReviewReceipt,
    RuntimeBinding,
    TrustedInstalledHatCatalog,
    decode_manifest,
    decide_compatibility,
)
from aioa_memory_kernel.sources.models import SourceAuthorityLevel
from tests._support import REPOSITORY_ROOT

MANIFEST_PATH = REPOSITORY_ROOT / "config/hats/german-law-1.0.0.json"
SCHEMA_PATH = REPOSITORY_ROOT / "schemas/hat-manifest.schema.json"
NOW = datetime(2031, 6, 1, tzinfo=UTC)
IDENTITY = decode_manifest(MANIFEST_PATH.read_bytes(), schema_path=SCHEMA_PATH)


def request(**changes):
    values = dict(request_id="request-synthetic-1", query_text="Welche Fassung gilt am Stichtag?", request_language="de", legal_jurisdiction=LegalJurisdiction.DE_FEDERAL, knowledge_as_of=NOW)
    values.update(changes)
    return GermanLawRequest(**values)


def metadata(source_class=GermanLegalSourceClass.DE_FEDERAL_AUTHENTIC_PROMULGATION, **changes):
    values = dict(
        source_id="source-synthetic-1",
        source_registry_reference="source-registry-synthetic-1",
        source_class=source_class,
        official_publisher="Bundesministerium der Justiz",
        canonical_official_identifier="synthetic-official-id-1",
        jurisdiction=LegalJurisdiction.DE_FEDERAL,
        language="de",
        authenticity_status=AuthenticityStatus.AUTHENTIC,
        consolidation_status=ConsolidationStatus.NOT_CONSOLIDATED,
        verification_status=VerificationStatus.AUTHENTICITY_VERIFIED,
        retrieval_reference="official-reference-synthetic-1",
        temporal=GermanLawTemporalFacts(effective_from=datetime(2030, 1, 1, tzinfo=UTC)),
    )
    values.update(changes)
    return GermanLawSourceMetadata(**values)


def mapping(value: GermanLawSourceMetadata) -> dict:
    return {
        "source_id": value.source_id,
        "source_registry_reference": value.source_registry_reference,
        "source_class": value.source_class.value,
        "official_publisher": value.official_publisher,
        "canonical_official_identifier": value.canonical_official_identifier,
        "jurisdiction": value.jurisdiction.value,
        "language": value.language,
        "authenticity_status": value.authenticity_status.value,
        "consolidation_status": value.consolidation_status.value,
        "verification_status": value.verification_status.value,
        "retrieval_reference": value.retrieval_reference,
        "court_identity": value.court_identity,
        "court_level": value.court_level,
        "temporal": {name: (getattr(value.temporal, name).isoformat() if getattr(value.temporal, name) else None) for name in value.temporal.__dataclass_fields__},
    }


def request_mapping(value: GermanLawRequest) -> dict:
    return {"request_id":value.request_id,"query_text":value.query_text,"request_language":value.request_language,"legal_jurisdiction":value.legal_jurisdiction.value,"knowledge_as_of":value.knowledge_as_of.isoformat() if value.knowledge_as_of else None,"federal_state":value.federal_state,"legal_domain":value.legal_domain,"official_identifier_hint":value.official_identifier_hint,"court_or_proceeding_hint":value.court_or_proceeding_hint}


class ManifestPackageTests(unittest.TestCase):
    def test_manifest_identity_and_compatibility(self):
        self.assertEqual((IDENTITY.manifest.hat_id, IDENTITY.manifest.hat_version), ("german-law", "1.0.0"))
        self.assertIs(decide_compatibility(IDENTITY), CompatibilityDecision.COMPATIBLE)

    def test_manifest_domains_languages_capabilities(self):
        self.assertEqual(IDENTITY.manifest.domain_ids, ("law.de", "law.de.federal", "law.de.state", "law.eu"))
        self.assertEqual(IDENTITY.manifest.supported_languages, ("de",))
        self.assertEqual(set(IDENTITY.manifest.capabilities), {"REQUEST_NORMALIZATION","SCOPE_DERIVATION","EVIDENCE_CONSTRAINTS","SOURCE_AUTHORITY_RANKING"})

    def test_manifest_zero_authority(self):
        policy = IDENTITY.manifest.security_policy
        self.assertFalse(policy.executable_user_code)
        self.assertFalse(policy.private_memory_access)
        self.assertEqual({policy.external_action_authority.value, policy.canonical_write_authority.value, policy.patch_approval_authority.value, policy.patch_commit_authority.value}, {"NONE"})

    def test_manifest_digest_deterministic(self):
        formatted = json.dumps(json.loads(MANIFEST_PATH.read_text()), indent=2, ensure_ascii=False).encode()
        other = decode_manifest(formatted, schema_path=SCHEMA_PATH)
        self.assertNotEqual(other.raw_manifest_sha256, IDENTITY.raw_manifest_sha256)
        self.assertEqual(other.typed_manifest_digest, IDENTITY.typed_manifest_digest)

    def test_manifest_contains_no_mutable_or_dynamic_fields(self):
        text = MANIFEST_PATH.read_text().casefold()
        for token in ("latest", "module_path", "entry_point", "package_url", "shell", "command"):
            self.assertNotIn(token, text)

    def test_manifest_does_not_hardcode_forbidden_scenario(self):
        self.assertNotIn("nachweisgesetz", MANIFEST_PATH.read_text().casefold())


class RequestScopeTests(unittest.TestCase):
    def test_federal_request(self): self.assertEqual(request().legal_jurisdiction, LegalJurisdiction.DE_FEDERAL)

    def test_state_request_requires_state(self):
        with self.assertRaisesRegex(GermanLawPolicyError, "state-law"):
            request(legal_jurisdiction=LegalJurisdiction.DE_STATE)

    def test_state_request_accepts_explicit_state(self):
        value=request(legal_jurisdiction=LegalJurisdiction.DE_STATE,federal_state="DE-BY")
        self.assertEqual(value.federal_state,"DE-BY")

    def test_eu_request(self): self.assertEqual(request(legal_jurisdiction=LegalJurisdiction.EU).legal_jurisdiction,LegalJurisdiction.EU)

    def test_language_does_not_infer_jurisdiction(self):
        with self.assertRaises(GermanLawPolicyError):
            GermanLawRequest("x","Text","de","DE_FEDERAL",NOW)  # type: ignore[arg-type]

    def test_only_german_request_language(self):
        with self.assertRaisesRegex(GermanLawPolicyError,"German requests"):
            request(request_language="en")

    def test_missing_knowledge_as_of_is_explicit(self):
        hat=GermanLawHat(IDENTITY.manifest); normalized=hat.normalize_request(request_mapping(request(knowledge_as_of=None)))
        self.assertEqual(normalized["ambiguities"],("KNOWLEDGE_AS_OF_MISSING",))

    def test_request_wording_preserved(self):
        text="  Wortlaut bleibt?  "
        with self.assertRaises(GermanLawPolicyError): request(query_text=text)
        text="Wortlaut  bleibt?"; self.assertEqual(request(query_text=text).query_text,text)

    def test_request_digest_deterministic(self): self.assertEqual(request().request_digest,request().request_digest)

    def test_scope_derivation(self):
        dimensions=GermanLawHat(IDENTITY.manifest).derive_scope_requirements(request_mapping(request()))
        self.assertEqual({item.name for item in dimensions},{"legal_jurisdiction","knowledge_as_of","source_language","legal_source_class"})

    def test_retrieval_constraints_are_declarative(self):
        hat=GermanLawHat(IDENTITY.manifest); dims=hat.derive_scope_requirements(request_mapping(request())); output=hat.build_retrieval_constraints(dims)
        self.assertFalse(output["executable_query"]); self.assertEqual(output["ambiguities"],())


class SourceAuthorityTests(unittest.TestCase):
    def test_authentic_promulgation_primary(self): self.assertIs(assess_source(metadata(),request()).authority_level,SourceAuthorityLevel.OFFICIAL_PRIMARY)

    def test_consolidated_text_distinction(self):
        value=metadata(GermanLegalSourceClass.DE_FEDERAL_OFFICIAL_CONSOLIDATED_LAW,authenticity_status=AuthenticityStatus.OFFICIAL_NON_AUTHENTIC,consolidation_status=ConsolidationStatus.OFFICIAL_CONSOLIDATED,verification_status=VerificationStatus.OFFICIAL_REFERENCE_VERIFIED)
        result=assess_source(value,request()); self.assertIs(result.authority_level,SourceAuthorityLevel.AUTHORITATIVE_SECONDARY); self.assertIn("CONSOLIDATED_TEXT_NOT_AUTHENTIC_PROMULGATION",result.reason_codes)

    def test_court_decision_case_specific(self):
        value=metadata(GermanLegalSourceClass.DE_FEDERAL_OFFICIAL_COURT_DECISION,official_publisher="Bundesverfassungsgericht",court_identity="BVerfG",court_level="federal-supreme")
        result=assess_source(value,request())
        self.assertIn("DECISION_PRIMARY_ONLY_FOR_IDENTIFIED_CASE",result.reason_codes)
        self.assertIn("COURT_OR_PROCEEDING_SCOPE_UNBOUND",result.unresolved_limitations)

    def test_court_decision_exact_scope_binding(self):
        value=metadata(GermanLegalSourceClass.DE_FEDERAL_OFFICIAL_COURT_DECISION,official_publisher="Bundesverfassungsgericht",court_identity="BVerfG",court_level="federal-supreme")
        result=assess_source(value,request(court_or_proceeding_hint="BVerfG"))
        self.assertIn("COURT_OR_PROCEEDING_SCOPE_BOUND",result.reason_codes)
        self.assertNotIn("COURT_OR_PROCEEDING_SCOPE_UNBOUND",result.unresolved_limitations)
        self.assertNotIn("COURT_OR_PROCEEDING_SCOPE_MISMATCH",result.unresolved_limitations)

    def test_court_decision_scope_mismatch_fails_closed(self):
        value=metadata(GermanLegalSourceClass.DE_FEDERAL_OFFICIAL_COURT_DECISION,official_publisher="Bundesverfassungsgericht",court_identity="BVerfG",court_level="federal-supreme")
        result=assess_source(value,request(court_or_proceeding_hint="BAG"))
        self.assertIn("COURT_OR_PROCEEDING_SCOPE_MISMATCH",result.unresolved_limitations)

    def test_legislative_material_not_enacted(self):
        value=metadata(GermanLegalSourceClass.OFFICIAL_LEGISLATIVE_MATERIAL,official_publisher="Deutscher Bundestag",authenticity_status=AuthenticityStatus.OFFICIAL_NON_AUTHENTIC,verification_status=VerificationStatus.OFFICIAL_REFERENCE_VERIFIED)
        self.assertIn("LEGISLATIVE_HISTORY_NOT_ENACTED_LAW",assess_source(value,request()).reason_codes)

    def test_administrative_guidance_distinction(self):
        value=metadata(GermanLegalSourceClass.OFFICIAL_ADMINISTRATIVE_GUIDANCE,official_publisher="Synthetic Federal Agency",authenticity_status=AuthenticityStatus.OFFICIAL_NON_AUTHENTIC)
        self.assertIn("AGENCY_INTERPRETATION_NOT_ENACTED_LAW",assess_source(value,request()).reason_codes)

    def test_eu_authentic_distinct_from_consolidated(self):
        authentic=metadata(GermanLegalSourceClass.EU_AUTHENTIC_OFFICIAL_JOURNAL,jurisdiction=LegalJurisdiction.EU,official_publisher="Publications Office of the European Union")
        consolidated=dataclasses.replace(authentic,source_id="eu-consolidated",source_class=GermanLegalSourceClass.EU_OFFICIAL_CONSOLIDATED_ACT,authenticity_status=AuthenticityStatus.OFFICIAL_NON_AUTHENTIC,consolidation_status=ConsolidationStatus.OFFICIAL_CONSOLIDATED,verification_status=VerificationStatus.OFFICIAL_REFERENCE_VERIFIED)
        self.assertNotEqual(assess_source(authentic,request(legal_jurisdiction=LegalJurisdiction.EU)).authority_level,assess_source(consolidated,request(legal_jurisdiction=LegalJurisdiction.EU)).authority_level)

    def test_user_derived_unknown_remain_distinct(self):
        expected={GermanLegalSourceClass.USER_SUPPLIED_LEGAL_DOCUMENT:SourceAuthorityLevel.USER_SUPPLIED,GermanLegalSourceClass.DERIVED_SUMMARY:SourceAuthorityLevel.DERIVED,GermanLegalSourceClass.UNKNOWN_LEGAL_SOURCE:SourceAuthorityLevel.UNKNOWN}
        for cls,level in expected.items(): self.assertIs(assess_source(metadata(cls,official_publisher=None,canonical_official_identifier=None,verification_status=VerificationStatus.UNVERIFIED),request()).authority_level,level)

    def test_spoofed_publisher_flagged(self):
        result=assess_source(metadata(official_publisher="Private Example Publisher"),request())
        self.assertIn("OFFICIAL_PUBLISHER_MISMATCH",result.unresolved_limitations)

    def test_jurisdiction_mismatch_flagged(self):
        result=assess_source(metadata(jurisdiction=LegalJurisdiction.EU),request())
        self.assertIn("JURISDICTION_MISMATCH",result.unresolved_limitations)

    def test_unverified_flagged(self):
        result=assess_source(metadata(verification_status=VerificationStatus.UNVERIFIED),request())
        self.assertIn("SOURCE_VERIFICATION_INCOMPLETE",result.unresolved_limitations)

    def test_assessment_digest_deterministic(self): self.assertEqual(assess_source(metadata(),request()).assessment_digest,assess_source(metadata(),request()).assessment_digest)


class TemporalAdapterRuntimeTests(unittest.TestCase):
    def test_publication_does_not_substitute_effective_date(self):
        value=metadata(temporal=GermanLawTemporalFacts(published_at=datetime(2030,1,1,tzinfo=UTC)))
        self.assertIs(assess_temporal(value,request()).decision,TemporalDecision.UNKNOWN)

    def test_future_effective_date(self):
        value=metadata(temporal=GermanLawTemporalFacts(effective_from=datetime(2032,1,1,tzinfo=UTC)))
        self.assertIs(assess_temporal(value,request()).decision,TemporalDecision.NOT_YET_APPLICABLE)

    def test_expired_interval(self):
        value=metadata(temporal=GermanLawTemporalFacts(effective_from=datetime(2020,1,1,tzinfo=UTC),effective_to=datetime(2030,1,1,tzinfo=UTC)))
        self.assertIs(assess_temporal(value,request()).decision,TemporalDecision.EXPIRED)

    def test_open_interval(self): self.assertIs(assess_temporal(metadata(),request()).decision,TemporalDecision.APPLICABLE)

    def test_invalid_interval(self):
        value=metadata(temporal=GermanLawTemporalFacts(effective_from=datetime(2032,1,1,tzinfo=UTC),effective_to=datetime(2030,1,1,tzinfo=UTC)))
        self.assertIs(assess_temporal(value,request()).decision,TemporalDecision.CONFLICTING)

    def test_missing_knowledge_time(self): self.assertIs(assess_temporal(metadata(),request(knowledge_as_of=None)).decision,TemporalDecision.UNKNOWN)

    def test_fixed_adapters(self):
        classes=(FederalGazetteMetadataAdapter,ConsolidatedFederalLawMetadataAdapter,OfficialCourtDecisionMetadataAdapter,EuLegalActMetadataAdapter,LegislativeMaterialMetadataAdapter)
        source_classes=(GermanLegalSourceClass.DE_FEDERAL_AUTHENTIC_PROMULGATION,GermanLegalSourceClass.DE_FEDERAL_OFFICIAL_CONSOLIDATED_LAW,GermanLegalSourceClass.DE_FEDERAL_OFFICIAL_COURT_DECISION,GermanLegalSourceClass.EU_AUTHENTIC_OFFICIAL_JOURNAL,GermanLegalSourceClass.OFFICIAL_LEGISLATIVE_MATERIAL)
        for adapter,cls in zip(classes,source_classes): self.assertEqual(adapter().adapt(mapping(metadata(cls))).metadata.source_class,cls)

    def test_unsupported_adapter_fails_closed(self):
        with self.assertRaisesRegex(GermanLawPolicyError,"no fixed adapter"):
            GermanLawMetadataAdapterRegistry().adapt(GermanLegalSourceClass.DERIVED_SUMMARY,mapping(metadata(GermanLegalSourceClass.DERIVED_SUMMARY)))

    def test_installed_hat_protocol(self): assert_system_installed_hat(GermanLawHat(IDENTITY.manifest))

    def test_registry_enable_resolve_capabilities_and_disable(self):
        service=HatRegistryService(clock=lambda:NOW); service.register(IDENTITY,decide_compatibility(IDENTITY)); service.validate("german-law","1.0.0")
        digest=hashlib.sha256(b"german-law-installed-1.0.0").hexdigest(); binding=RuntimeBinding("german-law-system-1a","german-law","1.0.0","GermanLawHat","1.0.0","hat-sdk-1a",digest)
        receipt=ReviewReceipt("german-law","1.0.0",IDENTITY.typed_manifest_digest,IDENTITY.raw_manifest_sha256,IDENTITY.schema_file_sha256,CompatibilityDecision.COMPATIBLE,canonical_sha256(IDENTITY.manifest.capabilities),binding.runtime_binding_id,digest,"ENABLE",("TRUSTED_STEP13_VALIDATION",),ReviewActor.TRUSTED_OPERATOR,"operator-redacted",NOW)
        service.enable("german-law","1.0.0",binding,receipt); catalog=TrustedInstalledHatCatalog(); catalog.register(binding,GermanLawHat(IDENTITY.manifest),IDENTITY.typed_manifest_digest)
        handle=service.resolve("german-law","1.0.0",catalog); result=handle.invoke("REQUEST_NORMALIZATION",request_mapping(request())); self.assertEqual(result["legal_jurisdiction"],"DE_FEDERAL")
        with self.assertRaises(HatRegistryError): handle.invoke("CLAIM_EXTRACTION","x")
        service.disable("german-law","1.0.0","operator-redacted")
        with self.assertRaises(HatRegistryError): handle.invoke("REQUEST_NORMALIZATION",request_mapping(request()))

    def test_rank_authentic_before_consolidated(self):
        hat=GermanLawHat(IDENTITY.manifest); req=request_mapping(request())
        authentic=metadata(source_id="authentic")
        consolidated=metadata(GermanLegalSourceClass.DE_FEDERAL_OFFICIAL_CONSOLIDATED_LAW,source_id="consolidated",authenticity_status=AuthenticityStatus.OFFICIAL_NON_AUTHENTIC,consolidation_status=ConsolidationStatus.OFFICIAL_CONSOLIDATED,verification_status=VerificationStatus.OFFICIAL_REFERENCE_VERIFIED)
        self.assertEqual(hat.rank_source_authority(({"request":req,"metadata":mapping(consolidated)},{"request":req,"metadata":mapping(authentic)})),("authentic","consolidated"))

    def test_generic_ranking_does_not_promote_unbound_court_decision(self):
        hat=GermanLawHat(IDENTITY.manifest); req=request_mapping(request())
        authentic=metadata(source_id="promulgation")
        court=metadata(GermanLegalSourceClass.DE_FEDERAL_OFFICIAL_COURT_DECISION,source_id="court",official_publisher="Bundesverfassungsgericht",court_identity="BVerfG",court_level="federal-supreme",authenticity_status=AuthenticityStatus.OFFICIAL_NON_AUTHENTIC)
        self.assertEqual(hat.rank_source_authority(({"request":req,"metadata":mapping(court)},{"request":req,"metadata":mapping(authentic)})),("promulgation","court"))

    def test_no_import_time_or_runtime_external_capability(self):
        source=(REPOSITORY_ROOT/"src/aioa_memory_kernel/german_law/hat.py").read_text()
        for token in ("subprocess", "requests.", "urllib.request", "os.system", "eval(", "exec("):
            self.assertNotIn(token,source)


if __name__ == "__main__":
    unittest.main()
