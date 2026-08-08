"""Immutable Step 19 embedding and vector-retrieval contracts.

Canonical records contain digests and decimal distances, never native vector
floats.  Embedding vectors have a separate exact IEEE-754 float32 identity.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import struct
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
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


STEP19_SCHEMA_VERSION = "1.0.0"
APPROVED_MODEL_ID = "intfloat/multilingual-e5-small"
APPROVED_MODEL_REVISION = "fd1525a9fd15316a2d503bf26ab031a61d056e98"
APPROVED_EMBEDDING_DIMENSION = 384
APPROVED_MAXIMUM_TOKENS = 512
APPROVED_WEIGHT_FILENAME = "model.safetensors"
APPROVED_WEIGHT_SHA256 = (
    "1a55775f53449dac10a2bcbc312469fac40b96d53198c407081a831f81c98477"
)
APPROVED_MODEL_LICENSE = "MIT"
APPROVED_INPUT_POLICY = "e5-query-passage-prefix-v1"
APPROVED_QUERY_PREFIX = "query: "
APPROVED_PASSAGE_PREFIX = "passage: "
APPROVED_NORMALIZATION = "L2_UNIT"
APPROVED_BACKEND_CONTRACT = "transformers-mean-pooling-v1"
EMBEDDING_BYTES_LENGTH = APPROVED_EMBEDDING_DIMENSION * 4
DEFAULT_BATCH_SIZE = 32
MAXIMUM_BATCH_SIZE = 64
DEFAULT_MAXIMUM_ITEMS_PER_RUN = 1000
HARD_MAXIMUM_ITEMS_PER_RUN = 10000
DEFAULT_VECTOR_RESULT_LIMIT = 20
MAXIMUM_VECTOR_RESULT_LIMIT = 100
MAXIMUM_QUERY_UTF8_BYTES = 4096
MAXIMUM_CANDIDATE_CONTENT_BYTES = 64 * 1024
MAXIMUM_TOTAL_CONTENT_BYTES = 1024 * 1024
MODEL_CONFIG_PATH = (
    Path(__file__).resolve().parents[3]
    / "config"
    / "embeddings"
    / "multilingual-e5-small-step19-1a.json"
)


class Step19ReasonCode(StableStringEnum):
    EMBEDDING_GENERATION_OK = "EMBEDDING_GENERATION_OK"
    VECTOR_RETRIEVAL_OK = "VECTOR_RETRIEVAL_OK"
    VECTOR_MATCH = "VECTOR_MATCH"
    NO_MATCH = "NO_MATCH"
    NO_HAT_SELECTED = "NO_HAT_SELECTED"
    AMBIGUOUS_ROUTE = "AMBIGUOUS_ROUTE"
    ROUTE_HASH_INVALID = "ROUTE_HASH_INVALID"
    ROUTE_SCOPE_MISMATCH = "ROUTE_SCOPE_MISMATCH"
    TENANT_MISMATCH = "TENANT_MISMATCH"
    USER_MISMATCH = "USER_MISMATCH"
    REQUEST_ID_MISMATCH = "REQUEST_ID_MISMATCH"
    HAT_IDENTITY_MISMATCH = "HAT_IDENTITY_MISMATCH"
    HAT_SCOPE_MISMATCH = "HAT_SCOPE_MISMATCH"
    MODEL_IDENTITY_INVALID = "MODEL_IDENTITY_INVALID"
    MODEL_RUNTIME_UNAVAILABLE = "MODEL_RUNTIME_UNAVAILABLE"
    MODEL_WEIGHT_MISMATCH = "MODEL_WEIGHT_MISMATCH"
    EMBEDDING_VECTOR_INVALID = "EMBEDDING_VECTOR_INVALID"
    CACHE_HIT = "CACHE_HIT"
    CACHE_MISS = "CACHE_MISS"
    CACHE_INTEGRITY_INVALID = "CACHE_INTEGRITY_INVALID"
    CACHE_CONFLICT = "CACHE_CONFLICT"
    BATCH_LIMIT_EXCEEDED = "BATCH_LIMIT_EXCEEDED"
    ITEM_LIMIT_EXCEEDED = "ITEM_LIMIT_EXCEEDED"
    QUERY_TOO_LARGE = "QUERY_TOO_LARGE"
    RESULT_LIMIT_EXCEEDED = "RESULT_LIMIT_EXCEEDED"
    SOURCE_NOT_ELIGIBLE = "SOURCE_NOT_ELIGIBLE"
    EMBEDDING_RECORD_CONFLICT = "EMBEDDING_RECORD_CONFLICT"
    DATABASE_ERROR = "DATABASE_ERROR"
    SCHEMA_UNSUPPORTED = "SCHEMA_UNSUPPORTED"


class EmbeddingBoundaryError(RuntimeError):
    """Sanitized fail-closed Step 19 error with a closed reason code."""

    def __init__(self, reason_code: Step19ReasonCode) -> None:
        if not isinstance(reason_code, Step19ReasonCode):
            raise TypeError("reason_code must be a Step19ReasonCode")
        super().__init__(f"Step 19 embedding/vector operation denied: {reason_code.value}")
        self.reason_code = reason_code


_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_DOMAIN_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}/[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_REVISION = re.compile(r"^[0-9a-f]{40}$")
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$")
_CACHE_KEY = re.compile(r"^[0-9a-f]{64}$")
_DISTANCE = re.compile(r"^(?:0|[1-9][0-9]{0,11})(?:\.[0-9]{1,8})?$")
_SUPPORTED_AUTHORITY = frozenset(
    {
        SourceAuthorityLevel.OFFICIAL_PRIMARY,
        SourceAuthorityLevel.AUTHORITATIVE_SECONDARY,
    }
)


def _text(value: object, field_name: str, maximum_bytes: int = 512) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or _CONTROL.search(value)
        or unicodedata.normalize("NFC", value) != value
        or len(value.encode("utf-8")) > maximum_bytes
    ):
        raise ContractValidationError(f"{field_name} must be bounded canonical NFC text")
    return value


def _domain_id(value: object, field_name: str) -> str:
    text = _text(value, field_name, 128)
    if _DOMAIN_ID.fullmatch(text) is None:
        raise ContractValidationError(f"{field_name} must be a logical identifier")
    return text


def _optional_text(value: object | None, field_name: str, maximum: int = 512) -> str | None:
    return None if value is None else _text(value, field_name, maximum)


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


def _content(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ContractValidationError("content must be non-empty text")
    if unicodedata.normalize("NFC", value) != value:
        raise ContractValidationError("content must use Unicode NFC")
    for character in value:
        if unicodedata.category(character) == "Cc" and character not in {"\t", "\n"}:
            raise ContractValidationError("content contains a prohibited control")
    if len(value.encode("utf-8")) > MAXIMUM_CANDIDATE_CONTENT_BYTES:
        raise ContractValidationError("content exceeds its byte limit")
    return value


def _mapping(value: object, field_name: str, maximum_bytes: int) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ContractValidationError(f"{field_name} must be a string-keyed object")
    frozen = freeze_json(value)
    if len(canonical_json_bytes(frozen)) > maximum_bytes:
        raise ContractValidationError(f"{field_name} exceeds its byte limit")
    return frozen


def _route_binding(
    *,
    route: KnowledgeRouteResult,
    tenant_id: str,
    user_id: str,
    request_id: str,
    route_hash: str,
    selected_hat_id: str | None,
    selected_hat_version: str | None,
    selected_manifest_digest: str | None,
    effective_scope: object,
    hat_scope_id: str | None,
    personal_memory_space_id: str | None,
) -> tuple[tuple[ScopeDimension, ...], str | None, str | None]:
    if not isinstance(route, KnowledgeRouteResult):
        raise EmbeddingBoundaryError(Step19ReasonCode.ROUTE_HASH_INVALID)
    try:
        verify_route_hash(route)
    except (ContractValidationError, IntegrityError) as exc:
        raise EmbeddingBoundaryError(Step19ReasonCode.ROUTE_HASH_INVALID) from exc
    for value, name in (
        (tenant_id, "tenant_id"),
        (user_id, "user_id"),
        (request_id, "request_id"),
    ):
        _text(value, name, 255)
    require_sha256_hex(route_hash, "route_hash")
    if route_hash != route.route_hash:
        raise EmbeddingBoundaryError(Step19ReasonCode.ROUTE_HASH_INVALID)
    if tenant_id != route.tenant_id:
        raise EmbeddingBoundaryError(Step19ReasonCode.TENANT_MISMATCH)
    if user_id != route.user_id:
        raise EmbeddingBoundaryError(Step19ReasonCode.USER_MISMATCH)
    if request_id != route.request_id:
        raise EmbeddingBoundaryError(Step19ReasonCode.REQUEST_ID_MISMATCH)
    selected = (selected_hat_id, selected_hat_version, selected_manifest_digest)
    expected = (
        route.selected_hat_id,
        route.selected_hat_version,
        route.selected_manifest_digest,
    )
    if selected != expected:
        raise EmbeddingBoundaryError(Step19ReasonCode.HAT_IDENTITY_MISMATCH)
    if selected_hat_id is not None:
        _domain_id(selected_hat_id, "selected_hat_id")
        _text(selected_hat_version, "selected_hat_version", 128)
        require_sha256_hex(selected_manifest_digest, "selected_manifest_digest")
    scope = _scope_tuple(effective_scope, "effective_scope")
    if scope != route.effective_scope:
        raise EmbeddingBoundaryError(Step19ReasonCode.ROUTE_SCOPE_MISMATCH)
    personal_space = _optional_text(
        personal_memory_space_id, "personal_memory_space_id", 255
    )
    scope_by_name = {item.name: item.value for item in scope}
    target = scope_by_name.get("target_scope")
    declared_space = scope_by_name.get("personal_memory_space_id")
    if personal_space is None:
        if declared_space is not None or target not in {
            None,
            MemoryTargetScope.SHARED_KNOWLEDGE_HAT.value,
        }:
            raise EmbeddingBoundaryError(Step19ReasonCode.ROUTE_SCOPE_MISMATCH)
    elif (
        declared_space != personal_space
        or target != MemoryTargetScope.USER_PERSONAL_HAT.value
    ):
        raise EmbeddingBoundaryError(Step19ReasonCode.ROUTE_SCOPE_MISMATCH)
    if route.knowledge_route in {KnowledgeRoute.HAT_ASSIST, KnowledgeRoute.HAT_ENFORCE}:
        hat_scope = _domain_id(hat_scope_id, "hat_scope_id")
    elif hat_scope_id is not None:
        raise EmbeddingBoundaryError(Step19ReasonCode.HAT_SCOPE_MISMATCH)
    else:
        hat_scope = None
    return scope, hat_scope, personal_space


@dataclass(frozen=True, slots=True)
class EmbeddingModelSpec:
    schema_version: str
    model_id: str
    model_revision: str
    model_family: str
    embedding_dimension: int
    maximum_tokens: int
    query_prefix: str
    passage_prefix: str
    normalization: str
    weight_filename: str
    weight_sha256: str
    license: str
    inference_backend: str
    input_policy_version: str
    backend_contract_version: str
    model_digest: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != STEP19_SCHEMA_VERSION:
            raise EmbeddingBoundaryError(Step19ReasonCode.MODEL_IDENTITY_INVALID)
        if not isinstance(self.model_id, str) or _MODEL_ID.fullmatch(self.model_id) is None:
            raise EmbeddingBoundaryError(Step19ReasonCode.MODEL_IDENTITY_INVALID)
        if not isinstance(self.model_revision, str) or _REVISION.fullmatch(self.model_revision) is None:
            raise EmbeddingBoundaryError(Step19ReasonCode.MODEL_IDENTITY_INVALID)
        for value, name in (
            (self.model_family, "model_family"),
            (self.inference_backend, "inference_backend"),
            (self.input_policy_version, "input_policy_version"),
            (self.backend_contract_version, "backend_contract_version"),
        ):
            _domain_id(value, name)
        if (
            isinstance(self.embedding_dimension, bool)
            or not isinstance(self.embedding_dimension, int)
            or self.embedding_dimension != APPROVED_EMBEDDING_DIMENSION
            or isinstance(self.maximum_tokens, bool)
            or not isinstance(self.maximum_tokens, int)
            or self.maximum_tokens != APPROVED_MAXIMUM_TOKENS
        ):
            raise EmbeddingBoundaryError(Step19ReasonCode.MODEL_IDENTITY_INVALID)
        if not isinstance(self.query_prefix, str) or not self.query_prefix.endswith(" "):
            raise EmbeddingBoundaryError(Step19ReasonCode.MODEL_IDENTITY_INVALID)
        if not isinstance(self.passage_prefix, str) or not self.passage_prefix.endswith(" "):
            raise EmbeddingBoundaryError(Step19ReasonCode.MODEL_IDENTITY_INVALID)
        _text(self.query_prefix[:-1], "query_prefix", 64)
        _text(self.passage_prefix[:-1], "passage_prefix", 64)
        _domain_id(self.normalization.casefold().replace("_", "-"), "normalization")
        if self.weight_filename != APPROVED_WEIGHT_FILENAME:
            raise EmbeddingBoundaryError(Step19ReasonCode.MODEL_IDENTITY_INVALID)
        require_sha256_hex(self.weight_sha256, "weight_sha256")
        _text(self.license, "license", 64)
        object.__setattr__(
            self,
            "model_digest",
            canonical_sha256(self, exclude_fields=("model_digest",)),
        )


_MODEL_CONFIG_FIELDS = {
    "schema_version",
    "model_id",
    "model_revision",
    "model_family",
    "embedding_dimension",
    "maximum_tokens",
    "query_prefix",
    "passage_prefix",
    "normalization",
    "weight_filename",
    "weight_sha256",
    "license",
    "inference_backend",
    "input_policy_version",
    "backend_contract_version",
    "config_digest",
}


def _strict_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise EmbeddingBoundaryError(Step19ReasonCode.MODEL_IDENTITY_INVALID)
        result[key] = value
    return result


def load_approved_model_spec() -> EmbeddingModelSpec:
    """Load the one checked-in Step 19 model; callers cannot select another."""

    try:
        payload = MODEL_CONFIG_PATH.read_bytes()
        if len(payload) > 32 * 1024:
            raise ValueError("model config exceeds bound")
        decoded = json.loads(payload.decode("utf-8"), object_pairs_hook=_strict_pairs)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise EmbeddingBoundaryError(Step19ReasonCode.MODEL_IDENTITY_INVALID) from exc
    if not isinstance(decoded, dict) or set(decoded) != _MODEL_CONFIG_FIELDS:
        raise EmbeddingBoundaryError(Step19ReasonCode.MODEL_IDENTITY_INVALID)
    claimed = decoded.pop("config_digest")
    try:
        require_sha256_hex(claimed, "config_digest")
        spec = EmbeddingModelSpec(**decoded)
    except (TypeError, ContractValidationError) as exc:
        raise EmbeddingBoundaryError(Step19ReasonCode.MODEL_IDENTITY_INVALID) from exc
    if claimed != spec.model_digest:
        raise EmbeddingBoundaryError(Step19ReasonCode.MODEL_IDENTITY_INVALID)
    exact = (
        spec.model_id == APPROVED_MODEL_ID
        and spec.model_revision == APPROVED_MODEL_REVISION
        and spec.embedding_dimension == APPROVED_EMBEDDING_DIMENSION
        and spec.maximum_tokens == APPROVED_MAXIMUM_TOKENS
        and spec.weight_filename == APPROVED_WEIGHT_FILENAME
        and spec.weight_sha256 == APPROVED_WEIGHT_SHA256
        and spec.license == APPROVED_MODEL_LICENSE
        and spec.input_policy_version == APPROVED_INPUT_POLICY
        and spec.query_prefix == APPROVED_QUERY_PREFIX
        and spec.passage_prefix == APPROVED_PASSAGE_PREFIX
        and spec.normalization == APPROVED_NORMALIZATION
        and spec.backend_contract_version == APPROVED_BACKEND_CONTRACT
    )
    if not exact:
        raise EmbeddingBoundaryError(Step19ReasonCode.MODEL_IDENTITY_INVALID)
    return spec


@dataclass(frozen=True, slots=True)
class EmbeddingVector:
    """Exact normalized float32 vector; never embedded in canonical JSON."""

    values: tuple[float, ...]
    float32_bytes: bytes = field(init=False, repr=False)
    bytes_sha256: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.values, (tuple, list)) or len(self.values) != APPROVED_EMBEDDING_DIMENSION:
            raise EmbeddingBoundaryError(Step19ReasonCode.EMBEDDING_VECTOR_INVALID)
        values: list[float] = []
        for item in self.values:
            if isinstance(item, bool) or not isinstance(item, (int, float)):
                raise EmbeddingBoundaryError(Step19ReasonCode.EMBEDDING_VECTOR_INVALID)
            value = float(item)
            if not math.isfinite(value):
                raise EmbeddingBoundaryError(Step19ReasonCode.EMBEDDING_VECTOR_INVALID)
            values.append(struct.unpack("<f", struct.pack("<f", value))[0])
        norm = math.sqrt(math.fsum(item * item for item in values))
        if not math.isfinite(norm) or abs(norm - 1.0) > 1e-5:
            raise EmbeddingBoundaryError(Step19ReasonCode.EMBEDDING_VECTOR_INVALID)
        payload = struct.pack(f"<{APPROVED_EMBEDDING_DIMENSION}f", *values)
        if len(payload) != EMBEDDING_BYTES_LENGTH:
            raise AssertionError("float32 embedding byte length differs")
        object.__setattr__(self, "values", tuple(values))
        object.__setattr__(self, "float32_bytes", payload)
        object.__setattr__(self, "bytes_sha256", hashlib.sha256(payload).hexdigest())


def normalize_embedding_vector(values: Sequence[float]) -> EmbeddingVector:
    if len(values) != APPROVED_EMBEDDING_DIMENSION:
        raise EmbeddingBoundaryError(Step19ReasonCode.EMBEDDING_VECTOR_INVALID)
    converted: list[float] = []
    for item in values:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise EmbeddingBoundaryError(Step19ReasonCode.EMBEDDING_VECTOR_INVALID)
        value = float(item)
        if not math.isfinite(value):
            raise EmbeddingBoundaryError(Step19ReasonCode.EMBEDDING_VECTOR_INVALID)
        converted.append(value)
    norm = math.sqrt(math.fsum(item * item for item in converted))
    if not math.isfinite(norm) or norm <= 0:
        raise EmbeddingBoundaryError(Step19ReasonCode.EMBEDDING_VECTOR_INVALID)
    return EmbeddingVector(tuple(value / norm for value in converted))


def vector_from_float32_bytes(payload: bytes) -> EmbeddingVector:
    if not isinstance(payload, bytes) or len(payload) != EMBEDDING_BYTES_LENGTH:
        raise EmbeddingBoundaryError(Step19ReasonCode.CACHE_INTEGRITY_INVALID)
    values = struct.unpack(f"<{APPROVED_EMBEDDING_DIMENSION}f", payload)
    return EmbeddingVector(values)


def vector_sql_literal(vector: EmbeddingVector) -> str:
    if not isinstance(vector, EmbeddingVector):
        raise EmbeddingBoundaryError(Step19ReasonCode.EMBEDDING_VECTOR_INVALID)
    return "[" + ",".join(format(value, ".9g") for value in vector.values) + "]"


def prepare_passage(content: str, spec: EmbeddingModelSpec) -> str:
    return spec.passage_prefix + _content(content)


def prepare_query(query_text: str, spec: EmbeddingModelSpec) -> str:
    text = _text(query_text, "query_text", MAXIMUM_QUERY_UTF8_BYTES)
    return spec.query_prefix + text


def passage_cache_key(
    *,
    model_digest: str,
    content_sha256: str,
    prepared_passage_sha256: str,
    input_policy_version: str,
) -> str:
    for value, name in (
        (model_digest, "model_digest"),
        (content_sha256, "content_sha256"),
        (prepared_passage_sha256, "prepared_passage_sha256"),
    ):
        require_sha256_hex(value, name)
    _domain_id(input_policy_version, "input_policy_version")
    return canonical_sha256(
        {
            "model_digest": model_digest,
            "content_sha256": content_sha256,
            "prepared_passage_sha256": prepared_passage_sha256,
            "input_policy_version": input_policy_version,
        }
    )


@dataclass(frozen=True, slots=True)
class EmbeddingGenerationRequest:
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
    model_digest: str
    batch_size: int = DEFAULT_BATCH_SIZE
    maximum_items: int = DEFAULT_MAXIMUM_ITEMS_PER_RUN
    personal_memory_space_id: str | None = None
    request_hash: str = field(init=False)

    def __post_init__(self) -> None:
        scope, hat_scope, personal_space = _route_binding(
            route=self.route,
            tenant_id=self.tenant_id,
            user_id=self.user_id,
            request_id=self.request_id,
            route_hash=self.route_hash,
            selected_hat_id=self.selected_hat_id,
            selected_hat_version=self.selected_hat_version,
            selected_manifest_digest=self.selected_manifest_digest,
            effective_scope=self.effective_scope,
            hat_scope_id=self.hat_scope_id,
            personal_memory_space_id=self.personal_memory_space_id,
        )
        object.__setattr__(self, "effective_scope", scope)
        object.__setattr__(self, "hat_scope_id", hat_scope)
        object.__setattr__(self, "personal_memory_space_id", personal_space)
        require_sha256_hex(self.model_digest, "model_digest")
        if self.model_digest != load_approved_model_spec().model_digest:
            raise EmbeddingBoundaryError(Step19ReasonCode.MODEL_IDENTITY_INVALID)
        if (
            isinstance(self.batch_size, bool)
            or not isinstance(self.batch_size, int)
            or not 1 <= self.batch_size <= MAXIMUM_BATCH_SIZE
        ):
            raise EmbeddingBoundaryError(Step19ReasonCode.BATCH_LIMIT_EXCEEDED)
        if (
            isinstance(self.maximum_items, bool)
            or not isinstance(self.maximum_items, int)
            or not 1 <= self.maximum_items <= HARD_MAXIMUM_ITEMS_PER_RUN
        ):
            raise EmbeddingBoundaryError(Step19ReasonCode.ITEM_LIMIT_EXCEEDED)
        object.__setattr__(self, "request_hash", canonical_sha256(self, exclude_fields=("request_hash",)))


@dataclass(frozen=True, slots=True)
class EmbeddingSource:
    tenant_id: str
    hat_scope_id: str
    source_id: str
    knowledge_version_id: str
    chunk_id: str
    chunk_ordinal: int
    content: str
    content_sha256: str
    version_ordinal: int
    source_scope_digest: str
    source_registry_digest: str
    source_artifact_digest: str
    effective_scope: tuple[ScopeDimension, ...]
    source_hash: str = field(init=False)

    def __post_init__(self) -> None:
        for value, name in (
            (self.tenant_id, "tenant_id"),
            (self.hat_scope_id, "hat_scope_id"),
            (self.source_id, "source_id"),
            (self.knowledge_version_id, "knowledge_version_id"),
            (self.chunk_id, "chunk_id"),
        ):
            _text(value, name, 255)
        for value, name in (
            (self.chunk_ordinal, "chunk_ordinal"),
            (self.version_ordinal, "version_ordinal"),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ContractValidationError(f"{name} must be non-negative")
        content = _content(self.content)
        require_sha256_hex(self.content_sha256, "content_sha256")
        if hashlib.sha256(content.encode("utf-8")).hexdigest() != self.content_sha256:
            raise EmbeddingBoundaryError(Step19ReasonCode.SOURCE_NOT_ELIGIBLE)
        object.__setattr__(self, "content", content)
        for value, name in (
            (self.source_scope_digest, "source_scope_digest"),
            (self.source_registry_digest, "source_registry_digest"),
            (self.source_artifact_digest, "source_artifact_digest"),
        ):
            require_sha256_hex(value, name)
        object.__setattr__(self, "effective_scope", _scope_tuple(self.effective_scope, "effective_scope"))
        object.__setattr__(self, "source_hash", canonical_sha256(self, exclude_fields=("source_hash",)))


@dataclass(frozen=True, slots=True)
class EmbeddingRecord:
    tenant_id: str
    hat_scope_id: str
    source_id: str
    knowledge_version_id: str
    chunk_id: str
    content_sha256: str
    model_id: str
    model_revision: str
    model_digest: str
    embedding_dimension: int
    embedding_input_digest: str
    embedding_bytes_sha256: str
    cache_key: str
    generation_backend: str
    generation_backend_version: str
    generation_backend_fingerprint: str
    truncated: bool
    record_hash: str = field(init=False)

    def __post_init__(self) -> None:
        for value, name in (
            (self.tenant_id, "tenant_id"),
            (self.hat_scope_id, "hat_scope_id"),
            (self.source_id, "source_id"),
            (self.knowledge_version_id, "knowledge_version_id"),
            (self.chunk_id, "chunk_id"),
        ):
            _text(value, name, 255)
        for value, name in (
            (self.content_sha256, "content_sha256"),
            (self.model_digest, "model_digest"),
            (self.embedding_input_digest, "embedding_input_digest"),
            (self.embedding_bytes_sha256, "embedding_bytes_sha256"),
            (self.generation_backend_fingerprint, "generation_backend_fingerprint"),
        ):
            require_sha256_hex(value, name)
        spec = load_approved_model_spec()
        if (
            self.model_id != spec.model_id
            or self.model_revision != spec.model_revision
            or self.model_digest != spec.model_digest
            or self.embedding_dimension != spec.embedding_dimension
        ):
            raise EmbeddingBoundaryError(Step19ReasonCode.MODEL_IDENTITY_INVALID)
        if not isinstance(self.cache_key, str) or _CACHE_KEY.fullmatch(self.cache_key) is None:
            raise EmbeddingBoundaryError(Step19ReasonCode.CACHE_INTEGRITY_INVALID)
        _domain_id(self.generation_backend, "generation_backend")
        if not isinstance(self.generation_backend_version, str) or _VERSION.fullmatch(self.generation_backend_version) is None:
            raise EmbeddingBoundaryError(Step19ReasonCode.MODEL_RUNTIME_UNAVAILABLE)
        if not isinstance(self.truncated, bool):
            raise ContractValidationError("truncated must be boolean")
        object.__setattr__(self, "record_hash", canonical_sha256(self, exclude_fields=("record_hash",)))


@dataclass(frozen=True, slots=True)
class EmbeddingGenerationResult:
    request_id: str
    tenant_id: str
    route_hash: str
    model_digest: str
    records: tuple[EmbeddingRecord, ...]
    cache_hits: int
    generated_count: int
    truncated: bool
    reason_codes: tuple[Step19ReasonCode, ...]
    result_hash: str = field(init=False)

    def __post_init__(self) -> None:
        _text(self.request_id, "request_id", 255)
        _text(self.tenant_id, "tenant_id", 255)
        require_sha256_hex(self.route_hash, "route_hash")
        require_sha256_hex(self.model_digest, "model_digest")
        if not isinstance(self.records, (tuple, list)) or any(not isinstance(item, EmbeddingRecord) for item in self.records):
            raise ContractValidationError("records must be immutable EmbeddingRecord values")
        records = tuple(self.records)
        if len(records) > HARD_MAXIMUM_ITEMS_PER_RUN:
            raise EmbeddingBoundaryError(Step19ReasonCode.ITEM_LIMIT_EXCEEDED)
        if len({item.record_hash for item in records}) != len(records):
            raise ContractValidationError("embedding records must be unique")
        for item in records:
            verify_embedding_record_hash(item)
            if item.tenant_id != self.tenant_id or item.model_digest != self.model_digest:
                raise EmbeddingBoundaryError(Step19ReasonCode.EMBEDDING_RECORD_CONFLICT)
        object.__setattr__(self, "records", records)
        for value, name in ((self.cache_hits, "cache_hits"), (self.generated_count, "generated_count")):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ContractValidationError(f"{name} must be non-negative")
        if self.cache_hits + self.generated_count != len(records):
            raise ContractValidationError("generation counts differ from records")
        if not isinstance(self.truncated, bool):
            raise ContractValidationError("truncated must be boolean")
        if not isinstance(self.reason_codes, (tuple, list)):
            raise ContractValidationError("reason_codes must be an ordered tuple")
        reasons = tuple(self.reason_codes)
        if any(not isinstance(item, Step19ReasonCode) for item in reasons) or len(reasons) != len(set(reasons)):
            raise ContractValidationError("reason_codes must be unique Step19ReasonCode values")
        object.__setattr__(self, "reason_codes", reasons)
        object.__setattr__(self, "result_hash", canonical_sha256(self, exclude_fields=("result_hash",)))


@dataclass(frozen=True, slots=True)
class VectorRetrievalRequest:
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
    query_text: str
    model_digest: str
    maximum_results: int = DEFAULT_VECTOR_RESULT_LIMIT
    personal_memory_space_id: str | None = None
    query_digest: str = field(init=False)
    request_hash: str = field(init=False)

    def __post_init__(self) -> None:
        scope, hat_scope, personal_space = _route_binding(
            route=self.route,
            tenant_id=self.tenant_id,
            user_id=self.user_id,
            request_id=self.request_id,
            route_hash=self.route_hash,
            selected_hat_id=self.selected_hat_id,
            selected_hat_version=self.selected_hat_version,
            selected_manifest_digest=self.selected_manifest_digest,
            effective_scope=self.effective_scope,
            hat_scope_id=self.hat_scope_id,
            personal_memory_space_id=self.personal_memory_space_id,
        )
        object.__setattr__(self, "effective_scope", scope)
        object.__setattr__(self, "hat_scope_id", hat_scope)
        object.__setattr__(self, "personal_memory_space_id", personal_space)
        try:
            query = _text(self.query_text, "query_text", MAXIMUM_QUERY_UTF8_BYTES)
        except ContractValidationError as exc:
            raise EmbeddingBoundaryError(Step19ReasonCode.QUERY_TOO_LARGE) from exc
        object.__setattr__(self, "query_text", query)
        require_sha256_hex(self.model_digest, "model_digest")
        if self.model_digest != load_approved_model_spec().model_digest:
            raise EmbeddingBoundaryError(Step19ReasonCode.MODEL_IDENTITY_INVALID)
        if (
            isinstance(self.maximum_results, bool)
            or not isinstance(self.maximum_results, int)
            or not 1 <= self.maximum_results <= MAXIMUM_VECTOR_RESULT_LIMIT
        ):
            raise EmbeddingBoundaryError(Step19ReasonCode.RESULT_LIMIT_EXCEEDED)
        object.__setattr__(self, "query_digest", hashlib.sha256(query.encode("utf-8")).hexdigest())
        object.__setattr__(self, "request_hash", canonical_sha256(self, exclude_fields=("request_hash",)))


@dataclass(frozen=True, slots=True)
class VectorRetrievalCandidate:
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
    model_digest: str
    embedding_bytes_sha256: str
    vector_distance: str
    candidate_hash: str = field(init=False)

    def __post_init__(self) -> None:
        for value, name in (
            (self.tenant_id, "tenant_id"),
            (self.hat_scope_id, "hat_scope_id"),
            (self.source_id, "source_id"),
            (self.knowledge_version_id, "knowledge_version_id"),
            (self.chunk_id, "chunk_id"),
            (self.source_kind, "source_kind"),
            (self.source_reference, "source_reference"),
            (self.snapshot_id, "snapshot_id"),
        ):
            _text(value, name, 1024)
        if isinstance(self.chunk_ordinal, bool) or not isinstance(self.chunk_ordinal, int) or self.chunk_ordinal < 0:
            raise ContractValidationError("chunk_ordinal must be non-negative")
        content = _content(self.content)
        require_sha256_hex(self.content_sha256, "content_sha256")
        if hashlib.sha256(content.encode("utf-8")).hexdigest() != self.content_sha256:
            raise EmbeddingBoundaryError(Step19ReasonCode.SOURCE_NOT_ELIGIBLE)
        object.__setattr__(self, "content", content)
        object.__setattr__(self, "language_tag", _optional_text(self.language_tag, "language_tag", 64))
        require_enum_member(self.authority_level, SourceAuthorityLevel, "authority_level")
        if self.authority_level not in _SUPPORTED_AUTHORITY:
            raise EmbeddingBoundaryError(Step19ReasonCode.SOURCE_NOT_ELIGIBLE)
        object.__setattr__(self, "authority_basis", _mapping(self.authority_basis, "authority_basis", 16 * 1024))
        require_enum_member(self.publication_state, SourcePublicationState, "publication_state")
        if self.publication_state is not SourcePublicationState.PUBLISHED:
            raise EmbeddingBoundaryError(Step19ReasonCode.SOURCE_NOT_ELIGIBLE)
        require_enum_member(self.access_class, SourceAccessClass, "access_class")
        require_enum_member(self.target_scope, MemoryTargetScope, "target_scope")
        object.__setattr__(self, "owner_user_id", _optional_text(self.owner_user_id, "owner_user_id", 255))
        object.__setattr__(self, "personal_memory_space_id", _optional_text(self.personal_memory_space_id, "personal_memory_space_id", 255))
        if self.access_class is SourceAccessClass.USER_PRIVATE:
            if self.owner_user_id is None or self.personal_memory_space_id is None or self.target_scope is not MemoryTargetScope.USER_PERSONAL_HAT:
                raise EmbeddingBoundaryError(Step19ReasonCode.SOURCE_NOT_ELIGIBLE)
        elif self.owner_user_id is not None or self.personal_memory_space_id is not None or self.target_scope is not MemoryTargetScope.SHARED_KNOWLEDGE_HAT:
            raise EmbeddingBoundaryError(Step19ReasonCode.SOURCE_NOT_ELIGIBLE)
        for value, name in (
            (self.scope_digest, "scope_digest"),
            (self.registry_digest, "registry_digest"),
            (self.artifact_digest, "artifact_digest"),
            (self.model_digest, "model_digest"),
            (self.embedding_bytes_sha256, "embedding_bytes_sha256"),
        ):
            require_sha256_hex(value, name)
        if self.model_digest != load_approved_model_spec().model_digest:
            raise EmbeddingBoundaryError(Step19ReasonCode.MODEL_IDENTITY_INVALID)
        object.__setattr__(self, "structured_metadata", _mapping(self.structured_metadata, "structured_metadata", 32 * 1024))
        object.__setattr__(self, "effective_scope", _scope_tuple(self.effective_scope, "effective_scope"))
        if not isinstance(self.vector_distance, str) or _DISTANCE.fullmatch(self.vector_distance) is None:
            raise EmbeddingBoundaryError(Step19ReasonCode.EMBEDDING_VECTOR_INVALID)
        try:
            distance = Decimal(self.vector_distance)
        except InvalidOperation as exc:
            raise EmbeddingBoundaryError(Step19ReasonCode.EMBEDDING_VECTOR_INVALID) from exc
        if not distance.is_finite() or distance < 0:
            raise EmbeddingBoundaryError(Step19ReasonCode.EMBEDDING_VECTOR_INVALID)
        normalized = format(distance.normalize(), "f")
        object.__setattr__(self, "vector_distance", "0" if normalized in {"-0", "0E+8"} else normalized)
        object.__setattr__(self, "candidate_hash", canonical_sha256(self, exclude_fields=("candidate_hash",)))


@dataclass(frozen=True, slots=True)
class VectorRetrievalResult:
    request_id: str
    tenant_id: str
    user_id: str
    route_hash: str
    selected_hat_id: str | None
    selected_hat_version: str | None
    selected_manifest_digest: str | None
    hat_scope_id: str | None
    effective_scope: tuple[ScopeDimension, ...]
    model_digest: str
    query_digest: str
    candidates: tuple[VectorRetrievalCandidate, ...]
    truncated: bool
    reason_codes: tuple[Step19ReasonCode, ...]
    candidate_count: int = field(init=False)
    result_hash: str = field(init=False)

    def __post_init__(self) -> None:
        for value, name in ((self.request_id, "request_id"), (self.tenant_id, "tenant_id"), (self.user_id, "user_id")):
            _text(value, name, 255)
        require_sha256_hex(self.route_hash, "route_hash")
        require_sha256_hex(self.model_digest, "model_digest")
        require_sha256_hex(self.query_digest, "query_digest")
        selected = (self.selected_hat_id, self.selected_hat_version, self.selected_manifest_digest, self.hat_scope_id)
        if any(value is not None for value in selected):
            if any(value is None for value in selected):
                raise EmbeddingBoundaryError(Step19ReasonCode.HAT_IDENTITY_MISMATCH)
            _domain_id(self.selected_hat_id, "selected_hat_id")
            _text(self.selected_hat_version, "selected_hat_version", 128)
            require_sha256_hex(self.selected_manifest_digest, "selected_manifest_digest")
            _domain_id(self.hat_scope_id, "hat_scope_id")
        object.__setattr__(self, "effective_scope", _scope_tuple(self.effective_scope, "effective_scope"))
        if not isinstance(self.candidates, (tuple, list)) or any(not isinstance(item, VectorRetrievalCandidate) for item in self.candidates):
            raise ContractValidationError("candidates must be immutable VectorRetrievalCandidate records")
        candidates = tuple(self.candidates)
        if len(candidates) > MAXIMUM_VECTOR_RESULT_LIMIT or sum(len(item.content.encode("utf-8")) for item in candidates) > MAXIMUM_TOTAL_CONTENT_BYTES:
            raise EmbeddingBoundaryError(Step19ReasonCode.RESULT_LIMIT_EXCEEDED)
        if len({item.candidate_hash for item in candidates}) != len(candidates):
            raise ContractValidationError("candidate identities must be unique")
        for item in candidates:
            verify_vector_candidate_hash(item)
            if item.tenant_id != self.tenant_id:
                raise EmbeddingBoundaryError(Step19ReasonCode.TENANT_MISMATCH)
            if item.hat_scope_id != self.hat_scope_id:
                raise EmbeddingBoundaryError(Step19ReasonCode.HAT_SCOPE_MISMATCH)
            if item.effective_scope != self.effective_scope:
                raise EmbeddingBoundaryError(Step19ReasonCode.ROUTE_SCOPE_MISMATCH)
            if item.model_digest != self.model_digest:
                raise EmbeddingBoundaryError(Step19ReasonCode.MODEL_IDENTITY_INVALID)
        object.__setattr__(self, "candidates", candidates)
        if not isinstance(self.truncated, bool):
            raise ContractValidationError("truncated must be boolean")
        if not isinstance(self.reason_codes, (tuple, list)):
            raise ContractValidationError("reason_codes must be ordered")
        reasons = tuple(self.reason_codes)
        if any(not isinstance(item, Step19ReasonCode) for item in reasons) or len(reasons) != len(set(reasons)):
            raise ContractValidationError("reason_codes must be unique Step19ReasonCode values")
        object.__setattr__(self, "reason_codes", reasons)
        object.__setattr__(self, "candidate_count", len(candidates))
        object.__setattr__(self, "result_hash", canonical_sha256(self, exclude_fields=("result_hash",)))


def verify_generation_request_hash(value: EmbeddingGenerationRequest) -> None:
    verify_canonical_hash(value, value.request_hash, exclude_fields=("request_hash",))


def verify_embedding_source_hash(value: EmbeddingSource) -> None:
    verify_canonical_hash(value, value.source_hash, exclude_fields=("source_hash",))


def verify_embedding_record_hash(value: EmbeddingRecord) -> None:
    verify_canonical_hash(value, value.record_hash, exclude_fields=("record_hash",))


def verify_generation_result_hash(value: EmbeddingGenerationResult) -> None:
    verify_canonical_hash(value, value.result_hash, exclude_fields=("result_hash",))


def verify_vector_request_hash(value: VectorRetrievalRequest) -> None:
    verify_canonical_hash(value, value.request_hash, exclude_fields=("request_hash",))


def verify_vector_candidate_hash(value: VectorRetrievalCandidate) -> None:
    verify_canonical_hash(value, value.candidate_hash, exclude_fields=("candidate_hash",))


def verify_vector_result_hash(value: VectorRetrievalResult) -> None:
    verify_canonical_hash(value, value.result_hash, exclude_fields=("result_hash",))


__all__ = [
    "APPROVED_BACKEND_CONTRACT", "APPROVED_EMBEDDING_DIMENSION",
    "APPROVED_INPUT_POLICY", "APPROVED_MAXIMUM_TOKENS", "APPROVED_MODEL_ID",
    "APPROVED_MODEL_LICENSE", "APPROVED_MODEL_REVISION", "APPROVED_NORMALIZATION",
    "APPROVED_PASSAGE_PREFIX", "APPROVED_QUERY_PREFIX", "APPROVED_WEIGHT_FILENAME",
    "APPROVED_WEIGHT_SHA256", "DEFAULT_BATCH_SIZE", "DEFAULT_MAXIMUM_ITEMS_PER_RUN",
    "DEFAULT_VECTOR_RESULT_LIMIT", "EMBEDDING_BYTES_LENGTH", "EmbeddingBoundaryError",
    "EmbeddingGenerationRequest", "EmbeddingGenerationResult", "EmbeddingModelSpec",
    "EmbeddingRecord", "EmbeddingSource", "EmbeddingVector", "HARD_MAXIMUM_ITEMS_PER_RUN",
    "MAXIMUM_BATCH_SIZE", "MAXIMUM_QUERY_UTF8_BYTES", "MAXIMUM_VECTOR_RESULT_LIMIT",
    "MODEL_CONFIG_PATH", "STEP19_SCHEMA_VERSION", "Step19ReasonCode",
    "VectorRetrievalCandidate", "VectorRetrievalRequest", "VectorRetrievalResult",
    "load_approved_model_spec", "normalize_embedding_vector", "passage_cache_key",
    "prepare_passage", "prepare_query", "vector_from_float32_bytes", "vector_sql_literal",
    "verify_embedding_record_hash", "verify_embedding_source_hash",
    "verify_generation_request_hash", "verify_generation_result_hash",
    "verify_vector_candidate_hash", "verify_vector_request_hash", "verify_vector_result_hash",
]
