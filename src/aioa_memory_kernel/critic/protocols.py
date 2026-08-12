"""Narrow ports for the optional Step 39 Critic bridge."""

from __future__ import annotations

from typing import Protocol

from aioa_memory_kernel.modeling import TextGenerationProvider
from aioa_memory_kernel.personal_memory.candidates import (
    CorrectionCandidateEnvelope,
    CorrectionCandidateIntakeReceipt,
)


class CriticCandidateIntake(Protocol):
    """The only durable capability exposed to the Critic bridge."""

    def submit_critic_loop_candidate(
        self,
        envelope: CorrectionCandidateEnvelope,
    ) -> tuple[CorrectionCandidateEnvelope, CorrectionCandidateIntakeReceipt]:
        ...


__all__ = ["CriticCandidateIntake", "TextGenerationProvider"]
