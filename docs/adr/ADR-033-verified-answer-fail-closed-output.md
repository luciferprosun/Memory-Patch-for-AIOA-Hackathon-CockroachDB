# ADR-033: Verified answer assembly and fail-closed output

## Status

Proposed. It becomes accepted only when the Step 26 closure commit is
reachable on `origin/main`.

## Context

Step 25 produces a Draft V2, claim-by-claim layered verifications, and a
hash-bound summary. Draft V2 generation alone is not verification, and model
text must not bypass route, policy, evidence, temporal, citation, or authority
boundaries. A final output boundary must decide whether exact verified text
can be returned and must handle correctable generation failures without
creating an unbounded loop or a fallback to a known-bad Draft V1.

## Decision

1. A normal answer is assembled only from the exact Draft V2 whose complete
   Step 25 result and `VERIFIED` summary pass every upstream integrity and
   policy gate.
2. Every returned claim must be `VERIFIED_SUPPORTED`; citations must come
   only from the verified Correction Packet universe.
3. The returned text is byte-for-byte the verified Draft V2. No model rewrite
   occurs after verification.
4. Step 17 knowledge policy and Step 21 evidence status are hard ceilings.
   A verified-looking Draft V2 cannot override `BLOCK_ANSWER`, confirmation,
   insufficient, conflicting, stale, unavailable, or invalid evidence.
5. `HAT_ENFORCE` never falls back to Draft V1 or an unverified Draft V2.
6. At most one final correction retry is permitted, and only for correctable
   generation/compliance failures with sufficient evidence.
7. Retry reuses the same packet, receipt, route, scope, and evidence universe,
   adds no retrieval, and receives only a bounded deterministic failure
   summary.
8. Retry output has a new immutable Draft V2 identity and undergoes the full
   Step 25 verifier again. A retry failure cannot trigger a second retry.
9. Exhausted or non-retryable verification yields an immutable human-review
   handoff or bounded sanitized failure, never an answer body.
10. Verified answer, review, and failure records grant no approval or
    execution authority. The copied execution decision is inert metadata.
11. No migration is added because the existing Step 4 schema cannot safely
    represent final outputs or multiple stage-2 revisions. Existing tables
    are not overloaded.
12. Step 27 alone owns Personal Memory persistence, quota, and model-binding
    work.

Step 27: NOT STARTED.

## Consequences

Successful output is directly traceable to one exact evidence, temporal,
packet, Draft V2, and verification lineage. Correctable provider output gets
one bounded opportunity to repair itself, but the model never becomes the
judge of success. Integrity and evidence failures avoid network calls and
remain deterministic.

Retry Draft V2 and final output durability remain an explicit schema
limitation rather than being hidden in ambiguous metadata. A later reviewed
migration can add storage without changing Step 26's semantic hashes.

## Rejected alternatives

### Fall back to Draft V1 with a warning

Rejected because `HAT_ENFORCE` explicitly forbids returning a known-bad,
uncorrected response.

### Trust the model's self-certification

Rejected because generation and verifier output are candidate data, not
authority.

### Retry until the model succeeds

Rejected because it is unbounded, can multiply provider effects, and still
does not repair evidence or policy failures.

### Retrieve more evidence during final output

Rejected because Step 26 must remain bound to the frozen packet universe.

### Add final-answer tables opportunistically

Rejected because persistence identity, retry revisioning, and upstream foreign
keys require a coordinated schema decision.
