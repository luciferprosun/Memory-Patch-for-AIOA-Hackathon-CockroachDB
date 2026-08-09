# Step 27 - Personal Memory HAT Persistence, Quotas and Model Bindings 1A

## Verdict

`COMPLETE AND PUSHED` once this closure record is reachable on `origin/main`.
Step 28: NOT STARTED.

## Starting identity

- Exact Step 26 baseline: `31b23f662be329a1e70440e50a50f41d2550b89c`
- Baseline subject: `feat(answers): add verified fail-closed output 1a`
- Branch: `main`
- Baseline tests: 1,622 passed
- Baseline focused Step 1/5/6/17-26 authority, tenant, persistence, and
  serialization regressions: 527 passed
- Baseline contract validator and compileall: PASS

The final closure identity is the commit containing this record. No future
commit SHA is embedded before that commit exists.

## Persistence and contract decision

Step 27 reuses the Step 1 Personal Memory lifecycle/quota contracts and the
Step 4/5/6 database, RLS, transaction, retry, and idempotency foundation. A
new migration is required because the foundational tables lacked durable
quota-policy identity, typed provider-neutral binding facts, independent
configuration/state versions, and database lifecycle/CAS enforcement.

Migration `0011_step27_personal_memory_persistence.sql` adds those exact
structures, preserves every prior migration byte, updates the canonical
manifest, and retains RLS/FORCE RLS and least privilege. The resulting schema
has 11 migrations, 43 tables, and 40 protected tables.

Immutable, hash-bound contracts cover quota policy, empty/configured slot,
model binding, quota usage, explicit commands, mutation receipts, and owner
export. The service exposes no generic field patch or patch-content write API.
Domain quota/state denials remain typed across transaction rollback.

## Empty slots, lifecycle, quotas, and bindings

An owner may create and idempotently replay a slot with zero memory items,
zero patches, zero bytes, and zero bindings. Configuration is versioned and
optimistically locked. The reused state machine and database trigger permit
the bounded configured/active/suspended/archived/delete lifecycle and reject
invalid or stale transitions. Slot activation never activates a patch.

Quotas are versioned, digest-bound hard policy evaluated transactionally.
Exact-limit use is accepted and over-limit writes roll back without eviction.
Usage is exact tenant/owner/slot accounting. Model bindings use bounded
provider/model/revision identities, contain no credentials, grant no access
or mutation authority, and have no Gemma dependency.

## Owner isolation, archive, export, and delete

Composite owner keys plus Step 5 `USER_PRIVATE` request context, RLS, and FORCE
RLS enforce private-by-default access. Controlled validation uses Tenant A /
User A, Tenant A / User B, and Tenant B / User C: only User A observes slot A;
both negative contexts observe zero rows.

Archive retains owner-private configuration and remains distinct from delete.
Export is bounded deterministic owner-only canonical JSON with configuration,
quota, and binding identities and no secret, path, or fake patch content.
Delete is implemented as `DELETED_PENDING -> DELETED`, removes bindings, and
retains a logical tombstone. Physical deletion is not claimed.

## Validation

- Step 27 focused suite: 30/30 PASS.
- Full repository suite after implementation: 1,652/1,652 PASS.
- Focused Step 17-26, RLS, persistence, Personal Memory authority, tenant,
  and serialization regressions: 681/681 PASS.
- CockroachDB schema, migration replay, and RLS focused regressions:
  115/115 PASS.
- Contract validator: PASS.
- Compileall: PASS.
- Controlled disposable CockroachDB Step 27 validation: PASS.

Sanitized evidence is committed at
`docs/evidence/personal-memory/step27-personal-memory-persistence-validation.json`.
It records 11 applied/11 replay-skipped migrations, CockroachDB v26.2.4 and
binary digest, three RLS/FORCE RLS tables, exact quota/binding/lifecycle
matrices, owner isolation, export/delete semantics, complete cleanup, and zero
provider/model/web/AWS/S3/external effects.

## Authority and known limitations

Personal Memory remains private data and never becomes canonical evidence or
executable HAT authority. Slot and model binding state cannot approve, commit,
activate patches, execute actions, or grant model read/write. A Verified
Answer does not auto-write memory.

Known limitations are explicit: Step 27 stores configuration and lifecycle
only; delete is a logical tombstone rather than physical erasure; binding
mode is the bounded `EXACT_MODEL` V1 mode; export contains configuration only;
and no patch content, active retrieval, cross-model reuse, or shared promotion
exists.

## Step 28 handoff

Step 28 may target one exact active/configured `PersonalMemoryHatSlot` using
its tenant, owner, HAT-scope, slot/configuration hash, quota-policy digest, and
provider-neutral model-binding configuration. It may construct a bounded
Correction Candidate Envelope from the Knowledge Hub/Critic Prompt Loop.

It must not approve, commit, activate, persist an active patch, retrieve a
patch, widen the owner scope, or turn Personal Memory into canonical evidence.
Step 28 remains NOT STARTED.
