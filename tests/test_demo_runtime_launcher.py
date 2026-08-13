"""Focused R2 tests for the controlled demo-runtime launcher."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import subprocess
import sys
import types
import unittest
from pathlib import Path

from tests._support import REPOSITORY_ROOT

from aioa_memory_kernel.demo_runtime.config import (
    JUDGE_ALLOWED_SUBJECTS_ENV,
    OIDC_CLIENT_ID_ENV,
    OIDC_ISSUER_ENV,
    PUBLIC_ORIGIN_ENV,
    RUNTIME_MODE_ENV,
    RuntimeAssemblyError,
    RuntimeErrorCode,
)


SCRIPT = REPOSITORY_ROOT / "scripts" / "run_demo_runtime_1a.py"
SPEC = importlib.util.spec_from_file_location("run_demo_runtime_1a_test", SCRIPT)
if SPEC is None or SPEC.loader is None:  # pragma: no cover
    raise RuntimeError("R2 launcher could not be loaded")
launcher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(launcher)


def _database_summary():
    return types.SimpleNamespace(
        discovered=19,
        applied=0,
        replay_skipped=19,
        failures=0,
    )


async def _prepared_database(settings):
    return _database_summary()


def _hosted_environment() -> dict[str, str]:
    return {
        RUNTIME_MODE_ENV: "HOSTED_DEMO",
        OIDC_ISSUER_ENV: "https://identity.test",
        OIDC_CLIENT_ID_ENV: "runtime-launcher-test",
        PUBLIC_ORIGIN_ENV: "https://runtime.test",
        JUDGE_ALLOWED_SUBJECTS_ENV: "memory-patch-launcher-test-subject",
        "DATABASE_URL_APP": (
            "postgresql://runtime-app:synthetic-app@db.example.invalid/"
            "memory_patch?sslmode=verify-full"
        ),
        "DATABASE_URL_MIGRATOR": (
            "postgresql://runtime-migrator:synthetic-migrator@"
            "db.example.invalid/memory_patch?sslmode=verify-full"
        ),
        "OPENROUTER_API_KEY": "synthetic-launcher-provider-key",
        "AIOA_DEMO_PROVIDER_BUDGET_EPOCH": "launcher-test-budget-r5-1a",
        "AIOA_DEMO_PROVIDER_TENANT_ID": "tenant-launcher-r5",
    }


class DemoRuntimeLauncherTests(unittest.TestCase):
    def test_missing_python_dependency_fails_without_traceback(self) -> None:
        completed = subprocess.run(
            (sys.executable, "-S", str(SCRIPT), "check-config"),
            cwd=REPOSITORY_ROOT,
            env=_hosted_environment(),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
            check=False,
        )
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(
            completed.stderr.strip(),
            "MEMORY_PATCH_RUNTIME_ERROR=DEPENDENCY_MISSING",
        )
        self.assertNotIn("Traceback", completed.stderr)

    def test_repository_detection_fails_closed(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()) as stderr:
            result = launcher.main(
                ("check-config",),
                environment=_hosted_environment(),
                repository_root=Path("/definitely/not/the/repository"),
            )
        self.assertEqual(result, 2)
        self.assertEqual(
            stderr.getvalue().strip(),
            "MEMORY_PATCH_RUNTIME_ERROR=CONFIG_INVALID",
        )

    def test_check_config_reports_only_sanitized_status(self) -> None:
        environment = _hosted_environment()
        sentinel = "step-r2-launcher-secret-sentinel"
        environment["OPENROUTER_API_KEY"] = sentinel
        with contextlib.redirect_stdout(io.StringIO()) as stdout, contextlib.redirect_stderr(
            io.StringIO()
        ) as stderr:
            result = launcher.main(("check-config",), environment=environment)
        rendered = stdout.getvalue() + stderr.getvalue()
        self.assertEqual(result, 0)
        self.assertIn("MEMORY_PATCH_RUNTIME_CONFIG=VALID", rendered)
        self.assertNotIn(sentinel, rendered)

    def test_missing_provider_credential_is_sanitized_before_database_work(self) -> None:
        environment = _hosted_environment()
        environment.pop("OPENROUTER_API_KEY")
        with contextlib.redirect_stderr(io.StringIO()) as stderr:
            result = launcher.main(("serve",), environment=environment)
        self.assertEqual(result, 2)
        self.assertEqual(
            stderr.getvalue().strip(),
            "MEMORY_PATCH_RUNTIME_ERROR=PROVIDER_CREDENTIAL_MISSING",
        )

    def test_test_mode_cannot_be_served(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()) as stderr:
            result = launcher.main(
                ("serve",),
                environment={RUNTIME_MODE_ENV: "TEST"},
                dependency_check=lambda: object(),
                server_runner=lambda settings: 0,
            )
        self.assertEqual(result, 2)
        self.assertEqual(
            stderr.getvalue().strip(),
            "MEMORY_PATCH_RUNTIME_ERROR=CONFIG_INVALID",
        )

    def test_server_exit_status_is_propagated(self) -> None:
        calls: list[object] = []

        def dependencies():
            calls.append("dependencies")
            return object()

        def server(settings):
            calls.append(settings)
            return 17

        async def database(settings):
            calls.append("database")
            return _database_summary()

        result = launcher.main(
            ("serve",),
            environment=_hosted_environment(),
            dependency_check=dependencies,
            database_preparer=database,
            server_runner=server,
        )
        self.assertEqual(result, 17)
        self.assertEqual(calls[0], "dependencies")
        self.assertEqual(calls[1], "database")
        self.assertEqual(calls[2].bind_host, "127.0.0.1")

    def test_unexpected_server_failure_reveals_only_error_class(self) -> None:
        sentinel = "step-r2-provider-secret-never-print"

        def failure(settings):
            raise RuntimeError(sentinel)

        with contextlib.redirect_stderr(io.StringIO()) as stderr:
            result = launcher.main(
                ("serve",),
                environment=_hosted_environment(),
                dependency_check=lambda: object(),
                database_preparer=_prepared_database,
                server_runner=failure,
            )
        self.assertEqual(result, 3)
        self.assertEqual(
            stderr.getvalue().strip(),
            "MEMORY_PATCH_RUNTIME_ERROR=RUNTIMEERROR",
        )
        self.assertNotIn(sentinel, stderr.getvalue())

    def test_database_failure_prevents_server_start(self) -> None:
        calls: list[str] = []

        async def database(settings):
            raise RuntimeAssemblyError(RuntimeErrorCode.DATABASE_MIGRATION_FAILED)

        with contextlib.redirect_stderr(io.StringIO()) as stderr:
            result = launcher.main(
                ("serve",),
                environment=_hosted_environment(),
                dependency_check=lambda: calls.append("dependencies"),
                database_preparer=database,
                server_runner=lambda settings: calls.append("server") or 0,
            )
        self.assertEqual(result, 2)
        self.assertEqual(calls, ["dependencies"])
        self.assertEqual(
            stderr.getvalue().strip(),
            "MEMORY_PATCH_RUNTIME_ERROR=DATABASE_MIGRATION_FAILED",
        )

    def test_dependency_preflight_error_code_is_preserved(self) -> None:
        def failure():
            raise RuntimeAssemblyError(RuntimeErrorCode.DEPENDENCY_MISSING)

        with contextlib.redirect_stderr(io.StringIO()) as stderr:
            result = launcher.main(
                ("serve",),
                environment=_hosted_environment(),
                dependency_check=failure,
            )
        self.assertEqual(result, 2)
        self.assertIn("DEPENDENCY_MISSING", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
