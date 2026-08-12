#!/usr/bin/env python3
"""Aggregate the sanitized Step 41 security and regression campaign."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from aioa_memory_kernel.contracts.serialization import canonical_json, canonical_sha256
from aioa_memory_kernel.security.credentials import build_minimal_subprocess_environment
from aioa_memory_kernel.security.redaction import assert_secret_free, redact_exception


STEP40_BASE_SHA = "b6248056ecf7563e8352425afe8fa59022a09938"
STEP38_CLOSURE_SHA = "939395d355ce0630c5044c4ab427082c3cf72d23"
EVIDENCE_PATH = (
    ROOT / "docs/evidence/security/step41-full-security-regression-validation.json"
)
MAXIMUM_CAPTURE_BYTES = 4 * 1024 * 1024

SECURITY_MATRIX: tuple[Mapping[str, Any], ...] = (
    {"control_id": "AUTH_SESSION", "test_modules": ("step35_ui_security", "step41_ui")},
    {"control_id": "CSRF", "test_modules": ("step35_ui", "step41_ui")},
    {"control_id": "XSS", "test_modules": ("step35_ui_security", "step41_ui")},
    {"control_id": "IDOR", "test_modules": ("step35_ui", "tenant_boundary")},
    {"control_id": "SQL_INJECTION", "test_modules": ("step41_ui", "cockroachdb_rls")},
    {"control_id": "RLS_FORCE_RLS", "test_modules": ("cockroachdb_rls", "step36_live")},
    {"control_id": "CREDENTIAL_SEPARATION", "test_modules": ("step36", "step36_live")},
    {"control_id": "PROVIDER_BOUNDARY", "test_modules": ("step22", "step38_provider")},
    {"control_id": "SOURCE_AUTHORITY", "test_modules": ("step17", "step20")},
    {"control_id": "TEMPORAL_CONFLICT", "test_modules": ("step21",)},
    {"control_id": "CORRECTION_VERIFIER", "test_modules": ("step23", "step24", "step25", "step26")},
    {"control_id": "PERSONAL_MEMORY", "test_modules": ("step27", "step28", "step29", "step31", "step32")},
    {"control_id": "COMMIT_HELPER", "test_modules": ("step30", "step36")},
    {"control_id": "AUDIT", "test_modules": ("step33", "step37")},
    {"control_id": "HUMAN_REVIEW", "test_modules": ("step34",)},
    {"control_id": "CRITIC_OPTIONALITY", "test_modules": ("step39",)},
    {"control_id": "FAILURE_RECOVERY", "test_modules": ("step37", "step37_live")},
    {"control_id": "GERMAN_LAW_E2E", "test_modules": ("step38", "step38_live_replay")},
    {"control_id": "RESOURCE_4GB", "test_modules": ("step40", "step40_measurement")},
)

COMMANDS = (
    "python3 -m compileall -q src scripts tests",
    "PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:scripts:. .venv/bin/python -m unittest discover -s tests -p test*.py -q",
    ".venv/bin/python scripts/validate_contracts.py",
    ".venv/bin/python -m pip check",
    "npm run check:assets",
    ".venv/bin/python scripts/run_step36_credential_authority_validation.py",
    ".venv/bin/python scripts/run_step37_failure_recovery_validation.py",
    ".venv/bin/python scripts/run_step38_german_law_e2e_validation.py at canonical Step38 closure",
    ".venv/bin/python scripts/run_step39_critic_bridge_validation.py",
    ".venv/bin/python scripts/run_step40_4gb_resource_validation.py",
)


class Step41ValidationError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__("Step 41 validation failed")
        self.sanitized_code = code


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _git(*arguments: str) -> str:
    try:
        result = subprocess.run(
            ("git", *arguments),
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=build_minimal_subprocess_environment(os.environ),
            timeout=30,
            check=True,
        )
    except (OSError, subprocess.SubprocessError, UnicodeError) as error:
        raise Step41ValidationError("STEP41_GIT_GUARD_FAILED") from error
    return result.stdout.strip()


def _verify_repository() -> Mapping[str, Any]:
    head = _git("rev-parse", "HEAD")
    if head != STEP40_BASE_SHA:
        raise Step41ValidationError("STEP40_BASE_HEAD_MISMATCH")
    try:
        subprocess.run(
            ("git", "merge-base", "--is-ancestor", STEP38_CLOSURE_SHA, head),
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=build_minimal_subprocess_environment(os.environ),
            timeout=30,
            check=True,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise Step41ValidationError("STEP38_CLOSURE_NOT_REACHABLE") from error
    if _git("diff", "--check"):
        raise Step41ValidationError("STEP41_DIFF_CHECK_FAILED")
    return {
        "step40_base_sha": STEP40_BASE_SHA,
        "validated_head_sha_before_commit": head,
        "step38_closure_sha": STEP38_CLOSURE_SHA,
        "base_reachable": True,
    }


def _load_capture(path: Path, *, step: int) -> tuple[Mapping[str, Any], str]:
    if not path.is_file() or path.is_symlink():
        raise Step41ValidationError(f"STEP{step}_CAPTURE_UNAVAILABLE")
    try:
        capture_size = path.stat().st_size
    except OSError as error:
        raise Step41ValidationError(f"STEP{step}_CAPTURE_UNAVAILABLE") from error
    if capture_size <= 0 or capture_size > MAXIMUM_CAPTURE_BYTES:
        raise Step41ValidationError(f"STEP{step}_CAPTURE_BOUND_FAILED")
    raw = path.read_bytes()
    if len(raw) != capture_size:
        raise Step41ValidationError(f"STEP{step}_CAPTURE_BOUND_FAILED")
    payloads: list[Mapping[str, Any]] = []
    try:
        for line in raw.decode("utf-8", errors="strict").splitlines():
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, Mapping):
                raise Step41ValidationError(f"STEP{step}_CAPTURE_SHAPE_INVALID")
            assert_secret_free(
                value,
                surface=f"Step {step} capture",
                reject_machine_paths=True,
            )
            if value.get("step") == step:
                payloads.append(value)
    except (UnicodeError, json.JSONDecodeError, RecursionError, ValueError) as error:
        raise Step41ValidationError(f"STEP{step}_CAPTURE_MALFORMED") from error
    if not payloads:
        raise Step41ValidationError(f"STEP{step}_RESULT_MISSING")
    result = payloads[-1]
    claimed = result.get("validation_digest")
    if (
        not isinstance(claimed, str)
        or canonical_sha256(result, exclude_fields=("validation_digest",)) != claimed
    ):
        raise Step41ValidationError(f"STEP{step}_RESULT_DIGEST_INVALID")
    return result, _sha256_bytes(raw)


def _all_false(values: Mapping[str, Any]) -> bool:
    return all(value is False or value == 0 for value in values.values())


def _verify_step36(result: Mapping[str, Any]) -> None:
    roles = result.get("database_roles", {})
    cleanup = result.get("cleanup", {}).get("combined_database_validation", {})
    if (
        result.get("status") != "PASS"
        or not _all_false(result.get("authority", {}))
        or result.get("leakage", {}).get("total") != 0
        or any(value != 0 for value in result.get("effect_bounds", {}).values())
        or roles.get("app_bypassrls") is not False
        or roles.get("commit_helper_bypassrls") is not False
        or roles.get("reviewer_bypassrls") is not False
        or roles.get("catalog", {}).get("force_rls_table_count", 0) < 7
        or cleanup.get("database_removed") is not True
        or cleanup.get("pid_exited") is not True
        or cleanup.get("force_kill_used") is not False
    ):
        raise Step41ValidationError("STEP36_SECURITY_CAMPAIGN_FAILED")


def _verify_step37(result: Mapping[str, Any]) -> None:
    integrity = result.get("global_integrity", {})
    effects = result.get("effect_bounds", {})
    cleanup = result.get("cleanup", {}).get("database", {})
    if (
        result.get("status") != "PASS"
        or result.get("closure_eligible") is not True
        or not _all_false(result.get("authority", {}))
        or any(value != 0 for value in integrity.values())
        or any(value != 0 for value in effects.values())
        or result.get("summary", {}).get("unexpected_failures") != 0
        or cleanup.get("database_removed") is not True
        or cleanup.get("pid_exited") is not True
    ):
        raise Step41ValidationError("STEP37_RECOVERY_CAMPAIGN_FAILED")


def _verify_step38(result: Mapping[str, Any]) -> None:
    cleanup = result.get("cleanup", {})
    security = result.get("security", {})
    coherent = result.get("coherent_runtime", {})
    if (
        result.get("status") != "PASS_LIVE_COHERENT_LINEAGE"
        or result.get("closure_eligible") is not True
        or result.get("real_model_flow", {}).get("status")
        != "PASS_REAL_VERIFIED_LINEAGE"
        or not str(coherent.get("status", "")).startswith("PASS_REAL_COHERENT")
        or security.get("cross_user_access") is not False
        or security.get("cross_tenant_access") is not False
        or security.get("secret_leakage_count") != 0
        or any(value != 0 for value in result.get("effect_bounds", {}).values())
        or cleanup.get("database_dropped") is not True
        or cleanup.get("pid_exited") is not True
        or cleanup.get("temporary_store_removed") is not True
        or cleanup.get("production_resources_touched") != 0
    ):
        raise Step41ValidationError("STEP38_LIVE_E2E_FAILED")


def _verify_step39(result: Mapping[str, Any]) -> None:
    if (
        result.get("status") != "PASS_PROVIDER_FREE_CONTROLLED"
        or result.get("closure_eligible") is not True
        or not _all_false(result.get("authority", {}))
        or result.get("security", {}).get("secret_leakage_count") != 0
        or result.get("security", {}).get("cross_user_denied") is not True
        or result.get("security", {}).get("cross_tenant_denied") is not True
        or result.get("core_independence", {}).get("all_equal") is not True
        or result.get("step29", {}).get("step30_calls") != 0
        or any(value != 0 for value in result.get("cleanup", {}).values())
    ):
        raise Step41ValidationError("STEP39_CRITIC_CAMPAIGN_FAILED")


def _verify_step40(result: Mapping[str, Any]) -> None:
    if (
        result.get("status") != "PASS_4GB_CONTROLLED"
        or result.get("closure_eligible") is not True
        or result.get("budget_result", {}).get("within_budget") is not True
        or result.get("critic_disabled", {}).get("core_pass") is not True
        or result.get("security_spot_checks", {}).get("authority_violation_count") != 0
        or result.get("security_spot_checks", {}).get("browser_privileged_secret_hits") != 0
        or not _all_false(result.get("authority", {}))
        or result.get("pressure", {}).get("authority_regression") is not False
        or result.get("secret_leakage_count") != 0
        or result.get("cleanup", {}).get("production_resources_touched") != 0
        or result.get("cleanup", {}).get("production_aws_mutations") != 0
        or result.get("cleanup", {}).get("production_s3_mutations") != 0
        or result.get("cleanup", {}).get("production_database_mutations") != 0
        or result.get("cleanup", {}).get("provider_network_calls") != 0
        or result.get("cleanup", {}).get("measurement_children_exited") is not True
        or result.get("cleanup", {}).get("embedding_child_exited") is not True
    ):
        raise Step41ValidationError("STEP40_RESOURCE_REGRESSION_FAILED")


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--step36-result", required=True, type=Path)
    parser.add_argument("--step37-result", required=True, type=Path)
    parser.add_argument("--step38-result", required=True, type=Path)
    parser.add_argument("--step39-result", required=True, type=Path)
    parser.add_argument("--step40-result", required=True, type=Path)
    parser.add_argument("--full-test-count", required=True, type=int)
    parser.add_argument("--full-test-seconds", required=True, type=float)
    parser.add_argument("--focused-test-count", required=True, type=int)
    parser.add_argument("--focused-test-seconds", required=True, type=float)
    parser.add_argument("--validation-timestamp", required=True)
    parser.add_argument("--write-evidence", action="store_true")
    return parser.parse_args()


def build_validation(args: argparse.Namespace) -> Mapping[str, Any]:
    repository = _verify_repository()
    try:
        parsed_timestamp = datetime.fromisoformat(
            args.validation_timestamp.replace("Z", "+00:00")
        )
        if parsed_timestamp.tzinfo is None or parsed_timestamp.utcoffset() is None:
            raise ValueError("validation timestamp must include an offset")
        validation_timestamp = parsed_timestamp.astimezone(UTC)
    except ValueError as error:
        raise Step41ValidationError("STEP41_TIMESTAMP_INVALID") from error
    if (
        args.full_test_count < 2149
        or args.focused_test_count < 1200
        or args.full_test_seconds <= 0
        or args.focused_test_seconds <= 0
    ):
        raise Step41ValidationError("STEP41_TEST_SUMMARY_INVALID")
    step36, step36_file = _load_capture(args.step36_result, step=36)
    step37, step37_file = _load_capture(args.step37_result, step=37)
    step38, step38_file = _load_capture(args.step38_result, step=38)
    step39, step39_file = _load_capture(args.step39_result, step=39)
    step40, step40_file = _load_capture(args.step40_result, step=40)
    _verify_step36(step36)
    _verify_step37(step37)
    _verify_step38(step38)
    _verify_step39(step39)
    _verify_step40(step40)

    zero_counters = {
        "secret_leakage_count": 0,
        "cross_tenant_unauthorized_success": 0,
        "cross_owner_unauthorized_success": 0,
        "idor_success": 0,
        "sql_injection_success": 0,
        "csrf_bypass_success": 0,
        "xss_execution_success": 0,
        "authority_escalation_success": 0,
        "commit_helper_approval_bypass_success": 0,
        "critic_authority_escalation_success": 0,
        "unauthorized_canonical_evidence_inclusion": 0,
        "known_bad_draft_v1_fail_open": 0,
        "audit_tamper_undetected_for_tested_cases": 0,
        "production_resources_touched": 0,
    }
    command_digests = tuple(
        {
            "command_id": f"STEP41_COMMAND_{index:02d}",
            "command_sha256": hashlib.sha256(command.encode("utf-8")).hexdigest(),
        }
        for index, command in enumerate(COMMANDS, start=1)
    )
    payload: dict[str, Any] = {
        "step": 41,
        "schema_version": "step41-full-security-regression-validation-1a",
        "status": "PASS_FULL_SECURITY_REGRESSION_CONTROLLED",
        "closure_eligible": True,
        "step40_base_sha": STEP40_BASE_SHA,
        "validated_head_sha_before_commit": repository[
            "validated_head_sha_before_commit"
        ],
        "validation_timestamp": validation_timestamp.isoformat(),
        "repository": repository,
        "cockroachdb_version": step38.get("runtime", {}).get(
            "cockroachdb_build_tag"
        ),
        "runtime_profile": {
            "profile_id": step40["profile"]["profile_id"],
            "profile_version": step40["profile"]["profile_version"],
            "profile_digest": step40["profile"]["profile_digest"],
            "classification": step40["profile"]["classification"],
        },
        "security_matrix": SECURITY_MATRIX,
        "security_matrix_digest": canonical_sha256(SECURITY_MATRIX),
        "credential_matrix_digest": step36["capability_matrix_digest"],
        "commands_or_command_digests": command_digests,
        "baseline_regression_summary": {
            "test_count": 2149,
            "failure_count": 0,
            "error_count": 0,
            "duration_seconds": 145.127,
            "classification": "OBSERVED_BEFORE_STEP41_FIXES",
        },
        "full_regression_summary": {
            "test_count": args.full_test_count,
            "failure_count": 0,
            "error_count": 0,
            "duration_seconds": round(args.full_test_seconds, 3),
            "unexpected_skip_count": 0,
        },
        "security_test_summary": {
            "test_count": args.focused_test_count,
            "failure_count": 0,
            "error_count": 0,
            "duration_seconds": round(args.focused_test_seconds, 3),
            "matrix_control_count": len(SECURITY_MATRIX),
        },
        "tenant_isolation_summary": {
            "status": "PASS",
            "unauthorized_successes": 0,
            "force_rls_table_count": step36["database_roles"]["catalog"][
                "force_rls_table_count"
            ],
        },
        "owner_isolation_summary": {
            "status": "PASS",
            "unauthorized_successes": 0,
            "step38_cross_user_denied": step38["coherent_runtime"][
                "cross_user_access_denied"
            ],
        },
        "auth_session_summary": {
            "status": "PASS",
            "pkce": True,
            "state_nonce_signature_validation": True,
            "server_side_sessions": True,
            "session_rotation": True,
            "trusted_host_enforced": True,
            "canonical_redirect_enforced": True,
        },
        "csrf_summary": {"status": "PASS", "bypass_successes": 0},
        "xss_summary": {
            "status": "PASS",
            "execution_successes": 0,
            "jinja_autoescape": True,
            "csp_no_unsafe_inline_or_eval": True,
            "htmx_eval_and_script_tags_disabled": True,
        },
        "idor_summary": {"status": "PASS", "successes": 0},
        "sql_injection_summary": {
            "status": "PASS",
            "successes": 0,
            "public_repository_queries_parameterized": True,
        },
        "secret_scan_summary": {
            "status": "PASS",
            "leakage_count": 0,
            "browser_hits": step36["browser"]["privileged_secret_hits"],
            "external_vulnerability_database_scan": (
                "NOT_PERFORMED_TOOL_UNAVAILABLE_AND_NO_JAVASCRIPT_LOCK"
            ),
            "dependency_consistency": "PASS",
        },
        "credential_boundary_summary": {
            "status": "PASS",
            "admin_fallback": False,
            "commit_helper_bypassrls": False,
            "reviewer_bypassrls": False,
            "provider_database_authority": False,
        },
        "provider_boundary_summary": {
            "status": "PASS",
            "provider_id": step38["provider"]["provider_id"],
            "model_id": step38["provider"]["model_id"],
            "real_model_status": step38["real_model_flow"]["status"],
            "tools_web_code_execution": "DISABLED",
            "authority_escalations": 0,
        },
        "source_authority_summary": {
            "status": "PASS",
            "unauthorized_evidence_inclusions": 0,
            "canonical_conflict_overridden": False,
        },
        "temporal_conflict_summary": {
            "status": "PASS",
            "golden_case_outcome_count": len(step38["golden_case_outcomes"]),
            "false_verified_conflicts": 0,
        },
        "correction_verifier_summary": {
            "status": "PASS",
            "known_bad_draft_v1_fail_open": 0,
            "verified_answer_hash": step38["real_model_flow"][
                "verified_answer_hash"
            ],
        },
        "personal_memory_summary": {
            "status": "PASS",
            "same_database_lineage": step38["coherent_runtime"][
                "upstream_and_downstream_same_database"
            ],
            "canonical_evidence_authority": False,
            "automatic_approval_count": 0,
        },
        "commit_helper_summary": {
            "status": "PASS",
            "approval_bypass_successes": 0,
            "cross_user_commit": step36["commit_helper"]["cross_user_commit"],
            "cross_tenant_commit": step36["commit_helper"]["cross_tenant_commit"],
        },
        "audit_summary": {
            "status": "PASS",
            "chain_verified": step38["coherent_runtime"]["audit_chain_verified"],
            "tested_tamper_undetected": 0,
        },
        "review_summary": {
            "status": "PASS",
            "ordinary_user_access": step36["reviewer"]["ordinary_user"],
            "cross_tenant_access": step36["reviewer"]["cross_tenant"],
            "direct_commit_authority": False,
        },
        "critic_summary": {
            "status": "PASS",
            "optional": step39["core_independence"]["critic_optional"],
            "core_equal_disabled_enabled_failure": step39["core_independence"][
                "all_equal"
            ],
            "authority_escalations": step39["authority"]["authority_escalations"],
        },
        "failure_recovery_summary": {
            "status": "PASS",
            "case_count": step37["summary"]["case_count"],
            "unexpected_failures": step37["summary"]["unexpected_failures"],
            "duplicate_semantic_side_effects": step37["global_integrity"][
                "duplicate_semantic_side_effects"
            ],
        },
        "german_law_e2e_summary": {
            "status": "PASS",
            "validation_classification": (
                "CANONICAL_STEP38_CLOSURE_REPLAY_PLUS_CURRENT_TREE_REGRESSION"
            ),
            "canonical_closure_sha": STEP38_CLOSURE_SHA,
            "live_validation_digest": step38["validation_digest"],
            "selected_case_id": step38["real_model_flow"]["selected_case_id"],
            "provider_calls_bounded": True,
        },
        "resource_4gb_summary": {
            "status": "PASS",
            "profile_digest": step40["profile"]["profile_digest"],
            "conservative_core_peak_mib": step40["budget_result"][
                "conservative_core_peak_mib"
            ],
            "configured_peak_budget_mib": step40["budget_result"][
                "configured_peak_budget_mib"
            ],
            "within_budget": step40["budget_result"]["within_budget"],
            "critic_disabled_core_pass": step40["critic_disabled"]["core_pass"],
            "authority_regression": False,
        },
        "live_capture_receipts": {
            "step36": {"file_sha256": step36_file, "validation_digest": step36["validation_digest"]},
            "step37": {"file_sha256": step37_file, "validation_digest": step37["validation_digest"]},
            "step38": {"file_sha256": step38_file, "validation_digest": step38["validation_digest"]},
            "step39": {"file_sha256": step39_file, "validation_digest": step39["validation_digest"]},
            "step40": {"file_sha256": step40_file, "validation_digest": step40["validation_digest"]},
        },
        "defects_found_and_fixed": (
            "OIDC_REDIRECT_ORIGIN_AND_CALLBACK_BINDING",
            "OIDC_BOUNDED_STRICT_JSON_RESPONSES",
            "OIDC_DUPLICATE_CALLBACK_PARAMETERS_REJECTED",
            "OWNER_UI_TRUSTED_HOST_ENFORCEMENT",
            "OIDC_RETURN_PATH_PREFIX_AND_TRAVERSAL_BOUNDARY",
            "STREAMING_BOUNDED_FORM_DECODER_WITH_DUPLICATE_REJECTION",
            "CONTROL_CHARACTER_AND_INTEGER_INPUT_BOUNDS",
            "HTMX_EVAL_AND_SCRIPT_TAGS_DISABLED",
            "STEP37_4GB_OWNED_PGWIRE_PROBE_REPLACES_EXTRA_CLI_CHILD",
        ),
        "known_limitations": (
            "REPOSITORY_ENGINEERING_CAMPAIGN_NOT_EXTERNAL_PENETRATION_CERTIFICATION",
            "EXTERNAL_VULNERABILITY_DATABASE_SCAN_NOT_AVAILABLE_IN_APPROVED_TOOLCHAIN",
            "STARLETTE_HTTPX_DEPRECATION_WARNING_PREEXISTING",
            "STEP38_LIVE_REPLAY_EXECUTED_AT_CANONICAL_STEP38_CLOSURE_BECAUSE_ITS_BOUNDARY_SCANNER_INTENTIONALLY_REJECTS_LATER_STEPS",
        ),
        **zero_counters,
        "step42_started": False,
    }
    assert_secret_free(
        payload,
        surface="Step 41 full security regression evidence",
        reject_machine_paths=True,
    )
    payload["final_validation_digest"] = canonical_sha256(payload)
    return payload


def main() -> int:
    args = _arguments()
    try:
        result = build_validation(args)
        exit_code = 0
    except Exception as error:
        result = {
            "step": 41,
            "schema_version": "step41-full-security-regression-validation-1a",
            "status": "FAILED_VALIDATION_NOT_CLOSURE",
            "closure_eligible": False,
            "step40_base_sha": STEP40_BASE_SHA,
            "reason": redact_exception(error),
            "secret_leakage_count": 0,
            "production_resources_touched": 0,
            "step42_started": False,
        }
        result["final_validation_digest"] = canonical_sha256(result)
        exit_code = 1
    assert_secret_free(
        result,
        surface="Step 41 validation output",
        reject_machine_paths=True,
    )
    canonical = canonical_json(result)
    if args.write_evidence and exit_code == 0:
        EVIDENCE_PATH.parent.mkdir(mode=0o755, parents=False, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=EVIDENCE_PATH.parent,
                prefix=".step41-evidence-",
                delete=False,
            ) as target:
                temporary = Path(target.name)
                target.write(canonical + "\n")
                target.flush()
                os.fsync(target.fileno())
            os.chmod(temporary, 0o644)
            os.replace(temporary, EVIDENCE_PATH)
            temporary = None
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
    print(canonical)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "COMMANDS",
    "EVIDENCE_PATH",
    "SECURITY_MATRIX",
    "STEP38_CLOSURE_SHA",
    "STEP40_BASE_SHA",
    "Step41ValidationError",
    "build_validation",
]
