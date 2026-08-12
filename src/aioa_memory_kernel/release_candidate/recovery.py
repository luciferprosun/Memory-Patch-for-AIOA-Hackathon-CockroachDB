"""Bounded local backup integrity and destructive-target guards."""

from __future__ import annotations

import dataclasses
import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, TypeVar

from aioa_memory_kernel.contracts.exceptions import (
    ContractValidationError,
    IntegrityError,
)
from aioa_memory_kernel.contracts.serialization import (
    canonical_sha256,
    require_sha256_hex,
)
from aioa_memory_kernel.security.redaction import assert_secret_free


RECOVERY_ROOT_PREFIX = "mp-step42-recovery-"
RESTORE_DATABASE_PREFIX = "mp_step42_restore_"
SOURCE_DATABASE_PREFIX = "mp_step42_source_"
_SAFE_DATABASE = re.compile(r"^[a-z][a-z0-9_]{2,62}$")
_T = TypeVar("_T")


def _reconstruct(value: _T, expected_type: type[_T]) -> _T:
    if not isinstance(value, expected_type):
        raise IntegrityError(f"expected {expected_type.__name__}")
    try:
        rebuilt = expected_type(
            **{
                field.name: getattr(value, field.name)
                for field in dataclasses.fields(expected_type)
            }
        )
    except (ContractValidationError, TypeError, ValueError) as error:
        raise IntegrityError(
            f"{expected_type.__name__} failed reconstruction"
        ) from error
    if rebuilt != value:
        raise IntegrityError(f"{expected_type.__name__} is not canonical")
    return rebuilt


def validate_disposable_recovery_root(path: Path) -> Path:
    """Accept only one real, mode-private Step42 child directly under /tmp."""

    if not isinstance(path, Path) or not path.is_absolute():
        raise ContractValidationError("recovery root must be an absolute Path")
    if not path.exists() or not path.is_dir() or path.is_symlink():
        raise ContractValidationError("recovery root must be a real directory")
    resolved = path.resolve(strict=True)
    if resolved.parent != Path("/tmp") or not resolved.name.startswith(
        RECOVERY_ROOT_PREFIX
    ):
        raise ContractValidationError("recovery root is outside the disposable boundary")
    if resolved in {Path("/"), Path("/tmp"), Path.home().resolve()}:
        raise ContractValidationError("recovery root is dangerously broad")
    if os.stat(resolved).st_mode & 0o077:
        raise ContractValidationError("recovery root must be mode-private")
    return resolved


def validate_restore_target(
    *,
    recovery_root: Path,
    source_database: str,
    restore_database: str,
    target_path: Path,
) -> Path:
    """Validate the exact disposable target before any restore mutation."""

    root = validate_disposable_recovery_root(recovery_root)
    for value, prefix, field_name in (
        (source_database, SOURCE_DATABASE_PREFIX, "source_database"),
        (restore_database, RESTORE_DATABASE_PREFIX, "restore_database"),
    ):
        if (
            not isinstance(value, str)
            or _SAFE_DATABASE.fullmatch(value) is None
            or not value.startswith(prefix)
        ):
            raise ContractValidationError(f"{field_name} is not disposable")
    if source_database == restore_database:
        raise ContractValidationError("restore target must differ from source")
    if not isinstance(target_path, Path) or not target_path.is_absolute():
        raise ContractValidationError("restore target path must be absolute")
    if target_path.is_symlink():
        raise ContractValidationError("restore target path cannot be a symlink")
    resolved = target_path.resolve(strict=False)
    if resolved.parent != root or not resolved.name.startswith("restore-"):
        raise ContractValidationError("restore target path is outside the owned root")
    if resolved in {root, Path("/"), Path("/tmp"), Path.home().resolve()}:
        raise ContractValidationError("restore target path is dangerously broad")
    return resolved


def _hash_file(path: Path) -> tuple[int, str]:
    if not path.is_file() or path.is_symlink():
        raise ContractValidationError("backup tree contains a non-regular file")
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            size += len(block)
            digest.update(block)
    return size, digest.hexdigest()


@dataclass(frozen=True, slots=True)
class BackupTreeReceipt:
    artifact_type: str
    file_count: int
    total_bytes: int
    file_digests: tuple[tuple[str, int, str], ...]
    rc_manifest_digest: str
    tree_digest: str

    def __post_init__(self) -> None:
        if self.artifact_type != "COCKROACHDB_NATIVE_BACKUP_TREE":
            raise ContractValidationError("backup artifact type differs")
        if (
            not isinstance(self.file_count, int)
            or isinstance(self.file_count, bool)
            or self.file_count < 1
            or not isinstance(self.total_bytes, int)
            or isinstance(self.total_bytes, bool)
            or self.total_bytes < 1
            or not isinstance(self.file_digests, tuple)
            or len(self.file_digests) != self.file_count
        ):
            raise ContractValidationError("backup tree counts are invalid")
        if self.file_digests != tuple(sorted(self.file_digests)):
            raise ContractValidationError("backup files must be sorted")
        names: set[str] = set()
        total = 0
        for name, size, digest in self.file_digests:
            if (
                not isinstance(name, str)
                or not name
                or name.startswith("/")
                or ".." in Path(name).parts
                or "\\" in name
                or name in names
                or not isinstance(size, int)
                or isinstance(size, bool)
                or size < 0
            ):
                raise ContractValidationError("backup file entry is invalid")
            names.add(name)
            total += size
            require_sha256_hex(digest, "backup file digest")
        if total != self.total_bytes:
            raise ContractValidationError("backup byte total differs")
        require_sha256_hex(self.rc_manifest_digest, "rc_manifest_digest")
        expected = canonical_sha256(self, exclude_fields=("tree_digest",))
        if self.tree_digest != expected:
            raise ContractValidationError("backup tree digest differs")
        assert_secret_free(self, surface="Step 42 backup receipt", reject_machine_paths=True)


def build_backup_tree_receipt(
    root: Path,
    *,
    rc_manifest_digest: str,
) -> BackupTreeReceipt:
    if not isinstance(root, Path) or not root.is_dir() or root.is_symlink():
        raise ContractValidationError("backup root must be a real directory")
    resolved = root.resolve(strict=True)
    entries: list[tuple[str, int, str]] = []
    for path in sorted(resolved.rglob("*")):
        if path.is_dir() and not path.is_symlink():
            continue
        size, digest = _hash_file(path)
        entries.append((path.relative_to(resolved).as_posix(), size, digest))
    payload = {
        "artifact_type": "COCKROACHDB_NATIVE_BACKUP_TREE",
        "file_count": len(entries),
        "total_bytes": sum(size for _name, size, _digest in entries),
        "file_digests": tuple(entries),
        "rc_manifest_digest": require_sha256_hex(
            rc_manifest_digest, "rc_manifest_digest"
        ),
    }
    return BackupTreeReceipt(**payload, tree_digest=canonical_sha256(payload))


def verify_backup_tree_receipt(
    root: Path,
    receipt: BackupTreeReceipt,
) -> BackupTreeReceipt:
    receipt = _reconstruct(receipt, BackupTreeReceipt)
    actual = build_backup_tree_receipt(
        root,
        rc_manifest_digest=receipt.rc_manifest_digest,
    )
    if actual != receipt:
        raise IntegrityError("backup artifact differs from its receipt")
    return receipt


@dataclass(frozen=True, slots=True)
class RecoveryWatermark:
    schema_version: str
    migration_ids: tuple[str, ...]
    critical_counts: tuple[tuple[str, int], ...]
    critical_hashes: tuple[tuple[str, str], ...]
    watermark_hash: str

    def __post_init__(self) -> None:
        if self.schema_version != "step42-recovery-watermark-1a":
            raise ContractValidationError("recovery watermark schema differs")
        if self.migration_ids != tuple(sorted(set(self.migration_ids))) or not self.migration_ids:
            raise ContractValidationError("migration IDs must be sorted and non-empty")
        if self.critical_counts != tuple(sorted(self.critical_counts)):
            raise ContractValidationError("critical counts must be sorted")
        if self.critical_hashes != tuple(sorted(self.critical_hashes)):
            raise ContractValidationError("critical hashes must be sorted")
        for name, count in self.critical_counts:
            if not isinstance(name, str) or not name or not isinstance(count, int) or count < 0:
                raise ContractValidationError("critical count is invalid")
        for name, digest in self.critical_hashes:
            if not isinstance(name, str) or not name:
                raise ContractValidationError("critical hash name is invalid")
            require_sha256_hex(digest, name)
        expected = canonical_sha256(self, exclude_fields=("watermark_hash",))
        if self.watermark_hash != expected:
            raise ContractValidationError("watermark hash differs")


def build_recovery_watermark(
    *,
    migration_ids: tuple[str, ...],
    critical_counts: Mapping[str, int],
    critical_hashes: Mapping[str, str],
) -> RecoveryWatermark:
    payload = {
        "schema_version": "step42-recovery-watermark-1a",
        "migration_ids": tuple(sorted(migration_ids)),
        "critical_counts": tuple(sorted(critical_counts.items())),
        "critical_hashes": tuple(sorted(critical_hashes.items())),
    }
    return RecoveryWatermark(**payload, watermark_hash=canonical_sha256(payload))


def verify_recovery_watermark(value: RecoveryWatermark) -> RecoveryWatermark:
    return _reconstruct(value, RecoveryWatermark)


__all__ = [
    "BackupTreeReceipt",
    "RECOVERY_ROOT_PREFIX",
    "RESTORE_DATABASE_PREFIX",
    "SOURCE_DATABASE_PREFIX",
    "RecoveryWatermark",
    "build_backup_tree_receipt",
    "build_recovery_watermark",
    "validate_disposable_recovery_root",
    "validate_restore_target",
    "verify_backup_tree_receipt",
    "verify_recovery_watermark",
]
