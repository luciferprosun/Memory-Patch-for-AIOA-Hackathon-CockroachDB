"""Deterministic, owner-scoped Step 27 configuration export."""

from __future__ import annotations

from aioa_memory_kernel.contracts.enums import PersonalMemorySpaceState

from .models import (
    PERSONAL_MEMORY_EXPORT_SCHEMA_VERSION,
    ExportSlotCommand,
    PersonalMemoryHatSlot,
    PersonalMemoryPersistenceError,
    PersonalMemoryQuotaUsageView,
    PersonalMemoryReasonCode,
    PersonalMemorySlotExport,
    verify_slot_hash,
    verify_usage_hash,
)


def build_personal_memory_export(
    command: ExportSlotCommand,
    slot: PersonalMemoryHatSlot,
    usage: PersonalMemoryQuotaUsageView,
) -> PersonalMemorySlotExport:
    """Export safe configuration only, never patch bodies or provider secrets."""

    verify_slot_hash(slot)
    verify_usage_hash(usage)
    exact_owner = (
        command.tenant_id == slot.tenant_id == usage.tenant_id
        and command.owner_user_id == slot.owner_user_id == usage.owner_user_id
        and command.personal_memory_space_id
        == slot.personal_memory_space_id
        == usage.personal_memory_space_id
    )
    if not exact_owner:
        raise PersonalMemoryPersistenceError(
            PersonalMemoryReasonCode.EXPORT_OWNER_MISMATCH
        )
    if slot.state is PersonalMemorySpaceState.DELETED:
        raise PersonalMemoryPersistenceError(
            PersonalMemoryReasonCode.DELETE_STATE_INVALID
        )
    return PersonalMemorySlotExport(
        export_schema_version=PERSONAL_MEMORY_EXPORT_SCHEMA_VERSION,
        tenant_id=slot.tenant_id,
        owner_user_id=slot.owner_user_id,
        personal_memory_space_id=slot.personal_memory_space_id,
        slot_hash=slot.slot_hash,
        state=slot.state,
        display_name=slot.display_name,
        quota_policy_id=slot.quota_policy_id,
        quota_policy_digest=slot.quota_policy_digest,
        model_binding_hashes=tuple(
            binding.binding_hash for binding in slot.model_bindings
        ),
        state_version=slot.state_version,
        configuration_version=slot.configuration_version,
        requested_at=command.requested_at,
        memory_item_count=usage.memory_item_count,
        patch_count=usage.patch_count,
        stored_bytes=usage.stored_bytes,
    )
