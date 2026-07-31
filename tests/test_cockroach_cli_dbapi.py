"""Offline tests for the validation-only interactive CockroachDB transport."""

from __future__ import annotations

import ast
import importlib.util
import sys
import unittest
from datetime import UTC, datetime
from pathlib import Path

from tests._support import REPOSITORY_ROOT


MODULE_PATH = REPOSITORY_ROOT / "scripts" / "cockroach_cli_dbapi.py"
SPEC = importlib.util.spec_from_file_location("cockroach_cli_dbapi_test", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
dbapi = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = dbapi
SPEC.loader.exec_module(dbapi)


class SqlRenderingTests(unittest.TestCase):
    def test_supported_values_render_without_shell_interpolation(self) -> None:
        at = datetime(2040, 1, 2, 3, 4, 5, tzinfo=UTC)
        rendered = dbapi.render_sql(
            "SELECT %s, %s, %s, %s, %s",
            ("a'b", 7, True, None, at),
        )
        self.assertEqual(
            rendered,
            "SELECT 'a''b', 7, true, NULL, '2040-01-02T03:04:05+00:00'",
        )

    def test_placeholder_mismatch_fails_closed(self) -> None:
        with self.assertRaises(dbapi.CockroachCliDbapiError):
            dbapi.render_sql("SELECT %s, %s", ("only-one",))

    def test_unsupported_or_unsafe_values_fail_closed(self) -> None:
        for value in (b"bytes", object(), "bad\x00value"):
            with self.subTest(value=type(value).__name__):
                with self.assertRaises(dbapi.CockroachCliDbapiError):
                    dbapi.sql_literal(value)

    def test_naive_timestamp_is_rejected(self) -> None:
        with self.assertRaises(dbapi.CockroachCliDbapiError):
            dbapi.sql_literal(datetime(2040, 1, 2))

    def test_cockroach_csv_null_sentinel_is_not_confused_with_empty_text(
        self,
    ) -> None:
        self.assertIsNone(dbapi.decode_csv_cell("NULL"))
        self.assertEqual(dbapi.decode_csv_cell(""), "")
        self.assertEqual(
            dbapi.decode_csv_cell("2042-02-03 04:05:06+00"),
            "2042-02-03 04:05:06+00",
        )


class StaticSafetyTests(unittest.TestCase):
    def test_module_is_import_inert(self) -> None:
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        process_calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "Popen"
        ]
        self.assertEqual(len(process_calls), 1)
        parent = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef)
            and node.name == "CockroachCliConnection"
        )
        self.assertIn(process_calls[0], tuple(ast.walk(parent)))

    def test_transport_is_loopback_and_has_no_credential_arguments(self) -> None:
        text = MODULE_PATH.read_text(encoding="utf-8")
        self.assertIn('host != "127.0.0.1"', text)
        self.assertNotIn("--password", text)
        self.assertNotIn("PGPASSWORD=", text)
        self.assertNotIn("sslmode=disable", text)

    def test_exact_child_cleanup_uses_no_broad_process_kill(self) -> None:
        text = MODULE_PATH.read_text(encoding="utf-8")
        self.assertNotIn("pkill", text)
        self.assertNotIn("killall", text)
        self.assertIn("self._process.terminate()", text)
        self.assertIn("self._process.kill()", text)


class OwnedChildRegistryTests(unittest.TestCase):
    def test_exact_child_must_exit_before_registry_release(self) -> None:
        class Process:
            pid = 12345
            returncode = None

            def poll(self) -> int | None:
                return self.returncode

        process = Process()
        registry = dbapi.OwnedChildRegistry()
        registry.register(process)
        self.assertEqual(registry.active_pids, (12345,))
        self.assertFalse(registry.all_reaped)
        with self.assertRaises(dbapi.CockroachCliDbapiError):
            registry.release(process)
        process.returncode = 0
        registry.release(process)
        self.assertTrue(registry.all_reaped)

    def test_duplicate_child_registration_fails_closed(self) -> None:
        class Process:
            pid = 23456

            @staticmethod
            def poll() -> int:
                return 0

        process = Process()
        registry = dbapi.OwnedChildRegistry()
        registry.register(process)
        with self.assertRaises(dbapi.CockroachCliDbapiError):
            registry.register(process)
        registry.release(process)


if __name__ == "__main__":
    unittest.main()
