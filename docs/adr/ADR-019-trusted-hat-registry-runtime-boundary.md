# ADR-019 — Trusted HAT registry and runtime boundary

## Decision

Knowledge HATs are declarative manifests paired with implementations installed
explicitly by the trusted application composition root. Manifest data never
selects a module, path, package, entry point, URL, callable, or command.

The registry validates strict local JSON, the committed schema and typed
`HatManifest`, SemVer compatibility with Kernel API `1.0.0`, the versioned
capability vocabulary, scope declarations, nested contracts, and the fixed
zero-authority policy. Enablement requires an immutable system-installation
binding and a digest-bound trusted-operator receipt.

Runtime resolution uses an injected `TrustedInstalledHatCatalog`. Each call is
mapped through a fixed capability-to-`HatSdk` method table and rechecks enabled
state and manifest identity. This is an allowlist boundary, not a sandbox for
arbitrary Python.

Personal Memory HATs remain private data spaces and are never executable
plugins.
