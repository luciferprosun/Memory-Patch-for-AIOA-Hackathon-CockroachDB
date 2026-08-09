# ADR-034: Personal Memory HAT persistence is private data, not authority

## Status

Proposed. It becomes accepted only when the Step 27 closure commit is
reachable on `origin/main`.

## Context

Early Kernel contracts define Personal Memory HATs as private, owner-scoped
memory spaces and already reserve lifecycle, quota, model-binding, export, and
deletion concepts. Step 27 must make that foundation durable without starting
the later correction-candidate, patch, approval, activation, retrieval, or
sharing workflows. Tenant-only application filtering would be insufficient
because two users inside one tenant must remain mutually isolated.

## Decision

1. A Personal Memory HAT is an owner-private data space, not executable code
   and not canonical source evidence.
2. An empty slot is valid and contains zero memory items, patches, and bytes.
3. Slot activation enables only the namespace; it is distinct from patch
   creation or activation.
4. Existing Step 1 lifecycle and quota contracts remain authoritative.
   Durable state transitions and compare-and-set versions are also enforced by
   the database.
5. Quotas are typed, versioned, hash-bound hard policy checked in the same
   short serializable transaction as a mutation. No silent eviction occurs.
6. Model bindings are bounded provider/model-neutral configuration. They
   contain no credentials and grant no read, write, approval, commit, or
   activation authority.
7. Personal Memory has no Gemma dependency. Exact logical identities from
   different providers/models use the same binding contract.
8. Owner privacy is enforced through composite owner lineage plus RLS and
   FORCE RLS on every Step 27 Personal Memory table, not through Python
   filtering.
9. Archive retains private data. Deletion is a two-stage logical tombstone in
   Step 27; physical deletion is not claimed.
10. Export is deterministic, bounded owner configuration JSON and contains no
    secret or fabricated patch content.
11. Exact command replay is idempotent. Conflicting replay and stale
    configuration versions fail closed.
12. Step 28 and later steps exclusively own candidate, proposal, approval,
    commit, activation, active retrieval, cross-model reuse, and shared
    promotion workflows.

Step 28: NOT STARTED.

## Consequences

Users can own configured or active empty Personal Memory slots before any
correction exists. Quota and binding policy can be audited independently from
patch content. Application roles cannot observe or mutate another user's
slot, including within the same tenant, while an explicit administrative
boundary remains distinct from normal access.

The retained logical tombstone supports audit-safe idempotency but does not
deliver physical erasure. Active patch materialization, retrieval, model use,
approval, and shared promotion remain intentionally unavailable.

## Rejected alternatives

### Treat a slot as an executable HAT plugin

Rejected because private memory data must not create code or external-action
authority.

### Rely on tenant filtering in application code

Rejected because Personal Memory isolation must separate users inside the
same tenant and must fail closed at the database boundary.

### Bind Personal Memory storage to Gemma

Rejected because durable personalization must survive provider/model changes
without giving a model ownership or authority.

### Initialize a slot with a placeholder patch

Rejected because an empty namespace is valid and placeholder content would
fabricate memory and corrupt quota accounting.

### Implement patch approval or retrieval with persistence

Rejected because those are separately gated roadmap steps with different
evidence and authority requirements.
