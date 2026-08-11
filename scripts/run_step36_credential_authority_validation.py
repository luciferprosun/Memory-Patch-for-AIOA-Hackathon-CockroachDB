#!/usr/bin/env python3
"""Controlled Step 36 credential and database-authority validation.

The runner uses fake values and owned disposable CockroachDB processes only.
It performs no provider call, production credential rotation, AWS/S3 mutation,
external action, failure injection, or Step 37 work.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any, Mapping
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts"), str(ROOT)]

import run_cockroachdb_migrations as migrations  # noqa: E402
import run_step18_retrieval_validation as step18  # noqa: E402
import run_step27_personal_memory_validation as step27  # noqa: E402
import run_step29_personal_memory_patch_validation as step29  # noqa: E402
import run_step30_user_approval_commit_activation_validation as step30  # noqa: E402
import run_step34_human_review_validation as step34  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from aioa_memory_kernel.contracts.serialization import (  # noqa: E402
    canonical_json,
    canonical_sha256,
)
from aioa_memory_kernel.audit_ledger.models import safe_audit_payload  # noqa: E402
from aioa_memory_kernel.contracts.exceptions import ContractValidationError  # noqa: E402
from aioa_memory_kernel.personal_memory import (  # noqa: E402
    PersonalMemoryLifecycleExportRecord,
)
from aioa_memory_kernel.personal_memory_ui import (  # noqa: E402
    MemoryOwnerSessionStore,
    OidcSettings,
    create_personal_memory_app,
)
from aioa_memory_kernel.review_workspace.models import safe_review_context  # noqa: E402
from aioa_memory_kernel.security import (  # noqa: E402
    CREDENTIAL_SPECS,
    CredentialBoundaryError,
    CredentialPurpose,
    SecretValue,
    assert_secret_free,
    build_minimal_subprocess_environment,
    credential_inventory_digest,
    load_required_credential,
    redact_text,
)
from tests.test_step35_personal_memory_ui import (  # noqa: E402
    FakeBackend,
    FakeOidcClient,
)


START_SHA = "6b2948fc371bbac1b5d48403d65bf7efadd8f56d"
CAPABILITY_MATRIX_PATH = (
    ROOT / "docs/security/STEP36_CREDENTIAL_CAPABILITY_MATRIX_1A.md"
)
DEFAULT_EXTERNAL_ENV = step27.DEFAULT_EXTERNAL_ENV
EXPECTED_COCKROACH_SHA256 = step27.EXPECTED_COCKROACH_SHA256
PRINCIPAL_INVENTORY = (
    "audit-reader-exporter",
    "authenticated-owner-api",
    "canonical-ingestion-worker",
    "human-reviewer",
    "migration-admin-operator",
    "model-provider-adapter",
    "normal-kernel-runtime",
    "personal-memory-activation-service",
    "personal-memory-commit-helper",
    "review-intake-handoff-service",
    "s3-snapshot-runtime",
    "source-publication-worker",
    "typed-audit-append-interface",
    "untrusted-browser-code",
)
PRIVILEGED_BROWSER_NAMES = tuple(
    sorted(
        {
            spec.environment_variable
            for spec in CREDENTIAL_SPECS.values()
            if spec.environment_variable is not None
        }
        | {
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
            "GITHUB_TOKEN",
        }
    )
)


class ValidationFailure(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__("Step 36 controlled validation failed")
        self.code = code
        self.sanitized_code = code


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cockroach-binary", type=Path)
    parser.add_argument("--external-env", type=Path, default=DEFAULT_EXTERNAL_ENV)
    return parser.parse_args()


def _progress(stage: str) -> None:
    print(
        canonical_json({"stage": stage, "status": "RUNNING", "step": 36}),
        file=sys.stderr,
        flush=True,
    )


def _offline_boundary_checks() -> Mapping[str, Any]:
    capability_matrix_digest = hashlib.sha256(
        CAPABILITY_MATRIX_PATH.read_bytes()
    ).hexdigest()
    environment_names = tuple(
        spec.environment_variable
        for spec in CREDENTIAL_SPECS.values()
        if spec.environment_variable is not None
    )
    if (
        len(CREDENTIAL_SPECS) != len(CredentialPurpose)
        or len(environment_names) != len(set(environment_names))
        or "DATABASE_URL" in environment_names
        or any(spec.browser_visible for spec in CREDENTIAL_SPECS.values())
    ):
        raise ValidationFailure("STEP36_CREDENTIAL_INVENTORY_INVALID")
    provider_capabilities = CREDENTIAL_SPECS[
        CredentialPurpose.MODEL_PROVIDER
    ].capabilities
    if provider_capabilities != ("CALL_APPROVED_MODEL_PROVIDER",):
        raise ValidationFailure("STEP36_PROVIDER_CAPABILITY_INVALID")

    broad_only = {
        "DATABASE_URL": "postgresql://root:fake@example.invalid/defaultdb",
        "DATABASE_URL_ADMIN": "postgresql://root:fake@example.invalid/defaultdb",
        "DATABASE_URL_MIGRATOR": "postgresql://migrator:fake@example.invalid/db",
    }
    try:
        load_required_credential(
            CredentialPurpose.PERSONAL_MEMORY_COMMIT_DATABASE,
            broad_only,
        )
    except CredentialBoundaryError:
        missing_commit_failed_closed = True
    else:
        raise ValidationFailure("STEP36_ADMIN_FALLBACK_ACCEPTED")

    sentinel = "step36-controlled-fake-secret-sentinel"
    provider = SecretValue(
        sentinel,
        purpose=CredentialPurpose.MODEL_PROVIDER,
        source_name="MOONSHOT_API_KEY",
    )
    if sentinel in str(provider) or sentinel in repr(provider):
        raise ValidationFailure("STEP36_SECRET_VALUE_RENDERED")
    child = build_minimal_subprocess_environment(
        {
            "PATH": os.environ.get("PATH", "/usr/bin"),
            "DATABASE_URL_MIGRATOR": sentinel,
            "DATABASE_URL_COMMIT_HELPER": sentinel,
            "MOONSHOT_API_KEY": sentinel,
            "UNRELATED_SECRET": sentinel,
        },
        allowed_names=("MOONSHOT_API_KEY",),
    )
    if (
        child.get("MOONSHOT_API_KEY") != sentinel
        or "DATABASE_URL_MIGRATOR" in child
        or "DATABASE_URL_COMMIT_HELPER" in child
        or "UNRELATED_SECRET" in child
    ):
        raise ValidationFailure("STEP36_CHILD_ENVIRONMENT_NOT_ISOLATED")

    ui_root = ROOT / "src/aioa_memory_kernel/personal_memory_ui"
    ui_files = [
        path
        for path in ui_root.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    ]
    ui_files.append(ROOT / "package.json")
    ui_text = "\n".join(
        path.read_text(encoding="utf-8", errors="replace") for path in ui_files
    )
    browser_hits = [name for name in PRIVILEGED_BROWSER_NAMES if name in ui_text]
    if browser_hits or "process.env" in ui_text or "import.meta.env" in ui_text:
        raise ValidationFailure("STEP36_BROWSER_PRIVILEGED_SECRET_EXPOSED")

    browser_sentinels = {
        name: f"step36-browser-fake-secret-{index}"
        for index, name in enumerate(PRIVILEGED_BROWSER_NAMES, start=1)
    }
    with mock.patch.dict(os.environ, browser_sentinels, clear=False):
        oidc = FakeOidcClient()
        app = create_personal_memory_app(
            backend=FakeBackend(),
            oidc_client=oidc,
            oidc_settings=OidcSettings(
                issuer="https://identity.example",
                client_id="personal-memory-ui",
                redirect_uri="https://testserver/memory/oidc/callback",
                public_origin="https://testserver",
            ),
            session_store=MemoryOwnerSessionStore(maximum_sessions=10),
            clock=lambda: 1000.0,
        )
        client = TestClient(app, base_url="https://testserver")
        login = client.get("/memory/login", follow_redirects=False)
        state = login.headers["location"].split("state=", 1)[1].split("&", 1)[0]
        callback = client.get(
            "/memory/oidc/callback",
            params={"code": "valid-code", "state": state},
            follow_redirects=False,
        )
        dashboard = client.get("/memory")
    rendered_browser_output = "\n".join(
        (
            login.text,
            canonical_json(dict(login.headers)),
            callback.text,
            canonical_json(dict(callback.headers)),
            dashboard.text,
            canonical_json(dict(dashboard.headers)),
            ui_text,
        )
    )
    rendered_secret_hits = sum(
        value in rendered_browser_output for value in browser_sentinels.values()
    )
    if rendered_secret_hits:
        raise ValidationFailure("STEP36_BROWSER_FAKE_SECRET_RENDERED")

    unsafe_surface_values = (
        {"safe": "Authorization: Bearer " + sentinel},
        {"safe": "postgresql://user:password@example.invalid/db"},
        {"safe": "AKIAABCDEFGHIJKLMNOP"},
        {
            "safe": "-----BEGIN OPENSSH PRIVATE KEY-----\n"
            + sentinel
            + "\n-----END OPENSSH PRIVATE KEY-----"
        },
    )
    leakage = {"audit": 0, "export": 0, "logs": 0, "review": 0, "ui": 0}
    for index, value in enumerate(unsafe_surface_values):
        try:
            safe_audit_payload(value)
        except ContractValidationError:
            pass
        else:
            leakage["audit"] += 1
        try:
            PersonalMemoryLifecycleExportRecord(
                record_type="STEP36_SECRET_NEGATIVE",
                record_id=f"step36-secret-negative-{index}",
                payload=value,
            )
        except ContractValidationError:
            pass
        else:
            leakage["export"] += 1
        try:
            safe_review_context(value)
        except ContractValidationError:
            pass
        else:
            leakage["review"] += 1
    rendered_diagnostics = (
        str(provider),
        repr(provider),
        redact_text("Authorization: Bearer " + sentinel),
        redact_text(
            "-----BEGIN OPENSSH PRIVATE KEY-----\n"
            + sentinel
            + "\n-----END OPENSSH PRIVATE KEY-----"
        ),
    )
    leakage["logs"] = sum(
        sentinel in rendered for rendered in rendered_diagnostics
    )
    leakage["ui"] = int(sentinel in ui_text)
    leakage["total"] = sum(leakage.values())
    if leakage["total"] != 0:
        raise ValidationFailure("STEP36_SECRET_SURFACE_LEAKAGE")

    safe_evidence = {
        "browser_privileged_secret_hits": len(browser_hits),
        "browser_rendered_secret_hits": rendered_secret_hits,
        "browser_rendered_utf8_bytes": len(rendered_browser_output.encode("utf-8")),
        "commit_helper_can_approve": False,
        "credential_inventory_digest": credential_inventory_digest(),
        "secret_leakage_count": leakage["total"],
    }
    assert_secret_free(
        safe_evidence,
        surface="STEP36_VALIDATION_EVIDENCE",
        reject_machine_paths=True,
    )
    return {
        "browser_privileged_secret_hits": len(browser_hits),
        "browser_rendered_secret_hits": rendered_secret_hits,
        "browser_rendered_utf8_bytes": len(
            rendered_browser_output.encode("utf-8")
        ),
        "capability_matrix_digest": capability_matrix_digest,
        "child_environment_allowlist": "PASS",
        "credential_count": len(CREDENTIAL_SPECS),
        "credential_inventory_digest": credential_inventory_digest(),
        "dedicated_environment_name_count": len(environment_names),
        "external_execution_authority": any(
            "EXTERNAL" in capability
            for spec in CREDENTIAL_SPECS.values()
            for capability in spec.capabilities
        ),
        "missing_commit_helper_credential_failed_closed": missing_commit_failed_closed,
        "model_provider_can_approve": any(
            capability in {"APPROVE_PERSONAL_MEMORY", "HUMAN_APPROVAL"}
            for capability in provider_capabilities
        ),
        "model_provider_can_db_commit": any(
            "DATABASE" in capability or "COMMIT" in capability
            for capability in provider_capabilities
        ),
        "provider_secret_redaction": "PASS",
        "principal_list": PRINCIPAL_INVENTORY,
        "leakage": leakage,
        "secret_leakage_count": leakage["total"],
    }


def _role_row(root, database: str, role: str, sql: str) -> Mapping[str, object]:
    connection = step27._PgwireConnection(
        port=root.sql_port,
        database=database,
        user=role,
    )
    try:
        cursor = connection.cursor()
        cursor.execute(sql)
        row = cursor.fetchone()
        cursor.close()
    finally:
        connection.close()
    if row is None:
        raise ValidationFailure("STEP36_ROLE_PROBE_EMPTY")
    return row


def _truth(value: object) -> bool:
    return value in (True, 1, "1", "t", "true", "TRUE")


class _Step36MigrationClient(step30._Step30HttpSqlClient):
    """Execute trusted migration batches over one owned pgwire connection."""

    @staticmethod
    def _requires_pgwire(statement: str) -> bool:
        return step18._leading_sql_keyword(statement) not in {
            "EXPLAIN",
            "SELECT",
            "SHOW",
            "WITH",
        }

    def execute(
        self,
        database: str,
        sql: str,
        *,
        timeout: float = 300,
    ) -> str:
        if self._requires_pgwire(sql):
            self._execute_admin_pgwire(database, sql, timeout=timeout)
            return ""
        return super().execute(database, sql, timeout=timeout)


def _live_role_checks(args: argparse.Namespace) -> Mapping[str, Any]:
    source_binary = step27._source_binary(args)
    binary_identity = migrations.verify_binary_identity(source_binary)
    if binary_identity["binary_sha256"] != EXPECTED_COCKROACH_SHA256:
        raise ValidationFailure("STEP36_COCKROACH_BINARY_DIGEST_MISMATCH")

    runtime = None
    root = None
    migration_client = None
    database = None
    roles: list[tuple[str, tuple[str, ...]]] = []
    cleanup: Mapping[str, Any] = {}
    cleanup_errors: list[str] = []
    primary_error: BaseException | None = None
    result: Mapping[str, Any] | None = None
    failure_stage = "RUNTIME_SETUP"

    with tempfile.TemporaryDirectory(prefix="mp-step36-binary-", dir="/tmp") as temp:
        local_binary = Path(temp) / "cockroach"
        shutil.copy2(source_binary, local_binary)
        run_id = "mp_step36_" + uuid.uuid4().hex[:12]
        runtime = migrations.LocalRuntime(local_binary, run_id)
        try:
            failure_stage = "START_DISPOSABLE_COCKROACHDB"
            started = step18._start_disposable_runtime(runtime)
            root = step30._Step30HttpSqlClient(started.port, started.sql_port)
            migration_client = _Step36MigrationClient(
                started.port,
                started.sql_port,
            )
            database = run_id + "_db"
            failure_stage = "CREATE_DATABASE"
            migrations.create_database(migration_client, database)
            failure_stage = "APPLY_MIGRATIONS"
            applied = migrations.apply_migrations(
                migration_client,
                database,
                timeout=300,
            )
            failure_stage = "REPLAY_MIGRATIONS"
            replay = migrations.apply_migrations(
                migration_client,
                database,
                timeout=300,
            )
            expected = len(migrations.load_migrations())
            if (
                len(applied["applied"]) != expected
                or replay["applied"]
                or len(replay["skipped"]) != expected
            ):
                raise ValidationFailure("STEP36_MIGRATION_REPLAY_MISMATCH")
            failure_stage = "VALIDATE_SECURITY_CATALOG"
            catalog = migrations.assert_step36_security_catalog(
                migration_client,
                database,
            )

            suffix = uuid.uuid4().hex[:10]
            role_specs = (
                (
                    "app",
                    ("mp_app_runtime", "mp_request_context_setter"),
                ),
                (
                    "commit",
                    (
                        "mp_personal_memory_commit_helper",
                        "mp_request_context_setter",
                    ),
                ),
                (
                    "reviewer",
                    ("mp_human_reviewer", "mp_request_context_setter"),
                ),
                (
                    "isolated_reviewer",
                    ("mp_human_reviewer", "mp_request_context_setter"),
                ),
                (
                    "review_service",
                    ("mp_review_service", "mp_request_context_setter"),
                ),
                (
                    "publisher",
                    (
                        "mp_source_publication_worker",
                        "mp_request_context_setter",
                    ),
                ),
                (
                    "audit_reader",
                    ("mp_audit_reader", "mp_request_context_setter"),
                ),
                (
                    "mixed_commit",
                    (
                        "mp_app_runtime",
                        "mp_personal_memory_commit_helper",
                        "mp_request_context_setter",
                    ),
                ),
                (
                    "mixed_publisher",
                    (
                        "mp_app_runtime",
                        "mp_source_publication_worker",
                        "mp_request_context_setter",
                    ),
                ),
            )
            names: dict[str, str] = {}
            for label, memberships in role_specs:
                failure_stage = "CREATE_ROLE_" + label.upper()
                role = f"mp_s36_{label}_{suffix}"
                step34._create_validation_role(root, role, memberships)
                roles.append((role, memberships))
                names[label] = role

            failure_stage = "DATABASE_ROLE_PROBES"
            commit_exact = _role_row(
                root,
                database,
                names["commit"],
                "SELECT memory_patch.step30_commit_helper_authorized() AS allowed, "
                "has_table_privilege(current_user, "
                "'memory_patch.memory_patch_approvals', 'INSERT') AS can_approve, "
                "has_table_privilege(current_user, "
                "'memory_patch.memory_patch_commits', 'INSERT') AS can_commit, "
                "has_table_privilege(current_user, "
                "'memory_patch.memory_items', 'UPDATE') AS can_activate, "
                "has_table_privilege(current_user, "
                "'memory_patch.source_registry_entries', 'UPDATE') AS can_publish, "
                "has_database_privilege(current_user, current_database(), "
                "'CREATE') AS can_migrate_database, "
                "has_schema_privilege(current_user, 'memory_patch', "
                "'CREATE') AS can_migrate_schema",
            )
            commit_app = _role_row(
                root,
                database,
                names["app"],
                "SELECT memory_patch.step30_commit_helper_authorized() AS allowed",
            )
            commit_mixed = _role_row(
                root,
                database,
                names["mixed_commit"],
                "SELECT memory_patch.step30_commit_helper_authorized() AS allowed",
            )
            publisher_exact = _role_row(
                root,
                database,
                names["publisher"],
                "SELECT memory_patch.step36_source_publisher_authorized() AS allowed, "
                "has_table_privilege(current_user, "
                "'memory_patch.source_publication_events', 'INSERT') AS can_publish, "
                "has_table_privilege(current_user, "
                "'memory_patch.memory_patch_proposals', 'UPDATE') AS can_commit",
            )
            publisher_mixed = _role_row(
                root,
                database,
                names["mixed_publisher"],
                "SELECT memory_patch.step36_source_publisher_authorized() AS allowed",
            )
            app_privileges = _role_row(
                root,
                database,
                names["app"],
                "SELECT has_table_privilege(current_user, "
                "'memory_patch.source_registry_entries', 'UPDATE') AS can_update_registry, "
                "has_table_privilege(current_user, "
                "'memory_patch.source_publication_events', 'INSERT') AS can_publish, "
                "has_table_privilege(current_user, "
                "'memory_patch.memory_patch_commits', 'INSERT') AS can_commit",
            )
            audit_reader = _role_row(
                root,
                database,
                names["audit_reader"],
                "SELECT memory_patch.step36_audit_reader_authorized() AS allowed, "
                "has_table_privilege(current_user, "
                "'memory_patch.audit_events', 'SELECT') AS can_read, "
                "has_table_privilege(current_user, "
                "'memory_patch.audit_events', 'INSERT') AS can_append, "
                "has_table_privilege(current_user, "
                "'memory_patch.audit_chain_heads', 'UPDATE') AS can_advance_head, "
                "has_table_privilege(current_user, "
                "'memory_patch.memory_patch_proposals', 'UPDATE') AS can_mutate_business",
            )
            reviewer = _role_row(
                root,
                database,
                names["reviewer"],
                "SELECT has_table_privilege(current_user, "
                "'memory_patch.memory_patch_commits', 'INSERT') AS can_commit, "
                "has_table_privilege(current_user, "
                "'memory_patch.memory_items', 'UPDATE') AS can_activate, "
                "has_table_privilege(current_user, "
                "'memory_patch.memory_patch_approvals', 'INSERT') AS can_approve, "
                "has_table_privilege(current_user, "
                "'memory_patch.source_registry_entries', 'UPDATE') AS can_publish, "
                "has_table_privilege(current_user, "
                "'memory_patch.personal_memory_spaces', 'UPDATE') AS can_change_slot, "
                "has_database_privilege(current_user, current_database(), "
                "'CREATE') AS can_migrate_database, "
                "has_schema_privilege(current_user, 'memory_patch', "
                "'CREATE') AS can_migrate_schema",
            )
            role_flags = _role_row(
                root,
                database,
                names["app"],
                "SELECT "
                "(SELECT rolbypassrls FROM pg_catalog.pg_roles "
                "WHERE rolname = 'mp_app_runtime') AS app_bypassrls, "
                "(SELECT rolbypassrls FROM pg_catalog.pg_roles "
                "WHERE rolname = 'mp_personal_memory_commit_helper') "
                "AS commit_helper_bypassrls, "
                "(SELECT rolbypassrls FROM pg_catalog.pg_roles "
                "WHERE rolname = 'mp_human_reviewer') AS reviewer_bypassrls",
            )

            if (
                not _truth(commit_exact["allowed"])
                or not _truth(commit_exact["can_commit"])
                or not _truth(commit_exact["can_activate"])
                or _truth(commit_exact["can_approve"])
                or _truth(commit_exact["can_publish"])
                or _truth(commit_exact["can_migrate_database"])
                or _truth(commit_exact["can_migrate_schema"])
            ):
                raise ValidationFailure("STEP36_COMMIT_HELPER_AUTHORITY_MISSING")
            if _truth(commit_app["allowed"]) or _truth(commit_mixed["allowed"]):
                raise ValidationFailure("STEP36_COMMIT_AUTHORITY_COMPOSITION_ALLOWED")
            if (
                not _truth(publisher_exact["allowed"])
                or not _truth(publisher_exact["can_publish"])
                or _truth(publisher_exact["can_commit"])
                or _truth(publisher_mixed["allowed"])
                or _truth(app_privileges["can_update_registry"])
                or _truth(app_privileges["can_publish"])
                or _truth(app_privileges["can_commit"])
            ):
                raise ValidationFailure("STEP36_SOURCE_PUBLICATION_SPLIT_INVALID")
            if (
                not _truth(audit_reader["allowed"])
                or not _truth(audit_reader["can_read"])
                or _truth(audit_reader["can_append"])
                or _truth(audit_reader["can_advance_head"])
                or _truth(audit_reader["can_mutate_business"])
            ):
                raise ValidationFailure("STEP36_AUDIT_READER_OVERPRIVILEGED")
            if any(_truth(reviewer[name]) for name in reviewer):
                raise ValidationFailure("STEP36_REVIEWER_OVERPRIVILEGED")
            if any(_truth(role_flags[name]) for name in role_flags):
                raise ValidationFailure("STEP36_RUNTIME_BYPASSRLS_PRESENT")

            _progress("STEP30_EXACT_COMMIT_HELPER_REGRESSION")
            failure_stage = "SEED_STEP30_IDENTITY"
            step30_request, _ = step30.hat_lineage()
            root.execute(
                database,
                step29._seed_identity_sql(
                    step30_request.route.tenant_id,
                    step30_request.route.user_id,
                    step30_request.temporal_result.trusted_now,
                ),
                timeout=120,
            )
            failure_stage = "VALIDATE_STEP30_SERVICES"
            step30_service = step30._validate_service(
                root=root,
                database=database,
                app_role=names["app"],
                commit_role=names["commit"],
            )

            _progress("STEP34_EXACT_REVIEWER_REGRESSION")
            failure_stage = "SEED_STEP34_IDENTITY"
            root.execute(database, step34._seed_identity_sql(), timeout=120)
            failure_stage = "VALIDATE_STEP34_SERVICES"
            step34_service = step34._validate_services(
                root=root,
                database=database,
                app_role=names["app"],
                audit_reader_role=names["audit_reader"],
                reviewer_role=names["reviewer"],
                isolated_reviewer_role=names["isolated_reviewer"],
                service_role=names["review_service"],
            )

            result = {
                "app_bypassrls": _truth(role_flags["app_bypassrls"]),
                "app_can_publish_source": (
                    _truth(app_privileges["can_update_registry"])
                    or _truth(app_privileges["can_publish"])
                ),
                "app_can_technical_commit": _truth(app_privileges["can_commit"]),
                "audit_reader_can_append": _truth(audit_reader["can_append"]),
                "audit_reader_can_mutate_business_state": _truth(
                    audit_reader["can_mutate_business"]
                ),
                "audit_reader_can_read": _truth(audit_reader["can_read"]),
                "catalog": catalog,
                "commit_helper_authorized": _truth(commit_exact["allowed"]),
                "commit_helper_bypassrls": _truth(
                    role_flags["commit_helper_bypassrls"]
                ),
                "commit_helper_can_activate": _truth(commit_exact["can_activate"]),
                "commit_helper_can_approve": _truth(commit_exact["can_approve"]),
                "commit_helper_can_migrate": (
                    _truth(commit_exact["can_migrate_database"])
                    or _truth(commit_exact["can_migrate_schema"])
                ),
                "commit_helper_can_publish_source": _truth(
                    commit_exact["can_publish"]
                ),
                "commit_helper_composed_role_authorized": _truth(
                    commit_mixed["allowed"]
                ),
                "migration_count": len(applied["applied"]),
                "migration_replay": "PASS",
                "publisher_can_commit_personal_memory": _truth(
                    publisher_exact["can_commit"]
                ),
                "publisher_composed_role_authorized": _truth(
                    publisher_mixed["allowed"]
                ),
                "reviewer_bypassrls": _truth(role_flags["reviewer_bypassrls"]),
                "reviewer_can_activate": _truth(reviewer["can_activate"]),
                "reviewer_can_approve": _truth(reviewer["can_approve"]),
                "reviewer_can_commit": _truth(reviewer["can_commit"]),
                "reviewer_can_migrate": (
                    _truth(reviewer["can_migrate_database"])
                    or _truth(reviewer["can_migrate_schema"])
                ),
                "reviewer_can_publish_source": _truth(reviewer["can_publish"]),
                "source_publication_worker_authorized": _truth(
                    publisher_exact["allowed"]
                ),
                "step30_service": step30_service,
                "step34_service": step34_service,
            }
        except BaseException as error:
            primary_error = error
        finally:
            if root is not None:
                if database is not None and migration_client is not None:
                    try:
                        migrations.drop_database(
                            migration_client,
                            database,
                            timeout=180,
                        )
                    except BaseException:
                        cleanup_errors.append("DATABASE_CLEANUP_FAILED")
                for role, memberships in reversed(roles):
                    try:
                        step34._drop_validation_role(root, role, memberships)
                    except BaseException:
                        cleanup_errors.append("ROLE_CLEANUP_FAILED")
            if runtime is not None:
                try:
                    cleanup = step18._stop_owned_runtime(runtime)
                except BaseException:
                    cleanup_errors.append("RUNTIME_CLEANUP_FAILED")

    if primary_error is not None:
        if isinstance(primary_error, ValidationFailure):
            raise primary_error
        sqlstate = getattr(primary_error, "sqlstate", None)
        suffix = (
            "SQLSTATE_" + sqlstate
            if isinstance(sqlstate, str) and sqlstate.isalnum()
            else type(primary_error).__name__.upper()
        )
        raise ValidationFailure("STEP36_" + failure_stage + "_" + suffix) from primary_error
    if cleanup_errors:
        raise ValidationFailure("STEP36_" + "_".join(cleanup_errors))
    if result is None:
        raise ValidationFailure("STEP36_ROLE_RESULT_MISSING")
    if not all(
        cleanup.get(field) is expected
        for field, expected in (
            ("pid_exited", True),
            ("ports_closed", True),
            ("temporary_store_removed", True),
            ("force_kill_used", False),
        )
    ):
        raise ValidationFailure("STEP36_RUNTIME_CLEANUP_INCOMPLETE")
    return {
        **result,
        "cleanup": {
            "database_removed": True,
            "roles_removed": len(roles),
            **cleanup,
        },
    }


def validate(args: argparse.Namespace) -> Mapping[str, Any]:
    _progress("OFFLINE_CREDENTIAL_AND_BROWSER_BOUNDARIES")
    offline = _offline_boundary_checks()
    _progress("STEP36_COMBINED_DATABASE_AUTHORITY_VALIDATION")
    live_result = _live_role_checks(args)
    step30_result = live_result["step30_service"]
    step34_result = live_result["step34_service"]
    database = {
        key: value
        for key, value in live_result.items()
        if key not in {"step30_service", "step34_service"}
    }

    if (
        step30_result["commit"]["dedicated_role"]
        != "mp_personal_memory_commit_helper"
        or step30_result["owner_isolation"]["cross_user_commit"] != "DENIED"
        or step30_result["approval"]["actor_type"] != "HUMAN_USER"
        or step34_result["isolation"]["ordinary_user_review_access"] != "DENIED"
        or step34_result["isolation"]["cross_tenant_review"] != "DENIED"
    ):
        raise ValidationFailure("STEP36_UPSTREAM_AUTHORITY_REGRESSION")

    result: dict[str, Any] = {
        "authority": {
            "app_bypassrls": database["app_bypassrls"],
            "app_can_publish_source": database["app_can_publish_source"],
            "app_can_technical_commit": database["app_can_technical_commit"],
            "audit_reader_can_append": database["audit_reader_can_append"],
            "audit_reader_can_mutate_business_state": database[
                "audit_reader_can_mutate_business_state"
            ],
            "commit_helper_bypassrls": database["commit_helper_bypassrls"],
            "commit_helper_can_approve": database["commit_helper_can_approve"],
            "commit_helper_can_migrate": database["commit_helper_can_migrate"],
            "commit_helper_can_publish_source": database[
                "commit_helper_can_publish_source"
            ],
            "external_execution_authority": offline[
                "external_execution_authority"
            ],
            "master_credential_fallback": not offline[
                "missing_commit_helper_credential_failed_closed"
            ],
            "model_provider_can_approve": offline["model_provider_can_approve"],
            "model_provider_can_db_commit": offline[
                "model_provider_can_db_commit"
            ],
            "reviewer_bypassrls": database["reviewer_bypassrls"],
            "reviewer_can_activate": database["reviewer_can_activate"],
            "reviewer_can_approve": database["reviewer_can_approve"],
            "reviewer_can_commit": database["reviewer_can_commit"],
            "reviewer_can_migrate": database["reviewer_can_migrate"],
            "reviewer_can_publish_source": database[
                "reviewer_can_publish_source"
            ],
        },
        "browser": {
            "privileged_secret_hits": offline[
                "browser_privileged_secret_hits"
            ],
            "public_environment_variables": 0,
            "rendered_secret_hits": offline["browser_rendered_secret_hits"],
            "rendered_utf8_bytes": offline["browser_rendered_utf8_bytes"],
        },
        "commit_helper": {
            "content_identity": step30_result["content_identity"],
            "cross_tenant_commit": step30_result["owner_isolation"][
                "cross_tenant_rows"
            ],
            "cross_user_commit": step30_result["owner_isolation"][
                "cross_user_commit"
            ],
            "dedicated_role": step30_result["commit"]["dedicated_role"],
            "positive_commit": "PASS",
            "receipt_hash": step30_result["commit"]["receipt_hash"],
            "tampered_or_changed_replay": step30_result["commit"]["replay"][
                "changed_replay"
            ],
        },
        "capability_matrix_digest": offline["capability_matrix_digest"],
        "cleanup": {
            "combined_database_validation": database["cleanup"],
        },
        "credential_inventory_digest": offline["credential_inventory_digest"],
        "database_roles": database,
        "effect_bounds": {
            "aws_mutations": 0,
            "failure_injection_framework": 0,
            "production_secret_rotations": 0,
            "provider_calls": 0,
            "s3_mutations": 0,
        },
        "leakage": offline["leakage"],
        "missing_secret_fail_closed": {
            "admin_fallback": not offline[
                "missing_commit_helper_credential_failed_closed"
            ],
            "commit_helper": offline[
                "missing_commit_helper_credential_failed_closed"
            ],
        },
        "reviewer": {
            "cross_tenant": step34_result["isolation"]["cross_tenant_review"],
            "ordinary_user": step34_result["isolation"][
                "ordinary_user_review_access"
            ],
            "positive_review": "PASS",
            "source_publication_authority": database[
                "reviewer_can_publish_source"
            ],
        },
        "principal_list": offline["principal_list"],
        "provider": {
            "capabilities": CREDENTIAL_SPECS[
                CredentialPurpose.MODEL_PROVIDER
            ].capabilities,
            "commit_helper_boundary_visible": False,
            "database_commit_authority": offline[
                "model_provider_can_db_commit"
            ],
            "human_approval_authority": offline[
                "model_provider_can_approve"
            ],
            "migration_boundary_visible": False,
            "reviewer_boundary_visible": False,
        },
        "rls_context_spoof": {
            "commit_cross_tenant": step30_result["owner_isolation"][
                "cross_tenant_rows"
            ],
            "commit_cross_user": step30_result["owner_isolation"][
                "cross_user_commit"
            ],
            "review_cross_tenant": step34_result["isolation"][
                "cross_tenant_review"
            ],
            "review_ordinary_user": step34_result["isolation"][
                "ordinary_user_review_access"
            ],
        },
        "rotation_readiness": "DOCUMENTED_NO_PRODUCTION_ROTATION",
        "schema_version": "step36-credential-authority-validation-1a",
        "start_sha": START_SHA,
        "status": "PASS",
        "step": 36,
        "step37_boundary": {
            "failure_injection_framework": 0,
            "step37_started": False,
        },
    }
    if any(result["authority"].values()):
        raise ValidationFailure("STEP36_FORBIDDEN_AUTHORITY_PRESENT")
    if result["leakage"]["total"] != 0:
        raise ValidationFailure("STEP36_SECRET_LEAKAGE_PRESENT")
    assert_secret_free(
        result,
        surface="STEP36_CONTROLLED_VALIDATION",
        reject_machine_paths=True,
    )
    result["validation_digest"] = canonical_sha256(result)
    return result


def main() -> int:
    try:
        result = validate(_arguments())
    except (
        OSError,
        TypeError,
        ValueError,
        RuntimeError,
        migrations.MigrationError,
        ValidationFailure,
    ) as error:
        reason = getattr(error, "sanitized_code", type(error).__name__.upper())
        print(
            canonical_json({"reason": reason, "status": "FAILED", "step": 36}),
            file=sys.stderr,
        )
        return 1
    print(canonical_json(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
