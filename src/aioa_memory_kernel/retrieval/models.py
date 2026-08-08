"""Immutable Step 18 exact and lexical retrieval contracts.

These records bind retrieval to the Step 17 route.  They carry candidates and
provenance only; they are not an evidence bundle, answer, approval, or action.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from aioa_memory_kernel.contracts.enums import (
    KnowledgeRoute,
    MemoryTargetScope,
    StableStringEnum,
)
from aioa_memory_kernel.contracts.exceptions import (
    ContractValidationError,
    IntegrityError,
)
from aioa_memory_kernel.contracts.scope import ScopeDimension
from aioa_memory_kernel.contracts.serialization import (
    canonical_json_bytes,
    canonical_sha256,
    freeze_json,
    require_enum_member,
    require_sha256_hex,
    verify_canonical_hash,
)
from aioa_memory_kernel.routing import KnowledgeRouteResult, verify_route_hash
from aioa_memory_kernel.sources import (
    SourceAccessClass,
    SourceAuthorityLevel,
    SourcePublicationState,
)


STEP18_SCHEMA_VERSION = "1.0.0"
DEFAULT_RESULT_LIMIT = 20
MAXIMUM_RESULT_LIMIT = 100
MAXIMUM_QUERY_UTF8_BYTES = 4096
MAXIMUM_EXACT_IDENTIFIERS = 16
MAXIMUM_KEYWORDS = 32
MAXIMUM_CANDIDATE_CONTENT_BYTES = 64 * 1024
MAXIMUM_TOTAL_CONTENT_BYTES = 1024 * 1024


class RetrievalMode(StableStringEnum):
    EXACT_IDENTIFIER = "EXACT_IDENTIFIER"
    STATUTE_SECTION = "STATUTE_SECTION"
    FULL_TEXT = "FULL_TEXT"
    KEYWORD = "KEYWORD"


class ExactIdentifierField(StableStringEnum):
    SOURCE_ID = "SOURCE_ID"
    KNOWLEDGE_VERSION_ID = "KNOWLEDGE_VERSION_ID"
    CHUNK_ID = "CHUNK_ID"
    OFFICIAL_IDENTIFIER = "OFFICIAL_IDENTIFIER"
    DOCUMENT_IDENTITY = "DOCUMENT_IDENTITY"
    VERSION_IDENTITY = "VERSION_IDENTITY"


class Step18ReasonCode(StableStringEnum):
    RETRIEVAL_OK = "RETRIEVAL_OK"
    NO_HAT_SELECTED = "NO_HAT_SELECTED"
    AMBIGUOUS_ROUTE = "AMBIGUOUS_ROUTE"
    ROUTE_HASH_INVALID = "ROUTE_HASH_INVALID"
    ROUTE_SCOPE_MISMATCH = "ROUTE_SCOPE_MISMATCH"
    TENANT_MISMATCH = "TENANT_MISMATCH"
    USER_MISMATCH = "USER_MISMATCH"
    REQUEST_ID_MISMATCH = "REQUEST_ID_MISMATCH"
    HAT_IDENTITY_MISMATCH = "HAT_IDENTITY_MISMATCH"
    HAT_SCOPE_MISMATCH = "HAT_SCOPE_MISMATCH"
    INVALID_EXACT_IDENTIFIER = "INVALID_EXACT_IDENTIFIER"
    INVALID_SECTION_IDENTIFIER = "INVALID_SECTION_IDENTIFIER"
    QUERY_TOO_LARGE = "QUERY_TOO_LARGE"
    RESULT_LIMIT_EXCEEDED = "RESULT_LIMIT_EXCEEDED"
    NO_MATCH = "NO_MATCH"
    SOURCE_NOT_PUBLISHED = "SOURCE_NOT_PUBLISHED"
    SOURCE_AUTHORITY_REJECTED = "SOURCE_AUTHORITY_REJECTED"
    ACCESS_CLASS_REJECTED = "ACCESS_CLASS_REJECTED"
    OWNER_SCOPE_REJECTED = "OWNER_SCOPE_REJECTED"
    EXACT_MATCH = "EXACT_MATCH"
    STATUTE_SECTION_MATCH = "STATUTE_SECTION_MATCH"
    FULL_TEXT_MATCH = "FULL_TEXT_MATCH"
    KEYWORD_MATCH = "KEYWORD_MATCH"
    RETRIEVAL_DATABASE_ERROR = "RETRIEVAL_DATABASE_ERROR"
    RETRIEVAL_SCHEMA_UNSUPPORTED = "RETRIEVAL_SCHEMA_UNSUPPORTED"


class RetrievalBoundaryError(RuntimeError):
    """Sanitized fail-closed error carrying a stable Step 18 reason code."""

    def __init__(self, reason_code: Step18ReasonCode) -> None:
        if not isinstance(reason_code, Step18ReasonCode):
            raise TypeError("reason_code must be a Step18ReasonCode")
        super().__init__(f"Step 18 retrieval denied: {reason_code.value}")
        self.reason_code = reason_code


_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_DOMAIN_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_IDENTIFIER = re.compile(r"^[^\x00-\x1f\x7f]{1,512}$")
_SECTION = re.compile(r"^[A-Za-z0-9ÄÖÜäöüß§][A-Za-z0-9ÄÖÜäöüß§ .()/-]{0,127}$")
_SCORE = re.compile(r"^(?:0|[1-9][0-9]{0,11})(?:\.[0-9]{1,8})?$")
_SUPPORTED_AUTHORITY = frozenset(
    {
        SourceAuthorityLevel.OFFICIAL_PRIMARY,
        SourceAuthorityLevel.AUTHORITATIVE_SECONDARY,
    }
)


def _nfc_text(value: object, field_name: str, maximum_bytes: int) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ContractValidationError(f"{field_name} must be canonical text")
    normalized = unicodedata.normalize("NFC", value)
    if normalized != value or _CONTROL.search(value):
        raise ContractValidationError(f"{field_name} must be NFC without controls")
    if len(value.encode("utf-8")) > maximum_bytes:
        raise ContractValidationError(f"{field_name} exceeds its byte limit")
    return value


def _nfc_content(value: object) -> str:
    """Validate bounded Step 11-style NFC text without changing its bytes."""

    if not isinstance(value, str) or not value:
        raise ContractValidationError("content must be non-empty text")
    if unicodedata.normalize("NFC", value) != value:
        raise ContractValidationError("content must use Unicode NFC")
    for character in value:
        if unicodedata.category(character) == "Cc" and character not in {
            "\t",
            "\n",
        }:
            raise ContractValidationError("content contains a prohibited control")
    if len(value.encode("utf-8")) > MAXIMUM_CANDIDATE_CONTENT_BYTES:
        raise ContractValidationError("content exceeds its byte limit")
    return value


def _domain_id(value: object, field_name: str) -> str:
    text = _nfc_text(value, field_name, 128)
    if _DOMAIN_ID.fullmatch(text) is None:
        raise ContractValidationError(f"{field_name} is not a logical identifier")
    return text


def _exact_identifier(value: object) -> str:
    text = _nfc_text(value, "exact identifier", 512)
    if _IDENTIFIER.fullmatch(text) is None:
        raise RetrievalBoundaryError(Step18ReasonCode.INVALID_EXACT_IDENTIFIER)
    return text


def _section_identifier(value: object, field_name: str) -> str:
    text = _nfc_text(value, field_name, 256)
    if _SECTION.fullmatch(text) is None:
        raise RetrievalBoundaryError(Step18ReasonCode.INVALID_SECTION_IDENTIFIER)
    return text


def _scope_tuple(value: object, field_name: str) -> tuple[ScopeDimension, ...]:
    if not isinstance(value, (tuple, list)):
        raise ContractValidationError(f"{field_name} must be an ordered scope")
    result = tuple(value)
    if any(not isinstance(item, ScopeDimension) for item in result):
        raise ContractValidationError(f"{field_name} must contain ScopeDimension")
    names = tuple(item.name for item in result)
    if names != tuple(sorted(names)) or len(names) != len(set(names)):
        raise ContractValidationError(f"{field_name} must be sorted and unique")
    return result


def _mapping(value: object, field_name: str, maximum_bytes: int) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or not all(isinstance(k, str) for k in value):
        raise ContractValidationError(f"{field_name} must be a string-keyed object")
    frozen = freeze_json(value)
    if len(canonical_json_bytes(frozen)) > maximum_bytes:
        raise ContractValidationError(f"{field_name} exceeds its byte limit")
    return frozen


def _optional_text(value: object | None, field_name: str, maximum: int = 512) -> str | None:
    return None if value is None else _nfc_text(value, field_name, maximum)


@dataclass(frozen=True, slots=True)
class ExactIdentifierSelector:
    field: ExactIdentifierField
    values: tuple[str, ...]
    selector_hash: str = field(init=False)

    def __post_init__(self) -> None:
        require_enum_member(self.field, ExactIdentifierField, "field")
        if not isinstance(self.values, (tuple, list)):
            raise RetrievalBoundaryError(Step18ReasonCode.INVALID_EXACT_IDENTIFIER)
        values = tuple(sorted(_exact_identifier(item) for item in self.values))
        if not values or len(values) > MAXIMUM_EXACT_IDENTIFIERS:
            raise RetrievalBoundaryError(Step18ReasonCode.INVALID_EXACT_IDENTIFIER)
        if len(values) != len(set(values)):
            raise RetrievalBoundaryError(Step18ReasonCode.INVALID_EXACT_IDENTIFIER)
        object.__setattr__(self, "values", values)
        object.__setattr__(self, "selector_hash", canonical_sha256(self, exclude_fields=("selector_hash",)))


@dataclass(frozen=True, slots=True)
class StatuteSectionSelector:
    statute_identifier: str
    section_identifier: str
    selector_hash: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "statute_identifier", _section_identifier(self.statute_identifier, "statute_identifier"))
        object.__setattr__(self, "section_identifier", _section_identifier(self.section_identifier, "section_identifier"))
        object.__setattr__(self, "selector_hash", canonical_sha256(self, exclude_fields=("selector_hash",)))


@dataclass(frozen=True, slots=True)
class FullTextQuery:
    query_text: str
    query_hash: str = field(init=False)

    def __post_init__(self) -> None:
        try:
            text = _nfc_text(self.query_text, "query_text", MAXIMUM_QUERY_UTF8_BYTES)
        except ContractValidationError as exc:
            raise RetrievalBoundaryError(Step18ReasonCode.QUERY_TOO_LARGE) from exc
        object.__setattr__(self, "query_text", text)
        object.__setattr__(self, "query_hash", canonical_sha256(self, exclude_fields=("query_hash",)))


@dataclass(frozen=True, slots=True)
class KeywordQuery:
    keywords: tuple[str, ...]
    query_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.keywords, (tuple, list)):
            raise RetrievalBoundaryError(Step18ReasonCode.QUERY_TOO_LARGE)
        try:
            words = tuple(sorted(_nfc_text(item, "keyword", 128) for item in self.keywords))
        except ContractValidationError as exc:
            raise RetrievalBoundaryError(Step18ReasonCode.QUERY_TOO_LARGE) from exc
        if not words or len(words) > MAXIMUM_KEYWORDS or len(words) != len(set(words)):
            raise RetrievalBoundaryError(Step18ReasonCode.QUERY_TOO_LARGE)
        if len(" ".join(words).encode("utf-8")) > MAXIMUM_QUERY_UTF8_BYTES:
            raise RetrievalBoundaryError(Step18ReasonCode.QUERY_TOO_LARGE)
        object.__setattr__(self, "keywords", words)
        object.__setattr__(self, "query_hash", canonical_sha256(self, exclude_fields=("query_hash",)))


RetrievalSelector = ExactIdentifierSelector | StatuteSectionSelector | FullTextQuery | KeywordQuery


def selector_hash(selector: RetrievalSelector) -> str:
    if isinstance(selector, ExactIdentifierSelector):
        return selector.selector_hash
    if isinstance(selector, StatuteSectionSelector):
        return selector.selector_hash
    if isinstance(selector, (FullTextQuery, KeywordQuery)):
        return selector.query_hash
    raise ContractValidationError("unsupported retrieval selector")


@dataclass(frozen=True, slots=True)
class RetrievalRequest:
    route: KnowledgeRouteResult
    tenant_id: str
    user_id: str
    request_id: str
    route_hash: str
    selected_hat_id: str | None
    selected_hat_version: str | None
    selected_manifest_digest: str | None
    effective_scope: tuple[ScopeDimension, ...]
    hat_scope_id: str | None
    retrieval_mode: RetrievalMode
    selector: RetrievalSelector
    maximum_results: int = DEFAULT_RESULT_LIMIT
    personal_memory_space_id: str | None = None
    request_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.route, KnowledgeRouteResult):
            raise RetrievalBoundaryError(Step18ReasonCode.ROUTE_HASH_INVALID)
        try:
            verify_route_hash(self.route)
        except (ContractValidationError, IntegrityError) as exc:
            raise RetrievalBoundaryError(Step18ReasonCode.ROUTE_HASH_INVALID) from exc
        for value, name in ((self.tenant_id, "tenant_id"), (self.user_id, "user_id"), (self.request_id, "request_id")):
            _nfc_text(value, name, 255)
        require_sha256_hex(self.route_hash, "route_hash")
        if self.route_hash != self.route.route_hash:
            raise RetrievalBoundaryError(Step18ReasonCode.ROUTE_HASH_INVALID)
        if self.tenant_id != self.route.tenant_id:
            raise RetrievalBoundaryError(Step18ReasonCode.TENANT_MISMATCH)
        if self.user_id != self.route.user_id:
            raise RetrievalBoundaryError(Step18ReasonCode.USER_MISMATCH)
        if self.request_id != self.route.request_id:
            raise RetrievalBoundaryError(Step18ReasonCode.REQUEST_ID_MISMATCH)
        selected = (self.selected_hat_id, self.selected_hat_version, self.selected_manifest_digest)
        expected = (self.route.selected_hat_id, self.route.selected_hat_version, self.route.selected_manifest_digest)
        if selected != expected:
            raise RetrievalBoundaryError(Step18ReasonCode.HAT_IDENTITY_MISMATCH)
        if self.selected_hat_id is not None:
            _domain_id(self.selected_hat_id, "selected_hat_id")
            _nfc_text(self.selected_hat_version, "selected_hat_version", 128)
            require_sha256_hex(self.selected_manifest_digest, "selected_manifest_digest")
        scope = _scope_tuple(self.effective_scope, "effective_scope")
        if scope != self.route.effective_scope:
            raise RetrievalBoundaryError(Step18ReasonCode.ROUTE_SCOPE_MISMATCH)
        object.__setattr__(self, "effective_scope", scope)
        personal_space = _optional_text(
            self.personal_memory_space_id,
            "personal_memory_space_id",
            255,
        )
        scope_by_name = {item.name: item.value for item in scope}
        declared_target = scope_by_name.get("target_scope")
        declared_personal_space = scope_by_name.get("personal_memory_space_id")
        if personal_space is None:
            if declared_personal_space is not None or declared_target not in {
                None,
                MemoryTargetScope.SHARED_KNOWLEDGE_HAT.value,
            }:
                raise RetrievalBoundaryError(Step18ReasonCode.ROUTE_SCOPE_MISMATCH)
        elif (
            declared_personal_space != personal_space
            or declared_target != MemoryTargetScope.USER_PERSONAL_HAT.value
        ):
            raise RetrievalBoundaryError(Step18ReasonCode.ROUTE_SCOPE_MISMATCH)
        object.__setattr__(self, "personal_memory_space_id", personal_space)
        if self.route.knowledge_route in {KnowledgeRoute.HAT_ASSIST, KnowledgeRoute.HAT_ENFORCE}:
            object.__setattr__(self, "hat_scope_id", _domain_id(self.hat_scope_id, "hat_scope_id"))
        elif self.hat_scope_id is not None:
            raise RetrievalBoundaryError(Step18ReasonCode.HAT_SCOPE_MISMATCH)
        require_enum_member(self.retrieval_mode, RetrievalMode, "retrieval_mode")
        expected_selector = {
            RetrievalMode.EXACT_IDENTIFIER: ExactIdentifierSelector,
            RetrievalMode.STATUTE_SECTION: StatuteSectionSelector,
            RetrievalMode.FULL_TEXT: FullTextQuery,
            RetrievalMode.KEYWORD: KeywordQuery,
        }[self.retrieval_mode]
        if not isinstance(self.selector, expected_selector):
            raise ContractValidationError("retrieval mode and selector differ")
        if isinstance(self.maximum_results, bool) or not isinstance(self.maximum_results, int) or self.maximum_results < 1:
            raise RetrievalBoundaryError(Step18ReasonCode.RESULT_LIMIT_EXCEEDED)
        if self.maximum_results > MAXIMUM_RESULT_LIMIT:
            raise RetrievalBoundaryError(Step18ReasonCode.RESULT_LIMIT_EXCEEDED)
        object.__setattr__(self, "request_hash", canonical_sha256(self, exclude_fields=("request_hash",)))


@dataclass(frozen=True, slots=True)
class RetrievalCandidate:
    tenant_id: str
    hat_scope_id: str
    source_id: str
    knowledge_version_id: str
    chunk_id: str
    chunk_ordinal: int
    content_sha256: str
    content: str
    language_tag: str | None
    authority_level: SourceAuthorityLevel
    authority_basis: Mapping[str, Any]
    source_kind: str
    source_reference: str
    publication_state: SourcePublicationState
    access_class: SourceAccessClass
    target_scope: MemoryTargetScope
    owner_user_id: str | None
    personal_memory_space_id: str | None
    scope_digest: str
    registry_digest: str
    artifact_digest: str
    snapshot_id: str
    structured_metadata: Mapping[str, Any]
    effective_scope: tuple[ScopeDimension, ...]
    retrieval_mode: RetrievalMode
    retrieval_score: str | None = None
    candidate_hash: str = field(init=False)

    def __post_init__(self) -> None:
        for value, name in (
            (self.tenant_id, "tenant_id"), (self.hat_scope_id, "hat_scope_id"),
            (self.source_id, "source_id"), (self.knowledge_version_id, "knowledge_version_id"),
            (self.chunk_id, "chunk_id"), (self.source_kind, "source_kind"),
            (self.source_reference, "source_reference"), (self.snapshot_id, "snapshot_id"),
        ):
            _nfc_text(value, name, 1024)
        if isinstance(self.chunk_ordinal, bool) or not isinstance(self.chunk_ordinal, int) or self.chunk_ordinal < 0:
            raise ContractValidationError("chunk_ordinal must be non-negative")
        require_sha256_hex(self.content_sha256, "content_sha256")
        content = _nfc_content(self.content)
        from hashlib import sha256
        if sha256(content.encode("utf-8")).hexdigest() != self.content_sha256:
            raise ContractValidationError("content_sha256 does not match content")
        object.__setattr__(self, "content", content)
        object.__setattr__(self, "language_tag", _optional_text(self.language_tag, "language_tag", 64))
        require_enum_member(self.authority_level, SourceAuthorityLevel, "authority_level")
        if self.authority_level not in _SUPPORTED_AUTHORITY:
            raise RetrievalBoundaryError(Step18ReasonCode.SOURCE_AUTHORITY_REJECTED)
        object.__setattr__(self, "authority_basis", _mapping(self.authority_basis, "authority_basis", 16 * 1024))
        require_enum_member(self.publication_state, SourcePublicationState, "publication_state")
        if self.publication_state is not SourcePublicationState.PUBLISHED:
            raise RetrievalBoundaryError(Step18ReasonCode.SOURCE_NOT_PUBLISHED)
        require_enum_member(self.access_class, SourceAccessClass, "access_class")
        require_enum_member(self.target_scope, MemoryTargetScope, "target_scope")
        object.__setattr__(self, "owner_user_id", _optional_text(self.owner_user_id, "owner_user_id", 255))
        object.__setattr__(self, "personal_memory_space_id", _optional_text(self.personal_memory_space_id, "personal_memory_space_id", 255))
        if self.access_class is SourceAccessClass.USER_PRIVATE:
            if self.owner_user_id is None or self.personal_memory_space_id is None:
                raise RetrievalBoundaryError(Step18ReasonCode.OWNER_SCOPE_REJECTED)
            if self.target_scope is not MemoryTargetScope.USER_PERSONAL_HAT:
                raise RetrievalBoundaryError(Step18ReasonCode.ACCESS_CLASS_REJECTED)
        elif self.owner_user_id is not None or self.personal_memory_space_id is not None:
            raise RetrievalBoundaryError(Step18ReasonCode.ACCESS_CLASS_REJECTED)
        elif self.target_scope is not MemoryTargetScope.SHARED_KNOWLEDGE_HAT:
            raise RetrievalBoundaryError(Step18ReasonCode.ACCESS_CLASS_REJECTED)
        for value, name in ((self.scope_digest, "scope_digest"), (self.registry_digest, "registry_digest"), (self.artifact_digest, "artifact_digest")):
            require_sha256_hex(value, name)
        object.__setattr__(self, "structured_metadata", _mapping(self.structured_metadata, "structured_metadata", 32 * 1024))
        object.__setattr__(self, "effective_scope", _scope_tuple(self.effective_scope, "effective_scope"))
        require_enum_member(self.retrieval_mode, RetrievalMode, "retrieval_mode")
        if self.retrieval_score is not None:
            if not isinstance(self.retrieval_score, str) or _SCORE.fullmatch(self.retrieval_score) is None:
                raise ContractValidationError("retrieval_score must be a canonical decimal string")
            try:
                normalized = format(Decimal(self.retrieval_score).normalize(), "f")
            except InvalidOperation as exc:
                raise ContractValidationError("retrieval_score is invalid") from exc
            object.__setattr__(self, "retrieval_score", normalized)
        object.__setattr__(self, "candidate_hash", canonical_sha256(self, exclude_fields=("candidate_hash",)))


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    request_id: str
    tenant_id: str
    user_id: str
    route_hash: str
    selected_hat_id: str | None
    selected_hat_version: str | None
    selected_manifest_digest: str | None
    hat_scope_id: str | None
    effective_scope: tuple[ScopeDimension, ...]
    retrieval_mode: RetrievalMode
    query_digest: str
    candidates: tuple[RetrievalCandidate, ...]
    truncated: bool
    reason_codes: tuple[Step18ReasonCode, ...]
    candidate_count: int = field(init=False)
    result_hash: str = field(init=False)

    def __post_init__(self) -> None:
        for value, name in ((self.request_id, "request_id"), (self.tenant_id, "tenant_id"), (self.user_id, "user_id")):
            _nfc_text(value, name, 255)
        require_sha256_hex(self.route_hash, "route_hash")
        selected = (
            self.selected_hat_id,
            self.selected_hat_version,
            self.selected_manifest_digest,
            self.hat_scope_id,
        )
        if any(value is not None for value in selected):
            if any(value is None for value in selected):
                raise RetrievalBoundaryError(Step18ReasonCode.HAT_IDENTITY_MISMATCH)
            _domain_id(self.selected_hat_id, "selected_hat_id")
            _nfc_text(self.selected_hat_version, "selected_hat_version", 128)
            require_sha256_hex(self.selected_manifest_digest, "selected_manifest_digest")
            _domain_id(self.hat_scope_id, "hat_scope_id")
        object.__setattr__(self, "effective_scope", _scope_tuple(self.effective_scope, "effective_scope"))
        require_enum_member(self.retrieval_mode, RetrievalMode, "retrieval_mode")
        require_sha256_hex(self.query_digest, "query_digest")
        if not isinstance(self.candidates, (tuple, list)) or any(not isinstance(item, RetrievalCandidate) for item in self.candidates):
            raise ContractValidationError("candidates must be immutable RetrievalCandidate records")
        candidates = tuple(self.candidates)
        if len(candidates) > MAXIMUM_RESULT_LIMIT:
            raise RetrievalBoundaryError(Step18ReasonCode.RESULT_LIMIT_EXCEEDED)
        if sum(len(item.content.encode("utf-8")) for item in candidates) > MAXIMUM_TOTAL_CONTENT_BYTES:
            raise RetrievalBoundaryError(Step18ReasonCode.RESULT_LIMIT_EXCEEDED)
        if len({item.candidate_hash for item in candidates}) != len(candidates):
            raise ContractValidationError("candidate identities must be unique")
        for item in candidates:
            verify_canonical_hash(
                item,
                item.candidate_hash,
                exclude_fields=("candidate_hash",),
            )
            if item.tenant_id != self.tenant_id:
                raise RetrievalBoundaryError(Step18ReasonCode.TENANT_MISMATCH)
            if item.hat_scope_id != self.hat_scope_id:
                raise RetrievalBoundaryError(Step18ReasonCode.HAT_SCOPE_MISMATCH)
            if (
                item.effective_scope != self.effective_scope
                or item.retrieval_mode is not self.retrieval_mode
            ):
                raise RetrievalBoundaryError(Step18ReasonCode.ROUTE_SCOPE_MISMATCH)
        object.__setattr__(self, "candidates", candidates)
        if not isinstance(self.truncated, bool):
            raise ContractValidationError("truncated must be a boolean")
        if not isinstance(self.reason_codes, (tuple, list)):
            raise ContractValidationError("reason_codes must be an ordered tuple")
        reasons = tuple(self.reason_codes)
        if any(not isinstance(item, Step18ReasonCode) for item in reasons) or len(reasons) != len(set(reasons)):
            raise ContractValidationError("reason_codes must be unique Step18ReasonCode values")
        object.__setattr__(self, "reason_codes", reasons)
        object.__setattr__(self, "candidate_count", len(candidates))
        object.__setattr__(self, "result_hash", canonical_sha256(self, exclude_fields=("result_hash",)))


def verify_request_hash(value: RetrievalRequest) -> None:
    verify_canonical_hash(value, value.request_hash, exclude_fields=("request_hash",))


def verify_candidate_hash(value: RetrievalCandidate) -> None:
    verify_canonical_hash(value, value.candidate_hash, exclude_fields=("candidate_hash",))


def verify_result_hash(value: RetrievalResult) -> None:
    verify_canonical_hash(value, value.result_hash, exclude_fields=("result_hash",))


def scope_values(scope: tuple[ScopeDimension, ...]) -> Mapping[str, Any]:
    """Return a frozen name/value projection used only for hard query filters."""

    return freeze_json({item.name: item.value for item in scope})


__all__ = [
    "DEFAULT_RESULT_LIMIT", "ExactIdentifierField", "ExactIdentifierSelector",
    "FullTextQuery", "KeywordQuery", "MAXIMUM_CANDIDATE_CONTENT_BYTES",
    "MAXIMUM_EXACT_IDENTIFIERS", "MAXIMUM_KEYWORDS", "MAXIMUM_QUERY_UTF8_BYTES",
    "MAXIMUM_RESULT_LIMIT", "MAXIMUM_TOTAL_CONTENT_BYTES", "RetrievalBoundaryError",
    "RetrievalCandidate", "RetrievalMode", "RetrievalRequest", "RetrievalResult",
    "STEP18_SCHEMA_VERSION", "StatuteSectionSelector", "Step18ReasonCode",
    "scope_values", "selector_hash", "verify_candidate_hash", "verify_request_hash",
    "verify_result_hash",
]
