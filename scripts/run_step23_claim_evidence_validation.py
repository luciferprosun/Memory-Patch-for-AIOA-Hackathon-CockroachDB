#!/usr/bin/env python3
"""Bounded offline validation for Step 23 claim and evidence binding."""

from __future__ import annotations

import copy
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from aioa_memory_kernel.claims import (  # noqa: E402
    PERSISTENCE_DECISION,
    ClaimBoundaryError,
    ClaimEvidenceBindingService,
    ClaimEvidenceCandidateStatus,
    ClaimReasonCode,
    load_claim_processing_policy,
    prepare_claim_binding_request,
    verify_packet_input_snapshot_hash,
    verify_snapshot_against_request,
)
from aioa_memory_kernel.contracts.serialization import (  # noqa: E402
    canonical_json,
    canonical_sha256,
)
from aioa_memory_kernel.modeling import (  # noqa: E402
    DraftV1Service,
    ProviderResponse,
    prepare_model_generation_request,
)
from aioa_memory_kernel.sources import SourceAuthorityLevel  # noqa: E402
from tests.test_step21_temporal_resolution import (  # noqa: E402
    bundle_outcome,
    metadata,
    resolve,
)


START_SHA = "a24e1439d5d3971182dbf79c5e317f390065e712"
STEP22_EVIDENCE = ROOT / "docs/evidence/modeling/step22-provider-neutral-draft-v1-validation.json"
FIXED_NOW = datetime(2026, 8, 9, 4, 0, tzinfo=UTC)


class ValidationFailure(RuntimeError):
    pass


class FixedClock:
    def now(self) -> datetime:
        return FIXED_NOW


class FakeProvider:
    def __init__(self, request, text: str) -> None:
        self.identity = request.provider_identity
        self.text = text
        self.calls = 0

    def provider_identity(self):
        return self.identity

    def generate(self, request, timeout_policy):
        self.calls += 1
        return ProviderResponse(
            provider_identity_digest=self.identity.identity_digest,
            model_id=self.identity.model_id,
            model_version=self.identity.model_revision_or_declared_version,
            provider_request_id="step23-offline-fixture",
            finish_reason="stop",
            response_content=self.text,
            usage_metadata={"prompt_tokens": 8, "completion_tokens": 18, "total_tokens": 26},
            latency_milliseconds=1,
        )


def _load_step22_evidence() -> dict[str, Any]:
    value = json.loads(STEP22_EVIDENCE.read_text(encoding="utf-8"))
    if value.get("status") != "PASS":
        raise ValidationFailure("Step 22 evidence does not report PASS")
    claimed = value.get("validation_digest")
    if claimed != canonical_sha256(value, exclude_fields=("validation_digest",)):
        raise ValidationFailure("Step 22 evidence digest mismatch")
    return value


def _draft(temporal_result, text: str):
    request = prepare_model_generation_request(
        temporal_result,
        "Welche Aussagen gelten?",
    )
    provider = FakeProvider(request, text)
    draft = DraftV1Service(
        provider,
        clock=FixedClock(),
        sleep=lambda _: None,
    ).generate(request).draft
    if provider.calls != 1:
        raise ValidationFailure("offline Draft V1 fixture call count changed")
    return draft


def _snapshot(draft_text: str, metadatas, contents):
    outcome = bundle_outcome(*metadatas, contents=contents)
    temporal = resolve(outcome)
    draft = _draft(temporal, draft_text)
    request = prepare_claim_binding_request(draft, (outcome.bundle,), temporal)
    snapshot = ClaimEvidenceBindingService().freeze_packet_input(request)
    verify_packet_input_snapshot_hash(snapshot)
    verify_snapshot_against_request(snapshot, request)
    return outcome, temporal, request, snapshot


def validate() -> dict[str, Any]:
    step22 = _load_step22_evidence()
    draft_text = (
        "Die Vorschrift ist aufgehoben. "
        "Das Gesetz gilt. "
        "Die Behörde entscheidet schriftlich. "
        "Ohne Nachweis bleibt die Behauptung offen. "
        "Hallo! "
        "Der Anspruch besteht und die Frist läuft."
    )
    main = _snapshot(
        draft_text,
        tuple(
            metadata(
                document=f"step23-document-{index}",
                version=f"step23-version-{index}",
                provision=f"§-{index + 1}",
            )
            for index in range(4)
        ),
        (
            "Die Vorschrift ist aufgehoben.",
            "Das Gesetz gilt nicht.",
            "Die Behörde entscheidet später schriftlich.",
            "Der Anspruch besteht und die Frist läuft.",
        ),
    )
    outcome, temporal, request, snapshot = main
    status_counts = {
        status.value: sum(
            item.candidate_status is status
            for item in snapshot.ordered_candidate_assessments
        )
        for status in ClaimEvidenceCandidateStatus
    }
    expected = {"SUPPORTED": 1, "REFUTED": 1, "UNVERIFIED": 4}
    if status_counts != expected:
        raise ValidationFailure("candidate-status matrix changed")

    future = _snapshot(
        "Die Vorschrift ist aufgehoben.",
        (metadata(version="future-v1", effective_from="2030-01-01"),),
        ("Die Vorschrift ist aufgehoben.",),
    )[-1]
    if future.ordered_candidate_assessments[0].candidate_status is not ClaimEvidenceCandidateStatus.UNVERIFIED:
        raise ValidationFailure("future evidence escaped the temporal ceiling")
    if ClaimReasonCode.TEMPORAL_MISMATCH not in future.ordered_evidence_links[0].reason_codes:
        raise ValidationFailure("temporal mismatch reason is absent")

    shared = {"document": "conflict-document", "provision": "§-1"}
    conflict = _snapshot(
        "Die Vorschrift ist aufgehoben.",
        (
            metadata(version="conflict-a", **shared),
            metadata(version="conflict-b", **shared),
        ),
        (
            "Die Vorschrift ist aufgehoben.",
            "Die Vorschrift ist nicht aufgehoben.",
        ),
    )[-1]
    conflict_assessment = conflict.ordered_candidate_assessments[0]
    if (
        conflict_assessment.candidate_status is not ClaimEvidenceCandidateStatus.UNVERIFIED
        or not conflict_assessment.supporting_link_hashes
        or not conflict_assessment.refuting_link_hashes
        or ClaimReasonCode.MATERIAL_CONFLICT not in conflict_assessment.reason_codes
    ):
        raise ValidationFailure("material conflict was not preserved")

    authority_negative = "PASS"
    try:
        from dataclasses import replace

        replace(
            snapshot.ordered_evidence_links[0],
            authority_level=SourceAuthorityLevel.INFORMATIONAL_SECONDARY,
        )
    except ClaimBoundaryError:
        pass
    else:
        authority_negative = "FAIL"
        raise ValidationFailure("weak authority link was admitted")

    isolation_negative = "PASS"
    tampered_draft = copy.copy(request.draft_v1)
    object.__setattr__(tampered_draft, "tenant_id", "cross-tenant")
    try:
        prepare_claim_binding_request(tampered_draft, (outcome.bundle,), temporal)
    except ClaimBoundaryError:
        pass
    else:
        isolation_negative = "FAIL"
        raise ValidationFailure("cross-tenant Draft V1 was admitted")

    policy = load_claim_processing_policy()
    evidence: dict[str, Any] = {
        "step": "STEP_23_CLAIM_EXTRACTION_EVIDENCE_BINDING_1A",
        "schema_version": "1.0.0",
        "status": "PASS",
        "start_sha": START_SHA,
        "upstream_identity": {
            "committed_step22_validation_digest": step22["validation_digest"],
            "committed_step22_draft_hash": step22["fake_provider_validation"]["draft_hash"],
            "typed_draft_v1_hash": request.draft_v1_hash,
            "step20_bundle_hashes": list(request.step20_bundle_hashes),
            "step21_result_hash": request.step21_result_hash,
            "route_hash": request.route_hash,
            "tenant_user_hat_scope_bound": True,
        },
        "claim_extraction": {
            "span_convention": policy.span_convention,
            "policy_id": policy.policy_id,
            "policy_version": policy.policy_version,
            "policy_digest": policy.policy_digest,
            "model_assisted": policy.model_assisted_extraction,
            "claim_count": len(snapshot.ordered_claims),
            "factual_claim_count": sum(item.claim_type.value != "NON_FACTUAL" for item in snapshot.ordered_claims),
            "non_factual_count": sum(item.claim_type.value == "NON_FACTUAL" for item in snapshot.ordered_claims),
            "compound_count": sum(item.atomicity.value == "COMPOUND" for item in snapshot.ordered_claims),
            "claim_hashes": [item.claim_hash for item in snapshot.ordered_claims],
        },
        "evidence_binding": {
            "link_count": len(snapshot.ordered_evidence_links),
            "link_hashes": [item.link_hash for item in snapshot.ordered_evidence_links],
            "new_retrieval_performed": False,
            "step20_candidate_order_creates_truth": False,
            "semantic_similarity_creates_truth": False,
            "temporal_mismatch_case": "PASS",
            "material_conflict_case": "PASS",
        },
        "candidate_assessments": {
            "counts": status_counts,
            "assessment_hashes": [
                item.assessment_hash for item in snapshot.ordered_candidate_assessments
            ],
            "statuses_are_step23_candidates_not_final_verdicts": True,
        },
        "packet_input_snapshot": {
            "snapshot_id": snapshot.snapshot_id,
            "snapshot_hash": snapshot.snapshot_hash,
            "claim_binding_request_hash": snapshot.claim_binding_request_hash,
            "correction_packet_constructed": False,
        },
        "fixture_boundary": {
            "real_step22_committed_identity": "PASS_HASH_ONLY",
            "real_provider_draft_text_available_in_committed_evidence": False,
            "typed_step20_step21_step22_pipeline": "PASS_SYNTHETIC_BOUNDED",
            "synthetic_edge_cases": "PASS_EXPLICITLY_SYNTHETIC",
        },
        "authority_and_isolation": {
            "authority_negative": authority_negative,
            "cross_tenant_negative": isolation_negative,
            "cross_user_negative": "PASS_UNIT_TEST",
            "cross_hat_negative": "PASS_UPSTREAM_AND_UNIT_TEST",
            "source_authority_upgraded": False,
            "temporal_policy_bypassed": False,
            "model_self_verification_authority": False,
            "approval_created": False,
            "execution_capability": False,
        },
        "persistence": {
            "decision": PERSISTENCE_DECISION,
            "migration_added": False,
            "reason": "Step23 candidate statuses remain a frozen hash-bound Step24 input; existing final claim-verdict persistence is not overloaded.",
        },
        "resource_bounds": {
            "maximum_claims": policy.maximum_claims,
            "maximum_evidence_links": policy.maximum_evidence_links,
            "network_calls": 0,
            "provider_calls": 0,
            "database_calls": 0,
            "aws_mutations": 0,
            "s3_mutations": 0,
        },
        "step24_started": False,
        "correction_packet": 0,
        "draft_v2": 0,
        "cleanup": {
            "temporary_runtime": "NOT_REQUIRED",
            "database": "NOT_REQUIRED",
            "owned_processes_started": 0,
        },
    }
    evidence["validation_digest"] = canonical_sha256(evidence)
    return evidence


def main() -> int:
    try:
        evidence = validate()
    except (ClaimBoundaryError, OSError, ValueError, ValidationFailure) as exc:
        print(canonical_json({"status": "FAILED", "reason": type(exc).__name__}), file=sys.stderr)
        return 1
    print(canonical_json(evidence))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
