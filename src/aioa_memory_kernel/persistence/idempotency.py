"""Durable, compare-and-set idempotency and resume operations."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from .errors import (
    IdempotencyConflictError,
    OperationStateConflictError,
    PersistenceConfigurationError,
)
from .models import (
    BeginOperation,
    ExternalReferenceIdentity,
    OperationClaim,
    OperationStatus,
    PersistenceOperation,
)
from .protocols import Row, TransactionProtocol


_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_SQLSTATE = re.compile(r"^[0-9A-Z]{5}$")
_ERROR_CODE = re.compile(r"^[A-Z0-9][A-Z0-9_:-]{0,63}$")

OPERATION_COLUMNS = (
    "tenant_id, operation_id, schema_version, owner_user_id, "
    "operation_kind, idempotency_key, request_digest, scope_digest, "
    "status, attempt_count, result_ref, result_digest, last_sqlstate, "
    "sanitized_error_code, created_at, updated_at, completed_at, "
    "origin_kind, origin_system, origin_version, adapter_version, "
    "artifact_kind, external_ref"
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _timestamp(value: object, field: str) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise PersistenceConfigurationError(
                f"{field} must be timezone-aware",
                sanitized_code="INVALID_DATABASE_ROW",
            )
        return value.astimezone(UTC)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise PersistenceConfigurationError(
                f"{field} is not a canonical timestamp",
                sanitized_code="INVALID_DATABASE_ROW",
            ) from exc
        if parsed.tzinfo is None:
            raise PersistenceConfigurationError(
                f"{field} must be timezone-aware",
                sanitized_code="INVALID_DATABASE_ROW",
            )
        return parsed.astimezone(UTC)
    raise PersistenceConfigurationError(
        f"{field} is not a timestamp",
        sanitized_code="INVALID_DATABASE_ROW",
    )


def _optional_timestamp(value: object, field: str) -> datetime | None:
    if value is None:
        return None
    return _timestamp(value, field)


def operation_from_row(row: Mapping[str, object]) -> PersistenceOperation:
    external_values = tuple(
        row.get(field)
        for field in (
            "origin_kind",
            "origin_system",
            "origin_version",
            "adapter_version",
            "artifact_kind",
            "external_ref",
        )
    )
    if all(value is None for value in external_values):
        external_identity = None
    elif all(isinstance(value, str) for value in external_values):
        external_identity = ExternalReferenceIdentity(
            origin_kind=str(external_values[0]),
            origin_system=str(external_values[1]),
            origin_version=str(external_values[2]),
            adapter_version=str(external_values[3]),
            artifact_kind=str(external_values[4]),
            external_ref=str(external_values[5]),
        )
    else:
        raise PersistenceConfigurationError(
            "database returned a partial external identity",
            sanitized_code="PARTIAL_EXTERNAL_IDENTITY",
        )
    try:
        status = OperationStatus(str(row["status"]))
        attempt_count = int(row["attempt_count"])
    except (KeyError, TypeError, ValueError) as exc:
        raise PersistenceConfigurationError(
            "database returned an invalid persistence operation row",
            sanitized_code="INVALID_DATABASE_ROW",
        ) from exc
    return PersistenceOperation(
        operation_id=str(row["operation_id"]),
        tenant_id=str(row["tenant_id"]),
        owner_user_id=(
            None if row.get("owner_user_id") is None else str(row["owner_user_id"])
        ),
        operation_kind=str(row["operation_kind"]),
        idempotency_key=str(row["idempotency_key"]),
        request_digest=str(row["request_digest"]),
        scope_digest=str(row["scope_digest"]),
        status=status,
        attempt_count=attempt_count,
        result_ref=(
            None if row.get("result_ref") is None else str(row["result_ref"])
        ),
        result_digest=(
            None if row.get("result_digest") is None else str(row["result_digest"])
        ),
        last_sqlstate=(
            None if row.get("last_sqlstate") is None else str(row["last_sqlstate"])
        ),
        sanitized_error_code=(
            None
            if row.get("sanitized_error_code") is None
            else str(row["sanitized_error_code"])
        ),
        created_at=_timestamp(row["created_at"], "created_at"),
        updated_at=_timestamp(row["updated_at"], "updated_at"),
        completed_at=_optional_timestamp(row.get("completed_at"), "completed_at"),
        external_identity=external_identity,
        schema_version=str(row["schema_version"]),
    )


class IdempotencyService:
    """One-table durable claim, completion, interruption, and final failure API."""

    def __init__(self, *, clock: Callable[[], datetime] = _utc_now) -> None:
        self._clock = clock

    def begin_or_resume_operation(
        self,
        transaction: TransactionProtocol,
        request: BeginOperation,
    ) -> OperationClaim:
        existing = self._find_by_key(transaction, request)
        if existing is not None:
            return self._claim_existing(transaction, request, existing)

        external = request.external_identity
        row = transaction.fetch_one(
            f"""
            INSERT INTO memory_patch.persistence_operations (
              tenant_id, operation_id, schema_version, owner_user_id,
              operation_kind, idempotency_key, request_digest, scope_digest,
              status, attempt_count, result_ref, result_digest,
              last_sqlstate, sanitized_error_code, created_at, updated_at,
              completed_at, origin_kind, origin_system, origin_version,
              adapter_version, artifact_kind, external_ref
            )
            VALUES (
              %s, %s, %s, %s, %s, %s, %s, %s,
              'IN_PROGRESS', 1, NULL, NULL, NULL, NULL, %s, %s, NULL,
              %s, %s, %s, %s, %s, %s
            )
            ON CONFLICT DO NOTHING
            RETURNING {OPERATION_COLUMNS}
            """,
            (
                request.tenant_id,
                request.operation_id,
                request.schema_version,
                request.owner_user_id,
                request.operation_kind,
                request.idempotency_key,
                request.request_digest,
                request.scope_digest,
                request.created_at,
                request.created_at,
                None if external is None else external.origin_kind,
                None if external is None else external.origin_system,
                None if external is None else external.origin_version,
                None if external is None else external.adapter_version,
                None if external is None else external.artifact_kind,
                None if external is None else external.external_ref,
            ),
        )
        if row is not None:
            return OperationClaim(
                operation=operation_from_row(row),
                may_proceed=True,
                resumed=False,
            )

        raced = self._find_by_key(transaction, request)
        source = "idempotency key"
        if raced is None and external is not None:
            raced = self._find_by_external_identity(
                transaction,
                request.tenant_id,
                external,
            )
            source = "external identity"
        if raced is None:
            raise OperationStateConflictError(
                "idempotency claim could not resolve a concurrent operation",
                operation_kind=request.operation_kind,
                sanitized_code="CLAIM_RACE_UNRESOLVED",
            )
        self._validate_binding(raced, request, source=source)
        return self._claim_existing(
            transaction,
            request,
            raced,
            binding_already_checked=True,
        )

    def complete_operation(
        self,
        transaction: TransactionProtocol,
        *,
        tenant_id: str,
        operation_id: str,
        expected_attempt_count: int,
        result_digest: str,
        result_ref: str | None = None,
    ) -> PersistenceOperation:
        self._require_digest(result_digest, "result_digest")
        if expected_attempt_count < 1:
            raise PersistenceConfigurationError(
                "expected attempt count must be positive",
                sanitized_code="INVALID_ATTEMPT_COUNT",
            )
        now = self._clock()
        row = transaction.fetch_one(
            f"""
            UPDATE memory_patch.persistence_operations
               SET status = 'COMPLETED',
                   result_ref = %s,
                   result_digest = %s,
                   last_sqlstate = NULL,
                   sanitized_error_code = NULL,
                   updated_at = %s,
                   completed_at = %s
             WHERE tenant_id = %s
               AND operation_id = %s
               AND status = 'IN_PROGRESS'
               AND attempt_count = %s
            RETURNING {OPERATION_COLUMNS}
            """,
            (
                result_ref,
                result_digest,
                now,
                now,
                tenant_id,
                operation_id,
                expected_attempt_count,
            ),
        )
        if row is not None:
            return operation_from_row(row)
        existing = self.get_operation(transaction, tenant_id, operation_id)
        if (
            existing is not None
            and existing.status is OperationStatus.COMPLETED
            and existing.result_digest == result_digest
            and existing.result_ref == result_ref
        ):
            return existing
        raise OperationStateConflictError(
            "operation completion compare-and-set failed",
            attempt=expected_attempt_count,
            sanitized_code="STALE_OPERATION_STATE",
        )

    def mark_operation_interrupted(
        self,
        transaction: TransactionProtocol,
        *,
        tenant_id: str,
        operation_id: str,
        expected_attempt_count: int,
        last_sqlstate: str | None = None,
        sanitized_error_code: str | None = None,
    ) -> PersistenceOperation:
        return self._mark_non_success(
            transaction,
            tenant_id=tenant_id,
            operation_id=operation_id,
            expected_attempt_count=expected_attempt_count,
            target=OperationStatus.INTERRUPTED,
            last_sqlstate=last_sqlstate,
            sanitized_error_code=sanitized_error_code,
        )

    def mark_operation_failed_final(
        self,
        transaction: TransactionProtocol,
        *,
        tenant_id: str,
        operation_id: str,
        expected_attempt_count: int,
        last_sqlstate: str | None = None,
        sanitized_error_code: str | None = None,
    ) -> PersistenceOperation:
        return self._mark_non_success(
            transaction,
            tenant_id=tenant_id,
            operation_id=operation_id,
            expected_attempt_count=expected_attempt_count,
            target=OperationStatus.FAILED_FINAL,
            last_sqlstate=last_sqlstate,
            sanitized_error_code=sanitized_error_code,
        )

    def get_operation(
        self,
        transaction: TransactionProtocol,
        tenant_id: str,
        operation_id: str,
    ) -> PersistenceOperation | None:
        row = transaction.fetch_one(
            f"""
            SELECT {OPERATION_COLUMNS}
              FROM memory_patch.persistence_operations
             WHERE tenant_id = %s
               AND operation_id = %s
            """,
            (tenant_id, operation_id),
        )
        return None if row is None else operation_from_row(row)

    def _find_by_key(
        self,
        transaction: TransactionProtocol,
        request: BeginOperation,
    ) -> PersistenceOperation | None:
        if request.owner_user_id is None:
            sql = f"""
                SELECT {OPERATION_COLUMNS}
                  FROM memory_patch.persistence_operations
                 WHERE tenant_id = %s
                   AND owner_user_id IS NULL
                   AND operation_kind = %s
                   AND idempotency_key = %s
            """
            parameters: tuple[object, ...] = (
                request.tenant_id,
                request.operation_kind,
                request.idempotency_key,
            )
        else:
            sql = f"""
                SELECT {OPERATION_COLUMNS}
                  FROM memory_patch.persistence_operations
                 WHERE tenant_id = %s
                   AND owner_user_id = %s
                   AND operation_kind = %s
                   AND idempotency_key = %s
            """
            parameters = (
                request.tenant_id,
                request.owner_user_id,
                request.operation_kind,
                request.idempotency_key,
            )
        row = transaction.fetch_one(sql, parameters)
        return None if row is None else operation_from_row(row)

    @staticmethod
    def _find_by_external_identity(
        transaction: TransactionProtocol,
        tenant_id: str,
        external: ExternalReferenceIdentity,
    ) -> PersistenceOperation | None:
        row = transaction.fetch_one(
            f"""
            SELECT {OPERATION_COLUMNS}
              FROM memory_patch.persistence_operations
             WHERE tenant_id = %s
               AND origin_kind = %s
               AND origin_system = %s
               AND origin_version = %s
               AND adapter_version = %s
               AND artifact_kind = %s
               AND external_ref = %s
            """,
            (
                tenant_id,
                external.origin_kind,
                external.origin_system,
                external.origin_version,
                external.adapter_version,
                external.artifact_kind,
                external.external_ref,
            ),
        )
        return None if row is None else operation_from_row(row)

    def _claim_existing(
        self,
        transaction: TransactionProtocol,
        request: BeginOperation,
        existing: PersistenceOperation,
        *,
        binding_already_checked: bool = False,
    ) -> OperationClaim:
        if not binding_already_checked:
            self._validate_binding(existing, request, source="idempotency key")
        if existing.status in {
            OperationStatus.COMPLETED,
            OperationStatus.IN_PROGRESS,
        }:
            return OperationClaim(existing, may_proceed=False, resumed=False)
        if existing.status is OperationStatus.FAILED_FINAL:
            raise OperationStateConflictError(
                "failed-final operation requires a new idempotency key",
                operation_kind=request.operation_kind,
                sanitized_code="FAILED_FINAL_NOT_RESUMABLE",
            )
        now = self._clock()
        row = transaction.fetch_one(
            f"""
            UPDATE memory_patch.persistence_operations
               SET status = 'IN_PROGRESS',
                   attempt_count = attempt_count + 1,
                   last_sqlstate = NULL,
                   sanitized_error_code = NULL,
                   updated_at = %s
             WHERE tenant_id = %s
               AND operation_id = %s
               AND status = %s
               AND attempt_count = %s
            RETURNING {OPERATION_COLUMNS}
            """,
            (
                now,
                existing.tenant_id,
                existing.operation_id,
                existing.status.value,
                existing.attempt_count,
            ),
        )
        if row is None:
            raise OperationStateConflictError(
                "operation resume compare-and-set failed",
                attempt=existing.attempt_count,
                operation_kind=request.operation_kind,
                sanitized_code="STALE_OPERATION_STATE",
            )
        return OperationClaim(
            operation=operation_from_row(row),
            may_proceed=True,
            resumed=True,
        )

    @staticmethod
    def _validate_binding(
        existing: PersistenceOperation,
        request: BeginOperation,
        *,
        source: str,
    ) -> None:
        if (
            existing.tenant_id != request.tenant_id
            or existing.owner_user_id != request.owner_user_id
            or existing.operation_kind != request.operation_kind
            or existing.request_digest != request.request_digest
            or existing.scope_digest != request.scope_digest
            or existing.external_identity != request.external_identity
        ):
            raise IdempotencyConflictError(
                f"{source} is bound to different canonical inputs",
                operation_kind=request.operation_kind,
                sanitized_code="IDEMPOTENCY_BINDING_CONFLICT",
            )

    def _mark_non_success(
        self,
        transaction: TransactionProtocol,
        *,
        tenant_id: str,
        operation_id: str,
        expected_attempt_count: int,
        target: OperationStatus,
        last_sqlstate: str | None,
        sanitized_error_code: str | None,
    ) -> PersistenceOperation:
        if expected_attempt_count < 1:
            raise PersistenceConfigurationError(
                "expected attempt count must be positive",
                sanitized_code="INVALID_ATTEMPT_COUNT",
            )
        if last_sqlstate is not None and not _SQLSTATE.fullmatch(last_sqlstate):
            raise PersistenceConfigurationError(
                "last_sqlstate is invalid",
                sanitized_code="INVALID_SQLSTATE",
            )
        if sanitized_error_code is not None and not _ERROR_CODE.fullmatch(
            sanitized_error_code
        ):
            raise PersistenceConfigurationError(
                "sanitized error code is invalid",
                sanitized_code="INVALID_ERROR_CODE",
            )
        row = transaction.fetch_one(
            f"""
            UPDATE memory_patch.persistence_operations
               SET status = %s,
                   last_sqlstate = %s,
                   sanitized_error_code = %s,
                   updated_at = %s
             WHERE tenant_id = %s
               AND operation_id = %s
               AND status = 'IN_PROGRESS'
               AND attempt_count = %s
            RETURNING {OPERATION_COLUMNS}
            """,
            (
                target.value,
                last_sqlstate,
                sanitized_error_code,
                self._clock(),
                tenant_id,
                operation_id,
                expected_attempt_count,
            ),
        )
        if row is None:
            raise OperationStateConflictError(
                "operation failure-state compare-and-set failed",
                attempt=expected_attempt_count,
                sanitized_code="STALE_OPERATION_STATE",
            )
        return operation_from_row(row)

    @staticmethod
    def _require_digest(value: str, field: str) -> None:
        if not isinstance(value, str) or not _DIGEST.fullmatch(value):
            raise PersistenceConfigurationError(
                f"{field} must be a lowercase SHA-256 digest",
                sanitized_code="INVALID_DIGEST",
            )
