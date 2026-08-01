"""Static safety and dispatch checks for the Step 15 operations scripts."""

from __future__ import annotations

import unittest
from unittest import mock
import sys
from pathlib import Path

from tests._support import REPOSITORY_ROOT

from scripts import run_german_law_temporal_jurisdiction_normalization as normalizer
from scripts import run_german_law_temporal_jurisdiction_validation as validation


class Step15OperationsScriptTests(unittest.TestCase):
    def test_normalization_guard_allows_only_step15_closure_scope(self) -> None:
        allowed = (
            "src/aioa_memory_kernel/german_law/normalization.py",
            "scripts/run_german_law_temporal_jurisdiction_normalization.py",
            "docs/evidence/corpus/step15-german-law-temporal-jurisdictional-summary.json",
            "docs/adr/ADR-022-german-law-temporal-jurisdictional-normalization.md",
            "docs/architecture/GERMAN_LAW_TEMPORAL_JURISDICTIONAL_NORMALIZATION_1A.md",
            "docs/operations/STEP_15_GERMAN_LAW_TEMPORAL_JURISDICTIONAL_VALIDATION_1A.md",
        )
        for path in allowed:
            with self.subTest(path=path):
                self.assertTrue(normalizer._allowed_dirty_path(path))
        for path in ("README.md", "src/unrelated.py", "docs/evidence/corpus/step14-other.json"):
            with self.subTest(path=path):
                self.assertFalse(normalizer._allowed_dirty_path(path))

    def test_validation_guard_allows_only_step15_closure_scope(self) -> None:
        self.assertTrue(validation._allowed_dirty_path("scripts/run_german_law_temporal_jurisdiction_validation.py"))
        self.assertTrue(validation._allowed_dirty_path("scripts/run_cockroachdb_migrations.py"))
        self.assertTrue(validation._allowed_dirty_path("docs/audits/STEP_15_GERMAN_LAW_TEMPORAL_JURISDICTIONAL_NORMALIZATION_CLOSURE_1A.md"))
        self.assertFalse(validation._allowed_dirty_path("docs/audits/STEP_14_OTHER.md"))

    def test_scripts_require_explicit_mode_or_external_confirmations(self) -> None:
        with mock.patch.object(sys, "argv", ["step15-normalizer"]), self.assertRaises(SystemExit):
            normalizer._arguments()

    def test_scripts_do_not_contain_dynamic_loading_or_corpus_execution(self) -> None:
        text = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (
                REPOSITORY_ROOT / "scripts/run_german_law_temporal_jurisdiction_normalization.py",
                REPOSITORY_ROOT / "scripts/run_german_law_temporal_jurisdiction_validation.py",
                REPOSITORY_ROOT / "src/aioa_memory_kernel/german_law/normalization.py",
            )
        ).lower()
        for forbidden in ("importlib", "entry_points", "eval(", "exec(", "pickle", "marshal", "ctypes", "requests.", "urllib.request", "boto3"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, text)
        self.assertIn("o_nofollow", text)
        self.assertIn("automatic_update_allowed", text)

    def test_disposable_database_prefix_is_explicitly_owned_by_step15(self) -> None:
        migration_script = (REPOSITORY_ROOT / "scripts/run_cockroachdb_migrations.py").read_text(encoding="utf-8")
        self.assertIn('"mp_step15_"', migration_script)

    def test_registry_replay_query_parses_tsv_header_and_exact_row(self) -> None:
        client = mock.Mock()
        client.execute.return_value = "source_id\tregistry_digest\nsource-a\tdigest-a\n"
        self.assertEqual(
            validation._database_registry_pair(client, "mp_step15_test", "source-a"),
            ("source-a", "digest-a"),
        )
        client.execute.return_value = "source_id\tregistry_digest\nsource-b\tdigest-a\n"
        with self.assertRaises(validation.ValidationFailure) as caught:
            validation._database_registry_pair(client, "mp_step15_test", "source-a")
        self.assertEqual(caught.exception.code, "STEP15_REGISTRY_ROW_MISSING")


if __name__ == "__main__":
    unittest.main()
