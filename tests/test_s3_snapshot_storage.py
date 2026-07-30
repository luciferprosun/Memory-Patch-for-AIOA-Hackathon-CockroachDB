"""Offline Step 7 S3 snapshot, Object Lock, and Step 9 compatibility tests."""

from __future__ import annotations

import ast
import hashlib
import importlib
import json
import sys
import unittest
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

from tests._support import REPOSITORY_ROOT, SOURCE_ROOT


if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from aioa_memory_kernel.contracts import StorageClass  # noqa: E402
from aioa_memory_kernel.persistence import (  # noqa: E402
    TransactionBoundaryViolation,
)
from aioa_memory_kernel.sources import SourcePublicationState  # noqa: E402
from aioa_memory_kernel.storage import (  # noqa: E402
    EXACT_BYTES_SERIALIZATION_VERSION,
    SNAPSHOT_SERIALIZATION_VERSION,
    STORAGE_EVIDENCE_ONLY,
    S3ObjectLockMode,
    S3SnapshotAdapter,
    S3SnapshotConfig,
    SnapshotAccessDeniedError,
    SnapshotCapabilityError,
    SnapshotConfigurationError,
    SnapshotConflictError,
    SnapshotEnvelope,
    SnapshotIntegrityError,
    SnapshotMalformedResponseError,
    SnapshotOperationError,
    SnapshotServiceUnavailableError,
    SnapshotSessionError,
    SnapshotStorageEvidence,
)
from tests.test_source_registry import make_record  # noqa: E402


NOW = datetime(2040, 1, 2, 12, 0, 0, tzinfo=UTC)
RETAIN_UNTIL = NOW + timedelta(days=7)
VERSION_ID = "synthetic-version-id-1"
STORAGE_ROOT = SOURCE_ROOT / "aioa_memory_kernel" / "storage"
STEP9_MIGRATION = (
    REPOSITORY_ROOT
    / "sql"
    / "cockroachdb"
    / "migrations"
    / "0006_step9_source_registry_provenance_publication_states.sql"
)
STEP9_EVIDENCE = (
    REPOSITORY_ROOT
    / "docs"
    / "evidence"
    / "cockroachdb-v26-2"
    / "step9-source-registry-validation.json"
)


class FakeAwsError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__("sanitized synthetic AWS failure")
        self.response = {"Error": {"Code": code}}


class EndpointConnectionError(Exception):
    pass


class NoCredentialsError(Exception):
    pass


class FakeBody:
    def __init__(
        self,
        payload: object,
        *,
        read_error: Exception | None = None,
        close_error: Exception | None = None,
    ) -> None:
        self.payload = payload
        self.read_error = read_error
        self.close_error = close_error
        self.closed = False
        self.read_amount: int | None = None

    def read(self, amount: int | None = None) -> object:
        self.read_amount = amount
        if self.read_error is not None:
            raise self.read_error
        if amount is not None and isinstance(self.payload, bytes):
            return self.payload[:amount]
        return self.payload

    def close(self) -> None:
        self.closed = True
        if self.close_error is not None:
            raise self.close_error


class FakeS3Client:
    def __init__(self) -> None:
        self.responses: dict[str, list[object]] = defaultdict(list)
        self.calls: list[tuple[str, dict[str, object]]] = []

    def queue(self, operation: str, *responses: object) -> None:
        self.responses[operation].extend(responses)

    def _call(self, operation: str, kwargs: dict[str, object]) -> object:
        self.calls.append((operation, kwargs))
        if not self.responses[operation]:
            raise AssertionError(f"missing fake response for {operation}")
        response = self.responses[operation].pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def get_bucket_location(self, **kwargs: object) -> object:
        return self._call("get_bucket_location", kwargs)

    def get_bucket_versioning(self, **kwargs: object) -> object:
        return self._call("get_bucket_versioning", kwargs)

    def get_object_lock_configuration(self, **kwargs: object) -> object:
        return self._call("get_object_lock_configuration", kwargs)

    def put_object(self, **kwargs: object) -> object:
        return self._call("put_object", kwargs)

    def head_object(self, **kwargs: object) -> object:
        return self._call("head_object", kwargs)

    def get_object(self, **kwargs: object) -> object:
        return self._call("get_object", kwargs)


def make_config(**overrides: object) -> S3SnapshotConfig:
    values: dict[str, object] = {
        "region": "eu-central-1",
        "bucket_name": "memory-patch-step7-synthetic",
        "retention_mode": S3ObjectLockMode.GOVERNANCE,
        "retention_days": 7,
        "key_prefix": "memory-patch/snapshots/v1",
    }
    values.update(overrides)
    return S3SnapshotConfig(**values)  # type: ignore[arg-type]


def make_snapshot(
    *,
    payload: object | None = None,
    captured_at: datetime = NOW,
    retain_until: datetime = RETAIN_UNTIL,
    storage_class: StorageClass = StorageClass.S3_GLOBAL_LOCKED_SNAPSHOT,
    retention_mode: object = S3ObjectLockMode.GOVERNANCE,
    source_artifact_digest: str | None = None,
    serialization_version: str = SNAPSHOT_SERIALIZATION_VERSION,
    media_type: str = "application/json",
) -> SnapshotEnvelope:
    if payload is None:
        payload = {
            "source": "synthetic-step7",
            "facts": {"alpha": 1, "beta": 2},
        }
    return SnapshotEnvelope(
        tenant_id="tenant-step7",
        source_id="source-step7",
        hat_scope_id="hat-scope-step7",
        payload=payload,
        serialization_version=serialization_version,
        media_type=media_type,
        captured_at=captured_at,
        retain_until=retain_until,
        retention_mode=retention_mode,  # type: ignore[arg-type]
        authority_metadata={
            "semantic_authority": "LOCAL_MEMORY_PATCH_ONLY",
            "storage_can_publish": False,
        },
        provenance_metadata={
            "origin": "synthetic-unit-test",
            "adapter_version": "1a",
        },
        source_artifact_digest=source_artifact_digest,
        storage_class=storage_class,
    )


def expected_metadata(snapshot: SnapshotEnvelope) -> dict[str, str]:
    values = {
        "snapshot-id": snapshot.snapshot_id,
        "canonical-sha256": snapshot.content_sha256,
        "content-length": str(snapshot.content_length),
        "snapshot-schema-version": snapshot.schema_version,
        "serialization-version": snapshot.serialization_version,
        "media-type": snapshot.media_type,
        "storage-class": snapshot.storage_class.value,
        "scope-digest": snapshot.scope_digest,
        "manifest-sha256": snapshot.manifest_sha256,
        "authority-status": STORAGE_EVIDENCE_ONLY,
    }
    if snapshot.source_artifact_digest is not None:
        values["source-artifact-digest"] = snapshot.source_artifact_digest
    return values


def object_response(
    snapshot: SnapshotEnvelope,
    *,
    version_id: str = VERSION_ID,
    payload: object | None = None,
    metadata: object | None = None,
) -> dict[str, object]:
    response: dict[str, object] = {
        "VersionId": version_id,
        "ContentLength": snapshot.content_length,
        "Metadata": (
            expected_metadata(snapshot) if metadata is None else metadata
        ),
        "ChecksumSHA256": snapshot.checksum_sha256_base64,
        "ContentType": snapshot.media_type,
        "ObjectLockMode": "GOVERNANCE",
        "ObjectLockRetainUntilDate": snapshot.retain_until,
        "ServerSideEncryption": "AES256",
    }
    if payload is not None:
        response["Body"] = FakeBody(payload)
    return response


def queue_capabilities(
    client: FakeS3Client,
    *,
    location: object = "eu-central-1",
    versioning: object = "Enabled",
    lock_enabled: object = "Enabled",
    mode: object = "GOVERNANCE",
    days: object = 7,
) -> None:
    client.queue(
        "get_bucket_location",
        {"LocationConstraint": location},
    )
    client.queue("get_bucket_versioning", {"Status": versioning})
    client.queue(
        "get_object_lock_configuration",
        {
            "ObjectLockConfiguration": {
                "ObjectLockEnabled": lock_enabled,
                "Rule": {
                    "DefaultRetention": {
                        "Mode": mode,
                        "Days": days,
                    }
                },
            }
        },
    )


def make_adapter(
    client: FakeS3Client,
    config: S3SnapshotConfig | None = None,
) -> S3SnapshotAdapter:
    return S3SnapshotAdapter(
        config or make_config(),
        client,
        clock=lambda: NOW,
    )


class SnapshotConfigurationTests(unittest.TestCase):
    def test_valid_configuration_is_explicit_and_non_secret(self) -> None:
        config = make_config(expected_bucket_owner="0" * 12)
        self.assertEqual(config.region, "eu-central-1")
        self.assertEqual(config.retention_days, 7)
        self.assertTrue(config.require_object_lock)
        self.assertNotIn(config.bucket_name, config.bucket_reference)

    def test_missing_configuration_fails_closed(self) -> None:
        with self.assertRaises(SnapshotConfigurationError):
            make_config(region="")
        with self.assertRaises(SnapshotConfigurationError):
            make_config(bucket_name="")

    def test_malformed_bucket_names_fail_closed(self) -> None:
        for bucket_name in (
            "UPPERCASE",
            "ab",
            "192.168.1.1",
            "bad..bucket",
            "xn--reserved-name",
            "reserved--ol-s3",
        ):
            with self.subTest(bucket_name=bucket_name):
                with self.assertRaises(SnapshotConfigurationError):
                    make_config(bucket_name=bucket_name)

    def test_malformed_prefixes_fail_closed(self) -> None:
        for prefix in ("/absolute", "trailing/", "double//slash", "a/../b"):
            with self.subTest(prefix=prefix):
                with self.assertRaises(SnapshotConfigurationError):
                    make_config(key_prefix=prefix)

    def test_unsupported_retention_mode_is_rejected(self) -> None:
        with self.assertRaises(SnapshotConfigurationError):
            make_config(retention_mode="COMPLIANCE")

    def test_private_snapshot_cannot_enter_locked_global_adapter(self) -> None:
        with self.assertRaises(SnapshotConfigurationError) as caught:
            make_snapshot(storage_class=StorageClass.S3_USER_PRIVATE_SNAPSHOT)
        self.assertEqual(
            caught.exception.sanitized_code,
            "PRIVATE_SNAPSHOT_REQUIRES_SEPARATE_STORAGE",
        )

    def test_naive_or_subsecond_retention_timestamp_is_rejected(self) -> None:
        with self.assertRaises(SnapshotConfigurationError):
            make_snapshot(retain_until=datetime(2040, 1, 9, 12, 0, 0))
        with self.assertRaises(SnapshotConfigurationError):
            make_snapshot(
                retain_until=datetime(
                    2040,
                    1,
                    9,
                    12,
                    0,
                    0,
                    1,
                    tzinfo=UTC,
                )
            )

    def test_deterministic_object_key_hides_scope_labels(self) -> None:
        config = make_config()
        snapshot = make_snapshot()
        first = config.object_key(
            snapshot.snapshot_id,
            snapshot.scope_digest,
            snapshot.object_suffix,
        )
        second = config.object_key(
            snapshot.snapshot_id,
            snapshot.scope_digest,
            snapshot.object_suffix,
        )
        self.assertEqual(first, second)
        self.assertNotIn(snapshot.tenant_id, first)
        self.assertNotIn(snapshot.source_id, first)
        self.assertTrue(first.endswith(f"{snapshot.snapshot_id}.json"))

    def test_object_key_rejects_noncanonical_snapshot_identity(self) -> None:
        with self.assertRaises(SnapshotConfigurationError) as caught:
            make_config().object_key(
                "s3snap-../escape",
                "0" * 64,
            )
        self.assertEqual(caught.exception.sanitized_code, "INVALID_SNAPSHOT_ID")

    def test_exact_bytes_keep_their_original_hash_and_binary_key(self) -> None:
        payload = b"\x00synthetic exact source bytes\xff"
        artifact_digest = hashlib.sha256(payload).hexdigest()
        snapshot = make_snapshot(
            payload=payload,
            serialization_version=EXACT_BYTES_SERIALIZATION_VERSION,
            media_type="application/octet-stream",
            source_artifact_digest=artifact_digest,
        )
        key = make_config().object_key(
            snapshot.snapshot_id,
            snapshot.scope_digest,
            snapshot.object_suffix,
        )
        self.assertEqual(snapshot.canonical_payload, payload)
        self.assertEqual(snapshot.content_sha256, artifact_digest)
        self.assertTrue(key.endswith(".bin"))

    def test_exact_bytes_must_match_referenced_step9_artifact(self) -> None:
        with self.assertRaises(SnapshotConfigurationError) as caught:
            make_snapshot(
                payload=b"exact bytes",
                serialization_version=EXACT_BYTES_SERIALIZATION_VERSION,
                media_type="application/octet-stream",
                source_artifact_digest="0" * 64,
            )
        self.assertEqual(
            caught.exception.sanitized_code,
            "SOURCE_ARTIFACT_DIGEST_MISMATCH",
        )

    def test_canonical_snapshot_hash_is_mapping_order_stable(self) -> None:
        first = make_snapshot(payload={"a": 1, "b": {"x": 2, "y": 3}})
        second = make_snapshot(payload={"b": {"y": 3, "x": 2}, "a": 1})
        self.assertEqual(first.canonical_payload, second.canonical_payload)
        self.assertEqual(first.content_sha256, second.content_sha256)
        self.assertEqual(first.snapshot_id, second.snapshot_id)

    def test_snapshot_identity_changes_with_retention_intent(self) -> None:
        first = make_snapshot()
        second = make_snapshot(retain_until=RETAIN_UNTIL + timedelta(days=1))
        self.assertNotEqual(first.snapshot_id, second.snapshot_id)


class BucketCapabilityTests(unittest.TestCase):
    def test_required_bucket_capabilities_pass(self) -> None:
        client = FakeS3Client()
        queue_capabilities(client)
        result = make_adapter(client).inspect_bucket_capabilities()
        self.assertEqual(result.region, "eu-central-1")
        self.assertEqual(result.versioning_status, "Enabled")
        self.assertTrue(result.object_lock_enabled)
        self.assertEqual(result.default_retention_days, 7)

    def test_region_mismatch_fails_closed(self) -> None:
        client = FakeS3Client()
        queue_capabilities(client, location="eu-west-1")
        with self.assertRaises(SnapshotCapabilityError):
            make_adapter(client).inspect_bucket_capabilities()

    def test_versioning_disabled_fails_closed(self) -> None:
        client = FakeS3Client()
        queue_capabilities(client, versioning="Suspended")
        with self.assertRaises(SnapshotCapabilityError):
            make_adapter(client).inspect_bucket_capabilities()

    def test_object_lock_or_default_retention_mismatch_fails_closed(self) -> None:
        for values in (
            {"lock_enabled": "Disabled"},
            {"mode": "COMPLIANCE"},
            {"days": 8},
        ):
            with self.subTest(values=values):
                client = FakeS3Client()
                queue_capabilities(client, **values)
                with self.assertRaises(SnapshotCapabilityError):
                    make_adapter(client).inspect_bucket_capabilities()

    def test_missing_object_lock_configuration_is_a_capability_failure(
        self,
    ) -> None:
        client = FakeS3Client()
        client.queue(
            "get_bucket_location",
            {"LocationConstraint": "eu-central-1"},
        )
        client.queue("get_bucket_versioning", {"Status": "Enabled"})
        client.queue(
            "get_object_lock_configuration",
            FakeAwsError("ObjectLockConfigurationNotFoundError"),
        )
        with self.assertRaises(SnapshotCapabilityError) as caught:
            make_adapter(client).inspect_bucket_capabilities()
        self.assertEqual(
            caught.exception.sanitized_code,
            "BUCKET_OBJECT_LOCK_REQUIRED",
        )


class PersistenceAndVerificationTests(unittest.TestCase):
    def test_successful_upload_returns_structured_storage_evidence(self) -> None:
        client = FakeS3Client()
        snapshot = make_snapshot()
        queue_capabilities(client)
        client.queue(
            "put_object",
            {
                "VersionId": VERSION_ID,
                "ChecksumSHA256": snapshot.checksum_sha256_base64,
            },
        )
        client.queue("head_object", object_response(snapshot))
        evidence = make_adapter(client).persist_snapshot(snapshot)
        self.assertEqual(evidence.version_id, VERSION_ID)
        self.assertTrue(evidence.metadata_verified)
        self.assertFalse(evidence.content_verified)
        self.assertFalse(evidence.idempotent_replay)
        self.assertEqual(evidence.authority_status, STORAGE_EVIDENCE_ONLY)
        self.assertNotEqual(evidence.evidence_digest, snapshot.content_sha256)

    def test_upload_request_contains_checksum_metadata_and_object_lock(self) -> None:
        client = FakeS3Client()
        snapshot = make_snapshot()
        queue_capabilities(client)
        client.queue("put_object", {"VersionId": VERSION_ID})
        client.queue("head_object", object_response(snapshot))
        make_adapter(client).persist_snapshot(snapshot)
        call = next(
            parameters
            for operation, parameters in client.calls
            if operation == "put_object"
        )
        self.assertEqual(call["Body"], snapshot.canonical_payload)
        self.assertEqual(call["ContentLength"], snapshot.content_length)
        self.assertEqual(call["ContentType"], snapshot.media_type)
        self.assertEqual(
            call["ChecksumSHA256"],
            snapshot.checksum_sha256_base64,
        )
        self.assertEqual(call["IfNoneMatch"], "*")
        self.assertEqual(call["Metadata"], expected_metadata(snapshot))
        self.assertEqual(call["ObjectLockMode"], "GOVERNANCE")
        self.assertEqual(
            call["ObjectLockRetainUntilDate"],
            snapshot.retain_until,
        )
        self.assertEqual(call["ServerSideEncryption"], "AES256")
        self.assertNotIn("ACL", call)
        self.assertNotIn("BypassGovernanceRetention", call)

    def test_persist_requires_full_retention_period_from_write_time(self) -> None:
        client = FakeS3Client()
        snapshot = make_snapshot(
            captured_at=NOW - timedelta(days=6),
            retain_until=NOW + timedelta(days=1),
        )
        with self.assertRaises(SnapshotConfigurationError) as caught:
            make_adapter(client).persist_snapshot(snapshot)
        self.assertEqual(
            caught.exception.sanitized_code,
            "RETENTION_WINDOW_TOO_SHORT",
        )
        self.assertEqual(client.calls, [])

    def test_missing_version_id_fails_closed_after_put(self) -> None:
        client = FakeS3Client()
        queue_capabilities(client)
        client.queue("put_object", {})
        with self.assertRaises(SnapshotMalformedResponseError) as caught:
            make_adapter(client).persist_snapshot(make_snapshot())
        self.assertEqual(caught.exception.sanitized_code, "MISSING_S3_VERSION_ID")

    def test_partial_success_with_unverifiable_head_is_not_success(self) -> None:
        client = FakeS3Client()
        snapshot = make_snapshot()
        queue_capabilities(client)
        client.queue("put_object", {"VersionId": VERSION_ID})
        malformed = object_response(snapshot)
        malformed.pop("ObjectLockRetainUntilDate")
        client.queue("head_object", malformed)
        with self.assertRaises(SnapshotIntegrityError):
            make_adapter(client).persist_snapshot(snapshot)

    def test_access_denial_is_translated_without_raw_message(self) -> None:
        client = FakeS3Client()
        queue_capabilities(client)
        client.queue("put_object", FakeAwsError("AccessDenied"))
        with self.assertRaises(SnapshotAccessDeniedError) as caught:
            make_adapter(client).persist_snapshot(make_snapshot())
        self.assertEqual(caught.exception.aws_error_code, "AccessDenied")
        self.assertNotIn("synthetic AWS failure", str(caught.exception))

    def test_expired_session_is_translated(self) -> None:
        client = FakeS3Client()
        client.queue("get_bucket_location", FakeAwsError("ExpiredToken"))
        with self.assertRaises(SnapshotSessionError) as caught:
            make_adapter(client).inspect_bucket_capabilities()
        self.assertEqual(caught.exception.aws_error_code, "ExpiredToken")

    def test_missing_credentials_exception_is_translated(self) -> None:
        client = FakeS3Client()
        client.queue(
            "get_bucket_location",
            NoCredentialsError("synthetic credential detail"),
        )
        with self.assertRaises(SnapshotSessionError) as caught:
            make_adapter(client).inspect_bucket_capabilities()
        self.assertEqual(caught.exception.sanitized_code, "AWS_SESSION_INVALID")
        self.assertNotIn("synthetic credential detail", str(caught.exception))

    def test_network_unavailability_is_translated(self) -> None:
        client = FakeS3Client()
        client.queue(
            "get_bucket_location",
            EndpointConnectionError("private endpoint omitted"),
        )
        with self.assertRaises(SnapshotServiceUnavailableError):
            make_adapter(client).inspect_bucket_capabilities()

    def test_malformed_sdk_response_fails_closed(self) -> None:
        client = FakeS3Client()
        client.queue("get_bucket_location", ["not", "a", "mapping"])
        with self.assertRaises(SnapshotMalformedResponseError):
            make_adapter(client).inspect_bucket_capabilities()

    def test_retrieval_verifies_exact_bytes_and_closes_body(self) -> None:
        client = FakeS3Client()
        snapshot = make_snapshot()
        body = FakeBody(snapshot.canonical_payload)
        response = object_response(snapshot)
        response["Body"] = body
        client.queue("get_object", response)
        retrieved = make_adapter(client).retrieve_snapshot(
            snapshot,
            version_id=VERSION_ID,
        )
        self.assertEqual(retrieved.payload, snapshot.canonical_payload)
        self.assertTrue(retrieved.evidence.content_verified)
        self.assertTrue(body.closed)
        self.assertEqual(body.read_amount, snapshot.content_length + 1)

    def test_stream_close_failure_is_sanitized_and_fails_closed(self) -> None:
        client = FakeS3Client()
        snapshot = make_snapshot()
        response = object_response(snapshot)
        response["Body"] = FakeBody(
            snapshot.canonical_payload,
            close_error=RuntimeError("synthetic raw close detail"),
        )
        client.queue("get_object", response)
        with self.assertRaises(SnapshotOperationError) as caught:
            make_adapter(client).retrieve_snapshot(
                snapshot,
                version_id=VERSION_ID,
            )
        self.assertEqual(str(caught.exception), "S3 operation failed closed")
        self.assertNotIn("synthetic raw close detail", str(caught.exception))


    def test_retrieval_remains_verifiable_after_retention_expires(self) -> None:
        client = FakeS3Client()
        snapshot = make_snapshot()
        client.queue(
            "get_object",
            object_response(
                snapshot,
                payload=snapshot.canonical_payload,
            ),
        )
        adapter = S3SnapshotAdapter(
            make_config(),
            client,
            clock=lambda: RETAIN_UNTIL + timedelta(days=1),
        )
        evidence = adapter.verify_snapshot(snapshot, version_id=VERSION_ID)
        self.assertTrue(evidence.content_verified)

    def test_retrieval_hash_mismatch_fails_closed(self) -> None:
        client = FakeS3Client()
        snapshot = make_snapshot()
        client.queue(
            "get_object",
            object_response(snapshot, payload=b"different"),
        )
        with self.assertRaises(SnapshotIntegrityError) as caught:
            make_adapter(client).retrieve_snapshot(
                snapshot,
                version_id=VERSION_ID,
            )
        self.assertEqual(
            caught.exception.sanitized_code,
            "S3_CONTENT_HASH_MISMATCH",
        )

    def test_metadata_mismatch_fails_closed(self) -> None:
        client = FakeS3Client()
        snapshot = make_snapshot()
        metadata = expected_metadata(snapshot)
        metadata["canonical-sha256"] = "0" * 64
        client.queue(
            "get_object",
            object_response(
                snapshot,
                payload=snapshot.canonical_payload,
                metadata=metadata,
            ),
        )
        with self.assertRaises(SnapshotIntegrityError) as caught:
            make_adapter(client).retrieve_snapshot(
                snapshot,
                version_id=VERSION_ID,
            )
        self.assertEqual(caught.exception.sanitized_code, "S3_METADATA_MISMATCH")

    def test_unexpected_metadata_fails_closed(self) -> None:
        client = FakeS3Client()
        snapshot = make_snapshot()
        metadata = expected_metadata(snapshot)
        metadata["unexpected-authority"] = "forbidden"
        client.queue(
            "get_object",
            object_response(
                snapshot,
                payload=snapshot.canonical_payload,
                metadata=metadata,
            ),
        )
        with self.assertRaises(SnapshotIntegrityError) as caught:
            make_adapter(client).retrieve_snapshot(
                snapshot,
                version_id=VERSION_ID,
            )
        self.assertEqual(caught.exception.sanitized_code, "S3_METADATA_MISMATCH")

    def test_missing_exact_version_is_an_integrity_failure(self) -> None:
        client = FakeS3Client()
        client.queue("get_object", FakeAwsError("NoSuchVersion"))
        with self.assertRaises(SnapshotIntegrityError) as caught:
            make_adapter(client).retrieve_snapshot(
                make_snapshot(),
                version_id=VERSION_ID,
            )
        self.assertEqual(caught.exception.sanitized_code, "S3_SNAPSHOT_NOT_FOUND")

    def test_version_mismatch_fails_closed(self) -> None:
        client = FakeS3Client()
        snapshot = make_snapshot()
        client.queue(
            "get_object",
            object_response(
                snapshot,
                version_id="different-version",
                payload=snapshot.canonical_payload,
            ),
        )
        with self.assertRaises(SnapshotIntegrityError) as caught:
            make_adapter(client).retrieve_snapshot(
                snapshot,
                version_id=VERSION_ID,
            )
        self.assertEqual(caught.exception.sanitized_code, "S3_VERSION_MISMATCH")

    def test_exact_repeat_is_idempotent_after_conditional_failure(self) -> None:
        client = FakeS3Client()
        snapshot = make_snapshot()
        queue_capabilities(client)
        client.queue("put_object", FakeAwsError("PreconditionFailed"))
        client.queue("head_object", object_response(snapshot))
        client.queue(
            "get_object",
            object_response(
                snapshot,
                payload=snapshot.canonical_payload,
            ),
        )
        evidence = make_adapter(client).persist_snapshot(snapshot)
        self.assertTrue(evidence.idempotent_replay)
        self.assertTrue(evidence.content_verified)
        put_calls = [
            call for call in client.calls if call[0] == "put_object"
        ]
        self.assertEqual(len(put_calls), 1)

    def test_conflicting_repeat_does_not_create_ambiguous_record(self) -> None:
        client = FakeS3Client()
        snapshot = make_snapshot()
        queue_capabilities(client)
        client.queue("put_object", FakeAwsError("PreconditionFailed"))
        client.queue(
            "head_object",
            object_response(snapshot, metadata={"snapshot-id": "different"}),
        )
        with self.assertRaises(SnapshotConflictError) as caught:
            make_adapter(client).persist_snapshot(snapshot)
        self.assertEqual(
            caught.exception.sanitized_code,
            "SNAPSHOT_IDEMPOTENCY_CONFLICT",
        )

    def test_external_calls_are_rejected_inside_persistence_transaction(self) -> None:
        client = FakeS3Client()
        boundary = TransactionBoundaryViolation(
            "synthetic open transaction",
            sanitized_code="OPEN_PERSISTENCE_TRANSACTION",
        )
        with mock.patch(
            "aioa_memory_kernel.storage.s3."
            "assert_no_open_persistence_transaction",
            side_effect=boundary,
        ):
            with self.assertRaises(TransactionBoundaryViolation):
                make_adapter(client).inspect_bucket_capabilities()
        self.assertEqual(client.calls, [])


class PublicApiAndImportSafetyTests(unittest.TestCase):
    def test_public_api_has_no_delete_or_retention_bypass_method(self) -> None:
        public_names = {
            name
            for name in dir(S3SnapshotAdapter)
            if not name.startswith("_")
        }
        for forbidden in (
            "delete_object",
            "delete_bucket",
            "bypass_governance_retention",
            "put_object_retention",
        ):
            self.assertNotIn(forbidden, public_names)

    def test_storage_package_has_no_sdk_or_shell_dependency(self) -> None:
        for path in STORAGE_ROOT.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            imported = {
                node.names[0].name
                for node in ast.walk(tree)
                if isinstance(node, ast.Import)
            }
            imported.update(
                node.module or ""
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom)
            )
            self.assertNotIn("boto3", imported)
            self.assertNotIn("botocore", imported)
            self.assertNotIn("subprocess", imported)

    def test_import_performs_no_socket_or_aws_client_call(self) -> None:
        import aioa_memory_kernel.storage as storage

        with mock.patch(
            "socket.socket",
            side_effect=AssertionError("network call during import"),
        ):
            reloaded = importlib.reload(storage)
        self.assertIs(reloaded.S3SnapshotAdapter, storage.S3SnapshotAdapter)

    def test_unit_test_package_contains_no_live_bucket_or_credential_use(self) -> None:
        text = "\n".join(
            path.read_text(encoding="utf-8")
            for path in STORAGE_ROOT.glob("*.py")
        ).casefold()
        for forbidden in (
            "aws_" + "access_key_id",
            "aws_" + "secret_access_key",
            "aws_" + "session_token",
            "boto3" + ".client",
            "aws " + "s3api",
        ):
            self.assertNotIn(forbidden, text)


class Step9CompatibilityTests(unittest.TestCase):
    def test_snapshot_reference_does_not_grant_publication_authority(
        self,
    ) -> None:
        record = make_record()
        snapshot = make_snapshot(
            source_artifact_digest=record.artifact.artifact_digest
        )
        self.assertEqual(
            snapshot.source_artifact_digest,
            record.artifact.artifact_digest,
        )
        self.assertEqual(
            record.current_publication_state,
            SourcePublicationState.REGISTERED,
        )

    def test_successful_upload_does_not_change_step9_registry_state(self) -> None:
        record = make_record()
        before = (
            record.registry_digest,
            record.current_publication_state,
            record.current_publication_sequence,
            record.current_publication_event_digest,
        )
        snapshot = make_snapshot(
            source_artifact_digest=record.artifact.artifact_digest
        )
        client = FakeS3Client()
        queue_capabilities(client)
        client.queue("put_object", {"VersionId": VERSION_ID})
        client.queue("head_object", object_response(snapshot))
        evidence = make_adapter(client).persist_snapshot(snapshot)
        self.assertEqual(evidence.authority_status, STORAGE_EVIDENCE_ONLY)
        self.assertEqual(
            before,
            (
                record.registry_digest,
                record.current_publication_state,
                record.current_publication_sequence,
                record.current_publication_event_digest,
            ),
        )

    def test_s3_failure_does_not_mutate_step9_publication_record(self) -> None:
        record = make_record()
        before = json.dumps(
            {
                "digest": record.registry_digest,
                "state": record.current_publication_state.value,
                "sequence": record.current_publication_sequence,
            },
            sort_keys=True,
        )
        client = FakeS3Client()
        queue_capabilities(client)
        client.queue("put_object", FakeAwsError("AccessDenied"))
        with self.assertRaises(SnapshotAccessDeniedError):
            make_adapter(client).persist_snapshot(make_snapshot())
        after = json.dumps(
            {
                "digest": record.registry_digest,
                "state": record.current_publication_state.value,
                "sequence": record.current_publication_sequence,
            },
            sort_keys=True,
        )
        self.assertEqual(before, after)

    def test_storage_evidence_exposes_no_publication_or_approval_operation(
        self,
    ) -> None:
        fields = set(SnapshotStorageEvidence.__dataclass_fields__)
        for forbidden in (
            "publication_state",
            "publication_eligibility",
            "approval",
            "commit",
            "execute",
            "source_authority",
        ):
            self.assertNotIn(forbidden, fields)

    def test_step9_migration_and_evidence_remain_unchanged(self) -> None:
        evidence = json.loads(STEP9_EVIDENCE.read_text(encoding="utf-8"))
        digest = hashlib.sha256(STEP9_MIGRATION.read_bytes()).hexdigest()
        self.assertEqual(
            digest,
            evidence["migration"]["migration_0006_sha256"],
        )
        self.assertNotIn("step7", STEP9_EVIDENCE.name)


if __name__ == "__main__":
    unittest.main()
