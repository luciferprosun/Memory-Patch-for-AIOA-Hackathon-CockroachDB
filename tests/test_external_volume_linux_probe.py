"""Focused Linux parent-transport inheritance tests for Step 8."""

from __future__ import annotations

import stat
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tests._support import REPOSITORY_ROOT  # noqa: F401

from aioa_memory_kernel.runtime.external_volume_linux import (
    LinuxExternalVolumeProbe,
)
from aioa_memory_kernel.storage import ExternalVolumeIdentityError


def _partition(*, transport: object = None, parent: object = "/dev/sda"):
    return {
        "path": "/dev/sda1",
        "kname": "/dev/sda1",
        "pkname": parent,
        "type": "part",
        "tran": transport,
        "uuid": "synthetic-uuid",
        "label": "SYNTHETIC",
        "ro": False,
    }


def _parent(*, transport: object = "usb", pkname: object = None):
    return {
        "path": "/dev/sda",
        "kname": "/dev/sda",
        "pkname": pkname,
        "type": "disk",
        "tran": transport,
        "uuid": None,
        "label": None,
        "ro": False,
    }


class LinuxExternalVolumeTransportTests(unittest.TestCase):
    def test_partition_usb_transport_passes_without_parent_probe(self) -> None:
        with patch.object(
            LinuxExternalVolumeProbe,
            "_json_command",
        ) as command:
            self.assertEqual(
                LinuxExternalVolumeProbe._resolved_transport(
                    _partition(transport="usb")
                ),
                "usb",
            )
        command.assert_not_called()

    def test_partition_empty_inherits_exact_usb_parent(self) -> None:
        payload = {"blockdevices": [_parent(), _partition()]}
        block_mode = SimpleNamespace(st_mode=stat.S_IFBLK)
        command = Mock(return_value=payload)
        with (
            patch.object(
                LinuxExternalVolumeProbe,
                "_json_command",
                command,
            ),
            patch(
                "aioa_memory_kernel.runtime.external_volume_linux.os.stat",
                return_value=block_mode,
            ),
        ):
            self.assertEqual(
                LinuxExternalVolumeProbe._resolved_transport(_partition()),
                "usb",
            )
        command.assert_called_once_with(
            [
                "lsblk",
                "--json",
                "--bytes",
                "--paths",
                "--output",
                LinuxExternalVolumeProbe._LSBLK_COLUMNS,
                "/dev/sda",
            ],
            "BLOCK_DEVICE_PARENT_PROBE_FAILED",
        )

    def test_partition_empty_rejects_non_usb_parent(self) -> None:
        payload = {
            "blockdevices": [_parent(transport="nvme"), _partition()]
        }
        block_mode = SimpleNamespace(st_mode=stat.S_IFBLK)
        with (
            patch.object(
                LinuxExternalVolumeProbe,
                "_json_command",
                return_value=payload,
            ),
            patch(
                "aioa_memory_kernel.runtime.external_volume_linux.os.stat",
                return_value=block_mode,
            ),
            self.assertRaises(ExternalVolumeIdentityError) as caught,
        ):
            LinuxExternalVolumeProbe._resolved_transport(_partition())
        self.assertEqual(
            caught.exception.sanitized_code,
            "BLOCK_DEVICE_PARENT_TRANSPORT_MISMATCH",
        )

    def test_partition_empty_rejects_missing_parent(self) -> None:
        with self.assertRaises(ExternalVolumeIdentityError) as caught:
            LinuxExternalVolumeProbe._resolved_transport(
                _partition(parent=None)
            )
        self.assertEqual(
            caught.exception.sanitized_code,
            "BLOCK_DEVICE_PARENT_MISSING",
        )

    def test_partition_parent_mismatch_fails_closed(self) -> None:
        payload = {
            "blockdevices": [
                _parent(pkname="/dev/controller0"),
                _partition(),
            ]
        }
        with (
            patch.object(
                LinuxExternalVolumeProbe,
                "_json_command",
                return_value=payload,
            ),
            self.assertRaises(ExternalVolumeIdentityError) as caught,
        ):
            LinuxExternalVolumeProbe._resolved_transport(_partition())
        self.assertEqual(
            caught.exception.sanitized_code,
            "BLOCK_DEVICE_PARENT_MISMATCH",
        )

    def test_partition_ambiguous_parent_fails_closed(self) -> None:
        payload = {
            "blockdevices": [_parent(), _parent(), _partition()]
        }
        with (
            patch.object(
                LinuxExternalVolumeProbe,
                "_json_command",
                return_value=payload,
            ),
            self.assertRaises(ExternalVolumeIdentityError) as caught,
        ):
            LinuxExternalVolumeProbe._resolved_transport(_partition())
        self.assertEqual(
            caught.exception.sanitized_code,
            "BLOCK_DEVICE_PARENT_AMBIGUOUS",
        )

    def test_partition_changed_identity_fails_closed(self) -> None:
        changed = _partition()
        changed["uuid"] = "changed-uuid"
        payload = {"blockdevices": [_parent(), changed]}
        with (
            patch.object(
                LinuxExternalVolumeProbe,
                "_json_command",
                return_value=payload,
            ),
            self.assertRaises(ExternalVolumeIdentityError) as caught,
        ):
            LinuxExternalVolumeProbe._resolved_transport(_partition())
        self.assertEqual(
            caught.exception.sanitized_code,
            "BLOCK_DEVICE_PARENT_MISMATCH",
        )


if __name__ == "__main__":
    unittest.main()
