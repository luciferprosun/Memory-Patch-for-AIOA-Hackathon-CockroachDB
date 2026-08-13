"""Step 35 owner-facing Personal Memory workspace.

Core view-model and repository adapters stay importable in the pinned model
runtime, which deliberately has no HTTP/UI dependencies.  OIDC and FastAPI
exports retain the public package API but load only when a caller requests
them.
"""

from __future__ import annotations

import importlib

from .backend import (
    KernelPersonalMemoryUiBackend,
    PersonalMemoryUiBackend,
    PersonalMemoryUiReadRepository,
)
from .models import (
    AuditEventView,
    DashboardView,
    ModelBindingView,
    OwnerActionResult,
    OwnerPrincipal,
    PatchView,
    PersonalMemoryUiAccessDenied,
    PersonalMemoryUiConflict,
    PersonalMemoryUiError,
    PersonalMemoryUiNotFound,
    QuotaView,
    STEP35_SCHEMA_VERSION,
    SlotView,
)


_AUTH_EXPORTS = frozenset(
    {
        "HttpxOidcClient",
        "MemoryOwnerSessionStore",
        "OidcClient",
        "OidcSettings",
        "OwnerSession",
        "OwnerSessionStore",
        "pkce_challenge",
    }
)
_DURABLE_SESSION_EXPORTS = frozenset(
    {
        "CockroachOwnerSessionRepository",
        "CockroachOwnerSessionStore",
        "DurableSessionError",
        "DurableSessionLimits",
    }
)
_WEB_EXPORTS = frozenset({"create_personal_memory_app"})


def __getattr__(name: str):
    if name in _AUTH_EXPORTS:
        module = importlib.import_module(f"{__name__}.auth")
    elif name in _DURABLE_SESSION_EXPORTS:
        module = importlib.import_module(f"{__name__}.cockroach_sessions")
    elif name in _WEB_EXPORTS:
        module = importlib.import_module(f"{__name__}.web")
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(module, name)
    globals()[name] = value
    return value

__all__ = [
    "AuditEventView",
    "CockroachOwnerSessionRepository",
    "CockroachOwnerSessionStore",
    "DashboardView",
    "DurableSessionError",
    "DurableSessionLimits",
    "HttpxOidcClient",
    "KernelPersonalMemoryUiBackend",
    "MemoryOwnerSessionStore",
    "ModelBindingView",
    "OidcClient",
    "OidcSettings",
    "OwnerActionResult",
    "OwnerPrincipal",
    "OwnerSession",
    "OwnerSessionStore",
    "PatchView",
    "PersonalMemoryUiAccessDenied",
    "PersonalMemoryUiBackend",
    "PersonalMemoryUiConflict",
    "PersonalMemoryUiError",
    "PersonalMemoryUiNotFound",
    "PersonalMemoryUiReadRepository",
    "QuotaView",
    "STEP35_SCHEMA_VERSION",
    "SlotView",
    "create_personal_memory_app",
    "pkce_challenge",
]
