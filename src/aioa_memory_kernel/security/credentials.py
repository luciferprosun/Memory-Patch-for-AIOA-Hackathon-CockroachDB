"""Typed, fail-closed runtime credential inventory for Step 36.

The module does not open a database connection, call a provider, or retrieve a
secret from an external service.  It gives deployment assembly code one exact
environment name per capability and deliberately has no admin/master fallback.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from aioa_memory_kernel.contracts.enums import StableStringEnum
from aioa_memory_kernel.contracts.serialization import canonical_sha256


_SAFE_SUBPROCESS_ENVIRONMENT_NAMES = frozenset(
    {
        "COMSPEC",
        "HOME",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "TMPDIR",
        "TZ",
        "WINDIR",
    }
)
AWS_WORKLOAD_IDENTITY_ENVIRONMENT_NAMES = (
    "AWS_CA_BUNDLE",
    "AWS_CONFIG_FILE",
    "AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE",
    "AWS_CONTAINER_CREDENTIALS_FULL_URI",
    "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI",
    "AWS_DEFAULT_PROFILE",
    "AWS_DEFAULT_REGION",
    "AWS_EC2_METADATA_DISABLED",
    "AWS_PROFILE",
    "AWS_REGION",
    "AWS_ROLE_ARN",
    "AWS_ROLE_SESSION_NAME",
    "AWS_SDK_LOAD_CONFIG",
    "AWS_SHARED_CREDENTIALS_FILE",
    "AWS_STS_REGIONAL_ENDPOINTS",
    "AWS_WEB_IDENTITY_TOKEN_FILE",
)
_SAFE_SECRET_SOURCE_NAME = re.compile(
    r"^(?:[A-Z][A-Z0-9_]{1,127}|injected-model-provider-credential)$"
)


class CredentialPurpose(StableStringEnum):
    APPLICATION_DATABASE = "APPLICATION_DATABASE"
    PERSONAL_MEMORY_COMMIT_DATABASE = "PERSONAL_MEMORY_COMMIT_DATABASE"
    MIGRATION_DATABASE = "MIGRATION_DATABASE"
    AUDIT_APPENDER_DATABASE = "AUDIT_APPENDER_DATABASE"
    AUDIT_READER_DATABASE = "AUDIT_READER_DATABASE"
    HUMAN_REVIEWER_DATABASE = "HUMAN_REVIEWER_DATABASE"
    REVIEW_SERVICE_DATABASE = "REVIEW_SERVICE_DATABASE"
    SOURCE_PUBLICATION_DATABASE = "SOURCE_PUBLICATION_DATABASE"
    INGESTION_DATABASE = "INGESTION_DATABASE"
    MODEL_PROVIDER = "MODEL_PROVIDER"
    S3_RUNTIME_IDENTITY = "S3_RUNTIME_IDENTITY"


class CredentialBoundaryError(RuntimeError):
    """A dedicated credential is absent, malformed, or used out of scope."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.sanitized_code = code


@dataclass(frozen=True, slots=True)
class CredentialSpec:
    purpose: CredentialPurpose
    logical_name: str
    environment_variable: str | None
    consumer: str
    required_for_operation: bool
    browser_visible: bool
    rotation_mechanism: str
    capabilities: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.purpose, CredentialPurpose):
            raise TypeError("credential purpose must be typed")
        if not self.logical_name or not self.consumer or not self.rotation_mechanism:
            raise ValueError("credential metadata must be complete")
        if self.browser_visible:
            raise ValueError("privileged credential cannot be browser-visible")
        if not self.capabilities or self.capabilities != tuple(sorted(set(self.capabilities))):
            raise ValueError("credential capabilities must be sorted and unique")


class SecretValue:
    """A non-serializable secret whose normal string forms are always redacted."""

    __slots__ = ("__value", "_purpose", "_source_name", "_sealed")

    def __init__(
        self,
        value: str,
        *,
        purpose: CredentialPurpose,
        source_name: str,
    ) -> None:
        if not isinstance(purpose, CredentialPurpose):
            raise TypeError("secret purpose must be typed")
        if (
            not isinstance(value, str)
            or not value
            or value != value.strip()
            or "\x00" in value
            or len(value.encode("utf-8")) > 16 * 1024
        ):
            raise CredentialBoundaryError("CREDENTIAL_VALUE_INVALID")
        if (
            not isinstance(source_name, str)
            or _SAFE_SECRET_SOURCE_NAME.fullmatch(source_name) is None
        ):
            raise CredentialBoundaryError("CREDENTIAL_SOURCE_INVALID")
        object.__setattr__(self, "_SecretValue__value", value)
        object.__setattr__(self, "_purpose", purpose)
        object.__setattr__(self, "_source_name", source_name)
        object.__setattr__(self, "_sealed", True)

    def __setattr__(self, name: str, value: object) -> None:
        if getattr(self, "_sealed", False):
            raise AttributeError("SecretValue is immutable")
        object.__setattr__(self, name, value)

    @property
    def purpose(self) -> CredentialPurpose:
        return self._purpose

    @property
    def source_name(self) -> str:
        return self._source_name

    def reveal_for(self, purpose: CredentialPurpose) -> str:
        if purpose is not self._purpose:
            raise CredentialBoundaryError("CREDENTIAL_PURPOSE_MISMATCH")
        return self.__value

    def __repr__(self) -> str:
        return (
            "SecretValue(purpose="
            f"{self._purpose.value!r}, source={self._source_name!r}, value='<redacted>')"
        )

    def __str__(self) -> str:
        return "<redacted>"

    def __format__(self, format_spec: str) -> str:
        return format(str(self), format_spec)

    def __reduce__(self):
        raise TypeError("SecretValue cannot be serialized")


def _spec(
    purpose: CredentialPurpose,
    logical_name: str,
    environment_variable: str | None,
    consumer: str,
    *capabilities: str,
    required: bool = True,
    rotation: str = "REPLACE_SECRET_AND_RESTART_CONSUMER",
) -> CredentialSpec:
    return CredentialSpec(
        purpose=purpose,
        logical_name=logical_name,
        environment_variable=environment_variable,
        consumer=consumer,
        required_for_operation=required,
        browser_visible=False,
        rotation_mechanism=rotation,
        capabilities=tuple(sorted(capabilities)),
    )


CREDENTIAL_SPECS: Mapping[CredentialPurpose, CredentialSpec] = {
    CredentialPurpose.APPLICATION_DATABASE: _spec(
        CredentialPurpose.APPLICATION_DATABASE,
        "application-database",
        "DATABASE_URL_APP",
        "kernel-and-owner-application-runtime",
        "OWNER_SCOPED_RUNTIME_CRUD",
    ),
    CredentialPurpose.PERSONAL_MEMORY_COMMIT_DATABASE: _spec(
        CredentialPurpose.PERSONAL_MEMORY_COMMIT_DATABASE,
        "personal-memory-commit-database",
        "DATABASE_URL_COMMIT_HELPER",
        "personal-memory-commit-helper-and-activation-service",
        "EXACT_APPROVED_PATCH_ACTIVATION",
        "EXACT_APPROVED_PATCH_COMMIT",
    ),
    CredentialPurpose.MIGRATION_DATABASE: _spec(
        CredentialPurpose.MIGRATION_DATABASE,
        "migration-database",
        "DATABASE_URL_MIGRATOR",
        "operations-only-migration-runner",
        "SCHEMA_MIGRATION",
    ),
    CredentialPurpose.AUDIT_APPENDER_DATABASE: _spec(
        CredentialPurpose.AUDIT_APPENDER_DATABASE,
        "audit-append-operation",
        None,
        "typed-audit-adapter-on-originating-business-transaction",
        "APPEND_TYPED_AUDIT",
        required=False,
        rotation="ROTATE_ORIGINATING_BUSINESS_DATABASE_CREDENTIAL",
    ),
    CredentialPurpose.AUDIT_READER_DATABASE: _spec(
        CredentialPurpose.AUDIT_READER_DATABASE,
        "audit-reader-database",
        "DATABASE_URL_AUDIT_READER",
        "bounded-audit-reader-exporter",
        "READ_BOUNDED_AUDIT",
    ),
    CredentialPurpose.HUMAN_REVIEWER_DATABASE: _spec(
        CredentialPurpose.HUMAN_REVIEWER_DATABASE,
        "human-reviewer-database",
        "DATABASE_URL_REVIEWER",
        "human-reviewer-workspace",
        "HUMAN_REVIEW_CLAIM_AND_DECISION",
    ),
    CredentialPurpose.REVIEW_SERVICE_DATABASE: _spec(
        CredentialPurpose.REVIEW_SERVICE_DATABASE,
        "review-service-database",
        "DATABASE_URL_REVIEW_SERVICE",
        "review-case-intake-and-business-handoff-service",
        "REVIEW_CASE_INTAKE_AND_TYPED_HANDOFF",
    ),
    CredentialPurpose.SOURCE_PUBLICATION_DATABASE: _spec(
        CredentialPurpose.SOURCE_PUBLICATION_DATABASE,
        "source-publication-database",
        "DATABASE_URL_SOURCE_PUBLICATION",
        "source-registry-publication-service",
        "SOURCE_REGISTRY_PUBLICATION",
    ),
    CredentialPurpose.INGESTION_DATABASE: _spec(
        CredentialPurpose.INGESTION_DATABASE,
        "ingestion-database",
        "DATABASE_URL_INGESTION",
        "canonical-ingestion-worker",
        "CANONICAL_INGESTION_STATE",
    ),
    CredentialPurpose.MODEL_PROVIDER: _spec(
        CredentialPurpose.MODEL_PROVIDER,
        "moonshot-provider-api",
        "MOONSHOT_API_KEY",
        "provider-neutral-model-adapter",
        "CALL_APPROVED_MODEL_PROVIDER",
    ),
    CredentialPurpose.S3_RUNTIME_IDENTITY: _spec(
        CredentialPurpose.S3_RUNTIME_IDENTITY,
        "s3-workload-identity",
        None,
        "snapshot-storage-adapter",
        "WRITE_LOCKED_SNAPSHOT",
        required=False,
        rotation="ROTATE_MACHINE_LOCAL_WORKLOAD_IDENTITY_OUTSIDE_REPOSITORY",
    ),
}


def load_required_credential(
    purpose: CredentialPurpose,
    environment: Mapping[str, str],
) -> SecretValue:
    """Load only the exact credential for ``purpose``; never try a fallback."""

    if not isinstance(purpose, CredentialPurpose):
        raise TypeError("credential purpose must be typed")
    spec = CREDENTIAL_SPECS[purpose]
    name = spec.environment_variable
    if name is None:
        if purpose is CredentialPurpose.S3_RUNTIME_IDENTITY:
            raise CredentialBoundaryError("WORKLOAD_IDENTITY_REQUIRED")
        raise CredentialBoundaryError("LOGICAL_OPERATION_USES_ORIGINATING_CREDENTIAL")
    value = environment.get(name)
    if value is None or not value:
        raise CredentialBoundaryError(
            "MISSING_DEDICATED_" + purpose.value + "_CREDENTIAL"
        )
    return SecretValue(value, purpose=purpose, source_name=name)


def credential_inventory_digest() -> str:
    return canonical_sha256(
        {
            purpose.value: {
                "logical_name": spec.logical_name,
                "environment_variable": spec.environment_variable,
                "consumer": spec.consumer,
                "required_for_operation": spec.required_for_operation,
                "browser_visible": spec.browser_visible,
                "rotation_mechanism": spec.rotation_mechanism,
                "capabilities": spec.capabilities,
            }
            for purpose, spec in sorted(CREDENTIAL_SPECS.items(), key=lambda item: item[0].value)
        }
    )


def build_minimal_subprocess_environment(
    environment: Mapping[str, str],
    *,
    allowed_names: tuple[str, ...] = (),
) -> dict[str, str]:
    """Return a consumer-specific child environment without ambient secrets.

    Callers must opt in to every non-operational variable by exact name.  The
    helper intentionally does not infer related credentials or fall back to a
    broader environment variable.
    """

    if not isinstance(environment, Mapping):
        raise TypeError("environment must be a mapping")
    if (
        not isinstance(allowed_names, tuple)
        or any(not isinstance(name, str) or not name for name in allowed_names)
        or len(set(allowed_names)) != len(allowed_names)
    ):
        raise TypeError("allowed_names must be a unique tuple of names")
    permitted = _SAFE_SUBPROCESS_ENVIRONMENT_NAMES.union(allowed_names)
    return {
        name: value
        for name, value in environment.items()
        if name in permitted and isinstance(value, str)
    }


__all__ = [
    "AWS_WORKLOAD_IDENTITY_ENVIRONMENT_NAMES",
    "CREDENTIAL_SPECS",
    "CredentialBoundaryError",
    "CredentialPurpose",
    "CredentialSpec",
    "SecretValue",
    "build_minimal_subprocess_environment",
    "credential_inventory_digest",
    "load_required_credential",
]
