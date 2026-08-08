"""Content-addressed external-volume passage embedding cache."""

from __future__ import annotations

import os

from aioa_memory_kernel.storage import (
    ExternalVolumeConflictError,
    ExternalVolumeError,
    ExternalVolumeOperation,
    ExternalVolumeRuntimeAdapter,
)

from .models import (
    EMBEDDING_BYTES_LENGTH,
    EmbeddingBoundaryError,
    EmbeddingRecord,
    EmbeddingVector,
    Step19ReasonCode,
    vector_from_float32_bytes,
)


class PassageEmbeddingCache:
    """Derived, rebuildable cache; it never changes source authority."""

    def __init__(self, external_volume: ExternalVolumeRuntimeAdapter) -> None:
        if not isinstance(external_volume, ExternalVolumeRuntimeAdapter):
            raise TypeError("external_volume must be ExternalVolumeRuntimeAdapter")
        self._external = external_volume

    @property
    def system_drive_fallback_allowed(self) -> bool:
        return False

    @staticmethod
    def relative_path(record: EmbeddingRecord) -> str:
        if not isinstance(record, EmbeddingRecord):
            raise TypeError("record must be EmbeddingRecord")
        return f"active/{record.model_digest[:16]}-{record.cache_key}.f32"

    def read(self, record: EmbeddingRecord) -> EmbeddingVector | None:
        relative_path = self.relative_path(record)
        try:
            target = self._external.resolve_path(
                ExternalVolumeOperation.EMBEDDING_CACHE,
                relative_path,
            )
            if not os.path.lexists(target):
                return None
            payload = self._external.read_exact(
                ExternalVolumeOperation.EMBEDDING_CACHE,
                relative_path,
                expected_sha256=record.embedding_bytes_sha256,
                expected_length=EMBEDDING_BYTES_LENGTH,
            )
            vector = vector_from_float32_bytes(payload)
        except ExternalVolumeError as exc:
            raise EmbeddingBoundaryError(
                Step19ReasonCode.CACHE_INTEGRITY_INVALID
            ) from exc
        if vector.bytes_sha256 != record.embedding_bytes_sha256:
            raise EmbeddingBoundaryError(Step19ReasonCode.CACHE_INTEGRITY_INVALID)
        return vector

    def store(
        self,
        record: EmbeddingRecord,
        vector: EmbeddingVector,
    ) -> EmbeddingVector:
        if not isinstance(vector, EmbeddingVector):
            raise EmbeddingBoundaryError(Step19ReasonCode.EMBEDDING_VECTOR_INVALID)
        if vector.bytes_sha256 != record.embedding_bytes_sha256:
            raise EmbeddingBoundaryError(Step19ReasonCode.CACHE_INTEGRITY_INVALID)
        relative_path = self.relative_path(record)
        try:
            self._external.atomic_write_exact(
                ExternalVolumeOperation.EMBEDDING_CACHE,
                relative_path,
                vector.float32_bytes,
                expected_sha256=vector.bytes_sha256,
                expected_length=EMBEDDING_BYTES_LENGTH,
            )
        except ExternalVolumeConflictError:
            replay = self.read(record)
            if replay is None:
                raise EmbeddingBoundaryError(Step19ReasonCode.CACHE_CONFLICT)
            return replay
        except ExternalVolumeError as exc:
            raise EmbeddingBoundaryError(Step19ReasonCode.CACHE_INTEGRITY_INVALID) from exc
        replay = self.read(record)
        if replay is None:
            raise EmbeddingBoundaryError(Step19ReasonCode.CACHE_INTEGRITY_INVALID)
        return replay


__all__ = ["PassageEmbeddingCache"]
