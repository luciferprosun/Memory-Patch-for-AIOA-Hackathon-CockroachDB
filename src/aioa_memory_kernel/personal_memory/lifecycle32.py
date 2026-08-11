"""Step 32 Personal Memory lifecycle and private-to-shared review contracts.

The module adds immutable terminal overlays and owner-private export/delete
records around the exact Step 30 patch lineage.  It never rewrites a patch,
publishes a shared memory item, upgrades source authority, or implements the
Step 33 audit ledger.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, fields as dataclass_fields
from datetime import datetime
from typing import Any

from aioa_memory_kernel.contracts.enums import (
    ActorType,
    DeidentificationStatus,
    PatchState,
    PersonalMemorySpaceState,
    PrivateDataClassification,
    SharedPromotionState,
    StableStringEnum,
)
from aioa_memory_kernel.contracts.exceptions import (
    ContractValidationError,
    IntegrityError,
)
from aioa_memory_kernel.contracts.patches import (
    SharedPromotionProposal,
    verify_shared_promotion_hash,
)
from aioa_memory_kernel.contracts.scope import ScopeDimension
from aioa_memory_kernel.contracts.serialization import (
    canonical_json_bytes,
    canonical_sha256,
    ensure_utc,
    freeze_json,
    require_sha256_hex,
    to_canonical_data,
    verify_canonical_hash,
)
from aioa_memory_kernel.persistence.errors import PersistenceError
from aioa_memory_kernel.security.redaction import assert_secret_free

from .lifecycle import (
    PersonalMemoryPatchLifecycleState,
    verify_personal_memory_patch_lifecycle_state,
)
from .models import PersonalMemoryHatSlot, verify_slot_hash
from .retrieval import CanonicalEvidenceCompatibility


STEP32_SCHEMA_VERSION = "1.0.0"
STEP32_STATE_VERSION = 8
STEP32_DEIDENTIFICATION_POLICY_ID = "personal-memory-shared-deidentification-1a"
STEP32_DEIDENTIFICATION_POLICY_VERSION = "1"
STEP32_EXPORT_SCHEMA_VERSION = "personal-memory-owner-export-2.0.0"
STEP32_SYSTEM_POLICY_ACTOR_ID = "personal-memory-lifecycle-policy-1a"
MAXIMUM_LIFECYCLE_TEXT_BYTES = 16 * 1024
MAXIMUM_EXPORT_RECORDS = 1024
MAXIMUM_EXPORT_BYTES = 8 * 1024 * 1024
MAXIMUM_REASON_CODES = 16
MAXIMUM_PRIVATE_IDENTIFIERS = 32


class Step32ActorType(StableStringEnum):
    HUMAN_OWNER = "HUMAN_OWNER"
    DETERMINISTIC_SYSTEM_POLICY = "DETERMINISTIC_SYSTEM_POLICY"
    LIFECYCLE_SERVICE = "LIFECYCLE_SERVICE"


class DeidentificationDecision(StableStringEnum):
    PASS = "PASS"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    FAIL = "FAIL"


class Step32ReasonCode(StableStringEnum):
    SUPERSESSION_CREATED = "SUPERSESSION_CREATED"
    SUPERSESSION_EXACT_REPLAY = "SUPERSESSION_EXACT_REPLAY"
    SUPERSESSION_CONFLICT = "SUPERSESSION_CONFLICT"
    SUPERSESSION_OWNER_MISMATCH = "SUPERSESSION_OWNER_MISMATCH"
    SUPERSESSION_SCOPE_MISMATCH = "SUPERSESSION_SCOPE_MISMATCH"
    REVOCATION_CREATED = "REVOCATION_CREATED"
    REVOCATION_EXACT_REPLAY = "REVOCATION_EXACT_REPLAY"
    REVOCATION_OWNER_MISMATCH = "REVOCATION_OWNER_MISMATCH"
    REVOCATION_STATE_INVALID = "REVOCATION_STATE_INVALID"
    EXPORT_READY = "EXPORT_READY"
    EXPORT_EXACT_REPLAY = "EXPORT_EXACT_REPLAY"
    EXPORT_OWNER_MISMATCH = "EXPORT_OWNER_MISMATCH"
    DELETE_REQUESTED = "DELETE_REQUESTED"
    DELETE_COMPLETED = "DELETE_COMPLETED"
    DELETE_EXACT_REPLAY = "DELETE_EXACT_REPLAY"
    DELETE_OWNER_MISMATCH = "DELETE_OWNER_MISMATCH"
    DELETE_STATE_INVALID = "DELETE_STATE_INVALID"
    SHARED_PROMOTION_PROPOSED = "SHARED_PROMOTION_PROPOSED"
    SHARED_PROMOTION_REPLAY = "SHARED_PROMOTION_REPLAY"
    SHARED_PROMOTION_OWNER_CONSENT_REQUIRED = (
        "SHARED_PROMOTION_OWNER_CONSENT_REQUIRED"
    )
    SHARED_PROMOTION_PRIVACY_REVIEW_REQUIRED = (
        "SHARED_PROMOTION_PRIVACY_REVIEW_REQUIRED"
    )
    SHARED_PROMOTION_CANONICAL_CONFLICT = (
        "SHARED_PROMOTION_CANONICAL_CONFLICT"
    )
    SHARED_PROMOTION_SOURCE_REVOKED = "SHARED_PROMOTION_SOURCE_REVOKED"
    SHARED_PROMOTION_SOURCE_DELETED = "SHARED_PROMOTION_SOURCE_DELETED"
    DEIDENTIFICATION_PASS = "DEIDENTIFICATION_PASS"
    DEIDENTIFICATION_REVIEW_REQUIRED = "DEIDENTIFICATION_REVIEW_REQUIRED"
    DEIDENTIFICATION_FAIL = "DEIDENTIFICATION_FAIL"
    CANONICAL_EVIDENCE_AUTHORITY_NOT_GRANTED = (
        "CANONICAL_EVIDENCE_AUTHORITY_NOT_GRANTED"
    )
    STATE_VERSION_CONFLICT = "STATE_VERSION_CONFLICT"
    REPLAY_CONFLICT = "REPLAY_CONFLICT"


class PersonalMemoryStep32Error(PersistenceError):
    def __init__(self, reason_code: Step32ReasonCode) -> None:
        if not isinstance(reason_code, Step32ReasonCode):
            raise TypeError("reason_code must be Step32ReasonCode")
        super().__init__(
            "Personal Memory Step 32 lifecycle rejected",
            operation_kind="PERSONAL_MEMORY_STEP32_LIFECYCLE",
            sanitized_code=reason_code.value,
        )
        self.reason_code = reason_code


def _text(value: object, name: str, maximum: int = 255) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or unicodedata.normalize("NFC", value) != value
        or len(value.encode("utf-8")) > maximum
        or any(ord(character) < 32 for character in value)
    ):
        raise ContractValidationError(f"{name} must be bounded canonical text")
    return value


def _logical_id(value: object, name: str) -> str:
    return _text(value, name)


def _timestamp(value: datetime, name: str) -> datetime:
    return ensure_utc(value, name)


def _scope(value: object) -> tuple[ScopeDimension, ...]:
    if not isinstance(value, (tuple, list)) or not value:
        raise ContractValidationError("scope must be a non-empty ordered tuple")
    result = tuple(value)
    if any(not isinstance(item, ScopeDimension) for item in result):
        raise ContractValidationError("scope must contain ScopeDimension values")
    if result != tuple(sorted(result, key=lambda item: item.name)):
        raise ContractValidationError("scope must be deterministically ordered")
    if len({item.name for item in result}) != len(result):
        raise ContractValidationError("scope dimension names must be unique")
    return result


def _reason_codes(value: object) -> tuple[Step32ReasonCode, ...]:
    if not isinstance(value, (tuple, list)) or not value:
        raise ContractValidationError("reason_codes must be non-empty")
    result = tuple(value)
    if len(result) > MAXIMUM_REASON_CODES or any(
        not isinstance(item, Step32ReasonCode) for item in result
    ):
        raise ContractValidationError("reason_codes are invalid")
    expected = tuple(sorted(set(result), key=lambda item: item.value))
    if result != expected:
        raise ContractValidationError("reason_codes must be unique and ordered")
    return result


def _replay_identity(
    operation: str,
    tenant_id: str,
    owner_user_id: str,
    idempotency_key: str,
) -> str:
    _logical_id(idempotency_key, "idempotency_key")
    return "personal-memory-step32-replay-" + canonical_sha256(
        {
            "operation": operation,
            "tenant_id": tenant_id,
            "owner_user_id": owner_user_id,
            "idempotency_key": idempotency_key,
        }
    )


def _reconstruct(value: object, name: str) -> None:
    try:
        rebuilt = type(value)(
            **{
                item.name: getattr(value, item.name)
                for item in dataclass_fields(value)
                if item.init
            }
        )
    except Exception as exc:
        raise IntegrityError(f"{name} semantic reconstruction failed") from exc
    if rebuilt != value:
        raise IntegrityError(f"{name} contains detached derived fields")


def _active_state(
    value: PersonalMemoryPatchLifecycleState,
) -> PersonalMemoryPatchLifecycleState:
    if not isinstance(value, PersonalMemoryPatchLifecycleState):
        raise ContractValidationError("an exact Step 30 lifecycle is required")
    verify_personal_memory_patch_lifecycle_state(value)
    if (
        value.state is not PatchState.ACTIVE
        or value.state_version != 7
        or value.committed_patch is None
        or value.commit_receipt is None
        or value.activation_receipt is None
    ):
        raise ContractValidationError("only an exact ACTIVE Step 30 patch is eligible")
    return value


def _same_owner_slot(
    left: PersonalMemoryPatchLifecycleState,
    right: PersonalMemoryPatchLifecycleState,
) -> bool:
    return (
        left.proposal.tenant_id == right.proposal.tenant_id
        and left.proposal.owner_user_id == right.proposal.owner_user_id
        and left.proposal.personal_memory_space_id
        == right.proposal.personal_memory_space_id
        and left.committed_patch.hat_scope_id == right.committed_patch.hat_scope_id
    )


@dataclass(frozen=True, slots=True)
class PersonalMemorySupersessionRequest:
    schema_version: str
    tenant_id: str
    owner_user_id: str
    personal_memory_space_id: str
    old_proposal_id: str
    old_patch_id: str
    old_patch_hash: str
    old_state_hash: str
    new_proposal_id: str
    new_patch_id: str
    new_patch_hash: str
    new_state_hash: str
    reason_codes: tuple[Step32ReasonCode, ...]
    effective_at: datetime
    idempotency_key: str
    replay_identity: str = field(init=False)
    request_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != STEP32_SCHEMA_VERSION:
            raise ContractValidationError("unsupported supersession schema")
        for name in (
            "tenant_id", "owner_user_id", "personal_memory_space_id",
            "old_proposal_id", "old_patch_id", "new_proposal_id", "new_patch_id",
        ):
            _logical_id(getattr(self, name), name)
        if self.old_patch_id == self.new_patch_id:
            raise ContractValidationError("supersession requires two patches")
        for name in ("old_patch_hash", "old_state_hash", "new_patch_hash", "new_state_hash"):
            require_sha256_hex(getattr(self, name), name)
        object.__setattr__(self, "reason_codes", _reason_codes(self.reason_codes))
        object.__setattr__(self, "effective_at", _timestamp(self.effective_at, "effective_at"))
        replay = _replay_identity("supersession", self.tenant_id, self.owner_user_id, self.idempotency_key)
        object.__setattr__(self, "replay_identity", replay)
        object.__setattr__(self, "request_hash", canonical_sha256(self, exclude_fields=("request_hash",)))


@dataclass(frozen=True, slots=True)
class PersonalMemoryPatchSupersession:
    schema_version: str
    supersession_id: str
    request_hash: str
    replay_identity: str
    tenant_id: str
    owner_user_id: str
    personal_memory_space_id: str
    old_proposal_id: str
    old_patch_id: str
    old_patch_hash: str
    old_state_hash: str
    new_proposal_id: str
    new_patch_id: str
    new_patch_hash: str
    new_state_hash: str
    patch_scope: tuple[ScopeDimension, ...]
    reason_codes: tuple[Step32ReasonCode, ...]
    actor_type: Step32ActorType
    actor_id: str
    effective_at: datetime
    state: PatchState = PatchState.SUPERSEDED
    state_version: int = STEP32_STATE_VERSION
    preserves_history: bool = True
    canonical_evidence: bool = False
    supersession_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != STEP32_SCHEMA_VERSION:
            raise ContractValidationError("unsupported supersession record schema")
        for name in (
            "supersession_id", "replay_identity", "tenant_id", "owner_user_id",
            "personal_memory_space_id", "old_proposal_id", "old_patch_id",
            "new_proposal_id", "new_patch_id", "actor_id",
        ):
            _logical_id(getattr(self, name), name)
        for name in ("request_hash", "old_patch_hash", "old_state_hash", "new_patch_hash", "new_state_hash"):
            require_sha256_hex(getattr(self, name), name)
        expected_id = "personal-memory-supersession-" + canonical_sha256(
            {
                "replay_identity": self.replay_identity,
                "request_hash": self.request_hash,
            }
        )
        if self.supersession_id != expected_id:
            raise ContractValidationError("supersession identity is detached")
        if self.old_patch_id == self.new_patch_id:
            raise ContractValidationError("supersession cannot self-reference")
        object.__setattr__(self, "patch_scope", _scope(self.patch_scope))
        object.__setattr__(self, "reason_codes", _reason_codes(self.reason_codes))
        if self.actor_type is not Step32ActorType.HUMAN_OWNER:
            raise ContractValidationError("Step 32 supersession requires the human owner")
        if self.actor_id != self.owner_user_id:
            raise ContractValidationError("supersession actor is not the owner")
        if self.state is not PatchState.SUPERSEDED or self.state_version != STEP32_STATE_VERSION:
            raise ContractValidationError("supersession terminal state is invalid")
        if self.preserves_history is not True or self.canonical_evidence is not False:
            raise ContractValidationError("supersession authority flags are invalid")
        object.__setattr__(self, "effective_at", _timestamp(self.effective_at, "effective_at"))
        object.__setattr__(self, "supersession_hash", canonical_sha256(self, exclude_fields=("supersession_hash",)))


@dataclass(frozen=True, slots=True)
class PersonalMemoryRevocationRequest:
    schema_version: str
    tenant_id: str
    owner_user_id: str
    personal_memory_space_id: str
    proposal_id: str
    patch_id: str
    patch_hash: str
    expected_state_hash: str
    expected_state_version: int
    reason_codes: tuple[Step32ReasonCode, ...]
    effective_at: datetime
    idempotency_key: str
    replay_identity: str = field(init=False)
    request_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != STEP32_SCHEMA_VERSION:
            raise ContractValidationError("unsupported revocation schema")
        for name in ("tenant_id", "owner_user_id", "personal_memory_space_id", "proposal_id", "patch_id"):
            _logical_id(getattr(self, name), name)
        require_sha256_hex(self.patch_hash, "patch_hash")
        require_sha256_hex(self.expected_state_hash, "expected_state_hash")
        if self.expected_state_version != 7:
            raise ContractValidationError("revocation must bind ACTIVE state version 7")
        object.__setattr__(self, "reason_codes", _reason_codes(self.reason_codes))
        object.__setattr__(self, "effective_at", _timestamp(self.effective_at, "effective_at"))
        object.__setattr__(self, "replay_identity", _replay_identity("revocation", self.tenant_id, self.owner_user_id, self.idempotency_key))
        object.__setattr__(self, "request_hash", canonical_sha256(self, exclude_fields=("request_hash",)))


@dataclass(frozen=True, slots=True)
class PersonalMemoryPatchRevocation:
    schema_version: str
    revocation_id: str
    request_hash: str
    replay_identity: str
    tenant_id: str
    owner_user_id: str
    personal_memory_space_id: str
    proposal_id: str
    patch_id: str
    patch_hash: str
    active_state_hash: str
    reason_codes: tuple[Step32ReasonCode, ...]
    actor_type: Step32ActorType
    actor_id: str
    effective_at: datetime
    state: PatchState = PatchState.REVOKED
    state_version: int = STEP32_STATE_VERSION
    content_preserved: bool = True
    deletion_performed: bool = False
    canonical_evidence: bool = False
    revocation_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != STEP32_SCHEMA_VERSION:
            raise ContractValidationError("unsupported revocation record schema")
        for name in ("revocation_id", "replay_identity", "tenant_id", "owner_user_id", "personal_memory_space_id", "proposal_id", "patch_id", "actor_id"):
            _logical_id(getattr(self, name), name)
        for name in ("request_hash", "patch_hash", "active_state_hash"):
            require_sha256_hex(getattr(self, name), name)
        expected_id = "personal-memory-revocation-" + canonical_sha256(
            {
                "replay_identity": self.replay_identity,
                "request_hash": self.request_hash,
            }
        )
        if self.revocation_id != expected_id:
            raise ContractValidationError("revocation identity is detached")
        object.__setattr__(self, "reason_codes", _reason_codes(self.reason_codes))
        if self.actor_type not in {Step32ActorType.HUMAN_OWNER, Step32ActorType.DETERMINISTIC_SYSTEM_POLICY}:
            raise ContractValidationError("revocation actor has authority")
        if self.actor_type is Step32ActorType.HUMAN_OWNER and self.actor_id != self.owner_user_id:
            raise ContractValidationError("revocation actor is not the owner")
        if (
            self.actor_type is Step32ActorType.DETERMINISTIC_SYSTEM_POLICY
            and self.actor_id != STEP32_SYSTEM_POLICY_ACTOR_ID
        ):
            raise ContractValidationError("revocation system actor is not canonical")
        if self.state is not PatchState.REVOKED or self.state_version != STEP32_STATE_VERSION:
            raise ContractValidationError("revocation terminal state is invalid")
        if self.content_preserved is not True or self.deletion_performed is not False or self.canonical_evidence is not False:
            raise ContractValidationError("revocation authority flags are invalid")
        object.__setattr__(self, "effective_at", _timestamp(self.effective_at, "effective_at"))
        object.__setattr__(self, "revocation_hash", canonical_sha256(self, exclude_fields=("revocation_hash",)))


@dataclass(frozen=True, slots=True)
class PersonalMemoryLifecycleExportRequest:
    schema_version: str
    tenant_id: str
    owner_user_id: str
    personal_memory_space_id: str
    slot_hash: str
    expected_state_version: int
    expected_configuration_version: int
    requested_at: datetime
    idempotency_key: str
    replay_identity: str = field(init=False)
    request_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != STEP32_SCHEMA_VERSION:
            raise ContractValidationError("unsupported lifecycle export schema")
        for name in ("tenant_id", "owner_user_id", "personal_memory_space_id"):
            _logical_id(getattr(self, name), name)
        require_sha256_hex(self.slot_hash, "slot_hash")
        if self.expected_state_version < 0 or self.expected_configuration_version < 0:
            raise ContractValidationError("export versions are invalid")
        object.__setattr__(self, "requested_at", _timestamp(self.requested_at, "requested_at"))
        object.__setattr__(self, "replay_identity", _replay_identity("export", self.tenant_id, self.owner_user_id, self.idempotency_key))
        object.__setattr__(self, "request_hash", canonical_sha256(self, exclude_fields=("request_hash",)))


def _safe_export_payload(value: object) -> Mapping[str, Any]:
    frozen = freeze_json(value)
    if not isinstance(frozen, Mapping):
        raise ContractValidationError("export payload must be an object")
    forbidden_key_fragments = {
        "api_key", "apikey", "authorization", "aws_secret", "bearer",
        "client_secret", "credential", "github_token", "password",
        "private_key", "refresh_token", "secret", "session_token",
    }

    def walk(item: object, key: str | None = None) -> None:
        normalized_key = (
            key.lower().replace("-", "_") if key is not None else None
        )
        if normalized_key is not None and any(
            fragment in normalized_key
            for fragment in forbidden_key_fragments
        ):
            raise ContractValidationError(
                "export contains a forbidden secret field"
            )
        if isinstance(item, Mapping):
            for child_key, child in item.items():
                if not isinstance(child_key, str):
                    raise ContractValidationError("export keys must be strings")
                walk(child, child_key)
        elif isinstance(item, (tuple, list)):
            for child in item:
                walk(child)
        elif isinstance(item, str):
            if (
                item.startswith(("/", "~/", "file://"))
                or re.match(r"^[A-Za-z]:[\\/]", item) is not None
            ):
                raise ContractValidationError(
                    "export contains a machine-local path"
                )
            if re.search(r"(?i)\bbearer\s+\S+", item) is not None:
                raise ContractValidationError(
                    "export contains a forbidden authorization value"
                )

    walk(frozen)
    try:
        assert_secret_free(
            frozen,
            surface="Personal Memory owner export",
            reject_machine_paths=True,
        )
    except ValueError as error:
        raise ContractValidationError(
            "export contains forbidden secret material"
        ) from error
    return frozen


@dataclass(frozen=True, slots=True)
class PersonalMemoryLifecycleExportRecord:
    record_type: str
    record_id: str
    payload: Mapping[str, Any]
    record_hash: str = field(init=False)

    def __post_init__(self) -> None:
        _text(self.record_type, "record_type", 128)
        _logical_id(self.record_id, "record_id")
        object.__setattr__(self, "payload", _safe_export_payload(self.payload))
        object.__setattr__(self, "record_hash", canonical_sha256(self, exclude_fields=("record_hash",)))


@dataclass(frozen=True, slots=True)
class PersonalMemoryLifecycleExportBundle:
    export_schema_version: str
    export_id: str
    request_hash: str
    replay_identity: str
    tenant_id: str
    owner_user_id: str
    personal_memory_space_id: str
    slot_hash: str
    records: tuple[PersonalMemoryLifecycleExportRecord, ...]
    exported_at: datetime
    deterministic_order: bool = True
    owner_private: bool = True
    shared_promotion: bool = False
    canonical_evidence: bool = False
    bundle_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.export_schema_version != STEP32_EXPORT_SCHEMA_VERSION:
            raise ContractValidationError("unsupported lifecycle export bundle")
        for name in ("export_id", "replay_identity", "tenant_id", "owner_user_id", "personal_memory_space_id"):
            _logical_id(getattr(self, name), name)
        require_sha256_hex(self.request_hash, "request_hash")
        require_sha256_hex(self.slot_hash, "slot_hash")
        expected_id = "personal-memory-export-" + canonical_sha256(
            {
                "replay_identity": self.replay_identity,
                "request_hash": self.request_hash,
            }
        )
        if self.export_id != expected_id:
            raise ContractValidationError("export identity is detached")
        if not isinstance(self.records, (tuple, list)):
            raise ContractValidationError("export records must be ordered")
        records = tuple(self.records)
        if len(records) > MAXIMUM_EXPORT_RECORDS or any(not isinstance(item, PersonalMemoryLifecycleExportRecord) for item in records):
            raise ContractValidationError("export record set exceeds its bound")
        expected = tuple(sorted(records, key=lambda item: (item.record_type, item.record_id, item.record_hash)))
        if records != expected:
            raise ContractValidationError("export records are not deterministic")
        if self.deterministic_order is not True or self.owner_private is not True or self.shared_promotion is not False or self.canonical_evidence is not False:
            raise ContractValidationError("export authority flags are invalid")
        object.__setattr__(self, "records", records)
        object.__setattr__(self, "exported_at", _timestamp(self.exported_at, "exported_at"))
        object.__setattr__(self, "bundle_hash", canonical_sha256(self, exclude_fields=("bundle_hash",)))
        if len(canonical_json_bytes(self)) > MAXIMUM_EXPORT_BYTES:
            raise ContractValidationError("export bundle exceeds byte bound")


@dataclass(frozen=True, slots=True)
class PersonalMemoryDeletionRequest:
    schema_version: str
    tenant_id: str
    owner_user_id: str
    personal_memory_space_id: str
    slot_hash: str
    expected_slot_state: PersonalMemorySpaceState
    expected_slot_state_version: int
    expected_slot_configuration_version: int
    proposal_id: str
    patch_id: str
    patch_hash: str
    active_state_hash: str
    requested_at: datetime
    idempotency_key: str
    replay_identity: str = field(init=False)
    request_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != STEP32_SCHEMA_VERSION:
            raise ContractValidationError("unsupported deletion schema")
        for name in ("tenant_id", "owner_user_id", "personal_memory_space_id", "proposal_id", "patch_id"):
            _logical_id(getattr(self, name), name)
        for name in ("slot_hash", "patch_hash", "active_state_hash"):
            require_sha256_hex(getattr(self, name), name)
        if self.expected_slot_state is not PersonalMemorySpaceState.DELETED_PENDING:
            raise ContractValidationError("logical deletion requires DELETED_PENDING")
        if self.expected_slot_state_version < 1 or self.expected_slot_configuration_version < 0:
            raise ContractValidationError("deletion versions are invalid")
        object.__setattr__(self, "requested_at", _timestamp(self.requested_at, "requested_at"))
        object.__setattr__(self, "replay_identity", _replay_identity("delete", self.tenant_id, self.owner_user_id, self.idempotency_key))
        object.__setattr__(self, "request_hash", canonical_sha256(self, exclude_fields=("request_hash",)))


@dataclass(frozen=True, slots=True)
class PersonalMemoryDeletionResult:
    schema_version: str
    deletion_id: str
    request_hash: str
    replay_identity: str
    tenant_id: str
    owner_user_id: str
    personal_memory_space_id: str
    proposal_id: str
    patch_id: str
    patch_hash: str
    tombstone_hash: str
    deleted_at: datetime
    slot_state: PersonalMemorySpaceState = PersonalMemorySpaceState.DELETED
    logical_delete: bool = True
    physical_delete: bool = False
    revoked: bool = False
    shared_artifacts_mutated: bool = False
    canonical_evidence: bool = False
    result_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != STEP32_SCHEMA_VERSION:
            raise ContractValidationError("unsupported deletion result schema")
        for name in ("deletion_id", "replay_identity", "tenant_id", "owner_user_id", "personal_memory_space_id", "proposal_id", "patch_id"):
            _logical_id(getattr(self, name), name)
        for name in ("request_hash", "patch_hash", "tombstone_hash"):
            require_sha256_hex(getattr(self, name), name)
        expected_id = "personal-memory-deletion-" + canonical_sha256(
            {
                "replay_identity": self.replay_identity,
                "request_hash": self.request_hash,
            }
        )
        if self.deletion_id != expected_id:
            raise ContractValidationError("deletion identity is detached")
        if self.slot_state is not PersonalMemorySpaceState.DELETED:
            raise ContractValidationError("deletion result must close the slot")
        if self.logical_delete is not True or self.physical_delete is not False or self.revoked is not False or self.shared_artifacts_mutated is not False or self.canonical_evidence is not False:
            raise ContractValidationError("deletion semantics are invalid")
        object.__setattr__(self, "deleted_at", _timestamp(self.deleted_at, "deleted_at"))
        object.__setattr__(self, "result_hash", canonical_sha256(self, exclude_fields=("result_hash",)))


@dataclass(frozen=True, slots=True)
class DeidentificationPolicy:
    schema_version: str = STEP32_SCHEMA_VERSION
    policy_id: str = STEP32_DEIDENTIFICATION_POLICY_ID
    policy_version: str = STEP32_DEIDENTIFICATION_POLICY_VERSION
    deterministic_only: bool = True
    model_privacy_authority: bool = False
    human_review_always_required: bool = True
    policy_digest: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != STEP32_SCHEMA_VERSION:
            raise ContractValidationError("unsupported de-identification policy")
        _logical_id(self.policy_id, "policy_id")
        _logical_id(self.policy_version, "policy_version")
        if self.deterministic_only is not True or self.model_privacy_authority is not False or self.human_review_always_required is not True:
            raise ContractValidationError("de-identification policy weakens review")
        object.__setattr__(self, "policy_digest", canonical_sha256(self, exclude_fields=("policy_digest",)))


@dataclass(frozen=True, slots=True)
class DeidentificationAssessment:
    schema_version: str
    source_statement_sha256: str
    candidate_shared_statement: str
    candidate_shared_statement_sha256: str
    detected_categories: tuple[str, ...]
    status: DeidentificationStatus
    decision: DeidentificationDecision
    policy_id: str
    policy_version: str
    policy_digest: str
    deterministic: bool
    model_certified: bool
    review_required: bool
    assessment_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != STEP32_SCHEMA_VERSION:
            raise ContractValidationError("unsupported de-identification assessment")
        require_sha256_hex(self.source_statement_sha256, "source_statement_sha256")
        statement = _text(self.candidate_shared_statement, "candidate_shared_statement", MAXIMUM_LIFECYCLE_TEXT_BYTES)
        if canonical_sha256(statement) != self.candidate_shared_statement_sha256:
            raise ContractValidationError("candidate shared statement hash differs")
        if not isinstance(self.detected_categories, (tuple, list)):
            raise ContractValidationError("detected categories must be ordered")
        categories = tuple(self.detected_categories)
        if categories != tuple(sorted(set(categories))) or any(not isinstance(item, str) for item in categories):
            raise ContractValidationError("detected categories are invalid")
        if not isinstance(self.status, DeidentificationStatus) or not isinstance(self.decision, DeidentificationDecision):
            raise ContractValidationError("de-identification result is not typed")
        _logical_id(self.policy_id, "policy_id")
        _logical_id(self.policy_version, "policy_version")
        require_sha256_hex(self.policy_digest, "policy_digest")
        policy = DeidentificationPolicy()
        if (
            self.policy_id != policy.policy_id
            or self.policy_version != policy.policy_version
            or self.policy_digest != policy.policy_digest
        ):
            raise ContractValidationError(
                "de-identification policy identity is detached"
            )
        if self.deterministic is not True or self.model_certified is not False or self.review_required is not True:
            raise ContractValidationError("privacy review boundary is invalid")
        object.__setattr__(self, "detected_categories", categories)
        object.__setattr__(self, "assessment_hash", canonical_sha256(self, exclude_fields=("assessment_hash",)))


@dataclass(frozen=True, slots=True)
class SharedMemoryPromotionConsent:
    schema_version: str
    consent_id: str
    tenant_id: str
    owner_user_id: str
    personal_memory_space_id: str
    source_patch_id: str
    source_patch_hash: str
    candidate_shared_statement_sha256: str
    deidentification_policy_digest: str
    target_hat_id: str
    consent_nonce: str
    actor_type: Step32ActorType
    actor_id: str
    consented_at: datetime
    consent_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != STEP32_SCHEMA_VERSION:
            raise ContractValidationError("unsupported promotion consent schema")
        for name in ("consent_id", "tenant_id", "owner_user_id", "personal_memory_space_id", "source_patch_id", "target_hat_id", "consent_nonce", "actor_id"):
            _logical_id(getattr(self, name), name)
        require_sha256_hex(self.source_patch_hash, "source_patch_hash")
        require_sha256_hex(self.candidate_shared_statement_sha256, "candidate_shared_statement_sha256")
        require_sha256_hex(
            self.deidentification_policy_digest,
            "deidentification_policy_digest",
        )
        expected_id = "shared-memory-promotion-consent-" + canonical_sha256(
            {
                "tenant_id": self.tenant_id,
                "owner_user_id": self.owner_user_id,
                "patch_id": self.source_patch_id,
                "patch_hash": self.source_patch_hash,
                "candidate_shared_statement_sha256": (
                    self.candidate_shared_statement_sha256
                ),
                "deidentification_policy_digest": (
                    self.deidentification_policy_digest
                ),
                "target_hat_id": self.target_hat_id,
                "consent_nonce": self.consent_nonce,
            }
        )
        if self.consent_id != expected_id:
            raise ContractValidationError("promotion consent identity is detached")
        if self.actor_type is not Step32ActorType.HUMAN_OWNER or self.actor_id != self.owner_user_id:
            raise ContractValidationError("shared promotion requires separate owner consent")
        object.__setattr__(self, "consented_at", _timestamp(self.consented_at, "consented_at"))
        object.__setattr__(self, "consent_hash", canonical_sha256(self, exclude_fields=("consent_hash",)))


@dataclass(frozen=True, slots=True)
class SharedMemoryPromotionRequest:
    schema_version: str
    tenant_id: str
    owner_user_id: str
    personal_memory_space_id: str
    source_proposal_id: str
    source_patch_id: str
    source_patch_hash: str
    source_state_hash: str
    target_hat_id: str
    promotion_purpose: str
    promotion_scope: tuple[ScopeDimension, ...]
    deidentification: DeidentificationAssessment
    owner_consent: SharedMemoryPromotionConsent
    canonical_evidence_compatibility: CanonicalEvidenceCompatibility
    reason_codes: tuple[Step32ReasonCode, ...]
    requested_at: datetime
    idempotency_key: str
    replay_identity: str = field(init=False)
    request_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != STEP32_SCHEMA_VERSION:
            raise ContractValidationError("unsupported promotion request schema")
        for name in ("tenant_id", "owner_user_id", "personal_memory_space_id", "source_proposal_id", "source_patch_id", "target_hat_id"):
            _logical_id(getattr(self, name), name)
        _text(self.promotion_purpose, "promotion_purpose", 1024)
        require_sha256_hex(self.source_patch_hash, "source_patch_hash")
        require_sha256_hex(self.source_state_hash, "source_state_hash")
        object.__setattr__(self, "promotion_scope", _scope(self.promotion_scope))
        if not isinstance(self.deidentification, DeidentificationAssessment) or not isinstance(self.owner_consent, SharedMemoryPromotionConsent):
            raise ContractValidationError("promotion privacy/consent inputs must be typed")
        verify_deidentification_assessment(self.deidentification)
        verify_shared_promotion_consent(self.owner_consent)
        if self.owner_consent.tenant_id != self.tenant_id or self.owner_consent.owner_user_id != self.owner_user_id or self.owner_consent.personal_memory_space_id != self.personal_memory_space_id or self.owner_consent.source_patch_id != self.source_patch_id or self.owner_consent.source_patch_hash != self.source_patch_hash or self.owner_consent.target_hat_id != self.target_hat_id or self.owner_consent.candidate_shared_statement_sha256 != self.deidentification.candidate_shared_statement_sha256 or self.owner_consent.deidentification_policy_digest != self.deidentification.policy_digest:
            raise ContractValidationError("promotion consent is detached")
        if not isinstance(self.canonical_evidence_compatibility, CanonicalEvidenceCompatibility):
            raise ContractValidationError("canonical evidence compatibility must be typed")
        object.__setattr__(self, "reason_codes", _reason_codes(self.reason_codes))
        object.__setattr__(self, "requested_at", _timestamp(self.requested_at, "requested_at"))
        if self.requested_at < self.owner_consent.consented_at:
            raise ContractValidationError("promotion request predates owner consent")
        object.__setattr__(self, "replay_identity", _replay_identity("shared-promotion", self.tenant_id, self.owner_user_id, self.idempotency_key))
        object.__setattr__(self, "request_hash", canonical_sha256(self, exclude_fields=("request_hash",)))


@dataclass(frozen=True, slots=True)
class SharedMemoryPromotionProposal:
    schema_version: str
    promotion_id: str
    request_hash: str
    replay_identity: str
    tenant_id: str
    owner_user_id: str
    personal_memory_space_id: str
    source_proposal_id: str
    source_patch_id: str
    source_patch_hash: str
    source_state_hash: str
    target_hat_id: str
    promotion_purpose: str
    candidate_shared_statement: str
    candidate_shared_statement_sha256: str
    promotion_scope: tuple[ScopeDimension, ...]
    deidentification: DeidentificationAssessment
    owner_consent_hash: str
    canonical_evidence_compatibility: CanonicalEvidenceCompatibility
    base_proposal: SharedPromotionProposal
    reason_codes: tuple[Step32ReasonCode, ...]
    review_required: bool
    shared_active: bool
    source_registry_published: bool
    canonical_evidence: bool
    created_at: datetime
    proposal_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != STEP32_SCHEMA_VERSION:
            raise ContractValidationError("unsupported shared promotion schema")
        for name in ("promotion_id", "replay_identity", "tenant_id", "owner_user_id", "personal_memory_space_id", "source_proposal_id", "source_patch_id", "target_hat_id"):
            _logical_id(getattr(self, name), name)
        require_sha256_hex(self.request_hash, "request_hash")
        require_sha256_hex(self.source_patch_hash, "source_patch_hash")
        require_sha256_hex(self.source_state_hash, "source_state_hash")
        require_sha256_hex(self.candidate_shared_statement_sha256, "candidate_shared_statement_sha256")
        require_sha256_hex(self.owner_consent_hash, "owner_consent_hash")
        expected_id = "shared-memory-promotion-" + canonical_sha256(
            {
                "replay_identity": self.replay_identity,
                "request_hash": self.request_hash,
            }
        )
        if self.promotion_id != expected_id:
            raise ContractValidationError("promotion identity is detached")
        statement = _text(self.candidate_shared_statement, "candidate_shared_statement", MAXIMUM_LIFECYCLE_TEXT_BYTES)
        if canonical_sha256(statement) != self.candidate_shared_statement_sha256:
            raise ContractValidationError("shared statement hash differs")
        object.__setattr__(self, "promotion_scope", _scope(self.promotion_scope))
        verify_deidentification_assessment(self.deidentification)
        if (
            self.candidate_shared_statement
            != self.deidentification.candidate_shared_statement
            or self.candidate_shared_statement_sha256
            != self.deidentification.candidate_shared_statement_sha256
        ):
            raise ContractValidationError(
                "shared statement is detached from de-identification"
            )
        if not isinstance(self.canonical_evidence_compatibility, CanonicalEvidenceCompatibility):
            raise ContractValidationError("canonical evidence result must be typed")
        if not isinstance(self.base_proposal, SharedPromotionProposal):
            raise ContractValidationError("canonical shared-promotion base is required")
        verify_shared_promotion_hash(self.base_proposal)
        expected_classification = (
            PrivateDataClassification.NONE
            if not self.deidentification.detected_categories
            else PrivateDataClassification.PERSONAL
        )
        if self.base_proposal.state is not SharedPromotionState.SHARED_PROMOTION_PROPOSED or self.base_proposal.shared_promotion_proposal_id != self.promotion_id or self.base_proposal.originating_personal_patch_id != self.source_patch_id or self.base_proposal.originating_personal_patch_hash != self.source_patch_hash or self.base_proposal.originating_personal_memory_space_id != self.personal_memory_space_id or self.base_proposal.owner_user_id != self.owner_user_id or self.base_proposal.tenant_id != self.tenant_id or self.base_proposal.target_hat_id != self.target_hat_id or self.base_proposal.hat_scope_dimensions != self.promotion_scope or self.base_proposal.private_data_classification is not expected_classification or self.base_proposal.deidentification_status is not self.deidentification.status or self.base_proposal.independent_evidence_references or self.base_proposal.independent_evidence_validated or self.base_proposal.domain_approval_id is not None or self.base_proposal.shared_commit_id is not None or self.base_proposal.created_at != self.created_at or self.base_proposal.updated_at != self.created_at:
            raise ContractValidationError("shared-promotion base is detached")
        object.__setattr__(self, "reason_codes", _reason_codes(self.reason_codes))
        if self.review_required is not True or self.shared_active is not False or self.source_registry_published is not False or self.canonical_evidence is not False:
            raise ContractValidationError("promotion proposal crossed its authority boundary")
        object.__setattr__(self, "created_at", _timestamp(self.created_at, "created_at"))
        object.__setattr__(self, "proposal_hash", canonical_sha256(self, exclude_fields=("proposal_hash",)))


_EMAIL = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
_PRIVATE_ID = re.compile(r"(?i)\b(?:account|customer|user|session|private)[-_ :#]*[A-Z0-9]{6,}\b")
_ADDRESS = re.compile(r"(?i)\b\d{1,5}\s+[\wÄÖÜäöüß.-]+(?:\s+[\wÄÖÜäöüß.-]+){0,3}\s+(?:street|st\.?|road|rd\.?|avenue|ave\.?|straße|str\.?)\b")


def load_deidentification_policy() -> DeidentificationPolicy:
    return DeidentificationPolicy()


def assess_shared_promotion_privacy(
    source_statement: str,
    *,
    private_identifiers: Sequence[str] = (),
) -> DeidentificationAssessment:
    source = _text(source_statement, "source_statement", MAXIMUM_LIFECYCLE_TEXT_BYTES)
    if not isinstance(private_identifiers, (tuple, list)) or len(private_identifiers) > MAXIMUM_PRIVATE_IDENTIFIERS:
        raise ContractValidationError("private identifier set is invalid")
    identifiers = tuple(sorted(set(_text(item, "private_identifier", 255) for item in private_identifiers)))
    candidate = source
    categories: set[str] = set()
    if _EMAIL.search(candidate):
        candidate = _EMAIL.sub("[REDACTED_EMAIL]", candidate)
        categories.add("EMAIL")
    if _PRIVATE_ID.search(candidate):
        candidate = _PRIVATE_ID.sub("[REDACTED_PRIVATE_ID]", candidate)
        categories.add("PRIVATE_ID")
    if _ADDRESS.search(candidate):
        candidate = _ADDRESS.sub("[REDACTED_ADDRESS]", candidate)
        categories.add("ADDRESS")
    for identifier in identifiers:
        if identifier in candidate:
            candidate = candidate.replace(identifier, "[REDACTED_OWNER_IDENTIFIER]")
            categories.add("OWNER_IDENTIFIER")
    policy = load_deidentification_policy()
    status = DeidentificationStatus.NOT_REQUIRED if not categories else DeidentificationStatus.COMPLETE
    decision = DeidentificationDecision.PASS if not categories else DeidentificationDecision.REVIEW_REQUIRED
    return DeidentificationAssessment(
        schema_version=STEP32_SCHEMA_VERSION,
        source_statement_sha256=canonical_sha256(source),
        candidate_shared_statement=candidate,
        candidate_shared_statement_sha256=canonical_sha256(candidate),
        detected_categories=tuple(sorted(categories)),
        status=status,
        decision=decision,
        policy_id=policy.policy_id,
        policy_version=policy.policy_version,
        policy_digest=policy.policy_digest,
        deterministic=True,
        model_certified=False,
        review_required=True,
    )


def build_supersession_request(
    old_state: PersonalMemoryPatchLifecycleState,
    new_state: PersonalMemoryPatchLifecycleState,
    *,
    reason_codes: Sequence[Step32ReasonCode],
    effective_at: datetime,
    idempotency_key: str,
) -> PersonalMemorySupersessionRequest:
    old = _active_state(old_state)
    new = _active_state(new_state)
    if not _same_owner_slot(old, new):
        raise PersonalMemoryStep32Error(Step32ReasonCode.SUPERSESSION_OWNER_MISMATCH)
    if canonical_json_bytes(old.committed_patch.patch_scope) != canonical_json_bytes(new.committed_patch.patch_scope):
        raise PersonalMemoryStep32Error(Step32ReasonCode.SUPERSESSION_SCOPE_MISMATCH)
    if effective_at < old.updated_at or effective_at < new.updated_at:
        raise PersonalMemoryStep32Error(Step32ReasonCode.SUPERSESSION_CONFLICT)
    return PersonalMemorySupersessionRequest(
        schema_version=STEP32_SCHEMA_VERSION,
        tenant_id=old.proposal.tenant_id,
        owner_user_id=old.proposal.owner_user_id,
        personal_memory_space_id=old.proposal.personal_memory_space_id,
        old_proposal_id=old.proposal.proposal_id,
        old_patch_id=old.committed_patch.patch_id,
        old_patch_hash=old.committed_patch.patch_hash,
        old_state_hash=old.state_hash,
        new_proposal_id=new.proposal.proposal_id,
        new_patch_id=new.committed_patch.patch_id,
        new_patch_hash=new.committed_patch.patch_hash,
        new_state_hash=new.state_hash,
        reason_codes=tuple(reason_codes),
        effective_at=effective_at,
        idempotency_key=idempotency_key,
    )


def create_patch_supersession(
    request: PersonalMemorySupersessionRequest,
    old_state: PersonalMemoryPatchLifecycleState,
    new_state: PersonalMemoryPatchLifecycleState,
    *,
    authenticated_owner_user_id: str,
) -> PersonalMemoryPatchSupersession:
    verify_supersession_request(request)
    old = _active_state(old_state)
    new = _active_state(new_state)
    if authenticated_owner_user_id != request.owner_user_id or not _same_owner_slot(old, new):
        raise PersonalMemoryStep32Error(Step32ReasonCode.SUPERSESSION_OWNER_MISMATCH)
    expected = build_supersession_request(old, new, reason_codes=request.reason_codes, effective_at=request.effective_at, idempotency_key=request.idempotency_key)
    if expected != request:
        raise PersonalMemoryStep32Error(Step32ReasonCode.SUPERSESSION_CONFLICT)
    supersession_id = "personal-memory-supersession-" + canonical_sha256({"replay_identity": request.replay_identity, "request_hash": request.request_hash})
    return PersonalMemoryPatchSupersession(
        schema_version=STEP32_SCHEMA_VERSION,
        supersession_id=supersession_id,
        request_hash=request.request_hash,
        replay_identity=request.replay_identity,
        tenant_id=request.tenant_id,
        owner_user_id=request.owner_user_id,
        personal_memory_space_id=request.personal_memory_space_id,
        old_proposal_id=request.old_proposal_id,
        old_patch_id=request.old_patch_id,
        old_patch_hash=request.old_patch_hash,
        old_state_hash=request.old_state_hash,
        new_proposal_id=request.new_proposal_id,
        new_patch_id=request.new_patch_id,
        new_patch_hash=request.new_patch_hash,
        new_state_hash=request.new_state_hash,
        patch_scope=old.committed_patch.patch_scope,
        reason_codes=request.reason_codes,
        actor_type=Step32ActorType.HUMAN_OWNER,
        actor_id=authenticated_owner_user_id,
        effective_at=request.effective_at,
    )


def build_revocation_request(
    state: PersonalMemoryPatchLifecycleState,
    *,
    reason_codes: Sequence[Step32ReasonCode],
    effective_at: datetime,
    idempotency_key: str,
) -> PersonalMemoryRevocationRequest:
    active = _active_state(state)
    if effective_at < active.updated_at:
        raise PersonalMemoryStep32Error(Step32ReasonCode.REVOCATION_STATE_INVALID)
    return PersonalMemoryRevocationRequest(
        schema_version=STEP32_SCHEMA_VERSION,
        tenant_id=active.proposal.tenant_id,
        owner_user_id=active.proposal.owner_user_id,
        personal_memory_space_id=active.proposal.personal_memory_space_id,
        proposal_id=active.proposal.proposal_id,
        patch_id=active.committed_patch.patch_id,
        patch_hash=active.committed_patch.patch_hash,
        expected_state_hash=active.state_hash,
        expected_state_version=active.state_version,
        reason_codes=tuple(reason_codes),
        effective_at=effective_at,
        idempotency_key=idempotency_key,
    )


def create_patch_revocation(
    request: PersonalMemoryRevocationRequest,
    state: PersonalMemoryPatchLifecycleState,
    *,
    actor_type: Step32ActorType,
    authenticated_actor_id: str,
) -> PersonalMemoryPatchRevocation:
    verify_revocation_request(request)
    active = _active_state(state)
    if actor_type not in {Step32ActorType.HUMAN_OWNER, Step32ActorType.DETERMINISTIC_SYSTEM_POLICY}:
        raise PersonalMemoryStep32Error(Step32ReasonCode.REVOCATION_OWNER_MISMATCH)
    if actor_type is Step32ActorType.HUMAN_OWNER and authenticated_actor_id != active.proposal.owner_user_id:
        raise PersonalMemoryStep32Error(Step32ReasonCode.REVOCATION_OWNER_MISMATCH)
    expected = build_revocation_request(active, reason_codes=request.reason_codes, effective_at=request.effective_at, idempotency_key=request.idempotency_key)
    if expected != request:
        raise PersonalMemoryStep32Error(Step32ReasonCode.REVOCATION_STATE_INVALID)
    revocation_id = "personal-memory-revocation-" + canonical_sha256({"replay_identity": request.replay_identity, "request_hash": request.request_hash})
    return PersonalMemoryPatchRevocation(
        schema_version=STEP32_SCHEMA_VERSION,
        revocation_id=revocation_id,
        request_hash=request.request_hash,
        replay_identity=request.replay_identity,
        tenant_id=request.tenant_id,
        owner_user_id=request.owner_user_id,
        personal_memory_space_id=request.personal_memory_space_id,
        proposal_id=request.proposal_id,
        patch_id=request.patch_id,
        patch_hash=request.patch_hash,
        active_state_hash=request.expected_state_hash,
        reason_codes=request.reason_codes,
        actor_type=actor_type,
        actor_id=authenticated_actor_id,
        effective_at=request.effective_at,
    )


def build_lifecycle_export_request(
    slot: PersonalMemoryHatSlot,
    *,
    requested_at: datetime,
    idempotency_key: str,
) -> PersonalMemoryLifecycleExportRequest:
    verify_slot_hash(slot)
    if slot.state is PersonalMemorySpaceState.DELETED:
        raise PersonalMemoryStep32Error(Step32ReasonCode.DELETE_STATE_INVALID)
    return PersonalMemoryLifecycleExportRequest(
        schema_version=STEP32_SCHEMA_VERSION,
        tenant_id=slot.tenant_id,
        owner_user_id=slot.owner_user_id,
        personal_memory_space_id=slot.personal_memory_space_id,
        slot_hash=slot.slot_hash,
        expected_state_version=slot.state_version,
        expected_configuration_version=slot.configuration_version,
        requested_at=requested_at,
        idempotency_key=idempotency_key,
    )


def build_lifecycle_export_bundle(
    request: PersonalMemoryLifecycleExportRequest,
    slot: PersonalMemoryHatSlot,
    records: Sequence[PersonalMemoryLifecycleExportRecord],
) -> PersonalMemoryLifecycleExportBundle:
    verify_lifecycle_export_request(request)
    verify_slot_hash(slot)
    if (request.tenant_id, request.owner_user_id, request.personal_memory_space_id, request.slot_hash, request.expected_state_version, request.expected_configuration_version) != (slot.tenant_id, slot.owner_user_id, slot.personal_memory_space_id, slot.slot_hash, slot.state_version, slot.configuration_version):
        raise PersonalMemoryStep32Error(Step32ReasonCode.EXPORT_OWNER_MISMATCH)
    def require_owner_scope(value: object) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                if key == "tenant_id" and child != request.tenant_id:
                    raise PersonalMemoryStep32Error(
                        Step32ReasonCode.EXPORT_OWNER_MISMATCH
                    )
                if (
                    key in {"owner_user_id", "user_id"}
                    and child != request.owner_user_id
                ):
                    raise PersonalMemoryStep32Error(
                        Step32ReasonCode.EXPORT_OWNER_MISMATCH
                    )
                if (
                    key == "personal_memory_space_id"
                    and child != request.personal_memory_space_id
                ):
                    raise PersonalMemoryStep32Error(
                        Step32ReasonCode.EXPORT_OWNER_MISMATCH
                    )
                require_owner_scope(child)
        elif isinstance(value, (tuple, list)):
            for child in value:
                require_owner_scope(child)

    for record in records:
        if not isinstance(record, PersonalMemoryLifecycleExportRecord):
            raise ContractValidationError("export record has invalid type")
        require_owner_scope(record.payload)
    export_id = "personal-memory-export-" + canonical_sha256({"replay_identity": request.replay_identity, "request_hash": request.request_hash})
    return PersonalMemoryLifecycleExportBundle(
        export_schema_version=STEP32_EXPORT_SCHEMA_VERSION,
        export_id=export_id,
        request_hash=request.request_hash,
        replay_identity=request.replay_identity,
        tenant_id=request.tenant_id,
        owner_user_id=request.owner_user_id,
        personal_memory_space_id=request.personal_memory_space_id,
        slot_hash=request.slot_hash,
        records=tuple(sorted(records, key=lambda item: (item.record_type, item.record_id, item.record_hash))),
        exported_at=request.requested_at,
    )


def build_deletion_request(
    state: PersonalMemoryPatchLifecycleState,
    slot: PersonalMemoryHatSlot,
    *,
    requested_at: datetime,
    idempotency_key: str,
) -> PersonalMemoryDeletionRequest:
    active = _active_state(state)
    verify_slot_hash(slot)
    if slot.state is not PersonalMemorySpaceState.DELETED_PENDING:
        raise PersonalMemoryStep32Error(Step32ReasonCode.DELETE_STATE_INVALID)
    if (active.proposal.tenant_id, active.proposal.owner_user_id, active.proposal.personal_memory_space_id) != (slot.tenant_id, slot.owner_user_id, slot.personal_memory_space_id):
        raise PersonalMemoryStep32Error(Step32ReasonCode.DELETE_OWNER_MISMATCH)
    if requested_at < active.updated_at or requested_at < slot.updated_at:
        raise PersonalMemoryStep32Error(Step32ReasonCode.DELETE_STATE_INVALID)
    return PersonalMemoryDeletionRequest(
        schema_version=STEP32_SCHEMA_VERSION,
        tenant_id=slot.tenant_id,
        owner_user_id=slot.owner_user_id,
        personal_memory_space_id=slot.personal_memory_space_id,
        slot_hash=slot.slot_hash,
        expected_slot_state=slot.state,
        expected_slot_state_version=slot.state_version,
        expected_slot_configuration_version=slot.configuration_version,
        proposal_id=active.proposal.proposal_id,
        patch_id=active.committed_patch.patch_id,
        patch_hash=active.committed_patch.patch_hash,
        active_state_hash=active.state_hash,
        requested_at=requested_at,
        idempotency_key=idempotency_key,
    )


def complete_logical_deletion(
    request: PersonalMemoryDeletionRequest,
    state: PersonalMemoryPatchLifecycleState,
    slot: PersonalMemoryHatSlot,
    *,
    authenticated_owner_user_id: str,
) -> PersonalMemoryDeletionResult:
    verify_deletion_request(request)
    expected = build_deletion_request(state, slot, requested_at=request.requested_at, idempotency_key=request.idempotency_key)
    if expected != request or authenticated_owner_user_id != request.owner_user_id:
        raise PersonalMemoryStep32Error(Step32ReasonCode.DELETE_OWNER_MISMATCH)
    return deletion_result_for_request(request)


def deletion_result_for_request(
    request: PersonalMemoryDeletionRequest,
) -> PersonalMemoryDeletionResult:
    """Build the deterministic logical tombstone, including for exact replay."""

    verify_deletion_request(request)
    deletion_id = "personal-memory-deletion-" + canonical_sha256({"replay_identity": request.replay_identity, "request_hash": request.request_hash})
    tombstone_hash = canonical_sha256({"deletion_id": deletion_id, "patch_id": request.patch_id, "patch_hash": request.patch_hash, "slot_hash": request.slot_hash, "deleted_at": request.requested_at, "logical_delete": True})
    return PersonalMemoryDeletionResult(
        schema_version=STEP32_SCHEMA_VERSION,
        deletion_id=deletion_id,
        request_hash=request.request_hash,
        replay_identity=request.replay_identity,
        tenant_id=request.tenant_id,
        owner_user_id=request.owner_user_id,
        personal_memory_space_id=request.personal_memory_space_id,
        proposal_id=request.proposal_id,
        patch_id=request.patch_id,
        patch_hash=request.patch_hash,
        tombstone_hash=tombstone_hash,
        deleted_at=request.requested_at,
    )


def parse_lifecycle_export_bundle(value: object) -> PersonalMemoryLifecycleExportBundle:
    if not isinstance(value, Mapping):
        raise ContractValidationError("serialized lifecycle export must be an object")
    expected = frozenset(PersonalMemoryLifecycleExportBundle.__dataclass_fields__)
    if frozenset(value) != expected:
        raise ContractValidationError("serialized lifecycle export fields differ")
    raw_records = value["records"]
    if not isinstance(raw_records, (tuple, list)):
        raise ContractValidationError("serialized export records must be an array")
    records: list[PersonalMemoryLifecycleExportRecord] = []
    record_fields = frozenset(PersonalMemoryLifecycleExportRecord.__dataclass_fields__)
    for raw in raw_records:
        if not isinstance(raw, Mapping) or frozenset(raw) != record_fields:
            raise ContractValidationError("serialized export record fields differ")
        record = PersonalMemoryLifecycleExportRecord(
            record_type=raw["record_type"],
            record_id=raw["record_id"],
            payload=raw["payload"],
        )
        if record.record_hash != raw["record_hash"]:
            raise IntegrityError("serialized export record hash differs")
        records.append(record)
    raw_time = value["exported_at"]
    if not isinstance(raw_time, str):
        raise ContractValidationError("serialized exported_at must be text")
    try:
        exported_at = datetime.fromisoformat(raw_time.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContractValidationError("serialized exported_at is invalid") from exc
    bundle = PersonalMemoryLifecycleExportBundle(
        export_schema_version=value["export_schema_version"],
        export_id=value["export_id"],
        request_hash=value["request_hash"],
        replay_identity=value["replay_identity"],
        tenant_id=value["tenant_id"],
        owner_user_id=value["owner_user_id"],
        personal_memory_space_id=value["personal_memory_space_id"],
        slot_hash=value["slot_hash"],
        records=tuple(records),
        exported_at=exported_at,
        deterministic_order=value["deterministic_order"],
        owner_private=value["owner_private"],
        shared_promotion=value["shared_promotion"],
        canonical_evidence=value["canonical_evidence"],
    )
    if bundle.bundle_hash != value["bundle_hash"]:
        raise IntegrityError("serialized lifecycle export bundle hash differs")
    return bundle


def build_shared_promotion_consent(
    state: PersonalMemoryPatchLifecycleState,
    assessment: DeidentificationAssessment,
    *,
    target_hat_id: str,
    consent_nonce: str,
    authenticated_owner_user_id: str,
    consented_at: datetime,
) -> SharedMemoryPromotionConsent:
    active = _active_state(state)
    verify_deidentification_assessment(assessment)
    expected_assessment = assess_shared_promotion_privacy(
        active.committed_patch.patch_statement,
        private_identifiers=(active.proposal.owner_user_id,),
    )
    if assessment != expected_assessment:
        raise PersonalMemoryStep32Error(
            Step32ReasonCode.SHARED_PROMOTION_PRIVACY_REVIEW_REQUIRED
        )
    if authenticated_owner_user_id != active.proposal.owner_user_id:
        raise PersonalMemoryStep32Error(Step32ReasonCode.SHARED_PROMOTION_OWNER_CONSENT_REQUIRED)
    consent_id = "shared-memory-promotion-consent-" + canonical_sha256({"tenant_id": active.proposal.tenant_id, "owner_user_id": active.proposal.owner_user_id, "patch_id": active.committed_patch.patch_id, "patch_hash": active.committed_patch.patch_hash, "candidate_shared_statement_sha256": assessment.candidate_shared_statement_sha256, "deidentification_policy_digest": assessment.policy_digest, "target_hat_id": target_hat_id, "consent_nonce": consent_nonce})
    return SharedMemoryPromotionConsent(
        schema_version=STEP32_SCHEMA_VERSION,
        consent_id=consent_id,
        tenant_id=active.proposal.tenant_id,
        owner_user_id=active.proposal.owner_user_id,
        personal_memory_space_id=active.proposal.personal_memory_space_id,
        source_patch_id=active.committed_patch.patch_id,
        source_patch_hash=active.committed_patch.patch_hash,
        candidate_shared_statement_sha256=assessment.candidate_shared_statement_sha256,
        deidentification_policy_digest=assessment.policy_digest,
        target_hat_id=target_hat_id,
        consent_nonce=consent_nonce,
        actor_type=Step32ActorType.HUMAN_OWNER,
        actor_id=authenticated_owner_user_id,
        consented_at=consented_at,
    )


def build_shared_promotion_request(
    state: PersonalMemoryPatchLifecycleState,
    *,
    target_hat_id: str,
    promotion_purpose: str,
    promotion_scope: Sequence[ScopeDimension],
    deidentification: DeidentificationAssessment,
    owner_consent: SharedMemoryPromotionConsent,
    canonical_evidence_compatibility: CanonicalEvidenceCompatibility,
    reason_codes: Sequence[Step32ReasonCode],
    requested_at: datetime,
    idempotency_key: str,
) -> SharedMemoryPromotionRequest:
    active = _active_state(state)
    expected_assessment = assess_shared_promotion_privacy(
        active.committed_patch.patch_statement,
        private_identifiers=(active.proposal.owner_user_id,),
    )
    if deidentification != expected_assessment:
        raise PersonalMemoryStep32Error(
            Step32ReasonCode.SHARED_PROMOTION_PRIVACY_REVIEW_REQUIRED
        )
    if canonical_json_bytes(tuple(promotion_scope)) != canonical_json_bytes(
        active.committed_patch.patch_scope
    ):
        raise PersonalMemoryStep32Error(
            Step32ReasonCode.SUPERSESSION_SCOPE_MISMATCH
        )
    return SharedMemoryPromotionRequest(
        schema_version=STEP32_SCHEMA_VERSION,
        tenant_id=active.proposal.tenant_id,
        owner_user_id=active.proposal.owner_user_id,
        personal_memory_space_id=active.proposal.personal_memory_space_id,
        source_proposal_id=active.proposal.proposal_id,
        source_patch_id=active.committed_patch.patch_id,
        source_patch_hash=active.committed_patch.patch_hash,
        source_state_hash=active.state_hash,
        target_hat_id=target_hat_id,
        promotion_purpose=promotion_purpose,
        promotion_scope=tuple(promotion_scope),
        deidentification=deidentification,
        owner_consent=owner_consent,
        canonical_evidence_compatibility=canonical_evidence_compatibility,
        reason_codes=tuple(reason_codes),
        requested_at=requested_at,
        idempotency_key=idempotency_key,
    )


def create_shared_promotion_proposal(
    request: SharedMemoryPromotionRequest,
    state: PersonalMemoryPatchLifecycleState,
    *,
    authenticated_owner_user_id: str,
) -> SharedMemoryPromotionProposal:
    verify_shared_promotion_request(request)
    active = _active_state(state)
    if authenticated_owner_user_id != request.owner_user_id or active.proposal.owner_user_id != request.owner_user_id:
        raise PersonalMemoryStep32Error(Step32ReasonCode.SHARED_PROMOTION_OWNER_CONSENT_REQUIRED)
    if request.source_state_hash != active.state_hash or request.source_patch_hash != active.committed_patch.patch_hash or request.source_patch_id != active.committed_patch.patch_id:
        raise PersonalMemoryStep32Error(Step32ReasonCode.SHARED_PROMOTION_SOURCE_REVOKED)
    expected_assessment = assess_shared_promotion_privacy(
        active.committed_patch.patch_statement,
        private_identifiers=(active.proposal.owner_user_id,),
    )
    if request.deidentification != expected_assessment:
        raise PersonalMemoryStep32Error(
            Step32ReasonCode.SHARED_PROMOTION_PRIVACY_REVIEW_REQUIRED
        )
    if canonical_json_bytes(request.promotion_scope) != canonical_json_bytes(
        active.committed_patch.patch_scope
    ):
        raise PersonalMemoryStep32Error(
            Step32ReasonCode.SUPERSESSION_SCOPE_MISMATCH
        )
    promotion_id = "shared-memory-promotion-" + canonical_sha256({"replay_identity": request.replay_identity, "request_hash": request.request_hash})
    classification = PrivateDataClassification.NONE if not request.deidentification.detected_categories else PrivateDataClassification.PERSONAL
    base = SharedPromotionProposal(
        schema_version=STEP32_SCHEMA_VERSION,
        shared_promotion_proposal_id=promotion_id,
        originating_personal_patch_id=request.source_patch_id,
        originating_personal_patch_hash=request.source_patch_hash,
        originating_personal_memory_space_id=request.personal_memory_space_id,
        tenant_id=request.tenant_id,
        owner_user_id=request.owner_user_id,
        target_hat_id=request.target_hat_id,
        private_data_classification=classification,
        deidentification_status=request.deidentification.status,
        independent_evidence_references=(),
        independent_evidence_validated=False,
        hat_scope_dimensions=request.promotion_scope,
        valid_from=None,
        valid_until=None,
        domain_approval_id=None,
        shared_commit_id=None,
        state=SharedPromotionState.SHARED_PROMOTION_PROPOSED,
        created_at=request.requested_at,
        updated_at=request.requested_at,
    )
    return SharedMemoryPromotionProposal(
        schema_version=STEP32_SCHEMA_VERSION,
        promotion_id=promotion_id,
        request_hash=request.request_hash,
        replay_identity=request.replay_identity,
        tenant_id=request.tenant_id,
        owner_user_id=request.owner_user_id,
        personal_memory_space_id=request.personal_memory_space_id,
        source_proposal_id=request.source_proposal_id,
        source_patch_id=request.source_patch_id,
        source_patch_hash=request.source_patch_hash,
        source_state_hash=request.source_state_hash,
        target_hat_id=request.target_hat_id,
        promotion_purpose=request.promotion_purpose,
        candidate_shared_statement=request.deidentification.candidate_shared_statement,
        candidate_shared_statement_sha256=request.deidentification.candidate_shared_statement_sha256,
        promotion_scope=request.promotion_scope,
        deidentification=request.deidentification,
        owner_consent_hash=request.owner_consent.consent_hash,
        canonical_evidence_compatibility=request.canonical_evidence_compatibility,
        base_proposal=base,
        reason_codes=request.reason_codes,
        review_required=True,
        shared_active=False,
        source_registry_published=False,
        canonical_evidence=False,
        created_at=request.requested_at,
    )


def verify_supersession_request(value: PersonalMemorySupersessionRequest) -> None:
    verify_canonical_hash(value, value.request_hash, exclude_fields=("request_hash",))
    _reconstruct(value, "supersession request")


def verify_patch_supersession(value: PersonalMemoryPatchSupersession) -> None:
    verify_canonical_hash(value, value.supersession_hash, exclude_fields=("supersession_hash",))
    _reconstruct(value, "patch supersession")


def verify_revocation_request(value: PersonalMemoryRevocationRequest) -> None:
    verify_canonical_hash(value, value.request_hash, exclude_fields=("request_hash",))
    _reconstruct(value, "revocation request")


def verify_patch_revocation(value: PersonalMemoryPatchRevocation) -> None:
    verify_canonical_hash(value, value.revocation_hash, exclude_fields=("revocation_hash",))
    _reconstruct(value, "patch revocation")


def verify_lifecycle_export_request(value: PersonalMemoryLifecycleExportRequest) -> None:
    verify_canonical_hash(value, value.request_hash, exclude_fields=("request_hash",))
    _reconstruct(value, "lifecycle export request")


def verify_lifecycle_export_bundle(value: PersonalMemoryLifecycleExportBundle) -> None:
    for record in value.records:
        verify_canonical_hash(record, record.record_hash, exclude_fields=("record_hash",))
        _reconstruct(record, "lifecycle export record")
    verify_canonical_hash(value, value.bundle_hash, exclude_fields=("bundle_hash",))
    _reconstruct(value, "lifecycle export bundle")


def verify_deletion_request(value: PersonalMemoryDeletionRequest) -> None:
    verify_canonical_hash(value, value.request_hash, exclude_fields=("request_hash",))
    _reconstruct(value, "deletion request")


def verify_deletion_result(value: PersonalMemoryDeletionResult) -> None:
    verify_canonical_hash(value, value.result_hash, exclude_fields=("result_hash",))
    _reconstruct(value, "deletion result")


def verify_deidentification_assessment(value: DeidentificationAssessment) -> None:
    verify_canonical_hash(value, value.assessment_hash, exclude_fields=("assessment_hash",))
    _reconstruct(value, "de-identification assessment")


def verify_shared_promotion_consent(value: SharedMemoryPromotionConsent) -> None:
    verify_canonical_hash(value, value.consent_hash, exclude_fields=("consent_hash",))
    _reconstruct(value, "shared promotion consent")


def verify_shared_promotion_request(value: SharedMemoryPromotionRequest) -> None:
    verify_deidentification_assessment(value.deidentification)
    verify_shared_promotion_consent(value.owner_consent)
    verify_canonical_hash(value, value.request_hash, exclude_fields=("request_hash",))
    _reconstruct(value, "shared promotion request")


def verify_shared_memory_promotion_proposal(value: SharedMemoryPromotionProposal) -> None:
    verify_deidentification_assessment(value.deidentification)
    verify_shared_promotion_hash(value.base_proposal)
    verify_canonical_hash(value, value.proposal_hash, exclude_fields=("proposal_hash",))
    _reconstruct(value, "shared memory promotion proposal")


def step32_to_jsonb(value: object) -> dict[str, Any]:
    result = to_canonical_data(value)
    if not isinstance(result, dict):
        raise ContractValidationError("Step 32 record must serialize as an object")
    return result


__all__ = [
    "MAXIMUM_EXPORT_BYTES", "MAXIMUM_EXPORT_RECORDS", "STEP32_DEIDENTIFICATION_POLICY_ID",
    "STEP32_DEIDENTIFICATION_POLICY_VERSION", "STEP32_EXPORT_SCHEMA_VERSION",
    "STEP32_SCHEMA_VERSION", "STEP32_STATE_VERSION", "STEP32_SYSTEM_POLICY_ACTOR_ID", "DeidentificationAssessment",
    "DeidentificationDecision", "DeidentificationPolicy", "PersonalMemoryDeletionRequest",
    "PersonalMemoryDeletionResult", "PersonalMemoryLifecycleExportBundle",
    "PersonalMemoryLifecycleExportRecord", "PersonalMemoryLifecycleExportRequest",
    "PersonalMemoryPatchRevocation", "PersonalMemoryPatchSupersession",
    "PersonalMemoryRevocationRequest", "PersonalMemoryStep32Error",
    "PersonalMemorySupersessionRequest", "SharedMemoryPromotionConsent",
    "SharedMemoryPromotionProposal", "SharedMemoryPromotionRequest", "Step32ActorType",
    "Step32ReasonCode", "assess_shared_promotion_privacy", "build_deletion_request",
    "build_lifecycle_export_bundle", "build_lifecycle_export_request",
    "build_revocation_request", "build_shared_promotion_consent",
    "build_shared_promotion_request", "build_supersession_request",
    "complete_logical_deletion", "create_patch_revocation", "create_patch_supersession",
    "create_shared_promotion_proposal", "load_deidentification_policy", "step32_to_jsonb",
    "deletion_result_for_request", "parse_lifecycle_export_bundle",
    "verify_deidentification_assessment", "verify_deletion_request",
    "verify_deletion_result", "verify_lifecycle_export_bundle",
    "verify_lifecycle_export_request", "verify_patch_revocation",
    "verify_patch_supersession", "verify_revocation_request",
    "verify_shared_memory_promotion_proposal", "verify_shared_promotion_consent",
    "verify_shared_promotion_request", "verify_supersession_request",
]
