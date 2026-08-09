"""Narrow Step 25 persistence port."""

from __future__ import annotations

from typing import Protocol

from .models import DraftV2


class DraftV2Store(Protocol):
    def load(
        self,
        *,
        tenant_id: str,
        user_id: str,
        draft_id: str,
    ) -> DraftV2 | None: ...

    def put(self, draft: DraftV2) -> DraftV2: ...


__all__ = ["DraftV2Store"]
