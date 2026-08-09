"""Narrow provider and Draft V1 persistence ports for Step 22."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from .models import (
    DraftV1,
    ProviderCallRequest,
    ProviderIdentity,
    ProviderResponse,
    ProviderTextRequest,
    TimeoutPolicy,
)


class DraftV1Provider(Protocol):
    """Text-only provider boundary with no database or action capability."""

    def provider_identity(self) -> ProviderIdentity:
        ...

    def generate(
        self,
        request: ProviderCallRequest,
        timeout_policy: TimeoutPolicy,
    ) -> ProviderResponse:
        ...


class TextGenerationProvider(Protocol):
    """The same Step 22 pinned text-only provider for later bounded prompts."""

    def provider_identity(self) -> ProviderIdentity:
        ...

    def generate(
        self,
        request: ProviderTextRequest,
        timeout_policy: TimeoutPolicy,
    ) -> ProviderResponse:
        ...


class DraftV1Store(Protocol):
    """Kernel-owned persistence used only before or after the provider call."""

    def load(
        self,
        *,
        tenant_id: str,
        user_id: str,
        draft_id: str,
    ) -> DraftV1 | None:
        ...

    def put(self, draft: DraftV1) -> DraftV1:
        ...


class TrustedClock(Protocol):
    def now(self) -> datetime:
        ...


__all__ = [
    "DraftV1Provider",
    "DraftV1Store",
    "TextGenerationProvider",
    "TrustedClock",
]
