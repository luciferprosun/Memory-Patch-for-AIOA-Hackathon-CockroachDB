"""Linux mount and block-device probe for the Step 8 runtime adapter."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from aioa_memory_kernel.storage.errors import (
    ExternalVolumeIdentityError,
    ExternalVolumeUnavailableError,
)
from aioa_memory_kernel.storage.external_volume import ExternalMountIdentity


def _nested_block_devices(devices: object) -> list[Mapping[str, Any]]:
    if not isinstance(devices, list):
        return []
    flattened: list[Mapping[str, Any]] = []
    for item in devices:
        if not isinstance(item, Mapping):
            continue
        flattened.append(item)
        flattened.extend(_nested_block_devices(item.get("children")))
    return flattened


class LinuxExternalVolumeProbe:
    """Read Linux mount and block-device identity without a shell."""

    _LSBLK_COLUMNS = "PATH,KNAME,PKNAME,TYPE,TRAN,UUID,LABEL,RO"

    @staticmethod
    def _json_command(
        arguments: list[str],
        failure_code: str,
    ) -> Mapping[str, Any]:
        try:
            completed = subprocess.run(
                arguments,
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=10,
                env={
                    "PATH": os.environ.get(
                        "PATH",
                        "/usr/sbin:/usr/bin:/sbin:/bin",
                    ),
                    "LANG": "C",
                    "LC_ALL": "C",
                },
            )
            payload = json.loads(completed.stdout)
        except (
            FileNotFoundError,
            subprocess.CalledProcessError,
            subprocess.TimeoutExpired,
            UnicodeError,
            json.JSONDecodeError,
        ) as exc:
            raise ExternalVolumeUnavailableError(
                "external-volume identity probe failed",
                sanitized_code=failure_code,
            ) from exc
        if not isinstance(payload, Mapping):
            raise ExternalVolumeUnavailableError(
                "external-volume identity probe returned malformed data",
                sanitized_code=failure_code,
            )
        return payload

    @classmethod
    def _resolved_transport(
        cls,
        selected: Mapping[str, Any],
    ) -> str:
        """Resolve transport from one exact partition and, only when needed,
        its one exact block parent.

        Linux commonly leaves ``TRAN`` empty on a partition while reporting
        it on the parent disk.  The fallback is intentionally USB-only and
        fails closed for every missing, ambiguous, or mismatched relationship.
        """

        transport = selected.get("tran")
        if isinstance(transport, str) and transport:
            return transport
        if transport not in (None, ""):
            raise ExternalVolumeIdentityError(
                "partition transport identity is malformed",
                sanitized_code="MALFORMED_BLOCK_DEVICE_IDENTITY",
            )
        if selected.get("type") != "part":
            raise ExternalVolumeIdentityError(
                "transport-less block device has no supported parent relationship",
                sanitized_code="BLOCK_DEVICE_PARENT_MISSING",
            )
        parent_path = selected.get("pkname")
        if (
            not isinstance(parent_path, str)
            or not parent_path.startswith("/dev/")
            or parent_path == selected.get("path")
        ):
            raise ExternalVolumeIdentityError(
                "partition parent identity is missing",
                sanitized_code="BLOCK_DEVICE_PARENT_MISSING",
            )
        parent_payload = cls._json_command(
            [
                "lsblk",
                "--json",
                "--bytes",
                "--paths",
                "--output",
                cls._LSBLK_COLUMNS,
                parent_path,
            ],
            "BLOCK_DEVICE_PARENT_PROBE_FAILED",
        )
        parent_devices = _nested_block_devices(
            parent_payload.get("blockdevices")
        )
        parents = [
            item
            for item in parent_devices
            if item.get("path") == parent_path
        ]
        if not parents:
            raise ExternalVolumeIdentityError(
                "partition parent is absent from block-device identity",
                sanitized_code="BLOCK_DEVICE_PARENT_MISSING",
            )
        if len(parents) != 1:
            raise ExternalVolumeIdentityError(
                "partition parent identity is ambiguous",
                sanitized_code="BLOCK_DEVICE_PARENT_AMBIGUOUS",
            )
        confirmed_children = [
            item
            for item in parent_devices
            if item.get("path") == selected.get("path")
        ]
        if len(confirmed_children) != 1:
            raise ExternalVolumeIdentityError(
                "partition relationship changed during parent verification",
                sanitized_code="BLOCK_DEVICE_PARENT_MISMATCH",
            )
        confirmed_child = confirmed_children[0]
        relationship_fields = (
            "path",
            "kname",
            "pkname",
            "type",
            "tran",
            "uuid",
            "label",
            "ro",
        )
        if any(
            confirmed_child.get(field) != selected.get(field)
            for field in relationship_fields
        ):
            raise ExternalVolumeIdentityError(
                "partition identity changed during parent verification",
                sanitized_code="BLOCK_DEVICE_PARENT_MISMATCH",
            )
        parent = parents[0]
        if (
            selected.get("pkname") != parent.get("path")
            or parent.get("kname") != parent.get("path")
            or parent.get("type") != "disk"
            or parent.get("pkname") not in (None, "")
        ):
            raise ExternalVolumeIdentityError(
                "partition and parent block-device identities do not match",
                sanitized_code="BLOCK_DEVICE_PARENT_MISMATCH",
            )
        try:
            parent_mode = os.stat(parent_path, follow_symlinks=True).st_mode
        except OSError as exc:
            raise ExternalVolumeUnavailableError(
                "partition parent block-device metadata is unavailable",
                sanitized_code="BLOCK_DEVICE_PARENT_UNAVAILABLE",
            ) from exc
        if not stat.S_ISBLK(parent_mode):
            raise ExternalVolumeIdentityError(
                "partition parent is not a block device",
                sanitized_code="BLOCK_DEVICE_PARENT_MISMATCH",
            )
        parent_transport = parent.get("tran")
        if parent_transport != "usb":
            raise ExternalVolumeIdentityError(
                "partition parent transport is not the approved USB transport",
                sanitized_code="BLOCK_DEVICE_PARENT_TRANSPORT_MISMATCH",
            )
        return "usb"

    def inspect(self, mountpoint: Path) -> ExternalMountIdentity:
        mount_payload = self._json_command(
            [
                "findmnt",
                "--json",
                "--bytes",
                "--output",
                "SOURCE,TARGET,FSTYPE,OPTIONS",
                "--target",
                str(mountpoint),
            ],
            "MOUNT_PROBE_FAILED",
        )
        filesystems = mount_payload.get("filesystems")
        if not isinstance(filesystems, list) or len(filesystems) != 1:
            raise ExternalVolumeUnavailableError(
                "configured mountpoint did not resolve to one filesystem",
                sanitized_code="MOUNT_NOT_FOUND",
            )
        mounted = filesystems[0]
        if not isinstance(mounted, Mapping):
            raise ExternalVolumeUnavailableError(
                "configured mountpoint returned malformed identity",
                sanitized_code="MALFORMED_MOUNT_IDENTITY",
            )
        source = mounted.get("source")
        target = mounted.get("target")
        filesystem_type = mounted.get("fstype")
        options = mounted.get("options")
        if not all(
            isinstance(value, str) and value
            for value in (source, target, filesystem_type, options)
        ):
            raise ExternalVolumeUnavailableError(
                "configured mountpoint returned incomplete identity",
                sanitized_code="MALFORMED_MOUNT_IDENTITY",
            )
        assert isinstance(source, str)
        assert isinstance(target, str)
        assert isinstance(filesystem_type, str)
        assert isinstance(options, str)
        if not source.startswith("/dev/"):
            raise ExternalVolumeIdentityError(
                "external volume is not backed by an explicit block device",
                sanitized_code="NON_BLOCK_MOUNT_SOURCE",
            )

        block_payload = self._json_command(
            [
                "lsblk",
                "--json",
                "--bytes",
                "--paths",
                "--output",
                self._LSBLK_COLUMNS,
                source,
            ],
            "BLOCK_DEVICE_PROBE_FAILED",
        )
        devices = _nested_block_devices(block_payload.get("blockdevices"))
        selected_devices = [
            item for item in devices if item.get("path") == source
        ]
        if not selected_devices:
            raise ExternalVolumeIdentityError(
                "mount source is absent from block-device identity",
                sanitized_code="BLOCK_DEVICE_IDENTITY_MISSING",
            )
        if len(selected_devices) != 1:
            raise ExternalVolumeIdentityError(
                "mount source block-device identity is ambiguous",
                sanitized_code="BLOCK_DEVICE_IDENTITY_AMBIGUOUS",
            )
        selected = selected_devices[0]
        if selected.get("kname") != source:
            raise ExternalVolumeIdentityError(
                "mount source block-device identity does not match",
                sanitized_code="BLOCK_DEVICE_IDENTITY_MISMATCH",
            )
        device_transport = self._resolved_transport(selected)
        try:
            source_mode = os.stat(source, follow_symlinks=True).st_mode
            mount_stat = os.stat(mountpoint, follow_symlinks=False)
            root_stat = os.stat("/", follow_symlinks=False)
            usage = shutil.disk_usage(mountpoint)
        except OSError as exc:
            raise ExternalVolumeUnavailableError(
                "external-volume filesystem metadata is unavailable",
                sanitized_code="FILESYSTEM_METADATA_UNAVAILABLE",
            ) from exc
        read_only = selected.get("ro")
        if read_only not in (0, 1, False, True):
            raise ExternalVolumeIdentityError(
                "block-device read-only state is malformed",
                sanitized_code="MALFORMED_BLOCK_DEVICE_IDENTITY",
            )
        return ExternalMountIdentity(
            target=Path(target),
            source=source,
            filesystem_type=filesystem_type,
            mount_options=frozenset(
                option for option in options.split(",") if option
            ),
            device_uuid=str(selected.get("uuid") or ""),
            device_label=str(selected.get("label") or ""),
            device_transport=device_transport,
            device_read_only=bool(read_only),
            source_is_block_device=stat.S_ISBLK(source_mode),
            total_bytes=usage.total,
            available_bytes=usage.free,
            mount_device_id=mount_stat.st_dev,
            system_root_device_id=root_stat.st_dev,
        )


__all__ = ["LinuxExternalVolumeProbe"]
