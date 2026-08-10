# ADR-039: Personal Memory terminal lifecycle and shared-promotion review boundary

## Status

Proposed. It becomes accepted only when the Step 32 closure commit is
reachable on `origin/main`.

## Context

Step 31 can retrieve an exact active, owner-private patch. Production
lifecycle now needs immutable supersession and revocation, deterministic
owner export, owner deletion, and a way to propose carefully bounded shared
material. These operations must not rewrite history, weaken owner isolation,
or turn private memory into canonical or shared authority.

## Decision

1. Supersession is an append-only relation between two exact, same-owner,
   same-slot, scope-compatible ACTIVE patches. It preserves old content and
   permits historical applicability before the effective time.
2. Revocation is an immutable terminal receipt that suppresses future use
   without rewriting or deleting patch content. Only the human owner or the
   exact deterministic lifecycle-policy actor may initiate it.
3. Deletion is separate from revocation. Step 32 completes the existing slot
   lifecycle as logical deletion, retains a tombstone and reports
   `physical_delete=false`.
4. Owner export is canonical, deterministic, bounded, private and
   replay-protected. Secret-like values, machine paths and foreign-owner data
   are rejected.
5. Private Step 30 approval is not consent to share. Shared promotion needs a
   separate exact owner consent, deterministic de-identification assessment
   and human review.
6. A shared-promotion proposal is never activated, published, or treated as
   canonical evidence in Step 32. Canonical conflict cannot be overridden.
7. Five owner-private append-only tables and guarded terminal metadata on the
   existing patch carrier provide durable persistence. RLS, FORCE RLS,
   security-invoker helpers and composite owner lineage are mandatory.
   CockroachDB's inbound-FK check receives only a relation-level SELECT grant
   for the Step 30 Commit Helper; the helper has no Step 32 row policy and
   therefore no lifecycle-record visibility or mutation authority.
8. Current retrieval suppresses terminal patches. Historical retrieval may
   use only the old superseded patch before `effective_at`; revocation and
   deletion are always suppressed.
9. Private deletion never silently mutates an independently reviewed or
   shared artifact.
10. Step 33 owns the global audit ledger/hash chain, Step 34 owns the human
    review workspace, and Step 35 owns the Personal Memory UI.

Step 33: NOT STARTED.

## Consequences

Personal Memory gains a durable lifecycle without changing patch content or
source authority. The owner can retrieve a bounded export and can complete a
logical deletion, while shared use remains behind an explicit privacy and
review boundary.

The terminal overlay makes the existing Step 31 query path efficient and
keeps historical behavior explicit. It also requires strict receipt-before-
terminal ordering inside one serializable transaction and exact runtime-role
grants.

## Rejected alternatives

### Rewrite or physically remove the old patch on supersession

Rejected because it destroys immutable history and makes historical queries
unverifiable.

### Treat revocation and deletion as one state

Rejected because future-use suppression and owner data deletion have
different semantics, retention, and replay requirements.

### Reuse private approval as sharing consent

Rejected because the Step 30 owner action authorized only private memory.

### Automatically publish a de-identified candidate

Rejected because deterministic redaction cannot establish complete privacy
or canonical source authority.
