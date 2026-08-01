"""Deterministic Step 15 temporal and jurisdictional normalization tests."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime
from pathlib import Path

from tests._support import SOURCE_ROOT  # noqa: F401

from aioa_memory_kernel.contracts.serialization import canonical_sha256
from aioa_memory_kernel.corpus import CorpusInventoryEngine
from aioa_memory_kernel.german_law.models import LegalJurisdiction
from aioa_memory_kernel.german_law.normalization import (
    FactVerificationStatus,
    JurisdictionNormalizationRecord,
    NormalizationEvidenceClass,
    NormalizationStatus,
    TemporalJurisdictionNormalizationEngine,
    TemporalJurisdictionNormalizationPolicy,
    TemporalJurisdictionReplayConflictError,
    TemporalJurisdictionSafetyError,
    TemporalPrecision,
    TimezoneStatus,
    _SafeSourceMetadataReader,
    _normalise_temporal_value,
    _strict_json,
    verify_temporal_jurisdiction_bundle,
)


NOW = datetime(2032, 1, 2, 3, 4, 5, tzinfo=UTC)
HEAD = "a" * 64
DEVICE = "external-volume-sha256:" + "b" * 64
DIGEST = "c" * 64


def _metadata(raw_payload: bytes, **overrides: object) -> bytes:
    value: dict[str, object] = {
        "record_id": "DE-FED-STEP15-FIXTURE",
        "document_family_id": "BJNR000000015",
        "source_sha256": hashlib.sha256(raw_payload).hexdigest(),
        "content_sha256": "d" * 64,
        "raw_source_path": "19_BULK_RAW_SOURCES/zips/demo.xml.zip",
        "normalized_source_path": "20_BULK_NORMALIZED_CORPUS/demo/",
        "source_url": "https://www.gesetze-im-internet.de/demo/xml.zip",
        "source_format": "application/zip (gii-norm XML)",
        "language": "de",
        "jurisdiction_layer": "DE_FEDERAL",
        "issuing_authority": "Bundesrepublik Deutschland",
        "source_authority_tier": "TIER_B",
        "binding_status": "consolidated_reference_text",
        "evidence_tier": "official_consolidated_service",
        "official_title": "Synthetic temporal fixture",
        "version_status": "consolidated_current",
        "version_basis": "source-declared",
        "source_retrieved_at": "2032-01-01",
        "parser_name": "gii-norm-parser",
        "parser_version": "1.1.0",
        "normalization_version": "1B",
        "provision_count": 1,
        "license_or_reuse_basis": "Declared official-work reuse fixture",
        "verification_state": "schema_validated",
        "temporal_data_limitations": "No inferred effective date.",
        "ausfertigung_datum": "",
        "promulgation_date": "",
        "publication_date": "",
        "effective_from": "",
        "effective_to": "",
        "applicable_from": "",
        "applicable_to": "",
        "adopted_at": "",
        "decision_date": "",
        "currentness_checked_at": "",
        "verified_at": "",
        "superseded_at": "",
        "repeal_date": "",
        "gii_builddate": "",
        "supersedes": [],
        "superseded_by": [],
    }
    value.update(overrides)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


class _Fixture:
    def __init__(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.derived = self.root / "derived"
        self.source.mkdir()
        self.derived.mkdir()

    def close(self) -> None:
        self.temp.cleanup()

    def populate(self, **metadata_values: object) -> Path:
        raw = self.source / "library/19_BULK_RAW_SOURCES/zips/demo.xml.zip"
        law = self.source / "library/20_BULK_NORMALIZED_CORPUS/demo/law_record.json"
        raw.parent.mkdir(parents=True)
        law.parent.mkdir(parents=True)
        payload = b"PK\x03\x04source bytes are data only"
        raw.write_bytes(payload)
        law.write_bytes(_metadata(payload, **metadata_values))
        return law

    def inventory(self) -> CorpusInventoryEngine:
        engine = CorpusInventoryEngine(
            source_root=self.source,
            bundle_parent=self.derived / "step14",
            device_reference=DEVICE,
            starting_head=HEAD,
            clock=lambda: NOW,
        )
        engine.execute(engine.plan())
        return engine

    def normalizer(self, inventory: CorpusInventoryEngine) -> TemporalJurisdictionNormalizationEngine:
        return TemporalJurisdictionNormalizationEngine(
            source_root=self.source,
            step14_bundle_root=inventory.bundle_root,
            bundle_parent=self.derived / "step15",
            device_reference=DEVICE,
            starting_head=HEAD,
            clock=lambda: NOW,
        )


class TemporalValueTests(unittest.TestCase):
    def test_supported_precision_and_timezone_forms_are_explicit(self) -> None:
        cases = {
            "2025": (TemporalPrecision.YEAR, TimezoneStatus.NOT_APPLICABLE, "2025"),
            "2025-03": (TemporalPrecision.MONTH, TimezoneStatus.NOT_APPLICABLE, "2025-03"),
            "2025-03-15": (TemporalPrecision.DATE, TimezoneStatus.NOT_APPLICABLE, "2025-03-15"),
            "15. März 2025": (TemporalPrecision.DATE, TimezoneStatus.NOT_APPLICABLE, "2025-03-15"),
            "2025-03-15T10:00:00": (TemporalPrecision.DATETIME, TimezoneStatus.UNKNOWN, "2025-03-15T10:00:00"),
            "2025-03-15T10:00:00+01:00": (TemporalPrecision.DATETIME, TimezoneStatus.EXPLICIT_OFFSET, "2025-03-15T09:00:00Z"),
            "20250315100000": (TemporalPrecision.DATETIME, TimezoneStatus.UNKNOWN, "2025-03-15T10:00:00"),
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                value = _normalise_temporal_value(raw)
                self.assertEqual((value.precision, value.timezone_status, value.normalized_value), expected)

    def test_unsupported_or_impossible_values_fail_closed(self) -> None:
        for raw in ("", "2025-02-30", "March 2025", "2025-03-15T10:00", "not-a-date", "2025-00"):
            with self.subTest(raw=raw):
                with self.assertRaises((TypeError, ValueError)):
                    _normalise_temporal_value(raw)

    def test_policy_is_immutable_and_refuses_authority(self) -> None:
        policy = TemporalJurisdictionNormalizationPolicy()
        self.assertEqual(policy.policy_digest, TemporalJurisdictionNormalizationPolicy().policy_digest)
        with self.assertRaises(FrozenInstanceError):
            policy.network_allowed = True  # type: ignore[misc]
        for field_name in (
            "source_tree_writes_allowed",
            "automatic_publication_allowed",
            "automatic_verification_allowed",
            "network_allowed",
            "model_calls_allowed",
        ):
            with self.subTest(field=field_name), self.assertRaisesRegex(ValueError, "cannot grant"):
                TemporalJurisdictionNormalizationPolicy(**{field_name: True})

    def test_strict_json_rejects_duplicate_nonfinite_nul_and_trailing_content(self) -> None:
        for payload in (b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":Infinity}', b'{"a":"x\x00"}', b'{"a":1} trailing'):
            with self.subTest(payload=payload[:12]):
                with self.assertRaises(TemporalJurisdictionSafetyError) as caught:
                    _strict_json(payload, field_name="fixture", maximum_bytes=1024)
                self.assertIn(caught.exception.sanitized_code, {"STEP15_METADATA_JSON_INVALID", "STEP15_METADATA_NUL"})


class JurisdictionModelTests(unittest.TestCase):
    def test_every_supported_state_code_requires_de_state(self) -> None:
        codes = (
            "DE-BW", "DE-BY", "DE-BE", "DE-BB", "DE-HB", "DE-HH", "DE-HE", "DE-MV",
            "DE-NI", "DE-NW", "DE-RP", "DE-SL", "DE-SN", "DE-ST", "DE-SH", "DE-TH",
        )
        record_ids: set[str] = set()
        for code in codes:
            record = JurisdictionNormalizationRecord(
                f"record-{code}", DIGEST, DIGEST, None, "doc", "version", "DE_STATE",
                LegalJurisdiction.DE_STATE, code, "fixture", NormalizationEvidenceClass.STRUCTURED_DOCUMENT_METADATA,
                FactVerificationStatus.DECLARED, NormalizationStatus.CLEAR,
            )
            record_ids.add(record.jurisdiction_record_id)
        self.assertEqual(len(record_ids), len(codes))
        with self.assertRaisesRegex(ValueError, "requires"):
            JurisdictionNormalizationRecord(
                "record", DIGEST, DIGEST, None, None, None, "DE_STATE", LegalJurisdiction.DE_STATE,
                None, "fixture", NormalizationEvidenceClass.STRUCTURED_DOCUMENT_METADATA,
                FactVerificationStatus.DECLARED, NormalizationStatus.CLEAR,
            )

    def test_language_hostname_and_path_are_not_jurisdiction_evidence(self) -> None:
        fixture = _Fixture()
        try:
            fixture.populate(language="de", jurisdiction_layer="", source_sha256="", source_url="https://example.de/DE_FEDERAL/file")
            inventory = fixture.inventory()
            engine = fixture.normalizer(inventory)
            result = engine.execute(engine.plan())
            records = [json.loads(line) for line in (fixture.derived / "step15" / result.run_id / "jurisdiction-normalization.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(records[0]["normalized_jurisdiction"], None)
            self.assertIn("JURISDICTION_UNKNOWN", records[0]["finding_codes"])
        finally:
            fixture.close()


class NormalizationEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = _Fixture()

    def tearDown(self) -> None:
        self.fixture.close()

    def _run(self, **metadata_values: object):
        self.fixture.populate(**metadata_values)
        inventory = self.fixture.inventory()
        engine = self.fixture.normalizer(inventory)
        plan = engine.plan()
        return inventory, engine, plan, engine.execute(plan)

    def test_full_run_is_deterministic_hash_bound_and_source_read_only(self) -> None:
        law = self.fixture.populate(
            publication_date="2025-03-15",
            promulgation_date="2025-03-16",
            effective_from="2025-04-01",
            effective_to="2026-04-01",
            applicable_from="2025-04-01",
            source_retrieved_at="2025-03-17T10:00:00+01:00",
            currentness_checked_at="2025-03-18",
            gii_builddate="20250318120000",
            ausfertigung_datum="2025-03-01",
        )
        before = hashlib.sha256(law.read_bytes()).hexdigest()
        inventory = self.fixture.inventory()
        engine = self.fixture.normalizer(inventory)
        plan = engine.plan()
        result = engine.execute(plan)
        after = hashlib.sha256(law.read_bytes()).hexdigest()
        replay = engine.execute(plan)
        self.assertEqual(before, after)
        self.assertEqual(result.manifest.manifest_digest, replay.manifest.manifest_digest)
        self.assertEqual(result.summary.summary_digest, replay.summary.summary_digest)
        self.assertEqual(result.verification["status"], "PASS")
        self.assertEqual(verify_temporal_jurisdiction_bundle(self.fixture.derived / "step15" / result.run_id)["status"], "PASS")
        self.assertEqual(result.summary.source_tree_writes, 0)
        self.assertEqual(result.summary.publication_transitions, 0)
        self.assertEqual(result.summary.model_calls, 0)
        facts = [json.loads(line) for line in (self.fixture.derived / "step15" / result.run_id / "temporal-normalization.jsonl").read_text(encoding="utf-8").splitlines()]
        types = {item["fact_type"] for item in facts}
        self.assertIn("PUBLISHED_AT", types)
        self.assertIn("PROMULGATED_AT", types)
        self.assertIn("EFFECTIVE_FROM", types)
        self.assertIn("CURRENTNESS_CHECKED_AT", types)
        self.assertNotIn("VERIFIED_AT", types)
        currentness = next(item for item in facts if item["fact_type"] == "CURRENTNESS_CHECKED_AT")
        self.assertIn("CURRENTNESS_CHECK_IS_NOT_AUTHENTICITY_VERIFICATION", currentness["finding_codes"])

    def test_reversed_interval_is_preserved_as_conflict_not_rewritten(self) -> None:
        _inventory, _engine, _plan, result = self._run(effective_from="2026-01-01", effective_to="2025-01-01")
        conflicts = [json.loads(line) for line in (self.fixture.derived / "step15" / result.run_id / "normalization-conflicts.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertIn("EFFECTIVE_INTERVAL_INVALID", {item["conflict_type"] for item in conflicts})
        facts = [json.loads(line) for line in (self.fixture.derived / "step15" / result.run_id / "temporal-normalization.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual({item["value"]["normalized_value"] for item in facts}, {"2026-01-01", "2025-01-01", "2032-01-01"})

    def test_step14_source_mutation_fails_before_external_writes(self) -> None:
        law = self.fixture.populate()
        inventory = self.fixture.inventory()
        law.write_bytes(law.read_bytes() + b"\n")
        engine = self.fixture.normalizer(inventory)
        with self.assertRaises(TemporalJurisdictionReplayConflictError) as caught:
            engine.plan()
        self.assertEqual(caught.exception.sanitized_code, "STEP15_SOURCE_TREE_CHANGED")
        self.assertFalse((self.fixture.derived / "step15").exists())

    def test_hash_bound_reader_rejects_symlink_escape_and_digest_change(self) -> None:
        root = self.fixture.source
        target = root / "inside.json"
        target.write_text("{}", encoding="utf-8")
        os.symlink(target.name, root / "linked.json")
        reader = _SafeSourceMetadataReader(root, maximum_bytes=4096, chunk_bytes=4096)
        with self.assertRaises(TemporalJurisdictionSafetyError):
            reader.read("linked.json", expected_sha256=hashlib.sha256(target.read_bytes()).hexdigest())
        with self.assertRaises(TemporalJurisdictionReplayConflictError) as caught:
            reader.read("inside.json", expected_sha256="0" * 64)
        self.assertEqual(caught.exception.sanitized_code, "STEP15_SOURCE_DIGEST_MISMATCH")

    def test_unsupported_metadata_is_logically_reviewed_without_executing_content(self) -> None:
        self.fixture.populate(effective_from="2025-02-30")
        inventory = self.fixture.inventory()
        engine = self.fixture.normalizer(inventory)
        result = engine.execute(engine.plan())
        conflicts = [json.loads(line) for line in (self.fixture.derived / "step15" / result.run_id / "normalization-conflicts.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertIn("INVALID_TEMPORAL_VALUE", {item["conflict_type"] for item in conflicts})
        self.assertFalse(any("exec(" in json.dumps(item) for item in conflicts))

    def test_oversized_optional_version_marker_preserves_valid_temporal_facts(self) -> None:
        _inventory, _engine, _plan, result = self._run(
            version_basis="x" * 513,
            publication_date="2025-01-01",
        )
        bundle = self.fixture.derived / "step15" / result.run_id
        versions = [json.loads(line) for line in (bundle / "document-versions.jsonl").read_text(encoding="utf-8").splitlines()]
        facts = [json.loads(line) for line in (bundle / "temporal-normalization.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(versions), 1)
        self.assertIsNone(versions[0]["version_basis"])
        self.assertIn("VERSION_BASIS_FIELD_INVALID", versions[0]["finding_codes"])
        self.assertIn("PUBLISHED_AT", {item["fact_type"] for item in facts})

    def test_step14_quarantine_prevents_metadata_normalization_without_a_source_write(self) -> None:
        law = self.fixture.populate(
            private_note="aws_secret_access_key=REDACTED_TEST_MARKER_NOT_A_CREDENTIAL"
        )
        before = hashlib.sha256(law.read_bytes()).hexdigest()
        inventory = self.fixture.inventory()
        engine = self.fixture.normalizer(inventory)
        result = engine.execute(engine.plan())
        self.assertEqual(hashlib.sha256(law.read_bytes()).hexdigest(), before)
        self.assertEqual(result.summary.metadata_records_quarantined, 1)
        self.assertEqual(result.summary.records_normalized, 1)
        conflicts = [json.loads(line) for line in (self.fixture.derived / "step15" / result.run_id / "normalization-conflicts.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertIn("STEP14_QUARANTINED_INPUT", {item["conflict_type"] for item in conflicts})
        self.assertEqual((self.fixture.derived / "step15" / result.run_id / "temporal-normalization.jsonl").read_text(encoding="utf-8"), "")

    def test_same_legal_version_key_with_different_bytes_is_a_conflict(self) -> None:
        self.fixture.populate(publication_date="2025-01-01")
        raw = self.fixture.source / "library/19_BULK_RAW_SOURCES/zips/demo-two.xml.zip"
        law = self.fixture.source / "library/20_BULK_NORMALIZED_CORPUS/demo-two/law_record.json"
        raw.write_bytes(b"PK\x03\x04second exact source bytes")
        law.parent.mkdir(parents=True)
        law.write_bytes(_metadata(raw.read_bytes(), publication_date="2026-01-01"))
        inventory = self.fixture.inventory()
        engine = self.fixture.normalizer(inventory)
        result = engine.execute(engine.plan())
        conflicts = [json.loads(line) for line in (self.fixture.derived / "step15" / result.run_id / "normalization-conflicts.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertIn("SAME_VERSION_ID_DIFFERENT_RAW_CONTENT", {item["conflict_type"] for item in conflicts})
        versions = [json.loads(line) for line in (self.fixture.derived / "step15" / result.run_id / "document-versions.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(versions), 2)
        self.assertEqual(len({item["version_key_identity"] for item in versions}), 1)
        self.assertEqual(len({item["version_identity"] for item in versions}), 2)

    def test_near_duplicate_is_not_a_version_or_supersession_proof(self) -> None:
        _inventory, _engine, _plan, result = self._run()
        versions = [json.loads(line) for line in (self.fixture.derived / "step15" / result.run_id / "document-versions.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(versions), 1)
        relationships = (self.fixture.derived / "step15" / result.run_id / "supersession-candidates.jsonl").read_text(encoding="utf-8")
        self.assertNotIn("DECLARED_SUPERSEDES", relationships)
        self.assertNotIn("NEAR_DUPLICATE_REVIEW_ONLY", relationships)

    def test_policy_change_changes_run_identity_and_cannot_resume_old_checkpoint(self) -> None:
        self.fixture.populate()
        inventory = self.fixture.inventory()
        first = self.fixture.normalizer(inventory)
        first_plan = first.plan()
        first.execute(first_plan)
        changed = TemporalJurisdictionNormalizationEngine(
            source_root=self.fixture.source,
            step14_bundle_root=inventory.bundle_root,
            bundle_parent=self.fixture.derived / "step15",
            device_reference=DEVICE,
            starting_head=HEAD,
            policy=replace(TemporalJurisdictionNormalizationPolicy(), checkpoint_batch_size=64),
            clock=lambda: NOW,
        )
        self.assertNotEqual(first_plan.policy_digest, changed.plan().policy_digest)
        self.assertNotEqual(first_plan.run_id, changed.plan().run_id)

    def test_version_identity_does_not_depend_on_operational_completed_at(self) -> None:
        _inventory, _engine, _plan, result = self._run(publication_date="2025-01-01")
        bundle = self.fixture.derived / "step15" / result.run_id
        version = json.loads((bundle / "document-versions.jsonl").read_text(encoding="utf-8").splitlines()[0])
        run = json.loads((bundle / "artifact-manifest.json").read_text(encoding="utf-8"))["run"]
        self.assertNotIn(run["completed_at"], canonical_sha256(version))
        self.assertTrue(version["version_identity"].startswith("legal-version-"))
