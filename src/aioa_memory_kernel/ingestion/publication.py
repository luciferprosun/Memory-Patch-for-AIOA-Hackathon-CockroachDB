"""Narrow Step 9 publication adapter for the Step 10 saga."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from aioa_memory_kernel.persistence import RequestContext
from aioa_memory_kernel.sources import (
    SourcePublicationState,
    SourceRegistryActor,
    SourceRegistryActorType,
    SourceRegistryService,
)

from .errors import IngestionReceiptError, IngestionValidationError
from .models import IngestionSaga, PublicationReceipt, ValidationReceipt


def _utc_now() -> datetime:
    return datetime.now(UTC)


class Step9PublicationPort:
    """Exercise only legal Step 9 transitions with deterministic identities."""

    def __init__(
        self,
        service: SourceRegistryService,
        *,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        if not isinstance(service, SourceRegistryService):
            raise IngestionValidationError(
                "publication port requires the Step 9 service",
                sanitized_code="INVALID_PUBLICATION_SERVICE",
            )
        if not callable(clock):
            raise IngestionValidationError(
                "publication clock must be callable",
                sanitized_code="INVALID_INGESTION_CLOCK",
            )
        self._service = service
        self._clock = clock
        self._actor = SourceRegistryActor(
            SourceRegistryActorType.TRUSTED_APPLICATION,
            "memory-patch-step10-ingestion-saga",
        )

    @staticmethod
    def _validate_binding(
        saga: IngestionSaga,
        validation_receipt: ValidationReceipt,
    ) -> None:
        if (
            not validation_receipt.accepted
            or validation_receipt.tenant_id != saga.tenant_id
            or validation_receipt.saga_id != saga.saga_id
            or validation_receipt.source_id != saga.source_id
            or validation_receipt.snapshot_id != saga.snapshot_id
        ):
            raise IngestionReceiptError(
                "publication input is not an accepted exact validation receipt",
                sanitized_code="PUBLICATION_RECEIPT_BINDING_MISMATCH",
            )

    def reconcile(
        self,
        context: RequestContext,
        saga: IngestionSaga,
        validation_receipt: ValidationReceipt,
    ) -> PublicationReceipt | None:
        self._validate_binding(saga, validation_receipt)
        record = self._service.get_source_registry(
            context,
            tenant_id=saga.tenant_id,
            source_id=saga.source_id,
            hat_scope_id=saga.hat_scope_id,
        )
        if record is None or (
            record.registry_digest != saga.source_registry_digest
            or record.scope.scope_digest != saga.scope_digest
            or record.snapshot_id != saga.snapshot_id
        ):
            raise IngestionReceiptError(
                "Step 9 registry no longer matches saga input",
                sanitized_code="PUBLICATION_SOURCE_BINDING_MISMATCH",
            )
        if record.current_publication_state is not (
            SourcePublicationState.PUBLISHED
        ):
            return None
        events = self._service.verify_publication_event_chain(
            context,
            tenant_id=saga.tenant_id,
            source_id=saga.source_id,
            hat_scope_id=saga.hat_scope_id,
        )
        if not events or events[-1].to_state is not (
            SourcePublicationState.PUBLISHED
        ):
            raise IngestionReceiptError(
                "published registry lacks its exact Step 9 event",
                sanitized_code="PUBLICATION_EVENT_MISSING",
            )
        event = events[-1]
        return PublicationReceipt(
            tenant_id=saga.tenant_id,
            saga_id=saga.saga_id,
            source_id=saga.source_id,
            snapshot_id=saga.snapshot_id,
            source_registry_digest=saga.source_registry_digest,
            publication_event_id=event.event_id,
            publication_event_digest=event.event_digest,
            publication_sequence=event.sequence_number,
            completed_at=event.created_at,
        )

    def publish(
        self,
        context: RequestContext,
        saga: IngestionSaga,
        validation_receipt: ValidationReceipt,
    ) -> PublicationReceipt:
        reconciled = self.reconcile(context, saga, validation_receipt)
        if reconciled is not None:
            return reconciled
        transitions = (
            (
                SourcePublicationState.REGISTERED,
                SourcePublicationState.REVIEW_REQUIRED,
                ("STEP10_VALIDATION_RECEIPT_PRESENT",),
            ),
            (
                SourcePublicationState.REVIEW_REQUIRED,
                SourcePublicationState.ELIGIBLE,
                ("STEP10_ELIGIBILITY_CONFIRMED",),
            ),
            (
                SourcePublicationState.ELIGIBLE,
                SourcePublicationState.PUBLISHED,
                ("STEP10_SAGA_PREREQUISITES_COMPLETE",),
            ),
        )
        for expected, target, reasons in transitions:
            record = self._service.get_source_registry(
                context,
                tenant_id=saga.tenant_id,
                source_id=saga.source_id,
                hat_scope_id=saga.hat_scope_id,
            )
            if record is None:
                raise IngestionReceiptError(
                    "Step 9 registry entry is unavailable",
                    sanitized_code="SOURCE_REGISTRY_NOT_FOUND",
                )
            if record.current_publication_state is target:
                continue
            if record.current_publication_state is not expected:
                if record.current_publication_state is (
                    SourcePublicationState.PUBLISHED
                ):
                    break
                raise IngestionReceiptError(
                    "Step 9 publication state requires operator review",
                    sanitized_code="PUBLICATION_STATE_CONFLICT",
                )
            suffix = target.value.lower()
            self._service.transition_publication_state(
                context,
                tenant_id=saga.tenant_id,
                source_id=saga.source_id,
                hat_scope_id=saga.hat_scope_id,
                expected_state=expected,
                expected_registry_digest=saga.source_registry_digest,
                expected_scope_digest=saga.scope_digest,
                target_state=target,
                event_id=f"s10-{saga.run_digest[:48]}-{suffix}",
                operation_id=f"s10-{saga.run_digest[:48]}-{suffix}",
                idempotency_key=f"{saga.idempotency_key}:publication:{suffix}",
                reason_codes=reasons,
                actor=self._actor,
                created_at=self._clock(),
            )
        receipt = self.reconcile(context, saga, validation_receipt)
        if receipt is None:
            raise IngestionReceiptError(
                "Step 9 publication did not reach a verified terminal event",
                sanitized_code="PUBLICATION_TERMINAL_MISMATCH",
            )
        return receipt
