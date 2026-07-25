"""Append-oriented, privacy-minimized audit metadata and hash chaining."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping

from .enums import ActorType
from .exceptions import ContractValidationError, IntegrityError
from .serialization import (
    canonical_sha256,
    ensure_utc,
    freeze_json,
    require_enum_member,
    require_non_empty,
    require_sha256_hex,
    verify_canonical_hash,
)


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """Metadata-only audit record suitable for future at-least-once export."""

    audit_event_id: str
    tenant_id: str
    user_id: str | None
    kernel_run_id: str | None
    event_type: str
    sequence_number: int
    previous_event_hash: str | None
    resource_type: str
    resource_id: str
    state_before: str | None
    state_after: str | None
    actor_type: ActorType
    actor_id: str
    content_hashes: Mapping[str, str]
    created_at: datetime
    personal_memory_space_id: str | None = None
    protected_payload_reference: str | None = None
    event_hash: str = field(init=False)

    def __post_init__(self) -> None:
        for field_name in (
            "audit_event_id",
            "tenant_id",
            "event_type",
            "resource_type",
            "resource_id",
            "actor_id",
        ):
            require_non_empty(getattr(self, field_name), field_name)
        require_enum_member(self.actor_type, ActorType, "actor_type")
        if (
            not isinstance(self.sequence_number, int)
            or isinstance(self.sequence_number, bool)
            or self.sequence_number < 0
        ):
            raise ContractValidationError(
                "sequence_number must be a non-negative integer"
            )
        if self.sequence_number == 0 and self.previous_event_hash is not None:
            raise ContractValidationError(
                "first audit event must not have a previous hash"
            )
        if self.sequence_number > 0:
            require_sha256_hex(
                self.previous_event_hash or "", "previous_event_hash"
            )
        if not self.content_hashes:
            raise ContractValidationError(
                "audit metadata requires at least one content hash"
            )
        for name, digest in self.content_hashes.items():
            require_non_empty(name, "content hash label")
            require_sha256_hex(digest, f"content hash {name}")
        object.__setattr__(
            self, "content_hashes", freeze_json(self.content_hashes)
        )
        if self.protected_payload_reference is not None:
            require_non_empty(
                self.protected_payload_reference,
                "protected_payload_reference",
            )
            if self.protected_payload_reference.startswith("data:"):
                raise ContractValidationError(
                    "audit payload must be referenced, not embedded"
                )
        if self.personal_memory_space_id is not None:
            require_non_empty(
                self.personal_memory_space_id, "personal_memory_space_id"
            )
        object.__setattr__(
            self, "created_at", ensure_utc(self.created_at, "created_at")
        )
        object.__setattr__(
            self, "event_hash", compute_audit_event_hash(self)
        )


def compute_audit_event_hash(event: AuditEvent) -> str:
    """Calculate a deterministic audit digest excluding its own hash."""

    return canonical_sha256(event, exclude_fields=("event_hash",))


def verify_audit_event_hash(event: AuditEvent) -> None:
    """Verify one audit record's deterministic identity."""

    verify_canonical_hash(
        event, event.event_hash, exclude_fields=("event_hash",)
    )


def verify_audit_chain(events: tuple[AuditEvent, ...]) -> None:
    """Verify sequence order, content integrity, and previous-hash linkage."""

    seen_ids: set[str] = set()
    previous: AuditEvent | None = None
    for event in events:
        if event.audit_event_id in seen_ids:
            raise IntegrityError("duplicate audit_event_id in audit chain")
        seen_ids.add(event.audit_event_id)
        verify_audit_event_hash(event)
        if previous is None:
            if event.sequence_number != 0 or event.previous_event_hash is not None:
                raise IntegrityError("audit chain must start at sequence zero")
        else:
            if event.sequence_number != previous.sequence_number + 1:
                raise IntegrityError("audit sequence is not contiguous")
            if event.previous_event_hash != previous.event_hash:
                raise IntegrityError("audit previous-event hash mismatch")
            if event.tenant_id != previous.tenant_id:
                raise IntegrityError("an audit chain cannot cross tenants")
            if event.created_at < previous.created_at:
                raise IntegrityError("audit event time cannot move backwards")
        previous = event


def deduplicate_audit_events(
    events: tuple[AuditEvent, ...],
) -> tuple[AuditEvent, ...]:
    """Deduplicate at-least-once exports by stable audit_event_id."""

    by_id: dict[str, AuditEvent] = {}
    for event in events:
        existing = by_id.get(event.audit_event_id)
        if existing is not None and existing.event_hash != event.event_hash:
            raise IntegrityError(
                "same audit_event_id was observed with different content"
            )
        by_id[event.audit_event_id] = event
    return tuple(
        sorted(
            by_id.values(),
            key=lambda event: (event.sequence_number, event.audit_event_id),
        )
    )


def build_audit_event(
    *,
    audit_event_id: str,
    tenant_id: str,
    user_id: str | None,
    kernel_run_id: str | None,
    event_type: str,
    sequence_number: int,
    previous_event: AuditEvent | None,
    resource_type: str,
    resource_id: str,
    state_before: str | None,
    state_after: str | None,
    actor_type: ActorType,
    actor_id: str,
    content_hashes: Mapping[str, str],
    created_at: datetime,
    personal_memory_space_id: str | None = None,
    protected_payload_reference: str | None = None,
) -> AuditEvent:
    """Build the next event using only metadata and protected references."""

    if previous_event is None:
        if sequence_number != 0:
            raise ContractValidationError(
                "first audit event sequence_number must be zero"
            )
        previous_hash = None
    else:
        if tenant_id != previous_event.tenant_id:
            raise ContractValidationError("audit event cannot cross tenants")
        if sequence_number != previous_event.sequence_number + 1:
            raise ContractValidationError("audit sequence must be contiguous")
        previous_hash = previous_event.event_hash
    return AuditEvent(
        audit_event_id=audit_event_id,
        tenant_id=tenant_id,
        user_id=user_id,
        kernel_run_id=kernel_run_id,
        event_type=event_type,
        sequence_number=sequence_number,
        previous_event_hash=previous_hash,
        resource_type=resource_type,
        resource_id=resource_id,
        state_before=state_before,
        state_after=state_after,
        actor_type=actor_type,
        actor_id=actor_id,
        content_hashes=content_hashes,
        created_at=created_at,
        personal_memory_space_id=personal_memory_space_id,
        protected_payload_reference=protected_payload_reference,
    )
