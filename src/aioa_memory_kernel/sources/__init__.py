"""Authority-neutral source registry, provenance, and publication control plane.

Registration and publication state do not grant answer, approval, commit,
memory activation, external-action, or Control Write authority.
"""

from .eligibility import evaluate_publication_eligibility
from .errors import (
    ProvenanceConflictError,
    ProvenanceCycleError,
    PublicationEligibilityError,
    PublicationEventChainError,
    PublicationTransitionError,
    SourceRegistryConflictError,
    SourceRegistryError,
    SourceRegistryValidationError,
)
from .models import (
    PUBLICATION_ELIGIBILITY_POLICY_VERSION,
    PUBLICATION_GENESIS_DIGEST,
    PUBLICATION_GENESIS_MARKER,
    OriginMetadata,
    ParserIdentity,
    ProvenanceArtifactIdentity,
    ProvenanceEdge,
    PublicationEligibilityDecision,
    PublicationStateEvent,
    RedactionState,
    SourceAccessClass,
    SourceAuthorityAssessment,
    SourceAuthorityLevel,
    SourceLicenseAssessment,
    SourceLicenseStatus,
    SourcePublicationState,
    SourceRegistryActor,
    SourceRegistryActorType,
    SourceRegistryRecord,
    SourceScopeDimensions,
    TransformationIdentity,
)
from .provenance import MAX_PROVENANCE_NODES, ProvenanceGraph
from .protocols import SourceRegistryRepositoryProtocol
from .registry import (
    CockroachSourceRegistryRepository,
    SourceRegistryService,
    edge_from_row,
    event_from_row,
    registry_from_row,
)
from .states import (
    ALLOWED_PUBLICATION_TRANSITIONS,
    advance_registry_state,
    build_publication_event,
    require_publication_transition,
    verify_publication_event_chain,
)


__all__ = [
    "ALLOWED_PUBLICATION_TRANSITIONS",
    "CockroachSourceRegistryRepository",
    "MAX_PROVENANCE_NODES",
    "OriginMetadata",
    "PUBLICATION_ELIGIBILITY_POLICY_VERSION",
    "PUBLICATION_GENESIS_DIGEST",
    "PUBLICATION_GENESIS_MARKER",
    "ParserIdentity",
    "ProvenanceArtifactIdentity",
    "ProvenanceConflictError",
    "ProvenanceCycleError",
    "ProvenanceEdge",
    "ProvenanceGraph",
    "PublicationEligibilityDecision",
    "PublicationEligibilityError",
    "PublicationEventChainError",
    "PublicationStateEvent",
    "PublicationTransitionError",
    "RedactionState",
    "SourceAccessClass",
    "SourceAuthorityAssessment",
    "SourceAuthorityLevel",
    "SourceLicenseAssessment",
    "SourceLicenseStatus",
    "SourcePublicationState",
    "SourceRegistryActor",
    "SourceRegistryActorType",
    "SourceRegistryConflictError",
    "SourceRegistryError",
    "SourceRegistryRecord",
    "SourceRegistryRepositoryProtocol",
    "SourceRegistryService",
    "SourceRegistryValidationError",
    "SourceScopeDimensions",
    "TransformationIdentity",
    "advance_registry_state",
    "build_publication_event",
    "edge_from_row",
    "evaluate_publication_eligibility",
    "event_from_row",
    "registry_from_row",
    "require_publication_transition",
    "verify_publication_event_chain",
]
