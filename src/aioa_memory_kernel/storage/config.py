"""Explicit fail-closed configuration for the Step 7 S3 adapter."""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass

from aioa_memory_kernel.contracts.enums import StorageClass
from aioa_memory_kernel.contracts.serialization import sha256_hex

from .errors import SnapshotConfigurationError
from .models import S3ObjectLockMode


_REGION = re.compile(r"^[a-z]{2}(?:-[a-z0-9]+)+-\d+$")
_BUCKET = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
_OWNER = re.compile(r"^\d{12}$")
_RESERVED_PREFIXES = ("xn--", "sthree-", "amzn_s3_demo_")
_RESERVED_SUFFIXES = ("-s3alias", "--ol-s3", ".mrap", "--x-s3", "--table-s3")


def _invalid(message: str, code: str) -> SnapshotConfigurationError:
    return SnapshotConfigurationError(message, sanitized_code=code)


def _validate_bucket_name(value: object) -> str:
    if not isinstance(value, str) or _BUCKET.fullmatch(value) is None:
        raise _invalid(
            "bucket_name must be a valid 3-63 character S3 bucket name",
            "INVALID_BUCKET_NAME",
        )
    if (
        ".." in value
        or any(value.startswith(prefix) for prefix in _RESERVED_PREFIXES)
        or any(value.endswith(suffix) for suffix in _RESERVED_SUFFIXES)
    ):
        raise _invalid("bucket_name uses a reserved S3 form", "INVALID_BUCKET_NAME")
    try:
        ipaddress.ip_address(value)
    except ValueError:
        pass
    else:
        raise _invalid(
            "bucket_name cannot be formatted as an IP address",
            "INVALID_BUCKET_NAME",
        )
    return value


def _validate_prefix(value: object) -> str:
    if not isinstance(value, str) or len(value.encode("utf-8")) > 512:
        raise _invalid(
            "key_prefix must be a bounded string",
            "INVALID_OBJECT_KEY_PREFIX",
        )
    if not value:
        return value
    if (
        value != value.strip()
        or value.startswith("/")
        or value.endswith("/")
        or "\\" in value
        or "//" in value
        or any(segment in {".", ".."} for segment in value.split("/"))
        or any(ord(character) < 32 for character in value)
    ):
        raise _invalid(
            "key_prefix must be a canonical relative S3 key prefix",
            "INVALID_OBJECT_KEY_PREFIX",
        )
    return value


@dataclass(frozen=True, slots=True)
class S3SnapshotConfig:
    """Runtime values supplied separately from infrastructure code."""

    region: str
    bucket_name: str
    retention_mode: S3ObjectLockMode
    retention_days: int
    key_prefix: str = "memory-patch/snapshots/v1"
    require_object_lock: bool = True
    expected_bucket_owner: str | None = None
    server_side_encryption: str = "AES256"
    storage_class: StorageClass = StorageClass.S3_GLOBAL_LOCKED_SNAPSHOT

    def __post_init__(self) -> None:
        if not isinstance(self.region, str) or _REGION.fullmatch(self.region) is None:
            raise _invalid("region is malformed", "INVALID_AWS_REGION")
        object.__setattr__(
            self,
            "bucket_name",
            _validate_bucket_name(self.bucket_name),
        )
        object.__setattr__(self, "key_prefix", _validate_prefix(self.key_prefix))
        if self.retention_mode is not S3ObjectLockMode.GOVERNANCE:
            raise _invalid(
                "Step 7 supports only GOVERNANCE retention",
                "UNSUPPORTED_RETENTION_MODE",
            )
        if (
            not isinstance(self.retention_days, int)
            or isinstance(self.retention_days, bool)
            or not 1 <= self.retention_days <= 365
        ):
            raise _invalid(
                "retention_days must be an integer from 1 through 365",
                "INVALID_RETENTION_DAYS",
            )
        if self.require_object_lock is not True:
            raise _invalid(
                "Object Lock is mandatory for the global snapshot adapter",
                "OBJECT_LOCK_REQUIRED",
            )
        if self.storage_class is not StorageClass.S3_GLOBAL_LOCKED_SNAPSHOT:
            raise _invalid(
                "global locked and user-private snapshots require separate storage",
                "PRIVATE_SNAPSHOT_REQUIRES_SEPARATE_STORAGE",
            )
        if self.server_side_encryption != "AES256":
            raise _invalid(
                "Step 7 adapter 1A requires explicit SSE-S3 AES256",
                "UNSUPPORTED_BUCKET_ENCRYPTION",
            )
        if self.expected_bucket_owner is not None and (
            not isinstance(self.expected_bucket_owner, str)
            or _OWNER.fullmatch(self.expected_bucket_owner) is None
        ):
            raise _invalid(
                "expected_bucket_owner must be a 12-digit account identifier",
                "INVALID_EXPECTED_BUCKET_OWNER",
            )

    @property
    def bucket_reference(self) -> str:
        """Return a stable non-secret bucket reference for audit evidence."""

        return f"s3-bucket-sha256:{sha256_hex(self.bucket_name)}"

    def object_key(
        self,
        snapshot_id: str,
        scope_digest: str,
        object_suffix: str = "json",
    ) -> str:
        """Derive a deterministic key without exposing tenant/source labels."""

        if (
            not isinstance(snapshot_id, str)
            or re.fullmatch(r"s3snap-[0-9a-f]{64}", snapshot_id) is None
        ):
            raise _invalid(
                "snapshot_id is not a Step 7 deterministic identity",
                "INVALID_SNAPSHOT_ID",
            )
        if (
            not isinstance(scope_digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", scope_digest) is None
        ):
            raise _invalid(
                "scope_digest must be a lowercase SHA-256 digest",
                "INVALID_SCOPE_DIGEST",
            )
        if object_suffix not in {"json", "bin"}:
            raise _invalid(
                "object_suffix is not a supported snapshot representation",
                "INVALID_OBJECT_SUFFIX",
            )
        relative = (
            f"global/v1/{scope_digest[:2]}/{scope_digest}/"
            f"{snapshot_id}.{object_suffix}"
        )
        key = f"{self.key_prefix}/{relative}" if self.key_prefix else relative
        if len(key.encode("utf-8")) > 1024:
            raise _invalid(
                "derived object key exceeds the S3 key length",
                "OBJECT_KEY_TOO_LONG",
            )
        return key
