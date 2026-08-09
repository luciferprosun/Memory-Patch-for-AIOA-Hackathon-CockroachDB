"""CockroachDB repository for owner-scoped Step 27 Personal Memory slots."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime

from aioa_memory_kernel.contracts.enums import PersonalMemorySpaceState
from aioa_memory_kernel.contracts.exceptions import IntegrityError
from aioa_memory_kernel.contracts.personal_memory import (
    PersonalHatQuotaPolicy,
    PersonalHatQuotaUsage,
)
from aioa_memory_kernel.persistence.errors import (
    ImmutableRecordConflictError,
    OperationStateConflictError,
    PersistenceConfigurationError,
)
from aioa_memory_kernel.persistence.protocols import TransactionProtocol

from .models import (
    STEP27_SCHEMA_VERSION,
    PersonalMemoryBindingMode,
    PersonalMemoryHatSlot,
    PersonalMemoryModelBinding,
    PersonalMemoryQuotaPolicyRecord,
    PersonalMemoryQuotaUsageView,
    personal_memory_hat_scope_id,
)


SLOT_COLUMNS = (
    "space.tenant_id, space.user_id, space.personal_memory_space_id, "
    "space.schema_version, space.state, space.display_name, "
    "space.created_at, space.updated_at, space.export_requested_at, "
    "space.deletion_requested_at, space.deleted_at, space.state_version, "
    "space.configuration_version, space.quota_policy_id, "
    "space.quota_policy_digest, space.configuration_digest, "
    "space.hat_scope_id, space.slot_hash"
)

BINDING_COLUMNS = (
    "tenant_id, user_id, personal_memory_space_id, model_binding_id, "
    "provider_id, model_id, model_revision, binding_mode, enabled, "
    "binding_version, binding_digest, bound_at"
)

QUOTA_COLUMNS = (
    "tenant_id, owner_user_id, quota_policy_id, schema_version, "
    "quota_policy_version, maximum_total_spaces, maximum_active_spaces, "
    "maximum_archived_spaces, maximum_bytes, maximum_personal_sources, "
    "maximum_active_memory_patches, maximum_session_memory_bytes, "
    "maximum_ingestion_jobs, maximum_embedding_or_index_bytes, "
    "maximum_model_bindings_per_space, policy_digest, created_at"
)


def _timestamp(value: object, field_name: str) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise PersistenceConfigurationError(
                f"{field_name} must be timezone-aware",
                sanitized_code="INVALID_PERSONAL_MEMORY_ROW",
            )
        return value.astimezone(UTC)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise PersistenceConfigurationError(
                f"{field_name} is not a timestamp",
                sanitized_code="INVALID_PERSONAL_MEMORY_ROW",
            ) from error
        if parsed.tzinfo is None:
            raise PersistenceConfigurationError(
                f"{field_name} must be timezone-aware",
                sanitized_code="INVALID_PERSONAL_MEMORY_ROW",
            )
        return parsed.astimezone(UTC)
    raise PersistenceConfigurationError(
        f"{field_name} is not a timestamp",
        sanitized_code="INVALID_PERSONAL_MEMORY_ROW",
    )


def _optional_timestamp(value: object, field_name: str) -> datetime | None:
    return None if value is None else _timestamp(value, field_name)


def _integer(value: object, field_name: str) -> int:
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as error:
        raise PersistenceConfigurationError(
            f"{field_name} is not an integer",
            sanitized_code="INVALID_PERSONAL_MEMORY_ROW",
        ) from error
    if parsed < 0:
        raise PersistenceConfigurationError(
            f"{field_name} is negative",
            sanitized_code="INVALID_PERSONAL_MEMORY_ROW",
        )
    return parsed


def _boolean(value: object, field_name: str) -> bool:
    if value in (True, "t", "true", "TRUE", "1", 1):
        return True
    if value in (False, "f", "false", "FALSE", "0", 0):
        return False
    raise PersistenceConfigurationError(
        f"{field_name} is not boolean",
        sanitized_code="INVALID_PERSONAL_MEMORY_ROW",
    )


def quota_policy_from_row(
    row: Mapping[str, object],
) -> PersonalMemoryQuotaPolicyRecord:
    try:
        record = PersonalMemoryQuotaPolicyRecord(
            schema_version=str(row["schema_version"]),
            tenant_id=str(row["tenant_id"]),
            owner_user_id=str(row["owner_user_id"]),
            quota_policy_id=str(row["quota_policy_id"]),
            quota_policy_version=str(row["quota_policy_version"]),
            limits=PersonalHatQuotaPolicy(
                maximum_total_spaces=_integer(
                    row["maximum_total_spaces"], "maximum_total_spaces"
                ),
                maximum_active_spaces=_integer(
                    row["maximum_active_spaces"], "maximum_active_spaces"
                ),
                maximum_archived_spaces=_integer(
                    row["maximum_archived_spaces"], "maximum_archived_spaces"
                ),
                maximum_bytes=_integer(row["maximum_bytes"], "maximum_bytes"),
                maximum_personal_sources=_integer(
                    row["maximum_personal_sources"], "maximum_personal_sources"
                ),
                maximum_active_memory_patches=_integer(
                    row["maximum_active_memory_patches"],
                    "maximum_active_memory_patches",
                ),
                maximum_session_memory_bytes=_integer(
                    row["maximum_session_memory_bytes"],
                    "maximum_session_memory_bytes",
                ),
                maximum_ingestion_jobs=_integer(
                    row["maximum_ingestion_jobs"], "maximum_ingestion_jobs"
                ),
                maximum_embedding_or_index_bytes=_integer(
                    row["maximum_embedding_or_index_bytes"],
                    "maximum_embedding_or_index_bytes",
                ),
            ),
            maximum_model_bindings_per_space=_integer(
                row["maximum_model_bindings_per_space"],
                "maximum_model_bindings_per_space",
            ),
        )
    except (KeyError, ValueError) as error:
        raise PersistenceConfigurationError(
            "database returned an invalid quota policy",
            sanitized_code="INVALID_PERSONAL_MEMORY_ROW",
        ) from error
    if record.policy_digest != row.get("policy_digest"):
        raise IntegrityError("persisted quota policy digest mismatch")
    return record


def model_binding_from_row(
    row: Mapping[str, object],
) -> PersonalMemoryModelBinding:
    try:
        record = PersonalMemoryModelBinding(
            schema_version=STEP27_SCHEMA_VERSION,
            tenant_id=str(row["tenant_id"]),
            owner_user_id=str(row["user_id"]),
            personal_memory_space_id=str(row["personal_memory_space_id"]),
            provider_id=str(row["provider_id"]),
            model_id=str(row["model_id"]),
            model_revision_or_declared_version=str(row["model_revision"]),
            binding_mode=PersonalMemoryBindingMode(str(row["binding_mode"])),
            enabled=_boolean(row["enabled"], "enabled"),
            binding_version=_integer(row["binding_version"], "binding_version"),
            bound_at=_timestamp(row["bound_at"], "bound_at"),
        )
    except (KeyError, ValueError) as error:
        raise PersistenceConfigurationError(
            "database returned an invalid model binding",
            sanitized_code="INVALID_PERSONAL_MEMORY_ROW",
        ) from error
    if record.binding_id != row.get("model_binding_id"):
        raise IntegrityError("persisted model binding identity mismatch")
    if record.binding_hash != row.get("binding_digest"):
        raise IntegrityError("persisted model binding digest mismatch")
    return record


def slot_from_row(
    row: Mapping[str, object],
    bindings: tuple[PersonalMemoryModelBinding, ...],
) -> PersonalMemoryHatSlot:
    try:
        record = PersonalMemoryHatSlot(
            schema_version=str(row["schema_version"]),
            tenant_id=str(row["tenant_id"]),
            owner_user_id=str(row["user_id"]),
            personal_memory_space_id=str(row["personal_memory_space_id"]),
            hat_scope_id=personal_memory_hat_scope_id(
                str(row["tenant_id"]),
                str(row["user_id"]),
                str(row["personal_memory_space_id"]),
            ),
            state=PersonalMemorySpaceState(str(row["state"])),
            display_name=(
                None if row.get("display_name") is None else str(row["display_name"])
            ),
            quota_policy_id=str(row["quota_policy_id"]),
            quota_policy_digest=str(row["quota_policy_digest"]),
            model_bindings=bindings,
            state_version=_integer(row["state_version"], "state_version"),
            configuration_version=_integer(
                row["configuration_version"], "configuration_version"
            ),
            created_at=_timestamp(row["created_at"], "created_at"),
            updated_at=_timestamp(row["updated_at"], "updated_at"),
            export_requested_at=_optional_timestamp(
                row.get("export_requested_at"), "export_requested_at"
            ),
            deletion_requested_at=_optional_timestamp(
                row.get("deletion_requested_at"), "deletion_requested_at"
            ),
            deleted_at=_optional_timestamp(row.get("deleted_at"), "deleted_at"),
        )
    except (KeyError, ValueError) as error:
        raise PersistenceConfigurationError(
            "database returned an invalid Personal Memory slot",
            sanitized_code="INVALID_PERSONAL_MEMORY_ROW",
        ) from error
    if record.configuration_digest != row.get("configuration_digest"):
        raise IntegrityError("persisted Personal Memory configuration digest mismatch")
    persisted_scope = row.get("hat_scope_id")
    persisted_slot_hash = row.get("slot_hash")
    if (persisted_scope is None) is not (persisted_slot_hash is None):
        raise IntegrityError("persisted Personal Memory authority tuple is partial")
    if persisted_scope is not None and persisted_scope != record.hat_scope_id:
        raise IntegrityError("persisted Personal Memory HAT scope mismatch")
    if persisted_slot_hash is not None and persisted_slot_hash != record.slot_hash:
        raise IntegrityError("persisted Personal Memory slot hash mismatch")
    return record


class PersonalMemoryCockroachRepository:
    """Explicit SQL operations; no generic field patching or patch-write API."""

    def get_quota_policy(
        self,
        transaction: TransactionProtocol,
        tenant_id: str,
        owner_user_id: str,
        quota_policy_id: str,
    ) -> PersonalMemoryQuotaPolicyRecord | None:
        row = transaction.fetch_one(
            f"""
            SELECT {QUOTA_COLUMNS}
              FROM memory_patch.personal_memory_quota_policies
             WHERE tenant_id = %s
               AND owner_user_id = %s
               AND quota_policy_id = %s
            """,
            (tenant_id, owner_user_id, quota_policy_id),
        )
        return None if row is None else quota_policy_from_row(row)

    def insert_quota_policy(
        self,
        transaction: TransactionProtocol,
        policy: PersonalMemoryQuotaPolicyRecord,
        created_at: datetime,
    ) -> PersonalMemoryQuotaPolicyRecord:
        limits = policy.limits
        row = transaction.fetch_one(
            f"""
            INSERT INTO memory_patch.personal_memory_quota_policies (
              tenant_id, owner_user_id, quota_policy_id, schema_version,
              quota_policy_version, maximum_total_spaces,
              maximum_active_spaces, maximum_archived_spaces, maximum_bytes,
              maximum_personal_sources, maximum_active_memory_patches,
              maximum_session_memory_bytes, maximum_ingestion_jobs,
              maximum_embedding_or_index_bytes,
              maximum_model_bindings_per_space, policy_digest, created_at
            ) VALUES (
              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
              %s, %s, %s, %s, %s, %s, %s
            )
            ON CONFLICT DO NOTHING
            RETURNING {QUOTA_COLUMNS}
            """,
            (
                policy.tenant_id,
                policy.owner_user_id,
                policy.quota_policy_id,
                policy.schema_version,
                policy.quota_policy_version,
                limits.maximum_total_spaces,
                limits.maximum_active_spaces,
                limits.maximum_archived_spaces,
                limits.maximum_bytes,
                limits.maximum_personal_sources,
                limits.maximum_active_memory_patches,
                limits.maximum_session_memory_bytes,
                limits.maximum_ingestion_jobs,
                limits.maximum_embedding_or_index_bytes,
                policy.maximum_model_bindings_per_space,
                policy.policy_digest,
                created_at,
            ),
        )
        if row is not None:
            return quota_policy_from_row(row)
        existing = self.get_quota_policy(
            transaction,
            policy.tenant_id,
            policy.owner_user_id,
            policy.quota_policy_id,
        )
        if existing is None or existing.policy_digest != policy.policy_digest:
            raise ImmutableRecordConflictError(
                "quota policy identity was reused with different limits",
                sanitized_code="PERSONAL_MEMORY_QUOTA_CONFLICT",
            )
        return existing

    def _bindings(
        self,
        transaction: TransactionProtocol,
        tenant_id: str,
        owner_user_id: str,
        personal_memory_space_id: str,
    ) -> tuple[PersonalMemoryModelBinding, ...]:
        rows = transaction.fetch_all(
            f"""
            SELECT {BINDING_COLUMNS}
              FROM memory_patch.personal_memory_model_bindings
             WHERE tenant_id = %s
               AND user_id = %s
               AND personal_memory_space_id = %s
             ORDER BY model_binding_id
            """,
            (tenant_id, owner_user_id, personal_memory_space_id),
        )
        return tuple(model_binding_from_row(row) for row in rows)

    def get_slot(
        self,
        transaction: TransactionProtocol,
        tenant_id: str,
        owner_user_id: str,
        personal_memory_space_id: str,
    ) -> PersonalMemoryHatSlot | None:
        row = transaction.fetch_one(
            f"""
            SELECT {SLOT_COLUMNS}
              FROM memory_patch.personal_memory_spaces AS space
             WHERE space.tenant_id = %s
               AND space.user_id = %s
               AND space.personal_memory_space_id = %s
            """,
            (tenant_id, owner_user_id, personal_memory_space_id),
        )
        if row is None:
            return None
        bindings = self._bindings(
            transaction, tenant_id, owner_user_id, personal_memory_space_id
        )
        return slot_from_row(row, bindings)

    def list_owner_slots(
        self,
        transaction: TransactionProtocol,
        tenant_id: str,
        owner_user_id: str,
    ) -> tuple[PersonalMemoryHatSlot, ...]:
        rows = transaction.fetch_all(
            f"""
            SELECT {SLOT_COLUMNS}
              FROM memory_patch.personal_memory_spaces AS space
             WHERE space.tenant_id = %s
               AND space.user_id = %s
             ORDER BY space.personal_memory_space_id
            """,
            (tenant_id, owner_user_id),
        )
        result = []
        for row in rows:
            space_id = str(row["personal_memory_space_id"])
            result.append(
                slot_from_row(
                    row,
                    self._bindings(
                        transaction, tenant_id, owner_user_id, space_id
                    ),
                )
            )
        return tuple(result)

    def create_empty_slot(
        self,
        transaction: TransactionProtocol,
        *,
        tenant_id: str,
        owner_user_id: str,
        personal_memory_space_id: str,
        quota_policy: PersonalMemoryQuotaPolicyRecord,
        created_at: datetime,
    ) -> PersonalMemoryHatSlot:
        slot = PersonalMemoryHatSlot(
            schema_version=STEP27_SCHEMA_VERSION,
            tenant_id=tenant_id,
            owner_user_id=owner_user_id,
            personal_memory_space_id=personal_memory_space_id,
            hat_scope_id=personal_memory_hat_scope_id(
                tenant_id,
                owner_user_id,
                personal_memory_space_id,
            ),
            state=PersonalMemorySpaceState.EMPTY,
            display_name=None,
            quota_policy_id=quota_policy.quota_policy_id,
            quota_policy_digest=quota_policy.policy_digest,
            model_bindings=(),
            state_version=0,
            configuration_version=0,
            created_at=created_at,
            updated_at=created_at,
        )
        row = transaction.fetch_one(
            """
            INSERT INTO memory_patch.personal_memory_spaces (
              tenant_id, user_id, personal_memory_space_id, schema_version,
              state, display_name, created_at, updated_at,
              export_requested_at, deletion_requested_at, deleted_at,
              state_version, configuration_version, quota_policy_id,
              quota_policy_digest, configuration_digest, hat_scope_id,
              slot_hash
            ) VALUES (
              %s, %s, %s, %s, 'EMPTY', NULL, %s, %s,
              NULL, NULL, NULL, 0, 0, %s, %s, %s, %s, %s
            )
            ON CONFLICT DO NOTHING
            RETURNING tenant_id
            """,
            (
                tenant_id,
                owner_user_id,
                personal_memory_space_id,
                STEP27_SCHEMA_VERSION,
                created_at,
                created_at,
                quota_policy.quota_policy_id,
                quota_policy.policy_digest,
                slot.configuration_digest,
                slot.hat_scope_id,
                slot.slot_hash,
            ),
        )
        if row is None:
            existing = self.get_slot(
                transaction, tenant_id, owner_user_id, personal_memory_space_id
            )
            if (
                existing is None
                or existing.quota_policy_id != quota_policy.quota_policy_id
                or existing.quota_policy_digest != quota_policy.policy_digest
            ):
                raise ImmutableRecordConflictError(
                    "Personal Memory slot identity already exists with different facts",
                    sanitized_code="PERSONAL_MEMORY_SLOT_CONFLICT",
                )
            return existing
        created = self.get_slot(
            transaction, tenant_id, owner_user_id, personal_memory_space_id
        )
        if created is None:
            raise PersistenceConfigurationError(
                "created Personal Memory slot is not readable",
                sanitized_code="PERSONAL_MEMORY_CREATE_INVISIBLE",
            )
        return created

    def owner_usage(
        self,
        transaction: TransactionProtocol,
        *,
        tenant_id: str,
        owner_user_id: str,
        personal_memory_space_id: str,
        quota_policy_digest: str,
    ) -> PersonalMemoryQuotaUsageView:
        space_row = transaction.fetch_one(
            """
            SELECT
              count(*) AS total_spaces,
              count(*) FILTER (WHERE state = 'ACTIVE') AS active_spaces,
              count(*) FILTER (WHERE state = 'ARCHIVED') AS archived_spaces
              FROM memory_patch.personal_memory_spaces
             WHERE tenant_id = %s AND user_id = %s AND state <> 'DELETED'
            """,
            (tenant_id, owner_user_id),
        )
        item_row = transaction.fetch_one(
            """
            SELECT
              count(item.memory_item_id) AS memory_item_count,
              coalesce(sum(octet_length(item.content::STRING)), 0) AS stored_bytes,
              count(item.memory_item_id) FILTER (
                WHERE item.trust_class = 'PERSONAL_VERIFIED_PATCH'
                  AND item.active = true
                  AND item.revoked = false
              ) AS active_memory_patches
              FROM memory_patch.hat_scopes AS scope
              LEFT JOIN memory_patch.memory_items AS item
                ON item.tenant_id = scope.tenant_id
               AND item.hat_scope_id = scope.hat_scope_id
               AND item.target_scope = scope.target_scope
             WHERE scope.tenant_id = %s
               AND scope.owner_user_id = %s
               AND scope.personal_memory_space_id = %s
               AND scope.target_scope = 'USER_PERSONAL_HAT'
            """,
            (tenant_id, owner_user_id, personal_memory_space_id),
        )
        binding_row = transaction.fetch_one(
            """
            SELECT count(*) AS binding_count
              FROM memory_patch.personal_memory_model_bindings
             WHERE tenant_id = %s
               AND user_id = %s
               AND personal_memory_space_id = %s
               AND enabled = true
            """,
            (tenant_id, owner_user_id, personal_memory_space_id),
        )
        if space_row is None or item_row is None or binding_row is None:
            raise PersistenceConfigurationError(
                "quota usage query returned no aggregate row",
                sanitized_code="PERSONAL_MEMORY_USAGE_UNAVAILABLE",
            )
        total_spaces = _integer(space_row["total_spaces"], "total_spaces")
        active_spaces = _integer(space_row["active_spaces"], "active_spaces")
        archived_spaces = _integer(
            space_row["archived_spaces"], "archived_spaces"
        )
        stored_bytes = _integer(item_row["stored_bytes"], "stored_bytes")
        memory_item_count = _integer(
            item_row["memory_item_count"], "memory_item_count"
        )
        active_patches = _integer(
            item_row["active_memory_patches"], "active_memory_patches"
        )
        return PersonalMemoryQuotaUsageView(
            schema_version=STEP27_SCHEMA_VERSION,
            tenant_id=tenant_id,
            owner_user_id=owner_user_id,
            personal_memory_space_id=personal_memory_space_id,
            quota_policy_digest=quota_policy_digest,
            usage=PersonalHatQuotaUsage(
                total_spaces=total_spaces,
                active_spaces=active_spaces,
                archived_spaces=archived_spaces,
                bytes_used=stored_bytes,
                personal_sources=0,
                active_memory_patches=active_patches,
                session_memory_bytes=0,
                ingestion_jobs=0,
                embedding_or_index_bytes=0,
            ),
            model_binding_count=_integer(
                binding_row["binding_count"], "binding_count"
            ),
            memory_item_count=memory_item_count,
            patch_count=active_patches,
            stored_bytes=stored_bytes,
        )

    def update_slot(
        self,
        transaction: TransactionProtocol,
        *,
        current: PersonalMemoryHatSlot,
        state: PersonalMemorySpaceState,
        display_name: str | None,
        quota_policy: PersonalMemoryQuotaPolicyRecord,
        model_bindings: tuple[PersonalMemoryModelBinding, ...],
        state_version: int,
        configuration_version: int,
        changed_at: datetime,
        export_requested_at: datetime | None,
        deletion_requested_at: datetime | None,
        deleted_at: datetime | None,
    ) -> PersonalMemoryHatSlot:
        updated_slot = PersonalMemoryHatSlot(
            schema_version=current.schema_version,
            tenant_id=current.tenant_id,
            owner_user_id=current.owner_user_id,
            personal_memory_space_id=current.personal_memory_space_id,
            hat_scope_id=current.hat_scope_id,
            state=state,
            display_name=display_name,
            quota_policy_id=quota_policy.quota_policy_id,
            quota_policy_digest=quota_policy.policy_digest,
            model_bindings=model_bindings,
            state_version=state_version,
            configuration_version=configuration_version,
            created_at=current.created_at,
            updated_at=changed_at,
            export_requested_at=export_requested_at,
            deletion_requested_at=deletion_requested_at,
            deleted_at=deleted_at,
        )
        row = transaction.fetch_one(
            """
            UPDATE memory_patch.personal_memory_spaces
               SET state = %s,
                   display_name = %s,
                   updated_at = %s,
                   export_requested_at = %s,
                   deletion_requested_at = %s,
                   deleted_at = %s,
                   state_version = %s,
                   configuration_version = %s,
                   quota_policy_id = %s,
                   quota_policy_digest = %s,
                   configuration_digest = %s,
                   hat_scope_id = %s,
                   slot_hash = %s
             WHERE tenant_id = %s
               AND user_id = %s
               AND personal_memory_space_id = %s
               AND state_version = %s
               AND configuration_version = %s
               AND configuration_digest = %s
               AND (hat_scope_id = %s OR hat_scope_id IS NULL)
               AND (slot_hash = %s OR slot_hash IS NULL)
            RETURNING tenant_id
            """,
            (
                state.value,
                display_name,
                changed_at,
                export_requested_at,
                deletion_requested_at,
                deleted_at,
                state_version,
                configuration_version,
                quota_policy.quota_policy_id,
                quota_policy.policy_digest,
                updated_slot.configuration_digest,
                updated_slot.hat_scope_id,
                updated_slot.slot_hash,
                current.tenant_id,
                current.owner_user_id,
                current.personal_memory_space_id,
                current.state_version,
                current.configuration_version,
                current.configuration_digest,
                current.hat_scope_id,
                current.slot_hash,
            ),
        )
        if row is None:
            raise OperationStateConflictError(
                "Personal Memory slot compare-and-set failed",
                sanitized_code="PERSONAL_MEMORY_CONFIGURATION_CONFLICT",
            )
        updated = self.get_slot(
            transaction,
            current.tenant_id,
            current.owner_user_id,
            current.personal_memory_space_id,
        )
        if updated is None:
            raise PersistenceConfigurationError(
                "updated Personal Memory slot is not readable",
                sanitized_code="PERSONAL_MEMORY_UPDATE_INVISIBLE",
            )
        return updated

    def insert_binding(
        self,
        transaction: TransactionProtocol,
        binding: PersonalMemoryModelBinding,
    ) -> bool:
        row = transaction.fetch_one(
            """
            INSERT INTO memory_patch.personal_memory_model_bindings (
              tenant_id, user_id, personal_memory_space_id, model_binding_id,
              provider_id, model_id, model_revision, binding_mode, enabled,
              binding_version, binding_digest, bound_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING
            RETURNING model_binding_id
            """,
            (
                binding.tenant_id,
                binding.owner_user_id,
                binding.personal_memory_space_id,
                binding.binding_id,
                binding.provider_id,
                binding.model_id,
                binding.model_revision_or_declared_version,
                binding.binding_mode.value,
                binding.enabled,
                binding.binding_version,
                binding.binding_hash,
                binding.bound_at,
            ),
        )
        if row is not None:
            return True
        rows = self._bindings(
            transaction,
            binding.tenant_id,
            binding.owner_user_id,
            binding.personal_memory_space_id,
        )
        existing = next(
            (item for item in rows if item.binding_id == binding.binding_id), None
        )
        if existing is None or existing.binding_hash != binding.binding_hash:
            raise ImmutableRecordConflictError(
                "model binding identity was reused with different facts",
                sanitized_code="PERSONAL_MEMORY_BINDING_CONFLICT",
            )
        return False

    def delete_binding(
        self,
        transaction: TransactionProtocol,
        binding: PersonalMemoryModelBinding,
    ) -> bool:
        row = transaction.fetch_one(
            """
            DELETE FROM memory_patch.personal_memory_model_bindings
             WHERE tenant_id = %s
               AND user_id = %s
               AND personal_memory_space_id = %s
               AND model_binding_id = %s
               AND binding_digest = %s
            RETURNING model_binding_id
            """,
            (
                binding.tenant_id,
                binding.owner_user_id,
                binding.personal_memory_space_id,
                binding.binding_id,
                binding.binding_hash,
            ),
        )
        return row is not None

    def delete_all_bindings(
        self,
        transaction: TransactionProtocol,
        slot: PersonalMemoryHatSlot,
    ) -> None:
        transaction.execute(
            """
            DELETE FROM memory_patch.personal_memory_model_bindings
             WHERE tenant_id = %s
               AND user_id = %s
               AND personal_memory_space_id = %s
            """,
            (slot.tenant_id, slot.owner_user_id, slot.personal_memory_space_id),
        )
