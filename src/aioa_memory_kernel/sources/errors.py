"""Sanitized failures for the source-registry control plane."""

from __future__ import annotations


class SourceRegistryError(RuntimeError):
    """Base error that exposes only a bounded operator-safe code."""

    def __init__(
        self,
        message: str = "source registry operation failed",
        *,
        sanitized_code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.sanitized_code = sanitized_code


class SourceRegistryValidationError(SourceRegistryError):
    """A source, scope, authority, license, or identity value is invalid."""


class SourceRegistryConflictError(SourceRegistryError):
    """An immutable registry identity was reused with different facts."""


class ProvenanceConflictError(SourceRegistryError):
    """A provenance edge identity was reused with different facts."""


class ProvenanceCycleError(SourceRegistryError):
    """A provenance edge would make the bounded lineage graph cyclic."""


class PublicationEligibilityError(SourceRegistryError):
    """A transition requiring eligibility received an ineligible decision."""


class PublicationTransitionError(SourceRegistryError):
    """The requested publication-state transition is not permitted."""


class PublicationEventChainError(SourceRegistryError):
    """The append-only publication event chain is incomplete or tampered."""
