"""Injectable Step 11 persistence and snapshot-resolution boundaries."""

from __future__ import annotations

from typing import Protocol

from aioa_memory_kernel.ingestion import IngestionSaga, ParseReceipt
from aioa_memory_kernel.persistence import RequestContext
from aioa_memory_kernel.persistence.protocols import TransactionProtocol
from aioa_memory_kernel.storage import SnapshotEnvelope

from .models import ParseArtifact


class ParsingArtifactRepositoryProtocol(Protocol):
    """Atomic CockroachDB persistence with exact immutable replay."""

    def get_by_saga(
        self,
        transaction: TransactionProtocol,
        *,
        tenant_id: str,
        saga_id: str,
    ) -> ParseArtifact | None: ...

    def put_artifact(
        self,
        transaction: TransactionProtocol,
        artifact: ParseArtifact,
    ) -> ParseArtifact: ...


class ParsingPersistenceProtocol(Protocol):
    def persist(
        self,
        context: RequestContext,
        artifact: ParseArtifact,
    ) -> ParseArtifact: ...

    def get_by_saga(
        self,
        context: RequestContext,
        *,
        tenant_id: str,
        saga_id: str,
    ) -> ParseArtifact | None: ...


class SnapshotEnvelopeResolverProtocol(Protocol):
    """Resolve only the already-authorized exact snapshot manifest."""

    def __call__(self, saga: IngestionSaga) -> SnapshotEnvelope: ...


class ParseReceiptRepositoryProtocol(Protocol):
    """Optional narrow receipt lookup used by reconciliation tests/adapters."""

    def reconcile_receipt(
        self,
        context: RequestContext,
        saga: IngestionSaga,
    ) -> ParseReceipt | None: ...


__all__ = [
    "ParseReceiptRepositoryProtocol",
    "ParsingArtifactRepositoryProtocol",
    "ParsingPersistenceProtocol",
    "SnapshotEnvelopeResolverProtocol",
]
