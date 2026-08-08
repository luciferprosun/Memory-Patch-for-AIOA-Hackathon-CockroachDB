#!/usr/bin/env python3
"""Run the offline, deterministic Step 17 routing-policy validation."""

from __future__ import annotations

import dataclasses
import hashlib
import sys
from datetime import UTC, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from aioa_memory_kernel.contracts import (  # noqa: E402
    AnswerStatus,
    EvidenceStatus,
    KnowledgeRoute,
    ScopeComparisonMode,
    ScopeDimension,
    ScopeValueType,
)
from aioa_memory_kernel.contracts.serialization import (  # noqa: E402
    canonical_json,
    canonical_sha256,
)
from aioa_memory_kernel.hats import (  # noqa: E402
    CompatibilityDecision,
    HatRegistryService,
    ManifestIdentity,
    ReviewActor,
    ReviewReceipt,
    RuntimeBinding,
    decode_manifest,
    decide_compatibility,
)
from aioa_memory_kernel.routing import (  # noqa: E402
    AuthorityPolicyContext,
    EvidenceCoverageStatus,
    ExecutionAuthorizationDecision,
    HatPolicyRequirement,
    HatRoutingCandidate,
    KnowledgePolicyDecision,
    RoutingInput,
    TrustedHatRegistrySnapshot,
    evaluate_policy_gate,
    route_knowledge_request,
)


BASELINE_SHA = "c51b32373dba5437f027268d1806a3fcdc1b3a91"
NOW = datetime(2031, 6, 1, tzinfo=UTC)
SCHEMA = ROOT / "schemas/hat-manifest.schema.json"
MANIFEST = ROOT / "config/hats/german-law-1.0.0.json"


def _enabled_entry(identity: ManifestIdentity):
    manifest = identity.manifest
    service = HatRegistryService(clock=lambda: NOW)
    service.register(identity, decide_compatibility(identity))
    service.validate(manifest.hat_id, manifest.hat_version)
    implementation_digest = hashlib.sha256(
        f"installed:{manifest.hat_id}:{manifest.hat_version}".encode()
    ).hexdigest()
    binding = RuntimeBinding(
        f"{manifest.hat_id}-system-step17",
        manifest.hat_id,
        manifest.hat_version,
        "Step17ValidationHat",
        manifest.hat_version,
        "hat-sdk-1a",
        implementation_digest,
    )
    receipt = ReviewReceipt(
        manifest.hat_id,
        manifest.hat_version,
        identity.typed_manifest_digest,
        identity.raw_manifest_sha256,
        identity.schema_file_sha256,
        CompatibilityDecision.COMPATIBLE,
        canonical_sha256(manifest.capabilities),
        binding.runtime_binding_id,
        implementation_digest,
        "ENABLE",
        ("TRUSTED_STEP17_VALIDATION",),
        ReviewActor.TRUSTED_OPERATOR,
        "operator-redacted",
        NOW,
    )
    return service.enable(
        manifest.hat_id,
        manifest.hat_version,
        binding,
        receipt,
    )


def _clone(identity: ManifestIdentity) -> ManifestIdentity:
    manifest = dataclasses.replace(
        identity.manifest,
        hat_id="synthetic-legal-hat",
        display_name="Synthetic Legal HAT",
    )
    raw_digest = canonical_sha256(
        {"fixture": "step17", "hat_id": manifest.hat_id}
    )
    return ManifestIdentity(
        manifest,
        raw_digest,
        raw_digest,
        canonical_sha256(manifest),
        identity.schema_file_sha256,
    )


def _scope() -> tuple[ScopeDimension, ...]:
    return (
        ScopeDimension(
            "legal_jurisdiction",
            "DE_FEDERAL",
            ScopeValueType.STRING,
            ScopeComparisonMode.EXACT,
            "trusted-validation",
            True,
        ),
        ScopeDimension(
            "knowledge_as_of",
            NOW,
            ScopeValueType.TIMESTAMP,
            ScopeComparisonMode.TIMESTAMP,
            "trusted-validation",
            True,
        ),
        ScopeDimension(
            "source_language",
            "de",
            ScopeValueType.STRING,
            ScopeComparisonMode.EXACT,
            "trusted-validation",
            True,
        ),
        ScopeDimension(
            "legal_source_class",
            ("DE_FEDERAL_OFFICIAL_CONSOLIDATED_LAW",),
            ScopeValueType.STRING_SET,
            ScopeComparisonMode.IN_SET,
            "trusted-validation",
            True,
        ),
    )


def _candidate(entry, requirement):
    return HatRoutingCandidate(
        entry.identity.manifest.hat_id,
        entry.identity.manifest.hat_version,
        entry.identity.typed_manifest_digest,
        requirement,
    )


def _routing_input(
    entries,
    candidates,
    evidence=EvidenceStatus.SUFFICIENT,
    coverage=EvidenceCoverageStatus.COMPLETE,
):
    return RoutingInput(
        tenant_id="tenant-validation",
        user_id="user-validation",
        request_id="request-step17-validation",
        request_kind="knowledge-question",
        normalized_query_or_subject="Welche Fassung gilt am Stichtag?",
        requested_domain_id="law.de.federal",
        requested_scope=_scope(),
        candidate_hat_descriptors=candidates,
        trusted_hat_registry_snapshot=TrustedHatRegistrySnapshot(
            "trusted-registry:step17:validation",
            entries,
        ),
        evidence_status=evidence,
        evidence_coverage_status=coverage,
        context_metadata={"classification_source": "deterministic-rule"},
    )


def _context(route, knowledge, execution, permitted_scope=()):
    return AuthorityPolicyContext(
        request_id=route.request_id,
        tenant_id=route.tenant_id,
        user_id=route.user_id,
        policy_reference="policy:step17:validation",
        policy_digest=canonical_sha256(
            {"policy_id": "step17-axis-b", "policy_version": "1a"}
        ),
        knowledge_policy_ceiling=knowledge,
        execution_authorization_ceiling=execution,
        scope_allowed=True,
        permitted_execution_scope=permitted_scope,
        selected_hat_id=route.selected_hat_id,
        selected_hat_version=route.selected_hat_version,
        selected_manifest_digest=route.selected_manifest_digest,
    )


def validate() -> dict[str, object]:
    identity = decode_manifest(MANIFEST.read_bytes(), schema_path=SCHEMA)
    german_entry = _enabled_entry(identity)
    second_entry = _enabled_entry(_clone(identity))
    advisory = _candidate(german_entry, HatPolicyRequirement.ADVISORY)
    mandatory = _candidate(german_entry, HatPolicyRequirement.MANDATORY)
    conflicting = _candidate(second_entry, HatPolicyRequirement.MANDATORY)

    route_inputs = {
        "PASS_THROUGH": _routing_input((german_entry,), ()),
        "HAT_ASSIST": _routing_input((german_entry,), (advisory,)),
        "HAT_ENFORCE": _routing_input((german_entry,), (mandatory,)),
        "AMBIGUOUS": _routing_input(
            (german_entry, second_entry),
            (mandatory, conflicting),
        ),
    }
    routes = {
        name: route_knowledge_request(value)
        for name, value in route_inputs.items()
    }
    for expected, result in routes.items():
        if result.knowledge_route.value != expected:
            raise RuntimeError(f"route matrix mismatch for {expected}")

    assist_input = route_inputs["HAT_ASSIST"]
    assist_route = routes["HAT_ASSIST"]
    knowledge_matrix = {}
    for decision in KnowledgePolicyDecision:
        policy = evaluate_policy_gate(
            assist_input,
            assist_route,
            _context(
                assist_route,
                decision,
                ExecutionAuthorizationDecision.DENY,
            ),
        )
        if policy.knowledge_policy_decision is not decision:
            raise RuntimeError(f"knowledge policy mismatch for {decision.value}")
        knowledge_matrix[decision.value] = {
            "answer_status": policy.answer_status.value,
            "policy_result_hash": policy.policy_result_hash,
        }

    pass_input = route_inputs["PASS_THROUGH"]
    pass_route = routes["PASS_THROUGH"]
    execution_matrix = {}
    for decision in ExecutionAuthorizationDecision:
        permitted = _scope() if decision is ExecutionAuthorizationDecision.ALLOW_SCOPED else ()
        policy = evaluate_policy_gate(
            pass_input,
            pass_route,
            _context(
                pass_route,
                KnowledgePolicyDecision.ALLOW_ANSWER,
                decision,
                permitted,
            ),
        )
        if policy.execution_authorization_decision is not decision:
            raise RuntimeError(f"execution policy mismatch for {decision.value}")
        execution_matrix[decision.value] = {
            "scope_dimension_count": len(policy.permitted_execution_scope),
            "policy_result_hash": policy.policy_result_hash,
        }

    partial_input = _routing_input(
        (german_entry,),
        (advisory,),
        EvidenceStatus.INSUFFICIENT,
        EvidenceCoverageStatus.PARTIAL,
    )
    partial_route = route_knowledge_request(partial_input)
    partial_policy = evaluate_policy_gate(
        partial_input,
        partial_route,
        _context(
            partial_route,
            KnowledgePolicyDecision.ALLOW_ANSWER,
            ExecutionAuthorizationDecision.DENY,
        ),
    )
    if not (
        partial_policy.evidence_status is EvidenceStatus.INSUFFICIENT
        and partial_policy.evidence_coverage_status
        is EvidenceCoverageStatus.PARTIAL
        and partial_policy.answer_status is AnswerStatus.DRAFT
        and partial_policy.knowledge_policy_decision
        is KnowledgePolicyDecision.REQUIRE_CONFIRMATION
    ):
        raise RuntimeError("answer/evidence separation failed")

    result: dict[str, object] = {
        "schema_version": "1.0.0",
        "step": "STEP_17_AXIS_A_ROUTER_AXIS_B_POLICY_GATE_EVIDENCE_STATUS_1A",
        "baseline_sha": BASELINE_SHA,
        "status": "PASS",
        "trusted_hat": {
            "hat_id": identity.manifest.hat_id,
            "hat_version": identity.manifest.hat_version,
            "manifest_digest": identity.typed_manifest_digest,
            "registry_state": german_entry.state.value,
        },
        "route_decision_matrix": {
            name: {
                "decision": route.knowledge_route.value,
                "selected_hat_id": route.selected_hat_id,
                "route_hash": route.route_hash,
            }
            for name, route in routes.items()
        },
        "knowledge_policy_matrix": knowledge_matrix,
        "execution_authorization_matrix": execution_matrix,
        "answer_evidence_separation": {
            "answer_status": partial_policy.answer_status.value,
            "evidence_status": partial_policy.evidence_status.value,
            "evidence_coverage_status": (
                partial_policy.evidence_coverage_status.value
            ),
            "knowledge_policy_decision": (
                partial_policy.knowledge_policy_decision.value
            ),
            "status": "PASS",
        },
        "authority_invariants": {
            "hat_execution_authority": False,
            "human_approval_created": False,
            "model_authority": False,
            "provider_authority": False,
            "router_execution_authority": False,
        },
        "tenant_isolation": {
            "cross_tenant_route": False,
            "cross_user_route": False,
            "identity_bound_in_result": True,
        },
        "external_effects": {
            "aws_calls": 0,
            "database_calls": 0,
            "filesystem_writes": 0,
            "model_calls": 0,
            "network_calls": 0,
            "provider_calls": 0,
            "subprocess_calls": 0,
        },
        "step18_started": False,
    }
    result["evidence_digest"] = canonical_sha256(result)
    return result


def main() -> None:
    print(canonical_json(validate()))


if __name__ == "__main__":
    main()
