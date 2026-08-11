"""Step 36 credential, capability, and redaction boundaries."""

from .credentials import (
    AWS_WORKLOAD_IDENTITY_ENVIRONMENT_NAMES,
    CREDENTIAL_SPECS,
    CredentialBoundaryError,
    CredentialPurpose,
    CredentialSpec,
    SecretValue,
    build_minimal_subprocess_environment,
    credential_inventory_digest,
    load_required_credential,
)
from .redaction import (
    FORBIDDEN_SECRET_KEY_FRAGMENTS,
    SAFE_SECURITY_METADATA_KEYS,
    assert_secret_free,
    contains_secret_material,
    redact_exception,
    redact_text,
)

__all__ = [
    "AWS_WORKLOAD_IDENTITY_ENVIRONMENT_NAMES",
    "CREDENTIAL_SPECS",
    "CredentialBoundaryError",
    "CredentialPurpose",
    "CredentialSpec",
    "FORBIDDEN_SECRET_KEY_FRAGMENTS",
    "SAFE_SECURITY_METADATA_KEYS",
    "SecretValue",
    "assert_secret_free",
    "build_minimal_subprocess_environment",
    "contains_secret_material",
    "credential_inventory_digest",
    "load_required_credential",
    "redact_exception",
    "redact_text",
]
