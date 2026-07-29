"""Narrow source-registry repository protocol over the Step 6 transaction."""

from __future__ import annotations

from typing import Protocol

from aioa_memory_kernel.persistence.protocols import TransactionProtocol

from .models import (
    ProvenanceEdge,
    PublicationStateEvent,
    SourcePublicationState,
    SourceRegistryRecord,
)


class SourceRegistryRepositoryProtocol(Protocol):
    def source_scope_exists(
        self,
        transaction: TransactionProtocol,
        tenant_id: str,
        source_id: str,
        hat_scope_id: str,
        source_kind: str,
        source_reference: str,
    ) -> bool: ...

    def get_registry(
        self,
        transaction: TransactionProtocol,
        tenant_id: str,
        source_id: str,
        hat_scope_id: str,
    ) -> SourceRegistryRecord | None: ...

    def put_registry(
        self,
        transaction: TransactionProtocol,
        record: SourceRegistryRecord,
    ) -> SourceRegistryRecord: ...

    def list_edges(
        self,
        transaction: TransactionProtocol,
        tenant_id: str,
        source_id: str,
        hat_scope_id: str,
    ) -> tuple[ProvenanceEdge, ...]: ...

    def put_edge(
        self,
        transaction: TransactionProtocol,
        edge: ProvenanceEdge,
    ) -> ProvenanceEdge: ...

    def get_event(
        self,
        transaction: TransactionProtocol,
        tenant_id: str,
        source_id: str,
        hat_scope_id: str,
        event_id: str,
    ) -> PublicationStateEvent | None: ...

    def list_events(
        self,
        transaction: TransactionProtocol,
        tenant_id: str,
        source_id: str,
        hat_scope_id: str,
    ) -> tuple[PublicationStateEvent, ...]: ...

    def append_event(
        self,
        transaction: TransactionProtocol,
        event: PublicationStateEvent,
    ) -> PublicationStateEvent: ...

    def compare_and_set_publication(
        self,
        transaction: TransactionProtocol,
        *,
        tenant_id: str,
        source_id: str,
        hat_scope_id: str,
        expected_state: SourcePublicationState,
        expected_sequence: int,
        expected_event_digest: str,
        event: PublicationStateEvent,
    ) -> SourceRegistryRecord: ...
