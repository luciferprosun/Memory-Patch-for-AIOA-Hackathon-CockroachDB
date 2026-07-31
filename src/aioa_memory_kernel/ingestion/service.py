"""Transactional application service for durable Step 10 saga state."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from aioa_memory_kernel.persistence import (
    BeginOperation,
    IdempotencyService,
    OperationStatus,
    RequestContext,
    SerializableTransactionRunner,
)
from aioa_memory_kernel.persistence.protocols import TransactionProtocol
from aioa_memory_kernel.sources import (
    CockroachSourceRegistryRepository,
    SourceAccessClass,
)
from aioa_memory_kernel.sources.protocols import (
    SourceRegistryRepositoryProtocol,
)

from .errors import (
    IngestionClaimError,
    IngestionConflictError,
    IngestionReceiptError,
    IngestionTransitionError,
    IngestionValidationError,
)
from .models import (
    ExternalEffectIntent,
    ExternalEffectKind,
    ExternalEffectReceipt,
    ExternalEffectRecord,
    IngestionSaga,
    OrphanRecord,
    SagaExecutionDisposition,
    SagaMilestone,
)
from .protocols import IngestionSagaRepositoryProtocol
from .repository import CockroachIngestionSagaRepository
from .state import (
    build_transition_event,
    next_milestone,
    verify_saga_event_chain,
)


def utc_now() -> datetime:
    return datetime.now(UTC)


_PREREQUISITES: dict[
    SagaMilestone,
    tuple[ExternalEffectKind, bool],
] = {
    SagaMilestone.ACQUIRED_LOCAL: (ExternalEffectKind.ACQUISITION, True),
    SagaMilestone.HASH_VERIFIED: (
        ExternalEffectKind.HASH_VERIFICATION,
        True,
    ),
    SagaMilestone.SNAPSHOT_UPLOAD_PENDING: (
        ExternalEffectKind.S3_UPLOAD,
        False,
    ),
    SagaMilestone.SNAPSHOT_UPLOADED: (ExternalEffectKind.S3_UPLOAD, True),
    SagaMilestone.SNAPSHOT_LOCK_VERIFIED: (
        ExternalEffectKind.S3_LOCK_VERIFICATION,
        True,
    ),
    SagaMilestone.PARSED: (ExternalEffectKind.PARSE, True),
    SagaMilestone.VALIDATED: (ExternalEffectKind.VALIDATION, True),
    SagaMilestone.PUBLISHED: (ExternalEffectKind.PUBLICATION, True),
}

_INTENT_MILESTONES = {
    ExternalEffectKind.ACQUISITION: SagaMilestone.REGISTERED,
    ExternalEffectKind.HASH_VERIFICATION: SagaMilestone.ACQUIRED_LOCAL,
    ExternalEffectKind.S3_UPLOAD: SagaMilestone.HASH_VERIFIED,
    ExternalEffectKind.S3_LOCK_VERIFICATION: SagaMilestone.SNAPSHOT_UPLOADED,
    ExternalEffectKind.PARSE: SagaMilestone.SNAPSHOT_LOCK_VERIFIED,
    ExternalEffectKind.VALIDATION: SagaMilestone.PARSED,
    ExternalEffectKind.PUBLICATION: SagaMilestone.VALIDATED,
}


class IngestionSagaService:
    """Own short retries and CAS; all external work stays outside this class."""

    def __init__(
        self,
        runner: SerializableTransactionRunner,
        *,
        repository: IngestionSagaRepositoryProtocol | None = None,
        source_repository: SourceRegistryRepositoryProtocol | None = None,
        idempotency: IdempotencyService | None = None,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        if not isinstance(runner, SerializableTransactionRunner):
            raise IngestionValidationError(
                "ingestion saga requires the Step 6 transaction runner",
                sanitized_code="INVALID_INGESTION_TRANSACTION_RUNNER",
            )
        if not callable(clock):
            raise IngestionValidationError(
                "ingestion clock must be callable",
                sanitized_code="INVALID_INGESTION_CLOCK",
            )
        self._runner = runner
        self._repository = repository or CockroachIngestionSagaRepository()
        self._sources = source_repository or CockroachSourceRegistryRepository()
        self._clock = clock
        self._idempotency = idempotency or IdempotencyService(clock=clock)

    @staticmethod
    def _require_context(context: RequestContext, saga: IngestionSaga) -> None:
        if context.tenant_id != saga.tenant_id:
            raise IngestionValidationError(
                "ingestion operation crosses tenant context",
                sanitized_code="INGESTION_CONTEXT_MISMATCH",
            )
        if saga.owner_user_id is None:
            if context.user_id is not None:
                raise IngestionValidationError(
                    "shared ingestion requires tenant-shared context",
                    sanitized_code="INGESTION_CONTEXT_MISMATCH",
                )
        elif context.user_id != saga.owner_user_id:
            raise IngestionValidationError(
                "private ingestion crosses user context",
                sanitized_code="INGESTION_CONTEXT_MISMATCH",
            )

    def register_saga(
        self,
        context: RequestContext,
        saga: IngestionSaga,
    ) -> IngestionSaga:
        self._require_context(context, saga)
        if (
            saga.current_milestone is not SagaMilestone.REGISTERED
            or saga.state_version != 0
            or saga.event_sequence != 0
        ):
            raise IngestionValidationError(
                "new ingestion saga must be exact genesis",
                sanitized_code="INVALID_INGESTION_GENESIS",
            )
        request = BeginOperation(
            operation_id=f"register-{saga.saga_id}",
            tenant_id=saga.tenant_id,
            owner_user_id=saga.owner_user_id,
            operation_kind="INGESTION_SAGA_REGISTER",
            idempotency_key=saga.idempotency_key,
            request_digest=saga.request_digest,
            scope_digest=saga.scope_digest,
            created_at=saga.created_at,
        )

        def operation(transaction: TransactionProtocol) -> IngestionSaga:
            claim = self._idempotency.begin_or_resume_operation(
                transaction,
                request,
            )
            if not claim.may_proceed:
                existing = self._repository.get_saga(
                    transaction,
                    saga.tenant_id,
                    saga.saga_id,
                )
                if (
                    claim.operation.status is OperationStatus.COMPLETED
                    and existing is not None
                    and existing.run_digest == saga.run_digest
                    and claim.operation.result_digest == saga.run_digest
                ):
                    return existing
                raise IngestionConflictError(
                    "saga registration is not a completed exact replay",
                    sanitized_code="INGESTION_REGISTRATION_REPLAY_CONFLICT",
                )
            source = self._sources.get_registry(
                transaction,
                saga.tenant_id,
                saga.source_id,
                saga.hat_scope_id,
            )
            if source is None:
                raise IngestionValidationError(
                    "Step 9 source registry entry is required",
                    sanitized_code="SOURCE_REGISTRY_NOT_FOUND",
                )
            owner = (
                source.scope.owner_user_id
                if source.access_class is SourceAccessClass.USER_PRIVATE
                else None
            )
            if (
                source.registry_digest != saga.source_registry_digest
                or source.scope.scope_digest != saga.scope_digest
                or source.snapshot_id != saga.snapshot_id
                or source.knowledge_version_id != saga.knowledge_version_id
                or source.artifact.artifact_digest != saga.content_sha256
                or source.artifact.byte_length != saga.content_length
                or source.artifact.exact_source_bytes is not True
                or owner != saga.owner_user_id
            ):
                raise IngestionValidationError(
                    "saga facts do not match the exact Step 9 registry entry",
                    sanitized_code="INGESTION_SOURCE_BINDING_MISMATCH",
                )
            stored = self._repository.put_saga(transaction, saga)
            self._idempotency.complete_operation(
                transaction,
                tenant_id=saga.tenant_id,
                operation_id=claim.operation.operation_id,
                expected_attempt_count=claim.operation.attempt_count,
                result_ref=saga.saga_id,
                result_digest=stored.run_digest,
            )
            return stored

        return self._runner.run(
            context,
            operation,
            operation_kind="INGESTION_SAGA_REGISTER",
        )

    def get_saga(
        self,
        context: RequestContext,
        *,
        tenant_id: str,
        saga_id: str,
    ) -> IngestionSaga | None:
        def operation(
            transaction: TransactionProtocol,
        ) -> IngestionSaga | None:
            saga = self._repository.get_saga(
                transaction,
                tenant_id,
                saga_id,
            )
            if saga is not None:
                self._require_context(context, saga)
            return saga

        return self._runner.run(
            context,
            operation,
            operation_kind="INGESTION_SAGA_READ",
        )

    def claim_saga(
        self,
        context: RequestContext,
        *,
        tenant_id: str,
        saga_id: str,
        claim_token_digest: str,
        lease_seconds: int,
    ) -> IngestionSaga:
        if not isinstance(lease_seconds, int) or not 1 <= lease_seconds <= 300:
            raise IngestionClaimError(
                "worker lease must be between 1 and 300 seconds",
                sanitized_code="INVALID_INGESTION_LEASE",
            )
        claimed_at = self._clock()
        claim_expires_at = claimed_at + timedelta(seconds=lease_seconds)

        def operation(transaction: TransactionProtocol) -> IngestionSaga:
            saga = self._require_saga(transaction, tenant_id, saga_id)
            self._require_context(context, saga)
            return self._repository.claim_worker(
                transaction,
                saga,
                claim_token_digest=claim_token_digest,
                claimed_at=claimed_at,
                claim_expires_at=claim_expires_at,
            )

        return self._runner.run(
            context,
            operation,
            operation_kind="INGESTION_SAGA_CLAIM",
        )

    def release_saga(
        self,
        context: RequestContext,
        *,
        tenant_id: str,
        saga_id: str,
        claim_token_digest: str,
    ) -> IngestionSaga:
        released_at = self._clock()

        def operation(transaction: TransactionProtocol) -> IngestionSaga:
            saga = self._require_saga(transaction, tenant_id, saga_id)
            self._require_context(context, saga)
            return self._repository.release_worker(
                transaction,
                saga,
                claim_token_digest=claim_token_digest,
                released_at=released_at,
            )

        return self._runner.run(
            context,
            operation,
            operation_kind="INGESTION_SAGA_RELEASE",
        )

    def record_effect_intent(
        self,
        context: RequestContext,
        intent: ExternalEffectIntent,
    ) -> ExternalEffectRecord:
        def operation(transaction: TransactionProtocol) -> ExternalEffectRecord:
            saga = self._require_saga(
                transaction,
                intent.tenant_id,
                intent.saga_id,
            )
            self._require_context(context, saga)
            expected_milestone = _INTENT_MILESTONES[intent.effect_kind]
            if saga.current_milestone is not expected_milestone:
                existing = self._repository.get_effect(
                    transaction,
                    intent.tenant_id,
                    intent.saga_id,
                    intent.effect_id,
                )
                if existing is not None and existing.intent == intent:
                    return existing
                raise IngestionTransitionError(
                    "external intent is not valid at the current milestone",
                    sanitized_code="EXTERNAL_INTENT_MILESTONE_MISMATCH",
                )
            if (
                intent.expected_snapshot_id != saga.snapshot_id
                or intent.expected_sha256 != saga.content_sha256
                or intent.expected_length != saga.content_length
            ):
                raise IngestionValidationError(
                    "external intent differs from immutable saga facts",
                    sanitized_code="EXTERNAL_INTENT_BINDING_MISMATCH",
                )
            return self._repository.put_effect_intent(transaction, intent)

        return self._runner.run(
            context,
            operation,
            operation_kind="INGESTION_EXTERNAL_INTENT",
        )

    def record_effect_receipt(
        self,
        context: RequestContext,
        receipt: ExternalEffectReceipt,
    ) -> ExternalEffectRecord:
        def operation(transaction: TransactionProtocol) -> ExternalEffectRecord:
            saga = self._require_saga(
                transaction,
                receipt.tenant_id,
                receipt.saga_id,
            )
            self._require_context(context, saga)
            existing = self._repository.get_effect(
                transaction,
                receipt.tenant_id,
                receipt.saga_id,
                receipt.effect_id,
            )
            if existing is None or existing.intent.intent_digest != (
                receipt.intent_digest
            ):
                raise IngestionReceiptError(
                    "external receipt has no exact durable intent",
                    sanitized_code="EXTERNAL_INTENT_NOT_FOUND",
                )
            if (
                receipt.evidence.get("snapshot_id") != saga.snapshot_id
                or receipt.evidence.get("canonical_sha256")
                != saga.content_sha256
                or receipt.evidence.get("content_length")
                != saga.content_length
            ):
                raise IngestionReceiptError(
                    "external receipt differs from immutable saga facts",
                    sanitized_code="EXTERNAL_RECEIPT_BINDING_MISMATCH",
                )
            return self._repository.attach_effect_receipt(
                transaction,
                receipt,
            )

        return self._runner.run(
            context,
            operation,
            operation_kind="INGESTION_EXTERNAL_RECEIPT",
        )

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
    ) -> IngestionSaga:
        if not isinstance(target_milestone, SagaMilestone):
            raise IngestionTransitionError(
                "target milestone has the wrong type",
                sanitized_code="INVALID_INGESTION_MILESTONE",
            )
        timestamp = self._clock()

        def operation(transaction: TransactionProtocol) -> IngestionSaga:
            saga = self._require_saga(transaction, tenant_id, saga_id)
            self._require_context(context, saga)
            if saga.current_milestone is target_milestone:
                events = self._repository.list_events(
                    transaction,
                    tenant_id,
                    saga_id,
                )
                event = next(
                    (
                        item
                        for item in events
                        if item.to_milestone is target_milestone
                    ),
                    None,
                )
                if event is not None and (
                    event.reason_code,
                    event.actor_boundary,
                    event.idempotency_reference,
                    event.prerequisite_receipt_digests,
                ) == (
                    reason_code,
                    actor_boundary,
                    idempotency_reference,
                    prerequisite_receipt_digests,
                ):
                    return saga
                raise IngestionConflictError(
                    "transition replay differs from durable event facts",
                    sanitized_code="INGESTION_TRANSITION_REPLAY_CONFLICT",
                )
            if next_milestone(saga.current_milestone) is not target_milestone:
                raise IngestionTransitionError(
                    "ingestion transition skips or reverses a milestone",
                    sanitized_code="ILLEGAL_INGESTION_TRANSITION",
                )
            effect_kind, require_receipt = _PREREQUISITES[target_milestone]
            if len(prerequisite_receipt_digests) != 1 or not (
                self._repository.has_effect_prerequisite(
                    transaction,
                    tenant_id=tenant_id,
                    saga_id=saga_id,
                    effect_kind=effect_kind,
                    prerequisite_digest=prerequisite_receipt_digests[0],
                    require_receipt=require_receipt,
                )
            ):
                raise IngestionReceiptError(
                    "transition prerequisite is not durable exact evidence",
                    sanitized_code="INGESTION_PREREQUISITE_MISSING",
                )
            event = build_transition_event(
                saga,
                target_milestone=target_milestone,
                reason_code=reason_code,
                actor_boundary=actor_boundary,
                idempotency_reference=idempotency_reference,
                prerequisite_receipt_digests=prerequisite_receipt_digests,
                created_at=timestamp,
            )
            stored = self._repository.append_event(transaction, event)
            return self._repository.compare_and_set_transition(
                transaction,
                saga,
                stored,
            )

        return self._runner.run(
            context,
            operation,
            operation_kind="INGESTION_SAGA_TRANSITION",
        )

    def record_failure(
        self,
        context: RequestContext,
        *,
        tenant_id: str,
        saga_id: str,
        disposition: SagaExecutionDisposition,
        reason_code: str,
        retry_delay_seconds: int | None,
    ) -> IngestionSaga:
        if disposition not in {
            SagaExecutionDisposition.RETRY_WAIT,
            SagaExecutionDisposition.OPERATOR_REVIEW,
            SagaExecutionDisposition.QUARANTINED,
        }:
            raise IngestionValidationError(
                "failure disposition is unsupported",
                sanitized_code="INVALID_INGESTION_FAILURE_DISPOSITION",
            )
        changed_at = self._clock()
        if disposition is SagaExecutionDisposition.RETRY_WAIT:
            if (
                not isinstance(retry_delay_seconds, int)
                or not 1 <= retry_delay_seconds <= 3600
            ):
                raise IngestionValidationError(
                    "retry delay must be bounded",
                    sanitized_code="INVALID_INGESTION_RETRY_DELAY",
                )
            next_retry_at = changed_at + timedelta(
                seconds=retry_delay_seconds
            )
        else:
            if retry_delay_seconds is not None:
                raise IngestionValidationError(
                    "non-retry failure cannot carry a retry delay",
                    sanitized_code="INVALID_INGESTION_RETRY_DELAY",
                )
            next_retry_at = None

        def operation(transaction: TransactionProtocol) -> IngestionSaga:
            saga = self._require_saga(transaction, tenant_id, saga_id)
            self._require_context(context, saga)
            return self._repository.set_disposition(
                transaction,
                saga,
                disposition=disposition,
                reason_code=reason_code,
                next_retry_at=next_retry_at,
                changed_at=changed_at,
            )

        return self._runner.run(
            context,
            operation,
            operation_kind="INGESTION_SAGA_FAILURE",
        )

    def record_orphan(
        self,
        context: RequestContext,
        orphan: OrphanRecord,
    ) -> OrphanRecord:
        def operation(transaction: TransactionProtocol) -> OrphanRecord:
            if orphan.saga_id is not None:
                saga = self._require_saga(
                    transaction,
                    orphan.tenant_id,
                    orphan.saga_id,
                )
                self._require_context(context, saga)
            elif context.tenant_id != orphan.tenant_id:
                raise IngestionValidationError(
                    "orphan operation crosses tenant context",
                    sanitized_code="INGESTION_CONTEXT_MISMATCH",
                )
            return self._repository.put_orphan(transaction, orphan)

        return self._runner.run(
            context,
            operation,
            operation_kind="INGESTION_ORPHAN_RECORD",
        )

    def verify_event_chain(
        self,
        context: RequestContext,
        *,
        tenant_id: str,
        saga_id: str,
    ) -> tuple[object, ...]:
        def operation(transaction: TransactionProtocol) -> tuple[object, ...]:
            saga = self._require_saga(transaction, tenant_id, saga_id)
            self._require_context(context, saga)
            return verify_saga_event_chain(
                saga,
                self._repository.list_events(
                    transaction,
                    tenant_id,
                    saga_id,
                ),
            )

        return self._runner.run(
            context,
            operation,
            operation_kind="INGESTION_EVENT_CHAIN_VERIFY",
        )

    def _require_saga(
        self,
        transaction: TransactionProtocol,
        tenant_id: str,
        saga_id: str,
    ) -> IngestionSaga:
        saga = self._repository.get_saga(
            transaction,
            tenant_id,
            saga_id,
        )
        if saga is None:
            raise IngestionValidationError(
                "ingestion saga was not found",
                sanitized_code="INGESTION_SAGA_NOT_FOUND",
            )
        return saga
