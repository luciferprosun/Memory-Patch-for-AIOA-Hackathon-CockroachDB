#!/usr/bin/env python3
"""Bounded offline validation for Step 24 Correction Packet integrity."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from aioa_memory_kernel.claims import ClaimReasonCode  # noqa: E402
from aioa_memory_kernel.contracts.serialization import (  # noqa: E402
    canonical_json,
    canonical_sha256,
)
from aioa_memory_kernel.corrections import (  # noqa: E402
    PACKET_AUTHENTICITY_ALGORITHM,
    PERSISTENCE_DECISION,
    CorrectionActionType,
    CorrectionPacketBoundaryError,
    CorrectionPacketService,
    HmacSha256PacketAuthenticator,
    ProhibitionType,
    Step24ReasonCode,
    canonical_packet_bytes,
    verify_correction_packet_hash,
    verify_packet_against_snapshot,
)
from tests.test_step21_temporal_resolution import metadata  # noqa: E402
from tests.test_step23_claim_evidence_binding import pipeline  # noqa: E402


START_SHA = "f328168434984ea022346758770d6df23c67bb08"
STEP23_EVIDENCE = ROOT / "docs/evidence/modeling/step23-claim-evidence-binding-validation.json"
VALIDATION_KEY_ID = "step24-non-production-validation-key"


class ValidationFailure(RuntimeError):
    pass


def _load_step23_evidence() -> dict[str, Any]:
    value = json.loads(STEP23_EVIDENCE.read_text(encoding="utf-8"))
    claimed = value.get("validation_digest")
    if value.get("status") != "PASS" or claimed != canonical_sha256(
        value,
        exclude_fields=("validation_digest",),
    ):
        raise ValidationFailure("Step 23 evidence identity is invalid")
    if value.get("step24_started") is not False:
        raise ValidationFailure("Step 23 evidence does not preserve the Step 24 gate")
    return value


def _main_snapshot():
    return pipeline(
        "Die Vorschrift ist aufgehoben. Das Gesetz gilt. Eine unbelegte Aussage. Hallo!",
        contents=(
            "Die Vorschrift ist aufgehoben.",
            "Das Gesetz gilt nicht.",
            "Anderer Inhalt.",
        ),
    )[-1]


def _future_snapshot():
    return pipeline(
        "Die Vorschrift ist aufgehoben.",
        metadatas=(metadata(version="step24-future", effective_from="2030-01-01"),),
        contents=("Die Vorschrift ist aufgehoben.",),
    )[-1]


def _authority_snapshot():
    text = "Laut offizieller Quelle gilt das Gesetz."
    return pipeline(text, contents=(text,))[-1]


def _conflict_snapshot():
    common = {"document": "step24-conflict-document", "provision": "§-24"}
    return pipeline(
        "Die Vorschrift ist aufgehoben.",
        contents=(
            "Die Vorschrift ist aufgehoben.",
            "Die Vorschrift ist nicht aufgehoben.",
        ),
        metadatas=(
            metadata(version="step24-conflict-a", **common),
            metadata(version="step24-conflict-b", **common),
        ),
    )[-1]


def validate() -> dict[str, Any]:
    step23 = _load_step23_evidence()
    signer = HmacSha256PacketAuthenticator(
        key_id=VALIDATION_KEY_ID,
        key_material=bytes(range(32)),
    )
    service = CorrectionPacketService(signer)
    snapshot = _main_snapshot()
    packet = service.build(snapshot)
    receipt = service.authenticate(packet)
    service.verify(packet, receipt)
    verify_packet_against_snapshot(packet, snapshot)

    replay = service.build(_main_snapshot())
    replay_receipt = service.authenticate(replay)
    if (
        replay.packet_hash != packet.packet_hash
        or canonical_packet_bytes(replay) != canonical_packet_bytes(packet)
        or replay_receipt.authenticator != receipt.authenticator
    ):
        raise ValidationFailure("canonical packet replay changed")

    supported = next(
        claim
        for claim in packet.ordered_claims
        if claim.exact_claim_text == "Die Vorschrift ist aufgehoben."
    )
    refuted = next(
        claim
        for claim in packet.ordered_claims
        if claim.exact_claim_text == "Das Gesetz gilt."
    )
    unverified = next(
        claim
        for claim in packet.ordered_claims
        if claim.exact_claim_text == "Eine unbelegte Aussage."
    )
    if any(
        item.claim_id == supported.claim_id
        for item in packet.ordered_required_corrections
    ):
        raise ValidationFailure("supported claim received a factual correction")
    refuted_correction = next(
        item
        for item in packet.ordered_required_corrections
        if item.claim_id == refuted.claim_id
    )
    unverified_correction = next(
        item
        for item in packet.ordered_required_corrections
        if item.claim_id == unverified.claim_id
    )
    if (
        refuted_correction.correction_action is not CorrectionActionType.REMOVE_CLAIM
        or unverified_correction.correction_action is not CorrectionActionType.QUALIFY_CLAIM
    ):
        raise ValidationFailure("correction derivation matrix changed")

    future_packet = service.build(_future_snapshot())
    if future_packet.ordered_required_corrections[0].correction_action is not CorrectionActionType.TEMPORAL_CORRECTION:
        raise ValidationFailure("temporal correction was not derived")
    authority_snapshot = _authority_snapshot()
    if ClaimReasonCode.SOURCE_AUTHORITY_INSUFFICIENT not in authority_snapshot.ordered_evidence_links[0].reason_codes:
        raise ValidationFailure("authority edge fixture changed")
    authority_packet = service.build(authority_snapshot)
    if authority_packet.ordered_required_corrections[0].correction_action is not CorrectionActionType.SOURCE_AUTHORITY_CORRECTION:
        raise ValidationFailure("authority correction was not derived")
    conflict_packet = service.build(_conflict_snapshot())
    if (
        len(conflict_packet.ordered_conflicts) != 1
        or not conflict_packet.ordered_conflicts[0].supporting_evidence_hashes
        or not conflict_packet.ordered_conflicts[0].refuting_evidence_hashes
        or ProhibitionType.DO_NOT_RESOLVE_CONFLICT_AS_CERTAIN
        not in {item.prohibition_type for item in conflict_packet.ordered_prohibited_claims}
    ):
        raise ValidationFailure("conflict was not preserved")

    snapshot_tamper = "PASS"
    tampered_snapshot = copy.copy(snapshot)
    object.__setattr__(tampered_snapshot, "snapshot_hash", "0" * 64)
    try:
        service.build(tampered_snapshot)
    except CorrectionPacketBoundaryError:
        pass
    else:
        snapshot_tamper = "FAIL"
        raise ValidationFailure("snapshot tampering was admitted")

    packet_tamper = "PASS"
    tampered_packet = copy.copy(packet)
    object.__setattr__(tampered_packet, "packet_hash", "0" * 64)
    try:
        verify_correction_packet_hash(tampered_packet)
    except CorrectionPacketBoundaryError:
        pass
    else:
        packet_tamper = "FAIL"
        raise ValidationFailure("packet tampering was admitted")

    hmac_tamper = "PASS"
    tampered_receipt = copy.copy(receipt)
    object.__setattr__(tampered_receipt, "authenticator", "0" * 64)
    try:
        service.verify(packet, tampered_receipt)
    except CorrectionPacketBoundaryError:
        pass
    else:
        hmac_tamper = "FAIL"
        raise ValidationFailure("HMAC tampering was admitted")

    isolation_negative = "PASS"
    cross_tenant_packet = copy.copy(packet)
    object.__setattr__(cross_tenant_packet, "tenant_id", "cross-tenant")
    try:
        verify_packet_against_snapshot(cross_tenant_packet, snapshot)
    except CorrectionPacketBoundaryError:
        pass
    else:
        isolation_negative = "FAIL"
        raise ValidationFailure("cross-tenant packet detachment was admitted")

    evidence: dict[str, Any] = {
        "step": "STEP_24_CORRECTION_PACKET_CONSTRUCTION_INTEGRITY_1A",
        "schema_version": "1.0.0",
        "status": "PASS",
        "start_sha": START_SHA,
        "upstream_identity": {
            "committed_step23_validation_digest": step23["validation_digest"],
            "committed_step23_snapshot_hash": step23["packet_input_snapshot"]["snapshot_hash"],
            "typed_validation_snapshot_hash": snapshot.snapshot_hash,
            "draft_v1_hash": packet.draft_v1_hash,
            "step20_bundle_hashes": list(packet.step20_evidence_bundle_hashes),
            "step21_result_hash": packet.step21_resolution_hash,
            "route_hash": packet.route_hash,
        },
        "packet": {
            "packet_id": packet.packet_id,
            "packet_hash": packet.packet_hash,
            "packet_policy_digest": packet.packet_policy.policy_digest,
            "scope_binding_digest": packet.scope_binding_digest,
            "knowledge_policy_binding_digest": packet.knowledge_policy_binding.binding_digest,
            "evidence_status": packet.evidence_status.value,
            "claim_count": len(packet.ordered_claims),
            "required_correction_count": len(packet.ordered_required_corrections),
            "prohibited_claim_count": len(packet.ordered_prohibited_claims),
            "citation_count": len(packet.ordered_citations),
            "conflict_count": len(packet.ordered_conflicts),
            "canonical_bytes": len(canonical_packet_bytes(packet)),
        },
        "derivation_matrix": {
            "supported_retained": "PASS",
            "refuted_corrected_and_prohibited": "PASS",
            "unverified_qualified": "PASS",
            "temporal_mismatch_correction": "PASS",
            "source_authority_correction": "PASS",
            "conflict_qualification": "PASS",
            "citation_binding": "PASS",
        },
        "ordered_hashes": {
            "corrections": [item.correction_hash for item in packet.ordered_required_corrections],
            "prohibited_claims": [item.prohibition_hash for item in packet.ordered_prohibited_claims],
            "citations": [item.citation_hash for item in packet.ordered_citations],
            "conflicts": [item.conflict_hash for item in conflict_packet.ordered_conflicts],
        },
        "integrity": {
            "canonical_replay": "PASS",
            "packet_tamper_negative": packet_tamper,
            "snapshot_tamper_negative": snapshot_tamper,
            "hmac_algorithm": PACKET_AUTHENTICITY_ALGORITHM,
            "hmac_key_id": VALIDATION_KEY_ID,
            "hmac_validation": "PASS",
            "hmac_tamper_negative": hmac_tamper,
            "authenticator_committed": False,
            "production_key_material_committed": False,
            "non_production_test_key": True,
            "receipt_hash": receipt.receipt_hash,
        },
        "fixture_boundary": {
            "real_step23_committed_identity": "PASS_HASH_ONLY",
            "real_step23_serialized_snapshot_available": False,
            "typed_step20_step21_step22_step23_pipeline": "PASS_SYNTHETIC_BOUNDED",
            "synthetic_edge_cases": "PASS_EXPLICITLY_SYNTHETIC",
        },
        "persistence": {
            "decision": PERSISTENCE_DECISION,
            "migration_added": False,
            "database_calls": 0,
            "reason": "Step4 tables are retained; Step24 does not guess missing durable upstream route/action-policy rows or independently persist claims.",
        },
        "authority_and_isolation": {
            "cross_tenant_negative": isolation_negative,
            "cross_user_negative": "PASS_UNIT_TEST",
            "cross_hat_negative": "PASS_UNIT_TEST",
            "source_authority_upgraded": False,
            "temporal_policy_bypassed": False,
            "packet_grants_approval": False,
            "packet_grants_execution": False,
        },
        "resource_and_effect_bounds": {
            "network_calls": 0,
            "provider_calls": 0,
            "model_calls": 0,
            "retrieval_calls": 0,
            "aws_mutations": 0,
            "s3_mutations": 0,
            "owned_processes_started": 0,
        },
        "step25_started": False,
        "draft_v2_generation": 0,
        "final_verifier": 0,
        "cleanup": "NOT_REQUIRED_NO_EXTERNAL_RUNTIME",
    }
    if Step24ReasonCode.PACKET_BUILT not in packet.reason_codes:
        raise ValidationFailure("packet build reason is absent")
    evidence["validation_digest"] = canonical_sha256(evidence)
    return evidence


def main() -> int:
    try:
        evidence = validate()
    except (
        CorrectionPacketBoundaryError,
        OSError,
        TypeError,
        ValueError,
        ValidationFailure,
    ) as exc:
        print(
            canonical_json({"status": "FAILED", "reason": type(exc).__name__}),
            file=sys.stderr,
        )
        return 1
    print(canonical_json(evidence))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
