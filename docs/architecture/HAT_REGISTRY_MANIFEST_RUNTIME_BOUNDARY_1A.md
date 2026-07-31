# HAT Registry, Manifest Validation and Runtime Boundary 1A

Step 12 preserves the Knowledge HAT/Personal Memory distinction. It adds a
global system registry for trusted Knowledge HAT deployments; it grants no
semantic, publication, approval, commit, canonical-write, external-action, or
private-memory authority.

## Validation pipeline

Exact bounded UTF-8 JSON bytes are decoded with duplicate-member and
non-finite-number rejection. Only the committed local schema is relevant.
Unknown top-level fields fail closed. Typed construction reuses `HatManifest`,
`HatSecurityPolicy`, and `HatScopeDimensionDefinition`. Raw, canonical JSON,
typed-manifest, and schema digests remain distinct.

HAT versions follow SemVer 2.0.0 syntax. Kernel compatibility accepts only an
AND conjunction of `=`, `==`, `>`, `>=`, `<`, and `<=` comparators containing
exact versions. Kernel API version is the repository constant `1.0.0`.

The `hat-capabilities-1a` vocabulary maps fixed tokens to fixed `HatSdk`
methods. Unknown and authority-bearing tokens fail closed. Scope definitions
reuse the existing typed contract; custom rules may only be declarations tied
to trusted installed logic and are never executed during validation.

## Lifecycle and persistence

The lifecycle is `REGISTERED -> VALIDATED -> ENABLED`, with explicit
`REJECTED` and `DISABLED` branches. Re-enablement requires a new operator
receipt. Events form an append-only digest chain. Migration `0009` reuses
`hat_manifests` and adds global `hat_registry_entries`,
`hat_registry_events`, and `hat_runtime_bindings`. Ordinary runtime can read
only enabled-resolution metadata through the narrow repository boundary; it
cannot mutate registry state or delete history.

## Runtime boundary

The application injects an explicit catalog of already-installed objects.
There is no scanning, entry-point discovery, dynamic import, package install,
remote manifest, archive extraction, subprocess, shell, pickle, shared-library
loading, or user code. The catalog verifies SDK shape, identity, version,
manifest digest, implementation metadata, enabled state, and declared
capability before invocation. Outputs remain non-authoritative and typed.

Step 13 may supply a concrete German Law HAT manifest and trusted
implementation, but no German-law policy or corpus behavior exists in Step 12.
