"""Short-transaction adapter for immutable Draft V1 persistence."""

from __future__ import annotations

from collections.abc import Mapping

from aioa_memory_kernel.contracts.exceptions import IntegrityError
from aioa_memory_kernel.modeling.models import (
    DraftV1,
    decode_draft_reference,
    encode_draft_reference,
    verify_draft_v1_hash,
)

from .cockroach import CockroachPersistenceRepository
from .errors import ImmutableRecordConflictError, PersistenceConfigurationError
from .models import AccessMode, DraftRecord, RequestContext
from .transaction import SerializableTransactionRunner, Transaction


class CockroachDraftV1Store:
    """Reuse Step 4 drafts and Step 5 RLS; no provider capability is present."""

    def __init__(
        self,
        runner: SerializableTransactionRunner,
        repository: CockroachPersistenceRepository | None = None,
    ) -> None:
        if not isinstance(runner, SerializableTransactionRunner):
            raise TypeError("runner must be SerializableTransactionRunner")
        if repository is not None and not isinstance(
            repository, CockroachPersistenceRepository
        ):
            raise TypeError("repository must be CockroachPersistenceRepository")
        self._runner = runner
        self._repository = repository or CockroachPersistenceRepository()

    @staticmethod
    def _context(tenant_id: str, user_id: str) -> RequestContext:
        return RequestContext(
            tenant_id=tenant_id,
            user_id=user_id,
            access_mode=AccessMode.USER_PRIVATE,
        )

    @staticmethod
    def _draft_from_row(
        row: Mapping[str, object],
        *,
        tenant_id: str,
        user_id: str,
        draft_id: str,
    ) -> DraftV1:
        try:
            if (
                row.get("tenant_id") != tenant_id
                or row.get("draft_id") != draft_id
                or row.get("draft_stage") != 1
            ):
                raise IntegrityError("persisted Draft V1 identity mismatch")
            draft = decode_draft_reference(row.get("immutable_content_reference"))
            verify_draft_v1_hash(draft)
            if (
                draft.tenant_id != tenant_id
                or draft.user_id != user_id
                or draft.draft_id != draft_id
                or row.get("kernel_run_id") != draft.request_id
                or row.get("content_sha256") != draft.draft_text_sha256
                or row.get("created_at") != draft.created_at
            ):
                raise IntegrityError("persisted Draft V1 facts mismatch")
            return draft
        except (IntegrityError, KeyError, TypeError) as exc:
            raise ImmutableRecordConflictError(
                "persisted Draft V1 failed immutable verification",
                sanitized_code="DRAFT_IMMUTABLE_CONFLICT",
            ) from exc

    def load(
        self,
        *,
        tenant_id: str,
        user_id: str,
        draft_id: str,
    ) -> DraftV1 | None:
        context = self._context(tenant_id, user_id)

        def read(transaction: Transaction) -> Mapping[str, object] | None:
            return self._repository.get_draft_record(
                transaction,
                tenant_id=tenant_id,
                draft_id=draft_id,
            )

        row = self._runner.run(context, read, operation_kind="draft-v1-load")
        if row is None:
            return None
        return self._draft_from_row(
            row,
            tenant_id=tenant_id,
            user_id=user_id,
            draft_id=draft_id,
        )

    def put(self, draft: DraftV1) -> DraftV1:
        if not isinstance(draft, DraftV1):
            raise PersistenceConfigurationError(
                "draft must be DraftV1",
                sanitized_code="INVALID_RECORD_TYPE",
            )
        verify_draft_v1_hash(draft)
        record = DraftRecord(
            tenant_id=draft.tenant_id,
            draft_id=draft.draft_id,
            kernel_run_id=draft.request_id,
            draft_stage=1,
            content_sha256=draft.draft_text_sha256,
            immutable_content_reference=encode_draft_reference(draft),
            created_at=draft.created_at,
        )
        context = self._context(draft.tenant_id, draft.user_id)

        def write(transaction: Transaction) -> Mapping[str, object]:
            return self._repository.put_draft(transaction, record)

        row = self._runner.run(context, write, operation_kind="draft-v1-put")
        return self._draft_from_row(
            row,
            tenant_id=draft.tenant_id,
            user_id=draft.user_id,
            draft_id=draft.draft_id,
        )


__all__ = ["CockroachDraftV1Store"]
