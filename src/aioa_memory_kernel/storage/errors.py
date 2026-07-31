"""Sanitized failures for the Step 7 and Step 8 storage boundaries."""

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


class ExternalVolumeError(RuntimeError):
    """Base external-volume failure with bounded, non-secret diagnostics."""

    def __init__(
        self,
        message: str = "external volume operation failed",
        *,
        operation: str | None = None,
        sanitized_code: str = "EXTERNAL_VOLUME_FAILURE",
    ) -> None:
        super().__init__(message)
        self.operation = operation
        self.sanitized_code = sanitized_code
        self.system_drive_fallback_allowed = False


class ExternalVolumeConfigurationError(ExternalVolumeError):
    """Explicit runtime configuration is missing, malformed, or unsafe."""


class ExternalVolumeUnavailableError(ExternalVolumeError):
    """The exact configured volume is absent or lacks a required capability."""


class ExternalVolumeIdentityError(ExternalVolumeError):
    """Live mount, device, filesystem, or marker identity did not match."""


class ExternalVolumeUnsafePathError(ExternalVolumeError):
    """A path escaped containment or used a symlink or special file."""


class ExternalVolumeIntegrityError(ExternalVolumeError):
    """Exact bytes, length, marker data, or read-back verification differed."""


class ExternalVolumeConflictError(ExternalVolumeError):
    """A no-overwrite target or atomic staging identity already exists."""


class ExternalVolumeOperationDisabledError(ExternalVolumeError):
    """An optional operation was disabled without selecting a fallback path."""
