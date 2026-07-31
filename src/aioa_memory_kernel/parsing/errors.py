"""Typed, sanitized failures for the deterministic Step 11 pipeline."""

from __future__ import annotations


class ParsingError(RuntimeError):
    """Base parsing error whose public code is safe to persist."""

    def __init__(self, message: str, *, sanitized_code: str) -> None:
        super().__init__(message)
        self.sanitized_code = sanitized_code


class ParsingValidationError(ParsingError):
    """A typed contract, identity, or structural validation failure."""


class UnsupportedMediaTypeError(ParsingError):
    """No exact immutable parser profile exists for the media type."""


class ParsingResourceLimitError(ParsingError):
    """A versioned Step 11 resource limit was exceeded."""


class ParsingPersistenceConflictError(ParsingError):
    """A deterministic identity is already bound to different facts."""


class ParsingQuarantineError(ParsingError):
    """The parse result requires deterministic quarantine or review."""
