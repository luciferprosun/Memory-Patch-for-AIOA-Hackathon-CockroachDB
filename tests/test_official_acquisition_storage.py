"""Deterministic storage-boundary tests for official corpus acquisition."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tests._support import REPOSITORY_ROOT  # noqa: F401

from aioa_memory_kernel.acquisition.errors import (
    AcquisitionIntegrityError,
    AcquisitionStorageError,
)
from aioa_memory_kernel.acquisition.models import AcquisitionPolicy
from aioa_memory_kernel.acquisition.storage import (
    AcquisitionRootGuard,
    LAYOUT,
    _relative_path,
)


class OfficialAcquisitionStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def _policy(**updates: object) -> AcquisitionPolicy:
        policy = AcquisitionPolicy(
            maximum_root_bytes=16 * 1024 * 1024,
            initial_minimum_free_bytes=2 * 1024 * 1024,
            final_minimum_free_bytes=1024 * 1024,
            maximum_response_bytes=4 * 1024 * 1024,
        )
        return replace(policy, **updates)

    def _guard(
        self,
        *,
        name: str = "root",
        root_exists: bool = True,
        policy: AcquisitionPolicy | None = None,
    ) -> AcquisitionRootGuard:
        mountpoint = self.base / name / "mount"
        parent = mountpoint / "HAT's libary"
        parent.mkdir(parents=True)
        root = parent / "German Law Official Corpus 1A"
        if root_exists:
            root.mkdir()
        guard = AcquisitionRootGuard.__new__(AcquisitionRootGuard)
        guard.policy = policy or self._policy()
        guard.repository_root = self.base / "repository"
        guard.mountpoint = mountpoint
        guard.parent = parent
        guard.root = root
        guard.seed = parent / "German law.zip"
        guard._mount_device_id = mountpoint.lstat().st_dev
        guard.request_count = 0
        guard.created_bytes = 0
        return guard

    def test_relative_paths_reject_absolute_traversal_and_noncanonical_forms(
        self,
    ) -> None:
        invalid = (
            "/absolute",
            "../escape",
            "nested/../escape",
            "nested//object",
            "nested/./object",
            "nested\\object",
            " leading",
            "trailing ",
            "nul\x00byte",
            "line\nbreak",
            "tab\tpath",
            "",
        )
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(AcquisitionStorageError):
                    _relative_path(value)
        self.assertEqual(
            _relative_path("objects/source.xml").as_posix(),
            "objects/source.xml",
        )

    def test_resolve_rejects_symlink_parent_and_stays_root_relative(self) -> None:
        guard = self._guard()
        outside = self.base / "outside"
        outside.mkdir()
        (guard.root / "unsafe").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(AcquisitionStorageError) as caught:
            guard.resolve("unsafe/object.bin")
        self.assertEqual(caught.exception.code, "ACQUISITION_PARENT_UNSAFE")

        safe = guard.root / "safe"
        safe.mkdir()
        target = guard.resolve("safe/object.bin")
        self.assertEqual(target, safe / "object.bin")
        self.assertEqual(target.relative_to(guard.root).as_posix(), "safe/object.bin")

    def test_initialize_creates_absent_root_and_root_relative_control_state(
        self,
    ) -> None:
        guard = self._guard(root_exists=False)
        created = guard.initialize(
            {
                "schema_version": "1.0.0",
                "worktree_digest": "a" * 64,
            }
        )
        self.assertTrue(created)
        self.assertTrue(guard.root.is_dir())
        for relative in LAYOUT:
            self.assertTrue((guard.root / relative).is_dir(), relative)

        policy_path = guard.root / "00_CONTROL/acquisition-policy.json"
        policy_record = json.loads(policy_path.read_text(encoding="utf-8"))
        self.assertEqual(
            policy_record["policy"]["target_relative_path"],
            guard.policy.target_relative_path,
        )
        self.assertEqual(
            policy_record["policy"]["seed_relative_path"],
            guard.policy.seed_relative_path,
        )
        serialized = policy_path.read_text(encoding="utf-8")
        self.assertNotIn(str(self.base), serialized)
        self.assertTrue(serialized.endswith("\n"))
        self.assertEqual(guard.request_count, 0)
        self.assertGreater(guard.root_size(), 0)
        self.assertEqual(guard.created_bytes, guard.root_size())

    def test_initial_space_policy_blocks_before_root_creation(self) -> None:
        policy = self._policy(initial_minimum_free_bytes=2**62)
        guard = self._guard(root_exists=False, policy=policy)
        with self.assertRaises(AcquisitionStorageError) as caught:
            guard.initialize({"schema_version": "1.0.0"})
        self.assertEqual(
            caught.exception.code,
            "ACQUISITION_INITIAL_SPACE_INSUFFICIENT",
        )
        self.assertFalse(guard.root.exists())

    def test_resume_uses_final_reserve_and_repair_check_does_not_move_parts(
        self,
    ) -> None:
        guard = self._guard(root_exists=False)
        self.assertTrue(guard.initialize({"schema_version": "1.0.0"}))
        orphan = guard.root / "10_DE_FEDERAL_CONSOLIDATED_GII/xml-zips"
        orphan = orphan / ".acquisition-synthetic.part"
        orphan.write_bytes(b"diagnostic")

        resumed = self._guard(name="resume-space-shadow")
        resumed.mountpoint = guard.mountpoint
        resumed.parent = guard.parent
        resumed.root = guard.root
        resumed._mount_device_id = guard._mount_device_id
        resumed.policy = guard.policy
        between_initial_and_final = (
            guard.policy.final_minimum_free_bytes + 1
        )
        statvfs = SimpleNamespace(
            f_bavail=between_initial_and_final,
            f_frsize=1,
        )
        with patch(
            "aioa_memory_kernel.acquisition.storage.os.statvfs",
            return_value=statvfs,
        ):
            self.assertFalse(
                resumed.initialize(
                    {"schema_version": "1.0.0"},
                    reconcile_orphan_parts=False,
                )
            )
        self.assertEqual(orphan.read_bytes(), b"diagnostic")
        self.assertEqual(
            list((guard.root / "90_QUARANTINE/orphan-parts").iterdir()),
            [],
        )

    def test_stream_writer_hashes_exact_chunks_publishes_and_removes_part(
        self,
    ) -> None:
        guard = self._guard()
        objects = guard.root / "objects"
        objects.mkdir()
        payload = b"alpha" + b"-" + b"omega"
        with guard.stream_writer("objects/payload.bin") as writer:
            writer.write(b"alpha")
            writer.write(b"-")
            writer.write(b"omega")
            digest, length = writer.publish()

        self.assertEqual(digest, hashlib.sha256(payload).hexdigest())
        self.assertEqual(length, len(payload))
        self.assertEqual((objects / "payload.bin").read_bytes(), payload)
        self.assertEqual(guard.created_bytes, len(payload))
        self.assertEqual(list(objects.glob(".acquisition-*.part")), [])

    def test_existing_target_and_concurrent_target_are_never_overwritten(
        self,
    ) -> None:
        guard = self._guard()
        objects = guard.root / "objects"
        objects.mkdir()
        target = objects / "payload.bin"
        target.write_bytes(b"original")
        with self.assertRaises(AcquisitionStorageError) as caught:
            guard.stream_writer("objects/payload.bin")
        self.assertEqual(caught.exception.code, "ACQUISITION_TARGET_EXISTS")
        self.assertEqual(target.read_bytes(), b"original")

        concurrent = guard.stream_writer("objects/concurrent.bin")
        with concurrent:
            concurrent.write(b"new-data")
            (objects / "concurrent.bin").write_bytes(b"raced-original")
            with self.assertRaises(AcquisitionStorageError) as raced:
                concurrent.publish()
        self.assertEqual(raced.exception.code, "ACQUISITION_TARGET_EXISTS")
        self.assertEqual(
            (objects / "concurrent.bin").read_bytes(),
            b"raced-original",
        )
        self.assertTrue(concurrent.part.exists())

    def test_failed_validation_preserves_part_for_bounded_resume_diagnostics(
        self,
    ) -> None:
        guard = self._guard()
        (guard.root / "objects").mkdir()
        (guard.root / "90_QUARANTINE/orphan-parts").mkdir(parents=True)

        def reject(_path: Path) -> None:
            raise AcquisitionIntegrityError(
                "synthetic validation rejection",
                code="SYNTHETIC_REJECTED",
            )

        writer = guard.stream_writer("objects/rejected.bin")
        with self.assertRaises(AcquisitionIntegrityError):
            with writer:
                writer.write(b"untrusted")
                writer.publish((reject,))
        self.assertFalse((guard.root / "objects/rejected.bin").exists())
        self.assertTrue(writer.part.exists())
        quarantined = guard.quarantine_orphan_parts()
        self.assertEqual(len(quarantined), 1)
        self.assertTrue(quarantined[0].startswith("90_QUARANTINE/orphan-parts/"))
        self.assertFalse(writer.part.exists())
        self.assertEqual(
            guard.resolve(quarantined[0]).read_bytes(),
            b"untrusted",
        )
        self.assertTrue(
            guard.resolve(quarantined[0].removesuffix(".bin") + ".json").is_file()
        )

    def test_response_root_and_free_space_budgets_fail_before_writing_chunk(
        self,
    ) -> None:
        cases = (
            (
                "response",
                self._policy(maximum_response_bytes=3),
                0,
                10 * 1024 * 1024,
                b"four",
                "SIZE_LIMIT_EXCEEDED",
            ),
            (
                "root",
                self._policy(maximum_root_bytes=5),
                3,
                10 * 1024 * 1024,
                b"three",
                "ACQUISITION_ROOT_SIZE_LIMIT",
            ),
            (
                "reserve",
                self._policy(
                    initial_minimum_free_bytes=6,
                    final_minimum_free_bytes=5,
                ),
                0,
                5,
                b"x",
                "ACQUISITION_FREE_SPACE_RESERVE",
            ),
        )
        for name, policy, created, free, payload, expected_code in cases:
            with self.subTest(case=name):
                guard = self._guard(name=name, policy=policy)
                (guard.root / "objects").mkdir()
                guard.created_bytes = created
                guard.free_bytes = lambda free=free: free  # type: ignore[method-assign]
                writer = guard.stream_writer("objects/bounded.bin")
                with self.assertRaises(AcquisitionStorageError) as caught:
                    with writer:
                        writer.write(payload)
                self.assertEqual(caught.exception.code, expected_code)
                self.assertEqual(writer.byte_length, 0)
                self.assertFalse((guard.root / "objects/bounded.bin").exists())

    def test_resume_reloads_ledgers_and_rejects_policy_conflict(self) -> None:
        guard = self._guard(root_exists=False)
        self.assertTrue(guard.initialize({"schema_version": "1.0.0"}))
        guard.append_jsonl(
            "00_CONTROL/request-ledger.jsonl",
            {"event": "HTTP_REQUEST_ATTEMPT", "request_sequence": 1},
        )
        guard.append_jsonl(
            "00_CONTROL/request-ledger.jsonl",
            {"event": "HTTP_REQUEST_ATTEMPT", "request_sequence": 2},
        )
        guard.append_jsonl(
            "00_CONTROL/object-ledger.jsonl",
            {"event": "OBJECT_PUBLISHED", "byte_length": 17},
        )
        expected_root_size = guard.root_size()

        resumed = self._guard(name="resume-shadow")
        resumed.mountpoint = guard.mountpoint
        resumed.parent = guard.parent
        resumed.root = guard.root
        resumed._mount_device_id = guard._mount_device_id
        resumed.policy = guard.policy
        self.assertFalse(resumed.initialize({"schema_version": "1.0.0"}))
        self.assertEqual(resumed.request_count, 2)
        self.assertEqual(resumed.created_bytes, expected_root_size)

        conflict = self._guard(name="conflict-shadow")
        conflict.mountpoint = guard.mountpoint
        conflict.parent = guard.parent
        conflict.root = guard.root
        conflict._mount_device_id = guard._mount_device_id
        conflict.policy = replace(guard.policy, maximum_requests=999)
        with self.assertRaises(AcquisitionIntegrityError) as caught:
            conflict.initialize({"schema_version": "1.0.0"})
        self.assertEqual(caught.exception.code, "ACQUISITION_POLICY_CONFLICT")

    def test_root_size_counts_regular_files_without_following_symlinks(self) -> None:
        guard = self._guard()
        data = guard.root / "data"
        data.mkdir()
        (data / "one.bin").write_bytes(b"123")
        (data / "two.bin").write_bytes(b"4567")
        self.assertEqual(guard.root_size(), 7)

    def test_root_size_rejects_symlink_directory_instead_of_undercounting(
        self,
    ) -> None:
        guard = self._guard()
        outside = self.base / "external-directory"
        outside.mkdir()
        (outside / "hidden.bin").write_bytes(b"not-owned")
        (guard.root / "linked").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(AcquisitionStorageError) as caught:
            guard.root_size()
        self.assertEqual(caught.exception.code, "ACQUISITION_ROOT_UNSAFE")

    def test_resume_ledger_stream_is_closed_after_counting(self) -> None:
        guard = self._guard()

        class TrackingStream:
            def __init__(self) -> None:
                self.closed = False

            def __iter__(self):
                return iter(
                    (
                        '{"event":"HTTP_REQUEST_ATTEMPT",'
                        '"request_sequence":1}\n',
                        "\n",
                    )
                )

            def __enter__(self):
                return self

            def __exit__(self, *_arguments: object) -> None:
                self.close()

            def close(self) -> None:
                self.closed = True

        class FakeLedgerPath:
            def __init__(self, stream: TrackingStream) -> None:
                self.stream = stream

            def open(self, mode: str, **_kwargs: object):
                self_mode = mode
                if self_mode != "r":
                    raise AssertionError("unexpected ledger mode")
                return self.stream

        stream = TrackingStream()
        original_resolve = guard.resolve
        guard.resolve = (  # type: ignore[method-assign]
            lambda relative: FakeLedgerPath(stream)
            if relative == "00_CONTROL/request-ledger.jsonl"
            else original_resolve(relative)
        )
        guard.root_size = lambda: 0  # type: ignore[method-assign]
        guard._load_ledger_state()
        self.assertEqual(guard.request_count, 1)
        self.assertTrue(stream.closed)


if __name__ == "__main__":
    unittest.main()
