"""Bounded Step 19 generation and vector retrieval orchestration."""

from __future__ import annotations

import hashlib
from decimal import Decimal

from aioa_memory_kernel.contracts.enums import KnowledgeRoute, MemoryTargetScope
from aioa_memory_kernel.contracts.exceptions import ContractValidationError, IntegrityError
from aioa_memory_kernel.contracts.serialization import canonical_sha256
from aioa_memory_kernel.persistence import (
    AccessMode,
    PersistenceError,
    RequestContext,
    SerializableTransactionRunner,
    assert_no_open_persistence_transaction,
)

from .backend import EmbeddingBackend
from .cache import PassageEmbeddingCache
from .models import (
    MAXIMUM_TOTAL_CONTENT_BYTES,
    EmbeddingBoundaryError,
    EmbeddingGenerationRequest,
    EmbeddingGenerationResult,
    EmbeddingRecord,
    EmbeddingSource,
    Step19ReasonCode,
    VectorRetrievalCandidate,
    VectorRetrievalRequest,
    VectorRetrievalResult,
    load_approved_model_spec,
    passage_cache_key,
    prepare_passage,
    prepare_query,
    verify_embedding_record_hash,
    verify_embedding_source_hash,
    verify_generation_request_hash,
    verify_vector_candidate_hash,
    verify_vector_request_hash,
)
from .repository import CockroachEmbeddingRepository


def _request_context(
    tenant_id: str,
    user_id: str,
    personal_memory_space_id: str | None,
) -> RequestContext:
    return RequestContext(
        tenant_id,
        user_id if personal_memory_space_id is not None else None,
        AccessMode.USER_PRIVATE
        if personal_memory_space_id is not None
        else AccessMode.TENANT_SHARED,
    )


def _require_hat_route(route: KnowledgeRoute) -> bool:
    if route is KnowledgeRoute.PASS_THROUGH:
        return False
    if route is KnowledgeRoute.AMBIGUOUS:
        raise EmbeddingBoundaryError(Step19ReasonCode.AMBIGUOUS_ROUTE)
    if route not in {KnowledgeRoute.HAT_ASSIST, KnowledgeRoute.HAT_ENFORCE}:
        raise EmbeddingBoundaryError(Step19ReasonCode.AMBIGUOUS_ROUTE)
    return True


class EmbeddingGenerationService:
    """Read source rows, close transactions, infer locally, then persist."""

    def __init__(
        self,
        runner: SerializableTransactionRunner,
        backend: EmbeddingBackend,
        cache: PassageEmbeddingCache,
        repository: CockroachEmbeddingRepository | None = None,
    ) -> None:
        if not isinstance(runner, SerializableTransactionRunner):
            raise TypeError("runner must be SerializableTransactionRunner")
        if not isinstance(cache, PassageEmbeddingCache):
            raise TypeError("cache must be PassageEmbeddingCache")
        self._runner = runner
        self._backend = backend
        self._cache = cache
        self._repository = repository or CockroachEmbeddingRepository()
        self._spec = load_approved_model_spec()
        identity = self._backend.identity()
        if identity.model_digest != self._spec.model_digest:
            raise EmbeddingBoundaryError(Step19ReasonCode.MODEL_IDENTITY_INVALID)

    @staticmethod
    def _empty(
        request: EmbeddingGenerationRequest,
        reason: Step19ReasonCode,
    ) -> EmbeddingGenerationResult:
        return EmbeddingGenerationResult(
            request_id=request.request_id,
            tenant_id=request.tenant_id,
            route_hash=request.route_hash,
            model_digest=request.model_digest,
            records=(),
            cache_hits=0,
            generated_count=0,
            truncated=False,
            reason_codes=(reason,),
        )

    @staticmethod
    def _input_identity(
        source: EmbeddingSource,
        prepared_passage_sha256: str,
        model_digest: str,
        input_policy_version: str,
    ) -> str:
        return canonical_sha256(
            {
                "tenant_id": source.tenant_id,
                "hat_scope_id": source.hat_scope_id,
                "source_id": source.source_id,
                "knowledge_version_id": source.knowledge_version_id,
                "chunk_id": source.chunk_id,
                "content_sha256": source.content_sha256,
                "model_digest": model_digest,
                "input_policy_version": input_policy_version,
                "prepared_passage_sha256": prepared_passage_sha256,
            }
        )

    def generate(
        self,
        request: EmbeddingGenerationRequest,
    ) -> EmbeddingGenerationResult:
        if not isinstance(request, EmbeddingGenerationRequest):
            raise EmbeddingBoundaryError(Step19ReasonCode.SCHEMA_UNSUPPORTED)
        try:
            verify_generation_request_hash(request)
        except (ContractValidationError, IntegrityError) as exc:
            raise EmbeddingBoundaryError(Step19ReasonCode.ROUTE_HASH_INVALID) from exc
        if not _require_hat_route(request.route.knowledge_route):
            return self._empty(request, Step19ReasonCode.NO_HAT_SELECTED)
        context = _request_context(
            request.tenant_id,
            request.user_id,
            request.personal_memory_space_id,
        )
        try:
            selected = tuple(
                self._runner.run(
                    context,
                    lambda transaction: self._repository.select_sources(
                        transaction, request
                    ),
                    operation_kind="STEP19_SELECT_EMBEDDING_SOURCES",
                )
            )
        except EmbeddingBoundaryError:
            raise
        except PersistenceError as exc:
            raise EmbeddingBoundaryError(Step19ReasonCode.DATABASE_ERROR) from exc
        truncated = len(selected) > request.maximum_items
        sources = selected[: request.maximum_items]
        for source in sources:
            try:
                verify_embedding_source_hash(source)
            except (ContractValidationError, IntegrityError) as exc:
                raise EmbeddingBoundaryError(Step19ReasonCode.SOURCE_NOT_ELIGIBLE) from exc
            if (
                source.tenant_id != request.tenant_id
                or source.hat_scope_id != request.hat_scope_id
                or source.effective_scope != request.effective_scope
            ):
                raise EmbeddingBoundaryError(Step19ReasonCode.SOURCE_NOT_ELIGIBLE)
        records: list[EmbeddingRecord] = []
        pending: list[tuple[EmbeddingSource, EmbeddingRecord | None]] = []
        cache_hits = 0
        for source in sources:
            try:
                existing = self._runner.run(
                    context,
                    lambda transaction, source=source: self._repository.find_record(
                        transaction, source, request.model_digest
                    ),
                    operation_kind="STEP19_FIND_EMBEDDING_RECORD",
                )
            except PersistenceError as exc:
                raise EmbeddingBoundaryError(Step19ReasonCode.DATABASE_ERROR) from exc
            if existing is not None:
                try:
                    verify_embedding_record_hash(existing)
                except (ContractValidationError, IntegrityError) as exc:
                    raise EmbeddingBoundaryError(
                        Step19ReasonCode.EMBEDDING_RECORD_CONFLICT
                    ) from exc
                if (
                    existing.content_sha256 != source.content_sha256
                    or existing.tenant_id != source.tenant_id
                    or existing.hat_scope_id != source.hat_scope_id
                    or existing.source_id != source.source_id
                    or existing.knowledge_version_id != source.knowledge_version_id
                    or existing.chunk_id != source.chunk_id
                ):
                    raise EmbeddingBoundaryError(
                        Step19ReasonCode.EMBEDDING_RECORD_CONFLICT
                    )
                replay = self._cache.read(existing)
                if replay is not None:
                    records.append(existing)
                    cache_hits += 1
                    continue
            pending.append((source, existing))
        identity = self._backend.identity()
        for offset in range(0, len(pending), request.batch_size):
            batch = pending[offset : offset + request.batch_size]
            prepared = tuple(
                prepare_passage(source.content, self._spec)
                for source, _ in batch
            )
            assert_no_open_persistence_transaction()
            embedded = self._backend.embed_passages(prepared)
            if len(embedded.vectors) != len(batch):
                raise EmbeddingBoundaryError(Step19ReasonCode.MODEL_RUNTIME_UNAVAILABLE)
            for (source, existing), prepared_text, vector, was_truncated in zip(
                batch,
                prepared,
                embedded.vectors,
                embedded.truncated,
                strict=True,
            ):
                prepared_sha = hashlib.sha256(prepared_text.encode("utf-8")).hexdigest()
                input_digest = self._input_identity(
                    source,
                    prepared_sha,
                    self._spec.model_digest,
                    self._spec.input_policy_version,
                )
                cache_key = passage_cache_key(
                    model_digest=self._spec.model_digest,
                    content_sha256=source.content_sha256,
                    prepared_passage_sha256=prepared_sha,
                    input_policy_version=self._spec.input_policy_version,
                )
                if existing is None:
                    record = EmbeddingRecord(
                        tenant_id=source.tenant_id,
                        hat_scope_id=source.hat_scope_id,
                        source_id=source.source_id,
                        knowledge_version_id=source.knowledge_version_id,
                        chunk_id=source.chunk_id,
                        content_sha256=source.content_sha256,
                        model_id=self._spec.model_id,
                        model_revision=self._spec.model_revision,
                        model_digest=self._spec.model_digest,
                        embedding_dimension=self._spec.embedding_dimension,
                        embedding_input_digest=input_digest,
                        embedding_bytes_sha256=vector.bytes_sha256,
                        cache_key=cache_key,
                        generation_backend=identity.backend_name,
                        generation_backend_version=identity.backend_version,
                        generation_backend_fingerprint=identity.backend_fingerprint,
                        truncated=was_truncated,
                    )
                else:
                    record = existing
                    if (
                        record.embedding_input_digest != input_digest
                        or record.embedding_bytes_sha256 != vector.bytes_sha256
                        or record.cache_key != cache_key
                        or record.truncated != was_truncated
                    ):
                        raise EmbeddingBoundaryError(
                            Step19ReasonCode.EMBEDDING_RECORD_CONFLICT
                        )
                self._cache.store(record, vector)
                if existing is None:
                    try:
                        self._runner.run(
                            context,
                            lambda transaction, record=record, vector=vector: self._repository.insert_record(
                                transaction, record, vector
                            ),
                            operation_kind="STEP19_INSERT_EMBEDDING_RECORD",
                        )
                    except PersistenceError as exc:
                        raise EmbeddingBoundaryError(
                            Step19ReasonCode.EMBEDDING_RECORD_CONFLICT
                        ) from exc
                records.append(record)
        records = sorted(
            records,
            key=lambda item: (
                item.tenant_id,
                item.hat_scope_id,
                item.source_id,
                item.knowledge_version_id,
                item.chunk_id,
            ),
        )
        if not records:
            reasons = (Step19ReasonCode.NO_MATCH,)
        else:
            reason_values = [Step19ReasonCode.EMBEDDING_GENERATION_OK]
            if cache_hits:
                reason_values.append(Step19ReasonCode.CACHE_HIT)
            if pending:
                reason_values.append(Step19ReasonCode.CACHE_MISS)
            reasons = tuple(reason_values)
        return EmbeddingGenerationResult(
            request_id=request.request_id,
            tenant_id=request.tenant_id,
            route_hash=request.route_hash,
            model_digest=request.model_digest,
            records=tuple(records),
            cache_hits=cache_hits,
            generated_count=len(pending),
            truncated=truncated,
            reason_codes=reasons,
        )


class VectorRetrievalService:
    """Return vector-local candidates; never fuse, rerank, answer, or act."""

    def __init__(
        self,
        runner: SerializableTransactionRunner,
        backend: EmbeddingBackend,
        repository: CockroachEmbeddingRepository | None = None,
    ) -> None:
        if not isinstance(runner, SerializableTransactionRunner):
            raise TypeError("runner must be SerializableTransactionRunner")
        self._runner = runner
        self._backend = backend
        self._repository = repository or CockroachEmbeddingRepository()
        self._spec = load_approved_model_spec()
        if self._backend.identity().model_digest != self._spec.model_digest:
            raise EmbeddingBoundaryError(Step19ReasonCode.MODEL_IDENTITY_INVALID)

    @staticmethod
    def _empty(
        request: VectorRetrievalRequest,
        reason: Step19ReasonCode,
    ) -> VectorRetrievalResult:
        return VectorRetrievalResult(
            request_id=request.request_id,
            tenant_id=request.tenant_id,
            user_id=request.user_id,
            route_hash=request.route_hash,
            selected_hat_id=request.selected_hat_id,
            selected_hat_version=request.selected_hat_version,
            selected_manifest_digest=request.selected_manifest_digest,
            hat_scope_id=request.hat_scope_id,
            effective_scope=request.effective_scope,
            model_digest=request.model_digest,
            query_digest=request.query_digest,
            candidates=(),
            truncated=False,
            reason_codes=(reason,),
        )

    def retrieve(self, request: VectorRetrievalRequest) -> VectorRetrievalResult:
        if not isinstance(request, VectorRetrievalRequest):
            raise EmbeddingBoundaryError(Step19ReasonCode.SCHEMA_UNSUPPORTED)
        try:
            verify_vector_request_hash(request)
        except (ContractValidationError, IntegrityError) as exc:
            raise EmbeddingBoundaryError(Step19ReasonCode.ROUTE_HASH_INVALID) from exc
        if not _require_hat_route(request.route.knowledge_route):
            return self._empty(request, Step19ReasonCode.NO_HAT_SELECTED)
        prepared = prepare_query(request.query_text, self._spec)
        assert_no_open_persistence_transaction()
        query_vector = self._backend.embed_query(prepared)
        context = _request_context(
            request.tenant_id,
            request.user_id,
            request.personal_memory_space_id,
        )
        try:
            found = tuple(
                self._runner.run(
                    context,
                    lambda transaction: self._repository.search_vectors(
                        transaction, request, query_vector
                    ),
                    operation_kind="STEP19_READ_ONLY_VECTOR_RETRIEVAL",
                )
            )
        except EmbeddingBoundaryError:
            raise
        except PersistenceError as exc:
            raise EmbeddingBoundaryError(Step19ReasonCode.DATABASE_ERROR) from exc
        def key(candidate: VectorRetrievalCandidate) -> tuple[Decimal, int, int, str]:
            version_ordinal = candidate.structured_metadata.get("version_ordinal")
            if isinstance(version_ordinal, bool) or not isinstance(version_ordinal, int):
                raise EmbeddingBoundaryError(Step19ReasonCode.SCHEMA_UNSUPPORTED)
            return (
                Decimal(candidate.vector_distance),
                version_ordinal,
                candidate.chunk_ordinal,
                candidate.chunk_id,
            )
        ordered = tuple(sorted(found, key=key))
        accepted: list[VectorRetrievalCandidate] = []
        total = 0
        truncated = len(ordered) > request.maximum_results
        for candidate in ordered:
            try:
                verify_vector_candidate_hash(candidate)
            except (ContractValidationError, IntegrityError) as exc:
                raise EmbeddingBoundaryError(Step19ReasonCode.SCHEMA_UNSUPPORTED) from exc
            if candidate.tenant_id != request.tenant_id:
                raise EmbeddingBoundaryError(Step19ReasonCode.TENANT_MISMATCH)
            if candidate.hat_scope_id != request.hat_scope_id:
                raise EmbeddingBoundaryError(Step19ReasonCode.HAT_SCOPE_MISMATCH)
            if candidate.effective_scope != request.effective_scope:
                raise EmbeddingBoundaryError(Step19ReasonCode.ROUTE_SCOPE_MISMATCH)
            expected_target = (
                MemoryTargetScope.USER_PERSONAL_HAT
                if request.personal_memory_space_id is not None
                else MemoryTargetScope.SHARED_KNOWLEDGE_HAT
            )
            if candidate.target_scope is not expected_target:
                raise EmbeddingBoundaryError(Step19ReasonCode.SOURCE_NOT_ELIGIBLE)
            if candidate.access_class.value == "USER_PRIVATE" and (
                candidate.owner_user_id != request.user_id
                or candidate.personal_memory_space_id != request.personal_memory_space_id
            ):
                raise EmbeddingBoundaryError(Step19ReasonCode.SOURCE_NOT_ELIGIBLE)
            size = len(candidate.content.encode("utf-8"))
            if len(accepted) >= request.maximum_results or total + size > MAXIMUM_TOTAL_CONTENT_BYTES:
                truncated = True
                break
            accepted.append(candidate)
            total += size
        if not accepted:
            return self._empty(request, Step19ReasonCode.NO_MATCH)
        return VectorRetrievalResult(
            request_id=request.request_id,
            tenant_id=request.tenant_id,
            user_id=request.user_id,
            route_hash=request.route_hash,
            selected_hat_id=request.selected_hat_id,
            selected_hat_version=request.selected_hat_version,
            selected_manifest_digest=request.selected_manifest_digest,
            hat_scope_id=request.hat_scope_id,
            effective_scope=request.effective_scope,
            model_digest=request.model_digest,
            query_digest=request.query_digest,
            candidates=tuple(accepted),
            truncated=truncated,
            reason_codes=(
                Step19ReasonCode.VECTOR_RETRIEVAL_OK,
                Step19ReasonCode.VECTOR_MATCH,
            ),
        )


__all__ = ["EmbeddingGenerationService", "VectorRetrievalService"]
