#!/usr/bin/env python3
"""Fail-closed, read-only CockroachDB Cloud release gate using ccloud."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
CCLOUD_VERSION = "0.6.12"
CCLOUD_BINARY_SHA256 = (
    "944c7a35f9fe6b166dea991040399ac4e1cf0c754d0514fd57d7c4333c5d4cb2"
)
MAXIMUM_CCLOUD_OUTPUT_BYTES = 2 * 1024 * 1024
CLUSTER_NAME = re.compile(r"^[a-z][a-z0-9-]{0,62}$")
REGION_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
ENUM_VALUE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
VERSION_VALUE = re.compile(r"^v[0-9]+\.[0-9]+\.[0-9]+$")
SHA256_VALUE = re.compile(r"^[0-9a-f]{64}$")
SAFE_CHILD_ENVIRONMENT = frozenset(
    {
        "HOME",
        "PATH",
        "XDG_CONFIG_HOME",
        "XDG_CACHE_HOME",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "NO_PROXY",
        "LANG",
        "LC_ALL",
    }
)


class ControlPlaneGateError(RuntimeError):
    """A sanitized, closed failure from the ccloud control-plane gate."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class ExpectedCluster:
    """Allowlisted, non-secret identity and readiness expectations."""

    name: str
    provider: str
    region: str
    plan: str
    state: str
    cockroach_version: str
    sql_dns_sha256: str

    def __post_init__(self) -> None:
        if CLUSTER_NAME.fullmatch(self.name) is None:
            raise ValueError("INVALID_CLUSTER_NAME")
        if REGION_NAME.fullmatch(self.region) is None:
            raise ValueError("INVALID_REGION")
        for value, reason in (
            (self.provider, "INVALID_PROVIDER"),
            (self.plan, "INVALID_PLAN"),
            (self.state, "INVALID_STATE"),
        ):
            if ENUM_VALUE.fullmatch(value) is None:
                raise ValueError(reason)
        if VERSION_VALUE.fullmatch(self.cockroach_version) is None:
            raise ValueError("INVALID_COCKROACH_VERSION")
        if SHA256_VALUE.fullmatch(self.sql_dns_sha256) is None:
            raise ValueError("INVALID_SQL_DNS_SHA256")


CommandRunner = Callable[[Sequence[str], Mapping[str, str]], str]


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _minimal_child_environment() -> dict[str, str]:
    return {
        name: value
        for name, value in os.environ.items()
        if name in SAFE_CHILD_ENVIRONMENT
    }


def _run_ccloud(command: Sequence[str], environment: Mapping[str, str]) -> str:
    try:
        completed = subprocess.run(
            tuple(command),
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
            env=dict(environment),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ControlPlaneGateError("CCLOUD_EXECUTION_FAILED") from exc
    if completed.returncode != 0:
        raise ControlPlaneGateError("CCLOUD_COMMAND_FAILED")
    encoded = completed.stdout.encode("utf-8")
    if not encoded or len(encoded) > MAXIMUM_CCLOUD_OUTPUT_BYTES:
        raise ControlPlaneGateError("CCLOUD_OUTPUT_BOUNDS_FAILED")
    return completed.stdout


def _parse_json(raw: str, *, expected_type: type[object]) -> object:
    try:
        value = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise ControlPlaneGateError("CCLOUD_JSON_INVALID") from exc
    if not isinstance(value, expected_type):
        raise ControlPlaneGateError("CCLOUD_JSON_SHAPE_INVALID")
    return value


def _required_string(record: Mapping[str, object], field: str) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value:
        raise ControlPlaneGateError("CCLOUD_CLUSTER_FIELD_INVALID")
    return value


def _region_projection(record: Mapping[str, object]) -> tuple[tuple[str, ...], str]:
    raw_regions = record.get("regions")
    if not isinstance(raw_regions, list) or not raw_regions:
        raise ControlPlaneGateError("CCLOUD_REGIONS_INVALID")
    region_names: list[str] = []
    sql_dns_values: list[str] = []
    primary_count = 0
    for raw_region in raw_regions:
        if not isinstance(raw_region, dict):
            raise ControlPlaneGateError("CCLOUD_REGIONS_INVALID")
        name = _required_string(raw_region, "name")
        sql_dns = _required_string(raw_region, "sql_dns")
        primary = raw_region.get("primary")
        if not isinstance(primary, bool):
            raise ControlPlaneGateError("CCLOUD_REGIONS_INVALID")
        region_names.append(name)
        sql_dns_values.append(sql_dns)
        primary_count += int(primary)
    if len(set(region_names)) != len(region_names) or primary_count != 1:
        raise ControlPlaneGateError("CCLOUD_REGIONS_INVALID")
    if len(set(sql_dns_values)) != 1:
        raise ControlPlaneGateError("CCLOUD_SQL_DNS_AMBIGUOUS")
    return tuple(sorted(region_names)), sql_dns_values[0]


def build_receipt(
    cluster_list: Sequence[object],
    cluster_info: Mapping[str, object],
    expected: ExpectedCluster,
    *,
    binary_sha256: str,
    script_sha256: str,
    observed_at: str,
) -> dict[str, object]:
    """Validate allowlisted metadata and return a secret-free receipt."""

    matches = [
        item
        for item in cluster_list
        if isinstance(item, dict) and item.get("name") == expected.name
    ]
    if len(matches) != 1:
        raise ControlPlaneGateError("CCLOUD_CLUSTER_IDENTITY_NOT_UNIQUE")
    listed = matches[0]
    listed_id = _required_string(listed, "id")
    info_id = _required_string(cluster_info, "id")
    if listed_id != info_id:
        raise ControlPlaneGateError("CCLOUD_CLUSTER_LIST_INFO_MISMATCH")

    actual = {
        "name": _required_string(cluster_info, "name"),
        "provider": _required_string(cluster_info, "cloud_provider"),
        "plan": _required_string(cluster_info, "plan"),
        "state": _required_string(cluster_info, "state"),
        "cockroach_version": _required_string(
            cluster_info, "cockroach_version"
        ),
    }
    regions, sql_dns = _region_projection(cluster_info)
    expected_values = {
        "name": expected.name,
        "provider": expected.provider,
        "plan": expected.plan,
        "state": expected.state,
        "cockroach_version": expected.cockroach_version,
    }
    if actual != expected_values:
        raise ControlPlaneGateError("CCLOUD_CLUSTER_EXPECTATION_MISMATCH")
    if expected.region not in regions:
        raise ControlPlaneGateError("CCLOUD_CLUSTER_REGION_MISMATCH")
    sql_dns_sha256 = _sha256_text(sql_dns)
    if sql_dns_sha256 != expected.sql_dns_sha256:
        raise ControlPlaneGateError("CCLOUD_SQL_DNS_IDENTITY_MISMATCH")

    receipt: dict[str, object] = {
        "schema_version": "ccloud-control-plane-release-gate-1a",
        "observed_at": observed_at,
        "verdict": "PASS_READ_ONLY_CCLOUD_CONTROL_PLANE_GATE",
        "tool": {
            "name": "ccloud",
            "version": CCLOUD_VERSION,
            "binary_sha256": binary_sha256,
            "script_sha256": script_sha256,
        },
        "agent_operation": {
            "purpose": "FAIL_CLOSED_RELEASE_CLUSTER_READINESS_GATE",
            "commands": [
                "ccloud cluster list --output json --quiet",
                f"ccloud cluster info {expected.name} --output json --quiet",
            ],
            "control_plane_reads": 2,
            "control_plane_mutations": 0,
            "database_reads": 0,
            "database_writes": 0,
            "provider_calls": 0,
            "aws_mutations": 0,
        },
        "cluster": {
            "name": actual["name"],
            "identity_sha256": _sha256_text(info_id),
            "provider": actual["provider"],
            "plan": actual["plan"],
            "state": actual["state"],
            "cockroach_version": actual["cockroach_version"],
            "regions": list(regions),
            "sql_dns_sha256": sql_dns_sha256,
        },
        "checks": {
            "authenticated_ccloud_session": True,
            "binary_pin_match": binary_sha256 == CCLOUD_BINARY_SHA256,
            "unique_cluster_name": True,
            "list_info_identity_match": True,
            "provider_match": True,
            "plan_match": True,
            "state_ready": True,
            "version_match": True,
            "region_match": True,
            "sql_dns_identity_match": True,
        },
        "security": {
            "raw_cluster_id_emitted": False,
            "account_or_creator_identity_emitted": False,
            "connection_string_emitted": False,
            "password_emitted": False,
            "token_emitted": False,
            "secret_value_emitted": False,
        },
        "frozen_application": {
            "source_modified": False,
            "runtime_image_rebuilt": False,
            "cockroachdb_records_modified": 0,
            "ecs_service_modified": False,
        },
    }
    receipt["receipt_sha256"] = _sha256_text(_canonical_json(receipt))
    return receipt


def run_gate(
    expected: ExpectedCluster,
    *,
    ccloud_binary: str = "ccloud",
    runner: CommandRunner = _run_ccloud,
    observed_at: str | None = None,
) -> dict[str, object]:
    binary_name = shutil.which(ccloud_binary)
    if binary_name is None:
        raise ControlPlaneGateError("CCLOUD_BINARY_NOT_FOUND")
    binary_path = Path(binary_name).resolve()
    binary_sha256 = _sha256_file(binary_path)
    if binary_sha256 != CCLOUD_BINARY_SHA256:
        raise ControlPlaneGateError("CCLOUD_BINARY_PIN_MISMATCH")
    environment = _minimal_child_environment()
    list_command = (
        str(binary_path),
        "cluster",
        "list",
        "--output",
        "json",
        "--quiet",
    )
    info_command = (
        str(binary_path),
        "cluster",
        "info",
        expected.name,
        "--output",
        "json",
        "--quiet",
    )
    cluster_list = _parse_json(
        runner(list_command, environment), expected_type=list
    )
    cluster_info = _parse_json(
        runner(info_command, environment), expected_type=dict
    )
    assert isinstance(cluster_list, list)
    assert isinstance(cluster_info, dict)
    timestamp = observed_at or datetime.now(timezone.utc).replace(
        microsecond=0
    ).isoformat()
    return build_receipt(
        cluster_list,
        cluster_info,
        expected,
        binary_sha256=binary_sha256,
        script_sha256=_sha256_file(Path(__file__).resolve()),
        observed_at=timestamp,
    )


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only ccloud JSON gate for the exact CockroachDB Cloud "
            "release cluster. No database or control-plane mutation is allowed."
        )
    )
    parser.add_argument("--cluster-name", required=True)
    parser.add_argument("--expected-provider", required=True)
    parser.add_argument("--expected-region", required=True)
    parser.add_argument("--expected-plan", required=True)
    parser.add_argument("--expected-state", required=True)
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--expected-sql-dns-sha256", required=True)
    parser.add_argument("--ccloud-binary", default="ccloud")
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        arguments = _argument_parser().parse_args(argv)
        expected = ExpectedCluster(
            name=arguments.cluster_name,
            provider=arguments.expected_provider,
            region=arguments.expected_region,
            plan=arguments.expected_plan,
            state=arguments.expected_state,
            cockroach_version=arguments.expected_version,
            sql_dns_sha256=arguments.expected_sql_dns_sha256,
        )
        receipt = run_gate(
            expected,
            ccloud_binary=arguments.ccloud_binary,
        )
    except (ControlPlaneGateError, ValueError) as exc:
        reason = (
            exc.reason
            if isinstance(exc, ControlPlaneGateError)
            else str(exc)
        )
        print(
            _canonical_json(
                {
                    "schema_version": "ccloud-control-plane-release-gate-1a",
                    "verdict": "BLOCKED_FAIL_CLOSED",
                    "reason": reason,
                }
            ),
            file=sys.stderr,
        )
        return 1
    if arguments.pretty:
        print(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(_canonical_json(receipt))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
