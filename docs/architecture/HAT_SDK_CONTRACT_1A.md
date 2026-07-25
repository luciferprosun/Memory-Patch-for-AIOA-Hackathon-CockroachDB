# Knowledge HAT SDK Contract 1A

## Manifest

Every system-installed Knowledge HAT declares a manifest with:

- schema, HAT, and compatibility versions;
- display name, domain IDs, and supported languages;
- generic scope-dimension definitions;
- capabilities;
- source-authority and retrieval contracts;
- claim, conflict, and memory contracts;
- a fixed security policy;
- named extension points.

The JSON Schema and `HatManifest` dataclass validate the same structural
boundary. A manifest is declarative configuration, not executable user input.

Every security policy must state:

```text
external_action_authority = NONE
canonical_write_authority = NONE
patch_approval_authority = NONE
patch_commit_authority = NONE
```

It must also reject executable user code and private-memory access from shared
retrieval.

## Protocol

`HatSdk` is a Python protocol for future trusted, system-installed
implementations. It defines operations to validate a manifest, normalize a
request, derive generic scope requirements, build retrieval constraints, rank
source authority, extract candidate claims, detect conflicts, create correction
requirements, and create a Memory Patch proposal.

These operations return declarations and proposals. They do not execute shell
commands, modify files, send messages, approve payments, authorize an action,
write canonical evidence, approve a patch, commit a patch, activate memory, or
grant capabilities.

This baseline has no arbitrary-code loader and no user-provided plugin
execution. `assert_system_installed_hat()` checks an already installed object;
it does not import one from a path or manifest.

## Domain neutrality

The two synthetic fixture manifests use unrelated dimensions: a semantic
software runtime version and a fictional equipment family/manual revision.
Kernel routing enums and patch states are identical for both.

German law can later supply a HAT manifest with its own scope and temporal
rules. It remains a client of these generic contracts and does not add legal
requirements to Kernel Core.
