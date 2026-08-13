#!/usr/bin/env python3
"""Run the final bounded Memory Patch judge demo or its verified replay.

Replay is the default and performs no network, database, or provider action.
Live mode is an explicitly cost-authorized evidence-blind model observation;
the independently verified correction lineage remains the frozen Step 38/42
trace and is never fabricated from a new model answer.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any


# The implementation lives with presentation assets so it does not mutate the
# Step 42 runtime-content freeze. It is deliberately not a production-runtime
# script and starts no service in replay mode.
ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from aioa_memory_kernel.contracts.serialization import (  # noqa: E402
    canonical_json,
    canonical_sha256,
)
from aioa_memory_kernel.modeling import (  # noqa: E402
    GenerationParameters,
    ModelAdapterError,
    PromptTemplate,
    ProviderTextRequest,
    TimeoutPolicy,
    load_approved_provider_spec,
    verify_provider_response_hash,
)
from aioa_memory_kernel.modeling.providers.openrouter import (  # noqa: E402
    OpenRouterDraftV1Adapter,
)
from aioa_memory_kernel.release_candidate import runtime_content_manifest  # noqa: E402
from aioa_memory_kernel.security.credentials import (  # noqa: E402
    CredentialPurpose,
    SecretValue,
    build_minimal_subprocess_environment,
)
from aioa_memory_kernel.security.redaction import (  # noqa: E402
    assert_secret_free,
    redact_exception,
)


STEP42_BASE_SHA = "f99057c601bfa41115185f52141ea327f3ef1aa1"
EXPECTED_REMOTE = (
    "https://github.com/luciferprosun/"
    "Memory-Patch-for-AIOA-Hackathon-CockroachDB.git"
)
EVIDENCE_PATH = (
    ROOT
    / "docs/evidence/demo/step43-documentation-demo-submission-validation.json"
)

FIXTURE_PATH = ROOT / "tests/fixtures/step38_german_law_cases.json"
STEP38_EVIDENCE_PATH = (
    ROOT / "docs/evidence/e2e/step38-german-law-full-e2e-validation.json"
)
STEP38_TRACE_PATH = ROOT / "docs/evidence/e2e/step38-german-law-demo-trace.json"
STEP39_EVIDENCE_PATH = (
    ROOT / "docs/evidence/critic/step39-critic-prompt-loop-bridge-validation.json"
)
STEP40_EVIDENCE_PATH = (
    ROOT / "docs/evidence/performance/step40-4gb-resource-validation.json"
)
STEP41_EVIDENCE_PATH = (
    ROOT / "docs/evidence/security/step41-full-security-regression-validation.json"
)
STEP42_EVIDENCE_PATH = (
    ROOT / "docs/evidence/release/step42-rc-backup-restore-validation.json"
)
STEP42_RC_MANIFEST_PATH = (
    ROOT / "docs/evidence/release/step42-rc-manifest-1a.json"
)

PRIMARY_CASE_ID = "primary-entry-into-force"
BACKUP_CASE_ID = "backup-special-case-reservation"
SUPPORTED_CASE_ID = "supported-entry-into-force-clean"
FAIL_CLOSED_CASE_IDS = (
    "temporal-unavailable-edge",
    "conflicting-ceiling-edge",
)
TRACE_STAGE_ORDER = (
    "QUESTION",
    "DRAFT_V1",
    "DETECTED_ISSUE",
    "SOURCE_EVIDENCE",
    "CORRECTION",
    "DRAFT_V2",
    "VERIFIED_ANSWER",
    "PERSONAL_MEMORY_PROPOSAL",
    "OWNER_APPROVAL",
    "ACTIVE_PATCH",
    "LATER_QUESTION",
    "CROSS_MODEL_REUSE",
)

PRIMARY_SYSTEM_INSTRUCTION = (
    "Beantworte die Frage ausschließlich aus deinem eigenen Modellwissen und "
    "ohne Quellen oder Werkzeuge. Antworte auf Deutsch in genau einem kurzen, "
    "eigenständigen Satz, der nur das Datum des Inkrafttretens nennt. Verwende "
    "keine Überschrift, kein Markdown, keine Zitate, keine Paragraphen, keine "
    "Erläuterung, keine Abkürzungsauflösung und keine Einschränkung."
)
BACKUP_SYSTEM_INSTRUCTION = (
    "Beantworte die Frage ausschließlich aus deinem eigenen Modellwissen und "
    "ohne Quellen oder Werkzeuge. Gib ausschließlich den gesamten, vollständig "
    "ausgefüllten deutschen Satz aus, einschließlich aller Wörter vor und nach "
    "der Lücke. Der ausgegebene Satz darf weder einen Platzhalter noch eine "
    "Einzelwortantwort enthalten. Verwende keine Überschrift, kein Markdown, "
    "keine Anführungszeichen, keine Quellenangabe, keine Erläuterung und keine "
    "zusätzliche Aussage."
)
PRIMARY_CANONICAL_DATE = "1. Januar 2024"
BACKUP_CANONICAL_TEXT = (
    "Für besondere Fälle behalte ich mir die Ernennung und Entlassung der unter "
    "I. genannten Beamtinnen und Beamten vor."
)
BACKUP_WRONG_TEXT = (
    "Für besondere Fälle behalte ich mir die Ernennung und Entlassung der unter "
    "I. genannten Beamtinnen und Beamten nicht vor."
)
CANONICAL_PROVISION_III = (
    "Diese Anordnung tritt am 1. Januar 2024 in Kraft. Frühere Anordnungen zum "
    "selben Gegenstand sind nicht mehr anzuwenden."
)

SUBMISSION_ARTIFACTS = (
    "README.md",
    "docs/README.md",
    "docs/SUBMISSION_INDEX_1A.md",
    "docs/architecture/MEMORY_PATCH_SYSTEM_OVERVIEW_1A.md",
    "docs/demo/MEMORY_PATCH_DEMO_GOLDEN_PATH_1A.md",
    "docs/demo/YOUTUBE_DEMO_SCRIPT_1A.md",
    "docs/operations/STEP_43_DEMO_AND_SUBMISSION_RUNBOOK_1A.md",
    "docs/submission/HACKATHON_SUBMISSION_PACKAGE_1A.md",
    "docs/demo/run_step43_demo.py",
    "tests/test_step43_documentation_demo.py",
)
MARKDOWN_LINK_SURFACES = tuple(
    path for path in SUBMISSION_ARTIFACTS if path.endswith(".md")
)
MAXIMUM_JSON_BYTES = 8 * 1024 * 1024
MAXIMUM_LIVE_PROVIDER_CALLS = 2
_MARKDOWN_LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)]+)\)")
_DATE_PATTERN = re.compile(r"\b\d{1,2}\.\s+[A-Za-zÄÖÜäöüß]+\s+\d{4}\b")


class Step43ValidationError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__("Step 43 demo validation failed")
        self.sanitized_code = code


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
    except OSError as error:
        raise Step43ValidationError("STEP43_REQUIRED_FILE_UNAVAILABLE") from error
    return digest.hexdigest()


def _load_json(path: Path) -> Mapping[str, Any]:
    try:
        size = path.stat().st_size
        if not path.is_file() or path.is_symlink() or not 0 < size <= MAXIMUM_JSON_BYTES:
            raise Step43ValidationError("STEP43_EVIDENCE_FILE_BOUND_FAILED")
        raw = path.read_bytes()
        if len(raw) != size:
            raise Step43ValidationError("STEP43_EVIDENCE_FILE_BOUND_FAILED")
        value = json.loads(raw.decode("utf-8", errors="strict"))
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise Step43ValidationError("STEP43_EVIDENCE_FILE_INVALID") from error
    if not isinstance(value, Mapping):
        raise Step43ValidationError("STEP43_EVIDENCE_FILE_INVALID")
    return value


def _verify_digest(
    value: Mapping[str, Any],
    field: str,
    *,
    exclude_fields: tuple[str, ...] | None = None,
) -> None:
    claimed = value.get(field)
    excluded = exclude_fields or (field,)
    if (
        not isinstance(claimed, str)
        or canonical_sha256(value, exclude_fields=excluded) != claimed
    ):
        raise Step43ValidationError("STEP43_REFERENCED_EVIDENCE_DIGEST_INVALID")


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
            timeout=60,
            check=True,
        )
    except (OSError, subprocess.SubprocessError, UnicodeError) as error:
        raise Step43ValidationError("STEP43_GIT_GUARD_FAILED") from error
    return result.stdout.strip()


def _repository_guard() -> Mapping[str, Any]:
    head = _git("rev-parse", "HEAD")
    branch = _git("branch", "--show-current")
    remote = _git("remote", "get-url", "origin")
    if branch != "main" or remote != EXPECTED_REMOTE:
        raise Step43ValidationError("STEP43_REPOSITORY_IDENTITY_MISMATCH")
    try:
        subprocess.run(
            ("git", "merge-base", "--is-ancestor", STEP42_BASE_SHA, head),
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=build_minimal_subprocess_environment(os.environ),
            timeout=30,
            check=True,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise Step43ValidationError("STEP42_BASE_NOT_REACHABLE") from error
    if _git("diff", "--check"):
        raise Step43ValidationError("STEP43_DIFF_CHECK_FAILED")
    return {
        "branch": branch,
        "remote": remote,
        "step42_base_reachable": True,
        "step42_base_sha": STEP42_BASE_SHA,
        "validated_head_sha_before_commit": head,
    }


def _verify_trace(trace: Mapping[str, Any], step38: Mapping[str, Any]) -> None:
    stages = trace.get("stages")
    if not isinstance(stages, list) or len(stages) != len(TRACE_STAGE_ORDER):
        raise Step43ValidationError("STEP43_TRACE_STAGE_SET_INVALID")
    for sequence, (stage, expected_id) in enumerate(
        zip(stages, TRACE_STAGE_ORDER, strict=True), start=1
    ):
        if (
            not isinstance(stage, Mapping)
            or stage.get("sequence") != sequence
            or stage.get("stage_id") != expected_id
            or stage.get("stage_digest")
            != canonical_sha256(stage, exclude_fields=("stage_digest",))
        ):
            raise Step43ValidationError("STEP43_TRACE_STAGE_INVALID")
    if trace.get("root_digest") != canonical_sha256(
        trace, exclude_fields=("root_digest",)
    ):
        raise Step43ValidationError("STEP43_TRACE_ROOT_INVALID")
    if (
        trace.get("status") != "PASS_LIVE_COHERENT_LINEAGE"
        or trace.get("closure_eligible") is not True
        or trace.get("selected_case_id") != PRIMARY_CASE_ID
        or trace.get("source_validation_digest") != step38.get("validation_digest")
        or trace.get("source_artifact_sha256")
        != _sha256_file(STEP38_EVIDENCE_PATH)
    ):
        raise Step43ValidationError("STEP43_TRACE_LINEAGE_INVALID")


def _case_map(suite: Mapping[str, Any]) -> Mapping[str, Mapping[str, Any]]:
    cases = suite.get("cases")
    if not isinstance(cases, list):
        raise Step43ValidationError("STEP43_GOLDEN_SUITE_INVALID")
    result: dict[str, Mapping[str, Any]] = {}
    for value in cases:
        if not isinstance(value, Mapping) or not isinstance(value.get("case_id"), str):
            raise Step43ValidationError("STEP43_GOLDEN_SUITE_INVALID")
        result[str(value["case_id"])] = value
    required = {PRIMARY_CASE_ID, BACKUP_CASE_ID, SUPPORTED_CASE_ID, *FAIL_CLOSED_CASE_IDS}
    if not required.issubset(result):
        raise Step43ValidationError("STEP43_GOLDEN_CASE_MISSING")
    return result


def _verify_referenced_evidence() -> Mapping[str, Any]:
    suite = _load_json(FIXTURE_PATH)
    trace = _load_json(STEP38_TRACE_PATH)
    step38 = _load_json(STEP38_EVIDENCE_PATH)
    step39 = _load_json(STEP39_EVIDENCE_PATH)
    step40 = _load_json(STEP40_EVIDENCE_PATH)
    step41 = _load_json(STEP41_EVIDENCE_PATH)
    step42 = _load_json(STEP42_EVIDENCE_PATH)
    rc_manifest = _load_json(STEP42_RC_MANIFEST_PATH)

    _verify_digest(step38, "validation_digest")
    _verify_digest(step39, "validation_digest")
    _verify_digest(step40, "validation_digest")
    _verify_digest(
        step41,
        "final_validation_digest",
        exclude_fields=("final_validation_digest",),
    )
    _verify_digest(step42, "validation_digest")
    _verify_trace(trace, step38)
    cases = _case_map(suite)
    _runtime_entries, current_runtime_digest = runtime_content_manifest()

    step39_authority = step39.get("authority", {})
    step40_authority = step40.get("authority", {})
    step41_zero_fields = (
        "secret_leakage_count",
        "cross_tenant_unauthorized_success",
        "cross_owner_unauthorized_success",
        "authority_escalation_success",
        "critic_authority_escalation_success",
        "unauthorized_canonical_evidence_inclusion",
        "known_bad_draft_v1_fail_open",
        "audit_tamper_undetected_for_tested_cases",
        "production_resources_touched",
    )
    if (
        step38.get("status") != "PASS_LIVE_COHERENT_LINEAGE"
        or step38.get("closure_eligible") is not True
        or step42.get("step") != 42
        or step42.get("closure_eligible") is not True
        or step42.get("model_correction_pipeline", {}).get("status")
        != "PASS_REAL_VERIFIED_LINEAGE"
        or step42.get("model_correction_pipeline", {}).get("selected_case_id")
        != PRIMARY_CASE_ID
        or step42.get("personal_memory_integrity", {}).get("private_noncanonical")
        is not True
        or step42.get("critic_optionality", {}).get("disabled_core_pass") is not True
        or step42.get("profile_4gb_smoke", {}).get("status")
        != "PASS_RESTORED_PROFILE_SMOKE"
        or any(value is not False and value != 0 for value in step39_authority.values())
        or step39.get("core_independence", {}).get("critic_optional") is not True
        or any(value is not False and value != 0 for value in step40_authority.values())
        or any(step41.get(field) != 0 for field in step41_zero_fields)
        or any(value != 0 for value in step42.get("security_counters", {}).values())
        or rc_manifest.get("manifest_digest")
        != step42.get("rc_manifest", {}).get("digest")
        or rc_manifest.get("runtime_content_digest") != current_runtime_digest
    ):
        raise Step43ValidationError("STEP43_FROZEN_RC_REFERENCE_INVALID")

    if (
        cases[PRIMARY_CASE_ID].get("expected_final_output") != "VERIFIED_ANSWER"
        or cases[BACKUP_CASE_ID].get("expected_final_output") != "VERIFIED_ANSWER"
        or cases[SUPPORTED_CASE_ID].get("expected_correction_condition")
        != "NO_MATERIAL_CORRECTION_REQUIRED"
        or any(
            cases[case_id].get("expected_final_output") != "HUMAN_REVIEW_REQUIRED"
            for case_id in FAIL_CLOSED_CASE_IDS
        )
    ):
        raise Step43ValidationError("STEP43_GOLDEN_CASE_EXPECTATION_INVALID")

    return {
        "suite": suite,
        "cases": cases,
        "trace": trace,
        "step38": step38,
        "step39": step39,
        "step40": step40,
        "step41": step41,
        "step42": step42,
        "rc_manifest": rc_manifest,
    }


def _validate_markdown_links() -> tuple[int, tuple[str, ...]]:
    checked = 0
    broken: list[str] = []
    root = ROOT.resolve()
    for relative in MARKDOWN_LINK_SURFACES:
        path = ROOT / relative
        if not path.is_file() or path.is_symlink():
            broken.append(f"{relative}:MISSING_DOCUMENT")
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="strict")
        except (OSError, UnicodeError):
            broken.append(f"{relative}:INVALID_UTF8")
            continue
        for raw_target in _MARKDOWN_LINK.findall(content):
            target = raw_target.strip().strip("<>")
            if target.startswith(("#", "https://", "http://", "mailto:")):
                continue
            target = target.split("#", 1)[0]
            if not target:
                continue
            checked += 1
            resolved = (path.parent / target).resolve()
            try:
                resolved.relative_to(root)
            except ValueError:
                broken.append(f"{relative}:{target}:OUTSIDE_REPOSITORY")
                continue
            # The first successful replay creates this exact canonical output;
            # accepting only that one not-yet-written target avoids a bootstrap
            # cycle without weakening validation of any documentation input.
            if resolved == EVIDENCE_PATH.resolve() and not resolved.exists():
                continue
            if not resolved.exists():
                broken.append(f"{relative}:{target}:MISSING")
    return checked, tuple(sorted(broken))


def _submission_artifact_digest() -> tuple[str, tuple[Mapping[str, Any], ...]]:
    entries: list[Mapping[str, Any]] = []
    for relative in SUBMISSION_ARTIFACTS:
        path = ROOT / relative
        if not path.is_file() or path.is_symlink():
            raise Step43ValidationError("STEP43_SUBMISSION_ARTIFACT_MISSING")
        size = path.stat().st_size
        if size <= 0:
            raise Step43ValidationError("STEP43_SUBMISSION_ARTIFACT_EMPTY")
        entries.append(
            {"path": relative, "sha256": _sha256_file(path), "size_bytes": size}
        )
    ordered = tuple(sorted(entries, key=lambda item: str(item["path"])))
    return canonical_sha256(ordered), ordered


def _browser_provider_key_count() -> int:
    hits = 0
    ui_root = ROOT / "src/aioa_memory_kernel/personal_memory_ui"
    for path in sorted(ui_root.rglob("*")):
        if not path.is_file() or path.suffix not in {".html", ".js", ".css"}:
            continue
        content = path.read_text(encoding="utf-8", errors="strict")
        hits += sum(
            content.count(marker)
            for marker in (
                "OPENROUTER_API_KEY",
                "MOONSHOT_API_KEY",
                "Authorization" + ": " + "Bearer",
            )
        )
    return hits


def _judge_projection(references: Mapping[str, Any]) -> Mapping[str, Any]:
    cases = references["cases"]
    trace = references["trace"]
    stage_by_id = {stage["stage_id"]: stage for stage in trace["stages"]}
    return {
        "replay_disclaimer": "REPLAY_NOT_A_NEW_LIVE_PROVIDER_VALIDATION",
        "authority_separation": (
            "MODEL_OUTPUT != CANONICAL_EVIDENCE != PERSONAL_MEMORY != "
            "CRITIC_CANDIDATE != HUMAN_APPROVAL"
        ),
        "phase_a_original_question": cases[PRIMARY_CASE_ID]["question"],
        "phase_b_draft_v1": {
            "evidence_blind": True,
            "raw_text_recorded": False,
            "draft_v1_hash": stage_by_id["DRAFT_V1"]["evidence"]["draft_v1_hash"],
            "validated_defect": "WRONG_EFFECTIVE_DATE",
            "tools_web_code_database_and_privileged_capabilities": "UNAVAILABLE",
        },
        "phase_c_authoritative_retrieval": {
            "route": cases[PRIMARY_CASE_ID]["expected_route"],
            "hat_id": "german-law",
            "source_id": cases[PRIMARY_CASE_ID]["expected_source_id"],
            "official_identifier": "BJNR1330A0023",
            "provision_ids": cases[PRIMARY_CASE_ID]["expected_provision_ids"],
            "evidence_status": cases[PRIMARY_CASE_ID]["expected_evidence_status"],
            "canonical_evidence": CANONICAL_PROVISION_III,
            "evidence_bundle_hash": stage_by_id["SOURCE_EVIDENCE"]["evidence"][
                "evidence_bundle_hash"
            ],
        },
        "phase_d_correction": {
            "claim_relation": "REFUTES",
            "required_correction": (
                "Replace the incorrect effective date with 1 January 2024."
            ),
            "correction_packet_hash": stage_by_id["CORRECTION"]["evidence"][
                "correction_packet_hash"
            ],
        },
        "phase_e_draft_v2_verification": {
            "draft_v2_hash": stage_by_id["DRAFT_V2"]["evidence"]["draft_v2_hash"],
            "verification_summary_hash": stage_by_id["VERIFIED_ANSWER"]["evidence"][
                "verification_summary_hash"
            ],
            "status": "VERIFIED",
        },
        "phase_f_verified_answer": {
            "output_status": "VERIFIED_ANSWER",
            "verified_answer_hash": stage_by_id["VERIFIED_ANSWER"]["evidence"][
                "verified_answer_hash"
            ],
            "known_bad_draft_v1_returned": False,
        },
        "phase_g_before_after": {
            "before": "Model claim contained the wrong effective date.",
            "evidence": "BMJErnAnO III: effective 1 January 2024.",
            "required_correction": "Use 1 January 2024.",
            "after": "Corrected claim is evidence-bound and verified.",
        },
        "phase_h_personal_memory": {
            "owner_approval_required": True,
            "state_path": "DETECTED_TO_AWAITING_APPROVAL_TO_APPROVED_TO_COMMITTED_TO_ACTIVE",
            "active_patch_hash": stage_by_id["ACTIVE_PATCH"]["evidence"][
                "active_patch_hash"
            ],
            "canonical_evidence_authority": False,
        },
        "phase_i_cross_model_reuse": {
            "same_active_patch": True,
            "model_a_identity_digest": stage_by_id["CROSS_MODEL_REUSE"]["evidence"][
                "model_a_identity_digest"
            ],
            "model_b_identity_digest": stage_by_id["CROSS_MODEL_REUSE"]["evidence"][
                "model_b_identity_digest"
            ],
        },
        "phase_j_critic": {
            "default_state": "DISABLED_INTENTIONAL",
            "optional": True,
            "candidate_only": True,
            "canonical_evidence_authority": False,
            "approval_commit_activation_authority": False,
        },
    }


def build_replay_validation() -> dict[str, Any]:
    repository = _repository_guard()
    references = _verify_referenced_evidence()
    link_count, broken_links = _validate_markdown_links()
    artifact_digest, artifacts = _submission_artifact_digest()
    browser_key_count = _browser_provider_key_count()
    if broken_links or browser_key_count:
        raise Step43ValidationError("STEP43_DOCUMENTATION_OR_BROWSER_BOUNDARY_FAILED")

    step38 = references["step38"]
    step39 = references["step39"]
    step40 = references["step40"]
    step41 = references["step41"]
    step42 = references["step42"]
    payload: dict[str, Any] = {
        "step": 43,
        "schema_version": "step43-documentation-demo-submission-validation-1a",
        "status": "PASS_DOCUMENTATION_DEMO_SUBMISSION_REPLAY",
        "closure_eligible": True,
        "base_sha": STEP42_BASE_SHA,
        "final_test_sha_or_precommit_sha": repository[
            "validated_head_sha_before_commit"
        ],
        "repository": repository,
        "primary_demo_case_id": PRIMARY_CASE_ID,
        "backup_demo_case_id": BACKUP_CASE_ID,
        "supported_demo_case_id": SUPPORTED_CASE_ID,
        "fail_closed_demo_case_ids": FAIL_CLOSED_CASE_IDS,
        "live_demo_validation_status": (
            "PASS_REFERENCED_EXACT_STEP42_RC_LIVE_PROOF_NOT_REEXECUTED_FOR_COST_CONTROL"
        ),
        "live_demo_reference": {
            "path": str(STEP42_EVIDENCE_PATH.relative_to(ROOT)),
            "validation_digest": step42["validation_digest"],
            "provider_id": step42["model_correction_pipeline"]["provider_id"],
            "model_id": step42["model_correction_pipeline"]["model_id"],
            "status": step42["model_correction_pipeline"]["status"],
            "tools_web_code_execution": step42["model_correction_pipeline"][
                "tools_web_code_execution"
            ],
        },
        "replay_demo_validation_status": (
            "PASS_REPLAY_NOT_A_NEW_LIVE_PROVIDER_VALIDATION"
        ),
        "verified_answer_status": "VERIFIED_ANSWER",
        "personal_memory_demo_status": "PASS_PRIVATE_NONCANONICAL_HASH_ONLY_REPLAY",
        "critic_default_state": "DISABLED_INTENTIONAL_OPTIONAL",
        "provider_calls_observed": {
            "step43_replay": 0,
            "referenced_step42": "BOUNDED_BY_APPROVED_ATTEMPT_POLICY_NOT_RECOUNTED",
            "step43_live_mode_maximum": MAXIMUM_LIVE_PROVIDER_CALLS,
        },
        "judge_projection": _judge_projection(references),
        "security_regression_reference": {
            "path": str(STEP41_EVIDENCE_PATH.relative_to(ROOT)),
            "validation_digest": step41["final_validation_digest"],
            "secret_leakage_count": step41["secret_leakage_count"],
            "cross_tenant_unauthorized_success": step41[
                "cross_tenant_unauthorized_success"
            ],
            "cross_owner_unauthorized_success": step41[
                "cross_owner_unauthorized_success"
            ],
        },
        "rc_restore_reference": {
            "path": str(STEP42_EVIDENCE_PATH.relative_to(ROOT)),
            "rc_id": step42["rc_manifest"]["rc_id"],
            "rc_manifest_digest": step42["rc_manifest"]["digest"],
            "validation_digest": step42["validation_digest"],
            "restore_status": step42["restore"]["status"],
        },
        "lineage_references": {
            "step38_validation_digest": step38["validation_digest"],
            "step38_trace_root_digest": references["trace"]["root_digest"],
            "step39_validation_digest": step39["validation_digest"],
            "step40_validation_digest": step40["validation_digest"],
        },
        "documentation": {
            "checked_internal_links": link_count,
            "broken_documentation_links": 0,
            "submission_artifacts_verified": True,
            "submission_artifact_count": len(artifacts),
            "submission_artifact_digest": artifact_digest,
        },
        "security_spot_check": {
            "secret_leakage_count": 0,
            "browser_provider_key_count": browser_key_count,
            "cross_tenant_or_owner_leakage": 0,
            "personal_memory_canonical_evidence_authority": False,
            "critic_authority_escalation_success": 0,
            "approval_or_commit_helper_bypass": 0,
        },
        "secret_leakage_count": 0,
        "broken_documentation_links": 0,
        "submission_artifacts_verified": True,
        "rc_runtime_semantics_changed": False,
        "numbered_roadmap_final_step": 43,
        "network_calls": 0,
        "database_processes_started": 0,
        "production_resources_touched": 0,
    }
    assert_secret_free(
        payload,
        surface="Step43 documentation/demo/submission replay",
        reject_machine_paths=True,
    )
    payload["validation_digest"] = canonical_sha256(payload)
    return payload


def _live_prompt(case_id: str, question: str) -> tuple[PromptTemplate, ProviderTextRequest]:
    if case_id == PRIMARY_CASE_ID:
        instruction = PRIMARY_SYSTEM_INSTRUCTION
    elif case_id == BACKUP_CASE_ID:
        instruction = BACKUP_SYSTEM_INSTRUCTION
    else:
        raise Step43ValidationError("STEP43_LIVE_CASE_UNSUPPORTED")
    template = PromptTemplate(
        template_id=f"step43-live-{case_id}-evidence-blind-1a",
        template_version="1",
        system_instruction=instruction,
    )
    spec = load_approved_provider_spec()
    request = ProviderTextRequest(
        provider_identity=spec.provider_identity(),
        purpose="step43-live-demo-draft-v1-1a",
        prompt_template_id=template.template_id,
        prompt_template_digest=template.template_digest,
        system_instruction=template.system_instruction,
        user_content=question,
        user_content_digest=hashlib.sha256(question.encode("utf-8")).hexdigest(),
        generation_parameters=GenerationParameters(
            temperature="0",
            top_p="1",
            max_output_tokens=96,
        ),
    )
    return template, request


def _classify_live_text(case_id: str, text: str) -> str:
    normalized = text.strip()
    if case_id == PRIMARY_CASE_ID:
        dates = tuple(_DATE_PATTERN.findall(normalized))
        if dates == (PRIMARY_CANONICAL_DATE,):
            return "CORRECT_CANONICAL_DATE_BACKUP_CASE_REQUIRED"
        if dates and PRIMARY_CANONICAL_DATE not in dates:
            return "NONCANONICAL_DATE_DEFECT_CANDIDATE"
        return "INVALID_OR_AMBIGUOUS_FAIL_CLOSED"
    if normalized == BACKUP_CANONICAL_TEXT:
        return "CORRECT_EXACT_NO_MATERIAL_DEFECT"
    if normalized == BACKUP_WRONG_TEXT:
        return "WRONG_EXACT_MATERIAL_DEFECT"
    return "INVALID_FAIL_CLOSED"


def run_live_observation(
    replay: Mapping[str, Any],
    *,
    allow_live_provider_cost: bool,
) -> dict[str, Any]:
    if allow_live_provider_cost is not True:
        raise Step43ValidationError("STEP43_LIVE_COST_AUTHORIZATION_REQUIRED")
    spec = load_approved_provider_spec()
    raw_secret = os.environ.pop(spec.credential_environment_variable, None)
    if not isinstance(raw_secret, str) or not raw_secret.strip():
        raise Step43ValidationError("STEP43_APPROVED_PROVIDER_CREDENTIAL_ABSENT")
    try:
        credential = SecretValue(
            raw_secret,
            purpose=CredentialPurpose.MODEL_PROVIDER,
            source_name=spec.credential_environment_variable,
        )
    finally:
        raw_secret = ""
    adapter = OpenRouterDraftV1Adapter(credential, spec=spec)
    suite = _load_json(FIXTURE_PATH)
    cases = _case_map(suite)
    observations: list[Mapping[str, Any]] = []
    selected_live_defect: str | None = None
    for case_id in (PRIMARY_CASE_ID, BACKUP_CASE_ID):
        question = str(cases[case_id]["question"])
        _template, request = _live_prompt(case_id, question)
        response = adapter.generate(request, TimeoutPolicy(attempt_timeout_seconds=45))
        verify_provider_response_hash(response)
        if (
            response.provider_identity_digest != spec.provider_identity().identity_digest
            or response.model_id != spec.model_id
            or response.model_version != spec.model_declared_version
            or response.tool_calls_present is not False
        ):
            raise Step43ValidationError("STEP43_LIVE_PROVIDER_IDENTITY_INVALID")
        classification = _classify_live_text(case_id, response.response_content)
        observation = {
            "case_id": case_id,
            "question": question,
            "draft_v1_text": response.response_content,
            "draft_v1_text_sha256": response.response_content_sha256,
            "classification": classification,
            "provider_response_hash": response.response_hash,
            "tools_web_code_execution": "DISABLED",
        }
        assert_secret_free(observation, surface="Step43 live draft observation")
        observations.append(observation)
        if classification in {
            "NONCANONICAL_DATE_DEFECT_CANDIDATE",
            "WRONG_EXACT_MATERIAL_DEFECT",
        }:
            selected_live_defect = case_id
            break
        if case_id == BACKUP_CASE_ID:
            break

    result = dict(replay)
    result.update(
        {
            "status": "PASS_LIVE_DRAFT_V1_PLUS_VERIFIED_RC_REPLAY",
            "mode": "LIVE_DRAFT_V1_OBSERVATION_PLUS_FROZEN_VERIFIED_REPLAY",
            "live_demo_validation_status": (
                "PASS_LIVE_DEFECT_OBSERVED"
                if selected_live_defect is not None
                else "PASS_LIVE_NO_SELECTABLE_DEFECT_USE_TRUTHFUL_REPLAY"
            ),
            "fresh_live_full_system_validation": False,
            "live_observations": tuple(observations),
            "live_selected_defect_case_id": selected_live_defect,
            "provider_calls_observed": len(observations),
            "provider_call_limit": MAXIMUM_LIVE_PROVIDER_CALLS,
            "network_calls": len(observations),
        }
    )
    result.pop("validation_digest", None)
    assert_secret_free(result, surface="Step43 live demo result")
    result["validation_digest"] = canonical_sha256(result)
    return result


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("replay", "live"), default="replay")
    parser.add_argument("--allow-live-provider-cost", action="store_true")
    parser.add_argument("--write-evidence", action="store_true")
    parser.add_argument("--pretty", action="store_true")
    return parser.parse_args()


def _write_evidence(payload: Mapping[str, Any]) -> None:
    EVIDENCE_PATH.parent.mkdir(mode=0o755, parents=False, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=EVIDENCE_PATH.parent,
            prefix=".step43-evidence-",
            delete=False,
        ) as target:
            temporary = Path(target.name)
            target.write(canonical_json(payload) + "\n")
            target.flush()
            os.fsync(target.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, EVIDENCE_PATH)
        temporary = None
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def main() -> int:
    args = _arguments()
    try:
        replay = build_replay_validation()
        if args.mode == "live":
            if args.write_evidence:
                raise Step43ValidationError("STEP43_LIVE_OUTPUT_MUST_NOT_REPLACE_EVIDENCE")
            result = run_live_observation(
                replay,
                allow_live_provider_cost=args.allow_live_provider_cost,
            )
        else:
            result = replay
            if args.write_evidence:
                _write_evidence(result)
        exit_code = 0
    except (Step43ValidationError, ModelAdapterError, ValueError) as error:
        result = {
            "step": 43,
            "schema_version": "step43-documentation-demo-submission-validation-1a",
            "status": "FAILED_VALIDATION_NOT_CLOSURE",
            "closure_eligible": False,
            "base_sha": STEP42_BASE_SHA,
            "reason": redact_exception(error),
            "secret_leakage_count": 0,
            "production_resources_touched": 0,
        }
        result["validation_digest"] = canonical_sha256(result)
        exit_code = 1
    assert_secret_free(result, surface="Step43 demo command output")
    if args.pretty:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(canonical_json(result))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "BACKUP_CASE_ID",
    "EVIDENCE_PATH",
    "FAIL_CLOSED_CASE_IDS",
    "MARKDOWN_LINK_SURFACES",
    "MAXIMUM_LIVE_PROVIDER_CALLS",
    "PRIMARY_CASE_ID",
    "STEP42_BASE_SHA",
    "SUBMISSION_ARTIFACTS",
    "SUPPORTED_CASE_ID",
    "TRACE_STAGE_ORDER",
    "Step43ValidationError",
    "_classify_live_text",
    "_validate_markdown_links",
    "build_replay_validation",
    "run_live_observation",
]
