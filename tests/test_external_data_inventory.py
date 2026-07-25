from __future__ import annotations

import hashlib
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
INVENTORY_PATH = REPOSITORY_ROOT / "scripts" / "external_data" / "inventory.py"
SPEC = importlib.util.spec_from_file_location("external_data_inventory", INVENTORY_PATH)
assert SPEC is not None and SPEC.loader is not None
inventory_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(inventory_module)


class ExternalDataInventoryTests(unittest.TestCase):
    def test_deterministic_ordering(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "root"
            root.mkdir()
            (root / "z.txt").write_text("z", encoding="utf-8")
            (root / "a.txt").write_text("a", encoding="utf-8")
            (root / "middle").mkdir()

            first = inventory_module.create_inventory(root, allowed_root=temporary)
            second = inventory_module.create_inventory(root, allowed_root=temporary)

            self.assertEqual(first, second)
            paths = [entry["path"] for entry in first["entries"]]
            self.assertEqual(paths, sorted(paths, key=os.fsencode))

    def test_regular_file_hashing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "root"
            root.mkdir()
            content = b"memory-patch-inventory\n"
            (root / "payload.bin").write_bytes(content)

            result = inventory_module.create_inventory(root, allowed_root=temporary)
            file_entry = next(
                entry for entry in result["entries"] if entry["type"] == "file"
            )
            self.assertEqual(file_entry["size"], len(content))
            self.assertEqual(file_entry["sha256"], hashlib.sha256(content).hexdigest())

    def test_empty_directories_are_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "root"
            (root / "empty").mkdir(parents=True)

            result = inventory_module.create_inventory(root, allowed_root=temporary)

            entries = {entry["path"]: entry for entry in result["entries"]}
            self.assertEqual(entries["empty"]["type"], "directory")
            self.assertEqual(result["summary"]["directories"], 2)

    def test_nested_paths_and_spaces(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "root with spaces"
            nested = root / "nested path" / "deeper"
            nested.mkdir(parents=True)
            (nested / "file name.txt").write_text("nested", encoding="utf-8")

            result = inventory_module.create_inventory(root, allowed_root=temporary)
            paths = {entry["path"] for entry in result["entries"]}

            self.assertIn("nested path/deeper/file name.txt", paths)

    def test_symlink_is_recorded_without_dereferencing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            allowed = Path(temporary) / "allowed"
            outside = Path(temporary) / "outside"
            root = allowed / "root"
            root.mkdir(parents=True)
            outside.mkdir()
            (outside / "private.txt").write_text("do not traverse", encoding="utf-8")
            (root / "external-link").symlink_to(outside, target_is_directory=True)

            result = inventory_module.create_inventory(root, allowed_root=allowed)
            entries = {entry["path"]: entry for entry in result["entries"]}

            self.assertEqual(entries["external-link"]["type"], "symlink")
            self.assertTrue(entries["external-link"]["target_escapes_allowed_root"])
            self.assertNotIn("external-link/private.txt", entries)
            self.assertEqual(result["summary"]["files"], 0)

    def test_mismatch_detection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            destination = Path(temporary) / "destination"
            source.mkdir()
            destination.mkdir()
            (source / "record.txt").write_text("source", encoding="utf-8")
            (destination / "record.txt").write_text("changed", encoding="utf-8")

            source_inventory = inventory_module.create_inventory(
                source, allowed_root=temporary
            )
            destination_inventory = inventory_module.create_inventory(
                destination, allowed_root=temporary
            )
            mismatches = inventory_module.compare_inventories(
                source_inventory, destination_inventory
            )

            self.assertTrue(mismatches)
            self.assertTrue(any("record.txt" in mismatch for mismatch in mismatches))

    def test_refuses_path_outside_allowed_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            allowed = Path(temporary) / "allowed"
            outside = Path(temporary) / "outside"
            allowed.mkdir()
            outside.mkdir()

            with self.assertRaises(inventory_module.InventoryError):
                inventory_module.create_inventory(outside, allowed_root=allowed)

    def test_missing_inventory_root_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            missing = Path(temporary) / "missing"

            with self.assertRaises(inventory_module.InventoryError):
                inventory_module.create_inventory(missing, allowed_root=temporary)

    def test_escaping_symlink_does_not_traverse_nested_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            allowed = Path(temporary) / "allowed"
            outside = Path(temporary) / "outside"
            root = allowed / "root"
            secret_directory = outside / "secret directory"
            root.mkdir(parents=True)
            secret_directory.mkdir(parents=True)
            (secret_directory / "secret.txt").write_text("secret", encoding="utf-8")
            (root / "escape").symlink_to(secret_directory, target_is_directory=True)

            result = inventory_module.create_inventory(root, allowed_root=allowed)
            paths = [entry["path"] for entry in result["entries"]]

            self.assertEqual(paths, [".", "escape"])


if __name__ == "__main__":
    unittest.main()
