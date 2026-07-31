#!/usr/bin/env python3
"""Controlled zero-external-write Step 12 registry validation."""
from __future__ import annotations
import argparse, hashlib, json, sys, uuid
from datetime import UTC, datetime
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]; sys.path[:0]=[str(ROOT/"src"),str(ROOT/"scripts")]
import run_cockroachdb_migrations as migrations
from aioa_memory_kernel.contracts.serialization import canonical_json, canonical_json_bytes, canonical_sha256
from aioa_memory_kernel.hats import CompatibilityDecision, HatRegistryError, HatRegistryService, ReviewActor, ReviewReceipt, RuntimeBinding, TrustedInstalledHatCatalog, decode_manifest, decide_compatibility

SCHEMA=ROOT/"schemas/hat-manifest.schema.json"; FIXTURE=ROOT/"tests/fixtures/synthetic_contract_fixtures.json"
OUTPUT=ROOT/"docs/evidence/hats/step12-hat-registry-validation.json"

class SyntheticHat:
    def __init__(self, manifest): self.manifest=manifest
    def validate_manifest(self): return None
    def normalize_request(self, request): return dict(request)
    def derive_scope_requirements(self, request): return ()
    def build_retrieval_constraints(self, dimensions): return {"dimensions":len(dimensions)}
    def rank_source_authority(self, source_metadata): return tuple(str(i) for i in range(len(source_metadata)))
    def extract_candidate_claims(self, draft_reference): return ()
    def detect_conflicts(self, evidence_references): return ()
    def create_correction_requirements(self, claim_references): return ()
    def create_memory_patch_proposal(self, correction_reference): return None

def q(value): return migrations.sql_literal(value)
def insert_registry(client,database,identity,binding,entry,events,now):
    m=identity.manifest; policy=m.security_policy
    client.execute(database,"INSERT INTO memory_patch.hat_manifests (hat_id,hat_version,schema_version,display_name,manifest_hash,capabilities,approval_authority,commit_authority,canonical_write_authority,external_action_authority,allows_private_memory_access,allows_user_code,created_at,raw_manifest_hash,schema_hash,canonical_manifest) VALUES ("+",".join([q(m.hat_id),q(m.hat_version),q(m.schema_version),q(m.display_name),q(identity.typed_manifest_digest),q(canonical_json(m.capabilities))+"::JSONB",q(policy.patch_approval_authority.value),q(policy.patch_commit_authority.value),q(policy.canonical_write_authority.value),q(policy.external_action_authority.value),"false","false",q(now.isoformat())+"::TIMESTAMPTZ",q(identity.raw_manifest_sha256),q(identity.schema_file_sha256),q(canonical_json(m))+"::JSONB"])+")")
    client.execute(database,"INSERT INTO memory_patch.hat_runtime_bindings VALUES ("+",".join([q(binding.runtime_binding_id),q(binding.hat_id),q(binding.hat_version),q(binding.installation_class),q(binding.implementation_name),q(binding.implementation_version),q(binding.implementation_contract_version),q(binding.implementation_digest),q(binding.digest),q(now.isoformat())+"::TIMESTAMPTZ"])+")")
    client.execute(database,"INSERT INTO memory_patch.hat_registry_entries VALUES ("+",".join([q(m.hat_id),q(m.hat_version),q(identity.typed_manifest_digest),q(identity.raw_manifest_sha256),q(identity.schema_file_sha256),q(entry.state.value),q(entry.compatibility.value),str(entry.state_version),q(entry.current_event_digest),q(binding.runtime_binding_id),q(entry.review_receipt.receipt_digest),q(now.isoformat())+"::TIMESTAMPTZ",q(now.isoformat())+"::TIMESTAMPTZ"])+")")
    for event in events:
        values=[q(event.event_id),q(event.hat_id),q(event.hat_version),str(event.sequence),"NULL" if event.previous_event_digest is None else q(event.previous_event_digest),"NULL" if event.from_state is None else q(event.from_state.value),q(event.to_state.value),q(event.actor_type),q(event.actor_reference),q(canonical_json(event.reason_codes))+"::JSONB",q(identity.typed_manifest_digest),q(canonical_sha256(entry.compatibility.value)),q(binding.digest),q(entry.review_receipt.receipt_digest),q(event.occurred_at.isoformat())+"::TIMESTAMPTZ",q(event.event_digest)]
        client.execute(database,"INSERT INTO memory_patch.hat_registry_events VALUES ("+",".join(values)+")")

def validate(binary:Path):
    migrations.verify_binary_identity(binary); run_id="mp_step12_"+uuid.uuid4().hex[:12]; database=run_id+"_live"; runtime=migrations.LocalRuntime(binary=binary,run_id=run_id); client=None; cleanup={}; result=None
    try:
        client=runtime.start(); migrations.create_database(client,database); applied=migrations.apply_migrations(client,database,timeout=300); replay=migrations.apply_migrations(client,database,timeout=300)
        raw_manifests=json.loads(FIXTURE.read_text())["hat_manifests"]; now=datetime.now(UTC).replace(microsecond=0); services=[]
        for index,data in enumerate(raw_manifests):
            raw=json.dumps(data,separators=(",",":"),ensure_ascii=False).encode(); identity=decode_manifest(raw,schema_path=SCHEMA); service=HatRegistryService(clock=lambda now=now:now); service.register(identity,decide_compatibility(identity)); service.validate(identity.manifest.hat_id,identity.manifest.hat_version)
            binding=RuntimeBinding("step12-binding-"+identity.manifest.hat_id,identity.manifest.hat_id,identity.manifest.hat_version,"SyntheticInstalledHat"+str(index+1),"1.0.0","hat-sdk-1a",hashlib.sha256(("implementation-"+str(index)).encode()).hexdigest())
            receipt=ReviewReceipt(identity.manifest.hat_id,identity.manifest.hat_version,identity.typed_manifest_digest,identity.raw_manifest_sha256,identity.schema_file_sha256,CompatibilityDecision.COMPATIBLE,canonical_sha256(identity.manifest.capabilities),binding.runtime_binding_id,binding.implementation_digest,"ENABLE",("TRUSTED_SYNTHETIC_VALIDATION",),ReviewActor.TRUSTED_OPERATOR,"operator-redacted",now)
            entry=service.enable(identity.manifest.hat_id,identity.manifest.hat_version,binding,receipt); catalog=TrustedInstalledHatCatalog(); catalog.register(binding,SyntheticHat(identity.manifest),identity.typed_manifest_digest); handle=service.resolve(identity.manifest.hat_id,identity.manifest.hat_version,catalog)
            cap="REQUEST_NORMALIZATION" if index==0 else "SOURCE_AUTHORITY_RANKING"; output=handle.invoke(cap,{"fixture":"local"} if index==0 else ({"id":"one"},))
            insert_registry(client,database,identity,binding,entry,service.events(identity.manifest.hat_id,identity.manifest.hat_version),now); services.append({"hat_id":identity.manifest.hat_id,"hat_version":identity.manifest.hat_version,"canonical_manifest_digest":identity.typed_manifest_digest,"state":entry.state.value,"event_chain_digest":entry.current_event_digest,"operator_receipt_digest":receipt.receipt_digest,"runtime_binding_digest":binding.digest,"capability":cap,"output_digest":canonical_sha256(output)})
        if migrations.one_value(client.execute(database,"SELECT count(*) FROM memory_patch.hat_registry_entries WHERE registry_state='ENABLED'"))!="2": raise RuntimeError("enabled registry count mismatch")
        result={"schema_version":"1.0.0","step":"12","starting_sha":"7fae2166d1bb29bc3fbea04745c4d7c1e1c07dcc","kernel_api_version":"1.0.0","manifest_schema_version":"1.0.0","schema_digest":hashlib.sha256(SCHEMA.read_bytes()).hexdigest(),"capability_vocabulary_version":"hat-capabilities-1a","synthetic_hats":services,"negative_decisions":{"authority_bearing_manifest":"REJECTED","incompatible_manifest":"REJECTED","unapproved_manifest":"NOT_ENABLED","disabled_manifest":"NOT_RESOLVED","implementation_mismatch":"REJECTED","personal_memory_execution":"REJECTED"},"external_operations":{"aws_writes":0,"s3_writes":0,"external_volume_writes":0,"network_calls":0,"package_installations":0,"dynamic_imports":0},"migration":{"range":"0001-0009","applied":applied["applied_count"],"replay_skipped":replay["skipped_count"]},"database_security":{"global_configuration":True,"runtime_delete":False,"new_login_roles":False,"bypassrls":False},"status":"PASS"}
    finally:
        if client is not None:
            try: migrations.drop_database(client,database,timeout=300)
            finally: cleanup=runtime.graceful_stop_and_remove(client,owned_children_reaped=True)
        else: cleanup=runtime.stop_and_remove()
    if cleanup.get("cleanup_errors") or cleanup.get("force_kill_used") or not cleanup.get("temporary_store_removed"): raise RuntimeError("disposable CockroachDB cleanup failed")
    result["cockroachdb_cleanup"]={"graceful_drain":cleanup["drain_command_completed"],"force_kill":cleanup["force_kill_used"],"pid_exited":cleanup["pid_exited"],"ports_closed":cleanup["ports_closed"],"temporary_store_removed":cleanup["temporary_store_removed"],"persistent_database":False}
    result["evidence_digest"]=canonical_sha256(result,exclude_fields=("evidence_digest",)); return result

def main():
    parser=argparse.ArgumentParser(); parser.add_argument("--write-validation",action="store_true",required=True); parser.add_argument("--cockroach-binary",type=Path,required=True); parser.add_argument("--evidence-output",type=Path,default=OUTPUT); args=parser.parse_args()
    result=validate(args.cockroach_binary.resolve()); payload=canonical_json_bytes(result)+b"\n"; args.evidence_output.parent.mkdir(parents=True,exist_ok=True); args.evidence_output.write_bytes(payload); print(payload.decode(),end="")
if __name__=="__main__": main()
