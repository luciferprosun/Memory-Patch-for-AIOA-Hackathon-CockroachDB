"""Adversarial tests for bounded ZIP, PDF, XML, and magic validation."""

from __future__ import annotations

import os
import stat
import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path

from aioa_memory_kernel.acquisition.archive import (
    MAXIMUM_XML_METADATA_BYTES,
    validate_magic,
    validate_pdf,
    validate_xml,
    validate_zip,
)
from aioa_memory_kernel.acquisition.errors import AcquisitionIntegrityError


class AcquisitionArchiveFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def assert_integrity_code(self, expected: str, operation) -> None:
        with self.assertRaises(AcquisitionIntegrityError) as caught:
            operation()
        self.assertEqual(caught.exception.code, expected)

    def write_zip(
        self,
        name: str,
        entries: tuple[tuple[zipfile.ZipInfo | str, bytes], ...],
        *,
        compression: int = zipfile.ZIP_STORED,
    ) -> Path:
        path = self.root / name
        with zipfile.ZipFile(path, "w", compression=compression) as archive:
            for member, payload in entries:
                archive.writestr(member, payload)
        return path


class ZipValidationTests(AcquisitionArchiveFixture):
    def test_valid_zip_returns_deterministic_bounded_summary(self) -> None:
        path = self.write_zip(
            "valid.zip",
            (
                ("metadata.xml", b"<root><value>1</value></root>"),
                ("nested/readme.txt", b"official metadata"),
            ),
        )

        first = validate_zip(path)
        replay = validate_zip(path)

        self.assertEqual(first, replay)
        self.assertEqual(first["entry_count"], 2)
        self.assertEqual(first["xml_entry_count"], 1)
        self.assertEqual(
            first["expanded_bytes"],
            len(b"<root><value>1</value></root>") + len(b"official metadata"),
        )

    def test_traversal_absolute_backslash_and_control_paths_fail(self) -> None:
        unsafe_names = (
            "../escape.txt",
            "/absolute.txt",
            "nested\\windows.txt",
            "control\x01.txt",
        )
        for index, member in enumerate(unsafe_names):
            with self.subTest(member=member):
                path = self.write_zip(
                    f"unsafe-{index}.zip",
                    ((member, b"payload"),),
                )
                self.assert_integrity_code(
                    "ZIP_UNSAFE_PATH",
                    lambda path=path: validate_zip(path),
                )

    def test_duplicate_and_normalized_path_collisions_fail(self) -> None:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            exact = self.write_zip(
                "exact-collision.zip",
                (("same.txt", b"one"), ("same.txt", b"two")),
            )
        normalized = self.write_zip(
            "normalized-collision.zip",
            (("directory", b"file"), ("directory/", b"")),
        )

        for path in (exact, normalized):
            with self.subTest(path=path.name):
                self.assert_integrity_code(
                    "ZIP_COLLISION",
                    lambda path=path: validate_zip(path),
                )

    def test_symlink_member_is_rejected(self) -> None:
        link = zipfile.ZipInfo("linked-law.xml")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        path = self.write_zip("symlink-member.zip", ((link, b"target"),))

        self.assert_integrity_code(
            "ZIP_UNSAFE_PATH",
            lambda: validate_zip(path),
        )

    def test_special_file_member_is_rejected(self) -> None:
        special = zipfile.ZipInfo("named-pipe")
        special.create_system = 3
        special.external_attr = (stat.S_IFIFO | 0o600) << 16
        path = self.write_zip("special-member.zip", ((special, b""),))

        self.assert_integrity_code(
            "ZIP_UNSAFE_PATH",
            lambda: validate_zip(path),
        )

    def test_expanded_size_limit_fails_before_crc_scan(self) -> None:
        path = self.write_zip("expanded.zip", (("large.bin", b"0123456789"),))

        self.assert_integrity_code(
            "ZIP_BOMB_SUSPECTED",
            lambda: validate_zip(path, maximum_expanded_bytes=9),
        )

    def test_high_compression_ratio_is_rejected(self) -> None:
        path = self.write_zip(
            "ratio.zip",
            (("zeros.bin", b"\0" * (2 * 1024 * 1024)),),
            compression=zipfile.ZIP_DEFLATED,
        )

        self.assert_integrity_code(
            "ZIP_BOMB_SUSPECTED",
            lambda: validate_zip(path),
        )

    def test_crc_corruption_is_rejected(self) -> None:
        payload = b"unique-crc-payload-for-acquisition"
        path = self.write_zip("crc.zip", (("payload.bin", payload),))
        raw = bytearray(path.read_bytes())
        position = raw.find(payload)
        self.assertGreaterEqual(position, 0)
        raw[position] ^= 0x01
        path.write_bytes(raw)

        self.assert_integrity_code(
            "ZIP_INTEGRITY_FAILED",
            lambda: validate_zip(path),
        )

    def test_malformed_zip_is_rejected(self) -> None:
        path = self.root / "malformed.zip"
        path.write_bytes(b"not-a-zip")

        self.assert_integrity_code(
            "ZIP_INTEGRITY_FAILED",
            lambda: validate_zip(path),
        )

    def test_zip_input_symlink_is_rejected(self) -> None:
        regular = self.write_zip("regular.zip", (("safe.txt", b"safe"),))
        link = self.root / "archive-link.zip"
        link.symlink_to(regular)

        self.assert_integrity_code(
            "ZIP_INTEGRITY_FAILED",
            lambda: validate_zip(link),
        )


class PdfAndMagicValidationTests(AcquisitionArchiveFixture):
    def test_valid_pdf_magic_passes(self) -> None:
        path = self.root / "valid.pdf"
        path.write_bytes(b"%PDF-1.7\nsynthetic official document\n%%EOF\n")

        self.assertEqual(validate_pdf(path), {"pdf_magic_verified": True})
        validate_magic(path, b"%PDF-")

    def test_invalid_pdf_magic_fails(self) -> None:
        path = self.root / "invalid.pdf"
        path.write_bytes(b"not-pdf")

        self.assert_integrity_code(
            "PDF_MAGIC_MISMATCH",
            lambda: validate_pdf(path),
        )
        self.assert_integrity_code(
            "CONTENT_TYPE_MISMATCH",
            lambda: validate_magic(path, b"%PDF-"),
        )

    def test_pdf_and_magic_input_symlinks_are_rejected(self) -> None:
        regular = self.root / "regular.pdf"
        regular.write_bytes(b"%PDF-1.7\n")
        link = self.root / "linked.pdf"
        link.symlink_to(regular)

        self.assert_integrity_code(
            "PDF_MAGIC_MISMATCH",
            lambda: validate_pdf(link),
        )
        self.assert_integrity_code(
            "CONTENT_TYPE_MISMATCH",
            lambda: validate_magic(link, b"%PDF-"),
        )


class XmlValidationTests(AcquisitionArchiveFixture):
    def test_valid_xml_returns_stable_root_tag(self) -> None:
        path = self.root / "valid.xml"
        path.write_bytes(b"<?xml version='1.0'?><root><law id='1'/></root>")

        first = validate_xml(path)
        replay = validate_xml(path)

        self.assertEqual(first, replay)
        self.assertEqual(first, {"xml_well_formed": True, "root_tag": "root"})

    def test_malformed_xml_is_rejected(self) -> None:
        path = self.root / "malformed.xml"
        path.write_bytes(b"<root><unclosed></root>")

        self.assert_integrity_code(
            "XML_MALFORMED",
            lambda: validate_xml(path),
        )

    def test_external_entity_is_not_resolved(self) -> None:
        path = self.root / "entity.xml"
        path.write_text(
            "<!DOCTYPE root [<!ENTITY external SYSTEM "
            "'file:///definitely-not-an-acquisition-fixture'>]>"
            "<root>&external;</root>",
            encoding="utf-8",
        )

        self.assert_integrity_code(
            "XML_MALFORMED",
            lambda: validate_xml(path),
        )

    def test_xml_size_limit_is_enforced_before_parse(self) -> None:
        path = self.root / "oversized.xml"
        with path.open("wb") as stream:
            stream.truncate(MAXIMUM_XML_METADATA_BYTES + 1)

        self.assert_integrity_code(
            "XML_SIZE_LIMIT_EXCEEDED",
            lambda: validate_xml(path),
        )

    def test_xml_input_symlink_is_rejected(self) -> None:
        regular = self.root / "regular.xml"
        regular.write_bytes(b"<root/>")
        link = self.root / "linked.xml"
        link.symlink_to(regular)

        self.assert_integrity_code(
            "XML_MALFORMED",
            lambda: validate_xml(link),
        )


if __name__ == "__main__":
    unittest.main()
