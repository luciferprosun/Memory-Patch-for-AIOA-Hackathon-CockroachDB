"""Correction Candidate, requirement, and frozen packet contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .enums import (
    ActionPolicy,
    ActorType,
    CorrectionCandidateState,
    EvidenceStatus,
    KnowledgeRoute,
)
from .evidence import ClaimCandidate, EvidenceItem
from .exceptions import ContractValidationError, OwnershipViolation
from .identities import KernelRunIdentity, MemoryOwnership, verify_run_ownership
from .personal_memory import MemoryConflict
from .scope import ScopeDimension
from .serialization import (
    canonical_sha256,
    ensure_utc,
    freeze_string_tuple,
    freeze_typed_tuple,
    require_enum_member,
    require_non_empty,
    require_schema_version,
    verify_canonical_hash,
)


_CORRECTION_PRODUCERS = frozenset(
    {
        ActorType.KNOWLEDGE_KERNEL,
        ActorType.KNOWLEDGE_HAT,
        ActorType.KNOWLEDGE_HUB,
        ActorType.CRITIC_PROMPT_LOOP,
        ActorType.MODEL_VERIFIER,
        ActorType.USER,
        ActorType.HUMAN_REVIEWER,
    }
)


@dataclass(frozen=True, slots=True)
class CorrectionCandidate:
    """Proposal-only correction signal; never approval, memory, or authority."""

    event_id: str
    tenant_id: str
    user_id: str
    personal_memory_space_id: str
    source_component: ActorType
    run_id: str
    model_binding_id: str
    draft_v1_reference: str
    detected_claims: tuple[ClaimCandidate, ...]
    proposed_correction: str
    available_evidence_references: tuple[str, ...]
    uncertainty: float
    created_at: datetime
    state: CorrectionCandidateState
    content_hash: str = field(init=False)

    def __post_init__(self) -> None:
        for field_name in (
            "event_id",
            "tenant_id",
            "user_id",
            "personal_memory_space_id",
            "run_id",
            "model_binding_id",
            "draft_v1_reference",
            "proposed_correction",
        ):
            require_non_empty(getattr(self, field_name), field_name)
        require_enum_member(
            self.source_component, ActorType, "source_component"
        )
        require_enum_member(
            self.state, CorrectionCandidateState, "state"
        )
        if self.source_component not in _CORRECTION_PRODUCERS:
            raise ContractValidationError(
                f"{self.source_component.value} cannot produce Correction Candidates"
            )
        object.__setattr__(
            self,
            "detected_claims",
            freeze_typed_tuple(
                self.detected_claims,
                ClaimCandidate,
                "detected_claims",
            ),
        )
        object.__setattr__(
            self,
            "available_evidence_references",
            freeze_string_tuple(
                self.available_evidence_references,
                "available_evidence_references",
                unique=True,
            ),
        )
        if not self.detected_claims:
            raise ContractValidationError(
                "a Correction Candidate requires at least one detected claim"
            )
        claim_ids = [claim.claim_id for claim in self.detected_claims]
        if len(claim_ids) != len(set(claim_ids)):
            raise ContractValidationError(
                "Correction Candidate claim IDs must be unique"
            )
        if not isinstance(self.uncertainty, (int, float)) or isinstance(
            self.uncertainty, bool
        ):
            raise ContractValidationError("uncertainty must be numeric")
        if not 0.0 <= float(self.uncertainty) <= 1.0:
            raise ContractValidationError("uncertainty must be between 0 and 1")
        object.__setattr__(
            self, "created_at", ensure_utc(self.created_at, "created_at")
        )
        object.__setattr__(
            self,
            "content_hash",
            canonical_sha256(self, exclude_fields=("content_hash",)),
        )

    @property
    def ownership(self) -> MemoryOwnership:
        return MemoryOwnership(
            tenant_id=self.tenant_id,
            user_id=self.user_id,
            personal_memory_space_id=self.personal_memory_space_id,
        )


@dataclass(frozen=True, slots=True)
class CorrectionRequirement:
    """A declarative, evidence-bound correction instruction."""

    requirement_id: str
    claim_id: str
    instruction: str
    evidence_references: tuple[str, ...]
    mandatory: bool

    def __post_init__(self) -> None:
        require_non_empty(self.requirement_id, "requirement_id")
        require_non_empty(self.claim_id, "claim_id")
        require_non_empty(self.instruction, "instruction")
        if self.mandatory is not True and self.mandatory is not False:
            raise ContractValidationError("mandatory must be a boolean")
        object.__setattr__(
            self,
            "evidence_references",
            freeze_string_tuple(
                self.evidence_references,
                "evidence_references",
                unique=True,
            ),
        )
        if self.mandatory and not self.evidence_references:
            raise ContractValidationError(
                "mandatory factual correction requires evidence references"
            )


@dataclass(frozen=True, slots=True)
class CorrectionPacket:
    """Frozen evidence and correction input for a future Draft V2 generator."""

    schema_version: str
    kernel_run_id: str
    draft_v1_id: str
    selected_hat_id: str
    knowledge_route: KnowledgeRoute
    action_policy: ActionPolicy
    evidence_status: EvidenceStatus
    scope_dimensions: tuple[ScopeDimension, ...]
    knowledge_as_of: datetime | None
    claims_under_review: tuple[ClaimCandidate, ...]
    ordered_evidence_items: tuple[EvidenceItem, ...]
    source_version_ids: tuple[str, ...]
    validity_or_version_scopes: tuple[ScopeDimension, ...]
    conflicts: tuple[MemoryConflict, ...]
    required_corrections: tuple[CorrectionRequirement, ...]
    prohibited_claims: tuple[str, ...]
    uncertainty: float
    citation_requirements: tuple[str, ...]
    retrieval_policy_version: str
    embedding_model_version: str | None
    packet_hash: str = field(init=False)

    def __post_init__(self) -> None:
        require_schema_version(self.schema_version)
        for field_name in (
            "kernel_run_id",
            "draft_v1_id",
            "selected_hat_id",
            "retrieval_policy_version",
        ):
            require_non_empty(getattr(self, field_name), field_name)
        require_enum_member(
            self.knowledge_route, KnowledgeRoute, "knowledge_route"
        )
        require_enum_member(self.action_policy, ActionPolicy, "action_policy")
        require_enum_member(
            self.evidence_status, EvidenceStatus, "evidence_status"
        )
        for field_name, expected_type in (
            ("scope_dimensions", ScopeDimension),
            ("claims_under_review", ClaimCandidate),
            ("ordered_evidence_items", EvidenceItem),
            ("validity_or_version_scopes", ScopeDimension),
            ("conflicts", MemoryConflict),
            ("required_corrections", CorrectionRequirement),
        ):
            object.__setattr__(
                self,
                field_name,
                freeze_typed_tuple(
                    getattr(self, field_name),
                    expected_type,
                    field_name,
                ),
            )
        for field_name in (
            "source_version_ids",
            "prohibited_claims",
            "citation_requirements",
        ):
            object.__setattr__(
                self,
                field_name,
                freeze_string_tuple(
                    getattr(self, field_name),
                    field_name,
                    unique=field_name == "source_version_ids",
                ),
            )
        if self.embedding_model_version is not None:
            require_non_empty(
                self.embedding_model_version,
                "embedding_model_version",
            )
        if self.knowledge_route not in {
            KnowledgeRoute.HAT_ASSIST,
            KnowledgeRoute.HAT_ENFORCE,
        }:
            raise ContractValidationError(
                "a Correction Packet requires a HAT-assisted or enforced route"
            )
        if self.knowledge_as_of is not None:
            object.__setattr__(
                self,
                "knowledge_as_of",
                ensure_utc(self.knowledge_as_of, "knowledge_as_of"),
            )
        if not self.claims_under_review:
            raise ContractValidationError(
                "Correction Packet requires claims under review"
            )
        claim_ids = [claim.claim_id for claim in self.claims_under_review]
        if len(claim_ids) != len(set(claim_ids)):
            raise ContractValidationError(
                "Correction Packet claim IDs must be unique"
            )
        evidence_ids = [
            item.evidence_id for item in self.ordered_evidence_items
        ]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ContractValidationError(
                "Correction Packet evidence IDs must be unique"
            )
        requirement_ids = [
            requirement.requirement_id
            for requirement in self.required_corrections
        ]
        if len(requirement_ids) != len(set(requirement_ids)):
            raise ContractValidationError(
                "Correction requirement IDs must be unique"
            )
        if not isinstance(self.uncertainty, (int, float)) or isinstance(
            self.uncertainty, bool
        ):
            raise ContractValidationError("uncertainty must be numeric")
        if not 0.0 <= float(self.uncertainty) <= 1.0:
            raise ContractValidationError("uncertainty must be between 0 and 1")
        ordered_versions = tuple(
            item.source_version_id for item in self.ordered_evidence_items
        )
        if set(ordered_versions) != set(self.source_version_ids):
            raise ContractValidationError(
                "source_version_ids must exactly cover ordered evidence"
            )
        if len(self.source_version_ids) != len(set(self.source_version_ids)):
            raise ContractValidationError("source_version_ids must be unique")
        if (
            self.evidence_status is EvidenceStatus.SUFFICIENT
            and not self.ordered_evidence_items
        ):
            raise ContractValidationError(
                "sufficient Correction Packet requires evidence"
            )
        object.__setattr__(
            self, "packet_hash", compute_correction_packet_hash(self)
        )


def validate_correction_candidate_ownership(
    candidate: CorrectionCandidate,
    *,
    run: KernelRunIdentity,
    target_ownership: MemoryOwnership,
) -> None:
    """Block a Critic or Kernel event from creating memory for another owner."""

    if candidate.run_id != run.kernel_run_id:
        raise OwnershipViolation("Correction Candidate references another run")
    if candidate.model_binding_id != run.model_binding_id:
        raise OwnershipViolation("model binding mismatch")
    verify_run_ownership(run, target_ownership)
    if candidate.ownership != target_ownership:
        raise OwnershipViolation("Correction Candidate target ownership mismatch")


def compute_correction_candidate_hash(candidate: CorrectionCandidate) -> str:
    """Compute a candidate digest excluding its own hash."""

    return canonical_sha256(candidate, exclude_fields=("content_hash",))


def verify_correction_candidate_hash(candidate: CorrectionCandidate) -> None:
    """Verify candidate integrity."""

    verify_canonical_hash(
        candidate, candidate.content_hash, exclude_fields=("content_hash",)
    )


def compute_correction_packet_hash(packet: CorrectionPacket) -> str:
    """Compute deterministic identity for the frozen packet."""

    return canonical_sha256(packet, exclude_fields=("packet_hash",))


def verify_correction_packet_hash(packet: CorrectionPacket) -> None:
    """Verify frozen packet integrity."""

    verify_canonical_hash(
        packet, packet.packet_hash, exclude_fields=("packet_hash",)
    )
