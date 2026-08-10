# ADR-037: Separate owner approval, technical commit and activation

## Status

Proposed. It becomes accepted only when the Step 30 closure commit is
reachable on `origin/main`.

## Context

Step 29 produces a validated, evidence-bound proposal in
`AWAITING_APPROVAL`. Validation proves eligibility but does not establish the
owner's consent, technical persistence authority, or activation eligibility.
Collapsing those decisions would let a validator or technical service
manufacture approval and would leave quota, slot and evidence TOCTOU gaps.

## Decision

1. Only an authenticated exact owner acting as `HUMAN_USER` may approve the
   exact hash-bound Step 29 proposal presentation.
2. Validation is not approval. Approval is proposal-specific,
   receipt-bound, and protected by a single-use replay identity.
3. `AWAITING_APPROVAL -> APPROVED`, `APPROVED -> COMMITTED`, and
   `COMMITTED -> ACTIVE` are separate, no-skip, compare-and-set transitions.
4. A distinct Personal Memory Commit Helper performs technical commit with a
   dedicated non-login, non-BYPASSRLS role. It cannot approve or broaden the
   approved payload.
5. Proposal, evidence, validation, slot, quota, model binding, owner and
   policy state are revalidated with trusted application time immediately
   before commit. Activation repeats the current-state checks.
6. Committed and active patch text is byte-for-byte identical to the approved
   proposal; no model rewrite or post-approval normalization is allowed.
7. Approval, commit and activation use independent idempotency identities and
   immutable receipts. Exact replay is idempotent; changed replay fails.
8. Migration 0014 enforces the three edges, exact payload lineage, RLS/FORCE
   RLS and least-privileged grants at the database boundary. Because
   CockroachDB v26.2 has no column-level UPDATE grants, a dedicated trigger
   narrows the Commit Helper's slot-table UPDATE to one exact increment of the
   non-semantic quota epoch and rejects every other slot change.
9. `COMMITTED` is durable but inactive. `ACTIVE` remains owner-private,
   non-canonical Personal Memory and grants no execution authority.
10. Step 31 exclusively owns retrieval and cross-model reuse.

Step 31: NOT STARTED.

## Consequences

Human intent, technical persistence, and activation are independently
auditable. Hash or state drift between phases fails closed, concurrent replay
cannot duplicate a patch, and the technical role cannot use its credential to
approve or mutate slot configuration.

The additional receipts and revalidation add state and database checks, but
they keep irreversible semantics explicit and allow later retrieval to rely
on one exact activation lineage without treating it as canonical evidence.

## Rejected alternatives

### Treat Step 29 validation as approval

Rejected because evidence eligibility does not prove owner consent.

### Let the normal runtime perform technical commit

Rejected because it would collapse approval presentation and privileged
persistence into one credential boundary.

### Commit and activate in one edge

Rejected because activation needs an independent receipt, replay identity,
quota check and current-slot revalidation.

### Rewrite the approved text before persistence

Rejected because existing validation and approval hashes would no longer
describe the committed content.
