"""Static and offline request tests for the Step 10 AWS CLI transport."""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests._support import REPOSITORY_ROOT


MODULE_PATH = REPOSITORY_ROOT / "scripts" / "aws_cli_s3_client.py"
SPEC = importlib.util.spec_from_file_location("aws_cli_s3_client_test", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
client_module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = client_module
SPEC.loader.exec_module(client_module)


class FakeCompleted:
    returncode = 0
    stdout = '{"Status":"Enabled"}'
    stderr = ""


class AwsCliTransportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.aws = Path(sys.executable).resolve()
        self.aws_sha256 = hashlib.sha256(self.aws.read_bytes()).hexdigest()
        self.client = client_module.AwsCliS3Client(
            aws_binary=self.aws,
            profile="aoia-admin",
            region="eu-central-1",
            temporary_directory=Path(self.temporary.name),
            expected_binary_sha256=self.aws_sha256,
        )

    def test_every_command_carries_explicit_profile_region_and_no_pager(
        self,
    ) -> None:
        with mock.patch.object(
            client_module.subprocess,
            "run",
            return_value=FakeCompleted(),
        ) as run:
            response = self.client.get_bucket_versioning(Bucket="bucket-name")
        self.assertEqual(response["Status"], "Enabled")
        command = run.call_args.args[0]
        self.assertIn("aoia-admin", command)
        self.assertIn("eu-central-1", command)
        self.assertIn("--no-cli-pager", command)
        self.assertNotIn("default", command)
        self.assertEqual(
            self.client.operation_counts["get-bucket-versioning"],
            1,
        )
        self.assertIsNot(
            self.client.operation_counts,
            self.client.operation_counts,
        )

    def test_cli_error_exposes_only_structured_code(self) -> None:
        failed = mock.Mock(
            returncode=1,
            stdout="",
            stderr="An error occurred (ExpiredToken) when calling an operation",
        )
        with mock.patch.object(client_module.subprocess, "run", return_value=failed):
            with self.assertRaises(client_module.AwsCliS3Error) as captured:
                self.client.get_bucket_versioning(Bucket="bucket-name")
        self.assertEqual(
            captured.exception.response,
            {"Error": {"Code": "ExpiredToken"}},
        )

    def test_expired_sso_cli_message_maps_to_session_error_code(self) -> None:
        failed = mock.Mock(
            returncode=1,
            stdout="",
            stderr=(
                "Error when retrieving token from SSO: "
                "Token has expired and refresh failed"
            ),
        )
        with mock.patch.object(client_module.subprocess, "run", return_value=failed):
            with self.assertRaises(client_module.AwsCliS3Error) as captured:
                self.client.get_bucket_versioning(Bucket="bucket-name")
        self.assertEqual(
            captured.exception.response,
            {"Error": {"Code": "SSOTokenLoadError"}},
        )
        self.assertNotIn("expired", str(captured.exception).casefold())

    def test_known_cli_transport_timeout_maps_to_retryable_s3_code(self) -> None:
        failed = mock.Mock(
            returncode=1,
            stdout="",
            stderr="Read timeout on endpoint URL: https://example.invalid/object",
        )
        with mock.patch.object(client_module.subprocess, "run", return_value=failed):
            with self.assertRaises(client_module.AwsCliS3Error) as captured:
                self.client.get_bucket_versioning(Bucket="bucket-name")
        self.assertEqual(
            captured.exception.response,
            {"Error": {"Code": "RequestTimeout"}},
        )

    def test_unknown_cli_failure_remains_fail_closed(self) -> None:
        failed = mock.Mock(returncode=1, stdout="", stderr="unexpected failure")
        with mock.patch.object(client_module.subprocess, "run", return_value=failed):
            with self.assertRaises(client_module.AwsCliS3Error) as captured:
                self.client.get_bucket_versioning(Bucket="bucket-name")
        self.assertEqual(
            captured.exception.response,
            {"Error": {"Code": "UnclassifiedAwsCliError"}},
        )

    def test_binary_identity_change_fails_before_subprocess(self) -> None:
        with (
            mock.patch.object(
                client_module,
                "_file_sha256",
                return_value="f" * 64,
            ),
            mock.patch.object(client_module.subprocess, "run") as run,
        ):
            with self.assertRaises(client_module.AwsCliS3Error) as captured:
                self.client.get_bucket_versioning(Bucket="bucket-name")
        self.assertEqual(
            captured.exception.response,
            {"Error": {"Code": "AwsCliBinaryIdentityChanged"}},
        )
        run.assert_not_called()

    def test_public_surface_contains_no_delete_or_retention_bypass(self) -> None:
        names = {
            name
            for name in dir(client_module.AwsCliS3Client)
            if not name.startswith("_")
        }
        self.assertNotIn("delete_object", names)
        self.assertNotIn("delete_bucket", names)
        self.assertNotIn("put_object_retention", names)
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertNotIn("bypass-governance-retention", source.casefold())

    def test_module_import_is_inert_and_uses_no_shell(self) -> None:
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "run"
        ]
        self.assertEqual(len(calls), 1)
        self.assertNotIn("shell=True", MODULE_PATH.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
