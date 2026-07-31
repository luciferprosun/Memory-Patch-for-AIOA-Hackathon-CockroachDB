from __future__ import annotations
import dataclasses, json, unittest
from datetime import UTC, datetime
from pathlib import Path

from aioa_memory_kernel.contracts.serialization import canonical_json_bytes, canonical_sha256
from aioa_memory_kernel.hats import (
    CompatibilityDecision, HatRegistryError, HatRegistryService, RegistryState,
    ReviewActor, ReviewReceipt, RuntimeBinding, TrustedInstalledHatCatalog,
    compatibility, decode_manifest, decide_compatibility, parse_semver,
)
from tests._support import REPOSITORY_ROOT

SCHEMA = REPOSITORY_ROOT / "schemas/hat-manifest.schema.json"
FIXTURES = json.loads((REPOSITORY_ROOT / "tests/fixtures/synthetic_contract_fixtures.json").read_text())["hat_manifests"]
NOW = datetime(2031, 1, 1, tzinfo=UTC)

def raw(index=0, **changes):
    value = dict(FIXTURES[index]); value.update(changes)
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()

class SyntheticHat:
    def __init__(self, manifest): self.manifest = manifest
    def validate_manifest(self): return None
    def normalize_request(self, request): return dict(request)
    def derive_scope_requirements(self, request): return ()
    def build_retrieval_constraints(self, dimensions): return {}
    def rank_source_authority(self, source_metadata): return tuple(str(i) for i in range(len(source_metadata)))
    def extract_candidate_claims(self, draft_reference): return ()
    def detect_conflicts(self, evidence_references): return ()
    def create_correction_requirements(self, claim_references): return ()
    def create_memory_patch_proposal(self, correction_reference): return None

class ManifestTests(unittest.TestCase):
    def test_two_fixtures_decode_and_are_compatible(self):
        identities = [decode_manifest(raw(i), schema_path=SCHEMA) for i in range(2)]
        self.assertEqual([i.manifest.hat_id for i in identities], ["synthetic-software-version", "synthetic-equipment-manual"])
        self.assertTrue(all(decide_compatibility(i) is CompatibilityDecision.COMPATIBLE for i in identities))
    def test_formatting_changes_raw_not_typed_digest(self):
        a=decode_manifest(raw(),schema_path=SCHEMA); b=decode_manifest(json.dumps(FIXTURES[0],indent=2).encode(),schema_path=SCHEMA)
        self.assertNotEqual(a.raw_manifest_sha256,b.raw_manifest_sha256); self.assertEqual(a.typed_manifest_digest,b.typed_manifest_digest)
    def test_strict_decode_rejections(self):
        cases=[b'\xff',b'{"a":1,"a":2}',b'{"x":NaN}',raw(extra=True),raw(hat_version="latest")]
        for item in cases:
            with self.subTest(item=item[:20]), self.assertRaises(HatRegistryError): decode_manifest(item,schema_path=SCHEMA)
    def test_compatibility_grammar(self):
        self.assertEqual(parse_semver("1.2.3")[:3],(1,2,3)); self.assertTrue(compatibility(">=1.0.0,<2.0.0")); self.assertFalse(compatibility(">2.0.0"))
        for expression in ("latest","^1.0.0","~1.0.0","1.*","1.0.0 || 2.0.0"):
            with self.assertRaises(HatRegistryError): compatibility(expression)
    def test_unknown_and_authority_capabilities_rejected(self):
        for token in ("UNKNOWN","SHELL_EXECUTION"):
            with self.assertRaises(Exception): decode_manifest(raw(capabilities=[token]),schema_path=SCHEMA)
    def test_unsafe_nested_contract_rejected(self):
        with self.assertRaises(HatRegistryError): decode_manifest(raw(extension_points={"module_path":"bad"}),schema_path=SCHEMA)

class RegistryRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.identity=decode_manifest(raw(),schema_path=SCHEMA); self.service=HatRegistryService(clock=lambda:NOW)
        self.binding=RuntimeBinding("synthetic-software-v1","synthetic-software-version","1.0.0","SyntheticSoftwareHat","1.0.0","hat-sdk-1a","a"*64)
    def receipt(self):
        return ReviewReceipt(self.identity.manifest.hat_id,self.identity.manifest.hat_version,self.identity.typed_manifest_digest,self.identity.raw_manifest_sha256,self.identity.schema_file_sha256,CompatibilityDecision.COMPATIBLE,canonical_sha256(self.identity.manifest.capabilities),self.binding.runtime_binding_id,self.binding.implementation_digest,"ENABLE",("OPERATOR_APPROVED",),ReviewActor.TRUSTED_OPERATOR,"operator-redacted",NOW)
    def test_lifecycle_event_chain_replay_and_conflict(self):
        registered=self.service.register(self.identity,CompatibilityDecision.COMPATIBLE); self.assertEqual(registered.state,RegistryState.REGISTERED)
        self.assertIs(self.service.register(self.identity,CompatibilityDecision.COMPATIBLE),registered)
        validated=self.service.validate(*self.identity.manifest.hat_id_version) if False else self.service.validate(self.identity.manifest.hat_id,self.identity.manifest.hat_version)
        enabled=self.service.enable(self.identity.manifest.hat_id,self.identity.manifest.hat_version,self.binding,self.receipt()); self.assertEqual(enabled.state,RegistryState.ENABLED)
        chain=self.service.events(self.identity.manifest.hat_id,self.identity.manifest.hat_version); self.assertEqual([e.sequence for e in chain],[1,2,3]); self.assertEqual(chain[2].previous_event_digest,chain[1].event_digest)
        conflict=dataclasses.replace(self.identity,typed_manifest_digest="b"*64)
        with self.assertRaises(HatRegistryError): self.service.register(conflict,CompatibilityDecision.COMPATIBLE)
    def test_catalog_runtime_gate_and_recheck(self):
        self.service.register(self.identity,CompatibilityDecision.COMPATIBLE); self.service.validate(self.identity.manifest.hat_id,"1.0.0"); self.service.enable(self.identity.manifest.hat_id,"1.0.0",self.binding,self.receipt())
        catalog=TrustedInstalledHatCatalog(); catalog.register(self.binding,SyntheticHat(self.identity.manifest),self.identity.typed_manifest_digest)
        handle=self.service.resolve(self.identity.manifest.hat_id,"1.0.0",catalog); self.assertEqual(handle.invoke("REQUEST_NORMALIZATION",{"x":1}),{"x":1})
        with self.assertRaises(HatRegistryError): handle.invoke("CONFLICT_DETECTION",())
        self.service.disable(self.identity.manifest.hat_id,"1.0.0","operator")
        with self.assertRaises(HatRegistryError): handle.invoke("REQUEST_NORMALIZATION",{})
    def test_untrusted_actor_and_personal_object_rejected(self):
        with self.assertRaises((ValueError,TypeError)): dataclasses.replace(self.receipt(),actor_type="MODEL")
        catalog=TrustedInstalledHatCatalog()
        with self.assertRaises(Exception): catalog.register(self.binding,object(),self.identity.typed_manifest_digest)

class MigrationTests(unittest.TestCase):
    def test_manifest_and_global_security(self):
        manifest=json.loads((REPOSITORY_ROOT/"sql/cockroachdb/migrations/manifest.json").read_text()); self.assertEqual(manifest["migrations"][-1]["migration_id"],"0009_step12_hat_registry_runtime_boundary")
        sql=(REPOSITORY_ROOT/"sql/cockroachdb/migrations/0009_step12_hat_registry_runtime_boundary.sql").read_text()
        for token in ("hat_registry_entries","hat_registry_events","hat_runtime_bindings","append-only","REVOKE ALL","GRANT SELECT"):
            self.assertIn(token,sql)
        self.assertNotIn("BYPASSRLS",sql); self.assertNotIn("CREATE ROLE",sql); self.assertNotIn("GRANT DELETE",sql)
