"""Dependency-injected S3 Snapshot Authority and Object Lock Adapter 1A."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from aioa_memory_kernel.persistence import (
    assert_no_open_persistence_transaction,
)

from .config import S3SnapshotConfig
from .errors import (
    SnapshotAccessDeniedError,
    SnapshotCapabilityError,
    SnapshotConfigurationError,
    SnapshotConflictError,
    SnapshotIntegrityError,
    SnapshotMalformedResponseError,
    SnapshotOperationError,
    SnapshotServiceUnavailableError,
    SnapshotSessionError,
    SnapshotStorageError,
)
from .models import (
    BucketCapabilities,
    RetrievedSnapshot,
    S3ObjectLockMode,
    SnapshotEnvelope,
    SnapshotStorageEvidence,
)
from .protocols import S3ClientProtocol


_SAFE_AWS_ERROR = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_ACCESS_DENIED = {
    "AccessDenied",
    "AccessDeniedException",
    "Forbidden",
    "UnauthorizedOperation",
}
_SESSION_ERRORS = {
    "ExpiredToken",
    "ExpiredTokenException",
    "InvalidAccessKeyId",
    "InvalidClientTokenId",
    "InvalidSignatureException",
    "NoCredentialsError",
    "RequestExpired",
    "SSOTokenLoadError",
    "SignatureDoesNotMatch",
    "TokenRefreshRequired",
    "UnauthorizedSSOTokenError",
    "UnrecognizedClientException",
}
_SESSION_EXCEPTION_NAMES = {
    "CredentialRetrievalError",
    "NoCredentialsError",
    "PartialCredentialsError",
    "SSOTokenLoadError",
    "TokenRetrievalError",
    "UnauthorizedSSOTokenError",
}
_UNAVAILABLE_ERRORS = {
    "InternalError",
    "InternalServerError",
    "RequestLimitExceeded",
    "RequestTimeout",
    "ServiceUnavailable",
    "SlowDown",
    "Throttling",
    "ThrottlingException",
}
_OBJECT_LOCK_ABSENT = {"ObjectLockConfigurationNotFoundError"}
_NOT_FOUND_ERRORS = {"NoSuchKey", "NoSuchVersion", "NotFound"}
_NETWORK_EXCEPTION_NAMES = {
    "ConnectTimeoutError",
    "ConnectionClosedError",
    "EndpointConnectionError",
    "HTTPClientError",
    "ReadTimeoutError",
}


def _aws_error_code(error: Exception) -> str | None:
    response = getattr(error, "response", None)
    if not isinstance(response, Mapping):
        return None
    payload = response.get("Error")
    if not isinstance(payload, Mapping):
        return None
    code = payload.get("Code")
    if not isinstance(code, str) or _SAFE_AWS_ERROR.fullmatch(code) is None:
        return None
    return code


def _translated_error(error: Exception, operation: str) -> SnapshotStorageError:
    code = _aws_error_code(error)
    exception_name = type(error).__name__
    common = {"operation": operation, "aws_error_code": code}
    if code in _ACCESS_DENIED:
        return SnapshotAccessDeniedError(
            "S3 denied the requested narrow operation",
            sanitized_code="S3_ACCESS_DENIED",
            **common,
        )
    if code in _SESSION_ERRORS or exception_name in _SESSION_EXCEPTION_NAMES:
        return SnapshotSessionError(
            "temporary AWS credentials are unavailable or expired",
            sanitized_code="AWS_SESSION_INVALID",
            **common,
        )
    if code in _OBJECT_LOCK_ABSENT:
        return SnapshotCapabilityError(
            "the target bucket has no Object Lock configuration",
            sanitized_code="BUCKET_OBJECT_LOCK_REQUIRED",
            **common,
        )
    if code in _NOT_FOUND_ERRORS:
        return SnapshotIntegrityError(
            "the requested immutable snapshot version is unavailable",
            sanitized_code="S3_SNAPSHOT_NOT_FOUND",
            **common,
        )
    if code in _UNAVAILABLE_ERRORS or exception_name in _NETWORK_EXCEPTION_NAMES:
        return SnapshotServiceUnavailableError(
            "S3 or its endpoint is unavailable",
            sanitized_code="S3_UNAVAILABLE",
            **common,
        )
    return SnapshotOperationError(
        "S3 operation failed closed",
        sanitized_code=code or "UNCLASSIFIED_S3_FAILURE",
        **common,
    )


def _mapping_response(value: object, operation: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SnapshotMalformedResponseError(
            "S3 response must be a mapping",
            operation=operation,
            sanitized_code="MALFORMED_S3_RESPONSE",
        )
    return value


def _version_id(value: object, operation: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value == "null"
        or len(value) > 1024
    ):
        raise SnapshotMalformedResponseError(
            "S3 version ID is required for locked snapshot evidence",
            operation=operation,
            sanitized_code="MISSING_S3_VERSION_ID",
        )
    return value


class S3SnapshotAdapter:
    """Store and verify canonical snapshots without granting local authority."""

    def __init__(
        self,
        config: S3SnapshotConfig,
        client: S3ClientProtocol,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(config, S3SnapshotConfig):
            raise SnapshotConfigurationError(
                "config must be an S3SnapshotConfig",
                sanitized_code="INVALID_S3_CONFIG",
            )
        for method_name in (
            "get_bucket_location",
            "get_bucket_versioning",
            "get_object_lock_configuration",
            "put_object",
            "head_object",
            "get_object",
        ):
            if not callable(getattr(client, method_name, None)):
                raise SnapshotConfigurationError(
                    "injected client does not implement the narrow S3 protocol",
                    sanitized_code="INVALID_S3_CLIENT",
                )
        if clock is not None and not callable(clock):
            raise SnapshotConfigurationError(
                "clock must be callable",
                sanitized_code="INVALID_CLOCK",
            )
        self._config = config
        self._client = client
        self._clock = clock or (lambda: datetime.now(UTC))

    def _call(
        self,
        operation: str,
        method: Callable[..., Mapping[str, Any]],
        **parameters: Any,
    ) -> Mapping[str, Any]:
        try:
            response = method(**parameters)
        except SnapshotStorageError:
            raise
        except Exception as error:
            raise _translated_error(error, operation) from None
        return _mapping_response(response, operation)

    def _bucket_parameters(self) -> dict[str, Any]:
        parameters: dict[str, Any] = {"Bucket": self._config.bucket_name}
        if self._config.expected_bucket_owner is not None:
            parameters["ExpectedBucketOwner"] = (
                self._config.expected_bucket_owner
            )
        return parameters

    def _now(self) -> datetime:
        value = self._clock()
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise SnapshotConfigurationError(
                "clock must return a timezone-aware datetime",
                sanitized_code="INVALID_CLOCK",
            )
        return value.astimezone(UTC)

    def inspect_bucket_capabilities(self) -> BucketCapabilities:
        """Prove region, versioning, Object Lock, and default retention."""

        assert_no_open_persistence_transaction()
        return self._inspect_bucket_capabilities()

    def _inspect_bucket_capabilities(self) -> BucketCapabilities:
        parameters = self._bucket_parameters()
        location = self._call(
            "GetBucketLocation",
            self._client.get_bucket_location,
            **parameters,
        )
        observed_region = location.get("LocationConstraint")
        if observed_region is None:
            observed_region = "us-east-1"
        elif observed_region == "EU":
            observed_region = "eu-west-1"
        if observed_region != self._config.region:
            raise SnapshotCapabilityError(
                "bucket region does not match adapter configuration",
                operation="GetBucketLocation",
                sanitized_code="BUCKET_REGION_MISMATCH",
            )
        versioning = self._call(
            "GetBucketVersioning",
            self._client.get_bucket_versioning,
            **parameters,
        )
        if versioning.get("Status") != "Enabled":
            raise SnapshotCapabilityError(
                "bucket versioning is not enabled",
                operation="GetBucketVersioning",
                sanitized_code="BUCKET_VERSIONING_REQUIRED",
            )
        lock_response = self._call(
            "GetObjectLockConfiguration",
            self._client.get_object_lock_configuration,
            **parameters,
        )
        lock_config = lock_response.get("ObjectLockConfiguration")
        if not isinstance(lock_config, Mapping) or (
            lock_config.get("ObjectLockEnabled") != "Enabled"
        ):
            raise SnapshotCapabilityError(
                "bucket Object Lock is not enabled",
                operation="GetObjectLockConfiguration",
                sanitized_code="BUCKET_OBJECT_LOCK_REQUIRED",
            )
        rule = lock_config.get("Rule")
        retention = (
            rule.get("DefaultRetention") if isinstance(rule, Mapping) else None
        )
        if not isinstance(retention, Mapping):
            raise SnapshotCapabilityError(
                "bucket default retention is missing",
                operation="GetObjectLockConfiguration",
                sanitized_code="BUCKET_DEFAULT_RETENTION_REQUIRED",
            )
        if retention.get("Mode") != self._config.retention_mode.value:
            raise SnapshotCapabilityError(
                "bucket default retention mode does not match configuration",
                operation="GetObjectLockConfiguration",
                sanitized_code="BUCKET_RETENTION_MODE_MISMATCH",
            )
        days = retention.get("Days")
        if (
            not isinstance(days, int)
            or isinstance(days, bool)
            or days != self._config.retention_days
            or "Years" in retention
        ):
            raise SnapshotCapabilityError(
                "bucket default retention period does not match configuration",
                operation="GetObjectLockConfiguration",
                sanitized_code="BUCKET_RETENTION_PERIOD_MISMATCH",
            )
        return BucketCapabilities(
            bucket_reference=self._config.bucket_reference,
            region=observed_region,
            versioning_status="Enabled",
            object_lock_enabled=True,
            default_retention_mode=S3ObjectLockMode.GOVERNANCE,
            default_retention_days=days,
        )

    def _validate_snapshot(
        self,
        snapshot: SnapshotEnvelope,
        *,
        require_active_retention: bool,
    ) -> None:
        if not isinstance(snapshot, SnapshotEnvelope):
            raise SnapshotConfigurationError(
                "snapshot must be a SnapshotEnvelope",
                sanitized_code="INVALID_SNAPSHOT_ENVELOPE",
            )
        if snapshot.retention_mode is not self._config.retention_mode:
            raise SnapshotConfigurationError(
                "snapshot retention mode differs from adapter configuration",
                sanitized_code="RETENTION_MODE_MISMATCH",
            )
        minimum = snapshot.captured_at + timedelta(
            days=self._config.retention_days
        )
        if snapshot.retain_until < minimum:
            raise SnapshotConfigurationError(
                "snapshot retention is shorter than the configured minimum",
                sanitized_code="RETENTION_WINDOW_TOO_SHORT",
            )
        if require_active_retention:
            observed_now = self._now()
            if snapshot.retain_until <= observed_now:
                raise SnapshotConfigurationError(
                    "snapshot retention has already expired",
                    sanitized_code="RETENTION_ALREADY_EXPIRED",
                )
            if snapshot.retain_until < (
                observed_now + timedelta(days=self._config.retention_days)
            ):
                raise SnapshotConfigurationError(
                    "snapshot retention is shorter than the configured write period",
                    sanitized_code="RETENTION_WINDOW_TOO_SHORT",
                )

    def _expected_metadata(
        self, snapshot: SnapshotEnvelope
    ) -> dict[str, str]:
        metadata = {
            "snapshot-id": snapshot.snapshot_id,
            "canonical-sha256": snapshot.content_sha256,
            "content-length": str(snapshot.content_length),
            "snapshot-schema-version": snapshot.schema_version,
            "serialization-version": snapshot.serialization_version,
            "media-type": snapshot.media_type,
            "storage-class": snapshot.storage_class.value,
            "scope-digest": snapshot.scope_digest,
            "manifest-sha256": snapshot.manifest_sha256,
            "authority-status": "STORAGE_EVIDENCE_ONLY",
        }
        if snapshot.source_artifact_digest is not None:
            metadata["source-artifact-digest"] = (
                snapshot.source_artifact_digest
            )
        return metadata

    def _put_parameters(
        self,
        snapshot: SnapshotEnvelope,
        object_key: str,
    ) -> dict[str, Any]:
        parameters: dict[str, Any] = {
            **self._bucket_parameters(),
            "Key": object_key,
            "Body": snapshot.canonical_payload,
            "ContentLength": snapshot.content_length,
            "ContentType": snapshot.media_type,
            "ChecksumSHA256": snapshot.checksum_sha256_base64,
            "IfNoneMatch": "*",
            "Metadata": self._expected_metadata(snapshot),
            "ObjectLockMode": snapshot.retention_mode.value,
            "ObjectLockRetainUntilDate": snapshot.retain_until,
            "ServerSideEncryption": self._config.server_side_encryption,
        }
        return parameters

    def _read_parameters(
        self,
        object_key: str,
        *,
        version_id: str | None,
    ) -> dict[str, Any]:
        parameters: dict[str, Any] = {
            **self._bucket_parameters(),
            "Key": object_key,
            "ChecksumMode": "ENABLED",
        }
        if version_id is not None:
            parameters["VersionId"] = version_id
        return parameters

    def _verify_response(
        self,
        response: Mapping[str, Any],
        snapshot: SnapshotEnvelope,
        object_key: str,
        *,
        operation: str,
        expected_version_id: str | None,
        content_verified: bool,
        idempotent_replay: bool,
    ) -> SnapshotStorageEvidence:
        observed_version = _version_id(response.get("VersionId"), operation)
        if (
            expected_version_id is not None
            and observed_version != expected_version_id
        ):
            raise SnapshotIntegrityError(
                "S3 version ID does not match the requested immutable version",
                operation=operation,
                sanitized_code="S3_VERSION_MISMATCH",
            )
        length = response.get("ContentLength")
        if (
            not isinstance(length, int)
            or isinstance(length, bool)
            or length != snapshot.content_length
        ):
            raise SnapshotIntegrityError(
                "S3 content length does not match the snapshot manifest",
                operation=operation,
                sanitized_code="S3_CONTENT_LENGTH_MISMATCH",
            )
        metadata = response.get("Metadata")
        expected_metadata = self._expected_metadata(snapshot)
        if not isinstance(metadata, Mapping):
            raise SnapshotIntegrityError(
                "S3 metadata does not match the snapshot manifest",
                operation=operation,
                sanitized_code="S3_METADATA_MISMATCH",
            )
        try:
            observed_metadata = dict(metadata)
        except Exception:
            raise SnapshotMalformedResponseError(
                "S3 metadata response is malformed",
                operation=operation,
                sanitized_code="MALFORMED_S3_METADATA",
            ) from None
        if observed_metadata != expected_metadata:
            raise SnapshotIntegrityError(
                "S3 metadata does not match the snapshot manifest",
                operation=operation,
                sanitized_code="S3_METADATA_MISMATCH",
            )
        if response.get("ChecksumSHA256") != snapshot.checksum_sha256_base64:
            raise SnapshotIntegrityError(
                "S3 SHA-256 checksum does not match canonical snapshot bytes",
                operation=operation,
                sanitized_code="S3_CHECKSUM_MISMATCH",
            )
        if response.get("ContentType") != snapshot.media_type:
            raise SnapshotIntegrityError(
                "S3 media type does not match the snapshot manifest",
                operation=operation,
                sanitized_code="S3_MEDIA_TYPE_MISMATCH",
            )
        if response.get("ObjectLockMode") != snapshot.retention_mode.value:
            raise SnapshotIntegrityError(
                "S3 Object Lock mode does not match retention intent",
                operation=operation,
                sanitized_code="S3_RETENTION_MODE_MISMATCH",
            )
        retain_until = response.get("ObjectLockRetainUntilDate")
        if (
            not isinstance(retain_until, datetime)
            or retain_until.tzinfo is None
            or retain_until.astimezone(UTC) != snapshot.retain_until
        ):
            raise SnapshotIntegrityError(
                "S3 retain-until timestamp does not match retention intent",
                operation=operation,
                sanitized_code="S3_RETENTION_DATE_MISMATCH",
            )
        if (
            response.get("ServerSideEncryption")
            != self._config.server_side_encryption
        ):
            raise SnapshotIntegrityError(
                "S3 encryption evidence does not match adapter configuration",
                operation=operation,
                sanitized_code="S3_ENCRYPTION_MISMATCH",
            )
        return SnapshotStorageEvidence(
            snapshot_id=snapshot.snapshot_id,
            canonical_sha256=snapshot.content_sha256,
            content_length=snapshot.content_length,
            bucket_reference=self._config.bucket_reference,
            object_key=object_key,
            version_id=observed_version,
            retention_mode=snapshot.retention_mode,
            retain_until=snapshot.retain_until,
            checksum_sha256_base64=snapshot.checksum_sha256_base64,
            metadata_verified=True,
            content_verified=content_verified,
            idempotent_replay=idempotent_replay,
        )

    def _head(
        self,
        snapshot: SnapshotEnvelope,
        object_key: str,
        *,
        version_id: str | None,
        idempotent_replay: bool,
    ) -> SnapshotStorageEvidence:
        response = self._call(
            "HeadObject",
            self._client.head_object,
            **self._read_parameters(object_key, version_id=version_id),
        )
        return self._verify_response(
            response,
            snapshot,
            object_key,
            operation="HeadObject",
            expected_version_id=version_id,
            content_verified=False,
            idempotent_replay=idempotent_replay,
        )

    def persist_snapshot(
        self, snapshot: SnapshotEnvelope
    ) -> SnapshotStorageEvidence:
        """Persist one deterministic immutable snapshot and verify its head."""

        assert_no_open_persistence_transaction()
        self._validate_snapshot(snapshot, require_active_retention=True)
        self._inspect_bucket_capabilities()
        object_key = self._config.object_key(
            snapshot.snapshot_id,
            snapshot.scope_digest,
            snapshot.object_suffix,
        )
        try:
            response = self._client.put_object(
                **self._put_parameters(snapshot, object_key)
            )
        except Exception as error:
            code = _aws_error_code(error)
            if code == "PreconditionFailed":
                return self._resolve_idempotent_replay(snapshot, object_key)
            if code == "ConditionalRequestConflict":
                raise SnapshotConflictError(
                    "concurrent write conflicted with deterministic snapshot key",
                    operation="PutObject",
                    sanitized_code="S3_CONDITIONAL_WRITE_CONFLICT",
                    aws_error_code=code,
                ) from None
            raise _translated_error(error, "PutObject") from None
        put_response = _mapping_response(response, "PutObject")
        version_id = _version_id(put_response.get("VersionId"), "PutObject")
        returned_checksum = put_response.get("ChecksumSHA256")
        if (
            returned_checksum is not None
            and returned_checksum != snapshot.checksum_sha256_base64
        ):
            raise SnapshotIntegrityError(
                "PutObject returned a different SHA-256 checksum",
                operation="PutObject",
                sanitized_code="S3_CHECKSUM_MISMATCH",
            )
        return self._head(
            snapshot,
            object_key,
            version_id=version_id,
            idempotent_replay=False,
        )

    def _resolve_idempotent_replay(
        self,
        snapshot: SnapshotEnvelope,
        object_key: str,
    ) -> SnapshotStorageEvidence:
        try:
            head = self._head(
                snapshot,
                object_key,
                version_id=None,
                idempotent_replay=True,
            )
            retrieved = self._retrieve(
                snapshot,
                object_key,
                version_id=head.version_id,
                idempotent_replay=True,
            )
        except (SnapshotIntegrityError, SnapshotMalformedResponseError) as error:
            raise SnapshotConflictError(
                "deterministic object key is already bound to different facts",
                operation="PutObject",
                sanitized_code="SNAPSHOT_IDEMPOTENCY_CONFLICT",
            ) from error
        return retrieved.evidence

    def inspect_snapshot(
        self,
        snapshot: SnapshotEnvelope,
        *,
        version_id: str,
    ) -> SnapshotStorageEvidence:
        """Inspect exact version, checksum, metadata, and retention evidence."""

        assert_no_open_persistence_transaction()
        self._validate_snapshot(snapshot, require_active_retention=False)
        exact_version = _version_id(version_id, "HeadObject")
        object_key = self._config.object_key(
            snapshot.snapshot_id,
            snapshot.scope_digest,
            snapshot.object_suffix,
        )
        return self._head(
            snapshot,
            object_key,
            version_id=exact_version,
            idempotent_replay=False,
        )

    def retrieve_snapshot(
        self,
        snapshot: SnapshotEnvelope,
        *,
        version_id: str,
    ) -> RetrievedSnapshot:
        """Retrieve one exact S3 version and verify bytes plus metadata."""

        assert_no_open_persistence_transaction()
        self._validate_snapshot(snapshot, require_active_retention=False)
        exact_version = _version_id(version_id, "GetObject")
        object_key = self._config.object_key(
            snapshot.snapshot_id,
            snapshot.scope_digest,
            snapshot.object_suffix,
        )
        return self._retrieve(
            snapshot,
            object_key,
            version_id=exact_version,
            idempotent_replay=False,
        )

    def _retrieve(
        self,
        snapshot: SnapshotEnvelope,
        object_key: str,
        *,
        version_id: str,
        idempotent_replay: bool,
    ) -> RetrievedSnapshot:
        response = self._call(
            "GetObject",
            self._client.get_object,
            **self._read_parameters(object_key, version_id=version_id),
        )
        body = response.get("Body")
        if not callable(getattr(body, "read", None)) or not callable(
            getattr(body, "close", None)
        ):
            raise SnapshotMalformedResponseError(
                "GetObject response has no closeable streaming body",
                operation="GetObject",
                sanitized_code="MALFORMED_S3_BODY",
            )
        length = response.get("ContentLength")
        if (
            not isinstance(length, int)
            or isinstance(length, bool)
            or length != snapshot.content_length
        ):
            raise SnapshotIntegrityError(
                "S3 content length does not match the snapshot manifest",
                operation="GetObject",
                sanitized_code="S3_CONTENT_LENGTH_MISMATCH",
            )
        try:
            payload = body.read(snapshot.content_length + 1)
        except Exception as error:
            try:
                body.close()
            except Exception:
                pass
            raise _translated_error(error, "GetObjectBodyRead") from None
        try:
            body.close()
        except Exception as error:
            raise _translated_error(error, "GetObjectBodyClose") from None
        if not isinstance(payload, bytes):
            raise SnapshotMalformedResponseError(
                "GetObject body did not return bytes",
                operation="GetObject",
                sanitized_code="MALFORMED_S3_BODY",
            )
        if payload != snapshot.canonical_payload:
            raise SnapshotIntegrityError(
                "retrieved bytes do not match the canonical snapshot",
                operation="GetObject",
                sanitized_code="S3_CONTENT_HASH_MISMATCH",
            )
        evidence = self._verify_response(
            response,
            snapshot,
            object_key,
            operation="GetObject",
            expected_version_id=version_id,
            content_verified=True,
            idempotent_replay=idempotent_replay,
        )
        return RetrievedSnapshot(payload=payload, evidence=evidence)

    def verify_snapshot(
        self,
        snapshot: SnapshotEnvelope,
        *,
        version_id: str,
    ) -> SnapshotStorageEvidence:
        """Return storage evidence only after an exact-version byte read."""

        return self.retrieve_snapshot(
            snapshot,
            version_id=version_id,
        ).evidence
