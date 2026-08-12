"""Cheap typed liveness and readiness projections for the 4 GB profile."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from aioa_memory_kernel.contracts.exceptions import ContractValidationError
from aioa_memory_kernel.contracts.serialization import canonical_sha256

from .resource_profile import Runtime4GBProfile, verify_runtime_4gb_profile


class ComponentState(str, Enum):
    READY = "READY"
    UNAVAILABLE = "UNAVAILABLE"
    DISABLED_INTENTIONAL = "DISABLED_INTENTIONAL"
    UNAVAILABLE_OPTIONAL = "UNAVAILABLE_OPTIONAL"
    UNLOADED_LAZY = "UNLOADED_LAZY"
    REQUEST_DRIVEN = "REQUEST_DRIVEN"


@dataclass(frozen=True, slots=True)
class ComponentHealth:
    component_id: str
    required: bool
    enabled: bool
    ready: bool
    state: ComponentState
    component_hash: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.component_id, str)
            or not self.component_id
            or not isinstance(self.required, bool)
            or not isinstance(self.enabled, bool)
            or not isinstance(self.ready, bool)
            or not isinstance(self.state, ComponentState)
        ):
            raise ContractValidationError("component health is malformed")
        if self.required and not self.enabled:
            raise ContractValidationError("required component cannot be disabled")
        if self.state is ComponentState.READY and not self.ready:
            raise ContractValidationError("READY component must be ready")
        expected = canonical_sha256(self, exclude_fields=("component_hash",))
        if self.component_hash != expected:
            raise ContractValidationError("component health hash differs")

    @classmethod
    def create(
        cls,
        *,
        component_id: str,
        required: bool,
        enabled: bool,
        ready: bool,
        state: ComponentState,
    ) -> "ComponentHealth":
        payload = {
            "component_id": component_id,
            "required": required,
            "enabled": enabled,
            "ready": ready,
            "state": state,
        }
        return cls(**payload, component_hash=canonical_sha256(payload))


@dataclass(frozen=True, slots=True)
class RuntimeHealthSnapshot:
    profile_digest: str
    liveness: bool
    readiness: bool
    components: tuple[ComponentHealth, ...]
    probe_performed_model_call: bool
    probe_performed_full_e2e: bool
    probe_performed_audit_chain_verification: bool
    snapshot_hash: str

    def __post_init__(self) -> None:
        if not isinstance(self.components, tuple) or not self.components:
            raise ContractValidationError("health components must be a tuple")
        if any(not isinstance(item, ComponentHealth) for item in self.components):
            raise ContractValidationError("health component type differs")
        if tuple(item.component_id for item in self.components) != tuple(
            sorted(item.component_id for item in self.components)
        ):
            raise ContractValidationError("health components must be sorted")
        if len({item.component_id for item in self.components}) != len(self.components):
            raise ContractValidationError("health component IDs must be unique")
        required_ready = all(item.ready for item in self.components if item.required)
        if self.readiness is not (self.liveness and required_ready):
            raise ContractValidationError("readiness result differs from components")
        if any(
            value is not False
            for value in (
                self.probe_performed_model_call,
                self.probe_performed_full_e2e,
                self.probe_performed_audit_chain_verification,
            )
        ):
            raise ContractValidationError("health probes must remain cheap")
        expected = canonical_sha256(self, exclude_fields=("snapshot_hash",))
        if self.snapshot_hash != expected:
            raise ContractValidationError("health snapshot hash differs")


def _health(
    component_id: str,
    *,
    required: bool,
    enabled: bool,
    ready: bool,
    state: ComponentState,
) -> ComponentHealth:
    return ComponentHealth.create(
        component_id=component_id,
        required=required,
        enabled=enabled,
        ready=ready,
        state=state,
    )


def build_runtime_health_snapshot(
    *,
    profile: Runtime4GBProfile,
    process_responsive: bool,
    external_volume_ready: bool,
    database_schema_ready: bool,
    german_law_corpus_ready: bool,
    personal_memory_persistence_ready: bool,
    audit_append_ready: bool,
    provider_configuration_ready: bool,
    owner_ui_ready: bool,
    embedding_loaded: bool,
    critic_enabled: bool = False,
    critic_available: bool = False,
    ingestion_enabled: bool = False,
) -> RuntimeHealthSnapshot:
    profile = verify_runtime_4gb_profile(profile)
    booleans = {
        "process_responsive": process_responsive,
        "external_volume_ready": external_volume_ready,
        "database_schema_ready": database_schema_ready,
        "german_law_corpus_ready": german_law_corpus_ready,
        "personal_memory_persistence_ready": personal_memory_persistence_ready,
        "audit_append_ready": audit_append_ready,
        "provider_configuration_ready": provider_configuration_ready,
        "owner_ui_ready": owner_ui_ready,
        "embedding_loaded": embedding_loaded,
        "critic_enabled": critic_enabled,
        "critic_available": critic_available,
        "ingestion_enabled": ingestion_enabled,
    }
    if any(not isinstance(value, bool) for value in booleans.values()):
        raise ContractValidationError("health inputs must be booleans")
    if critic_enabled and not critic_available:
        critic_state = ComponentState.UNAVAILABLE_OPTIONAL
        critic_ready = False
    elif critic_enabled:
        critic_state = ComponentState.READY
        critic_ready = True
    else:
        critic_state = ComponentState.DISABLED_INTENTIONAL
        critic_ready = True
    components = (
        _health(
            "audit",
            required=True,
            enabled=True,
            ready=audit_append_ready,
            state=(ComponentState.READY if audit_append_ready else ComponentState.UNAVAILABLE),
        ),
        _health(
            "critic",
            required=False,
            enabled=critic_enabled,
            ready=critic_ready,
            state=critic_state,
        ),
        _health(
            "database_schema",
            required=True,
            enabled=True,
            ready=database_schema_ready,
            state=(ComponentState.READY if database_schema_ready else ComponentState.UNAVAILABLE),
        ),
        _health(
            "embedding",
            required=False,
            enabled=True,
            ready=True,
            state=(ComponentState.READY if embedding_loaded else ComponentState.UNLOADED_LAZY),
        ),
        _health(
            "external_volume",
            required=True,
            enabled=True,
            ready=external_volume_ready,
            state=(ComponentState.READY if external_volume_ready else ComponentState.UNAVAILABLE),
        ),
        _health(
            "german_law_corpus",
            required=True,
            enabled=True,
            ready=german_law_corpus_ready,
            state=(ComponentState.READY if german_law_corpus_ready else ComponentState.UNAVAILABLE),
        ),
        _health(
            "ingestion",
            required=False,
            enabled=ingestion_enabled,
            ready=True,
            state=(ComponentState.READY if ingestion_enabled else ComponentState.DISABLED_INTENTIONAL),
        ),
        _health(
            "owner_ui",
            required=True,
            enabled=True,
            ready=owner_ui_ready,
            state=(ComponentState.READY if owner_ui_ready else ComponentState.UNAVAILABLE),
        ),
        _health(
            "personal_memory_persistence",
            required=True,
            enabled=True,
            ready=personal_memory_persistence_ready,
            state=(
                ComponentState.READY
                if personal_memory_persistence_ready
                else ComponentState.UNAVAILABLE
            ),
        ),
        _health(
            "provider_configuration",
            required=True,
            enabled=True,
            ready=provider_configuration_ready,
            state=(
                ComponentState.READY
                if provider_configuration_ready
                else ComponentState.UNAVAILABLE
            ),
        ),
        _health(
            "review_workspace",
            required=False,
            enabled=True,
            ready=True,
            state=ComponentState.REQUEST_DRIVEN,
        ),
    )
    liveness = process_responsive
    readiness = liveness and all(item.ready for item in components if item.required)
    payload = {
        "profile_digest": profile.profile_digest,
        "liveness": liveness,
        "readiness": readiness,
        "components": components,
        "probe_performed_model_call": False,
        "probe_performed_full_e2e": False,
        "probe_performed_audit_chain_verification": False,
    }
    return RuntimeHealthSnapshot(**payload, snapshot_hash=canonical_sha256(payload))


__all__ = [
    "ComponentHealth",
    "ComponentState",
    "RuntimeHealthSnapshot",
    "build_runtime_health_snapshot",
]
