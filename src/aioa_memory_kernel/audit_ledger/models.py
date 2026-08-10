"""Step 33 append-only audit-ledger contracts.

The contracts extend the early Kernel ``AuditEvent`` foundation with an
owner-private chain partition, an explicit genesis sentinel, closed event and
actor vocabularies, deterministic append receipts, verification results, and
bounded proof-carrying exports.  They use the repository canonical JSON and
SHA-256 helpers; no second serialization or cryptography system is introduced.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from aioa_memory_kernel.contracts.enums import StableStringEnum
from aioa_memory_kernel.contracts.exceptions import (
    ContractValidationError,
    IntegrityError,
)
from aioa_memory_kernel.contracts.serialization import (
    canonical_json_bytes,
    canonical_sha256,
    ensure_utc,
    freeze_json,
    require_sha256_hex,
    sha256_hex,
    to_canonical_data,
    verify_canonical_hash,
)
from aioa_memory_kernel.persistence.errors import PersistenceError


STEP33_AUDIT_SCHEMA_VERSION = "audit-event-envelope-1.0.0"
STEP33_EVENT_REGISTRY_VERSION = "audit-event-registry-2"
STEP33_CHAIN_POLICY_ID = "tenant-owner-audit-chain-1a"
STEP33_CHAIN_POLICY_VERSION = "1"
STEP33_HASH_ALGORITHM = "SHA-256"
STEP33_CANONICALIZATION_ID = "aioa-canonical-json-utf8-v1"
STEP33_EVENT_HASH_DOMAIN = "MEMORY_PATCH_AUDIT_EVENT_V1"
STEP33_PAYLOAD_HASH_DOMAIN = "MEMORY_PATCH_AUDIT_PAYLOAD_V1"
STEP33_CHAIN_ID_DOMAIN = "MEMORY_PATCH_AUDIT_CHAIN_V1"
STEP33_GENESIS_SENTINEL = sha256_hex("MEMORY_PATCH_AUDIT_GENESIS_V1")
STEP33_REDACTION_POLICY_ID = "audit-export-redaction-1a"
STEP33_REDACTION_POLICY_VERSION = "1"
STEP33_VERIFICATION_POLICY_ID = "audit-chain-verification-1a"
STEP33_VERIFICATION_POLICY_VERSION = "1"
STEP33_VERIFICATION_POLICY_DIGEST = canonical_sha256(
    {
        "policy_id": STEP33_VERIFICATION_POLICY_ID,
        "policy_version": STEP33_VERIFICATION_POLICY_VERSION,
        "checks": (
            "CHAIN_IDENTITY",
            "GENESIS_OR_RANGE_ANCHOR",
            "CONTIGUOUS_SEQUENCE",
            "UNIQUE_EVENT_ID",
            "PREVIOUS_EVENT_HASH",
            "EVENT_HASH",
            "PAYLOAD_DIGEST",
            "APPEND_RECEIPT",
            "CHAIN_HEAD",
        ),
        "auto_repair": False,
    }
)

DEFAULT_AUDIT_EXPORT_EVENTS = 1_000
MAX_AUDIT_EXPORT_EVENTS = 10_000
MAX_AUDIT_EXPORT_CHAINS = 1
MAX_AUDIT_EXPORT_BYTES = 8 * 1024 * 1024
MAX_AUDIT_PAYLOAD_BYTES = 16 * 1024
MAX_AUDIT_REASON_CODES = 32
MAX_AUDIT_LINEAGE_HASHES = 64

_SAFE_TEXT = re.compile(r"^[^\x00-\x1f\x7f]+$")
_CHAIN_SCOPE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+-]{0,254}$")
_SECRET_TOKENS = (
    "access_token",
    "api_key",
    "apikey",
    "auth_token",
    "authorization",
    "aws_secret",
    "bearer",
    "client_secret",
    "credential",
    "database_url",
    "github_token",
    "password",
    "presigned",
    "private_key",
    "refresh_token",
    "secret",
    "secret_access_key",
    "session_token",
)
_RAW_PRIVATE_KEYS = (
    "answer_text",
    "candidate_shared_statement",
    "draft_text",
    "patch_statement",
    "personal_memory_text",
    "prompt_text",
    "raw_content",
    "raw_prompt",
    "source_chunk_text",
)


class AuditEventType(StableStringEnum):
    KERNEL_REQUEST_RECEIVED = "KERNEL_REQUEST_RECEIVED"
    ROUTE_DECIDED = "ROUTE_DECIDED"
    KNOWLEDGE_POLICY_DECIDED = "KNOWLEDGE_POLICY_DECIDED"
    EVIDENCE_BUNDLE_CREATED = "EVIDENCE_BUNDLE_CREATED"
    TEMPORAL_RESOLUTION_COMPLETED = "TEMPORAL_RESOLUTION_COMPLETED"
    DRAFT_V1_GENERATED = "DRAFT_V1_GENERATED"
    CLAIM_EVIDENCE_VALIDATED = "CLAIM_EVIDENCE_VALIDATED"
    CORRECTION_PACKET_CREATED = "CORRECTION_PACKET_CREATED"
    DRAFT_V2_VERIFIED = "DRAFT_V2_VERIFIED"
    VERIFIED_ANSWER_ASSEMBLED = "VERIFIED_ANSWER_ASSEMBLED"
    VERIFIED_ANSWER_BLOCKED = "VERIFIED_ANSWER_BLOCKED"
    PERSONAL_MEMORY_SLOT_CREATED = "PERSONAL_MEMORY_SLOT_CREATED"
    PERSONAL_MEMORY_SLOT_CONFIGURED = "PERSONAL_MEMORY_SLOT_CONFIGURED"
    PERSONAL_MEMORY_SLOT_STATE_CHANGED = "PERSONAL_MEMORY_SLOT_STATE_CHANGED"
    CORRECTION_CANDIDATE_DETECTED = "CORRECTION_CANDIDATE_DETECTED"
    PERSONAL_MEMORY_PROPOSAL_CREATED = "PERSONAL_MEMORY_PROPOSAL_CREATED"
    PERSONAL_MEMORY_EVIDENCE_BOUND = "PERSONAL_MEMORY_EVIDENCE_BOUND"
    PERSONAL_MEMORY_VALIDATED = "PERSONAL_MEMORY_VALIDATED"
    PERSONAL_MEMORY_AWAITING_APPROVAL = "PERSONAL_MEMORY_AWAITING_APPROVAL"
    PERSONAL_MEMORY_APPROVED = "PERSONAL_MEMORY_APPROVED"
    PERSONAL_MEMORY_COMMITTED = "PERSONAL_MEMORY_COMMITTED"
    PERSONAL_MEMORY_ACTIVATED = "PERSONAL_MEMORY_ACTIVATED"
    PERSONAL_MEMORY_SUPERSEDED = "PERSONAL_MEMORY_SUPERSEDED"
    PERSONAL_MEMORY_REVOKED = "PERSONAL_MEMORY_REVOKED"
    PERSONAL_MEMORY_EXPORTED = "PERSONAL_MEMORY_EXPORTED"
    PERSONAL_MEMORY_DELETE_REQUESTED = "PERSONAL_MEMORY_DELETE_REQUESTED"
    PERSONAL_MEMORY_DELETED = "PERSONAL_MEMORY_DELETED"
    SHARED_PROMOTION_PROPOSED = "SHARED_PROMOTION_PROPOSED"
    DEIDENTIFICATION_REVIEW_REQUIRED = "DEIDENTIFICATION_REVIEW_REQUIRED"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    INTEGRITY_FAILURE = "INTEGRITY_FAILURE"
    OWNER_SCOPE_DENIED = "OWNER_SCOPE_DENIED"
    TENANT_SCOPE_DENIED = "TENANT_SCOPE_DENIED"
    POLICY_BLOCKED = "POLICY_BLOCKED"
    REVIEW_CASE_CREATED = "REVIEW_CASE_CREATED"
    REVIEW_CASE_CLAIMED = "REVIEW_CASE_CLAIMED"
    REVIEW_DECISION_RECORDED = "REVIEW_DECISION_RECORDED"
    REVIEW_HANDOFF_SUCCEEDED = "REVIEW_HANDOFF_SUCCEEDED"
    REVIEW_HANDOFF_FAILED = "REVIEW_HANDOFF_FAILED"
    REVIEW_CASE_RESOLVED = "REVIEW_CASE_RESOLVED"
    REVIEW_CASE_ESCALATED = "REVIEW_CASE_ESCALATED"


class AuditActorType(StableStringEnum):
    HUMAN_USER = "HUMAN_USER"
    KERNEL = "KERNEL"
    MODEL_ADAPTER = "MODEL_ADAPTER"
    CRITIC_LOOP = "CRITIC_LOOP"
    COMMIT_HELPER = "COMMIT_HELPER"
    ACTIVATION_SERVICE = "ACTIVATION_SERVICE"
    SYSTEM_POLICY = "SYSTEM_POLICY"
    REVIEW_SERVICE = "REVIEW_SERVICE"
    HUMAN_REVIEWER = "HUMAN_REVIEWER"
    MIGRATION_SERVICE = "MIGRATION_SERVICE"


class AuditSubjectType(StableStringEnum):
    KERNEL_RUN = "KERNEL_RUN"
    ROUTE_RESULT = "ROUTE_RESULT"
    POLICY_RESULT = "POLICY_RESULT"
    EVIDENCE_BUNDLE = "EVIDENCE_BUNDLE"
    TEMPORAL_RESULT = "TEMPORAL_RESULT"
    DRAFT = "DRAFT"
    CLAIM_ASSESSMENT = "CLAIM_ASSESSMENT"
    CORRECTION_PACKET = "CORRECTION_PACKET"
    VERIFIED_ANSWER = "VERIFIED_ANSWER"
    PERSONAL_MEMORY_SLOT = "PERSONAL_MEMORY_SLOT"
    CORRECTION_CANDIDATE = "CORRECTION_CANDIDATE"
    PERSONAL_MEMORY_PROPOSAL = "PERSONAL_MEMORY_PROPOSAL"
    PERSONAL_MEMORY_PATCH = "PERSONAL_MEMORY_PATCH"
    PERSONAL_MEMORY_EXPORT = "PERSONAL_MEMORY_EXPORT"
    PERSONAL_MEMORY_DELETION = "PERSONAL_MEMORY_DELETION"
    SHARED_PROMOTION_PROPOSAL = "SHARED_PROMOTION_PROPOSAL"
    REVIEW_CASE = "REVIEW_CASE"
    REVIEW_DECISION = "REVIEW_DECISION"
    REVIEW_HANDOFF = "REVIEW_HANDOFF"
    SECURITY_EVENT = "SECURITY_EVENT"


class AuditReasonCode(StableStringEnum):
    AUDIT_EVENT_APPENDED = "AUDIT_EVENT_APPENDED"
    AUDIT_EVENT_EXACT_REPLAY = "AUDIT_EVENT_EXACT_REPLAY"
    AUDIT_EVENT_REPLAY_CONFLICT = "AUDIT_EVENT_REPLAY_CONFLICT"
    AUDIT_EVENT_INVALID = "AUDIT_EVENT_INVALID"
    AUDIT_EVENT_PAYLOAD_INVALID = "AUDIT_EVENT_PAYLOAD_INVALID"
    AUDIT_CHAIN_VERIFIED = "AUDIT_CHAIN_VERIFIED"
    AUDIT_CHAIN_BROKEN = "AUDIT_CHAIN_BROKEN"
    AUDIT_GENESIS_INVALID = "AUDIT_GENESIS_INVALID"
    AUDIT_SEQUENCE_GAP = "AUDIT_SEQUENCE_GAP"
    AUDIT_SEQUENCE_DUPLICATE = "AUDIT_SEQUENCE_DUPLICATE"
    AUDIT_PREVIOUS_HASH_MISMATCH = "AUDIT_PREVIOUS_HASH_MISMATCH"
    AUDIT_EVENT_HASH_MISMATCH = "AUDIT_EVENT_HASH_MISMATCH"
    AUDIT_PAYLOAD_DIGEST_MISMATCH = "AUDIT_PAYLOAD_DIGEST_MISMATCH"
    AUDIT_CHAIN_HEAD_MISMATCH = "AUDIT_CHAIN_HEAD_MISMATCH"
    AUDIT_EXPORT_READY = "AUDIT_EXPORT_READY"
    AUDIT_EXPORT_TRUNCATED = "AUDIT_EXPORT_TRUNCATED"
    AUDIT_EXPORT_SCOPE_DENIED = "AUDIT_EXPORT_SCOPE_DENIED"
    AUDIT_EXPORT_REDACTED = "AUDIT_EXPORT_REDACTED"
    AUDIT_OWNER_MISMATCH = "AUDIT_OWNER_MISMATCH"
    AUDIT_TENANT_MISMATCH = "AUDIT_TENANT_MISMATCH"
    AUDIT_REVIEWER_UNAUTHORIZED = "AUDIT_REVIEWER_UNAUTHORIZED"


class AuditRedactionProfile(StableStringEnum):
    HASH_ONLY = "HASH_ONLY"
    SAFE_METADATA = "SAFE_METADATA"


class AuditLedgerError(PersistenceError):
    def __init__(self, reason_code: AuditReasonCode) -> None:
        if not isinstance(reason_code, AuditReasonCode):
            raise TypeError("reason_code must be AuditReasonCode")
        super().__init__(
            "Step 33 audit ledger rejected the operation",
            operation_kind="AUDIT_LEDGER_STEP33",
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
        or _SAFE_TEXT.fullmatch(value) is None
    ):
        raise ContractValidationError(f"{name} must be bounded canonical text")
    return value


def _optional_text(value: object | None, name: str, maximum: int = 255) -> str | None:
    if value is None:
        return None
    return _text(value, name, maximum)


def _digest(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise ContractValidationError(f"{name} must be a SHA-256 digest")
    return require_sha256_hex(value, name)


def _optional_digest(value: object | None, name: str) -> str | None:
    if value is None:
        return None
    return _digest(value, name)


def _time(value: object, name: str) -> datetime:
    if not isinstance(value, datetime):
        raise ContractValidationError(f"{name} must be a datetime")
    return ensure_utc(value, name)


def _enum(value: object, enum_type: type[StableStringEnum], name: str):
    if not isinstance(value, enum_type):
        raise ContractValidationError(f"{name} must be {enum_type.__name__}")
    return value


def _reason_codes(value: object) -> tuple[AuditReasonCode, ...]:
    if not isinstance(value, (tuple, list)) or not value:
        raise ContractValidationError("reason_codes must be a non-empty tuple")
    result = tuple(value)
    if len(result) > MAX_AUDIT_REASON_CODES or any(
        not isinstance(item, AuditReasonCode) for item in result
    ):
        raise ContractValidationError("reason_codes exceed the closed bound")
    if result != tuple(sorted(set(result), key=lambda item: item.value)):
        raise ContractValidationError("reason_codes must be sorted and unique")
    return result


def _hash_mapping(value: object, name: str) -> Mapping[str, str]:
    if (
        not isinstance(value, Mapping)
        or len(value) > MAX_AUDIT_LINEAGE_HASHES
        or any(not isinstance(key, str) for key in value)
    ):
        raise ContractValidationError(f"{name} must be a bounded mapping")
    normalized: dict[str, str] = {}
    for key in sorted(value):
        normalized[_text(key, f"{name} key", 128)] = _digest(value[key], f"{name}.{key}")
    return freeze_json(normalized)


def _walk_payload(value: object, *, path: str = "payload") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            key_text = _text(key, f"{path} key", 128)
            lowered = key_text.lower()
            if any(token in lowered for token in _SECRET_TOKENS + _RAW_PRIVATE_KEYS):
                raise ContractValidationError("audit payload contains a forbidden field")
            _walk_payload(item, path=f"{path}.{key_text}")
        return
    if isinstance(value, (tuple, list)):
        for index, item in enumerate(value):
            _walk_payload(item, path=f"{path}[{index}]")
        return
    if value is None or isinstance(value, (bool, int)):
        return
    if isinstance(value, str):
        _text(value, path, 2048)
        lowered = value.lower()
        if any(
            token in lowered
            for token in (
                "authorization:",
                "bearer ",
                "cockroachdb://",
                "github_pat_",
                "ghp_",
                "postgresql://",
                "sk-ant-",
                "sk-proj-",
                "x-amz-credential",
                "x-amz-signature",
            )
        ):
            raise ContractValidationError("audit payload contains secret-shaped text")
        return
    raise ContractValidationError("audit payload contains an unsupported value")


def safe_audit_payload(value: object) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ContractValidationError("event_payload must be a mapping")
    _walk_payload(value)
    frozen = freeze_json(value)
    if len(canonical_json_bytes(frozen)) > MAX_AUDIT_PAYLOAD_BYTES:
        raise ContractValidationError("event_payload exceeds the byte bound")
    return frozen


def compute_event_payload_digest(payload: Mapping[str, Any]) -> str:
    safe = safe_audit_payload(payload)
    return canonical_sha256(
        {"domain": STEP33_PAYLOAD_HASH_DOMAIN, "payload": safe}
    )


def compute_audit_chain_id(tenant_id: str, owner_user_id: str | None) -> str:
    tenant = _text(tenant_id, "tenant_id")
    owner = _optional_text(owner_user_id, "owner_user_id")
    if _CHAIN_SCOPE_ID.fullmatch(tenant) is None or (
        owner is not None and _CHAIN_SCOPE_ID.fullmatch(owner) is None
    ):
        raise ContractValidationError(
            "audit chain scope identifiers are not database-canonical"
        )
    digest = canonical_sha256(
        {
            "domain": STEP33_CHAIN_ID_DOMAIN,
            "partition_policy": STEP33_CHAIN_POLICY_ID,
            "tenant_id": tenant,
            "owner_user_id": owner,
        }
    )
    return f"audit-chain-{digest}"


@dataclass(frozen=True, slots=True)
class AuditEventDraft:
    event_type: AuditEventType
    tenant_id: str
    subject_type: AuditSubjectType
    subject_id: str
    subject_hash: str
    actor_type: AuditActorType
    actor_id: str
    idempotency_key: str
    occurred_at: datetime
    recorded_at: datetime
    event_payload: Mapping[str, Any]
    reason_codes: tuple[AuditReasonCode, ...]
    owner_user_id: str | None = None
    personal_memory_space_id: str | None = None
    kernel_run_id: str | None = None
    request_id: str | None = None
    policy_id: str | None = None
    policy_version: str | None = None
    policy_digest: str | None = None
    route_hash: str | None = None
    lineage_hashes: Mapping[str, str] = field(default_factory=dict)
    schema_version: str = STEP33_AUDIT_SCHEMA_VERSION
    event_payload_digest: str = field(init=False)
    draft_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != STEP33_AUDIT_SCHEMA_VERSION:
            raise ContractValidationError("unsupported Step 33 audit schema")
        _enum(self.event_type, AuditEventType, "event_type")
        _enum(self.subject_type, AuditSubjectType, "subject_type")
        _enum(self.actor_type, AuditActorType, "actor_type")
        for name in ("tenant_id", "subject_id", "actor_id", "idempotency_key"):
            _text(getattr(self, name), name)
        _digest(self.subject_hash, "subject_hash")
        owner = _optional_text(self.owner_user_id, "owner_user_id")
        space = _optional_text(
            self.personal_memory_space_id, "personal_memory_space_id"
        )
        if space is not None and owner is None:
            raise ContractValidationError("personal memory audit scope requires owner")
        _optional_text(self.kernel_run_id, "kernel_run_id")
        _optional_text(self.request_id, "request_id")
        policy_values = (self.policy_id, self.policy_version, self.policy_digest)
        if any(item is not None for item in policy_values):
            if not all(item is not None for item in policy_values):
                raise ContractValidationError("audit policy identity must be complete")
            _text(self.policy_id, "policy_id")
            _text(self.policy_version, "policy_version", 64)
            _digest(self.policy_digest, "policy_digest")
        _optional_digest(self.route_hash, "route_hash")
        object.__setattr__(self, "occurred_at", _time(self.occurred_at, "occurred_at"))
        object.__setattr__(self, "recorded_at", _time(self.recorded_at, "recorded_at"))
        if self.recorded_at < self.occurred_at:
            raise ContractValidationError("recorded_at cannot precede occurred_at")
        payload = safe_audit_payload(self.event_payload)
        object.__setattr__(self, "event_payload", payload)
        object.__setattr__(self, "reason_codes", _reason_codes(self.reason_codes))
        object.__setattr__(
            self, "lineage_hashes", _hash_mapping(self.lineage_hashes, "lineage_hashes")
        )
        object.__setattr__(
            self, "event_payload_digest", compute_event_payload_digest(payload)
        )
        object.__setattr__(
            self,
            "draft_hash",
            canonical_sha256(
                self,
                # Raw payload bytes are stored separately, but the
                # domain-separated payload digest is part of replay identity.
                exclude_fields=("event_payload", "draft_hash"),
            ),
        )


@dataclass(frozen=True, slots=True)
class AuditEventEnvelope:
    schema_version: str
    event_type: AuditEventType
    tenant_id: str
    owner_user_id: str | None
    personal_memory_space_id: str | None
    kernel_run_id: str | None
    request_id: str | None
    subject_type: AuditSubjectType
    subject_id: str
    subject_hash: str
    actor_type: AuditActorType
    actor_id: str
    policy_id: str | None
    policy_version: str | None
    policy_digest: str | None
    route_hash: str | None
    lineage_hashes: Mapping[str, str]
    reason_codes: tuple[AuditReasonCode, ...]
    sequence_number: int
    chain_id: str
    previous_event_hash: str
    event_payload_digest: str
    idempotency_key: str
    draft_hash: str
    occurred_at: datetime
    recorded_at: datetime
    hash_domain: str = STEP33_EVENT_HASH_DOMAIN
    event_id: str = field(init=False)
    event_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != STEP33_AUDIT_SCHEMA_VERSION:
            raise ContractValidationError("unsupported Step 33 audit schema")
        if self.hash_domain != STEP33_EVENT_HASH_DOMAIN:
            raise ContractValidationError("audit hash domain mismatch")
        _enum(self.event_type, AuditEventType, "event_type")
        _enum(self.subject_type, AuditSubjectType, "subject_type")
        _enum(self.actor_type, AuditActorType, "actor_type")
        for name in (
            "tenant_id",
            "subject_id",
            "actor_id",
            "idempotency_key",
            "chain_id",
        ):
            _text(getattr(self, name), name)
        expected_chain = compute_audit_chain_id(self.tenant_id, self.owner_user_id)
        if self.chain_id != expected_chain:
            raise ContractValidationError("audit chain_id is detached from scope")
        _optional_text(self.personal_memory_space_id, "personal_memory_space_id")
        if self.personal_memory_space_id is not None and self.owner_user_id is None:
            raise ContractValidationError("personal memory audit scope requires owner")
        _optional_text(self.kernel_run_id, "kernel_run_id")
        _optional_text(self.request_id, "request_id")
        _digest(self.subject_hash, "subject_hash")
        _digest(self.previous_event_hash, "previous_event_hash")
        _digest(self.event_payload_digest, "event_payload_digest")
        _digest(self.draft_hash, "draft_hash")
        if not isinstance(self.sequence_number, int) or isinstance(
            self.sequence_number, bool
        ) or self.sequence_number < 1:
            raise ContractValidationError("sequence_number must start at one")
        if self.sequence_number == 1 and self.previous_event_hash != STEP33_GENESIS_SENTINEL:
            raise ContractValidationError("audit genesis sentinel mismatch")
        policy_values = (self.policy_id, self.policy_version, self.policy_digest)
        if any(item is not None for item in policy_values):
            if not all(item is not None for item in policy_values):
                raise ContractValidationError("audit policy identity must be complete")
            _text(self.policy_id, "policy_id")
            _text(self.policy_version, "policy_version", 64)
            _digest(self.policy_digest, "policy_digest")
        _optional_digest(self.route_hash, "route_hash")
        object.__setattr__(self, "reason_codes", _reason_codes(self.reason_codes))
        object.__setattr__(
            self, "lineage_hashes", _hash_mapping(self.lineage_hashes, "lineage_hashes")
        )
        object.__setattr__(self, "occurred_at", _time(self.occurred_at, "occurred_at"))
        object.__setattr__(self, "recorded_at", _time(self.recorded_at, "recorded_at"))
        if self.recorded_at < self.occurred_at:
            raise ContractValidationError("recorded_at cannot precede occurred_at")
        identity = canonical_sha256(
            {
                "chain_id": self.chain_id,
                "sequence_number": self.sequence_number,
                "idempotency_key": self.idempotency_key,
                "draft_hash": self.draft_hash,
            }
        )
        object.__setattr__(self, "event_id", f"audit-event-{identity}")
        object.__setattr__(
            self,
            "event_hash",
            canonical_sha256(self, exclude_fields=("event_hash",)),
        )


@dataclass(frozen=True, slots=True)
class AuditAppendReceipt:
    chain_id: str
    sequence_number: int
    event_id: str
    event_hash: str
    previous_event_hash: str
    subject_type: AuditSubjectType
    subject_id: str
    subject_hash: str
    idempotency_key: str
    receipt_hash: str = field(init=False)

    def __post_init__(self) -> None:
        for name in ("chain_id", "event_id", "subject_id", "idempotency_key"):
            _text(getattr(self, name), name)
        if (
            not isinstance(self.sequence_number, int)
            or isinstance(self.sequence_number, bool)
            or self.sequence_number < 1
        ):
            raise ContractValidationError("receipt sequence is invalid")
        _enum(self.subject_type, AuditSubjectType, "subject_type")
        for name in ("event_hash", "previous_event_hash", "subject_hash"):
            _digest(getattr(self, name), name)
        object.__setattr__(
            self, "receipt_hash", canonical_sha256(self, exclude_fields=("receipt_hash",))
        )


@dataclass(frozen=True, slots=True)
class AuditLedgerEntry:
    """Durable envelope, minimized payload, and append proof.

    ``event_hash`` binds ``event_payload_digest``.  Keeping the payload next to
    the envelope permits explicit digest verification without putting raw
    private business content in the ledger.
    """

    envelope: AuditEventEnvelope
    event_payload: Mapping[str, Any]
    append_receipt: AuditAppendReceipt
    entry_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.envelope, AuditEventEnvelope):
            raise ContractValidationError("ledger entry requires an audit envelope")
        if not isinstance(self.append_receipt, AuditAppendReceipt):
            raise ContractValidationError("ledger entry requires an append receipt")
        verify_audit_event_envelope(self.envelope)
        payload = safe_audit_payload(self.event_payload)
        if compute_event_payload_digest(payload) != self.envelope.event_payload_digest:
            raise IntegrityError("audit ledger payload digest mismatch")
        reconstructed_draft = AuditEventDraft(
            event_type=self.envelope.event_type,
            tenant_id=self.envelope.tenant_id,
            owner_user_id=self.envelope.owner_user_id,
            personal_memory_space_id=self.envelope.personal_memory_space_id,
            kernel_run_id=self.envelope.kernel_run_id,
            request_id=self.envelope.request_id,
            subject_type=self.envelope.subject_type,
            subject_id=self.envelope.subject_id,
            subject_hash=self.envelope.subject_hash,
            actor_type=self.envelope.actor_type,
            actor_id=self.envelope.actor_id,
            idempotency_key=self.envelope.idempotency_key,
            occurred_at=self.envelope.occurred_at,
            recorded_at=self.envelope.recorded_at,
            event_payload=payload,
            reason_codes=self.envelope.reason_codes,
            policy_id=self.envelope.policy_id,
            policy_version=self.envelope.policy_version,
            policy_digest=self.envelope.policy_digest,
            route_hash=self.envelope.route_hash,
            lineage_hashes=self.envelope.lineage_hashes,
            schema_version=self.envelope.schema_version,
        )
        if (
            reconstructed_draft.draft_hash != self.envelope.draft_hash
            or reconstructed_draft.event_payload_digest
            != self.envelope.event_payload_digest
        ):
            raise IntegrityError("audit event draft/envelope binding mismatch")
        expected_receipt = build_audit_append_receipt(self.envelope)
        if self.append_receipt != expected_receipt:
            raise IntegrityError("audit ledger append receipt mismatch")
        object.__setattr__(self, "event_payload", payload)
        object.__setattr__(
            self, "entry_hash", canonical_sha256(self, exclude_fields=("entry_hash",))
        )


@dataclass(frozen=True, slots=True)
class AuditChainHead:
    tenant_id: str
    owner_user_id: str | None
    chain_id: str
    last_sequence: int
    last_event_hash: str
    head_version: int
    updated_at: datetime

    def __post_init__(self) -> None:
        _text(self.tenant_id, "tenant_id")
        _optional_text(self.owner_user_id, "owner_user_id")
        _text(self.chain_id, "chain_id")
        if self.chain_id != compute_audit_chain_id(self.tenant_id, self.owner_user_id):
            raise ContractValidationError("chain head scope mismatch")
        if (
            not isinstance(self.last_sequence, int)
            or isinstance(self.last_sequence, bool)
            or self.last_sequence < 0
        ):
            raise ContractValidationError("last_sequence is invalid")
        if (
            not isinstance(self.head_version, int)
            or isinstance(self.head_version, bool)
            or self.head_version < 0
        ):
            raise ContractValidationError("head_version is invalid")
        if self.head_version != self.last_sequence:
            raise ContractValidationError("chain head version must equal last sequence")
        _digest(self.last_event_hash, "last_event_hash")
        if self.last_sequence == 0 and self.last_event_hash != STEP33_GENESIS_SENTINEL:
            raise ContractValidationError("empty chain head must use genesis sentinel")
        object.__setattr__(self, "updated_at", _time(self.updated_at, "updated_at"))


@dataclass(frozen=True, slots=True)
class AuditChainVerificationResult:
    chain_id: str
    event_count: int
    first_sequence: int | None
    last_sequence: int | None
    first_hash: str | None
    last_hash: str | None
    verified: bool
    failure_sequence: int | None
    failure_reason_codes: tuple[AuditReasonCode, ...]
    verification_policy_id: str
    verification_policy_version: str
    verification_policy_digest: str
    result_hash: str = field(init=False)

    def __post_init__(self) -> None:
        _text(self.chain_id, "chain_id")
        if (
            not isinstance(self.event_count, int)
            or isinstance(self.event_count, bool)
            or self.event_count < 0
        ):
            raise ContractValidationError("event_count is invalid")
        for name in ("first_sequence", "last_sequence", "failure_sequence"):
            value = getattr(self, name)
            if value is not None and (
                not isinstance(value, int) or isinstance(value, bool) or value < 1
            ):
                raise ContractValidationError(f"{name} is invalid")
        _optional_digest(self.first_hash, "first_hash")
        _optional_digest(self.last_hash, "last_hash")
        range_values = (
            self.first_sequence,
            self.last_sequence,
            self.first_hash,
            self.last_hash,
        )
        if self.event_count == 0 and any(item is not None for item in range_values):
            raise ContractValidationError("empty verification cannot carry a range")
        if self.event_count > 0 and any(item is None for item in range_values):
            raise ContractValidationError("non-empty verification requires a range")
        if not isinstance(self.verified, bool):
            raise ContractValidationError("verified must be boolean")
        reasons = tuple(self.failure_reason_codes)
        if any(not isinstance(item, AuditReasonCode) for item in reasons):
            raise ContractValidationError("verification reasons are invalid")
        if reasons != tuple(sorted(set(reasons), key=lambda item: item.value)):
            raise ContractValidationError("verification reasons must be sorted and unique")
        if self.verified and (reasons or self.failure_sequence is not None):
            raise ContractValidationError("verified chain cannot contain failures")
        if (
            self.verified
            and self.first_sequence is not None
            and self.last_sequence is not None
            and self.event_count
            != self.last_sequence - self.first_sequence + 1
        ):
            raise ContractValidationError("verified chain range is not contiguous")
        if not self.verified and (not reasons or self.failure_sequence is None):
            raise ContractValidationError("broken chain requires a located reason")
        object.__setattr__(self, "failure_reason_codes", reasons)
        _text(self.verification_policy_id, "verification_policy_id")
        _text(self.verification_policy_version, "verification_policy_version", 64)
        _digest(self.verification_policy_digest, "verification_policy_digest")
        if (
            self.verification_policy_id != STEP33_VERIFICATION_POLICY_ID
            or self.verification_policy_version
            != STEP33_VERIFICATION_POLICY_VERSION
            or self.verification_policy_digest
            != STEP33_VERIFICATION_POLICY_DIGEST
        ):
            raise ContractValidationError("audit verification policy differs")
        object.__setattr__(
            self, "result_hash", canonical_sha256(self, exclude_fields=("result_hash",))
        )


@dataclass(frozen=True, slots=True)
class AuditRedactionPolicy:
    policy_id: str = STEP33_REDACTION_POLICY_ID
    policy_version: str = STEP33_REDACTION_POLICY_VERSION
    raw_private_content_allowed: bool = False
    secrets_allowed: bool = False
    machine_paths_allowed: bool = False
    preserve_original_payload_digest: bool = True
    policy_digest: str = field(init=False)

    def __post_init__(self) -> None:
        _text(self.policy_id, "policy_id")
        _text(self.policy_version, "policy_version", 64)
        if any(
            value is not expected
            for value, expected in (
                (self.raw_private_content_allowed, False),
                (self.secrets_allowed, False),
                (self.machine_paths_allowed, False),
                (self.preserve_original_payload_digest, True),
            )
        ):
            raise ContractValidationError("Step 33 redaction policy cannot be weakened")
        object.__setattr__(
            self, "policy_digest", canonical_sha256(self, exclude_fields=("policy_digest",))
        )


@dataclass(frozen=True, slots=True)
class AuditExportRequest:
    tenant_id: str
    requester_actor_type: AuditActorType
    requester_id: str
    owner_user_id: str
    chain_ids: tuple[str, ...]
    start_sequence: int
    end_sequence: int | None
    maximum_events: int
    redaction_profile: AuditRedactionProfile
    requested_at: datetime
    event_types: tuple[AuditEventType, ...] = ()
    request_hash: str = field(init=False)

    def __post_init__(self) -> None:
        _text(self.tenant_id, "tenant_id")
        _enum(self.requester_actor_type, AuditActorType, "requester_actor_type")
        _text(self.requester_id, "requester_id")
        _text(self.owner_user_id, "owner_user_id")
        if self.requester_actor_type is not AuditActorType.HUMAN_USER:
            raise ContractValidationError("Step 34 reviewer access is not implemented")
        chains = tuple(self.chain_ids)
        if not chains or len(chains) > MAX_AUDIT_EXPORT_CHAINS:
            raise ContractValidationError("chain_ids exceed the export bound")
        if chains != tuple(sorted(set(chains))):
            raise ContractValidationError("chain_ids must be sorted and unique")
        expected = compute_audit_chain_id(self.tenant_id, self.owner_user_id)
        if any(chain != expected for chain in chains):
            raise ContractValidationError("owner export requested a foreign chain")
        object.__setattr__(self, "chain_ids", chains)
        if (
            not isinstance(self.start_sequence, int)
            or isinstance(self.start_sequence, bool)
            or self.start_sequence < 1
        ):
            raise ContractValidationError("start_sequence must be positive")
        if self.end_sequence is not None and (
            not isinstance(self.end_sequence, int)
            or isinstance(self.end_sequence, bool)
            or self.end_sequence < self.start_sequence
        ):
            raise ContractValidationError("end_sequence is invalid")
        if (
            not isinstance(self.maximum_events, int)
            or isinstance(self.maximum_events, bool)
            or self.maximum_events < 1
            or self.maximum_events > MAX_AUDIT_EXPORT_EVENTS
        ):
            raise ContractValidationError("maximum_events exceeds the bound")
        _enum(self.redaction_profile, AuditRedactionProfile, "redaction_profile")
        event_types = tuple(self.event_types)
        if any(not isinstance(item, AuditEventType) for item in event_types):
            raise ContractValidationError("event_types contain an unknown value")
        if event_types != tuple(sorted(set(event_types), key=lambda item: item.value)):
            raise ContractValidationError("event_types must be sorted and unique")
        object.__setattr__(self, "event_types", event_types)
        object.__setattr__(self, "requested_at", _time(self.requested_at, "requested_at"))
        object.__setattr__(
            self, "request_hash", canonical_sha256(self, exclude_fields=("request_hash",))
        )


@dataclass(frozen=True, slots=True)
class AuditExportEvent:
    envelope: AuditEventEnvelope
    payload_representation: Mapping[str, Any]
    original_payload_digest: str
    redaction_profile: AuditRedactionProfile
    redacted: bool
    redaction_marker: str
    export_event_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.envelope, AuditEventEnvelope):
            raise ContractValidationError("export event requires AuditEventEnvelope")
        verify_audit_event_envelope(self.envelope)
        _digest(self.original_payload_digest, "original_payload_digest")
        if self.original_payload_digest != self.envelope.event_payload_digest:
            raise ContractValidationError("export payload digest detached from event")
        _enum(self.redaction_profile, AuditRedactionProfile, "redaction_profile")
        if not isinstance(self.redacted, bool):
            raise ContractValidationError("redacted must be boolean")
        _text(self.redaction_marker, "redaction_marker", 128)
        representation = safe_audit_payload(self.payload_representation)
        if self.redaction_profile is AuditRedactionProfile.HASH_ONLY and (
            representation or self.redacted is not True
        ):
            raise ContractValidationError("hash-only export must omit payload")
        if self.redaction_profile is AuditRedactionProfile.SAFE_METADATA and (
            self.redacted is not False
        ):
            raise ContractValidationError("safe metadata export marker differs")
        if (
            self.redaction_profile is AuditRedactionProfile.SAFE_METADATA
            and compute_event_payload_digest(representation)
            != self.original_payload_digest
        ):
            raise ContractValidationError(
                "safe metadata export differs from the bound payload"
            )
        object.__setattr__(self, "payload_representation", representation)
        object.__setattr__(
            self,
            "export_event_hash",
            canonical_sha256(self, exclude_fields=("export_event_hash",)),
        )


@dataclass(frozen=True, slots=True)
class AuditExportRangeProof:
    chain_id: str
    start_sequence: int
    end_sequence: int
    predecessor_hash: str
    first_event_hash: str
    last_event_hash: str
    proof_hash: str = field(init=False)

    def __post_init__(self) -> None:
        _text(self.chain_id, "chain_id")
        if (
            not isinstance(self.start_sequence, int)
            or isinstance(self.start_sequence, bool)
            or not isinstance(self.end_sequence, int)
            or isinstance(self.end_sequence, bool)
            or self.start_sequence < 1
            or self.end_sequence < self.start_sequence
        ):
            raise ContractValidationError("export range is invalid")
        for name in ("predecessor_hash", "first_event_hash", "last_event_hash"):
            _digest(getattr(self, name), name)
        object.__setattr__(
            self, "proof_hash", canonical_sha256(self, exclude_fields=("proof_hash",))
        )


@dataclass(frozen=True, slots=True)
class AuditExportBundle:
    schema_version: str
    request_hash: str
    tenant_id: str
    owner_user_id: str
    ordered_events: tuple[AuditExportEvent, ...]
    range_proofs: tuple[AuditExportRangeProof, ...]
    verification_results: tuple[AuditChainVerificationResult, ...]
    redaction_policy_id: str
    redaction_policy_version: str
    redaction_policy_digest: str
    exported_event_count: int
    truncated: bool
    continuation_token: str | None
    exported_at: datetime
    canonical_order: str = "chain_id,sequence_number"
    owner_private: bool = True
    business_authority: bool = False
    bundle_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != "audit-export-bundle-1.0.0":
            raise ContractValidationError("unsupported audit export schema")
        _digest(self.request_hash, "request_hash")
        _text(self.tenant_id, "tenant_id")
        _text(self.owner_user_id, "owner_user_id")
        events = tuple(self.ordered_events)
        if not events or len(events) > MAX_AUDIT_EXPORT_EVENTS:
            raise ContractValidationError("audit export exceeds event bound")
        ordered = tuple(
            sorted(
                events,
                key=lambda item: (
                    item.envelope.chain_id,
                    item.envelope.sequence_number,
                ),
            )
        )
        if events != ordered:
            raise ContractValidationError("audit export order is non-canonical")
        if any(
            item.envelope.tenant_id != self.tenant_id
            or item.envelope.owner_user_id != self.owner_user_id
            for item in events
        ):
            raise ContractValidationError("audit export contains foreign events")
        if self.exported_event_count != len(events):
            raise ContractValidationError("exported_event_count mismatch")
        if not isinstance(self.truncated, bool):
            raise ContractValidationError("truncated must be boolean")
        if self.truncated != (self.continuation_token is not None):
            raise ContractValidationError("continuation token/truncation mismatch")
        _optional_text(self.continuation_token, "continuation_token")
        object.__setattr__(self, "ordered_events", events)
        proofs = tuple(self.range_proofs)
        results = tuple(self.verification_results)
        if len(proofs) != 1 or any(
            not isinstance(item, AuditExportRangeProof) for item in proofs
        ):
            raise ContractValidationError("audit export range proofs are invalid")
        if len(results) != 1 or any(
            not isinstance(item, AuditChainVerificationResult) for item in results
        ):
            raise ContractValidationError("audit export verification results are invalid")
        if any(not item.verified for item in results):
            raise ContractValidationError("audit export cannot carry a broken proof")
        first = events[0].envelope
        last = events[-1].envelope
        proof = proofs[0]
        result = results[0]
        if (
            proof.chain_id != first.chain_id
            or proof.start_sequence != first.sequence_number
            or proof.end_sequence != last.sequence_number
            or proof.predecessor_hash != first.previous_event_hash
            or proof.first_event_hash != first.event_hash
            or proof.last_event_hash != last.event_hash
            or result.chain_id != first.chain_id
            or result.event_count != len(events)
            or result.first_sequence != first.sequence_number
            or result.last_sequence != last.sequence_number
            or result.first_hash != first.event_hash
            or result.last_hash != last.event_hash
        ):
            raise ContractValidationError("audit export proof differs from events")
        object.__setattr__(self, "range_proofs", proofs)
        object.__setattr__(
            self, "verification_results", results
        )
        _text(self.redaction_policy_id, "redaction_policy_id")
        _text(self.redaction_policy_version, "redaction_policy_version", 64)
        _digest(self.redaction_policy_digest, "redaction_policy_digest")
        expected_redaction = AuditRedactionPolicy()
        if (
            self.redaction_policy_id != expected_redaction.policy_id
            or self.redaction_policy_version != expected_redaction.policy_version
            or self.redaction_policy_digest != expected_redaction.policy_digest
        ):
            raise ContractValidationError("audit export redaction policy differs")
        object.__setattr__(self, "exported_at", _time(self.exported_at, "exported_at"))
        if self.canonical_order != "chain_id,sequence_number":
            raise ContractValidationError("canonical export order cannot change")
        if self.owner_private is not True or self.business_authority is not False:
            raise ContractValidationError("audit export authority boundary changed")
        if len(canonical_json_bytes(self, exclude_fields=("bundle_hash",))) > MAX_AUDIT_EXPORT_BYTES:
            raise ContractValidationError("audit export exceeds byte bound")
        object.__setattr__(
            self, "bundle_hash", canonical_sha256(self, exclude_fields=("bundle_hash",))
        )


def build_audit_event_envelope(
    draft: AuditEventDraft,
    *,
    sequence_number: int,
    previous_event_hash: str,
) -> AuditEventEnvelope:
    if not isinstance(draft, AuditEventDraft):
        raise TypeError("draft must be AuditEventDraft")
    verify_audit_event_draft(draft)
    return AuditEventEnvelope(
        schema_version=draft.schema_version,
        event_type=draft.event_type,
        tenant_id=draft.tenant_id,
        owner_user_id=draft.owner_user_id,
        personal_memory_space_id=draft.personal_memory_space_id,
        kernel_run_id=draft.kernel_run_id,
        request_id=draft.request_id,
        subject_type=draft.subject_type,
        subject_id=draft.subject_id,
        subject_hash=draft.subject_hash,
        actor_type=draft.actor_type,
        actor_id=draft.actor_id,
        policy_id=draft.policy_id,
        policy_version=draft.policy_version,
        policy_digest=draft.policy_digest,
        route_hash=draft.route_hash,
        lineage_hashes=draft.lineage_hashes,
        reason_codes=draft.reason_codes,
        sequence_number=sequence_number,
        chain_id=compute_audit_chain_id(draft.tenant_id, draft.owner_user_id),
        previous_event_hash=previous_event_hash,
        event_payload_digest=draft.event_payload_digest,
        idempotency_key=draft.idempotency_key,
        draft_hash=draft.draft_hash,
        occurred_at=draft.occurred_at,
        recorded_at=draft.recorded_at,
    )


def build_audit_append_receipt(event: AuditEventEnvelope) -> AuditAppendReceipt:
    if not isinstance(event, AuditEventEnvelope):
        raise TypeError("event must be AuditEventEnvelope")
    verify_audit_event_envelope(event)
    return AuditAppendReceipt(
        chain_id=event.chain_id,
        sequence_number=event.sequence_number,
        event_id=event.event_id,
        event_hash=event.event_hash,
        previous_event_hash=event.previous_event_hash,
        subject_type=event.subject_type,
        subject_id=event.subject_id,
        subject_hash=event.subject_hash,
        idempotency_key=event.idempotency_key,
    )


def build_audit_ledger_entry(
    event: AuditEventEnvelope,
    payload: Mapping[str, Any],
) -> AuditLedgerEntry:
    verify_audit_event_envelope(event)
    return AuditLedgerEntry(
        envelope=event,
        event_payload=payload,
        append_receipt=build_audit_append_receipt(event),
    )


def verify_audit_event_envelope(event: AuditEventEnvelope) -> None:
    if not isinstance(event, AuditEventEnvelope):
        raise TypeError("event must be AuditEventEnvelope")
    expected = AuditEventEnvelope(
        schema_version=event.schema_version,
        event_type=event.event_type,
        tenant_id=event.tenant_id,
        owner_user_id=event.owner_user_id,
        personal_memory_space_id=event.personal_memory_space_id,
        kernel_run_id=event.kernel_run_id,
        request_id=event.request_id,
        subject_type=event.subject_type,
        subject_id=event.subject_id,
        subject_hash=event.subject_hash,
        actor_type=event.actor_type,
        actor_id=event.actor_id,
        policy_id=event.policy_id,
        policy_version=event.policy_version,
        policy_digest=event.policy_digest,
        route_hash=event.route_hash,
        lineage_hashes=event.lineage_hashes,
        reason_codes=event.reason_codes,
        sequence_number=event.sequence_number,
        chain_id=event.chain_id,
        previous_event_hash=event.previous_event_hash,
        event_payload_digest=event.event_payload_digest,
        idempotency_key=event.idempotency_key,
        draft_hash=event.draft_hash,
        occurred_at=event.occurred_at,
        recorded_at=event.recorded_at,
        hash_domain=event.hash_domain,
    )
    if event.event_id != expected.event_id or event.event_hash != expected.event_hash:
        raise IntegrityError("audit event envelope integrity mismatch")
    verify_canonical_hash(event, event.event_hash, exclude_fields=("event_hash",))


def verify_audit_event_draft(draft: AuditEventDraft) -> None:
    if not isinstance(draft, AuditEventDraft):
        raise TypeError("draft must be AuditEventDraft")
    expected = AuditEventDraft(
        event_type=draft.event_type,
        tenant_id=draft.tenant_id,
        owner_user_id=draft.owner_user_id,
        personal_memory_space_id=draft.personal_memory_space_id,
        kernel_run_id=draft.kernel_run_id,
        request_id=draft.request_id,
        subject_type=draft.subject_type,
        subject_id=draft.subject_id,
        subject_hash=draft.subject_hash,
        actor_type=draft.actor_type,
        actor_id=draft.actor_id,
        idempotency_key=draft.idempotency_key,
        occurred_at=draft.occurred_at,
        recorded_at=draft.recorded_at,
        event_payload=draft.event_payload,
        reason_codes=draft.reason_codes,
        policy_id=draft.policy_id,
        policy_version=draft.policy_version,
        policy_digest=draft.policy_digest,
        route_hash=draft.route_hash,
        lineage_hashes=draft.lineage_hashes,
        schema_version=draft.schema_version,
    )
    if (
        draft.event_payload_digest != expected.event_payload_digest
        or draft.draft_hash != expected.draft_hash
    ):
        raise IntegrityError("audit event draft integrity mismatch")


def verify_audit_append_receipt(receipt: AuditAppendReceipt) -> None:
    if not isinstance(receipt, AuditAppendReceipt):
        raise TypeError("receipt must be AuditAppendReceipt")
    verify_canonical_hash(
        receipt, receipt.receipt_hash, exclude_fields=("receipt_hash",)
    )


def verify_audit_ledger_entry(entry: AuditLedgerEntry) -> None:
    if not isinstance(entry, AuditLedgerEntry):
        raise TypeError("entry must be AuditLedgerEntry")
    expected = AuditLedgerEntry(
        envelope=entry.envelope,
        event_payload=entry.event_payload,
        append_receipt=entry.append_receipt,
    )
    if entry.entry_hash != expected.entry_hash:
        raise IntegrityError("audit ledger entry integrity mismatch")


def audit_to_jsonb(value: object) -> Mapping[str, Any]:
    data = to_canonical_data(value)
    if not isinstance(data, Mapping):
        raise ContractValidationError("audit JSONB value must be an object")
    return data


def _parse_datetime(value: object, name: str) -> datetime:
    if not isinstance(value, str):
        raise ContractValidationError(f"{name} must be a canonical timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContractValidationError(f"{name} is invalid") from exc
    return ensure_utc(parsed, name)


def _parse_mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ContractValidationError(f"{name} must be a string-keyed mapping")
    return value


def parse_audit_event_envelope(value: object) -> AuditEventEnvelope:
    data = _parse_mapping(value, "audit event envelope")
    expected_fields = {
        field.name for field in __import__("dataclasses").fields(AuditEventEnvelope)
    }
    if set(data) != expected_fields:
        raise ContractValidationError("audit event envelope fields differ")
    try:
        event = AuditEventEnvelope(
            schema_version=data["schema_version"],
            event_type=AuditEventType(data["event_type"]),
            tenant_id=data["tenant_id"],
            owner_user_id=data["owner_user_id"],
            personal_memory_space_id=data["personal_memory_space_id"],
            kernel_run_id=data["kernel_run_id"],
            request_id=data["request_id"],
            subject_type=AuditSubjectType(data["subject_type"]),
            subject_id=data["subject_id"],
            subject_hash=data["subject_hash"],
            actor_type=AuditActorType(data["actor_type"]),
            actor_id=data["actor_id"],
            policy_id=data["policy_id"],
            policy_version=data["policy_version"],
            policy_digest=data["policy_digest"],
            route_hash=data["route_hash"],
            lineage_hashes=_parse_mapping(data["lineage_hashes"], "lineage_hashes"),
            reason_codes=tuple(AuditReasonCode(item) for item in data["reason_codes"]),
            sequence_number=data["sequence_number"],
            chain_id=data["chain_id"],
            previous_event_hash=data["previous_event_hash"],
            event_payload_digest=data["event_payload_digest"],
            idempotency_key=data["idempotency_key"],
            draft_hash=data["draft_hash"],
            occurred_at=_parse_datetime(data["occurred_at"], "occurred_at"),
            recorded_at=_parse_datetime(data["recorded_at"], "recorded_at"),
            hash_domain=data["hash_domain"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ContractValidationError("audit event envelope is invalid") from exc
    if event.event_id != data["event_id"] or event.event_hash != data["event_hash"]:
        raise IntegrityError("persisted audit event envelope integrity mismatch")
    return event


def parse_audit_append_receipt(value: object) -> AuditAppendReceipt:
    data = _parse_mapping(value, "audit append receipt")
    expected_fields = {
        field.name for field in __import__("dataclasses").fields(AuditAppendReceipt)
    }
    if set(data) != expected_fields:
        raise ContractValidationError("audit append receipt fields differ")
    try:
        receipt = AuditAppendReceipt(
            chain_id=data["chain_id"],
            sequence_number=data["sequence_number"],
            event_id=data["event_id"],
            event_hash=data["event_hash"],
            previous_event_hash=data["previous_event_hash"],
            subject_type=AuditSubjectType(data["subject_type"]),
            subject_id=data["subject_id"],
            subject_hash=data["subject_hash"],
            idempotency_key=data["idempotency_key"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ContractValidationError("audit append receipt is invalid") from exc
    if receipt.receipt_hash != data["receipt_hash"]:
        raise IntegrityError("persisted append receipt integrity mismatch")
    return receipt


def parse_audit_ledger_entry(value: object) -> AuditLedgerEntry:
    data = _parse_mapping(value, "audit ledger entry")
    if set(data) != {"envelope", "event_payload", "append_receipt", "entry_hash"}:
        raise ContractValidationError("audit ledger entry fields differ")
    entry = AuditLedgerEntry(
        envelope=parse_audit_event_envelope(data["envelope"]),
        event_payload=_parse_mapping(data["event_payload"], "event_payload"),
        append_receipt=parse_audit_append_receipt(data["append_receipt"]),
    )
    if entry.entry_hash != data["entry_hash"]:
        raise IntegrityError("persisted audit ledger entry integrity mismatch")
    return entry
