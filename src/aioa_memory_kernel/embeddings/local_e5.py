"""Lazy, offline-only local E5 runtime used by controlled Step 19 validation."""

from __future__ import annotations

import hashlib
import importlib.metadata
import os
import stat
from pathlib import Path
from typing import Any

from aioa_memory_kernel.persistence import assert_no_open_persistence_transaction

from .backend import (
    EmbeddingBackendIdentity,
    PassageEmbeddingBatch,
)
from .models import (
    EmbeddingBoundaryError,
    EmbeddingVector,
    Step19ReasonCode,
    load_approved_model_spec,
    normalize_embedding_vector,
)


_MAXIMUM_MODEL_FILE_BYTES = 1024 * 1024 * 1024


def _regular_file_identity(path: Path) -> tuple[str, int]:
    try:
        metadata = path.lstat()
        if (
            path.is_symlink()
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_size < 1
            or metadata.st_size > _MAXIMUM_MODEL_FILE_BYTES
        ):
            raise OSError("unsafe model file")
        digest = hashlib.sha256()
        total = 0
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                total += len(block)
                if total > _MAXIMUM_MODEL_FILE_BYTES:
                    raise OSError("model file exceeds its byte limit")
                digest.update(block)
        if total != metadata.st_size:
            raise OSError("model file changed while hashing")
    except OSError as exc:
        raise EmbeddingBoundaryError(
            Step19ReasonCode.MODEL_RUNTIME_UNAVAILABLE
        ) from exc
    return digest.hexdigest(), total


def verify_model_snapshot(
    model_directory: Path,
) -> tuple[tuple[str, str, int], ...]:
    """Verify the trusted local snapshot and exact safetensors identity."""

    approved = load_approved_model_spec()
    if not isinstance(model_directory, Path) or not model_directory.is_absolute():
        raise EmbeddingBoundaryError(Step19ReasonCode.MODEL_RUNTIME_UNAVAILABLE)
    try:
        root = model_directory.resolve(strict=True)
        metadata = model_directory.lstat()
    except OSError as exc:
        raise EmbeddingBoundaryError(Step19ReasonCode.MODEL_RUNTIME_UNAVAILABLE) from exc
    if model_directory.is_symlink() or not stat.S_ISDIR(metadata.st_mode) or root != model_directory:
        raise EmbeddingBoundaryError(Step19ReasonCode.MODEL_RUNTIME_UNAVAILABLE)
    weight = root / approved.weight_filename
    try:
        digest, _ = _regular_file_identity(weight)
    except EmbeddingBoundaryError as exc:
        raise EmbeddingBoundaryError(Step19ReasonCode.MODEL_WEIGHT_MISMATCH) from exc
    if digest != approved.weight_sha256:
        raise EmbeddingBoundaryError(Step19ReasonCode.MODEL_WEIGHT_MISMATCH)
    required = (
        "config.json",
        "sentencepiece.bpe.model",
        "tokenizer.json",
        "tokenizer_config.json",
        "special_tokens_map.json",
        approved.weight_filename,
    )
    identities: list[tuple[str, str, int]] = []
    for name in required:
        path = root / name
        file_digest, byte_length = _regular_file_identity(path)
        identities.append((name, file_digest, byte_length))
    return tuple(identities)


class LocalE5Backend:
    """Transformers mean-pooling backend; model loading is local-files-only."""

    def __init__(
        self,
        model_directory: Path,
    ) -> None:
        self._spec = load_approved_model_spec()
        self._files = verify_model_snapshot(model_directory)
        try:
            import torch
            from transformers import AutoModel, AutoTokenizer
        except ImportError as exc:
            raise EmbeddingBoundaryError(Step19ReasonCode.MODEL_RUNTIME_UNAVAILABLE) from exc
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        self._torch = torch
        try:
            self._tokenizer = AutoTokenizer.from_pretrained(
                str(model_directory),
                local_files_only=True,
                trust_remote_code=False,
            )
            self._model = AutoModel.from_pretrained(
                str(model_directory),
                local_files_only=True,
                trust_remote_code=False,
                use_safetensors=True,
            )
        except Exception as exc:
            raise EmbeddingBoundaryError(Step19ReasonCode.MODEL_RUNTIME_UNAVAILABLE) from exc
        self._model.eval()
        dimension = int(getattr(self._model.config, "hidden_size", 0))
        if dimension != self._spec.embedding_dimension:
            raise EmbeddingBoundaryError(Step19ReasonCode.MODEL_IDENTITY_INVALID)
        versions = {
            name: importlib.metadata.version(name)
            for name in ("torch", "transformers", "tokenizers", "safetensors")
        }
        backend_version = versions["transformers"]
        self._identity = EmbeddingBackendIdentity.create(
            backend_name="local-transformers-e5",
            backend_version=backend_version,
            model_spec=self._spec,
            runtime_components=versions,
        )

    @property
    def verified_files(self) -> tuple[tuple[str, str, int], ...]:
        return self._files

    def identity(self) -> EmbeddingBackendIdentity:
        return self._identity

    def _embed(self, texts: tuple[str, ...]) -> tuple[EmbeddingVector, ...]:
        assert_no_open_persistence_transaction()
        if not texts:
            return ()
        try:
            encoded = self._tokenizer(
                list(texts),
                padding=True,
                truncation=True,
                max_length=self._spec.maximum_tokens,
                return_tensors="pt",
            )
            with self._torch.no_grad():
                output = self._model(**encoded).last_hidden_state
                mask = encoded["attention_mask"].unsqueeze(-1).to(output.dtype)
                summed = (output * mask).sum(dim=1)
                counts = mask.sum(dim=1).clamp(min=1e-9)
                pooled = summed / counts
            rows = pooled.detach().cpu().tolist()
        except Exception as exc:
            raise EmbeddingBoundaryError(Step19ReasonCode.MODEL_RUNTIME_UNAVAILABLE) from exc
        return tuple(normalize_embedding_vector(row) for row in rows)

    def _truncated(self, text: str) -> bool:
        try:
            token_ids: Any = self._tokenizer(
                text,
                add_special_tokens=True,
                truncation=False,
            )["input_ids"]
        except Exception as exc:
            raise EmbeddingBoundaryError(Step19ReasonCode.MODEL_RUNTIME_UNAVAILABLE) from exc
        return len(token_ids) > self._spec.maximum_tokens

    def embed_passages(
        self,
        prepared_passages: tuple[str, ...],
    ) -> PassageEmbeddingBatch:
        if any(not text.startswith(self._spec.passage_prefix) for text in prepared_passages):
            raise EmbeddingBoundaryError(Step19ReasonCode.MODEL_IDENTITY_INVALID)
        return PassageEmbeddingBatch(
            self._embed(prepared_passages),
            tuple(self._truncated(text) for text in prepared_passages),
        )

    def embed_query(self, prepared_query: str) -> EmbeddingVector:
        if not isinstance(prepared_query, str) or not prepared_query.startswith(self._spec.query_prefix):
            raise EmbeddingBoundaryError(Step19ReasonCode.MODEL_IDENTITY_INVALID)
        return self._embed((prepared_query,))[0]


__all__ = ["LocalE5Backend", "verify_model_snapshot"]
