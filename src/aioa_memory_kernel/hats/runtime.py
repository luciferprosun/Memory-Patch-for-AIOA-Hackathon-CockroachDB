"""Explicit allowlist catalog and fixed capability invocation gate."""
from __future__ import annotations
from typing import Any, Callable, Mapping
from aioa_memory_kernel.contracts import HatSdk, assert_system_installed_hat
from aioa_memory_kernel.contracts.serialization import canonical_sha256
from .errors import HatRegistryError
from .models import CAPABILITY_METHODS, InstalledHat, RegistryEntry, RegistryState, RuntimeBinding

class TrustedInstalledHatCatalog:
    def __init__(self) -> None: self._items: dict[str, InstalledHat] = {}
    def register(self, binding: RuntimeBinding, instance: HatSdk, canonical_manifest_digest: str) -> None:
        assert_system_installed_hat(instance)
        if binding.runtime_binding_id in self._items: raise HatRegistryError("UNTRUSTED_RUNTIME_BINDING", "duplicate runtime binding")
        self._items[binding.runtime_binding_id] = InstalledHat(binding, instance, canonical_manifest_digest)
    def resolve(self, binding_id: str) -> InstalledHat:
        try: return self._items[binding_id]
        except KeyError as exc: raise HatRegistryError("IMPLEMENTATION_NOT_INSTALLED", "implementation is not installed") from exc

class HatRuntimeHandle:
    def __init__(self, entry_provider: Callable[[], RegistryEntry], installed: InstalledHat) -> None:
        self._entry_provider, self._installed = entry_provider, installed
    def invoke(self, capability: str, argument: Any) -> Any:
        entry = self._entry_provider()
        if entry.state is not RegistryState.ENABLED: raise HatRegistryError("HAT_NOT_ENABLED", "HAT is not enabled")
        if capability not in entry.identity.manifest.capabilities: raise HatRegistryError("CAPABILITY_NOT_DECLARED", "capability is not declared")
        method = CAPABILITY_METHODS.get(capability)
        if method is None: raise HatRegistryError("UNKNOWN_CAPABILITY", "unknown capability")
        installed = self._installed
        if installed.canonical_manifest_digest != entry.identity.typed_manifest_digest or installed.instance.manifest.hat_id != entry.identity.manifest.hat_id or installed.instance.manifest.hat_version != entry.identity.manifest.hat_version:
            raise HatRegistryError("IMPLEMENTATION_MANIFEST_MISMATCH", "installed implementation does not match registry")
        try:
            function = {name: getattr(installed.instance, name) for name in CAPABILITY_METHODS.values()}[method]
            result = function(argument)
        except HatRegistryError: raise
        except Exception as exc: raise HatRegistryError("RUNTIME_INVOCATION_FAILED", "trusted HAT invocation failed") from exc
        if capability in {"REQUEST_NORMALIZATION", "EVIDENCE_CONSTRAINTS"} and not isinstance(result, Mapping):
            raise HatRegistryError("CAPABILITY_OUTPUT_INVALID", "capability returned invalid output")
        return result
