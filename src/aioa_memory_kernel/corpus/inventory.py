"""Bounded, restartable Step 14 corpus inventory implementation.

The source tree is opened read-only with ``O_NOFOLLOW``.  Mutable scan state
lives only below the caller-supplied derived-data directory and is removed
after a canonical bundle has been completed.  SQLite is used solely as a
bounded local spool; it is not semantic or transactional corpus authority.
"""

from __future__ import annotations

import hashlib
import heapq
import json
import math
import os
import re
import sqlite3
import stat
from collections import Counter
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterator, Mapping

from aioa_memory_kernel.contracts.serialization import (
    canonical_json_bytes,
    canonical_sha256,
    sha256_hex,
    to_canonical_data,
)
from aioa_memory_kernel.parsing import ResourceLimits
from aioa_memory_kernel.parsing.errors import ParsingError
from aioa_memory_kernel.parsing.parsers import parse_json_document, parse_plain_text

from .errors import CorpusReplayConflictError, CorpusSafetyError
from .models import (
    CorpusFileRecord,
    CorpusInventoryManifest,
    CorpusInventoryPolicy,
    CorpusInventoryRun,
    CorpusInventorySummary,
    CorpusPathAlias,
    ExactDuplicateGroup,
    FileKind,
    InventoryLicenseStatus,
    InventoryPrivacyStatus,
    LicenseAssessment,
    NearDuplicateCandidateGroup,
    NormalizedDuplicateGroup,
    ParserSupportStatus,
    PrivacyAssessment,
    QuarantineDecision,
    QuarantineReason,
    QuarantineStatus,
    RegistrationDisposition,
    SourceRegistrationCandidate,
    StabilityStatus,
)


_TOKEN = re.compile(r"\w+", re.UNICODE)
_SECRET_RULES = (
    ("AWS_ACCESS_KEY_SIGNAL", re.compile(rb"(?:AKIA|ASIA)[0-9A-Z]{16}")),
    ("AWS_SECRET_FIELD_SIGNAL", re.compile(rb"aws_(?:secret_access_key|session_token)\s*[:=]", re.I)),
    ("BEARER_TOKEN_FIELD_SIGNAL", re.compile(rb"authorization[ \t]{0,32}:[ \t]{0,32}bearer[ \t]{1,32}", re.I)),
    ("PASSWORD_FIELD_SIGNAL", re.compile(rb"(?:password|passwd)[ \t]{0,32}[:=][ \t]{0,32}[^\s,;]{4,256}", re.I)),
)
_PRIVACY_RULES = (
    (
        "EMAIL_ADDRESS_SIGNAL",
        re.compile(
            rb"[A-Z0-9._%+-]{1,64}@[A-Z0-9.-]{1,253}\.[A-Z]{2,63}",
            re.I,
        ),
    ),
    ("GERMAN_IBAN_SIGNAL", re.compile(rb"\bDE[0-9]{20}\b")),
)

_OUTPUT_JSONL = (
    "file-records.jsonl",
    "path-aliases.jsonl",
    "exact-duplicate-groups.jsonl",
    "normalized-duplicate-groups.jsonl",
    "near-duplicate-candidates.jsonl",
    "source-registration-candidates.jsonl",
    "quarantine-candidates.jsonl",
    "license-assessments.jsonl",
    "privacy-assessments.jsonl",
)


def _path_digest(relative_path: str) -> str:
    return canonical_sha256({"relative_path": relative_path})


def _safe_relative(path: Path, root: Path) -> str:
    try:
        relative = path.relative_to(root).as_posix()
    except ValueError as exc:
        raise CorpusSafetyError(
            "corpus entry escaped the approved root",
            sanitized_code="PATH_ESCAPE",
        ) from exc
    parsed = PurePosixPath(relative)
    if (
        not relative
        or parsed.is_absolute()
        or str(parsed) != relative
        or any(part in {"", ".", ".."} for part in parsed.parts)
    ):
        raise CorpusSafetyError(
            "corpus entry has an unsafe relative path",
            sanitized_code="PATH_ESCAPE",
        )
    return relative


def _mode_kind(mode: int) -> FileKind:
    if stat.S_ISREG(mode):
        return FileKind.REGULAR
    if stat.S_ISDIR(mode):
        return FileKind.DIRECTORY
    if stat.S_ISLNK(mode):
        return FileKind.SYMLINK
    if stat.S_ISSOCK(mode):
        return FileKind.SOCKET
    if stat.S_ISFIFO(mode):
        return FileKind.FIFO
    if stat.S_ISBLK(mode):
        return FileKind.BLOCK_DEVICE
    if stat.S_ISCHR(mode):
        return FileKind.CHARACTER_DEVICE
    return FileKind.OTHER_SPECIAL


def _stat_identity_digest(relative_path: str, metadata: os.stat_result) -> str:
    return canonical_sha256(
        {
            "relative_path": relative_path,
            "kind": _mode_kind(metadata.st_mode).value,
            "device": metadata.st_dev,
            "inode": metadata.st_ino,
            "size": metadata.st_size,
            "mtime_ns": metadata.st_mtime_ns,
        }
    )


def _file_extension(path: str) -> str:
    name = PurePosixPath(path).name.casefold()
    for suffix in (".tar.gz", ".tar.xz", ".tar.zst"):
        if name.endswith(suffix):
            return suffix
    return PurePosixPath(name).suffix


def _media_type(extension: str) -> str | None:
    return {
        ".json": "application/json",
        ".txt": "text/plain",
        ".jsonl": "application/x-ndjson",
        ".xml": "application/xml",
        ".csv": "text/csv",
        ".md": "text/markdown",
        ".html": "text/html",
        ".htm": "text/html",
        ".zip": "application/zip",
        ".pdf": "application/pdf",
    }.get(extension)


def _source_family(relative_path: str) -> str | None:
    wrapped = f"/{relative_path}/"
    if "/19_BULK_RAW_SOURCES/zips/" in wrapped:
        return "GESETZE_IM_INTERNET_OFFICIAL_CONSOLIDATED_RAW"
    if "/20_BULK_NORMALIZED_CORPUS/" in wrapped:
        return "GESETZE_IM_INTERNET_DERIVED_NORMALIZED"
    if "/03_COMPLETE_FEDERAL_LAW_MANIFEST/" in wrapped:
        return "GESETZE_IM_INTERNET_SOURCE_MANIFEST"
    if "/02_SOURCE_AUTHORITY_REGISTRY/" in wrapped:
        return "SOURCE_AUTHORITY_REGISTRY_EVIDENCE"
    if "/14_LICENSE_AND_REUSE_LEDGER/" in wrapped:
        return "LICENSE_AND_REUSE_EVIDENCE"
    if "/15_QA_AND_VALIDATION/" in wrapped:
        return "CORPUS_QA_EVIDENCE"
    if relative_path.casefold().endswith((".zip", ".z01", ".z02", ".z03", ".z04")):
        return "ARCHIVE_PACKAGE"
    return None


def _strict_json_value(payload: bytes) -> Any:
    class Duplicate(ValueError):
        pass

    def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in values:
            if key in result:
                raise Duplicate(key)
            result[key] = value
        return result

    def constant(value: str) -> None:
        raise ValueError(value)

    return json.loads(
        payload.decode("utf-8-sig"),
        object_pairs_hook=pairs,
        parse_constant=constant,
    )


def _bottom_hashes(text: str, policy: CorpusInventoryPolicy) -> tuple[int, ...]:
    tokens = (match.group(0).casefold() for match in _TOKEN.finditer(text))
    width = policy.shingle_width
    window: list[str] = []
    heap: list[int] = []
    present: set[int] = set()
    for token in tokens:
        window.append(token)
        if len(window) < width:
            continue
        if len(window) > width:
            del window[0]
        digest = int.from_bytes(
            hashlib.sha256("\x1f".join(window).encode("utf-8")).digest()[:8],
            "big",
        )
        if digest in present:
            continue
        if len(heap) < policy.minhash_width:
            heapq.heappush(heap, -digest)
            present.add(digest)
        elif digest < -heap[0]:
            removed = -heapq.heapreplace(heap, -digest)
            present.remove(removed)
            present.add(digest)
    if not present and text:
        present.add(int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:8], "big"))
    return tuple(sorted(present))


def _signature_buckets(signature: tuple[int, ...], character_length: int) -> tuple[str, ...]:
    if not signature:
        return ()
    size_band = int(math.log2(max(1, character_length)) * 4)
    buckets: list[str] = [
        f"s:{size_band}:{value:016x}" for value in signature[:8]
    ]
    for ordinal in range(0, len(signature), 4):
        band = signature[ordinal : ordinal + 4]
        if len(band) < 2:
            break
        digest = hashlib.sha256(
            (f"{size_band}:" + ":".join(str(value) for value in band)).encode("ascii")
        ).hexdigest()
        buckets.append(f"{ordinal // 4:02d}:{digest}")
    return tuple(buckets)


@dataclass(frozen=True, slots=True)
class InventoryPlan:
    source_root_identity_digest: str
    source_tree_digest: str
    directories: int
    objects: int
    regular_files: int
    symlinks: int
    special_files: int
    bytes_observed: int
    estimated_bundle_bytes: int
    run_id: str
    plan_digest: str


class CorpusInventoryEngine:
    """Stream one immutable source snapshot into a canonical derived bundle."""

    def __init__(
        self,
        *,
        source_root: Path,
        bundle_parent: Path,
        device_reference: str,
        starting_head: str,
        policy: CorpusInventoryPolicy | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        progress: Callable[[Mapping[str, int]], None] | None = None,
    ) -> None:
        self.source_root = source_root
        self.bundle_parent = bundle_parent
        self.device_reference = device_reference
        self.starting_head = starting_head
        self.policy = policy or CorpusInventoryPolicy()
        self.clock = clock
        self.progress = progress or (lambda _value: None)
        self._root_stat = self._verify_roots()
        self.source_root_identity_digest = canonical_sha256(
            {
                "device_reference": device_reference,
                "root_device": self._root_stat.st_dev,
                "root_inode": self._root_stat.st_ino,
                "root_name_digest": sha256_hex(self.source_root.name),
            }
        )
        self.run_id = "step14-" + canonical_sha256(
            {
                "source_root_identity_digest": self.source_root_identity_digest,
                "policy_digest": self.policy.policy_digest,
                "starting_head": starting_head,
            }
        )[:32]
        self.bundle_root = bundle_parent / self.run_id

    def _verify_roots(self) -> os.stat_result:
        try:
            if self.source_root.resolve(strict=True) != self.source_root:
                raise CorpusSafetyError(
                    "source root contains a symlinked path component",
                    sanitized_code="UNSAFE_CORPUS_ROOT",
                )
            source = self.source_root.lstat()
            destination_path = (
                self.bundle_parent
                if os.path.lexists(self.bundle_parent)
                else self.bundle_parent.parent
            )
            if destination_path.resolve(strict=True) != destination_path:
                raise CorpusSafetyError(
                    "derived-data parent contains a symlinked path component",
                    sanitized_code="UNSAFE_BUNDLE_ROOT",
                )
            destination = destination_path.lstat()
        except OSError as exc:
            raise CorpusSafetyError(
                "source or derived-data parent is unavailable",
                sanitized_code="CORPUS_ROOT_UNAVAILABLE",
            ) from exc
        if self.source_root.is_symlink() or not stat.S_ISDIR(source.st_mode):
            raise CorpusSafetyError(
                "source root is not a direct directory",
                sanitized_code="UNSAFE_CORPUS_ROOT",
            )
        if (
            destination_path.is_symlink()
            or not stat.S_ISDIR(destination.st_mode)
            or (
                destination_path != self.bundle_parent
                and (
                    not self.bundle_parent.name
                    or self.bundle_parent.name in {".", ".."}
                    or "/" in self.bundle_parent.name
                )
            )
        ):
            raise CorpusSafetyError(
                "bundle parent is not a direct directory",
                sanitized_code="UNSAFE_BUNDLE_ROOT",
            )
        if source.st_dev != destination.st_dev:
            raise CorpusSafetyError(
                "source and approved derived-data parent differ by filesystem",
                sanitized_code="EXTERNAL_FILESYSTEM_BOUNDARY_MISMATCH",
            )
        for path, metadata in ((self.source_root, source), (destination_path, destination)):
            flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(path, flags)
            try:
                opened = os.fstat(descriptor)
                if opened.st_dev != metadata.st_dev or opened.st_ino != metadata.st_ino:
                    raise CorpusSafetyError(
                        "verified directory changed during open",
                        sanitized_code="CORPUS_DIRECTORY_RACE",
                    )
            finally:
                os.close(descriptor)
        return source

    def _walk(self) -> Iterator[tuple[Path, str, os.stat_result, FileKind]]:
        stack = [(self.source_root, self._root_stat)]
        while stack:
            directory, expected_directory = stack.pop()
            descriptor: int | None = None
            try:
                descriptor = os.open(
                    directory,
                    os.O_RDONLY
                    | os.O_CLOEXEC
                    | getattr(os, "O_DIRECTORY", 0)
                    | getattr(os, "O_NOFOLLOW", 0),
                )
                opened_directory = os.fstat(descriptor)
                if (
                    not stat.S_ISDIR(opened_directory.st_mode)
                    or opened_directory.st_dev != expected_directory.st_dev
                    or opened_directory.st_ino != expected_directory.st_ino
                ):
                    raise CorpusSafetyError(
                        "source directory identity changed during traversal",
                        sanitized_code="CORPUS_DIRECTORY_RACE",
                    )
                observed_entries: list[
                    tuple[str, os.stat_result | None]
                ] = []
                with os.scandir(descriptor) as entries:
                    ordered = sorted(entries, key=lambda entry: os.fsencode(entry.name))
                    for entry in ordered:
                        try:
                            metadata = entry.stat(follow_symlinks=False)
                        except OSError:
                            metadata = None
                        observed_entries.append((entry.name, metadata))
            except CorpusSafetyError:
                raise
            except OSError as exc:
                if directory == self.source_root:
                    raise CorpusSafetyError(
                        "source root cannot be enumerated",
                        sanitized_code="CORPUS_ROOT_UNREADABLE",
                    ) from exc
                relative = _safe_relative(directory, self.source_root)
                yield directory, relative, directory.lstat(), FileKind.UNREADABLE
                continue
            finally:
                if descriptor is not None:
                    os.close(descriptor)
            directories: list[tuple[Path, os.stat_result]] = []
            for entry_name, metadata in observed_entries:
                path = directory / entry_name
                relative = _safe_relative(path, self.source_root)
                if metadata is None:
                    yield path, relative, os.stat_result((0,) * 10), FileKind.UNREADABLE
                    continue
                kind = _mode_kind(metadata.st_mode)
                if metadata.st_dev != self._root_stat.st_dev:
                    yield path, relative, metadata, FileKind.OTHER_SPECIAL
                    continue
                yield path, relative, metadata, kind
                if kind is FileKind.DIRECTORY:
                    directories.append((path, metadata))
            stack.extend(reversed(directories))

    def tree_fingerprint(self) -> tuple[str, dict[str, int]]:
        digest = hashlib.sha256()
        counts = Counter({"directories": 1})
        root_identity = _stat_identity_digest(".", self._root_stat)
        digest.update(root_identity.encode("ascii"))
        for _path, relative, metadata, kind in self._walk():
            digest.update(_stat_identity_digest(relative, metadata).encode("ascii"))
            if kind is FileKind.DIRECTORY:
                counts["directories"] += 1
            else:
                counts["objects"] += 1
            if kind is FileKind.REGULAR:
                counts["regular_files"] += 1
                counts["bytes"] += metadata.st_size
            elif kind is FileKind.SYMLINK:
                counts["symlinks"] += 1
            elif kind is FileKind.UNREADABLE:
                counts["unreadable"] += 1
            elif kind is not FileKind.DIRECTORY:
                counts["special"] += 1
        return digest.hexdigest(), dict(counts)

    def plan(self) -> InventoryPlan:
        tree_digest, counts = self.tree_fingerprint()
        estimated = max(4 * 1024 * 1024, counts.get("objects", 0) * 4096)
        values = {
            "source_root_identity_digest": self.source_root_identity_digest,
            "source_tree_digest": tree_digest,
            "directories": counts.get("directories", 0),
            "objects": counts.get("objects", 0),
            "regular_files": counts.get("regular_files", 0),
            "symlinks": counts.get("symlinks", 0),
            "special_files": counts.get("special", 0),
            "bytes_observed": counts.get("bytes", 0),
            "estimated_bundle_bytes": estimated,
            "run_id": self.run_id,
        }
        return InventoryPlan(**values, plan_digest=canonical_sha256(values))

    @staticmethod
    def _connect(path: Path) -> sqlite3.Connection:
        connection = sqlite3.connect(path)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS records(
              relative_path TEXT PRIMARY KEY,
              record_id TEXT NOT NULL UNIQUE,
              stat_digest TEXT NOT NULL,
              file_kind TEXT NOT NULL,
              byte_size INTEGER,
              raw_sha256 TEXT,
              normalized_sha256 TEXT,
              normalized_length INTEGER,
              media_type TEXT,
              source_family TEXT,
              license_status TEXT NOT NULL,
              privacy_status TEXT NOT NULL,
              quarantine_status TEXT NOT NULL,
              record_json TEXT NOT NULL,
              license_json TEXT NOT NULL,
              privacy_json TEXT NOT NULL,
              quarantine_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS records_raw ON records(byte_size, raw_sha256);
            CREATE INDEX IF NOT EXISTS records_normalized ON records(media_type, normalized_sha256);
            CREATE TABLE IF NOT EXISTS signatures(
              record_id TEXT PRIMARY KEY,
              signature_json TEXT NOT NULL,
              character_length INTEGER NOT NULL,
              source_family TEXT,
              official_identifier TEXT,
              version_marker TEXT
            );
            CREATE TABLE IF NOT EXISTS buckets(
              bucket_key TEXT NOT NULL,
              record_id TEXT NOT NULL,
              PRIMARY KEY(bucket_key, record_id)
            );
            CREATE INDEX IF NOT EXISTS buckets_key ON buckets(bucket_key);
            CREATE TABLE IF NOT EXISTS law_metadata(
              law_record_path TEXT PRIMARY KEY,
              logical_source_id TEXT NOT NULL,
              raw_relative_path TEXT NOT NULL,
              metadata_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS law_identity ON law_metadata(logical_source_id);
            """
        )
        return connection

    def _initialize_state(self, connection: sqlite3.Connection, plan: InventoryPlan) -> tuple[datetime, int]:
        existing = dict(connection.execute("SELECT key, value FROM metadata"))
        if existing:
            expected = {
                "policy_digest": self.policy.policy_digest,
                "source_root_identity_digest": self.source_root_identity_digest,
                "source_tree_before_digest": plan.source_tree_digest,
                "starting_head": self.starting_head,
                "run_id": self.run_id,
            }
            for key, value in expected.items():
                if existing.get(key) != value:
                    raise CorpusReplayConflictError(
                        "inventory checkpoint belongs to different immutable facts",
                        sanitized_code="INCOMPATIBLE_CHECKPOINT",
                    )
            started_at = datetime.fromisoformat(existing["started_at"])
            resume_count = int(existing.get("resume_count", "0")) + 1
            connection.execute("UPDATE metadata SET value=? WHERE key='resume_count'", (str(resume_count),))
            connection.commit()
            return started_at, resume_count
        started_at = self.clock().astimezone(UTC)
        values = {
            "policy_digest": self.policy.policy_digest,
            "source_root_identity_digest": self.source_root_identity_digest,
            "source_tree_before_digest": plan.source_tree_digest,
            "starting_head": self.starting_head,
            "run_id": self.run_id,
            "started_at": started_at.isoformat(),
            "resume_count": "0",
        }
        connection.executemany("INSERT INTO metadata(key,value) VALUES(?,?)", sorted(values.items()))
        connection.commit()
        return started_at, 0

    def _hash_regular(
        self, path: Path, relative: str, expected: os.stat_result
    ) -> tuple[str | None, bytes, bytes, StabilityStatus, tuple[str, ...]]:
        digest = hashlib.sha256()
        supported = _file_extension(relative) in {".json", ".txt"}
        payload = bytearray()
        prefix = bytearray()
        findings: list[str] = []
        flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags)
        except OSError:
            return None, b"", b"", StabilityStatus.NOT_HASHED, ("UNREADABLE_FILE",)
        try:
            opened = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_dev != expected.st_dev
                or opened.st_ino != expected.st_ino
                or opened.st_size != expected.st_size
                or opened.st_mtime_ns != expected.st_mtime_ns
            ):
                return None, b"", b"", StabilityStatus.UNSTABLE, ("UNSTABLE_DURING_HASH",)
            total = 0
            while True:
                chunk = os.read(descriptor, self.policy.hash_chunk_bytes)
                if not chunk:
                    break
                total += len(chunk)
                digest.update(chunk)
                if supported and total <= self.policy.maximum_supported_parse_bytes:
                    payload.extend(chunk)
                if len(prefix) < self.policy.maximum_signal_scan_bytes:
                    prefix.extend(chunk[: self.policy.maximum_signal_scan_bytes - len(prefix)])
            after = os.fstat(descriptor)
        except OSError:
            return None, b"", bytes(prefix), StabilityStatus.NOT_HASHED, ("HASH_FAILURE",)
        finally:
            os.close(descriptor)
        try:
            final = path.lstat()
        except OSError:
            return None, b"", bytes(prefix), StabilityStatus.UNSTABLE, ("UNSTABLE_DURING_HASH",)
        for metadata in (after, final):
            if (
                metadata.st_dev != expected.st_dev
                or metadata.st_ino != expected.st_ino
                or metadata.st_size != expected.st_size
                or metadata.st_mtime_ns != expected.st_mtime_ns
            ):
                return None, b"", bytes(prefix), StabilityStatus.UNSTABLE, ("UNSTABLE_DURING_HASH",)
        if total != expected.st_size:
            return None, b"", bytes(prefix), StabilityStatus.UNSTABLE, ("INPUT_LENGTH_MISMATCH",)
        if supported and expected.st_size > self.policy.maximum_supported_parse_bytes:
            findings.append("STEP11_RESOURCE_LIMIT_EXCEEDED")
            payload.clear()
        return digest.hexdigest(), bytes(payload), bytes(prefix), StabilityStatus.STABLE, tuple(findings)

    @staticmethod
    def _law_metadata(relative: str, value: Any) -> tuple[str, str, dict[str, Any]] | None:
        if not relative.endswith("/law_record.json") or not isinstance(value, dict):
            return None
        required = {
            "record_id", "document_family_id", "source_sha256", "content_sha256",
            "raw_source_path", "source_url", "language", "jurisdiction_layer",
            "license_or_reuse_basis", "verification_state",
        }
        if not required.issubset(value):
            return None
        prefix, marker, _tail = relative.partition("20_BULK_NORMALIZED_CORPUS/")
        if not marker:
            return None
        raw_relative = prefix + str(value["raw_source_path"])
        logical = str(value["document_family_id"] or value["record_id"])
        bounded = {
            key: value.get(key)
            for key in (
                "record_id", "document_family_id", "source_sha256", "content_sha256",
                "raw_source_path", "normalized_source_path", "source_url", "source_format",
                "language", "jurisdiction_layer", "issuing_authority", "source_authority_tier",
                "binding_status", "evidence_tier", "official_title", "short_title",
                "abbreviation", "fna_identifier", "eli_identifier", "official_citation",
                "version_status", "currentness_status", "source_retrieved_at", "parser_name",
                "parser_version", "normalization_version", "provision_count",
                "license_or_reuse_basis", "verification_state", "temporal_data_limitations",
                "ausfertigung_datum", "promulgation_date", "publication_date",
                "effective_from", "effective_to", "repeal_date",
            )
        }
        return logical, raw_relative, bounded

    def _scan_one(
        self,
        connection: sqlite3.Connection,
        path: Path,
        relative: str,
        metadata: os.stat_result,
        kind: FileKind,
    ) -> None:
        path_digest = _path_digest(relative)
        raw_digest: str | None = None
        normalized_digest: str | None = None
        normalized_length: int | None = None
        parser_status = ParserSupportStatus.NOT_APPLICABLE
        payload = b""
        prefix = b""
        stability = StabilityStatus.NOT_HASHED
        findings: list[str] = []
        extension = _file_extension(relative)
        media_type = _media_type(extension)
        family = _source_family(relative)
        official_identifier: str | None = None
        parsed_text: str | None = None
        json_value: Any = None

        if kind is FileKind.REGULAR:
            raw_digest, payload, prefix, stability, hash_findings = self._hash_regular(path, relative, metadata)
            findings.extend(hash_findings)
            if raw_digest is not None and extension in {".json", ".txt"}:
                if not payload and metadata.st_size > 0:
                    parser_status = ParserSupportStatus.MALFORMED_SUPPORTED_FORMAT
                else:
                    try:
                        limits = ResourceLimits(maximum_input_bytes=self.policy.maximum_supported_parse_bytes)
                        parsed = (
                            parse_json_document(
                                payload,
                                expected_sha256=raw_digest,
                                expected_length=metadata.st_size,
                                limits=limits,
                            )
                            if extension == ".json"
                            else parse_plain_text(
                                payload,
                                expected_sha256=raw_digest,
                                expected_length=metadata.st_size,
                                limits=limits,
                            )
                        )
                        parser_status = ParserSupportStatus.STEP11_SUPPORTED
                        normalized_digest = parsed.normalized.normalized_sha256
                        normalized_length = len(parsed.rendered_text)
                        parsed_text = parsed.rendered_text
                        if extension == ".json":
                            json_value = _strict_json_value(payload)
                    except (ParsingError, UnicodeError, ValueError, RecursionError) as exc:
                        parser_status = ParserSupportStatus.MALFORMED_SUPPORTED_FORMAT
                        findings.append(getattr(exc, "sanitized_code", "CORRUPT_OR_MALFORMED"))
            elif kind is FileKind.REGULAR:
                parser_status = ParserSupportStatus.UNSUPPORTED_FORMAT
                findings.append("UNSUPPORTED_FORMAT")
        elif kind is FileKind.SYMLINK:
            findings.append("SYMLINK_NOT_FOLLOWED")
        elif kind is FileKind.UNREADABLE:
            findings.append("UNREADABLE_FILE")
        else:
            findings.append("SPECIAL_FILE_NOT_OPENED")

        law_metadata = self._law_metadata(relative, json_value)
        if law_metadata is not None:
            official_identifier = law_metadata[0]

        location_digests: list[str] = []
        secret_rules: list[str] = []
        privacy_rules: list[str] = []
        for rule, pattern in _SECRET_RULES:
            for match in pattern.finditer(prefix):
                secret_rules.append(rule)
                location_digests.append(canonical_sha256({"path_digest": path_digest, "rule": rule, "offset": match.start()}))
                if len(location_digests) >= 32:
                    break
        if not secret_rules:
            for rule, pattern in _PRIVACY_RULES:
                for match in pattern.finditer(prefix):
                    privacy_rules.append(rule)
                    location_digests.append(canonical_sha256({"path_digest": path_digest, "rule": rule, "offset": match.start()}))
                    if len(location_digests) >= 32:
                        break

        known_official = family in {
            "GESETZE_IM_INTERNET_OFFICIAL_CONSOLIDATED_RAW",
            "GESETZE_IM_INTERNET_DERIVED_NORMALIZED",
            "GESETZE_IM_INTERNET_SOURCE_MANIFEST",
            "SOURCE_AUTHORITY_REGISTRY_EVIDENCE",
            "LICENSE_AND_REUSE_EVIDENCE",
            "CORPUS_QA_EVIDENCE",
        }
        license_assessment = LicenseAssessment(
            InventoryLicenseStatus.DECLARED if known_official else InventoryLicenseStatus.UNKNOWN,
            ("EXPLICIT_LIBRARY_SOURCE_AND_REUSE_LEDGER",) if known_official else ("NO_FILE_BOUND_LICENSE_EVIDENCE",),
        )
        if secret_rules:
            privacy_status = InventoryPrivacyStatus.POTENTIALLY_SENSITIVE
            privacy_rule_ids = tuple(secret_rules)
        elif privacy_rules:
            privacy_status = InventoryPrivacyStatus.POTENTIALLY_SENSITIVE
            privacy_rule_ids = tuple(privacy_rules)
        elif known_official:
            privacy_status = InventoryPrivacyStatus.PUBLIC
            privacy_rule_ids = ("OFFICIAL_PUBLIC_LEGAL_LIBRARY_CLASS",)
        else:
            privacy_status = InventoryPrivacyStatus.UNKNOWN
            privacy_rule_ids = ("NO_DETERMINISTIC_PUBLIC_CLASSIFICATION",)
        privacy_assessment = PrivacyAssessment(
            privacy_status,
            privacy_rule_ids,
            len(secret_rules) + len(privacy_rules),
            tuple(location_digests),
        )

        reasons: list[QuarantineReason] = []
        quarantine_status = QuarantineStatus.CLEAR
        if kind is FileKind.SYMLINK:
            quarantine_status = QuarantineStatus.QUARANTINED
            reasons.append(QuarantineReason.SYMLINK)
        elif kind is FileKind.UNREADABLE:
            quarantine_status = QuarantineStatus.QUARANTINED
            reasons.append(QuarantineReason.UNREADABLE_FILE)
        elif kind is not FileKind.REGULAR:
            quarantine_status = QuarantineStatus.QUARANTINED
            reasons.append(QuarantineReason.SPECIAL_FILE)
        elif stability is StabilityStatus.UNSTABLE:
            quarantine_status = QuarantineStatus.QUARANTINED
            reasons.append(QuarantineReason.UNSTABLE_DURING_HASH)
        elif stability is StabilityStatus.NOT_HASHED:
            quarantine_status = QuarantineStatus.QUARANTINED
            reasons.append(QuarantineReason.HASH_FAILURE)
        elif parser_status is ParserSupportStatus.MALFORMED_SUPPORTED_FORMAT:
            quarantine_status = QuarantineStatus.QUARANTINED
            reasons.append(QuarantineReason.CORRUPT_OR_MALFORMED)
        elif secret_rules:
            quarantine_status = QuarantineStatus.QUARANTINED
            reasons.append(QuarantineReason.SECRET_OR_CREDENTIAL_SIGNAL)
        elif not known_official:
            quarantine_status = QuarantineStatus.REVIEW_REQUIRED
            reasons.extend((QuarantineReason.LICENSE_UNKNOWN, QuarantineReason.UNKNOWN_PROVENANCE))
        quarantine = QuarantineDecision(quarantine_status, tuple(reasons))

        record = CorpusFileRecord(
            source_root_identity_digest=self.source_root_identity_digest,
            relative_path=relative,
            path_digest=path_digest,
            file_kind=kind,
            stability_status=stability,
            byte_size=metadata.st_size if kind is FileKind.REGULAR else None,
            mtime_ns=metadata.st_mtime_ns if metadata.st_mtime_ns >= 0 else None,
            raw_sha256=raw_digest,
            normalized_sha256=normalized_digest,
            normalized_character_length=normalized_length,
            extension=extension,
            media_type_candidate=media_type,
            parser_support_status=parser_status,
            source_family_candidate=family,
            official_identifier_candidate=official_identifier,
            license_assessment_digest=license_assessment.assessment_digest,
            privacy_assessment_digest=privacy_assessment.assessment_digest,
            quarantine_decision_digest=quarantine.decision_digest,
            findings=tuple(findings),
        )
        record_json = canonical_json_bytes(record).decode("utf-8")
        connection.execute(
            """INSERT INTO records(
                 relative_path,record_id,stat_digest,file_kind,byte_size,raw_sha256,
                 normalized_sha256,normalized_length,media_type,source_family,
                 license_status,privacy_status,quarantine_status,record_json,
                 license_json,privacy_json,quarantine_json
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                relative, record.record_id, _stat_identity_digest(relative, metadata),
                kind.value, record.byte_size, raw_digest, normalized_digest,
                normalized_length, media_type, family, license_assessment.status.value,
                privacy_assessment.status.value, quarantine.status.value, record_json,
                canonical_json_bytes(license_assessment).decode("utf-8"),
                canonical_json_bytes(privacy_assessment).decode("utf-8"),
                canonical_json_bytes(quarantine).decode("utf-8"),
            ),
        )
        if parsed_text is not None and normalized_digest is not None:
            signature = _bottom_hashes(parsed_text, self.policy)
            version_marker = None
            if isinstance(json_value, dict):
                version_marker = str(json_value.get("version_status") or json_value.get("schema_version") or "") or None
            connection.execute(
                "INSERT INTO signatures VALUES(?,?,?,?,?,?)",
                (record.record_id, json.dumps(signature), len(parsed_text), family, official_identifier, version_marker),
            )
            for bucket in _signature_buckets(signature, len(parsed_text)):
                connection.execute("INSERT INTO buckets VALUES(?,?)", (bucket, record.record_id))
        if law_metadata is not None:
            logical, raw_relative, value = law_metadata
            connection.execute(
                "INSERT INTO law_metadata VALUES(?,?,?,?)",
                (relative, logical, raw_relative, json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)),
            )

    @staticmethod
    def _publish_no_replace(temporary: Path, target: Path) -> None:
        """Publish one owned temporary file without a rename-overwrite race."""

        try:
            os.link(temporary, target, follow_symlinks=False)
        except FileExistsError as exc:
            temporary.unlink(missing_ok=True)
            raise CorpusReplayConflictError(
                "derived artifact appeared during atomic publish",
                sanitized_code="BUNDLE_REPLAY_CONFLICT",
            ) from exc
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            raise CorpusSafetyError(
                "derived artifact no-overwrite publish failed",
                sanitized_code="BUNDLE_ATOMIC_PUBLISH_FAILED",
            ) from exc
        try:
            temporary.unlink()
            directory_fd = os.open(
                target.parent,
                os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_DIRECTORY", 0),
            )
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError as exc:
            raise CorpusSafetyError(
                "derived artifact publish durability failed",
                sanitized_code="BUNDLE_ATOMIC_PUBLISH_FAILED",
            ) from exc

    @staticmethod
    def _write_atomic_absent(path: Path, payload: bytes) -> None:
        if path.exists():
            if (
                path.is_symlink()
                or not path.is_file()
                or CorpusInventoryEngine._read_bounded_regular(
                    path,
                    maximum_bytes=len(payload),
                )
                != payload
            ):
                raise CorpusReplayConflictError(
                    "existing derived artifact differs from canonical output",
                    sanitized_code="BUNDLE_REPLAY_CONFLICT",
                )
            return
        temporary = path.with_name(f".{path.name}.{os.getpid()}.part")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(temporary, flags, 0o640)
        try:
            offset = 0
            while offset < len(payload):
                offset += os.write(descriptor, payload[offset:])
            os.fsync(descriptor)
        except BaseException:
            os.close(descriptor)
            temporary.unlink(missing_ok=True)
            raise
        else:
            os.close(descriptor)
        CorpusInventoryEngine._publish_no_replace(temporary, path)

    def _write_query_jsonl(
        self, connection: sqlite3.Connection, name: str, query: str
    ) -> tuple[int, str]:
        target = self.bundle_root / name
        temporary = target.with_name(f".{target.name}.{os.getpid()}.part")
        if target.exists():
            expected_digest = hashlib.sha256()
            expected_length = 0
            for (serialized,) in connection.execute(query):
                line = serialized.encode("utf-8") + b"\n"
                expected_digest.update(line)
                expected_length += len(line)
            if (
                target.is_symlink()
                or not target.is_file()
                or target.stat().st_size != expected_length
                or self._sha256_file(target) != expected_digest.hexdigest()
            ):
                raise CorpusReplayConflictError(
                    "existing JSONL output conflicts with checkpoint state",
                    sanitized_code="BUNDLE_REPLAY_CONFLICT",
                )
            return expected_length, expected_digest.hexdigest()
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o640)
        digest = hashlib.sha256()
        length = 0
        try:
            for (serialized,) in connection.execute(query):
                line = serialized.encode("utf-8") + b"\n"
                offset = 0
                while offset < len(line):
                    offset += os.write(descriptor, line[offset:])
                digest.update(line)
                length += len(line)
            os.fsync(descriptor)
        except BaseException:
            os.close(descriptor)
            temporary.unlink(missing_ok=True)
            raise
        else:
            os.close(descriptor)
        self._publish_no_replace(temporary, target)
        return length, digest.hexdigest()

    @staticmethod
    def _read_bounded_regular(path: Path, *, maximum_bytes: int) -> bytes:
        descriptor: int | None = None
        try:
            descriptor = os.open(
                path,
                os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
            )
            opened = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_size < 0
                or opened.st_size > maximum_bytes
            ):
                raise CorpusReplayConflictError(
                    "derived artifact is not a bounded regular file",
                    sanitized_code="BUNDLE_DIGEST_MISMATCH",
                )
            payload = bytearray()
            while len(payload) < opened.st_size:
                chunk = os.read(descriptor, min(1024 * 1024, opened.st_size - len(payload)))
                if not chunk:
                    break
                payload.extend(chunk)
            after = os.fstat(descriptor)
        except OSError as exc:
            raise CorpusReplayConflictError(
                "derived artifact could not be read safely",
                sanitized_code="BUNDLE_DIGEST_MISMATCH",
            ) from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)
        if (
            len(payload) != opened.st_size
            or opened.st_dev != after.st_dev
            or opened.st_ino != after.st_ino
            or opened.st_size != after.st_size
            or opened.st_mtime_ns != after.st_mtime_ns
        ):
            raise CorpusReplayConflictError(
                "derived artifact changed during read",
                sanitized_code="BUNDLE_DIGEST_MISMATCH",
            )
        return bytes(payload)

    @staticmethod
    def _sha256_file(path: Path) -> str:
        digest = hashlib.sha256()
        descriptor: int | None = None
        try:
            descriptor = os.open(
                path,
                os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
            )
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode):
                raise CorpusReplayConflictError(
                    "derived artifact is not a regular file",
                    sanitized_code="BUNDLE_DIGEST_MISMATCH",
                )
            observed = 0
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
                observed += len(chunk)
            after = os.fstat(descriptor)
        except OSError as exc:
            raise CorpusReplayConflictError(
                "derived artifact could not be hashed safely",
                sanitized_code="BUNDLE_DIGEST_MISMATCH",
            ) from exc
        finally:
            if descriptor is not None:
                os.close(descriptor)
        if (
            observed != opened.st_size
            or opened.st_dev != after.st_dev
            or opened.st_ino != after.st_ino
            or opened.st_size != after.st_size
            or opened.st_mtime_ns != after.st_mtime_ns
        ):
            raise CorpusReplayConflictError(
                "derived artifact changed during hash",
                sanitized_code="BUNDLE_DIGEST_MISMATCH",
            )
        return digest.hexdigest()

    def _materialize_groups(self, connection: sqlite3.Connection) -> dict[str, int]:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS exact_groups(group_id TEXT PRIMARY KEY, group_json TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS normalized_groups(group_id TEXT PRIMARY KEY, group_json TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS near_candidates(candidate_id TEXT PRIMARY KEY, candidate_json TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS registration_candidates(candidate_id TEXT PRIMARY KEY, candidate_json TEXT NOT NULL, disposition TEXT NOT NULL);
            """
        )
        exact_members = 0
        duplicate_bytes = 0
        for byte_size, raw_sha256 in connection.execute(
            "SELECT byte_size,raw_sha256 FROM records WHERE raw_sha256 IS NOT NULL GROUP BY byte_size,raw_sha256 HAVING count(*)>1 ORDER BY raw_sha256,byte_size"
        ):
            rows = list(connection.execute(
                "SELECT record_id,path_digest,source_family,license_status,privacy_status FROM (SELECT record_id,json_extract(record_json,'$.path_digest') AS path_digest,source_family,license_status,privacy_status FROM records) WHERE record_id IN (SELECT record_id FROM records WHERE byte_size=? AND raw_sha256=?) ORDER BY record_id",
                (byte_size, raw_sha256),
            ))
            group = ExactDuplicateGroup(
                raw_sha256, byte_size,
                tuple(row[0] for row in rows), tuple(row[1] for row in rows),
                len({row[2] for row in rows}) > 1,
                len({row[3] for row in rows}) > 1,
                len({row[4] for row in rows}) > 1,
            )
            connection.execute("INSERT OR REPLACE INTO exact_groups VALUES(?,?)", (group.group_id, canonical_json_bytes(group).decode("utf-8")))
            exact_members += len(rows)
            duplicate_bytes += group.informational_reclaimable_bytes

        for media_type, normalized_sha256 in connection.execute(
            "SELECT media_type,normalized_sha256 FROM records WHERE normalized_sha256 IS NOT NULL GROUP BY media_type,normalized_sha256 HAVING count(*)>1 ORDER BY media_type,normalized_sha256"
        ):
            rows = list(connection.execute(
                "SELECT record_id,raw_sha256 FROM records WHERE media_type=? AND normalized_sha256=? ORDER BY record_id",
                (media_type, normalized_sha256),
            ))
            group = NormalizedDuplicateGroup(media_type, normalized_sha256, tuple(row[0] for row in rows), tuple(row[1] for row in rows))
            connection.execute("INSERT OR REPLACE INTO normalized_groups VALUES(?,?)", (group.group_id, canonical_json_bytes(group).decode("utf-8")))

        seen_pairs: set[tuple[str, str]] = set()
        for bucket_key, count in connection.execute(
            "SELECT bucket_key,count(*) FROM buckets GROUP BY bucket_key HAVING count(*)>1 ORDER BY bucket_key"
        ):
            if count > self.policy.maximum_bucket_members:
                continue
            rows = list(connection.execute(
                """SELECT s.record_id,s.signature_json,s.source_family,s.official_identifier,
                          s.version_marker,r.normalized_sha256
                     FROM signatures s JOIN buckets b ON b.record_id=s.record_id
                     JOIN records r ON r.record_id=s.record_id
                    WHERE b.bucket_key=? ORDER BY s.record_id""",
                (bucket_key,),
            ))
            for left_index in range(len(rows)):
                for right_index in range(left_index + 1, len(rows)):
                    left, right = rows[left_index], rows[right_index]
                    pair = (left[0], right[0])
                    if pair in seen_pairs or left[5] == right[5]:
                        continue
                    left_sig = set(json.loads(left[1]))
                    right_sig = set(json.loads(right[1]))
                    union = left_sig | right_sig
                    similarity = int(1_000_000 * len(left_sig & right_sig) / len(union)) if union else 0
                    identifier_match = left[3] is not None and left[3] == right[3]
                    if similarity < self.policy.near_similarity_threshold_millionths and not identifier_match:
                        continue
                    reasons = ["BOUNDED_MINHASH_SIMILARITY"]
                    if identifier_match:
                        reasons.append("OFFICIAL_IDENTIFIER_MATCH")
                    candidate = NearDuplicateCandidateGroup(
                        left[0], right[0], self.policy.near_duplicate_algorithm,
                        similarity, tuple(reasons), left[2] != right[2],
                        identifier_match and left[4] != right[4],
                    )
                    connection.execute("INSERT OR IGNORE INTO near_candidates VALUES(?,?)", (candidate.candidate_id, canonical_json_bytes(candidate).decode("utf-8")))
                    seen_pairs.add(pair)
                    if len(seen_pairs) >= self.policy.maximum_near_candidates:
                        break
                if len(seen_pairs) >= self.policy.maximum_near_candidates:
                    break
            if len(seen_pairs) >= self.policy.maximum_near_candidates:
                break

        registration_conflicts = 0
        identities = dict(connection.execute("SELECT logical_source_id,count(*) FROM law_metadata GROUP BY logical_source_id"))
        for law_path, logical, raw_path, metadata_json in connection.execute(
            "SELECT law_record_path,logical_source_id,raw_relative_path,metadata_json FROM law_metadata ORDER BY logical_source_id,law_record_path"
        ):
            metadata = json.loads(metadata_json)
            law_row = connection.execute("SELECT record_id,normalized_sha256 FROM records WHERE relative_path=?", (law_path,)).fetchone()
            raw_row = connection.execute("SELECT record_id,raw_sha256 FROM records WHERE relative_path=?", (raw_path,)).fetchone()
            declared_source = metadata.get("source_sha256")
            declared_normalized = metadata.get("content_sha256")
            reasons = [
                "OFFICIAL_CONSOLIDATED_TEXT_NOT_AUTHENTIC_PROMULGATION",
                "STEP13_SOURCE_CLASS_REUSED",
                "STEP9_PUBLICATION_BOUNDARY_PRESERVED",
                "NORMALIZED_PROJECTION_AVAILABLE",
            ]
            disposition = RegistrationDisposition.READY_FOR_REGISTRATION
            if identities.get(logical, 0) != 1:
                disposition = RegistrationDisposition.QUARANTINED
                reasons.append("SOURCE_IDENTITY_CONFLICT")
                registration_conflicts += 1
            if raw_row is None or raw_row[1] is None or raw_row[1] != declared_source:
                disposition = RegistrationDisposition.QUARANTINED
                reasons.append("RAW_SOURCE_DIGEST_MISMATCH")
                registration_conflicts += 1
            if law_row is None or law_row[1] is None:
                disposition = RegistrationDisposition.QUARANTINED
                reasons.append("NORMALIZED_METADATA_MISSING")
                registration_conflicts += 1
            if metadata.get("verification_state") != "schema_validated":
                disposition = RegistrationDisposition.REVIEW_REQUIRED
                reasons.append("PRODUCER_VERIFICATION_STATE_REVIEW")
            if not metadata.get("license_or_reuse_basis"):
                disposition = RegistrationDisposition.REVIEW_REQUIRED
                reasons.append("LICENSE_UNKNOWN")
            content_digest = declared_source if isinstance(declared_source, str) and re.fullmatch(r"[0-9a-f]{64}", declared_source) else (raw_row[1] if raw_row else "0" * 64)
            normalized_value = declared_normalized if isinstance(declared_normalized, str) and re.fullmatch(r"[0-9a-f]{64}", declared_normalized) else None
            alias_digests = tuple(sorted(
                canonical_sha256({"relative_path_digest": _path_digest(value)})
                for value in (law_path, raw_path)
            ))
            candidate = SourceRegistrationCandidate(
                self.run_id,
                "de-federal-gii-" + re.sub(r"[^a-z0-9]+", "-", logical.casefold()).strip("-"),
                content_digest,
                normalized_value,
                "gii:" + logical,
                "german-law-global-1a",
                "DE_FEDERAL_OFFICIAL_CONSOLIDATED_LAW",
                "AUTHORITATIVE_SECONDARY",
                "CONFIRMED_PERMISSIVE" if metadata.get("license_or_reuse_basis") else "UNKNOWN",
                "PUBLIC",
                "NOT_REQUIRED",
                "DE_FEDERAL" if metadata.get("jurisdiction_layer") == "DE_FEDERAL" else None,
                metadata.get("language") if metadata.get("language") in {"de", "en"} else None,
                logical,
                alias_digests,
                ParserSupportStatus.STEP11_SUPPORTED,
                disposition,
                tuple(reasons),
            )
            connection.execute("INSERT OR REPLACE INTO registration_candidates VALUES(?,?,?)", (candidate.candidate_id, canonical_json_bytes(candidate).decode("utf-8"), disposition.value))
        connection.commit()
        return {
            "exact_duplicate_member_count": exact_members,
            "informational_duplicate_bytes": duplicate_bytes,
            "registration_conflict_count": registration_conflicts,
        }

    def execute(self, plan: InventoryPlan) -> tuple[CorpusInventorySummary, CorpusInventoryManifest]:
        current_plan = self.plan()
        if current_plan != plan:
            raise CorpusReplayConflictError(
                "source or plan changed after the approved planning checkpoint",
                sanitized_code="CHANGED_PLAN_INVALIDATION",
            )
        if not self.bundle_parent.exists():
            os.mkdir(self.bundle_parent, 0o750)
            parent_descriptor = os.open(
                self.bundle_parent.parent,
                os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_DIRECTORY", 0),
            )
            try:
                os.fsync(parent_descriptor)
            finally:
                os.close(parent_descriptor)
        if self.bundle_parent.is_symlink() or self.bundle_parent.lstat().st_dev != self._root_stat.st_dev:
            raise CorpusSafetyError("bundle parent changed", sanitized_code="UNSAFE_BUNDLE_ROOT")
        if self.bundle_root.exists():
            completion = self.bundle_root / "inventory-manifest.json"
            if completion.exists():
                raise CorpusReplayConflictError(
                    "completed inventory run already exists; use bundle verification",
                    sanitized_code="BUNDLE_ALREADY_COMPLETE",
                )
            if self.bundle_root.is_symlink() or not self.bundle_root.is_dir():
                raise CorpusSafetyError("bundle root is unsafe", sanitized_code="UNSAFE_BUNDLE_ROOT")
        else:
            os.mkdir(self.bundle_root, 0o750)
        checkpoints = self.bundle_root / "checkpoints"
        try:
            os.mkdir(checkpoints, 0o750)
        except FileExistsError:
            pass
        checkpoint_metadata = checkpoints.lstat()
        if (
            checkpoints.is_symlink()
            or not stat.S_ISDIR(checkpoint_metadata.st_mode)
            or checkpoint_metadata.st_dev != self._root_stat.st_dev
        ):
            raise CorpusSafetyError(
                "inventory checkpoint directory is unsafe",
                sanitized_code="UNSAFE_BUNDLE_ROOT",
            )
        state_path = checkpoints / "inventory-state.sqlite3"
        for candidate in (
            state_path,
            Path(str(state_path) + "-wal"),
            Path(str(state_path) + "-shm"),
        ):
            if not os.path.lexists(candidate):
                continue
            candidate_metadata = candidate.lstat()
            if (
                candidate.is_symlink()
                or not stat.S_ISREG(candidate_metadata.st_mode)
                or candidate_metadata.st_dev != self._root_stat.st_dev
            ):
                raise CorpusSafetyError(
                    "inventory checkpoint state is unsafe",
                    sanitized_code="UNSAFE_BUNDLE_ROOT",
                )
        with closing(self._connect(state_path)) as connection:
            started_at, resume_count = self._initialize_state(connection, plan)
            processed = 0
            for path, relative, metadata, kind in self._walk():
                if kind is FileKind.DIRECTORY:
                    continue
                existing = connection.execute("SELECT stat_digest FROM records WHERE relative_path=?", (relative,)).fetchone()
                stat_digest = _stat_identity_digest(relative, metadata)
                if existing is not None:
                    if existing[0] != stat_digest:
                        raise CorpusReplayConflictError(
                            "source changed after a checkpoint",
                            sanitized_code="SOURCE_MUTATION_DURING_SCAN",
                        )
                    continue
                self._scan_one(connection, path, relative, metadata, kind)
                processed += 1
                if processed % self.policy.checkpoint_batch_size == 0:
                    connection.commit()
                    self.progress({"processed": processed, "bytes": connection.execute("SELECT coalesce(sum(byte_size),0) FROM records").fetchone()[0]})
            connection.commit()
            group_counts = self._materialize_groups(connection)
            after_digest, after_counts = self.tree_fingerprint()
            if after_digest != plan.source_tree_digest:
                raise CorpusSafetyError(
                    "source tree changed during the controlled inventory",
                    sanitized_code="SOURCE_MUTATION_DURING_SCAN",
                )

            distributions: dict[str, dict[str, int]] = {}
            for name, column in (
                ("file_types", "coalesce(nullif(json_extract(record_json,'$.extension'),''),'[none]')"),
                ("source_families", "coalesce(source_family,'UNKNOWN')"),
                ("licenses", "license_status"),
                ("privacy", "privacy_status"),
                ("quarantine", "quarantine_status"),
                ("registration_dispositions", "disposition"),
            ):
                table = "registration_candidates" if column == "disposition" else "records"
                distributions[name] = {
                    str(key): int(count)
                    for key, count in connection.execute(f"SELECT {column},count(*) FROM {table} GROUP BY {column} ORDER BY {column}")
                }
            scalar = dict(connection.execute(
                """SELECT 'objects',count(*) FROM records UNION ALL
                    SELECT 'stable',count(*) FROM records WHERE raw_sha256 IS NOT NULL UNION ALL
                    SELECT 'bytes',coalesce(sum(byte_size),0) FROM records UNION ALL
                    SELECT 'raw',count(*) FROM records WHERE raw_sha256 IS NOT NULL UNION ALL
                    SELECT 'symlinks',count(*) FROM records WHERE file_kind='SYMLINK' UNION ALL
                    SELECT 'special',count(*) FROM records WHERE file_kind NOT IN ('REGULAR','SYMLINK','UNREADABLE') UNION ALL
                    SELECT 'unreadable',count(*) FROM records WHERE file_kind='UNREADABLE' UNION ALL
                    SELECT 'unstable',count(*) FROM records WHERE json_extract(record_json,'$.stability_status')='UNSTABLE_DURING_HASH' UNION ALL
                    SELECT 'exact_groups',count(*) FROM exact_groups UNION ALL
                    SELECT 'normalized_groups',count(*) FROM normalized_groups UNION ALL
                    SELECT 'near',count(*) FROM near_candidates UNION ALL
                    SELECT 'registration',count(*) FROM registration_candidates"""
            ))
            summary = CorpusInventorySummary(
                self.run_id, self.source_root_identity_digest,
                after_counts.get("directories", 0), int(scalar["objects"]), int(scalar["stable"]),
                int(scalar["bytes"]), int(scalar["raw"]), int(scalar["symlinks"]),
                int(scalar["special"]), int(scalar["unreadable"]), int(scalar["unstable"]),
                int(scalar["exact_groups"]), group_counts["exact_duplicate_member_count"],
                group_counts["informational_duplicate_bytes"], int(scalar["normalized_groups"]),
                int(scalar["near"]), int(scalar["registration"]),
                group_counts["registration_conflict_count"], distributions,
            )

            # Materialize assessments and records in deterministic root-relative order.
            generated: list[tuple[str, int, str]] = []
            queries = {
                "file-records.jsonl": "SELECT record_json FROM records ORDER BY relative_path",
                "path-aliases.jsonl": "SELECT json_object('alias_digest',lower(hex(sha3(raw_sha256||json_extract(record_json,'$.path_digest'),256)))) FROM records WHERE raw_sha256 IS NOT NULL ORDER BY relative_path",
                "exact-duplicate-groups.jsonl": "SELECT group_json FROM exact_groups ORDER BY group_id",
                "normalized-duplicate-groups.jsonl": "SELECT group_json FROM normalized_groups ORDER BY group_id",
                "near-duplicate-candidates.jsonl": "SELECT candidate_json FROM near_candidates ORDER BY candidate_id",
                "source-registration-candidates.jsonl": "SELECT candidate_json FROM registration_candidates ORDER BY candidate_id",
                "quarantine-candidates.jsonl": "SELECT quarantine_json FROM records WHERE quarantine_status!='CLEAR' ORDER BY relative_path",
                "license-assessments.jsonl": "SELECT license_json FROM records ORDER BY relative_path",
                "privacy-assessments.jsonl": "SELECT privacy_json FROM records ORDER BY relative_path",
            }
            # SQLite builds may omit sha3(), therefore path aliases are produced separately.
            aliases = self.bundle_root / "path-aliases.jsonl"
            alias_temporary = aliases.with_name(f".{aliases.name}.{os.getpid()}.part")
            descriptor = None if aliases.exists() else os.open(alias_temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o640)
            alias_digest = hashlib.sha256(); alias_length = 0
            try:
                for raw_sha, size, record_id, relative, record_json in connection.execute("SELECT raw_sha256,byte_size,record_id,relative_path,record_json FROM records WHERE raw_sha256 IS NOT NULL ORDER BY relative_path"):
                    parsed = json.loads(record_json)
                    alias = CorpusPathAlias(raw_sha, size, record_id, relative, parsed["path_digest"])
                    line = canonical_json_bytes(alias) + b"\n"
                    if descriptor is not None:
                        offset = 0
                        while offset < len(line):
                            offset += os.write(descriptor, line[offset:])
                    alias_digest.update(line); alias_length += len(line)
                if descriptor is not None:
                    os.fsync(descriptor)
            finally:
                if descriptor is not None:
                    os.close(descriptor)
            if descriptor is not None:
                self._publish_no_replace(alias_temporary, aliases)
            elif (
                aliases.is_symlink()
                or not aliases.is_file()
                or aliases.stat().st_size != alias_length
                or self._sha256_file(aliases) != alias_digest.hexdigest()
            ):
                raise CorpusReplayConflictError(
                    "existing path-alias output conflicts with checkpoint state",
                    sanitized_code="BUNDLE_REPLAY_CONFLICT",
                )
            generated.append((aliases.name, alias_length, alias_digest.hexdigest()))
            for name, query in queries.items():
                if name == "path-aliases.jsonl":
                    continue
                length, digest = self._write_query_jsonl(connection, name, query)
                generated.append((name, length, digest))
            summary_payload = canonical_json_bytes(summary) + b"\n"
            self._write_atomic_absent(self.bundle_root / "inventory-summary.json", summary_payload)
            generated.append(("inventory-summary.json", len(summary_payload), hashlib.sha256(summary_payload).hexdigest()))
            completed_at = self.clock().astimezone(UTC)
            run = CorpusInventoryRun(
                self.run_id, self.starting_head, self.source_root_identity_digest,
                self.device_reference, self.policy.policy_digest, started_at, completed_at,
                resume_count, plan.source_tree_digest, after_digest,
            )
            completion = {
                "run_id": self.run_id,
                "policy_digest": self.policy.policy_digest,
                "source_tree_digest": after_digest,
                "record_count": summary.objects_observed,
                "summary_digest": summary.summary_digest,
                "completed_at": completed_at.isoformat(),
                "state_spool_authority": "NONE",
            }
            completion_payload = canonical_json_bytes(completion) + b"\n"
            self._write_atomic_absent(checkpoints / "completion.json", completion_payload)
            generated.append(("checkpoints/completion.json", len(completion_payload), hashlib.sha256(completion_payload).hexdigest()))

        # Close and remove only the Step-14-owned local spool and its WAL files.
        for suffix in ("-wal", "-shm", ""):
            (Path(str(state_path) + suffix)).unlink(missing_ok=True)
        manifest = CorpusInventoryManifest(
            run, summary.summary_digest, tuple(generated),
            f"corpora/manifests/step14/{self.run_id}",
        )
        manifest_payload = canonical_json_bytes(manifest) + b"\n"
        self._write_atomic_absent(self.bundle_root / "inventory-manifest.json", manifest_payload)
        return summary, manifest


def verify_inventory_bundle(bundle_root: Path) -> dict[str, Any]:
    """Verify a completed bundle without trusting its manifest claims."""

    manifest_path = bundle_root / "inventory-manifest.json"
    if bundle_root.is_symlink() or not bundle_root.is_dir() or not manifest_path.is_file():
        raise CorpusSafetyError("inventory bundle is incomplete", sanitized_code="BUNDLE_INCOMPLETE")
    manifest = _strict_json_value(
        CorpusInventoryEngine._read_bounded_regular(
            manifest_path,
            maximum_bytes=4 * 1024 * 1024,
        )
    )
    expected_digest = manifest.get("manifest_digest")
    actual_digest = canonical_sha256(manifest, exclude_fields=("manifest_digest",))
    if expected_digest != actual_digest:
        raise CorpusReplayConflictError("inventory manifest digest mismatch", sanitized_code="BUNDLE_DIGEST_MISMATCH")
    verified: list[dict[str, Any]] = []
    for relative, length, digest in manifest.get("generated_files", []):
        target = bundle_root / relative
        if target.is_symlink() or not target.is_file() or target.stat().st_size != length:
            raise CorpusReplayConflictError("inventory artifact digest or length changed", sanitized_code="BUNDLE_DIGEST_MISMATCH")
        actual = CorpusInventoryEngine._sha256_file(target)
        if actual != digest:
            raise CorpusReplayConflictError("inventory artifact digest mismatch", sanitized_code="BUNDLE_DIGEST_MISMATCH")
        verified.append({"relative_path": relative, "byte_length": length, "sha256": actual})
    part_files = tuple(path.relative_to(bundle_root).as_posix() for path in bundle_root.rglob("*.part"))
    if part_files:
        raise CorpusReplayConflictError("inventory bundle contains partial files", sanitized_code="BUNDLE_PARTIAL_RESIDUE")
    return {"manifest_digest": actual_digest, "verified_files": verified, "part_files": [], "status": "PASS"}


__all__ = ["CorpusInventoryEngine", "InventoryPlan", "verify_inventory_bundle"]
