"""Static safety checks for the Step 16 publication operations scripts."""

from __future__ import annotations

import sys
import unittest
from unittest import mock

from tests._support import REPOSITORY_ROOT

from aioa_memory_kernel.storage import SnapshotServiceUnavailableError
from scripts import run_german_law_publication as publication
from scripts import run_german_law_publication_validation as validation


class Step16OperationsScriptTests(unittest.TestCase):
    def test_runner_guard_allows_only_step16_closure_scope(self) -> None:
        for path in (
            "src/aioa_memory_kernel/german_law/publication.py",
            "scripts/run_german_law_publication.py",
            "scripts/run_german_law_publication_validation.py",
            "scripts/aws_cli_s3_client.py",
            "tests/test_aws_cli_s3_client.py",
            "docs/evidence/corpus/step16-german-law-hat-publication-summary.json",
            "docs/audits/STEP_16_GERMAN_LAW_HAT_PUBLICATION_CORPUS_VERIFICATION_CLOSURE_1A.md",
        ):
            with self.subTest(path=path):
                self.assertTrue(publication._allowed_dirty_path(path))
                self.assertTrue(validation._allowed_dirty_path(path))
        for path in ("README.md", "src/unrelated.py", "docs/evidence/corpus/step15-other.json"):
            with self.subTest(path=path):
                self.assertFalse(publication._allowed_dirty_path(path))
                self.assertFalse(validation._allowed_dirty_path(path))

    def test_publication_runner_requires_one_explicit_mode(self) -> None:
        with mock.patch.object(sys, "argv", ["step16-publication"]):
            with self.assertRaises(SystemExit):
                publication._arguments()

    def test_validation_runner_requires_all_fixed_inputs(self) -> None:
        with mock.patch.object(sys, "argv", ["step16-validation"]):
            with self.assertRaises(SystemExit):
                validation._arguments()

    def test_scripts_do_not_use_dynamic_loading_or_model_clients(self) -> None:
        text = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (
                REPOSITORY_ROOT / "scripts/run_german_law_publication.py",
                REPOSITORY_ROOT / "scripts/run_german_law_publication_validation.py",
                REPOSITORY_ROOT / "src/aioa_memory_kernel/german_law/publication.py",
            )
        ).lower()
        for forbidden in (
            "importlib",
            "entry_points",
            "eval(",
            "exec(",
            "pickle",
            "marshal",
            "ctypes",
            "requests.",
            "urllib.request",
            "boto3",
            "bedrock",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, text)
        self.assertIn("o_nofollow", text)
        # Conditional create is implemented by the established Step 7 CLI
        # transport; Step 16 delegates all writes to that transport.
        transport = (REPOSITORY_ROOT / "scripts/aws_cli_s3_client.py").read_text(encoding="utf-8").lower()
        self.assertIn("if-none-match", transport)
        self.assertIn("step16_validation_s3_write_forbidden", text)

    def test_read_only_transport_fails_closed_on_put(self) -> None:
        transport = validation._ReadOnlyS3Transport(mock.Mock())
        with self.assertRaises(validation.ValidationFailure) as caught:
            transport.put_object(Bucket="example")
        self.assertEqual(caught.exception.code, "STEP16_VALIDATION_S3_WRITE_FORBIDDEN")
        self.assertEqual(transport.put_attempts, 1)

    def test_snapshot_retry_policy_is_bounded_and_typed(self) -> None:
        self.assertEqual(publication._SNAPSHOT_RETRY_DELAYS_SECONDS, (1.0, 2.0, 4.0))
        error = SnapshotServiceUnavailableError(
            "synthetic unavailable",
            operation="put_object",
            sanitized_code="S3_UNAVAILABLE",
            aws_error_code="RequestTimeout",
        )
        self.assertEqual(error.sanitized_code, "S3_UNAVAILABLE")
        source = (REPOSITORY_ROOT / "scripts/run_german_law_publication.py").read_text(encoding="utf-8")
        self.assertIn("except SnapshotServiceUnavailableError", source)
        self.assertNotIn("except SnapshotOperationError", source)


if __name__ == "__main__":
    unittest.main()
