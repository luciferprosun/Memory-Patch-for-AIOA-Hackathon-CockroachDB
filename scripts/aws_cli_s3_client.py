"""Validation-only AWS CLI transport for the Step 7 S3 adapter.

The production adapter remains SDK-agnostic and dependency-injected. This
transport exists because the repository intentionally installs neither boto3
nor botocore. It implements only the six methods required by the Step 7
protocol plus one read-only exact-key version listing for live evidence.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any


_AWS_ERROR = re.compile(r"An error occurred \(([A-Za-z0-9._-]{1,128})\)")
_SSO_SESSION_ERROR = re.compile(
    r"(?:error when retrieving token from sso|"
    r"sso session associated with this profile has expired|"
    r"token has expired and refresh failed)",
    re.IGNORECASE,
)
_TRANSIENT_TRANSPORT_ERROR = re.compile(
    r"(?:"
    r"read timeout|connect timeout|timed out|"
    r"connection (?:was )?(?:closed|reset)|"
    r"could not connect to (?:the )?endpoint|"
    r"endpoint connection error|"
    r"temporarily unavailable|service unavailable"
    r")",
    re.IGNORECASE,
)
_REGION = re.compile(r"^[a-z]{2}(?:-[a-z0-9]+)+-\d+$")
_PROFILE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class AwsCliS3Error(RuntimeError):
    """Boto-compatible sanitized code shape consumed by the Step 7 adapter."""

    def __init__(self, code: str) -> None:
        super().__init__("AWS CLI S3 operation failed")
        self.response = {"Error": {"Code": code}}


class _Body:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload
        self._closed = False

    def read(self, amount: int | None = None) -> bytes:
        if self._closed:
            raise AwsCliS3Error("BodyClosed")
        return self._payload if amount is None else self._payload[:amount]

    def close(self) -> None:
        self._closed = True


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise AwsCliS3Error("MalformedResponse")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AwsCliS3Error("MalformedResponse") from exc
    if parsed.tzinfo is None:
        raise AwsCliS3Error("MalformedResponse")
    return parsed


def _mapping(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping) or not all(
        isinstance(key, str) for key in value
    ):
        raise AwsCliS3Error("MalformedResponse")
    return dict(value)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise AwsCliS3Error("AwsCliBinaryUnavailable") from exc
    return digest.hexdigest()


class AwsCliS3Client:
    """Narrow no-shell AWS CLI client with explicit profile and Region."""

    def __init__(
        self,
        *,
        aws_binary: Path,
        profile: str,
        region: str,
        temporary_directory: Path,
        expected_binary_sha256: str,
        timeout_seconds: float = 60.0,
    ) -> None:
        if (
            not isinstance(aws_binary, Path)
            or not aws_binary.is_file()
            or aws_binary.is_symlink()
            or not isinstance(profile, str)
            or _PROFILE.fullmatch(profile) is None
            or not isinstance(region, str)
            or _REGION.fullmatch(region) is None
            or not isinstance(temporary_directory, Path)
            or not temporary_directory.is_dir()
            or temporary_directory.is_symlink()
            or not isinstance(expected_binary_sha256, str)
            or _SHA256.fullmatch(expected_binary_sha256) is None
            or not isinstance(timeout_seconds, (int, float))
            or not 1 <= timeout_seconds <= 300
        ):
            raise ValueError("unsafe AWS CLI validation transport configuration")
        if _file_sha256(aws_binary) != expected_binary_sha256:
            raise ValueError("AWS CLI binary digest differs")
        self._aws = aws_binary
        self._expected_binary_sha256 = expected_binary_sha256
        self._profile = profile
        self._region = region
        self._temporary_directory = temporary_directory
        self._timeout = float(timeout_seconds)
        self._operation_counts: dict[str, int] = {}

    def _base(self, operation: str) -> list[str]:
        self._operation_counts[operation] = (
            self._operation_counts.get(operation, 0) + 1
        )
        return [
            str(self._aws),
            "s3api",
            operation,
            "--profile",
            self._profile,
            "--region",
            self._region,
            "--no-cli-pager",
            "--output",
            "json",
        ]

    @property
    def operation_counts(self) -> Mapping[str, int]:
        """Return a defensive copy of exact CLI request counts."""

        return dict(self._operation_counts)

    @staticmethod
    def _parameter(
        arguments: list[str],
        name: str,
        value: object,
    ) -> None:
        if not isinstance(value, str) or not value:
            raise ValueError(f"{name} must be non-empty text")
        arguments.extend([name, value])

    def _run(self, arguments: Sequence[str]) -> dict[str, Any]:
        if _file_sha256(self._aws) != self._expected_binary_sha256:
            raise AwsCliS3Error("AwsCliBinaryIdentityChanged")
        environment = os.environ.copy()
        environment["AWS_PAGER"] = ""
        try:
            completed = subprocess.run(
                list(arguments),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                timeout=self._timeout,
                env=environment,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise AwsCliS3Error("RequestTimeout") from exc
        if completed.returncode != 0:
            match = _AWS_ERROR.search(completed.stderr)
            code = (
                match.group(1)
                if match
                else (
                    "SSOTokenLoadError"
                    if _SSO_SESSION_ERROR.search(completed.stderr)
                    else (
                        "RequestTimeout"
                        if _TRANSIENT_TRANSPORT_ERROR.search(completed.stderr)
                        else "UnclassifiedAwsCliError"
                    )
                )
            )
            if code in {"404", "NoSuchObject"}:
                code = "NotFound"
            raise AwsCliS3Error(code)
        try:
            value = json.loads(completed.stdout or "{}")
        except json.JSONDecodeError as exc:
            raise AwsCliS3Error("MalformedResponse") from exc
        return _mapping(value)

    @staticmethod
    def _common(arguments: list[str], values: Mapping[str, Any]) -> None:
        AwsCliS3Client._parameter(
            arguments,
            "--bucket",
            values.get("Bucket"),
        )
        owner = values.get("ExpectedBucketOwner")
        if owner is not None:
            AwsCliS3Client._parameter(
                arguments,
                "--expected-bucket-owner",
                owner,
            )

    @staticmethod
    def _normalize_object_response(value: Mapping[str, Any]) -> dict[str, Any]:
        response = dict(value)
        retain_until = response.get("ObjectLockRetainUntilDate")
        if retain_until is not None:
            response["ObjectLockRetainUntilDate"] = _timestamp(retain_until)
        return response

    def get_bucket_location(self, **values: Any) -> Mapping[str, Any]:
        arguments = self._base("get-bucket-location")
        self._common(arguments, values)
        return self._run(arguments)

    def get_bucket_versioning(self, **values: Any) -> Mapping[str, Any]:
        arguments = self._base("get-bucket-versioning")
        self._common(arguments, values)
        return self._run(arguments)

    def get_object_lock_configuration(
        self,
        **values: Any,
    ) -> Mapping[str, Any]:
        arguments = self._base("get-object-lock-configuration")
        self._common(arguments, values)
        return self._run(arguments)

    def head_object(self, **values: Any) -> Mapping[str, Any]:
        arguments = self._base("head-object")
        self._common(arguments, values)
        self._parameter(arguments, "--key", values.get("Key"))
        if values.get("ChecksumMode") == "ENABLED":
            arguments.extend(["--checksum-mode", "ENABLED"])
        version_id = values.get("VersionId")
        if version_id is not None:
            self._parameter(arguments, "--version-id", version_id)
        return self._normalize_object_response(self._run(arguments))

    def _new_transport_path(self, prefix: str) -> Path:
        descriptor, raw_path = tempfile.mkstemp(
            prefix=prefix,
            dir=self._temporary_directory,
        )
        os.close(descriptor)
        path = Path(raw_path)
        if path.is_symlink() or path.parent != self._temporary_directory:
            path.unlink(missing_ok=True)
            raise ValueError("unsafe AWS CLI transport path")
        return path

    def put_object(self, **values: Any) -> Mapping[str, Any]:
        body = values.get("Body")
        if not isinstance(body, bytes):
            raise ValueError("S3 put body must be immutable bytes")
        path = self._new_transport_path("s3-put-")
        try:
            with path.open("wb") as destination:
                destination.write(body)
                destination.flush()
                os.fsync(destination.fileno())
            arguments = self._base("put-object")
            self._common(arguments, values)
            for option, key in (
                ("--key", "Key"),
                ("--content-type", "ContentType"),
                ("--checksum-sha256", "ChecksumSHA256"),
                ("--if-none-match", "IfNoneMatch"),
                ("--object-lock-mode", "ObjectLockMode"),
                ("--server-side-encryption", "ServerSideEncryption"),
            ):
                self._parameter(arguments, option, values.get(key))
            length = values.get("ContentLength")
            if not isinstance(length, int) or length != len(body):
                raise ValueError("S3 content length differs from body")
            arguments.extend(["--content-length", str(length)])
            retain_until = values.get("ObjectLockRetainUntilDate")
            if not isinstance(retain_until, datetime) or (
                retain_until.tzinfo is None
            ):
                raise ValueError("S3 retention date is not timezone-aware")
            arguments.extend(
                [
                    "--object-lock-retain-until-date",
                    retain_until.isoformat(),
                ]
            )
            metadata = values.get("Metadata")
            if not isinstance(metadata, Mapping):
                raise ValueError("S3 metadata must be a mapping")
            arguments.extend(
                [
                    "--metadata",
                    json.dumps(
                        dict(metadata),
                        ensure_ascii=True,
                        allow_nan=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    "--body",
                    str(path),
                ]
            )
            return self._run(arguments)
        finally:
            path.unlink(missing_ok=True)

    def get_object(self, **values: Any) -> Mapping[str, Any]:
        path = self._new_transport_path("s3-get-")
        path.unlink()
        try:
            arguments = self._base("get-object")
            self._common(arguments, values)
            self._parameter(arguments, "--key", values.get("Key"))
            if values.get("ChecksumMode") == "ENABLED":
                arguments.extend(["--checksum-mode", "ENABLED"])
            version_id = values.get("VersionId")
            if version_id is not None:
                self._parameter(arguments, "--version-id", version_id)
            arguments.append(str(path))
            response = self._normalize_object_response(self._run(arguments))
            payload = path.read_bytes()
            response["Body"] = _Body(payload)
            return response
        finally:
            path.unlink(missing_ok=True)

    def list_object_versions(
        self,
        *,
        bucket: str,
        key: str,
    ) -> tuple[dict[str, Any], ...]:
        arguments = self._base("list-object-versions")
        self._parameter(arguments, "--bucket", bucket)
        self._parameter(arguments, "--prefix", key)
        response = self._run(arguments)
        versions = response.get("Versions", [])
        if not isinstance(versions, list):
            raise AwsCliS3Error("MalformedResponse")
        return tuple(
            dict(item)
            for item in versions
            if isinstance(item, Mapping) and item.get("Key") == key
        )


__all__ = ["AwsCliS3Client", "AwsCliS3Error"]
