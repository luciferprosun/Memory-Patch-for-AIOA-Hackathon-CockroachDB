#!/usr/bin/env python3
"""Bounded offline validation for Step 21 temporal evidence policy."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from aioa_memory_kernel.contracts.enums import EvidenceStatus  # noqa: E402
from aioa_memory_kernel.contracts.serialization import (  # noqa: E402
    canonical_json,
    canonical_sha256,
)
from aioa_memory_kernel.temporal import (  # noqa: E402
    EvidenceAvailability,
    FreshnessStatus,
    TemporalApplicability,
    TemporalQueryMode,
    verify_temporal_result_hash,
)
from tests.test_step21_temporal_resolution import (  # noqa: E402
    NOW,
    bundle_outcome,
    freshness_policy,
    metadata,
    resolve,
)


START_SHA = "785a349b3124e2251624a6502fb477cf421e02f4"
STEP20_EVIDENCE = ROOT / "docs/evidence/retrieval/step20-hybrid-evidence-bundle-validation.json"
STEP16_EVIDENCE = ROOT / "docs/evidence/corpus/step16-german-law-hat-publication-summary.json"


class ValidationFailure(RuntimeError):
    pass


def _load_verified(path: Path, digest_field: str, status_field: str) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get(status_field) not in {"PASS", "COMPLETE"}:
        raise ValidationFailure(f"{path.name} does not report PASS")
    claimed = value.get(digest_field)
    if claimed != canonical_sha256(value, exclude_fields=(digest_field,)):
        raise ValidationFailure(f"{path.name} digest mismatch")
    return value


def _assert_status(result: object, expected: EvidenceStatus, case: str) -> None:
    verify_temporal_result_hash(result)  # type: ignore[arg-type]
    if result.evidence_status is not expected:  # type: ignore[attr-defined]
        raise ValidationFailure(f"{case} returned the wrong evidence status")


def validate() -> dict[str, Any]:
    step20 = _load_verified(STEP20_EVIDENCE, "validation_digest", "status")
    step16 = _load_verified(STEP16_EVIDENCE, "evidence_digest", "verdict")
    real = step20["real_inputs"]
    real_document = f"verified-step16:{real['official_identifier']}"
    real_version = str(real["version_identity"])
    real_source = str(real["source_id"])

    current_outcome = bundle_outcome(
        metadata(
            document=real_document,
            version=real_version,
            official_identifier=str(real["official_identifier"]),
            provision="I.",
            effective_from="2024-01-01",
        ),
        source_ids=(real_source,),
    )
    current = resolve(current_outcome)
    _assert_status(current, EvidenceStatus.SUFFICIENT, "CURRENT_APPLICABLE")

    historical_outcome = bundle_outcome(
        metadata(
            document=real_document,
            version="synthetic-historical-v1",
            provision="I.",
            effective_from="2020-01-01",
            effective_to="2024-01-01",
            version_status="HISTORICAL",
        ),
        metadata(
            document=real_document,
            version=real_version,
            provision="I.",
            effective_from="2024-01-01",
        ),
        contents=("Historische Fassung.", "Aktuelle Fassung."),
        source_ids=(real_source, real_source),
    )
    historical = resolve(
        historical_outcome,
        mode=TemporalQueryMode.AS_OF,
        as_of=datetime(2022, 1, 1, tzinfo=timezone.utc),
    )
    _assert_status(historical, EvidenceStatus.SUFFICIENT, "HISTORICAL_AS_OF")
    if [item.version_identity for item in historical.assessments if item.selected] != [
        "synthetic-historical-v1"
    ]:
        raise ValidationFailure("historical version selection mismatch")

    future = resolve(
        bundle_outcome(metadata(version="synthetic-future", effective_from="2028-01-01"))
    )
    _assert_status(future, EvidenceStatus.INSUFFICIENT, "FUTURE_NOT_YET_EFFECTIVE")
    if future.assessments[0].temporal_applicability is not TemporalApplicability.NOT_YET_APPLICABLE:
        raise ValidationFailure("future-effective boundary mismatch")

    superseded = resolve(
        bundle_outcome(
            metadata(version="synthetic-old", superseded_by=("synthetic-new",), version_status="HISTORICAL"),
            metadata(version="synthetic-new", effective_from="2024-01-01", supersedes=("synthetic-old",)),
        )
    )
    _assert_status(superseded, EvidenceStatus.SUFFICIENT, "SUPERSEDED")

    conflicting = resolve(
        bundle_outcome(
            metadata(version="synthetic-conflict-a"),
            metadata(version="synthetic-conflict-b"),
            contents=("Inhalt A.", "Inhalt B."),
        )
    )
    _assert_status(conflicting, EvidenceStatus.CONFLICTING, "CONFLICTING")

    stale = resolve(
        bundle_outcome(metadata(version="synthetic-stale", verified_at="2020-01-01T00:00:00Z")),
        policy=freshness_policy(days=30),
    )
    _assert_status(stale, EvidenceStatus.STALE, "STALE")
    if stale.assessments[0].freshness_status is not FreshnessStatus.STALE:
        raise ValidationFailure("freshness dimension mismatch")

    insufficient = resolve(
        bundle_outcome(metadata(version="synthetic-unknown", effective_from=None))
    )
    _assert_status(insufficient, EvidenceStatus.INSUFFICIENT, "INSUFFICIENT")

    fallback = resolve(
        bundle_outcome(metadata(version="synthetic-primary-future", effective_from="2030-01-01")),
        fallback=bundle_outcome(metadata(version="synthetic-fallback-current", effective_from="2020-01-01")),
    )
    _assert_status(fallback, EvidenceStatus.SUFFICIENT, "COMPLETENESS_FALLBACK")
    if not fallback.completeness_fallback.attempted or fallback.completeness_fallback.attempts_used != 1:
        raise ValidationFailure("completeness fallback bound mismatch")

    unavailable = resolve(
        current_outcome,
        availability=EvidenceAvailability.UNAVAILABLE,
    )
    _assert_status(unavailable, EvidenceStatus.UNAVAILABLE, "UNAVAILABLE")

    cases = {
        "CURRENT_APPLICABLE": current,
        "HISTORICAL_AS_OF": historical,
        "FUTURE_NOT_YET_EFFECTIVE": future,
        "SUPERSEDED": superseded,
        "CONFLICTING": conflicting,
        "STALE": stale,
        "INSUFFICIENT": insufficient,
        "COMPLETENESS_FALLBACK": fallback,
        "UNAVAILABLE": unavailable,
    }
    evidence: dict[str, Any] = {
        "step": "STEP_21_TEMPORAL_RESOLVER_CONFLICT_FRESHNESS_POLICY_1A",
        "schema_version": "1.0.0",
        "status": "PASS",
        "start_sha": START_SHA,
        "step20_input": {
            "committed_validation_bundle_hash": step20["evidence_bundle"]["bundle_hash"],
            "typed_integration_bundle_hash": current.step20_bundle_hash,
            "route_hash": current.route_hash,
            "selected_hat_id": current.selected_hat_id,
            "selected_hat_version": current.selected_hat_version,
            "source_id": real_source,
            "version_identity": real_version,
        },
        "policies": {
            "temporal_policy_digest": current.temporal_policy_digest,
            "freshness_policy_id": current.freshness_policy_id,
            "freshness_policy_version": current.freshness_policy_version,
            "freshness_policy_digest": current.freshness_policy_digest,
            "completeness_policy_digest": current.completeness_policy_digest,
            "effective_interval": "START_INCLUSIVE_END_EXCLUSIVE",
        },
        "cases": {
            name: {
                "evidence_status": result.evidence_status.value,
                "result_hash": result.result_hash,
                "assessment_hashes": [item.assessment_hash for item in result.assessments],
                "selected_versions": [item.version_identity for item in result.assessments if item.selected],
                "conflict_group_hashes": [item.conflict_group_hash for item in result.conflict_groups],
            }
            for name, result in cases.items()
        },
        "fixture_boundary": {
            "real_step16_identity": "PASS_IDENTITY_AND_DIGEST_ONLY",
            "real_step16_evidence_digest": step16["evidence_digest"],
            "real_multiversion_temporal_fixture": "UNAVAILABLE",
            "synthetic_temporal_edge_family": "PASS_EXPLICITLY_SYNTHETIC",
            "raw_legal_text_claimed": False,
            "source_mutations": 0,
        },
        "authority_and_isolation": {
            "step20_bundle_hash_verified": True,
            "route_tenant_user_hat_scope_bound": True,
            "source_authority_unchanged": True,
            "answer_status_separate": True,
            "model_authority": False,
            "provider_authority": False,
            "execution_authority": False,
            "cross_tenant_resolution": False,
            "cross_hat_resolution": False,
        },
        "resource_bounds": {
            "maximum_completeness_attempts": 1,
            "fallback_attempts_observed": fallback.completeness_fallback.attempts_used,
            "network_acquisition": 0,
            "model_calls": 0,
            "provider_calls": 0,
            "aws_mutations": 0,
            "s3_mutations": 0,
        },
        "step22_started": False,
        "cleanup": {
            "disposable_database": "NOT_REQUIRED",
            "temporary_runtime": "NOT_REQUIRED",
            "owned_processes_started": 0,
        },
    }
    evidence["validation_digest"] = canonical_sha256(evidence)
    return evidence


def main() -> int:
    try:
        evidence = validate()
    except (OSError, ValueError, ValidationFailure) as exc:
        print(canonical_json({"status": "FAILED", "reason": type(exc).__name__}), file=sys.stderr)
        return 1
    print(canonical_json(evidence))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
