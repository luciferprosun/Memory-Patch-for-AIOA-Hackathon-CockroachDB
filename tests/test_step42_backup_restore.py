"""Step 42 backup integrity and destructive-target guard tests."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

import run_cockroachdb_migrations as migrations

from aioa_memory_kernel.contracts.exceptions import (
    ContractValidationError,
    IntegrityError,
)
from aioa_memory_kernel.release_candidate import (
    build_backup_tree_receipt,
    validate_disposable_recovery_root,
    validate_restore_target,
    verify_backup_tree_receipt,
)


class Step42BackupRestoreGuardTest(unittest.TestCase):
    def owned_root(self):
        path = Path(tempfile.mkdtemp(prefix="mp-step42-recovery-", dir="/tmp"))
        os.chmod(path, 0o700)
        self.addCleanup(lambda: self._cleanup(path))
        return path

    @staticmethod
    def _cleanup(path: Path) -> None:
        if not path.exists():
            return
        for child in sorted(path.rglob("*"), reverse=True):
            if child.is_file() or child.is_symlink():
                child.unlink()
            elif child.is_dir():
                child.rmdir()
        path.rmdir()

    def test_backup_tree_receipt_detects_byte_change_missing_file_and_extra_file(self):
        root = self.owned_root()
        backup = root / "native-backup"
        backup.mkdir(mode=0o700)
        (backup / "BACKUP_MANIFEST").write_bytes(b"manifest")
        data = backup / "data"
        data.mkdir()
        (data / "0001.sst").write_bytes(b"authoritative-test-fixture")
        receipt = build_backup_tree_receipt(
            backup,
            rc_manifest_digest="a" * 64,
        )
        self.assertEqual(verify_backup_tree_receipt(backup, receipt), receipt)
        (data / "0001.sst").write_bytes(b"tampered")
        with self.assertRaises(IntegrityError):
            verify_backup_tree_receipt(backup, receipt)
        (data / "0001.sst").write_bytes(b"authoritative-test-fixture")
        (backup / "unexpected").write_bytes(b"extra")
        with self.assertRaises(IntegrityError):
            verify_backup_tree_receipt(backup, receipt)

    def test_restore_target_requires_distinct_exact_step42_database_and_owned_path(self):
        root = self.owned_root()
        target = validate_restore_target(
            recovery_root=root,
            source_database="mp_step42_source_fixture_db",
            restore_database="mp_step42_restore_fixture_db",
            target_path=root / "restore-target",
        )
        self.assertEqual(target, root / "restore-target")
        invalid = (
            {
                "source_database": "production",
                "restore_database": "mp_step42_restore_fixture_db",
                "target_path": root / "restore-target",
            },
            {
                "source_database": "mp_step42_source_fixture_db",
                "restore_database": "production",
                "target_path": root / "restore-target",
            },
            {
                "source_database": "mp_step42_source_same_db",
                "restore_database": "mp_step42_source_same_db",
                "target_path": root / "restore-target",
            },
            {
                "source_database": "mp_step42_source_fixture_db",
                "restore_database": "mp_step42_restore_fixture_db",
                "target_path": Path("/tmp/restore-target"),
            },
        )
        for values in invalid:
            with self.subTest(values=values), self.assertRaises(
                ContractValidationError
            ):
                validate_restore_target(recovery_root=root, **values)

    def test_recovery_root_rejects_broad_wrong_prefix_symlink_and_open_mode(self):
        root = self.owned_root()
        self.assertEqual(validate_disposable_recovery_root(root), root)
        with self.assertRaises(ContractValidationError):
            validate_disposable_recovery_root(Path("/tmp"))
        wrong = Path(tempfile.mkdtemp(prefix="wrong-step42-", dir="/tmp"))
        self.addCleanup(lambda: self._cleanup(wrong))
        os.chmod(wrong, 0o700)
        with self.assertRaises(ContractValidationError):
            validate_disposable_recovery_root(wrong)
        open_root = self.owned_root()
        os.chmod(open_root, 0o755)
        with self.assertRaises(ContractValidationError):
            validate_disposable_recovery_root(open_root)

    def test_cockroach_external_io_accepts_only_owned_private_real_directory(self):
        root = self.owned_root()
        backup = root / "native-backup"
        backup.mkdir(mode=0o700)
        self.assertEqual(
            migrations.validate_step42_external_io_directory(backup),
            backup,
        )

        other_root = self.owned_root()
        other_backup = other_root / "native-backup"
        other_backup.mkdir(mode=0o700)
        backup.rmdir()
        backup.symlink_to(other_backup, target_is_directory=True)
        with self.assertRaises(migrations.MigrationError):
            migrations.validate_step42_external_io_directory(backup)

        open_root = self.owned_root()
        open_backup = open_root / "native-backup"
        open_backup.mkdir(mode=0o700)
        os.chmod(open_root, 0o755)
        with self.assertRaises(migrations.MigrationError):
            migrations.validate_step42_external_io_directory(open_backup)


if __name__ == "__main__":
    unittest.main()
