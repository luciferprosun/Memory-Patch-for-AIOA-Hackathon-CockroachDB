"""Focused D3 cockpit, isolation, truthfulness, and no-cost tests."""

from __future__ import annotations

import hashlib
import re
import time
import unittest
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

from fastapi.testclient import TestClient

from aioa_memory_kernel.demo_cockpit import (
    BoundedJuryRunCoordinator,
    CockpitShell,
    GuidedJuryCase,
    JuryExecutionKind,
    JuryFlowRequest,
    JuryFlowResult,
    JuryRunState,
    JuryStageProjection,
    JuryStageState,
    LegacyCompatibilityMode,
    build_default_cockpit_shell,
    build_legacy_archive_manifest,
)
from aioa_memory_kernel.demo_runtime.config import RuntimeProviderGuardSettings
from aioa_memory_kernel.demo_runtime.current_jury_flow import (
    LiveMemoryPatchJuryFlow,
    load_guided_jury_cases,
)
from aioa_memory_kernel.demo_runtime.provider_guard import (
    GuardReservation,
    GuardedProviderAdapter,
    ProviderCallPurpose,
    ProviderGuardAccountingSnapshot,
    ProviderGuardLedgerError,
)
from aioa_memory_kernel.contracts.enums import MemoryTargetScope
from aioa_memory_kernel.contracts.serialization import canonical_sha256
from aioa_memory_kernel.german_law.e2e import (
    REAL_HAT_SCOPE_ID,
    REAL_OFFICIAL_IDENTIFIER,
    REAL_PROVISION_HASHES,
    REAL_SOURCE_ID,
    REAL_VERSION_IDENTITY,
    project_bmjernano_temporal_facts,
    project_verified_bmjernano_evidence,
)
from aioa_memory_kernel.modeling import ModelReasonCode
from aioa_memory_kernel.persistence import SerializableTransactionRunner
from aioa_memory_kernel.personal_memory_ui import (
    MemoryOwnerSessionStore,
    OidcSettings,
    create_personal_memory_app,
)
from aioa_memory_kernel.personal_memory_ui.auth import SESSION_COOKIE_NAME
from aioa_memory_kernel.retrieval import (
    RetrievalCandidate,
    RetrievalMode,
    RetrievalResult,
    Step18ReasonCode,
    selector_hash,
)
from aioa_memory_kernel.sources import (
    SourceAccessClass,
    SourceAuthorityLevel,
    SourcePublicationState,
)
from tests.test_step21_temporal_resolution import metadata
from tests.test_step38_german_law_e2e import (
    DraftV1ThenExactTargetProvider,
    PROVISION_I,
    PROVISION_II,
    PROVISION_III,
)
from tests.test_step35_personal_memory_ui import (
    FakeBackend,
    FakeOidcClient,
    OWNER_A,
    OWNER_B,
    OWNER_C,
)


QUESTION = (
    "Vervollständige den Satz zur BMJErnAnO: „Diese Anordnung tritt am "
    "[Datum] in Kraft.“"
)
CASE = GuidedJuryCase(
    "primary-entry-into-force",
    "Primary: entry into force",
    QUESTION,
    "PRIMARY",
    hashlib.sha256(QUESTION.encode("utf-8")).hexdigest(),
)
STAGES = (
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


class _MemoryGuardLedger:
    """Test-only durable-shaped ledger; no network or database is touched."""

    durable = True

    def __init__(self) -> None:
        self.request_digests: set[str] = set()
        self.calls: list[ProviderCallPurpose] = []
        self.call_completions: list[tuple[object, object, bool]] = []

    def reserve_request(self, scope):
        if scope.request_digest in self.request_digests:
            raise ProviderGuardLedgerError(ModelReasonCode.MODEL_DUPLICATE_REQUEST)
        self.request_digests.add(scope.request_digest)
        return GuardReservation(scope, "request-global", "request-owner", "REQUEST")

    def reserve_call(self, scope, *, provider_request_hash, purpose, attempt_number):
        self.calls.append(purpose)
        return GuardReservation(
            scope,
            f"call-global-{attempt_number}",
            f"call-owner-{attempt_number}",
            "CALL",
        )

    def complete(
        self,
        reservation,
        *,
        result_digest,
        usage_reference,
        error_code,
        unknown_completion,
    ):
        if reservation.reservation_kind == "CALL":
            self.call_completions.append(
                (result_digest, error_code, unknown_completion)
            )

    def snapshot(self, scope, *, budget_denied_calls=0):
        completed = sum(
            result is not None for result, _error, _unknown in self.call_completions
        )
        failed = sum(
            error is not None and not unknown
            for _result, error, unknown in self.call_completions
        )
        unknown = sum(
            bool(value) for _result, _error, value in self.call_completions
        )
        return ProviderGuardAccountingSnapshot(
            "CALL-COUNT CEILING",
            len(self.request_digests),
            len(self.calls),
            completed,
            failed,
            unknown,
            len(self.calls),
            len(self.calls),
            8,
            8 - len(self.calls),
            budget_denied_calls,
        )


def _guard_limits() -> RuntimeProviderGuardSettings:
    return RuntimeProviderGuardSettings(
        budget_epoch="d3-controlled-validation-1a",
        tenant_id=OWNER_A.tenant_id,
        maximum_requests_total=8,
        maximum_requests_per_owner=4,
        maximum_requests_per_session=3,
        request_window_seconds=60,
        maximum_requests_per_window_global=8,
        maximum_requests_per_window_owner=4,
        maximum_requests_per_window_session=3,
        maximum_calls_total=8,
        maximum_calls_per_owner=4,
        maximum_calls_per_session=4,
        maximum_calls_per_request=2,
        maximum_concurrent_calls=1,
        maximum_queued_calls=2,
        queue_wait_seconds=1,
        maximum_input_bytes=24 * 1024,
        maximum_output_tokens=1024,
        timeout_seconds=45,
    )


def _controlled_retrieval(request):
    provision = request.selector.section_identifier
    projection = project_verified_bmjernano_evidence(
        {"I.": PROVISION_I, "II.": PROVISION_II, "III.": PROVISION_III}
    )
    content_by_provision = {
        "I.": projection.exact_official_evidence[0],
        "II.": projection.exact_official_evidence[1],
        "III.": projection.exact_official_evidence[2],
    }
    content = content_by_provision[provision]
    temporal = project_bmjernano_temporal_facts(PROVISION_III)
    digest = canonical_sha256("d3-controlled-retrieval-row")
    candidate = RetrievalCandidate(
        tenant_id=request.tenant_id,
        hat_scope_id=REAL_HAT_SCOPE_ID,
        source_id=REAL_SOURCE_ID,
        knowledge_version_id=REAL_VERSION_IDENTITY,
        chunk_id=f"d3-guided-{provision.casefold().replace('.', 'section')}",
        chunk_ordinal=0,
        content_sha256=REAL_PROVISION_HASHES[provision],
        content=content,
        language_tag="de",
        authority_level=SourceAuthorityLevel.OFFICIAL_PRIMARY,
        authority_basis={"official_identifier": REAL_OFFICIAL_IDENTIFIER},
        source_kind="DE_FEDERAL_OFFICIAL_CONSOLIDATED_LAW",
        source_reference=f"gii:{REAL_OFFICIAL_IDENTIFIER}:{provision}",
        publication_state=SourcePublicationState.PUBLISHED,
        access_class=SourceAccessClass.PUBLIC,
        target_scope=MemoryTargetScope.SHARED_KNOWLEDGE_HAT,
        owner_user_id=None,
        personal_memory_space_id=None,
        scope_digest=digest,
        registry_digest=digest,
        artifact_digest=digest,
        snapshot_id="d3-controlled-bmjernano-snapshot",
        structured_metadata=metadata(
            document=REAL_SOURCE_ID,
            version=REAL_VERSION_IDENTITY,
            provision=provision,
            official_identifier=REAL_OFFICIAL_IDENTIFIER,
            effective_from=temporal.effective_from_date,
            extras={
                "step38_temporal_projection_receipt_hash": temporal.receipt_hash,
                "step38_temporal_projection_method": temporal.projection_method,
            },
        ),
        effective_scope=request.effective_scope,
        retrieval_mode=RetrievalMode.STATUTE_SECTION,
    )
    return RetrievalResult(
        request_id=request.request_id,
        tenant_id=request.tenant_id,
        user_id=request.user_id,
        route_hash=request.route_hash,
        selected_hat_id=request.selected_hat_id,
        selected_hat_version=request.selected_hat_version,
        selected_manifest_digest=request.selected_manifest_digest,
        hat_scope_id=request.hat_scope_id,
        effective_scope=request.effective_scope,
        retrieval_mode=request.retrieval_mode,
        query_digest=selector_hash(request.selector),
        candidates=(candidate,),
        truncated=False,
        reason_codes=(Step18ReasonCode.STATUTE_SECTION_MATCH,),
    )


def stage_projections(answer: str) -> tuple[JuryStageProjection, ...]:
    return tuple(
        JuryStageProjection(
            f"stage-{index}",
            label,
            JuryStageState.COMPLETED if index < 10 else JuryStageState.NOT_RUN,
            answer if label in {"Draft V1", "Draft V2", "Verified Answer"} else f"{label} complete.",
            "Deterministic D3 test projection; no production authority",
            hashlib.sha256(label.encode("utf-8")).hexdigest() if index < 10 else None,
        )
        for index, label in enumerate(STAGES)
    )


class FakeExecutor:
    def __init__(self, *, blocked: bool = False) -> None:
        self.calls = []
        self.blocked = blocked

    def execute(self, request, progress):
        self.calls.append(request)
        values = stage_projections('<script>alert("xss")</script>')
        progress(values[:3])
        if self.blocked:
            return JuryFlowResult(
                JuryExecutionKind.DETERMINISTIC_TEST,
                JuryRunState.BLOCKED,
                "DEMO_BUDGET_EXHAUSTED",
                values,
            )
        answer = '<script>alert("xss")</script>'
        return JuryFlowResult(
            JuryExecutionKind.DETERMINISTIC_TEST,
            JuryRunState.COMPLETED,
            "VERIFIED_ANSWER",
            values,
            verified_answer=answer,
            verified_answer_hash=hashlib.sha256(answer.encode("utf-8")).hexdigest(),
            personal_memory_eligible=True,
        )


class D3LiveJuryFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.backend = FakeBackend()
        self.oidc = FakeOidcClient()
        self.store = MemoryOwnerSessionStore(maximum_sessions=20)
        self.executor = FakeExecutor()
        self.coordinator = BoundedJuryRunCoordinator(self.executor, (CASE,))
        default = build_default_cockpit_shell()
        shell = CockpitShell(
            default.runtime_status,
            legacy_mode=LegacyCompatibilityMode.ARCHIVAL_VIEW,
            legacy_archive=build_legacy_archive_manifest(),
        )
        self.app = create_personal_memory_app(
            backend=self.backend,
            oidc_client=self.oidc,
            oidc_settings=OidcSettings(
                issuer="https://identity.example",
                client_id="personal-memory-ui",
                redirect_uri="https://testserver/memory/oidc/callback",
                public_origin="https://testserver",
            ),
            session_store=self.store,
            clock=lambda: 1000.0,
            cockpit_shell=shell,
            jury_flow=self.coordinator,
        )
        self.client = TestClient(
            self.app,
            base_url="https://testserver",
            raise_server_exceptions=False,
        )

    def tearDown(self) -> None:
        self.coordinator.close()

    def login(self, principal=OWNER_A, *, client=None) -> TestClient:
        selected = client or self.client
        self.oidc.next_principal = principal
        response = selected.get("/memory/login", follow_redirects=False)
        state = parse_qs(urlparse(response.headers["location"]).query)["state"][0]
        callback = selected.get(
            "/memory/oidc/callback",
            params={"code": "valid-code", "state": state},
            follow_redirects=False,
        )
        self.assertEqual(callback.status_code, 303)
        return selected

    @staticmethod
    def hidden(html: str, name: str) -> str:
        match = re.search(rf'name="{re.escape(name)}" value="([^"]*)"', html)
        if match is None:
            raise AssertionError(f"missing {name}")
        return match.group(1)

    @staticmethod
    def run_id(response) -> str:
        return parse_qs(urlparse(response.headers["location"]).query)["run"][0]

    def wait_terminal(self, principal, client, run_id: str):
        handle = client.cookies.get(SESSION_COOKIE_NAME)
        self.assertIsNotNone(handle)
        digest = hashlib.sha256(handle.encode("ascii")).hexdigest()
        for _ in range(200):
            value = self.coordinator.get(
                principal,
                session_digest=digest,
                run_id=run_id,
            )
            if value.terminal:
                return value
            time.sleep(0.005)
        self.fail("jury run did not become terminal")

    def test_page_is_current_by_default_and_load_makes_zero_provider_calls(self) -> None:
        self.login()
        response = self.client.get("/memory/demo")
        self.assertEqual(response.status_code, 200)
        self.assertIn("CURRENT / EVIDENCE-BOUND", response.text)
        self.assertIn("Critical Prompt Loop — Legacy", response.text)
        self.assertIn("Primary: entry into force", response.text)
        self.assertIn('action="/memory/jury-runs"', response.text)
        self.assertIn(
            'action="/memory/jury-runs" hx-boost="false"', response.text
        )
        self.assertIn("AWAITING APPROVAL", response.text)
        self.assertIn("Approval alone does not commit or activate", response.text)
        self.assertEqual(self.executor.calls, [])

    def test_explicit_csrf_submit_runs_once_and_polling_never_calls_provider(self) -> None:
        self.login()
        page = self.client.get("/memory/demo")
        csrf = self.hidden(page.text, "csrf_token")
        token = self.hidden(page.text, "idempotency_key")
        response = self.client.post(
            "/memory/jury-runs",
            data={
                "csrf_token": csrf,
                "case_id": CASE.case_id,
                "idempotency_key": token,
                "owner_id": OWNER_B.owner_user_id,
                "tenant_id": "tenant-b",
            },
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 303)
        run_id = self.run_id(response)
        projection = self.wait_terminal(OWNER_A, self.client, run_id)
        self.assertEqual(projection.state, JuryRunState.COMPLETED)
        self.assertEqual(len(self.executor.calls), 1)
        self.assertIs(self.executor.calls[0].principal, OWNER_A)
        status = self.client.get(f"/memory/jury-runs/{run_id}")
        self.assertEqual(status.status_code, 200)
        self.assertIn("DETERMINISTIC_TEST", status.text)
        self.assertIn("Verified Answer", status.text)
        self.assertNotIn('<script>alert("xss")</script>', status.text)
        self.assertIn("&lt;script&gt;", status.text)
        self.client.get(f"/memory/jury-runs/{run_id}")
        self.client.get(f"/memory/jury-runs/{run_id}")
        self.assertEqual(len(self.executor.calls), 1)

        replay = self.client.post(
            "/memory/jury-runs",
            data={"csrf_token": csrf, "case_id": CASE.case_id, "idempotency_key": token},
            follow_redirects=False,
        )
        self.assertEqual(self.run_id(replay), run_id)
        self.assertEqual(len(self.executor.calls), 1)

    def test_missing_csrf_and_unapproved_case_fail_before_execution(self) -> None:
        self.login()
        missing = self.client.post(
            "/memory/jury-runs",
            data={"case_id": CASE.case_id, "idempotency_key": "d3-no-csrf"},
        )
        self.assertEqual(missing.status_code, 400)
        page = self.client.get("/memory/demo")
        invalid = self.client.post(
            "/memory/jury-runs",
            data={
                "csrf_token": self.hidden(page.text, "csrf_token"),
                "case_id": "attacker-custom-case",
                "idempotency_key": "d3-invalid-case",
            },
        )
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(self.executor.calls, [])

    def test_cross_owner_and_cross_session_status_access_are_denied(self) -> None:
        self.login()
        page = self.client.get("/memory/demo")
        response = self.client.post(
            "/memory/jury-runs",
            data={
                "csrf_token": self.hidden(page.text, "csrf_token"),
                "case_id": CASE.case_id,
                "idempotency_key": "d3-owner-a-run",
            },
            follow_redirects=False,
        )
        run_id = self.run_id(response)
        self.wait_terminal(OWNER_A, self.client, run_id)

        other = TestClient(
            self.app,
            base_url="https://testserver",
            raise_server_exceptions=False,
        )
        self.login(OWNER_B, client=other)
        denied = other.get(f"/memory/jury-runs/{run_id}")
        self.assertEqual(denied.status_code, 403)

        other_tenant = TestClient(
            self.app,
            base_url="https://testserver",
            raise_server_exceptions=False,
        )
        self.login(OWNER_C, client=other_tenant)
        denied_tenant = other_tenant.get(f"/memory/jury-runs/{run_id}")
        self.assertEqual(denied_tenant.status_code, 403)

        same_owner_other_session = TestClient(
            self.app,
            base_url="https://testserver",
            raise_server_exceptions=False,
        )
        self.login(OWNER_A, client=same_owner_other_session)
        denied_session = same_owner_other_session.get(f"/memory/jury-runs/{run_id}")
        self.assertEqual(denied_session.status_code, 403)

    def test_legacy_mode_and_status_reads_do_not_start_current_run(self) -> None:
        self.login()
        legacy = self.client.get(
            "/memory/demo", params={"mode": "critical_prompt_loop"}
        )
        self.assertEqual(legacy.status_code, 200)
        self.assertIn("LEGACY / ORIGIN — ARCHIVAL VIEW", legacy.text)
        self.assertNotIn('action="/memory/jury-runs"', legacy.text)
        self.assertEqual(self.executor.calls, [])


class D3BudgetFailureTests(unittest.TestCase):
    def test_budget_denial_exposes_no_verified_answer(self) -> None:
        executor = FakeExecutor(blocked=True)
        coordinator = BoundedJuryRunCoordinator(executor, (CASE,))
        try:
            projection = coordinator.submit(
                OWNER_A,
                session_digest="a" * 64,
                case_id=CASE.case_id,
                idempotency_key="d3-budget-denial",
            )
            for _ in range(200):
                projection = coordinator.get(
                    OWNER_A,
                    session_digest="a" * 64,
                    run_id=projection.run_id,
                )
                if projection.terminal:
                    break
                time.sleep(0.005)
            self.assertEqual(projection.state, JuryRunState.BLOCKED)
            self.assertEqual(projection.reason_code, "DEMO_BUDGET_EXHAUSTED")
            self.assertIsNone(projection.verified_answer)
            self.assertEqual(len(executor.calls), 1)
        finally:
            coordinator.close()


class D3CurrentRuntimePipelineTests(unittest.TestCase):
    def run_case(self, case_id: str, draft_v1: str):
        base_provider = DraftV1ThenExactTargetProvider(draft_v1)
        ledger = _MemoryGuardLedger()
        provider = GuardedProviderAdapter(
            base_provider,
            ledger=ledger,
            limits=_guard_limits(),
        )
        flow = LiveMemoryPatchJuryFlow(
            SerializableTransactionRunner(lambda: None),
            provider,
            execution_kind=JuryExecutionKind.DETERMINISTIC_TEST,
        )
        case = next(
            value for value in load_guided_jury_cases() if value.case_id == case_id
        )
        progress = []
        with patch(
            "aioa_memory_kernel.demo_runtime.current_jury_flow."
            "RetrievalService.retrieve",
            side_effect=_controlled_retrieval,
        ):
            result = flow.execute(
                JuryFlowRequest(
                    f"d3-controlled-{case_id}",
                    OWNER_A,
                    "b" * 64,
                    case,
                ),
                progress.append,
            )
        return result, base_provider, ledger, case, progress

    def test_primary_runs_step17_through_step26_behind_current_guard(self) -> None:
        result, base_provider, ledger, case, progress = self.run_case(
            "primary-entry-into-force",
            "Diese Anordnung tritt am 1. Januar 2025 in Kraft."
        )

        self.assertEqual(result.state, JuryRunState.COMPLETED)
        self.assertIs(result.execution_kind, JuryExecutionKind.DETERMINISTIC_TEST)
        self.assertEqual(result.reason_code, "VERIFIED_ANSWER")
        self.assertIsNotNone(result.verified_answer)
        self.assertEqual(len(result.stages), 14)
        self.assertEqual(tuple(item.label for item in result.stages), STAGES)
        self.assertEqual(
            ledger.calls,
            [ProviderCallPurpose.DRAFT_V1, ProviderCallPurpose.DRAFT_V2],
        )
        self.assertEqual(len(base_provider.requests), 2)
        draft_request = base_provider.requests[0][0]
        self.assertEqual(draft_request.original_query, case.question)
        self.assertNotIn("1. Januar 2024", draft_request.system_instruction)
        self.assertEqual(result.provider_summary.calls_reserved, 2)
        self.assertEqual(result.provider_summary.calls_completed, 2)
        self.assertTrue(result.personal_memory_eligible)
        self.assertIs(result.stages[10].state, JuryStageState.NOT_RUN)
        self.assertIn("did not fabricate", result.stages[10].summary)
        self.assertGreaterEqual(len(progress), 3)

    def test_correct_primary_is_truthful_and_declared_backup_remains_available(self) -> None:
        result, provider, ledger, _case, _progress = self.run_case(
            "primary-entry-into-force",
            "Diese Anordnung tritt am 1. Januar 2024 in Kraft.",
        )
        self.assertEqual(result.state, JuryRunState.COMPLETED)
        self.assertEqual(result.reason_code, "CORRECTION_NOT_REQUIRED")
        self.assertIsNone(result.verified_answer)
        self.assertFalse(result.personal_memory_eligible)
        self.assertEqual(ledger.calls, [ProviderCallPurpose.DRAFT_V1])
        self.assertEqual(len(provider.requests), 1)
        self.assertTrue(
            all(
                item.state is JuryStageState.NOT_APPLICABLE
                for item in result.stages[7:]
            )
        )
        self.assertEqual(
            tuple(value.case_id for value in load_guided_jury_cases()),
            ("primary-entry-into-force", "backup-special-case-reservation"),
        )

    def test_declared_backup_wrong_polarity_reaches_verified_answer(self) -> None:
        result, provider, ledger, _case, _progress = self.run_case(
            "backup-special-case-reservation",
            (
                "Für besondere Fälle behalte ich mir die Ernennung und Entlassung "
                "der unter I. genannten Beamtinnen und Beamten nicht vor."
            ),
        )
        self.assertEqual(result.state, JuryRunState.COMPLETED)
        self.assertEqual(result.reason_code, "VERIFIED_ANSWER")
        self.assertIn("behalte ich mir", result.verified_answer or "")
        self.assertNotIn("nicht vor", result.verified_answer or "")
        self.assertEqual(
            ledger.calls,
            [ProviderCallPurpose.DRAFT_V1, ProviderCallPurpose.DRAFT_V2],
        )
        self.assertEqual(len(provider.requests), 2)

    def test_current_executor_has_no_personal_memory_mutation_authority(self) -> None:
        forbidden = {
            "approve",
            "commit",
            "activate",
            "publish",
            "write_personal_memory",
        }
        self.assertTrue(forbidden.isdisjoint(vars(LiveMemoryPatchJuryFlow)))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
