"""Typed, secret-free configuration boundary for the post-roadmap demo runtime."""

from __future__ import annotations

import hashlib
import ipaddress
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from aioa_memory_kernel.modeling.models import (
    APPROVED_CREDENTIAL_ENVIRONMENT_VARIABLE,
    MAXIMUM_MAX_OUTPUT_TOKENS,
    MAXIMUM_PROVIDER_USER_CONTENT_UTF8_BYTES,
    load_approved_provider_spec,
)
from aioa_memory_kernel.personal_memory_ui.auth import OidcSettings
from aioa_memory_kernel.personal_memory_ui.cockroach_sessions import (
    DurableSessionLimits,
)
from aioa_memory_kernel.runtime import (
    DEFAULT_PROFILE_PATH,
    Runtime4GBProfile,
    load_runtime_4gb_profile,
)
from aioa_memory_kernel.security.credentials import (
    CredentialBoundaryError,
    CredentialPurpose,
    SecretValue,
    load_required_credential,
)


RUNTIME_MODE_ENV = "AIOA_RUNTIME_MODE"
RUNTIME_BIND_HOST_ENV = "AIOA_RUNTIME_BIND_HOST"
RUNTIME_PORT_ENV = "AIOA_RUNTIME_PORT"
COCKPIT_LEGACY_MODE_ENABLED_ENV = "AIOA_DEMO_LEGACY_MODE_ENABLED"
OIDC_ISSUER_ENV = "AIOA_OIDC_ISSUER"
OIDC_CLIENT_ID_ENV = "AIOA_OIDC_CLIENT_ID"
PUBLIC_ORIGIN_ENV = "AIOA_RUNTIME_PUBLIC_ORIGIN"
JUDGE_ALLOWED_SUBJECTS_ENV = "AIOA_JUDGE_ALLOWED_OIDC_SUBJECTS"
SESSION_TTL_ENV = "AIOA_SESSION_TTL_SECONDS"
SESSION_PENDING_TTL_ENV = "AIOA_OIDC_PENDING_TTL_SECONDS"
SESSION_MAXIMUM_TOTAL_ENV = "AIOA_SESSION_MAXIMUM_TOTAL"
SESSION_MAXIMUM_PENDING_ENV = "AIOA_OIDC_MAXIMUM_PENDING_FLOWS"
SESSION_MAXIMUM_PER_OWNER_ENV = "AIOA_SESSION_MAXIMUM_PER_OWNER"
SESSION_MAXIMUM_PAYLOAD_ENV = "AIOA_SESSION_MAXIMUM_PAYLOAD_BYTES"
DATABASE_ALLOW_INSECURE_LOCAL_ENV = "AIOA_DB_ALLOW_INSECURE_LOCAL"
DATABASE_POOL_MIN_ENV = "AIOA_DB_POOL_MIN"
DATABASE_POOL_MAX_ENV = "AIOA_DB_POOL_MAX"
DATABASE_ACQUISITION_TIMEOUT_ENV = "AIOA_DB_ACQUISITION_TIMEOUT_SECONDS"
DATABASE_CONNECTION_TIMEOUT_ENV = "AIOA_DB_CONNECTION_TIMEOUT_SECONDS"
DATABASE_STATEMENT_TIMEOUT_ENV = "AIOA_DB_STATEMENT_TIMEOUT_SECONDS"
DATABASE_MIGRATION_TIMEOUT_ENV = "AIOA_DB_MIGRATION_TIMEOUT_SECONDS"
PROVIDER_BUDGET_EPOCH_ENV = "AIOA_DEMO_PROVIDER_BUDGET_EPOCH"
PROVIDER_TENANT_ID_ENV = "AIOA_DEMO_PROVIDER_TENANT_ID"
PROVIDER_MAX_REQUESTS_TOTAL_ENV = "AIOA_DEMO_MAX_REQUESTS_TOTAL"
PROVIDER_MAX_REQUESTS_PER_OWNER_ENV = "AIOA_DEMO_MAX_REQUESTS_PER_OWNER"
PROVIDER_MAX_REQUESTS_PER_SESSION_ENV = "AIOA_DEMO_MAX_REQUESTS_PER_SESSION"
PROVIDER_REQUEST_WINDOW_SECONDS_ENV = "AIOA_DEMO_REQUEST_WINDOW_SECONDS"
PROVIDER_MAX_REQUESTS_WINDOW_GLOBAL_ENV = (
    "AIOA_DEMO_MAX_REQUESTS_PER_WINDOW_GLOBAL"
)
PROVIDER_MAX_REQUESTS_WINDOW_OWNER_ENV = "AIOA_DEMO_MAX_REQUESTS_PER_WINDOW_OWNER"
PROVIDER_MAX_REQUESTS_WINDOW_SESSION_ENV = (
    "AIOA_DEMO_MAX_REQUESTS_PER_WINDOW_SESSION"
)
PROVIDER_MAX_CALLS_TOTAL_ENV = "AIOA_DEMO_PROVIDER_MAX_CALLS_TOTAL"
PROVIDER_MAX_CALLS_PER_OWNER_ENV = "AIOA_DEMO_PROVIDER_MAX_CALLS_PER_OWNER"
PROVIDER_MAX_CALLS_PER_SESSION_ENV = "AIOA_DEMO_PROVIDER_MAX_CALLS_PER_SESSION"
PROVIDER_MAX_CALLS_PER_REQUEST_ENV = "AIOA_DEMO_PROVIDER_MAX_CALLS_PER_REQUEST"
PROVIDER_MAX_CONCURRENT_CALLS_ENV = "AIOA_DEMO_PROVIDER_MAX_CONCURRENT_CALLS"
PROVIDER_MAX_QUEUED_CALLS_ENV = "AIOA_DEMO_PROVIDER_MAX_QUEUED_CALLS"
PROVIDER_QUEUE_WAIT_SECONDS_ENV = "AIOA_DEMO_PROVIDER_QUEUE_WAIT_SECONDS"
PROVIDER_MAX_INPUT_BYTES_ENV = "AIOA_DEMO_PROVIDER_MAX_INPUT_BYTES"
PROVIDER_MAX_OUTPUT_TOKENS_ENV = "AIOA_DEMO_PROVIDER_MAX_OUTPUT_TOKENS"
PROVIDER_TIMEOUT_SECONDS_ENV = "AIOA_DEMO_PROVIDER_TIMEOUT_SECONDS"

APPLICATION_DATABASE_ROLE = "mp_app_runtime"
REQUEST_CONTEXT_ROLE = "mp_request_context_setter"
STEP40_APPLICATION_POOL_MIN = 1
STEP40_APPLICATION_POOL_MAX = 4

_SAFE_HOSTNAME = re.compile(
    r"^(?=.{1,253}\Z)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)(?:\."
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$"
)


class RuntimeMode(str, Enum):
    TEST = "TEST"
    LOCAL_DEMO = "LOCAL_DEMO"
    HOSTED_DEMO = "HOSTED_DEMO"


class RuntimeErrorCode(str, Enum):
    CONFIG_INVALID = "CONFIG_INVALID"
    AUTH_CONFIG_INVALID = "AUTH_CONFIG_INVALID"
    SESSION_CONFIG_INVALID = "SESSION_CONFIG_INVALID"
    DATABASE_CONFIG_INVALID = "DATABASE_CONFIG_INVALID"
    DATABASE_CREDENTIAL_MISSING = "DATABASE_CREDENTIAL_MISSING"
    DATABASE_TLS_REQUIRED = "DATABASE_TLS_REQUIRED"
    DATABASE_MIGRATION_FAILED = "DATABASE_MIGRATION_FAILED"
    DATABASE_AUTHORITY_INVALID = "DATABASE_AUTHORITY_INVALID"
    DATABASE_POOL_FAILED = "DATABASE_POOL_FAILED"
    PROVIDER_CONFIG_INVALID = "PROVIDER_CONFIG_INVALID"
    PROVIDER_CREDENTIAL_MISSING = "PROVIDER_CREDENTIAL_MISSING"
    PROVIDER_GUARD_CONFIG_INVALID = "PROVIDER_GUARD_CONFIG_INVALID"
    DEPENDENCY_MISSING = "DEPENDENCY_MISSING"
    UNSAFE_TEST_ADAPTER_IN_HOSTED_MODE = "UNSAFE_TEST_ADAPTER_IN_HOSTED_MODE"
    STARTUP_FAILED = "STARTUP_FAILED"
    SHUTDOWN_FAILED = "SHUTDOWN_FAILED"


class RuntimeAssemblyError(RuntimeError):
    """A bounded runtime failure whose public diagnostic contains no input value."""

    def __init__(self, code: RuntimeErrorCode) -> None:
        if not isinstance(code, RuntimeErrorCode):
            raise TypeError("runtime error code must be typed")
        super().__init__(f"Memory Patch runtime failed safely: {code.value}")
        self.code = code
        self.sanitized_code = code.value


def _bounded_environment_value(
    environment: Mapping[str, str],
    name: str,
    *,
    required: bool,
    default: str,
    maximum_bytes: int = 1024,
) -> str:
    value = environment.get(name, default)
    if (
        not isinstance(value, str)
        or (required and not value)
        or value != value.strip()
        or not value
        or len(value.encode("utf-8")) > maximum_bytes
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise RuntimeAssemblyError(RuntimeErrorCode.CONFIG_INVALID)
    return value


def _bind_host(value: str, mode: RuntimeMode) -> str:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        if _SAFE_HOSTNAME.fullmatch(value) is None:
            raise RuntimeAssemblyError(RuntimeErrorCode.CONFIG_INVALID) from None
        is_loopback = value.casefold() == "localhost"
    else:
        is_loopback = address.is_loopback
    if mode is RuntimeMode.LOCAL_DEMO and not is_loopback:
        raise RuntimeAssemblyError(RuntimeErrorCode.CONFIG_INVALID)
    return value


def _port(value: str) -> int:
    if re.fullmatch(r"[0-9]{1,5}", value) is None:
        raise RuntimeAssemblyError(RuntimeErrorCode.CONFIG_INVALID)
    result = int(value)
    if not 1 <= result <= 65535:
        raise RuntimeAssemblyError(RuntimeErrorCode.CONFIG_INVALID)
    return result


def _bounded_integer(
    environment: Mapping[str, str],
    name: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    raw = _bounded_environment_value(
        environment,
        name,
        required=False,
        default=str(default),
        maximum_bytes=8,
    )
    if re.fullmatch(r"[0-9]{1,8}", raw) is None:
        raise RuntimeAssemblyError(RuntimeErrorCode.CONFIG_INVALID)
    value = int(raw)
    if not minimum <= value <= maximum:
        raise RuntimeAssemblyError(RuntimeErrorCode.CONFIG_INVALID)
    return value


def _loopback_database_host(host: str) -> bool:
    if host.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@dataclass(frozen=True, slots=True, repr=False)
class DatabaseEndpoint:
    """Parsed non-secret identity plus one purpose-sealed connection value."""

    credential: SecretValue = field(compare=False, repr=False)
    host: str = field(repr=False)
    port: int
    database: str = field(repr=False)
    sslmode: str
    principal_digest: bytes = field(repr=False)
    credential_digest: bytes = field(repr=False)

    def __repr__(self) -> str:
        return (
            "DatabaseEndpoint(credential='<redacted>', endpoint='<redacted>', "
            f"port={self.port}, sslmode={self.sslmode!r})"
        )


def _database_endpoint(
    credential: SecretValue,
    *,
    mode: RuntimeMode,
    allow_insecure_local: bool,
) -> DatabaseEndpoint:
    try:
        raw = credential.reveal_for(credential.purpose)
        parsed = urlsplit(raw)
        port = parsed.port or 26257
        username = parsed.username
        hostname = parsed.hostname
        database = unquote(parsed.path[1:]) if parsed.path.startswith("/") else ""
        query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
    except (CredentialBoundaryError, TypeError, ValueError):
        raise RuntimeAssemblyError(RuntimeErrorCode.DATABASE_CONFIG_INVALID) from None
    if (
        parsed.scheme not in {"postgres", "postgresql"}
        or parsed.fragment
        or not isinstance(username, str)
        or not username
        or not isinstance(hostname, str)
        or not hostname
        or not 1 <= port <= 65535
        or not database
        or "/" in database
        or len(database.encode("utf-8")) > 128
        or any(ord(character) < 32 or ord(character) == 127 for character in database)
    ):
        raise RuntimeAssemblyError(RuntimeErrorCode.DATABASE_CONFIG_INVALID)
    sslmodes = query.get("sslmode", ())
    if len(sslmodes) != 1:
        raise RuntimeAssemblyError(RuntimeErrorCode.DATABASE_TLS_REQUIRED)
    sslmode = sslmodes[0].casefold()
    if mode is RuntimeMode.HOSTED_DEMO:
        tls_valid = sslmode == "verify-full"
    elif sslmode == "verify-full":
        tls_valid = True
    else:
        tls_valid = (
            allow_insecure_local
            and mode in {RuntimeMode.TEST, RuntimeMode.LOCAL_DEMO}
            and sslmode == "disable"
            and _loopback_database_host(hostname)
        )
    if not tls_valid:
        raise RuntimeAssemblyError(RuntimeErrorCode.DATABASE_TLS_REQUIRED)
    return DatabaseEndpoint(
        credential=credential,
        host=hostname.casefold(),
        port=port,
        database=database,
        sslmode=sslmode,
        principal_digest=hashlib.sha256(username.encode("utf-8")).digest(),
        credential_digest=hashlib.sha256(raw.encode("utf-8")).digest(),
    )


@dataclass(frozen=True, slots=True, repr=False)
class RuntimeDatabaseSettings:
    """Exact R3 database boundary with no serializable connection value."""

    application: DatabaseEndpoint
    migration: DatabaseEndpoint
    pool_minimum: int
    pool_maximum: int
    acquisition_timeout_seconds: int
    connection_timeout_seconds: int
    statement_timeout_seconds: int
    migration_timeout_seconds: int
    local_insecure_disposable: bool

    def __post_init__(self) -> None:
        if (
            not isinstance(self.application, DatabaseEndpoint)
            or self.application.credential.purpose
            is not CredentialPurpose.APPLICATION_DATABASE
            or not isinstance(self.migration, DatabaseEndpoint)
            or self.migration.credential.purpose
            is not CredentialPurpose.MIGRATION_DATABASE
            or self.application.host != self.migration.host
            or self.application.port != self.migration.port
            or self.application.database != self.migration.database
            or self.application.principal_digest == self.migration.principal_digest
            or self.application.credential_digest == self.migration.credential_digest
            or self.pool_minimum != STEP40_APPLICATION_POOL_MIN
            or self.pool_maximum != STEP40_APPLICATION_POOL_MAX
        ):
            raise RuntimeAssemblyError(RuntimeErrorCode.DATABASE_CONFIG_INVALID)

    @classmethod
    def from_mapping(
        cls,
        environment: Mapping[str, str],
        *,
        mode: RuntimeMode,
        profile: Runtime4GBProfile,
    ) -> "RuntimeDatabaseSettings":
        if profile.database.application_pool_max != STEP40_APPLICATION_POOL_MAX:
            raise RuntimeAssemblyError(RuntimeErrorCode.DATABASE_CONFIG_INVALID)
        local_flag = environment.get(DATABASE_ALLOW_INSECURE_LOCAL_ENV, "0")
        if local_flag not in {"0", "1"}:
            raise RuntimeAssemblyError(RuntimeErrorCode.DATABASE_CONFIG_INVALID)
        allow_insecure_local = local_flag == "1"
        if mode is RuntimeMode.HOSTED_DEMO and allow_insecure_local:
            raise RuntimeAssemblyError(RuntimeErrorCode.DATABASE_TLS_REQUIRED)
        try:
            application_credential = load_required_credential(
                CredentialPurpose.APPLICATION_DATABASE,
                environment,
            )
            migration_credential = load_required_credential(
                CredentialPurpose.MIGRATION_DATABASE,
                environment,
            )
        except CredentialBoundaryError:
            raise RuntimeAssemblyError(RuntimeErrorCode.DATABASE_CREDENTIAL_MISSING) from None
        application = _database_endpoint(
            application_credential,
            mode=mode,
            allow_insecure_local=allow_insecure_local,
        )
        migration = _database_endpoint(
            migration_credential,
            mode=mode,
            allow_insecure_local=allow_insecure_local,
        )
        return cls(
            application=application,
            migration=migration,
            pool_minimum=_bounded_integer(
                environment,
                DATABASE_POOL_MIN_ENV,
                default=STEP40_APPLICATION_POOL_MIN,
                minimum=STEP40_APPLICATION_POOL_MIN,
                maximum=STEP40_APPLICATION_POOL_MIN,
            ),
            pool_maximum=_bounded_integer(
                environment,
                DATABASE_POOL_MAX_ENV,
                default=STEP40_APPLICATION_POOL_MAX,
                minimum=STEP40_APPLICATION_POOL_MAX,
                maximum=STEP40_APPLICATION_POOL_MAX,
            ),
            acquisition_timeout_seconds=_bounded_integer(
                environment,
                DATABASE_ACQUISITION_TIMEOUT_ENV,
                default=5,
                minimum=1,
                maximum=15,
            ),
            connection_timeout_seconds=_bounded_integer(
                environment,
                DATABASE_CONNECTION_TIMEOUT_ENV,
                default=5,
                minimum=1,
                maximum=15,
            ),
            statement_timeout_seconds=_bounded_integer(
                environment,
                DATABASE_STATEMENT_TIMEOUT_ENV,
                default=10,
                minimum=1,
                maximum=60,
            ),
            migration_timeout_seconds=_bounded_integer(
                environment,
                DATABASE_MIGRATION_TIMEOUT_ENV,
                # Fresh late-chain DDL measured 110.247 seconds on the
                # canonical constrained host. Keep the established 300-second
                # hard ceiling as the default instead of making a clean
                # deployment require an undocumented override.
                default=300,
                minimum=1,
                maximum=300,
            ),
            local_insecure_disposable=allow_insecure_local,
        )

    @property
    def database_identity(self) -> tuple[str, int, str]:
        return (self.application.host, self.application.port, self.application.database)

    def __repr__(self) -> str:
        return (
            "RuntimeDatabaseSettings(endpoints='<redacted>', "
            f"pool_minimum={self.pool_minimum}, pool_maximum={self.pool_maximum}, "
            f"local_insecure_disposable={self.local_insecure_disposable})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class RuntimeJudgeAccessSettings:
    allowed_oidc_subjects: tuple[str, ...]

    def __post_init__(self) -> None:
        if (
            not 1 <= len(self.allowed_oidc_subjects) <= 32
            or tuple(sorted(set(self.allowed_oidc_subjects)))
            != self.allowed_oidc_subjects
        ):
            raise RuntimeAssemblyError(RuntimeErrorCode.AUTH_CONFIG_INVALID)

    def __repr__(self) -> str:
        return (
            "RuntimeJudgeAccessSettings("
            f"allowed_subject_count={len(self.allowed_oidc_subjects)})"
        )


@dataclass(frozen=True, slots=True)
class RuntimeSessionSettings:
    limits: DurableSessionLimits

    def __post_init__(self) -> None:
        if not isinstance(self.limits, DurableSessionLimits):
            raise RuntimeAssemblyError(RuntimeErrorCode.SESSION_CONFIG_INVALID)


@dataclass(frozen=True, slots=True, repr=False)
class RuntimeProviderSettings:
    """Pinned provider identity plus one purpose-sealed server credential."""

    credential: SecretValue = field(compare=False, repr=False)
    provider_id: str
    model_id: str
    model_declared_version: str
    adapter_id: str
    endpoint_class: str
    api_origin: str
    api_path: str
    configuration_digest: str

    def __post_init__(self) -> None:
        spec = load_approved_provider_spec()
        if (
            not isinstance(self.credential, SecretValue)
            or self.credential.purpose is not CredentialPurpose.MODEL_PROVIDER
            or self.credential.source_name
            != APPROVED_CREDENTIAL_ENVIRONMENT_VARIABLE
            or self.provider_id != spec.provider_id
            or self.model_id != spec.model_id
            or self.model_declared_version != spec.model_declared_version
            or self.adapter_id != spec.adapter_version
            or self.endpoint_class != spec.endpoint_class
            or self.api_origin != spec.api_origin
            or self.api_path != spec.chat_completions_path
            or self.configuration_digest != spec.config_digest
            or not all(
                (
                    spec.tooling_disabled,
                    spec.function_calling_disabled,
                    spec.web_browsing_disabled,
                    spec.code_execution_disabled,
                )
            )
        ):
            raise RuntimeAssemblyError(RuntimeErrorCode.PROVIDER_CONFIG_INVALID)

    def __repr__(self) -> str:
        return (
            "RuntimeProviderSettings(credential='<redacted>', "
            f"provider_id={self.provider_id!r}, model_id={self.model_id!r}, "
            f"adapter_id={self.adapter_id!r})"
        )


@dataclass(frozen=True, slots=True)
class RuntimeProviderGuardSettings:
    """Small, deterministic R5 quota envelope for one operator budget epoch."""

    budget_epoch: str | None
    tenant_id: str | None
    maximum_requests_total: int
    maximum_requests_per_owner: int
    maximum_requests_per_session: int
    request_window_seconds: int
    maximum_requests_per_window_global: int
    maximum_requests_per_window_owner: int
    maximum_requests_per_window_session: int
    maximum_calls_total: int
    maximum_calls_per_owner: int
    maximum_calls_per_session: int
    maximum_calls_per_request: int
    maximum_concurrent_calls: int
    maximum_queued_calls: int
    queue_wait_seconds: int
    maximum_input_bytes: int
    maximum_output_tokens: int
    timeout_seconds: int

    def __post_init__(self) -> None:
        if self.budget_epoch is not None and (
            not isinstance(self.budget_epoch, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", self.budget_epoch)
            is None
        ):
            raise RuntimeAssemblyError(RuntimeErrorCode.PROVIDER_GUARD_CONFIG_INVALID)
        if self.tenant_id is not None and (
            not isinstance(self.tenant_id, str)
            or not self.tenant_id
            or self.tenant_id != self.tenant_id.strip()
            or len(self.tenant_id.encode("utf-8")) > 255
            or any(
                ord(character) < 32 or ord(character) == 127
                for character in self.tenant_id
            )
        ):
            raise RuntimeAssemblyError(RuntimeErrorCode.PROVIDER_GUARD_CONFIG_INVALID)
        if not (
            1 <= self.maximum_requests_per_session
            <= self.maximum_requests_per_owner
            <= self.maximum_requests_total
            <= 256
            and 1 <= self.maximum_requests_per_window_session
            <= self.maximum_requests_per_window_owner
            <= self.maximum_requests_per_window_global
            <= self.maximum_requests_total
            and 10 <= self.request_window_seconds <= 3600
            and 1 <= self.maximum_calls_per_request
            <= self.maximum_calls_per_session
            <= self.maximum_calls_per_owner
            <= self.maximum_calls_total
            <= 256
            and self.maximum_concurrent_calls == 1
            and 1 <= self.maximum_queued_calls <= 2
            and 1 <= self.queue_wait_seconds <= 5
            and 1 <= self.maximum_input_bytes
            <= MAXIMUM_PROVIDER_USER_CONTENT_UTF8_BYTES
            and 1 <= self.maximum_output_tokens <= MAXIMUM_MAX_OUTPUT_TOKENS
            and 1 <= self.timeout_seconds <= 45
        ):
            raise RuntimeAssemblyError(RuntimeErrorCode.PROVIDER_GUARD_CONFIG_INVALID)


def _provider_settings(
    environment: Mapping[str, str],
) -> RuntimeProviderSettings | None:
    if APPROVED_CREDENTIAL_ENVIRONMENT_VARIABLE not in environment:
        return None
    try:
        credential = load_required_credential(
            CredentialPurpose.MODEL_PROVIDER,
            environment,
        )
        spec = load_approved_provider_spec()
        return RuntimeProviderSettings(
            credential=credential,
            provider_id=spec.provider_id,
            model_id=spec.model_id,
            model_declared_version=spec.model_declared_version,
            adapter_id=spec.adapter_version,
            endpoint_class=spec.endpoint_class,
            api_origin=spec.api_origin,
            api_path=spec.chat_completions_path,
            configuration_digest=spec.config_digest,
        )
    except CredentialBoundaryError:
        raise RuntimeAssemblyError(RuntimeErrorCode.PROVIDER_CREDENTIAL_MISSING) from None
    except RuntimeAssemblyError:
        raise
    except Exception:
        raise RuntimeAssemblyError(RuntimeErrorCode.PROVIDER_CONFIG_INVALID) from None


def _provider_guard_settings(
    environment: Mapping[str, str],
    *,
    mode: RuntimeMode,
    profile: Runtime4GBProfile,
) -> RuntimeProviderGuardSettings:
    raw_epoch = environment.get(PROVIDER_BUDGET_EPOCH_ENV)
    budget_epoch = (
        "test-budget-epoch-1a"
        if raw_epoch is None and mode is RuntimeMode.TEST
        else raw_epoch
    )
    if budget_epoch is not None and (
        not isinstance(budget_epoch, str)
        or not budget_epoch
        or budget_epoch != budget_epoch.strip()
    ):
        raise RuntimeAssemblyError(RuntimeErrorCode.PROVIDER_GUARD_CONFIG_INVALID)
    tenant_id = environment.get(PROVIDER_TENANT_ID_ENV)
    if tenant_id is None and mode is RuntimeMode.TEST:
        tenant_id = "tenant-r5"
    try:
        settings = RuntimeProviderGuardSettings(
            budget_epoch=budget_epoch,
            tenant_id=tenant_id,
            maximum_requests_total=_bounded_integer(
                environment, PROVIDER_MAX_REQUESTS_TOTAL_ENV,
                default=24, minimum=1, maximum=256,
            ),
            maximum_requests_per_owner=_bounded_integer(
                environment, PROVIDER_MAX_REQUESTS_PER_OWNER_ENV,
                default=8, minimum=1, maximum=64,
            ),
            maximum_requests_per_session=_bounded_integer(
                environment, PROVIDER_MAX_REQUESTS_PER_SESSION_ENV,
                default=6, minimum=1, maximum=32,
            ),
            request_window_seconds=_bounded_integer(
                environment, PROVIDER_REQUEST_WINDOW_SECONDS_ENV,
                default=60, minimum=10, maximum=3600,
            ),
            maximum_requests_per_window_global=_bounded_integer(
                environment, PROVIDER_MAX_REQUESTS_WINDOW_GLOBAL_ENV,
                default=12, minimum=1, maximum=64,
            ),
            maximum_requests_per_window_owner=_bounded_integer(
                environment, PROVIDER_MAX_REQUESTS_WINDOW_OWNER_ENV,
                default=4, minimum=1, maximum=32,
            ),
            maximum_requests_per_window_session=_bounded_integer(
                environment, PROVIDER_MAX_REQUESTS_WINDOW_SESSION_ENV,
                default=3, minimum=1, maximum=16,
            ),
            maximum_calls_total=_bounded_integer(
                environment, PROVIDER_MAX_CALLS_TOTAL_ENV,
                default=32, minimum=1, maximum=256,
            ),
            maximum_calls_per_owner=_bounded_integer(
                environment, PROVIDER_MAX_CALLS_PER_OWNER_ENV,
                default=12, minimum=1, maximum=64,
            ),
            maximum_calls_per_session=_bounded_integer(
                environment, PROVIDER_MAX_CALLS_PER_SESSION_ENV,
                default=10, minimum=1, maximum=32,
            ),
            maximum_calls_per_request=_bounded_integer(
                environment, PROVIDER_MAX_CALLS_PER_REQUEST_ENV,
                default=8, minimum=1, maximum=16,
            ),
            maximum_concurrent_calls=_bounded_integer(
                environment, PROVIDER_MAX_CONCURRENT_CALLS_ENV,
                default=1, minimum=1, maximum=1,
            ),
            maximum_queued_calls=_bounded_integer(
                environment, PROVIDER_MAX_QUEUED_CALLS_ENV,
                default=profile.queues.provider, minimum=1,
                maximum=profile.queues.provider,
            ),
            queue_wait_seconds=_bounded_integer(
                environment, PROVIDER_QUEUE_WAIT_SECONDS_ENV,
                default=2, minimum=1, maximum=5,
            ),
            maximum_input_bytes=_bounded_integer(
                environment, PROVIDER_MAX_INPUT_BYTES_ENV,
                default=MAXIMUM_PROVIDER_USER_CONTENT_UTF8_BYTES,
                minimum=1, maximum=MAXIMUM_PROVIDER_USER_CONTENT_UTF8_BYTES,
            ),
            maximum_output_tokens=_bounded_integer(
                environment, PROVIDER_MAX_OUTPUT_TOKENS_ENV,
                default=MAXIMUM_MAX_OUTPUT_TOKENS,
                minimum=1,
                maximum=MAXIMUM_MAX_OUTPUT_TOKENS,
            ),
            timeout_seconds=_bounded_integer(
                environment, PROVIDER_TIMEOUT_SECONDS_ENV,
                default=45, minimum=1, maximum=45,
            ),
        )
    except RuntimeAssemblyError:
        raise
    except (TypeError, ValueError):
        raise RuntimeAssemblyError(RuntimeErrorCode.PROVIDER_GUARD_CONFIG_INVALID) from None
    return settings


def _judge_access_settings(
    environment: Mapping[str, str], mode: RuntimeMode
) -> RuntimeJudgeAccessSettings:
    raw = _bounded_environment_value(
        environment,
        JUDGE_ALLOWED_SUBJECTS_ENV,
        required=mode is not RuntimeMode.TEST,
        default="memory-patch-test-subject" if mode is RuntimeMode.TEST else "",
        maximum_bytes=8192,
    )
    subjects = raw.split(",")
    if any(
        not subject
        or subject != subject.strip()
        or len(subject.encode("utf-8")) > 255
        for subject in subjects
    ):
        raise RuntimeAssemblyError(RuntimeErrorCode.AUTH_CONFIG_INVALID)
    try:
        return RuntimeJudgeAccessSettings(tuple(sorted(set(subjects))))
    except RuntimeAssemblyError:
        raise
    except Exception:
        raise RuntimeAssemblyError(RuntimeErrorCode.AUTH_CONFIG_INVALID) from None


def _session_settings(environment: Mapping[str, str]) -> RuntimeSessionSettings:
    try:
        limits = DurableSessionLimits(
            absolute_ttl_seconds=_bounded_integer(
                environment,
                SESSION_TTL_ENV,
                default=8 * 60 * 60,
                minimum=900,
                maximum=8 * 60 * 60,
            ),
            pending_ttl_seconds=_bounded_integer(
                environment,
                SESSION_PENDING_TTL_ENV,
                default=10 * 60,
                minimum=60,
                maximum=10 * 60,
            ),
            maximum_total_records=_bounded_integer(
                environment,
                SESSION_MAXIMUM_TOTAL_ENV,
                default=64,
                minimum=1,
                maximum=256,
            ),
            maximum_pending_flows=_bounded_integer(
                environment,
                SESSION_MAXIMUM_PENDING_ENV,
                default=16,
                minimum=1,
                maximum=128,
            ),
            maximum_sessions_per_owner=_bounded_integer(
                environment,
                SESSION_MAXIMUM_PER_OWNER_ENV,
                default=4,
                minimum=1,
                maximum=8,
            ),
            maximum_payload_bytes=_bounded_integer(
                environment,
                SESSION_MAXIMUM_PAYLOAD_ENV,
                default=2048,
                minimum=512,
                maximum=4096,
            ),
        )
    except RuntimeAssemblyError:
        raise
    except (TypeError, ValueError):
        raise RuntimeAssemblyError(RuntimeErrorCode.SESSION_CONFIG_INVALID) from None
    return RuntimeSessionSettings(limits=limits)


@dataclass(frozen=True, slots=True)
class RuntimeSettings:
    mode: RuntimeMode
    bind_host: str
    port: int
    oidc: OidcSettings
    profile: Runtime4GBProfile
    judge_access: RuntimeJudgeAccessSettings
    sessions: RuntimeSessionSettings
    database: RuntimeDatabaseSettings | None = None
    provider: RuntimeProviderSettings | None = None
    provider_guard: RuntimeProviderGuardSettings | None = None
    legacy_cockpit_enabled: bool = False

    @classmethod
    def from_mapping(
        cls,
        environment: Mapping[str, str],
        *,
        profile_path: Path = DEFAULT_PROFILE_PATH,
    ) -> "RuntimeSettings":
        if not isinstance(environment, Mapping):
            raise RuntimeAssemblyError(RuntimeErrorCode.CONFIG_INVALID)
        try:
            mode = RuntimeMode(environment.get(RUNTIME_MODE_ENV))
        except (TypeError, ValueError):
            raise RuntimeAssemblyError(RuntimeErrorCode.CONFIG_INVALID) from None

        bind_host = _bind_host(
            _bounded_environment_value(
                environment,
                RUNTIME_BIND_HOST_ENV,
                required=False,
                default="127.0.0.1",
                maximum_bytes=253,
            ),
            mode,
        )
        port = _port(
            _bounded_environment_value(
                environment,
                RUNTIME_PORT_ENV,
                required=False,
                default="8000",
                maximum_bytes=5,
            )
        )

        test_mode = mode is RuntimeMode.TEST
        issuer = _bounded_environment_value(
            environment,
            OIDC_ISSUER_ENV,
            required=not test_mode,
            default="https://identity.test" if test_mode else "",
        )
        client_id = _bounded_environment_value(
            environment,
            OIDC_CLIENT_ID_ENV,
            required=not test_mode,
            default="memory-patch-test-client" if test_mode else "",
            maximum_bytes=255,
        )
        public_origin = _bounded_environment_value(
            environment,
            PUBLIC_ORIGIN_ENV,
            required=not test_mode,
            default="https://testserver" if test_mode else "",
        ).rstrip("/")
        try:
            oidc = OidcSettings(
                issuer=issuer,
                client_id=client_id,
                redirect_uri=public_origin + "/memory/oidc/callback",
                public_origin=public_origin,
            )
            profile = load_runtime_4gb_profile(profile_path)
        except Exception:
            raise RuntimeAssemblyError(RuntimeErrorCode.CONFIG_INVALID) from None
        database: RuntimeDatabaseSettings | None
        if mode is RuntimeMode.TEST and not any(
            name in environment for name in ("DATABASE_URL_APP", "DATABASE_URL_MIGRATOR")
        ):
            database = None
        else:
            database = RuntimeDatabaseSettings.from_mapping(
                environment,
                mode=mode,
                profile=profile,
            )
        legacy_mode_flag = environment.get(COCKPIT_LEGACY_MODE_ENABLED_ENV, "0")
        if legacy_mode_flag not in {"0", "1"}:
            raise RuntimeAssemblyError(RuntimeErrorCode.CONFIG_INVALID)
        return cls(
            mode=mode,
            bind_host=bind_host,
            port=port,
            oidc=oidc,
            profile=profile,
            judge_access=_judge_access_settings(environment, mode),
            sessions=_session_settings(environment),
            database=database,
            provider=_provider_settings(environment),
            provider_guard=_provider_guard_settings(
                environment,
                mode=mode,
                profile=profile,
            ),
            legacy_cockpit_enabled=legacy_mode_flag == "1",
        )

    def require_database(self) -> RuntimeDatabaseSettings:
        database = self.database
        if database is None:
            raise RuntimeAssemblyError(RuntimeErrorCode.DATABASE_CREDENTIAL_MISSING)
        return database

    def require_provider(self) -> RuntimeProviderSettings:
        provider = self.provider
        if provider is None:
            raise RuntimeAssemblyError(RuntimeErrorCode.PROVIDER_CREDENTIAL_MISSING)
        return provider

    def require_provider_guard(self) -> RuntimeProviderGuardSettings:
        guard = self.provider_guard
        if guard is None or guard.budget_epoch is None or guard.tenant_id is None:
            raise RuntimeAssemblyError(RuntimeErrorCode.PROVIDER_GUARD_CONFIG_INVALID)
        return guard

    @classmethod
    def import_placeholder(
        cls,
        *,
        profile_path: Path = DEFAULT_PROFILE_PATH,
    ) -> "RuntimeSettings":
        """Return an inert route-building value; strict startup still must pass."""

        try:
            profile = load_runtime_4gb_profile(profile_path)
        except Exception:
            raise RuntimeAssemblyError(RuntimeErrorCode.CONFIG_INVALID) from None
        return cls(
            mode=RuntimeMode.HOSTED_DEMO,
            bind_host="127.0.0.1",
            port=8000,
            oidc=OidcSettings(
                issuer="https://runtime.invalid",
                client_id="runtime-configuration-missing",
                redirect_uri="https://127.0.0.1/memory/oidc/callback",
                public_origin="https://127.0.0.1",
            ),
            profile=profile,
            judge_access=RuntimeJudgeAccessSettings(
                ("runtime-configuration-missing",)
            ),
            sessions=RuntimeSessionSettings(limits=DurableSessionLimits()),
            database=None,
            provider=None,
            provider_guard=None,
            legacy_cockpit_enabled=False,
        )


__all__ = [
    "OIDC_CLIENT_ID_ENV",
    "OIDC_ISSUER_ENV",
    "PUBLIC_ORIGIN_ENV",
    "JUDGE_ALLOWED_SUBJECTS_ENV",
    "SESSION_MAXIMUM_PAYLOAD_ENV",
    "SESSION_MAXIMUM_PENDING_ENV",
    "SESSION_MAXIMUM_PER_OWNER_ENV",
    "SESSION_MAXIMUM_TOTAL_ENV",
    "SESSION_PENDING_TTL_ENV",
    "SESSION_TTL_ENV",
    "APPLICATION_DATABASE_ROLE",
    "COCKPIT_LEGACY_MODE_ENABLED_ENV",
    "DATABASE_ACQUISITION_TIMEOUT_ENV",
    "DATABASE_ALLOW_INSECURE_LOCAL_ENV",
    "DATABASE_CONNECTION_TIMEOUT_ENV",
    "DATABASE_MIGRATION_TIMEOUT_ENV",
    "DATABASE_POOL_MAX_ENV",
    "DATABASE_POOL_MIN_ENV",
    "DATABASE_STATEMENT_TIMEOUT_ENV",
    "PROVIDER_BUDGET_EPOCH_ENV",
    "PROVIDER_TENANT_ID_ENV",
    "PROVIDER_MAX_CALLS_PER_OWNER_ENV",
    "PROVIDER_MAX_CALLS_PER_REQUEST_ENV",
    "PROVIDER_MAX_CALLS_PER_SESSION_ENV",
    "PROVIDER_MAX_CALLS_TOTAL_ENV",
    "PROVIDER_MAX_CONCURRENT_CALLS_ENV",
    "PROVIDER_MAX_INPUT_BYTES_ENV",
    "PROVIDER_MAX_OUTPUT_TOKENS_ENV",
    "PROVIDER_MAX_QUEUED_CALLS_ENV",
    "PROVIDER_MAX_REQUESTS_PER_OWNER_ENV",
    "PROVIDER_MAX_REQUESTS_PER_SESSION_ENV",
    "PROVIDER_MAX_REQUESTS_TOTAL_ENV",
    "PROVIDER_MAX_REQUESTS_WINDOW_GLOBAL_ENV",
    "PROVIDER_MAX_REQUESTS_WINDOW_OWNER_ENV",
    "PROVIDER_MAX_REQUESTS_WINDOW_SESSION_ENV",
    "PROVIDER_QUEUE_WAIT_SECONDS_ENV",
    "PROVIDER_REQUEST_WINDOW_SECONDS_ENV",
    "PROVIDER_TIMEOUT_SECONDS_ENV",
    "DatabaseEndpoint",
    "REQUEST_CONTEXT_ROLE",
    "RUNTIME_BIND_HOST_ENV",
    "RUNTIME_MODE_ENV",
    "RUNTIME_PORT_ENV",
    "RuntimeAssemblyError",
    "RuntimeDatabaseSettings",
    "RuntimeErrorCode",
    "RuntimeMode",
    "RuntimeProviderGuardSettings",
    "RuntimeProviderSettings",
    "RuntimeJudgeAccessSettings",
    "RuntimeSessionSettings",
    "RuntimeSettings",
]
