"""Policy-driven freshness assessment, separate from legal applicability."""

from __future__ import annotations

from datetime import datetime, timedelta

from aioa_memory_kernel.contracts.serialization import ensure_utc

from .models import (
    FreshnessPolicy,
    FreshnessStatus,
    Step21ReasonCode,
    TemporalApplicability,
)
from .resolver import CandidateTemporalState, update_state


def evaluate_freshness(
    state: CandidateTemporalState,
    policy: FreshnessPolicy,
    *,
    trusted_now: datetime,
) -> CandidateTemporalState:
    """Evaluate operational freshness without changing applicability."""

    if state.applicability is not TemporalApplicability.APPLICABLE:
        return update_state(state, freshness_status=FreshnessStatus.NOT_APPLICABLE)
    threshold = policy.maximum_age_seconds_by_source_kind.get(
        state.item.source_kind
    )
    if threshold is None:
        return update_state(
            state,
            freshness_status=FreshnessStatus.UNKNOWN,
            reasons=(Step21ReasonCode.FRESHNESS_POLICY_MISSING,),
        )
    observation = None
    for field_name in policy.observation_precedence:
        value = getattr(state.facts, field_name)
        if value is not None:
            observation = value
            break
    if observation is None:
        return update_state(
            state,
            freshness_status=FreshnessStatus.UNKNOWN,
            reasons=(Step21ReasonCode.FRESHNESS_FACT_MISSING,),
        )
    now = ensure_utc(trusted_now, "trusted_now")
    if observation > now:
        return update_state(
            state,
            freshness_status=FreshnessStatus.UNKNOWN,
            reasons=(Step21ReasonCode.FRESHNESS_FACT_IN_FUTURE,),
        )
    if now - observation > timedelta(seconds=threshold):
        return update_state(
            state,
            freshness_status=FreshnessStatus.STALE,
            reasons=(Step21ReasonCode.EVIDENCE_STALE,),
        )
    return update_state(
        state,
        freshness_status=FreshnessStatus.FRESH,
        reasons=(Step21ReasonCode.EVIDENCE_FRESH,),
    )


__all__ = ["evaluate_freshness"]
