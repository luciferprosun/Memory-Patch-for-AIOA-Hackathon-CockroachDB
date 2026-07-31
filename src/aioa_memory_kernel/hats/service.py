"""Monotonic in-process registry policy; SQL repository mirrors these transitions."""
from __future__ import annotations
from datetime import datetime
from typing import Callable
from aioa_memory_kernel.contracts.serialization import canonical_sha256
from .errors import HatRegistryError
from .models import CompatibilityDecision, ManifestIdentity, RegistryEntry, RegistryEvent, RegistryState, ReviewReceipt, RuntimeBinding
from .runtime import HatRuntimeHandle, TrustedInstalledHatCatalog

class HatRegistryService:
    def __init__(self, *, clock: Callable[[], datetime]) -> None:
        self._clock, self._entries, self._events = clock, {}, {}
    def get(self, hat_id: str, version: str) -> RegistryEntry: return self._entries[(hat_id, version)]
    def events(self, hat_id: str, version: str) -> tuple[RegistryEvent, ...]: return tuple(self._events[(hat_id, version)])
    def _transition(self, entry: RegistryEntry | None, identity: ManifestIdentity, target: RegistryState, actor: str, reason: tuple[str, ...], *, compatibility: CompatibilityDecision, binding: RuntimeBinding | None = None, receipt: ReviewReceipt | None = None) -> RegistryEntry:
        key = (identity.manifest.hat_id, identity.manifest.hat_version); chain = self._events.setdefault(key, [])
        event = RegistryEvent("hatreg-event-" + canonical_sha256({"key":key,"sequence":len(chain)+1,"target":target.value,"reason":reason}), key[0], key[1], len(chain)+1, chain[-1].event_digest if chain else None, entry.state if entry else None, target, actor, actor, reason, self._clock())
        chain.append(event)
        updated = RegistryEntry(identity, compatibility, target, len(chain), event.event_digest, binding, receipt)
        self._entries[key] = updated; return updated
    def register(self, identity: ManifestIdentity, compatibility: CompatibilityDecision) -> RegistryEntry:
        key=(identity.manifest.hat_id,identity.manifest.hat_version); existing=self._entries.get(key)
        if existing:
            if existing.identity.typed_manifest_digest != identity.typed_manifest_digest: raise HatRegistryError("MANIFEST_DIGEST_CONFLICT", "manifest replay conflict")
            return existing
        return self._transition(None, identity, RegistryState.REGISTERED, "REGISTRY_SERVICE", ("REGISTER",), compatibility=compatibility)
    def validate(self, hat_id: str, version: str) -> RegistryEntry:
        entry=self.get(hat_id,version)
        if entry.state is not RegistryState.REGISTERED: raise HatRegistryError("REGISTRY_REPLAY_CONFLICT", "illegal validation transition")
        target=RegistryState.VALIDATED if entry.compatibility is CompatibilityDecision.COMPATIBLE else RegistryState.REJECTED
        return self._transition(entry,entry.identity,target,"REGISTRY_SERVICE",("COMPATIBILITY_CHECK",),compatibility=entry.compatibility)
    def enable(self, hat_id: str, version: str, binding: RuntimeBinding, receipt: ReviewReceipt) -> RegistryEntry:
        entry=self.get(hat_id,version)
        if entry.state not in {RegistryState.VALIDATED,RegistryState.DISABLED}: raise HatRegistryError("HAT_NOT_ENABLED", "HAT is not validated")
        if entry.compatibility is not CompatibilityDecision.COMPATIBLE or receipt.decision != "ENABLE" or receipt.canonical_manifest_digest != entry.identity.typed_manifest_digest or receipt.runtime_binding_id != binding.runtime_binding_id: raise HatRegistryError("MISSING_OPERATOR_RECEIPT", "valid trusted review receipt required")
        return self._transition(entry,entry.identity,RegistryState.ENABLED,receipt.actor_type.value,receipt.reason_codes,compatibility=entry.compatibility,binding=binding,receipt=receipt)
    def disable(self, hat_id: str, version: str, actor_reference: str) -> RegistryEntry:
        entry=self.get(hat_id,version)
        if entry.state not in {RegistryState.ENABLED,RegistryState.VALIDATED}: raise HatRegistryError("REGISTRY_REPLAY_CONFLICT", "illegal disable transition")
        return self._transition(entry,entry.identity,RegistryState.DISABLED,"TRUSTED_OPERATOR",("DISABLE",),compatibility=entry.compatibility,binding=entry.runtime_binding,receipt=entry.review_receipt)
    def resolve(self, hat_id: str, version: str, catalog: TrustedInstalledHatCatalog) -> HatRuntimeHandle:
        key=(hat_id,version); entry=self.get(*key)
        if entry.state is not RegistryState.ENABLED or entry.runtime_binding is None: raise HatRegistryError("HAT_NOT_ENABLED", "HAT is not enabled")
        return HatRuntimeHandle(lambda: self.get(*key), catalog.resolve(entry.runtime_binding.runtime_binding_id))
