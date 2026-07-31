"""Immutable Step 11 parsing, normalization, chunking, and finding models."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping

from aioa_memory_kernel.contracts.enums import StableStringEnum
from aioa_memory_kernel.contracts.serialization import (
    canonical_json_bytes,
    canonical_sha256,
    ensure_utc,
    freeze_json,
    require_sha256_hex,
    sha256_hex,
)

from .errors import ParsingValidationError


PARSING_SCHEMA_VERSION = "1.0.0"
PARSER_CONTRACT_VERSION = "generic-parsing-pipeline-1a"
VALIDATOR_CONTRACT_VERSION = "generic-parse-artifact-validator-1a"
NORMALIZATION_PROFILE_NAME = "unicode-nfc-text-normalization"
NORMALIZATION_PROFILE_VERSION = "1.0.0"
CHUNKING_PROFILE_NAME = "model-neutral-character-chunking"
CHUNKING_PROFILE_VERSION = "1.0.0"
SECURITY_RULESET_NAME = "prompt-injection-static-rules"
SECURITY_RULESET_VERSION = "1.0.0"
OFFSET_BASIS = "NORMALIZED_UNICODE_CODE_POINTS_NFC"

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,254}$")
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$")
_MEDIA_TYPE = re.compile(
    r"^[a-z0-9][a-z0-9!#$&^_.+-]{0,126}/"
    r"[a-z0-9][a-z0-9!#$&^_.+-]{0,126}$"
)
_CODE = re.compile(r"^[A-Z0-9][A-Z0-9_:-]{0,127}$")
_DOCUMENT_ID = re.compile(r"^parsedoc-[0-9a-f]{64}$")
_SECTION_ID = re.compile(r"^parsesection-[0-9a-f]{64}$")
_CHUNK_ID = re.compile(r"^parsechunk-[0-9a-f]{64}$")
_FINDING_ID = re.compile(r"^parsefinding-[0-9a-f]{64}$")
_LANGUAGE_TAG = re.compile(
    r"^(?:"
    r"[A-Za-z]{2,3}(?:-[A-Za-z]{3}){0,3}"
    r"|[A-Za-z]{4}"
    r"|[A-Za-z]{5,8}"
    r")"
    r"(?:-[A-Za-z]{4})?"
    r"(?:-(?:[A-Za-z]{2}|[0-9]{3}))?"
    r"(?:-(?:[A-Za-z0-9]{5,8}|[0-9][A-Za-z0-9]{3}))*"
    r"(?:-[0-9A-WY-Za-wy-z](?:-[A-Za-z0-9]{2,8})+)*"
    r"(?:-x(?:-[A-Za-z0-9]{1,8})+)?$"
)


def _text(value: object, field_name: str, maximum: int = 255) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > maximum
        or any(ord(character) < 32 for character in value)
    ):
        raise ParsingValidationError(
            f"{field_name} must be a bounded canonical string",
            sanitized_code="INVALID_PARSING_VALUE",
        )
    return value


def _identifier(value: object, field_name: str) -> str:
    text = _text(value, field_name)
    if _IDENTIFIER.fullmatch(text) is None:
        raise ParsingValidationError(
            f"{field_name} is not a canonical identifier",
            sanitized_code="INVALID_PARSING_IDENTIFIER",
        )
    return text


def _optional_identifier(value: object | None, field_name: str) -> str | None:
    return None if value is None else _identifier(value, field_name)


def _digest(value: object, field_name: str) -> str:
    try:
        return require_sha256_hex(value, field_name)  # type: ignore[arg-type]
    except Exception as exc:
        raise ParsingValidationError(
            f"{field_name} must be a lowercase SHA-256 digest",
            sanitized_code="INVALID_PARSING_DIGEST",
        ) from exc


def _timestamp(value: object, field_name: str) -> datetime:
    try:
        return ensure_utc(value, field_name)  # type: ignore[arg-type]
    except Exception as exc:
        raise ParsingValidationError(
            f"{field_name} must be timezone-aware",
            sanitized_code="INVALID_PARSING_TIMESTAMP",
        ) from exc


def _version(value: object, field_name: str) -> str:
    result = _text(value, field_name, 64)
    if _VERSION.fullmatch(result) is None or result.casefold() == "latest":
        raise ParsingValidationError(
            f"{field_name} must be an immutable explicit version",
            sanitized_code="MUTABLE_PARSER_VERSION_ALIAS",
        )
    return result


def _metadata(value: Mapping[str, Any], field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ParsingValidationError(
            f"{field_name} must be a mapping",
            sanitized_code="INVALID_PARSING_METADATA",
        )
    try:
        frozen = freeze_json(value)
        encoded = canonical_json_bytes(frozen)
    except Exception as exc:
        raise ParsingValidationError(
            f"{field_name} is not canonical JSON data",
            sanitized_code="INVALID_PARSING_METADATA",
        ) from exc
    if len(encoded) > 16 * 1024:
        raise ParsingValidationError(
            f"{field_name} exceeds the metadata bound",
            sanitized_code="RESOURCE_LIMIT_EXCEEDED",
        )
    assert isinstance(frozen, Mapping)
    return frozen


def _codes(values: tuple[str, ...], field_name: str) -> tuple[str, ...]:
    if not isinstance(values, tuple):
        raise ParsingValidationError(
            f"{field_name} must be an immutable tuple",
            sanitized_code="INVALID_PARSING_VALUE",
        )
    result = tuple(sorted({_text(value, field_name, 128) for value in values}))
    if result != values or any(_CODE.fullmatch(value) is None for value in result):
        raise ParsingValidationError(
            f"{field_name} must contain sorted unique reason codes",
            sanitized_code="INVALID_PARSING_VALUE",
        )
    return result


class SectionKind(StableStringEnum):
    TEXT_BLOCK = "TEXT_BLOCK"
    JSON_VALUE = "JSON_VALUE"


class FindingSeverity(StableStringEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    BLOCKING = "BLOCKING"


class FindingAction(StableStringEnum):
    RECORD_ONLY = "RECORD_ONLY"
    OPERATOR_REVIEW = "OPERATOR_REVIEW"
    QUARANTINE = "QUARANTINE"


class FindingCategory(StableStringEnum):
    ROLE_OR_SYSTEM_INSTRUCTION_MARKER = "ROLE_OR_SYSTEM_INSTRUCTION_MARKER"
    INSTRUCTION_OVERRIDE_PHRASE = "INSTRUCTION_OVERRIDE_PHRASE"
    TOOL_OR_COMMAND_EXECUTION_REQUEST = "TOOL_OR_COMMAND_EXECUTION_REQUEST"
    SECRET_OR_CREDENTIAL_EXFILTRATION_REQUEST = (
        "SECRET_OR_CREDENTIAL_EXFILTRATION_REQUEST"
    )
    REMOTE_OR_INDIRECT_INSTRUCTION = "REMOTE_OR_INDIRECT_INSTRUCTION"
    HIDDEN_MARKUP_OR_COMMENT_INSTRUCTION = (
        "HIDDEN_MARKUP_OR_COMMENT_INSTRUCTION"
    )
    ENCODED_OR_OBFUSCATED_INSTRUCTION_SIGNAL = (
        "ENCODED_OR_OBFUSCATED_INSTRUCTION_SIGNAL"
    )
    ZERO_WIDTH_OR_BIDI_CONTROL_SIGNAL = "ZERO_WIDTH_OR_BIDI_CONTROL_SIGNAL"
    RAG_POISONING_OR_RETRIEVAL_MANIPULATION_SIGNAL = (
        "RAG_POISONING_OR_RETRIEVAL_MANIPULATION_SIGNAL"
    )


class QuarantineReason(StableStringEnum):
    UNSUPPORTED_MEDIA_TYPE = "UNSUPPORTED_MEDIA_TYPE"
    INPUT_DIGEST_MISMATCH = "INPUT_DIGEST_MISMATCH"
    INPUT_LENGTH_MISMATCH = "INPUT_LENGTH_MISMATCH"
    INVALID_UTF8 = "INVALID_UTF8"
    PROHIBITED_CONTROL_CHARACTER = "PROHIBITED_CONTROL_CHARACTER"
    JSON_SYNTAX_INVALID = "JSON_SYNTAX_INVALID"
    JSON_DUPLICATE_MEMBER = "JSON_DUPLICATE_MEMBER"
    JSON_NONFINITE_NUMBER = "JSON_NONFINITE_NUMBER"
    JSON_DEPTH_LIMIT = "JSON_DEPTH_LIMIT"
    RESOURCE_LIMIT_EXCEEDED = "RESOURCE_LIMIT_EXCEEDED"
    NORMALIZATION_FAILURE = "NORMALIZATION_FAILURE"
    NORMALIZATION_NON_IDEMPOTENT = "NORMALIZATION_NON_IDEMPOTENT"
    SECTION_RANGE_INVALID = "SECTION_RANGE_INVALID"
    SECTION_CONTENT_MISMATCH = "SECTION_CONTENT_MISMATCH"
    CHUNK_RANGE_INVALID = "CHUNK_RANGE_INVALID"
    CHUNK_CONTENT_MISMATCH = "CHUNK_CONTENT_MISMATCH"
    CHUNK_COVERAGE_INVALID = "CHUNK_COVERAGE_INVALID"
    SECURITY_FINDING_LIMIT_EXCEEDED = "SECURITY_FINDING_LIMIT_EXCEEDED"
    BLOCKING_PROMPT_INJECTION_SIGNAL = "BLOCKING_PROMPT_INJECTION_SIGNAL"
    PERSISTENCE_CONFLICT = "PERSISTENCE_CONFLICT"
    PARSE_RECEIPT_BINDING_MISMATCH = "PARSE_RECEIPT_BINDING_MISMATCH"
    VALIDATION_RECEIPT_BINDING_MISMATCH = "VALIDATION_RECEIPT_BINDING_MISMATCH"
    EMPTY_DOCUMENT = "EMPTY_DOCUMENT"


@dataclass(frozen=True, slots=True)
class ResourceLimits:
    """Versioned, hardware-bounded limits with no silent truncation."""

    policy_name: str = "generic-parsing-resource-limits"
    policy_version: str = "1.0.0"
    maximum_input_bytes: int = 64 * 1024 * 1024
    maximum_decoded_characters: int = 8 * 1024 * 1024
    maximum_json_depth: int = 64
    maximum_json_members: int = 100_000
    maximum_json_array_length: int = 100_000
    maximum_string_length: int = 1 * 1024 * 1024
    maximum_sections: int = 100_000
    maximum_section_length: int = 4 * 1024 * 1024
    maximum_chunks: int = 100_000
    maximum_chunk_length: int = 1024
    maximum_security_findings: int = 1024
    maximum_metadata_bytes: int = 16 * 1024
    maximum_recursion_depth: int = 64

    def __post_init__(self) -> None:
        object.__setattr__(self, "policy_name", _identifier(self.policy_name, "policy_name"))
        object.__setattr__(self, "policy_version", _version(self.policy_version, "policy_version"))
        for field_name in (
            "maximum_input_bytes",
            "maximum_decoded_characters",
            "maximum_json_depth",
            "maximum_json_members",
            "maximum_json_array_length",
            "maximum_string_length",
            "maximum_sections",
            "maximum_section_length",
            "maximum_chunks",
            "maximum_chunk_length",
            "maximum_security_findings",
            "maximum_metadata_bytes",
            "maximum_recursion_depth",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ParsingValidationError(
                    f"{field_name} must be a positive integer",
                    sanitized_code="INVALID_RESOURCE_POLICY",
                )
        if self.maximum_input_bytes > 64 * 1024 * 1024:
            raise ParsingValidationError(
                "Step 11 input cannot exceed the Step 10 storage bound",
                sanitized_code="INVALID_RESOURCE_POLICY",
            )

    @property
    def policy_digest(self) -> str:
        return canonical_sha256(self)


@dataclass(frozen=True, slots=True)
class NormalizationProfile:
    name: str = NORMALIZATION_PROFILE_NAME
    version: str = NORMALIZATION_PROFILE_VERSION
    unicode_form: str = "NFC"
    encoding: str = "UTF-8-STRICT"
    bom_policy: str = "ALLOW_ONE_LEADING_UTF8_BOM"
    line_ending_policy: str = "CRLF_AND_CR_TO_LF"
    final_newline_policy: str = "PRESERVE_EXACTLY"

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _identifier(self.name, "normalization_name"))
        object.__setattr__(self, "version", _version(self.version, "normalization_version"))
        if self.unicode_form != "NFC":
            raise ParsingValidationError(
                "Step 11 permits only canonical NFC normalization",
                sanitized_code="INVALID_NORMALIZATION_PROFILE",
            )
        for field_name in (
            "encoding",
            "bom_policy",
            "line_ending_policy",
            "final_newline_policy",
        ):
            object.__setattr__(self, field_name, _text(getattr(self, field_name), field_name, 128))

    @property
    def profile_id(self) -> str:
        return f"{self.name}:{self.version}"

    @property
    def profile_digest(self) -> str:
        return canonical_sha256(self)


@dataclass(frozen=True, slots=True)
class ParserProfile:
    name: str
    version: str
    contract_version: str
    media_type: str
    normalization: NormalizationProfile = field(default_factory=NormalizationProfile)

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _identifier(self.name, "parser_name"))
        object.__setattr__(self, "version", _version(self.version, "parser_version"))
        object.__setattr__(self, "contract_version", _version(self.contract_version, "parser_contract_version"))
        if not isinstance(self.media_type, str) or _MEDIA_TYPE.fullmatch(self.media_type) is None:
            raise ParsingValidationError(
                "media_type must be an exact canonical type/subtype",
                sanitized_code="INVALID_PARSER_MEDIA_TYPE",
            )
        if not isinstance(self.normalization, NormalizationProfile):
            raise ParsingValidationError(
                "parser requires a typed normalization profile",
                sanitized_code="INVALID_NORMALIZATION_PROFILE",
            )

    @property
    def profile_digest(self) -> str:
        return canonical_sha256(self)


@dataclass(frozen=True, slots=True)
class ChunkingProfile:
    name: str = CHUNKING_PROFILE_NAME
    version: str = CHUNKING_PROFILE_VERSION
    maximum_characters: int = 1024
    target_characters: int = 896
    minimum_characters: int = 1
    overlap_characters: int = 64
    boundary_search_window: int = 160
    boundary_priority: tuple[str, ...] = (
        "SECTION_END",
        "LINE_BREAK",
        "SENTENCE_WHITESPACE",
        "WHITESPACE",
        "SAFE_HARD_CUT",
    )
    cross_section_chunks: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _identifier(self.name, "chunking_name"))
        object.__setattr__(self, "version", _version(self.version, "chunking_version"))
        for field_name in (
            "maximum_characters",
            "target_characters",
            "minimum_characters",
            "boundary_search_window",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ParsingValidationError(
                    f"{field_name} must be positive",
                    sanitized_code="INVALID_CHUNKING_PROFILE",
                )
        if (
            not isinstance(self.overlap_characters, int)
            or isinstance(self.overlap_characters, bool)
            or self.overlap_characters < 0
            or self.minimum_characters > self.target_characters
            or self.target_characters > self.maximum_characters
            or self.overlap_characters >= self.maximum_characters
        ):
            raise ParsingValidationError(
                "chunking size and overlap values are inconsistent",
                sanitized_code="INVALID_CHUNKING_PROFILE",
            )
        expected = (
            "SECTION_END",
            "LINE_BREAK",
            "SENTENCE_WHITESPACE",
            "WHITESPACE",
            "SAFE_HARD_CUT",
        )
        if self.boundary_priority != expected or self.cross_section_chunks is not False:
            raise ParsingValidationError(
                "Step 11 1A requires the fixed within-section boundary policy",
                sanitized_code="INVALID_CHUNKING_PROFILE",
            )

    @property
    def profile_digest(self) -> str:
        return canonical_sha256(self)


@dataclass(frozen=True, slots=True)
class LanguageTag:
    """Validated, descriptive BCP 47-style language metadata."""

    value: str

    def __post_init__(self) -> None:
        value = _text(self.value, "language_tag", 63)
        if _LANGUAGE_TAG.fullmatch(value) is None:
            raise ParsingValidationError(
                "language_tag is not a supported BCP 47-style tag",
                sanitized_code="INVALID_LANGUAGE_TAG",
            )
        object.__setattr__(self, "value", value)


@dataclass(frozen=True, slots=True)
class ParsedSection:
    document_id: str
    section_id: str
    section_ordinal: int
    parent_section_id: str | None
    section_kind: SectionKind
    structural_locator: str | None
    normalized_start_offset: int
    normalized_end_offset: int
    content: str = field(repr=False)
    content_sha256: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)
    parser_profile_digest: str = ""
    offset_basis: str = OFFSET_BASIS

    def __post_init__(self) -> None:
        if _DOCUMENT_ID.fullmatch(self.document_id) is None:
            raise ParsingValidationError(
                "section document_id is invalid",
                sanitized_code="SECTION_RANGE_INVALID",
            )
        if not isinstance(self.section_kind, SectionKind):
            raise ParsingValidationError(
                "section_kind has the wrong type",
                sanitized_code="SECTION_RANGE_INVALID",
            )
        if (
            not isinstance(self.section_ordinal, int)
            or isinstance(self.section_ordinal, bool)
            or self.section_ordinal < 0
            or not isinstance(self.normalized_start_offset, int)
            or not isinstance(self.normalized_end_offset, int)
            or self.normalized_start_offset < 0
            or self.normalized_end_offset <= self.normalized_start_offset
        ):
            raise ParsingValidationError(
                "section requires a valid half-open code-point range",
                sanitized_code="SECTION_RANGE_INVALID",
            )
        if not isinstance(self.content, str) or not self.content:
            raise ParsingValidationError(
                "section content must be non-empty text",
                sanitized_code="SECTION_CONTENT_MISMATCH",
            )
        digest = sha256_hex(self.content)
        if self.content_sha256:
            if _digest(self.content_sha256, "section_content_sha256") != digest:
                raise ParsingValidationError(
                    "section content digest mismatch",
                    sanitized_code="SECTION_CONTENT_MISMATCH",
                )
        else:
            object.__setattr__(self, "content_sha256", digest)
        object.__setattr__(self, "parser_profile_digest", _digest(self.parser_profile_digest, "parser_profile_digest"))
        object.__setattr__(self, "metadata", _metadata(self.metadata, "section_metadata"))
        if self.offset_basis != OFFSET_BASIS:
            raise ParsingValidationError(
                "section offset basis is unsupported",
                sanitized_code="SECTION_RANGE_INVALID",
            )
        if self.parent_section_id is not None and _SECTION_ID.fullmatch(self.parent_section_id) is None:
            raise ParsingValidationError(
                "section parent identity is invalid",
                sanitized_code="SECTION_RANGE_INVALID",
            )
        if self.structural_locator is not None:
            object.__setattr__(self, "structural_locator", _text(self.structural_locator, "structural_locator", 2048))
        identity = canonical_sha256(
            {
                "document_id": self.document_id,
                "section_ordinal": self.section_ordinal,
                "parent_section_id": self.parent_section_id,
                "section_kind": self.section_kind,
                "structural_locator": self.structural_locator,
                "normalized_start_offset": self.normalized_start_offset,
                "normalized_end_offset": self.normalized_end_offset,
                "content_sha256": self.content_sha256,
                "metadata": self.metadata,
                "parser_profile_digest": self.parser_profile_digest,
                "offset_basis": self.offset_basis,
            }
        )
        expected_id = f"parsesection-{identity}"
        if self.section_id:
            if self.section_id != expected_id:
                raise ParsingValidationError(
                    "section_id differs from immutable facts",
                    sanitized_code="SECTION_CONTENT_MISMATCH",
                )
        else:
            object.__setattr__(self, "section_id", expected_id)


@dataclass(frozen=True, slots=True)
class ParsedChunk:
    tenant_id: str
    source_id: str
    hat_scope_id: str
    knowledge_version_id: str
    document_id: str
    section_id: str
    chunk_id: str
    chunk_ordinal: int
    section_chunk_ordinal: int
    normalized_start_offset: int
    normalized_end_offset: int
    content: str = field(repr=False)
    content_sha256: str = ""
    chunking_profile_digest: str = ""
    overlap_prefix_characters: int = 0
    language_tag: LanguageTag | None = None
    offset_basis: str = OFFSET_BASIS

    def __post_init__(self) -> None:
        for field_name in ("tenant_id", "source_id", "hat_scope_id", "knowledge_version_id"):
            object.__setattr__(self, field_name, _identifier(getattr(self, field_name), field_name))
        if _DOCUMENT_ID.fullmatch(self.document_id) is None or _SECTION_ID.fullmatch(self.section_id) is None:
            raise ParsingValidationError(
                "chunk document or section identity is invalid",
                sanitized_code="CHUNK_RANGE_INVALID",
            )
        for field_name in ("chunk_ordinal", "section_chunk_ordinal", "normalized_start_offset"):
            value = getattr(self, field_name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ParsingValidationError(
                    f"{field_name} is invalid",
                    sanitized_code="CHUNK_RANGE_INVALID",
                )
        if (
            not isinstance(self.normalized_end_offset, int)
            or self.normalized_end_offset <= self.normalized_start_offset
            or not isinstance(self.content, str)
            or not self.content
        ):
            raise ParsingValidationError(
                "chunk requires non-empty content and a valid range",
                sanitized_code="CHUNK_RANGE_INVALID",
            )
        if (
            not isinstance(self.overlap_prefix_characters, int)
            or isinstance(self.overlap_prefix_characters, bool)
            or self.overlap_prefix_characters < 0
            or self.overlap_prefix_characters >= len(self.content)
        ):
            raise ParsingValidationError(
                "chunk overlap metadata is invalid",
                sanitized_code="CHUNK_RANGE_INVALID",
            )
        digest = sha256_hex(self.content)
        if self.content_sha256:
            if _digest(self.content_sha256, "chunk_content_sha256") != digest:
                raise ParsingValidationError(
                    "chunk content digest mismatch",
                    sanitized_code="CHUNK_CONTENT_MISMATCH",
                )
        else:
            object.__setattr__(self, "content_sha256", digest)
        object.__setattr__(self, "chunking_profile_digest", _digest(self.chunking_profile_digest, "chunking_profile_digest"))
        if self.language_tag is not None and not isinstance(self.language_tag, LanguageTag):
            raise ParsingValidationError(
                "chunk language_tag requires a typed tag",
                sanitized_code="INVALID_LANGUAGE_TAG",
            )
        if self.offset_basis != OFFSET_BASIS:
            raise ParsingValidationError(
                "chunk offset basis is unsupported",
                sanitized_code="CHUNK_RANGE_INVALID",
            )
        identity = canonical_sha256(
            {
                "tenant_id": self.tenant_id,
                "source_id": self.source_id,
                "hat_scope_id": self.hat_scope_id,
                "knowledge_version_id": self.knowledge_version_id,
                "document_id": self.document_id,
                "section_id": self.section_id,
                "chunk_ordinal": self.chunk_ordinal,
                "section_chunk_ordinal": self.section_chunk_ordinal,
                "normalized_start_offset": self.normalized_start_offset,
                "normalized_end_offset": self.normalized_end_offset,
                "content_sha256": self.content_sha256,
                "chunking_profile_digest": self.chunking_profile_digest,
                "overlap_prefix_characters": self.overlap_prefix_characters,
                "language_tag": None if self.language_tag is None else self.language_tag.value,
                "offset_basis": self.offset_basis,
            }
        )
        expected_id = f"parsechunk-{identity}"
        if self.chunk_id:
            if self.chunk_id != expected_id:
                raise ParsingValidationError(
                    "chunk_id differs from immutable facts",
                    sanitized_code="CHUNK_CONTENT_MISMATCH",
                )
        else:
            object.__setattr__(self, "chunk_id", expected_id)


@dataclass(frozen=True, slots=True)
class PromptInjectionFinding:
    document_id: str
    finding_id: str
    rule_id: str
    category: FindingCategory
    severity: FindingSeverity
    normalized_start_offset: int
    normalized_end_offset: int
    section_id: str | None
    evidence_excerpt_sha256: str
    action: FindingAction
    ruleset_name: str = SECURITY_RULESET_NAME
    ruleset_version: str = SECURITY_RULESET_VERSION
    finding_digest: str = ""

    def __post_init__(self) -> None:
        if _DOCUMENT_ID.fullmatch(self.document_id) is None:
            raise ParsingValidationError(
                "finding document identity is invalid",
                sanitized_code="INVALID_SECURITY_FINDING",
            )
        object.__setattr__(self, "rule_id", _text(self.rule_id, "rule_id", 128))
        if not isinstance(self.category, FindingCategory) or not isinstance(self.severity, FindingSeverity) or not isinstance(self.action, FindingAction):
            raise ParsingValidationError(
                "finding enum value is invalid",
                sanitized_code="INVALID_SECURITY_FINDING",
            )
        if (
            not isinstance(self.normalized_start_offset, int)
            or not isinstance(self.normalized_end_offset, int)
            or self.normalized_start_offset < 0
            or self.normalized_end_offset <= self.normalized_start_offset
        ):
            raise ParsingValidationError(
                "finding range is invalid",
                sanitized_code="INVALID_SECURITY_FINDING",
            )
        if self.section_id is not None and _SECTION_ID.fullmatch(self.section_id) is None:
            raise ParsingValidationError(
                "finding section identity is invalid",
                sanitized_code="INVALID_SECURITY_FINDING",
            )
        object.__setattr__(self, "evidence_excerpt_sha256", _digest(self.evidence_excerpt_sha256, "evidence_excerpt_sha256"))
        object.__setattr__(self, "ruleset_name", _identifier(self.ruleset_name, "ruleset_name"))
        object.__setattr__(self, "ruleset_version", _version(self.ruleset_version, "ruleset_version"))
        digest = canonical_sha256(self, exclude_fields=("finding_id", "finding_digest"))
        if self.finding_digest:
            if _digest(self.finding_digest, "finding_digest") != digest:
                raise ParsingValidationError(
                    "finding digest mismatch",
                    sanitized_code="INVALID_SECURITY_FINDING",
                )
        else:
            object.__setattr__(self, "finding_digest", digest)
        expected_id = f"parsefinding-{digest}"
        if self.finding_id:
            if self.finding_id != expected_id:
                raise ParsingValidationError(
                    "finding identity mismatch",
                    sanitized_code="INVALID_SECURITY_FINDING",
                )
        else:
            object.__setattr__(self, "finding_id", expected_id)


@dataclass(frozen=True, slots=True)
class QuarantineDecision:
    required: bool
    reason_codes: tuple[str, ...]
    decision_digest: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.required, bool):
            raise ParsingValidationError(
                "quarantine marker must be boolean",
                sanitized_code="INVALID_QUARANTINE_DECISION",
            )
        object.__setattr__(self, "reason_codes", _codes(self.reason_codes, "quarantine_reason_codes"))
        if self.required != bool(self.reason_codes):
            raise ParsingValidationError(
                "quarantine marker and reasons must agree",
                sanitized_code="INVALID_QUARANTINE_DECISION",
            )
        digest = canonical_sha256(self, exclude_fields=("decision_digest",))
        if self.decision_digest:
            if _digest(self.decision_digest, "decision_digest") != digest:
                raise ParsingValidationError(
                    "quarantine decision digest mismatch",
                    sanitized_code="INVALID_QUARANTINE_DECISION",
                )
        else:
            object.__setattr__(self, "decision_digest", digest)


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    tenant_id: str
    owner_user_id: str | None
    saga_id: str
    source_id: str
    snapshot_id: str
    knowledge_version_id: str
    knowledge_version_ordinal: int
    hat_scope_id: str
    s3_version_id: str
    locked_storage_evidence_digest: str
    input_sha256: str
    input_byte_length: int
    media_type: str
    parser_name: str
    parser_version: str
    parser_contract_version: str
    decoder_profile: str
    bom_policy: str
    bom_observed: bool
    normalization_profile: str
    normalization_version: str
    normalized_content_sha256: str
    normalized_character_length: int
    document_id: str
    section_count: int
    chunk_count: int
    security_finding_count: int
    section_manifest_digest: str
    chunk_manifest_digest: str
    finding_manifest_digest: str
    parse_artifact_digest: str
    completed_at: datetime
    language_tag: LanguageTag | None = None
    schema_version: str = PARSING_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for field_name in ("tenant_id", "saga_id", "source_id", "snapshot_id", "knowledge_version_id", "hat_scope_id"):
            object.__setattr__(self, field_name, _identifier(getattr(self, field_name), field_name))
        object.__setattr__(self, "owner_user_id", _optional_identifier(self.owner_user_id, "owner_user_id"))
        object.__setattr__(self, "s3_version_id", _text(self.s3_version_id, "s3_version_id", 1024))
        for field_name in (
            "locked_storage_evidence_digest",
            "input_sha256",
            "normalized_content_sha256",
            "section_manifest_digest",
            "chunk_manifest_digest",
            "finding_manifest_digest",
            "parse_artifact_digest",
        ):
            object.__setattr__(self, field_name, _digest(getattr(self, field_name), field_name))
        if not isinstance(self.media_type, str) or _MEDIA_TYPE.fullmatch(self.media_type) is None:
            raise ParsingValidationError(
                "document media_type is invalid",
                sanitized_code="INVALID_PARSER_MEDIA_TYPE",
            )
        for field_name in ("parser_name",):
            object.__setattr__(self, field_name, _identifier(getattr(self, field_name), field_name))
        for field_name in ("parser_version", "parser_contract_version", "normalization_version"):
            object.__setattr__(self, field_name, _version(getattr(self, field_name), field_name))
        for field_name in ("decoder_profile", "bom_policy", "normalization_profile"):
            object.__setattr__(self, field_name, _text(getattr(self, field_name), field_name, 128))
        if not isinstance(self.bom_observed, bool):
            raise ParsingValidationError(
                "BOM observation must be boolean",
                sanitized_code="INVALID_PARSING_VALUE",
            )
        for field_name in (
            "input_byte_length",
            "knowledge_version_ordinal",
            "normalized_character_length",
            "section_count",
            "chunk_count",
            "security_finding_count",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ParsingValidationError(
                    f"{field_name} must be non-negative",
                    sanitized_code="INVALID_PARSING_COUNT",
                )
        if self.knowledge_version_ordinal < 1:
            raise ParsingValidationError(
                "knowledge version ordinal must be positive",
                sanitized_code="INVALID_PARSING_COUNT",
            )
        if _DOCUMENT_ID.fullmatch(self.document_id) is None:
            raise ParsingValidationError(
                "document_id is invalid",
                sanitized_code="INVALID_PARSED_DOCUMENT",
            )
        if self.language_tag is not None and not isinstance(self.language_tag, LanguageTag):
            raise ParsingValidationError(
                "document language_tag requires a typed tag",
                sanitized_code="INVALID_LANGUAGE_TAG",
            )
        object.__setattr__(self, "completed_at", _timestamp(self.completed_at, "completed_at"))
        if self.schema_version != PARSING_SCHEMA_VERSION:
            raise ParsingValidationError(
                "parsed document schema version is unsupported",
                sanitized_code="INVALID_PARSED_DOCUMENT",
            )


@dataclass(frozen=True, slots=True)
class ParseArtifact:
    document: ParsedDocument
    normalized_text: str = field(repr=False)
    sections: tuple[ParsedSection, ...]
    chunks: tuple[ParsedChunk, ...]
    findings: tuple[PromptInjectionFinding, ...]
    quarantine: QuarantineDecision

    def __post_init__(self) -> None:
        if not isinstance(self.document, ParsedDocument) or not isinstance(self.normalized_text, str):
            raise ParsingValidationError(
                "parse artifact requires typed document and text",
                sanitized_code="INVALID_PARSE_ARTIFACT",
            )
        for value, expected, name in (
            (self.sections, ParsedSection, "sections"),
            (self.chunks, ParsedChunk, "chunks"),
            (self.findings, PromptInjectionFinding, "findings"),
        ):
            if not isinstance(value, tuple) or any(not isinstance(item, expected) for item in value):
                raise ParsingValidationError(
                    f"{name} must be an immutable typed tuple",
                    sanitized_code="INVALID_PARSE_ARTIFACT",
                )
        if not isinstance(self.quarantine, QuarantineDecision):
            raise ParsingValidationError(
                "parse artifact requires typed quarantine evidence",
                sanitized_code="INVALID_PARSE_ARTIFACT",
            )
        if len(self.normalized_text) != self.document.normalized_character_length or sha256_hex(self.normalized_text) != self.document.normalized_content_sha256:
            raise ParsingValidationError(
                "normalized text differs from document identity",
                sanitized_code="NORMALIZATION_FAILURE",
            )
        if (len(self.sections), len(self.chunks), len(self.findings)) != (
            self.document.section_count,
            self.document.chunk_count,
            self.document.security_finding_count,
        ):
            raise ParsingValidationError(
                "artifact counts differ from the document",
                sanitized_code="INVALID_PARSE_ARTIFACT",
            )
        section_manifest = canonical_sha256(tuple(section.section_id for section in self.sections))
        chunk_manifest = canonical_sha256(tuple(chunk.chunk_id for chunk in self.chunks))
        finding_manifest = canonical_sha256(tuple(finding.finding_id for finding in self.findings))
        if (section_manifest, chunk_manifest, finding_manifest) != (
            self.document.section_manifest_digest,
            self.document.chunk_manifest_digest,
            self.document.finding_manifest_digest,
        ):
            raise ParsingValidationError(
                "artifact manifests differ from document evidence",
                sanitized_code="INVALID_PARSE_ARTIFACT",
            )
        expected_artifact = canonical_sha256(
            {
                "document_id": self.document.document_id,
                "normalized_content_sha256": self.document.normalized_content_sha256,
                "section_manifest_digest": section_manifest,
                "chunk_manifest_digest": chunk_manifest,
                "finding_manifest_digest": finding_manifest,
                "quarantine_decision_digest": self.quarantine.decision_digest,
                "parser_profile": {
                    "name": self.document.parser_name,
                    "version": self.document.parser_version,
                    "contract": self.document.parser_contract_version,
                },
                "normalization_profile": {
                    "name": self.document.normalization_profile,
                    "version": self.document.normalization_version,
                },
            }
        )
        if expected_artifact != self.document.parse_artifact_digest:
            raise ParsingValidationError(
                "parse artifact digest differs from canonical facts",
                sanitized_code="INVALID_PARSE_ARTIFACT",
            )


@dataclass(frozen=True, slots=True)
class ParseValidationResult:
    accepted: bool
    reason_codes: tuple[str, ...]
    parse_artifact_digest: str
    validation_artifact_digest: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.accepted, bool):
            raise ParsingValidationError(
                "validation status must be boolean",
                sanitized_code="INVALID_PARSE_VALIDATION",
            )
        object.__setattr__(self, "reason_codes", _codes(self.reason_codes, "validation_reason_codes"))
        if self.accepted == bool(self.reason_codes):
            raise ParsingValidationError(
                "accepted status and blocking reasons are inconsistent",
                sanitized_code="INVALID_PARSE_VALIDATION",
            )
        object.__setattr__(self, "parse_artifact_digest", _digest(self.parse_artifact_digest, "parse_artifact_digest"))
        digest = canonical_sha256(self, exclude_fields=("validation_artifact_digest",))
        if self.validation_artifact_digest:
            if _digest(self.validation_artifact_digest, "validation_artifact_digest") != digest:
                raise ParsingValidationError(
                    "validation artifact digest mismatch",
                    sanitized_code="INVALID_PARSE_VALIDATION",
                )
        else:
            object.__setattr__(self, "validation_artifact_digest", digest)
