"""Step 17 Axis A routing and Axis B policy boundary tests."""

from __future__ import annotations

import ast
import dataclasses
import hashlib
import json
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from tests._support import REPOSITORY_ROOT

from aioa_memory_kernel.contracts import (
    AnswerStatus,
    ContractValidationError,
    EvidenceStatus,
    IntegrityError,
    KnowledgeRoute,
    ScopeComparisonMode,
    ScopeDimension,
    ScopeValueType,
)
from aioa_memory_kernel.contracts.serialization import canonical_sha256
from aioa_memory_kernel.hats import (
    CompatibilityDecision,
    HatRegistryService,
    ManifestIdentity,
    RegistryEntry,
    RegistryState,
    ReviewActor,
    ReviewReceipt,
    RuntimeBinding,
    decode_manifest,
    decide_compatibility,
)
from aioa_memory_kernel.routing import (
    AuthorityPolicyContext,
    EvidenceCoverageStatus,
    ExecutionAuthorizationDecision,
    HatCandidateAvailability,
    HatKernelResult,
    HatPolicyRequirement,
    HatRoutingCandidate,
    KnowledgePolicyDecision,
    KnowledgeRouteDecision,
    KnowledgeRouteResult,
    PolicyGateResult,
    RoutingInput,
    Step17ReasonCode,
    TrustedHatRegistrySnapshot,
    build_hat_kernel_result,
    evaluate_policy_gate,
    route_knowledge_request,
    verify_kernel_result_hash,
    verify_policy_result_hash,
    verify_route_hash,
)


NOW = datetime(2031, 6, 1, tzinfo=UTC)
MANIFEST_PATH = REPOSITORY_ROOT / "config/hats/german-law-1.0.0.json"
SCHEMA_PATH = REPOSITORY_ROOT / "schemas/hat-manifest.schema.json"
GERMAN_IDENTITY = decode_manifest(
    MANIFEST_PATH.read_bytes(),
    schema_path=SCHEMA_PATH,
)
POLICY_DIGEST = canonical_sha256(
    {"policy_id": "step17-axis-b", "policy_version": "1a"}
)


def _enabled_entry(identity: ManifestIdentity) -> RegistryEntry:
    manifest = identity.manifest
    service = HatRegistryService(clock=lambda: NOW)
    service.register(identity, decide_compatibility(identity))
    service.validate(manifest.hat_id, manifest.hat_version)
    implementation_digest = hashlib.sha256(
        f"installed:{manifest.hat_id}:{manifest.hat_version}".encode()
    ).hexdigest()
    binding = RuntimeBinding(
        runtime_binding_id=f"{manifest.hat_id}-system-step17",
        hat_id=manifest.hat_id,
        hat_version=manifest.hat_version,
        implementation_name="SyntheticStep17Hat",
        implementation_version=manifest.hat_version,
        implementation_contract_version="hat-sdk-1a",
        implementation_digest=implementation_digest,
    )
    receipt = ReviewReceipt(
        hat_id=manifest.hat_id,
        hat_version=manifest.hat_version,
        canonical_manifest_digest=identity.typed_manifest_digest,
        raw_manifest_digest=identity.raw_manifest_sha256,
        schema_digest=identity.schema_file_sha256,
        compatibility=CompatibilityDecision.COMPATIBLE,
        capabilities_digest=canonical_sha256(manifest.capabilities),
        runtime_binding_id=binding.runtime_binding_id,
        implementation_digest=implementation_digest,
        decision="ENABLE",
        reason_codes=("TRUSTED_STEP17_FIXTURE",),
        actor_type=ReviewActor.TRUSTED_OPERATOR,
        actor_reference="operator-redacted",
        reviewed_at=NOW,
    )
    return service.enable(
        manifest.hat_id,
        manifest.hat_version,
        binding,
        receipt,
    )


def _cloned_identity(hat_id: str) -> ManifestIdentity:
    manifest = dataclasses.replace(
        GERMAN_IDENTITY.manifest,
        hat_id=hat_id,
        display_name=f"Synthetic {hat_id}",
    )
    synthetic_raw = canonical_sha256(
        {"fixture": "step17", "hat_id": hat_id}
    )
    return ManifestIdentity(
        manifest=manifest,
        raw_manifest_sha256=synthetic_raw,
        canonical_manifest_sha256=synthetic_raw,
        typed_manifest_digest=canonical_sha256(manifest),
        schema_file_sha256=GERMAN_IDENTITY.schema_file_sha256,
    )


GERMAN_ENTRY = _enabled_entry(GERMAN_IDENTITY)
SECOND_ENTRY = _enabled_entry(_cloned_identity("synthetic-legal-hat"))


def _german_scope() -> tuple[ScopeDimension, ...]:
    return (
        ScopeDimension(
            name="legal_jurisdiction",
            value="DE_FEDERAL",
            value_type=ScopeValueType.STRING,
            comparison_mode=ScopeComparisonMode.EXACT,
            source="trusted-request",
            required=True,
        ),
        ScopeDimension(
            name="knowledge_as_of",
            value=NOW,
            value_type=ScopeValueType.TIMESTAMP,
            comparison_mode=ScopeComparisonMode.TIMESTAMP,
            source="trusted-request",
            required=True,
        ),
        ScopeDimension(
            name="source_language",
            value="de",
            value_type=ScopeValueType.STRING,
            comparison_mode=ScopeComparisonMode.EXACT,
            source="trusted-request",
            required=True,
        ),
        ScopeDimension(
            name="legal_source_class",
            value=("DE_FEDERAL_OFFICIAL_CONSOLIDATED_LAW",),
            value_type=ScopeValueType.STRING_SET,
            comparison_mode=ScopeComparisonMode.IN_SET,
            source="trusted-request",
            required=True,
        ),
    )


def _candidate(
    entry: RegistryEntry = GERMAN_ENTRY,
    requirement: HatPolicyRequirement = HatPolicyRequirement.ADVISORY,
    **changes: object,
) -> HatRoutingCandidate:
    values: dict[str, object] = {
        "hat_id": entry.identity.manifest.hat_id,
        "hat_version": entry.identity.manifest.hat_version,
        "manifest_digest": entry.identity.typed_manifest_digest,
        "policy_requirement": requirement,
    }
    values.update(changes)
    return HatRoutingCandidate(**values)


def _routing_input(
    *,
    candidates: tuple[HatRoutingCandidate, ...] = (),
    entries: tuple[RegistryEntry, ...] = (GERMAN_ENTRY,),
    scope: tuple[ScopeDimension, ...] | None = None,
    domain_id: str = "law.de.federal",
    evidence_status: EvidenceStatus = EvidenceStatus.SUFFICIENT,
    evidence_coverage_status: EvidenceCoverageStatus = (
        EvidenceCoverageStatus.COMPLETE
    ),
    tenant_id: str = "tenant-alpha",
    user_id: str = "user-alpha",
    context_metadata: dict[str, object] | None = None,
) -> RoutingInput:
    return RoutingInput(
        tenant_id=tenant_id,
        user_id=user_id,
        request_id="request-step17-1",
        request_kind="knowledge-question",
        normalized_query_or_subject="Welche Fassung gilt am Stichtag?",
        requested_domain_id=domain_id,
        requested_scope=_german_scope() if scope is None else scope,
        candidate_hat_descriptors=candidates,
        trusted_hat_registry_snapshot=TrustedHatRegistrySnapshot(
            snapshot_reference="trusted-registry:step17:test",
            entries=entries,
        ),
        evidence_status=evidence_status,
        evidence_coverage_status=evidence_coverage_status,
        context_metadata=(
            {"classification_source": "deterministic-rule"}
            if context_metadata is None
            else context_metadata
        ),
    )


def _policy_context(
    route: KnowledgeRouteResult,
    *,
    knowledge: KnowledgePolicyDecision = KnowledgePolicyDecision.ALLOW_ANSWER,
    execution: ExecutionAuthorizationDecision = (
        ExecutionAuthorizationDecision.DENY
    ),
    scope_allowed: bool = True,
    permitted_scope: tuple[ScopeDimension, ...] = (),
) -> AuthorityPolicyContext:
    return AuthorityPolicyContext(
        request_id=route.request_id,
        tenant_id=route.tenant_id,
        user_id=route.user_id,
        policy_reference="policy:step17:test",
        policy_digest=POLICY_DIGEST,
        knowledge_policy_ceiling=knowledge,
        execution_authorization_ceiling=execution,
        scope_allowed=scope_allowed,
        permitted_execution_scope=permitted_scope,
        selected_hat_id=route.selected_hat_id,
        selected_hat_version=route.selected_hat_version,
        selected_manifest_digest=route.selected_manifest_digest,
    )


def _route_and_policy(
    routing_input: RoutingInput,
    **context_changes: object,
) -> tuple[KnowledgeRouteResult, PolicyGateResult]:
    route = route_knowledge_request(routing_input)
    context = _policy_context(route, **context_changes)
    return route, evaluate_policy_gate(routing_input, route, context)


class DecisionContractTests(unittest.TestCase):
    def test_exact_decision_enum_values(self) -> None:
        self.assertEqual(
            [value.value for value in KnowledgeRouteDecision],
            ["PASS_THROUGH", "HAT_ASSIST", "HAT_ENFORCE", "AMBIGUOUS"],
        )
        self.assertEqual(
            [value.value for value in KnowledgePolicyDecision],
            ["ALLOW_ANSWER", "BLOCK_ANSWER", "REQUIRE_CONFIRMATION"],
        )
        self.assertEqual(
            [value.value for value in ExecutionAuthorizationDecision],
            ["ALLOW", "ALLOW_SCOPED", "REQUIRE_HUMAN", "DENY"],
        )

    def test_existing_evidence_status_is_reused_with_separate_coverage(self) -> None:
        self.assertEqual(
            [value.value for value in EvidenceCoverageStatus],
            ["COMPLETE", "PARTIAL", "EMPTY", "CONFLICTING"],
        )
        self.assertIsNot(EvidenceStatus, AnswerStatus)
        self.assertIsNot(EvidenceCoverageStatus, AnswerStatus)

    def test_inputs_and_outputs_are_immutable(self) -> None:
        value = _routing_input()
        route = route_knowledge_request(value)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            value.tenant_id = "tenant-other"  # type: ignore[misc]
        with self.assertRaises(dataclasses.FrozenInstanceError):
            route.knowledge_route = KnowledgeRoute.HAT_ENFORCE  # type: ignore[misc]
        with self.assertRaises(TypeError):
            value.context_metadata["x"] = "y"  # type: ignore[index]

    def test_same_input_has_same_decision_and_hash(self) -> None:
        first = _routing_input(candidates=(_candidate(),))
        second = _routing_input(candidates=(_candidate(),))
        first_route = route_knowledge_request(first)
        second_route = route_knowledge_request(second)
        self.assertEqual(first, second)
        self.assertEqual(first.input_hash, second.input_hash)
        self.assertEqual(first_route, second_route)
        self.assertEqual(first_route.route_hash, second_route.route_hash)
        verify_route_hash(first_route)

    def test_candidate_order_is_canonical(self) -> None:
        mandatory = _candidate(
            GERMAN_ENTRY,
            HatPolicyRequirement.MANDATORY,
        )
        advisory = _candidate(SECOND_ENTRY, HatPolicyRequirement.ADVISORY)
        entries = (SECOND_ENTRY, GERMAN_ENTRY)
        first = _routing_input(
            candidates=(mandatory, advisory),
            entries=entries,
        )
        second = _routing_input(
            candidates=(advisory, mandatory),
            entries=tuple(reversed(entries)),
        )
        self.assertEqual(first.input_hash, second.input_hash)
        self.assertEqual(
            route_knowledge_request(first).route_hash,
            route_knowledge_request(second).route_hash,
        )

    def test_duplicate_candidates_are_rejected(self) -> None:
        candidate = _candidate()
        with self.assertRaises(ContractValidationError):
            _routing_input(candidates=(candidate, candidate))

    def test_invalid_evidence_and_policy_states_fail_closed(self) -> None:
        with self.assertRaises(ContractValidationError):
            dataclasses.replace(_routing_input(), evidence_status="VERIFIED")
        with self.assertRaises(ContractValidationError):
            dataclasses.replace(
                _routing_input(),
                evidence_coverage_status=EvidenceCoverageStatus.PARTIAL,
            )
        route = route_knowledge_request(_routing_input())
        with self.assertRaises(ContractValidationError):
            dataclasses.replace(
                _policy_context(route),
                knowledge_policy_ceiling="ALLOW_ANSWER",
            )

    def test_hash_mismatch_is_rejected(self) -> None:
        routing_input = _routing_input(candidates=(_candidate(),))
        route = route_knowledge_request(routing_input)
        object.__setattr__(route, "reason_codes", (Step17ReasonCode.HAT_UNKNOWN,))
        with self.assertRaises(IntegrityError):
            evaluate_policy_gate(
                routing_input,
                route,
                _policy_context(route),
            )


class AxisARoutingTests(unittest.TestCase):
    def test_no_eligible_hat_passes_through(self) -> None:
        result = route_knowledge_request(_routing_input())
        self.assertIs(result.knowledge_route, KnowledgeRoute.PASS_THROUGH)
        self.assertIn(Step17ReasonCode.NO_ELIGIBLE_HAT, result.reason_codes)
        self.assertIsNone(result.selected_hat_id)

    def test_one_trusted_advisory_hat_assists(self) -> None:
        result = route_knowledge_request(
            _routing_input(candidates=(_candidate(),))
        )
        self.assertIs(result.knowledge_route, KnowledgeRoute.HAT_ASSIST)
        self.assertEqual(result.selected_hat_id, "german-law")
        self.assertIn(Step17ReasonCode.SINGLE_ASSISTING_HAT, result.reason_codes)

    def test_mandatory_trusted_hat_enforces_knowledge_policy(self) -> None:
        result = route_knowledge_request(
            _routing_input(
                candidates=(
                    _candidate(
                        GERMAN_ENTRY,
                        HatPolicyRequirement.MANDATORY,
                    ),
                )
            )
        )
        self.assertIs(result.knowledge_route, KnowledgeRoute.HAT_ENFORCE)
        self.assertIn(Step17ReasonCode.MANDATORY_HAT_POLICY, result.reason_codes)

    def test_multiple_mandatory_hats_are_ambiguous(self) -> None:
        result = route_knowledge_request(
            _routing_input(
                candidates=(
                    _candidate(GERMAN_ENTRY, HatPolicyRequirement.MANDATORY),
                    _candidate(SECOND_ENTRY, HatPolicyRequirement.MANDATORY),
                ),
                entries=(GERMAN_ENTRY, SECOND_ENTRY),
            )
        )
        self.assertIs(result.knowledge_route, KnowledgeRoute.AMBIGUOUS)
        self.assertIn(Step17ReasonCode.MULTIPLE_HAT_CONFLICT, result.reason_codes)
        self.assertIsNone(result.selected_hat_id)

    def test_disabled_hat_cannot_route(self) -> None:
        disabled = dataclasses.replace(GERMAN_ENTRY, state=RegistryState.DISABLED)
        result = route_knowledge_request(
            _routing_input(
                candidates=(_candidate(disabled),),
                entries=(disabled,),
            )
        )
        self.assertIs(result.knowledge_route, KnowledgeRoute.PASS_THROUGH)
        self.assertIn(Step17ReasonCode.HAT_DISABLED, result.reason_codes)

    def test_untrusted_hat_cannot_route(self) -> None:
        untrusted = dataclasses.replace(
            GERMAN_ENTRY,
            compatibility=CompatibilityDecision.INCOMPATIBLE_KERNEL_API,
        )
        result = route_knowledge_request(
            _routing_input(
                candidates=(
                    _candidate(untrusted, HatPolicyRequirement.MANDATORY),
                ),
                entries=(untrusted,),
            )
        )
        self.assertIs(result.knowledge_route, KnowledgeRoute.AMBIGUOUS)
        self.assertIn(Step17ReasonCode.HAT_UNTRUSTED, result.reason_codes)

    def test_quarantined_and_revoked_hats_cannot_route(self) -> None:
        for availability, reason in (
            (
                HatCandidateAvailability.QUARANTINED,
                Step17ReasonCode.HAT_QUARANTINED,
            ),
            (HatCandidateAvailability.REVOKED, Step17ReasonCode.HAT_REVOKED),
        ):
            with self.subTest(availability=availability):
                result = route_knowledge_request(
                    _routing_input(
                        candidates=(
                            _candidate(availability=availability),
                        )
                    )
                )
                self.assertIs(result.knowledge_route, KnowledgeRoute.PASS_THROUGH)
                self.assertIn(reason, result.reason_codes)

    def test_unknown_and_wrong_version_hats_fail_closed(self) -> None:
        cases = (
            (
                _candidate(
                    GERMAN_ENTRY,
                    HatPolicyRequirement.MANDATORY,
                    hat_id="unknown-hat",
                ),
                Step17ReasonCode.HAT_UNKNOWN,
            ),
            (
                _candidate(
                    GERMAN_ENTRY,
                    HatPolicyRequirement.MANDATORY,
                    hat_version="2.0.0",
                ),
                Step17ReasonCode.HAT_VERSION_MISMATCH,
            ),
        )
        for candidate, reason in cases:
            with self.subTest(reason=reason):
                result = route_knowledge_request(
                    _routing_input(candidates=(candidate,))
                )
                self.assertIs(result.knowledge_route, KnowledgeRoute.AMBIGUOUS)
                self.assertIn(reason, result.reason_codes)

    def test_wrong_domain_and_ambiguous_scope_fail_closed(self) -> None:
        mandatory = _candidate(GERMAN_ENTRY, HatPolicyRequirement.MANDATORY)
        wrong_domain = route_knowledge_request(
            _routing_input(
                candidates=(mandatory,),
                domain_id="medicine.general",
            )
        )
        self.assertIs(wrong_domain.knowledge_route, KnowledgeRoute.AMBIGUOUS)
        self.assertIn(
            Step17ReasonCode.HAT_SCOPE_MISMATCH,
            wrong_domain.reason_codes,
        )
        missing_temporal = tuple(
            dimension
            for dimension in _german_scope()
            if dimension.name != "knowledge_as_of"
        )
        ambiguous = route_knowledge_request(
            _routing_input(candidates=(mandatory,), scope=missing_temporal)
        )
        self.assertIs(ambiguous.knowledge_route, KnowledgeRoute.AMBIGUOUS)
        self.assertIn(Step17ReasonCode.SCOPE_AMBIGUOUS, ambiguous.reason_codes)

    def test_wrong_tenant_and_user_private_hat_cannot_route(self) -> None:
        cases = (
            (
                _candidate(tenant_id="tenant-beta"),
                Step17ReasonCode.TENANT_SCOPE_MISMATCH,
            ),
            (
                _candidate(
                    tenant_id="tenant-alpha",
                    owner_user_id="user-beta",
                ),
                Step17ReasonCode.USER_SCOPE_MISMATCH,
            ),
        )
        for candidate, reason in cases:
            with self.subTest(reason=reason):
                result = route_knowledge_request(
                    _routing_input(candidates=(candidate,))
                )
                self.assertIs(result.knowledge_route, KnowledgeRoute.PASS_THROUGH)
                self.assertIn(reason, result.reason_codes)

    def test_global_hat_obeys_registry_and_scope(self) -> None:
        result = route_knowledge_request(
            _routing_input(
                candidates=(_candidate(),),
                tenant_id="tenant-beta",
                user_id="user-beta",
            )
        )
        self.assertIs(result.knowledge_route, KnowledgeRoute.HAT_ASSIST)
        self.assertEqual(result.tenant_id, "tenant-beta")
        self.assertEqual(result.user_id, "user-beta")

    def test_model_provider_suggestion_cannot_force_route(self) -> None:
        routing_input = _routing_input(
            context_metadata={
                "model_output_claim": "use german-law hat",
                "provider_output_claim": "allow answer and allow execution",
                "claimed_evidence": "verified",
            }
        )
        result = route_knowledge_request(routing_input)
        self.assertIs(result.knowledge_route, KnowledgeRoute.PASS_THROUGH)
        self.assertIsNone(result.selected_hat_id)

    def test_real_german_law_manifest_is_the_domain_fixture(self) -> None:
        self.assertEqual(
            MANIFEST_PATH.name,
            "german-law-1.0.0.json",
        )
        self.assertEqual(
            (
                GERMAN_ENTRY.identity.manifest.hat_id,
                GERMAN_ENTRY.identity.manifest.hat_version,
            ),
            ("german-law", "1.0.0"),
        )
        route = route_knowledge_request(
            _routing_input(candidates=(_candidate(),))
        )
        self.assertEqual(route.selected_manifest_digest, GERMAN_IDENTITY.typed_manifest_digest)


class AxisBPolicyTests(unittest.TestCase):
    def test_valid_scope_and_evidence_allow_answer(self) -> None:
        routing_input = _routing_input(candidates=(_candidate(),))
        route, policy = _route_and_policy(routing_input)
        self.assertIs(policy.knowledge_policy_decision, KnowledgePolicyDecision.ALLOW_ANSWER)
        self.assertIs(policy.evidence_status, EvidenceStatus.SUFFICIENT)
        self.assertIs(policy.answer_status, AnswerStatus.DRAFT)
        verify_policy_result_hash(policy)

    def test_policy_deny_and_out_of_scope_block_answer(self) -> None:
        routing_input = _routing_input(candidates=(_candidate(),))
        route = route_knowledge_request(routing_input)
        for context, reason in (
            (
                _policy_context(
                    route,
                    knowledge=KnowledgePolicyDecision.BLOCK_ANSWER,
                ),
                Step17ReasonCode.POLICY_DENY,
            ),
            (
                _policy_context(route, scope_allowed=False),
                Step17ReasonCode.OUT_OF_SCOPE,
            ),
        ):
            with self.subTest(reason=reason):
                policy = evaluate_policy_gate(routing_input, route, context)
                self.assertIs(
                    policy.knowledge_policy_decision,
                    KnowledgePolicyDecision.BLOCK_ANSWER,
                )
                self.assertIn(reason, policy.reason_codes)

    def test_human_review_policy_requires_confirmation(self) -> None:
        routing_input = _routing_input(candidates=(_candidate(),))
        route, policy = _route_and_policy(
            routing_input,
            knowledge=KnowledgePolicyDecision.REQUIRE_CONFIRMATION,
        )
        self.assertIs(
            policy.knowledge_policy_decision,
            KnowledgePolicyDecision.REQUIRE_CONFIRMATION,
        )
        self.assertIn(
            Step17ReasonCode.HUMAN_CONFIRMATION_REQUIRED,
            policy.reason_codes,
        )

    def test_insufficient_and_unavailable_evidence_block(self) -> None:
        cases = (
            (EvidenceStatus.INSUFFICIENT, EvidenceCoverageStatus.EMPTY),
            (EvidenceStatus.UNAVAILABLE, EvidenceCoverageStatus.EMPTY),
            (EvidenceStatus.STALE, EvidenceCoverageStatus.COMPLETE),
            (EvidenceStatus.INVALID, EvidenceCoverageStatus.COMPLETE),
        )
        for evidence, coverage in cases:
            with self.subTest(evidence=evidence, coverage=coverage):
                routing_input = _routing_input(
                    candidates=(_candidate(),),
                    evidence_status=evidence,
                    evidence_coverage_status=coverage,
                )
                _, policy = _route_and_policy(routing_input)
                self.assertIs(
                    policy.knowledge_policy_decision,
                    KnowledgePolicyDecision.BLOCK_ANSWER,
                )
                self.assertIs(
                    policy.answer_status,
                    AnswerStatus.BLOCKED_NO_VERIFIED_EVIDENCE,
                )

    def test_partial_and_conflicting_evidence_require_review(self) -> None:
        cases = (
            (
                EvidenceStatus.INSUFFICIENT,
                EvidenceCoverageStatus.PARTIAL,
                AnswerStatus.DRAFT,
            ),
            (
                EvidenceStatus.CONFLICTING,
                EvidenceCoverageStatus.CONFLICTING,
                AnswerStatus.BLOCKED_CONFLICTING_EVIDENCE,
            ),
        )
        for evidence, coverage, answer in cases:
            with self.subTest(evidence=evidence, coverage=coverage):
                routing_input = _routing_input(
                    candidates=(_candidate(),),
                    evidence_status=evidence,
                    evidence_coverage_status=coverage,
                )
                _, policy = _route_and_policy(routing_input)
                self.assertIs(
                    policy.knowledge_policy_decision,
                    KnowledgePolicyDecision.REQUIRE_CONFIRMATION,
                )
                self.assertIs(policy.answer_status, answer)

    def test_answer_and_evidence_status_remain_independent(self) -> None:
        routing_input = _routing_input(
            candidates=(_candidate(),),
            evidence_status=EvidenceStatus.INSUFFICIENT,
            evidence_coverage_status=EvidenceCoverageStatus.PARTIAL,
        )
        route, policy = _route_and_policy(routing_input)
        result = build_hat_kernel_result(
            routing_input,
            route,
            policy,
            provenance_references=("evidence:step16:german-law-corpus",),
        )
        self.assertIs(result.evidence_status, EvidenceStatus.INSUFFICIENT)
        self.assertIs(
            result.evidence_coverage_status,
            EvidenceCoverageStatus.PARTIAL,
        )
        self.assertIs(result.answer_status, AnswerStatus.DRAFT)
        self.assertIs(result.knowledge_route, KnowledgeRoute.HAT_ASSIST)
        self.assertEqual(
            result.selected_manifest_digest,
            GERMAN_ENTRY.identity.typed_manifest_digest,
        )
        self.assertIsNot(type(result.evidence_status), type(result.answer_status))
        verify_kernel_result_hash(result)

    def test_hat_enforce_does_not_grant_execution(self) -> None:
        routing_input = _routing_input(
            candidates=(
                _candidate(GERMAN_ENTRY, HatPolicyRequirement.MANDATORY),
            )
        )
        route, policy = _route_and_policy(
            routing_input,
            execution=ExecutionAuthorizationDecision.REQUIRE_HUMAN,
        )
        self.assertIs(route.knowledge_route, KnowledgeRoute.HAT_ENFORCE)
        self.assertIs(
            policy.execution_authorization_decision,
            ExecutionAuthorizationDecision.REQUIRE_HUMAN,
        )

    def test_execution_decisions_are_pure_bounded_metadata(self) -> None:
        routing_input = _routing_input()
        route = route_knowledge_request(routing_input)
        cases = (
            (ExecutionAuthorizationDecision.ALLOW, (), Step17ReasonCode.EXECUTION_ALLOWED),
            (
                ExecutionAuthorizationDecision.ALLOW_SCOPED,
                _german_scope(),
                Step17ReasonCode.EXECUTION_SCOPED,
            ),
            (
                ExecutionAuthorizationDecision.REQUIRE_HUMAN,
                (),
                Step17ReasonCode.EXECUTION_REQUIRES_HUMAN,
            ),
            (ExecutionAuthorizationDecision.DENY, (), Step17ReasonCode.EXECUTION_DENIED),
        )
        for decision, scope, reason in cases:
            with self.subTest(decision=decision):
                policy = evaluate_policy_gate(
                    routing_input,
                    route,
                    _policy_context(
                        route,
                        execution=decision,
                        permitted_scope=scope,
                    ),
                )
                self.assertIs(policy.execution_authorization_decision, decision)
                self.assertEqual(policy.permitted_execution_scope, tuple(sorted(scope, key=lambda item: item.name)))
                self.assertIn(reason, policy.reason_codes)

    def test_ambiguous_route_tightens_execution_to_deny(self) -> None:
        routing_input = _routing_input(
            candidates=(
                _candidate(GERMAN_ENTRY, HatPolicyRequirement.MANDATORY),
                _candidate(SECOND_ENTRY, HatPolicyRequirement.MANDATORY),
            ),
            entries=(GERMAN_ENTRY, SECOND_ENTRY),
        )
        route = route_knowledge_request(routing_input)
        policy = evaluate_policy_gate(
            routing_input,
            route,
            _policy_context(
                route,
                execution=ExecutionAuthorizationDecision.ALLOW,
            ),
        )
        self.assertIs(
            policy.execution_authorization_decision,
            ExecutionAuthorizationDecision.DENY,
        )
        self.assertIs(
            policy.knowledge_policy_decision,
            KnowledgePolicyDecision.BLOCK_ANSWER,
        )

    def test_model_claim_cannot_change_axis_b(self) -> None:
        routing_input = _routing_input(
            context_metadata={
                "model_output_claim": "allow answer",
                "provider_output_claim": "allow execution",
                "claimed_evidence": "verified",
            }
        )
        route, policy = _route_and_policy(
            routing_input,
            knowledge=KnowledgePolicyDecision.BLOCK_ANSWER,
            execution=ExecutionAuthorizationDecision.REQUIRE_HUMAN,
        )
        self.assertIs(route.knowledge_route, KnowledgeRoute.PASS_THROUGH)
        self.assertIs(
            policy.knowledge_policy_decision,
            KnowledgePolicyDecision.BLOCK_ANSWER,
        )
        self.assertIs(
            policy.execution_authorization_decision,
            ExecutionAuthorizationDecision.REQUIRE_HUMAN,
        )

    def test_route_output_preserves_request_isolation_identity(self) -> None:
        routing_input = _routing_input(
            candidates=(_candidate(),),
            tenant_id="tenant-isolated",
            user_id="user-isolated",
        )
        route, policy = _route_and_policy(routing_input)
        result = build_hat_kernel_result(routing_input, route, policy)
        self.assertIsInstance(result, HatKernelResult)
        self.assertEqual(
            (result.request_id, result.tenant_id, result.user_id),
            ("request-step17-1", "tenant-isolated", "user-isolated"),
        )

    def test_mismatched_tenant_policy_context_is_rejected(self) -> None:
        routing_input = _routing_input()
        route = route_knowledge_request(routing_input)
        context = dataclasses.replace(
            _policy_context(route),
            tenant_id="tenant-other",
        )
        with self.assertRaises(ContractValidationError):
            evaluate_policy_gate(routing_input, route, context)


class InertnessAndBoundaryTests(unittest.TestCase):
    ROUTING_ROOT = REPOSITORY_ROOT / "src/aioa_memory_kernel/routing"

    def test_routing_modules_import_no_effectful_runtime(self) -> None:
        forbidden_imports = {
            "boto3",
            "httpx",
            "psycopg",
            "requests",
            "socket",
            "subprocess",
            "urllib",
        }
        for path in self.ROUTING_ROOT.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            imported = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.update(alias.name.split(".")[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.add(node.module.split(".")[0])
            self.assertTrue(forbidden_imports.isdisjoint(imported), path)

    def test_public_boundary_exposes_no_execution_method(self) -> None:
        forbidden = {
            "call_provider",
            "execute",
            "invoke",
            "run",
            "run_subprocess",
            "write",
        }
        for contract_type in (
            RoutingInput,
            KnowledgeRouteResult,
            PolicyGateResult,
            HatKernelResult,
        ):
            self.assertTrue(forbidden.isdisjoint(dir(contract_type)))

    def test_router_and_policy_perform_no_external_effect(self) -> None:
        routing_input = _routing_input(candidates=(_candidate(),))
        with (
            patch("builtins.open", side_effect=AssertionError("filesystem")),
            patch("socket.socket", side_effect=AssertionError("network")),
            patch("subprocess.run", side_effect=AssertionError("subprocess")),
            patch("subprocess.Popen", side_effect=AssertionError("subprocess")),
        ):
            route = route_knowledge_request(routing_input)
            policy = evaluate_policy_gate(
                routing_input,
                route,
                _policy_context(route),
            )
        self.assertIs(route.knowledge_route, KnowledgeRoute.HAT_ASSIST)
        self.assertIs(
            policy.knowledge_policy_decision,
            KnowledgePolicyDecision.ALLOW_ANSWER,
        )

    def test_step18_retrieval_implementation_is_absent(self) -> None:
        forbidden_definitions = {
            "exact_retrieve",
            "full_text_search",
            "hybrid_search",
            "metadata_retrieve",
            "rerank",
            "vector_search",
        }
        for path in self.ROUTING_ROOT.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            definitions = {
                node.name
                for node in ast.walk(tree)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
            self.assertTrue(forbidden_definitions.isdisjoint(definitions), path)
            self.assertNotIn("step18", path.read_text(encoding="utf-8").casefold())

    def test_routing_package_has_no_database_or_migration_file(self) -> None:
        self.assertFalse(any(self.ROUTING_ROOT.rglob("*.sql")))
        changed_sql = tuple(
            path
            for path in (REPOSITORY_ROOT / "sql").rglob("*step17*")
            if path.is_file()
        )
        self.assertEqual(changed_sql, ())


class DocumentationAndEvidenceTests(unittest.TestCase):
    def test_validation_evidence_is_hash_bound(self) -> None:
        path = (
            REPOSITORY_ROOT
            / "docs/evidence/routing/step17-routing-policy-validation.json"
        )
        evidence = json.loads(path.read_text(encoding="utf-8"))
        claimed = evidence.pop("evidence_digest")
        self.assertEqual(canonical_sha256(evidence), claimed)
        self.assertEqual(evidence["status"], "PASS")
        self.assertFalse(evidence["step18_started"])

    def test_step17_architecture_adr_operations_and_closure_exist(self) -> None:
        required = {
            "docs/architecture/AXIS_A_ROUTER_AXIS_B_POLICY_GATE_EVIDENCE_STATUS_1A.md": (
                "Axis A",
                "Axis B",
                "EvidenceCoverageStatus",
                "Step 18",
            ),
            "docs/adr/ADR-024-axis-a-router-axis-b-policy-evidence-status.md": (
                "deterministic",
                "non-authoritative",
                "Retrieval begins only in Step 18",
            ),
            "docs/operations/STEP_17_ROUTING_POLICY_VALIDATION_1A.md": (
                "run_step17_routing_policy_validation.py",
                "non-mutating",
            ),
            "docs/audits/STEP_17_AXIS_A_ROUTER_AXIS_B_POLICY_GATE_CLOSURE_1A.md": (
                "c51b32373dba5437f027268d1806a3fcdc1b3a91",
                "Step 18: NOT STARTED",
            ),
        }
        for relative, tokens in required.items():
            text = (REPOSITORY_ROOT / relative).read_text(encoding="utf-8")
            for token in tokens:
                self.assertIn(token, text, relative)

    def test_roadmap_preserves_completed_steps_and_leaves_step28_open(self) -> None:
        roadmap = (
            REPOSITORY_ROOT / "docs/roadmap/PRODUCTION_ROADMAP.md"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "- [x] **Step 17 — Axis A Router, Axis B Policy Gate and Evidence Status 1A**",
            roadmap,
        )
        self.assertIn(
            "- [x] **Step 18 — Exact and Full-Text Retrieval Baseline 1A**",
            roadmap,
        )
        self.assertIn(
            "- [x] **Step 19 — Embedding Generation and Vector Retrieval Foundation 1A**",
            roadmap,
        )
        self.assertIn(
            "- [x] **Step 20 — Hybrid Retrieval, Evidence Bundle and Deterministic Ranking 1A**",
            roadmap,
        )
        self.assertIn(
            "- [x] **Step 21 — Temporal Resolver, Conflict Detection and Freshness Policy 1A**",
            roadmap,
        )
        self.assertIn(
            "- [x] **Step 22 — Provider-Neutral Model Adapter and Draft V1 1A**",
            roadmap,
        )
        self.assertIn(
            "- [x] **Step 23 — Claim Extraction and Evidence Binding 1A**",
            roadmap,
        )
        self.assertIn(
            "- [x] **Step 24 — Correction Packet Construction and Integrity 1A**",
            roadmap,
        )
        self.assertIn(
            "- [x] **Step 25 — Draft V2 Generation and Layered Claim Verifier 1A**",
            roadmap,
        )
        self.assertIn(
            "- [x] **Step 26 — Verified Answer Assembly and Fail-Closed Output 1A**",
            roadmap,
        )
        self.assertIn(
            "- [x] **Step 27 — Personal Memory HAT Persistence, Quotas and Model Bindings 1A**",
            roadmap,
        )
        self.assertIn(
            "- [x] **Step 28 — Knowledge Hub and Critic Prompt Loop Correction Candidate Bridge 1A**",
            roadmap,
        )
        self.assertIn(
            "- [x] **Step 29 — Personal Memory Patch Proposal and Evidence Validation 1A**",
            roadmap,
        )
        self.assertIn("- [x] **Step 30", roadmap)
        self.assertIn("- [x] **Step 31", roadmap)
        self.assertIn("- [x] **Step 32", roadmap)
        self.assertIn("- [ ] **Step 33", roadmap)
        agents = (REPOSITORY_ROOT / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("Step 19 immutable local-model embeddings", agents)
        self.assertIn("Step 20 verified Step 18/19 input binding", agents)
        self.assertIn("Step 21 verified Step 20 bundle binding", agents)
        self.assertIn("Step 22 provider-neutral original-query-only", agents)
        self.assertIn("Step 23 exact-span deterministic claim extraction", agents)
        self.assertIn("Step 24 verified frozen Step 23 input binding", agents)
        self.assertIn("Step 25 verified Correction Packet integrity gating", agents)
        self.assertIn("Step 26 complete upstream integrity binding", agents)
        self.assertIn("Step 27 owner-private empty Personal Memory HAT slots", agents)
        self.assertIn("Step 28 owner- and slot-bound Correction Candidate", agents)
        self.assertIn("Step 29: COMPLETE AND PUSHED", agents)
        self.assertIn("Step 30: COMPLETE AND PUSHED", agents)
        self.assertIn("Step 31: COMPLETE AND PUSHED", agents)
        self.assertIn("Step 32: COMPLETE AND PUSHED", agents)
        self.assertIn("Step 33: NOT STARTED", agents)


if __name__ == "__main__":
    unittest.main()
