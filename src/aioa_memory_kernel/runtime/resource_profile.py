"""Versioned constrained-runtime profile for Memory Patch Step 40.

The profile controls resource placement and bounds only.  Every authority flag
is frozen false and the profile cannot change routing, evidence, verification,
Personal Memory, audit, or credential semantics.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

from aioa_memory_kernel.contracts.exceptions import (
    ContractValidationError,
    IntegrityError,
)
from aioa_memory_kernel.contracts.serialization import (
    canonical_sha256,
    require_sha256_hex,
)
from aioa_memory_kernel.security.redaction import assert_secret_free


PROFILE_SCHEMA_VERSION = "1.0.0"
PROFILE_ID = "memory-patch-4gb-demo-1a"
PROFILE_VERSION = "1.0.0"
DEFAULT_PROFILE_PATH = (
    Path(__file__).resolve().parents[3]
    / "config"
    / "runtime"
    / "4gb-demo-1a.json"
)

_T = TypeVar("_T")


def _integer(value: object, field_name: str, *, minimum: int = 0) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < minimum
    ):
        raise ContractValidationError(
            f"{field_name} must be an integer >= {minimum}"
        )
    return value


def _boolean(value: object, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ContractValidationError(f"{field_name} must be boolean")
    return value


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ContractValidationError(f"{field_name} must be canonical text")
    return value


def _exact_keys(
    value: object,
    expected: frozenset[str],
    field_name: str,
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != expected:
        raise ContractValidationError(f"{field_name} keys differ from the profile")
    if not all(isinstance(key, str) for key in value):
        raise ContractValidationError(f"{field_name} keys must be strings")
    return value


def _reconstruct(value: _T, expected_type: type[_T]) -> _T:
    if not isinstance(value, expected_type):
        raise IntegrityError(f"runtime profile requires {expected_type.__name__}")
    try:
        reconstructed = expected_type(
            **{
                field.name: getattr(value, field.name)
                for field in dataclasses.fields(expected_type)
            }
        )
    except (ContractValidationError, TypeError, ValueError) as exc:
        raise IntegrityError(
            f"{expected_type.__name__} failed canonical reconstruction"
        ) from exc
    if reconstructed != value:
        raise IntegrityError(f"{expected_type.__name__} is not canonical")
    return reconstructed


@dataclass(frozen=True, slots=True)
class HostMemoryBudget:
    nominal_host_mib: int
    minimum_detected_host_mib: int
    os_headroom_mib: int
    runtime_idle_budget_mib: int
    runtime_steady_budget_mib: int
    runtime_peak_budget_mib: int
    hard_pressure_observed_usage_mib: int
    minimum_available_mib: int

    def __post_init__(self) -> None:
        for field in dataclasses.fields(self):
            _integer(getattr(self, field.name), field.name, minimum=1)
        if not (
            self.minimum_detected_host_mib <= self.nominal_host_mib
            and self.runtime_idle_budget_mib < self.runtime_steady_budget_mib
            < self.runtime_peak_budget_mib
            < self.hard_pressure_observed_usage_mib
            < self.minimum_detected_host_mib
            and self.runtime_peak_budget_mib + self.os_headroom_mib
            <= self.nominal_host_mib
        ):
            raise ContractValidationError("host memory budgets are inconsistent")


@dataclass(frozen=True, slots=True)
class ProcessLayout:
    web_workers: int
    additional_frontend_processes: int
    local_generation_model_processes: int
    ingestion_workers: int
    review_workers: int

    def __post_init__(self) -> None:
        if _integer(self.web_workers, "web_workers", minimum=1) != 1:
            raise ContractValidationError("the 4 GB profile requires one web worker")
        for name in (
            "additional_frontend_processes",
            "local_generation_model_processes",
            "ingestion_workers",
            "review_workers",
        ):
            if _integer(getattr(self, name), name) != 0:
                raise ContractValidationError(
                    f"the constrained profile requires {name}=0"
                )


@dataclass(frozen=True, slots=True)
class DatabaseLimits:
    topology: str
    local_cockroach_processes: int
    application_pool_max: int
    commit_helper_pool_max: int
    audit_pool_max: int
    review_pool_max: int
    single_node_demo_available: bool
    production_ha_proven: bool

    def __post_init__(self) -> None:
        if _text(self.topology, "database.topology") != "REMOTE_REQUIRED_SERVICE":
            raise ContractValidationError("the constrained profile requires remote DB")
        if _integer(self.local_cockroach_processes, "local_cockroach_processes") != 0:
            raise ContractValidationError("core profile cannot start local CockroachDB")
        for name, maximum in (
            ("application_pool_max", 4),
            ("commit_helper_pool_max", 2),
            ("audit_pool_max", 2),
            ("review_pool_max", 2),
        ):
            value = _integer(getattr(self, name), name, minimum=1)
            if value > maximum:
                raise ContractValidationError(f"{name} exceeds its 4 GB bound")
        if self.commit_helper_pool_max == self.application_pool_max:
            raise ContractValidationError("Commit Helper pool must remain distinct")
        _boolean(self.single_node_demo_available, "single_node_demo_available")
        if _boolean(self.production_ha_proven, "production_ha_proven"):
            raise ContractValidationError("Step 40 does not prove production HA")

    @property
    def maximum_connections(self) -> int:
        return (
            self.application_pool_max
            + self.commit_helper_pool_max
            + self.audit_pool_max
            + self.review_pool_max
        )


@dataclass(frozen=True, slots=True)
class EmbeddingLimits:
    model_id: str
    lazy_load: bool
    maximum_instances: int
    model_processes: int
    batch_size: int
    hard_batch_limit: int

    def __post_init__(self) -> None:
        if _text(self.model_id, "embedding.model_id") != "intfloat/multilingual-e5-small":
            raise ContractValidationError("Step 19 embedding identity changed")
        if not _boolean(self.lazy_load, "embedding.lazy_load"):
            raise ContractValidationError("4 GB profile requires lazy embedding load")
        if _integer(self.maximum_instances, "maximum_instances", minimum=1) != 1:
            raise ContractValidationError("embedding runtime must be singleton")
        if _integer(self.model_processes, "model_processes", minimum=1) != 1:
            raise ContractValidationError("embedding model process count must be one")
        batch = _integer(self.batch_size, "embedding.batch_size", minimum=1)
        hard = _integer(self.hard_batch_limit, "embedding.hard_batch_limit", minimum=1)
        if batch > 8 or hard != 64 or batch > hard:
            raise ContractValidationError("embedding batch limits differ from Step 40")


@dataclass(frozen=True, slots=True)
class OptionalServicePolicy:
    critic_enabled_by_default: bool
    ingestion_enabled_by_default: bool
    ingestion_requires_prepared_corpus: bool
    review_request_driven: bool
    audit_enabled: bool

    def __post_init__(self) -> None:
        if _boolean(self.critic_enabled_by_default, "critic_enabled_by_default"):
            raise ContractValidationError("Critic must be disabled by default")
        if _boolean(self.ingestion_enabled_by_default, "ingestion_enabled_by_default"):
            raise ContractValidationError("ingestion must be disabled by default")
        if not _boolean(
            self.ingestion_requires_prepared_corpus,
            "ingestion_requires_prepared_corpus",
        ):
            raise ContractValidationError("disabled ingestion requires prepared corpus")
        if not _boolean(self.review_request_driven, "review_request_driven"):
            raise ContractValidationError("review must remain request driven")
        if not _boolean(self.audit_enabled, "audit_enabled"):
            raise ContractValidationError("audit cannot be disabled")


@dataclass(frozen=True, slots=True)
class QueueLimits:
    provider: int
    embedding: int
    critic: int
    ingestion: int
    review: int
    audit: int
    export: int

    def __post_init__(self) -> None:
        ceilings = {
            "provider": 2,
            "embedding": 8,
            "critic": 2,
            "ingestion": 2,
            "review": 8,
            "audit": 32,
            "export": 2,
        }
        for name, ceiling in ceilings.items():
            value = _integer(getattr(self, name), f"queues.{name}", minimum=1)
            if value > ceiling:
                raise ContractValidationError(f"queues.{name} exceeds its bound")

    def limit_for(self, work_kind: str) -> int:
        mapping = {
            "REQUIRED_CORE": self.provider,
            "OPTIONAL_CRITIC": self.critic,
            "OPTIONAL_INGESTION": self.ingestion,
            "HEAVY_EMBEDDING": self.embedding,
            "LARGE_EXPORT": self.export,
        }
        try:
            return mapping[work_kind]
        except KeyError as exc:
            raise ContractValidationError("unknown resource work kind") from exc


@dataclass(frozen=True, slots=True)
class ThreadLimits:
    blocking_executor_max: int
    embedding_intraop: int
    omp: int
    mkl: int
    tokenizer_parallelism: bool

    def __post_init__(self) -> None:
        if not 1 <= _integer(self.blocking_executor_max, "blocking_executor_max", minimum=1) <= 4:
            raise ContractValidationError("blocking executor exceeds its bound")
        for name in ("embedding_intraop", "omp", "mkl"):
            if _integer(getattr(self, name), name, minimum=1) != 1:
                raise ContractValidationError(f"{name} must equal one")
        if _boolean(self.tokenizer_parallelism, "tokenizer_parallelism"):
            raise ContractValidationError("tokenizer parallelism must be disabled")


@dataclass(frozen=True, slots=True)
class CachePolicy:
    location_class: str
    derived_external_cache_max_mib: int
    in_memory_cache_max_mib: int
    authoritative: bool
    rebuildable: bool

    def __post_init__(self) -> None:
        if _text(self.location_class, "cache.location_class") != "VERIFIED_EXTERNAL_VOLUME":
            raise ContractValidationError("cache must use the verified external volume")
        if not 1 <= _integer(
            self.derived_external_cache_max_mib,
            "derived_external_cache_max_mib",
            minimum=1,
        ) <= 4096:
            raise ContractValidationError("external cache bound is unsafe")
        if not 1 <= _integer(
            self.in_memory_cache_max_mib,
            "in_memory_cache_max_mib",
            minimum=1,
        ) <= 64:
            raise ContractValidationError("in-memory cache exceeds 64 MiB")
        if _boolean(self.authoritative, "cache.authoritative"):
            raise ContractValidationError("derived cache cannot be authoritative")
        if not _boolean(self.rebuildable, "cache.rebuildable"):
            raise ContractValidationError("derived cache must be rebuildable")


@dataclass(frozen=True, slots=True)
class StartupPolicy:
    order: tuple[str, ...]
    readiness_timeout_seconds: int
    shutdown_timeout_seconds: int

    def __post_init__(self) -> None:
        expected = (
            "VALIDATE_PROFILE",
            "VALIDATE_EXTERNAL_VOLUME",
            "CONNECT_DATABASE",
            "VERIFY_SCHEMA",
            "VERIFY_PREPARED_CORPUS",
            "START_KERNEL_UI",
            "KEEP_EMBEDDING_UNLOADED",
            "KEEP_CRITIC_DISABLED",
            "KEEP_INGESTION_DISABLED",
        )
        if not isinstance(self.order, tuple) or self.order != expected:
            raise ContractValidationError("startup order differs from Step 40")
        if not 1 <= _integer(
            self.readiness_timeout_seconds,
            "readiness_timeout_seconds",
            minimum=1,
        ) <= 30:
            raise ContractValidationError("readiness timeout is unbounded")
        if not 1 <= _integer(
            self.shutdown_timeout_seconds,
            "shutdown_timeout_seconds",
            minimum=1,
        ) <= 60:
            raise ContractValidationError("shutdown timeout is unbounded")


@dataclass(frozen=True, slots=True)
class LoggingPolicy:
    level: str
    large_payload_logging: bool
    security_logging_enabled: bool

    def __post_init__(self) -> None:
        if _text(self.level, "logging.level") != "INFO":
            raise ContractValidationError("constrained logging level must be INFO")
        if _boolean(self.large_payload_logging, "large_payload_logging"):
            raise ContractValidationError("large payload logging is forbidden")
        if not _boolean(self.security_logging_enabled, "security_logging_enabled"):
            raise ContractValidationError("security logging cannot be disabled")


@dataclass(frozen=True, slots=True)
class AuthorityCeiling:
    route_override: bool
    source_authority_override: bool
    canonical_evidence_override: bool
    verifier_bypass: bool
    rls_bypass: bool
    audit_bypass: bool
    approval_bypass: bool
    commit_boundary_merge: bool

    def __post_init__(self) -> None:
        if any(_boolean(getattr(self, field.name), field.name) for field in dataclasses.fields(self)):
            raise ContractValidationError("resource profile cannot gain authority")


@dataclass(frozen=True, slots=True)
class Runtime4GBProfile:
    schema_version: str
    profile_id: str
    profile_version: str
    host_budget: HostMemoryBudget
    process_layout: ProcessLayout
    database: DatabaseLimits
    embedding: EmbeddingLimits
    optional_services: OptionalServicePolicy
    queues: QueueLimits
    threads: ThreadLimits
    cache: CachePolicy
    startup: StartupPolicy
    logging: LoggingPolicy
    authority: AuthorityCeiling
    profile_digest: str

    def __post_init__(self) -> None:
        if self.schema_version != PROFILE_SCHEMA_VERSION:
            raise ContractValidationError("unsupported resource profile schema")
        if self.profile_id != PROFILE_ID or self.profile_version != PROFILE_VERSION:
            raise ContractValidationError("resource profile identity differs")
        for field_name, expected_type in (
            ("host_budget", HostMemoryBudget),
            ("process_layout", ProcessLayout),
            ("database", DatabaseLimits),
            ("embedding", EmbeddingLimits),
            ("optional_services", OptionalServicePolicy),
            ("queues", QueueLimits),
            ("threads", ThreadLimits),
            ("cache", CachePolicy),
            ("startup", StartupPolicy),
            ("logging", LoggingPolicy),
            ("authority", AuthorityCeiling),
        ):
            if not isinstance(getattr(self, field_name), expected_type):
                raise ContractValidationError(
                    f"{field_name} must be {expected_type.__name__}"
                )
        require_sha256_hex(self.profile_digest, "profile_digest")
        expected = canonical_sha256(self, exclude_fields=("profile_digest",))
        if self.profile_digest != expected:
            raise IntegrityError("resource profile digest differs")


_ROOT_KEYS = frozenset(field.name for field in dataclasses.fields(Runtime4GBProfile))


def _decode_json(payload: str) -> Mapping[str, Any]:
    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ContractValidationError("runtime profile has duplicate keys")
            result[key] = value
        return result

    try:
        value = json.loads(
            payload,
            object_pairs_hook=unique_object,
            parse_constant=lambda _: (_ for _ in ()).throw(
                ContractValidationError("non-finite profile value")
            ),
        )
    except (json.JSONDecodeError, UnicodeError, RecursionError) as exc:
        raise ContractValidationError("runtime profile JSON is malformed") from exc
    return _exact_keys(value, _ROOT_KEYS, "runtime profile")


def _nested(
    raw: Mapping[str, Any],
    name: str,
    expected_type: type[_T],
) -> _T:
    expected = frozenset(field.name for field in dataclasses.fields(expected_type))
    value = _exact_keys(raw[name], expected, name)
    kwargs = dict(value)
    if expected_type is StartupPolicy:
        order = kwargs.get("order")
        if not isinstance(order, list) or any(not isinstance(item, str) for item in order):
            raise ContractValidationError("startup order must be a string list")
        kwargs["order"] = tuple(order)
    try:
        return expected_type(**kwargs)
    except TypeError as exc:
        raise ContractValidationError(f"{name} is malformed") from exc


def load_runtime_4gb_profile(
    path: Path = DEFAULT_PROFILE_PATH,
) -> Runtime4GBProfile:
    if not isinstance(path, Path) or not path.is_file() or path.is_symlink():
        raise ContractValidationError("resource profile file is unavailable")
    try:
        payload = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ContractValidationError("resource profile could not be read") from exc
    raw = _decode_json(payload)
    assert_secret_free(raw, surface="Step40 runtime profile", reject_machine_paths=True)
    profile = Runtime4GBProfile(
        schema_version=raw["schema_version"],
        profile_id=raw["profile_id"],
        profile_version=raw["profile_version"],
        host_budget=_nested(raw, "host_budget", HostMemoryBudget),
        process_layout=_nested(raw, "process_layout", ProcessLayout),
        database=_nested(raw, "database", DatabaseLimits),
        embedding=_nested(raw, "embedding", EmbeddingLimits),
        optional_services=_nested(
            raw,
            "optional_services",
            OptionalServicePolicy,
        ),
        queues=_nested(raw, "queues", QueueLimits),
        threads=_nested(raw, "threads", ThreadLimits),
        cache=_nested(raw, "cache", CachePolicy),
        startup=_nested(raw, "startup", StartupPolicy),
        logging=_nested(raw, "logging", LoggingPolicy),
        authority=_nested(raw, "authority", AuthorityCeiling),
        profile_digest=raw["profile_digest"],
    )
    return verify_runtime_4gb_profile(profile)


def verify_runtime_4gb_profile(profile: Runtime4GBProfile) -> Runtime4GBProfile:
    if not isinstance(profile, Runtime4GBProfile):
        raise IntegrityError("runtime profile type differs")
    nested: dict[str, Any] = {}
    for field_name, expected_type in (
        ("host_budget", HostMemoryBudget),
        ("process_layout", ProcessLayout),
        ("database", DatabaseLimits),
        ("embedding", EmbeddingLimits),
        ("optional_services", OptionalServicePolicy),
        ("queues", QueueLimits),
        ("threads", ThreadLimits),
        ("cache", CachePolicy),
        ("startup", StartupPolicy),
        ("logging", LoggingPolicy),
        ("authority", AuthorityCeiling),
    ):
        nested[field_name] = _reconstruct(getattr(profile, field_name), expected_type)
    reconstructed = Runtime4GBProfile(
        schema_version=profile.schema_version,
        profile_id=profile.profile_id,
        profile_version=profile.profile_version,
        profile_digest=profile.profile_digest,
        **nested,
    )
    if reconstructed != profile:
        raise IntegrityError("runtime profile is not canonical")
    return reconstructed


__all__ = [
    "AuthorityCeiling",
    "CachePolicy",
    "DEFAULT_PROFILE_PATH",
    "DatabaseLimits",
    "EmbeddingLimits",
    "HostMemoryBudget",
    "LoggingPolicy",
    "OptionalServicePolicy",
    "PROFILE_ID",
    "PROFILE_SCHEMA_VERSION",
    "PROFILE_VERSION",
    "ProcessLayout",
    "QueueLimits",
    "Runtime4GBProfile",
    "StartupPolicy",
    "ThreadLimits",
    "load_runtime_4gb_profile",
    "verify_runtime_4gb_profile",
]
