"""Sanitized failures for the bounded Step 14 corpus inventory boundary."""

from __future__ import annotations


class CorpusInventoryError(RuntimeError):
    """Base failure that exposes only a stable, sanitized reason code."""

    def __init__(self, message: str, *, sanitized_code: str) -> None:
        super().__init__(message)
        self.sanitized_code = sanitized_code


class CorpusSafetyError(CorpusInventoryError):
    """The source or derived-data filesystem boundary failed closed."""


class CorpusReplayConflictError(CorpusInventoryError):
    """An existing checkpoint or bundle conflicts with the requested run."""


class CorpusRegistrationConflictError(CorpusInventoryError):
    """A stable logical source identity is bound to conflicting facts."""
