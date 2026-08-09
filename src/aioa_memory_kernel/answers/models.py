"""Immutable Step 26 verified-answer and fail-closed output contracts.

These contracts decide whether an already verified Draft V2 may be exposed as
an answer.  They grant no execution, approval, retrieval, or memory capability.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field

from aioa_memory_kernel.contracts.enums import (
    AnswerStatus,
    EvidenceStatus,
    KnowledgeRoute,
    StableStringEnum,
)
from aioa_memory_kernel.contracts.exceptions import ContractValidationError, IntegrityError
from aioa_memory_kernel.contracts.scope import ScopeDimension
from aioa_memory_kernel.contracts.serialization import (
    canonical_sha256,
    require_enum_member,
    require_sha256_hex,
    verify_canonical_hash,
)
from aioa_memory_kernel.corrections import (
    CorrectionCitation,
    CorrectionPacketIntegrityReceipt,
    CorrectionPacketV1A,
    verify_citation_hash,
    verify_correction_packet_hash,
    verify_integrity_receipt_hash,
)
from aioa_memory_kernel.evidence import (
    HybridEvidenceOutcome,
    verify_evidence_bundle_hash,
    verify_outcome_hash,
)
from aioa_memory_kernel.modeling import (
    AttemptPolicy,
    DraftV1,
    GenerationParameters,
    PromptTemplate,
    ProviderIdentity,
    TimeoutPolicy,
    load_approved_provider_spec,
    verify_draft_v1_hash,
)
from aioa_memory_kernel.routing import (
    ExecutionAuthorizationDecision,
    KnowledgePolicyDecision,
    KnowledgeRouteResult,
    PolicyGateResult,
    verify_policy_result_hash,
    verify_route_hash,
)
from aioa_memory_kernel.temporal import (
    TemporalResolutionResult,
    verify_temporal_result_hash,
)
from aioa_memory_kernel.verification import (
    DraftV2PipelineResult,
    FinalStep25ClaimVerdict,
    LayeredClaimVerification,
    verify_draft_v2_pipeline_result_hash,
)


STEP26_SCHEMA_VERSION = "1.0.0"
FINAL_ANSWER_POLICY_ID = "verified-answer-fail-closed-output-1a"
FINAL_ANSWER_POLICY_VERSION = "1"
FINAL_RETRY_PROMPT_ID = "verified-answer-single-correction-retry-1a"
FINAL_RETRY_PROMPT_VERSION = "1"
FINAL_RETRY_GENERATION_POLICY_ID = "verified-answer-retry-generation-1a"
FINAL_RETRY_TIMEOUT_POLICY_ID = "verified-answer-retry-timeout-1a"
FINAL_RETRY_ATTEMPT_POLICY_ID = "verified-answer-single-attempt-1a"
MAX_FINAL_CORRECTION_RETRIES = 1
MAX_ANSWER_UTF8_BYTES = 64 * 1024
MAX_FAILURE_MESSAGE_UTF8_BYTES = 512
MAX_LIMITATIONS = 128
MAX_CITATIONS = 256
MAX_CLAIM_VERIFICATIONS = 256
PERSISTENCE_DECISION = "STEP26_IMMUTABLE_RUNTIME_OUTPUT_NO_SAFE_STEP4_TABLE"


class FinalOutputStatus(StableStringEnum):
    VERIFIED_ANSWER = "VERIFIED_ANSWER"
    RETRY_REQUIRED = "RETRY_REQUIRED"
    HUMAN_REVIEW_REQUIRED = "HUMAN_REVIEW_REQUIRED"
    CONFIRMATION_REQUIRED = "CONFIRMATION_REQUIRED"
    BLOCKED_POLICY = "BLOCKED_POLICY"
    FAILED_VERIFICATION = "FAILED_VERIFICATION"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    CONFLICTING_EVIDENCE = "CONFLICTING_EVIDENCE"
    STALE_EVIDENCE = "STALE_EVIDENCE"
    UNAVAILABLE_EVIDENCE = "UNAVAILABLE_EVIDENCE"
    INTEGRITY_FAILURE = "INTEGRITY_FAILURE"


class FinalFailureClass(StableStringEnum):
    POLICY_BLOCK = "POLICY_BLOCK"
    AMBIGUOUS_ROUTE = "AMBIGUOUS_ROUTE"
    PASS_THROUGH_NOT_AUTHORIZED = "PASS_THROUGH_NOT_AUTHORIZED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    CONFLICTING_EVIDENCE = "CONFLICTING_EVIDENCE"
    STALE_EVIDENCE = "STALE_EVIDENCE"
    UNAVAILABLE_EVIDENCE = "UNAVAILABLE_EVIDENCE"
    INVALID_EVIDENCE = "INVALID_EVIDENCE"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    INTEGRITY_FAILURE = "INTEGRITY_FAILURE"


class FinalRetryResult(StableStringEnum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class Step26ReasonCode(StableStringEnum):
    ANSWER_VERIFIED = "ANSWER_VERIFIED"
    ANSWER_BLOCKED_POLICY = "ANSWER_BLOCKED_POLICY"
    ANSWER_REQUIRE_CONFIRMATION = "ANSWER_REQUIRE_CONFIRMATION"
    ANSWER_INSUFFICIENT_EVIDENCE = "ANSWER_INSUFFICIENT_EVIDENCE"
    ANSWER_CONFLICTING_EVIDENCE = "ANSWER_CONFLICTING_EVIDENCE"
    ANSWER_STALE_EVIDENCE = "ANSWER_STALE_EVIDENCE"
    ANSWER_UNAVAILABLE_EVIDENCE = "ANSWER_UNAVAILABLE_EVIDENCE"
    DRAFT_V2_VERIFICATION_FAILED = "DRAFT_V2_VERIFICATION_FAILED"
    PROHIBITED_CLAIM_VIOLATION = "PROHIBITED_CLAIM_VIOLATION"
    REQUIRED_CORRECTION_UNSATISFIED = "REQUIRED_CORRECTION_UNSATISFIED"
    INVALID_CITATION = "INVALID_CITATION"
    UNVERIFIED_CLAIM_REMAINS = "UNVERIFIED_CLAIM_REMAINS"
    FINAL_RETRY_REQUIRED = "FINAL_RETRY_REQUIRED"
    FINAL_RETRY_SUCCEEDED = "FINAL_RETRY_SUCCEEDED"
    FINAL_RETRY_FAILED = "FINAL_RETRY_FAILED"
    FINAL_RETRY_EXHAUSTED = "FINAL_RETRY_EXHAUSTED"
    HUMAN_REVIEW_REQUIRED = "HUMAN_REVIEW_REQUIRED"
    INTEGRITY_FAILURE = "INTEGRITY_FAILURE"
    HAT_ENFORCE_DRAFT_V1_FALLBACK_FORBIDDEN = (
        "HAT_ENFORCE_DRAFT_V1_FALLBACK_FORBIDDEN"
    )
    AMBIGUOUS_ROUTE = "AMBIGUOUS_ROUTE"
    PASS_THROUGH_FALLBACK_NOT_AUTHORIZED = "PASS_THROUGH_FALLBACK_NOT_AUTHORIZED"
    RETRY_SAME_PACKET = "RETRY_SAME_PACKET"
    RETRY_FULL_REVERIFICATION = "RETRY_FULL_REVERIFICATION"
    RETRY_NEW_EVIDENCE_FORBIDDEN = "RETRY_NEW_EVIDENCE_FORBIDDEN"


class Step26BoundaryError(RuntimeError):
    """Sanitized final-output denial."""

    def __init__(self, reason_code: Step26ReasonCode) -> None:
        if not isinstance(reason_code, Step26ReasonCode):
            raise TypeError("reason_code must be Step26ReasonCode")
        super().__init__(f"Step 26 final output denied: {reason_code.value}")
        self.reason_code = reason_code


_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_LOGICAL_ID = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,255}$")
_REASON_ORDER = {value: index for index, value in enumerate(Step26ReasonCode)}


def _text(value: object, field_name: str, maximum_bytes: int = 512) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or unicodedata.normalize("NFC", value) != value
        or _CONTROL.search(value)
        or len(value.encode("utf-8")) > maximum_bytes
    ):
        raise ContractValidationError(
            f"{field_name} must be bounded canonical NFC text"
        )
    return value


def _logical_id(value: object, field_name: str) -> str:
    result = _text(value, field_name, 256)
    if _LOGICAL_ID.fullmatch(result) is None:
        raise ContractValidationError(f"{field_name} must be a logical identifier")
    return result


def _content(value: object, field_name: str, maximum_bytes: int) -> str:
    if not isinstance(value, str) or not value:
        raise ContractValidationError(f"{field_name} must be non-empty text")
    if unicodedata.normalize("NFC", value) != value:
        raise ContractValidationError(f"{field_name} must use Unicode NFC")
    if _CONTROL.search(value) or len(value.encode("utf-8")) > maximum_bytes:
        raise ContractValidationError(f"{field_name} violates its content bound")
    return value


def _scope_tuple(value: object) -> tuple[ScopeDimension, ...]:
    if not isinstance(value, (tuple, list)) or any(
        not isinstance(item, ScopeDimension) for item in value
    ):
        raise ContractValidationError("effective_scope must be typed")
    result = tuple(sorted(value, key=lambda item: item.name))
    if len({item.name for item in result}) != len(result):
        raise ContractValidationError("effective_scope names must be unique")
    return result


def _string_tuple(
    value: object,
    field_name: str,
    maximum: int,
    *,
    ordered: bool = False,
    maximum_item_bytes: int = 1024,
) -> tuple[str, ...]:
    if not isinstance(value, (tuple, list)) or len(value) > maximum:
        raise ContractValidationError(f"{field_name} is outside Step 26 bounds")
    result = tuple(value)
    for item in result:
        _text(item, f"{field_name} item", maximum_item_bytes)
    if len(set(result)) != len(result):
        raise ContractValidationError(f"{field_name} must be unique")
    if not ordered and result != tuple(sorted(result)):
        raise ContractValidationError(f"{field_name} must be canonically sorted")
    return result


def _hash_tuple(
    value: object,
    field_name: str,
    maximum: int,
    *,
    ordered: bool = False,
) -> tuple[str, ...]:
    result = _string_tuple(value, field_name, maximum, ordered=ordered)
    for item in result:
        require_sha256_hex(item, f"{field_name} item")
    return result


def reason_codes(*values: Step26ReasonCode) -> tuple[Step26ReasonCode, ...]:
    return tuple(sorted(set(values), key=_REASON_ORDER.__getitem__))


def _reason_tuple(value: object) -> tuple[Step26ReasonCode, ...]:
    if not isinstance(value, (tuple, list)) or any(
        not isinstance(item, Step26ReasonCode) for item in value
    ):
        raise ContractValidationError("reason_codes must be typed")
    result = reason_codes(*value)
    if tuple(value) != result:
        raise ContractValidationError("reason_codes must be canonical")
    return result


@dataclass(frozen=True, slots=True)
class FinalAnswerPolicy:
    policy_id: str = FINAL_ANSWER_POLICY_ID
    policy_version: str = FINAL_ANSWER_POLICY_VERSION
    maximum_final_correction_retries: int = MAX_FINAL_CORRECTION_RETRIES
    require_step25_verified_summary: bool = True
    require_all_factual_claims_verified: bool = True
    require_packet_bound_citations: bool = True
    hat_enforce_draft_v1_fallback_allowed: bool = False
    retry_may_add_evidence: bool = False
    retry_requires_full_reverification: bool = True
    verified_answer_grants_execution: bool = False
    policy_digest: str = field(init=False)

    def __post_init__(self) -> None:
        if (
            self.policy_id != FINAL_ANSWER_POLICY_ID
            or self.policy_version != FINAL_ANSWER_POLICY_VERSION
            or self.maximum_final_correction_retries != 1
            or self.require_step25_verified_summary is not True
            or self.require_all_factual_claims_verified is not True
            or self.require_packet_bound_citations is not True
            or self.hat_enforce_draft_v1_fallback_allowed is not False
            or self.retry_may_add_evidence is not False
            or self.retry_requires_full_reverification is not True
            or self.verified_answer_grants_execution is not False
        ):
            raise ContractValidationError("Step 26 final-answer policy cannot be weakened")
        object.__setattr__(
            self,
            "policy_digest",
            canonical_sha256(self, exclude_fields=("policy_digest",)),
        )


def load_final_answer_policy() -> FinalAnswerPolicy:
    return FinalAnswerPolicy()


@dataclass(frozen=True, slots=True)
class FinalAnswerRequest:
    """Complete, verified lineage supplied to the Step 26 policy boundary."""

    route: KnowledgeRouteResult
    policy_result: PolicyGateResult
    step20_outcomes: tuple[HybridEvidenceOutcome, ...]
    temporal_result: TemporalResolutionResult | None
    draft_v1: DraftV1 | None
    correction_packet: CorrectionPacketV1A | None
    integrity_receipt: CorrectionPacketIntegrityReceipt | None
    step25_result: DraftV2PipelineResult | None
    final_policy: FinalAnswerPolicy = field(default_factory=load_final_answer_policy)
    request_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.route, KnowledgeRouteResult) or not isinstance(
            self.policy_result, PolicyGateResult
        ):
            raise Step26BoundaryError(Step26ReasonCode.INTEGRITY_FAILURE)
        try:
            verify_route_hash(self.route)
            verify_policy_result_hash(self.policy_result)
        except (ContractValidationError, IntegrityError) as exc:
            raise Step26BoundaryError(Step26ReasonCode.INTEGRITY_FAILURE) from exc
        if (
            self.policy_result.request_id != self.route.request_id
            or self.policy_result.tenant_id != self.route.tenant_id
            or self.policy_result.user_id != self.route.user_id
            or self.policy_result.route_hash != self.route.route_hash
        ):
            raise Step26BoundaryError(Step26ReasonCode.INTEGRITY_FAILURE)
        if not isinstance(self.final_policy, FinalAnswerPolicy) or (
            self.final_policy != load_final_answer_policy()
        ):
            raise ContractValidationError("final_policy must be the fixed Step 26 policy")

        outcomes = tuple(self.step20_outcomes)
        if len(outcomes) > 2 or any(
            not isinstance(item, HybridEvidenceOutcome) for item in outcomes
        ):
            raise Step26BoundaryError(Step26ReasonCode.INTEGRITY_FAILURE)
        try:
            for outcome in outcomes:
                verify_outcome_hash(outcome)
                if outcome.bundle is not None:
                    verify_evidence_bundle_hash(outcome.bundle)
        except (ContractValidationError, IntegrityError, RuntimeError) as exc:
            raise Step26BoundaryError(Step26ReasonCode.INTEGRITY_FAILURE) from exc
        object.__setattr__(self, "step20_outcomes", outcomes)

        hat_route = self.route.knowledge_route in {
            KnowledgeRoute.HAT_ASSIST,
            KnowledgeRoute.HAT_ENFORCE,
        }
        downstream = (
            self.temporal_result,
            self.draft_v1,
            self.correction_packet,
            self.integrity_receipt,
            self.step25_result,
        )
        if not hat_route:
            if outcomes or any(value is not None for value in downstream):
                raise Step26BoundaryError(Step26ReasonCode.INTEGRITY_FAILURE)
        else:
            if not 1 <= len(outcomes) <= 2 or any(value is None for value in downstream):
                raise Step26BoundaryError(Step26ReasonCode.INTEGRITY_FAILURE)
            self._verify_hat_lineage()

        object.__setattr__(
            self,
            "request_hash",
            canonical_sha256(
                {
                    "route_hash": self.route.route_hash,
                    "policy_result_hash": self.policy_result.policy_result_hash,
                    "step20_outcome_hashes": tuple(
                        item.outcome_hash for item in outcomes
                    ),
                    "temporal_result_hash": (
                        self.temporal_result.result_hash
                        if self.temporal_result is not None
                        else None
                    ),
                    "draft_v1_hash": (
                        self.draft_v1.draft_hash if self.draft_v1 is not None else None
                    ),
                    "correction_packet_hash": (
                        self.correction_packet.packet_hash
                        if self.correction_packet is not None
                        else None
                    ),
                    "integrity_receipt_hash": (
                        self.integrity_receipt.receipt_hash
                        if self.integrity_receipt is not None
                        else None
                    ),
                    "step25_result_hash": (
                        self.step25_result.result_hash
                        if self.step25_result is not None
                        else None
                    ),
                    "final_policy_digest": self.final_policy.policy_digest,
                }
            ),
        )

    def _verify_hat_lineage(self) -> None:
        temporal = self.temporal_result
        draft = self.draft_v1
        packet = self.correction_packet
        receipt = self.integrity_receipt
        pipeline = self.step25_result
        assert temporal is not None
        assert draft is not None
        assert packet is not None
        assert receipt is not None
        assert pipeline is not None
        try:
            verify_temporal_result_hash(temporal)
            verify_draft_v1_hash(draft)
            verify_correction_packet_hash(packet)
            verify_integrity_receipt_hash(receipt)
            verify_draft_v2_pipeline_result_hash(pipeline)
        except (ContractValidationError, IntegrityError, RuntimeError) as exc:
            raise Step26BoundaryError(Step26ReasonCode.INTEGRITY_FAILURE) from exc

        expected_outcome_hashes = (temporal.step20_outcome_hash,) + (
            (temporal.fallback_outcome_hash,)
            if temporal.fallback_outcome_hash is not None
            else ()
        )
        expected_bundle_hashes = (temporal.step20_bundle_hash,) + (
            (temporal.fallback_bundle_hash,)
            if temporal.fallback_bundle_hash is not None
            else ()
        )
        actual_outcome_hashes = tuple(item.outcome_hash for item in self.step20_outcomes)
        actual_bundle_hashes = tuple(
            item.bundle.bundle_hash if item.bundle is not None else None
            for item in self.step20_outcomes
        )
        if (
            actual_outcome_hashes != expected_outcome_hashes
            or actual_bundle_hashes != expected_bundle_hashes
            or packet.step20_evidence_bundle_hashes != expected_bundle_hashes
        ):
            raise Step26BoundaryError(Step26ReasonCode.INTEGRITY_FAILURE)

        common = (
            self.route.request_id,
            self.route.tenant_id,
            self.route.user_id,
            self.route.route_hash,
        )
        if any(
            (
                item.request_id,
                item.tenant_id,
                item.user_id,
                item.route_hash,
            )
            != common
            for item in self.step20_outcomes
        ):
            raise Step26BoundaryError(Step26ReasonCode.INTEGRITY_FAILURE)
        if (
            (temporal.request_id, temporal.tenant_id, temporal.user_id, temporal.route_hash)
            != common
            or (draft.request_id, draft.tenant_id, draft.user_id, draft.route_hash)
            != common
            or (packet.request_id, packet.tenant_id, packet.user_id, packet.route_hash)
            != common
            or (
                pipeline.draft_v2.request_id,
                pipeline.draft_v2.tenant_id,
                pipeline.draft_v2.user_id,
                pipeline.draft_v2.route_hash,
            )
            != common
        ):
            raise Step26BoundaryError(Step26ReasonCode.INTEGRITY_FAILURE)

        selected = (
            self.route.selected_hat_id,
            self.route.selected_hat_version,
            self.route.selected_manifest_digest,
            self.route.effective_scope,
        )
        if (
            (
                temporal.selected_hat_id,
                temporal.selected_hat_version,
                temporal.selected_manifest_digest,
                temporal.effective_scope,
            )
            != selected
            or (
                packet.selected_hat_id,
                packet.selected_hat_version,
                packet.selected_manifest_digest,
                packet.effective_scope,
            )
            != selected
            or any(
                item.bundle is None
                or (
                    item.bundle.selected_hat_id,
                    item.bundle.selected_hat_version,
                    item.bundle.selected_manifest_digest,
                    item.bundle.effective_scope,
                )
                != selected
                for item in self.step20_outcomes
            )
        ):
            raise Step26BoundaryError(Step26ReasonCode.INTEGRITY_FAILURE)

        if (
            self.policy_result.evidence_status is not temporal.evidence_status
            or packet.evidence_status is not temporal.evidence_status
            or draft.step21_evidence_status is not temporal.evidence_status
            or draft.step21_result_hash != temporal.result_hash
            or packet.step21_resolution_hash != temporal.result_hash
            or receipt.packet_hash != packet.packet_hash
            or pipeline.draft_v2.draft_v1_hash != draft.draft_hash
            or pipeline.draft_v2.correction_packet_hash != packet.packet_hash
            or pipeline.draft_v2.packet_integrity_receipt_hash != receipt.receipt_hash
            or pipeline.verification_summary.correction_packet_hash != packet.packet_hash
            or pipeline.verification_summary.draft_v2_hash
            != pipeline.draft_v2.draft_v2_hash
        ):
            raise Step26BoundaryError(Step26ReasonCode.INTEGRITY_FAILURE)


@dataclass(frozen=True, slots=True)
class ClaimVerificationReference:
    claim_id: str
    claim_hash: str
    verification_hash: str
    final_verdict: FinalStep25ClaimVerdict
    reference_hash: str = field(init=False)

    def __post_init__(self) -> None:
        _text(self.claim_id, "claim_id", 255)
        for value, name in (
            (self.claim_hash, "claim_hash"),
            (self.verification_hash, "verification_hash"),
        ):
            require_sha256_hex(value, name)
        require_enum_member(self.final_verdict, FinalStep25ClaimVerdict, "final_verdict")
        object.__setattr__(
            self,
            "reference_hash",
            canonical_sha256(self, exclude_fields=("reference_hash",)),
        )


@dataclass(frozen=True, slots=True)
class VerifiedAnswer:
    schema_version: str
    request_id: str
    tenant_id: str
    user_id: str
    route_hash: str
    selected_hat_id: str
    selected_hat_version: str
    selected_manifest_digest: str
    effective_scope: tuple[ScopeDimension, ...]
    evidence_bundle_hash: str
    fallback_evidence_bundle_hash: str | None
    temporal_resolution_hash: str
    correction_packet_hash: str
    packet_integrity_receipt_hash: str
    draft_v2_hash: str
    verification_summary_hash: str
    answer_text: str
    ordered_citations: tuple[CorrectionCitation, ...]
    claim_verification_references: tuple[ClaimVerificationReference, ...]
    limitations: tuple[str, ...]
    evidence_status: EvidenceStatus
    knowledge_policy_decision: KnowledgePolicyDecision
    execution_authorization_decision: ExecutionAuthorizationDecision
    answer_status: AnswerStatus
    output_status: FinalOutputStatus
    final_policy_digest: str
    persistence_decision: str = PERSISTENCE_DECISION
    answer_text_sha256: str = field(init=False)
    answer_byte_length: int = field(init=False)
    answer_id: str = field(init=False)
    answer_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != STEP26_SCHEMA_VERSION:
            raise ContractValidationError("unsupported Verified Answer schema")
        for value, name in (
            (self.request_id, "request_id"),
            (self.tenant_id, "tenant_id"),
            (self.user_id, "user_id"),
            (self.selected_hat_id, "selected_hat_id"),
            (self.selected_hat_version, "selected_hat_version"),
        ):
            _text(value, name, 255)
        for value, name in (
            (self.route_hash, "route_hash"),
            (self.selected_manifest_digest, "selected_manifest_digest"),
            (self.evidence_bundle_hash, "evidence_bundle_hash"),
            (self.temporal_resolution_hash, "temporal_resolution_hash"),
            (self.correction_packet_hash, "correction_packet_hash"),
            (self.packet_integrity_receipt_hash, "packet_integrity_receipt_hash"),
            (self.draft_v2_hash, "draft_v2_hash"),
            (self.verification_summary_hash, "verification_summary_hash"),
            (self.final_policy_digest, "final_policy_digest"),
        ):
            require_sha256_hex(value, name)
        if self.fallback_evidence_bundle_hash is not None:
            require_sha256_hex(
                self.fallback_evidence_bundle_hash,
                "fallback_evidence_bundle_hash",
            )
        object.__setattr__(self, "effective_scope", _scope_tuple(self.effective_scope))
        text = _content(self.answer_text, "answer_text", MAX_ANSWER_UTF8_BYTES)
        payload = text.encode("utf-8")
        object.__setattr__(self, "answer_text_sha256", hashlib.sha256(payload).hexdigest())
        object.__setattr__(self, "answer_byte_length", len(payload))

        citations = tuple(self.ordered_citations)
        if len(citations) > MAX_CITATIONS or any(
            not isinstance(item, CorrectionCitation) for item in citations
        ):
            raise ContractValidationError("ordered_citations are invalid")
        for citation in citations:
            verify_citation_hash(citation)
        if len({item.citation_id for item in citations}) != len(citations):
            raise ContractValidationError("citation identities must be unique")
        object.__setattr__(self, "ordered_citations", citations)

        references = tuple(self.claim_verification_references)
        if not references or len(references) > MAX_CLAIM_VERIFICATIONS or any(
            not isinstance(item, ClaimVerificationReference) for item in references
        ):
            raise ContractValidationError("claim verification references are invalid")
        if references != tuple(sorted(references, key=lambda item: item.claim_id)):
            raise ContractValidationError("claim verification references are not canonical")
        if len({item.claim_id for item in references}) != len(references):
            raise ContractValidationError("claim verification references must be unique")
        if any(
            item.final_verdict is not FinalStep25ClaimVerdict.VERIFIED_SUPPORTED
            for item in references
        ):
            raise ContractValidationError("a returned answer contains an unverified claim")
        object.__setattr__(self, "claim_verification_references", references)
        object.__setattr__(
            self,
            "limitations",
            _string_tuple(self.limitations, "limitations", MAX_LIMITATIONS),
        )
        if (
            self.evidence_status is not EvidenceStatus.SUFFICIENT
            or self.knowledge_policy_decision is not KnowledgePolicyDecision.ALLOW_ANSWER
            or self.answer_status is not AnswerStatus.VERIFIED
            or self.output_status is not FinalOutputStatus.VERIFIED_ANSWER
            or self.persistence_decision != PERSISTENCE_DECISION
        ):
            raise ContractValidationError("Verified Answer eligibility fields are invalid")
        require_enum_member(
            self.execution_authorization_decision,
            ExecutionAuthorizationDecision,
            "execution_authorization_decision",
        )
        answer_identity = canonical_sha256(
            {
                "request_id": self.request_id,
                "route_hash": self.route_hash,
                "draft_v2_hash": self.draft_v2_hash,
                "verification_summary_hash": self.verification_summary_hash,
            }
        )
        object.__setattr__(self, "answer_id", f"verified-answer:{answer_identity}")
        object.__setattr__(
            self,
            "answer_hash",
            canonical_sha256(self, exclude_fields=("answer_hash", "answer_text")),
        )


@dataclass(frozen=True, slots=True)
class RetryFailureSummary:
    original_draft_v2_hash: str
    original_verification_summary_hash: str
    unsatisfied_correction_ids: tuple[str, ...]
    violated_prohibited_claim_ids: tuple[str, ...]
    invalid_citation_claim_ids: tuple[str, ...]
    unverified_claim_ids: tuple[str, ...]
    refuted_claim_ids: tuple[str, ...]
    conflicting_claim_ids: tuple[str, ...]
    invalid_claim_ids: tuple[str, ...]
    reason_codes: tuple[Step26ReasonCode, ...]
    summary_hash: str = field(init=False)

    def __post_init__(self) -> None:
        require_sha256_hex(self.original_draft_v2_hash, "original_draft_v2_hash")
        require_sha256_hex(
            self.original_verification_summary_hash,
            "original_verification_summary_hash",
        )
        for name in (
            "unsatisfied_correction_ids",
            "violated_prohibited_claim_ids",
            "invalid_citation_claim_ids",
            "unverified_claim_ids",
            "refuted_claim_ids",
            "conflicting_claim_ids",
            "invalid_claim_ids",
        ):
            object.__setattr__(
                self,
                name,
                _string_tuple(getattr(self, name), name, MAX_CLAIM_VERIFICATIONS),
            )
        object.__setattr__(self, "reason_codes", _reason_tuple(self.reason_codes))
        if Step26ReasonCode.FINAL_RETRY_REQUIRED not in self.reason_codes:
            raise ContractValidationError("retry failure reason is missing")
        object.__setattr__(
            self,
            "summary_hash",
            canonical_sha256(self, exclude_fields=("summary_hash",)),
        )


@dataclass(frozen=True, slots=True)
class FinalCorrectionRetryRequest:
    draft_v1: DraftV1
    correction_packet: CorrectionPacketV1A
    integrity_receipt: CorrectionPacketIntegrityReceipt
    original_step25_result: DraftV2PipelineResult
    failure_summary: RetryFailureSummary
    provider_identity: ProviderIdentity
    prompt_template: PromptTemplate
    generation_parameters: GenerationParameters
    timeout_policy: TimeoutPolicy
    attempt_policy: AttemptPolicy
    retry_number: int = 1
    request_hash: str = field(init=False)

    def __post_init__(self) -> None:
        try:
            verify_draft_v1_hash(self.draft_v1)
            verify_correction_packet_hash(self.correction_packet)
            verify_integrity_receipt_hash(self.integrity_receipt)
            verify_draft_v2_pipeline_result_hash(self.original_step25_result)
        except (ContractValidationError, IntegrityError, RuntimeError) as exc:
            raise Step26BoundaryError(Step26ReasonCode.INTEGRITY_FAILURE) from exc
        if self.failure_summary.original_draft_v2_hash != (
            self.original_step25_result.draft_v2.draft_v2_hash
        ) or self.failure_summary.original_verification_summary_hash != (
            self.original_step25_result.verification_summary.summary_hash
        ):
            raise Step26BoundaryError(Step26ReasonCode.INTEGRITY_FAILURE)
        if (
            self.draft_v1.draft_hash
            != self.original_step25_result.draft_v2.draft_v1_hash
            or self.correction_packet.packet_hash
            != self.original_step25_result.draft_v2.correction_packet_hash
            or self.integrity_receipt.receipt_hash
            != self.original_step25_result.draft_v2.packet_integrity_receipt_hash
            or self.integrity_receipt.packet_hash != self.correction_packet.packet_hash
        ):
            raise Step26BoundaryError(Step26ReasonCode.INTEGRITY_FAILURE)
        if self.provider_identity != load_approved_provider_spec().provider_identity():
            raise Step26BoundaryError(Step26ReasonCode.INTEGRITY_FAILURE)
        if (
            self.prompt_template.template_id != FINAL_RETRY_PROMPT_ID
            or self.prompt_template.template_version != FINAL_RETRY_PROMPT_VERSION
            or self.generation_parameters.policy_id
            != FINAL_RETRY_GENERATION_POLICY_ID
            or self.timeout_policy.policy_id != FINAL_RETRY_TIMEOUT_POLICY_ID
            or self.attempt_policy.policy_id != FINAL_RETRY_ATTEMPT_POLICY_ID
            or self.attempt_policy.max_attempts != 1
            or self.retry_number != 1
        ):
            raise ContractValidationError("final retry policy is invalid")
        object.__setattr__(
            self,
            "request_hash",
            canonical_sha256(
                {
                    "draft_v1_hash": self.draft_v1.draft_hash,
                    "correction_packet_hash": self.correction_packet.packet_hash,
                    "integrity_receipt_hash": self.integrity_receipt.receipt_hash,
                    "original_step25_result_hash": self.original_step25_result.result_hash,
                    "failure_summary_hash": self.failure_summary.summary_hash,
                    "provider_identity_digest": self.provider_identity.identity_digest,
                    "prompt_template_digest": self.prompt_template.template_digest,
                    "generation_parameters_digest": (
                        self.generation_parameters.parameters_digest
                    ),
                    "timeout_policy_digest": self.timeout_policy.policy_digest,
                    "attempt_policy_digest": self.attempt_policy.policy_digest,
                    "retry_number": self.retry_number,
                }
            ),
        )


@dataclass(frozen=True, slots=True)
class FinalRetryRecord:
    retry_number: int
    retry_request_hash: str
    correction_packet_hash: str
    original_draft_v2_hash: str
    original_verification_summary_hash: str
    retry_draft_v2_hash: str
    retry_verification_summary_hash: str
    full_reverification_performed: bool
    new_evidence_used: bool
    result: FinalRetryResult
    reason_codes: tuple[Step26ReasonCode, ...]
    record_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.retry_number != 1:
            raise ContractValidationError("Step 26 permits exactly one retry")
        for value, name in (
            (self.retry_request_hash, "retry_request_hash"),
            (self.correction_packet_hash, "correction_packet_hash"),
            (self.original_draft_v2_hash, "original_draft_v2_hash"),
            (
                self.original_verification_summary_hash,
                "original_verification_summary_hash",
            ),
            (self.retry_draft_v2_hash, "retry_draft_v2_hash"),
            (
                self.retry_verification_summary_hash,
                "retry_verification_summary_hash",
            ),
        ):
            require_sha256_hex(value, name)
        if (
            self.original_draft_v2_hash == self.retry_draft_v2_hash
            or self.full_reverification_performed is not True
            or self.new_evidence_used is not False
        ):
            raise ContractValidationError("retry isolation/re-verification is invalid")
        require_enum_member(self.result, FinalRetryResult, "result")
        object.__setattr__(self, "reason_codes", _reason_tuple(self.reason_codes))
        required = (
            Step26ReasonCode.FINAL_RETRY_SUCCEEDED
            if self.result is FinalRetryResult.SUCCEEDED
            else Step26ReasonCode.FINAL_RETRY_FAILED
        )
        if required not in self.reason_codes:
            raise ContractValidationError("retry result reason is missing")
        object.__setattr__(
            self,
            "record_hash",
            canonical_sha256(self, exclude_fields=("record_hash",)),
        )


@dataclass(frozen=True, slots=True)
class HumanReviewRequired:
    schema_version: str
    request_id: str
    tenant_id: str
    user_id: str
    route_hash: str
    selected_hat_id: str | None
    selected_hat_version: str | None
    selected_manifest_digest: str | None
    draft_v1_hash: str | None
    draft_v2_hashes: tuple[str, ...]
    correction_packet_hash: str | None
    verification_summary_hash: str | None
    evidence_status: EvidenceStatus
    knowledge_policy_decision: KnowledgePolicyDecision
    output_status: FinalOutputStatus
    reason_codes: tuple[Step26ReasonCode, ...]
    review_payload_hashes: tuple[str, ...]
    result_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != STEP26_SCHEMA_VERSION:
            raise ContractValidationError("unsupported review-result schema")
        for value, name in (
            (self.request_id, "request_id"),
            (self.tenant_id, "tenant_id"),
            (self.user_id, "user_id"),
        ):
            _text(value, name, 255)
        require_sha256_hex(self.route_hash, "route_hash")
        selected = (
            self.selected_hat_id,
            self.selected_hat_version,
            self.selected_manifest_digest,
        )
        if any(value is None for value in selected) != all(
            value is None for value in selected
        ):
            raise ContractValidationError("review HAT identity must be complete")
        if self.selected_hat_id is not None:
            _logical_id(self.selected_hat_id, "selected_hat_id")
            _text(self.selected_hat_version, "selected_hat_version", 128)
            require_sha256_hex(self.selected_manifest_digest, "selected_manifest_digest")
        for value, name in (
            (self.draft_v1_hash, "draft_v1_hash"),
            (self.correction_packet_hash, "correction_packet_hash"),
            (self.verification_summary_hash, "verification_summary_hash"),
        ):
            if value is not None:
                require_sha256_hex(value, name)
        object.__setattr__(
            self,
            "draft_v2_hashes",
            _hash_tuple(self.draft_v2_hashes, "draft_v2_hashes", 2, ordered=True),
        )
        object.__setattr__(
            self,
            "review_payload_hashes",
            _hash_tuple(self.review_payload_hashes, "review_payload_hashes", 16),
        )
        require_enum_member(self.evidence_status, EvidenceStatus, "evidence_status")
        require_enum_member(
            self.knowledge_policy_decision,
            KnowledgePolicyDecision,
            "knowledge_policy_decision",
        )
        if self.output_status not in {
            FinalOutputStatus.HUMAN_REVIEW_REQUIRED,
            FinalOutputStatus.CONFIRMATION_REQUIRED,
        }:
            raise ContractValidationError("review output status is invalid")
        object.__setattr__(self, "reason_codes", _reason_tuple(self.reason_codes))
        if Step26ReasonCode.HUMAN_REVIEW_REQUIRED not in self.reason_codes:
            raise ContractValidationError("human-review reason is missing")
        object.__setattr__(
            self,
            "result_hash",
            canonical_sha256(self, exclude_fields=("result_hash",)),
        )


@dataclass(frozen=True, slots=True)
class BoundedAnswerFailure:
    schema_version: str
    request_id: str
    tenant_id: str
    user_id: str
    route_hash: str
    failure_class: FinalFailureClass
    evidence_status: EvidenceStatus
    knowledge_policy_decision: KnowledgePolicyDecision
    answer_status: AnswerStatus
    output_status: FinalOutputStatus
    verification_summary_hash: str | None
    reason_codes: tuple[Step26ReasonCode, ...]
    safe_message: str
    failure_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != STEP26_SCHEMA_VERSION:
            raise ContractValidationError("unsupported bounded-failure schema")
        for value, name in (
            (self.request_id, "request_id"),
            (self.tenant_id, "tenant_id"),
            (self.user_id, "user_id"),
        ):
            _text(value, name, 255)
        require_sha256_hex(self.route_hash, "route_hash")
        require_enum_member(self.failure_class, FinalFailureClass, "failure_class")
        require_enum_member(self.evidence_status, EvidenceStatus, "evidence_status")
        require_enum_member(
            self.knowledge_policy_decision,
            KnowledgePolicyDecision,
            "knowledge_policy_decision",
        )
        require_enum_member(self.answer_status, AnswerStatus, "answer_status")
        require_enum_member(self.output_status, FinalOutputStatus, "output_status")
        if self.output_status in {
            FinalOutputStatus.VERIFIED_ANSWER,
            FinalOutputStatus.RETRY_REQUIRED,
            FinalOutputStatus.HUMAN_REVIEW_REQUIRED,
            FinalOutputStatus.CONFIRMATION_REQUIRED,
        }:
            raise ContractValidationError("bounded failure output status is invalid")
        if self.verification_summary_hash is not None:
            require_sha256_hex(
                self.verification_summary_hash,
                "verification_summary_hash",
            )
        object.__setattr__(self, "reason_codes", _reason_tuple(self.reason_codes))
        _text(self.safe_message, "safe_message", MAX_FAILURE_MESSAGE_UTF8_BYTES)
        forbidden = (
            "api_key",
            "authorization:",
            "bearer ",
            "password",
            "postgresql://",
            "traceback",
            "/home/",
            "/media/",
        )
        if any(marker in self.safe_message.casefold() for marker in forbidden):
            raise ContractValidationError("bounded failure message leaks internal data")
        object.__setattr__(
            self,
            "failure_hash",
            canonical_sha256(self, exclude_fields=("failure_hash",)),
        )


@dataclass(frozen=True, slots=True)
class FinalAnswerOutcome:
    request_hash: str
    output_status: FinalOutputStatus
    verified_answer: VerifiedAnswer | None
    human_review: HumanReviewRequired | None
    bounded_failure: BoundedAnswerFailure | None
    retry_record: FinalRetryRecord | None
    outcome_hash: str = field(init=False)

    def __post_init__(self) -> None:
        require_sha256_hex(self.request_hash, "request_hash")
        require_enum_member(self.output_status, FinalOutputStatus, "output_status")
        choices = (
            self.verified_answer,
            self.human_review,
            self.bounded_failure,
        )
        if sum(value is not None for value in choices) != 1:
            raise ContractValidationError("final outcome requires exactly one payload")
        if self.verified_answer is not None:
            verify_verified_answer_hash(self.verified_answer)
            expected_status = FinalOutputStatus.VERIFIED_ANSWER
        elif self.human_review is not None:
            verify_human_review_hash(self.human_review)
            expected_status = self.human_review.output_status
        else:
            assert self.bounded_failure is not None
            verify_bounded_failure_hash(self.bounded_failure)
            expected_status = self.bounded_failure.output_status
        if self.output_status is not expected_status:
            raise ContractValidationError("final outcome status differs from its payload")
        if self.retry_record is not None:
            verify_retry_record_hash(self.retry_record)
        object.__setattr__(
            self,
            "outcome_hash",
            canonical_sha256(self, exclude_fields=("outcome_hash",)),
        )


def verify_final_answer_request_hash(value: FinalAnswerRequest) -> None:
    reconstructed = FinalAnswerRequest(
        route=value.route,
        policy_result=value.policy_result,
        step20_outcomes=value.step20_outcomes,
        temporal_result=value.temporal_result,
        draft_v1=value.draft_v1,
        correction_packet=value.correction_packet,
        integrity_receipt=value.integrity_receipt,
        step25_result=value.step25_result,
        final_policy=value.final_policy,
    )
    if reconstructed.request_hash != value.request_hash:
        raise IntegrityError("final answer request hash mismatch")


def verify_claim_verification_reference_hash(value: ClaimVerificationReference) -> None:
    verify_canonical_hash(value, value.reference_hash, exclude_fields=("reference_hash",))


def verify_verified_answer_hash(value: VerifiedAnswer) -> None:
    verify_canonical_hash(
        value,
        value.answer_hash,
        exclude_fields=("answer_hash", "answer_text"),
    )
    payload = value.answer_text.encode("utf-8")
    if (
        value.answer_text_sha256 != hashlib.sha256(payload).hexdigest()
        or value.answer_byte_length != len(payload)
    ):
        raise IntegrityError("Verified Answer text identity mismatch")
    for citation in value.ordered_citations:
        verify_citation_hash(citation)
    for reference in value.claim_verification_references:
        verify_claim_verification_reference_hash(reference)


def verify_retry_failure_summary_hash(value: RetryFailureSummary) -> None:
    verify_canonical_hash(value, value.summary_hash, exclude_fields=("summary_hash",))


def verify_final_retry_request_hash(value: FinalCorrectionRetryRequest) -> None:
    reconstructed = FinalCorrectionRetryRequest(
        draft_v1=value.draft_v1,
        correction_packet=value.correction_packet,
        integrity_receipt=value.integrity_receipt,
        original_step25_result=value.original_step25_result,
        failure_summary=value.failure_summary,
        provider_identity=value.provider_identity,
        prompt_template=value.prompt_template,
        generation_parameters=value.generation_parameters,
        timeout_policy=value.timeout_policy,
        attempt_policy=value.attempt_policy,
        retry_number=value.retry_number,
    )
    if reconstructed.request_hash != value.request_hash:
        raise IntegrityError("final retry request hash mismatch")


def verify_retry_record_hash(value: FinalRetryRecord) -> None:
    verify_canonical_hash(value, value.record_hash, exclude_fields=("record_hash",))


def verify_human_review_hash(value: HumanReviewRequired) -> None:
    verify_canonical_hash(value, value.result_hash, exclude_fields=("result_hash",))


def verify_bounded_failure_hash(value: BoundedAnswerFailure) -> None:
    verify_canonical_hash(value, value.failure_hash, exclude_fields=("failure_hash",))


def verify_final_answer_outcome_hash(value: FinalAnswerOutcome) -> None:
    verify_canonical_hash(value, value.outcome_hash, exclude_fields=("outcome_hash",))


__all__ = [
    "BoundedAnswerFailure",
    "ClaimVerificationReference",
    "FINAL_ANSWER_POLICY_ID",
    "FINAL_ANSWER_POLICY_VERSION",
    "FINAL_RETRY_ATTEMPT_POLICY_ID",
    "FINAL_RETRY_GENERATION_POLICY_ID",
    "FINAL_RETRY_PROMPT_ID",
    "FINAL_RETRY_PROMPT_VERSION",
    "FINAL_RETRY_TIMEOUT_POLICY_ID",
    "FinalAnswerOutcome",
    "FinalAnswerPolicy",
    "FinalAnswerRequest",
    "FinalCorrectionRetryRequest",
    "FinalFailureClass",
    "FinalOutputStatus",
    "FinalRetryRecord",
    "FinalRetryResult",
    "HumanReviewRequired",
    "MAX_FINAL_CORRECTION_RETRIES",
    "PERSISTENCE_DECISION",
    "RetryFailureSummary",
    "STEP26_SCHEMA_VERSION",
    "Step26BoundaryError",
    "Step26ReasonCode",
    "VerifiedAnswer",
    "load_final_answer_policy",
    "reason_codes",
    "verify_bounded_failure_hash",
    "verify_claim_verification_reference_hash",
    "verify_final_answer_outcome_hash",
    "verify_final_answer_request_hash",
    "verify_final_retry_request_hash",
    "verify_human_review_hash",
    "verify_retry_failure_summary_hash",
    "verify_retry_record_hash",
    "verify_verified_answer_hash",
]
