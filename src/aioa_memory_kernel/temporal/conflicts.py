"""Deterministic supersession graph and material conflict detection."""

from __future__ import annotations

from collections import defaultdict

from .models import (
    Step21ReasonCode,
    SupersessionStatus,
    TemporalApplicability,
    TemporalConflictGroup,
)
from .resolver import CandidateTemporalState, update_state


def _cycle_nodes(edges: dict[str, set[str]]) -> set[str]:
    visiting: set[str] = set()
    visited: set[str] = set()
    cyclic: set[str] = set()

    def visit(node: str, path: tuple[str, ...]) -> None:
        if node in visiting:
            if node in path:
                cyclic.update(path[path.index(node) :])
            cyclic.add(node)
            return
        if node in visited:
            return
        visiting.add(node)
        for target in sorted(edges.get(node, ())):
            visit(target, (*path, node))
        visiting.remove(node)
        visited.add(node)

    for node in sorted(edges):
        visit(node, ())
    return cyclic


def _conflict_group(
    logical_subject_identity: str,
    item_hashes: tuple[str, ...],
    reason: Step21ReasonCode,
) -> TemporalConflictGroup | None:
    unique = tuple(sorted(set(item_hashes)))
    if len(unique) < 2:
        return None
    return TemporalConflictGroup(
        logical_subject_identity=logical_subject_identity,
        candidate_item_hashes=unique,
        reason_codes=(reason,),
    )


def apply_supersession_and_conflicts(
    values: tuple[CandidateTemporalState, ...],
) -> tuple[tuple[CandidateTemporalState, ...], tuple[TemporalConflictGroup, ...]]:
    """Apply only explicit graph facts, then preserve material conflicts."""

    states = {value.item.item_hash: value for value in values}
    by_version: dict[str, list[CandidateTemporalState]] = defaultdict(list)
    edges: dict[str, set[str]] = defaultdict(set)
    for value in values:
        by_version[value.version_identity].append(value)
        for successor in value.facts.superseded_by:
            edges[value.version_identity].add(successor)
        for predecessor in value.facts.supersedes:
            edges[predecessor].add(value.version_identity)

    groups: dict[str, TemporalConflictGroup] = {}

    def mark(
        affected: tuple[CandidateTemporalState, ...],
        *,
        reason: Step21ReasonCode,
        supersession: SupersessionStatus,
        logical_subject: str,
    ) -> None:
        group = _conflict_group(
            logical_subject,
            tuple(item.item.item_hash for item in affected),
            reason,
        )
        if group is not None:
            groups[group.conflict_group_hash] = group
        for item in affected:
            states[item.item.item_hash] = update_state(
                states[item.item.item_hash],
                applicability=TemporalApplicability.CONFLICTING,
                supersession_status=supersession,
                conflict_group_id=(group.conflict_group_id if group else None),
                reasons=(reason, Step21ReasonCode.MATERIAL_CONFLICT),
            )

    cyclic_versions = _cycle_nodes(edges)
    if cyclic_versions:
        affected = tuple(
            item
            for version in sorted(cyclic_versions)
            for item in by_version.get(version, ())
        )
        if affected:
            mark(
                affected,
                reason=Step21ReasonCode.SUPERSESSION_CYCLE,
                supersession=SupersessionStatus.CYCLIC,
                logical_subject="supersession-cycle",
            )

    for predecessor, successors in sorted(edges.items()):
        predecessor_items = tuple(by_version.get(predecessor, ()))
        if not predecessor_items:
            continue
        if len(successors) > 1:
            affected = predecessor_items + tuple(
                item
                for successor in sorted(successors)
                for item in by_version.get(successor, ())
            )
            mark(
                affected,
                reason=Step21ReasonCode.SUPERSESSION_AMBIGUOUS,
                supersession=SupersessionStatus.AMBIGUOUS,
                logical_subject=predecessor_items[0].logical_subject_identity,
            )
            continue
        successor = next(iter(successors))
        successor_items = tuple(by_version.get(successor, ()))
        if not successor_items:
            for item in predecessor_items:
                states[item.item.item_hash] = update_state(
                    states[item.item.item_hash],
                    applicability=TemporalApplicability.UNKNOWN,
                    supersession_status=SupersessionStatus.UNKNOWN,
                    reasons=(Step21ReasonCode.SUPERSESSION_AMBIGUOUS,),
                )
            continue
        successor_is_applicable = any(
            states[item.item.item_hash].applicability
            is TemporalApplicability.APPLICABLE
            for item in successor_items
        )
        if successor_is_applicable:
            for item in predecessor_items:
                current = states[item.item.item_hash]
                if current.applicability is TemporalApplicability.APPLICABLE:
                    states[item.item.item_hash] = update_state(
                        current,
                        applicability=TemporalApplicability.SUPERSEDED,
                        supersession_status=SupersessionStatus.SUPERSEDED,
                        reasons=(Step21ReasonCode.SUPERSEDED_AT_AS_OF,),
                    )

    # One immutable version identity cannot legitimately carry incompatible
    # typed temporal facts at the same boundary.
    for version, version_items in sorted(by_version.items()):
        current = tuple(states[item.item.item_hash] for item in version_items)
        if len({item.facts.applicability_facts_hash for item in current}) > 1:
            mark(
                current,
                reason=Step21ReasonCode.TEMPORAL_FACTS_DIGEST_INVALID,
                supersession=SupersessionStatus.AMBIGUOUS,
                logical_subject=current[0].logical_subject_identity,
            )

    # Multiple applicable texts are a conflict only when they describe the
    # same exact logical document/provision scope. Identical content is
    # independent support, not a conflict.
    by_subject: dict[str, list[CandidateTemporalState]] = defaultdict(list)
    for item_hash in tuple(states):
        state = states[item_hash]
        if state.applicability is TemporalApplicability.APPLICABLE:
            by_subject[state.logical_subject_identity].append(state)
    for subject, subject_items in sorted(by_subject.items()):
        if len(subject_items) < 2:
            continue
        if len({item.item.identity.content_sha256 for item in subject_items}) > 1:
            mark(
                tuple(subject_items),
                reason=Step21ReasonCode.MATERIAL_CONFLICT,
                supersession=SupersessionStatus.AMBIGUOUS,
                logical_subject=subject,
            )
        else:
            for item in subject_items:
                states[item.item.item_hash] = update_state(
                    states[item.item.item_hash],
                    reasons=(Step21ReasonCode.INDEPENDENT_SUPPORT,),
                )

    ordered = tuple(states[value.item.item_hash] for value in values)
    return ordered, tuple(sorted(groups.values(), key=lambda item: item.conflict_group_id))


__all__ = ["apply_supersession_and_conflicts"]
