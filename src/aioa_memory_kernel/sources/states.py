"""Deterministic source-publication transitions and event-chain checks."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Iterable

from aioa_memory_kernel.contracts.serialization import canonical_sha256

from .errors import (
    PublicationEligibilityError,
    PublicationEventChainError,
    PublicationTransitionError,
)
from .models import (
    PUBLICATION_GENESIS_DIGEST,
    PublicationEligibilityDecision,
    PublicationStateEvent,
    SourcePublicationState,
    SourceRegistryActor,
    SourceRegistryRecord,
)


ALLOWED_PUBLICATION_TRANSITIONS: dict[
    SourcePublicationState,
    frozenset[SourcePublicationState],
] = {
    SourcePublicationState.REGISTERED: frozenset(
        {
            SourcePublicationState.REVIEW_REQUIRED,
            SourcePublicationState.QUARANTINED,
            SourcePublicationState.REJECTED,
        }
    ),
    SourcePublicationState.REVIEW_REQUIRED: frozenset(
        {
            SourcePublicationState.ELIGIBLE,
            SourcePublicationState.QUARANTINED,
            SourcePublicationState.REJECTED,
        }
    ),
    SourcePublicationState.ELIGIBLE: frozenset(
        {
            SourcePublicationState.PUBLISHED,
            SourcePublicationState.REVIEW_REQUIRED,
            SourcePublicationState.QUARANTINED,
        }
    ),
    SourcePublicationState.PUBLISHED: frozenset(
        {
            SourcePublicationState.WITHDRAWN,
            SourcePublicationState.QUARANTINED,
        }
    ),
    SourcePublicationState.QUARANTINED: frozenset(
        {
            SourcePublicationState.REVIEW_REQUIRED,
            SourcePublicationState.REJECTED,
        }
    ),
    SourcePublicationState.WITHDRAWN: frozenset(
        {SourcePublicationState.REVIEW_REQUIRED}
    ),
    SourcePublicationState.REJECTED: frozenset(),
}


def require_publication_transition(
    current: SourcePublicationState,
    target: SourcePublicationState,
) -> None:
    """Fail closed unless *target* is an exact declared successor."""

    if (
        not isinstance(current, SourcePublicationState)
        or not isinstance(target, SourcePublicationState)
        or target not in ALLOWED_PUBLICATION_TRANSITIONS.get(current, frozenset())
    ):
        raise PublicationTransitionError(
            "publication-state transition is not permitted",
            sanitized_code="ILLEGAL_PUBLICATION_TRANSITION",
        )


def build_publication_event(
    record: SourceRegistryRecord,
    *,
    event_id: str,
    target_state: SourcePublicationState,
    eligibility: PublicationEligibilityDecision,
    actor: SourceRegistryActor,
    reason_codes: tuple[str, ...],
    reviewer_reference: str | None,
    created_at: datetime,
) -> PublicationStateEvent:
    """Create one event bound to the current optimistic state."""

    if not isinstance(actor, SourceRegistryActor):
        raise PublicationTransitionError(
            "publication event requires a trusted typed actor",
            sanitized_code="UNTRUSTED_PUBLICATION_ACTOR",
        )
    require_publication_transition(record.current_publication_state, target_state)
    if (
        eligibility.registry_digest != record.registry_digest
        or eligibility.scope_digest != record.scope.scope_digest
        or eligibility.lineage_terminal_digest
        != record.artifact.artifact_digest
        or eligibility.snapshot_id != record.snapshot_id
        or eligibility.knowledge_version_id != record.knowledge_version_id
    ):
        raise PublicationEligibilityError(
            "eligibility decision is not bound to the current registry facts",
            sanitized_code="STALE_PUBLICATION_ELIGIBILITY",
        )
    if target_state in {
        SourcePublicationState.ELIGIBLE,
        SourcePublicationState.PUBLISHED,
    } and not eligibility.eligible:
        raise PublicationEligibilityError(
            "target publication state requires an eligible decision",
            sanitized_code="PUBLICATION_NOT_ELIGIBLE",
        )
    return PublicationStateEvent(
        tenant_id=record.tenant_id,
        source_id=record.source_id,
        hat_scope_id=record.hat_scope_id,
        event_id=event_id,
        sequence_number=record.current_publication_sequence + 1,
        from_state=record.current_publication_state,
        to_state=target_state,
        actor_type=actor.actor_type,
        actor_reference=actor.actor_reference,
        policy_version=eligibility.policy_version,
        eligibility_decision_digest=eligibility.decision_digest,
        reason_codes=reason_codes,
        reviewer_reference=reviewer_reference,
        previous_event_digest=record.current_publication_event_digest,
        created_at=created_at,
    )


def advance_registry_state(
    record: SourceRegistryRecord,
    event: PublicationStateEvent,
) -> SourceRegistryRecord:
    """Apply an already-validated event as an optimistic in-memory CAS."""

    if (
        event.tenant_id,
        event.source_id,
        event.hat_scope_id,
    ) != (record.tenant_id, record.source_id, record.hat_scope_id):
        raise PublicationTransitionError(
            "event and registry identities differ",
            sanitized_code="PUBLICATION_EVENT_SCOPE_MISMATCH",
        )
    require_publication_transition(
        record.current_publication_state,
        event.to_state,
    )
    if (
        event.from_state is not record.current_publication_state
        or event.sequence_number != record.current_publication_sequence + 1
        or event.previous_event_digest
        != record.current_publication_event_digest
    ):
        raise PublicationTransitionError(
            "publication event observed stale registry state",
            sanitized_code="STALE_PUBLICATION_STATE",
        )
    return replace(
        record,
        current_publication_state=event.to_state,
        current_publication_sequence=event.sequence_number,
        current_publication_event_digest=event.event_digest,
        updated_at=event.created_at,
    )


def _event_digest(event: PublicationStateEvent) -> str:
    return canonical_sha256(event, exclude_fields=("event_digest",))


def verify_publication_event_chain(
    record: SourceRegistryRecord,
    events: Iterable[PublicationStateEvent],
) -> tuple[PublicationStateEvent, ...]:
    """Verify ordering, digests, links, and the registry terminal pointer."""

    ordered = tuple(sorted(events, key=lambda event: event.sequence_number))
    if not ordered:
        if (
            record.current_publication_sequence != 0
            or record.current_publication_state
            is not SourcePublicationState.REGISTERED
            or record.current_publication_event_digest
            != PUBLICATION_GENESIS_DIGEST
        ):
            raise PublicationEventChainError(
                "registry terminal state has no publication events",
                sanitized_code="PUBLICATION_EVENT_CHAIN_MISSING",
            )
        return ordered

    previous_digest = PUBLICATION_GENESIS_DIGEST
    previous_state = SourcePublicationState.REGISTERED
    seen_event_ids: set[str] = set()
    seen_digests: set[str] = set()
    for expected_sequence, event in enumerate(ordered, start=1):
        if (
            event.tenant_id,
            event.source_id,
            event.hat_scope_id,
        ) != (record.tenant_id, record.source_id, record.hat_scope_id):
            raise PublicationEventChainError(
                "publication event crosses a registry scope",
                sanitized_code="PUBLICATION_EVENT_SCOPE_MISMATCH",
            )
        if event.sequence_number != expected_sequence:
            raise PublicationEventChainError(
                "publication event sequence contains a gap or duplicate",
                sanitized_code="PUBLICATION_EVENT_SEQUENCE_INVALID",
            )
        if event.event_id in seen_event_ids or event.event_digest in seen_digests:
            raise PublicationEventChainError(
                "publication event identity is duplicated",
                sanitized_code="PUBLICATION_EVENT_DUPLICATE",
            )
        if event.previous_event_digest != previous_digest:
            raise PublicationEventChainError(
                "publication event previous digest does not match",
                sanitized_code="PUBLICATION_EVENT_LINK_INVALID",
            )
        if event.from_state is not previous_state:
            raise PublicationEventChainError(
                "publication event state chain does not match",
                sanitized_code="PUBLICATION_EVENT_STATE_INVALID",
            )
        require_publication_transition(event.from_state, event.to_state)
        if _event_digest(event) != event.event_digest:
            raise PublicationEventChainError(
                "publication event digest is invalid",
                sanitized_code="PUBLICATION_EVENT_DIGEST_INVALID",
            )
        seen_event_ids.add(event.event_id)
        seen_digests.add(event.event_digest)
        previous_digest = event.event_digest
        previous_state = event.to_state

    if (
        record.current_publication_sequence != len(ordered)
        or record.current_publication_state is not previous_state
        or record.current_publication_event_digest != previous_digest
    ):
        raise PublicationEventChainError(
            "registry current state differs from the event-chain terminal",
            sanitized_code="PUBLICATION_TERMINAL_MISMATCH",
        )
    return ordered
