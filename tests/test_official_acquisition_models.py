"""Deterministic contract tests for official-corpus acquisition records."""

from __future__ import annotations

import dataclasses
import unittest
from dataclasses import replace

from aioa_memory_kernel.acquisition.models import (
    GIB,
    AcquisitionPolicy,
    HttpObjectReceipt,
    SourceStatus,
)


class AcquisitionPolicyTests(unittest.TestCase):
    def test_default_policy_is_the_exact_bounded_approved_plan(self) -> None:
        policy = AcquisitionPolicy()

        self.assertEqual(policy.schema_version, "1.0.0")
        self.assertEqual(
            policy.policy_id,
            "german-law-official-corpus-acquisition-1a",
        )
        self.assertEqual(
            policy.target_relative_path,
            "HAT's libary/German Law Official Corpus 1A",
        )
        self.assertEqual(policy.seed_relative_path, "HAT's libary/German law.zip")
        self.assertEqual(policy.seed_length, 774_599_792)
        self.assertEqual(policy.maximum_root_bytes, 120 * GIB)
        self.assertEqual(policy.initial_minimum_free_bytes, 350 * GIB)
        self.assertEqual(policy.final_minimum_free_bytes, 250 * GIB)
        self.assertEqual(policy.maximum_requests, 20_000)
        self.assertEqual(policy.maximum_response_bytes, 2 * GIB)
        self.assertEqual(policy.maximum_archive_expanded_bytes, 10 * GIB)
        self.assertEqual(policy.maximum_retries, 4)
        self.assertEqual(policy.connect_timeout_seconds, 30)
        self.assertEqual(policy.read_timeout_seconds, 180)
        self.assertEqual(policy.maximum_redirects, 3)

    def test_policy_digest_is_deterministic_and_binds_changes(self) -> None:
        first = AcquisitionPolicy()
        replay = AcquisitionPolicy()
        changed = replace(first, maximum_requests=first.maximum_requests - 1)

        self.assertEqual(first.digest, replay.digest)
        self.assertRegex(first.digest, r"^[0-9a-f]{64}$")
        self.assertNotEqual(first.digest, changed.digest)

    def test_policy_is_immutable(self) -> None:
        policy = AcquisitionPolicy()

        with self.assertRaises(dataclasses.FrozenInstanceError):
            policy.maximum_requests = 1  # type: ignore[misc]

    def test_unsupported_schema_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported acquisition policy"):
            AcquisitionPolicy(schema_version="2.0.0")

    def test_absolute_and_traversing_paths_are_rejected(self) -> None:
        for field_name, unsafe in (
            ("target_relative_path", "/absolute/target"),
            ("target_relative_path", "parent/../target"),
            ("target_relative_path", "parent\\target"),
            ("seed_relative_path", "/absolute/seed.zip"),
            ("seed_relative_path", "parent/../seed.zip"),
            ("seed_relative_path", "parent\\seed.zip"),
        ):
            with self.subTest(field_name=field_name, unsafe=unsafe):
                with self.assertRaisesRegex(ValueError, "path"):
                    AcquisitionPolicy(**{field_name: unsafe})

    def test_device_and_seed_digests_must_be_exact_sha256_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "device reference"):
            AcquisitionPolicy(
                expected_device_reference="external-volume-sha256:not-a-digest"
            )
        with self.assertRaisesRegex(ValueError, "seed"):
            AcquisitionPolicy(seed_sha256="not-a-digest")
        with self.assertRaisesRegex(ValueError, "seed"):
            AcquisitionPolicy(seed_length=0)

    def test_numeric_bounds_fail_closed(self) -> None:
        invalid = (
            {"maximum_root_bytes": 0},
            {"initial_minimum_free_bytes": 250 * GIB},
            {"maximum_requests": 0},
            {"maximum_response_bytes": 0},
            {"maximum_archive_expanded_bytes": 0},
            {"maximum_retries": -1},
            {"maximum_retries": 9},
            {"connect_timeout_seconds": 0},
            {"read_timeout_seconds": 0},
            {"maximum_redirects": 0},
            {"maximum_redirects": 6},
        )
        for values in invalid:
            with self.subTest(values=values):
                with self.assertRaisesRegex(ValueError, "bound"):
                    AcquisitionPolicy(**values)

    def test_source_status_values_are_stable_and_distinct(self) -> None:
        self.assertEqual(SourceStatus.COMPLETE.value, "COMPLETE")
        self.assertEqual(
            SourceStatus.SKIPPED_SAFE.value,
            "SKIPPED_SAFELY_WITH_EXACT_REASON",
        )
        self.assertEqual(
            SourceStatus.BLOCKED_CHANGED.value,
            "BLOCKED_BY_CHANGED_OFFICIAL_CONDITIONS",
        )
        self.assertEqual(len(SourceStatus), len({item.value for item in SourceStatus}))


class HttpObjectReceiptTests(unittest.TestCase):
    @staticmethod
    def receipt(**changes: object) -> HttpObjectReceipt:
        values: dict[str, object] = {
            "schema_version": "1.0.0",
            "source_catalog_id": "de-gii-consolidated-xml-1a",
            "requested_url": "https://www.gesetze-im-internet.de/a/xml.zip",
            "final_url": "https://www.gesetze-im-internet.de/a/xml.zip",
            "retrieved_at": "2030-01-02T12:00:00Z",
            "http_status": 200,
            "content_type": "application/zip",
            "content_length_header": "7",
            "etag": '"synthetic"',
            "last_modified": "Tue, 02 Jan 2030 12:00:00 GMT",
            "publisher_checksum": None,
            "publisher_checksum_algorithm": None,
            "local_sha256": "a" * 64,
            "byte_length": 7,
            "relative_output_path": (
                "10_DE_FEDERAL_CONSOLIDATED_GII/xml-zips/a.zip"
            ),
            "terms_reference": "gii-reuse-notes-1a",
            "license_reference": "gii-german-text-reuse-1a",
            "robots_reference": "gii-robots-1a",
            "request_sequence": 1,
            "retry_count": 0,
            "validation_status": "PASS",
            "quarantine_reasons": (),
        }
        values.update(changes)
        return HttpObjectReceipt(**values)  # type: ignore[arg-type]

    def test_sidecar_digest_is_deterministic_and_replay_stable(self) -> None:
        first = self.receipt().with_digest()
        replay = self.receipt().with_digest()

        self.assertRegex(first.sidecar_digest, r"^[0-9a-f]{64}$")
        self.assertEqual(first, replay)

    def test_sidecar_digest_binds_every_receipt_fact(self) -> None:
        baseline = self.receipt().with_digest()
        changed_url = self.receipt(
            final_url="https://www.gesetze-im-internet.de/b/xml.zip"
        ).with_digest()
        changed_bytes = self.receipt(
            local_sha256="b" * 64,
            byte_length=8,
            content_length_header="8",
        ).with_digest()
        quarantined = self.receipt(
            validation_status="QUARANTINED",
            quarantine_reasons=("ZIP_INTEGRITY_FAILED",),
        ).with_digest()

        for changed in (changed_url, changed_bytes, quarantined):
            with self.subTest(changed=changed):
                self.assertNotEqual(baseline.sidecar_digest, changed.sidecar_digest)

    def test_with_digest_replaces_untrusted_existing_digest(self) -> None:
        receipt = self.receipt(sidecar_digest="0" * 64)

        signed = receipt.with_digest()

        self.assertNotEqual(signed.sidecar_digest, "0" * 64)
        self.assertEqual(signed, self.receipt().with_digest())


if __name__ == "__main__":
    unittest.main()
