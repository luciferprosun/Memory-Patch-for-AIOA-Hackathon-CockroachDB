"""Offline contract and safety tests for the CockroachDB Step 3 spike.

These tests never start CockroachDB, open a network connection, or read a
credential-bearing environment variable. Live probes require a separate,
explicit ``--allow-live`` invocation of the capability harness.
"""

from __future__ import annotations

import copy
import io
import inspect
import json
import re
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from scripts import run_cockroachdb_capability_spike as spike


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PIN_PATH = REPOSITORY_ROOT / "config" / "cockroachdb" / "version-pin.json"
MATRIX_PATH = (
    REPOSITORY_ROOT
    / "docs"
    / "evidence"
    / "cockroachdb-v26-2"
    / "capability-matrix.json"
)
FINGERPRINT_PATH = MATRIX_PATH.with_name("runtime-fingerprint.json")
ROADMAP_PATH = REPOSITORY_ROOT / "docs" / "roadmap" / "PRODUCTION_ROADMAP.md"
DOCS_INDEX_PATH = REPOSITORY_ROOT / "docs" / "README.md"

STEP3_DOCUMENTS = (
    REPOSITORY_ROOT
    / "docs"
    / "architecture"
    / "COCKROACHDB_V26_2_CAPABILITY_BASELINE_1A.md",
    REPOSITORY_ROOT
    / "docs"
    / "adr"
    / "ADR-010-cockroachdb-v26-2-version-pin.md",
    REPOSITORY_ROOT
    / "docs"
    / "audits"
    / "STEP_3_COCKROACHDB_CAPABILITY_SPIKE_CLOSURE_1A.md",
)

EXPECTED_PIN_FIELDS = {
    "schema_version",
    "product",
    "target_series",
    "exact_version",
    "release_channel",
    "release_status",
    "release_date",
    "support_status",
    "verified_at_utc",
    "selection_decision",
    "artifact",
    "runtime",
    "official_sources",
    "rationale",
}

EXPECTED_ARTIFACT_FIELDS = {
    "kind",
    "platform",
    "architecture",
    "source",
    "local_sha256",
    "vendor_checksum_available",
    "vendor_checksum_verified",
    "immutable_container_digest",
}

EXPECTED_RUNTIME_FIELDS = {
    "server_build_tag",
    "server_version_output",
    "cluster_version",
    "upgrade_finalized",
    "deployment_mode",
}

EXPECTED_MATRIX_ROW_FIELDS = {
    "capability_id",
    "name",
    "status",
    "availability",
    "maturity",
    "deployment_scope",
    "runtime_exact_version",
    "probe_id",
    "probe_plane",
    "prerequisites",
    "expected_semantics",
    "observed_semantics",
    "expected_error",
    "observed_sqlstate",
    "evidence_reference",
    "official_source_references",
    "known_limitations",
    "required_for_mvp",
    "mvp_decision",
    "decision_reason",
    "cleanup_verified",
}

EXPECTED_MATRIX_SUMMARY_FIELDS = {
    "total_rows",
    "pass_count",
    "fail_count",
    "defer_count",
    "runtime_exact_version",
    "generated_at_utc",
    "matrix_sha256",
    "harness_version",
    "overall_step3_decision",
}


def _load_pin() -> dict[str, object]:
    return json.loads(PIN_PATH.read_text(encoding="utf-8"))


def _refresh_matrix_digest(matrix: dict[str, object]) -> None:
    summary = matrix["summary"]
    if not isinstance(summary, dict):
        raise AssertionError("test fixture summary must be an object")
    summary["matrix_sha256"] = spike.matrix_digest(matrix)


def _valid_matrix() -> dict[str, object]:
    pin = _load_pin()
    exact = pin["exact_version"]
    if not isinstance(exact, str):
        raise AssertionError("test fixture exact_version must be a string")
    recorder = spike.ProbeRecorder(exact)
    sources = spike.source_records("2026-07-26T00:00:00Z")
    source_for_capability = {
        capability_id: source["category"]
        for source in reversed(sources)
        for capability_id in source["capability_ids"]
    }
    for capability_id, _ in spike.REQUIRED_CAPABILITIES:
        status = (
            "PASS"
            if capability_id in {"CRDB-001", "CRDB-002", "CRDB-036"}
            else "DEFER"
        )
        recorder.mark(
            capability_id,
            status,
            "Offline synthetic matrix fixture; no live result is claimed.",
            sources=(source_for_capability[capability_id],),
            decision="USE" if status == "PASS" else "DEFER",
            decision_reason="Synthetic validator fixture.",
            probe_plane=(
                "OFFLINE_REPOSITORY"
                if capability_id == "CRDB-032"
                else "LIVE_LOCAL"
            ),
            cleanup_verified=True,
        )
    return spike.make_matrix(
        recorder,
        exact_version=exact,
        generated_at="2026-07-26T00:00:00Z",
        sources=sources,
    )


def _markdown_targets(document: Path) -> list[str]:
    content = document.read_text(encoding="utf-8")
    return re.findall(r"(?<!!)\[[^\]]+\]\(([^)]+)\)", content)


class VersionPinContractTests(unittest.TestCase):
    def test_repository_pin_is_valid_canonical_json(self) -> None:
        pin = _load_pin()
        spike.validate_pin(pin)
        self.assertEqual(set(pin), EXPECTED_PIN_FIELDS)
        self.assertEqual(
            PIN_PATH.read_text(encoding="utf-8"),
            spike.pretty_json(pin),
        )
        self.assertTrue(PIN_PATH.read_bytes().endswith(b"\n"))

    def test_exact_version_is_one_immutable_v26_2_patch(self) -> None:
        pin = _load_pin()
        self.assertRegex(str(pin["exact_version"]), r"^v26\.2\.\d+$")
        for invalid in (
            "latest",
            "v26.2",
            "26.2",
            "v26.2.x",
            "v26.2.*",
            "v26.3.0",
            "v26.2.4-rc.1",
            "",
            None,
        ):
            with self.subTest(invalid=invalid):
                mutated = copy.deepcopy(pin)
                mutated["exact_version"] = invalid
                with self.assertRaises(spike.HarnessError):
                    spike.validate_pin(mutated)

    def test_wrong_release_series_is_rejected(self) -> None:
        pin = _load_pin()
        for invalid in ("v26", "v26.1", "v26.3", "26.2", ""):
            with self.subTest(invalid=invalid):
                mutated = copy.deepcopy(pin)
                mutated["target_series"] = invalid
                with self.assertRaises(spike.HarnessError):
                    spike.validate_pin(mutated)

    def test_every_top_level_pin_field_is_required(self) -> None:
        pin = _load_pin()
        for field in sorted(EXPECTED_PIN_FIELDS):
            with self.subTest(field=field):
                mutated = copy.deepcopy(pin)
                del mutated[field]
                with self.assertRaises(spike.HarnessError):
                    spike.validate_pin(mutated)

    def test_every_artifact_identity_field_is_required(self) -> None:
        pin = _load_pin()
        artifact = pin["artifact"]
        self.assertIsInstance(artifact, dict)
        self.assertTrue(EXPECTED_ARTIFACT_FIELDS.issubset(artifact))
        for field in sorted(EXPECTED_ARTIFACT_FIELDS):
            with self.subTest(field=field):
                mutated = copy.deepcopy(pin)
                del mutated["artifact"][field]
                with self.assertRaises(spike.HarnessError):
                    spike.validate_pin(mutated)

    def test_every_runtime_identity_field_is_required(self) -> None:
        pin = _load_pin()
        runtime = pin["runtime"]
        self.assertIsInstance(runtime, dict)
        self.assertEqual(set(runtime), EXPECTED_RUNTIME_FIELDS)
        for field in sorted(EXPECTED_RUNTIME_FIELDS):
            with self.subTest(field=field):
                mutated = copy.deepcopy(pin)
                del mutated["runtime"][field]
                with self.assertRaises(spike.HarnessError):
                    spike.validate_pin(mutated)

    def test_advertised_vendor_checksum_must_be_verified(self) -> None:
        pin = _load_pin()
        mutated = copy.deepcopy(pin)
        mutated["artifact"]["vendor_checksum_available"] = True
        mutated["artifact"]["vendor_checksum_verified"] = False
        with self.assertRaises(spike.HarnessError):
            spike.validate_pin(mutated)

    def test_official_source_records_have_provenance_fields(self) -> None:
        pin = _load_pin()
        sources = pin["official_sources"]
        self.assertIsInstance(sources, list)
        self.assertGreaterEqual(len(sources), 3)
        for source in sources:
            with self.subTest(source=source.get("category")):
                self.assertEqual(
                    set(source),
                    {"category", "location", "retrieved_at_utc", "title"},
                )
                self.assertRegex(
                    source["location"],
                    r"^https://(?:www\.cockroachlabs\.com|binaries\.cockroachdb\.com)/",
                )
                self.assertRegex(
                    source["retrieved_at_utc"],
                    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$",
                )

    def test_pin_rejects_unofficial_or_ambiguous_artifact_identity(self) -> None:
        pin = _load_pin()
        mutations = (
            ("official_sources", 0, "location", "https://example.invalid/fake"),
            ("artifact", None, "kind", "latest-container"),
            ("artifact", None, "platform", "unknown"),
            ("pin", None, "verified_at_utc", "2026-07-26T09:49:06"),
        )
        for section, index, field, value in mutations:
            with self.subTest(section=section, field=field):
                mutated = copy.deepcopy(pin)
                if section == "official_sources":
                    mutated[section][index][field] = value
                elif section == "artifact":
                    mutated[section][field] = value
                else:
                    mutated[field] = value
                with self.assertRaises(spike.HarnessError):
                    spike.validate_pin(mutated)


class CanonicalEvidenceContractTests(unittest.TestCase):
    def test_canonical_json_is_stable_and_newline_terminated(self) -> None:
        left = {"z": 2, "a": {"y": 1, "x": 0}}
        right = {"a": {"x": 0, "y": 1}, "z": 2}
        expected = b'{"a":{"x":0,"y":1},"z":2}\n'
        self.assertEqual(spike.canonical_json_bytes(left), expected)
        self.assertEqual(spike.canonical_json_bytes(right), expected)

    def test_matrix_digest_is_stable_across_mapping_order(self) -> None:
        left = _valid_matrix()
        right = json.loads(
            json.dumps(left, sort_keys=True),
            object_pairs_hook=lambda pairs: dict(reversed(pairs)),
        )
        self.assertEqual(spike.matrix_digest(left), spike.matrix_digest(right))

    def test_valid_synthetic_matrix_passes(self) -> None:
        pin = _load_pin()
        matrix = _valid_matrix()
        spike.validate_matrix(matrix, pin=pin)
        self.assertEqual(
            matrix["summary"]["matrix_sha256"],
            spike.matrix_digest(matrix),
        )

    def test_fail_and_defer_override_permissive_mvp_decisions(self) -> None:
        pin = _load_pin()
        recorder = spike.ProbeRecorder(str(pin["exact_version"]))
        recorder.mark(
            "CRDB-001",
            "FAIL",
            "Synthetic negative result.",
            sources=("release",),
            decision="USE_WITH_GUARD",
        )
        recorder.mark(
            "CRDB-002",
            "DEFER",
            "Synthetic deferred result.",
            sources=("release",),
            decision="USE",
        )
        self.assertEqual(
            recorder.by_id["CRDB-001"]["mvp_decision"],
            "DO_NOT_USE",
        )
        self.assertEqual(
            recorder.by_id["CRDB-002"]["mvp_decision"],
            "DEFER",
        )

    def test_every_matrix_row_has_the_complete_contract(self) -> None:
        matrix = _valid_matrix()
        for row in matrix["capabilities"]:
            with self.subTest(capability_id=row["capability_id"]):
                self.assertEqual(set(row), EXPECTED_MATRIX_ROW_FIELDS)

    def test_required_capability_ids_are_complete_and_ordered(self) -> None:
        expected = [f"CRDB-{number:03d}" for number in range(1, 37)]
        actual = [item[0] for item in spike.REQUIRED_CAPABILITIES]
        self.assertEqual(len(actual), 36)
        self.assertEqual(actual, expected)

        matrix = _valid_matrix()
        matrix_ids = [row["capability_id"] for row in matrix["capabilities"]]
        self.assertEqual(matrix_ids[:36], expected)

    def test_missing_or_reordered_required_capability_is_rejected(self) -> None:
        pin = _load_pin()
        for mutation in ("missing", "reordered"):
            with self.subTest(mutation=mutation):
                matrix = _valid_matrix()
                rows = matrix["capabilities"]
                if mutation == "missing":
                    rows.pop(7)
                else:
                    rows[7], rows[8] = rows[8], rows[7]
                summary = matrix["summary"]
                summary["total_rows"] = len(rows)
                summary["defer_count"] = len(rows)
                _refresh_matrix_digest(matrix)
                with self.assertRaises(spike.HarnessError):
                    spike.validate_matrix(matrix, pin=pin)

    def test_duplicate_capability_id_is_rejected(self) -> None:
        pin = _load_pin()
        matrix = _valid_matrix()
        matrix["capabilities"].append(copy.deepcopy(matrix["capabilities"][-1]))
        matrix["summary"]["total_rows"] += 1
        matrix["summary"]["defer_count"] += 1
        _refresh_matrix_digest(matrix)
        with self.assertRaises(spike.HarnessError):
            spike.validate_matrix(matrix, pin=pin)

    def test_unknown_status_availability_maturity_and_decision_are_rejected(
        self,
    ) -> None:
        pin = _load_pin()
        cases = (
            ("status", "MAYBE"),
            ("availability", "SOMETIMES"),
            ("maturity", "STABLE_ENOUGH"),
            ("mvp_decision", "PROBABLY_USE"),
        )
        for field, value in cases:
            with self.subTest(field=field):
                matrix = _valid_matrix()
                matrix["capabilities"][0][field] = value
                _refresh_matrix_digest(matrix)
                with self.assertRaises(spike.HarnessError):
                    spike.validate_matrix(matrix, pin=pin)

    def test_summary_counts_are_recomputed_not_trusted(self) -> None:
        pin = _load_pin()
        matrix = _valid_matrix()
        matrix["summary"]["pass_count"] = 1
        matrix["summary"]["defer_count"] -= 1
        _refresh_matrix_digest(matrix)
        with self.assertRaises(spike.HarnessError):
            spike.validate_matrix(matrix, pin=pin)

    def test_every_summary_field_is_required(self) -> None:
        pin = _load_pin()
        matrix = _valid_matrix()
        self.assertEqual(set(matrix["summary"]), EXPECTED_MATRIX_SUMMARY_FIELDS)
        for field in sorted(EXPECTED_MATRIX_SUMMARY_FIELDS):
            with self.subTest(field=field):
                mutated = copy.deepcopy(matrix)
                del mutated["summary"][field]
                if field != "matrix_sha256":
                    _refresh_matrix_digest(mutated)
                with self.assertRaises(spike.HarnessError):
                    spike.validate_matrix(mutated, pin=pin)

    def test_matrix_digest_tampering_is_rejected(self) -> None:
        pin = _load_pin()
        matrix = _valid_matrix()
        matrix["capabilities"][0]["observed_semantics"] = "tampered"
        with self.assertRaises(spike.HarnessError):
            spike.validate_matrix(matrix, pin=pin)

    def test_runtime_version_must_match_pin_in_summary_and_every_row(self) -> None:
        pin = _load_pin()
        for mutation in ("summary", "row"):
            with self.subTest(mutation=mutation):
                matrix = _valid_matrix()
                if mutation == "summary":
                    matrix["summary"]["runtime_exact_version"] = "v26.2.999"
                else:
                    matrix["capabilities"][12]["runtime_exact_version"] = (
                        "v26.2.999"
                    )
                _refresh_matrix_digest(matrix)
                with self.assertRaises(spike.HarnessError):
                    spike.validate_matrix(matrix, pin=pin)

    def test_matrix_official_sources_are_complete_and_bound(self) -> None:
        pin = _load_pin()
        cases = ("missing_title", "unofficial_url", "naive_time", "bad_binding")
        for mutation in cases:
            with self.subTest(mutation=mutation):
                matrix = _valid_matrix()
                source = matrix["official_sources"][0]
                if mutation == "missing_title":
                    del source["title"]
                elif mutation == "unofficial_url":
                    source["location"] = "https://example.invalid/not-official"
                elif mutation == "naive_time":
                    source["retrieved_at_utc"] = "2026-07-26T00:00:00"
                else:
                    unrelated = next(
                        candidate
                        for candidate in matrix["official_sources"]
                        if "CRDB-001" not in candidate["capability_ids"]
                    )
                    matrix["capabilities"][0]["official_source_references"] = [
                        unrelated["location"]
                    ]
                _refresh_matrix_digest(matrix)
                with self.assertRaises(spike.HarnessError):
                    spike.validate_matrix(matrix, pin=pin)

    def test_matrix_closure_semantics_fail_closed(self) -> None:
        pin = _load_pin()
        mutations = (
            ("name", "renamed capability"),
            ("observed_semantics", ""),
            ("probe_plane", "MOCK"),
            ("required_for_mvp", "yes"),
            ("cleanup_verified", False),
            ("observed_sqlstate", "retry me"),
            ("evidence_reference", "dangling"),
            ("mvp_decision", "DO_NOT_USE"),
        )
        for field, value in mutations:
            with self.subTest(field=field):
                matrix = _valid_matrix()
                row = matrix["capabilities"][0]
                row["status"] = "PASS"
                row["mvp_decision"] = "USE"
                row[field] = value
                _refresh_matrix_digest(matrix)
                with self.assertRaises(spike.HarnessError):
                    spike.validate_matrix(matrix, pin=pin)

    def test_identity_and_cleanup_rows_must_pass(self) -> None:
        pin = _load_pin()
        for capability_id in ("CRDB-001", "CRDB-002", "CRDB-036"):
            with self.subTest(capability_id=capability_id):
                matrix = _valid_matrix()
                target = next(
                    row
                    for row in matrix["capabilities"]
                    if row["capability_id"] == capability_id
                )
                target["status"] = "DEFER"
                target["mvp_decision"] = "DEFER"
                _refresh_matrix_digest(matrix)
                with self.assertRaises(spike.HarnessError):
                    spike.validate_matrix(matrix, pin=pin)

    def test_overall_decision_is_derived_from_counts(self) -> None:
        pin = _load_pin()
        matrix = _valid_matrix()
        matrix["summary"]["overall_step3_decision"] = (
            "VALIDATED_FOR_STEP_4_CONSTRAINED_DESIGN"
        )
        _refresh_matrix_digest(matrix)
        with self.assertRaises(spike.HarnessError):
            spike.validate_matrix(matrix, pin=pin)

    def test_fingerprint_exact_server_binding_and_shape_fail_closed(self) -> None:
        pin = _load_pin()
        fingerprint = json.loads(
            FINGERPRINT_PATH.read_text(encoding="utf-8")
        )
        fingerprint["harness_version"] = spike.HARNESS_VERSION
        spike.validate_fingerprint(fingerprint, pin=pin)
        mutations = (
            ("runtime", "client_build_tag", None),
            ("artifact", "binary_sha256", None),
            ("runtime", "server_version", "CockroachDB CCL v26.2.999"),
            ("capability_probe_context", "harness_source_sha256", None),
        )
        for section, field, replacement in mutations:
            with self.subTest(section=section, field=field):
                mutated = copy.deepcopy(fingerprint)
                if replacement is None:
                    del mutated[section][field]
                else:
                    mutated[section][field] = replacement
                with self.assertRaises(spike.HarnessError):
                    spike.validate_fingerprint(mutated, pin=pin)

    def test_committed_live_evidence_is_present_valid_and_canonical(self) -> None:
        self.assertTrue(MATRIX_PATH.is_file(), f"missing {MATRIX_PATH}")
        self.assertTrue(FINGERPRINT_PATH.is_file(), f"missing {FINGERPRINT_PATH}")
        pin = _load_pin()
        matrix = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))
        fingerprint = json.loads(FINGERPRINT_PATH.read_text(encoding="utf-8"))
        spike.validate_matrix(matrix, pin=pin)
        spike.validate_fingerprint(fingerprint, pin=pin, matrix=matrix)
        self.assertEqual(
            MATRIX_PATH.read_text(encoding="utf-8"),
            spike.pretty_json(matrix),
        )
        self.assertEqual(
            FINGERPRINT_PATH.read_text(encoding="utf-8"),
            spike.pretty_json(fingerprint),
        )
        self.assertEqual(matrix["summary"]["total_rows"], len(matrix["capabilities"]))

    def test_secret_bearing_fields_and_values_are_rejected(self) -> None:
        forbidden = (
            {"password": "not-a-real-password"},
            {"nested": {"sql_url": "redacted-placeholder"}},
            {"value": "postgresql://user:pass@example.invalid/db"},
            {"value": "Bearer not-a-real-token"},
            {"value": "api_key=not-a-real-key"},
        )
        for value in forbidden:
            with self.subTest(value=value):
                with self.assertRaises(spike.HarnessError):
                    spike.assert_no_secret(value)

    def test_secret_in_matrix_is_rejected_even_with_valid_digest(self) -> None:
        pin = _load_pin()
        matrix = _valid_matrix()
        matrix["capabilities"][0]["known_limitations"] = [
            "postgresql://user:pass@example.invalid/db"
        ]
        _refresh_matrix_digest(matrix)
        with self.assertRaises(spike.HarnessError):
            spike.validate_matrix(matrix, pin=pin)


class RedactionAndSqlstateTests(unittest.TestCase):
    def test_changefeed_tsv_is_decoded_semantically_as_json(self) -> None:
        bytea_payload = json.dumps(
            {"after": {"payload": "v1"}},
            separators=(",", ":"),
        ).encode("utf-8").hex()
        fixtures = (
            (
                "table\tkey\tvalue\n"
                "synthetic\t[1]\t"
                '"{""after"": {""payload"": ""v1""}}"\n'
            ),
            (
                'table,key,value\nsynthetic,[1],'
                '"{""after"": {""payload"": ""v1""}}"\n'
            ),
            (
                'synthetic,[1],"{""after"":'
                ' {""payload"": ""v1""}}"\n'
            ),
            (
                "synthetic\t[1]\t"
                '"{\\"after\\":{\\"payload\\":\\"v1\\"}}"\n'
                "synthetic\t[1]\t"
                '"{\\"after\\":{\\"payload\\":\\"v2\\"}}"\n'
                "synthetic\t[1]\t"
                '"{\\"after\\":null}"\n'
                "synthetic\t\t"
                '"{\\"resolved\\":\\"2026-07-27T00:00:00Z\\"}"\n'
            ),
            f"synthetic\t\\x5b226b6579225d\t\\x{bytea_payload}\n",
        )
        for output in fixtures:
            with self.subTest(delimiter=output.splitlines()[0]):
                events = spike.parse_changefeed_json(output)
                self.assertEqual(events[0], {"after": {"payload": "v1"}})
                if '\\"' in output:
                    self.assertIn({"after": {"payload": "v2"}}, events)
                    self.assertIn({"after": None}, events)
                    self.assertIn(
                        {"resolved": "2026-07-27T00:00:00Z"},
                        events,
                    )

    def test_url_credentials_and_complete_dsn_are_redacted(self) -> None:
        source = (
            "failure postgresql://alice:not-a-real-password@private.invalid/"
            "defaultdb?sslmode=require"
        )
        redacted = spike.redact_text(source)
        self.assertNotIn("alice", redacted)
        self.assertNotIn("not-a-real-password", redacted)
        self.assertNotIn("private.invalid", redacted)
        self.assertNotIn("sslmode", redacted)
        self.assertIn("<redacted-dsn>", redacted)

    def test_password_bearer_and_query_parameters_are_redacted(self) -> None:
        source = (
            "password=not-a-real-password\n"
            "Authorization hint: Bearer not-a-real-token\n"
            "https://example.invalid/path?token=not-a-real-token"
        )
        redacted = spike.redact_text(source)
        self.assertNotIn("not-a-real-password", redacted)
        self.assertNotIn("not-a-real-token", redacted)
        self.assertIn("password=<redacted>", redacted)
        self.assertIn("Bearer <redacted>", redacted)
        self.assertIn("?<redacted-query>", redacted)

    def test_https_userinfo_and_authorization_headers_are_redacted(self) -> None:
        source = (
            "https://alice:not-a-real-password@private.invalid/path "
            "Authorization: Basic not-a-real-credential"
        )
        redacted = spike.redact_text(source)
        self.assertNotIn("alice", redacted)
        self.assertNotIn("not-a-real-password", redacted)
        self.assertNotIn("private.invalid", redacted)
        self.assertNotIn("not-a-real-credential", redacted)
        self.assertIn("<redacted-url-authority>", redacted)
        self.assertIn("Authorization: <redacted>", redacted)

    def test_sqlstate_is_extracted_without_error_text_classification(self) -> None:
        stderr = "ERROR: restart transaction\nSQLSTATE: 40001\n"
        self.assertEqual(spike.extract_sqlstate(stderr), "40001")
        self.assertIsNone(spike.extract_sqlstate("ERROR: restart transaction"))

    def test_only_40001_is_retryable(self) -> None:
        self.assertTrue(spike.is_retryable_sqlstate("40001"))
        for value in ("23505", "40003", "XX000", "", None):
            with self.subTest(value=value):
                self.assertFalse(spike.is_retryable_sqlstate(value))


class RetryAndTimeoutTests(unittest.TestCase):
    def test_retry_succeeds_with_bounded_exponential_backoff(self) -> None:
        attempts: list[int] = []
        sleeps: list[float] = []

        def operation(attempt: int) -> str:
            attempts.append(attempt)
            if attempt < 3:
                raise spike.SqlExecutionError("retry", sqlstate="40001")
            return "committed"

        result = spike.bounded_retry(
            operation,
            max_attempts=3,
            base_backoff_seconds=0.1,
            sleeper=sleeps.append,
        )
        self.assertEqual(result, "committed")
        self.assertEqual(attempts, [1, 2, 3])
        self.assertEqual(sleeps, [0.1, 0.2])

    def test_retry_exhaustion_is_deterministic_and_bounded(self) -> None:
        attempts: list[int] = []
        sleeps: list[float] = []

        def operation(attempt: int) -> None:
            attempts.append(attempt)
            raise spike.SqlExecutionError("retry", sqlstate="40001")

        with self.assertRaisesRegex(
            spike.HarnessError,
            r"retry attempts exhausted after 4: 40001",
        ):
            spike.bounded_retry(
                operation,
                max_attempts=4,
                base_backoff_seconds=0.5,
                sleeper=sleeps.append,
            )
        self.assertEqual(attempts, [1, 2, 3, 4])
        self.assertEqual(sleeps, [0.5, 1.0, 1.0])

    def test_permanent_23505_is_not_retried(self) -> None:
        attempts: list[int] = []
        sleeps: list[float] = []

        def operation(attempt: int) -> None:
            attempts.append(attempt)
            raise spike.SqlExecutionError("unique", sqlstate="23505")

        with self.assertRaises(spike.SqlExecutionError):
            spike.bounded_retry(
                operation,
                max_attempts=10,
                base_backoff_seconds=0.1,
                sleeper=sleeps.append,
            )
        self.assertEqual(attempts, [1])
        self.assertEqual(sleeps, [])

    def test_retry_cleanup_hook_runs_before_backoff_sleep(self) -> None:
        transaction_open = True
        observations: list[bool] = []

        def operation(attempt: int) -> str:
            nonlocal transaction_open
            transaction_open = True
            if attempt == 1:
                raise spike.SqlExecutionError("retry", sqlstate="40001")
            return "committed"

        def close_transaction() -> None:
            nonlocal transaction_open
            transaction_open = False

        def sleeper(_seconds: float) -> None:
            observations.append(transaction_open)

        result = spike.bounded_retry(
            operation,
            max_attempts=2,
            base_backoff_seconds=0.1,
            sleeper=sleeper,
            before_sleep=close_transaction,
        )
        self.assertEqual(result, "committed")
        self.assertEqual(observations, [False])

    def test_retry_configuration_has_hard_bounds(self) -> None:
        noop = lambda attempt: attempt
        invalid = (
            {"max_attempts": 0, "base_backoff_seconds": 0},
            {"max_attempts": 11, "base_backoff_seconds": 0},
            {"max_attempts": True, "base_backoff_seconds": 0},
            {"max_attempts": 1, "base_backoff_seconds": -0.1},
            {"max_attempts": 1, "base_backoff_seconds": 1.1},
        )
        for kwargs in invalid:
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(spike.HarnessError):
                    spike.bounded_retry(noop, **kwargs)

    def test_every_subprocess_timeout_must_be_bounded(self) -> None:
        spike.validate_timeout(0.1)
        spike.validate_timeout(180)
        for value in (0, -1, 181, True, None, "10"):
            with self.subTest(value=value):
                with self.assertRaises(spike.HarnessError):
                    spike.validate_timeout(value)

    def test_run_process_passes_explicit_timeout_to_subprocess(self) -> None:
        completed = mock.Mock(returncode=0, stdout="ok", stderr="")
        with mock.patch.object(
            spike.subprocess,
            "run",
            return_value=completed,
        ) as run:
            result = spike.run_process(["synthetic"], timeout=7)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(run.call_args.kwargs["timeout"], 7)
        self.assertEqual(run.call_args.kwargs["stdin"], spike.subprocess.DEVNULL)


class RuntimeGuardTests(unittest.TestCase):
    def test_insecure_local_runtime_is_loopback_only(self) -> None:
        spike.require_loopback("127.0.0.1", insecure=True)
        for host in ("localhost", "0.0.0.0", "::1", "192.0.2.1", ""):
            with self.subTest(host=host):
                with self.assertRaises(spike.HarnessError):
                    spike.require_loopback(host, insecure=True)

    def test_binary_version_mismatch_is_rejected_with_mocked_process(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / "cockroach"
            binary.write_text("synthetic executable placeholder", encoding="utf-8")
            result = spike.ProcessResult(
                0,
                "Build Tag: v26.2.3\nPlatform: linux amd64\n",
                "",
            )
            with mock.patch.object(spike, "run_process", return_value=result):
                with self.assertRaisesRegex(
                    spike.HarnessError,
                    r"binary version mismatch: expected v26\.2\.4, found v26\.2\.3",
                ):
                    spike.verify_binary_version(binary, "v26.2.4")

    def test_local_sql_client_scrubs_external_connection_overrides(self) -> None:
        inherited = {
            "COCKROACH_URL": "postgresql://user:secret@shared.invalid/db",
            "COCKROACH_SQL_URL": "redacted",
            "DATABASE_URL": "redacted",
            "PGHOST": "shared.invalid",
            "PGPASSWORD": "secret",
        }
        with mock.patch.dict(spike.os.environ, inherited, clear=False):
            client = spike.SqlClient(
                binary=Path("/synthetic/cockroach"),
                host="127.0.0.1",
                port=26257,
            )
            args, environment = client.command("defaultdb", "SELECT 1")
        for variable in inherited:
            self.assertNotIn(variable, environment)
        self.assertIn("--host=127.0.0.1", args)
        self.assertIn("--port=26257", args)

    def test_server_version_mismatch_is_rejected_with_fake_client(self) -> None:
        class FakeClient:
            def execute(self, database: str, sql: str) -> str:
                del database
                if sql == "SELECT version()":
                    return "version\nCockroachDB CCL v26.2.3\n"
                if sql == "SHOW CLUSTER SETTING version":
                    return "version\n26.2\n"
                raise AssertionError(f"unexpected SQL: {sql}")

        pin = _load_pin()
        recorder = spike.ProbeRecorder(str(pin["exact_version"]))
        build_output = f"Build Tag: {pin['exact_version']}\n"
        with self.assertRaisesRegex(
            spike.HarnessError,
            "client/server version does not match immutable pin",
        ):
            spike.probe_identity(FakeClient(), recorder, pin, build_output)

    def test_live_actions_refuse_to_run_without_explicit_permission(self) -> None:
        for action in ("--preflight", "--run"):
            with self.subTest(action=action), mock.patch.object(
                spike,
                "run_preflight",
            ) as preflight, mock.patch.object(
                spike,
                "run_live_local",
            ) as live, redirect_stderr(io.StringIO()):
                exit_code = spike.main(
                    [action, "--cockroach-binary", "/nonexistent/cockroach"]
                )
                self.assertEqual(exit_code, 1)
                preflight.assert_not_called()
                live.assert_not_called()

    def test_offline_validation_does_not_spawn_process_or_live_runtime(self) -> None:
        with mock.patch.object(spike, "run_process") as process, mock.patch.object(
            spike,
            "run_live_local",
        ) as live, redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            exit_code = spike.main(["--offline-validate"])
        self.assertEqual(exit_code, 0)
        process.assert_not_called()
        live.assert_not_called()


class CleanupOwnershipTests(unittest.TestCase):
    def test_local_server_process_uses_owned_runtime_directory(self) -> None:
        source = inspect.getsource(spike.LocalRuntime.start)
        self.assertIn("cwd=self.runtime_dir", source)

    def test_cleanup_path_must_be_directly_owned_under_tmp(self) -> None:
        spike.assert_owned_runtime_path(Path("/tmp/mp_step3_owned_fixture"))
        for path in (
            Path("/tmp/not_owned"),
            Path("/tmp/mp_step3_owned_fixture/child"),
            REPOSITORY_ROOT,
            Path("/home/l/mp_step3_owned_fixture"),
            Path("/"),
        ):
            with self.subTest(path=path):
                with self.assertRaises(spike.HarnessError):
                    spike.assert_owned_runtime_path(path)

    def test_cleanup_command_only_accepts_already_clean_evidence(self) -> None:
        complete = {
            "runtime_fingerprint": {
                "cleanup": {
                    "pid_exited": True,
                    "ports_closed": True,
                    "settings_match": True,
                    "temporary_path_removed": True,
                    "sql_resources_removed": True,
                    "owned_children_exited": True,
                    "remaining_changefeed": False,
                    "remaining_ttl_job": False,
                    "errors": [],
                    "child_cleanup_errors": [],
                }
            }
        }
        incomplete = copy.deepcopy(complete)
        incomplete["runtime_fingerprint"]["cleanup"]["ports_closed"] = False
        with tempfile.TemporaryDirectory() as directory:
            complete_path = Path(directory) / "complete.json"
            incomplete_path = Path(directory) / "incomplete.json"
            complete_path.write_text(json.dumps(complete), encoding="utf-8")
            incomplete_path.write_text(json.dumps(incomplete), encoding="utf-8")
            self.assertEqual(
                spike.cleanup_from_result(complete_path)["status"],
                "ALREADY_CLEAN",
            )
            with self.assertRaisesRegex(
                spike.HarnessError,
                "recorded cleanup is incomplete",
            ):
                spike.cleanup_from_result(incomplete_path)

    def test_harness_contains_no_broad_process_kill_command(self) -> None:
        source = Path(spike.__file__).read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"(?i)(?:^|\W)(?:pkill|killall)(?:\W|$)", source))
        self.assertIn("self.process.send_signal(signal.SIGTERM)", source)
        self.assertIn("self.process.kill()", source)


class SqlAssetTests(unittest.TestCase):
    def test_sql_assets_render_with_only_owned_identifiers(self) -> None:
        run_id = "mp_step3_20260726t120000z_000001_deadbeef"
        roles = {
            "app": f"{run_id}_app",
            "owner": f"{run_id}_owner",
            "tenant_a": f"{run_id}_tenant_a",
            "tenant_b": f"{run_id}_tenant_b",
        }
        rendered = spike.render_sql_assets(run_id, run_id, roles)
        self.assertEqual(tuple(rendered), spike.SQL_FILES)
        for name, content in rendered.items():
            with self.subTest(name=name):
                self.assertNotIn("{{", content)
                self.assertIn(run_id, content)

    def test_all_ordered_sql_assets_are_present_and_scoped(self) -> None:
        expected = (
            "00_runtime_identity.sql",
            "10_vector.sql",
            "20_full_text.sql",
            "30_rls.sql",
            "40_ttl.sql",
            "50_changefeed.sql",
            "60_partial_unique.sql",
            "70_as_of_system_time.sql",
            "80_serializable_retry.sql",
            "90_cleanup.sql",
        )
        self.assertEqual(spike.SQL_FILES, expected)
        spike.validate_sql_assets()
        for name in expected:
            content = (spike.SQL_ROOT / name).read_text(encoding="utf-8")
            self.assertIn("Memory Patch Step 3", content)
            self.assertIn("disposable", content)

    def test_missing_sql_asset_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in spike.SQL_FILES[:-1]:
                (root / name).write_text(
                    "-- Memory Patch Step 3 disposable capability probe\n",
                    encoding="utf-8",
                )
            with mock.patch.object(spike, "SQL_ROOT", root):
                with self.assertRaisesRegex(
                    spike.HarnessError,
                    "missing ordered SQL assets",
                ):
                    spike.validate_sql_assets()


class DocumentationClosureTests(unittest.TestCase):
    def test_step3_artifacts_are_linked_from_documentation_index(self) -> None:
        content = DOCS_INDEX_PATH.read_text(encoding="utf-8")
        expected_targets = (
            "architecture/COCKROACHDB_V26_2_CAPABILITY_BASELINE_1A.md",
            "adr/ADR-010-cockroachdb-v26-2-version-pin.md",
            "evidence/cockroachdb-v26-2/capability-matrix.json",
            "audits/STEP_3_COCKROACHDB_CAPABILITY_SPIKE_CLOSURE_1A.md",
        )
        for target in expected_targets:
            with self.subTest(target=target):
                self.assertIn(f"]({target})", content)
                self.assertTrue((DOCS_INDEX_PATH.parent / target).is_file())

    def test_roadmap_marks_only_step3_closed_and_step4_next(self) -> None:
        content = ROADMAP_PATH.read_text(encoding="utf-8")
        step3 = re.findall(
            r"^- \[([ x])\] \*\*Step 3 — "
            r"CockroachDB v26\.2 Capability Spike and Version Pin 1A\*\*",
            content,
            flags=re.MULTILINE,
        )
        step4 = re.findall(
            r"^- \[([ x])\] \*\*Step 4 — "
            r"CockroachDB Logical Schema and Migration Foundation 1A\*\*",
            content,
            flags=re.MULTILINE,
        )
        self.assertEqual(step3, ["x"])
        self.assertEqual(step4, [" "])
        self.assertIn(
            "Dokładny następny krok: "
            "`Step 4 — CockroachDB Logical Schema and Migration Foundation 1A`",
            content,
        )

    def test_required_step3_documents_exist(self) -> None:
        for document in STEP3_DOCUMENTS:
            with self.subTest(document=document):
                self.assertTrue(document.is_file(), f"missing {document}")

    def test_local_markdown_links_in_step3_package_resolve(self) -> None:
        documents = (DOCS_INDEX_PATH, ROADMAP_PATH, *STEP3_DOCUMENTS)
        for document in documents:
            self.assertTrue(document.is_file(), f"missing {document}")
            for raw_target in _markdown_targets(document):
                target = raw_target.split("#", 1)[0]
                if (
                    not target
                    or target.startswith("#")
                    or re.match(r"^[a-z][a-z0-9+.-]*:", target, re.IGNORECASE)
                ):
                    continue
                with self.subTest(document=document, target=target):
                    resolved = (document.parent / target).resolve()
                    self.assertTrue(
                        resolved.is_file(),
                        f"broken local link in {document}: {raw_target}",
                    )


if __name__ == "__main__":
    unittest.main()
