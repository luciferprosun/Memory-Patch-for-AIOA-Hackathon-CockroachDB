"""Deterministic lifecycle operations for Personal Memory HAT pools."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from types import MappingProxyType

from ..contracts.enums import PersonalMemorySpaceState
from ..contracts.exceptions import (
    ContractValidationError,
    InvalidTransition,
    OwnershipViolation,
)
from ..contracts.identities import verify_ownership
from ..contracts.personal_memory import (
    PersonalMemoryPool,
    PersonalMemorySpace,
    enforce_quota,
    quota_usage,
)
from ..contracts.serialization import (
    ensure_utc,
    require_enum_member,
    require_non_empty,
)


PERSONAL_MEMORY_TRANSITIONS = MappingProxyType({
    PersonalMemorySpaceState.EMPTY: frozenset(
        {
            PersonalMemorySpaceState.CONFIGURED,
            PersonalMemorySpaceState.DELETED_PENDING,
        }
    ),
    PersonalMemorySpaceState.CONFIGURED: frozenset(
        {
            PersonalMemorySpaceState.ACTIVE,
            PersonalMemorySpaceState.ARCHIVED,
            PersonalMemorySpaceState.DELETED_PENDING,
        }
    ),
    PersonalMemorySpaceState.ACTIVE: frozenset(
        {
            PersonalMemorySpaceState.SUSPENDED,
            PersonalMemorySpaceState.ARCHIVED,
            PersonalMemorySpaceState.DELETED_PENDING,
        }
    ),
    PersonalMemorySpaceState.SUSPENDED: frozenset(
        {
            PersonalMemorySpaceState.ACTIVE,
            PersonalMemorySpaceState.ARCHIVED,
            PersonalMemorySpaceState.DELETED_PENDING,
        }
    ),
    PersonalMemorySpaceState.ARCHIVED: frozenset(
        {
            PersonalMemorySpaceState.CONFIGURED,
            PersonalMemorySpaceState.DELETED_PENDING,
        }
    ),
    PersonalMemorySpaceState.DELETED_PENDING: frozenset(
        {PersonalMemorySpaceState.DELETED}
    ),
    PersonalMemorySpaceState.DELETED: frozenset(),
})

PERSONAL_MEMORY_TERMINAL_STATES = frozenset(
    {PersonalMemorySpaceState.DELETED}
)


def personal_memory_transition_allowed(
    current: PersonalMemorySpaceState,
    target: PersonalMemorySpaceState,
) -> bool:
    """Return whether the explicit Personal Memory graph contains the edge."""

    require_enum_member(current, PersonalMemorySpaceState, "current")
    require_enum_member(target, PersonalMemorySpaceState, "target")
    return target in PERSONAL_MEMORY_TRANSITIONS[current]


def _find_space(pool: PersonalMemoryPool, space_id: str) -> PersonalMemorySpace:
    require_non_empty(space_id, "personal_memory_space_id")
    matches = [
        space
        for space in pool.spaces
        if space.personal_memory_space_id == space_id
    ]
    if not matches:
        raise ContractValidationError("Personal Memory HAT does not exist")
    return matches[0]


def _authorize_space(
    space: PersonalMemorySpace,
    *,
    tenant_id: str | None,
    user_id: str | None,
) -> None:
    verify_ownership(
        space.ownership,
        tenant_id=tenant_id,
        user_id=user_id,
        personal_memory_space_id=space.personal_memory_space_id,
    )


def _replace_space(
    pool: PersonalMemoryPool, updated: PersonalMemorySpace
) -> PersonalMemoryPool:
    spaces = tuple(
        updated
        if space.personal_memory_space_id == updated.personal_memory_space_id
        else space
        for space in pool.spaces
    )
    candidate = replace(pool, spaces=spaces)
    enforce_quota(candidate.quota_policy, quota_usage(candidate.spaces))
    return candidate


def _require_monotonic_update(
    space: PersonalMemorySpace, changed_at: datetime
) -> None:
    if changed_at < space.updated_at:
        raise ContractValidationError(
            "Personal Memory HAT update cannot move time backwards"
        )


def allocate_personal_memory_space(
    pool: PersonalMemoryPool,
    *,
    personal_memory_space_id: str,
    created_at: datetime,
    tenant_id: str | None,
    user_id: str | None,
) -> tuple[PersonalMemoryPool, PersonalMemorySpace]:
    """Allocate one empty, inert slot after exact ownership and quota checks."""

    if tenant_id != pool.tenant_id:
        raise OwnershipViolation("cannot allocate a space across tenants")
    if user_id != pool.user_id:
        raise OwnershipViolation("cannot allocate a space for another user")
    require_non_empty(personal_memory_space_id, "personal_memory_space_id")
    if any(
        space.personal_memory_space_id == personal_memory_space_id
        for space in pool.spaces
    ):
        raise ContractValidationError("Personal Memory HAT ID already exists")
    created_at = ensure_utc(created_at, "created_at")
    space = PersonalMemorySpace(
        personal_memory_space_id=personal_memory_space_id,
        tenant_id=pool.tenant_id,
        user_id=pool.user_id,
        state=PersonalMemorySpaceState.EMPTY,
        display_name=None,
        created_at=created_at,
        updated_at=created_at,
    )
    candidate = replace(pool, spaces=pool.spaces + (space,))
    enforce_quota(candidate.quota_policy, quota_usage(candidate.spaces))
    return candidate, space


def transition_personal_memory_space(
    pool: PersonalMemoryPool,
    *,
    personal_memory_space_id: str,
    target_state: PersonalMemorySpaceState,
    changed_at: datetime,
    tenant_id: str | None,
    user_id: str | None,
) -> PersonalMemoryPool:
    """Apply one explicit state transition and preserve owner identity."""

    require_enum_member(target_state, PersonalMemorySpaceState, "target_state")
    space = _find_space(pool, personal_memory_space_id)
    _authorize_space(space, tenant_id=tenant_id, user_id=user_id)
    if not personal_memory_transition_allowed(space.state, target_state):
        raise InvalidTransition(
            f"Personal Memory HAT transition {space.state.value} -> "
            f"{target_state.value} is forbidden"
        )
    if target_state is PersonalMemorySpaceState.CONFIGURED and not space.display_name:
        raise ContractValidationError(
            "configuration requires a name; use configure_personal_memory_space"
        )
    changed_at = ensure_utc(changed_at, "changed_at")
    _require_monotonic_update(space, changed_at)
    updates: dict[str, object] = {
        "state": target_state,
        "updated_at": changed_at,
    }
    if target_state is PersonalMemorySpaceState.DELETED_PENDING:
        updates["deletion_requested_at"] = changed_at
    if target_state is PersonalMemorySpaceState.DELETED:
        updates["deleted_at"] = changed_at
        updates["display_name"] = None
        updates["model_binding_ids"] = ()
    updated = replace(space, **updates)
    return _replace_space(pool, updated)


def configure_personal_memory_space(
    pool: PersonalMemoryPool,
    *,
    personal_memory_space_id: str,
    display_name: str,
    changed_at: datetime,
    tenant_id: str | None,
    user_id: str | None,
) -> PersonalMemoryPool:
    """Name/configure an empty space or rename an owned non-terminal space."""

    space = _find_space(pool, personal_memory_space_id)
    _authorize_space(space, tenant_id=tenant_id, user_id=user_id)
    require_non_empty(display_name, "display_name")
    if space.state in {
        PersonalMemorySpaceState.DELETED_PENDING,
        PersonalMemorySpaceState.DELETED,
    }:
        raise InvalidTransition("a deleting or deleted space cannot be configured")
    changed_at = ensure_utc(changed_at, "changed_at")
    _require_monotonic_update(space, changed_at)
    target_state = (
        PersonalMemorySpaceState.CONFIGURED
        if space.state is PersonalMemorySpaceState.EMPTY
        else space.state
    )
    updated = replace(
        space,
        state=target_state,
        display_name=display_name,
        updated_at=changed_at,
    )
    return _replace_space(pool, updated)


def name_personal_memory_space(
    pool: PersonalMemoryPool,
    *,
    personal_memory_space_id: str,
    display_name: str,
    changed_at: datetime,
    tenant_id: str | None,
    user_id: str | None,
) -> PersonalMemoryPool:
    """Public naming alias with the same guards as configuration."""

    return configure_personal_memory_space(
        pool,
        personal_memory_space_id=personal_memory_space_id,
        display_name=display_name,
        changed_at=changed_at,
        tenant_id=tenant_id,
        user_id=user_id,
    )


def activate_personal_memory_space(
    pool: PersonalMemoryPool, **context: object
) -> PersonalMemoryPool:
    return transition_personal_memory_space(
        pool, target_state=PersonalMemorySpaceState.ACTIVE, **context
    )


def suspend_personal_memory_space(
    pool: PersonalMemoryPool, **context: object
) -> PersonalMemoryPool:
    return transition_personal_memory_space(
        pool, target_state=PersonalMemorySpaceState.SUSPENDED, **context
    )


def restore_personal_memory_space(
    pool: PersonalMemoryPool, **context: object
) -> PersonalMemoryPool:
    """Restore suspended to ACTIVE, or archived to CONFIGURED."""

    space_id = str(context["personal_memory_space_id"])
    space = _find_space(pool, space_id)
    if space.state is PersonalMemorySpaceState.SUSPENDED:
        target = PersonalMemorySpaceState.ACTIVE
    elif space.state is PersonalMemorySpaceState.ARCHIVED:
        target = PersonalMemorySpaceState.CONFIGURED
    else:
        raise InvalidTransition(
            f"{space.state.value} cannot be restored"
        )
    return transition_personal_memory_space(
        pool, target_state=target, **context
    )


def archive_personal_memory_space(
    pool: PersonalMemoryPool, **context: object
) -> PersonalMemoryPool:
    return transition_personal_memory_space(
        pool, target_state=PersonalMemorySpaceState.ARCHIVED, **context
    )


def request_personal_memory_export(
    pool: PersonalMemoryPool,
    *,
    personal_memory_space_id: str,
    requested_at: datetime,
    tenant_id: str | None,
    user_id: str | None,
) -> PersonalMemoryPool:
    """Record an export request without exporting private content."""

    space = _find_space(pool, personal_memory_space_id)
    _authorize_space(space, tenant_id=tenant_id, user_id=user_id)
    if space.state is PersonalMemorySpaceState.DELETED:
        raise InvalidTransition("a deleted space cannot be exported")
    requested_at = ensure_utc(requested_at, "requested_at")
    _require_monotonic_update(space, requested_at)
    return _replace_space(
        pool,
        replace(
            space,
            export_requested_at=requested_at,
            updated_at=requested_at,
        ),
    )


def request_personal_memory_deletion(
    pool: PersonalMemoryPool, **context: object
) -> PersonalMemoryPool:
    return transition_personal_memory_space(
        pool, target_state=PersonalMemorySpaceState.DELETED_PENDING, **context
    )


def complete_personal_memory_deletion(
    pool: PersonalMemoryPool, **context: object
) -> PersonalMemoryPool:
    return transition_personal_memory_space(
        pool, target_state=PersonalMemorySpaceState.DELETED, **context
    )


def bind_model_to_personal_memory_space(
    pool: PersonalMemoryPool,
    *,
    personal_memory_space_id: str,
    model_binding_id: str,
    changed_at: datetime,
    tenant_id: str | None,
    user_id: str | None,
) -> PersonalMemoryPool:
    """Bind a model reference without changing ownership or memory identity."""

    space = _find_space(pool, personal_memory_space_id)
    _authorize_space(space, tenant_id=tenant_id, user_id=user_id)
    if space.state not in {
        PersonalMemorySpaceState.CONFIGURED,
        PersonalMemorySpaceState.ACTIVE,
        PersonalMemorySpaceState.SUSPENDED,
    }:
        raise InvalidTransition(
            f"cannot bind a model while space is {space.state.value}"
        )
    require_non_empty(model_binding_id, "model_binding_id")
    changed_at = ensure_utc(changed_at, "changed_at")
    _require_monotonic_update(space, changed_at)
    bindings = tuple(sorted(set(space.model_binding_ids + (model_binding_id,))))
    updated = replace(
        space,
        model_binding_ids=bindings,
        updated_at=changed_at,
    )
    if updated.ownership != space.ownership:
        raise OwnershipViolation("model binding cannot transfer ownership")
    return _replace_space(pool, updated)
