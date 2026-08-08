# ADR-028: Temporal resolution, conflict preservation, and freshness policy

## Status

Proposed. It becomes accepted only when the Step 21 closure commit is
reachable on `origin/main`.

## Context

Step 20 freezes route-bound, authority-filtered evidence candidates and their
deterministic retrieval ordering. That order is useful input, but it cannot
decide whether a version applies at the question time, whether evidence is
fresh, or whether mutually incompatible versions create an unresolved
conflict. Those decisions must happen before any model drafts an answer.

## Decision

1. Step 21 accepts only a verified Step 20 `HybridEvidenceOutcome` and its
   `FrozenEvidenceBundle`, preserving the exact Step 17 route, tenant, user,
   HAT, manifest, and scope binding.
2. Question time is explicit and hash-bound. `CURRENT` and `UNSPECIFIED` use
   an injected trusted UTC clock; `AS_OF` and `FUTURE` require an exact
   timezone-aware timestamp.
3. Legal intervals are start-inclusive and end-exclusive. Operational times
   such as retrieval or ingestion time never substitute for legal effect
   time.
4. Step 20 rank cannot override temporal invalidity. Future, expired,
   superseded, unknown, or conflicting candidates remain assessed and retain
   provenance but are not selected as applicable evidence.
5. Supersession uses explicit trusted graph facts only. Cycles, ambiguous
   branches, overlapping incompatible texts, and temporal-integrity
   contradictions fail closed and preserve deterministic conflict groups.
6. Freshness is a separate policy dimension from applicability. Thresholds
   are explicit, source-kind-specific, versioned, and digest-bound. A missing
   threshold or observation produces `UNKNOWN`, not a guessed default.
7. Completeness fallback is limited to one additional verified Step 20 bundle
   with identical security and model bindings. It cannot widen tenant, HAT,
   scope, publication, authority, access, or owner boundaries.
8. Step 21 returns immutable, hash-bound candidate assessments and a temporal
   result with canonical evidence status. Answer and execution decisions stay
   separate and unchanged.
9. Step 21 adds no persistence schema, network lookup, provider/model call,
   answer generation, approval, or execution capability.
10. Step 22 begins only after the Step 21 result has made applicability,
    conflict, freshness, and evidence-state limits explicit.

Step 22: NOT STARTED.

## Consequences

The same verified bundle, question time, clock, and policies produce the same
assessments, conflict identities, selected item hashes, status, and result
hash. Historical and future versions remain visible without being silently
replaced by the database's current version.

A source may be applicable but stale, or recently observed but legally
inapplicable. Neither state changes source authority. Conflict remains
inspectable for later correction stages and is never resolved by model score.

## Rejected alternatives

### Use Step 20 rank as the current-version selector

Rejected because retrieval relevance is not legal applicability and would
erase historical, future-effective, or conflicting evidence.

### One global freshness duration

Rejected because knowledge domains change at different rates. Missing policy
must remain unknown rather than acquire an arbitrary threshold.

### Model-assisted conflict or supersession resolution

Rejected because model output is not authority and cannot invent temporal or
legal lineage.

### New Step 21 persistence tables

Rejected because the resolver is a pure evidence-policy boundary and no
durability requirement justifies a competing schema in this step.
