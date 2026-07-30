"""Injectable S3 and snapshot-storage protocols with no SDK dependency."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

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
