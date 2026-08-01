"""Bounded official-source acquisition with no semantic authority."""

from .archive import validate_pdf, validate_xml, validate_zip
from .errors import (
    AcquisitionError,
    AcquisitionIntegrityError,
    AcquisitionPolicyError,
    AcquisitionStorageError,
    AcquisitionTransportError,
)
from .http import SafeHttpsClient
from .models import AcquisitionPolicy, HttpObjectReceipt, SourceStatus
from .storage import AcquisitionRootGuard

__all__ = [
    "AcquisitionError",
    "AcquisitionIntegrityError",
    "AcquisitionPolicy",
    "AcquisitionPolicyError",
    "AcquisitionRootGuard",
    "AcquisitionStorageError",
    "AcquisitionTransportError",
    "HttpObjectReceipt",
    "SafeHttpsClient",
    "SourceStatus",
    "validate_pdf",
    "validate_xml",
    "validate_zip",
]
