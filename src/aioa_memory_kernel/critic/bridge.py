"""Optional, non-authoritative Critic invocation and Step 28 mapping.

The provider call is outside persistence transactions.  Raw provider text is
strictly parsed and discarded before this module constructs a Step 28
``CorrectionCandidateEnvelope`` from Kernel-owned identity, route, scope and
slot inputs.  This module receives no Step 29 or Step 30 service or capability
and calls neither boundary.  Python initializes the existing
``personal_memory`` package before its Step 28 submodule; that import topology
grants no service object or callable authority.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, fields
from datetime import datetime
from typing import Callable

from aioa_memory_kernel.contracts.correction import CorrectionCandidate
from aioa_memory_kernel.contracts.enums import ActorType, CorrectionCandidateState
from aioa_memory_kernel.contracts.evidence import ClaimCandidate
from aioa_memory_kernel.contracts.exceptions import ContractValidationError, IntegrityError
from aioa_memory_kernel.contracts.identities import KernelRunIdentity
from aioa_memory_kernel.contracts.serialization import (
    canonical_sha256,
    ensure_utc,
    verify_canonical_hash,
)
from aioa_memory_kernel.modeling import (
    ModelAdapterError,
    ModelReasonCode,
    ProviderIdentity,
    ProviderResponse,
    ProviderTextRequest,
    TimeoutPolicy,
    verify_provider_text_request_hash,
    verify_provider_response_hash,
)
from aioa_memory_kernel.persistence.transaction import assert_no_open_persistence_transaction
from aioa_memory_kernel.personal_memory.candidates import (
    CorrectionCandidateEnvelope,
    CorrectionCandidateIntakeDisposition,
    CorrectionCandidateIntakeReceipt,
    CorrectionCandidateMetadata,
    CorrectionCandidateReasonCode,
    CorrectionCandidateRouteResultLineage,
    CorrectionCandidateTrigger,
    build_correction_candidate_envelope,
    build_correction_candidate_intake_receipt,
    verify_correction_candidate_envelope,
    verify_correction_candidate_intake_receipt,
)
from aioa_memory_kernel.personal_memory.models import PersonalMemoryHatSlot, verify_slot_hash

from .models import (
    CRITIC_BRIDGE_VERSION,
    STEP39_SCHEMA_VERSION,
    CriticArtifactKind,
    CriticAssessment,
    CriticBridgeResult,
    CriticBridgeStatus,
    CriticCandidateMappingResult,
    CriticCandidateMappingStatus,
    CriticProviderCallReceipt,
    CriticProviderCallStatus,
    CriticReviewRequest,
    verify_critic_assessment_against_request,
    verify_critic_bridge_result,
    verify_critic_candidate_mapping_result,
    verify_critic_review_request,
)
from .parser import build_critic_provider_request, parse_critic_assessment
from .protocols import CriticCandidateIntake, TextGenerationProvider


CRITIC_BRIDGE_PRODUCER_ID = "aoia-critic-prompt-loop-bridge"
CRITIC_PROVIDER_PURPOSE = "step39-critic-bounded-review"


def _verify_provider_request_integrity(value: ProviderTextRequest) -> None:
    """Deeply reconstruct a request before and after crossing the provider port."""

    try:
        verify_provider_text_request_hash(value)
        for nested in (value.provider_identity, value.generation_parameters):
            reconstructed_nested = type(nested)(
                **{
                    item.name: getattr(nested, item.name)
                    for item in fields(nested)
                    if item.init
                }
            )
            if reconstructed_nested != nested:
                raise IntegrityError("nested provider request contract was mutated")
        reconstructed = ProviderTextRequest(
            **{
                item.name: getattr(value, item.name)
                for item in fields(value)
                if item.init
            }
        )
    except Exception as error:
        raise IntegrityError("Critic provider request reconstruction failed") from error
    if reconstructed != value:
        raise IntegrityError("Critic provider request contract was mutated")


def _verified_timeout_policy(value: TimeoutPolicy) -> TimeoutPolicy:
    """Return a fresh bounded timeout after semantic reconstruction."""

    try:
        reconstructed = TimeoutPolicy(
            **{
                item.name: getattr(value, item.name)
                for item in fields(value)
                if item.init
            }
        )
    except Exception as error:
        raise IntegrityError("Critic timeout policy reconstruction failed") from error
    if reconstructed != value:
        raise IntegrityError("Critic timeout policy contract was mutated")
    return reconstructed


def _verified_provider_identity(value: object) -> ProviderIdentity:
    """Return one deeply reconstructed provider identity or fail closed."""

    if not isinstance(value, ProviderIdentity):
        raise IntegrityError("Critic provider identity must be typed")
    try:
        reconstructed = ProviderIdentity(
            **{
                item.name: getattr(value, item.name)
                for item in fields(value)
                if item.init
            }
        )
    except Exception as error:
        raise IntegrityError("Critic provider identity reconstruction failed") from error
    if reconstructed != value:
        raise IntegrityError("Critic provider identity contract was mutated")
    return reconstructed


@dataclass(frozen=True, slots=True)
class CriticTrustedCandidateContext:
    """Kernel-owned candidate target and lineage; never model supplied."""

    kernel_run: KernelRunIdentity
    target_slot: PersonalMemoryHatSlot
    route_result_lineage: CorrectionCandidateRouteResultLineage
    detected_at: datetime
    context_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.kernel_run, KernelRunIdentity):
            raise ContractValidationError("kernel_run must be typed")
        if not isinstance(self.target_slot, PersonalMemoryHatSlot):
            raise ContractValidationError("target_slot must be typed")
        if not isinstance(
            self.route_result_lineage, CorrectionCandidateRouteResultLineage
        ):
            raise ContractValidationError("route_result_lineage must be typed")
        verify_slot_hash(self.target_slot)
        detected_at = ensure_utc(self.detected_at, "detected_at")
        object.__setattr__(self, "detected_at", detected_at)
        run = self.kernel_run
        slot = self.target_slot
        lineage = self.route_result_lineage
        if (
            run.tenant_id != slot.tenant_id
            or run.user_id != slot.owner_user_id
            or run.personal_memory_space_id != slot.personal_memory_space_id
            or lineage.request_id != run.kernel_run_id
            or detected_at < run.created_at
        ):
            raise ContractValidationError("trusted Critic target lineage is detached")
        if len(
            tuple(
                binding
                for binding in slot.model_bindings
                if binding.binding_id == run.model_binding_id and binding.enabled
            )
        ) != 1:
            raise ContractValidationError("trusted Critic target model binding differs")
        object.__setattr__(
            self, "context_hash", canonical_sha256(self, exclude_fields=("context_hash",))
        )


def verify_critic_trusted_candidate_context(value: CriticTrustedCandidateContext) -> None:
    if not isinstance(value, CriticTrustedCandidateContext):
        raise ContractValidationError("trusted candidate context must be typed")
    verify_canonical_hash(value, value.context_hash, exclude_fields=("context_hash",))
    reconstructed = CriticTrustedCandidateContext(
        kernel_run=value.kernel_run,
        target_slot=value.target_slot,
        route_result_lineage=value.route_result_lineage,
        detected_at=value.detected_at,
    )
    if reconstructed != value:
        raise IntegrityError("trusted candidate context reconstruction mismatch")


def _provider_receipt(
    request: CriticReviewRequest,
    *,
    status: CriticProviderCallStatus,
    provider_request_hash: str | None,
    provider_response_hash: str | None,
    attempt_count: int,
    failed_reason_codes: tuple[str, ...],
    unknown_completion: bool = False,
) -> CriticProviderCallReceipt:
    return CriticProviderCallReceipt(
        critic_request_hash=request.request_hash,
        status=status,
        provider_request_hash=provider_request_hash,
        provider_response_hash=provider_response_hash,
        attempt_count=attempt_count,
        failed_reason_codes=failed_reason_codes,
        unknown_completion=unknown_completion,
    )


class CriticPromptLoopService:
    """Bounded provider-neutral Critic call that cannot block the core flow."""

    def __init__(
        self,
        provider: TextGenerationProvider | None,
        *,
        timeout_policy: TimeoutPolicy | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if provider is not None and not callable(getattr(provider, "generate", None)):
            raise TypeError("provider must implement TextGenerationProvider")
        if timeout_policy is not None and not isinstance(timeout_policy, TimeoutPolicy):
            raise TypeError("timeout_policy must be TimeoutPolicy")
        if not callable(sleep):
            raise TypeError("sleep must be callable")
        selected_timeout = timeout_policy or TimeoutPolicy()
        reconstructed_timeout = _verified_timeout_policy(selected_timeout)
        self._provider = provider
        self._timeout = reconstructed_timeout
        self._sleep = sleep

    def review(
        self,
        request: CriticReviewRequest,
        *,
        enabled: bool,
    ) -> CriticBridgeResult:
        verify_critic_review_request(request)
        if not isinstance(enabled, bool):
            raise TypeError("enabled must be boolean")
        if not enabled:
            return CriticBridgeResult(
                critic_request_hash=request.request_hash,
                status=CriticBridgeStatus.DISABLED,
                provider_call_receipt=_provider_receipt(
                    request,
                    status=CriticProviderCallStatus.NOT_RUN,
                    provider_request_hash=None,
                    provider_response_hash=None,
                    attempt_count=0,
                    failed_reason_codes=(),
                ),
                assessment=None,
                audit_draft_hashes=(),
            )
        if self._provider is None:
            return CriticBridgeResult(
                critic_request_hash=request.request_hash,
                status=CriticBridgeStatus.PROVIDER_UNAVAILABLE,
                provider_call_receipt=_provider_receipt(
                    request,
                    status=CriticProviderCallStatus.NOT_RUN,
                    provider_request_hash=None,
                    provider_response_hash=None,
                    attempt_count=0,
                    failed_reason_codes=("CRITIC_PROVIDER_UNAVAILABLE",),
                ),
                assessment=None,
                audit_draft_hashes=(),
            )

        try:
            provider_identity = _verified_provider_identity(
                self._provider.provider_identity()
            )
            provider_identity_matches = provider_identity == request.provider_identity
        except Exception:
            return self._provider_failure(
                request,
                provider_request_hash=None,
                attempt_count=0,
                failed_reason_codes=(ModelReasonCode.MODEL_IDENTITY_MISMATCH.value,),
            )
        if not provider_identity_matches:
            return self._provider_failure(
                request,
                provider_request_hash=None,
                attempt_count=0,
                failed_reason_codes=(ModelReasonCode.MODEL_IDENTITY_MISMATCH.value,),
            )

        try:
            provider_request = build_critic_provider_request(request)
        except (ContractValidationError, IntegrityError, TypeError, ValueError):
            return CriticBridgeResult(
                critic_request_hash=request.request_hash,
                status=CriticBridgeStatus.INVALID_REQUEST,
                provider_call_receipt=_provider_receipt(
                    request,
                    status=CriticProviderCallStatus.FAILED_CLOSED,
                    provider_request_hash=None,
                    provider_response_hash=None,
                    attempt_count=0,
                    failed_reason_codes=("CRITIC_REQUEST_OUTSIDE_PROVIDER_BOUNDS",),
                ),
                assessment=None,
                audit_draft_hashes=(),
            )
        failures: list[str] = []
        response: ProviderResponse | None = None
        attempt_count = 0
        unknown_completion = False
        for attempt_count in range(1, 3):
            assert_no_open_persistence_transaction()
            try:
                _verify_provider_request_integrity(provider_request)
            except (ContractValidationError, IntegrityError, TypeError, ValueError):
                return self._provider_failure(
                    request,
                    provider_request_hash=None,
                    attempt_count=attempt_count - 1,
                    failed_reason_codes=("CRITIC_PROVIDER_REQUEST_INTEGRITY_FAILURE",),
                    unknown_completion=unknown_completion,
                )
            try:
                attempt_timeout = _verified_timeout_policy(self._timeout)
            except IntegrityError:
                return self._provider_failure(
                    request,
                    provider_request_hash=None,
                    attempt_count=attempt_count - 1,
                    failed_reason_codes=("CRITIC_TIMEOUT_POLICY_INTEGRITY_FAILURE",),
                    unknown_completion=unknown_completion,
                )
            try:
                response = self._provider.generate(provider_request, attempt_timeout)
            except ModelAdapterError as error:
                unknown_completion = unknown_completion or error.unknown_completion
                try:
                    _verify_provider_request_integrity(provider_request)
                except IntegrityError:
                    return self._provider_failure(
                        request,
                        provider_request_hash=None,
                        attempt_count=attempt_count,
                        failed_reason_codes=(
                            "CRITIC_PROVIDER_REQUEST_INTEGRITY_FAILURE",
                        ),
                        unknown_completion=unknown_completion,
                    )
                try:
                    _verified_timeout_policy(attempt_timeout)
                except IntegrityError:
                    return self._provider_failure(
                        request,
                        provider_request_hash=provider_request.request_hash,
                        attempt_count=attempt_count,
                        failed_reason_codes=(
                            "CRITIC_TIMEOUT_POLICY_INTEGRITY_FAILURE",
                        ),
                        unknown_completion=unknown_completion,
                    )
                failures.append(error.reason_code.value)
                if not error.retryable or attempt_count == 2:
                    return self._provider_failure(
                        request,
                        provider_request_hash=provider_request.request_hash,
                        attempt_count=attempt_count,
                        failed_reason_codes=tuple(failures),
                        unknown_completion=unknown_completion,
                    )
                try:
                    self._sleep(0.05)
                except Exception:
                    return self._provider_failure(
                        request,
                        provider_request_hash=provider_request.request_hash,
                        attempt_count=attempt_count,
                        failed_reason_codes=tuple(
                            failures + ["CRITIC_RETRY_DELAY_FAILURE"]
                        ),
                        unknown_completion=unknown_completion,
                    )
                continue
            except Exception:
                try:
                    _verify_provider_request_integrity(provider_request)
                except IntegrityError:
                    return self._provider_failure(
                        request,
                        provider_request_hash=None,
                        attempt_count=attempt_count,
                        failed_reason_codes=(
                            "CRITIC_PROVIDER_REQUEST_INTEGRITY_FAILURE",
                        ),
                        unknown_completion=unknown_completion,
                    )
                try:
                    _verified_timeout_policy(attempt_timeout)
                except IntegrityError:
                    return self._provider_failure(
                        request,
                        provider_request_hash=provider_request.request_hash,
                        attempt_count=attempt_count,
                        failed_reason_codes=(
                            "CRITIC_TIMEOUT_POLICY_INTEGRITY_FAILURE",
                        ),
                        unknown_completion=unknown_completion,
                    )
                failures.append("CRITIC_PROVIDER_UNAVAILABLE")
                return self._provider_failure(
                    request,
                    provider_request_hash=provider_request.request_hash,
                    attempt_count=attempt_count,
                    failed_reason_codes=tuple(failures),
                    unknown_completion=unknown_completion,
                )
            break
        try:
            _verify_provider_request_integrity(provider_request)
        except (ContractValidationError, IntegrityError, TypeError, ValueError):
            return self._provider_failure(
                request,
                provider_request_hash=None,
                attempt_count=attempt_count,
                failed_reason_codes=("CRITIC_PROVIDER_REQUEST_INTEGRITY_FAILURE",),
                unknown_completion=unknown_completion,
            )
        try:
            _verified_timeout_policy(attempt_timeout)
        except IntegrityError:
            return self._provider_failure(
                request,
                provider_request_hash=provider_request.request_hash,
                attempt_count=attempt_count,
                failed_reason_codes=("CRITIC_TIMEOUT_POLICY_INTEGRITY_FAILURE",),
                unknown_completion=unknown_completion,
            )
        if response is None:
            return self._provider_failure(
                request,
                provider_request_hash=provider_request.request_hash,
                attempt_count=attempt_count,
                failed_reason_codes=(ModelReasonCode.MODEL_RETRY_EXHAUSTED.value,),
                unknown_completion=unknown_completion,
            )

        safe_response_hash: str | None = None
        try:
            if not isinstance(response, ProviderResponse):
                raise ContractValidationError("Critic provider response must be typed")
            verify_provider_response_hash(response)
            try:
                reconstructed_response = ProviderResponse(
                    **{
                        item.name: getattr(response, item.name)
                        for item in fields(response)
                        if item.init
                    }
                )
            except Exception as error:
                raise IntegrityError(
                    "Critic provider response reconstruction failed"
                ) from error
            if reconstructed_response != response or response.tool_calls_present is not False:
                raise IntegrityError("Critic provider response contract was mutated")
            safe_response_hash = response.response_hash
            if (
                response.provider_identity_digest
                != request.provider_identity.identity_digest
                or response.model_id != request.provider_identity.model_id
                or response.model_version
                != request.provider_identity.model_revision_or_declared_version
            ):
                raise IntegrityError("Critic provider response identity differs")
            assessment = parse_critic_assessment(
                response.response_content,
                request=request,
                provider_response_hash=response.response_hash,
            )
            verify_critic_assessment_against_request(assessment, request)
        except (ContractValidationError, IntegrityError, ValueError, TypeError):
            return CriticBridgeResult(
                critic_request_hash=request.request_hash,
                status=CriticBridgeStatus.INVALID_OUTPUT,
                provider_call_receipt=_provider_receipt(
                    request,
                    status=CriticProviderCallStatus.RESPONSE_REJECTED,
                    provider_request_hash=provider_request.request_hash,
                    provider_response_hash=safe_response_hash,
                    attempt_count=attempt_count,
                    failed_reason_codes=("CRITIC_INVALID_OUTPUT",),
                    unknown_completion=unknown_completion,
                ),
                assessment=None,
                audit_draft_hashes=(),
            )

        status = (
            CriticBridgeStatus.ASSESSMENT_ACCEPTED
            if assessment.issue_detected
            else CriticBridgeStatus.NO_ISSUE
        )
        return CriticBridgeResult(
            critic_request_hash=request.request_hash,
            status=status,
            provider_call_receipt=_provider_receipt(
                request,
                status=CriticProviderCallStatus.RESPONSE_ACCEPTED,
                provider_request_hash=provider_request.request_hash,
                provider_response_hash=response.response_hash,
                attempt_count=attempt_count,
                failed_reason_codes=tuple(failures),
                unknown_completion=unknown_completion,
            ),
            assessment=assessment,
            audit_draft_hashes=(),
        )

    @staticmethod
    def _provider_failure(
        request: CriticReviewRequest,
        *,
        provider_request_hash: str | None,
        attempt_count: int,
        failed_reason_codes: tuple[str, ...],
        unknown_completion: bool = False,
    ) -> CriticBridgeResult:
        return CriticBridgeResult(
            critic_request_hash=request.request_hash,
            status=CriticBridgeStatus.PROVIDER_UNAVAILABLE,
            provider_call_receipt=_provider_receipt(
                request,
                status=CriticProviderCallStatus.FAILED_CLOSED,
                provider_request_hash=provider_request_hash,
                provider_response_hash=None,
                attempt_count=attempt_count,
                failed_reason_codes=failed_reason_codes,
                unknown_completion=unknown_completion,
            ),
            assessment=None,
            audit_draft_hashes=(),
        )


def _verify_request_against_trusted_context(
    request: CriticReviewRequest,
    context: CriticTrustedCandidateContext,
) -> None:
    verify_critic_review_request(request)
    verify_critic_trusted_candidate_context(context)
    run = context.kernel_run
    slot = context.target_slot
    lineage = context.route_result_lineage
    if (
        request.tenant_id != run.tenant_id
        or request.owner_user_id != run.user_id
        or request.request_id != run.kernel_run_id
        or request.kernel_run_id != run.kernel_run_id
        or request.route_hash != lineage.route_hash
        or request.original_query_digest != lineage.original_query_digest
        or request.selected_hat_id != lineage.selected_hat_id
        or request.selected_hat_version != lineage.selected_hat_version
        or request.selected_manifest_digest != lineage.selected_manifest_digest
        or request.effective_scope != lineage.effective_scope
        or request.correction_packet_hash != lineage.correction_packet_hash
        or request.evidence_status is not lineage.evidence_status
        or run.personal_memory_space_id != slot.personal_memory_space_id
    ):
        raise IntegrityError("Critic request differs from trusted candidate context")
    artifact_hashes = {item.artifact_kind: item.artifact_hash for item in request.artifacts}
    if artifact_hashes[CriticArtifactKind.DRAFT_V1] != lineage.draft_v1_hash:
        raise IntegrityError("Critic Draft V1 differs from route/result lineage")
    if (
        CriticArtifactKind.DRAFT_V2 in artifact_hashes
        and artifact_hashes[CriticArtifactKind.DRAFT_V2] != lineage.draft_v2_hash
    ):
        raise IntegrityError("Critic Draft V2 differs from route/result lineage")
    if (
        CriticArtifactKind.VERIFIED_ANSWER in artifact_hashes
        and artifact_hashes[CriticArtifactKind.VERIFIED_ANSWER]
        != lineage.verified_answer_hash
    ):
        raise IntegrityError("Critic Verified Answer differs from route/result lineage")


def map_critic_assessment_to_step28(
    request: CriticReviewRequest,
    bridge_result: CriticBridgeResult,
    *,
    trusted_context: CriticTrustedCandidateContext | None,
) -> tuple[CriticCandidateMappingResult, CorrectionCandidateEnvelope | None]:
    """Map validated untrusted output to an inert Step 28 envelope only."""

    verify_critic_bridge_result(bridge_result)
    if (
        bridge_result.critic_request_hash != request.request_hash
        or bridge_result.status
        not in {CriticBridgeStatus.ASSESSMENT_ACCEPTED, CriticBridgeStatus.NO_ISSUE}
        or bridge_result.assessment is None
    ):
        raise IntegrityError("Critic mapping requires an accepted parsed bridge result")
    expected_provider_request = build_critic_provider_request(request)
    if (
        bridge_result.provider_call_receipt.provider_request_hash
        != expected_provider_request.request_hash
    ):
        raise IntegrityError("Critic provider request receipt differs from request")
    assessment = bridge_result.assessment
    verify_critic_assessment_against_request(assessment, request)
    if not assessment.issue_detected:
        return (
            CriticCandidateMappingResult(
                critic_request_hash=request.request_hash,
                assessment_hash=assessment.assessment_hash,
                status=CriticCandidateMappingStatus.NO_ISSUE,
                candidate_content_hash=None,
                candidate_envelope_hash=None,
                step28_required=False,
            ),
            None,
        )
    if trusted_context is None:
        return (
            CriticCandidateMappingResult(
                critic_request_hash=request.request_hash,
                assessment_hash=assessment.assessment_hash,
                status=CriticCandidateMappingStatus.DIAGNOSTIC_ONLY_NO_TARGET,
                candidate_content_hash=None,
                candidate_envelope_hash=None,
                step28_required=False,
            ),
            None,
        )
    _verify_request_against_trusted_context(request, trusted_context)
    claims = {item.claim_id: item for item in request.claim_references}
    candidate_claims = tuple(
        sorted(
            (
                ClaimCandidate(
                    claim_id=claims[claim_id].claim_id,
                    draft_id=claims[claim_id].draft_id,
                    statement=claims[claim_id].statement,
                    claim_category=claims[claim_id].claim_category,
                    scope_dimensions=trusted_context.route_result_lineage.effective_scope,
                )
                for claim_id in assessment.affected_claim_ids
            ),
            key=lambda value: value.claim_id,
        )
    )
    candidate = CorrectionCandidate(
        event_id=f"critic-candidate-event-{assessment.assessment_hash}",
        tenant_id=trusted_context.kernel_run.tenant_id,
        user_id=trusted_context.kernel_run.user_id,
        personal_memory_space_id=trusted_context.target_slot.personal_memory_space_id,
        source_component=ActorType.CRITIC_PROMPT_LOOP,
        run_id=trusted_context.kernel_run.kernel_run_id,
        model_binding_id=trusted_context.kernel_run.model_binding_id,
        draft_v1_reference=trusted_context.route_result_lineage.draft_v1_hash,
        detected_claims=candidate_claims,
        proposed_correction=assessment.candidate_correction_text,
        available_evidence_references=assessment.evidence_reference_ids,
        uncertainty=1.0,
        created_at=trusted_context.detected_at,
        state=CorrectionCandidateState.DETECTED,
    )
    metadata = CorrectionCandidateMetadata(
        schema_version="1.0.0",
        trigger=CorrectionCandidateTrigger.CRITIC_PROMPT_LOOP_DETECTED,
        producer_id=CRITIC_BRIDGE_PRODUCER_ID,
        producer_version=CRITIC_BRIDGE_VERSION,
        reason_codes=(CorrectionCandidateReasonCode.SOURCE_CRITIC_PROMPT_LOOP,),
    )
    envelope = build_correction_candidate_envelope(
        candidate=candidate,
        kernel_run=trusted_context.kernel_run,
        slot=trusted_context.target_slot,
        route_result_lineage=trusted_context.route_result_lineage,
        metadata=metadata,
        idempotency_key=f"critic-request-{request.request_hash}",
        submitted_at=trusted_context.detected_at,
    )
    verify_correction_candidate_envelope(envelope)
    return (
        CriticCandidateMappingResult(
            critic_request_hash=request.request_hash,
            assessment_hash=assessment.assessment_hash,
            status=CriticCandidateMappingStatus.CANDIDATE_READY,
            candidate_content_hash=candidate.content_hash,
            candidate_envelope_hash=envelope.envelope_hash,
            step28_required=True,
        ),
        envelope,
    )


def submit_critic_step28_candidate(
    intake: CriticCandidateIntake,
    request: CriticReviewRequest,
    bridge_result: CriticBridgeResult,
    trusted_context: CriticTrustedCandidateContext,
    mapping: CriticCandidateMappingResult,
    envelope: CorrectionCandidateEnvelope,
) -> tuple[CorrectionCandidateEnvelope, CorrectionCandidateIntakeReceipt]:
    """Invoke only Step 28 and prove the returned immutable hashes."""

    verify_critic_review_request(request)
    verify_critic_bridge_result(bridge_result)
    verify_critic_candidate_mapping_result(mapping)
    if (
        bridge_result.critic_request_hash != request.request_hash
        or bridge_result.status is not CriticBridgeStatus.ASSESSMENT_ACCEPTED
        or bridge_result.assessment is None
        or mapping.critic_request_hash != request.request_hash
        or mapping.assessment_hash != bridge_result.assessment.assessment_hash
    ):
        raise IntegrityError("Step 28 submission is detached from parsed Critic result")
    expected_mapping, expected_envelope = map_critic_assessment_to_step28(
        request,
        bridge_result,
        trusted_context=trusted_context,
    )
    if (
        expected_envelope is None
        or expected_mapping != mapping
        or expected_envelope != envelope
    ):
        raise IntegrityError("Step 28 submission differs from canonical Critic mapping")
    if mapping.status is not CriticCandidateMappingStatus.CANDIDATE_READY:
        raise ContractValidationError("only a ready mapping can enter Step 28")
    verify_correction_candidate_envelope(envelope)
    if (
        mapping.candidate_content_hash != envelope.submission.candidate.content_hash
        or mapping.candidate_envelope_hash != envelope.envelope_hash
    ):
        raise IntegrityError("Critic mapping differs from Step 28 envelope")
    stored, receipt = intake.submit_critic_loop_candidate(envelope)
    verify_correction_candidate_envelope(stored)
    verify_correction_candidate_intake_receipt(receipt)
    if receipt.disposition not in {
        CorrectionCandidateIntakeDisposition.ACCEPTED,
        CorrectionCandidateIntakeDisposition.DUPLICATE,
        CorrectionCandidateIntakeDisposition.EXACT_REPLAY,
    }:
        raise IntegrityError("Step 28 Critic intake disposition is outside contract")
    expected_receipt = build_correction_candidate_intake_receipt(
        stored,
        accepted_at=receipt.accepted_at,
        disposition=receipt.disposition,
    )
    if (
        stored.envelope_hash != envelope.envelope_hash
        or receipt != expected_receipt
        or receipt.envelope_hash != stored.envelope_hash
        or receipt.candidate_content_hash != stored.submission.candidate.content_hash
    ):
        raise IntegrityError("Step 28 Critic intake acknowledgement differs")
    return stored, receipt


__all__ = [
    "CRITIC_BRIDGE_PRODUCER_ID",
    "CRITIC_PROVIDER_PURPOSE",
    "CriticPromptLoopService",
    "CriticTrustedCandidateContext",
    "map_critic_assessment_to_step28",
    "submit_critic_step28_candidate",
    "verify_critic_trusted_candidate_context",
]
