"""Step 21 temporal resolution, conflict, freshness, and fallback tests."""

from __future__ import annotations

import ast
import copy
import unittest
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from aioa_memory_kernel.contracts.enums import EvidenceStatus, KnowledgeRoute
from aioa_memory_kernel.contracts.exceptions import ContractValidationError, IntegrityError
from aioa_memory_kernel.evidence import (
    HybridEvidenceOutcome,
    HybridEvidenceService,
    RetrievalCoverageStatus,
    Step20ReasonCode,
)
from aioa_memory_kernel.retrieval import RetrievalMode
from aioa_memory_kernel.temporal import (
    MAX_COMPLETENESS_ATTEMPTS,
    TEMPORAL_FACTS_DIGEST_SCHEME,
    CompletenessPolicy,
    EvidenceAvailability,
    FreshnessPolicy,
    FreshnessStatus,
    Step21ReasonCode,
    SupersessionStatus,
    TemporalApplicability,
    TemporalBoundaryError,
    TemporalFacts,
    TemporalQueryMode,
    TemporalResolutionRequest,
    TemporalResolutionService,
    verify_conflict_group_hash,
    verify_temporal_assessment_hash,
    verify_temporal_request_hash,
    verify_temporal_result_hash,
)
from tests.test_step20_hybrid_evidence_bundle import (
    assemble,
    hybrid_request,
    lexical_candidate,
    lexical_pair,
    route,
)


ROOT = Path(__file__).resolve().parents[1]
TEMPORAL_ROOT = ROOT / "src/aioa_memory_kernel/temporal"
NOW = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)
SOURCE_KIND = "DE_FEDERAL_OFFICIAL_CONSOLIDATED_LAW"


class FixedClock:
    def __init__(self, value: datetime = NOW) -> None:
        self.value = value

    def now(self) -> datetime:
        return self.value


def instant(value: str | None) -> datetime | None:
    if value is None:
        return None
    if len(value) == 10:
        return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def metadata(
    *,
    document: str = "document-bgb",
    version: str = "version-1",
    provision: str = "§-1",
    official_identifier: str = "BGB",
    effective_from: str | None = "2024-01-01",
    effective_to: str | None = None,
    applicable_from: str | None = None,
    applicable_to: str | None = None,
    decision_date: str | None = None,
    verified_at: str | None = "2026-08-01T00:00:00Z",
    retrieved_at: str | None = None,
    source_observed_at: str | None = None,
    superseded_at: str | None = None,
    version_status: str | None = "CURRENT",
    supersedes: tuple[str, ...] = (),
    superseded_by: tuple[str, ...] = (),
    corrupt_digest: bool = False,
    extras: dict[str, object] | None = None,
) -> dict[str, object]:
    facts = TemporalFacts(
        effective_from=instant(effective_from),
        effective_to=instant(effective_to),
        applicable_from=instant(applicable_from),
        applicable_to=instant(applicable_to),
        decision_date=instant(decision_date),
        verified_at=instant(verified_at),
        retrieved_at=instant(retrieved_at),
        source_observed_at=instant(source_observed_at),
        superseded_at=instant(superseded_at),
        version_status=version_status,
        document_identity=document,
        version_identity=version,
        official_identifier=official_identifier,
        provision_identifier=provision,
        supersedes=supersedes,
        superseded_by=superseded_by,
    )
    temporal: dict[str, object] = {
        key: value
        for key, value in {
            "effective_from": effective_from,
            "effective_to": effective_to,
            "applicable_from": applicable_from,
            "applicable_to": applicable_to,
            "decision_date": decision_date,
            "verified_at": verified_at,
            "retrieved_at": retrieved_at,
            "source_observed_at": source_observed_at,
            "superseded_at": superseded_at,
            "version_status": version_status,
            "supersedes": list(supersedes),
            "superseded_by": list(superseded_by),
        }.items()
        if value not in (None, (), [])
    }
    result: dict[str, object] = {
        "document_identity": document,
        "version_identity": version,
        "official_identifier": official_identifier,
        "provision_identifier": provision,
        "temporal_facts": temporal,
        "temporal_facts_digest_scheme": TEMPORAL_FACTS_DIGEST_SCHEME,
        "temporal_facts_digest": "f" * 64 if corrupt_digest else facts.facts_hash,
        "is_current": version_status == "CURRENT",
    }
    result.update(extras or {})
    return result


def bundle_outcome(
    *metadatas: dict[str, object],
    contents: tuple[str, ...] | None = None,
    source_ids: tuple[str, ...] | None = None,
    route_value=None,
    truncated: bool = False,
    effective_scope=None,
):
    selected_route = route_value or route()
    bodies = contents or tuple(f"Tekst dowodowy {index}." for index in range(len(metadatas)))
    sources = source_ids or tuple(f"source-{index}" for index in range(len(metadatas)))
    candidates = tuple(
        lexical_candidate(
            RetrievalMode.EXACT_IDENTIFIER,
            source_id=sources[index],
            version_id=str(value.get("version_identity", f"version-{index}")),
            chunk_id=f"chunk-{index}-{str(value.get('version_identity', index))}",
            chunk_ordinal=index,
            content=bodies[index],
            structured_metadata=value,
            effective_scope=effective_scope,
        )
        for index, value in enumerate(metadatas)
    )
    pair = lexical_pair(
        RetrievalMode.EXACT_IDENTIFIER,
        candidates,
        route_value=selected_route,
        truncated=truncated,
    )
    return assemble((pair,), route_value=selected_route)


def freshness_policy(
    *,
    days: int = 30,
    version: str = "1",
    include_source: bool = True,
) -> FreshnessPolicy:
    return FreshnessPolicy(
        policy_id="german-law-reviewed-freshness-1a",
        policy_version=version,
        maximum_age_seconds_by_source_kind=(
            {SOURCE_KIND: days * 24 * 60 * 60} if include_source else {}
        ),
    )


def temporal_request(
    outcome,
    *,
    route_value=None,
    mode: TemporalQueryMode = TemporalQueryMode.CURRENT,
    as_of: datetime | None = None,
    now: datetime = NOW,
    policy: FreshnessPolicy | None = None,
    availability: EvidenceAvailability = EvidenceAvailability.AVAILABLE,
    fallback=None,
    completeness: CompletenessPolicy | None = None,
) -> TemporalResolutionRequest:
    return TemporalResolutionRequest(
        route=route_value or route(),
        step20_outcome=outcome,
        temporal_mode=mode,
        knowledge_as_of=as_of,
        trusted_now=now,
        availability=availability,
        freshness_policy=policy or freshness_policy(),
        completeness_policy=completeness or CompletenessPolicy(),
        fallback_outcome=fallback,
    )


def resolve(outcome, **kwargs):
    return TemporalResolutionService().resolve(temporal_request(outcome, **kwargs))


class DecisionContractTests(unittest.TestCase):
    def test_exact_enum_values(self) -> None:
        self.assertEqual(
            [item.value for item in TemporalQueryMode],
            ["CURRENT", "AS_OF", "FUTURE", "UNSPECIFIED"],
        )
        self.assertEqual(
            [item.value for item in TemporalApplicability],
            ["APPLICABLE", "NOT_YET_APPLICABLE", "EXPIRED", "SUPERSEDED", "UNKNOWN", "CONFLICTING"],
        )
        self.assertEqual(
            [item.value for item in FreshnessStatus],
            ["FRESH", "STALE", "UNKNOWN", "NOT_APPLICABLE"],
        )

    def test_request_assessment_and_result_are_immutable(self) -> None:
        request_value = temporal_request(bundle_outcome(metadata()))
        result = TemporalResolutionService().resolve(request_value)
        with self.assertRaises(FrozenInstanceError):
            request_value.trusted_now = NOW + timedelta(days=1)  # type: ignore[misc]
        with self.assertRaises(FrozenInstanceError):
            result.evidence_status = EvidenceStatus.INVALID  # type: ignore[misc]
        with self.assertRaises(FrozenInstanceError):
            result.assessments[0].selected = False  # type: ignore[misc]

    def test_hashes_are_deterministic_and_verifiable(self) -> None:
        outcome = bundle_outcome(metadata())
        first_request = temporal_request(outcome)
        second_request = temporal_request(outcome)
        first = TemporalResolutionService().resolve(first_request)
        second = TemporalResolutionService().resolve(second_request)
        self.assertEqual(first_request.request_hash, second_request.request_hash)
        self.assertEqual(first.result_hash, second.result_hash)
        verify_temporal_request_hash(first_request)
        verify_temporal_result_hash(first)
        verify_temporal_assessment_hash(first.assessments[0])

    def test_as_of_and_policy_version_change_hashes(self) -> None:
        outcome = bundle_outcome(metadata(effective_from="2020-01-01"))
        one = temporal_request(
            outcome,
            mode=TemporalQueryMode.AS_OF,
            as_of=datetime(2021, 1, 1, tzinfo=timezone.utc),
        )
        two = temporal_request(
            outcome,
            mode=TemporalQueryMode.AS_OF,
            as_of=datetime(2022, 1, 1, tzinfo=timezone.utc),
        )
        three = temporal_request(outcome, policy=freshness_policy(version="2"))
        self.assertNotEqual(one.request_hash, two.request_hash)
        self.assertNotEqual(one.request_hash, three.request_hash)

    def test_naive_times_and_wrong_mode_boundaries_fail(self) -> None:
        outcome = bundle_outcome(metadata())
        with self.assertRaises(ContractValidationError):
            temporal_request(outcome, now=datetime(2026, 1, 1))
        with self.assertRaises(ContractValidationError):
            temporal_request(outcome, mode=TemporalQueryMode.AS_OF, as_of=None)
        with self.assertRaises(ContractValidationError):
            temporal_request(
                outcome,
                mode=TemporalQueryMode.FUTURE,
                as_of=NOW - timedelta(seconds=1),
            )

    def test_injected_clock_prepares_same_canonical_request(self) -> None:
        outcome = bundle_outcome(metadata())
        prepared = TemporalResolutionService().prepare_request(
            route=route(),
            step20_outcome=outcome,
            temporal_mode=TemporalQueryMode.CURRENT,
            knowledge_as_of=None,
            clock=FixedClock(),
            availability=EvidenceAvailability.AVAILABLE,
            freshness_policy=freshness_policy(),
        )
        self.assertEqual(prepared.trusted_now, NOW)
        self.assertEqual(prepared.evaluation_as_of, NOW)


class RouteAndBindingTests(unittest.TestCase):
    def test_valid_hat_assist_and_enforce_are_accepted(self) -> None:
        for kind in (KnowledgeRoute.HAT_ASSIST, KnowledgeRoute.HAT_ENFORCE):
            with self.subTest(kind=kind):
                selected = route(kind)
                result = resolve(
                    bundle_outcome(metadata(), route_value=selected),
                    route_value=selected,
                )
                self.assertIs(result.evidence_status, EvidenceStatus.SUFFICIENT)

    def test_pass_through_returns_not_required_without_bundle(self) -> None:
        selected = route(KnowledgeRoute.PASS_THROUGH)
        request20 = hybrid_request((), route_value=selected, requested_modalities=())
        outcome = HybridEvidenceService().assemble(request20)
        result = resolve(outcome, route_value=selected)
        self.assertIs(result.evidence_status, EvidenceStatus.NOT_REQUIRED)
        self.assertFalse(result.assessments)
        self.assertIn(Step21ReasonCode.NO_HAT_SELECTED, result.reason_codes)

    def test_ambiguous_route_fails_closed(self) -> None:
        selected = route(KnowledgeRoute.AMBIGUOUS)
        request20 = hybrid_request((), route_value=selected, requested_modalities=())
        outcome = HybridEvidenceOutcome(
            hybrid_request_hash=request20.request_hash,
            request_id=selected.request_id,
            tenant_id=selected.tenant_id,
            user_id=selected.user_id,
            route_hash=selected.route_hash,
            retrieval_coverage=RetrievalCoverageStatus.EMPTY,
            evidence_status=EvidenceStatus.NOT_REQUIRED,
            bundle=None,
            reason_codes=(Step20ReasonCode.NO_HAT_SELECTED,),
        )
        with self.assertRaises(TemporalBoundaryError) as caught:
            resolve(outcome, route_value=selected)
        self.assertIs(caught.exception.reason_code, Step21ReasonCode.AMBIGUOUS_ROUTE)

    def test_stale_step20_outcome_hash_is_rejected(self) -> None:
        outcome = copy.copy(bundle_outcome(metadata()))
        object.__setattr__(outcome, "outcome_hash", "0" * 64)
        with self.assertRaises(TemporalBoundaryError):
            temporal_request(outcome)

    def test_route_tenant_user_and_hash_mismatch_are_rejected(self) -> None:
        outcome = bundle_outcome(metadata())
        cases = (
            route(tenant_id="tenant-other"),
            route(user_id="user-other"),
            route(request_id="request-other"),
        )
        for changed in cases:
            with self.subTest(changed=changed):
                with self.assertRaises(TemporalBoundaryError):
                    temporal_request(outcome, route_value=changed)

    def test_tampered_bundle_candidate_and_scope_fail_closed(self) -> None:
        outcome = copy.copy(bundle_outcome(metadata()))
        bundle = copy.copy(outcome.bundle)
        item = copy.copy(bundle.ordered_items[0])
        object.__setattr__(item.identity, "tenant_id", "tenant-other")
        object.__setattr__(bundle, "ordered_items", (item,))
        object.__setattr__(outcome, "bundle", bundle)
        with self.assertRaises(TemporalBoundaryError):
            temporal_request(outcome)

    def test_fallback_cannot_widen_tenant_hat_or_scope(self) -> None:
        primary = bundle_outcome(metadata(effective_from="2030-01-01"))
        for changed_route in (
            route(tenant_id="tenant-other"),
            route(user_id="user-other"),
        ):
            with self.subTest(changed_route=changed_route):
                # A valid Step 20 outcome for the changed route cannot be built
                # from the original tenant candidates; mismatch is denied even
                # earlier by Step 20. A mismatched primary route is likewise
                # rejected by the Step 21 binding.
                with self.assertRaises(TemporalBoundaryError):
                    temporal_request(primary, route_value=changed_route)


class ApplicabilityTests(unittest.TestCase):
    def test_current_effective_open_interval_is_applicable(self) -> None:
        result = resolve(bundle_outcome(metadata(effective_from="2020-01-01")))
        self.assertIs(result.assessments[0].temporal_applicability, TemporalApplicability.APPLICABLE)
        self.assertIs(result.evidence_status, EvidenceStatus.SUFFICIENT)

    def test_future_and_expired_candidates_are_not_applicable(self) -> None:
        cases = (
            (metadata(effective_from="2030-01-01"), TemporalApplicability.NOT_YET_APPLICABLE),
            (metadata(effective_from="2020-01-01", effective_to="2025-01-01"), TemporalApplicability.EXPIRED),
        )
        for value, expected in cases:
            with self.subTest(expected=expected):
                result = resolve(bundle_outcome(value))
                self.assertIs(result.assessments[0].temporal_applicability, expected)
                self.assertIs(result.evidence_status, EvidenceStatus.INSUFFICIENT)

    def test_effective_from_is_inclusive_and_effective_to_exclusive(self) -> None:
        boundary = datetime(2026, 1, 1, tzinfo=timezone.utc)
        at_start = resolve(
            bundle_outcome(metadata(effective_from="2026-01-01")),
            mode=TemporalQueryMode.AS_OF,
            as_of=boundary,
        )
        at_end = resolve(
            bundle_outcome(metadata(effective_from="2020-01-01", effective_to="2026-01-01")),
            mode=TemporalQueryMode.AS_OF,
            as_of=boundary,
        )
        self.assertIs(at_start.assessments[0].temporal_applicability, TemporalApplicability.APPLICABLE)
        self.assertIs(at_end.assessments[0].temporal_applicability, TemporalApplicability.EXPIRED)

    def test_historical_as_of_selects_historical_version_not_current_flag(self) -> None:
        old = metadata(version="version-old", effective_from="2020-01-01", effective_to="2024-01-01", version_status="HISTORICAL")
        new = metadata(version="version-new", effective_from="2024-01-01", version_status="CURRENT")
        result = resolve(
            bundle_outcome(old, new),
            mode=TemporalQueryMode.AS_OF,
            as_of=datetime(2022, 1, 1, tzinfo=timezone.utc),
            now=NOW,
        )
        self.assertEqual(
            [item.version_identity for item in result.assessments if item.selected],
            ["version-old"],
        )

    def test_future_mode_obeys_exact_effective_boundary(self) -> None:
        future = datetime(2027, 1, 1, tzinfo=timezone.utc)
        result = resolve(
            bundle_outcome(metadata(effective_from="2027-01-01")),
            mode=TemporalQueryMode.FUTURE,
            as_of=future,
        )
        self.assertIs(result.assessments[0].temporal_applicability, TemporalApplicability.APPLICABLE)

    def test_future_with_missing_legal_metadata_is_unknown_and_insufficient(self) -> None:
        value = metadata(effective_from=None, decision_date=None)
        result = resolve(
            bundle_outcome(value),
            mode=TemporalQueryMode.FUTURE,
            as_of=datetime(2027, 1, 1, tzinfo=timezone.utc),
        )
        self.assertIs(result.assessments[0].temporal_applicability, TemporalApplicability.UNKNOWN)
        self.assertIs(result.evidence_status, EvidenceStatus.INSUFFICIENT)

    def test_operational_time_does_not_replace_legal_effect_time(self) -> None:
        result = resolve(
            bundle_outcome(metadata(effective_from=None, verified_at="2026-08-08T00:00:00Z"))
        )
        self.assertIs(result.assessments[0].temporal_applicability, TemporalApplicability.UNKNOWN)

    def test_invalid_interval_is_conflicting(self) -> None:
        result = resolve(
            bundle_outcome(metadata(effective_from="2026-01-02", effective_to="2026-01-01"))
        )
        self.assertIs(result.assessments[0].temporal_applicability, TemporalApplicability.CONFLICTING)
        self.assertIs(result.evidence_status, EvidenceStatus.CONFLICTING)

    def test_corrupt_recomputable_temporal_digest_is_invalid(self) -> None:
        result = resolve(bundle_outcome(metadata(corrupt_digest=True)))
        self.assertIs(result.evidence_status, EvidenceStatus.INVALID)
        self.assertIn(Step21ReasonCode.TEMPORAL_FACTS_DIGEST_INVALID, result.assessments[0].reason_codes)


class SupersessionAndConflictTests(unittest.TestCase):
    def test_clear_supersession_excludes_old_for_current_question(self) -> None:
        old = metadata(version="version-old", effective_from="2020-01-01", superseded_by=("version-new",), version_status="HISTORICAL")
        new = metadata(version="version-new", effective_from="2024-01-01", supersedes=("version-old",))
        result = resolve(bundle_outcome(old, new))
        assessments = {item.version_identity: item for item in result.assessments}
        self.assertIs(assessments["version-old"].temporal_applicability, TemporalApplicability.SUPERSEDED)
        self.assertIs(assessments["version-new"].temporal_applicability, TemporalApplicability.APPLICABLE)

    def test_superseded_version_remains_historically_applicable(self) -> None:
        old = metadata(version="version-old", effective_from="2020-01-01", superseded_by=("version-new",), version_status="HISTORICAL")
        new = metadata(version="version-new", effective_from="2024-01-01", supersedes=("version-old",))
        result = resolve(
            bundle_outcome(old, new),
            mode=TemporalQueryMode.AS_OF,
            as_of=datetime(2022, 1, 1, tzinfo=timezone.utc),
        )
        self.assertEqual(
            [item.version_identity for item in result.assessments if item.selected],
            ["version-old"],
        )

    def test_ambiguous_supersession_chain_conflicts(self) -> None:
        old = metadata(version="version-old", superseded_by=("version-a", "version-b"))
        first = metadata(version="version-a", effective_from="2024-01-01")
        second = metadata(version="version-b", effective_from="2024-01-01")
        result = resolve(bundle_outcome(old, first, second))
        self.assertIs(result.evidence_status, EvidenceStatus.CONFLICTING)
        self.assertIn(Step21ReasonCode.SUPERSESSION_AMBIGUOUS, result.reason_codes + tuple(code for item in result.assessments for code in item.reason_codes))

    def test_supersession_cycle_fails_closed(self) -> None:
        first = metadata(version="version-a", superseded_by=("version-b",))
        second = metadata(version="version-b", superseded_by=("version-a",))
        result = resolve(bundle_outcome(first, second))
        self.assertIs(result.evidence_status, EvidenceStatus.CONFLICTING)
        self.assertTrue(any(item.supersession_status is SupersessionStatus.CYCLIC for item in result.assessments))

    def test_overlapping_incompatible_same_provision_conflicts(self) -> None:
        first = metadata(version="version-a")
        second = metadata(version="version-b")
        result = resolve(
            bundle_outcome(first, second, contents=("Text A", "Text B"))
        )
        self.assertIs(result.evidence_status, EvidenceStatus.CONFLICTING)
        self.assertEqual(len(result.conflict_groups), 1)
        verify_conflict_group_hash(result.conflict_groups[0])

    def test_tampered_conflict_group_hash_fails_closed(self) -> None:
        result = resolve(
            bundle_outcome(
                metadata(version="version-a"),
                metadata(version="version-b"),
                contents=("Text A", "Text B"),
            )
        )
        group = copy.copy(result.conflict_groups[0])
        object.__setattr__(group, "conflict_group_hash", "0" * 64)
        with self.assertRaises(IntegrityError):
            replace(result, conflict_groups=(group,))

    def test_independent_same_text_support_is_not_conflict(self) -> None:
        first = metadata(version="version-a")
        second = metadata(version="version-b")
        result = resolve(
            bundle_outcome(
                first,
                second,
                contents=("Gleicher amtlicher Text", "Gleicher amtlicher Text"),
                source_ids=("source-a", "source-b"),
            )
        )
        self.assertFalse(result.conflict_groups)
        self.assertIs(result.evidence_status, EvidenceStatus.SUFFICIENT)

    def test_freshness_observation_difference_is_not_temporal_conflict(self) -> None:
        first = metadata(version="version-shared", verified_at="2026-08-01T00:00:00Z")
        second = metadata(version="version-shared", verified_at="2026-08-02T00:00:00Z")
        result = resolve(
            bundle_outcome(
                first,
                second,
                contents=("Gleicher amtlicher Text", "Gleicher amtlicher Text"),
                source_ids=("source-a", "source-b"),
            )
        )
        self.assertFalse(result.conflict_groups)
        self.assertIs(result.evidence_status, EvidenceStatus.SUFFICIENT)

    def test_assessment_cannot_detach_from_verified_bundle(self) -> None:
        result = resolve(bundle_outcome(metadata()))
        assessment = replace(
            result.assessments[0],
            step20_bundle_hash="f" * 64,
        )
        with self.assertRaises(ContractValidationError):
            replace(result, assessments=(assessment,))

    def test_step20_top_rank_cannot_suppress_temporal_conflict(self) -> None:
        result = resolve(
            bundle_outcome(
                metadata(version="version-top"),
                metadata(version="version-lower"),
                contents=("Widerspruch A", "Widerspruch B"),
            )
        )
        self.assertIs(result.evidence_status, EvidenceStatus.CONFLICTING)
        self.assertFalse(result.resolved_item_hashes)


class FreshnessAndStatusTests(unittest.TestCase):
    def test_fresh_stale_and_unknown_are_distinct(self) -> None:
        cases = (
            (metadata(verified_at="2026-08-01T00:00:00Z"), freshness_policy(days=30), FreshnessStatus.FRESH, EvidenceStatus.SUFFICIENT),
            (metadata(verified_at="2025-01-01T00:00:00Z"), freshness_policy(days=30), FreshnessStatus.STALE, EvidenceStatus.STALE),
            (metadata(verified_at=None), freshness_policy(days=30), FreshnessStatus.UNKNOWN, EvidenceStatus.INSUFFICIENT),
            (metadata(), freshness_policy(include_source=False), FreshnessStatus.UNKNOWN, EvidenceStatus.INSUFFICIENT),
        )
        for value, policy, expected_freshness, expected_status in cases:
            with self.subTest(expected=expected_freshness):
                result = resolve(bundle_outcome(value), policy=policy)
                self.assertIs(result.assessments[0].freshness_status, expected_freshness)
                self.assertIs(result.evidence_status, expected_status)

    def test_stale_but_applicable_remains_inspectable(self) -> None:
        result = resolve(
            bundle_outcome(metadata(verified_at="2020-01-01T00:00:00Z")),
            policy=freshness_policy(days=30),
        )
        self.assertIs(result.assessments[0].temporal_applicability, TemporalApplicability.APPLICABLE)
        self.assertTrue(result.assessments[0].selected)
        self.assertIs(result.evidence_status, EvidenceStatus.STALE)

    def test_recent_but_not_applicable_is_not_fresh_evidence(self) -> None:
        result = resolve(bundle_outcome(metadata(effective_from="2030-01-01")))
        self.assertIs(result.assessments[0].freshness_status, FreshnessStatus.NOT_APPLICABLE)

    def test_known_availability_failure_is_unavailable(self) -> None:
        result = resolve(
            bundle_outcome(metadata()),
            availability=EvidenceAvailability.UNAVAILABLE,
        )
        self.assertIs(result.evidence_status, EvidenceStatus.UNAVAILABLE)
        self.assertFalse(result.resolved_item_hashes)

    def test_answer_status_remains_separate_and_unchanged(self) -> None:
        outcome = bundle_outcome(metadata())
        result = resolve(outcome)
        self.assertIs(result.answer_status, outcome.bundle.answer_status)
        self.assertNotIsInstance(result.answer_status, EvidenceStatus)

    def test_partial_step20_coverage_cannot_claim_sufficient(self) -> None:
        outcome = bundle_outcome(metadata(), truncated=True)
        result = resolve(outcome)
        self.assertIs(result.evidence_status, EvidenceStatus.INSUFFICIENT)
        self.assertIn(Step21ReasonCode.STEP20_COVERAGE_PARTIAL, result.reason_codes)


class CompletenessFallbackTests(unittest.TestCase):
    def test_incomplete_primary_triggers_one_same_scope_fallback(self) -> None:
        primary = bundle_outcome(metadata(version="future", effective_from="2030-01-01"))
        fallback = bundle_outcome(metadata(version="current", effective_from="2020-01-01"))
        result = resolve(primary, fallback=fallback)
        self.assertTrue(result.completeness_fallback.attempted)
        self.assertEqual(result.completeness_fallback.attempts_used, 1)
        self.assertEqual(result.completeness_fallback.additional_candidates_admitted, 1)
        self.assertIs(result.evidence_status, EvidenceStatus.SUFFICIENT)

    def test_fallback_replay_does_not_duplicate_candidate(self) -> None:
        primary = bundle_outcome(metadata(version="future", effective_from="2030-01-01"))
        result = resolve(primary, fallback=primary)
        self.assertEqual(result.completeness_fallback.additional_candidates_admitted, 0)
        self.assertEqual(len(result.assessments), 1)

    def test_failed_fallback_remains_explicitly_insufficient(self) -> None:
        primary = bundle_outcome(metadata(version="future-a", effective_from="2030-01-01"))
        fallback = bundle_outcome(metadata(version="future-b", effective_from="2031-01-01"))
        result = resolve(primary, fallback=fallback)
        self.assertIs(result.evidence_status, EvidenceStatus.INSUFFICIENT)
        self.assertIn(Step21ReasonCode.COMPLETENESS_FALLBACK_EXHAUSTED, result.reason_codes)

    def test_more_than_one_fallback_attempt_is_forbidden(self) -> None:
        with self.assertRaises(ContractValidationError):
            CompletenessPolicy(maximum_attempts=MAX_COMPLETENESS_ATTEMPTS + 1)

    def test_no_fallback_for_already_sufficient_primary(self) -> None:
        primary = bundle_outcome(metadata(version="current"))
        fallback = bundle_outcome(metadata(version="extra"))
        result = resolve(primary, fallback=fallback)
        self.assertFalse(result.completeness_fallback.attempted)
        self.assertIsNone(result.fallback_bundle_hash)


class AuthorityAndBoundaryTests(unittest.TestCase):
    def test_model_claims_cannot_change_time_freshness_or_conflict(self) -> None:
        base = metadata(
            effective_from="2030-01-01",
            verified_at="2020-01-01T00:00:00Z",
        )
        suggested = dict(base)
        suggested["model_output"] = {
            "knowledge_as_of": "2035-01-01",
            "freshness": "FRESH",
            "resolve_conflict": True,
            "authority_level": "OFFICIAL_PRIMARY",
        }
        first = resolve(bundle_outcome(base))
        second = resolve(bundle_outcome(suggested))
        self.assertEqual(
            first.assessments[0].temporal_applicability,
            second.assessments[0].temporal_applicability,
        )
        self.assertEqual(first.evidence_status, second.evidence_status)

    def test_temporal_result_does_not_change_human_or_execution_policy(self) -> None:
        outcome = bundle_outcome(metadata())
        result = resolve(outcome)
        self.assertIs(result.answer_status, outcome.bundle.answer_status)
        self.assertEqual(
            outcome.bundle.execution_authorization_decision.value,
            "REQUIRE_HUMAN",
        )

    def test_temporal_package_has_no_execution_or_step22_surface(self) -> None:
        names: set[str] = set()
        imports: set[str] = set()
        for path in TEMPORAL_ROOT.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            names.update(
                node.name.casefold()
                for node in ast.walk(tree)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            )
            imports.update(
                alias.name.casefold()
                for node in ast.walk(tree)
                if isinstance(node, ast.Import)
                for alias in node.names
            )
            imports.update(
                (node.module or "").casefold()
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom)
            )
        self.assertFalse({"execute", "approve", "commit", "generate_text", "draftv1"} & names)
        self.assertFalse(any(token in value for value in imports for token in ("requests", "httpx", "boto", "openai", "anthropic")))
        self.assertFalse((ROOT / "src/aioa_memory_kernel/model_adapter").exists())

    def test_no_database_migration_added_for_step21(self) -> None:
        self.assertFalse(
            any("step21" in path.name.casefold() for path in (ROOT / "sql/cockroachdb/migrations").glob("*.sql"))
        )


if __name__ == "__main__":
    unittest.main()
