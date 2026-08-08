# Step 21 - Temporal Resolver, Conflict Detection and Freshness Policy 1A

## Verdict

`COMPLETE AND PUSHED` once this closure record is reachable on `origin/main`.
Step 22: NOT STARTED.

## Starting identity

- Exact Step 20 baseline: `785a349b3124e2251624a6502fb477cf421e02f4`
- Baseline subject: `feat(retrieval): add hybrid evidence bundle ranking 1a`
- Branch: `main`
- Baseline tests: 1,415 passed
- Baseline contract validation and compileall: PASS

The final closure identity is the commit containing this record. No unverified
future commit SHA is embedded here.

## Implemented contracts and boundary

Step 21 adds the domain-neutral `aioa_memory_kernel.temporal` package. Its
typed request accepts the exact Step 17 route and canonical Step 20 outcome,
verifies all route/outcome/bundle/item hashes, and preserves tenant, user,
HAT, manifest, scope, authority, access, owner, and lineage identity.

The new immutable/hash-bound contracts cover question mode, explicit as-of,
injected trusted time, temporal facts, source-kind freshness policy, bounded
completeness policy, candidate assessments, stable conflict groups, fallback
summary, final evidence status, limitations, and the temporal result hash.
Answer and execution decisions remain independent and unchanged.

## Temporal and conflict semantics

`CURRENT`, `AS_OF`, `FUTURE`, and `UNSPECIFIED` modes are explicit. All times
are aware UTC values. Legal intervals are start-inclusive/end-exclusive;
operational timestamps do not replace effect/application time.

Candidates are classified as applicable, not yet applicable, expired,
superseded, unknown, or conflicting. Explicit supersession edges govern the
version graph. A current successor supersedes its predecessor at the queried
time while the predecessor can remain historically applicable. Branches,
cycles, temporal-digest contradictions, and overlapping incompatible content
fail closed into deterministic conflict groups. Step 20 rank never selects a
temporally invalid or conflicting winner.

## Freshness and evidence status

Freshness remains separate from temporal applicability. Versioned,
digest-bound policy supplies explicit thresholds by trusted source kind and a
fixed observation precedence. No universal fallback threshold exists. Missing
policy/facts remain unknown.

Final status is explicit: `SUFFICIENT`, `INSUFFICIENT`, `CONFLICTING`,
`UNAVAILABLE`, `STALE`, `INVALID`, or `NOT_REQUIRED` for a valid no-HAT path.
The status describes evidence only and does not authorize an answer,
approval, or execution.

## Completeness fallback

At most one fallback attempt may consume one additional already-verified Step
20 outcome. Exact Step 17 policy, route, tenant/user, HAT, manifest, scope,
retrieval, and embedding-model bindings must equal the primary bundle.
Duplicates are not reconsidered. Fallback cannot retrieve through a new
provider or widen publication, authority, access, or owner scope.

## Real and synthetic validation split

Controlled validation verifies the committed Step 16 evidence digest, the
committed Step 20 evidence digest and bundle identity, and the actual typed
Step 20-to-Step 21 boundary for one real published German-law identity.

The verified local fixture does not provide a proven multi-version temporal
family. Historical, future, supersession, conflict, stale, unavailable, and
fallback edge semantics therefore use a deterministic fixture explicitly
marked synthetic. No real historical conflict or corpus-wide temporal
coverage is claimed.

## Validation

- Step 21 focused suite: 47/47 PASS.
- Full repository suite: 1,462/1,462 PASS.
- Required Step 10/15/16/17/18/19/20, HAT, German Law temporal, authority,
  tenant, and serialization regressions: 519/519 PASS.
- Contract validator: PASS.
- Compileall: PASS.
- Controlled Step 21 validation: PASS.

The controlled matrix covers current, historical, future-not-yet-effective,
superseded, conflicting, stale, insufficient, unavailable, and bounded
fallback outcomes. Assessment/result hashes replay deterministically and a
tampered Step 20 or conflict-group hash fails closed.

Sanitized evidence is committed at
`docs/evidence/retrieval/step21-temporal-conflict-freshness-validation.json`.

## Persistence and effects

No migration is added. Persistence is not required for this pure Step 21
evidence-policy boundary. It performs no database query or mutation, model or
provider call, network acquisition, AWS/S3 mutation, source modification,
approval, commit, or execution.

## Known limitations and Step 22 handoff

Completeness is bounded to the verified Step 20 inputs and one exact-scope
fallback. An older Step 15 aggregate temporal digest is preserved when its
normalization-record preimage is absent from the Step 20 projection; Step 21
does not falsely claim recomputation.

Step 22 may consume `TemporalResolutionResult`, its evidence status, selected
Step 20 item hashes, assessments, conflict groups, freshness summary, and
limitations. It must keep the original query and uncorrected Draft V1 separate
from later correction evidence and cannot let a provider/model alter any
route, authority, temporal, conflict, or freshness decision.

Step 22 remains NOT STARTED in this closure.
