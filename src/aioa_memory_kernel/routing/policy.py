"""Deterministic, side-effect-free Axis B policy gate."""

from __future__ import annotations

from aioa_memory_kernel.contracts.enums import AnswerStatus, EvidenceStatus, KnowledgeRoute
from aioa_memory_kernel.contracts.exceptions import ContractValidationError
from aioa_memory_kernel.contracts.scope import ScopeDimension
from aioa_memory_kernel.contracts.serialization import verify_canonical_hash

from .models import (
    AuthorityPolicyContext,
    EvidenceCoverageStatus,
    ExecutionAuthorizationDecision,
    HatKernelResult,
    KnowledgePolicyDecision,
    KnowledgeRouteResult,
    PolicyGateResult,
    RoutingInput,
    Step17ReasonCode,
    verify_policy_result_hash,
    verify_route_hash,
    verify_routing_input_hash,
)


_HARD_EVIDENCE_FAILURES = frozenset(
    {
        EvidenceStatus.UNAVAILABLE,
        EvidenceStatus.STALE,
        EvidenceStatus.INVALID,
    }
)


def _validate_bindings(
    routing_input: RoutingInput,
    route: KnowledgeRouteResult,
    context: AuthorityPolicyContext,
) -> None:
    if not isinstance(routing_input, RoutingInput):
        raise ContractValidationError("routing_input must be typed")
    if not isinstance(route, KnowledgeRouteResult):
        raise ContractValidationError("route must be typed")
    if not isinstance(context, AuthorityPolicyContext):
        raise ContractValidationError("policy context must be typed")
    verify_routing_input_hash(routing_input)
    verify_route_hash(route)
    verify_canonical_hash(
        context,
        context.context_hash,
        exclude_fields=("context_hash",),
    )
    identities = (
        (routing_input.request_id, route.request_id, context.request_id),
        (routing_input.tenant_id, route.tenant_id, context.tenant_id),
        (routing_input.user_id, route.user_id, context.user_id),
    )
    if any(len(set(values)) != 1 for values in identities):
        raise ContractValidationError(
            "routing input, route, and policy context identities differ"
        )
    if route.routing_input_hash != routing_input.input_hash:
        raise ContractValidationError("route is bound to another routing input")
    if (
        route.registry_snapshot_hash
        != routing_input.trusted_hat_registry_snapshot.snapshot_hash
    ):
        raise ContractValidationError("route is bound to another registry snapshot")
    context_hat = (
        context.selected_hat_id,
        context.selected_hat_version,
        context.selected_manifest_digest,
    )
    route_hat = (
        route.selected_hat_id,
        route.selected_hat_version,
        route.selected_manifest_digest,
    )
    if route.knowledge_route in {
        KnowledgeRoute.HAT_ASSIST,
        KnowledgeRoute.HAT_ENFORCE,
    }:
        if context_hat != route_hat:
            raise ContractValidationError(
                "trusted HAT policy identity differs from the selected route"
            )
    elif any(value is not None for value in context_hat):
        raise ContractValidationError(
            "a non-HAT route cannot consume a HAT policy identity"
        )


def _knowledge_decision(
    routing_input: RoutingInput,
    route: KnowledgeRouteResult,
    context: AuthorityPolicyContext,
) -> tuple[KnowledgePolicyDecision, AnswerStatus, set[Step17ReasonCode]]:
    evidence = routing_input.evidence_status
    if route.knowledge_route is KnowledgeRoute.AMBIGUOUS:
        return (
            KnowledgePolicyDecision.BLOCK_ANSWER,
            AnswerStatus.BLOCKED_AMBIGUOUS_ROUTE,
            {Step17ReasonCode.SCOPE_AMBIGUOUS},
        )
    if not context.scope_allowed:
        return (
            KnowledgePolicyDecision.BLOCK_ANSWER,
            AnswerStatus.BLOCKED_POLICY,
            {Step17ReasonCode.OUT_OF_SCOPE},
        )
    if context.knowledge_policy_ceiling is KnowledgePolicyDecision.BLOCK_ANSWER:
        return (
            KnowledgePolicyDecision.BLOCK_ANSWER,
            AnswerStatus.BLOCKED_POLICY,
            {Step17ReasonCode.POLICY_DENY},
        )
    if evidence in _HARD_EVIDENCE_FAILURES:
        return (
            KnowledgePolicyDecision.BLOCK_ANSWER,
            AnswerStatus.BLOCKED_NO_VERIFIED_EVIDENCE,
            {Step17ReasonCode.EVIDENCE_INSUFFICIENT},
        )
    if (
        evidence is EvidenceStatus.INSUFFICIENT
        and routing_input.evidence_coverage_status
        is EvidenceCoverageStatus.EMPTY
    ):
        return (
            KnowledgePolicyDecision.BLOCK_ANSWER,
            AnswerStatus.BLOCKED_NO_VERIFIED_EVIDENCE,
            {Step17ReasonCode.EVIDENCE_INSUFFICIENT},
        )
    if (
        route.knowledge_route
        in {KnowledgeRoute.HAT_ASSIST, KnowledgeRoute.HAT_ENFORCE}
        and evidence is EvidenceStatus.NOT_REQUIRED
    ):
        return (
            KnowledgePolicyDecision.BLOCK_ANSWER,
            AnswerStatus.BLOCKED_NO_VERIFIED_EVIDENCE,
            {Step17ReasonCode.EVIDENCE_INSUFFICIENT},
        )
    if evidence is EvidenceStatus.CONFLICTING:
        return (
            KnowledgePolicyDecision.REQUIRE_CONFIRMATION,
            AnswerStatus.BLOCKED_CONFLICTING_EVIDENCE,
            {
                Step17ReasonCode.EVIDENCE_CONFLICTING,
                Step17ReasonCode.HUMAN_CONFIRMATION_REQUIRED,
            },
        )
    if (
        routing_input.evidence_coverage_status
        is EvidenceCoverageStatus.PARTIAL
        or context.knowledge_policy_ceiling
        is KnowledgePolicyDecision.REQUIRE_CONFIRMATION
    ):
        reasons = {Step17ReasonCode.HUMAN_CONFIRMATION_REQUIRED}
        if (
            routing_input.evidence_coverage_status
            is EvidenceCoverageStatus.PARTIAL
        ):
            reasons.add(Step17ReasonCode.EVIDENCE_INSUFFICIENT)
        return (
            KnowledgePolicyDecision.REQUIRE_CONFIRMATION,
            AnswerStatus.DRAFT,
            reasons,
        )
    answer_status = (
        AnswerStatus.PASS_THROUGH_RESULT
        if route.knowledge_route is KnowledgeRoute.PASS_THROUGH
        else AnswerStatus.DRAFT
    )
    return (
        KnowledgePolicyDecision.ALLOW_ANSWER,
        answer_status,
        {Step17ReasonCode.ANSWER_ALLOWED},
    )


def _execution_decision(
    route: KnowledgeRouteResult,
    context: AuthorityPolicyContext,
) -> tuple[
    ExecutionAuthorizationDecision,
    tuple[ScopeDimension, ...],
    Step17ReasonCode,
]:
    if route.knowledge_route is KnowledgeRoute.AMBIGUOUS or not context.scope_allowed:
        return (
            ExecutionAuthorizationDecision.DENY,
            (),
            Step17ReasonCode.EXECUTION_DENIED,
        )
    decision = context.execution_authorization_ceiling
    if decision is ExecutionAuthorizationDecision.ALLOW:
        reason = Step17ReasonCode.EXECUTION_ALLOWED
        scope: tuple[ScopeDimension, ...] = ()
    elif decision is ExecutionAuthorizationDecision.ALLOW_SCOPED:
        reason = Step17ReasonCode.EXECUTION_SCOPED
        scope = context.permitted_execution_scope
    elif decision is ExecutionAuthorizationDecision.REQUIRE_HUMAN:
        reason = Step17ReasonCode.EXECUTION_REQUIRES_HUMAN
        scope = ()
    else:
        reason = Step17ReasonCode.EXECUTION_DENIED
        scope = ()
    return decision, scope, reason


def evaluate_policy_gate(
    routing_input: RoutingInput,
    route: KnowledgeRouteResult,
    context: AuthorityPolicyContext,
) -> PolicyGateResult:
    """Evaluate Axis B without executing, approving, or mutating anything."""

    _validate_bindings(routing_input, route, context)
    knowledge, answer, reasons = _knowledge_decision(
        routing_input,
        route,
        context,
    )
    execution, execution_scope, execution_reason = _execution_decision(
        route,
        context,
    )
    reasons.add(execution_reason)
    return PolicyGateResult(
        request_id=routing_input.request_id,
        tenant_id=routing_input.tenant_id,
        user_id=routing_input.user_id,
        route_hash=route.route_hash,
        policy_context_hash=context.context_hash,
        evidence_status=routing_input.evidence_status,
        evidence_coverage_status=routing_input.evidence_coverage_status,
        knowledge_policy_decision=knowledge,
        execution_authorization_decision=execution,
        answer_status=answer,
        permitted_execution_scope=execution_scope,
        reason_codes=tuple(reasons),
    )


def build_hat_kernel_result(
    routing_input: RoutingInput,
    route: KnowledgeRouteResult,
    policy: PolicyGateResult,
    *,
    provenance_references: tuple[str, ...] = (),
) -> HatKernelResult:
    """Build the immutable HAT-to-Kernel data envelope."""

    if not isinstance(policy, PolicyGateResult):
        raise ContractValidationError("policy must be a PolicyGateResult")
    verify_routing_input_hash(routing_input)
    verify_route_hash(route)
    verify_policy_result_hash(policy)
    identities = (
        (routing_input.request_id, route.request_id, policy.request_id),
        (routing_input.tenant_id, route.tenant_id, policy.tenant_id),
        (routing_input.user_id, route.user_id, policy.user_id),
    )
    if any(len(set(values)) != 1 for values in identities):
        raise ContractValidationError("result identities differ")
    if policy.route_hash != route.route_hash:
        raise ContractValidationError("policy is bound to another route")
    reasons = tuple(set(route.reason_codes) | set(policy.reason_codes))
    return HatKernelResult(
        request_id=routing_input.request_id,
        tenant_id=routing_input.tenant_id,
        user_id=routing_input.user_id,
        hat_id=route.selected_hat_id,
        hat_version=route.selected_hat_version,
        selected_manifest_digest=route.selected_manifest_digest,
        knowledge_route=route.knowledge_route,
        route_hash=route.route_hash,
        requested_scope=routing_input.requested_scope,
        effective_scope=route.effective_scope,
        evidence_status=policy.evidence_status,
        evidence_coverage_status=policy.evidence_coverage_status,
        knowledge_policy_decision=policy.knowledge_policy_decision,
        execution_authorization_decision=(
            policy.execution_authorization_decision
        ),
        answer_status=policy.answer_status,
        reason_codes=reasons,
        provenance_references=provenance_references,
    )
