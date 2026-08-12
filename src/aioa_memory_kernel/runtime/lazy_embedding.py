"""Single-instance lazy embedding runtime for the constrained profile."""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Generic, TypeVar

from aioa_memory_kernel.contracts.exceptions import ContractValidationError
from aioa_memory_kernel.contracts.serialization import canonical_sha256

from .resource_guard import ResourceGuardDecision, ResourceWorkKind
from .resource_profile import Runtime4GBProfile, verify_runtime_4gb_profile


_RuntimeT = TypeVar("_RuntimeT")


class EmbeddingLoadBackpressured(RuntimeError):
    """A heavy embedding operation was rejected before model load."""

    sanitized_code = "STEP40_EMBEDDING_BACKPRESSURE"


@dataclass(frozen=True, slots=True)
class LazyEmbeddingStatus:
    profile_digest: str
    loaded: bool
    instance_count: int
    maximum_instances: int
    batch_size: int
    model_id: str
    status_hash: str

    def __post_init__(self) -> None:
        if not isinstance(self.loaded, bool):
            raise ContractValidationError("loaded must be boolean")
        if self.maximum_instances != 1 or self.instance_count not in (0, 1):
            raise ContractValidationError("embedding instance count is invalid")
        if self.loaded is not (self.instance_count == 1):
            raise ContractValidationError("embedding status and count differ")
        expected = canonical_sha256(self, exclude_fields=("status_hash",))
        if self.status_hash != expected:
            raise ContractValidationError("embedding status hash differs")

    @classmethod
    def create(
        cls,
        *,
        profile_digest: str,
        loaded: bool,
        instance_count: int,
        maximum_instances: int,
        batch_size: int,
        model_id: str,
    ) -> "LazyEmbeddingStatus":
        payload = {
            "profile_digest": profile_digest,
            "loaded": loaded,
            "instance_count": instance_count,
            "maximum_instances": maximum_instances,
            "batch_size": batch_size,
            "model_id": model_id,
        }
        return cls(**payload, status_hash=canonical_sha256(payload))


def embedding_thread_environment(profile: Runtime4GBProfile) -> Mapping[str, str]:
    """Return process-local environment overrides without mutating the host."""

    profile = verify_runtime_4gb_profile(profile)
    return MappingProxyType(
        {
            "OMP_NUM_THREADS": str(profile.threads.omp),
            "MKL_NUM_THREADS": str(profile.threads.mkl),
            "TOKENIZERS_PARALLELISM": (
                "true" if profile.threads.tokenizer_parallelism else "false"
            ),
        }
    )


class LazyEmbeddingRuntime(Generic[_RuntimeT]):
    """Create exactly one injected embedding backend on first permitted use."""

    def __init__(
        self,
        *,
        profile: Runtime4GBProfile,
        factory: Callable[[], _RuntimeT],
    ) -> None:
        self._profile = verify_runtime_4gb_profile(profile)
        if not callable(factory):
            raise TypeError("embedding factory must be callable")
        self._factory = factory
        self._lock = threading.Lock()
        self._runtime: _RuntimeT | None = None
        self._instance_count = 0

    def status(self) -> LazyEmbeddingStatus:
        return LazyEmbeddingStatus.create(
            profile_digest=self._profile.profile_digest,
            loaded=self._runtime is not None,
            instance_count=self._instance_count,
            maximum_instances=self._profile.embedding.maximum_instances,
            batch_size=self._profile.embedding.batch_size,
            model_id=self._profile.embedding.model_id,
        )

    def get(
        self,
        *,
        batch_size: int | None = None,
        guard_decision: ResourceGuardDecision | None = None,
    ) -> _RuntimeT:
        requested_batch = (
            self._profile.embedding.batch_size
            if batch_size is None
            else batch_size
        )
        if (
            not isinstance(requested_batch, int)
            or isinstance(requested_batch, bool)
            or requested_batch < 1
            or requested_batch > self._profile.embedding.batch_size
        ):
            raise ContractValidationError("embedding batch exceeds profile bound")
        if guard_decision is not None:
            if not isinstance(guard_decision, ResourceGuardDecision):
                raise TypeError("guard_decision must be typed")
            if guard_decision.work_kind is not ResourceWorkKind.HEAVY_EMBEDDING:
                raise ContractValidationError("embedding guard kind differs")
            if not guard_decision.allowed:
                raise EmbeddingLoadBackpressured(
                    "embedding operation was backpressured"
                )
        if self._runtime is not None:
            return self._runtime
        with self._lock:
            if self._runtime is None:
                runtime = self._factory()
                if runtime is None:
                    raise ContractValidationError(
                        "embedding factory returned no runtime"
                    )
                self._runtime = runtime
                self._instance_count += 1
                if self._instance_count != 1:
                    raise ContractValidationError(
                        "embedding singleton invariant failed"
                    )
        assert self._runtime is not None
        return self._runtime


__all__ = [
    "EmbeddingLoadBackpressured",
    "LazyEmbeddingRuntime",
    "LazyEmbeddingStatus",
    "embedding_thread_environment",
]
