#!/usr/bin/env python3
"""Bounded offline Step 25 Draft V2 and layered-verifier validation."""

from __future__ import annotations

import copy
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from aioa_memory_kernel.contracts.serialization import canonical_json, canonical_sha256  # noqa: E402
from aioa_memory_kernel.corrections import (  # noqa: E402
    HmacSha256PacketAuthenticator,
    build_correction_packet,
)
from aioa_memory_kernel.modeling import ProviderResponse  # noqa: E402
from aioa_memory_kernel.verification import (  # noqa: E402
    CheckResult,
    DeterministicFakeSemanticVerifier,
    DraftV2LayeredVerifier,
    DraftV2Service,
    FinalStep25ClaimVerdict,
    SemanticCandidateVerdict,
    Step25BoundaryError,
    VerificationSummaryStatus,
    prepare_draft_v2_generation_request,
    verify_draft_v2_hash,
    verify_verification_summary_hash,
)
from tests.test_step21_temporal_resolution import metadata  # noqa: E402
from tests.test_step23_claim_evidence_binding import pipeline  # noqa: E402


START_SHA = "92e723d8e2770c9862f82c725c3418aed31b1f1b"
STEP24_EVIDENCE = ROOT / "docs/evidence/modeling/step24-correction-packet-validation.json"
FIXED_NOW = datetime(2026, 8, 9, 8, 0, tzinfo=UTC)
VALIDATION_KEY_ID = "step25-non-production-validation-key"


class ValidationFailure(RuntimeError):
    pass


class FixedClock:
    def now(self):
        return FIXED_NOW


class FakeProvider:
    def __init__(self, request, text: str) -> None:
        self.identity = request.provider_identity
        self.text = text
        self.calls = 0
        self.requests = []

    def provider_identity(self):
        return self.identity

    def generate(self, request, timeout_policy):
        self.calls += 1
        self.requests.append(request)
        return ProviderResponse(
            provider_identity_digest=self.identity.identity_digest,
            model_id=self.identity.model_id,
            model_version=self.identity.model_revision_or_declared_version,
            provider_request_id=f"step25-offline-{self.calls}",
            finish_reason="stop",
            response_content=self.text,
            usage_metadata={"prompt_tokens": 20, "completion_tokens": 12, "total_tokens": 32},
            latency_milliseconds=1,
        )


class MemoryStore:
    def __init__(self):
        self.values = {}

    def load(self, *, tenant_id, user_id, draft_id):
        return self.values.get((tenant_id, user_id, draft_id))

    def put(self, draft):
        key = (draft.tenant_id, draft.user_id, draft.draft_v2_id)
        existing = self.values.get(key)
        if existing is not None and existing.draft_v2_hash != draft.draft_v2_hash:
            raise ValidationFailure("Draft V2 replay conflict")
        self.values[key] = draft
        return draft


def _load_step24_evidence() -> dict[str, Any]:
    value = json.loads(STEP24_EVIDENCE.read_text(encoding="utf-8"))
    if value.get("status") != "PASS" or value.get("validation_digest") != canonical_sha256(
        value,
        exclude_fields=("validation_digest",),
    ):
        raise ValidationFailure("Step 24 validation evidence is invalid")
    if value.get("step25_started") is not False:
        raise ValidationFailure("Step 24 evidence did not preserve the Step 25 gate")
    return value


def _inputs(text: str, *, contents, metadatas=None):
    values = pipeline(text, contents=contents, metadatas=metadatas)
    return values[2], build_correction_packet(values[-1])


def _run(draft, packet, text, semantic, *, store=None):
    authenticator = HmacSha256PacketAuthenticator(
        key_id=VALIDATION_KEY_ID,
        key_material=bytes(range(32)),
    )
    receipt = authenticator.authenticate(packet)
    request = prepare_draft_v2_generation_request(
        draft,
        packet,
        receipt,
        authenticator,
    )
    provider = FakeProvider(request, text)
    result = DraftV2Service(
        provider,
        authenticator,
        verifier=DraftV2LayeredVerifier(semantic),
        store=store,
        clock=FixedClock(),
        sleep=lambda _: None,
    ).generate_and_verify(request)
    verify_draft_v2_hash(result.draft_v2)
    verify_verification_summary_hash(result.verification_summary)
    return result, provider, request, authenticator, receipt


def validate() -> dict[str, Any]:
    step24 = _load_step24_evidence()
    main_draft, main_packet = _inputs(
        "Die Vorschrift ist aufgehoben. Das Gesetz gilt. Eine unbelegte Aussage. Hallo!",
        contents=(
            "Die Vorschrift ist aufgehoben.",
            "Das Gesetz gilt nicht.",
            "Anderer Inhalt.",
        ),
    )
    supporting = next(
        item for item in main_packet.ordered_citations if item.relation.value == "SUPPORTS"
    )
    main_text = (
        f"Die Vorschrift ist aufgehoben [citation:{supporting.citation_id}]. "
        "Eine unbelegte Aussage ist nicht verifiziert. Hallo!"
    )
    store = MemoryStore()
    main, provider, request, authenticator, receipt = _run(
        main_draft,
        main_packet,
        main_text,
        DeterministicFakeSemanticVerifier(),
        store=store,
    )
    replay_provider = FakeProvider(request, "must-not-run")
    replay = DraftV2Service(
        replay_provider,
        authenticator,
        verifier=DraftV2LayeredVerifier(DeterministicFakeSemanticVerifier()),
        store=store,
        clock=FixedClock(),
    ).generate_and_verify(request)
    if (
        provider.calls != 1
        or replay_provider.calls != 0
        or not replay.replayed
        or replay.draft_v2.draft_v2_hash != main.draft_v2.draft_v2_hash
        or replay.verification_summary.summary_hash
        != main.verification_summary.summary_hash
    ):
        raise ValidationFailure("Draft V2 replay changed semantic output")

    supported_draft, supported_packet = _inputs(
        "Die Vorschrift ist aufgehoben.",
        contents=("Die Vorschrift ist aufgehoben.",),
    )
    citation = supported_packet.ordered_citations[0]
    verified = _run(
        supported_draft,
        supported_packet,
        f"Die Vorschrift ist aufgehoben [citation:{citation.citation_id}].",
        DeterministicFakeSemanticVerifier(),
    )[0]
    semantic_support = _run(
        supported_draft,
        supported_packet,
        f"Die Regel wurde aufgehoben [citation:{citation.citation_id}].",
        DeterministicFakeSemanticVerifier(default=SemanticCandidateVerdict.SUPPORTS),
    )[0]
    semantic_uncertain = _run(
        supported_draft,
        supported_packet,
        f"Die Regel könnte geändert sein [citation:{citation.citation_id}].",
        DeterministicFakeSemanticVerifier(default=SemanticCandidateVerdict.UNCERTAIN),
    )[0]
    deterministic_override = _run(
        supported_draft,
        supported_packet,
        f"Die Vorschrift gilt ab 2099-01-01 [citation:{citation.citation_id}].",
        DeterministicFakeSemanticVerifier(default=SemanticCandidateVerdict.SUPPORTS),
    )[0]
    invalid_citation = _run(
        supported_draft,
        supported_packet,
        "Die Vorschrift ist aufgehoben [citation:not-allowed].",
        DeterministicFakeSemanticVerifier(default=SemanticCandidateVerdict.SUPPORTS),
    )[0]

    common = {"document": "step25-conflict-document", "provision": "§-25"}
    conflict_draft, conflict_packet = _inputs(
        "Die Vorschrift ist aufgehoben.",
        contents=(
            "Die Vorschrift ist aufgehoben.",
            "Die Vorschrift ist nicht aufgehoben.",
        ),
        metadatas=(
            metadata(version="step25-conflict-a", **common),
            metadata(version="step25-conflict-b", **common),
        ),
    )
    conflict_citations = " ".join(
        f"[citation:{item.citation_id}]" for item in conflict_packet.ordered_citations
    )
    conflict = _run(
        conflict_draft,
        conflict_packet,
        f"Nach den vorliegenden Quellen ist unklar, ob die Vorschrift aufgehoben ist {conflict_citations}.",
        DeterministicFakeSemanticVerifier(),
    )[0]

    if verified.verification_summary.summary_status is not VerificationSummaryStatus.VERIFIED:
        raise ValidationFailure("VERIFIED fixture changed")
    if main.verification_summary.summary_status is not VerificationSummaryStatus.INCOMPLETE:
        raise ValidationFailure("INCOMPLETE fixture changed")
    if invalid_citation.verification_summary.summary_status is not VerificationSummaryStatus.FAILED:
        raise ValidationFailure("FAILED fixture changed")
    if conflict.verification_summary.summary_status is not VerificationSummaryStatus.CONFLICTING:
        raise ValidationFailure("CONFLICTING fixture changed")
    if semantic_support.ordered_claim_verifications[0].final_step25_verdict is not FinalStep25ClaimVerdict.VERIFIED_SUPPORTED:
        raise ValidationFailure("semantic support signal was not bounded correctly")
    if semantic_uncertain.ordered_claim_verifications[0].final_step25_verdict is not FinalStep25ClaimVerdict.UNVERIFIED:
        raise ValidationFailure("semantic uncertainty was promoted")
    deterministic = deterministic_override.ordered_claim_verifications[0]
    if (
        deterministic.deterministic_fact_result is not CheckResult.FAIL
        or deterministic.final_step25_verdict is not FinalStep25ClaimVerdict.VERIFIED_REFUTED
    ):
        raise ValidationFailure("semantic signal overrode deterministic failure")

    tampered_packet = copy.copy(main_packet)
    object.__setattr__(tampered_packet, "packet_hash", "0" * 64)
    hmac_tamper = "PASS"
    try:
        prepare_draft_v2_generation_request(
            main_draft,
            tampered_packet,
            receipt,
            authenticator,
        )
    except Step25BoundaryError:
        pass
    else:
        hmac_tamper = "FAIL"
        raise ValidationFailure("packet tamper reached generation")

    evidence: dict[str, Any] = {
        "step": "STEP_25_DRAFT_V2_GENERATION_LAYERED_CLAIM_VERIFIER_1A",
        "schema_version": "1.0.0",
        "status": "PASS",
        "start_sha": START_SHA,
        "upstream_identity": {
            "step24_validation_digest": step24["validation_digest"],
            "draft_v1_hash": main_packet.draft_v1_hash,
            "correction_packet_hash": main_packet.packet_hash,
            "packet_integrity_receipt_hash": receipt.receipt_hash,
            "route_hash": main_packet.route_hash,
        },
        "draft_v2": {
            "draft_v2_hash": main.draft_v2.draft_v2_hash,
            "draft_text_sha256": main.draft_v2.draft_text_sha256,
            "draft_byte_length": main.draft_v2.draft_byte_length,
            "provider_identity_digest": request.provider_identity.identity_digest,
            "provider_id": request.provider_identity.provider_id,
            "model_id": request.provider_identity.model_id,
            "model_declared_version": request.provider_identity.model_revision_or_declared_version,
            "prompt_template_digest": request.prompt_template.template_digest,
            "generation_parameters_digest": request.generation_parameters.parameters_digest,
            "tools_disabled": True,
        },
        "layer_matrix": {
            "schema_contract": "PASS",
            "packet_compliance": "PASS",
            "deterministic_fact_date_source": "PASS",
            "citation_binding": "PASS",
            "evidence_binding": "PASS",
            "semantic_support_candidate": "PASS",
            "semantic_uncertainty": "PASS",
            "deterministic_failure_overrides_verifier": "PASS",
            "invalid_citation_negative": "PASS",
        },
        "main_verification": {
            "claim_count": main.verification_summary.claim_count,
            "required_corrections_satisfied": main.verification_summary.required_corrections_satisfied,
            "required_corrections_unsatisfied": main.verification_summary.required_corrections_unsatisfied,
            "prohibited_claim_violations": main.verification_summary.prohibited_claim_violations,
            "citation_failures": main.verification_summary.citation_failures,
            "verified_supported": main.verification_summary.verified_supported_count,
            "verified_refuted": main.verification_summary.verified_refuted_count,
            "unverified": main.verification_summary.unverified_count,
            "conflicting": main.verification_summary.conflicting_count,
            "invalid": main.verification_summary.invalid_count,
            "summary_status": main.verification_summary.summary_status.value,
            "summary_hash": main.verification_summary.summary_hash,
            "claim_verification_hashes": [
                item.verification_hash for item in main.ordered_claim_verifications
            ],
        },
        "summary_state_matrix": {
            "verified": verified.verification_summary.summary_hash,
            "incomplete": main.verification_summary.summary_hash,
            "failed": invalid_citation.verification_summary.summary_hash,
            "conflicting": conflict.verification_summary.summary_hash,
        },
        "replay": {
            "draft_v2_stage2_persistence": "PASS_IN_MEMORY_UNIT_BOUNDARY",
            "exact_replay": "PASS",
            "provider_calls_first": provider.calls,
            "provider_calls_replay": replay_provider.calls,
            "verification_summary_replay": "PASS",
            "durable_claim_verification_persistence": "DEFERRED_NO_SAFE_STEP4_VOCABULARY",
            "migration_added": False,
        },
        "provider_validation": {
            "fake_provider": "PASS",
            "fake_semantic_verifier": "PASS",
            "real_provider": "UNAVAILABLE_NOT_REQUIRED_CANONICAL_STEP22_QUOTA_BOUNDARY",
            "real_provider_call_attempted": False,
            "approved_provider_only": True,
        },
        "integrity_and_authority": {
            "packet_hmac_tamper_negative": hmac_tamper,
            "cross_tenant_negative": "PASS_UNIT_TEST",
            "cross_user_negative": "PASS_UNIT_TEST",
            "cross_hat_negative": "PASS_UPSTREAM_PACKET_LINEAGE",
            "model_self_certification_trusted": False,
            "semantic_verifier_is_authority": False,
            "source_authority_changed": False,
            "temporal_policy_bypassed": False,
        },
        "resource_and_effect_bounds": {
            "maximum_draft_v2_bytes": 65536,
            "maximum_claims": 256,
            "maximum_semantic_evidence_items": 16,
            "maximum_semantic_context_bytes": 16384,
            "maximum_model_attempts": 2,
            "network_calls": 0,
            "retrieval_calls": 0,
            "web_calls": 0,
            "aws_mutations": 0,
            "s3_mutations": 0,
        },
        "step26_started": False,
        "final_answer_assembly": 0,
        "human_review_flow": 0,
        "personal_memory_proposal": 0,
        "approval_created": 0,
        "execution_capability_added": 0,
        "cleanup": "NOT_REQUIRED_NO_EXTERNAL_RUNTIME",
    }
    evidence["validation_digest"] = canonical_sha256(evidence)
    return evidence


def main() -> int:
    try:
        evidence = validate()
    except (OSError, TypeError, ValueError, RuntimeError, ValidationFailure) as exc:
        print(canonical_json({"status": "FAILED", "reason": type(exc).__name__}), file=sys.stderr)
        return 1
    print(canonical_json(evidence))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
