# Personal Memory HATs 1A

## Two different security objects

A Knowledge HAT is a system-installed domain module with a versioned manifest
and zero consequential authority. A Personal Memory HAT is the public name for
a `PersonalMemorySpace`: a private, user-owned, non-executable data namespace.
They share neither authority nor visibility.

A Personal Memory HAT can later hold governed notes, user-provided source
references, verified personal patches, preferences, workflows, correction
history, and bounded model-experience hints. It is not plugin code, a shell
environment, a separate model, fine-tuning, an unlimited transcript, canonical
evidence, or an action authorizer.

## Model independence

Ownership attaches to `tenant_id`, `user_id`, and
`personal_memory_space_id`, never to a model. A space may list multiple model
binding IDs. Adding or changing a binding cannot transfer ownership, alter the
memory record, or promote its trust. The same governed memory therefore remains
usable when the base model changes.

This is external governed memory. It does not modify model weights.

## Configurable pool and quota

`PersonalMemoryPool` contains a deployment-configured number of spaces. There
is no compiled slot count. `PersonalHatQuotaPolicy` may independently cap total,
active, and archived spaces; bytes; personal sources; active patches;
session-memory bytes; ingestion jobs; and embedding/index footprint. `None`
means a deployment has not set that cap, not that the kernel grants unlimited
resources.

Allocation checks ownership and quota, then creates an empty, unnamed,
unbound, inert space in `EMPTY`. Quota rejection occurs before the pool is
changed.

## Lifecycle

The explicit states are:

```text
EMPTY
  -> CONFIGURED
  -> ACTIVE
  -> SUSPENDED
  -> ARCHIVED
  -> DELETED_PENDING
  -> DELETED
```

The graph also permits bounded side transitions documented in code:
configured or active spaces may archive or request deletion; suspended spaces
may restore to active; archived spaces may restore to configured; and deletion
completion occurs only from `DELETED_PENDING`. `DELETED` is terminal.

Naming/configuration, activation, suspension, restoration, archival, export
request, deletion request, deletion completion, and model binding all require
the exact tenant and user. Archived, suspended, deletion-pending, and deleted
spaces are not retrieval-eligible.

## Retrieval and deletion

Private memory retrieval requires exact owner and space identity. Missing
tenant context, cross-user access, cross-tenant access, or inclusion in shared
HAT retrieval is rejected. Revoked, inactive, not-yet-valid, expired, or stale
items are excluded.

An export request is metadata only in this step. Deletion is a two-stage
contract so a future persistence layer can perform bounded deletion and record
completion. No database or object store is touched here.
