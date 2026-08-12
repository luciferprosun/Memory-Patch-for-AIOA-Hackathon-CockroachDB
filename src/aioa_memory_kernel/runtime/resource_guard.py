"""Fail-closed resource-pressure decisions for the Step 40 profile."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from aioa_memory_kernel.contracts.exceptions import IntegrityError
from aioa_memory_kernel.contracts.serialization import (
    canonical_sha256,
    require_sha256_hex,
)

from .resource_profile import Runtime4GBProfile, verify_runtime_4gb_profile


class ResourceWorkKind(str, Enum):
    REQUIRED_CORE = "REQUIRED_CORE"
    OPTIONAL_CRITIC = "OPTIONAL_CRITIC"
    OPTIONAL_INGESTION = "OPTIONAL_INGESTION"
    HEAVY_EMBEDDING = "HEAVY_EMBEDDING"
    LARGE_EXPORT = "LARGE_EXPORT"


class ResourcePressureState(str, Enum):
    NORMAL = "NORMAL"
    SOFT_PRESSURE = "SOFT_PRESSURE"
    HARD_PRESSURE = "HARD_PRESSURE"


class ResourceDecisionCode(str, Enum):
    PERMITTED = "PERMITTED"
    QUEUE_CAPACITY_REACHED = "QUEUE_CAPACITY_REACHED"
    OPTIONAL_CRITIC_SUPPRESSED = "OPTIONAL_CRITIC_SUPPRESSED"
    OPTIONAL_INGESTION_PAUSED = "OPTIONAL_INGESTION_PAUSED"
    EMBEDDING_BACKPRESSURE = "EMBEDDING_BACKPRESSURE"
    EXPORT_BACKPRESSURE = "EXPORT_BACKPRESSURE"
    CORE_REQUEST_FAILED_CLOSED = "CORE_REQUEST_FAILED_CLOSED"


@dataclass(frozen=True, slots=True)
class ResourceObservation:
    host_total_mib: int
    host_available_mib: int
    process_tree_rss_mib: int
    observation_hash: str

    def __post_init__(self) -> None:
        for name in ("host_total_mib", "host_available_mib", "process_tree_rss_mib"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if self.host_total_mib < 1 or self.host_available_mib > self.host_total_mib:
            raise ValueError("host memory observation is inconsistent")
        require_sha256_hex(self.observation_hash, "observation_hash")
        expected = canonical_sha256(self, exclude_fields=("observation_hash",))
        if self.observation_hash != expected:
            raise IntegrityError("resource observation hash differs")

    @classmethod
    def create(
        cls,
        *,
        host_total_mib: int,
        host_available_mib: int,
        process_tree_rss_mib: int,
    ) -> "ResourceObservation":
        payload = {
            "host_total_mib": host_total_mib,
            "host_available_mib": host_available_mib,
            "process_tree_rss_mib": process_tree_rss_mib,
        }
        return cls(**payload, observation_hash=canonical_sha256(payload))

    @property
    def host_observed_usage_mib(self) -> int:
        return self.host_total_mib - self.host_available_mib


@dataclass(frozen=True, slots=True)
class ResourceGuardDecision:
    profile_digest: str
    observation_hash: str
    work_kind: ResourceWorkKind
    pressure_state: ResourcePressureState
    allowed: bool
    reason_code: ResourceDecisionCode
    queue_depth: int
    queue_limit: int
    verifier_bypass: bool
    audit_bypass: bool
    rls_bypass: bool
    automatic_personal_memory_approval: bool
    route_override: bool
    source_authority_override: bool
    canonical_evidence_override: bool
    decision_hash: str

    def __post_init__(self) -> None:
        require_sha256_hex(self.profile_digest, "profile_digest")
        require_sha256_hex(self.observation_hash, "observation_hash")
        require_sha256_hex(self.decision_hash, "decision_hash")
        if not isinstance(self.work_kind, ResourceWorkKind):
            raise ValueError("work_kind must be typed")
        if not isinstance(self.pressure_state, ResourcePressureState):
            raise ValueError("pressure_state must be typed")
        if not isinstance(self.reason_code, ResourceDecisionCode):
            raise ValueError("reason_code must be typed")
        if (
            not isinstance(self.allowed, bool)
            or not isinstance(self.queue_depth, int)
            or isinstance(self.queue_depth, bool)
            or self.queue_depth < 0
            or not isinstance(self.queue_limit, int)
            or isinstance(self.queue_limit, bool)
            or self.queue_limit < 1
        ):
            raise ValueError("resource guard decision bounds are malformed")
        authority = (
            self.verifier_bypass,
            self.audit_bypass,
            self.rls_bypass,
            self.automatic_personal_memory_approval,
            self.route_override,
            self.source_authority_override,
            self.canonical_evidence_override,
        )
        if any(value is not False for value in authority):
            raise ValueError("resource pressure cannot grant authority")
        if self.allowed is (self.reason_code is not ResourceDecisionCode.PERMITTED):
            raise ValueError("resource guard disposition and reason differ")
        expected = canonical_sha256(self, exclude_fields=("decision_hash",))
        if self.decision_hash != expected:
            raise IntegrityError("resource guard decision hash differs")

    @classmethod
    def create(
        cls,
        *,
        profile_digest: str,
        observation_hash: str,
        work_kind: ResourceWorkKind,
        pressure_state: ResourcePressureState,
        allowed: bool,
        reason_code: ResourceDecisionCode,
        queue_depth: int,
        queue_limit: int,
    ) -> "ResourceGuardDecision":
        payload = {
            "profile_digest": profile_digest,
            "observation_hash": observation_hash,
            "work_kind": work_kind,
            "pressure_state": pressure_state,
            "allowed": allowed,
            "reason_code": reason_code,
            "queue_depth": queue_depth,
            "queue_limit": queue_limit,
            "verifier_bypass": False,
            "audit_bypass": False,
            "rls_bypass": False,
            "automatic_personal_memory_approval": False,
            "route_override": False,
            "source_authority_override": False,
            "canonical_evidence_override": False,
        }
        return cls(**payload, decision_hash=canonical_sha256(payload))


class ResourcePressureGuard:
    """Apply only the Step 40 degradation order to one typed observation."""

    def __init__(self, profile: Runtime4GBProfile) -> None:
        self._profile = verify_runtime_4gb_profile(profile)

    def _pressure(self, observation: ResourceObservation) -> ResourcePressureState:
        budget = self._profile.host_budget
        if (
            observation.host_observed_usage_mib
            >= budget.hard_pressure_observed_usage_mib
            or observation.host_available_mib < budget.minimum_available_mib
            or observation.process_tree_rss_mib > budget.runtime_peak_budget_mib
        ):
            return ResourcePressureState.HARD_PRESSURE
        if (
            observation.host_available_mib < budget.minimum_available_mib * 2
            or observation.process_tree_rss_mib > budget.runtime_steady_budget_mib
        ):
            return ResourcePressureState.SOFT_PRESSURE
        return ResourcePressureState.NORMAL

    def evaluate(
        self,
        *,
        work_kind: ResourceWorkKind,
        observation: ResourceObservation,
        queue_depth: int,
    ) -> ResourceGuardDecision:
        if not isinstance(work_kind, ResourceWorkKind):
            raise TypeError("work_kind must be ResourceWorkKind")
        if not isinstance(observation, ResourceObservation):
            raise TypeError("observation must be ResourceObservation")
        ResourceObservation(
            host_total_mib=observation.host_total_mib,
            host_available_mib=observation.host_available_mib,
            process_tree_rss_mib=observation.process_tree_rss_mib,
            observation_hash=observation.observation_hash,
        )
        if not isinstance(queue_depth, int) or isinstance(queue_depth, bool) or queue_depth < 0:
            raise ValueError("queue_depth must be a non-negative integer")
        queue_limit = self._profile.queues.limit_for(work_kind.value)
        pressure = self._pressure(observation)
        reason = ResourceDecisionCode.PERMITTED
        allowed = True
        if queue_depth >= queue_limit:
            allowed = False
            reason = ResourceDecisionCode.QUEUE_CAPACITY_REACHED
        elif pressure is not ResourcePressureState.NORMAL:
            blocked = {
                ResourceWorkKind.OPTIONAL_CRITIC: ResourceDecisionCode.OPTIONAL_CRITIC_SUPPRESSED,
                ResourceWorkKind.OPTIONAL_INGESTION: ResourceDecisionCode.OPTIONAL_INGESTION_PAUSED,
                ResourceWorkKind.HEAVY_EMBEDDING: ResourceDecisionCode.EMBEDDING_BACKPRESSURE,
                ResourceWorkKind.LARGE_EXPORT: ResourceDecisionCode.EXPORT_BACKPRESSURE,
            }
            if work_kind in blocked:
                allowed = False
                reason = blocked[work_kind]
            elif pressure is ResourcePressureState.HARD_PRESSURE:
                allowed = False
                reason = ResourceDecisionCode.CORE_REQUEST_FAILED_CLOSED
        return ResourceGuardDecision.create(
            profile_digest=self._profile.profile_digest,
            observation_hash=observation.observation_hash,
            work_kind=work_kind,
            pressure_state=pressure,
            allowed=allowed,
            reason_code=reason,
            queue_depth=queue_depth,
            queue_limit=queue_limit,
        )


__all__ = [
    "ResourceDecisionCode",
    "ResourceGuardDecision",
    "ResourceObservation",
    "ResourcePressureGuard",
    "ResourcePressureState",
    "ResourceWorkKind",
]
