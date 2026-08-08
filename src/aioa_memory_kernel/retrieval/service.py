"""Step 18 route-bound retrieval orchestration with no external effects."""

from __future__ import annotations

from decimal import Decimal

from aioa_memory_kernel.contracts.enums import KnowledgeRoute, MemoryTargetScope
from aioa_memory_kernel.contracts.exceptions import ContractValidationError, IntegrityError
from aioa_memory_kernel.persistence import (
    AccessMode,
    PersistenceError,
    RequestContext,
    SerializableTransactionRunner,
)

from .models import (
    MAXIMUM_TOTAL_CONTENT_BYTES,
    RetrievalBoundaryError,
    RetrievalCandidate,
    RetrievalMode,
    RetrievalRequest,
    RetrievalResult,
    Step18ReasonCode,
    selector_hash,
    verify_candidate_hash,
    verify_request_hash,
)
from .repository import CockroachRetrievalRepository


_MATCH_REASON = {
    RetrievalMode.EXACT_IDENTIFIER: Step18ReasonCode.EXACT_MATCH,
    RetrievalMode.STATUTE_SECTION: Step18ReasonCode.STATUTE_SECTION_MATCH,
    RetrievalMode.FULL_TEXT: Step18ReasonCode.FULL_TEXT_MATCH,
    RetrievalMode.KEYWORD: Step18ReasonCode.KEYWORD_MATCH,
}


def _ordered_candidates(
    candidates: tuple[RetrievalCandidate, ...],
    mode: RetrievalMode,
) -> tuple[RetrievalCandidate, ...]:
    """Apply the canonical retrieval-local ordering as defense in depth."""

    def stable_identity(candidate: RetrievalCandidate) -> tuple[int, int, str]:
        version_ordinal = candidate.structured_metadata.get("version_ordinal")
        if isinstance(version_ordinal, bool) or not isinstance(version_ordinal, int):
            raise RetrievalBoundaryError(
                Step18ReasonCode.RETRIEVAL_SCHEMA_UNSUPPORTED
            )
        return version_ordinal, candidate.chunk_ordinal, candidate.chunk_id

    if mode in {RetrievalMode.FULL_TEXT, RetrievalMode.KEYWORD}:
        def lexical_key(
            candidate: RetrievalCandidate,
        ) -> tuple[Decimal, int, int, str]:
            if candidate.retrieval_score is None:
                raise RetrievalBoundaryError(
                    Step18ReasonCode.RETRIEVAL_SCHEMA_UNSUPPORTED
                )
            return (-Decimal(candidate.retrieval_score), *stable_identity(candidate))

        return tuple(sorted(candidates, key=lexical_key))
    return tuple(sorted(candidates, key=stable_identity))


class RetrievalService:
    """Resolve bounded metadata candidates; never generate an answer or action."""

    def __init__(
        self,
        runner: SerializableTransactionRunner,
        repository: CockroachRetrievalRepository | None = None,
    ) -> None:
        if not isinstance(runner, SerializableTransactionRunner):
            raise TypeError("runner must be SerializableTransactionRunner")
        self._runner = runner
        self._repository = repository or CockroachRetrievalRepository()

    @staticmethod
    def _empty(request: RetrievalRequest, reason: Step18ReasonCode) -> RetrievalResult:
        return RetrievalResult(
            request_id=request.request_id,
            tenant_id=request.tenant_id,
            user_id=request.user_id,
            route_hash=request.route_hash,
            selected_hat_id=request.selected_hat_id,
            selected_hat_version=request.selected_hat_version,
            selected_manifest_digest=request.selected_manifest_digest,
            hat_scope_id=request.hat_scope_id,
            effective_scope=request.effective_scope,
            retrieval_mode=request.retrieval_mode,
            query_digest=selector_hash(request.selector),
            candidates=(),
            truncated=False,
            reason_codes=(reason,),
        )

    def retrieve(self, request: RetrievalRequest) -> RetrievalResult:
        if not isinstance(request, RetrievalRequest):
            raise RetrievalBoundaryError(Step18ReasonCode.RETRIEVAL_SCHEMA_UNSUPPORTED)
        try:
            verify_request_hash(request)
        except (ContractValidationError, IntegrityError) as exc:
            raise RetrievalBoundaryError(Step18ReasonCode.ROUTE_HASH_INVALID) from exc
        if request.route.knowledge_route is KnowledgeRoute.PASS_THROUGH:
            return self._empty(request, Step18ReasonCode.NO_HAT_SELECTED)
        if request.route.knowledge_route is KnowledgeRoute.AMBIGUOUS:
            raise RetrievalBoundaryError(Step18ReasonCode.AMBIGUOUS_ROUTE)
        if request.route.knowledge_route not in {KnowledgeRoute.HAT_ASSIST, KnowledgeRoute.HAT_ENFORCE}:
            raise RetrievalBoundaryError(Step18ReasonCode.AMBIGUOUS_ROUTE)
        context = RequestContext(
            request.tenant_id,
            request.user_id if request.personal_memory_space_id is not None else None,
            AccessMode.USER_PRIVATE if request.personal_memory_space_id is not None else AccessMode.TENANT_SHARED,
        )
        try:
            found = self._runner.run(
                context,
                lambda transaction: self._repository.search(transaction, request),
                operation_kind="STEP18_READ_ONLY_RETRIEVAL",
            )
        except RetrievalBoundaryError:
            raise
        except PersistenceError as exc:
            raise RetrievalBoundaryError(Step18ReasonCode.RETRIEVAL_DATABASE_ERROR) from exc
        found = _ordered_candidates(tuple(found), request.retrieval_mode)
        accepted: list[RetrievalCandidate] = []
        total = 0
        truncated = len(found) > request.maximum_results
        for candidate in found:
            try:
                verify_candidate_hash(candidate)
            except (ContractValidationError, IntegrityError) as exc:
                raise RetrievalBoundaryError(Step18ReasonCode.RETRIEVAL_SCHEMA_UNSUPPORTED) from exc
            if candidate.tenant_id != request.tenant_id:
                raise RetrievalBoundaryError(Step18ReasonCode.TENANT_MISMATCH)
            if candidate.hat_scope_id != request.hat_scope_id:
                raise RetrievalBoundaryError(Step18ReasonCode.HAT_SCOPE_MISMATCH)
            if candidate.effective_scope != request.effective_scope:
                raise RetrievalBoundaryError(Step18ReasonCode.ROUTE_SCOPE_MISMATCH)
            expected_target = (
                MemoryTargetScope.USER_PERSONAL_HAT
                if request.personal_memory_space_id is not None
                else MemoryTargetScope.SHARED_KNOWLEDGE_HAT
            )
            if candidate.target_scope is not expected_target:
                raise RetrievalBoundaryError(Step18ReasonCode.ACCESS_CLASS_REJECTED)
            if candidate.access_class.value == "USER_PRIVATE" and (
                candidate.owner_user_id != request.user_id
                or candidate.personal_memory_space_id != request.personal_memory_space_id
            ):
                raise RetrievalBoundaryError(Step18ReasonCode.OWNER_SCOPE_REJECTED)
            size = len(candidate.content.encode("utf-8"))
            if len(accepted) >= request.maximum_results or total + size > MAXIMUM_TOTAL_CONTENT_BYTES:
                truncated = True
                break
            accepted.append(candidate)
            total += size
        if not accepted:
            return self._empty(request, Step18ReasonCode.NO_MATCH)
        return RetrievalResult(
            request_id=request.request_id,
            tenant_id=request.tenant_id,
            user_id=request.user_id,
            route_hash=request.route_hash,
            selected_hat_id=request.selected_hat_id,
            selected_hat_version=request.selected_hat_version,
            selected_manifest_digest=request.selected_manifest_digest,
            hat_scope_id=request.hat_scope_id,
            effective_scope=request.effective_scope,
            retrieval_mode=request.retrieval_mode,
            query_digest=selector_hash(request.selector),
            candidates=tuple(accepted),
            truncated=truncated,
            reason_codes=(Step18ReasonCode.RETRIEVAL_OK, _MATCH_REASON[request.retrieval_mode]),
        )


__all__ = ["RetrievalService"]
