"""Frozen evidence, claim, and claim-verdict contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping

from .enums import (
    ClaimVerdictStatus,
    EvidenceStatus,
    MemoryTrustClass,
)
from .exceptions import ContractValidationError
from .scope import ScopeDimension, scope_interval_is_valid
from .serialization import (
    canonical_sha256,
    ensure_utc,
    freeze_json,
    freeze_string_tuple,
    freeze_typed_tuple,
    require_enum_member,
    require_non_empty,
    require_sha256_hex,
    verify_canonical_hash,
)


_EVIDENCE_TRUST_CLASSES = frozenset(
    {
        MemoryTrustClass.CANONICAL_SOURCE_EVIDENCE,
        MemoryTrustClass.SHARED_HAT_VERIFIED_MEMORY,
    }
)


@dataclass(frozen=True, slots=True)
class EvidenceItem:
    """A reference to source-version bytes; model experience is never evidence."""

    evidence_id: str
    source_id: str
    source_version_id: str
    citation_reference: str
    content_hash: str
    trust_class: MemoryTrustClass
    authority_rank: int
    scope_dimensions: tuple[ScopeDimension, ...]
    retrieved_at: datetime
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for field_name in (
            "evidence_id",
            "source_id",
            "source_version_id",
            "citation_reference",
        ):
            require_non_empty(getattr(self, field_name), field_name)
        require_sha256_hex(self.content_hash, "content_hash")
        require_enum_member(
            self.trust_class, MemoryTrustClass, "trust_class"
        )
        if self.trust_class not in _EVIDENCE_TRUST_CLASSES:
            raise ContractValidationError(
                f"{self.trust_class.value} is memory or advice, not factual evidence"
            )
        if (
            not isinstance(self.authority_rank, int)
            or isinstance(self.authority_rank, bool)
            or self.authority_rank < 0
        ):
            raise ContractValidationError(
                "authority_rank must be a non-negative integer"
            )
        object.__setattr__(
            self,
            "scope_dimensions",
            freeze_typed_tuple(
                self.scope_dimensions,
                ScopeDimension,
                "scope_dimensions",
            ),
        )
        object.__setattr__(
            self, "retrieved_at", ensure_utc(self.retrieved_at, "retrieved_at")
        )
        for field_name in ("valid_from", "valid_until"):
            timestamp = getattr(self, field_name)
            if timestamp is not None:
                object.__setattr__(
                    self, field_name, ensure_utc(timestamp, field_name)
                )
        if not scope_interval_is_valid(self.valid_from, self.valid_until):
            raise ContractValidationError("evidence validity interval is inverted")
        scope_names = [dimension.name for dimension in self.scope_dimensions]
        if len(scope_names) != len(set(scope_names)):
            raise ContractValidationError(
                "evidence scope dimension names must be unique"
            )
        object.__setattr__(self, "metadata", freeze_json(self.metadata))


@dataclass(frozen=True, slots=True)
class EvidenceBundle:
    """Ordered, frozen evidence selected under one retrieval policy."""

    evidence_bundle_id: str
    kernel_run_id: str
    hat_id: str
    evidence_status: EvidenceStatus
    ordered_items: tuple[EvidenceItem, ...]
    retrieval_policy_version: str
    created_at: datetime
    bundle_hash: str = field(init=False)

    def __post_init__(self) -> None:
        for field_name in (
            "evidence_bundle_id",
            "kernel_run_id",
            "hat_id",
            "retrieval_policy_version",
        ):
            require_non_empty(getattr(self, field_name), field_name)
        require_enum_member(
            self.evidence_status, EvidenceStatus, "evidence_status"
        )
        object.__setattr__(
            self,
            "ordered_items",
            freeze_typed_tuple(
                self.ordered_items,
                EvidenceItem,
                "ordered_items",
            ),
        )
        ids = [item.evidence_id for item in self.ordered_items]
        if len(ids) != len(set(ids)):
            raise ContractValidationError("Evidence Bundle item IDs must be unique")
        if (
            self.evidence_status is EvidenceStatus.SUFFICIENT
            and not self.ordered_items
        ):
            raise ContractValidationError(
                "SUFFICIENT evidence status requires at least one evidence item"
            )
        if (
            self.evidence_status is EvidenceStatus.NOT_REQUIRED
            and self.ordered_items
        ):
            raise ContractValidationError(
                "NOT_REQUIRED Evidence Bundle must not contain evidence items"
            )
        object.__setattr__(
            self, "created_at", ensure_utc(self.created_at, "created_at")
        )
        object.__setattr__(
            self, "bundle_hash", compute_evidence_bundle_hash(self)
        )


@dataclass(frozen=True, slots=True)
class ClaimCandidate:
    """A draft claim identified for verification."""

    claim_id: str
    draft_id: str
    statement: str
    claim_category: str
    scope_dimensions: tuple[ScopeDimension, ...] = ()

    def __post_init__(self) -> None:
        for field_name in ("claim_id", "draft_id", "statement", "claim_category"):
            require_non_empty(getattr(self, field_name), field_name)
        object.__setattr__(
            self,
            "scope_dimensions",
            freeze_typed_tuple(
                self.scope_dimensions,
                ScopeDimension,
                "scope_dimensions",
            ),
        )


@dataclass(frozen=True, slots=True)
class ClaimVerdict:
    """Evidence-bound verification outcome for one claim."""

    claim_id: str
    verdict: ClaimVerdictStatus
    evidence_references: tuple[str, ...]
    verifier_id: str
    verified_at: datetime
    explanation_code: str

    def __post_init__(self) -> None:
        require_non_empty(self.claim_id, "claim_id")
        require_enum_member(self.verdict, ClaimVerdictStatus, "verdict")
        require_non_empty(self.verifier_id, "verifier_id")
        require_non_empty(self.explanation_code, "explanation_code")
        object.__setattr__(
            self,
            "evidence_references",
            freeze_string_tuple(
                self.evidence_references,
                "evidence_references",
                unique=True,
            ),
        )
        if (
            self.verdict is ClaimVerdictStatus.SUPPORTED
            and not self.evidence_references
        ):
            raise ContractValidationError(
                "a supported claim requires evidence references"
            )
        object.__setattr__(
            self, "verified_at", ensure_utc(self.verified_at, "verified_at")
        )


def compute_evidence_bundle_hash(bundle: EvidenceBundle) -> str:
    """Calculate a bundle digest while excluding its own digest field."""

    return canonical_sha256(bundle, exclude_fields=("bundle_hash",))


def verify_evidence_bundle_hash(bundle: EvidenceBundle) -> None:
    """Verify the immutable bundle identity."""

    verify_canonical_hash(
        bundle, bundle.bundle_hash, exclude_fields=("bundle_hash",)
    )
