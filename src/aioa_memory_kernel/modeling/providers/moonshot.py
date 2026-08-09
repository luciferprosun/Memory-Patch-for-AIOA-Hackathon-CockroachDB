"""Pinned Moonshot text-only adapter for the evidence-blind Draft V1 call."""

from __future__ import annotations

import json
import os
import socket
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from decimal import Decimal
from typing import Any

from aioa_memory_kernel.contracts.exceptions import ContractValidationError, IntegrityError

from ..models import (
    MAXIMUM_DRAFT_UTF8_BYTES,
    ModelAdapterError,
    ModelReasonCode,
    ProviderCallRequest,
    ProviderIdentity,
    ProviderResponse,
    ProviderSpec,
    ProviderTextRequest,
    TimeoutPolicy,
    load_approved_provider_spec,
    verify_provider_call_request_hash,
    verify_provider_text_request_hash,
)


MAXIMUM_PROVIDER_RESPONSE_BYTES = 256 * 1024
Transport = Callable[[str, Mapping[str, str], bytes, int], Mapping[str, Any]]


def _default_transport(
    endpoint: str,
    headers: Mapping[str, str],
    body: bytes,
    timeout_seconds: int,
) -> Mapping[str, Any]:
    request = urllib.request.Request(
        endpoint,
        data=body,
        headers=dict(headers),
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            raw = response.read(MAXIMUM_PROVIDER_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        if exc.code in {401, 403}:
            raise ModelAdapterError(ModelReasonCode.MODEL_AUTHENTICATION_FAILED) from None
        if exc.code in {400, 404, 405, 409, 415, 422}:
            raise ModelAdapterError(ModelReasonCode.MODEL_REQUEST_INVALID) from None
        if exc.code == 429 or 500 <= exc.code <= 599:
            raise ModelAdapterError(
                ModelReasonCode.MODEL_TRANSIENT_FAILURE,
                retryable=True,
                unknown_completion=True,
            ) from None
        raise ModelAdapterError(ModelReasonCode.MODEL_ADAPTER_UNAVAILABLE) from None
    except (TimeoutError, socket.timeout) as exc:
        raise ModelAdapterError(
            ModelReasonCode.MODEL_TIMEOUT,
            retryable=True,
            unknown_completion=True,
        ) from exc
    except urllib.error.URLError as exc:
        raise ModelAdapterError(
            ModelReasonCode.MODEL_TRANSIENT_FAILURE,
            retryable=True,
        ) from exc
    if len(raw) > MAXIMUM_PROVIDER_RESPONSE_BYTES:
        raise ModelAdapterError(ModelReasonCode.MODEL_RESPONSE_TOO_LARGE)
    try:
        decoded = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ModelAdapterError(ModelReasonCode.MODEL_RESPONSE_INVALID) from exc
    if not isinstance(decoded, Mapping):
        raise ModelAdapterError(ModelReasonCode.MODEL_RESPONSE_INVALID)
    return decoded


def _safe_usage(value: object) -> dict[str, int | None]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ModelAdapterError(ModelReasonCode.MODEL_RESPONSE_INVALID)
    allowed = {"prompt_tokens", "completion_tokens", "total_tokens"}
    result: dict[str, int | None] = {}
    for key, item in value.items():
        if key not in allowed:
            continue
        if item is not None and (
            isinstance(item, bool) or not isinstance(item, int) or item < 0
        ):
            raise ModelAdapterError(ModelReasonCode.MODEL_RESPONSE_INVALID)
        result[str(key)] = item
    return result


class MoonshotDraftV1Adapter:
    """Exact Moonshot adapter with no tools, DB, filesystem, or action port."""

    __slots__ = ("_api_key", "_identity", "_spec", "_transport")

    def __init__(
        self,
        api_key: str,
        *,
        transport: Transport = _default_transport,
        spec: ProviderSpec | None = None,
    ) -> None:
        if not isinstance(api_key, str) or not api_key or len(api_key) > 4096:
            raise ModelAdapterError(ModelReasonCode.MODEL_AUTHENTICATION_FAILED)
        if not callable(transport):
            raise TypeError("transport must be callable")
        approved = spec or load_approved_provider_spec()
        checked = load_approved_provider_spec()
        if approved != checked:
            raise ModelAdapterError(ModelReasonCode.MODEL_IDENTITY_MISMATCH)
        self._api_key = api_key
        self._spec = approved
        self._identity = approved.provider_identity()
        self._transport = transport

    @classmethod
    def from_environment(cls) -> "MoonshotDraftV1Adapter":
        spec = load_approved_provider_spec()
        key = os.environ.get(spec.credential_environment_variable)
        if not key:
            raise ModelAdapterError(ModelReasonCode.MODEL_ADAPTER_UNAVAILABLE)
        return cls(key, spec=spec)

    def __repr__(self) -> str:
        return (
            "MoonshotDraftV1Adapter(provider_id='moonshot-ai', "
            "model_id='moonshot-v1-8k', credential='<redacted>')"
        )

    def provider_identity(self) -> ProviderIdentity:
        return self._identity

    def generate(
        self,
        request: ProviderCallRequest | ProviderTextRequest,
        timeout_policy: TimeoutPolicy,
    ) -> ProviderResponse:
        if not isinstance(request, (ProviderCallRequest, ProviderTextRequest)) or not isinstance(timeout_policy, TimeoutPolicy):
            raise ModelAdapterError(ModelReasonCode.MODEL_REQUEST_INVALID)
        try:
            if isinstance(request, ProviderCallRequest):
                verify_provider_call_request_hash(request)
                user_content = request.original_query
            else:
                verify_provider_text_request_hash(request)
                user_content = request.user_content
        except (ContractValidationError, IntegrityError) as exc:
            raise ModelAdapterError(ModelReasonCode.MODEL_REQUEST_INVALID) from exc
        if request.provider_identity != self._identity:
            raise ModelAdapterError(ModelReasonCode.MODEL_IDENTITY_MISMATCH)

        parameters = request.generation_parameters
        payload: dict[str, Any] = {
            "model": self._spec.model_id,
            "messages": [
                {"role": "system", "content": request.system_instruction},
                {"role": "user", "content": user_content},
            ],
            "temperature": float(Decimal(parameters.temperature)),
            "top_p": float(Decimal(parameters.top_p)),
            "max_tokens": parameters.max_output_tokens,
            "stream": False,
        }
        if parameters.stop_sequences:
            payload["stop"] = list(parameters.stop_sequences)
        body = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        endpoint = self._spec.api_origin + self._spec.chat_completions_path
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json",
            "User-Agent": "aioa-memory-kernel-step22/1a",
        }
        started = time.monotonic_ns()
        decoded = self._transport(
            endpoint,
            headers,
            body,
            timeout_policy.attempt_timeout_seconds,
        )
        latency_ms = max(0, (time.monotonic_ns() - started) // 1_000_000)
        return self._parse_response(decoded, latency_ms)

    def _parse_response(
        self,
        decoded: Mapping[str, Any],
        latency_milliseconds: int,
    ) -> ProviderResponse:
        if "error" in decoded:
            raise ModelAdapterError(ModelReasonCode.MODEL_POLICY_REJECTED)
        model = decoded.get("model")
        if model != self._spec.model_id:
            raise ModelAdapterError(ModelReasonCode.MODEL_IDENTITY_MISMATCH)
        choices = decoded.get("choices")
        if not isinstance(choices, list) or len(choices) != 1:
            raise ModelAdapterError(ModelReasonCode.MODEL_RESPONSE_INVALID)
        choice = choices[0]
        if not isinstance(choice, Mapping):
            raise ModelAdapterError(ModelReasonCode.MODEL_RESPONSE_INVALID)
        message = choice.get("message")
        if not isinstance(message, Mapping):
            raise ModelAdapterError(ModelReasonCode.MODEL_RESPONSE_INVALID)
        if message.get("tool_calls") or message.get("function_call"):
            raise ModelAdapterError(ModelReasonCode.MODEL_TOOLING_BOUNDARY_VIOLATION)
        content = message.get("content")
        if not isinstance(content, str) or not content:
            raise ModelAdapterError(ModelReasonCode.MODEL_RESPONSE_EMPTY)
        if len(content.encode("utf-8")) > MAXIMUM_DRAFT_UTF8_BYTES:
            raise ModelAdapterError(ModelReasonCode.MODEL_RESPONSE_TOO_LARGE)
        finish_reason = choice.get("finish_reason")
        if not isinstance(finish_reason, str) or not finish_reason:
            raise ModelAdapterError(ModelReasonCode.MODEL_RESPONSE_INVALID)
        provider_request_id = decoded.get("id")
        if provider_request_id is not None and not isinstance(provider_request_id, str):
            raise ModelAdapterError(ModelReasonCode.MODEL_RESPONSE_INVALID)
        try:
            return ProviderResponse(
                provider_identity_digest=self._identity.identity_digest,
                model_id=self._spec.model_id,
                model_version=self._spec.model_declared_version,
                provider_request_id=provider_request_id,
                finish_reason=finish_reason,
                response_content=content,
                usage_metadata=_safe_usage(decoded.get("usage")),
                latency_milliseconds=latency_milliseconds,
                tool_calls_present=False,
            )
        except (ContractValidationError, UnicodeError) as exc:
            raise ModelAdapterError(ModelReasonCode.MODEL_RESPONSE_INVALID) from exc


__all__ = ["MoonshotDraftV1Adapter"]
