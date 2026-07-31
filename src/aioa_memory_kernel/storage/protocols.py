"""Injectable S3 and snapshot-storage protocols with no SDK dependency."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

from .external_volume import (
    ExternalVolumeOperation,
    ExternalVolumeStatus,
    ExternalVolumeWriteEvidence,
)
from .models import (
    BucketCapabilities,
    RetrievedSnapshot,
    SnapshotEnvelope,
    SnapshotStorageEvidence,
)


class StreamingBodyProtocol(Protocol):
    def read(self, amount: int | None = None) -> bytes: ...

    def close(self) -> None: ...


class S3ClientProtocol(Protocol):
    def get_bucket_location(self, **kwargs: Any) -> Mapping[str, Any]: ...

    def get_bucket_versioning(self, **kwargs: Any) -> Mapping[str, Any]: ...

    def get_object_lock_configuration(
        self, **kwargs: Any
    ) -> Mapping[str, Any]: ...

    def put_object(self, **kwargs: Any) -> Mapping[str, Any]: ...

    def head_object(self, **kwargs: Any) -> Mapping[str, Any]: ...

    def get_object(self, **kwargs: Any) -> Mapping[str, Any]: ...


class SnapshotStorageProtocol(Protocol):
    def inspect_bucket_capabilities(self) -> BucketCapabilities: ...

    def persist_snapshot(
        self, snapshot: SnapshotEnvelope
    ) -> SnapshotStorageEvidence: ...

    def inspect_snapshot(
        self,
        snapshot: SnapshotEnvelope,
        *,
        version_id: str,
    ) -> SnapshotStorageEvidence: ...

    def retrieve_snapshot(
        self,
        snapshot: SnapshotEnvelope,
        *,
        version_id: str,
    ) -> RetrievedSnapshot: ...

    def verify_snapshot(
        self,
        snapshot: SnapshotEnvelope,
        *,
        version_id: str,
    ) -> SnapshotStorageEvidence: ...


class ExternalVolumeRuntimeProtocol(Protocol):
    """Injectable Step 8 boundary with no system-drive fallback."""

    @property
    def system_drive_fallback_allowed(self) -> bool: ...

    def verify(self, *, require_write: bool = False) -> ExternalVolumeStatus: ...

    def resolve_path(
        self,
        operation: ExternalVolumeOperation,
        relative_path: str,
        *,
        require_write: bool = False,
    ) -> Path: ...

    def read_exact(
        self,
        operation: ExternalVolumeOperation,
        relative_path: str,
        *,
        expected_sha256: str,
        expected_length: int,
    ) -> bytes: ...

    def incomplete_atomic_artifacts(
        self,
        operation: ExternalVolumeOperation,
        relative_path: str,
    ) -> tuple[str, ...]: ...

    def atomic_write_exact(
        self,
        operation: ExternalVolumeOperation,
        relative_path: str,
        payload: bytes,
        *,
        expected_sha256: str,
        expected_length: int,
    ) -> ExternalVolumeWriteEvidence: ...
