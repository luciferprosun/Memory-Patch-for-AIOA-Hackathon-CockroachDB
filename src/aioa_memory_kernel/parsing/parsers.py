"""Concrete strict text/plain and application/json parser profiles."""

from __future__ import annotations

import json
import math
import unicodedata
from dataclasses import dataclass
from typing import Any

from aioa_memory_kernel.contracts.serialization import sha256_hex

from .errors import ParsingResourceLimitError, ParsingValidationError
from .models import ParserProfile, ResourceLimits, SectionKind
from .normalization import NormalizedText, decode_and_normalize


PLAIN_TEXT_PROFILE = ParserProfile(
    name="generic-utf8-plain-text-parser",
    version="1.0.0",
    contract_version="1.0.0",
    media_type="text/plain",
)
JSON_PROFILE = ParserProfile(
    name="generic-canonical-json-document-parser",
    version="1.0.0",
    contract_version="1.0.0",
    media_type="application/json",
)


@dataclass(frozen=True, slots=True)
class SectionDraft:
    ordinal: int
    kind: SectionKind
    start: int
    end: int
    content: str
    structural_locator: str | None
    metadata: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ParsedContent:
    normalized: NormalizedText
    rendered_text: str
    sections: tuple[SectionDraft, ...]


def _limit(message: str, code: str = "RESOURCE_LIMIT_EXCEEDED") -> None:
    raise ParsingResourceLimitError(message, sanitized_code=code)


def _plain_sections(text: str, limits: ResourceLimits) -> tuple[SectionDraft, ...]:
    sections: list[SectionDraft] = []
    offset = 0
    block_start: int | None = None
    block_end = 0
    for line in text.splitlines(keepends=True):
        line_end = offset + len(line)
        content_end = line_end - 1 if line.endswith("\n") else line_end
        if line.rstrip("\n").strip(" \t"):
            if block_start is None:
                block_start = offset
            block_end = content_end
        elif block_start is not None:
            content = text[block_start:block_end]
            if len(content) > limits.maximum_section_length:
                _limit("plain-text section exceeds its character limit")
            sections.append(
                SectionDraft(
                    ordinal=len(sections),
                    kind=SectionKind.TEXT_BLOCK,
                    start=block_start,
                    end=block_end,
                    content=content,
                    structural_locator=None,
                    metadata={"projection": "BLANK_LINE_SEPARATED_BLOCK"},
                )
            )
            block_start = None
        offset = line_end
    if block_start is not None:
        content = text[block_start:block_end]
        if len(content) > limits.maximum_section_length:
            _limit("plain-text section exceeds its character limit")
        sections.append(
            SectionDraft(
                ordinal=len(sections),
                kind=SectionKind.TEXT_BLOCK,
                start=block_start,
                end=block_end,
                content=content,
                structural_locator=None,
                metadata={"projection": "BLANK_LINE_SEPARATED_BLOCK"},
            )
        )
    if len(sections) > limits.maximum_sections:
        _limit("plain-text document exceeds the section-count limit")
    return tuple(sections)


def parse_plain_text(
    payload: bytes,
    *,
    expected_sha256: str,
    expected_length: int,
    limits: ResourceLimits,
    profile: ParserProfile = PLAIN_TEXT_PROFILE,
) -> ParsedContent:
    normalized = decode_and_normalize(
        payload,
        expected_sha256=expected_sha256,
        expected_length=expected_length,
        profile=profile.normalization,
        limits=limits,
    )
    sections = _plain_sections(normalized.text, limits)
    if not sections:
        raise ParsingValidationError(
            "plain-text document contains no non-empty block",
            sanitized_code="EMPTY_DOCUMENT",
        )
    return ParsedContent(normalized, normalized.text, sections)


class _DuplicateMember(ValueError):
    pass


class _NonFiniteNumber(ValueError):
    pass


def _object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateMember(key)
        result[key] = value
    return result


def _parse_constant(value: str) -> None:
    raise _NonFiniteNumber(value)


def _preflight_json_depth(text: str, maximum: int) -> None:
    depth = 0
    in_string = False
    escaped = False
    for character in text:
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character in "[{":
            depth += 1
            if depth > maximum:
                _limit("JSON nesting exceeds the depth limit", "JSON_DEPTH_LIMIT")
        elif character in "]}":
            depth -= 1


def _normalize_json_string(value: str) -> str:
    return unicodedata.normalize("NFC", value.replace("\r\n", "\n").replace("\r", "\n"))


def _normalize_and_measure_json(
    value: Any,
    limits: ResourceLimits,
    depth: int = 0,
    counters: dict[str, int] | None = None,
) -> Any:
    if counters is None:
        counters = {"members": 0}
    if depth > limits.maximum_json_depth or depth > limits.maximum_recursion_depth:
        _limit("JSON nesting exceeds the depth limit", "JSON_DEPTH_LIMIT")
    if isinstance(value, str):
        normalized = _normalize_json_string(value)
        if len(normalized) > limits.maximum_string_length:
            _limit("JSON string exceeds its length limit")
        return normalized
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ParsingValidationError(
                "JSON number is not finite",
                sanitized_code="JSON_NONFINITE_NUMBER",
            )
        return value
    if isinstance(value, list):
        if len(value) > limits.maximum_json_array_length:
            _limit("JSON array exceeds its item limit")
        return [
            _normalize_and_measure_json(item, limits, depth + 1, counters)
            for item in value
        ]
    if isinstance(value, dict):
        counters["members"] += len(value)
        if counters["members"] > limits.maximum_json_members:
            _limit("JSON object members exceed the document limit")
        normalized: dict[str, Any] = {}
        for raw_key, item in value.items():
            key = _normalize_json_string(raw_key)
            if len(key) > limits.maximum_string_length:
                _limit("JSON object key exceeds its length limit")
            if key in normalized:
                raise ParsingValidationError(
                    "JSON keys collide after canonical normalization",
                    sanitized_code="JSON_DUPLICATE_MEMBER",
                )
            normalized[key] = _normalize_and_measure_json(
                item,
                limits,
                depth + 1,
                counters,
            )
        return normalized
    raise ParsingValidationError(
        "JSON decoder returned an unsupported type",
        sanitized_code="JSON_SYNTAX_INVALID",
    )


def _pointer_component(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _render_json_with_sections(
    value: Any,
    limits: ResourceLimits,
) -> tuple[str, tuple[SectionDraft, ...]]:
    output: list[str] = []
    sections: list[SectionDraft] = []
    length = 0

    def append(text: str) -> None:
        nonlocal length
        output.append(text)
        length += len(text)

    def render(node: Any, pointer: str, depth: int) -> None:
        if depth > limits.maximum_recursion_depth:
            _limit("JSON rendering exceeds its recursion limit", "JSON_DEPTH_LIMIT")
        if isinstance(node, dict) and node:
            append("{")
            for index, key in enumerate(sorted(node)):
                if index:
                    append(",")
                append(json.dumps(key, ensure_ascii=False, allow_nan=False))
                append(":")
                render(node[key], f"{pointer}/{_pointer_component(key)}", depth + 1)
            append("}")
            return
        if isinstance(node, list) and node:
            append("[")
            for index, item in enumerate(node):
                if index:
                    append(",")
                render(item, f"{pointer}/{index}", depth + 1)
            append("]")
            return
        start = length
        rendered = json.dumps(
            node,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        append(rendered)
        end = length
        if len(rendered) > limits.maximum_section_length:
            _limit("JSON value section exceeds its character limit")
        value_type = (
            "null"
            if node is None
            else "boolean"
            if isinstance(node, bool)
            else "number"
            if isinstance(node, (int, float))
            else "string"
            if isinstance(node, str)
            else "array"
            if isinstance(node, list)
            else "object"
        )
        sections.append(
            SectionDraft(
                ordinal=len(sections),
                kind=SectionKind.JSON_VALUE,
                start=start,
                end=end,
                content=rendered,
                structural_locator=pointer if pointer else "#",
                metadata={
                    "json_pointer": pointer,
                    "json_value_type": value_type,
                    "projection": "CANONICAL_LEAF_OR_EMPTY_CONTAINER",
                },
            )
        )

    render(value, "", 0)
    if len(sections) > limits.maximum_sections:
        _limit("JSON document exceeds the section-count limit")
    return "".join(output), tuple(sections)


def parse_json_document(
    payload: bytes,
    *,
    expected_sha256: str,
    expected_length: int,
    limits: ResourceLimits,
    profile: ParserProfile = JSON_PROFILE,
) -> ParsedContent:
    normalized = decode_and_normalize(
        payload,
        expected_sha256=expected_sha256,
        expected_length=expected_length,
        profile=profile.normalization,
        limits=limits,
    )
    _preflight_json_depth(normalized.text, limits.maximum_json_depth)
    try:
        value = json.loads(
            normalized.text,
            object_pairs_hook=_object_pairs,
            parse_constant=_parse_constant,
        )
    except _DuplicateMember as exc:
        raise ParsingValidationError(
            "JSON contains a duplicate object member",
            sanitized_code="JSON_DUPLICATE_MEMBER",
        ) from exc
    except _NonFiniteNumber as exc:
        raise ParsingValidationError(
            "JSON contains a non-finite number",
            sanitized_code="JSON_NONFINITE_NUMBER",
        ) from exc
    except (json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise ParsingValidationError(
            "JSON syntax is invalid",
            sanitized_code="JSON_SYNTAX_INVALID",
        ) from exc
    value = _normalize_and_measure_json(value, limits)
    rendered, sections = _render_json_with_sections(value, limits)
    if len(rendered) > limits.maximum_decoded_characters:
        _limit("canonical JSON output exceeds the character limit")
    canonical_normalized = NormalizedText(
        text=rendered,
        bom_observed=normalized.bom_observed,
        input_sha256=normalized.input_sha256,
        input_byte_length=normalized.input_byte_length,
        normalized_sha256=sha256_hex(rendered),
    )
    return ParsedContent(canonical_normalized, rendered, sections)


PARSER_FUNCTIONS = {
    "text/plain": parse_plain_text,
    "application/json": parse_json_document,
}

PARSER_PROFILES = {
    "text/plain": PLAIN_TEXT_PROFILE,
    "application/json": JSON_PROFILE,
}
