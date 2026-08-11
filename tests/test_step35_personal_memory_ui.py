"""Focused Step 35 owner workspace behavior tests."""

from __future__ import annotations

import re
import unittest
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

from aioa_memory_kernel.contracts.serialization import canonical_sha256
from aioa_memory_kernel.persistence import SerializableTransactionRunner
from aioa_memory_kernel.personal_memory_ui import (
    AuditEventView,
    DashboardView,
    KernelPersonalMemoryUiBackend,
    MemoryOwnerSessionStore,
    ModelBindingView,
    OidcSettings,
    OwnerActionResult,
    OwnerPrincipal,
    PatchView,
    PersonalMemoryUiReadRepository,
    PersonalMemoryUiConflict,
    PersonalMemoryUiNotFound,
    QuotaView,
    SlotView,
    create_personal_memory_app,
)


NOW = datetime(2045, 1, 2, 3, 4, 5, tzinfo=UTC)


def digest(label: str) -> str:
    return canonical_sha256({"step": 35, "label": label})


OWNER_A = OwnerPrincipal("tenant-a", "owner-a", "oidc-a", "Owner A")
OWNER_B = OwnerPrincipal("tenant-a", "owner-b", "oidc-b", "Owner B")
OWNER_C = OwnerPrincipal("tenant-b", "owner-c", "oidc-c", "Owner C")


def quota() -> QuotaView:
    return QuotaView(2048, 8192, 1, 8, 1, 4, 1, 4)


def slot(owner: OwnerPrincipal, *, state: str = "ACTIVE") -> SlotView:
    return SlotView(
        personal_memory_space_id=f"space-{owner.owner_user_id}",
        display_name=f"{owner.display_name} memory",
        state=state,
        slot_hash=digest(f"slot-{owner.owner_user_id}-{state}"),
        state_version=3,
        configuration_version=2,
        updated_at=NOW,
        quota=quota(),
        model_bindings=(
            ModelBindingView(
                "binding-a",
                "provider-neutral-a",
                "model-a",
                "2026-08",
                "EXACT_MODEL",
                True,
                digest("binding-a"),
            ),
        ),
        patch_count=4,
        pending_approval_count=1,
        active_patch_count=1,
    )


def patch(
    owner: OwnerPrincipal,
    state: str,
    *,
    suffix: str,
    statement: str = "Use concise answers for this owner.",
) -> PatchView:
    active = state in {"ACTIVE", "SUPERSEDED", "REVOKED", "DELETED"}
    return PatchView(
        proposal_id=f"proposal-{owner.owner_user_id}-{suffix}",
        proposal_hash=digest(f"proposal-{owner.owner_user_id}-{suffix}"),
        personal_memory_space_id=f"space-{owner.owner_user_id}",
        statement=statement,
        statement_hash=digest(f"statement-{owner.owner_user_id}-{suffix}"),
        state=state,
        state_version=7 if active else 5,
        state_hash=digest(f"state-{owner.owner_user_id}-{suffix}"),
        scope_summary=("domain=personalization",),
        model_binding_id="binding-a",
        validation_receipt_hash=digest(f"validation-{suffix}"),
        evidence_binding_hash=digest(f"evidence-{suffix}"),
        validation_summary="Validated against bound evidence",
        limitations=("Private personalization only",),
        patch_id=f"patch-{owner.owner_user_id}-{suffix}" if active else None,
        patch_hash=digest(f"patch-{owner.owner_user_id}-{suffix}") if active else None,
        approval_receipt_hash=digest(f"approval-{suffix}") if active else None,
        activation_receipt_hash=digest(f"activation-{suffix}") if active else None,
        terminal_record_hash=(
            digest(f"terminal-{suffix}")
            if state in {"SUPERSEDED", "REVOKED", "DELETED"}
            else None
        ),
        superseded_by_patch_id="patch-owner-a-new" if state == "SUPERSEDED" else None,
        updated_at=NOW,
    )


class FakeOidcClient:
    def __init__(self) -> None:
        self.next_principal = OWNER_A
        self.last = None

    def authorization_url(self, *, state, nonce, code_challenge):
        self.last = (state, nonce, code_challenge)
        return f"https://identity.example/authorize?state={state}&challenge={code_challenge}"

    def authenticate(self, *, code, code_verifier, nonce):
        if code != "valid-code":
            raise ValueError("invalid code")
        self.last = (code_verifier, nonce)
        return self.next_principal


class FakeBackend:
    def __init__(self) -> None:
        self.calls = []
        self.slots = {principal.owner_user_id: slot(principal) for principal in (OWNER_A, OWNER_B, OWNER_C)}
        self.patches = {
            OWNER_A.owner_user_id: (
                patch(OWNER_A, "AWAITING_APPROVAL", suffix="pending"),
                patch(OWNER_A, "ACTIVE", suffix="active"),
                patch(OWNER_A, "SUPERSEDED", suffix="old"),
                patch(OWNER_A, "REVOKED", suffix="revoked"),
            ),
            OWNER_B.owner_user_id: (patch(OWNER_B, "ACTIVE", suffix="private-b"),),
            OWNER_C.owner_user_id: (),
        }

    def dashboard(self, principal):
        owner_slots = () if principal is OWNER_C else (self.slots[principal.owner_user_id],)
        patches = self.patches[principal.owner_user_id]
        return DashboardView(
            slots=owner_slots,
            pending_approvals=tuple(item for item in patches if item.state == "AWAITING_APPROVAL"),
            recent_patches=patches,
            recent_audit_events=(
                AuditEventView(
                    "event-a", "PERSONAL_MEMORY_ACTIVATED", "MEMORY_PATCH",
                    "patch-a", digest("audit-subject"), digest("audit-event"), 7,
                    NOW, ("AUDIT_EVENT_APPENDED",),
                ),
            ) if owner_slots else (),
            slot_count=len(owner_slots),
            active_slot_count=len(owner_slots),
            pending_approval_count=sum(item.state == "AWAITING_APPROVAL" for item in patches),
            active_patch_count=sum(item.state == "ACTIVE" for item in patches),
            superseded_patch_count=sum(item.state == "SUPERSEDED" for item in patches),
            revoked_patch_count=sum(item.state == "REVOKED" for item in patches),
        )

    def slot_detail(self, principal, space_id):
        expected = f"space-{principal.owner_user_id}"
        if space_id != expected:
            raise PersonalMemoryUiNotFound()
        return self.slots[principal.owner_user_id], self.patches[principal.owner_user_id]

    def _action(self, name, principal, values):
        self.calls.append((name, principal, values))
        if values.get("idempotency_key") == "stale":
            raise PersonalMemoryUiConflict()
        return OwnerActionResult(
            name, str(values.get("proposal_id") or values.get("space_id")),
            "APPROVED" if name == "APPROVE" else "ACTIVE", digest(name), False,
            f"{name} completed.",
        )

    def approve_proposal(self, principal, **values): return self._action("APPROVE", principal, values)
    def configure_slot(self, principal, **values): return self._action("CONFIGURE", principal, values)
    def transition_slot(self, principal, **values): return self._action("TRANSITION", principal, values)
    def update_model_binding(self, principal, **values): return self._action("MODEL_BINDING", principal, values)
    def revoke_patch(self, principal, **values): return self._action("REVOKE", principal, values)
    def export_slot(self, principal, **values): return self._action("EXPORT", principal, values)
    def delete_patch(self, principal, **values): return self._action("DELETE", principal, values)


class PersonalMemoryUiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.backend = FakeBackend()
        self.oidc = FakeOidcClient()
        self.store = MemoryOwnerSessionStore(maximum_sessions=20)
        settings = OidcSettings(
            issuer="https://identity.example",
            client_id="personal-memory-ui",
            redirect_uri="https://testserver/memory/oidc/callback",
            public_origin="https://testserver",
        )
        self.app = create_personal_memory_app(
            backend=self.backend,
            oidc_client=self.oidc,
            oidc_settings=settings,
            session_store=self.store,
            clock=lambda: 1000.0,
        )
        self.client = TestClient(self.app, base_url="https://testserver", raise_server_exceptions=False)

    def login(self, principal=OWNER_A):
        self.oidc.next_principal = principal
        response = self.client.get("/memory/login", follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        state = parse_qs(urlparse(response.headers["location"]).query)["state"][0]
        callback = self.client.get(
            "/memory/oidc/callback", params={"code": "valid-code", "state": state},
            follow_redirects=False,
        )
        self.assertEqual(callback.status_code, 303)

    @staticmethod
    def hidden(html: str, name: str) -> str:
        match = re.search(rf'name="{re.escape(name)}" value="([^"]*)"', html)
        if match is None:
            raise AssertionError(f"missing hidden field {name}")
        return match.group(1)

    def test_login_uses_pkce_and_secure_opaque_cookies(self):
        response = self.client.get("/memory/login", follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertIn("Secure", response.headers["set-cookie"])
        self.assertIn("HttpOnly", response.headers["set-cookie"])
        self.assertIn("SameSite=lax", response.headers["set-cookie"])
        self.assertEqual(len(self.oidc.last[2]), 43)
        self.assertNotIn("tenant-a", response.headers["set-cookie"])
        self.assertNotIn("owner-a", response.headers["set-cookie"])

    def test_unauthenticated_owner_route_redirects_to_oidc_login(self):
        response = self.client.get("/memory", follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertTrue(response.headers["location"].startswith("/memory/login"))

    def test_dashboard_renders_exact_backend_state_and_noncanonical_notice(self):
        self.login()
        response = self.client.get("/memory")
        self.assertEqual(response.status_code, 200)
        self.assertIn("AWAITING APPROVAL", response.text)
        self.assertIn("SUPERSEDED", response.text)
        self.assertIn("REVOKED", response.text)
        self.assertIn("not an official source or canonical evidence", response.text)
        self.assertIn("provider-neutral-a / model-a", response.text)
        self.assertEqual(response.headers["x-frame-options"], "DENY")
        self.assertIn("frame-ancestors 'none'", response.headers["content-security-policy"])

    def test_audit_projection_normalizes_http_sql_timestamp_for_rendering(self):
        class Transaction:
            @staticmethod
            def fetch_all(sql, parameters):
                return (
                    {
                        "event_id": "event-http-sql",
                        "event_type": "PERSONAL_MEMORY_ACTIVATED",
                        "subject_type": "PERSONAL_MEMORY_PATCH",
                        "subject_id": "patch-a",
                        "subject_hash": digest("audit-subject-http"),
                        "event_hash": digest("audit-event-http"),
                        "sequence_number": "7",
                        "occurred_at": "2045-01-02T03:04:05+00:00",
                        "reason_codes": '["AUDIT_EVENT_APPENDED"]',
                    },
                )

        events = PersonalMemoryUiReadRepository.list_audit_events(
            Transaction(),
            tenant_id="tenant-a",
            owner_user_id="owner-a",
            maximum_results=20,
        )
        self.assertEqual(events[0].occurred_at, NOW)
        self.assertEqual(events[0].reason_codes, ("AUDIT_EVENT_APPENDED",))

    def test_owner_private_not_found_is_raised_after_transaction_boundary(self):
        class BoundaryRunner(SerializableTransactionRunner):
            def __init__(self):
                super().__init__(lambda: None)
                self.ui_error_leaked = False

            def run(self, context, callback, *, operation_kind=None):
                try:
                    return callback(object())
                except PersonalMemoryUiNotFound:
                    self.ui_error_leaked = True
                    raise AssertionError("UI error leaked into persistence transaction")

        class EmptySlots:
            @staticmethod
            def get_slot(transaction, tenant_id, owner_user_id, space_id):
                return None

        class EmptyLifecycle:
            @staticmethod
            def get_state(transaction, tenant_id, owner_user_id, proposal_id):
                return None

        runner = BoundaryRunner()
        backend = KernelPersonalMemoryUiBackend(
            runner,
            personal_memory_service=object(),
            approval_service=object(),
            lifecycle_service=object(),
            slot_repository=EmptySlots(),
            lifecycle_repository=EmptyLifecycle(),
        )
        with self.assertRaises(PersonalMemoryUiNotFound):
            backend.slot_detail(OWNER_A, "space-owner-b")
        with self.assertRaises(PersonalMemoryUiNotFound):
            backend._state(OWNER_A, "proposal-owner-b")
        self.assertFalse(runner.ui_error_leaked)

    def test_zero_slot_owner_gets_truthful_empty_state(self):
        self.login(OWNER_C)
        response = self.client.get("/memory")
        self.assertIn("No Personal Memory yet", response.text)
        self.assertIn("No memory, usage, or activity has been fabricated", response.text)

    def test_direct_object_reference_cannot_cross_owner(self):
        self.login(OWNER_B)
        response = self.client.get("/memory/slots/space-owner-a")
        self.assertEqual(response.status_code, 404)
        self.assertNotIn("Owner A memory", response.text)
        self.assertNotIn("Use concise answers", response.text)

    def test_approval_is_deliberate_receipt_driven_and_owner_derived(self):
        self.login()
        page = self.client.get("/memory")
        pending = self.patches_for(OWNER_A)[0]
        data = {
            "csrf_token": self.hidden(page.text, "csrf_token"),
            "proposal_hash": pending.proposal_hash,
            "expected_state_version": str(pending.state_version),
            "expected_state_hash": pending.state_hash,
            "idempotency_key": "ui-approval-1234567890abcdef",
        }
        response = self.client.post(
            f"/memory/proposals/{pending.proposal_id}/approve",
            data=data,
            headers={"origin": "https://testserver"},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 303)
        call = self.backend.calls[-1]
        self.assertEqual(call[0], "APPROVE")
        self.assertEqual(call[1], OWNER_A)
        self.assertNotIn("tenant_id", call[2])
        self.assertNotIn("owner_user_id", call[2])

    def patches_for(self, owner):
        return self.backend.patches[owner.owner_user_id]

    def test_slot_page_separates_archive_delete_export_and_revocation(self):
        self.login()
        response = self.client.get("/memory/slots/space-owner-a")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Revocation is not deletion", response.text)
        self.assertIn("Archive retains data", response.text)
        self.assertIn("Prepare private JSON export", response.text)
        self.assertIn("Add exact model binding", response.text)
        self.assertNotIn("Publish to Source Registry", response.text)

    def test_model_binding_mutation_is_typed_owner_scoped_and_receipt_driven(self):
        self.login()
        response = self.client.get("/memory/slots/space-owner-a")
        result = self.client.post(
            "/memory/slots/space-owner-a/model-bindings",
            data={
                "csrf_token": self.hidden(response.text, "csrf_token"),
                "action": "ADD",
                "provider_id": "provider-neutral-b",
                "model_id": "model-b",
                "model_revision": "2026-08",
                "slot_hash": self.hidden(response.text, "slot_hash"),
                "expected_state_version": self.hidden(
                    response.text, "expected_state_version"
                ),
                "expected_configuration_version": self.hidden(
                    response.text, "expected_configuration_version"
                ),
                "idempotency_key": "binding-add-owner-a",
            },
            headers={"origin": "https://testserver"},
            follow_redirects=False,
        )
        self.assertEqual(result.status_code, 303)
        name, principal, sent = self.backend.calls[-1]
        self.assertEqual(name, "MODEL_BINDING")
        self.assertIs(principal, OWNER_A)
        self.assertNotIn("tenant_id", sent)
        self.assertNotIn("owner_user_id", sent)

    def test_ui_has_no_direct_commit_activation_or_generic_state_endpoint(self):
        paths = {route.path for route in self.app.routes}
        self.assertFalse(any("commit" in path for path in paths))
        self.assertFalse(any("activate" in path for path in paths))
        self.assertFalse(any("set_state" in path for path in paths))
        self.assertNotIn("/memory/reviewer", paths)

    def test_stale_backend_conflict_is_safe_and_does_not_overwrite(self):
        self.login()
        page = self.client.get("/memory/slots/space-owner-a")
        response = self.client.post(
            "/memory/slots/space-owner-a/transition",
            data={
                "csrf_token": self.hidden(page.text, "csrf_token"),
                "slot_hash": self.backend.slots[OWNER_A.owner_user_id].slot_hash,
                "expected_state_version": "3",
                "expected_configuration_version": "2",
                "target_state": "SUSPENDED",
                "idempotency_key": "stale",
            },
            headers={"origin": "https://testserver"},
        )
        self.assertEqual(response.status_code, 409)
        self.assertIn("changed", response.text)
        self.assertNotIn("Traceback", response.text)

    def test_logout_is_post_only_and_removes_session(self):
        self.login()
        page = self.client.get("/memory")
        self.assertEqual(self.client.get("/memory/logout").status_code, 405)
        result = self.client.post(
            "/memory/logout",
            data={"csrf_token": self.hidden(page.text, "csrf_token")},
            headers={"origin": "https://testserver"},
            follow_redirects=False,
        )
        self.assertEqual(result.status_code, 303)
        self.assertEqual(self.client.get("/memory", follow_redirects=False).status_code, 303)


if __name__ == "__main__":
    unittest.main()
