"""Immutable Step 22 provider, generation, and Draft V1 contracts.

Draft V1 is deliberately evidence-blind model output.  Step 21 identity is
bound only as out-of-band lineage; no route, evidence, approval, execution,
database, or external-action authority is granted by any value here.
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
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from aioa_memory_kernel.contracts.enums import EvidenceStatus, StableStringEnum
from aioa_memory_kernel.contracts.exceptions import ContractValidationError, IntegrityError
from aioa_memory_kernel.contracts.serialization import (
    canonical_json_bytes,
    canonical_sha256,
    ensure_utc,
    freeze_json,
    require_enum_member,
    require_sha256_hex,
    verify_canonical_hash,
)
from aioa_memory_kernel.temporal import TemporalResolutionResult, verify_temporal_result_hash


STEP22_SCHEMA_VERSION = "1.0.0"
APPROVED_PROVIDER_ID = "moonshot-ai"
APPROVED_ADAPTER_VERSION = "moonshot-chat-completions-1a"
APPROVED_MODEL_ID = "moonshot-v1-8k"
APPROVED_MODEL_DECLARED_VERSION = "moonshot-v1-8k"
APPROVED_ENDPOINT_CLASS = "moonshot-public-chat-completions-v1"
APPROVED_API_ORIGIN = "https://api.moonshot.ai"
APPROVED_CHAT_COMPLETIONS_PATH = "/v1/chat/completions"
APPROVED_CREDENTIAL_ENVIRONMENT_VARIABLE = "MOONSHOT_API_KEY"
APPROVED_CONTEXT_WINDOW_TOKENS = 8192
APPROVED_MODEL_REGISTRY_OWNER = "moonshot"
PROMPT_TEMPLATE_ID = "draft-v1-original-query-only-1a"
PROMPT_TEMPLATE_VERSION = "1"
GENERATION_POLICY_ID = "draft-v1-generation-parameters-1a"
GENERATION_POLICY_VERSION = "1"
TIMEOUT_POLICY_ID = "draft-v1-timeout-1a"
TIMEOUT_POLICY_VERSION = "1"
ATTEMPT_POLICY_ID = "draft-v1-retry-1a"
ATTEMPT_POLICY_VERSION = "1"
DEFAULT_MAX_OUTPUT_TOKENS = 512
MAXIMUM_MAX_OUTPUT_TOKENS = 1024
DEFAULT_ATTEMPT_TIMEOUT_SECONDS = 45
MAXIMUM_ATTEMPT_TIMEOUT_SECONDS = 90
DEFAULT_MAX_ATTEMPTS = 2
MAXIMUM_MAX_ATTEMPTS = 2
DEFAULT_RETRY_DELAY_MILLISECONDS = 250
MAXIMUM_ORIGINAL_QUERY_UTF8_BYTES = 4096
MAXIMUM_DRAFT_UTF8_BYTES = 64 * 1024
MAXIMUM_USAGE_METADATA_BYTES = 4096
MAXIMUM_PERSISTED_DRAFT_ENVELOPE_BYTES = 96 * 1024
DRAFT_REFERENCE_PREFIX = "data:application/vnd.aioa.draft-v1+json;base64,"
PROVIDER_CONFIG_PATH = (
    Path(__file__).resolve().parents[3]
    / "config"
    / "modeling"
    / "moonshot-v1-8k-step22-1a.json"
)


class ModelReasonCode(StableStringEnum):
    MODEL_GENERATION_OK = "MODEL_GENERATION_OK"
    MODEL_ADAPTER_UNAVAILABLE = "MODEL_ADAPTER_UNAVAILABLE"
    MODEL_IDENTITY_MISMATCH = "MODEL_IDENTITY_MISMATCH"
    MODEL_AUTHENTICATION_FAILED = "MODEL_AUTHENTICATION_FAILED"
    MODEL_REQUEST_INVALID = "MODEL_REQUEST_INVALID"
    MODEL_TIMEOUT = "MODEL_TIMEOUT"
    MODEL_TRANSIENT_FAILURE = "MODEL_TRANSIENT_FAILURE"
    MODEL_POLICY_REJECTED = "MODEL_POLICY_REJECTED"
    MODEL_RESPONSE_EMPTY = "MODEL_RESPONSE_EMPTY"
    MODEL_RESPONSE_TOO_LARGE = "MODEL_RESPONSE_TOO_LARGE"
    MODEL_RESPONSE_INVALID = "MODEL_RESPONSE_INVALID"
    MODEL_RETRY_EXHAUSTED = "MODEL_RETRY_EXHAUSTED"
    MODEL_TOOLING_BOUNDARY_VIOLATION = "MODEL_TOOLING_BOUNDARY_VIOLATION"
    STEP21_LINEAGE_INVALID = "STEP21_LINEAGE_INVALID"
    DRAFT_PERSISTENCE_CONFLICT = "DRAFT_PERSISTENCE_CONFLICT"
    DRAFT_REPLAYED = "DRAFT_REPLAYED"
    DRAFT_PERSISTED = "DRAFT_PERSISTED"


class ModelAdapterError(RuntimeError):
    """Sanitized Step 22 failure without provider response bodies or secrets."""

    def __init__(
        self,
        reason_code: ModelReasonCode,
        *,
        retryable: bool = False,
        unknown_completion: bool = False,
    ) -> None:
        if not isinstance(reason_code, ModelReasonCode):
            raise TypeError("reason_code must be a ModelReasonCode")
        if not isinstance(retryable, bool) or not isinstance(unknown_completion, bool):
            raise TypeError("error flags must be boolean")
        super().__init__(f"Step 22 model operation failed: {reason_code.value}")
        self.reason_code = reason_code
        self.retryable = retryable
        self.unknown_completion = unknown_completion


_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_LOGICAL_ID = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,127}$")
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+-]{0,127}$")
_DECIMAL = re.compile(r"^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")


def _text(value: object, field_name: str, maximum_bytes: int = 512) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or unicodedata.normalize("NFC", value) != value
        or _CONTROL.search(value)
        or len(value.encode("utf-8")) > maximum_bytes
    ):
        raise ContractValidationError(f"{field_name} must be bounded canonical NFC text")
    return value


def _logical_id(value: object, field_name: str) -> str:
    result = _text(value, field_name, 128)
    if _LOGICAL_ID.fullmatch(result) is None:
        raise ContractValidationError(f"{field_name} must be a logical identifier")
    return result


def _optional_text(
    value: object | None,
    field_name: str,
    maximum_bytes: int = 512,
) -> str | None:
    return None if value is None else _text(value, field_name, maximum_bytes)


def _content(value: object, field_name: str, maximum_bytes: int) -> str:
    if not isinstance(value, str) or not value:
        raise ContractValidationError(f"{field_name} must be non-empty text")
    if unicodedata.normalize("NFC", value) != value:
        raise ContractValidationError(f"{field_name} must use Unicode NFC")
    if len(value.encode("utf-8")) > maximum_bytes:
        raise ContractValidationError(f"{field_name} exceeds its UTF-8 byte limit")
    if any(
        unicodedata.category(character) == "Cc" and character not in {"\t", "\n"}
        for character in value
    ):
        raise ContractValidationError(f"{field_name} contains a prohibited control")
    return value


def _raw_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_decimal(value: object, field_name: str) -> tuple[str, Decimal]:
    if not isinstance(value, str) or _DECIMAL.fullmatch(value) is None:
        raise ContractValidationError(f"{field_name} must be a canonical decimal string")
    try:
        decimal = Decimal(value)
    except InvalidOperation as exc:
        raise ContractValidationError(f"{field_name} is invalid") from exc
    canonical = format(decimal, "f").rstrip("0").rstrip(".") if "." in value else value
    canonical = canonical or "0"
    if value != canonical:
        raise ContractValidationError(f"{field_name} is not canonical")
    return value, decimal


def _reason_tuple(value: object) -> tuple[ModelReasonCode, ...]:
    if not isinstance(value, (tuple, list)) or any(
        not isinstance(item, ModelReasonCode) for item in value
    ):
        raise ContractValidationError("reason codes must use ModelReasonCode")
    return tuple(value)


def _usage_mapping(value: object) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ContractValidationError("usage metadata must be a string-keyed mapping")
    for key, item in value.items():
        _text(key, "usage metadata key", 128)
        if item is not None and (isinstance(item, bool) or not isinstance(item, int) or item < 0):
            raise ContractValidationError("usage metadata values must be non-negative integers")
    frozen = freeze_json(dict(sorted(value.items())))
    if len(canonical_json_bytes(frozen)) > MAXIMUM_USAGE_METADATA_BYTES:
        raise ContractValidationError("usage metadata exceeds its byte limit")
    return frozen


@dataclass(frozen=True, slots=True)
class ProviderIdentity:
    provider_id: str
    adapter_version: str
    model_id: str
    model_revision_or_declared_version: str
    endpoint_class: str
    tooling_disabled: bool
    function_calling_disabled: bool
    web_browsing_disabled: bool
    code_execution_disabled: bool
    immutable_model_revision: bool
    identity_digest: str = field(init=False)

    def __post_init__(self) -> None:
        for value, name in (
            (self.provider_id, "provider_id"),
            (self.adapter_version, "adapter_version"),
            (self.endpoint_class, "endpoint_class"),
        ):
            _logical_id(value, name)
        _text(self.model_id, "model_id", 128)
        if _VERSION.fullmatch(self.model_revision_or_declared_version) is None:
            raise ContractValidationError("model version is invalid")
        for name in (
            "tooling_disabled",
            "function_calling_disabled",
            "web_browsing_disabled",
            "code_execution_disabled",
            "immutable_model_revision",
        ):
            if not isinstance(getattr(self, name), bool):
                raise ContractValidationError(f"{name} must be boolean")
        if not all(
            (
                self.tooling_disabled,
                self.function_calling_disabled,
                self.web_browsing_disabled,
                self.code_execution_disabled,
            )
        ):
            raise ModelAdapterError(ModelReasonCode.MODEL_TOOLING_BOUNDARY_VIOLATION)
        object.__setattr__(
            self,
            "identity_digest",
            canonical_sha256(self, exclude_fields=("identity_digest",)),
        )


@dataclass(frozen=True, slots=True)
class ProviderSpec:
    schema_version: str
    provider_id: str
    adapter_version: str
    model_id: str
    model_declared_version: str
    endpoint_class: str
    api_origin: str
    chat_completions_path: str
    credential_environment_variable: str
    context_window_tokens: int
    model_registry_owner: str
    immutable_model_revision: bool
    tooling_disabled: bool
    function_calling_disabled: bool
    web_browsing_disabled: bool
    code_execution_disabled: bool
    config_digest: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != STEP22_SCHEMA_VERSION:
            raise ModelAdapterError(ModelReasonCode.MODEL_IDENTITY_MISMATCH)
        identity = ProviderIdentity(
            provider_id=self.provider_id,
            adapter_version=self.adapter_version,
            model_id=self.model_id,
            model_revision_or_declared_version=self.model_declared_version,
            endpoint_class=self.endpoint_class,
            tooling_disabled=self.tooling_disabled,
            function_calling_disabled=self.function_calling_disabled,
            web_browsing_disabled=self.web_browsing_disabled,
            code_execution_disabled=self.code_execution_disabled,
            immutable_model_revision=self.immutable_model_revision,
        )
        if self.api_origin != APPROVED_API_ORIGIN or self.chat_completions_path != APPROVED_CHAT_COMPLETIONS_PATH:
            raise ModelAdapterError(ModelReasonCode.MODEL_IDENTITY_MISMATCH)
        if self.credential_environment_variable != APPROVED_CREDENTIAL_ENVIRONMENT_VARIABLE:
            raise ModelAdapterError(ModelReasonCode.MODEL_IDENTITY_MISMATCH)
        if self.context_window_tokens != APPROVED_CONTEXT_WINDOW_TOKENS:
            raise ModelAdapterError(ModelReasonCode.MODEL_IDENTITY_MISMATCH)
        _logical_id(self.model_registry_owner, "model_registry_owner")
        object.__setattr__(
            self,
            "config_digest",
            canonical_sha256(self, exclude_fields=("config_digest",)),
        )
        del identity

    def provider_identity(self) -> ProviderIdentity:
        return ProviderIdentity(
            provider_id=self.provider_id,
            adapter_version=self.adapter_version,
            model_id=self.model_id,
            model_revision_or_declared_version=self.model_declared_version,
            endpoint_class=self.endpoint_class,
            tooling_disabled=self.tooling_disabled,
            function_calling_disabled=self.function_calling_disabled,
            web_browsing_disabled=self.web_browsing_disabled,
            code_execution_disabled=self.code_execution_disabled,
            immutable_model_revision=self.immutable_model_revision,
        )


_PROVIDER_CONFIG_FIELDS = {
    "schema_version",
    "provider_id",
    "adapter_version",
    "model_id",
    "model_declared_version",
    "endpoint_class",
    "api_origin",
    "chat_completions_path",
    "credential_environment_variable",
    "context_window_tokens",
    "model_registry_owner",
    "immutable_model_revision",
    "tooling_disabled",
    "function_calling_disabled",
    "web_browsing_disabled",
    "code_execution_disabled",
    "config_digest",
}


def _strict_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def load_approved_provider_spec() -> ProviderSpec:
    """Load the one checked-in Step 22 provider/model decision."""

    try:
        payload = PROVIDER_CONFIG_PATH.read_bytes()
        if len(payload) > 32 * 1024:
            raise ValueError("provider config exceeds bound")
        decoded = json.loads(payload.decode("utf-8"), object_pairs_hook=_strict_pairs)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ModelAdapterError(ModelReasonCode.MODEL_IDENTITY_MISMATCH) from exc
    if not isinstance(decoded, dict) or set(decoded) != _PROVIDER_CONFIG_FIELDS:
        raise ModelAdapterError(ModelReasonCode.MODEL_IDENTITY_MISMATCH)
    claimed = decoded.pop("config_digest")
    try:
        require_sha256_hex(claimed, "config_digest")
        spec = ProviderSpec(**decoded)
    except (TypeError, ContractValidationError, ModelAdapterError) as exc:
        raise ModelAdapterError(ModelReasonCode.MODEL_IDENTITY_MISMATCH) from exc
    if claimed != spec.config_digest:
        raise ModelAdapterError(ModelReasonCode.MODEL_IDENTITY_MISMATCH)
    exact = (
        spec.provider_id == APPROVED_PROVIDER_ID
        and spec.adapter_version == APPROVED_ADAPTER_VERSION
        and spec.model_id == APPROVED_MODEL_ID
        and spec.model_declared_version == APPROVED_MODEL_DECLARED_VERSION
        and spec.endpoint_class == APPROVED_ENDPOINT_CLASS
        and spec.api_origin == APPROVED_API_ORIGIN
        and spec.chat_completions_path == APPROVED_CHAT_COMPLETIONS_PATH
        and spec.credential_environment_variable == APPROVED_CREDENTIAL_ENVIRONMENT_VARIABLE
        and spec.context_window_tokens == APPROVED_CONTEXT_WINDOW_TOKENS
        and spec.model_registry_owner == APPROVED_MODEL_REGISTRY_OWNER
        and spec.immutable_model_revision is False
        and spec.tooling_disabled is True
        and spec.function_calling_disabled is True
        and spec.web_browsing_disabled is True
        and spec.code_execution_disabled is True
    )
    if not exact:
        raise ModelAdapterError(ModelReasonCode.MODEL_IDENTITY_MISMATCH)
    return spec


@dataclass(frozen=True, slots=True)
class PromptTemplate:
    template_id: str
    template_version: str
    system_instruction: str
    template_digest: str = field(init=False)

    def __post_init__(self) -> None:
        _logical_id(self.template_id, "template_id")
        _text(self.template_version, "template_version", 64)
        _content(self.system_instruction, "system_instruction", 2048)
        object.__setattr__(
            self,
            "template_digest",
            canonical_sha256(self, exclude_fields=("template_digest",)),
        )


def load_draft_v1_prompt_template() -> PromptTemplate:
    return PromptTemplate(
        template_id=PROMPT_TEMPLATE_ID,
        template_version=PROMPT_TEMPLATE_VERSION,
        system_instruction=(
            "Answer the user's question directly and concisely. Do not claim access "
            "to tools, databases, files, browsing, hidden context, or external actions. "
            "Return ordinary inert text only."
        ),
    )


@dataclass(frozen=True, slots=True)
class GenerationParameters:
    temperature: str = "0.2"
    top_p: str = "0.9"
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS
    stop_sequences: tuple[str, ...] = ()
    streaming: bool = False
    seed: int | None = None
    policy_id: str = GENERATION_POLICY_ID
    policy_version: str = GENERATION_POLICY_VERSION
    parameters_digest: str = field(init=False)

    def __post_init__(self) -> None:
        _, temperature = _canonical_decimal(self.temperature, "temperature")
        _, top_p = _canonical_decimal(self.top_p, "top_p")
        if not Decimal("0") <= temperature <= Decimal("2"):
            raise ContractValidationError("temperature is outside policy")
        if not Decimal("0") < top_p <= Decimal("1"):
            raise ContractValidationError("top_p is outside policy")
        if (
            isinstance(self.max_output_tokens, bool)
            or not isinstance(self.max_output_tokens, int)
            or not 1 <= self.max_output_tokens <= MAXIMUM_MAX_OUTPUT_TOKENS
        ):
            raise ContractValidationError("max_output_tokens is outside policy")
        if not isinstance(self.stop_sequences, (tuple, list)):
            raise ContractValidationError("stop_sequences must be ordered")
        stops = tuple(self.stop_sequences)
        if len(stops) > 8 or len(stops) != len(set(stops)):
            raise ContractValidationError("stop_sequences are invalid")
        for item in stops:
            _content(item, "stop sequence", 128)
        object.__setattr__(self, "stop_sequences", stops)
        if self.streaming is not False or self.seed is not None:
            raise ContractValidationError("Step 22 V1 requires non-streaming generation without seed claims")
        _logical_id(self.policy_id, "generation policy_id")
        _text(self.policy_version, "generation policy_version", 64)
        object.__setattr__(
            self,
            "parameters_digest",
            canonical_sha256(self, exclude_fields=("parameters_digest",)),
        )


@dataclass(frozen=True, slots=True)
class TimeoutPolicy:
    attempt_timeout_seconds: int = DEFAULT_ATTEMPT_TIMEOUT_SECONDS
    policy_id: str = TIMEOUT_POLICY_ID
    policy_version: str = TIMEOUT_POLICY_VERSION
    policy_digest: str = field(init=False)

    def __post_init__(self) -> None:
        if (
            isinstance(self.attempt_timeout_seconds, bool)
            or not isinstance(self.attempt_timeout_seconds, int)
            or not 1 <= self.attempt_timeout_seconds <= MAXIMUM_ATTEMPT_TIMEOUT_SECONDS
        ):
            raise ContractValidationError("attempt timeout is outside policy")
        _logical_id(self.policy_id, "timeout policy_id")
        _text(self.policy_version, "timeout policy_version", 64)
        object.__setattr__(
            self,
            "policy_digest",
            canonical_sha256(self, exclude_fields=("policy_digest",)),
        )


@dataclass(frozen=True, slots=True)
class AttemptPolicy:
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    retry_delay_milliseconds: int = DEFAULT_RETRY_DELAY_MILLISECONDS
    policy_id: str = ATTEMPT_POLICY_ID
    policy_version: str = ATTEMPT_POLICY_VERSION
    policy_digest: str = field(init=False)

    def __post_init__(self) -> None:
        if (
            isinstance(self.max_attempts, bool)
            or not isinstance(self.max_attempts, int)
            or not 1 <= self.max_attempts <= MAXIMUM_MAX_ATTEMPTS
        ):
            raise ContractValidationError("max_attempts is outside policy")
        if (
            isinstance(self.retry_delay_milliseconds, bool)
            or not isinstance(self.retry_delay_milliseconds, int)
            or not 0 <= self.retry_delay_milliseconds <= 2000
        ):
            raise ContractValidationError("retry delay is outside policy")
        _logical_id(self.policy_id, "attempt policy_id")
        _text(self.policy_version, "attempt policy_version", 64)
        object.__setattr__(
            self,
            "policy_digest",
            canonical_sha256(self, exclude_fields=("policy_digest",)),
        )


@dataclass(frozen=True, slots=True)
class ProviderCallRequest:
    provider_identity: ProviderIdentity
    prompt_template_id: str
    prompt_template_digest: str
    system_instruction: str
    original_query: str
    original_query_digest: str
    generation_parameters: GenerationParameters
    request_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.provider_identity, ProviderIdentity):
            raise ContractValidationError("provider_identity must be typed")
        _logical_id(self.prompt_template_id, "prompt_template_id")
        require_sha256_hex(self.prompt_template_digest, "prompt_template_digest")
        _content(self.system_instruction, "system_instruction", 2048)
        _content(self.original_query, "original_query", MAXIMUM_ORIGINAL_QUERY_UTF8_BYTES)
        require_sha256_hex(self.original_query_digest, "original_query_digest")
        if self.original_query_digest != _raw_sha256(self.original_query):
            raise IntegrityError("original query digest mismatch")
        if not isinstance(self.generation_parameters, GenerationParameters):
            raise ContractValidationError("generation_parameters must be typed")
        digest = canonical_sha256(
            {
                "provider_identity_digest": self.provider_identity.identity_digest,
                "prompt_template_id": self.prompt_template_id,
                "prompt_template_digest": self.prompt_template_digest,
                "system_instruction_sha256": _raw_sha256(self.system_instruction),
                "original_query_digest": self.original_query_digest,
                "generation_parameters_digest": self.generation_parameters.parameters_digest,
            }
        )
        object.__setattr__(self, "request_hash", digest)


@dataclass(frozen=True, slots=True)
class ModelGenerationRequest:
    request_id: str
    tenant_id: str
    user_id: str
    route_hash: str
    step21_result_hash: str
    step21_evidence_status: EvidenceStatus
    original_query: str
    original_query_digest: str
    provider_identity: ProviderIdentity
    prompt_template: PromptTemplate
    generation_parameters: GenerationParameters
    timeout_policy: TimeoutPolicy
    attempt_policy: AttemptPolicy
    request_hash: str = field(init=False)

    def __post_init__(self) -> None:
        for value, name in (
            (self.request_id, "request_id"),
            (self.tenant_id, "tenant_id"),
            (self.user_id, "user_id"),
        ):
            _text(value, name, 255)
        require_sha256_hex(self.route_hash, "route_hash")
        require_sha256_hex(self.step21_result_hash, "step21_result_hash")
        require_enum_member(self.step21_evidence_status, EvidenceStatus, "step21_evidence_status")
        _content(self.original_query, "original_query", MAXIMUM_ORIGINAL_QUERY_UTF8_BYTES)
        require_sha256_hex(self.original_query_digest, "original_query_digest")
        if self.original_query_digest != _raw_sha256(self.original_query):
            raise IntegrityError("original query digest mismatch")
        for value, expected, name in (
            (self.provider_identity, ProviderIdentity, "provider_identity"),
            (self.prompt_template, PromptTemplate, "prompt_template"),
            (self.generation_parameters, GenerationParameters, "generation_parameters"),
            (self.timeout_policy, TimeoutPolicy, "timeout_policy"),
            (self.attempt_policy, AttemptPolicy, "attempt_policy"),
        ):
            if not isinstance(value, expected):
                raise ContractValidationError(f"{name} must be typed")
        object.__setattr__(
            self,
            "request_hash",
            canonical_sha256(self, exclude_fields=("request_hash", "original_query")),
        )


@dataclass(frozen=True, slots=True)
class ProviderResponse:
    provider_identity_digest: str
    model_id: str
    model_version: str
    provider_request_id: str | None
    finish_reason: str
    response_content: str
    usage_metadata: Mapping[str, Any]
    latency_milliseconds: int
    tool_calls_present: bool = False
    response_content_sha256: str = field(init=False)
    response_byte_length: int = field(init=False)
    response_hash: str = field(init=False)

    def __post_init__(self) -> None:
        require_sha256_hex(self.provider_identity_digest, "provider_identity_digest")
        _text(self.model_id, "model_id", 128)
        _text(self.model_version, "model_version", 128)
        object.__setattr__(
            self,
            "provider_request_id",
            _optional_text(self.provider_request_id, "provider_request_id", 512),
        )
        _text(self.finish_reason, "finish_reason", 128)
        content = _content(self.response_content, "response_content", MAXIMUM_DRAFT_UTF8_BYTES)
        payload = content.encode("utf-8")
        object.__setattr__(self, "response_content_sha256", hashlib.sha256(payload).hexdigest())
        object.__setattr__(self, "response_byte_length", len(payload))
        object.__setattr__(self, "usage_metadata", _usage_mapping(self.usage_metadata))
        if (
            isinstance(self.latency_milliseconds, bool)
            or not isinstance(self.latency_milliseconds, int)
            or not 0 <= self.latency_milliseconds <= 24 * 60 * 60 * 1000
        ):
            raise ContractValidationError("latency_milliseconds is outside policy")
        if self.tool_calls_present is not False:
            raise ModelAdapterError(ModelReasonCode.MODEL_TOOLING_BOUNDARY_VIOLATION)
        object.__setattr__(
            self,
            "response_hash",
            canonical_sha256(
                self,
                exclude_fields=("response_hash", "response_content", "latency_milliseconds"),
            ),
        )


@dataclass(frozen=True, slots=True)
class ModelGenerationResult:
    generation_request_hash: str
    provider_identity_digest: str
    attempt_count: int
    failed_attempt_reason_codes: tuple[ModelReasonCode, ...]
    provider_request_id: str | None
    model_id: str
    model_version: str
    finish_reason: str
    usage_metadata: Mapping[str, Any]
    response_content: str
    response_content_sha256: str
    response_byte_length: int
    latency_milliseconds: int
    provider_response_hash: str
    result_hash: str = field(init=False)

    def __post_init__(self) -> None:
        for value, name in (
            (self.generation_request_hash, "generation_request_hash"),
            (self.provider_identity_digest, "provider_identity_digest"),
            (self.response_content_sha256, "response_content_sha256"),
            (self.provider_response_hash, "provider_response_hash"),
        ):
            require_sha256_hex(value, name)
        if (
            isinstance(self.attempt_count, bool)
            or not isinstance(self.attempt_count, int)
            or not 1 <= self.attempt_count <= MAXIMUM_MAX_ATTEMPTS
        ):
            raise ContractValidationError("attempt_count is outside policy")
        failures = _reason_tuple(self.failed_attempt_reason_codes)
        if len(failures) != self.attempt_count - 1:
            raise ContractValidationError("failed attempt metadata is inconsistent")
        object.__setattr__(self, "failed_attempt_reason_codes", failures)
        object.__setattr__(
            self,
            "provider_request_id",
            _optional_text(self.provider_request_id, "provider_request_id", 512),
        )
        _text(self.model_id, "model_id", 128)
        _text(self.model_version, "model_version", 128)
        _text(self.finish_reason, "finish_reason", 128)
        content = _content(self.response_content, "response_content", MAXIMUM_DRAFT_UTF8_BYTES)
        payload = content.encode("utf-8")
        if self.response_content_sha256 != hashlib.sha256(payload).hexdigest():
            raise IntegrityError("generation response content digest mismatch")
        if self.response_byte_length != len(payload):
            raise IntegrityError("generation response byte length mismatch")
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
                exclude_fields=("result_hash", "response_content", "latency_milliseconds"),
            ),
        )


def derive_draft_id(generation_request_hash: str) -> str:
    require_sha256_hex(generation_request_hash, "generation_request_hash")
    digest = canonical_sha256(
        {"generation_request_hash": generation_request_hash, "draft_stage": 1}
    )
    return f"draft-v1-{digest}"


@dataclass(frozen=True, slots=True)
class DraftV1:
    schema_version: str
    draft_id: str
    generation_request_hash: str
    request_id: str
    tenant_id: str
    user_id: str
    original_query_digest: str
    route_hash: str
    provider_identity_digest: str
    prompt_template_digest: str
    generation_parameters_digest: str
    timeout_policy_digest: str
    attempt_policy_digest: str
    model_generation_result_hash: str
    provider_request_id: str | None
    model_id: str
    model_version: str
    finish_reason: str
    attempt_count: int
    usage_metadata: Mapping[str, Any]
    draft_text: str
    step21_result_hash: str
    step21_evidence_status: EvidenceStatus
    created_at: datetime
    draft_text_sha256: str = field(init=False)
    draft_byte_length: int = field(init=False)
    draft_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != STEP22_SCHEMA_VERSION:
            raise ContractValidationError("unsupported Draft V1 schema")
        _text(self.draft_id, "draft_id", 255)
        if self.draft_id != derive_draft_id(self.generation_request_hash):
            raise IntegrityError("draft identity is detached from generation request")
        for value, name in (
            (self.generation_request_hash, "generation_request_hash"),
            (self.original_query_digest, "original_query_digest"),
            (self.route_hash, "route_hash"),
            (self.provider_identity_digest, "provider_identity_digest"),
            (self.prompt_template_digest, "prompt_template_digest"),
            (self.generation_parameters_digest, "generation_parameters_digest"),
            (self.timeout_policy_digest, "timeout_policy_digest"),
            (self.attempt_policy_digest, "attempt_policy_digest"),
            (self.model_generation_result_hash, "model_generation_result_hash"),
            (self.step21_result_hash, "step21_result_hash"),
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
            or not 1 <= self.attempt_count <= MAXIMUM_MAX_ATTEMPTS
        ):
            raise ContractValidationError("attempt_count is outside policy")
        object.__setattr__(self, "usage_metadata", _usage_mapping(self.usage_metadata))
        text = _content(self.draft_text, "draft_text", MAXIMUM_DRAFT_UTF8_BYTES)
        payload = text.encode("utf-8")
        object.__setattr__(self, "draft_text_sha256", hashlib.sha256(payload).hexdigest())
        object.__setattr__(self, "draft_byte_length", len(payload))
        require_enum_member(self.step21_evidence_status, EvidenceStatus, "step21_evidence_status")
        object.__setattr__(self, "created_at", ensure_utc(self.created_at, "created_at"))
        object.__setattr__(
            self,
            "draft_hash",
            canonical_sha256(self, exclude_fields=("draft_hash", "draft_text")),
        )


@dataclass(frozen=True, slots=True)
class DraftGenerationReceipt:
    draft: DraftV1
    generation_result: ModelGenerationResult | None
    replayed: bool
    persisted: bool
    reason_codes: tuple[ModelReasonCode, ...]
    receipt_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.draft, DraftV1):
            raise ContractValidationError("draft must be DraftV1")
        if self.generation_result is not None:
            verify_generation_result_hash(self.generation_result)
            if (
                self.generation_result.result_hash
                != self.draft.model_generation_result_hash
                or self.generation_result.response_content != self.draft.draft_text
                or self.generation_result.response_content_sha256
                != self.draft.draft_text_sha256
            ):
                raise IntegrityError("generation result is detached from Draft V1")
        if not isinstance(self.replayed, bool) or not isinstance(self.persisted, bool):
            raise ContractValidationError("receipt flags must be boolean")
        reasons = _reason_tuple(self.reason_codes)
        object.__setattr__(self, "reason_codes", reasons)
        object.__setattr__(
            self,
            "receipt_hash",
            canonical_sha256(
                {
                    "draft_hash": self.draft.draft_hash,
                    "generation_result_hash": (
                        self.generation_result.result_hash
                        if self.generation_result is not None
                        else None
                    ),
                    "replayed": self.replayed,
                    "persisted": self.persisted,
                    "reason_codes": reasons,
                }
            ),
        )


_DRAFT_ENVELOPE_FIELDS = {
    "schema_version",
    "draft_id",
    "generation_request_hash",
    "request_id",
    "tenant_id",
    "user_id",
    "original_query_digest",
    "route_hash",
    "provider_identity_digest",
    "prompt_template_digest",
    "generation_parameters_digest",
    "timeout_policy_digest",
    "attempt_policy_digest",
    "model_generation_result_hash",
    "provider_request_id",
    "model_id",
    "model_version",
    "finish_reason",
    "attempt_count",
    "usage_metadata",
    "draft_text",
    "step21_result_hash",
    "step21_evidence_status",
    "created_at",
    "draft_text_sha256",
    "draft_byte_length",
    "draft_hash",
}


def encode_draft_reference(value: DraftV1) -> str:
    verify_draft_v1_hash(value)
    payload = {
        name: (
            value.step21_evidence_status.value
            if name == "step21_evidence_status"
            else value.created_at.isoformat().replace("+00:00", "Z")
            if name == "created_at"
            else dict(value.usage_metadata)
            if name == "usage_metadata"
            else getattr(value, name)
        )
        for name in sorted(_DRAFT_ENVELOPE_FIELDS)
    }
    raw = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    if len(raw) > MAXIMUM_PERSISTED_DRAFT_ENVELOPE_BYTES:
        raise ContractValidationError("Draft V1 persistence envelope exceeds its byte limit")
    return DRAFT_REFERENCE_PREFIX + base64.b64encode(raw).decode("ascii")


def decode_draft_reference(value: object) -> DraftV1:
    if not isinstance(value, str) or not value.startswith(DRAFT_REFERENCE_PREFIX):
        raise IntegrityError("Draft V1 reference has the wrong media type")
    encoded = value[len(DRAFT_REFERENCE_PREFIX) :]
    try:
        raw = base64.b64decode(encoded, validate=True)
        if len(raw) > MAXIMUM_PERSISTED_DRAFT_ENVELOPE_BYTES:
            raise ValueError("envelope too large")
        decoded = json.loads(raw.decode("utf-8"), object_pairs_hook=_strict_pairs)
    except (binascii.Error, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise IntegrityError("Draft V1 reference is invalid") from exc
    if not isinstance(decoded, dict) or set(decoded) != _DRAFT_ENVELOPE_FIELDS:
        raise IntegrityError("Draft V1 reference fields are invalid")
    claimed = {
        "draft_text_sha256": decoded.pop("draft_text_sha256"),
        "draft_byte_length": decoded.pop("draft_byte_length"),
        "draft_hash": decoded.pop("draft_hash"),
    }
    try:
        created = str(decoded["created_at"])
        decoded["created_at"] = datetime.fromisoformat(created.replace("Z", "+00:00"))
        decoded["step21_evidence_status"] = EvidenceStatus(decoded["step21_evidence_status"])
        draft = DraftV1(**decoded)
    except (TypeError, ValueError, ContractValidationError, IntegrityError) as exc:
        raise IntegrityError("Draft V1 reference contract is invalid") from exc
    if (
        draft.draft_text_sha256 != claimed["draft_text_sha256"]
        or draft.draft_byte_length != claimed["draft_byte_length"]
        or draft.draft_hash != claimed["draft_hash"]
    ):
        raise IntegrityError("Draft V1 reference integrity mismatch")
    return draft


def verify_provider_identity_hash(value: ProviderIdentity) -> None:
    verify_canonical_hash(value, value.identity_digest, exclude_fields=("identity_digest",))


def verify_provider_call_request_hash(value: ProviderCallRequest) -> None:
    if not isinstance(value, ProviderCallRequest):
        raise ContractValidationError("provider call request must be typed")
    expected = canonical_sha256(
        {
            "provider_identity_digest": value.provider_identity.identity_digest,
            "prompt_template_id": value.prompt_template_id,
            "prompt_template_digest": value.prompt_template_digest,
            "system_instruction_sha256": _raw_sha256(value.system_instruction),
            "original_query_digest": value.original_query_digest,
            "generation_parameters_digest": value.generation_parameters.parameters_digest,
        }
    )
    if value.request_hash != expected:
        raise IntegrityError("provider call request hash mismatch")


def verify_generation_request_hash(value: ModelGenerationRequest) -> None:
    verify_canonical_hash(
        value,
        value.request_hash,
        exclude_fields=("request_hash", "original_query"),
    )


def verify_provider_response_hash(value: ProviderResponse) -> None:
    verify_canonical_hash(
        value,
        value.response_hash,
        exclude_fields=("response_hash", "response_content", "latency_milliseconds"),
    )
    payload = value.response_content.encode("utf-8")
    if (
        value.response_content_sha256 != hashlib.sha256(payload).hexdigest()
        or value.response_byte_length != len(payload)
    ):
        raise IntegrityError("provider response content integrity mismatch")


def verify_generation_result_hash(value: ModelGenerationResult) -> None:
    verify_canonical_hash(
        value,
        value.result_hash,
        exclude_fields=("result_hash", "response_content", "latency_milliseconds"),
    )
    payload = value.response_content.encode("utf-8")
    if (
        value.response_content_sha256 != hashlib.sha256(payload).hexdigest()
        or value.response_byte_length != len(payload)
    ):
        raise IntegrityError("generation result content integrity mismatch")


def verify_draft_v1_hash(value: DraftV1) -> None:
    verify_canonical_hash(value, value.draft_hash, exclude_fields=("draft_hash", "draft_text"))
    if (
        value.draft_text_sha256 != _raw_sha256(value.draft_text)
        or value.draft_byte_length != len(value.draft_text.encode("utf-8"))
    ):
        raise IntegrityError("Draft V1 text digest mismatch")


def prepare_model_generation_request(
    temporal_result: TemporalResolutionResult,
    original_query: str,
    *,
    generation_parameters: GenerationParameters | None = None,
    timeout_policy: TimeoutPolicy | None = None,
    attempt_policy: AttemptPolicy | None = None,
) -> ModelGenerationRequest:
    if not isinstance(temporal_result, TemporalResolutionResult):
        raise ModelAdapterError(ModelReasonCode.STEP21_LINEAGE_INVALID)
    try:
        verify_temporal_result_hash(temporal_result)
    except (ContractValidationError, IntegrityError) as exc:
        raise ModelAdapterError(ModelReasonCode.STEP21_LINEAGE_INVALID) from exc
    query = _content(original_query, "original_query", MAXIMUM_ORIGINAL_QUERY_UTF8_BYTES)
    spec = load_approved_provider_spec()
    return ModelGenerationRequest(
        request_id=temporal_result.request_id,
        tenant_id=temporal_result.tenant_id,
        user_id=temporal_result.user_id,
        route_hash=temporal_result.route_hash,
        step21_result_hash=temporal_result.result_hash,
        step21_evidence_status=temporal_result.evidence_status,
        original_query=query,
        original_query_digest=_raw_sha256(query),
        provider_identity=spec.provider_identity(),
        prompt_template=load_draft_v1_prompt_template(),
        generation_parameters=generation_parameters or GenerationParameters(),
        timeout_policy=timeout_policy or TimeoutPolicy(),
        attempt_policy=attempt_policy or AttemptPolicy(),
    )


__all__ = [
    "APPROVED_ADAPTER_VERSION",
    "APPROVED_CREDENTIAL_ENVIRONMENT_VARIABLE",
    "APPROVED_MODEL_DECLARED_VERSION",
    "APPROVED_MODEL_ID",
    "APPROVED_PROVIDER_ID",
    "AttemptPolicy",
    "DraftGenerationReceipt",
    "DraftV1",
    "GenerationParameters",
    "MAXIMUM_DRAFT_UTF8_BYTES",
    "ModelAdapterError",
    "ModelGenerationRequest",
    "ModelGenerationResult",
    "ModelReasonCode",
    "PROMPT_TEMPLATE_ID",
    "PROMPT_TEMPLATE_VERSION",
    "PromptTemplate",
    "ProviderCallRequest",
    "ProviderIdentity",
    "ProviderResponse",
    "ProviderSpec",
    "STEP22_SCHEMA_VERSION",
    "TimeoutPolicy",
    "decode_draft_reference",
    "derive_draft_id",
    "encode_draft_reference",
    "load_approved_provider_spec",
    "load_draft_v1_prompt_template",
    "prepare_model_generation_request",
    "verify_draft_v1_hash",
    "verify_generation_request_hash",
    "verify_generation_result_hash",
    "verify_provider_call_request_hash",
    "verify_provider_identity_hash",
    "verify_provider_response_hash",
]
