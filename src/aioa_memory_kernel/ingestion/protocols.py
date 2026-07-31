"""Injectable Step 10 ports with no driver, SDK, or filesystem dependency."""

from __future__ import annotations

from typing import Protocol

from aioa_memory_kernel.persistence import RequestContext
from aioa_memory_kernel.persistence.protocols import TransactionProtocol
from aioa_memory_kernel.storage import (
    SnapshotEnvelope,
    SnapshotStorageEvidence,
    SnapshotStoragePlan,
)

from .models import (
    ExternalEffectIntent,
    ExternalEffectReceipt,
    ExternalEffectRecord,
    IngestionSaga,
    OrphanRecord,
    ParseReceipt,
    PublicationReceipt,
    SagaExecutionDisposition,
    SagaTransitionEvent,
    ValidationReceipt,
)


class IngestionSagaRepositoryProtocol(Protocol):
    def get_saga(
        self,
        transaction: TransactionProtocol,
        tenant_id: str,
        saga_id: str,
    ) -> IngestionSaga | None: ...

    def put_saga(
        self,
        transaction: TransactionProtocol,
        saga: IngestionSaga,
    ) -> IngestionSaga: ...

    def get_event(
        self,
        transaction: TransactionProtocol,
        tenant_id: str,
        saga_id: str,
        event_id: str,
    ) -> SagaTransitionEvent | None: ...

    def list_events(
        self,
        transaction: TransactionProtocol,
        tenant_id: str,
        saga_id: str,
    ) -> tuple[SagaTransitionEvent, ...]: ...

    def append_event(
        self,
        transaction: TransactionProtocol,
        event: SagaTransitionEvent,
    ) -> SagaTransitionEvent: ...

    def compare_and_set_transition(
        self,
        transaction: TransactionProtocol,
        saga: IngestionSaga,
        event: SagaTransitionEvent,
    ) -> IngestionSaga: ...

    def get_effect(
        self,
        transaction: TransactionProtocol,
        tenant_id: str,
        saga_id: str,
        effect_id: str,
    ) -> ExternalEffectRecord | None: ...

    def put_effect_intent(
        self,
        transaction: TransactionProtocol,
        intent: ExternalEffectIntent,
    ) -> ExternalEffectRecord: ...

    def attach_effect_receipt(
        self,
        transaction: TransactionProtocol,
        receipt: ExternalEffectReceipt,
    ) -> ExternalEffectRecord: ...

    def has_effect_prerequisite(
        self,
        transaction: TransactionProtocol,
        *,
        tenant_id: str,
        saga_id: str,
        effect_kind: object,
        prerequisite_digest: str,
        require_receipt: bool,
    ) -> bool: ...

    def claim_worker(
        self,
        transaction: TransactionProtocol,
        saga: IngestionSaga,
        *,
        claim_token_digest: str,
        claimed_at: object,
        claim_expires_at: object,
    ) -> IngestionSaga: ...

    def release_worker(
        self,
        transaction: TransactionProtocol,
        saga: IngestionSaga,
        *,
        claim_token_digest: str,
        released_at: object,
    ) -> IngestionSaga: ...

    def set_disposition(
        self,
        transaction: TransactionProtocol,
        saga: IngestionSaga,
        *,
        disposition: SagaExecutionDisposition,
        reason_code: str | None,
        next_retry_at: object | None,
        changed_at: object,
    ) -> IngestionSaga: ...

    def put_orphan(
        self,
        transaction: TransactionProtocol,
        orphan: OrphanRecord,
    ) -> OrphanRecord: ...


class AcquisitionPort(Protocol):
    """Obtain exact bytes without implementing a general downloader."""

    def acquire(self, saga: IngestionSaga) -> bytes: ...


class ParserPort(Protocol):
    """Step 11 boundary; Step 10 receives only deterministic receipts."""

    def parse(
        self,
        saga: IngestionSaga,
        *,
        s3_version_id: str,
        locked_storage_evidence_digest: str,
    ) -> ParseReceipt: ...

    def reconcile(
        self,
        saga: IngestionSaga,
        *,
        s3_version_id: str,
        locked_storage_evidence_digest: str,
    ) -> ParseReceipt | None: ...


class ValidatorPort(Protocol):
    """Injected validation boundary bound to a typed parse receipt."""

    def validate(
        self,
        saga: IngestionSaga,
        parse_receipt: ParseReceipt,
    ) -> ValidationReceipt: ...

    def reconcile(
        self,
        saga: IngestionSaga,
        parse_receipt: ParseReceipt,
    ) -> ValidationReceipt | None: ...


class PublicationPort(Protocol):
    """Only this boundary may call the existing Step 9 publication service."""

    def publish(
        self,
        context: RequestContext,
        saga: IngestionSaga,
        validation_receipt: ValidationReceipt,
    ) -> PublicationReceipt: ...

    def reconcile(
        self,
        context: RequestContext,
        saga: IngestionSaga,
        validation_receipt: ValidationReceipt,
    ) -> PublicationReceipt | None: ...


class SagaControlProtocol(Protocol):
    """Transaction-owning application service used by the orchestrator."""

    def register_saga(
        self,
        context: RequestContext,
        saga: IngestionSaga,
    ) -> IngestionSaga: ...

    def get_saga(
        self,
        context: RequestContext,
        *,
        tenant_id: str,
        saga_id: str,
    ) -> IngestionSaga | None: ...

    def claim_saga(
        self,
        context: RequestContext,
        *,
        tenant_id: str,
        saga_id: str,
        claim_token_digest: str,
        lease_seconds: int,
    ) -> IngestionSaga: ...

    def release_saga(
        self,
        context: RequestContext,
        *,
        tenant_id: str,
        saga_id: str,
        claim_token_digest: str,
    ) -> IngestionSaga: ...

    def record_effect_intent(
        self,
        context: RequestContext,
        intent: ExternalEffectIntent,
    ) -> ExternalEffectRecord: ...

    def record_effect_receipt(
        self,
        context: RequestContext,
        receipt: ExternalEffectReceipt,
    ) -> ExternalEffectRecord: ...

    def transition(
        self,
        context: RequestContext,
        *,
        tenant_id: str,
        saga_id: str,
        target_milestone: object,
        reason_code: str,
        actor_boundary: str,
        idempotency_reference: str,
        prerequisite_receipt_digests: tuple[str, ...],
    ) -> IngestionSaga: ...

    def record_failure(
        self,
        context: RequestContext,
        *,
        tenant_id: str,
        saga_id: str,
        disposition: SagaExecutionDisposition,
        reason_code: str,
        retry_delay_seconds: int | None,
    ) -> IngestionSaga: ...

    def record_orphan(
        self,
        context: RequestContext,
        orphan: OrphanRecord,
    ) -> OrphanRecord: ...


class PlannedSnapshotStorageProtocol(Protocol):
    """Step 7 storage plus a pure key plan and read-only reconciliation."""

    def plan_snapshot(self, snapshot: SnapshotEnvelope) -> SnapshotStoragePlan: ...

    def reconcile_snapshot(
        self,
        snapshot: SnapshotEnvelope,
    ) -> SnapshotStorageEvidence | None: ...

    def persist_snapshot(
        self,
        snapshot: SnapshotEnvelope,
    ) -> SnapshotStorageEvidence: ...

    def verify_snapshot(
        self,
        snapshot: SnapshotEnvelope,
        *,
        version_id: str,
    ) -> SnapshotStorageEvidence: ...
