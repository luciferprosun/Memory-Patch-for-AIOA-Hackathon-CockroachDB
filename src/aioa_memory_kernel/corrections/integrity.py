"""Kernel-side HMAC-SHA-256 boundary for Step 24 packets.

Key material is supplied at runtime, retained only inside the signer instance,
and never serialized into a packet, receipt, model input, log payload, or
validation artifact.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Protocol

from aioa_memory_kernel.contracts.exceptions import ContractValidationError, IntegrityError

from .models import (
    PACKET_AUTHENTICITY_ALGORITHM,
    PACKET_HMAC_DOMAIN_ID,
    STEP24_SCHEMA_VERSION,
    CorrectionPacketBoundaryError,
    CorrectionPacketIntegrityReceipt,
    CorrectionPacketV1A,
    Step24ReasonCode,
    verify_correction_packet_hash,
    verify_integrity_receipt_hash,
)


MINIMUM_HMAC_KEY_BYTES = 32
_DOMAIN_MESSAGE = PACKET_HMAC_DOMAIN_ID.encode("ascii") + b"\x00"


class CorrectionPacketAuthenticator(Protocol):
    """Narrow signer/verifier capability; it exposes no key bytes."""

    @property
    def key_id(self) -> str: ...

    def authenticate(self, packet: CorrectionPacketV1A) -> CorrectionPacketIntegrityReceipt: ...

    def verify(
        self,
        packet: CorrectionPacketV1A,
        receipt: CorrectionPacketIntegrityReceipt,
    ) -> None: ...


class HmacSha256PacketAuthenticator:
    """Runtime-configured HMAC provider with a redacted representation."""

    __slots__ = ("_key_id", "_key_material")

    def __init__(self, *, key_id: str, key_material: bytes) -> None:
        if (
            not isinstance(key_id, str)
            or not key_id
            or key_id != key_id.strip()
            or len(key_id.encode("utf-8")) > 255
        ):
            raise ContractValidationError("key_id must be bounded canonical text")
        if not isinstance(key_material, bytes) or len(key_material) < MINIMUM_HMAC_KEY_BYTES:
            raise ContractValidationError("HMAC key material is below the minimum bound")
        self._key_id = key_id
        self._key_material = bytes(key_material)

    @property
    def key_id(self) -> str:
        return self._key_id

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(key_id={self._key_id!r}, "
            "key_material=<redacted>)"
        )

    def _authenticator(self, packet_hash: str) -> str:
        message = _DOMAIN_MESSAGE + packet_hash.encode("ascii")
        return hmac.new(self._key_material, message, hashlib.sha256).hexdigest()

    def authenticate(
        self,
        packet: CorrectionPacketV1A,
    ) -> CorrectionPacketIntegrityReceipt:
        verify_correction_packet_hash(packet)
        return CorrectionPacketIntegrityReceipt(
            schema_version=STEP24_SCHEMA_VERSION,
            packet_hash=packet.packet_hash,
            integrity_algorithm=PACKET_AUTHENTICITY_ALGORITHM,
            key_id=self._key_id,
            authenticator=self._authenticator(packet.packet_hash),
            domain_id=PACKET_HMAC_DOMAIN_ID,
        )

    def verify(
        self,
        packet: CorrectionPacketV1A,
        receipt: CorrectionPacketIntegrityReceipt,
    ) -> None:
        try:
            verify_correction_packet_hash(packet)
            verify_integrity_receipt_hash(receipt)
        except (ContractValidationError, IntegrityError) as exc:
            raise CorrectionPacketBoundaryError(Step24ReasonCode.PACKET_HMAC_INVALID) from exc
        if (
            receipt.packet_hash != packet.packet_hash
            or receipt.integrity_algorithm != PACKET_AUTHENTICITY_ALGORITHM
            or receipt.domain_id != PACKET_HMAC_DOMAIN_ID
            or receipt.key_id != self._key_id
            or not hmac.compare_digest(
                receipt.authenticator,
                self._authenticator(packet.packet_hash),
            )
        ):
            raise CorrectionPacketBoundaryError(Step24ReasonCode.PACKET_HMAC_INVALID)


def verify_packet_authenticity(
    packet: CorrectionPacketV1A,
    receipt: CorrectionPacketIntegrityReceipt,
    authenticator: CorrectionPacketAuthenticator,
) -> None:
    # Validate the exact narrow callable surface without runtime-checking a
    # typing Protocol.
    if not (
        hasattr(authenticator, "authenticate")
        and callable(authenticator.authenticate)
        and hasattr(authenticator, "verify")
        and callable(authenticator.verify)
    ):
        raise TypeError("authenticator does not implement the Step 24 protocol")
    authenticator.verify(packet, receipt)


__all__ = [
    "CorrectionPacketAuthenticator",
    "HmacSha256PacketAuthenticator",
    "MINIMUM_HMAC_KEY_BYTES",
    "verify_packet_authenticity",
]
