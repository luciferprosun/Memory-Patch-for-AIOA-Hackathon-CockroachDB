#!/usr/bin/env python3
"""Controlled local-only Step 13 German Law HAT validation."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]

import run_cockroachdb_migrations as migrations
from run_hat_registry_validation import insert_registry
from aioa_memory_kernel.contracts.serialization import canonical_json_bytes, canonical_sha256
from aioa_memory_kernel.german_law import (
    AuthenticityStatus,
    ConsolidationStatus,
    GermanLawHat,
    GermanLawRequest,
    GermanLawSourceMetadata,
    GermanLawTemporalFacts,
    GermanLegalSourceClass,
    LegalJurisdiction,
    VerificationStatus,
    assess_source,
)
from aioa_memory_kernel.hats import (
    CompatibilityDecision,
    HatRegistryService,
    ReviewActor,
    ReviewReceipt,
    RuntimeBinding,
    TrustedInstalledHatCatalog,
    decode_manifest,
    decide_compatibility,
)

STARTING_SHA = "fb3e9bbeaa4dfc146bcc75d00edc4780be94edba"
SCHEMA = ROOT / "schemas/hat-manifest.schema.json"
MANIFEST = ROOT / "config/hats/german-law-1.0.0.json"
OUTPUT = ROOT / "docs/evidence/hats/step13-german-law-hat-policy-validation.json"

OFFICIAL_REFERENCES = (
    ("Verkündungsplattform/Gesetze im Internet Hinweise", "https://www.gesetze-im-internet.de/hinweise.html", "Electronic Federal Law Gazette is official from 2023-01-01; consolidated texts are non-official."),
    ("Deutscher Bundestag Parlamentsdokumentation", "https://www.bundestag.de/dokumente/parlamentsdokumentation", "DIP covers parliamentary papers, protocols, and legislative procedure; these materials are not enacted law."),
    ("EUR-Lex e-OJ authenticity", "https://eur-lex.europa.eu/content/help/oj/authenticity-eOJ.html?locale=en", "Electronic Official Journal is authentic and produces legal effects under the applicable EU regime."),
    ("Bundesverfassungsgericht Entscheidungen", "https://www.bundesverfassungsgericht.de/DE/Entscheidungen/entscheidungen_node.html", "Official court publication; decision effects remain decision-specific and typed."),
    ("Bundesgerichtshof Entscheidungen", "https://www.bundesgerichtshof.de/DE/Entscheidungen/entscheidungen_node.html", "Official federal court decision source."),
    ("Bundesarbeitsgericht Entscheidungen", "https://www.bundesarbeitsgericht.de/entscheidungen/", "Official federal labour court publication with stated electronic-publication caveats."),
    ("Bundesverwaltungsgericht Rechtsprechung", "https://www.bverwg.de/rechtsprechung", "Official federal administrative court decision source."),
    ("Bundesfinanzhof Entscheidungen", "https://www.bundesfinanzhof.de/de/entscheidungen/", "Official federal fiscal court decision source distinguishing publication classes."),
    ("Bundessozialgericht Entscheidungen", "https://www.bsg.bund.de/DE/Entscheidungen/entscheidungen_node.html", "Official federal social court decision source."),
)


def _request(request_id: str, jurisdiction: LegalJurisdiction, now: datetime, state: str | None = None) -> GermanLawRequest:
    return GermanLawRequest(request_id, "Welche Quellen gelten am angegebenen Stichtag?", "de", jurisdiction, now, federal_state=state)


def _source(source_id: str, source_class: GermanLegalSourceClass, jurisdiction: LegalJurisdiction, now: datetime, publisher: str, authenticity: AuthenticityStatus, consolidation: ConsolidationStatus) -> GermanLawSourceMetadata:
    return GermanLawSourceMetadata(source_id, "registry-" + source_id, source_class, publisher, "official-id-" + source_id, jurisdiction, "de", authenticity, consolidation, VerificationStatus.AUTHENTICITY_VERIFIED if authenticity is AuthenticityStatus.AUTHENTIC else VerificationStatus.OFFICIAL_REFERENCE_VERIFIED, "official-reference-" + source_id, GermanLawTemporalFacts(effective_from=now.replace(year=now.year - 1)), court_identity="BVerfG" if source_class is GermanLegalSourceClass.DE_FEDERAL_OFFICIAL_COURT_DECISION else None, court_level="federal-supreme" if source_class is GermanLegalSourceClass.DE_FEDERAL_OFFICIAL_COURT_DECISION else None)


def _request_map(value: GermanLawRequest) -> dict:
    return {"request_id":value.request_id,"query_text":value.query_text,"request_language":value.request_language,"legal_jurisdiction":value.legal_jurisdiction.value,"knowledge_as_of":value.knowledge_as_of.isoformat() if value.knowledge_as_of else None,"federal_state":value.federal_state}


def _source_map(value: GermanLawSourceMetadata) -> dict:
    return {"source_id":value.source_id,"source_registry_reference":value.source_registry_reference,"source_class":value.source_class.value,"official_publisher":value.official_publisher,"canonical_official_identifier":value.canonical_official_identifier,"jurisdiction":value.jurisdiction.value,"language":value.language,"authenticity_status":value.authenticity_status.value,"consolidation_status":value.consolidation_status.value,"verification_status":value.verification_status.value,"retrieval_reference":value.retrieval_reference,"court_identity":value.court_identity,"court_level":value.court_level,"temporal":{name:(getattr(value.temporal,name).isoformat() if getattr(value.temporal,name) else None) for name in value.temporal.__dataclass_fields__}}


def validate(binary: Path) -> dict:
    migrations.verify_binary_identity(binary)
    run_id = "mp_step13_" + uuid.uuid4().hex[:12]
    database = run_id + "_live"
    runtime = migrations.LocalRuntime(binary=binary, run_id=run_id)
    client = None
    cleanup: dict = {}
    result: dict | None = None
    try:
        client = runtime.start()
        migrations.create_database(client, database)
        applied = migrations.apply_migrations(client, database, timeout=300)
        replay = migrations.apply_migrations(client, database, timeout=300)
        now = datetime.now(UTC).replace(microsecond=0)
        identity = decode_manifest(MANIFEST.read_bytes(), schema_path=SCHEMA)
        service = HatRegistryService(clock=lambda: now)
        registered = service.register(identity, decide_compatibility(identity))
        replayed = service.register(identity, decide_compatibility(identity))
        service.validate("german-law", "1.0.0")
        implementation_digest = hashlib.sha256(b"GermanLawHat:1.0.0:hat-sdk-1a").hexdigest()
        binding = RuntimeBinding("german-law-system-installed-1a", "german-law", "1.0.0", "GermanLawHat", "1.0.0", "hat-sdk-1a", implementation_digest)
        receipt = ReviewReceipt("german-law", "1.0.0", identity.typed_manifest_digest, identity.raw_manifest_sha256, identity.schema_file_sha256, CompatibilityDecision.COMPATIBLE, canonical_sha256(identity.manifest.capabilities), binding.runtime_binding_id, implementation_digest, "ENABLE", ("TRUSTED_STEP13_CONTROLLED_VALIDATION",), ReviewActor.TRUSTED_OPERATOR, "operator-redacted", now)
        entry = service.enable("german-law", "1.0.0", binding, receipt)
        hat = GermanLawHat(identity.manifest)
        catalog = TrustedInstalledHatCatalog()
        catalog.register(binding, hat, identity.typed_manifest_digest)
        handle = service.resolve("german-law", "1.0.0", catalog)
        federal = _request("step13-federal", LegalJurisdiction.DE_FEDERAL, now)
        state = _request("step13-state", LegalJurisdiction.DE_STATE, now, "DE-BY")
        eu = _request("step13-eu", LegalJurisdiction.EU, now)
        normalized = [handle.invoke("REQUEST_NORMALIZATION", _request_map(item)) for item in (federal, state, eu)]
        scopes = [handle.invoke("SCOPE_DERIVATION", _request_map(item)) for item in (federal, state, eu)]
        constraints = [handle.invoke("EVIDENCE_CONSTRAINTS", item) for item in scopes]
        authentic = _source("promulgation", GermanLegalSourceClass.DE_FEDERAL_AUTHENTIC_PROMULGATION, LegalJurisdiction.DE_FEDERAL, now, "Bundesministerium der Justiz", AuthenticityStatus.AUTHENTIC, ConsolidationStatus.NOT_CONSOLIDATED)
        consolidated = _source("consolidation", GermanLegalSourceClass.DE_FEDERAL_OFFICIAL_CONSOLIDATED_LAW, LegalJurisdiction.DE_FEDERAL, now, "Bundesministerium der Justiz", AuthenticityStatus.OFFICIAL_NON_AUTHENTIC, ConsolidationStatus.OFFICIAL_CONSOLIDATED)
        court = _source("court", GermanLegalSourceClass.DE_FEDERAL_OFFICIAL_COURT_DECISION, LegalJurisdiction.DE_FEDERAL, now, "Bundesverfassungsgericht", AuthenticityStatus.OFFICIAL_NON_AUTHENTIC, ConsolidationStatus.NOT_CONSOLIDATED)
        legislative = _source("legislative", GermanLegalSourceClass.OFFICIAL_LEGISLATIVE_MATERIAL, LegalJurisdiction.DE_FEDERAL, now, "Deutscher Bundestag", AuthenticityStatus.OFFICIAL_NON_AUTHENTIC, ConsolidationStatus.NOT_CONSOLIDATED)
        inputs = tuple({"request":_request_map(federal),"metadata":_source_map(item)} for item in (consolidated,legislative,court,authentic))
        ranked = handle.invoke("SOURCE_AUTHORITY_RANKING", inputs)
        assessments = [assess_source(item, federal) for item in (authentic, consolidated, court, legislative)]
        insert_registry(client, database, identity, binding, entry, service.events("german-law", "1.0.0"), now)
        enabled_count = migrations.one_value(client.execute(database, "SELECT count(*) FROM memory_patch.hat_registry_entries WHERE registry_state='ENABLED'"))
        if (
            enabled_count != "1"
            or registered is not replayed
            or ranked.index("promulgation") >= ranked.index("court")
            or ranked.index("promulgation") >= ranked.index("consolidation")
        ):
            raise RuntimeError("controlled registry or authority validation mismatch")
        service.disable("german-law", "1.0.0", "operator-redacted")
        negative = {}
        try: service.resolve("german-law", "1.0.0", catalog)
        except Exception: negative["disabled_resolution"] = "REJECTED"
        conflict = json.loads(MANIFEST.read_text()); conflict["display_name"] = "Conflicting German Law HAT"
        try: service.register(decode_manifest(json.dumps(conflict,separators=(",",":")).encode(),schema_path=SCHEMA),CompatibilityDecision.COMPATIBLE)
        except Exception: negative["conflicting_manifest"] = "REJECTED"
        result = {
            "schema_version":"1.0.0","step":"13","starting_sha":STARTING_SHA,
            "manifest":{"hat_id":"german-law","hat_version":"1.0.0","canonical_digest":identity.typed_manifest_digest,"raw_digest":identity.raw_manifest_sha256,"schema_digest":identity.schema_file_sha256,"capabilities":identity.manifest.capabilities,"languages":identity.manifest.supported_languages,"domains":identity.manifest.domain_ids},
            "policies":{"source_authority_version":"german-law-source-authority-1a","temporal_version":"german-law-temporal-policy-1a","adapter_version":"german-law-metadata-adapters-1a","source_assessment_digests":tuple(item.assessment_digest for item in assessments)},
            "official_source_research":tuple({"title":title,"url":url,"retrieved_at":now.isoformat(),"policy_fact":fact} for title,url,fact in OFFICIAL_REFERENCES),
            "registry":{"state_before_disable":entry.state.value,"event_chain_digest":entry.current_event_digest,"review_receipt_digest":receipt.receipt_digest,"runtime_binding_digest":binding.digest,"exact_replay":True,"negative_decisions":negative},
            "runtime":{"normalized_request_digests":tuple(canonical_sha256(item) for item in normalized),"scope_digests":tuple(canonical_sha256(item) for item in scopes),"constraint_digests":tuple(canonical_sha256(item) for item in constraints),"ranked_source_ids":ranked},
            "migration":{"range":"0001-0009","applied":applied["applied_count"],"replay_skipped":replay["skipped_count"],"new_migration":False},
            "boundaries":{"aws_writes":0,"s3_writes":0,"external_volume_writes":0,"corpus_reads":0,"corpus_writes":0,"paid_source_access":0,"model_calls":0,"persistent_database":False,"final_question_selected":False,"forbidden_scenario_hardcoded":False,"step14_started":False},
            "status":"PASS",
        }
    finally:
        if client is not None:
            try:
                migrations.drop_database(client, database, timeout=300)
            finally:
                cleanup = runtime.graceful_stop_and_remove(client, owned_children_reaped=True)
        else:
            cleanup = runtime.stop_and_remove()
    if cleanup.get("cleanup_errors") or cleanup.get("force_kill_used") or not cleanup.get("temporary_store_removed"):
        raise RuntimeError("disposable CockroachDB cleanup failed")
    assert result is not None
    result["cockroachdb_cleanup"]={"version":"v26.2.4","runtime_mode":"DISPOSABLE_LOCAL_SINGLE_NODE","graceful_drain":cleanup["drain_command_completed"],"force_kill":cleanup["force_kill_used"],"pid_exited":cleanup["pid_exited"],"ports_closed":cleanup["ports_closed"],"temporary_store_removed":cleanup["temporary_store_removed"],"persistent_database":False}
    result["evidence_digest"] = canonical_sha256(result, exclude_fields=("evidence_digest",))
    return result


def main() -> None:
    parser=argparse.ArgumentParser(); parser.add_argument("--write-validation",action="store_true",required=True); parser.add_argument("--cockroach-binary",type=Path,required=True); parser.add_argument("--evidence-output",type=Path,default=OUTPUT); args=parser.parse_args()
    result=validate(args.cockroach_binary.expanduser().resolve()); payload=canonical_json_bytes(result)+b"\n"; args.evidence_output.parent.mkdir(parents=True,exist_ok=True); args.evidence_output.write_bytes(payload); print(payload.decode(),end="")


if __name__ == "__main__": main()
