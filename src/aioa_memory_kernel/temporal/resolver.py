"""Pure question-time temporal applicability for Step 21."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timezone

from aioa_memory_kernel.contracts.enums import MemoryTargetScope
from aioa_memory_kernel.contracts.serialization import canonical_sha256, ensure_utc
from aioa_memory_kernel.evidence import (
    EvidenceBundleItem,
    FrozenEvidenceBundle,
    verify_bundle_item_hash,
)
from aioa_memory_kernel.sources import (
    SourceAccessClass,
    SourceAuthorityLevel,
    SourcePublicationState,
)

from .models import (
    TEMPORAL_FACTS_DIGEST_SCHEME,
    FreshnessStatus,
    Step21ReasonCode,
    SupersessionStatus,
    TemporalApplicability,
    TemporalBoundaryError,
    TemporalFacts,
)


_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIME_FIELDS = (
    "published_at",
    "promulgated_at",
    "adopted_at",
    "effective_from",
    "effective_to",
    "applicable_from",
    "applicable_to",
    "decision_date",
    "retrieved_at",
    "ingested_at",
    "verified_at",
    "source_observed_at",
    "snapshot_captured_at",
    "superseded_at",
)


@dataclass(frozen=True, slots=True)
class CandidateTemporalState:
    bundle_hash: str
    item: EvidenceBundleItem
    facts: TemporalFacts
    logical_subject_identity: str
    applicability: TemporalApplicability
    supersession_status: SupersessionStatus
    freshness_status: FreshnessStatus
    conflict_group_id: str | None
    integrity_valid: bool
    reasons: tuple[Step21ReasonCode, ...]

    @property
    def version_identity(self) -> str:
        return self.facts.version_identity or self.item.identity.knowledge_version_id


def _instant(value: object, field_name: str) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return ensure_utc(value, field_name)
    if not isinstance(value, str) or value != value.strip():
        raise ValueError(f"{field_name} is not a canonical timestamp")
    if _DATE.fullmatch(value):
        return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(candidate)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} lacks an explicit timezone")
    return parsed.astimezone(timezone.utc)


def _relations(value: object) -> tuple[str, ...]:
    if value in (None, ""):
        return ()
    if isinstance(value, str):
        values = (value,)
    elif isinstance(value, (tuple, list)):
        values = tuple(value)
    else:
        raise ValueError("supersession relation must be text or an ordered list")
    if any(not isinstance(item, str) or not item.strip() for item in values):
        raise ValueError("supersession relation contains an invalid identity")
    return tuple(sorted(set(values)))


def _metadata_mapping(item: EvidenceBundleItem) -> Mapping[str, object]:
    nested = item.structured_metadata.get("temporal_facts")
    if nested is None:
        return item.structured_metadata
    if not isinstance(nested, Mapping):
        raise ValueError("temporal_facts must be a mapping")
    return nested


def extract_temporal_facts(
    item: EvidenceBundleItem,
) -> tuple[TemporalFacts, tuple[Step21ReasonCode, ...], bool]:
    """Decode only explicit, bounded temporal metadata without inference."""

    metadata = item.structured_metadata
    try:
        temporal = _metadata_mapping(item)
        values = {name: _instant(temporal.get(name), name) for name in _TIME_FIELDS}
        source_digest = metadata.get(
            "temporal_facts_digest",
            temporal.get("temporal_facts_digest"),
        )
        digest_scheme = metadata.get(
            "temporal_facts_digest_scheme",
            temporal.get("temporal_facts_digest_scheme"),
        )
        facts = TemporalFacts(
            **values,
            version_status=(
                str(temporal["version_status"])
                if temporal.get("version_status") not in (None, "")
                else str(metadata["version_status"])
                if metadata.get("version_status") not in (None, "")
                else None
            ),
            consolidation_status=(
                str(temporal["consolidation_status"])
                if temporal.get("consolidation_status") not in (None, "")
                else str(metadata["consolidation_status"])
                if metadata.get("consolidation_status") not in (None, "")
                else None
            ),
            document_identity=(
                str(metadata["document_identity"])
                if metadata.get("document_identity") not in (None, "")
                else None
            ),
            version_identity=(
                str(metadata["version_identity"])
                if metadata.get("version_identity") not in (None, "")
                else None
            ),
            official_identifier=(
                str(metadata["official_identifier"])
                if metadata.get("official_identifier") not in (None, "")
                else None
            ),
            provision_identifier=(
                str(metadata["provision_identifier"])
                if metadata.get("provision_identifier") not in (None, "")
                else None
            ),
            supersedes=_relations(temporal.get("supersedes", metadata.get("supersedes"))),
            superseded_by=_relations(
                temporal.get("superseded_by", metadata.get("superseded_by"))
            ),
            source_temporal_facts_digest=(
                str(source_digest) if source_digest not in (None, "") else None
            ),
            source_digest_scheme=(
                str(digest_scheme) if digest_scheme not in (None, "") else None
            ),
        )
    except (TypeError, ValueError) as exc:
        del exc
        fallback = TemporalFacts(
            document_identity=(
                str(metadata["document_identity"])
                if isinstance(metadata.get("document_identity"), str)
                else None
            ),
            version_identity=(
                str(metadata["version_identity"])
                if isinstance(metadata.get("version_identity"), str)
                else None
            ),
            official_identifier=(
                str(metadata["official_identifier"])
                if isinstance(metadata.get("official_identifier"), str)
                else None
            ),
            provision_identifier=(
                str(metadata["provision_identifier"])
                if isinstance(metadata.get("provision_identifier"), str)
                else None
            ),
        )
        return fallback, (Step21ReasonCode.TEMPORAL_FACTS_INVALID,), False

    if facts.source_digest_scheme == TEMPORAL_FACTS_DIGEST_SCHEME:
        if facts.source_temporal_facts_digest != facts.facts_hash:
            return facts, (Step21ReasonCode.TEMPORAL_FACTS_DIGEST_INVALID,), False
        digest_reasons: tuple[Step21ReasonCode, ...] = ()
    elif facts.source_temporal_facts_digest is not None:
        # Step 15 hashes the ordered normalization-record digests.  A flattened
        # Step 20 projection cannot recompute that preimage, so preserve it
        # without claiming verification.
        digest_reasons = (Step21ReasonCode.TEMPORAL_FACTS_DIGEST_PRESERVED,)
    else:
        digest_reasons = ()
    return facts, digest_reasons, True


def logical_subject_identity(item: EvidenceBundleItem, facts: TemporalFacts) -> str:
    document = (
        facts.document_identity
        or facts.official_identifier
        or item.identity.source_id
    )
    provision = facts.provision_identifier or item.identity.chunk_id
    digest = canonical_sha256(
        {
            "document_identity": document,
            "provision_identity": provision,
            "effective_scope": item.effective_scope,
        }
    )
    return f"temporal-subject:{digest}"


def _base_applicability(
    facts: TemporalFacts,
    as_of: datetime,
) -> tuple[TemporalApplicability, SupersessionStatus, tuple[Step21ReasonCode, ...]]:
    intervals = (
        (facts.effective_from, facts.effective_to),
        (facts.applicable_from, facts.applicable_to),
    )
    if any(start is not None and end is not None and start > end for start, end in intervals):
        return (
            TemporalApplicability.CONFLICTING,
            SupersessionStatus.AMBIGUOUS,
            (Step21ReasonCode.TEMPORAL_FACTS_INVALID,),
        )
    starts = tuple(value for value in (facts.effective_from, facts.applicable_from) if value is not None)
    ends = tuple(value for value in (facts.effective_to, facts.applicable_to) if value is not None)
    if starts and as_of < max(starts):
        return (
            TemporalApplicability.NOT_YET_APPLICABLE,
            SupersessionStatus.NOT_SUPERSEDED,
            (Step21ReasonCode.NOT_YET_EFFECTIVE,),
        )
    if ends and as_of >= min(ends):
        return (
            TemporalApplicability.EXPIRED,
            SupersessionStatus.NOT_SUPERSEDED,
            (Step21ReasonCode.EFFECTIVE_PERIOD_EXPIRED,),
        )
    if facts.superseded_at is not None and as_of >= facts.superseded_at:
        return (
            TemporalApplicability.SUPERSEDED,
            SupersessionStatus.SUPERSEDED,
            (Step21ReasonCode.SUPERSEDED_AT_AS_OF,),
        )
    status = (facts.version_status or "").upper()
    if status in {"REPEALED", "EXPIRED"}:
        if facts.superseded_at is None:
            return (
                TemporalApplicability.UNKNOWN,
                SupersessionStatus.UNKNOWN,
                (Step21ReasonCode.TEMPORAL_FACTS_MISSING,),
            )
    if status == "SUPERSEDED" and facts.superseded_at is None and not facts.superseded_by:
        return (
            TemporalApplicability.UNKNOWN,
            SupersessionStatus.UNKNOWN,
            (Step21ReasonCode.TEMPORAL_FACTS_MISSING,),
        )
    if not starts and not ends and facts.decision_date is None:
        return (
            TemporalApplicability.UNKNOWN,
            SupersessionStatus.UNKNOWN,
            (Step21ReasonCode.TEMPORAL_FACTS_MISSING,),
        )
    if facts.decision_date is not None and as_of < facts.decision_date:
        return (
            TemporalApplicability.NOT_YET_APPLICABLE,
            SupersessionStatus.NOT_SUPERSEDED,
            (Step21ReasonCode.NOT_YET_EFFECTIVE,),
        )
    return (
        TemporalApplicability.APPLICABLE,
        SupersessionStatus.NOT_SUPERSEDED,
        (Step21ReasonCode.EFFECTIVE_AT_AS_OF,),
    )


def assess_bundle_item(
    bundle: FrozenEvidenceBundle,
    item: EvidenceBundleItem,
    *,
    as_of: datetime,
) -> CandidateTemporalState:
    """Revalidate one Step 20 item and calculate base applicability."""

    verify_bundle_item_hash(item)
    if (
        item.identity.tenant_id != bundle.tenant_id
        or item.identity.hat_scope_id != bundle.hat_scope_id
        or item.effective_scope != bundle.effective_scope
        or item.publication_state is not SourcePublicationState.PUBLISHED
        or item.authority_level
        not in {
            SourceAuthorityLevel.OFFICIAL_PRIMARY,
            SourceAuthorityLevel.AUTHORITATIVE_SECONDARY,
        }
    ):
        raise TemporalBoundaryError(Step21ReasonCode.STEP20_INPUT_BINDING_MISMATCH)
    if item.access_class is SourceAccessClass.USER_PRIVATE:
        if (
            item.owner_user_id != bundle.user_id
            or item.target_scope is not MemoryTargetScope.USER_PERSONAL_HAT
            or item.personal_memory_space_id is None
        ):
            raise TemporalBoundaryError(Step21ReasonCode.STEP20_INPUT_BINDING_MISMATCH)
    elif (
        item.owner_user_id is not None
        or item.personal_memory_space_id is not None
        or item.target_scope is not MemoryTargetScope.SHARED_KNOWLEDGE_HAT
    ):
        raise TemporalBoundaryError(Step21ReasonCode.STEP20_INPUT_BINDING_MISMATCH)
    facts, digest_reasons, integrity_valid = extract_temporal_facts(item)
    subject = logical_subject_identity(item, facts)
    if not integrity_valid:
        applicability = TemporalApplicability.UNKNOWN
        supersession = SupersessionStatus.UNKNOWN
        base_reasons = digest_reasons
    else:
        applicability, supersession, base_reasons = _base_applicability(
            facts,
            ensure_utc(as_of, "as_of"),
        )
    reasons = tuple(sorted(set((*digest_reasons, *base_reasons)), key=lambda value: value.value))
    return CandidateTemporalState(
        bundle_hash=bundle.bundle_hash,
        item=item,
        facts=facts,
        logical_subject_identity=subject,
        applicability=applicability,
        supersession_status=supersession,
        freshness_status=FreshnessStatus.NOT_APPLICABLE,
        conflict_group_id=None,
        integrity_valid=integrity_valid,
        reasons=reasons,
    )


def update_state(
    state: CandidateTemporalState,
    *,
    applicability: TemporalApplicability | None = None,
    supersession_status: SupersessionStatus | None = None,
    freshness_status: FreshnessStatus | None = None,
    conflict_group_id: str | None | object = ...,
    reasons: tuple[Step21ReasonCode, ...] = (),
) -> CandidateTemporalState:
    values: dict[str, object] = {
        "applicability": applicability or state.applicability,
        "supersession_status": supersession_status or state.supersession_status,
        "freshness_status": freshness_status or state.freshness_status,
        "reasons": tuple(
            sorted(set((*state.reasons, *reasons)), key=lambda value: value.value)
        ),
    }
    if conflict_group_id is not ...:
        values["conflict_group_id"] = conflict_group_id
    return replace(state, **values)


__all__ = [
    "CandidateTemporalState",
    "assess_bundle_item",
    "extract_temporal_facts",
    "logical_subject_identity",
    "update_state",
]
