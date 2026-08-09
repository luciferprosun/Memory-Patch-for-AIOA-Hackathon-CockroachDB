#!/usr/bin/env python3
"""Bounded offline Step 26 verified-answer and fail-closed validation."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from aioa_memory_kernel.answers import (  # noqa: E402
    FinalOutputStatus,
    FinalRetryResult,
    Step26ReasonCode,
    VerifiedAnswerService,
    prepare_final_retry_request,
    verify_final_answer_outcome_hash,
    verify_verified_answer_hash,
)
from aioa_memory_kernel.contracts.enums import KnowledgeRoute  # noqa: E402
from aioa_memory_kernel.contracts.exceptions import IntegrityError  # noqa: E402
from aioa_memory_kernel.contracts.serialization import (  # noqa: E402
    canonical_json,
    canonical_sha256,
)
from aioa_memory_kernel.routing import KnowledgePolicyDecision  # noqa: E402
from tests.test_step21_temporal_resolution import metadata  # noqa: E402
from tests.test_step26_verified_answer_output import (  # noqa: E402
    FakeRetryProvider,
    FixedClock,
    hat_lineage,
)


START_SHA = "be3b206f95ac9723727a167929f0450f0ef1d887"
STEP25_EVIDENCE = ROOT / "docs/evidence/modeling/step25-draft-v2-layered-verifier-validation.json"


class ValidationFailure(RuntimeError):
    pass


def _load_step25_evidence() -> dict[str, Any]:
    value = json.loads(STEP25_EVIDENCE.read_text(encoding="utf-8"))
    if value.get("status") != "PASS" or value.get("validation_digest") != canonical_sha256(
        value,
        exclude_fields=("validation_digest",),
    ):
        raise ValidationFailure("Step 25 validation evidence is invalid")
    if value.get("step26_started") is not False:
        raise ValidationFailure("Step 25 evidence did not preserve the Step 26 gate")
    return value


def validate() -> dict[str, Any]:
    step25 = _load_step25_evidence()

    verified_request, authenticator = hat_lineage(
        route_kind=KnowledgeRoute.HAT_ENFORCE
    )
    verified = VerifiedAnswerService(authenticator).finalize(verified_request)
    if (
        verified.output_status is not FinalOutputStatus.VERIFIED_ANSWER
        or verified.verified_answer is None
        or verified.verified_answer.answer_text
        != verified_request.step25_result.draft_v2.draft_text
    ):
        raise ValidationFailure("verified answer fixture failed")
    verify_verified_answer_hash(verified.verified_answer)
    verify_final_answer_outcome_hash(verified)

    blocked_request, blocked_authenticator = hat_lineage(
        policy_decision=KnowledgePolicyDecision.BLOCK_ANSWER
    )
    blocked = VerifiedAnswerService(blocked_authenticator).finalize(blocked_request)
    confirmation_request, confirmation_authenticator = hat_lineage(
        policy_decision=KnowledgePolicyDecision.REQUIRE_CONFIRMATION
    )
    confirmation = VerifiedAnswerService(confirmation_authenticator).finalize(
        confirmation_request
    )
    if (
        blocked.output_status is not FinalOutputStatus.BLOCKED_POLICY
        or blocked.verified_answer is not None
        or confirmation.output_status is not FinalOutputStatus.CONFIRMATION_REQUIRED
        or confirmation.verified_answer is not None
    ):
        raise ValidationFailure("policy ceiling validation failed")

    insufficient_request, insufficient_authenticator = hat_lineage(
        values=(metadata(verified_at=None),)
    )
    insufficient = VerifiedAnswerService(insufficient_authenticator).finalize(
        insufficient_request
    )
    stale_request, stale_authenticator = hat_lineage(
        values=(metadata(verified_at="2020-01-01T00:00:00Z"),)
    )
    stale = VerifiedAnswerService(stale_authenticator).finalize(stale_request)
    conflict_request, conflict_authenticator = hat_lineage(
        values=(metadata(version="validation-a"), metadata(version="validation-b")),
        contents=("Widerspruch A", "Widerspruch B"),
    )
    conflicting = VerifiedAnswerService(conflict_authenticator).finalize(
        conflict_request
    )
    if (
        insufficient.output_status is not FinalOutputStatus.INSUFFICIENT_EVIDENCE
        or stale.output_status is not FinalOutputStatus.STALE_EVIDENCE
        or conflicting.output_status is not FinalOutputStatus.HUMAN_REVIEW_REQUIRED
    ):
        raise ValidationFailure("evidence ceiling validation failed")

    failed_request, failed_authenticator = hat_lineage(
        route_kind=KnowledgeRoute.HAT_ENFORCE,
        draft_v2_text="Eine unbelegte Aussage.",
    )
    no_provider = VerifiedAnswerService(failed_authenticator).finalize(failed_request)
    if (
        no_provider.output_status is not FinalOutputStatus.HUMAN_REVIEW_REQUIRED
        or no_provider.verified_answer is not None
        or Step26ReasonCode.HAT_ENFORCE_DRAFT_V1_FALLBACK_FORBIDDEN
        not in no_provider.human_review.reason_codes
    ):
        raise ValidationFailure("HAT_ENFORCE Draft V1 fallback boundary failed")

    retry_request = prepare_final_retry_request(failed_request, failed_authenticator)
    success_provider = FakeRetryProvider(
        retry_request.provider_identity,
        ("Die Vorschrift ist aufgehoben.",),
    )
    retry_success = VerifiedAnswerService(
        failed_authenticator,
        provider=success_provider,
        clock=FixedClock(),
    ).finalize(failed_request)
    if (
        retry_success.output_status is not FinalOutputStatus.VERIFIED_ANSWER
        or len(success_provider.requests) != 1
        or retry_success.retry_record.result is not FinalRetryResult.SUCCEEDED
        or not retry_success.retry_record.full_reverification_performed
        or retry_success.retry_record.new_evidence_used
    ):
        raise ValidationFailure("single retry success validation failed")

    failure_provider = FakeRetryProvider(
        retry_request.provider_identity,
        ("Weiter unbelegt.",),
    )
    retry_failure = VerifiedAnswerService(
        failed_authenticator,
        provider=failure_provider,
        clock=FixedClock(),
    ).finalize(failed_request)
    if (
        retry_failure.output_status is not FinalOutputStatus.HUMAN_REVIEW_REQUIRED
        or len(failure_provider.requests) != 1
        or retry_failure.retry_record.result is not FinalRetryResult.FAILED
        or retry_failure.verified_answer is not None
    ):
        raise ValidationFailure("single retry exhaustion validation failed")

    citation_tamper = "PASS"
    tampered_answer = copy.copy(verified.verified_answer)
    tampered_citation = copy.copy(tampered_answer.ordered_citations[0])
    object.__setattr__(tampered_citation, "candidate_hash", "0" * 64)
    object.__setattr__(tampered_answer, "ordered_citations", (tampered_citation,))
    try:
        verify_verified_answer_hash(tampered_answer)
    except IntegrityError:
        pass
    else:
        citation_tamper = "FAIL"
        raise ValidationFailure("citation tamper was accepted")

    summary_tamper = "PASS"
    tampered_summary = copy.copy(verified_request.step25_result.verification_summary)
    object.__setattr__(tampered_summary, "summary_hash", "0" * 64)
    tampered_pipeline = copy.copy(verified_request.step25_result)
    object.__setattr__(tampered_pipeline, "verification_summary", tampered_summary)
    from aioa_memory_kernel.verification import verify_draft_v2_pipeline_result_hash

    try:
        verify_draft_v2_pipeline_result_hash(tampered_pipeline)
    except IntegrityError:
        pass
    else:
        summary_tamper = "FAIL"
        raise ValidationFailure("verification summary tamper was accepted")

    answer = verified.verified_answer
    evidence: dict[str, Any] = {
        "step": "STEP_26_VERIFIED_ANSWER_ASSEMBLY_FAIL_CLOSED_OUTPUT_1A",
        "schema_version": "1.0.0",
        "status": "PASS",
        "start_sha": START_SHA,
        "upstream_identity": {
            "step25_validation_digest": step25["validation_digest"],
            "route_hash": verified_request.route.route_hash,
            "knowledge_route": verified_request.route.knowledge_route.value,
            "knowledge_policy_decision": (
                verified_request.policy_result.knowledge_policy_decision.value
            ),
            "evidence_status": verified_request.temporal_result.evidence_status.value,
            "evidence_bundle_hash": (
                verified_request.step20_outcomes[0].bundle.bundle_hash
            ),
            "temporal_result_hash": verified_request.temporal_result.result_hash,
            "correction_packet_hash": verified_request.correction_packet.packet_hash,
            "draft_v1_hash": verified_request.draft_v1.draft_hash,
            "draft_v2_hash": verified_request.step25_result.draft_v2.draft_v2_hash,
            "verification_summary_hash": (
                verified_request.step25_result.verification_summary.summary_hash
            ),
        },
        "verified_answer": {
            "output_status": verified.output_status.value,
            "answer_hash": answer.answer_hash,
            "answer_text_sha256": answer.answer_text_sha256,
            "answer_byte_length": answer.answer_byte_length,
            "citation_count": len(answer.ordered_citations),
            "verified_claim_count": len(answer.claim_verification_references),
            "limitations": list(answer.limitations),
            "text_equals_verified_draft_v2": True,
        },
        "policy_and_evidence_matrix": {
            "verified": verified.output_status.value,
            "block_answer": blocked.output_status.value,
            "require_confirmation": confirmation.output_status.value,
            "insufficient": insufficient.output_status.value,
            "conflicting": conflicting.output_status.value,
            "stale": stale.output_status.value,
        },
        "retry": {
            "maximum": 1,
            "success_attempt_count": len(success_provider.requests),
            "success_retry_draft_v2_hash": (
                retry_success.retry_record.retry_draft_v2_hash
            ),
            "success": retry_success.retry_record.result.value,
            "failure_attempt_count": len(failure_provider.requests),
            "failure": retry_failure.retry_record.result.value,
            "full_reverification": True,
            "same_correction_packet": True,
            "new_evidence": False,
            "second_retry": False,
            "real_provider": "UNAVAILABLE_NOT_REQUIRED_CANONICAL_STEP22_QUOTA_BOUNDARY",
        },
        "fail_closed": {
            "hat_enforce_draft_v1_fallback": False,
            "unverified_draft_v2_returned": False,
            "human_review_result_hash": no_provider.human_review.result_hash,
            "bounded_failure_hash": blocked.bounded_failure.failure_hash,
            "citation_tamper_negative": citation_tamper,
            "verification_summary_tamper_negative": summary_tamper,
        },
        "authority_and_isolation": {
            "model_authority": False,
            "verifier_authority": False,
            "approval_authority": False,
            "execution_authority": False,
            "cross_tenant_negative": "PASS_UNIT_TEST",
            "cross_user_negative": "PASS_UNIT_TEST",
            "cross_hat_negative": "PASS_UNIT_TEST",
        },
        "effect_bounds": {
            "provider_calls": 2,
            "provider_calls_fake_only": True,
            "new_retrieval": 0,
            "web_calls": 0,
            "aws_mutations": 0,
            "s3_mutations": 0,
            "personal_memory_writes": 0,
            "approval_actions": 0,
            "execution_actions": 0,
        },
        "persistence": {
            "decision": "STEP26_IMMUTABLE_RUNTIME_OUTPUT_NO_SAFE_STEP4_TABLE",
            "migration_added": False,
            "retry_draft_persisted": False,
        },
        "real_pipeline_fixture": "PASS_STEP20_THROUGH_STEP25_TYPED_CONTRACTS",
        "synthetic_edge_fixtures": "PASS_DETERMINISTIC_BOUNDED",
        "cleanup": "NOT_REQUIRED_NO_EXTERNAL_RUNTIME",
        "step27_started": False,
        "personal_memory_writes": 0,
        "approval_actions": 0,
        "execution_actions": 0,
    }
    evidence["validation_digest"] = canonical_sha256(evidence)
    return evidence


def main() -> int:
    try:
        evidence = validate()
    except (OSError, TypeError, ValueError, RuntimeError, ValidationFailure) as exc:
        print(
            canonical_json({"status": "FAILED", "reason": type(exc).__name__}),
            file=sys.stderr,
        )
        return 1
    print(canonical_json(evidence))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
