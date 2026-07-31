"""Offline Step 8 external-volume runtime and fail-closed policy tests."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from tests._support import REPOSITORY_ROOT

from aioa_memory_kernel.contracts import StorageClass
from aioa_memory_kernel.storage import (
    DEFAULT_MINIMUM_FREE_BYTES,
    EXTERNAL_VOLUME_CREATION_HEAD,
    EXTERNAL_VOLUME_EXPECTED_REMOTE,
    EXTERNAL_VOLUME_MARKER_NAME,
    EXTERNAL_VOLUME_PROJECT_ID,
    ExternalMountIdentity,
    ExternalVolumeConfig,
    ExternalVolumeConfigurationError,
    ExternalVolumeConflictError,
    ExternalVolumeFailurePolicy,
    ExternalVolumeIdentityError,
    ExternalVolumeIntegrityError,
    ExternalVolumeOperation,
    ExternalVolumeOperationDisabledError,
    ExternalVolumeRuntimeAdapter,
    ExternalVolumeUnavailableError,
    ExternalVolumeUnsafePathError,
    load_external_volume_environment,
)
from aioa_memory_kernel.storage import external_volume as external_volume_module


DEVICE_UUID = "11111111-2222-3333-4444-555555555555"
DEVICE_LABEL = "SYNTHETIC_EXTERNAL"
TRANSPORT = "usb"
TOTAL_BYTES = 128 * 1024**3
AVAILABLE_BYTES = 96 * 1024**3
STEP7_FIXTURE = (
    REPOSITORY_ROOT / "tests" / "fixtures" / "step7_live_validation_snapshot.json"
)
STEP7_SHA256 = "d1bedd6275072d01cc932af7a57d826837e27b9c7d1039bb558d4039abf819fc"


class FakeProbe:
    def __init__(self, identity: ExternalMountIdentity | Exception) -> None:
        self.identity = identity
        self.calls = 0

    def inspect(self, mountpoint: Path) -> ExternalMountIdentity:
        self.calls += 1
        if isinstance(self.identity, Exception):
            raise self.identity
        return self.identity


def config_values(mountpoint: Path) -> dict[str, str]:
    return {
        "AIOA_EXTERNAL_MOUNTPOINT": str(mountpoint),
        "AIOA_EXTERNAL_DATA_ROOT": str(
            mountpoint / "AIOA_DATA" / "Memory-Patch-for-AIOA"
        ),
        "AIOA_EXTERNAL_DEVICE_UUID": DEVICE_UUID,
        "AIOA_EXTERNAL_DEVICE_LABEL": DEVICE_LABEL,
        "AIOA_EXTERNAL_FILESYSTEM_TYPE": "ext4",
        "AIOA_EXTERNAL_DEVICE_TRANSPORT": TRANSPORT,
    }


class ExternalVolumeFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.mountpoint = Path(self.temporary.name) / "external"
        self.data_root = (
            self.mountpoint / "AIOA_DATA" / "Memory-Patch-for-AIOA"
        )
        self.data_root.mkdir(parents=True)
        for relative_path in external_volume_module._REQUIRED_DIRECTORIES:
            (self.data_root / relative_path).mkdir(parents=True, exist_ok=True)
        marker = {
            "schema_version": "1.0.0",
            "project_id": EXTERNAL_VOLUME_PROJECT_ID,
            "purpose": "external-data-volume",
            "device_uuid": DEVICE_UUID,
            "device_label": DEVICE_LABEL,
            "filesystem_type": "ext4",
            "created_at_utc": "2026-07-25T05:25:40Z",
            "repository_remote": EXTERNAL_VOLUME_EXPECTED_REMOTE,
            "repository_head_at_creation": EXTERNAL_VOLUME_CREATION_HEAD,
        }
        self.marker_path = self.data_root / EXTERNAL_VOLUME_MARKER_NAME
        self.marker_path.write_text(
            json.dumps(marker, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        self.marker_path.chmod(0o640)
        self.config = ExternalVolumeConfig.from_mapping(
            config_values(self.mountpoint)
        )
        mount_device_id = self.mountpoint.stat().st_dev
        self.identity = ExternalMountIdentity(
            target=self.mountpoint,
            source="/dev/synthetic1",
            filesystem_type="ext4",
            mount_options=frozenset(
                {"rw", "nodev", "nosuid", "relatime"}
            ),
            device_uuid=DEVICE_UUID,
            device_label=DEVICE_LABEL,
            device_transport=TRANSPORT,
            device_read_only=False,
            source_is_block_device=True,
            total_bytes=TOTAL_BYTES,
            available_bytes=AVAILABLE_BYTES,
            mount_device_id=mount_device_id,
            system_root_device_id=mount_device_id + 1,
        )
        self.probe = FakeProbe(self.identity)
        self.adapter = ExternalVolumeRuntimeAdapter(self.config, self.probe)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def rewrite_marker(self, **updates: object) -> None:
        marker = json.loads(self.marker_path.read_text(encoding="utf-8"))
        marker.update(updates)
        self.marker_path.write_text(
            json.dumps(marker, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        self.marker_path.chmod(0o640)


class ExternalVolumeConfigurationTests(ExternalVolumeFixture):
    def test_mapping_requires_explicit_identity(self) -> None:
        values = config_values(self.mountpoint)
        del values["AIOA_EXTERNAL_DEVICE_TRANSPORT"]
        with self.assertRaises(ExternalVolumeConfigurationError) as caught:
            ExternalVolumeConfig.from_mapping(values)
        self.assertEqual(caught.exception.sanitized_code, "MISSING_EXTERNAL_CONFIG")

    def test_data_root_must_be_exact_prepared_root(self) -> None:
        values = config_values(self.mountpoint)
        values["AIOA_EXTERNAL_DATA_ROOT"] = str(self.mountpoint / "other")
        with self.assertRaises(ExternalVolumeConfigurationError):
            ExternalVolumeConfig.from_mapping(values)

    def test_paths_must_be_canonical_absolute_and_non_root(self) -> None:
        for mountpoint in ("/", "relative", "/tmp/../tmp/external"):
            with self.subTest(mountpoint=mountpoint):
                values = config_values(self.mountpoint)
                values["AIOA_EXTERNAL_MOUNTPOINT"] = mountpoint
                values["AIOA_EXTERNAL_DATA_ROOT"] = (
                    f"{mountpoint}/AIOA_DATA/Memory-Patch-for-AIOA"
                )
                with self.assertRaises(ExternalVolumeConfigurationError):
                    ExternalVolumeConfig.from_mapping(values)

    def test_capacity_policy_is_bounded(self) -> None:
        values = config_values(self.mountpoint)
        values["AIOA_EXTERNAL_MINIMUM_FREE_BYTES"] = "not-an-integer"
        with self.assertRaises(ExternalVolumeConfigurationError):
            ExternalVolumeConfig.from_mapping(values)
        values["AIOA_EXTERNAL_MINIMUM_FREE_BYTES"] = "1"
        with self.assertRaises(ExternalVolumeConfigurationError):
            ExternalVolumeConfig.from_mapping(values)

    def test_policy_cannot_weaken_required_mount_options(self) -> None:
        with self.assertRaises(ExternalVolumeConfigurationError):
            replace(self.config, required_mount_options=frozenset({"rw"}))

    def test_environment_file_is_parsed_without_shell_execution(self) -> None:
        environment_path = Path(self.temporary.name) / "external-data.env"
        environment_path.write_text(
            "\n".join(
                (
                    f'AIOA_EXTERNAL_MOUNTPOINT="{self.mountpoint}"',
                    'AIOA_EXTERNAL_DATA_ROOT="${AIOA_EXTERNAL_MOUNTPOINT}/'
                    'AIOA_DATA/Memory-Patch-for-AIOA"',
                    f'AIOA_EXTERNAL_DEVICE_UUID="{DEVICE_UUID}"',
                    f'AIOA_EXTERNAL_DEVICE_LABEL="{DEVICE_LABEL}"',
                    'AIOA_EXTERNAL_FILESYSTEM_TYPE="ext4"',
                    f'AIOA_EXTERNAL_DEVICE_TRANSPORT="{TRANSPORT}"',
                    "",
                )
            ),
            encoding="utf-8",
        )
        environment_path.chmod(0o600)
        loaded = load_external_volume_environment(environment_path)
        loaded_config = ExternalVolumeConfig.from_mapping(loaded)
        self.assertEqual(loaded_config, self.config)

    def test_environment_file_rejects_shell_syntax_and_forward_references(
        self,
    ) -> None:
        environment_path = Path(self.temporary.name) / "unsafe.env"
        unsafe_values = (
            'AIOA_EXTERNAL_MOUNTPOINT="$(touch /tmp/never)"\n',
            'AIOA_EXTERNAL_DATA_ROOT="${AIOA_EXTERNAL_MOUNTPOINT}/data"\n',
            "AIOA_EXTERNAL_MOUNTPOINT=/tmp/external\n",
        )
        for content in unsafe_values:
            with self.subTest(content=content):
                environment_path.write_text(content, encoding="utf-8")
                environment_path.chmod(0o600)
                with self.assertRaises(ExternalVolumeConfigurationError):
                    load_external_volume_environment(environment_path)

    def test_environment_file_must_be_private_regular_file(self) -> None:
        environment_path = Path(self.temporary.name) / "public.env"
        environment_path.write_text('KEY="value"\n', encoding="utf-8")
        environment_path.chmod(0o644)
        with self.assertRaises(ExternalVolumeConfigurationError) as caught:
            load_external_volume_environment(environment_path)
        self.assertEqual(
            caught.exception.sanitized_code,
            "UNSAFE_EXTERNAL_ENV_FILE",
        )


class ExternalVolumeIdentityTests(ExternalVolumeFixture):
    def test_complete_identity_marker_tree_and_space_pass(self) -> None:
        status = self.adapter.verify(require_write=True)
        self.assertTrue(status.mount_identity_verified)
        self.assertTrue(status.marker_identity_verified)
        self.assertTrue(status.root_filesystem_distinct)
        self.assertTrue(status.writable_verified)
        self.assertEqual(status.storage_class, StorageClass.EXTERNAL_DERIVED)
        self.assertFalse(status.system_drive_fallback_allowed)
        self.assertEqual(status.reserve_bytes, DEFAULT_MINIMUM_FREE_BYTES)
        evidence = json.dumps(status.to_dict(), sort_keys=True)
        self.assertNotIn(DEVICE_UUID, evidence)
        self.assertNotIn(DEVICE_LABEL, evidence)
        self.assertNotIn(str(self.mountpoint), evidence)
        self.assertNotIn(self.identity.source, evidence)

    def test_every_operation_reprobes_live_identity(self) -> None:
        self.adapter.verify()
        self.adapter.resolve_path(
            ExternalVolumeOperation.VALIDATION_EVIDENCE,
            "first.json",
        )
        self.assertEqual(self.probe.calls, 2)

    def test_identity_mismatches_fail_closed(self) -> None:
        changes = (
            ("target", self.mountpoint / "wrong", "EXTERNAL_MOUNTPOINT_MISMATCH"),
            ("filesystem_type", "xfs", "EXTERNAL_FILESYSTEM_MISMATCH"),
            ("device_uuid", "wrong-uuid", "EXTERNAL_UUID_MISMATCH"),
            ("device_label", "WRONG", "EXTERNAL_LABEL_MISMATCH"),
            ("device_transport", "mmc", "EXTERNAL_TRANSPORT_MISMATCH"),
            ("source_is_block_device", False, "NON_BLOCK_MOUNT_SOURCE"),
            ("device_read_only", True, "EXTERNAL_DEVICE_READ_ONLY"),
        )
        for field_name, value, expected_code in changes:
            with self.subTest(field_name=field_name):
                probe = FakeProbe(replace(self.identity, **{field_name: value}))
                adapter = ExternalVolumeRuntimeAdapter(self.config, probe)
                with self.assertRaises(
                    (ExternalVolumeIdentityError, ExternalVolumeUnavailableError)
                ) as caught:
                    adapter.verify()
                self.assertEqual(caught.exception.sanitized_code, expected_code)

    def test_read_only_or_weakened_mount_options_fail_closed(self) -> None:
        identities = (
            replace(
                self.identity,
                mount_options=frozenset({"ro", "nodev", "nosuid"}),
            ),
            replace(
                self.identity,
                mount_options=frozenset({"rw", "relatime"}),
            ),
        )
        for identity in identities:
            with self.subTest(options=identity.mount_options):
                adapter = ExternalVolumeRuntimeAdapter(
                    self.config,
                    FakeProbe(identity),
                )
                with self.assertRaises(ExternalVolumeUnavailableError) as caught:
                    adapter.verify()
                self.assertEqual(
                    caught.exception.sanitized_code,
                    "UNSAFE_EXTERNAL_MOUNT_OPTIONS",
                )

    def test_system_root_filesystem_fallback_is_rejected(self) -> None:
        identity = replace(
            self.identity,
            system_root_device_id=self.identity.mount_device_id,
        )
        adapter = ExternalVolumeRuntimeAdapter(
            self.config,
            FakeProbe(identity),
        )
        with self.assertRaises(ExternalVolumeIdentityError) as caught:
            adapter.verify()
        self.assertEqual(
            caught.exception.sanitized_code,
            "SYSTEM_DRIVE_FALLBACK_DETECTED",
        )

    def test_conservative_free_space_reserve_is_enforced(self) -> None:
        identity = replace(
            self.identity,
            available_bytes=DEFAULT_MINIMUM_FREE_BYTES,
        )
        adapter = ExternalVolumeRuntimeAdapter(
            self.config,
            FakeProbe(identity),
        )
        with self.assertRaises(ExternalVolumeUnavailableError) as caught:
            adapter.verify()
        self.assertEqual(
            caught.exception.sanitized_code,
            "EXTERNAL_FREE_SPACE_EXHAUSTED",
        )

    def test_missing_or_mismatched_marker_fails_closed(self) -> None:
        self.rewrite_marker(device_uuid="different-uuid")
        with self.assertRaises(ExternalVolumeIdentityError) as caught:
            self.adapter.verify()
        self.assertEqual(
            caught.exception.sanitized_code,
            "EXTERNAL_MARKER_IDENTITY_MISMATCH",
        )

    def test_marker_schema_extra_field_fails_closed(self) -> None:
        self.rewrite_marker(unexpected=True)
        with self.assertRaises(ExternalVolumeIntegrityError) as caught:
            self.adapter.verify()
        self.assertEqual(
            caught.exception.sanitized_code,
            "EXTERNAL_MARKER_SCHEMA_MISMATCH",
        )

    def test_group_or_world_writable_marker_is_rejected(self) -> None:
        self.marker_path.chmod(0o662)
        with self.assertRaises(ExternalVolumeIdentityError) as caught:
            self.adapter.verify()
        self.assertEqual(
            caught.exception.sanitized_code,
            "UNSAFE_EXTERNAL_MARKER",
        )

    def test_required_directory_symlink_is_rejected(self) -> None:
        unsafe = self.data_root / "reports"
        unsafe.rmdir()
        unsafe.symlink_to(self.data_root / "logs", target_is_directory=True)
        with self.assertRaises(ExternalVolumeUnsafePathError):
            self.adapter.verify()


class ExternalVolumePathPolicyTests(ExternalVolumeFixture):
    def test_every_operation_has_explicit_no_fallback_policy(self) -> None:
        for operation in ExternalVolumeOperation:
            with self.subTest(operation=operation):
                policy = self.adapter.operation_policy(operation)
                self.assertEqual(policy.operation, operation)
                self.assertFalse(policy.system_drive_fallback_allowed)
                self.assertEqual(policy.storage_class, StorageClass.EXTERNAL_DERIVED)
                self.assertIn(
                    policy.failure_policy,
                    {
                        ExternalVolumeFailurePolicy.FAIL_CLOSED,
                        ExternalVolumeFailurePolicy.DISABLE_OPERATION_WITHOUT_FALLBACK,
                    },
                )
        self.assertFalse(self.adapter.system_drive_fallback_allowed)

    def test_paths_are_bound_to_operation_specific_roots(self) -> None:
        resolved = self.adapter.resolve_path(
            ExternalVolumeOperation.INGESTION_STAGING,
            "synthetic.bin",
            require_write=True,
        )
        self.assertEqual(
            resolved,
            self.data_root / "ingestion" / "downloads" / "synthetic.bin",
        )

    def test_absolute_noncanonical_and_traversal_paths_are_rejected(self) -> None:
        unsafe_paths = (
            "/absolute",
            "../escape",
            "nested/../escape",
            "double//segment",
            "dot/./segment",
            "back\\slash",
            " trailing",
            f"nested/{EXTERNAL_VOLUME_MARKER_NAME}",
        )
        for unsafe_path in unsafe_paths:
            with self.subTest(path=unsafe_path):
                with self.assertRaises(ExternalVolumeUnsafePathError):
                    self.adapter.resolve_path(
                        ExternalVolumeOperation.VALIDATION_EVIDENCE,
                        unsafe_path,
                    )

    def test_symlink_parent_escape_is_rejected(self) -> None:
        link = self.data_root / "reports" / "escape"
        link.symlink_to(Path(self.temporary.name), target_is_directory=True)
        with self.assertRaises(ExternalVolumeUnsafePathError):
            self.adapter.resolve_path(
                ExternalVolumeOperation.VALIDATION_EVIDENCE,
                "escape/payload.json",
            )

    def test_missing_parent_is_not_created(self) -> None:
        with self.assertRaises(ExternalVolumeUnavailableError):
            self.adapter.resolve_path(
                ExternalVolumeOperation.VALIDATION_EVIDENCE,
                "missing/payload.json",
                require_write=True,
            )
        self.assertFalse((self.data_root / "reports" / "missing").exists())

    def test_optional_cache_failure_disables_without_fallback(self) -> None:
        probe = FakeProbe(
            ExternalVolumeUnavailableError(
                "synthetic unavailable",
                sanitized_code="SYNTHETIC_UNAVAILABLE",
            )
        )
        adapter = ExternalVolumeRuntimeAdapter(self.config, probe)
        with self.assertRaises(ExternalVolumeOperationDisabledError) as caught:
            adapter.resolve_path(
                ExternalVolumeOperation.PACKAGE_CACHE,
                "entry.bin",
            )
        self.assertEqual(caught.exception.operation, "PACKAGE_CACHE")
        self.assertFalse(caught.exception.system_drive_fallback_allowed)

    def test_required_operation_failure_remains_fail_closed(self) -> None:
        probe = FakeProbe(
            ExternalVolumeUnavailableError(
                "synthetic unavailable",
                sanitized_code="SYNTHETIC_UNAVAILABLE",
            )
        )
        adapter = ExternalVolumeRuntimeAdapter(self.config, probe)
        with self.assertRaises(ExternalVolumeUnavailableError) as caught:
            adapter.resolve_path(
                ExternalVolumeOperation.INGESTION_STAGING,
                "entry.bin",
            )
        self.assertEqual(caught.exception.operation, "INGESTION_STAGING")
        self.assertFalse(caught.exception.system_drive_fallback_allowed)


class ExternalVolumeAtomicWriteTests(ExternalVolumeFixture):
    def test_atomic_no_overwrite_write_and_exact_read_back(self) -> None:
        payload = b'{"kind":"synthetic-step8","value":1}\n'
        digest = hashlib.sha256(payload).hexdigest()
        evidence = self.adapter.atomic_write_exact(
            ExternalVolumeOperation.VALIDATION_EVIDENCE,
            "step8-synthetic.json",
            payload,
            expected_sha256=digest,
            expected_length=len(payload),
        )
        self.assertTrue(evidence.atomic_no_replace)
        self.assertTrue(evidence.exact_read_back)
        self.assertTrue(evidence.file_fsync_completed)
        self.assertTrue(evidence.directory_fsync_completed)
        self.assertEqual(evidence.storage_class, StorageClass.EXTERNAL_DERIVED)
        self.assertFalse(evidence.system_drive_fallback_allowed)
        read_back = self.adapter.read_exact(
            ExternalVolumeOperation.VALIDATION_EVIDENCE,
            "step8-synthetic.json",
            expected_sha256=digest,
            expected_length=len(payload),
        )
        self.assertEqual(read_back, payload)
        staging = list(
            (self.data_root / "reports").glob(".aioa-step8-atomic-*.tmp")
        )
        self.assertEqual(staging, [])

    def test_existing_target_is_never_replaced(self) -> None:
        target = self.data_root / "reports" / "existing.json"
        target.write_bytes(b"original")
        payload = b"replacement"
        with self.assertRaises(ExternalVolumeConflictError) as caught:
            self.adapter.atomic_write_exact(
                ExternalVolumeOperation.VALIDATION_EVIDENCE,
                "existing.json",
                payload,
                expected_sha256=hashlib.sha256(payload).hexdigest(),
                expected_length=len(payload),
            )
        self.assertEqual(caught.exception.sanitized_code, "EXTERNAL_TARGET_EXISTS")
        self.assertEqual(target.read_bytes(), b"original")

    def test_payload_identity_mismatch_occurs_before_any_write(self) -> None:
        target = self.data_root / "reports" / "mismatch.json"
        with self.assertRaises(ExternalVolumeIntegrityError):
            self.adapter.atomic_write_exact(
                ExternalVolumeOperation.VALIDATION_EVIDENCE,
                "mismatch.json",
                b"payload",
                expected_sha256="0" * 64,
                expected_length=7,
            )
        self.assertFalse(target.exists())
        self.assertEqual(
            list((self.data_root / "reports").glob(".aioa-step8-atomic-*.tmp")),
            [],
        )

    def test_incomplete_target_bound_staging_is_preserved_and_blocks_write(
        self,
    ) -> None:
        relative_path = "recovery.json"
        staging_key = hashlib.sha256(
            f"reports/{relative_path}".encode("utf-8")
        ).hexdigest()[:16]
        staging = (
            self.data_root
            / "reports"
            / f".aioa-step8-atomic-{staging_key}-1234-recovery.tmp"
        )
        staging.write_bytes(b"incomplete")
        artifacts = self.adapter.incomplete_atomic_artifacts(
            ExternalVolumeOperation.VALIDATION_EVIDENCE,
            relative_path,
        )
        self.assertEqual(
            artifacts,
            (f"reports/{staging.name}",),
        )
        payload = b"replacement"
        with self.assertRaises(ExternalVolumeConflictError) as caught:
            self.adapter.atomic_write_exact(
                ExternalVolumeOperation.VALIDATION_EVIDENCE,
                relative_path,
                payload,
                expected_sha256=hashlib.sha256(payload).hexdigest(),
                expected_length=len(payload),
            )
        self.assertEqual(
            caught.exception.sanitized_code,
            "EXTERNAL_STAGING_ARTIFACT_EXISTS",
        )
        self.assertEqual(staging.read_bytes(), b"incomplete")
        self.assertFalse((self.data_root / "reports" / relative_path).exists())

    def test_exact_read_rejects_symlinks_and_special_files(self) -> None:
        regular = self.data_root / "reports" / "regular.bin"
        regular.write_bytes(b"payload")
        symlink = self.data_root / "reports" / "symlink.bin"
        symlink.symlink_to(regular)
        fifo = self.data_root / "reports" / "fifo"
        os.mkfifo(fifo, 0o600)
        digest = hashlib.sha256(b"payload").hexdigest()
        for relative_path in ("symlink.bin", "fifo"):
            with self.subTest(relative_path=relative_path):
                with self.assertRaises(ExternalVolumeUnsafePathError):
                    self.adapter.read_exact(
                        ExternalVolumeOperation.VALIDATION_EVIDENCE,
                        relative_path,
                        expected_sha256=digest,
                        expected_length=7,
                    )

    def test_step7_exact_fixture_is_compatible_with_step8_staging(self) -> None:
        payload = STEP7_FIXTURE.read_bytes()
        self.assertEqual(len(payload), 88)
        self.assertEqual(hashlib.sha256(payload).hexdigest(), STEP7_SHA256)
        evidence = self.adapter.atomic_write_exact(
            ExternalVolumeOperation.APPLICATION_SNAPSHOT_STAGING,
            f"step8-validation-{STEP7_SHA256}.json",
            payload,
            expected_sha256=STEP7_SHA256,
            expected_length=88,
        )
        self.assertEqual(evidence.content_sha256, STEP7_SHA256)
        self.assertEqual(evidence.content_length, 88)
        self.assertEqual(
            evidence.relative_path,
            f"snapshots/application/step8-validation-{STEP7_SHA256}.json",
        )

    def test_public_adapter_has_no_delete_or_fallback_api(self) -> None:
        public_names = set(dir(self.adapter))
        self.assertNotIn("delete", public_names)
        self.assertNotIn("remove", public_names)
        self.assertNotIn("fallback_path", public_names)
        self.assertNotIn("credential_path", public_names)
        self.assertNotIn("database_path", public_names)


class ExternalVolumeSourceSecurityTests(unittest.TestCase):
    def test_runtime_source_uses_no_shell_or_overwrite_primitive(self) -> None:
        source = (
            REPOSITORY_ROOT
            / "src"
            / "aioa_memory_kernel"
            / "storage"
            / "external_volume.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("shell=True", source)
        self.assertNotIn("os.replace(", source)
        self.assertNotIn("shutil.copy", source)
        self.assertNotIn("tempfile.mktemp", source)
        self.assertIn('"O_NOFOLLOW"', source)
        self.assertIn("os.O_EXCL", source)
        self.assertIn("os.fsync", source)

    def test_runtime_source_defines_external_derived_storage_only(self) -> None:
        source = (
            REPOSITORY_ROOT
            / "src"
            / "aioa_memory_kernel"
            / "storage"
            / "external_volume.py"
        ).read_text(encoding="utf-8")
        self.assertIn("StorageClass.EXTERNAL_DERIVED", source)
        self.assertNotIn("StorageClass.CRDB_TRANSACTIONAL", source)
        self.assertNotIn("StorageClass.S3_GLOBAL_LOCKED_SNAPSHOT", source)


if __name__ == "__main__":
    unittest.main()
