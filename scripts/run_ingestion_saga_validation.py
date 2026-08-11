#!/usr/bin/env python3
"""Plan or execute the explicitly gated Step 10 synthetic live validation.

``--plan`` performs repository, binary, AWS, S3, and external-volume
preflights without creating the Step 10 target. ``--write-validation`` first
repeats those checks, requires exact plan-bound confirmations, then exercises
one disposable CockroachDB runtime, one no-overwrite external-volume artifact,
and one Object-Locked S3 version. ``--recovery-plan`` and
``--recovery-validation`` instead reconcile that exact existing external
artifact and S3 version while making both external write methods fail closed.

Importing this module performs no process, filesystem, database, AWS, or
external-volume action.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence


sys.dont_write_bytecode = True
SCRIPT_ROOT = Path(__file__).resolve().parent
REPOSITORY_ROOT = SCRIPT_ROOT.parent
SOURCE_ROOT = REPOSITORY_ROOT / "src"
for import_root in (SCRIPT_ROOT, SOURCE_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

import run_cockroachdb_migrations as migrations  # noqa: E402
from aws_cli_s3_client import AwsCliS3Client  # noqa: E402
from cockroach_cli_dbapi import OwnedChildRegistry, connection_factory  # noqa: E402
from aioa_memory_kernel.contracts import MemoryTargetScope  # noqa: E402
from aioa_memory_kernel.contracts.serialization import (  # noqa: E402
    canonical_json_bytes,
    canonical_sha256,
)
from aioa_memory_kernel.ingestion import (  # noqa: E402
    IngestionOrchestrator,
    IngestionSagaService,
    ParseReceipt,
    SagaMilestone,
    Step9PublicationPort,
    ValidationReceipt,
    build_initial_saga,
)
from aioa_memory_kernel.persistence import (  # noqa: E402
    AccessMode,
    IdempotencyConflictError,
    RequestContext,
    SerializableTransactionRunner,
)
from aioa_memory_kernel.runtime import LinuxExternalVolumeProbe  # noqa: E402
from aioa_memory_kernel.security.credentials import (  # noqa: E402
    AWS_WORKLOAD_IDENTITY_ENVIRONMENT_NAMES,
    build_minimal_subprocess_environment,
)
from aioa_memory_kernel.sources import (  # noqa: E402
    PUBLICATION_GENESIS_DIGEST,
    OriginMetadata,
    ParserIdentity,
    ProvenanceArtifactIdentity,
    RedactionState,
    SourceAccessClass,
    SourceAuthorityAssessment,
    SourceAuthorityLevel,
    SourceLicenseAssessment,
    SourceLicenseStatus,
    SourcePublicationState,
    SourceRegistryRecord,
    SourceRegistryService,
    SourceScopeDimensions,
    TransformationIdentity,
)
from aioa_memory_kernel.storage import (  # noqa: E402
    EXACT_BYTES_SERIALIZATION_VERSION,
    EXTERNAL_VOLUME_PROJECT_ID,
    ExternalVolumeConfig,
    ExternalVolumeOperation,
    ExternalVolumeRuntimeAdapter,
    S3ObjectLockMode,
    S3SnapshotAdapter,
    S3SnapshotConfig,
    SnapshotEnvelope,
    load_external_volume_environment,
)


EXPECTED_REMOTE = (
    "https://github.com/luciferprosun/"
    "Memory-Patch-for-AIOA-Hackathon-CockroachDB"
)
EXPECTED_BRANCH = "main"
EXPECTED_HEAD = "e93536626c105f5186ce7e2c89a419f5bf6c4b83"
AWS_PROFILE = "aoia-admin"
AWS_REGION = "eu-central-1"
AWS_PERMISSION_CONTEXT = "LuciferSOL"
S3_BUCKET = "aioa-memory-patch-global-3f105fcd-eu-central-1"
S3_PREFIX = "memory-patch/snapshots/v1"
S3_RETENTION_DAYS = 7
PINNED_BINARY_SHA256 = (
    "a5392f4de2c7a2bd838a52b0dcde0d61dcecf2fb060a88b0771367309b5cbdcf"
)
FIXTURE_PATH = (
    REPOSITORY_ROOT / "tests" / "fixtures" / "step10_ingestion_saga_payload.json"
)
FIXTURE_SHA256 = (
    "61088c464f21622d0dccd28d41e6f041c9bf7abf165542262c9ea7f8d51241ca"
)
FIXTURE_LENGTH = 92
DEFAULT_EXTERNAL_CONFIG = REPOSITORY_ROOT / ".local" / "external-data.env"
DEFAULT_EVIDENCE_OUTPUT = (
    REPOSITORY_ROOT
    / "docs"
    / "evidence"
    / "ingestion"
    / "step10-ingestion-saga-validation.json"
)
DEFAULT_FAILURE_EVIDENCE_OUTPUT = (
    REPOSITORY_ROOT
    / "docs"
    / "evidence"
    / "ingestion"
    / "step10-ingestion-saga-validation-failure.json"
)
DEFAULT_RECOVERY_FAILURE_EVIDENCE_OUTPUT = (
    REPOSITORY_ROOT
    / "docs"
    / "evidence"
    / "ingestion"
    / "step10-ingestion-saga-recovery-failure.json"
)
PRESERVED_FAILURE_EVIDENCE_DIGEST = (
    "78a7cfd94c41a40726f9c4bde5f6f6c441615acba384acebab825b5651ff5de4"
)
PRESERVED_S3_VERSION_ID = "kfDFfBsGlAR_KoQxDodzESlhebuYpAMx"
AUDITED_SHUTDOWN_SETTINGS = {
    "server.shutdown.connections.timeout": "0s",
    "server.shutdown.initial_wait": "0s",
    "server.shutdown.jobs.timeout": "10s",
    "server.shutdown.lease_transfer_iteration.timeout": "5s",
    "server.shutdown.transactions.timeout": "10s",
}
_ASSUMED_ROLE_ARN = re.compile(
    r"^arn:[a-z0-9-]+:sts::[0-9]{12}:assumed-role/"
    r"(?P<role>[^/]{1,256})/(?P<session>[^/]{1,256})$"
)
_SAFE_SQL_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]{2,62}$")


class Step10ValidationError(RuntimeError):
    """A sanitized validation or gate invariant failed."""

    def __init__(
        self,
        message: str,
        *,
        sanitized_code: str = "STEP10_VALIDATION_FAILED",
    ) -> None:
        super().__init__(message)
        self.sanitized_code = sanitized_code


@dataclass(frozen=True, slots=True)
class ValidationBundle:
    ids: Mapping[str, str]
    snapshot: SnapshotEnvelope
    source_record: SourceRegistryRecord
    saga: object
    storage_config: S3SnapshotConfig
    storage_plan: object


class ValidationClock:
    """Deterministic monotonic timestamps inside one disposable runtime."""

    def __init__(self, start: datetime) -> None:
        self._current = start
        self._microsecond = 0

    def __call__(self) -> datetime:
        value = self._current.replace(microsecond=self._microsecond)
        self._microsecond += 1
        if self._microsecond >= 1_000_000:
            raise Step10ValidationError("validation clock exhausted")
        return value


class SyntheticAcquisition:
    """One fixed in-memory payload; this is not a network downloader."""

    def __init__(self, payload: bytes) -> None:
        self._payload = payload
        self.calls = 0

    def acquire(self, saga: object) -> bytes:
        self.calls += 1
        return self._payload


class ReconciliationOnlyAcquisition:
    """Fail closed if recovery attempts to reacquire already stored bytes."""

    calls = 0

    def acquire(self, saga: object) -> bytes:
        del saga
        raise Step10ValidationError(
            "recovery attempted a new acquisition",
            sanitized_code="RECOVERY_ACQUISITION_FORBIDDEN",
        )


class SyntheticParserBoundary:
    """Validation-only typed receipt producer, never a production parser."""

    def __init__(self, clock: ValidationClock) -> None:
        self._clock = clock
        self._receipt: ParseReceipt | None = None
        self.calls = 0

    def reconcile(
        self,
        saga: object,
        *,
        s3_version_id: str,
        locked_storage_evidence_digest: str,
    ) -> ParseReceipt | None:
        del locked_storage_evidence_digest
        if self._receipt is not None and (
            self._receipt.saga_id == saga.saga_id
            and self._receipt.s3_version_id == s3_version_id
        ):
            return self._receipt
        return None

    def parse(
        self,
        saga: object,
        *,
        s3_version_id: str,
        locked_storage_evidence_digest: str,
    ) -> ParseReceipt:
        self.calls += 1
        self._receipt = ParseReceipt(
            tenant_id=saga.tenant_id,
            saga_id=saga.saga_id,
            source_id=saga.source_id,
            snapshot_id=saga.snapshot_id,
            s3_version_id=s3_version_id,
            input_sha256=saga.content_sha256,
            parser_name="step10-synthetic-validation-receipt-port",
            parser_version="1.0.0",
            parser_contract_version="1.0.0",
            output_artifact_digest=canonical_sha256(
                {
                    "boundary": "STEP10_SYNTHETIC_PARSE_RECEIPT",
                    "input_sha256": saga.content_sha256,
                    "locked_storage_evidence_digest": (
                        locked_storage_evidence_digest
                    ),
                    "s3_version_id": s3_version_id,
                    "snapshot_id": saga.snapshot_id,
                }
            ),
            completed_at=self._clock(),
            synthetic_validation_boundary=True,
        )
        return self._receipt


class SyntheticValidatorBoundary:
    """Validation-only typed receipt producer, not a content validator."""

    def __init__(self, clock: ValidationClock) -> None:
        self._clock = clock
        self._receipt: ValidationReceipt | None = None
        self.calls = 0

    def reconcile(
        self,
        saga: object,
        parse_receipt: ParseReceipt,
    ) -> ValidationReceipt | None:
        if self._receipt is not None and (
            self._receipt.saga_id == saga.saga_id
            and self._receipt.parse_output_digest
            == parse_receipt.output_artifact_digest
        ):
            return self._receipt
        return None

    def validate(
        self,
        saga: object,
        parse_receipt: ParseReceipt,
    ) -> ValidationReceipt:
        self.calls += 1
        self._receipt = ValidationReceipt(
            tenant_id=saga.tenant_id,
            saga_id=saga.saga_id,
            source_id=saga.source_id,
            snapshot_id=saga.snapshot_id,
            parse_output_digest=parse_receipt.output_artifact_digest,
            validator_name="step10-synthetic-validation-receipt-port",
            validator_version="1.0.0",
            validator_contract_version="1.0.0",
            accepted=True,
            reason_codes=(),
            output_artifact_digest=canonical_sha256(
                {
                    "boundary": "STEP10_SYNTHETIC_VALIDATION_RECEIPT",
                    "parse_output_digest": parse_receipt.output_artifact_digest,
                    "snapshot_id": saga.snapshot_id,
                }
            ),
            completed_at=self._clock(),
            synthetic_validation_boundary=True,
        )
        return self._receipt


class CountingExternalVolume:
    """Expose the Step 8 protocol while counting only write calls."""

    def __init__(
        self,
        adapter: ExternalVolumeRuntimeAdapter,
        *,
        writes_allowed: bool = True,
    ) -> None:
        self._adapter = adapter
        self._writes_allowed = writes_allowed
        self.write_attempts = 0
        self.write_calls = 0

    @property
    def system_drive_fallback_allowed(self) -> bool:
        return self._adapter.system_drive_fallback_allowed

    def verify(self, *, require_write: bool = False) -> object:
        return self._adapter.verify(require_write=require_write)

    def read_exact(self, *arguments: object, **keywords: object) -> bytes:
        return self._adapter.read_exact(*arguments, **keywords)

    def incomplete_atomic_artifacts(
        self,
        *arguments: object,
        **keywords: object,
    ) -> tuple[str, ...]:
        return self._adapter.incomplete_atomic_artifacts(
            *arguments,
            **keywords,
        )

    def atomic_write_exact(
        self,
        *arguments: object,
        **keywords: object,
    ) -> object:
        self.write_attempts += 1
        if not self._writes_allowed:
            raise Step10ValidationError(
                "recovery attempted an external-volume write",
                sanitized_code="RECOVERY_EXTERNAL_WRITE_FORBIDDEN",
            )
        self.write_calls += 1
        return self._adapter.atomic_write_exact(*arguments, **keywords)


class RecoveryReadOnlyS3Client:
    """Delegate all S3 reads while making PutObject uncallable."""

    def __init__(self, client: AwsCliS3Client) -> None:
        self._client = client
        self.put_object_calls = 0

    @property
    def operation_counts(self) -> Mapping[str, int]:
        return self._client.operation_counts

    def put_object(self, **values: object) -> Mapping[str, object]:
        del values
        self.put_object_calls += 1
        raise Step10ValidationError(
            "recovery attempted an S3 write",
            sanitized_code="RECOVERY_S3_WRITE_FORBIDDEN",
        )

    def list_object_versions(
        self,
        *,
        bucket: str,
        key: str,
    ) -> tuple[dict[str, Any], ...]:
        return self._client.list_object_versions(bucket=bucket, key=key)

    def __getattr__(self, name: str) -> object:
        return getattr(self._client, name)


def _canonical_json(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _fixture() -> bytes:
    try:
        payload = FIXTURE_PATH.read_bytes()
    except OSError as exc:
        raise Step10ValidationError("synthetic fixture is unavailable") from exc
    if (
        len(payload) != FIXTURE_LENGTH
        or hashlib.sha256(payload).hexdigest() != FIXTURE_SHA256
    ):
        raise Step10ValidationError("synthetic fixture identity differs")
    return payload


def _parse_timestamp(value: str, field_name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise Step10ValidationError(f"{field_name} is not ISO-8601") from exc
    if parsed.tzinfo is None or parsed.microsecond:
        raise Step10ValidationError(
            f"{field_name} must be timezone-aware whole seconds"
        )
    return parsed.astimezone(UTC)


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _git(arguments: Sequence[str], *, timeout: float = 60.0) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(REPOSITORY_ROOT), *arguments],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            env={
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "LANG": "C",
                "LC_ALL": "C",
            },
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Step10ValidationError("repository guard failed") from exc
    if completed.returncode != 0:
        raise Step10ValidationError("repository guard failed")
    return completed.stdout.strip()


def _git_raw(arguments: Sequence[str], *, timeout: float = 60.0) -> bytes:
    try:
        completed = subprocess.run(
            ["git", "-C", str(REPOSITORY_ROOT), *arguments],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "LANG": "C",
                "LC_ALL": "C",
            },
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Step10ValidationError("repository fingerprint failed") from exc
    if completed.returncode != 0:
        raise Step10ValidationError("repository fingerprint failed")
    return completed.stdout


def _worktree_fingerprint() -> Mapping[str, object]:
    """Bind the approved plan to every changed or untracked repository file."""

    states: dict[bytes, str] = {}
    for arguments, state in (
        (("diff", "--name-only", "-z", "HEAD", "--"), "TRACKED_CHANGE"),
        (
            ("ls-files", "--others", "--exclude-standard", "-z"),
            "UNTRACKED",
        ),
    ):
        raw = _git_raw(arguments)
        if raw and not raw.endswith(b"\0"):
            raise Step10ValidationError("repository file list is malformed")
        for path_bytes in raw.split(b"\0"):
            if not path_bytes:
                continue
            if path_bytes in states and states[path_bytes] != state:
                raise Step10ValidationError("repository file state is ambiguous")
            states[path_bytes] = state
    entries: list[dict[str, object]] = []
    total_bytes = 0
    for path_bytes in sorted(states):
        relative = os.fsdecode(path_bytes)
        relative_path = Path(relative)
        if (
            relative_path.is_absolute()
            or ".." in relative_path.parts
            or not relative_path.parts
        ):
            raise Step10ValidationError("repository change path is unsafe")
        path = REPOSITORY_ROOT / relative_path
        entry: dict[str, object] = {
            "path": relative,
            "state": states[path_bytes],
        }
        if not path.exists() and not path.is_symlink():
            if states[path_bytes] != "TRACKED_CHANGE":
                raise Step10ValidationError("untracked repository file vanished")
            entry["content"] = "DELETED"
        else:
            if path.is_symlink() or not path.is_file():
                raise Step10ValidationError(
                    "changed repository input must be a regular file"
                )
            size = path.stat().st_size
            total_bytes += size
            if size > 16 * 1024 * 1024 or total_bytes > 64 * 1024 * 1024:
                raise Step10ValidationError(
                    "changed repository inputs exceed the bounded plan size"
                )
            digest = hashlib.sha256()
            with path.open("rb") as source:
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(block)
            entry.update(
                {
                    "content_sha256": digest.hexdigest(),
                    "executable": bool(path.stat().st_mode & 0o111),
                    "size": size,
                }
            )
        entries.append(entry)
    return {
        "worktree_change_count": len(entries),
        "worktree_digest": canonical_sha256(entries),
    }


def _repair_fingerprint() -> Mapping[str, object]:
    """Bind recovery approval to the focused shutdown/evidence repair."""

    relative_paths = (
        "scripts/cockroach_cli_dbapi.py",
        "scripts/run_cockroachdb_migrations.py",
        "scripts/run_ingestion_saga_validation.py",
        "tests/test_cockroach_cli_dbapi.py",
        "tests/test_ingestion_saga_validation.py",
        "docs/operations/STEP_10_INGESTION_SAGA_LIVE_VALIDATION_1A.md",
    )
    entries: list[dict[str, object]] = []
    for relative in relative_paths:
        path = REPOSITORY_ROOT / relative
        if not path.is_file() or path.is_symlink():
            raise Step10ValidationError("repair input is unavailable")
        entries.append(
            {
                "path": relative,
                "sha256": _file_sha256(path),
                "size": path.stat().st_size,
            }
        )
    return {
        "repair_file_count": len(entries),
        "repair_diff_digest": canonical_sha256(entries),
    }


def _repository_guard() -> Mapping[str, object]:
    if Path(_git(("rev-parse", "--show-toplevel"))) != REPOSITORY_ROOT:
        raise Step10ValidationError("resolved repository differs")
    remote = _git(("remote", "get-url", "origin")).removesuffix(".git").rstrip("/")
    if remote != EXPECTED_REMOTE:
        raise Step10ValidationError("origin remote differs")
    if _git(("branch", "--show-current")) != EXPECTED_BRANCH:
        raise Step10ValidationError("branch differs")
    _git(("fetch", "origin", "--prune"), timeout=120)
    head = _git(("rev-parse", "HEAD"))
    origin = _git(("rev-parse", "origin/main"))
    ahead_behind = _git(
        ("rev-list", "--left-right", "--count", "origin/main...HEAD")
    )
    if (
        head != EXPECTED_HEAD
        or origin != EXPECTED_HEAD
        or ahead_behind != "0\t0"
    ):
        raise Step10ValidationError("repository baseline differs")
    git_directory = Path(_git(("rev-parse", "--git-dir")))
    if not git_directory.is_absolute():
        git_directory = REPOSITORY_ROOT / git_directory
    markers = (
        "MERGE_HEAD",
        "REBASE_HEAD",
        "CHERRY_PICK_HEAD",
        "REVERT_HEAD",
        "BISECT_LOG",
        "rebase-apply",
        "rebase-merge",
    )
    if any((git_directory / marker).exists() for marker in markers):
        raise Step10ValidationError("interrupted Git operation exists")
    if _git(("ls-files", "-u")):
        raise Step10ValidationError("unmerged repository entries exist")
    return {
        "ahead_behind": [0, 0],
        "branch": EXPECTED_BRANCH,
        "head": head,
        "origin_main": origin,
        "remote": EXPECTED_REMOTE + ".git",
        **_worktree_fingerprint(),
    }


def _aws_json(
    aws_binary: Path,
    service: str,
    operation: str,
    parameters: Sequence[str] = (),
) -> Mapping[str, Any]:
    environment = build_minimal_subprocess_environment(
        os.environ,
        allowed_names=AWS_WORKLOAD_IDENTITY_ENVIRONMENT_NAMES,
    )
    environment["AWS_PAGER"] = ""
    command = [
        str(aws_binary),
        service,
        operation,
        *parameters,
        "--profile",
        AWS_PROFILE,
        "--region",
        AWS_REGION,
        "--no-cli-pager",
        "--output",
        "json",
    ]
    try:
        completed = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            timeout=60,
            env=environment,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Step10ValidationError("AWS read-only preflight failed") from exc
    if completed.returncode != 0:
        raise Step10ValidationError("AWS read-only preflight failed")
    try:
        value = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise Step10ValidationError("AWS preflight returned malformed JSON") from exc
    if not isinstance(value, Mapping):
        raise Step10ValidationError("AWS preflight returned malformed JSON")
    return value


def _verify_aws_binary(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if (
        not resolved.is_file()
        or resolved.is_symlink()
        or not os.access(resolved, os.X_OK)
    ):
        raise Step10ValidationError("AWS CLI path is not an executable file")
    return resolved


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise Step10ValidationError("tool binary is unavailable") from exc
    return digest.hexdigest()


def _aws_identity(aws_binary: Path) -> Mapping[str, object]:
    identity = _aws_json(
        aws_binary,
        "sts",
        "get-caller-identity",
    )
    arn = identity.get("Arn")
    if not isinstance(arn, str):
        raise Step10ValidationError("AWS identity has no assumed-role ARN")
    match = _ASSUMED_ROLE_ARN.fullmatch(arn)
    if match is None or AWS_PERMISSION_CONTEXT not in match.group("role"):
        raise Step10ValidationError("AWS identity is not the approved SSO role")
    return {
        "caller_type": "ASSUMED_ROLE",
        "permission_context": AWS_PERMISSION_CONTEXT,
        "profile": AWS_PROFILE,
        "region": AWS_REGION,
        "root_principal": False,
        "sensitive_identifiers_redacted": True,
        "temporary_sso_role": True,
    }


def _external_adapter(config_path: Path) -> ExternalVolumeRuntimeAdapter:
    values = load_external_volume_environment(config_path)
    config = ExternalVolumeConfig.from_mapping(values)
    return ExternalVolumeRuntimeAdapter(
        config,
        probe=LinuxExternalVolumeProbe(),
    )


def _identifier_bundle(namespace: str) -> dict[str, str]:
    suffix = namespace[:16]
    return {
        "database": f"mp_step10_{suffix}_live",
        "hat": f"mp_step10_hat_{suffix}",
        "hat_scope": f"mp_step10_scope_{suffix}",
        "knowledge_version": f"mp_step10_version_{suffix}",
        "login_role": f"mp_step10_{suffix}_app",
        "namespace": namespace,
        "source": f"mp_step10_source_{suffix}",
        "tenant": f"mp_step10_tenant_{suffix}",
        "user": f"mp_step10_user_{suffix}",
    }


def build_validation_bundle(
    captured_at: datetime,
    retain_until: datetime,
) -> ValidationBundle:
    """Build exact deterministic identities without contacting any backend."""

    payload = _fixture()
    namespace = canonical_sha256(
        {
            "contract": "STEP10_SYNTHETIC_LIVE_VALIDATION_1A",
            "captured_at": captured_at,
            "payload_sha256": FIXTURE_SHA256,
            "retain_until": retain_until,
        }
    )
    ids = _identifier_bundle(namespace)
    snapshot = SnapshotEnvelope(
        tenant_id=ids["tenant"],
        source_id=ids["source"],
        hat_scope_id=ids["hat_scope"],
        payload=payload,
        serialization_version=EXACT_BYTES_SERIALIZATION_VERSION,
        media_type="application/json",
        captured_at=captured_at,
        retain_until=retain_until,
        retention_mode=S3ObjectLockMode.GOVERNANCE,
        authority_metadata={
            "authority": "synthetic-validation-only",
            "semantic_authority": False,
        },
        provenance_metadata={
            "fixture": "step10_ingestion_saga_payload.json",
            "personal_data": False,
            "production_parser": False,
        },
        source_artifact_digest=FIXTURE_SHA256,
    )
    parser = ParserIdentity(
        "step10-synthetic-receipt-boundary",
        "1.0.0",
        "1.0.0",
    )
    transformation = TransformationIdentity(
        "exact-byte-registration",
        "1.0.0",
        "1.0.0",
    )
    origin = OriginMetadata(
        "SYNTHETIC_FIXTURE",
        "memory-patch-step10-validation",
        "1.0.0",
        "1.0.0",
        f"synthetic://memory-patch/step10/{namespace}",
        captured_at,
    )
    scope = SourceScopeDimensions(
        tenant_id=ids["tenant"],
        hat_scope_id=ids["hat_scope"],
        target_scope=MemoryTargetScope.SHARED_KNOWLEDGE_HAT,
        domain="synthetic-validation",
        jurisdiction="none",
        language="en",
        temporal_policy_reference="step10-validation-retention-1a",
        source_collection=("step10-synthetic-validation",),
        additional_dimensions={
            "namespace_digest": namespace,
            "production_data": False,
        },
    )
    source_record = SourceRegistryRecord(
        tenant_id=ids["tenant"],
        source_id=ids["source"],
        hat_scope_id=ids["hat_scope"],
        source_kind="SYNTHETIC_VALIDATION",
        source_reference=f"synthetic://memory-patch/step10/{namespace}",
        scope=scope,
        authority=SourceAuthorityAssessment(
            SourceAuthorityLevel.OFFICIAL_PRIMARY,
            {"assessment": "synthetic-validation-fixture"},
        ),
        license=SourceLicenseAssessment(
            SourceLicenseStatus.PUBLIC_DOMAIN,
            "synthetic-validation-only",
            "synthetic://memory-patch/license/validation-only",
        ),
        access_class=SourceAccessClass.TENANT_RESTRICTED,
        redaction_state=RedactionState.NOT_REQUIRED,
        parser=parser,
        transformation=transformation,
        origin=origin,
        artifact=ProvenanceArtifactIdentity(
            "EXACT_SOURCE_BYTES",
            FIXTURE_SHA256,
            FIXTURE_LENGTH,
            "application/json",
            origin,
            parser,
            transformation,
            captured_at,
            exact_source_bytes=True,
            model_generated=False,
        ),
        snapshot_id=snapshot.snapshot_id,
        knowledge_version_id=ids["knowledge_version"],
        current_publication_state=SourcePublicationState.REGISTERED,
        current_publication_sequence=0,
        current_publication_event_digest=PUBLICATION_GENESIS_DIGEST,
        created_at=captured_at,
        updated_at=captured_at,
    )
    local_relative_path = (
        f"step10-validation-{snapshot.snapshot_id}.json"
    )
    saga = build_initial_saga(
        tenant_id=ids["tenant"],
        source_id=ids["source"],
        hat_scope_id=ids["hat_scope"],
        owner_user_id=None,
        knowledge_version_id=ids["knowledge_version"],
        idempotency_key=f"step10-live-1a:{namespace}",
        scope_digest=scope.scope_digest,
        source_registry_digest=source_record.registry_digest,
        content_sha256=snapshot.content_sha256,
        content_length=snapshot.content_length,
        media_type=snapshot.media_type,
        local_relative_path=local_relative_path,
        snapshot_id=snapshot.snapshot_id,
        captured_at=snapshot.captured_at,
        retain_until=snapshot.retain_until,
        created_at=captured_at,
    )
    storage_config = S3SnapshotConfig(
        region=AWS_REGION,
        bucket_name=S3_BUCKET,
        retention_mode=S3ObjectLockMode.GOVERNANCE,
        retention_days=S3_RETENTION_DAYS,
        key_prefix=S3_PREFIX,
    )

    class _NoCallClient:
        def __getattr__(self, name: str) -> object:
            if name in {
                "get_bucket_location",
                "get_bucket_versioning",
                "get_object_lock_configuration",
                "put_object",
                "head_object",
                "get_object",
            }:
                return lambda **values: (_ for _ in ()).throw(
                    AssertionError("pure plan contacted AWS")
                )
            raise AttributeError(name)

    storage_plan = S3SnapshotAdapter(
        storage_config,
        _NoCallClient(),
    ).plan_snapshot(snapshot)
    return ValidationBundle(
        ids=ids,
        snapshot=snapshot,
        source_record=source_record,
        saga=saga,
        storage_config=storage_config,
        storage_plan=storage_plan,
    )


def _external_preflight(
    bundle: ValidationBundle,
    config_path: Path,
) -> tuple[ExternalVolumeRuntimeAdapter, Mapping[str, object]]:
    adapter = _external_adapter(config_path)
    status = adapter.verify(require_write=True)
    incomplete = adapter.incomplete_atomic_artifacts(
        ExternalVolumeOperation.INGESTION_STAGING,
        bundle.saga.local_relative_path,
    )
    if incomplete:
        raise Step10ValidationError(
            "target-bound incomplete external staging artifact exists"
        )
    target = adapter.resolve_path(
        ExternalVolumeOperation.INGESTION_STAGING,
        bundle.saga.local_relative_path,
        require_write=True,
    )
    if os.path.lexists(target):
        raise Step10ValidationError(
            "exact Step 10 external validation target already exists"
        )
    return adapter, {
        "device_reference": status.device_reference,
        "filesystem_type": status.filesystem_type,
        "operation": ExternalVolumeOperation.INGESTION_STAGING.value,
        "payload_length": bundle.snapshot.content_length,
        "payload_sha256": bundle.snapshot.content_sha256,
        "relative_path": (
            f"ingestion/downloads/{bundle.saga.local_relative_path}"
        ),
        "root_filesystem_fallback": False,
        "target_state": "ABSENT",
        "transport": status.device_transport,
        "writable": status.writable_verified,
    }


def _s3_preflight(
    bundle: ValidationBundle,
    aws_binary: Path,
    aws_binary_sha256: str,
    temporary_directory: Path,
) -> tuple[AwsCliS3Client, S3SnapshotAdapter, Mapping[str, object]]:
    client = AwsCliS3Client(
        aws_binary=aws_binary,
        profile=AWS_PROFILE,
        region=AWS_REGION,
        temporary_directory=temporary_directory,
        expected_binary_sha256=aws_binary_sha256,
    )
    storage = S3SnapshotAdapter(bundle.storage_config, client)
    capabilities = storage.inspect_bucket_capabilities()
    versions = client.list_object_versions(
        bucket=S3_BUCKET,
        key=bundle.storage_plan.object_key,
    )
    if versions:
        raise Step10ValidationError(
            "exact Step 10 S3 validation target already has a version"
        )
    return client, storage, {
        "bucket": S3_BUCKET,
        "bucket_reference": capabilities.bucket_reference,
        "default_retention_days": capabilities.default_retention_days,
        "object_key": bundle.storage_plan.object_key,
        "object_lock": capabilities.object_lock_enabled,
        "region": capabilities.region,
        "target_version_count": 0,
        "versioning": capabilities.versioning_status,
    }


def _load_preserved_failure_evidence(path: Path) -> Mapping[str, object]:
    resolved = _resolve_evidence_output(path)
    try:
        value = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Step10ValidationError(
            "preserved failure evidence is malformed"
        ) from exc
    if not isinstance(value, Mapping):
        raise Step10ValidationError("preserved failure evidence is malformed")
    evidence = dict(value)
    observed_digest = evidence.pop("evidence_digest", None)
    if (
        observed_digest != PRESERVED_FAILURE_EVIDENCE_DIGEST
        or canonical_sha256(evidence) != observed_digest
        or value.get("status") != "FAILED_VALIDATION_NOT_COMMITTED"
        or value.get("first_root_cause", {}).get("code")
        != "DISPOSABLE_RUNTIME_FORCE_KILL_USED"
    ):
        raise Step10ValidationError("preserved failure evidence differs")
    return value


def _external_recovery_preflight(
    bundle: ValidationBundle,
    config_path: Path,
) -> tuple[ExternalVolumeRuntimeAdapter, Mapping[str, object]]:
    adapter = _external_adapter(config_path)
    status = adapter.verify(require_write=False)
    incomplete = adapter.incomplete_atomic_artifacts(
        ExternalVolumeOperation.INGESTION_STAGING,
        bundle.saga.local_relative_path,
    )
    if incomplete:
        raise Step10ValidationError(
            "target-bound incomplete external staging artifact exists"
        )
    target = adapter.resolve_path(
        ExternalVolumeOperation.INGESTION_STAGING,
        bundle.saga.local_relative_path,
        require_write=False,
    )
    try:
        metadata = os.lstat(target)
    except OSError as exc:
        raise Step10ValidationError(
            "existing external recovery target is unavailable"
        ) from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise Step10ValidationError(
            "existing external recovery target is not a regular file"
        )
    payload = adapter.read_exact(
        ExternalVolumeOperation.INGESTION_STAGING,
        bundle.saga.local_relative_path,
        expected_sha256=bundle.snapshot.content_sha256,
        expected_length=bundle.snapshot.content_length,
    )
    if payload != bundle.snapshot.canonical_payload:
        raise Step10ValidationError("external recovery bytes differ")
    return adapter, {
        "device_reference": status.device_reference,
        "filesystem_type": status.filesystem_type,
        "operation": ExternalVolumeOperation.INGESTION_STAGING.value,
        "payload_length": len(payload),
        "payload_sha256": hashlib.sha256(payload).hexdigest(),
        "relative_path": (
            f"ingestion/downloads/{bundle.saga.local_relative_path}"
        ),
        "root_filesystem_fallback": False,
        "target_state": "EXACT_EXISTING_ARTIFACT",
        "transport": status.device_transport,
        "write_capability_required": False,
        "write_preflight_performed": False,
        "write_count_planned": 0,
    }


def _s3_recovery_preflight(
    bundle: ValidationBundle,
    aws_binary: Path,
    aws_binary_sha256: str,
    temporary_directory: Path,
    expected_version_id: str,
) -> tuple[AwsCliS3Client, S3SnapshotAdapter, Mapping[str, object]]:
    client = AwsCliS3Client(
        aws_binary=aws_binary,
        profile=AWS_PROFILE,
        region=AWS_REGION,
        temporary_directory=temporary_directory,
        expected_binary_sha256=aws_binary_sha256,
    )
    storage = S3SnapshotAdapter(bundle.storage_config, client)
    capabilities = storage.inspect_bucket_capabilities()
    versions = client.list_object_versions(
        bucket=S3_BUCKET,
        key=bundle.storage_plan.object_key,
    )
    if (
        len(versions) != 1
        or versions[0].get("VersionId") != expected_version_id
    ):
        raise Step10ValidationError("existing S3 recovery version differs")
    evidence = storage.verify_snapshot(
        bundle.snapshot,
        version_id=expected_version_id,
    )
    if not (evidence.content_verified and evidence.metadata_verified):
        raise Step10ValidationError("existing S3 recovery evidence is incomplete")
    if client.operation_counts.get("put-object", 0) != 0:
        raise Step10ValidationError("S3 recovery preflight attempted a write")
    return client, storage, {
        "bucket": S3_BUCKET,
        "bucket_reference": capabilities.bucket_reference,
        "default_retention_days": capabilities.default_retention_days,
        "object_key": bundle.storage_plan.object_key,
        "object_lock": capabilities.object_lock_enabled,
        "region": capabilities.region,
        "retain_until": _timestamp(evidence.retain_until),
        "target_version_count": 1,
        "version_id": evidence.version_id,
        "versioning": capabilities.versioning_status,
        "write_count_planned": 0,
    }


def _build_plan(
    *,
    repository: Mapping[str, object],
    binary: Path,
    binary_identity: Mapping[str, str],
    aws_identity: Mapping[str, object],
    external: Mapping[str, object],
    s3: Mapping[str, object],
    bundle: ValidationBundle,
    captured_at: datetime,
    retain_until: datetime,
    evidence_output: Path,
    aws_binary: Path,
    aws_binary_sha256: str,
) -> dict[str, object]:
    facts: dict[str, object] = {
        "schema_version": "1.0.0",
        "step": "Step 10 - Idempotent S3-CockroachDB Ingestion Saga 1A",
        "mode": "SYNTHETIC_MULTI_SYSTEM_VALIDATION",
        "repository": dict(repository),
        "aws": {
            **aws_identity,
            "bucket": S3_BUCKET,
            "cli_binary_sha256": aws_binary_sha256,
            "object_key": bundle.storage_plan.object_key,
            "payload_length": bundle.snapshot.content_length,
            "payload_sha256": bundle.snapshot.content_sha256,
            "retention_mode": S3ObjectLockMode.GOVERNANCE.value,
            "retain_until": _timestamp(retain_until),
            "target_version_count": s3["target_version_count"],
        },
        "external_volume": dict(external),
        "cockroachdb": {
            "binary_path": str(binary),
            "binary_sha256": binary_identity["binary_sha256"],
            "database": bundle.ids["database"],
            "exact_server_version": binary_identity["build_tag"],
            "external_io": "DISABLED",
            "login_role": bundle.ids["login_role"],
            "loopback_only": True,
            "persistent_data": False,
            "runtime_mode": "DISPOSABLE_LOCAL_SINGLE_NODE",
            "store": "type=mem,size=640MiB",
        },
        "synthetic_scope": {
            "captured_at": _timestamp(captured_at),
            "hat_id": bundle.ids["hat"],
            "hat_scope_id": bundle.ids["hat_scope"],
            "knowledge_version_id": bundle.ids["knowledge_version"],
            "namespace_digest": bundle.ids["namespace"],
            "owner_user_id": None,
            "saga_id": bundle.saga.saga_id,
            "snapshot_id": bundle.snapshot.snapshot_id,
            "source_id": bundle.ids["source"],
            "tenant_id": bundle.ids["tenant"],
            "user_id": bundle.ids["user"],
        },
        "database_changes": {
            "base_fixture_rows": {
                "hat_manifests": 1,
                "hat_scopes": 1,
                "knowledge_sources": 1,
                "knowledge_versions": 1,
                "source_snapshots": 1,
                "tenants": 1,
                "users": 1,
            },
            "ingestion_external_effects": 7,
            "ingestion_orphans": 0,
            "ingestion_saga_events": 8,
            "ingestion_sagas": 1,
            "persistence_operations": 5,
            "source_publication_events": 3,
            "source_registry": 1,
        },
        "boundaries": {
            "cross_system_acid_claim": False,
            "parser": "SYNTHETIC_TYPED_RECEIPT_ONLY",
            "production_parser": False,
            "step11_implemented": False,
            "validator": "SYNTHETIC_TYPED_RECEIPT_ONLY",
        },
        "replay_and_reconciliation": {
            "conflicting_replay": "REJECT_BEFORE_EXTERNAL_EFFECT",
            "exact_replay": "RETURN_SAME_PUBLISHED_SAGA",
            "external_volume_writes": 1,
            "s3_put_object_calls": 1,
            "s3_reconciliation": "EXACT_KEY_AND_VERSION_READ_ONLY",
            "step9_duplicate_events": 0,
        },
        "persistent_artifacts": {
            "cockroachdb": "NONE_AFTER_OWNED_RUNTIME_CLEANUP",
            "external_volume": (
                f"ingestion/downloads/{bundle.saga.local_relative_path}"
            ),
            "repository_evidence": str(
                evidence_output.relative_to(REPOSITORY_ROOT)
            ),
            "s3": (
                f"s3://{S3_BUCKET}/{bundle.storage_plan.object_key}"
            ),
        },
        "safety": {
            "existing_item_deleted": False,
            "existing_item_overwritten": False,
            "german_law_data": False,
            "model_calls": False,
            "personal_data": False,
            "public_access": False,
            "retention_bypass": False,
        },
        "cost_and_retention": {
            "estimated_payload_bytes": FIXTURE_LENGTH,
            "new_aws_resources": 0,
            "s3_version_retained_until": _timestamp(retain_until),
            "storage_cost": "one 92-byte Object-Locked version plus metadata",
        },
    }
    plan_digest = canonical_sha256(facts)
    invocation = [
        "python3",
        "scripts/run_ingestion_saga_validation.py",
        "--write-validation",
        "--cockroach-binary",
        str(binary),
        "--aws-binary",
        str(aws_binary),
        "--captured-at",
        _timestamp(captured_at),
        "--retain-until",
        _timestamp(retain_until),
        "--evidence-output",
        str(evidence_output.relative_to(REPOSITORY_ROOT)),
        "--confirm-project",
        EXTERNAL_VOLUME_PROJECT_ID,
        "--confirm-device-reference",
        str(external["device_reference"]),
        "--confirm-object-key",
        bundle.storage_plan.object_key,
        "--confirm-payload-sha256",
        FIXTURE_SHA256,
        "--confirm-plan-digest",
        plan_digest,
    ]
    return {
        **facts,
        "plan_digest": plan_digest,
        "exact_command_argv": invocation,
    }


def prepare_plan(
    *,
    cockroach_binary: Path,
    aws_binary: Path,
    captured_at: datetime,
    retain_until: datetime,
    external_config: Path,
    evidence_output: Path,
) -> tuple[
    dict[str, object],
    ValidationBundle,
    ExternalVolumeRuntimeAdapter,
]:
    repository = _repository_guard()
    binary = cockroach_binary.expanduser().resolve()
    binary_identity = migrations.verify_binary_identity(binary)
    if binary_identity["binary_sha256"] != PINNED_BINARY_SHA256:
        raise Step10ValidationError("CockroachDB binary digest differs")
    aws = _verify_aws_binary(aws_binary)
    aws_binary_sha256 = _file_sha256(aws)
    identity = _aws_identity(aws)
    if retain_until <= captured_at:
        raise Step10ValidationError("retain-until must follow captured-at")
    bundle = build_validation_bundle(captured_at, retain_until)
    adapter, external = _external_preflight(bundle, external_config)
    output = _resolve_evidence_output(evidence_output)
    if os.path.lexists(output):
        raise Step10ValidationError("Step 10 evidence output already exists")
    with tempfile.TemporaryDirectory(prefix="mp-step10-s3-preflight-") as raw:
        _, _, s3 = _s3_preflight(
            bundle,
            aws,
            aws_binary_sha256,
            Path(raw),
        )
    plan = _build_plan(
        repository=repository,
        binary=binary,
        binary_identity=binary_identity,
        aws_identity=identity,
        external=external,
        s3=s3,
        bundle=bundle,
        captured_at=captured_at,
        retain_until=retain_until,
        evidence_output=output,
        aws_binary=aws,
        aws_binary_sha256=aws_binary_sha256,
    )
    return plan, bundle, adapter


def _build_recovery_plan(
    *,
    repository: Mapping[str, object],
    repair: Mapping[str, object],
    binary: Path,
    binary_identity: Mapping[str, str],
    aws_identity: Mapping[str, object],
    external: Mapping[str, object],
    s3: Mapping[str, object],
    bundle: ValidationBundle,
    captured_at: datetime,
    retain_until: datetime,
    evidence_output: Path,
    recovery_failure_output: Path,
    preserved_failure_output: Path,
    aws_binary: Path,
    aws_binary_sha256: str,
) -> dict[str, object]:
    shutdown_budget = migrations.derive_graceful_shutdown_budget(
        AUDITED_SHUTDOWN_SETTINGS
    )
    suffix = bundle.ids["namespace"][:16]
    database = f"mp_step10_{suffix}_recovery"
    login_role = f"mp_step10_{suffix}_recovery_app"
    facts: dict[str, object] = {
        "schema_version": "1.0.0",
        "step": "Step 10 - Idempotent S3-CockroachDB Ingestion Saga 1A",
        "mode": "ZERO_EXTERNAL_WRITE_RECOVERY_VALIDATION",
        "repository": {**dict(repository), **dict(repair)},
        "aws": {
            **aws_identity,
            "bucket": S3_BUCKET,
            "cli_binary_sha256": aws_binary_sha256,
            "new_writes": 0,
            "object_key": bundle.storage_plan.object_key,
            "payload_length": bundle.snapshot.content_length,
            "payload_sha256": bundle.snapshot.content_sha256,
            "retention_mode": S3ObjectLockMode.GOVERNANCE.value,
            "retain_until": _timestamp(retain_until),
            "target_version_count": s3["target_version_count"],
            "version_id": s3["version_id"],
        },
        "external_volume": {**dict(external), "new_writes": 0},
        "cockroachdb": {
            "binary_path": str(binary),
            "binary_sha256": binary_identity["binary_sha256"],
            "database": database,
            "exact_server_version": binary_identity["build_tag"],
            "external_io": "DISABLED",
            "login_role": login_role,
            "loopback_only": True,
            "persistent_data": False,
            "runtime_mode": "DISPOSABLE_LOCAL_SINGLE_NODE",
            "shutdown_budget": shutdown_budget,
            "shutdown_method": "NODE_DRAIN_SELF_ON_RPC_WITH_SHUTDOWN",
            "store": "type=mem,size=640MiB",
        },
        "synthetic_scope": {
            "captured_at": _timestamp(captured_at),
            "hat_id": bundle.ids["hat"],
            "hat_scope_id": bundle.ids["hat_scope"],
            "knowledge_version_id": bundle.ids["knowledge_version"],
            "namespace_digest": bundle.ids["namespace"],
            "owner_user_id": None,
            "saga_id": bundle.saga.saga_id,
            "snapshot_id": bundle.snapshot.snapshot_id,
            "source_id": bundle.ids["source"],
            "tenant_id": bundle.ids["tenant"],
            "user_id": bundle.ids["user"],
        },
        "database_changes": {
            "ephemeral_only": True,
            "ingestion_external_effects": 7,
            "ingestion_orphans": 0,
            "ingestion_saga_events": 8,
            "ingestion_sagas": 1,
            "persistence_operations": 5,
            "source_publication_events": 3,
            "source_registry": 1,
        },
        "recovery": {
            "conflicting_replay": "REJECT_BEFORE_EXTERNAL_EFFECT",
            "deletions": 0,
            "exact_replay": "RETURN_SAME_PUBLISHED_SAGA",
            "external_volume_writes": 0,
            "failure_evidence_digest": PRESERVED_FAILURE_EVIDENCE_DIGEST,
            "failure_evidence_path": str(
                preserved_failure_output.relative_to(REPOSITORY_ROOT)
            ),
            "recovery_failure_evidence_path": str(
                recovery_failure_output.relative_to(REPOSITORY_ROOT)
            ),
            "retention_changes": 0,
            "s3_put_object_calls": 0,
            "success_evidence_path": str(
                evidence_output.relative_to(REPOSITORY_ROOT)
            ),
        },
        "boundaries": {
            "cross_system_acid_claim": False,
            "parser": "SYNTHETIC_TYPED_RECEIPT_ONLY",
            "production_parser": False,
            "step11_implemented": False,
            "validator": "SYNTHETIC_TYPED_RECEIPT_ONLY",
        },
        "safety": {
            "existing_item_deleted": False,
            "existing_item_overwritten": False,
            "new_aws_resources": 0,
            "public_access": False,
            "retention_bypass": False,
        },
    }
    plan_digest = canonical_sha256(facts)
    invocation = [
        "python3",
        "scripts/run_ingestion_saga_validation.py",
        "--recovery-validation",
        "--cockroach-binary",
        str(binary),
        "--aws-binary",
        str(aws_binary),
        "--captured-at",
        _timestamp(captured_at),
        "--retain-until",
        _timestamp(retain_until),
        "--evidence-output",
        str(evidence_output.relative_to(REPOSITORY_ROOT)),
        "--failure-evidence-output",
        str(recovery_failure_output.relative_to(REPOSITORY_ROOT)),
        "--preserved-failure-evidence",
        str(preserved_failure_output.relative_to(REPOSITORY_ROOT)),
        "--confirm-project",
        EXTERNAL_VOLUME_PROJECT_ID,
        "--confirm-device-reference",
        str(external["device_reference"]),
        "--confirm-external-relative-path",
        str(external["relative_path"]),
        "--confirm-payload-sha256",
        FIXTURE_SHA256,
        "--confirm-bucket",
        S3_BUCKET,
        "--confirm-object-key",
        bundle.storage_plan.object_key,
        "--confirm-version-id",
        str(s3["version_id"]),
        "--confirm-retain-until",
        _timestamp(retain_until),
        "--confirm-failure-evidence-digest",
        PRESERVED_FAILURE_EVIDENCE_DIGEST,
        "--confirm-recovery-plan-digest",
        plan_digest,
    ]
    return {
        **facts,
        "recovery_plan_digest": plan_digest,
        "exact_command_argv": invocation,
    }


def prepare_recovery_plan(
    *,
    cockroach_binary: Path,
    aws_binary: Path,
    captured_at: datetime,
    retain_until: datetime,
    external_config: Path,
    evidence_output: Path,
    failure_evidence_output: Path,
    preserved_failure_evidence: Path,
) -> tuple[
    dict[str, object],
    ValidationBundle,
    ExternalVolumeRuntimeAdapter,
]:
    repository = _repository_guard()
    repair = _repair_fingerprint()
    binary = cockroach_binary.expanduser().resolve()
    binary_identity = migrations.verify_binary_identity(binary)
    if binary_identity["binary_sha256"] != PINNED_BINARY_SHA256:
        raise Step10ValidationError("CockroachDB binary digest differs")
    aws = _verify_aws_binary(aws_binary)
    aws_binary_sha256 = _file_sha256(aws)
    identity = _aws_identity(aws)
    if retain_until <= captured_at:
        raise Step10ValidationError("retain-until must follow captured-at")
    bundle = build_validation_bundle(captured_at, retain_until)
    adapter, external = _external_recovery_preflight(bundle, external_config)
    output = _resolve_evidence_output(evidence_output)
    recovery_failure_output = _resolve_evidence_output(
        failure_evidence_output
    )
    preserved_output = _resolve_evidence_output(preserved_failure_evidence)
    if output == recovery_failure_output or output == preserved_output:
        raise Step10ValidationError("recovery evidence paths overlap")
    if os.path.lexists(output) or os.path.lexists(recovery_failure_output):
        raise Step10ValidationError("new recovery evidence output already exists")
    _load_preserved_failure_evidence(preserved_output)
    with tempfile.TemporaryDirectory(prefix="mp-step10-s3-recovery-") as raw:
        _, _, s3 = _s3_recovery_preflight(
            bundle,
            aws,
            aws_binary_sha256,
            Path(raw),
            PRESERVED_S3_VERSION_ID,
        )
    plan = _build_recovery_plan(
        repository=repository,
        repair=repair,
        binary=binary,
        binary_identity=binary_identity,
        aws_identity=identity,
        external=external,
        s3=s3,
        bundle=bundle,
        captured_at=captured_at,
        retain_until=retain_until,
        evidence_output=output,
        recovery_failure_output=recovery_failure_output,
        preserved_failure_output=preserved_output,
        aws_binary=aws,
        aws_binary_sha256=aws_binary_sha256,
    )
    return plan, bundle, adapter


def _sql_json(value: object) -> str:
    serialized = json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return migrations.sql_literal(serialized) + "::JSONB"


def _sql_timestamp(value: datetime) -> str:
    return migrations.sql_literal(value.isoformat()) + "::TIMESTAMPTZ"


def _quoted_identifier(value: str) -> str:
    if _SAFE_SQL_IDENTIFIER.fullmatch(value) is None:
        raise Step10ValidationError("unsafe disposable SQL identifier")
    return f'"{value}"'


def _seed_sql(bundle: ValidationBundle) -> str:
    q = migrations.sql_literal
    at = _sql_timestamp(bundle.snapshot.captured_at)
    ids = bundle.ids
    object_reference = (
        f"s3:{bundle.storage_plan.bucket_reference}:"
        f"{bundle.storage_plan.object_key}"
    )
    statements = [
        "INSERT INTO memory_patch.tenants "
        "(tenant_id, display_name, metadata, created_at, updated_at) VALUES "
        f"({q(ids['tenant'])}, 'Synthetic Step10 Validation Tenant', "
        f"{_sql_json({'synthetic_validation': True})}, {at}, {at})",
        "INSERT INTO memory_patch.users "
        "(tenant_id, user_id, display_name, metadata, created_at, updated_at) "
        "VALUES "
        f"({q(ids['tenant'])}, {q(ids['user'])}, "
        f"'Synthetic Step10 Validation User', "
        f"{_sql_json({'synthetic_validation': True})}, {at}, {at})",
        "INSERT INTO memory_patch.hat_manifests "
        "(hat_id, hat_version, schema_version, display_name, manifest_hash, "
        "capabilities, approval_authority, commit_authority, "
        "canonical_write_authority, external_action_authority, "
        "allows_private_memory_access, allows_user_code, created_at) VALUES "
        f"({q(ids['hat'])}, '1.0.0', '1.0.0', "
        f"'Synthetic Step10 Validation HAT', "
        f"{q(canonical_sha256({'hat_id': ids['hat']}))}, '[]'::JSONB, "
        "'NONE', 'NONE', 'NONE', 'NONE', false, false, "
        f"{at})",
        "INSERT INTO memory_patch.hat_scopes "
        "(tenant_id, hat_scope_id, target_scope, knowledge_hat_id, "
        "knowledge_hat_version, created_at) VALUES "
        f"({q(ids['tenant'])}, {q(ids['hat_scope'])}, "
        f"'SHARED_KNOWLEDGE_HAT', {q(ids['hat'])}, '1.0.0', {at})",
        "INSERT INTO memory_patch.knowledge_sources "
        "(tenant_id, source_id, hat_scope_id, source_kind, "
        "source_reference, provenance, source_observed_at, created_at) VALUES "
        f"({q(ids['tenant'])}, {q(ids['source'])}, {q(ids['hat_scope'])}, "
        "'SYNTHETIC_VALIDATION', "
        f"{q('synthetic://memory-patch/step10/' + ids['namespace'])}, "
        f"{_sql_json({'synthetic_validation': True})}, {at}, {at})",
        "INSERT INTO memory_patch.source_snapshots "
        "(tenant_id, snapshot_id, source_id, hat_scope_id, content_sha256, "
        "byte_length, storage_class, immutable_object_reference, captured_at, "
        "source_observed_at, provenance) VALUES "
        f"({q(ids['tenant'])}, {q(bundle.snapshot.snapshot_id)}, "
        f"{q(ids['source'])}, {q(ids['hat_scope'])}, "
        f"{q(bundle.snapshot.content_sha256)}, "
        f"{bundle.snapshot.content_length}, 'S3_GLOBAL_LOCKED_SNAPSHOT', "
        f"{q(object_reference)}, {at}, {at}, "
        f"{_sql_json({'storage_intent_only': True})})",
        "INSERT INTO memory_patch.knowledge_versions "
        "(tenant_id, knowledge_version_id, source_id, snapshot_id, "
        "hat_scope_id, version_ordinal, normalized_content_sha256, "
        "normalization_profile, is_current, created_at, provenance) VALUES "
        f"({q(ids['tenant'])}, {q(ids['knowledge_version'])}, "
        f"{q(ids['source'])}, {q(bundle.snapshot.snapshot_id)}, "
        f"{q(ids['hat_scope'])}, 1, {q(bundle.snapshot.content_sha256)}, "
        f"'exact-byte-synthetic-validation-placeholder-v1', true, {at}, "
        f"{_sql_json({'step11_implemented': False})})",
    ]
    return "BEGIN;\n" + ";\n".join(statements) + ";\nCOMMIT;"


def _create_login_role(root: migrations.SqlClient, role: str) -> None:
    quoted = _quoted_identifier(role)
    root.execute(
        "defaultdb",
        "SET allow_role_memberships_to_change_during_transaction = true;\n"
        f"CREATE ROLE {quoted} "
        "WITH LOGIN NOCREATEROLE NOCREATEDB NOBYPASSRLS;\n"
        "GRANT mp_app_runtime, mp_request_context_setter TO "
        f"{quoted}",
        timeout=180,
    )


def _drop_login_role(root: migrations.SqlClient, role: str) -> None:
    root.execute(
        "defaultdb",
        "SET allow_role_memberships_to_change_during_transaction = true;\n"
        f"DROP ROLE IF EXISTS {_quoted_identifier(role)}",
        timeout=180,
    )


def _row_counts(
    root: migrations.SqlClient,
    database: str,
    tenant_id: str,
    saga_id: str,
    source_id: str,
) -> dict[str, int]:
    q = migrations.sql_literal
    output = root.execute(
        database,
        "SELECT "
        "(SELECT count(*) FROM memory_patch.ingestion_sagas "
        f"WHERE tenant_id = {q(tenant_id)} AND saga_id = {q(saga_id)}) "
        "AS ingestion_sagas, "
        "(SELECT count(*) FROM memory_patch.ingestion_saga_events "
        f"WHERE tenant_id = {q(tenant_id)} AND saga_id = {q(saga_id)}) "
        "AS ingestion_saga_events, "
        "(SELECT count(*) FROM memory_patch.ingestion_external_effects "
        f"WHERE tenant_id = {q(tenant_id)} AND saga_id = {q(saga_id)}) "
        "AS ingestion_external_effects, "
        "(SELECT count(*) FROM memory_patch.ingestion_orphans "
        f"WHERE tenant_id = {q(tenant_id)}) AS ingestion_orphans, "
        "(SELECT count(*) FROM memory_patch.source_registry_entries "
        f"WHERE tenant_id = {q(tenant_id)} AND source_id = {q(source_id)}) "
        "AS source_registry, "
        "(SELECT count(*) FROM memory_patch.source_publication_events "
        f"WHERE tenant_id = {q(tenant_id)} AND source_id = {q(source_id)}) "
        "AS source_publication_events, "
        "(SELECT count(*) FROM memory_patch.persistence_operations "
        f"WHERE tenant_id = {q(tenant_id)}) AS persistence_operations",
    )
    rows = migrations.parse_tsv(output)
    if len(rows) != 1:
        raise Step10ValidationError("database count probe returned wrong shape")
    try:
        return {key: int(value) for key, value in rows[0].items()}
    except (TypeError, ValueError) as exc:
        raise Step10ValidationError("database count probe was malformed") from exc


def _publication_state(
    root: migrations.SqlClient,
    database: str,
    tenant_id: str,
    source_id: str,
) -> str:
    q = migrations.sql_literal
    return migrations.one_value(
        root.execute(
            database,
            "SELECT current_publication_state "
            "FROM memory_patch.source_registry_entries "
            f"WHERE tenant_id = {q(tenant_id)} "
            f"AND source_id = {q(source_id)}",
        )
    )


def _write_exclusive(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as destination:
            destination.write(payload)
            destination.flush()
            os.fsync(destination.fileno())
    except FileExistsError as exc:
        raise Step10ValidationError("evidence output already exists") from exc


def _resolve_evidence_output(path: Path) -> Path:
    candidate = path if path.is_absolute() else REPOSITORY_ROOT / path
    resolved = candidate.resolve()
    try:
        resolved.relative_to(REPOSITORY_ROOT)
    except ValueError as exc:
        raise Step10ValidationError(
            "evidence output must remain inside the repository"
        ) from exc
    return resolved


def _sanitized_failure(error: BaseException) -> str:
    value = getattr(error, "sanitized_code", None)
    if isinstance(value, str) and re.fullmatch(r"[A-Z0-9_:-]{1,128}", value):
        return value
    return type(error).__name__


def _plan_digest(plan: Mapping[str, object]) -> str:
    for field in ("recovery_plan_digest", "plan_digest"):
        value = plan.get(field)
        if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value):
            return value
    raise Step10ValidationError("validation plan digest is unavailable")


def _plan_section(
    plan: Mapping[str, object],
    field: str,
) -> Mapping[str, object]:
    value = plan.get(field)
    if not isinstance(value, Mapping):
        raise Step10ValidationError(f"validation plan {field} is malformed")
    return value


def _failure_evidence(
    *,
    plan: Mapping[str, object],
    sanitized_error: str,
    cleanup: Mapping[str, object],
    cleanup_errors: Sequence[str],
    external_write_attempts: int | None,
    s3_put_attempts: int | None,
    primary_validation_error: str | None = None,
    validation: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Build durable diagnostics without exception text or local paths."""

    external = _plan_section(plan, "external_volume")
    aws = _plan_section(plan, "aws")
    result: dict[str, object] = {
        "schema_version": "1.0.0",
        "step": "Step 10 - Idempotent S3-CockroachDB Ingestion Saga 1A",
        "status": "FAILED_SAFELY",
        "plan_digest": _plan_digest(plan),
        "first_root_cause": sanitized_error,
        "sanitized_error": sanitized_error,
        "primary_validation_error": primary_validation_error,
        "validation_status_before_cleanup": (
            "PASS" if validation is not None else "FAILED"
        ),
        "saga_milestone_before_cleanup": (
            None
            if validation is None
            else _plan_section(validation, "saga").get("final_milestone")
        ),
        "targets": {
            "external_relative_path": external["relative_path"],
            "s3_bucket": aws["bucket"],
            "s3_object_key": aws["object_key"],
            "s3_version_id": aws.get("version_id"),
        },
        "observed_attempts": {
            "external_volume_write": external_write_attempts,
            "s3_put_object": s3_put_attempts,
        },
        "preservation": {
            "external_artifact_deleted": False,
            "s3_version_deleted": False,
            "retention_bypass_used": False,
        },
        "cleanup": {
            "cleanup_errors": tuple(dict.fromkeys(cleanup_errors)),
            "drain_command_completed": bool(
                cleanup.get("drain_command_completed", False)
            ),
            "drain_completion_marker": bool(
                cleanup.get("drain_completion_marker", False)
            ),
            "drain_shutdown_requested": bool(
                cleanup.get("drain_shutdown_requested", False)
            ),
            "force_kill_used": bool(cleanup.get("force_kill_used", False)),
            "graceful_shutdown_requested": bool(
                cleanup.get("graceful_shutdown_requested", False)
            ),
            "owned_child_processes_reaped": bool(
                cleanup.get("owned_child_processes_reaped", False)
            ),
            "persistent_database_created": False,
            "persistent_service_created": False,
            "ports_closed": bool(cleanup.get("ports_closed", False)),
            "process_exit_code": cleanup.get("process_exit_code"),
            "process_exit_accepted": bool(
                cleanup.get("process_exit_accepted", False)
            ),
            "runtime_log_evidence": cleanup.get("runtime_log_evidence"),
            "runtime_pid_exited": bool(cleanup.get("pid_exited", False)),
            "shutdown_budget": cleanup.get("shutdown_budget"),
            "shutdown_method": cleanup.get("shutdown_method"),
            "temporary_directory_removed": bool(
                cleanup.get("temporary_store_removed", False)
            ),
        },
    }
    result["evidence_digest"] = canonical_sha256(result)
    return result


def _cleanup_invariant_failures(
    cleanup: Mapping[str, object],
    cleanup_errors: Sequence[str],
) -> tuple[str, ...]:
    failures = list(cleanup_errors)
    checks = (
        ("drain_command_completed", "BOUNDED_NODE_DRAIN_FAILED"),
        ("drain_completion_marker", "GRACEFUL_DRAIN_MARKER_MISSING"),
        ("drain_shutdown_requested", "DRAIN_SHUTDOWN_NOT_REQUESTED"),
        ("graceful_shutdown_requested", "GRACEFUL_SHUTDOWN_NOT_REQUESTED"),
        ("pid_exited", "OWNED_COCKROACH_PID_REMAINS"),
        ("process_exit_accepted", "GRACEFUL_SHUTDOWN_EXIT_CODE_MISMATCH"),
        ("ports_closed", "OWNED_LOOPBACK_PORT_REMAINS"),
        ("temporary_store_removed", "OWNED_TEMPORARY_RUNTIME_REMAINS"),
        ("owned_child_processes_reaped", "OWNED_SQL_CHILD_PROCESS_REMAINS"),
    )
    for field, code in checks:
        if not bool(cleanup.get(field, False)):
            failures.append(code)
    if bool(cleanup.get("force_kill_used", False)):
        failures.append("DISPOSABLE_RUNTIME_FORCE_KILL_USED")
    return tuple(dict.fromkeys(failures))


def _write_verified_evidence(path: Path, result: Mapping[str, object]) -> None:
    expected = result.get("evidence_digest")
    unsigned = {key: value for key, value in result.items() if key != "evidence_digest"}
    if not isinstance(expected, str) or canonical_sha256(unsigned) != expected:
        raise Step10ValidationError(
            "evidence digest differs before write",
            sanitized_code="EVIDENCE_DIGEST_MISMATCH",
        )
    _write_exclusive(path, _canonical_json(result))
    try:
        persisted = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise Step10ValidationError(
            "written evidence could not be read back",
            sanitized_code="EVIDENCE_READ_BACK_FAILED",
        ) from error
    if persisted != dict(result):
        raise Step10ValidationError(
            "written evidence read-back differs",
            sanitized_code="EVIDENCE_READ_BACK_MISMATCH",
        )


class _CleanupOnce:
    """Make the disposable-runtime cleanup call cardinality explicit."""

    def __init__(self) -> None:
        self._ran = False

    def run(self, cleanup: object) -> Mapping[str, object]:
        if self._ran:
            raise Step10ValidationError(
                "owned runtime cleanup was invoked twice",
                sanitized_code="DISPOSABLE_RUNTIME_CLEANUP_REPEATED",
            )
        if not callable(cleanup):
            raise Step10ValidationError("owned runtime cleanup is not callable")
        self._ran = True
        result = cleanup()
        if not isinstance(result, Mapping):
            raise Step10ValidationError("owned runtime cleanup result is malformed")
        return result


def _success_evidence(
    *,
    plan: Mapping[str, object],
    validation: Mapping[str, object],
    cleanup: Mapping[str, object],
    recovery_existing: bool,
) -> dict[str, object]:
    result: dict[str, object] = {
        "schema_version": "1.0.0",
        "step": "Step 10 - Idempotent S3-CockroachDB Ingestion Saga 1A",
        "status": "PASS",
        "mode": (
            "ZERO_EXTERNAL_WRITE_RECOVERY_VALIDATION"
            if recovery_existing
            else "SYNTHETIC_MULTI_SYSTEM_VALIDATION"
        ),
        "plan_digest": _plan_digest(plan),
        "repository": {
            "head": EXPECTED_HEAD,
            "remote": EXPECTED_REMOTE + ".git",
        },
        "aws": {
            "caller_type": "ASSUMED_ROLE",
            "permission_context": AWS_PERMISSION_CONTEXT,
            "profile": AWS_PROFILE,
            "region": AWS_REGION,
            "root_principal": False,
            "sensitive_identifiers_redacted": True,
            "temporary_sso_role": True,
        },
        "validation": dict(validation),
        "authority": {
            "cockroachdb": "ORCHESTRATION_STATE_ONLY",
            "external_volume": "DERIVED_STAGING_ONLY",
            "memory_patch_kernel": "SEMANTIC_AUTHORITY",
            "model_or_hat": "NO_AUTHORITY",
            "s3": "STORAGE_EVIDENCE_ONLY",
            "step9": "PUBLICATION_POLICY_BOUNDARY",
        },
        "cleanup": {
            "database_removed": True,
            "drain_command_completed": cleanup["drain_command_completed"],
            "drain_completion_marker": cleanup["drain_completion_marker"],
            "drain_shutdown_requested": cleanup[
                "drain_shutdown_requested"
            ],
            "drain_elapsed_seconds": cleanup.get("drain_elapsed_seconds"),
            "drain_output_sha256": cleanup.get("drain_output_sha256"),
            "force_kill_used": cleanup["force_kill_used"],
            "graceful_shutdown_requested": cleanup[
                "graceful_shutdown_requested"
            ],
            "owned_child_processes_reaped": cleanup[
                "owned_child_processes_reaped"
            ],
            "persistent_database_created": False,
            "persistent_service_created": False,
            "ports_closed": cleanup["ports_closed"],
            "process_exit_code": cleanup["process_exit_code"],
            "process_exit_accepted": cleanup["process_exit_accepted"],
            "runtime_log_evidence": cleanup["runtime_log_evidence"],
            "runtime_mode": "DISPOSABLE_LOCAL_SINGLE_NODE",
            "runtime_pid_exited": cleanup["pid_exited"],
            "shutdown_budget": cleanup["shutdown_budget"],
            "shutdown_method": cleanup["shutdown_method"],
            "sigterm_sent_to_exact_pid": cleanup[
                "sigterm_sent_to_exact_pid"
            ],
            "temporary_directory_removed": cleanup[
                "temporary_store_removed"
            ],
        },
        "recovery": {
            "enabled": recovery_existing,
            "existing_external_artifact_reconciled": recovery_existing,
            "existing_s3_version_reconciled": recovery_existing,
            "failure_evidence_digest": (
                PRESERVED_FAILURE_EVIDENCE_DIGEST
                if recovery_existing
                else None
            ),
            "failure_evidence_preserved": recovery_existing,
            "new_external_volume_writes": (
                validation["external_volume"]["write_calls"]
            ),
            "new_s3_writes": validation["s3"]["put_object_calls"],
        },
        "safety": {
            "cross_system_acid_claim": False,
            "existing_data_deleted": False,
            "existing_data_overwritten": False,
            "german_law_data": False,
            "model_calls": False,
            "personal_data": False,
            "public_access": False,
            "retention_bypass": False,
        },
    }
    result["evidence_digest"] = canonical_sha256(result)
    return result


def _finalize_validation_evidence(
    *,
    plan: Mapping[str, object],
    validation: Mapping[str, object] | None,
    primary_failure: BaseException | None,
    cleanup: Mapping[str, object],
    cleanup_errors: Sequence[str],
    external_write_attempts: int | None,
    s3_put_attempts: int | None,
    success_output: Path,
    failure_output: Path,
    recovery_existing: bool,
) -> Mapping[str, object]:
    """Persist the combined saga and cleanup verdict before returning/raising."""

    if success_output == failure_output:
        raise Step10ValidationError(
            "success and failure evidence outputs overlap",
            sanitized_code="EVIDENCE_OUTPUT_COLLISION",
        )
    invariant_failures = _cleanup_invariant_failures(cleanup, cleanup_errors)
    primary_code = (
        None if primary_failure is None else _sanitized_failure(primary_failure)
    )
    if primary_failure is not None or invariant_failures:
        first_root = primary_code
        if first_root is None:
            first_root = (
                "DISPOSABLE_RUNTIME_FORCE_KILL_USED"
                if "DISPOSABLE_RUNTIME_FORCE_KILL_USED" in invariant_failures
                else invariant_failures[0]
            )
        failed = _failure_evidence(
            plan=plan,
            sanitized_error=first_root,
            primary_validation_error=primary_code,
            cleanup=cleanup,
            cleanup_errors=invariant_failures,
            external_write_attempts=external_write_attempts,
            s3_put_attempts=s3_put_attempts,
            validation=validation,
        )
        try:
            _write_verified_evidence(failure_output, failed)
        except BaseException as error:
            raise Step10ValidationError(
                "failure evidence could not be persisted",
                sanitized_code="FAILURE_EVIDENCE_WRITE_FAILED",
            ) from error
        raise Step10ValidationError(
            "live validation failed safely at " + first_root,
            sanitized_code=first_root,
        ) from primary_failure
    if validation is None:
        raise Step10ValidationError(
            "live validation produced no result",
            sanitized_code="LIVE_VALIDATION_RESULT_MISSING",
        )
    result = _success_evidence(
        plan=plan,
        validation=validation,
        cleanup=cleanup,
        recovery_existing=recovery_existing,
    )
    try:
        _write_verified_evidence(success_output, result)
    except BaseException as error:
        raise Step10ValidationError(
            "success evidence could not be persisted",
            sanitized_code="SUCCESS_EVIDENCE_WRITE_FAILED",
        ) from error
    return result


def execute_live_validation(
    *,
    plan: Mapping[str, object],
    bundle: ValidationBundle,
    external_adapter: ExternalVolumeRuntimeAdapter,
    cockroach_binary: Path,
    aws_binary: Path,
    evidence_output: Path,
    failure_evidence_output: Path = DEFAULT_FAILURE_EVIDENCE_OUTPUT,
    recovery_existing: bool = False,
    expected_version_id: str | None = None,
) -> Mapping[str, object]:
    """Perform only the exact confirmed multi-system synthetic validation."""

    cockroach = _plan_section(plan, "cockroachdb")
    database = cockroach.get("database")
    role = cockroach.get("login_role")
    if not isinstance(database, str) or not isinstance(role, str):
        raise Step10ValidationError("disposable database identity is malformed")
    _quoted_identifier(database)
    _quoted_identifier(role)
    run_suffix = "_recovery" if recovery_existing else ""
    run_id = "mp_step10_" + bundle.ids["namespace"][:12] + run_suffix
    runtime = migrations.LocalRuntime(
        binary=cockroach_binary,
        run_id=run_id,
    )
    child_registry = OwnedChildRegistry()
    cleanup_once = _CleanupOnce()
    root: migrations.SqlClient | None = None
    database_created = False
    role_created = False
    cleanup_errors: list[str] = []
    cleanup: dict[str, Any] = {
        "pid_exited": False,
        "ports_closed": False,
        "temporary_store_removed": False,
    }
    validation: dict[str, Any] | None = None
    failure: BaseException | None = None
    volume: CountingExternalVolume | None = None
    aws_client: AwsCliS3Client | None = None
    s3_recovery_client: RecoveryReadOnlyS3Client | None = None
    try:
        root = runtime.start()
        server_version = migrations.one_value(
            root.execute("defaultdb", "SELECT version()")
        )
        cluster_version = migrations.one_value(
            root.execute("defaultdb", "SHOW CLUSTER SETTING version")
        )
        if (
            migrations.PINNED_VERSION not in server_version
            or cluster_version != migrations.PINNED_CLUSTER_VERSION
        ):
            raise Step10ValidationError("live CockroachDB identity differs")
        migrations.create_database(root, database)
        database_created = True
        first_apply = migrations.apply_migrations(root, database, timeout=180)
        replay_apply = migrations.apply_migrations(root, database, timeout=180)
        expected_migrations = migrations.offline_validate()["migration_count"]
        if (
            first_apply["applied_count"] != expected_migrations
            or replay_apply["applied_count"] != 0
            or replay_apply["skipped_count"] != expected_migrations
        ):
            raise Step10ValidationError("live migration replay differs")
        catalog = migrations.schema_catalog(root, database)
        migrations.assert_catalog(catalog)
        step9_security = migrations.assert_step9_security_catalog(root, database)
        step10_security = migrations.assert_step10_security_catalog(root, database)
        _create_login_role(root, role)
        role_created = True
        root.execute(database, _seed_sql(bundle), timeout=180)
        if runtime.runtime_dir is None or runtime.sql_port is None:
            raise Step10ValidationError("owned runtime facts are unavailable")
        factory = connection_factory(
            binary=cockroach_binary,
            port=runtime.sql_port,
            database=database,
            user=role,
            log_directory=runtime.runtime_dir,
            child_registry=child_registry,
            timeout_seconds=60,
        )
        clock = ValidationClock(bundle.snapshot.captured_at)
        runner = SerializableTransactionRunner(factory)
        source_service = SourceRegistryService(runner, clock=clock)
        context = RequestContext(
            bundle.ids["tenant"],
            None,
            AccessMode.TENANT_SHARED,
        )
        registered_source = source_service.register_source(
            context,
            bundle.source_record,
            operation_id=(
                "step10-source-register-" + bundle.ids["namespace"][:32]
            ),
            idempotency_key=(
                "step10-source-register:" + bundle.ids["namespace"]
            ),
        )
        if registered_source.registry_digest != (
            bundle.source_record.registry_digest
        ):
            raise Step10ValidationError("Step 9 registration digest differs")
        eligibility = source_service.evaluate_publication_eligibility(
            context,
            tenant_id=bundle.ids["tenant"],
            source_id=bundle.ids["source"],
            hat_scope_id=bundle.ids["hat_scope"],
            evaluated_at=clock(),
        )
        if not eligibility.eligible:
            raise Step10ValidationError(
                "synthetic source is not publication eligible"
            )
        saga_service = IngestionSagaService(runner, clock=clock)
        publication = Step9PublicationPort(source_service, clock=clock)
        acquisition = (
            ReconciliationOnlyAcquisition()
            if recovery_existing
            else SyntheticAcquisition(bundle.snapshot.canonical_payload)
        )
        parser = SyntheticParserBoundary(clock)
        validator = SyntheticValidatorBoundary(clock)
        volume = CountingExternalVolume(
            external_adapter,
            writes_allowed=not recovery_existing,
        )
        aws_client = AwsCliS3Client(
            aws_binary=aws_binary,
            profile=AWS_PROFILE,
            region=AWS_REGION,
            temporary_directory=runtime.runtime_dir,
            expected_binary_sha256=str(
                plan["aws"]["cli_binary_sha256"]
            ),
        )
        s3_transport: object = aws_client
        if recovery_existing:
            if not isinstance(expected_version_id, str) or not expected_version_id:
                raise Step10ValidationError("recovery S3 version is unavailable")
            s3_recovery_client = RecoveryReadOnlyS3Client(aws_client)
            s3_transport = s3_recovery_client
        storage = S3SnapshotAdapter(bundle.storage_config, s3_transport)
        before_versions = aws_client.list_object_versions(
            bucket=S3_BUCKET,
            key=bundle.storage_plan.object_key,
        )
        if recovery_existing:
            if (
                len(before_versions) != 1
                or before_versions[0].get("VersionId") != expected_version_id
            ):
                raise Step10ValidationError(
                    "existing S3 recovery version changed before execution"
                )
        elif before_versions:
            raise Step10ValidationError(
                "S3 target changed after approval and before write"
            )
        orchestrator = IngestionOrchestrator(
            control=saga_service,
            external_volume=volume,
            snapshot_storage=storage,
            acquisition=acquisition,
            parser=parser,
            validator=validator,
            publication=publication,
            clock=clock,
            token_bytes=lambda count: bytes.fromhex(
                bundle.ids["namespace"]
            )[:count],
        )
        completed = orchestrator.execute(
            context,
            bundle.saga,
            bundle.snapshot,
        )
        if completed.current_milestone is not SagaMilestone.PUBLISHED:
            raise Step10ValidationError("saga did not reach PUBLISHED")
        events = saga_service.verify_event_chain(
            context,
            tenant_id=completed.tenant_id,
            saga_id=completed.saga_id,
        )
        if len(events) != 8:
            raise Step10ValidationError("saga event chain is incomplete")
        counts_before_replay = _row_counts(
            root,
            database,
            completed.tenant_id,
            completed.saga_id,
            completed.source_id,
        )
        expected_counts = {
            "ingestion_external_effects": 7,
            "ingestion_orphans": 0,
            "ingestion_saga_events": 8,
            "ingestion_sagas": 1,
            "persistence_operations": 5,
            "source_publication_events": 3,
            "source_registry": 1,
        }
        if counts_before_replay != expected_counts:
            raise Step10ValidationError("durable Step 10 row counts differ")
        put_count_before_replay = aws_client.operation_counts.get(
            "put-object",
            0,
        )
        replay = orchestrator.execute(
            context,
            bundle.saga,
            bundle.snapshot,
        )
        counts_after_replay = _row_counts(
            root,
            database,
            replay.tenant_id,
            replay.saga_id,
            replay.source_id,
        )
        if (
            replay.run_digest != completed.run_digest
            or counts_after_replay != counts_before_replay
            or aws_client.operation_counts.get("put-object", 0)
            != put_count_before_replay
        ):
            raise Step10ValidationError("exact replay duplicated durable effects")
        conflicting = build_initial_saga(
            tenant_id=bundle.saga.tenant_id,
            source_id=bundle.saga.source_id,
            hat_scope_id=bundle.saga.hat_scope_id,
            owner_user_id=None,
            knowledge_version_id=bundle.saga.knowledge_version_id,
            idempotency_key=bundle.saga.idempotency_key,
            scope_digest=bundle.saga.scope_digest,
            source_registry_digest=bundle.saga.source_registry_digest,
            content_sha256="f" * 64,
            content_length=bundle.saga.content_length,
            media_type=bundle.saga.media_type,
            local_relative_path=bundle.saga.local_relative_path,
            snapshot_id=bundle.saga.snapshot_id,
            captured_at=bundle.saga.captured_at,
            retain_until=bundle.saga.retain_until,
            created_at=bundle.saga.created_at,
        )
        conflict_code: str | None = None
        try:
            saga_service.register_saga(context, conflicting)
        except IdempotencyConflictError as error:
            conflict_code = _sanitized_failure(error)
        if conflict_code != "IDEMPOTENCY_BINDING_CONFLICT":
            raise Step10ValidationError("conflicting replay did not fail closed")
        reconciled_s3 = storage.reconcile_snapshot(bundle.snapshot)
        if reconciled_s3 is None or not reconciled_s3.content_verified:
            raise Step10ValidationError("exact S3 reconciliation failed")
        s3_version_id = reconciled_s3.version_id
        if recovery_existing and s3_version_id != expected_version_id:
            raise Step10ValidationError("reconciled S3 version differs")
        local_payload = external_adapter.read_exact(
            ExternalVolumeOperation.INGESTION_STAGING,
            bundle.saga.local_relative_path,
            expected_sha256=bundle.snapshot.content_sha256,
            expected_length=bundle.snapshot.content_length,
        )
        if local_payload != bundle.snapshot.canonical_payload:
            raise Step10ValidationError("exact local reconciliation failed")
        after_versions = aws_client.list_object_versions(
            bucket=S3_BUCKET,
            key=bundle.storage_plan.object_key,
        )
        expected_put_calls = 0 if recovery_existing else 1
        expected_volume_writes = 0 if recovery_existing else 1
        expected_acquisition_calls = 0 if recovery_existing else 1
        put_calls = aws_client.operation_counts.get("put-object", 0)
        put_attempts = (
            s3_recovery_client.put_object_calls
            if s3_recovery_client is not None
            else put_calls
        )
        if (
            len(after_versions) != 1
            or after_versions[0].get("VersionId") != s3_version_id
            or put_calls != expected_put_calls
            or put_attempts != expected_put_calls
            or volume.write_calls != expected_volume_writes
            or volume.write_attempts != expected_volume_writes
            or acquisition.calls != expected_acquisition_calls
            or parser.calls != 1
            or validator.calls != 1
        ):
            raise Step10ValidationError(
                "live external effect count or exact version differs"
            )
        publication_events = source_service.verify_publication_event_chain(
            context,
            tenant_id=completed.tenant_id,
            source_id=completed.source_id,
            hat_scope_id=completed.hat_scope_id,
        )
        if (
            len(publication_events) != 3
            or _publication_state(
                root,
                database,
                completed.tenant_id,
                completed.source_id,
            )
            != SourcePublicationState.PUBLISHED.value
        ):
            raise Step10ValidationError("Step 9 publication evidence differs")
        validation = {
            "database": {
                "cluster_version": cluster_version,
                "database_removed_after_validation": True,
                "first_migration_apply": first_apply,
                "migration_replay": replay_apply,
                "row_counts": counts_after_replay,
                "server_version": server_version.splitlines()[0],
                "step9_security_digest": step9_security["security_digest"],
                "step10_security_digest": step10_security["security_digest"],
            },
            "external_volume": {
                "atomic_no_replace": True,
                "content_length": bundle.snapshot.content_length,
                "content_sha256": bundle.snapshot.content_sha256,
                "device_reference": plan["external_volume"]["device_reference"],
                "exact_read_back": True,
                "operation": ExternalVolumeOperation.INGESTION_STAGING.value,
                "relative_path": plan["external_volume"]["relative_path"],
                "reconciled_existing": recovery_existing,
                "system_drive_fallback": False,
                "write_attempts": volume.write_attempts,
                "write_calls": volume.write_calls,
            },
            "s3": {
                "bucket": S3_BUCKET,
                "content_length": reconciled_s3.content_length,
                "content_sha256": reconciled_s3.canonical_sha256,
                "metadata_verified": reconciled_s3.metadata_verified,
                "object_key": reconciled_s3.object_key,
                "object_lock_mode": reconciled_s3.retention_mode.value,
                "put_object_attempts": put_attempts,
                "put_object_calls": put_calls,
                "reconciled_existing": recovery_existing,
                "retain_until": _timestamp(reconciled_s3.retain_until),
                "version_id": reconciled_s3.version_id,
                "version_count": len(after_versions),
            },
            "saga": {
                "conflicting_replay": "REJECTED",
                "conflicting_replay_code": conflict_code,
                "exact_replay": "SAME_COMPLETED_SAGA",
                "event_chain_length": len(events),
                "final_milestone": completed.current_milestone.value,
                "publication_event_count": len(publication_events),
                "run_digest": completed.run_digest,
                "saga_id": completed.saga_id,
            },
            "synthetic_boundaries": {
                "parser_calls": parser.calls,
                "production_parser": False,
                "step11_implemented": False,
                "validator_calls": validator.calls,
            },
        }
    except BaseException as error:
        failure = error
    finally:
        if root is not None and database_created:
            try:
                migrations.drop_database(root, database, timeout=180)
            except BaseException as error:
                cleanup_errors.append(_sanitized_failure(error))
        if root is not None and role_created:
            try:
                _drop_login_role(root, role)
            except BaseException as error:
                cleanup_errors.append(_sanitized_failure(error))
        owned_children_reaped = child_registry.all_reaped
        cleanup = dict(
            cleanup_once.run(
                lambda: (
                    runtime.graceful_stop_and_remove(
                        root,
                        owned_children_reaped=owned_children_reaped,
                    )
                    if root is not None
                    and runtime.process is not None
                    and runtime.process.poll() is None
                    else runtime.stop_and_remove()
                )
            )
        )
        cleanup.setdefault(
            "owned_child_processes_reaped",
            owned_children_reaped,
        )
        cleanup_errors.extend(str(item) for item in cleanup["cleanup_errors"])
    s3_attempts: int | None
    if s3_recovery_client is not None:
        s3_attempts = s3_recovery_client.put_object_calls
    elif aws_client is not None:
        s3_attempts = aws_client.operation_counts.get("put-object", 0)
    else:
        s3_attempts = None
    return _finalize_validation_evidence(
        plan=plan,
        validation=validation,
        primary_failure=failure,
        cleanup=cleanup,
        cleanup_errors=cleanup_errors,
        external_write_attempts=(
            None if volume is None else volume.write_attempts
        ),
        s3_put_attempts=s3_attempts,
        success_output=_resolve_evidence_output(evidence_output),
        failure_output=_resolve_evidence_output(failure_evidence_output),
        recovery_existing=recovery_existing,
    )


def _arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", action="store_true")
    mode.add_argument("--write-validation", action="store_true")
    mode.add_argument("--recovery-plan", action="store_true")
    mode.add_argument("--recovery-validation", action="store_true")
    parser.add_argument("--cockroach-binary", type=Path, required=True)
    parser.add_argument("--aws-binary", type=Path, required=True)
    parser.add_argument("--captured-at", required=True)
    parser.add_argument("--retain-until", required=True)
    parser.add_argument(
        "--external-config",
        type=Path,
        default=DEFAULT_EXTERNAL_CONFIG,
    )
    parser.add_argument(
        "--evidence-output",
        type=Path,
        default=DEFAULT_EVIDENCE_OUTPUT,
    )
    parser.add_argument("--failure-evidence-output", type=Path)
    parser.add_argument(
        "--preserved-failure-evidence",
        type=Path,
        default=DEFAULT_FAILURE_EVIDENCE_OUTPUT,
    )
    parser.add_argument("--confirm-project")
    parser.add_argument("--confirm-device-reference")
    parser.add_argument("--confirm-external-relative-path")
    parser.add_argument("--confirm-bucket")
    parser.add_argument("--confirm-object-key")
    parser.add_argument("--confirm-version-id")
    parser.add_argument("--confirm-retain-until")
    parser.add_argument("--confirm-payload-sha256")
    parser.add_argument("--confirm-plan-digest")
    parser.add_argument("--confirm-failure-evidence-digest")
    parser.add_argument("--confirm-recovery-plan-digest")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _arguments(argv)
    try:
        captured_at = _parse_timestamp(arguments.captured_at, "captured-at")
        retain_until = _parse_timestamp(
            arguments.retain_until,
            "retain-until",
        )
        recovery_mode = bool(
            arguments.recovery_plan or arguments.recovery_validation
        )
        failure_output = arguments.failure_evidence_output or (
            DEFAULT_RECOVERY_FAILURE_EVIDENCE_OUTPUT
            if recovery_mode
            else DEFAULT_FAILURE_EVIDENCE_OUTPUT
        )
        if recovery_mode:
            plan, bundle, external_adapter = prepare_recovery_plan(
                cockroach_binary=arguments.cockroach_binary,
                aws_binary=arguments.aws_binary,
                captured_at=captured_at,
                retain_until=retain_until,
                external_config=arguments.external_config,
                evidence_output=arguments.evidence_output,
                failure_evidence_output=failure_output,
                preserved_failure_evidence=(
                    arguments.preserved_failure_evidence
                ),
            )
        else:
            plan, bundle, external_adapter = prepare_plan(
                cockroach_binary=arguments.cockroach_binary,
                aws_binary=arguments.aws_binary,
                captured_at=captured_at,
                retain_until=retain_until,
                external_config=arguments.external_config,
                evidence_output=arguments.evidence_output,
            )
        if arguments.plan or arguments.recovery_plan:
            print(_canonical_json(plan).decode("utf-8"), end="")
            return 0
        external = _plan_section(plan, "external_volume")
        aws = _plan_section(plan, "aws")
        confirmations: dict[str, tuple[object, object]] = {
            "project": (arguments.confirm_project, EXTERNAL_VOLUME_PROJECT_ID),
            "device_reference": (
                arguments.confirm_device_reference,
                external["device_reference"],
            ),
            "object_key": (arguments.confirm_object_key, aws["object_key"]),
            "payload_sha256": (
                arguments.confirm_payload_sha256,
                FIXTURE_SHA256,
            ),
        }
        if recovery_mode:
            confirmations.update(
                {
                    "external_relative_path": (
                        arguments.confirm_external_relative_path,
                        external["relative_path"],
                    ),
                    "bucket": (arguments.confirm_bucket, aws["bucket"]),
                    "version_id": (
                        arguments.confirm_version_id,
                        aws["version_id"],
                    ),
                    "retain_until": (
                        arguments.confirm_retain_until,
                        aws["retain_until"],
                    ),
                    "failure_evidence_digest": (
                        arguments.confirm_failure_evidence_digest,
                        PRESERVED_FAILURE_EVIDENCE_DIGEST,
                    ),
                    "recovery_plan_digest": (
                        arguments.confirm_recovery_plan_digest,
                        plan["recovery_plan_digest"],
                    ),
                }
            )
        else:
            confirmations["plan_digest"] = (
                arguments.confirm_plan_digest,
                plan["plan_digest"],
            )
        mismatched = [
            field
            for field, (observed, expected) in confirmations.items()
            if observed != expected
        ]
        if mismatched:
            raise Step10ValidationError(
                "exact live gate confirmation differs: "
                + ",".join(mismatched)
            )
        result = execute_live_validation(
            plan=plan,
            bundle=bundle,
            external_adapter=external_adapter,
            cockroach_binary=arguments.cockroach_binary.expanduser().resolve(),
            aws_binary=_verify_aws_binary(arguments.aws_binary),
            evidence_output=arguments.evidence_output,
            failure_evidence_output=failure_output,
            recovery_existing=recovery_mode,
            expected_version_id=(
                str(aws["version_id"]) if recovery_mode else None
            ),
        )
        print(_canonical_json(result).decode("utf-8"), end="")
        return 0
    except Exception as error:
        print(
            json.dumps(
                {
                    "schema_version": "1.0.0",
                    "status": "FAILED_SAFELY",
                    "sanitized_error": _sanitized_failure(error),
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
