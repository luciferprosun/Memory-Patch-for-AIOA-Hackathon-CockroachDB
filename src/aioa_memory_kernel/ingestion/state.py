"""Canonical Step 10 milestone transitions and event-chain verification."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Iterable

from aioa_memory_kernel.contracts.serialization import canonical_sha256

from .errors import IngestionTransitionError
from .models import (
    INGESTION_GENESIS_DIGEST,
    MILESTONE_ORDER,
    IngestionSaga,
    SagaExecutionDisposition,
    SagaMilestone,
    SagaTransitionEvent,
)


REQUIRED_RECEIPT_COUNTS: dict[SagaMilestone, int] = {
    SagaMilestone.ACQUIRED_LOCAL: 1,
    SagaMilestone.HASH_VERIFIED: 1,
    SagaMilestone.SNAPSHOT_UPLOAD_PENDING: 1,
    SagaMilestone.SNAPSHOT_UPLOADED: 1,
    SagaMilestone.SNAPSHOT_LOCK_VERIFIED: 1,
    SagaMilestone.PARSED: 1,
    SagaMilestone.VALIDATED: 1,
    SagaMilestone.PUBLISHED: 1,
}


def next_milestone(current: SagaMilestone) -> SagaMilestone | None:
    if not isinstance(current, SagaMilestone):
        return None
    index = MILESTONE_ORDER.index(current)
    if index == len(MILESTONE_ORDER) - 1:
        return None
    return MILESTONE_ORDER[index + 1]


def require_milestone_transition(
    current: SagaMilestone,
    target: SagaMilestone,
    prerequisite_receipt_digests: tuple[str, ...],
) -> None:
    """Require exactly one forward edge and its typed durable receipt."""

    if next_milestone(current) is not target:
        raise IngestionTransitionError(
            "ingestion milestone transition must advance exactly one edge",
            sanitized_code="ILLEGAL_INGESTION_TRANSITION",
        )
    expected = REQUIRED_RECEIPT_COUNTS[target]
    if len(prerequisite_receipt_digests) != expected:
        raise IngestionTransitionError(
            "ingestion milestone lacks its exact prerequisite receipt",
            sanitized_code="INGESTION_PREREQUISITE_MISSING",
        )


def build_transition_event(
    saga: IngestionSaga,
    *,
    target_milestone: SagaMilestone,
    reason_code: str,
    actor_boundary: str,
    idempotency_reference: str,
    prerequisite_receipt_digests: tuple[str, ...],
    created_at: datetime,
) -> SagaTransitionEvent:
    require_milestone_transition(
        saga.current_milestone,
        target_milestone,
        prerequisite_receipt_digests,
    )
    event_facts = {
        "tenant_id": saga.tenant_id,
        "saga_id": saga.saga_id,
        "sequence_number": saga.event_sequence + 1,
        "from_milestone": saga.current_milestone,
        "to_milestone": target_milestone,
        "reason_code": reason_code,
        "actor_boundary": actor_boundary,
        "idempotency_reference": idempotency_reference,
        "prerequisite_receipt_digests": prerequisite_receipt_digests,
        "previous_event_digest": saga.current_event_digest,
        "created_at": created_at,
    }
    event_id = f"ingevent-{canonical_sha256(event_facts)}"
    return SagaTransitionEvent(event_id=event_id, **event_facts)


def advance_saga(
    saga: IngestionSaga,
    event: SagaTransitionEvent,
) -> IngestionSaga:
    if (event.tenant_id, event.saga_id) != (saga.tenant_id, saga.saga_id):
        raise IngestionTransitionError(
            "ingestion event crosses a saga scope",
            sanitized_code="INGESTION_EVENT_SCOPE_MISMATCH",
        )
    require_milestone_transition(
        saga.current_milestone,
        event.to_milestone,
        event.prerequisite_receipt_digests,
    )
    if (
        event.from_milestone is not saga.current_milestone
        or event.sequence_number != saga.event_sequence + 1
        or event.previous_event_digest != saga.current_event_digest
    ):
        raise IngestionTransitionError(
            "ingestion event observed stale saga state",
            sanitized_code="STALE_INGESTION_STATE",
        )
    terminal = event.to_milestone is SagaMilestone.PUBLISHED
    return replace(
        saga,
        current_milestone=event.to_milestone,
        execution_disposition=(
            SagaExecutionDisposition.COMPLETED
            if terminal
            else SagaExecutionDisposition.READY
        ),
        state_version=saga.state_version + 1,
        event_sequence=event.sequence_number,
        current_event_digest=event.event_digest,
        next_retry_at=None,
        claim_token_digest=None,
        claimed_at=None,
        claim_expires_at=None,
        updated_at=event.created_at,
        terminal_at=event.created_at if terminal else None,
    )


def verify_saga_event_chain(
    saga: IngestionSaga,
    events: Iterable[SagaTransitionEvent],
) -> tuple[SagaTransitionEvent, ...]:
    ordered = tuple(sorted(events, key=lambda event: event.sequence_number))
    if not ordered:
        if (
            saga.current_milestone is not SagaMilestone.REGISTERED
            or saga.event_sequence != 0
            or saga.current_event_digest != INGESTION_GENESIS_DIGEST
        ):
            raise IngestionTransitionError(
                "non-genesis saga has no event chain",
                sanitized_code="INGESTION_EVENT_CHAIN_MISSING",
            )
        return ordered
    previous_milestone = SagaMilestone.REGISTERED
    previous_digest = INGESTION_GENESIS_DIGEST
    seen_ids: set[str] = set()
    seen_digests: set[str] = set()
    for expected_sequence, event in enumerate(ordered, start=1):
        if (event.tenant_id, event.saga_id) != (saga.tenant_id, saga.saga_id):
            raise IngestionTransitionError(
                "ingestion event chain crosses a saga scope",
                sanitized_code="INGESTION_EVENT_SCOPE_MISMATCH",
            )
        if (
            event.sequence_number != expected_sequence
            or event.event_id in seen_ids
            or event.event_digest in seen_digests
        ):
            raise IngestionTransitionError(
                "ingestion event sequence is duplicated or contains a gap",
                sanitized_code="INGESTION_EVENT_SEQUENCE_INVALID",
            )
        require_milestone_transition(
            previous_milestone,
            event.to_milestone,
            event.prerequisite_receipt_digests,
        )
        if (
            event.from_milestone is not previous_milestone
            or event.previous_event_digest != previous_digest
            or canonical_sha256(
                event,
                exclude_fields=("event_digest",),
            )
            != event.event_digest
        ):
            raise IngestionTransitionError(
                "ingestion event digest or link is invalid",
                sanitized_code="INGESTION_EVENT_CHAIN_INVALID",
            )
        seen_ids.add(event.event_id)
        seen_digests.add(event.event_digest)
        previous_milestone = event.to_milestone
        previous_digest = event.event_digest
    if (
        saga.event_sequence != len(ordered)
        or saga.current_milestone is not previous_milestone
        or saga.current_event_digest != previous_digest
    ):
        raise IngestionTransitionError(
            "saga terminal pointer differs from its event chain",
            sanitized_code="INGESTION_EVENT_TERMINAL_MISMATCH",
        )
    return ordered
