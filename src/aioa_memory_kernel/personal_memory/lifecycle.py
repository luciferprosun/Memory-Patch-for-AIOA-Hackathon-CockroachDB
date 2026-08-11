"""Step 30 owner approval, technical commit, and activation contracts.

The module is deliberately inert outside the three receipt-gated Personal
Memory lifecycle edges.  It contains no retrieval, provider, external-action,
or canonical-evidence authority.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field, fields as dataclass_fields
from datetime import datetime
from typing import Any

from aioa_memory_kernel.contracts.enums import (
    PatchState,
    ScopeComparisonMode,
    ScopeValueType,
    StableStringEnum,
)
from aioa_memory_kernel.contracts.exceptions import (
    ContractValidationError,
    IntegrityError,
)
from aioa_memory_kernel.contracts.scope import ScopeDimension
from aioa_memory_kernel.contracts.serialization import (
    canonical_json_bytes,
    canonical_sha256,
    ensure_utc,
    require_sha256_hex,
    to_canonical_data,
    verify_canonical_hash,
)
from aioa_memory_kernel.persistence.errors import PersistenceError

from .proposals import (
    MAXIMUM_PROPOSAL_RECORD_BYTES,
    PersonalMemoryPatchProposalState,
    parse_personal_memory_patch_state,
    verify_personal_memory_patch_state,
)


STEP30_SCHEMA_VERSION = "1.0.0"
PERSONAL_MEMORY_PATCH_LIFECYCLE_CONTRACT_TYPE = (
    "PersonalMemoryPatchLifecycleState"
)
PERSONAL_MEMORY_APPROVAL_POLICY_ID = "personal-memory-owner-approval-1a"
PERSONAL_MEMORY_APPROVAL_POLICY_VERSION = "1"
PERSONAL_MEMORY_COMMIT_ROLE = "mp_personal_memory_commit_helper"
PERSONAL_MEMORY_COMMIT_ACTOR_ID = "personal-memory-commit-helper-1a"
PERSONAL_MEMORY_ACTIVATION_ACTOR_ID = "personal-memory-activation-service-1a"
MAXIMUM_REVALIDATION_AGE_SECONDS = 24 * 60 * 60
MAXIMUM_NONCE_BYTES = 255
MAXIMUM_SUMMARY_BYTES = 4096

STEP30_STATES = (
    PatchState.APPROVED,
    PatchState.COMMITTED,
    PatchState.ACTIVE,
)
STEP30_STATE_VERSIONS = {
    PatchState.APPROVED: 5,
    PatchState.COMMITTED: 6,
    PatchState.ACTIVE: 7,
}
STEP30_TRANSITIONS = {
    PatchState.AWAITING_APPROVAL: PatchState.APPROVED,
    PatchState.APPROVED: PatchState.COMMITTED,
    PatchState.COMMITTED: PatchState.ACTIVE,
}


class PersonalMemoryApprovalActorType(StableStringEnum):
    HUMAN_USER = "HUMAN_USER"


class PersonalMemoryTechnicalActorType(StableStringEnum):
    COMMIT_HELPER = "COMMIT_HELPER"
    ACTIVATION_SERVICE = "ACTIVATION_SERVICE"


class Step30ReasonCode(StableStringEnum):
    APPROVAL_GRANTED = "APPROVAL_GRANTED"
    APPROVAL_EXACT_REPLAY = "APPROVAL_EXACT_REPLAY"
    APPROVAL_REPLAY_CONFLICT = "APPROVAL_REPLAY_CONFLICT"
    APPROVAL_OWNER_MISMATCH = "APPROVAL_OWNER_MISMATCH"
    APPROVAL_HASH_MISMATCH = "APPROVAL_HASH_MISMATCH"
    APPROVAL_STATE_MISMATCH = "APPROVAL_STATE_MISMATCH"
    APPROVAL_STALE = "APPROVAL_STALE"
    COMMIT_STARTED = "COMMIT_STARTED"
    COMMIT_EXACT_REPLAY = "COMMIT_EXACT_REPLAY"
    COMMIT_REPLAY_CONFLICT = "COMMIT_REPLAY_CONFLICT"
    COMMIT_APPROVAL_INVALID = "COMMIT_APPROVAL_INVALID"
    COMMIT_PROPOSAL_HASH_MISMATCH = "COMMIT_PROPOSAL_HASH_MISMATCH"
    COMMIT_VALIDATION_STALE = "COMMIT_VALIDATION_STALE"
    COMMIT_SLOT_INVALID = "COMMIT_SLOT_INVALID"
    COMMIT_QUOTA_EXCEEDED = "COMMIT_QUOTA_EXCEEDED"
    COMMIT_EVIDENCE_STALE = "COMMIT_EVIDENCE_STALE"
    COMMIT_BINDING_INVALID = "COMMIT_BINDING_INVALID"
    COMMIT_COMPLETED = "COMMIT_COMPLETED"
    ACTIVATION_STARTED = "ACTIVATION_STARTED"
    ACTIVATION_EXACT_REPLAY = "ACTIVATION_EXACT_REPLAY"
    ACTIVATION_REPLAY_CONFLICT = "ACTIVATION_REPLAY_CONFLICT"
    ACTIVATION_COMMIT_INVALID = "ACTIVATION_COMMIT_INVALID"
    ACTIVATION_SLOT_INVALID = "ACTIVATION_SLOT_INVALID"
    ACTIVATION_QUOTA_INVALID = "ACTIVATION_QUOTA_INVALID"
    ACTIVATION_BINDING_INVALID = "ACTIVATION_BINDING_INVALID"
    ACTIVATION_COMPLETED = "ACTIVATION_COMPLETED"
    MODEL_APPROVAL_FORBIDDEN = "MODEL_APPROVAL_FORBIDDEN"
    CRITIC_APPROVAL_FORBIDDEN = "CRITIC_APPROVAL_FORBIDDEN"
    HAT_APPROVAL_FORBIDDEN = "HAT_APPROVAL_FORBIDDEN"
    KERNEL_AUTO_APPROVAL_FORBIDDEN = "KERNEL_AUTO_APPROVAL_FORBIDDEN"
    OWNER_SCOPE_MISMATCH = "OWNER_SCOPE_MISMATCH"
    STATE_VERSION_CONFLICT = "STATE_VERSION_CONFLICT"
    CONTENT_CONFLICT = "CONTENT_CONFLICT"


class PersonalMemoryPatchLifecycleError(PersistenceError):
    def __init__(self, reason_code: Step30ReasonCode) -> None:
        super().__init__(
            "Personal Memory patch lifecycle rejected",
            operation_kind="PERSONAL_MEMORY_PATCH_STEP30",
            sanitized_code=reason_code.value,
        )
        self.reason_code = reason_code


def _text(value: object, name: str, maximum_bytes: int = 255) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractValidationError(f"{name} must be non-empty")
    if len(value.encode("utf-8")) > maximum_bytes:
        raise ContractValidationError(f"{name} exceeds its byte bound")
    return value


def _logical_id(value: object, name: str) -> str:
    result = _text(value, name)
    if any(ord(character) < 32 for character in result):
        raise ContractValidationError(f"{name} contains control characters")
    return result


def _scope_tuple(value: object) -> tuple[ScopeDimension, ...]:
    if not isinstance(value, (tuple, list)) or not value:
        raise ContractValidationError("approval/patch scope must be non-empty")
    result = tuple(value)
    if any(not isinstance(item, ScopeDimension) for item in result):
        raise ContractValidationError("scope must contain typed dimensions")
    if result != tuple(sorted(result, key=lambda item: item.name)):
        raise ContractValidationError("scope must be deterministically ordered")
    if len({item.name for item in result}) != len(result):
        raise ContractValidationError("scope dimensions must be unique")
    return result


def _timestamp(value: datetime, name: str) -> datetime:
    return ensure_utc(value, name)


def _replay_identity(phase: str, tenant_id: str, owner_user_id: str, key: str) -> str:
    _logical_id(key, f"{phase}_idempotency_key")
    return f"personal-memory-{phase}-replay-" + canonical_sha256(
        {
            "phase": phase,
            "tenant_id": tenant_id,
            "owner_user_id": owner_user_id,
            "idempotency_key": key,
        }
    )


def _reconstruct(value: object, context: str) -> None:
    init_values = {
        item.name: getattr(value, item.name)
        for item in dataclass_fields(value)
        if item.init
    }
    try:
        rebuilt = type(value)(**init_values)
    except Exception as exc:
        raise IntegrityError(f"{context} semantic reconstruction failed") from exc
    if rebuilt != value:
        raise IntegrityError(f"{context} contains detached derived fields")


@dataclass(frozen=True, slots=True)
class PersonalMemoryApprovalPolicy:
    policy_id: str = PERSONAL_MEMORY_APPROVAL_POLICY_ID
    policy_version: str = PERSONAL_MEMORY_APPROVAL_POLICY_VERSION
    maximum_revalidation_age_seconds: int = MAXIMUM_REVALIDATION_AGE_SECONDS
    exact_owner_required: bool = True
    policy_digest: str = field(init=False)

    def __post_init__(self) -> None:
        _logical_id(self.policy_id, "approval_policy_id")
        _logical_id(self.policy_version, "approval_policy_version")
        if (
            isinstance(self.maximum_revalidation_age_seconds, bool)
            or not isinstance(self.maximum_revalidation_age_seconds, int)
            or self.maximum_revalidation_age_seconds <= 0
            or self.maximum_revalidation_age_seconds
            > MAXIMUM_REVALIDATION_AGE_SECONDS
        ):
            raise ContractValidationError("approval revalidation age is invalid")
        if self.exact_owner_required is not True:
            raise ContractValidationError("Step 30 requires the exact human owner")
        object.__setattr__(
            self,
            "policy_digest",
            canonical_sha256(self, exclude_fields=("policy_digest",)),
        )


def load_personal_memory_approval_policy() -> PersonalMemoryApprovalPolicy:
    return PersonalMemoryApprovalPolicy()


def approval_presentation_digest(
    state: PersonalMemoryPatchProposalState,
) -> str:
    verify_personal_memory_patch_state(state)
    if (
        state.state is not PatchState.AWAITING_APPROVAL
        or state.evidence_binding is None
        or state.validation_receipt is None
        or not state.validation_receipt.validated
    ):
        raise ContractValidationError("approval presentation requires validated state")
    proposal = state.proposal
    return canonical_sha256(
        {
            "proposal_id": proposal.proposal_id,
            "proposal_hash": proposal.proposal_hash,
            "proposal_statement_sha256": proposal.proposal_statement_sha256,
            "target_binding_hash": proposal.target_binding_hash,
            "tenant_id": proposal.tenant_id,
            "owner_user_id": proposal.owner_user_id,
            "personal_memory_space_id": proposal.personal_memory_space_id,
            "scope": proposal.proposal_scope,
            "evidence_binding_hash": state.evidence_binding.binding_hash,
            "validation_receipt_hash": state.validation_receipt.receipt_hash,
            "limitations": state.evidence_binding.limitations,
            "validation_policy_digest": (
                state.validation_receipt.validation_policy_digest
            ),
        }
    )


@dataclass(frozen=True, slots=True)
class PersonalMemoryApprovalRequest:
    schema_version: str
    tenant_id: str
    owner_user_id: str
    personal_memory_space_id: str
    proposal_id: str
    proposal_hash: str
    evidence_binding_hash: str
    validation_receipt_hash: str
    expected_state: PatchState
    expected_state_version: int
    expected_state_hash: str
    approval_scope: tuple[ScopeDimension, ...]
    approval_summary_digest: str
    approval_nonce: str
    requested_at: datetime
    approval_replay_identity: str = field(init=False)
    request_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != STEP30_SCHEMA_VERSION:
            raise ContractValidationError("unsupported approval request schema")
        for value, name in (
            (self.tenant_id, "tenant_id"),
            (self.owner_user_id, "owner_user_id"),
            (self.personal_memory_space_id, "personal_memory_space_id"),
            (self.proposal_id, "proposal_id"),
        ):
            _logical_id(value, name)
        for value, name in (
            (self.proposal_hash, "proposal_hash"),
            (self.evidence_binding_hash, "evidence_binding_hash"),
            (self.validation_receipt_hash, "validation_receipt_hash"),
            (self.expected_state_hash, "expected_state_hash"),
            (self.approval_summary_digest, "approval_summary_digest"),
        ):
            require_sha256_hex(value, name)
        if self.expected_state is not PatchState.AWAITING_APPROVAL:
            raise ContractValidationError("approval requires AWAITING_APPROVAL")
        if self.expected_state_version != 4:
            raise ContractValidationError("approval expected state version must be 4")
        object.__setattr__(self, "approval_scope", _scope_tuple(self.approval_scope))
        nonce = _logical_id(self.approval_nonce, "approval_nonce")
        if len(nonce.encode("utf-8")) > MAXIMUM_NONCE_BYTES:
            raise ContractValidationError("approval nonce exceeds its byte bound")
        object.__setattr__(self, "requested_at", _timestamp(self.requested_at, "requested_at"))
        object.__setattr__(
            self,
            "approval_replay_identity",
            _replay_identity("approval", self.tenant_id, self.owner_user_id, nonce),
        )
        object.__setattr__(
            self,
            "request_hash",
            canonical_sha256(self, exclude_fields=("request_hash",)),
        )


@dataclass(frozen=True, slots=True)
class PersonalMemoryApprovalReceipt:
    schema_version: str
    approval_id: str
    request_hash: str
    tenant_id: str
    owner_user_id: str
    personal_memory_space_id: str
    proposal_id: str
    proposal_hash: str
    evidence_binding_hash: str
    validation_receipt_hash: str
    approved_scope: tuple[ScopeDimension, ...]
    approval_summary_digest: str
    approval_replay_identity: str
    actor_type: PersonalMemoryApprovalActorType
    actor_id: str
    approval_policy_id: str
    approval_policy_version: str
    approval_policy_digest: str
    reason_code: Step30ReasonCode
    approved_at: datetime
    receipt_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != STEP30_SCHEMA_VERSION:
            raise ContractValidationError("unsupported approval receipt schema")
        for value, name in (
            (self.approval_id, "approval_id"),
            (self.tenant_id, "tenant_id"),
            (self.owner_user_id, "owner_user_id"),
            (self.personal_memory_space_id, "personal_memory_space_id"),
            (self.proposal_id, "proposal_id"),
            (self.actor_id, "actor_id"),
            (self.approval_policy_id, "approval_policy_id"),
            (self.approval_policy_version, "approval_policy_version"),
        ):
            _logical_id(value, name)
        for value, name in (
            (self.request_hash, "request_hash"),
            (self.proposal_hash, "proposal_hash"),
            (self.evidence_binding_hash, "evidence_binding_hash"),
            (self.validation_receipt_hash, "validation_receipt_hash"),
            (self.approval_summary_digest, "approval_summary_digest"),
            (self.approval_policy_digest, "approval_policy_digest"),
        ):
            require_sha256_hex(value, name)
        _logical_id(self.approval_replay_identity, "approval_replay_identity")
        object.__setattr__(self, "approved_scope", _scope_tuple(self.approved_scope))
        if self.actor_type is not PersonalMemoryApprovalActorType.HUMAN_USER:
            raise ContractValidationError("approval actor must be HUMAN_USER")
        if self.actor_id != self.owner_user_id:
            raise ContractValidationError("approval actor must be the exact owner")
        policy = load_personal_memory_approval_policy()
        if (
            self.approval_policy_id != policy.policy_id
            or self.approval_policy_version != policy.policy_version
            or self.approval_policy_digest != policy.policy_digest
        ):
            raise ContractValidationError("approval policy identity differs")
        if self.reason_code is not Step30ReasonCode.APPROVAL_GRANTED:
            raise ContractValidationError("approval receipt reason is invalid")
        object.__setattr__(self, "approved_at", _timestamp(self.approved_at, "approved_at"))
        object.__setattr__(
            self,
            "receipt_hash",
            canonical_sha256(self, exclude_fields=("receipt_hash",)),
        )

    @property
    def execution_authority(self) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class PersonalMemoryCommitRequest:
    schema_version: str
    tenant_id: str
    owner_user_id: str
    personal_memory_space_id: str
    proposal_id: str
    proposal_hash: str
    approval_receipt_hash: str
    validation_receipt_hash: str
    expected_state: PatchState
    expected_state_version: int
    expected_state_hash: str
    commit_idempotency_key: str
    requested_at: datetime
    commit_replay_identity: str = field(init=False)
    request_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != STEP30_SCHEMA_VERSION:
            raise ContractValidationError("unsupported commit request schema")
        for value, name in (
            (self.tenant_id, "tenant_id"),
            (self.owner_user_id, "owner_user_id"),
            (self.personal_memory_space_id, "personal_memory_space_id"),
            (self.proposal_id, "proposal_id"),
            (self.commit_idempotency_key, "commit_idempotency_key"),
        ):
            _logical_id(value, name)
        for value, name in (
            (self.proposal_hash, "proposal_hash"),
            (self.approval_receipt_hash, "approval_receipt_hash"),
            (self.validation_receipt_hash, "validation_receipt_hash"),
            (self.expected_state_hash, "expected_state_hash"),
        ):
            require_sha256_hex(value, name)
        if self.expected_state is not PatchState.APPROVED or self.expected_state_version != 5:
            raise ContractValidationError("technical commit requires APPROVED version 5")
        object.__setattr__(self, "requested_at", _timestamp(self.requested_at, "requested_at"))
        object.__setattr__(
            self,
            "commit_replay_identity",
            _replay_identity(
                "commit", self.tenant_id, self.owner_user_id, self.commit_idempotency_key
            ),
        )
        object.__setattr__(
            self,
            "request_hash",
            canonical_sha256(self, exclude_fields=("request_hash",)),
        )


@dataclass(frozen=True, slots=True)
class CommittedPersonalMemoryPatch:
    schema_version: str
    patch_id: str
    proposal_id: str
    proposal_hash: str
    approval_receipt_hash: str
    validation_receipt_hash: str
    evidence_binding_hash: str
    tenant_id: str
    owner_user_id: str
    personal_memory_space_id: str
    hat_scope_id: str
    patch_statement: str
    patch_scope: tuple[ScopeDimension, ...]
    model_binding_id: str
    model_binding_hash: str
    commit_sequence: int
    committed_at: datetime
    patch_statement_sha256: str = field(init=False)
    patch_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != STEP30_SCHEMA_VERSION:
            raise ContractValidationError("unsupported committed patch schema")
        for value, name in (
            (self.patch_id, "patch_id"),
            (self.proposal_id, "proposal_id"),
            (self.tenant_id, "tenant_id"),
            (self.owner_user_id, "owner_user_id"),
            (self.personal_memory_space_id, "personal_memory_space_id"),
            (self.hat_scope_id, "hat_scope_id"),
            (self.model_binding_id, "model_binding_id"),
        ):
            _logical_id(value, name)
        for value, name in (
            (self.proposal_hash, "proposal_hash"),
            (self.approval_receipt_hash, "approval_receipt_hash"),
            (self.validation_receipt_hash, "validation_receipt_hash"),
            (self.evidence_binding_hash, "evidence_binding_hash"),
            (self.model_binding_hash, "model_binding_hash"),
        ):
            require_sha256_hex(value, name)
        statement = _text(self.patch_statement, "patch_statement", 16 * 1024)
        object.__setattr__(self, "patch_scope", _scope_tuple(self.patch_scope))
        if self.commit_sequence != 1:
            raise ContractValidationError("Step 30 1A commit sequence must be one")
        object.__setattr__(self, "committed_at", _timestamp(self.committed_at, "committed_at"))
        object.__setattr__(
            self,
            "patch_statement_sha256",
            hashlib.sha256(statement.encode("utf-8")).hexdigest(),
        )
        expected_id = "personal-memory-patch-" + canonical_sha256(
            {
                "tenant_id": self.tenant_id,
                "owner_user_id": self.owner_user_id,
                "personal_memory_space_id": self.personal_memory_space_id,
                "proposal_id": self.proposal_id,
                "proposal_hash": self.proposal_hash,
            }
        )
        if self.patch_id != expected_id:
            raise ContractValidationError("committed patch identity is invalid")
        object.__setattr__(
            self,
            "patch_hash",
            canonical_sha256(self, exclude_fields=("patch_hash",)),
        )

    @property
    def canonical_evidence(self) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class PersonalMemoryCommitReceipt:
    schema_version: str
    commit_id: str
    request_hash: str
    commit_replay_identity: str
    patch_id: str
    patch_hash: str
    proposal_id: str
    proposal_hash: str
    approval_receipt_hash: str
    validation_receipt_hash: str
    tenant_id: str
    owner_user_id: str
    personal_memory_space_id: str
    actor_type: PersonalMemoryTechnicalActorType
    actor_id: str
    authority_role: str
    state_version: int
    reason_code: Step30ReasonCode
    committed_at: datetime
    receipt_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != STEP30_SCHEMA_VERSION:
            raise ContractValidationError("unsupported commit receipt schema")
        for value, name in (
            (self.commit_id, "commit_id"),
            (self.commit_replay_identity, "commit_replay_identity"),
            (self.patch_id, "patch_id"),
            (self.proposal_id, "proposal_id"),
            (self.tenant_id, "tenant_id"),
            (self.owner_user_id, "owner_user_id"),
            (self.personal_memory_space_id, "personal_memory_space_id"),
            (self.actor_id, "actor_id"),
            (self.authority_role, "authority_role"),
        ):
            _logical_id(value, name)
        for value, name in (
            (self.request_hash, "request_hash"),
            (self.patch_hash, "patch_hash"),
            (self.proposal_hash, "proposal_hash"),
            (self.approval_receipt_hash, "approval_receipt_hash"),
            (self.validation_receipt_hash, "validation_receipt_hash"),
        ):
            require_sha256_hex(value, name)
        if self.actor_type is not PersonalMemoryTechnicalActorType.COMMIT_HELPER:
            raise ContractValidationError("commit receipt actor is invalid")
        if self.actor_id != PERSONAL_MEMORY_COMMIT_ACTOR_ID:
            raise ContractValidationError("commit actor identity differs")
        if self.authority_role != PERSONAL_MEMORY_COMMIT_ROLE:
            raise ContractValidationError("commit authority role differs")
        if self.state_version != 6:
            raise ContractValidationError("commit receipt state version must be 6")
        if self.reason_code is not Step30ReasonCode.COMMIT_COMPLETED:
            raise ContractValidationError("commit receipt reason is invalid")
        object.__setattr__(self, "committed_at", _timestamp(self.committed_at, "committed_at"))
        object.__setattr__(
            self,
            "receipt_hash",
            canonical_sha256(self, exclude_fields=("receipt_hash",)),
        )


@dataclass(frozen=True, slots=True)
class PersonalMemoryActivationRequest:
    schema_version: str
    tenant_id: str
    owner_user_id: str
    personal_memory_space_id: str
    patch_id: str
    proposal_hash: str
    approval_receipt_hash: str
    commit_receipt_hash: str
    expected_state: PatchState
    expected_state_version: int
    expected_state_hash: str
    activation_idempotency_key: str
    requested_at: datetime
    activation_replay_identity: str = field(init=False)
    request_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != STEP30_SCHEMA_VERSION:
            raise ContractValidationError("unsupported activation request schema")
        for value, name in (
            (self.tenant_id, "tenant_id"),
            (self.owner_user_id, "owner_user_id"),
            (self.personal_memory_space_id, "personal_memory_space_id"),
            (self.patch_id, "patch_id"),
            (self.activation_idempotency_key, "activation_idempotency_key"),
        ):
            _logical_id(value, name)
        for value, name in (
            (self.proposal_hash, "proposal_hash"),
            (self.approval_receipt_hash, "approval_receipt_hash"),
            (self.commit_receipt_hash, "commit_receipt_hash"),
            (self.expected_state_hash, "expected_state_hash"),
        ):
            require_sha256_hex(value, name)
        if self.expected_state is not PatchState.COMMITTED or self.expected_state_version != 6:
            raise ContractValidationError("activation requires COMMITTED version 6")
        object.__setattr__(self, "requested_at", _timestamp(self.requested_at, "requested_at"))
        object.__setattr__(
            self,
            "activation_replay_identity",
            _replay_identity(
                "activation",
                self.tenant_id,
                self.owner_user_id,
                self.activation_idempotency_key,
            ),
        )
        object.__setattr__(
            self,
            "request_hash",
            canonical_sha256(self, exclude_fields=("request_hash",)),
        )


@dataclass(frozen=True, slots=True)
class PersonalMemoryActivationReceipt:
    schema_version: str
    activation_id: str
    request_hash: str
    activation_replay_identity: str
    patch_id: str
    patch_hash: str
    patch_statement_sha256: str
    proposal_hash: str
    commit_receipt_hash: str
    approval_receipt_hash: str
    tenant_id: str
    owner_user_id: str
    personal_memory_space_id: str
    active_scope: tuple[ScopeDimension, ...]
    model_binding_id: str
    model_binding_hash: str
    actor_type: PersonalMemoryTechnicalActorType
    actor_id: str
    state_version: int
    reason_code: Step30ReasonCode
    activated_at: datetime
    receipt_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != STEP30_SCHEMA_VERSION:
            raise ContractValidationError("unsupported activation receipt schema")
        for value, name in (
            (self.activation_id, "activation_id"),
            (self.activation_replay_identity, "activation_replay_identity"),
            (self.patch_id, "patch_id"),
            (self.tenant_id, "tenant_id"),
            (self.owner_user_id, "owner_user_id"),
            (self.personal_memory_space_id, "personal_memory_space_id"),
            (self.model_binding_id, "model_binding_id"),
            (self.actor_id, "actor_id"),
        ):
            _logical_id(value, name)
        for value, name in (
            (self.request_hash, "request_hash"),
            (self.patch_hash, "patch_hash"),
            (self.patch_statement_sha256, "patch_statement_sha256"),
            (self.proposal_hash, "proposal_hash"),
            (self.commit_receipt_hash, "commit_receipt_hash"),
            (self.approval_receipt_hash, "approval_receipt_hash"),
            (self.model_binding_hash, "model_binding_hash"),
        ):
            require_sha256_hex(value, name)
        object.__setattr__(self, "active_scope", _scope_tuple(self.active_scope))
        if self.actor_type is not PersonalMemoryTechnicalActorType.ACTIVATION_SERVICE:
            raise ContractValidationError("activation receipt actor is invalid")
        if self.actor_id != PERSONAL_MEMORY_ACTIVATION_ACTOR_ID:
            raise ContractValidationError("activation actor identity differs")
        if self.state_version != 7:
            raise ContractValidationError("activation receipt state version must be 7")
        if self.reason_code is not Step30ReasonCode.ACTIVATION_COMPLETED:
            raise ContractValidationError("activation receipt reason is invalid")
        object.__setattr__(self, "activated_at", _timestamp(self.activated_at, "activated_at"))
        object.__setattr__(
            self,
            "receipt_hash",
            canonical_sha256(self, exclude_fields=("receipt_hash",)),
        )


@dataclass(frozen=True, slots=True)
class PersonalMemoryPatchLifecycleState:
    schema_version: str
    contract_type: str
    step29_state: PersonalMemoryPatchProposalState
    state: PatchState
    state_version: int
    approval_receipt: PersonalMemoryApprovalReceipt
    committed_patch: CommittedPersonalMemoryPatch | None
    commit_receipt: PersonalMemoryCommitReceipt | None
    activation_receipt: PersonalMemoryActivationReceipt | None
    updated_at: datetime
    state_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != STEP30_SCHEMA_VERSION:
            raise ContractValidationError("unsupported lifecycle state schema")
        if self.contract_type != PERSONAL_MEMORY_PATCH_LIFECYCLE_CONTRACT_TYPE:
            raise ContractValidationError("lifecycle contract type is invalid")
        if not isinstance(self.step29_state, PersonalMemoryPatchProposalState):
            raise ContractValidationError("step29_state must be typed")
        verify_personal_memory_patch_state(self.step29_state)
        if self.step29_state.state is not PatchState.AWAITING_APPROVAL:
            raise ContractValidationError("Step 30 must begin at AWAITING_APPROVAL")
        if self.state not in STEP30_STATES:
            raise ContractValidationError("state is outside Step 30")
        if self.state_version != STEP30_STATE_VERSIONS[self.state]:
            raise ContractValidationError("state version differs from Step 30 state")
        if not isinstance(self.approval_receipt, PersonalMemoryApprovalReceipt):
            raise ContractValidationError("approval receipt must be typed")
        proposal = self.step29_state.proposal
        binding = self.step29_state.evidence_binding
        validation = self.step29_state.validation_receipt
        if binding is None or validation is None:
            raise ContractValidationError("Step 30 lost Step 29 validation lineage")
        approval = self.approval_receipt
        if (
            approval.proposal_id != proposal.proposal_id
            or approval.proposal_hash != proposal.proposal_hash
            or approval.evidence_binding_hash != binding.binding_hash
            or approval.validation_receipt_hash != validation.receipt_hash
            or approval.tenant_id != proposal.tenant_id
            or approval.owner_user_id != proposal.owner_user_id
            or approval.personal_memory_space_id != proposal.personal_memory_space_id
            or approval.approved_scope != proposal.proposal_scope
            or approval.approved_at < self.step29_state.updated_at
        ):
            raise ContractValidationError("approval receipt is detached from proposal")
        if self.state is PatchState.APPROVED:
            if any(
                item is not None
                for item in (
                    self.committed_patch,
                    self.commit_receipt,
                    self.activation_receipt,
                )
            ):
                raise ContractValidationError("APPROVED cannot carry commit state")
        else:
            if not isinstance(self.committed_patch, CommittedPersonalMemoryPatch):
                raise ContractValidationError("committed patch must be typed")
            if not isinstance(self.commit_receipt, PersonalMemoryCommitReceipt):
                raise ContractValidationError("commit receipt must be typed")
            patch = self.committed_patch
            commit = self.commit_receipt
            if (
                patch.proposal_id != proposal.proposal_id
                or patch.proposal_hash != proposal.proposal_hash
                or patch.approval_receipt_hash != approval.receipt_hash
                or patch.validation_receipt_hash != validation.receipt_hash
                or patch.evidence_binding_hash != binding.binding_hash
                or patch.patch_statement != proposal.proposal_statement
                or patch.patch_statement_sha256
                != proposal.proposal_statement_sha256
                or patch.patch_scope != proposal.proposal_scope
                or patch.tenant_id != proposal.tenant_id
                or patch.owner_user_id != proposal.owner_user_id
                or patch.personal_memory_space_id
                != proposal.personal_memory_space_id
                or patch.hat_scope_id != proposal.hat_scope_id
                or patch.model_binding_id != proposal.model_binding_id
                or patch.model_binding_hash != proposal.model_binding_hash
                or commit.patch_id != patch.patch_id
                or commit.patch_hash != patch.patch_hash
                or commit.approval_receipt_hash != approval.receipt_hash
                or commit.proposal_hash != proposal.proposal_hash
                or patch.committed_at != commit.committed_at
                or patch.committed_at < approval.approved_at
            ):
                raise ContractValidationError("committed payload changed approved content")
            if self.state is PatchState.COMMITTED:
                if self.activation_receipt is not None:
                    raise ContractValidationError("COMMITTED cannot carry activation")
            else:
                if not isinstance(
                    self.activation_receipt, PersonalMemoryActivationReceipt
                ):
                    raise ContractValidationError("ACTIVE requires activation receipt")
                activation = self.activation_receipt
                if (
                    activation.patch_id != patch.patch_id
                    or activation.patch_hash != patch.patch_hash
                    or activation.patch_statement_sha256
                    != patch.patch_statement_sha256
                    or activation.proposal_hash != proposal.proposal_hash
                    or activation.commit_receipt_hash != commit.receipt_hash
                    or activation.approval_receipt_hash != approval.receipt_hash
                    or activation.active_scope != patch.patch_scope
                    or activation.model_binding_id != patch.model_binding_id
                    or activation.model_binding_hash != patch.model_binding_hash
                    or activation.tenant_id != proposal.tenant_id
                    or activation.owner_user_id != proposal.owner_user_id
                    or activation.personal_memory_space_id
                    != proposal.personal_memory_space_id
                    or activation.activated_at < commit.committed_at
                ):
                    raise ContractValidationError("activation is detached from commit")
        updated = _timestamp(self.updated_at, "updated_at")
        if updated < self.step29_state.updated_at:
            raise ContractValidationError("Step 30 state predates Step 29")
        expected_updated = {
            PatchState.APPROVED: approval.approved_at,
            PatchState.COMMITTED: (
                None if self.commit_receipt is None else self.commit_receipt.committed_at
            ),
            PatchState.ACTIVE: (
                None
                if self.activation_receipt is None
                else self.activation_receipt.activated_at
            ),
        }[self.state]
        if updated != expected_updated:
            raise ContractValidationError("Step 30 state time differs from its receipt")
        object.__setattr__(self, "updated_at", updated)
        object.__setattr__(
            self,
            "state_hash",
            canonical_sha256(self, exclude_fields=("state_hash",)),
        )
        if len(canonical_json_bytes(self)) > MAXIMUM_PROPOSAL_RECORD_BYTES:
            raise ContractValidationError("Step 30 lifecycle exceeds byte bound")

    @property
    def proposal(self):
        return self.step29_state.proposal

    @property
    def canonical_evidence(self) -> bool:
        return False


def build_personal_memory_approval_request(
    state: PersonalMemoryPatchProposalState,
    *,
    approval_nonce: str,
    requested_at: datetime,
) -> PersonalMemoryApprovalRequest:
    verify_personal_memory_patch_state(state)
    if state.state is not PatchState.AWAITING_APPROVAL:
        raise PersonalMemoryPatchLifecycleError(
            Step30ReasonCode.APPROVAL_STATE_MISMATCH
        )
    if ensure_utc(requested_at, "requested_at") < state.updated_at:
        raise PersonalMemoryPatchLifecycleError(Step30ReasonCode.APPROVAL_STALE)
    return PersonalMemoryApprovalRequest(
        schema_version=STEP30_SCHEMA_VERSION,
        tenant_id=state.proposal.tenant_id,
        owner_user_id=state.proposal.owner_user_id,
        personal_memory_space_id=state.proposal.personal_memory_space_id,
        proposal_id=state.proposal.proposal_id,
        proposal_hash=state.proposal.proposal_hash,
        evidence_binding_hash=state.evidence_binding.binding_hash,
        validation_receipt_hash=state.validation_receipt.receipt_hash,
        expected_state=PatchState.AWAITING_APPROVAL,
        expected_state_version=state.state_version,
        expected_state_hash=state.state_hash,
        approval_scope=state.proposal.proposal_scope,
        approval_summary_digest=approval_presentation_digest(state),
        approval_nonce=approval_nonce,
        requested_at=requested_at,
    )


def approve_personal_memory_patch(
    state: PersonalMemoryPatchProposalState,
    request: PersonalMemoryApprovalRequest,
    *,
    authenticated_actor_user_id: str,
    approved_at: datetime,
) -> PersonalMemoryPatchLifecycleState:
    verify_personal_memory_patch_state(state)
    verify_personal_memory_approval_request(request)
    proposal = state.proposal
    if approved_at < state.updated_at or approved_at < request.requested_at:
        raise PersonalMemoryPatchLifecycleError(Step30ReasonCode.APPROVAL_STALE)
    if authenticated_actor_user_id != proposal.owner_user_id:
        raise PersonalMemoryPatchLifecycleError(
            Step30ReasonCode.APPROVAL_OWNER_MISMATCH
        )
    if (
        state.state is not PatchState.AWAITING_APPROVAL
        or request.expected_state_hash != state.state_hash
        or request.expected_state_version != state.state_version
    ):
        raise PersonalMemoryPatchLifecycleError(
            Step30ReasonCode.APPROVAL_STATE_MISMATCH
        )
    if (
        request.tenant_id != proposal.tenant_id
        or request.owner_user_id != proposal.owner_user_id
        or request.personal_memory_space_id != proposal.personal_memory_space_id
    ):
        raise PersonalMemoryPatchLifecycleError(
            Step30ReasonCode.APPROVAL_OWNER_MISMATCH
        )
    if (
        request.proposal_id != proposal.proposal_id
        or request.proposal_hash != proposal.proposal_hash
        or request.evidence_binding_hash != state.evidence_binding.binding_hash
        or request.validation_receipt_hash
        != state.validation_receipt.receipt_hash
        or request.approval_scope != proposal.proposal_scope
        or request.approval_summary_digest != approval_presentation_digest(state)
    ):
        raise PersonalMemoryPatchLifecycleError(
            Step30ReasonCode.APPROVAL_HASH_MISMATCH
        )
    policy = load_personal_memory_approval_policy()
    approval_id = "personal-memory-approval-" + canonical_sha256(
        {
            "approval_replay_identity": request.approval_replay_identity,
            "request_hash": request.request_hash,
        }
    )
    receipt = PersonalMemoryApprovalReceipt(
        schema_version=STEP30_SCHEMA_VERSION,
        approval_id=approval_id,
        request_hash=request.request_hash,
        tenant_id=proposal.tenant_id,
        owner_user_id=proposal.owner_user_id,
        personal_memory_space_id=proposal.personal_memory_space_id,
        proposal_id=proposal.proposal_id,
        proposal_hash=proposal.proposal_hash,
        evidence_binding_hash=state.evidence_binding.binding_hash,
        validation_receipt_hash=state.validation_receipt.receipt_hash,
        approved_scope=proposal.proposal_scope,
        approval_summary_digest=request.approval_summary_digest,
        approval_replay_identity=request.approval_replay_identity,
        actor_type=PersonalMemoryApprovalActorType.HUMAN_USER,
        actor_id=authenticated_actor_user_id,
        approval_policy_id=policy.policy_id,
        approval_policy_version=policy.policy_version,
        approval_policy_digest=policy.policy_digest,
        reason_code=Step30ReasonCode.APPROVAL_GRANTED,
        approved_at=approved_at,
    )
    return PersonalMemoryPatchLifecycleState(
        schema_version=STEP30_SCHEMA_VERSION,
        contract_type=PERSONAL_MEMORY_PATCH_LIFECYCLE_CONTRACT_TYPE,
        step29_state=state,
        state=PatchState.APPROVED,
        state_version=5,
        approval_receipt=receipt,
        committed_patch=None,
        commit_receipt=None,
        activation_receipt=None,
        updated_at=approved_at,
    )


def build_personal_memory_commit_request(
    state: PersonalMemoryPatchLifecycleState,
    *,
    commit_idempotency_key: str,
    requested_at: datetime,
) -> PersonalMemoryCommitRequest:
    verify_personal_memory_patch_lifecycle_state(state)
    if state.state is not PatchState.APPROVED:
        raise PersonalMemoryPatchLifecycleError(
            Step30ReasonCode.COMMIT_APPROVAL_INVALID
        )
    if ensure_utc(requested_at, "requested_at") < state.updated_at:
        raise PersonalMemoryPatchLifecycleError(
            Step30ReasonCode.COMMIT_VALIDATION_STALE
        )
    return PersonalMemoryCommitRequest(
        schema_version=STEP30_SCHEMA_VERSION,
        tenant_id=state.proposal.tenant_id,
        owner_user_id=state.proposal.owner_user_id,
        personal_memory_space_id=state.proposal.personal_memory_space_id,
        proposal_id=state.proposal.proposal_id,
        proposal_hash=state.proposal.proposal_hash,
        approval_receipt_hash=state.approval_receipt.receipt_hash,
        validation_receipt_hash=state.step29_state.validation_receipt.receipt_hash,
        expected_state=PatchState.APPROVED,
        expected_state_version=state.state_version,
        expected_state_hash=state.state_hash,
        commit_idempotency_key=commit_idempotency_key,
        requested_at=requested_at,
    )


def commit_personal_memory_patch(
    state: PersonalMemoryPatchLifecycleState,
    request: PersonalMemoryCommitRequest,
    *,
    committed_at: datetime,
) -> PersonalMemoryPatchLifecycleState:
    verify_personal_memory_patch_lifecycle_state(state)
    verify_personal_memory_commit_request(request)
    proposal = state.proposal
    validation = state.step29_state.validation_receipt
    if committed_at < state.updated_at or committed_at < request.requested_at:
        raise PersonalMemoryPatchLifecycleError(
            Step30ReasonCode.COMMIT_VALIDATION_STALE
        )
    if state.state is not PatchState.APPROVED:
        raise PersonalMemoryPatchLifecycleError(
            Step30ReasonCode.COMMIT_APPROVAL_INVALID
        )
    if (
        request.expected_state_hash != state.state_hash
        or request.expected_state_version != state.state_version
    ):
        raise PersonalMemoryPatchLifecycleError(
            Step30ReasonCode.STATE_VERSION_CONFLICT
        )
    if (
        request.tenant_id != proposal.tenant_id
        or request.owner_user_id != proposal.owner_user_id
        or request.personal_memory_space_id != proposal.personal_memory_space_id
        or request.proposal_id != proposal.proposal_id
        or request.proposal_hash != proposal.proposal_hash
        or request.approval_receipt_hash != state.approval_receipt.receipt_hash
        or request.validation_receipt_hash != validation.receipt_hash
    ):
        raise PersonalMemoryPatchLifecycleError(
            Step30ReasonCode.COMMIT_PROPOSAL_HASH_MISMATCH
        )
    patch_id = "personal-memory-patch-" + canonical_sha256(
        {
            "tenant_id": proposal.tenant_id,
            "owner_user_id": proposal.owner_user_id,
            "personal_memory_space_id": proposal.personal_memory_space_id,
            "proposal_id": proposal.proposal_id,
            "proposal_hash": proposal.proposal_hash,
        }
    )
    patch = CommittedPersonalMemoryPatch(
        schema_version=STEP30_SCHEMA_VERSION,
        patch_id=patch_id,
        proposal_id=proposal.proposal_id,
        proposal_hash=proposal.proposal_hash,
        approval_receipt_hash=state.approval_receipt.receipt_hash,
        validation_receipt_hash=validation.receipt_hash,
        evidence_binding_hash=state.step29_state.evidence_binding.binding_hash,
        tenant_id=proposal.tenant_id,
        owner_user_id=proposal.owner_user_id,
        personal_memory_space_id=proposal.personal_memory_space_id,
        hat_scope_id=proposal.hat_scope_id,
        patch_statement=proposal.proposal_statement,
        patch_scope=proposal.proposal_scope,
        model_binding_id=proposal.model_binding_id,
        model_binding_hash=proposal.model_binding_hash,
        commit_sequence=1,
        committed_at=committed_at,
    )
    commit_id = "personal-memory-commit-" + canonical_sha256(
        {
            "commit_replay_identity": request.commit_replay_identity,
            "request_hash": request.request_hash,
            "patch_hash": patch.patch_hash,
        }
    )
    receipt = PersonalMemoryCommitReceipt(
        schema_version=STEP30_SCHEMA_VERSION,
        commit_id=commit_id,
        request_hash=request.request_hash,
        commit_replay_identity=request.commit_replay_identity,
        patch_id=patch.patch_id,
        patch_hash=patch.patch_hash,
        proposal_id=proposal.proposal_id,
        proposal_hash=proposal.proposal_hash,
        approval_receipt_hash=state.approval_receipt.receipt_hash,
        validation_receipt_hash=validation.receipt_hash,
        tenant_id=proposal.tenant_id,
        owner_user_id=proposal.owner_user_id,
        personal_memory_space_id=proposal.personal_memory_space_id,
        actor_type=PersonalMemoryTechnicalActorType.COMMIT_HELPER,
        actor_id=PERSONAL_MEMORY_COMMIT_ACTOR_ID,
        authority_role=PERSONAL_MEMORY_COMMIT_ROLE,
        state_version=6,
        reason_code=Step30ReasonCode.COMMIT_COMPLETED,
        committed_at=committed_at,
    )
    return PersonalMemoryPatchLifecycleState(
        schema_version=STEP30_SCHEMA_VERSION,
        contract_type=PERSONAL_MEMORY_PATCH_LIFECYCLE_CONTRACT_TYPE,
        step29_state=state.step29_state,
        state=PatchState.COMMITTED,
        state_version=6,
        approval_receipt=state.approval_receipt,
        committed_patch=patch,
        commit_receipt=receipt,
        activation_receipt=None,
        updated_at=committed_at,
    )


def build_personal_memory_activation_request(
    state: PersonalMemoryPatchLifecycleState,
    *,
    activation_idempotency_key: str,
    requested_at: datetime,
) -> PersonalMemoryActivationRequest:
    verify_personal_memory_patch_lifecycle_state(state)
    if state.state is not PatchState.COMMITTED:
        raise PersonalMemoryPatchLifecycleError(
            Step30ReasonCode.ACTIVATION_COMMIT_INVALID
        )
    if ensure_utc(requested_at, "requested_at") < state.updated_at:
        raise PersonalMemoryPatchLifecycleError(
            Step30ReasonCode.ACTIVATION_COMMIT_INVALID
        )
    return PersonalMemoryActivationRequest(
        schema_version=STEP30_SCHEMA_VERSION,
        tenant_id=state.proposal.tenant_id,
        owner_user_id=state.proposal.owner_user_id,
        personal_memory_space_id=state.proposal.personal_memory_space_id,
        patch_id=state.committed_patch.patch_id,
        proposal_hash=state.proposal.proposal_hash,
        approval_receipt_hash=state.approval_receipt.receipt_hash,
        commit_receipt_hash=state.commit_receipt.receipt_hash,
        expected_state=PatchState.COMMITTED,
        expected_state_version=state.state_version,
        expected_state_hash=state.state_hash,
        activation_idempotency_key=activation_idempotency_key,
        requested_at=requested_at,
    )


def activate_personal_memory_patch(
    state: PersonalMemoryPatchLifecycleState,
    request: PersonalMemoryActivationRequest,
    *,
    activated_at: datetime,
) -> PersonalMemoryPatchLifecycleState:
    verify_personal_memory_patch_lifecycle_state(state)
    verify_personal_memory_activation_request(request)
    if activated_at < state.updated_at or activated_at < request.requested_at:
        raise PersonalMemoryPatchLifecycleError(
            Step30ReasonCode.ACTIVATION_COMMIT_INVALID
        )
    if state.state is not PatchState.COMMITTED:
        raise PersonalMemoryPatchLifecycleError(
            Step30ReasonCode.ACTIVATION_COMMIT_INVALID
        )
    proposal = state.proposal
    patch = state.committed_patch
    commit = state.commit_receipt
    if (
        request.expected_state_hash != state.state_hash
        or request.expected_state_version != state.state_version
    ):
        raise PersonalMemoryPatchLifecycleError(
            Step30ReasonCode.STATE_VERSION_CONFLICT
        )
    if (
        request.tenant_id != proposal.tenant_id
        or request.owner_user_id != proposal.owner_user_id
        or request.personal_memory_space_id != proposal.personal_memory_space_id
        or request.patch_id != patch.patch_id
        or request.proposal_hash != proposal.proposal_hash
        or request.approval_receipt_hash != state.approval_receipt.receipt_hash
        or request.commit_receipt_hash != commit.receipt_hash
    ):
        raise PersonalMemoryPatchLifecycleError(
            Step30ReasonCode.ACTIVATION_COMMIT_INVALID
        )
    activation_id = "personal-memory-activation-" + canonical_sha256(
        {
            "activation_replay_identity": request.activation_replay_identity,
            "request_hash": request.request_hash,
            "patch_hash": patch.patch_hash,
        }
    )
    receipt = PersonalMemoryActivationReceipt(
        schema_version=STEP30_SCHEMA_VERSION,
        activation_id=activation_id,
        request_hash=request.request_hash,
        activation_replay_identity=request.activation_replay_identity,
        patch_id=patch.patch_id,
        patch_hash=patch.patch_hash,
        patch_statement_sha256=patch.patch_statement_sha256,
        proposal_hash=proposal.proposal_hash,
        commit_receipt_hash=commit.receipt_hash,
        approval_receipt_hash=state.approval_receipt.receipt_hash,
        tenant_id=proposal.tenant_id,
        owner_user_id=proposal.owner_user_id,
        personal_memory_space_id=proposal.personal_memory_space_id,
        active_scope=patch.patch_scope,
        model_binding_id=patch.model_binding_id,
        model_binding_hash=patch.model_binding_hash,
        actor_type=PersonalMemoryTechnicalActorType.ACTIVATION_SERVICE,
        actor_id=PERSONAL_MEMORY_ACTIVATION_ACTOR_ID,
        state_version=7,
        reason_code=Step30ReasonCode.ACTIVATION_COMPLETED,
        activated_at=activated_at,
    )
    return PersonalMemoryPatchLifecycleState(
        schema_version=STEP30_SCHEMA_VERSION,
        contract_type=PERSONAL_MEMORY_PATCH_LIFECYCLE_CONTRACT_TYPE,
        step29_state=state.step29_state,
        state=PatchState.ACTIVE,
        state_version=7,
        approval_receipt=state.approval_receipt,
        committed_patch=patch,
        commit_receipt=commit,
        activation_receipt=receipt,
        updated_at=activated_at,
    )


def verify_personal_memory_approval_policy(value: PersonalMemoryApprovalPolicy) -> None:
    verify_canonical_hash(value, value.policy_digest, exclude_fields=("policy_digest",))
    _reconstruct(value, "approval policy")


def verify_personal_memory_approval_request(value: PersonalMemoryApprovalRequest) -> None:
    verify_canonical_hash(value, value.request_hash, exclude_fields=("request_hash",))
    _reconstruct(value, "approval request")


def verify_personal_memory_approval_receipt(value: PersonalMemoryApprovalReceipt) -> None:
    verify_canonical_hash(value, value.receipt_hash, exclude_fields=("receipt_hash",))
    _reconstruct(value, "approval receipt")


def verify_personal_memory_commit_request(value: PersonalMemoryCommitRequest) -> None:
    verify_canonical_hash(value, value.request_hash, exclude_fields=("request_hash",))
    _reconstruct(value, "commit request")


def verify_committed_personal_memory_patch(value: CommittedPersonalMemoryPatch) -> None:
    verify_canonical_hash(value, value.patch_hash, exclude_fields=("patch_hash",))
    _reconstruct(value, "committed Personal Memory patch")


def verify_personal_memory_commit_receipt(value: PersonalMemoryCommitReceipt) -> None:
    verify_canonical_hash(value, value.receipt_hash, exclude_fields=("receipt_hash",))
    _reconstruct(value, "commit receipt")


def verify_personal_memory_activation_request(value: PersonalMemoryActivationRequest) -> None:
    verify_canonical_hash(value, value.request_hash, exclude_fields=("request_hash",))
    _reconstruct(value, "activation request")


def verify_personal_memory_activation_receipt(value: PersonalMemoryActivationReceipt) -> None:
    verify_canonical_hash(value, value.receipt_hash, exclude_fields=("receipt_hash",))
    _reconstruct(value, "activation receipt")


def verify_personal_memory_patch_lifecycle_state(
    value: PersonalMemoryPatchLifecycleState,
) -> None:
    verify_personal_memory_patch_state(value.step29_state)
    verify_personal_memory_approval_receipt(value.approval_receipt)
    if value.committed_patch is not None:
        verify_committed_personal_memory_patch(value.committed_patch)
    if value.commit_receipt is not None:
        verify_personal_memory_commit_receipt(value.commit_receipt)
    if value.activation_receipt is not None:
        verify_personal_memory_activation_receipt(value.activation_receipt)
    verify_canonical_hash(value, value.state_hash, exclude_fields=("state_hash",))
    _reconstruct(value, "Personal Memory patch lifecycle state")


def personal_memory_patch_lifecycle_to_jsonb(
    value: PersonalMemoryPatchLifecycleState,
) -> dict[str, Any]:
    verify_personal_memory_patch_lifecycle_state(value)
    result = to_canonical_data(value)
    if not isinstance(result, dict):
        raise ContractValidationError("lifecycle state did not serialize to an object")
    return result


def _mapping(value: object, names: frozenset[str], context: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or frozenset(value) != names:
        raise ContractValidationError(f"{context} has missing or unknown fields")
    return value


def _datetime(value: object, name: str) -> datetime:
    if not isinstance(value, str):
        raise ContractValidationError(f"{name} must be an ISO timestamp")
    try:
        return ensure_utc(datetime.fromisoformat(value.replace("Z", "+00:00")), name)
    except ValueError as exc:
        raise ContractValidationError(f"{name} is invalid") from exc


def _scope_from_json(value: object) -> tuple[ScopeDimension, ...]:
    if not isinstance(value, list):
        raise ContractValidationError("scope must be an array")
    result = []
    for item in value:
        data = _mapping(
            item,
            frozenset(ScopeDimension.__dataclass_fields__),
            "scope dimension",
        )
        value_type = ScopeValueType(data["value_type"])
        raw_value = data["value"]
        if value_type is ScopeValueType.STRING_SET:
            if not isinstance(raw_value, list):
                raise ContractValidationError(
                    "STRING_SET scope value must be an array"
                )
            raw_value = tuple(raw_value)
        elif value_type is ScopeValueType.TIMESTAMP:
            raw_value = _datetime(raw_value, "scope dimension value")
        result.append(
            ScopeDimension(
                name=data["name"],
                value=raw_value,
                value_type=value_type,
                comparison_mode=ScopeComparisonMode(data["comparison_mode"]),
                source=data["source"],
                required=data["required"],
            )
        )
    return tuple(result)


def _parse_approval_receipt(value: object) -> PersonalMemoryApprovalReceipt:
    data = _mapping(
        value,
        frozenset(PersonalMemoryApprovalReceipt.__dataclass_fields__),
        "approval receipt",
    )
    return PersonalMemoryApprovalReceipt(
        schema_version=data["schema_version"],
        approval_id=data["approval_id"],
        request_hash=data["request_hash"],
        tenant_id=data["tenant_id"],
        owner_user_id=data["owner_user_id"],
        personal_memory_space_id=data["personal_memory_space_id"],
        proposal_id=data["proposal_id"],
        proposal_hash=data["proposal_hash"],
        evidence_binding_hash=data["evidence_binding_hash"],
        validation_receipt_hash=data["validation_receipt_hash"],
        approved_scope=_scope_from_json(data["approved_scope"]),
        approval_summary_digest=data["approval_summary_digest"],
        approval_replay_identity=data["approval_replay_identity"],
        actor_type=PersonalMemoryApprovalActorType(data["actor_type"]),
        actor_id=data["actor_id"],
        approval_policy_id=data["approval_policy_id"],
        approval_policy_version=data["approval_policy_version"],
        approval_policy_digest=data["approval_policy_digest"],
        reason_code=Step30ReasonCode(data["reason_code"]),
        approved_at=_datetime(data["approved_at"], "approved_at"),
    )


def _parse_patch(value: object) -> CommittedPersonalMemoryPatch:
    data = _mapping(
        value,
        frozenset(CommittedPersonalMemoryPatch.__dataclass_fields__),
        "committed patch",
    )
    return CommittedPersonalMemoryPatch(
        schema_version=data["schema_version"],
        patch_id=data["patch_id"],
        proposal_id=data["proposal_id"],
        proposal_hash=data["proposal_hash"],
        approval_receipt_hash=data["approval_receipt_hash"],
        validation_receipt_hash=data["validation_receipt_hash"],
        evidence_binding_hash=data["evidence_binding_hash"],
        tenant_id=data["tenant_id"],
        owner_user_id=data["owner_user_id"],
        personal_memory_space_id=data["personal_memory_space_id"],
        hat_scope_id=data["hat_scope_id"],
        patch_statement=data["patch_statement"],
        patch_scope=_scope_from_json(data["patch_scope"]),
        model_binding_id=data["model_binding_id"],
        model_binding_hash=data["model_binding_hash"],
        commit_sequence=data["commit_sequence"],
        committed_at=_datetime(data["committed_at"], "committed_at"),
    )


def _parse_commit_receipt(value: object) -> PersonalMemoryCommitReceipt:
    data = _mapping(
        value,
        frozenset(PersonalMemoryCommitReceipt.__dataclass_fields__),
        "commit receipt",
    )
    return PersonalMemoryCommitReceipt(
        schema_version=data["schema_version"],
        commit_id=data["commit_id"],
        request_hash=data["request_hash"],
        commit_replay_identity=data["commit_replay_identity"],
        patch_id=data["patch_id"],
        patch_hash=data["patch_hash"],
        proposal_id=data["proposal_id"],
        proposal_hash=data["proposal_hash"],
        approval_receipt_hash=data["approval_receipt_hash"],
        validation_receipt_hash=data["validation_receipt_hash"],
        tenant_id=data["tenant_id"],
        owner_user_id=data["owner_user_id"],
        personal_memory_space_id=data["personal_memory_space_id"],
        actor_type=PersonalMemoryTechnicalActorType(data["actor_type"]),
        actor_id=data["actor_id"],
        authority_role=data["authority_role"],
        state_version=data["state_version"],
        reason_code=Step30ReasonCode(data["reason_code"]),
        committed_at=_datetime(data["committed_at"], "committed_at"),
    )


def _parse_activation_receipt(value: object) -> PersonalMemoryActivationReceipt:
    data = _mapping(
        value,
        frozenset(PersonalMemoryActivationReceipt.__dataclass_fields__),
        "activation receipt",
    )
    return PersonalMemoryActivationReceipt(
        schema_version=data["schema_version"],
        activation_id=data["activation_id"],
        request_hash=data["request_hash"],
        activation_replay_identity=data["activation_replay_identity"],
        patch_id=data["patch_id"],
        patch_hash=data["patch_hash"],
        patch_statement_sha256=data["patch_statement_sha256"],
        proposal_hash=data["proposal_hash"],
        commit_receipt_hash=data["commit_receipt_hash"],
        approval_receipt_hash=data["approval_receipt_hash"],
        tenant_id=data["tenant_id"],
        owner_user_id=data["owner_user_id"],
        personal_memory_space_id=data["personal_memory_space_id"],
        active_scope=_scope_from_json(data["active_scope"]),
        model_binding_id=data["model_binding_id"],
        model_binding_hash=data["model_binding_hash"],
        actor_type=PersonalMemoryTechnicalActorType(data["actor_type"]),
        actor_id=data["actor_id"],
        state_version=data["state_version"],
        reason_code=Step30ReasonCode(data["reason_code"]),
        activated_at=_datetime(data["activated_at"], "activated_at"),
    )


def parse_personal_memory_patch_lifecycle_state(
    value: object,
) -> PersonalMemoryPatchLifecycleState:
    data = _mapping(
        value,
        frozenset(PersonalMemoryPatchLifecycleState.__dataclass_fields__),
        "lifecycle state",
    )
    result = PersonalMemoryPatchLifecycleState(
        schema_version=data["schema_version"],
        contract_type=data["contract_type"],
        step29_state=parse_personal_memory_patch_state(data["step29_state"]),
        state=PatchState(data["state"]),
        state_version=data["state_version"],
        approval_receipt=_parse_approval_receipt(data["approval_receipt"]),
        committed_patch=(
            None if data["committed_patch"] is None else _parse_patch(data["committed_patch"])
        ),
        commit_receipt=(
            None
            if data["commit_receipt"] is None
            else _parse_commit_receipt(data["commit_receipt"])
        ),
        activation_receipt=(
            None
            if data["activation_receipt"] is None
            else _parse_activation_receipt(data["activation_receipt"])
        ),
        updated_at=_datetime(data["updated_at"], "updated_at"),
    )
    if result.state_hash != data["state_hash"]:
        raise IntegrityError("serialized Step 30 state hash differs")
    verify_personal_memory_patch_lifecycle_state(result)
    return result


__all__ = [
    "CommittedPersonalMemoryPatch",
    "MAXIMUM_REVALIDATION_AGE_SECONDS",
    "PERSONAL_MEMORY_ACTIVATION_ACTOR_ID",
    "PERSONAL_MEMORY_APPROVAL_POLICY_ID",
    "PERSONAL_MEMORY_APPROVAL_POLICY_VERSION",
    "PERSONAL_MEMORY_COMMIT_ACTOR_ID",
    "PERSONAL_MEMORY_COMMIT_ROLE",
    "PERSONAL_MEMORY_PATCH_LIFECYCLE_CONTRACT_TYPE",
    "PersonalMemoryActivationReceipt",
    "PersonalMemoryActivationRequest",
    "PersonalMemoryApprovalActorType",
    "PersonalMemoryApprovalPolicy",
    "PersonalMemoryApprovalReceipt",
    "PersonalMemoryApprovalRequest",
    "PersonalMemoryCommitReceipt",
    "PersonalMemoryCommitRequest",
    "PersonalMemoryPatchLifecycleError",
    "PersonalMemoryPatchLifecycleState",
    "PersonalMemoryTechnicalActorType",
    "STEP30_SCHEMA_VERSION",
    "STEP30_STATES",
    "STEP30_STATE_VERSIONS",
    "STEP30_TRANSITIONS",
    "Step30ReasonCode",
    "activate_personal_memory_patch",
    "approval_presentation_digest",
    "approve_personal_memory_patch",
    "build_personal_memory_activation_request",
    "build_personal_memory_approval_request",
    "build_personal_memory_commit_request",
    "commit_personal_memory_patch",
    "load_personal_memory_approval_policy",
    "parse_personal_memory_patch_lifecycle_state",
    "personal_memory_patch_lifecycle_to_jsonb",
    "verify_committed_personal_memory_patch",
    "verify_personal_memory_activation_receipt",
    "verify_personal_memory_activation_request",
    "verify_personal_memory_approval_policy",
    "verify_personal_memory_approval_receipt",
    "verify_personal_memory_approval_request",
    "verify_personal_memory_commit_receipt",
    "verify_personal_memory_commit_request",
    "verify_personal_memory_patch_lifecycle_state",
]
