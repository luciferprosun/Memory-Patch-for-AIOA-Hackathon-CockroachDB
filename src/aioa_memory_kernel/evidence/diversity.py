"""Deterministic, bounded Step 20 source/version diversity."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from .fusion import candidate_rank_key
from .models import (
    HybridCandidate,
    MAX_BUNDLE_ITEMS,
    MAX_EXACT_PRIORITY_ITEMS,
    MAX_ITEMS_PER_KNOWLEDGE_VERSION,
    MAX_ITEMS_PER_SOURCE,
    Step20ReasonCode,
)


@dataclass(frozen=True, slots=True)
class DiversitySelection:
    selected: tuple[HybridCandidate, ...]
    excluded_counts: Mapping[str, int]
    truncated: bool


def select_diverse_candidates(
    ranked_candidates: tuple[HybridCandidate, ...],
    *,
    maximum_items: int = MAX_BUNDLE_ITEMS,
) -> DiversitySelection:
    """Keep exact evidence first, then round-robin stable source groups."""

    if not isinstance(maximum_items, int) or isinstance(maximum_items, bool):
        raise TypeError("maximum_items must be an integer")
    if not 1 <= maximum_items <= MAX_BUNDLE_ITEMS:
        raise ValueError("maximum_items exceeds the fixed Step 20 policy")
    ordered = tuple(sorted(ranked_candidates, key=candidate_rank_key))
    selected: list[HybridCandidate] = []
    source_counts: dict[str, int] = defaultdict(int)
    version_counts: dict[str, int] = defaultdict(int)
    excluded: dict[str, int] = defaultdict(int)

    exact = tuple(candidate for candidate in ordered if candidate.match_class in {0, 1})
    remaining = tuple(candidate for candidate in ordered if candidate.match_class not in {0, 1})

    def admit(candidate: HybridCandidate) -> bool:
        if source_counts[candidate.identity.source_id] >= MAX_ITEMS_PER_SOURCE:
            excluded[Step20ReasonCode.DIVERSITY_SOURCE_CAP.value] += 1
            return False
        if version_counts[candidate.identity.knowledge_version_id] >= MAX_ITEMS_PER_KNOWLEDGE_VERSION:
            excluded[Step20ReasonCode.DIVERSITY_VERSION_CAP.value] += 1
            return False
        if len(selected) >= maximum_items:
            excluded[Step20ReasonCode.DIVERSITY_GLOBAL_LIMIT.value] += 1
            return False
        selected.append(candidate)
        source_counts[candidate.identity.source_id] += 1
        version_counts[candidate.identity.knowledge_version_id] += 1
        return True

    for index, candidate in enumerate(exact):
        if index >= MAX_EXACT_PRIORITY_ITEMS:
            excluded[Step20ReasonCode.DIVERSITY_EXACT_CAP.value] += 1
            continue
        admit(candidate)

    grouped: dict[str, list[HybridCandidate]] = {}
    for candidate in remaining:
        grouped.setdefault(candidate.identity.source_id, []).append(candidate)
    source_order = tuple(
        sorted(
            grouped,
            key=lambda source_id: candidate_rank_key(grouped[source_id][0]),
        )
    )
    offsets = {source_id: 0 for source_id in source_order}
    while len(selected) < maximum_items:
        progressed = False
        for source_id in source_order:
            offset = offsets[source_id]
            group = grouped[source_id]
            if offset >= len(group):
                continue
            candidate = group[offset]
            offsets[source_id] = offset + 1
            progressed = True
            admit(candidate)
            if len(selected) >= maximum_items:
                break
        if not progressed:
            break

    if len(selected) >= maximum_items:
        for source_id in source_order:
            count = len(grouped[source_id]) - offsets[source_id]
            if count > 0:
                excluded[Step20ReasonCode.DIVERSITY_GLOBAL_LIMIT.value] += count
    frozen_excluded = MappingProxyType(dict(sorted(excluded.items())))
    return DiversitySelection(
        selected=tuple(selected),
        excluded_counts=frozen_excluded,
        truncated=bool(frozen_excluded),
    )


__all__ = ["DiversitySelection", "select_diverse_candidates"]
