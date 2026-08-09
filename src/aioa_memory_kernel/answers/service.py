"""Step 26 orchestration: verified answer, one retry, or fail closed."""

from __future__ import annotations

from aioa_memory_kernel.contracts.exceptions import ContractValidationError, IntegrityError
from aioa_memory_kernel.corrections import (
    CorrectionPacketAuthenticator,
    verify_packet_authenticity,
)
from aioa_memory_kernel.modeling import ModelAdapterError, TextGenerationProvider, TrustedClock
from aioa_memory_kernel.verification import DraftV2LayeredVerifier

from .assembler import (
    assemble_verified_answer,
    build_bounded_failure,
    build_human_review_result,
)
from .models import (
    FinalAnswerOutcome,
    FinalAnswerRequest,
    FinalOutputStatus,
    FinalRetryRecord,
    FinalRetryResult,
    Step26ReasonCode,
    reason_codes,
    verify_final_answer_request_hash,
)
from .policy import evaluate_final_eligibility
from .retry import execute_final_retry, prepare_final_retry_request


class VerifiedAnswerService:
    """Apply final policy without acquiring retrieval, approval, or execution power."""

    def __init__(
        self,
        authenticator: CorrectionPacketAuthenticator,
        *,
        provider: TextGenerationProvider | None = None,
        verifier: DraftV2LayeredVerifier | None = None,
        clock: TrustedClock | None = None,
    ) -> None:
        if not callable(getattr(authenticator, "verify", None)):
            raise TypeError("authenticator must implement CorrectionPacketAuthenticator")
        if provider is not None and (
            not callable(getattr(provider, "provider_identity", None))
            or not callable(getattr(provider, "generate", None))
        ):
            raise TypeError("provider must implement TextGenerationProvider")
        if verifier is not None and not callable(getattr(verifier, "verify", None)):
            raise TypeError("verifier must implement the Step 25 verifier boundary")
        if clock is not None and not callable(getattr(clock, "now", None)):
            raise TypeError("clock must implement TrustedClock")
        self._authenticator = authenticator
        self._provider = provider
        self._verifier = verifier
        self._clock = clock

    def _outcome(
        self,
        request: FinalAnswerRequest,
        *,
        answer=None,
        review=None,
        failure=None,
        retry_record=None,
    ) -> FinalAnswerOutcome:
        payload = answer or review or failure
        return FinalAnswerOutcome(
            request_hash=request.request_hash,
            output_status=payload.output_status,
            verified_answer=answer,
            human_review=review,
            bounded_failure=failure,
            retry_record=retry_record,
        )

    def _integrity_failure(self, request: FinalAnswerRequest) -> FinalAnswerOutcome:
        failure = build_bounded_failure(
            request,
            FinalOutputStatus.INTEGRITY_FAILURE,
            extra_reasons=(Step26ReasonCode.INTEGRITY_FAILURE,),
        )
        return self._outcome(request, failure=failure)

    def finalize(self, request: FinalAnswerRequest) -> FinalAnswerOutcome:
        if not isinstance(request, FinalAnswerRequest):
            raise TypeError("request must be FinalAnswerRequest")
        try:
            verify_final_answer_request_hash(request)
            if request.correction_packet is not None:
                assert request.integrity_receipt is not None
                verify_packet_authenticity(
                    request.correction_packet,
                    request.integrity_receipt,
                    self._authenticator,
                )
        except (ContractValidationError, IntegrityError, RuntimeError):
            return self._integrity_failure(request)

        decision = evaluate_final_eligibility(request)
        if decision.output_status is FinalOutputStatus.VERIFIED_ANSWER:
            try:
                answer = assemble_verified_answer(request)
            except (ContractValidationError, IntegrityError, RuntimeError):
                return self._integrity_failure(request)
            return self._outcome(request, answer=answer)

        if decision.output_status is FinalOutputStatus.RETRY_REQUIRED:
            return self._retry_once(request)

        if decision.output_status is FinalOutputStatus.CONFIRMATION_REQUIRED:
            review = build_human_review_result(
                request,
                confirmation=True,
                extra_reasons=decision.reason_codes,
            )
            return self._outcome(request, review=review)

        if decision.output_status in {
            FinalOutputStatus.HUMAN_REVIEW_REQUIRED,
            FinalOutputStatus.CONFLICTING_EVIDENCE,
        }:
            review = build_human_review_result(
                request,
                extra_reasons=decision.reason_codes,
            )
            return self._outcome(request, review=review)

        failure_reasons = list(decision.reason_codes)
        if request.route.knowledge_route.value == "HAT_ENFORCE":
            failure_reasons.append(
                Step26ReasonCode.HAT_ENFORCE_DRAFT_V1_FALLBACK_FORBIDDEN
            )
        failure = build_bounded_failure(
            request,
            decision.output_status,
            extra_reasons=reason_codes(*failure_reasons),
        )
        return self._outcome(request, failure=failure)

    def _retry_once(self, request: FinalAnswerRequest) -> FinalAnswerOutcome:
        assert request.step25_result is not None
        original = request.step25_result
        if self._provider is None:
            review = build_human_review_result(
                request,
                pipelines=(original,),
                extra_reasons=reason_codes(
                    Step26ReasonCode.FINAL_RETRY_FAILED,
                    Step26ReasonCode.FINAL_RETRY_EXHAUSTED,
                    Step26ReasonCode.HUMAN_REVIEW_REQUIRED,
                    Step26ReasonCode.HAT_ENFORCE_DRAFT_V1_FALLBACK_FORBIDDEN,
                ),
            )
            return self._outcome(request, review=review)
        try:
            retry_request = prepare_final_retry_request(request, self._authenticator)
            retry_pipeline = execute_final_retry(
                retry_request,
                self._provider,
                self._authenticator,
                verifier=self._verifier,
                clock=self._clock,
            )
        except (ModelAdapterError, ContractValidationError, IntegrityError, RuntimeError):
            review = build_human_review_result(
                request,
                pipelines=(original,),
                extra_reasons=reason_codes(
                    Step26ReasonCode.FINAL_RETRY_FAILED,
                    Step26ReasonCode.FINAL_RETRY_EXHAUSTED,
                    Step26ReasonCode.HUMAN_REVIEW_REQUIRED,
                    Step26ReasonCode.HAT_ENFORCE_DRAFT_V1_FALLBACK_FORBIDDEN,
                ),
            )
            return self._outcome(request, review=review)

        retry_candidate = FinalAnswerRequest(
            route=request.route,
            policy_result=request.policy_result,
            step20_outcomes=request.step20_outcomes,
            temporal_result=request.temporal_result,
            draft_v1=request.draft_v1,
            correction_packet=request.correction_packet,
            integrity_receipt=request.integrity_receipt,
            step25_result=retry_pipeline,
            final_policy=request.final_policy,
        )
        retry_decision = evaluate_final_eligibility(retry_candidate)
        succeeded = retry_decision.output_status is FinalOutputStatus.VERIFIED_ANSWER
        retry_record = FinalRetryRecord(
            retry_number=1,
            retry_request_hash=retry_request.request_hash,
            correction_packet_hash=retry_request.correction_packet.packet_hash,
            original_draft_v2_hash=original.draft_v2.draft_v2_hash,
            original_verification_summary_hash=(
                original.verification_summary.summary_hash
            ),
            retry_draft_v2_hash=retry_pipeline.draft_v2.draft_v2_hash,
            retry_verification_summary_hash=(
                retry_pipeline.verification_summary.summary_hash
            ),
            full_reverification_performed=True,
            new_evidence_used=False,
            result=(
                FinalRetryResult.SUCCEEDED
                if succeeded
                else FinalRetryResult.FAILED
            ),
            reason_codes=reason_codes(
                Step26ReasonCode.RETRY_SAME_PACKET,
                Step26ReasonCode.RETRY_FULL_REVERIFICATION,
                Step26ReasonCode.RETRY_NEW_EVIDENCE_FORBIDDEN,
                (
                    Step26ReasonCode.FINAL_RETRY_SUCCEEDED
                    if succeeded
                    else Step26ReasonCode.FINAL_RETRY_FAILED
                ),
            ),
        )
        if succeeded:
            answer = assemble_verified_answer(request, pipeline=retry_pipeline)
            return self._outcome(
                request,
                answer=answer,
                retry_record=retry_record,
            )
        review = build_human_review_result(
            request,
            pipelines=(original, retry_pipeline),
            extra_reasons=reason_codes(
                Step26ReasonCode.FINAL_RETRY_FAILED,
                Step26ReasonCode.FINAL_RETRY_EXHAUSTED,
                Step26ReasonCode.HUMAN_REVIEW_REQUIRED,
                Step26ReasonCode.HAT_ENFORCE_DRAFT_V1_FALLBACK_FORBIDDEN,
            ),
        )
        return self._outcome(
            request,
            review=review,
            retry_record=retry_record,
        )


__all__ = ["VerifiedAnswerService"]
