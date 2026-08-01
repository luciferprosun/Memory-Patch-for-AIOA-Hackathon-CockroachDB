"""Bounded archive, PDF, and XML validation without extraction."""

from __future__ import annotations

import os
import stat
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path, PurePosixPath

from .errors import AcquisitionIntegrityError

MAXIMUM_ZIP_ENTRIES = 500_000
MAXIMUM_COMPRESSION_RATIO = 1_000
MAXIMUM_XML_METADATA_BYTES = 64 * 1024 * 1024


def _require_regular_file(path: Path, *, code: str) -> os.stat_result:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise AcquisitionIntegrityError(
            "downloaded object cannot be inspected",
            code=code,
        ) from exc
    if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
        raise AcquisitionIntegrityError(
            "downloaded object is not a regular no-follow file",
            code=code,
        )
    return metadata


def validate_zip(
    path: Path,
    *,
    maximum_expanded_bytes: int = 10 * 1024**3,
) -> dict[str, object]:
    """Verify ZIP structure, paths, claims, encryption, and CRCs."""

    _require_regular_file(path, code="ZIP_INTEGRITY_FAILED")
    try:
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            if len(entries) > MAXIMUM_ZIP_ENTRIES:
                raise AcquisitionIntegrityError(
                    "ZIP entry count exceeds its bound",
                    code="ZIP_BOMB_SUSPECTED",
                )
            names: set[str] = set()
            expanded = 0
            compressed = 0
            xml_entries = 0
            for entry in entries:
                name = entry.filename
                if (
                    not name
                    or "\\" in name
                    or "\x00" in name
                    or any(ord(character) < 32 for character in name)
                ):
                    raise AcquisitionIntegrityError(
                        "ZIP contains a malformed path",
                        code="ZIP_UNSAFE_PATH",
                    )
                relative = PurePosixPath(name)
                if relative.is_absolute() or any(
                    part in {"", ".", ".."} for part in relative.parts
                ):
                    raise AcquisitionIntegrityError(
                        "ZIP contains a traversal path",
                        code="ZIP_UNSAFE_PATH",
                    )
                normalized = relative.as_posix().rstrip("/")
                if normalized in names:
                    raise AcquisitionIntegrityError(
                        "ZIP contains a path collision",
                        code="ZIP_COLLISION",
                    )
                names.add(normalized)
                if entry.flag_bits & 0x1:
                    raise AcquisitionIntegrityError(
                        "encrypted ZIP entries are not accepted",
                        code="ZIP_ENCRYPTED",
                    )
                mode = (entry.external_attr >> 16) & 0xFFFF
                kind = stat.S_IFMT(mode)
                if kind and kind not in (stat.S_IFREG, stat.S_IFDIR):
                    raise AcquisitionIntegrityError(
                        "ZIP contains a symlink or special file",
                        code="ZIP_UNSAFE_PATH",
                    )
                expanded += entry.file_size
                compressed += entry.compress_size
                if expanded > maximum_expanded_bytes:
                    raise AcquisitionIntegrityError(
                        "ZIP expanded-size claim exceeds its bound",
                        code="ZIP_BOMB_SUSPECTED",
                    )
                if (
                    entry.file_size > 1024 * 1024
                    and entry.compress_size > 0
                    and entry.file_size // entry.compress_size
                    > MAXIMUM_COMPRESSION_RATIO
                ):
                    raise AcquisitionIntegrityError(
                        "ZIP compression ratio exceeds its bound",
                        code="ZIP_BOMB_SUSPECTED",
                    )
                if name.lower().endswith(".xml") and not entry.is_dir():
                    xml_entries += 1
            if expanded and compressed and expanded // compressed > MAXIMUM_COMPRESSION_RATIO:
                raise AcquisitionIntegrityError(
                    "ZIP aggregate compression ratio exceeds its bound",
                    code="ZIP_BOMB_SUSPECTED",
                )
            bad_member = archive.testzip()
            if bad_member is not None:
                raise AcquisitionIntegrityError(
                    "ZIP CRC validation failed",
                    code="ZIP_INTEGRITY_FAILED",
                )
            return {
                "entry_count": len(entries),
                "expanded_bytes": expanded,
                "compressed_payload_bytes": compressed,
                "xml_entry_count": xml_entries,
            }
    except AcquisitionIntegrityError:
        raise
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        raise AcquisitionIntegrityError(
            "ZIP validation failed",
            code="ZIP_INTEGRITY_FAILED",
        ) from exc


def validate_pdf(path: Path) -> dict[str, object]:
    _require_regular_file(path, code="PDF_MAGIC_MISMATCH")
    try:
        with path.open("rb", buffering=0) as stream:
            prefix = stream.read(5)
    except OSError as exc:
        raise AcquisitionIntegrityError(
            "PDF cannot be inspected",
            code="PDF_MAGIC_MISMATCH",
        ) from exc
    if prefix != b"%PDF-":
        raise AcquisitionIntegrityError(
            "PDF magic bytes do not match",
            code="PDF_MAGIC_MISMATCH",
        )
    return {"pdf_magic_verified": True}


def validate_xml(path: Path) -> dict[str, object]:
    try:
        metadata = _require_regular_file(path, code="XML_MALFORMED")
        if metadata.st_size > MAXIMUM_XML_METADATA_BYTES:
            raise AcquisitionIntegrityError(
                "XML metadata object exceeds its parser bound",
                code="XML_SIZE_LIMIT_EXCEEDED",
            )
        root_tag = ""
        for _, element in ET.iterparse(path, events=("start", "end")):
            if not root_tag:
                root_tag = element.tag
            element.clear()
    except AcquisitionIntegrityError:
        raise
    except (OSError, ET.ParseError, UnicodeError) as exc:
        raise AcquisitionIntegrityError(
            "XML is malformed",
            code="XML_MALFORMED",
        ) from exc
    return {"xml_well_formed": True, "root_tag": root_tag}


def validate_magic(path: Path, expected: bytes) -> None:
    _require_regular_file(path, code="CONTENT_TYPE_MISMATCH")
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | os.O_CLOEXEC
            | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            actual = os.read(descriptor, len(expected))
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise AcquisitionIntegrityError(
            "downloaded object cannot be inspected",
            code="CONTENT_TYPE_MISMATCH",
        ) from exc
    if actual != expected:
        raise AcquisitionIntegrityError(
            "downloaded object magic bytes do not match",
            code="CONTENT_TYPE_MISMATCH",
        )


__all__ = ["validate_magic", "validate_pdf", "validate_xml", "validate_zip"]
