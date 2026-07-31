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
                "PATH,TYPE,TRAN,UUID,LABEL,RO",
                source,
            ],
            "BLOCK_DEVICE_PROBE_FAILED",
        )
        devices = _nested_block_devices(block_payload.get("blockdevices"))
        selected = next(
            (item for item in devices if item.get("path") == source),
            None,
        )
        if selected is None:
            raise ExternalVolumeIdentityError(
                "mount source is absent from block-device identity",
                sanitized_code="BLOCK_DEVICE_IDENTITY_MISSING",
            )
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
            device_transport=str(selected.get("tran") or ""),
            device_read_only=bool(read_only),
            source_is_block_device=stat.S_ISBLK(source_mode),
            total_bytes=usage.total,
            available_bytes=usage.free,
            mount_device_id=mount_stat.st_dev,
            system_root_device_id=root_stat.st_dev,
        )


__all__ = ["LinuxExternalVolumeProbe"]
