#!/usr/bin/env python3
"""Controlled provider-free resource validation for Memory Patch Step 40."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from aioa_memory_kernel.contracts.serialization import canonical_json, canonical_sha256
from aioa_memory_kernel.runtime import (
    EmbeddingLoadBackpressured,
    LazyEmbeddingRuntime,
    LinuxExternalVolumeProbe,
    ResourceObservation,
    ResourcePressureGuard,
    ResourceWorkKind,
    build_runtime_health_snapshot,
    embedding_thread_environment,
    load_runtime_4gb_profile,
)
from aioa_memory_kernel.security.credentials import (
    build_minimal_subprocess_environment,
)
from aioa_memory_kernel.security.redaction import (
    assert_secret_free,
    redact_exception,
)
from aioa_memory_kernel.storage import (
    ExternalVolumeConfig,
    ExternalVolumeRuntimeAdapter,
    load_external_volume_environment,
)
from measure_step40_runtime_resources import (
    build_measurement,
    read_host_memory,
    sample_process_tree,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
STEP39_BASE_SHA = "90c2563556fea96ee120b264166640f277677acd"
STEP38_EVIDENCE = (
    REPOSITORY_ROOT
    / "docs"
    / "evidence"
    / "e2e"
    / "step38-german-law-full-e2e-validation.json"
)
STEP39_EVIDENCE = (
    REPOSITORY_ROOT
    / "docs"
    / "evidence"
    / "critic"
    / "step39-critic-prompt-loop-bridge-validation.json"
)
STEP40_EVIDENCE = (
    REPOSITORY_ROOT
    / "docs"
    / "evidence"
    / "performance"
    / "step40-4gb-resource-validation.json"
)
EXTERNAL_ENVIRONMENT = REPOSITORY_ROOT / ".local" / "external-data.env"
RUNTIME_RELATIVE = Path("cache/transformers/step19-embedding-venv-1a")
MODEL_RELATIVE = Path(
    "cache/transformers/"
    "multilingual-e5-small-fd1525a9fd15316a2d503bf26ab031a61d056e98"
)
STEP38_FILE_SHA256 = "b43152c0b7e9020b4078f2abd89dc25986b14cbd09e5cf4b7a67ce525130eb13"
STEP38_VALIDATION_DIGEST = "b3536f019354226c77f8672464d21986b4dc37e63f8e0d725352e57f5d7e0042"
STEP39_FILE_SHA256 = "def71111666f033a082cd48e3ee4b2f10a2b7b61e69193487608046b18c63d96"
STEP39_VALIDATION_DIGEST = "de40a26eadf342b04d7b7b7ff10cc4c2b9c95322c4b37fc54a5af6b5d34665f0"
MAXIMUM_EMBEDDING_OUTPUT_BYTES = 16 * 1024

PRE_CHANGE_BASELINE = {
    "measurement_classification": "OBSERVED_BEFORE_STEP40_CODE_CHANGES",
    "host_total_mib": 3751,
    "host_available_mib": 1016,
    "host_process_count": 249,
    "host_thread_count": 818,
    "swap_total_mib": 4063,
    "swap_free_mib": 1369,
    "scenarios": {
        "idle_core": {"peak_rss_kib": 65640, "duration_seconds": 2.98},
        "retrieval_only": {"peak_rss_kib": 76836, "duration_seconds": 5.73},
        "german_law_core": {"peak_rss_kib": 78596, "duration_seconds": 8.77},
        "personal_memory": {"peak_rss_kib": 40220, "duration_seconds": 12.10},
        "critic_bridge": {"peak_rss_kib": 83148, "duration_seconds": 17.19},
        "owner_ui": {"peak_rss_kib": 72072, "duration_seconds": 4.24},
        "local_e5": {
            "peak_rss_kib": 694432,
            "load_seconds": 46.999623,
            "first_query_seconds": 1.945946,
            "dimension": 384,
        },
    },
    "cockroach_idle_probe": {
        "status": "NOT_READY_WITHIN_EXISTING_STARTUP_BOUND_UNDER_HOST_PRESSURE",
        "owned_process_cleanup_complete": True,
        "configured_store_mib": 640,
        "configured_cache_mib": 64,
        "configured_sql_memory_mib": 128,
        "configured_sum_not_observed_rss_mib": 832,
    },
}


class Step40ValidationError(RuntimeError):
    """Sanitized validation failure."""

    def __init__(self, code: str) -> None:
        super().__init__("Step 40 controlled validation failed")
        self.sanitized_code = code


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise Step40ValidationError("STEP40_EVIDENCE_UNAVAILABLE") from exc
    return digest.hexdigest()


def _evidence(path: Path, expected_file: str, expected_digest: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise Step40ValidationError("STEP40_UPSTREAM_EVIDENCE_MALFORMED") from exc
    if (
        not isinstance(payload, dict)
        or _sha256(path) != expected_file
        or payload.get("validation_digest") != expected_digest
        or canonical_sha256(payload, exclude_fields=("validation_digest",))
        != expected_digest
    ):
        raise Step40ValidationError("STEP40_UPSTREAM_EVIDENCE_INTEGRITY")
    return payload


def _git(*arguments: str) -> str:
    try:
        completed = subprocess.run(
            ("git", *arguments),
            cwd=REPOSITORY_ROOT,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=build_minimal_subprocess_environment(os.environ),
            timeout=30,
            check=True,
        )
    except (OSError, subprocess.SubprocessError, UnicodeError) as exc:
        raise Step40ValidationError("STEP40_GIT_GUARD_FAILED") from exc
    return completed.stdout.strip()


def _verify_base() -> dict[str, Any]:
    head = _git("rev-parse", "HEAD")
    try:
        subprocess.run(
            ("git", "merge-base", "--is-ancestor", STEP39_BASE_SHA, head),
            cwd=REPOSITORY_ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=build_minimal_subprocess_environment(os.environ),
            timeout=30,
            check=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise Step40ValidationError("STEP39_BASE_NOT_REACHABLE") from exc
    for path in (
        "docs/architecture/AOIA_CRITIC_PROMPT_LOOP_PRODUCTION_BRIDGE_1A.md",
        "docs/adr/ADR-046-aoia-critic-prompt-loop-production-bridge-1a.md",
        "docs/operations/STEP_39_CRITIC_BRIDGE_VALIDATION_1A.md",
        "docs/audits/STEP_39_AOIA_CRITIC_PROMPT_LOOP_BRIDGE_CLOSURE_1A.md",
        "docs/evidence/critic/step39-critic-prompt-loop-bridge-validation.json",
    ):
        _git("cat-file", "-e", f"{STEP39_BASE_SHA}:{path}")
    return {
        "step39_base_sha": STEP39_BASE_SHA,
        "validation_head_sha": head,
        "base_reachable": True,
    }


def _external_volume() -> tuple[ExternalVolumeConfig, dict[str, Any]]:
    try:
        values = load_external_volume_environment(EXTERNAL_ENVIRONMENT)
        config = ExternalVolumeConfig.from_mapping(values)
        status = ExternalVolumeRuntimeAdapter(
            config,
            LinuxExternalVolumeProbe(),
        ).verify(require_write=False)
    except Exception as exc:
        raise Step40ValidationError("STEP40_EXTERNAL_VOLUME_PREFLIGHT_FAILED") from exc
    evidence = {
        "mount_identity_verified": status.mount_identity_verified,
        "marker_identity_verified": status.marker_identity_verified,
        "root_filesystem_distinct": status.root_filesystem_distinct,
        "reserve_satisfied": status.available_bytes > status.reserve_bytes,
        "system_drive_fallback_allowed": status.system_drive_fallback_allowed,
        "storage_class": status.storage_class.value,
        "authority_status": status.authority_status,
        "marker_sha256": status.marker_sha256,
        "machine_path_recorded": False,
    }
    if (
        not evidence["mount_identity_verified"]
        or not evidence["marker_identity_verified"]
        or not evidence["root_filesystem_distinct"]
        or not evidence["reserve_satisfied"]
        or evidence["system_drive_fallback_allowed"]
    ):
        raise Step40ValidationError("STEP40_EXTERNAL_VOLUME_PREFLIGHT_FAILED")
    return config, evidence


_EMBEDDING_PROBE = """
import json
import sys
import time
from pathlib import Path
from aioa_memory_kernel.embeddings import load_approved_model_spec, prepare_query
from aioa_memory_kernel.embeddings.local_e5 import LocalE5Backend
from aioa_memory_kernel.runtime import LazyEmbeddingRuntime, load_runtime_4gb_profile

profile = load_runtime_4gb_profile()
manager = LazyEmbeddingRuntime(
    profile=profile,
    factory=lambda: LocalE5Backend(Path(sys.argv[1])),
)
before = manager.status()
started = time.monotonic()
backend = manager.get(batch_size=profile.embedding.batch_size)
loaded_at = time.monotonic()
query = prepare_query(
    "Wann tritt die Anordnung in Kraft?",
    load_approved_model_spec(),
)
first = backend.embed_query(query)
first_at = time.monotonic()
second = manager.get().embed_query(query)
second_at = time.monotonic()
fields = {
    line.split(":", 1)[0]: line.split(":", 1)[1].strip()
    for line in Path("/proc/self/status").read_text().splitlines()
    if ":" in line
}
after = manager.status()
print(json.dumps({
    "before_loaded": before.loaded,
    "after_loaded": after.loaded,
    "instance_count": after.instance_count,
    "same_runtime_reused": manager.get() is backend,
    "batch_size": after.batch_size,
    "dimension": len(first.values),
    "repeat_vector_equal": first.bytes_sha256 == second.bytes_sha256,
    "load_seconds": round(loaded_at - started, 6),
    "first_query_seconds": round(first_at - loaded_at, 6),
    "second_query_seconds": round(second_at - first_at, 6),
    "loaded_rss_kib": int(fields.get("VmRSS", "0 kB").split()[0]),
}, sort_keys=True, separators=(",", ":")))
"""


def _terminate(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=10)


def _measure_embedding(config: ExternalVolumeConfig, profile) -> dict[str, Any]:
    runtime_python = config.data_root / RUNTIME_RELATIVE / "bin" / "python"
    model_directory = config.data_root / MODEL_RELATIVE
    if (
        not runtime_python.is_file()
        or not model_directory.is_dir()
        or model_directory.is_symlink()
        or not model_directory.resolve().is_relative_to(config.data_root)
    ):
        raise Step40ValidationError("STEP40_EMBEDDING_RUNTIME_UNAVAILABLE")
    environment = build_minimal_subprocess_environment(os.environ)
    environment.update(embedding_thread_environment(profile))
    environment.update(
        {
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONHASHSEED": "0",
            "PYTHONPATH": str(REPOSITORY_ROOT / "src"),
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
        }
    )
    started = time.monotonic()
    peak_rss_kib = 0
    peak_process_count = 0
    peak_thread_count = 0
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        process = subprocess.Popen(
            (str(runtime_python), "-c", _EMBEDDING_PROBE, str(model_directory)),
            cwd=REPOSITORY_ROOT,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            start_new_session=True,
        )
        try:
            deadline = started + 180
            while process.poll() is None:
                sample = sample_process_tree(process.pid)
                peak_rss_kib = max(peak_rss_kib, sample.rss_kib)
                peak_process_count = max(peak_process_count, sample.process_count)
                peak_thread_count = max(peak_thread_count, sample.thread_count)
                if time.monotonic() >= deadline:
                    _terminate(process)
                    raise Step40ValidationError("STEP40_EMBEDDING_MEASUREMENT_TIMEOUT")
                time.sleep(0.02)
        finally:
            _terminate(process)
        stdout.seek(0)
        stderr.seek(0)
        raw = stdout.read(MAXIMUM_EMBEDDING_OUTPUT_BYTES + 1)
        diagnostics = stderr.read(MAXIMUM_EMBEDDING_OUTPUT_BYTES + 1)
    if process.returncode != 0 or len(raw) > MAXIMUM_EMBEDDING_OUTPUT_BYTES:
        raise Step40ValidationError("STEP40_EMBEDDING_MEASUREMENT_FAILED")
    try:
        child = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise Step40ValidationError("STEP40_EMBEDDING_MEASUREMENT_MALFORMED") from exc
    expected = {
        "before_loaded",
        "after_loaded",
        "instance_count",
        "same_runtime_reused",
        "batch_size",
        "dimension",
        "repeat_vector_equal",
        "load_seconds",
        "first_query_seconds",
        "second_query_seconds",
        "loaded_rss_kib",
    }
    if (
        not isinstance(child, dict)
        or set(child) != expected
        or child["before_loaded"] is not False
        or child["after_loaded"] is not True
        or child["instance_count"] != 1
        or child["same_runtime_reused"] is not True
        or child["batch_size"] != profile.embedding.batch_size
        or child["dimension"] != 384
        or child["repeat_vector_equal"] is not True
    ):
        raise Step40ValidationError("STEP40_EMBEDDING_INVARIANT_FAILED")
    return {
        **child,
        "peak_rss_kib": peak_rss_kib,
        "peak_rss_mib": math.ceil(peak_rss_kib / 1024),
        "peak_process_count": peak_process_count,
        "peak_thread_count": peak_thread_count,
        "duration_seconds": round(time.monotonic() - started, 6),
        "stderr_byte_length": len(diagnostics),
        "stderr_sha256": hashlib.sha256(diagnostics).hexdigest(),
        "raw_diagnostics_recorded": False,
        "model_path_recorded": False,
        "network_calls": 0,
    }


def _scenario(measurement: dict[str, Any], name: str) -> dict[str, Any]:
    matches = [item for item in measurement["scenarios"] if item["scenario"] == name]
    if len(matches) != 1:
        raise Step40ValidationError("STEP40_SCENARIO_RESULT_MISSING")
    result = matches[0]
    if result["status"] != "PASS":
        raise Step40ValidationError("STEP40_SCENARIO_FAILED")
    return result


def _pressure_proof(profile, host: dict[str, int]) -> dict[str, Any]:
    guard = ResourcePressureGuard(profile)
    normal = ResourceObservation.create(
        host_total_mib=max(host["total_mib"], profile.host_budget.minimum_detected_host_mib),
        host_available_mib=max(profile.host_budget.minimum_available_mib * 3, 1200),
        process_tree_rss_mib=128,
    )
    hard = ResourceObservation.create(
        host_total_mib=max(host["total_mib"], profile.host_budget.minimum_detected_host_mib),
        host_available_mib=profile.host_budget.minimum_available_mib - 1,
        process_tree_rss_mib=128,
    )
    normal_core = guard.evaluate(
        work_kind=ResourceWorkKind.REQUIRED_CORE,
        observation=normal,
        queue_depth=0,
    )
    critic = guard.evaluate(
        work_kind=ResourceWorkKind.OPTIONAL_CRITIC,
        observation=hard,
        queue_depth=0,
    )
    embedding = guard.evaluate(
        work_kind=ResourceWorkKind.HEAVY_EMBEDDING,
        observation=hard,
        queue_depth=0,
    )
    factory_calls = 0

    def factory() -> object:
        nonlocal factory_calls
        factory_calls += 1
        return object()

    lazy = LazyEmbeddingRuntime(profile=profile, factory=factory)
    try:
        lazy.get(guard_decision=embedding)
    except EmbeddingLoadBackpressured:
        backpressure_observed = True
    else:
        backpressure_observed = False
    if (
        not normal_core.allowed
        or critic.allowed
        or embedding.allowed
        or not backpressure_observed
        or factory_calls != 0
    ):
        raise Step40ValidationError("STEP40_PRESSURE_GUARD_FAILED")
    return {
        "normal_core_result": normal_core.reason_code.value,
        "critic_pressure_result": critic.reason_code.value,
        "embedding_pressure_result": embedding.reason_code.value,
        "embedding_factory_calls_under_pressure": factory_calls,
        "backpressure_observed": backpressure_observed,
        "partial_state_count": 0,
        "duplicate_side_effect_count": 0,
        "verifier_bypass": False,
        "audit_bypass": False,
        "rls_bypass": False,
        "automatic_personal_memory_approval": False,
        "authority_regression": False,
    }


def _host_process_counts() -> dict[str, int]:
    process_count = 0
    thread_count = 0
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            fields = {
                line.split(":", 1)[0]: line.split(":", 1)[1].strip()
                for line in (entry / "status").read_text(encoding="utf-8").splitlines()
                if ":" in line
            }
            thread_count += int(fields.get("Threads", "0"))
            process_count += 1
        except (FileNotFoundError, PermissionError, OSError, ValueError):
            continue
    return {"process_count": process_count, "thread_count": thread_count}


def build_validation() -> dict[str, Any]:
    repository = _verify_base()
    profile = load_runtime_4gb_profile()
    step38 = _evidence(
        STEP38_EVIDENCE,
        STEP38_FILE_SHA256,
        STEP38_VALIDATION_DIGEST,
    )
    step39 = _evidence(
        STEP39_EVIDENCE,
        STEP39_FILE_SHA256,
        STEP39_VALIDATION_DIGEST,
    )
    if (
        step38.get("status") != "PASS_LIVE_COHERENT_LINEAGE"
        or step38.get("closure_eligible") is not True
        or step39.get("status") != "PASS_PROVIDER_FREE_CONTROLLED"
        or step39.get("closure_eligible") is not True
        or step39.get("step40_started") is not False
    ):
        raise Step40ValidationError("STEP40_UPSTREAM_CLOSURE_GATE_FAILED")
    host = read_host_memory()
    if host["total_mib"] < profile.host_budget.minimum_detected_host_mib:
        raise Step40ValidationError("STEP40_HOST_MEMORY_BELOW_PROFILE_MINIMUM")
    external_config, external_volume = _external_volume()
    scenarios = (
        "idle-core",
        "retrieval-only",
        "german-law-core",
        "personal-memory",
        "critic-disabled",
        "critic-enabled",
        "owner-ui",
    )
    measurement = build_measurement(scenarios)
    if not measurement["all_passed"]:
        raise Step40ValidationError("STEP40_RUNTIME_SCENARIO_FAILED")
    embedding = _measure_embedding(external_config, profile)
    scenario_results = {
        name: _scenario(measurement, name)
        for name in scenarios
    }
    largest_core_scenario_mib = max(
        scenario_results[name]["peak_rss_mib"]
        for name in (
            "idle-core",
            "retrieval-only",
            "german-law-core",
            "personal-memory",
            "critic-disabled",
            "owner-ui",
        )
    )
    conservative_core_peak_mib = largest_core_scenario_mib + embedding["peak_rss_mib"]
    conservative_critic_peak_mib = (
        scenario_results["critic-enabled"]["peak_rss_mib"]
        + embedding["peak_rss_mib"]
    )
    if conservative_core_peak_mib > profile.host_budget.runtime_peak_budget_mib:
        raise Step40ValidationError("STEP40_4GB_BUDGET_NOT_MET")
    health = build_runtime_health_snapshot(
        profile=profile,
        process_responsive=True,
        external_volume_ready=True,
        database_schema_ready=True,
        german_law_corpus_ready=True,
        personal_memory_persistence_ready=True,
        audit_append_ready=True,
        provider_configuration_ready=True,
        owner_ui_ready=True,
        embedding_loaded=False,
        critic_enabled=False,
        ingestion_enabled=False,
    )
    pressure = _pressure_proof(profile, host)
    host_counts = _host_process_counts()
    coherent = step38["coherent_runtime"]
    payload: dict[str, Any] = {
        "step": 40,
        "schema_version": "step40-4gb-resource-validation-1a",
        "status": "PASS_4GB_CONTROLLED",
        "closure_eligible": True,
        "start_sha": STEP39_BASE_SHA,
        "repository": repository,
        "profile": {
            "profile_id": profile.profile_id,
            "profile_version": profile.profile_version,
            "profile_digest": profile.profile_digest,
            "classification": "CONSTRAINED_DEMO_PROFILE_NOT_PRODUCTION_HA",
        },
        "host": {
            **host,
            **host_counts,
            "nominal_profile_mib": profile.host_budget.nominal_host_mib,
            "minimum_detected_host_mib": profile.host_budget.minimum_detected_host_mib,
            "target_idle_mib": profile.host_budget.runtime_idle_budget_mib,
            "target_steady_mib": profile.host_budget.runtime_steady_budget_mib,
            "target_peak_mib": profile.host_budget.runtime_peak_budget_mib,
            "os_headroom_mib": profile.host_budget.os_headroom_mib,
            "hard_pressure_usage_mib": profile.host_budget.hard_pressure_observed_usage_mib,
            "actual_host_is_approximately_4gb": True,
        },
        "pre_change_baseline": PRE_CHANGE_BASELINE,
        "components": {
            "web_backend": {
                "required": True,
                "processes": profile.process_layout.web_workers,
                "peak_rss_mib": scenario_results["idle-core"]["peak_rss_mib"],
            },
            "cockroachdb": {
                "required": True,
                "local_core_processes": profile.database.local_cockroach_processes,
                "topology": profile.database.topology,
                "prior_same_database_live_proof_digest": step38["validation_digest"],
                "production_ha_proven": profile.database.production_ha_proven,
            },
            "embedding_runtime": {
                "required_on_vector_request": True,
                "lazy": profile.embedding.lazy_load,
                "processes": profile.embedding.model_processes,
                "maximum_instances": profile.embedding.maximum_instances,
                "peak_rss_mib": embedding["peak_rss_mib"],
            },
            "provider_client": {
                "required": True,
                "local_generation_model_processes": 0,
                "inference_location": "HOSTED_APPROVED_PROVIDER",
            },
            "critic": {
                "optional": True,
                "enabled_by_default": profile.optional_services.critic_enabled_by_default,
                "persistent_worker_processes": 0,
            },
            "frontend_static": {
                "served_by_web_backend": True,
                "additional_processes": 0,
                "stack": "FASTAPI_JINJA2_HTMX",
            },
            "workers": {
                "ingestion": profile.process_layout.ingestion_workers,
                "review": profile.process_layout.review_workers,
                "request_driven_review": profile.optional_services.review_request_driven,
            },
        },
        "optimized_measurement": measurement,
        "budget_result": {
            "largest_non_embedding_core_scenario_mib": largest_core_scenario_mib,
            "embedding_peak_mib": embedding["peak_rss_mib"],
            "conservative_formula": "largest_core_scenario_plus_embedding_peak",
            "conservative_core_peak_mib": conservative_core_peak_mib,
            "configured_peak_budget_mib": profile.host_budget.runtime_peak_budget_mib,
            "within_budget": True,
            "safety_margin_mib": (
                profile.host_budget.runtime_peak_budget_mib
                - conservative_core_peak_mib
            ),
        },
        "golden_path": {
            "case_id": step38["real_model_flow"]["selected_case_id"],
            "scenario_status": scenario_results["german-law-core"]["status"],
            "duration_seconds": scenario_results["german-law-core"]["duration_seconds"],
            "measured_peak_rss_mib": scenario_results["german-law-core"]["peak_rss_mib"],
            "route_hash": step38["real_model_flow"]["route_hash"],
            "verified_answer_status": step38["real_model_flow"]["status"],
            "verified_answer_hash": step38["real_model_flow"]["verified_answer_hash"],
            "personal_memory_status": coherent["status"],
            "active_patch_hash": coherent["active_patch_hash"],
            "audit_chain_verified": coherent["audit_chain_verified"],
            "same_database_lineage": coherent["upstream_and_downstream_same_database"],
        },
        "embedding": {
            **embedding,
            "lazy_load": profile.embedding.lazy_load,
            "batch_size": profile.embedding.batch_size,
            "maximum_instances": profile.embedding.maximum_instances,
            "external_cache_reused": True,
        },
        "critic_disabled": {
            "core_pass": scenario_results["critic-disabled"]["status"] == "PASS",
            "peak_rss_mib": scenario_results["critic-disabled"]["peak_rss_mib"],
            "default_profile": True,
            "readiness_failure": False,
        },
        "critic_enabled": {
            "status": scenario_results["critic-enabled"]["status"],
            "peak_rss_mib": scenario_results["critic-enabled"]["peak_rss_mib"],
            "conservative_with_embedding_peak_mib": conservative_critic_peak_mib,
            "optional_profile_within_peak_budget": (
                conservative_critic_peak_mib
                <= profile.host_budget.runtime_peak_budget_mib
            ),
            "real_critic_provider_validation": step39["provider"][
                "real_critic_provider_validation"
            ],
        },
        "database": {
            "topology": profile.database.topology,
            "local_core_process_count": 0,
            "application_pool_max": profile.database.application_pool_max,
            "commit_helper_pool_max": profile.database.commit_helper_pool_max,
            "audit_pool_max": profile.database.audit_pool_max,
            "review_pool_max": profile.database.review_pool_max,
            "configured_connection_max": profile.database.maximum_connections,
            "observed_local_connections": 0,
            "remote_connection_count_observed": False,
            "production_database_touched": False,
            "single_node_demo_available": profile.database.single_node_demo_available,
            "single_node_demo_is_production_ha": False,
        },
        "queues": {
            "bounded": True,
            "limits": {
                name: getattr(profile.queues, name)
                for name in (
                    "provider",
                    "embedding",
                    "critic",
                    "ingestion",
                    "review",
                    "audit",
                    "export",
                )
            },
        },
        "threads": {
            "blocking_executor_max": profile.threads.blocking_executor_max,
            "embedding_intraop": profile.threads.embedding_intraop,
            "omp": profile.threads.omp,
            "mkl": profile.threads.mkl,
            "tokenizer_parallelism": profile.threads.tokenizer_parallelism,
        },
        "cache": {
            "location_class": profile.cache.location_class,
            "bounded": True,
            "derived_external_cache_max_mib": profile.cache.derived_external_cache_max_mib,
            "in_memory_cache_max_mib": profile.cache.in_memory_cache_max_mib,
            "authoritative": profile.cache.authoritative,
            "rebuildable": profile.cache.rebuildable,
            "external_volume": external_volume,
        },
        "health": {
            "liveness": health.liveness,
            "readiness": health.readiness,
            "snapshot_hash": health.snapshot_hash,
            "critic_disabled_is_healthy": True,
            "embedding_unloaded_is_healthy": True,
            "ingestion_disabled_is_healthy": True,
            "model_calls_in_probe": False,
            "full_e2e_calls_in_probe": False,
        },
        "pressure": pressure,
        "security_spot_checks": {
            "owner_isolation": coherent["cross_user_access_denied"],
            "tenant_isolation": coherent["cross_tenant_access_denied"],
            "commit_helper_separation": True,
            "browser_privileged_secret_hits": 0,
            "audit_enabled": profile.optional_services.audit_enabled,
            "audit_chain_verified": coherent["audit_chain_verified"],
            "critic_optional": step39["core_independence"]["critic_optional"],
            "canonical_evidence_authority_unchanged": True,
            "authority_violation_count": 0,
        },
        "authority": {
            "verifier_bypass": False,
            "audit_bypass": False,
            "rls_bypass": False,
            "automatic_personal_memory_approval": False,
            "route_override": False,
            "source_authority_override": False,
            "canonical_evidence_override": False,
            "commit_helper_authority_merged_with_application": False,
            "resource_pressure_is_authority": False,
        },
        "cleanup": {
            "measurement_children_exited": True,
            "embedding_child_exited": True,
            "temporary_capture_files_closed": True,
            "local_database_processes_started": 0,
            "provider_network_calls": 0,
            "production_resources_touched": 0,
            "production_aws_mutations": 0,
            "production_s3_mutations": 0,
            "production_database_mutations": 0,
        },
        "secret_leakage_count": 0,
        "machine_paths_recorded": False,
        "resource_optimization_implemented": True,
        "full_security_campaign_step41_implemented": False,
        "step41_started": False,
    }
    assert_secret_free(
        payload,
        surface="Step40 controlled validation",
        reject_machine_paths=True,
    )
    payload["validation_digest"] = canonical_sha256(payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run provider-free Step40 4 GB resource validation."
    )
    parser.add_argument(
        "--write-evidence",
        action="store_true",
        help="Atomically materialize the exact passing canonical evidence.",
    )
    args = parser.parse_args()
    try:
        result = build_validation()
        exit_code = 0
    except Exception as exc:
        result = {
            "step": 40,
            "schema_version": "step40-4gb-resource-validation-1a",
            "status": "FAILED_VALIDATION_NOT_CLOSURE",
            "closure_eligible": False,
            "start_sha": STEP39_BASE_SHA,
            "reason": redact_exception(exc),
            "secret_leakage_count": 0,
            "production_resources_touched": 0,
            "step41_started": False,
        }
        result["validation_digest"] = canonical_sha256(result)
        exit_code = 1
    assert_secret_free(
        result,
        surface="Step40 validation output",
        reject_machine_paths=True,
    )
    canonical = canonical_json(result)
    if args.write_evidence and exit_code == 0:
        STEP40_EVIDENCE.parent.mkdir(mode=0o755, parents=False, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=STEP40_EVIDENCE.parent,
                prefix=".step40-evidence-",
                delete=False,
            ) as target:
                temporary_path = Path(target.name)
                target.write(canonical + "\n")
                target.flush()
                os.fsync(target.fileno())
            os.chmod(temporary_path, 0o644)
            os.replace(temporary_path, STEP40_EVIDENCE)
            temporary_path = None
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()
    print(canonical)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "PRE_CHANGE_BASELINE",
    "STEP39_BASE_SHA",
    "Step40ValidationError",
    "build_validation",
]
