#!/usr/bin/env python3
"""Controlled launcher for ``aioa_memory_kernel.demo_runtime.asgi:app``.

R2-R5 bind ASGI, CockroachDB, durable judge sessions, and the guarded pinned
provider. ``check-config`` is network-free. ``prepare-database`` is the
explicit migration/app-role preflight and never retains migration authority.
``serve`` cannot use test adapters or an unarmed provider budget epoch.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

try:
    from aioa_memory_kernel.demo_runtime.composition import (  # noqa: E402
        require_default_runtime_dependencies,
    )
    from aioa_memory_kernel.demo_runtime.database import (  # noqa: E402
        prepare_runtime_database,
    )
    from aioa_memory_kernel.demo_runtime.config import (  # noqa: E402
        DATABASE_ACQUISITION_TIMEOUT_ENV,
        DATABASE_ALLOW_INSECURE_LOCAL_ENV,
        DATABASE_CONNECTION_TIMEOUT_ENV,
        DATABASE_MIGRATION_TIMEOUT_ENV,
        DATABASE_POOL_MAX_ENV,
        DATABASE_POOL_MIN_ENV,
        DATABASE_STATEMENT_TIMEOUT_ENV,
        OIDC_CLIENT_ID_ENV,
        OIDC_ISSUER_ENV,
        PROVIDER_BUDGET_EPOCH_ENV,
        PROVIDER_TENANT_ID_ENV,
        PUBLIC_ORIGIN_ENV,
        RUNTIME_BIND_HOST_ENV,
        RUNTIME_MODE_ENV,
        RUNTIME_PORT_ENV,
        RuntimeAssemblyError,
        RuntimeErrorCode,
        RuntimeMode,
        RuntimeSettings,
    )
    from aioa_memory_kernel.security.redaction import redact_exception  # noqa: E402
except ImportError:
    if __name__ == "__main__":
        print("MEMORY_PATCH_RUNTIME_ERROR=DEPENDENCY_MISSING", file=sys.stderr)
        raise SystemExit(2) from None
    raise


ASGI_TARGET = "aioa_memory_kernel.demo_runtime.asgi:app"


def _verify_repository(root: Path) -> None:
    if not isinstance(root, Path):
        raise RuntimeAssemblyError(RuntimeErrorCode.CONFIG_INVALID)
    try:
        resolved = root.resolve(strict=True)
    except OSError:
        raise RuntimeAssemblyError(RuntimeErrorCode.CONFIG_INVALID) from None
    required = (
        resolved / "AGENTS.md",
        resolved / "requirements-runtime.txt",
        resolved / "requirements-ui.txt",
        resolved / "src" / "aioa_memory_kernel" / "demo_runtime" / "asgi.py",
        resolved / "src" / "aioa_memory_kernel" / "personal_memory_ui" / "web.py",
    )
    if not resolved.is_dir() or any(not path.is_file() for path in required):
        raise RuntimeAssemblyError(RuntimeErrorCode.CONFIG_INVALID)


def _configuration_preflight(
    environment: Mapping[str, str],
    *,
    repository_root: Path,
) -> RuntimeSettings:
    _verify_repository(repository_root)
    settings = RuntimeSettings.from_mapping(environment)
    if settings.mode is not RuntimeMode.TEST:
        settings.require_provider()
        settings.require_provider_guard()
    try:
        importlib.import_module("uvicorn")
        importlib.import_module("psycopg")
        importlib.import_module("psycopg_pool")
        importlib.import_module("aioa_memory_kernel.demo_runtime.asgi")
    except Exception:
        raise RuntimeAssemblyError(RuntimeErrorCode.DEPENDENCY_MISSING) from None
    return settings


def _run_uvicorn(settings: RuntimeSettings) -> int:
    import uvicorn

    config = uvicorn.Config(
        ASGI_TARGET,
        host=settings.bind_host,
        port=settings.port,
        workers=1,
        lifespan="on",
        log_level=settings.profile.logging.level.casefold(),
        access_log=False,
    )
    server = uvicorn.Server(config)
    server.run()
    return 0 if server.started else 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the canonical Memory Patch ASGI application with R3 "
            "CockroachDB, R4 durable sessions, and R5 provider guardrails."
        ),
        epilog=(
            "Configuration names: "
            f"{RUNTIME_MODE_ENV}, {RUNTIME_BIND_HOST_ENV}, {RUNTIME_PORT_ENV}, "
            f"{OIDC_ISSUER_ENV}, {OIDC_CLIENT_ID_ENV}, {PUBLIC_ORIGIN_ENV}, "
            "DATABASE_URL_APP, DATABASE_URL_MIGRATOR, "
            f"{DATABASE_ALLOW_INSECURE_LOCAL_ENV}, {DATABASE_POOL_MIN_ENV}, "
            f"{DATABASE_POOL_MAX_ENV}, {DATABASE_ACQUISITION_TIMEOUT_ENV}, "
            f"{DATABASE_CONNECTION_TIMEOUT_ENV}, {DATABASE_STATEMENT_TIMEOUT_ENV}, "
            f"{DATABASE_MIGRATION_TIMEOUT_ENV}, OPENROUTER_API_KEY, "
            f"{PROVIDER_BUDGET_EPOCH_ENV}, {PROVIDER_TENANT_ID_ENV}. "
            "Never pass secret values as command-line arguments."
        ),
    )
    parser.add_argument(
        "command",
        choices=("check-config", "prepare-database", "serve"),
        help=(
            "validate configuration, explicitly prepare CockroachDB, or start "
            "the canonical ASGI server"
        ),
    )
    return parser


def _prepare_database(
    settings: RuntimeSettings,
    database_preparer: Callable[[RuntimeSettings], object],
) -> object:
    summary = asyncio.run(database_preparer(settings))
    required = (
        "discovered",
        "applied",
        "replay_skipped",
        "failures",
    )
    if any(
        not isinstance(getattr(summary, name, None), int)
        or isinstance(getattr(summary, name, None), bool)
        for name in required
    ):
        raise RuntimeAssemblyError(RuntimeErrorCode.DATABASE_MIGRATION_FAILED)
    return summary


def main(
    argv: Sequence[str] | None = None,
    *,
    environment: Mapping[str, str] | None = None,
    repository_root: Path = REPOSITORY_ROOT,
    dependency_check: Callable[[], object] = require_default_runtime_dependencies,
    database_preparer: Callable[[RuntimeSettings], object] = prepare_runtime_database,
    server_runner: Callable[[RuntimeSettings], int] = _run_uvicorn,
) -> int:
    arguments = _parser().parse_args(argv)
    source = os.environ if environment is None else environment
    try:
        settings = _configuration_preflight(source, repository_root=repository_root)
        if arguments.command == "check-config":
            print("MEMORY_PATCH_RUNTIME_CONFIG=VALID")
            return 0
        if settings.mode is RuntimeMode.TEST:
            raise RuntimeAssemblyError(RuntimeErrorCode.CONFIG_INVALID)
        if arguments.command == "prepare-database":
            summary = _prepare_database(settings, database_preparer)
            print("MEMORY_PATCH_RUNTIME_DATABASE=READY")
            for name in ("discovered", "applied", "replay_skipped", "failures"):
                print(f"MIGRATIONS_{name.upper()}={getattr(summary, name)}")
            return 0
        dependency_check()
        # The controlled launcher owns migration-before-server-start. This
        # keeps the ASGI readiness window for the already prepared runtime and
        # ensures no traffic can arrive while the operations credential exists.
        _prepare_database(settings, database_preparer)
        result = server_runner(settings)
        if not isinstance(result, int) or isinstance(result, bool) or not 0 <= result <= 255:
            raise RuntimeAssemblyError(RuntimeErrorCode.STARTUP_FAILED)
        return result
    except RuntimeAssemblyError as error:
        print(f"MEMORY_PATCH_RUNTIME_ERROR={error.sanitized_code}", file=sys.stderr)
        return 2
    except Exception as error:
        print(f"MEMORY_PATCH_RUNTIME_ERROR={redact_exception(error)}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
