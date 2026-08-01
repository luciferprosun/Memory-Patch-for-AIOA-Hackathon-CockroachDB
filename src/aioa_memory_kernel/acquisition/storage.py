"""Exact-root guard and streaming no-replace writes on the verified USB."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import stat
from collections.abc import Iterable, Mapping
from pathlib import Path, PurePosixPath

from aioa_memory_kernel.contracts.serialization import canonical_json_bytes
from aioa_memory_kernel.storage import (
    ExternalVolumeConfig,
    ExternalVolumeRuntimeAdapter,
    load_external_volume_environment,
)

from .errors import AcquisitionIntegrityError, AcquisitionStorageError
from .models import AcquisitionPolicy


LAYOUT = (
    "00_CONTROL/checkpoints",
    "00_CONTROL/checkpoints/object-intents",
    "01_PROVENANCE/source-sidecars",
    "02_LICENSES_AND_TERMS",
    "03_SOURCE_CATALOG",
    "10_DE_FEDERAL_CONSOLIDATED_GII/indexes",
    "10_DE_FEDERAL_CONSOLIDATED_GII/feeds",
    "10_DE_FEDERAL_CONSOLIDATED_GII/xml-zips",
    "10_DE_FEDERAL_CONSOLIDATED_GII/extracted-metadata",
    "10_DE_FEDERAL_CONSOLIDATED_GII/quarantine",
    "11_DE_FEDERAL_PROMULGATION_BGBL/indexes",
    "11_DE_FEDERAL_PROMULGATION_BGBL/feeds",
    "11_DE_FEDERAL_PROMULGATION_BGBL/issue-pages",
    "11_DE_FEDERAL_PROMULGATION_BGBL/issue-zips",
    "11_DE_FEDERAL_PROMULGATION_BGBL/pdfs",
    "11_DE_FEDERAL_PROMULGATION_BGBL/attachments",
    "11_DE_FEDERAL_PROMULGATION_BGBL/quarantine",
    "12_DE_STATE_BREMEN_METADATA/datasets",
    "12_DE_STATE_BREMEN_METADATA/provenance",
    "13_DE_STATE_BAYERN_CURRENT/indexes",
    "13_DE_STATE_BAYERN_CURRENT/feeds",
    "13_DE_STATE_BAYERN_CURRENT/xml-zips",
    "13_DE_STATE_BAYERN_CURRENT/quarantine",
    "14_DE_ADMINISTRATIVE_METADATA/bmf-feeds",
    "14_DE_ADMINISTRATIVE_METADATA/bmf-indexes",
    "14_DE_ADMINISTRATIVE_METADATA/quarantine",
    "20_UPDATE_FEEDS",
    "30_LOGIN_GATED_PLANS",
    "40_RESEARCH_ONLY_SOURCES",
    "90_QUARANTINE",
    "90_QUARANTINE/orphan-parts",
    "90_QUARANTINE/records",
    "99_REPORTS",
)


def _relative_path(value: str) -> PurePosixPath:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or "\\" in value
        or "\x00" in value
        or any(ord(character) < 32 for character in value)
        or len(value.encode("utf-8")) > 1024
    ):
        raise AcquisitionStorageError(
            "acquisition relative path is malformed",
            code="ACQUISITION_PATH_INVALID",
        )
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or str(path) != value
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise AcquisitionStorageError(
            "acquisition relative path escapes its root",
            code="ACQUISITION_PATH_ESCAPE",
        )
    return path


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(
        path,
        os.O_RDONLY
        | os.O_CLOEXEC
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class AcquisitionRootGuard:
    """Own exactly one approved sibling root on the already verified USB."""

    def __init__(
        self,
        *,
        repository_root: Path,
        policy: AcquisitionPolicy,
        config_path: Path,
    ) -> None:
        values = load_external_volume_environment(config_path)
        self.config = ExternalVolumeConfig.from_mapping(values)
        status = ExternalVolumeRuntimeAdapter(self.config).verify(
            require_write=True
        )
        if status.device_reference != policy.expected_device_reference:
            raise AcquisitionStorageError(
                "verified USB does not match acquisition policy",
                code="ACQUISITION_DEVICE_MISMATCH",
            )
        self.status = status
        self.policy = policy
        self.repository_root = repository_root
        self.mountpoint = self.config.mountpoint
        self.root = self.mountpoint.joinpath(
            *_relative_path(policy.target_relative_path).parts
        )
        self.seed = self.mountpoint.joinpath(
            *_relative_path(policy.seed_relative_path).parts
        )
        self._mount_device_id = self.mountpoint.lstat().st_dev
        self.request_count = 0
        self.created_bytes = 0
        self._verify_fixed_parent()
        self._verify_seed()

    def _verify_fixed_parent(self) -> None:
        current = self.mountpoint
        if self.mountpoint.is_symlink() or not self.mountpoint.is_dir():
            raise AcquisitionStorageError(
                "verified mountpoint is unsafe",
                code="ACQUISITION_MOUNT_UNSAFE",
            )
        for part in _relative_path(self.policy.target_relative_path).parts[:-1]:
            current = current / part
            try:
                metadata = current.lstat()
            except OSError as exc:
                raise AcquisitionStorageError(
                    "acquisition root parent is unavailable",
                    code="ACQUISITION_PARENT_UNAVAILABLE",
                ) from exc
            if (
                current.is_symlink()
                or not stat.S_ISDIR(metadata.st_mode)
                or metadata.st_dev != self._mount_device_id
            ):
                raise AcquisitionStorageError(
                    "acquisition root parent is unsafe",
                    code="ACQUISITION_PARENT_UNSAFE",
                )
        if os.path.realpath(current) != str(current):
            raise AcquisitionStorageError(
                "acquisition root parent contains a symlink",
                code="ACQUISITION_PARENT_UNSAFE",
            )
        self.parent = current

    def _verify_seed(self) -> None:
        try:
            before = self.seed.lstat()
        except OSError as exc:
            raise AcquisitionStorageError(
                "immutable seed archive is unavailable",
                code="SEED_ARCHIVE_UNAVAILABLE",
            ) from exc
        if (
            self.seed.is_symlink()
            or not stat.S_ISREG(before.st_mode)
            or before.st_dev != self._mount_device_id
            or before.st_size != self.policy.seed_length
        ):
            raise AcquisitionIntegrityError(
                "immutable seed archive identity differs",
                code="SEED_ARCHIVE_IDENTITY_MISMATCH",
            )
        digest = hashlib.sha256()
        with self.seed.open("rb", buffering=0) as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        after = self.seed.lstat()
        if (
            (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            or digest.hexdigest() != self.policy.seed_sha256
        ):
            raise AcquisitionIntegrityError(
                "immutable seed archive changed or failed hashing",
                code="SEED_ARCHIVE_IDENTITY_MISMATCH",
            )

    def initialize(
        self,
        initial_state: Mapping[str, object],
        *,
        reconcile_orphan_parts: bool = True,
    ) -> bool:
        root_preexists = os.path.lexists(self.root)
        available = os.statvfs(self.mountpoint).f_bavail * os.statvfs(
            self.mountpoint
        ).f_frsize
        minimum_free = (
            self.policy.final_minimum_free_bytes
            if root_preexists
            else self.policy.initial_minimum_free_bytes
        )
        if available < minimum_free:
            raise AcquisitionStorageError(
                "USB does not meet the acquisition free-space policy",
                code=(
                    "ACQUISITION_RESUME_SPACE_INSUFFICIENT"
                    if root_preexists
                    else "ACQUISITION_INITIAL_SPACE_INSUFFICIENT"
                ),
            )
        created = False
        try:
            os.mkdir(self.root, 0o700)
            created = True
            _fsync_directory(self.parent)
        except FileExistsError:
            pass
        if self.root.is_symlink() or not self.root.is_dir():
            raise AcquisitionStorageError(
                "acquisition root is unsafe",
                code="ACQUISITION_ROOT_UNSAFE",
            )
        root_stat = self.root.lstat()
        if root_stat.st_dev != self._mount_device_id:
            raise AcquisitionStorageError(
                "acquisition root crossed a filesystem boundary",
                code="ACQUISITION_ROOT_DEVICE_MISMATCH",
            )
        if created:
            for relative in LAYOUT:
                current = self.root
                for part in PurePosixPath(relative).parts:
                    current = current / part
                    try:
                        os.mkdir(current, 0o700)
                        _fsync_directory(current.parent)
                    except FileExistsError:
                        metadata = current.lstat()
                        if current.is_symlink() or not stat.S_ISDIR(metadata.st_mode):
                            raise AcquisitionStorageError(
                                "acquisition layout collided with unsafe entry",
                                code="ACQUISITION_LAYOUT_UNSAFE",
                            )
            self.write_json_absent(
                "00_CONTROL/acquisition-policy.json",
                {**initial_state, "policy": self.policy},
            )
            self.write_json_absent(
                "00_CONTROL/run-state.json",
                {**initial_state, "status": "INITIALIZED"},
            )
            self._create_empty("00_CONTROL/request-ledger.jsonl")
            self._create_empty("00_CONTROL/object-ledger.jsonl")
        else:
            self._verify_resume_layout()
        self._load_ledger_state()
        if reconcile_orphan_parts:
            self.quarantine_orphan_parts()
        return created

    def _verify_resume_layout(self) -> None:
        required = (
            "00_CONTROL/acquisition-policy.json",
            "00_CONTROL/run-state.json",
            "00_CONTROL/request-ledger.jsonl",
            "00_CONTROL/object-ledger.jsonl",
        )
        for relative in LAYOUT:
            path = self.resolve(relative)
            metadata = path.lstat()
            if (
                path.is_symlink()
                or not stat.S_ISDIR(metadata.st_mode)
                or metadata.st_dev != self._mount_device_id
            ):
                raise AcquisitionStorageError(
                    "resume layout is unsafe",
                    code="ACQUISITION_RESUME_UNSAFE",
                )
        for relative in required:
            path = self.resolve(relative)
            metadata = path.lstat()
            if (
                path.is_symlink()
                or not stat.S_ISREG(metadata.st_mode)
                or metadata.st_dev != self._mount_device_id
            ):
                raise AcquisitionStorageError(
                    "resume control file is unsafe",
                    code="ACQUISITION_RESUME_UNSAFE",
                )
        policy_record = json.loads(
            self.resolve("00_CONTROL/acquisition-policy.json").read_text(
                encoding="utf-8"
            )
        )
        if policy_record.get("policy", {}).get("digest") != self.policy.digest:
            raise AcquisitionIntegrityError(
                "resume policy digest differs",
                code="ACQUISITION_POLICY_CONFLICT",
            )

    def _load_ledger_state(self) -> None:
        request_ledger = self.resolve("00_CONTROL/request-ledger.jsonl")
        sequences: set[int] = set()
        with request_ledger.open("r", encoding="utf-8") as stream:
            for line in stream:
                if not line.strip():
                    continue
                record = json.loads(line)
                if record.get("event") not in {
                    "HTTP_REQUEST_ATTEMPT",
                    "HTTP_REDIRECT_ATTEMPT",
                }:
                    continue
                sequence = record.get("request_sequence")
                if not isinstance(sequence, int) or sequence <= 0:
                    raise AcquisitionIntegrityError(
                        "request ledger contains a malformed attempt",
                        code="ACQUISITION_LEDGER_INVALID",
                    )
                if sequence in sequences:
                    raise AcquisitionIntegrityError(
                        "request ledger repeats a request sequence",
                        code="ACQUISITION_LEDGER_INVALID",
                    )
                sequences.add(sequence)
        if sequences and sequences != set(range(1, max(sequences) + 1)):
            raise AcquisitionIntegrityError(
                "request ledger sequence is not contiguous",
                code="ACQUISITION_LEDGER_INVALID",
            )
        self.request_count = max(sequences, default=0)
        self.created_bytes = self.root_size()

    def resolve(self, relative: str) -> Path:
        parsed = _relative_path(relative)
        target = self.root.joinpath(*parsed.parts)
        if os.path.commonpath((target, self.root)) != str(self.root):
            raise AcquisitionStorageError(
                "acquisition path escapes root",
                code="ACQUISITION_PATH_ESCAPE",
            )
        current = self.root
        for part in parsed.parts[:-1]:
            current = current / part
            metadata = current.lstat()
            if (
                current.is_symlink()
                or not stat.S_ISDIR(metadata.st_mode)
                or metadata.st_dev != self._mount_device_id
            ):
                raise AcquisitionStorageError(
                    "acquisition parent is unsafe",
                    code="ACQUISITION_PARENT_UNSAFE",
                )
        return target

    def require_regular_file(self, relative: str) -> Path:
        target = self.resolve(relative)
        try:
            metadata = target.lstat()
        except OSError as exc:
            raise AcquisitionStorageError(
                "acquisition object is unavailable",
                code="ACQUISITION_OBJECT_UNAVAILABLE",
            ) from exc
        if (
            target.is_symlink()
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_dev != self._mount_device_id
        ):
            raise AcquisitionStorageError(
                "acquisition object is not a regular same-device file",
                code="ACQUISITION_OBJECT_UNSAFE",
            )
        return target

    def _create_empty(self, relative: str) -> None:
        target = self.resolve(relative)
        descriptor = os.open(
            target,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | os.O_CLOEXEC
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        os.fsync(descriptor)
        os.close(descriptor)
        _fsync_directory(target.parent)

    def write_json_absent(self, relative: str, value: object) -> str:
        payload_value = value
        if isinstance(value, Mapping) and "policy" in value:
            policy = value["policy"]
            if isinstance(policy, AcquisitionPolicy):
                payload_value = dict(value)
                policy_data = {
                    field: getattr(policy, field)
                    for field in policy.__dataclass_fields__
                }
                policy_data["digest"] = policy.digest
                payload_value["policy"] = policy_data
        payload = canonical_json_bytes(payload_value) + b"\n"
        digest = hashlib.sha256(payload).hexdigest()
        with self.stream_writer(relative) as writer:
            writer.write(payload)
            writer.publish()
        return digest

    def append_jsonl(self, relative: str, value: object) -> str:
        payload = canonical_json_bytes(value) + b"\n"
        target = self.resolve(relative)
        metadata = target.lstat()
        if (
            target.is_symlink()
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_dev != self._mount_device_id
        ):
            raise AcquisitionStorageError(
                "append-only ledger is unsafe",
                code="ACQUISITION_LEDGER_UNSAFE",
            )
        if self.created_bytes + len(payload) > self.policy.maximum_root_bytes:
            raise AcquisitionStorageError(
                "append would exceed the acquisition-root byte budget",
                code="ACQUISITION_ROOT_SIZE_LIMIT",
            )
        if self.free_bytes() - len(payload) < self.policy.final_minimum_free_bytes:
            raise AcquisitionStorageError(
                "append would breach the final free-space reserve",
                code="ACQUISITION_FREE_SPACE_RESERVE",
            )
        descriptor = os.open(
            target,
            os.O_WRONLY
            | os.O_APPEND
            | os.O_CLOEXEC
            | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            written = 0
            while written < len(payload):
                written += os.write(descriptor, payload[written:])
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        self.created_bytes += len(payload)
        return hashlib.sha256(payload).hexdigest()

    def stream_writer(self, relative: str) -> "AtomicStreamWriter":
        return AtomicStreamWriter(self, relative)

    def quarantine_orphan_parts(self) -> tuple[str, ...]:
        if not self.root.exists():
            return ()
        found: list[Path] = []
        for directory, names, files in os.walk(self.root, followlinks=False):
            safe_names: list[str] = []
            for name in sorted(names):
                child = Path(directory) / name
                metadata = child.lstat()
                if (
                    child.is_symlink()
                    or not stat.S_ISDIR(metadata.st_mode)
                    or metadata.st_dev != self._mount_device_id
                ):
                    raise AcquisitionStorageError(
                        "acquisition root contains an unsafe directory",
                        code="ACQUISITION_ROOT_UNSAFE",
                    )
                safe_names.append(name)
            names[:] = safe_names
            for name in sorted(files):
                if not name.startswith(".acquisition-") or not name.endswith(".part"):
                    continue
                path = Path(directory) / name
                metadata = path.lstat()
                if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
                    raise AcquisitionStorageError(
                        "orphan partial file is unsafe",
                        code="ACQUISITION_PART_UNSAFE",
                    )
                found.append(path)
        quarantined: list[str] = []
        for path in found:
            original_relative = path.relative_to(self.root).as_posix()
            digest = hashlib.sha256()
            length = 0
            with path.open("rb", buffering=0) as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
                    length += len(chunk)
            local_sha256 = digest.hexdigest()
            identity = hashlib.sha256(
                original_relative.encode("utf-8")
            ).hexdigest()
            quarantine_relative = (
                "90_QUARANTINE/orphan-parts/"
                f"{identity}-{local_sha256[:16]}.bin"
            )
            quarantine_path = self.resolve(quarantine_relative)
            if os.path.lexists(quarantine_path):
                existing = self.require_regular_file(quarantine_relative)
                existing_digest = hashlib.sha256()
                with existing.open("rb", buffering=0) as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        existing_digest.update(chunk)
                if (
                    existing.lstat().st_size != length
                    or existing_digest.hexdigest() != local_sha256
                ):
                    raise AcquisitionStorageError(
                        "orphan-part quarantine target conflicts",
                        code="ACQUISITION_QUARANTINE_CONFLICT",
                    )
            else:
                os.link(path, quarantine_path, follow_symlinks=False)
                _fsync_directory(quarantine_path.parent)
            os.unlink(path)
            _fsync_directory(path.parent)
            record = {
                "schema_version": "1.0.0",
                "reason": "INTERRUPTED_OR_REJECTED_PART",
                "original_relative_path": original_relative,
                "quarantine_relative_path": quarantine_relative,
                "local_sha256": local_sha256,
                "byte_length": length,
            }
            record_relative = quarantine_relative.removesuffix(".bin") + ".json"
            payload = canonical_json_bytes(record) + b"\n"
            record_path = self.resolve(record_relative)
            if record_path.exists():
                if record_path.read_bytes() != payload:
                    raise AcquisitionStorageError(
                        "orphan-part quarantine record conflicts",
                        code="ACQUISITION_QUARANTINE_CONFLICT",
                    )
            else:
                self.write_json_absent(record_relative, record)
            quarantined.append(quarantine_relative)
        return tuple(quarantined)

    def free_bytes(self) -> int:
        values = os.statvfs(self.mountpoint)
        return values.f_bavail * values.f_frsize

    def root_size(self) -> int:
        total = 0
        for directory, names, files in os.walk(self.root, followlinks=False):
            safe_names: list[str] = []
            for name in sorted(names):
                child = Path(directory) / name
                metadata = child.lstat()
                if (
                    child.is_symlink()
                    or not stat.S_ISDIR(metadata.st_mode)
                    or metadata.st_dev != self._mount_device_id
                ):
                    raise AcquisitionStorageError(
                        "acquisition root contains an unsafe directory",
                        code="ACQUISITION_ROOT_UNSAFE",
                    )
                safe_names.append(name)
            names[:] = safe_names
            for name in sorted(files):
                path = Path(directory) / name
                metadata = path.lstat()
                if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
                    raise AcquisitionStorageError(
                        "acquisition root contains an unsafe file",
                        code="ACQUISITION_ROOT_UNSAFE",
                    )
                total += metadata.st_size
        return total


class AtomicStreamWriter:
    def __init__(self, guard: AcquisitionRootGuard, relative: str) -> None:
        self.guard = guard
        self.relative = _relative_path(relative).as_posix()
        self.target = guard.resolve(self.relative)
        if os.path.lexists(self.target):
            raise AcquisitionStorageError(
                "no-overwrite target already exists",
                code="ACQUISITION_TARGET_EXISTS",
            )
        key = hashlib.sha256(self.relative.encode("utf-8")).hexdigest()[:16]
        self.part = self.target.parent / (
            f".acquisition-{key}-{os.getpid()}-{secrets.token_hex(8)}.part"
        )
        self.descriptor: int | None = None
        self.digest = hashlib.sha256()
        self.byte_length = 0
        self.published = False

    def __enter__(self) -> "AtomicStreamWriter":
        self.descriptor = os.open(
            self.part,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | os.O_CLOEXEC
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        return self

    def write(self, chunk: bytes) -> None:
        if self.descriptor is None or not isinstance(chunk, bytes):
            raise AcquisitionStorageError(
                "stream writer is not open",
                code="ACQUISITION_STREAM_STATE_INVALID",
            )
        if self.byte_length + len(chunk) > self.guard.policy.maximum_response_bytes:
            raise AcquisitionStorageError(
                "response exceeds the per-object bound",
                code="SIZE_LIMIT_EXCEEDED",
            )
        if self.guard.created_bytes + self.byte_length + len(chunk) > self.guard.policy.maximum_root_bytes:
            raise AcquisitionStorageError(
                "acquisition exceeds the root byte budget",
                code="ACQUISITION_ROOT_SIZE_LIMIT",
            )
        if self.guard.free_bytes() - len(chunk) < self.guard.policy.final_minimum_free_bytes:
            raise AcquisitionStorageError(
                "write would breach the final free-space reserve",
                code="ACQUISITION_FREE_SPACE_RESERVE",
            )
        offset = 0
        while offset < len(chunk):
            offset += os.write(self.descriptor, chunk[offset:])
        self.digest.update(chunk)
        self.byte_length += len(chunk)

    def publish(self, validators: Iterable[object] = ()) -> tuple[str, int]:
        if self.descriptor is None:
            raise AcquisitionStorageError(
                "stream writer is not open",
                code="ACQUISITION_STREAM_STATE_INVALID",
            )
        os.fsync(self.descriptor)
        os.close(self.descriptor)
        self.descriptor = None
        for validator in validators:
            if callable(validator):
                validator(self.part)
        try:
            os.link(self.part, self.target, follow_symlinks=False)
        except FileExistsError as exc:
            raise AcquisitionStorageError(
                "no-overwrite target appeared concurrently",
                code="ACQUISITION_TARGET_EXISTS",
            ) from exc
        _fsync_directory(self.target.parent)
        os.unlink(self.part)
        _fsync_directory(self.target.parent)
        self.published = True
        self.guard.created_bytes += self.byte_length
        return self.digest.hexdigest(), self.byte_length

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if self.descriptor is not None:
            try:
                os.close(self.descriptor)
            finally:
                self.descriptor = None


__all__ = ["AcquisitionRootGuard", "AtomicStreamWriter", "LAYOUT"]
