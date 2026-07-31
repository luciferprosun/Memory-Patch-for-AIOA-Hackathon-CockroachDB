"""Immutable models for the trusted system-installed HAT boundary."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from aioa_memory_kernel.contracts import HatManifest, HatSdk
from aioa_memory_kernel.contracts.serialization import canonical_sha256, ensure_utc, require_non_empty, require_sha256_hex

KERNEL_API_VERSION = "1.0.0"
CAPABILITY_VOCABULARY_VERSION = "hat-capabilities-1a"
CAPABILITY_METHODS = {
    "REQUEST_NORMALIZATION": "normalize_request",
    "SCOPE_DERIVATION": "derive_scope_requirements",
    "EVIDENCE_CONSTRAINTS": "build_retrieval_constraints",
    "SOURCE_AUTHORITY_RANKING": "rank_source_authority",
    "CLAIM_EXTRACTION": "extract_candidate_claims",
    "CONFLICT_DETECTION": "detect_conflicts",
    "CORRECTION_REQUIREMENTS": "create_correction_requirements",
    "CORRECTION_PROPOSAL": "create_memory_patch_proposal",
}

class CompatibilityDecision(str, Enum):
    COMPATIBLE = "COMPATIBLE"
    INCOMPATIBLE_KERNEL_API = "INCOMPATIBLE_KERNEL_API"
    UNSUPPORTED_MANIFEST_SCHEMA = "UNSUPPORTED_MANIFEST_SCHEMA"

class RegistryState(str, Enum):
    REGISTERED = "REGISTERED"
    VALIDATED = "VALIDATED"
    ENABLED = "ENABLED"
    DISABLED = "DISABLED"
    REJECTED = "REJECTED"

class ReviewActor(str, Enum):
    TRUSTED_OPERATOR = "TRUSTED_OPERATOR"
    MIGRATION_SERVICE = "MIGRATION_SERVICE"

@dataclass(frozen=True, slots=True)
class ManifestIdentity:
    manifest: HatManifest
    raw_manifest_sha256: str
    canonical_manifest_sha256: str
    typed_manifest_digest: str
    schema_file_sha256: str

@dataclass(frozen=True, slots=True)
class RuntimeBinding:
    runtime_binding_id: str
    hat_id: str
    hat_version: str
    implementation_name: str
    implementation_version: str
    implementation_contract_version: str
    implementation_digest: str
    installation_class: str = "SYSTEM_INSTALLED"
    def __post_init__(self) -> None:
        for name in ("runtime_binding_id", "hat_id", "hat_version", "implementation_name", "implementation_version", "implementation_contract_version"):
            require_non_empty(getattr(self, name), name)
        require_sha256_hex(self.implementation_digest, "implementation_digest")
        if self.installation_class != "SYSTEM_INSTALLED":
            raise ValueError("runtime binding must be SYSTEM_INSTALLED")
    @property
    def digest(self) -> str:
        return canonical_sha256(self)

@dataclass(frozen=True, slots=True)
class ReviewReceipt:
    hat_id: str
    hat_version: str
    canonical_manifest_digest: str
    raw_manifest_digest: str
    schema_digest: str
    compatibility: CompatibilityDecision
    capabilities_digest: str
    runtime_binding_id: str
    implementation_digest: str
    decision: str
    reason_codes: tuple[str, ...]
    actor_type: ReviewActor
    actor_reference: str
    reviewed_at: datetime
    receipt_digest: str = ""
    def __post_init__(self) -> None:
        if not isinstance(self.actor_type, ReviewActor):
            raise ValueError("untrusted review actor")
        ensure_utc(self.reviewed_at, "reviewed_at")
        expected = canonical_sha256(self, exclude_fields=("receipt_digest",))
        if self.receipt_digest and self.receipt_digest != expected:
            raise ValueError("review receipt digest mismatch")
        object.__setattr__(self, "receipt_digest", expected)

@dataclass(frozen=True, slots=True)
class RegistryEntry:
    identity: ManifestIdentity
    compatibility: CompatibilityDecision
    state: RegistryState
    state_version: int
    current_event_digest: str
    runtime_binding: RuntimeBinding | None = None
    review_receipt: ReviewReceipt | None = None

@dataclass(frozen=True, slots=True)
class RegistryEvent:
    event_id: str
    hat_id: str
    hat_version: str
    sequence: int
    previous_event_digest: str | None
    from_state: RegistryState | None
    to_state: RegistryState
    actor_type: str
    actor_reference: str
    reason_codes: tuple[str, ...]
    occurred_at: datetime
    event_digest: str = ""
    def __post_init__(self) -> None:
        ensure_utc(self.occurred_at, "occurred_at")
        expected = canonical_sha256(self, exclude_fields=("event_digest",))
        if self.event_digest and self.event_digest != expected:
            raise ValueError("event digest mismatch")
        object.__setattr__(self, "event_digest", expected)

@dataclass(frozen=True, slots=True)
class InstalledHat:
    binding: RuntimeBinding
    instance: HatSdk
    canonical_manifest_digest: str
