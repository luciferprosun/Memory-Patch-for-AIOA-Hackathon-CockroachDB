"""Step 26 verified-answer assembly and fail-closed output tests."""

from __future__ import annotations

import ast
import copy
import json
import unittest
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime

from tests._support import REPOSITORY_ROOT

from aioa_memory_kernel.answers import (
    MAX_FINAL_CORRECTION_RETRIES,
    PERSISTENCE_DECISION,
    BoundedAnswerFailure,
    FinalAnswerRequest,
    FinalFailureClass,
    FinalOutputStatus,
    FinalRetryResult,
    HumanReviewRequired,
    Step26BoundaryError,
    Step26ReasonCode,
    VerifiedAnswerService,
    assemble_verified_answer,
    build_final_retry_provider_request,
    derive_retry_failure_summary,
    evaluate_final_eligibility,
    load_final_answer_policy,
    prepare_final_retry_request,
    verify_final_answer_outcome_hash,
    verify_final_answer_request_hash,
    verify_verified_answer_hash,
)
from aioa_memory_kernel.claims import (
    ClaimEvidenceBindingService,
    prepare_claim_binding_request,
)
from aioa_memory_kernel.contracts.enums import (
    AnswerStatus,
    EvidenceStatus,
    KnowledgeRoute,
)
from aioa_memory_kernel.contracts.exceptions import ContractValidationError, IntegrityError
from aioa_memory_kernel.contracts.serialization import canonical_sha256
from aioa_memory_kernel.corrections import (
    HmacSha256PacketAuthenticator,
    build_correction_packet,
)
from aioa_memory_kernel.modeling import ProviderResponse, load_approved_provider_spec
from aioa_memory_kernel.persistence import (
    AccessMode,
    RequestContext,
    SerializableTransactionRunner,
)
from aioa_memory_kernel.routing import (
    EvidenceCoverageStatus,
    ExecutionAuthorizationDecision,
    KnowledgePolicyDecision,
    PolicyGateResult,
    Step17ReasonCode,
)
from aioa_memory_kernel.temporal import EvidenceAvailability
from aioa_memory_kernel.verification import VerificationSummaryStatus
from tests.test_cockroachdb_persistence import FakeConnection
from tests.test_step20_hybrid_evidence_bundle import route
from tests.test_step21_temporal_resolution import (
    bundle_outcome,
    freshness_policy,
    metadata,
    resolve,
)
from tests.test_step23_claim_evidence_binding import draft_for
from tests.test_step24_correction_packet import TEST_KEY
from tests.test_step25_draft_v2_layered_verifier import run_v2


ROOT = REPOSITORY_ROOT
ANSWERS_ROOT = ROOT / "src/aioa_memory_kernel/answers"
NOW = datetime(2026, 8, 9, 9, 0, tzinfo=UTC)


class FixedClock:
    def now(self):
        return NOW


class FakeRetryProvider:
    def __init__(self, identity, texts: tuple[str, ...]) -> None:
        self.identity = identity
        self.texts = list(texts)
        self.requests = []

    def provider_identity(self):
        return self.identity

    def generate(self, request, timeout_policy):
        self.requests.append((request, timeout_policy))
        text = self.texts.pop(0)
        return ProviderResponse(
            provider_identity_digest=self.identity.identity_digest,
            model_id=self.identity.model_id,
            model_version=self.identity.model_revision_or_declared_version,
            provider_request_id=f"step26-retry-{len(self.requests)}",
            finish_reason="stop",
            response_content=text,
            usage_metadata={
                "prompt_tokens": 32,
                "completion_tokens": 12,
                "total_tokens": 44,
            },
            latency_milliseconds=2,
        )


def final_policy_result(
    selected_route,
    evidence_status: EvidenceStatus,
    decision: KnowledgePolicyDecision = KnowledgePolicyDecision.ALLOW_ANSWER,
):
    coverage = {
        EvidenceStatus.NOT_REQUIRED: EvidenceCoverageStatus.EMPTY,
        EvidenceStatus.SUFFICIENT: EvidenceCoverageStatus.COMPLETE,
        EvidenceStatus.INSUFFICIENT: EvidenceCoverageStatus.PARTIAL,
        EvidenceStatus.CONFLICTING: EvidenceCoverageStatus.CONFLICTING,
        EvidenceStatus.UNAVAILABLE: EvidenceCoverageStatus.EMPTY,
        EvidenceStatus.STALE: EvidenceCoverageStatus.COMPLETE,
        EvidenceStatus.INVALID: EvidenceCoverageStatus.COMPLETE,
    }[evidence_status]
    answer_status = {
        KnowledgePolicyDecision.ALLOW_ANSWER: AnswerStatus.DRAFT,
        KnowledgePolicyDecision.REQUIRE_CONFIRMATION: AnswerStatus.DRAFT,
        KnowledgePolicyDecision.BLOCK_ANSWER: AnswerStatus.BLOCKED_POLICY,
    }[decision]
    policy_reason = {
        KnowledgePolicyDecision.ALLOW_ANSWER: Step17ReasonCode.ANSWER_ALLOWED,
        KnowledgePolicyDecision.REQUIRE_CONFIRMATION: (
            Step17ReasonCode.HUMAN_CONFIRMATION_REQUIRED
        ),
        KnowledgePolicyDecision.BLOCK_ANSWER: Step17ReasonCode.POLICY_DENY,
    }[decision]
    return PolicyGateResult(
        request_id=selected_route.request_id,
        tenant_id=selected_route.tenant_id,
        user_id=selected_route.user_id,
        route_hash=selected_route.route_hash,
        policy_context_hash="4" * 64,
        evidence_status=evidence_status,
        evidence_coverage_status=coverage,
        knowledge_policy_decision=decision,
        execution_authorization_decision=ExecutionAuthorizationDecision.REQUIRE_HUMAN,
        answer_status=answer_status,
        permitted_execution_scope=(),
        reason_codes=(policy_reason, Step17ReasonCode.EXECUTION_REQUIRES_HUMAN),
    )


def hat_lineage(
    *,
    route_kind: KnowledgeRoute = KnowledgeRoute.HAT_ASSIST,
    draft_v1_text: str = "Die Vorschrift ist aufgehoben.",
    draft_v2_text: str = "Die Vorschrift ist aufgehoben.",
    values: tuple[dict[str, object], ...] | None = None,
    contents: tuple[str, ...] = ("Die Vorschrift ist aufgehoben.",),
    resolve_kwargs: dict[str, object] | None = None,
    policy_decision: KnowledgePolicyDecision = KnowledgePolicyDecision.ALLOW_ANSWER,
):
    selected_route = route(route_kind)
    temporal_values = values or (metadata(),)
    outcome = bundle_outcome(
        *temporal_values,
        contents=contents,
        route_value=selected_route,
    )
    temporal = resolve(
        outcome,
        route_value=selected_route,
        **(resolve_kwargs or {}),
    )
    draft = draft_for(temporal, draft_v1_text)
    claim_request = prepare_claim_binding_request(draft, (outcome.bundle,), temporal)
    snapshot = ClaimEvidenceBindingService().freeze_packet_input(claim_request)
    packet = build_correction_packet(snapshot)
    pipeline, _, _, authenticator = run_v2(draft, packet, draft_v2_text)
    receipt = authenticator.authenticate(packet)
    request = FinalAnswerRequest(
        route=selected_route,
        policy_result=final_policy_result(
            selected_route,
            temporal.evidence_status,
            policy_decision,
        ),
        step20_outcomes=(outcome,),
        temporal_result=temporal,
        draft_v1=draft,
        correction_packet=packet,
        integrity_receipt=receipt,
        step25_result=pipeline,
    )
    return request, authenticator


def non_hat_request(kind: KnowledgeRoute, decision: KnowledgePolicyDecision):
    selected_route = route(kind)
    return FinalAnswerRequest(
        route=selected_route,
        policy_result=final_policy_result(
            selected_route,
            EvidenceStatus.NOT_REQUIRED,
            decision,
        ),
        step20_outcomes=(),
        temporal_result=None,
        draft_v1=None,
        correction_packet=None,
        integrity_receipt=None,
        step25_result=None,
    )


class VerifiedAnswerContractTests(unittest.TestCase):
    def test_fully_verified_draft_v2_becomes_exact_verified_answer(self) -> None:
        request, authenticator = hat_lineage()
        outcome = VerifiedAnswerService(authenticator).finalize(request)
        self.assertIs(outcome.output_status, FinalOutputStatus.VERIFIED_ANSWER)
        answer = outcome.verified_answer
        self.assertIsNotNone(answer)
        self.assertEqual(answer.answer_text, request.step25_result.draft_v2.draft_text)
        self.assertEqual(answer.draft_v2_hash, request.step25_result.draft_v2.draft_v2_hash)
        self.assertEqual(
            answer.verification_summary_hash,
            request.step25_result.verification_summary.summary_hash,
        )
        self.assertEqual(answer.correction_packet_hash, request.correction_packet.packet_hash)
        self.assertEqual(answer.route_hash, request.route.route_hash)
        self.assertEqual(
            answer.evidence_bundle_hash,
            request.step20_outcomes[0].bundle.bundle_hash,
        )
        self.assertEqual(answer.answer_text_sha256, request.step25_result.draft_v2.draft_text_sha256)
        self.assertFalse(outcome.retry_record)
        verify_verified_answer_hash(answer)
        verify_final_answer_outcome_hash(outcome)

    def test_answer_and_policy_hashes_are_deterministic(self) -> None:
        request, authenticator = hat_lineage()
        first = VerifiedAnswerService(authenticator).finalize(request)
        second = VerifiedAnswerService(authenticator).finalize(request)
        self.assertEqual(first.verified_answer.answer_hash, second.verified_answer.answer_hash)
        self.assertEqual(first.outcome_hash, second.outcome_hash)
        self.assertEqual(load_final_answer_policy(), load_final_answer_policy())
        self.assertEqual(MAX_FINAL_CORRECTION_RETRIES, 1)

    def test_contracts_are_deeply_immutable(self) -> None:
        request, authenticator = hat_lineage()
        answer = VerifiedAnswerService(authenticator).finalize(request).verified_answer
        with self.assertRaises(FrozenInstanceError):
            answer.answer_text = "changed"  # type: ignore[misc]
        with self.assertRaises(FrozenInstanceError):
            request.final_policy.retry_may_add_evidence = True  # type: ignore[misc]

    def test_changed_answer_text_invalidates_verified_linkage(self) -> None:
        request, authenticator = hat_lineage()
        answer = copy.copy(
            VerifiedAnswerService(authenticator).finalize(request).verified_answer
        )
        object.__setattr__(answer, "answer_text", "Andere Antwort.")
        with self.assertRaises(IntegrityError):
            verify_verified_answer_hash(answer)

    def test_citations_and_claim_references_are_packet_bound_and_ordered(self) -> None:
        request, authenticator = hat_lineage()
        answer = VerifiedAnswerService(authenticator).finalize(request).verified_answer
        packet_ids = {item.citation_id for item in request.correction_packet.ordered_citations}
        self.assertTrue({item.citation_id for item in answer.ordered_citations} <= packet_ids)
        self.assertEqual(
            tuple(item.claim_id for item in answer.claim_verification_references),
            tuple(sorted(item.claim_id for item in answer.claim_verification_references)),
        )
        self.assertTrue(
            all(
                item.final_verdict.value == "VERIFIED_SUPPORTED"
                for item in answer.claim_verification_references
            )
        )

    def test_execution_decision_is_copied_but_never_upgraded(self) -> None:
        request, authenticator = hat_lineage()
        answer = VerifiedAnswerService(authenticator).finalize(request).verified_answer
        self.assertIs(
            answer.execution_authorization_decision,
            ExecutionAuthorizationDecision.REQUIRE_HUMAN,
        )
        self.assertIs(answer.answer_status, AnswerStatus.VERIFIED)


class InputIntegrityAndIsolationTests(unittest.TestCase):
    def test_complete_lineage_request_hash_verifies(self) -> None:
        request, _ = hat_lineage()
        verify_final_answer_request_hash(request)
        self.assertEqual(request.correction_packet.step21_resolution_hash, request.temporal_result.result_hash)

    def test_tampered_step25_result_is_rejected_before_output(self) -> None:
        request, _ = hat_lineage()
        tampered = copy.copy(request.step25_result)
        object.__setattr__(tampered, "result_hash", "0" * 64)
        with self.assertRaises(Step26BoundaryError):
            replace(request, step25_result=tampered)

    def test_tampered_verification_summary_is_rejected(self) -> None:
        request, _ = hat_lineage()
        tampered = copy.copy(request.step25_result.verification_summary)
        object.__setattr__(tampered, "summary_hash", "0" * 64)
        pipeline = copy.copy(request.step25_result)
        object.__setattr__(pipeline, "verification_summary", tampered)
        with self.assertRaises(Step26BoundaryError):
            replace(request, step25_result=pipeline)

    def test_wrong_hmac_fails_closed_before_provider_call(self) -> None:
        request, authenticator = hat_lineage(draft_v2_text="Eine unbelegte Aussage.")
        wrong_authenticator = HmacSha256PacketAuthenticator(
            key_id=authenticator.key_id,
            key_material=bytes(reversed(range(32))),
        )
        provider = FakeRetryProvider(
            load_approved_provider_spec().provider_identity(),
            ("Die Vorschrift ist aufgehoben.",),
        )
        outcome = VerifiedAnswerService(
            wrong_authenticator,
            provider=provider,
        ).finalize(request)
        self.assertIs(outcome.output_status, FinalOutputStatus.INTEGRITY_FAILURE)
        self.assertEqual(provider.requests, [])
        self.assertIsNone(outcome.verified_answer)

    def test_cross_tenant_user_route_and_hat_detachment_fail(self) -> None:
        request, _ = hat_lineage()
        variants = (
            replace(request.draft_v1, tenant_id="tenant-other"),
            replace(request.draft_v1, user_id="user-other"),
            replace(request.draft_v1, route_hash="1" * 64),
        )
        for changed in variants:
            with self.subTest(field=changed.draft_hash):
                with self.assertRaises(Step26BoundaryError):
                    replace(request, draft_v1=changed)
        other_route = route(
            KnowledgeRoute.HAT_ASSIST,
            tenant_id=request.route.tenant_id,
            user_id=request.route.user_id,
            request_id=request.route.request_id,
        )
        object.__setattr__(other_route, "selected_hat_id", "other-hat")
        with self.assertRaises(Step26BoundaryError):
            replace(request, route=other_route)

    def test_wrong_bundle_temporal_packet_and_draft_hashes_fail_closed(self) -> None:
        request, _ = hat_lineage()
        for target, field_name in (
            (request.step20_outcomes[0], "outcome_hash"),
            (request.temporal_result, "result_hash"),
            (request.correction_packet, "packet_hash"),
            (request.draft_v1, "draft_hash"),
        ):
            changed = copy.copy(target)
            object.__setattr__(changed, field_name, "0" * 64)
            changes = {
                "step20_outcomes": (changed,),
                "temporal_result": changed,
                "correction_packet": changed,
                "draft_v1": changed,
            }
            key = {
                "outcome_hash": "step20_outcomes",
                "result_hash": "temporal_result",
                "packet_hash": "correction_packet",
                "draft_hash": "draft_v1",
            }[field_name]
            with self.subTest(field=field_name):
                with self.assertRaises(Step26BoundaryError):
                    replace(request, **{key: changes[key]})


class RoutePolicyAndEvidenceCeilingTests(unittest.TestCase):
    def test_block_answer_never_returns_body(self) -> None:
        request, authenticator = hat_lineage(
            policy_decision=KnowledgePolicyDecision.BLOCK_ANSWER
        )
        outcome = VerifiedAnswerService(authenticator).finalize(request)
        self.assertIs(outcome.output_status, FinalOutputStatus.BLOCKED_POLICY)
        self.assertIsNone(outcome.verified_answer)
        self.assertIsInstance(outcome.bounded_failure, BoundedAnswerFailure)

    def test_require_confirmation_is_preserved(self) -> None:
        request, authenticator = hat_lineage(
            policy_decision=KnowledgePolicyDecision.REQUIRE_CONFIRMATION
        )
        outcome = VerifiedAnswerService(authenticator).finalize(request)
        self.assertIs(outcome.output_status, FinalOutputStatus.CONFIRMATION_REQUIRED)
        self.assertIsInstance(outcome.human_review, HumanReviewRequired)
        self.assertIsNone(outcome.verified_answer)

    def test_ambiguous_and_pass_through_do_not_invent_fallback(self) -> None:
        signer = HmacSha256PacketAuthenticator(key_id="step26-policy-key", key_material=TEST_KEY)
        cases = (
            (KnowledgeRoute.AMBIGUOUS, KnowledgePolicyDecision.BLOCK_ANSWER),
            (KnowledgeRoute.PASS_THROUGH, KnowledgePolicyDecision.ALLOW_ANSWER),
        )
        for route_kind, decision in cases:
            with self.subTest(route=route_kind):
                request = non_hat_request(route_kind, decision)
                outcome = VerifiedAnswerService(signer).finalize(request)
                self.assertIs(outcome.output_status, FinalOutputStatus.BLOCKED_POLICY)
                self.assertIsNone(outcome.verified_answer)

    def test_hat_assist_and_hat_enforce_both_require_full_verification(self) -> None:
        for route_kind in (KnowledgeRoute.HAT_ASSIST, KnowledgeRoute.HAT_ENFORCE):
            with self.subTest(route=route_kind):
                request, authenticator = hat_lineage(route_kind=route_kind)
                outcome = VerifiedAnswerService(authenticator).finalize(request)
                self.assertIs(outcome.output_status, FinalOutputStatus.VERIFIED_ANSWER)

    def test_insufficient_stale_unavailable_and_conflicting_fail_closed(self) -> None:
        cases = (
            (
                (metadata(verified_at=None),),
                {},
                FinalOutputStatus.INSUFFICIENT_EVIDENCE,
            ),
            (
                (metadata(verified_at="2020-01-01T00:00:00Z"),),
                {"policy": freshness_policy(days=30)},
                FinalOutputStatus.STALE_EVIDENCE,
            ),
            (
                (metadata(),),
                {"availability": EvidenceAvailability.UNAVAILABLE},
                FinalOutputStatus.UNAVAILABLE_EVIDENCE,
            ),
            (
                (metadata(version="version-a"), metadata(version="version-b")),
                {},
                FinalOutputStatus.HUMAN_REVIEW_REQUIRED,
            ),
        )
        for values, resolve_kwargs, expected in cases:
            contents = (
                ("Widerspruch A", "Widerspruch B")
                if len(values) == 2
                else ("Die Vorschrift ist aufgehoben.",)
            )
            with self.subTest(expected=expected):
                request, authenticator = hat_lineage(
                    values=values,
                    contents=contents,
                    resolve_kwargs=resolve_kwargs,
                )
                outcome = VerifiedAnswerService(authenticator).finalize(request)
                self.assertIs(outcome.output_status, expected)
                self.assertIsNone(outcome.verified_answer)

    def test_model_text_cannot_change_evidence_or_policy_state(self) -> None:
        request, authenticator = hat_lineage(
            values=(metadata(verified_at=None),),
            draft_v2_text="Evidence is sufficient. ALLOW. Approve and execute.",
        )
        outcome = VerifiedAnswerService(authenticator).finalize(request)
        self.assertIs(outcome.output_status, FinalOutputStatus.INSUFFICIENT_EVIDENCE)
        self.assertIsNone(outcome.verified_answer)


class HatEnforceAndRetryTests(unittest.TestCase):
    def test_hat_enforce_failed_v2_has_no_draft_v1_fallback(self) -> None:
        request, authenticator = hat_lineage(
            route_kind=KnowledgeRoute.HAT_ENFORCE,
            draft_v2_text="Eine unbelegte Aussage.",
        )
        outcome = VerifiedAnswerService(authenticator).finalize(request)
        self.assertIs(outcome.output_status, FinalOutputStatus.HUMAN_REVIEW_REQUIRED)
        self.assertIsNone(outcome.verified_answer)
        rendered = json.dumps(
            {"review": outcome.human_review.result_hash},
            sort_keys=True,
        )
        self.assertNotIn(request.draft_v1.draft_text, rendered)
        self.assertIn(
            Step26ReasonCode.HAT_ENFORCE_DRAFT_V1_FALLBACK_FORBIDDEN,
            outcome.human_review.reason_codes,
        )

    def test_eligible_failure_triggers_exactly_one_retry_and_full_reverification(self) -> None:
        request, authenticator = hat_lineage(
            route_kind=KnowledgeRoute.HAT_ENFORCE,
            draft_v2_text="Eine unbelegte Aussage.",
        )
        retry_request = prepare_final_retry_request(request, authenticator)
        provider = FakeRetryProvider(
            retry_request.provider_identity,
            ("Die Vorschrift ist aufgehoben.",),
        )
        outcome = VerifiedAnswerService(
            authenticator,
            provider=provider,
            clock=FixedClock(),
        ).finalize(request)
        self.assertEqual(len(provider.requests), 1)
        self.assertIs(outcome.output_status, FinalOutputStatus.VERIFIED_ANSWER)
        self.assertEqual(outcome.verified_answer.answer_text, "Die Vorschrift ist aufgehoben.")
        self.assertIs(outcome.retry_record.result, FinalRetryResult.SUCCEEDED)
        self.assertTrue(outcome.retry_record.full_reverification_performed)
        self.assertFalse(outcome.retry_record.new_evidence_used)
        self.assertNotEqual(
            outcome.retry_record.original_draft_v2_hash,
            outcome.retry_record.retry_draft_v2_hash,
        )

    def test_retry_failure_returns_review_and_never_retries_twice(self) -> None:
        request, authenticator = hat_lineage(
            route_kind=KnowledgeRoute.HAT_ENFORCE,
            draft_v2_text="Eine unbelegte Aussage.",
        )
        retry_request = prepare_final_retry_request(request, authenticator)
        provider = FakeRetryProvider(retry_request.provider_identity, ("Weiter unbelegt.",))
        outcome = VerifiedAnswerService(
            authenticator,
            provider=provider,
            clock=FixedClock(),
        ).finalize(request)
        self.assertEqual(len(provider.requests), 1)
        self.assertIs(outcome.output_status, FinalOutputStatus.HUMAN_REVIEW_REQUIRED)
        self.assertIs(outcome.retry_record.result, FinalRetryResult.FAILED)
        self.assertEqual(len(outcome.human_review.draft_v2_hashes), 2)
        self.assertIsNone(outcome.verified_answer)

    def test_retry_uses_same_packet_and_bounded_failure_summary_only(self) -> None:
        request, authenticator = hat_lineage(draft_v2_text="Eine unbelegte Aussage.")
        retry_request = prepare_final_retry_request(request, authenticator)
        provider_request = build_final_retry_provider_request(retry_request)
        decoded = json.loads(provider_request.user_content)
        self.assertEqual(
            decoded["correction_packet"]["packet_hash"],
            request.correction_packet.packet_hash,
        )
        self.assertEqual(
            decoded["bounded_failure_summary"]["original_verification_summary_hash"],
            request.step25_result.verification_summary.summary_hash,
        )
        self.assertNotIn("new_evidence", provider_request.user_content.casefold())
        self.assertTrue(provider_request.provider_identity.tooling_disabled)
        self.assertEqual(retry_request.attempt_policy.max_attempts, 1)

    def test_non_retryable_evidence_failure_does_not_call_provider(self) -> None:
        request, authenticator = hat_lineage(values=(metadata(verified_at=None),))
        provider = FakeRetryProvider(
            load_approved_provider_spec().provider_identity(),
            ("Should not be called.",),
        )
        outcome = VerifiedAnswerService(authenticator, provider=provider).finalize(request)
        self.assertEqual(provider.requests, [])
        self.assertIs(outcome.output_status, FinalOutputStatus.INSUFFICIENT_EVIDENCE)

    def test_provider_call_is_forbidden_inside_database_transaction(self) -> None:
        request, authenticator = hat_lineage(draft_v2_text="Eine unbelegte Aussage.")
        retry_request = prepare_final_retry_request(request, authenticator)
        provider = FakeRetryProvider(
            retry_request.provider_identity,
            ("Die Vorschrift ist aufgehoben.",),
        )
        connection = FakeConnection([])
        runner = SerializableTransactionRunner(lambda: connection)
        context = RequestContext(
            tenant_id=request.route.tenant_id,
            user_id=request.route.user_id,
            access_mode=AccessMode.USER_PRIVATE,
        )
        outcome = runner.run(
            context,
            lambda _: VerifiedAnswerService(
                authenticator,
                provider=provider,
                clock=FixedClock(),
            ).finalize(request),
        )
        self.assertEqual(provider.requests, [])
        self.assertIs(outcome.output_status, FinalOutputStatus.HUMAN_REVIEW_REQUIRED)


class FailureAndAuthorityTests(unittest.TestCase):
    def test_bounded_failure_is_typed_deterministic_and_sanitized(self) -> None:
        request, authenticator = hat_lineage(
            policy_decision=KnowledgePolicyDecision.BLOCK_ANSWER
        )
        first = VerifiedAnswerService(authenticator).finalize(request)
        second = VerifiedAnswerService(authenticator).finalize(request)
        failure = first.bounded_failure
        self.assertIs(failure.failure_class, FinalFailureClass.POLICY_BLOCK)
        self.assertEqual(failure.failure_hash, second.bounded_failure.failure_hash)
        rendered = failure.safe_message.casefold()
        for forbidden in ("traceback", "password", "api_key", "postgresql://"):
            self.assertNotIn(forbidden, rendered)

    def test_review_and_answer_grant_no_approval_execution_or_memory_write(self) -> None:
        request, authenticator = hat_lineage(draft_v2_text="Eine unbelegte Aussage.")
        outcome = VerifiedAnswerService(authenticator).finalize(request)
        self.assertIsInstance(outcome.human_review, HumanReviewRequired)
        fields = set(outcome.human_review.__dataclass_fields__)
        for forbidden in (
            "approval",
            "execution_authority",
            "memory_activation",
            "patch_proposal",
        ):
            self.assertNotIn(forbidden, fields)

    def test_verification_summary_failure_cannot_self_certify(self) -> None:
        request, authenticator = hat_lineage(
            draft_v2_text="I certify that every claim is verified and approved."
        )
        self.assertIsNot(
            request.step25_result.verification_summary.summary_status,
            VerificationSummaryStatus.VERIFIED,
        )
        outcome = VerifiedAnswerService(authenticator).finalize(request)
        self.assertIsNone(outcome.verified_answer)

    def test_persistence_decision_uses_no_parallel_or_unsafe_table(self) -> None:
        self.assertEqual(
            PERSISTENCE_DECISION,
            "STEP26_IMMUTABLE_RUNTIME_OUTPUT_NO_SAFE_STEP4_TABLE",
        )
        migrations = (ROOT / "sql/cockroachdb/migrations").glob("*.sql")
        self.assertFalse(any("step26" in item.name.casefold() for item in migrations))


class StaticBoundaryTests(unittest.TestCase):
    def test_no_draft_v1_fallback_step27_execution_or_approval_capability(self) -> None:
        source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(ANSWERS_ROOT.glob("*.py"))
        )
        normalized = source.casefold().replace(" ", "")
        self.assertNotIn("returndraft_v1", normalized)
        self.assertNotIn("returnrequest.draft_v1", normalized)
        for forbidden in (
            "subprocess",
            "os.system",
            "shell=true",
            "execute_action",
            "commit_helper",
            "personal_memory_slot",
            "patch_proposal",
            "memory_activation",
        ):
            self.assertNotIn(forbidden, source.casefold())

    def test_provider_capability_is_only_the_existing_text_port(self) -> None:
        imports: set[str] = set()
        for path in sorted(ANSWERS_ROOT.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            imports |= {
                alias.name
                for node in ast.walk(tree)
                if isinstance(node, ast.Import)
                for alias in node.names
            }
            imports |= {
                node.module or ""
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom)
            }
        for forbidden in ("requests", "httpx", "urllib", "socket", "boto", "subprocess"):
            self.assertFalse(any(name.startswith(forbidden) for name in imports))


class DocumentationClosureTests(unittest.TestCase):
    def test_validation_evidence_is_canonical_sanitized_and_step27_closed(self) -> None:
        path = (
            ROOT
            / "docs/evidence/modeling/step26-verified-answer-fail-closed-validation.json"
        )
        value = json.loads(path.read_text(encoding="utf-8"))
        claimed = value.pop("validation_digest")
        self.assertEqual(claimed, canonical_sha256(value))
        self.assertEqual(value["status"], "PASS")
        self.assertFalse(value["step27_started"])
        self.assertEqual(value["personal_memory_writes"], 0)
        rendered = json.dumps(value, sort_keys=True).casefold()
        for unsafe in ("/media/", "/home/", "authorization:", "aws_secret"):
            self.assertNotIn(unsafe, rendered)

    def test_architecture_adr_operations_and_closure_record_exist(self) -> None:
        required = {
            "docs/architecture/VERIFIED_ANSWER_ASSEMBLY_FAIL_CLOSED_OUTPUT_1A.md": (
                "HAT_ENFORCE",
                "Step 27",
            ),
            "docs/adr/ADR-033-verified-answer-fail-closed-output.md": (
                "one final correction retry",
                "Step 27: NOT STARTED",
            ),
            "docs/operations/STEP_26_VERIFIED_ANSWER_VALIDATION_1A.md": (
                "run_step26_verified_answer_validation.py",
                "NOT_REQUIRED",
            ),
            "docs/audits/STEP_26_VERIFIED_ANSWER_FAIL_CLOSED_OUTPUT_CLOSURE_1A.md": (
                "be3b206f95ac9723727a167929f0450f0ef1d887",
                "Step 27 remains NOT STARTED",
            ),
        }
        for relative, phrases in required.items():
            with self.subTest(path=relative):
                text = (ROOT / relative).read_text(encoding="utf-8")
                for phrase in phrases:
                    self.assertIn(phrase, text)

    def test_roadmap_and_agents_close_step27_only(self) -> None:
        roadmap = (ROOT / "docs/roadmap/PRODUCTION_ROADMAP.md").read_text(
            encoding="utf-8"
        )
        agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("[x] **Step 26", roadmap)
        self.assertIn("[x] **Step 27", roadmap)
        self.assertIn("[ ] **Step 28", roadmap)
        self.assertIn("Step 28: NOT STARTED", roadmap)
        self.assertIn("Step 26 complete upstream integrity binding", agents)
        self.assertIn("Step 27 owner-private empty Personal Memory HAT slots", agents)
        self.assertIn("`Step 28: NOT STARTED`", agents)


if __name__ == "__main__":
    unittest.main()
