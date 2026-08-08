"""Small local-only embedding backend boundary for Step 19."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from aioa_memory_kernel.contracts.serialization import canonical_sha256, require_sha256_hex

from .models import EmbeddingModelSpec, EmbeddingVector


@dataclass(frozen=True, slots=True)
class EmbeddingBackendIdentity:
    backend_name: str
    backend_version: str
    backend_fingerprint: str
    model_digest: str

    def __post_init__(self) -> None:
        if not isinstance(self.backend_name, str) or not self.backend_name:
            raise TypeError("backend_name must be non-empty")
        if not isinstance(self.backend_version, str) or not self.backend_version:
            raise TypeError("backend_version must be non-empty")
        require_sha256_hex(self.backend_fingerprint, "backend_fingerprint")
        require_sha256_hex(self.model_digest, "model_digest")

    @classmethod
    def create(
        cls,
        *,
        backend_name: str,
        backend_version: str,
        model_spec: EmbeddingModelSpec,
        runtime_components: dict[str, str],
    ) -> "EmbeddingBackendIdentity":
        fingerprint = canonical_sha256(
            {
                "backend_name": backend_name,
                "backend_version": backend_version,
                "model_digest": model_spec.model_digest,
                "runtime_components": runtime_components,
            }
        )
        return cls(
            backend_name,
            backend_version,
            fingerprint,
            model_spec.model_digest,
        )


@dataclass(frozen=True, slots=True)
class PassageEmbeddingBatch:
    vectors: tuple[EmbeddingVector, ...]
    truncated: tuple[bool, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.vectors, (tuple, list)) or any(
            not isinstance(item, EmbeddingVector) for item in self.vectors
        ):
            raise TypeError("vectors must be immutable EmbeddingVector values")
        if not isinstance(self.truncated, (tuple, list)) or any(
            not isinstance(item, bool) for item in self.truncated
        ):
            raise TypeError("truncated must be immutable booleans")
        vectors = tuple(self.vectors)
        truncated = tuple(self.truncated)
        if len(vectors) != len(truncated):
            raise ValueError("vector and truncation counts differ")
        object.__setattr__(self, "vectors", vectors)
        object.__setattr__(self, "truncated", truncated)


class EmbeddingBackend(Protocol):
    """No model choice, chat, remote endpoint, or authority-bearing output."""

    def identity(self) -> EmbeddingBackendIdentity: ...

    def embed_passages(
        self,
        prepared_passages: tuple[str, ...],
    ) -> PassageEmbeddingBatch: ...

    def embed_query(self, prepared_query: str) -> EmbeddingVector: ...


__all__ = [
    "EmbeddingBackend",
    "EmbeddingBackendIdentity",
    "PassageEmbeddingBatch",
]
