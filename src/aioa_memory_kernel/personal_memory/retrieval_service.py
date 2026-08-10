"""Read-only Step 31 ACTIVE Personal Memory retrieval service."""

from __future__ import annotations

from aioa_memory_kernel.contracts.exceptions import (
    ContractValidationError,
    IntegrityError,
)
from aioa_memory_kernel.persistence import (
    AccessMode,
    RequestContext,
    SerializableTransactionRunner,
)
from aioa_memory_kernel.persistence.errors import PersistenceError
from aioa_memory_kernel.routing import KnowledgeRouteResult
from aioa_memory_kernel.temporal import TemporalResolutionResult

from .repository import PersonalMemoryCockroachRepository
from .retrieval import (
    MAXIMUM_ACTIVE_PATCH_CANDIDATES,
    ActivePatchAssessment,
    ActivePatchRetrievalError,
    ActivePatchRetrievalRequest,
    ActivePatchRetrievalResult,
    PersonalMemoryContextEnvelope,
    RetrievedActivePatch,
    Step31ReasonCode,
    assess_active_patch,
    build_active_patch_retrieval_result,
    build_personal_memory_context_envelope,
    retrieved_active_patch,
    verify_active_patch_retrieval_inputs,
)
from .retrieval_repository import ActivePatchRetrievalCockroachRepository


class ActivePatchRetrievalService:
    """Owner-scoped deterministic retrieval with no semantic write surface."""

    def __init__(
        self,
        transaction_runner: SerializableTransactionRunner,
        *,
        slot_repository: PersonalMemoryCockroachRepository | None = None,
        retrieval_repository: ActivePatchRetrievalCockroachRepository | None = None,
    ) -> None:
        if not isinstance(transaction_runner, SerializableTransactionRunner):
            raise TypeError("transaction_runner must be SerializableTransactionRunner")
        self._runner = transaction_runner
        self._slots = slot_repository or PersonalMemoryCockroachRepository()
        self._retrieval = (
            retrieval_repository or ActivePatchRetrievalCockroachRepository()
        )

    @staticmethod
    def _context(request: ActivePatchRetrievalRequest) -> RequestContext:
        return RequestContext(
            tenant_id=request.tenant_id,
            user_id=request.user_id,
            access_mode=AccessMode.USER_PRIVATE,
        )

    def retrieve(
        self,
        request: ActivePatchRetrievalRequest,
        *,
        route: KnowledgeRouteResult,
        temporal_result: TemporalResolutionResult,
    ) -> tuple[ActivePatchRetrievalResult, PersonalMemoryContextEnvelope]:
        verify_active_patch_retrieval_inputs(request, route, temporal_result)

        def work(transaction):
            slot = self._slots.get_slot(
                transaction,
                request.tenant_id,
                request.user_id,
                request.personal_memory_space_id,
            )
            if slot is None or not slot.retrieval_eligible:
                result = build_active_patch_retrieval_result(
                    request,
                    eligible_patches=(),
                    excluded_assessments=(),
                    truncated_patch_hashes=(),
                    considered_count=0,
                )
                return result, build_personal_memory_context_envelope(request, result)
            try:
                rows = self._retrieval.list_active_patch_candidates(
                    transaction,
                    tenant_id=request.tenant_id,
                    owner_user_id=request.user_id,
                    personal_memory_space_id=request.personal_memory_space_id,
                    hat_scope_id=slot.hat_scope_id,
                    limit=MAXIMUM_ACTIVE_PATCH_CANDIDATES + 1,
                    knowledge_as_of=request.knowledge_as_of,
                )
            except (ContractValidationError, IntegrityError) as exc:
                raise ActivePatchRetrievalError(
                    Step31ReasonCode.PATCH_ACTIVATION_RECEIPT_INVALID
                ) from exc
            overflow = rows[MAXIMUM_ACTIVE_PATCH_CANDIDATES:]
            considered = rows[:MAXIMUM_ACTIVE_PATCH_CANDIDATES]
            eligible: list[RetrievedActivePatch] = []
            excluded: list[ActivePatchAssessment] = []
            for candidate in considered:
                assessment, model_binding = assess_active_patch(
                    request,
                    temporal_result=temporal_result,
                    slot=slot,
                    candidate=candidate,
                )
                if assessment.eligible:
                    if model_binding is None:
                        raise ActivePatchRetrievalError(
                            Step31ReasonCode.MODEL_BINDING_MISMATCH
                        )
                    eligible.append(
                        retrieved_active_patch(
                            request,
                            candidate,
                            assessment,
                            model_binding,
                        )
                    )
                else:
                    excluded.append(assessment)
            eligible.sort(key=lambda item: item.patch_id)
            returned = tuple(eligible[: request.maximum_results])
            omitted = eligible[request.maximum_results :]
            truncated_hashes = {
                item.patch_hash for item in omitted
            } | {
                item.lifecycle_state.committed_patch.patch_hash
                for item in overflow
                if item.lifecycle_state.committed_patch is not None
            }
            result = build_active_patch_retrieval_result(
                request,
                eligible_patches=returned,
                excluded_assessments=excluded,
                truncated_patch_hashes=tuple(sorted(truncated_hashes)),
                considered_count=len(considered),
            )
            return result, build_personal_memory_context_envelope(request, result)

        try:
            return self._runner.run(
                self._context(request),
                work,
                operation_kind="PERSONAL_MEMORY_ACTIVE_PATCH_RETRIEVAL",
            )
        except ActivePatchRetrievalError:
            raise
        except PersistenceError:
            raise
        except (ContractValidationError, IntegrityError) as exc:
            raise ActivePatchRetrievalError(
                Step31ReasonCode.INPUT_INTEGRITY_INVALID
            ) from exc


__all__ = ["ActivePatchRetrievalService"]
