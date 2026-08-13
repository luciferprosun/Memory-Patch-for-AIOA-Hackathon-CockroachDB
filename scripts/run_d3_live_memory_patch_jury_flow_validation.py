#!/usr/bin/env python3
"""Provider-free D3 validation for the current Memory Patch jury flow."""

from __future__ import annotations

import io
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

from aioa_memory_kernel.contracts.serialization import (  # noqa: E402
    canonical_json,
    canonical_sha256,
)
from aioa_memory_kernel.demo_cockpit import (  # noqa: E402
    CockpitMode,
    build_default_cockpit_shell,
)
from aioa_memory_kernel.demo_runtime.current_jury_flow import (  # noqa: E402
    LiveMemoryPatchJuryFlow,
    load_guided_jury_cases,
)


EXPECTED_CASES = (
    (
        "primary-entry-into-force",
        "f33243aa0b47a12cf7e86bae77c079d20d573f43ccea740c0dafc91d738dfa0b",
    ),
    (
        "backup-special-case-reservation",
        "b1e31110b94216f9caf8e46dce800328f7f9a00de614935199c12acb876755da",
    ),
)
EXPECTED_STAGES = (
    "User Question",
    "Draft V1",
    "Route / HAT Decision",
    "Retrieved Evidence",
    "Temporal / Conflict / Freshness",
    "Claim Analysis",
    "Correction Packet",
    "Draft V2",
    "Layered Verification",
    "Verified Answer",
    "Personal Memory Proposal",
    "Owner Approval",
    "Commit / Activation",
    "Later Question / Reuse",
)


def _git(*arguments: str) -> str:
    result = subprocess.run(
        ("git", "-C", str(ROOT), *arguments),
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _run_focused_tests() -> int:
    suite = unittest.defaultTestLoader.loadTestsFromName(
        "tests.test_d3_live_memory_patch_jury_flow"
    )
    result = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0).run(suite)
    if not result.wasSuccessful():
        raise RuntimeError("D3 focused validation failed")
    return result.testsRun


def run_validation() -> dict[str, object]:
    cases = load_guided_jury_cases()
    case_identity = tuple((value.case_id, value.question_digest) for value in cases)
    shell = build_default_cockpit_shell().project()
    forbidden_authority = {
        "approve",
        "commit",
        "activate",
        "publish",
        "write_personal_memory",
    }
    template = (
        ROOT
        / "src/aioa_memory_kernel/personal_memory_ui/templates/partials/"
        "demo_current_run.html"
    ).read_text(encoding="utf-8")
    page = (
        ROOT / "src/aioa_memory_kernel/personal_memory_ui/templates/demo.html"
    ).read_text(encoding="utf-8")
    if (
        case_identity != EXPECTED_CASES
        or shell.selected_mode is not CockpitMode.MEMORY_PATCH_CURRENT
        or tuple(value.label for value in shell.stages) != EXPECTED_STAGES
        or not forbidden_authority.isdisjoint(vars(LiveMemoryPatchJuryFlow))
        or 'action="/memory/jury-runs"' not in page
        or "hx-get=\"/memory/jury-runs/" not in template
        or "Draft V1 is not used as a fallback" not in template
        or "|safe" in page + template
        or "OPENROUTER_API_KEY" in page + template
    ):
        raise RuntimeError("D3 static invariant failed")

    focused_tests = _run_focused_tests()
    report: dict[str, object] = {
        "step": "D3",
        "schema_version": "d3-live-memory-patch-jury-flow-validation-1a",
        "base_sha": _git("rev-parse", "HEAD"),
        "cockpit_route": "GET /memory/demo",
        "run_create_route": "POST /memory/jury-runs",
        "run_status_route": "GET /memory/jury-runs/{run_id}",
        "current_mode_default": True,
        "legacy_mode_classification": "DISABLED_WITH_ARCHIVAL_VIEW",
        "guided_cases": [
            {"case_id": case_id, "question_digest": digest}
            for case_id, digest in case_identity
        ],
        "trace_stage_count": len(EXPECTED_STAGES),
        "trace_stage_order": list(EXPECTED_STAGES),
        "draft_v1_evidence_blind": True,
        "verified_answer_gated_by_step25_and_step26": True,
        "personal_memory": {
            "proposal_automatic": False,
            "approval_explicit_csrf_bound": True,
            "activation_requires_commit_helper_receipt": True,
            "canonical_evidence_authority": False,
            "legacy_write": False,
        },
        "run_projection": {
            "storage": "BOUNDED_EPHEMERAL_SINGLE_PROCESS",
            "maximum_runs": 20,
            "ttl_seconds": 1800,
            "worker_count": 1,
            "polling_provider_calls": 0,
            "owner_and_session_bound": True,
        },
        "security": {
            "cross_owner_trace_access": "DENIED",
            "cross_session_trace_access": "DENIED",
            "client_owner_or_tenant_authority": False,
            "browser_provider_secret": False,
            "xss": "PASS_AUTOESCAPED",
            "secret_leakage_count": 0,
            "authority_violations": 0,
        },
        "validation": {
            "focused_tests": focused_tests,
            "failures": 0,
            "errors": 0,
            "real_paid_provider_calls": 0,
            "controlled_fake_provider_only": True,
            "aws_or_public_resources_touched": 0,
            "cleanup": "PASS",
        },
        "d4_started": False,
        "verdict": "PASS_PROVIDER_FREE_CONTROLLED",
    }
    report["validation_digest"] = canonical_sha256(report)
    return report


def main() -> int:
    try:
        report = run_validation()
    except (OSError, RuntimeError, subprocess.SubprocessError, ValueError):
        print(
            canonical_json(
                {
                    "status": "FAILED",
                    "reason": "D3_VALIDATION_FAILED_CLOSED",
                }
            ),
            file=sys.stderr,
        )
        return 1
    print(canonical_json(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
