"""Step 8 external-volume runtime adapter and fail-closed policy.

The adapter verifies the exact configured mount and marker before every
operation. It never returns an internal-disk fallback, creates directories,
stores credentials, hosts authoritative database state, or grants semantic
authority.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Protocol

from aioa_memory_kernel.contracts.enums import StableStringEnum, StorageClass

from .errors import (
    ExternalVolumeConfigurationError,
    ExternalVolumeConflictError,
    ExternalVolumeError,
    ExternalVolumeIdentityError,
    ExternalVolumeIntegrityError,
    ExternalVolumeOperationDisabledError,
    ExternalVolumeUnavailableError,
    ExternalVolumeUnsafePathError,
)


EXTERNAL_VOLUME_SCHEMA_VERSION = "1.0.0"
EXTERNAL_VOLUME_STORAGE_ONLY = "STORAGE_EVIDENCE_ONLY"
EXTERNAL_VOLUME_PROJECT_ID = "memory-patch-for-aioa"
EXTERNAL_VOLUME_RELATIVE_ROOT = "AIOA_DATA/Memory-Patch-for-AIOA"
EXTERNAL_VOLUME_MARKER_NAME = ".aioa-external-volume.json"
EXTERNAL_VOLUME_EXPECTED_REMOTE = (
    "https://github.com/luciferprosun/"
    "Memory-Patch-for-AIOA-Hackathon-CockroachDB"
)
EXTERNAL_VOLUME_CREATION_HEAD = "b3d555ec230a894b541e3570347fcf086511df2a"
DEFAULT_MINIMUM_FREE_BYTES = 20 * 1024 * 1024 * 1024
DEFAULT_MAXIMUM_ATOMIC_WRITE_BYTES = 64 * 1024 * 1024

_UUID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{3,127}$")
_LABEL = re.compile(r"^[^\x00-\x1f/]{1,128}$")
_FILESYSTEM = re.compile(r"^[a-z0-9][a-z0-9._+-]{0,31}$")
_TRANSPORT = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
_ENVIRONMENT_ASSIGNMENT = re.compile(r"^([A-Z][A-Z0-9_]*)=(.*)$")
_ENVIRONMENT_REFERENCE = re.compile(r"\$\{([A-Z][A-Z0-9_]*)\}")
_LOWER_SHA256 = re.compile(r"^[0-9a-f]{64}$")

_REQUIRED_DIRECTORIES = (
    "corpora",
    "corpora/incoming",
    "corpora/raw",
    "corpora/normalized",
    "corpora/rejected",
    "corpora/manifests",
    "embeddings",
    "embeddings/active",
    "embeddings/staging",
    "embeddings/manifests",
    "indexes",
    "indexes/active",
    "indexes/staging",
    "indexes/manifests",
    "ingestion",
    "ingestion/downloads",
    "ingestion/wheels",
    "ingestion/source-archives",
    "ingestion/build-cache",
    "cache",
    "cache/huggingface",
    "cache/datasets",
    "cache/transformers",
    "cache/pip",
    "cache/xdg",
    "cache/temporary",
    "snapshots",
    "snapshots/application",
    "snapshots/database-export",
    "snapshots/manifests",
    "backups",
    "backups/repository-data",
    "backups/migration-rollback",
    "backups/manifests",
    "migration",
    "migration/journals",
    "migration/inventories",
    "migration/verification",
    "migration/quarantine",
    "logs",
    "reports",
)


def _configuration_error(
    message: str,
    code: str,
) -> ExternalVolumeConfigurationError:
    return ExternalVolumeConfigurationError(message, sanitized_code=code)


def _normalize_remote(value: str) -> str:
    return value.removesuffix(".git").rstrip("/")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _device_reference(device_uuid: str) -> str:
    digest = _sha256(f"external-volume-1a:{device_uuid}".encode("utf-8"))
    return f"external-volume-sha256:{digest}"


def _canonical_absolute_path(value: object, field_name: str) -> Path:
    if (
        not isinstance(value, str)
        or not value
        or "\x00" in value
        or any(ord(character) < 32 for character in value)
    ):
        raise _configuration_error(
            f"{field_name} must be a canonical absolute path",
            "INVALID_EXTERNAL_PATH",
        )
    path = Path(value)
    if not path.is_absolute() or os.path.normpath(value) != value or value == "/":
        raise _configuration_error(
            f"{field_name} must be a canonical non-root absolute path",
            "INVALID_EXTERNAL_PATH",
        )
    return path


def _bounded_text(
    value: object,
    field_name: str,
    pattern: re.Pattern[str],
) -> str:
    if (
        not isinstance(value, str)
        or value != value.strip()
        or pattern.fullmatch(value) is None
    ):
        raise _configuration_error(
            f"{field_name} is missing or malformed",
            "INVALID_EXTERNAL_IDENTITY_CONFIG",
        )
    return value


def load_external_volume_environment(path: str | os.PathLike[str]) -> dict[str, str]:
    """Parse the Step 0B local environment file without executing shell code."""

    config_path = Path(path)
    try:
        metadata = config_path.lstat()
    except OSError as exc:
        raise _configuration_error(
            "external-volume environment file is unavailable",
            "EXTERNAL_ENV_UNAVAILABLE",
        ) from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or config_path.is_symlink()
        or metadata.st_size > 64 * 1024
        or metadata.st_mode & 0o077
    ):
        raise _configuration_error(
            "external-volume environment file is not a private regular file",
            "UNSAFE_EXTERNAL_ENV_FILE",
        )
    try:
        text = config_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise _configuration_error(
            "external-volume environment file cannot be read",
            "EXTERNAL_ENV_UNAVAILABLE",
        ) from exc

    values: dict[str, str] = {}
    for line_number, source_line in enumerate(text.splitlines(), start=1):
        line = source_line.strip()
        if not line or line.startswith("#"):
            continue
        match = _ENVIRONMENT_ASSIGNMENT.fullmatch(line)
        if match is None:
            raise _configuration_error(
                f"external-volume environment line {line_number} is malformed",
                "MALFORMED_EXTERNAL_ENV",
            )
        name, raw_value = match.groups()
        if name in values:
            raise _configuration_error(
                f"external-volume environment key {name} is duplicated",
                "DUPLICATE_EXTERNAL_ENV_KEY",
            )
        if len(raw_value) < 2 or raw_value[0] not in {'"', "'"}:
            raise _configuration_error(
                f"external-volume environment key {name} must be quoted",
                "MALFORMED_EXTERNAL_ENV",
            )
        quote = raw_value[0]
        if raw_value[-1] != quote:
            raise _configuration_error(
                f"external-volume environment key {name} has broken quoting",
                "MALFORMED_EXTERNAL_ENV",
            )
        value = raw_value[1:-1]
        if (
            "`" in value
            or "$(" in value
            or "\\" in value
            or "\x00" in value
            or any(ord(character) < 32 for character in value)
        ):
            raise _configuration_error(
                f"external-volume environment key {name} uses unsafe syntax",
                "UNSAFE_EXTERNAL_ENV_SYNTAX",
            )
        if quote == "'":
            if "$" in value:
                raise _configuration_error(
                    f"external-volume environment key {name} cannot expand",
                    "UNSAFE_EXTERNAL_ENV_SYNTAX",
                )
        else:
            def replace_reference(reference: re.Match[str]) -> str:
                referenced_name = reference.group(1)
                if referenced_name not in values:
                    raise _configuration_error(
                        "external-volume environment has a forward reference",
                        "UNRESOLVED_EXTERNAL_ENV_REFERENCE",
                    )
                return values[referenced_name]

            value = _ENVIRONMENT_REFERENCE.sub(replace_reference, value)
            if "$" in value:
                raise _configuration_error(
                    f"external-volume environment key {name} uses unsafe expansion",
                    "UNSAFE_EXTERNAL_ENV_SYNTAX",
                )
        values[name] = value
    return values


@dataclass(frozen=True, slots=True)
class ExternalVolumeConfig:
    """Explicit expected identity for one prepared external data volume."""

    mountpoint: Path
    data_root: Path
    device_uuid: str
    device_label: str
    filesystem_type: str
    device_transport: str
    minimum_free_bytes: int = DEFAULT_MINIMUM_FREE_BYTES
    reserve_percent: int = 10
    maximum_atomic_write_bytes: int = DEFAULT_MAXIMUM_ATOMIC_WRITE_BYTES
    project_id: str = EXTERNAL_VOLUME_PROJECT_ID
    expected_remote: str = EXTERNAL_VOLUME_EXPECTED_REMOTE
    creation_head: str = EXTERNAL_VOLUME_CREATION_HEAD
    required_mount_options: frozenset[str] = frozenset(
        {"rw", "nodev", "nosuid"}
    )

    def __post_init__(self) -> None:
        mountpoint = _canonical_absolute_path(str(self.mountpoint), "mountpoint")
        data_root = _canonical_absolute_path(str(self.data_root), "data_root")
        expected_root = mountpoint / EXTERNAL_VOLUME_RELATIVE_ROOT
        if data_root != expected_root:
            raise _configuration_error(
                "data_root is not the exact prepared project root",
                "EXTERNAL_ROOT_MISMATCH",
            )
        object.__setattr__(self, "mountpoint", mountpoint)
        object.__setattr__(self, "data_root", data_root)
        object.__setattr__(
            self,
            "device_uuid",
            _bounded_text(self.device_uuid, "device_uuid", _UUID),
        )
        object.__setattr__(
            self,
            "device_label",
            _bounded_text(self.device_label, "device_label", _LABEL),
        )
        object.__setattr__(
            self,
            "filesystem_type",
            _bounded_text(
                self.filesystem_type,
                "filesystem_type",
                _FILESYSTEM,
            ),
        )
        object.__setattr__(
            self,
            "device_transport",
            _bounded_text(
                self.device_transport,
                "device_transport",
                _TRANSPORT,
            ),
        )
        for field_name, value, minimum, maximum in (
            (
                "minimum_free_bytes",
                self.minimum_free_bytes,
                1024 * 1024 * 1024,
                8 * 1024**5,
            ),
            ("reserve_percent", self.reserve_percent, 1, 50),
            (
                "maximum_atomic_write_bytes",
                self.maximum_atomic_write_bytes,
                1,
                1024**3,
            ),
        ):
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or not minimum <= value <= maximum
            ):
                raise _configuration_error(
                    f"{field_name} is outside the bounded policy",
                    "INVALID_EXTERNAL_CAPACITY_POLICY",
                )
        if self.project_id != EXTERNAL_VOLUME_PROJECT_ID:
            raise _configuration_error(
                "project_id does not identify this repository",
                "EXTERNAL_PROJECT_MISMATCH",
            )
        if _normalize_remote(self.expected_remote) != EXTERNAL_VOLUME_EXPECTED_REMOTE:
            raise _configuration_error(
                "expected_remote does not identify this repository",
                "EXTERNAL_REPOSITORY_MISMATCH",
            )
        if self.creation_head != EXTERNAL_VOLUME_CREATION_HEAD:
            raise _configuration_error(
                "creation_head does not identify the Step 0B baseline",
                "EXTERNAL_CREATION_HEAD_MISMATCH",
            )
        if self.required_mount_options != frozenset({"rw", "nodev", "nosuid"}):
            raise _configuration_error(
                "required_mount_options cannot weaken the Step 8 policy",
                "UNSAFE_EXTERNAL_MOUNT_POLICY",
            )

    @classmethod
    def from_mapping(cls, values: Mapping[str, str]) -> ExternalVolumeConfig:
        """Build config from explicit environment-style values."""

        required = (
            "AIOA_EXTERNAL_MOUNTPOINT",
            "AIOA_EXTERNAL_DATA_ROOT",
            "AIOA_EXTERNAL_DEVICE_UUID",
            "AIOA_EXTERNAL_DEVICE_LABEL",
            "AIOA_EXTERNAL_FILESYSTEM_TYPE",
            "AIOA_EXTERNAL_DEVICE_TRANSPORT",
        )
        missing = [name for name in required if not values.get(name)]
        if missing:
            raise _configuration_error(
                "required external-volume configuration is missing",
                "MISSING_EXTERNAL_CONFIG",
            )

        def integer_value(name: str, default: int) -> int:
            raw = values.get(name)
            if raw is None:
                return default
            if not raw.isdecimal():
                raise _configuration_error(
                    f"{name} must be a decimal integer",
                    "INVALID_EXTERNAL_CAPACITY_POLICY",
                )
            return int(raw)

        return cls(
            mountpoint=Path(values["AIOA_EXTERNAL_MOUNTPOINT"]),
            data_root=Path(values["AIOA_EXTERNAL_DATA_ROOT"]),
            device_uuid=values["AIOA_EXTERNAL_DEVICE_UUID"],
            device_label=values["AIOA_EXTERNAL_DEVICE_LABEL"],
            filesystem_type=values["AIOA_EXTERNAL_FILESYSTEM_TYPE"],
            device_transport=values["AIOA_EXTERNAL_DEVICE_TRANSPORT"],
            minimum_free_bytes=integer_value(
                "AIOA_EXTERNAL_MINIMUM_FREE_BYTES",
                DEFAULT_MINIMUM_FREE_BYTES,
            ),
            reserve_percent=integer_value("AIOA_EXTERNAL_RESERVE_PERCENT", 10),
            maximum_atomic_write_bytes=integer_value(
                "AIOA_EXTERNAL_MAXIMUM_ATOMIC_WRITE_BYTES",
                DEFAULT_MAXIMUM_ATOMIC_WRITE_BYTES,
            ),
        )


class ExternalVolumeFailurePolicy(StableStringEnum):
    """Required failure behavior; no policy permits an internal fallback."""

    FAIL_CLOSED = "FAIL_CLOSED"
    DISABLE_OPERATION_WITHOUT_FALLBACK = "DISABLE_OPERATION_WITHOUT_FALLBACK"


class ExternalVolumeOperation(StableStringEnum):
    """Bounded derived-data operations permitted by the Step 8 adapter."""

    CORPUS_REPLICA = "CORPUS_REPLICA"
    EMBEDDING_CACHE = "EMBEDDING_CACHE"
    INDEX_CACHE = "INDEX_CACHE"
    INGESTION_STAGING = "INGESTION_STAGING"
    PACKAGE_CACHE = "PACKAGE_CACHE"
    APPLICATION_SNAPSHOT_STAGING = "APPLICATION_SNAPSHOT_STAGING"
    DATABASE_EXPORT = "DATABASE_EXPORT"
    BACKUP = "BACKUP"
    VALIDATION_EVIDENCE = "VALIDATION_EVIDENCE"


@dataclass(frozen=True, slots=True)
class ExternalVolumeOperationPolicy:
    operation: ExternalVolumeOperation
    relative_root: str
    failure_policy: ExternalVolumeFailurePolicy
    write_allowed: bool
    system_drive_fallback_allowed: bool = False
    storage_class: StorageClass = StorageClass.EXTERNAL_DERIVED


_OPERATION_POLICIES = {
    ExternalVolumeOperation.CORPUS_REPLICA: ExternalVolumeOperationPolicy(
        ExternalVolumeOperation.CORPUS_REPLICA,
        "corpora/raw",
        ExternalVolumeFailurePolicy.FAIL_CLOSED,
        True,
    ),
    ExternalVolumeOperation.EMBEDDING_CACHE: ExternalVolumeOperationPolicy(
        ExternalVolumeOperation.EMBEDDING_CACHE,
        "embeddings",
        ExternalVolumeFailurePolicy.DISABLE_OPERATION_WITHOUT_FALLBACK,
        True,
    ),
    ExternalVolumeOperation.INDEX_CACHE: ExternalVolumeOperationPolicy(
        ExternalVolumeOperation.INDEX_CACHE,
        "indexes",
        ExternalVolumeFailurePolicy.DISABLE_OPERATION_WITHOUT_FALLBACK,
        True,
    ),
    ExternalVolumeOperation.INGESTION_STAGING: ExternalVolumeOperationPolicy(
        ExternalVolumeOperation.INGESTION_STAGING,
        "ingestion/downloads",
        ExternalVolumeFailurePolicy.FAIL_CLOSED,
        True,
    ),
    ExternalVolumeOperation.PACKAGE_CACHE: ExternalVolumeOperationPolicy(
        ExternalVolumeOperation.PACKAGE_CACHE,
        "cache",
        ExternalVolumeFailurePolicy.DISABLE_OPERATION_WITHOUT_FALLBACK,
        True,
    ),
    ExternalVolumeOperation.APPLICATION_SNAPSHOT_STAGING:
        ExternalVolumeOperationPolicy(
            ExternalVolumeOperation.APPLICATION_SNAPSHOT_STAGING,
            "snapshots/application",
            ExternalVolumeFailurePolicy.FAIL_CLOSED,
            True,
        ),
    ExternalVolumeOperation.DATABASE_EXPORT: ExternalVolumeOperationPolicy(
        ExternalVolumeOperation.DATABASE_EXPORT,
        "snapshots/database-export",
        ExternalVolumeFailurePolicy.FAIL_CLOSED,
        True,
    ),
    ExternalVolumeOperation.BACKUP: ExternalVolumeOperationPolicy(
        ExternalVolumeOperation.BACKUP,
        "backups",
        ExternalVolumeFailurePolicy.FAIL_CLOSED,
        True,
    ),
    ExternalVolumeOperation.VALIDATION_EVIDENCE:
        ExternalVolumeOperationPolicy(
            ExternalVolumeOperation.VALIDATION_EVIDENCE,
            "reports",
            ExternalVolumeFailurePolicy.FAIL_CLOSED,
            True,
        ),
}


@dataclass(frozen=True, slots=True)
class ExternalMountIdentity:
    """Raw probe result retained only inside the verification boundary."""

    target: Path
    source: str
    filesystem_type: str
    mount_options: frozenset[str]
    device_uuid: str
    device_label: str
    device_transport: str
    device_read_only: bool
    source_is_block_device: bool
    total_bytes: int
    available_bytes: int
    mount_device_id: int
    system_root_device_id: int


class ExternalVolumeProbe(Protocol):
    def inspect(self, mountpoint: Path) -> ExternalMountIdentity: ...


@dataclass(frozen=True, slots=True)
class ExternalVolumeStatus:
    """Sanitized verified status safe for durable evidence."""

    schema_version: str
    device_reference: str
    filesystem_type: str
    device_transport: str
    total_bytes: int
    available_bytes: int
    reserve_bytes: int
    marker_sha256: str
    marker_created_at_utc: str
    mount_identity_verified: bool
    marker_identity_verified: bool
    root_filesystem_distinct: bool
    writable_verified: bool
    storage_class: StorageClass = StorageClass.EXTERNAL_DERIVED
    authority_status: str = EXTERNAL_VOLUME_STORAGE_ONLY
    system_drive_fallback_allowed: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "device_reference": self.device_reference,
            "filesystem_type": self.filesystem_type,
            "device_transport": self.device_transport,
            "total_bytes": self.total_bytes,
            "available_bytes": self.available_bytes,
            "reserve_bytes": self.reserve_bytes,
            "marker_sha256": self.marker_sha256,
            "marker_created_at_utc": self.marker_created_at_utc,
            "mount_identity_verified": self.mount_identity_verified,
            "marker_identity_verified": self.marker_identity_verified,
            "root_filesystem_distinct": self.root_filesystem_distinct,
            "writable_verified": self.writable_verified,
            "storage_class": self.storage_class.value,
            "authority_status": self.authority_status,
            "system_drive_fallback_allowed": (
                self.system_drive_fallback_allowed
            ),
        }


@dataclass(frozen=True, slots=True)
class ExternalVolumeWriteEvidence:
    """Exact-byte write evidence that grants no semantic authority."""

    schema_version: str
    operation: ExternalVolumeOperation
    relative_path: str
    content_sha256: str
    content_length: int
    device_reference: str
    marker_sha256: str
    atomic_no_replace: bool
    exact_read_back: bool
    file_fsync_completed: bool
    directory_fsync_completed: bool
    storage_class: StorageClass = StorageClass.EXTERNAL_DERIVED
    authority_status: str = EXTERNAL_VOLUME_STORAGE_ONLY
    system_drive_fallback_allowed: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "operation": self.operation.value,
            "relative_path": self.relative_path,
            "content_sha256": self.content_sha256,
            "content_length": self.content_length,
            "device_reference": self.device_reference,
            "marker_sha256": self.marker_sha256,
            "atomic_no_replace": self.atomic_no_replace,
            "exact_read_back": self.exact_read_back,
            "file_fsync_completed": self.file_fsync_completed,
            "directory_fsync_completed": self.directory_fsync_completed,
            "storage_class": self.storage_class.value,
            "authority_status": self.authority_status,
            "system_drive_fallback_allowed": (
                self.system_drive_fallback_allowed
            ),
        }


class ExternalVolumeRuntimeAdapter:
    """Freshly verify and access only the prepared external derived-data tree."""

    def __init__(
        self,
        config: ExternalVolumeConfig,
        probe: ExternalVolumeProbe | None = None,
    ) -> None:
        if not isinstance(config, ExternalVolumeConfig):
            raise _configuration_error(
                "config must be ExternalVolumeConfig",
                "INVALID_EXTERNAL_CONFIG",
            )
        self._config = config
        if probe is None:
            from aioa_memory_kernel.runtime import LinuxExternalVolumeProbe

            probe = LinuxExternalVolumeProbe()
        self._probe = probe

    @staticmethod
    def operation_policy(
        operation: ExternalVolumeOperation,
    ) -> ExternalVolumeOperationPolicy:
        if not isinstance(operation, ExternalVolumeOperation):
            raise _configuration_error(
                "operation is not a supported external-volume operation",
                "UNSUPPORTED_EXTERNAL_OPERATION",
            )
        return _OPERATION_POLICIES[operation]

    @property
    def system_drive_fallback_allowed(self) -> bool:
        return False

    def _safe_directory(
        self,
        path: Path,
        *,
        expected_device_id: int | None = None,
        require_write: bool,
        code: str,
    ) -> os.stat_result:
        try:
            metadata = path.lstat()
        except OSError as exc:
            raise ExternalVolumeUnavailableError(
                "required external-volume directory is unavailable",
                sanitized_code=code,
            ) from exc
        if path.is_symlink() or not stat.S_ISDIR(metadata.st_mode):
            raise ExternalVolumeUnsafePathError(
                "external-volume directory is a symlink or special file",
                sanitized_code="UNSAFE_EXTERNAL_DIRECTORY",
            )
        if expected_device_id is not None and metadata.st_dev != expected_device_id:
            raise ExternalVolumeIdentityError(
                "external-volume directory crossed a filesystem boundary",
                sanitized_code="EXTERNAL_FILESYSTEM_BOUNDARY_MISMATCH",
            )
        flags = (
            os.O_RDONLY
            | os.O_CLOEXEC
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            descriptor = os.open(path, flags)
            try:
                opened = os.fstat(descriptor)
                if (
                    not stat.S_ISDIR(opened.st_mode)
                    or opened.st_dev != metadata.st_dev
                    or opened.st_ino != metadata.st_ino
                ):
                    raise ExternalVolumeUnsafePathError(
                        "external-volume directory changed during verification",
                        sanitized_code="EXTERNAL_DIRECTORY_RACE",
                    )
            finally:
                os.close(descriptor)
        except ExternalVolumeError:
            raise
        except OSError as exc:
            raise ExternalVolumeUnavailableError(
                "external-volume directory cannot be opened safely",
                sanitized_code=code,
            ) from exc
        access_mode = os.R_OK | os.X_OK | (os.W_OK if require_write else 0)
        if not os.access(path, access_mode):
            raise ExternalVolumeUnavailableError(
                "external-volume directory lacks required access",
                sanitized_code="EXTERNAL_ACCESS_DENIED",
            )
        return metadata

    def _read_marker(
        self,
        mount: ExternalMountIdentity,
    ) -> tuple[str, str]:
        marker_path = self._config.data_root / EXTERNAL_VOLUME_MARKER_NAME
        try:
            metadata = marker_path.lstat()
        except OSError as exc:
            raise ExternalVolumeIdentityError(
                "external-volume marker is unavailable",
                sanitized_code="EXTERNAL_MARKER_MISSING",
            ) from exc
        if (
            marker_path.is_symlink()
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_dev != mount.mount_device_id
            or metadata.st_mode & 0o022
            or metadata.st_size > 64 * 1024
        ):
            raise ExternalVolumeIdentityError(
                "external-volume marker is unsafe",
                sanitized_code="UNSAFE_EXTERNAL_MARKER",
            )
        flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(marker_path, flags)
            try:
                opened = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(opened.st_mode)
                    or opened.st_dev != mount.mount_device_id
                    or opened.st_ino != metadata.st_ino
                ):
                    raise ExternalVolumeIdentityError(
                        "external-volume marker changed during verification",
                        sanitized_code="EXTERNAL_MARKER_RACE",
                    )
                marker_chunks: list[bytes] = []
                remaining = 64 * 1024 + 1
                while remaining:
                    chunk = os.read(descriptor, min(4096, remaining))
                    if not chunk:
                        break
                    marker_chunks.append(chunk)
                    remaining -= len(chunk)
                marker_bytes = b"".join(marker_chunks)
            finally:
                os.close(descriptor)
        except ExternalVolumeError:
            raise
        except OSError as exc:
            raise ExternalVolumeIdentityError(
                "external-volume marker cannot be read safely",
                sanitized_code="EXTERNAL_MARKER_UNREADABLE",
            ) from exc
        if len(marker_bytes) > 64 * 1024:
            raise ExternalVolumeIdentityError(
                "external-volume marker exceeds its bound",
                sanitized_code="UNSAFE_EXTERNAL_MARKER",
            )
        try:
            marker = json.loads(marker_bytes.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ExternalVolumeIntegrityError(
                "external-volume marker is malformed",
                sanitized_code="MALFORMED_EXTERNAL_MARKER",
            ) from exc
        expected = {
            "schema_version": EXTERNAL_VOLUME_SCHEMA_VERSION,
            "project_id": self._config.project_id,
            "purpose": "external-data-volume",
            "device_uuid": self._config.device_uuid,
            "device_label": self._config.device_label,
            "filesystem_type": self._config.filesystem_type,
            "repository_remote": _normalize_remote(
                self._config.expected_remote
            ),
            "repository_head_at_creation": self._config.creation_head,
        }
        if not isinstance(marker, Mapping) or set(marker) != {
            *expected,
            "created_at_utc",
        }:
            raise ExternalVolumeIntegrityError(
                "external-volume marker has an unexpected schema",
                sanitized_code="EXTERNAL_MARKER_SCHEMA_MISMATCH",
            )
        for key, expected_value in expected.items():
            actual_value = marker.get(key)
            if key == "repository_remote" and isinstance(actual_value, str):
                actual_value = _normalize_remote(actual_value)
            if actual_value != expected_value:
                raise ExternalVolumeIdentityError(
                    "external-volume marker identity does not match",
                    sanitized_code="EXTERNAL_MARKER_IDENTITY_MISMATCH",
                )
        created_at = marker.get("created_at_utc")
        if not isinstance(created_at, str) or not created_at.endswith("Z"):
            raise ExternalVolumeIntegrityError(
                "external-volume marker timestamp is malformed",
                sanitized_code="EXTERNAL_MARKER_TIMESTAMP_MALFORMED",
            )
        try:
            parsed = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ExternalVolumeIntegrityError(
                "external-volume marker timestamp is malformed",
                sanitized_code="EXTERNAL_MARKER_TIMESTAMP_MALFORMED",
            ) from exc
        if parsed.tzinfo is None:
            raise ExternalVolumeIntegrityError(
                "external-volume marker timestamp lacks a timezone",
                sanitized_code="EXTERNAL_MARKER_TIMESTAMP_MALFORMED",
            )
        return _sha256(marker_bytes), created_at

    def _verify_with_mount(
        self,
        *,
        require_write: bool,
    ) -> tuple[ExternalVolumeStatus, ExternalMountIdentity]:
        mount_metadata = self._safe_directory(
            self._config.mountpoint,
            require_write=require_write,
            code="EXTERNAL_MOUNTPOINT_UNAVAILABLE",
        )
        mount = self._probe.inspect(self._config.mountpoint)
        if mount.target != self._config.mountpoint:
            raise ExternalVolumeIdentityError(
                "configured path is not the exact filesystem mountpoint",
                sanitized_code="EXTERNAL_MOUNTPOINT_MISMATCH",
            )
        if not mount.source_is_block_device or not mount.source.startswith("/dev/"):
            raise ExternalVolumeIdentityError(
                "external volume is not backed by a block device",
                sanitized_code="NON_BLOCK_MOUNT_SOURCE",
            )
        if mount.filesystem_type != self._config.filesystem_type:
            raise ExternalVolumeIdentityError(
                "external-volume filesystem type does not match",
                sanitized_code="EXTERNAL_FILESYSTEM_MISMATCH",
            )
        if mount.device_uuid != self._config.device_uuid:
            raise ExternalVolumeIdentityError(
                "external-volume UUID does not match",
                sanitized_code="EXTERNAL_UUID_MISMATCH",
            )
        if mount.device_label != self._config.device_label:
            raise ExternalVolumeIdentityError(
                "external-volume label does not match",
                sanitized_code="EXTERNAL_LABEL_MISMATCH",
            )
        if mount.device_transport != self._config.device_transport:
            raise ExternalVolumeIdentityError(
                "external-volume transport does not match",
                sanitized_code="EXTERNAL_TRANSPORT_MISMATCH",
            )
        if not self._config.required_mount_options.issubset(
            mount.mount_options
        ) or "ro" in mount.mount_options:
            raise ExternalVolumeUnavailableError(
                "external volume lacks required safe mount options",
                sanitized_code="UNSAFE_EXTERNAL_MOUNT_OPTIONS",
            )
        if mount.device_read_only:
            raise ExternalVolumeUnavailableError(
                "external block device reports read-only state",
                sanitized_code="EXTERNAL_DEVICE_READ_ONLY",
            )
        if mount_metadata.st_dev != mount.mount_device_id:
            raise ExternalVolumeIdentityError(
                "mountpoint device identity changed during verification",
                sanitized_code="EXTERNAL_MOUNT_RACE",
            )
        if mount.mount_device_id == mount.system_root_device_id:
            raise ExternalVolumeIdentityError(
                "external data resolves to the system root filesystem",
                sanitized_code="SYSTEM_DRIVE_FALLBACK_DETECTED",
            )
        self._safe_directory(
            self._config.data_root,
            expected_device_id=mount.mount_device_id,
            require_write=require_write,
            code="EXTERNAL_DATA_ROOT_UNAVAILABLE",
        )
        if os.path.realpath(self._config.data_root) != str(self._config.data_root):
            raise ExternalVolumeUnsafePathError(
                "external data root contains a symlink",
                sanitized_code="EXTERNAL_ROOT_SYMLINK",
            )
        try:
            contained = (
                os.path.commonpath(
                    (self._config.data_root, self._config.mountpoint)
                )
                == str(self._config.mountpoint)
            )
        except ValueError:
            contained = False
        if not contained:
            raise ExternalVolumeUnsafePathError(
                "external data root escapes the configured mountpoint",
                sanitized_code="EXTERNAL_ROOT_ESCAPE",
            )
        for relative_path in _REQUIRED_DIRECTORIES:
            self._safe_directory(
                self._config.data_root / relative_path,
                expected_device_id=mount.mount_device_id,
                require_write=require_write,
                code="EXTERNAL_LAYOUT_INCOMPLETE",
            )
        reserve_bytes = max(
            self._config.minimum_free_bytes,
            mount.total_bytes * self._config.reserve_percent // 100,
        )
        if mount.available_bytes <= reserve_bytes:
            raise ExternalVolumeUnavailableError(
                "external volume does not exceed its free-space reserve",
                sanitized_code="EXTERNAL_FREE_SPACE_EXHAUSTED",
            )
        marker_sha256, marker_created_at = self._read_marker(mount)
        status = ExternalVolumeStatus(
            schema_version=EXTERNAL_VOLUME_SCHEMA_VERSION,
            device_reference=_device_reference(self._config.device_uuid),
            filesystem_type=mount.filesystem_type,
            device_transport=mount.device_transport,
            total_bytes=mount.total_bytes,
            available_bytes=mount.available_bytes,
            reserve_bytes=reserve_bytes,
            marker_sha256=marker_sha256,
            marker_created_at_utc=marker_created_at,
            mount_identity_verified=True,
            marker_identity_verified=True,
            root_filesystem_distinct=True,
            writable_verified=require_write,
        )
        return status, mount

    def verify(self, *, require_write: bool = False) -> ExternalVolumeStatus:
        """Verify live identity, containment, marker, layout, and capacity."""

        status, _ = self._verify_with_mount(require_write=require_write)
        return status

    @staticmethod
    def _validate_relative_path(relative_path: object) -> PurePosixPath:
        if (
            not isinstance(relative_path, str)
            or not relative_path
            or relative_path != relative_path.strip()
            or len(relative_path.encode("utf-8")) > 512
            or "\\" in relative_path
            or "\x00" in relative_path
            or any(ord(character) < 32 for character in relative_path)
        ):
            raise ExternalVolumeUnsafePathError(
                "external relative path is malformed",
                sanitized_code="INVALID_EXTERNAL_RELATIVE_PATH",
            )
        path = PurePosixPath(relative_path)
        if (
            path.is_absolute()
            or str(path) != relative_path
            or any(part in {"", ".", ".."} for part in path.parts)
            or path.name == EXTERNAL_VOLUME_MARKER_NAME
        ):
            raise ExternalVolumeUnsafePathError(
                "external relative path escapes its operation root",
                sanitized_code="EXTERNAL_PATH_ESCAPE",
            )
        return path

    def _operation_failure(
        self,
        policy: ExternalVolumeOperationPolicy,
        error: ExternalVolumeError,
    ) -> ExternalVolumeError:
        if (
            policy.failure_policy
            is ExternalVolumeFailurePolicy.DISABLE_OPERATION_WITHOUT_FALLBACK
        ):
            return ExternalVolumeOperationDisabledError(
                "optional external-volume operation is disabled",
                operation=policy.operation.value,
                sanitized_code=error.sanitized_code,
            )
        error.operation = policy.operation.value
        return error

    def _resolve_verified(
        self,
        operation: ExternalVolumeOperation,
        relative_path: str,
        *,
        require_write: bool,
    ) -> tuple[
        ExternalVolumeOperationPolicy,
        ExternalVolumeStatus,
        ExternalMountIdentity,
        Path,
    ]:
        policy = self.operation_policy(operation)
        if require_write and not policy.write_allowed:
            raise ExternalVolumeUnavailableError(
                "external-volume operation does not permit writes",
                operation=operation.value,
                sanitized_code="EXTERNAL_OPERATION_READ_ONLY",
            )
        try:
            status, mount = self._verify_with_mount(
                require_write=require_write
            )
        except ExternalVolumeError as exc:
            raise self._operation_failure(policy, exc) from exc
        relative = self._validate_relative_path(relative_path)
        operation_root = self._config.data_root / policy.relative_root
        target = operation_root.joinpath(*relative.parts)
        try:
            contained = (
                os.path.commonpath((target, operation_root))
                == str(operation_root)
            )
        except ValueError:
            contained = False
        if not contained:
            raise ExternalVolumeUnsafePathError(
                "external path escapes its operation root",
                operation=operation.value,
                sanitized_code="EXTERNAL_PATH_ESCAPE",
            )
        current = operation_root
        for part in relative.parts[:-1]:
            current = current / part
            self._safe_directory(
                current,
                expected_device_id=mount.mount_device_id,
                require_write=require_write,
                code="EXTERNAL_PARENT_UNAVAILABLE",
            )
        return policy, status, mount, target

    def resolve_path(
        self,
        operation: ExternalVolumeOperation,
        relative_path: str,
        *,
        require_write: bool = False,
    ) -> Path:
        """Return only a verified external path; never return a fallback."""

        _, _, _, target = self._resolve_verified(
            operation,
            relative_path,
            require_write=require_write,
        )
        return target

    def _read_regular_file(
        self,
        target: Path,
        *,
        mount_device_id: int,
        maximum_bytes: int,
        operation: ExternalVolumeOperation,
    ) -> bytes:
        flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
        try:
            before_open = target.lstat()
            if (
                target.is_symlink()
                or not stat.S_ISREG(before_open.st_mode)
                or before_open.st_dev != mount_device_id
                or before_open.st_size > maximum_bytes
            ):
                raise ExternalVolumeUnsafePathError(
                    "external target is not a bounded regular file",
                    operation=operation.value,
                    sanitized_code="UNSAFE_EXTERNAL_FILE",
                )
            descriptor = os.open(target, flags)
            try:
                metadata = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(metadata.st_mode)
                    or metadata.st_dev != mount_device_id
                    or metadata.st_size > maximum_bytes
                    or metadata.st_ino != before_open.st_ino
                ):
                    raise ExternalVolumeUnsafePathError(
                        "external target is not a bounded regular file",
                        operation=operation.value,
                        sanitized_code="UNSAFE_EXTERNAL_FILE",
                    )
                chunks: list[bytes] = []
                remaining = maximum_bytes + 1
                while remaining:
                    chunk = os.read(descriptor, min(1024 * 1024, remaining))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    remaining -= len(chunk)
            finally:
                os.close(descriptor)
        except ExternalVolumeError:
            raise
        except OSError as exc:
            raise ExternalVolumeUnavailableError(
                "external file cannot be read safely",
                operation=operation.value,
                sanitized_code="EXTERNAL_READ_FAILED",
            ) from exc
        payload = b"".join(chunks)
        if len(payload) > maximum_bytes:
            raise ExternalVolumeUnsafePathError(
                "external file exceeds its bounded read limit",
                operation=operation.value,
                sanitized_code="EXTERNAL_FILE_TOO_LARGE",
            )
        return payload

    def read_exact(
        self,
        operation: ExternalVolumeOperation,
        relative_path: str,
        *,
        expected_sha256: str,
        expected_length: int,
    ) -> bytes:
        """Read a regular external file and verify exact length and SHA-256."""

        if (
            not isinstance(expected_sha256, str)
            or _LOWER_SHA256.fullmatch(expected_sha256) is None
            or not isinstance(expected_length, int)
            or isinstance(expected_length, bool)
            or not 0 <= expected_length <= self._config.maximum_atomic_write_bytes
        ):
            raise _configuration_error(
                "expected external file identity is malformed",
                "INVALID_EXTERNAL_EXPECTED_IDENTITY",
            )
        _, _, mount, target = self._resolve_verified(
            operation,
            relative_path,
            require_write=False,
        )
        payload = self._read_regular_file(
            target,
            mount_device_id=mount.mount_device_id,
            maximum_bytes=self._config.maximum_atomic_write_bytes,
            operation=operation,
        )
        if len(payload) != expected_length or _sha256(payload) != expected_sha256:
            raise ExternalVolumeIntegrityError(
                "external file does not match its exact expected identity",
                operation=operation.value,
                sanitized_code="EXTERNAL_READBACK_MISMATCH",
            )
        return payload

    @staticmethod
    def _staging_key(
        policy: ExternalVolumeOperationPolicy,
        relative_path: str,
    ) -> str:
        identity = f"{policy.relative_root}/{relative_path}".encode("utf-8")
        return _sha256(identity)[:16]

    def _incomplete_staging_names(
        self,
        target: Path,
        *,
        staging_key: str,
        mount_device_id: int,
        operation: ExternalVolumeOperation,
    ) -> tuple[str, ...]:
        prefix = f".aioa-step8-atomic-{staging_key}-"
        matches: list[str] = []
        try:
            entries = tuple(target.parent.iterdir())
        except OSError as exc:
            raise ExternalVolumeUnavailableError(
                "external staging directory cannot be inspected",
                operation=operation.value,
                sanitized_code="EXTERNAL_STAGING_INSPECTION_FAILED",
            ) from exc
        for entry in entries:
            if not entry.name.startswith(prefix) or not entry.name.endswith(".tmp"):
                continue
            try:
                metadata = entry.lstat()
            except OSError as exc:
                raise ExternalVolumeUnavailableError(
                    "external staging artifact cannot be inspected",
                    operation=operation.value,
                    sanitized_code="EXTERNAL_STAGING_INSPECTION_FAILED",
                ) from exc
            if (
                entry.is_symlink()
                or not stat.S_ISREG(metadata.st_mode)
                or metadata.st_dev != mount_device_id
            ):
                raise ExternalVolumeUnsafePathError(
                    "external staging artifact is unsafe",
                    operation=operation.value,
                    sanitized_code="UNSAFE_EXTERNAL_STAGING_ARTIFACT",
                )
            matches.append(entry.name)
        return tuple(sorted(matches))

    def incomplete_atomic_artifacts(
        self,
        operation: ExternalVolumeOperation,
        relative_path: str,
    ) -> tuple[str, ...]:
        """List only target-bound Step 8 atomic staging artifacts read-only."""

        policy, _, mount, target = self._resolve_verified(
            operation,
            relative_path,
            require_write=False,
        )
        staging_key = self._staging_key(policy, relative_path)
        names = self._incomplete_staging_names(
            target,
            staging_key=staging_key,
            mount_device_id=mount.mount_device_id,
            operation=operation,
        )
        return tuple(f"{policy.relative_root}/{name}" for name in names)

    @staticmethod
    def _fsync_directory(
        directory: Path,
        operation: ExternalVolumeOperation,
    ) -> None:
        flags = (
            os.O_RDONLY
            | os.O_CLOEXEC
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            descriptor = os.open(directory, flags)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        except OSError as exc:
            raise ExternalVolumeUnavailableError(
                "external directory durability sync failed",
                operation=operation.value,
                sanitized_code="EXTERNAL_DIRECTORY_FSYNC_FAILED",
            ) from exc

    def atomic_write_exact(
        self,
        operation: ExternalVolumeOperation,
        relative_path: str,
        payload: bytes,
        *,
        expected_sha256: str,
        expected_length: int,
    ) -> ExternalVolumeWriteEvidence:
        """Atomically create one exact-byte file without replacing a target."""

        if not isinstance(payload, bytes):
            raise _configuration_error(
                "atomic external write requires immutable bytes",
                "INVALID_EXTERNAL_PAYLOAD",
            )
        if (
            not isinstance(expected_sha256, str)
            or _LOWER_SHA256.fullmatch(expected_sha256) is None
            or not isinstance(expected_length, int)
            or isinstance(expected_length, bool)
            or expected_length != len(payload)
            or expected_length > self._config.maximum_atomic_write_bytes
            or _sha256(payload) != expected_sha256
        ):
            raise ExternalVolumeIntegrityError(
                "atomic external payload does not match its expected identity",
                operation=(
                    operation.value
                    if isinstance(operation, ExternalVolumeOperation)
                    else None
                ),
                sanitized_code="EXTERNAL_PAYLOAD_IDENTITY_MISMATCH",
            )
        policy, status, mount, target = self._resolve_verified(
            operation,
            relative_path,
            require_write=True,
        )
        if not policy.write_allowed:
            raise ExternalVolumeUnavailableError(
                "external-volume operation does not permit writes",
                operation=operation.value,
                sanitized_code="EXTERNAL_OPERATION_READ_ONLY",
            )
        try:
            target.lstat()
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise ExternalVolumeUnavailableError(
                "external no-overwrite target cannot be inspected",
                operation=operation.value,
                sanitized_code="EXTERNAL_TARGET_INSPECTION_FAILED",
            ) from exc
        else:
            raise ExternalVolumeConflictError(
                "external no-overwrite target already exists",
                operation=operation.value,
                sanitized_code="EXTERNAL_TARGET_EXISTS",
            )

        staging_key = self._staging_key(policy, relative_path)
        if self._incomplete_staging_names(
            target,
            staging_key=staging_key,
            mount_device_id=mount.mount_device_id,
            operation=operation,
        ):
            raise ExternalVolumeConflictError(
                "an incomplete target-bound atomic staging artifact exists",
                operation=operation.value,
                sanitized_code="EXTERNAL_STAGING_ARTIFACT_EXISTS",
            )
        temporary_path = target.parent / (
            f".aioa-step8-atomic-{staging_key}-{os.getpid()}-"
            f"{secrets.token_hex(8)}.tmp"
        )
        descriptor: int | None = None
        temporary_created = False
        published = False
        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | os.O_CLOEXEC
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            descriptor = os.open(temporary_path, flags, 0o600)
            temporary_created = True
            written = 0
            while written < len(payload):
                count = os.write(descriptor, payload[written:])
                if count <= 0:
                    raise OSError("short atomic write")
                written += count
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = None

            staged = self._read_regular_file(
                temporary_path,
                mount_device_id=mount.mount_device_id,
                maximum_bytes=self._config.maximum_atomic_write_bytes,
                operation=operation,
            )
            if staged != payload:
                raise ExternalVolumeIntegrityError(
                    "atomic staging read-back differs from input",
                    operation=operation.value,
                    sanitized_code="EXTERNAL_STAGING_READBACK_MISMATCH",
                )
            try:
                os.link(
                    temporary_path,
                    target,
                    follow_symlinks=False,
                )
            except FileExistsError as exc:
                raise ExternalVolumeConflictError(
                    "external no-overwrite target appeared concurrently",
                    operation=operation.value,
                    sanitized_code="EXTERNAL_TARGET_EXISTS",
                ) from exc
            published = True
            self._fsync_directory(target.parent, operation)
            os.unlink(temporary_path)
            temporary_created = False
            self._fsync_directory(target.parent, operation)
        except ExternalVolumeError:
            raise
        except OSError as exc:
            raise ExternalVolumeUnavailableError(
                "atomic external-volume write failed",
                operation=operation.value,
                sanitized_code="EXTERNAL_ATOMIC_WRITE_FAILED",
            ) from exc
        finally:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            if temporary_created and not published:
                try:
                    os.unlink(temporary_path)
                except OSError:
                    pass

        read_back = self._read_regular_file(
            target,
            mount_device_id=mount.mount_device_id,
            maximum_bytes=self._config.maximum_atomic_write_bytes,
            operation=operation,
        )
        if len(read_back) != expected_length or _sha256(read_back) != expected_sha256:
            raise ExternalVolumeIntegrityError(
                "published external file failed exact read-back",
                operation=operation.value,
                sanitized_code="EXTERNAL_PUBLISHED_READBACK_MISMATCH",
            )
        return ExternalVolumeWriteEvidence(
            schema_version=EXTERNAL_VOLUME_SCHEMA_VERSION,
            operation=operation,
            relative_path=f"{policy.relative_root}/{relative_path}",
            content_sha256=expected_sha256,
            content_length=expected_length,
            device_reference=status.device_reference,
            marker_sha256=status.marker_sha256,
            atomic_no_replace=True,
            exact_read_back=True,
            file_fsync_completed=True,
            directory_fsync_completed=True,
        )


__all__ = [
    "DEFAULT_MAXIMUM_ATOMIC_WRITE_BYTES",
    "DEFAULT_MINIMUM_FREE_BYTES",
    "EXTERNAL_VOLUME_CREATION_HEAD",
    "EXTERNAL_VOLUME_EXPECTED_REMOTE",
    "EXTERNAL_VOLUME_MARKER_NAME",
    "EXTERNAL_VOLUME_PROJECT_ID",
    "EXTERNAL_VOLUME_RELATIVE_ROOT",
    "EXTERNAL_VOLUME_SCHEMA_VERSION",
    "EXTERNAL_VOLUME_STORAGE_ONLY",
    "ExternalMountIdentity",
    "ExternalVolumeConfig",
    "ExternalVolumeFailurePolicy",
    "ExternalVolumeOperation",
    "ExternalVolumeOperationPolicy",
    "ExternalVolumeProbe",
    "ExternalVolumeRuntimeAdapter",
    "ExternalVolumeStatus",
    "ExternalVolumeWriteEvidence",
    "load_external_volume_environment",
]
