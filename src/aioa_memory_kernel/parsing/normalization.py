"""Strict bounded UTF-8 decoding and canonical NFC normalization."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

from aioa_memory_kernel.contracts.serialization import sha256_hex

from .errors import ParsingResourceLimitError, ParsingValidationError
from .models import NormalizationProfile, ResourceLimits


UTF8_BOM = b"\xef\xbb\xbf"


@dataclass(frozen=True, slots=True)
class NormalizedText:
    text: str
    bom_observed: bool
    input_sha256: str
    input_byte_length: int
    normalized_sha256: str


def _fail_limit(message: str) -> None:
    raise ParsingResourceLimitError(
        message,
        sanitized_code="RESOURCE_LIMIT_EXCEEDED",
    )


def decode_and_normalize(
    payload: bytes,
    *,
    expected_sha256: str,
    expected_length: int,
    profile: NormalizationProfile,
    limits: ResourceLimits,
) -> NormalizedText:
    """Verify exact bytes, decode strictly, preserve semantics, and emit NFC."""

    if not isinstance(payload, bytes):
        raise ParsingValidationError(
            "parser input must be immutable bytes",
            sanitized_code="INVALID_PARSER_INPUT",
        )
    if len(payload) != expected_length:
        raise ParsingValidationError(
            "input byte length differs from the locked snapshot",
            sanitized_code="INPUT_LENGTH_MISMATCH",
        )
    if len(payload) > limits.maximum_input_bytes:
        _fail_limit("input exceeds the Step 11 byte limit")
    digest = sha256_hex(payload)
    if digest != expected_sha256:
        raise ParsingValidationError(
            "input digest differs from the locked snapshot",
            sanitized_code="INPUT_DIGEST_MISMATCH",
        )
    if not isinstance(profile, NormalizationProfile):
        raise ParsingValidationError(
            "normalization profile has the wrong type",
            sanitized_code="INVALID_NORMALIZATION_PROFILE",
        )
    bom_observed = payload.startswith(UTF8_BOM)
    encoded = payload[len(UTF8_BOM) :] if bom_observed else payload
    try:
        decoded = encoded.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ParsingValidationError(
            "input is not strict UTF-8",
            sanitized_code="INVALID_UTF8",
        ) from exc
    if len(decoded) > limits.maximum_decoded_characters:
        _fail_limit("decoded text exceeds the character limit")
    normalized_lines = decoded.replace("\r\n", "\n").replace("\r", "\n")
    for character in normalized_lines:
        codepoint = ord(character)
        if character == "\x00":
            raise ParsingValidationError(
                "NUL is prohibited in parsed content",
                sanitized_code="PROHIBITED_CONTROL_CHARACTER",
            )
        if unicodedata.category(character) == "Cc" and character not in {
            "\t",
            "\n",
        }:
            raise ParsingValidationError(
                f"prohibited control U+{codepoint:04X}",
                sanitized_code="PROHIBITED_CONTROL_CHARACTER",
            )
    try:
        normalized = unicodedata.normalize("NFC", normalized_lines)
    except Exception as exc:
        raise ParsingValidationError(
            "Unicode normalization failed",
            sanitized_code="NORMALIZATION_FAILURE",
        ) from exc
    if unicodedata.normalize("NFC", normalized) != normalized:
        raise ParsingValidationError(
            "Unicode normalization was not idempotent",
            sanitized_code="NORMALIZATION_NON_IDEMPOTENT",
        )
    if len(normalized) > limits.maximum_decoded_characters:
        _fail_limit("normalized text exceeds the character limit")
    return NormalizedText(
        text=normalized,
        bom_observed=bom_observed,
        input_sha256=digest,
        input_byte_length=len(payload),
        normalized_sha256=sha256_hex(normalized),
    )
