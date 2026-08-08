# ADR-027: Hybrid retrieval, Evidence Bundle, and deterministic ranking

## Status

Proposed. It becomes accepted only when the Step 20 closure commit is
reachable on `origin/main`.

## Context

Step 18 produces route-bound exact and German lexical candidates. Step 19
produces route-, source-, and model-bound vector candidates. Both enforce hard
tenant, HAT, scope, publication, authority, access, owner, and lineage filters
before candidate admission. Step 20 must combine those incomparable result
families without weakening their authority boundary or adding model judgment.

## Decision

1. Step 20 accepts only verified Step 18 and Step 19 typed request/result
   pairs with one exact Step 17 route and policy binding.
2. Hard eligibility is revalidated before final admission. Source authority
   and publication state remain hard facts, not ranking boosts.
3. Cross-modality identity is exact tenant/HAT/source/version/chunk/content
   lineage. Security-metadata conflicts fail closed.
4. Raw lexical scores and vector distances are not compared. Fusion uses the
   immutable integer reciprocal-rank policy `hybrid-retrieval-ranking-1a`.
5. Exact statute/section and identifier matches outrank pure vector
   similarity. Vector similarity never creates source or answer authority.
6. Diversity is deterministic, exact-first, source/version bounded, and uses
   no randomness or model.
7. Context budgeting is provider-neutral, byte-based, and uses only
   deterministic UTF-8-safe excerpts.
8. Evidence items and the final Evidence Bundle are deeply immutable and
   hash-bound to ordered membership and every input/policy identity.
9. Existing Step 4 evidence persistence is not used until an exact existing
   `kernel_run_id` is available. No migration or duplicate evidence schema is
   added.
10. Step 21 exclusively owns temporal applicability, freshness, and legal
    conflict resolution.

Step 21: NOT STARTED.

## Consequences

The same semantic upstream results produce the same candidate scores,
diversity selection, excerpt bytes, item hashes, and bundle hash regardless of
input order. Replaying an upstream result does not inflate score.

A missing vector result can yield an explicitly partial lexical bundle under
the fixed policy, but an unverified semantic candidate can never substitute
for it. The bundle may remain blocked by the Step 17 knowledge policy and
never grants execution authority.

## Rejected alternatives

### Native floating-point or learned fusion

Rejected because it makes integrity material less portable and gives a model
or uncalibrated score hidden authority over final ordering.

### Authority-weighted ranking

Rejected because eligibility must be decided before ranking; a weak source
cannot become admissible through score.

### Random or model-selected diversity

Rejected because replay would not be deterministic and model output is not
authority.

### New evidence persistence tables

Rejected because Step 4 already owns the evidence schema and the missing
kernel-run binding must not be concealed by a competing data model.
