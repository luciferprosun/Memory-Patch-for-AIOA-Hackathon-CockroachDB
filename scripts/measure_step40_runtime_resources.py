#!/usr/bin/env python3
"""Measure fixed Step 40 scenarios without accepting arbitrary commands."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import resource
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aioa_memory_kernel.contracts.serialization import canonical_sha256
from aioa_memory_kernel.security.credentials import (
    build_minimal_subprocess_environment,
)
from aioa_memory_kernel.security.redaction import assert_secret_free


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
MAXIMUM_CAPTURE_BYTES = 256 * 1024
DEFAULT_TIMEOUT_SECONDS = 180


_SCENARIOS: dict[str, tuple[str, ...]] = {
    "idle-core": (
        "-c",
        "import time; import aioa_memory_kernel.runtime; "
        "import aioa_memory_kernel.retrieval; "
        "import aioa_memory_kernel.personal_memory_ui.web; "
        "import aioa_memory_kernel.critic; time.sleep(0.25)",
    ),
    "retrieval-only": (
        "-m",
        "unittest",
        "tests.test_step38_real_retrieval",
        "-q",
    ),
    "german-law-core": (
        "-m",
        "unittest",
        "tests.test_step38_german_law_e2e",
        "-q",
    ),
    "personal-memory": (
        "-m",
        "unittest",
        "tests.test_step30_user_approval_commit_activation",
        "tests.test_step31_active_patch_retrieval",
        "-q",
    ),
    "critic-disabled": (
        "-m",
        "unittest",
        "tests.test_step40_optional_services.Step40OptionalServiceTests.test_critic_disabled_is_intentional_and_core_is_ready",
        "-q",
    ),
    "critic-enabled": (
        "-m",
        "unittest",
        "tests.test_step39_critic_bridge",
        "tests.test_step39_critic_german_law_e2e",
        "-q",
    ),
    "owner-ui": (
        "-m",
        "unittest",
        "tests.test_step35_personal_memory_ui",
        "-q",
    ),
}


@dataclass(frozen=True, slots=True)
class ProcessTreeSample:
    rss_kib: int
    process_count: int
    thread_count: int


def _proc_status(pid: int) -> tuple[int, int] | None:
    try:
        fields = {
            line.split(":", 1)[0]: line.split(":", 1)[1].strip()
            for line in (Path("/proc") / str(pid) / "status").read_text(
                encoding="utf-8"
            ).splitlines()
            if ":" in line
        }
        rss = int(fields.get("VmRSS", "0 kB").split()[0])
        threads = int(fields.get("Threads", "0"))
    except (FileNotFoundError, PermissionError, OSError, ValueError):
        return None
    return rss, threads


def _parent_map() -> dict[int, int]:
    result: dict[int, int] = {}
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            fields = (entry / "stat").read_text(encoding="utf-8").split()
            result[int(entry.name)] = int(fields[3])
        except (FileNotFoundError, PermissionError, OSError, ValueError, IndexError):
            continue
    return result


def sample_process_tree(root_pid: int) -> ProcessTreeSample:
    if not isinstance(root_pid, int) or isinstance(root_pid, bool) or root_pid < 1:
        raise ValueError("root_pid must be positive")
    parents = _parent_map()
    selected = {root_pid}
    changed = True
    while changed:
        changed = False
        for pid, parent in parents.items():
            if parent in selected and pid not in selected:
                selected.add(pid)
                changed = True
    rss = 0
    threads = 0
    observed = 0
    for pid in selected:
        status = _proc_status(pid)
        if status is None:
            continue
        observed += 1
        rss += status[0]
        threads += status[1]
    return ProcessTreeSample(
        rss_kib=rss,
        process_count=observed,
        thread_count=threads,
    )


def read_host_memory() -> dict[str, int]:
    fields: dict[str, int] = {}
    for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
        if ":" not in line:
            continue
        name, value = line.split(":", 1)
        parts = value.strip().split()
        if parts and parts[0].isdigit():
            fields[name] = int(parts[0])
    required = ("MemTotal", "MemAvailable", "SwapTotal", "SwapFree")
    if any(name not in fields for name in required):
        raise RuntimeError("host memory probe is incomplete")
    return {
        "total_mib": fields["MemTotal"] // 1024,
        "available_mib": fields["MemAvailable"] // 1024,
        "swap_total_mib": fields["SwapTotal"] // 1024,
        "swap_free_mib": fields["SwapFree"] // 1024,
    }


def _safe_environment() -> dict[str, str]:
    environment = build_minimal_subprocess_environment(
        os.environ,
        allowed_names=(
            "PYTHONDONTWRITEBYTECODE",
            "PYTHONHASHSEED",
            "PYTHONPATH",
        ),
    )
    environment.update(
        {
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONHASHSEED": "0",
            "PYTHONPATH": "src:scripts:.",
        }
    )
    return environment


def _terminate_process_group(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)


def measure_scenario(
    scenario: str,
    *,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    if scenario not in _SCENARIOS:
        raise ValueError("scenario is not allowlisted")
    if (
        not isinstance(timeout_seconds, int)
        or isinstance(timeout_seconds, bool)
        or not 1 <= timeout_seconds <= 600
    ):
        raise ValueError("timeout_seconds must be between 1 and 600")
    usage_before = resource.getrusage(resource.RUSAGE_CHILDREN)
    started = time.monotonic()
    peak = ProcessTreeSample(0, 0, 0)
    timed_out = False
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        process = subprocess.Popen(
            (sys.executable, *_SCENARIOS[scenario]),
            cwd=REPOSITORY_ROOT,
            env=_safe_environment(),
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            start_new_session=True,
        )
        try:
            deadline = started + timeout_seconds
            while process.poll() is None:
                sample = sample_process_tree(process.pid)
                peak = ProcessTreeSample(
                    max(peak.rss_kib, sample.rss_kib),
                    max(peak.process_count, sample.process_count),
                    max(peak.thread_count, sample.thread_count),
                )
                if time.monotonic() >= deadline:
                    timed_out = True
                    _terminate_process_group(process)
                    break
                time.sleep(0.02)
            final_sample = sample_process_tree(process.pid)
            peak = ProcessTreeSample(
                max(peak.rss_kib, final_sample.rss_kib),
                max(peak.process_count, final_sample.process_count),
                max(peak.thread_count, final_sample.thread_count),
            )
        finally:
            _terminate_process_group(process)
        elapsed = time.monotonic() - started
        stdout.seek(0)
        stderr.seek(0)
        stdout_bytes = stdout.read(MAXIMUM_CAPTURE_BYTES + 1)
        stderr_bytes = stderr.read(MAXIMUM_CAPTURE_BYTES + 1)
    output_within_bound = (
        len(stdout_bytes) <= MAXIMUM_CAPTURE_BYTES
        and len(stderr_bytes) <= MAXIMUM_CAPTURE_BYTES
    )
    usage_after = resource.getrusage(resource.RUSAGE_CHILDREN)
    return {
        "scenario": scenario,
        "status": (
            "PASS"
            if process.returncode == 0 and not timed_out and output_within_bound
            else "FAILED"
        ),
        "return_code": process.returncode,
        "timed_out": timed_out,
        "duration_seconds": round(elapsed, 6),
        "peak_rss_kib": peak.rss_kib,
        "peak_rss_mib": math.ceil(peak.rss_kib / 1024),
        "peak_process_count": peak.process_count,
        "peak_thread_count": peak.thread_count,
        "user_cpu_seconds": round(usage_after.ru_utime - usage_before.ru_utime, 6),
        "system_cpu_seconds": round(usage_after.ru_stime - usage_before.ru_stime, 6),
        "stdout_byte_length": min(len(stdout_bytes), MAXIMUM_CAPTURE_BYTES + 1),
        "stdout_sha256": hashlib.sha256(stdout_bytes).hexdigest(),
        "stderr_byte_length": min(len(stderr_bytes), MAXIMUM_CAPTURE_BYTES + 1),
        "stderr_sha256": hashlib.sha256(stderr_bytes).hexdigest(),
        "output_within_bound": output_within_bound,
        "raw_output_recorded": False,
        "command_recorded": False,
    }


def build_measurement(scenarios: tuple[str, ...]) -> dict[str, Any]:
    if not scenarios or len(set(scenarios)) != len(scenarios):
        raise ValueError("scenarios must be non-empty and unique")
    measurements = tuple(measure_scenario(scenario) for scenario in scenarios)
    payload: dict[str, Any] = {
        "schema_version": "step40-runtime-measurement-1a",
        "host": read_host_memory(),
        "scenarios": measurements,
        "all_passed": all(item["status"] == "PASS" for item in measurements),
        "secret_leakage_count": 0,
        "machine_paths_recorded": False,
    }
    assert_secret_free(payload, surface="Step40 resource measurement", reject_machine_paths=True)
    payload["measurement_digest"] = canonical_sha256(payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Measure fixed, provider-free Step40 runtime scenarios."
    )
    parser.add_argument(
        "--scenario",
        action="append",
        choices=tuple(sorted(_SCENARIOS)) + ("all",),
        required=True,
    )
    args = parser.parse_args()
    selected = tuple(args.scenario)
    if "all" in selected:
        if selected != ("all",):
            parser.error("all cannot be combined with another scenario")
        selected = tuple(sorted(_SCENARIOS))
    result = build_measurement(selected)
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "ProcessTreeSample",
    "build_measurement",
    "measure_scenario",
    "read_host_memory",
    "sample_process_tree",
]
