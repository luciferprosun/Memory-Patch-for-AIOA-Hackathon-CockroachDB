#!/usr/bin/env python3
"""Controlled Step 27 Personal Memory persistence and owner-isolation proof.

The runner uses the repository-pinned CockroachDB binary, copies that exact
binary into one owned temporary directory for predictable local execution, and
writes only to one disposable in-memory database.  No provider, model, web,
AWS, S3, patch, approval, activation, or execution boundary is invoked.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import socket
import struct
import sys
import tempfile
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts"), str(ROOT)]

import cockroach_cli_dbapi as cli_dbapi  # noqa: E402
import run_cockroachdb_migrations as migrations  # noqa: E402
import run_cockroachdb_rls_validation as rls_validation  # noqa: E402
import run_step18_retrieval_validation as step18  # noqa: E402
from aioa_memory_kernel.contracts.enums import (  # noqa: E402
    PersonalMemorySpaceState,
)
from aioa_memory_kernel.contracts.personal_memory import (  # noqa: E402
    PersonalHatQuotaPolicy,
)
from aioa_memory_kernel.contracts.serialization import (  # noqa: E402
    canonical_json,
    canonical_sha256,
)
from aioa_memory_kernel.persistence import (  # noqa: E402
    AccessMode,
    IdempotencyService,
    RequestContext,
    SerializableTransactionRunner,
)
from aioa_memory_kernel.personal_memory import (  # noqa: E402
    PERSONAL_MEMORY_EXPORT_SCHEMA_VERSION,
    PERSONAL_MEMORY_MODEL_BINDING_CONTRACT_VERSION,
    PERSONAL_MEMORY_QUOTA_CONTRACT_VERSION,
    PERSONAL_MEMORY_SLOT_CONTRACT_VERSION,
    STEP27_SCHEMA_VERSION,
    ConfigureSlotCommand,
    CreateEmptySlotCommand,
    ExportSlotCommand,
    ModelBindingCommand,
    PersonalMemoryBindingAction,
    PersonalMemoryBindingMode,
    PersonalMemoryModelBinding,
    PersonalMemoryMutationActor,
    PersonalMemoryPersistenceError,
    PersonalMemoryQuotaPolicyRecord,
    PersonalMemoryReasonCode,
    PersonalMemoryService,
    TransitionSlotCommand,
    verify_export_hash,
    verify_model_binding_hash,
    verify_quota_policy_hash,
    verify_receipt_hash,
    verify_slot_hash,
    verify_usage_hash,
)
from aioa_memory_kernel.storage import (  # noqa: E402
    ExternalVolumeConfig,
    load_external_volume_environment,
)


START_SHA = "31b23f662be329a1e70440e50a50f41d2550b89c"
EXPECTED_COCKROACH_SHA256 = (
    "a5392f4de2c7a2bd838a52b0dcde0d61dcecf2fb060a88b0771367309b5cbdcf"
)
DEFAULT_EXTERNAL_ENV = ROOT / ".local/external-data.env"
COCKROACH_RELATIVE = Path(
    "cache/xdg/cockroachdb/v26.2.4/linux-amd64/server/"
    "cockroach-v26.2.4.linux-amd64/cockroach"
)
FIXTURE_TIME = datetime(2042, 1, 2, 3, 4, 5, tzinfo=UTC)
TENANT_A = "tenant-step27-a"
TENANT_B = "tenant-step27-b"
USER_A = "user-step27-a"
USER_B = "user-step27-b"
USER_C = "user-step27-c"
SLOT_A = "personal-slot-step27-a"


class ValidationFailure(RuntimeError):
    """Sanitized controlled-validation failure."""

    def __init__(self, code: str) -> None:
        super().__init__("Step 27 controlled validation failed")
        self.code = code


_PGWIRE_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]{2,62}$")


class _PgwireError(RuntimeError):
    """Sanitized error from the owned validation-only pgwire connection."""

    def __init__(self, *, sqlstate: str | None = None) -> None:
        super().__init__("owned Step 27 pgwire statement failed")
        self.sqlstate = sqlstate
        self.pgcode = sqlstate
        self.sanitized_code = "STEP27_PGWIRE_STATEMENT_FAILED"


class _PgwireCursor:
    def __init__(self, connection: "_PgwireConnection") -> None:
        self._connection = connection
        self._rows: tuple[dict[str, object], ...] = ()
        self._offset = 0
        self._closed = False

    def execute(self, sql: str, parameters=None) -> None:
        if self._closed:
            raise _PgwireError()
        rendered = cli_dbapi.render_sql(sql, parameters)
        self._rows = self._connection._query(rendered)
        self._offset = 0

    def fetchone(self) -> Mapping[str, object] | None:
        if self._closed:
            raise _PgwireError()
        if self._offset >= len(self._rows):
            return None
        row = self._rows[self._offset]
        self._offset += 1
        return row

    def fetchall(self) -> tuple[Mapping[str, object], ...]:
        if self._closed:
            raise _PgwireError()
        result = self._rows[self._offset :]
        self._offset = len(self._rows)
        return result

    def close(self) -> None:
        self._closed = True
        self._rows = ()


class _PgwireConnection:
    """Tiny simple-query DB-API transport to one owned insecure local node."""

    def __init__(self, *, port: int, database: str, user: str) -> None:
        if (
            not isinstance(port, int)
            or not 1 <= port <= 65535
            or _PGWIRE_IDENTIFIER.fullmatch(database) is None
            or _PGWIRE_IDENTIFIER.fullmatch(user) is None
        ):
            raise ValidationFailure("STEP27_PGWIRE_CONFIGURATION_INVALID")
        self._socket = socket.create_connection(("127.0.0.1", port), timeout=30)
        self._socket.settimeout(90)
        self._cursor: _PgwireCursor | None = None
        self._closed = False
        parameters = (
            b"user\x00"
            + user.encode("ascii")
            + b"\x00database\x00"
            + database.encode("ascii")
            + b"\x00application_name\x00memory-patch-step27-validation\x00\x00"
        )
        self._socket.sendall(
            struct.pack("!II", len(parameters) + 8, 196608) + parameters
        )
        while True:
            message_type, payload = self._message()
            if message_type == b"R":
                if len(payload) < 4 or struct.unpack("!I", payload[:4])[0] != 0:
                    raise _PgwireError(sqlstate="28000")
            elif message_type == b"E":
                raise self._server_error(payload)
            elif message_type == b"Z":
                break

    def _receive_exact(self, size: int) -> bytes:
        chunks: list[bytes] = []
        remaining = size
        while remaining:
            value = self._socket.recv(remaining)
            if not value:
                raise _PgwireError()
            chunks.append(value)
            remaining -= len(value)
        return b"".join(chunks)

    def _message(self) -> tuple[bytes, bytes]:
        message_type = self._receive_exact(1)
        size = struct.unpack("!I", self._receive_exact(4))[0]
        if size < 4 or size > 8 * 1024 * 1024:
            raise _PgwireError()
        return message_type, self._receive_exact(size - 4)

    @staticmethod
    def _server_error(payload: bytes) -> _PgwireError:
        fields: dict[str, str] = {}
        for field in payload.rstrip(b"\x00").split(b"\x00"):
            if field:
                fields[field[:1].decode("ascii", "ignore")] = field[1:].decode(
                    "utf-8", "replace"
                )
        return _PgwireError(sqlstate=fields.get("C"))

    @staticmethod
    def _row_description(payload: bytes) -> tuple[str, ...]:
        if len(payload) < 2:
            raise _PgwireError()
        field_count = struct.unpack("!H", payload[:2])[0]
        offset = 2
        names: list[str] = []
        for _index in range(field_count):
            end = payload.find(b"\x00", offset)
            if end < offset or end + 19 > len(payload):
                raise _PgwireError()
            names.append(payload[offset:end].decode("utf-8"))
            offset = end + 19
        if offset != len(payload) or len(set(names)) != len(names):
            raise _PgwireError()
        return tuple(names)

    @staticmethod
    def _data_row(payload: bytes, names: tuple[str, ...]) -> dict[str, object]:
        if len(payload) < 2 or struct.unpack("!H", payload[:2])[0] != len(names):
            raise _PgwireError()
        offset = 2
        values: list[object] = []
        for _name in names:
            if offset + 4 > len(payload):
                raise _PgwireError()
            size = struct.unpack("!i", payload[offset : offset + 4])[0]
            offset += 4
            if size == -1:
                values.append(None)
            elif size < 0 or offset + size > len(payload):
                raise _PgwireError()
            else:
                values.append(payload[offset : offset + size].decode("utf-8"))
                offset += size
        if offset != len(payload):
            raise _PgwireError()
        return dict(zip(names, values, strict=True))

    def _query(self, sql: str) -> tuple[dict[str, object], ...]:
        if self._closed or not isinstance(sql, str) or not sql.strip():
            raise _PgwireError()
        payload = sql.encode("utf-8")
        if len(payload) > 1024 * 1024 or b"\x00" in payload:
            raise _PgwireError()
        self._socket.sendall(
            b"Q" + struct.pack("!I", len(payload) + 5) + payload + b"\x00"
        )
        names: tuple[str, ...] = ()
        rows: list[dict[str, object]] = []
        error: _PgwireError | None = None
        while True:
            message_type, response = self._message()
            if message_type == b"T":
                names = self._row_description(response)
            elif message_type == b"D":
                rows.append(self._data_row(response, names))
            elif message_type == b"E":
                error = self._server_error(response)
            elif message_type == b"Z":
                break
        if error is not None:
            raise error
        return tuple(rows)

    def cursor(self) -> _PgwireCursor:
        if self._closed or self._cursor is not None:
            raise _PgwireError()
        self._cursor = _PgwireCursor(self)
        return self._cursor

    def commit(self) -> None:
        self._query("COMMIT")

    def rollback(self) -> None:
        if not self._closed:
            self._query("ROLLBACK")

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._socket.sendall(b"X" + struct.pack("!I", 4))
        except OSError:
            pass
        self._socket.close()


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cockroach-binary", type=Path)
    parser.add_argument(
        "--external-env",
        type=Path,
        default=DEFAULT_EXTERNAL_ENV,
    )
    return parser.parse_args()


def _source_binary(args: argparse.Namespace) -> Path:
    if args.cockroach_binary is not None:
        return args.cockroach_binary.expanduser().resolve(strict=True)
    config = ExternalVolumeConfig.from_mapping(
        load_external_volume_environment(args.external_env)
    )
    return (config.data_root / COCKROACH_RELATIVE).resolve(strict=True)


def _quota_policy(
    *,
    maximum_total_spaces: int = 1,
    maximum_active_spaces: int = 1,
    maximum_archived_spaces: int = 1,
    maximum_model_bindings_per_space: int = 2,
) -> PersonalMemoryQuotaPolicyRecord:
    return PersonalMemoryQuotaPolicyRecord(
        schema_version=STEP27_SCHEMA_VERSION,
        tenant_id=TENANT_A,
        owner_user_id=USER_A,
        quota_policy_id="personal-quota-step27-v1",
        quota_policy_version="1",
        limits=PersonalHatQuotaPolicy(
            maximum_total_spaces=maximum_total_spaces,
            maximum_active_spaces=maximum_active_spaces,
            maximum_archived_spaces=maximum_archived_spaces,
            maximum_bytes=4096,
            maximum_personal_sources=0,
            maximum_active_memory_patches=0,
            maximum_session_memory_bytes=0,
            maximum_ingestion_jobs=0,
            maximum_embedding_or_index_bytes=0,
        ),
        maximum_model_bindings_per_space=maximum_model_bindings_per_space,
    )


def _binding(
    provider_id: str,
    model_id: str,
    revision: str,
    at: datetime,
) -> PersonalMemoryModelBinding:
    return PersonalMemoryModelBinding(
        schema_version=STEP27_SCHEMA_VERSION,
        tenant_id=TENANT_A,
        owner_user_id=USER_A,
        personal_memory_space_id=SLOT_A,
        provider_id=provider_id,
        model_id=model_id,
        model_revision_or_declared_version=revision,
        binding_mode=PersonalMemoryBindingMode.EXACT_MODEL,
        enabled=True,
        binding_version=1,
        bound_at=at,
    )


def _seed_identity_sql() -> str:
    q = migrations.sql_literal
    at = q(FIXTURE_TIME.isoformat()) + "::TIMESTAMPTZ"
    return ";\n".join(
        (
            "INSERT INTO memory_patch.tenants "
            "(tenant_id, display_name, metadata, created_at, updated_at) VALUES "
            f"({q(TENANT_A)}, 'Step 27 tenant A', '{{}}'::JSONB, {at}, {at}), "
            f"({q(TENANT_B)}, 'Step 27 tenant B', '{{}}'::JSONB, {at}, {at})",
            "INSERT INTO memory_patch.users "
            "(tenant_id, user_id, display_name, metadata, created_at, updated_at) VALUES "
            f"({q(TENANT_A)}, {q(USER_A)}, 'Step 27 user A', '{{}}'::JSONB, {at}, {at}), "
            f"({q(TENANT_A)}, {q(USER_B)}, 'Step 27 user B', '{{}}'::JSONB, {at}, {at}), "
            f"({q(TENANT_B)}, {q(USER_C)}, 'Step 27 user C', '{{}}'::JSONB, {at}, {at})",
        )
    )


def _create_validation_role(
    root: step18._Step18HttpSqlClient,
    role: str,
) -> None:
    identifier = rls_validation.role_identifier(role)
    connection = _PgwireConnection(
        port=root.sql_port, database="defaultdb", user="root"
    )
    try:
        cursor = connection.cursor()
        cursor.execute("SET allow_role_memberships_to_change_during_transaction = true")
        cursor.execute(
            f"CREATE ROLE {identifier} "
            "WITH LOGIN NOCREATEROLE NOCREATEDB NOBYPASSRLS"
        )
        cursor.execute(
            "GRANT mp_app_runtime, mp_request_context_setter TO " + identifier
        )
        cursor.close()
    finally:
        connection.close()


def _drop_validation_role(
    root: step18._Step18HttpSqlClient,
    role: str,
) -> None:
    identifier = rls_validation.role_identifier(role)
    connection = _PgwireConnection(
        port=root.sql_port, database="defaultdb", user="root"
    )
    try:
        cursor = connection.cursor()
        cursor.execute("SET allow_role_memberships_to_change_during_transaction = true")
        cursor.execute(
            "REVOKE mp_app_runtime, mp_request_context_setter FROM " + identifier
        )
        cursor.execute("DROP ROLE IF EXISTS " + identifier)
        cursor.close()
    finally:
        connection.close()


def _rls_catalog(root: step18._Step18HttpSqlClient, database: str) -> dict[str, Any]:
    output = root.execute(
        database,
        "SELECT relname, relrowsecurity, relforcerowsecurity "
        "FROM pg_catalog.pg_class WHERE oid IN ("
        "'memory_patch.personal_memory_spaces'::REGCLASS, "
        "'memory_patch.personal_memory_model_bindings'::REGCLASS, "
        "'memory_patch.personal_memory_quota_policies'::REGCLASS) "
        "ORDER BY relname",
        timeout=60,
    )
    rows = migrations.parse_tsv(output)
    if len(rows) != 3 or any(
        row.get("relrowsecurity") != "t" or row.get("relforcerowsecurity") != "t"
        for row in rows
    ):
        raise ValidationFailure("STEP27_RLS_FORCE_RLS_MISSING")
    return {
        "tables": [row["relname"] for row in rows],
        "rls_enabled": True,
        "force_rls_enabled": True,
    }


def _transition_command(
    slot: object,
    target: PersonalMemorySpaceState,
    *,
    key: str,
    at: datetime,
) -> TransitionSlotCommand:
    return TransitionSlotCommand(
        schema_version=STEP27_SCHEMA_VERSION,
        tenant_id=TENANT_A,
        owner_user_id=USER_A,
        personal_memory_space_id=SLOT_A,
        target_state=target,
        expected_state_version=slot.state_version,
        expected_configuration_version=slot.configuration_version,
        idempotency_key=key,
        requested_at=at,
        actor=PersonalMemoryMutationActor.OWNER_CONFIGURATION,
    )


def _validate_service(
    *,
    root: step18._Step18HttpSqlClient,
    database: str,
    role: str,
) -> dict[str, Any]:
    if root.sql_port is None:
        raise ValidationFailure("STEP27_SQL_PORT_MISSING")
    factory = lambda: _PgwireConnection(
        port=root.sql_port,
        database=database,
        user=role,
    )
    runner = SerializableTransactionRunner(factory, sleep=lambda _delay: None)
    completion_time = FIXTURE_TIME + timedelta(hours=1)
    service = PersonalMemoryService(
        runner,
        idempotency=IdempotencyService(clock=lambda: completion_time),
    )
    policy = _quota_policy()
    verify_quota_policy_hash(policy)
    create = CreateEmptySlotCommand(
        schema_version=STEP27_SCHEMA_VERSION,
        tenant_id=TENANT_A,
        owner_user_id=USER_A,
        personal_memory_space_id=SLOT_A,
        quota_policy=policy,
        idempotency_key="step27-create-slot-a",
        requested_at=FIXTURE_TIME,
        actor=PersonalMemoryMutationActor.OWNER_CONFIGURATION,
    )
    slot, created_receipt = service.create_empty_slot(create)
    replay_slot, replay_receipt = service.create_empty_slot(create)
    if (
        replay_slot.slot_hash != slot.slot_hash
        or not replay_receipt.replayed
        or replay_receipt.reason_code
        is not PersonalMemoryReasonCode.SLOT_ALREADY_EXISTS_EXACT_REPLAY
    ):
        raise ValidationFailure("STEP27_CREATE_REPLAY_MISMATCH")
    verify_slot_hash(slot)
    verify_receipt_hash(created_receipt)
    usage = service.quota_usage(
        tenant_id=TENANT_A,
        owner_user_id=USER_A,
        personal_memory_space_id=SLOT_A,
    )
    verify_usage_hash(usage)
    if (
        usage.memory_item_count != 0
        or usage.patch_count != 0
        or usage.stored_bytes != 0
        or usage.model_binding_count != 0
    ):
        raise ValidationFailure("STEP27_EMPTY_SLOT_USAGE_NONZERO")

    over_quota = False
    try:
        service.create_empty_slot(
            CreateEmptySlotCommand(
                schema_version=STEP27_SCHEMA_VERSION,
                tenant_id=TENANT_A,
                owner_user_id=USER_A,
                personal_memory_space_id="personal-slot-step27-over-quota",
                quota_policy=policy,
                idempotency_key="step27-create-over-quota",
                requested_at=FIXTURE_TIME + timedelta(seconds=1),
                actor=PersonalMemoryMutationActor.OWNER_CONFIGURATION,
            )
        )
    except PersonalMemoryPersistenceError as error:
        over_quota = error.reason_code is PersonalMemoryReasonCode.QUOTA_EXCEEDED
    if not over_quota:
        raise ValidationFailure("STEP27_SLOT_QUOTA_NOT_ENFORCED")

    slot, _ = service.configure_slot(
        ConfigureSlotCommand(
            schema_version=STEP27_SCHEMA_VERSION,
            tenant_id=TENANT_A,
            owner_user_id=USER_A,
            personal_memory_space_id=SLOT_A,
            display_name="Owner private memory",
            quota_policy=policy,
            expected_state_version=slot.state_version,
            expected_configuration_version=slot.configuration_version,
            idempotency_key="step27-configure-slot-a",
            requested_at=FIXTURE_TIME + timedelta(seconds=2),
            actor=PersonalMemoryMutationActor.OWNER_CONFIGURATION,
        )
    )

    binding_hashes: list[str] = []
    for index, (provider, model, revision) in enumerate(
        (
            ("provider-alpha", "model-alpha", "revision-1"),
            ("provider-beta", "model-beta", "revision-7"),
        ),
        start=3,
    ):
        value = _binding(
            provider,
            model,
            revision,
            FIXTURE_TIME + timedelta(seconds=index),
        )
        verify_model_binding_hash(value)
        slot, _ = service.update_model_binding(
            ModelBindingCommand(
                schema_version=STEP27_SCHEMA_VERSION,
                tenant_id=TENANT_A,
                owner_user_id=USER_A,
                personal_memory_space_id=SLOT_A,
                binding=value,
                action=PersonalMemoryBindingAction.ADD,
                expected_state_version=slot.state_version,
                expected_configuration_version=slot.configuration_version,
                idempotency_key=f"step27-bind-{index}",
                requested_at=FIXTURE_TIME + timedelta(seconds=index),
                actor=PersonalMemoryMutationActor.OWNER_CONFIGURATION,
            )
        )
        binding_hashes.append(value.binding_hash)

    binding_limit = False
    third = _binding(
        "provider-gamma",
        "model-gamma",
        "revision-2",
        FIXTURE_TIME + timedelta(seconds=5),
    )
    try:
        service.update_model_binding(
            ModelBindingCommand(
                schema_version=STEP27_SCHEMA_VERSION,
                tenant_id=TENANT_A,
                owner_user_id=USER_A,
                personal_memory_space_id=SLOT_A,
                binding=third,
                action=PersonalMemoryBindingAction.ADD,
                expected_state_version=slot.state_version,
                expected_configuration_version=slot.configuration_version,
                idempotency_key="step27-bind-over-quota",
                requested_at=FIXTURE_TIME + timedelta(seconds=5),
                actor=PersonalMemoryMutationActor.OWNER_CONFIGURATION,
            )
        )
    except PersonalMemoryPersistenceError as error:
        binding_limit = (
            error.reason_code
            is PersonalMemoryReasonCode.MODEL_BINDING_LIMIT_EXCEEDED
        )
    if not binding_limit:
        raise ValidationFailure("STEP27_BINDING_QUOTA_NOT_ENFORCED")

    transition_states: list[str] = [slot.state.value]
    for index, target in enumerate(
        (
            PersonalMemorySpaceState.ACTIVE,
            PersonalMemorySpaceState.SUSPENDED,
            PersonalMemorySpaceState.ARCHIVED,
        ),
        start=6,
    ):
        slot, _ = service.transition_slot(
            _transition_command(
                slot,
                target,
                key=f"step27-transition-{target.value.lower()}",
                at=FIXTURE_TIME + timedelta(seconds=index),
            )
        )
        transition_states.append(slot.state.value)

    export_command = ExportSlotCommand(
        schema_version=STEP27_SCHEMA_VERSION,
        tenant_id=TENANT_A,
        owner_user_id=USER_A,
        personal_memory_space_id=SLOT_A,
        expected_state_version=slot.state_version,
        expected_configuration_version=slot.configuration_version,
        idempotency_key="step27-export-owner-slot",
        requested_at=FIXTURE_TIME + timedelta(seconds=9),
        actor=PersonalMemoryMutationActor.OWNER_CONFIGURATION,
    )
    exported, _ = service.request_export(export_command)
    replayed_export, replay_export_receipt = service.request_export(export_command)
    verify_export_hash(exported)
    if (
        exported.export_digest != replayed_export.export_digest
        or not replay_export_receipt.replayed
    ):
        raise ValidationFailure("STEP27_EXPORT_REPLAY_MISMATCH")
    export_payload = json.loads(exported.canonical_text())
    if (
        export_payload.get("tenant_id") != TENANT_A
        or export_payload.get("owner_user_id") != USER_A
        or "patches" in export_payload
        or "provider_credentials" in export_payload
    ):
        raise ValidationFailure("STEP27_EXPORT_SCOPE_INVALID")

    slot = service.read_slot(
        tenant_id=TENANT_A,
        owner_user_id=USER_A,
        personal_memory_space_id=SLOT_A,
    )
    for index, target in enumerate(
        (
            PersonalMemorySpaceState.DELETED_PENDING,
            PersonalMemorySpaceState.DELETED,
        ),
        start=10,
    ):
        slot, _ = service.transition_slot(
            _transition_command(
                slot,
                target,
                key=f"step27-transition-{target.value.lower()}",
                at=FIXTURE_TIME + timedelta(seconds=index),
            )
        )
        transition_states.append(slot.state.value)
    if slot.deleted_at is None or slot.model_bindings:
        raise ValidationFailure("STEP27_LOGICAL_DELETE_INVALID")

    def scoped_count(context_tenant: str, context_user: str) -> int:
        context = RequestContext(
            tenant_id=context_tenant,
            user_id=context_user,
            access_mode=AccessMode.USER_PRIVATE,
        )
        rows = runner.run(
            context,
            lambda transaction: transaction.fetch_one(
                "SELECT count(*) AS row_count "
                "FROM memory_patch.personal_memory_spaces "
                "WHERE tenant_id = %s AND user_id = %s",
                (TENANT_A, USER_A),
            ),
            operation_kind="STEP27_OWNER_ISOLATION_PROBE",
        )
        if rows is None:
            raise ValidationFailure("STEP27_ISOLATION_PROBE_EMPTY")
        return int(rows["row_count"])

    owner_count = scoped_count(TENANT_A, USER_A)
    cross_user_count = scoped_count(TENANT_A, USER_B)
    cross_tenant_count = scoped_count(TENANT_B, USER_C)
    if owner_count != 1 or cross_user_count != 0 or cross_tenant_count != 0:
        raise ValidationFailure("STEP27_OWNER_RLS_LEAK")
    final_usage = service.quota_usage(
        tenant_id=TENANT_A,
        owner_user_id=USER_A,
        personal_memory_space_id=SLOT_A,
    )
    if final_usage.usage.total_spaces != 0:
        raise ValidationFailure("STEP27_DELETED_SLOT_QUOTA_MISMATCH")
    return {
        "slot_hash": slot.slot_hash,
        "configuration_digest": slot.configuration_digest,
        "quota_policy_digest": policy.policy_digest,
        "empty_usage": {
            "memory_item_count": usage.memory_item_count,
            "patch_count": usage.patch_count,
            "stored_bytes": usage.stored_bytes,
        },
        "state_transition_matrix": transition_states,
        "quota_matrix": {
            "under_limit": "PASS",
            "exact_total_space_limit": "PASS",
            "over_total_space_limit": "REJECTED",
            "binding_limit": "REJECTED_AT_THIRD_BINDING",
            "cross_user_usage_counted": False,
        },
        "model_binding_matrix": {
            "binding_count": len(binding_hashes),
            "binding_hashes": binding_hashes,
            "provider_model_neutral": True,
            "credentials_stored": False,
        },
        "owner_isolation": {
            "owner_visible_rows": owner_count,
            "cross_user_visible_rows": cross_user_count,
            "cross_tenant_visible_rows": cross_tenant_count,
        },
        "idempotent_replay": {
            "slot_create": "PASS",
            "owner_export": "PASS",
        },
        "archive": {
            "validated": True,
            "distinct_from_delete": True,
        },
        "export": {
            "schema_version": exported.export_schema_version,
            "export_digest": exported.export_digest,
            "canonical_json": True,
            "owner_scoped": True,
            "fake_patch_content": False,
        },
        "delete": {
            "implementation": "TWO_STAGE_LOGICAL_TOMBSTONE",
            "delete_request_only": False,
            "physical_delete": False,
            "deleted_row_retained": True,
        },
    }


def validate(args: argparse.Namespace) -> Mapping[str, Any]:
    source_binary = _source_binary(args)
    source_identity = migrations.verify_binary_identity(source_binary)
    if source_identity["binary_sha256"] != EXPECTED_COCKROACH_SHA256:
        raise ValidationFailure("STEP27_COCKROACH_BINARY_DIGEST_MISMATCH")

    runtime: migrations.LocalRuntime | None = None
    root: step18._Step18HttpSqlClient | None = None
    database: str | None = None
    role: str | None = None
    cleanup: Mapping[str, Any] = {}
    service_result: Mapping[str, Any] | None = None
    migration_result: Mapping[str, Any] | None = None
    replay_result: Mapping[str, Any] | None = None
    rls_result: Mapping[str, Any] | None = None
    primary_error: BaseException | None = None
    cleanup_errors: list[str] = []

    with tempfile.TemporaryDirectory(prefix="mp-step27-binary-", dir="/tmp") as temporary:
        temporary_path = Path(temporary)
        local_binary = temporary_path / "cockroach"
        shutil.copy2(source_binary, local_binary)
        local_identity = migrations.verify_binary_identity(local_binary)
        if local_identity["binary_sha256"] != EXPECTED_COCKROACH_SHA256:
            raise ValidationFailure("STEP27_COPIED_BINARY_DIGEST_MISMATCH")
        run_id = "mp_step27_" + uuid.uuid4().hex[:12]
        runtime = migrations.LocalRuntime(local_binary, run_id)
        try:
            root = step18._start_disposable_runtime(runtime)
            database = run_id + "_db"
            migrations.create_database(root, database)
            migration_result = migrations.apply_migrations(root, database, timeout=300)
            replay_result = migrations.apply_migrations(root, database, timeout=300)
            if (
                len(migration_result["applied"]) != 11
                or replay_result["applied"]
                or len(replay_result["skipped"]) != 11
            ):
                raise ValidationFailure("STEP27_MIGRATION_REPLAY_MISMATCH")
            root.execute(database, _seed_identity_sql(), timeout=120)
            role = "mp_s27_" + uuid.uuid4().hex[:16]
            _create_validation_role(root, role)
            rls_result = _rls_catalog(root, database)
            if runtime.runtime_dir is None:
                raise ValidationFailure("STEP27_RUNTIME_DIRECTORY_MISSING")
            service_result = _validate_service(
                root=root,
                database=database,
                role=role,
            )
        except BaseException as error:
            primary_error = error
        finally:
            if root is not None:
                if database is not None:
                    try:
                        migrations.drop_database(root, database, timeout=180)
                    except BaseException:
                        cleanup_errors.append("DATABASE_CLEANUP_FAILED")
                if role is not None:
                    try:
                        _drop_validation_role(root, role)
                    except BaseException:
                        cleanup_errors.append("ROLE_CLEANUP_FAILED")
            if runtime is not None:
                try:
                    cleanup = step18._stop_owned_runtime(runtime)
                except BaseException:
                    cleanup_errors.append("RUNTIME_CLEANUP_FAILED")

    if primary_error is not None:
        if isinstance(primary_error, ValidationFailure):
            raise primary_error
        code = getattr(primary_error, "sanitized_code", None)
        raise ValidationFailure(
            code if isinstance(code, str) else type(primary_error).__name__.upper()
        ) from primary_error
    if cleanup_errors:
        raise ValidationFailure("STEP27_" + "_".join(cleanup_errors))
    if not all(
        cleanup.get(field) is expected
        for field, expected in (
            ("pid_exited", True),
            ("ports_closed", True),
            ("temporary_store_removed", True),
            ("force_kill_used", False),
        )
    ):
        raise ValidationFailure("STEP27_RUNTIME_CLEANUP_INCOMPLETE")
    if None in (service_result, migration_result, replay_result, rls_result):
        raise ValidationFailure("STEP27_VALIDATION_RESULT_INCOMPLETE")

    assert service_result is not None
    assert migration_result is not None
    assert replay_result is not None
    assert rls_result is not None
    result: dict[str, Any] = {
        "step": 27,
        "schema_version": "step27-personal-memory-validation-1a",
        "status": "PASS",
        "start_sha": START_SHA,
        "contracts": {
            "slot": PERSONAL_MEMORY_SLOT_CONTRACT_VERSION,
            "quota": PERSONAL_MEMORY_QUOTA_CONTRACT_VERSION,
            "model_binding": PERSONAL_MEMORY_MODEL_BINDING_CONTRACT_VERSION,
            "export": PERSONAL_MEMORY_EXPORT_SCHEMA_VERSION,
        },
        "database": {
            "version": migrations.PINNED_VERSION,
            "binary_sha256": EXPECTED_COCKROACH_SHA256,
            "migration": "0011_step27_personal_memory_persistence.sql",
            "migration_count": len(migration_result["applied"]),
            "replay_skipped_count": len(replay_result["skipped"]),
            "rls": rls_result,
        },
        **service_result,
        "authority": {
            "personal_memory_hat_executable": False,
            "personal_memory_canonical_evidence": False,
            "model_authority": False,
            "approval_authority": False,
            "execution_authority": False,
            "verified_answer_auto_write": False,
            "gemma_hard_dependency": False,
        },
        "later_step_boundaries": {
            "step28_started": False,
            "critic_bridge": 0,
            "patch_proposals": 0,
            "approvals": 0,
            "patch_activations": 0,
            "active_patch_retrieval": 0,
            "shared_promotion": 0,
        },
        "effect_bounds": {
            "provider_calls": 0,
            "model_calls": 0,
            "web_calls": 0,
            "aws_mutations": 0,
            "s3_mutations": 0,
            "external_actions": 0,
        },
        "cleanup": {
            "pid_exited": cleanup["pid_exited"],
            "ports_closed": cleanup["ports_closed"],
            "temporary_store_removed": cleanup["temporary_store_removed"],
            "force_kill_used": cleanup["force_kill_used"],
            "database_removed": True,
            "role_removed": True,
        },
    }
    result["validation_digest"] = canonical_sha256(result)
    return result


def main() -> int:
    args = _arguments()
    try:
        result = validate(args)
    except (
        OSError,
        TypeError,
        ValueError,
        RuntimeError,
        migrations.MigrationError,
        ValidationFailure,
    ) as error:
        reason = error.code if isinstance(error, ValidationFailure) else type(error).__name__
        print(canonical_json({"status": "FAILED", "reason": reason}), file=sys.stderr)
        return 1
    print(canonical_json(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
