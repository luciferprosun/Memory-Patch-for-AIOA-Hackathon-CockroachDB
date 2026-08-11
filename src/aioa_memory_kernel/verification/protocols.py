"""Narrow Step 25 persistence port."""

from __future__ import annotations

from typing import Protocol

from .models import (
    CorrectedEvidenceVerifierRequest,
    CorrectedEvidenceVerifierSignal,
    DraftV2,
)


class CorrectedEvidenceVerifier(Protocol):
    """Deterministic proof port; never backed by a model candidate signal."""

    def verify(
        self,
        request: CorrectedEvidenceVerifierRequest,
    ) -> CorrectedEvidenceVerifierSignal: ...


class DraftV2Store(Protocol):
    def load(
        self,
        *,
        tenant_id: str,
        user_id: str,
        draft_id: str,
    ) -> DraftV2 | None: ...

    def put(self, draft: DraftV2) -> DraftV2: ...


__all__ = ["CorrectedEvidenceVerifier", "DraftV2Store"]
