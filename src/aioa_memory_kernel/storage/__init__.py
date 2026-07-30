"""S3 snapshot persistence and verification without semantic authority.

This package never creates an AWS client, authenticates, contacts AWS, grants
approval, changes publication state, deletes objects, or bypasses retention at
import time or through its public API.
"""

from .config import S3SnapshotConfig
from .errors import (
    SnapshotAccessDeniedError,
    SnapshotCapabilityError,
    SnapshotConfigurationError,
    SnapshotConflictError,
    SnapshotIntegrityError,
    SnapshotMalformedResponseError,
    SnapshotOperationError,
    SnapshotServiceUnavailableError,
    SnapshotSessionError,
    SnapshotStorageError,
)
from .models import (
    EXACT_BYTES_SERIALIZATION_VERSION,
    MAX_SNAPSHOT_BYTES,
    SNAPSHOT_SCHEMA_VERSION,
    SNAPSHOT_SERIALIZATION_VERSION,
    STORAGE_EVIDENCE_ONLY,
    BucketCapabilities,
    RetrievedSnapshot,
    S3ObjectLockMode,
    SnapshotEnvelope,
    SnapshotStorageEvidence,
)
from .protocols import (
    S3ClientProtocol,
    SnapshotStorageProtocol,
    StreamingBodyProtocol,
)
from .s3 import S3SnapshotAdapter


__all__ = [
    "MAX_SNAPSHOT_BYTES",
    "EXACT_BYTES_SERIALIZATION_VERSION",
    "SNAPSHOT_SCHEMA_VERSION",
    "SNAPSHOT_SERIALIZATION_VERSION",
    "STORAGE_EVIDENCE_ONLY",
    "BucketCapabilities",
    "RetrievedSnapshot",
    "S3ClientProtocol",
    "S3ObjectLockMode",
    "S3SnapshotAdapter",
    "S3SnapshotConfig",
    "SnapshotAccessDeniedError",
    "SnapshotCapabilityError",
    "SnapshotConfigurationError",
    "SnapshotConflictError",
    "SnapshotEnvelope",
    "SnapshotIntegrityError",
    "SnapshotMalformedResponseError",
    "SnapshotOperationError",
    "SnapshotServiceUnavailableError",
    "SnapshotSessionError",
    "SnapshotStorageError",
    "SnapshotStorageEvidence",
    "SnapshotStorageProtocol",
    "StreamingBodyProtocol",
]
