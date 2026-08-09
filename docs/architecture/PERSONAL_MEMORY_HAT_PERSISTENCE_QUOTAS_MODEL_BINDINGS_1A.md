# Personal Memory HAT Persistence, Quotas and Model Bindings 1A

## Boundary

Step 27 makes the existing Personal Memory HAT contract durable. A Personal
Memory HAT is an owner-private data space, not executable plugin code and not
canonical source evidence. Creating or activating a slot grants no provider,
model, approval, commit, patch-activation, retrieval, or external-execution
authority. A Step 26 `VerifiedAnswer` never writes memory automatically.

The implementation reuses the Step 1 `PersonalHatQuotaPolicy`,
`PersonalHatQuotaUsage`, `PersonalMemorySpaceState`, and validated state
machine; the Step 4 tenant, user, Personal Memory, HAT-scope, and model-binding
tables; the Step 5 transaction-bound request context and RLS model; and the
Step 6 serializable transaction, retry, and idempotency services.

## Durable contracts

`PersonalMemoryHatSlot` binds the exact tenant, owner, space ID, derived HAT
scope ID, lifecycle state, quota policy identity, ordered provider-neutral
model bindings, state/configuration versions, operational lifecycle dates,
configuration digest, and slot hash. Empty slots are first-class records with
no display name, model binding, memory item, or patch.

The service exposes explicit typed operations only:

- create and replay an empty slot;
- read one owner slot or list owner slots;
- configure its bounded display name and quota policy;
- activate, suspend, archive, request deletion, or complete logical deletion
  through the existing state machine;
- add or remove one typed exact-model binding;
- calculate an owner/slot quota view; and
- request a canonical owner export.

There is no arbitrary field-patch API and no patch-content write API. Every
mutation carries an owner/configuration actor, expected state and
configuration versions where applicable, a deterministic command hash, and a
Step 6 idempotency key. Results are immutable `SlotMutationReceipt` values.
Domain rejections are typed persistence failures so rollback preserves their
closed reason codes.

## Lifecycle

The durable transition guard mirrors the Step 1 state machine:

```text
EMPTY -> CONFIGURED | DELETED_PENDING
CONFIGURED -> ACTIVE | ARCHIVED | DELETED_PENDING
ACTIVE -> SUSPENDED | ARCHIVED | DELETED_PENDING
SUSPENDED -> ACTIVE | ARCHIVED | DELETED_PENDING
ARCHIVED -> CONFIGURED | DELETED_PENDING
DELETED_PENDING -> DELETED
```

Configuration and state use independent compare-and-set versions. Slot
activation only enables the private namespace for later governed workflows;
it does not create or activate a patch. `ARCHIVED` retains owner-private
configuration. Deletion is a two-stage logical tombstone: bindings are
removed, `DELETED_PENDING` records the request, and `DELETED` retains the
audit-safe row. Step 27 performs no physical deletion.

## Quotas

`PersonalMemoryQuotaPolicyRecord` wraps the existing Step 1 quota dimensions
with owner, policy ID/version, and a canonical digest. The deployment policy
is stored in `config/personal-memory/personal-memory-policy-1a.json`. Slot and
binding limits are hard policy, checked within the same short serializable
transaction as the mutation. The exact limit is accepted; an over-limit
mutation rolls back with a typed reason. No eviction occurs.

`PersonalMemoryQuotaUsageView` binds lifecycle counts, byte/item/patch counts,
enabled model-binding count, owner, slot, and policy digest. An empty slot has
zero memory items, patches, and stored bytes. Accounting is tenant/owner
scoped; another user's rows do not contribute.

## Provider-neutral model bindings

`PersonalMemoryModelBinding` contains a bounded provider ID, model ID,
declared revision, `EXACT_MODEL` mode, enabled state, version, stable binding
ID, and binding hash. It stores no credential or endpoint secret. Binding a
model is configuration only: it grants neither read nor write access and does
not activate later patch retrieval. The schema and service contain no Gemma
dependency; controlled validation binds two different logical models without
performing inference.

## Database isolation and migration

Migration `0011_step27_personal_memory_persistence.sql` adds the immutable
owner-bound quota table, versioned slot configuration fields, typed binding
fields, database lifecycle/CAS guards, and least-privilege grants. It preserves
all earlier migrations unchanged and is registered in the canonical manifest.

`personal_memory_spaces`, `personal_memory_model_bindings`, and
`personal_memory_quota_policies` all have RLS and FORCE RLS. Policies call the
trusted Step 5 user-context predicate, so normal application access requires
an exact tenant and owner match. Foreign keys retain the composite tenant,
owner, and slot identity. Python post-filtering is not the security boundary.

Every service operation opens one short `SERIALIZABLE` transaction, installs
the exact `USER_PRIVATE` request context, performs quota and optimistic
concurrency checks, mutates, and completes its idempotency record before
commit. Exact replay reuses the durable result; conflicting replay fails
closed.

## Export, privacy, and later steps

`PersonalMemorySlotExport` is bounded canonical JSON containing safe slot
configuration, state, quota identity, and model-binding identities only. It is
owner-scoped, deterministic for the same export command, excludes credentials
and machine-local paths, and never fabricates patch content.

Step 28 remains responsible for a Correction Candidate bridge. Steps 29-31
own patch proposal/evidence validation, approval/commit/activation, and active
patch retrieval/cross-model reuse. Step 32 owns shared promotion and broader
supersession/revocation/export/deletion workflows. None of those capabilities
is present in Step 27.
