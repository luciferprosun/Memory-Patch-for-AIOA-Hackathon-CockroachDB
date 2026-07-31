"""Transaction-owning Step 11 parse-artifact persistence service."""

from __future__ import annotations

from aioa_memory_kernel.contracts.serialization import canonical_sha256
from aioa_memory_kernel.persistence import (
    BeginOperation,
    IdempotencyService,
    RequestContext,
    SerializableTransactionRunner,
)
from aioa_memory_kernel.persistence.protocols import TransactionProtocol

from .errors import ParsingPersistenceConflictError, ParsingValidationError
from .models import ParseArtifact
from .protocols import ParsingArtifactRepositoryProtocol
from .repository import CockroachParsingArtifactRepository
from .validation import ParseArtifactValidator


class ParsingPersistenceService:
    """Persist complete artifacts atomically; retry only via the Step 6 runner."""

    def __init__(
        self,
        runner: SerializableTransactionRunner,
        *,
        repository: ParsingArtifactRepositoryProtocol | None = None,
        idempotency: IdempotencyService | None = None,
        validator: ParseArtifactValidator | None = None,
    ) -> None:
        if not isinstance(runner, SerializableTransactionRunner):
            raise ParsingValidationError(
                "parsing persistence requires the Step 6 transaction runner",
                sanitized_code="INVALID_PARSING_TRANSACTION_RUNNER",
            )
        self._runner = runner
        self._repository = repository or CockroachParsingArtifactRepository()
        self._idempotency = idempotency or IdempotencyService()
        self._validator = validator or ParseArtifactValidator()

    @staticmethod
    def _require_context(context: RequestContext, artifact: ParseArtifact) -> None:
        document = artifact.document
        if context.tenant_id != document.tenant_id:
            raise ParsingValidationError(
                "parse persistence crosses tenant context",
                sanitized_code="PARSING_CONTEXT_MISMATCH",
            )
        if document.owner_user_id is None:
            if context.user_id is not None:
                raise ParsingValidationError(
                    "shared parse requires tenant-shared context",
                    sanitized_code="PARSING_CONTEXT_MISMATCH",
                )
        elif context.user_id != document.owner_user_id:
            raise ParsingValidationError(
                "private parse crosses user context",
                sanitized_code="PARSING_CONTEXT_MISMATCH",
            )

    def persist(
        self,
        context: RequestContext,
        artifact: ParseArtifact,
    ) -> ParseArtifact:
        if not isinstance(artifact, ParseArtifact):
            raise ParsingValidationError(
                "persistence requires a typed parse artifact",
                sanitized_code="INVALID_PARSE_ARTIFACT",
            )
        self._require_context(context, artifact)
        validation = self._validator.validate(artifact)
        if not validation.accepted:
            raise ParsingValidationError(
                "structurally invalid or quarantined artifacts cannot persist",
                sanitized_code=validation.reason_codes[0],
            )
        document = artifact.document
        scope_digest = canonical_sha256(
            {
                "tenant_id": document.tenant_id,
                "owner_user_id": document.owner_user_id,
                "source_id": document.source_id,
                "hat_scope_id": document.hat_scope_id,
                "saga_id": document.saga_id,
            }
        )
        request = BeginOperation(
            operation_id=f"parse-{document.document_id}",
            tenant_id=document.tenant_id,
            owner_user_id=document.owner_user_id,
            operation_kind="STEP11_PARSE_ARTIFACT_PERSIST",
            idempotency_key=f"step11-parse:{document.saga_id}",
            request_digest=document.parse_artifact_digest,
            scope_digest=scope_digest,
            created_at=document.completed_at,
        )

        def operation(transaction: TransactionProtocol) -> ParseArtifact:
            claim = self._idempotency.begin_or_resume_operation(transaction, request)
            if not claim.may_proceed:
                existing = self._repository.get_by_saga(
                    transaction,
                    tenant_id=document.tenant_id,
                    saga_id=document.saga_id,
                )
                if (
                    existing is None
                    or existing.document.parse_artifact_digest
                    != document.parse_artifact_digest
                ):
                    raise ParsingPersistenceConflictError(
                        "completed parse operation lacks exact artifact evidence",
                        sanitized_code="PERSISTENCE_CONFLICT",
                    )
                return existing
            stored = self._repository.put_artifact(transaction, artifact)
            self._idempotency.complete_operation(
                transaction,
                tenant_id=document.tenant_id,
                operation_id=claim.operation.operation_id,
                expected_attempt_count=claim.operation.attempt_count,
                result_ref=document.document_id,
                result_digest=document.parse_artifact_digest,
            )
            return stored

        return self._runner.run(
            context,
            operation,
            operation_kind="STEP11_PARSE_ARTIFACT_PERSIST",
        )

    def get_by_saga(
        self,
        context: RequestContext,
        *,
        tenant_id: str,
        saga_id: str,
    ) -> ParseArtifact | None:
        if context.tenant_id != tenant_id:
            raise ParsingValidationError(
                "parse lookup crosses tenant context",
                sanitized_code="PARSING_CONTEXT_MISMATCH",
            )
        return self._runner.run(
            context,
            lambda transaction: self._repository.get_by_saga(
                transaction,
                tenant_id=tenant_id,
                saga_id=saga_id,
            ),
            operation_kind="STEP11_PARSE_ARTIFACT_READ",
        )


__all__ = ["ParsingPersistenceService"]
