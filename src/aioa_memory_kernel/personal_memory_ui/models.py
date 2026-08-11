"""Bounded owner-facing projections for the Step 35 Personal Memory UI.

These objects are presentation contracts.  They deliberately contain no
business-state mutators, database credentials, provider credentials, or
Commit Helper capability.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from aioa_memory_kernel.contracts.serialization import canonical_sha256


STEP35_SCHEMA_VERSION = "1.0.0"
DEFAULT_SLOT_PAGE_SIZE = 20
MAXIMUM_SLOT_PAGE_SIZE = 50
DEFAULT_PATCH_PAGE_SIZE = 20
MAXIMUM_PATCH_PAGE_SIZE = 50
DEFAULT_AUDIT_PAGE_SIZE = 20
MAXIMUM_AUDIT_PAGE_SIZE = 50
MAXIMUM_OWNER_UI_TEXT_BYTES = 16 * 1024


def _text(value: object, name: str, maximum_bytes: int = 255) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value.encode("utf-8")) > maximum_bytes
        or any(ord(character) < 32 for character in value)
    ):
        raise ValueError(f"{name} is invalid")
    return value


def _digest(value: object, name: str) -> str:
    result = _text(value, name, 64)
    if len(result) != 64 or any(character not in "0123456789abcdef" for character in result):
        raise ValueError(f"{name} must be a SHA-256 digest")
    return result


def bounded_page_size(value: int, maximum: int) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < 1
        or value > maximum
    ):
        raise ValueError("page size is outside the allowed bound")
    return value


@dataclass(frozen=True, slots=True)
class OwnerPrincipal:
    """Trusted identity derived from a verified OIDC ID token."""

    tenant_id: str
    owner_user_id: str
    oidc_subject: str
    display_name: str

    def __post_init__(self) -> None:
        _text(self.tenant_id, "tenant_id")
        _text(self.owner_user_id, "owner_user_id")
        _text(self.oidc_subject, "oidc_subject")
        _text(self.display_name, "display_name", 512)


@dataclass(frozen=True, slots=True)
class QuotaView:
    stored_bytes: int
    maximum_bytes: int | None
    active_patches: int
    maximum_active_patches: int | None
    model_bindings: int
    maximum_model_bindings: int
    total_spaces: int
    maximum_total_spaces: int | None


@dataclass(frozen=True, slots=True)
class ModelBindingView:
    binding_id: str
    provider_id: str
    model_id: str
    model_revision: str
    binding_mode: str
    enabled: bool
    binding_hash: str


@dataclass(frozen=True, slots=True)
class SlotView:
    personal_memory_space_id: str
    display_name: str
    state: str
    slot_hash: str
    state_version: int
    configuration_version: int
    updated_at: datetime
    quota: QuotaView
    model_bindings: tuple[ModelBindingView, ...]
    patch_count: int
    pending_approval_count: int
    active_patch_count: int


@dataclass(frozen=True, slots=True)
class PatchView:
    proposal_id: str
    proposal_hash: str
    personal_memory_space_id: str
    statement: str
    statement_hash: str
    state: str
    state_version: int
    state_hash: str
    scope_summary: tuple[str, ...]
    model_binding_id: str
    validation_receipt_hash: str | None
    evidence_binding_hash: str | None
    validation_summary: str
    limitations: tuple[str, ...]
    patch_id: str | None
    patch_hash: str | None
    approval_receipt_hash: str | None
    activation_receipt_hash: str | None
    terminal_record_hash: str | None
    superseded_by_patch_id: str | None
    updated_at: datetime
    canonical_evidence: bool = False

    def __post_init__(self) -> None:
        if self.canonical_evidence:
            raise ValueError("Personal Memory cannot be canonical evidence")
        if len(self.statement.encode("utf-8")) > MAXIMUM_OWNER_UI_TEXT_BYTES:
            raise ValueError("statement exceeds the owner UI bound")


@dataclass(frozen=True, slots=True)
class AuditEventView:
    event_id: str
    event_type: str
    subject_type: str
    subject_id: str
    subject_hash: str
    event_hash: str
    sequence_number: int
    occurred_at: datetime
    reason_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DashboardView:
    slots: tuple[SlotView, ...]
    pending_approvals: tuple[PatchView, ...]
    recent_patches: tuple[PatchView, ...]
    recent_audit_events: tuple[AuditEventView, ...]
    slot_count: int
    active_slot_count: int
    pending_approval_count: int
    active_patch_count: int
    superseded_patch_count: int
    revoked_patch_count: int


@dataclass(frozen=True, slots=True)
class OwnerActionResult:
    action: str
    subject_id: str
    resulting_state: str
    receipt_hash: str
    replayed: bool
    message: str
    result_hash: str = ""

    def __post_init__(self) -> None:
        _text(self.action, "action", 64)
        _text(self.subject_id, "subject_id")
        _text(self.resulting_state, "resulting_state", 64)
        _digest(self.receipt_hash, "receipt_hash")
        if not isinstance(self.replayed, bool):
            raise ValueError("replayed must be boolean")
        _text(self.message, "message", 1024)
        expected = canonical_sha256(self, exclude_fields=("result_hash",))
        if self.result_hash and self.result_hash != expected:
            raise ValueError("result_hash mismatch")
        object.__setattr__(self, "result_hash", expected)


class PersonalMemoryUiError(RuntimeError):
    """Sanitized UI boundary error."""

    status_code = 400
    safe_message = "The Personal Memory request could not be completed."


class PersonalMemoryUiNotFound(PersonalMemoryUiError):
    status_code = 404
    safe_message = "The requested Personal Memory item was not found."


class PersonalMemoryUiConflict(PersonalMemoryUiError):
    status_code = 409
    safe_message = "The item changed. Refresh the page and try again."


class PersonalMemoryUiAccessDenied(PersonalMemoryUiError):
    status_code = 403
    safe_message = "You are not authorized to access this Personal Memory item."


__all__ = [
    "AuditEventView",
    "DashboardView",
    "DEFAULT_AUDIT_PAGE_SIZE",
    "DEFAULT_PATCH_PAGE_SIZE",
    "DEFAULT_SLOT_PAGE_SIZE",
    "MAXIMUM_AUDIT_PAGE_SIZE",
    "MAXIMUM_PATCH_PAGE_SIZE",
    "MAXIMUM_SLOT_PAGE_SIZE",
    "ModelBindingView",
    "OwnerActionResult",
    "OwnerPrincipal",
    "PatchView",
    "PersonalMemoryUiAccessDenied",
    "PersonalMemoryUiConflict",
    "PersonalMemoryUiError",
    "PersonalMemoryUiNotFound",
    "QuotaView",
    "STEP35_SCHEMA_VERSION",
    "SlotView",
    "bounded_page_size",
]
