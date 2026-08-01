"""Sanitized failures for the bounded acquisition boundary."""

from __future__ import annotations


class AcquisitionError(RuntimeError):
    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


class AcquisitionPolicyError(AcquisitionError):
    pass


class AcquisitionStorageError(AcquisitionError):
    pass


class AcquisitionTransportError(AcquisitionError):
    pass


class AcquisitionIntegrityError(AcquisitionError):
    pass


__all__ = [
    "AcquisitionError",
    "AcquisitionIntegrityError",
    "AcquisitionPolicyError",
    "AcquisitionStorageError",
    "AcquisitionTransportError",
]
