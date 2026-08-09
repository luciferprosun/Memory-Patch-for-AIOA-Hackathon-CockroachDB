# ADR-036: Personal Memory proposals require canonical evidence validation

## Status

Proposed. It becomes accepted only when the Step 29 closure commit is
reachable on `origin/main`.

## Context

Step 28 persists owner-private correction candidates in `DETECTED` state.
Those candidates are intentionally non-authoritative. A bounded workflow is
needed to turn one candidate into a reviewable proposal without treating
candidate, Critic, or model text as evidence and without implementing Step 30
approval, commit, or activation.

## Decision

1. Step 29 exclusively owns `DETECTED -> PROPOSED -> EVIDENCE_BOUND ->
   VALIDATED -> AWAITING_APPROVAL`; no edge may be skipped.
2. A proposal preserves the exact candidate text and binds candidate,
   owner/tenant/slot, route/HAT/scope, model-binding, and upstream-result
   identities through deterministic hashes.
3. Evidence may be bound only from the verified Step 20, 21, 23, 24, and 26
   artifact universe. Candidate, proposal, Critic, and model output are not
   canonical evidence.
4. Exact deduplication, deterministic conflict checks, freshness/temporal
   checks, owner and current-slot validation, quota, and model-binding checks
   are fail-closed gates. A model is not their authority.
5. Canonical evidence constrains Personal Memory. A proposal that conflicts
   with stronger canonical evidence cannot validate.
6. Every edge revalidates current owner-slot lineage in a short serializable
   transaction and uses Step 6 idempotency plus optimistic state hashes.
7. Migration 0013 reuses the existing proposal and transition carriers,
   enforces RLS/FORCE RLS, exact state edges, deterministic dedup uniqueness,
   and a hard owner-slot proposal quota.
8. `VALIDATED` is not approval. `AWAITING_APPROVAL` contains no approval
   actor/token or commit/activation authority.
9. Step 30 exclusively owns explicit user approval and the
   `AWAITING_APPROVAL -> APPROVED -> COMMITTED -> ACTIVE` lifecycle.

Step 30: NOT STARTED.

## Consequences

Personal Memory correction opportunities can be preserved and checked
against exact canonical evidence before a user is asked to review them.
Concurrent duplicates and stale state fail closed, and private owner scope is
enforced at the database boundary rather than by Python filtering alone.

Step 29 deliberately cannot decide consent, commit data with dedicated
credentials, activate a patch, retrieve active memory, or promote Personal
Memory to a shared source.

## Rejected alternatives

### Treat a verified answer or candidate as an automatic patch

Rejected because answer verification and candidate detection do not establish
the user's intent to persist Personal Memory.

### Use a model to decide deduplication or conflicts

Rejected because model output is a candidate signal, not deterministic truth
or authority.

### Combine validation with approval and activation

Rejected because it would collapse evidence integrity, user consent, and
technical commit authority into one unauditable transition.
