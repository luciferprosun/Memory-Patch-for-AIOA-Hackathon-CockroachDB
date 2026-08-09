"""Step 24 orchestration with no retrieval, model, provider, or DB capability."""

from __future__ import annotations

from aioa_memory_kernel.claims import PacketInputSnapshot

from .builder import build_correction_packet
from .integrity import CorrectionPacketAuthenticator
from .models import (
    CorrectionPacketIntegrityReceipt,
    CorrectionPacketV1A,
    verify_correction_packet_hash,
)


class CorrectionPacketService:
    """Build packets deterministically and optionally authenticate them."""

    __slots__ = ("_authenticator",)

    def __init__(
        self,
        authenticator: CorrectionPacketAuthenticator | None = None,
    ) -> None:
        if authenticator is not None and not (
            hasattr(authenticator, "authenticate")
            and callable(authenticator.authenticate)
            and hasattr(authenticator, "verify")
            and callable(authenticator.verify)
        ):
            raise TypeError("authenticator does not implement the Step 24 protocol")
        self._authenticator = authenticator

    def build(self, snapshot: PacketInputSnapshot) -> CorrectionPacketV1A:
        return build_correction_packet(snapshot)

    def authenticate(
        self,
        packet: CorrectionPacketV1A,
    ) -> CorrectionPacketIntegrityReceipt:
        verify_correction_packet_hash(packet)
        if self._authenticator is None:
            raise RuntimeError("Step 24 authenticator is not configured")
        return self._authenticator.authenticate(packet)

    def verify(
        self,
        packet: CorrectionPacketV1A,
        receipt: CorrectionPacketIntegrityReceipt,
    ) -> None:
        verify_correction_packet_hash(packet)
        if self._authenticator is None:
            raise RuntimeError("Step 24 authenticator is not configured")
        self._authenticator.verify(packet, receipt)


__all__ = ["CorrectionPacketService"]
