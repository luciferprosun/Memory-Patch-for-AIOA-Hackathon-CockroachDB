"""Immutable Step 27 Personal Memory HAT persistence contracts.

The records in this module describe a private owner-scoped data space.  They
carry no provider, approval, commit, execution, patch-activation, retrieval,
or canonical-evidence authority.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from aioa_memory_kernel.contracts.enums import (
    PersonalMemorySpaceState,
    StableStringEnum,
)
from aioa_memory_kernel.contracts.exceptions import (
    ContractValidationError,
    IntegrityError,
    QuotaExceeded,
)
from aioa_memory_kernel.contracts.personal_memory import (
    PersonalHatQuotaPolicy,
    PersonalHatQuotaUsage,
    enforce_quota,
)
from aioa_memory_kernel.contracts.serialization import (
    canonical_json,
    canonical_json_bytes,
    canonical_sha256,
    ensure_utc,
    require_sha256_hex,
    verify_canonical_hash,
)
from aioa_memory_kernel.persistence.errors import PersistenceError


STEP27_SCHEMA_VERSION = "1.0.0"
PERSONAL_MEMORY_SLOT_CONTRACT_VERSION = "personal-memory-hat-slot-1a"
PERSONAL_MEMORY_QUOTA_CONTRACT_VERSION = "personal-memory-quota-policy-1a"
PERSONAL_MEMORY_MODEL_BINDING_CONTRACT_VERSION = "personal-memory-model-binding-1a"
PERSONAL_MEMORY_EXPORT_SCHEMA_VERSION = "personal-memory-owner-export-1a"
MAXIMUM_LOGICAL_ID_BYTES = 255
MAXIMUM_DISPLAY_NAME_BYTES = 256
MAXIMUM_PROVIDER_MODEL_ID_BYTES = 128
MAXIMUM_MODEL_BINDINGS_HARD_LIMIT = 64
MAXIMUM_EXPORT_BYTES = 64 * 1024
MAXIMUM_QUOTA_VALUE = (2**63) - 1
PERSONAL_MEMORY_POLICY_PATH = (
    Path(__file__).resolve().parents[3]
    / "config"
    / "personal-memory"
    / "personal-memory-policy-1a.json"
)

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_LOGICAL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+-]{0,254}$")


class PersonalMemoryBindingMode(StableStringEnum):
    """Provider-neutral Step 27 binding vocabulary."""

    EXACT_MODEL = "EXACT_MODEL"


class PersonalMemoryMutationActor(StableStringEnum):
    """Only trusted configuration boundaries may mutate a slot."""

    OWNER_CONFIGURATION = "OWNER_CONFIGURATION"
    TRUSTED_APPLICATION = "TRUSTED_APPLICATION"


class PersonalMemoryBindingAction(StableStringEnum):
    ADD = "ADD"
    REMOVE = "REMOVE"


class PersonalMemoryOperationKind(StableStringEnum):
    CREATE_EMPTY_SLOT = "CREATE_EMPTY_SLOT"
    CONFIGURE_SLOT = "CONFIGURE_SLOT"
    TRANSITION_SLOT = "TRANSITION_SLOT"
    UPDATE_MODEL_BINDING = "UPDATE_MODEL_BINDING"
    REQUEST_EXPORT = "REQUEST_EXPORT"


class PersonalMemoryReasonCode(StableStringEnum):
    SLOT_CREATED = "SLOT_CREATED"
    SLOT_ALREADY_EXISTS_EXACT_REPLAY = "SLOT_ALREADY_EXISTS_EXACT_REPLAY"
    SLOT_CONFIGURATION_UPDATED = "SLOT_CONFIGURATION_UPDATED"
    SLOT_ACTIVATED = "SLOT_ACTIVATED"
    SLOT_DEACTIVATED = "SLOT_DEACTIVATED"
    SLOT_ARCHIVED = "SLOT_ARCHIVED"
    OWNER_MISMATCH = "OWNER_MISMATCH"
    TENANT_MISMATCH = "TENANT_MISMATCH"
    SLOT_NOT_FOUND = "SLOT_NOT_FOUND"
    INVALID_STATE_TRANSITION = "INVALID_STATE_TRANSITION"
    CONFIGURATION_CONFLICT = "CONFIGURATION_CONFLICT"
    QUOTA_OK = "QUOTA_OK"
    QUOTA_EXCEEDED = "QUOTA_EXCEEDED"
    QUOTA_POLICY_INVALID = "QUOTA_POLICY_INVALID"
    MODEL_BINDING_ADDED = "MODEL_BINDING_ADDED"
    MODEL_BINDING_REMOVED = "MODEL_BINDING_REMOVED"
    MODEL_BINDING_LIMIT_EXCEEDED = "MODEL_BINDING_LIMIT_EXCEEDED"
    MODEL_BINDING_INVALID = "MODEL_BINDING_INVALID"
    EXPORT_READY = "EXPORT_READY"
    EXPORT_OWNER_MISMATCH = "EXPORT_OWNER_MISMATCH"
    DELETE_REQUESTED = "DELETE_REQUESTED"
    DELETE_COMPLETED = "DELETE_COMPLETED"
    DELETE_STATE_INVALID = "DELETE_STATE_INVALID"


class PersonalMemoryPersistenceError(PersistenceError):
    """Sanitized fail-closed Step 27 error."""

    def __init__(self, reason_code: PersonalMemoryReasonCode) -> None:
        if not isinstance(reason_code, PersonalMemoryReasonCode):
            raise TypeError("reason_code must be PersonalMemoryReasonCode")
        super().__init__(
            "Personal Memory operation failed",
            sanitized_code=reason_code.value,
        )
        self.reason_code = reason_code


def _text(value: object, field_name: str, maximum_bytes: int) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or unicodedata.normalize("NFC", value) != value
        or _CONTROL.search(value)
        or len(value.encode("utf-8")) > maximum_bytes
    ):
        raise ContractValidationError(
            f"{field_name} must be bounded canonical NFC text"
        )
    return value


def _logical_id(value: object, field_name: str) -> str:
    result = _text(value, field_name, MAXIMUM_LOGICAL_ID_BYTES)
    if _LOGICAL_ID.fullmatch(result) is None:
        raise ContractValidationError(f"{field_name} must be a logical identifier")
    return result


def _version(value: object, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ContractValidationError(f"{field_name} must be non-negative")
    return value


def _positive_version(value: object, field_name: str) -> int:
    result = _version(value, field_name)
    if result < 1:
        raise ContractValidationError(f"{field_name} must be positive")
    return result


def _bounded_quota(value: int | None, field_name: str) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < 0
        or value > MAXIMUM_QUOTA_VALUE
    ):
        raise ContractValidationError(
            f"{field_name} must be an explicit bounded non-negative integer"
        )
    return value


def _timestamp(value: datetime | None, field_name: str) -> datetime | None:
    return None if value is None else ensure_utc(value, field_name)


def personal_memory_hat_scope_id(
    tenant_id: str,
    owner_user_id: str,
    personal_memory_space_id: str,
) -> str:
    """Derive a stable private HAT-scope identity from the exact owner tuple."""

    payload = {
        "owner_user_id": _logical_id(owner_user_id, "owner_user_id"),
        "personal_memory_space_id": _logical_id(
            personal_memory_space_id, "personal_memory_space_id"
        ),
        "target_scope": "USER_PERSONAL_HAT",
        "tenant_id": _logical_id(tenant_id, "tenant_id"),
    }
    return f"personal-hat-scope-{canonical_sha256(payload)}"


@dataclass(frozen=True, slots=True)
class PersonalMemoryQuotaPolicyRecord:
    schema_version: str
    tenant_id: str
    owner_user_id: str
    quota_policy_id: str
    quota_policy_version: str
    limits: PersonalHatQuotaPolicy
    maximum_model_bindings_per_space: int
    policy_digest: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != STEP27_SCHEMA_VERSION:
            raise ContractValidationError("unsupported quota policy schema version")
        for value, name in (
            (self.tenant_id, "tenant_id"),
            (self.owner_user_id, "owner_user_id"),
            (self.quota_policy_id, "quota_policy_id"),
            (self.quota_policy_version, "quota_policy_version"),
        ):
            _logical_id(value, name)
        if not isinstance(self.limits, PersonalHatQuotaPolicy):
            raise ContractValidationError("limits must reuse PersonalHatQuotaPolicy")
        for name in self.limits.__dataclass_fields__:
            _bounded_quota(getattr(self.limits, name), name)
        binding_limit = _bounded_quota(
            self.maximum_model_bindings_per_space,
            "maximum_model_bindings_per_space",
        )
        if binding_limit > MAXIMUM_MODEL_BINDINGS_HARD_LIMIT:
            raise ContractValidationError("model binding quota exceeds the hard limit")
        object.__setattr__(
            self,
            "policy_digest",
            canonical_sha256(self, exclude_fields=("policy_digest",)),
        )


@dataclass(frozen=True, slots=True)
class PersonalMemoryModelBinding:
    schema_version: str
    tenant_id: str
    owner_user_id: str
    personal_memory_space_id: str
    provider_id: str
    model_id: str
    model_revision_or_declared_version: str
    binding_mode: PersonalMemoryBindingMode
    enabled: bool
    binding_version: int
    bound_at: datetime
    binding_id: str = field(init=False)
    binding_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != STEP27_SCHEMA_VERSION:
            raise ContractValidationError("unsupported model-binding schema version")
        for value, name in (
            (self.tenant_id, "tenant_id"),
            (self.owner_user_id, "owner_user_id"),
            (self.personal_memory_space_id, "personal_memory_space_id"),
        ):
            _logical_id(value, name)
        for value, name in (
            (self.provider_id, "provider_id"),
            (self.model_id, "model_id"),
            (
                self.model_revision_or_declared_version,
                "model_revision_or_declared_version",
            ),
        ):
            _text(value, name, MAXIMUM_PROVIDER_MODEL_ID_BYTES)
        if not isinstance(self.binding_mode, PersonalMemoryBindingMode):
            raise ContractValidationError("binding_mode is invalid")
        if not isinstance(self.enabled, bool):
            raise ContractValidationError("enabled must be boolean")
        _positive_version(self.binding_version, "binding_version")
        object.__setattr__(self, "bound_at", ensure_utc(self.bound_at, "bound_at"))
        identity = canonical_sha256(
            {
                "binding_mode": self.binding_mode,
                "model_id": self.model_id,
                "model_revision_or_declared_version": (
                    self.model_revision_or_declared_version
                ),
                "owner_user_id": self.owner_user_id,
                "personal_memory_space_id": self.personal_memory_space_id,
                "provider_id": self.provider_id,
                "tenant_id": self.tenant_id,
            }
        )
        object.__setattr__(self, "binding_id", f"personal-model-binding-{identity}")
        object.__setattr__(
            self,
            "binding_hash",
            canonical_sha256(
                self,
                exclude_fields=("binding_hash",),
            ),
        )


def _binding_tuple(value: object) -> tuple[PersonalMemoryModelBinding, ...]:
    if not isinstance(value, (tuple, list)) or any(
        not isinstance(item, PersonalMemoryModelBinding) for item in value
    ):
        raise ContractValidationError("model_bindings must contain typed bindings")
    frozen = tuple(sorted(value, key=lambda item: item.binding_id))
    identities = tuple(item.binding_id for item in frozen)
    if len(identities) != len(set(identities)):
        raise ContractValidationError("model binding identities must be unique")
    return frozen


def personal_memory_configuration_digest(
    *,
    tenant_id: str,
    owner_user_id: str,
    personal_memory_space_id: str,
    display_name: str | None,
    quota_policy_id: str,
    quota_policy_digest: str,
    model_bindings: tuple[PersonalMemoryModelBinding, ...],
) -> str:
    return canonical_sha256(
        {
            "display_name": display_name,
            "model_binding_hashes": tuple(
                binding.binding_hash for binding in model_bindings
            ),
            "owner_user_id": owner_user_id,
            "personal_memory_space_id": personal_memory_space_id,
            "quota_policy_digest": quota_policy_digest,
            "quota_policy_id": quota_policy_id,
            "tenant_id": tenant_id,
        }
    )


@dataclass(frozen=True, slots=True)
class PersonalMemoryHatSlot:
    schema_version: str
    tenant_id: str
    owner_user_id: str
    personal_memory_space_id: str
    hat_scope_id: str
    state: PersonalMemorySpaceState
    display_name: str | None
    quota_policy_id: str
    quota_policy_digest: str
    model_bindings: tuple[PersonalMemoryModelBinding, ...]
    state_version: int
    configuration_version: int
    created_at: datetime
    updated_at: datetime
    export_requested_at: datetime | None = None
    deletion_requested_at: datetime | None = None
    deleted_at: datetime | None = None
    configuration_digest: str = field(init=False)
    slot_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != STEP27_SCHEMA_VERSION:
            raise ContractValidationError("unsupported slot schema version")
        for value, name in (
            (self.tenant_id, "tenant_id"),
            (self.owner_user_id, "owner_user_id"),
            (self.personal_memory_space_id, "personal_memory_space_id"),
            (self.hat_scope_id, "hat_scope_id"),
            (self.quota_policy_id, "quota_policy_id"),
        ):
            _logical_id(value, name)
        require_sha256_hex(self.quota_policy_digest, "quota_policy_digest")
        expected_scope = personal_memory_hat_scope_id(
            self.tenant_id,
            self.owner_user_id,
            self.personal_memory_space_id,
        )
        if self.hat_scope_id != expected_scope:
            raise ContractValidationError("hat_scope_id does not bind the owner slot")
        if not isinstance(self.state, PersonalMemorySpaceState):
            raise ContractValidationError("state must be PersonalMemorySpaceState")
        bindings = _binding_tuple(self.model_bindings)
        object.__setattr__(self, "model_bindings", bindings)
        for binding in bindings:
            if (
                binding.tenant_id != self.tenant_id
                or binding.owner_user_id != self.owner_user_id
                or binding.personal_memory_space_id != self.personal_memory_space_id
            ):
                raise ContractValidationError("model binding crosses the slot owner")
        _version(self.state_version, "state_version")
        _version(self.configuration_version, "configuration_version")
        object.__setattr__(self, "created_at", ensure_utc(self.created_at, "created_at"))
        object.__setattr__(self, "updated_at", ensure_utc(self.updated_at, "updated_at"))
        if self.updated_at < self.created_at:
            raise ContractValidationError("updated_at cannot precede created_at")
        for name in (
            "export_requested_at",
            "deletion_requested_at",
            "deleted_at",
        ):
            object.__setattr__(self, name, _timestamp(getattr(self, name), name))
        if self.display_name is not None:
            _text(self.display_name, "display_name", MAXIMUM_DISPLAY_NAME_BYTES)
        if self.state is PersonalMemorySpaceState.EMPTY:
            if self.display_name is not None or bindings:
                raise ContractValidationError("an EMPTY slot must be inert")
        elif self.state in {
            PersonalMemorySpaceState.CONFIGURED,
            PersonalMemorySpaceState.ACTIVE,
            PersonalMemorySpaceState.SUSPENDED,
            PersonalMemorySpaceState.ARCHIVED,
        }:
            if self.display_name is None:
                raise ContractValidationError("a configured slot requires a display name")
        if self.state is PersonalMemorySpaceState.DELETED_PENDING:
            if self.deletion_requested_at is None:
                raise ContractValidationError("DELETED_PENDING requires request time")
        if self.state is PersonalMemorySpaceState.DELETED:
            if self.deleted_at is None or self.display_name is not None or bindings:
                raise ContractValidationError("DELETED slot must be inert and timestamped")
        elif self.deleted_at is not None:
            raise ContractValidationError("only DELETED may carry deleted_at")
        configuration_digest = personal_memory_configuration_digest(
            tenant_id=self.tenant_id,
            owner_user_id=self.owner_user_id,
            personal_memory_space_id=self.personal_memory_space_id,
            display_name=self.display_name,
            quota_policy_id=self.quota_policy_id,
            quota_policy_digest=self.quota_policy_digest,
            model_bindings=bindings,
        )
        object.__setattr__(self, "configuration_digest", configuration_digest)
        object.__setattr__(
            self,
            "slot_hash",
            canonical_sha256(
                self,
                exclude_fields=("slot_hash",),
            ),
        )

    @property
    def retrieval_eligible(self) -> bool:
        """State metadata only; Step 27 does not implement retrieval."""

        return self.state is PersonalMemorySpaceState.ACTIVE

    @property
    def executable(self) -> bool:
        return False

    @property
    def canonical_evidence(self) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class PersonalMemoryQuotaUsageView:
    schema_version: str
    tenant_id: str
    owner_user_id: str
    personal_memory_space_id: str
    quota_policy_digest: str
    usage: PersonalHatQuotaUsage
    model_binding_count: int
    memory_item_count: int
    patch_count: int
    stored_bytes: int
    usage_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != STEP27_SCHEMA_VERSION:
            raise ContractValidationError("unsupported quota usage schema version")
        for value, name in (
            (self.tenant_id, "tenant_id"),
            (self.owner_user_id, "owner_user_id"),
            (self.personal_memory_space_id, "personal_memory_space_id"),
        ):
            _logical_id(value, name)
        require_sha256_hex(self.quota_policy_digest, "quota_policy_digest")
        if not isinstance(self.usage, PersonalHatQuotaUsage):
            raise ContractValidationError("usage must reuse PersonalHatQuotaUsage")
        for name in (
            "model_binding_count",
            "memory_item_count",
            "patch_count",
            "stored_bytes",
        ):
            _version(getattr(self, name), name)
        object.__setattr__(
            self,
            "usage_hash",
            canonical_sha256(self, exclude_fields=("usage_hash",)),
        )


def enforce_step27_quota(
    policy: PersonalMemoryQuotaPolicyRecord,
    usage: PersonalMemoryQuotaUsageView,
) -> None:
    if (
        policy.tenant_id != usage.tenant_id
        or policy.owner_user_id != usage.owner_user_id
        or policy.policy_digest != usage.quota_policy_digest
    ):
        raise ContractValidationError("quota usage does not bind the policy owner")
    enforce_quota(policy.limits, usage.usage)
    if usage.model_binding_count > policy.maximum_model_bindings_per_space:
        raise QuotaExceeded("model binding count exceeds configured quota")


def _command_identity(
    *,
    schema_version: str,
    tenant_id: str,
    owner_user_id: str,
    personal_memory_space_id: str,
    idempotency_key: str,
    requested_at: datetime,
    actor: PersonalMemoryMutationActor,
) -> datetime:
    if schema_version != STEP27_SCHEMA_VERSION:
        raise ContractValidationError("unsupported command schema version")
    for value, name in (
        (tenant_id, "tenant_id"),
        (owner_user_id, "owner_user_id"),
        (personal_memory_space_id, "personal_memory_space_id"),
        (idempotency_key, "idempotency_key"),
    ):
        _logical_id(value, name)
    if not isinstance(actor, PersonalMemoryMutationActor):
        raise ContractValidationError("actor is not a trusted configuration boundary")
    return ensure_utc(requested_at, "requested_at")


@dataclass(frozen=True, slots=True)
class CreateEmptySlotCommand:
    schema_version: str
    tenant_id: str
    owner_user_id: str
    personal_memory_space_id: str
    quota_policy: PersonalMemoryQuotaPolicyRecord
    idempotency_key: str
    requested_at: datetime
    actor: PersonalMemoryMutationActor
    command_hash: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "requested_at",
            _command_identity(
                schema_version=self.schema_version,
                tenant_id=self.tenant_id,
                owner_user_id=self.owner_user_id,
                personal_memory_space_id=self.personal_memory_space_id,
                idempotency_key=self.idempotency_key,
                requested_at=self.requested_at,
                actor=self.actor,
            ),
        )
        if not isinstance(self.quota_policy, PersonalMemoryQuotaPolicyRecord):
            raise ContractValidationError("quota_policy must be typed")
        if (
            self.quota_policy.tenant_id != self.tenant_id
            or self.quota_policy.owner_user_id != self.owner_user_id
        ):
            raise ContractValidationError("quota policy crosses the command owner")
        object.__setattr__(
            self,
            "command_hash",
            canonical_sha256(self, exclude_fields=("command_hash",)),
        )


@dataclass(frozen=True, slots=True)
class ConfigureSlotCommand:
    schema_version: str
    tenant_id: str
    owner_user_id: str
    personal_memory_space_id: str
    display_name: str
    quota_policy: PersonalMemoryQuotaPolicyRecord
    expected_state_version: int
    expected_configuration_version: int
    idempotency_key: str
    requested_at: datetime
    actor: PersonalMemoryMutationActor
    command_hash: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "requested_at",
            _command_identity(
                schema_version=self.schema_version,
                tenant_id=self.tenant_id,
                owner_user_id=self.owner_user_id,
                personal_memory_space_id=self.personal_memory_space_id,
                idempotency_key=self.idempotency_key,
                requested_at=self.requested_at,
                actor=self.actor,
            ),
        )
        _text(self.display_name, "display_name", MAXIMUM_DISPLAY_NAME_BYTES)
        _version(self.expected_state_version, "expected_state_version")
        _version(
            self.expected_configuration_version,
            "expected_configuration_version",
        )
        if not isinstance(self.quota_policy, PersonalMemoryQuotaPolicyRecord):
            raise ContractValidationError("quota_policy must be typed")
        if (
            self.quota_policy.tenant_id != self.tenant_id
            or self.quota_policy.owner_user_id != self.owner_user_id
        ):
            raise ContractValidationError("quota policy crosses the command owner")
        object.__setattr__(
            self,
            "command_hash",
            canonical_sha256(self, exclude_fields=("command_hash",)),
        )


@dataclass(frozen=True, slots=True)
class TransitionSlotCommand:
    schema_version: str
    tenant_id: str
    owner_user_id: str
    personal_memory_space_id: str
    target_state: PersonalMemorySpaceState
    expected_state_version: int
    expected_configuration_version: int
    idempotency_key: str
    requested_at: datetime
    actor: PersonalMemoryMutationActor
    command_hash: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "requested_at",
            _command_identity(
                schema_version=self.schema_version,
                tenant_id=self.tenant_id,
                owner_user_id=self.owner_user_id,
                personal_memory_space_id=self.personal_memory_space_id,
                idempotency_key=self.idempotency_key,
                requested_at=self.requested_at,
                actor=self.actor,
            ),
        )
        if not isinstance(self.target_state, PersonalMemorySpaceState):
            raise ContractValidationError("target_state is invalid")
        _version(self.expected_state_version, "expected_state_version")
        _version(
            self.expected_configuration_version,
            "expected_configuration_version",
        )
        object.__setattr__(
            self,
            "command_hash",
            canonical_sha256(self, exclude_fields=("command_hash",)),
        )


@dataclass(frozen=True, slots=True)
class ModelBindingCommand:
    schema_version: str
    tenant_id: str
    owner_user_id: str
    personal_memory_space_id: str
    binding: PersonalMemoryModelBinding
    action: PersonalMemoryBindingAction
    expected_state_version: int
    expected_configuration_version: int
    idempotency_key: str
    requested_at: datetime
    actor: PersonalMemoryMutationActor
    command_hash: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "requested_at",
            _command_identity(
                schema_version=self.schema_version,
                tenant_id=self.tenant_id,
                owner_user_id=self.owner_user_id,
                personal_memory_space_id=self.personal_memory_space_id,
                idempotency_key=self.idempotency_key,
                requested_at=self.requested_at,
                actor=self.actor,
            ),
        )
        if not isinstance(self.binding, PersonalMemoryModelBinding):
            raise ContractValidationError("binding must be typed")
        if not isinstance(self.action, PersonalMemoryBindingAction):
            raise ContractValidationError("binding action is invalid")
        if (
            self.binding.tenant_id != self.tenant_id
            or self.binding.owner_user_id != self.owner_user_id
            or self.binding.personal_memory_space_id
            != self.personal_memory_space_id
        ):
            raise ContractValidationError("binding crosses the command owner")
        _version(self.expected_state_version, "expected_state_version")
        _version(
            self.expected_configuration_version,
            "expected_configuration_version",
        )
        object.__setattr__(
            self,
            "command_hash",
            canonical_sha256(self, exclude_fields=("command_hash",)),
        )


@dataclass(frozen=True, slots=True)
class ExportSlotCommand:
    schema_version: str
    tenant_id: str
    owner_user_id: str
    personal_memory_space_id: str
    expected_state_version: int
    expected_configuration_version: int
    idempotency_key: str
    requested_at: datetime
    actor: PersonalMemoryMutationActor
    command_hash: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "requested_at",
            _command_identity(
                schema_version=self.schema_version,
                tenant_id=self.tenant_id,
                owner_user_id=self.owner_user_id,
                personal_memory_space_id=self.personal_memory_space_id,
                idempotency_key=self.idempotency_key,
                requested_at=self.requested_at,
                actor=self.actor,
            ),
        )
        _version(self.expected_state_version, "expected_state_version")
        _version(
            self.expected_configuration_version,
            "expected_configuration_version",
        )
        object.__setattr__(
            self,
            "command_hash",
            canonical_sha256(self, exclude_fields=("command_hash",)),
        )


@dataclass(frozen=True, slots=True)
class SlotMutationReceipt:
    schema_version: str
    operation_id: str
    operation_kind: PersonalMemoryOperationKind
    reason_code: PersonalMemoryReasonCode
    tenant_id: str
    owner_user_id: str
    personal_memory_space_id: str
    command_hash: str
    slot_hash: str
    state: PersonalMemorySpaceState
    state_version: int
    configuration_version: int
    replayed: bool
    result_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != STEP27_SCHEMA_VERSION:
            raise ContractValidationError("unsupported receipt schema version")
        for value, name in (
            (self.operation_id, "operation_id"),
            (self.tenant_id, "tenant_id"),
            (self.owner_user_id, "owner_user_id"),
            (self.personal_memory_space_id, "personal_memory_space_id"),
        ):
            _logical_id(value, name)
        if not isinstance(self.operation_kind, PersonalMemoryOperationKind):
            raise ContractValidationError("operation_kind is invalid")
        if not isinstance(self.reason_code, PersonalMemoryReasonCode):
            raise ContractValidationError("reason_code is invalid")
        require_sha256_hex(self.command_hash, "command_hash")
        require_sha256_hex(self.slot_hash, "slot_hash")
        if not isinstance(self.state, PersonalMemorySpaceState):
            raise ContractValidationError("state is invalid")
        _version(self.state_version, "state_version")
        _version(self.configuration_version, "configuration_version")
        if not isinstance(self.replayed, bool):
            raise ContractValidationError("replayed must be boolean")
        object.__setattr__(
            self,
            "result_hash",
            canonical_sha256(self, exclude_fields=("result_hash",)),
        )


@dataclass(frozen=True, slots=True)
class PersonalMemorySlotExport:
    export_schema_version: str
    tenant_id: str
    owner_user_id: str
    personal_memory_space_id: str
    slot_hash: str
    state: PersonalMemorySpaceState
    display_name: str | None
    quota_policy_id: str
    quota_policy_digest: str
    model_binding_hashes: tuple[str, ...]
    state_version: int
    configuration_version: int
    requested_at: datetime
    memory_item_count: int
    patch_count: int
    stored_bytes: int
    export_digest: str = field(init=False)

    def __post_init__(self) -> None:
        if self.export_schema_version != PERSONAL_MEMORY_EXPORT_SCHEMA_VERSION:
            raise ContractValidationError("unsupported export schema version")
        for value, name in (
            (self.tenant_id, "tenant_id"),
            (self.owner_user_id, "owner_user_id"),
            (self.personal_memory_space_id, "personal_memory_space_id"),
            (self.quota_policy_id, "quota_policy_id"),
        ):
            _logical_id(value, name)
        for value, name in (
            (self.slot_hash, "slot_hash"),
            (self.quota_policy_digest, "quota_policy_digest"),
        ):
            require_sha256_hex(value, name)
        if not isinstance(self.model_binding_hashes, (tuple, list)):
            raise ContractValidationError("model_binding_hashes must be ordered")
        hashes = tuple(sorted(self.model_binding_hashes))
        if len(hashes) != len(set(hashes)):
            raise ContractValidationError("model binding hashes must be unique")
        for value in hashes:
            require_sha256_hex(value, "model binding hash")
        object.__setattr__(self, "model_binding_hashes", hashes)
        if not isinstance(self.state, PersonalMemorySpaceState):
            raise ContractValidationError("state is invalid")
        if self.display_name is not None:
            _text(self.display_name, "display_name", MAXIMUM_DISPLAY_NAME_BYTES)
        _version(self.state_version, "state_version")
        _version(self.configuration_version, "configuration_version")
        object.__setattr__(
            self, "requested_at", ensure_utc(self.requested_at, "requested_at")
        )
        for name in ("memory_item_count", "patch_count", "stored_bytes"):
            _version(getattr(self, name), name)
        object.__setattr__(
            self,
            "export_digest",
            canonical_sha256(self, exclude_fields=("export_digest",)),
        )
        if len(self.canonical_bytes()) > MAXIMUM_EXPORT_BYTES:
            raise ContractValidationError("owner export exceeds its byte limit")

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self)

    def canonical_text(self) -> str:
        return canonical_json(self)


def verify_quota_policy_hash(value: PersonalMemoryQuotaPolicyRecord) -> None:
    verify_canonical_hash(value, value.policy_digest, exclude_fields=("policy_digest",))


def verify_model_binding_hash(value: PersonalMemoryModelBinding) -> None:
    verify_canonical_hash(value, value.binding_hash, exclude_fields=("binding_hash",))


def verify_slot_hash(value: PersonalMemoryHatSlot) -> None:
    verify_canonical_hash(value, value.slot_hash, exclude_fields=("slot_hash",))


def verify_usage_hash(value: PersonalMemoryQuotaUsageView) -> None:
    verify_canonical_hash(value, value.usage_hash, exclude_fields=("usage_hash",))


def verify_receipt_hash(value: SlotMutationReceipt) -> None:
    verify_canonical_hash(value, value.result_hash, exclude_fields=("result_hash",))


def verify_export_hash(value: PersonalMemorySlotExport) -> None:
    try:
        verify_canonical_hash(
            value,
            value.export_digest,
            exclude_fields=("export_digest",),
        )
    except IntegrityError:
        raise


def operation_id_for_command(
    operation_kind: PersonalMemoryOperationKind,
    tenant_id: str,
    owner_user_id: str,
    idempotency_key: str,
) -> str:
    if not isinstance(operation_kind, PersonalMemoryOperationKind):
        raise ContractValidationError("operation_kind is invalid")
    digest = canonical_sha256(
        {
            "idempotency_key": _logical_id(idempotency_key, "idempotency_key"),
            "operation_kind": operation_kind,
            "owner_user_id": _logical_id(owner_user_id, "owner_user_id"),
            "tenant_id": _logical_id(tenant_id, "tenant_id"),
        }
    )
    return f"personal-memory-operation-{digest}"


def owner_scope_digest(
    tenant_id: str,
    owner_user_id: str,
    personal_memory_space_id: str,
) -> str:
    return canonical_sha256(
        {
            "owner_user_id": owner_user_id,
            "personal_memory_space_id": personal_memory_space_id,
            "target_scope": "USER_PERSONAL_HAT",
            "tenant_id": tenant_id,
        }
    )


def policy_from_mapping(
    value: dict[str, Any],
    *,
    tenant_id: str,
    owner_user_id: str,
) -> PersonalMemoryQuotaPolicyRecord:
    """Build one owner-bound policy from sanitized configuration data."""

    if not isinstance(value, dict):
        raise ContractValidationError("policy configuration must be an object")
    limits = value.get("limits")
    if not isinstance(limits, dict) or set(limits) != set(
        PersonalHatQuotaPolicy.__dataclass_fields__
    ):
        raise ContractValidationError("quota limit set is not exact")
    return PersonalMemoryQuotaPolicyRecord(
        schema_version=str(value.get("schema_version")),
        tenant_id=tenant_id,
        owner_user_id=owner_user_id,
        quota_policy_id=str(value.get("quota_policy_id")),
        quota_policy_version=str(value.get("quota_policy_version")),
        limits=PersonalHatQuotaPolicy(**limits),
        maximum_model_bindings_per_space=value.get(
            "maximum_model_bindings_per_space"
        ),
    )
