"""Offline security and behavior tests for the read-only ccloud release gate."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import sys
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

from tests._support import REPOSITORY_ROOT


MODULE_PATH = (
    REPOSITORY_ROOT / "scripts" / "run_ccloud_control_plane_gate.py"
)
SPEC = importlib.util.spec_from_file_location(
    "run_ccloud_control_plane_gate_test", MODULE_PATH
)
assert SPEC is not None and SPEC.loader is not None
gate_module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = gate_module
SPEC.loader.exec_module(gate_module)


CLUSTER_ID = "11111111-2222-3333-4444-555555555555"
SQL_DNS = "fluid-lemur-19455.example.cockroachlabs.cloud"
SQL_DNS_SHA256 = hashlib.sha256(SQL_DNS.encode("utf-8")).hexdigest()


def expected_cluster(**overrides: str) -> object:
    values = {
        "name": "fluid-lemur",
        "provider": "GCP",
        "region": "europe-west3",
        "plan": "SERVERLESS",
        "state": "CREATED",
        "cockroach_version": "v26.2.5",
        "sql_dns_sha256": SQL_DNS_SHA256,
    }
    values.update(overrides)
    return gate_module.ExpectedCluster(**values)


def cluster_record(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "id": CLUSTER_ID,
        "name": "fluid-lemur",
        "cloud_provider": "GCP",
        "cockroach_version": "v26.2.5",
        "plan": "SERVERLESS",
        "state": "CREATED",
        "account_id": "account-must-not-be-emitted",
        "creator_id": "creator-must-not-be-emitted",
        "regions": [
            {
                "name": "europe-west3",
                "primary": True,
                "sql_dns": SQL_DNS,
            }
        ],
    }
    value.update(overrides)
    return value


class CcloudControlPlaneGateTests(unittest.TestCase):
    def test_valid_metadata_produces_sanitized_read_only_receipt(self) -> None:
        receipt = gate_module.build_receipt(
            [cluster_record()],
            cluster_record(),
            expected_cluster(),
            binary_sha256=gate_module.CCLOUD_BINARY_SHA256,
            script_sha256="a" * 64,
            observed_at="2026-08-16T00:00:00+00:00",
        )
        self.assertEqual(
            receipt["verdict"], "PASS_READ_ONLY_CCLOUD_CONTROL_PLANE_GATE"
        )
        self.assertEqual(receipt["agent_operation"]["control_plane_reads"], 2)
        self.assertEqual(
            receipt["agent_operation"]["control_plane_mutations"], 0
        )
        serialized = json.dumps(receipt, sort_keys=True)
        self.assertNotIn(CLUSTER_ID, serialized)
        self.assertNotIn(SQL_DNS, serialized)
        self.assertNotIn("account-must-not-be-emitted", serialized)
        self.assertNotIn("creator-must-not-be-emitted", serialized)
        self.assertEqual(receipt["cluster"]["identity_sha256"], hashlib.sha256(CLUSTER_ID.encode()).hexdigest())
        self.assertEqual(receipt["cluster"]["sql_dns_sha256"], SQL_DNS_SHA256)
        self.assertRegex(receipt["receipt_sha256"], r"^[0-9a-f]{64}$")

    def test_cluster_name_must_be_unique(self) -> None:
        with self.assertRaisesRegex(
            gate_module.ControlPlaneGateError,
            "CCLOUD_CLUSTER_IDENTITY_NOT_UNIQUE",
        ):
            gate_module.build_receipt(
                [cluster_record(), cluster_record()],
                cluster_record(),
                expected_cluster(),
                binary_sha256=gate_module.CCLOUD_BINARY_SHA256,
                script_sha256="a" * 64,
                observed_at="2026-08-16T00:00:00+00:00",
            )

    def test_list_and_info_identity_must_match(self) -> None:
        with self.assertRaisesRegex(
            gate_module.ControlPlaneGateError,
            "CCLOUD_CLUSTER_LIST_INFO_MISMATCH",
        ):
            gate_module.build_receipt(
                [cluster_record()],
                cluster_record(id="different-cluster-id"),
                expected_cluster(),
                binary_sha256=gate_module.CCLOUD_BINARY_SHA256,
                script_sha256="a" * 64,
                observed_at="2026-08-16T00:00:00+00:00",
            )

    def test_all_control_plane_expectations_fail_closed(self) -> None:
        cases = {
            "provider": {"cloud_provider": "AWS"},
            "plan": {"plan": "DEDICATED"},
            "state": {"state": "PAUSED"},
            "version": {"cockroach_version": "v26.2.4"},
            "name": {"name": "another-cluster"},
        }
        for label, changes in cases.items():
            with self.subTest(label=label):
                with self.assertRaises(
                    gate_module.ControlPlaneGateError
                ):
                    gate_module.build_receipt(
                        [cluster_record()],
                        cluster_record(**changes),
                        expected_cluster(),
                        binary_sha256=gate_module.CCLOUD_BINARY_SHA256,
                        script_sha256="a" * 64,
                        observed_at="2026-08-16T00:00:00+00:00",
                    )

    def test_region_and_dns_identity_fail_closed(self) -> None:
        wrong_region = [
            {"name": "us-east1", "primary": True, "sql_dns": SQL_DNS}
        ]
        wrong_dns = [
            {
                "name": "europe-west3",
                "primary": True,
                "sql_dns": "another.example.cockroachlabs.cloud",
            }
        ]
        for regions in (wrong_region, wrong_dns):
            with self.assertRaises(gate_module.ControlPlaneGateError):
                gate_module.build_receipt(
                    [cluster_record()],
                    cluster_record(regions=regions),
                    expected_cluster(),
                    binary_sha256=gate_module.CCLOUD_BINARY_SHA256,
                    script_sha256="a" * 64,
                    observed_at="2026-08-16T00:00:00+00:00",
                )

    def test_region_projection_requires_one_primary_and_one_dns_identity(self) -> None:
        invalid_regions = (
            [],
            [{"name": "europe-west3", "primary": False, "sql_dns": SQL_DNS}],
            [
                {"name": "europe-west3", "primary": True, "sql_dns": SQL_DNS},
                {
                    "name": "europe-west4",
                    "primary": False,
                    "sql_dns": "different.example.cockroachlabs.cloud",
                },
            ],
        )
        for regions in invalid_regions:
            with self.subTest(regions=regions):
                with self.assertRaises(gate_module.ControlPlaneGateError):
                    gate_module.build_receipt(
                        [cluster_record()],
                        cluster_record(regions=regions),
                        expected_cluster(),
                        binary_sha256=gate_module.CCLOUD_BINARY_SHA256,
                        script_sha256="a" * 64,
                        observed_at="2026-08-16T00:00:00+00:00",
                    )

    def test_run_gate_executes_only_two_allowlisted_read_commands(self) -> None:
        commands: list[tuple[str, ...]] = []
        environments: list[dict[str, str]] = []

        def runner(command: object, environment: object) -> str:
            commands.append(tuple(command))
            environments.append(dict(environment))
            if "list" in command:
                return json.dumps([cluster_record()])
            return json.dumps(cluster_record())

        with (
            mock.patch.object(gate_module.shutil, "which", return_value=sys.executable),
            mock.patch.object(
                gate_module,
                "_sha256_file",
                side_effect=[gate_module.CCLOUD_BINARY_SHA256, "a" * 64],
            ),
            mock.patch.dict(
                os.environ,
                {
                    "HOME": "/safe-home",
                    "PATH": "/safe-path",
                    "OPENROUTER_API_KEY": "must-not-enter-child",
                    "DATABASE_URL_APP": "must-not-enter-child",
                    "AWS_SECRET_ACCESS_KEY": "must-not-enter-child",
                },
                clear=True,
            ),
        ):
            receipt = gate_module.run_gate(
                expected_cluster(),
                runner=runner,
                observed_at="2026-08-16T00:00:00+00:00",
            )
        self.assertEqual(receipt["agent_operation"]["control_plane_reads"], 2)
        self.assertEqual(len(commands), 2)
        self.assertEqual(commands[0][1:], ("cluster", "list", "--output", "json", "--quiet"))
        self.assertEqual(commands[1][1:3], ("cluster", "info"))
        self.assertEqual(commands[1][3], "fluid-lemur")
        self.assertNotIn("create", commands[0] + commands[1])
        self.assertNotIn("update", commands[0] + commands[1])
        self.assertNotIn("delete", commands[0] + commands[1])
        for environment in environments:
            self.assertEqual(environment, {"HOME": "/safe-home", "PATH": "/safe-path"})

    def test_binary_pin_is_checked_before_any_command(self) -> None:
        runner = mock.Mock()
        with (
            mock.patch.object(gate_module.shutil, "which", return_value=sys.executable),
            mock.patch.object(gate_module, "_sha256_file", return_value="f" * 64),
        ):
            with self.assertRaisesRegex(
                gate_module.ControlPlaneGateError,
                "CCLOUD_BINARY_PIN_MISMATCH",
            ):
                gate_module.run_gate(expected_cluster(), runner=runner)
        runner.assert_not_called()

    def test_json_parser_rejects_malformed_or_wrong_shape(self) -> None:
        with self.assertRaisesRegex(
            gate_module.ControlPlaneGateError, "CCLOUD_JSON_INVALID"
        ):
            gate_module._parse_json("not-json", expected_type=list)
        with self.assertRaisesRegex(
            gate_module.ControlPlaneGateError, "CCLOUD_JSON_SHAPE_INVALID"
        ):
            gate_module._parse_json("{}", expected_type=list)

    def test_cli_failure_does_not_reflect_stderr(self) -> None:
        secret = "sentinel-secret-value"
        failed = mock.Mock(
            returncode=1,
            stdout="",
            stderr=f"authentication failed for {secret}",
        )
        with mock.patch.object(gate_module.subprocess, "run", return_value=failed):
            with self.assertRaises(gate_module.ControlPlaneGateError) as captured:
                gate_module._run_ccloud(("ccloud", "cluster", "list"), {})
        self.assertEqual(str(captured.exception), "CCLOUD_COMMAND_FAILED")
        self.assertNotIn(secret, str(captured.exception))

    def test_argument_validation_rejects_injection_shaped_values(self) -> None:
        invalid_values = (
            {"name": "fluid-lemur;delete"},
            {"region": "$(touch-bad)"},
            {"provider": "GCP;DELETE"},
            {"cockroach_version": "latest"},
            {"sql_dns_sha256": "not-a-digest"},
        )
        for changes in invalid_values:
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    expected_cluster(**changes)

    def test_main_failure_is_structured_and_sanitized(self) -> None:
        error = io.StringIO()
        with redirect_stderr(error):
            status = gate_module.main(
                [
                    "--cluster-name",
                    "invalid;name",
                    "--expected-provider",
                    "GCP",
                    "--expected-region",
                    "europe-west3",
                    "--expected-plan",
                    "SERVERLESS",
                    "--expected-state",
                    "CREATED",
                    "--expected-version",
                    "v26.2.5",
                    "--expected-sql-dns-sha256",
                    "a" * 64,
                ]
            )
        self.assertEqual(status, 1)
        payload = json.loads(error.getvalue())
        self.assertEqual(payload["verdict"], "BLOCKED_FAIL_CLOSED")
        self.assertEqual(payload["reason"], "INVALID_CLUSTER_NAME")

    def test_documentation_explains_both_cockroachdb_tools_and_live_aws(self) -> None:
        integration = (
            REPOSITORY_ROOT
            / "docs/submission/DEVPOST_COMPONENT_INTEGRATION_1A.md"
        ).read_text(encoding="utf-8")
        for required in (
            "Distributed Vector Indexing",
            "ccloud CLI",
            "Amazon ECS",
            "Amazon S3",
            "What the agent actually does",
            "control-plane mutations: `0`",
        ):
            self.assertIn(required, integration)

    def test_committed_live_receipt_matches_the_exact_gate_script(self) -> None:
        evidence_path = (
            REPOSITORY_ROOT
            / "docs/evidence/cockroachdb-cloud/"
            "ccloud-control-plane-gate-1a.json"
        )
        receipt = json.loads(evidence_path.read_text(encoding="utf-8"))
        receipt_digest = receipt.pop("receipt_sha256")
        canonical_receipt = json.dumps(
            receipt,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        self.assertEqual(
            receipt_digest,
            hashlib.sha256(canonical_receipt.encode("utf-8")).hexdigest(),
        )
        self.assertEqual(
            receipt["tool"]["script_sha256"],
            hashlib.sha256(MODULE_PATH.read_bytes()).hexdigest(),
        )
        self.assertEqual(
            receipt["verdict"],
            "PASS_READ_ONLY_CCLOUD_CONTROL_PLANE_GATE",
        )
        self.assertEqual(
            receipt["agent_operation"]["control_plane_mutations"], 0
        )
        self.assertEqual(receipt["agent_operation"]["database_writes"], 0)
        self.assertFalse(any(receipt["security"].values()))


if __name__ == "__main__":
    unittest.main()
