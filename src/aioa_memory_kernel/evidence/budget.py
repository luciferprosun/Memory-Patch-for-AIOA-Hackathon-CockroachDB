"""Provider-neutral UTF-8 byte budgeting for Step 20 evidence excerpts."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from .models import (
    MAX_CONTEXT_BUDGET_BYTES,
    MAX_EXCERPT_BYTES_PER_ITEM,
    MIN_PARTIAL_EXCERPT_BYTES,
    EvidenceBundleItem,
    EvidenceExcerpt,
    HybridCandidate,
    Step20ReasonCode,
    citation_reference_for,
    evidence_id_for,
    load_ranking_policy,
)


@dataclass(frozen=True, slots=True)
class BudgetAssembly:
    items: tuple[EvidenceBundleItem, ...]
    context_bytes_used: int
    excluded_counts: Mapping[str, int]
    truncated: bool


def utf8_safe_prefix(value: str, maximum_bytes: int) -> str:
    """Return the longest deterministic prefix that is valid UTF-8."""

    if not isinstance(value, str):
        raise TypeError("value must be text")
    if not isinstance(maximum_bytes, int) or isinstance(maximum_bytes, bool):
        raise TypeError("maximum_bytes must be an integer")
    if maximum_bytes < 0:
        raise ValueError("maximum_bytes must be non-negative")
    payload = value.encode("utf-8")
    if len(payload) <= maximum_bytes:
        return value
    return payload[:maximum_bytes].decode("utf-8", errors="ignore")


def assemble_budgeted_items(
    candidates: tuple[HybridCandidate, ...],
    *,
    context_budget_bytes: int,
) -> BudgetAssembly:
    if (
        not isinstance(context_budget_bytes, int)
        or isinstance(context_budget_bytes, bool)
        or not 1 <= context_budget_bytes <= MAX_CONTEXT_BUDGET_BYTES
    ):
        raise ValueError("context budget exceeds Step 20 bounds")
    policy = load_ranking_policy()
    items: list[EvidenceBundleItem] = []
    excluded: dict[str, int] = defaultdict(int)
    used = 0
    for candidate in candidates:
        payload = candidate.content.encode("utf-8")
        remaining = context_budget_bytes - used
        allowed = min(MAX_EXCERPT_BYTES_PER_ITEM, remaining)
        if len(payload) <= allowed:
            excerpt_text = candidate.content
        else:
            excerpt_text = utf8_safe_prefix(candidate.content, allowed)
            if len(excerpt_text.encode("utf-8")) < MIN_PARTIAL_EXCERPT_BYTES:
                excluded[Step20ReasonCode.CONTEXT_BUDGET_EXCLUDED.value] += 1
                continue
        excerpt_payload = excerpt_text.encode("utf-8")
        truncated = len(excerpt_payload) < len(payload)
        if truncated:
            excluded[Step20ReasonCode.CONTEXT_EXCERPT_TRUNCATED.value] += 1
        excerpt = EvidenceExcerpt(
            text=excerpt_text,
            full_content_sha256=candidate.identity.content_sha256,
            start_byte=0,
            end_byte=len(excerpt_payload),
            utf8_byte_length=len(excerpt_payload),
            excerpt_sha256=hashlib.sha256(excerpt_payload).hexdigest(),
            truncated=truncated,
        )
        items.append(
            EvidenceBundleItem(
                item_ordinal=len(items) + 1,
                evidence_id=evidence_id_for(candidate.identity),
                identity=candidate.identity,
                citation_reference=citation_reference_for(
                    candidate.identity,
                    candidate.source_reference,
                ),
                excerpt=excerpt,
                authority_level=candidate.authority_level,
                authority_basis=candidate.authority_basis,
                source_kind=candidate.source_kind,
                source_reference=candidate.source_reference,
                publication_state=candidate.publication_state,
                access_class=candidate.access_class,
                target_scope=candidate.target_scope,
                owner_user_id=candidate.owner_user_id,
                personal_memory_space_id=candidate.personal_memory_space_id,
                scope_digest=candidate.scope_digest,
                registry_digest=candidate.registry_digest,
                artifact_digest=candidate.artifact_digest,
                snapshot_id=candidate.snapshot_id,
                structured_metadata=candidate.structured_metadata,
                effective_scope=candidate.effective_scope,
                contributions=candidate.contributions,
                match_class=candidate.match_class,
                fused_score=candidate.fused_score,
                ranking_policy_id=policy.policy_id,
                ranking_policy_version=policy.policy_version,
                ranking_policy_digest=policy.policy_digest,
            )
        )
        used += len(excerpt_payload)
    frozen_excluded = MappingProxyType(dict(sorted(excluded.items())))
    return BudgetAssembly(
        items=tuple(items),
        context_bytes_used=used,
        excluded_counts=frozen_excluded,
        truncated=bool(frozen_excluded),
    )


__all__ = ["BudgetAssembly", "assemble_budgeted_items", "utf8_safe_prefix"]
