"""Sanitized failures for the Step 7 S3 snapshot boundary."""

from __future__ import annotations


class SnapshotStorageError(RuntimeError):
    """Base failure exposing only bounded, non-secret diagnostics."""

    def __init__(
        self,
        message: str = "snapshot storage operation failed",
        *,
        operation: str | None = None,
        sanitized_code: str | None = None,
        aws_error_code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.operation = operation
        self.sanitized_code = sanitized_code
        self.aws_error_code = aws_error_code


class SnapshotConfigurationError(SnapshotStorageError):
    """Typed configuration or deterministic snapshot data is invalid."""


class SnapshotCapabilityError(SnapshotStorageError):
    """The target bucket does not provide the required safe capabilities."""


class SnapshotConflictError(SnapshotStorageError):
    """A deterministic object identity already binds different storage facts."""


class SnapshotIntegrityError(SnapshotStorageError):
    """Retrieved bytes, metadata, version, checksum, or retention did not match."""


class SnapshotMalformedResponseError(SnapshotStorageError):
    """An S3 response omitted or malformed a field required by the contract."""


class SnapshotAccessDeniedError(SnapshotStorageError):
    """The current identity lacks a required narrow S3 permission."""


class SnapshotSessionError(SnapshotStorageError):
    """The temporary AWS identity is missing, invalid, or expired."""


class SnapshotServiceUnavailableError(SnapshotStorageError):
    """S3 or its network endpoint was unavailable."""


class SnapshotOperationError(SnapshotStorageError):
    """An otherwise classified S3 operation failed closed."""
