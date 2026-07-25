#!/usr/bin/env python3
"""Deterministic, non-dereferencing filesystem inventories for safe migration."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable

SCHEMA_VERSION = "1.0.0"
HASH_CHUNK_SIZE = 1024 * 1024


class InventoryError(RuntimeError):
    """Raised when a safe deterministic inventory cannot be completed."""


def _canonical(path: os.PathLike[str] | str) -> str:
    return os.path.realpath(os.path.abspath(os.fspath(path)))


def _is_within(path: str, root: str) -> bool:
    try:
        return os.path.commonpath((path, root)) == root
    except ValueError:
        return False


def _relative_posix(path: str, root: str) -> str:
    relative = os.path.relpath(path, root)
    return "." if relative == "." else Path(relative).as_posix()


def _allocated_size(metadata: os.stat_result) -> int:
    return int(getattr(metadata, "st_blocks", 0)) * 512


def _directory_entry(relative_path: str, metadata: os.stat_result) -> dict[str, Any]:
    return {
        "path": relative_path,
        "type": "directory",
        "mode": stat.S_IMODE(metadata.st_mode),
        "allocated_size": _allocated_size(metadata),
    }


def _hash_open_file(descriptor: int) -> str:
    digest = hashlib.sha256()
    while True:
        chunk = os.read(descriptor, HASH_CHUNK_SIZE)
        if not chunk:
            return digest.hexdigest()
        digest.update(chunk)


def _regular_file_entry(
    absolute_path: str,
    relative_path: str,
    initial_metadata: os.stat_result,
) -> dict[str, Any]:
    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW

    try:
        descriptor = os.open(absolute_path, flags)
    except FileNotFoundError as exc:
        raise InventoryError(f"path disappeared during inventory: {relative_path}") from exc
    except OSError as exc:
        raise InventoryError(f"cannot safely open regular file {relative_path}: {exc}") from exc

    try:
        opened_metadata = os.fstat(descriptor)
        if not stat.S_ISREG(opened_metadata.st_mode):
            raise InventoryError(f"path changed type during inventory: {relative_path}")
        if (
            opened_metadata.st_dev != initial_metadata.st_dev
            or opened_metadata.st_ino != initial_metadata.st_ino
        ):
            raise InventoryError(f"path changed identity during inventory: {relative_path}")
        digest = _hash_open_file(descriptor)
        final_open_metadata = os.fstat(descriptor)
    finally:
        os.close(descriptor)

    try:
        final_path_metadata = os.lstat(absolute_path)
    except FileNotFoundError as exc:
        raise InventoryError(f"path disappeared during inventory: {relative_path}") from exc

    stable_fields = (
        "st_dev",
        "st_ino",
        "st_size",
        "st_mtime_ns",
    )
    for field in stable_fields:
        initial = getattr(opened_metadata, field)
        after_open = getattr(final_open_metadata, field)
        after_path = getattr(final_path_metadata, field)
        if initial != after_open or initial != after_path:
            raise InventoryError(f"regular file changed during inventory: {relative_path}")

    return {
        "path": relative_path,
        "type": "file",
        "mode": stat.S_IMODE(opened_metadata.st_mode),
        "size": opened_metadata.st_size,
        "allocated_size": _allocated_size(opened_metadata),
        "sha256": digest,
    }


def _symlink_entry(
    absolute_path: str,
    relative_path: str,
    metadata: os.stat_result,
    allowed_root: str,
) -> dict[str, Any]:
    try:
        link_target = os.readlink(absolute_path)
        final_metadata = os.lstat(absolute_path)
    except FileNotFoundError as exc:
        raise InventoryError(f"symlink disappeared during inventory: {relative_path}") from exc

    if (
        metadata.st_dev != final_metadata.st_dev
        or metadata.st_ino != final_metadata.st_ino
        or metadata.st_mtime_ns != final_metadata.st_mtime_ns
    ):
        raise InventoryError(f"symlink changed during inventory: {relative_path}")

    if os.path.isabs(link_target):
        resolved_target = _canonical(link_target)
    else:
        resolved_target = _canonical(os.path.join(os.path.dirname(absolute_path), link_target))

    return {
        "path": relative_path,
        "type": "symlink",
        "mode": stat.S_IMODE(metadata.st_mode),
        "allocated_size": _allocated_size(metadata),
        "link_target": link_target,
        "target_escapes_allowed_root": not _is_within(resolved_target, allowed_root),
    }


def _special_type(mode: int) -> str:
    if stat.S_ISSOCK(mode):
        return "socket"
    if stat.S_ISFIFO(mode):
        return "fifo"
    if stat.S_ISBLK(mode):
        return "block-device"
    if stat.S_ISCHR(mode):
        return "character-device"
    return "special"


def create_inventory(
    root: os.PathLike[str] | str,
    *,
    allowed_root: os.PathLike[str] | str,
) -> dict[str, Any]:
    """Inventory *root* without following symlinks.

    Both the allowed root and inventory root must already exist as real
    directories. The inventory root's canonical location must be contained by
    the allowed root. An escaping symlink is recorded but never traversed.
    """

    requested_root = os.path.abspath(os.fspath(root))
    requested_allowed_root = os.path.abspath(os.fspath(allowed_root))

    for path, description in (
        (requested_allowed_root, "allowed root"),
        (requested_root, "inventory root"),
    ):
        try:
            metadata = os.lstat(path)
        except FileNotFoundError as exc:
            raise InventoryError(f"{description} does not exist: {path}") from exc
        if stat.S_ISLNK(metadata.st_mode):
            raise InventoryError(f"{description} must not be a symlink: {path}")
        if not stat.S_ISDIR(metadata.st_mode):
            raise InventoryError(f"{description} is not a directory: {path}")

    canonical_allowed_root = _canonical(requested_allowed_root)
    canonical_root = _canonical(requested_root)
    if not _is_within(canonical_root, canonical_allowed_root):
        raise InventoryError(
            f"inventory root escapes allowed root: {canonical_root} "
            f"not within {canonical_allowed_root}"
        )

    root_device = os.lstat(canonical_root).st_dev
    records: list[dict[str, Any]] = []

    def scan_directory(absolute_directory: str) -> None:
        try:
            directory_metadata = os.lstat(absolute_directory)
        except FileNotFoundError as exc:
            raise InventoryError(
                f"directory disappeared during inventory: "
                f"{_relative_posix(absolute_directory, canonical_root)}"
            ) from exc
        if not stat.S_ISDIR(directory_metadata.st_mode):
            raise InventoryError(
                f"directory changed type during inventory: "
                f"{_relative_posix(absolute_directory, canonical_root)}"
            )
        if directory_metadata.st_dev != root_device:
            raise InventoryError(
                f"nested filesystem boundary encountered: "
                f"{_relative_posix(absolute_directory, canonical_root)}"
            )
        canonical_directory = _canonical(absolute_directory)
        if not _is_within(canonical_directory, canonical_allowed_root):
            raise InventoryError(f"directory escapes allowed root: {absolute_directory}")

        relative_directory = _relative_posix(absolute_directory, canonical_root)
        records.append(_directory_entry(relative_directory, directory_metadata))

        try:
            with os.scandir(absolute_directory) as iterator:
                children = sorted(iterator, key=lambda entry: os.fsencode(entry.name))
        except FileNotFoundError as exc:
            raise InventoryError(
                f"directory disappeared during scan: {relative_directory}"
            ) from exc
        except OSError as exc:
            raise InventoryError(f"cannot scan directory {relative_directory}: {exc}") from exc

        for child in children:
            absolute_child = os.path.join(absolute_directory, child.name)
            relative_child = _relative_posix(absolute_child, canonical_root)
            try:
                metadata = child.stat(follow_symlinks=False)
            except FileNotFoundError as exc:
                raise InventoryError(
                    f"path disappeared during inventory: {relative_child}"
                ) from exc
            except OSError as exc:
                raise InventoryError(f"cannot stat {relative_child}: {exc}") from exc

            mode = metadata.st_mode
            if stat.S_ISDIR(mode):
                scan_directory(absolute_child)
            elif stat.S_ISREG(mode):
                records.append(
                    _regular_file_entry(absolute_child, relative_child, metadata)
                )
            elif stat.S_ISLNK(mode):
                records.append(
                    _symlink_entry(
                        absolute_child,
                        relative_child,
                        metadata,
                        canonical_allowed_root,
                    )
                )
            else:
                records.append(
                    {
                        "path": relative_child,
                        "type": _special_type(mode),
                        "mode": stat.S_IMODE(mode),
                        "allocated_size": _allocated_size(metadata),
                    }
                )

    scan_directory(canonical_root)
    records.sort(key=lambda record: os.fsencode(record["path"]))

    counts = {
        "directories": sum(record["type"] == "directory" for record in records),
        "files": sum(record["type"] == "file" for record in records),
        "symlinks": sum(record["type"] == "symlink" for record in records),
        "special": sum(
            record["type"] not in {"directory", "file", "symlink"}
            for record in records
        ),
        "escaping_symlinks": sum(
            record["type"] == "symlink"
            and bool(record["target_escapes_allowed_root"])
            for record in records
        ),
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "root": canonical_root,
        "allowed_root": canonical_allowed_root,
        "summary": {
            **counts,
            "apparent_bytes": sum(
                int(record.get("size", 0))
                for record in records
                if record["type"] == "file"
            ),
            "allocated_bytes": sum(
                int(record.get("allocated_size", 0)) for record in records
            ),
        },
        "entries": records,
    }


def _entry_comparison_fields(entry_type: str) -> tuple[str, ...]:
    if entry_type == "file":
        return ("type", "size", "sha256")
    if entry_type == "symlink":
        return ("type", "link_target", "target_escapes_allowed_root")
    return ("type",)


def compare_inventories(
    source: dict[str, Any],
    destination: dict[str, Any],
) -> list[str]:
    """Return deterministic mismatch descriptions; an empty list means equal."""

    mismatches: list[str] = []
    if source.get("schema_version") != SCHEMA_VERSION:
        mismatches.append("source schema_version is unsupported")
    if destination.get("schema_version") != SCHEMA_VERSION:
        mismatches.append("destination schema_version is unsupported")

    source_entries = {
        entry["path"]: entry for entry in source.get("entries", [])
    }
    destination_entries = {
        entry["path"]: entry for entry in destination.get("entries", [])
    }

    for path in sorted(source_entries.keys() - destination_entries.keys()):
        mismatches.append(f"missing destination path: {path}")
    for path in sorted(destination_entries.keys() - source_entries.keys()):
        mismatches.append(f"unexpected destination path: {path}")

    for path in sorted(source_entries.keys() & destination_entries.keys()):
        source_entry = source_entries[path]
        destination_entry = destination_entries[path]
        source_type = source_entry.get("type")
        destination_type = destination_entry.get("type")
        fields: Iterable[str]
        if source_type != destination_type:
            fields = ("type",)
        else:
            fields = _entry_comparison_fields(str(source_type))
        for field in fields:
            if source_entry.get(field) != destination_entry.get(field):
                mismatches.append(
                    f"{path}: {field} differs "
                    f"({source_entry.get(field)!r} != "
                    f"{destination_entry.get(field)!r})"
                )

    for field in ("directories", "files", "symlinks", "special"):
        source_value = source.get("summary", {}).get(field)
        destination_value = destination.get("summary", {}).get(field)
        if source_value != destination_value:
            mismatches.append(
                f"summary {field} differs ({source_value!r} != {destination_value!r})"
            )

    return sorted(set(mismatches))


def write_inventory(inventory: dict[str, Any], output_path: os.PathLike[str] | str) -> None:
    output = os.path.abspath(os.fspath(output_path))
    parent = os.path.dirname(output)
    if not os.path.isdir(parent):
        raise InventoryError(f"inventory output parent does not exist: {parent}")
    if os.path.islink(output):
        raise InventoryError(f"refusing to overwrite symlink output: {output}")

    descriptor, temporary = tempfile.mkstemp(
        prefix=".aioa-inventory.", suffix=".tmp", dir=parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(inventory, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, output)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_inventory(path: os.PathLike[str] | str) -> dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            inventory = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise InventoryError(f"cannot load inventory {path}: {exc}") from exc
    if not isinstance(inventory, dict):
        raise InventoryError(f"inventory is not a JSON object: {path}")
    return inventory


def verify_read_through(
    application_path: os.PathLike[str] | str,
    inventory: dict[str, Any],
) -> None:
    """Verify application-facing reads against regular-file inventory hashes."""

    application_root = os.path.abspath(os.fspath(application_path))
    if not os.path.isdir(application_root):
        raise InventoryError(f"application-facing path is not readable: {application_root}")

    files = [
        entry for entry in inventory.get("entries", []) if entry.get("type") == "file"
    ]
    if not files:
        try:
            os.listdir(application_root)
        except OSError as exc:
            raise InventoryError(
                f"cannot list application-facing path: {application_root}: {exc}"
            ) from exc
        return

    for entry in files:
        path = os.path.join(application_root, *entry["path"].split("/"))
        try:
            with open(path, "rb") as handle:
                digest = hashlib.file_digest(handle, "sha256").hexdigest()
        except OSError as exc:
            raise InventoryError(f"cannot read through {path}: {exc}") from exc
        if digest != entry["sha256"]:
            raise InventoryError(f"read-through checksum mismatch: {entry['path']}")


def _json_to_stdout(payload: Any) -> None:
    json.dump(payload, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    create_parser = subparsers.add_parser("create", help="create an inventory")
    create_parser.add_argument("root")
    create_parser.add_argument("--allowed-root", required=True)
    create_parser.add_argument("--output")

    summary_parser = subparsers.add_parser("summary", help="print only inventory summary")
    summary_parser.add_argument("root")
    summary_parser.add_argument("--allowed-root", required=True)

    compare_parser = subparsers.add_parser("compare", help="compare two inventories")
    compare_parser.add_argument("source_inventory")
    compare_parser.add_argument("destination_inventory")

    read_parser = subparsers.add_parser(
        "verify-read", help="verify reads through an application-facing path"
    )
    read_parser.add_argument("application_path")
    read_parser.add_argument("inventory")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "create":
            inventory = create_inventory(
                arguments.root,
                allowed_root=arguments.allowed_root,
            )
            if arguments.output:
                write_inventory(inventory, arguments.output)
            else:
                _json_to_stdout(inventory)
            return 0
        if arguments.command == "summary":
            inventory = create_inventory(
                arguments.root,
                allowed_root=arguments.allowed_root,
            )
            _json_to_stdout(inventory["summary"])
            return 0
        if arguments.command == "compare":
            mismatches = compare_inventories(
                load_inventory(arguments.source_inventory),
                load_inventory(arguments.destination_inventory),
            )
            _json_to_stdout({"match": not mismatches, "mismatches": mismatches})
            return 0 if not mismatches else 1
        if arguments.command == "verify-read":
            verify_read_through(
                arguments.application_path,
                load_inventory(arguments.inventory),
            )
            _json_to_stdout({"read_through": "verified"})
            return 0
    except InventoryError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    parser.error("unhandled command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
