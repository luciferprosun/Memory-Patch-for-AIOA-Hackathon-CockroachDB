"""Deterministic Step 14 inventory and filesystem-safety tests."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import socket
import tempfile
import unittest
from unittest import mock
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime
from pathlib import Path

from tests._support import SOURCE_ROOT  # noqa: F401

from aioa_memory_kernel.corpus import (
    CorpusInventoryEngine,
    CorpusInventoryPolicy,
    CorpusReplayConflictError,
    CorpusSafetyError,
    FileKind,
    InventoryLicenseStatus,
    InventoryPrivacyStatus,
    LicenseAssessment,
    ParserSupportStatus,
    PrivacyAssessment,
    QuarantineDecision,
    QuarantineReason,
    QuarantineStatus,
    RegistrationDisposition,
    SourceRegistrationCandidate,
    verify_inventory_bundle,
)
from aioa_memory_kernel.corpus import inventory as inventory_module
from aioa_memory_kernel.contracts.serialization import canonical_sha256
from aioa_memory_kernel.german_law import (
    build_source_registry_record,
    registration_operation_identity,
)
from aioa_memory_kernel.sources import SourcePublicationState


REGISTRATION_SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts/run_german_law_corpus_registration_validation.py"
)
REGISTRATION_SPEC = importlib.util.spec_from_file_location(
    "step14_registration_validation_test",
    REGISTRATION_SCRIPT,
)
assert REGISTRATION_SPEC is not None and REGISTRATION_SPEC.loader is not None
REGISTRATION_MODULE = importlib.util.module_from_spec(REGISTRATION_SPEC)
REGISTRATION_SPEC.loader.exec_module(REGISTRATION_MODULE)


NOW = datetime(2032, 1, 2, 3, 4, 5, tzinfo=UTC)
HEAD = "a" * 64
DEVICE = "external-volume-sha256:" + "b" * 64


def _law_record(raw_payload: bytes) -> bytes:
    value = {
        "record_id": "DE-FED-DEMO-LAW",
        "document_family_id": "BJNR000000001",
        "source_sha256": hashlib.sha256(raw_payload).hexdigest(),
        "content_sha256": "c" * 64,
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
        "official_title": "Synthetic public legal fixture",
        "short_title": "Demo",
        "abbreviation": "DemoG",
        "fna_identifier": "",
        "eli_identifier": "",
        "official_citation": "Synthetic citation",
        "version_status": "consolidated_current",
        "currentness_status": "not_checked",
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
        "repeal_date": "",
    }
    return json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")


class InventoryFixture:
    def __init__(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.bundle_parent = self.root / "derived"
        self.source.mkdir()
        self.bundle_parent.mkdir()

    def close(self) -> None:
        self.temp.cleanup()

    def populate(self) -> None:
        library = self.source / "library"
        raw = library / "19_BULK_RAW_SOURCES/zips"
        normalized = library / "20_BULK_NORMALIZED_CORPUS/demo"
        raw.mkdir(parents=True)
        normalized.mkdir(parents=True)
        raw_payload = b"PK\x03\x04synthetic-not-executed"
        (raw / "demo.xml.zip").write_bytes(raw_payload)
        (normalized / "law_record.json").write_bytes(_law_record(raw_payload))
        (library / "14_LICENSE_AND_REUSE_LEDGER").mkdir()
        (library / "14_LICENSE_AND_REUSE_LEDGER/README.txt").write_text(
            "Declared license evidence.\n", encoding="utf-8"
        )
        (self.source / "same-a.txt").write_text("Alpha beta gamma delta epsilon.\n", encoding="utf-8")
        (self.source / "same-b.txt").write_text("Alpha beta gamma delta epsilon.\n", encoding="utf-8")
        (self.source / "near.txt").write_text("Alpha beta gamma delta epsilon zeta.\n", encoding="utf-8")
        (self.source / "binary.bin").write_bytes(bytes(range(64)))
        (self.source / "empty.bin").write_bytes(b"")

    def engine(self, **policy_values: int) -> CorpusInventoryEngine:
        policy = CorpusInventoryPolicy(**policy_values)
        return CorpusInventoryEngine(
            source_root=self.source,
            bundle_parent=self.bundle_parent,
            device_reference=DEVICE,
            starting_head=HEAD,
            policy=policy,
            clock=lambda: NOW,
        )


class CorpusInventoryModelTests(unittest.TestCase):
    def test_policy_is_immutable_and_digest_is_deterministic(self) -> None:
        first = CorpusInventoryPolicy()
        second = CorpusInventoryPolicy()
        self.assertEqual(first.policy_digest, second.policy_digest)
        with self.assertRaises(FrozenInstanceError):
            first.hash_chunk_bytes = 1  # type: ignore[misc]

    def test_policy_forbids_source_writes_and_publication(self) -> None:
        with self.assertRaisesRegex(ValueError, "cannot write"):
            CorpusInventoryPolicy(source_tree_writes_allowed=True)
        with self.assertRaisesRegex(ValueError, "cannot write"):
            CorpusInventoryPolicy(automatic_publication_allowed=True)

    def test_assessments_never_store_raw_excerpt(self) -> None:
        location = canonical_sha256({"path": "fixture", "offset": 2})
        privacy = PrivacyAssessment(
            InventoryPrivacyStatus.POTENTIALLY_SENSITIVE,
            ("EMAIL_ADDRESS_SIGNAL",),
            1,
            (location,),
        )
        license_value = LicenseAssessment(
            InventoryLicenseStatus.UNKNOWN,
            ("NO_FILE_BOUND_LICENSE_EVIDENCE",),
        )
        self.assertNotIn("@", privacy.assessment_digest)
        self.assertRegex(license_value.assessment_digest, r"^[0-9a-f]{64}$")

    def test_clear_quarantine_cannot_carry_reason(self) -> None:
        with self.assertRaisesRegex(ValueError, "clear"):
            QuarantineDecision(
                QuarantineStatus.CLEAR,
                (QuarantineReason.UNSUPPORTED_FORMAT,),
            )

    def test_review_or_quarantine_requires_reason(self) -> None:
        for status in (QuarantineStatus.REVIEW_REQUIRED, QuarantineStatus.QUARANTINED):
            with self.subTest(status=status), self.assertRaisesRegex(ValueError, "needs"):
                QuarantineDecision(status, ())

    def test_every_license_status_is_explicit_and_digest_bound(self) -> None:
        values = {
            status: LicenseAssessment(status, (f"RULE_{status.value}",)).assessment_digest
            for status in InventoryLicenseStatus
        }
        self.assertEqual(set(values), set(InventoryLicenseStatus))
        self.assertEqual(len(set(values.values())), len(InventoryLicenseStatus))

    def test_every_privacy_status_is_explicit_and_digest_bound(self) -> None:
        values = {
            status: PrivacyAssessment(status, (f"RULE_{status.value}",), 0).assessment_digest
            for status in InventoryPrivacyStatus
        }
        self.assertEqual(set(values), set(InventoryPrivacyStatus))
        self.assertEqual(len(set(values.values())), len(InventoryPrivacyStatus))

    def test_every_quarantine_reason_is_representable_without_source_action(self) -> None:
        decisions = {
            reason: QuarantineDecision(QuarantineStatus.QUARANTINED, (reason,))
            for reason in QuarantineReason
        }
        self.assertEqual(set(decisions), set(QuarantineReason))
        self.assertTrue(all(value.status is QuarantineStatus.QUARANTINED for value in decisions.values()))

    def test_inventory_run_accepts_sha1_git_object_id_but_not_arbitrary_text(self) -> None:
        from aioa_memory_kernel.corpus import CorpusInventoryRun

        valid = CorpusInventoryRun(
            "step14-fixture",
            "a" * 40,
            "b" * 64,
            DEVICE,
            "c" * 64,
            NOW,
            NOW,
            0,
            "d" * 64,
            "d" * 64,
        )
        self.assertEqual(valid.starting_head, "a" * 40)
        with self.assertRaisesRegex(ValueError, "Git object"):
            replace(valid, starting_head="not-a-git-object")


class CorpusInventoryEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = InventoryFixture()

    def tearDown(self) -> None:
        self.fixture.close()

    def test_plan_is_read_only_and_deterministic(self) -> None:
        self.fixture.populate()
        engine = self.fixture.engine()
        before = sorted(path.relative_to(self.fixture.source).as_posix() for path in self.fixture.source.rglob("*"))
        first = engine.plan()
        second = engine.plan()
        after = sorted(path.relative_to(self.fixture.source).as_posix() for path in self.fixture.source.rglob("*"))
        self.assertEqual(first, second)
        self.assertEqual(before, after)
        self.assertEqual(first.regular_files, 8)
        self.assertFalse(engine.bundle_root.exists())

    def test_full_bundle_is_deterministic_and_digest_verified(self) -> None:
        self.fixture.populate()
        engine = self.fixture.engine(near_similarity_threshold_millionths=500_000)
        plan = engine.plan()
        source_before = engine.tree_fingerprint()
        summary, manifest = engine.execute(plan)
        source_after = engine.tree_fingerprint()
        verification = verify_inventory_bundle(engine.bundle_root)
        self.assertEqual(source_before, source_after)
        self.assertEqual(summary.source_tree_writes, 0)
        self.assertEqual(summary.source_files_modified, 0)
        self.assertGreaterEqual(summary.exact_duplicate_group_count, 1)
        self.assertGreaterEqual(summary.exact_duplicate_member_count, 2)
        self.assertEqual(summary.registration_candidate_count, 1)
        self.assertEqual(summary.registration_conflict_count, 0)
        self.assertEqual(verification["manifest_digest"], manifest.manifest_digest)
        self.assertFalse(list(engine.bundle_root.rglob("*.part")))
        self.assertFalse(list(engine.bundle_root.rglob("*.sqlite3")))

    def test_exact_duplicates_preserve_both_aliases(self) -> None:
        self.fixture.populate()
        engine = self.fixture.engine()
        engine.execute(engine.plan())
        groups = [json.loads(line) for line in (engine.bundle_root / "exact-duplicate-groups.jsonl").read_text().splitlines()]
        aliases = [json.loads(line) for line in (engine.bundle_root / "path-aliases.jsonl").read_text().splitlines()]
        target = [group for group in groups if group["byte_size"] == len("Alpha beta gamma delta epsilon.\n".encode())]
        self.assertEqual(len(target), 1)
        self.assertEqual(len(target[0]["member_record_ids"]), 2)
        self.assertEqual(sum(alias["byte_size"] == target[0]["byte_size"] for alias in aliases), 2)

    def test_same_size_different_bytes_are_not_exact_duplicates(self) -> None:
        (self.fixture.source / "a.bin").write_bytes(b"abc")
        (self.fixture.source / "b.bin").write_bytes(b"abd")
        engine = self.fixture.engine()
        summary, _ = engine.execute(engine.plan())
        self.assertEqual(summary.exact_duplicate_group_count, 0)

    def test_empty_and_binary_files_are_hashed_without_execution(self) -> None:
        self.fixture.populate()
        engine = self.fixture.engine()
        engine.execute(engine.plan())
        records = [json.loads(line) for line in (engine.bundle_root / "file-records.jsonl").read_text().splitlines()]
        empty = next(item for item in records if item["relative_path"] == "empty.bin")
        binary = next(item for item in records if item["relative_path"] == "binary.bin")
        self.assertEqual(empty["raw_sha256"], hashlib.sha256(b"").hexdigest())
        self.assertEqual(binary["parser_support_status"], ParserSupportStatus.UNSUPPORTED_FORMAT.value)

    def test_large_file_is_streamed_with_bounded_hash_chunks(self) -> None:
        payload = (b"0123456789abcdef" * (192 * 1024)) + b"tail"
        target = self.fixture.source / "large.bin"
        target.write_bytes(payload)
        engine = self.fixture.engine(hash_chunk_bytes=4096)
        summary, _ = engine.execute(engine.plan())
        records = [json.loads(line) for line in (engine.bundle_root / "file-records.jsonl").read_text().splitlines()]
        record = records[0]
        self.assertEqual(summary.bytes_observed, len(payload))
        self.assertEqual(record["raw_sha256"], hashlib.sha256(payload).hexdigest())
        self.assertEqual(record["byte_size"], len(payload))

    def test_same_bytes_at_different_paths_have_distinct_object_ids(self) -> None:
        (self.fixture.source / "a.txt").write_text("same", encoding="utf-8")
        (self.fixture.source / "b.txt").write_text("same", encoding="utf-8")
        engine = self.fixture.engine()
        engine.execute(engine.plan())
        records = [json.loads(line) for line in (engine.bundle_root / "file-records.jsonl").read_text().splitlines()]
        self.assertEqual(records[0]["raw_sha256"], records[1]["raw_sha256"])
        self.assertNotEqual(records[0]["record_id"], records[1]["record_id"])
        self.assertNotEqual(records[0]["path_digest"], records[1]["path_digest"])

    def test_step11_json_normalization_finds_formatting_only_duplicate(self) -> None:
        (self.fixture.source / "a.json").write_text('{"b":1,"a":2}', encoding="utf-8")
        (self.fixture.source / "b.json").write_text('{\n  "a": 2,\n  "b": 1\n}', encoding="utf-8")
        engine = self.fixture.engine()
        summary, _ = engine.execute(engine.plan())
        groups = [json.loads(line) for line in (engine.bundle_root / "normalized-duplicate-groups.jsonl").read_text().splitlines()]
        self.assertEqual(summary.normalized_duplicate_group_count, 1)
        self.assertEqual(len(groups[0]["member_record_ids"]), 2)
        self.assertEqual(len(set(groups[0]["raw_sha256_values"])), 2)

    def test_unsupported_xml_never_claims_normalized_equivalence(self) -> None:
        (self.fixture.source / "a.xml").write_text("<a>1</a>", encoding="utf-8")
        (self.fixture.source / "b.xml").write_text("<a> 1 </a>", encoding="utf-8")
        engine = self.fixture.engine()
        summary, _ = engine.execute(engine.plan())
        self.assertEqual(summary.normalized_duplicate_group_count, 0)
        records = [json.loads(line) for line in (engine.bundle_root / "file-records.jsonl").read_text().splitlines()]
        self.assertTrue(all(row["normalized_sha256"] is None for row in records))

    def test_malformed_utf8_and_json_fail_closed(self) -> None:
        (self.fixture.source / "bad.txt").write_bytes(b"\xff")
        (self.fixture.source / "bad.json").write_bytes(b'{"x":1,"x":2}')
        engine = self.fixture.engine()
        engine.execute(engine.plan())
        records = [json.loads(line) for line in (engine.bundle_root / "file-records.jsonl").read_text().splitlines()]
        for name in ("bad.txt", "bad.json"):
            item = next(record for record in records if record["relative_path"] == name)
            self.assertEqual(item["parser_support_status"], ParserSupportStatus.MALFORMED_SUPPORTED_FORMAT.value)

    def test_step11_resource_limit_is_recorded_without_aborting_inventory(self) -> None:
        (self.fixture.source / "deep.json").write_text(
            "[" * 70 + "0" + "]" * 70,
            encoding="utf-8",
        )
        engine = self.fixture.engine()
        summary, _ = engine.execute(engine.plan())
        records = [json.loads(line) for line in (engine.bundle_root / "file-records.jsonl").read_text().splitlines()]
        item = next(record for record in records if record["relative_path"] == "deep.json")
        self.assertEqual(summary.objects_observed, 1)
        self.assertEqual(item["parser_support_status"], ParserSupportStatus.MALFORMED_SUPPORTED_FORMAT.value)
        self.assertIn("JSON_DEPTH_LIMIT", item["findings"])

    def test_symlink_is_recorded_and_never_followed(self) -> None:
        outside = self.fixture.root / "outside.txt"
        outside.write_text("secret outside", encoding="utf-8")
        (self.fixture.source / "outside-link").symlink_to(outside)
        engine = self.fixture.engine()
        summary, _ = engine.execute(engine.plan())
        records = [json.loads(line) for line in (engine.bundle_root / "file-records.jsonl").read_text().splitlines()]
        link = next(item for item in records if item["relative_path"] == "outside-link")
        self.assertEqual(summary.symlink_count, 1)
        self.assertEqual(link["file_kind"], FileKind.SYMLINK.value)
        self.assertIsNone(link["raw_sha256"])

    def test_symlink_inside_root_is_also_evidence_not_content(self) -> None:
        target = self.fixture.source / "target.txt"
        target.write_text("inside", encoding="utf-8")
        (self.fixture.source / "inside-link").symlink_to(target.name)
        engine = self.fixture.engine()
        summary, _ = engine.execute(engine.plan())
        self.assertEqual(summary.symlink_count, 1)
        records = [json.loads(line) for line in (engine.bundle_root / "file-records.jsonl").read_text().splitlines()]
        link = next(row for row in records if row["relative_path"] == "inside-link")
        self.assertIsNone(link["raw_sha256"])
        self.assertIn("SYMLINK_NOT_FOLLOWED", link["findings"])

    def test_symlinked_source_root_ancestor_is_rejected(self) -> None:
        real_parent = self.fixture.root / "real-source-parent"
        real_source = real_parent / "source"
        real_source.mkdir(parents=True)
        (real_source / "data.txt").write_text("bounded", encoding="utf-8")
        alias_parent = self.fixture.root / "source-parent-alias"
        os.symlink(real_parent, alias_parent)
        with self.assertRaisesRegex(CorpusSafetyError, "symlinked"):
            CorpusInventoryEngine(
                source_root=alias_parent / "source",
                bundle_parent=self.fixture.bundle_parent,
                device_reference=DEVICE,
                starting_head=HEAD,
                clock=lambda: NOW,
            )

    def test_broken_symlink_is_recorded(self) -> None:
        (self.fixture.source / "broken").symlink_to("missing")
        engine = self.fixture.engine()
        summary, _ = engine.execute(engine.plan())
        self.assertEqual(summary.symlink_count, 1)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO unsupported")
    def test_special_file_is_not_opened(self) -> None:
        os.mkfifo(self.fixture.source / "pipe")
        engine = self.fixture.engine()
        summary, _ = engine.execute(engine.plan())
        self.assertEqual(summary.special_count, 1)

    def test_source_mutation_after_plan_invalidates_execution(self) -> None:
        (self.fixture.source / "item.txt").write_text("one", encoding="utf-8")
        engine = self.fixture.engine()
        plan = engine.plan()
        (self.fixture.source / "item.txt").write_text("two", encoding="utf-8")
        with self.assertRaisesRegex(CorpusReplayConflictError, "changed"):
            engine.execute(plan)

    def test_mutation_during_hash_is_detected_before_digest_is_trusted(self) -> None:
        target = self.fixture.source / "mutable.bin"
        target.write_bytes(b"a" * 8192)
        engine = self.fixture.engine(hash_chunk_bytes=4096)
        expected = target.lstat()
        original_read = os.read
        mutated = False

        def changing_read(descriptor: int, size: int) -> bytes:
            nonlocal mutated
            chunk = original_read(descriptor, size)
            if chunk and not mutated:
                mutated = True
                target.write_bytes(b"b" * 8192)
            return chunk

        with mock.patch("os.read", changing_read):
            digest, _payload, _prefix, stability, findings = engine._hash_regular(
                target, "mutable.bin", expected
            )
        self.assertIsNone(digest)
        self.assertEqual(stability.value, "UNSTABLE_DURING_HASH")
        self.assertIn("UNSTABLE_DURING_HASH", findings)

    def test_unreadable_open_is_recorded_without_aborting_other_files(self) -> None:
        blocked = self.fixture.source / "blocked.bin"
        blocked.write_bytes(b"blocked")
        good = self.fixture.source / "good.bin"
        good.write_bytes(b"good")
        engine = self.fixture.engine()
        plan = engine.plan()
        original_open = os.open

        def guarded_open(path: object, flags: int, mode: int = 0o777) -> int:
            if Path(path) == blocked:
                raise PermissionError("synthetic unreadable fixture")
            return original_open(path, flags, mode)

        with mock.patch("os.open", guarded_open):
            summary, _ = engine.execute(plan)
        self.assertEqual(summary.objects_observed, 2)
        records = [json.loads(line) for line in (engine.bundle_root / "file-records.jsonl").read_text().splitlines()]
        denied = next(row for row in records if row["relative_path"] == "blocked.bin")
        self.assertIsNone(denied["raw_sha256"])
        self.assertIn("UNREADABLE_FILE", denied["findings"])

    def test_root_relative_guard_rejects_escape(self) -> None:
        with self.assertRaisesRegex(CorpusSafetyError, "escaped"):
            inventory_module._safe_relative(
                self.fixture.root / "outside.bin",
                self.fixture.source,
            )

    def test_interrupted_scan_resumes_without_duplicate_records(self) -> None:
        self.fixture.populate()
        calls = 0

        def interrupt(_values: object) -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("synthetic interruption")

        policy = CorpusInventoryPolicy(checkpoint_batch_size=1)
        first = CorpusInventoryEngine(
            source_root=self.fixture.source,
            bundle_parent=self.fixture.bundle_parent,
            device_reference=DEVICE,
            starting_head=HEAD,
            policy=policy,
            clock=lambda: NOW,
            progress=interrupt,
        )
        plan = first.plan()
        with self.assertRaisesRegex(RuntimeError, "interruption"):
            first.execute(plan)
        resumed = CorpusInventoryEngine(
            source_root=self.fixture.source,
            bundle_parent=self.fixture.bundle_parent,
            device_reference=DEVICE,
            starting_head=HEAD,
            policy=policy,
            clock=lambda: NOW,
        )
        summary, manifest = resumed.execute(plan)
        self.assertEqual(summary.objects_observed, 8)
        self.assertEqual(manifest.run.resume_count, 1)
        records = (resumed.bundle_root / "file-records.jsonl").read_text().splitlines()
        self.assertEqual(len(records), 8)

    def test_incompatible_checkpoint_is_rejected(self) -> None:
        self.fixture.populate()
        policy = CorpusInventoryPolicy(checkpoint_batch_size=1)
        first = CorpusInventoryEngine(
            source_root=self.fixture.source,
            bundle_parent=self.fixture.bundle_parent,
            device_reference=DEVICE,
            starting_head=HEAD,
            policy=policy,
            clock=lambda: NOW,
            progress=lambda _values: (_ for _ in ()).throw(RuntimeError("stop")),
        )
        plan = first.plan()
        with self.assertRaises(RuntimeError):
            first.execute(plan)
        state = first.bundle_root / "checkpoints/inventory-state.sqlite3"
        import sqlite3

        with sqlite3.connect(state) as connection:
            connection.execute("UPDATE metadata SET value='wrong' WHERE key='policy_digest'")
            connection.commit()
        with self.assertRaisesRegex(CorpusReplayConflictError, "different immutable facts"):
            first.execute(plan)

    def test_completed_bundle_refuses_overwrite(self) -> None:
        self.fixture.populate()
        engine = self.fixture.engine()
        plan = engine.plan()
        engine.execute(plan)
        with self.assertRaisesRegex(CorpusReplayConflictError, "already exists"):
            engine.execute(plan)

    def test_atomic_publish_race_never_overwrites_existing_target(self) -> None:
        target = self.fixture.bundle_parent / "race.json"
        real_link = inventory_module.os.link

        def create_racing_target(
            source: object,
            destination: object,
            **kwargs: object,
        ) -> None:
            Path(destination).write_bytes(b"racing-writer")
            real_link(source, destination, **kwargs)

        with mock.patch.object(
            inventory_module.os,
            "link",
            side_effect=create_racing_target,
        ):
            with self.assertRaises(CorpusReplayConflictError):
                CorpusInventoryEngine._write_atomic_absent(
                    target,
                    b"step14-output",
                )
        self.assertEqual(target.read_bytes(), b"racing-writer")
        self.assertEqual(list(self.fixture.bundle_parent.glob("*.part")), [])

    def test_checkpoint_symlink_cannot_escape_derived_root(self) -> None:
        self.fixture.populate()
        engine = self.fixture.engine()
        plan = engine.plan()
        engine.bundle_root.mkdir()
        outside = self.fixture.root / "outside-checkpoint-target"
        outside.mkdir()
        os.symlink(outside, engine.bundle_root / "checkpoints")
        with self.assertRaisesRegex(CorpusSafetyError, "checkpoint"):
            engine.execute(plan)
        self.assertEqual(list(outside.iterdir()), [])

    def test_checkpoint_state_symlink_cannot_overwrite_external_file(self) -> None:
        self.fixture.populate()
        engine = self.fixture.engine()
        plan = engine.plan()
        checkpoints = engine.bundle_root / "checkpoints"
        checkpoints.mkdir(parents=True)
        outside = self.fixture.root / "outside-state.sqlite3"
        outside.write_bytes(b"do-not-change")
        os.symlink(outside, checkpoints / "inventory-state.sqlite3")
        with self.assertRaisesRegex(CorpusSafetyError, "checkpoint state"):
            engine.execute(plan)
        self.assertEqual(outside.read_bytes(), b"do-not-change")

    def test_bundle_tampering_is_detected(self) -> None:
        self.fixture.populate()
        engine = self.fixture.engine()
        engine.execute(engine.plan())
        with (engine.bundle_root / "file-records.jsonl").open("ab") as stream:
            stream.write(b"{}\n")
        with self.assertRaisesRegex(CorpusReplayConflictError, "digest"):
            verify_inventory_bundle(engine.bundle_root)

    def test_near_candidates_are_review_only(self) -> None:
        self.fixture.populate()
        engine = self.fixture.engine(near_similarity_threshold_millionths=300_000)
        summary, _ = engine.execute(engine.plan())
        candidates = [json.loads(line) for line in (engine.bundle_root / "near-duplicate-candidates.jsonl").read_text().splitlines()]
        self.assertGreaterEqual(summary.near_duplicate_candidate_count, 1)
        self.assertTrue(all(item["human_review_required"] for item in candidates))

    def test_secret_signal_uses_digest_not_excerpt(self) -> None:
        (self.fixture.source / "config.txt").write_text("password=not-a-real-fixture-secret", encoding="utf-8")
        engine = self.fixture.engine()
        engine.execute(engine.plan())
        evidence = (engine.bundle_root / "privacy-assessments.jsonl").read_text()
        self.assertNotIn("not-a-real-fixture-secret", evidence)
        self.assertIn("PASSWORD_FIELD_SIGNAL", evidence)

    def test_source_and_bundle_must_share_verified_filesystem(self) -> None:
        # The fail-closed branch is exercised with an injected metadata mismatch.
        self.fixture.populate()
        engine = self.fixture.engine()
        original = Path.lstat

        def changed(path: Path):
            value = original(path)
            if path == self.fixture.bundle_parent:
                values = list(value)
                values[2] = value.st_dev + 1
                return os.stat_result(values)
            return value

        with mock.patch.object(Path, "lstat", changed):
            with self.assertRaises(CorpusSafetyError):
                self.fixture.engine()


class CorpusRegistrationMappingTests(unittest.TestCase):
    def candidate(self, **changes: object) -> SourceRegistrationCandidate:
        value = SourceRegistrationCandidate(
            "step14-fixture",
            "de-federal-gii-bjnr000000001",
            "a" * 64,
            "b" * 64,
            "gii:BJNR000000001",
            "german-law-global-1a",
            "DE_FEDERAL_OFFICIAL_CONSOLIDATED_LAW",
            "AUTHORITATIVE_SECONDARY",
            "CONFIRMED_PERMISSIVE",
            "PUBLIC",
            "NOT_REQUIRED",
            "DE_FEDERAL",
            "de",
            "BJNR000000001",
            ("c" * 64,),
            ParserSupportStatus.STEP11_SUPPORTED,
            RegistrationDisposition.READY_FOR_REGISTRATION,
            ("STEP9_PUBLICATION_BOUNDARY_PRESERVED",),
        )
        return replace(value, **changes)

    def test_step9_record_starts_at_exact_unpublished_genesis(self) -> None:
        candidate = self.candidate()
        record = build_source_registry_record(candidate, created_at=NOW)
        self.assertEqual(record.current_publication_state, SourcePublicationState.REGISTERED)
        self.assertEqual(record.current_publication_sequence, 0)
        self.assertIsNone(record.knowledge_version_id)
        self.assertEqual(record.scope.jurisdiction, "DE_FEDERAL")
        self.assertEqual(
            record.scope.additional_dimensions["candidate_digest"],
            candidate.candidate_digest,
        )
        self.assertEqual(
            record.scope.additional_dimensions["provenance_aliases_digest"],
            canonical_sha256(candidate.provenance_alias_digests),
        )

    def test_language_never_infers_missing_jurisdiction(self) -> None:
        with self.assertRaisesRegex(ValueError, "infer"):
            build_source_registry_record(self.candidate(jurisdiction=None), created_at=NOW)

    def test_wrong_step13_source_class_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "source class"):
            build_source_registry_record(self.candidate(source_class="UNKNOWN_LEGAL_SOURCE"), created_at=NOW)

    def test_duplicate_alias_is_not_registered(self) -> None:
        with self.assertRaisesRegex(ValueError, "does not permit"):
            build_source_registry_record(self.candidate(disposition=RegistrationDisposition.DUPLICATE_ALIAS), created_at=NOW)

    def test_review_and_quarantine_candidates_remain_unpublished(self) -> None:
        for disposition in (
            RegistrationDisposition.REVIEW_REQUIRED,
            RegistrationDisposition.QUARANTINED,
        ):
            with self.subTest(disposition=disposition):
                record = build_source_registry_record(
                    self.candidate(disposition=disposition), created_at=NOW
                )
                self.assertEqual(
                    record.current_publication_state,
                    SourcePublicationState.REGISTERED,
                )

    def test_registration_identity_is_exactly_replayable_and_conflict_bound(self) -> None:
        first = registration_operation_identity(self.candidate())
        replay = registration_operation_identity(self.candidate())
        conflict = registration_operation_identity(self.candidate(content_sha256="d" * 64))
        self.assertEqual(first, replay)
        self.assertNotEqual(first, conflict)


class CorpusRegistrationValidationScriptTests(CorpusRegistrationMappingTests):
    def test_evidence_parent_is_created_through_verified_repository_parent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "docs/evidence").mkdir(parents=True)
            with mock.patch.object(REGISTRATION_MODULE, "ROOT", root):
                digest = REGISTRATION_MODULE._write_evidence(
                    Path("docs/evidence/corpus/step14-evidence.json"),
                    {"status": "PASS"},
                )
            target = root / "docs/evidence/corpus/step14-evidence.json"
            self.assertTrue(target.is_file())
            self.assertEqual(json.loads(target.read_bytes())["evidence_digest"], digest)

    def test_symlinked_evidence_parent_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence_root = root / "docs/evidence"
            evidence_root.mkdir(parents=True)
            escaped = root / "escaped"
            escaped.mkdir()
            (evidence_root / "corpus").symlink_to(escaped, target_is_directory=True)
            with mock.patch.object(REGISTRATION_MODULE, "ROOT", root):
                with self.assertRaises(
                    REGISTRATION_MODULE.ValidationFailure
                ) as caught:
                    REGISTRATION_MODULE._write_evidence(
                        Path("docs/evidence/corpus/step14-evidence.json"),
                        {"status": "PASS"},
                    )
            self.assertEqual(caught.exception.code, "UNSAFE_EVIDENCE_PATH")
            self.assertEqual(list(escaped.iterdir()), [])

    def test_evidence_publish_race_never_overwrites_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            parent = root / "docs/evidence/corpus"
            parent.mkdir(parents=True)
            target = parent / "step14-evidence.json"
            real_link = REGISTRATION_MODULE.os.link

            def create_racing_target(
                source: object,
                destination: object,
                **kwargs: object,
            ) -> None:
                Path(destination).write_bytes(b"existing-evidence")
                real_link(source, destination, **kwargs)

            with (
                mock.patch.object(REGISTRATION_MODULE, "ROOT", root),
                mock.patch.object(
                    REGISTRATION_MODULE.os,
                    "link",
                    side_effect=create_racing_target,
                ),
            ):
                with self.assertRaises(
                    REGISTRATION_MODULE.ValidationFailure
                ) as caught:
                    REGISTRATION_MODULE._write_evidence(
                        Path("docs/evidence/corpus/step14-evidence.json"),
                        {"status": "PASS"},
                    )
            self.assertEqual(caught.exception.code, "EVIDENCE_NO_OVERWRITE")
            self.assertEqual(target.read_bytes(), b"existing-evidence")
            self.assertEqual(list(parent.glob("*.part")), [])

    def test_candidate_jsonl_requires_canonical_bytes(self) -> None:
        candidate = self.candidate()
        from aioa_memory_kernel.contracts.serialization import canonical_json_bytes

        data = json.loads(canonical_json_bytes(candidate))
        parsed = REGISTRATION_MODULE._candidate_from_data(data)
        self.assertEqual(parsed, candidate)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "candidates.jsonl"
            target.write_bytes(json.dumps(data, indent=2).encode() + b"\n")
            with self.assertRaisesRegex(
                REGISTRATION_MODULE.ValidationFailure,
                "Step 14 registration validation failed",
            ):
                REGISTRATION_MODULE.load_candidates(target)

    def test_candidate_file_must_match_verified_manifest_digest(self) -> None:
        from aioa_memory_kernel.contracts.serialization import canonical_json_bytes

        line = canonical_json_bytes(self.candidate()) + b"\n"
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "candidates.jsonl"
            target.write_bytes(line)
            with self.assertRaises(
                REGISTRATION_MODULE.ValidationFailure
            ) as caught:
                REGISTRATION_MODULE.load_candidates(
                    target,
                    expected_sha256="0" * 64,
                    expected_length=len(line),
                )
        self.assertEqual(caught.exception.code, "CANDIDATE_FILE_DIGEST_MISMATCH")

    def test_duplicate_json_member_fails_closed(self) -> None:
        with self.assertRaises(REGISTRATION_MODULE.ValidationFailure) as caught:
            REGISTRATION_MODULE._strict_json_line(b'{"x":1,"x":2}')
        self.assertEqual(caught.exception.code, "DUPLICATE_CANDIDATE_MEMBER")

    def test_nonfinite_candidate_number_fails_closed(self) -> None:
        with self.assertRaises(REGISTRATION_MODULE.ValidationFailure) as caught:
            REGISTRATION_MODULE._strict_json_line(b'{"x":NaN}')
        self.assertEqual(caught.exception.code, "NONFINITE_CANDIDATE_NUMBER")

    def test_control_candidates_are_explicit_and_not_real_inventory_rows(self) -> None:
        review = REGISTRATION_MODULE._control_candidate(
            self.candidate(),
            suffix="review",
            disposition=RegistrationDisposition.REVIEW_REQUIRED,
        )
        quarantine = REGISTRATION_MODULE._control_candidate(
            self.candidate(),
            suffix="quarantine",
            disposition=RegistrationDisposition.QUARANTINED,
        )
        self.assertNotEqual(review.candidate_id, quarantine.candidate_id)
        self.assertEqual(review.reason_codes, ("SYNTHETIC_CONTROL_ONLY",))
        self.assertEqual(quarantine.disposition, RegistrationDisposition.QUARANTINED)

    def test_first_registration_uses_step6_durable_operation(self) -> None:
        candidate = self.candidate()
        record = build_source_registry_record(candidate, created_at=NOW)
        sql = "\n".join(
            REGISTRATION_MODULE._registration_sql(record, candidate, replay=False)
        )
        self.assertIn("memory_patch.persistence_operations", sql)
        self.assertIn("SOURCE_REGISTER", sql)
        self.assertIn(record.registry_digest, sql)

    def test_exact_replay_cannot_create_new_operation(self) -> None:
        candidate = self.candidate()
        record = build_source_registry_record(candidate, created_at=NOW)
        sql = "\n".join(
            REGISTRATION_MODULE._registration_sql(record, candidate, replay=True)
        )
        self.assertNotIn("persistence_operations", sql)
        self.assertEqual(sql.count("ON CONFLICT DO NOTHING"), 2)

    def test_registration_sql_batches_stay_below_process_argument_limit(self) -> None:
        candidate = self.candidate()
        record = build_source_registry_record(candidate, created_at=NOW)
        pairs = tuple((candidate, record) for _ in range(80))
        client = mock.Mock()
        REGISTRATION_MODULE._insert_records(
            client,
            "step14_test",
            pairs,
            replay=False,
        )
        calls = client.execute.call_args_list
        self.assertGreater(len(calls), 1)
        self.assertTrue(
            all(
                len(call.args[1].encode("utf-8"))
                <= REGISTRATION_MODULE.SQL_ARGUMENT_MAX_BYTES
                for call in calls
            )
        )
        self.assertTrue(
            all(
                call.args[1].count("INSERT INTO memory_patch.knowledge_sources")
                <= REGISTRATION_MODULE.BATCH_SIZE
                for call in calls
            )
        )

    def test_sql_client_oserror_is_sanitized(self) -> None:
        candidate = self.candidate()
        record = build_source_registry_record(candidate, created_at=NOW)
        client = mock.Mock()
        client.execute.side_effect = OSError("synthetic process failure")
        with self.assertRaises(REGISTRATION_MODULE.ValidationFailure) as caught:
            REGISTRATION_MODULE._insert_records(
                client,
                "step14_test",
                ((candidate, record),),
                replay=False,
            )
        self.assertEqual(caught.exception.code, "SQL_CLIENT_PROCESS_FAILED")

    def test_validation_script_preserves_step14_boundaries(self) -> None:
        text = REGISTRATION_SCRIPT.read_text(encoding="utf-8")
        self.assertIn('"automatic_publication": 0', text)
        self.assertIn('"step15_started": False', text)
        self.assertIn('"raw_corpus_body_stored_in_database": False', text)
        self.assertNotIn("requests.", text)
        self.assertNotIn("boto3", text)
        self.assertNotIn("delete_object", text)

    def test_step14_database_prefix_is_owned_and_bounded(self) -> None:
        migration_script = (
            Path(__file__).resolve().parents[1]
            / "scripts/run_cockroachdb_migrations.py"
        ).read_text(encoding="utf-8")
        self.assertIn('"mp_step14_"', migration_script)
        self.assertIn("graceful_stop_and_remove", REGISTRATION_SCRIPT.read_text())


if __name__ == "__main__":
    unittest.main()
