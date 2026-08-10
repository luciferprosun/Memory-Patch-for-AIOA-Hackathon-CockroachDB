# ADR-038: Owner-private ACTIVE patch retrieval and cross-model reuse

## Status

Proposed. It becomes accepted only when the Step 31 closure commit is
reachable on `origin/main`.

## Context

Step 30 creates an exact owner-approved and activated private patch. Query-time
use must preserve that lifecycle, tenant/user isolation, current route and
temporal policy, while allowing model-independent reuse. Treating this memory
as evidence, using non-active states, or copying it into per-model records
would break the established authority and identity boundaries.

## Decision

1. Only exact Step 30 `ACTIVE` patches with fully verified activation, commit,
   approval, validation, proposal and candidate lineage are query-time
   eligible.
2. Retrieval is read-only, owner-private, tenant/slot scoped, parameterized,
   bounded and protected by existing RLS/FORCE RLS.
3. Exact route scope, trusted temporal applicability and current Step 27 model
   binding checks are mandatory. Any failed gate suppresses the patch.
4. A patch is model-independent data. The same patch identity/content may be
   reused by multiple provider-neutral model identities when each has a
   current allowed binding; no Gemma dependency is introduced.
5. Personal Memory context is explicitly non-canonical. It cannot become an
   Evidence Bundle item, upgrade source authority, create citation authority,
   approve an answer or grant execution authority.
6. Current canonical evidence wins. A conflict or detached current evidence
   identity suppresses the patch for the query without mutating lifecycle
   state.
7. Results default to 8, permit at most 32 returned patches and inspect at
   most 128 candidates with deterministic ordering/truncation hashes.
8. Existing storage and `memory_items_scope_retrieval_idx` are sufficient;
   Step 31 adds no migration or vector subsystem.
9. Step 32 exclusively owns persisted supersession, revocation, export/delete
   lifecycle changes and shared promotion.

Step 32: NOT STARTED.

## Consequences

Later model calls can receive the same exact private memory without embedding
the memory in any provider. Owner isolation and full activation lineage are
checked before context assembly, while canonical knowledge remains the higher
authority. Suppressed patches remain unchanged for later Step 32 policy.

The design performs deterministic reconstruction and current-policy checks on
each bounded candidate. This costs more than trusting an `active` boolean but
closes hash, receipt and TOCTOU gaps without a new database schema.

## Rejected alternatives

### Retrieve every non-terminal proposal

Rejected because approval readiness, approval and commit do not make content
active.

### Store one patch copy per model

Rejected because it fragments semantic identity and creates model ownership
of Personal Memory.

### Treat approved memory as canonical evidence

Rejected because owner-private personalization cannot outrank canonical
sources or independently authorize an answer.

### Persist revocation on query-time conflict

Rejected because Step 32 owns durable supersession and revocation.
