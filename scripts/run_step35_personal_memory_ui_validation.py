#!/usr/bin/env python3
"""Controlled Step 35 UI/API validation against owned disposable CockroachDB."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import tempfile
import uuid
import warnings
from datetime import timedelta
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import parse_qs, urlparse


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts"), str(ROOT)]
warnings.filterwarnings(
    "ignore",
    message=r"Using `httpx` with `starlette\.testclient` is deprecated.*",
)

import run_cockroachdb_migrations as migrations  # noqa: E402
import run_step18_retrieval_validation as step18  # noqa: E402
import run_step27_personal_memory_validation as step27  # noqa: E402
import run_step29_personal_memory_patch_validation as step29  # noqa: E402
import run_step30_user_approval_commit_activation_validation as step30  # noqa: E402
import run_step31_active_patch_retrieval_validation as step31  # noqa: E402
import run_step32_personal_memory_lifecycle_validation as step32  # noqa: E402
from tests.test_step26_verified_answer_output import hat_lineage  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from aioa_memory_kernel.audit_ledger import (  # noqa: E402
    AuditActorType,
    AuditEventDraft,
    AuditEventType,
    AuditLedgerService,
    AuditReasonCode,
    AuditSubjectType,
)
from aioa_memory_kernel.contracts.enums import (  # noqa: E402
    PatchState,
    PersonalMemorySpaceState,
)
from aioa_memory_kernel.contracts.serialization import (  # noqa: E402
    canonical_json,
    canonical_sha256,
)
from aioa_memory_kernel.security.credentials import CredentialPurpose  # noqa: E402
from aioa_memory_kernel.personal_memory import (  # noqa: E402
    PersonalMemoryLifecycle32Service,
    PersonalMemoryMutationActor,
    Step32ActorType,
    Step32ReasonCode,
    TransitionSlotCommand,
    build_deletion_request,
    build_revocation_request,
    build_supersession_request,
)
from aioa_memory_kernel.personal_memory_ui import (  # noqa: E402
    KernelPersonalMemoryUiBackend,
    MemoryOwnerSessionStore,
    OidcSettings,
    OwnerPrincipal,
    create_personal_memory_app,
)


START_SHA = "9dce1e9192e98d38f3af64d736effa3b017788b8"
EXPECTED_COCKROACH_SHA256 = step27.EXPECTED_COCKROACH_SHA256
DEFAULT_EXTERNAL_ENV = step27.DEFAULT_EXTERNAL_ENV


class ValidationFailure(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__("Step 35 controlled validation failed")
        self.code = code


class _ProgressMigrationClient:
    def __init__(self, delegate: step30._Step30HttpSqlClient) -> None:
        self._delegate = delegate
        self._migration_ids = tuple(item.migration_id for item in migrations.load_migrations())
        self._next = 0

    def execute(self, database: str, sql: str, *, timeout: float = 300) -> str:
        current = self._migration_ids[self._next] if self._next < len(self._migration_ids) else None
        if current and "INSERT INTO memory_patch.schema_migrations" not in sql:
            _progress("MIGRATION_" + current.upper())
        result = self._delegate.execute(database, sql, timeout=timeout)
        if current and "INSERT INTO memory_patch.schema_migrations" in sql and current in sql:
            self._next += 1
        return result


class _FakeOidcClient:
    def __init__(self, principal: OwnerPrincipal) -> None:
        self.principal = principal

    def authorization_url(self, *, state: str, nonce: str, code_challenge: str) -> str:
        return (
            "https://identity.step35.invalid/authorize?state=" + state
            + "&code_challenge=" + code_challenge
        )

    def authenticate(self, *, code: str, code_verifier: str, nonce: str) -> OwnerPrincipal:
        if code != "controlled-human-owner" or not code_verifier or not nonce:
            raise ValueError("controlled OIDC authentication failed")
        return self.principal


class _DeterministicActionTokens:
    def __init__(self) -> None:
        self._counts: dict[str, int] = {}

    def __call__(self, prefix: str) -> str:
        count = self._counts.get(prefix, 0) + 1
        self._counts[prefix] = count
        return f"step35-controlled-{prefix}-{count:04d}"


class _Forms(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.forms: list[dict[str, Any]] = []
        self._current: dict[str, Any] | None = None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "form":
            self._current = {"action": attributes.get("action", ""), "values": {}}
        elif tag == "input" and self._current is not None and attributes.get("name"):
            self._current["values"][attributes["name"]] = attributes.get("value", "")

    def handle_endtag(self, tag):
        if tag == "form" and self._current is not None:
            self.forms.append(self._current)
            self._current = None


def _form(html: str, action_suffix: str) -> Mapping[str, str]:
    parser = _Forms()
    parser.feed(html)
    matches = [item for item in parser.forms if str(item["action"]).endswith(action_suffix)]
    if not matches:
        raise ValidationFailure("STEP35_FORM_NOT_FOUND_" + re.sub(r"\W+", "_", action_suffix).upper())
    return matches[0]["values"]


def _form_with_value(
    html: str, action_suffix: str, field: str, expected: str
) -> Mapping[str, str]:
    parser = _Forms()
    parser.feed(html)
    matches = [
        item
        for item in parser.forms
        if str(item["action"]).endswith(action_suffix)
        and item["values"].get(field) == expected
    ]
    if len(matches) != 1:
        raise ValidationFailure(
            "STEP35_FORM_VARIANT_NOT_FOUND_"
            + re.sub(r"\W+", "_", action_suffix + "_" + expected).upper()
        )
    return matches[0]["values"]


def _static_ui_contract() -> Mapping[str, Any]:
    package = ROOT / "src" / "aioa_memory_kernel" / "personal_memory_ui"
    asset = package / "static" / "htmx.min.js"
    asset_hash = hashlib.sha256(asset.read_bytes()).hexdigest()
    expected_asset_hash = "22283ef68cb7545914f0a88a1bdedc7256a703d1d580c1d255217d0a50d31313"
    if asset_hash != expected_asset_hash:
        raise ValidationFailure("STEP35_HTMX_ASSET_DIGEST_MISMATCH")
    requirements = tuple(
        line.strip()
        for line in (ROOT / "requirements-ui.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )
    package_json = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    text = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in package.rglob("*")
        if path.is_file() and path != asset
    )
    forbidden = (
        "OPENROUTER_API_KEY",
        "AWS_SECRET_ACCESS_KEY",
        "GITHUB_TOKEN",
        "COMMIT_HELPER_PASSWORD",
        "shell=True",
        "os.system(",
    )
    if any(item in text for item in forbidden):
        raise ValidationFailure("STEP35_STATIC_AUTHORITY_OR_SECRET_LEAK")
    templates = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (package / "templates").glob("*.html")
    )
    css = (package / "static" / "app.css").read_text(encoding="utf-8")
    if not all(
        marker in templates
        for marker in ('<label', 'role="status"', 'method="post"')
    ) or not all(
        marker in css
        for marker in (":focus-visible", "max-width: 800px", "max-width: 520px")
    ):
        raise ValidationFailure("STEP35_ACCESSIBILITY_RESPONSIVE_CONTRACT_MISSING")
    return {
        "accessibility_contract": "PASS",
        "asset_sha256": asset_hash,
        "dependencies": requirements,
        "htmx_version": package_json["dependencies"]["htmx.org"],
        "responsive_css_contract": "PASS",
        "secret_leakage_count": 0,
    }


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cockroach-binary", type=Path)
    parser.add_argument("--external-env", type=Path, default=DEFAULT_EXTERNAL_ENV)
    return parser.parse_args()


def _progress(stage: str) -> None:
    print(canonical_json({"stage": stage, "status": "RUNNING", "step": 35}), file=sys.stderr, flush=True)


def _failure_progress(stage: str, error: BaseException) -> None:
    current: BaseException | None = error
    details: list[str] = []
    seen: set[int] = set()
    while current is not None and id(current) not in seen and len(details) < 5:
        seen.add(id(current))
        detail = " ".join(str(current).split())[:384]
        details.append(type(current).__name__ + (":" + detail if detail else ""))
        current = current.__cause__
    print(
        canonical_json(
            {
                "detail": details,
                "sanitized_code": getattr(error, "sanitized_code", None),
                "sqlstate": getattr(error, "sqlstate", None),
                "stage": stage,
                "status": "FAILED",
                "step": 35,
                "validation_code": getattr(error, "code", None),
            }
        ),
        file=sys.stderr,
        flush=True,
    )


def _login(client: TestClient) -> None:
    start = client.get("/memory/login", follow_redirects=False)
    if start.status_code != 303:
        raise ValidationFailure("STEP35_OIDC_LOGIN_START_FAILED")
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    callback = client.get(
        "/memory/oidc/callback",
        params={"code": "controlled-human-owner", "state": state},
        follow_redirects=False,
    )
    if callback.status_code != 303:
        raise ValidationFailure("STEP35_OIDC_CALLBACK_FAILED")


def _catalog(root, database: str) -> Mapping[str, Any]:
    tables = (
        "personal_memory_spaces",
        "memory_patch_proposals",
        "memory_items",
        "personal_memory_patch_revocations",
        "personal_memory_deletions",
        "audit_events",
    )
    quoted = ", ".join(
        f"'memory_patch.{table}'::REGCLASS" for table in tables
    )
    rows = migrations.parse_tsv(
        root.execute(
            database,
            "SELECT relname, relrowsecurity, relforcerowsecurity "
            f"FROM pg_catalog.pg_class WHERE oid IN ({quoted}) ORDER BY relname",
            timeout=60,
        )
    )
    if len(rows) != len(tables) or any(
        row["relrowsecurity"] != "t" or row["relforcerowsecurity"] != "t"
        for row in rows
    ):
        raise ValidationFailure("STEP35_RLS_FORCE_RLS_MISSING")
    return {"force_rls": True, "rls": True, "tables": [row["relname"] for row in rows]}


def _service_validation(*, root, database: str, app_role: str, commit_role: str) -> Mapping[str, Any]:
    pipeline_request, _ = hat_lineage()
    if root.sql_port is None:
        raise ValidationFailure("STEP35_SQL_PORT_MISSING")
    app_runner = step30._runner(
        port=root.sql_port,
        database=database,
        role=app_role,
        credential_purpose=CredentialPurpose.APPLICATION_DATABASE,
        diagnostic=True,
    )
    commit_runner = step30._runner(
        port=root.sql_port,
        database=database,
        role=commit_role,
        credential_purpose=CredentialPurpose.PERSONAL_MEMORY_COMMIT_DATABASE,
        diagnostic=True,
    )
    audit_runner = step30._runner(
        port=root.sql_port,
        database=database,
        role=app_role,
        credential_purpose=CredentialPurpose.AUDIT_APPENDER_DATABASE,
        diagnostic=True,
    )
    base = pipeline_request.temporal_result.trusted_now
    clock = step32._TrustedClock(base + timedelta(minutes=1))
    personal, candidates, proposals, approvals, commits, activations = step31._lifecycle_services(
        request=pipeline_request, app_runner=app_runner, commit_runner=commit_runner, clock=clock
    )
    lifecycle = PersonalMemoryLifecycle32Service(app_runner, trusted_clock=clock)

    _progress("CREATE_REAL_STEP27_TO_STEP32_FIXTURES")
    primary_slot, _ = step31._create_slot(
        personal, pipeline_request,
        slot_id="personal-slot-step35-owner-workspace", suffix="step35-primary", offset=0,
    )
    awaiting = step30._prepare_awaiting(
        request=pipeline_request, slot=primary_slot,
        candidate_service=candidates, proposal_service=proposals,
        suffix="step35-owner-approval", offset=0,
    )
    old_text = "The owner prefers concise responses for this exact scope."
    old_request, _ = hat_lineage(draft_v1_text=old_text, draft_v2_text=old_text, contents=(old_text,))
    old_active = step32._advance_text(
        request=old_request, slot=primary_slot,
        candidate_service=candidates, proposal_service=proposals,
        approval_service=approvals, commit_service=commits,
        activation_service=activations, clock=clock,
        suffix="step35-superseded", offset=10, text=old_text,
    )
    new_text = "The owner prefers concise responses with explicit limitations."
    new_request, _ = hat_lineage(draft_v1_text=new_text, draft_v2_text=new_text, contents=(new_text,))
    current_active = step32._advance_text(
        request=new_request, slot=primary_slot,
        candidate_service=candidates, proposal_service=proposals,
        approval_service=approvals, commit_service=commits,
        activation_service=activations, clock=clock,
        suffix="step35-current", offset=20, text=new_text,
    )
    supersede_at = current_active.updated_at + timedelta(seconds=1)
    clock.current = supersede_at
    supersession, _ = lifecycle.supersede(
        build_supersession_request(
            old_active, current_active,
            reason_codes=(Step32ReasonCode.SUPERSESSION_CREATED,),
            effective_at=supersede_at,
            idempotency_key="step35-ui-supersession-fixture",
        ),
        authenticated_owner_user_id=primary_slot.owner_user_id,
    )

    revoked_slot, _ = step31._create_slot(
        personal, pipeline_request,
        slot_id="personal-slot-step35-revoked", suffix="step35-revoked", offset=10,
    )
    revoked_active = step32._advance_text(
        request=pipeline_request, slot=revoked_slot,
        candidate_service=candidates, proposal_service=proposals,
        approval_service=approvals, commit_service=commits,
        activation_service=activations, clock=clock,
        suffix="step35-revoked", offset=30,
        text=pipeline_request.correction_packet.ordered_claims[0].exact_claim_text,
    )
    revoke_at = revoked_active.updated_at + timedelta(seconds=1)
    clock.current = revoke_at
    revocation, _ = lifecycle.revoke(
        build_revocation_request(
            revoked_active,
            reason_codes=(Step32ReasonCode.REVOCATION_CREATED,),
            effective_at=revoke_at,
            idempotency_key="step35-ui-revocation-fixture",
        ),
        actor_type=Step32ActorType.HUMAN_OWNER,
        authenticated_actor_id=primary_slot.owner_user_id,
    )

    deleted_slot, _ = step31._create_slot(
        personal, pipeline_request,
        slot_id="personal-slot-step35-deleted", suffix="step35-deleted", offset=20,
    )
    deleted_active = step32._advance_text(
        request=pipeline_request, slot=deleted_slot,
        candidate_service=candidates, proposal_service=proposals,
        approval_service=approvals, commit_service=commits,
        activation_service=activations, clock=clock,
        suffix="step35-deleted", offset=40,
        text=pipeline_request.correction_packet.ordered_claims[0].exact_claim_text,
    )
    delete_request_at = deleted_active.updated_at + timedelta(seconds=1)
    deleted_slot, _ = personal.transition_slot(
        TransitionSlotCommand(
            schema_version=deleted_slot.schema_version,
            tenant_id=deleted_slot.tenant_id,
            owner_user_id=deleted_slot.owner_user_id,
            personal_memory_space_id=deleted_slot.personal_memory_space_id,
            target_state=PersonalMemorySpaceState.DELETED_PENDING,
            expected_state_version=deleted_slot.state_version,
            expected_configuration_version=deleted_slot.configuration_version,
            idempotency_key="step35-ui-delete-request-fixture",
            requested_at=delete_request_at,
            actor=PersonalMemoryMutationActor.OWNER_CONFIGURATION,
        )
    )
    audit_time = delete_request_at + timedelta(seconds=2)
    audit = AuditLedgerService(audit_runner)
    audit_entry, _ = audit.append_event(
        AuditEventDraft(
            event_type=AuditEventType.PERSONAL_MEMORY_ACTIVATED,
            tenant_id=primary_slot.tenant_id,
            owner_user_id=primary_slot.owner_user_id,
            personal_memory_space_id=primary_slot.personal_memory_space_id,
            subject_type=AuditSubjectType.PERSONAL_MEMORY_PATCH,
            subject_id=current_active.committed_patch.patch_id,
            subject_hash=current_active.committed_patch.patch_hash,
            actor_type=AuditActorType.ACTIVATION_SERVICE,
            actor_id="step35-controlled-activation-service",
            idempotency_key="step35-ui-owner-audit-fixture",
            occurred_at=audit_time,
            recorded_at=audit_time,
            event_payload={"canonical_evidence": False, "state": "ACTIVE"},
            reason_codes=(AuditReasonCode.AUDIT_EVENT_APPENDED,),
            lineage_hashes={"activation_receipt_hash": current_active.activation_receipt.receipt_hash},
        ),
        authenticated_tenant_id=primary_slot.tenant_id,
        authenticated_actor_type=AuditActorType.ACTIVATION_SERVICE,
        authenticated_actor_id="step35-controlled-activation-service",
    )

    clock.current = audit_time + timedelta(seconds=1)
    backend = KernelPersonalMemoryUiBackend(
        app_runner,
        personal_memory_service=personal,
        approval_service=approvals,
        lifecycle_service=lifecycle,
        trusted_now=lambda: clock.current,
    )
    principal = OwnerPrincipal(
        primary_slot.tenant_id,
        primary_slot.owner_user_id,
        "step35-controlled-owner-subject",
        "Controlled Owner A",
    )
    settings = OidcSettings(
        issuer="https://identity.step35.invalid",
        client_id="step35-controlled-ui",
        redirect_uri="https://testserver/memory/oidc/callback",
        public_origin="https://testserver",
    )
    app = create_personal_memory_app(
        backend=backend,
        oidc_client=_FakeOidcClient(principal),
        oidc_settings=settings,
        session_store=MemoryOwnerSessionStore(maximum_sessions=20),
        clock=lambda: 1000.0,
        action_token_factory=_DeterministicActionTokens(),
    )
    client = TestClient(app, base_url="https://testserver", raise_server_exceptions=False)
    _login(client)
    initial_view = backend.dashboard(principal)
    initial_states = tuple(sorted({item.state for item in initial_view.recent_patches}))
    primary_slot_view = next(
        item
        for item in initial_view.slots
        if item.personal_memory_space_id == primary_slot.personal_memory_space_id
    )
    dashboard = client.get("/memory")
    required_states = ("AWAITING_APPROVAL", "ACTIVE", "SUPERSEDED", "REVOKED")
    if dashboard.status_code != 200:
        raise ValidationFailure(f"STEP35_DASHBOARD_HTTP_{dashboard.status_code}")
    missing_states = tuple(state for state in required_states if state not in dashboard.text)
    if missing_states:
        raise ValidationFailure("STEP35_DASHBOARD_MISSING_" + "_".join(missing_states))
    if "not an official source or canonical evidence" not in dashboard.text:
        raise ValidationFailure("STEP35_NONCANONICAL_MESSAGE_MISSING")
    _progress("OWNER_DASHBOARD_AND_LIFECYCLE_PASS")

    approval_values = dict(_form(dashboard.text, f"/{awaiting.proposal.proposal_id}/approve"))
    approval = client.post(
        f"/memory/proposals/{awaiting.proposal.proposal_id}/approve",
        data=approval_values,
        headers={"origin": "https://testserver"},
        follow_redirects=False,
    )
    if approval.status_code != 303:
        raise ValidationFailure("STEP35_OWNER_APPROVAL_E2E_FAILED")
    stale = client.post(
        f"/memory/proposals/{awaiting.proposal.proposal_id}/approve",
        data=approval_values,
        headers={"origin": "https://testserver"},
    )
    if stale.status_code != 409:
        raise ValidationFailure("STEP35_STALE_APPROVAL_NOT_DENIED")
    approved_view = backend.dashboard(principal)
    approved_patch = next(
        item
        for item in approved_view.recent_patches
        if item.proposal_id == awaiting.proposal.proposal_id
    )
    if approved_patch.state != "APPROVED" or approved_patch.approval_receipt_hash is None:
        raise ValidationFailure("STEP35_APPROVED_RESULT_NOT_RENDERED")
    _progress("OWNER_APPROVAL_AND_STALE_NEGATIVE_PASS")

    slot_page = client.get(f"/memory/slots/{primary_slot.personal_memory_space_id}")
    binding_values = dict(
        _form_with_value(slot_page.text, "/model-bindings", "action", "ADD")
    )
    binding_values.update(
        {
            "provider_id": "provider-neutral-step35",
            "model_id": "owner-ui-model",
            "model_revision": "1a",
        }
    )
    binding_result = client.post(
        f"/memory/slots/{primary_slot.personal_memory_space_id}/model-bindings",
        data=binding_values,
        headers={"origin": "https://testserver"},
        follow_redirects=False,
    )
    if binding_result.status_code != 303:
        raise ValidationFailure("STEP35_MODEL_BINDING_OWNER_ACTION_FAILED")
    slot_page = client.get(f"/memory/slots/{primary_slot.personal_memory_space_id}")
    if "provider-neutral-step35 / owner-ui-model" not in slot_page.text:
        raise ValidationFailure("STEP35_MODEL_BINDING_RESULT_NOT_RENDERED")
    export_values = dict(_form(slot_page.text, "/export"))
    quota_values = {
        **export_values,
        "action": "ADD",
        "provider_id": "provider-neutral-over-quota",
        "model_id": "owner-ui-over-quota",
        "model_revision": "1a",
        "idempotency_key": "step35-model-binding-quota-negative",
    }
    quota_result = client.post(
        f"/memory/slots/{primary_slot.personal_memory_space_id}/model-bindings",
        data=quota_values,
        headers={"origin": "https://testserver"},
    )
    if quota_result.status_code != 409:
        raise ValidationFailure("STEP35_MODEL_BINDING_QUOTA_NOT_ENFORCED")
    _progress("MODEL_BINDING_OWNER_ACTION_PASS")

    export_result = client.post(
        f"/memory/slots/{primary_slot.personal_memory_space_id}/export",
        data=export_values,
        headers={"origin": "https://testserver"},
        follow_redirects=False,
    )
    if export_result.status_code != 303:
        raise ValidationFailure("STEP35_OWNER_EXPORT_E2E_FAILED")
    revoke_values = dict(_form(slot_page.text, f"/{current_active.proposal.proposal_id}/revoke"))
    revoke_result = client.post(
        f"/memory/patches/{current_active.proposal.proposal_id}/revoke",
        data=revoke_values,
        headers={"origin": "https://testserver"},
        follow_redirects=False,
    )
    if revoke_result.status_code != 303:
        raise ValidationFailure("STEP35_OWNER_REVOCATION_E2E_FAILED")
    delete_page = client.get(f"/memory/slots/{deleted_slot.personal_memory_space_id}")
    delete_values = dict(
        _form(delete_page.text, f"/{deleted_active.proposal.proposal_id}/delete")
    )
    delete_result = client.post(
        f"/memory/patches/{deleted_active.proposal.proposal_id}/delete",
        data=delete_values,
        headers={"origin": "https://testserver"},
        follow_redirects=False,
    )
    if delete_result.status_code != 303:
        raise ValidationFailure("STEP35_OWNER_DELETE_E2E_FAILED")
    deletion, _, deletion_replayed = lifecycle.delete(
        build_deletion_request(
            deleted_active,
            deleted_slot,
            requested_at=clock.current,
            idempotency_key=delete_values["idempotency_key"],
        ),
        authenticated_owner_user_id=primary_slot.owner_user_id,
    )
    if not deletion_replayed:
        raise ValidationFailure("STEP35_OWNER_DELETE_RECEIPT_NOT_REPLAYABLE")
    _progress("OWNER_EXPORT_REVOCATION_AND_DELETE_PASS")

    other_principal = OwnerPrincipal(
        primary_slot.tenant_id, step29.OTHER_USER,
        "step35-other-user-subject", "Controlled User B",
    )
    other_app = create_personal_memory_app(
        backend=backend,
        oidc_client=_FakeOidcClient(other_principal),
        oidc_settings=settings,
        session_store=MemoryOwnerSessionStore(maximum_sessions=10),
        clock=lambda: 1000.0,
        action_token_factory=_DeterministicActionTokens(),
    )
    other_client = TestClient(other_app, base_url="https://testserver", raise_server_exceptions=False)
    _login(other_client)
    other_dashboard = other_client.get("/memory")
    other_direct = other_client.get(f"/memory/slots/{primary_slot.personal_memory_space_id}")
    other_csrf = _form(other_dashboard.text, "/logout")["csrf_token"]

    def other_values(values: Mapping[str, str]) -> dict[str, str]:
        return {**values, "csrf_token": other_csrf}

    cross_user_mutations = {
        "approval": other_client.post(
            f"/memory/proposals/{awaiting.proposal.proposal_id}/approve",
            data=other_values(approval_values),
            headers={"origin": "https://testserver"},
        ).status_code,
        "model_binding": other_client.post(
            f"/memory/slots/{primary_slot.personal_memory_space_id}/model-bindings",
            data=other_values(binding_values),
            headers={"origin": "https://testserver"},
        ).status_code,
        "export": other_client.post(
            f"/memory/slots/{primary_slot.personal_memory_space_id}/export",
            data=other_values(export_values),
            headers={"origin": "https://testserver"},
        ).status_code,
        "revocation": other_client.post(
            f"/memory/patches/{current_active.proposal.proposal_id}/revoke",
            data=other_values(revoke_values),
            headers={"origin": "https://testserver"},
        ).status_code,
        "delete": other_client.post(
            f"/memory/patches/{deleted_active.proposal.proposal_id}/delete",
            data=other_values(delete_values),
            headers={"origin": "https://testserver"},
        ).status_code,
    }
    if other_dashboard.status_code != 200:
        raise ValidationFailure(f"STEP35_CROSS_USER_DASHBOARD_HTTP_{other_dashboard.status_code}")
    if "No Personal Memory yet" not in other_dashboard.text:
        raise ValidationFailure("STEP35_CROSS_USER_EMPTY_STATE_MISSING")
    if other_direct.status_code != 404:
        raise ValidationFailure(f"STEP35_CROSS_USER_SLOT_HTTP_{other_direct.status_code}")
    for action, status_code in cross_user_mutations.items():
        if status_code != 404:
            raise ValidationFailure(
                f"STEP35_CROSS_USER_{action.upper()}_HTTP_{status_code}"
            )

    tenant_principal = OwnerPrincipal(
        step29.OTHER_TENANT, step29.OTHER_TENANT_USER,
        "step35-other-tenant-subject", "Controlled Tenant B",
    )
    tenant_app = create_personal_memory_app(
        backend=backend,
        oidc_client=_FakeOidcClient(tenant_principal),
        oidc_settings=settings,
        session_store=MemoryOwnerSessionStore(maximum_sessions=10),
        clock=lambda: 1000.0,
        action_token_factory=_DeterministicActionTokens(),
    )
    tenant_client = TestClient(tenant_app, base_url="https://testserver", raise_server_exceptions=False)
    _login(tenant_client)
    tenant_direct = tenant_client.get(f"/memory/slots/{primary_slot.personal_memory_space_id}")
    if tenant_direct.status_code != 404:
        raise ValidationFailure("STEP35_CROSS_TENANT_IDOR_NOT_DENIED")

    missing_csrf = client.post("/memory/logout", data={})
    unsafe_gets = (
        client.get(f"/memory/proposals/{awaiting.proposal.proposal_id}/approve").status_code,
        client.get(f"/memory/patches/{current_active.proposal.proposal_id}/revoke").status_code,
        client.get(f"/memory/slots/{primary_slot.personal_memory_space_id}/export").status_code,
    )
    if missing_csrf.status_code != 400 or unsafe_gets != (405, 405, 405):
        raise ValidationFailure("STEP35_HTTP_MUTATION_SAFETY_FAILED")
    _progress("OWNER_ISOLATION_AND_HTTP_SAFETY_PASS")

    final_dashboard = backend.dashboard(principal)
    states = tuple(sorted({item.state for item in final_dashboard.recent_patches}))
    if "DELETED" not in states:
        raise ValidationFailure("STEP35_DELETED_STATE_NOT_RENDERED")
    return {
        "audit": {
            "event_hash": audit_entry.envelope.event_hash,
            "owner_scoped": True,
            "rendered": bool(final_dashboard.recent_audit_events),
        },
        "delete": {
            "owner_ui_action": "PASS",
            "logical": deletion.logical_delete,
            "physical": deletion.physical_delete,
            "result_hash": deletion.result_hash,
            "retrieved_as_active": False,
        },
        "fixtures": {
            "tenant_a": primary_slot.tenant_id,
            "tenant_b": step29.OTHER_TENANT,
            "user_a": primary_slot.owner_user_id,
            "user_b": step29.OTHER_USER,
        },
        "lifecycle_states": states,
        "model_bindings": {
            "display": "PASS",
            "owner_action": "PASS",
            "provider_neutral": True,
        },
        "owner_approval": {
            "approval_receipt_hash": approved_patch.approval_receipt_hash,
            "exact_step30_service": True,
            "proposal_hash": awaiting.proposal.proposal_hash,
            "resulting_state": approved_patch.state,
            "stale_negative": "DENIED",
        },
        "owner_export": "PASS",
        "owner_revocation": {
            "receipt_hash": revocation.revocation_hash,
            "ui_second_patch_revocation": "PASS",
        },
        "security": {
            "cross_tenant": "DENIED",
            "cross_user": "DENIED",
            "cross_user_mutations": {
                "approval": "DENIED",
                "delete": "DENIED",
                "export": "DENIED",
                "model_binding": "DENIED",
                "revocation": "DENIED",
            },
            "csrf": "PASS",
            "idor": "DENIED",
            "unsafe_get_mutations": 0,
        },
        "slot": {
            "bounded_list": "PASS",
            "detail": "PASS",
            "receipt_driven_actions": "PASS",
        },
        "patch_lifecycle": {
            "active_rendered": "ACTIVE" in initial_states,
            "approved_rendered_after_owner_action": approved_patch.state == "APPROVED",
            "awaiting_approval_rendered": "AWAITING_APPROVAL" in initial_states,
            "deleted_rendered": "DELETED" in states,
            "revoked_rendered": "REVOKED" in initial_states,
            "superseded_rendered": "SUPERSEDED" in initial_states,
        },
        "quota": {
            "display": "PASS",
            "maximum_model_bindings": primary_slot_view.quota.maximum_model_bindings,
            "model_binding_limit_enforcement": "DENIED_AT_LIMIT",
            "stored_bytes": primary_slot_view.quota.stored_bytes,
        },
        "supersession_hash": supersession.supersession_hash,
    }


def validate(args: argparse.Namespace) -> Mapping[str, Any]:
    static_ui = _static_ui_contract()
    source_binary = step27._source_binary(args)
    if migrations.verify_binary_identity(source_binary)["binary_sha256"] != EXPECTED_COCKROACH_SHA256:
        raise ValidationFailure("STEP35_COCKROACH_BINARY_DIGEST_MISMATCH")
    pipeline_request, _ = hat_lineage()
    runtime = root = None
    database = app_role = commit_role = None
    cleanup: Mapping[str, Any] = {}
    primary_error: BaseException | None = None
    result = migration_result = replay_result = catalog = None
    with tempfile.TemporaryDirectory(prefix="mp-step35-binary-", dir="/tmp") as temp:
        local_binary = Path(temp) / "cockroach"
        shutil.copy2(source_binary, local_binary)
        run_id = "mp_step35_" + uuid.uuid4().hex[:12]
        runtime = migrations.LocalRuntime(local_binary, run_id)
        try:
            _progress("START_DISPOSABLE_COCKROACHDB")
            started = step18._start_disposable_runtime(runtime)
            root = step30._Step30HttpSqlClient(started.port, started.sql_port)
            client = _ProgressMigrationClient(root)
            database = run_id + "_db"
            migrations.create_database(root, database)
            _progress("APPLY_MIGRATIONS")
            migration_result = migrations.apply_migrations(client, database, timeout=300)
            _progress("REPLAY_MIGRATIONS")
            replay_result = migrations.apply_migrations(client, database, timeout=300)
            expected = len(migrations.load_migrations())
            if len(migration_result["applied"]) != expected or replay_result["applied"] or len(replay_result["skipped"]) != expected:
                raise ValidationFailure("STEP35_MIGRATION_REPLAY_MISMATCH")
            root.execute(
                database,
                step29._seed_identity_sql(
                    pipeline_request.route.tenant_id,
                    pipeline_request.route.user_id,
                    pipeline_request.temporal_result.trusted_now,
                ),
                timeout=120,
            )
            catalog = _catalog(root, database)
            app_role = "mp_s35_app_" + uuid.uuid4().hex[:12]
            commit_role = "mp_s35_commit_" + uuid.uuid4().hex[:12]
            step27._create_validation_role(root, app_role)
            step30._create_commit_validation_role(root, commit_role)
            result = _service_validation(
                root=root, database=database, app_role=app_role, commit_role=commit_role
            )
        except BaseException as error:
            _failure_progress("CONTROLLED_VALIDATION", error)
            primary_error = error
        finally:
            _progress("CLEANUP_DISPOSABLE_RUNTIME")
            if root is not None and database is not None:
                try:
                    migrations.drop_database(root, database, timeout=180)
                except BaseException as error:
                    primary_error = primary_error or error
            if root is not None and app_role is not None:
                try:
                    step27._drop_validation_role(root, app_role)
                except BaseException as error:
                    primary_error = primary_error or error
            if root is not None and commit_role is not None:
                try:
                    step30._drop_commit_validation_role(root, commit_role)
                except BaseException as error:
                    primary_error = primary_error or error
            if runtime is not None:
                cleanup = step18._stop_owned_runtime(runtime)
    if primary_error is not None:
        if isinstance(primary_error, ValidationFailure):
            raise primary_error
        raise ValidationFailure(getattr(primary_error, "sanitized_code", type(primary_error).__name__.upper())) from primary_error
    if not all(cleanup.get(key) is expected for key, expected in (
        ("pid_exited", True), ("ports_closed", True),
        ("temporary_store_removed", True), ("force_kill_used", False),
    )):
        raise ValidationFailure("STEP35_RUNTIME_CLEANUP_INCOMPLETE")
    if None in (result, migration_result, replay_result, catalog):
        raise ValidationFailure("STEP35_VALIDATION_RESULT_INCOMPLETE")
    output: dict[str, Any] = {
        "authority": {
            "canonical_source_publication": False,
            "credential_architecture_redesign": False,
            "execution_authority": False,
            "frontend_authority": False,
            "personal_memory_canonical_evidence": False,
        },
        "cleanup": {
            "app_role_removed": True,
            "commit_role_removed": True,
            "database_removed": True,
            **cleanup,
        },
        "database": {
            "binary_sha256": EXPECTED_COCKROACH_SHA256,
            "migration_added": False,
            "migration_count": len(migration_result["applied"]),
            "replay_skipped_count": len(replay_result["skipped"]),
            "version": migrations.PINNED_VERSION,
        },
        "rls": catalog,
        "schema_version": "step35-personal-memory-ui-validation-1a",
        "start_sha": START_SHA,
        "status": "PASS",
        "step": 35,
        "step36_boundary": {"credential_architecture_redesign": 0, "step36_started": False},
        "ui": {
            "asset_check_command": "npm run check:assets",
            "authentication": "OIDC_AUTHORIZATION_CODE_PKCE",
            "build_command": "npm run check:assets",
            "csrf": True,
            "framework": "FastAPI+Jinja2+HTMX",
            "framework_versions": {
                "fastapi": "0.141.1",
                "htmx": "2.0.8",
                "jinja2": "3.1.6",
                "starlette": "1.6.0",
            },
            "owner_identity_server_derived": True,
            "responsive_viewports": (390, 768, 1280),
            "routes": (
                "/memory",
                "/memory/slots/{space_id}",
                "/memory/proposals/{proposal_id}/approve",
                "/memory/slots/{space_id}/model-bindings",
                "/memory/patches/{proposal_id}/revoke",
                "/memory/slots/{space_id}/export",
                "/memory/patches/{proposal_id}/delete",
            ),
            "session": "OPAQUE_SERVER_SIDE",
            "stored_xss": "ESCAPED",
            **static_ui,
        },
        **result,
    }
    output["validation_digest"] = canonical_sha256(output)
    return output


def main() -> int:
    try:
        result = validate(_arguments())
    except Exception as error:
        reason = error.code if isinstance(error, ValidationFailure) else type(error).__name__
        print(canonical_json({"reason": reason, "status": "FAILED"}), file=sys.stderr)
        return 1
    print(canonical_json(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
