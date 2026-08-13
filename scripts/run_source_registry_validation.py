#!/usr/bin/env python3
"""Validate Step 9 source registry and publication controls.

Offline mode opens no socket. Live mode requires an explicit opt-in, the exact
pinned CockroachDB full-server binary, and an output path on the accepted
external volume. The live runtime is one loopback-only disposable node.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import subprocess
import sys
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence


sys.dont_write_bytecode = True
SCRIPT_ROOT = Path(__file__).resolve().parent
REPOSITORY_ROOT = SCRIPT_ROOT.parent
SOURCE_ROOT = REPOSITORY_ROOT / "src"
for import_root in (SCRIPT_ROOT, SOURCE_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

import run_cockroachdb_migrations as migrations  # noqa: E402
import run_cockroachdb_persistence_validation as persistence  # noqa: E402
import run_cockroachdb_rls_validation as rls  # noqa: E402
from aioa_memory_kernel.contracts import MemoryTargetScope  # noqa: E402
from aioa_memory_kernel.contracts.serialization import canonical_json  # noqa: E402
from aioa_memory_kernel.security.credentials import (  # noqa: E402
    build_minimal_subprocess_environment,
)
from aioa_memory_kernel.sources import (  # noqa: E402
    PUBLICATION_GENESIS_DIGEST,
    OriginMetadata,
    ParserIdentity,
    ProvenanceArtifactIdentity,
    ProvenanceCycleError,
    ProvenanceEdge,
    ProvenanceGraph,
    RedactionState,
    SourceAccessClass,
    SourceAuthorityAssessment,
    SourceAuthorityLevel,
    SourceLicenseAssessment,
    SourceLicenseStatus,
    SourcePublicationState,
    SourceRegistryActor,
    SourceRegistryActorType,
    SourceRegistryRecord,
    SourceScopeDimensions,
    TransformationIdentity,
    advance_registry_state,
    build_publication_event,
    evaluate_publication_eligibility,
    event_from_row,
    verify_publication_event_chain,
)


PINNED_VERSION = "v26.2.4"
PINNED_CLUSTER_VERSION = "26.2"
RUN_PREFIX = "mp_step9_"
ROLE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{2,62}$")
FIXED_ROLES = (
    "mp_app_runtime",
    "mp_audit_reader",
    "mp_human_reviewer",
    "mp_personal_memory_commit_helper",
    "mp_request_context_setter",
    "mp_review_service",
    "mp_schema_owner",
    "mp_security_owner",
    "mp_source_publication_worker",
)
FIXTURE_TIME = datetime(2039, 4, 5, 6, 7, 8, tzinfo=UTC)


class SourceRegistryValidationError(RuntimeError):
    """A Step 9 validation invariant failed."""


@dataclass(frozen=True, slots=True)
class LiveValidationPaths:
    workspace_root: Path
    backup_root: Path
    recovery_bundle: Path
    binary: Path
    runtime_root: Path
    runtime_parent: Path
    json_output: Path
    pycache_root: Path


def _reported_path(report: Path, pattern: str, label: str) -> Path:
    match = re.search(pattern, report.read_text(encoding="utf-8"), re.MULTILINE)
    if match is None:
        raise SourceRegistryValidationError(
            f"restored checkpoint lacks the recorded {label}"
        )
    return Path(match.group(1)).resolve()


def _reject_symlink_components(path: Path, label: str) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if current.exists() and current.is_symlink():
            raise SourceRegistryValidationError(
                f"{label} contains a symbolic-link component"
            )


def _require_absolute_workspace_child(
    raw: Path,
    *,
    label: str,
    workspace_root: Path,
    backup_root: Path,
    recovery_bundle: Path,
) -> Path:
    if not raw.is_absolute():
        raise SourceRegistryValidationError(f"{label} must be an absolute path")
    _reject_symlink_components(raw, label)
    resolved = raw.resolve()
    broad = {
        Path("/"),
        Path("/home"),
        Path("/media"),
        workspace_root,
        workspace_root.parent,
    }
    if resolved in broad or not resolved.is_relative_to(workspace_root):
        raise SourceRegistryValidationError(
            f"{label} must be a strict child of the restored workspace"
        )
    if (
        resolved == REPOSITORY_ROOT.resolve()
        or resolved.is_relative_to(REPOSITORY_ROOT.resolve())
        or resolved == backup_root
        or resolved.is_relative_to(backup_root)
        or resolved == recovery_bundle
        or resolved.is_relative_to(recovery_bundle)
    ):
        raise SourceRegistryValidationError(
            f"{label} crosses an immutable or source-code boundary"
        )
    return resolved


def resolve_live_validation_paths(
    binary: Path,
    runtime_root: Path,
    json_output: Path,
) -> LiveValidationPaths:
    candidates = [
        ancestor
        for ancestor in REPOSITORY_ROOT.resolve().parents
        if (
            ancestor / "reports" / "MEMORY_PATCH_STEP9_RESTORED_STATE.md"
        ).is_file()
        and (
            ancestor / "reports" / "POST_REINSTALL_RESTORE_REPORT.md"
        ).is_file()
    ]
    if len(candidates) != 1:
        raise SourceRegistryValidationError(
            "could not resolve exactly one restored workspace"
        )
    workspace_root = candidates[0]
    state_report = (
        workspace_root / "reports" / "MEMORY_PATCH_STEP9_RESTORED_STATE.md"
    )
    restore_report = (
        workspace_root / "reports" / "POST_REINSTALL_RESTORE_REPORT.md"
    )
    recorded_repository = _reported_path(
        state_report,
        r"^- Repository path: `([^`\n]+)`$",
        "repository path",
    )
    recovery_bundle = _reported_path(
        state_report,
        r"^- Recovery bundle path: `([^`\n]+)`$",
        "recovery bundle",
    )
    backup_root = _reported_path(
        restore_report,
        r"^## 7\. Resolved Backup Root\n\n`([^`\n]+)`$",
        "master backup root",
    )
    if recorded_repository != REPOSITORY_ROOT.resolve():
        raise SourceRegistryValidationError(
            "validator is not running from the recorded restored repository"
        )
    if (
        not recovery_bundle.is_dir()
        or not recovery_bundle.is_relative_to(workspace_root / "_recovery")
        or not backup_root.is_dir()
    ):
        raise SourceRegistryValidationError(
            "restored checkpoint boundaries are unavailable"
        )
    resolved_binary = _require_absolute_workspace_child(
        binary,
        label="CockroachDB binary",
        workspace_root=workspace_root,
        backup_root=backup_root,
        recovery_bundle=recovery_bundle,
    )
    resolved_runtime = _require_absolute_workspace_child(
        runtime_root,
        label="runtime root",
        workspace_root=workspace_root,
        backup_root=backup_root,
        recovery_bundle=recovery_bundle,
    )
    resolved_output = _require_absolute_workspace_child(
        json_output,
        label="JSON output",
        workspace_root=workspace_root,
        backup_root=backup_root,
        recovery_bundle=recovery_bundle,
    )
    raw_pycache = os.environ.get("PYTHONPYCACHEPREFIX")
    if raw_pycache is None:
        raise SourceRegistryValidationError(
            "live validation requires explicit PYTHONPYCACHEPREFIX"
        )
    resolved_pycache = _require_absolute_workspace_child(
        Path(raw_pycache),
        label="Python cache",
        workspace_root=workspace_root,
        backup_root=backup_root,
        recovery_bundle=recovery_bundle,
    )
    expected_binary_root = (
        workspace_root / "package-cache" / "cockroachdb" / PINNED_VERSION
    ).resolve()
    expected_runtime = (
        workspace_root / "databases" / "memory-patch-step9" / "live-run"
    ).resolve()
    expected_output_parent = (
        workspace_root / "reports" / "runtime" / "step9"
    ).resolve()
    expected_pycache = (
        workspace_root / "build-cache" / "memory-patch-step9-pycache"
    ).resolve()
    if (
        not resolved_binary.is_file()
        or resolved_binary.is_symlink()
        or not resolved_binary.is_relative_to(expected_binary_root)
    ):
        raise SourceRegistryValidationError(
            "CockroachDB binary is outside the pinned package-cache boundary"
        )
    if resolved_runtime != expected_runtime:
        raise SourceRegistryValidationError(
            "runtime root differs from the exact Step 9 data-disk path"
        )
    if (
        resolved_output.parent != expected_output_parent
        or resolved_output.suffix != ".json"
        or not expected_output_parent.is_dir()
    ):
        raise SourceRegistryValidationError(
            "JSON output differs from the exact Step 9 report boundary"
        )
    if resolved_pycache != expected_pycache or not resolved_pycache.is_dir():
        raise SourceRegistryValidationError(
            "Python cache differs from the exact Step 9 build-cache path"
        )
    if (
        sys.pycache_prefix is None
        or Path(sys.pycache_prefix).resolve() != resolved_pycache
    ):
        raise SourceRegistryValidationError(
            "Python did not activate the explicit data-disk bytecode cache"
        )
    partial = resolved_output.with_name(resolved_output.name + ".partial")
    if resolved_output.exists() or partial.exists():
        raise SourceRegistryValidationError(
            "refusing to overwrite an existing live evidence output"
        )
    if resolved_runtime.exists():
        raise SourceRegistryValidationError(
            "runtime root must not exist before live validation"
        )
    for existing in (
        workspace_root,
        expected_binary_root,
        expected_runtime.parent,
        expected_output_parent,
        resolved_pycache,
        resolved_binary,
    ):
        if existing.stat().st_dev != workspace_root.stat().st_dev:
            raise SourceRegistryValidationError(
                "a validation path leaves the verified workspace filesystem"
            )
    return LiveValidationPaths(
        workspace_root=workspace_root,
        backup_root=backup_root,
        recovery_bundle=recovery_bundle,
        binary=resolved_binary,
        runtime_root=resolved_runtime,
        runtime_parent=resolved_runtime / "cache" / "temporary",
        json_output=resolved_output,
        pycache_root=resolved_pycache,
    )


def _create_runtime_parent(paths: LiveValidationPaths) -> None:
    created: list[Path] = []
    try:
        for path in (
            paths.runtime_root,
            paths.runtime_root / "cache",
            paths.runtime_parent,
        ):
            path.mkdir(mode=0o700)
            created.append(path)
            if (
                path.is_symlink()
                or path.stat().st_dev != paths.workspace_root.stat().st_dev
            ):
                raise SourceRegistryValidationError(
                    "runtime directory left the verified workspace filesystem"
                )
    except BaseException:
        for path in reversed(created):
            try:
                path.rmdir()
            except OSError:
                pass
        raise


def _remove_empty_runtime_parents(paths: LiveValidationPaths) -> list[str]:
    errors: list[str] = []
    for path in (
        paths.runtime_parent,
        paths.runtime_root / "cache",
        paths.runtime_root,
    ):
        try:
            path.rmdir()
        except FileNotFoundError:
            continue
        except OSError:
            errors.append("owned runtime parent is not empty")
    return errors


class ProbeRecorder:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def check(
        self,
        probe_id: str,
        category: str,
        condition: bool,
        observed: str,
        *,
        sqlstate: str | None = None,
    ) -> None:
        row: dict[str, Any] = {
            "category": category,
            "observed": observed,
            "probe_id": probe_id,
            "status": "PASS" if condition else "FAIL",
        }
        if sqlstate is not None:
            row["sqlstate"] = sqlstate
        self.rows.append(row)
        if not condition:
            raise SourceRegistryValidationError(
                f"{probe_id} failed: {observed}"
            )

    def summary(self) -> dict[str, Any]:
        categories: dict[str, int] = {}
        for row in self.rows:
            categories[row["category"]] = (
                categories.get(row["category"], 0) + 1
            )
        return {
            "category_counts": dict(sorted(categories.items())),
            "fail_count": sum(row["status"] != "PASS" for row in self.rows),
            "pass_count": sum(row["status"] == "PASS" for row in self.rows),
            "probe_count": len(self.rows),
        }


def canonical_json_bytes(value: Any) -> bytes:
    return migrations.canonical_json_bytes(value)


def digest_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sql_json(value: Any) -> str:
    return migrations.sql_literal(canonical_json(value)) + "::JSONB"


def timestamp_sql(value: datetime) -> str:
    return migrations.sql_literal(
        value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    ) + "::TIMESTAMPTZ"


def validate_role(role: str) -> None:
    if not ROLE_PATTERN.fullmatch(role) or not role.startswith(RUN_PREFIX):
        raise SourceRegistryValidationError(
            "disposable role lacks the exact Step 9 marker"
        )


def quoted_role(role: str) -> str:
    validate_role(role)
    return f'"{role}"'


def run_unit_suite() -> dict[str, Any]:
    environment = build_minimal_subprocess_environment(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    result = migrations.run_process(
        [
            sys.executable,
            "-m",
            "unittest",
            "tests.test_source_registry",
            "-v",
        ],
        timeout=180,
        environment=environment,
    )
    if result.returncode != 0:
        raise SourceRegistryValidationError(
            "Step 9 unit suite failed: "
            + migrations.sanitize_error(result.stderr)
        )
    match = re.search(r"Ran (\d+) tests?", result.stderr)
    count = int(match.group(1)) if match else 0
    if count < 74:
        raise SourceRegistryValidationError(
            "Step 9 unit suite did not cover all 74 audited cases"
        )
    return {"status": "PASS", "test_count": count}


def source_modules_have_no_cloud_dependency() -> bool:
    imported: set[str] = set()
    source_root = SOURCE_ROOT / "aioa_memory_kernel" / "sources"
    for path in source_root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported.update(
            alias.name.casefold()
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        )
        imported.update(
            (node.module or "").casefold()
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        )
    return not any(
        token in module
        for module in imported
        for token in ("boto", "botocore", "aws")
    )


def offline_validate(*, run_tests: bool = True) -> dict[str, Any]:
    migration = migrations.offline_validate()
    rls_result = rls.offline_validate()
    persistence_result = persistence.offline_validate()
    policy = migrations.load_source_registry_manifest()
    if policy["policy_version"] != "source-publication-eligibility-1a":
        raise SourceRegistryValidationError(
            "source publication policy identity differs"
        )
    if migration["migration_count"] != 19:
        raise SourceRegistryValidationError("migration chain is not 0001-0019")
    if migration["source_registry_table_count"] != 3:
        raise SourceRegistryValidationError(
            "source-registry table coverage differs"
        )
    if not source_modules_have_no_cloud_dependency():
        raise SourceRegistryValidationError(
            "source package imports a forbidden cloud dependency"
        )
    step9_sql = (
        migrations.MIGRATION_ROOT
        / "0006_step9_source_registry_provenance_publication_states.sql"
    ).read_text(encoding="utf-8")
    for required in (
        "source_registry_entries",
        "source_provenance_edges",
        "source_publication_events",
        "ENABLE ROW LEVEL SECURITY",
        "FORCE ROW LEVEL SECURITY",
        "source_registry_entries_s9_publication_guard",
        "source-publication-eligibility-1a",
    ):
        if required not in step9_sql:
            raise SourceRegistryValidationError(
                f"Step 9 SQL lacks required fragment: {required}"
            )
    if re.search(r"(?im)^GRANT\s+DELETE\b", step9_sql):
        raise SourceRegistryValidationError("Step 9 grants runtime DELETE")
    unit = run_unit_suite() if run_tests else {
        "status": "NOT_RUN_IN_LIVE_PREFLIGHT",
        "test_count": 74,
    }
    return {
        "cloud_api_calls": 0,
        "migration": migration,
        "policy_version": policy["policy_version"],
        "persistence": persistence_result,
        "rls": rls_result,
        "source_registry_table_count": 3,
        "status": "PASS",
        "target_version": PINNED_VERSION,
        "unit_tests": unit,
    }


def fixture_ids(run_id: str) -> dict[str, str]:
    values = {
        "hat": f"{run_id}_hat",
        "tenant_a": f"{run_id}_tenant_a",
        "tenant_b": f"{run_id}_tenant_b",
        "user_a1": f"{run_id}_user_a1",
        "user_a2": f"{run_id}_user_a2",
        "user_b1": f"{run_id}_user_b1",
    }
    for suffix in ("shared_a", "shared_b", "private_a1", "private_a2", "private_b1"):
        values[f"scope_{suffix}"] = f"{run_id}_scope_{suffix}"
        values[f"source_{suffix}"] = f"{run_id}_source_{suffix}"
        values[f"snapshot_{suffix}"] = f"{run_id}_snapshot_{suffix}"
        values[f"version_{suffix}"] = f"{run_id}_version_{suffix}"
    for suffix in ("a1", "a2", "b1"):
        values[f"space_{suffix}"] = f"{run_id}_space_{suffix}"
    return values


def fixture_coordinate(
    ids: Mapping[str, str],
    suffix: str,
) -> tuple[str, str | None, bool]:
    if suffix == "shared_a":
        return ids["tenant_a"], None, False
    if suffix == "shared_b":
        return ids["tenant_b"], None, False
    if suffix == "private_a1":
        return ids["tenant_a"], ids["user_a1"], True
    if suffix == "private_a2":
        return ids["tenant_a"], ids["user_a2"], True
    if suffix == "private_b1":
        return ids["tenant_b"], ids["user_b1"], True
    raise SourceRegistryValidationError("unknown synthetic fixture suffix")


def source_payload(suffix: str) -> bytes:
    return f"memory-patch-step9-synthetic:{suffix}".encode("utf-8")


def source_digest(suffix: str) -> str:
    return hashlib.sha256(source_payload(suffix)).hexdigest()


def source_reference(suffix: str) -> str:
    return f"synthetic://step9/source/{suffix}"


def base_fixture_sql(ids: Mapping[str, str]) -> str:
    q = migrations.sql_literal
    at = timestamp_sql(FIXTURE_TIME)
    statements = [
        "INSERT INTO memory_patch.tenants "
        "(tenant_id, display_name, metadata, created_at, updated_at) VALUES "
        f"({q(ids['tenant_a'])}, 'Synthetic Step9 Tenant A', "
        f"'{{}}'::JSONB, {at}, {at}), "
        f"({q(ids['tenant_b'])}, 'Synthetic Step9 Tenant B', "
        f"'{{}}'::JSONB, {at}, {at})",
        "INSERT INTO memory_patch.users "
        "(tenant_id, user_id, display_name, metadata, created_at, updated_at) "
        "VALUES "
        f"({q(ids['tenant_a'])}, {q(ids['user_a1'])}, "
        f"'Synthetic Step9 User A1', '{{}}'::JSONB, {at}, {at}), "
        f"({q(ids['tenant_a'])}, {q(ids['user_a2'])}, "
        f"'Synthetic Step9 User A2', '{{}}'::JSONB, {at}, {at}), "
        f"({q(ids['tenant_b'])}, {q(ids['user_b1'])}, "
        f"'Synthetic Step9 User B1', '{{}}'::JSONB, {at}, {at})",
        "INSERT INTO memory_patch.hat_manifests "
        "(hat_id, hat_version, schema_version, display_name, manifest_hash, "
        "capabilities, approval_authority, commit_authority, "
        "canonical_write_authority, external_action_authority, "
        "allows_private_memory_access, allows_user_code, created_at) VALUES "
        f"({q(ids['hat'])}, '1.0.0', '1.0.0', "
        f"'Synthetic Step9 Knowledge HAT', {q(digest_text(ids['hat']))}, "
        "'[]'::JSONB, 'NONE', 'NONE', 'NONE', 'NONE', false, false, "
        f"{at})",
    ]
    principals = (
        ("a1", "tenant_a", "user_a1"),
        ("a2", "tenant_a", "user_a2"),
        ("b1", "tenant_b", "user_b1"),
    )
    for suffix, tenant_key, user_key in principals:
        statements.extend(
            (
                "INSERT INTO memory_patch.personal_memory_spaces "
                "(tenant_id, user_id, personal_memory_space_id, "
                "schema_version, state, display_name, created_at, updated_at) "
                "VALUES "
                f"({q(ids[tenant_key])}, {q(ids[user_key])}, "
                f"{q(ids[f'space_{suffix}'])}, '1.0.0', 'ACTIVE', "
                f"'Synthetic Step9 Space {suffix.upper()}', {at}, {at})",
                "INSERT INTO memory_patch.hat_scopes "
                "(tenant_id, hat_scope_id, target_scope, owner_user_id, "
                "personal_memory_space_id, created_at) VALUES "
                f"({q(ids[tenant_key])}, "
                f"{q(ids[f'scope_private_{suffix}'])}, "
                f"'USER_PERSONAL_HAT', {q(ids[user_key])}, "
                f"{q(ids[f'space_{suffix}'])}, {at})",
            )
        )
    for suffix, tenant_key in (("shared_a", "tenant_a"), ("shared_b", "tenant_b")):
        statements.append(
            "INSERT INTO memory_patch.hat_scopes "
            "(tenant_id, hat_scope_id, target_scope, knowledge_hat_id, "
            "knowledge_hat_version, created_at) VALUES "
            f"({q(ids[tenant_key])}, {q(ids[f'scope_{suffix}'])}, "
            f"'SHARED_KNOWLEDGE_HAT', {q(ids['hat'])}, '1.0.0', {at})"
        )
    for suffix in (
        "shared_a",
        "shared_b",
        "private_a1",
        "private_a2",
        "private_b1",
    ):
        tenant_id, _, _ = fixture_coordinate(ids, suffix)
        payload = source_payload(suffix)
        statements.extend(
            (
                "INSERT INTO memory_patch.knowledge_sources "
                "(tenant_id, source_id, hat_scope_id, source_kind, "
                "source_reference, provenance, source_observed_at, created_at) "
                "VALUES "
                f"({q(tenant_id)}, {q(ids[f'source_{suffix}'])}, "
                f"{q(ids[f'scope_{suffix}'])}, 'SYNTHETIC', "
                f"{q(source_reference(suffix))}, "
                f"{sql_json({'fixture': 'step9'})}, {at}, {at})",
                "INSERT INTO memory_patch.source_snapshots "
                "(tenant_id, snapshot_id, source_id, hat_scope_id, "
                "content_sha256, byte_length, storage_class, "
                "immutable_object_reference, captured_at, source_observed_at, "
                "provenance) VALUES "
                f"({q(tenant_id)}, {q(ids[f'snapshot_{suffix}'])}, "
                f"{q(ids[f'source_{suffix}'])}, "
                f"{q(ids[f'scope_{suffix}'])}, {q(source_digest(suffix))}, "
                f"{len(payload)}, 'CRDB_TRANSACTIONAL', "
                f"{q('synthetic://step9/snapshot/' + suffix)}, {at}, {at}, "
                f"{sql_json({'fixture': 'step9'})})",
                "INSERT INTO memory_patch.knowledge_versions "
                "(tenant_id, knowledge_version_id, source_id, snapshot_id, "
                "hat_scope_id, version_ordinal, normalized_content_sha256, "
                "normalization_profile, is_current, created_at, provenance) "
                "VALUES "
                f"({q(tenant_id)}, {q(ids[f'version_{suffix}'])}, "
                f"{q(ids[f'source_{suffix}'])}, "
                f"{q(ids[f'snapshot_{suffix}'])}, "
                f"{q(ids[f'scope_{suffix}'])}, 1, "
                f"{q(digest_text('normalized:' + suffix))}, "
                f"'synthetic-step9-v1', true, {at}, "
                f"{sql_json({'fixture': 'step9'})})",
            )
        )
    return "BEGIN;\n" + ";\n".join(statements) + ";\nCOMMIT;"


def make_record(
    ids: Mapping[str, str],
    suffix: str,
    *,
    license_status: SourceLicenseStatus = SourceLicenseStatus.PUBLIC_DOMAIN,
) -> SourceRegistryRecord:
    tenant_id, user_id, personal = fixture_coordinate(ids, suffix)
    parser = ParserIdentity("synthetic-parser", "1.0.0", "1.0.0")
    transformation = TransformationIdentity(
        "exact-byte-registration",
        "1.0.0",
        "1.0.0",
    )
    origin = OriginMetadata(
        "SYNTHETIC_FIXTURE",
        "memory-patch-step9-live",
        "1.0.0",
        "1.0.0",
        f"synthetic://step9/origin/{suffix}",
        FIXTURE_TIME,
    )
    scope = SourceScopeDimensions(
        tenant_id=tenant_id,
        hat_scope_id=ids[f"scope_{suffix}"],
        target_scope=(
            MemoryTargetScope.USER_PERSONAL_HAT
            if personal
            else MemoryTargetScope.SHARED_KNOWLEDGE_HAT
        ),
        owner_user_id=user_id,
        personal_memory_space_id=(
            ids[f"space_{suffix.removeprefix('private_')}"]
            if personal
            else None
        ),
        domain="synthetic",
        jurisdiction="test",
        language="en",
        temporal_policy_reference="synthetic-time-policy-1",
        source_collection=(f"synthetic-{suffix}",),
        additional_dimensions={"fixture": "step9"},
    )
    return SourceRegistryRecord(
        tenant_id=tenant_id,
        source_id=ids[f"source_{suffix}"],
        hat_scope_id=scope.hat_scope_id,
        source_kind="SYNTHETIC",
        source_reference=source_reference(suffix),
        scope=scope,
        authority=SourceAuthorityAssessment(
            SourceAuthorityLevel.OFFICIAL_PRIMARY,
            {"assessment": "synthetic-live-validation"},
        ),
        license=SourceLicenseAssessment(
            license_status,
            "synthetic-license-1",
            "synthetic://step9/license",
        ),
        access_class=(
            SourceAccessClass.USER_PRIVATE
            if personal
            else SourceAccessClass.TENANT_RESTRICTED
        ),
        redaction_state=RedactionState.NOT_REQUIRED,
        parser=parser,
        transformation=transformation,
        origin=origin,
        artifact=ProvenanceArtifactIdentity(
            "EXACT_SOURCE_BYTES",
            source_digest(suffix),
            len(source_payload(suffix)),
            "application/octet-stream",
            origin,
            parser,
            transformation,
            FIXTURE_TIME,
            exact_source_bytes=True,
            model_generated=False,
        ),
        snapshot_id=ids[f"snapshot_{suffix}"],
        knowledge_version_id=ids[f"version_{suffix}"],
        current_publication_state=SourcePublicationState.REGISTERED,
        current_publication_sequence=0,
        current_publication_event_digest=PUBLICATION_GENESIS_DIGEST,
        created_at=FIXTURE_TIME,
        updated_at=FIXTURE_TIME,
    )


def registry_insert_sql(record: SourceRegistryRecord) -> str:
    q = migrations.sql_literal
    scope_data = {
        "additional_dimensions": dict(record.scope.additional_dimensions),
        "domain": record.scope.domain,
        "jurisdiction": record.scope.jurisdiction,
        "language": record.scope.language,
        "source_collection": list(record.scope.source_collection),
        "temporal_policy_reference": record.scope.temporal_policy_reference,
    }
    values: list[str] = [
        q(record.tenant_id),
        q(record.source_id),
        q(record.hat_scope_id),
        q(record.schema_version),
        q(record.source_kind),
        q(record.source_reference),
        q(record.scope.target_scope.value),
        "NULL" if record.scope.owner_user_id is None else q(record.scope.owner_user_id),
        (
            "NULL"
            if record.scope.personal_memory_space_id is None
            else q(record.scope.personal_memory_space_id)
        ),
        q(record.authority.authority_level.value),
        sql_json(record.authority.authority_basis),
        q(record.license.license_status.value),
        (
            "NULL"
            if record.license.license_identifier is None
            else q(record.license.license_identifier)
        ),
        (
            "NULL"
            if record.license.license_reference is None
            else q(record.license.license_reference)
        ),
        q(record.access_class.value),
        q(record.redaction_state.value),
        sql_json(scope_data),
        q(record.scope.scope_digest),
        q(record.parser.parser_name),
        q(record.parser.parser_version),
        q(record.parser.parser_contract_version),
        q(record.transformation.transformation_name),
        q(record.transformation.transformation_version),
        q(record.transformation.transformation_contract_version),
        q(record.origin.origin_kind),
        q(record.origin.origin_system),
        q(record.origin.origin_version),
        q(record.origin.adapter_version),
        "NULL" if record.origin.external_ref is None else q(record.origin.external_ref),
        (
            "NULL"
            if record.origin.observed_at is None
            else timestamp_sql(record.origin.observed_at)
        ),
        q(record.artifact.artifact_kind),
        q(record.artifact.artifact_digest),
        (
            "NULL"
            if record.artifact.byte_length is None
            else str(record.artifact.byte_length)
        ),
        (
            "NULL"
            if record.artifact.media_type is None
            else q(record.artifact.media_type)
        ),
        timestamp_sql(record.artifact.created_at),
        "true" if record.artifact.exact_source_bytes else "false",
        "true" if record.artifact.model_generated else "false",
        "NULL" if record.snapshot_id is None else q(record.snapshot_id),
        (
            "NULL"
            if record.knowledge_version_id is None
            else q(record.knowledge_version_id)
        ),
        q(record.current_publication_state.value),
        str(record.current_publication_sequence),
        q(record.current_publication_event_digest),
        q(record.registry_digest),
        timestamp_sql(record.created_at),
        timestamp_sql(record.updated_at),
    ]
    return (
        "INSERT INTO memory_patch.source_registry_entries ("
        "tenant_id, source_id, hat_scope_id, schema_version, source_kind, "
        "source_reference, target_scope, owner_user_id, "
        "personal_memory_space_id, authority_level, authority_basis, "
        "license_status, license_identifier, license_reference, access_class, "
        "redaction_state, scope_dimensions, scope_digest, parser_name, "
        "parser_version, parser_contract_version, transformation_name, "
        "transformation_version, transformation_contract_version, origin_kind, "
        "origin_system, origin_version, adapter_version, external_ref, "
        "observed_at, artifact_kind, artifact_digest, artifact_byte_length, "
        "artifact_media_type, artifact_created_at, exact_source_bytes, "
        "model_generated, snapshot_id, knowledge_version_id, "
        "current_publication_state, current_publication_sequence, "
        "current_publication_event_digest, registry_digest, created_at, updated_at"
        ") VALUES (" + ", ".join(values) + ")"
    )


def edge_insert_sql(edge: ProvenanceEdge, *, replay_safe: bool = False) -> str:
    q = migrations.sql_literal
    sql = (
        "INSERT INTO memory_patch.source_provenance_edges ("
        "tenant_id, source_id, hat_scope_id, edge_id, "
        "parent_artifact_digest, child_artifact_digest, edge_kind, "
        "parser_name, parser_version, parser_contract_version, "
        "transformation_name, transformation_version, "
        "transformation_contract_version, metadata, edge_digest, created_at"
        ") VALUES ("
        f"{q(edge.tenant_id)}, {q(edge.source_id)}, {q(edge.hat_scope_id)}, "
        f"{q(edge.edge_id)}, {q(edge.parent_artifact_digest)}, "
        f"{q(edge.child_artifact_digest)}, {q(edge.edge_kind)}, "
        f"{q(edge.parser.parser_name)}, {q(edge.parser.parser_version)}, "
        f"{q(edge.parser.parser_contract_version)}, "
        f"{q(edge.transformation.transformation_name)}, "
        f"{q(edge.transformation.transformation_version)}, "
        f"{q(edge.transformation.transformation_contract_version)}, "
        f"{sql_json(edge.metadata)}, {q(edge.edge_digest)}, "
        f"{timestamp_sql(edge.created_at)})"
    )
    return sql + (" ON CONFLICT DO NOTHING" if replay_safe else "")


def publication_transaction_sql(
    record: SourceRegistryRecord,
    event: Any,
    *,
    stored_previous_event_digest: str | None = None,
) -> str:
    q = migrations.sql_literal
    previous_event_digest = (
        event.previous_event_digest
        if stored_previous_event_digest is None
        else stored_previous_event_digest
    )
    insert = (
        "INSERT INTO memory_patch.source_publication_events ("
        "tenant_id, source_id, hat_scope_id, event_id, sequence_number, "
        "from_state, to_state, actor_type, actor_reference, policy_version, "
        "eligibility_decision_digest, reason_codes, reviewer_reference, "
        "previous_event_digest, event_digest, created_at) VALUES ("
        f"{q(event.tenant_id)}, {q(event.source_id)}, {q(event.hat_scope_id)}, "
        f"{q(event.event_id)}, {event.sequence_number}, "
        f"{q(event.from_state.value)}, {q(event.to_state.value)}, "
        f"{q(event.actor_type.value)}, {q(event.actor_reference)}, "
        f"{q(event.policy_version)}, {q(event.eligibility_decision_digest)}, "
        f"{sql_json(event.reason_codes)}, "
        f"{'NULL' if event.reviewer_reference is None else q(event.reviewer_reference)}, "
        f"{q(previous_event_digest)}, {q(event.event_digest)}, "
        f"{timestamp_sql(event.created_at)})"
    )
    update = (
        "UPDATE memory_patch.source_registry_entries SET "
        f"current_publication_state = {q(event.to_state.value)}, "
        f"current_publication_sequence = {event.sequence_number}, "
        f"current_publication_event_digest = {q(event.event_digest)}, "
        f"updated_at = {timestamp_sql(event.created_at)} "
        f"WHERE tenant_id = {q(record.tenant_id)} "
        f"AND source_id = {q(record.source_id)} "
        f"AND hat_scope_id = {q(record.hat_scope_id)} "
        f"AND current_publication_state = {q(record.current_publication_state.value)} "
        f"AND current_publication_sequence = {record.current_publication_sequence} "
        f"AND current_publication_event_digest = "
        f"{q(record.current_publication_event_digest)}"
    )
    return insert + ";\n" + update


def operation_begin_sql(
    *,
    tenant_id: str,
    owner_user_id: str | None,
    operation_id: str,
    operation_kind: str,
    idempotency_key: str,
    request_digest: str,
    scope_digest: str,
    created_at: datetime,
) -> str:
    q = migrations.sql_literal
    owner = "NULL" if owner_user_id is None else q(owner_user_id)
    at = timestamp_sql(created_at)
    return (
        "INSERT INTO memory_patch.persistence_operations ("
        "tenant_id, operation_id, schema_version, owner_user_id, "
        "operation_kind, idempotency_key, request_digest, scope_digest, "
        "status, attempt_count, result_ref, result_digest, last_sqlstate, "
        "sanitized_error_code, created_at, updated_at, completed_at"
        ") VALUES ("
        f"{q(tenant_id)}, {q(operation_id)}, '1.0.0', {owner}, "
        f"{q(operation_kind)}, {q(idempotency_key)}, {q(request_digest)}, "
        f"{q(scope_digest)}, 'IN_PROGRESS', 1, NULL, NULL, NULL, NULL, "
        f"{at}, {at}, NULL)"
    )


def operation_complete_sql(
    *,
    tenant_id: str,
    operation_id: str,
    result_ref: str,
    result_digest: str,
    completed_at: datetime,
) -> str:
    q = migrations.sql_literal
    at = timestamp_sql(completed_at)
    return (
        "UPDATE memory_patch.persistence_operations SET "
        f"status = 'COMPLETED', result_ref = {q(result_ref)}, "
        f"result_digest = {q(result_digest)}, updated_at = {at}, "
        f"completed_at = {at} "
        f"WHERE tenant_id = {q(tenant_id)} "
        f"AND operation_id = {q(operation_id)} "
        "AND status = 'IN_PROGRESS' AND attempt_count = 1"
    )


def durable_mutation_sql(
    *,
    tenant_id: str,
    owner_user_id: str | None,
    operation_id: str,
    operation_kind: str,
    idempotency_key: str,
    request_digest: str,
    scope_digest: str,
    result_ref: str,
    result_digest: str,
    occurred_at: datetime,
    mutation_sql: str,
) -> str:
    return ";\n".join(
        (
            operation_begin_sql(
                tenant_id=tenant_id,
                owner_user_id=owner_user_id,
                operation_id=operation_id,
                operation_kind=operation_kind,
                idempotency_key=idempotency_key,
                request_digest=request_digest,
                scope_digest=scope_digest,
                created_at=occurred_at,
            ),
            mutation_sql.rstrip().rstrip(";"),
            operation_complete_sql(
                tenant_id=tenant_id,
                operation_id=operation_id,
                result_ref=result_ref,
                result_digest=result_digest,
                completed_at=occurred_at,
            ),
        )
    )


def raw_edge_insert_sql(
    *,
    edge: ProvenanceEdge,
    edge_id: str,
    parent_digest: str,
    child_digest: str,
    edge_digest: str,
) -> str:
    q = migrations.sql_literal
    return (
        "INSERT INTO memory_patch.source_provenance_edges ("
        "tenant_id, source_id, hat_scope_id, edge_id, "
        "parent_artifact_digest, child_artifact_digest, edge_kind, "
        "parser_name, parser_version, parser_contract_version, "
        "transformation_name, transformation_version, "
        "transformation_contract_version, metadata, edge_digest, created_at"
        ") VALUES ("
        f"{q(edge.tenant_id)}, {q(edge.source_id)}, {q(edge.hat_scope_id)}, "
        f"{q(edge_id)}, {q(parent_digest)}, {q(child_digest)}, "
        f"{q(edge.edge_kind)}, {q(edge.parser.parser_name)}, "
        f"{q(edge.parser.parser_version)}, "
        f"{q(edge.parser.parser_contract_version)}, "
        f"{q(edge.transformation.transformation_name)}, "
        f"{q(edge.transformation.transformation_version)}, "
        f"{q(edge.transformation.transformation_contract_version)}, "
        f"{sql_json(edge.metadata)}, {q(edge_digest)}, "
        f"{timestamp_sql(edge.created_at)})"
    )


def create_test_roles(
    root: migrations.SqlClient,
    roles: Mapping[str, str],
) -> None:
    statements = [
        "SET allow_role_memberships_to_change_during_transaction = true"
    ]
    for role in roles.values():
        statements.append(
            f"CREATE ROLE {quoted_role(role)} "
            "WITH LOGIN NOCREATEROLE NOCREATEDB NOBYPASSRLS"
        )
    for key in ("a1", "a2", "b1"):
        statements.append(
            "GRANT mp_app_runtime, mp_request_context_setter TO "
            f"{quoted_role(roles[key])}"
        )
    for key in ("publisher_a", "publisher_b"):
        statements.append(
            "GRANT mp_source_publication_worker, mp_request_context_setter TO "
            f"{quoted_role(roles[key])}"
        )
    statements.append(
        f"GRANT mp_app_runtime TO {quoted_role(roles['runtime_only'])}"
    )
    statements.append(
        "GRANT mp_schema_owner, mp_app_runtime, mp_request_context_setter TO "
        f"{quoted_role(roles['owner_probe'])}"
    )
    root.execute("defaultdb", ";\n".join(statements), timeout=180)


def drop_roles(
    root: migrations.SqlClient,
    roles: Mapping[str, str],
) -> None:
    statements = [
        "SET allow_role_memberships_to_change_during_transaction = true"
    ]
    statements.extend(
        f"DROP ROLE IF EXISTS {quoted_role(role)}" for role in roles.values()
    )
    statements.extend(f'DROP ROLE IF EXISTS "{role}"' for role in FIXED_ROLES)
    root.execute("defaultdb", ";\n".join(statements), timeout=180)


def scalar_in_context(
    client: rls.RoleSqlClient,
    database: str,
    tenant_id: str,
    user_id: str | None,
    access_mode: str,
    query: str,
) -> str:
    return rls.context_scalar(
        client,
        database,
        tenant_id,
        user_id,
        access_mode,
        query,
    )


def expect_context_error(
    client: rls.RoleSqlClient,
    database: str,
    tenant_id: str,
    user_id: str | None,
    access_mode: str,
    statement: str,
    expected: set[str],
) -> str:
    return rls.expected_error_in_context(
        client,
        database,
        tenant_id,
        user_id,
        access_mode,
        statement,
        expected=expected,
    )


def run_live_validation(paths: LiveValidationPaths) -> dict[str, Any]:
    offline = offline_validate(run_tests=False)
    identity = rls.binary_identity(paths.binary)
    run_id = RUN_PREFIX + uuid.uuid4().hex[:12]
    primary_database = run_id + "_primary"
    reproduction_database = run_id + "_reproduction"
    ids = fixture_ids(run_id)
    roles = {
        "a1": run_id + "_role_a1",
        "a2": run_id + "_role_a2",
        "b1": run_id + "_role_b1",
        "publisher_a": run_id + "_publisher_a",
        "publisher_b": run_id + "_publisher_b",
        "runtime_only": run_id + "_runtime_only",
        "owner_probe": run_id + "_owner_probe",
    }
    expected_role_names = set(FIXED_ROLES) | set(roles.values())
    runtime = migrations.LocalRuntime(
        binary=paths.binary,
        run_id=run_id,
        runtime_parent=paths.runtime_parent,
    )
    recorder = ProbeRecorder()
    root: migrations.SqlClient | None = None
    created_databases: list[str] = []
    roles_created = False
    role_cleanup_verified = False
    cleanup_errors: list[str] = []
    failure: BaseException | None = None
    result: dict[str, Any] | None = None
    runtime_cleanup: dict[str, Any] = {
        "cleanup_errors": [],
        "force_kill_used": False,
        "panic_detected": False,
        "pid_exited": False,
        "ports_closed": False,
        "temporary_store_removed": False,
    }
    try:
        _create_runtime_parent(paths)
        print("LIVE PROGRESS: starting owned runtime", file=sys.stderr, flush=True)
        root = runtime.start()
        print("LIVE PROGRESS: runtime ready", file=sys.stderr, flush=True)
        server_version = migrations.one_value(
            root.execute("defaultdb", "SELECT version()")
        )
        cluster_version = migrations.one_value(
            root.execute("defaultdb", "SHOW CLUSTER SETTING version")
        )
        recorder.check(
            "LIVE-001",
            "identity",
            PINNED_VERSION in server_version,
            "server reports exact pinned release",
        )
        recorder.check(
            "LIVE-002",
            "identity",
            cluster_version == PINNED_CLUSTER_VERSION,
            "cluster version is 26.2",
        )
        for database in (primary_database, reproduction_database):
            migrations.create_database(root, database)
            created_databases.append(database)
        print("LIVE PROGRESS: disposable databases ready", file=sys.stderr, flush=True)
        first_apply = migrations.apply_migrations(
            root,
            primary_database,
            timeout=180,
        )
        no_op_apply = migrations.apply_migrations(
            root,
            primary_database,
            timeout=180,
        )
        reproduction_apply = migrations.apply_migrations(
            root,
            reproduction_database,
            timeout=180,
        )
        print("LIVE PROGRESS: migrations applied and replayed", file=sys.stderr, flush=True)
        recorder.check(
            "LIVE-003",
            "migration",
            first_apply["applied_count"] == 19,
            "nineteen migrations applied from zero",
        )
        recorder.check(
            "LIVE-004",
            "migration",
            no_op_apply["applied_count"] == 0
            and no_op_apply["skipped_count"] == 19,
            "checksum-verified migration replay was a complete no-op",
        )
        recorder.check(
            "LIVE-005",
            "migration",
            reproduction_apply["applied_count"] == 19,
            "second fresh database applied the same nineteen migrations",
        )
        primary_schema = migrations.schema_catalog(root, primary_database)
        reproduction_schema = migrations.schema_catalog(
            root,
            reproduction_database,
        )
        primary_security = migrations.assert_step9_security_catalog(
            root,
            primary_database,
        )
        reproduction_security = migrations.assert_step9_security_catalog(
            root,
            reproduction_database,
        )
        print("LIVE PROGRESS: schema/security catalogs verified", file=sys.stderr, flush=True)
        schema_security_digest = hashlib.sha256(
            canonical_json_bytes(
                {
                    "schema": primary_schema["schema_digest"],
                    "security": primary_security["security_digest"],
                }
            )
        ).hexdigest()
        reproduction_digest = hashlib.sha256(
            canonical_json_bytes(
                {
                    "schema": reproduction_schema["schema_digest"],
                    "security": reproduction_security["security_digest"],
                }
            )
        ).hexdigest()
        recorder.check(
            "LIVE-006",
            "migration",
            schema_security_digest == reproduction_digest,
            "two fresh databases reproduced one schema/security digest",
        )
        create_test_roles(root, roles)
        roles_created = True
        disposable_roles_sql = ", ".join(
            migrations.sql_literal(role) for role in sorted(roles.values())
        )
        membership_rows = migrations.parse_tsv(
            root.execute(
                "defaultdb",
                "SELECT parent.rolname AS parent_role, "
                "member.rolname AS member_role "
                "FROM pg_catalog.pg_auth_members AS membership "
                "JOIN pg_catalog.pg_roles AS parent "
                "ON parent.oid = membership.roleid "
                "JOIN pg_catalog.pg_roles AS member "
                "ON member.oid = membership.member "
                f"WHERE member.rolname IN ({disposable_roles_sql}) "
                "ORDER BY parent.rolname, member.rolname",
            )
        )
        actual_memberships = {
            (row["parent_role"], row["member_role"])
            for row in membership_rows
        }
        expected_memberships = {
            ("mp_app_runtime", roles["a1"]),
            ("mp_request_context_setter", roles["a1"]),
            ("mp_app_runtime", roles["a2"]),
            ("mp_request_context_setter", roles["a2"]),
            ("mp_app_runtime", roles["b1"]),
            ("mp_request_context_setter", roles["b1"]),
            ("mp_app_runtime", roles["runtime_only"]),
            ("mp_schema_owner", roles["owner_probe"]),
            ("mp_app_runtime", roles["owner_probe"]),
            ("mp_request_context_setter", roles["owner_probe"]),
            ("mp_source_publication_worker", roles["publisher_a"]),
            ("mp_request_context_setter", roles["publisher_a"]),
            ("mp_source_publication_worker", roles["publisher_b"]),
            ("mp_request_context_setter", roles["publisher_b"]),
        }
        recorder.check(
            "LIVE-STEP36-ROLE-001",
            "roles",
            actual_memberships == expected_memberships,
            "publication LOGINs inherit only publisher and trusted-context "
            "capabilities while registration LOGINs retain only app authority",
        )
        root.execute(primary_database, base_fixture_sql(ids), timeout=180)
        print("LIVE PROGRESS: roles and base fixtures ready", file=sys.stderr, flush=True)
        assert runtime.sql_port is not None
        clients = {
            key: rls.RoleSqlClient(
                binary=paths.binary,
                port=runtime.sql_port,
                user=role,
            )
            for key, role in roles.items()
        }
        records = {
            suffix: make_record(
                ids,
                suffix,
                license_status=(
                    SourceLicenseStatus.PROHIBITED
                    if suffix == "shared_b"
                    else SourceLicenseStatus.PUBLIC_DOMAIN
                ),
            )
            for suffix in (
                "shared_a",
                "shared_b",
                "private_a1",
                "private_a2",
                "private_b1",
            )
        }
        contexts = {
            "shared_a": ("a1", ids["tenant_a"], None, "TENANT_SHARED"),
            "shared_b": ("b1", ids["tenant_b"], None, "TENANT_SHARED"),
            "private_a1": (
                "a1",
                ids["tenant_a"],
                ids["user_a1"],
                "USER_PRIVATE",
            ),
            "private_a2": (
                "a2",
                ids["tenant_a"],
                ids["user_a2"],
                "USER_PRIVATE",
            ),
            "private_b1": (
                "b1",
                ids["tenant_b"],
                ids["user_b1"],
                "USER_PRIVATE",
            ),
        }
        forged = replace(
            records["shared_a"],
            current_publication_state=SourcePublicationState.PUBLISHED,
            current_publication_sequence=3,
            current_publication_event_digest=digest_text(
                "forged-publication-pointer"
            ),
            updated_at=FIXTURE_TIME + timedelta(seconds=1),
        )
        forged_state = expect_context_error(
            clients["a1"],
            primary_database,
            ids["tenant_a"],
            None,
            "TENANT_SHARED",
            registry_insert_sql(forged),
            {"42501"},
        )
        recorder.check(
            "LIVE-GENESIS-001",
            "publication_negative",
            forged_state == "42501",
            "runtime registration cannot bypass exact publication genesis",
            sqlstate=forged_state,
        )
        registration_operations: dict[str, str] = {}
        for number, suffix in enumerate(records, start=7):
            client_key, tenant_id, user_id, mode = contexts[suffix]
            record = records[suffix]
            operation_id = f"{run_id}_register_{suffix}"
            registration_operations[suffix] = operation_id
            count = scalar_in_context(
                clients[client_key],
                primary_database,
                tenant_id,
                user_id,
                mode,
                durable_mutation_sql(
                    tenant_id=tenant_id,
                    owner_user_id=record.scope.owner_user_id,
                    operation_id=operation_id,
                    operation_kind="SOURCE_REGISTER",
                    idempotency_key=f"register-source-{suffix}",
                    request_digest=record.registry_digest,
                    scope_digest=record.scope.scope_digest,
                    result_ref=record.source_id,
                    result_digest=record.registry_digest,
                    occurred_at=record.created_at,
                    mutation_sql=registry_insert_sql(record),
                )
                + ";\nSELECT count(*) AS probe_value "
                "FROM memory_patch.source_registry_entries "
                f"WHERE source_id = {migrations.sql_literal(record.source_id)}",
            )
            recorder.check(
                f"LIVE-{number:03d}",
                "positive",
                count == "1",
                f"{suffix} registry insert and read succeeded",
            )
        completed_registration_count = migrations.one_value(
            root.execute(
                primary_database,
                "SELECT count(*) FROM memory_patch.persistence_operations "
                "WHERE operation_kind = 'SOURCE_REGISTER' "
                "AND status = 'COMPLETED' AND attempt_count = 1",
            )
        )
        recorder.check(
            "LIVE-IDEMPOTENCY-001",
            "idempotency",
            completed_registration_count == "5",
            "all source registrations reused durable Step 6 operation state",
        )
        registration_conflict = expect_context_error(
            clients["a1"],
            primary_database,
            ids["tenant_a"],
            None,
            "TENANT_SHARED",
            operation_begin_sql(
                tenant_id=ids["tenant_a"],
                owner_user_id=None,
                operation_id=f"{run_id}_register_conflict",
                operation_kind="SOURCE_REGISTER",
                idempotency_key="register-source-shared_a",
                request_digest=digest_text("conflicting-register-request"),
                scope_digest=records["shared_a"].scope.scope_digest,
                created_at=FIXTURE_TIME,
            ),
            {"23505"},
        )
        recorder.check(
            "LIVE-IDEMPOTENCY-002",
            "idempotency",
            registration_conflict == "23505",
            "changed request cannot reuse a Step 9 registration key",
            sqlstate=registration_conflict,
        )
        stale_completion = scalar_in_context(
            clients["a1"],
            primary_database,
            ids["tenant_a"],
            None,
            "TENANT_SHARED",
            "WITH changed AS ("
            "UPDATE memory_patch.persistence_operations "
            "SET status = 'COMPLETED' "
            f"WHERE operation_id = {migrations.sql_literal(registration_operations['shared_a'])} "
            "AND status = 'IN_PROGRESS' AND attempt_count = 1 "
            "RETURNING operation_id"
            ") SELECT count(*) AS probe_value FROM changed",
        )
        recorder.check(
            "LIVE-IDEMPOTENCY-003",
            "idempotency",
            stale_completion == "0",
            "stale Step 6 completion compare-and-set changed zero rows",
        )
        print("LIVE PROGRESS: registry access probes passed", file=sys.stderr, flush=True)
        edge = ProvenanceEdge(
            tenant_id=records["shared_a"].tenant_id,
            source_id=records["shared_a"].source_id,
            hat_scope_id=records["shared_a"].hat_scope_id,
            edge_id=run_id + "_edge_shared_a",
            parent_artifact_digest=digest_text("step9-live-root"),
            child_artifact_digest=records["shared_a"].artifact.artifact_digest,
            edge_kind="EXACT_SOURCE_LINEAGE",
            parser=records["shared_a"].parser,
            transformation=records["shared_a"].transformation,
            metadata={"fixture": "step9"},
            created_at=FIXTURE_TIME,
        )
        edge_operation_id = f"{run_id}_provenance_edge"
        edge_count = scalar_in_context(
            clients["a1"],
            primary_database,
            ids["tenant_a"],
            None,
            "TENANT_SHARED",
            durable_mutation_sql(
                tenant_id=edge.tenant_id,
                owner_user_id=None,
                operation_id=edge_operation_id,
                operation_kind="PROVENANCE_EDGE_APPEND",
                idempotency_key="provenance-edge-shared-a",
                request_digest=edge.edge_digest,
                scope_digest=records["shared_a"].scope.scope_digest,
                result_ref=edge.edge_id,
                result_digest=edge.edge_digest,
                occurred_at=edge.created_at,
                mutation_sql=edge_insert_sql(edge),
            )
            + ";\nSELECT count(*) AS probe_value "
            "FROM memory_patch.source_provenance_edges "
            f"WHERE edge_id = {migrations.sql_literal(edge.edge_id)}",
        )
        recorder.check(
            "LIVE-012",
            "provenance",
            edge_count == "1",
            "same-scope provenance edge appended",
        )
        replay_count = scalar_in_context(
            clients["a1"],
            primary_database,
            ids["tenant_a"],
            None,
            "TENANT_SHARED",
            edge_insert_sql(edge, replay_safe=True)
            + ";\nSELECT count(*) AS probe_value "
            "FROM memory_patch.source_provenance_edges "
            f"WHERE edge_id = {migrations.sql_literal(edge.edge_id)}",
        )
        recorder.check(
            "LIVE-013",
            "idempotency",
            replay_count == "1",
            "exact edge replay retained one immutable fact",
        )
        conflicting_edge = replace(
            edge,
            child_artifact_digest=digest_text("conflicting-child"),
            edge_digest="",
        )
        conflict_state = expect_context_error(
            clients["a1"],
            primary_database,
            ids["tenant_a"],
            None,
            "TENANT_SHARED",
            edge_insert_sql(conflicting_edge),
            {"23505"},
        )
        recorder.check(
            "LIVE-PROVENANCE-001",
            "provenance_negative",
            conflict_state == "23505",
            "same edge identity with changed canonical facts was rejected",
            sqlstate=conflict_state,
        )
        self_edge_state = expect_context_error(
            clients["a1"],
            primary_database,
            ids["tenant_a"],
            None,
            "TENANT_SHARED",
            raw_edge_insert_sql(
                edge=edge,
                edge_id=f"{run_id}_self_edge",
                parent_digest=edge.child_artifact_digest,
                child_digest=edge.child_artifact_digest,
                edge_digest=digest_text("self-edge-fact"),
            ),
            {"23514"},
        )
        recorder.check(
            "LIVE-PROVENANCE-002",
            "provenance_negative",
            self_edge_state == "23514",
            "database constraint rejected a provenance self-edge",
            sqlstate=self_edge_state,
        )
        reverse_edge = ProvenanceEdge(
            tenant_id=edge.tenant_id,
            source_id=edge.source_id,
            hat_scope_id=edge.hat_scope_id,
            edge_id=f"{run_id}_cycle_edge",
            parent_artifact_digest=edge.child_artifact_digest,
            child_artifact_digest=edge.parent_artifact_digest,
            edge_kind="FORBIDDEN_CYCLE",
            parser=edge.parser,
            transformation=edge.transformation,
            metadata={"fixture": "step9"},
            created_at=FIXTURE_TIME + timedelta(seconds=1),
        )
        cycle_rejected = False
        try:
            ProvenanceGraph((edge,)).add_edge(reverse_edge)
        except ProvenanceCycleError:
            cycle_rejected = True
        cycle_count = migrations.one_value(
            root.execute(
                primary_database,
                "SELECT count(*) FROM memory_patch.source_provenance_edges "
                f"WHERE edge_id = {migrations.sql_literal(reverse_edge.edge_id)}",
            )
        )
        recorder.check(
            "LIVE-PROVENANCE-003",
            "provenance_negative",
            cycle_rejected and cycle_count == "0",
            "trusted repository DAG guard rejected a cycle before persistence",
        )
        completed_edge_operation = migrations.one_value(
            root.execute(
                primary_database,
                "SELECT count(*) FROM memory_patch.persistence_operations "
                f"WHERE operation_id = {migrations.sql_literal(edge_operation_id)} "
                "AND operation_kind = 'PROVENANCE_EDGE_APPEND' "
                "AND status = 'COMPLETED' "
                f"AND result_digest = {migrations.sql_literal(edge.edge_digest)}",
            )
        )
        recorder.check(
            "LIVE-IDEMPOTENCY-004",
            "idempotency",
            completed_edge_operation == "1",
            "provenance append completed through durable Step 6 state",
        )
        print("LIVE PROGRESS: provenance probes passed", file=sys.stderr, flush=True)
        graph = ProvenanceGraph((edge,))
        eligible = evaluate_publication_eligibility(
            records["shared_a"],
            graph,
            evaluated_at=FIXTURE_TIME + timedelta(minutes=1),
        )
        ineligible = evaluate_publication_eligibility(
            records["shared_b"],
            ProvenanceGraph(),
            evaluated_at=FIXTURE_TIME + timedelta(minutes=1),
        )
        recorder.check(
            "LIVE-014",
            "eligibility",
            eligible.eligible,
            "eligible shared source passed deterministic policy",
        )
        recorder.check(
            "LIVE-015",
            "eligibility",
            not ineligible.eligible
            and "LICENSE_PROHIBITED" in ineligible.reason_codes,
            "prohibited source failed deterministic policy",
        )
        print("LIVE PROGRESS: eligibility probes passed", file=sys.stderr, flush=True)
        current = records["shared_a"]
        events = []
        trusted_actor = SourceRegistryActor(
            SourceRegistryActorType.HUMAN_REVIEWER,
            "synthetic-human-review-boundary",
        )
        for offset, target in enumerate(
            (
                SourcePublicationState.REVIEW_REQUIRED,
                SourcePublicationState.ELIGIBLE,
                SourcePublicationState.PUBLISHED,
            ),
            start=1,
        ):
            decision = evaluate_publication_eligibility(
                current,
                graph,
                evaluated_at=FIXTURE_TIME + timedelta(minutes=offset),
            )
            event = build_publication_event(
                current,
                event_id=f"{run_id}_event_{offset}",
                target_state=target,
                eligibility=decision,
                actor=trusted_actor,
                reason_codes=(),
                reviewer_reference="synthetic-human-reviewer",
                created_at=FIXTURE_TIME + timedelta(minutes=offset),
            )
            transition_operation_id = f"{run_id}_transition_{offset}"
            transition_sql = durable_mutation_sql(
                tenant_id=current.tenant_id,
                owner_user_id=None,
                operation_id=transition_operation_id,
                operation_kind="PUBLICATION_STATE_TRANSITION",
                idempotency_key=f"publication-transition-{offset}",
                request_digest=event.event_digest,
                scope_digest=current.scope.scope_digest,
                result_ref=event.event_id,
                result_digest=event.event_digest,
                occurred_at=event.created_at,
                mutation_sql=publication_transaction_sql(current, event),
            )
            if offset == 1:
                app_publication_state = expect_context_error(
                    clients["a1"],
                    primary_database,
                    ids["tenant_a"],
                    None,
                    "TENANT_SHARED",
                    transition_sql,
                    {"42501"},
                )
                recorder.check(
                    "LIVE-STEP36-PUBLISHER-002",
                    "publication_negative",
                    app_publication_state == "42501",
                    "normal app registration authority cannot perform a "
                    "publication transition",
                    sqlstate=app_publication_state,
                )
            clients["publisher_a"].execute(
                primary_database,
                rls.context_transaction(
                    ids["tenant_a"],
                    None,
                    "TENANT_SHARED",
                    transition_sql,
                ),
            )
            events.append(event)
            current = advance_registry_state(current, event)
        final_state = scalar_in_context(
            clients["publisher_a"],
            primary_database,
            ids["tenant_a"],
            None,
            "TENANT_SHARED",
            "SELECT current_publication_state AS probe_value "
            "FROM memory_patch.source_registry_entries "
            f"WHERE source_id = {migrations.sql_literal(current.source_id)}",
        )
        recorder.check(
            "LIVE-016",
            "publication",
            final_state == "PUBLISHED",
            "legal transition chain reached PUBLISHED",
        )
        stored_event_rows = migrations.parse_tsv(
            root.execute(
                primary_database,
                "SELECT tenant_id, source_id, hat_scope_id, event_id, "
                "sequence_number, from_state, to_state, actor_type, "
                "actor_reference, policy_version, eligibility_decision_digest, "
                "reason_codes, reviewer_reference, previous_event_digest, "
                "event_digest, created_at "
                "FROM memory_patch.source_publication_events "
                f"WHERE source_id = {migrations.sql_literal(current.source_id)} "
                "ORDER BY sequence_number",
            )
        )
        stored_events = tuple(event_from_row(row) for row in stored_event_rows)
        verified_events = verify_publication_event_chain(current, stored_events)
        recorder.check(
            "LIVE-017",
            "publication",
            verified_events == tuple(events),
            "database-read event sequence, digests, actors, links, and terminal "
            "pointer verified",
        )
        pointer_match = migrations.one_value(
            root.execute(
                primary_database,
                "SELECT count(*) FROM memory_patch.source_registry_entries AS registry "
                "JOIN memory_patch.source_publication_events AS event "
                "ON event.tenant_id = registry.tenant_id "
                "AND event.source_id = registry.source_id "
                "AND event.hat_scope_id = registry.hat_scope_id "
                "AND event.sequence_number = registry.current_publication_sequence "
                "AND event.to_state = registry.current_publication_state "
                "AND event.event_digest = registry.current_publication_event_digest "
                f"WHERE registry.source_id = {migrations.sql_literal(current.source_id)}",
            )
        )
        recorder.check(
            "LIVE-PUBLICATION-001",
            "publication",
            pointer_match == "1",
            "current registry state points to exactly one terminal event",
        )
        transition_operation_count = migrations.one_value(
            root.execute(
                primary_database,
                "SELECT count(*) FROM memory_patch.persistence_operations "
                "WHERE operation_kind = 'PUBLICATION_STATE_TRANSITION' "
                "AND status = 'COMPLETED' AND attempt_count = 1",
            )
        )
        recorder.check(
            "LIVE-IDEMPOTENCY-005",
            "idempotency",
            transition_operation_count == "3",
            "all publication transitions reused durable Step 6 operation state",
        )
        stale_pointer = scalar_in_context(
            clients["publisher_a"],
            primary_database,
            ids["tenant_a"],
            None,
            "TENANT_SHARED",
            "WITH changed AS ("
            "UPDATE memory_patch.source_registry_entries SET "
            "updated_at = updated_at "
            f"WHERE source_id = {migrations.sql_literal(current.source_id)} "
            "AND current_publication_state = 'REGISTERED' "
            "AND current_publication_sequence = 0 "
            f"AND current_publication_event_digest = "
            f"{migrations.sql_literal(PUBLICATION_GENESIS_DIGEST)} "
            "RETURNING source_id"
            ") SELECT count(*) AS probe_value FROM changed",
        )
        recorder.check(
            "LIVE-PUBLICATION-002",
            "publication_negative",
            stale_pointer == "0",
            "stale publication compare-and-set changed zero rows",
        )
        tamper_state = expect_context_error(
            clients["publisher_a"],
            primary_database,
            ids["tenant_a"],
            None,
            "TENANT_SHARED",
            "UPDATE memory_patch.source_publication_events SET "
            "actor_reference = 'tampered-boundary' "
            f"WHERE event_id = {migrations.sql_literal(events[0].event_id)}",
            {"42501"},
        )
        recorder.check(
            "LIVE-PUBLICATION-003",
            "publication_negative",
            tamper_state == "42501",
            "append-only publication event tampering was denied",
            sqlstate=tamper_state,
        )
        delete_event_state = expect_context_error(
            clients["publisher_a"],
            primary_database,
            ids["tenant_a"],
            None,
            "TENANT_SHARED",
            "DELETE FROM memory_patch.source_publication_events "
            f"WHERE event_id = {migrations.sql_literal(events[0].event_id)}",
            {"42501"},
        )
        recorder.check(
            "LIVE-PUBLICATION-004",
            "publication_negative",
            delete_event_state == "42501",
            "append-only publication event deletion was denied",
            sqlstate=delete_event_state,
        )
        withdrawal_decision = evaluate_publication_eligibility(
            current,
            graph,
            evaluated_at=FIXTURE_TIME + timedelta(minutes=4),
        )
        withdrawal_event = build_publication_event(
            current,
            event_id=f"{run_id}_wrong_link_event",
            target_state=SourcePublicationState.WITHDRAWN,
            eligibility=withdrawal_decision,
            actor=trusted_actor,
            reason_codes=("WRONG_LINK_PROBE",),
            reviewer_reference="synthetic-human-reviewer",
            created_at=FIXTURE_TIME + timedelta(minutes=4),
        )
        wrong_link_operation_id = f"{run_id}_wrong_link_operation"
        wrong_link_sql = durable_mutation_sql(
            tenant_id=current.tenant_id,
            owner_user_id=None,
            operation_id=wrong_link_operation_id,
            operation_kind="PUBLICATION_STATE_TRANSITION",
            idempotency_key="publication-wrong-link-probe",
            request_digest=withdrawal_event.event_digest,
            scope_digest=current.scope.scope_digest,
            result_ref=withdrawal_event.event_id,
            result_digest=withdrawal_event.event_digest,
            occurred_at=withdrawal_event.created_at,
            mutation_sql=publication_transaction_sql(
                current,
                withdrawal_event,
                stored_previous_event_digest=digest_text(
                    "wrong-previous-event"
                ),
            ),
        )
        wrong_link_state = expect_context_error(
            clients["publisher_a"],
            primary_database,
            ids["tenant_a"],
            None,
            "TENANT_SHARED",
            wrong_link_sql,
            {"23514"},
        )
        rolled_back_wrong_link = migrations.one_value(
            root.execute(
                primary_database,
                "SELECT count(*) FROM memory_patch.source_publication_events "
                f"WHERE event_id = {migrations.sql_literal(withdrawal_event.event_id)}",
            )
        )
        rolled_back_operation = migrations.one_value(
            root.execute(
                primary_database,
                "SELECT count(*) FROM memory_patch.persistence_operations "
                f"WHERE operation_id = "
                f"{migrations.sql_literal(wrong_link_operation_id)}",
            )
        )
        pointer_after_rollback = migrations.one_value(
            root.execute(
                primary_database,
                "SELECT count(*) FROM memory_patch.source_registry_entries "
                f"WHERE source_id = {migrations.sql_literal(current.source_id)} "
                f"AND current_publication_state = "
                f"{migrations.sql_literal(current.current_publication_state.value)} "
                f"AND current_publication_sequence = "
                f"{current.current_publication_sequence} "
                f"AND current_publication_event_digest = "
                f"{migrations.sql_literal(current.current_publication_event_digest)}",
            )
        )
        recorder.check(
            "LIVE-PUBLICATION-005",
            "publication_negative",
            wrong_link_state == "23514"
            and rolled_back_wrong_link == "0"
            and rolled_back_operation == "0"
            and pointer_after_rollback == "1",
            "wrong link, Step 6 operation, and pointer mutation rolled back "
            "atomically",
            sqlstate=wrong_link_state,
        )
        print("LIVE PROGRESS: publication chain passed", file=sys.stderr, flush=True)
        illegal_state = expect_context_error(
            clients["publisher_b"],
            primary_database,
            ids["tenant_b"],
            None,
            "TENANT_SHARED",
            "INSERT INTO memory_patch.source_publication_events "
            "(tenant_id, source_id, hat_scope_id, event_id, sequence_number, "
            "from_state, to_state, actor_type, actor_reference, policy_version, "
            "eligibility_decision_digest, reason_codes, reviewer_reference, "
            "previous_event_digest, event_digest, created_at) VALUES "
            f"({migrations.sql_literal(ids['tenant_b'])}, "
            f"{migrations.sql_literal(ids['source_shared_b'])}, "
            f"{migrations.sql_literal(ids['scope_shared_b'])}, "
            f"{migrations.sql_literal(run_id + '_illegal_event')}, 1, "
            "'REGISTERED', 'PUBLISHED', 'HUMAN_REVIEWER', "
            "'synthetic-human-review-boundary', "
            "'source-publication-eligibility-1a', "
            f"{migrations.sql_literal(digest_text('eligibility'))}, '[]'::JSONB, "
            "NULL, "
            f"{migrations.sql_literal(PUBLICATION_GENESIS_DIGEST)}, "
            f"{migrations.sql_literal(digest_text('illegal-event'))}, "
            f"{timestamp_sql(FIXTURE_TIME + timedelta(minutes=1))})",
            {"23514"},
        )
        recorder.check(
            "LIVE-018",
            "publication_negative",
            illegal_state == "23514",
            "direct REGISTERED to PUBLISHED rejected",
            sqlstate=illegal_state,
        )
        for forbidden_actor in ("MODEL", "HAT", "CRITIC"):
            actor_state = expect_context_error(
                clients["publisher_b"],
                primary_database,
                ids["tenant_b"],
                None,
                "TENANT_SHARED",
                "INSERT INTO memory_patch.source_publication_events "
                "(tenant_id, source_id, hat_scope_id, event_id, "
                "sequence_number, from_state, to_state, actor_type, "
                "actor_reference, policy_version, "
                "eligibility_decision_digest, reason_codes, "
                "reviewer_reference, previous_event_digest, event_digest, "
                "created_at) VALUES ("
                f"{migrations.sql_literal(ids['tenant_b'])}, "
                f"{migrations.sql_literal(ids['source_shared_b'])}, "
                f"{migrations.sql_literal(ids['scope_shared_b'])}, "
                f"{migrations.sql_literal(run_id + '_actor_' + forbidden_actor.casefold())}, "
                "1, 'REGISTERED', 'REVIEW_REQUIRED', "
                f"{migrations.sql_literal(forbidden_actor)}, "
                "'untrusted-publication-actor', "
                "'source-publication-eligibility-1a', "
                f"{migrations.sql_literal(digest_text('actor-eligibility-' + forbidden_actor))}, "
                "'[]'::JSONB, NULL, "
                f"{migrations.sql_literal(PUBLICATION_GENESIS_DIGEST)}, "
                f"{migrations.sql_literal(digest_text('actor-event-' + forbidden_actor))}, "
                f"{timestamp_sql(FIXTURE_TIME + timedelta(minutes=1))})",
                {"23514"},
            )
            recorder.check(
                f"LIVE-ACTOR-{forbidden_actor}",
                "authority",
                actor_state == "23514",
                f"{forbidden_actor} cannot be recorded as a publication actor",
                sqlstate=actor_state,
            )
        authority_columns = migrations.one_value(
            root.execute(
                primary_database,
                "SELECT count(*) FROM information_schema.columns "
                "WHERE table_schema = 'memory_patch' "
                "AND table_name IN ('source_registry_entries', "
                "'source_provenance_edges', 'source_publication_events') "
                "AND column_name IN ('approval_authority', 'commit_authority', "
                "'execution_authority', 'answer_permission')",
            )
        )
        recorder.check(
            "LIVE-AUTHORITY-001",
            "authority",
            authority_columns == "0",
            "publication schema grants no approval, commit, execution, or "
            "answer authority",
        )
        update_state = expect_context_error(
            clients["publisher_b"],
            primary_database,
            ids["tenant_b"],
            None,
            "TENANT_SHARED",
            "UPDATE memory_patch.source_registry_entries SET "
            "current_publication_state = 'REVIEW_REQUIRED', "
            "current_publication_sequence = 1, "
            f"current_publication_event_digest = "
            f"{migrations.sql_literal(digest_text('missing-event'))}, "
            f"updated_at = {timestamp_sql(FIXTURE_TIME + timedelta(minutes=2))} "
            f"WHERE source_id = "
            f"{migrations.sql_literal(ids['source_shared_b'])}",
            {"23514"},
        )
        recorder.check(
            "LIVE-019",
            "publication_negative",
            update_state == "23514",
            "current state cannot advance without its exact event",
            sqlstate=update_state,
        )
        cross_tenant_read = scalar_in_context(
            clients["a1"],
            primary_database,
            ids["tenant_a"],
            None,
            "TENANT_SHARED",
            "SELECT count(*) AS probe_value "
            "FROM memory_patch.source_registry_entries "
            f"WHERE source_id = "
            f"{migrations.sql_literal(ids['source_shared_b'])}",
        )
        recorder.check(
            "LIVE-020",
            "cross_tenant",
            cross_tenant_read == "0",
            "Tenant A cannot read Tenant B registry identity",
        )
        foreign_edge = ProvenanceEdge(
            tenant_id=records["shared_b"].tenant_id,
            source_id=records["shared_b"].source_id,
            hat_scope_id=records["shared_b"].hat_scope_id,
            edge_id=run_id + "_cross_tenant_edge",
            parent_artifact_digest=digest_text("foreign-root"),
            child_artifact_digest=records["shared_b"].artifact.artifact_digest,
            edge_kind="FORBIDDEN_CROSS_TENANT",
            parser=records["shared_b"].parser,
            transformation=records["shared_b"].transformation,
            metadata={"fixture": "step9"},
            created_at=FIXTURE_TIME,
        )
        cross_tenant_write_state = expect_context_error(
            clients["a1"],
            primary_database,
            ids["tenant_a"],
            None,
            "TENANT_SHARED",
            edge_insert_sql(foreign_edge),
            {"42501"},
        )
        recorder.check(
            "LIVE-021",
            "cross_tenant",
            cross_tenant_write_state == "42501",
            "Tenant A cannot write Tenant B provenance",
            sqlstate=cross_tenant_write_state,
        )
        reverse_read = scalar_in_context(
            clients["b1"],
            primary_database,
            ids["tenant_b"],
            None,
            "TENANT_SHARED",
            "SELECT count(*) AS probe_value "
            "FROM memory_patch.source_registry_entries "
            f"WHERE source_id = "
            f"{migrations.sql_literal(ids['source_shared_a'])}",
        )
        recorder.check(
            "LIVE-022",
            "cross_tenant",
            reverse_read == "0",
            "Tenant B cannot read Tenant A registry identity",
        )
        cross_user_read = scalar_in_context(
            clients["a1"],
            primary_database,
            ids["tenant_a"],
            ids["user_a1"],
            "USER_PRIVATE",
            "SELECT count(*) AS probe_value "
            "FROM memory_patch.source_registry_entries "
            f"WHERE source_id = "
            f"{migrations.sql_literal(ids['source_private_a2'])}",
        )
        recorder.check(
            "LIVE-023",
            "cross_user",
            cross_user_read == "0",
            "User A1 cannot read User A2 private registry",
        )
        foreign_private_edge = ProvenanceEdge(
            tenant_id=records["private_a2"].tenant_id,
            source_id=records["private_a2"].source_id,
            hat_scope_id=records["private_a2"].hat_scope_id,
            edge_id=run_id + "_cross_user_edge",
            parent_artifact_digest=digest_text("private-root"),
            child_artifact_digest=records["private_a2"].artifact.artifact_digest,
            edge_kind="FORBIDDEN_CROSS_USER",
            parser=records["private_a2"].parser,
            transformation=records["private_a2"].transformation,
            metadata={"fixture": "step9"},
            created_at=FIXTURE_TIME,
        )
        cross_user_write_state = expect_context_error(
            clients["a1"],
            primary_database,
            ids["tenant_a"],
            ids["user_a1"],
            "USER_PRIVATE",
            edge_insert_sql(foreign_private_edge),
            {"42501"},
        )
        recorder.check(
            "LIVE-024",
            "cross_user",
            cross_user_write_state == "42501",
            "User A1 cannot write User A2 private provenance",
            sqlstate=cross_user_write_state,
        )
        tenant_only_private = scalar_in_context(
            clients["a1"],
            primary_database,
            ids["tenant_a"],
            None,
            "TENANT_SHARED",
            "SELECT count(*) AS probe_value "
            "FROM memory_patch.source_registry_entries "
            "WHERE access_class = 'USER_PRIVATE'",
        )
        recorder.check(
            "LIVE-025",
            "context",
            tenant_only_private == "0",
            "tenant-only context reveals no private registry rows",
        )
        unset_output = clients["runtime_only"].execute(
            primary_database,
            "SELECT count(*) AS probe_value "
            "FROM memory_patch.source_registry_entries",
        )
        unset_count = rls.extract_named_scalar(unset_output)
        recorder.check(
            "LIVE-026",
            "context",
            unset_count == "0",
            "unset context fails closed",
        )
        owner_output = clients["owner_probe"].execute(
            primary_database,
            "SELECT count(*) AS probe_value "
            "FROM memory_patch.source_registry_entries",
        )
        owner_count = rls.extract_named_scalar(owner_output)
        recorder.check(
            "LIVE-027",
            "force_rls",
            owner_count == "0",
            "schema-owner member remains constrained by FORCE RLS",
        )
        quoted_expected_roles = ", ".join(
            migrations.sql_literal(role)
            for role in sorted(expected_role_names)
        )
        role_rows = migrations.parse_tsv(
            root.execute(
                "defaultdb",
                "SELECT rolname, rolbypassrls, rolcreaterole, rolcreatedb, "
                "rolsuper FROM pg_catalog.pg_roles "
                f"WHERE rolname IN ({quoted_expected_roles}) "
                "ORDER BY rolname",
            )
        )
        safe_roles = (
            {row["rolname"] for row in role_rows} == expected_role_names
            and all(
                row["rolbypassrls"] == "f"
                and row["rolcreaterole"] == "f"
                and row["rolcreatedb"] == "f"
                and row["rolsuper"] == "f"
                for row in role_rows
            )
        )
        recorder.check(
            "LIVE-028",
            "roles",
            safe_roles,
            "all normal and test roles lack bypass/admin options",
        )
        delete_state = expect_context_error(
            clients["a1"],
            primary_database,
            ids["tenant_a"],
            None,
            "TENANT_SHARED",
            "DELETE FROM memory_patch.source_registry_entries "
            f"WHERE source_id = "
            f"{migrations.sql_literal(ids['source_shared_a'])}",
            {"42501"},
        )
        recorder.check(
            "LIVE-029",
            "privilege",
            delete_state == "42501",
            "runtime DELETE is denied",
            sqlstate=delete_state,
        )
        event_count = scalar_in_context(
            clients["a1"],
            primary_database,
            ids["tenant_a"],
            None,
            "TENANT_SHARED",
            "SELECT count(*) AS probe_value "
            "FROM memory_patch.source_publication_events "
            f"WHERE source_id = "
            f"{migrations.sql_literal(ids['source_shared_a'])}",
        )
        recorder.check(
            "LIVE-030",
            "publication",
            event_count == "3",
            "exact append-only publication history retained",
        )
        print("LIVE PROGRESS: isolation and authority probes passed", file=sys.stderr, flush=True)
        result = {
            "binary_identity": identity,
            "cloud_api_calls": 0,
            "cluster_version": cluster_version,
            "fixture_summary": {
                "real_content": False,
                "source_count": 5,
                "synthetic_only": True,
                "tenant_count": 2,
                "user_count": 3,
            },
            "insecure_local_transport": {
                "limitations": (
                    "Validates SQL identity, transaction context, RLS, FORCE "
                    "RLS, schema, and denial semantics only; it does not prove "
                    "production certificate or end-user authentication."
                ),
                "loopback_only": True,
                "used": True,
            },
            "migration": {
                "first_apply": first_apply,
                "no_op_apply": no_op_apply,
                "reproduction_apply": reproduction_apply,
            },
            "offline": offline,
            "probes": recorder.rows,
            "probe_summary": recorder.summary(),
            "schema_security_digest": schema_security_digest,
            "reproduction_database": {
                "digest_matched": True,
                "reproduction_digest": reproduction_digest,
            },
            "security_catalog": primary_security,
            "server_version": server_version.splitlines()[0],
            "status": "PASS",
        }
    except BaseException as exc:
        failure = exc
    finally:
        if root is not None:
            for database in reversed(created_databases):
                try:
                    migrations.drop_database(root, database, timeout=180)
                except Exception as exc:
                    cleanup_errors.append(f"database cleanup: {exc}")
            if roles_created or created_databases:
                try:
                    drop_roles(root, roles)
                    quoted_cleanup_roles = ", ".join(
                        migrations.sql_literal(role)
                        for role in sorted(expected_role_names)
                    )
                    remaining = int(
                        migrations.one_value(
                            root.execute(
                                "defaultdb",
                                "SELECT count(*) FROM pg_catalog.pg_roles "
                                f"WHERE rolname IN ({quoted_cleanup_roles})",
                            )
                        )
                    )
                    role_cleanup_verified = remaining == 0
                    if not role_cleanup_verified:
                        cleanup_errors.append(
                            f"disposable/fixed roles remain: {remaining}"
                        )
                except Exception as exc:
                    cleanup_errors.append(f"role cleanup: {exc}")
        try:
            runtime_cleanup = runtime.stop_and_remove()
            cleanup_errors.extend(runtime_cleanup["cleanup_errors"])
            if runtime_cleanup["force_kill_used"]:
                cleanup_errors.append(
                    "graceful shutdown required a forced kill"
                )
            if runtime_cleanup["panic_detected"]:
                cleanup_errors.append(
                    "owned CockroachDB runtime reported a panic"
                )
        except Exception:
            cleanup_errors.append(
                "owned CockroachDB runtime cleanup raised an exception"
            )
        finally:
            cleanup_errors.extend(_remove_empty_runtime_parents(paths))
    if cleanup_errors:
        raise SourceRegistryValidationError(
            f"live cleanup failed: {cleanup_errors}"
        )
    if failure is not None:
        if isinstance(failure, Exception):
            raise failure
        raise SourceRegistryValidationError("live validation was interrupted")
    if result is None:
        raise SourceRegistryValidationError(
            "live validation produced no result"
        )
    result["cleanup"] = {
        "databases_removed": len(created_databases) == 2,
        "disposable_and_fixed_roles_removed": role_cleanup_verified,
        "force_kill_used": runtime_cleanup["force_kill_used"],
        "panic_detected": runtime_cleanup["panic_detected"],
        "owned_pid_exited": runtime_cleanup["pid_exited"],
        "ports_closed": runtime_cleanup["ports_closed"],
        "temporary_store_removed": runtime_cleanup["temporary_store_removed"],
    }
    result["generated_at_utc"] = migrations.utc_now()
    post_shutdown_identity = rls.binary_identity(paths.binary)
    if post_shutdown_identity != identity:
        raise SourceRegistryValidationError(
            "CockroachDB binary identity changed during live validation"
        )
    result["post_shutdown_binary_identity"] = post_shutdown_identity
    return result


def write_json_output(path: Path, value: Mapping[str, Any]) -> None:
    output = path.expanduser().resolve()
    temporary = output.with_name(output.name + ".partial")
    if output.exists() or temporary.exists():
        raise SourceRegistryValidationError(
            "refusing to overwrite live evidence output"
        )
    created_temporary = False
    created_output = False
    try:
        with temporary.open("xb") as handle:
            created_temporary = True
            handle.write(canonical_json_bytes(value))
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.link(temporary, output)
        created_output = True
        temporary.unlink()
        created_temporary = False
        directory_fd = os.open(
            output.parent,
            os.O_RDONLY | os.O_DIRECTORY,
        )
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except OSError as exc:
        if created_temporary and temporary.exists():
            temporary.unlink()
        if created_output and output.exists():
            output.unlink()
        raise SourceRegistryValidationError(
            "could not create live evidence without overwriting"
        ) from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--offline-validate", action="store_true")
    action.add_argument("--live-test", action="store_true")
    parser.add_argument("--allow-live", action="store_true")
    parser.add_argument("--cockroach-binary", type=Path)
    parser.add_argument("--runtime-root", type=Path)
    parser.add_argument("--json-output", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.offline_validate:
            result = offline_validate()
        else:
            if not args.allow_live:
                raise SourceRegistryValidationError(
                    "live validation requires --allow-live"
                )
            if (
                args.cockroach_binary is None
                or args.runtime_root is None
                or args.json_output is None
            ):
                raise SourceRegistryValidationError(
                    "live validation requires binary, runtime, and output paths"
                )
            paths = resolve_live_validation_paths(
                args.cockroach_binary,
                args.runtime_root,
                args.json_output,
            )
            result = run_live_validation(paths)
            write_json_output(paths.json_output, result)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (
        SourceRegistryValidationError,
        migrations.MigrationError,
        rls.RlsValidationError,
        persistence.PersistenceValidationError,
    ) as exc:
        print(f"ERROR: {migrations.sanitize_error(str(exc))}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
