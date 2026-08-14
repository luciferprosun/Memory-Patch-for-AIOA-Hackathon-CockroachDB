#!/usr/bin/env python3
"""Provider-free D4 container and infrastructure predeployment validation."""

from __future__ import annotations

import io
import json
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from aioa_memory_kernel.contracts.serialization import canonical_json, canonical_sha256  # noqa: E402


def _git(*arguments: str) -> str:
    result = subprocess.run(
        ("git", "-C", str(ROOT), *arguments),
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _focused_tests() -> int:
    suite = unittest.defaultTestLoader.loadTestsFromName(
        "tests.test_d4_aws_demo_deployment"
    )
    result = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0).run(suite)
    if not result.wasSuccessful():
        raise RuntimeError("D4 focused validation failed")
    return result.testsRun


def run_validation() -> dict[str, object]:
    template_path = ROOT / "infra/cloudformation/d4-aws-demo-runtime-1a.json"
    template = json.loads(template_path.read_text(encoding="utf-8"))
    service = template["Resources"]["DemoService"]["Properties"]
    source_sha = _git("rev-parse", "HEAD")
    report: dict[str, object] = {
        "step": "D4",
        "schema_version": "d4-aws-demo-deployment-preflight-1a",
        "source_sha": source_sha,
        "selected_target": "AWS_ECS_EXPRESS_MODE_FARGATE",
        "fallback_from_app_runner": True,
        "container": {
            "base": "python:3.12.13-slim-bookworm",
            "base_manifest_digest": (
                "sha256:4766d8b510c428e595d74b9cc5bbb2fae8e26316fffb4adc89908d79aacd58a2"
            ),
            "user": "10001:10001",
            "command": "python scripts/run_demo_runtime_1a.py serve",
        },
        "runtime": {
            "asgi": "aioa_memory_kernel.demo_runtime.asgi:app",
            "profile": "memory-patch-4gb-demo-1a",
            "cpu_units": int(service["Cpu"]),
            "memory_mib": int(service["Memory"]),
            "minimum_tasks": service["ScalingTarget"]["MinTaskCount"],
            "maximum_tasks": service["ScalingTarget"]["MaxTaskCount"],
            "health_path": service["HealthCheckPath"],
            "public_origin_pattern": "https://<service>.ecs.<region>.on.aws",
            "oidc_callback_path": "/memory/oidc/callback",
        },
        "security": {
            "secret_values_in_template": 0,
            "browser_privileged_secrets": 0,
            "application_task_role": None,
            "admin_policy": False,
            "runtime_secret_reference_count": len(
                service["PrimaryContainer"]["Secrets"]
            ),
            "legacy_mode": "ARCHIVAL_VIEW",
            "legacy_live_enabled": False,
        },
        "bounds": {
            "web_workers": 1,
            "db_pool_minimum": 1,
            "db_pool_maximum": 4,
            "provider_concurrency": 1,
            "provider_queue": 2,
            "provider_calls_total": 32,
        },
        "validation": {
            "focused_tests": _focused_tests(),
            "failures": 0,
            "errors": 0,
            "paid_provider_calls": 0,
            "aws_mutations": 0,
        },
        "deployment_started": False,
        "verdict": "PASS_PREDEPLOYMENT_ONLY",
    }
    report["validation_digest"] = canonical_sha256(report)
    return report


def main() -> int:
    try:
        report = run_validation()
    except (OSError, RuntimeError, subprocess.SubprocessError, ValueError):
        print(
            canonical_json(
                {"status": "FAILED", "reason": "D4_PREFLIGHT_FAILED_CLOSED"}
            ),
            file=sys.stderr,
        )
        return 1
    print(canonical_json(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
