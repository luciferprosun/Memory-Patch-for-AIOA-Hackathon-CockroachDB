"""Advisory model-experience memory contract; never factual evidence."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .enums import ModelExperienceOutcome
from .exceptions import ContractValidationError
from .serialization import (
    canonical_sha256,
    ensure_utc,
    require_enum_member,
    require_non_empty,
)


@dataclass(frozen=True, slots=True)
class ModelExperienceEvent:
    """A bounded hint about model behavior, scoped to model identity and owner."""

    model_experience_event_id: str
    tenant_id: str | None
    user_id: str | None
    personal_memory_space_id: str | None
    provider: str
    model_family: str
    exact_model_version: str
    failure_category: str
    kernel_run_id: str
    claim_category: str
    correction_outcome: ModelExperienceOutcome
    verifier_outcome: str
    created_at: datetime
    expires_at: datetime | None
    retention_policy: str
    event_hash: str = field(init=False)

    def __post_init__(self) -> None:
        for field_name in (
            "model_experience_event_id",
            "provider",
            "model_family",
            "exact_model_version",
            "failure_category",
            "kernel_run_id",
            "claim_category",
            "verifier_outcome",
            "retention_policy",
        ):
            require_non_empty(getattr(self, field_name), field_name)
        require_enum_member(
            self.correction_outcome,
            ModelExperienceOutcome,
            "correction_outcome",
        )
        if (self.tenant_id is None) != (self.user_id is None):
            raise ContractValidationError(
                "tenant and user scope must be both present or both absent"
            )
        if self.personal_memory_space_id is not None:
            if self.tenant_id is None or self.user_id is None:
                raise ContractValidationError(
                    "personal memory space requires tenant and user scope"
                )
            require_non_empty(
                self.personal_memory_space_id, "personal_memory_space_id"
            )
        object.__setattr__(
            self, "created_at", ensure_utc(self.created_at, "created_at")
        )
        if self.expires_at is not None:
            object.__setattr__(
                self, "expires_at", ensure_utc(self.expires_at, "expires_at")
            )
            if self.expires_at <= self.created_at:
                raise ContractValidationError(
                    "model experience expiry must follow creation"
                )
        object.__setattr__(
            self,
            "event_hash",
            canonical_sha256(self, exclude_fields=("event_hash",)),
        )


def assert_model_experience_is_advisory(
    event: ModelExperienceEvent,
    *,
    used_as_evidence: bool = False,
    used_for_approval: bool = False,
    used_for_action_authorization: bool = False,
) -> None:
    """Reject forbidden uses while leaving prioritization as a future seam."""

    if not isinstance(event, ModelExperienceEvent):
        raise ContractValidationError("event must be a ModelExperienceEvent")
    if used_as_evidence:
        raise ContractValidationError("model experience is never factual evidence")
    if used_for_approval:
        raise ContractValidationError("model experience cannot approve memory")
    if used_for_action_authorization:
        raise ContractValidationError("model experience cannot authorize actions")
