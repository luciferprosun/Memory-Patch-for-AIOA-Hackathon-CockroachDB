"""Immutable Step 25 Draft V2 and layered-verification contracts.

Draft V2 is model output, not verified output.  Every final Step 25 claim
verdict is produced by a fixed aggregation policy in which deterministic
integrity, scope, fact, temporal, source, and citation failures outrank any
semantic-verifier signal.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from aioa_memory_kernel.claims import ClaimAtomicity, ClaimType
from aioa_memory_kernel.contracts.enums import StableStringEnum
from aioa_memory_kernel.contracts.exceptions import ContractValidationError, IntegrityError
from aioa_memory_kernel.contracts.scope import ScopeDimension
from aioa_memory_kernel.contracts.serialization import (
    canonical_json_bytes,
    canonical_sha256,
    ensure_utc,
    freeze_json,
    require_enum_member,
    require_sha256_hex,
    verify_canonical_hash,
)
from aioa_memory_kernel.corrections import (
    CorrectionPacketIntegrityReceipt,
    CorrectionPacketV1A,
    verify_correction_packet_hash,
    verify_integrity_receipt_hash,
)
from aioa_memory_kernel.modeling import (
    AttemptPolicy,
    DraftV1,
    GenerationParameters,
    ModelReasonCode,
    PromptTemplate,
    ProviderIdentity,
    TimeoutPolicy,
    load_approved_provider_spec,
    verify_draft_v1_hash,
)


STEP25_SCHEMA_VERSION = "1.0.0"
DRAFT_V2_PROMPT_TEMPLATE_ID = "draft-v2-correction-packet-only-1a"
DRAFT_V2_PROMPT_TEMPLATE_VERSION = "1"
DRAFT_V2_GENERATION_POLICY_ID = "draft-v2-generation-parameters-1a"
DRAFT_V2_TIMEOUT_POLICY_ID = "draft-v2-timeout-1a"
DRAFT_V2_ATTEMPT_POLICY_ID = "draft-v2-retry-1a"
SEMANTIC_VERIFIER_PROMPT_ID = "draft-v2-semantic-claim-verifier-1a"
SEMANTIC_VERIFIER_PROMPT_VERSION = "1"
SEMANTIC_VERIFIER_POLICY_ID = "draft-v2-semantic-verifier-1a"
DRAFT_V2_CLAIM_SPAN_CONVENTION = (
    "draft-v2-unicode-codepoints-start-inclusive-end-exclusive-v1"
)
MAX_DRAFT_V2_CLAIMS = 256
MAX_DRAFT_V2_UTF8_BYTES = 64 * 1024
MAX_DRAFT_V2_CLAIM_UTF8_BYTES = 16 * 1024
MAX_SEMANTIC_EVIDENCE_ITEMS = 16
MAX_SEMANTIC_CONTEXT_UTF8_BYTES = 16 * 1024
MAX_REASON_CODES = 64
MAX_LIMITATIONS = 64
MAX_PERSISTED_DRAFT_V2_ENVELOPE_BYTES = 128 * 1024
DRAFT_V2_REFERENCE_PREFIX = "data:application/vnd.aioa.draft-v2+json;base64,"
PERSISTENCE_DECISION = (
    "DRAFT_V2_REUSES_STEP4_DRAFTS_STAGE_2_VERIFICATIONS_DEFERRED"
)


class Step25ReasonCode(StableStringEnum):
    DRAFT_V2_GENERATED = "DRAFT_V2_GENERATED"
    DRAFT_V2_INVALID = "DRAFT_V2_INVALID"
    DRAFT_V2_REPLAYED = "DRAFT_V2_REPLAYED"
    DRAFT_V2_PERSISTED = "DRAFT_V2_PERSISTED"
    PACKET_HASH_INVALID = "PACKET_HASH_INVALID"
    PACKET_HMAC_INVALID = "PACKET_HMAC_INVALID"
    PACKET_LINEAGE_INVALID = "PACKET_LINEAGE_INVALID"
    REQUIRED_CORRECTION_SATISFIED = "REQUIRED_CORRECTION_SATISFIED"
    REQUIRED_CORRECTION_MISSING = "REQUIRED_CORRECTION_MISSING"
    REQUIRED_CORRECTION_PARTIAL = "REQUIRED_CORRECTION_PARTIAL"
    PROHIBITED_CLAIM_ABSENT = "PROHIBITED_CLAIM_ABSENT"
    PROHIBITED_CLAIM_PRESENT = "PROHIBITED_CLAIM_PRESENT"
    PROHIBITED_CLAIM_SEMANTIC_MATCH = "PROHIBITED_CLAIM_SEMANTIC_MATCH"
    SCHEMA_CHECK_PASS = "SCHEMA_CHECK_PASS"
    SCHEMA_CHECK_FAIL = "SCHEMA_CHECK_FAIL"
    CITATION_VALID = "CITATION_VALID"
    CITATION_INVALID = "CITATION_INVALID"
    CITATION_NOT_ALLOWED = "CITATION_NOT_ALLOWED"
    FACT_CHECK_PASS = "FACT_CHECK_PASS"
    FACT_CHECK_FAIL = "FACT_CHECK_FAIL"
    TEMPORAL_CHECK_PASS = "TEMPORAL_CHECK_PASS"
    TEMPORAL_CHECK_FAIL = "TEMPORAL_CHECK_FAIL"
    SOURCE_CHECK_PASS = "SOURCE_CHECK_PASS"
    SOURCE_CHECK_FAIL = "SOURCE_CHECK_FAIL"
    EVIDENCE_SUPPORTS = "EVIDENCE_SUPPORTS"
    EVIDENCE_REFUTES = "EVIDENCE_REFUTES"
    EVIDENCE_UNVERIFIED = "EVIDENCE_UNVERIFIED"
    EVIDENCE_CONFLICTING = "EVIDENCE_CONFLICTING"
    SEMANTIC_VERIFIER_SUPPORTS = "SEMANTIC_VERIFIER_SUPPORTS"
    SEMANTIC_VERIFIER_REFUTES = "SEMANTIC_VERIFIER_REFUTES"
    SEMANTIC_VERIFIER_UNCERTAIN = "SEMANTIC_VERIFIER_UNCERTAIN"
    SEMANTIC_VERIFIER_UNAVAILABLE = "SEMANTIC_VERIFIER_UNAVAILABLE"
    SEMANTIC_VERIFIER_INVALID = "SEMANTIC_VERIFIER_INVALID"
    CLAIM_VERIFIED_SUPPORTED = "CLAIM_VERIFIED_SUPPORTED"
    CLAIM_VERIFIED_REFUTED = "CLAIM_VERIFIED_REFUTED"
    CLAIM_UNVERIFIED = "CLAIM_UNVERIFIED"
    CLAIM_CONFLICTING = "CLAIM_CONFLICTING"
    CLAIM_INVALID = "CLAIM_INVALID"
    NON_FACTUAL_NO_EVIDENCE_REQUIRED = "NON_FACTUAL_NO_EVIDENCE_REQUIRED"
    SUMMARY_VERIFIED = "SUMMARY_VERIFIED"
    SUMMARY_FAILED = "SUMMARY_FAILED"
    SUMMARY_INCOMPLETE = "SUMMARY_INCOMPLETE"
    SUMMARY_CONFLICTING = "SUMMARY_CONFLICTING"
    DRAFT_V2_PERSISTENCE_CONFLICT = "DRAFT_V2_PERSISTENCE_CONFLICT"


class CheckResult(StableStringEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    UNDECIDED = "UNDECIDED"


class CorrectionComplianceStatus(StableStringEnum):
    SATISFIED = "SATISFIED"
    NOT_SATISFIED = "NOT_SATISFIED"
    PARTIALLY_SATISFIED = "PARTIALLY_SATISFIED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class ProhibitedClaimPresence(StableStringEnum):
    NOT_PRESENT = "NOT_PRESENT"
    PRESENT_EXACT = "PRESENT_EXACT"
    PRESENT_SEMANTICALLY_EQUIVALENT = "PRESENT_SEMANTICALLY_EQUIVALENT"
    UNCERTAIN = "UNCERTAIN"


class EvidenceBindingResult(StableStringEnum):
    SUPPORTED = "SUPPORTED"
    REFUTED = "REFUTED"
    UNVERIFIED = "UNVERIFIED"
    CONFLICTING = "CONFLICTING"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class SemanticCandidateVerdict(StableStringEnum):
    SUPPORTS = "SUPPORTS"
    REFUTES = "REFUTES"
    UNCERTAIN = "UNCERTAIN"
    UNAVAILABLE = "UNAVAILABLE"
    INVALID = "INVALID"
    NOT_REQUIRED = "NOT_REQUIRED"


class FinalStep25ClaimVerdict(StableStringEnum):
    VERIFIED_SUPPORTED = "VERIFIED_SUPPORTED"
    VERIFIED_REFUTED = "VERIFIED_REFUTED"
    UNVERIFIED = "UNVERIFIED"
    CONFLICTING = "CONFLICTING"
    INVALID = "INVALID"


class VerificationSummaryStatus(StableStringEnum):
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"
    INCOMPLETE = "INCOMPLETE"
    CONFLICTING = "CONFLICTING"


class Step25BoundaryError(RuntimeError):
    """Sanitized fail-closed Step 25 boundary error."""

    def __init__(self, reason_code: Step25ReasonCode) -> None:
        if not isinstance(reason_code, Step25ReasonCode):
            raise TypeError("reason_code must be Step25ReasonCode")
        super().__init__(f"Step 25 Draft V2 operation denied: {reason_code.value}")
        self.reason_code = reason_code


_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_LOGICAL_ID = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,255}$")
_DRAFT_V2_CLAIM_ID = re.compile(r"^draft-v2-claim-[0-9a-f]{64}$")
_REASON_ORDER = {value: index for index, value in enumerate(Step25ReasonCode)}


def _text(value: object, field_name: str, maximum_bytes: int = 1024) -> str:
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


def _optional_text(
    value: object | None,
    field_name: str,
    maximum_bytes: int = 1024,
) -> str | None:
    return None if value is None else _text(value, field_name, maximum_bytes)


def _content(value: object, field_name: str, maximum_bytes: int) -> str:
    if not isinstance(value, str) or not value:
        raise ContractValidationError(f"{field_name} must be non-empty text")
    if unicodedata.normalize("NFC", value) != value:
        raise ContractValidationError(f"{field_name} must use Unicode NFC")
    if len(value.encode("utf-8")) > maximum_bytes:
        raise ContractValidationError(f"{field_name} exceeds its UTF-8 byte limit")
    if _CONTROL.search(value):
        raise ContractValidationError(f"{field_name} contains a prohibited control")
    return value


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _scope_tuple(value: object) -> tuple[ScopeDimension, ...]:
    if not isinstance(value, (tuple, list)) or any(
        not isinstance(item, ScopeDimension) for item in value
    ):
        raise ContractValidationError("effective_scope must be typed")
    result = tuple(value)
    if len({item.name for item in result}) != len(result):
        raise ContractValidationError("effective_scope names must be unique")
    return result


def _hash_tuple(
    value: object,
    field_name: str,
    maximum: int,
    *,
    ordered: bool = False,
) -> tuple[str, ...]:
    if not isinstance(value, (tuple, list)) or len(value) > maximum:
        raise ContractValidationError(f"{field_name} is outside Step 25 bounds")
    result = tuple(value)
    for item in result:
        require_sha256_hex(item, field_name)
    if len(set(result)) != len(result):
        raise ContractValidationError(f"{field_name} must be unique")
    if not ordered and result != tuple(sorted(result)):
        raise ContractValidationError(f"{field_name} must be canonically sorted")
    return result


def _string_tuple(
    value: object,
    field_name: str,
    maximum: int,
    *,
    maximum_item_bytes: int = 1024,
    canonical: bool = True,
) -> tuple[str, ...]:
    if not isinstance(value, (tuple, list)) or len(value) > maximum:
        raise ContractValidationError(f"{field_name} is outside Step 25 bounds")
    result = tuple(value)
    for item in result:
        _text(item, f"{field_name} item", maximum_item_bytes)
    if len(set(result)) != len(result):
        raise ContractValidationError(f"{field_name} must be unique")
    if canonical and result != tuple(sorted(result)):
        raise ContractValidationError(f"{field_name} must be canonically sorted")
    return result


def reason_codes(*values: Step25ReasonCode) -> tuple[Step25ReasonCode, ...]:
    return tuple(sorted(set(values), key=_REASON_ORDER.__getitem__))


def _reason_tuple(value: object) -> tuple[Step25ReasonCode, ...]:
    if not isinstance(value, (tuple, list)) or any(
        not isinstance(item, Step25ReasonCode) for item in value
    ):
        raise ContractValidationError("reason_codes must be typed")
    result = reason_codes(*value)
    if tuple(value) != result or len(result) > MAX_REASON_CODES:
        raise ContractValidationError("reason_codes must be canonical and bounded")
    return result


def _usage_mapping(value: object) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ContractValidationError("usage metadata must be string-keyed")
    allowed = {"prompt_tokens", "completion_tokens", "total_tokens"}
    result: dict[str, int | None] = {}
    for key, item in value.items():
        if key not in allowed:
            continue
        if item is not None and (
            isinstance(item, bool) or not isinstance(item, int) or item < 0
        ):
            raise ContractValidationError("usage metadata value is invalid")
        result[key] = item
    return freeze_json(dict(sorted(result.items())))


def derive_draft_v2_id(generation_request_hash: str) -> str:
    require_sha256_hex(generation_request_hash, "generation_request_hash")
    return "draft-v2-" + canonical_sha256(
        {"generation_request_hash": generation_request_hash, "draft_stage": 2}
    )


def derive_draft_v2_claim_id(
    draft_v2_hash: str,
    start_offset: int,
    end_offset: int,
    exact_claim_text: str,
) -> str:
    require_sha256_hex(draft_v2_hash, "draft_v2_hash")
    return "draft-v2-claim-" + canonical_sha256(
        {
            "draft_v2_hash": draft_v2_hash,
            "start_offset": start_offset,
            "end_offset": end_offset,
            "exact_claim_text": exact_claim_text,
        }
    )


@dataclass(frozen=True, slots=True)
class DraftV2GenerationRequest:
    draft_v1: DraftV1
    correction_packet: CorrectionPacketV1A
    integrity_receipt: CorrectionPacketIntegrityReceipt
    request_id: str
    tenant_id: str
    user_id: str
    route_hash: str
    original_query_digest: str
    draft_v1_hash: str
    correction_packet_hash: str
    packet_integrity_receipt_hash: str
    provider_identity: ProviderIdentity
    prompt_template: PromptTemplate
    generation_parameters: GenerationParameters
    timeout_policy: TimeoutPolicy
    attempt_policy: AttemptPolicy
    request_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.draft_v1, DraftV1):
            raise Step25BoundaryError(Step25ReasonCode.DRAFT_V2_INVALID)
        if not isinstance(self.correction_packet, CorrectionPacketV1A):
            raise Step25BoundaryError(Step25ReasonCode.PACKET_HASH_INVALID)
        if not isinstance(self.integrity_receipt, CorrectionPacketIntegrityReceipt):
            raise Step25BoundaryError(Step25ReasonCode.PACKET_HMAC_INVALID)
        try:
            verify_draft_v1_hash(self.draft_v1)
            verify_correction_packet_hash(self.correction_packet)
            verify_integrity_receipt_hash(self.integrity_receipt)
        except (ContractValidationError, IntegrityError, RuntimeError) as exc:
            raise Step25BoundaryError(Step25ReasonCode.PACKET_HASH_INVALID) from exc
        for value, name in (
            (self.request_id, "request_id"),
            (self.tenant_id, "tenant_id"),
            (self.user_id, "user_id"),
        ):
            _text(value, name, 255)
        for value, name in (
            (self.route_hash, "route_hash"),
            (self.original_query_digest, "original_query_digest"),
            (self.draft_v1_hash, "draft_v1_hash"),
            (self.correction_packet_hash, "correction_packet_hash"),
            (self.packet_integrity_receipt_hash, "packet_integrity_receipt_hash"),
        ):
            require_sha256_hex(value, name)
        packet = self.correction_packet
        draft = self.draft_v1
        expected = (
            draft.request_id,
            draft.tenant_id,
            draft.user_id,
            draft.route_hash,
            draft.original_query_digest,
            draft.draft_hash,
            packet.packet_hash,
            self.integrity_receipt.receipt_hash,
        )
        actual = (
            self.request_id,
            self.tenant_id,
            self.user_id,
            self.route_hash,
            self.original_query_digest,
            self.draft_v1_hash,
            self.correction_packet_hash,
            self.packet_integrity_receipt_hash,
        )
        packet_lineage = (
            packet.request_id,
            packet.tenant_id,
            packet.user_id,
            packet.route_hash,
            packet.original_query_digest,
            packet.draft_id,
            packet.draft_v1_hash,
            packet.draft_text_sha256,
            packet.step21_resolution_hash,
            packet.evidence_status,
        )
        draft_lineage = (
            draft.request_id,
            draft.tenant_id,
            draft.user_id,
            draft.route_hash,
            draft.original_query_digest,
            draft.draft_id,
            draft.draft_hash,
            draft.draft_text_sha256,
            draft.step21_result_hash,
            draft.step21_evidence_status,
        )
        if (
            actual != expected
            or packet_lineage != draft_lineage
            or self.integrity_receipt.packet_hash != packet.packet_hash
        ):
            raise Step25BoundaryError(Step25ReasonCode.PACKET_LINEAGE_INVALID)
        if not isinstance(self.provider_identity, ProviderIdentity):
            raise ContractValidationError("provider_identity must be typed")
        if self.provider_identity != load_approved_provider_spec().provider_identity():
            raise Step25BoundaryError(Step25ReasonCode.DRAFT_V2_INVALID)
        for value, expected_type, name in (
            (self.prompt_template, PromptTemplate, "prompt_template"),
            (self.generation_parameters, GenerationParameters, "generation_parameters"),
            (self.timeout_policy, TimeoutPolicy, "timeout_policy"),
            (self.attempt_policy, AttemptPolicy, "attempt_policy"),
        ):
            if not isinstance(value, expected_type):
                raise ContractValidationError(f"{name} must be typed")
        if (
            self.prompt_template.template_id != DRAFT_V2_PROMPT_TEMPLATE_ID
            or self.prompt_template.template_version != DRAFT_V2_PROMPT_TEMPLATE_VERSION
            or self.generation_parameters.policy_id != DRAFT_V2_GENERATION_POLICY_ID
            or self.timeout_policy.policy_id != DRAFT_V2_TIMEOUT_POLICY_ID
            or self.attempt_policy.policy_id != DRAFT_V2_ATTEMPT_POLICY_ID
        ):
            raise Step25BoundaryError(Step25ReasonCode.DRAFT_V2_INVALID)
        object.__setattr__(
            self,
            "request_hash",
            canonical_sha256(
                {
                    "request_id": self.request_id,
                    "tenant_id": self.tenant_id,
                    "user_id": self.user_id,
                    "route_hash": self.route_hash,
                    "original_query_digest": self.original_query_digest,
                    "draft_v1_hash": self.draft_v1_hash,
                    "correction_packet_hash": self.correction_packet_hash,
                    "packet_integrity_receipt_hash": self.packet_integrity_receipt_hash,
                    "provider_identity_digest": self.provider_identity.identity_digest,
                    "prompt_template_digest": self.prompt_template.template_digest,
                    "generation_parameters_digest": self.generation_parameters.parameters_digest,
                    "timeout_policy_digest": self.timeout_policy.policy_digest,
                    "attempt_policy_digest": self.attempt_policy.policy_digest,
                }
            ),
        )


@dataclass(frozen=True, slots=True)
class DraftV2GenerationResult:
    generation_request_hash: str
    provider_identity_digest: str
    attempt_count: int
    failed_attempt_reason_codes: tuple[ModelReasonCode, ...]
    provider_request_id: str | None
    model_id: str
    model_version: str
    finish_reason: str
    usage_metadata: Mapping[str, Any]
    response_text: str
    response_text_sha256: str
    response_byte_length: int
    latency_milliseconds: int
    provider_response_hash: str
    result_hash: str = field(init=False)

    def __post_init__(self) -> None:
        for value, name in (
            (self.generation_request_hash, "generation_request_hash"),
            (self.provider_identity_digest, "provider_identity_digest"),
            (self.response_text_sha256, "response_text_sha256"),
            (self.provider_response_hash, "provider_response_hash"),
        ):
            require_sha256_hex(value, name)
        if (
            isinstance(self.attempt_count, bool)
            or not isinstance(self.attempt_count, int)
            or not 1 <= self.attempt_count <= 2
        ):
            raise ContractValidationError("attempt_count is outside policy")
        failures = tuple(self.failed_attempt_reason_codes)
        if (
            len(failures) != self.attempt_count - 1
            or any(not isinstance(item, ModelReasonCode) for item in failures)
        ):
            raise ContractValidationError("failed-attempt metadata is invalid")
        object.__setattr__(self, "failed_attempt_reason_codes", failures)
        object.__setattr__(
            self,
            "provider_request_id",
            _optional_text(self.provider_request_id, "provider_request_id", 512),
        )
        _text(self.model_id, "model_id", 128)
        _text(self.model_version, "model_version", 128)
        _text(self.finish_reason, "finish_reason", 128)
        text = _content(self.response_text, "response_text", MAX_DRAFT_V2_UTF8_BYTES)
        payload = text.encode("utf-8")
        if (
            self.response_text_sha256 != hashlib.sha256(payload).hexdigest()
            or self.response_byte_length != len(payload)
        ):
            raise IntegrityError("Draft V2 generation content identity mismatch")
        object.__setattr__(self, "usage_metadata", _usage_mapping(self.usage_metadata))
        if (
            isinstance(self.latency_milliseconds, bool)
            or not isinstance(self.latency_milliseconds, int)
            or self.latency_milliseconds < 0
        ):
            raise ContractValidationError("latency_milliseconds is invalid")
        object.__setattr__(
            self,
            "result_hash",
            canonical_sha256(
                self,
                exclude_fields=("result_hash", "response_text", "latency_milliseconds"),
            ),
        )


@dataclass(frozen=True, slots=True)
class DraftV2:
    schema_version: str
    draft_v2_id: str
    generation_request_hash: str
    request_id: str
    tenant_id: str
    user_id: str
    route_hash: str
    original_query_digest: str
    draft_v1_hash: str
    correction_packet_hash: str
    packet_integrity_receipt_hash: str
    generation_result_hash: str
    provider_identity_digest: str
    prompt_template_digest: str
    generation_parameters_digest: str
    timeout_policy_digest: str
    attempt_policy_digest: str
    provider_request_id: str | None
    model_id: str
    model_version: str
    finish_reason: str
    attempt_count: int
    usage_metadata: Mapping[str, Any]
    draft_text: str
    created_at: datetime
    draft_text_sha256: str = field(init=False)
    draft_byte_length: int = field(init=False)
    draft_v2_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != STEP25_SCHEMA_VERSION:
            raise ContractValidationError("unsupported Draft V2 schema")
        _text(self.draft_v2_id, "draft_v2_id", 255)
        if self.draft_v2_id != derive_draft_v2_id(self.generation_request_hash):
            raise IntegrityError("Draft V2 identity is detached from its request")
        for value, name in (
            (self.generation_request_hash, "generation_request_hash"),
            (self.route_hash, "route_hash"),
            (self.original_query_digest, "original_query_digest"),
            (self.draft_v1_hash, "draft_v1_hash"),
            (self.correction_packet_hash, "correction_packet_hash"),
            (self.packet_integrity_receipt_hash, "packet_integrity_receipt_hash"),
            (self.generation_result_hash, "generation_result_hash"),
            (self.provider_identity_digest, "provider_identity_digest"),
            (self.prompt_template_digest, "prompt_template_digest"),
            (self.generation_parameters_digest, "generation_parameters_digest"),
            (self.timeout_policy_digest, "timeout_policy_digest"),
            (self.attempt_policy_digest, "attempt_policy_digest"),
        ):
            require_sha256_hex(value, name)
        for value, name in (
            (self.request_id, "request_id"),
            (self.tenant_id, "tenant_id"),
            (self.user_id, "user_id"),
            (self.model_id, "model_id"),
            (self.model_version, "model_version"),
            (self.finish_reason, "finish_reason"),
        ):
            _text(value, name, 255)
        object.__setattr__(
            self,
            "provider_request_id",
            _optional_text(self.provider_request_id, "provider_request_id", 512),
        )
        if (
            isinstance(self.attempt_count, bool)
            or not isinstance(self.attempt_count, int)
            or not 1 <= self.attempt_count <= 2
        ):
            raise ContractValidationError("attempt_count is outside policy")
        object.__setattr__(self, "usage_metadata", _usage_mapping(self.usage_metadata))
        text = _content(self.draft_text, "draft_text", MAX_DRAFT_V2_UTF8_BYTES)
        payload = text.encode("utf-8")
        object.__setattr__(self, "draft_text_sha256", hashlib.sha256(payload).hexdigest())
        object.__setattr__(self, "draft_byte_length", len(payload))
        object.__setattr__(self, "created_at", ensure_utc(self.created_at, "created_at"))
        object.__setattr__(
            self,
            "draft_v2_hash",
            canonical_sha256(
                self,
                exclude_fields=("draft_v2_hash", "draft_text", "created_at"),
            ),
        )


@dataclass(frozen=True, slots=True)
class DraftV2ClaimRecord:
    draft_v2_id: str
    draft_v2_hash: str
    start_offset: int
    end_offset: int
    exact_claim_text: str
    normalized_match_text: str
    claim_type: ClaimType
    atomicity: ClaimAtomicity
    scope_dimensions: tuple[ScopeDimension, ...]
    cited_citation_ids: tuple[str, ...]
    aligned_draft_v1_claim_ids: tuple[str, ...]
    reason_codes: tuple[Step25ReasonCode, ...]
    exact_claim_text_sha256: str = field(init=False)
    claim_id: str = field(init=False)
    claim_hash: str = field(init=False)

    def __post_init__(self) -> None:
        _text(self.draft_v2_id, "draft_v2_id", 255)
        require_sha256_hex(self.draft_v2_hash, "draft_v2_hash")
        if (
            isinstance(self.start_offset, bool)
            or not isinstance(self.start_offset, int)
            or isinstance(self.end_offset, bool)
            or not isinstance(self.end_offset, int)
            or self.start_offset < 0
            or self.start_offset >= self.end_offset
        ):
            raise Step25BoundaryError(Step25ReasonCode.DRAFT_V2_INVALID)
        text = _text(
            self.exact_claim_text,
            "exact_claim_text",
            MAX_DRAFT_V2_CLAIM_UTF8_BYTES,
        )
        _text(
            self.normalized_match_text,
            "normalized_match_text",
            MAX_DRAFT_V2_CLAIM_UTF8_BYTES,
        )
        require_enum_member(self.claim_type, ClaimType, "claim_type")
        require_enum_member(self.atomicity, ClaimAtomicity, "atomicity")
        object.__setattr__(self, "scope_dimensions", _scope_tuple(self.scope_dimensions))
        object.__setattr__(
            self,
            "cited_citation_ids",
            _string_tuple(self.cited_citation_ids, "cited_citation_ids", 64),
        )
        object.__setattr__(
            self,
            "aligned_draft_v1_claim_ids",
            _string_tuple(
                self.aligned_draft_v1_claim_ids,
                "aligned_draft_v1_claim_ids",
                MAX_DRAFT_V2_CLAIMS,
            ),
        )
        object.__setattr__(self, "reason_codes", _reason_tuple(self.reason_codes))
        if Step25ReasonCode.SCHEMA_CHECK_PASS not in self.reason_codes:
            raise ContractValidationError("Draft V2 claim extraction reason is missing")
        object.__setattr__(self, "exact_claim_text_sha256", _sha256_text(text))
        claim_id = derive_draft_v2_claim_id(
            self.draft_v2_hash,
            self.start_offset,
            self.end_offset,
            text,
        )
        object.__setattr__(self, "claim_id", claim_id)
        object.__setattr__(
            self,
            "claim_hash",
            canonical_sha256(self, exclude_fields=("claim_hash",)),
        )


@dataclass(frozen=True, slots=True)
class RequiredCorrectionCompliance:
    correction_id: str
    source_claim_id: str
    status: CorrectionComplianceStatus
    matched_draft_v2_claim_ids: tuple[str, ...]
    reason_codes: tuple[Step25ReasonCode, ...]
    compliance_hash: str = field(init=False)

    def __post_init__(self) -> None:
        _text(self.correction_id, "correction_id", 255)
        _text(self.source_claim_id, "source_claim_id", 255)
        require_enum_member(self.status, CorrectionComplianceStatus, "status")
        matched = _string_tuple(
            self.matched_draft_v2_claim_ids,
            "matched_draft_v2_claim_ids",
            MAX_DRAFT_V2_CLAIMS,
        )
        if any(_DRAFT_V2_CLAIM_ID.fullmatch(item) is None for item in matched):
            raise ContractValidationError("matched Draft V2 claim identity is invalid")
        object.__setattr__(self, "matched_draft_v2_claim_ids", matched)
        object.__setattr__(self, "reason_codes", _reason_tuple(self.reason_codes))
        object.__setattr__(
            self,
            "compliance_hash",
            canonical_sha256(self, exclude_fields=("compliance_hash",)),
        )


@dataclass(frozen=True, slots=True)
class ProhibitedClaimCompliance:
    prohibited_claim_id: str
    source_claim_id: str
    presence: ProhibitedClaimPresence
    matched_draft_v2_claim_ids: tuple[str, ...]
    reason_codes: tuple[Step25ReasonCode, ...]
    compliance_hash: str = field(init=False)

    def __post_init__(self) -> None:
        _text(self.prohibited_claim_id, "prohibited_claim_id", 255)
        _text(self.source_claim_id, "source_claim_id", 255)
        require_enum_member(self.presence, ProhibitedClaimPresence, "presence")
        matched = _string_tuple(
            self.matched_draft_v2_claim_ids,
            "matched_draft_v2_claim_ids",
            MAX_DRAFT_V2_CLAIMS,
        )
        if any(_DRAFT_V2_CLAIM_ID.fullmatch(item) is None for item in matched):
            raise ContractValidationError("matched Draft V2 claim identity is invalid")
        object.__setattr__(self, "matched_draft_v2_claim_ids", matched)
        object.__setattr__(self, "reason_codes", _reason_tuple(self.reason_codes))
        object.__setattr__(
            self,
            "compliance_hash",
            canonical_sha256(self, exclude_fields=("compliance_hash",)),
        )


@dataclass(frozen=True, slots=True)
class SemanticVerifierRequest:
    claim_id: str
    draft_v2_hash: str
    correction_packet_hash: str
    claim_text: str
    allowed_citation_ids: tuple[str, ...]
    evidence_context: tuple[str, ...]
    deterministic_context_digest: str
    provider_identity: ProviderIdentity
    prompt_template: PromptTemplate
    generation_parameters: GenerationParameters
    timeout_policy: TimeoutPolicy
    attempt_policy: AttemptPolicy
    request_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if _DRAFT_V2_CLAIM_ID.fullmatch(self.claim_id) is None:
            raise ContractValidationError("semantic claim_id is invalid")
        for value, name in (
            (self.draft_v2_hash, "draft_v2_hash"),
            (self.correction_packet_hash, "correction_packet_hash"),
            (self.deterministic_context_digest, "deterministic_context_digest"),
        ):
            require_sha256_hex(value, name)
        _text(self.claim_text, "claim_text", MAX_DRAFT_V2_CLAIM_UTF8_BYTES)
        object.__setattr__(
            self,
            "allowed_citation_ids",
            _string_tuple(
                self.allowed_citation_ids,
                "allowed_citation_ids",
                MAX_SEMANTIC_EVIDENCE_ITEMS,
            ),
        )
        context = _string_tuple(
            self.evidence_context,
            "evidence_context",
            MAX_SEMANTIC_EVIDENCE_ITEMS,
            maximum_item_bytes=4096,
        )
        if sum(len(item.encode("utf-8")) for item in context) > MAX_SEMANTIC_CONTEXT_UTF8_BYTES:
            raise ContractValidationError("semantic evidence context exceeds its bound")
        object.__setattr__(self, "evidence_context", context)
        if self.provider_identity != load_approved_provider_spec().provider_identity():
            raise Step25BoundaryError(Step25ReasonCode.SEMANTIC_VERIFIER_INVALID)
        if (
            self.prompt_template.template_id != SEMANTIC_VERIFIER_PROMPT_ID
            or self.prompt_template.template_version != SEMANTIC_VERIFIER_PROMPT_VERSION
            or self.generation_parameters.policy_id != SEMANTIC_VERIFIER_POLICY_ID
        ):
            raise Step25BoundaryError(Step25ReasonCode.SEMANTIC_VERIFIER_INVALID)
        object.__setattr__(
            self,
            "request_hash",
            canonical_sha256(
                {
                    "claim_id": self.claim_id,
                    "draft_v2_hash": self.draft_v2_hash,
                    "correction_packet_hash": self.correction_packet_hash,
                    "claim_text_sha256": _sha256_text(self.claim_text),
                    "allowed_citation_ids": self.allowed_citation_ids,
                    "evidence_context": self.evidence_context,
                    "deterministic_context_digest": self.deterministic_context_digest,
                    "provider_identity_digest": self.provider_identity.identity_digest,
                    "prompt_template_digest": self.prompt_template.template_digest,
                    "generation_parameters_digest": self.generation_parameters.parameters_digest,
                    "timeout_policy_digest": self.timeout_policy.policy_digest,
                    "attempt_policy_digest": self.attempt_policy.policy_digest,
                }
            ),
        )


@dataclass(frozen=True, slots=True)
class SemanticVerifierSignal:
    semantic_request_hash: str
    candidate_verdict: SemanticCandidateVerdict
    evidence_reference_ids: tuple[str, ...]
    reason_codes: tuple[Step25ReasonCode, ...]
    provider_response_hash: str | None
    signal_hash: str = field(init=False)

    def __post_init__(self) -> None:
        require_sha256_hex(self.semantic_request_hash, "semantic_request_hash")
        require_enum_member(
            self.candidate_verdict,
            SemanticCandidateVerdict,
            "candidate_verdict",
        )
        object.__setattr__(
            self,
            "evidence_reference_ids",
            _string_tuple(
                self.evidence_reference_ids,
                "evidence_reference_ids",
                MAX_SEMANTIC_EVIDENCE_ITEMS,
            ),
        )
        object.__setattr__(self, "reason_codes", _reason_tuple(self.reason_codes))
        if self.provider_response_hash is not None:
            require_sha256_hex(self.provider_response_hash, "provider_response_hash")
        object.__setattr__(
            self,
            "signal_hash",
            canonical_sha256(self, exclude_fields=("signal_hash",)),
        )


@dataclass(frozen=True, slots=True)
class LayeredClaimVerification:
    claim_id: str
    claim_hash: str
    draft_v2_hash: str
    schema_layer_result: CheckResult
    packet_compliance_result: CheckResult
    deterministic_fact_result: CheckResult
    temporal_result: CheckResult
    source_result: CheckResult
    citation_result: CheckResult
    evidence_binding_result: EvidenceBindingResult
    semantic_verifier_result: SemanticVerifierSignal
    final_step25_verdict: FinalStep25ClaimVerdict
    reason_codes: tuple[Step25ReasonCode, ...]
    limitations: tuple[str, ...]
    verification_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if _DRAFT_V2_CLAIM_ID.fullmatch(self.claim_id) is None:
            raise ContractValidationError("verification claim_id is invalid")
        require_sha256_hex(self.claim_hash, "claim_hash")
        require_sha256_hex(self.draft_v2_hash, "draft_v2_hash")
        for value, enum_type, name in (
            (self.schema_layer_result, CheckResult, "schema_layer_result"),
            (self.packet_compliance_result, CheckResult, "packet_compliance_result"),
            (self.deterministic_fact_result, CheckResult, "deterministic_fact_result"),
            (self.temporal_result, CheckResult, "temporal_result"),
            (self.source_result, CheckResult, "source_result"),
            (self.citation_result, CheckResult, "citation_result"),
            (self.evidence_binding_result, EvidenceBindingResult, "evidence_binding_result"),
            (self.final_step25_verdict, FinalStep25ClaimVerdict, "final_step25_verdict"),
        ):
            require_enum_member(value, enum_type, name)
        if not isinstance(self.semantic_verifier_result, SemanticVerifierSignal):
            raise ContractValidationError("semantic_verifier_result must be typed")
        object.__setattr__(self, "reason_codes", _reason_tuple(self.reason_codes))
        object.__setattr__(
            self,
            "limitations",
            _string_tuple(self.limitations, "limitations", MAX_LIMITATIONS),
        )
        object.__setattr__(
            self,
            "verification_hash",
            canonical_sha256(self, exclude_fields=("verification_hash",)),
        )


@dataclass(frozen=True, slots=True)
class DraftV2VerificationSummary:
    schema_version: str
    request_id: str
    tenant_id: str
    user_id: str
    route_hash: str
    draft_v2_hash: str
    correction_packet_hash: str
    claim_count: int
    verified_supported_count: int
    verified_refuted_count: int
    unverified_count: int
    conflicting_count: int
    invalid_count: int
    required_correction_results: tuple[RequiredCorrectionCompliance, ...]
    prohibited_claim_results: tuple[ProhibitedClaimCompliance, ...]
    required_corrections_satisfied: int
    required_corrections_unsatisfied: int
    prohibited_claim_violations: int
    citation_failures: int
    ordered_claim_verification_hashes: tuple[str, ...]
    summary_status: VerificationSummaryStatus
    reason_codes: tuple[Step25ReasonCode, ...]
    persistence_decision: str = PERSISTENCE_DECISION
    summary_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != STEP25_SCHEMA_VERSION:
            raise ContractValidationError("unsupported verification summary schema")
        for value, name in (
            (self.request_id, "request_id"),
            (self.tenant_id, "tenant_id"),
            (self.user_id, "user_id"),
        ):
            _text(value, name, 255)
        for value, name in (
            (self.route_hash, "route_hash"),
            (self.draft_v2_hash, "draft_v2_hash"),
            (self.correction_packet_hash, "correction_packet_hash"),
        ):
            require_sha256_hex(value, name)
        counts = (
            self.claim_count,
            self.verified_supported_count,
            self.verified_refuted_count,
            self.unverified_count,
            self.conflicting_count,
            self.invalid_count,
            self.required_corrections_satisfied,
            self.required_corrections_unsatisfied,
            self.prohibited_claim_violations,
            self.citation_failures,
        )
        if any(isinstance(item, bool) or not isinstance(item, int) or item < 0 for item in counts):
            raise ContractValidationError("verification summary count is invalid")
        if self.claim_count != sum(
            (
                self.verified_supported_count,
                self.verified_refuted_count,
                self.unverified_count,
                self.conflicting_count,
                self.invalid_count,
            )
        ):
            raise ContractValidationError("claim verdict counts do not add up")
        corrections = tuple(self.required_correction_results)
        prohibitions = tuple(self.prohibited_claim_results)
        if any(not isinstance(item, RequiredCorrectionCompliance) for item in corrections):
            raise ContractValidationError("required correction results must be typed")
        if any(not isinstance(item, ProhibitedClaimCompliance) for item in prohibitions):
            raise ContractValidationError("prohibited claim results must be typed")
        if corrections != tuple(sorted(corrections, key=lambda item: item.correction_id)):
            raise ContractValidationError("required correction results are not canonical")
        if prohibitions != tuple(
            sorted(prohibitions, key=lambda item: item.prohibited_claim_id)
        ):
            raise ContractValidationError("prohibited claim results are not canonical")
        object.__setattr__(self, "required_correction_results", corrections)
        object.__setattr__(self, "prohibited_claim_results", prohibitions)
        object.__setattr__(
            self,
            "ordered_claim_verification_hashes",
            _hash_tuple(
                self.ordered_claim_verification_hashes,
                "ordered_claim_verification_hashes",
                MAX_DRAFT_V2_CLAIMS,
                ordered=True,
            ),
        )
        if len(self.ordered_claim_verification_hashes) != self.claim_count:
            raise ContractValidationError("verification hash coverage is incomplete")
        require_enum_member(self.summary_status, VerificationSummaryStatus, "summary_status")
        object.__setattr__(self, "reason_codes", _reason_tuple(self.reason_codes))
        if self.persistence_decision != PERSISTENCE_DECISION:
            raise ContractValidationError("Step 25 persistence decision is invalid")
        object.__setattr__(
            self,
            "summary_hash",
            canonical_sha256(self, exclude_fields=("summary_hash",)),
        )


@dataclass(frozen=True, slots=True)
class DraftV2PipelineResult:
    draft_v2: DraftV2
    generation_result: DraftV2GenerationResult | None
    ordered_claims: tuple[DraftV2ClaimRecord, ...]
    ordered_claim_verifications: tuple[LayeredClaimVerification, ...]
    verification_summary: DraftV2VerificationSummary
    replayed: bool
    persisted: bool
    result_hash: str = field(init=False)

    def __post_init__(self) -> None:
        verify_draft_v2_hash(self.draft_v2)
        if self.generation_result is not None:
            verify_draft_v2_generation_result_hash(self.generation_result)
            if (
                self.generation_result.result_hash != self.draft_v2.generation_result_hash
                or self.generation_result.response_text != self.draft_v2.draft_text
            ):
                raise IntegrityError("generation result is detached from Draft V2")
        claims = tuple(self.ordered_claims)
        verifications = tuple(self.ordered_claim_verifications)
        if len(claims) != len(verifications):
            raise ContractValidationError("claim verification coverage is incomplete")
        if claims != tuple(
            sorted(claims, key=lambda item: (item.start_offset, item.end_offset, item.claim_id))
        ):
            raise ContractValidationError("Draft V2 claims are not canonical")
        if tuple(item.claim_id for item in claims) != tuple(
            item.claim_id for item in verifications
        ):
            raise ContractValidationError("claim verification order differs")
        for claim in claims:
            verify_draft_v2_claim_hash(claim)
        for verification in verifications:
            verify_layered_claim_verification_hash(verification)
        verify_verification_summary_hash(self.verification_summary)
        if (
            self.verification_summary.draft_v2_hash != self.draft_v2.draft_v2_hash
            or self.verification_summary.ordered_claim_verification_hashes
            != tuple(item.verification_hash for item in verifications)
        ):
            raise IntegrityError("verification summary is detached from pipeline result")
        if not isinstance(self.replayed, bool) or not isinstance(self.persisted, bool):
            raise ContractValidationError("pipeline replay flags must be boolean")
        object.__setattr__(self, "ordered_claims", claims)
        object.__setattr__(self, "ordered_claim_verifications", verifications)
        object.__setattr__(
            self,
            "result_hash",
            canonical_sha256(
                {
                    "draft_v2_hash": self.draft_v2.draft_v2_hash,
                    "generation_result_hash": (
                        self.generation_result.result_hash
                        if self.generation_result is not None
                        else None
                    ),
                    "ordered_claim_hashes": tuple(item.claim_hash for item in claims),
                    "ordered_verification_hashes": tuple(
                        item.verification_hash for item in verifications
                    ),
                    "verification_summary_hash": self.verification_summary.summary_hash,
                    "replayed": self.replayed,
                    "persisted": self.persisted,
                }
            ),
        )


_DRAFT_V2_REFERENCE_FIELDS = {
    "schema_version",
    "draft_v2_id",
    "generation_request_hash",
    "request_id",
    "tenant_id",
    "user_id",
    "route_hash",
    "original_query_digest",
    "draft_v1_hash",
    "correction_packet_hash",
    "packet_integrity_receipt_hash",
    "generation_result_hash",
    "provider_identity_digest",
    "prompt_template_digest",
    "generation_parameters_digest",
    "timeout_policy_digest",
    "attempt_policy_digest",
    "provider_request_id",
    "model_id",
    "model_version",
    "finish_reason",
    "attempt_count",
    "usage_metadata",
    "draft_text",
    "created_at",
    "draft_text_sha256",
    "draft_byte_length",
    "draft_v2_hash",
}


def _strict_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def encode_draft_v2_reference(value: DraftV2) -> str:
    verify_draft_v2_hash(value)
    payload = {
        name: (
            value.created_at.isoformat().replace("+00:00", "Z")
            if name == "created_at"
            else dict(value.usage_metadata)
            if name == "usage_metadata"
            else getattr(value, name)
        )
        for name in sorted(_DRAFT_V2_REFERENCE_FIELDS)
    }
    raw = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    if len(raw) > MAX_PERSISTED_DRAFT_V2_ENVELOPE_BYTES:
        raise ContractValidationError("Draft V2 persistence envelope exceeds bound")
    return DRAFT_V2_REFERENCE_PREFIX + base64.b64encode(raw).decode("ascii")


def decode_draft_v2_reference(value: object) -> DraftV2:
    if not isinstance(value, str) or not value.startswith(DRAFT_V2_REFERENCE_PREFIX):
        raise IntegrityError("Draft V2 reference has the wrong media type")
    try:
        raw = base64.b64decode(value[len(DRAFT_V2_REFERENCE_PREFIX) :], validate=True)
        if len(raw) > MAX_PERSISTED_DRAFT_V2_ENVELOPE_BYTES:
            raise ValueError("Draft V2 envelope too large")
        decoded = json.loads(raw.decode("utf-8"), object_pairs_hook=_strict_pairs)
    except (binascii.Error, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise IntegrityError("Draft V2 reference is invalid") from exc
    if not isinstance(decoded, dict) or set(decoded) != _DRAFT_V2_REFERENCE_FIELDS:
        raise IntegrityError("Draft V2 reference fields are invalid")
    claimed = {
        "draft_text_sha256": decoded.pop("draft_text_sha256"),
        "draft_byte_length": decoded.pop("draft_byte_length"),
        "draft_v2_hash": decoded.pop("draft_v2_hash"),
    }
    try:
        decoded["created_at"] = datetime.fromisoformat(
            str(decoded["created_at"]).replace("Z", "+00:00")
        )
        draft = DraftV2(**decoded)
    except (TypeError, ValueError, ContractValidationError, IntegrityError) as exc:
        raise IntegrityError("Draft V2 reference contract is invalid") from exc
    if (
        draft.draft_text_sha256 != claimed["draft_text_sha256"]
        or draft.draft_byte_length != claimed["draft_byte_length"]
        or draft.draft_v2_hash != claimed["draft_v2_hash"]
    ):
        raise IntegrityError("Draft V2 reference integrity mismatch")
    return draft


def verify_draft_v2_generation_request_hash(value: DraftV2GenerationRequest) -> None:
    reconstructed = DraftV2GenerationRequest(
        draft_v1=value.draft_v1,
        correction_packet=value.correction_packet,
        integrity_receipt=value.integrity_receipt,
        request_id=value.request_id,
        tenant_id=value.tenant_id,
        user_id=value.user_id,
        route_hash=value.route_hash,
        original_query_digest=value.original_query_digest,
        draft_v1_hash=value.draft_v1_hash,
        correction_packet_hash=value.correction_packet_hash,
        packet_integrity_receipt_hash=value.packet_integrity_receipt_hash,
        provider_identity=value.provider_identity,
        prompt_template=value.prompt_template,
        generation_parameters=value.generation_parameters,
        timeout_policy=value.timeout_policy,
        attempt_policy=value.attempt_policy,
    )
    if reconstructed.request_hash != value.request_hash:
        raise IntegrityError("Draft V2 generation request hash mismatch")


def verify_draft_v2_generation_result_hash(value: DraftV2GenerationResult) -> None:
    verify_canonical_hash(
        value,
        value.result_hash,
        exclude_fields=("result_hash", "response_text", "latency_milliseconds"),
    )
    if (
        value.response_text_sha256 != _sha256_text(value.response_text)
        or value.response_byte_length != len(value.response_text.encode("utf-8"))
    ):
        raise IntegrityError("Draft V2 generation result content mismatch")


def verify_draft_v2_hash(value: DraftV2) -> None:
    verify_canonical_hash(
        value,
        value.draft_v2_hash,
        exclude_fields=("draft_v2_hash", "draft_text", "created_at"),
    )
    if (
        value.draft_text_sha256 != _sha256_text(value.draft_text)
        or value.draft_byte_length != len(value.draft_text.encode("utf-8"))
        or value.draft_v2_id != derive_draft_v2_id(value.generation_request_hash)
    ):
        raise IntegrityError("Draft V2 text or identity mismatch")


def verify_draft_v2_claim_hash(value: DraftV2ClaimRecord) -> None:
    verify_canonical_hash(value, value.claim_hash, exclude_fields=("claim_hash",))
    if (
        value.claim_id
        != derive_draft_v2_claim_id(
            value.draft_v2_hash,
            value.start_offset,
            value.end_offset,
            value.exact_claim_text,
        )
        or value.exact_claim_text_sha256 != _sha256_text(value.exact_claim_text)
    ):
        raise IntegrityError("Draft V2 claim identity mismatch")


def verify_semantic_signal_hash(value: SemanticVerifierSignal) -> None:
    verify_canonical_hash(value, value.signal_hash, exclude_fields=("signal_hash",))


def verify_layered_claim_verification_hash(value: LayeredClaimVerification) -> None:
    verify_canonical_hash(
        value,
        value.verification_hash,
        exclude_fields=("verification_hash",),
    )
    verify_semantic_signal_hash(value.semantic_verifier_result)


def verify_verification_summary_hash(value: DraftV2VerificationSummary) -> None:
    verify_canonical_hash(value, value.summary_hash, exclude_fields=("summary_hash",))


def verify_draft_v2_pipeline_result_hash(value: DraftV2PipelineResult) -> None:
    """Verify the complete Step 25 envelope before a final-output decision.

    Step 25 originally verified every nested record while constructing the
    envelope.  Step 26 is a later trust boundary, so it also needs an exported
    verifier for an envelope that may have crossed a persistence or process
    boundary.
    """

    if not isinstance(value, DraftV2PipelineResult):
        raise ContractValidationError("value must be a DraftV2PipelineResult")
    verify_draft_v2_hash(value.draft_v2)
    if value.generation_result is not None:
        verify_draft_v2_generation_result_hash(value.generation_result)
        if (
            value.generation_result.result_hash
            != value.draft_v2.generation_result_hash
            or value.generation_result.response_text != value.draft_v2.draft_text
        ):
            raise IntegrityError("generation result is detached from Draft V2")
    for claim in value.ordered_claims:
        verify_draft_v2_claim_hash(claim)
    for verification in value.ordered_claim_verifications:
        verify_layered_claim_verification_hash(verification)
    verify_verification_summary_hash(value.verification_summary)
    expected = canonical_sha256(
        {
            "draft_v2_hash": value.draft_v2.draft_v2_hash,
            "generation_result_hash": (
                value.generation_result.result_hash
                if value.generation_result is not None
                else None
            ),
            "ordered_claim_hashes": tuple(
                item.claim_hash for item in value.ordered_claims
            ),
            "ordered_verification_hashes": tuple(
                item.verification_hash
                for item in value.ordered_claim_verifications
            ),
            "verification_summary_hash": value.verification_summary.summary_hash,
            "replayed": value.replayed,
            "persisted": value.persisted,
        }
    )
    require_sha256_hex(value.result_hash, "result_hash")
    if expected != value.result_hash:
        raise IntegrityError("Draft V2 pipeline result hash mismatch")


__all__ = [
    "CheckResult",
    "CorrectionComplianceStatus",
    "DRAFT_V2_ATTEMPT_POLICY_ID",
    "DRAFT_V2_CLAIM_SPAN_CONVENTION",
    "DRAFT_V2_GENERATION_POLICY_ID",
    "DRAFT_V2_PROMPT_TEMPLATE_ID",
    "DRAFT_V2_PROMPT_TEMPLATE_VERSION",
    "DRAFT_V2_TIMEOUT_POLICY_ID",
    "DraftV2",
    "DraftV2ClaimRecord",
    "DraftV2GenerationRequest",
    "DraftV2GenerationResult",
    "DraftV2PipelineResult",
    "DraftV2VerificationSummary",
    "EvidenceBindingResult",
    "FinalStep25ClaimVerdict",
    "LayeredClaimVerification",
    "MAX_DRAFT_V2_CLAIMS",
    "MAX_DRAFT_V2_UTF8_BYTES",
    "PERSISTENCE_DECISION",
    "ProhibitedClaimCompliance",
    "ProhibitedClaimPresence",
    "RequiredCorrectionCompliance",
    "SEMANTIC_VERIFIER_POLICY_ID",
    "SEMANTIC_VERIFIER_PROMPT_ID",
    "SEMANTIC_VERIFIER_PROMPT_VERSION",
    "STEP25_SCHEMA_VERSION",
    "SemanticCandidateVerdict",
    "SemanticVerifierRequest",
    "SemanticVerifierSignal",
    "Step25BoundaryError",
    "Step25ReasonCode",
    "VerificationSummaryStatus",
    "decode_draft_v2_reference",
    "derive_draft_v2_claim_id",
    "derive_draft_v2_id",
    "encode_draft_v2_reference",
    "reason_codes",
    "verify_draft_v2_claim_hash",
    "verify_draft_v2_generation_request_hash",
    "verify_draft_v2_generation_result_hash",
    "verify_draft_v2_hash",
    "verify_draft_v2_pipeline_result_hash",
    "verify_layered_claim_verification_hash",
    "verify_semantic_signal_hash",
    "verify_verification_summary_hash",
]
